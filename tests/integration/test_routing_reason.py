"""Plano 29 · Fase A2 — threading do motivo do handoff entre hops.

Integração de ``_continue_routing`` com o AGNO mockado: o agente "comercial"
fez ``transferir_agente(roteador, motivo=...)`` no 1º hop; o hop do roteador
deve RECEBER o motivo no contexto (mensagem sintética ``[REDIRECIONAMENTO de
...]``) e o ``routing_steps`` deve carregar o ``reason``.

    venv/bin/python -m pytest tests/integration/test_routing_reason.py -q
"""

from __future__ import annotations

import asyncio

import pytest

from agent import agno_engine, agent_factory
from agent.agno_engine import EngineResult
from app.services import agent_run_service
from db.repositories import agent_repo, contact_repo, conversation_repo
from ai_engine import dynamic_registry

PHONE = "5511777770042"


class _FakeHandler:
    split_messages = False

    def __init__(self):
        self.dispatched = []

    def _build_system_prompt(self, contact, base_prompt=None, split_messages=None):
        return base_prompt or "prompt"

    def _select_active_tools(self, spec):
        return []

    def _record_usage_tokens(self, *a, **k):
        pass

    def _dispatch_tool(self, contact, name, args):
        self.dispatched.append((name, args))
        return "Transferência realizada."


class _Contact:
    def __init__(self, cid, phone):
        self.id = cid
        self.phone = phone
        self.channel_id = "default"
        self.is_group = False


