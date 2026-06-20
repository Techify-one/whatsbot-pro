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

r = client.post("/api/quick-replies", json={"short_code": "oi-anna", "content": "Olá! Sou a Atendente."})
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
r = client.put(f"/api/quick-replies/{_qr['id']}", json={"content": "Olá! Atendente aqui."})
check("PUT /quick-replies -> 200", r.status_code == 200)
check("PUT /quick-replies -> content atualizado", r.json()["data"]["content"] == "Olá! Atendente aqui.")

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

# ── Fluxo vivo (Fase 2): ContactMemory.add_message resolve+stampa conversation_id ──
from agent.memory import ContactMemory as _CM
from db.tables import messages as _msgs_t
_cm = _CM("5500011122233")  # fresh phone -> cria contato + conversa
_cm.add_message("user", "olá mundo")
with _get_engine().connect() as _conn:
    _last = _conn.execute(
        _sa_select(_msgs_t.c.conversation_id, _msgs_t.c.content)
        .where(_msgs_t.c.contact_id == _cm.id)
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
             "/runtime", "/users", "/conversations"]:
    r = client.get(path)
    check(f"GET {path} -> 200", r.status_code == 200)

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
#  Summary
# ═══════════════════════════════════════════════════════════════════

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
