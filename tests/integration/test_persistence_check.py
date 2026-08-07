"""Boot-time storage persistence self-check (server/persistence_check.py).

The ``WHATSBOT_TEST`` guard makes ``ensure_storage_persistence`` a no-op in the
suite (so it never writes a marker into the repo's ``storages/``), so we exercise
the real detection via ``_run_check`` directly, against an isolated tmp dir + the
Postgres TEST database (``_engine_ready``). The sentinel token lives in the shared
``config`` table, so each test cleans the key before and after.

Log assertions attach a handler straight to ``pc.logger`` (not ``caplog``) because
the app's logging config disables propagation to the root logger.
"""

from __future__ import annotations

import contextlib
import logging

import pytest

from db.repositories import config_repo
from server import persistence_check as pc


@pytest.fixture
def clean_token():
    config_repo.delete_prefix(pc._CONFIG_KEY)
    yield
    config_repo.delete_prefix(pc._CONFIG_KEY)


@contextlib.contextmanager
def capture_errors():
    """Collect ERROR-level records emitted by the module logger during the block.

    Forces ``pc.logger`` enabled + DEBUG for the duration: the pytest harness marks
    pre-existing module loggers ``disabled=True`` (an artifact — production uses
    ``logging.basicConfig`` and never disables loggers, so the warning emits there).
    """
    records: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _H()
    handler.setLevel(logging.ERROR)
    prev_level, prev_disabled = pc.logger.level, pc.logger.disabled
    pc.logger.setLevel(logging.DEBUG)
    pc.logger.disabled = False
    pc.logger.addHandler(handler)
    try:
        yield records
    finally:
        pc.logger.removeHandler(handler)
        pc.logger.setLevel(prev_level)
        pc.logger.disabled = prev_disabled


def test_first_boot_initializes(_engine_ready, clean_token, tmp_path):
    marker = tmp_path / pc._MARKER_NAME
    with capture_errors() as errs:
        status = pc._run_check(tmp_path)
    assert status == "initialized"
    assert marker.is_file()
    token = marker.read_text(encoding="utf-8").strip()
    assert token
    # DB and disk carry the same token; no loud error on a clean first boot.
    assert config_repo.get(pc._CONFIG_KEY) == token
    assert not errs


def test_second_boot_persistent(_engine_ready, clean_token, tmp_path):
    pc._run_check(tmp_path)                    # first boot establishes the token
    with capture_errors() as errs:
        status = pc._run_check(tmp_path)       # same dir + same DB → no wipe
    assert status == "persistent"
    assert not errs


def test_wiped_disk_is_ephemeral(_engine_ready, clean_token, tmp_path):
    pc._run_check(tmp_path)                    # establish token on disk + DB
    (tmp_path / pc._MARKER_NAME).unlink()      # simulate a redeploy wiping storages/
    with capture_errors() as errs:
        status = pc._run_check(tmp_path)       # DB token survives, disk marker gone
    assert status == "ephemeral"
    assert errs and "NÃO é persistente" in errs[0].getMessage()
    # Re-anchored so the marker exists again within this container lifetime.
    assert (tmp_path / pc._MARKER_NAME).is_file()


def test_db_reset_disk_survived(_engine_ready, clean_token, tmp_path):
    pc._run_check(tmp_path)                     # establish token on disk + DB
    config_repo.delete_prefix(pc._CONFIG_KEY)   # simulate DB reset, disk intact
    status = pc._run_check(tmp_path)
    assert status == "persistent"
    # DB token re-seeded from the surviving disk marker.
    disk = (tmp_path / pc._MARKER_NAME).read_text(encoding="utf-8").strip()
    assert config_repo.get(pc._CONFIG_KEY) == disk


def test_ensure_is_noop_under_test_env(_engine_ready, tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSBOT_TEST", "1")
    status = pc.ensure_storage_persistence(tmp_path)
    assert status == "unknown"
    # The guard means nothing is written to disk.
    assert not (tmp_path / pc._MARKER_NAME).exists()
