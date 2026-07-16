"""Verificação HMAC dos callbacks do executor (plano 51 · 02 F2).

Dependency FastAPI para as rotas ``/public/_internal/*`` (auth-exempt via a
convenção ``PLUGIN_PUBLIC_PATH_RE`` do core — a autenticação REAL é esta).

- Assinatura validada sobre os BYTES CRUS (``await request.body()``) — nunca um
  dict re-serializado (ordenação de chaves Python≠Node quebraria o HMAC).
- Janela de 60s; nonce LRU in-memory (cap 5000, TTL 5min) contra replay.
- ``X-WB-On-Behalf-Of`` OBRIGATÓRIO: resolve o usuário RBAC e o carimba em
  ``request.state.user`` para as mutações reaplicarem ``authz.acheck`` — o MESMO
  primitivo do core (D02-c). Instalação aberta (sem usuários) ⇒ default-allow,
  idêntico ao core.
- Gate dormente (D02-e): backend != "external" OU secret não configurado ⇒ 404
  (finge que a rota não existe, molde ``hmac.guard.ts:58-61``).
"""

from __future__ import annotations

import hmac as hmac_mod
import logging
import time

from fastapi import HTTPException, Request

from db.repositories import config_repo

from . import ai_client

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60
NONCE_CAP = 5000
NONCE_TTL = 5 * 60.0

# Nonce LRU module-level (sobrevive entre requests do mesmo processo).
_nonces: dict[str, float] = {}


def _nonce_seen(request_id: str) -> bool:
    now = time.time()
    # Evict expirados (e o excedente mais antigo quando estoura o cap).
    expired = [k for k, t in _nonces.items() if now - t > NONCE_TTL]
    for k in expired:
        _nonces.pop(k, None)
    if request_id in _nonces:
        return True
    if len(_nonces) >= NONCE_CAP:
        oldest = min(_nonces, key=_nonces.get)
        _nonces.pop(oldest, None)
    _nonces[request_id] = now
    return False


def _feature_active() -> bool:
    backend = str(config_repo.get("plugin.melhorias.generator_backend", "external")
                  or "external")
    return backend == "external" and ai_client.is_configured()


async def hmac_guard(request: Request) -> None:
    # Gate dormente: nada agêntico existe até o operador configurar url+secret
    # e trocar o backend (D02-e). 404, não 403 — finge inexistência.
    if not _feature_active():
        raise HTTPException(status_code=404, detail="Not Found")

    ts = request.headers.get("X-WB-Timestamp") or ""
    sig = request.headers.get("X-WB-Signature") or ""
    rid = request.headers.get("X-WB-Request-Id") or ""
    obo = request.headers.get("X-WB-On-Behalf-Of")

    if not ts or not sig:
        raise HTTPException(status_code=403, detail="Assinatura ausente.")
    try:
        ts_num = int(ts)
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Timestamp inválido.")
    if abs(int(time.time()) - ts_num) > WINDOW_SECONDS:
        raise HTTPException(status_code=403, detail="Timestamp fora da janela.")
    if not rid or _nonce_seen(rid):
        raise HTTPException(status_code=403, detail="requestId reusado ou ausente.")

    # ⚠️ bytes crus — a MESMA string que o executor assinou.
    raw = await request.body()
    path = request.url.path
    expected = ai_client.sign(ai_client.shared_secret(), request.method, path,
                              ts, rid, raw.decode("utf-8", errors="replace"))
    if len(expected) != len(sig) or not hmac_mod.compare_digest(expected, sig):
        raise HTTPException(status_code=403, detail="Assinatura inválida.")

    # On-behalf-of obrigatório (D02-c). Vazio/ausente ⇒ 403. Um id não-numérico
    # ou inexistente vira user=None SÓ quando a instalação é aberta (sem
    # usuários) — senão 403 (não deixar a IA agir como "ninguém" com RBAC ativo).
    if obo is None or str(obo).strip() == "":
        raise HTTPException(status_code=403, detail="On-Behalf-Of ausente.")
    user = None
    try:
        from db.repositories import user_repo
        user = user_repo.get(int(str(obo).strip()))
    except (TypeError, ValueError):
        user = None
    if user is None:
        try:
            from db.repositories import user_repo
            has_users = user_repo.has_any()
        except Exception:
            has_users = False
        if has_users:
            raise HTTPException(status_code=403, detail="Usuário on-behalf-of inválido.")
    request.state.user = user
    request.state.hmac = {"request_id": rid, "on_behalf_of": obo}
