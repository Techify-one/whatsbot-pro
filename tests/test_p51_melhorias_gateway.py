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
