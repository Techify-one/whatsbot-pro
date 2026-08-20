"""O compilador único das tools editáveis in-process.

Roda sem banco. O caso que importa é o ``__package__``: sem ele, o ``tools.py``
de um plugin — que quase sempre começa com ``from . import logic`` — não compila,
e a edição do operador falharia calada (o reconcile mantém o disco e só uma
linha ``install_status='failed'`` denuncia).
"""

import importlib.util
import sys

import pytest

from agent.ai_tool_compile import (
    compile_tool_module,
    compile_tool_namespace,
    extract_tool,
    resolve_executor,
)

UMA_TOOL = '''
SCHEMA = {"type": "function", "function": {"name": "solo", "description": "d"}}


def execute(ctx, args):
    return "solo"
'''

DUAS_TOOLS = '''
A = {"type": "function", "function": {"name": "a", "description": "da"}}
B = {"type": "function", "function": {"name": "b", "description": "db"}}


def ea(ctx, args):
    return "A"


def eb(ctx, args):
    return "B"


CORE_TOOLS = [(A, ea), (B, eb)]
'''


def test_forma_de_uma_tool_por_modulo():
    schema, execute = compile_tool_module("solo", UMA_TOOL)
    assert schema["function"]["name"] == "solo"
    assert execute(None, {}) == "solo"


@pytest.mark.parametrize("nome,esperado", [("a", "A"), ("b", "B")])
def test_multi_tool_resolve_pelo_nome_nunca_pela_posicao(nome, esperado):
    schema, execute = compile_tool_module(nome, DUAS_TOOLS)
    assert schema["function"]["name"] == nome
    assert execute(None, {}) == esperado


def test_multi_tool_ignora_o_apelido_SCHEMA():
    """Num módulo multi-tool, um ``SCHEMA`` solto devolveria a tool ERRADA."""
    code = DUAS_TOOLS + '\nSCHEMA = {"type": "function", "function": {"name": "a"}}\n'
    schema, _ = compile_tool_module("b", code)
    assert schema["function"]["name"] == "b"


def test_nome_fora_do_modulo_e_erro_legivel():
    with pytest.raises(ValueError) as e:
        compile_tool_module("inexistente", DUAS_TOOLS)
    assert "inexistente" in str(e.value)


def test_codigo_vazio():
    with pytest.raises(ValueError, match="vazio"):
        compile_tool_module("x", "   ")


def test_schema_com_nome_divergente():
    code = '''
SCHEMA = {"type": "function", "function": {"name": "outro"}}


def execute(ctx, args):
    return ""
'''
    with pytest.raises(ValueError, match="deve ser igual"):
        compile_tool_module("esperado", code)


# ── o import relativo ────────────────────────────────────────────────────────

@pytest.fixture
def pacote(tmp_path, monkeypatch):
    """Um pacote em sys.modules como o loader de plugins deixa o dele."""
    nome = "pkg_de_teste_ai_tool_compile"
    d = tmp_path / nome
    d.mkdir()
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "logic.py").write_text("VALOR = 'do-modulo-irmao'\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        nome, d / "__init__.py", submodule_search_locations=[str(d)])
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, nome, mod)
    spec.loader.exec_module(mod)
    yield nome, d
    sys.modules.pop(f"{nome}.logic", None)


RELATIVO = '''
from . import logic

SCHEMA = {"type": "function", "function": {"name": "rel", "description": "d"}}


def execute(ctx, args):
    return logic.VALOR
'''


def test_import_relativo_resolve_com_package(pacote):
    nome, d = pacote
    _schema, execute = compile_tool_module(
        "rel", RELATIVO, package=nome, file=str(d / "tools.py"))
    assert execute(None, {}) == "do-modulo-irmao"


def test_import_relativo_sem_package_falha(pacote):
    """A prova de que o ``package`` é load-bearing, e não decoração."""
    with pytest.raises(Exception):
        compile_tool_module("rel", RELATIVO)


def test_nao_registra_o_modulo_compilado_em_sys_modules(pacote):
    """Gravá-lo substituiria o módulo VIVO que o loader importou, e quem já
    tivesse feito ``from . import tools`` ficaria com o objeto antigo."""
    nome, d = pacote
    compile_tool_module("rel", RELATIVO, package=nome, file=str(d / "tools.py"))
    assert f"{nome}.tools" not in sys.modules


def test_um_namespace_serve_varias_tools():
    """O reconcile compila o módulo UMA vez por grupo — um módulo com efeito
    colateral em import o executaria uma vez por tool."""
    ns = compile_tool_namespace(DUAS_TOOLS)
    assert extract_tool(ns, "a")[1](None, {}) == "A"
    assert extract_tool(ns, "b")[1](None, {}) == "B"
    assert resolve_executor(ns, "inexistente") is None
