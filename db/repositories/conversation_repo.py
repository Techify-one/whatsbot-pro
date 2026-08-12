"""Repository for conversations — the atendimento thread (plano 01 Fase 1).

display_id is a human-friendly sequential id allocated atomically from
conversation_counters in the SAME transaction as the INSERT (P6).
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import (select, update, func, delete as sa_delete,
                        insert as sa_insert, case, exists, false as sa_false,
                        literal)
from sqlalchemy.exc import IntegrityError

from db.engine import get_engine
from db.repositories import conversation_query, conversation_label_repo
from db.tables import (conversations, contacts, conversation_counters,
                       inboxes, messages, unread_msg_ids, conversation_label_links,
                       mentions)

logger = logging.getLogger(__name__)

_COUNTER = "conversation_display_id"
DEFAULT_INBOX_ID = 1  # inbox semeado na migration 0013


def _next_display_id(conn) -> int:
    """Atomically reserve the next display_id within `conn`'s transaction."""
    current = conn.execute(
        select(conversation_counters.c.next_value)
        .where(conversation_counters.c.name == _COUNTER)
    ).scalar()
    if current is None:
        current = 1
        conn.execute(conversation_counters.insert().values(
            name=_COUNTER, next_value=current + 1))
    else:
        conn.execute(update(conversation_counters)
                     .where(conversation_counters.c.name == _COUNTER)
                     .values(next_value=current + 1))
    return current


def default_agent_key_for_inbox(inbox_id: int) -> str | None:
    """Agent a brand-new conversation is bound to so the panel shows "IA padrão"
    handling it from the start: the inbox's ``default_agent_key`` if configured,
    otherwise the global default AI agent. Best-effort — never blocks creation.

    Public (plano 23 Fase B4) so ``conversation_service`` can resolve the default
    agent for the AI-on transfer policy (which moved out of the repo)."""
    from db.repositories import inbox_repo, agent_repo
    try:
        inbox = inbox_repo.get(inbox_id)
        if inbox and inbox.get("default_agent_key"):
            return inbox["default_agent_key"]
    except Exception:
        logger.debug("conversation create: inbox default_agent_key lookup failed")
    # plano 36 + fix agente-padrão (2026-07): agente marcado como padrão de novas
    # conversas (is_default=1). Desde o fix, o RUNTIME também honra a marcação
    # (agent_repo.get_default), então carimbo e fallback são o mesmo agente.
    # Conversas em andamento não mudam. Só adota se existe e está habilitado.
    try:
        entry = agent_repo.get_new_conversation_default()
        if entry and entry.get("enabled") and entry.get("agent_key"):
            return entry["agent_key"]
    except Exception:
        logger.debug("conversation create: is_default agent lookup failed")
    # Piso legado: a chave literal "default", só se a row ainda existe e está
    # habilitada (pós-fix ela é excluível). Sem piso → None: a conversa nasce sem
    # carimbo e o runtime resolve o fallback do momento (self-healing). Antes o
    # retorno era o literal "default" incondicional — inclusive no ramo de exceção
    # acima, o que gravava um vínculo errado quando a consulta falhava.
    try:
        legacy = agent_repo.get(agent_repo.DEFAULT_AGENT_KEY)
        if legacy and legacy.get("enabled"):
            return agent_repo.DEFAULT_AGENT_KEY
    except Exception:
        logger.debug("conversation create: legacy default agent lookup failed")
    return None


def _default_ai_enabled() -> bool:
    """Whether brand-new conversations start with the AI active (config-driven).

    Plano 17: the per-conversation ``ai_active`` seed comes from the global
    ``default_ai_enabled`` toggle (no longer from ``contacts.ai_enabled``).
    Best-effort — defaults to True so a config read failure never silences the AI.
    """
    from db.repositories import config_repo
    try:
        return bool(config_repo.get("default_ai_enabled", True))
    except Exception:
        return True


def _global_ai_enabled() -> bool:
    """The panel-wide ``auto_reply`` master switch — the SAME gate the webhook
    checks first (``_channel_ai_enabled``). When it is OFF the AI never replies on
    any channel, so a brand-new conversation must NOT be stamped as AI-active nor
    bound to an agent — otherwise the sidebar shows the "IA" badge + an assigned
    agent for a conversation the AI will never touch. Best-effort → defaults to True
    so a config read failure never silences the AI.
    """
    from db.repositories import config_repo
    try:
        return bool(config_repo.get("auto_reply", True))
    except Exception:
        return True


