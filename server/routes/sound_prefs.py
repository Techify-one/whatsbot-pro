"""Preferências de som por usuário + catálogo + biblioteca de sons importados.

- ``GET  /api/me/sound-prefs``  → override esparso do usuário + padrão GLOBAL da
  equipe (para o cliente resolver o efetivo sem uma 2ª chamada) + catálogo.
- ``PUT  /api/me/sound-prefs``  → grava o override esparso (normalizado, fail-open).
- ``GET  /api/sounds/catalog``  → eventos + sons (sintetizados + IMPORTADOS).
- ``GET/POST /api/sounds/library`` e ``PUT/DELETE /api/sounds/library/{id}`` →
  biblioteca de sons importados pela equipe (nome escolhido no import).

Molde: ``server/routes/saved_filters.py`` (identidade via ``current_user``; NULL
em modo aberto). Som é PESSOAL → sem gate de ``settings.manage`` (qualquer
atendente logado edita a própria preferência E importa sons na biblioteca da
equipe). O padrão da equipe (global) é editado via ``PUT /api/config``
(``sound_settings``, gated por ``settings.notifications``/``settings.manage``).

Import: só ÁUDIO (extensão + content-type + magic bytes conferidos na borda) e no
máximo :data:`MAX_SOUND_BYTES`. O arquivo vai para ``statics/sounds/<gerado>`` —
o nome do upload NUNCA é usado no disco (evita traversal/colisão).
"""

import asyncio
import logging
import re
import uuid
from pathlib import Path

from fastapi import File, Form, Request, UploadFile

from db.repositories import custom_sound_repo, user_sound_pref_repo
from server import sound_catalog
from server.authz import current_user
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

# ── Regras de import ───────────────────────────────────────────────────────────
MAX_SOUND_BYTES = 1024 * 1024          # 1 MB — som de notificação é curto
MAX_SOUND_NAME = 60
# Extensão → assinaturas aceitas no início do arquivo (magic bytes). O casamento é
# por EXTENSÃO + (content-type de áudio OU assinatura), então um .exe renomeado
# para .mp3 não passa e um browser que não manda content-type ainda funciona.
_AUDIO_EXTS = {".mp3", ".ogg", ".oga", ".wav", ".m4a", ".aac", ".webm", ".flac"}
_MIME_BY_EXT = {
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".webm": "audio/webm", ".flac": "audio/flac",
}


def _looks_like_audio(head: bytes) -> bool:
    """Sniff dos formatos aceitos (12 primeiros bytes bastam)."""
    if len(head) < 4:
        return False
    if head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return True                                   # MP3 (com/sem tag ID3)
    if head[:4] == b"OggS":
        return True                                   # OGG / Opus / Vorbis
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return True                                   # WAV
    if head[4:8] == b"ftyp":
        return True                                   # M4A / MP4 audio
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return True                                   # WebM / Matroska
    if head[:4] == b"fLaC":
        return True                                   # FLAC
    if head[:2] == b"\xff\xf1" or head[:2] == b"\xff\xf9":
        return True                                   # AAC (ADTS)
    return False


def _clean_name(raw: str, fallback: str) -> str:
    """Nome amigável do som: sem quebras/controles, colapsado e limitado."""
    name = re.sub(r"[\x00-\x1f\x7f]", " ", str(raw or "")).strip()
    name = re.sub(r"\s+", " ", name)[:MAX_SOUND_NAME]
    return name or fallback[:MAX_SOUND_NAME] or "Som importado"


def _user_id(request: Request) -> int | None:
    user = current_user(request)
    return (user or {}).get("id")


