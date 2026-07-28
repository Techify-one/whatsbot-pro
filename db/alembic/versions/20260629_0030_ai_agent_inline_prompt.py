"""Move the agent prompt inline: add ``prompt`` to ``ai_agents`` and backfill it.

The AI engine used to reference a shared, reusable prompt template by
``ai_agents.prompt_key`` → ``ai_prompts.body``. The product model changed: each
agent now owns its own prompt (a free-text body, not selectable/reusable from
another agent). This migration adds the inline ``prompt`` column and backfills it
from the agent's previously-referenced template so behaviour is preserved exactly.

Backfill rule (preserves the old resolution): an agent's effective prompt key was
``prompt_key or 'default'`` — i.e. an agent that left the prompt blank inherited
the default agent's body. We copy ``ai_prompts[effective_key].body`` into the new
column. Agents whose template is missing/empty stay empty and fall back to the
in-code seed prompt at runtime (same as before).

The legacy ``prompt_key`` column and the ``ai_prompts`` table/history are left in
place (non-destructive, reversible) but are no longer read for resolution.

Revision ID: 0030_ai_agent_inline_prompt
Revises: 0029_cpf_not_system
Create Date: 2026-06-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0030_ai_agent_inline_prompt"
down_revision: Union[str, Sequence[str], None] = "0029_cpf_not_system"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(conn, table: str, column: str) -> bool:
    insp = sa.inspect(conn)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "ai_agents", "prompt"):
        op.add_column(
            "ai_agents",
            sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        )

    # Backfill from ai_prompts using the OLD resolution (prompt_key or 'default').
    insp = sa.inspect(conn)
    if not (insp.has_table("ai_agents") and insp.has_table("ai_prompts")):
        return
    from db.tables import ai_agents, ai_prompts

    prompts = {
        r["prompt_key"]: (r["body"] or "")
        for r in conn.execute(
            sa.select(ai_prompts.c.prompt_key, ai_prompts.c.body)
        ).mappings().all()
    }
    agents = conn.execute(
        sa.select(ai_agents.c.agent_key, ai_agents.c.prompt_key, ai_agents.c.prompt)
    ).mappings().all()
    for a in agents:
        if (a["prompt"] or "").strip():
            continue  # already has an inline prompt (re-run safe)
        effective_key = (a["prompt_key"] or "").strip() or "default"
        body = prompts.get(effective_key, "")
        if body:
            conn.execute(
                sa.update(ai_agents)
                .where(ai_agents.c.agent_key == a["agent_key"])
                .values(prompt=body)
            )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "ai_agents", "prompt"):
        op.drop_column("ai_agents", "prompt")
