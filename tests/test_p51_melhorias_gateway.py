"""Plano 51 · sub-plano 02 — gateway agêntico do plugin ``melhorias``.

Cobre: assinatura HMAC (vetor de referência), guard (gate dormente 404;
replay/ts/corpo alterado/OBO ausente ⇒ 403), multi-seleção (âncora + filhas),
fluxo external (approve → em_chat com executor stubbado), write-through
``_internal`` (messages/approvals/status + fechamento da sugestão), mutação
``_internal`` versionada (agent/variable) e idempotência do gate (b).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from unittest.mock import patch


_EXT = {
    "plugin.melhorias.generator_backend": "external",
    "plugin.melhorias.ai_server_url": "http://127.0.0.1:9",  # nunca chamado (stub)
    "plugin.melhorias.ai_server_secret": "s" * 40,
}


def _mods():
    ai_client = importlib.import_module("whatsbot_plugins.melhorias.ai_client")
    chat_logic = importlib.import_module("whatsbot_plugins.melhorias.chat_logic")
    logic = importlib.import_module("whatsbot_plugins.melhorias.logic")
    return ai_client, chat_logic, logic


def _seed_ai_reply(handler, phone, content="resposta marcada"):
    contact = handler._get_contact(phone)
    contact.add_message("user", "preciso de ajuda")
    saved = contact.add_message("assistant", content)
    return contact, saved


def _signed_headers(ai_client, method, path, body: str, obo="1"):
    ts = str(int(time.time()))
    rid = ai_client.new_request_id()
    sig = ai_client.sign("s" * 40, method, path, ts, rid, body)
    return {"X-WB-Timestamp": ts, "X-WB-Signature": sig,
            "X-WB-Request-Id": rid, "X-WB-On-Behalf-Of": obo,
            "Content-Type": "application/json"}


# ── HMAC: vetor de referência ────────────────────────────────────────────────

def test_sign_reference_vector(plugin_app):
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    ai_client, _, _ = _mods()
    sig = ai_client.sign("segredo-fixo", "POST", "/conversations", "1700000000",
                         "rid-1", '{"a":1}')
    # Vetor calculado uma vez e travado: detecta qualquer mudança na string
    # canônica (method\npath\nts\nrid\nbody).
    import hashlib, hmac as hmac_mod
    expected = hmac_mod.new(b"segredo-fixo",
                            b"POST\n/conversations\n1700000000\nrid-1\n" + b'{"a":1}',
                            hashlib.sha256).hexdigest()
    assert sig == expected


# ── Guard: gate dormente + rejeições ─────────────────────────────────────────

def test_internal_is_404_when_backend_not_external(plugin_app):
    built = plugin_app("melhorias", settings_overrides={
        "plugin.melhorias.generator_backend": "stub"})
    r = built.client.post("/api/plugins/melhorias/public/_internal/messages",
                          json={"conversation_id": "x", "role": "user"})
    assert r.status_code == 404  # finge inexistência (D02-e)


def test_internal_is_404_when_unconfigured_at_default(plugin_app):
    """1.3.0: o default do backend virou 'external', mas sem URL+secret o gate
    dormente (is_configured) ainda finge inexistência — HMAC nunca é validado
    com secret vazio. Trava o caminho do NOVO default (config ausente)."""
    built = plugin_app("melhorias")  # nenhum override: backend cai no default
    r = built.client.post("/api/plugins/melhorias/public/_internal/messages",
                          json={"conversation_id": "x", "role": "user"})
    assert r.status_code == 404


def test_internal_rejects_bad_signatures(plugin_app):
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, _, _ = _mods()
    path = "/api/plugins/melhorias/public/_internal/messages"
    body = json.dumps({"conversation_id": "c1", "role": "user", "content": "oi"},
                      ensure_ascii=False, separators=(",", ":"))

    # Sem headers ⇒ 403 (nunca 401 de operador — rota é auth-exempt via /public/).
    r = built.client.post(path, content=body,
                          headers={"Content-Type": "application/json"})
    assert r.status_code == 403

    # Corpo alterado após assinar ⇒ 403.
    h = _signed_headers(ai_client, "POST", path, body)
    r = built.client.post(path, content=body + " ", headers=h)
    assert r.status_code == 403

    # Timestamp fora da janela ⇒ 403.
    h = _signed_headers(ai_client, "POST", path, body)
    h["X-WB-Timestamp"] = str(int(time.time()) - 3600)
    r = built.client.post(path, content=body, headers=h)
    assert r.status_code == 403

    # OBO ausente ⇒ 403.
    h = _signed_headers(ai_client, "POST", path, body)
    del h["X-WB-On-Behalf-Of"]
    r = built.client.post(path, content=body, headers=h)
    assert r.status_code == 403

    # Replay do MESMO request-id ⇒ 2º recusado.
    h = _signed_headers(ai_client, "POST", path, body)
    r1 = built.client.post(path, content=body, headers=h)
    r2 = built.client.post(path, content=body, headers=h)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 403


# ── Multi-seleção: âncora + filhas ───────────────────────────────────────────

def test_create_suggestion_multi_messages(plugin_app):
    built = plugin_app("melhorias", settings_overrides={
        "plugin.melhorias.generator_backend": "stub"})
    handler = built.agent_handler
    phone = "5511960100001"
    contact = handler._get_contact(phone)
    contact.add_message("user", "oi")
    a = contact.add_message("assistant", "resposta A")
    b = contact.add_message("assistant", "resposta B")

    r = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone,
        "messages": [
            {"content": "resposta A", "ts": a["ts"], "_id": a["id"]},
            {"content": "resposta B", "ts": b["ts"], "_id": b["id"]},
        ],
        "feedback": "as duas sairam erradas",
        "conversation_id": a["conversation_id"],
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # Âncora = 1ª mensagem (colunas single preservadas p/ deep-link/busca).
    assert data["message_content"] == "resposta A"
    assert [m["content"] for m in data["messages"]] == ["resposta A", "resposta B"]
    assert data["messages"][1]["_id"] == b["id"]

    # Compat: singular continua aceito.
    r = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone,
        "message": {"content": "resposta A", "ts": a["ts"], "_id": a["id"]},
        "feedback": "so uma",
    })
    assert r.status_code == 200, r.text
    assert r.json()["data"]["messages"][0]["content"] == "resposta A"


# ── Fluxo external: approve abre a conversa (executor stubbado) ──────────────

def test_external_approve_opens_chat_and_completed_closes(plugin_app):
    built = plugin_app("melhorias", settings_overrides=_EXT)
    handler = built.agent_handler
    ai_client, chat_logic, logic = _mods()
    phone = "5511960100002"
    _, saved = _seed_ai_reply(handler, phone)

    r = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone,
        "message": {"content": "resposta marcada", "ts": saved["ts"],
                    "_id": saved["id"]},
        "feedback": "errada",
        "conversation_id": saved["conversation_id"],
    })
    sid = r.json()["data"]["id"]

    sent = {}

    async def fake_start(cid, *, user_id, target, model=""):
        sent["start"] = {"cid": cid, "target": target}
        return {}

    async def fake_send(cid, *, user_id, text="", parts=None):
        sent["first_message"] = text
        return {}

    with patch.object(ai_client, "start", side_effect=fake_start), \
         patch.object(ai_client, "send", side_effect=fake_send), \
         patch.object(chat_logic, "ensure_consumer", lambda *a, **k: None):
        r = built.client.post(
            f"/api/plugins/melhorias/suggestions/{sid}/conversations",
            json={"observation": "foca no tom da resposta"})
    assert r.status_code == 200, r.text
    conv = r.json()["data"]["conversation"]
    assert conv["status"] == "ACTIVE"
    assert r.json()["data"]["suggestion"]["status"] == "em_chat"
    assert sent["start"]["target"]["suggestion_id"] == sid
    # Payload inicial carrega o contexto + a observação do gate (a).
    assert "resposta marcada" in sent["first_message"]
    assert "foca no tom da resposta" in sent["first_message"]

    cid = conv["id"]

    # Write-through do executor: mensagens + status COMPLETED fecham a sugestão.
    path = "/api/plugins/melhorias/public/_internal/messages"
    body = json.dumps({"conversation_id": cid, "role": "assistant",
                       "content": "## Diagnóstico final\ntudo certo"},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text

    path = "/api/plugins/melhorias/public/_internal/conversation-status"
    body = json.dumps({"conversation_id": cid, "status": "COMPLETED"},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text

    final = built.client.get(
        f"/api/plugins/melhorias/suggestions/{sid}").json()["data"]
    assert final["status"] == "aprovada"
    assert "Diagnóstico final" in final["analysis"]

    detail = built.client.get(
        f"/api/plugins/melhorias/conversations/{cid}").json()["data"]
    assert detail["conversation"]["status"] == "COMPLETED"
    assert any(m["role"] == "assistant" for m in detail["messages"])


# ── Gate (b): aprovação idempotente ──────────────────────────────────────────

def test_approval_decision_is_idempotent(plugin_app):
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, logic = _mods()
    handler = built.agent_handler
    phone = "5511960100003"
    _, saved = _seed_ai_reply(handler, phone)
    r = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone, "message": {"content": "resposta marcada",
                                    "ts": saved["ts"], "_id": saved["id"]},
        "feedback": "x"})
    sid = r.json()["data"]["id"]
    conv = chat_logic.create_conversation(sid)
    cid = conv["id"]

    # Executor registra o approval via _internal.
    path = "/api/plugins/melhorias/public/_internal/approvals"
    body = json.dumps({"approval_id": "appr-1", "conversation_id": cid,
                       "tool_name": "patch_agent_prompt",
                       "tool_input": {"prompt": "novo"},
                       "summary": "Editar prompt do agente default"},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text

    async def fake_approve(cid_, **kwargs):
        return {}

    with patch.object(ai_client, "approve", side_effect=fake_approve):
        r1 = built.client.post(
            f"/api/plugins/melhorias/conversations/{cid}/approve",
            json={"approval_id": "appr-1", "approved": True})
        r2 = built.client.post(
            f"/api/plugins/melhorias/conversations/{cid}/approve",
            json={"approval_id": "appr-1", "approved": False})
    assert r1.status_code == 200, r1.text
    assert r1.json()["data"]["approved"] == 1
    assert r2.status_code == 409  # já decidida


# ── Mutação _internal: escreve via repo VERSIONADO ───────────────────────────

def test_internal_mutations_are_versioned(plugin_app):
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, _, _ = _mods()
    from agent import agent_factory
    from db.repositories import agent_repo, variable_repo

    agent_factory.seed_default_agent()
    before = agent_repo.get(agent_repo.DEFAULT_AGENT_KEY)

    path = f"/api/plugins/melhorias/public/_internal/agents/{agent_repo.DEFAULT_AGENT_KEY}/prompt"
    body = json.dumps({"prompt": "prompt melhorado pela IA (teste p51)",
                       "change_note": "teste p51"},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text
    after = r.json()["data"]
    assert after["version"] == before["version"] + 1
    assert after["prompt"] == "prompt melhorado pela IA (teste p51)"
    # História cresceu → rollback possível.
    hist = agent_repo.list_history(agent_repo.DEFAULT_AGENT_KEY)
    assert hist and hist[0]["version"] == after["version"]

    # Variável: save versionado + rollback forward via _internal.
    variable_repo.delete("p51_gw_var")
    path = "/api/plugins/melhorias/public/_internal/variables/p51_gw_var"
    body = json.dumps({"value": "v1"}, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200 and r.json()["data"]["version"] == 1
    body = json.dumps({"value": "v2"}, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.json()["data"]["version"] == 2
    path = "/api/plugins/melhorias/public/_internal/variables/p51_gw_var/rollback/1"
    r = built.client.post(path, content="",
                          headers=_signed_headers(ai_client, "POST", path, ""))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["value"] == "v1"
    assert r.json()["data"]["version"] == 3
    variable_repo.delete("p51_gw_var")


# ── Plano 60 · sessão do Claude expirada (camada 2) ──────────────────────────

_AUTH_401 = ('API Error: 401 {"type":"error","error":{"type":"authentication_error",'
             '"message":"OAuth token has expired · Please run /login"}}')


def _open_conversation(built, chat_logic, phone: str) -> tuple[int, str]:
    """Sugestão + conversa ACTIVE, sem passar pelo executor."""
    handler = built.agent_handler
    _, saved = _seed_ai_reply(handler, phone)
    r = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone,
        "message": {"content": "resposta marcada", "ts": saved["ts"],
                    "_id": saved["id"]},
        "feedback": "errada"})
    sid = r.json()["data"]["id"]
    return sid, chat_logic.create_conversation(sid)["id"]


def test_auth_error_write_through_is_not_ai_content(plugin_app):
    """O 401 chega como ``role: assistant`` em HTTP 200 (o SDK o converte em
    texto). O gateway o registra como FALHA: ``role=system`` + conversa
    ``AUTH_EXPIRED`` + estado global — e ele NUNCA vira a análise final."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, logic = _mods()
    sid, cid = _open_conversation(built, chat_logic, "5511960100060")

    # Uma resposta boa ANTES do erro — é ela que deve sobreviver como análise.
    chat_logic.append_chat_message(cid, "assistant", content="## Diagnóstico\nok")

    path = "/api/plugins/melhorias/public/_internal/messages"
    body = json.dumps({"conversation_id": cid, "role": "assistant",
                       "content": _AUTH_401},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["auth_expired"] is True

    msgs = chat_logic.list_chat_messages(cid)
    err_rows = [m for m in msgs if _AUTH_401 in (m.get("content") or "")]
    assert err_rows and all(m["role"] == "system" for m in err_rows)

    assert chat_logic.get_conversation(cid)["status"] == "AUTH_EXPIRED"
    assert chat_logic.session_expired() is True
    # Blindagem: a "Análise gerada pela IA" pula o 401 e pega a resposta real.
    assert chat_logic._last_assistant_content(cid) == "## Diagnóstico\nok"


def test_auth_error_already_stored_as_assistant_is_skipped(plugin_app):
    """Blindagem retroativa (2.7): linhas de 401 JÁ gravadas como ``assistant``
    na base de produção não podem virar análise numa conclusão futura."""
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    sid, cid = _open_conversation(built, chat_logic, "5511960100061")
    chat_logic.append_chat_message(cid, "assistant", content="análise de verdade")
    chat_logic.append_chat_message(cid, "assistant", content=_AUTH_401)
    assert chat_logic._last_assistant_content(cid) == "análise de verdade"


def test_human_pasting_the_401_does_not_kill_the_session(plugin_app):
    """A mensagem do humano é persistida por OUTRO caminho (routes.py): colar o
    texto do erro no chat para perguntar sobre ele nunca marca a sessão morta."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    chat_logic.clear_session_state()
    sid, cid = _open_conversation(built, chat_logic, "5511960100062")

    async def fake_send(cid_, **kwargs):
        return {}

    with patch.object(ai_client, "send", side_effect=fake_send), \
         patch.object(chat_logic, "ensure_consumer", lambda *a, **k: None):
        r = built.client.post(
            f"/api/plugins/melhorias/conversations/{cid}/messages",
            json={"text": f"o que significa isso? {_AUTH_401}"})
    assert r.status_code == 200, r.text
    assert chat_logic.session_expired() is False
    assert chat_logic.get_conversation(cid)["status"] == "ACTIVE"

    # E o write-through com role != assistant também não classifica.
    path = "/api/plugins/melhorias/public/_internal/messages"
    body = json.dumps({"conversation_id": cid, "role": "user",
                       "content": _AUTH_401},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text
    assert chat_logic.session_expired() is False


def test_start_conversation_is_gated_while_session_expired(plugin_app):
    """Portão (2.6): com a sessão morta, abrir conversa nova é recusado com
    mensagem acionável — em vez de queimar a sugestão num chat natimorto."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    handler = built.agent_handler
    phone = "5511960100063"
    _, saved = _seed_ai_reply(handler, phone)
    r = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone,
        "message": {"content": "resposta marcada", "ts": saved["ts"],
                    "_id": saved["id"]},
        "feedback": "errada"})
    sid = r.json()["data"]["id"]

    chat_logic.mark_session_expired(conversation_id=None, message=_AUTH_401)
    started = {}

    async def fake_start(cid, **kwargs):
        started["called"] = True
        return {}

    with patch.object(ai_client, "start", side_effect=fake_start):
        r = built.client.post(
            f"/api/plugins/melhorias/suggestions/{sid}/conversations",
            json={"observation": ""})
    assert r.status_code == 400, r.text
    assert "expirada" in r.json()["error"].lower()
    assert "called" not in started  # nem chegou ao executor

    # O estado também sai no GET /config (leitura inicial do painel).
    cfg = built.client.get("/api/plugins/melhorias/config").json()["data"]
    assert cfg["ai_session"]["status"] == "expired"

    chat_logic.clear_session_state()


