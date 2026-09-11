"""Gestão de chaves de API no PAINEL (plano "Sistema de API com chave por usuário").

Emitir / listar / revogar. O *uso* da chave não passa por aqui — quem autentica é
o middleware (``server/app.py``). Estas rotas são gateadas por ``apikey.manage``,
que é **admin-only** de propósito (§4.2): a chave herda TODAS as permissões do
dono, então quem cunha chave escolhe poder.

**Guardrails de emissão (§4)** — como a chave vale em todo ``/api/*`` e herda tudo
do dono, o controle inteiro se concentra no momento de emitir:

0. **emitir PARA OUTRO usuário exige ``users.manage``.** ``apikey.manage`` sozinho
   é uma permissão sobre SI MESMO: cunha, lista e revoga apenas as próprias
   chaves. Sem isso a permissão seria uma escalada silenciosa — quem a tivesse
   emitiria uma chave no nome de um administrador e herdaria a instalação
   inteira. Quem já pode *criar e editar usuários* (``users.manage``) já podia
   fazer isso pela porta da frente, então para essa pessoa nada muda;
1. chave para usuário com papel ``admin`` exige ``confirm: true`` explícito
   (chave de admin vazada = instalação inteira);
2. ``apikey.manage`` fora de ``ROLE_DEFAULTS`` (ver ``server/permissions.py``);
3. rate-limit com bucket PRÓPRIO por chave (aplicado no middleware);
4. ``expires_at`` preenchido por padrão (1 ano) em vez de nulo;
5. o segredo aparece UMA ÚNICA VEZ, na resposta da criação.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import Depends, Request

from db import audit_actions
from db.repositories import api_key_repo, user_repo
from server import api_keys as keylib
from server import audit_listener
from server.authz import check, current_user
from server.deps import install_exception_handlers, require_permission
from server.helpers import _err, _ok

logger = logging.getLogger(__name__)

# Teto de validade — uma chave "eterna" é o pior caso de vazamento silencioso.
_MAX_TTL_DAYS = 3650


def register_routes(app, deps):
    install_exception_handlers(app)

    def _owner_view(row: dict, users_by_id: dict) -> dict:
        view = keylib.public_view(row)
        owner = users_by_id.get(row.get("user_id"))
        view["user_name"] = (owner or {}).get("name") or ""
        view["user_email"] = (owner or {}).get("email") or ""
        return view

    def _pode_escolher_dono(request: Request) -> bool:
        """``True`` quando o ator pode emitir/ver/revogar chave de OUTRO usuário.

        A régua é ``users.manage`` — quem cria e edita usuários já consegue, pela
        porta da frente, um usuário com as permissões que quiser. Para todo o
        resto, ``apikey.manage`` vale sobre si mesmo e mais ninguém (§4.0)."""
        return check(request, "users.manage")

    def _escopo_de_leitura(request: Request) -> int | None:
        """``user_id`` ao qual a listagem está presa, ou ``None`` para "todos"."""
        if _pode_escolher_dono(request):
            return None
        actor = current_user(request)
        return (actor or {}).get("id")

    @app.get("/api/api-keys/owners",
             dependencies=[Depends(require_permission("apikey.manage"))])
    async def list_api_key_owners(request: Request):
        """Donos que ESTE ator pode escolher ao emitir — a fonte do seletor da tela.

        Existe porque ``GET /api/users`` é gateado por ``users.manage``: sem esta
        rota, quem tem só ``apikey.manage`` recebia 403 e ficava com o seletor
        vazio, sem conseguir emitir chave nem para si mesmo. Devolve a lista já
        recortada pela mesma regra que o POST aplica, então a tela não decide
        nada — ela só desenha o que o servidor autoriza."""
        def _load():
            actor = current_user(request)
            todos = _pode_escolher_dono(request)
            if todos:
                users = [u for u in user_repo.list_all() if u.get("is_active")]
            else:
                eu = user_repo.get((actor or {}).get("id")) if actor else None
                users = [eu] if eu and eu.get("is_active") else []
            return {
                "owners": [{"id": u["id"], "name": u.get("name") or "",
                            "email": u.get("email") or "",
                            "is_admin": bool(u.get("is_admin"))} for u in users],
                "can_choose_others": bool(todos),
            }

        return _ok(await asyncio.to_thread(_load))

    @app.get("/api/api-keys",
             dependencies=[Depends(require_permission("apikey.manage"))])
    async def list_api_keys(request: Request, user_id: int | None = None,
                            include_revoked: bool = True):
        """Lista as chaves (NUNCA o segredo — ele só existiu na criação).

        Sem ``users.manage`` a listagem é PRESA às chaves do próprio ator: ver a
        chave alheia não vaza o segredo, mas entrega o inventário de integrações
        da instalação — e o ``user_id`` da query viraria um enumerador de quem
        tem chave. O recorte acontece aqui, não no filtro do cliente."""
        preso_a = _escopo_de_leitura(request)
        alvo = preso_a if preso_a is not None else user_id
        def _load():
            rows = (api_key_repo.list_for_user(alvo, include_revoked=include_revoked)
                    if alvo else
                    api_key_repo.list_all(include_revoked=include_revoked))
            users_by_id = {u["id"]: u for u in user_repo.list_all()}
            return [_owner_view(r, users_by_id) for r in rows]

        return _ok(await asyncio.to_thread(_load))

    @app.post("/api/api-keys",
              dependencies=[Depends(require_permission("apikey.manage"))])
    async def create_api_key(body: dict, request: Request):
        """Emite uma chave. Devolve o segredo UMA ÚNICA VEZ em ``key``."""
        label = (body.get("label") or "").strip()
        if not label:
            return _err("Informe um rótulo para a chave.")
        if len(label) > 120:
            return _err("Rótulo deve ter no máximo 120 caracteres.")

        raw_uid = body.get("user_id")
        actor = current_user(request)
        try:
            target_id = int(raw_uid) if raw_uid not in (None, "") else (
                actor.get("id") if actor else None)
        except (TypeError, ValueError):
            return _err("user_id inválido.")
        if target_id is None:
            return _err("Informe o usuário dono da chave.")

        # Guardrail §4.0 — ``apikey.manage`` é permissão sobre SI MESMO. Emitir no
        # nome de outra pessoa é escalar para as permissões dela (e, no caso de um
        # admin, para a instalação inteira), então exige ``users.manage`` — quem já
        # pode fabricar o usuário que quiser. A comparação é com o ator da
        # requisição; nenhum campo do corpo participa da decisão.
        if not _pode_escolher_dono(request):
            eu = (actor or {}).get("id")
            if eu is None or target_id != eu:
                return _err(
                    "Você só pode emitir chave de API para você mesmo. Emitir no "
                    "nome de outro usuário exige a permissão de gerenciar usuários.",
                    status=403, data={"reason": "owner_must_be_self"})

        owner = await asyncio.to_thread(user_repo.get, target_id)
        if owner is None:
            return _err("Usuário não encontrado.", 404)
        if not owner.get("is_active"):
            return _err("Usuário inativo não pode ter chave de API.")

        # Guardrail §4.1 — chave de admin dá a instalação inteira. Exige um "sim"
        # explícito em vez de recusar de vez: há casos legítimos (migração), mas
        # nenhum deles é acidental.
        if owner.get("is_admin") and not body.get("confirm"):
            return _err(
                "Este usuário é administrador: a chave herdaria TODAS as permissões "
                "da instalação. Prefira criar um usuário dedicado para a integração. "
                "Para continuar mesmo assim, reenvie com \"confirm\": true.",
                status=409, data={"reason": "admin_owner_requires_confirm"})

        # Guardrail §4.4 — validade preenchida por padrão (1 ano), nunca nula.
        raw_days = body.get("expires_in_days", None)
        if raw_days in (None, ""):
            expires_at = time.time() + keylib.DEFAULT_TTL_SECONDS
        elif str(raw_days).lower() == "never":
            if not body.get("confirm"):
                return _err(
                    "Chave sem validade exige confirmação explícita "
                    "(\"confirm\": true).", status=409,
                    data={"reason": "never_expires_requires_confirm"})
            expires_at = None
        else:
            try:
                days = int(raw_days)
            except (TypeError, ValueError):
                return _err("expires_in_days inválido.")
            if days < 1 or days > _MAX_TTL_DAYS:
                return _err(f"expires_in_days deve estar entre 1 e {_MAX_TTL_DAYS}.")
            expires_at = time.time() + days * 86400

        try:
            raw, prefix, key_hash = await asyncio.to_thread(keylib.generate_key)
        except RuntimeError as e:
            return _err(str(e), status=503)

        row = await asyncio.to_thread(
            api_key_repo.create, user_id=target_id, label=label,
            key_hash=key_hash, prefix=prefix, last4=keylib.last4(raw),
            expires_at=expires_at,
            created_by=(actor.get("id") if actor else None))

        # NUNCA o segredo na auditoria — só rótulo, dono e validade.
        await asyncio.to_thread(
            audit_listener.record,
            action=audit_actions.AuditAction.API_KEY_CREATE,
            resource_type=audit_actions.ResourceType.API_KEY,
            resource_id=row["id"],
            after={"label": label, "user_id": target_id,
                   "user_email": owner.get("email"),
                   "prefix": prefix, "expires_at": expires_at})
        logger.info("[ApiKey] chave %s emitida para user=%s por user=%s",
                    row["id"], target_id, (actor or {}).get("id"))

        view = keylib.public_view(row)
        view["user_name"] = owner.get("name") or ""
        view["user_email"] = owner.get("email") or ""
        # ↓ a ÚNICA vez que o segredo existe fora da memória do cliente.
        view["key"] = raw
        return _ok(view)

    @app.delete("/api/api-keys/{key_id}",
                dependencies=[Depends(require_permission("apikey.manage"))])
    async def revoke_api_key(key_id: int, request: Request):
        """Revoga (soft) — chamadas seguintes com esta chave viram 401."""
        row = await asyncio.to_thread(api_key_repo.get, key_id)
        if row is None:
            return _err("Chave não encontrada.", 404)
        # Mesma régua da emissão (§4.0): sem ``users.manage``, só as próprias chaves.
        # 404 em vez de 403 de propósito — um 403 confirmaria que a chave existe.
        if not _pode_escolher_dono(request):
            eu = (current_user(request) or {}).get("id")
            if eu is None or row.get("user_id") != eu:
                return _err("Chave não encontrada.", 404)
        if row.get("revoked_at"):
            return _ok(keylib.public_view(row))
        await asyncio.to_thread(api_key_repo.revoke, key_id)
        await asyncio.to_thread(
            audit_listener.record,
            action=audit_actions.AuditAction.API_KEY_REVOKE,
            resource_type=audit_actions.ResourceType.API_KEY,
            resource_id=key_id,
            before={"label": row.get("label"), "user_id": row.get("user_id"),
                    "prefix": row.get("prefix")},
            after={"revoked": True})
        logger.info("[ApiKey] chave %s revogada por user=%s",
                    key_id, (current_user(request) or {}).get("id"))
        fresh = await asyncio.to_thread(api_key_repo.get, key_id)
        return _ok(keylib.public_view(fresh or row))
