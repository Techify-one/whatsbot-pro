import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_gowa_binary() -> Path:
    """Locate the GOWA binary."""
    base = Path(__file__).resolve().parent.parent
    binary = base / "bin" / ("gowa.exe" if sys.platform == "win32" else "gowa")
    return binary


class GOWAManager:
    """Manages the GOWA subprocess lifecycle."""

    def __init__(self, port: int = 3000, data_dir: Path | None = None,
                 webhook_url: str | None = None, on_restart=None):
        self.port = port
        self.webhook_url = webhook_url
        self.data_dir = data_dir or Path.home() / ".config" / "WhatsBot"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._process: subprocess.Popen | None = None
        self._running = False
        self._watchdog_thread: threading.Thread | None = None
        self._restart_count = 0
        self._restart_window_start = 0.0
        self._max_restarts = 3
        self._restart_window_sec = 60
        self._on_restart = on_restart

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self):
        """Start the GOWA process."""
        if self.is_running:
            logger.info("GOWA already running (pid=%s)", self._process.pid)
            return

        binary = _get_gowa_binary()
        if not binary.exists():
            raise FileNotFoundError(
                f"GOWA binary not found at {binary}. "
                "Place gowa.exe in the bin/ directory."
            )

        cmd = [
            str(binary),
            "rest",
            "--port", str(self.port),
        ]
        if self.webhook_url:
            cmd.extend(["--webhook", self.webhook_url])
        # Enable chat_presence webhook events (typing/recording indicators)
        cmd.extend(["--webhook-events", "message,chat_presence"])
        # Must be "available" to receive typing events from contacts
        cmd.extend(["--presence-on-connect", "available"])

        logger.info("Starting GOWA: %s", " ".join(cmd))
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        self._running = True
        logger.info("GOWA started (pid=%s)", self._process.pid)

        # Start watchdog
        self._watchdog_thread = threading.Thread(
            target=self._watchdog, daemon=True, name="gowa-watchdog"
        )
        self._watchdog_thread.start()

    def stop(self):
        """Stop the GOWA process gracefully."""
        self._running = False
        if self._process is None:
            return

        logger.info("Stopping GOWA (pid=%s)...", self._process.pid)
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("GOWA did not stop gracefully, killing...")
            self._process.kill()
            self._process.wait(timeout=3)
        except Exception as e:
            logger.error("Error stopping GOWA: %s", e)
        finally:
            self._process = None
            logger.info("GOWA stopped.")

    def restart(self):
        """Stop and start GOWA."""
        self.stop()
        time.sleep(1)
        self.start()

    def _watchdog(self):
        """Watch the GOWA process and restart on crash."""
        while self._running:
            if self._process and self._process.poll() is not None:
                exit_code = self._process.returncode
                logger.warning("GOWA exited with code %s", exit_code)
                self._process = None

                if not self._running:
                    break

                # Rate-limit restarts
                now = time.time()
                if now - self._restart_window_start > self._restart_window_sec:
                    self._restart_count = 0
                    self._restart_window_start = now

                self._restart_count += 1
                if self._restart_count > self._max_restarts:
                    logger.error(
                        "GOWA crashed %d times in %ds, giving up.",
                        self._restart_count,
                        self._restart_window_sec,
                    )
                    self._running = False
                    break

                logger.info("Restarting GOWA in 5 seconds... (attempt %d/%d)",
                            self._restart_count, self._max_restarts)
                time.sleep(5)
                if self._running:
                    try:
                        self.start()
                        if self._on_restart:
                            try:
                                self._on_restart()
                            except Exception as cb_err:
                                logger.error("on_restart callback error: %s", cb_err)
                    except Exception as e:
                        logger.error("Failed to restart GOWA: %s", e)
                break  # New watchdog thread is started by start()
            time.sleep(2)