def _insert_conversation(conn, *, inbox_id: int, contact_id: int, contact_inbox_id: int,
                         opened_at: float | None, ai_active: int | None,
                         is_archived: int, active_agent_key: str | None,
                         origin: str | None, status: str = "open",
                         assignee_user_id: int | None = None) -> dict:
    """Insert a conversation row on an EXISTING transaction ``conn`` and return it.

    Shared by :func:`create` (opens its own txn) and :func:`_create_open_atomic`
    (checks-then-inserts inside one txn to close the brand-new-contact race).
    ``status`` defaults to ``"open"``; the "ignorar abertura" rule inserts a
    brand-new thread already ``"closed"`` (plano regex: contato novo não abre atendimento).

    ``assignee_user_id`` (plano 71) is the channel's "atendente padrão para novas
    conversas" — a human user_id stamped ONLY at CREATE. ``None`` (default) keeps
    the legacy behaviour: the conversation nasce sem dono (fila "Não atribuídas").
    The caller that passes it also seeds ``ai_active=0`` (atendente humano ⇒ IA
    off ⇒ sem agente, pela regra abaixo); we do NOT re-derive that here."""
    now = time.time()
    opened = opened_at if opened_at is not None else now
    if ai_active is None:
        ai_active = 1 if _default_ai_enabled() else 0
    # Fix atribuição-IA-off (2026-07): o carimbo do agente segue o MESMO princípio
    # do gate global abaixo — uma conversa que nasce com a IA desligada (seed
    # per-canal/global de default_ai_enabled em 0) NÃO deve nascer "atribuída" a
    # um agente de IA que nunca vai respondê-la; sem vínculo ela cai na fila "Não
    # atribuídas". Religar a IA da conversa depois re-vincula o padrão
    # (conversation_service.set_ai ON). Um active_agent_key EXPLÍCITO do caller
    # continua respeitado independentemente do ai_active.
    if active_agent_key is None and ai_active:
        active_agent_key = default_agent_key_for_inbox(inbox_id)
    # Global master gate: with the panel-wide ``auto_reply`` switch OFF, no channel
    # ever replies (webhook checks it first), so a fresh conversation must start with
    # the AI off and NO agent bound — the UI must not advertise "IA"/an agent for a
    # thread the AI won't handle. This overrides the per-channel/default seeds above;
    # it applies ONLY at CREATE (this function is create-only). An explicit human
    # transfer to an agent goes through ``conversation_repo.update``, not here.
    if not _global_ai_enabled():
        ai_active = 0
        active_agent_key = None
    display_id = _next_display_id(conn)
    result = conn.execute(conversations.insert().values(
        display_id=display_id, inbox_id=inbox_id, contact_id=contact_id,
        contact_inbox_id=contact_inbox_id, status=status, is_archived=is_archived,
        ai_active=ai_active, active_agent_key=active_agent_key, origin=origin,
        assignee_user_id=assignee_user_id,
        opened_at=opened, last_activity_at=opened,
        custom_attributes={}, created_at=now, updated_at=now,
    ))
    conv_id = result.inserted_primary_key[0]
    row = conn.execute(
        select(conversations).where(conversations.c.id == conv_id)).mappings().first()
    return dict(row)


def create(*, inbox_id: int, contact_id: int, contact_inbox_id: int,
           opened_at: float | None = None, ai_active: int | None = None,
           is_archived: int = 0, active_agent_key: str | None = None,
           origin: str | None = None, status: str = "open") -> dict:
    """Insert a new conversation. ``origin`` (plano 28) records who started the
    thread — ``inbound`` (customer), ``outbound``/``manual`` (operator), ``imported``
    (chat import) — and drives the sidebar visibility gate. Always inserts — but at
    most ONE conversation per (contact, inbox) may be OPEN (partial unique index
    ``uq_atend_open_contact_inbox``, migration 0036; raises ``IntegrityError``
    otherwise; ``status="closed"`` está fora do índice, então múltiplas fechadas são
    permitidas). The inbound auto-create dedup lives in :func:`_create_open_atomic`."""
    with get_engine().begin() as conn:
        return _insert_conversation(
            conn, inbox_id=inbox_id, contact_id=contact_id,
            contact_inbox_id=contact_inbox_id, opened_at=opened_at,
            ai_active=ai_active, is_archived=is_archived,
            active_agent_key=active_agent_key, origin=origin, status=status)


def _create_open_atomic(*, inbox_id: int, contact_id: int, contact_inbox_id: int,
                        opened_at: float | None, origin: str | None,
                        ai_active_seed: int | None = None,
                        assignee_user_id_seed: int | None = None) -> tuple[dict, bool]:
    """Race-safe get-or-create of the OPEN conversation for (contact, inbox).

    Closes the brand-new-contact double-create: when two inbound messages of a
    contact with NO conversation yet resolve concurrently, both would otherwise
    ``create`` a thread. The re-check inside the write transaction catches the
    common case (the loser sees the winner's committed row and reuses it), but on
    Postgres READ COMMITTED two concurrent transactions can both pass it — the
    partial unique index ``uq_atend_open_contact_inbox`` (one OPEN conversation
    per contact/inbox, migration 0036) is the real backstop: the loser's INSERT
    raises ``IntegrityError`` and we adopt the winner's thread. Returns
    ``(conv, created)`` where ``created`` is False when an existing open thread was
    adopted. Only called when the pre-check already found no conversation at all, so
    the common path still inserts."""
    try:
        with get_engine().begin() as conn:
            existing = conn.execute(
                select(conversations)
                .where(conversations.c.contact_id == contact_id,
                       conversations.c.inbox_id == inbox_id,
                       conversations.c.status == "open")
                .order_by(conversations.c.last_activity_at.desc())
            ).mappings().first()
            if existing is not None:
                return dict(existing), False
            row = _insert_conversation(
                conn, inbox_id=inbox_id, contact_id=contact_id,
                contact_inbox_id=contact_inbox_id, opened_at=opened_at,
                ai_active=ai_active_seed, is_archived=0, active_agent_key=None,
                origin=origin, assignee_user_id=assignee_user_id_seed)
            return row, True
    except IntegrityError:
        # Loser of the open-conversation race (uq_atend_open_contact_inbox): the
        # winner committed between our re-check and INSERT. Adopt its thread.
        winner = get_open_for_contact_inbox(contact_id, inbox_id)
        if winner is None:
            raise
        logger.info("open-conversation race for contact=%s inbox=%s: adopted #%s",
                    contact_id, inbox_id, winner["id"])
        return winner, False


def get(conv_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations).where(conversations.c.id == conv_id)).mappings().first()
    return dict(row) if row else None


