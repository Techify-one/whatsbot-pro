"""Repository for messages table."""

from __future__ import annotations

import json
import time

from sqlalchemy import and_, delete as sa_delete, insert as sa_insert, select, update as sa_update

from db.engine import get_engine
from db.repositories._mapping import coerce_json
from db.tables import messages


def add(contact_id: int, role: str, content: str, *,
        media_type: str | None = None, media_path: str | None = None,
        media_caption: str | None = None,
        status: str | None = None, msg_id: str | None = None,
        reply_to_msg_id: str | None = None,
        conversation_id: int | None = None,
        sent_by_user_id: int | None = None,
        sent_by_name: str | None = None,
        agent_key: str | None = None,
        execution_id: int | None = None,
        ts: float | None = None) -> dict:
    """Insert a message and return it as a dict.

    conversation_id (plano 01 Fase 2) liga a mensagem à thread de atendimento;
    aditivo e nullable — contact_id permanece a chave de pertencimento.
    ``agent_key`` atribui a mensagem ao agente de IA que a produziu (resposta ou
    tool_call); NULL para mensagens não-IA.
    ``execution_id`` (plano 51) liga a resposta da IA à execução que a produziu
    (FK lógica, nullable) — link O(1) msg→execution; NULL cai no fuzzy legado.
    ``media_caption`` (plano 87) guarda a legenda VERBATIM que o cliente digitou
    junto da mídia. Fica fora do ``content`` de propósito: o content é reescrito
    depois pela descrição/transcrição da IA e deixa de ser separável. NULL =
    mídia sem legenda (ou mensagem que não é mídia).
    """
    ts = ts or time.time()
    with get_engine().begin() as conn:
        result = conn.execute(sa_insert(messages).values(
            contact_id=contact_id,
            role=role,
            content=content,
            ts=ts,
            media_type=media_type,
            media_path=media_path,
            media_caption=media_caption,
            status=status,
            msg_id=msg_id,
            reply_to_msg_id=reply_to_msg_id,
            conversation_id=conversation_id,
            sent_by_user_id=sent_by_user_id,
            sent_by_name=sent_by_name,
            agent_key=agent_key,
            execution_id=execution_id,
        ))
        new_id = result.inserted_primary_key[0]
    return {
        "id": new_id,
        "role": role,
        "content": content,
        "ts": ts,
        "media_type": media_type,
        "media_path": media_path,
        "media_caption": media_caption,
        "status": status,
        "msg_id": msg_id,
        "reply_to_msg_id": reply_to_msg_id,
        "conversation_id": conversation_id,
        "sent_by_name": sent_by_name,
        "agent_key": agent_key,
        "execution_id": execution_id,
    }


def find_execution_for_message(msg_row: dict) -> dict | None:
    """Resolve a execução que produziu uma resposta da IA via o link preciso.

    Usa ``messages.execution_id`` (plano 51 F1) → ``execution_repo.get_by_id``.
    Devolve None quando a linha é legada (execution_id NULL) ou a execução foi
    pruneada — o consumidor decide se cai no match fuzzy por janela de ts (DL2).
    Nunca lança: degradar é o contrato.
    """
    exec_id = (msg_row or {}).get("execution_id")
    if not exec_id:
        return None
    try:
        from db.repositories import execution_repo
        return execution_repo.get_by_id(int(exec_id))
    except Exception:
        return None


def get_all(contact_id: int, *, limit: int | None = None,
            before_id: int | None = None) -> list[dict]:
    """Return messages for a contact ordered by timestamp (oldest→newest).

    ``limit`` (plano 50): keyset pagination for the chat panel. When set, fetch the
    newest ``limit`` rows (``ts DESC, id DESC``) — optionally only those *older* than
    ``before_id`` (``id < before_id``) — and return them chronological. ``limit=None``
    ⇒ the historical path (all rows, ``ORDER BY ts``), byte-for-byte identical, for
    internal callers that need the whole thread.
    """
    cond = messages.c.contact_id == contact_id
    if before_id is not None:
        cond = cond & (messages.c.id < before_id)
    return _select_messages(cond, limit)


