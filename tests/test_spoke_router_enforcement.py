"""Plano 30 · F5 (WS4) — spoke só devolve pro roteador (enforcement em execute).

Hoje a allowlist só vale quando o agente ATUAL é o roteador; um spoke pula toda
validação e transfere pra qualquer agente enabled. O ramo novo: agente atual
não-router (spoke) ⇒ destino permitido = só o roteador (``get_router()``);
mensagem de bloqueio cita a rota de escape. Política P4: sem roteador no banco,
não bloqueia (degrada pro comportamento legado — não trava quem não usa
hub-and-spoke).

Rodar: venv/bin/python -m pytest tests/test_spoke_router_enforcement.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.tools import transferir_agente
from db.repositories import agent_repo, contact_repo, conversation_repo

ROUTER = "p30_roteador"
VENDAS = "p30_vendas"
SUPORTE = "p30_suporte"
FORA = "p30_fora_da_allowlist"
PHONE = "5511930000051"


def _save_agent(key: str, *, is_router: bool = False,
                routing_targets: list[str] | None = None,
                enabled: bool = True, description: str = "") -> None:
    agent_repo.save(
        key,
        display_name=key.replace("p30_", "").capitalize(),
        prompt="prompt de teste",
        model_config={},
        tool_names=None,
        enabled=enabled,
        description=description,
        is_router=is_router,
        routing_targets=routing_targets,
        hooks_config={},
    )


@pytest.fixture
def hub(_engine_ready):
    """Hub-and-spoke de teste: 1 roteador (allowlist vendas/suporte) + 3 agentes."""
    _save_agent(ROUTER, is_router=True, routing_targets=[VENDAS, SUPORTE])
    _save_agent(VENDAS, description="Vendas e planos")
    _save_agent(SUPORTE, description="Suporte técnico")
    _save_agent(FORA)
    contact = contact_repo.get_or_create(PHONE)
    conv, _ = conversation_repo.resolve_for_contact_ex(contact["id"], PHONE)
    ctx = SimpleNamespace(contact=SimpleNamespace(id=contact["id"], phone=PHONE))
    yield ctx, conv
    conversation_repo.set_agent(conv["id"], None)
    for key in (ROUTER, VENDAS, SUPORTE, FORA):
        agent_repo.delete(key)


def _set_current(conv: dict, agent_key: str | None) -> None:
    conversation_repo.set_agent(conv["id"], agent_key)


def test_router_para_allowlist_continua_ok(hub):
    ctx, conv = hub
    _set_current(conv, ROUTER)
    r = transferir_agente.execute(ctx, {"agente": VENDAS})
    assert r.startswith("Transferência registrada"), r
    assert conversation_repo.get(conv["id"])["active_agent_key"] == VENDAS


def test_router_fora_da_allowlist_continua_bloqueado(hub):
    ctx, conv = hub
    _set_current(conv, ROUTER)
    r = transferir_agente.execute(ctx, {"agente": FORA})
    assert r.startswith("Erro"), r
    assert "destinos permitidos" in r
    assert conversation_repo.get(conv["id"])["active_agent_key"] == ROUTER


def test_spoke_devolve_pro_roteador_ok(hub):
    ctx, conv = hub
    _set_current(conv, VENDAS)
    r = transferir_agente.execute(ctx, {"agente": ROUTER, "motivo": "fora do escopo"})
    assert r.startswith("Transferência registrada"), r
    assert conversation_repo.get(conv["id"])["active_agent_key"] == ROUTER


def test_spoke_para_outro_spoke_bloqueado(hub):
    ctx, conv = hub
    _set_current(conv, VENDAS)
    r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    assert r.startswith("Erro"), r
    # A mensagem orienta a rota de escape: devolver ao roteador.
    assert ROUTER in r and "transferir_agente" in r
    # O handoff NÃO foi persistido.
    assert conversation_repo.get(conv["id"])["active_agent_key"] == VENDAS


def test_sem_roteador_nao_bloqueia_spoke(hub):
    """P4: get_router()==None → degrada pro comportamento atual (sem trava)."""
    ctx, conv = hub
    _save_agent(ROUTER, is_router=False, routing_targets=[VENDAS, SUPORTE])
    assert agent_repo.get_router() is None
    _set_current(conv, VENDAS)
    r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    assert r.startswith("Transferência registrada"), r


def test_conversa_sem_agente_ativo_segue_legado(hub):
    """Sem active_agent_key não dá pra classificar o atual — sem enforcement."""
    ctx, conv = hub
    _set_current(conv, None)
    r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    assert r.startswith("Transferência registrada"), r
