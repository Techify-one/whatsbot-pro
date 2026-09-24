"""Plano 172 — o `new_message` de mídia do operador nasce sem `_id`.

`MessagingService.send_media` ([app/services/messaging_service.py:638](../../app/services/messaging_service.py))
monta o broadcast ao vivo com `conversation_id` (lido de `_saved`) mas nunca lê
`_saved.get("id")` — ao contrário do card de "Transcrição privada" emitido pela
MESMA função (`:678`) e da nota privada de mídia (`server/routes/contacts.py`),
que incluem `_id` desde o plano 53.

Sem `_id`, quando a reconciliação otimista do painel falha em casar a bolha
local com o eco do servidor (heurística de 30s, `docs/UI_CONVERSA.md:143`), a
cópia que sobra é anexada como mensagem nova SEM identidade — `MediaContent.js`
não consegue buscar `/api/messages/{id}/media` e marca "Mídia indisponível"
para sempre, sem retry. Um F5 conserta porque a releitura usa
`message_repo._row_to_dict`, que sempre inclui `_id`.

Este teste NÃO simula a janela de 30s (isso é heurística de frontend, puro
`services/messages.js`) — ele trava o formato do payload que o backend emite:
o `_id` tem de estar lá, sempre, para as 4 rotas de mídia do operador, seja a
reconciliação bem-sucedida ou não.

    venv/bin/python -m pytest tests/integration/test_operator_media_broadcast_identity.py -q
"""

from __future__ import annotations

import io

import pytest

from db.repositories import contact_repo, message_repo


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
OGG = b"OggS" + b"\x00" * 200
PDF = b"%PDF-1.4\n" + b"\x00" * 200
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200


# ── helpers (duplicados de propósito — ver docstring do plano 172 F1: manter
# este arquivo independente de tests/integration/characterization/) ─────────

def _capture_broadcasts(ws_manager, events: list):
    async def _capture(event, data):
        events.append((event, data))
    original = ws_manager.broadcast
    ws_manager.broadcast = _capture
    return original


def _media_rows(phone: str) -> list[dict]:
    contact = contact_repo.get_by_phone(phone)
    if contact is None:
        return []
    return [m for m in message_repo.get_all(contact["id"]) if m.get("media_type")]


def _post(built, phone: str, route: str, field: str, name: str, blob: bytes,
          mime: str, **data):
    return built.client.post(
        f"/api/contacts/{phone}/{route}",
        files={field: (name, io.BytesIO(blob), mime)}, data=data)


def _send_and_capture(built, phone: str, route: str, field: str, name: str,
                       blob: bytes, mime: str, **data):
    """POST numa rota de mídia capturando os broadcasts do `ws_manager`.

    Devolve `(response, events)`. Os eventos capturados incluem também o card
    de transcrição, se a direção "Enviadas" estiver ligada no canal (não está,
    por padrão, nestes testes).
    """
    deps = built.app.state.deps
    events: list = []
    original = _capture_broadcasts(deps.ws_manager, events)
    try:
        r = _post(built, phone, route, field, name, blob, mime, **data)
    finally:
        deps.ws_manager.broadcast = original
    return r, events


def _new_message_payloads(events: list, media_type: str) -> list[dict]:
    return [d["message"] for e, d in events
            if e == "new_message" and (d.get("message") or {}).get("media_type") == media_type]


# ── as 4 rotas: `_id` presente e igual ao da linha realmente salva ─────────

@pytest.mark.parametrize("route,field,name,blob,mime,kind", [
    ("send-image", "image", "foto.png", PNG, "image/png", "image"),
    ("send-audio", "audio", "voz.ogg", OGG, "audio/ogg", "audio"),
    ("send-document", "document", "contrato.pdf", PDF, "application/pdf", "document"),
    ("send-video", "video", "clipe.mp4", MP4, "video/mp4", "video"),
])
def test_broadcast_de_midia_do_operador_carrega_o_id_da_linha_salva(
        build_app, route, field, name, blob, mime, kind):
    phone = f"55119701{hash(route) % 100000:05d}"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})

    r, events = _send_and_capture(built, phone, route, field, name, blob, mime,
                                   caption="legenda de teste")

    assert r.status_code == 200, r.text

    msgs = _new_message_payloads(events, kind)
    assert len(msgs) == 1, f"esperava 1 broadcast new_message de {kind}, veio {len(msgs)}"
    msg = msgs[0]

    saved_rows = _media_rows(phone)
    assert len(saved_rows) == 1
    saved = saved_rows[0]

    assert "_id" in msg and msg["_id"] is not None, (
        f"new_message de {kind} nasceu sem _id — a cópia que a reconciliação "
        f"otimista não conseguir casar fica presa em 'Mídia indisponível' até o F5"
    )
    assert msg["_id"] == saved["_id"], (
        "o _id do broadcast tem de ser o da MESMA linha salva no banco, não "
        "um id qualquer"
    )
