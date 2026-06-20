"""Comprehensive endpoint tests for WhatsBot API.

Uses FastAPI TestClient with a real temporary SQLite database.
No external services needed (GOWA/OpenRouter are mocked).
"""

import io
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Initialize the engine in a temp directory before importing anything else.
# Default: SQLite. Override with ``WHATSBOT_TEST_DB_URL`` to run the same
# assertions against Postgres (e.g. via testcontainers).
_tmpdir = tempfile.mkdtemp(prefix="whatsbot_test_")
_db_path = Path(_tmpdir) / "whatsbot.db"

from db import init_db, init_engine

_test_url = os.environ.get("WHATSBOT_TEST_DB_URL", "").strip()
if _test_url:
    init_engine(_test_url)
    from db.connection import _run_alembic_upgrade  # noqa: E402
    _run_alembic_upgrade()
else:
    init_db(_db_path)

# Seed some test data
from db.repositories import contact_repo, message_repo, usage_repo, tag_repo, config_repo, execution_repo

def _seed_data():
    """Insert test contacts, messages, tags, usage into the test DB."""
    now = time.time()

    # Create contacts
    c1 = contact_repo.get_or_create("5511999990001")
    contact_repo.update(c1["id"], name="Alice Test", email="alice@test.com",
                        profession="Engineer", company="TestCo")

    c2 = contact_repo.get_or_create("5511999990002")
    contact_repo.update(c2["id"], name="Bob Test", is_archived=True)

    # Add messages
    message_repo.add(c1["id"], "user", "Olá, tudo bem?", ts=now - 100)
    message_repo.add(c1["id"], "assistant", "Tudo sim! Como posso ajudar?", ts=now - 90)
    message_repo.add(c1["id"], "user", "Qual o horário de funcionamento?", ts=now - 50)
    message_repo.add(c1["id"], "assistant", "Nosso horário é de 9h às 18h.", ts=now - 40)

    message_repo.add(c2["id"], "user", "Oi", ts=now - 200)

    # Add observations
    contact_repo.add_observation(c1["id"], "Cliente VIP")

    # Add tags
    tag_repo.create("vip", "#ff0000")
    tag_repo.create("lead", "#00ff00")
    tag_repo.add_contact_tag(c1["id"], "vip")

    # Add usage
    usage_repo.add(c1["id"], "text", "openai/gpt-4o-mini", 100, 50, 150, 0.001)
    usage_repo.add(c1["id"], "text", "openai/gpt-4o-mini", 200, 80, 280, 0.002)

    # Increment unread for c2
    contact_repo.increment_unread(c2["id"], "msg_001")

_seed_data()

# Now import app components
from config.settings import Settings
from agent.handler import AgentHandler
from server.app import create_app

# Create mocks for GOWA
mock_gowa_manager = MagicMock()
mock_gowa_client = MagicMock()
mock_gowa_client.send_message = MagicMock(return_value=None)
mock_gowa_client.send_image = MagicMock(return_value=None)
mock_gowa_client.send_audio = MagicMock(return_value=None)
mock_gowa_client.send_file = MagicMock(return_value=None)
mock_gowa_client.send_chat_presence = MagicMock(return_value=None)
mock_gowa_client.mark_as_read = MagicMock(return_value=None)
mock_gowa_client.revoke_message = MagicMock(return_value=None)
mock_gowa_client.delete_message = MagicMock(return_value=None)
mock_gowa_client.react_to_message = MagicMock(return_value=None)
mock_gowa_client.reconnect = MagicMock(return_value=None)
mock_gowa_client.logout = MagicMock(return_value=None)
mock_gowa_client.get_own_number = MagicMock(return_value="5511999990001")

# Create real Settings and AgentHandler (backed by test DB)
settings = Settings()
agent_handler = AgentHandler(
    api_key="test-key-fake",
    system_prompt="Você é um assistente de teste.",
    max_context_messages=10,
    model="openai/gpt-4o-mini",
)

# Create the app (skip lifespan to avoid background tasks)
app = create_app(
    settings=settings,
    gowa_manager=mock_gowa_manager,
    gowa_client=mock_gowa_client,
    agent_handler=agent_handler,
)

# Patch lifespan to be a no-op for testing
from contextlib import asynccontextmanager

@asynccontextmanager
async def _noop_lifespan(app):
    yield

app.router.lifespan_context = _noop_lifespan

from starlette.testclient import TestClient
client = TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════
#  Test runner
# ═══════════════════════════════════════════════════════════════════

passed = 0
failed = 0
errors = []


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        msg = f"  FAIL {name}" + (f" -- {detail}" if detail else "")
        print(msg)
        errors.append(msg)


def section(title: str):
    print(f"\n{'-'*60}")
    print(f"  {title}")
    print(f"{'-'*60}")


# ═══════════════════════════════════════════════════════════════════
#  1. Health
# ═══════════════════════════════════════════════════════════════════
section("Health")
r = client.get("/health")
check("GET /health -> 200", r.status_code == 200)
check("GET /health -> ok=true", r.json().get("ok") is True)

# ═══════════════════════════════════════════════════════════════════
#  2. Auth
# ═══════════════════════════════════════════════════════════════════
section("Auth")

r = client.get("/api/auth/check")
check("GET /api/auth/check (no password) -> authenticated", r.json()["data"]["authenticated"] is True)
check("GET /api/auth/check -> has_password=false", r.json()["data"]["has_password"] is False)