def test_resume_and_relogin_clear_the_session_state(plugin_app):
    """Recuperação (2.5): retomar a conversa E concluir o relogin apagam o
    estado global e devolvem a conversa ao ar."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    sid, cid = _open_conversation(built, chat_logic, "5511960100064")
    chat_logic.record_auth_failure(cid, _AUTH_401)
    assert chat_logic.session_expired() is True

    async def fake_resume(cid_, **kwargs):
        return {}

    with patch.object(ai_client, "resume", side_effect=fake_resume), \
         patch.object(chat_logic, "ensure_consumer", lambda *a, **k: None):
        r = built.client.post(f"/api/plugins/melhorias/conversations/{cid}/resume")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "ACTIVE"
    assert chat_logic.session_expired() is False

    # Relogin concluído com sucesso: mesma limpeza, pelo lado do servidor.
    chat_logic.mark_session_expired(conversation_id=cid, message=_AUTH_401)

    async def fake_relogin_complete(user_id, session_id, code):
        return {"ok": True, "authenticated": True}

    with patch.object(ai_client, "relogin_complete",
                      side_effect=fake_relogin_complete):
        r = built.client.post("/api/plugins/melhorias/admin/relogin/complete",
                              json={"sessionId": "s1", "code": "abc"})
    assert r.status_code == 200, r.text
    assert chat_logic.session_expired() is False


def test_resume_restarts_the_sse_consumer(plugin_app):
    """Retomar RECRIA o runner no executor — o stream ainda aberto aponta para o
    runner antigo e não entrega mais nada. Como ``ensure_consumer`` é idempotente
    (no-op com uma task viva), sem derrubar o consumidor o painel ficaria em
    "IA pensando…" para sempre enquanto o write-through persiste a resposta."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    sid, cid = _open_conversation(built, chat_logic, "5511960100065")

    async def fake_stream(cid_, suggestion_id, user_id=None):
        await asyncio.sleep(3600)          # segura a task viva, como o stream real

    async def fake_resume(cid_, **kwargs):
        return {}

    with patch.object(ai_client, "resume", side_effect=fake_resume), \
         patch.object(chat_logic, "_consume_stream", side_effect=fake_stream):
        r = built.client.post(f"/api/plugins/melhorias/conversations/{cid}/resume")
        assert r.status_code == 200, r.text
        first = chat_logic._consumers.get(cid)
        assert first is not None and not first.done()

        # 2º resume com o consumidor AINDA vivo: tem de trocar a task.
        r = built.client.post(f"/api/plugins/melhorias/conversations/{cid}/resume")
        assert r.status_code == 200, r.text
        second = chat_logic._consumers.get(cid)

    assert second is not None
    assert second is not first, "resume reaproveitou o stream morto"
    assert first.done(), "o consumidor antigo continuou pendurado no stream morto"
    chat_logic.stop_consumer(cid)


