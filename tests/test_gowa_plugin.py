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


def _load_gowa_plugin_module(name):
    """Load a gowa-plugin submodule WITH package context.

    ``lifecycle.py`` does ``from . import alerts`` (and plano 52 adds
    ``from . import processes``), so a bare ``spec_from_file_location`` load —
    no parent package — breaks with "attempted relative import with no known
    parent package". Mirror the loader's real mechanics instead: register the
    plugin dir as a package (``submodule_search_locations``) and import the
    submodule through the import system.
    """
    import importlib as _importlib
    pkg_dir = ROOT / "assets" / "plugin_examples" / "gowa"
    pkg_name = "test_gowa_pkg"
    if pkg_name not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            pkg_name, pkg_dir / "__init__.py",
            submodule_search_locations=[str(pkg_dir)])
        pkg = importlib.util.module_from_spec(pkg_spec)
        sys.modules[pkg_name] = pkg
        pkg_spec.loader.exec_module(pkg)
    return _importlib.import_module(f"{pkg_name}.{name}")


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


gl = _load_gowa_plugin_module("lifecycle")

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

# ── Plano 52 F1 baseline: characterize the REAL _build_cmd argv shape (with the
# repo binary), so the F2 parametrization is provably byte-identical for the
# shared process. Skipped when the binary hasn't been downloaded on this machine.
if _gowa_mgr_mod._get_gowa_binary().exists():
    _tmp_dd = Path(tempfile.mkdtemp(prefix="gowa_cmd_"))
    try:
        _mgr_base = GOWAManager(
            port=12345, data_dir=_tmp_dd,
            webhook_url="http://127.0.0.1:8090/api/webhook/gowa/default")
        _cmd_base = _mgr_base._build_cmd()
        check("_build_cmd: argv[1:4] == ['rest','--port','12345']",
              _cmd_base[1:4] == ["rest", "--port", "12345"])
        check("_build_cmd: --webhook uses the instance URL",
              "--webhook" in _cmd_base and
              _cmd_base[_cmd_base.index("--webhook") + 1].endswith("/api/webhook/gowa/default"))
        check("_build_cmd: --webhook-events present", "--webhook-events" in _cmd_base)
        check("_build_cmd: presence + os flags present",
              "--presence-on-connect" in _cmd_base and "--os" in _cmd_base)
        check("_build_cmd: no --whatsapp-proxy in argv (plano 52 D5)",
              all("whatsapp-proxy" not in a for a in _cmd_base))
    finally:
        shutil.rmtree(_tmp_dd, ignore_errors=True)

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
    # ── Plano 52 baseline: the SHARED process contract that dedicated (per-proxy)
    # processes must NOT disturb — parent env/cwd inherited, no proxy in argv.
    check("shared spec inherits parent env (env is None)", bool(spec) and spec.env is None)
    check("shared spec inherits parent cwd (cwd is None)", bool(spec) and spec.cwd is None)
    check("shared argv has no --whatsapp-proxy (proxy is env-only, plano 52 D5)",
          bool(spec) and all("whatsapp-proxy" not in str(a) for a in spec.cmd))

    task_names = {s.name for s in fsup.registered}
    check("registered post-spawn init task (gowa:gowa_init)", "gowa:gowa_init" in task_names)
    check("registered 3 polling loops (status/qr/avatar)",
          {"gowa:status_poll", "gowa:qr_poll", "gowa:avatar_fetch"} <= task_names)
    check("registered the plano-52 process reconcile loop",
          "gowa:process_reconcile" in task_names)
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
    #     SKIP gowa when tombstoned. Only gowa is auto-installed now (plano 33 D3:
    #     telegram/whatsapp_cloud are import-only), so a tombstoned gowa leaves
    #     nothing copied.
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
_cr.set_status("default", own_phone="5544999990001", logged_in=1, gowa_device_id="whatsbot")
_cr.create(id="whatsapp_teste", provider="gowa", display_name="2o numero")  # device_id None → uses id
_cr.create(id="teste", provider="whatsapp_cloud", display_name="cloud")
_cr.set_status("whatsapp_teste", own_phone="5519999990001", logged_in=1)

