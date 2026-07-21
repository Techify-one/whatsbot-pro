"""Repository for custom_sounds — biblioteca de sons importados pela equipe.

Uma linha por som importado na aba "Sons". O ÁUDIO fica em ``statics/sounds/``
(``filename``); aqui ficam o nome amigável (escolhido no import, é o rótulo do
seletor de som), o mime/tamanho validados na borda e quem subiu.

O id vira o sound-id ``custom:<id>`` nas preferências (``config.sound_settings``
e ``user_sound_prefs``). Uma preferência que aponte para um som EXCLUÍDO não é
erro: o motor de som cai no som padrão do evento (fail-open).
"""

from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import custom_sounds as cs


def list_all() -> list[dict]:
    """Todos os sons importados, mais recentes primeiro."""
    with get_engine().connect() as conn:
        rows = conn.execute(select(cs).order_by(cs.c.id.desc())).mappings().all()
    return [dict(r) for r in rows]


def get(sound_id: int) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(select(cs).where(cs.c.id == sound_id)).mappings().first()
    return dict(row) if row else None


def create(*, name: str, filename: str, mime: str = "", size_bytes: int = 0,
           created_by: int | None = None) -> dict:
    with get_engine().begin() as conn:
        new_id = conn.execute(cs.insert().values(
            name=name, filename=filename, mime=mime, size_bytes=size_bytes,
            created_by=created_by, created_at=time.time(),
        ).returning(cs.c.id)).scalar_one()
    return get(new_id) or {}


def rename(sound_id: int, name: str) -> dict | None:
    with get_engine().begin() as conn:
        conn.execute(sa_update(cs).where(cs.c.id == sound_id).values(name=name))
    return get(sound_id)


def delete(sound_id: int) -> dict | None:
    """Remove a linha e devolve o registro apagado (o caller apaga o arquivo)."""
    row = get(sound_id)
    if not row:
        return None
    with get_engine().begin() as conn:
        conn.execute(sa_delete(cs).where(cs.c.id == sound_id))
    return row