def test_is_auth_error_mirrors_the_frontend(plugin_app):
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    for bad in ("HTTP 401 Unauthorized", "authentication_error: invalid",
                "Please run /login to continue", "Invalid API key",
                "invalid authentication credentials"):
        assert chat_logic.is_auth_error(bad) is True, bad
    for ok in ("resposta normal do modelo", "", None, "4011 registros",
               "o total foi de 1401 reais"):
        assert chat_logic.is_auth_error(ok) is False, ok


# ── Parser SSE do consumidor ─────────────────────────────────────────────────

def test_parse_sse_frame(plugin_app):
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    ev = chat_logic.parse_sse_frame(
        'event: message_chunk\ndata: {"messageId":"m1","delta":"olá"}')
    assert ev == ("message_chunk", {"messageId": "m1", "delta": "olá"})
    # Heartbeat (linha ":") ignorado; frame só de comentário → None.
    assert chat_logic.parse_sse_frame(": keep-alive") is None
    # data inválido não explode.
    ev = chat_logic.parse_sse_frame("event: done\ndata: not-json")
    assert ev == ("done", {})


# ── Plano 61 · falhas TIPADAS do executor ────────────────────────────────────
#
# A classificação deixou de ser adivinhação por TEXTO: o executor manda um
# ``kind``. A heurística sobrevive só como fallback (executor antigo / balde
# ``unknown``). O que estes testes travam é a fronteira entre as duas.

