"""Contrato do payload de ``message.persisted`` (plano 174 F4).

O plugin ``fechamento_regex`` depende deste sinal: o payload NÃO traz o texto da
mensagem, então o plugin relê a linha por ``(conversation_id, role, ts)``. Isso só
funciona enquanto (a) as chaves forem exatamente estas e (b) o ``ts`` do payload
for IGUAL ao da linha gravada. Se um dos dois mudar no core, o plugin para de achar
a mensagem — e falha de forma silenciosa (fail-safe: não fecha nada).

    venv/bin/python -m pytest tests/contracts/test_message_persisted_payload.py -q
"""

from __future__ import annotations

import time

import pytest

EXPECTED_KEYS = {"conversation_id", "contact_id", "role", "msg_id", "ts"}

@pytest.fixture
def emitted(monkeypatch, _engine_ready):
    """Captura o que ``ContactMemory`` emite em ``plugins.events.emit``."""
    from plugins import events

    calls: list[tuple[str, dict]] = []
    original = events.emit

    def _spy(name, payload=None, *a, **k):
        if name == "message.persisted":
            calls.append((name, dict(payload)))
        return original(name, payload, *a, **k)

    monkeypatch.setattr(events, "emit", _spy)
    return calls

def _contact():
    from agent.memory import ContactMemory

    return ContactMemory(f"5511988{int(time.time() * 1000) % 10**7:07d}")

def _row_ts(message_pk: int) -> float:
    from sqlalchemy import text

    from db.engine import get_engine

    with get_engine().connect() as conn:
        return conn.execute(text("SELECT ts FROM messages WHERE id = :i"),
                            {"i": message_pk}).scalar()

@pytest.mark.parametrize("role,kwargs", [
    ("user", {"msg_id": "wamid.P1"}),
    ("assistant", {"msg_id": "wamid.P2", "status": "sent"}),
    ("assistant", {"msg_id": "wamid.P3", "status": "sent", "sent_by_name": "Criar Conta"}),
])
def test_payload_tem_exatamente_as_chaves_do_contrato(emitted, role, kwargs):
    contact = _contact()
    saved = contact.add_message(role, "texto", **kwargs)

    assert len(emitted) == 1
    payload = emitted[0][1]
    assert set(payload) == EXPECTED_KEYS
    assert payload["role"] == role
    assert payload["contact_id"] == contact.id
    assert payload["conversation_id"] == saved["conversation_id"]
    assert payload["msg_id"] == kwargs["msg_id"]

@pytest.mark.parametrize("role", ["user", "assistant"])
def test_ts_do_payload_e_igual_ao_da_linha_gravada(emitted, role):
    """A igualdade exata é o que permite ao plugin achar a linha sem o texto."""
    contact = _contact()
    saved = contact.add_message(role, "texto", msg_id=f"wamid.T-{role}")

    payload = emitted[0][1]
    assert payload["ts"] == saved["ts"]
    assert payload["ts"] == _row_ts(saved["id"])

def test_papel_de_painel_tambem_dispara(emitted):
    """Quem assina precisa filtrar por ``role`` — nota privada também emite."""
    contact = _contact()
    contact.add_message("user", "oi")
    contact.add_message("private_note", "anotação interna")

    assert [p["role"] for _n, p in emitted] == ["user", "private_note"]
