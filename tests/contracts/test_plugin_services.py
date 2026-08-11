"""Contract for the in-process plugin→plugin service seam (``plugins.services``).

DB-free. Everything here is the executable statement of the guarantees the seam
promises: dispatch never raises, ``get()`` never returns ``None``, an async op
never blocks the event loop, and the surface never touches HTTP.
"""

import ast
import asyncio
import inspect
import textwrap
from pathlib import Path

import pytest

from plugins import services
from plugins.manifest import load_manifest
from plugins.services import ServiceDisabled


@pytest.fixture(autouse=True)
def _clean_registry():
    services.reset()
    yield
    services.reset()


def _register(pid="provider", ops=None, version="1.0.0", allow=None):
    services.register_plugin_services(
        pid, ops or {"ping": lambda: "pong"}, version=version, allow=allow)


# ── Availability / null object ───────────────────────────────────────────


def test_unknown_plugin_answers_unavailable_without_raising():
    res = services.call("nope", "whatever")

    assert res.status == services.UNAVAILABLE
    assert res.ok is False
    assert res.plugin_id == "nope" and res.op == "whatever"


def test_get_never_returns_none_and_missing_proxy_is_falsy():
    proxy = services.get("nope")

    assert proxy is not None
    assert bool(proxy) is False
    assert proxy.ops == ()
    assert services.available("nope") is False


def test_registered_proxy_is_truthy_and_lists_its_ops():
    _register(ops={"a": lambda: 1, "b": lambda: 2})

    proxy = services.get("provider")

    assert bool(proxy) is True
    assert proxy.ops == ("a", "b")
    assert proxy.version == "1.0.0"
    assert services.describe() == {"provider": {"version": "1.0.0", "ops": ["a", "b"]}}


# ── sync / async dispatch matrix ─────────────────────────────────────────


def test_sync_impl_called_from_the_loop_thread():
    _register(ops={"echo": lambda text="": text.upper()})

    async def main():
        return services.call("provider", "echo", text="oi")

    res = asyncio.run(main())

    assert res.ok and res.data == "OI"


def test_async_impl_via_acall():
    async def slow(n=0):
        await asyncio.sleep(0)
        return n + 1

    _register(ops={"slow": slow})

    res = asyncio.run(services.acall("provider", "slow", n=41))

    assert res.ok and res.data == 42


def test_sync_impl_via_acall_runs_in_a_thread():
    _register(ops={"echo": lambda text="": text})

    res = asyncio.run(services.acall("provider", "echo", text="ok"))

    assert res.ok and res.data == "ok"


def test_async_impl_sync_called_from_a_worker_thread_is_bridged():
    async def add(a=0, b=0):
        await asyncio.sleep(0)
        return a + b

    _register(ops={"add": add})

    async def main():
        loop = asyncio.get_running_loop()
        from plugins import context as plugin_context
        plugin_context.set_runtime(None, loop)
        try:
            return await asyncio.to_thread(
                lambda: services.call("provider", "add", a=2, b=3))
        finally:
            plugin_context.set_runtime(None, None)

    res = asyncio.run(main())

    assert res.ok and res.data == 5


def test_async_impl_sync_called_from_loop_thread_returns_wrong_context_and_does_not_deadlock():
    async def never(*_a, **_kw):
        await asyncio.sleep(3600)

    _register(ops={"never": never})

    async def main():
        loop = asyncio.get_running_loop()
        from plugins import context as plugin_context
        plugin_context.set_runtime(None, loop)
        try:
            return services.call("provider", "never")
        finally:
            plugin_context.set_runtime(None, None)

    res = asyncio.run(asyncio.wait_for(main(), timeout=5))

    assert res.status == services.WRONG_CONTEXT
    assert res.ok is False


# ── Isolation ────────────────────────────────────────────────────────────


def test_unknown_op_answers_unknown_op():
    _register()

    res = services.call("provider", "does_not_exist")

    assert res.status == services.UNKNOWN_OP


