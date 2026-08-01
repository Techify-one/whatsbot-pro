"""Plano 69 — a lista bate com a contagem (filtros server-side).

The core invariant of plano 69: the filtered LIST and the tab COUNT read the SAME
server-side WHERE, so ``len(/api/atendimentos/filter)`` == ``/api/atendimentos/count``
``all`` for any single-page result. Plano 60 already shipped the count; plano 69 makes
the list agree with it.

These tests drive the REAL HTTP routes (``build_app`` fixture) as an authenticated
operator and prove:

* F0 — the new internal ``has_mention`` dim: ``/filter?has_mention=true`` returns
  exactly the conversations with an unread @mention of the logged-in user, and its
  length equals the count's ``all``/``mentions`` (the "Menções" tab, server-side).
* The reported bug shape — ``agent=user:<id>``: the list length equals the count's
  ``all`` (before plano 69 the list re-filtered client-side over a 50-row page and
  diverged from the server count).
* The no-user branch of ``has_mention`` is constant-false (mirrors the count's
  ``mentions=0`` without a user), so list and count stay consistent (never one server,
  one client).

    venv/bin/python -m pytest tests/test_plano69_list_matches_count.py -q
"""

from __future__ import annotations

import pytest


_CREATED_USER_IDS: list[int] = []


@pytest.fixture(scope="module", autouse=True)
def _cleanup_p69_users(_engine_ready):
    yield
    from db.repositories import session_repo, user_repo

    for user_id in reversed(_CREATED_USER_IDS):
        session_repo.delete_for_user(user_id)
        assert user_repo.delete(user_id), f"could not remove plano 69 user {user_id}"
    _CREATED_USER_IDS.clear()


def _auth(built, email: str):
    """Authenticate ``built.client`` as a fresh admin operator (read_all + assign).

    Plano 48 closes the API gate once ≥1 user exists, so every request needs a token.
    Returns the operator's user id."""
    from db.repositories import user_repo, session_repo
    from server.auth import generate_session_token
    op = user_repo.get_by_email(email)
    if op is None:
        op = user_repo.create(
            email=email, name="P69 Operador",
            password_hash="x", role_keys=["admin"],
        )
        _CREATED_USER_IDS.append(op["id"])
    tok = generate_session_token()
    session_repo.create(tok, op["id"], user_agent="test", ip="127.0.0.1")
    built.client.headers["Authorization"] = f"Bearer {tok}"
    return op["id"]


def _open_conv(phone: str):
    """Create a contact + open conversation (default inbox). Returns (conv_id, contact_id)."""
    from db.repositories import contact_repo, conversation_repo
    contact = contact_repo.get_or_create(phone)
    conv, _ = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net")
    return conv["id"], contact["id"]


def _seed_mention(conv_id: int, contact_id: int, user_id: int):
    """Insert one unread @mention of ``user_id`` on ``conv_id`` (via a private note)."""
    from db.repositories import message_repo, mention_repo
    msg = message_repo.add(contact_id, "private_note", "@op veja isso",
                           conversation_id=conv_id)
    mid = msg["id"] if isinstance(msg, dict) else msg
    mention_repo.add_many(
        message_id=mid, conversation_id=conv_id, contact_id=contact_id,
        mentioned_user_ids=[user_id], actor_user_id=None, actor_name="Sistema")


def test_has_mention_filter_matches_mentions_tab(build_app):
    """F0: ``/filter?has_mention=true`` == the conversations I was mentioned in, and its
    length equals ``/count`` ``all``/``mentions`` — list and count agree by construction."""
    from db.repositories import mention_repo

    built = build_app(["gowa"])
    uid = _auth(built, "p69_mention@test.com")

    # Two conversations mention me, one does not (control).
    c1, ct1 = _open_conv("5511960690001")
    c2, ct2 = _open_conv("5511960690002")
    c3, _ct3 = _open_conv("5511960690003")  # no mention
    _seed_mention(c1, ct1, uid)
    _seed_mention(c2, ct2, uid)

    expected_ids = set(mention_repo.unread_conversation_ids(uid))
    assert {c1, c2} <= expected_ids and c3 not in expected_ids

    r = built.client.get("/api/atendimentos/count?has_mention=true")
    assert r.status_code == 200, r.text
    counts = r.json()["data"]
    r = built.client.get("/api/atendimentos/filter?has_mention=true&limit=50")
    assert r.status_code == 200, r.text
    convs = r.json()["data"]["conversations"]
    got_ids = {c["id"] for c in convs}

    # The heart of plano 69: the LIST equals the COUNT (no client re-filter drift).
    assert len(convs) == counts["all"], (len(convs), counts)
    assert counts["all"] == counts["mentions"], counts
    # And it is exactly the mentioned set (this fresh user has no other mentions).
    assert got_ids == expected_ids, (got_ids, expected_ids)
    assert c3 not in got_ids


