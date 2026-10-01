"""Plano 175 — contrato: toda mídia ao vivo termina com ALGUMA cópia com `_id`.

Desde `4245ac5f` o painel só renderiza mídia buscando `/api/messages/{id}/media`
(`web/static/js/components/contacts/MediaContent.js`), e o `{id}` é o `_id` da
bolha. Bolha sem `_id` ⇒ "Mídia indisponível" até o F5. O defeito apareceu duas
vezes por falta desta regra (plano 172: operador; plano 175: cliente + eco).

A regra, por produtor de `new_message` com `media_type`:

* **inbound do cliente** — o 1º evento (t=0) sai ANTES do INSERT e **não tem
  `_id` por construção** (reabrir isso reabre a janela broadcast-antes-do-save do
  plano 57; ver `test_batch_message_identity.py`). Logo precisa vir um
  autoritativo pós-save com `_id`, e o frontend o adota pelo `msg_id`
  (`reconcileByMsgId`, `services/messages.js`);
* **eco do celular do atendente** — NÃO há autoritativo depois: a única cópia
  precisa levar o `_id`.

Este arquivo prova o lado do BACKEND (existe uma cópia com `_id`, e é o da linha
salva). Quem ADOTA o `_id` é o frontend, provado por `node --test
web/static/js/services/messages.test.js` — os dois juntos fecham a cadeia.

⚠️ Produtor novo de `new_message` com mídia (provider, plugin, rota) tem de levar
`_id` OU ser seguido de um autoritativo; se este teste ficar vermelho, o conserto
é no produtor, **nunca** relaxar a asserção.

Exceção conhecida e adiada (P3 do plano 175): o sandbox
(`server/routes/sandbox.py`) emite sem `msg_id` e sem `_id`; a página dele não usa
`MediaContent`.

    venv/bin/python -m pytest tests/contracts/test_media_broadcast_identity_contract.py -v
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from db.repositories import contact_repo, message_repo

# kind do payload do GOWA → arquivo qualquer (o parser só lê `path`).
KINDS = [
    ("image", "statics/media/c.jpg"),
    ("audio", "statics/media/c.oga"),
    ("video", "statics/media/c.mp4"),
    ("sticker", "statics/media/c.webp"),
    ("document", "statics/media/c.pdf"),
]


def _new_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _drain(built, phone: str, channel_id: str = "default") -> None:
    async def _run():
        for _ in range(8):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=15.0)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_run)


def _post(built, phone: str, msg_id: str, kind: str, path: str, *, from_me: bool):
    payload = {
        "from": f"{phone}@s.whatsapp.net", "id": msg_id, "from_name": "Contato",
        kind: {"path": path},
    }
    if from_me:
        payload["is_from_me"] = True
    r = built.client.post("/api/webhook/gowa/default",
                          json={"event": "message", "payload": payload})
    assert r.status_code == 200, r.text


def _run_and_capture(built, phone: str, msg_id: str, kind: str, path: str, *,
                     from_me: bool) -> list[dict]:
    """Emite o webhook e devolve TODAS as cópias `new_message` desse `msg_id`."""
    ws = built.app.state.deps.ws_manager
    seen: list[tuple[str, dict]] = []
    original = ws.broadcast

    async def _spy(event, data=None):
        seen.append((event, data or {}))
        return await original(event, data)

    ws.broadcast = _spy
    try:
        _post(built, phone, msg_id, kind, path, from_me=from_me)
        _drain(built, phone)
    finally:
        ws.broadcast = original
    return [d["message"] for ev, d in seen
            if ev == "new_message"
            and (d.get("message") or {}).get("msg_id") == msg_id]


def _saved_row(phone: str, msg_id: str) -> dict:
    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "o webhook precisa ter materializado o contato"
    rows = [m for m in message_repo.get_all(contact["id"]) if m.get("msg_id") == msg_id]
    assert len(rows) == 1, f"uma linha por mensagem; obtido={len(rows)}"
    return rows[0]


def _build(build_app):
    return build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})


@pytest.mark.parametrize("kind,path", KINDS)
def test_inbound_do_cliente_termina_com_uma_copia_com_id(build_app, kind, path):
    built = _build(build_app)
    phone = _new_phone()
    msg_id = f"in_{kind}_{uuid.uuid4().hex[:10]}"

    copies = _run_and_capture(built, phone, msg_id, kind, path, from_me=False)

    assert copies, f"nenhum new_message de {kind} foi emitido"
    assert all(c.get("media_type") == kind for c in copies if c.get("media_type"))
    with_id = [c for c in copies if c.get("_id") is not None]
    assert with_id, (
        f"nenhuma cópia do new_message de {kind} do cliente leva `_id`: sem o "
        "autoritativo pós-save a bolha nunca ganha identidade e fica em 'Mídia "
        "indisponível' até o F5 — produtor: "
        "app/services/messaging_service.py (save do batch) + realtime_broadcast.py "
        "(plano 175)")
    assert {c["_id"] for c in with_id} == {_saved_row(phone, msg_id)["_id"]}, (
        "o `_id` emitido tem de ser o da MESMA linha salva")
    # Pareamento que o frontend usa para adotar o `_id`: tudo pelo mesmo msg_id.
    assert all(c["msg_id"] == msg_id for c in copies)


@pytest.mark.parametrize("kind,path", KINDS)
def test_eco_do_celular_termina_com_uma_copia_com_id(build_app, kind, path):
    built = _build(build_app)
    phone = _new_phone()
    msg_id = f"eco_{kind}_{uuid.uuid4().hex[:10]}"

    copies = _run_and_capture(built, phone, msg_id, kind, path, from_me=True)

    assert copies, f"nenhum new_message de eco de {kind} foi emitido"
    with_id = [c for c in copies if c.get("_id") is not None]
    assert with_id, (
        f"o eco de {kind} não leva `_id` e NÃO há autoritativo depois — produtor: "
        "app/services/message_ingest_service.py `_ingest_echo` (plano 175)")
    assert {c["_id"] for c in with_id} == {_saved_row(phone, msg_id)["_id"]}
