"""Integration coverage for loading the synthetic provider as a real plugin."""

from pathlib import Path

from tests.fake_provider import FakeChannel
from tests.plugin_test_utils import loaded_plugin_module
from tests.support import build_test_app


FAKE_PLUGIN_SOURCE = (
    Path(__file__).resolve().parent / "fixtures" / "plugins" / "fake_provider"
)


def _write_bus_plugin(root: Path, plugin_id: str = "bus_fixture") -> Path:
    source = root / plugin_id
    source.mkdir(parents=True)
    (source / "plugin.yaml").write_text(
        f"id: {plugin_id}\n"
        "name: Bus Fixture\n"
        "version: 1.0.0\n"
        'whatsbot_api_version: ">=1.0,<2.0"\n'
        "entry:\n"
        "  events: events\n"
        "  filters: filters\n"
        "rbac:\n"
        "  group: Bus Fixture\n"
        "  permissions:\n"
        "    - key: view\n"
        "      label: View fixture\n",
        encoding="utf-8",
    )
    (source / "events.py").write_text(
        "def _received(ctx, payload):\n"
        "    return None\n"
        "EVENT_HANDLERS = {'message.received': _received}\n",
        encoding="utf-8",
    )
    (source / "filters.py").write_text(
        "def _prompt(ctx, value):\n"
        "    return value\n"
        "FILTERS = {'filter.system_prompt': _prompt}\n",
        encoding="utf-8",
    )
    return source


def _assert_plugin_row_equivalent(actual, expected) -> None:
    if expected is None:
        assert actual is None
        return
    assert actual is not None
    for key in ("version", "enabled", "installed_deps", "load_error"):
        assert actual.get(key) == expected.get(key)


def test_app_boots_with_no_plugin_source_folders(build_app, authenticated_admin):
    """The core must boot when neither workbench nor installed plugins exist."""
    built = build_app([])
    authenticated_admin(built.client)

    assert list((built.data_dir / "assets" / "plugin_examples").iterdir()) == []
    assert list((built.data_dir / "storages" / "plugins").iterdir()) == []
    assert built.app.state.deps.plugins_registry.loaded == {}

    response = built.client.get("/api/channels/providers")
    assert response.status_code == 200, response.text
    providers = {
        descriptor["provider"]
        for descriptor in response.json()["data"]["providers"]
    }
    # GOWA is still the core compatibility provider until plano 100 F2.
    assert providers == {"gowa"}


def test_fake_provider_is_loaded_into_registry_and_provider_endpoint(
    build_app,
    authenticated_admin,
):
    built = build_app(
        ["fake_provider"],
        plugin_sources={"fake_provider": FAKE_PLUGIN_SOURCE},
    )
    authenticated_admin(built.client)

    deps = built.app.state.deps
    assert set(deps.plugins_registry.loaded) == {"fake_provider"}
    assert deps.channel_registry.get_provider("fake") is FakeChannel

    response = built.client.get("/api/channels/providers")
    assert response.status_code == 200, response.text
    providers = response.json()["data"]["providers"]
    fake_descriptor = next(
        descriptor for descriptor in providers
        if descriptor["provider"] == "fake"
    )
    assert fake_descriptor["contact_type"] == "outros"
    assert fake_descriptor["capabilities"] == {
        "needs_qr": False,
        "templates": False,
    }

    manifest_response = built.client.get("/api/plugins/manifest")
    assert manifest_response.status_code == 200, manifest_response.text
    public_manifest = next(
        item for item in manifest_response.json()["data"]["plugins"]
        if item["id"] == "fake_provider"
    )
    assert public_manifest["plugin_services_version"] == "1.0"


def test_explicit_source_restores_plugin_database_row(_engine_ready):
    from db.repositories import plugin_repo

    before = plugin_repo.get("fake_provider")
    built = build_test_app(
        ["fake_provider"],
        plugin_sources={"fake_provider": FAKE_PLUGIN_SOURCE},
    )
    try:
        assert plugin_repo.get("fake_provider")["enabled"] == 1
    finally:
        built.close()

    after = plugin_repo.get("fake_provider")
    _assert_plugin_row_equivalent(after, before)


def test_normal_resolved_source_restores_plugin_database_row(_engine_ready):
    """Row isolation applies to every requested plugin, not only explicit fixtures."""
    from db.repositories import plugin_repo

    before = plugin_repo.get("telegram")
    built = build_test_app(["telegram"])
    try:
        assert plugin_repo.get("telegram")["enabled"] == 1
    finally:
        built.close()
    _assert_plugin_row_equivalent(plugin_repo.get("telegram"), before)