def get_by_conversation(conversation_id: int, *, limit: int | None = None,
                        before_id: int | None = None) -> list[dict]:
    """Return messages for ONE conversation ordered by ts (plano 11 D1).

    Conversa-cêntrico: filtra por ``conversation_id`` (uma thread por canal),
    ao contrário de :func:`get_all` que casa por ``contact_id`` e funde todos os
    canais do mesmo número. Usa ``idx_msg_conversation_ts`` (db/tables.py).

    ``limit``/``before_id`` (plano 50): keyset como em :func:`get_all` — página das
    ``limit`` mais recentes (+ ``id < before_id`` p/ "carregar anteriores"), devolvida
    cronológica. ``limit=None`` ⇒ thread inteira (byte-idêntico ao caminho legado).
    """
    cond = messages.c.conversation_id == conversation_id
    if before_id is not None:
        cond = cond & (messages.c.id < before_id)
    return _select_messages(cond, limit)


def _select_messages(cond, limit: int | None) -> list[dict]:
    """Shared SELECT for the chat panel readers (:func:`get_all`/:func:`get_by_conversation`).

    ``limit=None`` ⇒ all rows ``ORDER BY ts`` (legacy, chronological). ``limit`` set ⇒
    the newest ``limit`` rows via ``ts DESC, id DESC`` (id desempata ts empatado —
    keyset estável, plano 50 Riscos), revertidas para cronológico (oldest→newest).
    """
    with get_engine().connect() as conn:
        if limit is None:
            rows = conn.execute(
                select(messages).where(cond).order_by(messages.c.ts)
            ).mappings().all()
            return [_row_to_dict(r) for r in rows]
        rows = conn.execute(
            select(messages).where(cond)
            .order_by(messages.c.ts.desc(), messages.c.id.desc())
            .limit(limit)
        ).mappings().all()
    return [_row_to_dict(r) for r in reversed(rows)]


def _fetch_limit(limit: int, exclude) -> int:
    """Rows to read from SQL: ``limit`` normally, an over-fetch window when a regex
    blacklist is active (plano 43 D4) so the Python cut can't shrink below ``limit``.

    Imported lazily to keep the db layer free of a load-time dependency on ``agent``.
    """
    if not exclude:
        return limit
    from agent.history_filter import HISTORY_FETCH_CAP
    return max(limit, HISTORY_FETCH_CAP)


def _apply_exclude(rows, limit: int, exclude) -> list[dict]:
    """Turn newest-first DB rows into the chronological LLM-context list.

    ``exclude`` empty ⇒ the historical path (``reversed`` of exactly ``limit`` rows),
    byte-for-byte identical. Otherwise: apply the regex blacklist to the over-fetched
    newest-first rows, keep the newest ``limit`` survivors, return oldest→newest.
    """
    if not exclude:
        return [_row_to_dict(r) for r in reversed(rows)]
    from agent import history_filter
    dicts_newest_first = [_row_to_dict(r) for r in rows]
    survivors = history_filter.filter_rows(dicts_newest_first, exclude)[:limit]
    return list(reversed(survivors))


def get_context(contact_id: int, limit: int, *, exclude=None) -> list[dict]:
    """Return the last N eligible messages for LLM context.

    ``exclude`` (plano 43): a list of compiled regex patterns from
    ``agent.history_filter.load_compiled()``. When set (non-empty), a row whose
    ``role<TAB>content`` matches one is cut from the LLM history. Because the regex
    can't run in SQL, the query OVER-FETCHES up to ``HISTORY_FETCH_CAP`` rows, filters
    in Python, then keeps the newest ``limit`` survivors — so cutting rows never shrinks
    the window below ``limit``. ``None``/empty ⇒ the historical path (SQL ``LIMIT
    limit``), byte-for-byte identical.
    """
    # "error" incluído (plano 31 review): cards painel-only de erro persistidos
    # (reply vazio F4, falha de resolução de agente) não podem chegar ao LLM —
    # OpenAI/AGNO só aceitam system/user/assistant/tool e o turno crasharia.
    excluded = ("transcription", "tool_call", "system_notice",
                "conversation_event", "system", "error")
    fetch_limit = _fetch_limit(limit, exclude)
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(messages)
            .where(
                (messages.c.contact_id == contact_id)
                & (~messages.c.role.in_(excluded))
                & ((messages.c.status.is_(None)) | (messages.c.status != "failed"))
            )
            .order_by(messages.c.ts.desc())
            .limit(fetch_limit)
        ).mappings().all()
    return _apply_exclude(rows, limit, exclude)


