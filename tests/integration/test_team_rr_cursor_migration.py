"""Migration ``0072_team_rr_cursor`` — cursor do round-robin: um por time.

Roda o Alembic de verdade contra o Postgres de TESTE: ``downgrade`` para a
revisão anterior (cursor por time + inbox), semeia linhas, ``upgrade`` e confere.
O ``finally`` devolve o banco ao head, porque a engine é global ao processo.

O que se prova:
  * várias linhas do MESMO time (uma por inbox) colapsam em UMA, a mais recente;
  * times diferentes não se misturam;
  * ``inbox_id`` some e a PK passa a ser só ``team_id``;
  * ``downgrade`` restaura o shape antigo sem levantar (cursor reinicia vazio).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from db.engine import get_engine
from tests.paths import PROJECT_ROOT

PREV = "0071_ai_credentials"

def _cfg() -> Config:
    cfg = Config(str(Path(PROJECT_ROOT) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(PROJECT_ROOT) / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url).replace("%", "%%"))
    return cfg

def _state_columns() -> set[str]:
    return {c["name"] for c in inspect(get_engine()).get_columns("team_routing_state")}

@pytest.fixture
def at_head(_engine_ready):
    """Garante o head antes e DEPOIS: o round-trip mexe no banco compartilhado."""
    cfg = _cfg()
    command.upgrade(cfg, "head")
    try:
        yield cfg
    finally:
        command.upgrade(cfg, "head")

def test_collapses_to_one_row_per_team_keeping_the_most_recent(at_head):
    marker = uuid.uuid4().hex[:8]
    command.downgrade(at_head, PREV)
    assert "inbox_id" in _state_columns()
    team_a = team_b = None
    created_channels: list[str] = []
    try:
        with get_engine().begin() as conn:
            inboxes = []
            for i in range(3):
                channel_id = f"rrm_{marker}_{i}"
                created_channels.append(channel_id)
                conn.execute(text(
                    "INSERT INTO channels (id, provider, display_name, config, created_at, updated_at) "
                    "VALUES (:id, 'test', :id, '{}', 0, 0)"), {"id": channel_id})
                inboxes.append(conn.execute(text(
                    "INSERT INTO inboxes (channel_id, name, channel_type, created_at, updated_at) "
                    "VALUES (:id, :id, 'test', 0, 0) RETURNING id"), {"id": channel_id}).scalar())
            users = [conn.execute(text(
                "INSERT INTO users (email, name, password_hash, is_active, "
                "custom_permissions, created_at, updated_at) "
                "VALUES (:e, 'u', 'x', 1, 1, 0, 0) RETURNING id"),
                {"e": f"rrm-{marker}-{i}@test.local"}).scalar() for i in range(3)]
            team_a, team_b = [conn.execute(text(
                "INSERT INTO teams (name, created_at, updated_at, routing_mode) "
                "VALUES (:n, 0, 0, 'round_robin') RETURNING id"),
                {"n": f"rr-{marker}-{k}"}).scalar() for k in ("a", "b")]
            # time A: três inboxes; a de updated_at maior (users[2]) deve sobreviver
            for inbox_id, user_id, ts in zip(inboxes, users, (100.0, 300.0, 200.0)):
                conn.execute(text(
                    "INSERT INTO team_routing_state (team_id, inbox_id, last_user_id, updated_at) "
                    "VALUES (:t, :i, :u, :ts)"),
                    {"t": team_a, "i": inbox_id, "u": user_id, "ts": ts})
            # time B: uma linha só — não pode ser tocada
            conn.execute(text(
                "INSERT INTO team_routing_state (team_id, inbox_id, last_user_id, updated_at) "
                "VALUES (:t, :i, :u, 50.0)"), {"t": team_b, "i": inboxes[0], "u": users[0]})

        command.upgrade(at_head, "head")

        assert "inbox_id" not in _state_columns()
        pk = inspect(get_engine()).get_pk_constraint("team_routing_state")
        assert pk["constrained_columns"] == ["team_id"]
        with get_engine().connect() as conn:
            rows = {r[0]: (r[1], r[2]) for r in conn.execute(text(
                "SELECT team_id, last_user_id, updated_at FROM team_routing_state "
                "WHERE team_id IN (:a, :b)"), {"a": team_a, "b": team_b})}
        assert rows[team_a] == (users[1], 300.0)   # a mais recente
        assert rows[team_b] == (users[0], 50.0)    # intacto
        assert len(rows) == 2
    finally:
        with get_engine().begin() as conn:
            if team_a is not None:
                conn.execute(text("DELETE FROM teams WHERE id IN (:a, :b)"),
                             {"a": team_a, "b": team_b})
            conn.execute(text("DELETE FROM users WHERE email LIKE :p"),
                         {"p": f"rrm-{marker}-%"})
            for channel_id in created_channels:
                conn.execute(text("DELETE FROM channels WHERE id = :id"), {"id": channel_id})

def test_downgrade_restores_the_per_inbox_shape(at_head):
    command.downgrade(at_head, PREV)
    assert "inbox_id" in _state_columns()
    pk = inspect(get_engine()).get_pk_constraint("team_routing_state")
    assert pk["constrained_columns"] == ["team_id", "inbox_id"]
    command.upgrade(at_head, "head")                # volta sem levantar
    assert "inbox_id" not in _state_columns()

def test_revision_id_fits_alembic_version_column():
    """``alembic_version.version_num`` é varchar(32): um id maior quebra o upgrade
    só em runtime (aconteceu com o nome longo desta migration)."""
    src = (Path(PROJECT_ROOT) / "db" / "alembic" / "versions"
           / "20261016_0072_team_rr_cursor.py").read_text(encoding="utf-8")
    assert 'revision: str = "0072_team_rr_cursor"' in src
    assert len("0072_team_rr_cursor") <= 32