def register_routes(app, deps):
    settings = deps.settings

    def _global_default() -> dict:
        """Padrão GLOBAL normalizado (fail-open ao seed se a config estiver corrompida)."""
        raw = settings.get("sound_settings", None)
        norm = sound_catalog.normalize(raw, sparse=False)
        # normalize() de um não-dict devolve {} → cai no seed para o cliente sempre
        # ter um piso utilizável.
        if not norm.get("events"):
            from config.settings import SOUND_SETTINGS_SEED
            return sound_catalog.normalize(SOUND_SETTINGS_SEED, sparse=False)
        return norm

    sounds_dir = Path(settings.data_dir) / "statics" / "sounds"

    def _library() -> list[dict]:
        """Sons importados no formato do catálogo (id ``custom:<n>`` + url)."""
        return [sound_catalog.custom_entry(r["id"], r["name"], r["filename"])
                for r in custom_sound_repo.list_all()]

    async def _catalog_with_library() -> dict:
        cat = sound_catalog.catalog()
        try:
            cat["sounds"] = cat["sounds"] + await asyncio.to_thread(_library)
        except Exception as e:  # noqa: BLE001 — biblioteca indisponível não quebra a tela
            logger.debug("sound library indisponível: %s", e)
        return cat

    @app.get("/api/sounds/catalog")
    async def sounds_catalog():
        """Catálogo de eventos + sons (sintetizados do código + importados)."""
        return _ok(await _catalog_with_library())

    # ── Biblioteca de sons importados ─────────────────────────────────────────
    @app.get("/api/sounds/library")
    async def list_custom_sounds():
        """Sons importados pela equipe (qualquer atendente logado consulta)."""
        rows = await asyncio.to_thread(custom_sound_repo.list_all)
        return _ok([sound_catalog.custom_entry(r["id"], r["name"], r["filename"],
                                               extra=r) for r in rows])

    @app.post("/api/sounds/library")
    async def upload_custom_sound(request: Request, file: UploadFile = File(...),
                                  name: str = Form("")):
        """Importa um som para a biblioteca da equipe.

        Só ÁUDIO (extensão conhecida + content-type de áudio OU magic bytes) e no
        máximo 1 MB — a leitura é limitada, um arquivo maior é recusado sem ser
        gravado. O nome exibido é o do formulário (ou o do arquivo, sem extensão).
        """
        ext = Path(file.filename or "").suffix.lower()
        if ext not in _AUDIO_EXTS:
            return _err("Formato não suportado. Envie um áudio "
                        "(mp3, ogg, wav, m4a, aac, webm ou flac).")
        # Lê 1 byte além do teto: se sobrar algo, o arquivo passou do limite.
        data = await file.read(MAX_SOUND_BYTES + 1)
        if len(data) > MAX_SOUND_BYTES:
            return _err(f"Arquivo grande demais (máx. {MAX_SOUND_BYTES // 1024} KB).")
        if not data:
            return _err("Arquivo vazio.")
        content_type = (file.content_type or "").lower()
        if not (content_type.startswith("audio/") or _looks_like_audio(data[:12])):
            return _err("O arquivo não parece ser um áudio válido.")
        sounds_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"          # nunca o nome do upload
        (sounds_dir / filename).write_bytes(data)
        row = await asyncio.to_thread(
            custom_sound_repo.create,
            name=_clean_name(name, Path(file.filename or "").stem),
            filename=filename,
            mime=content_type if content_type.startswith("audio/") else _MIME_BY_EXT.get(ext, ""),
            size_bytes=len(data),
            created_by=_user_id(request),
        )
        return _ok(sound_catalog.custom_entry(row["id"], row["name"], row["filename"],
                                              extra=row))

    @app.put("/api/sounds/library/{sound_id}")
    async def rename_custom_sound(sound_id: int, body: dict):
        """Renomeia um som importado (o rótulo que aparece no seletor)."""
        current = await asyncio.to_thread(custom_sound_repo.get, sound_id)
        if not current:
            return _err("Som não encontrado.", status=404)
        name = _clean_name((body or {}).get("name"), current["name"])
        row = await asyncio.to_thread(custom_sound_repo.rename, sound_id, name)
        return _ok(sound_catalog.custom_entry(row["id"], row["name"], row["filename"],
                                              extra=row))

    @app.delete("/api/sounds/library/{sound_id}")
    async def delete_custom_sound(sound_id: int):
        """Remove o som da biblioteca e apaga o arquivo.

        Preferências que apontavam para ele não são reescritas: o motor cai no som
        padrão do evento quando o id não existe mais (fail-open)."""
        row = await asyncio.to_thread(custom_sound_repo.delete, sound_id)
        if not row:
            return _err("Som não encontrado.", status=404)
        try:
            (sounds_dir / row["filename"]).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("falha ao apagar som %s: %s", row["filename"], e)
        return _ok({"deleted": sound_id})

    @app.get("/api/me/sound-prefs")
    async def get_sound_prefs(request: Request):
        """Override do usuário + padrão global + catálogo (tudo para resolver no cliente)."""
        uid = _user_id(request)
        prefs = await asyncio.to_thread(user_sound_pref_repo.get, uid)
        return _ok({
            "prefs": sound_catalog.normalize(prefs or {}, sparse=True),
            "global_default": _global_default(),
            "catalog": sound_catalog.catalog(),
        })

    @app.put("/api/me/sound-prefs")
    async def put_sound_prefs(request: Request):
        """Grava o override esparso do usuário (normalizado, fail-open)."""
        try:
            body = await request.json()
        except Exception:
            return _err("Corpo inválido.")
        prefs = body.get("prefs") if isinstance(body, dict) and "prefs" in body else body
        clean = sound_catalog.normalize(prefs, sparse=True)
        uid = _user_id(request)
        saved = await asyncio.to_thread(user_sound_pref_repo.upsert, uid, clean)
        return _ok({"prefs": saved, "global_default": _global_default()})
