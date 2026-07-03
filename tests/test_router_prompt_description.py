"""Plano 30 · F6 (WS5) — descrições dos agentes no system prompt do roteador.

``build_for_contact`` injeta, quando o agente resolvido é ``is_router``, a
seção "Agentes disponíveis para transferência" com uma linha por destino:
``display_name (agent_key) — description``. A fonte da lista é
``transferir_agente.router_destinations`` — a MESMA allowlist que ``execute``
aceita (F5), então o prompt nunca induz um destino barrado. Falha na montagem
da seção não derruba o turno (try/except).

Rodar: venv/bin/python -m pytest tests/test_router_prompt_description.py -q
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import agent_factory
from agent.tools import transferir_agente
from db.repositories import agent_repo, contact_repo, conversation_repo

ROUTER = "p30f6_roteador"
VENDAS = "p30f6_vendas"
SUPORTE = "p30f6_suporte"
DESLIGADO = "p30f6_desligado"
PHONE = "5511930000061"

SECTION = "--- Agentes disponíveis para transferência ---"


def _save_agent(key: str, *, display_name: str = "", is_router: bool = False,
                routing_targets: list[str] | None = None,
                enabled: bool = True, description: str = "") -> None:
    agent_repo.save(
        key,
        display_name=display_name or key,
        prompt=f"Prompt inline de {key}.",
        model_config={},
        tool_names=None,
        enabled=enabled,
        description=description,
        is_router=is_router,
        routing_targets=routing_targets,
        hooks_config={},
    )


@pytest.fixture
def world(_engine_ready):
    from ai_engine import dynamic_registry

    agent_factory.seed_default_agent()
    _save_agent(ROUTER, display_name="Roteador F6", is_router=True)
    _save_agent(VENDAS, display_name="Vendas F6", description="Vendas e planos")
    _save_agent(SUPORTE, display_name="Suporte F6",
                description="Suporte técnico de instalação")
    _save_agent(DESLIGADO, enabled=False, description="não deve aparecer")
    dynamic_registry.invalidate()

    c = contact_repo.get_or_create(PHONE)
    conv, _ = conversation_repo.resolve_for_contact_ex(c["id"], PHONE)
    contact = SimpleNamespace(id=c["id"], phone=PHONE, is_group=False)
    yield contact, conv
    conversation_repo.set_agent(conv["id"], None)
    for key in (ROUTER, VENDAS, SUPORTE, DESLIGADO):
        agent_repo.delete(key)
    dynamic_registry.invalidate()


def _bind_and_build(contact, conv, agent_key):
    from ai_engine import dynamic_registry
    conversation_repo.set_agent(conv["id"], agent_key)
    dynamic_registry.invalidate()
    return agent_factory.build_for_contact(None, contact)


def test_prompt_do_roteador_lista_destinos_com_descricao(world):
    contact, conv = world
    spec = _bind_and_build(contact, conv, ROUTER)
    assert SECTION in spec.base_prompt
    assert f"Vendas F6 ({VENDAS}) — Vendas e planos" in spec.base_prompt
    assert f"Suporte F6 ({SUPORTE}) — Suporte técnico de instalação" in spec.base_prompt
    # Agente desligado e o próprio roteador ficam de fora.
    assert DESLIGADO not in spec.base_prompt
    assert f"({ROUTER})" not in spec.base_prompt
    # O prompt inline do agente continua na frente da seção (aditiva).
    assert spec.base_prompt.startswith(f"Prompt inline de {ROUTER}.")


def test_lista_do_prompt_igual_allowlist_do_execute(world):
    """F5×F6: todo destino listado no prompt é aceito por execute()."""
    contact, conv = world
    router = agent_repo.get(ROUTER)
    listed = [a["agent_key"] for a in transferir_agente.router_destinations(router)]
    assert listed  # sanidade

    ctx = SimpleNamespace(contact=contact)
    for key in listed:
        conversation_repo.set_agent(conv["id"], ROUTER)
        r = transferir_agente.execute(ctx, {"agente": key})
        assert r.startswith("Transferência registrada"), (key, r)


def test_routing_targets_restringe_a_secao(world):
    contact, conv = world
    _save_agent(ROUTER, display_name="Roteador F6", is_router=True,
                routing_targets=[VENDAS])
    spec = _bind_and_build(contact, conv, ROUTER)
    assert f"({VENDAS})" in spec.base_prompt
    assert f"({SUPORTE})" not in spec.base_prompt


def test_agente_nao_router_nao_ganha_secao(world):
    contact, conv = world
    spec = _bind_and_build(contact, conv, VENDAS)
    assert SECTION not in spec.base_prompt


def test_falha_na_secao_nao_derruba_o_turno(world):
    contact, conv = world
    with patch.object(agent_factory, "_router_destinations_section",
                      side_effect=RuntimeError("boom")):
        spec = _bind_and_build(contact, conv, ROUTER)
    assert spec is not None and spec.agent_key == ROUTER
    assert SECTION not in spec.base_prompt


def test_roteador_sem_destinos_nao_injeta_secao_vazia(world):
    contact, conv = world
    for key in (VENDAS, SUPORTE):
        _save_agent(key, enabled=False)
    # O agente default continua enabled — restringe a allowlist a um alvo
    # inexistente pra zerar os destinos.
    _save_agent(ROUTER, display_name="Roteador F6", is_router=True,
                routing_targets=["nao_existe"])
    spec = _bind_and_build(contact, conv, ROUTER)
    assert SECTION not in spec.base_prompt
