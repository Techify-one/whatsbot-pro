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

# Load-bearing (plano 13): the test's Settings() resolves data_dir to the repo
# root, so create_app reads the REAL storages/plugins + assets/plugin_examples.
# This flag makes bootstrap_gowa_upgrade a no-op so the suite never copies/enables
# the bundled gowa plugin into the git-ignored storages/plugins (which would change
# create_app behavior and dirty the tree). Set BEFORE importing server.app.
os.environ["WHATSBOT_TEST"] = "1"

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
# plano 27: connection_state must be stubbed — a bare MagicMock is truthy and
# would make status()/get_qr_code() read "connected" everywhere.
mock_gowa_client.connection_state = MagicMock(
    return_value={"connected": False, "logged_in": False})
mock_gowa_client.get_own_number = MagicMock(return_value="5511999990001")
# Lookups parse_gowa_inbound makes via the client (return concrete values, not
# bare MagicMocks, so contact.save() doesn't try to persist a Mock — plano 13).
mock_gowa_client.get_group_name = MagicMock(return_value="Grupo Teste")
mock_gowa_client.can_bot_send_in_group = MagicMock(return_value=True)
mock_gowa_client.is_chat_archived = MagicMock(return_value=False)
mock_gowa_client.get_message_filename = MagicMock(return_value="")

# Create real Settings and AgentHandler (backed by test DB)
settings = Settings()
agent_handler = AgentHandler(
    api_key="test-key-fake",
    max_context_messages=10,
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
check("GET /api/config -> has audio_model field", "audio_model" in data)
check("GET /api/config -> has API key field", "openrouter_api_key" in data)
check("GET /api/config -> sem chave legada system_prompt (plano 22)",
      "system_prompt" not in data)
check("GET /api/config -> sem chave legada model (plano 22)", "model" not in data)
check("GET /api/config -> sem flag legada ai_engine_enabled (plano 22)",
      "ai_engine_enabled" not in data)
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
# Permalink de mensagem (estilo Chatwoot): cada msg expõe conversation_id p/ ancorar o link.
check("GET /api/contacts/{phone} -> messages expose conversation_id",
      all("conversation_id" in m for m in data["messages"]))
check("GET /api/contacts/{phone} -> has tags", isinstance(data.get("tags"), list))
check("GET /api/contacts/{phone} -> has observations", isinstance(data.get("info", {}).get("observations"), list))

# Channel-scoped load (multicanal): escolher uma caixa de entrada NUNCA pode mostrar
# a conversa de OUTRO canal do mesmo número. Com ?channel_id, o thread é escopado à
# conversa daquele canal (vazio se não houver) — nunca funde as mensagens legadas.
_unscoped = client.get("/api/contacts/5511999990001").json()["data"]["messages"]
r = client.get("/api/contacts/5511999990001?channel_id=default")
check("GET /api/contacts?channel_id -> 200", r.status_code == 200)
_sd = r.json()["data"]
check("GET /api/contacts?channel_id -> echoes channel", _sd.get("channel_id") == "default")
check("GET /api/contacts?channel_id -> escopa (sem fundir canais)",
      len(_sd.get("messages", [])) < len(_unscoped))
check("GET /api/contacts?channel_id -> conversation_id presente na resposta",
      "conversation_id" in _sd)
# Compositor hints (plano 21): mesmo sem conversa, o thread channel-scoped informa se
# o canal aceita template e se a janela de texto livre está aberta — senão o botão de
# template some ao abrir um canal Cloud novo. GOWA (default) → sem template, sempre aberto.
check("GET /api/contacts?channel_id -> templates_supported presente",
      "templates_supported" in _sd)
check("GET /api/contacts?channel_id -> session_open presente",
      "session_open" in _sd)
check("GET /api/contacts?channel_id=default (GOWA) -> sem template, janela aberta",
      _sd.get("templates_supported") is False and _sd.get("session_open") is True)

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
# Regressão (C2): a nota retornada/transmitida carrega conversation_id, para o painel
# conversa-cêntrico rotear ao vivo (sem ele a nota some em canal não-default).
check("POST /private-message -> carries conversation_id (routing)",
      "conversation_id" in _pn and _pn.get("conversation_id") == private_notes[-1].get("conversation_id"))
# Regressão (C4): o _id retornado é o da PRÓPRIA linha inserida (add_message agora
# retorna a linha), não um get_last racy que pegaria a última msg do contato.
check("POST /private-message -> _id is the inserted row (no get_last race)",
      _pn.get("_id") == private_notes[-1].get("_id"))
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
    "address": "Rua das Flores, 123 - Centro",
    "observations": ["VIP client", "Prefers morning calls"],
})
check("PUT /info -> 200", r.status_code == 200)
info = r.json()["data"]
check("PUT /info -> name updated", info.get("name") == "Alice Updated")
check("PUT /info -> email updated", info.get("email") == "alice_new@test.com")
check("PUT /info -> address updated", info.get("address") == "Rua das Flores, 123 - Centro")

# Verify persistence
r = client.get("/api/contacts/5511999990001")
data = r.json()["data"]
check("PUT /info -> persisted name", data["name"] == "Alice Updated")
check("PUT /info -> persisted address (top-level)", data.get("address") == "Rua das Flores, 123 - Centro")
check("PUT /info -> persisted address (info)",
      data.get("info", {}).get("address") == "Rua das Flores, 123 - Centro")
# Observations are a full replace — assert exact content, not just length
check("PUT /info -> persisted observations (content)",
      set(data.get("info", {}).get("observations", [])) == {"VIP client", "Prefers morning calls"})

# Clearing a scalar field: an empty string is an intentional clear (replace semantics)
r = client.put("/api/contacts/5511999990001/info", json={"company": ""})
check("PUT /info (clear company) -> 200", r.status_code == 200)
r = client.get("/api/contacts/5511999990001")
data = r.json()["data"]
check("PUT /info -> company cleared", (data.get("company") or "") == "")
# A partial body must NOT wipe untouched fields
check("PUT /info -> address untouched after partial PUT",
      data.get("address") == "Rua das Flores, 123 - Centro")

# Whitespace-only observation is filtered out (route + repo both strip blanks)
r = client.put("/api/contacts/5511999990001/info", json={"observations": ["Boa", "   "]})
check("PUT /info (obs whitespace) -> 200", r.status_code == 200)
r = client.get("/api/contacts/5511999990001")
check("PUT /info -> only non-blank observation kept",
      r.json()["data"].get("info", {}).get("observations", []) == ["Boa"])

# Error contract: a failure must carry a non-empty error string (bug #3, backend half)
r = client.put("/api/contacts/5511999990001/info", json={"custom_attributes": "notadict"})
_e = r.json()
check("PUT /info (invalid custom_attributes) -> ok False", _e.get("ok") is False)
check("PUT /info (invalid) -> error is non-empty str",
      isinstance(_e.get("error"), str) and _e.get("error") != "")

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

# A new global tag links once it exists — the backend contract the panel relies
# on (create-before-link). set_contact_tags only links names present in `tags`.
client.post("/api/tags", json={"name": "novatag", "color": "#123456"})
client.put("/api/contacts/5511999990001/tags", json={"tags": ["vip", "lead", "novatag"]})
r = client.get("/api/contacts/5511999990001")
check("PUT /tags (new global tag) -> persisted", "novatag" in r.json()["data"].get("tags", []))

# A name that doesn't exist globally is silently dropped (not persisted) — this
# is why the panel must create the tag first instead of sending a raw name.
client.put("/api/contacts/5511999990001/tags", json={"tags": ["vip", "naoexiste"]})
r = client.get("/api/contacts/5511999990001")
check("PUT /tags (unknown name) -> not persisted", "naoexiste" not in r.json()["data"].get("tags", []))
check("PUT /tags (unknown name) -> known tag still persisted", "vip" in r.json()["data"].get("tags", []))

# Non-existent contact (use different number not auto-created elsewhere)
r = client.put("/api/contacts/9999999999/tags", json={"tags": ["vip"]})
check("PUT /contacts/9999/tags -> 404", r.status_code == 404)
# Error contract on a real failure path (the panel surfaces res.error): a failure
# must carry ok:False + a non-empty error string.
check("PUT /tags (404) -> ok False + non-empty error",
      r.json().get("ok") is False and isinstance(r.json().get("error"), str) and r.json().get("error") != "")

# ═══════════════════════════════════════════════════════════════════
#  15a2. Saved conversation filters (presets nomeados por usuário)
# ═══════════════════════════════════════════════════════════════════
section("Saved conversation filters")

# Empty to start
r = client.get("/api/me/conversation-filters")
check("GET /api/me/conversation-filters -> 200", r.status_code == 200)
check("GET /api/me/conversation-filters -> empty list", r.json()["data"] == [])

# Create
_spec = {"statusFilter": "open", "assignmentTab": "mine", "sortBy": "activity",
         "tagFilter": ["vip"], "advFilters": [{"dim": "tag", "op": "eq", "value": "vip"}]}
r = client.post("/api/me/conversation-filters", json={"name": "VIPs abertas", "spec": _spec})
check("POST /api/me/conversation-filters -> 200", r.status_code == 200)
_fid = r.json()["data"]["id"]
check("POST saved-filter -> name", r.json()["data"]["name"] == "VIPs abertas")
check("POST saved-filter -> spec persisted", r.json()["data"]["spec"]["assignmentTab"] == "mine")

# Missing name
r = client.post("/api/me/conversation-filters", json={"name": "", "spec": _spec})
check("POST saved-filter (no name) -> ok False", r.json().get("ok") is False)

# Invalid spec
r = client.post("/api/me/conversation-filters", json={"name": "ruim", "spec": "naodict"})
check("POST saved-filter (bad spec) -> ok False", r.json().get("ok") is False)

# Duplicate name (case-insensitive)
r = client.post("/api/me/conversation-filters", json={"name": "vips abertas", "spec": _spec})
check("POST saved-filter (dup name) -> ok False", r.json().get("ok") is False)

# List now has one
r = client.get("/api/me/conversation-filters")
check("GET saved-filters -> 1 item", len(r.json()["data"]) == 1)

# Rename + overwrite spec
r = client.put(f"/api/me/conversation-filters/{_fid}",
               json={"name": "VIPs", "spec": {**_spec, "statusFilter": "all"}})
check("PUT saved-filter -> 200", r.status_code == 200)
check("PUT saved-filter -> renamed", r.json()["data"]["name"] == "VIPs")
check("PUT saved-filter -> spec updated", r.json()["data"]["spec"]["statusFilter"] == "all")

# Update unknown id
r = client.put("/api/me/conversation-filters/999999", json={"name": "x"})
check("PUT saved-filter (404) -> 404", r.status_code == 404)

# Delete
r = client.delete(f"/api/me/conversation-filters/{_fid}")
check("DELETE saved-filter -> 200", r.status_code == 200)
r = client.delete(f"/api/me/conversation-filters/{_fid}")
check("DELETE saved-filter (404) -> 404", r.status_code == 404)

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

# Clearing a custom attribute: an explicit null removes the key (bug #5)
r = client.put(f"/api/contacts/{_caphone}/info", json={"custom_attributes": {"vip": None}})
check("PUT /info (clear custom attr via null) -> 200", r.status_code == 200)
r = client.get(f"/api/contacts/{_caphone}")
_ca2 = r.json()["data"].get("custom_attributes", {})
check("PUT /info -> custom attr 'vip' removed", "vip" not in _ca2)
check("PUT /info -> other custom attr 'plano' kept", _ca2.get("plano") == "premium")

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
# Auto-geração do id quando o body não envia "id" (usuário só escolhe display_name)
import re as _re_chan
r = client.post("/api/channels", json={"provider": "whatsapp_cloud", "display_name": "Auto Cloud"})
check("POST /channels sem id -> 200", r.status_code == 200)
check("POST /channels sem id -> id auto <provider>_<hex>",
      bool(_re_chan.match(r"^whatsapp_cloud_[0-9a-f]{8}$", (r.json()["data"] or {}).get("id", ""))))

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

# Usuários atribuíveis (picker de agentes na criação do canal) — channel.manage.
r = client.get("/api/channels/assignable-users")
check("GET /channels/assignable-users -> 200", r.status_code == 200)
check("GET assignable-users -> users é lista", isinstance(r.json()["data"].get("users"), list))

# Membros da inbox de um canal (agentes que veem/recebem a caixa) — channel.manage.
r = client.get("/api/channels/default/members")
check("GET /channels/default/members -> 200", r.status_code == 200)
_m = r.json()["data"]
check("GET members -> tem inbox_id", isinstance(_m.get("inbox_id"), int))
check("GET members -> member_ids é lista", isinstance(_m.get("member_ids"), list))
check("GET members -> users é lista", isinstance(_m.get("users"), list))
r = client.get("/api/channels/inexistente/members")
check("GET members canal desconhecido -> 404", r.status_code == 404)
# Cria um usuário e o atribui como membro da inbox do canal default.
from db.repositories import user_repo as _urepo
_u = _urepo.create(email="agente_inbox@x.com", name="Agente Inbox",
                   password_hash="x", role_keys=["atendente"])