r = client.post("/api/auth/login", json={"password": "test"})
check("POST /api/auth/login (no pw set) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  3. Config
# ═══════════════════════════════════════════════════════════════════
section("Config")

r = client.get("/api/config")
check("GET /api/config -> 200", r.status_code == 200)
data = r.json()["data"]
check("GET /api/config -> has model field", "model" in data)
check("GET /api/config -> has API key field", "openrouter_api_key" in data)
check("GET /api/config -> has system_prompt", "system_prompt" in data)
check("GET /api/config -> has split_messages", "split_messages" in data)
check("GET /api/config -> has has_password", "has_password" in data)
check("GET /api/config -> has setup_completed", "setup_completed" in data)

r = client.put("/api/config", json={"auto_reply": False})
check("PUT /api/config -> 200", r.status_code == 200)
check("PUT /api/config -> saved", r.json()["data"]["message"] == "Configurações salvas!")

r = client.get("/api/config")
check("PUT /api/config -> auto_reply persisted", r.json()["data"]["auto_reply"] is False)

# Restore
client.put("/api/config", json={"auto_reply": True})

# group_reply_mode is exposed and round-trips (controls AI replies in groups)
check("GET /api/config -> has group_reply_mode", "group_reply_mode" in data)
r = client.put("/api/config", json={"group_reply_mode": "always"})
check("PUT /api/config (group_reply_mode) -> 200", r.status_code == 200)
r = client.get("/api/config")
check("PUT /api/config -> group_reply_mode persisted",
      r.json()["data"]["group_reply_mode"] == "always")
client.put("/api/config", json={"group_reply_mode": "mention_only"})

# setup_completed flag round-trips through PUT
r = client.put("/api/config", json={"setup_completed": True})
check("PUT /api/config (setup_completed) -> 200", r.status_code == 200)
r = client.get("/api/config")
check("PUT /api/config -> setup_completed persisted", r.json()["data"]["setup_completed"] is True)
client.put("/api/config", json={"setup_completed": False})  # restore

# Test key (will fail since no real API)
r = client.post("/api/config/test-key", json={"api_key": ""})
check("POST /api/config/test-key (empty) -> error", r.json()["ok"] is False)

# ═══════════════════════════════════════════════════════════════════
#  4. Status
# ═══════════════════════════════════════════════════════════════════
section("Status")

r = client.get("/api/status")
check("GET /api/status -> 200", r.status_code == 200)
data = r.json()["data"]
check("GET /api/status -> has connected", "connected" in data)
check("GET /api/status -> has msg_count", "msg_count" in data)
check("GET /api/status -> has auto_reply_running", "auto_reply_running" in data)

# ═══════════════════════════════════════════════════════════════════
#  5. Contacts list
# ═══════════════════════════════════════════════════════════════════
section("Contacts — List")

r = client.get("/api/contacts")
check("GET /api/contacts -> 200", r.status_code == 200)
contacts_data = r.json()["data"]
check("GET /api/contacts -> is list", isinstance(contacts_data, list))
non_archived = [c for c in contacts_data if not c.get("is_archived")]
check("GET /api/contacts -> has non-archived contacts", len(non_archived) >= 1)
check("GET /api/contacts -> exposes avatar_v (cache-busting)", all("avatar_v" in c for c in contacts_data))

# Search
r = client.get("/api/contacts?q=Alice")
check("GET /api/contacts?q=Alice -> finds Alice", len(r.json()["data"]) >= 1)

r = client.get("/api/contacts?q=xyznotexist")
check("GET /api/contacts?q=xyz -> empty", len(r.json()["data"]) == 0)

# Accent-insensitive search: an unaccented query matches accented names (both ways)
_acc = contact_repo.get_or_create("5511999990009")
contact_repo.update(_acc["id"], name="Ótavio Açaí")
r = client.get("/api/contacts?q=otavio")
check("GET /api/contacts?q=otavio -> matches 'Ótavio' (no accent)",
      any(c["phone"] == "5511999990009" for c in r.json()["data"]))
r = client.get("/api/contacts?q=AÇAÍ")
check("GET /api/contacts?q=AÇAÍ -> matches 'Açaí' (accented, any case)",
      any(c["phone"] == "5511999990009" for c in r.json()["data"]))

# Search by message content (normal, private note, transcription)
_msc = contact_repo.get_or_create("5511999990010")
contact_repo.update(_msc["id"], name="Sem Termo No Nome")
_orc = message_repo.add(_msc["id"], "user", "preciso do orçamento atualizado")
message_repo.add(_msc["id"], "private_note", "lembrete: cobrar pagamento amanhã")
message_repo.add(_msc["id"], "transcription", "transcrição: reunião remarcada para quinta")
r = client.get("/api/contacts?q=orcamento")
_hit = next((c for c in r.json()["data"] if c["phone"] == "5511999990010"), None)
check("GET /api/contacts?q=orcamento -> matches by normal message (accent-insensitive)", _hit is not None)
check("GET /api/contacts?q=orcamento -> returns match snippet (original accents)",
      bool(_hit) and "orçamento" in _hit.get("match_snippet", ""))
check("GET /api/contacts?q=orcamento -> returns matched message id",
      bool(_hit) and _hit.get("match_msg_id") == _orc["id"])
r = client.get("/api/contacts?q=cobrar pagamento")
check("GET /api/contacts?q=cobrar pagamento -> matches by private note",
      any(c["phone"] == "5511999990010" for c in r.json()["data"]))
r = client.get("/api/contacts?q=remarcada")
check("GET /api/contacts?q=remarcada -> matches by transcription",
      any(c["phone"] == "5511999990010" for c in r.json()["data"]))
r = client.get("/api/contacts?q=conteudoinexistente123")
check("GET /api/contacts?q=conteudoinexistente123 -> empty",
      not any(c["phone"] == "5511999990010" for c in r.json()["data"]))

# Bot @mention flag: surfaces in the list and clears when the chat is read
_mc = contact_repo.get_or_create("5511999990011")
contact_repo.update(_mc["id"], is_group=1, group_name="Grupo Menção")
contact_repo.set_mention(_mc["id"])
r = client.get("/api/contacts")
_mhit = next((c for c in r.json()["data"] if c["phone"] == "5511999990011"), None)
check("GET /api/contacts -> has_unread_mention exposed", bool(_mhit) and _mhit.get("has_unread_mention") is True)
contact_repo.mark_as_read(_mc["id"])
r = client.get("/api/contacts")
_mhit2 = next((c for c in r.json()["data"] if c["phone"] == "5511999990011"), None)
check("mark_as_read -> mention flag cleared", bool(_mhit2) and _mhit2.get("has_unread_mention") is False)

# Unread-count endpoint (browser-tab badge). Static path must win over /{phone}.
r = client.get("/api/contacts/unread-count")
check("GET /api/contacts/unread-count -> 200", r.status_code == 200)
check("GET /api/contacts/unread-count -> int count", isinstance(r.json()["data"]["count"], int))
_uc = contact_repo.get_or_create("5511999990012")
contact_repo.mark_as_read(_uc["id"])
_before = client.get("/api/contacts/unread-count").json()["data"]["count"]
contact_repo.increment_unread(_uc["id"], "WAMID_UC_1")
_after = client.get("/api/contacts/unread-count").json()["data"]["count"]
check("unread-count -> increments with an unread conversation", _after == _before + 1)
contact_repo.mark_as_read(_uc["id"])
_cleared = client.get("/api/contacts/unread-count").json()["data"]["count"]
check("unread-count -> drops after read", _cleared == _before)

# Archived
r = client.get("/api/contacts?archived=true")
check("GET /api/contacts?archived=true -> has archived", len(r.json()["data"]) >= 1)
archived_names = [c.get("name", "") for c in r.json()["data"]]
check("GET /api/contacts?archived=true -> Bob is archived", any("Bob" in n for n in archived_names))

# Pin / unpin: pinned conversations sort to the top of the list
r = client.post("/api/contacts/5511999990001/pin", json={"pinned": True})
check("POST /pin -> 200", r.status_code == 200)
check("POST /pin -> pinned true", r.json()["data"]["pinned"] is True)
r = client.get("/api/contacts")
_list = r.json()["data"]
check("GET /api/contacts -> pinned contact is first", _list and _list[0]["phone"] == "5511999990001")
check("GET /api/contacts -> exposes is_pinned", _list[0].get("is_pinned") is True)
r = client.post("/api/contacts/5511999990001/pin", json={"pinned": False})
check("POST /pin (unpin) -> 200", r.status_code == 200 and r.json()["data"]["pinned"] is False)
r = client.post("/api/contacts/5511999990001/pin", json={})
check("POST /pin (no field) -> 400", r.status_code == 400)
r = client.post("/api/contacts/5511999999999/pin", json={"pinned": True})
check("POST /pin (unknown contact) -> 404", r.status_code == 404)

# ═══════════════════════════════════════════════════════════════════
#  6. Contact detail
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Detail")

r = client.get("/api/contacts/5511999990001")
check("GET /api/contacts/{phone} -> 200", r.status_code == 200)
data = r.json()["data"]
check("GET /api/contacts/{phone} -> has phone", data.get("phone") == "5511999990001")
check("GET /api/contacts/{phone} -> has name", data.get("name") == "Alice Test")
check("GET /api/contacts/{phone} -> has email", data.get("email") == "alice@test.com")
check("GET /api/contacts/{phone} -> has messages", isinstance(data.get("messages"), list))
check("GET /api/contacts/{phone} -> messages count", len(data["messages"]) == 4)
check("GET /api/contacts/{phone} -> has tags", isinstance(data.get("tags"), list))
check("GET /api/contacts/{phone} -> has observations", isinstance(data.get("info", {}).get("observations"), list))

# Non-existent contact — auto-creates on GET
r = client.get("/api/contacts/0000000000")
check("GET /api/contacts/0000 -> auto-create 200", r.status_code == 200 and r.json()["ok"])

# ═══════════════════════════════════════════════════════════════════
#  7. Contact send message
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Message")

r = client.post("/api/contacts/5511999990001/send", json={"message": "Teste manual"})
check("POST /send -> 200", r.status_code == 200)
check("POST /send -> message sent", "enviada" in r.json()["data"]["message"].lower())
check("POST /send -> gowa called", mock_gowa_client.send_message.called)

# Empty message
r = client.post("/api/contacts/5511999990001/send", json={"message": ""})
check("POST /send (empty) -> 400", r.status_code == 400)

# Reply (quote an existing message): reply_to is forwarded to GOWA and persisted
mock_gowa_client.send_message.reset_mock()
r = client.post("/api/contacts/5511999990001/send",
                json={"message": "Respondendo", "reply_to": "WAMID_QUOTE_1"})
check("POST /send (reply_to) -> 200", r.status_code == 200)
_args, _kwargs = mock_gowa_client.send_message.call_args
check("POST /send (reply_to) -> gowa got reply id",
      "WAMID_QUOTE_1" in (list(_args) + list(_kwargs.values())))
_reply_cid = contact_repo.get_full_contact("5511999990001")["id"]
_last = message_repo.get_last(_reply_cid)
check("POST /send (reply_to) -> persisted reply_to_msg_id",
      _last.get("reply_to_msg_id") == "WAMID_QUOTE_1")

# ═══════════════════════════════════════════════════════════════════
#  8. Contact retry send
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Retry Send")

r = client.post("/api/contacts/5511999990001/retry-send", json={"message": "Retry msg"})
check("POST /retry-send -> 200", r.status_code == 200)

r = client.post("/api/contacts/5511999990001/retry-send", json={"message": ""})
check("POST /retry-send (empty) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  8a. Delete message (scope me / all)
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Delete Message")

_del_cid = contact_repo.get_full_contact("5511999990001")["id"]

# scope=all on own (outgoing) message -> revoke for everyone
message_repo.add(_del_cid, "assistant", "Msg para apagar", msg_id="WAMID_DEL_1", status="operator")
mock_gowa_client.revoke_message.reset_mock()
r = client.post("/api/contacts/5511999990001/messages/delete",
                json={"msg_id": "WAMID_DEL_1", "scope": "all"})
check("POST /messages/delete (all) -> 200", r.status_code == 200)
check("POST /messages/delete (all) -> gowa revoke called", mock_gowa_client.revoke_message.called)
_m = message_repo.get_by_msg_id("WAMID_DEL_1")
check("POST /messages/delete (all) -> flagged revoked", bool(_m) and bool(_m.get("revoked")))
check("POST /messages/delete (all) -> content preserved", bool(_m) and bool(_m.get("content")))
check("POST /messages/delete (all) -> scope all", bool(_m) and _m.get("revoke_scope") == "all")

# scope=all on a contact (user) message -> rejected
message_repo.add(_del_cid, "user", "Msg do contato", msg_id="WAMID_USER_1")
r = client.post("/api/contacts/5511999990001/messages/delete",
                json={"msg_id": "WAMID_USER_1", "scope": "all"})
check("POST /messages/delete (all, user msg) -> 400", r.status_code == 400)

# scope=me by db_id (local message without msg_id) -> row kept, flagged revoked
_local = message_repo.add(_del_cid, "assistant", "Local sem msg_id")
r = client.post("/api/contacts/5511999990001/messages/delete",
                json={"db_id": _local["id"], "scope": "me"})
check("POST /messages/delete (me, db_id) -> 200", r.status_code == 200)
_kept = [m for m in message_repo.get_all(_del_cid) if m.get("_id") == _local["id"]]
check("POST /messages/delete (me) -> row kept", len(_kept) == 1)
check("POST /messages/delete (me) -> flagged revoked", bool(_kept and _kept[0].get("revoked")))
check("POST /messages/delete (me) -> content preserved", bool(_kept and _kept[0].get("content")))
check("POST /messages/delete (me) -> scope me", bool(_kept) and _kept[0].get("revoke_scope") == "me")

# missing identifiers -> 400
r = client.post("/api/contacts/5511999990001/messages/delete", json={"scope": "me"})
check("POST /messages/delete (no id) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  8b. React to message
# ═══════════════════════════════════════════════════════════════════
section("Contacts — React Message")

message_repo.add(_del_cid, "user", "Msg para reagir", msg_id="WAMID_REACT_1")

# Add a reaction
mock_gowa_client.react_to_message.reset_mock()
r = client.post("/api/contacts/5511999990001/messages/react",
                json={"msg_id": "WAMID_REACT_1", "emoji": "👍"})
check("POST /messages/react -> 200", r.status_code == 200)
check("POST /messages/react -> gowa called", mock_gowa_client.react_to_message.called)
check("POST /messages/react -> reactions in response", r.json()["data"]["reactions"].get("👍") == ["me"])
_m = message_repo.get_by_msg_id("WAMID_REACT_1")
check("POST /messages/react -> persisted", _m.get("reactions", {}).get("👍") == ["me"])

# Change reaction (replaces, one per reactor)
r = client.post("/api/contacts/5511999990001/messages/react",
                json={"msg_id": "WAMID_REACT_1", "emoji": "❤️"})
_m = message_repo.get_by_msg_id("WAMID_REACT_1")
check("POST /messages/react (change) -> replaced", "👍" not in _m.get("reactions", {}) and _m["reactions"].get("❤️") == ["me"])

# Remove reaction (empty emoji)
r = client.post("/api/contacts/5511999990001/messages/react",
                json={"msg_id": "WAMID_REACT_1", "emoji": ""})
_m = message_repo.get_by_msg_id("WAMID_REACT_1")
check("POST /messages/react (remove) -> cleared", not _m.get("reactions"))

# Missing msg_id -> 400
r = client.post("/api/contacts/5511999990001/messages/react", json={"emoji": "👍"})
check("POST /messages/react (no msg_id) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  8b. Private message (panel-only) — no AI trigger
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Private Message")

_gowa_calls_before = mock_gowa_client.send_message.call_count
r = client.post(
    "/api/contacts/5511999990001/private-message",
    json={"text": "Cliente VIP, atender com prioridade"},
)
check("POST /private-message -> 200", r.status_code == 200)
_pn = r.json()["data"]
check("POST /private-message -> saved note returned",
      _pn.get("role") == "private_note" and "prioridade" in (_pn.get("content") or ""))
check("POST /private-message -> returns DB id (for delete)", bool(_pn.get("_id")))
check(
    "POST /private-message -> no GOWA send",
    mock_gowa_client.send_message.call_count == _gowa_calls_before,
)

# Confirm the message is in the contact's history with role=private_note
r = client.get("/api/contacts/5511999990001")
msgs = r.json()["data"]["messages"]
private_notes = [m for m in msgs if m.get("role") == "private_note"]
check("GET /api/contacts -> private_note present", len(private_notes) >= 1)
check(
    "GET /api/contacts -> private_note status is null",
    private_notes and private_notes[-1].get("status") in (None, ""),
)

# Private notes are deletable by DB id (scope=me) without a msg_id -> kept, flagged revoked
r = client.post("/api/contacts/5511999990001/messages/delete",
                json={"db_id": _pn["_id"], "scope": "me"})
check("POST /messages/delete (private note, db_id) -> 200", r.status_code == 200)
r = client.get("/api/contacts/5511999990001")
_remaining = [m for m in r.json()["data"]["messages"] if m.get("_id") == _pn["_id"]]
check("POST /messages/delete (private note) -> row kept", len(_remaining) == 1)
check("POST /messages/delete (private note) -> flagged revoked", bool(_remaining and _remaining[0].get("revoked")))
check(
    "GET /api/contacts -> private_note content matches",
    private_notes and "VIP" in private_notes[-1]["content"],
)

# Empty payload
r = client.post("/api/contacts/5511999990001/private-message", json={"text": ""})
check("POST /private-message (empty) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  9. Contact send image
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Image")

# Create a fake PNG (1x1 pixel)
fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
r = client.post(
    "/api/contacts/5511999990001/send-image",
    files={"image": ("test.png", io.BytesIO(fake_png), "image/png")},
    data={"caption": "Test caption"},
)
check("POST /send-image -> 200", r.status_code == 200)
check("POST /send-image -> gowa called", mock_gowa_client.send_image.called)

# ═══════════════════════════════════════════════════════════════════
#  10. Contact send audio
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Audio")

fake_ogg = b"OggS" + b"\x00" * 100
r = client.post(
    "/api/contacts/5511999990001/send-audio",
    files={"audio": ("voice.ogg", io.BytesIO(fake_ogg), "audio/ogg")},
)
check("POST /send-audio -> 200", r.status_code == 200)
check("POST /send-audio -> gowa called", mock_gowa_client.send_audio.called)

# ═══════════════════════════════════════════════════════════════════
#  10b. Contact send document
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Document")

fake_pdf = b"%PDF-1.4\n" + b"\x00" * 100
r = client.post(
    "/api/contacts/5511999990001/send-document",
    files={"document": ("relatorio.pdf", io.BytesIO(fake_pdf), "application/pdf")},
    data={"caption": "Doc caption"},
)
check("POST /send-document -> 200", r.status_code == 200)
check("POST /send-document -> gowa called", mock_gowa_client.send_file.called)

# ═══════════════════════════════════════════════════════════════════
#  11. Contact presence
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Presence")

r = client.post("/api/contacts/5511999990001/presence", json={"action": "start"})
check("POST /presence -> 200", r.status_code == 200)
check("POST /presence -> gowa called", mock_gowa_client.send_chat_presence.called)

# ═══════════════════════════════════════════════════════════════════
#  12. Contact mark read
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Mark Read")

r = client.post("/api/contacts/5511999990002/read")
check("POST /read -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  13. Contact toggle AI
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Toggle AI")

r = client.post("/api/contacts/5511999990001/toggle-ai", json={"enabled": False})
check("POST /toggle-ai -> 200", r.status_code == 200)
check("POST /toggle-ai -> disabled", r.json()["data"]["ai_enabled"] is False)

r = client.post("/api/contacts/5511999990001/toggle-ai", json={"enabled": True})
check("POST /toggle-ai -> re-enabled", r.json()["data"]["ai_enabled"] is True)

r = client.post("/api/contacts/5511999990001/toggle-ai", json={})
check("POST /toggle-ai (no field) -> 400", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  14. Contact update info
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Update Info")

r = client.put("/api/contacts/5511999990001/info", json={
    "name": "Alice Updated",
    "email": "alice_new@test.com",
    "profession": "Senior Engineer",
    "company": "NewCo",
    "observations": ["VIP client", "Prefers morning calls"],
})
check("PUT /info -> 200", r.status_code == 200)
info = r.json()["data"]
check("PUT /info -> name updated", info.get("name") == "Alice Updated")
check("PUT /info -> email updated", info.get("email") == "alice_new@test.com")

# Verify persistence
r = client.get("/api/contacts/5511999990001")
data = r.json()["data"]
check("PUT /info -> persisted name", data["name"] == "Alice Updated")
check("PUT /info -> persisted observations", len(data.get("info", {}).get("observations", [])) == 2)

# ═══════════════════════════════════════════════════════════════════
#  15. Tags
# ═══════════════════════════════════════════════════════════════════
section("Tags")

r = client.get("/api/tags")
check("GET /api/tags -> 200", r.status_code == 200)
tags = r.json()["data"]
check("GET /api/tags -> is dict", isinstance(tags, dict))
check("GET /api/tags -> has vip", "vip" in tags)
check("GET /api/tags -> has lead", "lead" in tags)

# Create tag
r = client.post("/api/tags", json={"name": "hot", "color": "#ff6600"})
check("POST /api/tags -> 200", r.status_code == 200)
check("POST /api/tags -> created", r.json()["data"]["name"] == "hot")

# Duplicate tag
r = client.post("/api/tags", json={"name": "hot", "color": "#ff6600"})
check("POST /api/tags (dup) -> 400", r.status_code == 400)

# Update tag
r = client.put("/api/tags/hot", json={"name": "super_hot", "color": "#ff0066"})
check("PUT /api/tags/{name} -> 200", r.status_code == 200)
check("PUT /api/tags -> renamed", r.json()["data"]["name"] == "super_hot")

# Update non-existent
r = client.put("/api/tags/nonexist", json={"color": "#000"})
check("PUT /api/tags (404) -> 404", r.status_code == 404)

# Delete tag
r = client.delete("/api/tags/super_hot")
check("DELETE /api/tags -> 200", r.status_code == 200)

r = client.delete("/api/tags/super_hot")
check("DELETE /api/tags (404) -> 404", r.status_code == 404)

# Set contact tags
r = client.put("/api/contacts/5511999990001/tags", json={"tags": ["vip", "lead"]})
check("PUT /contacts/{phone}/tags -> 200", r.status_code == 200)
check("PUT /contacts/{phone}/tags -> set", set(r.json()["data"]["tags"]) == {"vip", "lead"})

# Non-existent contact (use different number not auto-created elsewhere)
r = client.put("/api/contacts/9999999999/tags", json={"tags": ["vip"]})
check("PUT /contacts/9999/tags -> 404", r.status_code == 404)

# ═══════════════════════════════════════════════════════════════════
#  15b. Quick Replies (plano 04)
# ═══════════════════════════════════════════════════════════════════
section("Quick Replies")

r = client.get("/api/quick-replies")
check("GET /quick-replies -> 200", r.status_code == 200)
check("GET /quick-replies -> is list", isinstance(r.json()["data"], list))

r = client.post("/api/quick-replies", json={"short_code": "oi-anna", "content": "Olá! Sou a Anna."})
check("POST /quick-replies -> 200", r.status_code == 200)
_qr = r.json()["data"]
check("POST /quick-replies -> returns id", isinstance(_qr.get("id"), int))
check("POST /quick-replies -> short_code stored", _qr.get("short_code") == "oi-anna")

# Normalization: leading slash + uppercase get stripped/lowercased
r = client.post("/api/quick-replies", json={"short_code": "/HORARIO", "content": "8h às 18h"})
check("POST /quick-replies -> normaliza /HORARIO -> horario", r.json()["data"]["short_code"] == "horario")

# Uniqueness (P41) — duplicate short_code rejected
r = client.post("/api/quick-replies", json={"short_code": "oi-anna", "content": "outro"})
check("POST /quick-replies (dup) -> erro", r.json().get("ok") is False)

# Invalid short_code (space/accent) rejected
r = client.post("/api/quick-replies", json={"short_code": "com espaco", "content": "x"})
check("POST /quick-replies (inválido) -> erro", r.json().get("ok") is False)

# List now has the two created
r = client.get("/api/quick-replies")
_codes = {q["short_code"] for q in r.json()["data"]}
check("GET /quick-replies -> contém criados", {"oi-anna", "horario"} <= _codes)

# Update content
r = client.put(f"/api/quick-replies/{_qr['id']}", json={"content": "Olá! Anna aqui."})
check("PUT /quick-replies -> 200", r.status_code == 200)
check("PUT /quick-replies -> content atualizado", r.json()["data"]["content"] == "Olá! Anna aqui.")

# Update to a colliding short_code rejected
r = client.put(f"/api/quick-replies/{_qr['id']}", json={"short_code": "horario"})
check("PUT /quick-replies (colisão) -> erro", r.json().get("ok") is False)

# Delete
r = client.delete(f"/api/quick-replies/{_qr['id']}")
check("DELETE /quick-replies -> 200", r.status_code == 200)
r = client.delete(f"/api/quick-replies/{_qr['id']}")
check("DELETE /quick-replies (de novo) -> 404", r.status_code == 404)

# ═══════════════════════════════════════════════════════════════════
#  15c. Custom Attributes (plano 05)
# ═══════════════════════════════════════════════════════════════════
section("Custom Attributes")

# Create a list-type definition
r = client.post("/api/custom-attributes", json={
    "attribute_key": "plano", "display_name": "Plano", "type": "list",
    "applies_to": "contact", "options": ["free", "premium"],
})
check("POST /custom-attributes (list) -> 200", r.status_code == 200)
_def_plano = r.json()["data"]
check("POST /custom-attributes -> retorna id", isinstance(_def_plano.get("id"), int))

# Create a checkbox definition
r = client.post("/api/custom-attributes", json={
    "attribute_key": "vip", "display_name": "VIP", "type": "checkbox", "applies_to": "contact",
})
check("POST /custom-attributes (checkbox) -> 200", r.status_code == 200)

# Invalid key (not snake_case) rejected
r = client.post("/api/custom-attributes", json={
    "attribute_key": "Plano Pago", "display_name": "x", "type": "text", "applies_to": "contact",
})
check("POST /custom-attributes (key inválida) -> erro", r.json().get("ok") is False)

# list type without options rejected
r = client.post("/api/custom-attributes", json={
    "attribute_key": "sem_opcoes", "display_name": "x", "type": "list", "applies_to": "contact",
})
check("POST /custom-attributes (list sem options) -> erro", r.json().get("ok") is False)

# Duplicate (key, applies_to) rejected
r = client.post("/api/custom-attributes", json={
    "attribute_key": "plano", "display_name": "Outro", "type": "text", "applies_to": "contact",
})
check("POST /custom-attributes (dup) -> erro", r.json().get("ok") is False)

# List active definitions for contact
r = client.get("/api/custom-attributes?applies_to=contact")
check("GET /custom-attributes -> 200", r.status_code == 200)
_keys = {d["attribute_key"] for d in r.json()["data"]}
check("GET /custom-attributes -> contém plano+vip", {"plano", "vip"} <= _keys)

# Create a contact (PUT info auto-creates) then set valid custom attributes
_caphone = "5511999990050"
r = client.put(f"/api/contacts/{_caphone}/info", json={
    "name": "Cliente CA", "custom_attributes": {"plano": "premium", "vip": True},
})
check("PUT /info (custom_attributes válidos) -> 200", r.status_code == 200)

# Read back via full contact
r = client.get(f"/api/contacts/{_caphone}")
_ca = r.json()["data"].get("custom_attributes", {})
check("GET /contacts -> custom_attributes.plano == premium", _ca.get("plano") == "premium")
check("GET /contacts -> custom_attributes.vip == True (bool)", _ca.get("vip") is True)

# Invalid value for list rejected
r = client.put(f"/api/contacts/{_caphone}/info", json={"custom_attributes": {"plano": "enterprise"}})
check("PUT /info (valor inválido) -> erro", r.json().get("ok") is False)

# Unknown key rejected (P50)
r = client.put(f"/api/contacts/{_caphone}/info", json={"custom_attributes": {"desconhecido": "x"}})
check("PUT /info (key desconhecida) -> 400", r.status_code == 400)

# Update definition (display_name) — key stays
r = client.put(f"/api/custom-attributes/{_def_plano['id']}", json={"display_name": "Plano Comercial"})
check("PUT /custom-attributes -> 200", r.status_code == 200)
check("PUT /custom-attributes -> display atualizado", r.json()["data"]["display_name"] == "Plano Comercial")

# filterable (plano 05 Fase 6) — persiste no create/update e alimenta list_filterable
r = client.post("/api/custom-attributes", json={
    "attribute_key": "segmento", "display_name": "Segmento", "type": "text",
    "applies_to": "contact", "filterable": True})
check("POST /custom-attributes filterable=true -> persistido", r.json()["data"]["filterable"] == 1)
_seg_id = r.json()["data"]["id"]
r = client.put(f"/api/custom-attributes/{_seg_id}", json={"filterable": False})
check("PUT filterable=false -> 0", r.json()["data"]["filterable"] == 0)
r = client.put(f"/api/custom-attributes/{_seg_id}", json={"filterable": True})
check("PUT filterable=true -> 1", r.json()["data"]["filterable"] == 1)
from db.repositories import custom_attribute_repo as _ca_repo
check("repo.list_filterable(contact) -> inclui segmento",
      "segmento" in {d["attribute_key"] for d in _ca_repo.list_filterable("contact")})

# Soft-delete definition
r = client.delete(f"/api/custom-attributes/{_def_plano['id']}")
check("DELETE /custom-attributes -> 200", r.status_code == 200)
r = client.get("/api/custom-attributes?applies_to=contact")
check("GET /custom-attributes -> plano some após soft-delete",
      "plano" not in {d["attribute_key"] for d in r.json()["data"]})

# Purge orphans (the deleted 'plano' value should be removed from the contact JSON)
r = client.post("/api/custom-attributes/purge-orphans?applies_to=contact")
check("POST /custom-attributes/purge-orphans -> 200", r.status_code == 200)
r = client.get(f"/api/contacts/{_caphone}")
check("purge-orphans -> remove valor órfão 'plano'",
      "plano" not in r.json()["data"].get("custom_attributes", {}))

# ═══════════════════════════════════════════════════════════════════
#  15d. Runtime observability (plano 09 Fase 5)
# ═══════════════════════════════════════════════════════════════════
section("Runtime")

r = client.get("/api/runtime/tasks")
check("GET /runtime/tasks -> 200", r.status_code == 200)
check("GET /runtime/tasks -> is list", isinstance(r.json()["data"], list))

r = client.get("/api/runtime/subprocesses")
check("GET /runtime/subprocesses -> 200", r.status_code == 200)
check("GET /runtime/subprocesses -> is list", isinstance(r.json()["data"], list))

# ═══════════════════════════════════════════════════════════════════
#  15e. Channels (plano 02 Fase 0)
# ═══════════════════════════════════════════════════════════════════
section("Channels")

r = client.get("/api/channels")
check("GET /channels -> 200", r.status_code == 200)
_chans = r.json()["data"]
check("GET /channels -> is list", isinstance(_chans, list))
_default = next((c for c in _chans if c["id"] == "default"), None)
check("GET /channels -> seeds 'default' channel", _default is not None)
check("GET /channels -> default provider is gowa", _default and _default["provider"] == "gowa")

r = client.get("/api/channels/default")
check("GET /channels/default -> 200", r.status_code == 200)

r = client.get("/api/channels/inexistente")
check("GET /channels/{unknown} -> 404", r.status_code == 404)

# Credential masking (P15): set a secret via repo, ensure the API masks it.
from db.repositories import channel_credential_repo as _ccrepo
_ccrepo.set("default", "access_token", "supersecrettoken9876")
r = client.get("/api/channels/default")
_creds = r.json()["data"].get("credentials", {})
check("GET /channels/default -> credential masked",
      _creds.get("access_token", "").startswith("••••") and "supersecret" not in _creds.get("access_token", ""))
check("GET /channels/default -> mask keeps last 4", _creds.get("access_token", "").endswith("9876"))

# CRUD (plano 02 Fase 2)
r = client.post("/api/channels", json={
    "id": "cloud_teste", "provider": "whatsapp_cloud", "display_name": "Cloud Teste",
    "credentials": {"access_token": "EAAtokenSecreto123", "phone_number_id": "55123",
                    "verify_token": "vtok_abc"}})
check("POST /channels (cloud) -> 200", r.status_code == 200)
check("POST /channels -> token mascarado na volta",
      r.json()["data"]["credentials"].get("access_token", "").startswith("••••"))
r = client.post("/api/channels", json={"id": "ID-INVALIDO", "provider": "whatsapp_cloud"})
check("POST /channels id inválido -> 400", r.status_code == 400)
r = client.post("/api/channels", json={"id": "x_prov", "provider": "telegrama"})
check("POST /channels provider inválido -> 400", r.status_code == 400)
r = client.post("/api/channels", json={"id": "cloud_teste", "provider": "whatsapp_cloud"})
check("POST /channels id duplicado -> 409", r.status_code == 409)

r = client.put("/api/channels/cloud_teste", json={"display_name": "Cloud Renomeado", "enabled": False})
check("PUT /channels -> 200 atualiza", r.status_code == 200 and r.json()["data"]["display_name"] == "Cloud Renomeado")
check("PUT /channels -> enabled=0", r.json()["data"]["enabled"] == 0)
# placeholder mascarado não sobrescreve o segredo real
client.put("/api/channels/cloud_teste", json={"credentials": {"access_token": "••••o123"}})
check("PUT mask placeholder -> NÃO sobrescreve segredo",
      _ccrepo.get("cloud_teste", "access_token") == "EAAtokenSecreto123")

r = client.get("/api/channels/cloud_teste/status")
check("GET /channels/{id}/status -> 200", r.status_code == 200 and "connected" in r.json()["data"])

# Canais conectados (picker "iniciar conversa"): só connected+logged_in+enabled.
from db.repositories import channel_repo as _chrepo
r = client.get("/api/channels/connected")
check("GET /channels/connected -> 200", r.status_code == 200)
_conn = r.json()["data"]
check("GET /channels/connected -> is list", isinstance(_conn, list))
check("GET /channels/connected -> sem credenciais expostas",
      all("credentials" not in c for c in _conn))
# Um canal explicitamente desconectado não aparece
_chrepo.create(id="off_teste", provider="whatsapp_cloud", display_name="Desconectado", enabled=1)
_chrepo.set_status("off_teste", connected=0, logged_in=0)
r = client.get("/api/channels/connected")
check("GET /channels/connected -> exclui desconectados",
      not any(c["id"] == "off_teste" for c in r.json()["data"]))
_chrepo.delete("off_teste")
# Marcar um canal cloud como conectado+logado faz ele aparecer
_chrepo.create(id="conn_teste", provider="whatsapp_cloud", display_name="Conectado", enabled=1)
_chrepo.set_status("conn_teste", connected=1, logged_in=1)
r = client.get("/api/channels/connected")
_conn = r.json()["data"]
_ct = next((c for c in _conn if c["id"] == "conn_teste"), None)
check("GET /channels/connected -> inclui conectado+logado", _ct is not None)
check("GET /channels/connected -> carrega display_name", _ct and _ct.get("display_name") == "Conectado")
# Desabilitar remove da lista mesmo conectado
_chrepo.update("conn_teste", enabled=0)
r = client.get("/api/channels/connected")
check("GET /channels/connected -> exclui desabilitado",
      not any(c["id"] == "conn_teste" for c in r.json()["data"]))
_chrepo.delete("conn_teste")

# Handshake do webhook por provider (Cloud API verification) — auth-exempt
r = client.get("/api/webhook/whatsapp_cloud/cloud_teste",
               params={"hub.mode": "subscribe", "hub.verify_token": "vtok_abc",
                       "hub.challenge": "desafio42"})
check("GET webhook handshake (token ok) -> ecoa challenge",
      r.status_code == 200 and r.text == "desafio42")
r = client.get("/api/webhook/whatsapp_cloud/cloud_teste",
               params={"hub.mode": "subscribe", "hub.verify_token": "ERRADO",
                       "hub.challenge": "desafio42"})
check("GET webhook handshake (token errado) -> 403", r.status_code == 403)
r = client.post("/api/webhook/whatsapp_cloud/cloud_teste", json={"entry": []})
check("POST webhook inbound -> 200 (nunca 500)", r.status_code == 200)
r = client.post("/api/webhook/whatsapp_cloud/inexistente", json={})
check("POST webhook canal desconhecido -> 200 ignored", r.status_code == 200)

r = client.delete("/api/channels/default")
check("DELETE /channels/default -> 400 (protegido)", r.status_code == 400)
r = client.delete("/api/channels/cloud_teste")
check("DELETE /channels -> 200", r.status_code == 200)
check("DELETE -> credenciais removidas", _ccrepo.get("cloud_teste", "access_token") is None)

# ═══════════════════════════════════════════════════════════════════
#  15f. RBAC seed (plano 03 Fase 1)
# ═══════════════════════════════════════════════════════════════════
section("RBAC seed")

from sqlalchemy import select as _sa_select, func as _sa_func
from db.engine import get_engine as _get_engine
from db.tables import roles as _roles_t, permissions as _perms_t, role_permissions as _rp_t
with _get_engine().connect() as _conn:
    _role_keys = {r[0] for r in _conn.execute(_sa_select(_roles_t.c.key))}
    _perm_count = _conn.execute(_sa_select(_sa_func.count()).select_from(_perms_t)).scalar()
    _rp_count = _conn.execute(_sa_select(_sa_func.count()).select_from(_rp_t)).scalar()
check("RBAC seed -> 3 system roles (admin/gestor/atendente)",
      _role_keys == {"admin", "gestor", "atendente"})
check("RBAC seed -> 16 permissions", _perm_count == 16)
check("RBAC seed -> role_permissions populated (gestor 13 + atendente 5)", _rp_count == 18)

# ═══════════════════════════════════════════════════════════════════
#  15g. RBAC users + login (plano 03 Fases 2-3, aditivo)
# ═══════════════════════════════════════════════════════════════════
section("RBAC users + login")

# Bootstrap the first admin (one-time, only while no users exist)
r = client.post("/api/auth/bootstrap",
                json={"email": "admin@test.com", "name": "Admin", "password": "supersecret"})
check("POST /auth/bootstrap -> 200", r.status_code == 200)
_admin = r.json()["data"]["user"]
check("bootstrap -> admin role + is_admin",
      _admin.get("roles") == ["admin"] and _admin.get("is_admin") is True)
check("bootstrap -> password_hash not leaked", "password_hash" not in _admin)

# Second bootstrap blocked
r = client.post("/api/auth/bootstrap",
                json={"email": "x@y.com", "name": "X", "password": "supersecret"})
check("POST /auth/bootstrap (2nd) -> 409", r.status_code == 409)

# User login (Argon2id verify + opaque session token)
r = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "supersecret"})
check("POST /auth/login (user) -> 200", r.status_code == 200)
_utok = r.json()["data"]["token"]
check("user login -> opaque token", len(_utok) > 20)
check("user login -> returns user", r.json()["data"]["user"]["email"] == "admin@test.com")

r = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "nope"})
check("POST /auth/login (user wrong pw) -> 401", r.status_code == 401)

# /me resolves the session and computes permissions
r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_utok}"})
check("GET /auth/me (user) -> 200", r.status_code == 200)
_perms = r.json()["data"]["user"]["permissions"]
check("admin me -> all 16 permissions", len([p for p in _perms if p != "*"]) == 16)

r = client.get("/api/auth/check", headers={"Authorization": f"Bearer {_utok}"})
check("GET /auth/check (user session) -> authenticated",
      r.json()["data"]["authenticated"] is True)

# Logout invalidates the session
r = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {_utok}"})
check("POST /auth/logout -> 200", r.status_code == 200)
r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_utok}"})
check("GET /auth/me (after logout) -> 401", r.status_code == 401)

# Resolver + short-circuit (gestor via repo, since user-management UI is Fase 5)
from db.repositories import user_repo as _urepo, rbac_repo as _rrepo
from server.auth import hash_password_argon2 as _hpa
_g = _urepo.create(email="gestor@test.com", name="G",
                   password_hash=_hpa("supersecret"), role_keys=["gestor"])
_gperms = _rrepo.user_permissions(_g["id"])
check("gestor resolver -> 13 perms, no '*'", "*" not in _gperms and len(_gperms) == 13)
check("gestor lacks users.manage", "users.manage" not in _gperms)
check("admin resolver -> short-circuit '*'", "*" in _rrepo.user_permissions(_admin["id"]))

# ── Users CRUD + permission gating (Fases 4-5) ─────────────────────
r = client.get("/api/roles")
check("GET /api/roles -> 200", r.status_code == 200)
check("GET /api/roles -> 3 roles + 16 perms",
      len(r.json()["data"]["roles"]) == 3 and len(r.json()["data"]["permissions"]) == 16)

r = client.get("/api/users")
check("GET /api/users (open/legacy) -> 200", r.status_code == 200)
check("GET /api/users -> lists admin + gestor", len(r.json()["data"]["users"]) >= 2)

r = client.post("/api/users", json={"email": "att@test.com", "name": "Atendente",
                                    "password": "supersecret", "roles": ["atendente"]})
check("POST /api/users (atendente) -> 200", r.status_code == 200)
_att_id = r.json()["data"]["user"]["id"]
check("create -> roles applied", r.json()["data"]["user"]["roles"] == ["atendente"])

r = client.post("/api/users", json={"email": "att@test.com", "name": "Dup",
                                    "password": "supersecret", "roles": ["atendente"]})
check("POST /api/users (dup email) -> 409", r.status_code == 409)

r = client.post("/api/users", json={"email": "weak@test.com", "name": "W",
                                    "password": "short", "roles": ["atendente"]})
check("POST /api/users (weak pw) -> 400", r.status_code == 400)

r = client.put(f"/api/users/{_att_id}", json={"roles": ["gestor"], "name": "Promovido"})
check("PUT /api/users (promote) -> 200", r.status_code == 200)
check("PUT -> roles updated", r.json()["data"]["user"]["roles"] == ["gestor"])

r = client.post(f"/api/users/{_att_id}/password", json={"password": "anothersecret"})
check("POST /api/users/{id}/password -> 200", r.status_code == 200)

# Last-admin guard: deleting the only active admin is refused
r = client.delete(f"/api/users/{_admin['id']}")
check("DELETE last admin -> 409 guard", r.status_code == 409)

r = client.delete(f"/api/users/{_att_id}")
check("DELETE user -> 200", r.status_code == 200)

# Fase 4 enforcement: a logged-in gestor lacks users.manage -> 403
r = client.post("/api/auth/login", json={"email": "gestor@test.com", "password": "supersecret"})
_gtok = r.json()["data"]["token"]
r = client.get("/api/users", headers={"Authorization": f"Bearer {_gtok}"})
check("GET /api/users (gestor session) -> 403 (lacks users.manage)", r.status_code == 403)
r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_gtok}"})
check("gestor /me -> 200 (still authenticated)", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  15g-bis. Custom per-user permissions + role editor + new gates
# ═══════════════════════════════════════════════════════════════════
section("RBAC custom permissions + role editor")

# ── Custom user: explicit permission set replaces roles ────────────
r = client.post("/api/users", json={
    "email": "custom@test.com", "name": "Custom", "password": "supersecret",
    "custom_permissions": True,
    "permissions": ["contact.read", "conversation.reply"]})
check("POST /api/users (custom) -> 200", r.status_code == 200)
_cu = r.json()["data"]["user"]
_cu_id = _cu["id"]
check("custom user -> flag set, no roles",
      _cu.get("custom_permissions") is True and _cu.get("roles") == [])
check("custom user -> explicit permissions echoed",
      set(_cu.get("permissions") or []) == {"contact.read", "conversation.reply"})
check("custom user -> not admin", _cu.get("is_admin") is False)

# custom mode requires at least one permission
r = client.post("/api/users", json={
    "email": "empty@test.com", "name": "E", "password": "supersecret",
    "custom_permissions": True, "permissions": []})
check("POST /api/users (custom, no perms) -> 400", r.status_code == 400)

# effective permissions = exactly the explicit set (no '*', no role union)
_curesolved = _rrepo.user_permissions(_cu_id)
check("custom resolver -> exact set, no '*'",
      _curesolved == {"contact.read", "conversation.reply"})

r = client.post("/api/auth/login", json={"email": "custom@test.com", "password": "supersecret"})
_ctok = r.json()["data"]["token"]
r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_ctok}"})
check("custom /me -> exact perms",
      set(r.json()["data"]["user"]["permissions"]) == {"contact.read", "conversation.reply"})

