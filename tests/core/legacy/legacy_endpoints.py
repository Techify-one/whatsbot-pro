"""Comprehensive endpoint tests for WhatsBot API.

Uses FastAPI TestClient with the configured PostgreSQL test database.
No external services needed (GOWA/OpenRouter are mocked).
"""

import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from tests.paths import PROJECT_ROOT

# Ensure project root is on path
sys.path.insert(0, str(PROJECT_ROOT))

from server import upload_limits  # noqa: E402  (plano 64 — tetos de upload)

# Load-bearing (plano 13): the test's Settings() resolves data_dir to the repo
# root, so create_app reads the REAL storages/plugins + assets/plugin_examples.
# This flag makes bootstrap_gowa_upgrade a no-op so the suite never copies/enables
# the bundled gowa plugin into the git-ignored storages/plugins (which would change
# create_app behavior and dirty the tree). Set BEFORE importing server.app.
os.environ["WHATSBOT_TEST"] = "1"

# Initialize the engine on the Postgres TEST database (plano 29 C3): schema
# reset + Alembic head via tests.pg (URL de WHATSBOT_TEST_DB_URL ou .env).
# ``_tmpdir`` continua existindo só para artefatos de mídia do teste.
_tmpdir = tempfile.mkdtemp(prefix="whatsbot_test_")

from tests.pg import init_test_engine  # noqa: E402

init_test_engine(reset=True)

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
# Anonymous client (never carries a default auth header). Used for the "no token"
# assertions once the suite bootstraps an admin and sets a default Authorization
# header on ``client`` (plano 48 F0 — the API gate closes once ≥1 user exists, so
# an unauthenticated request must come from a client with no bearer token).
anon = TestClient(app, raise_server_exceptions=False)


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


skipped: list[str] = []


def plugin_source_or_skip(plugin_id: str, what: str):
    """Resolve a plugin source tree, or record a SKIP when it is not shipped here.

    Only ``gowa`` is bundled with this repository; every other plugin lives in the
    plugins repository (``WHATSBOT_PLUGIN_SOURCE_ROOT``) or in an installed tree.
    Blocks below that assert PROVIDER-SPECIFIC behaviour resolve their source
    through this helper, so a checkout without those plugins skips the block
    instead of aborting the whole script at import time.

    Returns the source directory, or ``None`` when the plugin is unavailable.
    """
    from tests.plugin_test_utils import PluginSourceNotFound, resolve_plugin_source
    try:
        return resolve_plugin_source(plugin_id)
    except PluginSourceNotFound:
        label = f"{plugin_id}: {what}"
        if label not in skipped:
            skipped.append(label)
            print(f"  SKIP {label} — fonte do plugin ausente neste checkout")
        return None


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

# ── plano 48 F1 — anti-lockout: numa instalação nova (0 usuários) o gate fica
#    ABERTO para o 1º admin ser criável. /auth/check reporta has_users=false, o
#    /api/* responde sem token e o /ws conecta — senão a instalação novinha
#    ficaria trancada. O bootstrap em si (0→1 admin) é exercitado adiante, na
#    seção RBAC; aqui ainda há 0 usuários.
import json as _json_f1
check("F1 fresh install: /auth/check -> has_users=false",
      client.get("/api/auth/check").json()["data"]["has_users"] is False)
check("F1 fresh install: /api/config aberto sem token (0 usuários)",
      anon.get("/api/config").status_code == 200)
# (At 0 users the gate is open, so this proves bootstrap is REACHABLE — reaches the
# handler and validates the body — not the prefix-exemption mechanism per se.)
check("F1 fresh install: /api/auth/bootstrap alcançável a 0 usuários (400 sem body)",
      anon.post("/api/auth/bootstrap", json={}).status_code == 400)
try:
    with anon.websocket_connect("/ws") as _ws_f1:
        _ev_f1 = _json_f1.loads(_ws_f1.receive_text())
    check("F1 fresh install: /ws aberto sem token (0 usuários)", _ev_f1.get("event") == "status")
except Exception:
    check("F1 fresh install: /ws aberto sem token (0 usuários)", False)

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

# plano 47 (D9) — tamanho global do resultado de tool reaproveitado
r = client.get("/api/config")
check("GET /api/config -> has ai_tool_reuse_result_max_chars",
      "ai_tool_reuse_result_max_chars" in r.json()["data"])
r = client.put("/api/config", json={"ai_tool_reuse_result_max_chars": 350})
check("PUT /api/config (ai_tool_reuse_result_max_chars) -> 200", r.status_code == 200)
check("PUT /api/config -> ai_tool_reuse_result_max_chars persisted",
      client.get("/api/config").json()["data"]["ai_tool_reuse_result_max_chars"] == 350)
client.put("/api/config", json={"ai_tool_reuse_result_max_chars": 800})  # restore

# plano 47 (D8) — toggle reuse_result por-tool (uniforme, sem guarda por nome)
_tools = client.get("/api/tools").json()["data"]["tools"]
check("GET /api/tools -> item traz reuse_result", bool(_tools) and "reuse_result" in _tools[0])
if _tools:
    _tname = _tools[0]["name"]
    r = client.put(f"/api/tools/{_tname}", json={"reuse_result": True})
    check("PUT /api/tools {reuse_result:true} -> 200", r.status_code == 200)
    check("PUT /api/tools -> reuse_result persisted", r.json()["data"]["reuse_result"] is True)
    client.put(f"/api/tools/{_tname}", json={"reuse_result": False})  # restore

# ── public_base_url: captura + self-heal + override por env ──────────────────
# Garante um estado limpo de env (sem override) para os testes de captura/self-heal.
_saved_pbu_env = {k: os.environ.pop(k, None)
                  for k in ("WHATSBOT_PUBLIC_URL", "PUBLIC_BASE_URL")}
try:
    # public_base_url é exposto e writable (editável na UI Configurações → Avançado)
    check("GET /api/config -> has public_base_url", "public_base_url" in data)

    # Semeia um IP de LAN (cenário do bug: 1º acesso foi direto pelo IP:porta local)
    client.put("/api/config", json={"public_base_url": "http://203.0.113.40:8090"})

    # Self-heal: acessando pelo domínio (headers de proxy reverso), um IP de LAN
    # salvo é substituído pelo domínio real — regressão do link com IP local.
    r = client.get("/api/config", headers={
        "x-forwarded-host": "whatsbot-dev.teste.techify.run",
        "x-forwarded-proto": "https"})
    check("public_base_url self-heal: IP de LAN -> domínio",
          r.json()["data"]["public_base_url"] == "https://whatsbot-dev.teste.techify.run")

    # Um domínio já salvo NÃO é sobrescrito por um acesso via loopback (visita dev).
    r = client.get("/api/config", headers={
        "x-forwarded-host": "localhost:8090", "x-forwarded-proto": "http"})
    check("public_base_url: loopback não sobrescreve domínio salvo",
          r.json()["data"]["public_base_url"] == "https://whatsbot-dev.teste.techify.run")

    # Edição manual via PUT (campo writable) persiste, normalizada (sem barra final).
    r = client.put("/api/config", json={"public_base_url": "https://manual.example.com/"})
    check("PUT public_base_url -> 200", r.status_code == 200)
    r = client.get("/api/config", headers={
        "x-forwarded-host": "manual.example.com", "x-forwarded-proto": "https"})
    check("public_base_url: edição manual persiste sem barra final",
          r.json()["data"]["public_base_url"] == "https://manual.example.com")

    # Override por env é autoritativo (proxies que não repassam x-forwarded-*).
    os.environ["WHATSBOT_PUBLIC_URL"] = "https://env-forced.example.com"
    r = client.get("/api/config", headers={
        "x-forwarded-host": "outro.example.com", "x-forwarded-proto": "https"})
    check("public_base_url: env WHATSBOT_PUBLIC_URL tem prioridade",
          r.json()["data"]["public_base_url"] == "https://env-forced.example.com")
finally:
    # Restaura env e limpa o valor semeado para não vazar pros próximos testes.
    os.environ.pop("WHATSBOT_PUBLIC_URL", None)
    os.environ.pop("PUBLIC_BASE_URL", None)
    for _k, _v in _saved_pbu_env.items():
        if _v is not None:
            os.environ[_k] = _v
    config_repo.set("public_base_url", "")

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

# plano 50 F5 — paginação server-side (só quando `limit` é passado; sem ele = legado).
# Semear contatos suficientes p/ ter mais de uma página.
for _i in range(8):
    _seed = contact_repo.get_or_create(f"55000512{_i:04d}")
    contact_repo.update(_seed["id"], name=f"Seed Pag {_i}")
r = client.get("/api/contacts?limit=3&offset=0")
check("GET /api/contacts?limit=3 -> 200", r.status_code == 200)
_pg = r.json()["data"]
check("F5: com limit -> envelope {items,total,has_more}",
      isinstance(_pg, dict) and "items" in _pg and "total" in _pg and "has_more" in _pg)
check("F5: página respeita limit (<=3 itens)", len(_pg["items"]) <= 3)
check("F5: total >= itens da página e has_more coerente",
      _pg["total"] >= len(_pg["items"]) and _pg["has_more"] is (0 + len(_pg["items"]) < _pg["total"]))
check("F5: itens da página expõem avatar_v", all("avatar_v" in c for c in _pg["items"]))
# Caminhar as páginas reconstrói o universo sem dup (mesmo total).
_seen_ids, _off, _guard = set(), 0, 0
while _guard < 500:
    _guard += 1
    _p = client.get(f"/api/contacts?limit=25&offset={_off}").json()["data"]
    for _c in _p["items"]:
        _seen_ids.add(_c["id"])
    if not _p["has_more"]:
        break
    _off += 25
check("F5: paginação cobre todos os contatos (sem dup)", len(_seen_ids) == _p["total"],
      f"vistos={len(_seen_ids)} total={_p['total']}")
# limit gigante é capado (não estoura), offset negativo vira 0.
_r_cap = client.get("/api/contacts?limit=99999").json()["data"]
check("F5: limit=99999 capado por CAP_LIST (<=200)", len(_r_cap["items"]) <= 200)
# Busca + paginação: envelope também, e a página é subconjunto do filtro.
_r_qp = client.get("/api/contacts?q=Seed+Pag&limit=5&offset=0").json()["data"]
check("F5: busca paginada -> envelope", isinstance(_r_qp, dict) and "items" in _r_qp)
check("F5: busca paginada respeita limit", len(_r_qp["items"]) <= 5)
check("F5: sem limit continua lista legada (retrocompat)",
      isinstance(client.get("/api/contacts").json()["data"], list))

# plano 50 F7 — sort=name devolve a página em ordem alfabética (tela /contacts).
_r_sort = client.get("/api/contacts?limit=50&offset=0&sort=name").json()["data"]
_names = [(c.get("name") or c.get("phone") or "") for c in _r_sort["items"]]
check("F7: sort=name -> página em ordem alfabética",
      _names == sorted(_names, key=lambda s: s.lower()))
# default (recency) difere de name (ordenações distintas) — sanity de que o param pega.
_r_rec = client.get("/api/contacts?limit=50&offset=0").json()["data"]
check("F7: sort default é recência (envelope válido)",
      isinstance(_r_rec.get("items"), list))

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

# plano 50 F6 — o scan de conteúdo é bounded (só as N recentes) + guarda de >=2 chars.
# Match por conteúdo com 2+ chars ainda funciona (regressão coberta acima); uma busca de
# 1 caractere NÃO aciona o scan (não traz o contato que só casa por mensagem).
_r1 = client.get("/api/contacts?q=ç").json()["data"]  # 1 char: só nome/telefone/tag
check("F6: busca de 1 char não casa por conteúdo de mensagem",
      not any(c["phone"] == "5511999990010" for c in _r1))
from db.search.contact_search import (MESSAGE_SCAN_CAP as _SCAN_CAP,
                                      MIN_SCAN_QUERY_LEN as _MINLEN,
                                      contact_ids_matching_message as _cimm)
from db.engine import get_engine as _f6_get_engine
check("F6: guarda de comprimento mínimo do scan (>=2)", _MINLEN >= 2)
check("F6: scan cap é um teto positivo", _SCAN_CAP > 0)
# O scan de 1 char retorna vazio direto (nem toca no banco).
with _f6_get_engine().connect() as _c6:
    check("F6: contact_ids_matching_message('a') -> {} (abaixo do mínimo)",
          _cimm(_c6, "a", False) == {})

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
#  plano 50 F11 — Export CSV: streaming, sem N+1 de tags, íntegro
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Export (plano 50 F11)")
# Contato com tags, para validar que o batch de tags carrega certo no export.
_exp_c = contact_repo.get_or_create("5500051100001")
contact_repo.update(_exp_c["id"], name="Export Zeca")
tag_repo.create("vip-export", "#ff0000")
tag_repo.set_contact_tags(_exp_c["id"], ["vip-export"])  # set_contact_tags usa NOMES
r = client.get("/api/contacts/export")
check("F11: GET /contacts/export -> 200", r.status_code == 200)
check("F11: export content-type é CSV", "text/csv" in r.headers.get("content-type", ""))
check("F11: export é attachment (contatos.csv)",
      "contatos.csv" in r.headers.get("content-disposition", ""))
_lines = r.text.splitlines()
check("F11: CSV tem header com phone/name/tags", bool(_lines) and "phone" in _lines[0] and "tags" in _lines[0])
check("F11: BOM no início (Excel UTF-8)", r.text[:1] == "﻿")
# A linha do Zeca traz a tag (batch de tags funcionou; sem N+1).
_zeca = [ln for ln in _lines if "5500051100001" in ln]
check("F11: contato exportado aparece no CSV", len(_zeca) == 1)
check("F11: tag do contato vem na linha (batch, sem N+1)",
      bool(_zeca) and "vip-export" in _zeca[0])
# iter_for_export em chunks cobre todos os contatos (paridade com list_for_export).
from db.repositories import contact_query as _cq
_all_export = list(_cq.iter_for_export(None))
_chunked = list(_cq.iter_for_export(None, chunk=2))
check("F11: iter_for_export chunk=2 == chunk padrão (mesma cobertura)",
      [c["phone"] for c in _all_export] == [c["phone"] for c in _chunked])
check("F11: list_for_export delega ao gerador (mesmo total)",
      len(contact_repo.list_for_export(None)) == len(_all_export))

# plano 50 F12 — defesa em profundidade: a lista de atendimentos também capa o limit
# (já tinha min(limit,200); confirma que ?limit gigante nunca estoura). Os demais list
# endpoints admin (agents/tools/users/roles/channels/quick-replies/custom-attributes)
# não recebem `limit` — dataset naturalmente pequeno (1 linha por entidade).
_r_atd = client.get("/api/atendimentos?limit=99999")
check("F12: /api/atendimentos?limit=99999 -> 200", _r_atd.status_code == 200)
_atd_data = _r_atd.json()["data"]
_atd_items = _atd_data.get("conversations") if isinstance(_atd_data, dict) else _atd_data
check("F12: lista de atendimentos capa o limit (<=200)",
      not isinstance(_atd_items, list) or len(_atd_items) <= 200)
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
# Sem usuário logado (instalação aberta): a mensagem manual não carrega remetente,
# então o painel cai no rótulo "Manual" (sent_by_name ausente).
_nm_last = message_repo.get_last(contact_repo.get_full_contact("5511999990001")["id"])
check("POST /send (sem usuário) -> sem sent_by_name", not _nm_last.get("sent_by_name"))

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

# ── Edit message (plano — editar mensagem) ──────────────────────────
# Provider capabilities: GOWA revokes + edits; WhatsApp Cloud edits but does NOT
# revoke (so the panel hides "Apagar" there). Drives the UI by capability.
from channels.providers.gowa_channel import GOWAChannel as _GC
_gc = _GC("default")
check("cap: GOWA revoke", _gc.capabilities.revoke is True)
check("cap: GOWA edit_message", _gc.capabilities.edit_message is True)
import importlib.util as _ilu
def _load_provider(_name, _path):
    _s = _ilu.spec_from_file_location(_name, _path)
    _m = _ilu.module_from_spec(_s); _s.loader.exec_module(_m); return _m
_src_wac_caps = plugin_source_or_skip("whatsapp_cloud", "capabilities revoke/edit")
_src_tg_caps = plugin_source_or_skip("telegram", "capabilities revoke/edit")
if _src_wac_caps and _src_tg_caps:
    _wac = _load_provider("wac_test", _src_wac_caps / "channels.py")
    _cc = _wac.WhatsAppCloudChannel("c_test", credentials={
        "access_token": "x", "phone_number_id": "y", "verify_token": "z"})
    # WhatsApp Cloud é o único canal sem apagar NEM editar (ambas as capabilities off).
    check("cap: Cloud revoke=False (hides Apagar)", _cc.capabilities.revoke is False)
    check("cap: Cloud edit_message=False (hides Editar)", _cc.capabilities.edit_message is False)
    # Telegram suporta apagar E editar (deleteMessage / editMessageText).
    _tg = _load_provider("tg_test", _src_tg_caps / "channels.py")
    _tc = _tg.TelegramChannel("t_test", credentials={"bot_token": "123:abc"})
    check("cap: Telegram revoke=True", _tc.capabilities.revoke is True)
    check("cap: Telegram edit_message=True", _tc.capabilities.edit_message is True)
    check("cap: Telegram has edit_text override", "edit_text" in type(_tc).__dict__)
    check("cap: Telegram has revoke override", "revoke" in type(_tc).__dict__)

# Edit an outgoing (assistant) text message on the default (GOWA) channel -> 200
message_repo.add(_del_cid, "assistant", "Texto original", msg_id="WAMID_EDIT_1", status="operator")
r = client.post("/api/contacts/5511999990001/messages/edit",
                json={"msg_id": "WAMID_EDIT_1", "text": "Texto editado"})
check("POST /messages/edit -> 200", r.status_code == 200)
_e = message_repo.get_by_msg_id("WAMID_EDIT_1")
check("POST /messages/edit -> content updated", bool(_e) and _e.get("content") == "Texto editado")
check("POST /messages/edit -> edited_ts set", bool(_e) and _e.get("edited_ts"))

# Edit a contact (user) message -> rejected
message_repo.add(_del_cid, "user", "Msg do contato", msg_id="WAMID_EDIT_USER")
r = client.post("/api/contacts/5511999990001/messages/edit",
                json={"msg_id": "WAMID_EDIT_USER", "text": "hack"})
check("POST /messages/edit (user msg) -> 400", r.status_code == 400)

# Edit a media message -> rejected (text only)
message_repo.add(_del_cid, "assistant", "", msg_id="WAMID_EDIT_MEDIA",
                 status="operator", media_type="image", media_path="/x.jpg")
r = client.post("/api/contacts/5511999990001/messages/edit",
                json={"msg_id": "WAMID_EDIT_MEDIA", "text": "legenda nova"})
check("POST /messages/edit (media) -> 400", r.status_code == 400)

# Missing msg_id -> 400
r = client.post("/api/contacts/5511999990001/messages/edit", json={"text": "x"})
check("POST /messages/edit (no msg_id) -> 400", r.status_code == 400)

# Empty text -> 400
r = client.post("/api/contacts/5511999990001/messages/edit",
                json={"msg_id": "WAMID_EDIT_1", "text": "   "})
check("POST /messages/edit (empty text) -> 400", r.status_code == 400)

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

# Plano 53 — contrato de identidade do payload/response da nota (dedup do painel):
# (a) default (notify_private_messages OFF): `_id` sempre presente, `msg_id` ausente
#     (a row nasce sem msg_id); autor no payload espelha a row (ausente em modo aberto).
check("POST /private-message -> sem msg_id com notify_private_messages off",
      not _pn.get("msg_id"))
check("POST /private-message -> sent_by_name espelha a row",
      _pn.get("sent_by_name") == private_notes[-1].get("sent_by_name"))

# (b) com notify_private_messages ON a nota ganha o msg_id sintético "pn:<uuid>"
#     e o response/broadcast o carregam — identidade estável imune a clock skew.
config_repo.set("notify_private_messages", True)
r = client.post("/api/contacts/5511999990001/private-message",
                json={"text": "nota com identidade pn"})
check("POST /private-message (notify on) -> 200", r.status_code == 200)
_pn2 = r.json()["data"]
check("POST /private-message (notify on) -> msg_id sintético pn:",
      str(_pn2.get("msg_id") or "").startswith("pn:"))
check("POST /private-message (notify on) -> _id presente junto do msg_id",
      bool(_pn2.get("_id")))
config_repo.set("notify_private_messages", False)
# Limpa o unread que o modo notify gera (não vazar estado p/ testes seguintes).
client.post("/api/contacts/5511999990001/read")

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

# ── Notificação de mensagens privadas (config notify_private_messages) ───────────
# Padrão OFF: uma nota privada NÃO incrementa a não-lida (ícone verde / contagem da
# aba) — preserva o comportamento legado. Ligada: acende (mesmo encanamento da
# não-lida de cliente, via unread_count + conversation_upsert). Contato isolado para
# não colidir com a não-lida deixada por outros testes.
section("Contacts — Private Message Notification")
_np_phone = "5511999990077"

_u_before = client.get("/api/contacts/unread-count").json()["data"]["count"]
r = client.post(f"/api/contacts/{_np_phone}/private-message", json={"text": "nota interna A"})
check("POST /private-message (notif OFF) -> 200", r.status_code == 200)
_u_off = client.get("/api/contacts/unread-count").json()["data"]["count"]
check("notify_private_messages OFF -> unread-count inalterado", _u_off == _u_before)

r = client.put("/api/config", json={"notify_private_messages": True})
check("PUT /api/config notify_private_messages=True -> 200", r.status_code == 200)
check("GET /api/config -> notify_private_messages persisted",
      client.get("/api/config").json()["data"].get("notify_private_messages") is True)

_u_before_on = client.get("/api/contacts/unread-count").json()["data"]["count"]
r = client.post(f"/api/contacts/{_np_phone}/private-message", json={"text": "nota interna B"})
check("POST /private-message (notif ON) -> 200", r.status_code == 200)
_pn_on = r.json()["data"]
_u_on = client.get("/api/contacts/unread-count").json()["data"]["count"]
check("notify_private_messages ON -> unread-count (aba) incrementa", _u_on == _u_before_on + 1)

# Badge VERDE por-conversa: a nota privada notificada participa do unread por-conversa
# (subquery unread_msg_ids ⋈ messages) via um msg_id sintético, então o atendimento
# mostra unread_count > 0 (mark_read=false p/ não zerar ao observar).
_conv_id = _pn_on.get("conversation_id")
_rconv = client.get(f"/api/atendimentos/{_conv_id}?mark_read=false")
check("notify ON -> atendimento GET 200", _rconv.status_code == 200)
_conv = (_rconv.json().get("data") or {}).get("conversation") or {}
check("notify ON -> badge verde por-conversa (unread_count>0)", (_conv.get("unread_count") or 0) > 0)

# Preview + subida ao topo: com a config ligada a nota privada vira a "última mensagem"
# da sidebar (conteúdo + role + ts), então a linha sobe e desenha o cadeado no front.
check("notify ON -> preview = conteúdo da nota privada",
      _conv.get("last_message") == "nota interna B")
check("notify ON -> last_message_role = 'private_note' (dispara o cadeado)",
      _conv.get("last_message_role") == "private_note")
check("notify ON -> last_message_ts avança (sobe ao topo)",
      (_conv.get("last_message_ts") or 0) > 0)

# Abrir o atendimento (GET .../messages, mark_read=True) zera o verde E a aba: o msg_id
# sintético é apagado de unread_msg_ids e o contador do contato decrementa — reusando
# mark_conversation_read, sem código de clear novo.
_rread = client.get(f"/api/atendimentos/{_conv_id}/messages")  # mark_read=True (default) → limpa
check("notify ON -> abrir atendimento (messages) retorna 200", _rread.status_code == 200)
_conv_after = (client.get(f"/api/atendimentos/{_conv_id}").json().get("data") or {}).get("conversation") or {}
check("notify ON -> verde zera após abrir o atendimento", (_conv_after.get("unread_count") or 0) == 0)
_u_read = client.get("/api/contacts/unread-count").json()["data"]["count"]
check("notify ON -> aba zera após abrir o atendimento", _u_read == _u_before_on)

# Reset da config para não afetar as checagens seguintes.
client.put("/api/config", json={"notify_private_messages": False})

# Gate do preview: com a config DESLIGADA a nota privada volta a ser pulada no preview
# da sidebar (comportamento legado) — não vira a última mensagem, então não há cadeado.
_conv_off = (client.get(f"/api/atendimentos/{_conv_id}").json().get("data") or {}).get("conversation") or {}
check("notify OFF -> preview NÃO reflete a nota privada (legado)",
      _conv_off.get("last_message_role") != "private_note")

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
# plano 64 · F12 — ids opcionais no envelope (a UI reconcilia por _localId
# quando ausentes, então isto é aditivo e não-quebrante).
_img_data = r.json().get("data") or {}
check("send-image -> _ok traz media_path",
      str(_img_data.get("media_path", "")).startswith("statics/outbox/"))
check("send-image -> _ok tem a chave msg_id", "msg_id" in _img_data)
check("send-image -> mensagem original preservada",
      _img_data.get("message") == "Imagem enviada.")

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
#  10c. Contact send video (plano 65)
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Send Video")

# GOWA (always-open) routes kind="video" to send_file — no Cloud validation.
mock_gowa_client.send_file.reset_mock()
fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
r = client.post(
    "/api/contacts/5511999990001/send-video",
    files={"video": ("clip.mp4", io.BytesIO(fake_mp4), "video/mp4")},
    data={"caption": "Vídeo teste"},
)
check("POST /send-video -> 200", r.status_code == 200)
# plano 64 · F9: o GOWA passou a ter /send/video, então o vídeo vai NATIVO
# (antes do plano 64 este caminho degradava para send_file).
check("POST /send-video -> gowa send_video chamado", mock_gowa_client.send_video.called)
_vid_call = mock_gowa_client.send_video.call_args
_vid_path = _vid_call.args[1] if len(_vid_call.args) > 1 else _vid_call.kwargs.get("video_path")
check("vídeo gravado com extensão vinda do MIME", str(_vid_path).endswith(".mp4"))
check("vídeo em disco tem entropia (plano 64 · F1)",
      re.match(r"^\d+_[0-9a-f]{8}\.mp4$", Path(_vid_path).name) is not None)
_vid_msgs = client.get("/api/contacts/5511999990001").json()["data"]["messages"]
check("vídeo persiste com media_type=video",
      any(m.get("media_type") == "video" for m in _vid_msgs))

# Video validator policy — declared BY THE PROVIDER (plano 65), never by name.
# A channel declaring VideoLimits is enforced; one declaring none never blocks;
# a windowed channel from a plugin that predates media_limits falls back to the
# legacy Cloud limits (retrocompat).
from types import SimpleNamespace as _NS
from channels import video_validate as _vv
from channels.base import VideoLimits as _VL
_declared = _NS(session_window_hours=24, media_limits={"video": _VL(
    max_bytes=16 * 1024 * 1024, extensions=(".mp4", ".3gp", ".3gpp"),
    video_codecs=("h264",), audio_codecs=("aac",), max_audio_streams=1)})
_legacy = _NS(session_window_hours=24)          # plugin sem media_limits
_open = _NS(session_window_hours=0)             # GOWA/Telegram: sem limites
check("video_limits: no declaration + always-open -> None",
      _vv.video_limits(_open) is None)
check("video_limits: no declaration + windowed -> legacy Cloud fallback",
      _vv.video_limits(_legacy) is _vv.LEGACY_CLOUD_VIDEO_LIMITS)
check("video_limits: declared wins over fallback",
      _vv.video_limits(_declared).max_bytes == 16 * 1024 * 1024)
check("validate_video: no limits -> ok (any file)",
      _vv.validate_video("/tmp/whatever.mkv", _open).ok)
