"""O nome do zip de export carrega a versao do manifesto.

`GET /api/plugins/{id}/export` devolvia sempre `<id>-plugin.zip`, entao dois
downloads do mesmo plugin em momentos diferentes eram indistinguiveis. Agora o
nome e `<id>-<versao>-plugin.zip`, degradando para o antigo quando a versao nao
puder ser lida com seguranca.
"""

from pathlib import Path

from plugins.semver import WHATSBOT_API_VERSION
from server.routes.plugins import _export_filename


def _write_plugin(root: Path, pid: str, *, version: str = "1.35.0",
                  api_range: str = ">=1.0,<99.0") -> Path:
    d = root / pid
    d.mkdir()
    (d / "plugin.yaml").write_text(
        f"id: {pid}\n"
        f"name: {pid}\n"
        f"version: {version}\n"
        f"whatsbot_api_version: \"{api_range}\"\n",
        encoding="utf-8",
    )
    return d


def test_versao_entra_no_nome(tmp_path):
    d = _write_plugin(tmp_path, "foo", version="1.35.0")
    assert _export_filename("foo", d) == "foo-1.35.0-plugin.zip"


def test_sem_manifesto_cai_no_nome_antigo(tmp_path):
    d = tmp_path / "foo"
    d.mkdir()
    assert _export_filename("foo", d) == "foo-plugin.zip"


def test_api_incompativel_nao_levanta(tmp_path):
    # load_manifest levanta ValueError quando o range nao cobre o core rodando;
    # exportar esse plugin e justamente o caso de uso, nao pode virar 500.
    major = int(WHATSBOT_API_VERSION.split(".")[0])
    d = _write_plugin(tmp_path, "foo", api_range=f">={major + 50}.0,<{major + 60}.0")
    assert _export_filename("foo", d) == "foo-plugin.zip"


def test_versao_com_caractere_hostil_e_descartada(tmp_path):
    # `_is_semver` aceita build tag arbitraria; o header nao pode aceitar.
    d = _write_plugin(tmp_path, "foo", version='1.0.0+a"b')
    assert _export_filename("foo", d) == "foo-plugin.zip"