r = client.put("/api/channels/default/members", json={"user_ids": [_u["id"]]})
check("PUT members -> 200", r.status_code == 200)
check("PUT members -> persiste o membro", _u["id"] in r.json()["data"]["member_ids"])
r = client.get("/api/channels/default/members")
check("GET members -> reflete o membro salvo", _u["id"] in r.json()["data"]["member_ids"])
r = client.put("/api/channels/default/members", json={"user_ids": []})
check("PUT members -> esvazia o conjunto", r.json()["data"]["member_ids"] == [])
r = client.put("/api/channels/default/members", json={"user_ids": "nope"})
check("PUT members tipo inválido -> 400", r.status_code == 400)
_urepo.delete(_u["id"])

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
# Soft-delete (default): arquiva, preserva credenciais/histórico, some da lista.
r = client.delete("/api/channels/cloud_teste")
check("DELETE /channels -> 200 (soft-delete)", r.status_code == 200)
check("DELETE -> arquivado (archived=True)", r.json()["data"].get("archived") is True)
check("DELETE soft -> credenciais preservadas", _ccrepo.get("cloud_teste", "access_token") is not None)
_archived_list = client.get("/api/channels?archived=true").json()["data"]
_archived_ids = {c["id"] for c in (_archived_list if isinstance(_archived_list, list) else _archived_list.get("channels", []))}
check("GET /channels?archived=true -> inclui o canal arquivado", "cloud_teste" in _archived_ids)
_live_list = client.get("/api/channels").json()["data"]
_live_ids = {c["id"] for c in (_live_list if isinstance(_live_list, list) else _live_list.get("channels", []))}
check("GET /channels -> esconde o arquivado", "cloud_teste" not in _live_ids)
# Restore: volta para a lista.
r = client.post("/api/channels/cloud_teste/restore")
check("POST /channels/{id}/restore -> 200", r.status_code == 200)
check("restore -> archived=0", r.json()["data"].get("archived") == 0)
# Purge: hard-delete remove credenciais + inbox (CASCADE).
r = client.delete("/api/channels/cloud_teste?purge=true")
check("DELETE ?purge=true -> 200", r.status_code == 200)
check("DELETE purge -> purged=True", r.json()["data"].get("purged") is True)
check("DELETE purge -> credenciais removidas", _ccrepo.get("cloud_teste", "access_token") is None)

# ── Sync de nome canal→inbox + listagem sem órfãs (plano inboxes/canais §4.1/§4.6) ──
r = client.post("/api/channels", json={
    "id": "sync_ch", "provider": "whatsapp_cloud", "display_name": "Nome Antigo"})
check("POST /channels (sync_ch) -> 200", r.status_code == 200)
_inbx = client.get("/api/inboxes").json()["data"]
_inbx = _inbx if isinstance(_inbx, list) else _inbx.get("inboxes", [])
check("GET /inboxes -> inbox do canal com nome do canal",
      any(i["channel_id"] == "sync_ch" and i["name"] == "Nome Antigo" for i in _inbx))
client.put("/api/channels/sync_ch", json={"display_name": "Nome Novo"})
_inbx = client.get("/api/inboxes").json()["data"]
_inbx = _inbx if isinstance(_inbx, list) else _inbx.get("inboxes", [])
check("PUT display_name -> inbox renomeada (sync)",
      any(i["channel_id"] == "sync_ch" and i["name"] == "Nome Novo" for i in _inbx))
# Arquivar esconde a inbox da listagem (JOIN exclui canal arquivado).
client.delete("/api/channels/sync_ch")
_inbx = client.get("/api/inboxes").json()["data"]
_inbx = _inbx if isinstance(_inbx, list) else _inbx.get("inboxes", [])
check("GET /inboxes -> esconde inbox de canal arquivado",
      not any(i["channel_id"] == "sync_ch" for i in _inbx))
client.delete("/api/channels/sync_ch?purge=true")

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
check("RBAC seed -> 28 permissions", _perm_count == 28)
check("RBAC seed -> role_permissions populated (gestor 24 + atendente 5)", _rp_count == 29)
with _get_engine().connect() as _conn:
    _perm_keys = {r[0] for r in _conn.execute(_sa_select(_perms_t.c.key))}
check("RBAC seed -> template.create/template.delete present",
      {"template.create", "template.delete"} <= _perm_keys)

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
check("admin me -> all 28 permissions", len([p for p in _perms if p != "*"]) == 28)

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
check("gestor resolver -> 24 perms, no '*'", "*" not in _gperms and len(_gperms) == 24)
check("gestor lacks users.manage", "users.manage" not in _gperms)
check("gestor has template.create/template.delete",
      {"template.create", "template.delete"} <= _gperms)
check("admin resolver -> short-circuit '*'", "*" in _rrepo.user_permissions(_admin["id"]))

# ── Users CRUD + permission gating (Fases 4-5) ─────────────────────
r = client.get("/api/roles")
check("GET /api/roles -> 200", r.status_code == 200)
check("GET /api/roles -> 3 roles + 28 perms",
      len(r.json()["data"]["roles"]) == 3 and len(r.json()["data"]["permissions"]) == 28)

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

# Caixas de entrada por usuário (inbox_members editado na tela de Usuários)
_roles_data = client.get("/api/roles").json()["data"]
check("GET /api/roles -> expõe inboxes (lista)", isinstance(_roles_data.get("inboxes"), list))
_inboxes_f1 = _roles_data.get("inboxes") or []
if _inboxes_f1:
    _ib_id = _inboxes_f1[0]["id"]
    r = client.put(f"/api/users/{_att_id}", json={"inbox_ids": [_ib_id]})
    check("PUT /api/users (inbox_ids) -> 200", r.status_code == 200)
    check("PUT /api/users -> inbox_ids na resposta",
          r.json()["data"]["user"].get("inbox_ids") == [_ib_id])
    _list = client.get("/api/users").json()["data"]["users"]
    _row = next((x for x in _list if x["id"] == _att_id), None)
    check("GET /api/users -> inbox_ids persistido", bool(_row) and _row.get("inbox_ids") == [_ib_id])
    r = client.put(f"/api/users/{_att_id}", json={"inbox_ids": []})
    check("PUT /api/users -> limpa inbox_ids", r.json()["data"]["user"].get("inbox_ids") == [])

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
r = client.put("/api/config", json={"auto_reply": True}, headers=_chdr)
check("PUT /api/config (no settings.manage) -> 403", r.status_code == 403)
r = client.get("/api/contacts", headers=_chdr)
check("GET /api/contacts (has contact.read) -> 200", r.status_code == 200)
r = client.get("/api/users", headers=_chdr)
check("GET /api/users (no users.manage) -> 403", r.status_code == 403)
r = client.post("/api/quick-replies", json={"short_code": "x", "content": "y"}, headers=_chdr)
check("POST /api/quick-replies (no quickreply.manage) -> 403", r.status_code == 403)
r = client.put("/api/contacts/5511999/info", json={"name": "x"}, headers=_chdr)
check("PUT /api/contacts/{p}/info (no contact.write) -> 403", r.status_code == 403)

# ── Plano 24: new CRUD gates (custom user has neither) ─────────────
r = client.post("/api/tags", json={"name": "z", "color": "#fff"}, headers=_chdr)
check("POST /api/tags (no tag.manage) -> 403", r.status_code == 403)
r = client.delete("/api/tags/qualquer", headers=_chdr)
check("DELETE /api/tags/{n} (no tag.manage) -> 403", r.status_code == 403)
r = client.post("/api/conversation-labels", json={"name": "z"}, headers=_chdr)
check("POST /api/conversation-labels (no conversation_label.manage) -> 403", r.status_code == 403)
r = client.post("/api/sandbox/send", json={"phone": "5511999", "message": "oi"}, headers=_chdr)
check("POST /api/sandbox/send (no sandbox.use) -> 403", r.status_code == 403)
r = client.get("/api/usage/summary", headers=_chdr)
check("GET /api/usage/summary (no usage.read) -> 403", r.status_code == 403)
r = client.get("/api/executions", headers=_chdr)
check("GET /api/executions (no execution.read) -> 403", r.status_code == 403)
r = client.delete("/api/executions", headers=_chdr)
check("DELETE /api/executions (no execution.delete) -> 403", r.status_code == 403)
r = client.delete("/api/contacts/5511999", headers=_chdr)
check("DELETE /api/contacts/{p} (no contact.delete) -> 403", r.status_code == 403)
r = client.delete("/api/conversations/999999", headers=_chdr)
check("DELETE /api/conversations/{id} (no conversation.delete) -> 403", r.status_code == 403)
r = client.post("/api/custom-attributes", json={"attribute_key": "z", "display_name": "Z"}, headers=_chdr)
check("POST /api/custom-attributes (no custom_attribute.manage) -> 403", r.status_code == 403)
r = client.get("/api/admin/database", headers=_chdr)
check("GET /api/admin/database (no database.manage) -> 403", r.status_code == 403)
r = client.get("/api/ai/agents", headers=_chdr)
check("GET /api/ai/agents (no agent.manage) -> 403", r.status_code == 403)

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
      "permission_keys" in _by_key["gestor"] and len(_by_key["gestor"]["permission_keys"]) == 24)
check("GET /api/roles -> admin shows all 28",
      len(_by_key["admin"]["permission_keys"]) == 28)

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
check("gestor restored to 24 perms", len(_rrepo.get_role_permissions("gestor")) == 24)

# ── RBAC para plugins (plano "RBAC para Plugins") ──────────────────
import asyncio as _asyncio
import types as _types
from plugins.manifest import _parse_rbac as _parse_rbac
from plugins.context import plugin_permission as _plugin_permission
from plugins import events as _events
from server import authz as _authz
from fastapi import HTTPException as _HTTPException

# 1) Manifest: parse + validate the rbac: block (invalid keys dropped).
_rbac_parsed = _parse_rbac(
    {"group": "Lembretes", "permissions": [
        {"key": "view", "label": "Ver"},
        {"key": "delete", "label": "Excluir"},
        {"key": "Bad Key", "label": "x"},   # invalid → dropped
        {"key": "view", "label": "dup"},     # dup → dropped
    ]}, "lembretes")
check("manifest rbac -> group parsed", _rbac_parsed["group"] == "Lembretes")
check("manifest rbac -> invalid/dup keys dropped",
      [p["key"] for p in _rbac_parsed["permissions"]] == ["view", "delete"])
check("manifest rbac absent -> {}", _parse_rbac(None, "x") == {})

# 2) Repo: upsert plugin perms → catalog merge + keys + delete cascade.
_rrepo.upsert_plugin_permission("plugin.lembretes.view", "Ver lembretes",
                                "lembretes", "Lembretes")
_rrepo.upsert_plugin_permission("plugin.lembretes.delete", "Excluir lembretes",
                                "lembretes", "Lembretes")
_pkeys = _rrepo.plugin_permission_keys()
check("plugin_permission_keys -> includes plugin perms",
      {"plugin.lembretes.view", "plugin.lembretes.delete"} <= _pkeys)
_catalog = _rrepo.list_catalog()
_cat_view = next((c for c in _catalog if c["key"] == "plugin.lembretes.view"), None)
check("list_catalog -> core + plugin rows", _cat_view is not None
      and _cat_view["plugin_id"] == "lembretes" and _cat_view["group_label"] == "Lembretes")
check("list_catalog -> core perms have plugin_id None",
      any(c["key"] == "conversation.read" and c["plugin_id"] is None for c in _catalog))

# 3) /api/roles exposes plugin perms with metadata.
_roles_payload = client.get("/api/roles").json()["data"]
_api_view = next((p for p in _roles_payload["permissions"]
                  if p["key"] == "plugin.lembretes.view"), None)
check("GET /api/roles -> plugin perm in catalog with group_label",
      _api_view is not None and _api_view.get("group_label") == "Lembretes")

# 4) Create a role with a plugin perm passes; bogus key rejected silently.
r = client.post("/api/roles", json={"key": "lembrete_admin", "name": "Lembrete Admin",
    "permission_keys": ["plugin.lembretes.view", "plugin.lembretes.bogus"]})
check("POST /api/roles (plugin perm) -> 200", r.status_code == 200)
_lr_id = r.json()["data"]["role"]["id"]
_lr_keys = r.json()["data"]["role"]["permission_keys"]
check("create role -> valid plugin perm kept, bogus dropped",
      _lr_keys == ["plugin.lembretes.view"])
client.delete(f"/api/roles/{_lr_id}")

# 5) plugin_permission() dependency: infers id from path; default-allow legacy.
_pu = _urepo.create(email="pluginuser@test.com", name="PU",
                    password_hash=_hpa("supersecret"), role_keys=["atendente"])
_pu_id = _pu["id"]
_dep = _plugin_permission("delete").dependency
def _freq(user=None, path="/api/plugins/lembretes/items/1"):
    return _types.SimpleNamespace(state=_types.SimpleNamespace(user=user),
                                  url=_types.SimpleNamespace(path=path))
# legacy/open (no user) -> allowed (no raise)
_asyncio.run(_dep(_freq(user=None)))
check("plugin_permission -> legacy/open allowed", True)
# logged-in user WITHOUT the perm -> 403
_denied = False
try:
    _asyncio.run(_dep(_freq(user={"id": _pu_id})))
except _HTTPException as e:
    _denied = e.status_code == 403
check("plugin_permission -> user without perm -> 403", _denied)
# grant the perm to that user (custom) -> allowed
_urepo.set_custom_permissions(_pu_id, ["plugin.lembretes.delete"])
_asyncio.run(_dep(_freq(user={"id": _pu_id})))
check("plugin_permission -> user with perm -> allowed", True)
# non-plugin path -> cannot infer id -> allowed (no raise)
_asyncio.run(_dep(_freq(user={"id": _pu_id}, path="/api/contacts")))
check("plugin_permission -> non-plugin path allowed", True)