check("validate_video: declared + non-mp4 -> bad_format",
      _vv.validate_video("/tmp/clip.mkv", _declared).reason == _vv.BAD_FORMAT)
check("validate_video: legacy fallback + non-mp4 -> bad_format",
      _vv.validate_video("/tmp/clip.mkv", _legacy).reason == _vv.BAD_FORMAT)
# Oversized mp4 (>16 MB) -> too_big, under both declared and fallback policy.
import tempfile as _tf, os as _os
_big = _os.path.join(_tf.gettempdir(), "wac_big_test.mp4")
with open(_big, "wb") as _fh:
    _fh.truncate(16 * 1024 * 1024 + 1)
check("validate_video: declared + >16MB -> too_big",
      _vv.validate_video(_big, _declared).reason == _vv.TOO_BIG)
check("validate_video: legacy fallback + >16MB -> too_big",
      _vv.validate_video(_big, _legacy).reason == _vv.TOO_BIG)
check("validate_video: too_big message cites the declared cap",
      "16 MB" in _vv.validate_video(_big, _declared).message)
# A provider declaring a different cap is honoured (no Meta numbers in the core).
_small = _NS(media_limits={"video": _VL(max_bytes=1024)})
check("validate_video: custom cap honoured (1 KB)",
      _vv.validate_video(_big, _small).reason == _vv.TOO_BIG)
_os.remove(_big)

# Pré-validação genérica de anexo (imagem/áudio/documento) — mesma regra
# policy-vs-mechanism: os números vêm do provider, o core só avalia/descreve.
from channels import media_limits as _ml
from channels.base import MediaLimits as _ML
_cloudish = _NS(session_window_hours=24, media_limits={
    "image": _ML(max_bytes=5 * 1024 * 1024, extensions=(".jpg", ".jpeg", ".png")),
    "document": _ML(max_bytes=100 * 1024 * 1024, extensions=(".pdf", ".txt")),
    "video": _VL(max_bytes=16 * 1024 * 1024),
})
check("media_limits: canal sem declaração -> sem limites (nada bloqueia)",
      _ml.limits_for(_open, "image") is None
      and _ml.validate_upload("x.exe", 10 ** 9, _open, "document").ok)
check("media_limits: imagem fora do formato -> bad_format",
      _ml.validate_upload("foto.gif", 10, _cloudish, "image").reason == _ml.BAD_FORMAT)
check("media_limits: imagem acima do cap -> too_big",
      _ml.validate_upload("foto.png", 6 * 1024 * 1024, _cloudish, "image").reason
      == _ml.TOO_BIG)
check("media_limits: mensagem de too_big cita o cap declarado",
      "5 MB" in _ml.validate_upload("foto.png", 6 * 1024 * 1024, _cloudish, "image").message)
check("media_limits: imagem conforme -> ok",
      _ml.validate_upload("foto.PNG", 1024, _cloudish, "image").ok)
check("media_limits: documento fora da lista -> bad_format",
      _ml.validate_upload("a.zip", 10, _cloudish, "document").reason == _ml.BAD_FORMAT)
check("media_limits: documento conforme -> ok",
      _ml.validate_upload("a.pdf", 10 * 1024 * 1024, _cloudish, "document").ok)
_hint = _NS(session_window_hours=24, media_limits={
    "document": _ML(max_bytes=100 * 1024 * 1024, extensions=(".pdf",)),
    "video": _VL(max_bytes=16 * 1024 * 1024, extensions=(".mp4",)),
    "audio": _ML(max_bytes=16 * 1024 * 1024, extensions=(".ogg",)),
})
check("media_limits: arquivo no anexo errado aponta o anexo certo (vídeo)",
      "use o anexo Vídeo" in _ml.validate_upload("c.mp4", 10, _hint, "document").message)
check("media_limits: arquivo no anexo errado aponta o anexo certo (áudio)",
      "use o anexo Áudio" in _ml.validate_upload("v.ogg", 10, _hint, "document").message)
check("media_limits: extensão que nenhum kind aceita não ganha dica",
      "use o anexo" not in _ml.validate_upload("a.zip", 10, _hint, "document").message)
check("media_limits: kind sem declaração no canal -> ok",
      _ml.validate_upload("v.ogg", 10 ** 9, _cloudish, "audio").ok)
_desc = _ml.describe(_cloudish, video_transcode_available=True)
check("media_limits.describe: expõe cap+extensões por tipo",
      _desc["image"]["max_bytes"] == 5 * 1024 * 1024
      and ".png" in _desc["image"]["extensions"])
check("media_limits.describe: video carrega o flag de transcode",
      _desc["video"]["transcode"] is True
      and _ml.describe(_cloudish)["video"]["transcode"] is False)
check("media_limits.describe: canal sem limites -> {} (painel não bloqueia nada)",
      _ml.describe(_open) == {})

# Áudio codec-aware: o provider declara os codecs por container (a Meta só aceita
# ogg com OPUS), o core valida com ffprobe e recodifica em vez de deixar falhar.
import shutil as _shutil, subprocess as _subprocess
from channels import audio_transcode as _at, audio_validate as _av
from channels.base import AudioLimits as _AL
_AUDIO = _AL(
    max_bytes=16 * 1024 * 1024,
    extensions=(".mp3", ".m4a", ".ogg"),
    codecs=("aac", "mp3"),
    codecs_by_ext=((".ogg", ("opus",)), (".mp3", ("mp3",))),
    transcode_targets=(".ogg", ".m4a"),
)
_audioish = _NS(session_window_hours=24, media_limits={"audio": _AUDIO})
check("audio_validate: canal com MediaLimits simples não opta por codec/transcode",
      _av.audio_limits(_cloudish) is None and _av.validate_audio("x.ogg", _cloudish).ok)
check("audio_validate: canal sem limites -> ok",
      _av.audio_limits(_open) is None and _av.validate_audio("x.ogg", _open).ok)
check("audio_validate: container fora da lista -> bad_format",
      _av.validate_audio("/tmp/x.wav", _audioish).reason == _av.BAD_FORMAT)
check("AudioLimits.codecs_for: regra por container, com fallback no conjunto geral",
      _AUDIO.codecs_for(".ogg") == ("opus",) and _AUDIO.codecs_for(".m4a") == ("aac", "mp3"))
check("audio_transcode: alvo é o 1º container declarado pelo provider que o core encoda",
      _at._target_ext(_AUDIO) == ".ogg"
      and _at._target_ext(_AL(extensions=(".amr",))) is None)
check("audio_transcode: bitrate cabe no cap declarado (e respeita os limites sãos)",
      _at._bitrate_kbps(60 * 60, 1024 * 1024) == _at._MIN_KBPS
      and _at._bitrate_kbps(1, 16 * 1024 * 1024) == _at._MAX_KBPS)
check("audio_transcode: provider que proíbe re-encode nunca é recodificado",
      _at.transcode_to_limits("/tmp/x.ogg", _AL(extensions=(".ogg",), transcode=False)) is None)
_desc_a = _ml.describe(_audioish, audio_transcode_available=True)
check("media_limits.describe: áudio carrega o flag de transcode",
      _desc_a["audio"]["transcode"] is True
      and _ml.describe(_audioish)["audio"]["transcode"] is False)

# Ida-e-volta real com ffmpeg (o caso que motivou tudo: Ogg/Vorbis é recusado
# pela Meta e vira Ogg/Opus). Pulado quando não há ffmpeg no ambiente.
if _at.available() and _shutil.which("ffprobe"):
    _ogg = _os.path.join(_tf.gettempdir(), "wb_test_vorbis.ogg")
    _rc = _subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-c:a", "libvorbis", _ogg],
        stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL).returncode
    if _rc == 0:
        check("audio_validate: ogg/vorbis (recusado pela Meta) -> bad_codec",
              _av.validate_audio(_ogg, _audioish).reason == _av.BAD_CODEC)
        _out = _at.transcode_to_limits(_ogg, _AUDIO)
        check("audio_transcode: ogg/vorbis vira ogg/opus e passa a validar",
              bool(_out) and _av.validate_audio(_out, _audioish).ok)
        if _out:
            _os.remove(_out)
    _os.remove(_ogg)

# ═══════════════════════════════════════════════════════════════════
#  10d. Upload hardening (plano 64 · F1/F2)
#  10d. Upload hardening (plano 64 · F1/F2)
# ═══════════════════════════════════════════════════════════════════
section("Contacts — Upload hardening (plano 64)")

# F1 — dois uploads "no mesmo instante" nunca se sobrescrevem: o nome em disco
# leva entropia própria (uuid), não só o milissegundo.
mock_gowa_client.send_image.reset_mock()
for _ in range(2):
    client.post(
        "/api/contacts/5511999990001/send-image",
        files={"image": ("mesma.png", io.BytesIO(fake_png), "image/png")},
    )
_paths = [c.args[1] if len(c.args) > 1 else c.kwargs.get("image_path")
          for c in mock_gowa_client.send_image.call_args_list]
check("2 uploads seguidos -> nomes em disco distintos",
      len(_paths) == 2 and _paths[0] != _paths[1])
check("nome em disco tem entropia (ms_uuid)",
      all(re.match(r"^\d+_[0-9a-f]{8}\.png$", Path(p).name) for p in _paths))

# F1 — a extensão vem do MIME validado, não do nome do cliente: um .html
# enviado como documento NUNCA nasce como .html em statics/outbox (XSS).
mock_gowa_client.send_file.reset_mock()
r = client.post(
    "/api/contacts/5511999990001/send-document",
    files={"document": ("payload.html", io.BytesIO(b"<script>alert(1)</script>"),
                        "text/html")},
)
check("POST /send-document (.html) -> 200", r.status_code == 200)
_doc_call = mock_gowa_client.send_file.call_args
_doc_path = _doc_call.args[1] if len(_doc_call.args) > 1 else _doc_call.kwargs.get("file_path")
check("documento .html não vira .html em disco",
      not Path(_doc_path).name.lower().endswith(".html"))
check("documento .html vira .bin em disco", Path(_doc_path).name.endswith(".bin"))
check("nome ORIGINAL preservado para o contato",
      _doc_call.kwargs.get("filename") == "payload.html")

# F2 — teto de corpo: acima do limite responde 413 antes de ler o arquivo.
_over = b"\x00" * (upload_limits.MAX_UPLOAD_BYTES + 1024)
r = client.post(
    "/api/contacts/5511999990001/send-image",
    files={"image": ("grande.png", io.BytesIO(_over), "image/png")},
)
check("upload acima do teto -> 413", r.status_code == 413)
check("413 -> envelope {ok:false,error}",
      r.json().get("ok") is False and "MB" in (r.json().get("error") or ""))
del _over

r = client.post(
    "/api/contacts/5511999990001/send-image",
    files={"image": ("ok.png", io.BytesIO(fake_png), "image/png")},
)
check("upload abaixo do teto -> segue o fluxo normal", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  10d. Serving de statics/outbox (plano 64 · F10 — XSS armazenado)
# ═══════════════════════════════════════════════════════════════════
section("Statics outbox — Content-Disposition (plano 64)")

# A pasta real usada pelo app (o Settings() do teste resolve data_dir para a
# raiz do repo), a mesma onde as rotas de envio já gravaram acima.
_outbox = app.state.deps.statics_outbox_dir
_outbox.mkdir(parents=True, exist_ok=True)
(_outbox / "legit.png").write_bytes(fake_png)
(_outbox / "evil.html").write_text("<script>alert(1)</script>")
(_outbox / "evil.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')
(_outbox / "planilha.xlsx").write_bytes(b"PK\x03\x04")

r = client.get("/statics/outbox/legit.png")
check("png legítimo -> 200 inline", r.status_code == 200)
check("png legítimo -> sem Content-Disposition", "content-disposition" not in r.headers)
check("png legítimo -> media type de imagem", r.headers.get("content-type") == "image/png")

r = client.get("/statics/outbox/evil.html")
check("html -> 200 (não some, só não executa)", r.status_code == 200)
check("html -> Content-Disposition: attachment",
      r.headers.get("content-disposition", "").startswith("attachment"))
check("html -> servido como octet-stream, não text/html",
      r.headers.get("content-type", "").startswith("application/octet-stream"))

r = client.get("/statics/outbox/evil.svg")
check("svg -> forçado a download", r.headers.get("content-disposition", "").startswith("attachment"))

r = client.get("/statics/outbox/planilha.xlsx")
check("xlsx -> forçado a download", r.headers.get("content-disposition", "").startswith("attachment"))

r = client.get("/statics/outbox/nao_existe.png")
check("arquivo inexistente -> 404", r.status_code == 404)

r = client.get("/statics/outbox/..%2F..%2Fetc%2Fpasswd")
check("path traversal -> não serve", r.status_code in (404, 400))

for _f in ("legit.png", "evil.html", "evil.svg", "planilha.xlsx"):
    (_outbox / _f).unlink(missing_ok=True)

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

# Plano 77 — an orphan custom_attribute already stored on the contact (e.g. the
# `cw_id`/`cw_identifier` leftovers from the Chatwoot migration, which have no
# definition) must NOT block the save when the panel re-sends the whole JSON.
from db.engine import get_engine as _p77_engine
from db.tables import contacts as _p77_contacts
from sqlalchemy import update as _p77_update, select as _p77_select
_p77_row = contact_repo.get_by_phone("5511999990001")
with _p77_engine().begin() as _c77:
    _c77.execute(
        _p77_update(_p77_contacts)
        .where(_p77_contacts.c.id == _p77_row["id"])
        .values(custom_attributes={"cw_id": "42", "cw_identifier": "abc"})
    )
# A genuinely new + undefined key still 400s (P50 preserved)
r = client.put("/api/contacts/5511999990001/info",
               json={"custom_attributes": {"totally_unknown_key": "x"}})
check("PUT /info (new undefined key) -> ok False (P50)", r.json().get("ok") is False)
# Re-sending the stored orphan alongside a real edit succeeds
r = client.put("/api/contacts/5511999990001/info",
               json={"name": "Alice Orphan", "custom_attributes": {"cw_id": "42"}})
check("PUT /info (stored orphan re-sent) -> 200", r.status_code == 200)
check("PUT /info (stored orphan) -> name saved",
      r.json()["data"].get("name") == "Alice Orphan")
with _p77_engine().connect() as _c77:
    _p77_saved = _c77.execute(
        _p77_select(_p77_contacts.c.custom_attributes)
        .where(_p77_contacts.c.id == _p77_row["id"])
    ).scalar()
check("PUT /info (stored orphan) -> orphan key preserved",
      (_p77_saved or {}).get("cw_id") == "42")

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
#  15a3. Sons de notificação configuráveis (plano 63)
# ═══════════════════════════════════════════════════════════════════
section("Sons de notificação (plano 63)")

# Catálogo estático
r = client.get("/api/sounds/catalog")
check("GET /api/sounds/catalog -> 200", r.status_code == 200)
_cat = r.json()["data"]
check("catalog tem 4 eventos", len(_cat["events"]) == 4)
check("catalog tem 7 sons", len(_cat["sounds"]) == 7)
check("catalog new_message notification/one-shot",
      any(e["key"] == "new_message" and e["duration_applies"] is False for e in _cat["events"]))
check("catalog ia_to_human alert/duration",
      any(e["key"] == "ia_to_human" and e["duration_applies"] is True for e in _cat["events"]))

# sound_settings exposto no GET /api/config (padrão global)
r = client.get("/api/config")
_ss = r.json()["data"].get("sound_settings")
check("GET /api/config -> sound_settings presente", isinstance(_ss, dict))
check("sound_settings master default ON", _ss.get("master_enabled") is True)
check("sound_settings new_message volume 0.6", _ss["events"]["new_message"]["volume"] == 0.6)

# PUT /api/config sound_settings — normaliza (fail-open): som inválido e volume
# fora de faixa são descartados/clampados; não corrompe.
r = client.put("/api/config", json={"sound_settings": {
    "master_enabled": False,
    "events": {"new_message": {"sound": "blip", "volume": 5, "enabled": True, "lixo": 1},
               "evento_falso": {"x": 1}},
}})
check("PUT /api/config sound_settings -> 200", r.status_code == 200)
_ss2 = client.get("/api/config").json()["data"]["sound_settings"]
check("global norm: master off", _ss2["master_enabled"] is False)
check("global norm: volume clampado a 1.0", _ss2["events"]["new_message"]["volume"] == 1.0)
check("global norm: som válido mantido", _ss2["events"]["new_message"]["sound"] == "blip")
check("global norm: evento falso descartado", "evento_falso" not in _ss2["events"])
# Restaura o padrão global para não afetar asserts seguintes
client.put("/api/config", json={"sound_settings": {
    "master_enabled": True,
    "events": {"new_message": {"enabled": True, "sound": "ding", "volume": 0.6}},
}})

# GET /api/me/sound-prefs (modo aberto → uid=None): override vazio + global + catálogo
r = client.get("/api/me/sound-prefs")
check("GET /api/me/sound-prefs -> 200", r.status_code == 200)
_sp = r.json()["data"]
check("me/sound-prefs: prefs esparso vazio", _sp["prefs"] == {})
check("me/sound-prefs: global_default com eventos", bool(_sp["global_default"]["events"]))
check("me/sound-prefs: catálogo embutido", len(_sp["catalog"]["events"]) == 4)

# PUT override esparso (uid=None) — junk descartado, volume clampado
r = client.put("/api/me/sound-prefs", json={"prefs": {
    "events": {"mention": {"sound": "chime", "volume": -3, "enabled": False, "nope": 1},
               "ia_to_human": {"volume": 0.4, "duration": 999},
               "bogus": {"z": 1}},
}})
check("PUT /api/me/sound-prefs -> 200", r.status_code == 200)
_saved = r.json()["data"]["prefs"]
check("user override: volume clampado a 0.0", _saved["events"]["mention"]["volume"] == 0.0)
check("user override: duração clampada a 30", _saved["events"]["ia_to_human"]["duration"] == 30)
check("user override: evento inválido descartado", "bogus" not in _saved["events"])
check("user override: esparso (sem new_message)", "new_message" not in _saved["events"])

# GET persiste o override (uid=None)
r = client.get("/api/me/sound-prefs")
check("GET me/sound-prefs -> override persistido",
      r.json()["data"]["prefs"]["events"]["mention"]["sound"] == "chime")

# PUT com prefs não-dict → fail-open para {}
r = client.put("/api/me/sound-prefs", json={"prefs": "lixo"})
check("PUT me/sound-prefs (não-dict) -> {} fail-open", r.json()["data"]["prefs"] == {})

# Repo unit: caminho uid REAL (ON CONFLICT) + isolamento entre usuários
from db.repositories import user_sound_pref_repo as _uspr
_uspr.upsert(4242, {"master_enabled": False, "events": {"new_message": {"volume": 0.1}}})
check("repo uid real: get devolve override",
      _uspr.get(4242)["events"]["new_message"]["volume"] == 0.1)
_uspr.upsert(4242, {"events": {"new_message": {"volume": 0.9}}})  # overwrite (ON CONFLICT)
check("repo uid real: upsert sobrescreve",
      _uspr.get(4242)["events"]["new_message"]["volume"] == 0.9)
check("repo isolamento: outro uid sem override", _uspr.get(4343) is None)

from server import sound_catalog as _sound_catalog

# ── Biblioteca de sons importados (aba "Sons") ─────────────────────────────────
# Qualquer atendente logado importa; só ÁUDIO e no máximo 1 MB. O arquivo vai para
# statics/sounds/<gerado> — o nome do upload nunca é usado no disco.
r = client.get("/api/sounds/library")
check("GET /api/sounds/library -> 200 lista", r.status_code == 200 and r.json()["data"] == [])

_wav = b"RIFF" + b"\x00" * 4 + b"WAVEfmt " + b"\x00" * 32   # header WAV mínimo
r = client.post("/api/sounds/library",
                files={"file": ("meu-som.wav", _wav, "audio/wav")},
                data={"name": "Sino da recepção"})
check("POST /api/sounds/library -> 200", r.status_code == 200 and r.json()["ok"])
_snd = r.json()["data"]
_snd_num = int(_snd["id"].split(":")[1])
check("som importado -> id custom:<n>", _snd["id"].startswith("custom:"))
check("som importado -> nome escolhido", _snd["label"] == "Sino da recepção")
check("som importado -> cls any (serve p/ alerta e one-shot)", _snd["cls"] == "any")
check("som importado -> url em /statics/sounds/", _snd["url"].startswith("/statics/sounds/"))
check("som importado -> arquivo renomeado (não usa o nome do upload)",
      "meu-som" not in _snd["url"])

# Recusa o que não é áudio (extensão fora da lista) e o que passa do teto.
r = client.post("/api/sounds/library", files={"file": ("virus.exe", b"MZ\x90\x00", "application/x-msdownload")})
check("import .exe -> recusado", r.status_code == 400 and r.json()["ok"] is False)
r = client.post("/api/sounds/library", files={"file": ("falso.mp3", b"MZ\x90\x00nao-e-audio", "application/octet-stream")})
check("import extensão de áudio com conteúdo não-áudio -> recusado", r.json()["ok"] is False)
r = client.post("/api/sounds/library", files={"file": ("grande.wav", b"RIFF" + b"\x00" * (1024 * 1024 + 64), "audio/wav")})
check("import acima de 1 MB -> recusado", r.json()["ok"] is False)
check("import recusado -> nada gravado",
      len(client.get("/api/sounds/library").json()["data"]) == 1)

# O catálogo passa a oferecer o som importado junto dos sintetizados.
_cat2 = client.get("/api/sounds/catalog").json()["data"]
check("catálogo inclui o som importado",
      any(s["id"] == _snd["id"] and s["kind"] == "file" for s in _cat2["sounds"]))
# E o catálogo embutido em /api/me/sound-prefs também — é POR ELE que a tela
# (seletor + lista de importados) e o motor de som carregam. Sem isso o som
# importado não aparece em lugar nenhum nem toca (regressão corrigida).
_cat3 = client.get("/api/me/sound-prefs").json()["data"]["catalog"]
check("me/sound-prefs: catálogo inclui o som importado",
      any(s["id"] == _snd["id"] and s.get("kind") == "file" for s in _cat3["sounds"]))

# Uma preferência pode apontar para o som importado (normalize aceita custom:<n>).
r = client.put("/api/me/sound-prefs", json={"prefs": {"events": {"new_message": {"sound": _snd["id"]}}}})
check("preferência aceita som importado",
      r.json()["data"]["prefs"]["events"]["new_message"]["sound"] == _snd["id"])
check("normalize recusa custom: malformado",
      _sound_catalog.is_valid_sound_id("custom:abc") is False
      and _sound_catalog.is_valid_sound_id("custom:7") is True)

r = client.put(f"/api/sounds/library/{_snd_num}", json={"name": "Sino novo"})
check("PUT renomeia o som", r.json()["data"]["label"] == "Sino novo")

r = client.delete(f"/api/sounds/library/{_snd_num}")
check("DELETE remove o som", r.status_code == 200 and r.json()["ok"])
check("DELETE -> biblioteca vazia", client.get("/api/sounds/library").json()["data"] == [])
check("DELETE de som inexistente -> 404", client.delete("/api/sounds/library/99999").status_code == 404)
# A preferência que apontava para o som excluído NÃO é reescrita (o motor cai no
# som padrão do evento) — fail-open, sem varredura de preferências.
check("preferência órfã sobrevive ao delete",
      client.get("/api/me/sound-prefs").json()["data"]["prefs"]["events"]["new_message"]["sound"]
      == _snd["id"])
client.put("/api/me/sound-prefs", json={"prefs": {}})   # limpa p/ os asserts seguintes

# ── Permissão de escrita POR CHAVE (abas de Configurações Gerais) ──────────────
from config.settings import config_key_permission as _ckp
check("chave de avisos -> settings.general", _ckp("system_notice_tags") == "settings.general")
check("chave de avançado -> settings.advanced", _ckp("audit_retention_days") == "settings.advanced")
check("notas privadas -> settings.notifications",
      _ckp("notify_private_messages") == "settings.notifications")
check("padrão de som -> settings.notifications", _ckp("sound_settings") == "settings.notifications")
check("chave sem aba -> só settings.manage", _ckp("openrouter_api_key") is None)

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

# Plano 59 — opções do filtro "Canais" vêm do banco (endpoint leve, sem creds).
r = client.get("/api/channels/for-filter")
check("GET /channels/for-filter -> 200", r.status_code == 200)
_ff = r.json()["data"]
check("GET /channels/for-filter -> is list", isinstance(_ff, list))
_ff_default = next((c for c in _ff if c["id"] == "default"), None)
check("GET /channels/for-filter -> inclui 'default'", _ff_default is not None)
check("GET /channels/for-filter -> só id/provider/display_name",
      _ff_default is not None and set(_ff_default.keys()) == {"id", "provider", "display_name"})
check("GET /channels/for-filter -> sem credentials", all("credentials" not in c for c in _ff))

# plano 50 F13 — status-batch: UMA request cobre N canais.
r = client.post("/api/channels/status-batch", json={"ids": ["default", "inexistente"]})
check("F13: channels status-batch -> 200", r.status_code == 200)
_sb = r.json()["data"]["status_by_id"]
check("F13: status-batch traz o canal existente", "default" in _sb)
check("F13: status-batch ignora canal inexistente", "inexistente" not in _sb)
check("F13: status-batch ids não-lista -> erro",
      client.post("/api/channels/status-batch", json={"ids": 5}).json().get("ok") is False)

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
    "credentials": {"access_token": "EAAtokenSecreto123",
                    "app_secret": "appSecretCloudTeste",
                    "phone_number_id": "55123",
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
r = client.post("/api/channels", json={
    "provider": "whatsapp_cloud", "display_name": "Auto Cloud",
    "credentials": {"access_token": "tokAuto", "app_secret": "secAuto",
                    "phone_number_id": "PN_AUTO_CLOUD",
                    "verify_token": "vtAuto"}})
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
# Criar um usuário liga o gate has_users (plano 48 F0): a partir daqui as rotas
# channel.manage exigem uma sessão de admin. O bootstrap do admin da suíte só
# acontece na seção RBAC adiante, então autenticamos estas chamadas com uma
# sessão de admin DESCARTÁVEL, removida no fim junto com o membro — o intervalo
# seguinte (delete/restore de canais) volta a rodar em modo aberto (0 usuários).
from db.repositories import user_repo as _urepo
from server.auth import hash_password_argon2 as _hpa_mem
_u = _urepo.create(email="agente_inbox@x.com", name="Agente Inbox",
                   password_hash="x", role_keys=["atendente"])
_mem_admin = _urepo.create(email="_memtest_admin@x.com", name="MemAdmin",
                           password_hash=_hpa_mem("supersecret"), role_keys=["admin"])
_mem_tok = client.post("/api/auth/login",
                       json={"email": "_memtest_admin@x.com", "password": "supersecret"}
                       ).json()["data"]["token"]
_mem_h = {"Authorization": f"Bearer {_mem_tok}"}
r = client.put("/api/channels/default/members", json={"user_ids": [_u["id"]]}, headers=_mem_h)
check("PUT members -> 200", r.status_code == 200)
check("PUT members -> persiste o membro", _u["id"] in r.json()["data"]["member_ids"])
r = client.get("/api/channels/default/members", headers=_mem_h)
check("GET members -> reflete o membro salvo", _u["id"] in r.json()["data"]["member_ids"])
r = client.put("/api/channels/default/members", json={"user_ids": []}, headers=_mem_h)
check("PUT members -> esvazia o conjunto", r.json()["data"]["member_ids"] == [])
r = client.put("/api/channels/default/members", json={"user_ids": "nope"}, headers=_mem_h)
check("PUT members tipo inválido -> 400", r.status_code == 400)
# PUT sem mudança real na lista não gera linha de auditoria (o form de edição do
# canal chama /members em todo salvamento — ex.: só desligar a IA do canal).
import plugins.events as _mem_bus  # noqa: E402
_mem_events = []
_mem_listener = lambda name, payload: (  # noqa: E731
    _mem_events.append(payload) if name == "channel.members_changed" else None)
_mem_bus.register_core_sync_listener(_mem_listener)
client.put("/api/channels/default/members", json={"user_ids": [_u["id"]]}, headers=_mem_h)
_mem_events.clear()
r = client.put("/api/channels/default/members", json={"user_ids": [_u["id"]]}, headers=_mem_h)
check("PUT members sem mudança -> 200", r.status_code == 200)
check("PUT members sem mudança não emite channel.members_changed", _mem_events == [])
client.put("/api/channels/default/members", json={"user_ids": []}, headers=_mem_h)
check("PUT members com mudança emite channel.members_changed", len(_mem_events) == 1)
check("evento de members carrega antes != depois",
      _mem_events and _mem_events[0]["_audit_before"] != _mem_events[0]["_audit_after"])
_mem_bus._core_sync_listeners.remove(_mem_listener)
_urepo.delete(_u["id"])
_urepo.delete(_mem_admin["id"])  # volta a 0 usuários (gate aberto) para o resto da seção

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

# O canal 'default' agora É removível (plano exclui-default) — o teste destrutivo
# (arquivar/purgar o default + estado zero-canais + recriar) roda no FIM da suíte,
# depois de todos os testes que ainda dependem do canal/inbox default.
# Soft-delete: arquiva, preserva credenciais/histórico, some da lista.
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
    "id": "sync_ch", "provider": "whatsapp_cloud", "display_name": "Nome Antigo",
    "credentials": {"access_token": "tokSync", "app_secret": "secSync",
                    "phone_number_id": "PN_SYNC", "verify_token": "vtSync"}})
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
check("RBAC seed -> 40 permissions", _perm_count == 40)
check("RBAC seed -> role_permissions populated (gestor 35 + atendente 5)", _rp_count == 40)
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
check("admin me -> all 40 permissions", len([p for p in _perms if p != "*"]) == 40)

r = client.get("/api/auth/check", headers={"Authorization": f"Bearer {_utok}"})
check("GET /auth/check (user session) -> authenticated",
      r.json()["data"]["authenticated"] is True)

# ── Self-service password change (plano 47) — POST /api/me/password ──
_meh = {"Authorization": f"Bearer {_utok}"}
r = client.post("/api/me/password",
                json={"current_password": "wrongpw", "new_password": "newsecret123"}, headers=_meh)
check("POST /me/password (wrong current) -> 400", r.status_code == 400)
r = client.post("/api/me/password",
                json={"current_password": "supersecret", "new_password": "short"}, headers=_meh)
check("POST /me/password (new too short) -> 400", r.status_code == 400)
r = client.post("/api/me/password",
                json={"current_password": "supersecret", "new_password": "supersecret"}, headers=_meh)
check("POST /me/password (same as current) -> 400", r.status_code == 400)
r = client.post("/api/me/password",
                json={"current_password": "supersecret", "new_password": "newsecret123"}, headers=_meh)
check("POST /me/password (valid) -> 200", r.status_code == 200)
# Old password no longer authenticates; the new one does.
r = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "supersecret"})
check("login old password after self-change -> 401", r.status_code == 401)
r = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "newsecret123"})
check("login new password after self-change -> 200", r.status_code == 200)
# No RBAC identity (no token) -> the middleware rejects with 401 before the handler
# runs (plano 48 F0: the API gate is closed because ≥1 user exists). Uses `anon`
# since `client` is about to carry a default admin header.
r = anon.post("/api/me/password",
              json={"current_password": "newsecret123", "new_password": "another12345"})
