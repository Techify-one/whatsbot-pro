"""Directly-driven plugin-lifecycle tests (plano 13 Fase 1.2→2).

The endpoint suite (tests/test_endpoints.py) no-ops the FastAPI lifespan, so it
NEVER exercises plugin ``setup()``/``teardown()`` nor the subprocess/task
ownership that backs "disable a channel plugin → its subprocess dies". This file
drives those paths directly (no HTTP, no lifespan) so the GOWA-as-plugin
extraction has real coverage of its riskiest mechanic.

Run standalone (kept OUT of test_endpoints.py so the 670-count contract there is
untouched):

    source venv/bin/activate
    python tests/test_gowa_plugin.py
"""

import os
import sys
import asyncio
import time
import shutil
import tempfile
import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Defensive: this file imports plugin/runtime modules but never builds the app;
# the flag keeps any incidentally-imported bootstrap path a no-op (plano 13).
os.environ.setdefault("WHATSBOT_TEST", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.subprocess_service import SubprocessService, SubprocessSpec
from runtime.supervisor import TaskSupervisor
import plugins.context as pctx
from plugins.lifecycle import manager as lifecycle_manager


# ── Test runner (mirrors tests/test_endpoints.py) ──────────────────────────
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


class _LoadedStub:
    """Minimal stand-in for loader.LoadedPlugin (run_setup reads id/setup_fn/teardown_fn)."""

    def __init__(self, plugin_id, setup_fn=None, teardown_fn=None):
        self.id = plugin_id
        self.setup_fn = setup_fn
        self.teardown_fn = teardown_fn


class _WsStub:
    async def broadcast(self, *a, **k):
        return None


def _pid_alive(pid) -> bool:
    """True if the PID is a live process. Checked by PID, NOT a cmdline string
    match — a marker in argv would also match this very test/shell process."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# ═══════════════════════════════════════════════════════════════════
#  Plugin lifecycle direct-drive (plano 13 seam) — the disable→kill proof
# ═══════════════════════════════════════════════════════════════════
section("Plugin lifecycle direct-drive (subprocess ownership)")

loop = asyncio.new_event_loop()
svc = SubprocessService()
sup = TaskSupervisor()
pctx.set_runtime_services(sup, svc)
pctx.set_runtime(_WsStub(), loop)

# A throwaway plugin whose setup() spawns a long sleeper it OWNS. Teardown must
# kill it via stop_owner(owner == plugin_id) — exactly the GOWA disable path.
_SLEEPER = "import time; time.sleep(30)"
_probe_pid = None
_probe_managed = None


def _probe_setup(ctx):
    # spawn_subprocess forces spec.owner = ctx.plugin_id ('probe_id').
    global _probe_managed
    _probe_managed = ctx.spawn_subprocess(SubprocessSpec(
        name="probe_sleep",
        cmd=[sys.executable, "-c", _SLEEPER],
        signature=sys.executable,
    ))


loaded = _LoadedStub("probe_id", setup_fn=_probe_setup)

try:
    err = loop.run_until_complete(lifecycle_manager.run_setup(loaded, None, loop))
    check("run_setup() returns no error", err is None, str(err))

    statuses = svc.status()
    probe = next((s for s in statuses if s["name"] == "probe_sleep"), None)
    check("subprocess 'probe_sleep' was spawned", probe is not None)
    check("subprocess is running after setup()", bool(probe and probe["running"]))
    check("subprocess owner forced to plugin id ('probe_id')",
          bool(probe and probe["owner"] == "probe_id"))
    check("ctx.deps is None outside a server (set_deps never called)",
          getattr(lifecycle_manager.get_context("probe_id"), "deps", "x") is None)

    # The actual OS process exists (checked by PID, not a cmdline string match).
    _probe_pid = probe["pid"] if probe else None
    check("sleeper process alive (by PID) while owned", _pid_alive(_probe_pid))

    # ── Teardown (the 'disable plugin' path) must kill the owned subprocess ──
    loop.run_until_complete(lifecycle_manager.run_teardown("probe_id"))

    statuses_after = svc.status()
    probe_after = next((s for s in statuses_after if s["name"] == "probe_sleep"), None)
    check("subprocess stopped after teardown()", not (probe_after and probe_after["running"]))
    check("plugin context removed after teardown()",
          lifecycle_manager.get_context("probe_id") is None)
    check("sleeper PID dead after teardown", not _pid_alive(_probe_pid))

    # Double-stop idempotency: the gowa plugin registers BOTH stop_owner (via
    # teardown) AND on_unload(managed.stop) as a backstop, so stop() runs twice on
    # the same ManagedProcess. A second stop() on an already-dead proc must no-op,
    # not raise — otherwise teardown would error on the backstop.
    _double_stop_ok = True
    try:
        if _probe_managed is not None:
            _probe_managed.stop()
    except Exception:  # noqa: BLE001
        _double_stop_ok = False
    check("ManagedProcess.stop() is idempotent (backstop double-stop is safe)",
          _double_stop_ok)
finally:
    try:
        svc.stop_all()
    except Exception:
        pass
    # Belt-and-suspenders by PID: no orphan sleeper survives a failed assert.
    if _probe_pid and _pid_alive(_probe_pid):
        try:
            import signal as _sig
            os.kill(int(_probe_pid), _sig.SIGKILL)
        except Exception:
            pass
    loop.close()


# ═══════════════════════════════════════════════════════════════════
#  GOWA plugin lifecycle: setup spawns an OWNED subprocess + loops;
#  teardown (disable) kills them (stop_owner('gowa')).
# ═══════════════════════════════════════════════════════════════════
section("gowa plugin lifecycle (owned subprocess + teardown kill)")


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeSvc:
    def __init__(self):
        self.spawned = []
        self.managed = []
        self.stopped = []

    def spawn(self, spec):
        self.spawned.append(spec)
        m = MagicMock(name="managed")
        m.spec = spec
        self.managed.append(m)
        return m

    def stop_owner(self, owner):
        self.stopped.append(owner)

    def stop_all(self):
        pass


class _FakeSup:
    def __init__(self):
        self.registered = []
        self.stopped = []

    def register(self, spec):
        self.registered.append(spec)

    async def start(self, name):
        return None

    async def stop_owner(self, owner):
        self.stopped.append(owner)


gl = _load_module(str(ROOT / "assets" / "plugin_examples" / "gowa" / "lifecycle.py"),
                  "test_gowa_lifecycle")

# ── Interface guard: the plugin reaches into GOWAManager/gowa.manager internals.
# Assert those symbols exist on the REAL class/module so an over-mock can never
# again hide a rename — this is exactly the false-green that shipped the live
# ``AttributeError: 'GOWAManager' object has no attribute '_get_gowa_binary'``
# (the plugin called gm._get_gowa_binary(), but that's a MODULE function, not a
# method, and a bare MagicMock auto-fabricated it). ──────────────────────────
from gowa.manager import GOWAManager
import gowa.manager as _gowa_mgr_mod
check("GOWAManager._build_cmd is a real method (plugin reuses it for argv)",
      callable(getattr(GOWAManager, "_build_cmd", None)))
check("gowa.manager exposes debug-log helpers (plugin imports them for log parity)",
      all(hasattr(_gowa_mgr_mod, n) for n in
          ("_debug_enabled", "_gowa_log_path", "GOWA_LOG_MAX_BYTES")))
check("_get_gowa_binary is a MODULE function, not a GOWAManager method "
      "(the live-bug invariant: plugin must NOT call gm._get_gowa_binary())",
      callable(getattr(_gowa_mgr_mod, "_get_gowa_binary", None))
      and not hasattr(GOWAManager, "_get_gowa_binary"))

# spec=GOWAManager makes any access to an attribute NOT on the real class raise
# AttributeError (so a future gm.<typo>() surfaces as a setup error here, not in
# production). _build_cmd is a real method → allowed; _on_restart is an instance
# attr (set in __init__, not on the class) → the plugin reads it via getattr(...,
# None), which tolerates its absence, and spec (not spec_set) still lets us set it.
fake_mgr = MagicMock(spec=GOWAManager)
fake_mgr._build_cmd.return_value = [sys.executable, "-c", "pass"]
fake_mgr._on_restart = MagicMock()
fake_deps = MagicMock()
fake_deps.gowa_manager = fake_mgr
fake_deps.gowa_client = MagicMock()
fake_deps.ws_manager = MagicMock()
fake_deps.state = MagicMock()

loop2 = asyncio.new_event_loop()
fsvc = _FakeSvc()
fsup = _FakeSup()
pctx.set_runtime_services(fsup, fsvc)
pctx.set_runtime(_WsStub(), loop2)
pctx.set_deps(fake_deps)

try:
    loaded2 = _LoadedStub("gowa", setup_fn=gl.setup, teardown_fn=gl.teardown)
    err = loop2.run_until_complete(lifecycle_manager.run_setup(loaded2, None, loop2))
    check("gowa setup() returns no error", err is None, str(err))

    check("setup spawned exactly one subprocess", len(fsvc.spawned) == 1)
    spec = fsvc.spawned[0] if fsvc.spawned else None
    check("subprocess spec name is 'gowa'", bool(spec and spec.name == "gowa"))
    check("subprocess owner forced to 'gowa' (kills on disable)",
          bool(spec and spec.owner == "gowa"))
    check("subprocess stdout is NOT PIPE (deadlock guard)",
          bool(spec and spec.stdout is not subprocess.PIPE))
    check("subprocess signature set (stale-kill guard)", bool(spec and spec.signature))

    task_names = {s.name for s in fsup.registered}
    check("registered post-spawn init task (gowa:gowa_init)", "gowa:gowa_init" in task_names)
    check("registered 3 polling loops (status/qr/avatar)",
          {"gowa:status_poll", "gowa:qr_poll", "gowa:avatar_fetch"} <= task_names)
    check("all gowa tasks owned by 'gowa'",
          all(s.owner == "gowa" for s in fsup.registered))

    # ── Teardown == the "disable plugin" path: must stop_owner('gowa') ──
    loop2.run_until_complete(lifecycle_manager.run_teardown("gowa"))
    check("teardown called subprocess stop_owner('gowa')", "gowa" in fsvc.stopped)
    check("teardown called task stop_owner('gowa')", "gowa" in fsup.stopped)
    check("teardown ran on_unload backstop (managed.stop)",
          bool(fsvc.managed) and fsvc.managed[0].stop.called)
finally:
    loop2.close()


# ── setup() is a clean no-op without deps (test harness / zero-channel boot) ──
loop3 = asyncio.new_event_loop()
fsvc2 = _FakeSvc()
pctx.set_runtime_services(_FakeSup(), fsvc2)
pctx.set_runtime(_WsStub(), loop3)
pctx.set_deps(None)
try:
    loaded3 = _LoadedStub("gowa", setup_fn=gl.setup, teardown_fn=gl.teardown)
    err = loop3.run_until_complete(lifecycle_manager.run_setup(loaded3, None, loop3))
    check("gowa setup() no-ops cleanly when deps is None", err is None and not fsvc2.spawned)
    loop3.run_until_complete(lifecycle_manager.run_teardown("gowa"))
finally:
    loop3.close()


# ═══════════════════════════════════════════════════════════════════
#  Bootstrap: upgrade installs+enables gowa once; WHATSBOT_TEST no-ops it
# ═══════════════════════════════════════════════════════════════════
section("bootstrap_gowa_upgrade idempotency + WHATSBOT_TEST guard")

import plugins.loader as _loader
import db.repositories.channel_repo as _channel_repo
import db.repositories.plugin_repo as _plugin_repo

_orig_list = _channel_repo.list_all
_orig_upsert = _plugin_repo.upsert
_orig_env = os.environ.get("WHATSBOT_TEST")
_enable_calls = []
_channel_repo.list_all = lambda: [{"id": "default", "provider": "gowa"}]
_plugin_repo.upsert = lambda pid, ver, *, enabled=None: _enable_calls.append((pid, enabled))
_tmp = tempfile.mkdtemp(prefix="gowa_boot_")
_src = ROOT / "assets" / "plugin_examples"
try:
    tmp_plugins = Path(_tmp) / "plugins"
    tmp_plugins.mkdir(parents=True)

    # WHATSBOT_TEST set -> guarded no-op (the load-bearing test-isolation check).
    os.environ["WHATSBOT_TEST"] = "1"
    r_guard = _loader.bootstrap_gowa_upgrade(tmp_plugins, _src)
    check("upgrade no-ops under WHATSBOT_TEST", r_guard is False and not (tmp_plugins / "gowa").exists())

    # WHATSBOT_TEST unset + a default/gowa channel exists -> installs + enables.
    os.environ.pop("WHATSBOT_TEST", None)
    r1 = _loader.bootstrap_gowa_upgrade(tmp_plugins, _src)
    check("upgrade installs gowa on an existing GOWA install",
          r1 is True and (tmp_plugins / "gowa" / "plugin.yaml").exists())
    check("upgrade enabled the gowa plugin row", ("gowa", True) in _enable_calls)

    # Second call -> idempotent no-op (target exists).
    r2 = _loader.bootstrap_gowa_upgrade(tmp_plugins, _src)
    check("upgrade is idempotent (no re-copy when target exists)", r2 is False)

    # No default/gowa channel -> never resurrects gowa (Cloud-only/Telegram-only).
    shutil.rmtree(tmp_plugins / "gowa")
    _channel_repo.list_all = lambda: [{"id": "tg1", "provider": "telegram"}]
    r3 = _loader.bootstrap_gowa_upgrade(tmp_plugins, _src)
    check("upgrade skips installs without a default/gowa channel",
          r3 is False and not (tmp_plugins / "gowa").exists())
finally:
    _channel_repo.list_all = _orig_list
    _plugin_repo.upsert = _orig_upsert
    if _orig_env is None:
        os.environ.pop("WHATSBOT_TEST", None)
    else:
        os.environ["WHATSBOT_TEST"] = _orig_env
    shutil.rmtree(_tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  Uninstall TOMBSTONE: a deliberately-removed gowa must STAY gone across
#  the next boot. The 'default' gowa channel row persists after uninstall,
#  so WITHOUT the tombstone both bootstrap paths would re-install + re-enable
#  GOWA on the very restart the uninstall schedules (the critical resurrection
#  bug the adversarial review caught).
# ═══════════════════════════════════════════════════════════════════
section("uninstall tombstone — gowa stays gone (no resurrection on reboot)")

import db.repositories.config_repo as _config_repo

_t_orig_cfg = _config_repo.get
_t_orig_list = _channel_repo.list_all
_t_orig_upsert = _plugin_repo.upsert
_t_orig_env = os.environ.get("WHATSBOT_TEST")
_tomb = {"gowa_uninstalled": "1"}
_t_enable = []
_config_repo.get = lambda key, default=None: _tomb.get(key, default)
# Channel row PERSISTS after uninstall — this is what would otherwise resurrect it.
_channel_repo.list_all = lambda: [{"id": "default", "provider": "gowa"}]
_plugin_repo.upsert = lambda pid, ver, *, enabled=None: _t_enable.append((pid, enabled))
_tmp2 = tempfile.mkdtemp(prefix="gowa_tomb_")
try:
    os.environ.pop("WHATSBOT_TEST", None)

    # (1) upgrade-bootstrap must NOT reinstall when the tombstone is set.
    tp = Path(_tmp2) / "plugins"; tp.mkdir(parents=True)
    r = _loader.bootstrap_gowa_upgrade(tp, _src)
    check("tombstone blocks upgrade-bootstrap resurrection",
          r is False and not (tp / "gowa").exists())
    check("tombstone blocks the re-enable of gowa", ("gowa", True) not in _t_enable)

    # (2) fresh-install bootstrap (empty dir, e.g. user removed every plugin) must
    #     SKIP gowa while still copying the other bundled examples.
    copied = _loader.bootstrap_initial_plugins(tp, _src)
    check("tombstone skips gowa in initial bootstrap",
          "gowa" not in copied and not (tp / "gowa").exists())

    # (3) clearing the tombstone (deliberate reinstall) re-allows the install.
    _tomb["gowa_uninstalled"] = "0"
    tp2 = Path(_tmp2) / "plugins_reinstall"; tp2.mkdir(parents=True)
    r2 = _loader.bootstrap_gowa_upgrade(tp2, _src)
    check("cleared tombstone re-allows gowa install",
          r2 is True and (tp2 / "gowa" / "plugin.yaml").exists())
finally:
    _config_repo.get = _t_orig_cfg
    _channel_repo.list_all = _t_orig_list
    _plugin_repo.upsert = _t_orig_upsert
    if _t_orig_env is None:
        os.environ.pop("WHATSBOT_TEST", None)
    else:
        os.environ["WHATSBOT_TEST"] = _t_orig_env
    shutil.rmtree(_tmp2, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  Degradation branches of gowa setup() — must no-op cleanly, never crash
#  the lifespan, and surface a UI hint when the binary is missing.
# ═══════════════════════════════════════════════════════════════════
section("gowa setup() degrades cleanly (binary missing / no manager)")

# (a) binary missing -> _build_cmd raises FileNotFoundError -> no spawn, UI notified.
loop4 = asyncio.new_event_loop()
fsvc4 = _FakeSvc()
mgr_nobin = MagicMock(spec=GOWAManager)
mgr_nobin._build_cmd.side_effect = FileNotFoundError("no bin/gowa")
deps_nobin = MagicMock()
deps_nobin.gowa_manager = mgr_nobin
deps_nobin.ws_manager.broadcast = AsyncMock()
pctx.set_runtime_services(_FakeSup(), fsvc4)
pctx.set_runtime(_WsStub(), loop4)
pctx.set_deps(deps_nobin)
try:
    err = loop4.run_until_complete(lifecycle_manager.run_setup(
        _LoadedStub("gowa", setup_fn=gl.setup, teardown_fn=gl.teardown), None, loop4))
    check("setup() no-crash + no spawn when GOWA binary is missing",
          err is None and not fsvc4.spawned)
    check("binary-missing surfaces a degraded UI notification (gowa_status)",
          deps_nobin.ws_manager.broadcast.called)
    loop4.run_until_complete(lifecycle_manager.run_teardown("gowa"))
finally:
    loop4.close()

# (b) deps present but gowa_manager is None -> clean no-op (non-GOWA boot).
loop5 = asyncio.new_event_loop()
fsvc5 = _FakeSvc()
deps_nomgr = MagicMock()
deps_nomgr.gowa_manager = None
pctx.set_runtime_services(_FakeSup(), fsvc5)
pctx.set_runtime(_WsStub(), loop5)
pctx.set_deps(deps_nomgr)
try:
    err = loop5.run_until_complete(lifecycle_manager.run_setup(
        _LoadedStub("gowa", setup_fn=gl.setup, teardown_fn=gl.teardown), None, loop5))
    check("setup() no-ops when deps has no gowa_manager", err is None and not fsvc5.spawned)
    loop5.run_until_complete(lifecycle_manager.run_teardown("gowa"))
finally:
    loop5.close()


# ═══════════════════════════════════════════════════════════════════
#  Sole-owner invariant: the core never auto-runs GOWA (no resurrection
#  on disable/uninstall — the channel row persists, so a core fallback
#  keyed on registry.loaded would wrongly restart GOWA when disabled).
# ═══════════════════════════════════════════════════════════════════
section("core delegates GOWA entirely to the plugin")

import server.background as _bg
import server.app as _app_mod

check("core exposes no gowa-task registrar (plugin is the sole owner)",
      not hasattr(_bg, "register_gowa_tasks"))
# The lifespan source must not gate GOWA tasks on registry.loaded (the buggy
# resurrection path). audit_purge is the only task the core itself registers.
import inspect as _inspect
_app_src = _inspect.getsource(_app_mod.create_app)
check("core lifespan does not register gowa_start/status_poll/qr_poll/avatar_fetch",
      not any(t in _app_src for t in ('"gowa_start"', '"status_poll"',
                                      '"qr_poll"', '"avatar_fetch"')))
check("core lifespan still registers audit_purge (not a channel concern)",
      '"audit_purge"' in _app_src)


# ═══════════════════════════════════════════════════════════════════
#  Inbound device → channel routing (plano 02 §0.5 / Decisão P13).
#  GOWA delivers every device's inbound to ONE webhook URL, so the receiving
#  channel must be recovered from the payload envelope (device_id = receiving
#  JID, session_id = registered device string). Regression guard for the bug
#  where a 2nd GOWA number's messages collapsed into the "default" channel.
# ═══════════════════════════════════════════════════════════════════
section("inbound GOWA device → channel resolution (channel_repo)")

import logging as _logging
_logging.disable(_logging.INFO)
# Plano 29 C3 (Postgres-only): roda contra o Postgres de teste com o schema
# resetado — o equivalente do antigo temp-SQLite dedicado desta seção.
from tests.pg import init_test_engine as _init_test_engine
_init_test_engine(reset=True)
from db.repositories import channel_repo as _cr

# 'default' is seeded by migration 0011 (gowa, gowa_device_id='whatsbot').
_cr.set_status("default", own_phone="554498510557", logged_in=1, gowa_device_id="whatsbot")
_cr.create(id="whatsapp_teste", provider="gowa", display_name="2o numero")  # device_id None → uses id
_cr.create(id="teste", provider="whatsapp_cloud", display_name="cloud")
_cr.set_status("whatsapp_teste", own_phone="5519988998565", logged_in=1)

check("session_id maps to its channel (default)",
      _cr.get_gowa_channel_for_device("whatsbot", None) == "default")
check("session_id maps 2nd number (device_id NULL → channel id)",
      _cr.get_gowa_channel_for_device("whatsapp_teste", None) == "whatsapp_teste")
check("receiving JID maps via own_phone (2nd number)",
      _cr.get_gowa_channel_for_device(None, "5519988998565@s.whatsapp.net") == "whatsapp_teste")
check("receiving JID with :device suffix maps to default",
      _cr.get_gowa_channel_for_device(None, "554498510557:7@s.whatsapp.net") == "default")
check("unknown device → None (caller keeps URL channel)",
      _cr.get_gowa_channel_for_device("nope", "5500000000000@s.whatsapp.net") is None)
check("no identity given → None",
      _cr.get_gowa_channel_for_device(None, None) is None)
check("whatsapp_cloud channel never matched as gowa",
      _cr.get_gowa_channel_for_device("teste", None) is None)
_cr.create(id="vendas", provider="gowa", display_name="Vendas", gowa_device_id="vendas_ab12cd34")
check("session_id resolves before login (own_phone still NULL)",
      _cr.get_gowa_channel_for_device("vendas_ab12cd34", None) == "vendas")
_cr.set_status("whatsapp_teste", enabled=0)
check("disabled channel → None (won't route)",
      _cr.get_gowa_channel_for_device("whatsapp_teste", None) is None)


# ═══════════════════════════════════════════════════════════════════
#  Results
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'='*60}")
if failed:
    sys.exit(1)