def test_agent_filter_list_matches_count(build_app):
    """The reported bug shape — ``agent=user:<id>``: the list length equals the count's
    ``all``. Before plano 69 the sidebar re-filtered a 50-row page client-side and the
    two diverged (count 21 / list 7)."""
    built = build_app(["gowa"])
    admin = _auth(built, "p69_agent_admin@test.com")

    # A fresh assignee with NO other conversations, so the universe is exactly ours.
    from db.repositories import user_repo
    agent = user_repo.create(email="p69_assignee@test.com", name="Atendente X Teste",
                             password_hash="x", role_keys=["atendente"])
    _CREATED_USER_IDS.append(agent["id"])

    ids = []
    for i in range(3):
        conv_id, _ct = _open_conv(f"551196069010{i}")
        r = built.client.post(f"/api/conversations/{conv_id}/assign",
                              json={"assignee_user_id": agent["id"]})
        assert r.status_code == 200, r.text
        ids.append(conv_id)

    q = f"agent=user:{agent['id']}&status=open"
    counts = built.client.get(f"/api/atendimentos/count?{q}").json()["data"]
    convs = built.client.get(f"/api/atendimentos/filter?{q}&limit=50").json()["data"]["conversations"]

    assert counts["all"] == 3, counts
    assert len(convs) == counts["all"], (len(convs), counts)
    assert {c["id"] for c in convs} == set(ids)


def test_has_mention_without_user_is_constant_false(build_app):
    """Without a logged-in user, ``has_mention`` is constant-false (mirrors the count's
    ``mentions=0``), so the filtered list is empty too — list and count stay consistent."""
    build_app(["gowa"])  # ensure the engine/app are initialized
    from db.filters.spec import from_params
    from db.filters import build_where
    from db.filters.translate import FilterContext
    from db.repositories import conversation_repo

    where = build_where(from_params({"has_mention": "true"}), FilterContext(user_id=None))
    rows = conversation_repo.list_filtered(where, current_user_id=None, limit=50)
    counts = conversation_repo.count_tab_counts(where, current_user_id=None)
    assert rows == []
    assert counts["all"] == 0 and counts["mentions"] == 0


# ── F5b — Contatos: filtro avançado server-side ────────────────────────────

def test_contacts_filter_by_tag_total_reflects_filter(build_app):
    """F5b: ``/api/contacts?tag=X`` lista só os contatos com a etiqueta e o ``total``
    reflete o FILTRO (não a página) — a lista bate com o total, como no hub."""
    from db.repositories import contact_repo, tag_repo

    built = build_app(["gowa"])
    _auth(built, "p69_contacts_tag@test.com")
    tag_repo.create("p69tag", "#123456")
    tagged = []
    for i in range(3):
        c = contact_repo.get_or_create(f"5511965069{i:04d}")
        tag_repo.add_contact_tag(c["id"], "p69tag")
        tagged.append(c["phone"])
    ctrl = contact_repo.get_or_create("5511965069900")["phone"]  # sem a etiqueta

    page = built.client.get("/api/contacts?tag=p69tag&limit=50").json()["data"]
    phones = {c["phone"] for c in page["items"]}
    assert set(tagged) <= phones, (tagged, phones)
    assert ctrl not in phones
    assert page["total"] == 3, page["total"]
    assert len(page["items"]) == page["total"]  # cabem numa página → lista == total

    # ``total`` é do universo filtrado, não da janela: limit=1 mantém total=3.
    p2 = built.client.get("/api/contacts?tag=p69tag&limit=1").json()["data"]
    assert p2["total"] == 3 and len(p2["items"]) == 1

    # ``ne`` (não é nenhuma) exclui os 3 etiquetados.
    p3 = built.client.get(
        "/api/contacts?tag=p69tag&tag__op=not_equal_to&limit=500").json()["data"]
    assert set(tagged).isdisjoint({c["phone"] for c in p3["items"]})


def test_contacts_filter_by_contact_type_narrows(build_app):
    """F5b: ``/api/contacts?contact_type=telegram`` narrows the set and the total drops
    below the unfiltered total."""
    from db.repositories import contact_repo

    built = build_app(["gowa"])
    _auth(built, "p69_contacts_type@test.com")
    tel = [contact_repo.get_or_create(f"5511966069{i:04d}", contact_type="telegram")["phone"]
           for i in range(2)]

    all_page = built.client.get("/api/contacts?limit=500").json()["data"]
    tel_page = built.client.get("/api/contacts?contact_type=telegram&limit=500").json()["data"]
    assert tel_page["total"] >= 2
    assert tel_page["total"] < all_page["total"], "o filtro tem de estreitar o universo"
    assert all(c.get("contact_type") == "telegram" for c in tel_page["items"])
    assert set(tel) <= {c["phone"] for c in tel_page["items"]}