check("POST /me/password (no session) -> 401", r.status_code == 401)
# The session token stays valid across a password change (opaque, not derived).
r = client.get("/api/auth/me", headers=_meh)
check("session valid after self password change", r.status_code == 200)
# Restore the admin password so downstream expectations hold.
r = client.post("/api/me/password",
                json={"current_password": "newsecret123", "new_password": "supersecret"}, headers=_meh)
check("POST /me/password (restore) -> 200", r.status_code == 200)

# Logout invalidates the session
r = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {_utok}"})
check("POST /auth/logout -> 200", r.status_code == 200)
r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_utok}"})
check("GET /auth/me (after logout) -> 401", r.status_code == 401)

# ── From here on, the suite runs authenticated as a full admin BY DEFAULT ──────
# The API gate is now closed (an admin exists → has_users, plano 48 F0), so every
# tokenless `client.*` call would 401. We attach a DEDICATED admin session as the
# default Authorization header — deliberately NOT `_utok` (just logged out above)
# — so it stays valid through the rest of the suite. Requests that pass their own
# `headers={Authorization: ...}` still override this; "no token" cases use `anon`.
_suite_admin_tok = client.post(
    "/api/auth/login", json={"email": "admin@test.com", "password": "supersecret"}
).json()["data"]["token"]
client.headers["Authorization"] = f"Bearer {_suite_admin_tok}"

# Resolver + short-circuit (gestor via repo, since user-management UI is Fase 5)
from db.repositories import user_repo as _urepo, rbac_repo as _rrepo
from server.auth import hash_password_argon2 as _hpa
_g = _urepo.create(email="gestor@test.com", name="G",
                   password_hash=_hpa("supersecret"), role_keys=["gestor"])
_gperms = _rrepo.user_permissions(_g["id"])
check("gestor resolver -> 35 perms, no '*'", "*" not in _gperms and len(_gperms) == 35)
check("gestor lacks users.manage", "users.manage" not in _gperms)
check("gestor has template.create/template.delete",
      {"template.create", "template.delete"} <= _gperms)
check("admin resolver -> short-circuit '*'", "*" in _rrepo.user_permissions(_admin["id"]))

# ── Users CRUD + permission gating (Fases 4-5) ─────────────────────
r = client.get("/api/roles")
check("GET /api/roles -> 200", r.status_code == 200)
check("GET /api/roles -> 3 roles + 40 perms",
      len(r.json()["data"]["roles"]) == 3 and len(r.json()["data"]["permissions"]) == 40)

r = client.get("/api/users")
check("GET /api/users (admin session) -> 200", r.status_code == 200)
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
check("GET /api/ai/agents (no agent.config.manage) -> 403", r.status_code == 403)
r = client.get("/api/ai/variables", headers=_chdr)
check("GET /api/ai/variables (no agent.variables.manage) -> 403", r.status_code == 403)
r = client.get("/api/ai/tools", headers=_chdr)
check("GET /api/ai/tools (no agent.tools.manage) -> 403", r.status_code == 403)
r = client.put("/api/ai/agents/default/prompt", json={"prompt": "x"}, headers=_chdr)
check("PUT /api/ai/agents/default/prompt (no agent.prompts.edit) -> 403", r.status_code == 403)
r = client.get("/api/ai/agents/default/prompt/history", headers=_chdr)
check("GET /api/ai/agents/{k}/prompt/history (no agent.prompts.version) -> 403", r.status_code == 403)
r = client.get("/api/ai/agents/default/history", headers=_chdr)
check("GET /api/ai/agents/{k}/history (no agent.prompts.version) -> 403", r.status_code == 403)
r = client.post("/api/ai/agents/default/rollback/1", headers=_chdr)
check("POST /api/ai/agents/{k}/rollback (no agent.prompts.version) -> 403", r.status_code == 403)
r = client.delete("/api/ai/agents/default/prompt/history/1", headers=_chdr)
check("DELETE /api/ai/agents/{k}/prompt/history/{v} (no agent.prompts.delete) -> 403", r.status_code == 403)
r = client.put("/api/ai/agents/rbac_new_agent", json={"display_name": "X"}, headers=_chdr)
check("PUT /api/ai/agents/{new} create (no agent.create) -> 403", r.status_code == 403)
r = client.put("/api/ai/agents/rbac_dup_agent",
               json={"display_name": "X", "duplicate": True}, headers=_chdr)
check("PUT /api/ai/agents/{new} duplicate (no agent.duplicate) -> 403", r.status_code == 403)
r = client.get("/api/tools", headers=_chdr)
check("GET /api/tools (no agent.tools.manage) -> 403", r.status_code == 403)
r = client.post("/api/contacts/import",
                files={"file": ("c.csv", "phone\n5511999\n", "text/csv")}, headers=_chdr)
check("POST /api/contacts/import (no contact.import) -> 403", r.status_code == 403)

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
      "permission_keys" in _by_key["gestor"] and len(_by_key["gestor"]["permission_keys"]) == 35)
check("GET /api/roles -> admin shows all 40",
      len(_by_key["admin"]["permission_keys"]) == 40)

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
check("gestor restored to 35 perms", len(_rrepo.get_role_permissions("gestor")) == 35)

# ── RBAC para plugins (plano "RBAC para Plugins") ──────────────────
import asyncio as _asyncio
import types as _types
from plugins.manifest import _parse_rbac as _parse_rbac
from plugins.context import plugin_permission as _plugin_permission, core_permission as _core_permission
from plugins import events as _events
from server import authz as _authz
from server.deps import PermissionDeniedError as _PermissionDeniedError
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
# O plugin precisa existir ATIVO para suas permissões aparecerem no catálogo
# (list_catalog esconde permissões de plugins desativados/ausentes).
from db.repositories import plugin_repo as _prepo
_prepo.upsert("lembretes", "1.0.0", enabled=True)
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
# Agrupamento core/plugin (metadado de exibição): tier + group em cada item.
_cat_by_key = {c["key"]: c for c in _catalog}
check("list_catalog -> core perm tier=core + group",
      _cat_by_key["conversation.read"]["tier"] == "core"
      and _cat_by_key["conversation.read"]["group"] == "Atendimentos e conversas")
check("list_catalog -> AI perms under 'IA e agente'",
      _cat_by_key["agent.config.manage"]["group"] == "IA e agente"
      and _cat_by_key["agent.prompts.edit"]["tier"] == "core")
check("list_catalog -> granular prompt perms present under 'IA e agente'",
      all(k in _cat_by_key and _cat_by_key[k]["group"] == "IA e agente"
          for k in ("agent.prompts.edit", "agent.prompts.version", "agent.prompts.delete")))
check("list_catalog -> agent.create/duplicate present under 'IA e agente'",
      all(k in _cat_by_key and _cat_by_key[k]["group"] == "IA e agente"
          for k in ("agent.create", "agent.duplicate")))
check("list_catalog -> agent.prompts.manage removido do catálogo",
      "agent.prompts.manage" not in _cat_by_key)
check("list_catalog -> templates shown under Plugins tier",
      _cat_by_key["template.create"]["tier"] == "plugin"
      and _cat_by_key["template.create"]["group"] == "Templates (WhatsApp Cloud)")
check("list_catalog -> plugin perm carries tier=plugin",
      _cat_view["tier"] == "plugin" and _cat_view["group"] == "Lembretes")
check("list_catalog -> agent.manage removido do catálogo",
      "agent.manage" not in _cat_by_key)

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

# 4b) Desativar o plugin ESCONDE suas permissões do picker mas PRESERVA os grants
# (sobrevive ao ciclo desativar→editar→reativar). Ver rbac_repo.list_catalog +
# set_role_permissions (hidden_plugin_permission_keys).
r = client.post("/api/roles", json={"key": "lembrete_ops", "name": "Lembrete Ops",
    "permission_keys": ["plugin.lembretes.view", "conversation.read"]})
_lo_id = r.json()["data"]["role"]["id"]
_prepo.set_enabled("lembretes", False)
_cat_off = {c["key"] for c in _rrepo.list_catalog()}
check("plugin desativado -> permissões somem do catálogo",
      "plugin.lembretes.view" not in _cat_off and "plugin.lembretes.delete" not in _cat_off)
check("plugin desativado -> chave ainda válida (grants persistem)",
      "plugin.lembretes.view" in _rrepo.plugin_permission_keys())
# Editar o cargo com o plugin OFF (picker manda só o que vê) NÃO apaga o grant escondido.
r = client.put(f"/api/roles/{_lo_id}", json={"permission_keys": ["conversation.read"]})
check("editar cargo com plugin off -> grant escondido preservado",
      "plugin.lembretes.view" in _rrepo.get_role_permissions("lembrete_ops"))
# Idem para usuário custom: editar sem ver o plugin preserva o grant escondido.
_puo = _urepo.create(email="lembreteops@test.com", name="LO",
                     password_hash=_hpa("supersecret"),
                     permission_keys=["plugin.lembretes.view"], custom=True)
_urepo.set_custom_permissions(_puo["id"], ["conversation.read"])
check("editar usuário custom com plugin off -> grant escondido preservado",
      "plugin.lembretes.view" in _rrepo.user_permissions(_puo["id"]))
client.delete(f"/api/users/{_puo['id']}")
# Reativar traz a permissão de volta ao catálogo, com o grant intacto.
_prepo.set_enabled("lembretes", True)
_cat_on = {c["key"] for c in _rrepo.list_catalog()}
check("reativar plugin -> permissão volta ao catálogo", "plugin.lembretes.view" in _cat_on)
check("reativar plugin -> grant continua no cargo",
      "plugin.lembretes.view" in _rrepo.get_role_permissions("lembrete_ops"))
client.delete(f"/api/roles/{_lo_id}")

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
# logged-in user WITHOUT the perm -> raises PermissionDeniedError (rendered as the
# unified {"ok": false, "error": "Permissão negada."} 403 envelope by the app handler).
_denied = False
try:
    _asyncio.run(_dep(_freq(user={"id": _pu_id})))
except _PermissionDeniedError:
    _denied = True
check("plugin_permission -> user without perm -> 403", _denied)
# grant the perm to that user (custom) -> allowed
_urepo.set_custom_permissions(_pu_id, ["plugin.lembretes.delete"])
_asyncio.run(_dep(_freq(user={"id": _pu_id})))
check("plugin_permission -> user with perm -> allowed", True)
# non-plugin path -> cannot infer id -> allowed (no raise)
_asyncio.run(_dep(_freq(user={"id": _pu_id}, path="/api/contacts")))
check("plugin_permission -> non-plugin path allowed", True)

# 5b) core_permission() dependency (plano 81): gates a plugin route on a LITERAL
# core catalog key (channel.manage), NOT plugin.<id>.<key>. Used by the channel
# providers (gowa/telegram/whatsapp_cloud/website) so the same permission that
# governs the core Channels screen also governs each provider's config endpoints.
_cdep = _core_permission("channel.manage").dependency
# legacy/open (no user) -> allowed (no raise)
_asyncio.run(_cdep(_freq(user=None)))
check("core_permission -> legacy/open allowed", True)
# logged-in user WITHOUT channel.manage -> PermissionDeniedError (unified 403 envelope)
_cpu = _urepo.create(email="corepu@test.com", name="CorePU",
                     password_hash=_hpa("supersecret"), role_keys=["atendente"])
_cpu_id = _cpu["id"]
_cdenied = False
try:
    _asyncio.run(_cdep(_freq(user={"id": _cpu_id})))
except _PermissionDeniedError:
    _cdenied = True
