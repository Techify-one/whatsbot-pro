"""LLM tool registry — registration, override resolution and generic dispatch.

Extracted from ``agent.handler`` (Plano 23 · Fase B5, master §2.3). Owns the
WhatsBot tool registry that feeds the AGNO engine:

* registration (core ``CORE_TOOLS``, plugin tools, code-in-DB ``ai_tools``),
  with the collision no-op that gives in-code/code precedence over the DB;
* override resolution (``tool_overrides`` → effective ``_tool_schemas``);
* the GENERIC dispatch (never an ``if/elif`` by tool name), preserving the
  ``filter.tool.args`` / ``filter.tool.result`` + ``tool.before`` / ``tool.after``
  semantics applied by the AGNO entrypoints around ``dispatch``.

The :class:`ToolRegistry` keeps zero policy of its own beyond the registry —
filters/events are applied by the AGNO entrypoints (``agent.agno_engine``); this
module only resolves the executor and runs it (``dispatch``), exactly as the old
``AgentHandler._dispatch_tool`` did. ``AgentHandler`` holds one instance and
delegates the public surface (``known_tool_names``/``register_plugin_tools``/…)
so existing callers keep working unchanged.
"""

from __future__ import annotations

import copy
import logging

from db.repositories import tool_override_repo
from plugins.context import ToolContext

logger = logging.getLogger(__name__)


def _default_enabled(name: str) -> bool:
    """Default de ``tool_overrides.enabled`` na primeira criação da row.

    Delegado a ``agent.ai_builtin_tools.default_override_enabled`` (dono da
    política off-by-default do plano 30 WS2). Import lazy + defensivo: qualquer
    falha cai no comportamento legado (nasce ligada).
    """
    try:
        from agent import ai_builtin_tools
        return ai_builtin_tools.default_override_enabled(name)
    except Exception:  # noqa: BLE001 — nunca bloqueia um registro de tool
        return True


