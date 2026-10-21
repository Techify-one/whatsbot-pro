"""Atualizador do binário do GOWA (dono: este plugin).

Baixa uma release do repositório oficial ``go-whatsapp-web-multidevice``, confere
o SHA-256 contra o ``checksums.txt`` publicado, troca o binário em
``storages/bin/`` e reinicia os processos; se o build novo não passar no health
check, volta sozinho para o anterior.

Só stdlib e nada do core importado no topo: a lógica é testável sem app, banco ou
subprocesso. Tudo é SÍNCRONO e pensado para rodar em ``asyncio.to_thread``. O que
é específico do servidor (parar/subir o GOWA compartilhado e os dedicados por
proxy, health check, onde ficam as sessões) entra por um objeto ``runtime`` com
cinco métodos — ver :class:`lifecycle.GowaRuntime`:

* ``stop()`` / ``start()``        — derruba / sobe TODOS os processos GOWA;
* ``wait_healthy(timeout)``       — o GOWA compartilhado responde?
* ``running_version()``           — versão que o processo VIVO diz ser ('' = não sei);
* ``session_root()``              — pasta-base onde o GOWA grava ``storages/*.db``.

Dois lugares para o binário (mesma ideia do WhatsBot-Lite):

* ``bin/gowa``          — o **bundled**: o que a imagem Docker / o launcher trouxe.
  Somente leitura em runtime (no Docker é symlink para a camada da imagem, que
  some no redeploy).
* ``storages/bin/gowa`` — o **gerenciado**: instalado por este módulo. ``storages/``
  é o volume persistente, então só ele sobrevive a redeploy.

``resolve_binary()`` COMPARA versões em vez de preferir sempre o gerenciado: uma
imagem reconstruída com um GOWA mais novo vence um override velho esquecido.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

GOWA_GH_REPO = "aldinokemal/go-whatsapp-web-multidevice"
GOWA_RELEASES_API = f"https://api.github.com/repos/{GOWA_GH_REPO}/releases/latest"
GOWA_RELEASE_DOWNLOAD = (
    f"https://github.com/{GOWA_GH_REPO}/releases/download/v{{version}}/{{asset}}"
)
GOWA_RELEASE_PAGE = f"https://github.com/{GOWA_GH_REPO}/releases/tag/v{{version}}"

# Faixa que o WhatsBot sabe operar. Fora dela a versão ainda é oferecida, mas só
# instala com confirmação explícita. 8.11.0 é o piso porque é o que o Pro rodava
# antes deste atualizador; o teto é o próximo major (10.x ainda não foi avaliado).
SUPPORTED_RANGE = ">=8.11.0,<10.0.0"

# Usado só quando nada diz qual versão veio em ``bin/`` (sem ``.gowa_stamp`` e sem
# arquivo ``GOWA_VERSION``). Manter igual ao ``ARG GOWA_VERSION`` do Dockerfile.
BASELINE_VERSION = "8.11.0"

_CHECK_TTL_SEC = 3600        # cache da API de releases do GitHub
_FORCE_MIN_INTERVAL = 60     # piso entre dois "Verificar" manuais
_HEALTH_TIMEOUT_SEC = 60
_DOWNLOAD_TIMEOUT_SEC = 120
_API_TIMEOUT_SEC = 15
_PROBE_TIMEOUT_SEC = 3
_CHUNK = 64 * 1024

# A URL de download é SEMPRE montada aqui, a partir de uma versão que passou por
# este regex. Nunca aceitar URL vinda do cliente: viraria SSRF / path traversal.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_UA = "WhatsBot"

# O GOWA grava a sessão do WhatsApp aqui (NÃO em ``whatsbot.db``, que é nosso).
# Um major pode migrar esse schema sem volta, então tiramos um snapshot antes de
# atravessar a fronteira — senão o botão "Reverter" seria teatro.
_SESSION_DB_PREFIXES = ("whatsapp.db", "chatstorage.db")


class GowaUpdateBusy(RuntimeError):
    """Já existe uma atualização/reversão em andamento."""


class GowaUpdateError(RuntimeError):
    """Falha com mensagem própria para o usuário."""


_LOCK = threading.Lock()
_MUTE_AFTER_SEC = 90         # silêncio do alerta de desconexão após a troca
_muted_until = 0.0
_release_cache: dict = {"data": None, "fetched_at": 0.0, "etag": ""}
_state: dict = {"status": "idle", "phase": "", "progress": 0, "version": "",
                "message": "", "last_result": None}
_runtime = None  # registrado por ``lifecycle.setup`` (None = fora do servidor)


# ── Plataforma / assets ─────────────────────────────────────────────────────

_OS_MAP = {"linux": "linux", "darwin": "darwin", "win32": "windows"}
_ARCH_MAP = {
    "x86_64": "amd64", "amd64": "amd64",
    "aarch64": "arm64", "arm64": "arm64",
    "i386": "386", "i686": "386", "x86": "386",
    "armv7l": "armv7", "armv7": "armv7", "armv6l": "armv7",
}
# armv7 e 386 só existem para linux e windows.
_VALID_COMBOS = {
    ("linux", "amd64"), ("linux", "arm64"), ("linux", "386"), ("linux", "armv7"),
    ("darwin", "amd64"), ("darwin", "arm64"),
    ("windows", "amd64"), ("windows", "386"),
}


def detect_platform() -> tuple[str, str] | None:
    """``(os, arch)`` no vocabulário das releases do GOWA, ou None se sem build."""
    os_ = _OS_MAP.get(sys.platform)
    arch = _ARCH_MAP.get(platform.machine().lower())
    if not os_ or not arch or (os_, arch) not in _VALID_COMBOS:
        return None
    return os_, arch


def asset_name(version: str, os_: str, arch: str) -> str:
    return f"whatsapp_{version}_{os_}_{arch}.zip"


def inner_binary_name(os_: str, arch: str) -> str:
    """Nome do binário DENTRO do zip: ``linux-amd64``, ``windows-amd64.exe``."""
    return f"{os_}-{arch}.exe" if os_ == "windows" else f"{os_}-{arch}"


def checksums_asset(os_: str) -> str:
    """Os checksums do macOS vivem em outro arquivo; o errado dá 404."""
    return "checksums-macos.txt" if os_ == "darwin" else "checksums.txt"


# ── Versões ─────────────────────────────────────────────────────────────────

def parse_version(value: str) -> tuple[int, int, int]:
    """``9.1.0`` / ``v9.1.0`` / ``9.1.0-rc1`` → tupla comparável.

    Qualquer coisa ilegível vira ``(0, 0, 0)``: perde toda comparação em vez de
    levantar no meio de uma atualização.
    """
    if not value:
        return (0, 0, 0)
    text = str(value).strip().lstrip("vV")
    for sep in ("-", "+", " "):
        if sep in text:
            text = text.split(sep, 1)[0]
    numbers: list[int] = []
    for part in text.split(".")[:3]:
        try:
            numbers.append(int(part))
        except (TypeError, ValueError):
            return (0, 0, 0)
    if not numbers:
        return (0, 0, 0)
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def version_cmp(a: str, b: str) -> int:
    va, vb = parse_version(a), parse_version(b)
    return -1 if va < vb else (1 if va > vb else 0)


_RANGE_RE = re.compile(r"^\s*(>=|<=|==|>|<)\s*(\S+)\s*$")


def is_supported(version: str, spec: str = SUPPORTED_RANGE) -> bool:
    """Avalia ``version`` contra uma faixa como ``>=8.11.0,<10.0.0``."""
    if not version:
        return False
    current = parse_version(version)
    for clause in spec.split(","):
        match = _RANGE_RE.match(clause)
        if not match:
            continue
        op, bound = match.group(1), parse_version(match.group(2))
        ok = {">=": current >= bound, "<=": current <= bound, "==": current == bound,
              ">": current > bound, "<": current < bound}[op]
        if not ok:
            return False
    return True


def _is_major_jump(current: str, target: str) -> bool:
    return parse_version(current)[0] != parse_version(target)[0]


# ── Caminhos ────────────────────────────────────────────────────────────────

def repo_root() -> Path:
    """Raiz do projeto — a mesma que o core usa para achar ``bin/gowa``."""
    try:
        from gowa.manager import _get_gowa_binary
        return _get_gowa_binary().parent.parent
    except Exception:  # noqa: BLE001 — sem o core (teste): cai no cwd
        return Path.cwd()


def binary_name() -> str:
    return "gowa.exe" if sys.platform == "win32" else "gowa"


def bundled_binary_path() -> Path:
    return repo_root() / "bin" / binary_name()


def managed_dir(create: bool = False) -> Path:
    path = repo_root() / "storages" / "bin"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def managed_binary_path() -> Path:
    return managed_dir() / binary_name()


def backup_binary_path() -> Path:
    return managed_dir() / (binary_name() + ".bak")


def meta_path() -> Path:
    return managed_dir() / "gowa_meta.json"


def tmp_dir(create: bool = False) -> Path:
    """Área de staging. Dentro de ``storages/bin`` de propósito: o ``os.replace``
    final só é atômico no MESMO filesystem, e ``/tmp`` costuma ser outro mount."""
    path = managed_dir(create=create) / ".tmp"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def session_backup_dir(version: str) -> Path:
    return managed_dir() / "session_backup" / (version or "desconhecida")


# ── Resolução do binário ────────────────────────────────────────────────────

def read_bundled_version() -> str:
    """Versão do binário de ``bin/``.

    ``.gowa_stamp`` (escrito pelos launchers/Dockerfile depois do download) é
    autoritativo para aquela máquina; o arquivo ``GOWA_VERSION`` é o plano B.
    """
    root = repo_root()
    candidates = [root / "bin" / ".gowa_stamp", root / "GOWA_VERSION"]
    try:  # symlink do Docker: o stamp pode estar ao lado do binário real
        candidates.insert(0, bundled_binary_path().resolve().parent / ".gowa_stamp")
    except OSError:
        pass
    for path in candidates:
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip().lstrip("vV")
                if _VERSION_RE.match(value):
                    return value
        except OSError as exc:
            logger.debug("gowa updater: não li %s: %s", path, exc)
    return BASELINE_VERSION


def read_meta() -> dict:
    """``storages/bin/gowa_meta.json``. Nunca levanta."""
    try:
        data = json.loads(meta_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}
    except json.JSONDecodeError:
        logger.warning("gowa_meta.json corrompido — ignorando.")
        return {}
    return data if isinstance(data, dict) else {}


def write_meta(data: dict) -> None:
    """Escrita atômica (arquivo temporário + ``os.replace``)."""
    managed_dir(create=True)
    target = meta_path()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def resolve_binary() -> tuple[Path, str, str]:
    """``(caminho, origem, versão)`` do binário a executar; origem = managed|bundled."""
    bundled_version = read_bundled_version()
    managed = managed_binary_path()
    if managed.is_file():
        managed_version = read_meta().get("version", "")
        if managed_version and version_cmp(managed_version, bundled_version) >= 0:
            return managed, "managed", managed_version
        logger.info("GOWA gerenciado (%s) é mais antigo que o do sistema (%s); usando bin/.",
                    managed_version or "sem metadados", bundled_version)
    return bundled_binary_path(), "bundled", bundled_version


def apply_binary(cmd: list) -> list:
    """Aponta o ``argv[0]`` do comando para o binário resolvido.

    O core monta o argv com ``bin/gowa`` fixo (``GOWAManager._build_cmd``); em vez
    de mexer nele, o plugin troca só o executável — e SÓ quando há um binário
    gerenciado a usar. Sem ele o ``cmd`` volta byte a byte igual (contrato histórico
    do argv compartilhado). Falha segura: qualquer problema devolve o original.
    """
    try:
        path, source, _ = resolve_binary()
        if source == "managed" and cmd and path.is_file():
            return [str(path), *cmd[1:]]
    except Exception as exc:  # noqa: BLE001
        logger.warning("gowa updater: não consegui resolver o binário (%s); usando o padrão", exc)
    return cmd


def can_rollback() -> bool:
    return managed_binary_path().is_file()


def backup_version() -> str:
    """Versão que o rollback restauraria, ou '' quando não há como reverter."""
    if backup_binary_path().is_file():
        return read_meta().get("previous_version", "") or "anterior"
    # Sem .bak o anterior era o bundled, que sempre existe: reverter é largar o gerenciado.
    if managed_binary_path().is_file():
        return read_bundled_version()
    return ""


def probe_running_version(port: int, timeout: float = _PROBE_TIMEOUT_SEC) -> str:
    """Pergunta a versão ao processo VIVO. '' quando não responde / não reconheço.

    ``/app/info`` existe do 9.0.0 em diante; o 8.x só mostra a versão na página
    raiz. Esta é a única fonte que vale para "o que está rodando agora".
    """
    probes = (("/app/info", r'"version"\s*:\s*"v?(\d+\.\d+\.\d+)'),
              ("/", r"v(\d+\.\d+\.\d+)"))
    for path, pattern in probes:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}{path}",
                                        timeout=timeout) as resp:
                body = resp.read(65536).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            continue  # URLError/HTTPError herdam de OSError
        match = re.search(pattern, body)
        if match:
            return match.group(1)
    return ""


# ── Estado / runtime ────────────────────────────────────────────────────────

def set_runtime(runtime) -> None:
    global _runtime
    _runtime = runtime


def get_runtime():
    return _runtime


def is_busy() -> bool:
    return _LOCK.locked()


def get_state() -> dict:
    return dict(_state)


def _set_state(status: str, phase: str = "", progress: int = 0,
               version: str = "", message: str = "") -> None:
    _state.update({"status": status, "phase": phase, "progress": progress,
                   "version": version, "message": message})


def status(runtime=None) -> dict:
    """Retrato local (sem rede) do que está instalado e do que dá para fazer."""
    path, source, resolved = resolve_binary()
    running = ""
    # Durante a troca o processo está sendo reiniciado: sondá-lo só atrasaria o polling.
    if runtime is not None and not is_busy():
        try:
            running = runtime.running_version() or ""
        except Exception:  # noqa: BLE001
            running = ""
    installed = running or resolved
    combo = detect_platform()
    return {
        "managed": runtime is not None,
        "installed_version": installed,
        "running_version": running,
        "source": source,
        "bundled_version": read_bundled_version(),
        "supported_range": SUPPORTED_RANGE,
        "supported": is_supported(installed),
        "can_rollback": can_rollback(),
        "backup_version": backup_version(),
        "binary_path": str(path),
        "platform_supported": combo is not None,
        "platform": "/".join(combo) if combo else f"{sys.platform}/{platform.machine()}",
        "busy": is_busy(),
        "state": get_state(),
    }


# ── GitHub ──────────────────────────────────────────────────────────────────

def _api_headers() -> dict:
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": _UA}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_latest_release(force: bool = False) -> dict:
    """``{version, published_at, url, rate_limited, cached}`` da última release.

    Cache de 1h: a API anônima do GitHub dá 60 req/h por IP, e uma resposta de
    rate-limit cai no cache em vez de virar erro na tela.
    """
    now = time.monotonic()
    age = now - float(_release_cache.get("fetched_at") or 0.0)
    cached = _release_cache.get("data")
    if cached and not force and age < _CHECK_TTL_SEC:
        return {**cached, "cached": True, "rate_limited": False}
    if cached and force and age < _FORCE_MIN_INTERVAL:
        return {**cached, "cached": True, "rate_limited": False}

    headers = _api_headers()
    if cached and _release_cache.get("etag"):
        headers["If-None-Match"] = _release_cache["etag"]
    req = urllib.request.Request(GOWA_RELEASES_API, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_SEC) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            new_etag = resp.headers.get("ETag", "")
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cached:
            _release_cache["fetched_at"] = now
            return {**cached, "cached": True, "rate_limited": False}
        if exc.code in (403, 429) and exc.headers.get("X-RateLimit-Remaining") == "0":
            logger.warning("gowa updater: rate limit do GitHub; servindo o cache.")
            if cached:
                return {**cached, "cached": True, "rate_limited": True}
            raise GowaUpdateError(
                "Limite de consultas do GitHub atingido. Tente de novo em alguns minutos.") from exc
        raise GowaUpdateError(f"GitHub respondeu {exc.code} ao consultar a release.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        if cached:
            logger.warning("gowa updater: consulta falhou (%s); servindo o cache.", exc)
            return {**cached, "cached": True, "rate_limited": False}
        raise GowaUpdateError(f"Não consegui consultar as releases do GOWA: {exc}") from exc

    version = (payload.get("tag_name") or "").lstrip("vV")
    data = {"version": version,
            "published_at": payload.get("published_at") or "",
            "url": payload.get("html_url") or GOWA_RELEASE_PAGE.format(version=version)}
    _release_cache.update({"data": data, "fetched_at": now, "etag": new_etag})
    return {**data, "cached": False, "rate_limited": False}


def check(runtime=None, force: bool = False) -> dict:
    """``status()`` + o que há de novo no GitHub."""
    out = {**status(runtime), "latest_version": "", "update_available": False,
           "latest_supported": False, "major_jump": False, "release_url": "",
           "published_at": "", "rate_limited": False, "error": ""}
    try:
        latest = fetch_latest_release(force=force)
    except GowaUpdateError as exc:
        out["error"] = str(exc)
        return out
    version = latest["version"]
    out.update({
        "latest_version": version,
        "release_url": latest["url"],
        "published_at": latest["published_at"],
        "rate_limited": latest.get("rate_limited", False),
        "latest_supported": is_supported(version),
        "update_available": bool(version) and version_cmp(version, out["installed_version"]) > 0,
        "major_jump": bool(version) and _is_major_jump(out["installed_version"], version),
    })
    return out


# ── Download / verificação ──────────────────────────────────────────────────

def _download(url: str, dest: Path, version: str) -> Path:
    """Baixa ``url`` em ``dest`` atualizando o progresso (0–99%)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last_pct = -1
    try:
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SEC) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            read = 0
            with dest.open("wb") as fh:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    read += len(chunk)
                    if total:
                        pct = min(int(read * 100 / total), 99)
                        if pct != last_pct:
                            last_pct = pct
                            _set_state("running", "downloading", pct, version)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        dest.unlink(missing_ok=True)
        raise GowaUpdateError(f"Falha ao baixar {url}: {exc}") from exc
    return dest