def assignment_of(conv_id: int) -> dict | None:
    """Só quem "possui" a conversa: ``{assignee_user_id, active_agent_key}``.

    Vai no payload do ``new_message`` do ingest para o painel decidir se toca o som
    de mensagem nova (só as minhas ou as sem dono). Duas colunas em vez do ``get()``
    inteiro porque isto roda no caminho quente de TODA mensagem recebida.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations.c.assignee_user_id, conversations.c.active_agent_key)
            .where(conversations.c.id == conv_id)).mappings().first()
    return dict(row) if row else None


def get_open_for_contact(contact_id: int) -> dict | None:
    """Most recent open conversation for a contact (the active thread)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations)
            .where(conversations.c.contact_id == contact_id,
                   conversations.c.status == "open")
            .order_by(conversations.c.last_activity_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def get_latest_for_contact(contact_id: int) -> dict | None:
    """Most recent conversation for a contact, regardless of status."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations)
            .where(conversations.c.contact_id == contact_id)
            .order_by(conversations.c.last_activity_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def get_open_for_contact_inbox(contact_id: int, inbox_id: int) -> dict | None:
    """Most recent open conversation for a contact WITHIN one inbox (plano 11 F1).

    A conversa é por-canal: o mesmo contato/número tem threads separadas por inbox
    (uma inbox por canal). Resolução de saída/agente usa esta visão por-inbox.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations)
            .where(conversations.c.contact_id == contact_id,
                   conversations.c.inbox_id == inbox_id,
                   conversations.c.status == "open")
            .order_by(conversations.c.last_activity_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def get_latest_for_contact_inbox(contact_id: int, inbox_id: int) -> dict | None:
    """Most recent conversation for a contact WITHIN one inbox, any status."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations)
            .where(conversations.c.contact_id == contact_id,
                   conversations.c.inbox_id == inbox_id)
            .order_by(conversations.c.last_activity_at.desc())
        ).mappings().first()
    return dict(row) if row else None


def get_open_for_contact_scoped(contact) -> dict | None:
    """Open conversation of a ``contact``-like object, scoped to ITS inbox (plano 37).

    Idioma central da correção "por-canal, não por-contato": recebe um objeto com
    ``.id`` e (idealmente) ``.inbox_id`` — todo ``ContactMemory``/``ctx.contact``
    tem ambos — e resolve a conversa aberta DAQUELE inbox, nunca a mais recente de
    qualquer canal. Fail-open (D2): um double de teste/legado sem ``inbox_id`` cai
    no resolver channel-blind, preservando byte-a-byte o comportamento antigo. É o
    ÚNICO ponto sancionado a chamar ``get_open_for_contact`` como fallback."""
    inbox_id = getattr(contact, "inbox_id", None)
    if inbox_id is not None:
        return get_open_for_contact_inbox(contact.id, inbox_id)
    return get_open_for_contact(contact.id)


def resolve_for_contact_ex(contact_id: int, jid: str, *, reopen_if_closed: bool = False,
                           opened_at: float | None = None,
                           inbox_id: int = DEFAULT_INBOX_ID,
                           origin: str | None = None,
                           create_closed: bool = False,
                           ai_active_seed: int | None = None,
                           assignee_user_id_seed: int | None = None) -> tuple[dict, str | None]:
    """Like :func:`resolve_for_contact` but also reports the lifecycle transition.

    Returns ``(conv, event)`` where ``event`` is ``"created"`` (a brand-new
    conversation was opened), ``"reopened"`` (an existing closed conversation was
    reactivated by this inbound), or ``None`` (already-open conversation, no
    transition). Lets callers surface an automatic system notice (plano 12 §3)
    without re-querying the prior status.

    ``create_closed`` (regra "ignorar abertura"): quando NÃO há conversa ainda para o
    contato/inbox, cria-a já FECHADA em vez de aberta — um contato novo cuja mensagem
    casa a regex de "não abrir protocolo" não deve abrir um atendimento. A conversa
    fechada mantém a mensagem salva/visível (aba "Fechado") sem card de sistema
    (``event=None``). Não afeta uma conversa já existente (aí o ramo abaixo decide
    reabrir/manter). Como a conversa nasce ``status="closed"``, fica fora do índice
    ``uq_atend_open_contact_inbox`` e não passa pelo dedup de :func:`_create_open_atomic`.

    ``origin`` (plano 28) stamps a brand-new conversation's provenance. Concurrency:
    if two inbound messages of a brand-new contact resolve in parallel, both see
    "no conversation" here — :func:`_create_open_atomic` re-checks + inserts inside
    ONE write transaction, and the partial unique index
    ``uq_atend_open_contact_inbox`` (one OPEN conversation per contact/inbox,
    migration 0036) backstops the Postgres race: the loser adopts the winner's
    thread (``event=None``) instead of creating a duplicate. Multiple CLOSED
    conversations per contact/inbox remain allowed (the atendimento model keeps
    the history).

    ``ai_active_seed`` (plano 38 F1) is the per-channel ``default_ai_enabled`` toggle
    (``1``/``0``) resolved by the caller (``ContactMemory`` already holds it). It seeds
    ``ai_active`` ONLY on a brand-new conversation (CREATE); a reopen never re-seeds
    (preserves a manual pause). ``None`` keeps the legacy global fallback
    (:func:`_default_ai_enabled`), so callers that don't pass it are byte-identical.

    ``assignee_user_id_seed`` (plano 71) is the channel's "atendente padrão para
    novas conversas" (a human user_id) resolved by the caller. Applies in DOIS
    momentos (P2 revisado 2026-07-21): (1) no nascimento — carimba a conversa nova
    (CREATE, event ``created``); (2) na REABERTURA — quando uma conversa fechada
    reabre "Não atribuída" (``assignee_user_id IS NULL``), reaplica o dono + IA off,
    NUNCA sobrescrevendo uma atribuição existente. Cobre o fluxo da plataforma externa (a 2ª
    dúvida do aluno reabre a mesma conversa que o fechamento deixou órfã). O reuse de
    uma conversa JÁ ABERTA não é tocado (respeita o estado vivo). ``None`` (default)
    ⇒ nunca carimba (fila "Não atribuídas"), byte-idêntico ao legado. O ramo
    ``create_closed`` da regra "ignorar abertura" não recebe dono.
    """
    from db.repositories import contact_inbox_repo
    ci = contact_inbox_repo.get_or_create(
        inbox_id=inbox_id, contact_id=contact_id, source_id=jid, source_jid=jid)
    conv = get_latest_for_contact_inbox(contact_id, inbox_id)
    if conv is None:
        if create_closed:
            row = create(
                inbox_id=inbox_id, contact_id=contact_id, contact_inbox_id=ci["id"],
                opened_at=opened_at, origin=origin, status="closed")
            return row, None  # sem card "Conversa iniciada": conversa nasce fechada
        row, created = _create_open_atomic(
            inbox_id=inbox_id, contact_id=contact_id, contact_inbox_id=ci["id"],
            opened_at=opened_at, origin=origin, ai_active_seed=ai_active_seed,
            assignee_user_id_seed=assignee_user_id_seed)
        return row, ("created" if created else None)
    if reopen_if_closed and conv["status"] == "closed":
        reopened = set_status(conv["id"], "open")
        # plano 71 (P2 revisado, 2026-07-21): se a conversa REABRIU "Não atribuída"
        # e o canal tem atendente padrão, aplica-o de novo (assignee + IA off + sem
        # agente) — espelha o estado de nascimento. Cobre o fluxo da plataforma externa: a 1ª
        # dúvida do aluno nasce atribuída ao Atendente X; ele fecha; a 2ª dúvida reabre
        # a MESMA conversa (que o close deixou órfã) e ela precisa voltar pro
        # Atendente X. NUNCA sobrescreve uma atribuição existente (só quando NULL), então
        # respeita uma reatribuição manual que tenha sobrevivido ao fechamento
        # (ex.: plano 67 mantém o dono). Reabrir uma conversa JÁ ABERTA (reuse) não
        # passa por aqui — não mexe no estado vivo.
        if (assignee_user_id_seed and reopened is not None
                and reopened.get("assignee_user_id") is None):
            reopened = _update(conv["id"], {
                "assignee_user_id": assignee_user_id_seed,
                "ai_active": 0,
                "active_agent_key": None,
            }) or reopened
        return reopened, "reopened"
    return conv, None


def resolve_for_contact(contact_id: int, jid: str, *, reopen_if_closed: bool = False,
                        opened_at: float | None = None,
                        inbox_id: int = DEFAULT_INBOX_ID,
                        origin: str | None = None,
                        create_closed: bool = False) -> dict:
    """Return the active conversation for a contact IN a given inbox (plano 01 F2 / plano 11 F1).

    Idempotent: get_or_creates the contact_inbox on ``inbox_id`` (JID = source_id,
    espelhando o backfill da migration 0013) e a conversa. ``inbox_id`` é resolvido
    do canal de origem (uma inbox por canal); o default mantém o GOWA inalterado.
    Quando reopen_if_closed e a última conversa daquela inbox está closed, reabre —
    uma nova mensagem inbound reativa o atendimento, sem misturar canais.

    Thin wrapper sobre :func:`resolve_for_contact_ex` (descarta o flag de transição)
    — mantido para os call sites que só querem a conversa.
    """
    conv, _event = resolve_for_contact_ex(
        contact_id, jid, reopen_if_closed=reopen_if_closed,
        opened_at=opened_at, inbox_id=inbox_id, origin=origin,
        create_closed=create_closed)
    conv = dict(conv)
    conv["created"] = (_event == "created")
    return conv


# Enriched-conversation SELECT assembly lives in ``conversation_query`` (plano 23
# Fase E2). Local aliases keep the existing call sites unchanged.
_enriched_columns = conversation_query.enriched_columns
_enriched_from = conversation_query.enriched_from
_finalize_conv = conversation_query.finalize_conv


def _notify_private_enabled() -> bool:
    """A conta optou por notificar mensagens privadas? (config global, default off).

    Quando ligada, a nota privada participa do preview/ordenação da sidebar (vira a
    "última mensagem": sobe ao topo + texto + cadeado). Espelha o mesmo gate usado em
    ``agent.memory`` para o badge verde. Defensivo — nunca quebra a listagem."""
    from db.repositories import config_repo
    try:
        return bool(config_repo.get("notify_private_messages", False))
    except Exception:
        return False


def _attach_labels(rows: list[dict]) -> list[dict]:
    """Enrich finalized conversation rows with their conversation-label names.

    Conversation labels are SEPARATE from contact tags (own registry). The sidebar
    filters by them client-side, so each row carries ``labels`` (list of names).
    Done in ONE batched query for the whole page.
    """
    ids = [r["id"] for r in rows if r.get("id") is not None]
    by_conv = conversation_label_repo.get_names_for_conversations(ids)
    for r in rows:
        r["labels"] = by_conv.get(r.get("id"), [])
    return rows


def _attach_contact_tags(rows: list[dict]) -> list[dict]:
    """Enrich rows com as TAGS DO CONTATO (nomes), em UMA query batch (plano 50 F8).

    Distinto de :func:`_attach_labels` (etiquetas da CONVERSA). O sidebar conversa-first
    filtra por `tag` (tags do contato) client-side, então cada row leva ``contact_tags``
    — assim o `convRowToSidebarRow` não precisa de um fetch de contatos à parte."""
    from db.repositories import contact_query
    ids = [r["contact_id"] for r in rows if r.get("contact_id") is not None]
    if not ids:
        for r in rows:
            r["contact_tags"] = []
        return rows
    with get_engine().connect() as conn:
        by_contact = contact_query.tags_by_contact(conn, ids)
    for r in rows:
        r["contact_tags"] = list(by_contact.get(r.get("contact_id"), []))
    return rows


def list_conversations(*, status: str | None = None, inbox_id: int | None = None,
                       assignee_user_id: int | None = None, is_archived: int | None = None,
                       inbox_ids: list[int] | None = None, current_user_id: int | None = None,
                       contact_ids: list[int] | None = None,
                       limit: int = 100, offset: int = 0) -> list[dict]:
    """List conversations with contact + channel info + last-message preview +
    per-conversation unread, pinned-first then newest. Feeds the conversa-cêntrica
    sidebar and the full-page conversation list (plano 11 D1).

    ``inbox_ids`` scopes visibility to a set of inboxes (inbox membership): ``None``
    means no scoping (sees all); an empty list means the user is a member of no
    inbox and sees nothing.

    ``contact_ids`` restringe aos atendimentos de um conjunto de contatos (mesma
    semântica de ``inbox_ids``: ``None`` = sem restrição, lista vazia = nada). É o
    que a BUSCA da barra lateral usa: sem ele, ela pedia as 200 conversas mais
    recentes e descartava o contato cujo atendimento fosse mais antigo que a janela."""
    stmt = select(*_enriched_columns(_notify_private_enabled(), current_user_id)).select_from(_enriched_from())
    if status is not None:
        stmt = stmt.where(conversations.c.status == status)
    if inbox_id is not None:
        stmt = stmt.where(conversations.c.inbox_id == inbox_id)
    if assignee_user_id is not None:
        stmt = stmt.where(conversations.c.assignee_user_id == assignee_user_id)
    if is_archived is not None:
        stmt = stmt.where(conversations.c.is_archived == is_archived)
    if inbox_ids is not None:
        stmt = stmt.where(conversations.c.inbox_id.in_(inbox_ids) if inbox_ids
                          else sa_false())
    if contact_ids is not None:
        stmt = stmt.where(conversations.c.contact_id.in_(contact_ids) if contact_ids
                          else sa_false())
    stmt = (stmt.order_by(conversations.c.is_pinned.desc(),
                          conversations.c.last_activity_at.desc())
            .limit(limit).offset(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return _attach_contact_tags(_attach_labels([_finalize_conv(r) for r in rows]))


def list_filtered(where, *, inbox_ids: list[int] | None = None,
                  current_user_id: int | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
    """List conversations matching a pre-built (injection-safe) WHERE from db.filters.

    ``inbox_ids`` scopes by inbox membership (see :func:`list_conversations`)."""
    stmt = select(*_enriched_columns(_notify_private_enabled(), current_user_id)).select_from(_enriched_from())
    if where is not None:
        stmt = stmt.where(where)
    if inbox_ids is not None:
        stmt = stmt.where(conversations.c.inbox_id.in_(inbox_ids) if inbox_ids
                          else sa_false())
    stmt = (stmt.order_by(conversations.c.is_pinned.desc(),
                          conversations.c.last_activity_at.desc())
            .limit(limit).offset(offset))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return _attach_contact_tags(_attach_labels([_finalize_conv(r) for r in rows]))


def count_tab_counts(where, *, inbox_ids: list[int] | None = None,
                     current_user_id: int | None = None) -> dict:
    """Count the conversation-hub tabs for a pre-built filter WHERE.

    The WHERE comes from ``db.filters`` (same safety boundary used by
    ``list_filtered``). ``all`` is the filtered universe; the other counters are
    conditional aggregates inside that same universe, so the UI can display totals
    without loading every matching row.
    """
    mine_filter = (conversations.c.assignee_user_id == current_user_id
                   if current_user_id is not None else literal(False))
    if current_user_id is not None:
        mention_filter = (
            exists()
            .where(mentions.c.conversation_id == conversations.c.id)
            .where(mentions.c.mentioned_user_id == current_user_id)
            .where(mentions.c.read_at.is_(None))
        )
    else:
        mention_filter = literal(False)

    stmt = (
        select(
            func.count().label("all"),
            func.count().filter(mine_filter).label("mine"),
            func.count().filter(
                conversations.c.assignee_user_id.is_(None),
                conversations.c.active_agent_key.is_(None),
            ).label("unassigned"),
            func.count().filter(mention_filter).label("mentions"),
        )
        .select_from(_enriched_from())
    )
    if where is not None:
        stmt = stmt.where(where)
    if inbox_ids is not None:
        stmt = stmt.where(conversations.c.inbox_id.in_(inbox_ids) if inbox_ids
                          else sa_false())
    with get_engine().connect() as conn:
        row = conn.execute(stmt).mappings().first() or {}
    return {
        "all": int(row.get("all") or 0),
        "mine": int(row.get("mine") or 0),
        "unassigned": int(row.get("unassigned") or 0),
        "mentions": int(row.get("mentions") or 0),
    }


def get_with_channel(conv_id: int, current_user_id: int | None = None) -> dict | None:
    """One conversation enriched with contact + channel + preview + unread (plano 11).

    Used by the conversa-cêntrico chat endpoint to render the header (channel badge,
    contact name) without re-resolving by phone (which would fuse channels)."""
    stmt = (select(*_enriched_columns(_notify_private_enabled(), current_user_id)).select_from(_enriched_from())
            .where(conversations.c.id == conv_id))
    with get_engine().connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return _finalize_conv(row) if row else None


def channel_id_for_contact(contact_id: int) -> str | None:
    """The channel of a contact's MOST RECENT conversation (plano 38 F6).

    Lets a per-contact background job (avatar sweep) route through the right provider
    instead of assuming GOWA. ``None`` when the contact has no conversation yet (skip)
    or resolution fails. Best-effort — never raises."""
    try:
        conv = get_latest_for_contact(contact_id)
        if conv is None:
            return None
        enriched = get_with_channel(conv["id"])
        return enriched.get("channel_id") if enriched else None
    except Exception:
        logger.debug("channel_id_for_contact failed for %s", contact_id, exc_info=True)
        return None


def latest_channel_id_by_contact(contact_ids: list[int]) -> dict[int, str | None]:
    """Batch version of :func:`channel_id_for_contact` (plano 62 F7).

    ONE query resolves, for each contact, the ``channel_id`` of its MOST RECENT
    conversation — same semantics as the one-by-one path: "most recent" is
    ``last_activity_at DESC`` (:func:`get_latest_for_contact`) and the channel is
    reached via ``atendimentos.inbox_id → inboxes.channel_id`` with an OUTER join
    (:func:`conversation_query.enriched_from`), so a conversation whose inbox is
    gone maps to ``None``. Contacts with no conversation are ABSENT from the
    result (the caller skips them). Best-effort — returns ``{}`` on failure,
    never raises. Uses Postgres ``DISTINCT ON (contact_id)``; ``id DESC`` breaks
    ``last_activity_at`` ties deterministically."""
    if not contact_ids:
        return {}
    try:
        stmt = (
            select(conversations.c.contact_id, inboxes.c.channel_id)
            .select_from(conversations.outerjoin(
                inboxes, inboxes.c.id == conversations.c.inbox_id))
            .where(conversations.c.contact_id.in_(contact_ids))
            .order_by(conversations.c.contact_id,
                      conversations.c.last_activity_at.desc(),
                      conversations.c.id.desc())
            .distinct(conversations.c.contact_id)
        )
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).all()
        return {contact_id: channel_id for contact_id, channel_id in rows}
    except Exception:
        logger.debug("latest_channel_id_by_contact failed", exc_info=True)
        return {}


def get_row_for_broadcast(conv_id: int) -> dict | None:
    """One enriched conversation row in the EXACT shape of a ``/api/atendimentos``
    list item — ``get_with_channel`` plus the conversation labels AND contact tags
    (plano 28 / plano 72 F0).

    This is the single source of the ``conversation_upsert`` WS payload, so a
    row pushed live is byte-for-byte what a refetch would return. ``get_with_channel``
    alone omits ``labels`` (conversation labels) AND ``contact_tags`` (contact tags),
    which ``list_filtered``/``list_conversations`` both attach (L468/L488) — leaving
    them off made an upserted row diverge from a fetched one on the `tag` dimension,
    so the client's live insert-gate could never trust tags (plano 72 A1). Attaching
    both here closes that gap and makes `tag`/`conv_label` reliable in the gate."""
    row = get_with_channel(conv_id)
    if row is None:
        return None
    return _attach_contact_tags(_attach_labels([row]))[0]


def mark_conversation_read(conv_id: int) -> list[str]:
    """Clear unread for ONE conversation; return its unread msg_ids for read receipts.

    Deletes only the ``unread_msg_ids`` whose message belongs to this conversation
    and decrements the CONTACT's denormalized ``unread_count`` by that many (clamped
    at 0), so opening one channel's thread never clears another channel's badge
    (plano 11 D1). ``has_unread_mention`` (nível-contato no MVP) é preservado.
    """
    with get_engine().begin() as conn:
        conv = conn.execute(
            select(conversations.c.contact_id).where(conversations.c.id == conv_id)
        ).first()
        if conv is None:
            return []
        contact_id = conv.contact_id
        rows = conn.execute(
            select(unread_msg_ids.c.id, unread_msg_ids.c.msg_id)
            .select_from(unread_msg_ids.join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id))
            .where(unread_msg_ids.c.contact_id == contact_id)
            .where(messages.c.conversation_id == conv_id)
        ).all()
        row_ids = [r.id for r in rows]
        msg_ids = [r.msg_id for r in rows]
        n = len(row_ids)
        if n:
            conn.execute(sa_delete(unread_msg_ids).where(unread_msg_ids.c.id.in_(row_ids)))
            conn.execute(update(contacts).where(contacts.c.id == contact_id).values(
                unread_count=case((contacts.c.unread_count <= n, 0),
                                  else_=contacts.c.unread_count - n),
                updated_at=time.time(),
            ))
    return msg_ids


def mark_conversation_unread(conv_id: int) -> bool:
    """Re-light the green badge for ONE conversation (plano 49 — per-conversa).

    Simétrico a :func:`mark_conversation_read`: a não-lida por-conversa é derivada de
    ``unread_msg_ids ⋈ messages`` filtrando por ``conversation_id`` (mesma subquery que
    alimenta o badge da sidebar em ``conversation_query.enriched_columns``), então marcar
    como não lida = inserir 1 linha ``unread_msg_ids`` de uma mensagem daquela conversa.

    Idempotente: se a conversa já tem ``unread_msg_ids``, é no-op (``unread_msg_ids`` não
    tem unique em ``(contact_id, msg_id)`` — evita inflar o contador em cliques repetidos).
    Ancora na última mensagem *inbound* (``role='user'``) com ``msg_id``; caindo para a
    última mensagem de qualquer role com ``msg_id`` (o join derivado só precisa de um
    ``msg_id`` daquela conversa). Sempre incrementa ``contacts.unread_count`` (+1) para
    manter o badge da aba do navegador (``unread_repo.unread_conversation_count``) coerente.

    Retorna ``True`` se marcou, ``False`` no-op (conversa inexistente ou já não-lida).
    """
    with get_engine().begin() as conn:
        conv = conn.execute(
            select(conversations.c.contact_id).where(conversations.c.id == conv_id)
        ).first()
        if conv is None:
            return False
        contact_id = conv.contact_id
        # Idempotência: já há alguma não-lida derivada nesta conversa?
        already = conn.execute(
            select(
                exists().where(
                    (unread_msg_ids.c.msg_id == messages.c.msg_id)
                    & (messages.c.conversation_id == conv_id)
                )
            )
        ).scalar()
        if already:
            return False
        # Escolhe o msg_id-âncora: último inbound com msg_id; senão última msg com msg_id.
        base = (
            select(messages.c.msg_id)
            .where(messages.c.conversation_id == conv_id)
            .where(messages.c.msg_id.is_not(None))
            .where(messages.c.msg_id != "")
        )
        target = conn.execute(
            base.where(messages.c.role == "user").order_by(messages.c.ts.desc()).limit(1)
        ).scalar()
        if target is None:
            target = conn.execute(
                base.order_by(messages.c.ts.desc()).limit(1)
            ).scalar()
        if target is not None:
            conn.execute(sa_insert(unread_msg_ids).values(
                contact_id=contact_id, msg_id=target,
            ))
        # Sobe o contador denormalizado do contato (badge da aba) mesmo sem msg_id-âncora.
        conn.execute(update(contacts).where(contacts.c.id == contact_id).values(
            unread_count=case((contacts.c.unread_count < 1, 1),
                              else_=contacts.c.unread_count + 1),
            updated_at=time.time(),
        ))
    return True


# NOTE (plano 23 Fase E2): the conversation-centric ``unread_conversation_count``
# was removed here — it had ZERO callers. The browser-tab badge count is served by
# the contact-centric ``contact_repo.unread_conversation_count`` (single source,
# now in ``unread_repo``). The conversation-centric join variant was also
# incompatible with the legacy suite, which increments unread with a phantom msg_id
# the ``unread_msg_ids ⋈ messages`` join would not match.


def count(*, status: str | None = None) -> int:
    stmt = select(func.count()).select_from(conversations)
    if status is not None:
        stmt = stmt.where(conversations.c.status == status)
    with get_engine().connect() as conn:
        return conn.execute(stmt).scalar() or 0


def _update(conv_id: int, values: dict) -> dict | None:
    values = {**values, "updated_at": time.time()}
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(conversations.c.id == conv_id).values(**values))
    return get(conv_id)


def set_status(conv_id: int, status: str, *, clear_assignee: bool = True) -> dict | None:
    """Set a conversation's status and the columns DERIVED from it (data write).

    Writes ``status`` plus the status-derived columns: on close, stamps
    ``resolved_at`` AND drops the ACTIVE AI AGENT (``active_agent_key``) so the
    conversation leaves the agent runtime; on open, clears ``resolved_at``.

    The HUMAN assignee (``assignee_user_id``) is dropped on close ONLY when
    ``clear_assignee`` is true (the default — legacy behavior: the conversation
    lands back as "Não atribuída"). Callers may pass ``clear_assignee=False`` to
    KEEP the current attendant assigned across the close (plano 67 — a plugin opts
    into this via ``filter.conversation.clear_assignee_on_close`` in the service
    layer). ``active_agent_key`` is ALWAYS cleared on close regardless — that drop
    is what lets the reopen path fall back to the MARKED default agent.

    Plano 23 Fase B4: this stays a single status-derived data write (the inbound
    auto-reopen path ``resolve_for_contact_ex`` and test setup depend on the exact
    column shape). The LIFECYCLE ORCHESTRATION that the routes need — the
    ``filter.conversation.before_status`` gate, the WS broadcast, the
    ``conversation.status_changed`` / ``conversation.reopened`` bus emits, and the
    status notice — moved to ``conversation_service.set_status`` (which calls this
    for the write). The repo no longer emits or notices."""
    values = {"status": status}
    if status == "closed":
        values["resolved_at"] = time.time()
        values["active_agent_key"] = None
        if clear_assignee:
            values["assignee_user_id"] = None
    elif status == "open":
        values["resolved_at"] = None
    return _update(conv_id, values)


def set_archived(conv_id: int, is_archived: int) -> dict | None:
    return _update(conv_id, {"is_archived": is_archived})


def set_pinned(conv_id: int, is_pinned: int) -> dict | None:
    """Fixar/desafixar uma conversa no topo da sidebar (plano 54 — por atendimento)."""
    return _update(conv_id, {"is_pinned": is_pinned})


def set_assignee(conv_id: int, assignee_user_id: int | None) -> dict | None:
    return _update(conv_id, {"assignee_user_id": assignee_user_id})


def set_ai_active(conv_id: int, ai_active: int) -> dict | None:
    """Pause/resume the AI for a specific conversation (gate nível conversa)."""
    return _update(conv_id, {"ai_active": ai_active})


# Plano 23 Fase B4: the per-conversation AI TRANSFER policy (toggle AND
# (re)assign — ON re-binds the inbox's default agent + clears the human assignee;
# OFF hands the chat to the operator + clears the agent) moved OUT of the repo into
# ``conversation_service.set_ai`` (unified with the other ownership transitions via
# ``_transfer``). The repo keeps only the pure-data ``set_ai_active`` /
# ``set_assignee`` / ``assign_agent`` primitives the service composes.


def set_custom_attributes(conv_id: int, attrs: dict) -> dict | None:
    """Replace the conversation's custom_attributes JSON (reatribui o dict inteiro)."""
    return _update(conv_id, {"custom_attributes": dict(attrs or {})})


