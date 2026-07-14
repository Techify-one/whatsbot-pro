"""Testes do plugin ``retorno_automatico`` (Plano 48).

Rodar (banco de teste próprio do plano 48):

    WHATSBOT_TEST_DB_URL="postgresql+psycopg://.../whatsbot_test_48?sslmode=require" \
        venv/bin/python -m pytest tests/test_retorno_automatico.py -q

O plugin é 100% self-contained em ``storages/plugins/retorno_automatico/`` — como o
harness ``build_test_app`` copia plugins de ``assets/plugin_examples/`` (que o plano 48
NÃO pode tocar), estes testes exercitam os módulos do plugin DIRETO (import por
``sys.path``) e, para os caminhos de app real (carrega limpo / rota de settings),
montam um app hermético cujo ``storages/plugins`` contém só este plugin.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

# ── Import dos módulos do plugin (self-contained em storages/plugins) ──────────
# NÃO adicionamos ``storages/plugins`` ao ``sys.path`` — isso faria o diretório do
# PLUGIN ``gowa`` sombrear o pacote CORE ``gowa`` (``from gowa.client import ...``).
# Em vez disso, registramos o pacote ``retorno_automatico`` por importlib (como o
# loader do core faz com ``whatsbot_plugins.<id>``), sem poluir o sys.path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = PROJECT_ROOT / "storages" / "plugins" / "retorno_automatico"
# O plugin é gitignored (distribuído por .zip). Num worktree limpo sem ele instalado,
# vira SKIP claro em vez de erro de coleta (que poluiria um `pytest tests/` inteiro).
if not (PLUGIN_DIR / "plugin.yaml").is_file():
    pytest.skip("plugin retorno_automatico não instalado em storages/plugins "
                "(distribuído por .zip)", allow_module_level=True)

_PKG = "retorno_automatico"
if _PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _PKG, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)])
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

from retorno_automatico import schedule as ra_schedule  # noqa: E402
from retorno_automatico import logic as ra_logic  # noqa: E402
from retorno_automatico import settings as ra_settings  # noqa: E402
from retorno_automatico import lifecycle as ra_lifecycle  # noqa: E402

NOTE_AUTHOR = ra_logic.NOTE_AUTHOR
CFG_PREFIX = f"plugin.{ra_logic.PLUGIN_ID}."


# ── Helpers de seeding (usam os repos do core direto) ─────────────────────────

def _raw_update_conv(conv_id: int, **cols) -> None:
    from db.engine import get_engine
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    with get_engine().begin() as conn:
        conn.execute(text(f"UPDATE atendimentos SET {sets} WHERE id = :cid"),
                     {**cols, "cid": conv_id})


def _mk_conversation(phone: str, *, agent_key: str | None = "default",
                     ai_active: int = 1, assignee_user_id: int | None = None,
                     is_group: bool = False, status: str = "open",
                     is_archived: int = 0):
    """Cria contato + conversa aberta e devolve ``(contact_id, conv_id)``.

    Espelha o padrão de ``tests/test_human_gate.py``: resolve a conversa no inbox
    default e ajusta agente/assignee/ai_active para casar a elegibilidade do plugin.
    """
    from db.repositories import contact_repo, conversation_repo

    c = contact_repo.get_or_create(phone)
    if is_group:
        contact_repo.update(c["id"], is_group=True, group_name="Grupo Teste")
    conversation_repo.resolve_for_contact(
        c["id"], f"{phone}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(c["id"])
    conversation_repo.assign_agent(
        conv["id"], assignee_user_id=assignee_user_id,
        active_agent_key=agent_key, ai_active=ai_active)
    cols = {}
    if status != "open":
        cols["status"] = status
    if is_archived:
        cols["is_archived"] = is_archived
    if cols:
        _raw_update_conv(conv["id"], **cols)
    return c["id"], conv["id"]


def _add_msg(contact_id: int, conv_id: int, role: str, ts: float, *,
             status: str | None = None, sent_by_name: str | None = None,
             content: str = "x"):
    from db.repositories import message_repo
    return message_repo.add(
        contact_id, role, content, ts=ts, status=status,
        sent_by_name=sent_by_name, conversation_id=conv_id)


def _note_count(conv_id: int) -> int:
    """Nº de notas do plugin (sent_by_name=NOTE_AUTHOR) numa conversa."""
    from db.engine import get_engine
    with get_engine().connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM messages WHERE conversation_id = :cid "
                 "AND role = 'private_note' AND sent_by_name = :a"),
            {"cid": conv_id, "a": NOTE_AUTHOR},
        ).scalar()
    return int(n or 0)


def _set_cfg(**kw) -> None:
    from db.repositories import config_repo
    for k, v in kw.items():
        config_repo.set(CFG_PREFIX + k, v)


def _tz_for_local_hour(target_hour: int) -> float:
    """Offset (horas) que faz a hora LOCAL virar ~``target_hour`` agora (real).

    Deixa ``is_open_now`` DETERMINÍSTICO em testes que rodam contra o relógio real,
    sem depender de que dia/hora a suíte roda.
    """
    utc = datetime.now(timezone.utc)
    tz = target_hour - utc.hour
    while tz < -12:
        tz += 24
    while tz > 14:
        tz -= 24
    return float(tz)


def _open_config(*, open_now: bool = True, silence_minutes: int = 15,
                 max_notes: int = 3, apply_to_groups: bool = False) -> None:
    """Config do plugin com expediente FORÇADO aberto/fechado p/ o instante real."""
    if open_now:
        tz, start, end = _tz_for_local_hour(12), "06:00", "18:00"
    else:
        tz, start, end = _tz_for_local_hour(3), "08:00", "18:00"
    _set_cfg(enabled=True, silence_minutes=silence_minutes,
             max_consecutive_notes=max_notes,
             business_start=start, business_end=end, tz_offset_hours=tz,
             day_mon=True, day_tue=True, day_wed=True, day_thu=True,
             day_fri=True, day_sat=True, day_sun=True,
             apply_to_groups=apply_to_groups)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mk_conv(_engine_ready):
    """Factory que cria conversas e as torna INELEGÍVEIS no teardown (isolamento).

    ``run_cycle`` varre TODAS as conversas do banco; desligar ``ai_active`` no fim de
    cada teste evita que uma conversa elegível de um teste polua o ``run_cycle`` do
    próximo."""
    created: list[int] = []

    def _factory(phone: str, **kw):
        cid, conv = _mk_conversation(phone, **kw)
        created.append(conv)
        return cid, conv

    yield _factory
    from db.repositories import conversation_repo
    for conv in created:
        try:
            conversation_repo.set_ai_active(conv, 0)
        except Exception:
            pass


@pytest.fixture
def wired_deps(_engine_ready):
    """Wire ``get_deps().agent_handler`` (dispatch precisa dele)."""
    from agent.handler import AgentHandler
    from plugins.context import get_deps, set_deps

    ah = AgentHandler(api_key="test-key-fake", max_context_messages=10,
                      default_ai_enabled=True)
    prev = get_deps()
    set_deps(SimpleNamespace(agent_handler=ah))
    yield ah
    set_deps(prev)


@pytest.fixture(scope="module")
def plugin_http(_engine_ready):
    """App hermético REAL cujo ``storages/plugins`` contém só este plugin.

    Espelha ``tests.support.build_test_app`` mas copiando de ``storages/plugins``
    (o harness padrão copia de ``assets/plugin_examples``, intocável no plano 48)."""
    from unittest.mock import MagicMock

    from config.settings import Settings
    from agent.handler import AgentHandler
    from db.repositories import plugin_repo
    from plugins.manifest import load_manifest
    from server.app import create_app
    from starlette.testclient import TestClient

    tmp = tempfile.TemporaryDirectory(prefix="whatsbot_p48_")
    data_dir = Path(tmp.name)
    plugins_dir = data_dir / "storages" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "assets" / "plugin_examples").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (data_dir / "statics").mkdir(parents=True, exist_ok=True)

    shutil.copytree(PLUGIN_DIR, plugins_dir / "retorno_automatico",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    manifest = load_manifest(plugins_dir / "retorno_automatico")
    plugin_repo.upsert(manifest.id, manifest.version, enabled=True)

    settings = Settings()
    settings.data_dir = data_dir
    settings.logs_dir = data_dir / "logs"

    agent_handler = AgentHandler(api_key="test-key-fake", max_context_messages=10,
                                 default_ai_enabled=True)
    application = create_app(settings=settings, gowa_manager=MagicMock(),
                             gowa_client=MagicMock(), agent_handler=agent_handler)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    application.router.lifespan_context = _noop_lifespan
    client = TestClient(application, raise_server_exceptions=True)
    client.__enter__()
    try:
        yield client
    finally:
        try:
            client.__exit__(None, None, None)
        except Exception:
            pass
        tmp.cleanup()


# ══════════════════════════════════════════════════════════════════════════════
# F0 — Scaffold: o plugin carrega limpo (sem load_error) pelo loader real.
# ══════════════════════════════════════════════════════════════════════════════

def test_f0_plugin_loads_clean(_engine_ready, tmp_path):
    from db.repositories import plugin_repo
    from plugins.loader import discover_and_load

    staging = tmp_path / "plugins"
    staging.mkdir()
    shutil.copytree(PLUGIN_DIR, staging / "retorno_automatico",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    discover_and_load(staging)
    plugin_repo.set_enabled("retorno_automatico", True)
    registry = discover_and_load(staging)

    assert "retorno_automatico" in registry.loaded, "plugin não carregou"
    row = plugin_repo.get("retorno_automatico")
    assert row is not None and not row.get("load_error"), row
    loaded = registry.loaded["retorno_automatico"]
    assert loaded.settings_cls is not None, "settings_cls não exposta"
    assert loaded.setup_fn is not None and loaded.teardown_fn is not None


def test_f0_card_no_load_error_via_app(plugin_http):
    r = plugin_http.get("/api/plugins")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True, body
    plugins = body["data"] if isinstance(body["data"], list) else body["data"].get("plugins", [])
    mine = [p for p in plugins if p.get("id") == "retorno_automatico"]
    assert mine, f"plugin não listado: {plugins}"
    assert not mine[0].get("load_error"), mine[0]


# ══════════════════════════════════════════════════════════════════════════════
# F1 — Settings declarativas: schema + persistência + vale no ciclo seguinte.
# ══════════════════════════════════════════════════════════════════════════════

def test_f1_settings_get_schema_and_values(plugin_http):
    r = plugin_http.get("/api/plugins/retorno_automatico/settings")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    props = data["schema"]["properties"]
    for field in ("enabled", "silence_minutes", "max_consecutive_notes",
                  "business_start", "business_end", "day_mon", "day_sun",
                  "tz_offset_hours", "apply_to_groups", "note_template"):
        assert field in props, f"campo {field} ausente no schema"
    # Defaults refletidos nos valores.
    assert data["values"]["silence_minutes"] == 180
    assert data["values"]["max_consecutive_notes"] == 3


def test_f1_settings_put_persists(plugin_http):
    payload = {"silence_minutes": 45, "max_consecutive_notes": 5,
               "business_start": "09:00", "day_sat": True}
    r = plugin_http.put("/api/plugins/retorno_automatico/settings", json=payload)
    assert r.status_code == 200, r.text
    r2 = plugin_http.get("/api/plugins/retorno_automatico/settings")
    vals = r2.json()["data"]["values"]
    assert vals["silence_minutes"] == 45
    assert vals["max_consecutive_notes"] == 5
    assert vals["business_start"] == "09:00"
    assert vals["day_sat"] is True
    # Persistido no namespace correto.
    from db.repositories import config_repo
    assert config_repo.get(CFG_PREFIX + "silence_minutes") == 45


def test_f1_load_settings_reflects_config(_engine_ready):
    _set_cfg(silence_minutes=120, max_consecutive_notes=7, tz_offset_hours=-3.0,
             note_template="oi {cliente}")
    cfg = ra_logic.load_settings()
    assert cfg.silence_minutes == 120
    assert cfg.max_consecutive_notes == 7
    assert cfg.tz_offset_hours == -3.0
    assert cfg.note_template == "oi {cliente}"
    # Campos não salvos herdam o default declarativo.
    assert cfg.business_end == "18:00"


def test_f1_load_settings_bad_config_falls_back(_engine_ready):
    from db.repositories import config_repo
    config_repo.set(CFG_PREFIX + "silence_minutes", "não-é-número")
    cfg = ra_logic.load_settings()  # não levanta
    assert cfg.silence_minutes == 180  # default
    config_repo.delete_prefix(CFG_PREFIX + "silence_minutes")


# ══════════════════════════════════════════════════════════════════════════════
# F2 — Expediente (schedule.py, puro).
# ══════════════════════════════════════════════════════════════════════════════

def test_f2_parse_hhmm():
    assert ra_schedule.parse_hhmm("08:00") == 480
    assert ra_schedule.parse_hhmm("18:30") == 1110
    assert ra_schedule.parse_hhmm("00:00") == 0
    assert ra_schedule.parse_hhmm("23:59") == 1439
    for bad in ("24:00", "12:60", "abc", "", "8", "8:5:0", None, "-1:00"):
        assert ra_schedule.parse_hhmm(bad) is None, bad


_WED_NOON = datetime(2026, 1, 7, 12, 0, tzinfo=timezone.utc).timestamp()  # quarta 12:00 UTC


def _cfg(**kw):
    base = {"business_start": "08:00", "business_end": "18:00",
            "tz_offset_hours": 0.0,
            "day_mon": True, "day_tue": True, "day_wed": True, "day_thu": True,
            "day_fri": True, "day_sat": False, "day_sun": False}
    base.update(kw)
    return base


def test_f2_is_open_weekday_midday():
    assert ra_schedule.is_open_now(_WED_NOON, _cfg()) is True


def test_f2_is_open_day_toggle_off():
    # Domingo desligado (day_sun=False por padrão).
    sun_noon = datetime(2026, 1, 11, 12, 0, tzinfo=timezone.utc).timestamp()
    assert ra_schedule.is_open_now(sun_noon, _cfg()) is False


def test_f2_is_open_before_start():
    t = datetime(2026, 1, 7, 7, 59, tzinfo=timezone.utc).timestamp()
    assert ra_schedule.is_open_now(t, _cfg()) is False


def test_f2_is_open_end_exclusive():
    t_end = datetime(2026, 1, 7, 18, 0, tzinfo=timezone.utc).timestamp()
    assert ra_schedule.is_open_now(t_end, _cfg()) is False  # 18:00 exclusivo
    t_in = datetime(2026, 1, 7, 17, 59, tzinfo=timezone.utc).timestamp()
    assert ra_schedule.is_open_now(t_in, _cfg()) is True


def test_f2_is_open_tz_offset_shifts_window():
    # 10:00 UTC; com fuso −3 → 07:00 local (antes das 08) → fechado.
    t = datetime(2026, 1, 7, 10, 0, tzinfo=timezone.utc).timestamp()
    assert ra_schedule.is_open_now(t, _cfg(tz_offset_hours=-3.0)) is False
    assert ra_schedule.is_open_now(t, _cfg(tz_offset_hours=0.0)) is True


def test_f2_is_open_start_ge_end_closed():
    assert ra_schedule.is_open_now(_WED_NOON,
                                   _cfg(business_start="18:00", business_end="08:00")) is False


def test_f2_is_open_bad_hhmm_closed():
    assert ra_schedule.is_open_now(_WED_NOON, _cfg(business_start="xx")) is False


# ══════════════════════════════════════════════════════════════════════════════
# F3 — Leituras: list_candidates + conversation_signals.
# ══════════════════════════════════════════════════════════════════════════════

def test_f3_list_candidates_eligibility(mk_conv):
    _, elig = mk_conv("5511480030001")
    _, no_agent = mk_conv("5511480030002", agent_key=None)
    _, has_human = mk_conv("5511480030003", assignee_user_id=42)
    _, ai_off = mk_conv("5511480030004", ai_active=0)
    _, closed = mk_conv("5511480030005", status="closed")
    _, archived = mk_conv("5511480030006", is_archived=1)
    _, group = mk_conv("120363480030007", is_group=True)

    ids = {c["conversation_id"] for c in ra_logic.list_candidates(apply_to_groups=False)}
    assert elig in ids
    for excluded in (no_agent, has_human, ai_off, closed, archived, group):
        assert excluded not in ids, excluded

    ids_g = {c["conversation_id"] for c in ra_logic.list_candidates(apply_to_groups=True)}
    assert group in ids_g, "grupo deveria entrar com apply_to_groups=True"
    assert elig in ids_g


def test_f3_conversation_signals(mk_conv):
    now = time.time()
    cid, conv = mk_conv("5511480031001")
    _add_msg(cid, conv, "user", now - 200)                       # cliente
    _add_msg(cid, conv, "assistant", now - 100, status="sent")   # IA (âncora)
    _add_msg(cid, conv, "assistant", now - 50, status="failed")  # falho → NÃO âncora
    _add_msg(cid, conv, "private_note", now - 30, sent_by_name=NOTE_AUTHOR)
    _add_msg(cid, conv, "private_note", now - 10, sent_by_name=NOTE_AUTHOR)
    _add_msg(cid, conv, "private_note", now - 5, sent_by_name="Operador João")  # outro autor

    s = ra_logic.conversation_signals(conv)
    assert abs(s["last_client_ts"] - (now - 200)) < 0.01
    assert abs(s["last_outbound_ts"] - (now - 100)) < 0.01, "falho não pode virar âncora"
    assert abs(s["last_note_ts"] - (now - 10)) < 0.01
    assert s["notes_since_reply"] == 2, "só notas do plugin, após o último inbound"


def test_f3_signals_note_before_reply_not_counted(mk_conv):
    now = time.time()
    cid, conv = mk_conv("5511480031002")
    _add_msg(cid, conv, "assistant", now - 300, status="sent")
    _add_msg(cid, conv, "private_note", now - 100, sent_by_name=NOTE_AUTHOR)
    _add_msg(cid, conv, "user", now - 20)  # cliente respondeu DEPOIS da nota

    s = ra_logic.conversation_signals(conv)
    assert s["notes_since_reply"] == 0, "nota anterior ao último inbound não conta"


def test_f3_signals_empty_conversation(mk_conv):
    _, conv = mk_conv("5511480031003")
    s = ra_logic.conversation_signals(conv)
    assert s == {"last_client_ts": None, "last_outbound_ts": None,
                 "last_note_ts": None, "notes_since_reply": 0}


# ══════════════════════════════════════════════════════════════════════════════
# F4 — Decisão (pura).
# ══════════════════════════════════════════════════════════════════════════════

_DEC_CFG = _cfg(silence_minutes=180, max_consecutive_notes=3,
                tz_offset_hours=0.0)  # aberto em _WED_NOON


def _sig(**kw):
    base = {"last_client_ts": None, "last_outbound_ts": None,
            "last_note_ts": None, "notes_since_reply": 0}
    base.update(kw)
    return base


def test_f4_decide_skip_no_outbound():
    d = ra_logic.decide(_WED_NOON, _sig(), _DEC_CFG)
    assert d.fire is False and d.reason == "sem_saida"


def test_f4_decide_skip_client_replied():
    s = _sig(last_outbound_ts=_WED_NOON - 10000, last_client_ts=_WED_NOON - 100)
    d = ra_logic.decide(_WED_NOON, s, _DEC_CFG)
    assert d.fire is False and d.reason == "cliente_respondeu"


def test_f4_decide_skip_limit():
    s = _sig(last_outbound_ts=_WED_NOON - 10000, notes_since_reply=3)
    d = ra_logic.decide(_WED_NOON, s, _DEC_CFG)
    assert d.fire is False and d.reason == "limite_atingido"


def test_f4_decide_skip_not_due():
    s = _sig(last_outbound_ts=_WED_NOON - 60)  # 60s < 3h
    d = ra_logic.decide(_WED_NOON, s, _DEC_CFG)
    assert d.fire is False and d.reason == "nao_venceu"


def test_f4_decide_skip_note_anchor_not_due():
    # Venceu pela saída, mas uma nota recente re-ancora → ainda não venceu (idempotência).
    s = _sig(last_outbound_ts=_WED_NOON - 10000, last_note_ts=_WED_NOON - 60)
    d = ra_logic.decide(_WED_NOON, s, _DEC_CFG)
    assert d.fire is False and d.reason == "nao_venceu"


def test_f4_decide_skip_outside_hours():
    s = _sig(last_outbound_ts=_WED_NOON - 20000)  # >3h → vencido; só o expediente barra
    closed_cfg = _cfg(silence_minutes=180, business_start="18:00", business_end="23:00",
                      tz_offset_hours=0.0)  # 12:00 < 18:00 → fechado
    d = ra_logic.decide(_WED_NOON, s, closed_cfg)
    assert d.fire is False and d.reason == "fora_expediente"


def test_f4_decide_fire_tentativa():
    s = _sig(last_outbound_ts=_WED_NOON - 20000, notes_since_reply=1)  # >3h → vencido
    d = ra_logic.decide(_WED_NOON, s, _DEC_CFG)
    assert d.fire is True and d.reason == "fire"
    assert d.tentativa == 2  # notes_since_reply + 1


# ══════════════════════════════════════════════════════════════════════════════
# F4 — run_cycle + dispatch (integração com DB e memória).
# ══════════════════════════════════════════════════════════════════════════════

def test_f4_run_cycle_fires_then_idempotent(mk_conv, wired_deps):
    _open_config(silence_minutes=15)  # 900s
    now = time.time()
    cid, conv = mk_conv("5511480040001")
    _add_msg(cid, conv, "assistant", now - 4000, status="sent")  # IA falou; cliente sumiu

    s1 = ra_logic.run_cycle()
    assert s1["fired"] >= 1
    assert _note_count(conv) == 1, "deve postar exatamente 1 nota"

    ra_logic.run_cycle()  # 2º tick: âncora ≈ nota recém-postada → não duplica
    assert _note_count(conv) == 1, "2º tick não pode duplicar (idempotência)"


def test_f4_operator_counts_as_anchor(mk_conv, wired_deps):
    """D2: resposta manual do operador conta igual à da IA (vira âncora)."""
    _open_config(silence_minutes=15)
    now = time.time()
    cid, conv = mk_conv("5511480040002")
    _add_msg(cid, conv, "assistant", now - 4000, status="operator",
             sent_by_name="Atendente")

    ra_logic.run_cycle()
    assert _note_count(conv) == 1


def test_f4_failed_send_not_anchor(mk_conv, wired_deps):
    """Envio 'failed' não conta como âncora → sem saída válida → não dispara."""
    _open_config(silence_minutes=15)
    now = time.time()
    cid, conv = mk_conv("5511480040003")
    _add_msg(cid, conv, "assistant", now - 4000, status="failed")

    ra_logic.run_cycle()
    assert _note_count(conv) == 0


def test_f4_client_reply_resets_then_new_cycle(mk_conv, wired_deps):
    _open_config(silence_minutes=15)
    now = time.time()
    cid, conv = mk_conv("5511480040004")
    _add_msg(cid, conv, "assistant", now - 4000, status="sent")
    _add_msg(cid, conv, "private_note", now - 3500, sent_by_name=NOTE_AUTHOR)
    _add_msg(cid, conv, "user", now - 3000)  # cliente respondeu → reset

    ra_logic.run_cycle()
    assert _note_count(conv) == 1, "cliente respondeu → não posta nova nota"

    # IA re-engaja depois; cliente volta a sumir → novo ciclo pode disparar.
    _add_msg(cid, conv, "assistant", now - 2000, status="sent")
    ra_logic.run_cycle()
    assert _note_count(conv) == 2, "após reset + nova saída, dispara de novo"


def test_f4_limit_standby(mk_conv, wired_deps):
    """D3: ao atingir max_consecutive_notes, entra em standby (não posta mais)."""
    _open_config(silence_minutes=15, max_notes=2)
    now = time.time()
    cid, conv = mk_conv("5511480040005")
    _add_msg(cid, conv, "assistant", now - 5000, status="sent")
    _add_msg(cid, conv, "private_note", now - 3000, sent_by_name=NOTE_AUTHOR)
    _add_msg(cid, conv, "private_note", now - 2000, sent_by_name=NOTE_AUTHOR)

    ra_logic.run_cycle()
    assert _note_count(conv) == 2, "limite atingido → standby, sem 3ª nota"


def test_f4_groups_excluded_by_default(mk_conv, wired_deps):
    _open_config(silence_minutes=15, apply_to_groups=False)
    now = time.time()
    cid, conv = mk_conv("120363480040006", is_group=True)
    _add_msg(cid, conv, "assistant", now - 4000, status="sent")

    ra_logic.run_cycle()
    assert _note_count(conv) == 0, "grupo não deve receber nota com apply_to_groups=False"


def test_f4_outside_hours_defers_then_fires(mk_conv, wired_deps):
    """D1: fora do expediente NÃO dispara; dispara no 1º tick dentro do expediente."""
    now = time.time()
    cid, conv = mk_conv("5511480040007")
    _add_msg(cid, conv, "assistant", now - 4000, status="sent")

    _open_config(open_now=False, silence_minutes=15)  # expediente fechado agora
    ra_logic.run_cycle()
    assert _note_count(conv) == 0, "fora do expediente não dispara"

    _open_config(open_now=True, silence_minutes=15)   # abriu o expediente
    ra_logic.run_cycle()
    assert _note_count(conv) == 1, "dispara no 1º tick dentro do expediente"


def test_f4_disabled_master_switch(mk_conv, wired_deps):
    _open_config(silence_minutes=15)
    _set_cfg(enabled=False)
    now = time.time()
    cid, conv = mk_conv("5511480040008")
    _add_msg(cid, conv, "assistant", now - 4000, status="sent")

    summary = ra_logic.run_cycle()
    assert summary.get("enabled") is False
    assert _note_count(conv) == 0
    _set_cfg(enabled=True)


def test_f4_dispatch_note_template_render(mk_conv, wired_deps):
    _open_config(silence_minutes=30)
    _set_cfg(note_template="Cliente {cliente} sumiu há {tempo} ({minutos}min) — tentativa {tentativa}/{max}")
    now = time.time()
    from db.repositories import contact_repo
    cid, conv = mk_conv("5511480040009")
    contact_repo.update(cid, name="Maria")
    _add_msg(cid, conv, "assistant", now - 4000, status="sent")

    ra_logic.run_cycle()
    from db.repositories import message_repo
    notes = [m for m in message_repo.get_by_conversation(conv)
             if m["role"] == "private_note" and m.get("sent_by_name") == NOTE_AUTHOR]
    assert notes, "nenhuma nota postada"
    body = notes[-1]["content"]
    assert "Maria" in body and "30min" in body and "1/3" in body, body


def test_f4_human_duration_formats():
    hd = ra_logic._human_duration
    assert hd(1) == "1min"
    assert hd(30) == "30min"
    assert hd(45) == "45min"
    assert hd(60) == "1h"
    assert hd(90) == "1h30min"
    assert hd(180) == "3h"


# ══════════════════════════════════════════════════════════════════════════════
# F5 — Scheduler supervisado (spawn_task PERMANENT).
# ══════════════════════════════════════════════════════════════════════════════

def test_f5_scheduler_registers_and_stops(_engine_ready):
    import plugins.context as pctx
    from plugins.context import PluginContext, broadcast
    from runtime.supervisor import TaskSupervisor

    _set_cfg(enabled=False)  # ciclo inerte durante o teste

    prev_sup = pctx._runtime.supervisor
    prev_loop = pctx._runtime.loop

    async def _drive():
        sup = TaskSupervisor()
        loop = asyncio.get_running_loop()
        pctx.set_runtime_services(sup, None)
        pctx._runtime.loop = loop
        ctx = PluginContext(plugin_id="retorno_automatico", loop=loop, broadcast=broadcast)

        ra_lifecycle.setup(ctx)  # → ctx.spawn_task("scheduler", ..., PERMANENT)
        await asyncio.sleep(0.15)

        status = {t["name"]: t for t in sup.status()}
        assert "retorno_automatico:scheduler" in status, status
        task = status["retorno_automatico:scheduler"]
        assert task["state"] == "running"
        assert task["owner"] == "retorno_automatico"
        assert task["policy"] == "permanent"

        await sup.stop_owner("retorno_automatico")
        after = {t["name"]: t for t in sup.status()}
        assert after["retorno_automatico:scheduler"]["state"] == "stopped"

    try:
        asyncio.run(_drive())
    finally:
        pctx._runtime.supervisor = prev_sup
        pctx._runtime.loop = prev_loop
        _set_cfg(enabled=True)


# ══════════════════════════════════════════════════════════════════════════════
# F7 — Export .zip reimporta e reproduz o plugin (loader real).
# ══════════════════════════════════════════════════════════════════════════════

def test_f7_export_zip_roundtrips(plugin_http, tmp_path):
    import io
    import zipfile

    r = plugin_http.get("/api/plugins/retorno_automatico/export")
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("application/zip")

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    for expected in ("plugin.yaml", "__init__.py", "settings.py",
                     "schedule.py", "logic.py", "lifecycle.py"):
        assert expected in names, f"{expected} ausente no .zip: {names}"
    assert not any("__pycache__" in n for n in names)

    # Reimporta: extrai e carrega pelo loader real → tem de reproduzir limpo.
    staging = tmp_path / "plugins" / "retorno_automatico"
    staging.mkdir(parents=True)
    zf.extractall(staging)

    from db.repositories import plugin_repo
    from plugins.loader import discover_and_load

    discover_and_load(tmp_path / "plugins")
    plugin_repo.set_enabled("retorno_automatico", True)
    registry = discover_and_load(tmp_path / "plugins")
    assert "retorno_automatico" in registry.loaded
    assert not plugin_repo.get("retorno_automatico").get("load_error")
