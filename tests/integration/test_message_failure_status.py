"""Plano 75 F4 — ``message_repo.mark_failed_by_msg_id``.

Trava o contrato da função nova (que NÃO pode ser o ``update_status_by_msg_id``
monotônico: aquele filtra ``status IN ('sent','delivered')`` e um template/envio do
operador nasce ``'operator'``, então reusá-lo seria um no-op silencioso):

- ``operator`` → ``failed`` (transiciona, devolve a row)
- ``sent`` → ``failed`` (transiciona)
- ``read`` → ``failed`` (NO-OP: já foi lida, logo não pode ter falhado)
- msg_id inexistente → ``None``
- 2ª chamada seguida → ``None`` (idempotente ⇒ a F6 não gera um 2º card de erro)
- sem cascata: a mensagem anterior do mesmo contato não é marcada junto

    venv/bin/python -m pytest tests/integration/test_message_failure_status.py -q
"""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select

from db.engine import get_engine
from db.repositories import contact_repo, contact_inbox_repo, conversation_repo, message_repo
from db.tables import messages

INBOX_ID = 1  # seeded by migration 0013


def _unique_msg_id(prefix: str) -> str:
    return f"p75_{prefix}_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def thread(_engine_ready):
    """Contato + conversa abertos para as mensagens de saída do teste.

    O banco é global ao processo, então cada chamada usa um telefone novo — assim
    o índice parcial ``uq_atend_open_contact_inbox`` nunca colide entre testes.
    """
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"])
    return contact, conv


def _add_outgoing(thread, status: str, *, content: str = "Olá", msg_id: str | None = None,
                  ts: float | None = None) -> str:
    contact, conv = thread
    mid = msg_id or _unique_msg_id(status)
    message_repo.add(contact["id"], "assistant", content, status=status, msg_id=mid,
                     conversation_id=conv["id"], ts=ts or time.time())
    return mid


def _status_of(msg_id: str) -> str | None:
    with get_engine().connect() as conn:
        return conn.execute(
            select(messages.c.status).where(messages.c.msg_id == msg_id)
        ).scalar_one()


# ---------------------------------------------------------------- transições

def test_operator_becomes_failed(thread):
    """O caso que motivou a função: envio do painel/template nasce 'operator'."""
    contact, conv = thread
    mid = _add_outgoing(thread, "operator", content="Segue o template")

    row = message_repo.mark_failed_by_msg_id(mid)

    assert row is not None
    assert row["contact_id"] == contact["id"]
    assert row["conversation_id"] == conv["id"]   # F6 grava o card nesta thread
    assert row["content"] == "Segue o template"
    assert row["msg_id"] == mid
    assert isinstance(row["id"], int)
    assert _status_of(mid) == "failed"


def test_sent_becomes_failed(thread):
    mid = _add_outgoing(thread, "sent")

    assert message_repo.mark_failed_by_msg_id(mid) is not None
    assert _status_of(mid) == "failed"


def test_delivered_becomes_failed(thread):
    mid = _add_outgoing(thread, "delivered")

    assert message_repo.mark_failed_by_msg_id(mid) is not None
    assert _status_of(mid) == "failed"


# ---------------------------------------------------------------- no-ops

def test_read_is_never_overwritten(thread):
    """Já foi lida pelo destinatário ⇒ não pode ter falhado."""
    mid = _add_outgoing(thread, "read")

    assert message_repo.mark_failed_by_msg_id(mid) is None
    assert _status_of(mid) == "read"


def test_unknown_msg_id_returns_none(_engine_ready):
    assert message_repo.mark_failed_by_msg_id(_unique_msg_id("ghost")) is None
    assert message_repo.mark_failed_by_msg_id("") is None


def test_second_call_is_idempotent(thread):
    """A 2ª chamada devolve None — é o guard de dedup do card de erro (F6)."""
    mid = _add_outgoing(thread, "sent")

    first = message_repo.mark_failed_by_msg_id(mid)
    second = message_repo.mark_failed_by_msg_id(mid)

    assert first is not None
    assert second is None
    assert _status_of(mid) == "failed"


# ---------------------------------------------------------------- sem cascata

def test_does_not_cascade_to_prior_messages(thread):
    """Ack é monotônico e cascateia; falha é de UMA mensagem específica."""
    now = time.time()
    older = _add_outgoing(thread, "sent", content="anterior", ts=now - 60)
    target = _add_outgoing(thread, "sent", content="alvo", ts=now)

    assert message_repo.mark_failed_by_msg_id(target) is not None
    assert _status_of(target) == "failed"
    assert _status_of(older) == "sent"