def set_agent(conv_id: int, agent_key: str | None) -> dict | None:
    """Bind a specific AI agent to this conversation (plano 06)."""
    return _update(conv_id, {"active_agent_key": agent_key})


def assign_agent(conv_id: int, *, assignee_user_id: int | None,
                 active_agent_key: str | None, ai_active: int | None = None) -> dict | None:
    """Unified assignment (plano 10): route a conversation to a HUMAN (set
    ``assignee_user_id``, clear the AI agent) or to an AI agent (set
    ``active_agent_key``, clear the human). One atomic write so the panel/bus get
    a single event. ``ai_active=None`` leaves the AI gate untouched."""
    values = {
        "assignee_user_id": assignee_user_id,
        "active_agent_key": active_agent_key,
    }
    if ai_active is not None:
        values["ai_active"] = ai_active
    return _update(conv_id, values)


def ensure_ai_agent(contact_id: int, agent_key: str,
                    inbox_id: int | None = None) -> dict | None:
    """Attribute the contact's active conversation to the AI agent that is
    answering, so the inbox shows its assignee chip (e.g. "IA padrão") sempre que
    a IA responde — mesmo em conversas reabertas após resolução (que limpa o
    ``active_agent_key``) ou criadas antes do agente-padrão existir.

    No-op when a human has taken the chat over (``assignee_user_id`` set), when it
    is already bound to this same agent, or when the conversation is not open.
    Returns the updated conv only when it actually changed, so callers can
    broadcast a single assignment event.

    Plano 37 (A6): ``inbox_id`` escopa a atribuição à conversa ABERTA do canal do
    turno — nunca carimba (nem rouba) a conversa de OUTRO canal do mesmo número,
    inclusive uma **fechada** (o antigo ``get_latest`` incluía fechadas). Fail-open
    (D2): sem ``inbox_id`` cai no resolver legado por-contato."""
    conv = (get_open_for_contact_inbox(contact_id, inbox_id)
            if inbox_id is not None else get_latest_for_contact(contact_id))
    if conv is None:
        return None
    if conv.get("status") != "open":
        return None
    if not conv.get("ai_active"):
        return None  # AI paused on this conversation (plano 17) — never re-attribute
    if conv.get("assignee_user_id") is not None:
        return None  # a person owns this chat — don't steal it for the AI
    if conv.get("active_agent_key") == agent_key:
        return None  # already attributed to this agent
    return _update(conv["id"], {"active_agent_key": agent_key})


