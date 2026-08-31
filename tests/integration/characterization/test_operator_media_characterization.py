"""Caracterização das QUATRO rotas de mídia do operador (plano 151 · F1).

Estes testes existem para uma coisa só: fixar o comportamento observável das
rotas ``send-image`` / ``send-audio`` / ``send-document`` / ``send-video`` do
painel **antes** de o preparo subir para ``MessagingService.send_media_upload``
(F2). Depois do refactor eles têm de continuar verdes **sem serem editados** —
é essa a rede, não o número de asserções.

O que cada bloco protege, e por que ele é frágil de um jeito que importa:

* **a tabela por-kind** (§2.2 do plano) — ``content`` persistido, ``caption``
  repassada ao canal e ``filename``. As quatro rotas montam SEIS parâmetros
  diferentes para a mesma chamada; uma segunda cópia dessa tabela divergiria em
  silêncio (o precedente é ``send_template``, que teve duas cópias até o 119);
* **o despacho por ``kind``** — imagem com ``kind="document"`` tem de chegar ao
  canal por ``/send/file`` (``documentMessage``, sem recompressão). É o pedido
  que originou o plano, e nada no core faz sniffing de MIME para decidir isso;
* **a ORDEM dos guards** — janela de 24h e limite do canal bloqueiam ANTES de o
  arquivo ir para o disco (sem órfão em ``statics/outbox/``);
* **o desvio de sandbox** — nunca toca o provedor;
* **``abort_ai_cycle``** — enviar mídia é tomada humana e incrementa a época
  (plano 96): sem isso a IA fala por cima do atendente 20 s depois.

``FakeGowaClient.sent`` é a lista ordenada ``(kind, args)`` de tudo que saiu
para o provedor — é dela que sai a prova de "por qual endpoint foi".

    venv/bin/python -m pytest tests/integration/characterization/test_operator_media_characterization.py -q
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

from channels.base import ChannelCapabilities, MediaLimits
from channels.outbound import OutboundRouter
from db.repositories import contact_repo, message_repo


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
OGG = b"OggS" + b"\x00" * 200
PDF = b"%PDF-1.4\n" + b"\x00" * 200
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200


# ── helpers ──────────────────────────────────────────────────────────────────

def _rows(phone: str) -> list[dict]:
    contact = contact_repo.get_by_phone(phone)
    if contact is None:
        return []
    return message_repo.get_all(contact["id"])


def _media_rows(phone: str) -> list[dict]:
    return [m for m in _rows(phone) if m.get("media_type")]


def _outbox(built) -> Path:
    """A pasta de saída REAL desta app (o build hermético usa um tmpdir)."""
    return Path(built.app.state.deps.statics_outbox_dir)


def _outbox_names(built) -> set[str]:
    out = _outbox(built)
    return {p.name for p in out.iterdir()} if out.exists() else set()


def _post(built, phone: str, route: str, field: str, name: str, blob: bytes,
          mime: str, **data):
    return built.client.post(
        f"/api/contacts/{phone}/{route}",
        files={field: (name, io.BytesIO(blob), mime)}, data=data)


def _caps(**kw):
    """Um canal que DECLARA o que o teste precisa, sem tocar em nome de provider."""
    base = dict(media=True, groups=True, inbound_route="path")
    base.update(kw)
    return ChannelCapabilities(**base)


@pytest.fixture
def declared_caps():
    """Substitui o que o canal declara (``OutboundRouter.capabilities``).

    O GOWA não declara limite nem janela — é o canal certo para o caso feliz e
    inútil para caracterizar bloqueio. Trocar a CAPABILITY (e não o provider) é
    também o que prova que os guards são dirigidos por declaração.
    """
    def _apply(caps):
        return patch.object(OutboundRouter, "capabilities", lambda self, cid: caps)
    return _apply


# ── 1 · caso feliz de cada kind: a tabela por-kind, verbatim ────────────────

def test_imagem_persiste_a_legenda_como_conteudo_e_vai_por_send_image(build_app):
    phone = "5511970151001"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    r = _post(built, phone, "send-image", "image", "foto.png", PNG, "image/png",
              caption="olha isso")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["msg_id"] == "FAKE_SENT_IMG"
    assert body["data"]["media_path"].startswith("statics/outbox/")

    row = _media_rows(phone)[-1]
    assert row["media_type"] == "image"
    assert row["content"] == "olha isso"      # §2.2: content = caption
    assert row["status"] == "operator"

    kinds = [k for k, _ in built.gowa_client.sent]
    assert kinds == ["image"], "imagem tem de sair por /send/image"
    assert built.gowa_client.images[-1]["caption"] == "olha isso"


def test_audio_persiste_o_rotulo_fixo_e_nao_manda_legenda(build_app):
    """§2.2: áudio grava ``[Áudio]``, emite texto vazio e NÃO repassa legenda —
    ``/send/audio`` do GOWA é nota de voz (PTT) e não aceita caption."""
    phone = "5511970151002"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    r = _post(built, phone, "send-audio", "audio", "voz.ogg", OGG, "audio/ogg")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["msg_id"] == "FAKE_SENT_AUDIO"

    row = _media_rows(phone)[-1]
    assert row["media_type"] == "audio"
    assert row["content"] == "[Áudio]"

    assert [k for k, _ in built.gowa_client.sent] == ["audio"]
    assert "caption" not in built.gowa_client.audios[-1]


def test_documento_persiste_o_rotulo_com_o_nome_e_anexa_a_legenda(build_app):
    phone = "5511970151003"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    r = _post(built, phone, "send-document", "document", "contrato.pdf", PDF,
              "application/pdf", caption="assine aqui")

    assert r.status_code == 200, r.text
    row = _media_rows(phone)[-1]
    assert row["media_type"] == "document"
    # §2.2: rótulo + "\n" + legenda (a legenda NÃO substitui o rótulo).
    assert row["content"] == "[Documento enviado: contrato.pdf]\nassine aqui"

    assert [k for k, _ in built.gowa_client.sent] == ["file"]
    sent = built.gowa_client.files[-1]
    assert sent["filename"] == "contrato.pdf", (
        "o nome ORIGINAL viaja separado do nome em disco — é ele que define o "
        "MIME no fio e o que o destinatário vê")
    assert sent["caption"] == "assine aqui"


def test_video_cai_no_rotulo_quando_nao_ha_legenda(build_app):
    phone = "5511970151004"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    # GOWA não declara ``media_limits["video"]`` nem janela ⇒ ``video_limits``
    # devolve ``None`` e ``validate_video`` sai OK sem tocar em ffprobe.
    r = _post(built, phone, "send-video", "video", "clipe.mp4", MP4, "video/mp4")

    assert r.status_code == 200, r.text
    row = _media_rows(phone)[-1]
    assert row["media_type"] == "video"
    assert row["content"] == "[Vídeo]"       # §2.2: caption or "[Vídeo]"


# ── 2 · o pedido do plano: imagem COMO documento ────────────────────────────

def test_imagem_enviada_como_documento_vai_por_send_file(build_app):
    """O core despacha por ``kind`` PURO, sem olhar o MIME.

    É o que preserva a qualidade da imagem (``/send/file`` = ``documentMessage``,
    sem recompressão). Se alguém "consertar" isto inferindo o kind do
    ``content_type``, este teste fica vermelho — e deve ficar.
    """
    phone = "5511970151005"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    r = _post(built, phone, "send-document", "document", "foto.png", PNG, "image/png")

    assert r.status_code == 200, r.text
    row = _media_rows(phone)[-1]
    assert row["media_type"] == "document"
    assert row["content"] == "[Documento enviado: foto.png]"

    assert [k for k, _ in built.gowa_client.sent] == ["file"], (
        "uma imagem mandada como documento NÃO pode cair em /send/image")
    assert built.gowa_client.files[-1]["filename"] == "foto.png"


# ── 3 · limite do canal: bloqueia ANTES de gravar ───────────────────────────

def test_limite_do_canal_bloqueia_sem_deixar_orfao_no_disco(build_app, declared_caps):
    phone = "5511970151006"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    before = _outbox_names(built)

    caps = _caps(media_limits={"image": MediaLimits(max_bytes=10,
                                                    extensions=(".png",))})
    with declared_caps(caps):
        r = _post(built, phone, "send-image", "image", "foto.png", PNG, "image/png")

    assert r.status_code == 413
    body = r.json()
    assert body["ok"] is False
    assert body["data"]["reason"] == "too_big"
    assert _outbox_names(built) == before, "bloqueio não pode deixar arquivo órfão"
    assert built.gowa_client.sent == []
    assert _media_rows(phone) == []


def test_formato_recusado_pelo_canal_e_415(build_app, declared_caps):
    phone = "5511970151007"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    before = _outbox_names(built)

    caps = _caps(media_limits={"document": MediaLimits(max_bytes=10 * 1024 * 1024,
                                                       extensions=(".pdf",))})
    with declared_caps(caps):
        r = _post(built, phone, "send-document", "document", "planilha.xlsx", PDF,
                  "application/vnd.ms-excel")

    assert r.status_code == 415
    assert r.json()["data"]["reason"] == "bad_format"
    assert _outbox_names(built) == before
    assert built.gowa_client.sent == []


# ── 4 · janela de 24h: 409 antes de gravar ──────────────────────────────────

def test_janela_fechada_bloqueia_antes_de_gravar_o_arquivo(build_app, declared_caps):
    """Canal com ``session_window_hours`` e sem inbound recente ⇒ 409, e o
    arquivo nem chega ao disco. Sem inbound nenhum a janela está fechada."""
    phone = "5511970151008"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    before = _outbox_names(built)

    with declared_caps(_caps(session_window_hours=24)):
        r = _post(built, phone, "send-image", "image", "foto.png", PNG, "image/png",
                  caption="oi")

    assert r.status_code == 409
    assert r.json()["data"]["reason"] == "session_window_closed"
    assert _outbox_names(built) == before
    assert built.gowa_client.sent == []


def test_canal_sempre_aberto_nunca_e_bloqueado_pela_janela(build_app):
    """GOWA não declara ``session_window_hours`` ⇒ o guard devolve ``None``
    SEMPRE. Este é o par do teste acima: prova que o bloqueio vem da capability
    declarada, não de um ``if provider ==``."""
    phone = "5511970151009"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    r = _post(built, phone, "send-image", "image", "foto.png", PNG, "image/png")

    assert r.status_code == 200, r.text
    assert [k for k, _ in built.gowa_client.sent] == ["image"]


# ── 5 · sandbox: nunca toca o provedor ──────────────────────────────────────

def test_contato_de_sandbox_salva_local_e_nao_envia(build_app):
    phone = "5511970151010"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    from db.repositories import config_repo
    from server.routes.sandbox import SANDBOX_CONTACT_PREFIX
    config_repo.set(f"{SANDBOX_CONTACT_PREFIX}{phone}", True)
    try:
        r = _post(built, phone, "send-image", "image", "foto.png", PNG, "image/png",
                  caption="teste")
    finally:
        config_repo.delete_prefix(f"{SANDBOX_CONTACT_PREFIX}{phone}")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["msg_id"] in (None, "")
    assert built.gowa_client.sent == [], "sandbox nunca vai ao provedor"

    row = _media_rows(phone)[-1]
    assert row["media_type"] == "image"
    assert row["content"] == "teste"


# ── 6 · enviar mídia é tomada humana (plano 96) ─────────────────────────────

def test_envio_de_midia_incrementa_a_epoca_de_aborto_da_ia(build_app):
    """``abort_ai_cycle`` roda em TODA rota de mídia. Sem isso, a resposta da IA
    já em voo chega por cima do atendente segundos depois do envio."""
    phone = "5511970151011"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    state = built.app.state.deps.state
    key = ("default", phone)
    before = state.ai_abort_epochs.get(key, 0)

    _post(built, phone, "send-image", "image", "foto.png", PNG, "image/png")
    _post(built, phone, "send-document", "document", "a.pdf", PDF, "application/pdf")

    assert state.ai_abort_epochs.get(key, 0) >= before + 2


# ── 7 · vídeo com codec recusado vira 422 amigável ─────────────────────────

def test_video_recusado_pelo_provedor_com_131053_vira_422_bad_codec(build_app):
    """A dica do 131053 é FORMATAÇÃO DE MENSAGEM (fica na rota), não regra.
    O provedor devolve o código cru; o painel mostra o que fazer."""
    from channels.base import SendResult

    phone = "5511970151012"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    with patch.object(OutboundRouter, "send_media",
                      lambda self, *a, **k: SendResult(
                          ok=False, error="(#131053) Failed to upload video")):
        r = _post(built, phone, "send-video", "video", "clipe.mp4", MP4, "video/mp4")

    assert r.status_code == 422
    body = r.json()
    assert body["data"]["reason"] == "bad_codec"
    assert "H.264" in body["error"]