def _fetch_checksums(version: str, os_: str) -> dict[str, str]:
    """``{asset: sha256}`` do arquivo de checksums da release ({} se indisponível)."""
    url = GOWA_RELEASE_DOWNLOAD.format(version=version, asset=checksums_asset(os_))
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_SEC) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("gowa updater: sem checksums (%s): %s", url, exc)
        return {}
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            sums[parts[-1]] = parts[0].lower()
    return sums


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_binary(zip_path: Path, member: str, dest: Path) -> None:
    """Extrai UM membro do zip, com guarda contra zip-slip."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        target = member if member in names else None
        if target is None:
            candidates = [i for i in zf.infolist()
                          if not i.is_dir() and not i.filename.lower().endswith(".md")]
            if not candidates:
                raise GowaUpdateError(f"O zip do GOWA não contém o binário {member}.")
            target = max(candidates, key=lambda i: i.file_size).filename
            logger.warning("gowa updater: %s ausente no zip, usando %s", member, target)
        if ".." in Path(target).parts or Path(target).is_absolute():
            raise GowaUpdateError(f"Entrada suspeita no zip do GOWA: {target}")
        with zf.open(target) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)


def _clear_quarantine(path: Path) -> None:
    """Tira a quarentena do Gatekeeper (macOS); sem isso o binário morre com SIGKILL
    ao subir e o watchdog gasta as 3 tentativas em 60s e desiste calado."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(path)],
                       capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("gowa updater: xattr falhou em %s: %s", path, exc)


