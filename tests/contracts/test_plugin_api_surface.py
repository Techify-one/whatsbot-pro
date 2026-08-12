"""Guard: a superfície da API de plugins não muda sem bump de WHATSBOT_API_VERSION.

``WHATSBOT_API_VERSION`` (plugins/semver.py) é o número contra o qual TODO plugin
declara ``whatsbot_api_version``. Ficou em ``1.0.0`` do nascimento do sistema de
plugins (2026-05-10) até 2026-08-11 enquanto a superfície crescia de 35 para 75
eventos, de 0 para 24 filtros e ganhava ``channels/base.py`` inteiro — logo o
guard de compat NUNCA rejeitou nada e nenhum plugin conseguia dizer "preciso de
um core que tenha ``ctx.extras.signature_authenticated``".

A regra em prosa já existia em ``plugins/events.py`` desde 2026-06-29 e foi
violada 8 dias depois, em silêncio (os dois filtros de ``4e78062``). Documentação
não bumpa número. Este arquivo faz quatro coisas:

1. **Coerência** — todo nome que o core produz (``emit*`` / ``apply_filter*``,
   inclusive por wrapper) está no catálogo, e o catálogo não tem nome morto.
2. **Drift** — a superfície viva bate com ``tests/goldens/plugin_api_surface.json``.
3. **Bump** — o snapshot NÃO pode ser regenerado enquanto ``WHATSBOT_API_VERSION``
   não for MAIOR que a versão gravada nele. Regenerar é o único caminho
   sancionado para aceitar drift ⇒ aceitar drift exige bump.
4. **Changelog** — a constante é igual à entrada mais nova de
   ``docs/PLUGIN_API_CHANGELOG.md``: o número nunca viaja sem a prosa.

Aceitar uma mudança intencional::

    # 1. bump plugins/semver.py:WHATSBOT_API_VERSION (MINOR se aditivo)
    # 2. entrada no topo de docs/PLUGIN_API_CHANGELOG.md
    # 3. UPDATE_PLUGIN_API_SURFACE=1 venv/bin/python -m pytest \
    #        tests/contracts/test_plugin_api_surface.py

Roda sem banco, em ~2 s.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re

import pytest

from tests.paths import PROJECT_ROOT

SNAPSHOT = PROJECT_ROOT / "tests" / "goldens" / "plugin_api_surface.json"
CHANGELOG = PROJECT_ROOT / "docs" / "PLUGIN_API_CHANGELOG.md"
UPDATE_ENV = "UPDATE_PLUGIN_API_SURFACE"

# Deliberadamente NÃO é o ``UPDATE_GOLDENS`` usado em massa para refrescar os 111
# goldens de caracterização: aquele varreria a superfície da API junto, que é
# exatamente o bypass acidental que este guard existe para impedir.

# Heading do changelog: "## 1.1.0 — 2026-08-11" (travessão ou hífen). ``search``
# pega o PRIMEIRO, então a entrada mais nova tem de vir antes das outras; o
# apêndice histórico usa "###" e headings sem versão de propósito.
_HEADING_RE = re.compile(r"^##\s+(\d+\.\d+\.\d+)\s*[—-]", re.MULTILINE)


# ── verificador 1: catálogo ↔ produtores ─────────────────────────────────────

_CORE_ROOTS = ("agent", "ai_engine", "app", "channels", "config", "db",
               "domain", "gowa", "runtime", "server", "plugins")
_FILTER_NAME_RE = re.compile(r"^filter\.[a-z0-9_]+(?:\.[a-z0-9_]+)*$")
_EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")

# Nomes emitidos por um caminho que o scanner não enxerga. VAZIO hoje: o mapa
# ``domain.events.EVENT_NAME`` é lido explicitamente e todo wrapper do core que
# emite tem "emit"/"broadcast" no nome. Acrescentar aqui exige um comentário
# dizendo QUEM emite — é a válvula de escape, não o esconderijo.
_INDIRECT_PRODUCERS: set[str] = set()


def _scan_bus_producers() -> tuple[set[str], set[str]]:
    """``(eventos, filtros)`` que o core de fato produz.

    Casa literal string em QUALQUER argumento de chamada — não só o 1º — porque
    ``apply_message_filter(name, msg, extras)`` e ``broadcast_and_emit(ws_event,
    bus_event, ...)`` passam o nome adiante. Uma varredura só-do-arg-0
    aposentaria seams vivos (``filter.message.before_save`` é um deles).
    """
    from domain.events import EVENT_NAME

    events: set[str] = set(EVENT_NAME.values())
    filters: set[str] = set()
    for root in _CORE_ROOTS:
        for path in (PROJECT_ROOT / root).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                callee = (fn.attr if isinstance(fn, ast.Attribute)
                          else fn.id if isinstance(fn, ast.Name) else "")
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    if not (isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)):
                        continue
                    value = arg.value
                    if _FILTER_NAME_RE.match(value):
                        filters.add(value)
                    elif (("emit" in callee or "broadcast" in callee)
                          and _EVENT_NAME_RE.match(value)):
                        events.add(value)
    return events, filters


def test_bus_catalogue_matches_producers():
    """Nome com produtor vivo está no catálogo — e o catálogo não tem nome morto."""
    from plugins.events import KNOWN_EVENTS, KNOWN_FILTERS

    events, filters = _scan_bus_producers()

    orphan_ev = sorted(events - KNOWN_EVENTS)
    orphan_fl = sorted(filters - KNOWN_FILTERS)
    assert not orphan_ev and not orphan_fl, (
        "Seam com produtor vivo FORA do catálogo de plugins/events.py:\n"
        + "".join(f"  emit  {n}\n" for n in orphan_ev)
        + "".join(f"  apply {n}\n" for n in orphan_fl)
        + "\nUm plugin que assine esses nomes leva WARNING de 'desconhecido' "
          "sobre um gancho que FUNCIONA.\n"
          "Catalogue no MESMO commit do call site (é a regra escrita no topo de "
          "KNOWN_FILTERS) e bumpe MINOR.")

    dead_ev = sorted(KNOWN_EVENTS - events - _INDIRECT_PRODUCERS)
    dead_fl = sorted(KNOWN_FILTERS - filters - _INDIRECT_PRODUCERS)
    assert not dead_ev and not dead_fl, (
        "Nome de catálogo SEM produtor no core:\n"
        + "".join(f"  {n}\n" for n in dead_ev + dead_fl)
        + "\nOu o produtor sumiu (então retire o nome — é PATCH: varredura "
          "repo-wide + changelog + teste de WARNING, precedente "
          "filter.media.unknown), ou ele é emitido por um caminho que o scanner "
          "não vê (então acrescente a _INDIRECT_PRODUCERS com um comentário "
          "dizendo quem emite).\n"
          "Convenção que mantém o scanner honesto: todo wrapper que emite tem "
          "'emit' ou 'broadcast' no nome.")


# ── verificador 2: snapshot da superfície ────────────────────────────────────

def _sig(fn) -> str:
    """Assinatura SEM o valor dos defaults (só a marca ``=…``).

    Deliberado: ``check_api_compat(spec, current=WHATSBOT_API_VERSION)`` traria o
    VALOR da constante para dentro do snapshot, que passaria a mudar a cada bump
    — o guard acusaria uma quebra fantasma justamente no commit que faz a coisa
    certa. "Tem default" continua registrado, porque tornar um parâmetro
    obrigatório É quebra.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return f"{getattr(fn, '__name__', '?')}(...)"
    parts: list[str] = []
    star_done = False
    for p in sig.parameters.values():
        if p.kind is p.VAR_POSITIONAL:
            parts.append(f"*{p.name}")
            star_done = True
        elif p.kind is p.VAR_KEYWORD:
            parts.append(f"**{p.name}")
        else:
            if p.kind is p.KEYWORD_ONLY and not star_done:
                parts.append("*")
                star_done = True
            parts.append(p.name + ("=…" if p.default is not p.empty else ""))
    return f"{getattr(fn, '__name__', '?')}({', '.join(parts)})"


