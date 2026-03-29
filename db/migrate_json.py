"""Migrate existing JSON files to SQLite database.

Can be run standalone: python -m db.migrate_json
Or called from main.py on first boot.
"""

import json
import logging
import time
from pathlib import Path

from db.connection import get_db

logger = logging.getLogger(__name__)


def needs_migration(data_dir: Path) -> bool:
    """Check if migration is needed: DB is empty and JSON files exist."""
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    if count > 0:
        return False
    # Check if there are JSON contact files
    contacts_dir = data_dir / "contacts"
    if not contacts_dir.exists():
        return False
    return any(
        f.suffix == ".json" and not f.stem.startswith("_")
        for f in contacts_dir.iterdir()
    )


def migrate(data_dir: Path) -> None:
    """Migrate all JSON data to SQLite."""
    conn = get_db()

    # Double-check we haven't already migrated
    count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    if count > 0:
        logger.info("Database already has data, skipping migration")
        return

    logger.info("Starting JSON → SQLite migration...")
    start = time.time()

    contacts_migrated = 0
    messages_migrated = 0
    usage_migrated = 0
    tags_migrated = 0

    # --- Migrate config.json ---
    config_path = data_dir / "config.json"
    if not config_path.exists():
        # Docker path
        config_path = data_dir / "storages" / "config.json"
    if config_path.exists():
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            conn.executemany(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in config_data.items()],
            )
            logger.info("Migrated %d config keys", len(config_data))
        except Exception as e:
            logger.error("Failed to migrate config.json: %s", e)

    # --- Migrate _tags.json ---
    contacts_dir = data_dir / "contacts"
    tags_file = contacts_dir / "_tags.json"
    tag_name_to_id: dict[str, int] = {}
    if tags_file.exists():
        try:
            tags_data = json.loads(tags_file.read_text(encoding="utf-8"))
            for name, info in tags_data.items():
                color = info.get("color", "#6b7280")
                cur = conn.execute(
                    "INSERT INTO tags (name, color) VALUES (?, ?)", (name, color)
                )
                tag_name_to_id[name] = cur.lastrowid
                tags_migrated += 1
            logger.info("Migrated %d tags", tags_migrated)
        except Exception as e:
            logger.error("Failed to migrate _tags.json: %s", e)

    # --- Migrate contact files ---
    if contacts_dir.exists():
        for f in sorted(contacts_dir.glob("*.json")):
            if f.stem.startswith("_"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                phone = data.get("phone", f.stem)
                info = data.get("info", {})
                old_id = data.get("id")

                # Insert contact
                if old_id is not None:
                    cur = conn.execute(
                        """INSERT INTO contacts
                           (id, phone, name, email, profession, company, address,
                            ai_enabled, is_group, group_name, is_archived,
                            unread_count, unread_ai_count, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            old_id, phone,
                            info.get("name", ""),
                            info.get("email", ""),
                            info.get("profession", ""),
                            info.get("company", ""),
                            info.get("address", ""),
                            1 if data.get("ai_enabled", True) else 0,
                            1 if data.get("is_group", False) else 0,
                            data.get("group_name", ""),
                            1 if data.get("is_archived", False) else 0,
                            data.get("unread_count", 0),
                            data.get("unread_ai_count", 0),
                            data.get("created_at", time.time()),
                            data.get("updated_at", time.time()),
                        ),
                    )
                    contact_id = old_id
                else:
                    cur = conn.execute(
                        """INSERT INTO contacts
                           (phone, name, email, profession, company, address,
                            ai_enabled, is_group, group_name, is_archived,
                            unread_count, unread_ai_count, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            phone,
                            info.get("name", ""),
                            info.get("email", ""),
                            info.get("profession", ""),
                            info.get("company", ""),
                            info.get("address", ""),
                            1 if data.get("ai_enabled", True) else 0,
                            1 if data.get("is_group", False) else 0,
                            data.get("group_name", ""),
                            1 if data.get("is_archived", False) else 0,
                            data.get("unread_count", 0),
                            data.get("unread_ai_count", 0),
                            data.get("created_at", time.time()),
                            data.get("updated_at", time.time()),
                        ),
                    )
                    contact_id = cur.lastrowid

                # Observations
                observations = info.get("observations", [])
                if observations:
                    now = time.time()
                    conn.executemany(
                        "INSERT INTO observations (contact_id, text, created_at) VALUES (?, ?, ?)",
                        [(contact_id, obs, now) for obs in observations if obs],
                    )

                # Messages (bulk insert)
                messages = data.get("messages", [])
                if messages:
                    conn.executemany(
                        """INSERT INTO messages
                           (contact_id, role, content, ts, media_type, media_path, status, msg_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        [
                            (
                                contact_id,
                                m.get("role", "user"),
                                m.get("content", ""),
                                m.get("ts", 0),
                                m.get("media_type"),
                                m.get("media_path"),
                                m.get("status"),
                                m.get("msg_id"),
                            )
                            for m in messages
                        ],
                    )
                    messages_migrated += len(messages)

                # Usage (bulk insert)
                usage_records = data.get("usage", [])
                if usage_records:
                    conn.executemany(
                        """INSERT INTO usage
                           (contact_id, call_type, model, prompt_tokens,
                            completion_tokens, total_tokens, cost_usd, ts)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        [
                            (
                                contact_id,
                                u.get("call_type", "text"),
                                u.get("model", ""),
                                u.get("prompt_tokens", 0),
                                u.get("completion_tokens", 0),
                                u.get("total_tokens", 0),
                                u.get("cost_usd", 0.0),
                                u.get("ts", 0),
                            )
                            for u in usage_records
                        ],
                    )
                    usage_migrated += len(usage_records)

                # Tags
                contact_tags = data.get("tags", [])
                for tag_name in contact_tags:
                    tag_id = tag_name_to_id.get(tag_name)
                    if tag_id:
                        conn.execute(
                            "INSERT OR IGNORE INTO contact_tags (contact_id, tag_id) VALUES (?, ?)",
                            (contact_id, tag_id),
                        )

                # Unread msg_ids
                unread_ids = data.get("unread_msg_ids", [])
                if unread_ids:
                    conn.executemany(
                        "INSERT INTO unread_msg_ids (contact_id, msg_id) VALUES (?, ?)",
                        [(contact_id, mid) for mid in unread_ids],
                    )

                contacts_migrated += 1

            except Exception as e:
                logger.error("Failed to migrate contact %s: %s", f.name, e)

    conn.commit()
    elapsed = time.time() - start
    logger.info(
        "Migration complete in %.1fs: %d contacts, %d messages, %d usage records, %d tags",
        elapsed, contacts_migrated, messages_migrated, usage_migrated, tags_migrated,
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    data_dir = Path(__file__).resolve().parent.parent
    from db.connection import init_db
    storages_dir = data_dir / "storages"
    storages_dir.mkdir(exist_ok=True)
    init_db(storages_dir / "whatsbot.db")
    migrate(data_dir)