# 6) ABAC seam: filter.authz.decision can downgrade allow->deny.
def _abac_deny(ctx, value):
    return {**value, "allow": False}
_events.register_filter("testabac", "filter.authz.decision", _abac_deny)
_abac_req = _freq(user={"id": _pu_id})  # user HAS the perm now
_allowed_after_filter = _asyncio.run(_authz.acheck(_abac_req, "plugin.lembretes.delete"))
check("filter.authz.decision -> downgrades allow to deny", _allowed_after_filter is False)
_events._filters.pop("filter.authz.decision", None)  # drop just the test filter

# 7) Delete plugin perms removes rows (role/user grants cascade by FK).
_removed = _rrepo.delete_plugin_permissions("lembretes")
check("delete_plugin_permissions -> rows removed", _removed == 2)
check("delete_plugin_permissions -> keys gone",
      not _rrepo.plugin_permission_keys())
_urepo.delete(_pu_id)  # cleanup the test user

# ── Protocolos: Kanban Views (visualizações personalizadas) ──────────────
section("Protocolos Kanban Views")
import importlib.util as _ilu
from plugins.manifest import load_manifest as _load_manifest, _parse_yaml as _parse_yaml
from plugins.migrator import run_pending_migrations as _run_pending

_atd_dir = Path(PROJECT_ROOT) / "storages" / "plugins" / "protocolos"

# 1) Manifest declara a permissão nova manage_team_views (aparece no PermissionPicker).
_atd_yaml = _parse_yaml((_atd_dir / "plugin.yaml").read_text(encoding="utf-8"))
_atd_rbac = _parse_rbac(_atd_yaml.get("rbac"), "protocolos")
check("protocolos rbac -> manage_team_views declarada",
      any(p["key"] == "manage_team_views" for p in _atd_rbac.get("permissions", [])))

# 2) Aplica as migrations do plugin no DB de teste (cria plugin_protocolos_* incl. 006).
_atd_manifest = _load_manifest(_atd_dir)
_run_pending(_atd_manifest, _atd_dir)
_alogic_spec = _ilu.spec_from_file_location("atendimentos_logic_test", _atd_dir / "logic.py")
_alogic = _ilu.module_from_spec(_alogic_spec)
_alogic_spec.loader.exec_module(_alogic)

# 2b) Seed 010: as abas padrão Status/Atendente agora são VIEWS REAIS (equipe, sem owner) —
# editáveis/excluíveis como qualquer visualização criada pela interface. Roda antes do CRUD
# abaixo, então só os 2 seeds existem (positions 0 e 1).
_seeded = _alogic.list_kanban_views(user_id=None)
_by_gb = {v["group_by"]: v for v in _seeded
          if v.get("owner_user_id") is None and v.get("scope") == "team"}
check("seed 010 -> view Status existe", "status" in _by_gb and _by_gb["status"]["name"] == "Status")
check("seed 010 -> view Atendente existe",
      "atendente" in _by_gb and _by_gb["atendente"]["name"] == "Atendente")
check("seed 010 -> Status visível a todos (team legado)",
      _alogic._view_visible(_by_gb["status"], 12345, set()) is True)
check("seed 010 -> ordenadas primeiro por position",
      _by_gb["status"]["position"] == 0 and _by_gb["atendente"]["position"] == 1)
_su, _sue = _alogic.update_kanban_view(_by_gb["status"]["id"], name="Status renomeado")
check("seed 010 -> Status editável", _sue is None and _su and _su.get("name") == "Status renomeado")
_sd, _sde = _alogic.delete_kanban_view(_by_gb["atendente"]["id"])
check("seed 010 -> Atendente excluível",
      _sd and _sde is None and _alogic.get_kanban_view(_by_gb["atendente"]["id"]) is None)

# 3) CRUD + validação.
_v1, _e1 = _alogic.create_kanban_view(name="Por etapa", scope="personal", group_by="attr",
                                      group_attr_key="etapa", filters={"status": "aberto"},
                                      owner_user_id=101)
check("create view pessoal -> ok", _e1 is None and bool(_v1 and _v1.get("id")))
check("create view -> filters round-trip dict", _v1 and _v1.get("filters") == {"status": "aberto"})
_v2, _e2 = _alogic.create_kanban_view(name="Equipe vendas", scope="team", group_by="data",
                                      group_date_mode="mes", owner_user_id=101)
check("create view equipe -> ok", _e2 is None and bool(_v2 and _v2.get("id")))
_, _ev_attr = _alogic.create_kanban_view(name="x", group_by="attr", group_attr_key="", owner_user_id=1)
check("validação: attr sem key -> erro", _ev_attr is not None)
_, _ev_date = _alogic.create_kanban_view(name="y", group_by="data", group_date_mode="bad", owner_user_id=1)
check("validação: data mode inválido -> erro", _ev_date is not None)
_, _ev_name = _alogic.create_kanban_view(name="   ", owner_user_id=1)
check("validação: nome vazio -> erro", _ev_name is not None)

# 4) list_kanban_views: pessoal do user + TODAS as de equipe.
_ids101 = {v["id"] for v in _alogic.list_kanban_views(user_id=101)}
_ids999 = {v["id"] for v in _alogic.list_kanban_views(user_id=999)}
check("list user 101 -> vê pessoal + equipe", {_v1["id"], _v2["id"]} <= _ids101)
check("list user 999 -> vê equipe, NÃO vê pessoal de 101",
      _v2["id"] in _ids999 and _v1["id"] not in _ids999)

# 5) update + delete.
_vu, _eu = _alogic.update_kanban_view(_v1["id"], name="Por etapa v2")
check("update view -> nome alterado", _eu is None and _vu and _vu.get("name") == "Por etapa v2")
_okdel, _edel = _alogic.delete_kanban_view(_v1["id"])
check("delete view -> ok e some", _okdel and _edel is None and _alogic.get_kanban_view(_v1["id"]) is None)

# 6) set_conversa_attr: branches de erro (sem e2e de conversa — coberto manualmente).
_, _esa = _alogic.set_atendimento_attr(99999, "etapa", "Proposta")
check("set_atendimento_attr -> protocolo inexistente -> erro", _esa is not None)

# 7) attr_filters: caminho do filtro por atributo aceito sem quebrar (lista vazia/segura).
_lf = _alogic.list_protocolos(attr_filters={"etapa": "Proposta"}, limit=50)
check("list_protocolos(attr_filters) -> retorna lista", isinstance(_lf, list))

# 7b) Preferência POR-USUÁRIO (pessoal x equipe) por visualização. Usa _v2 (equipe) + user 101.
_p0 = _alogic.get_user_view_pref(_v2["id"], 101)
check("pref ausente -> default equipe",
      _p0 == {"use_personal": False, "personal_filters": {}})
_p1 = _alogic.upsert_user_view_pref(_v2["id"], 101, use_personal=True,
                                    personal_filters={"status": "fechado"})
check("upsert pref -> retorna pessoal",
      _p1 == {"use_personal": True, "personal_filters": {"status": "fechado"}})
_p1r = _alogic.get_user_view_pref(_v2["id"], 101)
check("pref persistida -> personal_filters round-trip",
      _p1r["use_personal"] is True and _p1r["personal_filters"] == {"status": "fechado"})
_p2 = _alogic.upsert_user_view_pref(_v2["id"], 101, use_personal=False)
check("upsert parcial -> flip use_personal mantém filters",
      _p2 == {"use_personal": False, "personal_filters": {"status": "fechado"}})
_p999 = _alogic.get_user_view_pref(_v2["id"], 999)
check("pref isolada por usuário", _p999 == {"use_personal": False, "personal_filters": {}})
_alogic.upsert_user_view_pref(_v2["id"], 101, use_personal=True)
_lv101 = {v["id"]: v.get("pref") for v in _alogic.list_kanban_views(user_id=101)}
_lv999 = {v["id"]: v.get("pref") for v in _alogic.list_kanban_views(user_id=999)}
check("list anexa pref do chamador 101",
      _lv101.get(_v2["id"], {}).get("use_personal") is True)
check("list anexa pref default p/ 999",
      _lv999.get(_v2["id"]) == {"use_personal": False, "personal_filters": {}})
check("get_user_view_pref(uid=None) -> default equipe",
      _alogic.get_user_view_pref(_v2["id"], None) == {"use_personal": False, "personal_filters": {}})
_vp, _evp = _alogic.create_kanban_view(name="Equipe tmp", scope="team",
                                       group_by="status", owner_user_id=101)
_alogic.upsert_user_view_pref(_vp["id"], 101, use_personal=True, personal_filters={"q": "x"})
_alogic.delete_kanban_view(_vp["id"])
check("delete_kanban_view -> prefs limpas",
      _alogic.get_user_view_pref(_vp["id"], 101) == {"use_personal": False, "personal_filters": {}})

# 7c) available_filters: quais TIPOS de filtro a aba expõe (metadado da view, decidido no editor).
check("view sem available_filters -> None (todos)", _v2.get("available_filters") is None)
_va, _eva = _alogic.create_kanban_view(name="Só status+curso", scope="team", group_by="status",
                                       available_filters=["status", "attr:curso"], owner_user_id=101)
check("create available_filters -> round-trip lista",
      _eva is None and _va.get("available_filters") == ["status", "attr:curso"])
_vau, _ = _alogic.update_kanban_view(_va["id"], name="Só status+curso v2")
check("update sem available_filters -> mantém lista (sentinela)",
      _vau.get("available_filters") == ["status", "attr:curso"])
_vau2, _ = _alogic.update_kanban_view(_va["id"], available_filters=["periodo"])
check("update com available_filters -> troca lista", _vau2.get("available_filters") == ["periodo"])
_vau3, _ = _alogic.update_kanban_view(_va["id"], available_filters=None)
check("update available_filters=None -> None (todos)", _vau3.get("available_filters") is None)
_alogic.delete_kanban_view(_va["id"])

# 7d) ACL de visibilidade "Quem pode ver": grupos (roles) + usuários (incluir/excluir).
_vacl, _eacl = _alogic.create_kanban_view(
    name="Só atendentes", group_by="status",
    visibility_roles=["atendente"], visibility_users_exclude=[500], owner_user_id=101)
check("create ACL -> scope derivado team", _eacl is None and _vacl.get("scope") == "team")
check("create ACL -> roles round-trip", _vacl.get("visibility_roles") == ["atendente"])
check("create ACL -> exclude round-trip", _vacl.get("visibility_users_exclude") == [500])
check("ACL: criador sempre vê", _alogic._view_visible(_vacl, 101, set()) is True)
check("ACL: atendente (do grupo) vê", _alogic._view_visible(_vacl, 300, {"atendente"}) is True)
check("ACL: atendente EXCLUÍDO não vê", _alogic._view_visible(_vacl, 500, {"atendente"}) is False)
check("ACL: gestor (fora do grupo) não vê", _alogic._view_visible(_vacl, 300, {"gestor"}) is False)
check("ACL: admin vê tudo", _alogic._view_visible(_vacl, 900, {"admin"}) is True)
_vinc, _ = _alogic.create_kanban_view(name="Incluir fulano", group_by="status",
                                      visibility_users_include=[700], owner_user_id=101)
check("ACL: usuário incluído vê (sem papel)", _alogic._view_visible(_vinc, 700, set()) is True)
check("ACL: não incluído/sem grupo não vê", _alogic._view_visible(_vinc, 701, set()) is False)
_vp2, _ = _alogic.create_kanban_view(name="Priv", group_by="status", owner_user_id=101)
check("sem ACL -> scope personal", _vp2.get("scope") == "personal")
check("personal: outro não vê", _alogic._view_visible(_vp2, 800, {"atendente"}) is False)
_ids_at = {v["id"] for v in _alogic.list_kanban_views(user_id=300, role_keys={"atendente"})}
check("list(atendente) inclui view de atendentes", _vacl["id"] in _ids_at)
_ids_ex = {v["id"] for v in _alogic.list_kanban_views(user_id=500, role_keys={"atendente"})}
check("list(atendente excluído) não inclui", _vacl["id"] not in _ids_ex)
_alogic.delete_kanban_view(_vacl["id"])
_alogic.delete_kanban_view(_vinc["id"])
_alogic.delete_kanban_view(_vp2["id"])

# 8) Gate de EQUIPE (acheck) — o que a rota usa p/ criar/editar visualização de equipe.
_rrepo.upsert_plugin_permission("plugin.protocolos.manage_team_views",
                                "Criar/editar visualizações de EQUIPE no Kanban",
                                "protocolos", "Protocolos")
_tvu = _urepo.create(email="teamviews@test.com", name="TV",
                     password_hash=_hpa("supersecret"), role_keys=["atendente"])
_tvu_id = _tvu["id"]
_tv_req = _types.SimpleNamespace(
    state=_types.SimpleNamespace(user={"id": _tvu_id}),
    url=_types.SimpleNamespace(path="/api/plugins/protocolos/kanban-views"))
check("manage_team_views -> user SEM perm negado",
      _asyncio.run(_authz.acheck(_tv_req, "plugin.protocolos.manage_team_views")) is False)
_urepo.set_custom_permissions(_tvu_id, ["plugin.protocolos.manage_team_views"])
check("manage_team_views -> user COM perm permitido",
      _asyncio.run(_authz.acheck(_tv_req, "plugin.protocolos.manage_team_views")) is True)
_urepo.delete(_tvu_id)
_rrepo.delete_plugin_permissions("protocolos")

