"""Plano 30 · F5 (WS4) — spoke só chega ao roteador (enforcement em execute).

Hoje a allowlist só vale quando o agente ATUAL é o roteador; um spoke pula toda
validação e transfere pra qualquer agente enabled. O ramo novo: agente atual
não-router (spoke) ⇒ destino permitido = só o roteador (``get_router()``).
Política P4: sem roteador no banco, não bloqueia (degrada pro comportamento
legado — não trava quem não usa hub-and-spoke).

Spoke→spoke é COAGIDO, não recusado (fix 2026-09): a recusa gastava a única
chamada permitida pelo ``call_limit: 1`` de ``ai_engine.hooks`` e a correção que
a própria mensagem de erro pedia era bloqueada — o handoff não acontecia e o
turno morria em silêncio. Aqui o destino é reescrito pro roteador e o pedido
original vai carimbado no ``motivo``.

Rodar: venv/bin/python -m pytest tests/integration/test_spoke_router_enforcement.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.execution import _current_step_agent
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


def test_spoke_para_outro_spoke_e_coagido_pro_roteador(hub):
    """O pedido não é recusado: o DESTINO é reescrito pro hub."""
    ctx, conv = hub
    _set_current(conv, VENDAS)
    r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    assert not r.startswith("Erro"), r
    assert "devolvida ao roteador" in r
    # O handoff FOI persistido — no roteador, não no spoke pedido.
    assert conversation_repo.get(conv["id"])["active_agent_key"] == ROUTER


def test_coercao_carimba_o_pedido_no_motivo(hub):
    """O roteador precisa saber PARA ONDE o spoke queria mandar.

    ``_last_transfer_reason`` lê ``args['motivo']`` da chamada REGISTRADA, não o
    retorno da tool — por isso a escrita é in place no dict recebido.
    """
    ctx, conv = hub
    _set_current(conv, VENDAS)
    args = {"agente": SUPORTE, "motivo": "cliente quer cancelar"}
    transferir_agente.execute(ctx, args)
    assert f"pediu encaminhamento para '{SUPORTE}'" in args["motivo"]
    assert "cliente quer cancelar" in args["motivo"]


def test_coercao_sem_motivo_ainda_registra_o_pedido(hub):
    ctx, conv = hub
    _set_current(conv, VENDAS)
    args = {"agente": SUPORTE}
    transferir_agente.execute(ctx, args)
    assert args["motivo"] == f"Vendas pediu encaminhamento para '{SUPORTE}'"


def test_sem_roteador_nao_bloqueia_spoke(hub):
    """P4: get_router()==None → degrada pro comportamento atual (sem trava)."""
    ctx, conv = hub
    _save_agent(ROUTER, is_router=False, routing_targets=[VENDAS, SUPORTE])
    assert agent_repo.get_router() is None
    _set_current(conv, VENDAS)
    r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    assert r.startswith("Transferência registrada"), r


def test_roteador_desabilitado_nao_trava_spoke(hub):
    """Review plano 30: get_router() não filtra enabled — um roteador
    DESABILITADO não pode receber a conversa (o check de enabled rejeita), e
    sem este fix o spoke ficava em deadlock (bloqueado pra todos os destinos).
    Roteador desabilitado ⇒ degrada pro P4 (sem trava)."""
    ctx, conv = hub
    _save_agent(ROUTER, is_router=True, routing_targets=[VENDAS, SUPORTE],
                enabled=False)
    _set_current(conv, VENDAS)
    r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    assert r.startswith("Transferência registrada"), r


def test_conversa_sem_agente_ativo_segue_legado(hub):
    """Sem active_agent_key E sem hop em execução — nada para classificar."""
    ctx, conv = hub
    _set_current(conv, None)
    r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    assert r.startswith("Transferência registrada"), r


def test_hop_em_execucao_classifica_conversa_sem_agente(hub):
    """O FURO que a linha da conversa deixava: ``active_agent_key`` NULL.

    Uma conversa que nunca carimbou agente (nasceu com a IA off, ou caiu no
    agente padrão) respondia por um spoke sem estar vinculada a ele — e a
    validação de papel era pulada inteira. O ContextVar do hop sabe quem
    executa.
    """
    ctx, conv = hub
    _set_current(conv, None)
    token = _current_step_agent.set(VENDAS)
    try:
        r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    finally:
        _current_step_agent.reset(token)
    assert "devolvida ao roteador" in r, r
    assert conversation_repo.get(conv["id"])["active_agent_key"] == ROUTER


def test_hop_em_execucao_vence_a_linha_da_conversa(hub):
    """Divergiram? Quem manda é quem está executando ESTE hop.

    Num turno multi-agente a conversa já pode estar gravada no destino do hop
    anterior enquanto outro agente ainda executa.
    """
    ctx, conv = hub
    _set_current(conv, ROUTER)
    token = _current_step_agent.set(VENDAS)
    try:
        r = transferir_agente.execute(ctx, {"agente": SUPORTE})
    finally:
        _current_step_agent.reset(token)
    assert "devolvida ao roteador" in r, r
    assert conversation_repo.get(conv["id"])["active_agent_key"] == ROUTER