def _reset_failure_state(chat_logic):
    """Zera o que é módulo-global entre casos (throttle + cooldown)."""
    chat_logic._failure_seen.clear()
    chat_logic._executor_cooldown_until = 0.0
    chat_logic.clear_session_state()


def _frame(kind=None, message="", retry_after=None) -> str:
    data = {"message": message}
    if kind is not None:
        data["kind"] = kind
    if retry_after is not None:
        data["retry_after"] = retry_after
    return f"event: error\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def test_typed_auth_required_kills_the_session_without_401_text(plugin_app):
    """O kind manda: sessão expirada é reconhecida mesmo num texto que não tem
    401 nem marcador nenhum — o que a heurística sozinha jamais pegaria."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    _, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100070")

    out = chat_logic.record_executor_failure(
        cid, "sua sessão acabou, refaça o login", kind="auth_required")

    assert out["fatal"] is True
    assert chat_logic.get_conversation(cid)["status"] == "AUTH_EXPIRED"
    assert chat_logic.session_expired() is True
    rows = [m for m in chat_logic.list_chat_messages(cid) if m["role"] == "system"]
    assert rows and rows[-1]["failure_kind"] == "auth_required"
    chat_logic.clear_session_state()


def test_typed_rate_limited_ignores_a_401_in_the_text(plugin_app):
    """A correção-título: limite de uso cujo corpo cita um 401 de upstream NÃO
    pode virar sessão expirada — era o falso positivo que fazia o painel
    oferecer "Renovar sessão" para algo que renovar não conserta."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    _, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100071")

    out = chat_logic.record_executor_failure(
        cid, 'Rate limit: upstream 401 {"type":"authentication_error"}',
        kind="rate_limited", retry_after=30)

    assert out["fatal"] is False
    assert chat_logic.session_expired() is False
    assert chat_logic.get_conversation(cid)["status"] == "ACTIVE"
    rows = [m for m in chat_logic.list_chat_messages(cid) if m["role"] == "system"]
    assert rows[-1]["failure_kind"] == "rate_limited"


def test_derive_kind_falls_back_to_the_heuristic(plugin_app):
    """A linha da retrocompatibilidade, incluindo o balde ``unknown``.

    ``unknown`` é um VALOR, não um vazio: escrever ``kind or heurística`` faria o
    fluxo parar nele e uma sessão de fato expirada deixaria de ser detectada.
    """
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    d = chat_logic.derive_kind
    # Sem kind (executor antigo): decide o texto.
    assert d({"message": _AUTH_401}) == "auth_required"
    assert d({"message": "análise normal"}) is None
    # kind == unknown: o executor não soube classificar ⇒ o texto ainda decide.
    assert d({"kind": "unknown", "message": _AUTH_401}) == "auth_required"
    assert d({"kind": "unknown", "message": "coisa qualquer"}) == "unknown"
    # kind reconhecido: manda nele, texto IGNORADO.
    assert d({"kind": "rate_limited", "message": _AUTH_401}) == "rate_limited"
    # kind que este plugin não conhece nunca é fatal.
    assert d({"kind": "credit_low", "message": "x"}) == "unknown"
    assert chat_logic.kind_is_fatal("credit_low") is False
    assert chat_logic.kind_is_fatal("unknown") is False
    assert chat_logic.kind_is_fatal("auth_required") is True


# ── Plano 62 · A — o texto para de decidir sobre CONTEÚDO DA IA ──────────────

def test_is_auth_error_strict_drops_the_lone_401(plugin_app):
    """O par que DEFINE o modo estrito: um 401 solto na prosa deixa de ser sinal.

    ``strict`` existe porque a mesma função serve dois canais de qualidade
    diferente — texto que já se sabe ser erro (frame ``event: error``) × conteúdo
    da IA, onde o 401 pode ser só o assunto da análise.
    """
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    f = chat_logic.is_auth_error

    for prosa in ("o total foi de 401 reais",
                  "o endpoint do cliente devolveu 401, oriente o agente",
                  "HTTP 401 Unauthorized"):
        assert f(prosa) is True, prosa            # frouxo: o número basta
        assert f(prosa, strict=True) is False, prosa   # estrito: não basta

    # A string REAL do SDK é pega nos DOIS modos — estrito não perde cobertura.
    assert f(_AUTH_401) is True
    assert f(_AUTH_401, strict=True) is True
    for marker in ("authentication_error: invalid", "Please run /login para seguir",
                   "Invalid API key", "invalid authentication credentials"):
        assert f(marker, strict=True) is True, marker
    for vazio in ("resposta normal do modelo", "", None):
        assert f(vazio, strict=True) is False, vazio