def test_raising_op_is_isolated_as_error():
    def boom():
        raise ValueError("kaput")

    _register(ops={"boom": boom})

    res = services.call("provider", "boom")

    assert res.status == services.ERROR
    assert "ValueError" in res.error


def test_service_disabled_becomes_disabled():
    def off():
        raise ServiceDisabled("mirror_enabled=False")

    _register(ops={"off": off})

    res = services.call("provider", "off")

    assert res.status == services.DISABLED
    assert "mirror_enabled" in res.error


def test_locally_defined_service_disabled_also_becomes_disabled():
    # A provider that must stay importable on an older core defines its own
    # fallback class with the same NAME; dispatch matches by class name too.
    class ServiceDisabled(RuntimeError):  # noqa: N801 — shadows on purpose
        pass

    def off():
        raise ServiceDisabled("sem credencial")

    _register(ops={"off": off})

    assert services.call("provider", "off").status == services.DISABLED


def test_async_raising_op_is_isolated_as_error():
    async def boom():
        raise RuntimeError("nope")

    _register(ops={"boom": boom})

    assert asyncio.run(services.acall("provider", "boom")).status == services.ERROR


# ── Version negotiation ──────────────────────────────────────────────────


def test_incompatible_range_is_a_status_not_an_exception():
    _register(version="2.0.0")

    res = services.call("provider", "ping", _requires=">=1.0,<2.0")

    assert res.status == services.INCOMPATIBLE
    assert bool(services.get("provider", requires=">=1.0,<2.0")) is False


def test_star_and_absent_range_are_compatible():
    _register(version="3.4.5")

    assert services.call("provider", "ping").ok
    assert services.call("provider", "ping", _requires="*").ok


def test_uses_services_supplies_the_default_range_via_as_plugin():
    _register(version="2.0.0")
    services.register_plugin_uses(
        "consumer", [{"plugin": "provider", "version": ">=1.0,<2.0"}])

    assert services.call("provider", "ping", _as="consumer").status == services.INCOMPATIBLE
    # An unrelated caller has no declaration → "*" → compatible.
    assert services.call("provider", "ping", _as="outro").ok
    # An explicit requires= wins over the manifest entry.
    assert services.call("provider", "ping", _as="consumer", _requires=">=2.0").ok


# ── Allowlist ────────────────────────────────────────────────────────────


def test_allowlist_blocks_callers_outside_it():
    _register(allow=("amigo",))

    assert services.call("provider", "ping", _as="amigo").ok
    assert services.call("provider", "ping", _as="estranho").status == services.UNAVAILABLE
    assert services.call("provider", "ping").status == services.UNAVAILABLE


# ── Registration hygiene ─────────────────────────────────────────────────


def test_validate_services_rejects_non_dict_and_non_callable(caplog):
    assert services.validate_services("p", "nope") == {}
    assert services.validate_services("p", None) == {}
    out = services.validate_services("p", {"ok": lambda: 1, "bad": 3})
    assert list(out) == ["ok"]


def test_meta_op_cannot_be_overridden_by_the_provider():
    assert services.validate_services("p", {"__meta__": lambda: "mine"}) == {}


def test_unregister_plugin_removes_the_surface():
    _register()
    services.register_plugin_uses("provider", [{"plugin": "x", "version": "*"}])

    services.unregister_plugin("provider")

    assert services.call("provider", "ping").status == services.UNAVAILABLE
    assert services.describe() == {}


def test_meta_op_is_answered_automatically():
    _register(ops={"a": lambda: 1}, version="1.2.3")

    res = services.call("provider", "__meta__")

    assert res.ok
    assert res.data == {"version": "1.2.3", "ops": ["a"]}


