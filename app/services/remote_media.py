"""Buscar um arquivo numa URL escolhida pelo CHAMADOR, sem virar SSRF (plano 151 · F3).

``POST /api/v1/messages/media/link`` deixa uma integração dizer "manda o arquivo
que está nesta URL". Isso é **Server-Side Request Forgery por construção**: quem
tem apenas ``conversation.reply`` passa a mandar o servidor abrir conexões, e sem
guard a chave de API viraria um scanner da rede interna e um leitor do endpoint
de metadados da nuvem (``169.254.169.254``, que devolve credencial de instância
em texto puro).

O precedente no repo é o ``follow_redirects=False`` do
:mod:`server.webhook_dispatcher` — necessário, e bem longe de suficiente: lá a
URL é cadastrada por um admin, aqui ela chega no corpo de cada requisição.

Os seis guards (nomeados como no plano, cada um com teste próprio):

======  ===========================================================  ==================================
Guard   Regra                                                        Por que
======  ===========================================================  ==================================
G1      só ``http``/``https``                                        ``file://``/``gopher://`` leem disco e falam protocolos internos
G2      ``follow_redirects=False``                                   redirect é o bypass clássico de allowlist — o alvo público responde ``302`` para ``127.0.0.1``
G3      recusa IP de loopback/privado/link-local/CGNAT/ULA/multicast  sem isso a rota é um oráculo da rede interna; roda sobre o IP **resolvido**, não sobre o texto do host
G4      teto aplicado no STREAMING                                    ``Content-Length`` é declarado pelo servidor remoto — mentir nele é trivial
G5      timeout curto (10 s)                                          uma URL que pendura prende um worker
G6      tudo vira :class:`RemoteMediaError`                           alvo inalcançável é entrada inválida (400), nunca bug do WhatsBot (500)
======  ===========================================================  ==================================

⚠️ **G3 é sobre o IP, não sobre o nome.** ``http://localhost.meudominio.com`` é
um host público que resolve para ``127.0.0.1`` — recusar por substring de nome
não pega isso, e recusar depois de conectar já vazou a informação. Por isso o
host é resolvido AQUI, todos os endereços são checados, e a conexão é feita
contra um IP aprovado (``Host:`` preservado no cabeçalho) em vez de deixar o
httpx resolver de novo — senão sobra uma janela de DNS rebinding entre a
checagem e a conexão.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0

# Reasons — vocabulário estável, é o ``code`` que a v1 devolve em ``details``.
BAD_SCHEME = "bad_scheme"
BLOCKED_HOST = "blocked_host"
TOO_BIG = "too_big"
UNREACHABLE = "unreachable"
BAD_STATUS = "bad_status"


class RemoteMediaError(Exception):
    """Falha de DOMÍNIO ao buscar a URL — a rota mapeia para 400, nunca 500.

    É a única exceção que :func:`fetch_remote_media` levanta: qualquer coisa
    vinda do socket/httpx é convertida aqui dentro.
    """

    def __init__(self, message: str, *, reason: str = UNREACHABLE) -> None:
        self.message = message
        self.reason = reason
        super().__init__(message)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """G3 — todo endereço que não seja internet pública aberta.

    ``is_global`` já cobre a maior parte (privado, loopback, link-local,
    multicast, reservado, CGNAT ``100.64/10``, ULA ``fc00::/7``), mas ele é
    permissivo em faixas que não queremos aqui, então os casos que importam
    ficam EXPLÍCITOS — inclusive o ``169.254.169.254``, que é o alvo concreto de
    todo relatório de SSRF em nuvem.
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or not ip.is_global
    )


