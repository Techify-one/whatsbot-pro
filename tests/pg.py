"""Postgres de TESTE — resolução da URL + reset de schema (plano 29 · C3).

Toda a suíte (pytest e scripts legados standalone) roda contra um Postgres de
teste. A URL vem de ``WHATSBOT_TEST_DB_URL`` (env) com fallback no ``.env`` da
raiz do repo — sem NENHUM caminho SQLite.

Guard-rail: como o bootstrap APAGA o schema ``public`` (reset por processo), a
URL precisa apontar para um banco cujo nome contenha ``test``; para usar outro
nome, exporte ``WHATSBOT_TEST_DB_ALLOW_ANY=1`` conscientemente.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_MISSING_MSG = (
    "WHATSBOT_TEST_DB_URL não configurada — a suíte roda contra um Postgres de "
    "TESTE (plano 29 C3, sem SQLite). Configure a env (ou uma linha no .env da "
    "raiz): WHATSBOT_TEST_DB_URL=postgresql+psycopg://user:senha@host:5432/"
    "whatsbot_test"
)


def _from_dotenv() -> str:
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return ""
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("WHATSBOT_TEST_DB_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def test_db_url() -> str:
    """Resolve e valida a URL do Postgres de teste (env > .env)."""
    url = os.environ.get("WHATSBOT_TEST_DB_URL", "").strip() or _from_dotenv()
    if not url:
        raise RuntimeError(_MISSING_MSG)
    if not url.startswith("postgresql"):
        raise RuntimeError(
            "WHATSBOT_TEST_DB_URL deve ser Postgres "
            "(postgresql+psycopg://…) — o WhatsBot é Postgres-only (plano 29).")
    from sqlalchemy.engine import make_url
    dbname = make_url(url).database or ""
    if "test" not in dbname and os.environ.get("WHATSBOT_TEST_DB_ALLOW_ANY") != "1":
        raise RuntimeError(
            f"WHATSBOT_TEST_DB_URL aponta para o banco '{dbname}', que não parece "
            f"um banco de TESTE — o runner APAGA o schema public. Use um banco com "
            f"'test' no nome ou exporte WHATSBOT_TEST_DB_ALLOW_ANY=1.")
    # Propaga para subprocessos (test_legacy_suite roda os scripts herdando o env).
    os.environ["WHATSBOT_TEST_DB_URL"] = url
    return url


def reset_schema(url: str) -> None:
    """DROP SCHEMA public CASCADE + CREATE — banco de teste vazio e determinístico."""
    from sqlalchemy import create_engine

    eng = create_engine(url, future=True,
                        connect_args={"prepare_threshold": None})
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")
    eng.dispose()


def init_test_engine(*, reset: bool = True) -> str:
    """Bootstrap padrão dos testes: resolve URL, (re)cria o schema e migra a head.

    ``reset=True`` (default) dá a cada PROCESSO de teste um schema limpo — o
    equivalente Postgres do antigo temp-SQLite por processo.
    """
    url = test_db_url()
    if reset:
        reset_schema(url)
    from db import init_engine
    from db.connection import _run_alembic_upgrade

    init_engine(url)
    _run_alembic_upgrade()
    return url
