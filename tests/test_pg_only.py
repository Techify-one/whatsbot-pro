"""Plano 29 · Eixo C (C0) — fail-fast Postgres na resolução da DATABASE_URL.

    venv/bin/python -m pytest tests/test_pg_only.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from db.engine import resolve_database_url


def test_sem_url_falha_com_mensagem_acionavel(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError) as exc:
        resolve_database_url(tmp_path)
    assert "DATABASE_URL" in str(exc.value)
    assert "postgresql+psycopg://" in str(exc.value)


def test_url_sqlite_rejeitada(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///storages/whatsbot.db")
    with pytest.raises(RuntimeError) as exc:
        resolve_database_url(tmp_path)
    assert "Postgres-only" in str(exc.value)


def test_url_postgres_do_env_aceita(tmp_path: Path, monkeypatch):
    url = "postgresql+psycopg://u:p@db:5432/whatsbot"
    monkeypatch.setenv("DATABASE_URL", url)
    assert resolve_database_url(tmp_path) == url


def test_database_json_nao_e_mais_lido(tmp_path: Path, monkeypatch):
    """Plano 29 C4: o override storages/database.json morreu — só DATABASE_URL."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "database.json").write_text(
        '{"url": "postgresql+psycopg://u:p@db:5432/whatsbot"}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        resolve_database_url(tmp_path)


def test_erro_nao_vaza_senha(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://user:supersecret@db:3306/x")
    with pytest.raises(RuntimeError) as exc:
        resolve_database_url(tmp_path)
    assert "supersecret" not in str(exc.value)
