"""Repository for per-user @mentions in private notes (colaboração estilo Chatwoot).

Uma nota privada pode mencionar N usuários → N linhas em ``mentions``. Diferente do
unread por-contato (``unread_repo``), este é POR-USUÁRIO: alimenta o badge "@" e a aba
"Menções" da sidebar (as conversas em que EU fui mencionado). ``read_at`` NULL = não-lida;
abrir a conversa marca como lida.
"""

from __future__ import annotations

import time

from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from db.engine import get_engine
from db.tables import mentions


def add_many(*, message_id: int, conversation_id: int, contact_id: int,
             mentioned_user_ids: list[int], actor_user_id: int | None,
             actor_name: str | None) -> int:
    """Insert one mention row per user (deduped). Returns the number inserted.

    ``actor_user_id`` (o autor) é excluído — ninguém se menciona. IDs repetidos
    (ex.: usuário citado E membro da caixa mencionada) colapsam num só."""
    targets = [uid for uid in dict.fromkeys(mentioned_user_ids)
               if uid and uid != actor_user_id]
    if not targets:
        return 0
    now = time.time()
    rows = [{
        "message_id": message_id,
        "conversation_id": conversation_id,
        "contact_id": contact_id,
        "mentioned_user_id": uid,
        "actor_user_id": actor_user_id,
        "actor_name": actor_name,
        "created_at": now,
        "read_at": None,
    } for uid in targets]
    with get_engine().begin() as conn:
        conn.execute(mentions.insert(), rows)
    return len(rows)


def unread_count(user_id: int) -> int:
    """Quantas menções não-lidas o usuário tem (badge global/aba)."""
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count())
            .select_from(mentions)
            .where(mentions.c.mentioned_user_id == user_id)
            .where(mentions.c.read_at.is_(None))
        ).scalar() or 0


def unread_conversation_ids(user_id: int) -> list[int]:
    """Conversas com menção não-lida para o usuário (filtro da aba Menções)."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(mentions.c.conversation_id.distinct())
            .where(mentions.c.mentioned_user_id == user_id)
            .where(mentions.c.read_at.is_(None))
        ).all()
    return [r[0] for r in rows]


def mark_read(user_id: int, conversation_id: int) -> int:
    """Marca como lidas as menções do usuário numa conversa (ao abrir). Retorna quantas."""
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_update(mentions)
            .where(mentions.c.mentioned_user_id == user_id)
            .where(mentions.c.conversation_id == conversation_id)
            .where(mentions.c.read_at.is_(None))
            .values(read_at=time.time())
        )
    return result.rowcount or 0
