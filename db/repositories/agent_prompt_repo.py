"""Repository for ``ai_agent_prompt_history`` — the dedicated prompt version trail.

A git-like track of an agent's inline prompt: each version is a FULL snapshot of
the prompt text (the git "blob" content model), and the diff is computed on demand
via :mod:`agent.prompt_diff` (never stored). The version counter is independent per
``agent_key`` (``MAX(version)+1``), with **dedup** (no new version when the prompt
is unchanged) and an optional per-version commit ``note``.

Additive to ``ai_agents_history`` (the whole-agent trail): both coexist and answer
different questions. Molded on :mod:`db.repositories.prompt_repo`.
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete, select, update as sa_update

from db.engine import get_engine
from db.tables import ai_agent_prompt_history


def _last(agent_key: str, conn) -> dict | None:
    row = conn.execute(
        select(ai_agent_prompt_history)
        .where(ai_agent_prompt_history.c.agent_key == agent_key)
        .order_by(ai_agent_prompt_history.c.version.desc())
        .limit(1)
    ).mappings().first()
    return dict(row) if row else None


def record(agent_key: str, prompt: str, note: str | None = None, conn=None) -> dict | None:
    """Append a new prompt version — **dedup**: returns ``None`` (no row) when the
    prompt equals the agent's last recorded prompt.

    ``conn`` may be passed to run inside the caller's transaction (atomic with the
    agent save). Without it, the write runs in its own transaction.
    """
    prompt = prompt or ""

    def _run(c) -> dict | None:
        last = _last(agent_key, c)
        if last is not None and (last["prompt"] or "") == prompt:
            return None
        version = (last["version"] + 1) if last else 1
        now = time.time()
        c.execute(ai_agent_prompt_history.insert().values(
            agent_key=agent_key, version=version, prompt=prompt,
            note=note, created_at=now,
        ))
        return {"agent_key": agent_key, "version": version, "prompt": prompt,
                "note": note, "created_at": now}

    if conn is not None:
        return _run(conn)
    with get_engine().begin() as c:
        return _run(c)


def amend(agent_key: str, prompt: str, note: str | None = None, conn=None) -> dict | None:
    """Overwrite the agent's LATEST prompt version in place (git ``commit --amend``)
    instead of appending a new one. The version number is preserved and the
    original ``created_at`` is kept (so the "atual" version doesn't jump by date);
    only the prompt text and the commit ``note`` are replaced — the previous text
    of that version is lost.

    With no prior version (brand-new agent), falls back to :func:`record`, which
    inserts v1. Unlike ``record``, ``amend`` has **no dedup**: it overwrites the
    current text even when it didn't change (the caller already gated the action).

    ``conn`` may be passed to run inside the caller's transaction (atomic with the
    agent save). Without it, the write runs in its own transaction.
    """
    prompt = prompt or ""

    def _run(c) -> dict | None:
        last = _last(agent_key, c)
        if last is None:
            return record(agent_key, prompt, note=note, conn=c)
        c.execute(
            sa_update(ai_agent_prompt_history)
            .where(
                ai_agent_prompt_history.c.agent_key == agent_key,
                ai_agent_prompt_history.c.version == last["version"],
            )
            .values(prompt=prompt, note=note)
        )
        return {"agent_key": agent_key, "version": last["version"], "prompt": prompt,
                "note": note, "created_at": last["created_at"]}

    if conn is not None:
        return _run(conn)
    with get_engine().begin() as c:
        return _run(c)


def list_history(agent_key: str) -> list[dict]:
    """Version trail (newest-first) enriched with per-version diff counts vs the
    previous version. v1 (no predecessor): ``added_lines`` = nº de linhas, ``removed_lines`` = 0.
    """
    from agent import prompt_diff  # lazy: avoids loading the agent package at import time

    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_agent_prompt_history)
            .where(ai_agent_prompt_history.c.agent_key == agent_key)
            .order_by(ai_agent_prompt_history.c.version.asc())
        ).mappings().all()

    out: list[dict] = []
    prev = ""
    for i, r in enumerate([dict(r) for r in rows]):
        cur = r["prompt"] or ""
        if i == 0:
            added, removed = len(cur.splitlines()), 0
        else:
            d = prompt_diff.text_diff(prev, cur)
            added, removed = d["added_lines"], d["removed_lines"]
        out.append({
            "version": r["version"],
            "created_at": r["created_at"],
            "note": r["note"],
            "added_lines": added,
            "removed_lines": removed,
        })
        prev = cur
    out.reverse()
    return out


def get_version(agent_key: str, version: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(
                ai_agent_prompt_history.c.version,
                ai_agent_prompt_history.c.created_at,
                ai_agent_prompt_history.c.note,
                ai_agent_prompt_history.c.prompt,
            ).where(
                ai_agent_prompt_history.c.agent_key == agent_key,
                ai_agent_prompt_history.c.version == version,
            )
        ).mappings().first()
    return dict(row) if row else None


def diff(agent_key: str, from_version: int, to_version: int) -> dict | None:
    """Compute the on-the-fly diff between two versions. ``None`` if either is absent."""
    from agent import prompt_diff  # lazy

    a = get_version(agent_key, from_version)
    b = get_version(agent_key, to_version)
    if a is None or b is None:
        return None
    d = prompt_diff.text_diff(
        a["prompt"], b["prompt"],
        from_label=f"v{from_version}", to_label=f"v{to_version}",
    )
    d["prompt_from"] = a["prompt"]
    d["prompt_to"] = b["prompt"]
    return d


def rename_version(agent_key: str, version: int, note: str | None) -> dict | None:
    """Edit a version's commit ``note`` (the human-friendly name). Returns the
    updated version row, or ``None`` if the version doesn't exist."""
    note = (note or "").strip() or None
    with get_engine().begin() as conn:
        res = conn.execute(
            sa_update(ai_agent_prompt_history)
            .where(
                ai_agent_prompt_history.c.agent_key == agent_key,
                ai_agent_prompt_history.c.version == version,
            )
            .values(note=note)
        )
        if res.rowcount == 0:
            return None
    return get_version(agent_key, version)


def delete_version(agent_key: str, version: int) -> bool:
    """Remove a single version snapshot from the trail. Non-destructive to the
    live agent (whose prompt lives in ``ai_agents.prompt``). Returns ``True`` if a
    row was deleted."""
    with get_engine().begin() as conn:
        res = conn.execute(
            sa_delete(ai_agent_prompt_history).where(
                ai_agent_prompt_history.c.agent_key == agent_key,
                ai_agent_prompt_history.c.version == version,
            )
        )
    return res.rowcount > 0


def restore(agent_key: str, version: int) -> dict | None:
    """Re-apply a past prompt via ``agent_repo.save`` (forward, non-destructive) —
    preserves the agent's other fields and records a fresh prompt version.
    """
    from db.repositories import agent_repo

    ver = get_version(agent_key, version)
    if ver is None:
        return None
    existing = agent_repo.get(agent_key)
    if not existing:
        return None
    return agent_repo.save(
        agent_key,
        display_name=existing.get("display_name") or "",
        prompt=ver["prompt"],
        prompt_key=existing.get("prompt_key", ""),
        model_config=existing.get("model_config") or {},
        tool_names=existing.get("tool_names"),
        enabled=bool(existing.get("enabled", True)),
        description=existing.get("description", ""),
        is_router=bool(existing.get("is_router", False)),
        routing_targets=existing.get("routing_targets"),
        hooks_config=existing.get("hooks_config") or {},
        change_note=f"Restaurado da versão v{version}",
    )
