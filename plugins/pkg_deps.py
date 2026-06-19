"""Shared pip dependency installer for runtime-loaded code.

Single choke point for installing third-party Python packages declared by code
that ships outside ``requirements.txt`` — currently **plugins** (manifest
``dependencies``) and **code-in-DB AI tools** (``ai_tools.dependencies``). Both
call :func:`ensure_pip_deps`, so the install behaviour, timeout and security
policy never drift between the two.

Design notes:

- **Check-before-install**: the caller passes the already-installed spec set
  (``already``); pip only touches the network the first time a spec set changes.
- **Security choke point**: :func:`is_dep_allowed` is the one place to close the
  policy (allowlist) later — open in the MVP, one-line change to restrict.
- **Fail-closed**: any problem raises; the caller decides how to surface it
  (plugin → ``load_error``; AI tool → ``install_status='failed'``). The app
  still boots either way.
- **Frozen builds**: in a PyInstaller/frozen interpreter there is no pip and
  ``sys.executable`` is the bundled app, so we refuse with a clear message
  instead of spawning a broken subprocess.
"""

from __future__ import annotations

import importlib
import logging
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

# Generous: a cold install of something like google-auth + transitive deps can
# take a while on a slow connection.
PIP_TIMEOUT = 600  # seconds


def is_dep_allowed(pkg: str) -> bool:
    """Allowlist gate for a dependency package name.

    OPEN in the MVP (returns ``True`` for everything) but the single choke point
    is here: closing the policy later (curated allowlist) is a one-line change,
    no refactor — both plugins and AI tools route through it.
    """
    return True


def pkg_name(spec: str) -> str:
    """Extract the bare package name from a pip spec (``httpx>=0.27`` → ``httpx``)."""
    m = re.match(r"^[A-Za-z0-9_.\-]+", (spec or "").strip())
    return m.group(0) if m else (spec or "").strip()


def ensure_pip_deps(
    deps,
    *,
    already=None,
    label: str = "",
) -> bool:
    """Install ``deps`` via pip unless already satisfied.

    Returns ``True`` if pip actually ran, ``False`` if it was a no-op (empty or
    the spec set already matches ``already``). The caller is responsible for
    persisting the installed set so the next boot short-circuits.

    Raises:
        PermissionError: a dependency is blocked by :func:`is_dep_allowed`.
        RuntimeError: pip failed, or running inside a frozen build.
        subprocess.TimeoutExpired: pip exceeded :data:`PIP_TIMEOUT`.
    """
    deps = [str(d).strip() for d in (deps or []) if str(d).strip()]
    if not deps:
        return False
    # Cache marker: skip pip entirely when the installed set already matches.
    if already is not None and list(already) == deps:
        return False

    blocked = [d for d in deps if not is_dep_allowed(pkg_name(d))]
    if blocked:
        raise PermissionError(f"dependencies not allowed by policy: {blocked}")

    if getattr(sys, "frozen", False):
        raise RuntimeError(
            "pip indisponível em build empacotado (frozen); instale as "
            "dependências manualmente no ambiente: " + ", ".join(deps)
        )

    tag = f"{label}: " if label else ""
    logger.info("%sinstalling dependencies %s", tag, deps)
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", *deps],
        capture_output=True,
        text=True,
        timeout=PIP_TIMEOUT,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"pip install failed (code {proc.returncode}): {tail}")

    # Make the freshly installed package importable in THIS process without a
    # restart — both consumers install then import in the same boot pass.
    importlib.invalidate_caches()
    return True
