"""DB-free contract for the independently versioned frontend service surface."""

from pathlib import Path

from plugins.manifest import load_manifest


def _manifest(tmp_path: Path, body: str):
    plugin = tmp_path / "service_contract"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "id: service_contract\n"
        "name: Service Contract\n"
        "version: 1.0.0\n"
        'whatsbot_api_version: ">=1.0,<2.0"\n'
        + body,
        encoding="utf-8",
    )
    return load_manifest(plugin)


def test_legacy_manifest_defaults_plugin_services_to_1_x(tmp_path):
    manifest = _manifest(tmp_path, "")

    assert manifest.plugin_services_version == "1.0"
    assert manifest.to_public_dict()["plugin_services_version"] == "1.0"


def test_manifest_preserves_declared_plugin_services_range(tmp_path):
    manifest = _manifest(
        tmp_path,
        'plugin_services_version: ">=2.0,<3.0"\n',
    )

    assert manifest.plugin_services_version == ">=2.0,<3.0"
    assert manifest.to_public_dict()["plugin_services_version"] == ">=2.0,<3.0"
