"""Consentimento de marketing: clique em botão → campo de descadastro no CDP.

Duas metades bem separadas, pelo mesmo motivo do ``push.py``: **o handler de
evento NUNCA posta**. O barramento é fire-and-forget e engole exceção, então uma
falha de rede dentro do handler viraria perda silenciosa — e aqui o dado perdido
é um pedido de descadastro, que tem peso legal.

O handler marca o contato numa fila própria e a task supervisionada entrega,
recalculando o valor na hora do envio. Com isso um contato que clica "sair" e
depois "voltar" em dez segundos produz **um** PUT, com o estado final.

Fila PRÓPRIA e não a do ``push``: aquela é dirigida pelo mapeamento
atributo-do-WhatsBot ↔ slug-do-CDP, e o campo de descadastro não tem contraparte
no WhatsBot. Reusá-la arrastaria junto o motor de conflito e o ``pull``, que
poderia **sobrescrever** um descadastro com o valor antigo do CDP.
"""

from __future__ import annotations

import logging
import os
import random
import time

from sqlalchemy import text

from plugins.context import make_plugin_db

from . import _config, consent_match, field_map, push, writer
from . import client as tk_client

logger = logging.getLogger("plugins.trackify.consent")

OFFICIAL_PROVIDER = "whatsapp_cloud"

MAX_ATTEMPTS = 8
BACKOFF_CAP = 300.0
LOCK_STALE_AFTER = 300.0
CHANNEL_TTL = 30.0
MAX_RULES = 200

# Contato sem cadastro no CDP não é falha do item: pode aparecer daqui a pouco
# (um e-mail preenchido no painel, a varredura de conferência criando o vínculo).
# Desistir em silêncio de um descadastro é o pior desfecho possível, então
# reagenda de hora em hora por um dia antes de virar erro visível na tela.
UNLINKED_RETRY = 3600.0
UNLINKED_GIVE_UP = 24 * 3600.0

_worker_id = f"{os.getpid()}"

# Cache de provider por canal. ``message.received`` é o evento mais quente do
# sistema — mesmo com os guards baratos na frente, uma ida ao banco por clique
# não precisa virar uma por mensagem. Precedente: o cache de tipos de JID em
# ``app/services/message_ingest_service.py``.
_channel_cache: dict[str, tuple[float, str]] = {}


def _now() -> float:
    return time.time()


def reset_for_tests() -> None:
    """Zera o estado de módulo. Só para a suíte — o cache é global."""
    _channel_cache.clear()


# ── Gates ────────────────────────────────────────────────────────────────

def provider_of(channel_id: str) -> str:
    if not channel_id:
        return ""
    agora = _now()
    hit = _channel_cache.get(channel_id)
    if hit and (agora - hit[0]) < CHANNEL_TTL:
        return hit[1]
    try:
        from db.repositories import channel_repo
        ch = channel_repo.get(channel_id) or {}
    except Exception:  # noqa: BLE001 — falha de leitura não é cacheada
        return ""
    provider = str(ch.get("provider") or "")
    _channel_cache[channel_id] = (agora, provider)
    return provider


def is_official(channel_id: str) -> bool:
    """Gate DURO da feature. Só WhatsApp oficial (Cloud API)."""
    return provider_of(channel_id) == OFFICIAL_PROVIDER


def official_channels() -> list[dict]:
    """Canais elegíveis, para a tela oferecer. Nunca levanta."""
    try:
        from db.repositories import channel_repo
        rows = channel_repo.list_all() or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ch in rows:
        if str(ch.get("provider") or "") != OFFICIAL_PROVIDER:
            continue
        if int(ch.get("archived") or 0):
            continue
        out.append({
            "channel_id": ch.get("id"),
            "display_name": ch.get("display_name") or ch.get("id"),
            "own_phone": ch.get("own_phone") or "",
            "enabled": int(ch.get("enabled") or 0),
        })
    return out


def allowed_channel_ids() -> set[str]:
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(
                "SELECT channel_id FROM plugin_trackify_consent_channel "
                "WHERE enabled = 1")).mappings().all()
        return {str(r["channel_id"]) for r in rows}
    except Exception:  # noqa: BLE001
        return set()


# ── Gancho do barramento ─────────────────────────────────────────────────

