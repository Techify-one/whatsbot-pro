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


# ── Sessão do Claude expirada (plano 60 · camada 2) ──────────────────────────
#
# O executor entrega o 401 do SDK como se fosse RESPOSTA da IA (``role:
# assistant``, HTTP 200) — a classificação abaixo é o único ponto de decisão do
# servidor, espelho fiel do ``isAuthError`` de ``static/chat_core.js``.

AUTH_ERROR_MARKERS = (
    "authentication_error",
    "invalid authentication credentials",
    "please run /login",
    "invalid api key",
)
_AUTH_401_RE = re.compile(r"\b401\b")

# Estado GLOBAL da sessão (não por-conversa): a credencial é do executor inteiro.
# Sem migration — mesmo padrão key-value do ``logic._setting``.
SESSION_STATE_KEY = "plugin.melhorias.ai_session_state"
SESSION_EVENT = "plugin_melhorias_ai_session"


def is_auth_error(text_value) -> bool:
    """A mensagem é o 401 do Claude vestido de conteúdo? (espelho do frontend)"""
    t = str(text_value or "").lower()
    if _AUTH_401_RE.search(t):
        return True
    return any(marker in t for marker in AUTH_ERROR_MARKERS)


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


def _publish_session_state(state: dict, *, persist: str | None) -> dict:
    """Persiste (``persist`` = string a gravar; ``None`` limpa) e avisa TODOS os
    painéis abertos — quem chegar depois lê o mesmo estado no ``GET /config``."""
    try:
        config_repo.set(SESSION_STATE_KEY, persist or "")
    except Exception:  # noqa: BLE001 — o aviso ao painel vale mesmo sem persistir
        logger.warning("melhorias: falha ao gravar o estado da sessão de IA")
    try:
        broadcast(SESSION_EVENT, state)
    except Exception:  # noqa: BLE001
        logger.debug("melhorias: broadcast de %s falhou", SESSION_EVENT)
    return state


def mark_session_expired(*, conversation_id: str | None = None,
                         message: str = "") -> dict:
    state = {"status": "expired", "at": now(),
             "conversation_id": conversation_id,
             "message": (message or "")[:500]}
    return _publish_session_state(
        state, persist=json.dumps(state, ensure_ascii=False))


def clear_session_state() -> dict:
    """Sessão renovada/retomada: apaga o estado e avisa os painéis."""
    return _publish_session_state({"status": "ok", "at": now()}, persist=None)


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


def set_conversation_status(cid: str, status: str) -> dict | None:
    if status not in CONV_STATUSES:
        return None
    ts = now()
    done = status in ("COMPLETED", "CANCELLED", "ERRORED")
    with make_plugin_db() as conn:
        conn.execute(text(
            f"UPDATE {_CONV} SET status = :st, updated_at = :ts, "
            "completed_at = CASE WHEN :done THEN :ts ELSE completed_at END "
            "WHERE id = :id"),
            {"st": status, "ts": ts, "done": done, "id": cid})
    return get_conversation(cid)


# ── Mensagens do chat (append-only) ──────────────────────────────────────────

def append_chat_message(cid: str, role: str, *, content: str | None = None,
                        tool_name: str | None = None, tool_input=None,
                        tool_result=None, token_usage=None) -> int:
    with make_plugin_db() as conn:
        mid = conn.execute(text(
            f"INSERT INTO {_MSGS} (conversation_id, role, content, tool_name, "
            "tool_input, tool_result, token_usage, created_at) VALUES "
            "(:cid, :role, :content, :tool_name, :tool_input, :tool_result, "
            ":token_usage, :ts) RETURNING id"), {
                "cid": cid, "role": role, "content": content,
                "tool_name": tool_name, "tool_input": _dumps(tool_input),
                "tool_result": _dumps(tool_result),
                "token_usage": _dumps(token_usage), "ts": now()}).scalar_one()
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
    try:
        await ai_client.resume(cid, user_id=user_id, target=target,
                               history=history, model=conv.get("model") or "")
    except Exception as e:  # noqa: BLE001
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


async def _consume_stream(cid: str, suggestion_id: int, user_id=None) -> None:
    """Consome a SSE do executor e re-emite no /ws. Reconecta com backoff
    enquanto a conversa estiver ACTIVE; termina em COMPLETED/CANCELLED/ERRORED."""
    attempts = 0
    while True:
        conv = await asyncio.to_thread(get_conversation, cid)
        if not conv or conv.get("status") != "ACTIVE":
            break
        try:
            buffer = ""
            async for chunk in ai_client.open_stream(cid, user_id=user_id):
                attempts = 0
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    if not frame.strip():
                        continue
                    parsed = parse_sse_frame(frame)
                    if parsed:
                        # Mesma classificação do write-through (plano 60 · 2.4):
                        # um frame `event: error` de auth marca a sessão morta.
                        if parsed[0] == "error" and is_auth_error(
                                (parsed[1] or {}).get("message")):
                            await asyncio.to_thread(
                                record_auth_failure, cid,
                                str((parsed[1] or {}).get("message") or ""))
                        _ws_emit(suggestion_id, cid, parsed[0], parsed[1])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("melhorias: stream da conversa %s caiu: %s", cid, e)
        attempts += 1
        if attempts > 5:
            logger.warning("melhorias: stream %s desistiu após %s tentativas",
                           cid, attempts)
            break
        await asyncio.sleep(min(2.0 * attempts, 10.0))
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

def record_auth_failure(cid: str, content: str) -> dict:
    """Um 401 do Claude chegou vestido de conteúdo — registra como FALHA, não
    como resposta da IA (plano 60 · 2.3/2.4). UM helper, DOIS pontos de entrada:
    o write-through do executor e o frame ``event: error`` do stream.

    - persiste com ``role="system"`` ⇒ sai do ``_last_assistant_content`` e
      nunca vira a "Análise gerada pela IA";
    - marca a conversa como ``AUTH_EXPIRED`` (recuperável por ``resume``);
    - grava o estado GLOBAL da sessão e avisa todos os painéis abertos.
    """
    mid = None
    try:
        mid = append_chat_message(cid, "system", content=content)
    except Exception:  # noqa: BLE001 — o estado da sessão importa mais
        logger.warning("melhorias: falha ao persistir o 401 da conversa %s", cid)
    conv = set_conversation_status(cid, "AUTH_EXPIRED")
    state = mark_session_expired(conversation_id=cid, message=content)
    # O chat aberto reage na hora (sem esperar reabrir o modal).
    _ws_emit((conv or {}).get("suggestion_id"), cid, "auth_expired",
             {"message": content, "status": "AUTH_EXPIRED"})
    # NÃO paramos o consumidor aqui: este helper roda DENTRO do próprio
    # ``_consume_stream`` no caminho do frame de erro (cancelar seria suicídio).
    # O laço já encerra sozinho na próxima volta — a conversa não está mais ACTIVE.
    return {"id": mid, "conversation": conv, "session": state}


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