def touch_activity(conv_id: int, ts: float | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(conversations.c.id == conv_id)
                     .values(last_activity_at=ts if ts is not None else time.time()))


def delete(conv_id: int) -> int | None:
    """Hard-delete a conversation and its messages (plano 16).

    Returns the deleted conversation's ``contact_id`` (truthy) or ``None`` if it
    did not exist. Steps, all in ONE transaction:

    1. Clear unread for this conversation FIRST (espelha :func:`mark_conversation_read`):
       ``unread_msg_ids`` has no ``conversation_id`` and ``contacts.unread_count`` is
       denormalized, so deleting the messages before clearing would orphan unread rows
       and inflate the badge.
    2. Delete ``messages WHERE conversation_id`` EXPLICITLY — the column has no real FK
       in SQLite (migration 0013 added it without one), so the declarative CASCADE does
       not fire. (In Postgres the FK may cascade; the explicit delete is then idempotent.)
    3. Delete ``conversation_label_links`` (defensive — FK may be missing in legacy SQLite).
    4. Delete the ``conversations`` row.
    """
    with get_engine().begin() as conn:
        row = conn.execute(
            select(conversations.c.contact_id).where(conversations.c.id == conv_id)
        ).first()
        if row is None:
            return None
        contact_id = row.contact_id
        unread_rows = conn.execute(
            select(unread_msg_ids.c.id)
            .select_from(unread_msg_ids.join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id))
            .where(unread_msg_ids.c.contact_id == contact_id)
            .where(messages.c.conversation_id == conv_id)
        ).all()
        n = len(unread_rows)
        if n:
            conn.execute(sa_delete(unread_msg_ids)
                         .where(unread_msg_ids.c.id.in_([r.id for r in unread_rows])))
            conn.execute(update(contacts).where(contacts.c.id == contact_id).values(
                unread_count=case((contacts.c.unread_count <= n, 0),
                                  else_=contacts.c.unread_count - n),
                updated_at=time.time(),
            ))
        conn.execute(sa_delete(messages).where(messages.c.conversation_id == conv_id))
        conn.execute(sa_delete(conversation_label_links)
                     .where(conversation_label_links.c.conversation_id == conv_id))
        conn.execute(sa_delete(conversations).where(conversations.c.id == conv_id))
    return contact_id