def on_message_received(ctx, payload: dict) -> None:
    """Um clique de botão virou intenção de consentimento?

    A ordem dos guards é deliberada: a primeira comparação é em memória e mata
    ~99,9% do tráfego (toda mensagem de texto) **sem tocar no banco**. É isso
    que autoriza este plugin a assinar o evento mais quente do sistema.
    """
    try:
        p = payload or {}
        if p.get("media_type") != "interactive":
            return
        if p.get("is_group"):
            return

        cand = consent_match.tokens(p.get("media_extras"))
        if not cand:
            # Forma que não reconhecemos (ex.: o {button_id,title} do GOWA, ou
            # uma resposta de Flow). Segunda linha de defesa além do gate de
            # provider abaixo.
            return

        channel_id = str(p.get("channel_id") or "")
        if not is_official(channel_id):
            return

        # Registrado SEMPRE, antes da allow-list e do interruptor: é a fonte da
        # tela "botões vistos recentemente", e sem ela o operador não tem como
        # descobrir qual string a Meta devolve para configurar a regra.
        _record_seen(channel_id, cand)

        if channel_id not in allowed_channel_ids():
            return
        if not _config.consent_ready():
            return

        rule = consent_match.resolve(cand, list_rules(enabled_only=True), channel_id)
        if not rule or rule.get("meaning") == consent_match.IGNORE:
            return

        from db.repositories import contact_repo
        contact = contact_repo.get_by_phone(str(p.get("phone") or ""))
        if not contact or contact.get("is_group"):
            return

        contact_id = int(contact["id"])
        _record_state(contact_id, rule, cand, channel_id, str(p.get("msg_id") or ""))
        bump(int(rule["id"]))
        enqueue(contact_id, f"botao:{rule['meaning']}")
    except Exception:  # noqa: BLE001 — handler de bus nunca derruba o pipeline
        logger.debug("trackify: falha ao processar clique de consentimento",
                     exc_info=True)


def _record_seen(channel_id: str, cand: dict) -> None:
    chave = consent_match.seen_key(cand)
    if not chave:
        return
    agora = _now()
    try:
        with make_plugin_db() as conn:
            conn.execute(text(
                "INSERT INTO plugin_trackify_consent_seen "
                "(channel_id, match_norm, raw_payload, raw_text, shape, hits, "
                " first_seen_at, last_seen_at) "
                "VALUES (:c, :k, :p, :t, :s, 1, :now, :now) "
                "ON CONFLICT (channel_id, match_norm) DO UPDATE SET "
                " hits = plugin_trackify_consent_seen.hits + 1, "
                " last_seen_at = :now, raw_payload = EXCLUDED.raw_payload, "
                " raw_text = EXCLUDED.raw_text, shape = EXCLUDED.shape"),
                {"c": channel_id, "k": chave[:300], "p": cand["payload"][:300],
                 "t": cand["text"][:300], "s": cand["shape"], "now": agora})
    except Exception:  # noqa: BLE001 — o registro de descoberta nunca bloqueia
        logger.debug("trackify: falha ao registrar botão visto", exc_info=True)


def _record_state(contact_id: int, rule: dict, cand: dict, channel_id: str,
                  msg_id: str) -> None:
    agora = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "INSERT INTO plugin_trackify_consent_state "
            "(contact_id, desired, channel_id, raw_payload, raw_text, msg_id, "
            " button_id, clicked_at, clicks, created_at, updated_at) "
            "VALUES (:cid, :d, :ch, :p, :t, :m, :b, :now, 1, :now, :now) "
            "ON CONFLICT (contact_id) DO UPDATE SET "
            " desired = EXCLUDED.desired, channel_id = EXCLUDED.channel_id, "
            " raw_payload = EXCLUDED.raw_payload, raw_text = EXCLUDED.raw_text, "
            " msg_id = EXCLUDED.msg_id, button_id = EXCLUDED.button_id, "
            " clicked_at = EXCLUDED.clicked_at, "
            " clicks = plugin_trackify_consent_state.clicks + 1, "
            " updated_at = EXCLUDED.updated_at"),
            {"cid": contact_id, "d": rule["meaning"], "ch": channel_id,
             "p": cand["payload"][:300], "t": cand["text"][:300],
             "m": msg_id[:120], "b": int(rule["id"]), "now": agora})