check("core_permission -> user without channel.manage -> 403", _cdenied)
# grant channel.manage (custom) -> allowed
_urepo.set_custom_permissions(_cpu_id, ["channel.manage"])
_asyncio.run(_cdep(_freq(user={"id": _cpu_id})))
check("core_permission -> user with channel.manage -> allowed", True)
_urepo.delete(_cpu_id)  # cleanup

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
_src_protocolos = plugin_source_or_skip("protocolos", "Kanban Views + atendimentos")
if _src_protocolos:
    section("Protocolos Kanban Views")
    import importlib.util as _ilu
    from plugins.manifest import load_manifest as _load_manifest, _parse_yaml as _parse_yaml
    from plugins.migrator import run_pending_migrations as _run_pending
    from tests.plugin_test_utils import resolve_plugin_source as _resolve_plugin_source

    _atd_dir = _src_protocolos

    # 1) Manifest declara a permissão nova manage_team_views (aparece no PermissionPicker).
    _atd_yaml = _parse_yaml((_atd_dir / "plugin.yaml").read_text(encoding="utf-8"))
    _atd_rbac = _parse_rbac(_atd_yaml.get("rbac"), "protocolos")
    check("protocolos rbac -> manage_team_views declarada",
          any(p["key"] == "manage_team_views" for p in _atd_rbac.get("permissions", [])))
    check("protocolos rbac -> create_views declarada",
          any(p["key"] == "create_views" for p in _atd_rbac.get("permissions", [])))

    # 2) Aplica as migrations do plugin no DB de teste (cria plugin_protocolos_* incl. 006).
    _atd_manifest = _load_manifest(_atd_dir)
    _run_pending(_atd_manifest, _atd_dir)
    # Carrega o plugin como PACOTE (igual ao runtime, que registra `whatsbot_plugins.<id>`):
    # logic.py usa imports RELATIVOS (`from . import kanban_index`), que só resolvem dentro de
    # um pacote. O __path__ sintético aponta para a pasta do plugin.
    import importlib as _il
    import sys as _sys
    import types as _types
    _APKG = "protocolos_pkg_test"
    if _APKG not in _sys.modules:
        _apkg = _types.ModuleType(_APKG)
        _apkg.__path__ = [str(_atd_dir)]
        _sys.modules[_APKG] = _apkg
    _alogic_spec = _ilu.spec_from_file_location(f"{_APKG}.logic", _atd_dir / "logic.py")
    _alogic = _ilu.module_from_spec(_alogic_spec)
    _sys.modules[f"{_APKG}.logic"] = _alogic   # antes do exec: o pacote precisa se auto-resolver
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

    # 3) CRUD + validação. Agrupar por CAMPO DE PROTOCOLO (pfield) usa os field-defs do plugin
    # (defaults: motivo_abertura/resultado = select). Atributo de conversa NÃO agrupa mais.
    _v1, _e1 = _alogic.create_kanban_view(name="Por motivo", scope="personal", group_by="pfield",
                                          group_field_scope="protocolo", group_attr_key="motivo_abertura",
                                          filters={"status": "aberto"}, owner_user_id=101)
    check("create view pessoal (pfield) -> ok", _e1 is None and bool(_v1 and _v1.get("id")))
    check("create view -> filters round-trip dict", _v1 and _v1.get("filters") == {"status": "aberto"})
    check("pfield -> round-trip scope+key",
          _v1.get("group_by") == "pfield" and _v1.get("group_field_scope") == "protocolo"
          and _v1.get("group_attr_key") == "motivo_abertura")
    # favorite_filters: default None; create com lista faz round-trip; update com _UNSET preserva,
    # None limpa. Espelha o modelo de available_filters.
    check("create view -> favorite_filters default None", _v1.get("favorite_filters") is None)
    _vf, _evf = _alogic.create_kanban_view(name="Favoritos", scope="personal", group_by="status",
                                           available_filters=["status", "atendente", "periodo"],
                                           favorite_filters=["status", "periodo"], owner_user_id=101)
    check("create view -> favorite_filters round-trip",
          _evf is None and _vf.get("favorite_filters") == ["status", "periodo"])
    _vf2, _evf2 = _alogic.update_kanban_view(_vf["id"], name="Favoritos v2")
    check("update sem favorite_filters -> preserva (_UNSET)",
          _evf2 is None and _vf2.get("favorite_filters") == ["status", "periodo"])
    _vf3, _evf3 = _alogic.update_kanban_view(_vf["id"], favorite_filters=None)
    check("update favorite_filters=None -> limpa", _evf3 is None and _vf3.get("favorite_filters") is None)
    _alogic.delete_kanban_view(_vf["id"])
    _v2, _e2 = _alogic.create_kanban_view(name="Equipe vendas", scope="team", group_by="data",
                                          group_date_mode="mes", owner_user_id=101)
    check("create view equipe -> ok", _e2 is None and bool(_v2 and _v2.get("id")))
    _, _ev_pf = _alogic.create_kanban_view(name="x", group_by="pfield",
                                           group_field_scope="protocolo", group_attr_key="", owner_user_id=1)
    check("validação: pfield sem campo -> erro", _ev_pf is not None)
    _, _ev_pf2 = _alogic.create_kanban_view(name="x2", group_by="pfield",
                                            group_field_scope="protocolo", group_attr_key="obs", owner_user_id=1)
    check("validação: pfield campo não-opção (obs) -> erro", _ev_pf2 is not None)
    _, _ev_date = _alogic.create_kanban_view(name="y", group_by="data", group_date_mode="bad", owner_user_id=1)
    check("validação: data mode inválido -> erro", _ev_date is not None)

    # 3b) Novos modos de data: "semana" e "personalizado" (janela de/até + granularidade).
    _vw, _ewk = _alogic.create_kanban_view(name="Por semana", scope="team", group_by="data",
                                           group_date_mode="semana", owner_user_id=101)
    check("create view data 'semana' -> ok", _ewk is None and bool(_vw and _vw.get("id")))
    _vp, _epc = _alogic.create_kanban_view(name="Período custom", scope="team", group_by="data",
                                           group_date_mode="personalizado", group_date_from="2026-06-01",
                                           group_date_to="2026-06-30", group_date_grain="semana",
                                           owner_user_id=101)
    check("create view data 'personalizado' -> ok", _epc is None and bool(_vp and _vp.get("id")))
    check("personalizado -> round-trip from/to/grain",
          _vp and _vp.get("group_date_from") == "2026-06-01" and _vp.get("group_date_to") == "2026-06-30"
          and _vp.get("group_date_grain") == "semana")
    _, _ep_range = _alogic.create_kanban_view(name="p-range", group_by="data",
                                              group_date_mode="personalizado", group_date_from="2026-06-30",
                                              group_date_to="2026-06-01", group_date_grain="dia", owner_user_id=1)
    check("validação: personalizado from > to -> erro", _ep_range is not None)
    _, _ep_grain = _alogic.create_kanban_view(name="p-grain", group_by="data",
                                              group_date_mode="personalizado", group_date_from="2026-06-01",
                                              group_date_to="2026-06-30", group_date_grain="ano", owner_user_id=1)
    check("validação: personalizado granularidade inválida -> erro", _ep_grain is not None)
    _, _ep_nodate = _alogic.create_kanban_view(name="p-nodate", group_by="data",
                                               group_date_mode="personalizado", group_date_grain="dia", owner_user_id=1)
    check("validação: personalizado sem datas -> erro", _ep_nodate is not None)
    # Modo não-personalizado NÃO persiste janela (from/to/grain são limpos → NULL).
    _vn, _evn = _alogic.create_kanban_view(name="Só mês", scope="team", group_by="data",
                                           group_date_mode="mes", group_date_from="2026-06-01",
                                           group_date_to="2026-06-30", group_date_grain="dia", owner_user_id=101)
    check("modo não-personalizado -> janela ignorada (NULL)",
          _evn is None and _vn and _vn.get("group_date_from") is None and _vn.get("group_date_grain") is None)
    # update de personalizado -> mês limpa a janela persistida.
    _vp2, _eup = _alogic.update_kanban_view(_vp["id"], group_date_mode="mes")
    check("update personalizado -> mês limpa janela",
          _eup is None and _vp2 and _vp2.get("group_date_from") is None and _vp2.get("group_date_grain") is None)

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

    # 6) set_protocolo_field: erros + gravação num campo de opção do protocolo (drag do Kanban).
    _, _esa = _alogic.set_protocolo_field(99999, "protocolo", "motivo_abertura", "Dúvida")
    check("set_protocolo_field -> protocolo inexistente -> erro", _esa is not None)
    _, _esa2 = _alogic.set_protocolo_field(99999, "protocolo", "obs", "x")
    check("set_protocolo_field -> campo não-opção -> erro", _esa2 is not None)
    _proto_sf = _alogic.ensure_protocolo_for_contact(70001, phone="5511999990001", name="Cliente Teste")
    _pw, _pwe = _alogic.set_protocolo_field(_proto_sf["id"], "protocolo", "motivo_abertura", "Dúvida")
    check("set_protocolo_field -> grava valor de opção",
          _pwe is None and _pw and (_pw.get("fields") or {}).get("motivo_abertura") == "Dúvida")

    # 7) attr_filters namespaceados: pf:<scope>:<key> (campo de protocolo) + cattr:<key> (contato).
    _lf = _alogic.list_protocolos(attr_filters={"pf:protocolo:motivo_abertura": "Dúvida"}, limit=50)
    check("list_protocolos(pf filter) -> acha o protocolo", any(a["id"] == _proto_sf["id"] for a in _lf["items"]))
    _lf2 = _alogic.list_protocolos(attr_filters={"pf:protocolo:motivo_abertura": "Reclamação"}, limit=50)
    check("list_protocolos(pf filter) -> exclui valor diferente",
          all(a["id"] != _proto_sf["id"] for a in _lf2["items"]))
    _lf3 = _alogic.list_protocolos(attr_filters={"cattr:qualquer": "x"}, limit=50)
    check("list_protocolos(cattr filter) -> envelope {items,total,has_more}",
          isinstance(_lf3, dict) and isinstance(_lf3.get("items"), list)
          and "total" in _lf3 and "has_more" in _lf3)
    # O filtro nativo "Atendente" passa a casar o EFETIVO (COALESCE) — é o que faz o protocolo
    # só-provisório aparecer ao filtrar pelo atendente que está com a conversa.
    _aw, _ap = _alogic._build_list_where(assignee_user_id=[7])
    check("filtro atendente -> COALESCE(definitivo, provisório) IN :auids",
          any("COALESCE(assignee_user_id, provisional_assignee_user_id) IN :auids" in c for c in _aw)
          and _ap.get("auids") == [7])
    # _row_matches_filter: cattr = substring case-insensitive (texto); pf = igualdade exata.
    check("cattr filter -> substring case-insensitive",
          _alogic._row_matches_filter({"contact_attrs": {"profissao": "Engenheiro Civil"}}, "cattr:profissao", "civil") is True
          and _alogic._row_matches_filter({"contact_attrs": {"profissao": "Engenheiro"}}, "cattr:profissao", "civil") is False)
    check("pf filter -> exato (não substring)",
          _alogic._row_matches_filter({"fields": {"resultado": "Resolvido"}}, "pf:protocolo:resultado", "Resolv") is False
          and _alogic._row_matches_filter({"fields": {"resultado": "Resolvido"}}, "pf:protocolo:resultado", "Resolvido") is True)
    # pf de QUALQUER tipo: campo de OPÇÃO (em option_keys) casa EXATO; campo de TEXTO casa SUBSTRING.
    check("pf filter -> texto=substring, opção=exato (option_keys)",
          _alogic._row_matches_filter({"fields": {"obs": "Cliente VIP retornou"}}, "pf:protocolo:obs", "vip", set()) is True
          and _alogic._row_matches_filter({"fields": {"resultado": "Resolvido"}}, "pf:protocolo:resultado", "Resolv",
                                          {"protocolo:resultado"}) is False)

    # 7a-canal) Filtro por CANAL: resolve o canal da conversa MAIS RECENTE do protocolo
    # (protocolo → vínculo plugin → core atendimentos → inboxes → channels) e casa por igualdade
    # EXATA de channel_id. Ramo puro de _row_matches_filter (sem DB):
    check("canal filter -> igualdade exata de channel_id",
          _alogic._row_matches_filter({"channel_id": "canal_teste_filtro"}, "canal", "canal_teste_filtro") is True
          and _alogic._row_matches_filter({"channel_id": "outro"}, "canal", "canal_teste_filtro") is False
          and _alogic._row_matches_filter({"channel_id": ""}, "canal", "canal_teste_filtro") is False)
    # list_channels(): reaproveita channel_repo.list_all (não-arquivados), shape {id,name,provider}.
    from db.repositories import (channel_repo as _chan_repo, inbox_repo as _inbox_repo,
                                 contact_inbox_repo as _ci_repo)
    _chan_repo.create(id="canal_teste_filtro", provider="test", display_name="Canal Filtro Teste")
    _chrow = next((c for c in _alogic.list_channels() if c["id"] == "canal_teste_filtro"), None)
    check("list_channels -> shape {id,name,provider}",
          _chrow is not None and _chrow.get("name") == "Canal Filtro Teste" and _chrow.get("provider") == "test")
    # Seed REAL do encadeamento (channel→inbox→contact→contact_inbox→conversation→vínculo) p/
    # exercitar o JOIN de _attach_channels de ponta a ponta.
    _cinbox = _inbox_repo.get_or_create_for_channel("canal_teste_filtro", name="Canal Filtro Teste")
    _cct = _alogic.contact_repo.get_or_create("5511777770001")
    _cci = _ci_repo.get_or_create(inbox_id=_cinbox["id"], contact_id=_cct["id"],
                                  source_id="5511777770001@s.whatsapp.net")
    _cconv = _alogic.conversation_repo.create(inbox_id=_cinbox["id"], contact_id=_cct["id"],
                                              contact_inbox_id=_cci["id"], origin="manual")
    _cproto = _alogic.ensure_protocolo_for_contact(_cct["id"], phone="5511777770001", name="Cliente Canal")
    _alogic.ensure_open_cycle(_cconv["id"], _cct["id"], _cproto["id"])
    _lc = _alogic.list_protocolos(attr_filters={"canal": "canal_teste_filtro"}, limit=200)
    check("list_protocolos(canal filter) -> acha o protocolo do canal",
          any(a["id"] == _cproto["id"] for a in _lc["items"]))
    _lc2 = _alogic.list_protocolos(attr_filters={"canal": "canal_inexistente"}, limit=200)
    check("list_protocolos(canal filter) -> exclui canal diferente",
          all(a["id"] != _cproto["id"] for a in _lc2["items"]))

    # 7c) BUSCA "Buscar cliente" (q → SQL) case- E acento-insensível (fix filtros protocolos).
    _proto_q = _alogic.ensure_protocolo_for_contact(70055, phone="5511960000055", name="João DA Silva")
    def _q_has(q):
        return any(a["id"] == _proto_q["id"] for a in _alogic.list_protocolos(q=q, limit=200)["items"])
    check("q busca: 'joão' (exato) acha", _q_has("joão"))
    check("q busca: 'joao' (sem acento) acha", _q_has("joao"))          # acento-insensível
    check("q busca: 'JOAO' (maiúsc.) acha", _q_has("JOAO"))             # case-insensível (o bug)
    check("q busca: 'silva' (substring) acha", _q_has("silva"))
    check("q busca: 'da' (minúsc. do nome) acha", _q_has("da"))
    check("q busca: 'zzznaoexiste' NÃO acha", not _q_has("zzznaoexiste"))

    # 7d) Paginação (plano 50): envelope {items,total,has_more}, caminhada de cursor sem
    # dup/gap, e teto (clamp_limit/clamp_offset). Isola um conjunto próprio via um token
    # único no nome (q → caminho normal com LIMIT/OFFSET + COUNT no SQL).
    _PGN = 57  # > PAGE_LIST (50) → força múltiplas páginas
    _pgn_ids = set()
    for _i in range(_PGN):
        _pph = f"55119{_i:07d}"
        _pc = _alogic.contact_repo.get_or_create(_pph)
        _pp = _alogic.ensure_protocolo_for_contact(_pc["id"], phone=_pph, name=f"ZPGNTEST Cliente {_i}")
        _pgn_ids.add(_pp["id"])
    _pg1 = _alogic.list_protocolos(q="ZPGNTEST", limit=20, offset=0)
    check("paginação: envelope {items,total,has_more}",
          isinstance(_pg1, dict) and isinstance(_pg1.get("items"), list)
          and isinstance(_pg1.get("total"), int) and isinstance(_pg1.get("has_more"), bool))
    check("paginação: total isola o conjunto (== _PGN) e >= len(items)",
          _pg1["total"] == _PGN and _pg1["total"] >= len(_pg1["items"]))
    check("paginação: página respeita o limit", len(_pg1["items"]) <= 20)
    check("paginação: 1ª página tem has_more (57 > 20)", _pg1["has_more"] is True)
    check("paginação: has_more coerente com offset+len < total",
          _pg1["has_more"] == (0 + len(_pg1["items"]) < _pg1["total"]))
    # Caminhada de cursor por offset: cobre TODOS os ZPGNTEST sem duplicar/pular.
    _seen, _off = [], 0
    while True:
        _pg = _alogic.list_protocolos(q="ZPGNTEST", limit=20, offset=_off)
        _seen.extend(a["id"] for a in _pg["items"])
        if not _pg["has_more"] or not _pg["items"]:
            break
        _off += len(_pg["items"])
    check("paginação: cursor-walk cobre todo o conjunto sem gap",
          set(_seen) == _pgn_ids and len(_seen) == len(set(_seen)))
    check("paginação: última página has_more=False", _pg["has_more"] is False)
    # Teto: limit exagerado é capado por CAP_LIST (200); negativos saneados (página não-vazia).
    _cap = _alogic.list_protocolos(limit=99999, offset=0)
    check("paginação: limit=99999 capado em <= 200", len(_cap["items"]) <= 200)
    _neg = _alogic.list_protocolos(q="ZPGNTEST", limit=-5, offset=-10)
    check("paginação: limit/offset negativos saneados (>=1 item, offset→0)",
          len(_neg["items"]) >= 1 and _neg["total"] == _PGN)

    # ── 7e) Kanban agrupado NO SERVIDOR: índice em cache + paginação POR COLUNA ──
    # O agrupamento saiu do navegador (grouping.py) e cada coluna pagina sozinha, então
    # nenhuma tela precisa baixar a coleção inteira para montar as colunas/contagens.
    _kanban = _il.import_module(f"{_APKG}.kanban_index")
    _grp = _il.import_module(f"{_APKG}.grouping")
    # A chave do cache do índice precisa enxergar o recorte dos filtros, senão trocar o filtro
    # serviria as colunas do filtro anterior.
    check("cache do índice -> chave sensível aos filtros",
          len({_kanban._cache_key({"group_by": "status"}, f, 1, "America/Sao_Paulo")
               for f in ({"nota": ["5"]}, {"nota": ["1"]}, {})}) == 3)
    _kanban.invalidate()
    _gf = {"q": "ZPGNTEST"}          # isola o conjunto criado em 7d

    # (a) Classificador PURO e determinístico (tz + "agora" fixos), um modo por vez.
    import datetime as _dtm
    _tzt = _grp.resolve_tz("America/Sao_Paulo")
    _nowt = _dtm.datetime(2026, 7, 17, 15, 0, 0, tzinfo=_tzt).timestamp()
    _t_today = _dtm.datetime(2026, 7, 17, 9, 0, 0, tzinfo=_tzt).timestamp()
    _t_yest = _dtm.datetime(2026, 7, 16, 9, 0, 0, tzinfo=_tzt).timestamp()
    _t_old = _dtm.datetime(2025, 1, 1, 9, 0, 0, tzinfo=_tzt).timestamp()
    _g_st = _grp.build_grouping({"group_by": "status"})
    check("grouping status -> colunas aberto/fechado e classificação",
          [c["id"] for c in _g_st.static_columns()] == ["aberto", "fechado"]
          and _g_st.column_id_of({"status": "fechado"}) == "fechado"
          and _g_st.column_id_of({"status": "aberto"}) == "aberto")
    _g_at = _grp.build_grouping({"group_by": "atendente"}, users=[{"id": 7, "name": "Ana"}])
    check("grouping atendente -> u:<id>/__none__ + rótulo de usuário fora da lista",
          _g_at.column_id_of({"assignee_user_id": 7}) == "u:7"
          and _g_at.column_id_of({"assignee_user_id": None}) == "__none__"
          and _g_at.label_for("u:99") == "Usuário #99")
    # Atendente EFETIVO (1.24.0): sem definitivo, cai no PROVISÓRIO (dono da conversa no core).
    check("grouping atendente -> efetivo = definitivo ?? provisório",
          _g_at.column_id_of({"assignee_user_id": None, "provisional_assignee_user_id": 7}) == "u:7"
          and _g_at.column_id_of({"assignee_user_id": 3, "provisional_assignee_user_id": 7}) == "u:3"
          and _g_at.column_id_of({"assignee_user_id": None,
                                  "provisional_assignee_user_id": None}) == "__none__")
    _g_dt = _grp.build_grouping({"group_by": "data", "group_date_mode": "faixas"},
                                now_epoch=_nowt, tz=_tzt)
    check("grouping data/faixas -> today/yesterday/older",
          _g_dt.column_id_of({"opened_at": _t_today}) == "today"
          and _g_dt.column_id_of({"opened_at": _t_yest}) == "yesterday"
          and _g_dt.column_id_of({"opened_at": _t_old}) == "older")
    _g_dd = _grp.build_grouping({"group_by": "data", "group_date_mode": "dia"},
                                now_epoch=_nowt, tz=_tzt)
    check("grouping data/dia -> bucket d:YYYY-MM-DD, sem data -> __nodate__",
          _g_dd.column_id_of({"opened_at": _t_today}) == "d:2026-07-17"
          and _g_dd.column_id_of({"opened_at": None}) == "__nodate__")
    check("grouping data/dia -> colunas dinâmicas desc + especial ao fim",
          [c["id"] for c in _g_dd.build_dynamic_columns(
              ["d:2026-07-16", "d:2026-07-17", "__nodate__"])]
          == ["d:2026-07-17", "d:2026-07-16", "__nodate__"])
    _g_sem = _grp.build_grouping({"group_by": "data", "group_date_mode": "semana"},
                                 now_epoch=_nowt, tz=_tzt)
    _g_mes = _grp.build_grouping({"group_by": "data", "group_date_mode": "mes"},
                                 now_epoch=_nowt, tz=_tzt)
    check("grouping data/semana|mes -> chave da segunda-feira e do mês",
          _g_sem.column_id_of({"opened_at": _t_today}) == "w:2026-07-13"
          and _g_mes.column_id_of({"opened_at": _t_today}) == "m:2026-07")
    _g_pr = _grp.build_grouping(
        {"group_by": "data", "group_date_mode": "personalizado", "group_date_grain": "dia",
         "group_date_from": "2026-07-17", "group_date_to": "2026-07-17"},
        now_epoch=_nowt, tz=_tzt)
    check("grouping data/personalizado -> dentro da janela vs __outofrange__",
          _g_pr.column_id_of({"opened_at": _t_today}) == "d:2026-07-17"
          and _g_pr.column_id_of({"opened_at": _t_yest}) == "__outofrange__")
    _g_pf = _grp.build_grouping(
        {"group_by": "pfield", "group_field_scope": "protocolo", "group_attr_key": "motivo"},
        option_def=lambda s, k: {"key": k, "options": ["Dúvida"], "label": "Motivo"})
    check("grouping pfield -> o:<valor>/__none__, lista pega o 1º, needs=extras",
          _g_pf.column_id_of({"fields": {"motivo": "Dúvida"}}) == "o:Dúvida"
          and _g_pf.column_id_of({"fields": {"motivo": ["Dúvida"]}}) == "o:Dúvida"
          and _g_pf.column_id_of({"fields": {}}) == "__none__"
          and "protocolo_extras" in _g_pf.needs)
    check("grouping pfield -> campo removido vira indisponível (1 coluna, só-leitura)",
          _grp.build_grouping({"group_by": "pfield", "group_field_scope": "protocolo",
                               "group_attr_key": "sumiu"}, option_def=lambda s, k: None).unavailable is True)

    # (b) Índice: colunas + contagem EXATA por coluna (os ZPGNTEST nascem todos abertos).
    _idx = _kanban.build_index({"group_by": "status"}, _gf)
    _icols = {c["id"]: c["total"] for c in _idx["columns"]}
    check("índice status -> aberto == _PGN, fechado == 0",
          _icols.get("aberto") == _PGN and _icols.get("fechado") == 0)
    check("índice -> soma das colunas == total filtrado, sem truncar",
          sum(c["total"] for c in _idx["columns"]) == _PGN and _idx["truncated"] is False)
    check("índice -> ids da coluna sem duplicata",
          len(_idx["column_ids"]["aberto"]) == _PGN
          and set(_idx["column_ids"]["aberto"]) == _pgn_ids)

    # (c) Paginação POR COLUNA: cursor-walk cobre a coluna sem dup/gap; teto respeitado.
    _seen_c, _offc = [], 0
    while True:
        _pgc = _alogic.grouped_column_page({"group_by": "status"}, _gf, "aberto",
                                           limit=20, offset=_offc)
        _seen_c.extend(a["id"] for a in _pgc["items"])
        if not _pgc["has_more"] or not _pgc["items"]:
            break
        _offc += len(_pgc["items"])
    check("coluna paginada -> cursor-walk cobre a coluna sem dup/gap",
          len(_seen_c) == _PGN and len(set(_seen_c)) == _PGN and set(_seen_c) == _pgn_ids)
    check("coluna paginada -> total = tamanho da coluna e última página has_more=False",
          _pgc["total"] == _PGN and _pgc["has_more"] is False)
    check("coluna paginada -> limit exagerado capado em <= 200",
          len(_alogic.grouped_column_page({"group_by": "status"}, _gf, "aberto",
                                          limit=99999)["items"]) <= 200)
    _pgz = _alogic.grouped_column_page({"group_by": "status"}, _gf, "fechado", limit=20)
    check("coluna vazia -> envelope zerado",
          _pgz["items"] == [] and _pgz["total"] == 0 and _pgz["has_more"] is False)
    check("coluna inexistente -> envelope zerado (não estoura)",
          _alogic.grouped_column_page({"group_by": "status"}, _gf, "nao_existe")["total"] == 0)

    # (d) grouped_columns (o que a rota serve) + agrupamento por atendente.
    _gcols = _alogic.grouped_columns({"group_by": "atendente"}, _gf)
    _nao = next((c for c in _gcols["columns"] if c["id"] == "__none__"), None)
    check("grouped_columns atendente -> 'Não atribuído' concentra os ZPGNTEST",
          _nao is not None and _nao["total"] == _PGN)
    check("grouped_columns -> shape {columns,truncated,unavailable,read_only}",
          all(k in _gcols for k in ("columns", "truncated", "unavailable", "read_only")))

    # (e) Cache/geração: o bump muda a chave ⇒ invalida em todas as réplicas.
    _ck1 = _kanban._cache_key({"group_by": "status"}, _gf, _kanban.generation(), "America/Sao_Paulo")
    _kanban.bump_generation()
    _ck2 = _kanban._cache_key({"group_by": "status"}, _gf, _kanban.generation(), "America/Sao_Paulo")
    check("geração -> bump muda a chave do cache (invalidação entre réplicas)", _ck1 != _ck2)
    check("q busca: por telefone (substring) acha", _q_has("960000055"))
    # Campo de OPÇÃO agora casa ignorando caixa E acento (antes: exato sensível).
    check("pf opção -> case-insensível",
          _alogic._row_matches_filter({"fields": {"resultado": "Resolvido"}}, "pf:protocolo:resultado",
                                      "resolvido", {"protocolo:resultado"}) is True)
    check("pf opção -> acento-insensível",
          _alogic._row_matches_filter({"fields": {"cidade": "São Paulo"}}, "pf:protocolo:cidade",
                                      "sao paulo", {"protocolo:cidade"}) is True)
    check("pf opção -> ainda exclui valor diferente",
          _alogic._row_matches_filter({"fields": {"resultado": "Resolvido"}}, "pf:protocolo:resultado",
                                      "pendente", {"protocolo:resultado"}) is False)
    check("cattr -> acento-insensível",
          _alogic._row_matches_filter({"contact_attrs": {"cidade": "São Paulo"}}, "cattr:cidade", "sao") is True)

    # 7b) Preferência POR-USUÁRIO (pessoal x equipe) por visualização. Usa _v2 (equipe) + user 101.
    _p0 = _alogic.get_user_view_pref(_v2["id"], 101)
    check("pref ausente -> default equipe",
          _p0 == {"use_personal": False, "personal_filters": {}, "personal_column_order": None})
    _p1 = _alogic.upsert_user_view_pref(_v2["id"], 101, use_personal=True,
                                        personal_filters={"status": "fechado"})
    check("upsert pref -> retorna pessoal",
          _p1 == {"use_personal": True, "personal_filters": {"status": "fechado"},
                  "personal_column_order": None})
    _p1r = _alogic.get_user_view_pref(_v2["id"], 101)
    check("pref persistida -> personal_filters round-trip",
          _p1r["use_personal"] is True and _p1r["personal_filters"] == {"status": "fechado"})
    _p2 = _alogic.upsert_user_view_pref(_v2["id"], 101, use_personal=False)
    check("upsert parcial -> flip use_personal mantém filters",
          _p2 == {"use_personal": False, "personal_filters": {"status": "fechado"},
                  "personal_column_order": None})
    _p999 = _alogic.get_user_view_pref(_v2["id"], 999)
    check("pref isolada por usuário", _p999 == {"use_personal": False, "personal_filters": {}, "personal_column_order": None})
    _alogic.upsert_user_view_pref(_v2["id"], 101, use_personal=True)
    _lv101 = {v["id"]: v.get("pref") for v in _alogic.list_kanban_views(user_id=101)}
    _lv999 = {v["id"]: v.get("pref") for v in _alogic.list_kanban_views(user_id=999)}
    check("list anexa pref do chamador 101",
          _lv101.get(_v2["id"], {}).get("use_personal") is True)
    check("list anexa pref default p/ 999",
          _lv999.get(_v2["id"]) == {"use_personal": False, "personal_filters": {}, "personal_column_order": None})
    check("get_user_view_pref(uid=None) -> default equipe",
          _alogic.get_user_view_pref(_v2["id"], None) == {"use_personal": False, "personal_filters": {}, "personal_column_order": None})
    _vp, _evp = _alogic.create_kanban_view(name="Equipe tmp", scope="team",
                                           group_by="status", owner_user_id=101)
    _alogic.upsert_user_view_pref(_vp["id"], 101, use_personal=True, personal_filters={"q": "x"})
    _alogic.delete_kanban_view(_vp["id"])
    check("delete_kanban_view -> prefs limpas",
          _alogic.get_user_view_pref(_vp["id"], 101) == {"use_personal": False, "personal_filters": {}, "personal_column_order": None})

    # 7c) available_filters: quais TIPOS de filtro a aba expõe (metadado da view, decidido no editor).
    check("view sem available_filters -> None (todos)", _v2.get("available_filters") is None)
    _va, _eva = _alogic.create_kanban_view(name="Só status+curso", scope="team", group_by="status",
                                           available_filters=["status", "cattr:curso"], owner_user_id=101)
    check("create available_filters -> round-trip lista",
          _eva is None and _va.get("available_filters") == ["status", "cattr:curso"])
    _vau, _ = _alogic.update_kanban_view(_va["id"], name="Só status+curso v2")
    check("update sem available_filters -> mantém lista (sentinela)",
          _vau.get("available_filters") == ["status", "cattr:curso"])
    _vau2, _ = _alogic.update_kanban_view(_va["id"], available_filters=["periodo"])
    check("update com available_filters -> troca lista", _vau2.get("available_filters") == ["periodo"])
    _vau3, _ = _alogic.update_kanban_view(_va["id"], available_filters=None)
    check("update available_filters=None -> None (todos)", _vau3.get("available_filters") is None)
    _alogic.delete_kanban_view(_va["id"])

    # 7c-bis) column_order (EQUIPE, na view) + personal_column_order (PESSOAL, na pref): ordem das
    # colunas do Kanban. Mesma mecânica de available_filters (sentinela _UNSET no update, [] limpa).
    _vco, _evco = _alogic.create_kanban_view(name="Ordem colunas", scope="team", group_by="status",
                                             column_order=["fechado", "aberto"], owner_user_id=101)
    check("create column_order -> round-trip lista",
          _evco is None and _vco.get("column_order") == ["fechado", "aberto"])
    _vsem, _ = _alogic.create_kanban_view(name="Sem ordem", scope="team", group_by="status",
                                          owner_user_id=101)
    check("view sem column_order -> None (ordem padrão)", _vsem.get("column_order") is None)
    _alogic.delete_kanban_view(_vsem["id"])
    _vcou, _ = _alogic.update_kanban_view(_vco["id"], name="Ordem colunas v2")
    check("update sem column_order -> mantém lista (sentinela)",
          _vcou.get("column_order") == ["fechado", "aberto"])
    _vcou2, _ = _alogic.update_kanban_view(_vco["id"], column_order=["aberto", "fechado"])
    check("update com column_order -> troca lista", _vcou2.get("column_order") == ["aberto", "fechado"])
    _vcou3, _ = _alogic.update_kanban_view(_vco["id"], column_order=[])
    check("update column_order=[] -> None (ordem padrão)", _vcou3.get("column_order") is None)
    # personal_column_order via my-pref (preferência do PRÓPRIO usuário, gated só por `view`).
    _alogic.upsert_user_view_pref(_vco["id"], 202, personal_column_order=["fechado", "aberto"])
    check("personal_column_order -> round-trip",
          _alogic.get_user_view_pref(_vco["id"], 202).get("personal_column_order") == ["fechado", "aberto"])
    _lv_pco = {v["id"]: v.get("pref") for v in _alogic.list_kanban_views(user_id=202)}
    check("list anexa personal_column_order do chamador",
          _lv_pco.get(_vco["id"], {}).get("personal_column_order") == ["fechado", "aberto"])
    _alogic.upsert_user_view_pref(_vco["id"], 202, use_personal=True)  # merge parcial
    check("merge parcial (só use_personal) -> personal_column_order preservado",
          _alogic.get_user_view_pref(_vco["id"], 202).get("personal_column_order") == ["fechado", "aberto"])
    check("get_user_view_pref(uid=None) -> personal_column_order None",
          _alogic.get_user_view_pref(_vco["id"], None).get("personal_column_order") is None)
    _alogic.delete_kanban_view(_vco["id"])

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

    # 8b) Gate de CRIAÇÃO de visualizações (create_views) — a rota exige create_views OU
    # manage_team_views. Aqui validamos que a permissão está no catálogo RBAC (acheck).
    _rrepo.upsert_plugin_permission("plugin.protocolos.create_views",
                                    "Criar novas visualizações (agrupamentos) no Kanban",
                                    "protocolos", "Protocolos")
    _cvu = _urepo.create(email="createviews@test.com", name="CV",
                         password_hash=_hpa("supersecret"), role_keys=["atendente"])
    _cvu_id = _cvu["id"]
    _cv_req = _types.SimpleNamespace(
        state=_types.SimpleNamespace(user={"id": _cvu_id}),
        url=_types.SimpleNamespace(path="/api/plugins/protocolos/kanban-views"))
    check("create_views -> user SEM perm negado",
          _asyncio.run(_authz.acheck(_cv_req, "plugin.protocolos.create_views")) is False)
    _urepo.set_custom_permissions(_cvu_id, ["plugin.protocolos.create_views"])
    check("create_views -> user COM perm permitido",
          _asyncio.run(_authz.acheck(_cv_req, "plugin.protocolos.create_views")) is True)
    _urepo.delete(_cvu_id)
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
          _alogic._missing_required("protocolo", {"cursos": [], "obs": "", "atendente": 1}) is not None)
    check("_missing_required ok quando preenchido",
          _alogic._missing_required("protocolo", {"cursos": ["A"], "obs": "", "atendente": 1}) is None)

    # 9a-bis) Atendente é rótulo FIXO + OBRIGATÓRIO nos 2 escopos, não-criável/removível como extra.
    for _sc in ("protocolo", "atendimento"):
        _ats = [d for d in _alogic.get_field_defs(_sc) if d.get("type") == "atendente"]
        check(f"atendente fixo -> existe exatamente 1 em '{_sc}'", len(_ats) == 1)
        check(f"atendente fixo -> fixed+obrigatório em '{_sc}'",
              _ats and _ats[0].get("fixed") is True and _ats[0].get("required") is True
              and _ats[0].get("key") == "atendente")
    # Tentar CRIAR um campo atendente extra é ignorado (não vira extra; segue só o fixo).
    _atend_defs_before = _alogic.get_extra_defs("atendimento")  # p/ restaurar ao fim
    _alogic.set_field_defs("atendimento", [
        {"key": "resp", "label": "Responsável", "type": "atendente"},
        {"key": "obs", "label": "Observações", "type": "textarea"},
    ])
    _at_after = [d for d in _alogic.get_field_defs("atendimento") if d.get("type") == "atendente"]
    check("criar atendente extra -> ignorado (segue 1 só, fixo)",
          len(_at_after) == 1 and _at_after[0].get("fixed") is True)
    check("criar atendente extra -> não persistiu como extra",
          all(d.get("type") != "atendente" for d in _alogic.get_extra_defs("atendimento")))
    # normalize_values NÃO exige atendente (coerção ok sem ele); o required é gate de fechamento.
    _cae, _eae = _alogic.normalize_values("atendimento", {"obs": "x"})
    check("normalize_values -> não bloqueia por atendente ausente", _eae is None)
    # _missing_required exige atendente (sem assignee -> erro; com -> ok).
    check("_missing_required atendimento -> sem atendente bloqueia",
          _alogic._missing_required("atendimento", {"obs": "x"}) is not None)
    check("_missing_required atendimento -> com atendente ok",
          _alogic._missing_required("atendimento", {"obs": "x", "atendente": 1}) is None)
    # Restaura os defs de atendimento como estavam antes deste bloco.
    _alogic.set_field_defs("atendimento", _atend_defs_before)

    # 9b) "Lista de seleção" (type=select): `multiple` liga marcação de VÁRIAS → valor vira LISTA
    # (igual a checkboxes); single continua string. Radio saiu da UI (seed migrado p/ select).
    check("select múltiplo -> value_type list",
          _alogic._extra_value_type({"type": "select", "multiple": True}) == "list")
    check("select single -> value_type string",
          _alogic._extra_value_type({"type": "select"}) == "string")
    check("seed: nenhum campo default é radio (migrado p/ select)",
          all(d.get("type") != "radio"
              for defs in _alogic.DEFAULT_EXTRA_DEFS.values() for d in defs))
    _alogic.set_field_defs("protocolo", [
        {"key": "origem", "label": "Origem", "type": "select", "options": ["Site", "Loja", "Telefone"]},
        {"key": "motivos", "label": "Motivos", "type": "select", "options": ["A", "B", "C"], "multiple": True},
    ])
    _psel = {d["key"]: d for d in _alogic.get_field_defs("protocolo")}
    check("select múltiplo -> multiple persistido", _psel.get("motivos", {}).get("multiple") is True)
    _cm, _ecm = _alogic.normalize_values("protocolo", {"motivos": ["A", "C"], "origem": "Site"})
    check("select múltiplo -> valor vira LISTA", _ecm is None and _cm.get("motivos") == ["A", "C"])
    check("select single -> valor string", _cm.get("origem") == "Site")
    _, _eminv = _alogic.normalize_values("protocolo", {"motivos": ["Z"]})
    check("select múltiplo -> opção inválida barrada", _eminv is not None)
    _alogic.set_field_defs("protocolo", [
        {"key": "motivos", "label": "Motivos", "type": "select", "options": ["A", "B"],
         "multiple": True, "required": True},
    ])
    _, _emreq = _alogic.normalize_values("protocolo", {"motivos": []})
    check("select múltiplo obrigatório vazio -> erro", _emreq is not None and "Motivos" in _emreq)
    _cmr, _ecmr = _alogic.normalize_values("protocolo", {"motivos": ["A"]})
    check("select múltiplo obrigatório preenchido -> ok", _ecmr is None and _cmr.get("motivos") == ["A"])
    _alogic.set_field_defs("protocolo", [])  # limpa p/ não vazar estado a testes seguintes

    # ── Protocolos: Ignorar abertura por regex (direção) + pular avaliação por atributos ──
    section("Protocolos Ignorar abertura / Pular avaliação")

    class _CtxExtras:
        def __init__(self, extras):
            self.extras = extras

    def _msgs(user_text):  # lista estilo OpenAI (última msg = trigger do contato)
        return [{"role": "system", "content": "sp"}, {"role": "user", "content": user_text}]

    # Feature 1 — regra "ignorar abertura" (config + matcher + before_reopen)
    _sk = _alogic.set_skip_open_config({"enabled": True, "regex": r"PROT-\d", "direction": "sent"})
    check("skip-open round-trip", _sk == {"enabled": True, "regex": r"PROT-\d", "direction": "sent"})
    check("direção sent casa enviada", _alogic._skip_open_matches("PROT-9", "sent") is True)
    check("direção sent NÃO casa recebida", _alogic._skip_open_matches("PROT-9", "received") is False)
    # ENVIADA (operador) → before_reopen impede reabrir (mensagem ainda vai ao WhatsApp).
    check("before_reopen sent+casa -> False (não reabre)",
          _alogic.before_reopen(_CtxExtras({"role": "assistant", "text": "PROT-1"}), True) is False)
    check("before_reopen user na direção sent -> não bloqueia",
          _alogic.before_reopen(_CtxExtras({"role": "user", "text": "PROT-1"}), True) is True)
    # RECEBIDA (contato) → mensagem aparece (não dropa); só a resposta da IA é abortada via
    # suppress_ai_on_ignored (filter.llm.messages → None). Protocolo pulado em on_inbound.
    _alogic.set_skip_open_config({"enabled": True, "regex": r"PROT-\d", "direction": "received"})
    check("suppress_ai aborta IA p/ recebida que casa (received)",
          _alogic.suppress_ai_on_ignored(_CtxExtras({}), _msgs("PROT-2")) is None)
    check("suppress_ai deixa IA rodar p/ recebida que NÃO casa",
          _alogic.suppress_ai_on_ignored(_CtxExtras({}), _msgs("oi tudo bem")) == _msgs("oi tudo bem"))
    # notify_on_ignored: silencia (sem badge/som/alerta) a recebida que casa.
    check("notify_on_ignored: recebida que casa -> silenciosa (False)",
          _alogic.notify_on_ignored(_CtxExtras({"text": "PROT-2"}), True) is False)
    check("notify_on_ignored: recebida que NÃO casa -> notifica (True)",
          _alogic.notify_on_ignored(_CtxExtras({"text": "oi"}), True) is True)
    _alogic.set_skip_open_config({"enabled": True, "regex": r"PROT-\d", "direction": "sent"})
    check("notify_on_ignored NÃO silencia quando direção só sent",
          _alogic.notify_on_ignored(_CtxExtras({"text": "PROT-3"}), True) is True)
    check("suppress_ai NÃO aborta quando direção só sent",
          _alogic.suppress_ai_on_ignored(_CtxExtras({}), _msgs("PROT-3")) == _msgs("PROT-3"))
    _alogic.set_skip_open_config({"enabled": True, "regex": r"PROT-\d", "direction": "both"})
    check("suppress_ai aborta na direção both",
          _alogic.suppress_ai_on_ignored(_CtxExtras({}), _msgs("PROT-4")) is None)
    check("both: before_reopen sent também bloqueia",
          _alogic.before_reopen(_CtxExtras({"role": "assistant", "text": "PROT-4"}), True) is False)
    check("suppress_ai ignora value não-lista", _alogic.suppress_ai_on_ignored(_CtxExtras({}), "x") == "x")
    _alogic.set_skip_open_config({"enabled": False, "regex": r"PROT-\d", "direction": "both"})
    check("desligado -> suppress_ai deixa IA rodar",
          _alogic.suppress_ai_on_ignored(_CtxExtras({}), _msgs("PROT-5")) == _msgs("PROT-5"))
    check("direção inválida cai em sent",
          _alogic.set_skip_open_config({"enabled": True, "regex": "x", "direction": "zzz"})["direction"] == "sent")
    _alogic.set_skip_open_config({"enabled": True, "regex": "[bad", "direction": "both"})
    check("regex inválida -> False sem exceção", _alogic._skip_open_matches("qq", "sent") is False)
    _alogic.set_skip_open_config({"enabled": False, "regex": r"PROT-\d", "direction": "both"})
    check("desligado -> matcher False", _alogic._skip_open_matches("PROT-3", "sent") is False)
    check("desligado -> before_reopen mantém value",
          _alogic.before_reopen(_CtxExtras({"role": "user", "text": "PROT-3"}), True) is True)

    # Feature 2 — pular avaliação por atributos (contato + conversa)
    # O casamento valor-armazenado × valor-da-regra é do `_rule_matches` (via
    # `_stored_parts`/`_condition_matches`). O helper antigo `_attr_value_matches` existia
    # só para a decisão de continuidade por atributo, removida na 2.0.0 do plugin.
    def _r1(op, *values):
        return {"key": "a", "scope": "contact", "conditions": [{"op": op, "values": list(values)}]}
    check("attr match string (case/trim)", _alogic._rule_matches("Não Possui", _r1("eq", " não possui ")) is True)
    check("attr match lista nativa", _alogic._rule_matches(["a", "não possui"], _r1("eq", "não possui")) is True)
    check("attr match multi vírgula", _alogic._rule_matches("a, não possui, b", _r1("eq", "não possui")) is True)
    check("attr no-match diferente", _alogic._rule_matches("possui", _r1("eq", "não possui")) is False)
    # Fichas (plugin 2.0.0): positivo casa com QUALQUER uma, negativo exige NENHUMA.
    check("attr match qualquer ficha", _alogic._rule_matches("possui", _r1("eq", "não possui", "possui")) is True)
    check("attr negativo exige nenhuma ficha", _alogic._rule_matches("possui", _r1("neq", "não possui", "possui")) is False)
    check("sanitize descarta scope inválido",
          _alogic._sanitize_skip_attrs([{"key": "k", "scope": "x", "value": "v"}]) == [])

    from db.repositories import custom_attribute_repo as _ca_repo
    from db.repositories import conversation_repo as _sk_conv_repo
    from db.tables import contacts as _contacts_tbl, conversations as _conv_tbl
    _skc = contact_repo.get_or_create("5511900000042")
    _skcid = _skc["id"]
    _ca_repo.set_values(_contacts_tbl, _skcid, {"curso_de_interesse": "não possui"})
    _alogic.set_protocol_config({"enabled": True, "normal": {"title": "", "link": "https://x"},
                                 "privado": {"title": "", "link": ""},
                                 "skip_attrs": [{"key": "curso_de_interesse", "scope": "contact", "value": "não possui"}]})
    # A condição guarda `values` (fichas) + o espelho legado `value` quando é 1 ficha só.
    check("skip_attrs round-trip na protocol-config",
          _alogic.get_protocol_config().get("skip_attrs")
          == [{"key": "curso_de_interesse", "scope": "contact", "join": "any",
               "conditions": [{"op": "eq", "values": ["não possui"], "value": "não possui"}],
               "value": "não possui"}])
    check("skip avaliação por atributo de CONTATO -> True",
          _alogic._should_skip_evaluation({"contact_id": _skcid}, None) is True)
    _ca_repo.set_values(_contacts_tbl, _skcid, {"curso_de_interesse": "engenharia"})
    check("valor diferente -> não pula", _alogic._should_skip_evaluation({"contact_id": _skcid}, None) is False)
    _skconv = _sk_conv_repo.resolve_for_contact(_skcid, "5511900000042@s.whatsapp.net")
    _ca_repo.set_values(_conv_tbl, _skconv["id"], {"origem": "spam"})
    _alogic.set_protocol_config({"enabled": True, "normal": {"title": "", "link": "https://x"},
                                 "privado": {"title": "", "link": ""},
                                 "skip_attrs": [{"key": "origem", "scope": "conversation", "value": "spam"}]})
    check("skip avaliação por atributo de CONVERSA -> True",
          _alogic._should_skip_evaluation({"contact_id": _skcid}, _skconv["id"]) is True)
    _alogic.set_protocol_config({"enabled": True, "normal": {"title": "", "link": "https://x"},
                                 "privado": {"title": "", "link": ""}, "skip_attrs": []})
    check("sem regras -> não pula", _alogic._should_skip_evaluation({"contact_id": _skcid}, _skconv["id"]) is False)

    # Escopo "protocolo": a regra também aceita um RÓTULO da aba Protocolo (sistema de
    # campos próprio do plugin), lido de ``at["fields"]`` — não é atributo do core.
    check("sanitize aceita scope protocolo",
          _alogic._sanitize_skip_attrs([{"key": "resultado", "scope": "protocolo", "value": "sem contato"}])
          == [{"key": "resultado", "scope": "protocolo", "join": "any",
               "conditions": [{"op": "eq", "values": ["sem contato"], "value": "sem contato"}],
               "value": "sem contato"}])
    _alogic.set_protocol_config({"enabled": True, "normal": {"title": "", "link": "https://x"},
                                 "privado": {"title": "", "link": ""},
                                 "skip_attrs": [{"key": "resultado", "scope": "protocolo", "value": "sem contato"}]})
    check("skip avaliação por RÓTULO do protocolo -> True",
          _alogic._should_skip_evaluation({"contact_id": _skcid, "fields": {"resultado": "Sem contato"}}, None) is True)
    check("rótulo do protocolo com outro valor -> não pula",
          _alogic._should_skip_evaluation({"contact_id": _skcid, "fields": {"resultado": "vendeu"}}, None) is False)
    check("rótulo multi (checkboxes) casa por pertencimento",
          _alogic._should_skip_evaluation({"contact_id": _skcid, "fields": {"resultado": ["x", "sem contato"]}}, None) is True)
    check("protocolo sem o rótulo preenchido -> não pula",
          _alogic._should_skip_evaluation({"contact_id": _skcid, "fields": {}}, None) is False)
    _alogic.set_protocol_config({"enabled": True, "normal": {"title": "", "link": "https://x"},
                                 "privado": {"title": "", "link": ""}, "skip_attrs": []})
    _alogic.set_skip_open_config({"enabled": False, "regex": "", "direction": "sent"})  # limpa estado

    # Feature 3 — Resolver/Finalizar robusto a atendimento ÓRFÃO (ciclo sem conversa viva)
    from sqlalchemy import text as _sa_text
    from db.tables import messages as _msgs_t
    _rob_c = contact_repo.get_or_create("5511900000077")
    _rob_proto = _alogic.ensure_protocolo_for_contact(
        _rob_c["id"], phone="5511900000077", name="Robustez")
    # atendente preenchido (rótulo fixo obrigatório) — replica o caso do print onde o protocolo
    # órfão já tinha atendente e finaliza sem pedir nada.
    with _get_engine().begin() as _rc:
        _rc.execute(_sa_text("UPDATE plugin_protocolos_protocolos SET assignee_user_id=1, "
                             "assignee_name='Admin' WHERE id=:i"), {"i": _rob_proto["id"]})
    # (a) ciclo ÓRFÃO (conversation_id inexistente no core) + required OK -> close auto-encerra
    #     o órfão e finaliza SEM travar com 'resolva-o antes'.
    _alogic._insert_cycle(999_000_111, _rob_c["id"], _rob_proto["id"])
    _rob_at, _rob_err = _alogic.close_protocolo(_rob_proto["id"], assignee_user_id=1, assignee_name="Admin")
    check("close: ciclo órfão NÃO bloqueia com 'resolva-o antes'",
          not (_rob_err and "resolva-o antes" in _rob_err))
    check("close: protocolo órfão finaliza (status fechado)", _rob_err is None)
    check("close: ciclo órfão foi auto-encerrado",
          _alogic._open_cycles_of_protocolo(_rob_proto["id"]) == [])
    # (a2) SEM required (sem atendente): retorna erro de obrigatório e NÃO deixa efeito colateral
    #      (o ciclo órfão continua ABERTO — validação ANTES de qualquer escrita).
    _rob_c2 = contact_repo.get_or_create("5511900000078")
    _rob_proto_nr = _alogic.ensure_protocolo_for_contact(
        _rob_c2["id"], phone="5511900000078", name="Sem atendente")
    _alogic._insert_cycle(999_000_113, _rob_c2["id"], _rob_proto_nr["id"])
    _, _rob_err_nr = _alogic.close_protocolo(_rob_proto_nr["id"])  # sem assignee
    check("close sem required -> erro de obrigatório", bool(_rob_err_nr) and "brigat" in _rob_err_nr)
    check("close sem required -> ciclo órfão SEGUE aberto (sem efeito colateral)",
          len(_alogic._open_cycles_of_protocolo(_rob_proto_nr["id"])) == 1)
    # (b) ciclo RESOLVÍVEL: conversa viva no core -> ainda bloqueia (comportamento inalterado).
    _rob_proto2 = _alogic.ensure_protocolo_for_contact(
        _rob_c["id"], phone="5511900000077", name="Robustez")  # proto1 fechado -> novo protocolo aberto
    _rob_live = _sk_conv_repo.resolve_for_contact(_rob_c["id"], "5511900000077@s.whatsapp.net")
    _alogic._insert_cycle(_rob_live["id"], _rob_c["id"], _rob_proto2["id"])
    _, _rob_err2 = _alogic.close_protocolo(_rob_proto2["id"])
    check("close: ciclo com conversa viva ainda exige resolver antes",
          bool(_rob_err2) and "resolva-o antes" in _rob_err2)
    check("close: conversa viva NÃO é encerrada (ciclo segue aberto)",
          len(_alogic._open_cycles_of_protocolo(_rob_proto2["id"])) == 1)
    # (c) resolve_atendimento de conversa inexistente -> no-op gracioso (sem 'não encontrada').
    _res_link, _res_err = _alogic.resolve_atendimento(999_000_222, {})
    check("resolve_atendimento conversa inexistente -> gracioso (err None)", _res_err is None)
    # (d) _emit_proto_notice numa conversa deletada -> no-op limpo (sem exceção, sem row órfã).
    _alogic._emit_proto_notice("protocolo_closed", conversation_id=999_000_222, contact_id=_rob_c["id"])
    check("_emit_proto_notice em conversa inexistente -> não cria conversation_event",
          _get_engine().connect().execute(_sa_select(_sa_func.count()).select_from(_msgs_t)
              .where(_msgs_t.c.conversation_id == 999_000_222)).scalar() == 0)
    # (e) Opção B — avaliação PULADA em protocolo órfão (conversa do protocolo foi excluída).
    _orf_c = contact_repo.get_or_create("5511900000079")
    _orf_conv = _sk_conv_repo.resolve_for_contact(_orf_c["id"], "5511900000079@s.whatsapp.net")
    _orf_proto = _alogic.ensure_protocolo_for_contact(
        _orf_c["id"], phone="5511900000079", name="Órfão aval")
    _alogic._insert_cycle(_orf_conv["id"], _orf_c["id"], _orf_proto["id"])
    check("_is_orphan_protocolo -> False com conversa viva",
          _alogic._is_orphan_protocolo(_alogic.get_protocolo(_orf_proto["id"])) is False)
    contact_repo.delete(_orf_c["id"])   # exclui contato -> cascade apaga a conversa -> protocolo órfão
    check("_is_orphan_protocolo -> True após conversa excluída",
          _alogic._is_orphan_protocolo(_alogic.get_protocolo(_orf_proto["id"])) is True)
    # send_protocol_on_close é best-effort e no harness get_deps()=None (sai cedo); a decisão
    # de pular está isolada em _is_orphan_protocolo (testada acima) — chamamos p/ garantir no-raise.
    _alogic.send_protocol_on_close(_alogic.get_protocolo(_orf_proto["id"]))
    check("send_protocol_on_close(órfão) não levanta", True)

    # ── "Aberto por" (opener) no protocolo e nos atendimentos (ciclos) ──
    # _resolve_opener é puro: mapeia a origem do evento/ação para {kind,user_id,name}.
    check("_resolve_opener(inbound) -> Contato",
          _alogic._resolve_opener("inbound") == {"kind": "contact", "user_id": None, "name": "Contato"})
    check("_resolve_opener(ai) -> IA",
          _alogic._resolve_opener("ai") == {"kind": "ia", "user_id": None, "name": "IA"})
    check("_resolve_opener(agent) -> usa nome do current_user",
          _alogic._resolve_opener("agent", None, user_id=7, name="Fulano")
          == {"kind": "agent", "user_id": 7, "name": "Fulano"})
    check("_resolve_opener(operator) sem conversa -> Atendente (best-effort vazio)",
          _alogic._resolve_opener("operator", None)["name"] == "Atendente")
    check("_resolve_opener(desconhecido) -> cai em Contato",
          _alogic._resolve_opener("")["kind"] == "contact")
    # Criação real GRAVA o opener; get_protocolo devolve as colunas via dict(row).
    _op_c = contact_repo.get_or_create("5511900000090")
    _op_proto = _alogic.ensure_protocolo_for_contact(
        _op_c["id"], phone="5511900000090", name="Opener Teste",
        opener=_alogic._resolve_opener("inbound"))
    _op_row = _alogic.get_protocolo(_op_proto["id"])
    check("ensure_protocolo grava opened_by (Contato)",
          _op_row.get("opened_by_kind") == "contact" and _op_row.get("opened_by_name") == "Contato")
    # O ciclo carrega SEU próprio opener (aqui simulamos abertura pela IA).
    _op_conv = _sk_conv_repo.resolve_for_contact(_op_c["id"], "5511900000090@s.whatsapp.net")
    _op_cyc = _alogic._insert_cycle(_op_conv["id"], _op_c["id"], _op_proto["id"],
                                    opener=_alogic._resolve_opener("ai"))
    check("_insert_cycle grava opened_by no ciclo (IA)", _op_cyc.get("opened_by_name") == "IA")
    # list_atendimentos (usado pelo popup) expõe opened_by_name na linha.
    _op_list = _alogic.list_atendimentos(_op_proto["id"])
    check("list_atendimentos -> opened_by_name na linha",
          any(a.get("opened_by_name") == "IA" for a in _op_list))
    # Registro SEM opener (histórico antigo) fica com '' -> a UI mostra '—'.
    _op_c2 = contact_repo.get_or_create("5511900000091")
    _op_proto2 = _alogic.ensure_protocolo_for_contact(
        _op_c2["id"], phone="5511900000091", name="Sem opener")
    check("ensure_protocolo sem opener -> opened_by_name vazio (histórico)",
          _alogic.get_protocolo(_op_proto2["id"]).get("opened_by_name") == "")

    # ── Religar IA ao fechar: setting (default ON) + helper best-effort ──
    check("get_reactivate_ai_on_close_setting -> default True",
          _alogic.get_reactivate_ai_on_close_setting() is True)
    config_repo.set("plugin.protocolos.reactivate_ai_on_close", False)
    check("get_reactivate_ai_on_close_setting -> respeita override False",
          _alogic.get_reactivate_ai_on_close_setting() is False)
    config_repo.set("plugin.protocolos.reactivate_ai_on_close", True)
    # Órfão: reactivate_ai_after_close pula (via _is_orphan_protocolo) e não levanta. No harness
    # get_deps()=None, então o caminho de religar propriamente é coberto pelo teste de core set_ai.
    _asyncio.run(_alogic.reactivate_ai_after_close(_alogic.get_protocolo(_orf_proto["id"])))
    check("reactivate_ai_after_close(órfão) não levanta", True)

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
# Índice parcial uq_atend_open_contact_inbox (migration 0036): no máximo UMA
# conversa ABERTA por (contato, inbox) — fecha a 1ª antes de criar a 2ª.
_conv_repo.set_status(_conv1["id"], "closed")
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

