"""Tool: route the scoped conversation through an explicitly allowed team."""

from __future__ import annotations

import logging
import time

from agent.execution import get_current_step_agent
from agent.handoff import record_team_handoff_outcome
from app.services.team_routing_service import route_to_team_sync
from db.repositories import agent_repo, conversation_repo, team_repo, user_repo
from domain.team_routing import TeamRoutingError

logger = logging.getLogger(__name__)


TRANSFER_TO_TEAM_TOOL = {
    "type": "function",
    "display_label": "Encaminhar para time",
    "function": {
        "name": "transfer_to_team",
        "description": (
            "Encaminha esta conversa para um time autorizado e aplica a estratégia "
            "de distribuição configurada nele. Use somente um team_id publicado "
            "na seção de times disponíveis do seu prompt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {
                    "type": "integer",
                    "description": "ID exato do time de destino.",
                },
                "reason": {
                    "type": "string",
                    "description": "Motivo curto do encaminhamento.",
                },
            },
            "required": ["team_id", "reason"],
        },
    },
}


def team_destinations(agent: dict | None) -> list[dict]:
    """Single source for prompt and executor: active ∩ AI-opt-in ∩ allowlist."""
    allowed = (agent or {}).get("routing_team_ids") or []
    try:
        allowed_ids = {int(team_id) for team_id in allowed}
    except (TypeError, ValueError):
        return []
    if not allowed_ids:
        return []
    return [
        team for team in team_repo.list_all(include_inactive=False)
        if team.get("ai_assignable") and int(team["id"]) in allowed_ids
    ]


def _event_payload(conv: dict, result) -> dict:
    return {
        "conversation_id": conv.get("id"),
        "display_id": conv.get("display_id"),
        "contact_id": conv.get("contact_id"),
        "status": conv.get("status"),
        "assignee_user_id": conv.get("assignee_user_id"),
        "team_id": conv.get("team_id"),
        "active_agent_key": conv.get("active_agent_key"),
        "ai_active": conv.get("ai_active"),
        "is_archived": conv.get("is_archived"),
        "inbox_id": conv.get("inbox_id"),
        "routing_strategy": result.strategy,
        "routing_fallback_reason": result.fallback_reason,
        "ts": time.time(),
    }


def _emit_post_commit(conv: dict, result) -> None:
    """Publish the same dimensions as operator routing, after core commit."""
    try:
        from plugins.events import emit_with_filter_sync

        payload = _event_payload(conv, result)
        emit_with_filter_sync("conversation.team_assigned", {
            **payload, "previous_team_id": result.before.team_id})
        ownership_changed = (
            result.before.assignee_user_id != result.after.assignee_user_id
            or result.before.active_agent_key != result.after.active_agent_key
        )
        if ownership_changed:
            event = ("conversation.assigned"
                     if (result.after.assignee_user_id is not None
                         or result.after.active_agent_key is not None)
                     else "conversation.unassigned")
            emit_with_filter_sync(event, payload)
        if result.before.ai_active != result.after.ai_active:
            emit_with_filter_sync("conversation.ai_toggled", payload)
    except Exception:
        logger.debug("transfer_to_team: emissão pós-commit falhou", exc_info=True)


def execute(ctx, args: dict) -> str:
    """Validate the current hop's allowlist and route one scoped conversation."""
    caller_key = get_current_step_agent()
    caller = agent_repo.get(caller_key) if caller_key else None
    if not caller or not caller.get("enabled"):
        return "Erro: não foi possível identificar o agente que pediu o encaminhamento."

    raw_team_id = args.get("team_id")
    if isinstance(raw_team_id, bool):
        return "Erro: informe um team_id válido."
    try:
        team_id = int(raw_team_id)
    except (TypeError, ValueError):
        return "Erro: informe um team_id válido."

    destinations = team_destinations(caller)
    target = next((team for team in destinations if int(team["id"]) == team_id), None)
    if target is None:
        available = ", ".join(
            f"{team['id']} ({team['name']})" for team in destinations) or "(nenhum)"
        return (f"Erro: o time {team_id} não está autorizado para este agente. "
                f"Times disponíveis: {available}.")

    conv = conversation_repo.get_open_for_contact_scoped(ctx.contact)
    if not conv:
        return "Erro: não há conversa aberta neste canal para encaminhar."
    try:
        result = route_to_team_sync(conv["id"], team_id)
    except TeamRoutingError as exc:
        return f"Erro ao encaminhar para o time: {exc}."
    except Exception:
        logger.exception("transfer_to_team falhou para conversa %s", conv.get("id"))
        return "Erro ao encaminhar a conversa para o time."

    updated = conversation_repo.get(conv["id"]) or conv
    _emit_post_commit(updated, result)
    terminal = result.after.active_agent_key is None
    # The engine consumes this metadata into the executed-call result. It never
    # mutates or persists the model-provided arguments as if the model sent it.
    record_team_handoff_outcome(terminal)

    destination = target.get("name") or f"time {team_id}"
    if result.after.assignee_user_id is not None:
        user = user_repo.get(result.after.assignee_user_id) or {}
        person = user.get("name") or user.get("email") or str(result.after.assignee_user_id)
        outcome = f"distribuída para {person}"
    elif result.after.active_agent_key:
        agent = agent_repo.get(result.after.active_agent_key) or {}
        person = agent.get("display_name") or result.after.active_agent_key
        outcome = f"entregue ao agente de IA {person}"
    else:
        outcome = "colocada na fila segura, com a IA pausada"
    fallback = (f" Fallback aplicado: {result.fallback_reason}."
                if result.fallback_reason else "")
    if terminal:
        instruction = " Confirme brevemente ao cliente e encerre sua atuação neste turno."
    else:
        instruction = " O agente de destino continuará o atendimento neste mesmo turno."
    return (f"Encaminhamento para {destination} concluído: conversa {outcome}."
            f"{fallback}{instruction}")
