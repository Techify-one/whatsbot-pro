"""Estado por (mapeamento, contato): o que os dois lados valiam na última vez.

É a memória que transforma "os valores estão diferentes" em "QUEM mudou" — sem
ela o motor não distingue uma edição de um lado de uma edição do outro, e todo
par divergente viraria conflito.

Guarda HASH, nunca valor (ver ``field_codec.hash_value``). E distingue ``NULL``
(nunca observamos) de ``''`` (observamos, estava vazio): ver a discussão em
``sync_core.decide``.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import text

from plugins.context import make_plugin_db

logger = logging.getLogger("plugins.trackify.sync_state")

_COLS = ("map_id, contact_id, trackify_contact_id, wb_hash, tk_hash, rejected_hash, "
         "conflict, conflict_reason, wb_changed_at, tk_changed_at, last_push_at, "
         "last_pull_at, last_error")


def load_for_contacts(contact_ids: list[int]) -> dict[tuple[int, int], dict]:
    """``{(map_id, contact_id): estado}`` para um lote. Uma consulta só — o
    caminho de push e a varredura chamam isto por lote justamente para não
    fazer N+1 na tabela."""
    if not contact_ids:
        return {}
    with make_plugin_db() as conn:
        rows = conn.execute(
            text(f"SELECT {_COLS} FROM plugin_trackify_field_state "
                 f"WHERE contact_id = ANY(:ids)"),
            {"ids": list(contact_ids)}).mappings().all()
    return {(r["map_id"], r["contact_id"]): dict(r) for r in rows}


def record(map_id: int, contact_id: int, *, wb_hash: str, tk_hash: str,
           trackify_contact_id: str = "", conflict: bool = False,
           conflict_reason: str = "", rejected_hash: str | None = None,
           pushed: bool = False, pulled: bool = False,
           changed_at: float | None = None, error: str = "") -> None:
    """Carimba a convergência (ou o conflito) do par.

    ``rejected_hash`` só é ESCRITO quando vem preenchido: passar ``None`` mantém
    o valor anterior. Zerá-lo por descuido faria o mesmo valor recusado voltar a
    ser tentado em todo ciclo.
    """
    now = time.time()
    # ``wb_changed_at``/``tk_changed_at`` decididos em PYTHON, não num
    # ``CASE WHEN :param IS NULL`` no SQL: o Postgres não consegue inferir o
    # tipo de um parâmetro que só aparece comparado a NULL e recusa a consulta
    # inteira ("could not determine data type of parameter").
    wb_chg = changed_at if (changed_at is not None and not pulled) else None
    tk_chg = changed_at if (changed_at is not None and pulled) else None
    params = {
        "m": map_id, "c": contact_id, "tk": trackify_contact_id or "",
        "wb_h": wb_hash, "tk_h": tk_hash,
        "conf": 1 if conflict else 0, "reason": (conflict_reason or "")[:300],
        "rej": rejected_hash,
        "push_at": now if pushed else None,
        "pull_at": now if pulled else None,
        "wb_chg": wb_chg, "tk_chg": tk_chg,
        "err": (error or "")[:500],
        "now": now,
    }
    sql = (
        "INSERT INTO plugin_trackify_field_state "
        "(map_id, contact_id, trackify_contact_id, wb_hash, tk_hash, rejected_hash, "
        " conflict, conflict_reason, wb_changed_at, tk_changed_at, last_push_at, "
        " last_pull_at, last_error, created_at, updated_at) "
        "VALUES (:m, :c, :tk, :wb_h, :tk_h, :rej, :conf, :reason, :wb_chg, :tk_chg, "
        "        :push_at, :pull_at, :err, :now, :now) "
        "ON CONFLICT (map_id, contact_id) DO UPDATE SET "
        "  trackify_contact_id = CASE WHEN :tk = '' "
        "      THEN plugin_trackify_field_state.trackify_contact_id ELSE :tk END, "
        "  wb_hash = :wb_h, tk_hash = :tk_h, "
        "  rejected_hash = COALESCE(:rej, plugin_trackify_field_state.rejected_hash), "
        "  conflict = :conf, conflict_reason = :reason, "
        "  wb_changed_at = COALESCE(:wb_chg, plugin_trackify_field_state.wb_changed_at), "
        "  tk_changed_at = COALESCE(:tk_chg, plugin_trackify_field_state.tk_changed_at), "
        "  last_push_at = COALESCE(:push_at, plugin_trackify_field_state.last_push_at), "
        "  last_pull_at = COALESCE(:pull_at, plugin_trackify_field_state.last_pull_at), "
        "  last_error = :err, updated_at = :now"
    )
    try:
        with make_plugin_db() as conn:
            conn.execute(text(sql), params)
    except Exception:  # noqa: BLE001
        # Perder um carimbo custa uma redetecção no próximo ciclo, não o dado —
        # por isso não levanta. Mas é WARNING, não debug: um carimbo que nunca
        # grava faz a sincronização reenviar o mesmo valor para sempre, e em
        # debug isso passa despercebido.
        logger.warning("trackify: falha ao gravar estado de sincronização",
                       exc_info=True)


def counters() -> dict:
    """Números do card de status."""
    try:
        with make_plugin_db() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) AS total, "
                "       COUNT(*) FILTER (WHERE conflict = 1) AS conflitos, "
                "       COUNT(*) FILTER (WHERE last_error <> '') AS com_erro "
                "FROM plugin_trackify_field_state")).mappings().first()
        return dict(row or {})
    except Exception:  # noqa: BLE001
        return {"total": 0, "conflitos": 0, "com_erro": 0}


def conflicts(limit: int = 50) -> list[dict]:
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(
                f"SELECT {_COLS}, updated_at FROM plugin_trackify_field_state "
                f"WHERE conflict = 1 ORDER BY updated_at DESC LIMIT :lim"),
                {"lim": limit}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def clear_conflict(map_id: int, contact_id: int) -> None:
    try:
        with make_plugin_db() as conn:
            conn.execute(text(
                "UPDATE plugin_trackify_field_state SET conflict = 0, "
                " conflict_reason = '', updated_at = :now "
                "WHERE map_id = :m AND contact_id = :c"),
                {"m": map_id, "c": contact_id, "now": time.time()})
    except Exception:  # noqa: BLE001
        logger.debug("trackify: falha ao limpar conflito", exc_info=True)


# ── Cursores ─────────────────────────────────────────────────────────────

def get_cursor(name: str) -> dict:
    try:
        with make_plugin_db() as conn:
            row = conn.execute(text(
                "SELECT cursor_ts, cursor_id, note FROM plugin_trackify_sync_cursor "
                "WHERE name = :n"), {"n": name}).mappings().first()
    except Exception:  # noqa: BLE001
        row = None
    return dict(row) if row else {"cursor_ts": 0.0, "cursor_id": "", "note": ""}


def set_cursor(name: str, cursor_ts: float, cursor_id: str = "", note: str = "") -> None:
    try:
        with make_plugin_db() as conn:
            conn.execute(text(
                "INSERT INTO plugin_trackify_sync_cursor "
                "(name, cursor_ts, cursor_id, note, updated_at) "
                "VALUES (:n, :ts, :cid, :note, :now) "
                "ON CONFLICT (name) DO UPDATE SET cursor_ts = :ts, cursor_id = :cid, "
                " note = :note, updated_at = :now"),
                {"n": name, "ts": float(cursor_ts), "cid": cursor_id or "",
                 "note": (note or "")[:200], "now": time.time()})
    except Exception:  # noqa: BLE001
        logger.debug("trackify: falha ao gravar cursor", exc_info=True)