check("session_id maps to its channel (default)",
      _cr.get_gowa_channel_for_device("whatsbot", None) == "default")
check("session_id maps 2nd number (device_id NULL → channel id)",
      _cr.get_gowa_channel_for_device("whatsapp_teste", None) == "whatsapp_teste")
check("receiving JID maps via own_phone (2nd number)",
      _cr.get_gowa_channel_for_device(None, "5519999990001@s.whatsapp.net") == "whatsapp_teste")
check("receiving JID with :device suffix maps to default",
      _cr.get_gowa_channel_for_device(None, "5544999990001:7@s.whatsapp.net") == "default")
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
#  Plano 52 — dedicated per-proxy GOWA processes (pure units)
# ═══════════════════════════════════════════════════════════════════
section("plano 52 — processes: validation / planning / ports / spec (pure)")

gp = _load_gowa_plugin_module("processes")

# validate_proxy_url
check("proxy: socks5 with auth is valid",
      gp.validate_proxy_url("socks5://user:pass@1.2.3.4:1080") is None)
check("proxy: plain http is valid", gp.validate_proxy_url("http://1.2.3.4:8080") is None)
check("proxy: https is valid", gp.validate_proxy_url("https://proxy.example:443") is None)
check("proxy: empty is rejected", gp.validate_proxy_url("") is not None)
check("proxy: bad scheme rejected (ftp)", gp.validate_proxy_url("ftp://x:1") is not None)
check("proxy: hostless rejected", gp.validate_proxy_url("socks5://") is not None)
check("proxy: garbage rejected", gp.validate_proxy_url("not a url") is not None)

# plan_reconcile (pure diff)
check("plan: empty/empty -> no actions",
      gp.plan_reconcile({}, {}) == {"spawn": [], "stop": [], "restart": []})
check("plan: new desired -> spawn",
      gp.plan_reconcile({"a": "p1"}, {}) == {"spawn": ["a"], "stop": [], "restart": []})
check("plan: gone desired -> stop",
      gp.plan_reconcile({}, {"a": {"proxy": "p1"}})
      == {"spawn": [], "stop": ["a"], "restart": []})
check("plan: changed proxy -> restart",
      gp.plan_reconcile({"a": "p2"}, {"a": {"proxy": "p1"}})
      == {"spawn": [], "stop": [], "restart": ["a"]})
check("plan: same proxy -> steady state (no actions)",
      gp.plan_reconcile({"a": "p1"}, {"a": {"proxy": "p1"}})
      == {"spawn": [], "stop": [], "restart": []})
check("plan: deterministic ordering",
      gp.plan_reconcile({"b": "x", "a": "y"}, {}) ["spawn"] == ["a", "b"])

# desired_proxies (pure over rows + credential getter)
_rows_52 = [
    {"id": "default", "provider": "gowa", "enabled": 1, "archived": 0},
    {"id": "ch_ok", "provider": "gowa", "enabled": 1, "archived": 0},
    {"id": "ch_off", "provider": "gowa", "enabled": 0, "archived": 0},
    {"id": "ch_arch", "provider": "gowa", "enabled": 1, "archived": 1},
    {"id": "ch_noproxy", "provider": "gowa", "enabled": 1, "archived": 0},
    {"id": "ch_bad", "provider": "gowa", "enabled": 1, "archived": 0},
    {"id": "tg", "provider": "telegram", "enabled": 1, "archived": 0},
]
_creds_52 = {
    ("default", "proxy_url"): "socks5://u:p@9.9.9.9:1080",
    ("ch_ok", "proxy_url"): "socks5://u:p@1.2.3.4:1080",
    ("ch_off", "proxy_url"): "socks5://u:p@1.2.3.4:1080",
    ("ch_arch", "proxy_url"): "socks5://u:p@1.2.3.4:1080",
    ("ch_bad", "proxy_url"): "ftp://nope",
    ("tg", "proxy_url"): "socks5://u:p@1.2.3.4:1080",
}
_desired = gp.desired_proxies(_rows_52, lambda cid, key: _creds_52.get((cid, key)))
check("desired: only enabled+non-archived gowa channels with valid proxy",
      _desired == {"ch_ok": "socks5://u:p@1.2.3.4:1080"})
