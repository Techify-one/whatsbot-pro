"""A fachada ``/api/v1`` (fase 6) — DTO próprio, escopo do usuário, mesma regra.

O ponto do plano que estes testes travam é o §6: v1 **delega** aos mesmos
serviços do painel. O envio, em especial, passa por
``MessagingService.send_text`` — se um dia alguém reimplementar o envio aqui,
``test_v1_send_uses_the_shared_service`` fica vermelho.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from db.repositories import contact_repo, session_repo, user_repo
from server.auth import generate_session_token


@pytest.fixture
def admin_client(client):
    """Sessão de admin — a v1 aceita sessão de painel e chave pela mesma porta."""
    user = user_repo.create(
        email=f"v1-{uuid.uuid4().hex}@test.local", name="V1",
        password_hash="test-only", role_keys=["admin"])
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
    client.headers.pop("Authorization", None)
    session_repo.delete(token)
    user_repo.delete(user["id"])


# ── DTO ─────────────────────────────────────────────────────────────────────

def test_error_shape_is_v1_not_the_panel_envelope(admin_client):
    """O painel devolve ``{ok:false}`` com 200; a v1 devolve status HTTP + ``error``."""
    r = admin_client.get("/api/v1/contacts/nao-existe-999")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert "ok" not in body


def test_contacts_list_is_paginated(admin_client):
    r = admin_client.get("/api/v1/contacts?limit=1")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"items", "total", "limit", "offset", "has_more"}
    assert body["limit"] == 1


def test_limit_is_clamped_never_500(admin_client):
    assert admin_client.get("/api/v1/contacts?limit=99999").json()["limit"] == 200
    assert admin_client.get("/api/v1/contacts?limit=abc").status_code == 422


# ── contatos: escrita compartilha o serviço do painel ───────────────────────

def test_create_and_patch_contact(admin_client):
    phone = f"5511{uuid.uuid4().int % 10**9:09d}"
    try:
        r = admin_client.post("/api/v1/contacts",
                              json={"phone": phone, "name": "Fulano"})
        assert r.status_code == 201, r.text
        assert r.json()["created"] is True
        assert r.json()["phone"] == phone

        r2 = admin_client.patch(f"/api/v1/contacts/{phone}", json={"name": "Beltrano"})
        assert r2.status_code == 200
        assert r2.json()["name"] == "Beltrano"
    finally:
        row = contact_repo.get_by_phone(phone)
        if row:
            contact_repo.delete(row["id"])


def test_unknown_custom_attribute_is_400_not_500(admin_client):
    phone = f"5511{uuid.uuid4().int % 10**9:09d}"
    try:
        admin_client.post("/api/v1/contacts", json={"phone": phone})
        r = admin_client.patch(f"/api/v1/contacts/{phone}",
                               json={"custom_attributes": {"nao_existe_xyz": "1"}})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_attribute"
    finally:
        row = contact_repo.get_by_phone(phone)
        if row:
            contact_repo.delete(row["id"])


# ── o motor de filtros se autodescreve ──────────────────────────────────────

def test_filter_schema_is_self_describing(admin_client):
    r = admin_client.get("/api/v1/conversations/filter-schema")
    assert r.status_code == 200
    dims = r.json()["dimensions"]
    assert dims and all("key" in d for d in dims)


def test_malformed_filter_is_400_not_500(admin_client):
    r = admin_client.post("/api/v1/conversations/filter",
                          json={"filters": [{"key": "nao_existe", "operator": "eq",
                                             "values": ["x"]}]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_filter"


# ── §6: o envio NÃO é reimplementado ────────────────────────────────────────

def test_v1_send_uses_the_shared_service(admin_client):
    """A v1 chama ``MessagingService.send_text`` — a MESMA função do painel.

    Uma segunda implementação mandaria fora da janela de 24h, para o JID errado,
    sem calar a IA — e nada disso apareceria como erro. Este teste é a trava.
    """
    from app.services.messaging_service import MessagingService

    fake = AsyncMock(return_value={
        "ok": True, "msg_id": "MID1", "conversation_id": 5,
        "channel_id": "default", "sandbox": False, "message": "Mensagem enviada."})
    with patch.object(MessagingService, "send_text", fake):
        r = admin_client.post("/api/v1/messages",
                              json={"phone": "5511999990002", "message": "oi"})
    assert r.status_code == 201, r.text
    assert r.json() == {"sent": True, "msg_id": "MID1", "conversation_id": 5,
                        "channel_id": "default", "sandbox": False}
    assert fake.await_count == 1


def test_v1_send_maps_the_24h_window_block_to_409(admin_client):
    from app.services.messaging_service import MessagingService

    fake = AsyncMock(return_value={
        "ok": False, "status": 409, "reason": "session_window_closed",
        "message": "Fora da janela de 24h: só é possível enviar um template aprovado."})
    with patch.object(MessagingService, "send_text", fake):
        r = admin_client.post("/api/v1/messages",
                              json={"phone": "5511999990002", "message": "oi"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "session_window_closed"


def test_v1_send_requires_phone(admin_client):
    r = admin_client.post("/api/v1/messages", json={"message": "oi"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_field"


# ── OpenAPI ─────────────────────────────────────────────────────────────────

def test_openapi_lists_only_v1_and_declares_the_key(admin_client):
    r = admin_client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["paths"], "esquema vazio"
    assert all(p.startswith("/api/v1") for p in schema["paths"])
    scheme = schema["components"]["securitySchemes"]["ApiKeyAuth"]
    assert scheme == {"type": "apiKey", "in": "header", "name": "X-Api-Key"}


# ── etiquetas de conversa: as duas superfícies chamam a MESMA função ────────

def test_v1_labels_delegate_to_the_shared_service(admin_client):
    """Se alguém reimplementar isto na v1, o painel aberto para de se atualizar,
    plugin nenhum recebe ``conversation.labeled`` e o fio não ganha o card."""
    from app.services import conversation_service as conv_svc
    from db.repositories import conversation_repo

    fake = AsyncMock(return_value=["urgente"])
    with patch.object(conv_svc, "apply_labels", fake), \
         patch.object(conversation_repo, "get", lambda cid: {
             "id": cid, "inbox_id": None, "contact_id": 1}):
        r = admin_client.put("/api/v1/conversations/1/labels",
                             json={"labels": ["urgente"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"conversation_id": 1, "labels": ["urgente"]}
    assert fake.await_count == 1


def test_v1_labels_rejects_a_non_list(admin_client):
    r = admin_client.put("/api/v1/conversations/1/labels", json={"labels": "urgente"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_field"


# ── multicanal: recusar é mais honesto que adivinhar ────────────────────────

def test_v1_send_refuses_an_ambiguous_target(admin_client):
    """Contato com conversa aberta em DUAS caixas ⇒ 409, não um chute.

    ``get_open_for_contact`` (contact-scoped) devolveria uma das duas e a
    mensagem sairia pelo canal errado, em silêncio — é o §8 do plano de API e o
    guardrail do plano 37 P4.
    """
    from db.repositories import conversation_repo

    with patch.object(contact_repo, "get_by_phone", lambda p: {"id": 42}), \
         patch.object(conversation_repo, "list_conversations",
                      lambda **kw: [{"id": 1, "channel_id": "gowa", "inbox_id": 1},
                                    {"id": 2, "channel_id": "cloud", "inbox_id": 2}]):
        r = admin_client.post("/api/v1/messages",
                              json={"phone": "5511999990003", "message": "oi"})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "ambiguous_target"
    # A resposta DIZ quais são as opções — senão o integrador fica adivinhando.
    assert {o["channel_id"] for o in err["details"]["options"]} == {"gowa", "cloud"}


def test_v1_send_uses_the_single_open_conversation(admin_client):
    """Uma só conversa aberta ⇒ resolve sozinho, sem exigir nada do integrador."""
    from app.services.messaging_service import MessagingService
    from db.repositories import conversation_repo

    fake = AsyncMock(return_value={
        "ok": True, "msg_id": "M", "conversation_id": 7, "channel_id": "gowa",
        "sandbox": False, "message": "Mensagem enviada."})
    with patch.object(contact_repo, "get_by_phone", lambda p: {"id": 42}), \
         patch.object(conversation_repo, "list_conversations",
                      lambda **kw: [{"id": 7, "channel_id": "gowa", "inbox_id": 1}]), \
         patch.object(MessagingService, "send_text", fake):
        r = admin_client.post("/api/v1/messages",
                              json={"phone": "5511999990003", "message": "oi"})
    assert r.status_code == 201, r.text
    assert fake.await_args.kwargs["conversation_id"] == 7
    assert fake.await_args.kwargs["channel_id"] == "gowa"


# ── o envelope legado do bloqueio de 24h ────────────────────────────────────

def test_panel_send_keeps_the_reason_payload(admin_client):
    """``POST /api/contacts/{phone}/send`` bloqueado pela janela devolve
    ``data.reason`` — o compositor do painel LÊ essa chave para decidir se
    oferece o fluxo de template. O refactor R-txt quase a perdeu.
    """
    from app.services.messaging_service import MessagingService

    fake = AsyncMock(return_value={
        "ok": False, "status": 409, "reason": "session_window_closed",
        "message": "Fora da janela de 24h: só é possível enviar um template aprovado.",
        "data": {"reason": "session_window_closed"}})
    with patch.object(MessagingService, "send_text", fake):
        r = admin_client.post("/api/contacts/5511999990004/send",
                              json={"message": "oi"})
    assert r.status_code == 409
    body = r.json()
    assert body["ok"] is False
    assert body["data"]["reason"] == "session_window_closed"


def test_panel_send_error_without_extra_keeps_the_legacy_shape(admin_client):
    """Os demais erros NUNCA mandaram ``data`` — mandar agora mudaria a forma
    da resposta para clientes antigos."""
    from app.services.messaging_service import MessagingService

    fake = AsyncMock(return_value={
        "ok": False, "status": 400, "reason": "blocked_by_plugin",
        "message": "Mensagem bloqueada por plugin."})
    with patch.object(MessagingService, "send_text", fake):
        r = admin_client.post("/api/contacts/5511999990004/send",
                              json={"message": "oi"})
    assert r.status_code == 400
    assert "data" not in r.json()


# ── catálogo: edição de etiqueta de conversa e de atributo ──────────────────

def test_conversation_label_patch_renames_preserving_the_id(admin_client):
    """Renomear PRESERVA o id — é a diferença para apagar-e-recriar, que
    perderia o vínculo com as conversas já etiquetadas."""
    name = f"lbl-{uuid.uuid4().hex[:8]}"
    created = admin_client.post("/api/v1/conversation-labels",
                                json={"name": name, "color": "#111111"}).json()
    r = admin_client.patch(f"/api/v1/conversation-labels/{created['id']}",
                           json={"name": name + "-novo", "color": "#222222"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert body["name"] == name + "-novo"
    assert body["color"] == "#222222"
    admin_client.delete(f"/api/v1/conversation-labels/{created['id']}")


def test_conversation_label_patch_is_partial(admin_client):
    """Campo ausente fica intocado (semântica de PATCH, igual à de contatos)."""
    name = f"lbl-{uuid.uuid4().hex[:8]}"
    created = admin_client.post("/api/v1/conversation-labels",
                                json={"name": name, "color": "#333333"}).json()
    body = admin_client.patch(f"/api/v1/conversation-labels/{created['id']}",
                              json={"color": "#444444"}).json()
    assert body["name"] == name          # não foi enviado ⇒ preservado
    assert body["color"] == "#444444"
    admin_client.delete(f"/api/v1/conversation-labels/{created['id']}")


def test_conversation_label_patch_refuses_a_duplicate_name(admin_client):
    a = admin_client.post("/api/v1/conversation-labels",
                          json={"name": f"a-{uuid.uuid4().hex[:8]}"}).json()
    b = admin_client.post("/api/v1/conversation-labels",
                          json={"name": f"b-{uuid.uuid4().hex[:8]}"}).json()
    r = admin_client.patch(f"/api/v1/conversation-labels/{b['id']}",
                           json={"name": a["name"]})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate"
    admin_client.delete(f"/api/v1/conversation-labels/{a['id']}")
    admin_client.delete(f"/api/v1/conversation-labels/{b['id']}")


def test_conversation_label_patch_404_when_absent(admin_client):
    assert admin_client.patch("/api/v1/conversation-labels/999999",
                              json={"color": "#555555"}).status_code == 404


def test_conversation_label_writes_broadcast_the_registry(admin_client):
    """As paletas abertas no painel precisam do push — a rota do painel manda
    em toda escrita do registro, e a v1 tem de mandar também."""
    sent: list[str] = []

    async def _spy(event, payload=None):
        sent.append(event)

    label = None
    from server.state import ConnectionManager
    with patch.object(ConnectionManager, "broadcast", new=AsyncMock(side_effect=_spy)):
        label = admin_client.post("/api/v1/conversation-labels",
                                  json={"name": f"br-{uuid.uuid4().hex[:8]}"}).json()
        admin_client.patch(f"/api/v1/conversation-labels/{label['id']}",
                           json={"color": "#666666"})
        admin_client.delete(f"/api/v1/conversation-labels/{label['id']}")
    assert sent.count("conversation_labels_registry_changed") == 3


def test_custom_attribute_patch_edits_the_editable_fields(admin_client):
    key = f"attr_{uuid.uuid4().hex[:8]}"
    created = admin_client.post("/api/v1/custom-attributes", json={
        "attribute_key": key, "display_name": "Antigo",
        "type": "text", "applies_to": "conversation"}).json()
    r = admin_client.patch(f"/api/v1/custom-attributes/{created['id']}", json={
        "display_name": "Novo", "description": "descrição", "required": True,
        "filterable": True, "position": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "Novo"
    assert body["description"] == "descrição"
    assert body["required"] == 1 and body["filterable"] == 1
    assert body["position"] == 3
    # A identidade NÃO muda por aqui — é o que separa editar de recriar.
    assert body["attribute_key"] == key
    assert body["type"] == "text"
    assert body["applies_to"] == "conversation"
    admin_client.delete(f"/api/v1/custom-attributes/{created['id']}")


def test_custom_attribute_patch_ignores_identity_fields(admin_client):
    """Mandar ``attribute_key``/``type`` num atributo comum é IGNORADO (não 500,
    não renomeia): o repo só aceita os campos editáveis."""
    key = f"attr_{uuid.uuid4().hex[:8]}"
    created = admin_client.post("/api/v1/custom-attributes", json={
        "attribute_key": key, "display_name": "X", "type": "text",
        "applies_to": "contact"}).json()
    body = admin_client.patch(f"/api/v1/custom-attributes/{created['id']}", json={
        "attribute_key": "outra_chave", "type": "number",
        "applies_to": "conversation", "display_name": "Y"}).json()
    assert body["attribute_key"] == key
    assert body["type"] == "text"
    assert body["applies_to"] == "contact"
    assert body["display_name"] == "Y"
    admin_client.delete(f"/api/v1/custom-attributes/{created['id']}")


def test_custom_attribute_patch_validates_list_options(admin_client):
    key = f"attr_{uuid.uuid4().hex[:8]}"
    created = admin_client.post("/api/v1/custom-attributes", json={
        "attribute_key": key, "display_name": "Lista", "type": "list",
        "applies_to": "conversation", "options": ["a", "b"]}).json()
    ok = admin_client.patch(f"/api/v1/custom-attributes/{created['id']}",
                            json={"options": ["a", "b", "c"]})
    assert ok.status_code == 200 and ok.json()["options"] == ["a", "b", "c"]
    bad = admin_client.patch(f"/api/v1/custom-attributes/{created['id']}",
                             json={"options": []})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_field"
    admin_client.delete(f"/api/v1/custom-attributes/{created['id']}")


def test_custom_attribute_patch_rejects_empty_display_name(admin_client):
    key = f"attr_{uuid.uuid4().hex[:8]}"
    created = admin_client.post("/api/v1/custom-attributes", json={
        "attribute_key": key, "display_name": "X", "type": "text",
        "applies_to": "contact"}).json()
    r = admin_client.patch(f"/api/v1/custom-attributes/{created['id']}",
                           json={"display_name": "   "})
    assert r.status_code == 400
    admin_client.delete(f"/api/v1/custom-attributes/{created['id']}")


def test_custom_attribute_patch_404_when_absent(admin_client):
    assert admin_client.patch("/api/v1/custom-attributes/999999",
                              json={"display_name": "X"}).status_code == 404
