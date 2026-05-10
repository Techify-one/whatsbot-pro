"""Authentication endpoints (login, check)."""

import logging
import time
from collections import deque

from fastapi import Request

from server.auth import auth_required, hash_password, generate_token, verify_token
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 5


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def register_routes(app, deps):
    settings = deps.settings
    state = deps.state

    @app.post("/api/auth/login")
    async def login(body: dict, request: Request):
        ip = _client_ip(request)
        now = time.time()
        attempts = state.login_attempts.get(ip)
        if attempts is None:
            attempts = deque(maxlen=_LOGIN_MAX_FAILURES * 2)
            state.login_attempts[ip] = attempts
        # Drop entries older than the window
        while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= _LOGIN_MAX_FAILURES:
            logger.warning("Login rate limit triggered for %s.", ip)
            return _err("Muitas tentativas. Tente novamente em alguns minutos.", status=429)

        password = body.get("password", "")
        if not password:
            return _err("Senha não informada.", status=400)

        if not auth_required(settings):
            return _err("Nenhuma senha configurada.", status=400)

        salt = settings.get("web_password_salt", "")
        expected_hash = settings.get("web_password_hash", "")
        actual_hash = hash_password(password, salt)

        import hmac as _hmac
        if not _hmac.compare_digest(actual_hash, expected_hash):
            attempts.append(now)
            logger.warning("Failed login attempt from %s.", ip)
            return _err("Senha incorreta.", status=401)

        state.login_attempts.pop(ip, None)
        token = generate_token(expected_hash, salt)
        logger.info("Successful login from %s.", ip)
        return _ok({"token": token})

    @app.get("/api/auth/check")
    async def check_auth(request: Request):
        has_password = auth_required(settings)

        if not has_password:
            return _ok({"authenticated": True, "has_password": False})

        auth_header = request.headers.get("authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

        if token and verify_token(token, settings):
            return _ok({"authenticated": True, "has_password": True})

        return _err("Não autenticado.", status=401)