def get_state(contact_id: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(
            "SELECT * FROM plugin_trackify_consent_state WHERE contact_id = :c"),
            {"c": contact_id}).mappings().first()
    return dict(row) if row else None


# ── Regras (configuração) ────────────────────────────────────────────────

_RULE_COLS = ("id, channel_id, match_field, match_value, match_norm, meaning, "
              "template_name, template_category, enabled, position, hits, "
              "last_hit_at, created_at, updated_at")


def list_rules(*, enabled_only: bool = False) -> list[dict]:
    sql = f"SELECT {_RULE_COLS} FROM plugin_trackify_consent_button"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY position, id"
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(sql)).mappings().all()
    except Exception:  # noqa: BLE001
        return []
    return [dict(r) for r in rows]


def validate_rules(rows, *, official_ids: set[str]) -> tuple[list[dict], dict]:
    """``(linhas_limpas, erros_por_índice)``.

    Erros são POR LINHA (``{"2": ["..."]}``) e não uma frase só, no molde de
    ``field_map.validate``: com dez botões na tela, "configuração inválida" não
    diz qual deles.

    ``official_ids`` entra como parâmetro para esta função continuar testável
    sem tocar no banco.
    """
    errors: dict[str, list[str]] = {}
    clean: list[dict] = []

    def err(i: int, msg: str) -> None:
        errors.setdefault(str(i), []).append(msg)

    if not isinstance(rows, list):
        return [], {"_": ["Formato inválido."]}
    if len(rows) > MAX_RULES:
        return [], {"_": [f"Máximo de {MAX_RULES} regras."]}

    vistos: dict[tuple[str, str], int] = {}

    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            err(i, "Formato inválido.")
            continue

        channel_id = str(raw.get("channel_id") or "").strip()
        match_field = str(raw.get("match_field") or "any").strip()
        match_value = str(raw.get("match_value") or "").strip()
        meaning = str(raw.get("meaning") or "").strip()
        template_name = str(raw.get("template_name") or "").strip()
        template_category = str(raw.get("template_category") or "").strip().upper()
        enabled = bool(raw.get("enabled", True))

        if channel_id and channel_id not in official_ids:
            err(i, "Este canal não existe ou não é de WhatsApp oficial.")
            continue
        if match_field not in ("any", "payload", "text"):
            err(i, "Critério de casamento inválido.")
            continue
        if not match_value:
            err(i, "Informe o texto ou o payload do botão.")
            continue
        if meaning not in consent_match.MEANINGS:
            err(i, "Escolha o que o botão significa.")
            continue

        match_norm = consent_match.normalize(match_value)
        if not match_norm:
            err(i, "Este valor não sobra nada depois de normalizado.")
            continue

        # Decisão de escopo: só botão de template MARKETING vira regra. O clique
        # em si não diz de qual template veio, então este é o ÚNICO ponto onde a
        # categoria pode ser exigida — vale para o que a tela importou. Regra
        # digitada à mão chega sem categoria e é aceita.
        if template_category and template_category != "MARKETING":
            err(i, f"Só botões de template MARKETING viram regra "
                   f"(este é {template_category}).")
            continue

        dup = vistos.get((channel_id, match_norm))
        if dup is not None:
            err(i, f"Este botão já está configurado na linha {dup + 1}.")
            continue
        vistos[(channel_id, match_norm)] = i

        clean.append({
            "channel_id": channel_id,
            "match_field": match_field,
            "match_value": match_value,
            "match_norm": match_norm,
            "meaning": meaning,
            "template_name": template_name,
            "template_category": template_category,
            "enabled": 1 if enabled else 0,
            "position": len(clean),
        })

    return clean, errors


