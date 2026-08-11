"""The service seam is wired by the REAL loader/app — and never over HTTP.

Boots the actual app with a synthetic provider plugin, so the assertions here
survive the seam being ripped out (a test that imports the module by path would
stay green with the wiring gone).
"""

from pathlib import Path

import pytest

from plugins import services
from tests.support import build_test_app

PROVIDER_ID = "svc_provider"

PLUGIN_YAML = """id: svc_provider
name: Service Provider
version: 1.0.0
whatsbot_api_version: ">=1.0,<2.0"
entry:
  services: services
uses_services:
  - plugin: trackify
    version: ">=1.0,<2.0"
"""

SERVICES_PY = """SERVICES_VERSION = "1.4.0"


def ping(text=""):
    return f"pong:{text}"


SERVICES = {"ping": ping}
"""


@pytest.fixture
def provider_source(tmp_path: Path) -> Path:
    plugin = tmp_path / PROVIDER_ID
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(PLUGIN_YAML, encoding="utf-8")
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "services.py").write_text(SERVICES_PY, encoding="utf-8")
    return plugin


def _build(provider_source: Path):
    return build_test_app(
        plugins=(PROVIDER_ID,), plugin_sources={PROVIDER_ID: provider_source})


def test_services_are_registered_by_create_app_before_setup(_engine_ready, provider_source):
    built = _build(provider_source)
    try:
        proxy = services.get(PROVIDER_ID)

        assert bool(proxy) is True
        assert proxy.version == "1.4.0"
        assert services.call(PROVIDER_ID, "ping", text="oi").data == "pong:oi"
    finally:
        built.close()


def test_uses_services_from_the_manifest_reaches_the_registry(_engine_ready, provider_source):
    built = _build(provider_source)
    try:
        assert services._uses.get(PROVIDER_ID) == {"trackify": ">=1.0,<2.0"}
    finally:
        built.close()


def test_two_apps_in_the_same_process_do_not_leak(_engine_ready, provider_source):
    first = _build(provider_source)
    first.close()

    assert services.available(PROVIDER_ID) is False

    second = _build(provider_source)
    try:
        assert services.available(PROVIDER_ID) is True
    finally:
        second.close()

    assert services.available(PROVIDER_ID) is False


def test_services_are_never_reachable_over_http(_engine_ready, provider_source, tmp_path):
    """A provider's route table must be byte-identical to one without services."""
    plain = tmp_path / "svc_plain"
    plain.mkdir()
    (plain / "plugin.yaml").write_text(
        PLUGIN_YAML.replace("entry:\n  services: services\n", ""), encoding="utf-8")
    (plain / "__init__.py").write_text("", encoding="utf-8")

    with_services = _build(provider_source)
    try:
        routes_with = sorted(
            (getattr(r, "path", ""), sorted(getattr(r, "methods", ()) or ()))
            for r in with_services.app.routes)
    finally:
        with_services.close()

    # Same plugin id, same manifest, only entry.services removed.
    plain_named = tmp_path / PROVIDER_ID / "plain"
    plain_named.parent.mkdir(exist_ok=True)
    plain_named.mkdir()
    for name in ("plugin.yaml", "__init__.py"):
        (plain_named / name).write_text(
            (plain / name).read_text(encoding="utf-8"), encoding="utf-8")

    without = build_test_app(
        plugins=(PROVIDER_ID,), plugin_sources={PROVIDER_ID: plain_named})
    try:
        routes_without = sorted(
            (getattr(r, "path", ""), sorted(getattr(r, "methods", ()) or ()))
            for r in without.app.routes)
    finally:
        without.close()

    assert routes_with == routes_without
    assert not [p for p, _ in routes_with if "/service" in p or "/rpc" in p]