# plano 50 F8 — o row enriquecido carrega tags + atributos + avatar do CONTATO, para o
# sidebar conversa-first filtrar/renderizar sem um fetch de contatos à parte + has_more.
check("F8: row enriquecido traz contact_tags", "contact_tags" in _convs[0])
check("F8: row enriquecido traz contact_custom_attributes (dict)",
      isinstance(_convs[0].get("contact_custom_attributes"), dict))
check("F8: row enriquecido traz avatar_v", "avatar_v" in _convs[0])
check("F8: lista de atendimentos devolve has_more", "has_more" in r.json()["data"])

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
# Envio manual autenticado carimba o nome do operador (exibido no lugar de "Manual").
_att_last = message_repo.get_last(_cid)
check("send (autenticado) -> persiste sent_by_name do operador",
      _att_last.get("sent_by_name") == "A2")
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
# Segundo admin, com sessão própria (_mgrtok), para exercitar assign-me/ai como
# um operador distinto do admin default da suíte.
_mgr = _urepo2.create(email="mgr@test.com", name="Mgr",
                      password_hash=_hpa2("supersecret"), role_keys=["admin"])
_mgrtok = client.post("/api/auth/login",
                      json={"email": "mgr@test.com", "password": "supersecret"}).json()["data"]["token"]
r = client.post(f"/api/conversations/{_conv2['id']}/assign-me",
                headers={"Authorization": f"Bearer {_mgrtok}"})
check("POST assign-me (admin) -> 200 + assignee=eu",
      r.status_code == 200 and r.json()["data"]["conversation"]["assignee_user_id"] == _mgr["id"])
r = anon.post(f"/api/conversations/{_conv2['id']}/assign-me")
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

# ── Regra "ignorar abertura": contato NOVO que casa a regex NÃO abre atendimento ──
# (create_closed): a conversa nasce FECHADA — mensagem salva/visível, sem atendimento
# aberto nem card de sistema. reopen=None/True seguem criando ABERTA.
_ci_new = _ci_repo.get_or_create(inbox_id=1, contact_id=_cid,
                                 source_id=f"{_cid}@s.whatsapp.net")
_jid_new = f"{_cid}@s.whatsapp.net"
# fecha qualquer conversa aberta do par p/ o próximo resolve ver "nenhuma aberta"... na
# verdade create só olha get_latest; usamos um contato NOVO dedicado p/ isolar o caso.
_skc_new = contact_repo.get_or_create("5500011199999")  # contato novo, sem conversa
_ci_skn = _ci_repo.get_or_create(inbox_id=1, contact_id=_skc_new["id"],
                                 source_id=f"{_skc_new['id']}@s.whatsapp.net")
_conv_closed, _ev_closed = _conv_repo.resolve_for_contact_ex(
    _skc_new["id"], f"{_skc_new['id']}@s.whatsapp.net", create_closed=True)
check("create_closed: contato novo -> conversa FECHADA", _conv_closed["status"] == "closed")
check("create_closed: sem transição 'created' (sem card)", _ev_closed is None)
# control: contato novo diferente sem create_closed -> ABERTA (comportamento inalterado)
_skc_open = contact_repo.get_or_create("5500011188888")
_conv_open, _ev_open = _conv_repo.resolve_for_contact_ex(
    _skc_open["id"], f"{_skc_open['id']}@s.whatsapp.net", create_closed=False)
check("control: sem create_closed -> conversa ABERTA", _conv_open["status"] == "open")
check("control: transição 'created'", _ev_open == "created")
# wiring via ContactMemory: ensure_conversation_live('user', reopen=False) -> fechada,
# add_message('user', reopen=False) mantém fechada (batch re-resolve a MESMA thread).
_cm_skip = _CM("5500011177777")  # contato novo
_conv_id_skip = _cm_skip.ensure_conversation_live("user", False)
check("ensure_conversation_live(reopen=False) -> cria conversa", _conv_id_skip is not None)
check("ensure_conversation_live(reopen=False) -> FECHADA",
      _conv_repo.get(_conv_id_skip)["status"] == "closed")
_cm_skip.add_message("user", "não abrir proto", reopen=False)
check("add_message(reopen=False) mantém a MESMA conversa fechada",
      _conv_repo.get(_conv_id_skip)["status"] == "closed")
with _get_engine().connect() as _conn:
    _skip_msg = _conn.execute(
        _sa_select(_msgs_t.c.conversation_id).where(_msgs_t.c.contact_id == _cm_skip.id)
        .where(_msgs_t.c.role == "user").order_by(_msgs_t.c.id.desc()).limit(1)).scalar()
check("mensagem ignorada fica salva e vinculada à conversa fechada",
      _skip_msg == _conv_id_skip)
# control via memória: contato novo, reopen=None (regra padrão) -> ABERTA
_cm_norm = _CM("5500011166666")
_conv_id_norm = _cm_norm.ensure_conversation_live("user", None)
check("control memória: reopen=None -> conversa ABERTA",
      _conv_repo.get(_conv_id_norm)["status"] == "open")

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
# uq_atend_open_contact_inbox: fecha a conversa aberta do par antes de criar outra.
_conv_repo.set_status(_conv2["id"], "closed")
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
# Sem operador (automação / serviço interno) o "desligar IA" não tem a quem
# atribuir → cai em "Não atribuídas". Via HTTP isso não é mais alcançável (o gate
# has_users exige sessão de usuário — plano 48 F0), então exercemos a política de
# transferência direto no serviço com actor_id=None.
_conv_repo.set_status(_p17["id"], "closed")  # 1 aberta por (contato, inbox)
_p17b = _conv_repo.create(inbox_id=1, contact_id=_cid, contact_inbox_id=_ci["id"])
_conv_repo.set_assignee(_p17b["id"], _mgr["id"])
from app.services import conversation_service as _csvc_p17
_db17 = _asyncio.run(_csvc_p17.set_ai(app.state.deps, _conv_repo.get(_p17b["id"]), 0,
                                      actor_id=None, actor_name=None))
check("P17 ai off (sem operador) -> Não atribuídas", _db17["assignee_user_id"] is None)

# ── set_ai(clear_transfer_tag): religar a IA mantendo a tag (usado pelo fechar-
#    protocolo). Default=True remove a tag; False preserva o rótulo. ──
from app.services import conversation_service as _csvc
from agent.tools.transfer_to_human import TRANSFER_TAG as _TT
try:
    tag_repo.create(_TT, "#ef4444")
except Exception:  # noqa: BLE001 — tag pode já existir
    pass
# (a) clear_transfer_tag=False -> IA religa (agente default, assignee limpo) e a tag FICA.
_conv_repo.set_status(_p17b["id"], "closed")  # 1 aberta por (contato, inbox)
_ctk = _conv_repo.create(inbox_id=1, contact_id=_cid, contact_inbox_id=_ci["id"])
_conv_repo.set_assignee(_ctk["id"], _mgr["id"])  # humano assume
_conv_repo.set_ai_active(_ctk["id"], 0)
tag_repo.add_contact_tag(_cid, _TT)
_ktag = _asyncio.run(_csvc.set_ai(
    app.state.deps, _conv_repo.get(_ctk["id"]), 1, clear_transfer_tag=False))
check("clear_transfer_tag=False -> ai_active=1", bool(_ktag) and _ktag["ai_active"] == 1)
check("clear_transfer_tag=False -> agente default religado", _ktag["active_agent_key"] == "default")
check("clear_transfer_tag=False -> assignee humano limpo", _ktag["assignee_user_id"] is None)
check("clear_transfer_tag=False -> tag transferido_atendente PRESERVADA",
      _TT in tag_repo.get_contact_tags(_cid))
# (b) default (clear_transfer_tag=True) -> religa E remove a tag (regressão do plano 29 A5).
_conv_repo.set_status(_ctk["id"], "closed")
_ctk2 = _conv_repo.create(inbox_id=1, contact_id=_cid, contact_inbox_id=_ci["id"])
_conv_repo.set_assignee(_ctk2["id"], _mgr["id"])
_conv_repo.set_ai_active(_ctk2["id"], 0)
tag_repo.add_contact_tag(_cid, _TT)
_asyncio.run(_csvc.set_ai(app.state.deps, _conv_repo.get(_ctk2["id"]), 1))
check("clear_transfer_tag default=True -> tag removida",
      _TT not in tag_repo.get_contact_tags(_cid))

