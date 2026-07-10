"""Repository for the ``tool_overrides`` table.

Stores per-tool overrides (enabled flag, description, display_label) keyed by
the canonical tool ``name``. Rows are created eagerly when a tool is first
registered (via ``ensure``), so the table is the authoritative source for the
Tools management UI.

``description=NULL`` means "use the schema default from code". Reset is done by
sending ``description=null`` in the PUT — the repo ``upsert`` distinguishes
"do not touch" from "set to NULL" via a sentinel.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import tool_overrides
from db.upsert import upsert as upsert_stmt


_UNSET = object()


def get(name: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(tool_overrides).where(tool_overrides.c.name == name)
        ).mappings().first()
    return dict(row) if row else None


def list_all() -> list[dict]:
    # Mirror the original ORDER BY: plugin tools (non-null plugin_id) sort after
    # core tools, then by plugin_id, then by name.
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(tool_overrides).order_by(
                (tool_overrides.c.plugin_id.is_not(None)),
                tool_overrides.c.plugin_id,
                tool_overrides.c.name,
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def reuse_enabled_names() -> set[str]:
    """Names of tools with ``reuse_result`` ON (plano 47). Fail-safe → empty set.

    Read on every turn by ``agent.tool_memory.build_block`` to decide which tools
    keep their ``→ resultado`` in the compact block. Any read error degrades to an
    empty set (no reuse) so a turn is never broken.
    """
    try:
        return {row["name"] for row in list_all() if row.get("reuse_result")}
    except Exception:
        return set()


def ensure(name: str, plugin_id: str | None, *,
           default_enabled: bool = True) -> None:
    """Insert a default row on the first time a tool is registered.

    No-op when a row for ``name`` already exists. Always refreshes ``plugin_id``
    on conflict so a tool moving between core/plugin or between plugins ends up
    pointing at the current source.

    ``default_enabled`` (plano 30 WS2) só vale para a PRIMEIRA criação da row —
    ``update_cols=['plugin_id']`` garante que rows existentes nunca regridem,
    então tools off-by-default nascem desligadas apenas em instalação nova.
    """
    now = time.time()
    with get_engine().begin() as conn:
        conn.execute(upsert_stmt(
            tool_overrides,
            {
                "name": name,
                "plugin_id": plugin_id,
                "enabled": 1 if default_enabled else 0,
                "description": None,
                "display_label": None,
                # plano 47 — default 0: só na 1ª criação; ``update_cols=['plugin_id']``
                # garante que o re-registro NUNCA regride a escolha do usuário.
                "reuse_result": 0,
                "updated_at": now,
            },
            conflict_cols=["name"],
            update_cols=["plugin_id"],
        ))


def upsert_override(
    name: str,
    *,
    enabled=_UNSET,
    description=_UNSET,
    display_label=_UNSET,
    reuse_result=_UNSET,
) -> dict | None:
    """Apply a partial update. Sentinel ``_UNSET`` keeps the existing value.

    Pass ``description=None`` (or ``display_label=None``) to clear the field
    (reset to default). ``reuse_result`` (plano 47) toggles reaproveitar o
    resultado da tool no ``tool_memory``. Returns the updated row, or ``None`` if
    the tool is unknown.
    """
    existing = get(name)
    if existing is None:
        return None
    new_enabled = existing["enabled"] if enabled is _UNSET else (1 if enabled else 0)
    new_description = existing["description"] if description is _UNSET else description
    new_label = existing["display_label"] if display_label is _UNSET else display_label
    new_reuse = (existing.get("reuse_result", 0) if reuse_result is _UNSET
                 else (1 if reuse_result else 0))
    with get_engine().begin() as conn:
        conn.execute(sa_update(tool_overrides).where(tool_overrides.c.name == name).values(
            enabled=new_enabled,
            description=new_description,
            display_label=new_label,
            reuse_result=new_reuse,
            updated_at=time.time(),
        ))
    return get(name)


# Public name preserved.
upsert = upsert_override  # type: ignore[assignment]


def delete(name: str) -> int:
    """Remove a row de UMA tool (plano 30 WS3 — delete real de builtin).

    Usado pelo DELETE de builtin tombada: sem isso a row ficaria órfã até o
    ``delete_orphans`` do próximo boot e a tool ainda apareceria em /api/tools.
    """
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(tool_overrides).where(
            tool_overrides.c.name == name
        ))
    return result.rowcount or 0


def delete_for_plugin(plugin_id: str) -> int:
    """Remove all overrides belonging to a plugin. Used when plugin is deleted."""
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(tool_overrides).where(
            tool_overrides.c.plugin_id == plugin_id
        ))
    return result.rowcount or 0


def delete_orphans(known_names: set[str]) -> int:
    """Remove rows for tools that are no longer registered (renamed/removed)."""
    with get_engine().begin() as conn:
        rows = conn.execute(select(tool_overrides.c.name)).all()
        orphans = [r.name for r in rows if r.name not in known_names]
        for name in orphans:
            conn.execute(sa_delete(tool_overrides).where(tool_overrides.c.name == name))
    return len(orphans)
