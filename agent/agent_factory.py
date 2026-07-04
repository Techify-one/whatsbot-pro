"""Config-in-DB factory feeding the AGNO engine — the single AI engine.

Reads the DB-driven agent definition (``ai_agents`` + ``ai_prompts`` +
``ai_variables``), renders the prompt template, and returns an :class:`AgentSpec`
the handler/engine consume.

There is **no legacy path** anymore: :func:`build_for_contact` always resolves an
:class:`AgentSpec` via a cascade (bound agent → default agent → in-code seed
constants). It only raises :class:`AgentResolutionError` when the database is
genuinely broken — the handler then isolates the failure to that one
conversation (logs + a painel-only error card) without ever messaging the client.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from db.repositories import (
    agent_repo, variable_repo, conversation_repo, inbox_repo,
)
from ai_engine import dynamic_registry

logger = logging.getLogger(__name__)

DEFAULT_AGENT_KEY = agent_repo.DEFAULT_AGENT_KEY
# Legacy: agents used to reference a shared ai_prompts template by key. The prompt
# is now inline on each agent (``ai_agents.prompt``); this constant is retained
# only for back-compat with old call sites/tests.
DEFAULT_PROMPT_KEY = "default"

# Seed constants — used ONLY to plant the default agent/prompt on the very first
# boot and as a last-resort fallback when the DB row carries no prompt/model.
# After the first boot, everything comes from the DB (Agentes/Prompts screens).
DEFAULT_SYSTEM_PROMPT = (
    "Você é um assistente útil e amigável. Responda de forma clara e concisa. "
    "Use português brasileiro."
)
DEFAULT_MODEL = "deepseek/deepseek-v4-pro"

# Older builds seeded one of these prompt bodies. Used by the one-time legacy
# migration to detect an "untouched" default prompt that is safe to overwrite
# with the user's customised ``config.system_prompt``.
_LEGACY_SEED_PROMPTS = {
    "",
    DEFAULT_SYSTEM_PROMPT,
    "Você é um assistente útil.",
}

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class AgentResolutionError(Exception):
    """Raised when no agent can be resolved for a contact (broken DB).

    This is the rare, genuinely-broken case. The handler catches it, logs, writes
    a painel-only error card to the affected conversation, and sends nothing to
    the client — so one broken conversation never takes the whole service down.
    """


@dataclass
class AgentSpec:
    """Resolved, per-request agent configuration from the DB."""
    agent_key: str
    base_prompt: str
    model_config: dict = field(default_factory=dict)
    tool_names: list[str] | None = None  # None = every registered tool

    @property
    def model(self) -> str | None:
        return self.model_config.get("model") or None


def render_template(body: str, variables: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders with values from ``variables``.

    Only tokens that look like ``{identifier}`` AND match a known variable are
    replaced; unknown tokens and literal braces in the prompt text are left
    untouched (so JSON examples / stray braces survive intact).
    """
    if not body or "{" not in body:
        return body or ""

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        return variables[name] if name in variables else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, body)


def seed_default_agent(settings=None) -> None:
    """Create the default agent row if absent (idempotent).

    Seeds from the in-code constants (:data:`DEFAULT_SYSTEM_PROMPT` /
    :data:`DEFAULT_MODEL`) with the prompt stored inline on the agent. Never
    overwrites an existing row (no version bump), so user edits in the DB are
    preserved across boots. ``settings`` is accepted for backwards-compatible call
    sites but is no longer used.
    """
    try:
        agent_repo.ensure(
            DEFAULT_AGENT_KEY,
            display_name="Agente padrão",
            prompt=DEFAULT_SYSTEM_PROMPT,
            model_config={"model": DEFAULT_MODEL},
            tool_names=None,  # all registered tools
            enabled=True,
        )
        logger.info("AI engine: default agent seeded (or already present)")
    except Exception as e:
        logger.warning("AI engine seed failed: %s", e)


