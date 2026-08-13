"""Plano 119 — o caminho da mídia do template vem do navegador.

``send_template`` passou a gravar o cabeçalho de mídia junto da mensagem, para o
histórico mostrar a imagem que saiu em vez de só o texto do corpo. O caminho é
escolhido pelo modal (que roda no cliente), então ele é uma ENTRADA e nunca pode
ser usado como veio: sem a trava, um ``../../`` faria o painel pedir um arquivo
arbitrário do disco.

O outro lado do contrato é igualmente importante: recusar tem de ser MACIO.
Quando esta função roda, o template já foi entregue ao cliente — devolver erro
por causa da miniatura transformaria um envio bem-sucedido em falha na tela.

    venv/bin/python -m pytest tests/core/test_template_media_sanitize.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.template_service import sanitize_media


@pytest.fixture
def deps(tmp_path):
    """Deps com um outbox real e um arquivo dentro dele."""
    outbox = tmp_path / "statics" / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "foto.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "segredo.env").write_text("DATABASE_URL=postgres://...")
    return SimpleNamespace(statics_outbox_dir=outbox)


def test_arquivo_do_outbox_passa(deps):
    assert sanitize_media(deps, "image", "statics/outbox/foto.jpg") == (
        "image", "statics/outbox/foto.jpg")


def test_barra_inicial_e_maiuscula_sao_normalizadas(deps):
    """O cliente manda `/statics/...` sem querer; isso não é ataque, é ruído."""
    assert sanitize_media(deps, "IMAGE", "/statics/outbox/foto.jpg") == (
        "image", "statics/outbox/foto.jpg")


@pytest.mark.parametrize("path", [
    "statics/outbox/../../segredo.env",
    "statics/outbox/../avatars/5511999.jpg",
    "../segredo.env",
    "/etc/passwd",
    "statics/avatars/5511999.jpg",       # outro diretório, mesmo que exista
    "statics/outbox/sub/foto.jpg",       # subdiretório nunca é servido
    "statics/outbox/",
    "https://exemplo.com/x.jpg",         # URL externa não é caminho nosso
])
def test_caminho_fora_do_outbox_e_recusado(deps, path):
    assert sanitize_media(deps, "image", path) == (None, None)


def test_arquivo_inexistente_e_recusado(deps):
    """Sem o arquivo a bolha nasceria quebrada — melhor gravar texto."""
    assert sanitize_media(deps, "image", "statics/outbox/nao_existe.jpg") == (None, None)


@pytest.mark.parametrize("kind", ["audio", "sticker", "location", "", "texto", None])
def test_tipo_que_o_painel_nao_desenha_e_recusado(deps, kind):
    """Cabeçalho de template só é imagem, vídeo ou documento."""
    assert sanitize_media(deps, kind, "statics/outbox/foto.jpg") == (None, None)


@pytest.mark.parametrize("kind", ["image", "video", "document"])
def test_os_tres_formatos_de_cabecalho_passam(deps, kind):
    assert sanitize_media(deps, kind, "statics/outbox/foto.jpg")[0] == kind


def test_sem_midia_e_o_caminho_normal_e_silencioso(deps):
    """A imensa maioria dos templates não tem cabeçalho de mídia."""
    assert sanitize_media(deps, None, None) == (None, None)
    assert sanitize_media(deps, "", "") == (None, None)


def test_sem_outbox_configurado_nao_explode(tmp_path):
    """Deps sem o atributo (teste, core antigo) valida só a forma."""
    assert sanitize_media(SimpleNamespace(), "image", "statics/outbox/x.jpg") == (
        "image", "statics/outbox/x.jpg")
    assert sanitize_media(SimpleNamespace(), "image", "../x.jpg") == (None, None)
