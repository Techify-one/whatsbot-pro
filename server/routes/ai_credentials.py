"""Cofre de credenciais das tools de IA (plano 177).

Gerencia as credenciais que as tools de CÓDIGO recebem pelo ``stdin`` do
subprocesso isolado. Gated por ``agent.credentials.manage``.

⚠️ O valor de um campo secreto **não vem em nenhuma listagem**: a lista traz
``{key, secret, has_value}`` e ``value`` só aparece quando o operador marcou o
campo como não secreto. A ÚNICA via de volta é ``.../fields/{key}/reveal`` (o
"olho" da tela): um campo por vez, só por sessão de painel, auditada. O valor
também não entra no ``audit_log`` (só nomes de campo e ``secret_definido``), no
evento ``ai.config.changed`` nem em log.

Módulo à parte de ``ai_engine.py`` (já grande). Salvar credencial NÃO agenda
restart: o host lê o cofre a cada execução de tool.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from db import audit_actions
from db.repositories import credential_repo, tool_repo
from server import audit_listener
from server.authz import current_user
from server.deps import install_exception_handlers, require_permission
from server.helpers import _err, _ok

logger = logging.getLogger(__name__)

_PERM = "agent.credentials.manage"


def _emit_changed(name: str) -> None:
    """Mesmo contrato de ``ai_engine._emit_changed``: limpa o cache de config e
    avisa o barramento. O payload é ``{kind, key, ts}`` — jamais valor."""
    try:
        from ai_engine import dynamic_registry
        dynamic_registry.invalidate()
    except Exception:
        pass
    try:
        from plugins.events import emit as emit_event
        emit_event("ai.config.changed",
                   {"kind": "credential", "key": name, "ts": time.time()})
    except Exception:
        pass


def _actor(request: Request) -> tuple[int | None, str | None]:
    user = current_user(request)
    if not user:
        return None, None
    return user.get("id"), (user.get("name") or user.get("email") or None)


async def _audit(action: str, name: str, *, before=None, after=None) -> None:
    # NUNCA o valor: ``before``/``after`` levam só nomes de campo e a marca
    # ``secret_definido``. A denylist do audit_repo casa nome EXATO de chave e o
    # nome do campo é do operador — não dá para confiar nela aqui.
    # ``record`` grava no banco de forma BLOQUEANTE: fora do loop (padrão de
    # webhooks_out.py). O ator vem de um ContextVar, que ``to_thread`` copia.
    await asyncio.to_thread(
        audit_listener.record,
        action=action,
        resource_type=audit_actions.ResourceType.CREDENTIAL,
        resource_id=name,
        before=before, after=after)


def register_routes(app, deps):
    install_exception_handlers(app)

    @app.get("/api/ai/credentials",
             dependencies=[Depends(require_permission(_PERM))])
    async def list_credentials():
        items = await asyncio.to_thread(credential_repo.list_meta)
        # Tools que o vínculo aponta mas não existem mais (P4: sem FK) — a tela
        # mostra "tool inexistente" e deixa remover.
        known = {r["name"] for r in await asyncio.to_thread(tool_repo.list_all)}
        for item in items:
            item["missing_tools"] = [t for t in item["tools"] if t not in known]
        return _ok(items)

    @app.put("/api/ai/credentials/{name}",
             dependencies=[Depends(require_permission(_PERM))])
    async def save_credential(name: str, body: dict, request: Request):
        actor_id, actor_name = _actor(request)
        try:
            meta, change = await asyncio.to_thread(
                credential_repo.upsert_credential,
                name, body.get("description", ""), body.get("fields"),
                actor_id=actor_id, actor_name=actor_name)
        except ValueError as e:
            return _err(str(e))
        await _audit(
            audit_actions.AuditAction.CREDENTIAL_CREATE if change["created"]
            else audit_actions.AuditAction.CREDENTIAL_UPDATE,
            name,
            after={
                "campos": change["campos"],
                "definidos": change["definidos"],
                "removidos": change["removidos"],
                "secret_definido": bool(change["definidos"]),
            })
        _emit_changed(name)
        logger.info("AI credential saved: %s (%d campos)", name, len(change["campos"]))
        return _ok(meta)

    @app.delete("/api/ai/credentials/{name}",
                dependencies=[Depends(require_permission(_PERM))])
    async def delete_credential(name: str):
        before = await asyncio.to_thread(credential_repo.get_meta, name)
        if before is None:
            return _err("Credencial não encontrada.", 404)
        await asyncio.to_thread(credential_repo.delete, name)
        await _audit(audit_actions.AuditAction.CREDENTIAL_DELETE, name,
               before={"campos": [f["key"] for f in before["fields"]],
                       "tools": before["tools"]})
        _emit_changed(name)
        return _ok({"deleted": name})

    @app.post("/api/ai/credentials/{name}/fields/{key}/reveal",
              dependencies=[Depends(require_permission(_PERM))])
    async def reveal_credential_field(name: str, key: str, request: Request):
        """O ÚNICO endpoint que devolve o valor de um campo salvo (o "olho" da tela).

        POST e não GET: não entra em cache, em prefetch nem em histórico de
        navegação por engano. Só sessão de painel: uma chave de API herda todas as
        permissões do dono e uma integração não tem por que LER a credencial.
        Cada leitura vira ``credential.reveal`` na trilha (credencial + campo, sem
        o valor).
        """
        if getattr(request.state, "api_key", None):
            return _err(
                "Ver o valor de uma credencial só é permitido pelo painel "
                "(sessão), não por chave de API.", 403)
        value = await asyncio.to_thread(credential_repo.reveal_field, name, key)
        if value is None:
            return _err("Credencial ou campo não encontrado.", 404)
        await _audit(audit_actions.AuditAction.CREDENTIAL_REVEAL, name,
               after={"campo": key})
        return JSONResponse(
            {"ok": True, "data": {"name": name, "key": key, "value": value}},
            headers={"Cache-Control": "no-store"})

    @app.put("/api/ai/credentials/{name}/tools",
             dependencies=[Depends(require_permission(_PERM))])
    async def set_credential_tools(name: str, body: dict):
        tools = body.get("tools")
        if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
            return _err("tools deve ser uma lista de nomes.")
        before = await asyncio.to_thread(credential_repo.get_meta, name)
        # Vínculo que JÁ existe fica isento: a tool pode ter sido excluída (P4, sem
        # FK) e reenviar a lista que a mantém não pode ser recusado por isso.
        already = set((before or {}).get("tools", []))
        bad = await _not_code_tools([t for t in tools if t not in already])
        if bad:
            return _err(_NOT_CODE_MSG % ", ".join(bad))
        saved = await asyncio.to_thread(credential_repo.set_tool_links, name, tools)
        if saved is None:
            return _err("Credencial não encontrada.", 404)
        await _audit(audit_actions.AuditAction.CREDENTIAL_LINK, name,
               before={"tools": (before or {}).get("tools", [])},
               after={"tools": saved})
        _emit_changed(name)
        return _ok({"name": name, "tools": saved})

    @app.put("/api/ai/tools/{tool_name}/credentials",
             dependencies=[Depends(require_permission(_PERM))])
    async def set_tool_credentials(tool_name: str, body: dict):
        names = body.get("credentials")
        if not isinstance(names, list) or any(not isinstance(c, str) for c in names):
            return _err("credentials deve ser uma lista de nomes.")
        bad = await _not_code_tools([tool_name])
        if bad:
            return _err(_NOT_CODE_MSG % ", ".join(bad))
        before = await asyncio.to_thread(credential_repo.links_for_tool, tool_name)
        try:
            saved = await asyncio.to_thread(
                credential_repo.set_credential_links, tool_name, names)
        except ValueError as e:
            return _err(str(e))
        # ⚠️ NÃO use a chave "credentials": ela está na denylist do audit_repo
        # (casa nome EXATO) e a trilha mostraria "***" no lugar dos nomes.
        await _audit(audit_actions.AuditAction.CREDENTIAL_LINK, tool_name,
               before={"tool": tool_name, "credenciais": before},
               after={"tool": tool_name, "credenciais": saved})
        for cred in sorted(set(before) | set(saved)):
            _emit_changed(cred)
        return _ok({"tool": tool_name, "credentials": saved})


_NOT_CODE_MSG = (
    "Só tools de código recebem credencial (as demais rodam no processo do "
    "servidor e não passam pelo runner isolado): %s.")


async def _not_code_tools(names: list[str]) -> list[str]:
    """Nomes que NÃO são ``kind='code'``. Liberar credencial para tool in-process
    daria falsa sensação de controle — o runner isolado é o único que a entrega.
    Inexistente também é recusada (o vínculo é por nome, mas nasce de uma tool real)."""
    bad = []
    for name in names:
        row = await asyncio.to_thread(tool_repo.get, name)
        if row is None or (row.get("kind") or "code") != "code":
            bad.append(name)
    return bad
