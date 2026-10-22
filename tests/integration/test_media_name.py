"""Plano 179 (Fase 7) — o nome que o REMETENTE deu ao arquivo (``messages.media_name``).

* envio do operador: grava o nome do upload; nomes que o PRÓPRIO front inventa
  (``image.png``, ``voice.ogg``, ``blob``) viram NULL;
* entrada: só grava o que o provider DECLAROU em ``media_extras.file_name`` — genérico,
  sem ``if provider ==`` (foto/vídeo/áudio não declaram nome e ficam NULL);
* o guard de ``_build_image_content`` com ``media_path`` vazio (mídia removida).

    venv/bin/python -m pytest tests/integration/test_media_name.py -q
"""

from __future__ import annotations

import io
import json
import time
import uuid

from sqlalchemy import text

from channels.base import ChannelCapabilities
from db.engine import get_engine
from db.repositories import channel_repo, contact_repo, inbox_repo, message_repo
from tests.fake_provider import FakeChannel

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64


class GroupFake(FakeChannel):
    def send_media_group(self, chat_id, items, *, caption=""):
        from channels.base import SendResult
        return SendResult(ok=True, external_msg_id="<g@x>")


def _register(app):
    channel_id = f"mn_{uuid.uuid4().hex[:8]}"
    provider = f"mn_prov_{uuid.uuid4().hex[:6]}"
    registry = app.state.deps.channel_registry
    klass = GroupFake.configured(
        provider=provider, capabilities=ChannelCapabilities(media=True, media_group=True))
    registry.register_provider(klass)
    inst = klass(channel_id)
    registry.add_channel(channel_id, inst)
    channel_repo.create(id=channel_id, provider=provider, display_name=channel_id)
    inbox_repo.get_or_create_for_channel(channel_id, name=channel_id)
    return channel_id


def _seed(app, channel_id):
    phone = f"cli{uuid.uuid4().hex[:8]}@cliente.example"
    mem = app.state.deps.agent_handler._get_contact(phone, channel_id=channel_id)
    seeded = mem.add_message("assistant", "seed")
    contact = contact_repo.get_by_phone(phone)
    message_repo.add(contact["id"], "user", "oi",
                     conversation_id=seeded["conversation_id"], ts=time.time() - 60)
    return phone, contact["id"]


def _names(contact_id):
    with get_engine().connect() as conn:
        return [r[0] for r in conn.execute(text(
            "SELECT media_name FROM messages WHERE contact_id = :c AND media_type IS NOT NULL "
            "ORDER BY id"), {"c": contact_id})]


def test_operator_upload_name_is_stored_and_app_made_names_are_not(app, client):
    cid = _register(app)
    phone, contact_id = _seed(app, cid)
    files = [("Contrato Social.pdf", PDF, "application/pdf"), ("image.png", PNG, "image/png")]
    r = client.post(
        f"/api/contacts/{phone}/send-media-group",
        files=[("files", (n, io.BytesIO(b), m)) for n, b, m in files],
        data={"kinds": json.dumps(["document", "image"]), "caption": "", "channel_id": cid})
    assert r.status_code == 200, r.text
    assert _names(contact_id) == ["Contrato Social.pdf", None]


def test_a_hostile_upload_name_is_cleaned_before_it_is_stored(app, client):
    cid = _register(app)
    phone, contact_id = _seed(app, cid)
    r = client.post(
        f"/api/contacts/{phone}/send-media-group",
        files=[("files", ("..\\..\\evil<>.pdf", io.BytesIO(PDF), "application/pdf")),
               ("files", ("ok.pdf", io.BytesIO(PDF), "application/pdf"))],
        data={"kinds": json.dumps(["document", "document"]), "caption": "", "channel_id": cid})
    assert r.status_code == 200, r.text
    stored = _names(contact_id)
    assert stored[1] == "ok.pdf"
    assert stored[0] and "/" not in stored[0] and "\\" not in stored[0] and "<" not in stored[0]


def test_inbound_stores_only_what_the_provider_declared():
    from app.services.message_ingest_service import _declared_file_name as declared
    assert declared({"file_name": "Contrato.pdf"}) == "Contrato.pdf"
    assert declared({"file_name": "../../x/Planilha.xlsx"}) == "Planilha.xlsx"
    assert declared({"caption": "só legenda"}) is None        # foto/vídeo/áudio: sem nome
    assert declared({"file_name": ""}) is None
    assert declared({"file_name": None}) is None
    assert declared(None) is None
    assert declared("não é dict") is None


def test_message_repo_add_persists_media_name_and_hides_it_from_the_wire(_engine_ready):
    contact = contact_repo.get_or_create("5511" + str(uuid.uuid4().int)[:9])
    saved = message_repo.add(contact["id"], "user", "[Documento recebido: A.pdf]",
                             media_type="document", media_path="statics/media/x.pdf",
                             media_name="A.pdf")
    assert saved["id"]
    assert _names(contact["id"]) == ["A.pdf"]


def test_removed_state_is_exposed_only_when_set(_engine_ready):
    contact = contact_repo.get_or_create("5511" + str(uuid.uuid4().int)[:9])
    kept = message_repo.add(contact["id"], "user", "", media_type="image",
                            media_path="statics/media/k.jpg")
    gone = message_repo.add(contact["id"], "user", "", media_type="image",
                            media_path="statics/media/g.jpg")
    assert message_repo.mark_media_removed([gone["id"]]) == 1
    rows = {m["_id"]: m for m in message_repo.get_all(contact["id"])}
    assert "media_state" not in rows[kept["id"]] and rows[kept["id"]]["media_path"]
    assert rows[gone["id"]]["media_state"] == "removed"
    assert "media_path" not in rows[gone["id"]]               # NULL ⇒ ausente no payload
    assert rows[gone["id"]]["media_type"] == "image"          # o tipo é MANTIDO


def test_build_image_content_with_an_empty_path_returns_the_placeholder():
    """Mídia removida pela tela Arquivos: ``media_path`` vazio não pode virar leitura da raiz."""
    from agent.memory import _build_image_content
    assert _build_image_content("", "") == "[Imagem enviada pelo contato]"
    assert _build_image_content("", "olha a obra") == "olha a obra"
    assert _build_image_content(None, "") == "[Imagem enviada pelo contato]"
