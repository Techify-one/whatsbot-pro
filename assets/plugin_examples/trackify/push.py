"""Saída: WhatsBot → Trackify (campos do contato).

Duas metades bem separadas, pelo mesmo motivo do ``mirror.py``: **o handler de
evento NUNCA posta**. O barramento é fire-and-forget e engole exceção, então
uma falha de rede dentro do handler viraria perda silenciosa. O handler só
marca o contato como sujo numa fila, e quem entrega é a task supervisionada.

A fila guarda **só o contact_id**, não o valor. O worker recalcula o estado
desejado na hora de enviar, e isso compra três coisas de graça:

* cinco edições em dez segundos colapsam num PUT só (índice único parcial);
* payload velho é impossível — não existe payload guardado;
* o evento, o botão "Simular/Sincronizar agora" e a varredura de conferência
  viram literalmente a mesma função, ``plan_for_contact``.
"""

from __future__ import annotations

import logging
import os
import random
import time

from sqlalchemy import text

from plugins.context import make_plugin_db

from . import _config, field_codec, field_map, session, sync_core, sync_state, writer

logger = logging.getLogger("plugins.trackify.push")

MAX_ATTEMPTS = 8
BACKOFF_CAP = 300.0
COOLDOWN_FLOOR = 5.0
COOLDOWN_CAP = 120.0
LOCK_STALE_AFTER = 300.0
IDENTITY_TTL = 7 * 24 * 3600.0

# Tools da IA que gravam no cadastro do contato. O core NÃO emite evento de
# barramento nesse caminho (só a rota do painel emite ``contact.updated``),
# então sem este gancho tudo que a IA descobre nunca chegaria ao CDP.
# Mantido SEPARADO de ``mirror.CONTACT_WRITING_TOOLS``: mexer naquele mudaria o
# que o espelho de eventos manda, que é outra feature com outro escopo.
CONTACT_ATTR_TOOLS = {"save_contact_info", "set_custom_attribute"}

# Relógio de cooldown PRÓPRIO: as rotas de contato do Trackify têm um balde de
# taxa diferente do da ingestão (30/min contra 60/min), então compartilhar o
# relógio do dispatcher faria um 429 de um lado calar o outro sem motivo.
_cooldown_until = 0.0
_worker_id = f"{os.getpid()}"


def _now() -> float:
    return time.time()


def cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - _now())


def note_cooldown(seconds: float) -> None:
    global _cooldown_until
    s = max(COOLDOWN_FLOOR, min(float(seconds or 0), COOLDOWN_CAP))
    _cooldown_until = max(_cooldown_until, _now() + s)


# ── Ganchos do barramento ────────────────────────────────────────────────

def on_contact_updated(ctx, payload: dict) -> None:
    """``PUT /api/contacts/{phone}/info`` — o caminho do painel."""
    phone = (payload or {}).get("phone")
    if phone:
        _enqueue_by_phone(str(phone), "contact.updated")


def on_tool_after(ctx, payload: dict) -> None:
    """A IA gravando no cadastro (``set_custom_attribute``/``save_contact_info``)."""
    p = payload or {}
    if p.get("tool_name") not in CONTACT_ATTR_TOOLS or p.get("error"):
        return
    phone = p.get("phone")
    if phone:
        _enqueue_by_phone(str(phone), f"tool.{p.get('tool_name')}")


def _enqueue_by_phone(phone: str, reason: str) -> None:
    try:
        if not _config.field_sync_ready():
            return          # desligado: a fila não pode crescer à toa
        from db.repositories import contact_repo
        c = contact_repo.get_by_phone(phone)
        if c and not c.get("is_group"):
            enqueue(int(c["id"]), reason)
    except Exception:  # noqa: BLE001 — handler de bus nunca derruba o pipeline
        logger.debug("trackify: falha ao enfileirar push de campo", exc_info=True)