# ── New enforcement gates (custom user lacks these) ────────────────
_chdr = {"Authorization": f"Bearer {_ctok}"}
r = client.put("/api/config", json={"model": "x"}, headers=_chdr)
check("PUT /api/config (no settings.manage) -> 403", r.status_code == 403)
r = client.get("/api/contacts", headers=_chdr)
check("GET /api/contacts (has contact.read) -> 200", r.status_code == 200)
r = client.get("/api/users", headers=_chdr)
check("GET /api/users (no users.manage) -> 403", r.status_code == 403)
r = client.post("/api/quick-replies", json={"short_code": "x", "content": "y"}, headers=_chdr)
check("POST /api/quick-replies (no quickreply.manage) -> 403", r.status_code == 403)
r = client.put("/api/contacts/5511999/info", json={"name": "x"}, headers=_chdr)
check("PUT /api/contacts/{p}/info (no contact.write) -> 403", r.status_code == 403)

# ── Switching modes + last-admin guard ────────────────────────────
r = client.put(f"/api/users/{_cu_id}", json={
    "custom_permissions": False, "roles": ["atendente"]})
check("PUT custom->role -> 200", r.status_code == 200)
check("PUT custom->role -> flag cleared + role set",
      r.json()["data"]["user"]["custom_permissions"] is False
      and r.json()["data"]["user"]["roles"] == ["atendente"])
