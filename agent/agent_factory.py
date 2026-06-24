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
    agent_repo, prompt_repo, variable_repo, conversation_repo, inbox_repo,
)
from ai_engine import dynamic_registry

logger = logging.getLogger(__name__)

DEFAULT_AGENT_KEY = agent_repo.DEFAULT_AGENT_KEY
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
    """Create the default prompt + agent rows if absent (idempotent).

    Seeds from the in-code constants (:data:`DEFAULT_SYSTEM_PROMPT` /
    :data:`DEFAULT_MODEL`). Never overwrites existing rows (no version bump), so
    user edits in the DB are preserved across boots. ``settings`` is accepted for
    backwards-compatible call sites but is no longer used.
    """
    try:
        prompt_repo.ensure(DEFAULT_PROMPT_KEY, DEFAULT_SYSTEM_PROMPT)
        agent_repo.ensure(
            DEFAULT_AGENT_KEY,
            display_name="Agente padrão",
            prompt_key=DEFAULT_PROMPT_KEY,
            model_config={"model": DEFAULT_MODEL},
            tool_names=None,  # all registered tools
            enabled=True,
        )
        logger.info("AI engine: default agent/prompt seeded (or already present)")
    except Exception as e:
        logger.warning("AI engine seed failed: %s", e)


def migrate_legacy_config_to_default_agent() -> None:
    """One-time, idempotent migration of legacy ``config`` into the default agent.

    Older installs configured the AI via ``config.system_prompt`` / ``config.model``.
    Those keys are being retired, so — *before* anything stops reading them — copy a
    user's customised values into the canonical default agent. Safe to run on every
    boot: only fills the default prompt when it is still empty/seed, and the default
    model when it is empty or still the seed value, so a real DB edit is never lost.
    """
    try:
        from db.repositories import config_repo

        legacy_prompt = config_repo.get("system_prompt", None)
        if legacy_prompt and legacy_prompt not in _LEGACY_SEED_PROMPTS:
            current_body = (prompt_repo.get(DEFAULT_PROMPT_KEY) or {}).get("body") or ""
            if current_body in _LEGACY_SEED_PROMPTS:
                prompt_repo.save(DEFAULT_PROMPT_KEY, legacy_prompt)
                logger.info("Legacy config.system_prompt migrated → default agent prompt")

        legacy_model = config_repo.get("model", None)
        if legacy_model:
            agent = agent_repo.get(DEFAULT_AGENT_KEY)
            if agent:
                mc = dict(agent.get("model_config") or {})
                if not mc.get("model") or mc.get("model") == DEFAULT_MODEL:
                    if mc.get("model") != legacy_model:
                        mc["model"] = legacy_model
                        agent_repo.save(
                            DEFAULT_AGENT_KEY,
                            display_name=agent.get("display_name") or "Agente padrão",
                            prompt_key=agent.get("prompt_key") or DEFAULT_PROMPT_KEY,
                            model_config=mc,
                            tool_names=agent.get("tool_names"),
                            enabled=bool(agent.get("enabled", True)),
                            description=agent.get("description", ""),
                            is_router=bool(agent.get("is_router", False)),
                            routing_targets=agent.get("routing_targets"),
                            hooks_config=agent.get("hooks_config") or {},
                        )
                        logger.info("Legacy config.model migrated → default agent model")
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

        prompt_row = dynamic_registry.get_prompt(agent.get("prompt_key") or DEFAULT_PROMPT_KEY)
        body = (prompt_row or {}).get("body") or ""
        if not body:
            # No prompt body in the DB — fall back to the seed prompt so the agent
            # never runs with an empty system prompt.
            body = DEFAULT_SYSTEM_PROMPT

        variables = dynamic_registry.variables_map()
        rendered = render_template(body, variables)

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
