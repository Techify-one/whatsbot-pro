"""Example plugin tool — counts how many times the contact has triggered it."""

import logging

from db.connection import get_db

logger = logging.getLogger(__name__)


PING_TOOL = {
    "type": "function",
    "function": {
        "name": "example_ping",
        "description": (
            "Tool de demonstração do plugin Exemplo. "
            "Use APENAS quando o usuário pedir 'ping de teste do exemplo' ou "
            "explicitamente solicitar acionar o plugin de exemplo. "
            "Não use em conversas normais."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "Nota opcional anexada ao ping",
                },
            },
            "required": [],
        },
    },
}


def execute_ping(ctx, args: dict) -> str | None:
    """Increment a per-contact counter and return a confirmation string."""
    phone = ctx.contact.phone
    note = (args or {}).get("note", "")
    conn = get_db()
    conn.execute(
        "INSERT INTO plugin_example_pings (phone, note, ts) VALUES (?, ?, strftime('%s', 'now'))",
        (phone, note),
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM plugin_example_pings WHERE phone = ?", (phone,)
    ).fetchone()["n"]
    logger.info("example_ping for %s (count=%d)", phone, count)
    return f"Ping registrado. Este contato já enviou {count} ping(s) ao plugin de exemplo."


CORE_TOOLS = [
    (PING_TOOL, execute_ping),
]