check("PUT custom->role -> explicit grants cleared",
      r.json()["data"]["user"].get("permissions") == [])

# Converting the only active admin to custom mode drops their admin role -> guard
r = client.put(f"/api/users/{_admin['id']}", json={
    "custom_permissions": True, "permissions": ["contact.read"]})
check("PUT last-admin -> custom -> 409 guard", r.status_code == 409)

client.delete(f"/api/users/{_cu_id}")

# ── Role editor: GET /api/roles exposes permission_keys ────────────
r = client.get("/api/roles")
_roles_payload = r.json()["data"]["roles"]
_by_key = {ro["key"]: ro for ro in _roles_payload}
check("GET /api/roles -> permission_keys present",
      "permission_keys" in _by_key["gestor"] and len(_by_key["gestor"]["permission_keys"]) == 13)
check("GET /api/roles -> admin shows all 16",
      len(_by_key["admin"]["permission_keys"]) == 16)

# Create a custom role
r = client.post("/api/roles", json={
    "key": "supervisor", "name": "Supervisor",
    "permission_keys": ["conversation.read", "audit.read"]})
check("POST /api/roles (custom) -> 200", r.status_code == 200)
_sup = r.json()["data"]["role"]
_sup_id = _sup["id"]
check("custom role -> is_system 0 + perms",
      _sup.get("is_system") == 0 and set(_sup["permission_keys"]) == {"conversation.read", "audit.read"})

# Reserved + invalid keys rejected
r = client.post("/api/roles", json={"key": "admin", "name": "X", "permission_keys": []})
check("POST /api/roles (reserved key) -> 400", r.status_code == 400)
r = client.post("/api/roles", json={"key": "Bad Key", "name": "X", "permission_keys": []})
check("POST /api/roles (invalid key) -> 400", r.status_code == 400)

# A user with the custom role inherits its permissions
r = client.post("/api/users", json={
    "email": "sup@test.com", "name": "S", "password": "supersecret", "roles": ["supervisor"]})
_sup_user_id = r.json()["data"]["user"]["id"]
check("user with custom role -> inherits perms",
      _rrepo.user_permissions(_sup_user_id) == {"conversation.read", "audit.read"})

# Edit the custom role's permissions -> reflected live
r = client.put(f"/api/roles/{_sup_id}", json={"permission_keys": ["conversation.read"]})
check("PUT /api/roles (custom) -> 200", r.status_code == 200)
check("role edit -> reflected on assigned user",
      _rrepo.user_permissions(_sup_user_id) == {"conversation.read"})

# admin role is locked
_admin_role_id = _by_key["admin"]["id"]
r = client.put(f"/api/roles/{_admin_role_id}", json={"permission_keys": []})
check("PUT admin role -> 400 (locked)", r.status_code == 400)

# delete blocked while assigned, system roles undeletable
r = client.delete(f"/api/roles/{_sup_id}")
check("DELETE custom role (assigned) -> 409", r.status_code == 409)
_gestor_role_id = _by_key["gestor"]["id"]
r = client.delete(f"/api/roles/{_gestor_role_id}")
check("DELETE system role -> 409", r.status_code == 409)

# free the role then delete it
client.delete(f"/api/users/{_sup_user_id}")
r = client.delete(f"/api/roles/{_sup_id}")
check("DELETE custom role (free) -> 200", r.status_code == 200)

# edit gestor then restore defaults
r = client.put(f"/api/roles/{_gestor_role_id}", json={"permission_keys": ["conversation.read"]})
check("PUT gestor role (shrink) -> 200", r.status_code == 200)
check("gestor shrunk to 1 perm", _rrepo.get_role_permissions("gestor") == {"conversation.read"})
r = client.post(f"/api/roles/{_gestor_role_id}/reset")
check("POST /api/roles/{id}/reset -> 200", r.status_code == 200)
check("gestor restored to 13 perms", len(_rrepo.get_role_permissions("gestor")) == 13)

# ═══════════════════════════════════════════════════════════════════
#  15h. Conversations (plano 01 Fase 1)
# ═══════════════════════════════════════════════════════════════════
section("Conversations")

from db.repositories import (conversation_repo as _conv_repo,
                             contact_inbox_repo as _ci_repo)
from db.tables import contacts as _contacts_t, inboxes as _inboxes_t

with _get_engine().connect() as _conn:
    _cid = _conn.execute(_sa_select(_contacts_t.c.id).limit(1)).scalar()
    _inbox_seeded = _conn.execute(
        _sa_select(_inboxes_t.c.id).where(_inboxes_t.c.id == 1)).scalar()
check("inbox default seeded (id=1)", _inbox_seeded == 1)

_ci = _ci_repo.get_or_create(inbox_id=1, contact_id=_cid,
                             source_id=f"{_cid}@s.whatsapp.net")
_conv1 = _conv_repo.create(inbox_id=1, contact_id=_cid, contact_inbox_id=_ci["id"])
_conv2 = _conv_repo.create(inbox_id=1, contact_id=_cid, contact_inbox_id=_ci["id"])
check("conversation_repo.create -> display_id sequencial",
      _conv2["display_id"] == _conv1["display_id"] + 1)
check("create -> status open + ai_active", _conv1["status"] == "open" and _conv1["ai_active"] == 1)
check("create -> bound to default AI agent (IA padrão)", _conv1["active_agent_key"] == "default")

r = client.get("/api/conversations")
check("GET /api/conversations -> 200", r.status_code == 200)
_convs = r.json()["data"]["conversations"]
check("list -> includes created convs + contact_name join",
      len(_convs) >= 2 and "contact_name" in _convs[0])

r = client.get(f"/api/conversations/{_conv1['id']}")
check("GET /api/conversations/{id} -> 200", r.status_code == 200)
check("detail -> right conversation", r.json()["data"]["conversation"]["id"] == _conv1["id"])

r = client.get("/api/conversations/999999")
check("GET /api/conversations/{missing} -> 404", r.status_code == 404)

r = client.post(f"/api/conversations/{_conv1['id']}/status", json={"status": "closed"})
check("POST status closed -> 200", r.status_code == 200)
check("status closed -> resolved_at set",
      r.json()["data"]["conversation"]["status"] == "closed"
      and r.json()["data"]["conversation"]["resolved_at"] is not None)

r = client.post(f"/api/conversations/{_conv1['id']}/status", json={"status": "bogus"})
check("POST status invalid -> 400", r.status_code == 400)

r = client.post(f"/api/conversations/{_conv1['id']}/assign", json={"assignee_user_id": _admin["id"]})
check("POST assign -> 200 + assignee set",
      r.status_code == 200 and r.json()["data"]["conversation"]["assignee_user_id"] == _admin["id"])

r = client.post(f"/api/conversations/{_conv1['id']}/archive", json={"archived": True})
check("POST archive -> is_archived=1", r.json()["data"]["conversation"]["is_archived"] == 1)

r = client.get("/api/conversations?status=closed")
check("GET /api/conversations?status=closed -> filtered",
      all(c["status"] == "closed" for c in r.json()["data"]["conversations"]))

# Permission gating: atendente lacks conversation.assign
from db.repositories import user_repo as _urepo2
from server.auth import hash_password_argon2 as _hpa2
_at = _urepo2.create(email="att2@test.com", name="A2",
                     password_hash=_hpa2("supersecret"), role_keys=["atendente"])
r = client.post("/api/auth/login", json={"email": "att2@test.com", "password": "supersecret"})
_attok = r.json()["data"]["token"]
r = client.post(f"/api/conversations/{_conv2['id']}/assign",
                json={"assignee_user_id": None}, headers={"Authorization": f"Bearer {_attok}"})
check("atendente assign -> 403 (lacks conversation.assign)", r.status_code == 403)
r = client.get("/api/conversations", headers={"Authorization": f"Bearer {_attok}"})
check("atendente list -> 200 (has conversation.read)", r.status_code == 200)

# ── plano 10 Onda 0: assign-me, ai, agent, info, contact→conversation ──
# Usuário admin vivo (o _admin original foi deletado num teste anterior)
_mgr = _urepo2.create(email="mgr@test.com", name="Mgr",
                      password_hash=_hpa2("supersecret"), role_keys=["admin"])
_mgrtok = client.post("/api/auth/login",
                      json={"email": "mgr@test.com", "password": "supersecret"}).json()["data"]["token"]
r = client.post(f"/api/conversations/{_conv2['id']}/assign-me",
                headers={"Authorization": f"Bearer {_mgrtok}"})
check("POST assign-me (admin) -> 200 + assignee=eu",
      r.status_code == 200 and r.json()["data"]["conversation"]["assignee_user_id"] == _mgr["id"])
r = client.post(f"/api/conversations/{_conv2['id']}/assign-me")
check("POST assign-me sem auth -> 401", r.status_code == 401)

# reopen: resolver limpa o assignee; reabrir (status=open) deixa a conversa SEM
# responsável, então ela cai na aba "Não atribuídas".
client.post(f"/api/conversations/{_conv2['id']}/assign-me",
            headers={"Authorization": f"Bearer {_mgrtok}"})
client.post(f"/api/conversations/{_conv2['id']}/status", json={"status": "closed"})
r = client.post(f"/api/conversations/{_conv2['id']}/status",
                json={"status": "open"}, headers={"Authorization": f"Bearer {_mgrtok}"})
check("POST reopen (status open) -> 200 + status open",
      r.status_code == 200 and r.json()["data"]["conversation"]["status"] == "open")
check("POST reopen -> sem responsável (cai em 'Não atribuídas')",
      r.json()["data"]["conversation"]["assignee_user_id"] is None)
check("POST reopen -> resolved_at limpo",
      r.json()["data"]["conversation"]["resolved_at"] is None)

r = client.post(f"/api/conversations/{_conv2['id']}/ai", json={"active": False})
check("POST ai -> ai_active=0", r.json()["data"]["conversation"]["ai_active"] == 0)

r = client.post(f"/api/conversations/{_conv2['id']}/agent", json={"agent_key": "default"})
check("POST agent -> 200", r.status_code == 200)

# PUT info com custom_attributes de conversa (cria def primeiro)
client.post("/api/custom-attributes", json={
    "attribute_key": "prioridade", "display_name": "Prioridade",
    "type": "text", "applies_to": "conversation"})
r = client.put(f"/api/conversations/{_conv2['id']}/info",
               json={"custom_attributes": {"prioridade": "alta"}})
check("PUT info custom_attributes -> 200 + salvo",
      r.status_code == 200 and
      r.json()["data"]["conversation"]["custom_attributes"].get("prioridade") == "alta")
r = client.put(f"/api/conversations/{_conv2['id']}/info",
               json={"custom_attributes": {"inexistente": "x"}})
check("PUT info atributo desconhecido -> 400", r.status_code == 400)
r = client.put(f"/api/conversations/999999/info", json={"custom_attributes": {}})
check("PUT info conversa inexistente -> 404", r.status_code == 404)

# GET /contacts/{phone}/conversation resolve a conversa aberta
with _get_engine().connect() as _conn:
    _cphone = _conn.execute(
        _sa_select(_contacts_t.c.phone).where(_contacts_t.c.id == _cid)).scalar()
r = client.get(f"/api/contacts/{_cphone}/conversation")
check("GET contacts/{phone}/conversation -> 200", r.status_code == 200)
check("contact conversation -> tem conversation_id",
      (r.json()["data"]["conversation"] or {}).get("id") is not None)
r = client.get("/api/contacts/naoexiste/conversation")
check("GET contacts/{missing}/conversation -> 404", r.status_code == 404)

# ── Fluxo vivo (Fase 2): ContactMemory.add_message resolve+stampa conversation_id ──
from agent.memory import ContactMemory as _CM
from db.tables import messages as _msgs_t
_cm = _CM("5500011122233")  # fresh phone -> cria contato + conversa
_cm.add_message("user", "olá mundo")
with _get_engine().connect() as _conn:
    # filtra role=='user' — add_message agora também grava avisos de sistema
    # (plano 12: 'created') no mesmo fio, que seriam a linha mais recente.
    _last = _conn.execute(
        _sa_select(_msgs_t.c.conversation_id, _msgs_t.c.content)
        .where(_msgs_t.c.contact_id == _cm.id)
        .where(_msgs_t.c.role == "user")
        .order_by(_msgs_t.c.id.desc()).limit(1)).mappings().first()
check("add_message -> mensagem ganha conversation_id", _last["conversation_id"] is not None)
_live_conv = _conv_repo.get(_last["conversation_id"])
check("add_message -> conversa criada p/ o contato", _live_conv["contact_id"] == _cm.id)
_act0 = _live_conv["last_activity_at"]

# assistant message stampa a MESMA conversa
_cm.add_message("assistant", "oi, tudo bem?")
with _get_engine().connect() as _conn:
    _conv_ids = {r[0] for r in _conn.execute(
        _sa_select(_msgs_t.c.conversation_id).where(_msgs_t.c.contact_id == _cm.id))}
check("add_message assistant -> mesma conversa (1 thread)", _conv_ids == {_live_conv["id"]})

# reopen: fechar e mandar inbound reabre
_conv_repo.set_status(_live_conv["id"], "closed")
_cm.add_message("user", "voltei")
check("inbound reabre conversa closed",
      _conv_repo.get(_live_conv["id"])["status"] == "open")

# Fatia 2: gate ai_active por conversa
from server.routes.webhook import _conversation_ai_active as _ai_gate
check("gate ai_active default True (ai_active=1)", _ai_gate(_cm) is True)
r = client.post(f"/api/conversations/{_live_conv['id']}/ai", json={"active": False})
check("POST /conversations/{id}/ai active=false -> 200", r.status_code == 200)
check("set_ai_active -> ai_active=0", r.json()["data"]["conversation"]["ai_active"] == 0)
check("gate ai_active False quando pausada", _ai_gate(_cm) is False)
r = client.post(f"/api/conversations/{_live_conv['id']}/ai", json={"active": True})
check("POST /conversations/{id}/ai active=true -> reativa", r.json()["data"]["conversation"]["ai_active"] == 1)
check("gate volta a True", _ai_gate(_cm) is True)

# ═══════════════════════════════════════════════════════════════════
#  15h-bis. Avisos de sistema no chat (plano 12)
# ═══════════════════════════════════════════════════════════════════
section("System notices (plano 12)")

from server import system_notices as _sn
from db.repositories import config_repo as _sn_cfg
from db.repositories import message_repo as _sn_msg_repo

def _notices(conv_id):
    """conversation_event message contents for a conversation, ordered by id."""
    with _get_engine().connect() as _conn:
        rows = _conn.execute(
            _sa_select(_msgs_t.c.content)
            .where(_msgs_t.c.conversation_id == conv_id)
            .where(_msgs_t.c.role == "conversation_event")
            .order_by(_msgs_t.c.id)).all()
    return [r[0] for r in rows]

# Registry consistency: todo event_type tem formatter + grupo conhecido.
check("plano12 registry: formatters == group_of",
      set(_sn.FORMATTERS) == set(_sn.EVENT_GROUP_OF))
