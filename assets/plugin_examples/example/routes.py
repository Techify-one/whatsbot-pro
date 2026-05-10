"""Example plugin REST endpoints, mounted under ``/api/plugins/example``."""

from fastapi import APIRouter

from db.connection import get_db

router = APIRouter()


@router.get("/pings")
async def list_pings(limit: int = 50):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, phone, note, ts FROM plugin_example_pings "
        "ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {"ok": True, "data": [dict(r) for r in rows]}


@router.delete("/pings")
async def clear_pings():
    conn = get_db()
    conn.execute("DELETE FROM plugin_example_pings")
    conn.commit()
    return {"ok": True}
