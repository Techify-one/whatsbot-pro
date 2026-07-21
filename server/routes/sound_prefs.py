"""Preferências de som por usuário + catálogo (plano 63).

- ``GET  /api/me/sound-prefs``  → override esparso do usuário + padrão GLOBAL da
  equipe (para o cliente resolver o efetivo sem uma 2ª chamada) + catálogo.
- ``PUT  /api/me/sound-prefs``  → grava o override esparso (normalizado, fail-open).
- ``GET  /api/sounds/catalog``  → metadados estáticos (eventos, sons, classe).

Molde: ``server/routes/saved_filters.py`` (identidade via ``current_user``; NULL
em modo aberto). Som é PESSOAL → sem gate de ``settings.manage`` (qualquer
atendente logado edita a própria preferência). O padrão da equipe (global) é
editado via ``PUT /api/config`` (``sound_settings``, esse sim gated).
"""

import asyncio
import logging

from fastapi import Request

from db.repositories import user_sound_pref_repo
from server import sound_catalog
from server.authz import current_user
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


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

    @app.get("/api/sounds/catalog")
    async def sounds_catalog():
        """Catálogo estático de eventos e sons (público — só metadados)."""
        return _ok(sound_catalog.catalog())

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
