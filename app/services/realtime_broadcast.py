"""Pure builders for real-time WebSocket payloads (plano 57).

Kept dependency-free so the payload shape can be unit-tested in isolation and
reused by both inbound save sites (batch text/media in ``messaging_service`` and
group-no-mention in ``message_ingest_service``) without a circular import.
"""

from __future__ import annotations


def build_inbound_saved_message(saved: dict,
                                *, supersedes: list[str] | None = None) -> dict:
    """Build the AUTHORITATIVE ``new_message.message`` payload emitted AFTER the
    inbound INSERT (plano 57).

    Fecha a janela "broadcast-antes-do-save": o ``new_message`` do ingest sai no
    t=0 (antes do INSERT, sem ``_id``); este sai DEPOIS, carregando a identidade
    real da linha (``_id``/``msg_id``/``ts`` do banco). O frontend reconcilia no
    lugar com a cópia otimista do t=0 via ``msg_id``/``_id`` (dedup), então a
    entrega dupla é inofensiva.

    ``supersedes`` = a lista de ``msg_id`` que um batch combinou nesta única linha
    (o batch junta N mensagens em ``"a\\nb"`` sob o ``msg_id`` da ÚLTIMA). O
    frontend usa isso para colapsar as bolhas otimistas individuais das mensagens
    anteriores na linha combinada. O ``msg_id`` da própria linha é excluído (ele
    reconcilia no lugar, preservando a posição da bolha).

    Args:
        saved: o dict retornado por ``message_repo.add`` / ``ContactMemory.add_message``
            (tem ``id``/``role``/``content``/``ts``/``msg_id``/``conversation_id``/…).
        supersedes: msg_ids combinados no batch (opcional).

    Returns:
        O dict ``message`` pronto para ``ws_manager.broadcast("new_message", …)``.
    """
    msg: dict = {
        "role": saved.get("role", "user"),
        "content": saved.get("content"),
        "ts": saved.get("ts"),
        "authoritative": True,
    }
    if saved.get("msg_id"):
        msg["msg_id"] = saved["msg_id"]
    if saved.get("id") is not None:
        msg["_id"] = saved["id"]
    if saved.get("conversation_id") is not None:
        msg["conversation_id"] = saved["conversation_id"]
    if saved.get("media_type"):
        msg["media_type"] = saved["media_type"]
        msg["media_path"] = saved.get("media_path")
    if saved.get("reply_to_msg_id"):
        msg["reply_to_msg_id"] = saved["reply_to_msg_id"]
    if supersedes:
        own = saved.get("msg_id")
        extra = [m for m in supersedes if m and m != own]
        if extra:
            msg["supersedes"] = extra
    return msg