def find_empty_inbound_ghosts(cutoff_ts: float, limit: int = 200) -> list[dict]:
    """Inbound "ghost" conversations to sweep (plano 28 Fase 5).

    A ghost is ``origin='inbound'`` + created before ``cutoff_ts`` + NO VISIBLE
    message — the conversation was materialized at ingest (t=0) but the batch that
    would persist its first customer message never ran (shutdown/crash between t=0 and
    t≈3s). "Visible" excludes the panel-only roles (``LIST_PANEL_ONLY_ROLES``): the t=0
    materialization ALSO writes a ``conversation_event`` "created" card, so a real
    ghost is never literally message-less — it has only that card. The TTL sweep
    removes these so they don't linger as permanently-empty rows. The TTL (default
    30 min) is far longer than the batch delay (~3s), so a legitimate brand-new
    conversation is never caught. Returns minimal dicts for delete+broadcast."""
    from db.repositories._mapping import LIST_PANEL_ONLY_ROLES
    no_visible_msg = ~(select(messages.c.id)
                       .where(messages.c.conversation_id == conversations.c.id)
                       .where(messages.c.role.notin_(LIST_PANEL_ONLY_ROLES))
                       .exists())
    stmt = (select(conversations.c.id, conversations.c.contact_id,
                   conversations.c.display_id, conversations.c.inbox_id,
                   conversations.c.status)
            .where(conversations.c.origin == "inbound")
            .where(conversations.c.created_at < cutoff_ts)
            .where(no_visible_msg)
            .limit(limit))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]
