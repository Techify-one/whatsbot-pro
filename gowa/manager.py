import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from runtime.subprocess_service import ManagedProcess, SubprocessSpec

logger = logging.getLogger(__name__)

GOWA_LOG_MAX_BYTES = 10 * 1024 * 1024  # truncate above ~10 MB


def _get_gowa_binary() -> Path:
    """Locate the GOWA binary."""
    base = Path(__file__).resolve().parent.parent
    binary = base / "bin" / ("gowa.exe" if sys.platform == "win32" else "gowa")
    return binary


def _gowa_log_path() -> Path:
    """Return path to the GOWA debug log file (created lazily)."""
    base = Path(__file__).resolve().parent.parent
    logs_dir = base / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / "gowa.log"


def _debug_enabled() -> bool:
    return os.environ.get("WHATSBOT_GOWA_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


class GOWAManager:
    """Manages the GOWA subprocess lifecycle.

    Delegates the actual process control to ``runtime.subprocess_service`` (plano
    09 Fase 4): GOWA now runs in its own process group, dies with the Python
    parent (Linux ``PR_SET_PDEATHSIG`` — covers the plugin-toggle ``os._exit``),
    is stale-killed on boot (replacing the launcher's external ``pkill``), and is
    watchdogged with the same 3/60s rate-limit. The public API
    (``start``/``stop``/``restart``/``is_running``/``_on_restart``) is preserved.
    """

    def __init__(self, port: int = 3000, data_dir: Path | None = None,
                 webhook_url: str | None = None, on_restart=None,
                 readiness=None):
        self.port = port
        self.webhook_url = webhook_url
        self.data_dir = data_dir or Path.home() / ".config" / "WhatsBot"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._managed: ManagedProcess | None = None
        self._log_fh = None
        # Set after construction by the server (server/app.py); read dynamically
        # at restart time, so we keep it as an instance attribute, not captured.
        self._on_restart = on_restart
        self._readiness = readiness

    @property
    def is_running(self) -> bool:
        return self._managed is not None and self._managed.is_running()

    def _build_cmd(self) -> list[str]:
        binary = _get_gowa_binary()
        if not binary.exists():
            raise FileNotFoundError(
                f"GOWA binary not found at {binary}. "
                "Place gowa.exe in the bin/ directory."
            )
        cmd = [str(binary), "rest", "--port", str(self.port)]
        if self.webhook_url:
            cmd.extend(["--webhook", self.webhook_url])
        # Forward every event the webhook handler knows how to process. GOWA
        # only delivers events listed here; omitting one (e.g. group.participants)
        # silently drops it even though webhook.py has a handler.
        cmd.extend(["--webhook-events", ",".join([
            "message",
            "message.reaction",
            "message.edited",
            "message.revoked",
            "message.deleted",
            "message.ack",
            "chat_presence",
            "group.participants",
            "group.joined",
            "call.offer",
        ])])
        # Must be "available" to receive typing events from contacts
        cmd.extend(["--presence-on-connect", "available"])
        cmd.extend(["--os", "Techify - WhatsBot"])
        if _debug_enabled():
            cmd.extend(["--debug=true"])
        return cmd

    def start(self):
        """Start the GOWA process (via the managed subprocess service)."""
        if self.is_running:
            logger.info("GOWA already running (pid=%s)", self._managed.status().get("pid"))
            return

        cmd = self._build_cmd()
        debug_on = _debug_enabled()
        logger.info("Starting GOWA (debug=%s): %s", debug_on, " ".join(cmd))

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW  # no-op of compat off-Windows

        if debug_on:
            log_path = _gowa_log_path()
            try:
                if log_path.exists() and log_path.stat().st_size > GOWA_LOG_MAX_BYTES:
                    log_path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Could not rotate gowa.log: %s", e)
            self._log_fh = open(log_path, "ab", buffering=0)
            stdout_target = self._log_fh
            stderr_target = subprocess.STDOUT
            logger.info("GOWA debug logs -> %s", log_path)
        else:
            self._log_fh = None
            stdout_target = subprocess.DEVNULL
            stderr_target = subprocess.DEVNULL

        spec = SubprocessSpec(
            name="gowa",
            cmd=cmd,
            signature=str(_get_gowa_binary()),  # stale-kill guard: match GOWA binary path
            readiness=self._readiness,
            readiness_timeout=15.0,
            stdout=stdout_target,
            stderr=stderr_target,
            creationflags=creation_flags,
            max_restarts=3,
            window_sec=60.0,
            restart_delay=5.0,
            # Read _on_restart dynamically (server sets it after construction).
            on_restart=lambda: self._on_restart() if self._on_restart else None,
            owner="core",
        )
        self._managed = ManagedProcess(spec)
        self._managed.start()
        logger.info("GOWA started (pid=%s)", self._managed.status().get("pid"))

    def stop(self):
        """Stop the GOWA process gracefully (kills the whole process group)."""
        if self._managed is not None:
            try:
                self._managed.stop()
            except Exception as e:  # noqa: BLE001
                logger.error("Error stopping GOWA: %s", e)
            finally:
                self._managed = None
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None
        logger.info("GOWA stopped.")

    def restart(self):
        """Stop and start GOWA."""
        self.stop()
        time.sleep(1)
        self.start()