check("plano12 registry: grupos referenciados existem",
      all(g in _sn.EVENT_GROUPS for g in _sn.EVENT_GROUP_OF.values()))

# Garante os 4 grupos ligados (defaults), conversa dedicada (contato novo).
for _k in ("system_notice_assignment", "system_notice_tags",
           "system_notice_status", "system_notice_ai"):
    _sn_cfg.set(_k, True)
# Sessão admin (Mgr) p/ exercitar o caminho do AUTOR ("Mgr fez X").
_snhdr = {"Authorization": f"Bearer {_mgrtok}"}
_sncm = _CM("5500099988877")
_sncm.add_message("user", "início")  # cria conversa (+ aviso 'created', autor=None)
_snconv = _conv_repo.get_open_for_contact(_sncm.id)
_sn_phone = _sncm.phone
check("create -> aviso 'created' no fio",
      any("iniciada" in c for c in _notices(_snconv["id"])))

# (a) status close/open -> grupo status, com autor nomeado
_n = len(_notices(_snconv["id"]))
client.post(f"/api/conversations/{_snconv['id']}/status", json={"status": "closed"}, headers=_snhdr)
client.post(f"/api/conversations/{_snconv['id']}/status", json={"status": "open"}, headers=_snhdr)
_after = _notices(_snconv["id"])
check("status close/open -> 2 avisos", len(_after) - _n == 2)
check("status_closed -> 'Mgr resolveu'", any("Mgr resolveu a conversa" in c for c in _after[_n:]))
check("status_open -> 'Mgr reabriu'", any("Mgr reabriu a conversa" in c for c in _after[_n:]))

# (b) GATE: grupo status OFF => nada grava
_sn_cfg.set("system_notice_status", False)
_n = len(_notices(_snconv["id"]))
client.post(f"/api/conversations/{_snconv['id']}/status", json={"status": "closed"}, headers=_snhdr)
check("grupo status OFF -> nenhum aviso (gate na geração)",
      len(_notices(_snconv["id"])) == _n)
_sn_cfg.set("system_notice_status", True)
client.post(f"/api/conversations/{_snconv['id']}/status", json={"status": "open"}, headers=_snhdr)

# (c) assign + unassign -> grupo assignment, nomeia o alvo
_n = len(_notices(_snconv["id"]))
client.post(f"/api/conversations/{_snconv['id']}/assign", json={"assignee_user_id": _mgr["id"]}, headers=_snhdr)
client.post(f"/api/conversations/{_snconv['id']}/assign", json={"assignee_user_id": None}, headers=_snhdr)
_after = _notices(_snconv["id"])
check("assign + unassign -> 2 avisos", len(_after) - _n == 2)
check("assigned -> nomeia alvo (Mgr)", any("para Mgr" in c for c in _after[_n:]))
check("unassigned -> 'removeu a atribuição'",
      any("removeu a atribuição" in c for c in _after[_n:]))

# (d) ai off/on (conversa) -> grupo ai
_n = len(_notices(_snconv["id"]))
client.post(f"/api/conversations/{_snconv['id']}/ai", json={"active": False}, headers=_snhdr)
client.post(f"/api/conversations/{_snconv['id']}/ai", json={"active": True}, headers=_snhdr)
check("ai off/on -> 2 avisos (grupo ai)", len(_notices(_snconv["id"])) - _n == 2)

# (e) agent_changed -> grupo ai
_n = len(_notices(_snconv["id"]))
client.post(f"/api/conversations/{_snconv['id']}/agent", json={"agent_key": "default"}, headers=_snhdr)
check("agent_changed -> 1 aviso", len(_notices(_snconv["id"])) - _n == 1)

# (f) attribute_set -> grupo ai (def 'prioridade' criada antes); no-op não gera
_n = len(_notices(_snconv["id"]))
client.put(f"/api/conversations/{_snconv['id']}/info",
           json={"custom_attributes": {"prioridade": "alta"}}, headers=_snhdr)
_after = _notices(_snconv["id"])
check("attribute_set -> 1 aviso", len(_after) - _n == 1)
check("attribute_set -> 'definiu'", any("definiu" in c for c in _after[_n:]))
_n = len(_notices(_snconv["id"]))
client.put(f"/api/conversations/{_snconv['id']}/info",
           json={"custom_attributes": {"prioridade": "alta"}}, headers=_snhdr)
check("attribute_set no-op (mesmo valor) -> nenhum aviso",
      len(_notices(_snconv["id"])) == _n)

# (g) tags por contato -> grupo tags (resolve conversa aberta do contato)
client.post("/api/tags", json={"name": "vip", "color": "#ff0000"}, headers=_snhdr)
_n = len(_notices(_snconv["id"]))
client.put(f"/api/contacts/{_sn_phone}/tags", json={"tags": ["vip"]}, headers=_snhdr)
client.put(f"/api/contacts/{_sn_phone}/tags", json={"tags": []}, headers=_snhdr)
_after = _notices(_snconv["id"])
check("tag add/remove -> 2 avisos", len(_after) - _n == 2)
check("tag_added -> 'adicionou a tag'", any("adicionou a tag" in c for c in _after[_n:]))
check("tag_removed -> 'removeu a tag'", any("removeu a tag" in c for c in _after[_n:]))

# (h) GATE: grupo tags OFF
_sn_cfg.set("system_notice_tags", False)
_n = len(_notices(_snconv["id"]))
client.put(f"/api/contacts/{_sn_phone}/tags", json={"tags": ["vip"]}, headers=_snhdr)
check("grupo tags OFF -> nenhum aviso", len(_notices(_snconv["id"])) == _n)
_sn_cfg.set("system_notice_tags", True)

# (i) toggle-ai por contato -> grupo ai
_n = len(_notices(_snconv["id"]))
client.post(f"/api/contacts/{_sn_phone}/toggle-ai", json={"enabled": False}, headers=_snhdr)
check("toggle-ai contato -> 1 aviso (grupo ai)", len(_notices(_snconv["id"])) - _n == 1)

# (j) auto-reabertura: cliente manda msg numa conversa closed
_conv_repo.set_status(_snconv["id"], "closed")
_n = len(_notices(_snconv["id"]))
_sncm.add_message("user", "oi de novo")
check("auto-reopen -> aviso 'reaberta automaticamente'",
      any("reaberta automaticamente" in c for c in _notices(_snconv["id"])[_n:]))

# (k) ai_takeover: 1×/conversa via has_event (dedupe)
check("ai_takeover ainda não existe", _sn.has_event(_snconv["id"], "ai_takeover") is False)
_sn.emit_conversation_notice(event_type="ai_takeover", conversation_id=_snconv["id"],
                             contact_id=_sncm.id, phone=_sn_phone)
check("ai_takeover emitido -> has_event True", _sn.has_event(_snconv["id"], "ai_takeover") is True)
check("ai_takeover -> card 'IA assumiu'",
      any("IA assumiu o atendimento" in c for c in _notices(_snconv["id"])))

# (l) exclusões: conversation_event fora do contexto do LLM
_snctx = _sn_msg_repo.get_context(_sncm.id, 200)
check("conversation_event excluído do contexto do LLM",
      all(m["role"] != "conversation_event" for m in _snctx))

# (m) exclusões: preview da sidebar nunca é um conversation_event
client.post(f"/api/conversations/{_snconv['id']}/archive", json={"archived": False})
_snlist = client.get("/api/conversations").json()["data"]["conversations"]
_snrow = next((c for c in _snlist if c["id"] == _snconv["id"]), None)
check("sidebar -> conversa listada", _snrow is not None)
check("sidebar preview role != conversation_event",
      _snrow is None or _snrow.get("last_message_role") != "conversation_event")

# (n) evento desconhecido -> no-op silencioso (não grava)
_n = len(_notices(_snconv["id"]))
_sn.emit_conversation_notice(event_type="inexistente_xyz", conversation_id=_snconv["id"],
                             contact_id=_sncm.id, phone=_sn_phone)
check("evento desconhecido -> no-op", len(_notices(_snconv["id"])) == _n)

# ═══════════════════════════════════════════════════════════════════
#  15i. Filtros de conversas (plano 08)
# ═══════════════════════════════════════════════════════════════════
section("Conversation Filters")

r = client.get("/api/conversations/filter-schema")
check("GET filter-schema -> 200", r.status_code == 200)
_dim_keys = {d["key"] for d in r.json()["data"]["dimensions"]}
check("filter-schema -> tem status/labels/q", {"status", "labels", "q"} <= _dim_keys)
check("filter-schema -> NÃO expõe colunas cruas", "drop_table" not in _dim_keys)

r = client.get("/api/conversations/filter?status=open")
check("GET filter?status=open -> 200", r.status_code == 200)
check("filter status=open -> só abertas",
      all(c["status"] == "open" for c in r.json()["data"]["conversations"]))

_open_get = client.get("/api/conversations/filter?status=open").json()["data"]["count"]
r = client.post("/api/conversations/filter", json={
    "filters": [{"attribute_key": "status", "filter_operator": "equal_to", "values": ["open"]}]})
check("POST filter (Chatwoot payload) == GET", r.json()["data"]["count"] == _open_get)

# Adversariais → 400
check("filter?drop_table=1 -> 400 (dim desconhecida)",
      client.get("/api/conversations/filter?drop_table=1").status_code == 400)
check("filter status valor inválido -> 400",
      client.get("/api/conversations/filter?status=hacked").status_code == 400)
r = client.post("/api/conversations/filter", json={
    "filters": [{"attribute_key": "status", "filter_operator": "DROP", "values": ["x"]}]})
check("POST filter operador inválido -> 400", r.status_code == 400)
check("filter cattr não-filtrável -> 400",
      client.get("/api/conversations/filter?cattr:nao_existe=x").status_code == 400)

# cattr end-to-end: def conversation filterable + valor
r = client.post("/api/custom-attributes", json={
    "attribute_key": "plano_conv", "display_name": "Plano", "type": "text",
    "applies_to": "conversation", "filterable": True})
check("cria cattr conversation filterable -> 200", r.status_code == 200)
_conv_repo.set_custom_attributes(_live_conv["id"], {"plano_conv": "gold"})
r = client.get("/api/conversations/filter-schema")
check("filter-schema inclui cattr:plano_conv",
      "cattr:plano_conv" in {d["key"] for d in r.json()["data"]["dimensions"]})
r = client.get("/api/conversations/filter?cattr:plano_conv=gold")
check("filter cattr=gold -> acha a conversa",
      any(c["id"] == _live_conv["id"] for c in r.json()["data"]["conversations"]))
r = client.get("/api/conversations/filter?cattr:plano_conv=silver")
check("filter cattr=silver -> não acha",
      all(c["id"] != _live_conv["id"] for c in r.json()["data"]["conversations"]))

# Robustez (achados do pentest adversarial) — input malformado vira 400, nunca 500
r = client.post("/api/conversations/filter", json={
    "filters": [{"attribute_key": "status", "filter_operator": "equal_to", "values": [{"$ne": None}]}]})
check("filter value dict (NoSQL-ish) -> 400 não 500", r.status_code == 400)
r = client.post("/api/conversations/filter", json={
    "filters": [{"attribute_key": "priority", "filter_operator": "equal_to", "values": []}]})
check("filter values:[] em op escalar -> 400 não 500", r.status_code == 400)
r = client.post("/api/conversations/filter", json={
    "filters": [{"attribute_key": "assignee", "filter_operator": "equal_to", "values": []}]})
check("filter assignee values:[] -> 400 não 500", r.status_code == 400)

# ═══════════════════════════════════════════════════════════════════
#  15j. AI Engine — history / rollback (plano 06)
# ═══════════════════════════════════════════════════════════════════
section("AI Engine history/rollback")

client.put("/api/ai/agents/default", json={
    "display_name": "Agente A", "prompt_key": "default", "model_config": {}, "enabled": True})
client.put("/api/ai/agents/default", json={
    "display_name": "Agente B", "prompt_key": "default", "model_config": {}, "enabled": True})
r = client.get("/api/ai/agents/default/history")
check("GET agent history -> 200", r.status_code == 200)
_hist = r.json()["data"]
check("agent history -> >=2 versões, mais nova primeiro",
      len(_hist) >= 2 and _hist[0]["version"] > _hist[1]["version"])
_v_a = next(h["version"] for h in _hist if True)  # latest is 'Agente B'
# rollback para a versão de "Agente A" (a penúltima salva)
_target = sorted(h["version"] for h in _hist)[-2]
r = client.post(f"/api/ai/agents/default/rollback/{_target}")
check("POST agent rollback -> 200", r.status_code == 200)
check("rollback -> cria versão NOVA", r.json()["data"]["version"] > _hist[0]["version"])
check("rollback -> restaura display_name", r.json()["data"]["display_name"] == "Agente A")
r = client.post("/api/ai/agents/default/rollback/9999")
check("rollback versão inexistente -> 404", r.status_code == 404)

# Prompt history/rollback
client.put("/api/ai/prompts/default", json={"body": "Prompt V1"})
client.put("/api/ai/prompts/default", json={"body": "Prompt V2"})
r = client.get("/api/ai/prompts/default/history")
check("GET prompt history -> >=2", len(r.json()["data"]) >= 2)
_pt = sorted(h["version"] for h in r.json()["data"])[-2]
r = client.post(f"/api/ai/prompts/default/rollback/{_pt}")
check("prompt rollback -> restaura body V1", r.json()["data"]["body"] == "Prompt V1")

# Binding agente↔conversa + campos de roteamento (plano 06 schema)
r = client.put("/api/ai/agents/default", json={
    "display_name": "Roteador", "prompt_key": "default", "model_config": {},
    "enabled": True, "is_router": True, "routing_targets": ["vendas", "suporte"],
    "description": "Agente roteador"})
check("save agent c/ is_router+routing_targets -> 200", r.status_code == 200)
check("agent is_router persistido", r.json()["data"]["is_router"] is True)
check("agent routing_targets persistido", r.json()["data"]["routing_targets"] == ["vendas", "suporte"])

# hooks_config (plano 06 migration 0016) + rollback restaura TODOS os campos de roteamento
r = client.put("/api/ai/agents/default", json={
    "display_name": "Com Hooks", "prompt_key": "default", "model_config": {}, "enabled": True,
    "is_router": True, "routing_targets": ["vendas"],
    "hooks_config": {"buscar_pedido": {"call_limit": 2}}})
check("save agent c/ hooks_config -> 200", r.status_code == 200)
check("hooks_config persistido", r.json()["data"]["hooks_config"] == {"buscar_pedido": {"call_limit": 2}})
_v_hooks = r.json()["data"]["version"]
# sobrescreve com agente simples (sem router/hooks), depois faz rollback para a versão com hooks
client.put("/api/ai/agents/default", json={
    "display_name": "Simples", "prompt_key": "default", "model_config": {}, "enabled": True})
r = client.post(f"/api/ai/agents/default/rollback/{_v_hooks}")
check("rollback restaura is_router", r.json()["data"]["is_router"] is True)
check("rollback restaura routing_targets", r.json()["data"]["routing_targets"] == ["vendas"])
check("rollback restaura hooks_config",
      r.json()["data"]["hooks_config"] == {"buscar_pedido": {"call_limit": 2}})

r = client.post(f"/api/conversations/{_live_conv['id']}/agent", json={"agent_key": "default"})
check("POST /conversations/{id}/agent -> 200", r.status_code == 200)
check("conversa ganha active_agent_key", r.json()["data"]["conversation"]["active_agent_key"] == "default")
r = client.post(f"/api/conversations/{_live_conv['id']}/agent", json={"agent_key": None})
check("POST agent null -> desvincula",
      r.json()["data"]["conversation"]["active_agent_key"] is None)

# Inbox → agente default (plano 06 binding via API)
r = client.get("/api/inboxes")
check("GET /api/inboxes -> 200 lista", r.status_code == 200 and isinstance(r.json()["data"], list))
check("GET /api/inboxes -> tem inbox default id=1",
      any(i["id"] == 1 for i in r.json()["data"]))
r = client.put("/api/inboxes/1/default-agent", json={"agent_key": "default"})
check("PUT inbox default-agent -> 200", r.status_code == 200 and r.json()["data"]["default_agent_key"] == "default")
r = client.put("/api/inboxes/1/default-agent", json={"agent_key": "nao_existe"})
check("PUT inbox default-agent agente inexistente -> 400", r.status_code == 400)
r = client.put("/api/inboxes/999/default-agent", json={"agent_key": "default"})
check("PUT inbox default-agent inbox inexistente -> 404", r.status_code == 404)
r = client.put("/api/inboxes/1/default-agent", json={"agent_key": None})
check("PUT inbox default-agent null -> desvincula", r.json()["data"]["default_agent_key"] is None)

