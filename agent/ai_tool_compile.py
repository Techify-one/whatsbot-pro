"""Compila código de tool guardado no banco e extrai ``(schema, executor)``.

Ponto ÚNICO de compilação in-process das tools editáveis — as builtins core
(``agent.ai_builtin_tools``) e as tools de plugin (``agent.ai_plugin_tools``).
O caminho ISOLADO (``agent.tool_runner``, subprocesso) tem a própria cópia de
propósito: ela roda noutro processo e não pode importar daqui.

Duas formas de módulo são aceitas, as mesmas que o ``tool_runner`` já entende:

* ``SCHEMA``/``TOOL`` (dict) + ``execute(ctx, args)`` — uma tool por módulo, o
  formato das tools core;
* ``CORE_TOOLS = [(schema, executor), ...]`` — VÁRIAS tools num módulo só, o
  formato de ``entry.tools`` de plugin. O executor é escolhido pelo **nome da
  linha**, nunca pela posição.

──────────────────────────────────────────────────────────────────────────────
``package`` — o que faz import relativo funcionar
──────────────────────────────────────────────────────────────────────────────
O ``tools.py`` de um plugin quase sempre começa com ``from . import logic``.
``exec`` com um namespace vazio levanta ``KeyError: '__name__' not in globals``
antes mesmo de chegar ao import; com ``__name__`` mas sem ``__package__``, o
import relativo levanta ``ImportError: attempted relative import with no known
parent package``. Nos dois casos a edição do operador falharia CALADA: o
reconcile mantém a baseline do disco e só uma linha ``install_status='failed'``
denuncia.

Passando ``package="whatsbot_plugins.<id>"`` o import resolve, porque o loader
já deixou esse pacote em ``sys.modules`` com ``submodule_search_locations``
(``plugins/loader.py``). E ele resolve para o módulo JÁ IMPORTADO — é isso que
faz a tool editada continuar enxergando o mesmo ``ContextVar`` do módulo
``logic`` que o resto do plugin usa.

⚠️ O módulo compilado NUNCA entra em ``sys.modules``: gravá-lo sob
``whatsbot_plugins.<id>.tools`` substituiria o módulo vivo que o loader
importou, e quem já tivesse feito ``from . import tools`` ficaria com o objeto
antigo. O objeto criado aqui é destacado, e morre com o escopo do chamador.
"""

from __future__ import annotations

import importlib.util
import types

__all__ = ["compile_tool_module", "compile_tool_namespace",
           "extract_tool", "resolve_executor", "schema_for"]


def schema_for(namespace: dict, name: str, *, allow_alias: bool = True) -> dict | None:
    """O schema OpenAI de ``name`` dentro de um namespace de módulo.

    ``allow_alias`` aceita os apelidos ``SCHEMA``/``TOOL`` (o formato de uma
    tool por módulo). Num módulo MULTI-tool ele tem de ficar desligado: um
    ``SCHEMA`` solto devolveria o schema da tool errada em silêncio.
    """
    if allow_alias:
        schema = namespace.get("SCHEMA") or namespace.get("TOOL")
        if isinstance(schema, dict):
            return schema
    for entry in _core_tools(namespace):
        schema = entry[0]
        if (schema.get("function") or {}).get("name") == name:
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


def resolve_executor(namespace: dict, name: str):
    """O callable que executa ``name``. Espelha ``tool_runner._resolve_executor``."""
    for schema, executor in _core_tools(namespace):
        if (schema.get("function") or {}).get("name") == name:
            return executor
    execute = namespace.get("execute")
    return execute if callable(execute) else None


def _core_tools(namespace: dict) -> list[tuple[dict, callable]]:
    """As entradas ``(schema, executor)`` válidas de ``CORE_TOOLS``, ou vazio."""
    core = namespace.get("CORE_TOOLS") or namespace.get("TOOLS")
    if not isinstance(core, (list, tuple)):
        return []
    return [
        entry for entry in core
        if isinstance(entry, tuple) and len(entry) == 2
        and isinstance(entry[0], dict) and callable(entry[1])
    ]


def compile_tool_module(
    name: str,
    code: str,
    *,
    package: str | None = None,
    module_name: str = "tools",
    file: str | None = None,
) -> tuple[dict, callable]:
    """Compila ``code`` e devolve ``(schema, executor)`` da tool ``name``.

    ``package`` é o pacote do plugin (``whatsbot_plugins.<id>``) — obrigatório
    para que imports relativos resolvam. ``None`` compila um módulo solto (o
    caso das builtins core, que são autocontidas).

    Levanta ``ValueError`` quando o código não compila ou não expõe o contrato.
    O chamador trata isso mantendo a baseline do disco registrada.
    """
    namespace = compile_tool_namespace(
        code, package=package, module_name=module_name, file=file, label=name)
    return extract_tool(namespace, name)


def compile_tool_namespace(
    code: str,
    *,
    package: str | None = None,
    module_name: str = "tools",
    file: str | None = None,
    label: str = "tool",
) -> dict:
    """Compila ``code`` UMA vez e devolve o namespace do módulo resultante.

    Separado de :func:`compile_tool_module` porque um ``tools.py`` de plugin
    define várias tools: o reconcile compila o módulo uma vez por grupo e
    resolve cada nome com :func:`extract_tool`, em vez de re-executar o módulo N
    vezes (um módulo com efeito colateral em import o executaria N vezes).
    """
    if not isinstance(code, str) or not code.strip():
        raise ValueError("código vazio")

    full_name = f"{package}.{module_name}" if package else f"<tool:{label}>"
    origin = file or f"<tool:{label}>"
    spec = importlib.util.spec_from_file_location(full_name, origin)
    module = (
        importlib.util.module_from_spec(spec) if spec is not None
        else types.ModuleType(full_name)
    )
    # __package__ é o que faz `from . import logic` resolver — ver o docstring.
    module.__package__ = package or ""
    module.__file__ = origin

    compiled = compile(code, origin, "exec")
    # noqa: S102 — in-process by design: a tool editada precisa do ToolContext
    # vivo e, no caso de plugin, dos próprios módulos do plugin.
    exec(compiled, module.__dict__)  # noqa: S102
    return module.__dict__


def extract_tool(namespace: dict, name: str) -> tuple[dict, callable]:
    """``(schema, executor)`` de ``name`` num namespace já compilado."""
    multi = bool(_core_tools(namespace))
    executor = resolve_executor(namespace, name)
    if not callable(executor):
        raise ValueError(
            "o código deve definir CORE_TOOLS=[(schema, executor), ...] "
            f"com uma tool chamada '{name}'" if multi
            else "o código deve definir execute(ctx, args)"
        )
    schema = schema_for(namespace, name, allow_alias=not multi)
    if schema is None:
        raise ValueError(f"o código deve definir o schema da tool '{name}'")
    fn = schema.get("function") or {}
    if fn.get("name") != name:
        raise ValueError(
            f"o nome no schema ('{fn.get('name')}') deve ser igual a '{name}'"
        )
    return schema, executor
