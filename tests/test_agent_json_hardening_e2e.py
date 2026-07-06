"""E2E do hardening de JSON (plano 34 F4): um turno de IA sobre uma linha
``ai_agents`` **suja** (JSON duplo-codificado) ou o agente default **desativado**
ENVIA uma resposta (degradada) em vez de emitir o card de erro painel-only
``role='error'`` — o apagão do achado E.A.

Roda um turno REAL do handler com o AGNO stubado no mesmo ponto que a
caracterização (``agno_engine.build_runner``), então ``build_for_contact`` executa
de verdade. Sem rede.

    venv/bin/python -m pytest tests/test_agent_json_hardening_e2e.py -q
"""

import json

from sqlalchemy import update

from tests.characterization.test_agent_turn_characterization import (
    _patch_agno_turn, _run_turn, _project_messages,
)


def _restore_clean_default():
    from db.repositories import agent_repo
    from agent import agent_factory
    from ai_engine import dynamic_registry
    agent_repo.save(
        agent_repo.DEFAULT_AGENT_KEY, display_name="Agente padrão",
        prompt=agent_factory.DEFAULT_SYSTEM_PROMPT,
        model_config={"model": agent_factory.DEFAULT_MODEL},
        tool_names=None, enabled=True,
    )
    dynamic_registry.invalidate()


def _raw_update_default(**values):
    from db.engine import get_engine
    from db.tables import ai_agents
    from db.repositories import agent_repo
    from ai_engine import dynamic_registry
    with get_engine().begin() as conn:
        conn.execute(update(ai_agents)
                     .where(ai_agents.c.agent_key == agent_repo.DEFAULT_AGENT_KEY)
                     .values(**values))
    dynamic_registry.invalidate()


def _assert_replied_without_error_card(result, phone, expected_reply):
    assert result.reply == expected_reply, result.reply
    msgs = _project_messages(phone)
    roles = [m["role"] for m in msgs]
    assert "error" not in roles, f"card de erro emitido indevidamente: {roles}"
    assert any(m["role"] == "assistant" and m["content"] == expected_reply
               for m in msgs), msgs


def test_dirty_model_config_still_replies(build_app):
    """Linha default com ``model_config`` DUPLO-codificado → o turno responde."""
    phone = "5511976000340"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    # A corrupção do apagão: uma string JSON dentro de outra na coluna TEXT.
    _raw_update_default(model_config=json.dumps(json.dumps({"model": "x/y"})))
    try:
        with _patch_agno_turn(tool_name="save_contact_info",
                              tool_args={"name": "Zé"},
                              final_reply="resposta degradada A"):
            result = _run_turn(built, phone, "oi")
    finally:
        _restore_clean_default()
    _assert_replied_without_error_card(result, phone, "resposta degradada A")


def test_disabled_default_agent_replies_via_floor(build_app):
    """Agente default DESATIVADO (banco inconsistente) → piso de emergência
    responde em vez de levantar AgentResolutionError / gravar card de erro."""
    phone = "5511976000341"
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    _raw_update_default(enabled=0)
    try:
        with _patch_agno_turn(tool_name="save_contact_info",
                              tool_args={"name": "Ana"},
                              final_reply="resposta degradada B"):
            result = _run_turn(built, phone, "oi")
    finally:
        _restore_clean_default()
    _assert_replied_without_error_card(result, phone, "resposta degradada B")