def _unwrap(obj):
    """Função por trás de classmethod/staticmethod, senão ``None``.

    Load-bearing: ``Channel.provider_descriptor`` / ``contact_type`` /
    ``identity_from_credentials`` / ``source_id_for`` são CLASSMETHODS —
    exatamente os hooks que o /new-channel manda um plugin implementar.
    ``vars(cls)`` devolve o descriptor, e ``inspect.isfunction`` sobre ele é
    False: um extrator ingênuo omite a metade mais importante do contrato de
    canal e o guard fica verde nela.
    """
    if isinstance(obj, (classmethod, staticmethod)):
        return obj.__func__
    return obj if inspect.isfunction(obj) else None


def _describe_class(name: str, cls) -> str:
    methods = []
    for attr, raw in vars(cls).items():
        if attr.startswith("_"):
            continue
        fn = _unwrap(raw)
        if fn is None:
            continue
        kind = ("classmethod " if isinstance(raw, classmethod)
                else "staticmethod " if isinstance(raw, staticmethod) else "")
        methods.append(kind + _sig(fn))
    fields = sorted(f for f in (getattr(cls, "__annotations__", {}) or {})
                    if not f.startswith("_"))
    return f"class {name}(fields={fields}, methods={sorted(methods)})"


def _public_members(mod) -> list[str]:
    """Funções e classes públicas DEFINIDAS em ``mod`` (re-export é ignorado)."""
    out: list[str] = []
    for name, obj in vars(mod).items():
        if name.startswith("_"):
            continue
        if getattr(obj, "__module__", None) != mod.__name__:
            continue
        if inspect.isfunction(obj):
            out.append(_sig(obj))
        elif inspect.isclass(obj):
            out.append(_describe_class(name, obj))
    return sorted(out)