@pytest.fixture
def routing_world(_engine_ready):
    """Roteador + comercial no DB, contato com conversa aberta no comercial."""
    agent_factory.seed_default_agent()
    agent_repo.save("roteador29", display_name="Roteador",
                    prompt="Você roteia.", model_config={"model": "test/model"},
                    tool_names=None, enabled=True,
                    is_router=True, routing_targets=["comercial29"])
    agent_repo.save("comercial29", display_name="Comercial",
                    prompt="Você vende.", model_config={"model": "test/model"},
                    tool_names=None, enabled=True)
    dynamic_registry.invalidate()

    c = contact_repo.get_or_create(PHONE)
    conversation_repo.resolve_for_contact(
        c["id"], f"{PHONE}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(c["id"])
    yield _Contact(c["id"], PHONE), conv
    conversation_repo.set_agent(conv["id"], None)
    dynamic_registry.invalidate()


def test_hop_recebe_motivo_e_steps_carregam_reason(routing_world, monkeypatch):
    contact, conv = routing_world
    handler = _FakeHandler()

    # Estado: o 1º hop (comercial) JÁ rodou e transferiu pro roteador com motivo.
    conversation_repo.set_agent(conv["id"], "roteador29")
    dynamic_registry.invalidate()
    motivo = "oferta X não existe"
    first_result = EngineResult(
        reply="um momento",
        executed_tools=[{
            "tool": "transferir_agente",
            "args": {"agente": "roteador29", "motivo": motivo},
            "result": "Transferência registrada: ...",
        }],
        usage=None,
    )
    first_spec = agent_factory.AgentSpec(
        agent_key="comercial29", base_prompt="Você vende.",
        model_config={"model": "test/model"})

    captured = []

    async def _fake_run_async(handler, contact, sender, messages, active_tools,
                              model_config=None):
        captured.append(messages)
        return EngineResult(reply="resposta do roteador", executed_tools=[], usage=None)

    monkeypatch.setattr(agno_engine, "run_async", _fake_run_async)

    context_messages = [{"role": "user", "content": "quero a oferta X"}]
    result, combined, usage, steps = asyncio.run(
        agent_run_service._continue_routing(
            handler, contact, PHONE, context_messages, first_spec,
            first_result, first_result.executed_tools, None,
            disable_tools=False))

    assert result.reply == "resposta do roteador"
    # O hop do roteador viu a mensagem sintética com origem + motivo.
    assert len(captured) == 1
    hop_msgs = captured[0]
    synthetic = [m for m in hop_msgs
                 if m["role"] == "user" and "[REDIRECIONAMENTO de" in m["content"]]
    assert len(synthetic) == 1
    assert "comercial29" in synthetic[0]["content"]
    assert motivo in synthetic[0]["content"]
    # O contexto original continua presente, antes da sintética.
    assert hop_msgs.index({"role": "user", "content": "quero a oferta X"}) \
        < hop_msgs.index(synthetic[0])
    # routing_steps carrega o reason (A7).
    assert steps == [{"from": "comercial29", "to": "roteador29",
                      "depth": 1, "reason": motivo}]


def test_revisita_roteador_comercial_roteador_no_mesmo_turno(routing_world, monkeypatch):
    """Plano 29 A3: o cenário-alvo do plano — 3 hops no mesmo turno.

    1º hop (já rodado): roteador transfere pro comercial ("quero oferta X").
    2º hop: comercial descobre que a oferta não existe e DEVOLVE pro roteador.
    3º hop: roteador REVISITADO (is_reinvoke) recebe o motivo e responde.
    """
    contact, conv = routing_world
    handler = _FakeHandler()

    conversation_repo.set_agent(conv["id"], "comercial29")
    first_result = EngineResult(
        reply="vou te passar pro comercial",
        executed_tools=[{
            "tool": "transferir_agente",
            "args": {"agente": "comercial29", "motivo": "cliente quer a oferta X"},
            "result": "Transferência registrada: ...",
        }], usage=None)
    first_spec = agent_factory.AgentSpec(
        agent_key="roteador29", base_prompt="Você roteia.",
        model_config={"model": "test/model"})

    captured = []

    async def _fake_run_async(handler, contact, sender, messages, active_tools,
                              model_config=None):
        captured.append(messages)
        if len(captured) == 1:
            # Hop do comercial: oferta não existe → devolve pro roteador.
            conversation_repo.set_agent(conv["id"], "roteador29")
            return EngineResult(
                reply="", executed_tools=[{
                    "tool": "transferir_agente",
                    "args": {"agente": "roteador29",
                             "motivo": "oferta X não existe"},
                    "result": "Transferência registrada: ...",
                }], usage=None)
        # Hop do roteador revisitado: responde e encerra.
        return EngineResult(reply="a oferta X não está disponível; posso ajudar com Y",
                            executed_tools=[], usage=None)

    monkeypatch.setattr(agno_engine, "run_async", _fake_run_async)

    result, combined, _, steps = asyncio.run(
        agent_run_service._continue_routing(
            handler, contact, PHONE,
            [{"role": "user", "content": "quero a oferta X"}],
            first_spec, first_result, first_result.executed_tools, None,
            disable_tools=False))

    # 3 hops no mesmo turno: roteador→comercial→roteador.
    assert [(s["from"], s["to"]) for s in steps] == [
        ("roteador29", "comercial29"), ("comercial29", "roteador29")]
    assert steps[0]["reason"] == "cliente quer a oferta X"
    assert steps[1]["reason"] == "oferta X não existe"
    assert result.reply.startswith("a oferta X não está disponível")

    # O hop revisitado recebeu a sintética de re-invocação com o motivo.
    revisit_msgs = captured[1]
    synthetic = [m for m in revisit_msgs
                 if m["role"] == "user" and "[REDIRECIONAMENTO de" in m["content"]]
    assert "oferta X não existe" in synthetic[-1]["content"]
    assert "não repita a mesma transferência" in synthetic[-1]["content"]


def test_cap_estourado_escala_pra_humano(routing_world, monkeypatch):
    """Plano 29 A4+A10: cadeia que nunca resolve → transfer_to_human forçado."""
    from db.repositories import config_repo

    contact, conv = routing_world
    handler = _FakeHandler()
    config_repo.set("ai_max_route_depth", 3)
    try:
        conversation_repo.set_agent(conv["id"], "comercial29")
        first_result = EngineResult(
            reply="", executed_tools=[{
                "tool": "transferir_agente",
                "args": {"agente": "comercial29", "motivo": "vai"},
                "result": "Transferência registrada: ...",
            }], usage=None)
        first_spec = agent_factory.AgentSpec(
            agent_key="roteador29", base_prompt="Você roteia.",
            model_config={"model": "test/model"})

        async def _pingpong_run_async(handler, contact, sender, messages,
                                      active_tools, model_config=None):
            cur = conversation_repo.get_open_for_contact(contact.id)["active_agent_key"]
            nxt = "roteador29" if cur == "comercial29" else "comercial29"
            conversation_repo.set_agent(conv["id"], nxt)
            return EngineResult(reply="", executed_tools=[{
                "tool": "transferir_agente",
                "args": {"agente": nxt, "motivo": "volta"},
                "result": "Transferência registrada: ...",
            }], usage=None)

        monkeypatch.setattr(agno_engine, "run_async", _pingpong_run_async)

        result, combined, _, steps = asyncio.run(
            agent_run_service._continue_routing(
                handler, contact, PHONE, [{"role": "user", "content": "oi"}],
                first_spec, first_result, first_result.executed_tools, None,
                disable_tools=False))

        # Cap 3 → 2 hops extras; handoff seguia pendente → escalou pra humano.
        assert len(steps) == 2
        assert handler.dispatched, "transfer_to_human não foi disparado"
        name, args = handler.dispatched[-1]
        assert name == "transfer_to_human"
        assert "Limite de roteamento atingido" in args["reason"]
        forced = [t for t in combined if t.get("forced")]
        assert forced and forced[-1]["tool"] == "transfer_to_human"
    finally:
        config_repo.set("ai_max_route_depth", 5)


def test_handoff_sem_motivo_injeta_sintetica_sem_linha_motivo(routing_world, monkeypatch):
    contact, conv = routing_world
    handler = _FakeHandler()
    conversation_repo.set_agent(conv["id"], "comercial29")
    dynamic_registry.invalidate()
    first_result = EngineResult(
        reply="", executed_tools=[{
            "tool": "transferir_agente",
            "args": {"agente": "comercial29"},  # sem motivo
            "result": "Transferência registrada: ...",
        }], usage=None)
    first_spec = agent_factory.AgentSpec(
        agent_key="roteador29", base_prompt="Você roteia.",
        model_config={"model": "test/model"})

    captured = []

    async def _fake_run_async(handler, contact, sender, messages, active_tools,
                              model_config=None):
        captured.append(messages)
        return EngineResult(reply="ok", executed_tools=[], usage=None)

    monkeypatch.setattr(agno_engine, "run_async", _fake_run_async)

    result, _, _, steps = asyncio.run(
        agent_run_service._continue_routing(
            handler, contact, PHONE, [{"role": "user", "content": "oi"}],
            first_spec, first_result, first_result.executed_tools, None,
            disable_tools=False))

    synthetic = [m for m in captured[0]
                 if m["role"] == "user" and "[REDIRECIONAMENTO de" in m["content"]]
    assert len(synthetic) == 1
    assert "Motivo:" not in synthetic[0]["content"]
    assert steps[0]["reason"] is None
