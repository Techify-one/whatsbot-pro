"""Roster notices ("X entrou no grupo") carry a linkable participant token.

``describe_change`` emits ``[[member:<phone>|<name>]]`` for every participant whose
phone is known, so the panel can turn the name into a link to "Novo contato"
(web/static/js/services/systemMemberLinks.js parses the SAME token). A participant
with no resolvable phone — a lid-addressed member no roster ever mapped — stays
plain text: linking the opaque lid would create a "contact" for a number that
doesn't exist.

No DB and no HTTP: name layers are stubbed, the roster comes from a fake client.
"""

from __future__ import annotations

import pytest

from agent import group_mentions as gm

GROUP = "120363999990000@g.us"
PHONE = "5511999999999"
LID = "199998887776665"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Fresh module state + every name layer stubbed (no DB, no GOWA)."""
    monkeypatch.setattr(gm, "_client", object())
    monkeypatch.setattr(gm, "_members_cache", {})
    monkeypatch.setattr(gm, "_pushname_cache", {})
    monkeypatch.setattr(gm, "_pushname_attempted", set())
    monkeypatch.setattr(gm, "_lid_phone", {})
    monkeypatch.setattr(gm, "_store_map", lambda client=None: {})
    monkeypatch.setattr(gm, "_fetch_pushname", lambda jid, client=None: "")
    names: dict[str, str] = {}
    monkeypatch.setattr(gm, "_saved_name", lambda phone: names.get(phone, ""))
    return names


@pytest.fixture
def saved(_isolated):
    return _isolated


def _jid(phone: str = PHONE) -> str:
    return f"{phone}@s.whatsapp.net"


def test_join_links_a_named_participant(saved):
    saved[PHONE] = "Thiago Carvalho"
    assert gm.describe_change("join", [_jid()]) == (
        f"[[member:{PHONE}|Thiago Carvalho]] entrou no grupo")


def test_leave_links_too(saved):
    saved[PHONE] = "Thiago Carvalho"
    assert gm.describe_change("leave", [_jid()]) == (
        f"[[member:{PHONE}|Thiago Carvalho]] saiu do grupo")


def test_several_participants_each_get_a_token(saved):
    saved[PHONE] = "Ana"
    saved["5522888888888"] = "Bruno"
    text = gm.describe_change("join", [_jid(), _jid("5522888888888")])
    assert text == (f"[[member:{PHONE}|Ana]], [[member:5522888888888|Bruno]] "
                    "entraram no grupo")


def test_nameless_participant_is_labelled_with_the_phone():
    assert gm.describe_change("join", [_jid()]) == (
        f"[[member:{PHONE}|+{PHONE}]] entrou no grupo")


def test_lid_without_a_known_phone_stays_plain(saved):
    saved[LID] = "Thiago Carvalho"           # a "contact" keyed by the lid digits
    text = gm.describe_change("join", [f"{LID}@lid"])
    assert text == "Thiago Carvalho entrou no grupo"
    assert "[[member" not in text


def test_lid_resolved_by_a_roster_links_the_real_phone(saved):
    """get_members learns lid→phone; a later ``leave`` (roster already dropped the
    member) still links to the phone, not to the lid."""
    class _Client:
        def get_group_info(self, jid):
            return {"Participants": [
                {"PhoneNumber": f"{PHONE}@s.whatsapp.net", "LID": f"{LID}@lid"}]}

    gm.get_members(GROUP, force=True, client=_Client())
    saved[PHONE] = "Thiago Carvalho"
    assert gm.describe_change("leave", [f"{LID}@lid"]) == (
        f"[[member:{PHONE}|Thiago Carvalho]] saiu do grupo")


def test_nameless_lid_shows_the_phone_not_the_lid():
    gm._lid_phone[LID] = PHONE
    assert gm.describe_change("leave", [f"{LID}@lid"]) == (
        f"[[member:{PHONE}|+{PHONE}]] saiu do grupo")


def test_brackets_in_a_name_cannot_close_the_token(saved):
    saved[PHONE] = "Ana ]] [x"
    text = gm.describe_change("join", [_jid()])
    assert text == f"[[member:{PHONE}|Ana  x]] entrou no grupo"


def test_implausible_number_is_not_linked(saved):
    saved["12345"] = "Curto"
    assert gm.describe_change("join", [_jid("12345")]) == "Curto entrou no grupo"


def test_no_nameable_participant_gives_no_notice():
    assert gm.describe_change("join", []) == ""
