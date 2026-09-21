#!/usr/bin/env python3
"""Executor Claude Code FALSO — testa o plugin ``melhorias`` sem o Claude.

Fala o MESMO protocolo do ``whatsbot-ai-server`` real (plano 51): recebe as
chamadas HMAC-assinadas do gateway (``ai_client.py``), streama eventos SSE e
escreve de volta nas rotas ``/public/_internal/*`` com a mesma assinatura. Só
stdlib — nenhuma dependência, roda com o python do sistema ou o do venv.

    ./venv/bin/python scripts/fake_ai_executor.py --secret "<32+ chars>"

O que dá para exercitar (comandos digitados no chat do painel):

    /ajuda        lista os comandos
    /auth         frame `auth_required` (reproduz a sessão expirada)
    /morrer       daqui em diante TODO turno responde 401 (até o relogin)
    /viver        volta ao normal sem relogin
    /limite N     frame `rate_limited` com retry_after=N (default 30s)
    /cota         frame `quota_exceeded` (sem crédito — relogin não resolve)
    /sobrecarga   frame `overloaded`
    /limite401    `rate_limited` cujo TEXTO cita um 401 — o falso positivo que a
                  heurística antiga tratava como sessão expirada
    /prosa401     análise LEGÍTIMA que cita um 401 em prosa, persistida pelo
                  write-through — antes matava a sessão global, hoje é bolha comum
    /legado       401 como resposta da IA, sem kind (executor antigo)
    /tool         cartão de ferramenta (running → done)
    /pergunta     a IA PERGUNTA (1 pergunta, 3 opções) e ESPERA a resposta
    /perguntas    duas perguntas, uma delas com multi-seleção
    /pergunta_dup duas perguntas com o MESMO texto (o gateway deve recusar)
    /parar        resposta longa e lenta, para exercitar o botão Parar
    /fila         demora 25s — digite enquanto isso para testar a fila
    /esforco      imprime o nível de esforço que o gateway mandou
    /aprovacao    registra uma aprovação ✓/✕ e espera a decisão
    /mutacao      lê os agentes pelo bridge _internal e propõe mudar um prompt
                  (só grava de verdade com --allow-mutations)
    /erro         evento `error` sem kind (cai na heurística, como antes)
    /lento N      demora N segundos antes de responder ("IA pensando…")
    /fim          fecha a conversa (conversation-status COMPLETED)

O relogin do painel funciona: ``/admin/relogin/start`` devolve um sessionId +
URL falsa e QUALQUER código não-vazio é aceito (o código ``recusar`` é rejeitado
de propósito). Ao completar, a sessão volta a ficar autenticada — e os runners
são reconstruídos, que é justamente o que o executor real NÃO faz hoje.

Controle em runtime, sem reiniciar (não exige HMAC, só localhost):

    curl -X POST 127.0.0.1:8099/_fake/mode -d '{"mode":"dead"}'
    curl 127.0.0.1:8099/_fake/state

Tipagem do write-through (plano 62 · B): por padrão todo ``POST /messages``
carrega a chave ``kind`` — com ``null`` quando não é falha —, que é como o
executor TIPADO declara "confie na etiqueta, não leia o texto". A flag
``--legacy-untyped`` OMITE a chave e simula o executor antigo, sem precisar de um
executor antigo de verdade. (``/legado`` omite a chave sempre: ele É o antigo.)
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import queue
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("fake-executor")

INTERNAL = "/api/plugins/melhorias/public/_internal"

# Textos reais do SDK quando o OAuth morre (os dois da tela do operador).
AUTH_ERRORS = [
    'API Error: 401 {"type":"error","error":{"type":"authentication_error",'
    '"message":"OAuth access token has expired. Re-authenticate to continue."},'
    '"request_id":null} · Please run /login',
    'API Error: 401 {"type":"error","error":{"type":"authentication_error",'
    '"message":"OAuth access token has been revoked."},"request_id":null}'
    ' · Please run /login',
]

HELP = """**Executor falso** — comandos disponíveis:

- `/auth` — responde com o erro 401 do Claude (uma vez)
- `/morrer` / `/viver` — liga/desliga a sessão expirada para TODOS os turnos
- `/limite 30` — frame `rate_limited` com `retry_after` (default 30s)
- `/cota` — frame `quota_exceeded` (sem crédito)
- `/sobrecarga` — frame `overloaded`
- `/limite401` — `rate_limited` cujo TEXTO cita um 401 (teste do falso positivo)
- `/prosa401` — análise LEGÍTIMA citando um 401 em prosa (NÃO é falha)
- `/legado` — 401 como resposta da IA, sem `kind` (executor antigo)
- `/tool` — cartão de ferramenta
- `/pergunta` — a IA PERGUNTA e espera você responder (destrava ao responder)
- `/perguntas` — duas perguntas, uma com multi-seleção
- `/pergunta_dup` — bloco inválido (dois enunciados iguais) — deve ser recusado
- `/parar` — resposta longa e lenta, para testar o botão Parar
- `/fila` — demora 25s: digite nesse meio-tempo para ver a fila
- `/esforco` — mostra o nível de esforço que o gateway mandou
- `/aprovacao` — cartão de aprovação ✓/✕
- `/mutacao` — lê os agentes pelo bridge e propõe mudar um prompt
- `/erro` — evento de erro sem `kind` (executor antigo — cai na heurística)
- `/lento 8` — demora 8s para responder
- `/fim` — encerra a conversa (COMPLETED)

