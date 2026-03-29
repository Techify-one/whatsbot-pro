"""Repository for tags and contact_tags tables."""

from db.connection import get_db


def get_all() -> dict[str, dict]:
    """Return all tags as {name: {color: ...}} dict (matching old TagRegistry format)."""
    conn = get_db()
    rows = conn.execute("SELECT name, color FROM tags ORDER BY name").fetchall()
    return {r["name"]: {"color": r["color"]} for r in rows}


def get_by_name(name: str) -> dict | None:
    """Get a tag by name. Returns {id, name, color} or None."""
    conn = get_db()
    row = conn.execute("SELECT * FROM tags WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def create(name: str, color: str) -> bool:
    """Create a tag. Returns False if name already exists."""
    conn = get_db()
    existing = conn.execute("SELECT 1 FROM tags WHERE name = ?", (name,)).fetchone()
    if existing:
        return False
    conn.execute("INSERT INTO tags (name, color) VALUES (?, ?)", (name, color))
    conn.commit()
    return True


def update(old_name: str, *, new_name: str | None = None, color: str | None = None) -> bool:
    """Update a tag's name and/or color. Returns False if not found."""
    conn = get_db()
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (old_name,)).fetchone()
    if not row:
        return False
    if color:
        conn.execute("UPDATE tags SET color = ? WHERE name = ?", (color, old_name))
    if new_name and new_name != old_name:
        conn.execute("UPDATE tags SET name = ? WHERE name = ?", (new_name, old_name))
    conn.commit()
    return True


def delete(name: str) -> bool:
    """Delete a tag and remove it from all contacts. Returns False if not found."""
    conn = get_db()
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if not row:
        return False
    tag_id = row["id"]
    conn.execute("DELETE FROM contact_tags WHERE tag_id = ?", (tag_id,))
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()
    return True


def get_contact_tags(contact_id: int) -> list[str]:
    """Return tag names for a contact."""
    conn = get_db()
    rows = conn.execute(
        """SELECT t.name FROM tags t
           JOIN contact_tags ct ON ct.tag_id = t.id
           WHERE ct.contact_id = ?
           ORDER BY t.name""",
        (contact_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def set_contact_tags(contact_id: int, tag_names: list[str]) -> None:
    """Replace all tags for a contact with the given list."""
    conn = get_db()
    conn.execute("DELETE FROM contact_tags WHERE contact_id = ?", (contact_id,))
    for name in tag_names:
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            conn.execute(
                "INSERT OR IGNORE INTO contact_tags (contact_id, tag_id) VALUES (?, ?)",
                (contact_id, row["id"]),
            )
    conn.commit()


def add_contact_tag(contact_id: int, tag_name: str) -> None:
    """Add a single tag to a contact."""
    conn = get_db()
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
    if row:
        conn.execute(
            "INSERT OR IGNORE INTO contact_tags (contact_id, tag_id) VALUES (?, ?)",
            (contact_id, row["id"]),
        )
        conn.commit()


def remove_contact_tag(contact_id: int, tag_name: str) -> None:
    """Remove a single tag from a contact."""
    conn = get_db()
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
    if row:
        conn.execute(
            "DELETE FROM contact_tags WHERE contact_id = ? AND tag_id = ?",
            (contact_id, row["id"]),
        )
        conn.commit()