def get_context_by_conversation(conversation_id: int, limit: int, *,
                                exclude=None) -> list[dict]:
    """Last N eligible messages of ONE conversation, LLM-context style.

    Mesmo filtro de elegibilidade de :func:`get_context` (roles painel-only e
    envios failed excluídos), mas escopado por ``conversation_id`` — multi-canal
    não mistura threads de outros canais (plano 31 F3; ``get_by_conversation``
    não filtra nem limita, por isso a variante dedicada).

    ``exclude`` (plano 43): idêntico ao de :func:`get_context` — over-fetch +
    lista-negra por regex + tail N. ``None``/vazio ⇒ caminho histórico byte-idêntico.
    """
    # "error" incluído (plano 31 review): cards painel-only de erro persistidos
    # (reply vazio F4, falha de resolução de agente) não podem chegar ao LLM —
    # OpenAI/AGNO só aceitam system/user/assistant/tool e o turno crasharia.
    excluded = ("transcription", "tool_call", "system_notice",
                "conversation_event", "system", "error")
    fetch_limit = _fetch_limit(limit, exclude)
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(messages)
            .where(
                (messages.c.conversation_id == conversation_id)
                & (~messages.c.role.in_(excluded))
                & ((messages.c.status.is_(None)) | (messages.c.status != "failed"))
            )
            .order_by(messages.c.ts.desc())
            .limit(fetch_limit)
        ).mappings().all()
    return _apply_exclude(rows, limit, exclude)


def get_tool_calls_by_conversation(conversation_id: int, limit: int = 50) -> list[dict]:
    """Return the ``tool_call`` cards of ONE conversation, oldest→newest (plano 37 A1).

    Deliberately does NOT reuse :func:`get_context`'s eligibility filter (which
    EXCLUDES role='tool_call'): this is the source for the compact tool-memory
    block that re-injects "what already ran this conversation" so the model stops
    re-running the same tools between turns. Scoped by ``conversation_id`` (one
    thread per channel), so a sibling channel's tools don't leak in.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(messages)
            .where(
                (messages.c.conversation_id == conversation_id)
                & (messages.c.role == "tool_call")
            )
            .order_by(messages.c.ts.desc())
            .limit(limit)
        ).mappings().all()
    return [_row_to_dict(r) for r in reversed(rows)]


def get_last(contact_id: int) -> dict | None:
    """Return the most recent message for a contact."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(messages)
            .where(messages.c.contact_id == contact_id)
            .order_by(messages.c.ts.desc())
            .limit(1)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def last_inbound_ts(*, conversation_id: int | None = None,
                    contact_id: int | None = None) -> float | None:
    """Timestamp of the most recent INBOUND (role='user') message.

    Scoped to a conversation when ``conversation_id`` is given (conversa-cêntrico —
    one 24h window per channel), else to the whole contact. Drives the WhatsApp
    Cloud 24h free-text window (``channels.outbound.session_open``). Returns None
    when there is no inbound yet (windowed channels then treat the window as closed).

    NOTE: counts only role='user' rows. An inbound *reaction* doesn't create such a
    row (it mutates the target message's ``reactions`` column), so a reaction alone
    won't reopen the window here — the operator falls back to a template, which is
    the safe failure. Acceptable: a reaction is normally preceded by a real message.
    """
    if conversation_id is not None:
        cond = (messages.c.role == "user") & (messages.c.conversation_id == conversation_id)
    elif contact_id is not None:
        cond = (messages.c.role == "user") & (messages.c.contact_id == contact_id)
    else:
        return None
    with get_engine().connect() as conn:
        ts = conn.execute(
            select(messages.c.ts).where(cond)
            .order_by(messages.c.ts.desc()).limit(1)
        ).scalar_one_or_none()
    return float(ts) if ts is not None else None


