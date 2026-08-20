"""Tools de plugin são editáveis, versionadas e excluíveis como as do core.

Sobe o app REAL com um plugin sintético (o loader de verdade, o boot de verdade),
de modo que arrancar a costura de ``agent.ai_plugin_tools`` derruba estes testes —
um teste que importasse o módulo por caminho continuaria verde sem a costura.

O plugin sintético reproduz DE PROPÓSITO a forma que motivou o recurso: um
``tools.py`` com ``CORE_TOOLS`` de DUAS tools que faz ``from . import logic``.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from agent import ai_builtin_tools, ai_plugin_tools
from db.repositories import tool_override_repo, tool_repo
from server.routes import ai_engine as ai_routes
from server.routes import plugins as plugin_routes
from tests.support import build_test_app

PLUGIN_ID = "twotools"
TOOL_A = "twotools_alpha"
TOOL_B = "twotools_beta"

PLUGIN_YAML = f"""id: {PLUGIN_ID}
name: Two Tools
version: 1.0.0
whatsbot_api_version: ">=1.0,<2.0"
entry:
  tools: tools
"""

LOGIC_PY = '''MARCA = "do-disco"


def marca():
    return MARCA
'''

# O import relativo é o ponto: sem __package__ no namespace do exec, editar esta
# tool falharia calada e o reconcile cairia de volta no código do disco.
TOOLS_PY = '''from . import logic

A = {"type": "function", "display_label": "Alpha",
     "function": {"name": "twotools_alpha", "description": "alpha",
                  "parameters": {"type": "object", "properties": {}}}}
B = {"type": "function", "display_label": "Beta",
     "function": {"name": "twotools_beta", "description": "beta",
                  "parameters": {"type": "object", "properties": {}}}}


def execute_a(ctx, args):
    return "alpha:" + logic.marca()


def execute_b(ctx, args):
    return "beta:" + logic.marca()


CORE_TOOLS = [(A, execute_a), (B, execute_b)]
'''

EDITADO = TOOLS_PY.replace('return "alpha:" + logic.marca()',
                           'return "alpha-EDITADO:" + logic.marca()')


@pytest.fixture
def plugin_source(tmp_path: Path) -> Path:
    plugin = tmp_path / PLUGIN_ID
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(PLUGIN_YAML, encoding="utf-8")
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "logic.py").write_text(LOGIC_PY, encoding="utf-8")
    (plugin / "tools.py").write_text(TOOLS_PY, encoding="utf-8")
    return plugin


@pytest.fixture(autouse=True)
def _sem_restart():
    """As rotas de delete agendam ``os._exit(0)`` — que mataria o pytest."""
    with patch.object(ai_routes, "schedule_restart", lambda *a, **k: None), \
         patch.object(plugin_routes, "schedule_restart", lambda *a, **k: None):
        yield


@pytest.fixture(autouse=True)
def _limpa_rows():
    """As rows vivem no banco COMPARTILHADO — limpar antes e depois."""
    def _wipe():
        for nome in (TOOL_A, TOOL_B):
            tool_repo.delete(nome)
            tool_override_repo.delete(nome)
        ai_builtin_tools.untombstone_tools([TOOL_A, TOOL_B])
        ai_plugin_tools.forget_tombstone_owners([TOOL_A, TOOL_B])
    _wipe()
    yield
    _wipe()


def _build(plugin_source: Path):
    return build_test_app(
        plugins=(PLUGIN_ID,), plugin_sources={PLUGIN_ID: plugin_source})


def _run(handler, name, ctx=None, args=None):
    executor, _pid = handler._tool_executors[name]
    return executor(ctx, args or {})


# ── seed ─────────────────────────────────────────────────────────────────────

def test_seed_cria_uma_row_por_tool_do_plugin(_engine_ready, plugin_source):
    built = _build(plugin_source)
    try:
        for nome in (TOOL_A, TOOL_B):
            row = tool_repo.get(nome)
            assert row is not None, f"{nome} não foi semeada"
            assert row["kind"] == "plugin"
            assert row["plugin_id"] == PLUGIN_ID
            assert row["version"] == 1
            assert row["install_status"] == "ok"
            assert "CORE_TOOLS" in row["code"]
        # Mesmo módulo ⇒ mesmo código nas duas rows.
        assert tool_repo.get(TOOL_A)["code"] == tool_repo.get(TOOL_B)["code"]
    finally:
        built.close()


def test_row_v1_nao_executa_o_codigo_do_banco(_engine_ready, plugin_source):
    """O guard central: só edição (version > 1) tira o disco do comando."""
    built = _build(plugin_source)
    built.close()
    # Envenena o código SEM bumpar a versão (o caminho que o seed usa).
    tool_repo.sync_source(TOOL_A, description="alpha",
                          code="raise RuntimeError('não deveria rodar')",
                          kind="plugin", plugin_id=PLUGIN_ID)
    assert tool_repo.get(TOOL_A)["version"] == 1

    built = _build(plugin_source)
    try:
        assert _run(built.agent_handler, TOOL_A) == "alpha:do-disco"
    finally:
        built.close()


# ── edição ───────────────────────────────────────────────────────────────────

def test_edicao_com_import_relativo_compila_e_sobrepoe(_engine_ready, plugin_source):
    """A regressão que motivou o namespace do exec.

    Com um namespace vazio o ``from . import logic`` levantaria ImportError, o
    reconcile manteria o disco e a edição sumiria sem ninguém ver.
    """
    built = _build(plugin_source)
    built.close()
    tool_repo.save(TOOL_A, description="alpha", code=EDITADO,
                   dependencies=[], enabled=True)
    assert tool_repo.get(TOOL_A)["version"] == 2

    built = _build(plugin_source)
    try:
        row = tool_repo.get(TOOL_A)
        assert row["install_status"] == "ok", row["install_error"]
        assert _run(built.agent_handler, TOOL_A) == "alpha-EDITADO:do-disco"
        # E o dono da tool sobreviveu ao save — sem isso ctx.plugin_db morre.
        assert built.agent_handler._tool_executors[TOOL_A][1] == PLUGIN_ID
        assert row["plugin_id"] == PLUGIN_ID
    finally:
        built.close()


def test_irma_nao_editada_segue_no_disco(_engine_ready, plugin_source):
    """Editar uma row só sobrepõe aquele nome; a irmã continua na baseline."""
    built = _build(plugin_source)
    built.close()
    tool_repo.save(TOOL_A, description="alpha", code=EDITADO,
                   dependencies=[], enabled=True)

    built = _build(plugin_source)
    try:
        assert _run(built.agent_handler, TOOL_A) == "alpha-EDITADO:do-disco"
        assert _run(built.agent_handler, TOOL_B) == "beta:do-disco"
    finally:
        built.close()


def test_edicao_quebrada_mantem_a_baseline_e_marca_failed(_engine_ready, plugin_source):
    built = _build(plugin_source)
    built.close()
    tool_repo.save(TOOL_A, description="alpha", code="def (:", # sintaxe inválida
                   dependencies=[], enabled=True)

    built = _build(plugin_source)
    try:
        row = tool_repo.get(TOOL_A)
        assert row["install_status"] == "failed"
        assert row["install_error"]
        # Fail-closed para o código confiável: a tool continua funcionando.
        assert _run(built.agent_handler, TOOL_A) == "alpha:do-disco"
    finally:
        built.close()


def test_row_desabilitada_desregistra_a_tool(_engine_ready, plugin_source):
    built = _build(plugin_source)
    built.close()
    tool_repo.set_enabled(TOOL_A, False)

    built = _build(plugin_source)
    try:
        assert TOOL_A not in built.agent_handler.known_tool_names()
        assert TOOL_B in built.agent_handler.known_tool_names()
    finally:
        built.close()


# ── drift de upgrade do plugin ───────────────────────────────────────────────

def test_reseed_realinha_row_v1_e_preserva_a_editada(_engine_ready, plugin_source):
    """Plugin atualizado por .zip: a row não editada segue o disco, a editada não.

    Sem isso o editor mostraria o código da versão ANTERIOR do plugin enquanto o
    que roda é o novo — o editor mentiria.
    """
    built = _build(plugin_source)
    built.close()
    tool_repo.save(TOOL_B, description="beta", code=EDITADO,
                   dependencies=[], enabled=False)   # B editada (v2), desligada
    historico_antes = len(tool_repo.list_history(TOOL_A))
    novo_disco = TOOLS_PY.replace('return "beta:"', 'return "beta2:"')
    (plugin_source / "tools.py").write_text(novo_disco, encoding="utf-8")

    built = _build(plugin_source)
    try:
        a = tool_repo.get(TOOL_A)
        assert a["code"] == novo_disco, "row v1 não acompanhou o disco"
        assert a["version"] == 1, "realinhar com o disco não é edição: sem bump"
        # Espelhar o disco não é edição: uma linha de history aqui ofereceria um
        # "reverter" para uma versão idêntica à atual.
        assert len(tool_repo.list_history(TOOL_A)) == historico_antes

        b = tool_repo.get(TOOL_B)
        assert b["code"] == EDITADO, "edição do operador foi sobrescrita"
        assert b["version"] == 2
        assert b["enabled"] is False, "o upgrade religou uma tool desligada"
    finally:
        built.close()


# ── tombstone ────────────────────────────────────────────────────────────────

def test_tool_de_plugin_tombada_nao_ressuscita_no_boot(_engine_ready, plugin_source):
    """O loader recontribui a tool a TODO boot — só o tombstone a segura."""
    built = _build(plugin_source)
    try:
        resp = built.client.delete(f"/api/ai/tools/{TOOL_A}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["tombstoned"] is True
    finally:
        built.close()

    assert tool_repo.get(TOOL_A) is None
    assert tool_override_repo.get(TOOL_A) is None
    assert TOOL_A in ai_builtin_tools.deleted_tools()

    built = _build(plugin_source)
    try:
        assert TOOL_A not in built.agent_handler.known_tool_names()
        assert tool_repo.get(TOOL_A) is None
        # A irmã do mesmo módulo continua inteira — o escopo é por NOME.
        assert TOOL_B in built.agent_handler.known_tool_names()
    finally:
        built.close()


def test_desinstalar_o_plugin_limpa_rows_e_tombstones(_engine_ready, plugin_source):
    """Desinstalar apaga as rows E libera as marcas — é a via de volta.

    O caso difícil é a tool que o operador JÁ tinha excluído: a row dela não
    existe mais, então o dono vem do registro gravado no delete. Sem isso ela
    ficaria tombada para sempre e reinstalar o plugin o traria incompleto.
    """
    built = _build(plugin_source)
    try:
        assert built.client.delete(f"/api/ai/tools/{TOOL_A}").status_code == 200
    finally:
        built.close()
    assert TOOL_A in ai_builtin_tools.deleted_tools()
    assert ai_plugin_tools.tombstoned_names_for(PLUGIN_ID) == [TOOL_A]

    built = _build(plugin_source)
    try:
        resp = built.client.delete(f"/api/plugins/{PLUGIN_ID}")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["ai_tools_removed"] == 1        # sobrava só a row de B
        assert data["tombstones_cleared"] == 1      # a marca de A saiu
    finally:
        built.close()

    assert tool_repo.get(TOOL_B) is None
    assert TOOL_A not in ai_builtin_tools.deleted_tools()
    assert ai_plugin_tools.tombstoned_names_for(PLUGIN_ID) == []

    # Reinstalar devolve as DUAS tools.
    built = _build(plugin_source)
    try:
        nomes = built.agent_handler.known_tool_names()
        assert TOOL_A in nomes and TOOL_B in nomes
    finally:
        built.close()


# ── isolamento ───────────────────────────────────────────────────────────────

def test_installer_isolado_ignora_kind_plugin(_engine_ready, plugin_source):
    """kind='plugin' roda in-process; entrar no installer isolado marcaria
    'failed' numa tool que está funcionando (o subprocesso não resolve o
    ``from . import logic``) e materializaria o módulo em storages/ai_tools/."""
    built = build_test_app(
        plugins=(PLUGIN_ID,), plugin_sources={PLUGIN_ID: plugin_source},
        settings_overrides={"ai_tools_code_enabled": True})
    try:
        assert tool_repo.get(TOOL_A)["install_status"] == "ok"
        materializado = Path(built.data_dir) / "storages" / "ai_tools" / f"{TOOL_A}.py"
        assert not materializado.exists()
    finally:
        built.close()