def _imported_module_names(path: Path) -> set[str]:
    """Every module name the file imports — comments/docstrings excluded (AST)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_services_module_does_not_import_fastapi():
    imported = _imported_module_names(Path(services.__file__))

    assert not any(name.split(".")[0] == "fastapi" for name in imported), imported


# ── Manifest: uses_services ──────────────────────────────────────────────


def _manifest(tmp_path: Path, body: str):
    plugin = tmp_path / "uses_contract"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "id: uses_contract\n"
        "name: Uses Contract\n"
        "version: 1.0.0\n"
        'whatsbot_api_version: ">=1.0,<2.0"\n'
        + body,
        encoding="utf-8",
    )
    return load_manifest(plugin)


def test_uses_services_parses(tmp_path):
    manifest = _manifest(
        tmp_path,
        "uses_services:\n"
        "  - plugin: trackify\n"
        '    version: ">=1.0,<2.0"\n',
    )

    assert manifest.uses_services == [
        {"plugin": "trackify", "version": ">=1.0,<2.0"}]


def test_uses_services_absent_is_an_empty_list(tmp_path):
    assert _manifest(tmp_path, "").uses_services == []


def test_malformed_uses_services_is_dropped_and_never_fatal(tmp_path):
    manifest = _manifest(
        tmp_path,
        "uses_services:\n"
        "  - plugin: trackify\n"
        "  - plugin: 'NAO VALIDO'\n",
    )

    # Bad entry dropped, good one kept, plugin still loads.
    assert manifest.uses_services == [{"plugin": "trackify", "version": "*"}]


def test_uses_services_not_a_list_is_ignored(tmp_path):
    assert _manifest(tmp_path, "uses_services: nope\n").uses_services == []


def test_uses_services_and_plugin_services_version_are_independent(tmp_path):
    manifest = _manifest(
        tmp_path,
        'plugin_services_version: ">=2.0,<3.0"\n'
        "uses_services:\n"
        "  - plugin: trackify\n"
        '    version: ">=1.0,<2.0"\n',
    )

    assert manifest.plugin_services_version == ">=2.0,<3.0"
    assert manifest.uses_services == [{"plugin": "trackify", "version": ">=1.0,<2.0"}]
    # The internal surface must NOT leak into the public HTTP payload.
    assert "uses_services" not in manifest.to_public_dict()


# ── Old-core compatibility (the executable proof of §7) ──────────────────


def test_entry_services_on_a_core_without_the_row_is_ignored(tmp_path, monkeypatch):
    from plugins import loader

    plugin = tmp_path / "old_core"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "id: old_core\nname: Old Core\nversion: 1.0.0\n"
        "entry:\n  services: services\n",
        encoding="utf-8",
    )
    (plugin / "services.py").write_text(
        "SERVICES = {'ping': lambda: 'pong'}\n", encoding="utf-8")

    legacy_specs = [spec for spec in loader._ENTRY_SPECS if spec[0] != "services"]
    monkeypatch.setattr(loader, "_ENTRY_SPECS", legacy_specs)

    loaded = loader._load_plugin_module(load_manifest(plugin), plugin)

    assert loaded.services == {}


def test_entry_services_collects_the_surface(tmp_path):
    from plugins import loader

    plugin = tmp_path / "new_core"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "id: new_core\nname: New Core\nversion: 1.0.0\n"
        "entry:\n  services: services\n",
        encoding="utf-8",
    )
    (plugin / "services.py").write_text(
        "SERVICES_VERSION = '2.1.0'\n"
        "SERVICES_ALLOW = ('amigo',)\n"
        "SERVICES = {'ping': lambda: 'pong'}\n",
        encoding="utf-8",
    )

    loaded = loader._load_plugin_module(load_manifest(plugin), plugin)

    assert list(loaded.services) == ["ping"]
    assert loaded.services_version == "2.1.0"
    assert loaded.services_allow == ("amigo",)
    # The seam must never populate the HTTP router.
    assert loaded.router is None


def test_entry_services_handler_never_reads_router():
    from plugins import loader

    tree = ast.parse(textwrap.dedent(inspect.getsource(loader._entry_services)))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert "router" not in attributes