def get_last_user_message(contact_id: int,
                          conversation_id: int | None = None) -> dict | None:
    """Return the most recent user message (for updating with transcription etc).

    Plano 37 (B5): ``conversation_id`` escopa a busca àquela conversa/canal — num
    contato com áudio no Telegram e texto no GOWA na mesma janela, a transcrição
    deve atualizar a msg do canal certo, não a globalmente mais recente. Ausente →
    contact-global (comportamento legado)."""
    cond = (messages.c.contact_id == contact_id) & (messages.c.role == "user")
    if conversation_id is not None:
        cond = cond & (messages.c.conversation_id == conversation_id)
    with get_engine().connect() as conn:
        row = conn.execute(
            select(messages)
            .where(cond)
            .order_by(messages.c.ts.desc())
            .limit(1)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def update_content(message_id: int, content: str) -> None:
    """Update the content of a specific message."""
    with get_engine().begin() as conn:
        conn.execute(sa_update(messages).where(messages.c.id == message_id).values(content=content))


def mark_edited(message_id: int, content: str) -> float | None:
    """Edit a sent message: update its content and stamp ``edited_ts`` (epoch now).

    Used by the operator/AI message-edit flow. Returns the new ``edited_ts`` on a
    matched row (so the caller can broadcast it), or ``None`` if nothing matched."""
    if not message_id:
        return None
    ts = time.time()
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_update(messages).where(messages.c.id == message_id)
            .values(content=content, edited_ts=ts)
        )
    return ts if (result.rowcount or 0) > 0 else None