def replace_rules(rows: list[dict]) -> None:
    """Troca o conjunto inteiro numa transação.

    Apaga-e-insere (em vez de diferenciar) porque o índice UNIQUE
    ``(channel_id, match_norm)`` tropeçaria numa simples reordenação. Os
    contadores de acerto (``hits``) são reancorados pela chave lógica: sem isso
    editar o rótulo de um botão zeraria a evidência de que ele vinha sendo
    clicado.
    """
    agora = _now()
    with make_plugin_db() as conn:
        antigos = conn.execute(text(
            "SELECT channel_id, match_norm, hits, last_hit_at, created_at "
            "FROM plugin_trackify_consent_button")).mappings().all()
        historico = {(str(r["channel_id"]), str(r["match_norm"])): dict(r)
                     for r in antigos}
        conn.execute(text("DELETE FROM plugin_trackify_consent_button"))
        for r in rows:
            velho = historico.get((r["channel_id"], r["match_norm"])) or {}
            conn.execute(text(
                "INSERT INTO plugin_trackify_consent_button "
                "(channel_id, match_field, match_value, match_norm, meaning, "
                " template_name, template_category, enabled, position, hits, "
                " last_hit_at, created_at, updated_at) "
                "VALUES (:ch, :mf, :mv, :mn, :me, :tn, :tc, :en, :po, :hits, "
                " :last, :created, :now)"),
                {"ch": r["channel_id"], "mf": r["match_field"],
                 "mv": r["match_value"], "mn": r["match_norm"],
                 "me": r["meaning"], "tn": r["template_name"],
                 "tc": r["template_category"], "en": r["enabled"],
                 "po": r["position"], "hits": int(velho.get("hits") or 0),
                 "last": velho.get("last_hit_at"),
                 "created": velho.get("created_at") or agora, "now": agora})


def bump(rule_id: int) -> None:
    """Incremento isolado do contador. Nunca reescreve a linha inteira: a tela
    pode estar salvando o conjunto no mesmo instante."""
    try:
        with make_plugin_db() as conn:
            conn.execute(text(
                "UPDATE plugin_trackify_consent_button "
                "SET hits = hits + 1, last_hit_at = :now WHERE id = :id"),
                {"id": rule_id, "now": _now()})
    except Exception:  # noqa: BLE001
        logger.debug("trackify: falha ao contar acerto de regra", exc_info=True)


# ── Canais permitidos (configuração) ─────────────────────────────────────

def list_channels() -> list[dict]:
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(
                "SELECT channel_id, enabled FROM plugin_trackify_consent_channel "
                "ORDER BY channel_id")).mappings().all()
    except Exception:  # noqa: BLE001
        return []
    return [dict(r) for r in rows]


def replace_channels(channel_ids, *, official_ids: set[str]) -> list[str]:
    """Troca a allow-list. Devolve os ids aceitos (os não-oficiais caem fora)."""
    aceitos = [c for c in dict.fromkeys(str(x).strip() for x in (channel_ids or []))
               if c and c in official_ids]
    agora = _now()
    with make_plugin_db() as conn:
        conn.execute(text("DELETE FROM plugin_trackify_consent_channel"))
        for cid in aceitos:
            conn.execute(text(
                "INSERT INTO plugin_trackify_consent_channel "
                "(channel_id, enabled, created_at, updated_at) "
                "VALUES (:c, 1, :now, :now)"), {"c": cid, "now": agora})
    return aceitos


# ── Botões vistos ────────────────────────────────────────────────────────

def list_seen(limit: int = 50) -> list[dict]:
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(
                "SELECT channel_id, match_norm, raw_payload, raw_text, shape, "
                " hits, first_seen_at, last_seen_at "
                "FROM plugin_trackify_consent_seen "
                "ORDER BY last_seen_at DESC LIMIT :lim"),
                {"lim": max(1, min(int(limit or 50), 200))}).mappings().all()
    except Exception:  # noqa: BLE001
        return []
    return [dict(r) for r in rows]


def prune_seen(days: int = 30) -> int:
    corte = _now() - max(1, int(days or 30)) * 86400.0
    try:
        with make_plugin_db() as conn:
            res = conn.execute(text(
                "DELETE FROM plugin_trackify_consent_seen WHERE last_seen_at < :c"),
                {"c": corte})
        return int(res.rowcount or 0)
    except Exception:  # noqa: BLE001
        return 0


# ── Fila ─────────────────────────────────────────────────────────────────

def enqueue(contact_id: int, reason: str = "") -> None:
    """Marca o contato. Idempotente por construção: o índice único parcial
    garante no máximo uma linha pendente por contato."""
    agora = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "INSERT INTO plugin_trackify_consent_outbox "
            "(contact_id, reason, status, next_attempt_at, enqueued_at, "
            " created_at, updated_at) "
            "VALUES (:c, :r, 'pending', :now, :now, :now, :now) "
            "ON CONFLICT (contact_id) WHERE status = 'pending' "
            "DO UPDATE SET reason = :r, updated_at = :now"),
            {"c": contact_id, "r": (reason or "")[:100], "now": agora})