def test_close_restores_process_global_bus_and_synthetic_rbac(_engine_ready, tmp_path):
    from db.repositories import rbac_repo
    from plugins import events as bus

    source = _write_bus_plugin(tmp_path)
    handlers_before = {name: list(items) for name, items in bus._handlers.items()}
    filters_before = {name: list(items) for name, items in bus._filters.items()}
    listeners_before = list(bus._core_sync_listeners)
    assert not any(
        row["plugin_id"] == "bus_fixture"
        for row in rbac_repo.list_plugin_permissions()
    )

    built = build_test_app(
        ["bus_fixture"], plugin_sources={"bus_fixture": source},
    )
    assert any(pid == "bus_fixture" for pid, _ in bus._handlers["message.received"])
    assert any(pid == "bus_fixture" for _, pid, _ in bus._filters["filter.system_prompt"])
    assert any(
        row["plugin_id"] == "bus_fixture"
        for row in rbac_repo.list_plugin_permissions()
    )
    built.close()

    assert bus._handlers == handlers_before
    assert bus._filters == filters_before
    assert bus._core_sync_listeners == listeners_before
    assert not any(
        row["plugin_id"] == "bus_fixture"
        for row in rbac_repo.list_plugin_permissions()
    )


def test_close_restores_tool_overrides_deleted_by_subset_boot(_engine_ready):
    """The app's global orphan cleanup must not leak through the test harness."""
    from uuid import uuid4

    from db.repositories import tool_override_repo

    name = f"harness_unselected_{uuid4().hex}"
    tool_override_repo.ensure(name, "plugin_not_selected")
    expected = tool_override_repo.upsert_override(
        name,
        enabled=False,
        description="keep this preference",
        display_label="Unselected tool",
        reuse_result=True,
    )
    try:
        built = build_test_app([])
        try:
            assert tool_override_repo.get(name) is None
        finally:
            built.close()
        assert tool_override_repo.get(name) == expected
    finally:
        tool_override_repo.delete(name)


def test_nested_builds_isolate_and_restore_group_mentions_singleton(_engine_ready):
    from agent import group_mentions
    from tests.fakes import FakeGowaClient

    baseline_client = group_mentions._client
    baseline_members = dict(group_mentions._members_cache)
    baseline_phone = group_mentions._bot_phone
    baseline_name = group_mentions._bot_name
    first_client = FakeGowaClient()
    second_client = FakeGowaClient()
    first = build_test_app([], gowa_client=first_client)
    try:
        group_mentions.set_bot_identity("5511999990001", "Outer bot")
        group_mentions._members_cache["first@g.us"] = (1.0, [{"name": "First"}])
        group_mentions._pushname_cache["5511"] = "First"

        second = build_test_app([], gowa_client=second_client)
        try:
            assert group_mentions._client is second_client
            assert group_mentions._members_cache == {}
            assert group_mentions._pushname_cache == {}
            assert group_mentions._bot_phone == ""
            assert group_mentions._bot_name == ""
        finally:
            second.close()

        assert group_mentions._client is first_client
        assert "first@g.us" in group_mentions._members_cache
        assert group_mentions._pushname_cache["5511"] == "First"
        assert group_mentions._bot_phone == "5511999990001"
        assert group_mentions._bot_name == "Outer bot"
    finally:
        first.close()

    assert group_mentions._client is baseline_client
    assert group_mentions._members_cache == baseline_members
    assert group_mentions._bot_phone == baseline_phone
    assert group_mentions._bot_name == baseline_name


def test_nested_build_with_same_client_restores_mutated_group_member_cache(
    _engine_ready,
):
    """A nested app cannot mutate the outer app's cached member dictionaries."""
    import time

    from agent import group_mentions
    from tests.fakes import FakeGowaClient

    shared_client = FakeGowaClient()
    outer = build_test_app([], gowa_client=shared_client)
    try:
        original = {
            "phone": "5511999991111",
            "lid": "",
            "name": "",
            "is_admin": False,
        }
        group_mentions._members_cache["same@g.us"] = (
            time.time(), [original.copy()],
        )

        inner = build_test_app([], gowa_client=shared_client)
        try:
            # Same-client init deliberately retains the live cache. Simulate the
            # in-place name enrichment performed by get_members(resolve_names=True).
            group_mentions._members_cache["same@g.us"][1][0]["name"] = "Mutated"
        finally:
            inner.close()

        assert group_mentions._members_cache["same@g.us"][1] == [original]
    finally:
        outer.close()