# ── P66: o header "Atribuir a mim" / "Atribuída a você" roteia pelo toggle /ai
#    (setConversationAi → set_ai). Assumir DESLIGA a IA e atribui ao operador;
#    devolver RELIGA a IA, limpa o responsável e remove a tag de transferência. ──
_conv_repo.set_status(_ctk2["id"], "closed")  # 1 aberta por (contato, inbox)
_p66 = _conv_repo.create(inbox_id=1, contact_id=_cid, contact_inbox_id=_ci["id"])
_conv_repo.set_ai_active(_p66["id"], 1)        # nasce com IA ligada, sem humano
tag_repo.add_contact_tag(_cid, _TT)            # simula transferência prévia p/ humano
# "Atribuir a mim" no header -> POST /ai {active:false} como o operador logado.
r = client.post(f"/api/conversations/{_p66['id']}/ai", json={"active": False},
                headers={"Authorization": f"Bearer {_mgrtok}"})
check("P66 header assumir -> 200", r.status_code == 200)
_h66 = r.json()["data"]["conversation"]
check("P66 header assumir -> IA desligada", _h66["ai_active"] == 0)
check("P66 header assumir -> conversa atribuída ao operador", _h66["assignee_user_id"] == _mgr["id"])
check("P66 header assumir -> agente ativo limpo", not _h66["active_agent_key"])
# "Atribuída a você" no header -> POST /ai {active:true} devolve à IA.
r = client.post(f"/api/conversations/{_p66['id']}/ai", json={"active": True},
                headers={"Authorization": f"Bearer {_mgrtok}"})
check("P66 header devolver -> 200", r.status_code == 200)
_h66b = r.json()["data"]["conversation"]
check("P66 header devolver -> IA religada", _h66b["ai_active"] == 1)
check("P66 header devolver -> responsável humano limpo", _h66b["assignee_user_id"] is None)
check("P66 header devolver -> agente default re-vinculado", _h66b["active_agent_key"] == "default")
check("P66 header devolver -> tag transferido_atendente removida",
      _TT not in tag_repo.get_contact_tags(_cid))

# ── A IA se auto-desligando (transfer_to_human) emite o card "SISTEMA pausou a IA" ──
from server import system_notices as _sysn
_txf_cm = _CM("5500011177777")
_txf_conv = _txf_cm.ensure_conversation_live("user", None)
_asyncio.run(app.state.deps.broadcast_tool_calls(
    "5500011177777",
    [{"tool": "transfer_to_human", "args": {"reason": "cliente pediu humano"}}],
    channel_id="default"))
check("transfer_to_human -> card ai_off emitido no fio",
      _sysn.has_event(_txf_conv, "ai_off") is True)
# Tool comum (sem transfer) NÃO cria card ai_off (guarda contra falso positivo).
_txf_cm2 = _CM("5500011188888")
_txf_conv2 = _txf_cm2.ensure_conversation_live("user", None)
_asyncio.run(app.state.deps.broadcast_tool_calls(
    "5500011188888", [{"tool": "save_contact_info", "args": {}}], channel_id="default"))
check("tool comum (sem transfer) -> nenhum card ai_off",
      _sysn.has_event(_txf_conv2, "ai_off") is False)
# Autor do card: ação MANUAL (usuário) usa o nome; ação AUTOMÁTICA (sem actor) usa "SISTEMA".
check("ai_on manual -> nome do usuário",
      _sysn.FORMATTERS["ai_on"](actor="Fulano") == "🤖 Fulano reativou a IA.")
check("ai_on automático -> SISTEMA",
      _sysn.FORMATTERS["ai_on"](actor=None) == "🤖 SISTEMA reativou a IA.")
check("ai_off manual -> nome do usuário",
      _sysn.FORMATTERS["ai_off"](actor="Fulano") == "🤖 Fulano pausou a IA.")
check("ai_off automático -> SISTEMA",
      _sysn.FORMATTERS["ai_off"](actor=None) == "🤖 SISTEMA pausou a IA.")

# ═══════════════════════════════════════════════════════════════════
#  Atribuição de agente por mensagem ("IA - <NOME>" / "Ferramenta IA - <NOME>")
# ═══════════════════════════════════════════════════════════════════
section("Atribuição de agente nas mensagens")
from db.repositories import agent_repo as _ar
# (0) resolver agent_key -> display_name (com fallbacks)
_ar.ensure("attr_ag", display_name="Agente Atributo")
_ar.ensure("attr_ag_vazio", display_name="")
check("display_name_for -> nome do agente",
      _ar.display_name_for("attr_ag") == "Agente Atributo")
check("display_name_for(display_name vazio) -> fallback para a chave",
      _ar.display_name_for("attr_ag_vazio") == "attr_ag_vazio")
check("display_name_for(inexistente) -> None",
      _ar.display_name_for("nao_existe_xyz") is None)
check("display_name_for(None) -> None", _ar.display_name_for(None) is None)

# (1) message_repo.add persiste agent_key e _row_to_dict o expõe
_attr_cm = _CM("5500019200001")
_attr_conv = _attr_cm.ensure_conversation_live("user", None)
_attr_msg = message_repo.add(_attr_cm.id, "assistant", "resposta da IA",
                             conversation_id=_attr_conv, agent_key="attr_ag")
check("message_repo.add -> retorna agent_key", _attr_msg.get("agent_key") == "attr_ag")
_attr_rows = message_repo.get_by_conversation(_attr_conv)
_attr_ai = [m for m in _attr_rows if m.get("role") == "assistant"]
check("_row_to_dict expõe agent_key na mensagem persistida",
      bool(_attr_ai) and _attr_ai[-1].get("agent_key") == "attr_ag")

# (2) GET /messages enriquece com agent_name (display_name resolvido)
r = client.get(f"/api/atendimentos/{_attr_conv}/messages")
check("GET /messages -> 200", r.status_code == 200)
_gm = [m for m in r.json()["data"]["messages"]
       if m.get("role") == "assistant" and m.get("agent_key") == "attr_ag"]
check("GET /messages -> assistant carrega agent_name resolvido",
      bool(_gm) and _gm[-1].get("agent_name") == "Agente Atributo")

# (3) broadcast_tool_calls carimba o agent_key no card de tool
_attr_cm2 = _CM("5500019200002")
_attr_conv2 = _attr_cm2.ensure_conversation_live("user", None)
_asyncio.run(app.state.deps.broadcast_tool_calls(
    "5500019200002", [{"tool": "save_contact_info", "args": {}}],
    channel_id="default", agent_key="attr_ag"))
_tc_rows = [m for m in message_repo.get_by_conversation(_attr_conv2)
            if m.get("role") == "tool_call"]
check("broadcast_tool_calls -> card tool_call carimbado com agent_key",
      bool(_tc_rows) and _tc_rows[-1].get("agent_key") == "attr_ag")
r = client.get(f"/api/atendimentos/{_attr_conv2}/messages")
_gtc = [m for m in r.json()["data"]["messages"] if m.get("role") == "tool_call"]
check("GET /messages -> tool_call carrega agent_name",
      bool(_gtc) and _gtc[-1].get("agent_name") == "Agente Atributo")

# (4) config show_agent_name: default True, exposto e gravável
_cfg = client.get("/api/config").json()["data"]
check("config expõe show_agent_name default True", _cfg.get("show_agent_name") is True)
r = client.put("/api/config", json={"show_agent_name": False})
check("PUT /config show_agent_name=False -> 200", r.status_code == 200)
check("config show_agent_name persiste False",
      client.get("/api/config").json()["data"].get("show_agent_name") is False)
client.put("/api/config", json={"show_agent_name": True})  # restaura o default

# ══════════════════════════════════════════════════════════════════════
#  Caracterização do fluxo de chat (plano 50 F2) — fixa o comportamento
#  ATUAL antes de paginar (F3/F4). O baseline "traz tudo" é a asserção que
#  a F3 vai mudar de forma CONSCIENTE.
# ══════════════════════════════════════════════════════════════════════
section("Chat: caracterização pré-paginação (plano 50 F2)")

from server.pagination import PAGE_MSGS as _PAGE_MSGS  # cap de página do chat (50)

_pg_cm = _CM("5500050000001")
_pg_conv = _pg_cm.ensure_conversation_live("user", None)
# >120 mensagens nesta conversa (alterna user/assistant p/ ter ambos os papéis).
# ts realista/crescente a partir de agora: em produção id e ts crescem juntos (msgs
# inseridas em ordem temporal) — o keyset (cursor por id, order by ts,id) DEPENDE disso.
_PG_TOTAL = 130
_pg_base = time.time()
for _i in range(_PG_TOTAL):
    _role = "user" if _i % 2 == 0 else "assistant"
    message_repo.add(_pg_cm.id, _role, f"msg-{_i:03d}",
                     conversation_id=_pg_conv, ts=_pg_base + _i)
# O repo com limit=None traz TUDO (caminho legado byte-idêntico p/ callers internos):
# minhas 130 + o card conversation_event 'created' emitido na criação.
_pg_rows = message_repo.get_by_conversation(_pg_conv)
_pg_repo_total = len(_pg_rows)
check("F2: repo com limit=None traz TUDO (caminho legado intacto)",
      _pg_repo_total >= _PG_TOTAL, f"esperava >= {_PG_TOTAL}, veio {_pg_repo_total}")

# F3: o ENDPOINT agora pagina — devolve a PÁGINA mais recente (PAGE_MSGS) + has_more.
r = client.get(f"/api/atendimentos/{_pg_conv}/messages?mark_read=false")
check("F3: GET /messages -> 200", r.status_code == 200)
_pg_data = r.json()["data"]
# Shape: `messages` na raiz do data + `has_more` (novo).
check("F3: shape -> `messages` na raiz do data", isinstance(_pg_data.get("messages"), list))
check("F3: shape -> `has_more` presente", "has_more" in _pg_data)
_pg_msgs = _pg_data["messages"]
check("F3: página recente tem PAGE_MSGS msgs (não a thread inteira)",
      len(_pg_msgs) == _PAGE_MSGS, f"esperava {_PAGE_MSGS}, veio {len(_pg_msgs)}")
check("F3: conversa longa -> has_more=True", _pg_data["has_more"] is True)
# Ordem cronológica (ts crescente) DENTRO da página — invariante preservada.
_pg_ts = [m.get("ts") or 0 for m in _pg_msgs]
check("F3: página em ordem cronológica (ts crescente)", _pg_ts == sorted(_pg_ts))
check("F3: session_open presente na resposta", "session_open" in _pg_data)

# Caminhada keyset (F3 "Pronto quando"): before_id = menor id da página → anteriores.
# Reconstrói a thread inteira sem duplicar nem pular.
_pg_collected = list(_pg_msgs)
_pg_before = min(m["_id"] for m in _pg_msgs)
_pg_guard = 0
# Guarda proporcional ao total (cada página traz >=1 msg): sobrevive a PAGE_MSGS pequeno
# (ex.: valor de QA temporário) sem estourar antes de reconstruir a thread inteira.
while _pg_data["has_more"] and _pg_guard < _pg_repo_total + 5:
    _pg_guard += 1
    r = client.get(f"/api/atendimentos/{_pg_conv}/messages"
                   f"?mark_read=false&limit={_PAGE_MSGS}&before_id={_pg_before}")
    _pg_data = r.json()["data"]
    _page = _pg_data["messages"]
    check(f"F3 keyset: página before_id={_pg_before} não-vazia", len(_page) > 0)
    _pg_collected = _page + _pg_collected  # prepend (mais antigas na frente)
    if _page:
        _pg_before = min(m["_id"] for m in _page)
_pg_ids = [m["_id"] for m in _pg_collected]
check("F3 keyset: reconstruiu a thread inteira (sem dup/gap)",
      len(_pg_ids) == _pg_repo_total and len(set(_pg_ids)) == _pg_repo_total,
      f"coletado={len(_pg_ids)} unico={len(set(_pg_ids))} repo={_pg_repo_total}")
check("F3 keyset: última página -> has_more=False", _pg_data["has_more"] is False)
_pg_all_mine = [m["content"] for m in _pg_collected if str(m.get("content", "")).startswith("msg-")]
check("F3 keyset: minhas 130 msgs todas presentes e em ordem",
      _pg_all_mine == [f"msg-{i:03d}" for i in range(_PG_TOTAL)],
      f"veio {len(_pg_all_mine)} msgs minhas")

# Conversa CURTA (< PAGE_MSGS): devolve tudo + has_more=False. Nº de msgs proporcional
# ao PAGE_MSGS (deixa folga p/ o card conversation_event caber na mesma página) —
# robusto a um PAGE_MSGS pequeno (valor de QA temporário).
_pg_short_n = max(1, _PAGE_MSGS - 2)
_pg_short_cm = _CM("5500050000003")
_pg_short_conv = _pg_short_cm.ensure_conversation_live("user", None)
for _i in range(_pg_short_n):
    message_repo.add(_pg_short_cm.id, "user", f"s-{_i}",
                     conversation_id=_pg_short_conv, ts=time.time() + _i)
_r_short = client.get(f"/api/atendimentos/{_pg_short_conv}/messages?mark_read=false").json()["data"]
check("F3: conversa curta -> has_more=False", _r_short["has_more"] is False)
check("F3: conversa curta -> devolve todas (<= PAGE_MSGS)",
      len(_r_short["messages"]) <= _PAGE_MSGS and len(_r_short["messages"]) >= _pg_short_n)

# Caminho legado GET /api/contacts/{phone} também pagina (all-channels merge).
_r_cpath = client.get("/api/contacts/5500050000001?mark_read=false").json()["data"]
check("F3: /api/contacts/{phone} pagina (página recente <= PAGE_MSGS)",
      len(_r_cpath["messages"]) == _PAGE_MSGS)
check("F3: /api/contacts/{phone} devolve has_more", _r_cpath.get("has_more") is True)

# mark_read=false não zera o badge de não-lidas (comportamento preservado por F3).
_pg_cm2 = _CM("5500050000002")
_pg_conv2 = _pg_cm2.ensure_conversation_live("user", None)
message_repo.add(_pg_cm2.id, "user", "oi", conversation_id=_pg_conv2, ts=2_000_000)
_before = client.get(f"/api/atendimentos/{_pg_conv2}").json()["data"]["conversation"].get("unread_count", 0)
client.get(f"/api/atendimentos/{_pg_conv2}/messages?mark_read=false")
_after = client.get(f"/api/atendimentos/{_pg_conv2}").json()["data"]["conversation"].get("unread_count", 0)
check("F2: mark_read=false NÃO altera unread_count", _before == _after,
      f"antes={_before} depois={_after}")

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
# A conversa A (aberta pelo add_message acima) e a B não podem estar abertas ao
# mesmo tempo no mesmo par (uq_atend_open_contact_inbox) — fecha a A antes.
_conv_repo.set_status(_convA_id, "closed")
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
# Plano 36: is_default (padrão de novas conversas) trafega pelo endpoint (save/get) e
# é radio — marcar um novo rebaixa o anterior. O patch só-prompt preserva a flag.
client.put("/api/ai/agents/p36e_a", json={
    "display_name": "P36 A", "model_config": {"model": "openai/gpt-4o-mini"},
    "enabled": True, "is_default": True})
_p36 = {a["agent_key"]: a for a in client.get("/api/ai/agents").json()["data"]}
check("P36 GET expõe is_default=True no agente marcado", _p36.get("p36e_a", {}).get("is_default") is True)
client.put("/api/ai/agents/p36e_b", json={
    "display_name": "P36 B", "model_config": {"model": "openai/gpt-4o-mini"},
    "enabled": True, "is_default": True})
_p36 = {a["agent_key"]: a for a in client.get("/api/ai/agents").json()["data"]}
check("P36 marcar B como padrão rebaixa A (radio)", _p36.get("p36e_a", {}).get("is_default") is False)
check("P36 B é o novo padrão", _p36.get("p36e_b", {}).get("is_default") is True)
client.put("/api/ai/agents/p36e_b/prompt", json={"prompt": "novo prompt"})
check("P36 patch só-prompt preserva is_default",
      (_agent_repo.get("p36e_b") or {}).get("is_default") is True)
client.delete("/api/ai/agents/p36e_a")
# ── Fix agente-padrão (2026-07): a trava de excluir/desativar é SEMÂNTICA ──
# O fallback atual do sistema (o is_default habilitado, ou o 'default' legado
# sem marcação) é intocável; qualquer outro — inclusive o 'default' — sai.
r = client.delete("/api/ai/agents/p36e_b")
check("fix padrão: excluir o agente marcado (fallback atual) -> 400",
      r.status_code == 400)
r = client.put("/api/ai/agents/default", json={
    "display_name": "Agente padrão", "prompt_key": "default",
    "model_config": {"model": "anthropic/claude-sonnet-4-6"}, "enabled": False})
check("fix padrão: desativar 'default' com outro padrão marcado -> 200",
      r.status_code == 200)
check("fix padrão: 'default' ficou desabilitado",
      not (_agent_repo.get("default") or {}).get("enabled"))
r = client.put("/api/ai/agents/p36e_tmp_off", json={
    "display_name": "Tmp", "model_config": {"model": "openai/gpt-4o-mini"},
    "enabled": False, "is_default": True})
check("fix padrão: marcar is_default num agente desabilitado -> 400",
      r.status_code == 400)
# Desmarcar o padrão exige o piso legado vivo: com 'default' desabilitado -> 400.
r = client.put("/api/ai/agents/p36e_b", json={
    "display_name": "P36 B", "model_config": {"model": "openai/gpt-4o-mini"},
    "enabled": True, "is_default": False})
check("fix padrão: desmarcar sem piso legado habilitado -> 400",
      r.status_code == 400)
# Reabilita o 'default' → desmarcar passa a valer, e o ex-padrão vira excluível.
client.put("/api/ai/agents/default", json={
    "display_name": "Agente padrão", "prompt_key": "default",
    "model_config": {"model": "anthropic/claude-sonnet-4-6"}, "enabled": True})
r = client.put("/api/ai/agents/p36e_b", json={
    "display_name": "P36 B", "model_config": {"model": "openai/gpt-4o-mini"},
    "enabled": True, "is_default": False})
check("fix padrão: desmarcar com piso legado vivo -> 200", r.status_code == 200)
r = client.delete("/api/ai/agents/p36e_b")
check("fix padrão: excluir o ex-padrão desmarcado -> 200", r.status_code == 200)
# Invariante do agente default (Fase 5, agora semântico): sem nenhum is_default
# marcado o 'default' legado É o fallback — não pode ser desativado nem excluído.
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

# ── P31 F6 (A1): backend valida o nome da variável (só {identificador} renderiza) ──
r = client.put("/api/ai/variables/p31_nome_valido", json={"value": "acme"})
check("P31 PUT variável válida -> 200", r.status_code == 200)
# P31 F7 (A2): coluna morta 'category' removida de API/repo/schema (migration 0037).
check("P31 resposta do PUT sem 'category'",
      "category" not in (r.json().get("data") or {}))
_v31 = next((v for v in client.get("/api/ai/variables").json()["data"]
             if v["name"] == "p31_nome_valido"), None)
check("P31 GET variables lista a criada", _v31 is not None)
check("P31 GET variables sem campo 'category'",
      _v31 is not None and "category" not in _v31)
r = client.put("/api/ai/variables/nome-invalido", json={"value": "x"})
check("P31 PUT variável com hífen -> 400", r.status_code == 400)
r = client.put("/api/ai/variables/1comeca_numero", json={"value": "x"})
check("P31 PUT variável começando por número -> 400", r.status_code == 400)
r = client.put("/api/ai/variables/" + "a" * 65, json={"value": "x"})
check("P31 PUT variável nome >64 chars -> 400", r.status_code == 400)
r = client.put("/api/ai/variables/p31_x%0A", json={"value": "x"})
check("P31 PUT variável com newline final (%0A) -> 400", r.status_code == 400)
r = client.get("/api/ai/variables")
check("P31 variável inválida não foi persistida",
      not any(v["name"] == "nome-invalido" for v in r.json()["data"]))
r = client.delete("/api/ai/variables/p31_nome_valido")
check("P31 DELETE variável -> 200", r.status_code == 200)

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

# (d) ai off (conversa) -> aviso do grupo ai + card "assumiu" (P17: quem desliga
#     a IA assume a conversa); ai on -> grupo ai
_n = len(_notices(_snconv["id"]))
client.post(f"/api/conversations/{_snconv['id']}/ai", json={"active": False}, headers=_snhdr)
client.post(f"/api/conversations/{_snconv['id']}/ai", json={"active": True}, headers=_snhdr)
_after = _notices(_snconv["id"])
check("ai off/on -> 3 avisos (ai off + assumiu + ai on)", len(_after) - _n == 3)
check("ai off -> card 'assumiu a conversa'",
      any("assumiu a conversa" in c for c in _after[_n:]))

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
      any("IA assumiu a conversa" in c for c in _notices(_snconv["id"])))

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

# Paginação (plano 50 D) — o filtro devolve has_more e caminha por offset sem dup/gap.
_all = client.get("/api/conversations/filter?limit=200").json()["data"]["conversations"]
_all_ids = [c["id"] for c in _all]
if 2 <= len(_all_ids) < 200:
    _p1 = client.get("/api/conversations/filter?limit=1&offset=0").json()["data"]
    check("filter?limit=1 -> has_more True", _p1.get("has_more") is True)
    check("filter?limit=1 -> 1 item", len(_p1["conversations"]) == 1)
    # Caminha todas as páginas de 1 em 1 e reconstrói o universo sem duplicar.
    _walked, _off = [], 0
    while True:
        _pg = client.get(f"/api/conversations/filter?limit=1&offset={_off}").json()["data"]
        _rows = _pg["conversations"]
        _walked += [c["id"] for c in _rows]
        if not _pg.get("has_more") or not _rows:
            break
        _off += len(_rows)
    check("filter offset walk cobre o universo (ordem/sem dup)", _walked == _all_ids)
_big = client.get("/api/conversations/filter?limit=99999").json()["data"]
check("filter?limit=99999 -> capado (<=200)", len(_big["conversations"]) <= 200)

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

# plano 50 F9 — paginação de usage (envelope só com `limit`; sem ele = lista legada).
r = client.get("/api/usage/by-contact?limit=1&offset=0")
_ub = r.json()["data"]
check("F9: by-contact?limit -> envelope {items,total,has_more}",
      isinstance(_ub, dict) and "items" in _ub and "total" in _ub and "has_more" in _ub)
check("F9: by-contact página respeita limit", len(_ub["items"]) <= 1)
check("F9: by-contact ordenado por custo desc (top gastadores)",
      _ub["total"] >= 1)
# by_type da página vem preenchido (não perdido pela paginação).
if _ub["items"]:
    check("F9: by-contact item da página traz by_type", "by_type" in _ub["items"][0])
# Caminhar as páginas cobre o total sem dup.
_seen, _o, _g = set(), 0, 0
while _g < 200:
    _g += 1
    _p = client.get(f"/api/usage/by-contact?limit=2&offset={_o}").json()["data"]
    for _it in _p["items"]:
        _seen.add(_it["phone"])
    if not _p["has_more"]:
        break
    _o += 2
check("F9: by-contact paginação cobre o total", len(_seen) == _p["total"],
      f"vistos={len(_seen)} total={_p['total']}")
# detail paginado
r = client.get("/api/usage/contact/5511999990001?limit=1&offset=0")
_ud = r.json()["data"]
check("F9: contact/{phone}?limit -> envelope", isinstance(_ud, dict) and "items" in _ud)
check("F9: contact detail respeita limit", len(_ud["items"]) <= 1)
check("F9: contact detail total >= 2", _ud["total"] >= 2)
# sem limit -> lista legada (retrocompat)
check("F9: by-contact sem limit continua lista",
      isinstance(client.get("/api/usage/by-contact").json()["data"], list))
check("F9: contact detail sem limit continua lista",
      isinstance(client.get("/api/usage/contact/5511999990001").json()["data"], list))
# summary global intacto (não paginado)
check("F9: summary segue com total_tokens (agregado intacto)",
      "total_tokens" in client.get("/api/usage/summary").json()["data"])

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

# plano 50 F1 — limit livre é capado por clamp_limit (?limit=99999 nunca > cap 200)
r = client.get("/api/webhook-payloads?limit=99999")
check("GET /api/webhook-payloads?limit=99999 -> 200", r.status_code == 200)
check("GET /api/webhook-payloads?limit=99999 -> <= cap 200", len(r.json()["data"]) <= 200)
r = client.get("/api/executions?limit=99999")
check("GET /api/executions?limit=99999 -> 200", r.status_code == 200)
check("GET /api/executions?limit=99999 -> items <= cap 200",
      len(r.json()["data"]["items"]) <= 200)

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

# Inbound edit (cliente edita a própria mensagem): reflete no DB + broadcast.
_inb_cid = contact_repo.get_full_contact("5511999990001")["id"]
message_repo.add(_inb_cid, "user", "texto do cliente", msg_id="WAMID_INB_EDIT_1")
r = client.post("/api/webhook/gowa/default", json={
    "event": "message.edited",
    "payload": {
        "id": "WAMID_INB_EDIT_1",
        "original_message_id": "WAMID_INB_EDIT_1",
        "from": "5511999990001@s.whatsapp.net",
        "chat_id": "5511999990001@s.whatsapp.net",
        "body": "texto editado pelo cliente",
    },
})
check("POST /webhook (edited) -> 200", r.status_code == 200)
_ie = message_repo.get_by_msg_id("WAMID_INB_EDIT_1")
check("webhook edited -> content atualizado no DB",
      bool(_ie) and _ie.get("content") == "texto editado pelo cliente")
check("webhook edited -> edited_ts setado", bool(_ie) and _ie.get("edited_ts"))

# Inbound revoke (cliente apaga pra todos): mensagem fica flagged revoked.
message_repo.add(_inb_cid, "user", "vou apagar", msg_id="WAMID_INB_REV_1")
r = client.post("/api/webhook/gowa/default", json={
    "event": "message.revoked",
    "payload": {"revoked_message_id": "WAMID_INB_REV_1",
                "chat_id": "5511999990001@s.whatsapp.net"},
})
check("POST /webhook (revoked) -> 200", r.status_code == 200)
_ir = message_repo.get_by_msg_id("WAMID_INB_REV_1")
check("webhook revoked -> flagged revoked no DB", bool(_ir) and bool(_ir.get("revoked")))

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

# plano 33: GET /api/channels/providers devolve DESCRIPTORS (não só nomes), só dos
# providers registrados, com a forma que o frontend genérico consome.
_pr = client.get("/api/channels/providers")
check("GET /channels/providers -> 200", _pr.status_code == 200)
_prov_list = _pr.json().get("data", {}).get("providers", [])
_prov_by = {d.get("provider"): d for d in _prov_list if isinstance(d, dict)}
check("providers -> lista de descriptors (dicts, não strings)",
      bool(_prov_list) and all(isinstance(d, dict) for d in _prov_list))
_test_desc = _prov_by.get("test")
check("providers -> inclui o provider 'test' registrado", _test_desc is not None)
check("descriptor 'test' tem a forma base (label + credential_fields + capabilities)",
      _test_desc is not None
      and "label" in _test_desc
      and isinstance(_test_desc.get("credential_fields"), list)
      and isinstance(_test_desc.get("capabilities"), dict))
check("providers -> required_credentials é um dict por provider",
      isinstance(_pr.json()["data"].get("required_credentials"), dict))
check("providers -> provider NÃO registrado não aparece",
      "provider_que_nao_existe" not in _prov_by)

# plano 85 B2: desde o plano 76 H1 este endpoint é TAMBÉM o catálogo de APRESENTAÇÃO
# do hub (rótulo/cor/tipo de contato do selo de canal em toda linha da sidebar), então
# ele é gated por `conversation.reply`, não por `channel.manage` — um atendente sem
# gestão de canais ficava com os selos no fallback cinza por causa do 403. A ESCRITA
# continua em `channel.manage`. Sessão descartável (criar usuário liga o gate has_users):
# removida no fim para o intervalo seguinte voltar ao modo aberto.
_b2_at = _urepo.create(email="_b2_atendente@x.com", name="B2 Atendente",
                       password_hash=_hpa("supersecret"), role_keys=["atendente"])
