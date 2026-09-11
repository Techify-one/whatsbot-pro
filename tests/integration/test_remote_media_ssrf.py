"""Os seis guards de :mod:`app.services.remote_media` (plano 151 · F3).

Um teste por guard, com o motivo no nome. O que está sendo travado aqui não é
"o download funciona" — é que **o download NÃO funciona** exatamente nos casos em
que funcionar seria uma escalada de privilégio: quem tem só ``conversation.reply``
não pode transformar o servidor em scanner da rede interna nem em leitor do
endpoint de metadados da nuvem.

    venv/bin/python -m pytest tests/integration/test_remote_media_ssrf.py -q
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import patch

import pytest

from app.services import remote_media
from app.services.remote_media import RemoteMediaError, fetch_remote_media


MB = 1024 * 1024


def _fetch(url: str, *, max_bytes: int = 10 * MB):
    return asyncio.run(fetch_remote_media(url, max_bytes=max_bytes, timeout=2.0))


def _resolving_to(ip: str):
    """Faz ``getaddrinfo`` devolver ``ip`` para QUALQUER nome.

    É assim que se testa "nome público que resolve para endereço interno" sem
    depender de um DNS de verdade — o caso que uma checagem por texto do host
    deixaria passar.
    """
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return patch.object(
        remote_media.socket, "getaddrinfo",
        lambda host, port, *a, **k: [
            (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))])


# ── G1 — só http/https ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://10.0.0.1:70/_teste",
    "ftp://exemplo.com/arquivo.pdf",
    "data:text/plain;base64,aGVsbG8=",
])
def test_g1_recusa_esquema_que_nao_seja_http(url):
    with pytest.raises(RemoteMediaError) as e:
        _fetch(url)
    assert e.value.reason == remote_media.BAD_SCHEME


def test_g1_url_sem_host_e_recusada():
    with pytest.raises(RemoteMediaError) as e:
        _fetch("http:///caminho/sem/host.pdf")
    assert e.value.reason == remote_media.BAD_SCHEME


# ── G3 — o IP RESOLVIDO, não o texto do host ────────────────────────────────

@pytest.mark.parametrize("ip", [
    "127.0.0.1",            # loopback
    "10.0.0.5",           # RFC1918 — um Postgres interno mora numa dessas
    "192.168.1.10",
    "172.16.0.1",
    "169.254.169.254",      # metadados de nuvem: o alvo clássico
    "100.64.0.1",           # CGNAT
    "::1",                  # loopback IPv6
    "fd00::1",              # ULA IPv6
    "0.0.0.0",
])
def test_g3_recusa_endereco_interno(ip):
    with pytest.raises(RemoteMediaError) as e:
        with _resolving_to(ip):
            _fetch("https://arquivos.exemplo.com/cert.pdf")
    assert e.value.reason == remote_media.BLOCKED_HOST


def test_g3_nome_publico_que_resolve_para_loopback_e_recusado():
    """O caso que uma checagem por NOME deixaria passar inteiro.

    ``localhost.meudominio.com`` é um host público, registrado, com A record
    apontando para ``127.0.0.1``. Só olhar o texto do host aceita; olhar o
    endereço resolvido recusa.
    """
    with pytest.raises(RemoteMediaError) as e:
        with _resolving_to("127.0.0.1"):
            _fetch("https://localhost.meudominio.com/cert.pdf")
    assert e.value.reason == remote_media.BLOCKED_HOST


def test_g3_recusa_quando_um_dos_enderecos_e_interno():
    """Registro duplo (um público + um privado) é recusado.

    Aceitar "basta um endereço ser público" é a forma canônica de burlar o
    guard: o atacante publica os dois e a conexão pode sair por qualquer um.
    """
    def _two(host, port, *a, **k):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
        ]
    with pytest.raises(RemoteMediaError) as e:
        with patch.object(remote_media.socket, "getaddrinfo", _two):
            _fetch("https://duplo.exemplo.com/cert.pdf")
    assert e.value.reason == remote_media.BLOCKED_HOST


def test_g3_ip_interno_literal_na_url_e_recusado():
    """Sem DNS no meio: o mesmo guard vale para um literal."""
    with pytest.raises(RemoteMediaError) as e:
        _fetch("http://10.0.0.5:5432/")
    assert e.value.reason == remote_media.BLOCKED_HOST


# ── G2 — redirect não é seguido ─────────────────────────────────────────────

def test_g2_redirect_nao_e_seguido_e_vira_erro_explicito():
    """Redirect é o bypass clássico: o alvo público responde 302 para
    ``http://169.254.169.254/``. Não seguimos — e dizemos por quê."""
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    with _resolving_to("93.184.216.34"), _mock_transport(_handler):
        with pytest.raises(RemoteMediaError) as e:
            _fetch("https://arquivos.exemplo.com/cert.pdf")
    assert e.value.reason == remote_media.BAD_STATUS
    assert "redirecionamento" in e.value.message


