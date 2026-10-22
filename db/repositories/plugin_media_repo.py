"""Arquivos que pertencem a um PLUGIN (plano 179, convenção de host).

    Uma coluna chamada ``media_path`` numa tabela ``plugin_<id>_*`` declara que aquele
    arquivo é DO PLUGIN. A tela Arquivos nunca o apaga do disco — só o tira das conversas.

Por que convenção e não filtro/evento: ``arquivos_suporte``, ``retornos`` e
``whatsapp_cloud`` já guardam o caminho assim (``statics/outbox/<nome>``), então os três
ficam protegidos sem zip novo; e um plugin quebrado não pode abrir a guarda (um filtro
do bus isola a exceção deixando o valor passar = fail-open, o oposto do necessário).

⚠️ Fail-closed: quem chama trata ``PluginMediaError`` como "tudo está em uso". Na dúvida,
guarda o arquivo.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from sqlalchemy import column, select, table, text

from db.engine import get_engine
from db.tables import plugins as plugins_table

logger = logging.getLogger(__name__)

_TABLE_RE = re.compile(r"^plugin_[a-z0-9_]+$")
_COLUMN = "media_path"
_CACHE_TTL = 60.0
_BATCH = 500

_lock = threading.Lock()
_cache: tuple[float, list[str]] | None = None


class PluginMediaError(RuntimeError):
    """A descoberta ou a consulta falhou — o chamador deve guardar todo arquivo."""


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def media_tables() -> list[str]:
    """Tabelas ``plugin_*`` com coluna ``media_path`` (cache de 60 s)."""
    global _cache
    now = time.monotonic()
    with _lock:
        if _cache and now - _cache[0] < _CACHE_TTL:
            return _cache[1]
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name LIKE 'plugin\\_%' ESCAPE '\\' "
                "AND column_name = :col ORDER BY table_name"), {"col": _COLUMN}).all()
    except Exception as exc:
        raise PluginMediaError(f"descoberta de tabelas de plugin falhou: {exc}") from exc
    # Nome validado ANTES de virar identificador de SQL (monta via ``table()``, nunca f-string).
    names = [r[0] for r in rows if _TABLE_RE.match(r[0] or "")]
    with _lock:
        _cache = (time.monotonic(), names)
    return names


def plugin_ids() -> dict[str, str]:
    """``plugin_id`` → nome exibível, vindo da tabela ``plugins`` (nome = id se não houver)."""
    try:
        with get_engine().connect() as conn:
            ids = [r[0] for r in conn.execute(select(plugins_table.c.id))]
    except Exception as exc:
        raise PluginMediaError(f"leitura da tabela plugins falhou: {exc}") from exc
    return {i: i for i in ids}


def owner_of_table(table_name: str, known_ids: list[str]) -> str | None:
    """Prefixo MAIS LONGO do nome da tabela que casa com um id de plugin.

    O id pode ter ``_`` (``arquivos_suporte``), então ``plugin_arquivos_suporte_arquivos``
    não se resolve por ``split``. Sem id conhecido, devolve ``None`` (o arquivo continua
    protegido — só o NOME do dono fica desconhecido)."""
    best = None
    for pid in known_ids:
        if table_name == f"plugin_{pid}" or table_name.startswith(f"plugin_{pid}_"):
            if best is None or len(pid) > len(best):
                best = pid
    return best


def owned_paths(paths: list[str]) -> dict[str, str | None]:
    """Mapa ``media_path`` → ``plugin_id`` (ou ``None`` se o dono não se resolve) dos
    caminhos de ``paths`` que algum plugin declara seu.

    Casa a forma com e sem barra inicial. Levanta :class:`PluginMediaError` em qualquer
    falha — o chamador então trata TODO arquivo de ``statics/outbox`` como em uso."""
    wanted = [p for p in dict.fromkeys(paths) if p]
    if not wanted:
        return {}
    tables = media_tables()
    if not tables:
        return {}
    known = list(plugin_ids())
    found: dict[str, str | None] = {}
    try:
        with get_engine().connect() as conn:
            for name in tables:
                owner = owner_of_table(name, known)
                t = table(name, column(_COLUMN))
                col = t.c[_COLUMN]
                for i in range(0, len(wanted), _BATCH):
                    chunk = wanted[i:i + _BATCH]
                    variants = list(chunk) + ["/" + p for p in chunk]
                    for (value,) in conn.execute(
                            select(col).where(col.in_(variants)).distinct()):
                        found[str(value).lstrip("/")] = owner
    except PluginMediaError:
        raise
    except Exception as exc:
        raise PluginMediaError(f"consulta de arquivos de plugin falhou: {exc}") from exc
    return found