def test_derive_kind_strict_ignores_a_401_in_prose(plugin_app):
    """O modo estrito atravessa o ``derive_kind`` (é ele que os call sites usam)."""
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    d = chat_logic.derive_kind
    assert d({"message": _AUTH_401}, strict=True) == "auth_required"
    assert d({"message": "o endpoint devolveu 401 na consulta"},
             strict=True) is None
    # Sem strict o canal da SSE fica intacto: o 401 solto ainda classifica.
    assert d({"message": "o endpoint devolveu 401 na consulta"}) == "auth_required"
    # E o balde ``unknown`` continua caindo na heurística, agora estrita.
    assert d({"kind": "unknown", "message": _AUTH_401},
             strict=True) == "auth_required"
    assert d({"kind": "unknown", "message": "deu 401 lá"},
             strict=True) == "unknown"


def test_clamp_retry_after(plugin_app):
    """Sem clamp, um ``retry_after`` absurdo congelaria um consumidor por um dia."""
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    c = chat_logic.clamp_retry_after
    assert c(30) == 30 and c("45") == 45 and c(12.7) == 12
    assert c(86400) == chat_logic.RETRY_AFTER_MAX
    assert c(-5) == 0
    assert c(None) is None and c("depois") is None and c(True) is None


def test_transient_failure_never_writes_the_global_state(plugin_app):
    """Isolamento duro: o caminho não-fatal não pode ENCOSTAR na config.

    O publicador antigo tinha a sentinela ``persist=None`` significando *apagar*
    — um aviso transitório roteado por ali limparia a sessão expirada em silêncio
    e reabriria o portão de conversa nova.
    """
    built = plugin_app("melhorias", settings_overrides=_EXT)
    _, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100072")

    with patch.object(chat_logic.config_repo, "set") as cfg_set:
        chat_logic.record_executor_failure(cid, "limite", kind="rate_limited")
        chat_logic.record_executor_failure(cid, "sem crédito", kind="quota_exceeded")
        chat_logic.record_executor_failure(cid, "sobrecarga", kind="overloaded")
    assert cfg_set.call_count == 0, "falha não-fatal gravou estado global"


def test_transient_failure_cannot_resurrect_an_expired_session(plugin_app):
    """Um frame transitório atrasado, chegando depois do 401, não pode dizer que
    está tudo bem — no painel isso apagaria a faixa e reabriria o botão."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    _, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100073")

    chat_logic.record_executor_failure(cid, _AUTH_401, kind="auth_required")
    assert chat_logic.session_expired() is True
    chat_logic.record_executor_failure(cid, "limite", kind="rate_limited")
    assert chat_logic.session_expired() is True
    assert chat_logic.get_conversation(cid)["status"] == "AUTH_EXPIRED"
    chat_logic.clear_session_state()


def test_broadcast_matrix_per_kind(plugin_app):
    """Quem avisa quem, por kind — e UM cartão por falha, nunca dois."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    _, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100074")

    def events_for(kind):
        with patch.object(chat_logic, "broadcast") as bc:
            chat_logic._failure_seen.clear()
            chat_logic.record_executor_failure(cid, "msg", kind=kind)
            return [c.args[0] for c in bc.call_args_list], bc.call_args_list

    names, calls = events_for("auth_required")
    assert chat_logic.SESSION_EVENT in names          # faixa persistida
    assert "plugin_melhorias_ai_event" in names
    conv_ev = [c.args[1] for c in calls if c.args[0] == "plugin_melhorias_ai_event"]
    assert conv_ev[0]["event"] == "auth_expired"
    assert conv_ev[0]["data"]["kind"] == "auth_required"
    chat_logic.clear_session_state()

    for kind in ("rate_limited", "quota_exceeded", "overloaded", "unknown"):
        names, calls = events_for(kind)
        assert chat_logic.SESSION_EVENT not in names, f"{kind} mexeu na sessão global"
        assert chat_logic.NOTICE_EVENT in names, kind
        conv_ev = [c.args[1] for c in calls if c.args[0] == "plugin_melhorias_ai_event"]
        assert conv_ev[0]["event"] == "executor_failure", kind
        assert conv_ev[0]["data"]["kind"] == kind
    assert chat_logic.session_expired() is False


def test_quota_exceeded_does_not_block_new_conversations(plugin_app):
    """Sem crédito avisa, mas não fecha a porta: o gateway não tem como saber que
    a cota voltou (o ``/health`` só responde por autenticação), então bloquear
    sem sinal de liberação prenderia o operador até alguém mexer na config."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    handler = built.agent_handler
    phone = "5511960100075"
    _, saved = _seed_ai_reply(handler, phone)
    r = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone,
        "message": {"content": "resposta marcada", "ts": saved["ts"],
                    "_id": saved["id"]},
        "feedback": "errada"})
    sid = r.json()["data"]["id"]
    cid0 = chat_logic.create_conversation(sid)["id"]
    chat_logic.record_executor_failure(cid0, "sem crédito", kind="quota_exceeded")

    async def fake_start(cid, **kwargs):
        return {}

    async def fake_send(cid, **kwargs):
        return {}

    with patch.object(ai_client, "start", side_effect=fake_start), \
         patch.object(ai_client, "send", side_effect=fake_send), \
         patch.object(chat_logic, "ensure_consumer", lambda *a, **k: None):
        r = built.client.post(
            f"/api/plugins/melhorias/suggestions/{sid}/conversations",
            json={"observation": ""})
    assert r.status_code == 200, r.text
    cfg = built.client.get("/api/plugins/melhorias/config").json()["data"]
    assert cfg["ai_session"]["status"] == "ok"


def test_failure_rows_carry_the_kind_and_stay_out_of_the_resume(plugin_app):
    """A linha de falha guarda o tipo (é o que a hidratação lê) e NUNCA volta ao
    executor no histórico do resume."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100076")
    chat_logic.append_chat_message(cid, "assistant", content="## Diagnóstico\nok")
    chat_logic.record_executor_failure(cid, "sem crédito", kind="quota_exceeded")
    # Linha LEGADA (gravada antes do kind existir) continua válida com NULL.
    legacy = chat_logic.append_chat_message(cid, "system", content=_AUTH_401)

    rows = {m["id"]: m for m in chat_logic.list_chat_messages(cid)}
    assert rows[legacy]["failure_kind"] is None
    kinds = [m["failure_kind"] for m in rows.values() if m["failure_kind"]]
    assert kinds == ["quota_exceeded"]
    # A falha não pode virar a "Análise gerada pela IA".
    assert chat_logic._last_assistant_content(cid) == "## Diagnóstico\nok"

    seen = {}

    async def fake_resume(cid_, **kwargs):
        seen.update(kwargs)
        return {}

    with patch.object(ai_client, "resume", side_effect=fake_resume), \
         patch.object(chat_logic, "ensure_consumer", lambda *a, **k: None):
        asyncio.get_event_loop().run_until_complete(
            chat_logic.resume_conversation(cid))
    assert all(h["role"] in ("user", "assistant") for h in seen["history"])
    chat_logic.clear_session_state()


