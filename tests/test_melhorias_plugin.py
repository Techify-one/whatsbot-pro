"""Testes do plugin ``melhorias`` (fluxo de aprovação de sugestões de melhoria).

Vive em ``tests/`` (como o antigo ``test_improve_conversation_scope.py``) para
usar as fixtures ``plugin_app``/``build_app`` (engine + teardown) e rodar via
``pytest tests/``. Cobrem: geração NA APROVAÇÃO com o gerador STUB, filtros do
painel, gating de status/RBAC, e um unit do ``DirectApiGenerator`` (patch em
``handler._get_client``) portando a caracterização do antigo ``generate_improvement``.
"""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace
from unittest.mock import patch

_STUB = {"plugin.melhorias.generator_backend": "stub"}


def _seed_ai_reply(handler, phone, content="resposta marcada"):
    contact = handler._get_contact(phone)
    contact.add_message("user", "preciso de ajuda")
    saved = contact.add_message("assistant", content)
    return contact, saved


def test_create_lists_pendente_and_posts_notice(plugin_app):
    """Solicitar → linha pendente (sem análise) + aviso de sistema com link na conversa."""
    built = plugin_app("melhorias", settings_overrides=_STUB)
    handler = built.agent_handler
    phone = "5511960000001"
    contact, saved = _seed_ai_reply(handler, phone)

    r = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone,
        "message": {"content": "resposta marcada", "ts": saved["ts"], "_id": saved["id"]},
        "feedback": "saiu errado",
        "conversation_id": saved["conversation_id"],
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "pendente"
    assert data["analysis"] == ""
    assert data["feedback"] == "saiu errado"
    assert data["message_content"] == "resposta marcada"
    assert data["conversation_url"] and f"message={saved['id']}" in data["conversation_url"]
    assert data["panel_url"] and f"detail={data['id']}" in data["panel_url"]

    # Aviso painel-only role=system na conversa, com a URL do painel.
    from db.repositories import message_repo
    msgs = message_repo.get_all(contact.id)
    notices = [m for m in msgs if m.get("role") == "system"
               and "painel de aprovação" in (m.get("content") or "")]
    assert notices, "esperava um aviso de sistema com link ao painel"
    assert f"/melhorias?detail={data['id']}" in notices[-1]["content"]


def test_list_filters(plugin_app):
    """GET /suggestions filtra por status / q."""
    built = plugin_app("melhorias", settings_overrides=_STUB)
    handler = built.agent_handler
    phone = "5511960000002"
    _, saved = _seed_ai_reply(handler, phone, content="conteudo-unico-xyz")
    built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone, "message": {"content": "conteudo-unico-xyz", "ts": saved["ts"], "_id": saved["id"]},
        "feedback": "", "conversation_id": saved["conversation_id"]})

    r = built.client.get("/api/plugins/melhorias/suggestions?status=pendente")
    assert r.status_code == 200
    assert all(s["status"] == "pendente" for s in r.json()["data"])
    assert any(s["message_content"] == "conteudo-unico-xyz" for s in r.json()["data"])

    r = built.client.get("/api/plugins/melhorias/suggestions?status=aprovada")
    assert all(s["message_content"] != "conteudo-unico-xyz" for s in r.json()["data"])

    r = built.client.get("/api/plugins/melhorias/suggestions?q=unico-xyz")
    assert any(s["message_content"] == "conteudo-unico-xyz" for s in r.json()["data"])


def test_approve_generates_analysis_reject_does_not(plugin_app):
    """Aprovar gera a análise (stub) + marca aprovada; recusar só marca; 409 se já decidida."""
    built = plugin_app("melhorias", settings_overrides=_STUB)
    handler = built.agent_handler
    phone = "5511960000003"
    _, saved = _seed_ai_reply(handler, phone)
    sid = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone, "message": {"content": "resposta marcada", "ts": saved["ts"], "_id": saved["id"]},
        "feedback": "tom errado", "conversation_id": saved["conversation_id"]}).json()["data"]["id"]

    r = built.client.post(f"/api/plugins/melhorias/suggestions/{sid}/approve")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "aprovada"
    assert "[stub]" in d["analysis"]
    assert d["decided_at"] is not None
    assert d["model"] == "stub-model"

    # aprovar de novo → 409
    r2 = built.client.post(f"/api/plugins/melhorias/suggestions/{sid}/approve")
    assert r2.status_code == 409

    # outra sugestão → recusar (sem análise)
    phone2 = "5511960000004"
    _, s2 = _seed_ai_reply(handler, phone2)
    sid2 = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone2, "message": {"content": "resposta marcada", "ts": s2["ts"], "_id": s2["id"]},
        "feedback": "", "conversation_id": s2["conversation_id"]}).json()["data"]["id"]
    r3 = built.client.post(f"/api/plugins/melhorias/suggestions/{sid2}/reject")
    assert r3.status_code == 200
    d3 = r3.json()["data"]
    assert d3["status"] == "recusada"
    assert d3["analysis"] == ""
    assert d3["decided_at"] is not None