# 9) Tipos de campo NOVOS: número, data, regex (text/textarea/number) e "caixa de seleção"
# configurável única/múltipla. Testado direto na logic (scope protocolo não sincroniza core).
_alogic.set_field_defs("protocolo", [
    {"key": "idade", "label": "Idade", "type": "number"},
    {"key": "nascimento", "label": "Nascimento", "type": "date"},
    {"key": "cpf", "label": "CPF", "type": "text", "regex_pattern": r"^\d{11}$", "regex_cue": "11 dígitos"},
    {"key": "cursos", "label": "Cursos", "type": "checkboxes", "options": ["A", "B", "C"], "multiple": True, "required": True},
    {"key": "turno", "label": "Turno", "type": "checkboxes", "options": ["Manhã", "Tarde"], "multiple": False},
])
_pdefs = {d["key"]: d for d in _alogic.get_field_defs("protocolo")}
check("field types -> número/data/checkboxes persistidos",
      _pdefs.get("idade", {}).get("type") == "number"
      and _pdefs.get("nascimento", {}).get("type") == "date"
      and _pdefs.get("cursos", {}).get("type") == "checkboxes"
      and _pdefs["cursos"].get("multiple") is True)
check("field types -> regex_pattern/regex_cue persistidos",
      _pdefs.get("cpf", {}).get("regex_pattern") == r"^\d{11}$"
      and _pdefs["cpf"].get("regex_cue") == "11 dígitos")
_, _en = _alogic.normalize_values("protocolo", {"idade": "abc", "cursos": ["A"]})
check("número inválido -> erro", _en is not None and "número" in _en.lower())
_cnv, _env = _alogic.normalize_values("protocolo", {"idade": "3,5", "cursos": ["A"]})
check("número válido (vírgula) -> ok", _env is None)
_, _ed = _alogic.normalize_values("protocolo", {"nascimento": "2020/01/01", "cursos": ["A"]})
check("data inválida -> erro", _ed is not None)
_cdv, _edv = _alogic.normalize_values("protocolo", {"nascimento": "2020-01-01", "cursos": ["A"]})
check("data válida AAAA-MM-DD -> ok", _edv is None)
_, _er = _alogic.normalize_values("protocolo", {"cpf": "123", "cursos": ["A"]})
check("regex não casa -> erro com a dica", _er is not None and "11 dígitos" in _er)
_, _erok = _alogic.normalize_values("protocolo", {"cpf": "12345678901", "cursos": ["A"]})
check("regex casa -> ok", _erok is None)
_cc, _ecc = _alogic.normalize_values("protocolo", {"cursos": ["A", "C"]})
check("checkboxes múltiplo -> lista de opções", _ecc is None and _cc.get("cursos") == ["A", "C"])
_, _eci = _alogic.normalize_values("protocolo", {"cursos": ["Z"]})
check("checkboxes opção inválida -> erro", _eci is not None)
_cs, _ecs = _alogic.normalize_values("protocolo", {"cursos": ["A"], "turno": ["Manhã", "Tarde"]})
check("checkboxes single (multiple=False) -> corta p/ 1", _ecs is None and _cs.get("turno") == ["Manhã"])
_, _ereq = _alogic.normalize_values("protocolo", {"cursos": []})
check("checkboxes obrigatório vazio -> erro", _ereq is not None and "Cursos" in _ereq)
check("_missing_required revalida tipo+required no fechamento",
      _alogic._missing_required("protocolo", {"cursos": [], "obs": ""}) is not None)
check("_missing_required ok quando preenchido",
      _alogic._missing_required("protocolo", {"cursos": ["A"], "obs": ""}) is None)
_alogic.set_field_defs("protocolo", [])  # limpa p/ não vazar estado a testes seguintes

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

# ── Escopo de caixa no ENVIO (Bug 2, plano inboxes/canais §4.7) ──
# att2 (atendente, sem read_all) não é membro de nenhuma inbox ⇒ não vê nem envia.
from db.repositories import inbox_member_repo as _imrepo
with _get_engine().connect() as _conn:
    _scope_phone = _conn.execute(
        _sa_select(_contacts_t.c.phone).where(_contacts_t.c.id == _cid)).scalar()
_h_att = {"Authorization": f"Bearer {_attok}"}
r = client.post(f"/api/contacts/{_scope_phone}/send",
                json={"message": "oi", "conversation_id": _conv2["id"]}, headers=_h_att)
check("send (não-membro, sem read_all) -> 403", r.status_code == 403)
r = client.get(f"/api/conversations/{_conv2['id']}", headers=_h_att)
check("get conversa (não-membro) -> 404 (escopo leitura)", r.status_code == 404)
# Vira membro da inbox 1 ⇒ passa a enviar.
_imrepo.set_inboxes_for_user(_at["id"], [1])
r = client.post(f"/api/contacts/{_scope_phone}/send",
                json={"message": "oi agora vai", "conversation_id": _conv2["id"]}, headers=_h_att)
check("send (membro da inbox) -> 200", r.status_code == 200)
# Sem read_all e membro só da inbox 1: enviar para conversa de OUTRA inbox bloqueia.
from db.repositories import inbox_repo as _ibx_repo
if _chrepo.get("scope_ch") is None:
    _chrepo.create(id="scope_ch", provider="whatsapp_cloud", display_name="Scope CH")
_other_ib = _ibx_repo.get_or_create_for_channel("scope_ch", name="Scope CH")
_other_ci = _ci_repo.get_or_create(inbox_id=_other_ib["id"], contact_id=_cid, source_id=f"scope:{_cid}")
_other_conv = _conv_repo.create(inbox_id=_other_ib["id"], contact_id=_cid, contact_inbox_id=_other_ci["id"])
r = client.post(f"/api/contacts/{_scope_phone}/send",
                json={"message": "nope", "conversation_id": _other_conv["id"]}, headers=_h_att)
check("send (membro de outra inbox) -> 403", r.status_code == 403)

# ── Escopo na LEITURA por-contato (view legada não pode vazar) ──
# Contato cujo ÚNICO thread está numa inbox que att2 NÃO acessa (scope_ch).
from db.repositories import contact_repo as _crepo_scope
_hidden_c = _crepo_scope.get_or_create("5599911112222", default_ai_enabled=False)
_hidden_ci = _ci_repo.get_or_create(
    inbox_id=_other_ib["id"], contact_id=_hidden_c["id"], source_id="scope:hidden")
_conv_repo.create(inbox_id=_other_ib["id"], contact_id=_hidden_c["id"],
                  contact_inbox_id=_hidden_ci["id"])
_imrepo.set_inboxes_for_user(_at["id"], [1])  # membro só da inbox 1
r = client.get("/api/contacts", headers=_h_att)
_clist = r.json()["data"]
_cphones = {c.get("phone") for c in (_clist if isinstance(_clist, list) else _clist.get("contacts", []))}
check("GET /contacts (escopado) -> exclui contato de inbox não-membro",
      "5599911112222" not in _cphones)
r = client.get("/api/contacts/5599911112222", headers=_h_att)
check("GET /contacts/{phone} (inbox não-membro) -> 404", r.status_code == 404)
# read_all fura o escopo: admin envia em qualquer caixa mesmo sem membership.
_imrepo.set_inboxes_for_user(_at["id"], [])  # limpa para não afetar testes seguintes

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

# reopen automático também emite conversation_status_changed (espelha o status no
# painel ao vivo: a sidebar migra a conversa Resolvidas->Abertas e o painel
# Atendimentos refetcha, sem refresh manual).
import plugins.context as _pctx
_orig_bcast = _pctx.broadcast
_bcast_events = []
_pctx.broadcast = lambda ev, data: _bcast_events.append((ev, data))
try:
    _conv_repo.set_status(_live_conv["id"], "closed")
    _cm.add_message("user", "voltei de novo")
finally:
    _pctx.broadcast = _orig_bcast
_reopen_evts = [d for ev, d in _bcast_events
                if ev == "conversation_status_changed"
                and d.get("conversation_id") == _live_conv["id"]]
check("reopen automático emite conversation_status_changed", len(_reopen_evts) == 1)
check("reopen broadcast status=open",
      bool(_reopen_evts) and _reopen_evts[0]["status"] == "open")

# reopen pelo atendente: a resposta (assistant) também reabre uma conversa closed
_conv_repo.set_status(_live_conv["id"], "closed")
_cm.add_message("assistant", "olá, retomando o atendimento")
check("resposta do atendente reabre conversa closed",
      _conv_repo.get(_live_conv["id"])["status"] == "open")

# roles painel-only NÃO reabrem (nota privada não reativa o atendimento)
_conv_repo.set_status(_live_conv["id"], "closed")
_cm.add_message("private_note", "anotação interna")
check("private_note NÃO reabre conversa closed",
      _conv_repo.get(_live_conv["id"])["status"] == "closed")
_conv_repo.set_status(_live_conv["id"], "open")  # restaura p/ os testes seguintes

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
#  15h-ter. Planos 16-20 (apagar conversa/contato, IA por conversa, atrib. sistema)
# ═══════════════════════════════════════════════════════════════════
section("Planos 16-20 (apagar, IA por conversa, atributos de sistema)")

# ── P17: desligar IA entrega a conversa a quem desligou + zera agente; ligar
#    rebinda o agente default e limpa o responsável humano (IA assume) ──
_p17 = _conv_repo.create(inbox_id=1, contact_id=_cid, contact_inbox_id=_ci["id"])
_conv_repo.set_assignee(_p17["id"], _mgr["id"])  # responsável humano + agente default
# Operador autenticado desliga a IA -> assume a conversa.
r = client.post(f"/api/conversations/{_p17['id']}/ai", json={"active": False},
                headers={"Authorization": f"Bearer {_mgrtok}"})
_d = r.json()["data"]["conversation"]
check("P17 ai off -> ai_active=0", _d["ai_active"] == 0)
check("P17 ai off -> active_agent_key limpo", not _d["active_agent_key"])
check("P17 ai off -> assignee = quem desligou", _d["assignee_user_id"] == _mgr["id"])
r = client.post(f"/api/conversations/{_p17['id']}/ai", json={"active": True},
                headers={"Authorization": f"Bearer {_mgrtok}"})
_d2 = r.json()["data"]["conversation"]
check("P17 ai on -> ai_active=1", _d2["ai_active"] == 1)
check("P17 ai on -> religa agente default", _d2["active_agent_key"] == "default")
check("P17 ai on -> limpa responsável humano (IA assume)", _d2["assignee_user_id"] is None)
# Legacy/open (sem identidade de operador) cai em "Não atribuídas".
_p17b = _conv_repo.create(inbox_id=1, contact_id=_cid, contact_inbox_id=_ci["id"])
_conv_repo.set_assignee(_p17b["id"], _mgr["id"])
r = client.post(f"/api/conversations/{_p17b['id']}/ai", json={"active": False})
_db17 = r.json()["data"]["conversation"]
check("P17 ai off (sem auth) -> Não atribuídas", _db17["assignee_user_id"] is None)

# ── P16: apagar conversa (mantém contato + outras conversas; some com as msgs) ──
_cmdel = _CM("5500077766655")
_cmdel.add_message("user", "primeira conversa")
with _get_engine().connect() as _conn:
    _convA_id = _conn.execute(
        _sa_select(_msgs_t.c.conversation_id)
        .where(_msgs_t.c.contact_id == _cmdel.id)
        .where(_msgs_t.c.role == "user")
        .order_by(_msgs_t.c.id.desc()).limit(1)).scalar()
_ciB = _ci_repo.get_or_create(inbox_id=1, contact_id=_cmdel.id,
                              source_id=f"{_cmdel.id}@s.whatsapp.net")
_convB = _conv_repo.create(inbox_id=1, contact_id=_cmdel.id, contact_inbox_id=_ciB["id"])
r = client.delete(f"/api/conversations/{_convA_id}")
check("P16 DELETE conversa -> 200", r.status_code == 200)
check("P16 DELETE -> conversa removida", _conv_repo.get(_convA_id) is None)
check("P16 DELETE -> outra conversa do contato preservada",
      _conv_repo.get(_convB["id"]) is not None)
with _get_engine().connect() as _conn:
    _msgs_left = _conn.execute(
        _sa_select(_msgs_t.c.id).where(_msgs_t.c.conversation_id == _convA_id)).fetchall()
    _contact_left = _conn.execute(
        _sa_select(_contacts_t.c.id).where(_contacts_t.c.id == _cmdel.id)).scalar()
check("P16 DELETE -> mensagens da conversa apagadas", len(_msgs_left) == 0)
check("P16 DELETE -> contato preservado", _contact_left == _cmdel.id)
r = client.delete("/api/conversations/999999")
check("P16 DELETE conversa inexistente -> 404", r.status_code == 404)

# ── P19: atributos padrão semeados (CPF/Email/Profissão/Empresa/Endereço) ──
# Agora são seeds EDITÁVEIS e DELETÁVEIS (is_system=0): vêm por padrão mas o
# usuário pode renomear/apagar. A trava is_system=1 ainda existe no código e é
# coberta logo abaixo com um atributo sintético.
from db.system_attributes import seed_system_attributes as _seed_sys
from db.repositories import custom_attribute_repo as _cad_repo
_seed_sys()
_dmap = _cad_repo.get_definitions_map("contact")
check("P19 seed -> CPF criado is_system=0 (editável/deletável)",
      _dmap.get("cpf") is not None and _dmap["cpf"].get("is_system") == 0)
check("P19 seed -> defaults Email/Profissão/Empresa/Endereço presentes",
      {"email", "profession", "company", "address"} <= set(_dmap.keys()))
