"""Repository for execution tracking tables."""

import json
import time

from db.connection import get_db


def create(phone: str, trigger_type: str = "webhook") -> int:
    """Create a new execution and return its ID."""
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO executions (phone, trigger_type, started_at) VALUES (?, ?, ?)",
        (phone, trigger_type, time.time()),
    )
    conn.commit()
    return cursor.lastrowid


def add_step(execution_id: int, step_type: str,
             data: dict | None = None, status: str = "ok") -> int:
    """Add a step to an execution and return step ID."""
    conn = get_db()
    data_json = json.dumps(data, ensure_ascii=False) if data else None
    cursor = conn.execute(
        "INSERT INTO execution_steps (execution_id, step_type, status, data, ts) VALUES (?, ?, ?, ?, ?)",
        (execution_id, step_type, status, data_json, time.time()),
    )
    conn.commit()
    return cursor.lastrowid


def complete(execution_id: int, status: str = "completed",
             error: str | None = None) -> None:
    """Mark an execution as completed or failed."""
    conn = get_db()
    conn.execute(
        "UPDATE executions SET status = ?, completed_at = ?, error = ? WHERE id = ?",
        (status, time.time(), error, execution_id),
    )
    conn.commit()


def get_by_id(execution_id: int) -> dict | None:
    """Return an execution with all its steps."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM executions WHERE id = ?", (execution_id,)
    ).fetchone()
    if not row:
        return None

    execution = dict(row)
    steps = conn.execute(
        "SELECT * FROM execution_steps WHERE execution_id = ? ORDER BY ts",
        (execution_id,),
    ).fetchall()
    execution["steps"] = []
    for s in steps:
        step = dict(s)
        if step.get("data"):
            try:
                step["data"] = json.loads(step["data"])
            except (json.JSONDecodeError, TypeError):
                pass
        execution["steps"].append(step)
    return execution


def list_executions(limit: int = 50, offset: int = 0,
                    phone: str | None = None,
                    status: str | None = None) -> list[dict]:
    """List executions (newest first) with step count and duration."""
    conn = get_db()
    clauses = []
    params: list = []
    if phone:
        clauses.append("e.phone = ?")
        params.append(phone)
    if status:
        clauses.append("e.status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = conn.execute(
        f"""SELECT e.*,
                   (SELECT COUNT(*) FROM execution_steps s WHERE s.execution_id = e.id) AS step_count
            FROM executions e {where}
            ORDER BY e.id DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        if d.get("started_at") and d.get("completed_at"):
            d["duration_ms"] = round((d["completed_at"] - d["started_at"]) * 1000)
        else:
            d["duration_ms"] = None
        results.append(d)
    return results


def count(phone: str | None = None, status: str | None = None) -> int:
    """Count total executions for pagination."""
    conn = get_db()
    clauses = []
    params: list = []
    if phone:
        clauses.append("phone = ?")
        params.append(phone)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    row = conn.execute(f"SELECT COUNT(*) AS cnt FROM executions {where}", params).fetchone()
    return row["cnt"]


def prune(max_keep: int) -> int:
    """Delete oldest executions keeping only the most recent max_keep. Returns count deleted."""
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) AS cnt FROM executions").fetchone()["cnt"]
    if total <= max_keep:
        return 0
    cursor = conn.execute(
        """DELETE FROM executions WHERE id NOT IN (
            SELECT id FROM executions ORDER BY id DESC LIMIT ?
        )""",
        (max_keep,),
    )
    conn.commit()
    return cursor.rowcount


def get_webhook_payloads(limit: int = 50) -> list[dict]:
    """Get recent webhook payloads from execution steps (replaces in-memory deque)."""
    conn = get_db()
    rows = conn.execute(
        """SELECT s.ts, s.data, e.phone
           FROM execution_steps s
           JOIN executions e ON e.id = s.execution_id
           WHERE s.step_type = 'webhook_received'
           ORDER BY s.ts DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    results = []
    for r in rows:
        entry = {"ts": r["ts"], "phone": r["phone"]}
        if r["data"]:
            try:
                entry["payload"] = json.loads(r["data"])
            except (json.JSONDecodeError, TypeError):
                entry["payload"] = r["data"]
        else:
            entry["payload"] = {}
        results.append(entry)
    results.reverse()
    return results