def enqueue(contact_id: int, reason: str = "") -> None:
    """Marca o contato como sujo. Idempotente por construção: o índice único
    parcial garante no máximo uma linha pendente por contato."""
    now = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "INSERT INTO plugin_trackify_field_outbox "
            "(contact_id, reason, status, next_attempt_at, enqueued_at, "
            " created_at, updated_at) "
            "VALUES (:c, :r, 'pending', :now, :now, :now, :now) "
            "ON CONFLICT (contact_id) WHERE status = 'pending' "
            "DO UPDATE SET reason = :r, updated_at = :now"),
            {"c": contact_id, "r": (reason or "")[:100], "now": now})


# ── Plano de escrita ─────────────────────────────────────────────────────

class Plan:
    def __init__(self, skip: str = "", trackify_contact_id: str = "",
                 field_values: dict | None = None, decisions: list | None = None):
        self.skip = skip
        self.trackify_contact_id = trackify_contact_id
        self.field_values = field_values or {}
        self.decisions = decisions or []


def decisions_for_contact(contact_id: int, maps: list) -> tuple:
    """``(motivo_pular, tk_id, decisões, valores_do_whatsbot)`` para um contato.

    Compartilhado pelo push e pela varredura de conferência, de propósito: são
    os dois caminhos que precisam comparar os dois lados, e duas cópias da
    comparação divergiriam com o tempo.
    """
    from db.repositories import contact_repo

    contact = contact_repo.get(contact_id)
    if not contact:
        return "contato não existe mais", "", [], {}
    if contact.get("is_group"):
        return "grupo não tem cadastro no CDP", "", [], {}

    tk_id = _linked_id(str(contact.get("phone") or ""), contact)
    if not tk_id:
        return "contato não vinculado a um cadastro no Trackify", "", [], {}

    wb_values = _wb_values(contact)
    tk_values = _tk_values(tk_id, [m["tk_slug"] for m in maps])
    states = sync_state.load_for_contacts([contact_id])

    decisions = []
    for m in maps:
        wb_val = wb_values.get((m["wb_scope"], m["wb_key"]))
        tk_val = tk_values.get(m["tk_slug"])
        d = sync_core.decide(m, states.get((m["id"], contact_id)), wb_val, tk_val)
        decisions.append({"map": m, "decision": d, "wb": wb_val, "tk": tk_val})
    return "", tk_id, decisions, wb_values


def plan_for_contact(contact_id: int) -> Plan:
    """O que precisaria ser escrito no CDP para este contato convergir.

    Bloqueante (banco do WhatsBot + leitura do CDP). Sem vínculo de identidade
    o plano é vazio com motivo: esta feature **nunca cria contato no Trackify**
    — quem faz isso é o espelho de eventos, com o toggle e o limite dele.
    """
    maps = [m for m in field_map.list_maps(enabled_only=True)
            if m["direction"] in ("to_trackify", "both")]
    if not maps:
        return Plan(skip="nenhum mapeamento de saída ativo")

    skip, tk_id, decisions, wb_values = decisions_for_contact(contact_id, maps)
    if skip:
        return Plan(skip=skip)

    field_values: dict[str, str] = {}
    for d in decisions:
        if d["decision"].action != sync_core.PUSH:
            continue
        m = d["map"]
        encoded, err = field_codec.to_trackify(m, _definition(m, wb_values), d["wb"])
        if err:
            field_map.bump(m["id"], "pushed_err", stamp="last_push_at", error=err)
            sync_state.record(m["id"], contact_id, wb_hash=d["decision"].wb_hash,
                              tk_hash=d["decision"].tk_hash,
                              trackify_contact_id=tk_id, error=err)
            continue
        field_values[m["tk_slug"]] = encoded

    return Plan(trackify_contact_id=tk_id, field_values=field_values,
                decisions=decisions)


def _definition(mapping: dict, wb_values: dict) -> dict:
    """Definição do atributo, no formato que ``validate_value`` espera."""
    return wb_values.get(("__def__", mapping["wb_key"])) or {
        "attribute_key": mapping["wb_key"], "type": "text"}


