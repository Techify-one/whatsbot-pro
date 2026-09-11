"""Geração e resolução de chaves de API (plano "Sistema de API com chave por usuário").

Irmão de :mod:`server.auth`, e pelo mesmo motivo: lógica PURA, sem FastAPI, para
poder ser testada sem servidor.

**O insight que orienta o desenho**: a chave é apenas um *crachá* novo que resolve
para o MESMO ``request.state.user`` que uma sessão resolve. Feito isso no
middleware, RBAC, auditoria (ator), escopo por inbox e o gating de rotas de plugin
funcionam sem alteração — a chave "vira o usuário".

Formato do segredo::

    wsk_live_<prefix>.<secret>
              └16 hex┘ └43 ch┘

O ``prefix`` é PÚBLICO (indexado, é por ele que a linha é encontrada) e o
``secret`` só existe em Argon2 no banco. O cabeçalho é ``X-Api-Key``, separado do
``Authorization`` DE PROPÓSITO: o middleware nunca confunde crachá de sessão com
crachá de chave.

⚠️ O separador entre prefixo e segredo é ``.``, e o prefixo é **hexadecimal**.
Não é estética: ``secrets.token_urlsafe`` usa o alfabeto base64url, que INCLUI
``-`` e ``_`` — com ``_`` separando os campos, um prefixo ou segredo sorteado com
um ``_`` produzia mais de quatro pedaços e a chave era recusada como malformada,
de forma aleatória (~1 em 3 chaves). ``.`` não pertence ao alfabeto, então o
parsing é total.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time

logger = logging.getLogger(__name__)

KEY_HEADER = "x-api-key"
KEY_PREFIX_LABEL = "wsk_live"
_SEPARATOR = "."        # fora do alfabeto base64url — ver o aviso no topo
_PREFIX_BYTES = 8       # → 16 chars hex
_SECRET_BYTES = 32      # → 43 chars urlsafe

# Validade padrão de uma chave nova (guardrail §4.4 do plano: ``expires_at``
# preenchido por padrão em vez de nulo).
DEFAULT_TTL_SECONDS = 365 * 24 * 3600

# Verificação Argon2 é cara de propósito (~50-100ms). Uma integração faz muitas
# chamadas por minuto com a MESMA chave, então guardamos só o resultado do
# COMPARE (hash → ok) por pouco tempo. A AUTORIZAÇÃO não é cacheada: a linha é
# relida do banco a cada request, então revogar/expirar vale na hora.
_VERIFY_TTL_SECONDS = 60
_verify_cache: dict[str, float] = {}
_VERIFY_CACHE_MAX = 512


def _pwd_context():
    from server.auth import _pwd_context as ctx
    return ctx


def generate_key() -> tuple[str, str, str]:
    """``(raw, prefix, key_hash)`` para uma chave nova. ``raw`` só existe aqui e na
    resposta da criação — nunca é persistido nem auditado."""
    ctx = _pwd_context()
    if ctx is None:
        raise RuntimeError("passlib[argon2] não instalado — chaves de API indisponíveis")
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    raw = f"{KEY_PREFIX_LABEL}_{prefix}{_SEPARATOR}{secret}"
    return raw, prefix, ctx.hash(secret)


def last4(raw: str) -> str:
    return raw[-4:] if raw else ""


def split_key(raw: str) -> tuple[str, str] | tuple[None, None]:
    """``(prefix, secret)`` de um segredo bem-formado, senão ``(None, None)``."""
    if not raw or not isinstance(raw, str):
        return None, None
    raw = raw.strip()
    tag = f"{KEY_PREFIX_LABEL}_"
    if not raw.startswith(tag):
        return None, None
    body = raw[len(tag):]
    prefix, sep, secret = body.partition(_SEPARATOR)
    if not sep or not prefix or not secret:
        return None, None
    return prefix, secret


def _verify(secret: str, key_hash: str, cache_key: str) -> bool:
    now = time.time()
    hit = _verify_cache.get(cache_key)
    if hit is not None and hit > now:
        return True
    ctx = _pwd_context()
    if ctx is None or not key_hash:
        return False
    try:
        ok = ctx.verify(secret, key_hash)
    except Exception:  # noqa: BLE001 — hash corrompido/legado nunca autentica
        return False
    if ok:
        if len(_verify_cache) >= _VERIFY_CACHE_MAX:
            _verify_cache.clear()
        _verify_cache[cache_key] = now + _VERIFY_TTL_SECONDS
    return ok


def is_usable(row: dict, *, now: float | None = None) -> bool:
    """A chave está viva? (não revogada e não expirada)."""
    now = time.time() if now is None else now
    if row.get("revoked_at"):
        return False
    exp = row.get("expires_at")
    return not (exp and exp <= now)


def resolve_api_key(raw: str):
    """``(user, key_row)`` para uma chave válida, senão ``(None, None)``.

    Bloqueante (leituras no banco) — o middleware chama via ``asyncio.to_thread``.
    Nunca levanta: qualquer tropeço vira "não autenticado". Exige ``is_active`` no
    usuário dono, mesmo padrão de :func:`server.auth.resolve_request_token`.
    """
    prefix, secret = split_key(raw)
    if not prefix:
        return None, None
    try:
        from db.repositories import api_key_repo, user_repo
        row = api_key_repo.get_by_prefix(prefix)
        if not row or not is_usable(row):
            return None, None
        cache_key = hashlib.sha256(
            f"{row['id']}:{raw}".encode("utf-8")).hexdigest()
        if not _verify(secret, row.get("key_hash") or "", cache_key):
            return None, None
        user = user_repo.get(row["user_id"])
        if not user or not user.get("is_active"):
            return None, None
        api_key_repo.touch_last_used(row["id"])
        return user, row
    except Exception:  # noqa: BLE001 — uma falha de resolução é "não autenticado"
        logger.debug("resolve_api_key falhou", exc_info=True)
        return None, None


def public_view(row: dict) -> dict:
    """Forma que pode sair na API/UI — NUNCA inclui hash nem segredo."""
    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "label": row.get("label") or "",
        "prefix": row.get("prefix"),
        "masked": f"{KEY_PREFIX_LABEL}_{row.get('prefix')}.…{row.get('last4') or ''}",
        "created_at": row.get("created_at"),
        "last_used_at": row.get("last_used_at"),
        "expires_at": row.get("expires_at"),
        "revoked_at": row.get("revoked_at"),
        "created_by": row.get("created_by"),
        "active": is_usable(row),
    }