# Reinstall de tool code-in-DB (não dispara restart real no teste)
import server.routes.ai_engine as _aimod
_aimod.schedule_restart = lambda *a, **k: None  # no-op: evita os._exit na suíte
client.put("/api/ai/tools/tool_teste", json={
    "description": "teste", "code": "SCHEMA={}\ndef execute(ctx,args):\n    return None",
    "dependencies": [], "enabled": False, "restart": False})
r = client.post("/api/ai/tools/tool_teste/reinstall")
check("POST /api/ai/tools/{name}/reinstall -> 200", r.status_code == 200)
r = client.post("/api/ai/tools/nao_existe/reinstall")
check("reinstall tool inexistente -> 404", r.status_code == 404)

# ═══════════════════════════════════════════════════════════════════
#  16. Usage
# ═══════════════════════════════════════════════════════════════════
section("Usage")

r = client.get("/api/usage/summary")
check("GET /usage/summary -> 200", r.status_code == 200)
data = r.json()["data"]
check("GET /usage/summary -> has total_tokens", "total_tokens" in data)
check("GET /usage/summary -> tokens > 0", data.get("total_tokens", 0) > 0)

r = client.get("/api/usage/summary?period=24h")
check("GET /usage/summary?period=24h -> 200", r.status_code == 200)
check("GET /usage/summary -> has period_start", r.json()["data"].get("period_start") is not None)

r = client.get("/api/usage/by-contact")
check("GET /usage/by-contact -> 200", r.status_code == 200)
by_contact = r.json()["data"]
check("GET /usage/by-contact -> is list", isinstance(by_contact, list))
check("GET /usage/by-contact -> has entries", len(by_contact) >= 1)

r = client.get("/api/usage/contact/5511999990001")
check("GET /usage/contact/{phone} -> 200", r.status_code == 200)
detail = r.json()["data"]
check("GET /usage/contact -> is list", isinstance(detail, list))
check("GET /usage/contact -> has records", len(detail) >= 2)

r = client.get("/api/usage/contact/0000000000")
check("GET /usage/contact/0000 -> empty", r.json()["data"] == [])

# Executions writer (Onda 0): agent_key/total_tokens/total_cost_usd were created
# in migration 0007 but never populated. Verify the new writer accumulates them.
_exec_id = execution_repo.create("5511999990001", "test")
_fresh = execution_repo.get_by_id(_exec_id)
check("executions -> nascem zerados", (_fresh.get("total_tokens") or 0) == 0 and (_fresh.get("total_cost_usd") or 0) == 0)
check("executions -> agent_key nasce vazio", not _fresh.get("agent_key"))
execution_repo.add_usage(_exec_id, total_tokens=100, cost_usd=0.0012)
execution_repo.add_usage(_exec_id, total_tokens=50, cost_usd=0.0003)  # 2nd call accumulates
execution_repo.set_agent_key(_exec_id, "default")
_after = execution_repo.get_by_id(_exec_id)
check("executions.total_tokens -> acumula (100+50)", _after.get("total_tokens") == 150)
check("executions.total_cost_usd -> acumula", abs((_after.get("total_cost_usd") or 0) - 0.0015) < 1e-9)
check("executions.agent_key -> populado", _after.get("agent_key") == "default")
execution_repo.add_usage(_exec_id, total_tokens=0, cost_usd=0.0)  # no-op
check("executions.add_usage(0,0) -> no-op", execution_repo.get_by_id(_exec_id).get("total_tokens") == 150)

# ═══════════════════════════════════════════════════════════════════
#  17. Logs
# ═══════════════════════════════════════════════════════════════════
section("Logs")

r = client.get("/api/logs")
check("GET /api/logs -> 200", r.status_code == 200)
check("GET /api/logs -> is list", isinstance(r.json()["data"], list))

r = client.get("/api/logs?limit=5")
check("GET /api/logs?limit=5 -> 200", r.status_code == 200)

