"""Authentication endpoints (login, check)."""

import asyncio
import logging
import time
from collections import deque

from fastapi import Request

from server.auth import (
    hash_password_argon2, verify_password_argon2, generate_session_token,
    resolve_request_token,
)
from db.repositories import user_repo, session_repo, rbac_repo
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)

_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 10


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def register_routes(app, deps):
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

        # Login is RBAC-only (plano 48): email + senha → Argon2id verify + opaque
        # session token. The legacy single-password scheme was retired.
        email = (body.get("email") or "").strip().lower()
        password = body.get("password", "")
        if not email or not password:
            return _err("Informe email e senha.", status=400)

        auth_row = await asyncio.to_thread(user_repo.get_auth_row, email)
        ok = bool(auth_row) and bool(auth_row.get("is_active")) and \
            verify_password_argon2(password, auth_row.get("password_hash", ""))
        if not ok:
            attempts.append(now)
            logger.warning("Failed user login (%s) from %s.", email, ip)
            return _err("Email ou senha incorretos.", status=401)
        state.login_attempts.pop(ip, None)
        token = generate_session_token()
        ua = request.headers.get("user-agent", "")
        await asyncio.to_thread(session_repo.create, token, auth_row["id"],
                                user_agent=ua, ip=ip)
        await asyncio.to_thread(user_repo.touch_login, auth_row["id"])
        user = await asyncio.to_thread(user_repo.get, auth_row["id"])
        logger.info("Successful user login (%s) from %s.", email, ip)
        return _ok({"token": token, "user": user})

    def _bearer(request: Request) -> str:
        h = request.headers.get("authorization", "")
        return h[7:] if h.startswith("Bearer ") else ""

    @app.get("/api/auth/check")
    async def check_auth(request: Request):
        # ``has_password`` is retained (fixed False) for frontend compat — the
        # legacy panel password was retired (plano 48). Auth hinges on has_users.
        has_users = await asyncio.to_thread(user_repo.has_any)

        if not has_users:
            return _ok({"authenticated": True, "has_password": False, "has_users": False})

        token = _bearer(request)
        kind, user = await asyncio.to_thread(resolve_request_token, token)
        if kind is not None:
            return _ok({"authenticated": True, "has_password": False,
                        "has_users": True, "user": user})
        # Carry has_users on the 401 too so the panel can drive the login/bootstrap
        # flow (a request only reaches here once ≥1 user exists).
        return _err("Não autenticado.", status=401,
                    data={"has_password": False, "has_users": has_users})

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        token = _bearer(request)
        if token:
            await asyncio.to_thread(session_repo.delete, token)
        return _ok({"loggedOut": True})

    @app.get("/api/auth/me")
    async def me(request: Request):
        token = _bearer(request)
        kind, user = await asyncio.to_thread(resolve_request_token, token)
        if kind == "user":
            perms = await asyncio.to_thread(rbac_repo.user_permissions, user["id"])
            user = dict(user)
            user["permissions"] = sorted(perms)
            return _ok({"user": user})
        return _err("Não autenticado.", status=401)

    @app.post("/api/auth/bootstrap")
    async def bootstrap(body: dict):
        """Create the first admin user. Only works while NO users exist (one-time)."""
        if (await asyncio.to_thread(user_repo.count)) > 0:
            return _err("Já existe usuário; bootstrap indisponível.", status=409)
        email = (body.get("email") or "").strip().lower()
        name = (body.get("name") or "").strip() or email
        password = body.get("password") or ""
        if not email or "@" not in email:
            return _err("Email inválido.", status=400)
        if len(password) < 8:
            return _err("A senha deve ter ao menos 8 caracteres.", status=400)
        phc = hash_password_argon2(password)
        user = await asyncio.to_thread(
            user_repo.create, email=email, name=name, password_hash=phc,
            role_keys=["admin"])
        logger.info("Bootstrap: first admin user created (%s).", email)
        return _ok({"user": user})