def _source_assign(relpath: str, name: str):
    """Constante lida do FONTE por AST, sem importar o módulo.

    Usado para ``server/app.py:PLUGIN_PUBLIC_PATH_RE``: importar ``server.app``
    num guard DB-free arrastaria a aplicação inteira.
    """
    tree = ast.parse((PROJECT_ROOT / relpath).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        if not any(t.id == name for t in targets):
            continue
        v = node.value
        if isinstance(v, ast.Call) and v.args and isinstance(v.args[0], ast.Constant):
            return v.args[0].value                      # re.compile(r"...")
        if isinstance(v, ast.Constant):
            return v.value
        if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
            return sorted(e.value for e in v.elts if isinstance(e, ast.Constant))
    raise AssertionError(
        f"{relpath}: constante {name!r} não encontrada — foi renomeada? "
        f"Isso é MAJOR (convenção de host).")


def build_surface() -> dict:
    """O contrato voltado a plugins, como estrutura JSON determinística."""
    from channels import base as channel_base
    from channels import events as channel_events
    from plugins import context as plugin_context
    from plugins import events as bus
    from plugins import loader as plugin_loader
    from plugins import manifest as plugin_manifest
    from plugins import semver as plugin_semver
    from plugins import services as plugin_services

    return {
        "events": sorted(bus.KNOWN_EVENTS),
        "filters": sorted(bus.KNOWN_FILTERS),
        "experimental_filters": sorted(bus.EXPERIMENTAL_FILTERS),
        "lifecycle_events": sorted(bus._LIFECYCLE_EVENTS),
        "dispatch_only_keys": sorted(bus._DISPATCH_ONLY_KEYS),
        "context": _public_members(plugin_context),
        "manifest": _public_members(plugin_manifest),
        "semver": _public_members(plugin_semver),
        # A API interna plugin→plugin. Não é exposta por HTTP de propósito, mas é
        # superfície declarada como qualquer outra: um plugin a importa pelo nome.
        "services": _public_members(plugin_services),
        "channel_base": _public_members(channel_base),
        "channel_events": _public_members(channel_events),
        # A ORDEM é contratual (plugins/loader.py diz "do not reorder").
        "entry_specs": [key for key, _ in plugin_loader._ENTRY_SPECS],
        "conventions": {
            "plugin_id_re": _source_assign("plugins/manifest.py", "_ID_RE"),
            "rbac_key_re": _source_assign("plugins/manifest.py", "_RBAC_KEY_RE"),
            "migration_file_re": _source_assign("plugins/migrator.py", "_MIG_FILE_RE"),
            "table_op_re": _source_assign("plugins/migrator.py", "_TABLE_OP_RE"),
            "audit_action_re": _source_assign("db/audit_actions.py", "PLUGIN_ACTION_RE"),
            "public_path_re": _source_assign("server/app.py", "PLUGIN_PUBLIC_PATH_RE"),
            "teardown_timeout_sec": _source_assign("plugins/lifecycle.py",
                                                   "TEARDOWN_TIMEOUT_SEC"),
            "test_fixtures": _source_assign("tests/plugin_fixtures.py", "__all__"),
        },
    }


def _version_tuple(v: str):
    from plugins.semver import parse_simple_semver
    return parse_simple_semver(v)


def _classify(old: dict, new: dict) -> tuple[str, list[str]]:
    """``("MAJOR"|"MINOR"|"NONE", linhas de diff legíveis)``.

    Um filtro que sai de ``KNOWN_FILTERS`` estando em ``EXPERIMENTAL_FILTERS`` no
    snapshot ANTERIOR conta como MINOR: o contrato já diz que seam experimental
    pode se mover sem MAJOR até se formar.
    """
    experimental_before = set(old.get("experimental_filters", []))
    removed: list[str] = []
    added: list[str] = []
    graduated: list[str] = []
    for section in sorted(set(old) | set(new)):
        before, after = old.get(section), new.get(section)
        if isinstance(before, dict) or isinstance(after, dict):
            before = [f"{k}={v!r}" for k, v in sorted((before or {}).items())]
            after = [f"{k}={v!r}" for k, v in sorted((after or {}).items())]
        b, a = set(before or []), set(after or [])
        for x in sorted(b - a):
            line = f"  - [{section}] {x}"
            if section in ("filters", "experimental_filters") and x in experimental_before:
                graduated.append(line)
            else:
                removed.append(line)
        added += [f"  + [{section}] {x}" for x in sorted(a - b)]
    if not removed and not added and not graduated:
        return "NONE", []
    return ("MAJOR" if removed else "MINOR"), removed + graduated + added


def _write_snapshot(version: str, surface: dict) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"whatsbot_api_version": version, "surface": surface},
                      indent=2, sort_keys=True, ensure_ascii=False)
    SNAPSHOT.write_text(body + "\n", encoding="utf-8")


