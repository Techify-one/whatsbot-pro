"""Consultas da tela Arquivos (plano 179): cruzam o DISCO com as mensagens.

Escopo (D2): a listagem e a prévia de quem não tem escopo irrestrito partem do banco —
"caminhos referenciados por mensagens de conversas que EU posso ver" — e só então são
cruzadas com o disco. Mensagem sem ``conversation_id`` (legado) nunca aparece para quem
tem escopo; arquivo sem mensagem só aparece para quem vê tudo.

O ``scope`` é o ``ConversationAccessScope`` de ``server/authz.py`` (duck-typing:
``is_unrestricted`` + ``collection_clause(surface)``) — este módulo não importa ``server``.

⚠️ ``media_path`` pode ter barra inicial em linha legada (``/statics/outbox/x``): toda
consulta casa as DUAS formas e devolve o caminho já normalizado (sem barra).
"""

from __future__ import annotations

from sqlalchemy import func, or_, select

from db.engine import get_engine
from db.search.contact_search import (
    SEARCH_EXCLUDED_ROLES, TRIGRAM_MIN_LEN, _folded_match, _folded_pattern, build_q_clause, fold,
)
from db.tables import channels, contacts, conversations, inboxes, messages

_BATCH = 500
#: Quanto do texto da mensagem a busca lê (transcrição pode ser longa).
SEARCH_TEXT_CHARS = 1500


def _norm(path: str | None) -> str:
    return str(path or "").lstrip("/")


def _variants(paths) -> list[str]:
    out: list[str] = []
    for p in dict.fromkeys(_norm(p) for p in paths):
        if p:
            out.extend((p, "/" + p))
    return out


def _chunks(items: list, size: int = _BATCH * 2):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _prefix_clause(prefix: str):
    return or_(messages.c.media_path.like(prefix + "%"),
               messages.c.media_path.like("/" + prefix + "%"))


def _scoped(scope) -> bool:
    return scope is not None and not scope.is_unrestricted


def _filtered(prefixes: list[str], *, scope=None, contact_id: int | None = None,
              contact_text: str = "", inbox_id: int | None = None, chat_type: str = ""):
    """``(from_clause, [conditions])`` das mensagens de mídia sob ``prefixes`` que passam
    nos filtros. Junta ``atendimentos``/``contacts`` só quando o filtro precisa."""
    conds = [or_(*[_prefix_clause(p) for p in prefixes])]
    source = messages
    if _scoped(scope) or inbox_id is not None:
        source = source.join(conversations, conversations.c.id == messages.c.conversation_id)
        if _scoped(scope):
            conds.append(scope.collection_clause("list"))
        if inbox_id is not None:
            conds.append(conversations.c.inbox_id == int(inbox_id))
    if chat_type in ("individual", "group"):
        source = source.join(contacts, contacts.c.id == messages.c.contact_id)
        conds.append(contacts.c.is_group == (1 if chat_type == "group" else 0))
    if contact_id:
        conds.append(messages.c.contact_id == int(contact_id))
    elif contact_text.strip():
        clause = build_q_clause(contact_text.strip(), include_messages=False)
        if clause is not None:
            conds.append(messages.c.contact_id.in_(select(contacts.c.id).where(clause)))
    return source, conds


def needs_db_filter(scope, *, contact_id=None, contact_text="", inbox_id=None, chat_type="") -> bool:
    return bool(_scoped(scope) or contact_id or (contact_text or "").strip()
                or inbox_id is not None or chat_type in ("individual", "group"))


def allowed_paths(prefixes: list[str], *, scope=None, contact_id: int | None = None,
                  contact_text: str = "", inbox_id: int | None = None,
                  chat_type: str = "") -> set[str]:
    """Caminhos (normalizados) de mídia sob ``prefixes`` ligados a pelo menos UMA
    mensagem que passa no escopo e nos filtros."""
    source, conds = _filtered(prefixes, scope=scope, contact_id=contact_id,
                              contact_text=contact_text, inbox_id=inbox_id, chat_type=chat_type)
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(messages.c.media_path).select_from(source).where(*conds).distinct()).all()
    return {_norm(r[0]) for r in rows if r[0]}


