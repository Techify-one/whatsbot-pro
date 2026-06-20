"""Append-only audit trail repository (plano 07).

Core SQLAlchemy, same pattern as the other repos. INTENTIONALLY exposes no
``update``/``delete`` by id — the trail is immutable at the app layer. The ONLY
deletion is ``purge()`` (retention policy). ``before``/``after`` are sanitised
(secrets denylisted) BEFORE serialisation, never on read.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import and_, delete as sa_delete, func, insert as sa_insert, select

from db.engine import get_engine
from db.tables import audit_log

# Keys whose values are replaced by "***" before persisting (recursive). Names
# confirmed as real secrets in the project (config/settings.py, plano 03).
_SECRET_KEYS = frozenset({
    "openrouter_api_key", "access_token", "api_key", "apikey", "password",
    "password_hash", "token", "secret", "credentials", "authorization",
    "verify_token", "client_secret", "private_key",
})
_REDACTED = "***"


def _sanitize(data):
    """Recursively replace secret values with '***'. Returns a new structure."""
    if isinstance(data, dict):
        return {
            k: (_REDACTED if isinstance(k, str) and k.lower() in _SECRET_KEYS
                else _sanitize(v))
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [_sanitize(v) for v in data]
    return data


def _dump(data) -> str | None:
    if data is None:
        return None
    return json.dumps(_sanitize(data), ensure_ascii=False, default=str)


def add(*, actor_user_id: int | None = None, actor_type: str = "system",
        actor_label: str | None = None, action: str, resource_type: str,
        resource_id=None, before=None, after=None, ip_address: str | None = None,
        request_id: str | None = None, created_at: float | None = None) -> int:
    """Insert an audit row (the only write). Returns the inserted id.

    ``before``/``after`` are sanitised + JSON-encoded here. ``resource_id`` is
    coerced to str (covers phone/jid/uuid/int).
    """
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(audit_log).values(
            actor_user_id=actor_user_id,
            actor_type=actor_type or "system",
            actor_label=actor_label,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            before_json=_dump(before),
            after_json=_dump(after),
            ip_address=ip_address,
            request_id=request_id,
            created_at=created_at if created_at is not None else time.time(),
        ))
        return result.inserted_primary_key[0]


def _filters(*, actor_user_id=None, actor_type=None, resource_type=None,
             resource_id=None, action=None, ts_from=None, ts_to=None):
    conds = []
    if actor_user_id is not None:
        conds.append(audit_log.c.actor_user_id == actor_user_id)
    if actor_type:
        conds.append(audit_log.c.actor_type == actor_type)
    if resource_type:
        conds.append(audit_log.c.resource_type == resource_type)
    if resource_id is not None:
        conds.append(audit_log.c.resource_id == str(resource_id))
    if action:
        conds.append(audit_log.c.action == action)
    if ts_from is not None:
        conds.append(audit_log.c.created_at >= ts_from)
    if ts_to is not None:
        conds.append(audit_log.c.created_at <= ts_to)
    return conds


def query(*, actor_user_id=None, actor_type=None, resource_type=None,
          resource_id=None, action=None, ts_from=None, ts_to=None,
          limit: int = 50, offset: int = 0) -> list[dict]:
    """Filtered, newest-first page of audit rows."""
    conds = _filters(actor_user_id=actor_user_id, actor_type=actor_type,
                     resource_type=resource_type, resource_id=resource_id,
                     action=action, ts_from=ts_from, ts_to=ts_to)
    stmt = select(audit_log)
    if conds:
        stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(audit_log.c.created_at.desc()).limit(limit).offset(offset)
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def count(*, actor_user_id=None, actor_type=None, resource_type=None,
          resource_id=None, action=None, ts_from=None, ts_to=None) -> int:
    conds = _filters(actor_user_id=actor_user_id, actor_type=actor_type,
                     resource_type=resource_type, resource_id=resource_id,
                     action=action, ts_from=ts_from, ts_to=ts_to)
    stmt = select(func.count()).select_from(audit_log)
    if conds:
        stmt = stmt.where(and_(*conds))
    with get_engine().connect() as conn:
        return int(conn.execute(stmt).scalar() or 0)


def distinct_actions() -> dict:
    """Distinct ``action`` and ``resource_type`` values (to populate filter selects)."""
    with get_engine().connect() as conn:
        actions = [r[0] for r in conn.execute(
            select(audit_log.c.action).distinct().order_by(audit_log.c.action))]
        rtypes = [r[0] for r in conn.execute(
            select(audit_log.c.resource_type).distinct().order_by(audit_log.c.resource_type))]
    return {"actions": actions, "resource_types": rtypes}


def purge(older_than_epoch: float) -> int:
    """Delete rows older than the cutoff (retention policy — the ONLY deletion).

    There is deliberately no update()/delete-by-id: the trail is append-only.
    """
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_delete(audit_log).where(audit_log.c.created_at < older_than_epoch))
        return result.rowcount or 0