def test_repeated_transient_frames_are_throttled(plugin_app):
    """O executor retenta internamente e cospe o mesmo 429 várias vezes: uma
    falha só não pode virar cinco linhas no banco e cinco cartões na tela."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    _, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100077")

    for _ in range(5):
        chat_logic.record_executor_failure(cid, "limite", kind="rate_limited")
    rows = [m for m in chat_logic.list_chat_messages(cid)
            if m.get("failure_kind") == "rate_limited"]
    assert len(rows) == 1
    # Kind DIFERENTE não é abafado pelo throttle do anterior.
    chat_logic.record_executor_failure(cid, "sem crédito", kind="quota_exceeded")
    assert [m for m in chat_logic.list_chat_messages(cid)
            if m.get("failure_kind") == "quota_exceeded"]


def test_late_failure_cannot_downgrade_a_finished_conversation(plugin_app):
    """Bug latente que este trabalho fecha: um frame de erro atrasado virava uma
    conversa já COMPLETED em AUTH_EXPIRED, DEPOIS de a sugestão ter sido
    finalizada."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    _, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100078")
    chat_logic.set_conversation_status(cid, "COMPLETED")

    chat_logic.record_executor_failure(cid, _AUTH_401, kind="auth_required")
    assert chat_logic.get_conversation(cid)["status"] == "COMPLETED"
    chat_logic.clear_session_state()


def test_resume_stops_the_consumer_before_calling_the_executor(plugin_app):
    """Ordem importa: enquanto o ``resume`` está na rede, o consumidor VELHO
    ainda roda e pode gravar AUTH_EXPIRED depois do ACTIVE — justo quando o
    operador acabou de clicar "Renovar sessão"."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100079")
    order = []

    async def fake_resume(cid_, **kwargs):
        order.append("resume")
        return {}

    with patch.object(ai_client, "resume", side_effect=fake_resume), \
         patch.object(chat_logic, "stop_consumer",
                      side_effect=lambda c: order.append("stop")), \
         patch.object(chat_logic, "ensure_consumer", lambda *a, **k: None):
        r = built.client.post(f"/api/plugins/melhorias/conversations/{cid}/resume")
    assert r.status_code == 200, r.text
    assert order == ["stop", "resume"]


def test_resume_restores_the_consumer_when_the_executor_refuses(plugin_app):
    """Derrubar antes de chamar tem um preço: se o executor recusar, a conversa
    ficaria ACTIVE sem ninguém escutando. O consumidor volta."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100080")
    restored = []

    async def boom(cid_, **kwargs):
        raise RuntimeError("executor fora do ar")

    with patch.object(ai_client, "resume", side_effect=boom), \
         patch.object(chat_logic, "ensure_consumer",
                      side_effect=lambda *a, **k: restored.append(a)):
        r = built.client.post(f"/api/plugins/melhorias/conversations/{cid}/resume")
    assert r.status_code == 502, r.text
    assert restored, "consumidor não foi devolvido após o resume falhar"


# ── Consumidor SSE ───────────────────────────────────────────────────────────

class _Clock:
    """Relógio falso: ``sleep`` avança o tempo em vez de queimar wall-clock.

    Sem ele o teste do cooldown esperaria 30s REAIS por reconexão (o laço
    re-checa ``_cooldown_remaining()``, que só encolhe com o tempo de verdade).
    """

    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def now(self):
        return self.t

    def advance(self, secs):
        self.t += max(0.0, float(secs or 0))


def _drive_stream(chat_logic, ai_client, cid, sid, frames, *, rounds=2, clock=None):
    """Roda ``_consume_stream`` por N reconexões, capturando a ordem dos eventos.

    Cada abertura entrega ``frames`` e fecha LIMPO (sem exceção) — que é
    exatamente o caso que matava o consumidor em silêncio.
    """
    clock = clock or _Clock()
    events: list[tuple] = []
    opens = {"n": 0}

    async def fake_open(cid_, **kwargs):
        opens["n"] += 1
        if opens["n"] > rounds:
            raise asyncio.CancelledError
        events.append(("open", opens["n"]))
        for f in frames:
            yield f.encode("utf-8")

    async def fake_sleep(secs):
        events.append(("sleep", secs))
        clock.advance(secs)

    with patch.object(ai_client, "open_stream", fake_open), \
         patch.object(chat_logic, "now", clock.now), \
         patch.object(chat_logic.asyncio, "sleep", side_effect=fake_sleep):
        try:
            asyncio.get_event_loop().run_until_complete(
                chat_logic._consume_stream(cid, sid))
        except asyncio.CancelledError:
            pass
    waits = [s for kind, s in events if kind == "sleep"]
    return waits, opens["n"], events


def test_consumer_survives_clean_closes_and_honors_retry_after(plugin_app):
    """Os dois furos do laço, num caso só.

    (a) o contador só zerava DENTRO do ``async for``, então um fechamento limpo
        caía direto no ``attempts += 1`` e cinco deles matavam o consumidor —
        conversa zumbi presa em "IA pensando…";
    (b) religar em 2s num executor que acabou de dizer "espere 30s" é martelar.
    """
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100081")
    # Qualquer stream que entregou algo conta como saudável neste teste.
    with patch.object(chat_logic, "STREAM_HEALTHY_SEC", 0.0):
        waits, opens, _ = _drive_stream(
            chat_logic, ai_client, cid, sid,
            [_frame("rate_limited", "limite", retry_after=30)], rounds=8)

    # Sobreviveu a MUITO mais que as 5 tentativas do orçamento antigo.
    assert opens > 6, "o consumidor desistiu depois de fechamentos limpos"
    # E cada religada respeitou o "espere 30s" em vez do backoff de 2s.
    assert waits and all(w == 30 for w in waits), waits
    assert chat_logic.session_expired() is False


