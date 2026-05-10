"""Example prompt fragment injected into the system prompt."""

from db.connection import get_db


def ping_count_fragment(contact, ctx) -> str:
    """Inject a small note about how many pings this contact already triggered."""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM plugin_example_pings WHERE phone = ?",
            (contact.phone,),
        ).fetchone()
        n = (row["n"] if row else 0) or 0
    except Exception:
        return ""
    if n == 0:
        return ""
    return (
        f"\n\n--- Plugin Exemplo ---\n"
        f"Este contato já acionou example_ping {n} vez(es) anteriormente.\n"
        f"--- Fim ---"
    )


PROMPT_FRAGMENTS = [ping_count_fragment]
