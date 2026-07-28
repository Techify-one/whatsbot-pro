"""App version resolution.

The running code version is used as the installation-metadata ``app_version``
(see ``config.settings._seed_installation_metadata``). There is no packaged
version (``pyproject.toml`` carries no ``[project]``; releases are cut from git
tags by the ``release-up`` skill), so the version is resolved at runtime from,
in order of precedence:

1. ``WHATSBOT_VERSION`` env — the reliable source in Docker/Coolify (inject it as
   a build ARG → ENV in the Dockerfile, or set it in the container envs).
2. A ``VERSION`` file at the project root (plain ``vX.Y.Z`` string).
3. ``git describe --tags --always`` — dev checkout fallback.
4. ``"unknown"`` — nothing resolved.

Resolved once at import and cached in ``APP_VERSION``.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _from_env() -> str | None:
    v = (os.environ.get("WHATSBOT_VERSION") or "").strip()
    return v or None


def _from_version_file() -> str | None:
    try:
        path = Path(__file__).resolve().parent.parent / "VERSION"
        if path.is_file():
            v = path.read_text(encoding="utf-8").strip()
            return v or None
    except OSError:
        pass
    return None


def _from_git() -> str | None:
    try:
        repo_root = Path(__file__).resolve().parent.parent
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0:
            v = out.stdout.strip()
            return v or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def get_app_version() -> str:
    """Resolve the running code version (see module docstring for precedence)."""
    for source in (_from_env, _from_version_file, _from_git):
        v = source()
        if v:
            return v
    return "unknown"


APP_VERSION = get_app_version()