def _wb_values(contact: dict) -> dict:
    """``{(escopo, chave): valor}`` + as definições, numa passada só."""
    from db.repositories import custom_attribute_repo as ca_repo

    out: dict = {}
    for key in field_map.ALLOWED_COLUMNS:
        out[(field_map.SCOPE_COLUMN, key)] = contact.get(key)
        out[("__def__", key)] = {"attribute_key": key, "type": "text"}

    attrs = contact.get("custom_attributes")
    if not isinstance(attrs, dict):
        attrs = {}
    for d in ca_repo.list_definitions("contact"):
        k = d["attribute_key"]
        out[(field_map.SCOPE_ATTR, k)] = attrs.get(k)
        out[("__def__", k)] = d
    return out


_TK_VALUES_SQL = """
SELECT cf.slug AS slug, cfv.value AS value
FROM contact_field_values cfv
JOIN custom_fields cf ON cf.id = cfv.custom_field_id
WHERE cfv.contact_id = CAST(:cid AS uuid) AND cf.slug = ANY(:slugs)
"""


def _tk_values(trackify_contact_id: str, slugs: list) -> dict:
    if not slugs:
        return {}
    rows = trackify_db_run(_TK_VALUES_SQL,
                           {"cid": trackify_contact_id, "slugs": list(slugs)})
    # Slug ausente = campo sem linha EAV no CDP, ou seja VAZIO — e não
    # "desconhecido". A diferença importa: sem semear com "" um campo apagado lá
    # nunca seria detectado como apagado.
    out = {s: "" for s in slugs}
    out.update({r["slug"]: r["value"] for r in rows})
    return out


def trackify_db_run(sql: str, params: dict) -> list:
    from . import trackify_db
    return trackify_db.run_read(sql, params)


def _linked_id(phone: str, contact: dict | None = None) -> str:
    """Id do cadastro do contato no CDP.

    Usa o cache de identidade e, quando ele não tem nada válido, **procura na
    hora** pelo telefone e pelos campos conectados que são identificador. Sem
    essa busca sob demanda o escopo da sincronização dependia de o espelho de
    eventos já ter passado por aquele contato — conectar o CPF na tela não fazia
    diferença nenhuma até o contato gerar um evento.

    Vínculo velho é descartado de propósito: um merge no Trackify DELETA a
    grafia do perdedor, então o cache envelheceria sem aviso.
    """
    if not phone:
        return ""
    with make_plugin_db() as conn:
        row = conn.execute(text(
            "SELECT trackify_contact_id, resolved_at FROM plugin_trackify_identity "
            "WHERE phone = :p"), {"p": phone}).mappings().first()
    if row and row["trackify_contact_id"] and (
            _now() - float(row["resolved_at"] or 0) <= IDENTITY_TTL):
        return str(row["trackify_contact_id"])

    return _resolver_agora(phone, contact)


def _resolver_agora(phone: str, contact: dict | None) -> str:
    """Procura o cadastro no CDP e grava o vínculo. ``""`` quando não achou.

    Grava SÓ com acerto único. Mais de um cadastro casando é ambiguidade real
    (medido: números com 2 cadastros) e escolher em silêncio colaria os dados de
    uma pessoa no cadastro de outra.
    """
    from db.repositories import contact_repo
    from . import identity

    try:
        c = contact or contact_repo.get_by_phone(phone)
        if not c:
            return ""
        matches = identity.resolve_mapped(
            phone=phone, extras=field_map.identifier_hints(c),
            contact_type=c.get("contact_type") or "whatsapp")
        if len(matches) != 1:
            return ""
        m = matches[0]
        with make_plugin_db() as conn:
            conn.execute(text(
                "INSERT INTO plugin_trackify_identity "
                "(phone, trackify_contact_id, matched_slug, exact_value, resolved_at) "
                "VALUES (:p, :cid, :slug, :val, :ts) "
                "ON CONFLICT (phone) DO UPDATE SET "
                " trackify_contact_id = EXCLUDED.trackify_contact_id, "
                " matched_slug = EXCLUDED.matched_slug, "
                " exact_value = EXCLUDED.exact_value, "
                " resolved_at = EXCLUDED.resolved_at"),
                {"p": phone, "cid": m.contact_id, "slug": m.slug,
                 "val": m.exact_value, "ts": _now()})
        logger.info("trackify: contato %s vinculado por '%s'", phone, m.slug)
        return str(m.contact_id)
    except Exception:  # noqa: BLE001 — sem vínculo o contato só fica fora do escopo
        logger.debug("trackify: falha ao resolver identidade sob demanda", exc_info=True)
        return ""