def update_status(contact_id: int, content: str, new_status: str | None,
                   msg_id: str | None = None) -> None:
    """Update status of the most recent message matching content (for retry-send)."""
    with get_engine().begin() as conn:
        target_id = conn.execute(
            select(messages.c.id)
            .where(
                (messages.c.contact_id == contact_id)
                & (messages.c.content == content)
                & (messages.c.status == "failed")
            )
            .order_by(messages.c.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        if target_id is None:
            return
        values = {"status": new_status}
        if msg_id:
            values["msg_id"] = msg_id
        conn.execute(sa_update(messages).where(messages.c.id == target_id).values(**values))


def update_status_by_msg_id(msg_id: str, new_status: str) -> list[str]:
    """Update delivery status by GOWA msg_id. Forward-only: sent → delivered → read.

    Does not overwrite 'operator' or 'failed' statuses. Cascades to all prior
    outgoing messages for the same contact (delivered/read are monotonic).
    """
    updated_msg_ids: list[str] = []
    with get_engine().begin() as conn:
        # Update the specific message.
        result = conn.execute(
            sa_update(messages)
            .where(
                (messages.c.msg_id == msg_id)
                & (messages.c.status.is_not(None))
                & (messages.c.status.in_(("sent", "delivered")))
            )
            .values(status=new_status)
        )
        if (result.rowcount or 0) > 0:
            updated_msg_ids.append(msg_id)

        # Find the anchor row to drive the cascade.
        anchor = conn.execute(
            select(messages.c.contact_id, messages.c.ts).where(messages.c.msg_id == msg_id)
        ).first()
        if anchor:
            prior_statuses = ("sent",) if new_status == "delivered" else ("sent", "delivered")
            # Gather IDs first so we can return them to the caller.
            prior_rows = conn.execute(
                select(messages.c.msg_id)
                .where(
                    (messages.c.contact_id == anchor.contact_id)
                    & (messages.c.role == "assistant")
                    & (messages.c.ts <= anchor.ts)
                    & (messages.c.status.in_(prior_statuses))
                    & (messages.c.msg_id.is_not(None))
                    & (messages.c.msg_id != msg_id)
                )
            ).all()
            cascaded = [r.msg_id for r in prior_rows]
            if cascaded:
                conn.execute(
                    sa_update(messages)
                    .where(
                        (messages.c.contact_id == anchor.contact_id)
                        & (messages.c.role == "assistant")
                        & (messages.c.ts <= anchor.ts)
                        & (messages.c.status.in_(prior_statuses))
                    )
                    .values(status=new_status)
                )
                updated_msg_ids.extend(cascaded)
    return updated_msg_ids


#: Outgoing statuses a delivery failure is allowed to overwrite.
#: ``read`` is deliberately absent (a message the recipient already read cannot
#: have failed) and so is ``failed`` itself (idempotency — see below).
_FAILABLE_STATUSES = ("sent", "delivered", "operator")


def mark_failed_by_msg_id(msg_id: str) -> dict | None:
    """Mark an OUTGOING message as ``failed`` by its provider msg_id (plano 75 F4).

    Overwrites ``sent`` / ``delivered`` / ``operator``. NEVER overwrites ``read``
    (already read by the recipient ⇒ it cannot have failed) nor ``failed``
    (idempotent — a second webhook for the same failure is a no-op).

    Unlike :func:`update_status_by_msg_id`, there is **no cascade**: delivery
    acks are monotonic across the thread, but a failure belongs to one specific
    message. It also cannot reuse that function, whose filter is
    ``status IN ('sent','delivered')`` — panel/template sends are stored as
    ``operator``, so reusing it would be a silent no-op.

    Returns the updated row (``id``, ``contact_id``, ``conversation_id``,
    ``content``, ``msg_id``) when the transition actually happened, and ``None``
    when it did NOT — i.e. the msg_id is unknown, the message was already
    ``failed``, or it is already ``read``. Callers therefore get the return value
    as their own dedup guard: a truthy row means "this failure is new, act on it"
    (e.g. write the error card once), ``None`` means "nothing changed, stay
    quiet". Distinguishing *unknown* from *not-transitioned* is intentionally
    left out of the return value; log the msg_id at the call site instead.
    """
    if not msg_id:
        return None
    with get_engine().begin() as conn:
        row = conn.execute(
            sa_update(messages)
            .where(
                (messages.c.msg_id == msg_id)
                & (messages.c.status.is_not(None))
                & (messages.c.status.in_(_FAILABLE_STATUSES))
            )
            .values(status="failed")
            .returning(
                messages.c.id,
                messages.c.contact_id,
                messages.c.conversation_id,
                messages.c.content,
                messages.c.msg_id,
            )
        ).mappings().first()
    return dict(row) if row else None


def exists_by_msg_id(msg_id: str) -> bool:
    """Is there ANY stored message with this provider ``msg_id``? (plano 75 F5)

    The companion :func:`mark_failed_by_msg_id` deliberately collapses "unknown
    msg_id" and "known but not transitioned" into a single ``None`` (see its
    docstring), which is exactly the distinction the failure webhook needs:

    * row exists ⇒ the ``None`` was a legitimate dedup (already ``failed`` or
      already ``read``) — stay quiet;
    * no row at all ⇒ the outgoing message has not been persisted **yet** (the
      reply loop writes every part only after sending all of them, so a part can
      stay sent-but-unsaved for a couple of seconds) — the caller may retry.

    Read-only and cheap (indexed lookup capped at one row); never raises for an
    empty ``msg_id``.
    """
    if not msg_id:
        return False
    with get_engine().connect() as conn:
        found = conn.execute(
            select(messages.c.id).where(messages.c.msg_id == msg_id).limit(1)
        ).first()
    return found is not None


def get_contact_id_by_msg_id(msg_id: str) -> int | None:
    """Look up the contact_id for a given GOWA msg_id."""
    with get_engine().connect() as conn:
        cid = conn.execute(
            select(messages.c.contact_id).where(messages.c.msg_id == msg_id).limit(1)
        ).scalar_one_or_none()
    return cid


def update_msg_id_and_status(message_id: int, msg_id: str, status: str) -> None:
    """Set msg_id and status on a message (used after retry-send)."""
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(messages).where(messages.c.id == message_id).values(msg_id=msg_id, status=status)
        )


def delete_all(contact_id: int) -> None:
    """Delete all messages for a contact."""
    with get_engine().begin() as conn:
        conn.execute(sa_delete(messages).where(messages.c.contact_id == contact_id))


def get_by_db_id(message_id: int) -> dict | None:
    """Row by internal db id (plano 51: trace por mensagem selecionada —
    recupera ``execution_id``/``agent_key`` de uma mensagem marcada)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(messages).where(messages.c.id == int(message_id))
        ).mappings().first()
    return _row_to_dict(row) if row else None


def get_by_msg_id(msg_id: str) -> dict | None:
    """Look up a single message by its GOWA msg_id."""
    if not msg_id:
        return None
    with get_engine().connect() as conn:
        row = conn.execute(
            select(messages).where(messages.c.msg_id == msg_id).limit(1)
        ).mappings().first()
    return _row_to_dict(row) if row else None


# `revoked` column encodes WHICH kind of deletion happened, so the UI can show a
# scope-specific label. 0 = not revoked, 1 = "para todos" (delete for everyone),
# 2 = "para mim" (delete for me). Old rows revoked before this distinction existed
# carry 1 and read as "para todos".
_REVOKE_CODE = {"all": 1, "me": 2}


def mark_revoked(msg_id: str, scope: str = "all") -> bool:
    """Mark a message as revoked by its GOWA msg_id. Covers both 'delete for me'
    (scope='me') and 'delete for everyone' (scope='all') — in both cases the message
    is removed from WhatsApp but KEPT in our DB (content/media preserved) so the panel
    still shows it with a scope-specific 'deleted' indicator. Returns True if matched."""
    if not msg_id:
        return False
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_update(messages)
            .where(messages.c.msg_id == msg_id)
            .values(revoked=_REVOKE_CODE.get(scope, 1))
        )
    return (result.rowcount or 0) > 0


def mark_revoked_by_id(message_id: int, scope: str = "me") -> bool:
    """Mark a message as revoked by its DB primary key (for local messages without a
    msg_id, e.g. failed sends / private notes). Content is kept; the row is never
    hard-deleted. Returns True if a row matched."""
    if not message_id:
        return False
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_update(messages).where(messages.c.id == message_id)
            .values(revoked=_REVOKE_CODE.get(scope, 2))
        )
    return (result.rowcount or 0) > 0


def set_reaction(msg_id: str, emoji: str, reactor: str) -> dict | None:
    """Set (or clear, when ``emoji`` is empty) ``reactor``'s reaction on a message.

    Each reactor holds at most one emoji (WhatsApp semantics): a new emoji replaces
    the prior one. Returns the updated ``{emoji: [reactor, ...]}`` map, or None if
    the message doesn't exist.
    """
    if not msg_id:
        return None
    with get_engine().begin() as conn:
        row = conn.execute(
            select(messages.c.id, messages.c.reactions).where(messages.c.msg_id == msg_id).limit(1)
        ).mappings().first()
        if row is None:
            return None
        data = coerce_json(row["reactions"], {})
        if not isinstance(data, dict):
            data = {}
        # Drop the reactor from every emoji (one reaction per person).
        for em in list(data.keys()):
            data[em] = [r for r in data[em] if r != reactor]
            if not data[em]:
                del data[em]
        if emoji:
            data.setdefault(emoji, [])
            if reactor not in data[emoji]:
                data[emoji].append(reactor)
        conn.execute(
            sa_update(messages).where(messages.c.id == row["id"]).values(
                reactions=json.dumps(data) if data else None
            )
        )
    return data


def _row_to_dict(row) -> dict:
    d = {
        "role": row["role"],
        "content": row["content"],
        "ts": row["ts"],
        "status": row["status"],
        "msg_id": row["msg_id"],
    }
    if row["media_type"]:
        d["media_type"] = row["media_type"]
    if row["media_path"]:
        d["media_path"] = row["media_path"]
    # plano 87: a legenda VERBATIM do cliente. Só sai quando existe — linha
    # legada (anterior à coluna) e mídia sem legenda continuam byte-idênticas,
    # e o painel cai no fallback conservador por prefixo.
    if row.get("media_caption"):
        d["media_caption"] = row["media_caption"]
    # `revoked` may be absent on very old rows read before the column existed.
    # 1 = "para todos", 2 = "para mim" (see _REVOKE_CODE).
    if row.get("revoked"):
        d["revoked"] = True
        d["revoke_scope"] = "me" if row["revoked"] == 2 else "all"
    if row.get("reactions"):
        parsed = coerce_json(row["reactions"], None)
        if parsed:
            d["reactions"] = parsed
    # `reply_to_msg_id` may be absent on old rows read before the column existed.
    if row.get("reply_to_msg_id"):
        d["reply_to_msg_id"] = row["reply_to_msg_id"]
    # Timestamp da última edição (operador/IA). Ausente/NULL em mensagens nunca
    # editadas → o painel só mostra "editada" quando presente.
    if row.get("edited_ts"):
        d["edited_ts"] = row["edited_ts"]
    # Nome (snapshot) do operador que enviou a mensagem manual. Ausente em linhas
    # legadas / echo / IA → o frontend cai em "Manual". Só expõe o nome (o
    # sent_by_user_id é interno e não vai ao cliente).
    if row.get("sent_by_name"):
        d["sent_by_name"] = row["sent_by_name"]
    # Agente de IA que produziu a mensagem (resposta/tool_call). Ausente em linhas
    # legadas / mensagens não-IA. O nome exibível é resolvido acima (rota/broadcast);
    # aqui expõe a chave crua para quem quiser resolver por conta própria.
    if row.get("agent_key"):
        d["agent_key"] = row["agent_key"]
    # Execução que produziu a resposta (plano 51 F1) — link O(1) msg→execution.
    # Ausente em linhas legadas / mensagens fora de turno rastreado.
    if row.get("execution_id"):
        d["execution_id"] = row["execution_id"]
    d["_id"] = row["id"]
    # conversa-cêntrico (plano 11): expõe a thread de pertencimento ao frontend,
    # para montar permalink de mensagem (/conversations/<id>?message=<_id>) mesmo
    # na visão contato-cêntrica/mesclada. nullable em linhas pré-plano-11.
    d["conversation_id"] = row["conversation_id"]
    return d


# Max chars kept from the quoted message body (plano 75 F10 / risco R11): the reply
# bubble only shows one truncated line, so the page payload must not carry the whole
# original message. The panel truncates again at render time (140) — this is the wire cap.
QUOTED_SNIPPET_MAX = 120


def get_by_msg_ids(msg_ids, *, conversation_id: int | None = None,
                   contact_id: int | None = None) -> dict[str, dict]:
    """Batch lookup of messages by their provider ``msg_id`` (plano 75 F10).

    Used to hydrate the ``quoted`` block of a reply whose target fell OUTSIDE the
    keyset page the panel loaded (bug C2): one indexed query per page instead of the
    client-side scan over the in-memory window (``idx_msg_id`` already exists in
    production — no DDL).

    Matching is by EXACT equality, never normalized: production holds four id shapes
    side by side (``wamid.…`` Cloud, ``WAID:wamid.…`` imported from Chatwoot,
    ``3EB0…`` GOWA, plain int Telegram) and each only needs to match itself
    (plano 75 F.P.#7 / R12 — stripping the ``WAID:`` prefix would break the replies
    that work today).

    A scope is MANDATORY (``conversation_id`` and/or ``contact_id``): without it an
    unrelated conversation's content would leak into the panel. Returns a map
    ``{msg_id: light_row}`` with only what the reply bubble renders — the body is
    truncated at :data:`QUOTED_SNIPPET_MAX` and no media path / raw payload is
    exposed. Ids with no match are simply absent from the map.
    """
    if conversation_id is None and contact_id is None:
        raise ValueError("get_by_msg_ids requires a conversation_id and/or contact_id scope")
    wanted = [m for m in dict.fromkeys(msg_ids or []) if m]
    if not wanted:
        return {}
    cond = messages.c.msg_id.in_(wanted)
    if conversation_id is not None:
        cond = cond & (messages.c.conversation_id == conversation_id)
    if contact_id is not None:
        cond = cond & (messages.c.contact_id == contact_id)
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(
                messages.c.id, messages.c.msg_id, messages.c.role, messages.c.content,
                messages.c.media_type, messages.c.media_caption,
                messages.c.status, messages.c.sent_by_name,
            ).where(cond).order_by(messages.c.id)
        ).mappings().all()
    out: dict[str, dict] = {}
    for r in rows:
        mid = r["msg_id"]
        if mid in out:
            # Same provider id twice (re-delivery/import): keep the oldest row — the
            # quote points at the message that already existed.
            continue
        body = r["content"] or ""
        out[mid] = {
            "_id": r["id"],
            "msg_id": mid,
            "role": r["role"],
            "content": body[:QUOTED_SNIPPET_MAX],
            "media_type": r["media_type"],
            # plano 87: o snippet da citação usa a legenda do cliente; sem ela o
            # painel mostrava a descrição gerada pela IA (que vem no `content`).
            "media_caption": (r["media_caption"] or "")[:QUOTED_SNIPPET_MAX] or None,
            # Cheap scalars the panel needs for the sender label of the quoted line
            # ("Manual"/operator name vs "IA"); no media path, no raw payload (R11).
            "status": r["status"],
            "sent_by_name": r["sent_by_name"],
        }
    return out