_cpf = _dmap.get("cpf")
_seed_sys()  # 2ª chamada deve ser no-op
_cpf2 = _cad_repo.get_definitions_map("contact").get("cpf")
check("P19 seed idempotente (mesmo id)", bool(_cpf2) and _cpf2["id"] == _cpf["id"])
# Default editável: renomear display_name -> 200; apagar -> 200 (não é protegido).
r = client.put(f"/api/custom-attributes/{_cpf['id']}", json={"display_name": "CPF do cliente"})
check("P19 PUT editar display_name de default -> 200", r.status_code == 200)
r = client.delete(f"/api/custom-attributes/{_cpf['id']}")
check("P19 DELETE default (CPF) -> 200 (deletável)", r.status_code == 200)

# Trava is_system=1 ainda funciona: cria um atributo de sistema sintético e
# confirma que DELETE e rename de key/scope são bloqueados (400).
_locked = _cad_repo.create_definition(
    attribute_key="sys_locked", display_name="Travado", applies_to="contact",
    is_system=1)
r = client.delete(f"/api/custom-attributes/{_locked['id']}")
check("P19 DELETE atributo de sistema (is_system=1) -> 400", r.status_code == 400)
r = client.put(f"/api/custom-attributes/{_locked['id']}", json={"attribute_key": "outro"})
check("P19 PUT renomear atributo de sistema (is_system=1) -> 400", r.status_code == 400)
r = client.put(f"/api/custom-attributes/{_locked['id']}", json={"display_name": "Travado 2"})
check("P19 PUT editar display_name de atributo de sistema -> 200", r.status_code == 200)

# ── P22: motor único (config-in-DB) — sem espelhamento, invariante do default ──
from db.repositories import agent_repo as _agent_repo
_def_agent = _agent_repo.get("default")
check("P22 agente 'default' semeado", _def_agent is not None)
check("P22 default tem modelo não-vazio",
      bool(dict((_def_agent or {}).get("model_config") or {}).get("model")))
check("P22 default usa todas as tools core (tool_names None)",
      (_def_agent or {}).get("tool_names") is None)
check("P22 default tem prompt inline (semeado)",
      bool((_def_agent or {}).get("prompt")))
# Editar agente default já NÃO espelha em config (config não tem mais essas chaves).
# O prompt agora é inline no próprio agente (sem tabela de prompts reutilizáveis).
client.put("/api/ai/agents/default", json={
    "display_name": "Agente padrão",
    "prompt": "Prompt inline do agente default",
    "model_config": {"model": "anthropic/claude-sonnet-4-6"}})
_saved = _agent_repo.get("default") or {}
check("P22 default model salvo no DB",
      dict(_saved.get("model_config") or {}).get("model") == "anthropic/claude-sonnet-4-6")
check("P22 default prompt inline salvo no DB",
      _saved.get("prompt") == "Prompt inline do agente default")
check("P22 config NÃO ganha chave 'model'",
      "model" not in client.get("/api/config").json()["data"])
# Patch dedicado de prompt (usado pelo wizard): grava o prompt e preserva os demais campos.
client.put("/api/ai/agents/default/prompt", json={"prompt": "Prompt via wizard"})
_after = _agent_repo.get("default") or {}
check("P22 PUT /agents/default/prompt grava o prompt inline",
      _after.get("prompt") == "Prompt via wizard")
check("P22 PUT /agents/default/prompt preserva o modelo",
      dict(_after.get("model_config") or {}).get("model") == "anthropic/claude-sonnet-4-6")
check("P22 config NÃO ganha chave 'system_prompt'",
      "system_prompt" not in client.get("/api/config").json()["data"])
# Invariante do agente default (Fase 5): não pode ser desativado nem excluído.
r = client.put("/api/ai/agents/default", json={
    "display_name": "Agente padrão", "prompt_key": "default",
    "model_config": {"model": "anthropic/claude-sonnet-4-6"}, "enabled": False})
check("P22 desativar agente default -> 400", r.status_code == 400)
check("P22 default segue habilitado", bool((_agent_repo.get("default") or {}).get("enabled")))
r = client.delete("/api/ai/agents/default")
check("P22 excluir agente default -> 400", r.status_code == 400)
# Um agente não-default continua podendo ser criado e excluído.
client.put("/api/ai/agents/p22_tmp", json={
    "display_name": "Temp", "prompt_key": "default",
    "model_config": {"model": "openai/gpt-4o-mini"}, "enabled": True})
r = client.delete("/api/ai/agents/p22_tmp")
check("P22 excluir agente não-default -> 200", r.status_code == 200)

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
      any("iniciado" in c for c in _notices(_snconv["id"])))

# (a) status close/open -> grupo status, com autor nomeado
_n = len(_notices(_snconv["id"]))
client.post(f"/api/conversations/{_snconv['id']}/status", json={"status": "closed"}, headers=_snhdr)
client.post(f"/api/conversations/{_snconv['id']}/status", json={"status": "open"}, headers=_snhdr)
_after = _notices(_snconv["id"])
check("status close/open -> 2 avisos", len(_after) - _n == 2)
check("status_closed -> 'Mgr resolveu'", any("Mgr resolveu o atendimento" in c for c in _after[_n:]))
check("status_open -> 'Mgr reabriu'", any("Mgr reabriu o atendimento" in c for c in _after[_n:]))

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

# (d) ai off (conversa) -> aviso do grupo ai + card "assumiu" (P17: quem desliga
#     a IA assume a conversa); ai on -> grupo ai
_n = len(_notices(_snconv["id"]))
client.post(f"/api/conversations/{_snconv['id']}/ai", json={"active": False}, headers=_snhdr)
client.post(f"/api/conversations/{_snconv['id']}/ai", json={"active": True}, headers=_snhdr)
_after = _notices(_snconv["id"])
check("ai off/on -> 3 avisos (ai off + assumiu + ai on)", len(_after) - _n == 3)
check("ai off -> card 'assumiu o atendimento'",
      any("assumiu o atendimento" in c for c in _after[_n:]))

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
check("auto-reopen -> aviso 'reaberto automaticamente'",
      any("reaberto automaticamente" in c for c in _notices(_snconv["id"])[_n:]))

# (j2) auto-reabertura pelo atendente: resposta (assistant) numa conversa closed
_conv_repo.set_status(_snconv["id"], "closed")
_n = len(_notices(_snconv["id"]))
_sncm.add_message("assistant", "retomando")
check("auto-reopen por atendente -> aviso 'resposta enviada'",
      any("resposta enviada" in c for c in _notices(_snconv["id"])[_n:]))

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

# ═══════════════════════════════════════════════════════════════════
#  15j-bis. AI Engine — dedicated prompt version trail (git-like)
# ═══════════════════════════════════════════════════════════════════
section("AI Engine prompt trail")

_pk = "promptver"
r = client.put(f"/api/ai/agents/{_pk}", json={
    "display_name": "Versionado", "prompt": "Linha 1\nLinha 2",
    "model_config": {"model": "x"}, "enabled": True})
check("create agent c/ prompt -> 200", r.status_code == 200)


def _ptrail():
    return client.get(f"/api/ai/agents/{_pk}/prompt/history").json()["data"]


_t0 = _ptrail()
check("prompt trail tem v1 inicial", len(_t0) >= 1)
_base = len(_t0)

# Muda só o display_name → trilha do prompt NÃO cresce (dedup do record)
client.put(f"/api/ai/agents/{_pk}", json={
    "display_name": "Versionado 2", "prompt": "Linha 1\nLinha 2",
    "model_config": {"model": "x"}, "enabled": True})
check("save só display_name -> trilha do prompt não cresce", len(_ptrail()) == _base)

# Save idêntico → dedup do agente (não bumpa versão) + trilha não cresce
_vbefore = client.get(f"/api/ai/agents/{_pk}").json()["data"]["version"]
client.put(f"/api/ai/agents/{_pk}", json={
    "display_name": "Versionado 2", "prompt": "Linha 1\nLinha 2",
    "model_config": {"model": "x"}, "enabled": True})
check("save idêntico -> agente não bumpa versão",
      client.get(f"/api/ai/agents/{_pk}").json()["data"]["version"] == _vbefore)
check("save idêntico -> trilha do prompt não cresce", len(_ptrail()) == _base)

# Muda o prompt → cria versão na trilha, com change_note
r = client.put(f"/api/ai/agents/{_pk}", json={
    "display_name": "Versionado 2", "prompt": "Linha 1\nLinha 2 alterada\nLinha 3",
    "model_config": {"model": "x"}, "enabled": True, "change_note": "ajuste de tom"})
check("save muda prompt -> 200", r.status_code == 200)
_t1 = _ptrail()
check("prompt trail cresceu", len(_t1) == _base + 1)
check("trilha newest-first", _t1[0]["version"] > _t1[1]["version"])
check("change_note gravado e exposto", _t1[0]["note"] == "ajuste de tom")
check("history enriquecido (added/removed)",
      "added_lines" in _t1[0] and "removed_lines" in _t1[0])

_newv = _t1[0]["version"]
_oldv = _t1[-1]["version"]
r = client.get(f"/api/ai/agents/{_pk}/prompt/history/{_newv}")
check("GET prompt version -> 200 c/ prompt completo",
      r.status_code == 200 and "Linha 3" in r.json()["data"]["prompt"])
r = client.get(f"/api/ai/agents/{_pk}/prompt/history/9999")
check("GET prompt version inexistente -> 404", r.status_code == 404)

r = client.get(f"/api/ai/agents/{_pk}/prompt/diff", params={"from": _oldv, "to": _newv})
check("GET prompt diff -> 200", r.status_code == 200)
_d = r.json()["data"]
check("diff unified_diff não vazio", bool(_d["unified_diff"]))
check("diff conta added_lines", _d["added_lines"] >= 1)
check("diff tem lines estruturadas", isinstance(_d["lines"], list) and len(_d["lines"]) > 0)
r = client.get(f"/api/ai/agents/{_pk}/prompt/diff", params={"from": _oldv, "to": 9999})
check("diff versão inexistente -> 404", r.status_code == 404)

r = client.post(f"/api/ai/agents/{_pk}/prompt/restore/{_oldv}")
check("POST prompt restore -> 200", r.status_code == 200)
check("restore restaura o prompt antigo",
      client.get(f"/api/ai/agents/{_pk}").json()["data"]["prompt"] == "Linha 1\nLinha 2")
check("restore cria nova versão na trilha", len(_ptrail()) == _base + 2)

r = client.post(f"/api/ai/agents/{_pk}/prompt/restore/9999")
check("restore versão inexistente -> 404", r.status_code == 404)

# version_mode="amend" → sobrescreve a última versão da trilha (não cresce)
_before_amend = _ptrail()
_topv = _before_amend[0]["version"]
r = client.put(f"/api/ai/agents/{_pk}", json={
    "display_name": "Versionado 2", "prompt": "Prompt sobrescrito",
    "model_config": {"model": "x"}, "enabled": True,
    "change_note": "amend nota", "version_mode": "amend"})
check("amend -> 200", r.status_code == 200)
_after_amend = _ptrail()
check("amend não cria nova versão na trilha", len(_after_amend) == len(_before_amend))
check("amend mantém o número da versão", _after_amend[0]["version"] == _topv)
check("amend sobrescreve o prompt vivo",
      client.get(f"/api/ai/agents/{_pk}").json()["data"]["prompt"] == "Prompt sobrescrito")
r = client.get(f"/api/ai/agents/{_pk}/prompt/history/{_topv}")
check("amend sobrescreve o texto da versão",
      r.json()["data"]["prompt"] == "Prompt sobrescrito")
check("amend grava a nova nota", r.json()["data"]["note"] == "amend nota")

# version_mode="new" (default) → volta a criar nova versão
_before_new = _ptrail()
r = client.put(f"/api/ai/agents/{_pk}", json={
    "display_name": "Versionado 2", "prompt": "Mais uma alteração",
    "model_config": {"model": "x"}, "enabled": True, "version_mode": "new"})
check("version_mode=new cria nova versão", len(_ptrail()) == len(_before_new) + 1)

# version_mode inválido → 400
r = client.put(f"/api/ai/agents/{_pk}", json={
    "display_name": "Versionado 2", "prompt": "x",
    "model_config": {"model": "x"}, "enabled": True, "version_mode": "zzz"})
check("version_mode inválido -> ok=False", r.json()["ok"] is False)

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

# Presence event (live generic GOWA route — v8 ``event``/``payload`` envelope).
r = client.post("/api/webhook/gowa/default", json={
    "event": "chat_presence",
    "payload": {"from": "5511999990001@s.whatsapp.net", "state": "composing"},
})
check("POST /webhook (presence) -> 200", r.status_code == 200)

# is_from_me echo (should be ignored / saved as echo, not replied)
r = client.post("/api/webhook/gowa/default", json={
    "event": "message",
    "payload": {
        "body": "echo test",
        "from": "5511999990001@s.whatsapp.net",
        "id": "echo_001",
        "is_from_me": True,
    },
})
check("POST /webhook (echo) -> 200", r.status_code == 200)