class ToolRegistry:
    """Holds the tool schema/executor registry and resolves overrides.

    State (was on ``AgentHandler``):

    * ``_tool_originals`` — canonical in-code schema per tool (already stripped
      of non-OpenAI keys like ``display_label``).
    * ``_tool_default_labels`` — in-code ``display_label`` per tool (UI default).
    * ``_tool_schemas`` — the *effective* list sent to the LLM, rebuilt whenever
      overrides change.
    * ``_disabled_tools`` — names disabled by a user override.
    * ``_tool_executors`` — ``name -> (executor_callable, plugin_id_or_None)``.
    * ``_prompt_fragments`` — list of ``(fragment_fn, plugin_id_or_None)``.
    """

    def __init__(self) -> None:
        self._tool_originals: dict[str, dict] = {}
        self._tool_default_labels: dict[str, str] = {}
        self._tool_schemas: list[dict] = []
        self._disabled_tools: set[str] = set()
        # name -> (executor_callable, plugin_id_or_None)
        self._tool_executors: dict[str, tuple[callable, str | None]] = {}
        # Prompt fragment registry — list of (fragment_fn, plugin_id_or_None).
        # Each fragment is ``Callable[[ContactMemory, PromptContext], str]``.
        self._prompt_fragments: list[tuple[callable, str | None]] = []

    # ── registration ─────────────────────────────────────────────────────────
    def register_tool(
        self,
        schema: dict,
        executor: callable,
        plugin_id: str | None = None,
    ) -> None:
        """Register a tool schema + executor. No-ops on name collision.

        Stores a clean deep-copy in ``_tool_originals`` (with WhatsBot-specific
        keys like ``display_label`` stripped, so it's safe to send to the LLM
        as-is) and eagerly inserts a default row into ``tool_overrides`` so the
        management UI sees every registered tool.
        """
        try:
            name = schema["function"]["name"]
        except (KeyError, TypeError):
            logger.warning("Invalid tool schema: %s", schema)
            return
        if name in self._tool_executors:
            existing_pid = self._tool_executors[name][1]
            logger.warning(
                "Tool name collision: '%s' already registered by %s; ignoring %s",
                name, existing_pid or "core", plugin_id or "core",
            )
            return
        # Pluck WhatsBot-only metadata so the schema we pass to the LLM proxy is
        # a clean tool spec.
        clean = copy.deepcopy(schema)
        default_label = clean.pop("display_label", None)
        if default_label:
            self._tool_default_labels[name] = str(default_label)
        self._tool_originals[name] = clean
        self._tool_schemas.append(clean)
        self._tool_executors[name] = (executor, plugin_id)
        try:
            tool_override_repo.ensure(
                name, plugin_id, default_enabled=_default_enabled(name))
        except Exception as e:
            logger.warning("tool_overrides.ensure failed for %s: %s", name, e)

    def register_plugin_tools(
        self,
        plugin_id: str,
        tools: list[tuple[dict, callable]],
    ) -> None:
        """Register tools from a plugin. Called by the plugin loader."""
        for schema, executor in tools:
            self.register_tool(schema, executor, plugin_id=plugin_id)

    def register_ai_tools(self, tools: list[tuple[dict, callable]]) -> int:
        """Register code-in-DB tools (``ai_tools``). Returns the count registered.

        Called by ``agent.ai_tool_installer`` after core + plugin tools, so the
        registry's collision no-op gives code precedence over the DB. Tagged with
        ``plugin_id=None`` (same as core) — identity is the tool ``name``.
        """
        before = len(self._tool_executors)
        for schema, executor in tools:
            self.register_tool(schema, executor, plugin_id=None)
        return len(self._tool_executors) - before

    def override_tool(
        self,
        schema: dict,
        executor: callable,
        plugin_id: str | None = None,
    ) -> None:
        """Replace an already-registered tool's schema + executor in place.

        Unlike :meth:`register_tool` (which no-ops on collision), this REPLACES
        the existing entry. Used by the built-in tools installer when an operator
        has edited a ``kind='builtin'`` core tool: the edited DB code overrides
        the trusted on-disk baseline registered at construction.

        ``_tool_schemas`` is rebuilt from ``_tool_originals`` by
        :meth:`refresh_tool_overrides` (called once after install at startup), so
        we only update the source dicts here.
        """
        try:
            name = schema["function"]["name"]
        except (KeyError, TypeError):
            logger.warning("override_tool: invalid schema %s", schema)
            return
        clean = copy.deepcopy(schema)
        default_label = clean.pop("display_label", None)
        if default_label:
            self._tool_default_labels[name] = str(default_label)
        self._tool_originals[name] = clean
        self._tool_executors[name] = (executor, plugin_id)
        try:
            tool_override_repo.ensure(
                name, plugin_id, default_enabled=_default_enabled(name))
        except Exception as e:
            logger.warning("tool_overrides.ensure failed for %s: %s", name, e)

    def unregister_tool(self, name: str) -> None:
        """Remove a tool from the registry (e.g. a disabled built-in tool).

        ``_tool_schemas`` is rebuilt from ``_tool_originals`` by
        :meth:`refresh_tool_overrides`, so dropping it from the source dicts is
        enough — the rebuilt effective list won't include it.
        """
        self._tool_originals.pop(name, None)
        self._tool_executors.pop(name, None)
        self._tool_default_labels.pop(name, None)
        self._disabled_tools.discard(name)

    def register_plugin_prompts(
        self,
        plugin_id: str,
        fragments: list[callable],
    ) -> None:
        """Register prompt fragments from a plugin. Called by the plugin loader."""
        for fn in fragments:
            self._prompt_fragments.append((fn, plugin_id))

    # ── introspection ────────────────────────────────────────────────────────
    def known_tool_names(self) -> set[str]:
        """Names of every tool currently registered (core + plugin)."""
        return set(self._tool_originals.keys())

    def refresh_tool_overrides(self) -> None:
        """Re-read ``tool_overrides`` and rebuild ``_tool_schemas``.

        Called after every PUT on /api/tools/{name} and once after plugin
        loading at startup. Atomically replaces ``_tool_schemas`` so requests
        already in flight keep their captured reference.

        Plugin tools registered after startup are not supported today — plugin
        enable/disable forces a server restart, and this method assumes the
        registry is stable when called.
        """
        try:
            overrides = {row["name"]: row for row in tool_override_repo.list_all()}
        except Exception as e:
            logger.warning("Failed to read tool_overrides: %s", e)
            overrides = {}
        new_schemas: list[dict] = []
        new_disabled: set[str] = set()
        for name, original in self._tool_originals.items():
            ov = overrides.get(name)
            if ov and not ov["enabled"]:
                new_disabled.add(name)
                continue
            if ov and ov.get("description"):
                schema = copy.deepcopy(original)
                schema["function"]["description"] = ov["description"]
                new_schemas.append(schema)
            else:
                new_schemas.append(original)
        self._tool_schemas = new_schemas
        self._disabled_tools = new_disabled

    def list_tools(self) -> list[dict]:
        """Return metadata for every registered tool, with override state merged."""
        try:
            overrides = {row["name"]: row for row in tool_override_repo.list_all()}
        except Exception:
            overrides = {}
        items: list[dict] = []
        for name, original in self._tool_originals.items():
            fn = original.get("function", {})
            default_description = fn.get("description", "")
            default_label = self._tool_default_labels.get(name)
            _, plugin_id = self._tool_executors.get(name, (None, None))
            ov = overrides.get(name) or {}
            current_description = ov.get("description") or default_description
            current_label = ov.get("display_label") or default_label
            items.append({
                "name": name,
                "plugin_id": plugin_id,
                "default_description": default_description,
                "current_description": current_description,
                "default_label": default_label,
                "display_label": ov.get("display_label"),
                "current_label": current_label,
                "enabled": bool(ov.get("enabled", 1)),
                "has_override": bool(ov.get("description")),
                "has_label_override": bool(ov.get("display_label")),
                "parameters_schema": fn.get("parameters", {}),
            })
        return items

    def select_active_tools(self, agent_spec) -> list[dict]:
        """Return the effective tool schemas, restricted to the agent's selection.

        When the DB-driven agent declares ``tool_names`` (a list), only those
        tools are exposed; ``None`` (or no spec) means every registered tool —
        identical to the legacy behaviour. Plugin ``filter.llm.tools`` runs
        afterwards on whatever subset this returns.
        """
        if agent_spec is None or agent_spec.tool_names is None:
            return list(self._tool_schemas)
        wanted = set(agent_spec.tool_names)
        return [
            s for s in self._tool_schemas
            if (s.get("function") or {}).get("name") in wanted
        ]

    # ── dispatch ─────────────────────────────────────────────────────────────
    def make_tool_ctx(
        self,
        contact,
        handler,
        tag_registry,
        plugin_id: str | None = None,
    ) -> ToolContext:
        return ToolContext(
            contact=contact,
            handler=handler,
            tag_registry=tag_registry,
            plugin_id=plugin_id,
        )

    def dispatch(
        self,
        contact,
        handler,
        tag_registry,
        name: str,
        args: dict,
    ) -> str | None:
        """Run a tool by name and return an optional follow-up feedback string.

        Generic — never an ``if/elif`` by tool name. ``filter.tool.args`` /
        ``filter.tool.result`` and ``tool.before`` / ``tool.after`` are applied by
        the AGNO entrypoints around this call (``agent.agno_engine``)."""
        entry = self._tool_executors.get(name)
        if not entry:
            logger.warning("Unknown tool: %s", name)
            return None
        if name in self._disabled_tools:
            logger.info("Tool '%s' is disabled by user override; skipping", name)
            return None
        executor, plugin_id = entry
        ctx = self.make_tool_ctx(contact, handler, tag_registry, plugin_id=plugin_id)
        try:
            return executor(ctx, args)
        except Exception as e:
            logger.warning("Tool '%s' execution failed: %s", name, e)
            return None