def all_paths(prefixes: list[str]) -> set[str]:
    """TODO caminho de mídia referenciado sob ``prefixes`` (base do filtro "sem conversa")."""
    return allowed_paths(prefixes, scope=None)


def text_rows(prefixes: list[str], q: str, *, scope=None, contact_id: int | None = None,
              contact_text: str = "", inbox_id: int | None = None,
              chat_type: str = "") -> list[dict]:
    """Mensagens de mídia candidatas a casar com a busca ``q`` (pré-filtro no SQL).

    Casa ``media_name`` sempre e ``content``/``media_caption`` só com ``q`` de ≥ 3
    caracteres (mesmo piso do pg_trgm da busca de conversas — abaixo disso o ILIKE
    varreria tudo). O refinamento fino (descartar acerto que só pegou a marca
    "[Áudio recebido]") é do chamador, em Python, só nestes candidatos."""
    folded = fold(q)
    if not folded:
        return []
    pattern = _folded_pattern(q)
    branches = [_folded_match(messages.c.media_name, pattern)]
    if len(folded) >= TRIGRAM_MIN_LEN:
        branches.append(_folded_match(messages.c.content, pattern))
        branches.append(_folded_match(messages.c.media_caption, pattern))
    source, conds = _filtered(prefixes, scope=scope, contact_id=contact_id,
                              contact_text=contact_text, inbox_id=inbox_id, chat_type=chat_type)
    stmt = (
        select(
            messages.c.id, messages.c.media_path, messages.c.media_name,
            messages.c.media_type, messages.c.media_caption,
            func.substr(messages.c.content, 1, SEARCH_TEXT_CHARS).label("content"),
        )
        .select_from(source)
        .where(*conds, messages.c.role.notin_(SEARCH_EXCLUDED_ROLES), or_(*branches))
        .order_by(messages.c.id)
    )
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["media_path"] = _norm(d["media_path"])
        out.append(d)
    return out


def sender_name_rows(paths: list[str]) -> list[dict]:
    """De onde sai o nome que o remetente deu a cada arquivo de ``paths``.

    Uma linha por mensagem que aponta para o caminho e PODE ter nome, a mais antiga
    primeiro: ``media_name`` (gravado desde a coluna existir) e, só para documento, o
    início do ``content`` (``[Documento recebido: nome.ext]``, como o nome era guardado
    antes). O chamador escolhe o primeiro utilizável por caminho."""
    rows: list[dict] = []
    wanted = [p for p in dict.fromkeys(_norm(p) for p in paths) if p]
    if not wanted:
        return rows
    with get_engine().connect() as conn:
        for chunk in _chunks(wanted, _BATCH):
            found = conn.execute(
                select(
                    messages.c.media_path, messages.c.media_name, messages.c.media_type,
                    func.substr(messages.c.content, 1, 400).label("content_head"),
                )
                .where(messages.c.media_path.in_(_variants(chunk)))
                .where(or_(messages.c.media_name.is_not(None), messages.c.media_type == "document"))
                .order_by(messages.c.id)
            ).mappings().all()
            for r in found:
                d = dict(r)
                d["media_path"] = _norm(d["media_path"])
                rows.append(d)
    return rows


def origins(paths: list[str], scope=None) -> dict[str, dict]:
    """``media_path`` → de onde veio (mensagem MAIS ANTIGA dentro do escopo).

    Caminho sem mensagem (ou sem mensagem visível ao escopo) fica de fora = "sem
    conversa". Mensagem legada sem ``conversation_id`` vem com ``conversation_id=None``
    (a tela mostra o nome sem link) — só para escopo irrestrito."""
    wanted = [p for p in dict.fromkeys(_norm(p) for p in paths) if p]
    found: dict[str, dict] = {}
    if not wanted:
        return found
    scoped = _scoped(scope)
    source = messages.join(contacts, contacts.c.id == messages.c.contact_id)
    if scoped:
        source = source.join(conversations, conversations.c.id == messages.c.conversation_id)
    else:
        source = source.outerjoin(conversations, conversations.c.id == messages.c.conversation_id)
    source = source.outerjoin(inboxes, inboxes.c.id == conversations.c.inbox_id).outerjoin(
        channels, channels.c.id == inboxes.c.channel_id)
    with get_engine().connect() as conn:
        for chunk in _chunks(wanted, _BATCH):
            stmt = (
                select(
                    messages.c.media_path, messages.c.id.label("message_id"),
                    messages.c.ts.label("msg_ts"), messages.c.conversation_id,
                    contacts.c.id.label("contact_id"), contacts.c.phone, contacts.c.name,
                    contacts.c.group_name, contacts.c.is_group,
                    conversations.c.inbox_id,
                    func.coalesce(channels.c.display_name, inboxes.c.name).label("inbox_name"),
                )
                .select_from(source)
                .where(messages.c.media_path.in_(_variants(chunk)))
                .order_by(messages.c.id)
            )
            if scoped:
                stmt = stmt.where(scope.collection_clause("list"))
            for r in conn.execute(stmt).mappings():
                found.setdefault(_norm(r["media_path"]), dict(r))
    return found