_b2_h = {"Authorization": "Bearer " + client.post(
    "/api/auth/login", json={"email": "_b2_atendente@x.com", "password": "supersecret"}
).json()["data"]["token"]}
_b2_r = client.get("/api/channels/providers", headers=_b2_h)
check("plano 85 B2: atendente sem channel.manage -> GET /channels/providers 200",
      _b2_r.status_code == 200)
_b2_provs = _b2_r.json().get("data", {}).get("providers", [])
check("plano 85 B2: atendente recebe os descriptors", bool(_b2_provs))
check("plano 85 B2: descriptor não carrega valor de credencial",
      all("credentials" not in d for d in _b2_provs)
      and all("value" not in f
              for d in _b2_provs for f in (d.get("credential_fields") or [])))
check("plano 85 B2: atendente continua SEM criar canal (escrita segue channel.manage)",
      client.post("/api/channels",
                  json={"id": "b2_nope", "provider": "test", "display_name": "x"},
                  headers=_b2_h).status_code == 403)
_urepo.delete(_b2_at["id"])

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
_src_tg_plugin = plugin_source_or_skip("telegram", "canal Telegram (seção 19c)")
if _src_tg_plugin:
    section("Telegram plugin (plano 13)")

    import importlib.util as _ilu
    _tg_path = _src_tg_plugin / "channels.py"
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

    # plano 64 — a zona "Arquivo" do drop tem de chegar como ARQUIVO no Telegram.
    # Sem `disable_content_type_detection`, o servidor do Telegram detecta o .mp4
    # e o exibe com player, tornando a zona indistinguível da de vídeo.
    _tg_mp4 = os.path.join(_tmpdir, "clipe_tg.mp4")
    with open(_tg_mp4, "wb") as _f:
        _f.write(b"\x00\x00\x00\x18ftypmp42")
    _tg.send_media("555", "document", _tg_mp4, caption="cap", filename="clipe.mp4")
    check("telegram send_media(document) -> sendDocument",
          _tg_calls[-1][0] == "sendDocument")
    check("telegram sendDocument -> desliga a detecção de tipo do servidor",
          _tg_calls[-1][1].get("disable_content_type_detection") == "true")
    check("telegram sendDocument -> nome original preservado",
          (_tg_calls[-1][2] or {}).get("document", (None,))[0] == "clipe.mp4")

    _tg.send_media("555", "video", _tg_mp4, caption="cap")
    check("telegram send_media(video) -> sendVideo (zona foto/vídeo intacta)",
          _tg_calls[-1][0] == "sendVideo")
    check("telegram sendVideo -> NÃO manda o flag de documento",
          "disable_content_type_detection" not in (_tg_calls[-1][1] or {}))

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
_ged = _pgi({"event": "message.edited", "payload": {
    "id": "e1", "original_message_id": "e1", "body": "novo texto",
    "chat_id": "5511@s.whatsapp.net"}})[0]
check("gowa parse: edited -> kind edited",
      _ged.kind == "edited" and _ged.text == "novo texto"
      and _ged.media_extras.get("original_message_id") == "e1")
check("gowa parse: evento desconhecido -> []", _pgi({"event": "nope", "payload": {}}) == [])

_src_tg_edit = plugin_source_or_skip("telegram", "parse de edited_message")
if _src_tg_edit:
    # Telegram: edited_message vira um evento "edited" (não uma msg nova).
    _tg_mod = _load_provider("tg_edit_test", _src_tg_edit / "channels.py")
    _tg_ch = _tg_mod.TelegramChannel("t_edit", credentials={"bot_token": "1:x"})
    _tg_new = _tg_ch.parse_inbound({"message": {
        "message_id": 55, "date": 1, "text": "original",
        "chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Cli"}}})
    check("telegram parse: message normal -> kind message",
          len(_tg_new) == 1 and _tg_new[0].kind == "message")
    _tg_ed = _tg_ch.parse_inbound({"edited_message": {
        "message_id": 55, "date": 2, "text": "editado",
        "chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Cli"}}})
    check("telegram parse: edited_message -> kind edited",
          len(_tg_ed) == 1 and _tg_ed[0].kind == "edited"
          and _tg_ed[0].text == "editado"
          and _tg_ed[0].media_extras.get("original_message_id") == "55")

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

# ── Escopo do som de mensagem nova: o payload diz de QUEM é a conversa ─────────
# O painel só toca (e só mostra o pop-up) quando a conversa é do atendente logado
# ou não tem dono nenhum. A decisão é client-side (shouldNotifyNewMessage), mas
# depende destes dois campos virem no broadcast do ingest — é o que se testa aqui.
_deps_snd = app.state.deps
_orig_snd_bcast = _deps_snd.ws_manager.broadcast
_snd_msgs = []


async def _capture_new_message(event, data):
    if event == "new_message" and (data.get("message") or {}).get("role") == "user":
        _snd_msgs.append(data["message"])
    return await _orig_snd_bcast(event, data)


def _inbound_capture(phone: str, msg_id: str):
    """POST inbound pela rota real capturando os `new_message` de role user."""
    _snd_msgs.clear()
    _deps_snd.ws_manager.broadcast = _capture_new_message
    try:
        client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{phone}@s.whatsapp.net", "id": msg_id, "body": "escopo do som",
                "from_name": "Cliente Som"}})
    finally:
        _deps_snd.ws_manager.broadcast = _orig_snd_bcast
    # O t=0 (não-autoritativo) é o que dispara som/pop-up no painel.
    return next((m for m in _snd_msgs if not m.get("authoritative")), None)


# `assignee_user_id` é NULLABLE e SEM FK (db/tables.py) — aqui basta um id qualquer:
# o que se verifica é o campo chegar no payload, não a existência do usuário.
_snd_uid = 4242
_snd_phone = "5511707070055"
_contact11.get_or_create(_snd_phone)
_contact11.update(_contact11.get_by_phone(_snd_phone)["id"], ai_enabled=0)

_m0 = _inbound_capture(_snd_phone, "gw_som_1")
check("new_message (t=0) carrega assignee_user_id", _m0 is not None and "assignee_user_id" in _m0)
check("new_message (t=0) carrega active_agent_key", _m0 is not None and "active_agent_key" in _m0)

# Atribuída a alguém → o payload carrega o dono, e só ele toca no painel.
_snd_conv = _conv11.get(_m0["conversation_id"]) if _m0 else None
if _snd_conv:
    _conv11.set_assignee(_snd_conv["id"], _snd_uid)
_m1 = _inbound_capture(_snd_phone, "gw_som_2")
check("new_message -> assignee_user_id reflete o atendente da conversa",
      _m1 is not None and _m1.get("assignee_user_id") == _snd_uid)

# Sem atendente → assignee nulo (o painel toca para todos, se também não houver IA).
if _snd_conv:
    _conv11.set_assignee(_snd_conv["id"], None)
_m2 = _inbound_capture(_snd_phone, "gw_som_3")
check("new_message -> sem atendente devolve assignee_user_id nulo",
      _m2 is not None and _m2.get("assignee_user_id") is None)

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

# Plano 49 — "marcar como não lida" POR CONVERSA (simétrico ao read, isolado por canal).
# _cd acabou de ser lido (unread_count==0); _cf segue não-lido. Marcar _cd como não lida
# NÃO pode reacender/afetar _cf (o bug: os endpoints por-contato acendiam as duas).
_run = client.post(f"/api/atendimentos/{_cd['id']}/unread")
check("POST /api/atendimentos/{id}/unread -> 200", _run.status_code == 200)
check("POST conv unread -> marked=True", (_run.json().get("data") or {}).get("marked") is True)
check("mark_conversation_unread: reacende só a conversa marcada",
      (_conv11.get_with_channel(_cd["id"]) or {}).get("unread_count", 0) >= 1)
check("mark_conversation_unread: NÃO afeta a conversa do outro canal",
      (_conv11.get_with_channel(_cf["id"]) or {}).get("unread_count", 0) >= 1)
# Idempotente: 2ª chamada é no-op (unread_msg_ids não tem unique — não pode inflar/duplicar)
_before_cnt = (_conv11.get_with_channel(_cd["id"]) or {}).get("unread_count", 0)
_run2 = client.post(f"/api/atendimentos/{_cd['id']}/unread")
check("POST conv unread idempotente -> marked=False",
      (_run2.json().get("data") or {}).get("marked") is False)
check("mark_conversation_unread idempotente: contador por conversa não muda",
      (_conv11.get_with_channel(_cd["id"]) or {}).get("unread_count", 0) == _before_cnt)
# Endpoint de leitura por conversa (menu de contexto "marcar como lida")
_rrd = client.post(f"/api/atendimentos/{_cd['id']}/read")
check("POST /api/atendimentos/{id}/read -> 200", _rrd.status_code == 200)
check("conv read endpoint: zera só a conversa lida",
      (_conv11.get_with_channel(_cd["id"]) or {}).get("unread_count", 0) == 0)
check("conv read endpoint: NÃO zera o outro canal (D1)",
      (_conv11.get_with_channel(_cf["id"]) or {}).get("unread_count", 0) >= 1)
check("POST conv unread -> 404 em conversa inexistente",
      client.post("/api/atendimentos/99999/unread").status_code == 404)

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


def _service_number_unreachable():
    """Patch target: ``GET /service_number`` fora do ar.

    Sem isto o bloco abaixo dependeria de REDE. O core deixou de ter número de
    provisionamento embutido (era o literal "5513981744038" até 2026-08-20), então
    numa máquina sem internet o destino passaria a ser vazio e o envio seria
    recusado — o teste falharia por ambiente, não por regressão. Fixamos os dois
    lados: endpoint inacessível + env conhecida.
    """
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock(
        get=AsyncMock(side_effect=RuntimeError("sem rede"))))
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


# request-key: sends the provisioning WhatsApp message and arms polling
_send_calls_before = mock_gowa_client.send_message.call_count
with patch("app.services.provisioning_service.httpx.AsyncClient",
           _service_number_unreachable()), \
     patch("app.services.provisioning_service.TECHIFY_PROVISION_NUMBER",
           "5513981744038"):
    r = client.post("/api/setup/request-key")
check("POST /api/setup/request-key -> 200", r.status_code == 200)
check("POST /api/setup/request-key -> returns number",
      r.json()["data"].get("number") == "5511999990001")
check("POST /api/setup/request-key -> WhatsApp message sent",
      mock_gowa_client.send_message.call_count == _send_calls_before + 1)

# ...e sem destino nenhum (nem env, nem endpoint) NADA é enviado: o core não tem
# mais número embutido para servir de reserva.
_send_calls_before = mock_gowa_client.send_message.call_count
with patch("app.services.provisioning_service.httpx.AsyncClient",
           _service_number_unreachable()), \
     patch("app.services.provisioning_service.TECHIFY_PROVISION_NUMBER", ""):
    r = client.post("/api/setup/request-key")
check("POST /api/setup/request-key sem destino -> 400", r.status_code == 400)
check("POST /api/setup/request-key sem destino -> erro acionável",
      r.json()["ok"] is False and "destino" in r.json()["error"])
check("POST /api/setup/request-key sem destino -> nada enviado",
      mock_gowa_client.send_message.call_count == _send_calls_before)

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

# Sandbox video (plano 65) — stored locally, agent reacts to the caption.
with patch.object(agent_handler, "aprocess_message",
                  new=AsyncMock(return_value=ProcessResult(
                      reply="Vi o vídeo", tool_calls=[]))):
    r = client.post(
        "/api/sandbox/send-video",
        files={"video": ("clip.mp4", io.BytesIO(fake_mp4), "video/mp4")},
        data={"phone": "sandbox_test", "caption": "olha isso"},
    )
    check("POST /sandbox/send-video -> 200", r.status_code == 200)

r = client.post("/api/sandbox/clear", json={"phone": "sandbox_test"})
check("POST /sandbox/clear -> 200", r.status_code == 200)

r = client.post("/api/sandbox/clear", json={})
check("POST /sandbox/clear (all) -> 200", r.status_code == 200)

# ═══════════════════════════════════════════════════════════════════
#  22. Frontend routes (SPA)
# ═══════════════════════════════════════════════════════════════════
section("Frontend SPA Routes")

for path in ["/", "/contacts", "/dashboard", "/protocolos", "/attendances", "/audit", "/sandbox", "/costs",
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
#  23. Auth enforcement — o gate da API/WS fecha quando existe ≥1 usuário
#      (plano 48 F0 — substitui a antiga "Senha do Painel", já aposentada)
# ═══════════════════════════════════════════════════════════════════
section("Auth — enforcement (has_users)")
import json as _json23

# A suíte já criou um admin (bootstrap acima), então existe ≥1 usuário → o gate da
# API está FECHADO: um request sem sessão de usuário válida é recusado pelo
# middleware com 401, independentemente de qualquer senha de painel (aposentada).
# `anon` não carrega token; `client` carrega a sessão de admin default.
check("GET /api/config (no token) -> 401", anon.get("/api/config").status_code == 401)
check("GET /api/channels (no token) -> 401", anon.get("/api/channels").status_code == 401)
check("GET /api/users (no token) -> 401", anon.get("/api/users").status_code == 401)

# /api/auth/check é isento do middleware, mas o handler ainda responde 401 quando
# exige sessão e nenhuma é apresentada; carrega has_users=True.
r = anon.get("/api/auth/check")
check("GET /auth/check (no token) -> 401", r.status_code == 401)
check("GET /auth/check -> has_users=true", (r.json().get("data") or {}).get("has_users") is True)

# Prefixos de deep-link (SPA) ficam abertos mesmo sem token (o reload de uma URL de
# entidade serve o index.html) — mas o /api/* equivalente segue protegido.
check("GET /channels/default (SPA, no token) -> 200",
      anon.get("/channels/default").status_code == 200)
check("GET /ai/agents/default (SPA, no token) -> 200",
      anon.get("/ai/agents/default").status_code == 200)

# Endpoints isentos funcionam sem token.
check("POST /webhook (auth exempt, no token) -> 200",
      anon.post("/api/webhook/gowa/default", json={"event": "unknown"}).status_code == 200)
check("GET /health (auth exempt, no token) -> 200", anon.get("/health").status_code == 200)

# Com sessão de admin válida, o gate deixa passar.
check("GET /api/config (admin session) -> 200", client.get("/api/config").status_code == 200)

# O gate do WebSocket espelha o do HTTP (plano 48 F0): sem ?token= o servidor aceita
# e fecha com 4401; com sessão de usuário válida conecta e envia o status inicial.
try:
    with anon.websocket_connect("/ws") as _ws_noauth:
        _ws_noauth.receive_text()
    check("WS sem token -> fechado (4401)", False)  # never reached: gate must close it
except Exception as _ws_exc:
    # Assert the ACTUAL close code is 4401 (not merely "some exception") — a
    # different code (4403/1008) or an unrelated error must fail this.
    check("WS sem token -> fechado (4401)", getattr(_ws_exc, "code", None) == 4401)

with client.websocket_connect(f"/ws?token={_suite_admin_tok}") as _ws_ok:
    _ws_first = _json23.loads(_ws_ok.receive_text())
    check("WS com sessão de admin -> conecta (status inicial)", _ws_first.get("event") == "status")

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

# ── IP público autodeclarado pelo painel (plano 86) ─────────────────────
# O IP real morre num hop antes do proxy reverso, então o navegador informa o
# próprio IP público em X-Client-Public-IP. Vale SÓ para a auditoria (D4).
def _last_export_ip():
    rows = _audit_repo.query(limit=20, offset=0, action="data.export")
    return max(rows, key=lambda x: x["id"])["ip_address"] if rows else None


_XFF = {"X-Forwarded-For": "10.99.0.4"}   # cadeia de exemplo (só hop privado)

client.get("/api/audit/export?format=csv", headers={**_XFF, "X-Client-Public-IP": "200.1.2.3"})
check("audit grava o IP público declarado pelo painel", _last_export_ip() == "200.1.2.3")

client.get("/api/audit/export?format=csv", headers=_XFF)
check("sem o cabeçalho -> IP de rede, idêntico a antes", _last_export_ip() == "10.99.0.4")

client.get("/api/audit/export?format=csv", headers={**_XFF, "X-Client-Public-IP": "192.168.0.7"})
check("cabeçalho privado é ignorado -> IP de rede", _last_export_ip() == "10.99.0.4")

client.get("/api/audit/export?format=csv", headers={**_XFF, "X-Client-Public-IP": "nao-e-ip"})
check("cabeçalho com lixo é ignorado -> IP de rede", _last_export_ip() == "10.99.0.4")

# D4: o bucket do rate-limit de login NÃO pode se mover com o cabeçalho — senão
# bastaria variá-lo a cada tentativa para ganhar tentativas infinitas.
_rl_xff = {"X-Forwarded-For": "203.0.113.9"}   # bucket isolado dos demais testes
_rl_status = []
for _i in range(12):
    _rl = client.post("/api/auth/login",
                      json={"email": "admin@test.com", "password": "errada"},
                      headers={**_rl_xff, "X-Client-Public-IP": f"200.1.2.{_i + 10}"})
    _rl_status.append(_rl.status_code)
check("rate-limit de login não se move variando X-Client-Public-IP (D4)",
      429 in _rl_status, f"status={_rl_status}")

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

# ── Alerta sonoro de transferência ENTRE atendentes (agent_transfer_alert) ──────
# O backend emite `agent_transfer_alert` numa reatribuição PARA OUTRO humano; o
# frontend filtra pelo usuário logado para tocar só na aba de quem recebeu. Config
# global própria (agent_transfer_alert_enabled/_duration), independente do
# alerta IA→humano.
_deps_ata = app.state.deps
_orig_ata_bcast = _deps_ata.ws_manager.broadcast
_ata_events = []
async def _capture_ata(event, data):
    if event == "agent_transfer_alert":
        _ata_events.append(data)
    return await _orig_ata_bcast(event, data)

# GET /api/config expõe as duas chaves novas com defaults.
_cfg_ata = client.get("/api/config").json()["data"]
check("config -> agent_transfer_alert_enabled default True",
      _cfg_ata.get("agent_transfer_alert_enabled") is True)
check("config -> agent_transfer_alert_duration default 5",
      _cfg_ata.get("agent_transfer_alert_duration") == 5)

# Transferência para OUTRO atendente → dispara o alerta direcionado. Exercitada no
# SERVIÇO (actor != assignee): pelo HTTP o cliente de teste está logado como o
# próprio `_admin_id`, o que seria auto-atribuição e nunca soaria.
from app.services import conversation_service as _conv_svc

config_repo.set("agent_transfer_alert_enabled", True)
config_repo.set("agent_transfer_alert_duration", 7)


def _assign_via_service(actor_id=None):
    """Reatribui a conversa `_cid` capturando os broadcasts de alerta."""
    _ata_events.clear()
    _conv_ata = conversation_repo.get(_cid)
    _deps_ata.ws_manager.broadcast = _capture_ata
    try:
        _asyncio.run(_conv_svc.assign(_deps_ata, _conv_ata, _admin_id, actor_id=actor_id))
    finally:
        _deps_ata.ws_manager.broadcast = _orig_ata_bcast


r = client.post(f"/api/conversations/{_cid}/assign", json={"assignee_user_id": _admin_id})
check("assign p/ outro atendente -> 200", r.status_code == 200)
_assign_via_service()
check("assign p/ outro atendente -> agent_transfer_alert emitido",
      any(e.get("assignee_user_id") == _admin_id for e in _ata_events))
check("agent_transfer_alert -> carrega duration da config global",
      any(e.get("duration") == 7 for e in _ata_events))

# Auto-atribuição NÃO dispara o alerta (não é "de um atendente para outro"). Testado
# no serviço: (a) assign_me sempre é auto-atribuição; (b) assign com actor==assignee.
_ata_events.clear()
_deps_ata.ws_manager.broadcast = _capture_ata
try:
    _conv_me = conversation_repo.resolve_for_contact(_alice["id"], "5511999990001@s.whatsapp.net")
    _asyncio.run(_conv_svc.assign_me(_deps_ata, _conv_me, _admin_id))
    check("assign_me -> sem agent_transfer_alert", len(_ata_events) == 0)
    _conv_self = conversation_repo.resolve_for_contact(_alice["id"], "5511999990001@s.whatsapp.net")
    _asyncio.run(_conv_svc.assign(_deps_ata, _conv_self, _admin_id, actor_id=_admin_id))
    check("assign com actor==assignee -> sem agent_transfer_alert", len(_ata_events) == 0)
finally:
    _deps_ata.ws_manager.broadcast = _orig_ata_bcast

# Toggle desligado → nenhum alerta, mesmo transferindo para outro atendente.
config_repo.set("agent_transfer_alert_enabled", False)
_assign_via_service()
check("agent_transfer_alert desligado -> sem broadcast", len(_ata_events) == 0)
config_repo.set("agent_transfer_alert_enabled", True)

# Padrão da equipe (sound_settings) tem precedência sobre as keys legadas: é lá
# que a aba "Notificações e sons" grava ativação/duração dos alertas.
config_repo.set("sound_settings", {
    "master_enabled": True,
    "events": {"assigned_to_me": {"enabled": True, "duration": 11}},
})
_assign_via_service()
check("sound_settings duration vence a key legada",
      any(e.get("duration") == 11 for e in _ata_events))

config_repo.set("sound_settings", {
    "master_enabled": True,
    "events": {"assigned_to_me": {"enabled": False}},
})
_assign_via_service()
check("sound_settings enabled=False silencia mesmo com key legada ligada",
      len(_ata_events) == 0)
config_repo.set("sound_settings", {
    "master_enabled": True,
    "events": {"new_message": {"enabled": True, "sound": "ding", "volume": 0.6}},
})

# event_gate puro: sem o evento no padrão da equipe, o piso é a key legada.
from server import sound_catalog as _sc
check("event_gate: cai no legado quando o padrão não define",
      _sc.event_gate({"events": {}}, "ia_to_human",
                     legacy_enabled=True, legacy_duration=9) == (True, 9))
check("event_gate: padrão da equipe vence o legado",
      _sc.event_gate({"events": {"ia_to_human": {"enabled": False, "duration": 12}}},
                     "ia_to_human", legacy_enabled=True, legacy_duration=9) == (False, 12))
check("event_gate: entrada corrompida cai no legado (fail-open)",
      _sc.event_gate("lixo", "ia_to_human",
                     legacy_enabled=False, legacy_duration=4) == (False, 4))
from channels import ai_settings as _ai_settings_mod
check("ia_to_human deixou de ser per-canal",
      "transfer_alert_enabled" not in _ai_settings_mod.PER_CHANNEL_AI_KEYS)

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

# "Fulano está digitando…" entre ATENDENTES: a rota de presença do operador reemite
# o mesmo sinal como `operator_typing`, carimbado com quem digita, para os outros
# painéis logados. Efêmero (só WS) e escopado pela conversa — mesma chave que o
# indicador do cliente usa na linha da sidebar.
_captured_op = []
async def _capture_op(event, data):
    if event == "operator_typing":
        _captured_op.append(data)
    return await _orig_bcast(event, data)
_deps_pres.ws_manager.broadcast = _capture_op
try:
    r = client.post("/api/contacts/5511999990001/presence",
                    json={"action": "start", "conversation_id": _cid})
    r_stop = client.post("/api/contacts/5511999990001/presence",
                         json={"action": "stop", "conversation_id": _cid})
finally:
    _deps_pres.ws_manager.broadcast = _orig_bcast
check("POST /presence (start) -> 200", r.status_code == 200)
_op = _captured_op[0] if _captured_op else {}
check("operator_typing emitido no start", _op.get("active") is True)
check("operator_typing -> conversation_id da conversa", _op.get("conversation_id") == _cid)
check("operator_typing -> phone do contato", _op.get("phone") == "5511999990001")
check("operator_typing -> identifica o atendente logado",
      _op.get("user_id") is not None and bool(_op.get("user_name")))
check("POST /presence (stop) -> 200", r_stop.status_code == 200)
check("operator_typing -> active False no stop",
      len(_captured_op) == 2 and _captured_op[1].get("active") is False)

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

# plano 50 F13 — batch de etiquetas: UMA request cobre N conversas.
r = client.post("/api/atendimentos/labels-batch", json={"ids": [_conv2["id"], 999999]})
check("F13: labels-batch -> 200", r.status_code == 200)
_lb = r.json()["data"]["labels_by_conv"]
check("F13: labels-batch traz as etiquetas da conversa",
      {l["name"] for l in _lb.get(str(_conv2["id"]), [])} == {"Urgente", "VIP"})
check("F13: labels-batch ids desconhecidos -> lista vazia",
      _lb.get(str(999999), []) == [])
check("F13: labels-batch ids não-lista -> erro",
      client.post("/api/atendimentos/labels-batch", json={"ids": "x"}).json().get("ok") is False)
check("F13: labels-batch sem ids -> vazio ok",
      client.post("/api/atendimentos/labels-batch", json={}).json().get("ok") is True)
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


# Spec de forma do template. Antes do plano 92 estas regras eram constantes do
# CORE; agora quem as declara é o provider, e o core só avalia — então o fake
# precisa declarar as suas para os 400 de validação continuarem existindo.
from channels.base import TemplateSpec

_FAKE_TPL_SPEC = TemplateSpec(
    categories=frozenset({"UTILITY", "MARKETING", "AUTHENTICATION"}),
    header_formats=frozenset({"IMAGE", "VIDEO", "DOCUMENT"}),
    button_types=frozenset({"QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE"}),
    button_type_max={"URL": 2, "PHONE_NUMBER": 1, "COPY_CODE": 1},
    button_text_max=25,
    buttons_max=10,
    upload_mimes=frozenset({"image/jpeg", "image/png", "video/mp4", "video/3gpp",
                            "application/pdf"}),
    upload_max_bytes=16 * 1024 * 1024,
)


class _FakeTplChannel(_Ch):
    provider = "fake_cloud"

    def __init__(self, channel_id):
        super().__init__(channel_id, _Caps(templates=True, session_window_hours=24,
                                           template_spec=_FAKE_TPL_SPEC))
        self.sent = []
        self.created = None
        self.deleted = None
        self.uploaded = None
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
                        body_examples=None, header_examples=None,
                        header_format=None, header_handle=None, buttons=None):
        self.created = {"name": name, "category": category, "language": language,
                        "body_text": body_text, "header_text": header_text,
                        "footer_text": footer_text, "body_examples": body_examples,
                        "header_format": header_format,
                        "header_handle": header_handle, "buttons": buttons}
        return {"ok": True, "id": "TPLNEW", "status": "PENDING", "category": category}

    def upload_example(self, file_bytes, mime, filename):
        self.uploaded = {"size": len(file_bytes), "mime": mime, "filename": filename}
        return {"ok": True, "handle": "4:fakehandle=="}

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

# Plano 119 — o cabeçalho de mídia do template vira BOLHA no histórico. Antes o
# painel guardava só o texto do corpo: quem abrisse a conversa depois não tinha
# como saber qual imagem tinha saído.
_tpl_outbox = app.state.deps.statics_outbox_dir
_tpl_outbox.mkdir(parents=True, exist_ok=True)
(_tpl_outbox / "tpl_header.jpg").write_bytes(b"\xff\xd8\xff\xdb")


def _tpl_last_msg():
    with _get_engine().connect() as _c:
        return _c.execute(
            _sa_select(_msgs_t.c.content, _msgs_t.c.media_type, _msgs_t.c.media_path)
            .where(_msgs_t.c.contact_id == _cid)
            .order_by(_msgs_t.c.id.desc()).limit(1)).first()


