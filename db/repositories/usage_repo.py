"""Repository for usage (cost tracking) table."""

import time

from db.connection import get_db


def add(contact_id: int, call_type: str, model: str,
        prompt_tokens: int, completion_tokens: int,
        total_tokens: int, cost_usd: float) -> None:
    """Insert a usage record."""
    conn = get_db()
    conn.execute(
        """INSERT INTO usage (contact_id, call_type, model, prompt_tokens,
           completion_tokens, total_tokens, cost_usd, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (contact_id, call_type, model, prompt_tokens,
         completion_tokens, total_tokens, cost_usd, time.time()),
    )
    conn.commit()


def _time_filter(start_ts: float | None, end_ts: float | None) -> tuple[str, list]:
    """Build WHERE clause fragment for time filtering."""
    clauses = []
    params = []
    if start_ts is not None:
        clauses.append("ts >= ?")
        params.append(start_ts)
    if end_ts is not None:
        clauses.append("ts <= ?")
        params.append(end_ts)
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where, params


def summary(contact_id: int, start_ts: float | None = None,
            end_ts: float | None = None) -> dict:
    """Return aggregated usage stats for a single contact."""
    time_where, time_params = _time_filter(start_ts, end_ts)
    conn = get_db()

    # Overall totals
    row = conn.execute(
        f"""SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                   COUNT(*) AS call_count
            FROM usage WHERE contact_id = ?{time_where}""",
        [contact_id] + time_params,
    ).fetchone()

    totals = {
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "cost_usd": row["cost_usd"],
        "call_count": row["call_count"],
        "by_type": {},
    }

    # Breakdown by call_type
    by_type_rows = conn.execute(
        f"""SELECT call_type,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                   COUNT(*) AS call_count
            FROM usage WHERE contact_id = ?{time_where}
            GROUP BY call_type""",
        [contact_id] + time_params,
    ).fetchall()

    for r in by_type_rows:
        totals["by_type"][r["call_type"]] = {
            "cost_usd": r["cost_usd"],
            "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"],
            "total_tokens": r["total_tokens"],
            "call_count": r["call_count"],
        }

    return totals


def global_summary(start_ts: float | None = None,
                   end_ts: float | None = None) -> dict:
    """Return aggregated usage stats across ALL contacts."""
    time_where, time_params = _time_filter(start_ts, end_ts)
    conn = get_db()

    where_clause = "WHERE 1=1" + time_where if time_where else ""
    row = conn.execute(
        f"""SELECT COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                   COUNT(*) AS call_count
            FROM usage {where_clause}""",
        time_params,
    ).fetchone()

    totals = {
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "cost_usd": row["cost_usd"],
        "call_count": row["call_count"],
        "by_type": {},
    }

    by_type_rows = conn.execute(
        f"""SELECT call_type,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                   COUNT(*) AS call_count
            FROM usage {where_clause}
            GROUP BY call_type""",
        time_params,
    ).fetchall()

    for r in by_type_rows:
        totals["by_type"][r["call_type"]] = {
            "cost_usd": r["cost_usd"],
            "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"],
            "total_tokens": r["total_tokens"],
            "call_count": r["call_count"],
        }

    return totals


def by_contact(start_ts: float | None = None,
               end_ts: float | None = None) -> list[dict]:
    """Return usage breakdown per contact (for the by-contact endpoint)."""
    time_where, time_params = _time_filter(start_ts, end_ts)
    conn = get_db()

    where_clause = "WHERE 1=1" + time_where if time_where else ""
    rows = conn.execute(
        f"""SELECT u.contact_id, c.phone, c.name,
                   COALESCE(SUM(u.prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(u.completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(u.total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(u.cost_usd), 0.0) AS cost_usd,
                   COUNT(*) AS call_count
            FROM usage u
            JOIN contacts c ON c.id = u.contact_id
            {where_clause}
            GROUP BY u.contact_id
            HAVING call_count > 0
            ORDER BY cost_usd DESC""",
        time_params,
    ).fetchall()

    results = []
    for row in rows:
        contact_id = row["contact_id"]
        # Get by_type breakdown for this contact
        by_type_rows = conn.execute(
            f"""SELECT call_type,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                       COUNT(*) AS call_count
                FROM usage WHERE contact_id = ?{time_where}
                GROUP BY call_type""",
            [contact_id] + time_params,
        ).fetchall()

        by_type = {}
        for r in by_type_rows:
            by_type[r["call_type"]] = {
                "cost_usd": r["cost_usd"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "total_tokens": r["total_tokens"],
                "call_count": r["call_count"],
            }

        results.append({
            "phone": row["phone"],
            "name": row["name"] or "",
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["total_tokens"],
            "cost_usd": row["cost_usd"],
            "call_count": row["call_count"],
            "by_type": by_type,
        })

    return results


def detail(contact_id: int, start_ts: float | None = None,
           end_ts: float | None = None) -> list[dict]:
    """Return raw usage records for a specific contact."""
    time_where, time_params = _time_filter(start_ts, end_ts)
    conn = get_db()
    rows = conn.execute(
        f"""SELECT call_type, model, prompt_tokens, completion_tokens,
                   total_tokens, cost_usd, ts
            FROM usage WHERE contact_id = ?{time_where}
            ORDER BY ts""",
        [contact_id] + time_params,
    ).fetchall()
    return [dict(r) for r in rows]