def test_plugin_api_surface_matches_snapshot():
    from plugins.semver import WHATSBOT_API_VERSION as current

    surface = build_surface()
    update = os.environ.get(UPDATE_ENV)

    if not SNAPSHOT.exists():
        if update:
            _write_snapshot(current, surface)
            return
        pytest.fail(
            f"Snapshot da API de plugins ausente ({SNAPSHOT}).\n"
            f"Gere com:  {UPDATE_ENV}=1 venv/bin/python -m pytest "
            f"tests/contracts/test_plugin_api_surface.py")

    stored = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    stored_version = stored["whatsbot_api_version"]
    level, diff = _classify(stored["surface"], surface)

    if update:
        # O DENTE: regenerar é o único caminho sancionado para aceitar drift, e
        # ele se RECUSA a rodar enquanto a constante não tiver andado.
        if level != "NONE" and _version_tuple(current) <= _version_tuple(stored_version):
            pytest.fail(
                f"Recusando regenerar o snapshot da API de plugins.\n"
                f"A superfície mudou ({level}) mas WHATSBOT_API_VERSION continua "
                f"{current} (o snapshot foi tirado em {stored_version}).\n\n"
                + "\n".join(diff)
                + f"\n\nBumpe plugins/semver.py:WHATSBOT_API_VERSION primeiro "
                  f"({'MAJOR — algo foi removido/renomeado' if level == 'MAJOR' else 'MINOR — só adição'}), "
                  f"escreva a entrada em docs/PLUGIN_API_CHANGELOG.md e rode de novo.\n"
                  f"Se o nome removido NÃO tinha produtor vivo, é correção de "
                  f"catálogo: bump PATCH e registre a varredura no changelog.")
        _write_snapshot(current, surface)
        return

    if level != "NONE":
        pytest.fail(
            f"A superfície da API de plugins mudou sem bump de versão.\n"
            f"WHATSBOT_API_VERSION é {current}; o snapshot foi tirado em "
            f"{stored_version}.\n\n"
            f"O que se mexeu ({level}):\n" + "\n".join(diff) + "\n\n"
            f"Todo plugin declara um range contra esse número. Se ele não anda, "
            f"nenhum plugin consegue exigir o core que tem estes seams.\n\n"
            f"Para aceitar:\n"
            f"  1. bumpe plugins/semver.py:WHATSBOT_API_VERSION "
            f"({'MAJOR' if level == 'MAJOR' else 'MINOR'} — "
            f"{'um nome foi removido/renomeado/retipado' if level == 'MAJOR' else 'só adição'})\n"
            f"  2. entrada no topo de docs/PLUGIN_API_CHANGELOG.md\n"
            f"  3. {UPDATE_ENV}=1 venv/bin/python -m pytest "
            f"tests/contracts/test_plugin_api_surface.py")

    assert current == stored_version, (
        f"WHATSBOT_API_VERSION é {current}, o snapshot registra {stored_version} "
        f"e a superfície é IDÊNTICA.\n"
        f"Ou o bump é espúrio (reverta), ou o snapshot ficou velho "
        f"(rode com {UPDATE_ENV}=1).")