r = client.post(f"/api/conversations/{_tpl_conv['id']}/send-template", json={
    "template_name": "boas_vindas", "language": "pt_BR",
    "preview_text": "Olá Alice, com imagem",
    "media_type": "image", "media_path": "statics/outbox/tpl_header.jpg"})
check("send-template com mídia -> 200", r.status_code == 200)
_m = _tpl_last_msg()
check("send-template com mídia -> linha vira bolha de imagem",
      _m is not None and _m[1] == "image" and _m[2] == "statics/outbox/tpl_header.jpg")
check("send-template com mídia -> texto do corpo continua na linha",
      _m is not None and "com imagem" in (_m[0] or ""))

# Path traversal NUNCA vira media_path: a mensagem grava como texto e o envio
# (que já chegou ao cliente) não vira erro na tela.
r = client.post(f"/api/conversations/{_tpl_conv['id']}/send-template", json={
    "template_name": "boas_vindas", "language": "pt_BR",
    "preview_text": "Olá Alice, sem imagem",
    "media_type": "image", "media_path": "statics/outbox/../../.env"})
check("send-template com caminho traversal -> 200 (recusa macia)", r.status_code == 200)
_m = _tpl_last_msg()
check("send-template com caminho traversal -> gravou texto puro",
      _m is not None and _m[1] is None and _m[2] is None)

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

check("create template só-texto -> não manda mídia/botões (regressão)",
      _fake_ch.created.get("header_format") is None
      and _fake_ch.created.get("header_handle") is None
      and _fake_ch.created.get("buttons") is None)

# ── Cabeçalho de mídia + botões (plano 73) ──────────────────────────────
r = client.post(f"/api/conversations/{_tpl_conv['id']}/templates", json={
    "name": "promo_midia", "category": "UTILITY", "body_text": "Olá!",
    "header_format": "IMAGE", "header_handle": "4:abc==",
    "buttons": [
        {"type": "QUICK_REPLY", "text": "Falar com atendente"},
        {"type": "URL", "text": "Rastrear", "url": "https://x/{{1}}", "example": "https://x/12"},
        {"type": "PHONE_NUMBER", "text": "Ligar", "phone_number": "+5511999999999"},
        {"type": "COPY_CODE", "example": "PROMO2026"},
    ]})
check("POST create template com mídia+botões -> 200", r.status_code == 200)
check("create template -> canal recebeu header de mídia",
      _fake_ch.created["header_format"] == "IMAGE"
      and _fake_ch.created["header_handle"] == "4:abc==")
check("create template -> botões normalizados (4 tipos, chaves por tipo)",
      [b["type"] for b in _fake_ch.created["buttons"]]
      == ["QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE"]
      and _fake_ch.created["buttons"][1]["example"] == ["https://x/12"]
      and "text" not in _fake_ch.created["buttons"][3])
check("create template header_format sem handle -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "x", "body_text": "y",
                        "header_format": "IMAGE"}).status_code == 400)
check("create template header_format inválido -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "x", "body_text": "y", "header_format": "AUDIO",
                        "header_handle": "4:z"}).status_code == 400)
check("create template botão de tipo desconhecido -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "x", "body_text": "y",
                        "buttons": [{"type": "FLOW", "text": "a"}]}).status_code == 400)
check("create template 3 botões URL -> 400 (limite de mistura)",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "x", "body_text": "y", "buttons": [
                      {"type": "URL", "text": "a", "url": "https://a"},
                      {"type": "URL", "text": "b", "url": "https://b"},
                      {"type": "URL", "text": "c", "url": "https://c"}]}).status_code == 400)
check("create template botão URL com {{1}} sem exemplo -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "x", "body_text": "y", "buttons": [
                      {"type": "URL", "text": "a", "url": "https://a/{{1}}"}]}).status_code == 400)
check("create template texto de botão > 25 chars -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "x", "body_text": "y", "buttons": [
                      {"type": "QUICK_REPLY", "text": "x" * 26}]}).status_code == 400)
check("create template buttons não-lista -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates",
                  json={"name": "x", "body_text": "y", "buttons": "nope"}).status_code == 400)

# Upload do exemplo de mídia (handle da Meta)
r = client.post(f"/api/conversations/{_tpl_conv['id']}/templates/upload-example",
                files={"file": ("foto.png", b"\x89PNG fake bytes", "image/png")})
check("POST upload-example (conv) -> 200 + handle", r.status_code == 200
      and r.json()["data"]["handle"] == "4:fakehandle==")
check("upload-example -> canal recebeu bytes/mime",
      _fake_ch.uploaded and _fake_ch.uploaded["mime"] == "image/png"
      and _fake_ch.uploaded["size"] > 0)
check("upload-example MIME fora da whitelist -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates/upload-example",
                  files={"file": ("a.exe", b"MZ", "application/x-msdownload")}
                  ).status_code == 400)
check("upload-example arquivo > 16 MiB -> 400",
      client.post(f"/api/conversations/{_tpl_conv['id']}/templates/upload-example",
                  files={"file": ("big.png", b"x" * (16 * 1024 * 1024 + 1), "image/png")}
                  ).status_code == 400)
check("upload-example canal sem templates -> 400",
      client.post(f"/api/conversations/{_conv2['id']}/templates/upload-example",
                  files={"file": ("foto.png", b"abc", "image/png")}).status_code == 400)
check("upload-example conversa inexistente -> 404",
      client.post("/api/conversations/999999/templates/upload-example",
                  files={"file": ("foto.png", b"abc", "image/png")}).status_code == 404)

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

# Criação estendida + upload de exemplo pelo canal (plano 73).
r = client.post("/api/channels/cloud_test/templates", json={
    "name": "canal_midia", "category": "UTILITY", "body_text": "Oi",
    "header_format": "DOCUMENT", "header_handle": "4:doc==",
    "buttons": [{"type": "QUICK_REPLY", "text": "Ok"}]})
check("channel create template com mídia+botões -> 200", r.status_code == 200)
check("channel create template -> canal recebeu mídia/botões",
      _fake_ch.created["header_format"] == "DOCUMENT"
      and _fake_ch.created["buttons"] == [{"type": "QUICK_REPLY", "text": "Ok"}])
check("channel create template header_format inválido -> 400",
      client.post("/api/channels/cloud_test/templates",
                  json={"name": "x", "body_text": "y", "header_format": "AUDIO",
                        "header_handle": "4:z"}).status_code == 400)
r = client.post("/api/channels/cloud_test/templates/upload-example",
                files={"file": ("foto.jpg", b"\xff\xd8 fake", "image/jpeg")})
check("channel upload-example -> 200 + handle", r.status_code == 200
      and r.json()["data"]["handle"] == "4:fakehandle==")
check("channel upload-example MIME inválido -> 400",
      client.post("/api/channels/cloud_test/templates/upload-example",
                  files={"file": ("a.txt", b"x", "text/plain")}).status_code == 400)
check("channel upload-example canal sem templates -> 400",
      client.post("/api/channels/default/templates/upload-example",
                  files={"file": ("foto.png", b"x", "image/png")}).status_code == 400)
check("channel upload-example canal inexistente -> 404",
      client.post("/api/channels/nao_existe/templates/upload-example",
                  files={"file": ("foto.png", b"x", "image/png")}).status_code == 404)

# ── Carregador genérico de provider de plugin (usado pelos blocos guardados) ──
# Fica FORA dos guards: os blocos que o chamam já resolveram a fonte do plugin.
import importlib.util as _p32_ilu
import sys as _p32_sys
import types as _p32_types


def _p32_load_provider(prov, clsname):
    # Carrega o channels.py do plugin como PACOTE (whatsbot_plugins.<prov>), igual
    # ao runtime — necessário porque providers como facebook_messenger importam a
    # base RELATIVAMENTE (plano 76·F9: `from .meta_graph import …`). Harmless para
    # quem não usa import relativo (whatsapp_cloud/telegram).
    plugin_dir = plugin_source_or_skip(prov, "dedup plano 32")
    if "whatsbot_plugins" not in _p32_sys.modules:
        _parent = _p32_types.ModuleType("whatsbot_plugins")
        _parent.__path__ = []
        _p32_sys.modules["whatsbot_plugins"] = _parent
    pkg_name = f"whatsbot_plugins.{prov}"
    if pkg_name not in _p32_sys.modules:
        _pkg = _p32_types.ModuleType(pkg_name)
        _pkg.__path__ = [str(plugin_dir)]
        _p32_sys.modules[pkg_name] = _pkg
    full = f"{pkg_name}.channels"
    spec = _p32_ilu.spec_from_file_location(full, plugin_dir / "channels.py")
    m = _p32_ilu.module_from_spec(spec)
    _p32_sys.modules[full] = m
    spec.loader.exec_module(m)
    return getattr(m, clsname)



_src_wac_tpl = plugin_source_or_skip("whatsapp_cloud", "templates, upload de mídia e dedup de conta")
_src_tg_dedup = plugin_source_or_skip("telegram", "templates, upload de mídia e dedup de conta")
if _src_wac_tpl and _src_tg_dedup:
    # ── WhatsAppCloudChannel.list_templates parsing (mock Graph API) ──
    section("WhatsApp Cloud — list_templates parsing (Frente C)")
    import importlib.util as _ilu
    _wac_spec = _ilu.spec_from_file_location(
        "wac_under_test", _src_wac_tpl / "channels.py")
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

    # ═══════════════════════════════════════════════════════════════════
    #  Account-identity dedup (plano 32) — create/update 409 enforcement
    # ═══════════════════════════════════════════════════════════════════
    section("Account-identity dedup (plano 32)")

    # The cloud/telegram plugins are DISABLED in the hermetic test app, so their
    # provider classes aren't registered — register them into the live registry now
    # (as production does when they're enabled) to exercise the generic dedup
    # enforcement end to end. Appended at the very end so it can't affect earlier tests.
    _p32_reg = app.state.deps.channel_registry
    _p32_reg.register_provider(_p32_load_provider("whatsapp_cloud", "WhatsAppCloudChannel"))
    _p32_reg.register_provider(_p32_load_provider("telegram", "TelegramChannel"))

    # plano 33: os providers reais agora aparecem no endpoint com descriptor completo
    # (credential_fields + capabilities + post_create) — a base do form dinâmico.
    _pr33 = client.get("/api/channels/providers").json()["data"]
    _by33 = {d["provider"]: d for d in _pr33["providers"]}
    check("descriptor telegram registrado -> aparece com bot_token required",
          "telegram" in _by33
          and any(f["key"] == "bot_token" and f.get("required")
                  for f in _by33["telegram"]["credential_fields"])
          and _by33["telegram"]["post_create"]["kind"] == "autoconfigure")
    check("descriptor whatsapp_cloud -> creds + templates + webhook_url pós-criação",
          "whatsapp_cloud" in _by33
          and {f["key"] for f in _by33["whatsapp_cloud"]["credential_fields"]}
              >= {"access_token", "app_secret", "phone_number_id", "verify_token"}
          and any(f["key"] == "app_secret" and f.get("required")
                  for f in _by33["whatsapp_cloud"]["credential_fields"])
          and _by33["whatsapp_cloud"]["capabilities"]["templates"] is True
          and _by33["whatsapp_cloud"]["post_create"]["kind"] == "webhook_url")
    check("required_credentials reflete saúde operacional, não migração de create",
          set(_pr33["required_credentials"].get("whatsapp_cloud", []))
          == {"access_token", "phone_number_id", "verify_token"})

    r = client.post("/api/channels", json={
        "id": "p32_cloud_sem_secret", "provider": "whatsapp_cloud",
        "display_name": "Cloud inseguro",
        "credentials": {"access_token": "tokX", "phone_number_id": "PN_NO_SECRET",
                        "verify_token": "vtX"}})
    check("cloud novo sem app_secret -> 400", r.status_code == 400)

    # whatsapp_cloud: two channels with the same phone_number_id -> 409
    _p32_cloud = {"access_token": "tokA", "app_secret": "secA",
                  "phone_number_id": "PN_DEDUP_1", "verify_token": "vtA"}
    r = client.post("/api/channels", json={
        "id": "p32_cloud_a", "provider": "whatsapp_cloud", "display_name": "Cloud A",
        "credentials": _p32_cloud})
    check("dedup: 1º cloud (phone_number_id novo) -> 200", r.status_code == 200)
    r = client.post("/api/channels", json={
        "id": "p32_cloud_b", "provider": "whatsapp_cloud", "display_name": "Cloud B",
        "credentials": {"access_token": "tokB", "app_secret": "secB",
                        "phone_number_id": "PN_DEDUP_1", "verify_token": "vtB"}})
    check("dedup: 2º cloud mesmo phone_number_id -> 409", r.status_code == 409)

    # Different phone_number_id -> OK
    r = client.post("/api/channels", json={
        "id": "p32_cloud_c", "provider": "whatsapp_cloud", "display_name": "Cloud C",
        "credentials": {"access_token": "tokC", "app_secret": "secC",
                        "phone_number_id": "PN_DEDUP_2", "verify_token": "vtC"}})
    check("dedup: cloud phone_number_id diferente -> 200", r.status_code == 200)

    # telegram: same bot_id (parsed from {bot_id}:{hash}) -> 409
    r = client.post("/api/channels", json={
        "id": "p32_tg_a", "provider": "telegram", "display_name": "TG A",
        "credentials": {"bot_token": "700700:AAA"}})
    check("dedup: 1º telegram (bot novo) -> 200", r.status_code == 200)
    r = client.post("/api/channels", json={
        "id": "p32_tg_b", "provider": "telegram", "display_name": "TG B",
        "credentials": {"bot_token": "700700:BBB"}})  # same bot_id 700700
    check("dedup: 2º telegram mesmo bot_id -> 409", r.status_code == 409)

    # Same numeric value across DIFFERENT providers/kinds is NOT a duplicate.
    r = client.post("/api/channels", json={
        "id": "p32_cloud_num", "provider": "whatsapp_cloud", "display_name": "Cloud Num",
        "credentials": {"access_token": "tokN", "app_secret": "secN",
                        "phone_number_id": "700700", "verify_token": "vtN"}})
    check("dedup: mesmo valor em provider/kind diferente -> 200 (não é duplicata)",
          r.status_code == 200)

    # PUT edit-to-collide: change Cloud C's phone_number_id to the one Cloud A owns -> 409
    r = client.put("/api/channels/p32_cloud_c",
                   json={"credentials": {"phone_number_id": "PN_DEDUP_1"}})
    check("dedup: PUT editar credencial para colidir -> 409", r.status_code == 409)

    # PUT an unrelated field (no credential change) -> not blocked
    r = client.put("/api/channels/p32_cloud_c", json={"display_name": "Cloud C renomeado"})
    check("dedup: PUT campo não-credencial -> 200", r.status_code == 200)

    # PUT to the channel's OWN identity is not a self-conflict
    r = client.put("/api/channels/p32_cloud_c",
                   json={"credentials": {"phone_number_id": "PN_DEDUP_2"}})
    check("dedup: PUT para a própria identidade -> 200", r.status_code == 200)


    # ── Menções em nota privada (colaboração estilo Chatwoot) ────────────────────────
    # @menção de atendente/time numa nota privada grava linhas em `mentions` (por-usuário),
    # emite `mention_created`, alimenta `has_user_mention` e a aba Menções. A partir do
    # bootstrap a suíte roda como admin (default header — plano 48 F0), então a nota é
    # autorada pelo admin; as peças por-usuário (has_user_mention, unread_count) são
    # checadas via repo com o user_id explícito (independem do current_user do request).
section("Contacts — Private Note Mentions")
from db.repositories import user_repo as _mrepo_users, mention_repo, inbox_member_repo, conversation_repo as _mrepo_conv

_u_a = _mrepo_users.create(email="mention_a@test.com", name="Atendente A",
                           password_hash="x", role_keys=["atendente"])
_u_b = _mrepo_users.create(email="mention_b@test.com", name="Atendente B",
                           password_hash="x", role_keys=["atendente"])
_mn_phone = "5511999990088"
_mentionee = _u_a["id"]
_mentionee_before = mention_repo.unread_count(_mentionee)

r = client.post(f"/api/contacts/{_mn_phone}/private-message",
                json={"text": "por favor confirmar este caso", "mentions": [_mentionee]})
check("POST /private-message com mentions -> 200", r.status_code == 200)
_mn_conv = (r.json().get("data") or {}).get("conversation_id")
check("private-message mentions -> devolve conversation_id", _mn_conv is not None)
check("mention_repo.unread_count incrementa para o mencionado",
      mention_repo.unread_count(_mentionee) == _mentionee_before + 1)

_row_for_mentionee = _mrepo_conv.get_with_channel(_mn_conv, _mentionee)
check("has_user_mention=True para o usuário mencionado",
      bool((_row_for_mentionee or {}).get("has_user_mention")) is True)
_row_for_none = _mrepo_conv.get_with_channel(_mn_conv, None)
check("has_user_mention=False sem usuário (broadcast/anônimo)",
      bool((_row_for_none or {}).get("has_user_mention")) is False)

_cleared = mention_repo.mark_read(_mentionee, _mn_conv)
check("mention_repo.mark_read limpa a menção", _cleared >= 1)
check("unread_count volta ao baseline após ler",
      mention_repo.unread_count(_mentionee) == _mentionee_before)
_row_after = _mrepo_conv.get_with_channel(_mn_conv, _mentionee)
check("has_user_mention=False após abrir/ler",
      bool((_row_after or {}).get("has_user_mention")) is False)

# @time = membros da caixa de entrada da conversa (uma menção por membro).
_inbox_id = (_row_for_mentionee or {}).get("inbox_id")
_team_ids = [_u_a["id"], _u_b["id"]]
if _inbox_id is not None:
    inbox_member_repo.set_members(_inbox_id, _team_ids)
    _before_team = {uid: mention_repo.unread_count(uid) for uid in _team_ids}
    r = client.post(f"/api/contacts/{_mn_phone}/private-message",
                    json={"text": "time, olhem isso", "mention_inbox": True})
    check("POST /private-message mention_inbox -> 200", r.status_code == 200)
    check("mention_inbox gera menção para cada membro da caixa",
          all(mention_repo.unread_count(uid) == _before_team[uid] + 1 for uid in _team_ids))

# Anexos privados: imagem + documento viram nota privada (media_type/path), 200.
_img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
r = client.post(f"/api/contacts/{_mn_phone}/private-image",
                files={"image": ("nota.png", _img, "image/png")},
                data={"caption": "print do erro"})
check("POST /private-image -> 200", r.status_code == 200)
check("private-image -> nota com media_type=image",
      (r.json().get("data") or {}).get("media_type") == "image")

r = client.post(f"/api/contacts/{_mn_phone}/private-document",
                files={"document": ("relatorio.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"caption": ""})
check("POST /private-document -> 200", r.status_code == 200)
check("private-document -> nota com media_type=document",
      (r.json().get("data") or {}).get("media_type") == "document")


# ══════════════════════════════════════════════════════════════════════════════
# Excluir o canal 'default' (plano exclui-default) — DESTRUTIVO, roda por ÚLTIMO:
# arquiva/purga o default, zera a lista de canais e recria. Não deixar nada depois
# que dependa do canal/inbox default.
# ══════════════════════════════════════════════════════════════════════════════
from db.repositories import channel_repo as _chrepo_x, inbox_repo as _ibxrepo_x
from app.services.message_ingest_service import _read_gowa_allowed_jid_types as _read_jid_x
from channels import jid as _jid_x
from agent import memory as _mem_x

# 1) Arquivar (soft-delete) o default: antes retornava 400 (protegido), agora 200.
r = client.delete("/api/channels/default")
check("DELETE /channels/default -> 200 (não mais protegido)", r.status_code == 200)
check("DELETE /channels/default -> archived=True",
      (r.json().get("data") or {}).get("archived") is True)
_arch = client.get("/api/channels?archived=true").json()["data"]
_arch_ids = {c["id"] for c in (_arch if isinstance(_arch, list) else _arch.get("channels", []))}
check("default arquivado aparece em ?archived=true", "default" in _arch_ids)
_live = client.get("/api/channels").json()["data"]
_live_ids = {c["id"] for c in (_live if isinstance(_live, list) else _live.get("channels", []))}
check("default arquivado some da lista viva", "default" not in _live_ids)
r = client.post("/api/channels/default/restore")
check("POST /channels/default/restore -> 200", r.status_code == 200)

# 2) Purgar TODOS os canais restantes, incluindo o default → estado zero-canais.
for _c in _chrepo_x.list_all(include_archived=True):
    client.delete(f"/api/channels/{_c['id']}?purge=true")
_live = client.get("/api/channels").json()["data"]
_live_list = _live if isinstance(_live, list) else _live.get("channels", [])
check("purge de todos -> GET /channels vazio", _live_list == [])
check("purge do default -> sem inbox órfã 'default'",
      _ibxrepo_x.get_by_channel("default") is None)

# 3) Inbound com zero canais: degrada gracioso (200 ignored), sem inbox-fantasma.
r = client.post("/api/webhook/gowa/default",
                json={"from": "5511999990099@s.whatsapp.net", "message": {"text": "oi"}})
check("webhook inbound zero-canais -> 200 (nunca 500)", r.status_code == 200)
check("webhook inbound zero-canais -> status=ignored",
      (r.json().get("data") or {}).get("status") == "ignored")
check("webhook inbound zero-canais -> NÃO cria inbox 'default' (anti-fantasma)",
      _ibxrepo_x.get_by_channel("default") is None)

# 4) allowed_jid_types com o default removido -> default permissivo, sem crash.
check("_read_gowa_allowed_jid_types cai no default permissivo",
      _read_jid_x() == list(_jid_x.DEFAULT_ALLOWED_JID_TYPES))

# 5) Recriar um canal após a exclusão: id gerado != 'default', inbox criada, resolve ok.
# GOWA não exige credenciais (fluxo QR), então recria sem creds — como o default original.
r = client.post("/api/channels",
                json={"provider": "gowa", "display_name": "Novo Canal"})
check("POST /channels após zero-canais -> 200", r.status_code == 200)
_new_id = (r.json().get("data") or {}).get("id")
check("canal recriado tem id gerado != 'default'", bool(_new_id) and _new_id != "default")
_inbx = client.get("/api/inboxes").json()["data"]
_inbx = _inbx if isinstance(_inbx, list) else _inbx.get("inboxes", [])
check("canal recriado -> inbox existe", any(i["channel_id"] == _new_id for i in _inbx))
_mem_x.invalidate_channel_caches(_new_id)
check("resolve_inbox_id(novo canal) -> inbox do canal",
      _mem_x.resolve_inbox_id(_new_id) is not None)


# ── Plano 76 · F0/F5 — mascaramento de credencial na borda da API ────────────
# CARACTERIZAÇÃO + contrato: o que é PÚBLICO sai em claro (o form de edição
# precisa pré-preencher), o resto é mascarado. A regra deixou de ser a lista
# NON_SECRET_CRED_KEYS e passou a ser o descriptor do provider
# (credential_fields[].type == "text"), com uma GUARDA DE NOME que mascara
# qualquer chave que "cheire" a segredo mesmo declarada como texto.
_p76_reg = app.state.deps.channel_registry


def _p76_creds(channel_id):
    return (client.get(f"/api/channels/{channel_id}").json()["data"] or {}).get("credentials", {})


# Cloud e Messenger vêm da loja de plugins; o contrato genérico de mascaramento
# continua coberto abaixo pelo provider fictício _P76GuardChannel.
_src_p76_wac = plugin_source_or_skip("whatsapp_cloud", "mascaramento de credencial (plano 76)")
_src_p76_fb = plugin_source_or_skip("facebook_messenger", "mascaramento de credencial (plano 76)")
if _src_p76_wac and _src_p76_fb:
    r = client.post("/api/channels", json={
        "id": "p76_cloud", "provider": "whatsapp_cloud", "display_name": "Cloud P76",
        "credentials": {"access_token": "EAAsegredoLongo1234",
                        "app_secret": "appsecret_cloud_p76", "phone_number_id": "PN_P76",
                        "waba_id": "WABA_P76", "verify_token": "vtok_p76"}})
    check("P76: cria canal cloud -> 200", r.status_code == 200)
    _c76 = _p76_creds("p76_cloud")
    check("P76: phone_number_id (type=text) em claro", _c76.get("phone_number_id") == "PN_P76")
    check("P76: waba_id (type=text) em claro", _c76.get("waba_id") == "WABA_P76")
    check("P76: access_token (type=secret) mascarado",
          _c76.get("access_token", "").startswith("••••") and "segredo" not in _c76.get("access_token", ""))
    check("P76: app_secret (type=secret) mascarado",
          _c76.get("app_secret", "").startswith("••••")
          and "appsecret" not in _c76.get("app_secret", ""))
    check("P76: verify_token (token_suggest) mascarado",
          _c76.get("verify_token", "").startswith("••••"))

    # Messenger: page_id é identificador público (type=text); os dois segredos, não.
    _p76_fb = _p32_load_provider("facebook_messenger", "FacebookMessengerChannel")
    _p76_reg.register_provider(_p76_fb)
    r = client.post("/api/channels", json={
        "id": "p76_fb", "provider": "facebook_messenger", "display_name": "Página P76",
        "credentials": {"page_id": "PAGE_P76", "page_access_token": "EAApageSegredo987",
                        "app_secret": "appsecret_p76", "verify_token": "vt_p76"}})
    check("P76: cria canal messenger -> 200", r.status_code == 200)
    _f76 = _p76_creds("p76_fb")
    check("P76: page_id (type=text) em claro", _f76.get("page_id") == "PAGE_P76")
    check("P76: page_access_token mascarado", _f76.get("page_access_token", "").startswith("••••"))
    check("P76: app_secret mascarado", _f76.get("app_secret", "").startswith("••••"))

# Guarda de nome (F5 item 4): um provider fictício declara uma credencial
# ``type: "text"`` cujo NOME cheira a segredo (api_token) — o core mascara mesmo
# assim (um plugin de terceiro que erre o type não pode virar vazamento).
class _P76GuardChannel(_Channel):
    provider = "p76guard"

    def __init__(self, channel_id, registry=None, credentials=None):
        super().__init__(channel_id, _Caps(qr=False, templates=False, inbound_route="path"))

    @classmethod
    def provider_descriptor(cls):
        return {
            "provider": "p76guard", "label": "Guard P76", "color": "gray",
            "credential_fields": [
                {"key": "public_id", "label": "ID público", "type": "text", "required": True},
                {"key": "api_token", "label": "Token (erro de type)", "type": "text", "required": True},
            ],
            "config_fields": [], "capabilities": {"needs_qr": False, "templates": False},
        }

    def status(self):
        return {"connected": True, "logged_in": True, "needs_qr": False, "error": None}

    def send_text(self, chat_id, text, *, reply_to=None, mentions=None):
        return _SendResult(ok=True, external_msg_id="x")

    def send_media(self, chat_id, kind, path_or_url, *, caption="", filename=None):
        return _SendResult(ok=True, external_msg_id="x")

    def parse_inbound(self, raw):
        return []


_p76_reg.register_provider(_P76GuardChannel)
r = client.post("/api/channels", json={
    "id": "p76_guard", "provider": "p76guard", "display_name": "Guard",
    "credentials": {"public_id": "PUB_P76", "api_token": "segredo_vazando_123"}})
check("P76 guarda: cria canal -> 200", r.status_code == 200)
_g76 = _p76_creds("p76_guard")
check("P76 guarda: public_id (type=text) em claro", _g76.get("public_id") == "PUB_P76")
check("P76 guarda: api_token (type=text mas nome-segredo) MASCARADO",
      _g76.get("api_token", "").startswith("••••") and "segredo" not in _g76.get("api_token", ""))

print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed, {len(skipped)} blocks skipped")
print(f"{'='*60}")

if skipped:
    print("\nBlocos pulados (plugin distribuído pela loja, fora deste repositório):")
    for s in skipped:
        print(f"  - {s}")

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