r = client.delete("/api/logs")
check("DELETE /api/logs -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  18. Webhook Payloads
# ═══════════════════════════════════════════════════════════════════
section("Webhook Payloads")

r = client.get("/api/webhook-payloads")
check("GET /api/webhook-payloads -> 200", r.status_code == 200)
check("GET /api/webhook-payloads -> is list", isinstance(r.json()["data"], list))

# ═══════════════════════════════════════════════════════════════════
#  19. Webhook (incoming message simulation)
# ═══════════════════════════════════════════════════════════════════
section("Webhook")

# Presence event
r = client.post("/api/webhook", json={
    "type": "chat_presence",
    "data": [{"from": "5511999990001@s.whatsapp.net", "state": "composing"}],
})
check("POST /webhook (presence) -> 200", r.status_code == 200)

# is_from_me echo (should be ignored)
r = client.post("/api/webhook", json={
    "body": "echo test",
    "from": "5511999990001@s.whatsapp.net",
    "id": "echo_001",
    "is_from_me": True,
})
check("POST /webhook (echo) -> 200", r.status_code == 200)

# message.ack event
r = client.post("/api/webhook", json={
    "type": "message.ack",
    "data": [{"id": "msg_001", "chat_jid": "5511999990002@s.whatsapp.net", "ack": 3}],
})
check("POST /webhook (ack) -> 200", r.status_code == 200)

# Reply-quote extraction from inbound payloads (GOWA nests this inconsistently).
from server.routes.webhook import _extract_reply_to as _ext_reply
check("reply extract: flat replied_id", _ext_reply({"replied_id": "Q1"}) == "Q1")
check("reply extract: nested message.replied_id",
      _ext_reply({"message": {"text": "oi", "id": "SELF", "replied_id": "Q2"}}) == "Q2")
check("reply extract: context_info.stanza_id",
      _ext_reply({"context_info": {"stanza_id": "Q3"}}) == "Q3")
check("reply extract: quoted_message object id",
      _ext_reply({"quoted_message": {"id": "Q4"}}) == "Q4")
check("reply extract: own id is NOT a reply", _ext_reply({"id": "SELF", "body": "oi"}) is None)
check("reply extract: quoted text is NOT a reply id",
      _ext_reply({"quoted_message": "texto", "body": "oi"}) is None)

# ═══════════════════════════════════════════════════════════════════
#  19b. Runtime multi-canal (plano 11): ingest, inbox-por-canal, saída
# ═══════════════════════════════════════════════════════════════════
section("Multi-canal runtime (plano 11)")

import asyncio as _aio
from channels.base import Channel as _Channel, ChannelCapabilities as _Caps, SendResult as _SendResult
from channels.events import InboundEvent as _InboundEvent
from agent.handler import ProcessResult as _PR
from db.repositories import (conversation_repo as _conv11, inbox_repo as _inbox11,
                             contact_repo as _contact11, channel_repo as _chan11)

_deps = app.state.deps
_registry = _deps.channel_registry
_router = _deps.outbound_router


class _FakeChannel(_Channel):
    """In-test provider — records sends, parses a trivial inbound payload."""
    provider = "test"

    def __init__(self, channel_id, registry=None, credentials=None):
        super().__init__(channel_id, _Caps(
            qr=False, templates=False, groups=False, presence=False,
            reactions=True, media=True, inbound_route="path"))
        self.sent = []

    def status(self):
        return {"connected": True, "logged_in": True, "needs_qr": False, "error": None}

    def send_text(self, chat_id, text, *, reply_to=None, mentions=None):
        self.sent.append((chat_id, text))
        return _SendResult(ok=True, external_msg_id=f"out_{len(self.sent)}")

    def send_media(self, chat_id, kind, path_or_url, *, caption="", filename=None):
        return _SendResult(ok=True, external_msg_id="out_media")

    def parse_inbound(self, raw):
        return [_InboundEvent(
            channel_id=self.channel_id, provider="test", kind="message",
            external_msg_id=raw.get("id", ""), chat_id=raw.get("from", ""),
            sender_id=raw.get("from", ""), text=raw.get("text", ""))]


# Register provider + live instance + DB row + inbox (post-boot, as a real op would)
_registry.register_provider(_FakeChannel)
_fake = _FakeChannel("fake_ch")
_registry.add_channel("fake_ch", _fake)
_chan11.create(id="fake_ch", provider="test", display_name="Fake")
_fake_inbox = _inbox11.get_or_create_for_channel("fake_ch", name="Fake")

check("inbox-por-canal: fake_ch ganha inbox própria",
      _fake_inbox["channel_id"] == "fake_ch" and _fake_inbox["id"] != 1)
check("inbox_repo.get_by_channel(default) -> inbox 1",
      (_inbox11.get_by_channel("default") or {}).get("id") == 1)

# OutboundRouter: capability gating + routing + missing channel
check("router caps fake (media on, presence off)",
      _router.capabilities("fake_ch").media and not _router.capabilities("fake_ch").presence)
check("router caps default/gowa (presence+groups on)",
      _router.capabilities("default").presence and _router.capabilities("default").groups)
_rt = _router.send_text("fake_ch", "5511777770001", "via router")
check("router.send_text -> ok + external_msg_id", _rt.ok and bool(_rt.external_msg_id))
check("router roteou ao canal de destino", bool(_fake.sent) and _fake.sent[-1][1] == "via router")
check("router.send_text canal inexistente -> not ok",
      not _router.send_text("nao_existe", "x", "y").ok)
_router.send_presence("fake_ch", "5511777770001", "composing")  # no-op (presence=False)
check("router.send_presence em canal sem presença -> não envia nada",
      all(t != "__presence__" for _, t in _fake.sent))
_fake.sent.clear()

# Fase 6: janela de sessão (capability-driven, sem if provider ==)
class _FakeWindowed(_FakeChannel):
    def __init__(self, channel_id, registry=None, credentials=None):
        super().__init__(channel_id)
        self.capabilities.templates = True
        self.capabilities.session_window_hours = 24
_fwin = _FakeWindowed("fake_win")
_registry.add_channel("fake_win", _fwin)
check("session_open: gowa (janela=0) sempre aberto",
      _router.session_open("default", None) is True)
check("session_open: canal 0h sempre aberto mesmo com inbound antigo",
      _router.session_open("fake_ch", time.time() - 99 * 3600) is True)
check("session_open: dentro da janela de 24h -> aberto",
      _router.session_open("fake_win", time.time() - 3600) is True)
check("session_open: fora da janela de 24h -> fechado (exige template)",
      _router.session_open("fake_win", time.time() - 25 * 3600) is False)
check("session_open: sem inbound prévio em canal com janela -> fechado",
      _router.session_open("fake_win", None) is False)

# End-to-end ingest with the LLM mocked: inbound → conversa na inbox do canal → saída pelo canal
async def _drive_ingest():
    ev = _InboundEvent(channel_id="fake_ch", provider="test", kind="message",
                       external_msg_id="in_1", chat_id="5511777770001",
                       sender_id="5511777770001", sender_name="Fulano da Cloud",
                       text="oi canal oficial")
    await _deps.ingest_event(ev)
    t = _deps.state.processing_tasks.get(("fake_ch", "5511777770001"))
    if t:
        await t

# Force a fast, reply-enabled config for the drive (prior tests may have toggled
# these). api_key must be truthy or the handler short-circuits before the LLM.
_old_bd = settings.get("message_batch_delay", 3.0)
_old_ar = settings.get("auto_reply", True)
_old_key = agent_handler.api_key
settings.set("message_batch_delay", 0)
settings.set("response_delay_min", 0)
settings.set("response_delay_max", 0)
settings.set("split_message_delay", 0)
settings.set("auto_reply", True)
agent_handler.api_key = "test-key-fake"
with patch.object(agent_handler, "aprocess_message",
                  new=AsyncMock(return_value=_PR(reply="resposta do canal oficial"))):
    _aio.run(_drive_ingest())
settings.set("message_batch_delay", _old_bd)
settings.set("auto_reply", _old_ar)
agent_handler.api_key = _old_key

_fc = _contact11.get_by_phone("5511777770001")
check("ingest: contato resolvido por phone (D2 unificado)", _fc is not None)
_fi = _inbox11.get_by_channel("fake_ch")
_conv_fake = (_conv11.get_open_for_contact_inbox(_fc["id"], _fi["id"])
              if _fc and _fi else None)
check("ingest: conversa criada na inbox do canal (não na default)",
      _conv_fake is not None and _conv_fake["inbox_id"] == _fi["id"] and _fi["id"] != 1)
check("ingest: mensagem do usuário salva",
      bool(_fc) and any(m["content"] == "oi canal oficial"
                        for m in message_repo.get_all(_fc["id"])))
check("ingest: resposta roteada de volta PELO canal de origem",
      any("resposta do canal oficial" in t for _, t in _fake.sent))
check("ingest: pushName do remetente aplicado ao contato",
      bool(_fc) and (_fc.get("name") or "").lstrip("~") == "Fulano da Cloud")

# D1: o MESMO contato tem conversas SEPARADAS por canal (default vs fake)
_cd = _conv11.resolve_for_contact(_fc["id"], "5511777770001@s.whatsapp.net", inbox_id=1)
_cf = _conv11.resolve_for_contact(_fc["id"], "5511777770001@s.whatsapp.net", inbox_id=_fi["id"])
check("D1: conversas separadas por canal (mesmo contato/numero)", _cd["id"] != _cf["id"])
check("D1: cada conversa na sua inbox",
      _cd["inbox_id"] == 1 and _cf["inbox_id"] == _fi["id"])

# Fase 4: a lista de conversas expõe canal/provider (indicador na UI)
_r_convs = client.get("/api/conversations")
_all_convs = (_r_convs.json().get("data") or {}).get("conversations") or []
_fake_row = next((c for c in _all_convs if c.get("inbox_id") == _fi["id"]), None)
check("Fase 4: conversa do canal traz channel_provider",
      bool(_fake_row) and _fake_row.get("channel_provider") == "test")
check("Fase 4: conversa do canal traz channel_id",
      bool(_fake_row) and _fake_row.get("channel_id") == "fake_ch")
check("Fase 4: conversas do gowa trazem provider gowa",
      any(c.get("channel_provider") == "gowa" for c in _all_convs))

# Idempotência inbound por (channel_id, external_msg_id) — re-entrega não duplica
async def _drive_dup():
    ev = _InboundEvent(channel_id="fake_ch", provider="test", kind="message",
                       external_msg_id="in_1", chat_id="5511777770001",
                       sender_id="5511777770001", text="DUPLICADA")
    await _deps.ingest_event(ev)
_aio.run(_drive_dup())
check("idempotência: re-entrega do mesmo external_msg_id é descartada",
      not any(m["content"] == "DUPLICADA" for m in message_repo.get_all(_fc["id"])))

# HTTP: webhook por-canal → parse_inbound → ingest dispatched (wiring real)
_contact11.get_or_create("5511777770002")
_c2 = _contact11.get_by_phone("5511777770002")
_contact11.update(_c2["id"], ai_enabled=0)  # no LLM call in the background cycle
settings.set("message_batch_delay", 0)
r = client.post("/api/webhook/test/fake_ch",
                json={"id": "in_http", "from": "5511777770002", "text": "http oi"})
settings.set("message_batch_delay", _old_bd)
check("POST /api/webhook/test/fake_ch -> 200", r.status_code == 200)
check("POST inbound -> 1 evento parseado", r.json()["data"].get("events") == 1)
check("POST inbound -> evento ingerido (handled>=1)", r.json()["data"].get("handled", 0) >= 1)

# ── Conversa-cêntrico (plano 11 D1): leitura + unread + saída POR CONVERSA ──
section("Conversa-cêntrico (plano 11 D1)")

# _cd (inbox default) e _cf (inbox do canal fake) são duas conversas do MESMO
# contato (_fc). Mensagens distintas em cada uma provam que ler por conversation_id
# NÃO funde os canais — ao contrário de get_all(contact_id).
message_repo.add(_fc["id"], "user", "MSG_DEFAULT_ONLY", conversation_id=_cd["id"])
message_repo.add(_fc["id"], "user", "MSG_FAKE_ONLY", conversation_id=_cf["id"])
_msgs_d = message_repo.get_by_conversation(_cd["id"])
_msgs_f = message_repo.get_by_conversation(_cf["id"])
check("get_by_conversation: conversa default vê só a sua msg",
      any(m["content"] == "MSG_DEFAULT_ONLY" for m in _msgs_d)
      and not any(m["content"] == "MSG_FAKE_ONLY" for m in _msgs_d))
check("get_by_conversation: conversa do canal vê só a sua msg",
      any(m["content"] == "MSG_FAKE_ONLY" for m in _msgs_f)
      and not any(m["content"] == "MSG_DEFAULT_ONLY" for m in _msgs_f))
check("get_all (legado) ainda funde os dois canais (contraste do bug)",
      any(m["content"] == "MSG_DEFAULT_ONLY" for m in message_repo.get_all(_fc["id"]))
      and any(m["content"] == "MSG_FAKE_ONLY" for m in message_repo.get_all(_fc["id"])))

# GET /api/conversations/{id}/messages — thread escopado a UM canal
_rm = client.get(f"/api/conversations/{_cf['id']}/messages")
check("GET /api/conversations/{id}/messages -> 200", _rm.status_code == 200)
_dm = _rm.json().get("data") or {}
check("conversation messages: só as mensagens da conversa do canal",
      any(m["content"] == "MSG_FAKE_ONLY" for m in (_dm.get("messages") or []))
      and not any(m["content"] == "MSG_DEFAULT_ONLY" for m in (_dm.get("messages") or [])))
check("conversation messages: channel_id da conversa", _dm.get("channel_id") == "fake_ch")
check("conversation messages: conversa traz provider",
      (_dm.get("conversation") or {}).get("channel_provider") == "test")
check("conversation messages: contato embutido (shape do chat)",
      (_dm.get("contact") or {}).get("phone") == "5511777770001")
check("GET conversation messages: 404 em conversa inexistente",
      client.get("/api/conversations/99999/messages").status_code == 404)

# Lista enriquecida (sidebar conversa-cêntrica): preview + unread_count por conversa
_lc = ((client.get("/api/conversations").json().get("data") or {}).get("conversations") or [])
_lcf = next((c for c in _lc if c.get("id") == _cf["id"]), None)
check("lista conversas: row traz channel_id (sidebar por canal)",
      bool(_lcf) and _lcf.get("channel_id") == "fake_ch")
check("lista conversas: row traz last_message (preview)", bool(_lcf) and "last_message" in _lcf)
check("lista conversas: row traz unread_count por conversa",
      bool(_lcf) and isinstance(_lcf.get("unread_count"), int))

# Unread DERIVADO por conversa (unread_msg_ids ⋈ messages.conversation_id):
# abrir o thread de um canal não pode zerar o badge do outro canal.
message_repo.add(_fc["id"], "user", "UNREAD_D", msg_id="UMD_1", conversation_id=_cd["id"])
message_repo.add(_fc["id"], "user", "UNREAD_F", msg_id="UMF_1", conversation_id=_cf["id"])
_contact11.increment_unread(_fc["id"], "UMD_1")
_contact11.increment_unread(_fc["id"], "UMF_1")
check("unread por conversa: default conta a sua não-lida",
      (_conv11.get_with_channel(_cd["id"]) or {}).get("unread_count", 0) >= 1)
check("unread por conversa: canal conta a sua não-lida",
      (_conv11.get_with_channel(_cf["id"]) or {}).get("unread_count", 0) >= 1)
_read_ids = _conv11.mark_conversation_read(_cd["id"])
check("mark_conversation_read: retorna msg_ids só da conversa lida",
      "UMD_1" in _read_ids and "UMF_1" not in _read_ids)
check("mark_conversation_read: zera só a conversa lida",
      (_conv11.get_with_channel(_cd["id"]) or {}).get("unread_count", 0) == 0)
check("mark_conversation_read: NÃO zera a conversa do outro canal (D1)",
      (_conv11.get_with_channel(_cf["id"]) or {}).get("unread_count", 0) >= 1)

# Envio do operador é CHANNEL-AWARE (plano 11): conversation_id do canal → sai PELO
# canal, não pelo GOWA — exatamente o 2º bug (responder numa conversa Cloud ia pelo GOWA).
_fake.sent.clear()
mock_gowa_client.send_message.reset_mock()
_rs = client.post("/api/contacts/5511777770001/send",
                  json={"message": "OP_VIA_FAKE", "conversation_id": _cf["id"]})
check("send com conversation_id do canal -> 200", _rs.status_code == 200)
check("send roteado PELO canal da conversa (não GOWA)",
      any(t == "OP_VIA_FAKE" for _, t in _fake.sent) and not mock_gowa_client.send_message.called)
# Sem conversation_id -> fallback 'default' (GOWA): comportamento legado preservado
mock_gowa_client.send_message.reset_mock()
_fake.sent.clear()
_rs2 = client.post("/api/contacts/5511777770001/send", json={"message": "OP_VIA_GOWA"})
check("send sem conversation_id -> fallback GOWA (legado intacto)",
      _rs2.status_code == 200 and mock_gowa_client.send_message.called
      and not any(t == "OP_VIA_GOWA" for _, t in _fake.sent))

# ═══════════════════════════════════════════════════════════════════
#  20. QR / WhatsApp
# ═══════════════════════════════════════════════════════════════════
section("WhatsApp / QR")

r = client.get("/api/qr")
check("GET /api/qr -> 204 (no qr)", r.status_code == 204)

r = client.post("/api/qr/refresh")
check("POST /api/qr/refresh -> 200", r.status_code == 200)

r = client.post("/api/whatsapp/reconnect")
check("POST /whatsapp/reconnect -> 200", r.status_code == 200)
check("POST /whatsapp/reconnect -> gowa called", mock_gowa_client.reconnect.called)

r = client.post("/api/whatsapp/logout")
check("POST /whatsapp/logout -> 200", r.status_code == 200)
check("POST /whatsapp/logout -> gowa called", mock_gowa_client.logout.called)

# ═══════════════════════════════════════════════════════════════════
#  20b. Setup Wizard
# ═══════════════════════════════════════════════════════════════════
section("Setup Wizard")


class _FakeTechifyResp:
    """Stands in for an httpx Response from the Techify provisioning route."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _fake_async_client(resp):
    """Build a patch target mimicking httpx.AsyncClient as an async CM."""
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


# request-key: sends the provisioning WhatsApp message and arms polling
_send_calls_before = mock_gowa_client.send_message.call_count
r = client.post("/api/setup/request-key")
check("POST /api/setup/request-key -> 200", r.status_code == 200)
check("POST /api/setup/request-key -> returns number",
      r.json()["data"].get("number") == "5511999990001")
check("POST /api/setup/request-key -> WhatsApp message sent",
      mock_gowa_client.send_message.call_count == _send_calls_before + 1)

# key-status: account not ready yet
with patch("server.routes.setup.httpx.AsyncClient",
           _fake_async_client(_FakeTechifyResp(200, {"status": "pending"}))):
    r = client.get("/api/setup/key-status")
check("GET /api/setup/key-status (pending) -> 200", r.status_code == 200)
check("GET /api/setup/key-status -> pending", r.json()["data"]["status"] == "pending")

# key-status: key ready -> persisted to config
_provisioned_key = "sk-techify-provisioned-abcdef123456"
with patch("server.routes.setup.httpx.AsyncClient",
           _fake_async_client(_FakeTechifyResp(200, {
               "status": "ready", "api_key": _provisioned_key}))):
    r = client.get("/api/setup/key-status")
check("GET /api/setup/key-status (ready) -> 200", r.status_code == 200)
check("GET /api/setup/key-status -> ready", r.json()["data"]["status"] == "ready")
check("GET /api/setup/key-status -> key saved to config",
      config_repo.get("openrouter_api_key") == _provisioned_key)

# ═══════════════════════════════════════════════════════════════════
#  21. Sandbox
# ═══════════════════════════════════════════════════════════════════
section("Sandbox")

# sandbox/send requires a working LLM — mock it
with patch.object(agent_handler, "process_message") as mock_process:
    from agent.handler import ProcessResult
    mock_process.return_value = ProcessResult(reply="Resposta de teste", tool_calls=[])

    r = client.post("/api/sandbox/send", json={"phone": "sandbox_test", "message": "Oi"})
    check("POST /sandbox/send -> 200", r.status_code == 200)
    check("POST /sandbox/send -> has reply", "reply" in r.json().get("data", {}))
    check("POST /sandbox/send -> reply text", r.json()["data"]["reply"] == "Resposta de teste")

r = client.post("/api/sandbox/send", json={"phone": "", "message": "Oi"})
check("POST /sandbox/send (no phone) -> 400", r.status_code == 400)

r = client.post("/api/sandbox/send", json={"phone": "test", "message": ""})
check("POST /sandbox/send (no msg) -> 400", r.status_code == 400)

r = client.post("/api/sandbox/clear", json={"phone": "sandbox_test"})
check("POST /sandbox/clear -> 200", r.status_code == 200)

r = client.post("/api/sandbox/clear", json={})
check("POST /sandbox/clear (all) -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  22. Frontend routes (SPA)
# ═══════════════════════════════════════════════════════════════════
section("Frontend SPA Routes")

for path in ["/", "/painel", "/sandbox", "/costs", "/quick-replies", "/custom-attributes",
             "/runtime", "/users", "/conversations", "/ai"]:
    r = client.get(path)
    check(f"GET {path} -> 200", r.status_code == 200)

# Conversa-cêntrico (plano 11 D1): /conversations/<id> serve o SPA (refresh direto
# no chat de uma conversa) — espelha /contacts/<id>.
check("GET /conversations/1 (SPA) -> 200", client.get("/conversations/1").status_code == 200)
check("GET /contacts/1 (SPA) -> 200", client.get("/contacts/1").status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  23. Auth with password
# ═══════════════════════════════════════════════════════════════════
section("Auth — With Password")

# Set a password
r = client.put("/api/config", json={"web_password": "mysecret123"})
check("SET password -> 200", r.status_code == 200)

# Now auth should be required
r = client.get("/api/auth/check")
check("GET /auth/check (no token) -> 401", r.status_code == 401)

# Login
r = client.post("/api/auth/login", json={"password": "mysecret123"})
check("POST /auth/login -> 200", r.status_code == 200)
token = r.json()["data"]["token"]
check("POST /auth/login -> has token", len(token) > 0)

# Check with token
r = client.get("/api/auth/check", headers={"Authorization": f"Bearer {token}"})
check("GET /auth/check (valid token) -> 200", r.status_code == 200)
check("GET /auth/check -> authenticated", r.json()["data"]["authenticated"] is True)

# Wrong password
r = client.post("/api/auth/login", json={"password": "wrong"})
check("POST /auth/login (wrong) -> 401", r.status_code == 401)

# API endpoint without auth (should be blocked)
r = client.get("/api/config")
check("GET /api/config (no auth) -> 401", r.status_code == 401)

# API endpoint with auth
r = client.get("/api/config", headers={"Authorization": f"Bearer {token}"})
check("GET /api/config (with auth) -> 200", r.status_code == 200)

# Webhook should be exempt from auth
r = client.post("/api/webhook", json={"type": "unknown"})
check("POST /webhook (auth exempt) -> 200", r.status_code == 200)

# Health should be exempt
r = client.get("/health")
check("GET /health (auth exempt) -> 200", r.status_code == 200)

# Remove password to not affect other tests
r = client.put("/api/config", json={"web_password": ""}, headers={"Authorization": f"Bearer {token}"})
check("REMOVE password -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  Audit trail (plano 07)
# ═══════════════════════════════════════════════════════════════════
from db.repositories import audit_repo as _audit_repo  # noqa: E402

_audit_repo.add(action="config.update", resource_type="config", resource_id="k1",
                after={"openrouter_api_key": "sk-x", "v": 1})
_audit_repo.add(action="plugin.enable", resource_type="plugin", resource_id="lembretes",
                actor_type="user", actor_user_id=3, actor_label="Op")

r = client.get("/api/audit")
check("GET /api/audit -> 200", r.status_code == 200)
_d = r.json().get("data", {})
check("audit list tem items+total", _d.get("total", 0) >= 2 and len(_d.get("items", [])) >= 2)
check("audit segredo mascarado na listagem",
      all("sk-x" not in (it.get("after_json") or "") for it in _d["items"]))

r = client.get("/api/audit?action=plugin.enable")
check("GET /api/audit filtro action", r.json()["data"]["total"] == 1)
r = client.get("/api/audit?resource_type=config")
check("GET /api/audit filtro resource_type", r.json()["data"]["total"] >= 1)

r = client.get("/api/audit/actions")
check("GET /api/audit/actions -> 200", r.status_code == 200)
check("audit/actions lista actions",
      "config.update" in r.json()["data"]["actions"])

_before_export = _audit_repo.count()
r = client.get("/api/audit/export?format=csv")
check("GET /api/audit/export csv -> 200", r.status_code == 200)
check("export csv content-type", "text/csv" in r.headers.get("content-type", ""))
check("export csv tem header", "action" in r.text.splitlines()[0])
check("export é auditado (data.export)", _audit_repo.count() == _before_export + 1)
r = client.get("/api/audit/export?format=json")
check("export json content-type", "application/json" in r.headers.get("content-type", ""))
r = client.get("/api/audit/export?format=xml")
check("export formato inválido -> erro", r.json().get("ok") is False)

# ═══════════════════════════════════════════════════════════════════
#  Conversation tabs + unified agent assignment (plano 10)
# ═══════════════════════════════════════════════════════════════════
section("Conversation tabs + agent assignment (plano 10)")

from db.repositories import conversation_repo
import agent.agent_factory as _agent_factory

# Enriched contact list: every row carries its active conversation's fields, so
# the status/assignment tabs can filter + count client-side.
_rows = client.get("/api/contacts").json()["data"]
check("GET /api/contacts -> rows expose conversation_id", all("conversation_id" in c for c in _rows))
check("GET /api/contacts -> rows expose conv_status", all("conv_status" in c for c in _rows))
check("GET /api/contacts -> rows expose assignee_user_id", all("assignee_user_id" in c for c in _rows))
check("GET /api/contacts -> rows expose active_agent_key", all("active_agent_key" in c for c in _rows))

# Seed the default AI agent (lifespan is skipped in tests) so it's assignable.
_agent_factory.seed_default_agent(settings)

r = client.get("/api/conversations/assignable-agents")
check("GET /api/conversations/assignable-agents -> 200", r.status_code == 200)
_aa = r.json().get("data", {})
check("assignable-agents -> has users list", isinstance(_aa.get("users"), list))
check("assignable-agents -> has ai_agents list", isinstance(_aa.get("ai_agents"), list))
check("assignable-agents -> default AI agent present",
      any(a.get("agent_key") == "default" for a in _aa.get("ai_agents", [])))

# Human agents (created earlier in the suite — bootstrap + /api/users) are listed.
_users = client.get("/api/conversations/assignable-agents").json()["data"]["users"]
check("assignable-agents -> lists human agents", len(_users) >= 1)
_admin_id = _users[0]["id"]

# Create a conversation for Alice and exercise the unified assign-agent endpoint.
_alice = contact_repo.get_by_phone("5511999990001")
_conv = conversation_repo.resolve_for_contact(_alice["id"], "5511999990001@s.whatsapp.net")
_cid = _conv["id"]

# Assign to a HUMAN → assignee set, AI agent cleared, IA turned OFF.
r = client.post(f"/api/conversations/{_cid}/assign-agent", json={"kind": "user", "user_id": _admin_id})
check("assign-agent kind=user -> 200", r.status_code == 200)
_c = r.json()["data"]["conversation"]
check("assign-agent kind=user -> assignee set", _c.get("assignee_user_id") == _admin_id)
check("assign-agent kind=user -> AI agent cleared", not _c.get("active_agent_key"))
check("assign-agent kind=user -> IA desligada", _c.get("ai_active") in (0, False))
# A human took over → contact-level AI gate OFF (drives the "IA OFF" badge).
check("assign-agent kind=user -> contato IA OFF (badge)",
      contact_repo.get(_alice["id"])["ai_enabled"] is False)

# Assign to an AI agent → agent set, human cleared, IA turned ON.
r = client.post(f"/api/conversations/{_cid}/assign-agent", json={"kind": "ai", "agent_key": "default"})
check("assign-agent kind=ai -> 200", r.status_code == 200)
_c = r.json()["data"]["conversation"]
check("assign-agent kind=ai -> agent set", _c.get("active_agent_key") == "default")
check("assign-agent kind=ai -> human cleared", _c.get("assignee_user_id") is None)
check("assign-agent kind=ai -> IA ligada", _c.get("ai_active") in (1, True))
# An AI agent took over → contact-level AI gate back ON ("IA" badge).
check("assign-agent kind=ai -> contato IA ON (badge)",
      contact_repo.get(_alice["id"])["ai_enabled"] is True)

# Unassign → both cleared.
r = client.post(f"/api/conversations/{_cid}/assign-agent", json={"kind": "none"})
check("assign-agent kind=none -> 200", r.status_code == 200)
_c = r.json()["data"]["conversation"]
check("assign-agent kind=none -> human cleared", _c.get("assignee_user_id") is None)
check("assign-agent kind=none -> agent cleared", not _c.get("active_agent_key"))

# Validation.
r = client.post(f"/api/conversations/{_cid}/assign-agent", json={"kind": "user"})
check("assign-agent kind=user sem user_id -> 400", r.status_code == 400)
r = client.post(f"/api/conversations/{_cid}/assign-agent", json={"kind": "bogus"})
check("assign-agent kind inválido -> 400", r.status_code == 400)

# The enriched contact list now reflects Alice's open conversation.
_alice_row = next((c for c in client.get("/api/contacts").json()["data"]
                   if c["phone"] == "5511999990001"), None)
check("GET /api/contacts -> Alice carries her conversation", _alice_row and _alice_row.get("conversation_id") == _cid)
check("GET /api/contacts -> Alice conv_status open", _alice_row and _alice_row.get("conv_status") == "open")

# chat_presence ("digitando") deve carregar o conversation_id EXATO da conversa
# GOWA — assim o frontend escopa o indicador àquela conversa (e não a todas as
# conversas do número em outros canais). Conversa-cêntrico (plano 11).
_deps_pres = app.state.deps
_deps_pres.state.presence_conv_cache.clear()  # garante resolução fresca
_captured_pres = []
_orig_bcast = _deps_pres.ws_manager.broadcast
async def _capture_bcast(event, data):
    if event == "chat_presence":
        _captured_pres.append(data)
    return await _orig_bcast(event, data)
_deps_pres.ws_manager.broadcast = _capture_bcast
try:
    r = client.post("/api/webhook", json={
        "event": "chat_presence",
        "payload": {"from": "5511999990001@s.whatsapp.net", "state": "composing"},
    })
finally:
    _deps_pres.ws_manager.broadcast = _orig_bcast
check("POST /webhook chat_presence -> 200", r.status_code == 200)
_pres = _captured_pres[-1] if _captured_pres else {}
check("chat_presence broadcast -> channel_id default", _pres.get("channel_id") == "default")
check("chat_presence broadcast -> conversation_id = conversa GOWA da Alice",
      _pres.get("conversation_id") == _cid)
# Contato sem conversa GOWA → conversation_id None (frontend cai no fallback canal::phone).
_deps_pres.state.presence_conv_cache.clear()
_captured_pres.clear()
_deps_pres.ws_manager.broadcast = _capture_bcast
try:
    r = client.post("/api/webhook", json={
        "event": "chat_presence",
        "payload": {"from": "5511000000999@s.whatsapp.net", "state": "composing"},
    })
finally:
    _deps_pres.ws_manager.broadcast = _orig_bcast
check("chat_presence broadcast -> conversation_id None p/ contato sem conversa",
      (_captured_pres[-1] if _captured_pres else {}).get("conversation_id") is None)

# ═══════════════════════════════════════════════════════════════════
#  Summary
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
#  Custom attributes — conversation scope value validation (Onda 1)
# ═══════════════════════════════════════════════════════════════════
section("Custom Attributes — conversation value validation (Onda 1)")

client.post("/api/custom-attributes", json={
    "attribute_key": "valor_conv", "display_name": "Valor", "type": "number",
    "applies_to": "conversation"})
r = client.put(f"/api/conversations/{_conv2['id']}/info",
               json={"custom_attributes": {"valor_conv": "abc"}})
check("conv attr number inválido -> 400", r.status_code == 400)
r = client.put(f"/api/conversations/{_conv2['id']}/info",
               json={"custom_attributes": {"valor_conv": "42"}})
check("conv attr number normalizado -> 42.0",
      r.status_code == 200 and
      r.json()["data"]["conversation"]["custom_attributes"].get("valor_conv") == 42.0)
check("conv attr não vaza p/ escopo contato",
      "valor_conv" not in {d["attribute_key"]
                           for d in client.get("/api/custom-attributes?applies_to=contact").json()["data"]})

# ═══════════════════════════════════════════════════════════════════
#  Conversation labels — registry + per-conversation (Onda 3)
# ═══════════════════════════════════════════════════════════════════
section("Conversation Labels (Onda 3)")

r = client.post("/api/conversation-labels", json={"name": "Urgente", "color": "#ef4444"})
check("POST /conversation-labels -> 200", r.status_code == 200)
_lbl_urgente = r.json()["data"]
check("create label -> id + name", bool(_lbl_urgente.get("id")) and _lbl_urgente["name"] == "Urgente")
check("create label duplicada -> erro",
      client.post("/api/conversation-labels", json={"name": "Urgente", "color": "#000"}).json().get("ok") is False)
check("create label sem nome -> erro",
      client.post("/api/conversation-labels", json={"name": "", "color": "#000"}).json().get("ok") is False)
client.post("/api/conversation-labels", json={"name": "VIP", "color": "#8b5cf6"})

r = client.get("/api/conversation-labels")
check("GET /conversation-labels -> 200", r.status_code == 200)
check("registro global lista Urgente+VIP",
      {"Urgente", "VIP"} <= {l["name"] for l in r.json()["data"]})

r = client.put(f"/api/conversation-labels/{_lbl_urgente['id']}", json={"color": "#dc2626"})
check("PUT label cor -> 200 + atualizada", r.status_code == 200 and r.json()["data"]["color"] == "#dc2626")
check("PUT label rename colisão -> erro",
      client.put(f"/api/conversation-labels/{_lbl_urgente['id']}", json={"name": "VIP"}).json().get("ok") is False)
check("PUT label inexistente -> 404",
      client.put("/api/conversation-labels/999999", json={"name": "X"}).status_code == 404)

r = client.put(f"/api/conversations/{_conv2['id']}/labels", json={"labels": ["Urgente", "VIP"]})
check("PUT conv labels -> 200 + snapshot",
      r.status_code == 200 and set(r.json()["data"]["labels"]) == {"Urgente", "VIP"})
r = client.get(f"/api/conversations/{_conv2['id']}/labels")
check("GET conv labels -> 2 etiquetas", r.status_code == 200 and len(r.json()["data"]["labels"]) == 2)
check("PUT conv labels remove p/ snapshot menor",
      client.put(f"/api/conversations/{_conv2['id']}/labels", json={"labels": ["VIP"]}).json()["data"]["labels"] == ["VIP"])
check("PUT conv labels ignora nome inexistente",
      set(client.put(f"/api/conversations/{_conv2['id']}/labels",
                     json={"labels": ["VIP", "NaoExiste"]}).json()["data"]["labels"]) == {"VIP"})
check("PUT conv labels não-lista -> erro",
      client.put(f"/api/conversations/{_conv2['id']}/labels", json={"labels": "x"}).json().get("ok") is False)
check("PUT conv labels conversa inexistente -> 404",
      client.put("/api/conversations/999999/labels", json={"labels": []}).status_code == 404)

# delete cascades the link off the conversation
_vip_id = next(l["id"] for l in client.get("/api/conversation-labels").json()["data"] if l["name"] == "VIP")
check("DELETE label -> 200", client.delete(f"/api/conversation-labels/{_vip_id}").status_code == 200)
check("delete label -> link removido da conversa (cascade)",
      all(l["name"] != "VIP" for l in client.get(f"/api/conversations/{_conv2['id']}/labels").json()["data"]["labels"]))
check("DELETE label inexistente -> 404",
      client.delete("/api/conversation-labels/999999").status_code == 404)

# ── Filter dimension conv_labels (separate from contact-tag `labels`) ──
client.post("/api/conversation-labels", json={"name": "Filtravel", "color": "#06b6d4"})
client.put(f"/api/conversations/{_conv2['id']}/labels", json={"labels": ["Filtravel"]})
check("filter-schema inclui conv_labels",
      "conv_labels" in {d["key"] for d in client.get("/api/conversations/filter-schema").json()["data"]["dimensions"]})
r = client.post("/api/conversations/filter", json={
    "filters": [{"attribute_key": "conv_labels", "filter_operator": "in", "values": ["Filtravel"]}]})
check("filter conv_labels:in -> acha a conversa",
      r.status_code == 200 and any(c["id"] == _conv2["id"] for c in r.json()["data"]["conversations"]))
r = client.post("/api/conversations/filter", json={
    "filters": [{"attribute_key": "conv_labels", "filter_operator": "in", "values": ["Inexistente"]}]})
check("filter conv_labels nome inexistente -> não acha",
      all(c["id"] != _conv2["id"] for c in r.json()["data"]["conversations"]))
check("filter conv_labels equal_to -> 400 (só 'in')",
      client.post("/api/conversations/filter", json={
          "filters": [{"attribute_key": "conv_labels", "filter_operator": "equal_to", "values": ["Filtravel"]}]}).status_code == 400)

# ── System notice for conversation labels (grupo conv_labels) ──
_sn_cfg.set("system_notice_conv_labels", True)
client.post("/api/conversation-labels", json={"name": "Notif", "color": "#10b981"})
_lblcm = _CM("5500077766655")
_lblcm.add_message("user", "oi")
_lblconv = _conv_repo.get_open_for_contact(_lblcm.id)
_lbl_hdr = {"Authorization": f"Bearer {_mgrtok}"}
client.put(f"/api/conversations/{_lblconv['id']}/labels", json={"labels": ["Notif"]}, headers=_lbl_hdr)
_lbl_notices = [c for c in _notices(_lblconv["id"]) if "etiqueta" in c]
check("conv label add -> aviso de sistema 'etiqueta'", len(_lbl_notices) >= 1)
check("conv label add -> nomeia autor (Mgr)", any("Mgr" in c for c in _lbl_notices))
_sn_cfg.set("system_notice_conv_labels", False)
_before_lbl = len([c for c in _notices(_lblconv["id"]) if "etiqueta" in c])
client.put(f"/api/conversations/{_lblconv['id']}/labels", json={"labels": []}, headers=_lbl_hdr)
check("grupo conv_labels OFF -> nenhum aviso novo",
      len([c for c in _notices(_lblconv["id"]) if "etiqueta" in c]) == _before_lbl)
_sn_cfg.set("system_notice_conv_labels", True)

# ═══════════════════════════════════════════════════════════════════
#  Cloud API templates (Frente C)
# ═══════════════════════════════════════════════════════════════════
section("Cloud API Templates (Frente C)")

from channels.base import Channel as _Ch, ChannelCapabilities as _Caps, SendResult as _SR
from db.repositories import inbox_repo as _ibx_repo


class _FakeTplChannel(_Ch):
    provider = "fake_cloud"

    def __init__(self, channel_id):
        super().__init__(channel_id, _Caps(templates=True, session_window_hours=24))
        self.sent = []
        self._tpls = [{
            "name": "boas_vindas", "language": "pt_BR", "category": "MARKETING",
            "status": "APPROVED", "components": [
                {"type": "header", "format": "image"},
                {"type": "body", "text": "Olá {{1}}, pedido {{2}} confirmado!"},
                {"type": "buttons", "buttons": [{"type": "url", "text": "Rastrear", "url": "https://x/{{1}}"}]},
            ]}]

    def status(self):
        return {"connected": True, "logged_in": True, "needs_qr": False, "error": None}

    def send_text(self, *a, **k):
        return _SR(ok=True, external_msg_id="t")

    def send_media(self, *a, **k):
        return _SR(ok=True, external_msg_id="t")

    def parse_inbound(self, raw):
        return []

    def list_templates(self):
        return list(self._tpls)

    def send_template(self, chat_id, template_name, lang="pt_BR", components=None):
        self.sent.append({"chat_id": chat_id, "name": template_name, "lang": lang, "components": components})
        return _SR(ok=True, external_msg_id="tpl_msg_99")


_tpl_inbox = _ibx_repo.create(channel_id="cloud_test", name="Cloud Test")
_tpl_ci = _ci_repo.get_or_create(inbox_id=_tpl_inbox["id"], contact_id=_cid, source_id=f"cloud:{_cid}")
_tpl_conv = _conv_repo.create(inbox_id=_tpl_inbox["id"], contact_id=_cid, contact_inbox_id=_tpl_ci["id"])
app.state.deps.channel_registry.add_channel("cloud_test", _FakeTplChannel("cloud_test"))

check("GET templates (canal default GOWA) -> supported=false",
      client.get(f"/api/conversations/{_conv2['id']}/templates").json()["data"]["supported"] is False)
r = client.get(f"/api/conversations/{_tpl_conv['id']}/templates")
check("GET templates (cloud) -> supported=true", r.json()["data"]["supported"] is True)
check("GET templates (cloud) -> lista boas_vindas",
      any(t["name"] == "boas_vindas" for t in r.json()["data"]["templates"]))
check("GET templates conversa inexistente -> 404",
      client.get("/api/conversations/999999/templates").status_code == 404)

r = client.post(f"/api/conversations/{_tpl_conv['id']}/send-template", json={
    "template_name": "boas_vindas", "language": "pt_BR",
    "components": [
        {"type": "header", "parameters": [{"type": "image", "image": {"link": "https://x/i.jpg"}}]},
        {"type": "body", "parameters": [{"type": "text", "text": "Alice"}, {"type": "text", "text": "123"}]},
    ],
    "preview_text": "Olá Alice, pedido 123 confirmado!"})
check("POST send-template -> 200", r.status_code == 200)
check("send-template -> msg_id retornado", r.json()["data"].get("msg_id") == "tpl_msg_99")
_fake_ch = app.state.deps.channel_registry.get("cloud_test")
check("send-template -> canal recebeu name + components",
      bool(_fake_ch.sent) and _fake_ch.sent[-1]["name"] == "boas_vindas"
      and len(_fake_ch.sent[-1]["components"]) == 2)
with _get_engine().connect() as _conn:
    _tpl_saved = _conn.execute(
        _sa_select(_msgs_t.c.id)
        .where(_msgs_t.c.contact_id == _cid)
        .where(_msgs_t.c.content.like("%pedido 123%"))
        .limit(1)).first()
check("send-template -> mensagem persistida no fio", _tpl_saved is not None)

check("send-template canal sem suporte -> 400",
      client.post(f"/api/conversations/{_conv2['id']}/send-template",
                  json={"template_name": "x"}).status_code == 400)
check("send-template sem template_name -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/send-template", json={}).status_code == 400)
check("send-template components não-lista -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/send-template",
                  json={"template_name": "x", "components": "nope"}).status_code == 400)

# Compositor hints on the chat-messages endpoint.
r = client.get(f"/api/conversations/{_tpl_conv['id']}/messages")
check("conv messages (cloud) -> templates_supported=true", r.json()["data"].get("templates_supported") is True)
check("conv messages (cloud, sem inbound) -> session_open=false (janela 24h)",
      r.json()["data"].get("session_open") is False)
check("conv messages (default) -> templates_supported=false",
      client.get(f"/api/conversations/{_conv2['id']}/messages").json()["data"].get("templates_supported") is False)

# ── WhatsAppCloudChannel.list_templates parsing (mock Graph API) ──
section("WhatsApp Cloud — list_templates parsing (Frente C)")
import importlib.util as _ilu
_wac_spec = _ilu.spec_from_file_location(
    "wac_under_test", "assets/plugin_examples/whatsapp_cloud/channels.py")
_wac = _ilu.module_from_spec(_wac_spec)
_wac_spec.loader.exec_module(_wac)


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
        self.text = ""

    def json(self):
        return self._p


class _FakeHttpClient:
    def __init__(self, pages):
        self._pages = pages
        self._i = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, params=None):
        p = self._pages[min(self._i, len(self._pages) - 1)]
        self._i += 1
        return p


_pages = [
    _Resp(200, {"data": [
        {"name": "t1", "status": "APPROVED", "language": "pt_BR", "category": "MARKETING",
         "components": [{"type": "BODY", "text": "Oi {{1}}"}]},
        {"name": "t2_pending", "status": "PENDING", "language": "pt_BR"},
    ], "paging": {"next": "https://graph/next-page"}}),
    _Resp(200, {"data": [
        {"name": "t3", "status": "APPROVED", "language": "en", "category": "UTILITY", "components": []},
    ]}),
]
_orig_httpx_client = _wac.httpx.Client
_wac.httpx.Client = lambda *a, **k: _FakeHttpClient(_pages)
try:
    _ch = _wac.WhatsAppCloudChannel("cloud_unit", credentials={
        "waba_id": "WABA1", "access_token": "TOK", "phone_number_id": "PN"})
    _tpls = _ch.list_templates()
finally:
    _wac.httpx.Client = _orig_httpx_client
check("list_templates -> filtra APPROVED (PENDING fora)",
      {t["name"] for t in _tpls} == {"t1", "t3"})
check("list_templates -> seguiu paginação (2 páginas)", len(_tpls) == 2)
check("list_templates -> normaliza type p/ minúsculas",
      _tpls[0]["components"][0]["type"] == "body")
check("list_templates sem waba_id -> []",
      _wac.WhatsAppCloudChannel("x", credentials={"access_token": "T"}).list_templates() == [])

print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'='*60}")

if errors:
    print("\nFailed tests:")
    for e in errors:
        print(e)

# Cleanup temp dir
import shutil
try:
    shutil.rmtree(_tmpdir, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if failed else 0)