def claim(limit: int, worker: str) -> list[dict]:
    agora = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_consent_outbox SET status = 'pending', "
            " locked_by = '' "
            "WHERE status = 'sending' AND COALESCE(locked_at, 0) < :stale"),
            {"stale": agora - LOCK_STALE_AFTER})
        rows = conn.execute(text(
            "UPDATE plugin_trackify_consent_outbox SET status = 'sending', "
            " locked_by = :w, locked_at = :now, updated_at = :now "
            "WHERE id IN ("
            "  SELECT id FROM plugin_trackify_consent_outbox "
            "  WHERE status = 'pending' AND next_attempt_at <= :now "
            "  ORDER BY next_attempt_at, id LIMIT :lim FOR UPDATE SKIP LOCKED"
            ") RETURNING id, contact_id, reason, attempts, enqueued_at"),
            {"w": worker, "now": agora, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def finish(row_id: int, status: str, *, error: str = "",
           http: int | None = None) -> None:
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_consent_outbox SET status = :st, "
            " locked_by = '', last_error = :err, last_http_status = :http, "
            " updated_at = :now WHERE id = :id"),
            {"id": row_id, "st": status, "err": (error or "")[:500],
             "http": http, "now": _now()})


def retry_later(row_id: int, attempts: int, *, error: str = "",
                http: int | None = None, count_attempt: bool = True,
                delay: float | None = None) -> None:
    """Reagenda com backoff exponencial e jitter, ou com um atraso fixo.

    ``count_attempt=False`` para 429 e para "sem vínculo": é falha de RITMO ou
    de estado externo, não do item — incrementar mataria um backlog saudável.
    """
    n = attempts + 1 if count_attempt else attempts
    if n >= MAX_ATTEMPTS:
        finish(row_id, "failed", error=error, http=http)
        return
    if delay is None:
        delay = min(2.0 * (2 ** max(0, n - 1)), BACKOFF_CAP)
        delay *= 0.8 + random.random() * 0.4
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_consent_outbox SET status = 'pending', "
            " attempts = :n, next_attempt_at = :next, locked_by = '', "
            " last_error = :err, last_http_status = :http, updated_at = :now "
            "WHERE id = :id"),
            {"id": row_id, "n": n, "next": _now() + delay,
             "err": (error or "")[:500], "http": http, "now": _now()})


