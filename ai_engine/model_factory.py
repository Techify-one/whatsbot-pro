"""Model parameter resolution for the config-in-DB engine (plano 06).

Pure (no agno/openai import) so it is unit-testable. ``build_kwargs`` resolves
the model id + tuning params from a 3-level cascade and returns a plain dict the
caller hands to ``OpenAILike(**kwargs)``.

Multi-provider: the ``model`` id is passed through as-is — the Techify proxy is
OpenRouter-compatible, so ``"openai/gpt-..."`` / ``"google/..."`` /
``"anthropic/..."`` route by prefix without provider-specific clients.

Tuning cascade (REF §3), highest precedence first:
  1. ``model_config[param]``                  (per-agent, from ai_agents.model_config)
  2. ``variables["{param}_{agent_key}"]``     (per-agent override in ai_variables)
  3. ``variables[param]``                     (global default in ai_variables)

Aliases are normalised: ``max_output_tokens``/``max_completion_tokens`` →
``max_tokens``; ``thinking_level`` → ``reasoning_effort``.
"""

from __future__ import annotations

_TUNING_KEYS = ("temperature", "top_p", "max_tokens", "reasoning_effort")
_ALIASES = {
    "max_output_tokens": "max_tokens",
    "max_completion_tokens": "max_tokens",
    "thinking_level": "reasoning_effort",
}
_FLOAT_KEYS = ("temperature", "top_p")
_INT_KEYS = ("max_tokens",)


def _normalize(mc: dict | None) -> dict:
    out = dict(mc or {})
    for alias, canon in _ALIASES.items():
        if out.get(alias) is not None and out.get(canon) is None:
            out[canon] = out[alias]
    return out


def resolve_params(model_config: dict | None, variables: dict | None = None,
                   agent_key: str | None = None) -> dict:
    """Resolve each tuning key by the cascade. Returns only the keys that resolved."""
    mc = _normalize(model_config)
    variables = variables or {}
    resolved: dict = {}
    for key in _TUNING_KEYS:
        if mc.get(key) is not None:
            resolved[key] = mc[key]
        elif agent_key and variables.get(f"{key}_{agent_key}") not in (None, ""):
            resolved[key] = variables[f"{key}_{agent_key}"]
        elif variables.get(key) not in (None, ""):
            resolved[key] = variables[key]
    return resolved


def build_kwargs(model_config: dict | None, *, fallback_model: str | None,
                 default_max_tokens: int, variables: dict | None = None,
                 agent_key: str | None = None) -> dict:
    """Build the ``OpenAILike`` kwargs (id + coerced tuning params).

    Values from ``ai_variables`` arrive as strings; they are coerced to the right
    type (a bad value is skipped rather than raising — never break the run).
    """
    mc = _normalize(model_config)
    agent_key = agent_key or mc.get("_agent_key")
    params = resolve_params(mc, variables, agent_key)

    kwargs: dict = {"id": mc.get("model") or fallback_model}
    for key in _FLOAT_KEYS:
        if key in params:
            try:
                kwargs[key] = float(params[key])
            except (TypeError, ValueError):
                pass
    try:
        kwargs["max_tokens"] = int(params.get("max_tokens") or default_max_tokens)
    except (TypeError, ValueError):
        kwargs["max_tokens"] = default_max_tokens
    if params.get("reasoning_effort"):
        kwargs["reasoning_effort"] = str(params["reasoning_effort"])
    return kwargs