def test_consumer_shares_a_cooldown_across_conversations(plugin_app):
    """Limite/sobrecarga são da CONTA do executor: com cinco conversas abertas,
    cinco laços martelariam em paralelo."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid_a, cid_a = _open_conversation(built, chat_logic, "5511960100082")
    sid_b, cid_b = _open_conversation(built, chat_logic, "5511960100083")

    clock = _Clock()
    # A conversa A apanha do executor (sem rodar o laço: o sleep pós-rodada
    # consumiria justamente o cooldown que queremos observar na B).
    with patch.object(chat_logic, "now", clock.now):
        asyncio.get_event_loop().run_until_complete(chat_logic._handle_error_frame(
            cid_a, sid_a, {"kind": "overloaded", "message": "529"}))
        assert chat_logic._cooldown_remaining() == chat_logic.OVERLOADED_FLOOR

    # A conversa B ESPERA antes de sequer abrir o stream — o relógio é comum.
    _waits, _opens, events = _drive_stream(chat_logic, ai_client, cid_b, sid_b,
                                           [], rounds=1, clock=clock)
    assert events and events[0][0] == "sleep", \
        "a outra conversa ignorou o cooldown compartilhado"
    assert events[0][1] == chat_logic.OVERLOADED_FLOOR
    assert events[1][0] == "open"       # e só então abre
    chat_logic._executor_cooldown_until = 0.0


def test_untyped_error_frame_keeps_todays_behavior(plugin_app):
    """Executor antigo: o ``error`` cru é repassado ao painel e NADA é
    persistido — exatamente como antes."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100084")

    with patch.object(chat_logic, "broadcast") as bc:
        _drive_stream(chat_logic, ai_client, cid, sid,
                      [_frame(None, "falha simulada no runner")], rounds=1)
        evs = [c.args[1] for c in bc.call_args_list
               if c.args[0] == "plugin_melhorias_ai_event"]
    assert [e["event"] for e in evs] == ["error"]
    assert not [m for m in chat_logic.list_chat_messages(cid) if m["role"] == "system"]
    assert chat_logic.session_expired() is False


def test_recognized_kind_emits_only_the_typed_event(plugin_app):
    """Kind reconhecido não pode emitir o ``error`` cru TAMBÉM — seriam dois
    cartões vermelhos para uma falha só."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100085")

    with patch.object(chat_logic, "broadcast") as bc:
        _drive_stream(chat_logic, ai_client, cid, sid,
                      [_frame("quota_exceeded", "sem crédito")], rounds=1)
        evs = [c.args[1] for c in bc.call_args_list
               if c.args[0] == "plugin_melhorias_ai_event"]
    assert [e["event"] for e in evs] == ["executor_failure"]


def test_consumer_gives_up_loudly(plugin_app):
    """Desistir em silêncio deixava o chat girando para sempre."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100086")

    async def dead_open(cid_, **kwargs):
        raise RuntimeError("connection refused")
        yield b""  # pragma: no cover — mantém a função geradora

    async def fake_sleep(secs):
        return None

    with patch.object(ai_client, "open_stream", dead_open), \
         patch.object(chat_logic.asyncio, "sleep", side_effect=fake_sleep), \
         patch.object(chat_logic, "broadcast") as bc:
        asyncio.get_event_loop().run_until_complete(
            chat_logic._consume_stream(cid, sid))
        evs = [c.args[1] for c in bc.call_args_list
               if c.args[0] == "plugin_melhorias_ai_event"]
    assert evs and evs[-1]["event"] == "executor_failure"
    assert cid not in chat_logic._consumers


# ── Write-through (rede de segurança) ────────────────────────────────────────

