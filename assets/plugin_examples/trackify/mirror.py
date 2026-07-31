"""Eventos do WhatsBot → linhas na fila de saída.

Regra de ouro deste módulo: **o handler NUNCA posta**. O barramento é
fire-and-forget e engole exceção do handler ([plugins/events.py]), então um erro
de rede dentro do handler viraria perda silenciosa de dado no CDP. O handler faz
o mínimo — decidir elegibilidade, montar o snapshot e INSERIR — e quem entrega é
a task supervisionada (``dispatcher.py``).

A identidade NÃO é resolvida aqui: resolver toca o banco do Nexus e o enqueue
tem que ser a transação mais curta possível. Quem resolve é o dispatcher, no
momento do envio.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid

from sqlalchemy import text

from db.repositories import contact_repo
from plugins.context import make_plugin_db

from . import _config, phone as phone_mod

logger = logging.getLogger("plugins.trackify.mirror")

# Tipos de acontecimento espelhados (escopo travado com o usuário no plano 94).
KIND_CONV_CREATED = "conversation_created"
KIND_CONV_RESOLVED = "conversation_resolved"
KIND_CONV_REOPENED = "conversation_reopened"
KIND_PROTO_OPENED = "protocolo_opened"
KIND_PROTO_CLOSED = "protocolo_closed"
KIND_PROTO_RATED = "protocolo_rated"
KIND_CONTACT_UPDATED = "contact_updated"
KIND_CONTACT_TAGGED = "contact_tagged"
KIND_CONTACT_UNTAGGED = "contact_untagged"

# Título humano do evento na timeline do CDP. Sem isto o adapter do Trackify cai
# no ``event_type`` e toda linha lê "protocolo_closed".
TITLES = {
    KIND_CONV_CREATED: "Atendimento iniciado no WhatsApp",
    KIND_CONV_RESOLVED: "Atendimento resolvido",
    KIND_CONV_REOPENED: "Atendimento reaberto",
    KIND_PROTO_OPENED: "Protocolo aberto",
    KIND_PROTO_CLOSED: "Protocolo finalizado",
    KIND_PROTO_RATED: "Atendimento avaliado pelo cliente",
    KIND_CONTACT_UPDATED: "Cadastro atualizado no atendimento",
    KIND_CONTACT_TAGGED: "Etiqueta aplicada",
    KIND_CONTACT_UNTAGGED: "Etiqueta removida",
}

# Kinds descartáveis quando a fila passa do teto — o de menor valor no CDP.
LOW_VALUE_KINDS = (KIND_CONTACT_UPDATED,)


# ── Ator de quem fechou (seam do core, sem patch) ────────────────────────
#
# O core zera ``assignee_user_id`` ANTES de emitir o evento de fechamento, então
# "quem fechou" não chega no payload. Mas ``filter.conversation.clear_assignee_on_close``
# recebe ``ctx_extras={"conversation_id", "user_id"}`` e o ``user_id`` é o ATOR
# (quem clicou Resolver) — melhor ainda que o assignee. Guardamos aqui.
_actor_lock = threading.Lock()
_actor_by_conv: dict[int, tuple[int | None, float]] = {}
_ACTOR_TTL = 120.0


def remember_closer(conversation_id: int | None, user_id) -> None:
    if conversation_id is None:
        return
    now = time.time()
    with _actor_lock:
        _actor_by_conv[int(conversation_id)] = (user_id, now)
        # Poda barata: o dicionário nunca deve crescer sem limite.
        if len(_actor_by_conv) > 512:
            for k, (_, ts) in list(_actor_by_conv.items()):
                if now - ts > _ACTOR_TTL:
                    _actor_by_conv.pop(k, None)


def pop_closer(conversation_id: int | None):
    if conversation_id is None:
        return None
    with _actor_lock:
        hit = _actor_by_conv.pop(int(conversation_id), None)
    if not hit:
        return None
    user_id, ts = hit
    return user_id if (time.time() - ts) <= _ACTOR_TTL else None


# ── Identidade da instalação ─────────────────────────────────────────────

def install_id() -> str:
    """Id estável desta instalação; entra no ``external_id``.

    Sem ele, staging e produção apontando para o mesmo canal do Trackify
    colidiriam no dedup e um engoliria os eventos do outro.
    """
    from db.repositories import config_repo
    val = (_config.setting("install_id") or "").strip()
    if val:
        return val
    val = uuid.uuid4().hex[:12]
    try:
        config_repo.set(_config.PREFIX + "install_id", val)
    except Exception:  # noqa: BLE001
        logger.warning("trackify: não consegui persistir o install_id", exc_info=True)
    return val


def make_external_id(suffix: str) -> str:
    return f"wb.{install_id()}.{suffix}"


# ── Elegibilidade ────────────────────────────────────────────────────────

def _allowed_types() -> set[str]:
    raw = str(_config.setting("mirror_contact_types") or "whatsapp")
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


def eligible(contact: dict | None) -> tuple[bool, str]:
    """``(pode_espelhar, motivo_da_recusa)``.

    Grupo nunca passa: contato no Trackify só tem soft delete, então um JID de
    grupo viraria linha-lixo permanente no CDP. E o ``phone`` do WhatsBot é um
    identificador GENÉRICO por canal — o widget de site grava id de sessão ali.
    """
    if not contact:
        return False, "contato inexistente"
    if contact.get("is_group"):
        return False, "conversa de grupo"
    ctype = (contact.get("contact_type") or "whatsapp").lower()
    if ctype not in _allowed_types():
        return False, f"tipo de contato '{ctype}' fora do escopo"
    ph = contact.get("phone") or ""
    if ctype == "telegram":
        if not phone_mod.digits_only(ph):
            return False, "sem chat_id de telegram"
        return True, ""
    if not phone_mod.looks_like_phone(ph):
        return False, "identificador não é telefone"
    return True, ""


# ── Enfileiramento ───────────────────────────────────────────────────────

def enqueue(kind: str, *, contact: dict, external_id: str, data: dict,
            occurred_at: float | None = None) -> bool:
    """Grava UMA linha na fila. ``False`` quando foi descartado ou já existia.

    Nunca levanta: chamado de dentro de handler de evento, onde exceção some.
    """
    ok, why = eligible(contact)
    if not ok:
        logger.debug("trackify: %s descartado (%s)", kind, why)
        return False

    ts = float(occurred_at or time.time())
    payload = {
        "v": 1,
        "source": "whatsbot",
        "install_id": install_id(),
        "kind": kind,
        "external_id": external_id,
        "title": TITLES.get(kind, kind),
        "contact": {
            "name": (contact.get("name") or "").strip(),
            "email": (contact.get("email") or "").strip(),
            "phone": contact.get("phone") or "",
            "contact_type": (contact.get("contact_type") or "whatsapp").lower(),
        },
        "data": {k: v for k, v in (data or {}).items() if v not in (None, "")},
    }
    now = time.time()
    try:
        with make_plugin_db() as conn:
            conn.execute(
                text("INSERT INTO plugin_trackify_outbox "
                     "(external_id, kind, contact_id, phone, payload, occurred_at, "
                     " status, attempts, next_attempt_at, created_at, updated_at) "
                     "VALUES (:eid, :kind, :cid, :phone, :payload, :occ, "
                     " 'pending', 0, :now, :now, :now) "
                     "ON CONFLICT (external_id) DO NOTHING"),
                {"eid": external_id, "kind": kind, "cid": contact.get("id"),
                 "phone": contact.get("phone") or "",
                 "payload": json.dumps(payload, ensure_ascii=False),
                 "occ": ts, "now": now},
            )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("trackify: falha ao enfileirar %s (%s)", kind, external_id,
                       exc_info=True)
        return False


def _enabled() -> bool:
    return bool(_config.setting("mirror_enabled", False))


def _contact_by_id(contact_id) -> dict | None:
    try:
        return contact_repo.get(int(contact_id)) if contact_id is not None else None
    except Exception:  # noqa: BLE001
        return None


def _contact_by_phone(ph: str) -> dict | None:
    try:
        return contact_repo.get_by_phone(ph) if ph else None
    except Exception:  # noqa: BLE001
        return None


# ── Handlers ─────────────────────────────────────────────────────────────

def on_conversation_created(ctx, payload: dict) -> None:
    if not _enabled():
        return
    c = _contact_by_id(payload.get("contact_id"))
    cid = payload.get("conversation_id")
    enqueue(KIND_CONV_CREATED, contact=c or {},
            external_id=make_external_id(f"conv.{cid}"),
            occurred_at=payload.get("ts"),
            data={"conversation_id": cid, "canal": payload.get("inbox_id"),
                  "origem": payload.get("trigger") or "painel"})


def on_conversation_status(ctx, payload: dict) -> None:
    """Só o FECHAMENTO vira evento. Abertura já veio em ``conversation.created``
    e reabertura tem verbo próprio — espelhar toda transição triplicaria o volume
    contra um orçamento de 40 envios/min."""
    if not _enabled() or payload.get("status") != "closed":
        return
    c = _contact_by_id(payload.get("contact_id"))
    conv_id = payload.get("conversation_id")
    actor = pop_closer(conv_id)
    # ``resolved_at`` não vem no payload; o ts do evento é o instante do fechamento.
    ts = payload.get("ts") or time.time()
    enqueue(KIND_CONV_RESOLVED, contact=c or {},
            external_id=make_external_id(f"conv.{conv_id}.r{int(ts)}"),
            occurred_at=ts,
            data={"conversation_id": conv_id, "canal": payload.get("inbox_id"),
                  "atendente": _user_name(actor or payload.get("assignee_user_id"))})


def on_conversation_reopened(ctx, payload: dict) -> None:
    if not _enabled():
        return
    c = (_contact_by_id(payload.get("contact_id"))
         or _contact_by_phone(payload.get("phone") or ""))
    conv_id = payload.get("conversation_id")
    ts = payload.get("ts") or time.time()
    enqueue(KIND_CONV_REOPENED, contact=c or {},
            external_id=make_external_id(f"conv.{conv_id}.o{int(ts)}"),
            occurred_at=ts,
            data={"conversation_id": conv_id,
                  "origem": payload.get("trigger") or "manual"})


def on_protocolo_opened(ctx, payload: dict) -> None:
    if not _enabled():
        return
    c = (_contact_by_id(payload.get("contact_id"))
         or _contact_by_phone(payload.get("contact_phone") or ""))
    pid = payload.get("protocolo_id")
    enqueue(KIND_PROTO_OPENED, contact=c or {},
            external_id=make_external_id(f"proto.{pid}"),
            occurred_at=payload.get("opened_at") or payload.get("ts"),
            data={"protocolo_id": pid,
                  "conversation_id": payload.get("conversation_id"),
                  "aberto_por": payload.get("opened_by_kind"),
                  "atendente": payload.get("opened_by_name")})


def on_protocolo_closed(ctx, payload: dict) -> None:
    if not _enabled():
        return
    c = (_contact_by_id(payload.get("contact_id"))
         or _contact_by_phone(payload.get("contact_phone") or ""))
    pid = payload.get("protocolo_id")
    closed_at = payload.get("closed_at") or payload.get("ts")
    data = {
        "protocolo_id": pid,
        "conversation_id": payload.get("conversation_id"),
        "atendente": payload.get("assignee_name"),
        "aberto_por": payload.get("opened_by_kind"),
        "motivo": payload.get("reason"),
    }
    # Rótulos do protocolo (o que o atendente preencheu ao finalizar) — é o dado
    # mais rico do atendimento. Só valores simples; nada de estrutura aninhada,
    # que o mapping JSONata do outro lado não saberia ler.
    for k, v in (payload.get("fields") or {}).items():
        if isinstance(v, (str, int, float)) and str(v).strip():
            data[f"campo_{k}"] = str(v)
        elif isinstance(v, list) and v:
            data[f"campo_{k}"] = ", ".join(str(x) for x in v)
    enqueue(KIND_PROTO_CLOSED, contact=c or {},
            external_id=make_external_id(f"proto.{pid}.c{int(float(closed_at or 0))}"),
            occurred_at=closed_at, data=data)


def on_protocolo_rated(ctx, payload: dict) -> None:
    if not _enabled():
        return
    c = _contact_by_id(payload.get("contact_id"))
    enqueue(KIND_PROTO_RATED, contact=c or {},
            external_id=make_external_id(f"aval.{payload.get('id_protocol')}"),
            occurred_at=payload.get("answered_at") or payload.get("ts"),
            data={"protocolo_id": payload.get("protocolo_id"),
                  "nota": payload.get("nota"),
                  "sugestao": payload.get("sugestao")})


def on_contact_updated(ctx, payload: dict) -> None:
    """O atendente preencheu e-mail/nome/etc no painel.

    ⚠️ E-mail é IDENTIFICADOR no Trackify (prioridade 10, e 99,5% dos contatos do
    CDP têm um): um valor errado gruda o evento no contato errado. Por isso o
    e-mail só viaja quando tem forma de e-mail.
    """
    if not _enabled():
        return
    ph = payload.get("phone") or ""
    c = _contact_by_phone(ph)
    if not c:
        return
    ts = payload.get("ts") or time.time()
    # Janela de 60s: uma edição de cadastro dispara vários ``contact.updated``
    # seguidos (o painel salva campo a campo). Colapsa na mesma linha.
    bucket = int(float(ts) // 60)
    enqueue(KIND_CONTACT_UPDATED, contact=c,
            external_id=make_external_id(f"contact.{c.get('id')}.u{bucket}"),
            occurred_at=ts,
            data={"conversation_id": None})


def _tags_hash(tags) -> str:
    joined = ",".join(sorted(str(t) for t in (tags or [])))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def on_contact_tagged(ctx, payload: dict) -> None:
    """``contact.tagged`` traz o SNAPSHOT das tags e é emitido em TODO save,
    mesmo sem mudança. O ``external_id`` endereçado pelo conteúdo colapsa
    re-saves idênticos em zero eventos no CDP."""
    if not _enabled():
        return
    c = _contact_by_phone(payload.get("phone") or "")
    if not c:
        return
    tags = payload.get("tags") or []
    enqueue(KIND_CONTACT_TAGGED, contact=c,
            external_id=make_external_id(f"contact.{c.get('id')}.t{_tags_hash(tags)}"),
            occurred_at=payload.get("ts"),
            data={"etiquetas": ", ".join(str(t) for t in tags)})


def on_contact_untagged(ctx, payload: dict) -> None:
    """A REMOÇÃO vira evento na timeline, mas não mexe nas tags do contato: a
    ingestão do Trackify é aditiva por regra dura (tag nunca é removida por
    webhook). As tags do CDP derivam só para cima em relação ao WhatsBot."""
    if not _enabled():
        return
    c = _contact_by_phone(payload.get("phone") or "")
    if not c:
        return
    tag = str(payload.get("tag") or "")
    ts = payload.get("ts") or time.time()
    enqueue(KIND_CONTACT_UNTAGGED, contact=c,
            external_id=make_external_id(
                f"contact.{c.get('id')}.x{_tags_hash([tag])}.{int(float(ts))}"),
            occurred_at=ts,
            data={"etiqueta_removida": tag})


def _user_name(user_id):
    if user_id is None:
        return None
    try:
        from db.repositories import user_repo
        u = user_repo.get(int(user_id))
        return (u or {}).get("name") or None
    except Exception:  # noqa: BLE001
        return None
