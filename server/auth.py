"""Authentication utilities for WhatsBot web panel.

Two coexisting schemes (plano 03, additive rollout):
- Legacy single-password: SHA-256 hash + deterministic token (``web_password_hash``).
  Kept working so nothing breaks; this is the default until RBAC is enforced.
- RBAC users: Argon2id PHC hashes (per-user, salt embedded) + opaque server-side
  session tokens (``user_sessions`` table). Used when a user logs in.
"""

import hashlib
import hmac
import secrets

try:
    from passlib.context import CryptContext
    _pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
except Exception:  # passlib/argon2 absent — RBAC user login disabled, legacy still works
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


def generate_salt() -> str:
    """Generate a random hex salt."""
    return secrets.token_hex(32)


def hash_password(password: str, salt: str) -> str:
    """Hash a password with the given salt using SHA-256."""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def generate_token(password_hash: str, salt: str) -> str:
    """Generate a deterministic session token from the password hash.

    Changes automatically when the password changes.
    """
    return hashlib.sha256(
        (password_hash + salt + "session").encode("utf-8")
    ).hexdigest()


def verify_token(token: str, settings) -> bool:
    """Verify a session token against the stored password hash."""
    password_hash = settings.get("web_password_hash", "")
    salt = settings.get("web_password_salt", "")
    if not password_hash or not salt:
        return False
    expected = generate_token(password_hash, salt)
    return hmac.compare_digest(token, expected)


def auth_required(settings) -> bool:
    """Check if authentication is enabled (password is set)."""
    return bool(settings.get("web_password_hash", ""))


def rbac_enforced(settings) -> bool:
    """Whether a valid USER session is required (opt-in; default off).

    Off by default so the single-password / open install keeps working on a
    live server. Flip ``rbac_enforce`` once at least one admin user exists.
    """
    return bool(settings.get("rbac_enforce", False))


def resolve_request_token(token: str, settings):
    """Resolve a Bearer token to an identity (plano 03 Fase 3, additive).

    Returns ``("user", user_dict)`` for a valid user session, ``("legacy", None)``
    for the legacy single-password token, or ``(None, None)`` if neither matches.
    Repos imported lazily to avoid an import cycle at module load.
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
    if verify_token(token, settings):
        return "legacy", None
    return None, None