def _smoke_test(path: Path) -> None:
    """Executa o binário uma vez para pegar download de arquitetura errada ANTES de
    mexer no que está em uso. O código de saída é irrelevante; só importa rodar."""
    try:
        subprocess.run([str(path), "--help"], capture_output=True, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        pass  # rodou, só não saiu rápido
    except OSError as exc:
        raise GowaUpdateError(
            f"O binário baixado não executa nesta máquina ({exc}). "
            "Provavelmente é de outra arquitetura.") from exc


def _replace_with_retry(src: Path, dest: Path, attempts: int = 10) -> None:
    """``os.replace`` com retentativas: o Windows segura o .exe recém-fechado."""
    for attempt in range(attempts):
        try:
            os.replace(src, dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)


# ── Snapshot da sessão (só em major) ────────────────────────────────────────

def session_files(root: Path) -> list[Path]:
    """Bancos de sessão do GOWA: o do processo compartilhado (``storages/``) e o de
    cada dedicado por proxy (``storages/gowa_ch_<id>/storages/``). Inclui ``-wal``
    e ``-shm`` — copiar só o ``.db`` de um SQLite em WAL perde o que ainda não foi
    consolidado."""
    dirs = [root / "storages", *sorted((root / "storages").glob("gowa_ch_*/storages"))]
    found: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        found.extend(p for p in directory.iterdir()
                     if p.is_file() and p.name.startswith(_SESSION_DB_PREFIXES))
    return found


def _backup_session(version: str, root: Path) -> str:
    """Copia as sessões para ``storages/bin/session_backup/<versão>/`` (mesma árvore
    relativa à raiz). Devolve a pasta ou '' se não havia nada. FALHA ALTO: um
    snapshot incompleto antes de uma migração irreversível é pior que nenhum."""
    files = session_files(root)
    if not files:
        return ""
    target = session_backup_dir(version)
    shutil.rmtree(target, ignore_errors=True)
    for src in files:
        dest = target / src.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            raise GowaUpdateError(
                f"Não consegui salvar o backup da sessão ({src.name}): {exc}. "
                "Atualização cancelada.") from exc
    logger.info("gowa updater: snapshot da sessão em %s (%d arquivos)", target, len(files))
    return str(target)


def _restore_session(backup_dir: str, root: Path) -> None:
    if not backup_dir:
        return
    source = Path(backup_dir)
    if not source.is_dir():
        return
    files = [p for p in source.rglob("*") if p.is_file()]
    # Um -wal/-shm que sobrou do schema NOVO, casado com o .db antigo, corrompe.
    for f in files:
        dest = root / f.relative_to(source)
        base = re.sub(r"-(wal|shm)$", "", dest.name)
        for suffix in ("-wal", "-shm"):
            (dest.parent / (base + suffix)).unlink(missing_ok=True)
    for f in files:
        dest = root / f.relative_to(source)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        except OSError as exc:
            logger.warning("gowa updater: não restaurei %s: %s", dest.name, exc)
    logger.info("gowa updater: sessão restaurada de %s", backup_dir)


# ── Instalação ──────────────────────────────────────────────────────────────

def preflight(version: str, force_unsupported: bool = False) -> dict | None:
    """None se dá para prosseguir; senão o dict de resposta (``ok: False``).

    Roda na rota ANTES de abrir a thread, para a tela receber na hora o pedido de
    confirmação / o erro de formato, sem esperar o ciclo de download.
    """
    version = (version or "").strip().lstrip("vV")
    if not _VERSION_RE.match(version):
        return {"ok": False, "version": version,
                "message": f"Versão inválida: {version!r}. Use o formato x.y.z."}
    if not is_supported(version) and not force_unsupported:
        return {"ok": False, "requires_confirmation": True, "version": version,
                "supported_range": SUPPORTED_RANGE,
                "message": (f"A versão {version} está fora da faixa homologada "
                            f"({SUPPORTED_RANGE}). Confirme para instalar mesmo assim.")}
    if detect_platform() is None:
        return {"ok": False, "version": version,
                "message": (f"Atualização automática indisponível em "
                            f"{sys.platform}/{platform.machine()}: o GOWA não publica "
                            "binário para esta combinação.")}
    return None


def install(version: str, *, runtime, force_unsupported: bool = False) -> dict:
    """Baixa, verifica e instala o GOWA ``version``. Volta atrás se falhar.

    Levanta :class:`GowaUpdateBusy` só se já houver outra operação; qualquer outra
    falha vira ``{"ok": False, ...}`` — roda numa thread de fundo, onde exceção
    solta seria um erro mudo.
    """
    if not _LOCK.acquire(blocking=False):
        raise GowaUpdateBusy("Já existe uma atualização do GOWA em andamento.")
    result: dict = {"ok": False, "message": "Erro inesperado."}
    try:
        _state["last_result"] = None
        result = _install_locked(version, runtime, force_unsupported)
        return result
    finally:
        _finish(result)


def _finish(result: dict) -> None:
    """Fecha a operação: estado ocioso, resultado visível à tela e silêncio dos
    alertas (os números reconectam sozinhos nos segundos seguintes ao reinício)."""
    global _muted_until
    _muted_until = time.time() + _MUTE_AFTER_SEC
    _set_state("idle")
    _state["last_result"] = {**result, "finished_at": time.time()}
    _LOCK.release()


def alerts_muted() -> bool:
    """True durante a troca e logo depois dela. O alerta de desconexão consulta isto:
    reiniciar o GOWA derruba todos os números por alguns segundos, e sem a pausa a
    própria atualização mandaria um falso "WhatsApp desconectado" ao Telegram."""
    return is_busy() or time.time() < _muted_until


def _install_locked(version: str, runtime, force_unsupported: bool) -> dict:
    version = (version or "").strip().lstrip("vV")
    pre = preflight(version, force_unsupported)
    if pre is not None:
        return pre

    os_, arch = detect_platform()  # type: ignore[misc] — preflight garantiu
    tmp_root = tmp_dir(create=True)
    root = Path(runtime.session_root())
    # Dois degraus de "já mexi": parar os processos exige SUBIR de novo; trocar o
    # binário exige DEVOLVER o anterior. Confundir os dois apagava um binário
    # gerenciado bom quando a falha vinha entre o stop e a troca (ex.: backup).
    stopped = False
    swapped = False
    session_backup = ""
    previous_version = previous_source = ""
    try:
        _, previous_source, resolved = resolve_binary()
        previous_version = (runtime.running_version() or "") or resolved
        supported = is_supported(version)
        _set_state("running", "downloading", 0, version)

        # 1. Baixa ao lado do destino final (mesmo filesystem p/ o os.replace).
        asset = asset_name(version, os_, arch)
        zip_path = tmp_root / asset
        _download(GOWA_RELEASE_DOWNLOAD.format(version=version, asset=asset), zip_path, version)

        # 2. Checksum. Sem ele NÃO instala: é binário executável vindo da rede.
        _set_state("running", "verifying", 100, version)
        expected = _fetch_checksums(version, os_).get(asset, "")
        if not expected:
            raise GowaUpdateError(
                "Não consegui obter o checksum publicado desta versão; "
                "atualização cancelada por segurança.")
        if _sha256(zip_path) != expected:
            raise GowaUpdateError(
                "O arquivo baixado não confere com o checksum publicado. "
                "Atualização cancelada.")

        # 3. Extrai e prova que RODA antes de tocar no binário em uso.
        staged = tmp_root / (binary_name() + ".new")
        staged.unlink(missing_ok=True)
        _extract_binary(zip_path, inner_binary_name(os_, arch), staged)
        staged.chmod(0o755)
        _clear_quarantine(staged)
        _smoke_test(staged)

        # 4. Para TUDO (o Windows trava o .exe em uso; e o SQLite só fica consistente
        #    com o GOWA parado) e, num major, tira o snapshot da sessão.
        _set_state("running", "installing", 100, version)
        stopped = True
        runtime.stop()
        if _is_major_jump(previous_version, version):
            session_backup = _backup_session(previous_version, root)

        # 5. Troca, guardando o anterior em .bak. Um .bak que sobrou de um ciclo
        #    antigo nunca vale: ou é recriado agora, ou some.
        managed, backup = managed_binary_path(), backup_binary_path()
        managed_dir(create=True)
        backup.unlink(missing_ok=True)
        if managed.is_file():
            _replace_with_retry(managed, backup)
        swapped = True  # a partir daqui o "anterior" mora no .bak (ou era o bundled)
        _replace_with_retry(staged, managed)
        managed.chmod(0o755)
        _clear_quarantine(managed)
        write_meta({
            "version": version, "installed_at": time.time(), "asset": asset,
            "sha256": expected, "checksum": "verified", "supported": supported,
            "previous_version": previous_version, "previous_source": previous_source,
            "session_backup": session_backup,
        })

        # 6. Sobe e prova que está vivo.
        _set_state("running", "restarting", 100, version)
        runtime.start()
        _set_state("running", "health_check", 100, version)
        if not runtime.wait_healthy(_HEALTH_TIMEOUT_SEC):
            raise GowaUpdateError(
                f"O GOWA {version} não respondeu em {_HEALTH_TIMEOUT_SEC}s após a atualização.")

        running = runtime.running_version() or ""
        if running and running != version:
            logger.info("gowa updater: o GOWA diz ser %s (esperado %s); gravando o real.",
                        running, version)
            meta = read_meta()
            meta["version"] = running
            write_meta(meta)
        logger.info("gowa updater: %s -> %s", previous_version, running or version)
        return {"ok": True, "version": running or version,
                "previous_version": previous_version, "supported": supported,
                "rolled_back": False,
                "message": f"GOWA atualizado para a versão {running or version}."}

    except Exception as exc:  # noqa: BLE001 — entre stop() e start() o WhatsApp está fora
        # do ar: QUALQUER falha tem de chegar ao rollback, não subir e deixá-lo parado.
        message = str(exc) if isinstance(exc, GowaUpdateError) else f"Erro inesperado: {exc}"
        logger.error("gowa updater: atualização para %s falhou: %s", version, message)
        if not isinstance(exc, (GowaUpdateError, OSError, zipfile.BadZipFile)):
            logger.exception("gowa updater: erro inesperado")
        rolled_back = False
        if swapped:
            _set_state("running", "rollback", 100, version)
            rolled_back = _restore(runtime, session_backup)
        elif stopped:
            # Parei os processos mas não cheguei a trocar o binário: nada a
            # desfazer, só religar o que já estava rodando.
            _set_state("running", "restarting", 100, version)
            try:
                runtime.start()
                runtime.wait_healthy(30)
            except Exception as restart_exc:  # noqa: BLE001
                logger.error("gowa updater: não consegui religar o GOWA: %s", restart_exc)
        return {"ok": False, "version": version, "previous_version": previous_version,
                "rolled_back": rolled_back, "message": message}
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _restore(runtime, session_backup: str = "") -> bool:
    """Devolve o binário anterior e sobe de novo. Melhor esforço, nunca levanta."""
    managed, backup = managed_binary_path(), backup_binary_path()
    try:
        runtime.stop()
        if backup.is_file():
            managed.unlink(missing_ok=True)
            _replace_with_retry(backup, managed)
            meta = read_meta()
            write_meta({"version": meta.get("previous_version", ""),
                        "installed_at": time.time(),
                        "rolled_back_from": meta.get("version", ""),
                        "previous_version": "", "previous_source": "bundled"})
        else:
            # Sem .bak o anterior era o bundled, que sempre existe.
            managed.unlink(missing_ok=True)
            meta_path().unlink(missing_ok=True)
        _restore_session(session_backup, Path(runtime.session_root()))
        runtime.start()
        runtime.wait_healthy(30)
        logger.info("gowa updater: revertido para %s", resolve_binary()[2])
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("gowa updater: a reversão falhou: %s", exc)
        return False


def rollback(*, runtime) -> dict:
    """Restaura o binário anterior a pedido do usuário."""
    if not _LOCK.acquire(blocking=False):
        raise GowaUpdateBusy("Já existe uma atualização do GOWA em andamento.")
    result: dict = {"ok": False, "rolled_back": False, "message": "Erro inesperado."}
    try:
        _state["last_result"] = None
        if not can_rollback():
            result = {"ok": False, "rolled_back": False,
                      "message": "Não há versão anterior do GOWA para restaurar."}
            return result
        target = backup_version()
        _set_state("running", "rollback", 100, target)
        if not _restore(runtime, read_meta().get("session_backup", "")):
            result = {"ok": False, "rolled_back": False,
                      "message": "Falha ao restaurar a versão anterior do GOWA."}
            return result
        current = resolve_binary()[2]
        result = {"ok": True, "version": current, "rolled_back": True,
                  "message": f"GOWA revertido para a versão {current}."}
        return result
    finally:
        _finish(result)
