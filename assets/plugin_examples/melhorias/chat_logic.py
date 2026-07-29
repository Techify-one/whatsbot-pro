"""Conversa agêntica de melhoria (plano 51 · 02 F3/F5/F6).

Orquestra o chat entre o painel e o executor Claude Code externo:

- Persistência nas 3 tabelas do plugin (``plugin_melhorias_ai_conversations`` /
  ``_ai_messages`` / ``_ai_approvals``) — o executor escreve nelas via as rotas
  ``/public/_internal/*`` (write-through) e o painel as lê.
- ``start_conversation`` = gate D1-(a): o humano aprova a IA COMEÇAR (+ injeta
  observação); monta o payload inicial (mesmo ``build_analysis_payload`` do
  backend direto), inicia o runner no executor e envia a 1ª mensagem.
- Consumidor SSE→WS (D02-d): o gateway consome a SSE do executor server-side e
  re-emite cada evento como ``broadcast("plugin_melhorias_ai_event", {...})``
  no /ws que o painel já mantém (o painel filtra por ``conversation_id``).
  Tasks por-conversa em registry module-level — o toggle do plugin reinicia o
  processo, então não há órfão sobrevivente.
- ``decide_approval`` = gate D1-(b): V/X por mutação, idempotente
  (``approved IS NULL`` = pendente; já decidido ⇒ erro → 409 na rota).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid

from sqlalchemy import text

from db.repositories import config_repo
from plugins.context import broadcast, make_plugin_db

from . import ai_client

logger = logging.getLogger(__name__)

_CONV = "plugin_melhorias_ai_conversations"
_MSGS = "plugin_melhorias_ai_messages"
_APPR = "plugin_melhorias_ai_approvals"

# ``AUTH_EXPIRED`` (plano 60) é recuperável — distinto de ``ERRORED``: a conversa
# volta a viver com um ``resume`` depois que o operador renova a sessão do Claude.
# Sem migration: ``status`` é TEXT sem CHECK (003_ai_chat.sql).
CONV_STATUSES = ("ACTIVE", "COMPLETED", "CANCELLED", "ERRORED", "AUTH_EXPIRED")

# Caps do blob de resume (molde do executor de referência).
RESUME_MAX_TURNS = 20
RESUME_MAX_CHARS = 4000

# Consumidores SSE ativos, por conversation_id (o restart do plugin derruba o
# processo inteiro, então o registry morre junto — nunca sobrevive órfão).
_consumers: dict[str, asyncio.Task] = {}


def now() -> float:
    return time.time()


def _dumps(v) -> str | None:
    if v is None:
        return None
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _loads(v):
    if v in (None, ""):
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


# ── Falhas do executor (plano 61 — sucessor do plano 60 · camada 2) ──────────
#
# O executor manda um ``kind`` TIPADO no frame ``event: error`` da SSE. A
# heurística de texto abaixo continua viva como FALLBACK para executor antigo
# (e para o balde ``unknown``), nunca como fonte primária: ela dá falso positivo
# quando a própria IA menciona um 401 numa análise, e é cega para limite de uso,
# cota e sobrecarga — que acabavam confundidos com sessão expirada.

AUTH_ERROR_MARKERS = (
    "authentication_error",
    "invalid authentication credentials",
    "please run /login",
    "invalid api key",
)
_AUTH_401_RE = re.compile(r"\b401\b")

# A taxonomia. ``fatal`` = mata a sessão GLOBAL (só a credencial inválida mata:
# é a única que o operador conserta sozinho, num botão). ``durable`` = o aviso
# precisa sobreviver a um F5 (cota não passa sozinha; um 429 passa em segundos).
# ``action`` é o que o frontend usa para escolher o rodapé — ele ramifica por
# AÇÃO, não por nome de kind, então um kind novo degrada para um cartão genérico
# sem exigir atualização do cliente.
EXECUTOR_KINDS = {
    "auth_required":  {"fatal": True,  "action": "relogin", "durable": True},
    "rate_limited":   {"fatal": False, "action": "wait",    "durable": False},
    "overloaded":     {"fatal": False, "action": "wait",    "durable": False},
    "quota_exceeded": {"fatal": False, "action": "billing", "durable": True},
    "unknown":        {"fatal": False, "action": "none",    "durable": False},
}

# Teto do ``retry_after``: o contrato não fixa unidade (assumimos segundos) e sem
# clamp um ``86400`` congelaria um consumidor por um dia.
RETRY_AFTER_MAX = 300
# Piso de espera de ``overloaded`` sem ``retry_after`` — religar em 2s num
# executor sobrecarregado é martelar.
OVERLOADED_FLOOR = 5.0

# Estado GLOBAL da sessão (não por-conversa): a credencial é do executor inteiro.
# Sem migration — mesmo padrão key-value do ``logic._setting``.
SESSION_STATE_KEY = "plugin.melhorias.ai_session_state"
SESSION_EVENT = "plugin_melhorias_ai_session"
# Avisos EFÊMEROS (limite/cota/sobrecarga) andam num evento SEPARADO. Reusar o
# ``SESSION_EVENT`` seria um bug: o painel faz ``setAiSession(d)`` (substitui),
# então um aviso transitório chegando depois apagaria a faixa de sessão expirada
# e reabriria o botão "Aprovar p/ iniciar" contra um servidor que ainda recusa.
# Com evento próprio, painel antigo em cache simplesmente o ignora.
NOTICE_EVENT = "plugin_melhorias_ai_notice"


def is_auth_error(text_value) -> bool:
    """A mensagem é o 401 do Claude vestido de conteúdo? (espelho do frontend)

    FALLBACK apenas — ver ``derive_kind``.
    """
    t = str(text_value or "").lower()
    if _AUTH_401_RE.search(t):
        return True
    return any(marker in t for marker in AUTH_ERROR_MARKERS)


def normalize_kind(raw) -> str | None:
    """``kind`` do executor → chave da tabela. Vazio ⇒ ``None`` ("não veio").

    Um kind que a tabela não conhece vira ``unknown`` — NUNCA fatal. Se o
    executor shipar um `credit_low` antes deste plugin saber o que é, o pior que
    acontece é um cartão genérico; matar a sessão por acidente, não.
    """
    k = str(raw or "").strip().lower()
    if not k:
        return None
    return k if k in EXECUTOR_KINDS else "unknown"


def derive_kind(data: dict | None) -> str | None:
    """A regra de classificação, num lugar só (stream e write-through a usam).

    ``unknown`` é um VALOR, não um vazio — escrever ``kind or heurística`` faria
    o fluxo parar no ``unknown`` e nunca chegar ao fallback, e uma sessão de fato
    expirada deixaria de ser detectada. Por isso o teste é explícito.
    """
    data = data or {}
    kind = normalize_kind(data.get("kind"))
    if kind in (None, "unknown") and is_auth_error(data.get("message")):
        return "auth_required"
    return kind


def kind_is_fatal(kind: str | None) -> bool:
    return bool(EXECUTOR_KINDS.get(kind or "", {}).get("fatal"))


def clamp_retry_after(v) -> int | None:
    """Segundos, clampado em ``0..RETRY_AFTER_MAX``. Ilegível ⇒ ``None``."""
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return max(0, min(n, RETRY_AFTER_MAX))


def get_session_state() -> dict:
    """``{status: 'ok'|'expired', at?, conversation_id?}``. Ausente/ilegível ⇒ ok."""
    raw = str(config_repo.get(SESSION_STATE_KEY, "") or "").strip()
    if not raw:
        return {"status": "ok"}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {"status": "ok"}
    return data if isinstance(data, dict) else {"status": "ok"}


def session_expired() -> bool:
    return str(get_session_state().get("status") or "") == "expired"


def _write_session_state(raw: str) -> None:
    try:
        config_repo.set(SESSION_STATE_KEY, raw)
    except Exception:  # noqa: BLE001 — o aviso ao painel vale mesmo sem persistir
        logger.warning("melhorias: falha ao gravar o estado da sessão de IA")


def _announce(event: str, payload: dict) -> dict:
    try:
        broadcast(event, payload)
    except Exception:  # noqa: BLE001
        logger.debug("melhorias: broadcast de %s falhou", event)
    return payload


def _persist_session_state(state: dict) -> dict:
    """Grava o estado GLOBAL e avisa TODOS os painéis abertos — quem chegar
    depois lê o mesmo estado no ``GET /config``.

    Existe como função separada de propósito: o antigo ``_publish_session_state``
    tinha a sentinela ``persist=None`` significando **apagar**, e o caminho novo
    (aviso efêmero, que não deve gravar nada) passaria por ela da forma
    intuitiva e limparia a sessão expirada em silêncio.
    """
    _write_session_state(json.dumps(state, ensure_ascii=False))
    return _announce(SESSION_EVENT, state)


def mark_session_expired(*, conversation_id: str | None = None,
                         message: str = "") -> dict:
    state = {"status": "expired", "kind": "auth_required", "at": now(),
             "conversation_id": conversation_id,
             "message": (message or "")[:500]}
    return _persist_session_state(state)


def clear_session_state() -> dict:
    """Sessão renovada/retomada: zera o estado e avisa os painéis.

    O ÚNICO caminho que escreve o estado "vivo". Persiste ``at`` em vez de
    limpar para string vazia: ``routes.test_connection`` usa a ausência de
    ``at`` como "nunca vimos nada" e reportava ``unknown`` para sempre depois de
    um relogin bem-sucedido.
    """
    state = {"status": "ok", "at": now()}
    _persist_session_state(state)
    # Um relogin/resume também mata qualquer aviso efêmero pendurado no painel.
    _announce(NOTICE_EVENT, {"status": "ok", "at": state["at"]})
    return state


def notify_executor_issue(kind: str, *, message: str = "",
                          retry_after=None, conversation_id: str | None = None) -> dict:
    """Aviso EFÊMERO de falha não-fatal: avisa os painéis e **não grava nada**.

    Transitório por natureza — um 429 passa em segundos, e um estado persistido
    que ninguém limpa vira aviso fantasma. O caso durável (cota) sobrevive a um
    F5 pelo histórico da conversa (``failure_kind`` na linha), não por aqui.
    """
    return _announce(NOTICE_EVENT, {
        "status": "degraded", "kind": kind,
        "message": (message or "")[:500],
        "retry_after": clamp_retry_after(retry_after),
        "conversation_id": conversation_id, "at": now()})


# ── Conversas ────────────────────────────────────────────────────────────────

def create_conversation(suggestion_id: int, *, user_id=None, model: str = "") -> dict:
    cid = uuid.uuid4().hex
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(text(
            f"INSERT INTO {_CONV} (id, suggestion_id, user_id, status, model, "
            "created_at, updated_at) VALUES (:id, :sid, :uid, 'ACTIVE', :model, "
            ":ts, :ts)"), {
                "id": cid, "sid": int(suggestion_id), "uid": user_id,
                "model": model or "", "ts": ts})
    return get_conversation(cid)


def get_conversation(cid: str) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(f"SELECT * FROM {_CONV} WHERE id = :id"),
                           {"id": cid}).mappings().first()
    return dict(row) if row else None


def list_conversations(suggestion_id: int) -> list[dict]:
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT * FROM {_CONV} WHERE suggestion_id = :sid "
            "ORDER BY created_at DESC"), {"sid": int(suggestion_id)}).mappings().all()
    return [dict(r) for r in rows]


def set_conversation_status(cid: str, status: str, *,
                            only_if: str | None = None) -> dict | None:
    """``only_if`` = só troca se o status ATUAL for esse (UPDATE guardado).

    Usado pelo caminho de falha: um frame de erro atrasado não pode rebaixar uma
    conversa já ``COMPLETED`` para ``AUTH_EXPIRED`` — o ``finalize_agentic_
    suggestion`` já rodou e a sugestão está fechada. Também estreita a janela em
    que uma falha em voo sobrescreve o ``ACTIVE`` que o ``resume`` acabou de
    gravar, logo depois de o operador clicar "Renovar sessão".
    """
    if status not in CONV_STATUSES:
        return None
    ts = now()
    done = status in ("COMPLETED", "CANCELLED", "ERRORED")
    guard = " AND status = :only_if" if only_if else ""
    params = {"st": status, "ts": ts, "done": done, "id": cid}
    if only_if:
        params["only_if"] = only_if
    with make_plugin_db() as conn:
        conn.execute(text(
            f"UPDATE {_CONV} SET status = :st, updated_at = :ts, "
            "completed_at = CASE WHEN :done THEN :ts ELSE completed_at END "
            f"WHERE id = :id{guard}"), params)
    return get_conversation(cid)


# ── Mensagens do chat (append-only) ──────────────────────────────────────────

def append_chat_message(cid: str, role: str, *, content: str | None = None,
                        tool_name: str | None = None, tool_input=None,
                        tool_result=None, token_usage=None,
                        failure_kind: str | None = None) -> int:
    """``failure_kind`` só é escrito por ``record_executor_failure`` (linhas
    ``role="system"``) — é o que permite à hidratação do chat ler o TIPO da
    falha em vez de adivinhá-lo pelo texto."""
    with make_plugin_db() as conn:
        mid = conn.execute(text(
            f"INSERT INTO {_MSGS} (conversation_id, role, content, tool_name, "
            "tool_input, tool_result, token_usage, failure_kind, created_at) "
            "VALUES (:cid, :role, :content, :tool_name, :tool_input, "
            ":tool_result, :token_usage, :failure_kind, :ts) RETURNING id"), {
                "cid": cid, "role": role, "content": content,
                "tool_name": tool_name, "tool_input": _dumps(tool_input),
                "tool_result": _dumps(tool_result),
                "token_usage": _dumps(token_usage),
                "failure_kind": failure_kind, "ts": now()}).scalar_one()
    return mid


def list_chat_messages(cid: str, limit: int = 500) -> list[dict]:
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT * FROM {_MSGS} WHERE conversation_id = :cid "
            "ORDER BY id LIMIT :lim"),
            {"cid": cid, "lim": max(1, min(int(limit), 2000))}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["tool_input"] = _loads(d.get("tool_input"))
        d["tool_result"] = _loads(d.get("tool_result"))
        d["token_usage"] = _loads(d.get("token_usage"))
        out.append(d)
    return out


# ── Aprovações (gate D1-b) ───────────────────────────────────────────────────

def register_approval(approval_id: str, cid: str, *, tool_name: str,
                      tool_input=None, summary: str = "") -> dict:
    with make_plugin_db() as conn:
        conn.execute(text(
            f"INSERT INTO {_APPR} (id, conversation_id, tool_name, tool_input, "
            "summary, approved, created_at) VALUES (:id, :cid, :tool, :input, "
            ":summary, NULL, :ts) ON CONFLICT (id) DO NOTHING"), {
                "id": approval_id, "cid": cid, "tool": tool_name,
                "input": _dumps(tool_input), "summary": summary or "",
                "ts": now()})
    return get_approval(approval_id)


def get_approval(approval_id: str) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(f"SELECT * FROM {_APPR} WHERE id = :id"),
                           {"id": approval_id}).mappings().first()
    if not row:
        return None
    d = dict(row)
    d["tool_input"] = _loads(d.get("tool_input"))
    return d


def list_approvals(cid: str) -> list[dict]:
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT * FROM {_APPR} WHERE conversation_id = :cid ORDER BY created_at"),
            {"cid": cid}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["tool_input"] = _loads(d.get("tool_input"))
        out.append(d)
    return out


def decide_approval(approval_id: str, cid: str, *, approved: bool,
                    reason: str = "", decided_by=None) -> tuple[dict | None, str | None]:
    """Decide um approval pendente. Idempotência: já decidido ⇒ erro (409)."""
    row = get_approval(approval_id)
    if not row or row.get("conversation_id") != cid:
        return None, "Aprovação não encontrada."
    if row.get("approved") is not None:
        return None, "Aprovação já decidida."
    with make_plugin_db() as conn:
        conn.execute(text(
            f"UPDATE {_APPR} SET approved = :ap, reason = :reason, "
            "decided_by = :by, decided_at = :ts WHERE id = :id"), {
                "ap": 1 if approved else 0, "reason": (reason or "")[:500],
                "by": decided_by, "ts": now(), "id": approval_id})
    return get_approval(approval_id), None


# ── Orquestração (async — chamada das rotas) ─────────────────────────────────

def _ws_emit(suggestion_id: int, cid: str, event: str, data: dict) -> None:
    """Re-emite um evento do executor no /ws do operador (D02-d). O painel
    filtra por ``conversation_id``."""
    try:
        broadcast("plugin_melhorias_ai_event", {
            "suggestion_id": suggestion_id, "conversation_id": cid,
            "event": event, "data": data or {}})
    except Exception:  # noqa: BLE001
        logger.debug("melhorias: broadcast do evento %s falhou", event)


async def start_conversation(sid: int, *, observation: str = "",
                             model: str = "", user_id=None,
                             handler=None) -> tuple[dict | None, str | None]:
    """Gate D1-(a): humano libera a IA + injeta observação; abre a conversa.

    Monta a mensagem inicial com o MESMO contexto do backend direto
    (``build_analysis_payload``) + a observação extra, inicia o runner no
    executor, envia a 1ª mensagem e liga o consumidor SSE→WS.
    """
    from . import generation, logic

    if not ai_client.is_configured():
        return None, ("Servidor de IA não configurado — defina a URL e o secret do "
                      "executor em Configurações de IA → seção Sugestão de melhoria.")
    # Portão (plano 60 · 2.6): com a sessão morta, toda conversa nova já nasce
    # morta — recusar aqui poupa a sugestão em vez de queimá-la.
    if session_expired():
        return None, ("Sessão do Claude expirada — renove a sessão antes de "
                      "iniciar uma nova análise.")
    suggestion = logic.get_suggestion(sid)
    if not suggestion:
        return None, "Sugestão não encontrada."
    if suggestion.get("status") not in ("pendente", "em_chat"):
        return None, "Sugestão já foi decidida."

    targets = logic.get_suggestion_messages(sid) or [{
        "content": suggestion.get("message_content") or "",
        "ts": suggestion.get("message_ts") or 0,
        "_id": suggestion.get("message_db_id"),
    }]
    ctx = generation.GenContext(
        handler=handler, phone=suggestion.get("contact_phone") or "",
        target_message=targets[0], target_messages=targets,
        feedback=suggestion.get("feedback") or "",
        conversation_id=suggestion.get("conversation_id"),
        model_override=logic._setting("model"),
        prompt_override=logic._setting("prompt"))
    try:
        initial = await asyncio.to_thread(
            generation.ExternalAgentGenerator.build_initial_message, ctx)
    except Exception as e:  # noqa: BLE001 — contexto degradado ainda permite o chat
        logger.warning("melhorias: build_analysis_payload falhou: %s", e)
        initial = (f"{generation.COMMUNICATION_STYLE_PREAMBLE}\n\n"
                   f"## Resposta marcada como incorreta\n"
                   f"{suggestion.get('message_content') or ''}\n\n"
                   f"## O que o operador disse que saiu errado\n"
                   f"{suggestion.get('feedback') or '(vazio)'}")
    if (observation or "").strip():
        initial += f"\n\n## Observação extra do aprovador\n{observation.strip()}"

    conv = await asyncio.to_thread(create_conversation, sid,
                                   user_id=user_id, model=model)
    cid = conv["id"]
    target = {"suggestion_id": sid,
              "phone": suggestion.get("contact_phone") or "",
              "conversation_id": suggestion.get("conversation_id")}
    try:
        await ai_client.start(cid, user_id=user_id, target=target, model=model)
        ensure_consumer(cid, sid, user_id=user_id)
        await ai_client.send(cid, user_id=user_id, text=initial)
    except Exception as e:  # noqa: BLE001
        logger.error("melhorias: start no executor falhou: %s", e)
        await asyncio.to_thread(set_conversation_status, cid, "ERRORED")
        stop_consumer(cid)
        return None, f"Falha ao iniciar a conversa no executor: {e}"

    await asyncio.to_thread(logic.mark_suggestion_in_chat, sid)
    return get_conversation(cid), None


async def resume_conversation(cid: str, *, user_id=None) -> tuple[dict | None, str | None]:
    """Recria o runner in-memory do executor a partir do histórico persistido
    (caps 20 turnos / 4000 chars por mensagem — molde do executor)."""
    conv = get_conversation(cid)
    if not conv:
        return None, "Conversa não encontrada."
    rows = [m for m in list_chat_messages(cid)
            if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()]
    history = [{"role": m["role"], "content": (m["content"] or "")[:RESUME_MAX_CHARS]}
               for m in rows[-RESUME_MAX_TURNS:]]
    target = {"suggestion_id": conv.get("suggestion_id")}
    # O executor RECRIA o runner ⇒ o stream que ainda está aberto aponta para o
    # runner ANTIGO e nunca mais entrega nada. ``ensure_consumer`` é idempotente
    # (não faz nada com uma task viva), então sem este ``stop`` o consumidor
    # ficaria pendurado no stream morto: a IA responde, o write-through persiste,
    # e o painel fica em "IA pensando…" para sempre. Retomar SEMPRE reabre o stream.
    #
    # Derrubar ANTES da chamada (e não depois) fecha a janela em que o consumidor
    # velho processa um frame de erro atrasado e grava ``AUTH_EXPIRED`` DEPOIS do
    # ``ACTIVE`` abaixo — justo quando o operador acabou de renovar a sessão.
    stop_consumer(cid)
    try:
        await ai_client.resume(cid, user_id=user_id, target=target,
                               history=history, model=conv.get("model") or "")
    except Exception as e:  # noqa: BLE001
        # Não retomou: devolve o consumidor que acabamos de tirar do ar, senão a
        # conversa fica ACTIVE sem ninguém escutando.
        ensure_consumer(cid, conv.get("suggestion_id"), user_id=user_id)
        return None, f"Falha ao retomar a conversa no executor: {e}"
    conv = await asyncio.to_thread(set_conversation_status, cid, "ACTIVE")
    # O executor aceitou a retomada ⇒ a credencial nova está valendo (plano 60 · 2.5).
    await asyncio.to_thread(clear_session_state)
    ensure_consumer(cid, conv.get("suggestion_id"), user_id=user_id)
    return conv, None


# ── Imagens (plano 51 · 02/03 F6) ────────────────────────────────────────────

IMAGE_MAX_BYTES = 5 * 1024 * 1024
_IMAGE_MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".webp": "image/webp"}


def resolve_image_parts(parts: list) -> list:
    """Normaliza as ``parts`` da mensagem do humano antes de ir ao executor.

    - ``{type:'text', text}`` passa direto.
    - ``{type:'image', source:{type:'base64',...}}`` (upload manual) passa com
      cap de tamanho.
    - ``{type:'image', media_path}`` (mensagem-imagem SELECIONADA — o arquivo
      já está em disco): o gateway LÊ o arquivo e converte para base64 — o
      executor nunca busca arquivo. O path é confinado a ``statics/`` (anti
      path-traversal).
    Partes inválidas são descartadas (nunca derrubam o envio).
    """
    import base64
    import os

    out: list = []
    root = os.path.realpath("statics")
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        kind = p.get("type")
        if kind == "text":
            text = str(p.get("text") or "")
            if text.strip():
                out.append({"type": "text", "text": text})
            continue
        if kind != "image":
            continue
        source = p.get("source")
        if isinstance(source, dict) and source.get("type") == "base64":
            data = str(source.get("data") or "")
            if 0 < len(data) <= IMAGE_MAX_BYTES * 4 // 3:
                out.append({"type": "image", "source": {
                    "type": "base64",
                    "media_type": str(source.get("media_type") or "image/jpeg"),
                    "data": data}})
            continue
        media_path = str(p.get("media_path") or "").lstrip("/")
        if not media_path:
            continue
        full = os.path.realpath(media_path)
        if not full.startswith(root + os.sep):
            logger.warning("melhorias: media_path fora de statics/ recusado: %s",
                           media_path)
            continue
        ext = os.path.splitext(full)[1].lower()
        mime = _IMAGE_MIMES.get(ext)
        if not mime:
            continue
        try:
            if os.path.getsize(full) > IMAGE_MAX_BYTES:
                continue
            with open(full, "rb") as fh:
                data = base64.b64encode(fh.read()).decode("ascii")
            out.append({"type": "image", "source": {
                "type": "base64", "media_type": mime, "data": data}})
        except OSError as e:
            logger.debug("melhorias: leitura de %s falhou: %s", full, e)
    return out


# ── Consumidor SSE → WS ──────────────────────────────────────────────────────

def parse_sse_frame(frame: str) -> tuple[str, dict] | None:
    """Um frame SSE (separado por linha em branco) → ``(event, data)``.
    Linhas ``:`` (heartbeat) são ignoradas; ``data`` inválido vira ``{}``."""
    event = "message"
    data_lines: list[str] = []
    for line in frame.split("\n"):
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if not data_lines and event == "message":
        return None
    try:
        data = json.loads("\n".join(data_lines)) if data_lines else {}
    except (ValueError, TypeError):
        data = {}
    return event, data if isinstance(data, dict) else {}


# Espera mínima entre reconexões — sem ela, um stream que entrega e fecha na
# hora viraria laço quente (o backoff de ``attempts`` zerado dá 0s).
RECONNECT_FLOOR = 1.0
# Um stream que entregou algo E viveu isso conta como SAUDÁVEL, não como
# tentativa gasta. Com o executor falhando em ~8s, é o que separa "o executor
# respondeu e encerrou" de "a conexão nem subiu".
STREAM_HEALTHY_SEC = 5.0
MAX_ATTEMPTS = 5

# Limite de uso e sobrecarga são da CONTA do executor, não de uma conversa: com
# cinco conversas abertas, cinco laços martelariam em paralelo. Este relógio é
# compartilhado — quem apanhar segura todo mundo.
_executor_cooldown_until: float = 0.0


def _note_cooldown(seconds) -> None:
    global _executor_cooldown_until
    try:
        s = float(seconds or 0)
    except (TypeError, ValueError):
        return
    if s > 0:
        _executor_cooldown_until = max(_executor_cooldown_until, now() + s)


def _cooldown_remaining() -> float:
    return max(0.0, _executor_cooldown_until - now())


async def _handle_error_frame(cid: str, suggestion_id: int, data: dict) -> float:
    """Trata um frame ``event: error``. Devolve os segundos de espera sugeridos.

    Só emite o evento TIPADO quando reconhece o kind — emitir também o ``error``
    cru renderizaria dois cartões para uma falha só. Sem kind reconhecido, o
    comportamento é exatamente o de antes: repassa o ``error`` e não persiste nada.
    """
    kind = derive_kind(data)
    if kind is None:
        _ws_emit(suggestion_id, cid, "error", data)
        return 0.0
    retry_after = clamp_retry_after((data or {}).get("retry_after"))
    await asyncio.to_thread(
        record_executor_failure, cid, str((data or {}).get("message") or ""),
        kind=kind, retry_after=retry_after)
    if EXECUTOR_KINDS.get(kind, {}).get("action") != "wait":
        return 0.0
    wait = float(retry_after) if retry_after else OVERLOADED_FLOOR
    _note_cooldown(wait)
    return wait


async def _consume_stream(cid: str, suggestion_id: int, user_id=None) -> None:
    """Consome a SSE do executor e re-emite no /ws. Reconecta com backoff
    enquanto a conversa estiver ACTIVE; termina em COMPLETED/CANCELLED/ERRORED."""
    attempts = 0
    while True:
        conv = await asyncio.to_thread(get_conversation, cid)
        if not conv or conv.get("status") != "ACTIVE":
            break
        cooling = _cooldown_remaining()
        if cooling > 0:
            await asyncio.sleep(cooling)
            continue
        hint = 0.0
        opened_at = now()
        delivered = False
        try:
            buffer = ""
            async for chunk in ai_client.open_stream(cid, user_id=user_id):
                delivered = True
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    if not frame.strip():
                        continue
                    parsed = parse_sse_frame(frame)
                    if not parsed:
                        continue
                    if parsed[0] == "error":
                        hint = max(hint, await _handle_error_frame(
                            cid, suggestion_id, parsed[1]))
                    else:
                        _ws_emit(suggestion_id, cid, parsed[0], parsed[1])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("melhorias: stream da conversa %s caiu: %s", cid, e)
        # O contador só zera quando o stream de fato SERVIU. Antes ele zerava
        # dentro do ``async for``, então um fechamento LIMPO caía direto no
        # ``attempts += 1``: cinco fechamentos limpos matavam o consumidor em
        # silêncio e a conversa ficava zumbi (ACTIVE, ninguém escutando, painel
        # preso em "IA pensando…"). Só não aparecia porque um 401 mudava o status.
        if delivered and (now() - opened_at) >= STREAM_HEALTHY_SEC:
            attempts = 0
        else:
            attempts += 1
        if attempts > MAX_ATTEMPTS:
            logger.warning("melhorias: stream %s desistiu após %s tentativas",
                           cid, attempts)
            # Desistir em silêncio deixa o chat girando para sempre: o fallback
            # de quiescência do frontend não arma sem uma resposta assentada.
            _ws_emit(suggestion_id, cid, "executor_failure", {
                "kind": "unknown", "retry_after": None, "status": "ACTIVE",
                "message": "Sem conexão com o executor — a análise foi interrompida."})
            break
        await asyncio.sleep(max(RECONNECT_FLOOR, hint, min(2.0 * attempts, 10.0)))
    _consumers.pop(cid, None)


def ensure_consumer(cid: str, suggestion_id: int, *, user_id=None) -> None:
    """Liga o consumidor SSE→WS da conversa (idempotente)."""
    task = _consumers.get(cid)
    if task and not task.done():
        return
    _consumers[cid] = asyncio.create_task(
        _consume_stream(cid, suggestion_id, user_id=user_id),
        name=f"melhorias:sse:{cid}")


def stop_consumer(cid: str) -> None:
    task = _consumers.pop(cid, None)
    if task and not task.done():
        task.cancel()


# ── Callbacks do executor (chamados pelas rotas _internal) ───────────────────

# Anti-rajada: o executor retenta internamente e pode cuspir o mesmo 429 várias
# vezes seguidas. Sem isso, uma rajada vira cinco linhas no banco e cinco
# cartões vermelhos no chat para uma falha só.
FAILURE_THROTTLE_SEC = 10.0
_failure_seen: dict[tuple[str, str], float] = {}


def _throttled(cid: str, kind: str) -> bool:
    key = (cid, kind)
    last = _failure_seen.get(key, 0.0)
    ts = now()
    if ts - last < FAILURE_THROTTLE_SEC:
        return True
    _failure_seen[key] = ts
    return False


def record_executor_failure(cid: str, content: str, *, kind: str,
                            retry_after=None) -> dict:
    """Falha do executor registrada como FALHA, nunca como resposta da IA.

    UM helper, DOIS pontos de entrada: o write-through (``_internal/messages``) e
    o frame ``event: error`` do stream. O que varia por ``kind``:

    - sempre persiste com ``role="system"`` + ``failure_kind`` ⇒ sai do
      ``_last_assistant_content``, nunca vira a "Análise gerada pela IA", e a
      hidratação do chat lê o TIPO em vez de adivinhar pelo texto;
    - ``auth_required`` (o único fatal) marca a conversa ``AUTH_EXPIRED``, grava
      o estado GLOBAL e emite ``auth_expired``;
    - os demais NÃO tocam o status da conversa nem o estado global — só emitem o
      aviso efêmero + ``executor_failure``. Limite/sobrecarga passam sozinhos, e
      cota não é consertável com o botão "Renovar sessão".
    """
    kind = normalize_kind(kind) or "unknown"
    fatal = kind_is_fatal(kind)
    retry_after = clamp_retry_after(retry_after)

    mid = None
    if not _throttled(cid, kind):
        try:
            mid = append_chat_message(cid, "system", content=content,
                                      failure_kind=kind)
        except Exception:  # noqa: BLE001 — o estado da sessão importa mais
            logger.warning("melhorias: falha ao persistir %s da conversa %s",
                           kind, cid)

    if not fatal:
        conv = get_conversation(cid)
        state = notify_executor_issue(kind, message=content,
                                      retry_after=retry_after,
                                      conversation_id=cid)
        _ws_emit((conv or {}).get("suggestion_id"), cid, "executor_failure",
                 {"kind": kind, "message": content, "retry_after": retry_after,
                  "status": (conv or {}).get("status") or "ACTIVE"})
        return {"id": mid, "conversation": conv, "session": state, "kind": kind,
                "fatal": False}

    # Guardado: uma falha atrasada não rebaixa conversa já finalizada.
    conv = set_conversation_status(cid, "AUTH_EXPIRED", only_if="ACTIVE")
    state = mark_session_expired(conversation_id=cid, message=content)
    # O chat aberto reage na hora (sem esperar reabrir o modal).
    _ws_emit((conv or {}).get("suggestion_id"), cid, "auth_expired",
             {"kind": kind, "message": content, "status": "AUTH_EXPIRED"})
    # NÃO paramos o consumidor aqui: este helper roda DENTRO do próprio
    # ``_consume_stream`` no caminho do frame de erro (cancelar seria suicídio).
    # O laço já encerra sozinho na próxima volta — a conversa não está mais ACTIVE.
    return {"id": mid, "conversation": conv, "session": state, "kind": kind,
            "fatal": True}


def record_auth_failure(cid: str, content: str) -> dict:
    """Shim do nome antigo. O plugin vive em 4 cópias que derivam entre si — três
    linhas evitam um chamador fora desta árvore explodir."""
    return record_executor_failure(cid, content, kind="auth_required")


def _last_assistant_content(cid: str) -> str:
    """Artefato final da conversa = última mensagem assistant com texto.

    Blindagem retroativa (plano 60 · 2.7): linhas de 401 já gravadas como
    ``assistant`` na base de produção NUNCA podem virar a análise final.
    """
    for m in reversed(list_chat_messages(cid)):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            content = m["content"].strip()
            if is_auth_error(content):
                continue
            return content
    return ""


def on_conversation_status(cid: str, status: str) -> dict | None:
    """Write-through de status + fechamento da sugestão no COMPLETED (02 F6)."""
    from . import logic

    conv = set_conversation_status(cid, status)
    if not conv:
        return None
    sid = conv.get("suggestion_id")
    if status == "COMPLETED":
        logic.finalize_agentic_suggestion(
            sid, analysis=_last_assistant_content(cid), model=conv.get("model") or "")
    if status in ("COMPLETED", "CANCELLED", "ERRORED"):
        stop_consumer(cid)
    try:
        broadcast("plugin_melhorias_changed",
                  {"id": sid, "action": f"conversation_{status.lower()}"})
    except Exception:  # noqa: BLE001
        pass
    return conv


def conclude_conversation(cid: str) -> dict | None:
    """Encerramento MANUAL pelo operador (plano 58): fecha a conversa (COMPLETED)
    e finaliza a sugestão como ``concluida`` (artefato final = última resposta da
    IA). Espelha ``on_conversation_status`` mas usa o terminal manual; NÃO chama o
    executor (o caller já dispara ``ai_client.cancel``) nem para o consumidor
    (o caller faz ``stop_consumer``, como no cancel)."""
    from . import logic

    conv = set_conversation_status(cid, "COMPLETED")
    if not conv:
        return None
    sid = conv.get("suggestion_id")
    logic.conclude_agentic_suggestion(
        sid, analysis=_last_assistant_content(cid), model=conv.get("model") or "")
    try:
        broadcast("plugin_melhorias_changed",
                  {"id": sid, "action": "conversation_completed"})
    except Exception:  # noqa: BLE001
        pass
    return conv
