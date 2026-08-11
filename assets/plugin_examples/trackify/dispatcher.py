"""Entrega da fila de saída no Trackify (``POST /ingestion/<canal>``).

Quem chama é a task supervisionada de ``lifecycle.py``. Aqui mora tudo que pode
falhar: resolver identidade, postar, e decidir o que fazer com cada resposta.

Três decisões que valem explicação:

* **A identidade é resolvida AQUI, não no enqueue.** O Trackify casa contato por
  igualdade exata de string e, com a política "criar sempre", uma grafia
  diferente vira um contato duplicado em silêncio. Então antes de postar
  perguntamos ao CDP qual grafia ele já tem e reenviamos exatamente aquela.
* **429 não conta como tentativa do item.** É falha de ritmo do worker, não do
  evento; incrementar mataria um backlog saudável só porque o CDP estava ocupado.
* **4xx de configuração vira ``blocked``, não retry.** Um mapping JSONata errado
  não melhora na décima tentativa — melhora quando alguém conserta.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from plugins.context import make_plugin_db

from . import _config, identity
from . import client as tk_client

logger = logging.getLogger("plugins.trackify.dispatcher")

HTTP_TIMEOUT = 20.0
MAX_ATTEMPTS = 8
BACKOFF_CAP = 300.0
COOLDOWN_FLOOR = 5.0
COOLDOWN_CAP = 120.0
IDENTITY_TTL = 7 * 24 * 3600.0
LOCK_STALE_AFTER = 300.0

# Cooldown COMPARTILHADO: 429/5xx falam sobre o servidor, não sobre o item. Um
# relógio de módulo segura o worker inteiro em vez de cada linha tentar sozinha.
_cooldown_until = 0.0


def _now() -> float:
    return time.time()


def cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - _now())


def note_cooldown(seconds: float) -> None:
    global _cooldown_until
    s = max(COOLDOWN_FLOOR, min(float(seconds or 0), COOLDOWN_CAP))
    _cooldown_until = max(_cooldown_until, _now() + s)


# ── Cache de identidade ──────────────────────────────────────────────────

def _cached_identity(ph: str) -> dict | None:
    if not ph:
        return None
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT trackify_contact_id, matched_slug, exact_value, resolved_at "
                 "FROM plugin_trackify_identity WHERE phone = :p"),
            {"p": ph}).mappings().first()
    if not row or (_now() - float(row["resolved_at"] or 0)) > IDENTITY_TTL:
        return None
    return dict(row)


def _store_identity(ph: str, contact_id: str, slug: str, value: str) -> None:
    if not ph:
        return
    with make_plugin_db() as conn:
        conn.execute(
            text("INSERT INTO plugin_trackify_identity "
                 "(phone, trackify_contact_id, matched_slug, exact_value, resolved_at) "
                 "VALUES (:p, :cid, :slug, :val, :ts) "
                 "ON CONFLICT (phone) DO UPDATE SET "
                 " trackify_contact_id = EXCLUDED.trackify_contact_id, "
                 " matched_slug = EXCLUDED.matched_slug, "
                 " exact_value = EXCLUDED.exact_value, "
                 " resolved_at = EXCLUDED.resolved_at"),
            {"p": ph, "cid": contact_id or "", "slug": slug or "",
             "val": value or "", "ts": _now()})


def _forget_identity(ph: str) -> None:
    """Invalida o cache — chamado quando o CDP devolve um contato diferente do
    cacheado, que é a assinatura de um MERGE lá dentro (o merge deleta a grafia
    do perdedor, então o valor guardado envelheceu sem aviso)."""
    if not ph:
        return
    with make_plugin_db() as conn:
        conn.execute(text("DELETE FROM plugin_trackify_identity WHERE phone = :p"),
                     {"p": ph})


def _hints(ph: str, email: str) -> dict:
    """Campos conectados que são identificador, para a busca do contato.

    O e-mail solto do envelope entra como reserva: ele vem da coluna legada, que
    hoje está vazia na maioria das instalações.
    """
    pistas: dict = {}
    try:
        from db.repositories import contact_repo

        from . import field_map
        c = contact_repo.get_by_phone(ph) if ph else None
        if c:
            pistas = field_map.identifier_hints(c)
    except Exception:  # noqa: BLE001 — sem pistas, sobra o telefone
        logger.debug("trackify: falha ao montar pistas de identidade", exc_info=True)
    if email and "@" in email:
        pistas.setdefault(identity.SLUG_EMAIL, email)
    return pistas


async def resolve_identity(http, ph: str, email: str, contact_type: str) -> dict:
    """Identificadores a enviar. Usa o valor EXATO do CDP quando o contato existe.

    Sem acerto, devolve a forma canônica E.164 — é a grafia do maior grupo já
    presente no CDP, então maximiza a chance de uma fonte futura (formulário,
    anúncio, digitação) cair na mesma linha em vez de forkar.
    """
    import asyncio

    hit = await asyncio.to_thread(_cached_identity, ph)
    if hit and hit.get("exact_value"):
        out = {hit["matched_slug"]: hit["exact_value"]}
        if email and "@" in email:
            out.setdefault(identity.SLUG_EMAIL, email.strip().lower())
        return out

    pistas = await asyncio.to_thread(_hints, ph, email)
    matches = await identity.resolve_mapped(http, phone=ph, extras=pistas,
                                            contact_type=contact_type)
    if len(matches) == 1:
        m = matches[0]
        await asyncio.to_thread(_store_identity, ph, m.contact_id, m.slug, m.exact_value)
        out = {m.slug: m.exact_value}
        if email and "@" in email and m.slug != identity.SLUG_EMAIL:
            out[identity.SLUG_EMAIL] = email.strip().lower()
        return out

    # Zero acertos (cria) ou mais de um (ambíguo — não escolhemos em silêncio;
    # mandamos a forma canônica e deixamos o CDP resolver pela prioridade dele).
    return identity.canonical_identity(phone=ph, email=email, contact_type=contact_type)


# ── Envelope ─────────────────────────────────────────────────────────────

async def build_body(http, row: dict) -> dict:
    payload = json.loads(row["payload"])
    contact = payload.get("contact") or {}
    ph = contact.get("phone") or row.get("phone") or ""
    ident = await resolve_identity(http, ph, contact.get("email") or "",
                                   contact.get("contact_type") or "whatsapp")
    # ⚠️ ISO-8601 COM offset explícito. O adapter do Trackify faz ``new Date(v)``:
    # um número seria lido como MILISSEGUNDOS (o ts do WhatsBot é em segundos e
    # carimbaria tudo em 1970) e um ISO sem offset seria lido no fuso do servidor.
    occurred = datetime.fromtimestamp(float(row["occurred_at"]), timezone.utc).isoformat()
    return {
        **payload,
        "occurred_at": occurred,
        "identity": ident,
        "contact": {k: v for k, v in contact.items() if k != "phone" and v},
    }


# ── Fila ─────────────────────────────────────────────────────────────────

def claim(limit: int, worker: str) -> list[dict]:
    """Reserva até ``limit`` linhas prontas. ``SKIP LOCKED`` para várias réplicas
    nunca pegarem a mesma linha."""
    now = _now()
    with make_plugin_db() as conn:
        # Devolve à fila o que ficou preso em 'sending' (worker morto no meio).
        conn.execute(
            text("UPDATE plugin_trackify_outbox SET status = 'pending', locked_by = '' "
                 "WHERE status = 'sending' AND COALESCE(locked_at, 0) < :stale"),
            {"stale": now - LOCK_STALE_AFTER})
        rows = conn.execute(
            text("UPDATE plugin_trackify_outbox SET status = 'sending', "
                 " locked_by = :w, locked_at = :now, updated_at = :now "
                 "WHERE id IN ("
                 "  SELECT id FROM plugin_trackify_outbox "
                 "  WHERE status = 'pending' AND next_attempt_at <= :now "
                 "  ORDER BY next_attempt_at, id LIMIT :lim FOR UPDATE SKIP LOCKED"
                 ") RETURNING id, external_id, kind, contact_id, phone, payload, "
                 "           occurred_at, attempts"),
            {"w": worker, "now": now, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def _finish(row_id: int, status: str, **fields) -> None:
    sets = ["status = :st", "updated_at = :now", "locked_by = ''"]
    params = {"id": row_id, "st": status, "now": _now()}
    for k, v in fields.items():
        sets.append(f"{k} = :{k}")
        params[k] = v
    with make_plugin_db() as conn:
        conn.execute(text(f"UPDATE plugin_trackify_outbox SET {', '.join(sets)} "
                          "WHERE id = :id"), params)


def _retry(row: dict, error: str, http_status: int | None, *, count_attempt: bool) -> None:
    attempts = int(row["attempts"] or 0) + (1 if count_attempt else 0)
    if attempts > MAX_ATTEMPTS:
        _finish(row["id"], "failed", attempts=attempts, last_error=error[:500],
                last_http_status=http_status)
        logger.warning("trackify: desisti de %s depois de %d tentativas (%s)",
                       row["external_id"], attempts, error[:120])
        return
    # Jitter: sem ele um backlog inteiro retenta em lockstep e re-estoura o limite.
    delay = min(2.0 * (2 ** max(0, attempts - 1)), BACKOFF_CAP)
    delay *= 0.8 + random.random() * 0.4
    _finish(row["id"], "pending", attempts=attempts, last_error=error[:500],
            last_http_status=http_status, next_attempt_at=_now() + delay)


async def deliver_one(client: httpx.AsyncClient, row: dict, url: str,
                      api_key: str) -> str:
    """Entrega uma linha. Devolve o estado final para o log/telemetria.

    ⚠️ Tudo que toca banco vai para ``asyncio.to_thread``: ``build_body`` resolve
    identidade no banco do NEXUS (outro servidor) e as transições escrevem no
    banco local. Chamar isso direto de uma corrotina travaria o event loop do
    app inteiro — o WhatsBot pararia de responder enquanto a fila drena.
    """
    try:
        body = await build_body(client, row)
    except Exception as e:  # noqa: BLE001 — payload corrompido não é retryável
        await asyncio.to_thread(_finish, row["id"], "blocked",
                                last_error=f"payload inválido: {type(e).__name__}")
        return "blocked"

    # A ingestão aceita a API key do Trackify (escopo `ingest`) — é a MESMA
    # credencial da leitura e da escrita, e por isso o plugin guarda um segredo
    # só. A chave de canal (`X-Trackify-Key`, configurada em channels.config)
    # continua sendo enviada quando existir: instalações antigas dependem dela, e
    # o servidor confere a do módulo primeiro e cai na do canal depois.
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Trackify-Key"] = api_key
    chave_modulo = tk_client.api_key()
    if chave_modulo:
        headers["X-API-Key"] = chave_modulo
    try:
        resp = await client.post(url, json=body, headers=headers)
    except Exception as e:  # noqa: BLE001
        note_cooldown(COOLDOWN_FLOOR)
        await asyncio.to_thread(_retry, row, f"rede: {type(e).__name__}", None,
                                count_attempt=True)
        return "retry"

    status = resp.status_code
    if status in (200, 202):
        data = {}
        try:
            data = (resp.json() or {}).get("data") or {}
        except Exception:  # noqa: BLE001
            pass
        tc = str(data.get("contactId") or "")
        await asyncio.to_thread(_settle_sent, row, tc,
                                str(data.get("eventId") or ""), status)
        return "sent"

    if status == 429:
        retry_after = resp.headers.get("retry-after")
        try:
            note_cooldown(float(retry_after))
        except (TypeError, ValueError):
            note_cooldown(COOLDOWN_FLOOR)
        # NÃO conta tentativa: a fila está saudável, o ritmo é que estourou.
        await asyncio.to_thread(_retry, row, "429 (limite de ingestão)", status,
                                count_attempt=False)
        return "throttled"

    if status in (400, 401, 403, 404, 422):
        await asyncio.to_thread(
            _finish, row["id"], "blocked", last_http_status=status,
            last_error=f"{status}: {resp.text[:300]}")
        logger.warning("trackify: %s bloqueado (HTTP %s) — configuração do canal?",
                       row["external_id"], status)
        return "blocked"

    note_cooldown(COOLDOWN_FLOOR)
    await asyncio.to_thread(_retry, row, f"HTTP {status}: {resp.text[:200]}", status,
                            count_attempt=True)
    return "retry"


def _settle_sent(row: dict, trackify_contact_id: str, event_id: str,
                 status: int) -> None:
    """Fecha uma entrega bem-sucedida (bloqueante — chamar via ``to_thread``).

    Se o CDP devolveu um contato DIFERENTE do que temos em cache, houve um merge
    lá dentro: o merge deleta a grafia do perdedor, então o valor cacheado ficou
    obsoleto sem nenhum aviso. Invalidamos para a próxima entrega re-resolver.
    """
    cached = _cached_identity(row["phone"])
    if trackify_contact_id and cached and cached.get("trackify_contact_id") \
            and cached["trackify_contact_id"] != trackify_contact_id:
        _forget_identity(row["phone"])
    _finish(row["id"], "sent", last_error="", last_http_status=status,
            trackify_contact_id=trackify_contact_id, trackify_event_id=event_id)


def prune(max_age_days: int) -> int:
    """Descarta o que envelheceu demais — com contador, nunca em silêncio."""
    cutoff = _now() - max_age_days * 86400
    with make_plugin_db() as conn:
        res = conn.execute(
            text("UPDATE plugin_trackify_outbox SET status = 'dropped', updated_at = :now, "
                 " last_error = 'expirou na fila' "
                 "WHERE status IN ('pending', 'failed') AND occurred_at < :cutoff"),
            {"now": _now(), "cutoff": cutoff})
    return int(res.rowcount or 0)


def stats() -> dict:
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT status, COUNT(*) AS n FROM plugin_trackify_outbox "
                 "GROUP BY status")).mappings().all()
        oldest = conn.execute(
            text("SELECT MIN(occurred_at) FROM plugin_trackify_outbox "
                 "WHERE status = 'pending'")).scalar()
    out = {r["status"]: int(r["n"]) for r in rows}
    out["backlog_age_s"] = int(_now() - float(oldest)) if oldest else 0
    out["cooldown_s"] = int(cooldown_remaining())
    return out
