"""Envio AGRUPADO de anexos — "Agrupar" no compositor (plano 177).

Um canal que declara ``ChannelCapabilities.media_group`` (e-mail) entrega N arquivos como
UMA mensagem do provedor. O painel guarda uma bolha por arquivo — como o e-mail RECEBIDO
— e o que se trava aqui é o contrato do CORE, sem plugin: o core nunca conhece o provider
pelo nome, só a capability.

Três propriedades:

1. **Uma chamada ao provedor**, não N (o ponto do recurso).
2. **Identidade por bolha.** O painel FUNDE bolhas de mesmo ``msg_id`` numa só
   (``reconcileByMsgId``). Se as N linhas guardassem o id do provedor cru, N-1 bolhas
   sumiriam na tela. O ÚLTIMO arquivo fica com o id do provedor (é o que a resposta do
   cliente cita); os anteriores levam ``<id>#<n>``.
3. **Tudo-ou-nada.** Falha (limite, provedor, janela) não grava nada nem deixa arquivo
   órfão em ``statics/outbox``.

    venv/bin/python -m pytest tests/integration/test_media_group_send.py -q
"""

from __future__ import annotations

import io
import json
import time
import uuid

from channels.base import ChannelCapabilities, SendResult
from db.repositories import channel_repo, contact_repo, inbox_repo, message_repo
from tests.fake_provider import FakeChannel

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64