def migrate_legacy_config_to_default_agent() -> None:
    """One-time, idempotent migration of legacy ``config`` into the default agent.

    Older installs configured the AI via ``config.system_prompt`` / ``config.model``.
    Those keys are being retired, so — *before* anything stops reading them — copy a
    user's customised values into the canonical default agent's inline prompt/model.
    Safe to run on every boot: only fills the prompt when it is still empty/seed, and
    the model when it is empty or still the seed value, so a real DB edit is never lost.
    """
    try:
        from db.repositories import config_repo

        agent = agent_repo.get(DEFAULT_AGENT_KEY)
        if not agent:
            return

        # Re-save once if either the legacy prompt or model still needs migrating.
        new_prompt = agent.get("prompt") or ""
        mc = dict(agent.get("model_config") or {})
        changed = False

        legacy_prompt = config_repo.get("system_prompt", None)
        if legacy_prompt and legacy_prompt not in _LEGACY_SEED_PROMPTS:
            if (new_prompt or "").strip() in _LEGACY_SEED_PROMPTS:
                new_prompt = legacy_prompt
                changed = True

        legacy_model = config_repo.get("model", None)
        if legacy_model and (not mc.get("model") or mc.get("model") == DEFAULT_MODEL):
            if mc.get("model") != legacy_model:
                mc["model"] = legacy_model
                changed = True

        if changed:
            agent_repo.save(
                DEFAULT_AGENT_KEY,
                display_name=agent.get("display_name") or "Agente padrão",
                prompt=new_prompt,
                model_config=mc,
                tool_names=agent.get("tool_names"),
                enabled=bool(agent.get("enabled", True)),
                description=agent.get("description", ""),
                is_router=bool(agent.get("is_router", False)),
                routing_targets=agent.get("routing_targets"),
                hooks_config=agent.get("hooks_config") or {},
            )
            logger.info("Legacy config (system_prompt/model) migrated → default agent")
    except Exception as e:
        logger.warning("Legacy config migration failed: %s", e)


def resolve_active_agent_key(contact) -> str | None:
    """Which agent should handle this contact's open conversation? (plano 06)

    Precedência: conversation.active_agent_key → inbox.default_agent_key → None
    (None = cair no agente default em :func:`build_for_contact`). Tudo best-effort:
    qualquer falha devolve None e o caller usa o default.
    """
    cid = getattr(contact, "id", None)
    if cid is None:
        return None
    try:
        conv = conversation_repo.get_open_for_contact(cid)
        if not conv:
            return None
        if conv.get("active_agent_key"):
            return conv["active_agent_key"]
        inbox_id = conv.get("inbox_id")
        if inbox_id:
            inbox = inbox_repo.get(inbox_id)
            if inbox and inbox.get("default_agent_key"):
                return inbox["default_agent_key"]
    except Exception as e:  # pragma: no cover - defensivo
        logger.debug("AI engine: resolve_active_agent_key failed (%s)", e)
    return None


def _resolve_active_agent(contact) -> dict | None:
    """Resolve the bound agent row, falling back to the default if absent/disabled."""
    key = resolve_active_agent_key(contact)
    if key:
        agent = dynamic_registry.get_agent(key)
        if agent and agent.get("enabled"):
            return agent
        # Bound agent missing/disabled → não trava o atendimento, usa o default.
        logger.debug("AI engine: agente vinculado %r ausente/desativado; usando default", key)
    return dynamic_registry.get_default_agent()


def _transfer_tool_available(handler, agent: dict) -> bool:
    """``transferir_agente`` está acionável por ESTE agente neste turno?

    Falso quando o ``tool_names`` do agente exclui a tool, ou quando ela não
    está ativa no registry (nasce OFF no plano 30 F3, pode ter sido desativada
    ou deletada). ``handler`` ausente (chamadas antigas/testes) ou falha de
    introspecção assumem disponível (comportamento anterior).
    """
    tool_names = agent.get("tool_names")
    if tool_names is not None and "transferir_agente" not in tool_names:
        return False
    if handler is None:
        return True
    try:
        return bool(handler.is_tool_active("transferir_agente"))
    except Exception:  # noqa: BLE001
        return True