def test_api_version_matches_changelog():
    from plugins.semver import WHATSBOT_API_VERSION as current

    top = None
    if CHANGELOG.exists():
        m = _HEADING_RE.search(CHANGELOG.read_text(encoding="utf-8"))
        top = m.group(1) if m else None
    assert top is not None, (
        f"docs/PLUGIN_API_CHANGELOG.md ausente ou sem heading "
        f"'## <versão> — <data>'. Ele é a metade em prosa do contrato: todo bump "
        f"de WHATSBOT_API_VERSION ({current}) precisa de uma entrada dizendo ao "
        f"autor de plugin o que mudou.\n"
        f"Atenção: o heading da entrada mais nova tem de ser o PRIMEIRO "
        f"'## X.Y.Z —' do arquivo (o apêndice histórico usa '###').")
    assert current == top, (
        f"WHATSBOT_API_VERSION é {current} mas a entrada mais nova de "
        f"docs/PLUGIN_API_CHANGELOG.md é {top}.\n"
        f"Ajuste um dos dois — o número nunca viaja sem a prosa.")


def test_manifest_reexport_stays_in_sync():
    from plugins.manifest import WHATSBOT_API_VERSION as via_manifest
    from plugins.semver import WHATSBOT_API_VERSION as via_semver

    assert via_manifest == via_semver, (
        "plugins/manifest.py re-exporta a constante por VALOR; a fonte é "
        "plugins/semver.py.")


def test_api_range_syntax_contract():
    """A sintaxe aceita no ``whatsbot_api_version`` é contrato — e diverge do JS.

    O parser do frontend aceita ``^``/``~``/``"1.1"`` e trata versão pura como
    compat por MAJOR. O parser Python REJEITA os três e trata versão pura como
    IGUALDADE EXATA. Se alguém "consertar" um dos lados, que seja com este teste
    na frente — e um range rejeitado significa plugin que NÃO CARREGA.
    """
    from plugins.semver import check_api_compat as ok

    assert ok("*", "1.1.0") and ok("", "1.1.0")
    assert ok(">=1.0,<2.0", "1.1.0") and ok(">=1.1,<2.0", "1.1.0")
    assert not ok(">=1.2,<2.0", "1.1.0")
    assert not ok(">=1.0,<2.0", "2.0.0")     # MAJOR derruba o parque inteiro
    assert ok("1.1.0", "1.1.0") and not ok("1.1.0", "1.1.1")   # igualdade EXATA
    for rejected in ("1.1", "^1.1", "~1.1", "1", ">=1.0,<2.0 || >=2.0,<3.0"):
        assert not ok(rejected, "1.1.0"), f"{rejected!r} deveria ser rejeitado"
