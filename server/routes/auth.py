"""Authentication endpoints (login, check)."""

import logging

from fastapi import Request

from server.auth import auth_required, hash_password, generate_token, verify_token
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    settings = deps.settings

    @app.post("/api/auth/login")
    async def login(body: dict):
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
            logger.warning("Failed login attempt.")
            return _err("Senha incorreta.", status=401)

        token = generate_token(expected_hash, salt)
        logger.info("Successful login.")
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