class GroupFake(FakeChannel):
    """Canal que entrega o grupo numa chamada só e guarda o que recebeu."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.groups: list[dict] = []
        self.group_result = SendResult(ok=True, external_msg_id="<grp-1@mail.example>")

    def send_media_group(self, chat_id, items, *, caption=""):
        self.groups.append({"chat_id": chat_id, "items": list(items), "caption": caption})
        return self.group_result


def _register(app, caps: ChannelCapabilities, cls=GroupFake):
    channel_id = f"mg_{uuid.uuid4().hex[:8]}"
    provider = f"mg_prov_{uuid.uuid4().hex[:6]}"
    registry = app.state.deps.channel_registry
    klass = cls.configured(provider=provider, capabilities=caps)
    registry.register_provider(klass)
    inst = klass(channel_id)
    registry.add_channel(channel_id, inst)
    channel_repo.create(id=channel_id, provider=provider, display_name=channel_id)
    inbox_repo.get_or_create_for_channel(channel_id, name=channel_id)
    return channel_id, inst


def _seed(app, channel_id: str) -> str:
    """Conversa aberta no canal (o grupo é resposta ao cliente)."""
    phone = f"cli{uuid.uuid4().hex[:8]}@cliente.example"
    mem = app.state.deps.agent_handler._get_contact(phone, channel_id=channel_id)
    seeded = mem.add_message("assistant", "seed")
    contact = contact_repo.get_by_phone(phone)
    message_repo.add(contact["id"], "user", "oi",
                     conversation_id=seeded["conversation_id"], ts=time.time() - 60)
    return phone


def _post(client, phone, channel_id, files, kinds, caption=""):
    return client.post(
        f"/api/contacts/{phone}/send-media-group",
        files=[("files", (name, io.BytesIO(blob), mime)) for name, blob, mime in files],
        data={"kinds": json.dumps(kinds), "caption": caption, "channel_id": channel_id})


def _media_rows(phone):
    contact = contact_repo.get_by_phone(phone)
    return [m for m in message_repo.get_all(contact["id"]) if m.get("media_type")]


GROUP_CAPS = ChannelCapabilities(media=True, media_group=True)
FILES = [("a.png", PNG, "image/png"), ("b.png", PNG, "image/png"),
         ("c.pdf", PDF, "application/pdf")]
KINDS = ["image", "image", "document"]


def test_o_grupo_e_UMA_chamada_ao_provedor(app, client):
    cid, inst = _register(app, GROUP_CAPS)
    phone = _seed(app, cid)

    r = _post(client, phone, cid, FILES, KINDS, caption="Segue tudo junto.")

    assert r.status_code == 200, r.text
    assert len(inst.groups) == 1, "grupo = UMA mensagem do provedor"
    assert [i["filename"] for i in inst.groups[0]["items"]] == ["a.png", "b.png", "c.pdf"]
    assert inst.groups[0]["caption"] == "Segue tudo junto."
    # Nada saiu pelo caminho de um arquivo só.
    assert [k for k, _ in inst.sent if k == "media"] == []


def test_uma_bolha_por_arquivo_com_a_legenda_na_ultima(app, client):
    cid, inst = _register(app, GROUP_CAPS)
    phone = _seed(app, cid)

    r = _post(client, phone, cid, FILES, KINDS, caption="Segue tudo junto.")
    assert r.status_code == 200, r.text

    rows = _media_rows(phone)
    assert [m["media_type"] for m in rows] == ["image", "image", "document"]
    # Legenda SÓ na última (a que a aceita); as outras não repetem o texto.
    assert rows[0]["content"] == "" and rows[1]["content"] == ""
    assert rows[2]["content"] == "[Documento enviado: c.pdf]\nSegue tudo junto."


def test_cada_bolha_tem_msg_id_proprio_e_o_ultimo_e_o_do_provedor(app, client):
    """O painel funde bolhas de mesmo msg_id — ids repetidos fariam N-1 sumirem."""
    cid, inst = _register(app, GROUP_CAPS)
    phone = _seed(app, cid)

    r = _post(client, phone, cid, FILES, KINDS)
    assert r.status_code == 200, r.text

    ids = [m["msg_id"] for m in _media_rows(phone)]
    assert len(set(ids)) == 3, f"msg_id repetido entre bolhas: {ids}"
    assert ids[-1] == "<grp-1@mail.example>", "a última guarda o id que o cliente vai citar"
    assert ids[0] == "<grp-1@mail.example>#1" and ids[1] == "<grp-1@mail.example>#2"
    # O ACK devolve os mesmos ids, na ordem, para o painel adotar nas bolhas otimistas.
    assert r.json()["data"]["msg_ids"] == ids


def test_audio_no_grupo_nao_leva_a_legenda(app, client):
    """Áudio é nota de voz e não aceita legenda: ela desce para o último que aceita."""
    cid, inst = _register(app, GROUP_CAPS)
    phone = _seed(app, cid)

    r = _post(client, phone, cid,
              [("a.png", PNG, "image/png"), ("v.ogg", b"OggS" + b"\0" * 32, "audio/ogg")],
              ["image", "audio"], caption="olha")
    assert r.status_code == 200, r.text

    rows = _media_rows(phone)
    assert rows[0]["content"] == "olha", "a legenda cai na imagem, não no áudio"
    assert rows[1]["content"] == "[Áudio]"


def test_falha_do_provedor_nao_grava_nada_nem_deixa_arquivo_orfao(app, client, tmp_path):
    cid, inst = _register(app, GROUP_CAPS)
    inst.group_result = SendResult(ok=False, error="SMTP recusou")
    phone = _seed(app, cid)
    outbox = app.state.deps.statics_outbox_dir
    import os
    antes = set(os.listdir(outbox)) if os.path.isdir(outbox) else set()

    r = _post(client, phone, cid, FILES, KINDS, caption="x")

    assert r.status_code == 500
    assert _media_rows(phone) == [], "tudo-ou-nada: nenhuma bolha gravada"
    depois = set(os.listdir(outbox)) if os.path.isdir(outbox) else set()
    assert depois == antes, f"arquivos órfãos em statics/outbox: {depois - antes}"


def test_canal_sem_a_capability_recusa_e_nao_grava_em_disco(app, client):
    """WhatsApp/Telegram: o agrupamento não existe — 400, e a fila do painel segue
    mandando um arquivo por mensagem."""
    cid, inst = _register(app, ChannelCapabilities(media=True))   # sem media_group
    phone = _seed(app, cid)

    r = _post(client, phone, cid, FILES, KINDS)

    assert r.status_code == 400, r.text
    assert inst.groups == [] and _media_rows(phone) == []


def test_kinds_fora_de_sincronia_com_os_arquivos_e_400(app, client):
    cid, inst = _register(app, GROUP_CAPS)
    phone = _seed(app, cid)

    r = _post(client, phone, cid, FILES, ["image"])     # 3 arquivos, 1 kind

    assert r.status_code == 400
    assert inst.groups == []


def test_kind_invalido_e_400(app, client):
    cid, inst = _register(app, GROUP_CAPS)
    phone = _seed(app, cid)

    r = _post(client, phone, cid, FILES[:2], ["image", "sticker"])

    assert r.status_code == 400
    assert inst.groups == []


def test_mais_arquivos_que_o_teto_e_400(app, client):
    from app.services.messaging_service import MAX_GROUP_FILES
    cid, inst = _register(app, GROUP_CAPS)
    phone = _seed(app, cid)
    many = [(f"{n}.png", PNG, "image/png") for n in range(MAX_GROUP_FILES + 1)]

    r = _post(client, phone, cid, many, ["image"] * len(many))

    assert r.status_code == 400
    assert inst.groups == []


# ── a capability chega ao painel (a whitelist do shapeConvData depende disto) ──

def test_payload_do_contato_expoe_a_capability(app, client):
    cid, _ = _register(app, GROUP_CAPS)
    phone = _seed(app, cid)

    data = client.get(f"/api/contacts/{phone}?channel_id={cid}").json()["data"]

    assert data["media_group_supported"] is True


def test_payload_de_canal_sem_a_capability_diz_false(app, client):
    cid, _ = _register(app, ChannelCapabilities(media=True))
    phone = _seed(app, cid)

    data = client.get(f"/api/contacts/{phone}?channel_id={cid}").json()["data"]

    assert data["media_group_supported"] is False