def test_failed_build_rolls_back_rows_bus_and_modules(
    _engine_ready, tmp_path, monkeypatch,
):
    from db.repositories import plugin_repo
    from plugins import events as bus
    from starlette.testclient import TestClient
    import sys

    source = _write_bus_plugin(tmp_path)
    before = plugin_repo.get("bus_fixture")
    handlers_before = {name: list(items) for name, items in bus._handlers.items()}
    filters_before = {name: list(items) for name, items in bus._filters.items()}

    def _fail_enter(_self):
        raise RuntimeError("fixture enter failed")

    monkeypatch.setattr(TestClient, "__enter__", _fail_enter)

    import pytest
    with pytest.raises(RuntimeError, match="fixture enter failed"):
        build_test_app(
            ["bus_fixture"], plugin_sources={"bus_fixture": source},
        )

    _assert_plugin_row_equivalent(plugin_repo.get("bus_fixture"), before)
    assert bus._handlers == handlers_before
    assert bus._filters == filters_before
    assert not any(
        name == "whatsbot_plugins.bus_fixture"
        or name.startswith("whatsbot_plugins.bus_fixture.")
        for name in sys.modules
    )


def test_repeated_build_reloads_same_plugin_id_from_new_explicit_source(
    build_app,
    tmp_path,
):
    """A second app must not reuse submodules imported from the first tree."""

    def _source(folder: str, provider: str) -> Path:
        source = tmp_path / folder
        source.mkdir()
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "plugin.yaml").write_text(
            "id: reload_provider\n"
            "name: Reload Provider Fixture\n"
            "version: 1.0.0\n"
            'whatsbot_api_version: ">=1.0,<2.0"\n'
            "entry:\n"
            "  channels: channels\n",
            encoding="utf-8",
        )
        (source / "channels.py").write_text(
            "from tests.fake_provider import FakeChannel\n"
            f"Provider = FakeChannel.configured(provider={provider!r})\n"
            "CHANNEL_PROVIDERS = [Provider]\n",
            encoding="utf-8",
        )
        return source

    first = build_app(
        ["reload_provider"],
        plugin_sources={"reload_provider": _source("first", "reload_first")},
    )
    assert first.app.state.deps.channel_registry.get_provider("reload_first") is not None

    second = build_app(
        ["reload_provider"],
        plugin_sources={"reload_provider": _source("second", "reload_second")},
    )
    registry = second.app.state.deps.channel_registry
    assert registry.get_provider("reload_first") is None
    assert registry.get_provider("reload_second") is not None
    runtime_module = loaded_plugin_module("reload_provider", "channels")
    assert runtime_module.Provider.provider == "reload_second"


def test_explicit_migrated_source_keeps_history_between_builds(
    _engine_ready,
    tmp_path,
):
    """Teardown must not delete migration history while leaving tables behind."""
    from db.engine import get_engine
    from db.repositories import plugin_repo
    from sqlalchemy import text

    plugin_id = "migration_fixture"
    source = tmp_path / plugin_id
    migrations = source / "migrations"
    migrations.mkdir(parents=True)
    (source / "plugin.yaml").write_text(
        f"id: {plugin_id}\n"
        "name: Migration Fixture\n"
        "version: 1.0.0\n"
        'whatsbot_api_version: ">=1.0,<2.0"\n'
        "migrations: migrations\n",
        encoding="utf-8",
    )
    # If close() deleted plugin_migrations but left the table, the second build
    # would replay this non-idempotent ALTER and fail with a duplicate column.
    (migrations / "001_initial.sql").write_text(
        "CREATE TABLE IF NOT EXISTS plugin_migration_fixture_state "
        "(id INTEGER PRIMARY KEY);\n"
        "ALTER TABLE plugin_migration_fixture_state ADD COLUMN marker TEXT;\n",
        encoding="utf-8",
    )

    first = build_test_app(
        [plugin_id], plugin_sources={plugin_id: source},
    )
    first.close()
    retained = plugin_repo.get(plugin_id)
    assert retained is not None
    assert retained["enabled"] == 0
    assert plugin_repo.applied_migrations(plugin_id) == {1}

    second = build_test_app(
        [plugin_id], plugin_sources={plugin_id: source},
    )
    try:
        assert plugin_repo.applied_migrations(plugin_id) == {1}
    finally:
        second.close()
        # The test owns this synthetic schema and can fully remove it now.
        with get_engine().begin() as connection:
            connection.execute(text(
                "DROP TABLE IF EXISTS plugin_migration_fixture_state"
            ))
        plugin_repo.delete(plugin_id)
