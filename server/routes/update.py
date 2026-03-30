"""WhatsBot — self-update endpoint (uses GitHub Releases API for versioning)."""

import asyncio
import json
import logging
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

GITHUB_REPO = "Techify-one/whatsbot"
GITHUB_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

PRESERVE_DIRS = {"storages", "statics", "logs", "venv", ".git", "bin"}
PRESERVE_FILES = {".env"}


def _get_project_root(settings) -> Path:
    return Path(settings.data_dir)


def _read_local_version(project_root: Path) -> str:
    """Read version from git tags (latest tag matching v*)."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v*"],
            capture_output=True, text=True, cwd=str(project_root), timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lstrip("v")
    except Exception as exc:
        logger.debug("git describe failed: %s", exc)
    return "0.0.0"


def _fetch_remote_version() -> str:
    """Fetch latest release version from GitHub Releases API."""
    try:
        req = urllib.request.Request(
            GITHUB_RELEASES_API,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "WhatsBot"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            return tag.lstrip("v")
    except Exception as exc:
        logger.warning("Failed to fetch remote version: %s", exc)
        return ""


def _should_preserve(rel_path: str) -> bool:
    """Return True if *rel_path* must NOT be overwritten during update."""
    parts = Path(rel_path).parts
    if not parts:
        return True
    if parts[0] in PRESERVE_DIRS:
        return True
    if rel_path in PRESERVE_FILES:
        return True
    if "__pycache__" in parts:
        return True
    return False


def _perform_update(project_root: Path) -> str:
    """Download latest ZIP from GitHub and overwrite code files."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "update.zip"

        # ── Download ──────────────────────────────────────────────
        logger.info("Downloading update from %s", GITHUB_ZIP_URL)
        try:
            urllib.request.urlretrieve(GITHUB_ZIP_URL, str(zip_path))
        except Exception as exc:
            raise RuntimeError(f"Erro ao baixar atualização: {exc}") from exc

        # ── Extract ───────────────────────────────────────────────
        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile as exc:
            raise RuntimeError("Arquivo de atualização inválido.") from exc

        with zf:
            names = zf.namelist()
            if not names:
                raise RuntimeError("ZIP vazio.")

            # GitHub ZIPs have a top-level folder like "whatsbot-main/"
            top_folder = names[0].split("/")[0] + "/"

            extract_dir = tmp_path / "extracted"
            zf.extractall(extract_dir)

        source_root = extract_dir / top_folder.rstrip("/")
        if not source_root.is_dir():
            raise RuntimeError(f"Estrutura inesperada no ZIP (pasta {top_folder} não encontrada).")

        # ── Copy new files ────────────────────────────────────────
        copied = 0
        for src_file in source_root.rglob("*"):
            if src_file.is_dir():
                continue

            rel = src_file.relative_to(source_root).as_posix()

            # Security: reject path traversal
            if ".." in rel:
                logger.warning("Skipping suspicious path: %s", rel)
                continue

            if _should_preserve(rel):
                continue

            dest = project_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            copied += 1

        new_version = _read_local_version(project_root)
        logger.info("Update applied: %d files updated. New version: %s", copied, new_version)
        return f"Atualizado para v{new_version} — {copied} arquivos atualizados. Reinicie o servidor para aplicar."


def register_routes(app, deps):
    settings = deps.settings

    @app.get("/api/update/check")
    async def check_update():
        project_root = _get_project_root(settings)
        current = await asyncio.to_thread(_read_local_version, project_root)
        latest = await asyncio.to_thread(_fetch_remote_version)
        has_update = bool(latest and latest != current)
        return _ok({
            "current_version": current,
            "latest_version": latest,
            "update_available": has_update,
        })

    @app.post("/api/update")
    async def apply_update():
        project_root = _get_project_root(settings)
        try:
            msg = await asyncio.to_thread(_perform_update, project_root)
        except RuntimeError as exc:
            return _err(str(exc), 500)
        except Exception as exc:
            logger.exception("Unexpected error during update")
            return _err(f"Erro inesperado: {exc}", 500)
        return _ok({"message": msg})
