"""2ª engine SQLAlchemy — conexão READ-ONLY ao banco do Nexus (RBNexusDB).

Adaptado de ``storages/plugins/vendas_ia/nexus_db.py`` (arquivo COPIADO, não
importado — plugins nunca dependem uns dos outros). Medido na F0 do plano 94:
as tabelas do Trackify (``contacts``/``events``/``contact_field_values``/…) e as
do ``vendas_ia`` (``produtos_*``) vivem no MESMO banco, então o DSN é o mesmo
valor — mas cada plugin guarda o seu, por autocontenção.

ISOLADA do ``db.engine.get_engine()`` do WhatsBot: o CDP vive noutro servidor.
Todas as queries são leitura (``connect()``, sem ``begin()``).

Defensivo por contrato: sem DSN configurado (ou com DSN inválido),
``get_engine`` devolve ``None`` e os callers viram no-op logado — nunca derruba
o boot do WhatsBot. O DSN é secret (settings), com ``sslmode=require`` forçado;
nunca é logado nem colocado em URL de log.

DIVERGE do precedente do ``vendas_ia`` em duas coisas, de propósito (plano 94 §7):
transação **read-only de verdade** no servidor e ``statement_timeout`` curto —
este plugin lê um banco de PRODUÇÃO compartilhado a cada abertura de modal, e
uma consulta ruim não pode prender conexão do Nexus.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from . import _config

logger = logging.getLogger("plugins.trackify.db")

# Singleton chaveado pelo DSN: editar o DSN nas settings reconstrói a engine sem
# precisar de restart (o valor efetivo é relido a cada get_engine).
_lock = threading.Lock()
_engine: Engine | None = None
_engine_dsn: str | None = None

# Cache leve por chave (identidade/jornada de um contato).
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 60.0  # fallback quando o setting está ausente/inválido


def _ttl() -> float:
    """TTL efetivo do cache (setting ``cache_ttl_seconds``, fallback 60s).

    Coerção defensiva: não numérico/≤0 cai no default (fail-safe)."""
    try:
        v = float(_config.setting("cache_ttl_seconds", _CACHE_TTL))
        return v if v > 0 else _CACHE_TTL
    except Exception:  # noqa: BLE001
        return _CACHE_TTL


def cached(key: str, fn):
    """Memoização por TTL. Só para leitura idempotente."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < _ttl():
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    """Limpa o cache local (chamável após reconfigurar o Nexus)."""
    with _cache_lock:
        _cache.clear()


def _normalize_dsn(dsn: str) -> str:
    """Força o driver psycopg (v3) — o WhatsBot só instala ``psycopg[binary]``.

    Um DSN colado como ``postgresql://`` (bare) faz o SQLAlchemy escolher o driver
    default ``psycopg2``, que NÃO está instalado (só ``psycopg`` v3) → ``create_engine``
    levanta ``ModuleNotFoundError`` e a engine vira ``None`` (todo read some em
    silêncio). Reescrevemos o scheme para ``postgresql+psycopg://`` deixando
    qualquer ``+driver`` explícito intacto.
    """
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://"):]
    return dsn


def _statement_timeout_ms() -> int:
    try:
        v = int(_config.setting("statement_timeout_ms", 5000))
        return v if v > 0 else 5000
    except Exception:  # noqa: BLE001
        return 5000


def get_engine() -> Engine | None:
    """Engine read-only para o Nexus, ou ``None`` se o DSN não está configurado.

    Reconstrói se o DSN mudou. Falha de criação vira ``None`` (no-op logado).
    """
    global _engine, _engine_dsn
    dsn = _config.nexus_dsn()
    if not dsn:
        return None
    dsn = _normalize_dsn(dsn)
    with _lock:
        if _engine is not None and _engine_dsn == dsn:
            return _engine
        # DSN novo/alterado — descarta a engine antiga.
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            _engine = None
        try:
            _engine = create_engine(
                dsn,
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_size=2,
                max_overflow=3,
                connect_args={
                    "sslmode": "require",
                    # psycopg3 prepara statements por padrão; atrás de pooler
                    # (pgbouncer) isso quebra. Mesmo valor do vendas_ia.
                    "prepare_threshold": None,
                    # A garantia de verdade: o SERVIDOR recusa escrita e mata
                    # consulta lenta. Não depende de disciplina do call site.
                    "options": (
                        f"-c statement_timeout={_statement_timeout_ms()} "
                        "-c default_transaction_read_only=on"
                    ),
                },
            )
            _engine_dsn = dsn
        except Exception:  # noqa: BLE001 — nunca derruba o boot por causa do Nexus
            logger.warning("trackify: falha ao criar engine do Nexus (DSN inválido?)",
                           exc_info=True)
            _engine = None
            _engine_dsn = None
        return _engine


