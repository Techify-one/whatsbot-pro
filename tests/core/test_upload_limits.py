"""O teto de upload é por LISTA DE CAMINHOS — e por isso esquecível (plano 151 · F4).

O gate de 50 MB (``server.upload_limits``) não vale para "toda rota que recebe
arquivo": ele vale para as rotas que alguém lembrou de escrever na regex. Uma
rota de upload nova que fique de fora **não tem teto nenhum** e carrega o corpo
inteiro para a RAM do processo — foi o achado que virou o item I9 do plano.

Estes testes são a rede contra isso: eles falham se a rota de mídia da v1 sair
da regex, e falham se o teto do caminho JSON (``content_base64``, que não passa
pelo middleware de ``multipart``) for medido depois de decodificar.

    venv/bin/python -m pytest tests/core/test_upload_limits.py -q
"""

from __future__ import annotations

import pytest

from server.upload_limits import (MAX_UPLOAD_BYTES, base64_exceeds,
                                  is_upload_path, too_large_message)


MB = 1024 * 1024


# ── I9 — a rota da v1 está coberta ──────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/v1/messages/media",
    "/api/v1/messages/media/",
])
def test_a_rota_de_midia_da_v1_tem_teto(path):
    """Sem esta linha na regex, um POST de 500 MB na v1 é lido inteiro na RAM."""
    assert is_upload_path(path) is True


@pytest.mark.parametrize("path", [
    "/api/contacts/5511999999999/send-image",
    "/api/contacts/5511999999999/send-audio",
    "/api/contacts/5511999999999/send-video",
    "/api/contacts/5511999999999/send-document",
    "/api/contacts/import",
    "/api/sandbox/send-image",
])
def test_as_rotas_do_painel_continuam_cobertas(path):
    """A regex é ancorada; alargá-la para a v1 não pode ter afrouxado o resto."""
    assert is_upload_path(path) is True


@pytest.mark.parametrize("path", [
    "/api/v1/messages",                    # texto: corpo JSON pequeno
    "/api/v1/messages/media/link",         # JSON — o teto dele é o base64_exceeds
    "/api/v1/contacts",
    "/api/contacts/5511999999999/send",
    "/api/v1/messages/media/extra",        # a âncora ``$`` impede o prefixo solto
])
def test_rota_que_nao_e_upload_multipart_nao_entra_no_gate(path):
    assert is_upload_path(path) is False


# ── I10 — o teto do base64 é medido ANTES de decodificar ────────────────────

def _b64_of(size: int) -> str:
    """A string base64 que um arquivo de ``size`` bytes produziria — sem alocar
    o arquivo (é justamente o ponto: o guard não pode precisar do conteúdo)."""
    body = (size + 2) // 3 * 4
    pad = (3 - size % 3) % 3
    return "A" * (body - pad) + "=" * pad


def test_base64_grande_e_recusado_pelo_comprimento_da_string():
    """60 MB de arquivo ⇒ ~80 MB de base64. A recusa não pode exigir decodificar
    (decodificar já é ter o arquivo inteiro na memória)."""
    assert base64_exceeds(_b64_of(60 * MB)) is True


def test_base64_dentro_do_teto_passa():
    assert base64_exceeds(_b64_of(10 * MB)) is False


def test_a_fronteira_do_base64_bate_com_o_teto_do_multipart():
    """Os dois caminhos recusam no MESMO tamanho de arquivo — um integrador não
    deve descobrir um limite diferente só por ter trocado de forma de envio."""
    assert base64_exceeds(_b64_of(MAX_UPLOAD_BYTES)) is False
    assert base64_exceeds(_b64_of(MAX_UPLOAD_BYTES + 1)) is True


def test_a_frase_do_limite_e_a_mesma_nas_duas_superficies():
    assert too_large_message() == f"Arquivo excede o limite de {MAX_UPLOAD_BYTES // MB} MB."
