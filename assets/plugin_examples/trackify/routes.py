"""REST do plugin (montado em ``/api/plugins/trackify``).

Shell fino sobre ``journey``/``identity``. Envelope ``{"ok", "data"|"error"}`` e
gate por ``plugin_permission`` (default-allow em instalação legada/sem RBAC).

⚠️ O contrato é ``?contact_id=<int do WhatsBot>``, **nunca** ``?phone=``:

* telefone em query string vaza para log de proxy — a mesma higiene que o plano
  exige do DSN;
* ``?phone=`` seria spoofável — qualquer atendente com ``view`` consultaria
  quanto QUALQUER número gastou, que é exatamente o vazamento financeiro que o
  RBAC tenta gatear.

Leitura NÃO é auditada (regra do repo: GET/listagem fica fora da trilha).
Tudo que toca o Nexus roda em ``asyncio.to_thread`` — ``run_read`` é bloqueante.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.repositories import contact_repo
from plugins.context import audit, plugin_permission

from . import _config, dispatcher, identity, journey, trackify_db

logger = logging.getLogger("plugins.trackify.routes")

router = APIRouter()

# Sentinela do segredo: o que a tela recebe e pode devolver sem apagar a chave.
_MASK = "***"


def _err(msg: str, status: int = 400):
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


def _contact_scope(contact_id: int) -> dict | None:
    """Telefone/e-mail/tipo do contato do WhatsBot — a única fonte de identidade.

    Resolver no servidor (em vez de aceitar o telefone do cliente) é o que torna
    a rota não-spoofável.
    """
    c = contact_repo.get(contact_id)
    if not c:
        return None
    return {
        "phone": c.get("phone") or "",
        "email": (c.get("email") or "").strip(),
        "name": c.get("name") or "",
        "contact_type": c.get("contact_type") or "whatsapp",
        "is_group": bool(c.get("is_group")),
    }


@router.get("/journey", dependencies=[plugin_permission("view")])
async def get_journey(contact_id: int):
    """Os 3 blocos da jornada do contato da conversa aberta."""
    scope = await asyncio.to_thread(_contact_scope, contact_id)
    if scope is None:
        return _err("Contato não encontrado.", 404)
    if scope["is_group"]:
        # Grupo não é pessoa: não tem jornada comercial nenhuma.
        return {"ok": True, "data": {"found": False, "configured": True,
                                     "is_group": True, "candidates": []}}

    data = await asyncio.to_thread(
        journey.journey_for,
        phone=scope["phone"], email=scope["email"] or None,
        contact_type=scope["contact_type"])
    data["whatsbot"] = {"contact_id": contact_id, "name": scope["name"]}
    return {"ok": True, "data": data}


@router.get("/journey/events", dependencies=[plugin_permission("view")])
async def get_journey_events(trackify_contact_id: str, offset: int = 0,
                             event_type: str | None = None,
                             limit: int | None = None):
    """Página seguinte da linha do tempo de um contato JÁ resolvido.

    Recebe o uuid do Trackify (devolvido por ``/journey``), não o telefone — a
    resolução já foi feita e gateada na chamada anterior.
    """
    if not trackify_db.is_configured():
        return _err("Conexão com o Trackify não configurada.", 503)
    data = await asyncio.to_thread(
        journey.fetch_timeline, trackify_contact_id,
        limit=limit, offset=offset, event_type=event_type)
    return {"ok": True, "data": data}


@router.get("/journey/search", dependencies=[plugin_permission("view")])
async def search_journey(email: str | None = None, cpf: str | None = None):
    """Busca manual quando o telefone não casou (~77% dos casos).

    Mesmo gate da jornada: buscar CPF arbitrário no CDP é mais sensível do que
    abrir a jornada do contato que já está na tela, então não afrouxa aqui.
    """
    if not (email or cpf):
        return _err("Informe e-mail ou CPF.")
    if not trackify_db.is_configured():
        return _err("Conexão com o Trackify não configurada.", 503)

    def _run():
        matches = identity.resolve_by_email(email) if email else []
        if not matches and cpf:
            matches = identity.resolve_by_cpf(cpf)
        if not matches:
            return {"found": False, "candidates": []}
        if len(matches) > 1:
            return {"found": False, "ambiguous": True, "candidates": [
                {**(journey.fetch_identity(m.contact_id) or {"contact_id": m.contact_id}),
                 "matched_by": m.slug} for m in matches]}
        data = journey.build_journey(matches[0].contact_id)
        data["matched_by"] = matches[0].slug
        return data

    return {"ok": True, "data": await asyncio.to_thread(_run)}


@router.get("/journey/by-id", dependencies=[plugin_permission("view")])
async def get_journey_by_id(trackify_contact_id: str):
    """Jornada de um cadastro escolhido no seletor de ambiguidade."""
    if not trackify_db.is_configured():
        return _err("Conexão com o Trackify não configurada.", 503)
    data = await asyncio.to_thread(journey.build_journey, trackify_contact_id)
    return {"ok": True, "data": data}


@router.get("/outbox", dependencies=[plugin_permission("manage")])
async def outbox(status: str | None = None, limit: int = 50):
    """Fila de saída: contadores + as linhas que precisam de atenção."""
    def _run():
        from sqlalchemy import text as _t
        from plugins.context import make_plugin_db
        st = dispatcher.stats()
        where = "WHERE status = :st" if status else "WHERE status IN ('failed','blocked')"
        with make_plugin_db() as conn:
            rows = conn.execute(
                _t("SELECT id, external_id, kind, phone, status, attempts, "
                   "       last_error, last_http_status, occurred_at, updated_at "
                   f"FROM plugin_trackify_outbox {where} "
                   "ORDER BY updated_at DESC LIMIT :lim"),
                {"st": status, "lim": max(1, min(limit, 200))}).mappings().all()
        return {"stats": st, "rows": [dict(r) for r in rows]}
    return {"ok": True, "data": await asyncio.to_thread(_run)}


@router.post("/outbox/{row_id}/requeue", dependencies=[plugin_permission("manage")])
async def requeue(row_id: int):
    """Devolve uma linha à fila — o caminho de volta depois de consertar o canal
    ou o mapeamento no Trackify (que é o que produz `blocked`)."""
    def _run():
        import time as _time
        from sqlalchemy import text as _t
        from plugins.context import make_plugin_db
        with make_plugin_db() as conn:
            res = conn.execute(
                _t("UPDATE plugin_trackify_outbox SET status = 'pending', attempts = 0, "
                   " next_attempt_at = :now, last_error = '', updated_at = :now "
                   "WHERE id = :id AND status IN ('failed','blocked','dropped')"),
                {"id": row_id, "now": _time.time()})
        return int(res.rowcount or 0)
    n = await asyncio.to_thread(_run)
    if not n:
        return _err("Linha não encontrada ou não reprocessável.", 404)
    audit("trackify", "outbox.requeue", resource_id=str(row_id))
    return {"ok": True, "data": {"requeued": n}}


@router.get("/ingestion-key", dependencies=[plugin_permission("manage")])
async def get_key():
    """Devolve a chave MASCARADA. O form declarativo de settings devolveria o
    valor em claro (``GET /api/plugins/{id}/settings`` não mascara nada), por
    isso a chave mora fora dele."""
    val = await asyncio.to_thread(lambda: (_config.setting("api_key") or "").strip())
    return {"ok": True, "data": {"set": bool(val), "masked": _MASK if val else ""}}


@router.put("/ingestion-key", dependencies=[plugin_permission("manage")])
async def put_key(body: dict):
    """Grava a chave. O sentinela ``***`` preserva o valor atual — assim a tela
    pode enviar o formulário inteiro sem apagar o segredo por descuido."""
    val = str((body or {}).get("api_key") or "")
    if val == _MASK:
        return {"ok": True, "data": {"unchanged": True}}

    def _run():
        from db.repositories import config_repo
        config_repo.set(_config.PREFIX + "api_key", val.strip())
    await asyncio.to_thread(_run)
    audit("trackify", "config.update", resource_id="api_key")
    return {"ok": True, "data": {"set": bool(val.strip())}}


@router.get("/health", dependencies=[plugin_permission("view")])
async def health():
    """Distingue "não configurado" de "inalcançável" de "schema mudou".

    Sem isto, um schema alterado no Trackify viraria uma tela vazia sem
    explicação — o erro tem que ser acionável, não um 500.
    """
    configured = await asyncio.to_thread(trackify_db.is_configured)
    reachable, message = (False, "DSN do Nexus não configurado.")
    schema_ok, missing = False, []
    if configured:
        reachable, message = await asyncio.to_thread(trackify_db.ping)
        if reachable:
            schema_ok, missing = await asyncio.to_thread(trackify_db.schema_check)
    return {"ok": True, "data": {
        "configured": configured,
        "reachable": reachable,
        "message": message,
        "schema_ok": schema_ok,
        "schema_missing": missing,
        "base_url_set": bool(_config.nexus_base_url()),
        "mirror_enabled": bool(_config.setting("mirror_enabled", False)),
    }}