# ── Fila ─────────────────────────────────────────────────────────────────

def claim(limit: int, worker: str) -> list[dict]:
    now = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_field_outbox SET status = 'pending', locked_by = '' "
            "WHERE status = 'sending' AND COALESCE(locked_at, 0) < :stale"),
            {"stale": now - LOCK_STALE_AFTER})
        rows = conn.execute(text(
            "UPDATE plugin_trackify_field_outbox SET status = 'sending', "
            " locked_by = :w, locked_at = :now, updated_at = :now "
            "WHERE id IN ("
            "  SELECT id FROM plugin_trackify_field_outbox "
            "  WHERE status = 'pending' AND next_attempt_at <= :now "
            "  ORDER BY next_attempt_at, id LIMIT :lim FOR UPDATE SKIP LOCKED"
            ") RETURNING id, contact_id, reason, attempts"),
            {"w": worker, "now": now, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def finish(row_id: int, status: str, *, error: str = "", http: int | None = None) -> None:
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_field_outbox SET status = :st, locked_by = '', "
            " last_error = :err, last_http_status = :http, updated_at = :now "
            "WHERE id = :id"),
            {"id": row_id, "st": status, "err": (error or "")[:500],
             "http": http, "now": _now()})


def retry_later(row_id: int, attempts: int, *, error: str = "",
                http: int | None = None, count_attempt: bool = True) -> None:
    """Reagenda com backoff exponencial e jitter.

    ``count_attempt=False`` para 429: é falha de RITMO do worker, não do item —
    incrementar mataria um backlog saudável só porque o CDP estava ocupado.
    """
    n = attempts + 1 if count_attempt else attempts
    if n >= MAX_ATTEMPTS:
        finish(row_id, "failed", error=error, http=http)
        return
    delay = min(2.0 * (2 ** max(0, n - 1)), BACKOFF_CAP)
    delay *= 0.8 + random.random() * 0.4
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_field_outbox SET status = 'pending', attempts = :n, "
            " next_attempt_at = :next, locked_by = '', last_error = :err, "
            " last_http_status = :http, updated_at = :now "
            "WHERE id = :id"),
            {"id": row_id, "n": n, "next": _now() + delay, "err": (error or "")[:500],
             "http": http, "now": _now()})


def stats() -> dict:
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(
                "SELECT status, COUNT(*) AS n FROM plugin_trackify_field_outbox "
                "GROUP BY status")).mappings().all()
        return {r["status"]: int(r["n"]) for r in rows}
    except Exception:  # noqa: BLE001
        return {}


# ── Entrega ──────────────────────────────────────────────────────────────

async def deliver_one(client, row: dict) -> str:
    """Processa uma linha da fila. Devolve um rótulo do desfecho (para o laço)."""
    import asyncio

    contact_id = int(row["contact_id"])
    plan = await asyncio.to_thread(plan_for_contact, contact_id)

    if plan.skip:
        await asyncio.to_thread(finish, row["id"], "blocked", error=plan.skip)
        return "skipped"
    if not plan.field_values:
        # Nada divergente: o caminho ESMAGADORAMENTE comum depois do primeiro
        # ciclo. Zero HTTP aqui é o que mantém o orçamento de taxa utilizável.
        await asyncio.to_thread(_record_all, contact_id, plan)
        await asyncio.to_thread(finish, row["id"], "sent")
        return "noop"

    if bool(_config.setting("field_sync_dry_run", True)):
        resumo = ", ".join(f"{k}={v!r}" for k, v in sorted(plan.field_values.items()))
        await asyncio.to_thread(finish, row["id"], "sent",
                                error=f"[modo seco] enviaria: {resumo}"[:500])
        return "dry_run"

    sess = await session.get(client)
    if sess is None:
        # Sem sessão não é falha do item: não conta tentativa.
        await asyncio.to_thread(retry_later, row["id"], int(row["attempts"]),
                                error=session.blocked_reason() or "sem sessão",
                                count_attempt=False)
        return "no_session"

    res = await writer.put_contact(client, sess, plan.trackify_contact_id,
                                   plan.field_values)
    if res.verdict == writer.UNAUTHORIZED:
        # Exatamente UMA re-tentativa com sessão nova. Nunca mais que uma: a
        # rota de login é limitada a 5/min.
        session.invalidate()
        sess = await session.get(client, force=True)
        if sess is not None:
            res = await writer.put_contact(client, sess, plan.trackify_contact_id,
                                           plan.field_values)

    return await asyncio.to_thread(_apply_result, row, plan, contact_id, res)