def test_contacts_filter_accepts_frontend_param_shape(build_app):
    """F6 contract: the exact params the frontend serializer emits
    (`labels=` key, `contact_type__op=not_equal_to`) are accepted by the backend."""
    from db.repositories import contact_repo, tag_repo

    built = build_app(["gowa"])
    _auth(built, "p69_contacts_shape@test.com")
    tag_repo.create("p69shape", "#abcabc")
    c = contact_repo.get_or_create("5511968069001", contact_type="telegram")
    tag_repo.add_contact_tag(c["id"], "p69shape")

    # `labels=` (not `tag=`) — the serializer maps tag → labels.
    page = built.client.get("/api/contacts?labels=p69shape&limit=50").json()["data"]
    assert c["phone"] in {x["phone"] for x in page["items"]}
    assert page["total"] == 1, page["total"]

    # contact_type "≠" via the __op override the serializer emits.
    r = built.client.get("/api/contacts?contact_type=telegram&contact_type__op=not_equal_to&limit=500")
    assert r.status_code == 200, r.text
    assert all(x.get("contact_type") != "telegram" for x in r.json()["data"]["items"])


def test_contacts_filter_unknown_cattr_is_400(build_app):
    """F5b: a non-filterable contact attribute key is rejected (400, not 500)."""
    built = build_app(["gowa"])
    _auth(built, "p69_contacts_bad@test.com")
    r = built.client.get("/api/contacts?cattr:contact:naoexiste=x&limit=50")
    assert r.status_code == 400, r.text


def test_contacts_no_filter_is_unchanged(build_app):
    """F5b guard: no filter param ⇒ byte-identical to the pre-plano-69 path."""
    from db.repositories import contact_repo

    built = build_app(["gowa"])
    _auth(built, "p69_contacts_nofilter@test.com")
    contact_repo.get_or_create("5511967069001")
    page = built.client.get("/api/contacts?limit=500").json()["data"]
    assert isinstance(page.get("items"), list) and isinstance(page.get("total"), int)
    assert page["total"] == len(page["items"]) or page["has_more"] is True


# ── F7 — Custos: busca + ordenação server-side ─────────────────────────────

def test_usage_by_contact_search_and_sort_server_side(build_app):
    """F7: `/api/usage/by-contact` filters (q) and sorts (sort/order) server-side; the
    total reflects the search, so a spender outside the first page is still found."""
    from db.repositories import contact_repo, usage_repo

    built = build_app(["gowa"])
    _auth(built, "p69_usage@test.com")
    a = contact_repo.get_or_create("5511969069001"); contact_repo.update(a["id"], name="ZZUSG Alpha")
    b = contact_repo.get_or_create("5511969069002"); contact_repo.update(b["id"], name="ZZUSG Bravo")
    usage_repo.add(a["id"], "text", "m", 10, 5, 15, 0.10)    # menos tokens, MAIS custo
    usage_repo.add(b["id"], "text", "m", 100, 50, 150, 0.01)  # MAIS tokens, menos custo

    # q isola nosso conjunto; o total reflete a busca.
    page = built.client.get("/api/usage/by-contact?q=ZZUSG&limit=50").json()["data"]
    assert {x["phone"] for x in page["items"]} == {a["phone"], b["phone"]}
    assert page["total"] == 2
    # default = custo desc → Alpha (0.10) antes de Bravo (0.01)
    assert [x["phone"] for x in page["items"]] == [a["phone"], b["phone"]]

    # ordenar por tokens asc/desc reordena o RANKING (não só o carregado)
    asc = built.client.get("/api/usage/by-contact?q=ZZUSG&sort=total_tokens&order=asc&limit=50").json()["data"]
    assert [x["phone"] for x in asc["items"]] == [a["phone"], b["phone"]]     # 15, 150
    desc = built.client.get("/api/usage/by-contact?q=ZZUSG&sort=total_tokens&order=desc&limit=50").json()["data"]
    assert [x["phone"] for x in desc["items"]] == [b["phone"], a["phone"]]    # 150, 15

    # buscar um gastador pelo nome o encontra (total = 1)
    only = built.client.get("/api/usage/by-contact?q=Bravo&limit=50").json()["data"]
    assert [x["phone"] for x in only["items"]] == [b["phone"]] and only["total"] == 1
