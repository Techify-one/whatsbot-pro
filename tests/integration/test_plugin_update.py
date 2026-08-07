"""Plugin UPDATE — atualizar um plugin sem apagar suas tabelas/dados.

Cobre a garantia central da feature "atualizar plugin" (task WHATSBOT): trocar o
código do plugin por um novo .zip e rodar SÓ as migrations novas, preservando as
linhas já gravadas (ao contrário de Deletar + Importar, que dropa as tabelas).

Exercita diretamente os helpers do endpoint (``server.routes.plugins``) + o
migrator incremental contra o engine global do harness (``_engine_ready``).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text as sa_text

from db.engine import get_engine
from db.repositories import plugin_repo
from plugins.loader import _recover_interrupted_updates
from plugins.manifest import load_manifest
from plugins.migrator import run_pending_migrations
from server.routes.plugins import (
    _read_zip_manifest,
    _reject_unsafe_zip_paths,
    _swap_plugin_dir,
    _version_key,
)


def _write_plugin(root: Path, pid: str, version: str,
                  migrations: dict[str, str], body: str = "") -> Path:
    """Materialize a plugin folder (manifest + migrations + a code file)."""
    d = root / pid
    (d / "migrations").mkdir(parents=True, exist_ok=True)
    (d / "plugin.yaml").write_text(
        f"id: {pid}\n"
        f"name: Demo {pid}\n"
        f"version: {version}\n"
        f'whatsbot_api_version: ">=1.0,<2.0"\n'
        f"migrations: migrations\n",
        encoding="utf-8",
    )
    (d / "__init__.py").write_text(body, encoding="utf-8")
    for fname, sql in migrations.items():
        (d / "migrations" / fname).write_text(sql, encoding="utf-8")
    return d


def _zip_of(plugin_dir: Path) -> zipfile.ZipFile:
    """Zip a plugin folder with the manifest at the archive root (import shape)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in plugin_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(plugin_dir).as_posix())
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_update_preserves_table_data(_engine_ready, tmp_path):
    """The crux: an update keeps existing rows and applies only NEW migrations."""
    pid = "demoupd"
    tbl = f"plugin_{pid}_items"
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    eng = get_engine()

    # ── v1: ship a table, install it, simulate "production data" ────────────
    v1 = _write_plugin(plugins_dir, pid, "1.0.0", {
        "001_init.sql": (
            f"CREATE TABLE {tbl} "
            f"(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);"
        ),
    }, body="# v1\n")
    plugin_repo.upsert(pid, "1.0.0")
    assert run_pending_migrations(load_manifest(v1), v1) == [1]
    with eng.begin() as conn:
        conn.execute(sa_text(f"INSERT INTO {tbl} (name) VALUES ('keep-me')"))

    try:
        # ── v2: same 001 + a NEW additive 002 (built in a separate staging) ──
        src2 = tmp_path / "src2"
        src2.mkdir()
        v2 = _write_plugin(src2, pid, "2.0.0", {
            "001_init.sql": (
                f"CREATE TABLE {tbl} "
                f"(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);"
            ),
            "002_add_col.sql": f"ALTER TABLE {tbl} ADD COLUMN note TEXT;",
        }, body="# v2\n")

        # ACT — exactly what the /update endpoint does: swap code, bump version,
        # let the loader re-run pending migrations. NO drop_plugin_tables call.
        _swap_plugin_dir(_zip_of(v2), plugins_dir, pid)
        plugin_repo.upsert(pid, "2.0.0")
        applied = run_pending_migrations(load_manifest(plugins_dir / pid),
                                         plugins_dir / pid)

        # ── ASSERT ──────────────────────────────────────────────────────────
        assert applied == [2], "apenas a migration nova (002) deve rodar"
        with eng.connect() as conn:
            rows = conn.execute(sa_text(f"SELECT name, note FROM {tbl}")).all()
        assert rows == [("keep-me", None)], "linha de produção preservada + coluna nova"
        assert plugin_repo.get(pid)["version"] == "2.0.0"
        # the on-disk code was actually replaced by the new bundle
        assert "# v2" in (plugins_dir / pid / "__init__.py").read_text(encoding="utf-8")
    finally:
        # keep the session-shared DB tidy regardless of assertion outcome
        plugin_repo.drop_plugin_tables(pid)
        plugin_repo.delete(pid)


def test_version_key_orders_and_flags_downgrade():
    assert _version_key("2.0.0") > _version_key("1.9.9")
    assert _version_key("1.0.10") > _version_key("1.0.2")
    assert _version_key("1.2.0-rc1") == _version_key("1.2.0")  # suffix ignored
    # downgrade detection (new < installed)
    assert _version_key("1.0.0") < _version_key("1.1.0")


def test_version_key_strips_v_prefix():
    # review finding #5: "v1.2.3" must not collapse to (0,)
    assert _version_key("v2.0.0") == _version_key("2.0.0")
    assert _version_key("V2.0.0") > _version_key("v1.9.0")


def _put_dir(parent, name, marker_value):
    d = parent / name
    d.mkdir(parents=True)
    (d / "marker.txt").write_text(marker_value, encoding="utf-8")
    return d


def test_recover_promotes_staging_when_target_missing(tmp_path):
    # review finding #1: crash AFTER target->backup and AFTER staging extracted
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    _put_dir(pdir, ".demo.update-staging", "new")
    _put_dir(pdir, ".demo.update-backup", "old")
    _recover_interrupted_updates(pdir)
    assert (pdir / "demo" / "marker.txt").read_text() == "new"
    assert not (pdir / ".demo.update-staging").exists()
    assert not (pdir / ".demo.update-backup").exists()


def test_recover_restores_backup_when_no_staging(tmp_path):
    # crash AFTER target->backup but BEFORE staging was ready: revert to old code
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    _put_dir(pdir, ".demo.update-backup", "old")
    _recover_interrupted_updates(pdir)
    assert (pdir / "demo" / "marker.txt").read_text() == "old"
    assert not (pdir / ".demo.update-backup").exists()


def test_recover_clears_leftovers_when_target_present(tmp_path):
    # a fully-completed swap left dot-dirs behind: drop them, keep the live plugin
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    _put_dir(pdir, "demo", "live")
    _put_dir(pdir, ".demo.update-staging", "x")
    _put_dir(pdir, ".demo.update-backup", "y")
    _recover_interrupted_updates(pdir)
    assert (pdir / "demo" / "marker.txt").read_text() == "live"
    assert not (pdir / ".demo.update-staging").exists()
    assert not (pdir / ".demo.update-backup").exists()


def test_read_zip_manifest_extracts_id(tmp_path):
    p = _write_plugin(tmp_path, "alpha", "1.0.0", {})
    zf, meta, pid = _read_zip_manifest(_bytes_of(p))
    assert pid == "alpha"
    assert meta["version"] == "1.0.0"


def test_read_zip_manifest_rejects_missing_manifest():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("random.txt", "nope")
    with pytest.raises(ValueError, match="manifest"):
        _read_zip_manifest(buf.getvalue())


def test_reject_unsafe_zip_paths():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(ValueError, match="perigoso"):
        _reject_unsafe_zip_paths(zipfile.ZipFile(io.BytesIO(buf.getvalue())))


def _bytes_of(plugin_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in plugin_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(plugin_dir).as_posix())
    return buf.getvalue()