def test_contact_rename_refreshes_snapshot(plugin_app):
    """Renomear o contato re-sincroniza o snapshot contact_name de TODAS as suas
    sugestões (exibição + busca passam a refletir o nome atual). Idempotente."""
    built = plugin_app("melhorias", settings_overrides=_STUB)
    logic = importlib.import_module("whatsbot_plugins.melhorias.logic")
    handler = built.agent_handler
    phone = "5511960000007"
    contact, saved = _seed_ai_reply(handler, phone)
    contact.set_info_fields({"name": "Nome Antigo"})

    sid = built.client.post("/api/plugins/melhorias/suggestions", json={
        "phone": phone,
        "message": {"content": "resposta marcada", "ts": saved["ts"], "_id": saved["id"]},
        "feedback": "", "conversation_id": saved["conversation_id"]}).json()["data"]["id"]
    got = built.client.get(f"/api/plugins/melhorias/suggestions/{sid}").json()["data"]
    assert got["contact_name"] == "Nome Antigo"

    # Renomeia o contato e re-sincroniza (simula o handler de contact.updated).
    contact.set_info_fields({"name": "Nome Novo"})
    assert logic.refresh_contact_snapshot(phone) == 1
    got2 = built.client.get(f"/api/plugins/melhorias/suggestions/{sid}").json()["data"]
    assert got2["contact_name"] == "Nome Novo"
    # A busca também passa a achar pelo nome novo.
    found = built.client.get("/api/plugins/melhorias/suggestions?q=Nome Novo").json()["data"]
    assert any(s["id"] == sid for s in found)
    # Idempotente: sem mudança real, não reescreve nada.
    assert logic.refresh_contact_snapshot(phone) == 0


def test_config_get_put(plugin_app):
    """GET/PUT /config lê e grava plugin.melhorias.model/prompt."""
    built = plugin_app("melhorias", settings_overrides=_STUB)
    r = built.client.get("/api/plugins/melhorias/config")
    assert r.status_code == 200
    assert "prompt_default" in r.json()["data"]

    r = built.client.put("/api/plugins/melhorias/config",
                         json={"model": "openai/gpt-4o-mini", "prompt": "meu prompt"})
    assert r.status_code == 200
    assert r.json()["data"]["model"] == "openai/gpt-4o-mini"
    r = built.client.get("/api/plugins/melhorias/config")
    assert r.json()["data"]["prompt"] == "meu prompt"


# ── Unit do gerador direto (porta a caracterização do antigo generate_improvement) ──

def _fake_llm_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class _FakeClient:
    def __init__(self, response):
        self.create_calls = []
        outer = self

        class _C:
            def create(self, **kwargs):
                outer.create_calls.append(kwargs)
                return response
        self.chat = SimpleNamespace(completions=_C())


def test_direct_generator_unit(plugin_app):
    """DirectApiGenerator: patch em _get_client → análise, forma da chamada e usage."""
    built = plugin_app("melhorias", settings_overrides=_STUB)
    gen = importlib.import_module("whatsbot_plugins.melhorias.generation")
    handler = built.agent_handler
    phone = "5511960000005"
    contact = handler._get_contact(phone)
    contact.add_message("user", "qual o preço?")
    contact.add_message("assistant", "não sei dizer")

    analysis_text = "**Diagnóstico**\nok\n\n**Recomendações**\n- item"
    fake = _FakeClient(_fake_llm_response(analysis_text))
    with patch.object(handler, "_get_client", return_value=fake):
        res = gen.DirectApiGenerator().generate(gen.GenContext(
            handler=handler, phone=phone,
            target_message={"content": "não sei dizer", "ts": time.time()},
            feedback="saiu errado"))
    assert res.analysis == analysis_text
    assert len(fake.create_calls) == 1
    call = fake.create_calls[0]
    assert call.get("temperature") == 0.4
    assert call.get("max_tokens") == 8000
    assert [m["role"] for m in call["messages"]] == ["system", "user"]


def test_direct_generator_no_api_key(plugin_app):
    """Sem API key → retorno de fallback, cliente nunca é construído."""
    built = plugin_app("melhorias", settings_overrides=_STUB)
    gen = importlib.import_module("whatsbot_plugins.melhorias.generation")
    handler = built.agent_handler
    saved_key = handler.api_key
    handler.api_key = ""
    try:
        with patch.object(handler, "_get_client",
                          side_effect=AssertionError("client must not be used")):
            res = gen.DirectApiGenerator().generate(gen.GenContext(
                handler=handler, phone="5511960000006",
                target_message={"content": "x", "ts": 0}, feedback=""))
    finally:
        handler.api_key = saved_key
    assert "API key não configurada" in res.analysis