def _router_destinations_section(router: dict) -> str:
    """Seção "Agentes disponíveis para transferência" do prompt do roteador.

    Uma linha por destino: ``display_name (agent_key) — description``. Retorna
    string vazia quando não há destino (roteador sem spokes enabled).
    """
    from agent.tools.transferir_agente import router_destinations

    destinations = router_destinations(router)
    if not destinations:
        return ""
    lines = []
    for a in destinations:
        label = a.get("display_name") or a["agent_key"]
        line = f"- {label} ({a['agent_key']})"
        description = (a.get("description") or "").strip()
        if description:
            line += f" — {description}"
        lines.append(line)
    return (
        "\n\n--- Agentes disponíveis para transferência ---\n"
        "Quando o assunto do contato corresponder à especialidade de um destes "
        "agentes, chame a tool transferir_agente informando o agent_key exato "
        "(entre parênteses) e um motivo curto:\n"
        + "\n".join(lines)
        + "\n--- Fim dos agentes disponíveis ---"
    )


def build_for_contact(handler, contact) -> AgentSpec:
    """Resolve the DB-driven agent for a request. Always returns an ``AgentSpec``.

    Cascade: bound agent (conversation→inbox) → default agent → in-code seed
    constants for any missing prompt/model. Raises :class:`AgentResolutionError`
    only when nothing resolves (the default agent itself is missing/disabled, i.e.
    a genuinely broken DB) — the handler isolates that to one conversation.
    """
    try:
        agent = _resolve_active_agent(contact)
        if not agent or not agent.get("enabled"):
            raise AgentResolutionError(
                "agente default ausente ou desativado (banco inconsistente)"
            )

        body = agent.get("prompt") or ""
        if not body:
            # No inline prompt on the agent — fall back to the seed prompt so the
            # agent never runs with an empty system prompt.
            body = DEFAULT_SYSTEM_PROMPT

        variables = dynamic_registry.variables_map()
        rendered = render_template(body, variables)

        # Plano 30 F6 (WS5): o roteador recebe no system prompt a lista de
        # destinos com as descrições dos agentes (ai_agents.description) — a
        # MESMA allowlist que transferir_agente.execute aceita (fonte única em
        # router_destinations, F5). Seção ADITIVA ao prompt inline (se o humano
        # já listou agentes à mão, as duas coexistem — sem dedupe). Só injeta
        # quando transferir_agente está de fato acionável pelo agente (a tool
        # nasce OFF no F3 e pode estar desabilitada/deletada/fora do
        # tool_names — anunciar destinos sem a tool induz alucinação).
        # Defensivo: falha aqui nunca derruba o turno.
        if agent.get("is_router") and _transfer_tool_available(handler, agent):
            try:
                rendered += _router_destinations_section(agent)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "AI engine: seção de destinos do roteador falhou (%s)", e)

        model_config = dict(agent.get("model_config") or {})
        if not model_config.get("model"):
            model_config["model"] = DEFAULT_MODEL
        # Let the model factory resolve per-agent tuning vars ({param}_{agent_key}).
        model_config["_agent_key"] = agent["agent_key"]
        # Declarative tool hooks (call_limit/requires_prior_call) enforced in the
        # AGNO entrypoint. Carried on model_config; the engine strips it out.
        model_config["_hooks_config"] = agent.get("hooks_config") or {}

        return AgentSpec(
            agent_key=agent["agent_key"],
            base_prompt=rendered,
            model_config=model_config,
            tool_names=agent.get("tool_names"),
        )
    except AgentResolutionError:
        raise
    except Exception as e:
        logger.error("AI engine: build_for_contact failed (%s)", e)
        raise AgentResolutionError(str(e)) from e
