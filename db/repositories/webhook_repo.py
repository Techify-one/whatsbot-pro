"""Repositório dos webhooks de saída (fase 8 do plano de API).

Duas tabelas, dois papéis: ``webhook_endpoints`` é a CONFIGURAÇÃO (para onde
mandar, com que segredo, quais eventos) e ``webhook_deliveries`` é a FILA
DURÁVEL (uma linha por evento × endpoint, com tentativa e backoff).

A fila mora no banco de propósito: um toggle de plugin derruba o processo, e uma
entrega pendente em memória morreria junto — exatamente o que a fase 8 do plano
proíbe.
"""

from __future__ import annotations

import time

from sqlalchemy import and_, delete as sa_delete, insert, or_, select, update

from db.engine import get_engine
from db.tables import webhook_deliveries, webhook_endpoints

# Backoff das re-tentativas, em segundos. Depois da última, a entrega vira
# ``dead`` (dead-letter) e fica na tabela para diagnóstico — nunca some calada.
RETRY_BACKOFF = (30, 120, 600, 3600, 21600)
MAX_ATTEMPTS = len(RETRY_BACKOFF) + 1

# Falhas consecutivas que desligam o endpoint automaticamente. Um endpoint morto
# não pode ficar consumindo worker e enchendo a fila para sempre.
AUTO_DISABLE_STREAK = 20


# ── Endpoints ───────────────────────────────────────────────────────────────

def create_endpoint(*, url: str, secret: str, events: list[str],
                    description: str = "", created_by: int | None = None) -> dict:
    now = time.time()
    with get_engine().begin() as conn:
        result = conn.execute(insert(webhook_endpoints).values(
            url=url, secret=secret, events=list(events), description=description,
            enabled=1, created_at=now, updated_at=now, created_by=created_by,
            failure_streak=0))
        new_id = result.inserted_primary_key[0]
        row = conn.execute(select(webhook_endpoints)
                           .where(webhook_endpoints.c.id == new_id)).mappings().first()
    return dict(row)


def get_endpoint(endpoint_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(webhook_endpoints)
                           .where(webhook_endpoints.c.id == endpoint_id)).mappings().first()
    return dict(row) if row else None


def list_endpoints(*, only_enabled: bool = False) -> list[dict]:
    stmt = select(webhook_endpoints)
    if only_enabled:
        stmt = stmt.where(webhook_endpoints.c.enabled == 1)
    stmt = stmt.order_by(webhook_endpoints.c.id)
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def update_endpoint(endpoint_id: int, **fields) -> dict | None:
    if not fields:
        return get_endpoint(endpoint_id)
    fields["updated_at"] = time.time()
    with get_engine().begin() as conn:
        conn.execute(update(webhook_endpoints)
                     .where(webhook_endpoints.c.id == endpoint_id).values(**fields))
    return get_endpoint(endpoint_id)


def delete_endpoint(endpoint_id: int) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(webhook_endpoints)
                              .where(webhook_endpoints.c.id == endpoint_id))
    return bool(result.rowcount)


# ── Fila de entregas ────────────────────────────────────────────────────────

def enqueue(endpoint_id: int, event: str, payload: dict) -> int:
    """Enfileira UMA entrega, pronta para sair agora."""
    now = time.time()
    with get_engine().begin() as conn:
        result = conn.execute(insert(webhook_deliveries).values(
            endpoint_id=endpoint_id, event=event, payload=payload,
            status="pending", attempts=0, next_attempt_at=now,
            created_at=now, updated_at=now))
    return result.inserted_primary_key[0]


def due_deliveries(limit: int = 50) -> list[dict]:
    """Entregas prontas para tentar (pendentes ou re-agendadas cujo prazo venceu)."""
    now = time.time()
    stmt = (select(webhook_deliveries)
            .where(and_(
                or_(webhook_deliveries.c.status == "pending",
                    webhook_deliveries.c.status == "failed"),
                webhook_deliveries.c.next_attempt_at <= now))
            .order_by(webhook_deliveries.c.next_attempt_at)
            .limit(limit))
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def mark_delivered(delivery_id: int, response_status: int | None) -> None:
    now = time.time()
    with get_engine().begin() as conn:
        conn.execute(update(webhook_deliveries)
                     .where(webhook_deliveries.c.id == delivery_id)
                     .values(status="delivered", response_status=response_status,
                             last_error=None, updated_at=now,
                             attempts=webhook_deliveries.c.attempts + 1))


def mark_failed(delivery_id: int, attempts: int, *, error: str,
                response_status: int | None = None) -> str:
    """Re-agenda com backoff, ou manda para a dead-letter. Devolve o novo status."""
    now = time.time()
    next_attempt = attempts  # já é o número de tentativas JÁ feitas + esta
    if next_attempt >= MAX_ATTEMPTS:
        status, delay = "dead", 0.0
    else:
        status = "failed"
        delay = RETRY_BACKOFF[min(next_attempt - 1, len(RETRY_BACKOFF) - 1)]
    with get_engine().begin() as conn:
        conn.execute(update(webhook_deliveries)
                     .where(webhook_deliveries.c.id == delivery_id)
                     .values(status=status, attempts=next_attempt,
                             next_attempt_at=now + delay,
                             response_status=response_status,
                             last_error=(error or "")[:500], updated_at=now))
    return status


def record_endpoint_result(endpoint_id: int, *, ok: bool,
                           response_status: int | None) -> int:
    """Atualiza o placar do endpoint. Devolve a sequência de falhas resultante."""
    now = time.time()
    with get_engine().begin() as conn:
        if ok:
            conn.execute(update(webhook_endpoints)
                         .where(webhook_endpoints.c.id == endpoint_id)
                         .values(last_delivery_at=now, last_status=response_status,
                                 failure_streak=0, updated_at=now))
            return 0
        row = conn.execute(select(webhook_endpoints.c.failure_streak)
                           .where(webhook_endpoints.c.id == endpoint_id)).scalar()
        streak = int(row or 0) + 1
        values = {"last_delivery_at": now, "last_status": response_status,
                  "failure_streak": streak, "updated_at": now}
        if streak >= AUTO_DISABLE_STREAK:
            values["enabled"] = 0
            values["disabled_reason"] = (
                f"Desligado automaticamente após {streak} falhas consecutivas.")
        conn.execute(update(webhook_endpoints)
                     .where(webhook_endpoints.c.id == endpoint_id).values(**values))
        return streak


def list_deliveries(endpoint_id: int | None = None, *, limit: int = 50,
                    status: str | None = None) -> list[dict]:
    stmt = select(webhook_deliveries)
    if endpoint_id is not None:
        stmt = stmt.where(webhook_deliveries.c.endpoint_id == endpoint_id)
    if status:
        stmt = stmt.where(webhook_deliveries.c.status == status)
    stmt = stmt.order_by(webhook_deliveries.c.id.desc()).limit(limit)
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def purge_delivered(older_than_epoch: float) -> int:
    """Limpa entregas ENTREGUES antigas. ``dead`` nunca é apagada aqui — ela é o
    registro de que algo não chegou, e some só por decisão explícita."""
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(webhook_deliveries).where(and_(
            webhook_deliveries.c.status == "delivered",
            webhook_deliveries.c.updated_at < older_than_epoch)))
    return result.rowcount or 0