def _apply_result(row: dict, plan: Plan, contact_id: int, res) -> str:
    if res.verdict == writer.OK:
        _record_all(contact_id, plan, pushed=True)
        for d in plan.decisions:
            if d["decision"].action == sync_core.PUSH:
                field_map.bump(d["map"]["id"], "pushed_ok", stamp="last_push_at")
        finish(row["id"], "sent", http=res.http_status)
        return "sent"

    if res.verdict == writer.CONFLICT:
        # Nunca re-tentar: outro cadastro é dono do identificador e só uma
        # decisão humana resolve.
        for d in plan.decisions:
            if d["decision"].action == sync_core.PUSH:
                m = d["map"]
                field_map.bump(m["id"], "conflicts", stamp="last_push_at",
                               error=res.error)
                sync_state.record(m["id"], contact_id,
                                  wb_hash=d["decision"].wb_hash,
                                  tk_hash=d["decision"].tk_hash,
                                  trackify_contact_id=plan.trackify_contact_id,
                                  conflict=True, conflict_reason=res.error,
                                  error=res.error)
        finish(row["id"], "blocked", error=res.error, http=res.http_status)
        return "conflict"

    if res.verdict == writer.UNLINKED:
        _forget_identity(contact_id)
        finish(row["id"], "blocked", error=res.error, http=res.http_status)
        return "unlinked"

    if res.verdict == writer.BLOCKED:
        for d in plan.decisions:
            if d["decision"].action == sync_core.PUSH:
                field_map.bump(d["map"]["id"], "pushed_err", stamp="last_push_at",
                               error=res.error)
        finish(row["id"], "blocked", error=res.error, http=res.http_status)
        return "blocked"

    if res.verdict == writer.THROTTLED:
        note_cooldown(res.retry_after or COOLDOWN_FLOOR)
        retry_later(row["id"], int(row["attempts"]), error=res.error,
                    http=res.http_status, count_attempt=False)
        return "throttled"

    retry_later(row["id"], int(row["attempts"]), error=res.error, http=res.http_status)
    return "retry"


def _record_all(contact_id: int, plan: Plan, *, pushed: bool = False) -> None:
    for d in plan.decisions:
        dec = d["decision"]
        if dec.action == sync_core.CONFLICT:
            field_map.bump(d["map"]["id"], "conflicts")
            sync_state.record(d["map"]["id"], contact_id, wb_hash=dec.wb_hash,
                              tk_hash=dec.tk_hash,
                              trackify_contact_id=plan.trackify_contact_id,
                              conflict=True, conflict_reason=dec.reason)
            continue
        if dec.action == sync_core.NOOP:
            continue
        # Um push bem-sucedido faz os dois lados valerem o valor do WhatsBot.
        tk_hash = dec.wb_hash if (pushed and dec.action == sync_core.PUSH) else dec.tk_hash
        sync_state.record(d["map"]["id"], contact_id, wb_hash=dec.wb_hash,
                          tk_hash=tk_hash,
                          trackify_contact_id=plan.trackify_contact_id,
                          pushed=pushed and dec.action == sync_core.PUSH)


def _forget_identity(contact_id: int) -> None:
    from db.repositories import contact_repo
    c = contact_repo.get(contact_id)
    if not c:
        return
    with make_plugin_db() as conn:
        conn.execute(text("DELETE FROM plugin_trackify_identity WHERE phone = :p"),
                     {"p": c.get("phone") or ""})
