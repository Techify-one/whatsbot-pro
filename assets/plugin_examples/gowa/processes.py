"""Per-channel dedicated GOWA processes (plano 52).

A GOWA channel with an outbound proxy configured (credential ``proxy_url``) runs
its own GOWA subprocess: own port, own cwd (isolates ``whatsapp.db`` /
``chatstorage.db`` — GOWA has NO env/flag override for the chat-storage path, so
cwd is the isolation seam), own ``WHATSAPP_PROXY`` env and a per-channel webhook
URL (``/api/webhook/gowa/<channel_id>`` — the route already exists). Channels
without a proxy stay on the shared process untouched (plano 52 D3 — zero
regression; nothing re-pairs on upgrade).

Orchestration is DECLARATIVE and self-healing:

* :func:`plan_reconcile` (pure — unit-testable without subprocesses) computes
  ``{spawn, stop, restart}`` from desired state vs running state.
* :func:`reconcile_once` applies the plan: spawns/stops dedicated processes and
  performs the shared↔dedicated session transitions (evicting the device from
  the shared process when a proxy is turned ON, and recreating the shared
  instance when it is turned OFF). Turning a proxy on/off re-pairs THAT number
  (QR) — expected, and stated in the field's help text.
* ``lifecycle.setup`` runs it once at boot and then every ``RECONCILE_INTERVAL``
  seconds via a PERMANENT supervised task, so create/edit/delete/enable/disable
  all converge without event plumbing.

⚠️ Secret hygiene (plano 52 D5): the proxy URL carries credentials. It is passed
ONLY via the ``WHATSAPP_PROXY`` env on the spec — never in argv (``_build_cmd``
output is logged and visible in ``ps``). GOWA itself redacts the URL in its own
logs (``redactProxyURL``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Credential key on the channel that turns the dedicated process on (descriptor
# field ``proxy_url`` — channels/providers/gowa_channel.py).
PROXY_CRED_KEY = "proxy_url"
# Channel config key persisting the allocated port (stable across restarts —
# webhook URL and the per-channel client both derive from it).
PORT_CONFIG_KEY = "gowa_dedicated_port"
_ALLOWED_SCHEMES = ("socks5", "http", "https")
RECONCILE_INTERVAL = 15.0
# Filesystem safety: a channel id becomes part of a directory name.
_SAFE_CID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# cid -> {"managed": ManagedProcess, "proxy": str, "port": int}
# Rebuilt from scratch on every worker boot (children die with the parent via
# PR_SET_PDEATHSIG, so an empty registry == no dedicated processes running).
_MANAGED: dict = {}


# ── Spec building (plano 52 F2 — shared + dedicated go through here) ─────────

def build_spec(*, name: str, cmd: list, stdout, stderr, on_restart=None,
               env: dict | None = None, cwd: str | None = None):
    """One SubprocessSpec shape for the shared AND the dedicated GOWA processes.

    Extracted from the inline block in ``lifecycle.setup`` (plano 52 F2). The
    shared process passes ``env=None``/``cwd=None`` (inherit the parent — the
    historical behaviour, byte-identical); a dedicated process passes its own
    env (``WHATSAPP_PROXY``) and cwd (session isolation).
    """
    from runtime.subprocess_service import SubprocessSpec
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return SubprocessSpec(
        name=name,
        cmd=cmd,
        signature=str(cmd[0]) if cmd else "",  # stale-kill guard (per pid-file/name)
        readiness=None,
        readiness_timeout=15.0,
        stdout=stdout,
        stderr=stderr,
        creationflags=creation_flags,
        max_restarts=3,
        window_sec=60.0,
        restart_delay=5.0,
        on_restart=on_restart,
        env=env,
        cwd=cwd,
    )


# ── Proxy URL validation (plano 52 F3) ───────────────────────────────────────

def validate_proxy_url(url) -> str | None:
    """Return a PT-BR error message, or ``None`` when the URL is usable.

    Accepts only the schemes whatsmeow's ``SetProxyAddress`` understands
    (socks5/http/https) with a non-empty host. Best practice (documented in the
    field help): a STATIC, dedicated IP per number — never a rotating endpoint.
    """
    raw = str(url or "").strip()
    if not raw:
        return "URL de proxy vazia"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "URL de proxy inválida"
    if parts.scheme not in _ALLOWED_SCHEMES:
        return ("esquema inválido (use socks5://, http:// ou https://)")
    try:
        host = parts.hostname
    except ValueError:
        return "host do proxy inválido"
    if not host:
        return "host do proxy vazio"
    return None


# ── Desired state (pure over rows + a credential getter) ────────────────────

def desired_proxies(rows: list, get_credential) -> dict:
    """{channel_id: proxy_url} for every gowa channel that WANTS a dedicated
    process: enabled, not archived, not ``default`` (the legacy singleton
    pipeline — plano 52 F5), with a syntactically valid proxy credential.
    Invalid proxies are EXCLUDED here (the executor surfaces them as
    ``last_error``); pure — no DB writes."""
    out = {}
    for r in rows or []:
        if r.get("provider") != "gowa":
            continue
        if not r.get("enabled") or r.get("archived"):
            continue
        cid = r.get("id") or ""
        if cid == "default":
            continue
        try:
            proxy = (get_credential(cid, PROXY_CRED_KEY) or "").strip()
        except Exception:  # noqa: BLE001 — a cred-store hiccup must not stop others
            continue
        if not proxy or validate_proxy_url(proxy) is not None:
            continue
        out[cid] = proxy
    return out


def plan_reconcile(desired: dict, running: dict) -> dict:
    """Pure diff: desired {cid: proxy} vs running {cid: {"proxy": ...}} →
    ``{"spawn": [...], "stop": [...], "restart": [...]}`` (sorted, deterministic).
    ``restart`` = still desired but the live process runs a DIFFERENT proxy."""
    spawn = sorted(cid for cid in desired if cid not in running)
    stop = sorted(cid for cid in running if cid not in desired)
    restart = sorted(
        cid for cid, proxy in desired.items()
        if cid in running and (running[cid] or {}).get("proxy") != proxy
    )
    return {"spawn": spawn, "stop": stop, "restart": restart}


# ── Port allocation (persisted in channels.config) ──────────────────────────

def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False


def _config_of(row: dict) -> dict:
    try:
        cfg = json.loads(row.get("config") or "{}")
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def allocate_port(row: dict, base_port: int, used_ports: set,
                  *, port_free=_port_free) -> int:
    """Reuse the persisted port when it is still usable; otherwise the first
    free port above ``base_port`` (skipping ports other channels persisted).
    ``port_free`` is injectable for tests."""
    cfg = _config_of(row or {})
    try:
        persisted = int(cfg.get(PORT_CONFIG_KEY) or 0)
    except (TypeError, ValueError):
        persisted = 0
    if persisted > 0 and persisted not in used_ports and port_free(persisted):
        return persisted
    port = int(base_port) + 1
    while port in used_ports or not port_free(port):
        port += 1
        if port > 65535:
            raise RuntimeError("no free port available for dedicated GOWA process")
    return port


def _persist_port(cid: str, row: dict, port: int) -> None:
    cfg = _config_of(row)
    if cfg.get(PORT_CONFIG_KEY) == port:
        return
    cfg[PORT_CONFIG_KEY] = port
    from db.repositories import channel_repo
    channel_repo.set_status(cid, config=json.dumps(cfg))


def _clear_port(cid: str) -> None:
    from db.repositories import channel_repo
    row = channel_repo.get(cid)
    if not row:
        return
    cfg = _config_of(row)
    if PORT_CONFIG_KEY not in cfg:
        return
    cfg.pop(PORT_CONFIG_KEY, None)
    channel_repo.set_status(cid, config=json.dumps(cfg))


# ── Filesystem prep (cwd isolation + statics bridge) ─────────────────────────

def prepare_channel_dir(cid: str) -> Path:
    """Create ``storages/gowa_ch_<cid>/`` (the dedicated process cwd) with a
    RELATIVE ``statics`` symlink back to the app's statics dir, so media the
    dedicated GOWA reads/writes lands on the same paths the panel serves
    (server/app.py statics mount). GOWA's own DBs are created by GOWA under
    ``<cwd>/storages/`` — isolated per channel by construction.

    The dir is intentionally NOT removed when the proxy is turned off — the
    paired session inside survives a later re-enable."""
    base = Path.cwd()
    d = base / "storages" / f"gowa_ch_{cid}"
    d.mkdir(parents=True, exist_ok=True)
    link = d / "statics"
    if not link.is_symlink() and not link.exists():
        target = Path(os.path.relpath(base / "statics", d))
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as e:
            # Windows without symlink privilege: media paths from this process
            # will not resolve in the panel. Dedicated processes are a
            # server-side (Linux/Docker) feature; degrade loudly, not fatally.
            logger.warning("gowa processes: could not symlink %s -> %s (%s); "
                           "media from this channel may not render", link, target, e)
    return d


def _channel_webhook_url(shared_url: str | None, cid: str) -> str | None:
    """Derive the per-channel webhook from the shared one:
    ``.../api/webhook/gowa/default`` → ``.../api/webhook/gowa/<cid>``."""
    if not shared_url:
        return None
    return shared_url.rsplit("/", 1)[0] + f"/{cid}"


# ── Executor (side-effects) ──────────────────────────────────────────────────

def _set_proxy_error(cid: str, row: dict, message: str | None) -> None:
    """Write/clear ``last_error`` for proxy problems — only when it changes, and
    never clobbering an unrelated error message."""
    from db.repositories import channel_repo
    current = str(row.get("last_error") or "")
    if message:
        if current != message:
            channel_repo.set_status(cid, last_error=message)
    elif current.startswith("Proxy"):
        channel_repo.set_status(cid, last_error="")


def _rebuild_live_instance(deps, cid: str) -> None:
    """Re-register the live Channel instance so its client points at the right
    port (dedicated after a spawn; shared after a stop). Same mechanism as
    ``channel_service.register_live``."""
    try:
        from db.repositories import channel_repo
        row = channel_repo.get(cid)
        registry = getattr(deps, "channel_registry", None)
        if not row or registry is None:
            return
        if row.get("archived"):
            # Never resurrect a soft-deleted channel into the live registry
            # (channel_service.delete removed it on purpose).
            return
        from channels.providers.gowa_channel import build_gowa_channel
        inst = build_gowa_channel(cid, row, gowa_client=deps.gowa_client,
                                  gowa_manager=deps.gowa_manager)
        registry.add_channel(cid, inst)
    except Exception as e:  # noqa: BLE001
        logger.warning("gowa processes: could not rebuild live instance %s: %s", cid, e)


def _evict_device_from_shared(deps, row: dict) -> None:
    """shared→dedicated transition (plano 52 F5): log the device out of the
    SHARED process and purge its slot there, so the number is never connected
    twice (once without proxy). The dedicated process re-pairs via QR."""
    cid = row.get("id") or ""
    device_id = row.get("gowa_device_id") or cid
    try:
        from gowa.client import GOWAClient
        cli = GOWAClient(port=getattr(deps.gowa_manager, "port", 3000))
        cli.device_id = device_id
        cli.strict_device = True
        cli._device_ready = True  # do not auto-create; act on the existing slot only
        try:
            cli.logout()
        except Exception:  # noqa: BLE001
            pass
        try:
            cli.delete_device()
        except Exception:  # noqa: BLE001
            pass
        logger.info("gowa processes: evicted device %s from shared process "
                    "(channel %s moves to a dedicated proxied process)", device_id, cid)
    except Exception as e:  # noqa: BLE001
        logger.warning("gowa processes: shared eviction for %s failed: %s", cid, e)


def _spawn_dedicated(ctx, deps, cid: str, row: dict, proxy: str) -> bool:
    gm = deps.gowa_manager
    if not _SAFE_CID.match(cid or ""):
        logger.error("gowa processes: unsafe channel id %r — not spawning", cid)
        return False
    webhook = _channel_webhook_url(getattr(gm, "webhook_url", None), cid)
    if not webhook:
        logger.error("gowa processes: shared webhook_url unset — cannot derive "
                     "per-channel webhook for %s; not spawning", cid)
        return False

    used = set()
    for other_cid, info in _MANAGED.items():
        if other_cid != cid and info.get("port"):
            used.add(int(info["port"]))
    base_port = int(getattr(gm, "port", 3000))
    used.add(base_port)
    try:
        port = allocate_port(row, base_port, used)
    except RuntimeError as e:
        logger.error("gowa processes: %s (channel %s)", e, cid)
        return False

    try:
        cmd = gm._build_cmd(port=port, webhook_url=webhook)
    except FileNotFoundError as e:
        logger.warning("gowa processes: binary missing (%s); cannot spawn %s", e, cid)
        return False
    cwd = prepare_channel_dir(cid)
    env = {**os.environ, "WHATSAPP_PROXY": proxy}

    # Transition bookkeeping BEFORE the spawn: a number moving off the shared
    # process must not stay connected there (double session, one without proxy).
    if (row.get("gowa_isolation") or "shared") != "dedicated_process":
        _evict_device_from_shared(deps, row)

    spec = build_spec(name=f"gowa_{cid}", cmd=cmd,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      env=env, cwd=str(cwd))
    try:
        managed = ctx.spawn_subprocess(spec)
    except RuntimeError as e:  # subprocess service not wired
        logger.warning("gowa processes: subprocess service not wired (%s)", e)
        return False
    ctx.on_unload(managed.stop)  # backstop; stop_owner('gowa') is the primary kill
    _MANAGED[cid] = {"managed": managed, "proxy": proxy, "port": port}

    _persist_port(cid, row, port)
    from db.repositories import channel_repo
    channel_repo.set_status(cid, gowa_isolation="dedicated_process")
    _set_proxy_error(cid, row, None)
    _rebuild_live_instance(deps, cid)
    logger.info("gowa processes: dedicated GOWA up for channel %s (port %d, "
                "proxied)", cid, port)
    return True


def _stop_dedicated(deps, cid: str, *, to_shared: bool) -> None:
    info = _MANAGED.pop(cid, None)
    if info:
        try:
            info["managed"].stop()
        except Exception as e:  # noqa: BLE001
            logger.warning("gowa processes: stop of gowa_%s failed: %s", cid, e)
    if not to_shared:
        return
    # dedicated→shared transition: the channel goes back to the shared process
    # (fresh QR there). Keep storages/gowa_ch_<cid>/ on disk — the paired session
    # inside survives a later proxy re-enable.
    try:
        _clear_port(cid)
        from db.repositories import channel_repo
        row = channel_repo.get(cid)
        if row is not None:
            channel_repo.set_status(cid, gowa_isolation="shared")
            _set_proxy_error(cid, row, None)
        _rebuild_live_instance(deps, cid)
        logger.info("gowa processes: channel %s back on the shared GOWA process", cid)
    except Exception as e:  # noqa: BLE001
        logger.warning("gowa processes: shared-transition for %s failed: %s", cid, e)


def reconcile_once(ctx, deps) -> dict:
    """One reconcile pass. Returns the applied plan (for logs/tests)."""
    from db.repositories import channel_repo, channel_credential_repo

    rows = channel_repo.list_all()
    desired = desired_proxies(rows, channel_credential_repo.get)

    # Surface configuration problems as last_error (invalid proxy; proxy on the
    # default channel, which stays on the legacy singleton — plano 52 F5).
    for r in rows:
        if r.get("provider") != "gowa" or not r.get("enabled") or r.get("archived"):
            continue
        cid = r.get("id") or ""
        try:
            proxy = (channel_credential_repo.get(cid, PROXY_CRED_KEY) or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if not proxy:
            continue
        if cid == "default":
            _set_proxy_error(cid, r, "Proxy não é suportado no canal padrão — "
                                     "crie um canal GOWA novo para usar proxy")
            continue
        err = validate_proxy_url(proxy)
        if err:
            _set_proxy_error(cid, r, f"Proxy inválido: {err}")

    # Heal rows that CLAIM a dedicated process but should not have one and have
    # none running — e.g. the proxy credential was removed while the server was
    # OFF (fresh boot ⇒ _MANAGED is empty, so the plain "stop" diff never sees
    # them). Idempotent: after healing, isolation='shared' and the port key is
    # gone, so the condition stops matching.
    for r in rows:
        if r.get("provider") != "gowa":
            continue
        cid = r.get("id") or ""
        if not cid or cid in desired or cid in _MANAGED:
            continue
        if (r.get("gowa_isolation") or "shared") == "dedicated_process" \
                or _config_of(r).get(PORT_CONFIG_KEY):
            _stop_dedicated(deps, cid, to_shared=True)

    plan = plan_reconcile(desired, dict(_MANAGED))
    for cid in plan["stop"]:
        _stop_dedicated(deps, cid, to_shared=True)
    for cid in plan["restart"]:
        _stop_dedicated(deps, cid, to_shared=False)
    row_by_id = {r.get("id"): r for r in rows}
    for cid in plan["restart"] + plan["spawn"]:
        row = row_by_id.get(cid)
        if row is None:
            continue
        _spawn_dedicated(ctx, deps, cid, row, desired[cid])
    if plan["spawn"] or plan["stop"] or plan["restart"]:
        logger.info("gowa processes: reconcile applied %s", plan)
    return plan


async def reconcile_loop(ctx, deps) -> None:
    """PERMANENT supervised task (lifecycle.setup). ``reconcile_once`` runs in a
    worker thread — spawn() can block briefly on the stale-kill path."""
    while True:
        try:
            await asyncio.to_thread(reconcile_once, ctx, deps)
        except Exception as e:  # noqa: BLE001 — a bad pass must not kill the loop
            logger.warning("gowa processes: reconcile pass failed: %s", e)
        await asyncio.sleep(RECONCILE_INTERVAL)
