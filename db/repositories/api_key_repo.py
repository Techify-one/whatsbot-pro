"""Repositório das chaves de API (plano "Sistema de API com chave por usuário").

Espelha ``session_repo`` (SQLAlchemy Core, síncrono, chamado via
``asyncio.to_thread`` nas rotas). O SEGREDO NUNCA passa por aqui em claro: quem
gera/verifica o hash é ``server.api_keys``; este módulo só persiste ``key_hash``,
``prefix`` e ``last4``.

Revogação é SOFT (``revoked_at``) de propósito: a linha continua existindo para
que a auditoria possa resolver ``audit_log.api_key_id`` → rótulo da chave depois
de revogada.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select, update

from db.engine import get_engine
from db.tables import api_keys


def create(*, user_id: int, label: str, key_hash: str, prefix: str,
           last4: str = "", expires_at: float | None = None,
           created_by: int | None = None) -> dict:
    """Insere a chave e devolve a linha criada (sem segredo — ele não vem parar aqui)."""
    now = time.time()
    with get_engine().begin() as conn:
        result = conn.execute(insert(api_keys).values(
            user_id=user_id, label=(label or "")[:120], key_hash=key_hash,
            prefix=prefix, last4=last4, scopes=None, created_at=now,
            last_used_at=None, expires_at=expires_at, revoked_at=None,
            created_by=created_by,
        ))
        new_id = result.inserted_primary_key[0]
        row = conn.execute(select(api_keys).where(api_keys.c.id == new_id)).mappings().first()
    return dict(row)


def get(key_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(api_keys).where(api_keys.c.id == key_id)).mappings().first()
    return dict(row) if row else None


def get_by_prefix(prefix: str) -> dict | None:
    """Linha cujo ``prefix`` bate (o prefixo é único na prática — 12 bytes aleatórios).

    NÃO filtra revogada/expirada: quem decide é ``server.api_keys.resolve_api_key``,
    para que a razão da recusa fique num lugar só e testável.
    """
    if not prefix:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(
            select(api_keys).where(api_keys.c.prefix == prefix)
            .order_by(api_keys.c.id.desc())
        ).mappings().first()
    return dict(row) if row else None


def list_for_user(user_id: int, *, include_revoked: bool = True) -> list[dict]:
    stmt = select(api_keys).where(api_keys.c.user_id == user_id)
    if not include_revoked:
        stmt = stmt.where(api_keys.c.revoked_at.is_(None))
    stmt = stmt.order_by(api_keys.c.created_at.desc())
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_all(*, include_revoked: bool = True) -> list[dict]:
    stmt = select(api_keys)
    if not include_revoked:
        stmt = stmt.where(api_keys.c.revoked_at.is_(None))
    stmt = stmt.order_by(api_keys.c.created_at.desc())
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def touch_last_used(key_id: int) -> None:
    """Best-effort: uma falha aqui nunca pode derrubar a request autenticada."""
    try:
        with get_engine().begin() as conn:
            conn.execute(update(api_keys).where(api_keys.c.id == key_id)
                         .values(last_used_at=time.time()))
    except Exception:  # noqa: BLE001 — telemetria, não autenticação
        pass


def revoke(key_id: int) -> bool:
    """Marca ``revoked_at`` (soft). ``False`` se já estava revogada ou não existe."""
    with get_engine().begin() as conn:
        result = conn.execute(
            update(api_keys)
            .where(api_keys.c.id == key_id, api_keys.c.revoked_at.is_(None))
            .values(revoked_at=time.time()))
    return bool(result.rowcount)


def purge_expired(older_than_days: int = 90) -> int:
    """Apaga DE VEZ chaves revogadas/expiradas há mais de ``older_than_days``.

    Só toca no que já está morto — uma chave viva nunca é removida. A janela
    existe para a auditoria conseguir resolver o rótulo por um tempo.
    """
    cutoff = time.time() - older_than_days * 86400
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(api_keys).where(
            ((api_keys.c.revoked_at.isnot(None)) & (api_keys.c.revoked_at < cutoff))
            | ((api_keys.c.expires_at.isnot(None)) & (api_keys.c.expires_at < cutoff))
        ))
    return result.rowcount or 0
