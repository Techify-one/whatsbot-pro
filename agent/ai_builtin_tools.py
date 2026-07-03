"""Built-in (core) tools as editable code-in-DB rows (``kind='builtin'``).

The four core tools (``save_contact_info``, ``transfer_to_human``,
``set_custom_attribute``, ``transferir_agente``) are ALSO seeded into the
``ai_tools`` table so the unified Tools UI can show them with version / edit /
history exactly like user code-in-DB tools.

Unlike code-in-DB tools, built-in tools run **IN-PROCESS** with the live
``ToolContext`` (they call ``ctx.contact.*`` / ``ctx.tag_registry`` / repos),
so they can NOT run in the isolated subprocess sandbox and are NOT gated by the
``ai_tools_code_enabled`` kill-switch — they are core functionality.

────────────────────────────────────────────────────────────────────────────
SAFETY MODEL (fail-closed — a core tool must never disappear)
────────────────────────────────────────────────────────────────────────────
  • The handler registers the trusted on-disk ``CORE_TOOLS`` at construction.
    That baseline is ALWAYS available and never depends on the database.
  • Seeding is idempotent and only INSERTS missing rows — it never clobbers an
    operator's edits.
  • An UNEDITED built-in (``version == 1``) keeps the trusted on-disk
    registration; its DB code is NOT exec'd in the host process.
  • An EDITED built-in (``version > 1``) has its DB code compiled + exec'd
    IN-PROCESS and OVERRIDES the baseline. ⚠️ This is arbitrary code running
    with full host privileges — but only after an admin explicitly edited the
    tool (ADM feature). If the edited code fails to compile / lacks the
    contract, the baseline on-disk registration stays and the row is marked
    ``install_status='failed'`` with the reason.
  • A DISABLED built-in is unregistered from the handler.
"""

from __future__ import annotations

import inspect
import logging
import types

from agent.tools import save_contact_info as _m_save
from agent.tools import set_custom_attribute as _m_attr
from agent.tools import transfer_to_human as _m_transfer
from agent.tools import transferir_agente as _m_agent
from db.repositories import tool_repo

logger = logging.getLogger(__name__)


# name -> on-disk module that defines the tool (schema dict + execute()).
BUILTIN_MODULES: dict[str, types.ModuleType] = {
    "save_contact_info": _m_save,
    "transfer_to_human": _m_transfer,
    "set_custom_attribute": _m_attr,
    "transferir_agente": _m_agent,
}

# Tools que NASCEM DESLIGADAS numa instalação nova (plano 30 D3/D7). O conjunto
# é consultado pelos DOIS seeds (``seed_builtin_tools`` → ``ai_tools.enabled`` e
# ``default_override_enabled`` → ``tool_overrides.enabled``) para os gates nunca
# divergirem no nascimento. Bancos existentes não são tocados — os seeds só
# criam rows ausentes.
OFF_BY_DEFAULT_TOOLS: set[str] = {"transferir_agente"}


def default_override_enabled(name: str) -> bool:
    """Default de ``tool_overrides.enabled`` na PRIMEIRA criação da row.

    Fora de :data:`OFF_BY_DEFAULT_TOOLS` o default segue ligado. Para as
    off-by-default, o default espelha a row ``ai_tools`` do momento: numa
    instalação nova (sem row ainda) nasce OFF; quando o operador ligou a tool
    pela UI unificada (``ai_tools.enabled=1``) e a row de override foi limpa
    pelo ``delete_orphans`` enquanto a tool esteve desregistrada, a recriação
    no boot seguinte respeita a intenção do operador (senão o "ligar" nunca
    sobreviveria ao restart — os dois gates divergiriam).
    """
    if name not in OFF_BY_DEFAULT_TOOLS:
        return True
    try:
        row = tool_repo.get(name)
        return bool(row and row.get("enabled"))
    except Exception:  # noqa: BLE001 — nasce OFF em caso de dúvida (D3)
        return False


def _schema_in(namespace: dict, name: str) -> dict | None:
    """Find the OpenAI tool-schema dict for ``name`` in a module namespace.

    Core tool modules name their schema ``<NAME>_TOOL`` (not ``SCHEMA``), so we
    accept the canonical aliases first and then scan for any dict that matches
    the ``{type:function, function:{name}}`` shape with the right name.
    """
    schema = namespace.get("SCHEMA") or namespace.get("TOOL")
    if isinstance(schema, dict):
        return schema
    for value in namespace.values():
        if (
            isinstance(value, dict)
            and value.get("type") == "function"
            and isinstance(value.get("function"), dict)
            and value["function"].get("name") == name
        ):
            return value
    return None