check("desired: 'default' is NEVER dedicated (legacy singleton)",
      "default" not in _desired)

# allocate_port (persisted-first, injectable port_free)
check("port: persisted port reused when free",
      gp.allocate_port({"config": '{"gowa_dedicated_port": 4001}'}, 4000, set(),
                       port_free=lambda p: True) == 4001)
check("port: persisted port skipped when taken by another channel",
      gp.allocate_port({"config": '{"gowa_dedicated_port": 4001}'}, 4000, {4001},
                       port_free=lambda p: True) == 4002)
check("port: allocates first free above base",
      gp.allocate_port({"config": "{}"}, 4000, set(),
                       port_free=lambda p: p != 4001) == 4002)
check("port: malformed config falls through to allocation",
      gp.allocate_port({"config": "not json"}, 4000, set(),
                       port_free=lambda p: True) == 4001)

# webhook derivation + spec building
check("webhook: default URL -> per-channel URL",
      gp._channel_webhook_url("http://127.0.0.1:8090/api/webhook/gowa/default", "ch1")
      == "http://127.0.0.1:8090/api/webhook/gowa/ch1")
check("webhook: unset shared URL -> None", gp._channel_webhook_url(None, "ch1") is None)

_spec_52 = gp.build_spec(name="gowa_x", cmd=["/bin/x", "rest"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={"WHATSAPP_PROXY": "socks5://u:p@h:1"}, cwd="/tmp/x")
check("build_spec: name/env/cwd pass through",
      _spec_52.name == "gowa_x" and _spec_52.cwd == "/tmp/x"
      and _spec_52.env.get("WHATSAPP_PROXY") == "socks5://u:p@h:1")
check("build_spec: signature derived from argv[0]", _spec_52.signature == "/bin/x")
check("build_spec: shared defaults keep env/cwd inherited (None)",
      gp.build_spec(name="gowa", cmd=["/bin/x"], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL).env is None)


# ═══════════════════════════════════════════════════════════════════
#  Plano 52 — reconcile integration (real repos on the test PG,
#  fake ctx/deps, tmp cwd so storages/gowa_ch_* never touches the repo)
# ═══════════════════════════════════════════════════════════════════
section("plano 52 — reconcile_once (spawn/stop/transitions against test PG)")

from db.repositories import channel_credential_repo as _ccr
from types import SimpleNamespace


class _RecCtx:
    """ctx double: records specs, returns a fake ManagedProcess per spawn."""

    def __init__(self):
        self.specs = []
        self.managed = []

    def spawn_subprocess(self, spec):
        self.specs.append(spec)
        m = MagicMock(name=f"managed_{spec.name}")
        m.spec = spec
        self.managed.append(m)
        return m

    def on_unload(self, fn):
        pass


_cr.create(id="proxy1", provider="gowa", display_name="Proxied")
_ccr.set("proxy1", "proxy_url", "socks5://user:pass@10.0.0.9:1080")
_ccr.set("default", "proxy_url", "socks5://user:pass@10.0.0.9:1080")
_ccr.set("vendas", "proxy_url", "ftp://invalid")

_rec_ctx = _RecCtx()
_rec_mgr = GOWAManager(port=19997, data_dir=Path(tempfile.mkdtemp(prefix="gowa_dd_")),
                       webhook_url="http://127.0.0.1:8090/api/webhook/gowa/default")
_rec_deps = SimpleNamespace(gowa_manager=_rec_mgr, gowa_client=MagicMock(port=19997),
                            channel_registry=MagicMock())

_prev_cwd = os.getcwd()
_tmp_cwd = tempfile.mkdtemp(prefix="gowa_rec_")
os.chdir(_tmp_cwd)
try:
    gp._MANAGED.clear()
    plan1 = gp.reconcile_once(_rec_ctx, _rec_deps)
    check("reconcile: spawns exactly the valid proxied channel",
          plan1["spawn"] == ["proxy1"] and len(_rec_ctx.specs) == 1)
    _sp = _rec_ctx.specs[0]
    check("reconcile: spec name is gowa_<cid>", _sp.name == "gowa_proxy1")
    check("reconcile: WHATSAPP_PROXY only in env, never argv",
          _sp.env.get("WHATSAPP_PROXY") == "socks5://user:pass@10.0.0.9:1080"
          and all("socks5" not in str(a) and "whatsapp-proxy" not in str(a)
                  for a in _sp.cmd))
    check("reconcile: per-channel webhook in argv",
          any(str(a).endswith("/api/webhook/gowa/proxy1") for a in _sp.cmd))
    check("reconcile: dedicated cwd under storages/gowa_ch_<cid>",
          _sp.cwd is not None and _sp.cwd.endswith(os.path.join("storages", "gowa_ch_proxy1")))
    check("reconcile: statics symlink bridge created",
          (Path(_sp.cwd) / "statics").is_symlink())
    _row_p1 = _cr.get("proxy1")
    _cfg_p1 = __import__("json").loads(_row_p1.get("config") or "{}")
    check("reconcile: port persisted in channel config",
          int(_cfg_p1.get("gowa_dedicated_port") or 0) > 19997)
    check("reconcile: gowa_isolation flipped to dedicated_process",
          _row_p1.get("gowa_isolation") == "dedicated_process")
    check("reconcile: live instance rebuilt in the registry",
          _rec_deps.channel_registry.add_channel.called)
    check("reconcile: default with proxy -> last_error explains, no dedicated spawn",
          str((_cr.get("default") or {}).get("last_error") or "").startswith("Proxy não é suportado"))
    check("reconcile: invalid proxy -> last_error 'Proxy inválido'",
          str((_cr.get("vendas") or {}).get("last_error") or "").startswith("Proxy inválido"))

    # Steady state: nothing changes on the next pass.
    plan2 = gp.reconcile_once(_rec_ctx, _rec_deps)
    check("reconcile: steady state is a no-op",
          plan2 == {"spawn": [], "stop": [], "restart": []} and len(_rec_ctx.specs) == 1)

    # Proxy CHANGED -> restart (stop + respawn, same channel).
    _ccr.set("proxy1", "proxy_url", "http://user:pass@10.0.0.10:8080")
    plan3 = gp.reconcile_once(_rec_ctx, _rec_deps)
    check("reconcile: changed proxy -> restart", plan3["restart"] == ["proxy1"])
    check("reconcile: restart stopped the old process",
          _rec_ctx.managed[0].stop.called)
    check("reconcile: restart spawned with the NEW proxy env",
          len(_rec_ctx.specs) == 2
          and _rec_ctx.specs[1].env.get("WHATSAPP_PROXY") == "http://user:pass@10.0.0.10:8080")

    # Proxy REMOVED -> stop + transition back to shared.
    _ccr.set("proxy1", "proxy_url", "")
    plan4 = gp.reconcile_once(_rec_ctx, _rec_deps)
    check("reconcile: removed proxy -> stop", plan4["stop"] == ["proxy1"])
    check("reconcile: dedicated process stopped", _rec_ctx.managed[1].stop.called)
    _row_p1b = _cr.get("proxy1")
    _cfg_p1b = __import__("json").loads(_row_p1b.get("config") or "{}")
    check("reconcile: back to shared (isolation + port cleared)",
          _row_p1b.get("gowa_isolation") == "shared"
          and "gowa_dedicated_port" not in _cfg_p1b)

    # Heal: row CLAIMS dedicated but nothing desired/running (e.g. proxy removed
    # while the server was off) -> converges back to shared on the next pass.
    _cr.set_status("proxy1", gowa_isolation="dedicated_process",
                   config='{"gowa_dedicated_port": 45678}')
    gp._MANAGED.clear()
    gp.reconcile_once(_rec_ctx, _rec_deps)
    _row_heal = _cr.get("proxy1")
    check("reconcile: heals stale dedicated claim back to shared",
          _row_heal.get("gowa_isolation") == "shared"
          and "gowa_dedicated_port" not in (_row_heal.get("config") or ""))
finally:
    os.chdir(_prev_cwd)
    gp._MANAGED.clear()
    shutil.rmtree(_tmp_cwd, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  Plano 52 — bundled-plugin VERSION-AWARE upgrade (P7): newer bundled
#  version replaces the installed copy; same/older never re-copies.
# ═══════════════════════════════════════════════════════════════════
section("plano 52 — bootstrap_gowa_upgrade version-aware in-place upgrade")

_v_orig_list = _channel_repo.list_all
_v_orig_env = os.environ.get("WHATSBOT_TEST")
_channel_repo.list_all = lambda: [{"id": "default", "provider": "gowa"}]
_tmp3 = tempfile.mkdtemp(prefix="gowa_verup_")
try:
    os.environ.pop("WHATSBOT_TEST", None)
    tp = Path(_tmp3) / "plugins"; tp.mkdir(parents=True)
    # Install the CURRENT bundled version first (the missing-install path).
    r_install = _loader.bootstrap_gowa_upgrade(tp, _src)
    check("verup: initial install works", r_install is True)

    # Simulate an OLDER installed copy + a marker file (would be lost on clobber).
    yaml_p = tp / "gowa" / "plugin.yaml"
    _yaml_txt = yaml_p.read_text(encoding="utf-8")
    import re as _re
    _cur_ver = _re.search(r"^version:\s*(\S+)", _yaml_txt, _re.M).group(1).strip("'\"")
    yaml_p.write_text(_yaml_txt.replace(f"version: {_cur_ver}", "version: 0.9.0"),
                      encoding="utf-8")
    (tp / "gowa" / "LOCAL_EDIT.marker").write_text("x", encoding="utf-8")

    r_up = _loader.bootstrap_gowa_upgrade(tp, _src)
    check("verup: newer bundled version re-copies in place", r_up is True)
    check("verup: installed manifest now matches the bundled version",
          f"version: {_cur_ver}" in yaml_p.read_text(encoding="utf-8"))
    check("verup: replacement is a clean copy (local marker gone)",
          not (tp / "gowa" / "LOCAL_EDIT.marker").exists())
    check("verup: processes.py shipped by the upgrade",
          (tp / "gowa" / "processes.py").exists())

    r_same = _loader.bootstrap_gowa_upgrade(tp, _src)
    check("verup: same version never re-copies", r_same is False)

    # NEWER installed than bundled (e.g. dev rollback) -> keep installed.
    yaml_p.write_text(yaml_p.read_text(encoding="utf-8")
                      .replace(f"version: {_cur_ver}", "version: 99.0.0"),
                      encoding="utf-8")
    r_newer = _loader.bootstrap_gowa_upgrade(tp, _src)
    check("verup: installed NEWER than bundled is left alone", r_newer is False)
finally:
    _channel_repo.list_all = _v_orig_list
    if _v_orig_env is None:
        os.environ.pop("WHATSBOT_TEST", None)
    else:
        os.environ["WHATSBOT_TEST"] = _v_orig_env
    shutil.rmtree(_tmp3, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  Results
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'='*60}")
if failed:
    sys.exit(1)
