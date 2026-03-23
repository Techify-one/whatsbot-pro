"""AI usage tracking with SQLite — records tokens and cost per AI call."""

import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    phone             TEXT    NOT NULL,
    call_type         TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL    NOT NULL DEFAULT 0.0,
    created_at        REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_phone      ON usage(phone);
CREATE INDEX IF NOT EXISTS idx_usage_created_at  ON usage(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_phone_ts    ON usage(phone, created_at);
"""


class UsageTracker:
    """Records and queries AI usage/cost data using SQLite."""

    def __init__(self, data_dir: Path):
        self.db_path = data_dir / "usage.db"
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    # ── Record ───────────────────────────────────────────────────────

    def record(
        self,
        phone: str,
        call_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO usage
               (phone, call_type, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (phone, call_type, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, time.time()),
        )
        conn.commit()
        logger.debug(
            "Usage recorded: %s %s %s tokens=%d cost=%.6f",
            phone, call_type, model, total_tokens, cost_usd,
        )

    # ── Queries ──────────────────────────────────────────────────────

    def _where_clause(
        self,
        phone: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []
        if phone:
            clauses.append("phone = ?")
            params.append(phone)
        if start_ts is not None:
            clauses.append("created_at >= ?")
            params.append(start_ts)
        if end_ts is not None:
            clauses.append("created_at <= ?")
            params.append(end_ts)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def total_summary(
        self,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> dict:
        where, params = self._where_clause(start_ts=start_ts, end_ts=end_ts)
        conn = self._get_conn()

        row = conn.execute(
            f"""SELECT
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                    COUNT(*) AS call_count
                FROM usage{where}""",
            params,
        ).fetchone()

        by_type: dict[str, dict] = {}
        for tr in conn.execute(
            f"""SELECT call_type,
                       COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COUNT(*) AS call_count
                FROM usage{where}
                GROUP BY call_type""",
            params,
        ).fetchall():
            by_type[tr["call_type"]] = {
                "cost_usd": tr["cost_usd"],
                "prompt_tokens": tr["prompt_tokens"],
                "completion_tokens": tr["completion_tokens"],
                "total_tokens": tr["total_tokens"],
                "call_count": tr["call_count"],
            }

        return {
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["total_tokens"],
            "cost_usd": row["cost_usd"],
            "call_count": row["call_count"],
            "by_type": by_type,
        }

    def summary_by_contact(
        self,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> list[dict]:
        where, params = self._where_clause(start_ts=start_ts, end_ts=end_ts)
        conn = self._get_conn()

        rows = conn.execute(
            f"""SELECT phone,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                       COUNT(*) AS call_count
                FROM usage{where}
                GROUP BY phone
                ORDER BY cost_usd DESC""",
            params,
        ).fetchall()

        result = []
        for r in rows:
            phone = r["phone"]
            pw, pp = self._where_clause(phone=phone, start_ts=start_ts, end_ts=end_ts)
            by_type: dict[str, dict] = {}
            for tr in conn.execute(
                f"""SELECT call_type,
                           COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                           COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens,
                           COUNT(*) AS call_count
                    FROM usage{pw}
                    GROUP BY call_type""",
                pp,
            ).fetchall():
                by_type[tr["call_type"]] = {
                    "cost_usd": tr["cost_usd"],
                    "prompt_tokens": tr["prompt_tokens"],
                    "completion_tokens": tr["completion_tokens"],
                    "total_tokens": tr["total_tokens"],
                    "call_count": tr["call_count"],
                }

            result.append({
                "phone": phone,
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "total_tokens": r["total_tokens"],
                "cost_usd": r["cost_usd"],
                "call_count": r["call_count"],
                "by_type": by_type,
            })

        return result

    def contact_detail(
        self,
        phone: str,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> list[dict]:
        where, params = self._where_clause(phone=phone, start_ts=start_ts, end_ts=end_ts)
        conn = self._get_conn()
        rows = conn.execute(
            f"""SELECT call_type, model, prompt_tokens, completion_tokens,
                       total_tokens, cost_usd, created_at
                FROM usage{where}
                ORDER BY created_at DESC""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