def test_write_through_typed_kind_is_not_fatal(plugin_app):
    """Com ``kind`` no corpo, o write-through também para de adivinhar."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100087")

    path = "/api/plugins/melhorias/public/_internal/messages"
    body = json.dumps({"conversation_id": cid, "role": "assistant",
                       "kind": "rate_limited", "retry_after": 20,
                       "content": f"limite atingido — upstream disse {_AUTH_401}"},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kind"] == "rate_limited"
    assert data["fatal"] is False
    assert data["auth_expired"] is False      # chave antiga preservada
    assert chat_logic.session_expired() is False
    assert chat_logic.get_conversation(cid)["status"] == "ACTIVE"


def test_write_through_cannot_forge_the_typed_slot(plugin_app):
    """Só o helper do gateway escreve ``failure_kind``; o executor não pode
    carimbar uma linha comum como se fosse falha."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100088")

    path = "/api/plugins/melhorias/public/_internal/messages"
    body = json.dumps({"conversation_id": cid, "role": "system",
                       "failure_kind": "auth_required", "tool_name": "forjado",
                       "content": "aviso qualquer"},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text
    row = chat_logic.list_chat_messages(cid)[-1]
    assert row["failure_kind"] is None
    assert row["tool_name"] is None
    assert chat_logic.session_expired() is False


def test_human_pasting_a_rate_limit_message_does_nothing(plugin_app):
    """Extensão da garantia do plano 60 aos kinds novos."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100089")

    path = "/api/plugins/melhorias/public/_internal/messages"
    body = json.dumps({"conversation_id": cid, "role": "user",
                       "kind": "auth_required", "content": _AUTH_401},
                      ensure_ascii=False, separators=(",", ":"))
    r = built.client.post(path, content=body,
                          headers=_signed_headers(ai_client, "POST", path, body))
    assert r.status_code == 200, r.text
    assert chat_logic.session_expired() is False
    assert chat_logic.get_conversation(cid)["status"] == "ACTIVE"


# ── Plano 62 · o texto sai do circuito no write-through ──────────────────────

_MSG_PATH = "/api/plugins/melhorias/public/_internal/messages"


def _post_message(built, ai_client, body_dict: dict):
    """POST assinado em ``_internal/messages`` — o canal do write-through."""
    body = json.dumps(body_dict, ensure_ascii=False, separators=(",", ":"))
    return built.client.post(
        _MSG_PATH, content=body,
        headers=_signed_headers(ai_client, "POST", _MSG_PATH, body))


def test_write_through_prose_401_stays_a_normal_message(plugin_app):
    """O TESTE-TÍTULO deste trabalho.

    Este plugin analisa atendimentos: citar um erro 401 em prosa é assunto
    plausível de uma análise. A regex frouxa concluía "sessão expirada" a partir
    do CONTEÚDO DA IA — derrubava a sessão GLOBAL e bloqueava conversa nova. É o
    bug original no lugar de maior consequência.
    """
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100090")
    analise = ("**Diagnóstico**\n\nO endpoint do cliente devolveu 401 na consulta "
               "de pedido, então a tool falhou e o agente improvisou um prazo.")

    r = _post_message(built, ai_client, {"conversation_id": cid,
                                         "role": "assistant", "content": analise})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "kind" not in data, "análise legítima foi classificada como falha"

    row = chat_logic.list_chat_messages(cid)[-1]
    assert row["role"] == "assistant"      # bolha comum, não linha de falha
    assert row["failure_kind"] is None
    assert chat_logic.session_expired() is False
    assert chat_logic.get_conversation(cid)["status"] == "ACTIVE"
    # E ela é elegível como análise final (a frouxa a descartava).
    assert chat_logic._last_assistant_content(cid) == analise


def test_write_through_literal_sdk_marker_is_still_fatal(plugin_app):
    """A rede de segurança NÃO foi desmontada: sem etiqueta, marcador literal do
    SDK no conteúdo da IA continua sendo sessão expirada (executor antigo)."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100091")

    r = _post_message(built, ai_client, {
        "conversation_id": cid, "role": "assistant",
        "content": "OAuth token has expired · Please run /login"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["auth_expired"] is True
    assert chat_logic.get_conversation(cid)["status"] == "AUTH_EXPIRED"
    assert chat_logic.session_expired() is True
    chat_logic.clear_session_state()


def test_error_frame_without_kind_still_reads_the_lone_401(plugin_app):
    """O canal da SSE ficou INTACTO no modo frouxo: o frame já É um erro, a única
    dúvida é QUAL falha é — ali o 401 solto continua sendo informação boa."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    _, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100092")

    asyncio.get_event_loop().run_until_complete(
        chat_logic._handle_error_frame(cid, sid, {"message": "HTTP 401 na sessão"}))
    assert chat_logic.get_conversation(cid)["status"] == "AUTH_EXPIRED"
    assert chat_logic.session_expired() is True
    chat_logic.clear_session_state()


def test_last_assistant_content_keeps_an_analysis_mentioning_401(plugin_app):
    """S2 ficou estrito: a análise que cita um 401 volta a ser elegível, e a
    linha LEGADA com a string real do SDK continua sendo pulada."""
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    sid, cid = _open_conversation(built, chat_logic, "5511960100093")
    chat_logic.append_chat_message(cid, "assistant", content="análise anterior")
    boa = "A integração devolveu 401 — peça ao time para revalidar a credencial."
    chat_logic.append_chat_message(cid, "assistant", content=boa)
    assert chat_logic._last_assistant_content(cid) == boa

    # Linha legada de 401 gravada como assistant: continua fora.
    chat_logic.append_chat_message(cid, "assistant", content=_AUTH_401)
    assert chat_logic._last_assistant_content(cid) == boa


def test_write_through_presence_of_kind_decides_not_its_value(plugin_app):
    """A armadilha do plano 62 · B, travada.

    Com a chave SEMPRE no corpo, *presença* significa "executor tipado" e
    *ausência* "executor antigo" — o ``null`` é sinal de CAPACIDADE, não valor a
    interpretar. Testar ``body.get("kind")`` (que devolve ``None`` nos dois casos)
    anularia o ramo em silêncio; por isso os dois corpos, com o MESMO conteúdo,
    têm de produzir resultados DIFERENTES.
    """
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()

    # (a) chave PRESENTE e null ⇒ executor tipado: o texto nunca é lido.
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100094")
    r = _post_message(built, ai_client, {"conversation_id": cid,
                                         "role": "assistant", "kind": None,
                                         "content": _AUTH_401})
    assert r.status_code == 200, r.text
    tipado = r.json()["data"]
    assert "kind" not in tipado
    assert chat_logic.list_chat_messages(cid)[-1]["role"] == "assistant"
    assert chat_logic.session_expired() is False
    assert chat_logic.get_conversation(cid)["status"] == "ACTIVE"

    # (b) chave AUSENTE, mesmo conteúdo ⇒ caminho antigo preservado.
    _reset_failure_state(chat_logic)
    sid2, cid2 = _open_conversation(built, chat_logic, "5511960100095")
    r = _post_message(built, ai_client, {"conversation_id": cid2,
                                         "role": "assistant",
                                         "content": _AUTH_401})
    assert r.status_code == 200, r.text
    legado = r.json()["data"]
    assert legado["kind"] == "auth_required" and legado["fatal"] is True
    assert chat_logic.get_conversation(cid2)["status"] == "AUTH_EXPIRED"

    assert tipado != legado, "presença da chave não mudou nada — B é no-op"
    chat_logic.clear_session_state()


def test_write_through_typed_non_fatal_kind_keeps_the_compat_key(plugin_app):
    """Etiqueta não-fatal é honrada e a chave antiga ``auth_expired`` sobrevive
    (o executor pode ramificar nela)."""
    built = plugin_app("melhorias", settings_overrides=_EXT)
    ai_client, chat_logic, _ = _mods()
    _reset_failure_state(chat_logic)
    sid, cid = _open_conversation(built, chat_logic, "5511960100096")

    r = _post_message(built, ai_client, {
        "conversation_id": cid, "role": "assistant", "kind": "quota_exceeded",
        "content": f"sem crédito — upstream mandou {_AUTH_401}"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kind"] == "quota_exceeded"
    assert data["fatal"] is False
    assert data["auth_expired"] is False
    assert chat_logic.session_expired() is False
    assert chat_logic.get_conversation(cid)["status"] == "ACTIVE"


def test_clear_session_state_keeps_an_ok_marker(plugin_app):
    """``clear`` gravava string vazia, então ``get_session_state`` voltava sem
    ``at`` e o test-connection reportava "desconhecido" para SEMPRE depois de um
    relogin bem-sucedido."""
    built = plugin_app("melhorias", settings_overrides=_EXT)  # noqa: F841
    _, chat_logic, _ = _mods()
    chat_logic.mark_session_expired(conversation_id=None, message=_AUTH_401)
    chat_logic.clear_session_state()
    state = chat_logic.get_session_state()
    assert state["status"] == "ok"
    assert state.get("at")