def stats() -> dict:
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(
                "SELECT status, COUNT(*) AS n FROM plugin_trackify_consent_outbox "
                "GROUP BY status")).mappings().all()
        return {r["status"]: int(r["n"]) for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def list_queue(status: str = "", limit: int = 50) -> list[dict]:
    sql = ("SELECT id, contact_id, reason, status, attempts, last_error, "
           " last_http_status, enqueued_at, updated_at "
           "FROM plugin_trackify_consent_outbox")
    params: dict = {"lim": max(1, min(int(limit or 50), 200))}
    if status:
        sql += " WHERE status = :st"
        params["st"] = status
    sql += " ORDER BY updated_at DESC LIMIT :lim"
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
    except Exception:  # noqa: BLE001
        return []
    return [dict(r) for r in rows]


def retry_failed() -> int:
    """Devolve à fila tudo que parou em ``failed``/``blocked``."""
    agora = _now()
    with make_plugin_db() as conn:
        res = conn.execute(text(
            "UPDATE plugin_trackify_consent_outbox SET status = 'pending', "
            " attempts = 0, next_attempt_at = :now, locked_by = '', "
            " last_error = '', updated_at = :now "
            "WHERE status IN ('failed', 'blocked')"), {"now": agora})
    return int(res.rowcount or 0)


# ── Entrega ──────────────────────────────────────────────────────────────

def desired_value(desired: str) -> str:
    return (_config.consent_optout_value() if desired == consent_match.OPTOUT
            else _config.consent_optin_value())


async def deliver_one(client, row: dict) -> str:
    """Processa uma linha da fila. Devolve um rótulo do desfecho (para o laço)."""
    import asyncio

    contact_id = int(row["contact_id"])
    estado = await asyncio.to_thread(get_state, contact_id)
    if not estado or not estado.get("desired"):
        await asyncio.to_thread(finish, row["id"], "blocked",
                                error="sem decisão de consentimento registrada")
        return "skipped"

    from db.repositories import contact_repo
    contact = await asyncio.to_thread(contact_repo.get, contact_id)
    if not contact:
        await asyncio.to_thread(finish, row["id"], "blocked",
                                error="contato não existe mais")
        return "skipped"
    if contact.get("is_group"):
        await asyncio.to_thread(finish, row["id"], "blocked",
                                error="grupo não tem cadastro no CDP")
        return "skipped"

    # Reuso deliberado: a resolução de identidade, o cache de 7 dias, a regra de
    # "só grava com acerto ÚNICO" e o "nunca cria contato no CDP" já vivem lá.
    # Uma segunda cópia divergiria e passaria a colar consentimento no cadastro
    # errado.
    try:
        tk_id = await push._linked_id(client, str(contact.get("phone") or ""), contact)
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(retry_later, row["id"], int(row["attempts"]),
                                error=f"resolução de identidade falhou: {type(e).__name__}",
                                count_attempt=False)
        return "read_failed"

    if not tk_id:
        # ⚠️ Nada de ``row.get(...) or _now()``: um epoch 0 é falsy e a idade
        # daria zero, então a linha nunca envelheceria e o item ficaria
        # reagendando para sempre sem nunca aparecer como erro na tela.
        try:
            enfileirado_em = float(row.get("enqueued_at"))
        except (TypeError, ValueError):
            enfileirado_em = _now()
        if (_now() - enfileirado_em) > UNLINKED_GIVE_UP:
            await asyncio.to_thread(
                finish, row["id"], "blocked",
                error="contato não vinculado a um cadastro no Trackify há mais "
                      "de 24h — vincule ou marque o descadastro à mão no CDP")
            return "unlinked"
        await asyncio.to_thread(retry_later, row["id"], int(row["attempts"]),
                                error="contato ainda não vinculado a um cadastro "
                                      "no Trackify",
                                count_attempt=False, delay=UNLINKED_RETRY)
        return "unlinked_retry"

    slug = _config.consent_field_slug()
    valor = desired_value(str(estado["desired"]))

    # Já está como queremos: caminho comum de clique repetido. Zero HTTP é o que
    # mantém o orçamento de 30/min utilizável.
    if (estado.get("written_value") is not None
            and str(estado["written_value"]) == valor
            and str(estado.get("trackify_contact_id") or "") == tk_id):
        await asyncio.to_thread(finish, row["id"], "sent")
        return "noop"

    if bool(_config.setting("consent_dry_run", True)):
        await asyncio.to_thread(
            finish, row["id"], "sent",
            error=f"[modo seco] gravaria {slug}={valor!r} no contato {tk_id}")
        return "dry_run"

    if not tk_client.is_configured():
        await asyncio.to_thread(
            retry_later, row["id"], int(row["attempts"]),
            error=_config.setting("sync_blocked_reason")
                  or "API key do Trackify não configurada",
            count_attempt=False)
        return "no_credential"

    res = await writer.put_contact(client, tk_id, {slug: valor})
    return await asyncio.to_thread(_apply_result, row, contact_id, tk_id, valor, res)


def _apply_result(row: dict, contact_id: int, tk_id: str, valor: str, res) -> str:
    if res.verdict == writer.OK:
        _record_written(contact_id, tk_id, valor)
        finish(row["id"], "sent", http=res.http_status)
        return "sent"

    if res.verdict == writer.UNLINKED:
        # Merge no CDP apagou a grafia deste contato: esquece o vínculo para a
        # próxima tentativa resolver do zero.
        push._forget_identity(contact_id)
        retry_later(row["id"], int(row["attempts"]), error=res.error,
                    http=res.http_status, count_attempt=False,
                    delay=UNLINKED_RETRY)
        return "unlinked"

    if res.verdict == writer.UNAUTHORIZED:
        # A credencial é UMA só para as duas sincronizações: parar as duas é o
        # comportamento certo, e a tela mostra o motivo em vez de martelar o
        # Nexus do cliente.
        push._block_sync(res.error)
        finish(row["id"], "blocked", error=res.error, http=res.http_status)
        return "unauthorized"

    if res.verdict in (writer.BLOCKED, writer.CONFLICT):
        _record_error(contact_id, res.error)
        finish(row["id"], "blocked", error=res.error, http=res.http_status)
        return "blocked"

    if res.verdict == writer.THROTTLED:
        push.note_cooldown(res.retry_after or 5.0)
        retry_later(row["id"], int(row["attempts"]), error=res.error,
                    http=res.http_status, count_attempt=False)
        return "throttled"

    retry_later(row["id"], int(row["attempts"]), error=res.error,
                http=res.http_status)
    return "retry"


def _record_written(contact_id: int, tk_id: str, valor: str) -> None:
    agora = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_consent_state SET written_value = :v, "
            " written_at = :now, trackify_contact_id = :tk, last_error = '', "
            " updated_at = :now WHERE contact_id = :c"),
            {"c": contact_id, "v": valor, "tk": tk_id, "now": agora})


