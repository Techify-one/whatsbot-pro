"""Plano 175 — o `new_message` do ECO de mídia (celular do atendente) nasce sem `_id`.

`message_ingest_service._ingest_echo` salva a linha (`_saved`) e emite o
`new_message` ao vivo, mas o broadcast levava só `conversation_id` e NUNCA lia
`_saved.get("id")`. Diferente do inbound do cliente, o eco não tem um segundo
evento autoritativo pós-save que cure a bolha: a cópia sem `_id` é a única que o
painel recebe.

Sem `_id`, `MediaContent.js` não consegue buscar `/api/messages/{id}/media` e a
bolha fica em "Mídia indisponível" até o F5 (a releitura usa
`message_repo._row_to_dict`, que sempre traz `_id`). Mesmo defeito do plano 172,
só que num produtor que aquele plano não cobriu.

Complementa `test_operator_media_broadcast_identity.py` (operador pelo painel) —
este cobre o eco do celular.

    venv/bin/python -m pytest tests/integration/test_echo_media_broadcast_identity.py -v
"""

from __future__ import annotations

import uuid

import pytest

from db.repositories import contact_repo, message_repo


def _new_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _capture_broadcasts(ws_manager, events: list):
    async def _capture(event, data=None):
        events.append((event, data or {}))
    original = ws_manager.broadcast
    ws_manager.broadcast = _capture
    return original


def _echo_media(built, phone: str, msg_id: str, kind: str, path: str):
    return built.client.post("/api/webhook/gowa/default", json={
        "event": "message", "payload": {
            "from": f"{phone}@s.whatsapp.net", "id": msg_id,
            "is_from_me": True, "from_name": "Eu",
            kind: {"path": path}}})


@pytest.mark.parametrize("kind,path", [
    ("image", "statics/media/eco.jpg"),
    ("audio", "statics/media/eco.oga"),
    ("video", "statics/media/eco.mp4"),
    ("document", "statics/media/eco.pdf"),
])
def test_eco_de_midia_carrega_o_id_da_linha_salva(build_app, kind, path):
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    phone = _new_phone()
    msg_id = f"eco_{kind}_{uuid.uuid4().hex[:10]}"

    events: list = []
    original = _capture_broadcasts(built.app.state.deps.ws_manager, events)
    try:
        r = _echo_media(built, phone, msg_id, kind, path)
    finally:
        built.app.state.deps.ws_manager.broadcast = original
    assert r.status_code == 200, r.text

    copies = [d["message"] for e, d in events
              if e == "new_message" and (d.get("message") or {}).get("msg_id") == msg_id]
    assert len(copies) == 1, (
        f"o eco emite exatamente UM new_message por mídia; obtido={len(copies)}")
    msg = copies[0]
    assert msg.get("media_type") == kind

    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "o eco precisa ter materializado o contato"
    rows = [m for m in message_repo.get_all(contact["id"]) if m.get("msg_id") == msg_id]
    assert len(rows) == 1, "o eco grava uma linha"

    assert msg.get("_id") is not None, (
        f"new_message do eco de {kind} nasceu sem _id e NÃO há autoritativo para "
        "curá-lo — a bolha fica em 'Mídia indisponível' até o F5 (plano 175)")
    assert msg["_id"] == rows[0]["_id"], (
        "o _id do broadcast tem de ser o da MESMA linha salva, não um id qualquer")