def test_g2_o_cliente_e_construido_sem_follow_redirects():
    """Trava a construção, não só o comportamento observado: se alguém ligar
    ``follow_redirects=True`` no cliente, o teste acima passaria a seguir o
    302 silenciosamente para a rede interna."""
    import inspect
    src = inspect.getsource(remote_media.fetch_remote_media)
    assert "follow_redirects=False" in src


# ── G4 — o teto vale no STREAM, não no Content-Length ───────────────────────

def test_g4_corta_no_teto_mesmo_com_content_length_mentindo():
    """O servidor remoto declara 10 bytes e manda 5 MB. Confiar no header é
    confiar no atacante."""
    import httpx

    payload = b"x" * (5 * MB)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload,
                              headers={"content-length": "10",
                                       "content-type": "application/pdf"})

    with _resolving_to("93.184.216.34"), _mock_transport(_handler):
        with pytest.raises(RemoteMediaError) as e:
            _fetch("https://arquivos.exemplo.com/grande.pdf", max_bytes=1 * MB)
    assert e.value.reason == remote_media.TOO_BIG


def test_g4_arquivo_dentro_do_teto_passa_com_o_content_type():
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.4 conteudo",
                              headers={"content-type": "application/pdf; charset=binary"})

    with _resolving_to("93.184.216.34"), _mock_transport(_handler):
        data, ctype = _fetch("https://arquivos.exemplo.com/cert.pdf")
    assert data == b"%PDF-1.4 conteudo"
    assert ctype == "application/pdf"


# ── G6 — nada escapa como 500 ───────────────────────────────────────────────

def test_g6_host_inalcancavel_vira_erro_de_dominio():
    def _boom(host, port, *a, **k):
        raise OSError("Name or service not known")

    with patch.object(remote_media.socket, "getaddrinfo", _boom):
        with pytest.raises(RemoteMediaError) as e:
            _fetch("https://nao-existe.invalido/cert.pdf")
    assert e.value.reason == remote_media.UNREACHABLE


def test_g6_erro_de_rede_no_meio_do_download_vira_erro_de_dominio():
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada")

    with _resolving_to("93.184.216.34"), _mock_transport(_handler):
        with pytest.raises(RemoteMediaError) as e:
            _fetch("https://arquivos.exemplo.com/cert.pdf")
    assert e.value.reason == remote_media.UNREACHABLE


def test_g6_http_404_vira_erro_de_dominio_e_nao_excecao_crua():
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _resolving_to("93.184.216.34"), _mock_transport(_handler):
        with pytest.raises(RemoteMediaError) as e:
            _fetch("https://arquivos.exemplo.com/sumiu.pdf")
    assert e.value.reason == remote_media.BAD_STATUS


def test_g6_resposta_vazia_vira_erro_de_dominio():
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    with _resolving_to("93.184.216.34"), _mock_transport(_handler):
        with pytest.raises(RemoteMediaError):
            _fetch("https://arquivos.exemplo.com/vazio.pdf")


# ── infra de teste ──────────────────────────────────────────────────────────

def _mock_transport(handler):
    """Injeta um ``MockTransport`` no ``AsyncClient`` que a função constrói.

    A função monta o próprio cliente de propósito (é ela que fixa
    ``follow_redirects=False`` e o timeout); o teste embrulha o construtor em
    vez de passar um transporte por parâmetro, para não abrir um seam que a
    produção poderia usar para desligar os guards.
    """
    import httpx

    original = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    return patch.object(httpx, "AsyncClient", _factory)