def run_read(sql: str, params: dict | None = None) -> list[dict]:
    """Executa um SELECT read-only no Nexus e devolve ``list[dict]``.

    Bind params NOMEADOS (``:q``) — nunca ``%s`` (convenção do repo WhatsBot).
    Sem engine ⇒ devolve ``[]`` (no-op). Bloqueante: chamar de rota async via
    ``asyncio.to_thread``.
    """
    engine = get_engine()
    if engine is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params or {}).mappings().all()
    return [dict(r) for r in rows]


def is_configured() -> bool:
    """True quando há um DSN do Nexus configurado (não valida a conexão)."""
    return bool(_config.nexus_dsn())


def ping() -> tuple[bool, str]:
    """Teste de conexão read-only. Retorna ``(ok, mensagem)`` — nunca levanta."""
    if not is_configured():
        return False, "DSN do Nexus não configurado."
    engine = get_engine()
    if engine is None:
        return False, "Não foi possível criar a engine (DSN inválido?)."
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar_one()
        return True, "Conexão read-only com o Nexus OK."
    except Exception as e:  # noqa: BLE001
        # Mensagem enxuta (sem DSN/segredo).
        return False, f"Falha na conexão: {type(e).__name__}"


# Tabelas/colunas de que a tela depende. Um schema que mudou tem que virar
# mensagem acionável no /health, não um 500 na cara do vendedor.
_REQUIRED = [
    ("contacts", ("id", "status", "total_spent", "first_seen_at", "converted_at", "deleted_at")),
    ("contact_field_values", ("contact_id", "custom_field_id", "value")),
    ("custom_fields", ("id", "slug", "is_identifier", "identifier_priority", "deleted_at")),
    # Lidos pela sincronização de campos: o changelog é a ÚNICA fonte que
    # registra limpeza e autoria, e ``field_types`` traz o regex do alvo.
    ("contact_changelog", ("id", "contact_id", "custom_field_id", "new_value",
                           "source", "user_id", "created_at")),
    ("field_types", ("id", "slug", "regex_pattern")),
    ("events", ("id", "contact_id", "channel_id", "event_type", "title", "value",
                "occurred_at", "deleted_at")),
    ("event_field_values", ("event_id", "event_custom_field_id", "value")),
    ("event_custom_fields", ("id", "slug")),
    ("channels", ("id", "slug")),
    ("tags", ("id", "name")),
    ("contact_tags", ("contact_id", "tag_id")),
]


def schema_check() -> tuple[bool, list[str]]:
    """Confere as tabelas/colunas usadas. Retorna ``(ok, faltantes)``.

    Nunca levanta: sem engine devolve ``(False, ["<sem conexão>"])``.
    """
    if get_engine() is None:
        return False, ["<sem conexão com o Nexus>"]
    try:
        rows = run_read(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY(:tables)",
            {"tables": [t for t, _ in _REQUIRED]},
        )
    except Exception as e:  # noqa: BLE001
        return False, [f"<falha ao ler o schema: {type(e).__name__}>"]
    have: dict[str, set] = {}
    for r in rows:
        have.setdefault(r["table_name"], set()).add(r["column_name"])
    missing: list[str] = []
    for table, cols in _REQUIRED:
        if table not in have:
            missing.append(table)
            continue
        for c in cols:
            if c not in have[table]:
                missing.append(f"{table}.{c}")
    return (not missing), missing
