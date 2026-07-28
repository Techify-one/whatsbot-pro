"""Plano 42 A2 — migration 0046 (``_strip_nongowa_source_ids``).

Strips the WhatsApp JID suffix from ``contact_inboxes.source_id``/``source_jid``
for NON-GOWA inboxes and consolidates collisions (re-point conversations to the
canonical row, delete the duplicate). The migration itself runs at schema build on
an EMPTY table, so here we exercise the helper directly on seeded data.

    venv/bin/python -m pytest tests/test_migration_0047_source_id.py -q
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import select, text

from db.engine import get_engine
from db.tables import atendimentos, contact_inboxes
from db.repositories import (channel_repo, contact_inbox_repo, contact_repo,
                             conversation_repo, inbox_repo)

_MIG_PATH = (pathlib.Path(__file__).resolve().parent.parent
             / "db/alembic/versions/20260710_0047_source_id_native.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_0046_test", _MIG_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ci(ci_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contact_inboxes.c.source_id, contact_inboxes.c.source_jid)
            .where(contact_inboxes.c.id == ci_id)).mappings().first()
    return dict(row) if row else None


def _conv_contact_inbox(conv_id: int):
    with get_engine().connect() as conn:
        return conn.execute(
            select(atendimentos.c.contact_inbox_id)
            .where(atendimentos.c.id == conv_id)).scalar_one_or_none()


@pytest.fixture
def mig_env(build_app):
    build_app(["gowa"])
    created = {"channels": [], "contacts": []}

    def mk_channel(cid: str, provider: str) -> int:
        created["channels"].append(cid)  # track for cleanup even if pre-existing
        if channel_repo.get(cid) is None:
            channel_repo.create(id=cid, provider=provider, display_name=cid, enabled=1)
        ib = inbox_repo.get_by_channel(cid) or inbox_repo.create(channel_id=cid, name=cid)
        return ib["id"]

    def mk_contact(phone: str) -> int:
        c = contact_repo.get_or_create(phone)
        created["contacts"].append(c["id"])
        return c["id"]

    yield mk_channel, mk_contact

    # cleanup: deleting a channel cascades inboxes -> contact_inboxes -> atendimentos
    with get_engine().begin() as conn:
        for cid in created["channels"]:
            conn.execute(text("DELETE FROM channels WHERE id = :id"), {"id": cid})
        for c_id in set(created["contacts"]):
            conn.execute(text("DELETE FROM contacts WHERE id = :id"), {"id": c_id})


def test_migration_strips_and_consolidates(mig_env):
    mk_channel, mk_contact = mig_env
    mod = _load_migration()

    cloud_ib = mk_channel("mig0046_cloud", "whatsapp_cloud")
    gowa_ib = mk_channel("mig0046_gowa", "gowa")

    # (a) plain non-GOWA suffixed row -> stripped to bare
    c1 = mk_contact("5511900000101")
    plain = contact_inbox_repo.get_or_create(
        inbox_id=cloud_ib, contact_id=c1, source_id="5511900000101@s.whatsapp.net")

    # (b) collision: bare (inserted first => canonical MIN(id)) + suffixed dup with a
    #     conversation on the DUP -> after: dup deleted, conv re-pointed to canonical.
    c2 = mk_contact("5511900000102")
    canon = contact_inbox_repo.get_or_create(
        inbox_id=cloud_ib, contact_id=c2, source_id="5511900000102")
    dup = contact_inbox_repo.get_or_create(
        inbox_id=cloud_ib, contact_id=c2, source_id="5511900000102@s.whatsapp.net")
    conv = conversation_repo.create(
        inbox_id=cloud_ib, contact_id=c2, contact_inbox_id=dup["id"])

    # (c) GOWA suffixed row -> untouched (that IS its native id)
    c3 = mk_contact("5511900000103")
    gowa_ci = contact_inbox_repo.get_or_create(
        inbox_id=gowa_ib, contact_id=c3, source_id="5511900000103@s.whatsapp.net")

    with get_engine().begin() as conn:
        mod._strip_nongowa_source_ids(conn)

    # (a) both columns stripped
    p = _ci(plain["id"])
    assert p["source_id"] == "5511900000101"
    assert p["source_jid"] == "5511900000101"

    # (b) canonical kept + bare; duplicate deleted; conversation re-pointed
    assert _ci(canon["id"])["source_id"] == "5511900000102"
    assert _ci(dup["id"]) is None
    assert _conv_contact_inbox(conv["id"]) == canon["id"]

    # (c) GOWA row is byte-identical (still suffixed)
    assert _ci(gowa_ci["id"])["source_id"] == "5511900000103@s.whatsapp.net"