def _compile_extract(name: str, code: str) -> tuple[dict, callable]:
    """Compile + exec edited built-in code IN-PROCESS; return (schema, execute).

    Raises ``ValueError`` if the code doesn't compile or doesn't expose the
    ``schema dict + execute(ctx, args)`` contract.
    """
    if not isinstance(code, str) or not code.strip():
        raise ValueError("código vazio")
    namespace: dict = {}
    compiled = compile(code, f"<builtin_tool:{name}>", "exec")
    exec(compiled, namespace)  # noqa: S102 — in-process by design for builtin tools
    execute = namespace.get("execute")
    if not callable(execute):
        raise ValueError("o código deve definir execute(ctx, args)")
    schema = _schema_in(namespace, name)
    if schema is None:
        raise ValueError(f"o código deve definir o schema da tool '{name}'")
    fn = schema.get("function") or {}
    if fn.get("name") != name:
        raise ValueError(
            f"o nome no schema ('{fn.get('name')}') deve ser igual a '{name}'"
        )
    return schema, execute


def seed_builtin_tools() -> None:
    """Insert the built-in tool rows from the current on-disk source (idempotent).

    Only inserts rows that don't exist yet — never overwrites operator edits.
    Seeded rows start at version 1, install_status='ok' (the trusted on-disk
    code is what actually runs until the operator edits it). Rows start enabled,
    EXCETO as de :data:`OFF_BY_DEFAULT_TOOLS`, que nascem desligadas (plano 30
    D3) — vale só para instalações novas, já que rows existentes são puladas.
    """
    for name, module in BUILTIN_MODULES.items():
        try:
            if tool_repo.get(name) is not None:
                continue
            source = inspect.getsource(module)
            schema = _schema_in(module.__dict__, name) or {}
            description = (schema.get("function") or {}).get("description", "")
            tool_repo.save(
                name,
                description=description,
                code=source,
                dependencies=[],
                enabled=name not in OFF_BY_DEFAULT_TOOLS,
                kind="builtin",
            )
            tool_repo.set_status(name, "ok", None)
            logger.info("Seeded built-in tool '%s' into ai_tools", name)
        except Exception as e:  # noqa: BLE001 — seeding is best-effort
            logger.warning("Could not seed built-in tool '%s': %s", name, e)


def register_builtin_overrides(handler) -> None:
    """Reconcile each built-in tool's registration with its ``ai_tools`` row.

    Runs at startup AFTER the handler registered the on-disk baseline and BEFORE
    ``refresh_tool_overrides``. Disabled rows are unregistered; edited rows
    (version > 1) override the baseline with their DB code (fail-closed).
    """
    for name in BUILTIN_MODULES:
        try:
            row = tool_repo.get(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("built-in tool '%s': cannot read row (%s)", name, e)
            continue
        if row is None:
            # Seed didn't run — the on-disk baseline registration stays.
            continue

        if not row.get("enabled", True):
            handler.unregister_tool(name)
            _safe_status(name, "ok", None)
            continue

        if int(row.get("version", 1)) <= 1:
            # Unedited seed → keep the trusted on-disk registration.
            _safe_status(name, "ok", None)
            continue

        # Edited built-in → run the DB code in-process, overriding the baseline.
        try:
            schema, execute = _compile_extract(name, row.get("code") or "")
            handler.override_tool(schema, execute, plugin_id=None)
            _safe_status(name, "ok", None)
            logger.info("Built-in tool '%s' overridden by edited DB code (v%s)",
                        name, row.get("version"))
        except Exception as e:  # noqa: BLE001 — keep baseline, mark failed
            logger.error(
                "Built-in tool '%s' edit failed (%s); keeping on-disk version.",
                name, e,
            )
            _safe_status(name, "failed", str(e))


def is_builtin(name: str) -> bool:
    return name in BUILTIN_MODULES


def _safe_status(name: str, status: str, error: str | None) -> None:
    try:
        tool_repo.set_status(name, status, error)
    except Exception as e:  # noqa: BLE001
        logger.warning("built-in tool '%s': set_status failed (%s)", name, e)