def _record_error(contact_id: int, erro: str) -> None:
    agora = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_consent_state SET last_error = :e, "
            " updated_at = :now WHERE contact_id = :c"),
            {"c": contact_id, "e": (erro or "")[:500], "now": agora})


# ── Status para a tela ───────────────────────────────────────────────────

def _mapped_here(slug: str) -> bool:
    """O slug do descadastro também está mapeado na aba 'Campos do contato'?"""
    if not slug:
        return False
    try:
        return any(str(m.get("tk_slug") or "") == slug
                   for m in field_map.list_maps())
    except Exception:  # noqa: BLE001
        return False



async def status(client) -> dict:
    """Diagnóstico acionável: o que falta para isto funcionar.

    Distingue os estados de propósito — *desligado*, *sem credencial*, *sem
    canal marcado*, *sem regra ativa* e *campo errado no CDP* falham todos do
    mesmo jeito (nada acontece) e exigem consertos diferentes.
    """
    import asyncio

    slug = _config.consent_field_slug()
    optout = _config.consent_optout_value()
    optin = _config.consent_optin_value()

    out = {
        "enabled": bool(_config.setting("consent_enabled", False)),
        "dry_run": bool(_config.setting("consent_dry_run", True)),
        "credential_set": _config.credential_set(),
        "blocked_reason": (_config.setting("sync_blocked_reason") or "").strip(),
        "field_slug": slug,
        "optout_value": optout,
        "optin_value": optin,
        "value_errors": consent_match.validate_values(optout, optin),
        "channels": await asyncio.to_thread(official_channels),
        "allowed_channel_ids": sorted(await asyncio.to_thread(allowed_channel_ids)),
        "rules": await asyncio.to_thread(list_rules),
        "queue": await asyncio.to_thread(stats),
        "field": None,
        "field_error": "",
        # Guarda cruzada com a aba "Campos do contato": o mesmo slug mapeado
        # dos dois lados ligaria o ``pull``, que traria o valor antigo do CDP e
        # poderia DESFAZER um descadastro.
        "field_map_conflict": await asyncio.to_thread(_mapped_here, slug),
    }

    if not out["credential_set"]:
        out["field_error"] = "API key do Trackify não configurada."
        return out

    res = await tk_client.cached("custom-fields", lambda: tk_client.custom_fields(client))
    if not res.ok:
        out["field_error"] = res.error or "não foi possível ler os campos do Trackify"
        return out

    linhas = res.data if isinstance(res.data, list) else (res.data or {}).get("data") or []
    achado = next((f for f in linhas if str(f.get("slug") or "") == slug), None)
    if achado is None:
        out["field_error"] = (
            f"O campo '{slug}' não existe no Trackify. Enquanto ele não existir, "
            "o Campanhas também não consegue ler o descadastro — os dois lados "
            "precisam apontar para o mesmo slug.")
        return out

    out["field"] = {
        "slug": slug,
        "name": achado.get("name") or "",
        "is_active": bool(achado.get("isActive", achado.get("is_active", True))),
        "is_identifier": bool(achado.get("isIdentifier",
                                         achado.get("is_identifier", False))),
        "field_type": ((achado.get("fieldType") or {}).get("name")
                       if isinstance(achado.get("fieldType"), dict)
                       else achado.get("fieldType") or achado.get("field_type") or ""),
        "regex_pattern": achado.get("regexPattern") or achado.get("regex_pattern") or "",
    }
    if not out["field"]["is_active"]:
        out["field_error"] = (
            f"O campo '{slug}' está desativado no Trackify: o PUT devolve 200 e "
            "não grava nada.")
    return out
