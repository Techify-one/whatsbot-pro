"""Repository for messages table."""

import time

from db.connection import get_db


def add(contact_id: int, role: str, content: str, *,
        media_type: str | None = None, media_path: str | None = None,
        status: str | None = None, msg_id: str | None = None,
        ts: float | None = None) -> dict:
    """Insert a message and return it as a dict."""
    conn = get_db()
    ts = ts or time.time()
    cur = conn.execute(
        """INSERT INTO messages (contact_id, role, content, ts, media_type, media_path, status, msg_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (contact_id, role, content, ts, media_type, media_path, status, msg_id),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "role": role,
        "content": content,
        "ts": ts,
        "media_type": media_type,
        "media_path": media_path,
        "status": status,
        "msg_id": msg_id,
    }


def get_all(contact_id: int) -> list[dict]:
    """Return all messages for a contact ordered by timestamp."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE contact_id = ? ORDER BY ts",
        (contact_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_context(contact_id: int, limit: int) -> list[dict]:
    """Return the last N eligible messages for LLM context.

    Excludes transcription, tool_call, system_notice, and failed messages.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM messages
           WHERE contact_id = ?
             AND role NOT IN ('transcription', 'tool_call', 'system_notice')
             AND (status IS NULL OR status != 'failed')
           ORDER BY ts DESC
           LIMIT ?""",
        (contact_id, limit),
    ).fetchall()
    # Reverse to get chronological order
    return [_row_to_dict(r) for r in reversed(rows)]


def get_last(contact_id: int) -> dict | None:
    """Return the most recent message for a contact."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM messages WHERE contact_id = ? ORDER BY ts DESC LIMIT 1",
        (contact_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_last_user_message(contact_id: int) -> dict | None:
    """Return the most recent user message (for updating with transcription etc)."""
    conn = get_db()
    row = conn.execute(
        """SELECT * FROM messages
           WHERE contact_id = ? AND role = 'user'
           ORDER BY ts DESC LIMIT 1""",
        (contact_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def update_content(message_id: int, content: str) -> None:
    """Update the content of a specific message."""
    conn = get_db()
    conn.execute("UPDATE messages SET content = ? WHERE id = ?", (content, message_id))
    conn.commit()


def update_status(contact_id: int, content: str, new_status: str | None) -> None:
    """Update status of the most recent message matching content (for retry-send)."""
    conn = get_db()
    row = conn.execute(
        """SELECT id FROM messages
           WHERE contact_id = ? AND content = ? AND status = 'failed'
           ORDER BY ts DESC LIMIT 1""",
        (contact_id, content),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE messages SET status = ? WHERE id = ?",
            (new_status, row["id"]),
        )
        conn.commit()


def delete_all(contact_id: int) -> None:
    """Delete all messages for a contact."""
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE contact_id = ?", (contact_id,))
    conn.commit()


def _row_to_dict(row) -> dict:
    d = {
        "role": row["role"],
        "content": row["content"],
        "ts": row["ts"],
    }
    if row["media_type"]:
        d["media_type"] = row["media_type"]
    if row["media_path"]:
        d["media_path"] = row["media_path"]
    if row["status"]:
        d["status"] = row["status"]
    if row["msg_id"]:
        d["msg_id"] = row["msg_id"]
    # Include DB id for internal use (update_content etc)
    d["_id"] = row["id"]
    return d