# message.ack event (read receipt; GOWA ``receipt_type``/``ids`` shape)
r = client.post("/api/webhook/gowa/default", json={
    "event": "message.ack",
    "payload": {"ids": ["msg_001"], "from": "5511999990002@s.whatsapp.net",
                "receipt_type": "read"},
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

# ── Filtro de tipos de JID (canal GOWA) ──────────────────────────────
section("Webhook — filtro de tipos de JID (GOWA)")

from channels import jid as _jid
from server.routes.webhook import (
    _read_gowa_allowed_jid_types as _read_allowed,
    reset_allowed_jid_cache as _reset_jid_cache,
)

# classify_jid: o sufixo do JID (depois do @) define o tipo, não o número.
check("classify_jid person", _jid.classify_jid("556490000001@s.whatsapp.net") == _jid.PERSON)
check("classify_jid person_lid", _jid.classify_jid("8312345678@lid") == _jid.PERSON_LID)
check("classify_jid group", _jid.classify_jid("120363423773591388@g.us") == _jid.GROUP)
check("classify_jid newsletter", _jid.classify_jid("120363167775174375@newsletter") == _jid.NEWSLETTER)
check("classify_jid broadcast", _jid.classify_jid("status@broadcast") == _jid.BROADCAST)
check("classify_jid bot", _jid.classify_jid("867051314767696@bot") == _jid.BOT)
check("classify_jid unknown (sem @)", _jid.classify_jid("12345") == _jid.UNKNOWN)
check("classify_jid unknown (sufixo novo)", _jid.classify_jid("x@futuro") == _jid.UNKNOWN)

# normalize: mantém só os conhecidos, na ordem canônica; cai no default se inválido.
check("normalize mantém conhecidos",
      _read_allowed and _jid.normalize_allowed_types(["bot", "person", "lixo"]) == ["person", "bot"])
check("normalize vazio -> default",
      _jid.normalize_allowed_types([]) == _jid.DEFAULT_ALLOWED_JID_TYPES)
check("normalize não-lista -> default",
      _jid.normalize_allowed_types("person") == _jid.DEFAULT_ALLOWED_JID_TYPES)

# is_allowed: tipos desconhecidos nunca são bloqueados (comportamento legado).
check("is_allowed unknown sempre passa",
      _jid.is_allowed(_jid.UNKNOWN, _jid.DEFAULT_ALLOWED_JID_TYPES) is True)
check("is_allowed newsletter bloqueado por default",
      _jid.is_allowed(_jid.NEWSLETTER, _jid.DEFAULT_ALLOWED_JID_TYPES) is False)

# Default do canal: newsletter/broadcast/bot são descartados (corrige o bug do
# "contato fantasma" — antes tudo que não era @g.us caía no ramo "pessoa").
_reset_jid_cache()
for _suffix, _label in (("newsletter", "canal"), ("broadcast", "status"), ("bot", "bot")):
    _jidstr = f"120363000000000001@{_suffix}" if _suffix != "broadcast" else "status@broadcast"
    # Live generic GOWA route: the JID-type discard runs inside ``ingest_event``
    # BEFORE any contact is materialized (parity with the retired legacy handler),
    # so the route still answers 200 — the discard is asserted by "no phantom
    # contact" below (and the characterization golden ``jid_*_discarded``).
    r = client.post("/api/webhook/gowa/default", json={
        "event": "message",
        "payload": {
            "body": f"post de {_label}", "from": _jidstr,
            "chat_id": _jidstr, "id": f"jidfilter_{_suffix}_001", "is_from_me": False,
        },
    })
    check(f"POST /webhook ({_label}) -> ignorado por tipo (200)",
          r.status_code == 200)

# E nenhum contato fantasma foi materializado para esses JIDs.
_clist = client.get("/api/contacts").json()["data"]
_cphones = {c.get("phone", "") for c in (_clist if isinstance(_clist, list) else _clist.get("contacts", []))}
check("filtro JID não cria contato fantasma",
      not any("newsletter" in p or "broadcast" in p or "@bot" in p for p in _cphones))

# Habilitar 'newsletter' no canal default passa a permitir o tipo (persistência +
# leitura + invalidação de cache na edição de config).
r = client.put("/api/channels/default", json={
    "config": {"allowed_jid_types": ["person", "person_lid", "group", "newsletter"]},
})
check("PUT /channels/default config allowed_jid_types -> 200", r.status_code == 200)
check("reader reflete config após edição (cache invalidado)",
      "newsletter" in _read_allowed() and "broadcast" not in _read_allowed())
# Restaura o default para não afetar os demais testes.
client.put("/api/channels/default", json={
    "config": {"allowed_jid_types": list(_jid.DEFAULT_ALLOWED_JID_TYPES)},
})
_reset_jid_cache()

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

# plano 13: this suite's GOWA coverage rides on the UNCONDITIONAL 'default' gowa
# channel that create_app materializes with the MOCK client — NOT on the gowa
# PLUGIN, which must never load here (bootstrap_gowa_upgrade is WHATSBOT_TEST-
# guarded and a freshly-discovered gowa row defaults to disabled). Pin it: a guard
# or default-enable regression then fails HERE, not as unexplained count drift.
check("plano 13: gowa plugin NOT loaded under WHATSBOT_TEST (sole-owner suite invariant)",
      "gowa" not in _deps.plugins_registry.loaded)


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

# ═══════════════════════════════════════════════════════════════════
#  19c. Telegram plugin (plano 13 Fase 3) — canal 100% sobre o ponto de extensão
# ═══════════════════════════════════════════════════════════════════
section("Telegram plugin (plano 13)")

import importlib.util as _ilu
_tg_path = Path(__file__).resolve().parent.parent / "assets" / "plugin_examples" / "telegram" / "channels.py"
_tg_spec = _ilu.spec_from_file_location("tg_channels_test", str(_tg_path))
_tg_mod = _ilu.module_from_spec(_tg_spec); _tg_spec.loader.exec_module(_tg_mod)
_TelegramChannel = _tg_mod.TelegramChannel

_tg = _TelegramChannel("tg_ch", credentials={"bot_token": "123:ABC"})

# Capabilities dirigem o comportamento (sem if provider ==)
check("telegram caps: sem QR, sem templates, grupos on, janela 0h",
      (not _tg.capabilities.qr) and (not _tg.capabilities.templates)
      and _tg.capabilities.groups and _tg.capabilities.session_window_hours == 0)
check("telegram é um Channel registrável (CHANNEL_PROVIDERS)",
      _tg_mod.CHANNEL_PROVIDERS == [_TelegramChannel])

# parse_inbound: texto privado
_ev = _tg.parse_inbound({"update_id": 1, "message": {
    "message_id": 11, "date": 1700000000, "chat": {"id": 555, "type": "private"},
    "from": {"id": 555, "first_name": "João", "last_name": "Silva"}, "text": "oi bot"}})[0]
check("telegram parse: texto privado -> message", _ev.kind == "message" and _ev.text == "oi bot")
check("telegram parse: chat_id/sender_id/nome resolvidos",
      _ev.chat_id == "555" and _ev.sender_id == "555" and _ev.sender_name == "João Silva")
check("telegram parse: external_msg_id + ts", _ev.external_msg_id == "11" and _ev.ts == 1700000000.0)
check("telegram parse: privado não é grupo", _ev.is_group is False)

# foto com caption (media_id alimenta o download da pipeline; caption vira texto)
_evp = _tg.parse_inbound({"message": {"message_id": 12, "date": 1, "chat": {"id": 5, "type": "private"},
    "from": {"id": 5, "first_name": "J"}, "photo": [{"file_id": "small"}, {"file_id": "BIG"}], "caption": "olha"}})[0]
check("telegram parse: foto -> image + maior file_id + caption como texto",
      _evp.media_type == "image" and _evp.media_extras.get("media_id") == "BIG" and _evp.text == "olha")

# voz em grupo -> audio (transcrição roda na pipeline)
_evv = _tg.parse_inbound({"message": {"message_id": 13, "date": 1, "chat": {"id": 9, "type": "supergroup", "title": "G"},
    "from": {"id": 7, "username": "ze"}, "voice": {"file_id": "V", "duration": 5, "mime_type": "audio/ogg"}}})[0]
check("telegram parse: voz -> audio + is_voice_note + grupo",
      _evv.media_type == "audio" and _evv.media_extras.get("is_voice_note") and _evv.is_group)

# localização: media_path geo: (NÃO tenta download)
_evl = _tg.parse_inbound({"message": {"message_id": 14, "date": 1, "chat": {"id": 9, "type": "private"},
    "from": {"id": 7}, "location": {"latitude": -23.5, "longitude": -46.6}}})[0]
check("telegram parse: location -> geo media_path",
      _evl.media_type == "location" and _evl.media_path == "geo:-23.5,-46.6")

# reação e updates ignorados
_evr = _tg.parse_inbound({"message_reaction": {"message_id": 20, "date": 1, "chat": {"id": 9},
    "user": {"id": 7}, "new_reaction": [{"type": "emoji", "emoji": "👍"}]}})[0]
check("telegram parse: message_reaction -> reaction event",
      _evr.kind == "reaction" and _evr.media_extras.get("emoji") == "👍"
      and _evr.media_extras.get("reacted_message_id") == "20")
check("telegram parse: update sem mensagem -> []", _tg.parse_inbound({"update_id": 99}) == [])
check("telegram parse: raw não-dict -> []", _tg.parse_inbound(None) == [])

# Outbound/status sem rede: patch do _request
_tg_calls = []
def _tg_fake_request(method, payload=None, files=None, timeout=None):
    _tg_calls.append((method, payload, files))
    if method == "getMe":
        return {"ok": True, "result": {"id": 1, "username": "meubot", "first_name": "Bot"}}
    return {"ok": True, "result": {"message_id": 42}}
_tg._request = _tg_fake_request

_tsr = _tg.send_text("555", "resposta", reply_to="11")
check("telegram send_text -> ok + external_msg_id", _tsr.ok and _tsr.external_msg_id == "42")
check("telegram send_text -> sendMessage + reply_parameters",
      _tg_calls[-1][0] == "sendMessage"
      and _tg_calls[-1][1].get("reply_parameters", {}).get("message_id") == 11)

_tst = _tg.status()
check("telegram status -> conectado via getMe",
      _tst["connected"] and _tst["logged_in"] and _tst.get("own_username") == "meubot")

_tg.react("555", "11", "❤️")
check("telegram react -> setMessageReaction com emoji",
      _tg_calls[-1][0] == "setMessageReaction"
      and _tg_calls[-1][1]["reaction"][0]["emoji"] == "❤️")

_tsm = _tg.send_media("555", "image", "https://x/y.jpg", caption="cap")
check("telegram send_media(url) -> sendPhoto com link no campo photo",
      _tg_calls[-1][0] == "sendPhoto" and _tg_calls[-1][1].get("photo") == "https://x/y.jpg")

# Token ausente -> erro limpo, sem rede (nunca derruba o core)
_tg_noTok = _TelegramChannel("tg2")
check("telegram sem token -> status missing_bot_token",
      _tg_noTok.status().get("error") == "missing_bot_token")
check("telegram sem token -> send_text not ok", not _tg_noTok.send_text("1", "x").ok)

# ── Runtime exposto ao contexto do plugin (plano 13 Fase 1.1) ──
from plugins.context import (set_channel_runtime as _scr, get_channel_runtime as _gcr,
                             PluginContext as _PluginCtx)
from plugins.lifecycle import manager as _lcm11
_prev_rt = _gcr()
def _sentinel_ingest(ev):
    return None
_scr(_registry, _router, _sentinel_ingest)
check("Fase 1.1: get_channel_runtime devolve o que foi wired",
      _gcr() == (_registry, _router, _sentinel_ingest))
check("Fase 1.1: PluginContext expõe os campos de canal",
      all(hasattr(_PluginCtx("x"), a)
          for a in ("channel_registry", "outbound_router", "ingest_event")))
_ctx11 = _lcm11._ensure_context("telegram_rt_test", None, None)
check("Fase 1.1: _ensure_context injeta channel_registry no ctx",
      _ctx11.channel_registry is _registry)
check("Fase 1.1: _ensure_context injeta outbound_router no ctx",
      _ctx11.outbound_router is _router)
check("Fase 1.1: _ensure_context injeta ingest_event no ctx",
      _ctx11.ingest_event is _sentinel_ingest)
_lcm11._contexts.pop("telegram_rt_test", None)
_scr(*_prev_rt)  # restaura o estado anterior do runtime de canal

# ═══════════════════════════════════════════════════════════════════
#  19d. GOWA atrás do contrato (plano 13 Fase 0) — parse_gowa_inbound + ingest
# ═══════════════════════════════════════════════════════════════════
section("GOWA contrato (plano 13)")

from gowa.inbound import parse_gowa_inbound as _pgi


class _FakeGowaClient:
    def get_message_filename(self, jid, mid):
        return "nota.pdf"

    def get_group_name(self, jid):
        return "Equipe"

    def can_bot_send_in_group(self, jid, bot):
        return True

    def is_chat_archived(self, jid):
        return False


_gc = _FakeGowaClient()

# private text
_g = _pgi({"event": "message", "payload": {
    "from": "5511666660001@s.whatsapp.net", "id": "g1", "body": "oi", "from_name": "Ana"}},
    client=_gc)[0]
check("gowa parse: privado -> message in + chat_id digits",
      _g.kind == "message" and _g.direction == "in" and _g.chat_id == "5511666660001"
      and _g.text == "oi" and _g.display_text == "oi" and _g.trigger_ai)

# group sem menção (mention_only) -> trigger_ai False + prefixo [Nome]
_g = _pgi({"event": "message", "payload": {
    "chat_id": "120363001@g.us", "from": "5511666660002@s.whatsapp.net", "id": "g2",
    "body": "bom dia", "from_name": "Bia"}}, client=_gc, group_mode="mention_only")[0]
check("gowa parse: grupo sem menção -> trigger_ai=False + display_text [Nome]:",
      _g.is_group and _g.trigger_ai is False
      and _g.display_text == "[Bia]: bom dia" and _g.group_name == "Equipe")

# group com menção -> trigger_ai True + menção removida do display
_g = _pgi({"event": "message", "payload": {
    "chat_id": "120363001@g.us", "from": "5511666660002@s.whatsapp.net", "id": "g3",
    "body": "@5599 oi bot", "from_name": "Bia"}}, client=_gc, bot_phone="5599",
    group_mode="mention_only")[0]
check("gowa parse: grupo com @menção -> trigger_ai=True + mentioned",
      _g.mentioned and _g.trigger_ai and _g.display_text == "[Bia]: oi bot")

# echo (is_from_me) -> direction out
_g = _pgi({"event": "message", "payload": {
    "from": "5511666660001@s.whatsapp.net", "id": "g4", "body": "resp", "is_from_me": True}},
    client=_gc)[0]
check("gowa parse: echo (is_from_me) -> direction=out", _g.direction == "out")

# document -> filename resolvido via client
_g = _pgi({"event": "message", "payload": {
    "from": "5511666660001@s.whatsapp.net", "id": "g5", "document": {"path": "/x/u.bin"}}},
    client=_gc)[0]
check("gowa parse: documento -> filename resolvido",
      _g.media_type == "document" and _g.media_extras.get("file_name") == "nota.pdf")

# reaction / presence / ack(normalizado) / revoked / ignorado
check("gowa parse: reaction",
      _pgi({"event": "message.reaction", "payload": {
          "chat_id": "5511@s.whatsapp.net", "reaction": "👍", "reacted_message_id": "r1"}})[0].kind == "reaction")
check("gowa parse: presence",
      _pgi({"event": "chat_presence", "payload": {
          "from": "5511@s.whatsapp.net", "state": "composing"}})[0].media_extras.get("state") == "composing")
_acks = _pgi({"event": "message.ack", "payload": {"receipt_type": "read", "ids": ["a1", "a2"],
                                                  "chat_id": "5511@s.whatsapp.net"}})
check("gowa parse: ack normalizado -> 1 receipt por id, status=read",
      len(_acks) == 2 and all(a.kind == "receipt" and a.media_extras.get("status") == "read" for a in _acks))
check("gowa parse: revoked", _pgi({"event": "message.revoked", "payload": {
    "revoked_message_id": "v1", "chat_id": "5511@s.whatsapp.net"}})[0].kind == "revoked")
check("gowa parse: evento desconhecido -> []", _pgi({"event": "nope", "payload": {}}) == [])

# ── ingest_event honra os campos GOWA (via fake_ch, channel-agnostic) ──
settings.set("message_batch_delay", 0)
settings.set("auto_reply", True)
agent_handler.api_key = "test-key-fake"


async def _drive_one(ev):
    await _deps.ingest_event(ev)
    t = _deps.state.processing_tasks.get((ev.channel_id, ev.chat_id or ev.sender_id))
    if t:
        await t


# display_text é o que vai pro histórico/LLM (prefixo [Nome]: preservado)
_fake.sent.clear()
with patch.object(agent_handler, "aprocess_message",
                  new=AsyncMock(return_value=_PR(reply="ok grupo"))):
    _aio.run(_drive_one(_InboundEvent(
        channel_id="fake_ch", provider="test", kind="message",
        external_msg_id="gi_disp", chat_id="5511666661111", sender_id="5511666661111",
        text="olá", display_text="[Ana]: olá", trigger_ai=True)))
_fc_disp = _contact11.get_by_phone("5511666661111")
check("ingest: display_text salvo no histórico (prefixo [Nome]:)",
      bool(_fc_disp) and any(m["content"] == "[Ana]: olá" for m in message_repo.get_all(_fc_disp["id"])))

# trigger_ai=False (grupo sem menção): salva, NÃO agenda orquestrador, NÃO responde
_fake.sent.clear()
_aio.run(_drive_one(_InboundEvent(
    channel_id="fake_ch", provider="test", kind="message",
    external_msg_id="gi_noai", chat_id="5511666662222@g.us", sender_id="5511666662299",
    is_group=True, text="oi grupo", display_text="[Zé]: oi grupo", trigger_ai=False)))
_fc_noai = _contact11.get_by_phone("5511666662222@g.us")
check("ingest: trigger_ai=False salva a mensagem no histórico",
      bool(_fc_noai) and any(m["content"] == "[Zé]: oi grupo" for m in message_repo.get_all(_fc_noai["id"])))
check("ingest: trigger_ai=False NÃO responde (sem envio pelo canal)",
      not any("oi grupo" in t for _, t in _fake.sent))
check("ingest: trigger_ai=False NÃO deixa task de orquestrador ativa",
      _deps.state.processing_tasks.get(("fake_ch", "5511666662222@g.us")) is None)

# echo (direction=out): salva como assistant/operator, não responde
_fake.sent.clear()
_aio.run(_drive_one(_InboundEvent(
    channel_id="fake_ch", provider="test", kind="message", direction="out",
    external_msg_id="gi_echo", chat_id="5511666663333", sender_id="5511666663333",
    text="enviado do celular")))
_fc_echo = _contact11.get_by_phone("5511666663333")
_echo_msgs = message_repo.get_all(_fc_echo["id"]) if _fc_echo else []
check("ingest: echo (direction=out) salvo como assistant",
      any(m["content"] == "enviado do celular" and m["role"] == "assistant" for m in _echo_msgs))
check("ingest: echo não dispara envio pelo canal",
      not any("enviado do celular" in t for _, t in _fake.sent))

# ── HTTP real: GOWA pela rota genérica /api/webhook/gowa/default (Fase 0.3) ──
# Exatamente o caminho que o subprocesso GOWA passa a usar ao vivo:
# POST → GOWAChannel.parse_inbound → parse_gowa_inbound → _dispatch_events → ingest.
settings.set("message_batch_delay", 0)

# Privado: contato com IA off (não dispara LLM no ciclo de fundo); só validamos o wiring.
_contact11.get_or_create("5511707070001")
_contact11.update(_contact11.get_by_phone("5511707070001")["id"], ai_enabled=0)
r = client.post("/api/webhook/gowa/default", json={
    "event": "message", "payload": {
        "from": "5511707070001@s.whatsapp.net", "id": "gw_http_1", "body": "oi gowa",
        "from_name": "Cliente"}})
check("HTTP gowa: POST /api/webhook/gowa/default -> 200", r.status_code == 200)
check("HTTP gowa: 1 evento parseado + ingerido",
      r.json()["data"].get("events") == 1 and r.json()["data"].get("handled", 0) >= 1)

# Grupo sem menção: salva no histórico de forma síncrona (trigger_ai=False), sem resposta
r = client.post("/api/webhook/gowa/default", json={
    "event": "message", "payload": {
        "chat_id": "120363707070@g.us", "from": "5511707070099@s.whatsapp.net",
        "id": "gw_http_grp", "body": "bom dia grupo", "from_name": "Membro"}})
check("HTTP gowa: grupo sem menção -> 200", r.status_code == 200)
_grp = _contact11.get_by_phone("120363707070@g.us")
check("HTTP gowa: grupo sem menção salvo com prefixo [Nome]:",
      bool(_grp) and any(m["content"] == "[Membro]: bom dia grupo"
                         for m in message_repo.get_all(_grp["id"])))

# Echo (is_from_me) pela rota genérica -> salvo como assistant/operator (síncrono)
r = client.post("/api/webhook/gowa/default", json={
    "event": "message", "payload": {
        "from": "5511707070002@s.whatsapp.net", "id": "gw_http_echo",
        "body": "msg do meu celular", "is_from_me": True}})
check("HTTP gowa: echo -> 200", r.status_code == 200)
_echo_http = _contact11.get_by_phone("5511707070002")
check("HTTP gowa: echo salvo como assistant/operator",
      bool(_echo_http) and any(m["content"] == "msg do meu celular" and m["role"] == "assistant"
                               for m in message_repo.get_all(_echo_http["id"])))

# Reaction pela rota genérica
r = client.post("/api/webhook/gowa/default", json={
    "event": "message.reaction", "payload": {
        "chat_id": "5511707070001@s.whatsapp.net", "from": "5511707070001@s.whatsapp.net",
        "reaction": "🔥", "reacted_message_id": "gw_http_1"}})
check("HTTP gowa: reaction -> 200 + handled", r.status_code == 200
      and r.json()["data"].get("handled", 0) >= 1)

# Canal desconhecido nunca derruba (200 ignored)
check("HTTP gowa: canal inexistente -> 200 ignored",
      client.post("/api/webhook/gowa/nao_existe", json={"event": "message", "payload": {}}).status_code == 200)

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

# sandbox/send requires a working LLM — mock it. Fase B5/C-1: the sandbox now
# awaits the async ``aprocess_message`` (sync ``process_message`` was removed).
from agent.handler import ProcessResult
with patch.object(agent_handler, "aprocess_message",
                  new=AsyncMock(return_value=ProcessResult(
                      reply="Resposta de teste", tool_calls=[]))):
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

for path in ["/", "/contacts", "/dashboard", "/attendances", "/audit", "/sandbox", "/costs",
             "/quick-replies", "/custom-attributes", "/runtime", "/users", "/conversations", "/ai"]:
    r = client.get(path)
    check(f"GET {path} -> 200", r.status_code == 200)

# Legacy PT aliases still serve the SPA (frontend rewrites them to the English path).
for path in ["/contatos", "/painel", "/atendimentos", "/auditoria"]:
    r = client.get(path)
    check(f"GET {path} (legacy alias) -> 200", r.status_code == 200)

# Conversa-cêntrico (plano 11 D1): /conversations/<id> serve o SPA (refresh direto
# no chat de uma conversa) — espelha /contacts/<id>.
check("GET /conversations/1 (SPA) -> 200", client.get("/conversations/1").status_code == 200)
check("GET /contacts/1 (SPA) -> 200", client.get("/contacts/1").status_code == 200)

# Deep-links por entidade (identidade natural): cada um serve o SPA no reload,
# mirroring /contacts/<id>. Sub-abas do /ai e do /users também são deep-linkáveis.
for path in [
    "/ai/agents", "/ai/agents/default", "/ai/prompts/saudacao",
    "/ai/variables/nome_empresa", "/ai/tools", "/ai/tools/save_contact_info",
    "/plugins/lembretes", "/channels/default",
    "/users/1", "/users/roles", "/users/roles/gestor",
    "/quick-replies/saudacao",
    "/custom-attributes/contact", "/custom-attributes/contact/empresa",
    "/custom-attributes/conversation/prioridade",
]:
    check(f"GET {path} (SPA) -> 200", client.get(path).status_code == 200)

# short_code com "/" codificado (%2F) ainda serve o SPA (não 404).
check("GET /quick-replies/%2Fsaud (SPA) -> 200",
      client.get("/quick-replies/%2Fsaud").status_code == 200)
# /users/roles é palavra reservada — não pode ser capturado como user_id numérico.
check("GET /users/roles != user_id (SPA) -> 200", client.get("/users/roles").status_code == 200)

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

# Os prefixos de deep-link (SPA) ficam abertos mesmo com senha, para o reload de
# uma URL de entidade servir o index.html — mas a API equivalente segue protegida.
check("GET /channels/default (SPA, no token) -> 200",
      client.get("/channels/default").status_code == 200)
check("GET /ai/agents/default (SPA, no token) -> 200",
      client.get("/ai/agents/default").status_code == 200)
check("GET /api/channels (no token) -> 401 (API ainda protegida)",
      client.get("/api/channels").status_code == 401)

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

# Webhook should be exempt from auth (live generic route, exempt via the
# ``/api/webhook/`` prefix — the legacy exact ``/api/webhook`` route is retired).
r = client.post("/api/webhook/gowa/default", json={"event": "unknown"})
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
    r = client.post("/api/webhook/gowa/default", json={
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
    r = client.post("/api/webhook/gowa/default", json={
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
        self.created = None
        self.deleted = None
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

    def create_template(self, name, *, category, language, body_text,
                        header_text=None, footer_text=None,
                        body_examples=None, header_examples=None):
        self.created = {"name": name, "category": category, "language": language,
                        "body_text": body_text, "header_text": header_text,
                        "footer_text": footer_text, "body_examples": body_examples}
        return {"ok": True, "id": "TPLNEW", "status": "PENDING", "category": category}

    def delete_template(self, name):
        self.deleted = name
        return {"ok": True}


from db.repositories import channel_repo as _tpl_chrepo
# DB row so the channel-scoped endpoints (plano 21) resolve it (channel_repo.get
# finds it; enabled=1 keeps it LIVE) plus the registry fixture below.
_tpl_chrepo.create(id="cloud_test", provider="whatsapp_cloud", display_name="Cloud Test", enabled=1)
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

# ── Create / delete templates (gated template.create / template.delete) ──
_rt = client.get(f"/api/conversations/{_tpl_conv['id']}/templates").json()["data"]
check("GET templates (cloud) -> can_create/can_delete flags (open install)",
      _rt.get("can_create") is True and _rt.get("can_delete") is True)

r = client.post(f"/api/conversations/{_tpl_conv['id']}/templates", json={
    "name": "pedido_ok", "category": "UTILITY", "language": "pt_BR",
    "body_text": "Olá {{1}}, pedido {{2}} ok", "body_examples": ["João", "123"]})
check("POST create template -> 200", r.status_code == 200)
check("create template -> status PENDING retornado", r.json()["data"].get("status") == "PENDING")
check("create template -> canal recebeu a definição",
      _fake_ch.created and _fake_ch.created["name"] == "pedido_ok"
      and _fake_ch.created["category"] == "UTILITY"
      and _fake_ch.created["body_examples"] == ["João", "123"])
check("create template nome inválido (maiúsc/espaço) -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "Pedido OK", "body_text": "x"}).status_code == 400)
check("create template sem body_text -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "ok"}).status_code == 400)
check("create template categoria inválida -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "ok", "body_text": "x", "category": "NOPE"}).status_code == 400)
check("create template canal sem suporte -> 400",
      client.post(f"/api/conversations/{_conv2['id']}/templates",
                  json={"name": "ok", "body_text": "x"}).status_code == 400)

r = client.delete(f"/api/conversations/{_tpl_conv['id']}/templates/pedido_ok")
check("DELETE template -> 200", r.status_code == 200)
check("delete template -> canal recebeu name", _fake_ch.deleted == "pedido_ok")
check("delete template canal sem suporte -> 400",
      client.delete(f"/api/conversations/{_conv2['id']}/templates/x").status_code == 400)

# Compositor hints on the chat-messages endpoint.
r = client.get(f"/api/conversations/{_tpl_conv['id']}/messages")
check("conv messages (cloud) -> templates_supported=true", r.json()["data"].get("templates_supported") is True)
check("conv messages (cloud, sem inbound) -> session_open=false (janela 24h)",
      r.json()["data"].get("session_open") is False)
check("conv messages (default) -> templates_supported=false",
      client.get(f"/api/conversations/{_conv2['id']}/messages").json()["data"].get("templates_supported") is False)

# ── Channel-scoped session-state + templates (plano 21: Nova conversa) ──
# A "Nova conversa" precisa, ANTES de existir conversa, saber a janela de 24h e
# (se fechada) listar/enviar templates pelo canal.
section("Nova conversa — janela 24h + templates por canal (plano 21)")
from db.repositories import contact_repo as _ct_repo21
_ph21 = _ct_repo21.get(_cid)["phone"]

# Cloud com conversa existente mas SEM inbound recente -> janela fechada (template).
r = client.get(f"/api/channels/cloud_test/session-state?phone={_ph21}")
check("session-state (cloud) -> 200", r.status_code == 200)
_ss = r.json()["data"]
check("session-state (cloud) -> templates_supported=true", _ss.get("templates_supported") is True)
check("session-state (cloud, sem inbound) -> session_open=false", _ss.get("session_open") is False)
check("session-state (cloud) -> has_conversation=true", _ss.get("has_conversation") is True)
check("session-state (cloud) -> conversation_id do thread", _ss.get("conversation_id") == _tpl_conv["id"])

# GOWA é sempre aberto e não tem templates.
_ssg = client.get("/api/channels/default/session-state?phone=5511777770000").json()["data"]
check("session-state (gowa) -> session_open=true (sempre aberto)", _ssg.get("session_open") is True)
check("session-state (gowa) -> templates_supported=false", _ssg.get("templates_supported") is False)
check("session-state (gowa, sem conversa) -> has_conversation=false", _ssg.get("has_conversation") is False)

check("session-state sem phone -> 400",
      client.get("/api/channels/cloud_test/session-state").status_code == 400)
check("session-state canal inexistente -> 404",
      client.get("/api/channels/naoexiste/session-state?phone=5511").status_code == 404)

# Lista de templates por canal (mesmo shape do conversation-scoped).
_rct = client.get("/api/channels/cloud_test/templates").json()["data"]
check("channel templates (cloud) -> supported=true", _rct["supported"] is True)
check("channel templates (cloud) -> lista boas_vindas",
      any(t["name"] == "boas_vindas" for t in _rct["templates"]))
check("channel templates (gowa) -> supported=false",
      client.get("/api/channels/default/templates").json()["data"]["supported"] is False)

# Enviar template por canal cria a conversa nova (telefone fresco, não polui _tpl_conv).
r = client.post("/api/channels/cloud_test/send-template", json={
    "phone": "5511888880000", "template_name": "boas_vindas", "language": "pt_BR",
    "components": [{"type": "body", "parameters": [{"type": "text", "text": "Novo"}]}],
    "preview_text": "Olá Novo, boas-vindas!"})
check("channel send-template -> 200", r.status_code == 200)
check("channel send-template -> msg_id", r.json()["data"].get("msg_id") == "tpl_msg_99")
check("channel send-template sem phone -> 400",
      client.post("/api/channels/cloud_test/send-template",
                  json={"template_name": "x"}).status_code == 400)
check("channel send-template sem template_name -> 400",
      client.post("/api/channels/cloud_test/send-template",
                  json={"phone": "5511"}).status_code == 400)
check("channel send-template canal sem suporte (gowa) -> 400",
      client.post("/api/channels/default/send-template",
                  json={"phone": "5511", "template_name": "x"}).status_code == 400)

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
        self.content = b"{}"

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
check("list_templates -> inclui todos os status (PENDING incluso)",
      {t["name"] for t in _tpls} == {"t1", "t2_pending", "t3"})
check("list_templates -> seguiu paginação (2 páginas)", len(_tpls) == 3)
check("list_templates -> normaliza type p/ minúsculas",
      _tpls[0]["components"][0]["type"] == "body")
check("list_templates -> preserva status p/ badge",
      next(t for t in _tpls if t["name"] == "t2_pending")["status"] == "PENDING")
check("list_templates sem waba_id -> []",
      _wac.WhatsAppCloudChannel("x", credentials={"access_token": "T"}).list_templates() == [])


# ── WhatsAppCloudChannel.create_template / delete_template (mock Graph) ──
class _FakeWriteClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        self.calls.append(("post", url, json))
        return self.resp

    def delete(self, url, headers=None, params=None):
        self.calls.append(("delete", url, params))
        return self.resp


_fwc = _FakeWriteClient(_Resp(200, {"id": "TPL123", "status": "PENDING", "category": "UTILITY"}))
_wac.httpx.Client = lambda *a, **k: _fwc
try:
    _ch2 = _wac.WhatsAppCloudChannel("cloud_unit", credentials={
        "waba_id": "WABA1", "access_token": "TOK", "phone_number_id": "PN"})
    _cres = _ch2.create_template(
        "pedido_ok", category="UTILITY", language="pt_BR",
        body_text="Olá {{1}}, pedido {{2}} ok", header_text="Aviso {{1}}",
        footer_text="Equipe", body_examples=["João", "123"], header_examples=["Promo"])
finally:
    _wac.httpx.Client = _orig_httpx_client
check("create_template -> ok + id/status",
      _cres.get("ok") and _cres.get("id") == "TPL123" and _cres.get("status") == "PENDING")
_sent_payload = _fwc.calls[-1][2]
check("create_template -> componentes HEADER/BODY/FOOTER uppercase",
      [c["type"] for c in _sent_payload["components"]] == ["HEADER", "BODY", "FOOTER"])
_body_comp = next(c for c in _sent_payload["components"] if c["type"] == "BODY")
check("create_template -> body example aninhado [[...]]",
      _body_comp["example"]["body_text"] == [["João", "123"]])
_hdr_comp = next(c for c in _sent_payload["components"] if c["type"] == "HEADER")
check("create_template -> header TEXT + example",
      _hdr_comp["format"] == "TEXT" and _hdr_comp["example"]["header_text"] == ["Promo"])
check("create_template -> payload name/category/language",
      _sent_payload["name"] == "pedido_ok" and _sent_payload["category"] == "UTILITY"
      and _sent_payload["language"] == "pt_BR")
check("create_template sem waba_id -> ok=False",
      _wac.WhatsAppCloudChannel("x", credentials={"access_token": "T"}).create_template(
          "n", category="UTILITY", language="pt_BR", body_text="b").get("ok") is False)

_fwc_del = _FakeWriteClient(_Resp(200, {"success": True}))
_wac.httpx.Client = lambda *a, **k: _fwc_del
try:
    _ch3 = _wac.WhatsAppCloudChannel("cloud_unit", credentials={
        "waba_id": "WABA1", "access_token": "TOK", "phone_number_id": "PN"})
    _dres = _ch3.delete_template("pedido_ok")
finally:
    _wac.httpx.Client = _orig_httpx_client
check("delete_template -> ok", _dres.get("ok") is True)
check("delete_template -> chamou DELETE com name",
      _fwc_del.calls[-1][0] == "delete" and _fwc_del.calls[-1][2] == {"name": "pedido_ok"})

# ═══════════════════════════════════════════════════════════════════
#  WhatsApp Cloud — media upload (P1) + janela 24h no envio (P3)
# ═══════════════════════════════════════════════════════════════════
section("WhatsApp Cloud — upload de mídia (P1) + gate janela 24h (P3)")

# A local file written by the panel must be UPLOADED to /media and sent by id —
# sending the local path as `link` is what produced (#100) not a valid URI.
_media_tmp = os.path.join(_tmpdir, "outbox_img.png")
with open(_media_tmp, "wb") as _mf:
    _mf.write(b"\x89PNG\r\n\x1a\nFAKEDATA")


class _FakeMediaClient:
    def __init__(self):
        self.upload_calls = []
        self.msg_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None, data=None, files=None):
        if files is not None:                       # multipart upload to /media
            self.upload_calls.append({"url": url, "data": data})
            return _Resp(200, {"id": "MEDIA_XYZ"})
        self.msg_calls.append({"url": url, "json": json})  # JSON message send
        return _Resp(200, {"messages": [{"id": "wamid.OUT"}]})


_fmc = _FakeMediaClient()
_wac.httpx.Client = lambda *a, **k: _fmc
try:
    _chm = _wac.WhatsAppCloudChannel("cloud_unit", credentials={
        "access_token": "TOK", "phone_number_id": "PN"})
    _mres = _chm.send_media("5511", "image", _media_tmp, caption="oi")
finally:
    _wac.httpx.Client = _orig_httpx_client
check("send_media(local) -> ok + external id", _mres.ok and _mres.external_msg_id == "wamid.OUT")
check("send_media(local) -> upload em /{phone_id}/media",
      bool(_fmc.upload_calls) and _fmc.upload_calls[-1]["url"].endswith("/PN/media"))
check("send_media(local) -> upload com messaging_product=whatsapp",
      _fmc.upload_calls[-1]["data"].get("messaging_product") == "whatsapp")
check("send_media(local) -> mensagem usa media id, não link",
      _fmc.msg_calls[-1]["json"]["image"].get("id") == "MEDIA_XYZ"
      and "link" not in _fmc.msg_calls[-1]["json"]["image"])
check("send_media(local) -> caption preservado",
      _fmc.msg_calls[-1]["json"]["image"].get("caption") == "oi")

# A public URL is sent as link (no upload).
_fmc2 = _FakeMediaClient()
_wac.httpx.Client = lambda *a, **k: _fmc2
try:
    _chu = _wac.WhatsAppCloudChannel("cloud_unit", credentials={
        "access_token": "TOK", "phone_number_id": "PN"})
    _chu.send_media("5511", "image", "https://pub.example/x.jpg")
finally:
    _wac.httpx.Client = _orig_httpx_client
check("send_media(url pública) -> sem upload, usa link",
      not _fmc2.upload_calls
      and _fmc2.msg_calls[-1]["json"]["image"].get("link") == "https://pub.example/x.jpg")


# Upload failure surfaces a clean SendResult error (never an invalid link).
class _FailUploadClient(_FakeMediaClient):
    def post(self, url, headers=None, json=None, data=None, files=None):
        if files is not None:
            return _Resp(400, {"error": {"message": "bad media"}})
        return _Resp(200, {"messages": [{"id": "x"}]})


_ffu = _FailUploadClient()
_wac.httpx.Client = lambda *a, **k: _ffu
try:
    _chf = _wac.WhatsAppCloudChannel("cloud_unit", credentials={
        "access_token": "TOK", "phone_number_id": "PN"})
    _fres = _chf.send_media("5511", "image", _media_tmp)
finally:
    _wac.httpx.Client = _orig_httpx_client
check("send_media(local) upload falha -> ok=False media_upload_failed",
      _fres.ok is False and _fres.error == "media_upload_failed")

# ── 24h send gate on the operator routes (P3) ──
from db.repositories import conversation_repo as _cr_gate
_gate_conv = _cr_gate.get_with_channel(_tpl_conv["id"])  # cloud_test, window=24h
_gate_phone = _gate_conv["contact_phone"]

r = client.post(f"/api/contacts/{_gate_phone}/send",
                json={"message": "fora da janela", "conversation_id": _tpl_conv["id"]})
check("send (cloud sem inbound recente) -> 409 janela 24h", r.status_code == 409)
check("send (cloud fora da janela) -> reason session_window_closed",
      (r.json().get("data") or {}).get("reason") == "session_window_closed")

# A recent inbound reopens the 24h window -> free text allowed again.
_sn_msg_repo.add(_gate_conv["contact_id"], "user", "oi de novo",
                 conversation_id=_tpl_conv["id"], ts=time.time())
r = client.post(f"/api/contacts/{_gate_phone}/send",
                json={"message": "agora vai", "conversation_id": _tpl_conv["id"]})
check("send (cloud com inbound recente) -> 200 dentro da janela", r.status_code == 200)

# GOWA (session_window_hours=0) is never gated.
r = client.post("/api/contacts/5511999990001/send", json={"message": "gowa livre"})
check("send (gowa) -> não bloqueado pela janela 24h", r.status_code == 200)

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
