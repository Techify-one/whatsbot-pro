"""Plano 179 — tela Arquivos: rotas ``/api/files/*``.

Cobre o gate ``files.manage``, o escopo por conversa (D2: lista e prévia recortadas;
exclusão SEM recorte por caixa — P1), id forjado, HTML/SVG nunca inline, arquivo de plugin
que sai da conversa mas nunca do disco (D3) com fail-closed, auditoria sem conteúdo de
conversa e o roteamento do WebSocket.

⚠️ ``deps.file_manager`` aponta SEMPRE para um ``tmp_path`` — nunca para o ``statics/`` real.

    venv/bin/python -m pytest tests/integration/test_files_api.py -q
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid

import pytest
from sqlalchemy import text

from db.engine import get_engine
from db.repositories import (
    contact_repo, conversation_repo, inbox_member_repo, message_repo, session_repo, user_repo,
)
from server import file_manager as fm
from server.auth import generate_session_token

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60


def _phone() -> str:
    return "5511" + str(uuid.uuid4().int)[:9]


def _mk_inbox(tag: str) -> int:
    from db.repositories import channel_repo, inbox_repo
    channel_id = f"p179_{tag}_{uuid.uuid4().hex[:6]}"
    channel_repo.create(id=channel_id, provider="whatsapp_cloud", display_name=channel_id, enabled=1)
    inbox = inbox_repo.get_by_channel(channel_id) or inbox_repo.create(
        channel_id=channel_id, name=channel_id)
    return inbox["id"]


def _msg(inbox_id: int, media_path: str | None, *, content="", media_type="image",
         media_name=None, caption=None, role="user", name=""):
    """Contato + conversa na ``inbox_id`` + UMA mensagem com ``media_path``."""
    phone = _phone()
    contact = contact_repo.get_or_create(phone)
    if name:
        contact_repo.update(contact["id"], name=name)
    conv, _ = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net", inbox_id=inbox_id)
    saved = message_repo.add(
        contact["id"], role, content, media_type=media_type, media_path=media_path,
        media_caption=caption, media_name=media_name, conversation_id=conv["id"])
    return {"phone": phone, "contact_id": contact["id"], "conv_id": conv["id"],
            "msg_id": saved["id"], "path": media_path}


def _id(root: str, rel: str) -> str:
    return base64.urlsafe_b64encode(f"{root}:{rel}".encode()).decode().rstrip("=")


@pytest.fixture
def env(client, tmp_path):
    """FileManager num tmp_path + dois inboxes + admin e um usuário preso à inbox A."""
    deps = client.app.state.deps
    original = deps.file_manager
    data = tmp_path / "data"
    (data / "statics" / "media").mkdir(parents=True)
    (data / "statics" / "outbox").mkdir(parents=True)
    deps.file_manager = fm.FileManager(data, cache_ttl=0)

    inbox_a, inbox_b = _mk_inbox("a"), _mk_inbox("b")
    made_users: list[dict] = []
    tokens: list[str] = []
    put_paths: list[str] = []

    def login(*, role_keys=None, permission_keys=None, inboxes=()):
        user = user_repo.create(
            email=f"f179-{uuid.uuid4().hex}@test.local", name="Files Tester",
            password_hash="test-only", role_keys=role_keys,
            permission_keys=permission_keys, custom=permission_keys is not None)
        for ib in inboxes:
            inbox_member_repo.set_members(ib, list({*inbox_member_repo.member_ids(ib), user["id"]}))
        token = generate_session_token()
        session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
        made_users.append(user)
        tokens.append(token)
        return {"Authorization": f"Bearer {token}"}

    def put(root: str, rel: str, data_bytes: bytes = JPEG):
        path = data / "statics" / fm.ROOTS[root] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data_bytes)
        put_paths.append(f"statics/{fm.ROOTS[root]}/{rel}")
        return put_paths[-1]

    ns = type("Env", (), {})()
    ns.deps, ns.data, ns.inbox_a, ns.inbox_b = deps, data, inbox_a, inbox_b
    ns.login, ns.put = login, put
    ns.path = lambda root, rel: data / "statics" / fm.ROOTS[root] / rel
    client.headers.pop("Authorization", None)
    # O 1º usuário fecha a instalação aberta: toda requisição precisa de credencial.
    ns.admin = login(role_keys=["admin"])
    yield ns
    client.headers.pop("Authorization", None)
    deps.file_manager = original
    # O banco é compartilhado entre os testes: o caminho de um arquivo (ex.: "linked.jpg")
    # se repete, e "a mensagem mais antiga ganha" na origem/nome. Solta o que este criou.
    if put_paths:
        with get_engine().begin() as conn:
            conn.execute(text(
                "UPDATE messages SET media_path = NULL "
                "WHERE ltrim(media_path, '/') = ANY(:p)"), {"p": put_paths})
    for token in tokens:
        session_repo.delete(token)
    for user in made_users:
        user_repo.delete(user["id"])


def _get(client, env, url, headers=None, **kw):
    return client.get(url, headers=headers or env.admin, **kw)


# ── gate ────────────────────────────────────────────────────────────────────

def test_every_route_requires_files_manage(client, env):
    h = env.login(permission_keys=["conversation.read"])
    for url in ("/api/files/usage", "/api/files/contacts", "/api/files/inboxes", "/api/files"):
        assert client.get(url, headers=h).status_code == 403, url
    assert client.get(f"/api/files/{_id('received', 'a.jpg')}/preview", headers=h).status_code == 403
    assert client.post("/api/files/delete", json={"ids": ["x"]}, headers=h).status_code == 403


def test_files_manage_alone_is_enough_and_does_not_need_conversation_read(client, env):
    h = env.login(permission_keys=["files.manage"])
    assert client.get("/api/files/usage", headers=h).status_code == 200


def test_gestor_role_can_open_the_screen(client, env):
    h = env.login(role_keys=["gestor"])
    assert client.get("/api/files/usage", headers=h).status_code == 200


# ── usage ───────────────────────────────────────────────────────────────────

def test_usage_unscoped_has_roots_system_and_real_disk(client, env):
    env.put("received", "a.jpg")
    env.put("sent", "b.jpg")
    data = _get(client, env, "/api/files/usage").json()["data"]
    assert data["scoped"] is False
    assert data["roots"]["received"]["files"] == 1 and data["roots"]["sent"]["files"] == 1
    assert "system_bytes" in data
    assert data["disk"]["total"] > 0 and data["disk"]["free"] >= 0


def test_usage_scoped_hides_the_breakdown_and_counts_only_what_is_visible(client, env):
    a = _msg(env.inbox_a, env.put("received", "va.jpg"))
    _msg(env.inbox_b, env.put("received", "vb.jpg"))
    env.put("received", "orphan.jpg")
    h = env.login(permission_keys=["files.manage", "conversation.read"], inboxes=[env.inbox_a])
    data = client.get("/api/files/usage", headers=h).json()["data"]
    assert data["scoped"] is True
    assert "roots" not in data and "system_bytes" not in data
    assert data["disk"]["total"] > 0
    assert data["visible"]["files"] == 1 and data["visible"]["bytes"] == len(JPEG)
    assert a["path"]


# ── listagem + escopo ───────────────────────────────────────────────────────

def _names(resp):
    return sorted(i["rel"] for i in resp.json()["data"]["items"])


def test_listing_is_flat_with_origin_and_orphan_flag(client, env):
    m = _msg(env.inbox_a, env.put("received", "linked.jpg"), name="Alice Foto")
    env.put("received", "loose.jpg")
    r = _get(client, env, "/api/files", params={"root": "received"})
    assert r.status_code == 200
    items = {i["rel"]: i for i in r.json()["data"]["items"]}
    assert items["linked.jpg"]["orphan"] is False
    o = items["linked.jpg"]["origin"]
    assert o["conversation_id"] == m["conv_id"] and o["message_id"] == m["msg_id"]
    assert o["name"] == "Alice Foto" and o["is_group"] is False
    assert items["loose.jpg"]["orphan"] is True and "origin" not in items["loose.jpg"]
    assert all("url" not in i for i in items.values())


def test_scoped_user_only_sees_files_of_visible_conversations(client, env):
    _msg(env.inbox_a, env.put("received", "in-a.jpg"))
    _msg(env.inbox_b, env.put("received", "in-b.jpg"))
    env.put("received", "no-conv.jpg")
    h = env.login(permission_keys=["files.manage", "conversation.read"], inboxes=[env.inbox_a])
    r = client.get("/api/files", headers=h, params={"root": "received"})
    assert _names(r) == ["in-a.jpg"]          # sem conversa NUNCA aparece para quem tem escopo
    assert r.json()["data"]["total"] == 1


def test_orphans_filter_is_empty_for_a_scoped_user_and_works_for_admin(client, env):
    _msg(env.inbox_a, env.put("received", "linked.jpg"))
    env.put("received", "loose.jpg")
    h = env.login(permission_keys=["files.manage", "conversation.read"], inboxes=[env.inbox_a])
    assert _names(client.get("/api/files", headers=h, params={"orphans": True})) == []
    assert _names(_get(client, env, "/api/files", params={"orphans": True})) == ["loose.jpg"]


def test_filter_by_channel_contact_and_chat_type(client, env):
    a = _msg(env.inbox_a, env.put("received", "a.jpg"), name="Cliente A")
    _msg(env.inbox_b, env.put("received", "b.jpg"), name="Cliente B")
    assert _names(_get(client, env, "/api/files", params={"inbox_id": env.inbox_a})) == ["a.jpg"]
    assert _names(_get(client, env, "/api/files", params={"contact_id": a["contact_id"]})) == ["a.jpg"]
    assert _names(_get(client, env, "/api/files", params={"chat_type": "group"})) == []
    assert set(_names(_get(client, env, "/api/files", params={"chat_type": "individual"}))) >= {"a.jpg", "b.jpg"}


def test_search_by_caption_description_and_not_by_the_placeholder(client, env):
    _msg(env.inbox_a, env.put("received", "cap.jpg"), caption="obra da rua das Flores",
         content="obra da rua das Flores")
    _msg(env.inbox_a, env.put("received", "desc.jpg"),
         content="[Descrição da imagem]: um recibo de pagamento\n")
    _msg(env.inbox_a, env.put("received", "plain.jpg"), content="[Imagem enviada pelo contato]")
    by_caption = _get(client, env, "/api/files", params={"q": "flores"})
    assert _names(by_caption) == ["cap.jpg"]
    assert by_caption.json()["data"]["items"][0]["match"] == "text"
    assert _names(_get(client, env, "/api/files", params={"q": "recibo"})) == ["desc.jpg"]
    assert _names(_get(client, env, "/api/files", params={"q": "contato"})) == []   # só a marca


def test_search_by_the_name_the_sender_gave(client, env):
    _msg(env.inbox_a, env.put("received", "1792-uuid.pdf", b"%PDF-1.4"),
         media_type="document", media_name="Contrato Social.pdf",
         content="[Documento recebido: Contrato Social.pdf]")
    r = _get(client, env, "/api/files", params={"q": "contrato"})
    item = r.json()["data"]["items"][0]
    assert item["rel"] == "1792-uuid.pdf" and item["name"] == "Contrato Social.pdf"
    assert item["match"] == "name"


def test_legacy_document_name_is_read_from_the_text(client, env):
    _msg(env.inbox_a, env.put("received", "old.pdf", b"%PDF-1.4"), media_type="document",
         content="[Documento recebido: Orçamento 2026.pdf]\nsegue")
    item = _get(client, env, "/api/files").json()["data"]["items"][0]
    assert item["name"] == "Orçamento 2026.pdf"


def test_legacy_media_path_with_a_leading_slash_still_links(client, env):
    _msg(env.inbox_a, "/" + env.put("received", "slash.jpg"))
    item = _get(client, env, "/api/files").json()["data"]["items"][0]
    assert item["orphan"] is False


def test_period_and_kind_filters(client, env):
    import os
    import time
    old = env.put("received", "old.jpg")
    os.utime(env.path("received", "old.jpg"), (time.time() - 10 * 86400,) * 2)
    env.put("received", "new.jpg")
    env.put("received", "clip.mp4", b"\x00\x00\x00\x18ftypmp42")
    cutoff = time.time() - 2 * 86400
    assert _names(_get(client, env, "/api/files", params={"date_from": cutoff})) == ["clip.mp4", "new.jpg"]
    assert _names(_get(client, env, "/api/files", params={"date_to": cutoff})) == ["old.jpg"]
    assert _names(_get(client, env, "/api/files", params={"kind": "video"})) == ["clip.mp4"]
    assert old


def test_invalid_root_is_400(client, env):
    assert _get(client, env, "/api/files", params={"root": "storages"}).status_code == 400


def test_contacts_and_inboxes_pickers_are_scoped(client, env):
    a = _msg(env.inbox_a, env.put("received", "pa.jpg"), name="Zelda Alvo")
    _msg(env.inbox_b, env.put("received", "pb.jpg"), name="Zelda Escondida")
    h = env.login(permission_keys=["files.manage", "conversation.read"], inboxes=[env.inbox_a])
    names = [c["name"] for c in client.get("/api/files/contacts", headers=h,
                                           params={"q": "zelda"}).json()["data"]]
    assert names == ["Zelda Alvo"]
    admin_names = {c["name"] for c in _get(client, env, "/api/files/contacts",
                                           params={"q": "zelda"}).json()["data"]}
    assert {"Zelda Alvo", "Zelda Escondida"} <= admin_names
    assert [i["id"] for i in client.get("/api/files/inboxes", headers=h).json()["data"]] == [env.inbox_a]
    assert {env.inbox_a, env.inbox_b} <= {i["id"] for i in
                                          _get(client, env, "/api/files/inboxes").json()["data"]}
    assert a["contact_id"]


# ── prévia ──────────────────────────────────────────────────────────────────

def test_preview_serves_the_bytes_with_the_real_mime_and_no_store(client, env):
    _msg(env.inbox_a, env.put("received", "p.jpg"))
    r = _get(client, env, f"/api/files/{_id('received', 'p.jpg')}/preview")
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    dl = _get(client, env, f"/api/files/{_id('received', 'p.jpg')}/preview", params={"download": True})
    assert dl.headers["content-disposition"].startswith("attachment")


def test_preview_download_uses_the_sender_name(client, env):
    _msg(env.inbox_a, env.put("received", "1792-uuid.pdf", b"%PDF-1.4"), media_type="document",
         media_name="Contrato.pdf")
    r = _get(client, env, f"/api/files/{_id('received', '1792-uuid.pdf')}/preview",
             params={"download": True})
    assert "Contrato.pdf" in r.headers["content-disposition"]


@pytest.mark.parametrize("name,body", [
    ("x.html", b"<script>alert(1)</script>"), ("x.svg", b"<svg><script>1</script></svg>"),
])
def test_active_content_is_never_inline(client, env, name, body):
    _msg(env.inbox_a, env.put("received", name, body), media_type="document")
    r = _get(client, env, f"/api/files/{_id('received', name)}/preview")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-type"].startswith("application/octet-stream")


def test_forged_id_of_another_inbox_is_404_for_a_scoped_user(client, env):
    _msg(env.inbox_a, env.put("received", "mine.jpg"))
    _msg(env.inbox_b, env.put("received", "theirs.jpg"))
    env.put("received", "no-conv.jpg")
    h = env.login(permission_keys=["files.manage", "conversation.read"], inboxes=[env.inbox_a])
    assert client.get(f"/api/files/{_id('received', 'mine.jpg')}/preview", headers=h).status_code == 200
    assert client.get(f"/api/files/{_id('received', 'theirs.jpg')}/preview", headers=h).status_code == 404
    assert client.get(f"/api/files/{_id('received', 'no-conv.jpg')}/preview", headers=h).status_code == 404
    assert client.get(f"/api/files/{_id('received', 'no-conv.jpg')}/preview",
                      headers=env.admin).status_code == 200


def test_garbage_and_traversal_ids_are_404(client, env):
    for bad in ("!!!", _id("received", "../../storages/x"), _id("storages", "a"), _id("received", "")):
        assert _get(client, env, f"/api/files/{bad}/preview").status_code == 404, bad


# ── exclusão ────────────────────────────────────────────────────────────────

def _row(msg_id: int) -> dict:
    with get_engine().connect() as conn:
        return dict(conn.execute(
            text("SELECT media_path, media_state, media_type FROM messages WHERE id = :i"),
            {"i": msg_id}).mappings().one())


def test_delete_a_common_file_removes_it_and_flags_every_message(client, env):
    path = env.put("received", "gone.jpg")
    m1 = _msg(env.inbox_a, path)
    m2 = _msg(env.inbox_b, path)                  # o MESMO arquivo em outra caixa
    r = client.post("/api/files/delete", json={"ids": [_id("received", "gone.jpg")]}, headers=env.admin)
    assert r.status_code == 200
    d = r.json()["data"]
    assert (d["files"], d["bytes"], d["failed"], d["messages"]) == (1, len(JPEG), 0, 2)
    assert d["kept_plugin"] == 0 and d["kept_unknown"] == 0
    assert not env.path("received", "gone.jpg").exists()
    for m in (m1, m2):
        row = _row(m["msg_id"])
        assert row["media_path"] is None and row["media_state"] == "removed"
        assert row["media_type"] == "image"        # o tipo é mantido


def test_scoped_user_with_the_permission_deletes_even_outside_their_inbox(client, env):
    """P1: quem tem a permissão apaga independente da caixa de entrada."""
    path = env.put("received", "shared.jpg")
    mine = _msg(env.inbox_a, path)
    theirs = _msg(env.inbox_b, path)
    env.put("received", "no-conv.jpg")
    h = env.login(permission_keys=["files.manage", "conversation.read"], inboxes=[env.inbox_a])
    r = client.post("/api/files/delete", headers=h, json={"ids": [
        _id("received", "shared.jpg"), _id("received", "no-conv.jpg")]})
    d = r.json()["data"]
    assert d["files"] == 2 and d["failed"] == 0
    assert not env.path("received", "shared.jpg").exists()
    assert _row(mine["msg_id"])["media_state"] == "removed"
    assert _row(theirs["msg_id"])["media_state"] == "removed"


def test_delete_with_garbage_or_traversal_ids_counts_failed_and_touches_nothing(client, env):
    env.put("received", "keep.jpg")
    r = client.post("/api/files/delete", headers=env.admin, json={"ids": [
        "!!!", _id("received", "../../.env"), _id("storages", "x")]})
    d = r.json()["data"]
    assert d["files"] == 0 and d["failed"] == 3
    assert env.path("received", "keep.jpg").exists()


def test_delete_body_validation_and_batch_cap(client, env):
    assert client.post("/api/files/delete", json={"ids": []}, headers=env.admin).status_code == 400
    assert client.post("/api/files/delete", json={}, headers=env.admin).status_code == 400
    assert client.post("/api/files/delete", json={"ids": [1]}, headers=env.admin).status_code == 400
    too_many = [_id("received", f"f{i}.jpg") for i in range(fm.MAX_DELETE_IDS + 1)]
    assert client.post("/api/files/delete", json={"ids": too_many}, headers=env.admin).status_code == 400


# ── arquivo de plugin (D3) ──────────────────────────────────────────────────

@pytest.fixture
def plugin_table(_engine_ready):
    """Tabela ``plugin_x179_coisas`` (id de plugin com ``_``) com coluna ``media_path``."""
    from db.repositories import plugin_media_repo
    with get_engine().begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS plugin_arq179_coisas"))
        conn.execute(text("CREATE TABLE plugin_arq179_coisas (id serial PRIMARY KEY, media_path text)"))
        conn.execute(text("INSERT INTO plugins (id, version, enabled, installed_at, updated_at) "
                          "VALUES ('arq179', '1.0.0', 0, 0, 0) ON CONFLICT (id) DO NOTHING"))
    plugin_media_repo.invalidate()
    yield "plugin_arq179_coisas"
    with get_engine().begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS plugin_arq179_coisas"))
        conn.execute(text("DELETE FROM plugins WHERE id = 'arq179'"))
    plugin_media_repo.invalidate()


def _register(table: str, path: str):
    with get_engine().begin() as conn:
        conn.execute(text(f"INSERT INTO {table} (media_path) VALUES (:p)"), {"p": path})


def test_plugin_owner_is_resolved_by_the_longest_id_prefix():
    from db.repositories import plugin_media_repo as pm
    ids = ["arquivos", "arquivos_suporte"]
    assert pm.owner_of_table("plugin_arquivos_suporte_arquivos", ids) == "arquivos_suporte"
    assert pm.owner_of_table("plugin_arquivos_x", ids) == "arquivos"
    assert pm.owner_of_table("plugin_outro_x", ids) is None


def test_plugin_file_is_flagged_in_the_listing(client, env, plugin_table):
    path = env.put("sent", "material.pdf", b"%PDF-1.4")
    _register(plugin_table, path)
    item = _get(client, env, "/api/files", params={"root": "sent"}).json()["data"]["items"][0]
    assert item["plugin"]["id"] == "arq179" and item["orphan"] is True


def test_deleting_a_plugin_file_leaves_the_disk_but_clears_the_conversations(client, env, plugin_table):
    path = env.put("sent", "material.pdf", b"%PDF-1.4")
    _register(plugin_table, path)
    sent = _msg(env.inbox_a, path, media_type="document", role="assistant")
    r = client.post("/api/files/delete", json={"ids": [_id("sent", "material.pdf")]}, headers=env.admin)
    d = r.json()["data"]
    assert (d["files"], d["bytes"], d["kept_plugin"], d["messages"]) == (0, 0, 1, 1)
    assert env.path("sent", "material.pdf").exists()                 # o plugin continua enviando
    row = _row(sent["msg_id"])
    assert row["media_path"] is None and row["media_state"] == "removed"


def test_a_failure_discovering_plugin_files_keeps_everything_in_the_outbox(client, env, monkeypatch):
    """Fail-closed: na dúvida, guarda."""
    from db.repositories import plugin_media_repo as pm
    sent_path = env.put("sent", "unknown.pdf", b"%PDF-1.4")
    recv_path = env.put("received", "inbound.jpg")
    sent = _msg(env.inbox_a, sent_path, media_type="document", role="assistant")
    recv = _msg(env.inbox_a, recv_path)

    def boom(_paths):
        raise pm.PluginMediaError("simulated")
    monkeypatch.setattr(pm, "owned_paths", boom)
    r = client.post("/api/files/delete", headers=env.admin, json={"ids": [
        _id("sent", "unknown.pdf"), _id("received", "inbound.jpg")]})
    d = r.json()["data"]
    assert d["kept_unknown"] == 1 and d["files"] == 1                 # só o recebido sai
    assert env.path("sent", "unknown.pdf").exists()
    assert not env.path("received", "inbound.jpg").exists()
    assert _row(sent["msg_id"])["media_state"] == "removed"           # sai da conversa
    assert _row(recv["msg_id"])["media_state"] == "removed"


# ── auditoria ───────────────────────────────────────────────────────────────

def test_audit_row_has_only_counts_and_disk_names_never_conversation_content(client, env):
    secret_caption = "SEGREDO-DA-CONVERSA-179"
    _msg(env.inbox_a, env.put("received", "1792-aud.pdf", b"%PDF-1.4"), media_type="document",
         media_name="Nome-Do-Remetente-179.pdf", caption=secret_caption,
         content=f"[Documento recebido: Nome-Do-Remetente-179.pdf]\n{secret_caption}")
    client.post("/api/files/delete", json={"ids": [_id("received", "1792-aud.pdf")]}, headers=env.admin)
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT action, resource_type, after_json FROM audit_log "
            "WHERE action = 'files.delete' ORDER BY id DESC LIMIT 1")).mappings().first()
    assert row is not None and row["resource_type"] == "file"
    blob = row["after_json"]
    after = json.loads(blob)
    assert after["files"] == 1 and after["root"] == "received" and after["paths"] == ["1792-aud.pdf"]
    assert secret_caption not in blob and "Nome-Do-Remetente-179" not in blob


# ── WebSocket ───────────────────────────────────────────────────────────────

class _FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def accept(self):
        return None

    async def send_text(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        return None


def test_message_media_updated_reaches_only_who_can_see_the_conversation(_engine_ready):
    from server.state import ConnectionManager
    inbox_a, inbox_b = _mk_inbox("wa"), _mk_inbox("wb")
    m = _msg(inbox_a, "statics/media/ws.jpg")
    member = user_repo.create(email=f"ws179m-{uuid.uuid4().hex}@t.local", name="m",
                              password_hash="x", role_keys=[], permission_keys=["conversation.read"],
                              custom=True)
    outsider = user_repo.create(email=f"ws179o-{uuid.uuid4().hex}@t.local", name="o",
                                password_hash="x", role_keys=[], permission_keys=["conversation.read"],
                                custom=True)
    inbox_member_repo.set_members(inbox_a, [member["id"]])
    inbox_member_repo.set_members(inbox_b, [outsider["id"]])

    async def scenario():
        manager = ConnectionManager()
        allowed, denied = _FakeSocket(), _FakeSocket()
        await manager.connect(allowed, member["id"])
        await manager.connect(denied, outsider["id"])
        await manager.broadcast("message_media_updated", {
            "conversation_id": m["conv_id"],
            "messages": [{"_id": m["msg_id"], "media_state": "removed", "media_path": None}]})
        # sem conversation_id provado ⇒ DESCARTADO, nunca vira broadcast global
        await manager.broadcast("message_media_updated", {"messages": []})
        return allowed.sent, denied.sent

    try:
        got, none = asyncio.run(scenario())
    finally:
        for u in (member, outsider):
            user_repo.delete(u["id"])
    assert [e["event"] for e in got] == ["message_media_updated"]
    assert got[0]["data"]["messages"][0]["media_state"] == "removed"
    assert none == []


def test_delete_broadcasts_per_conversation_and_a_counts_only_storage_event(client, env):
    sent: list[tuple[str, dict]] = []
    ws = env.deps.ws_manager
    original = ws.broadcast

    async def spy(event, data):
        sent.append((event, data))
        return await original(event, data)

    ws.broadcast = spy
    try:
        m = _msg(env.inbox_a, env.put("received", "live.jpg"))
        client.post("/api/files/delete", json={"ids": [_id("received", "live.jpg")]}, headers=env.admin)
    finally:
        ws.broadcast = original
    media = [d for e, d in sent if e == "message_media_updated"]
    assert media == [{"conversation_id": m["conv_id"],
                      "messages": [{"_id": m["msg_id"], "media_state": "removed", "media_path": None}]}]
    storage = [d for e, d in sent if e == "storage_changed"]
    assert storage == [{"files": 1, "bytes": len(JPEG), "messages": 1}]
    assert "conversation_id" not in storage[0]