Qualquer outra mensagem recebe uma resposta comum."""

ANALYSIS = """**Diagnóstico**

O agente respondeu fora do horário comercial sem checar o expediente: ele tratou
a mensagem como um atendimento normal e prometeu que "o time entra em contato",
sem dizer QUANDO. O cliente ficou sem previsão.

**Recomendações**

- Instrua o agente a informar o horário de atendimento sempre que a mensagem
  chegar fora dele, com a próxima janela de resposta ("respondemos amanhã a
  partir das 9h").
- Evite prometer contato do time sem prazo — vira expectativa não cumprida.

_(resposta gerada pelo EXECUTOR FALSO — nenhum Claude foi consultado)_

Digite `/ajuda` para ver os comandos de teste."""


# ── Estado ───────────────────────────────────────────────────────────────────

class Conversation:
    """Um runner in-memory (o mesmo conceito do executor real)."""

    def __init__(self, cid: str, callback_url: str, user_id: str, model: str):
        self.id = cid
        self.callback_url = (callback_url or "").rstrip("/")
        self.user_id = str(user_id or "")
        self.model = model or ""
        self.events: queue.Queue = queue.Queue()
        self.pending: dict[str, dict] = {}   # approval_id → contexto
        self.turns = 0
        self.auth_error_idx = 0
        # plano 150: parar o turno, esforço vigente e perguntas bloqueando.
        self.stop = threading.Event()
        self.effort = ""
        self.questions: dict[str, dict] = {}   # question_id → {event, answer}


# TTL fictício do token OAuth. O executor real não renova sozinho (decisão do
# operador), então a expiração é evento RECORRENTE — é o que o monitor
# preventivo (fase 2) sonda pelo /health.
TOKEN_TTL_SEC = 8 * 3600


# O que este executor falso sabe fazer (plano 150). Anunciado na abertura, na
# retomada e no /health — nunca sondável, porque campo desconhecido é
# descartado em silêncio pelo executor real.
CAPABILITIES = {"ask_user": True, "effort": True, "interrupt": True,
                "activity": False}


def caps_payload() -> dict:
    """Fragmento a mesclar nas respostas. Vazio com ``--legacy-caps``."""
    return {} if STATE.legacy_caps else {"capabilities": dict(CAPABILITIES)}


def iso_utc(ts: float) -> str:
    """Epoch → ISO 8601 UTC com milissegundos e `Z`, como o executor real."""
    return (datetime.fromtimestamp(ts, timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.convs: dict[str, Conversation] = {}
        self.authenticated = True
        self.expires_at = time.time() + TOKEN_TTL_SEC
        self.relogins: dict[str, float] = {}
        self.secret = ""
        self.allow_mutations = False
        self.verify = True
        # Plano 62 · B: ligada, OMITE a chave ``kind`` do write-through e finge
        # ser o executor antigo (o gateway cai na heurística estrita).
        self.legacy_untyped = False
        # plano 150 · F2: ligada, OMITE a chave ``capabilities`` das respostas
        # e finge ser um executor anterior à negociação — é a única forma
        # honesta de exercitar esse caminho sem ter uma build antiga à mão.
        self.legacy_caps = False

    def renew(self) -> None:
        """Sessão viva de novo: o relógio do TTL recomeça."""
        self.authenticated = True
        self.expires_at = time.time() + TOKEN_TTL_SEC

    def get(self, cid: str) -> Conversation | None:
        with self.lock:
            return self.convs.get(cid)


STATE = State()


# ── HMAC (idêntico a ai_client.sign / hmac_guard) ────────────────────────────

def sign(secret: str, method: str, path: str, ts: str, rid: str, body: str) -> str:
    payload = "\n".join([method.upper(), path, ts, rid, body])
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def new_request_id() -> str:
    return f"{int(time.time() * 1000):x}-{secrets.token_hex(4)}"


# ── Callbacks para o gateway (write-through + tools) ─────────────────────────

def call_gateway(conv: Conversation, path: str, payload=None, *,
                 method: str = "POST", query: str = "") -> dict | None:
    """Chama ``/public/_internal/<path>`` assinado. ``query`` NÃO entra na
    assinatura (o guard usa ``request.url.path``)."""
    if not conv.callback_url:
        logger.warning("conversa %s sem callbackUrl — callback ignorado", conv.id)
        return None
    full_path = INTERNAL + path
    body = "" if payload is None else json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"))
    ts = str(int(time.time()))
    rid = new_request_id()
    headers = {
        "X-WB-Timestamp": ts,
        "X-WB-Signature": sign(STATE.secret, method, full_path, ts, rid, body),
        "X-WB-Request-Id": rid,
        "X-WB-On-Behalf-Of": conv.user_id,
    }
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        conv.callback_url + full_path + query,
        data=body.encode("utf-8") if body else None,
        headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        logger.warning("callback %s %s → HTTP %s %s", method, full_path, e.code, detail)
        return {"ok": False, "error": f"HTTP {e.code}: {detail}"}
    except Exception as e:  # noqa: BLE001
        logger.warning("callback %s %s falhou: %s", method, full_path, e)
        return {"ok": False, "error": str(e)}


# ── Emissão de eventos (SSE) ─────────────────────────────────────────────────

def emit(conv: Conversation, event: str, data: dict) -> None:
    conv.events.put((event, data))


def persist_message(conv: Conversation, payload: dict, *,
                    typed: bool | None = None) -> dict | None:
    """Write-through de mensagem, com a declaração de tipagem do plano 62 · B.

    O executor TIPADO manda a chave ``kind`` SEMPRE — ``null`` quando não é falha
    — e é a PRESENÇA dela que diz ao gateway "confie na etiqueta, não leia o
    texto". ``typed=False`` omite a chave (executor antigo); ``typed=None`` segue
    a flag global ``--legacy-untyped``.
    """
    if typed is None:
        typed = not STATE.legacy_untyped
    body = dict(payload)
    if typed:
        body.setdefault("kind", None)
    else:
        body.pop("kind", None)
    return call_gateway(conv, "/messages", body)


def say(conv: Conversation, text: str, *, persist: bool = True,
        chunk_delay: float = 0.03, typed: bool | None = None) -> None:
    """Streama uma mensagem do assistant e a persiste via write-through — o
    mesmo par (SSE + POST /messages) que o executor real faz."""
    mid = "m-" + secrets.token_hex(5)
    emit(conv, "message_start", {"messageId": mid})
    words = text.split(" ")
    sent, cut = [], False
    for i in range(0, len(words), 6):
        # O Parar precisa cortar NO MEIO do stream — um fake que só checa no
        # início não prova nada sobre a interrupção.
        if conv.stop.is_set():
            cut = True
            break
        piece = " ".join(words[i:i + 6])
        sent.append(piece)
        emit(conv, "message_chunk", {"messageId": mid, "delta": piece + " "})
        time.sleep(chunk_delay)
    partial = " ".join(sent) if cut else text
    end = {"messageId": mid, "content": partial}
    if cut:
        # A marca é o que impede a meia resposta de virar a "análise final".
        end["interrupted"] = True
    emit(conv, "message_end", end)
    if persist:
        body = {"conversation_id": conv.id, "role": "assistant",
                "content": partial}
        if cut:
            body["interrupted"] = True
        persist_message(conv, body, typed=typed)


# ── Turnos ───────────────────────────────────────────────────────────────────

def auth_error_text(conv: Conversation) -> str:
    text = AUTH_ERRORS[conv.auth_error_idx % len(AUTH_ERRORS)]
    conv.auth_error_idx += 1
    return text


def fail(conv: Conversation, kind: str, message: str, retry_after=None) -> None:
    """Frame de erro TIPADO — o caminho novo do executor real (plano 61).

    O `kind` é derivado de `SDKAssistantMessage.error`, NÃO do `subtype`: um 401
    chega do SDK como `subtype: "success"` com `is_error: true`, então `subtype`
    diria "success" para uma falha. Por isso ele nunca entra no payload.
    """
    emit(conv, "error", {"kind": kind, "message": message,
                         "retry_after": retry_after})


def run_turn(conv: Conversation, text: str) -> None:
    """Produz a resposta de um turno (roda numa thread própria)."""
    try:
        _run_turn(conv, text)
    except Exception:  # noqa: BLE001
        logger.exception("turno da conversa %s explodiu", conv.id)
        emit(conv, "error", {"message": "Executor falso: erro interno."})
    finally:
        emit(conv, "done", {})


def _run_turn(conv: Conversation, text: str) -> None:
    body = (text or "").strip()
    lower = body.lower()
    conv.turns += 1
    # Comando = PRIMEIRO TOKEN exato ("/ajudante" não é "/ajuda").
    cmd = lower.split()[0] if lower.startswith("/") else ""

    # Sessão morta: todo turno vira 401 (menos /ajuda, para não trancar o teste).
    if not STATE.authenticated and cmd != "/ajuda":
        logger.info("conversa %s: turno com sessão EXPIRADA → 401", conv.id)
        fail(conv, "auth_required", auth_error_text(conv))
        return

    if cmd == "/ajuda":
        say(conv, HELP, persist=False)
        return

    if cmd == "/auth":
        fail(conv, "auth_required", auth_error_text(conv))
        return

    if cmd == "/limite":
        parts = lower.split()
        secs = 30
        if len(parts) > 1:
            try:
                secs = max(1, min(300, int(parts[1])))
            except ValueError:
                pass
        fail(conv, "rate_limited",
             "Limite de uso da conta atingido. Tente novamente em instantes.",
             retry_after=secs)
        return

    if cmd == "/limite401":
        # O caso que a heurística de texto errava: limite de uso cujo corpo cita
        # um 401 de upstream. Com o kind tipado, NÃO pode virar sessão expirada.
        fail(conv, "rate_limited",
             'Rate limit: upstream respondeu 401 {"type":"authentication_error"} '
             "durante o throttling. Tente de novo.", retry_after=20)
        return

    if cmd == "/cota":
        fail(conv, "quota_exceeded",
             "Sem crédito na conta do executor. Recarregue para continuar.")
        return

    if cmd == "/sobrecarga":
        fail(conv, "overloaded", "Executor sobrecarregado (529). Tentando de novo.")
        return

    if cmd == "/morrer":
        with STATE.lock:
            STATE.authenticated = False
        logger.info("modo DEAD ligado pelo chat")
        # Caminho NOVO: a falha vai pelo frame tipado, não mais como resposta da
        # IA persistida. `/legado` continua exercitando o write-through antigo.
        fail(conv, "auth_required", auth_error_text(conv))
        return

    if cmd == "/prosa401":
        # Reprodução do bug do plano 62: análise LEGÍTIMA que cita um 401 como
        # ASSUNTO. Antes, a regex sobre o conteúdo da IA concluía "sessão
        # expirada", derrubava a sessão GLOBAL e bloqueava conversa nova. Hoje
        # tem de virar uma bolha comum — nos dois modos (tipado e legado, porque
        # o texto não tem marcador literal do SDK).
        say(conv, "**Diagnóstico**\n\nO problema não é do agente: o endpoint do "
                  "cliente devolveu 401 durante a consulta de pedido, então a "
                  "tool falhou e o agente improvisou.\n\n**Recomendações**\n\n"
                  "- Oriente o agente a avisar que a consulta está indisponível "
                  "em vez de inventar prazo.\n- Peça ao time de integração para "
                  "revalidar a credencial que está retornando 401.")
        return

    if cmd == "/legado":
        # Executor ANTIGO: 401 entregue como resposta da IA, em HTTP 200, sem
        # kind nenhum — exercita a rede de segurança do write-through. A chave é
        # omitida SEMPRE (`typed=False`): este comando É o executor antigo, então
        # não segue a flag global.
        logger.info("conversa %s: 401 pelo caminho legado (write-through)", conv.id)
        say(conv, auth_error_text(conv), typed=False)
        return

    if cmd == "/viver":
        with STATE.lock:
            STATE.renew()
        logger.info("modo OK religado pelo chat")
        say(conv, "Sessão restaurada (sem relogin). Pode continuar.", persist=False)
        return

    if cmd == "/erro":
        emit(conv, "error", {"message": "Executor falso: falha simulada no runner."})
        return

    if cmd == "/lento":
        parts = lower.split()
        secs = 8
        if len(parts) > 1:
            try:
                secs = max(1, min(120, int(parts[1])))
            except ValueError:
                pass
        logger.info("conversa %s: dormindo %ss", conv.id, secs)
        time.sleep(secs)
        say(conv, f"Demorei {secs}s de propósito — era o teste do 'IA pensando…'.")
        return

    if cmd == "/fim":
        say(conv, "Encerrando a melhoria por aqui. Este texto vira a análise final.")
        call_gateway(conv, "/conversation-status",
                     {"conversation_id": conv.id, "status": "COMPLETED"})
        return

    if cmd == "/tool":
        tid = "t-" + secrets.token_hex(4)
        emit(conv, "tool_call_start", {"toolCallId": tid, "name": "listar_agentes",
                                       "input": {"scope": "todos"}})
        res = call_gateway(conv, "/agents", method="GET")
        ok = bool(res and res.get("ok"))
        agents = (res or {}).get("data") or []
        emit(conv, "tool_call_end", {
            "toolCallId": tid,
            "output": f"{len(agents)} agente(s)" if ok else None,
            "error": None if ok else (res or {}).get("error", "falhou")})
        persist_message(conv, {
            "conversation_id": conv.id, "role": "tool", "tool_name": "listar_agentes",
            "tool_input": {"scope": "todos"},
            "tool_result": f"{len(agents)} agente(s)" if ok else "erro"})
        say(conv, f"Li a configuração pelo bridge: **{len(agents)} agente(s)**."
                  if ok else
                  f"O bridge recusou a leitura: {(res or {}).get('error')}")
        return

    if cmd == "/pergunta":
        ans = ask(conv, [{
            "question": "Qual regra aplicar fora do horário comercial?",
            "header": "Horário", "multiSelect": False,
            "options": [
                {"label": "Informar a janela",
                 "description": "Dizer o horário e quando a equipe responde"},
                {"label": "Só pedir desculpa",
                 "description": "Sem prometer prazo nenhum"},
                {"label": "Encaminhar ao humano",
                 "description": "Abrir atendimento mesmo fora do horário"}]}])
        say(conv, f"Entendi: **{ans}**. Sigo por esse caminho."
                  if ans else "Ninguém respondeu — sigo pela melhor hipótese.")
        return

    if cmd == "/perguntas":
        ans = ask(conv, [
            {"question": "Qual regra aplicar fora do horário comercial?",
             "header": "Horário", "multiSelect": False,
             "options": [{"label": "Informar a janela", "description": "com prazo"},
                         {"label": "Só pedir desculpa", "description": "sem prazo"}]},
            {"question": "Onde a mudança vale?",
             "header": "Escopo", "multiSelect": True,
             "options": [{"label": "Só este agente", "description": "escopo mínimo"},
                         {"label": "Todos os agentes", "description": "vale para a base toda"},
                         {"label": "Só neste horário", "description": "condicional"}]}])
        say(conv, f"Anotado: {ans}")
        return

    if cmd == "/pergunta_dup":
        # Duas perguntas com o MESMO enunciado — o write-through DEVE recusar
        # com 400 (o `answers` do SDK é indexado pelo texto e colapsaria).
        q = {"question": "Qual regra?", "header": "R", "multiSelect": False,
             "options": [{"label": "A"}, {"label": "B"}]}
        out = call_gateway(conv, "/questions", {
            "conversation_id": conv.id, "question_id": "q-dup-" + secrets.token_hex(3),
            "questions": [q, dict(q)]})
        say(conv, f"O gateway respondeu ao bloco duplicado: `{out}` "
                  "(esperado: recusa).")
        return

    if cmd == "/parar":
        say(conv, ("Vou escrever uma análise bem longa de propósito para dar "
                   "tempo de você clicar em Parar. ") + ("palavra " * 400),
            chunk_delay=0.25)
        return

    if cmd == "/fila":
        time.sleep(25)
        say(conv, "Voltei. As mensagens que você enfileirou chegam agora, "
                  "uma por vez e na ordem.")
        return

    if cmd == "/esforco":
        say(conv, f"O gateway me mandou o esforço: **{conv.effort or '(nenhum)'}**.")
        return

    if cmd == "/aprovacao":
        propose_approval(conv, kind="demo", tool_name="atualizar_prompt_do_agente",
                         tool_input={"agent_key": "(exemplo)",
                                     "trecho": "Informe o horário de atendimento…"},
                         summary="Acrescentar a regra de horário ao prompt do agente.")
        return

    if cmd == "/mutacao":
        run_mutation(conv)
        return

    # Turno normal. O 1º é a mensagem de contexto montada pelo gateway.
    if conv.turns == 1 or body.startswith("## Estilo de comunicação"):
        say(conv, ANALYSIS)
        return
    say(conv, f"Recebi: “{body[:180]}”. Anotado — posso propor um ajuste no prompt "
              f"se você quiser (`/aprovacao` ou `/mutacao`).")


def ask(conv: Conversation, questions: list, *, timeout: float = 300.0) -> dict | None:
    """Faz uma pergunta ao operador e BLOQUEIA o turno até a resposta.

    É a fidelidade que importa: no executor real o ``canUseTool`` fica
    pendurado segurando a query inteira. Um fake que respondesse sozinho
    testaria o cartão, não o caso que interessa — a conversa travada esperando
    um humano.
    """
    qid = "q-" + secrets.token_hex(5)
    ev = threading.Event()
    conv.questions[qid] = {"event": ev, "answer": None}
    call_gateway(conv, "/questions", {
        "conversation_id": conv.id, "question_id": qid,
        "tool_use_id": "toolu_" + secrets.token_hex(6),
        "questions": questions, "allow_free_text": True})
    emit(conv, "question_needed", {"questionId": qid, "questions": questions,
                                   "allowFreeText": True})
    logger.info("conversa %s: PERGUNTA %s — esperando o operador", conv.id, qid)
    if not ev.wait(timeout):
        logger.info("conversa %s: pergunta %s expirou", conv.id, qid)
        return None
    answer = conv.questions.pop(qid, {}).get("answer")
    emit(conv, "question_resolved", {"questionId": qid, "source": "human"})
    return answer


def propose_approval(conv: Conversation, *, kind: str, tool_name: str,
                     tool_input: dict, summary: str, extra: dict | None = None) -> None:
    """Registra a aprovação no gateway (write-through) e emite o cartão."""
    aid = "ap-" + secrets.token_hex(6)
    payload = {"approval_id": aid, "conversation_id": conv.id,
               "tool_name": tool_name, "tool_input": tool_input, "summary": summary}
    res = call_gateway(conv, "/approvals", payload)
    if not (res and res.get("ok")):
        say(conv, f"Não consegui registrar a aprovação: {(res or {}).get('error')}")
        return
    conv.pending[aid] = {"kind": kind, "tool_input": tool_input, **(extra or {})}
    emit(conv, "approval_needed", {"approvalId": aid, "toolName": tool_name,
                                   "toolInput": tool_input, "summary": summary})
    say(conv, f"Proponho: {summary}\n\nAprove (✓) ou recuse (✕) no cartão acima.",
        persist=False)


def run_mutation(conv: Conversation) -> None:
    """Fluxo real: lê os agentes pelo bridge e propõe mudar o prompt de um."""
    res = call_gateway(conv, "/agents", method="GET")
    if not (res and res.get("ok")):
        say(conv, f"Não consegui ler os agentes pelo bridge: "
                  f"{(res or {}).get('error')}\n\n(Confira se o usuário logado tem "
                  f"a permissão `agent.config.manage`.)")
        return
    agents = res.get("data") or []
    if not agents:
        say(conv, "O bridge respondeu, mas não há agentes cadastrados.")
        return
    agent = agents[0]
    key = agent.get("key") or agent.get("agent_key")
    prompt = (agent.get("prompt") or "")
    marker = "\n\nSe a mensagem chegar fora do horário comercial, informe o horário " \
             "de atendimento e a próxima janela de resposta."
    propose_approval(
        conv, kind="prompt", tool_name="atualizar_prompt_do_agente",
        tool_input={"agent_key": key, "acrescentar": marker.strip()},
        summary=(f"Acrescentar a regra de horário ao prompt do agente "
                 f"'{agent.get('display_name') or key}'"
                 + ("" if STATE.allow_mutations else " (SIMULADO — o executor falso "
                    "está sem --allow-mutations, nada será gravado)")),
        extra={"agent_key": key, "new_prompt": prompt + marker})


def resolve_approval(conv: Conversation, aid: str, approved: bool, reason: str) -> None:
    ctx = conv.pending.pop(aid, None)
    try:
        if not approved:
            say(conv, f"Ok, descartei essa mudança."
                      + (f" Motivo: {reason}" if reason else ""))
            return
        if not ctx or ctx.get("kind") != "prompt":
            say(conv, "Mudança aprovada — nada a gravar (era um cartão de exemplo).")
            return
        if not STATE.allow_mutations:
            say(conv, "Mudança aprovada. **Nada foi gravado**: o executor falso "
                      "roda sem `--allow-mutations`. Suba com essa flag para exercitar "
                      "a escrita versionada de verdade.")
            return
        res = call_gateway(conv, f"/agents/{ctx['agent_key']}/prompt",
                           {"prompt": ctx["new_prompt"],
                            "change_note": "teste do executor falso"})
        if res and res.get("ok"):
            version = (res.get("data") or {}).get("version")
            say(conv, f"Prompt do agente `{ctx['agent_key']}` atualizado — "
                      f"versão **{version}** (dá para reverter pelo histórico).")
        else:
            say(conv, f"O bridge recusou a escrita: {(res or {}).get('error')}")
    finally:
        emit(conv, "done", {})


# ── HTTP ─────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FakeAiExecutor/1.0"

    def log_message(self, fmt, *args):  # silencia o log padrão barulhento
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # -- helpers --------------------------------------------------------------

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8", errors="replace") if length else ""

    def _json(self, payload, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _check_hmac(self, body: str) -> bool:
        if not STATE.verify:
            return True
        ts = self.headers.get("X-WB-Timestamp") or ""
        sig = self.headers.get("X-WB-Signature") or ""
        rid = self.headers.get("X-WB-Request-Id") or ""
        path = self.path.split("?")[0]
        if not ts or not sig or not rid:
            logger.warning("request sem headers HMAC: %s %s", self.command, path)
            self._json({"error": "assinatura ausente"}, 403)
            return False
        expected = sign(STATE.secret, self.command, path, ts, rid, body)
        if not hmac.compare_digest(expected, sig):
            logger.warning("assinatura INVÁLIDA em %s %s (secret errado?)",
                           self.command, path)
            self._json({"error": "assinatura inválida"}, 403)
            return False
        return True

    # -- rotas ----------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            # `expires_at` é o que destrava o monitor preventivo (fase 2): dá
            # para avisar ANTES de o token morrer em vez de reagir depois.
            # `None` quando a sessão já está morta.
            #
            # FORMATO ESPELHA O EXECUTOR REAL: string ISO 8601 em UTC com
            # milissegundos e sufixo `Z` (ex.: "2026-07-29T19:09:18.334Z"), NÃO
            # epoch. O real também manda `service`. Se este falso divergir, o
            # monitor da fase 2 nasce testado contra o formato errado.
            self._json({"ok": True, "service": "whatsbot-ai-server",
                        **caps_payload(), "claude": {
                "authenticated": STATE.authenticated,
                "mode": "oauth",
                "expires_at": iso_utc(STATE.expires_at) if STATE.authenticated else None,
                **({} if STATE.authenticated else {"reason": "token_expired"})}})
            return
        if path == "/_fake/state":
            with STATE.lock:
                self._json({"authenticated": STATE.authenticated,
                            "allow_mutations": STATE.allow_mutations,
                            "legacy_untyped": STATE.legacy_untyped,
                            "conversations": list(STATE.convs)})
            return
        if not self._check_hmac(""):
            return
        if path == "/auth-check":
            self._json({"ok": True})
            return
        if path.startswith("/conversations/") and path.endswith("/stream"):
            self._stream(path.split("/")[2])
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        body = self._read_body()

        # Controle local do fake — sem HMAC de propósito.
        if path.startswith("/_fake/"):
            self._fake_control(path, body)
            return
        if not self._check_hmac(body):
            return
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            payload = {}

        if path == "/conversations":
            self._start(payload)
            return
        if path.startswith("/admin/relogin/"):
            self._relogin(path.rsplit("/", 1)[-1], payload)
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "conversations":
            cid, action = parts[1], parts[2]
            conv = STATE.get(cid)
            if not conv and action != "resume":
                # 404 aqui é o gatilho do auto-resume do gateway (routes.py).
                logger.info("conversa %s desconhecida em /%s → 404", cid, action)
                self._json({"error": "conversation not found"}, 404)
                return
            handler = {"messages": self._message, "approve": self._approve,
                       "cancel": self._cancel, "resume": self._resume,
                       "interrupt": self._interrupt,
                       "effort": self._effort}.get(action)
            if handler:
                handler(cid, conv, payload)
                return
        # /conversations/{cid}/questions/{qid}/answer
        if (len(parts) == 5 and parts[0] == "conversations"
                and parts[2] == "questions" and parts[4] == "answer"):
            conv = STATE.get(parts[1])
            if not conv:
                self._json({"error": "conversation not found"}, 404)
                return
            self._answer_question(conv, parts[3], payload)
            return
        self._json({"error": "not found"}, 404)

    # -- implementações -------------------------------------------------------

    def _start(self, payload: dict) -> None:
        cid = str(payload.get("conversationId") or "")
        if not cid:
            self._json({"error": "conversationId obrigatório"}, 400)
            return
        conv = Conversation(cid, str(payload.get("callbackUrl") or ""),
                            payload.get("userId"), str(payload.get("model") or ""))
        with STATE.lock:
            STATE.convs[cid] = conv
        conv.effort = str(payload.get("effort") or "")
        logger.info("conversa %s CRIADA (user=%s callback=%s effort=%s)",
                    cid, conv.user_id, conv.callback_url, conv.effort or "-")
        self._json({"ok": True, "conversationId": cid, **caps_payload(),
                    "effort": conv.effort})

    def _resume(self, cid: str, conv: Conversation | None, payload: dict) -> None:
        """Recria o runner hidratando do histórico — é o que devolve vida a uma
        conversa antiga depois do relogin."""
        conv = Conversation(cid, str(payload.get("callbackUrl") or ""),
                            payload.get("userId"), str(payload.get("model") or ""))
        conv.turns = len(payload.get("history") or [])
        with STATE.lock:
            STATE.convs[cid] = conv
        conv.effort = str(payload.get("effort") or "")
        logger.info("conversa %s RETOMADA (%s turnos de histórico)", cid, conv.turns)
        self._json({"ok": True, "resumed": True, **caps_payload(),
                    "effort": conv.effort})

    def _message(self, cid: str, conv: Conversation, payload: dict) -> None:
        # Turno novo começa limpo: a marca de parada vale para UM turno.
        conv.stop.clear()
        if payload.get("effort"):
            conv.effort = str(payload["effort"])   # carona do gateway
        text = str(payload.get("text") or "")
        if not text and payload.get("parts"):
            text = " ".join(p.get("text", "") for p in payload["parts"]
                            if isinstance(p, dict) and p.get("type") == "text") \
                   or "[imagem]"
        logger.info("conversa %s ← %r", cid, text[:80])
        threading.Thread(target=run_turn, args=(conv, text), daemon=True).start()
        self._json({"ok": True, "accepted": True})

    def _approve(self, cid: str, conv: Conversation, payload: dict) -> None:
        aid = str(payload.get("approvalId") or payload.get("approval_id") or "")
        approved = bool(payload.get("approved"))
        reason = str(payload.get("reason") or "")
        logger.info("conversa %s: aprovação %s → %s", cid, aid,
                    "APROVADA" if approved else "RECUSADA")
        threading.Thread(target=resolve_approval,
                         args=(conv, aid, approved, reason), daemon=True).start()
        self._json({"ok": True})

    def _interrupt(self, cid: str, conv: Conversation, payload: dict) -> None:
        """Para o TURNO — a conversa continua viva (≠ /cancel, que a destrói)."""
        conv.stop.set()
        logger.info("conversa %s: turno INTERROMPIDO (%s)", cid,
                    payload.get("reason") or "user_stop")
        self._json({"ok": True, "interrupted": True})

    def _effort(self, cid: str, conv: Conversation, payload: dict) -> None:
        conv.effort = str(payload.get("effort") or "")
        # `next_turn` é a resposta honesta com um turno em voo: recriar a query
        # do SDK no meio dele perderia a resposta em andamento.
        applied = "now" if not conv.stop.is_set() else "next_turn"
        logger.info("conversa %s: esforço → %s (%s)", cid, conv.effort or "-", applied)
        self._json({"ok": True, "effort": conv.effort, "applied": applied})

    def _answer_question(self, conv: Conversation, qid: str, payload: dict) -> None:
        """Destrava o turno que estava pendurado no ``ask()``."""
        slot = conv.questions.get(qid)
        if not slot:
            self._json({"error": "question not found"}, 404)
            return
        upd = payload.get("updatedInput") or {}
        slot["answer"] = upd.get("answers") or upd.get("response") or ""
        slot["event"].set()
        logger.info("conversa %s: pergunta %s RESPONDIDA → %r",
                    conv.id, qid, slot["answer"])
        self._json({"ok": True})

    def _cancel(self, cid: str, conv: Conversation, payload: dict) -> None:
        logger.info("conversa %s CANCELADA", cid)
        with STATE.lock:
            STATE.convs.pop(cid, None)
        conv.events.put(("__close__", {}))
        self._json({"ok": True})

    def _relogin(self, action: str, payload: dict) -> None:
        if action == "start":
            sid = "relogin-" + secrets.token_hex(6)
            with STATE.lock:
                STATE.relogins[sid] = time.time()
            logger.info("relogin %s iniciado — cole QUALQUER código no painel "
                        "(use 'recusar' para testar a recusa)", sid)
            self._json({"ok": True, "sessionId": sid,
                        "url": "https://claude.ai/oauth/authorize?fake=1"})
            return
        if action == "complete":
            sid = str(payload.get("sessionId") or payload.get("session_id") or "")
            code = str(payload.get("code") or "").strip()
            if not code or code.lower() == "recusar":
                self._json({"error": "código inválido"}, 400)
                return
            with STATE.lock:
                STATE.relogins.pop(sid, None)
                STATE.renew()
                stale = list(STATE.convs)
            # O executor REAL não faz isto hoje — os runners antigos continuam
            # com a credencial morta. Aqui reconstruímos, que é o comportamento
            # correto proposto na análise.
            logger.info("relogin COMPLETO — sessão autenticada; %s runner(s) "
                        "seguem válidos: %s", len(stale), stale)
            self._json({"ok": True, "authenticated": True})
            return
        if action == "abort":
            with STATE.lock:
                STATE.relogins.pop(str(payload.get("sessionId") or ""), None)
            self._json({"ok": True})
            return
        self._json({"error": "not found"}, 404)

    def _fake_control(self, path: str, body: str) -> None:
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            payload = {}
        if path == "/_fake/mode":
            mode = str(payload.get("mode") or "").lower()
            if mode not in ("ok", "dead"):
                self._json({"error": "mode deve ser 'ok' ou 'dead'"}, 400)
                return
            with STATE.lock:
                if mode == "ok":
                    STATE.renew()
                else:
                    STATE.authenticated = False
            logger.info("modo alterado para %s", mode.upper())
            self._json({"ok": True, "authenticated": STATE.authenticated})
            return
        self._json({"error": "not found"}, 404)

    def _stream(self, cid: str) -> None:
        """SSE: drena a fila da conversa. Sem Content-Length — o corpo termina
        quando a conexão cai (o gateway lê com read-timeout infinito)."""
        conv = STATE.get(cid)
        if not conv:
            self._json({"error": "conversation not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        logger.info("conversa %s: stream SSE conectado", cid)
        last_beat = time.time()
        try:
            while True:
                try:
                    event, data = conv.events.get(timeout=1.0)
                except queue.Empty:
                    if time.time() - last_beat > 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_beat = time.time()
                    if STATE.get(cid) is None:
                        break
                    continue
                if event == "__close__":
                    break
                frame = (f"event: {event}\n"
                         f"data: {json.dumps(data, ensure_ascii=False)}\n\n")
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
                last_beat = time.time()
        except (BrokenPipeError, ConnectionResetError):
            logger.info("conversa %s: stream SSE caiu (o gateway reconecta)", cid)


def main() -> None:
    ap = argparse.ArgumentParser(description="Executor Claude Code falso (testes)")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--secret", required=True,
                    help="o MESMO segredo configurado em plugin.melhorias.ai_server_secret")
    ap.add_argument("--mode", choices=("ok", "dead"), default="ok",
                    help="'dead' sobe já com a sessão do Claude expirada")
    ap.add_argument("--allow-mutations", action="store_true",
                    help="deixa o /mutacao GRAVAR o prompt do agente de verdade")
    ap.add_argument("--insecure", action="store_true",
                    help="não valida a assinatura HMAC de entrada (debug)")
    ap.add_argument("--legacy-untyped", action="store_true",
                    help="OMITE a chave 'kind' do write-through (finge ser o "
                         "executor antigo, sem declaração de tipagem)")
    ap.add_argument("--legacy-caps", action="store_true",
                    help="OMITE a chave 'capabilities' das respostas (finge ser "
                         "um executor anterior à negociação: o painel tem de "
                         "esconder esforço, Parar e perguntas SEM erro nenhum)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    STATE.secret = args.secret
    if args.mode == "ok":
        STATE.renew()
    else:
        STATE.authenticated = False
    STATE.allow_mutations = args.allow_mutations
    STATE.verify = not args.insecure
    STATE.legacy_untyped = args.legacy_untyped
    STATE.legacy_caps = args.legacy_caps

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    logger.info("executor FALSO em http://%s:%s (modo=%s, mutações=%s, "
                "write-through=%s)",
                args.host, args.port, args.mode,
                "ON" if args.allow_mutations else "OFF",
                "SEM kind (legado)" if args.legacy_untyped else "tipado")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("encerrando")


if __name__ == "__main__":
    main()
