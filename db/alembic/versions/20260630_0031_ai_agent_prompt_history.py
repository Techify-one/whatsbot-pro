"""Add ``ai_agent_prompt_history`` — the dedicated, git-like prompt version trail.

Each agent's inline prompt (``ai_agents.prompt``, source of truth since 0030) gets
a separate version track: one row per version holding the FULL prompt text (the git
"blob" content model). Diffs are computed on demand (``agent.prompt_diff``), never
stored. This is ADDITIVE to ``ai_agents_history`` (the whole-agent trail) — neither
that table nor ``ai_agents`` is touched.

Backfill reconstructs the real prompt evolution from the existing whole-agent
history: for each agent, walk its ``ai_agents_history`` rows (asc by version),
extract ``prompt`` from each snapshot, dedup consecutive identical prompts, and
insert sequential trail versions (1,2,3…) preserving the original ``created_at``.
Agents with no usable history fall back to a single v1 = the current
``ai_agents.prompt``. Idempotent: agents that already have trail rows are skipped.

Revision ID: 0031_ai_agent_prompt_history
Revises: 0030_ai_agent_inline_prompt
Create Date: 2026-06-30
"""
from __future__ import annotations

import json
import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0031_ai_agent_prompt_history"
down_revision: Union[str, Sequence[str], None] = "0030_ai_agent_inline_prompt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(conn, table: str) -> bool:
    return sa.inspect(conn).has_table(table)


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "ai_agent_prompt_history"):
        op.create_table(
            "ai_agent_prompt_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("agent_key", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
        )
        op.create_index(
            "idx_ai_agent_prompt_hist",
            "ai_agent_prompt_history",
            ["agent_key", "version"],
        )

    if not _has_table(conn, "ai_agents"):
        return
    from db.tables import ai_agent_prompt_history, ai_agents

    # Skip agents that already have a trail (re-run safe).
    seeded = {
        r[0] for r in conn.execute(
            sa.select(ai_agent_prompt_history.c.agent_key).distinct()
        ).all()
    }

    agents = conn.execute(
        sa.select(ai_agents.c.agent_key, ai_agents.c.prompt)
    ).mappings().all()

    has_hist = _has_table(conn, "ai_agents_history")
    for a in agents:
        agent_key = a["agent_key"]
        if agent_key in seeded:
            continue

        # Reconstruct the prompt timeline from the whole-agent history snapshots.
        timeline: list[tuple[str, float]] = []
        if has_hist:
            from db.tables import ai_agents_history
            rows = conn.execute(
                sa.select(ai_agents_history.c.snapshot, ai_agents_history.c.created_at)
                .where(ai_agents_history.c.agent_key == agent_key)
                .order_by(ai_agents_history.c.version.asc())
            ).mappings().all()
            for row in rows:
                try:
                    snap = json.loads(row["snapshot"]) if row["snapshot"] else {}
                except (json.JSONDecodeError, TypeError):
                    snap = {}
                timeline.append(((snap.get("prompt") or ""), row["created_at"]))

        # Dedup consecutive identical prompts; drop a leading empty placeholder.
        evolution: list[tuple[str, float]] = []
        prev = None
        for prompt_text, created_at in timeline:
            if prompt_text == prev:
                continue
            evolution.append((prompt_text, created_at))
            prev = prompt_text
        evolution = [(p, c) for (p, c) in evolution if p.strip()]

        if not evolution:
            current = (a["prompt"] or "")
            evolution = [(current, time.time())]

        for idx, (prompt_text, created_at) in enumerate(evolution, start=1):
            conn.execute(ai_agent_prompt_history.insert().values(
                agent_key=agent_key,
                version=idx,
                prompt=prompt_text,
                note=None,
                created_at=created_at,
            ))


def downgrade() -> None:
    conn = op.get_bind()
    if _has_table(conn, "ai_agent_prompt_history"):
        op.drop_index("idx_ai_agent_prompt_hist", table_name="ai_agent_prompt_history")
        op.drop_table("ai_agent_prompt_history")
