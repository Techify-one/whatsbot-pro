"""Repository for contacts table."""

import time

from db.connection import get_db


def get_or_create(phone: str, default_ai_enabled: bool = True) -> dict:
    """Get a contact by phone, creating it if it doesn't exist. Returns a dict."""
    conn = get_db()
    row = conn.execute("SELECT * FROM contacts WHERE phone = ?", (phone,)).fetchone()
    if row is not None:
        return _row_to_dict(row)
    now = time.time()
    cur = conn.execute(
        """INSERT INTO contacts (phone, ai_enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?)""",
        (phone, 1 if default_ai_enabled else 0, now, now),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "phone": phone,
        "name": "",
        "email": "",
        "profession": "",
        "company": "",
        "address": "",
        "ai_enabled": default_ai_enabled,
        "is_group": False,
        "group_name": "",
        "is_archived": False,
        "unread_count": 0,
        "unread_ai_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def get_by_phone(phone: str) -> dict | None:
    """Get a contact by phone number. Returns None if not found."""
    conn = get_db()
    row = conn.execute("SELECT * FROM contacts WHERE phone = ?", (phone,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def update(contact_id: int, **fields) -> None:
    """Update specific fields on a contact."""
    if not fields:
        return
    fields["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [contact_id]
    conn = get_db()
    conn.execute(f"UPDATE contacts SET {set_clause} WHERE id = ?", values)
    conn.commit()


def increment_unread(contact_id: int, msg_id: str | None = None) -> None:
    """Increment unread_count and optionally track the msg_id."""
    conn = get_db()
    conn.execute(
        "UPDATE contacts SET unread_count = unread_count + 1, updated_at = ? WHERE id = ?",
        (time.time(), contact_id),
    )
    if msg_id:
        conn.execute(
            "INSERT INTO unread_msg_ids (contact_id, msg_id) VALUES (?, ?)",
            (contact_id, msg_id),
        )
    conn.commit()


def increment_unread_ai(contact_id: int) -> None:
    """Increment unread_ai_count."""
    conn = get_db()
    conn.execute(
        "UPDATE contacts SET unread_ai_count = unread_ai_count + 1, updated_at = ? WHERE id = ?",
        (time.time(), contact_id),
    )
    conn.commit()


def mark_as_read(contact_id: int) -> list[str]:
    """Reset unread counts and return the unread msg_ids (for read receipts)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT msg_id FROM unread_msg_ids WHERE contact_id = ?", (contact_id,)
    ).fetchall()
    msg_ids = [r["msg_id"] for r in rows]
    conn.execute("DELETE FROM unread_msg_ids WHERE contact_id = ?", (contact_id,))
    conn.execute(
        "UPDATE contacts SET unread_count = 0, unread_ai_count = 0, updated_at = ? WHERE id = ?",
        (time.time(), contact_id),
    )
    conn.commit()
    return msg_ids


def get_observations(contact_id: int) -> list[str]:
    """Return all observations for a contact."""
    conn = get_db()
    rows = conn.execute(
        "SELECT text FROM observations WHERE contact_id = ? ORDER BY created_at",
        (contact_id,),
    ).fetchall()
    return [r["text"] for r in rows]


def set_observations(contact_id: int, observations: list[str]) -> None:
    """Replace all observations for a contact."""
    conn = get_db()
    conn.execute("DELETE FROM observations WHERE contact_id = ?", (contact_id,))
    now = time.time()
    conn.executemany(
        "INSERT INTO observations (contact_id, text, created_at) VALUES (?, ?, ?)",
        [(contact_id, text, now) for text in observations if text.strip()],
    )
    conn.commit()


def add_observation(contact_id: int, text: str) -> None:
    """Append a single observation if it doesn't already exist."""
    conn = get_db()
    existing = conn.execute(
        "SELECT 1 FROM observations WHERE contact_id = ? AND text = ?",
        (contact_id, text),
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO observations (contact_id, text, created_at) VALUES (?, ?, ?)",
        (contact_id, text, time.time()),
    )
    conn.commit()


def list_contacts(q: str = "", archived: bool = False) -> list[dict]:
    """List contacts with last message preview, tags, and unread counts.

    This replaces the old glob-all-JSON-files approach with a single efficient query.
    """
    conn = get_db()

    # Main query: contacts with last visible message via subquery
    rows = conn.execute(
        """
        SELECT c.*,
               lm.content   AS last_msg_content,
               lm.role      AS last_msg_role,
               lm.ts        AS last_msg_ts,
               lm.media_type AS last_msg_media_type,
               (SELECT COUNT(*) FROM messages WHERE contact_id = c.id) AS msg_count
        FROM contacts c
        LEFT JOIN (
            SELECT m1.contact_id, m1.content, m1.role, m1.ts, m1.media_type
            FROM messages m1
            INNER JOIN (
                SELECT contact_id, MAX(ts) AS max_ts
                FROM messages
                WHERE role NOT IN ('transcription', 'system_notice')
                GROUP BY contact_id
            ) m2 ON m1.contact_id = m2.contact_id AND m1.ts = m2.max_ts
        ) lm ON lm.contact_id = c.id
        WHERE c.is_archived = ?
        ORDER BY c.updated_at DESC
        """,
        (1 if archived else 0,),
    ).fetchall()

    results = []
    for row in rows:
        contact_id = row["id"]

        # Get tags for this contact
        tag_rows = conn.execute(
            """SELECT t.name FROM tags t
               JOIN contact_tags ct ON ct.tag_id = t.id
               WHERE ct.contact_id = ?""",
            (contact_id,),
        ).fetchall()
        tags = [t["name"] for t in tag_rows]

        # Build last message preview
        last_content = ""
        lmt = row["last_msg_media_type"]
        if row["last_msg_content"] is not None:
            if lmt == "image":
                last_content = (row["last_msg_content"] or "")[:80] or "\U0001f4f7 Imagem"
            elif lmt == "audio":
                last_content = "\U0001f3a4 Áudio"
            else:
                last_content = (row["last_msg_content"] or "")[:80]

        is_group = bool(row["is_group"])
        group_name = row["group_name"] or ""
        name = group_name if is_group else (row["name"] or "")

        results.append({
            "id": contact_id,
            "phone": row["phone"],
            "name": name,
            "last_message": last_content,
            "last_message_role": row["last_msg_role"] or "",
            "last_message_ts": row["last_msg_ts"] or 0,
            "msg_count": row["msg_count"] or 0,
            "unread_count": row["unread_count"],
            "unread_ai_count": row["unread_ai_count"],
            "ai_enabled": bool(row["ai_enabled"]),
            "is_group": is_group,
            "group_name": group_name,
            "is_archived": bool(row["is_archived"]),
            "tags": tags,
            "updated_at": row["updated_at"],
        })

    if q:
        ql = q.lower()
        results = [
            c for c in results
            if ql in c["name"].lower()
            or ql in c["phone"]
            or ql in c.get("group_name", "").lower()
            or any(ql in t.lower() for t in c.get("tags", []))
        ]

    return results


def get_full_contact(phone: str) -> dict | None:
    """Get full contact data for API response (contact + info + observations)."""
    conn = get_db()
    row = conn.execute("SELECT * FROM contacts WHERE phone = ?", (phone,)).fetchone()
    if row is None:
        return None

    contact_id = row["id"]
    observations = get_observations(contact_id)

    tag_rows = conn.execute(
        """SELECT t.name FROM tags t
           JOIN contact_tags ct ON ct.tag_id = t.id
           WHERE ct.contact_id = ?""",
        (contact_id,),
    ).fetchall()
    tags = [t["name"] for t in tag_rows]

    data = _row_to_dict(row)
    data["info"] = {
        "name": row["name"],
        "email": row["email"],
        "profession": row["profession"],
        "company": row["company"],
        "address": row["address"],
        "observations": observations,
    }
    data["tags"] = tags
    return data


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict with Python types."""
    return {
        "id": row["id"],
        "phone": row["phone"],
        "name": row["name"],
        "email": row["email"],
        "profession": row["profession"],
        "company": row["company"],
        "address": row["address"],
        "ai_enabled": bool(row["ai_enabled"]),
        "is_group": bool(row["is_group"]),
        "group_name": row["group_name"],
        "is_archived": bool(row["is_archived"]),
        "unread_count": row["unread_count"],
        "unread_ai_count": row["unread_ai_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