def _resolve_allowed(host: str, port: int) -> str:
    """Resolve ``host`` e devolve UM endereço aprovado, ou levanta (G3).

    Recusa se **qualquer** endereço resolvido for bloqueado — não basta um deles
    ser público: um nome com registro duplo (um público, um ``127.0.0.1``) é
    exatamente a forma de burlar uma checagem que aceita "pelo menos um ok".
    """
    # Um literal de IP na URL nem chega ao DNS; a checagem é a mesma.
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise RemoteMediaError(f"Não foi possível resolver o host: {e}",
                               reason=UNREACHABLE) from None
    if not infos:
        raise RemoteMediaError("Host sem endereço.", reason=UNREACHABLE)

    approved: str | None = None
    for family, _type, _proto, _canon, sockaddr in infos:
        raw = sockaddr[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise RemoteMediaError("Endereço de destino inválido.",
                                   reason=BLOCKED_HOST) from None
        if _is_blocked_ip(ip):
            raise RemoteMediaError(
                "Destino não permitido: a URL aponta para um endereço interno "
                "(rede privada, loopback ou metadados da nuvem).",
                reason=BLOCKED_HOST)
        if approved is None:
            approved = raw
    return approved or host


async def fetch_remote_media(url: str, *, max_bytes: int,
                             timeout: float = TIMEOUT_SECONDS) -> tuple[bytes, str | None]:
    """Baixa ``url`` respeitando G1–G6. Devolve ``(bytes, content_type|None)``.

    Levanta :class:`RemoteMediaError` — e **só** ela — em qualquer falha.
    """
    import httpx

    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https"):                       # G1
        raise RemoteMediaError(
            "URL inválida: use http:// ou https://.", reason=BAD_SCHEME)
    if not parts.hostname:
        raise RemoteMediaError("URL sem host.", reason=BAD_SCHEME)

    port = parts.port or (443 if parts.scheme == "https" else 80)
    ip = _resolve_allowed(parts.hostname, port)                     # G3

    # Conecta no IP JÁ aprovado e carrega o ``Host:`` original — fecha a janela
    # de DNS rebinding entre a checagem e a conexão. Com TLS, o ``Host`` também
    # é o SNI/nome verificado no certificado, então o handshake continua correto.
    netloc_ip = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
    target = urlunsplit((parts.scheme, netloc_ip, parts.path or "/",
                         parts.query, ""))
    host_header = parts.netloc.split("@")[-1]

    chunks: list[bytes] = []
    total = 0
    try:
        async with httpx.AsyncClient(
                follow_redirects=False,                             # G2
                timeout=timeout,                                    # G5
                headers={"Host": host_header,
                         "User-Agent": "WhatsBot-Pro-Media/1"}) as client:
            async with client.stream("GET", target,
                                     extensions={"sni_hostname": parts.hostname}) as resp:
                if resp.status_code >= 400:
                    raise RemoteMediaError(
                        f"A URL respondeu HTTP {resp.status_code}.",
                        reason=BAD_STATUS)
                if 300 <= resp.status_code < 400:
                    # G2: não seguimos — e dizemos por quê, senão o integrador
                    # fica horas achando que o arquivo é que está errado.
                    raise RemoteMediaError(
                        "A URL respondeu com um redirecionamento, que não é "
                        "seguido por segurança. Informe a URL final do arquivo.",
                        reason=BAD_STATUS)
                content_type = (resp.headers.get("content-type") or "").split(";")[0].strip()
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:                           # G4
                        raise RemoteMediaError(
                            f"Arquivo remoto excede o limite de "
                            f"{max_bytes // (1024 * 1024)} MB.", reason=TOO_BIG)
                    chunks.append(chunk)
    except RemoteMediaError:
        raise
    except Exception as e:  # noqa: BLE001 — G6: rede fora do ar não é bug nosso
        logger.info("[RemoteMedia] falha ao buscar %s: %s", parts.hostname, e)
        raise RemoteMediaError(f"Não foi possível baixar o arquivo: "
                               f"{type(e).__name__}", reason=UNREACHABLE) from None

    if not chunks:
        raise RemoteMediaError("O arquivo remoto está vazio.", reason=UNREACHABLE)
    return b"".join(chunks), (content_type or None)
