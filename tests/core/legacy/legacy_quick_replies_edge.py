"""Edge-case validation for quick replies (respostas rápidas) — QA supplement.

Covers gaps NOT exercised by tests/test_endpoints.py: empty/whitespace content,
length limits (short_code 40, content 5000), PUT on a missing id, short_code that
normalizes to empty ('/'), and the autocomplete-feeding GET shape. Standalone:
roda contra o Postgres de teste (schema resetado), mirroring test_endpoints.py.
"""

import os
import sys
from tests.paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

from unittest.mock import MagicMock

from tests.pg import init_test_engine
init_test_engine(reset=True)

from config.settings import Settings
from agent.handler import AgentHandler
from server.app import create_app

gowa_manager = MagicMock()
gowa_client = MagicMock()
gowa_client.send_message = MagicMock(return_value=None)
gowa_client.get_own_number = MagicMock(return_value="5511999990001")

settings = Settings()
agent_handler = AgentHandler(
    api_key="test-key-fake",
    max_context_messages=10,
)
app = create_app(
    settings=settings,
    gowa_manager=gowa_manager,
    gowa_client=gowa_client,
    agent_handler=agent_handler,
)

from contextlib import asynccontextmanager

@asynccontextmanager
async def _noop_lifespan(app):
    yield

app.router.lifespan_context = _noop_lifespan

from starlette.testclient import TestClient
client = TestClient(app, raise_server_exceptions=False)

_passed = 0
_failed = 0

def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK {label}")
    else:
        _failed += 1
        print(f"  XX FAIL {label}")

print("Quick Replies — edge cases (QA supplement)")

# 1. Empty content rejected
r = client.post("/api/quick-replies", json={"short_code": "vazio", "content": ""})
check("content vazio -> erro", r.json().get("ok") is False)

# 2. Whitespace-only content rejected (stripped to empty)
r = client.post("/api/quick-replies", json={"short_code": "espacos", "content": "    \n\t  "})
check("content só-espaços -> erro", r.json().get("ok") is False)

# 3. short_code that is only a slash normalizes to '' -> obrigatório
r = client.post("/api/quick-replies", json={"short_code": "/", "content": "oi"})
check("short_code '/' -> erro obrigatório", r.json().get("ok") is False)

# 4. short_code over 40 chars rejected
r = client.post("/api/quick-replies", json={"short_code": "a" * 41, "content": "oi"})
check("short_code > 40 chars -> erro", r.json().get("ok") is False)

# 5. short_code exactly 40 chars accepted
r = client.post("/api/quick-replies", json={"short_code": "a" * 40, "content": "oi"})
check("short_code == 40 chars -> ok", r.json().get("ok") is True)

# 6. content over 5000 chars rejected
r = client.post("/api/quick-replies", json={"short_code": "longo", "content": "x" * 5001})
check("content > 5000 chars -> erro", r.json().get("ok") is False)

# 7. content exactly 5000 chars accepted
r = client.post("/api/quick-replies", json={"short_code": "max5k", "content": "x" * 5000})
check("content == 5000 chars -> ok", r.json().get("ok") is True)

# 8. PUT on a non-existent id -> 404
r = client.put("/api/quick-replies/999999", json={"content": "nada"})
check("PUT id inexistente -> 404", r.status_code == 404)

# 9. Accented short_code rejected by regex safety net
r = client.post("/api/quick-replies", json={"short_code": "saudação", "content": "oi"})
check("short_code com acento -> erro", r.json().get("ok") is False)

# 10. Uppercase + leading slash normalized (mirrors /HORARIO but with hyphen)
r = client.post("/api/quick-replies", json={"short_code": "/OI-EZE", "content": "Olá! Sou o Ezequiel."})
check("short_code '/OI-EZE' -> normaliza p/ 'oi-eze'",
      r.json().get("ok") and r.json()["data"]["short_code"] == "oi-eze")

# 11. PUT updating a row to its OWN short_code (no-op change) is allowed
_data = r.json().get("data") or {}
_id = _data.get("id")
r = client.put(f"/api/quick-replies/{_id}", json={"short_code": "oi-eze", "content": "Atualizado."})
check("PUT mesmo short_code (sem colisão consigo) -> ok",
      r.json().get("ok") and r.json()["data"]["content"] == "Atualizado.")

# 12. GET shape feeds autocomplete: each row has short_code + content
r = client.get("/api/quick-replies")
rows = r.json().get("data") or []
check("GET -> linhas têm short_code e content",
      all("short_code" in x and "content" in x for x in rows) and len(rows) > 0)

print(f"\n  RESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
