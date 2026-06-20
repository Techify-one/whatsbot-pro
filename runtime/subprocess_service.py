"""Managed subprocess service (plano 09 Fase 4).

A robust owner of subprocesses: starts them in their own process group,
makes them die with the parent (Linux ``PR_SET_PDEATHSIG`` — defends against
the toggle's ``os._exit``), kills the whole tree on stop, stale-kills a
leftover on boot (essential so the GOWA WhatsApp session is not duplicated),
runs a watchdog with rate-limit, and waits on a readiness probe.

The core uses it for the GOWA bridge (hardening); it is also exposed to plugins
(plano 09 Fase 5), which unlocks GOWA-as-plugin (plano 02) and the code-in-DB
retrofit (plano 06).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from runtime._proc_platform import (
    IS_WINDOWS,
    is_pid_alive,
    kill_process_group,
    pdeathsig_preexec,
    process_cmdline,
)

logger = logging.getLogger(__name__)

# Default location for PID files (gitignored).
_RUN_DIR = Path(__file__).resolve().parent.parent / "storages" / "run"


@dataclass
class SubprocessSpec:
    name: str                         # e.g. "gowa" / "gowa:comercial"
    cmd: list                         # argv
    env: Optional[dict] = None
    cwd: Optional[str] = None
    pid_file: Optional[Path] = None   # default: storages/run/<name>.pid
    signature: str = ""               # cmd token written to the PID-file (stale-kill guard)
    readiness: Optional[Callable[[], bool]] = None
    readiness_timeout: float = 15.0
    stdout: object = subprocess.DEVNULL
    stderr: object = subprocess.DEVNULL
    creationflags: int = 0
    max_restarts: int = 3
    window_sec: float = 60.0
    restart_delay: float = 5.0
    on_restart: Optional[Callable[[], None]] = None
    owner: str = "core"               # "core" or plugin_id


class ManagedProcess:
    """One supervised OS process: stale-kill → spawn (group + die-with-parent) → readiness → watchdog."""

    def __init__(self, spec: SubprocessSpec):
        self.spec = spec
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._watchdog: Optional[threading.Thread] = None
        self._restart_count = 0
        self._restart_window_start = 0.0
        self._lock = threading.Lock()
        if spec.pid_file is None:
            _RUN_DIR.mkdir(parents=True, exist_ok=True)
            spec.pid_file = _RUN_DIR / f"{_safe_name(spec.name)}.pid"

    # ── Lifecycle ────────────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self.is_running():
                logger.info("%s already running (pid=%s)", self.spec.name, self._process.pid)
                return
            self._stale_kill()
            self._spawn()
            self._running = True
        self._await_readiness()
        self._start_watchdog()

    def _spawn(self) -> None:
        spec = self.spec
        popen_kwargs: dict = {
            "stdout": spec.stdout,
            "stderr": spec.stderr,
            "cwd": spec.cwd,
            "env": spec.env,
            "creationflags": spec.creationflags,
        }
        if not IS_WINDOWS:
            # New session → own process group; preexec sets die-with-parent (Linux).
            popen_kwargs["start_new_session"] = True
            preexec = pdeathsig_preexec()
            if preexec is not None:
                popen_kwargs["preexec_fn"] = preexec
        self._process = subprocess.Popen(spec.cmd, **popen_kwargs)
        self._write_pid_file(self._process.pid)
        logger.info("%s started (pid=%s)", spec.name, self._process.pid)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._running = False
            proc = self._process
            if proc is None:
                self._remove_pid_file()
                return
            pid = proc.pid
            logger.info("Stopping %s (pid=%s)...", self.spec.name, pid)
            # SIGTERM the whole group → wait → SIGKILL the group.
            kill_process_group(pid, signal.SIGTERM if not IS_WINDOWS else signal.SIGTERM)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("%s did not stop gracefully, killing group...", self.spec.name)
                kill_process_group(pid, signal.SIGKILL if not IS_WINDOWS else signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    logger.error("%s still alive after SIGKILL", self.spec.name)
            except Exception as e:  # noqa: BLE001
                logger.error("Error stopping %s: %s", self.spec.name, e)
            finally:
                self._process = None
                self._remove_pid_file()
                logger.info("%s stopped.", self.spec.name)

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def status(self) -> dict:
        return {
            "name": self.spec.name,
            "owner": self.spec.owner,
            "running": self.is_running(),
            "pid": self._process.pid if self._process else None,
            "restarts": self._restart_count,
        }

    # ── Stale-kill ───────────────────────────────────────────────────
    def _stale_kill(self) -> None:
        """Kill a leftover process from a previous run if the PID-file matches.

        Guarded by a command signature: we only kill if the live PID's cmdline
        still contains our signature — the defense against killing a recycled PID
        (the highest risk in this plan).
        """
        pid, sig = self._read_pid_file()
        if pid is None or not is_pid_alive(pid):
            self._remove_pid_file()
            return
        expected = sig or self.spec.signature
        cmdline = process_cmdline(pid)
        if expected and cmdline and expected not in cmdline:
            logger.warning(
                "%s: PID-file pid=%s is alive but cmdline does not match signature "
                "%r — NOT killing (likely recycled PID)", self.spec.name, pid, expected)
            self._remove_pid_file()
            return
        logger.warning("%s: killing stale process pid=%s from previous run", self.spec.name, pid)
        kill_process_group(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5.0
        while is_pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if is_pid_alive(pid):
            kill_process_group(pid, signal.SIGKILL)
        self._remove_pid_file()

    # ── Readiness ────────────────────────────────────────────────────
    def _await_readiness(self) -> None:
        probe = self.spec.readiness
        if probe is None:
            return
        deadline = time.monotonic() + self.spec.readiness_timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                logger.warning("%s exited before readiness", self.spec.name)
                return
            try:
                if probe():
                    logger.info("%s ready", self.spec.name)
                    return
            except Exception:
                pass
            time.sleep(0.3)
        logger.warning("%s readiness timed out after %.0fs", self.spec.name, self.spec.readiness_timeout)

    # ── Watchdog ─────────────────────────────────────────────────────
    def _start_watchdog(self) -> None:
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, daemon=True, name=f"{_safe_name(self.spec.name)}-watchdog")
        self._watchdog.start()

    def _watchdog_loop(self) -> None:
        while self._running:
            proc = self._process
            if proc is not None and proc.poll() is not None:
                code = proc.returncode
                logger.warning("%s exited with code %s", self.spec.name, code)
                self._process = None
                if not self._running:
                    break
                now = time.monotonic()
                if now - self._restart_window_start > self.spec.window_sec:
                    self._restart_count = 0
                    self._restart_window_start = now
                self._restart_count += 1
                if self._restart_count > self.spec.max_restarts:
                    logger.error("%s crashed %d times in %.0fs, giving up.",
                                 self.spec.name, self._restart_count, self.spec.window_sec)
                    self._running = False
                    _emit("subprocess.crashed", {"name": self.spec.name, "owner": self.spec.owner})
                    break
                logger.info("Restarting %s in %.0fs (attempt %d/%d)", self.spec.name,
                            self.spec.restart_delay, self._restart_count, self.spec.max_restarts)
                time.sleep(self.spec.restart_delay)
                if self._running:
                    try:
                        with self._lock:
                            self._spawn()
                        self._await_readiness()
                        _emit("subprocess.restarted", {"name": self.spec.name, "owner": self.spec.owner})
                        if self.spec.on_restart:
                            try:
                                self.spec.on_restart()
                            except Exception as cb:  # noqa: BLE001
                                logger.error("%s on_restart callback error: %s", self.spec.name, cb)
                    except Exception as e:  # noqa: BLE001
                        logger.error("Failed to restart %s: %s", self.spec.name, e)
                break  # a fresh watchdog is started by _spawn→start path
            time.sleep(2)

    # ── PID file ─────────────────────────────────────────────────────
    def _write_pid_file(self, pid: int) -> None:
        try:
            self.spec.pid_file.parent.mkdir(parents=True, exist_ok=True)
            sig = self.spec.signature or (self.spec.cmd[0] if self.spec.cmd else "")
            self.spec.pid_file.write_text(f"{pid}\n{sig}\n", encoding="utf-8")
        except OSError as e:
            logger.debug("%s: could not write pid file: %s", self.spec.name, e)

    def _read_pid_file(self) -> tuple[Optional[int], str]:
        try:
            lines = self.spec.pid_file.read_text(encoding="utf-8").splitlines()
            pid = int(lines[0].strip())
            sig = lines[1].strip() if len(lines) > 1 else ""
            return pid, sig
        except (FileNotFoundError, ValueError, IndexError, OSError):
            return None, ""

    def _remove_pid_file(self) -> None:
        try:
            self.spec.pid_file.unlink(missing_ok=True)
        except OSError:
            pass


class SubprocessService:
    """Registry of managed processes, owned by the core or by plugins."""

    def __init__(self) -> None:
        self._procs: dict[str, ManagedProcess] = {}

    def spawn(self, spec: SubprocessSpec) -> ManagedProcess:
        existing = self._procs.get(spec.name)
        if existing is not None and existing.is_running():
            return existing
        mp = ManagedProcess(spec)
        self._procs[spec.name] = mp
        mp.start()
        return mp

    def get(self, name: str) -> Optional[ManagedProcess]:
        return self._procs.get(name)

    def stop(self, name: str) -> None:
        mp = self._procs.get(name)
        if mp is not None:
            mp.stop()

    def stop_owner(self, owner: str) -> None:
        for mp in list(self._procs.values()):
            if mp.spec.owner == owner:
                mp.stop()

    def stop_all(self) -> None:
        for mp in list(self._procs.values()):
            try:
                mp.stop()
            except Exception as e:  # noqa: BLE001
                logger.error("Error stopping %s: %s", mp.spec.name, e)

    def status(self) -> list:
        return [mp.status() for mp in self._procs.values()]


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _emit(event: str, payload: dict) -> None:
    try:
        from plugins.events import emit
        emit(event, payload)
    except Exception as e:  # observability must never break process control
        logger.debug("emit %s failed: %s", event, e)
