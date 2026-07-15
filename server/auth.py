"""Authentication utilities for WhatsBot web panel.

Login is exclusively **RBAC users**: Argon2id PHC hashes (per-user, salt embedded)
+ opaque server-side session tokens (``user_sessions`` table). The legacy
single-password scheme (``web_password_hash``, SHA-256 + deterministic token) was
retired in plano 48 — the API/WS gate now closes on ``has_users`` (see
``server.app`` / ``server.routes.websocket``), not on a panel password.
"""

import secrets

try:
    from passlib.context import CryptContext
    _pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
except Exception:  # passlib/argon2 absent — RBAC user login disabled
    _pwd_context = None


def hash_password_argon2(password: str) -> str:
    """Return an Argon2id PHC string (salt embedded). Raises if passlib is absent."""
    if _pwd_context is None:
        raise RuntimeError("passlib[argon2] não instalado — login de usuário indisponível")
    return _pwd_context.hash(password)


def verify_password_argon2(password: str, phc_hash: str) -> bool:
    """Verify a password against an Argon2id PHC string. Never raises."""
    if _pwd_context is None or not phc_hash:
        return False
    try:
        return _pwd_context.verify(password, phc_hash)
    except Exception:
        return False


def generate_session_token() -> str:
    """Opaque, server-side session token id (stored in user_sessions)."""
    return secrets.token_urlsafe(32)


def rbac_enforced(settings) -> bool:
    """Whether a valid USER session is hard-required regardless of ``has_users``.

    An optional override on top of the self-healing ``has_users`` gate (plano 48):
    the API/WS close as soon as ≥1 user exists, so this is normally redundant. Kept
    as a rigid opt-in (e.g. to force enforcement on a zero-user install).
    """
    return bool(settings.get("rbac_enforce", False))


def resolve_request_token(token: str):
    """Resolve a Bearer token to a user identity.

    Returns ``("user", user_dict)`` for a valid, active user session, or
    ``(None, None)`` otherwise. Repos imported lazily to avoid an import cycle.
    """
    if not token:
        return None, None
    try:
        from db.repositories import session_repo, user_repo
        sess = session_repo.get_valid(token)
        if sess:
            user = user_repo.get(sess["user_id"])
            if user and user.get("is_active"):
                return "user", user
    except Exception:
        pass
    return None, None