def visible_to(scope, media_path: str, surface: str = "direct") -> bool:
    """O arquivo está ligado a ao menos uma mensagem de conversa que o escopo abre?

    É a prova de autorização da PRÉVIA: o id opaco não é segredo (quem tem a permissão
    monta o id de qualquer arquivo), então a checagem é refeita pelo caminho. Escopo
    irrestrito sempre passa. ``surface='direct'`` é o mesmo de ``/api/messages/{id}/media``."""
    if not _scoped(scope):
        return True
    path = _norm(media_path)
    if not path:
        return False
    with get_engine().connect() as conn:
        return conn.execute(
            select(messages.c.id)
            .select_from(messages.join(conversations, conversations.c.id == messages.c.conversation_id))
            .where(messages.c.media_path.in_(_variants([path])))
            .where(scope.collection_clause(surface))
            .limit(1)
        ).first() is not None


def messages_for_paths(paths: list[str]) -> list[dict]:
    """Toda mensagem que aponta para algum dos ``paths`` (``id``, ``media_path``,
    ``conversation_id``) — base da exclusão, que NÃO tem restrição de escopo."""
    wanted = [p for p in dict.fromkeys(_norm(p) for p in paths) if p]
    rows: list[dict] = []
    if not wanted:
        return rows
    with get_engine().connect() as conn:
        for chunk in _chunks(wanted, _BATCH):
            found = conn.execute(
                select(messages.c.id, messages.c.media_path, messages.c.conversation_id)
                .where(messages.c.media_path.in_(_variants(chunk)))
            ).mappings().all()
            for r in found:
                d = dict(r)
                d["media_path"] = _norm(d["media_path"])
                rows.append(d)
    return rows


def suggest_contacts(q: str, limit: int, scope=None) -> list[dict]:
    """Contatos/grupos que ``q`` nomeia (nome, nome do grupo, telefone ou etiqueta),
    para o seletor "Contato ou grupo". Quem tem escopo só recebe contatos com alguma
    conversa que pode ver."""
    clause = build_q_clause((q or "").strip(), include_messages=False)
    stmt = select(contacts.c.id, contacts.c.phone, contacts.c.name, contacts.c.group_name,
                  contacts.c.is_group, contacts.c.updated_at)
    if clause is not None:
        stmt = stmt.where(clause)
    if _scoped(scope):
        stmt = stmt.where(contacts.c.id.in_(
            select(conversations.c.contact_id).where(scope.collection_clause("list"))))
    stmt = stmt.order_by(contacts.c.updated_at.desc()).limit(max(1, min(int(limit), 20)))
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    out = []
    for r in rows:
        name = ((r["group_name"] if r["is_group"] else r["name"]) or "").lstrip("~").strip()
        out.append({"id": r["id"], "phone": r["phone"], "name": name,
                    "is_group": bool(r["is_group"])})
    return out


def visible_inboxes(scope=None) -> list[dict]:
    """Caixas (canais vivos) que o escopo enxerga, para o seletor "Canal". A tela Arquivos
    NÃO tem ``inbox.manage``, então não pode usar ``GET /api/inboxes``."""
    from db.repositories import inbox_repo
    rows = inbox_repo.list_with_channel()
    allowed = getattr(scope, "inbox_ids", None) if _scoped(scope) else None
    return [{"id": r["id"], "name": r["name"]} for r in rows
            if allowed is None or r["id"] in allowed]
