"""REST endpoints for the DB-driven AI engine (config-in-DB + code-in-DB).

CRUD over ``ai_agents`` / ``ai_prompts`` / ``ai_variables`` / ``ai_tools``.

Agent / prompt / variable edits take effect on the **next message** with no
restart — ``agent.agent_factory.build_for_contact`` reads the DB per request.
Tool edits (code-in-DB) require a process restart so the installer re-runs:
mutating a tool schedules one, and ``POST /api/ai/restart`` triggers it manually.

Every mutation emits ``ai.config.changed`` so cache layers / multi-worker
deployments can invalidate.
"""

import asyncio
import logging
import re
import time

from fastapi import Depends, Query, Request

from server.helpers import _ok, _err
from server.deps import require_permission, install_exception_handlers, PermissionDeniedError
from server.authz import has_permission
from db.repositories import (
    agent_repo, agent_prompt_repo, prompt_repo, variable_repo, tool_repo,
)
from plugins.events import emit as emit_event
from plugins.restart import schedule_restart

logger = logging.getLogger(__name__)


def _coerce_agent_json_fields(row: dict) -> dict:
    """Coage os campos JSON de um agente vindo do DB para tipos limpos (plano 34 F3).

    Tolera linhas duplo-codificadas via ``coerce_json`` (N-camadas): usado no
    patch-só-prompt (``save_agent_prompt``) para NUNCA repassar uma string crua ao
    ``save()`` — o que gerava tripla codificação sobre uma linha já suja. As duas
    rotas de gravação (``save_agent`` valida a entrada do usuário; esta saneia a
    linha do DB) deixam de divergir no tratamento desses campos.
    """
    from db.repositories._mapping import coerce_json
    mc = coerce_json(row.get("model_config"), {})
    hc = coerce_json(row.get("hooks_config"), {})
    tn = coerce_json(row.get("tool_names"), None)
    rt = coerce_json(row.get("routing_targets"), None)
    return {
        "model_config": mc if isinstance(mc, dict) else {},
        "hooks_config": hc if isinstance(hc, dict) else {},
        "tool_names": tn if isinstance(tn, list) else None,
        "routing_targets": rt if isinstance(rt, list) else None,
    }


def _emit_changed(kind: str, key: str) -> None:
    # Clear the config cache first so the very next message sees the edit, then
    # broadcast for any other listeners (plugins, multi-worker invalidation).
    try:
        from ai_engine import dynamic_registry
        dynamic_registry.invalidate()
    except Exception:
        pass
    try:
        emit_event("ai.config.changed", {"kind": kind, "key": key, "ts": time.time()})
    except Exception:
        pass


def register_routes(app, deps):

    # B1: render require_permission's PermissionDeniedError to the legacy
    # _err(403) envelope (idempotent across route modules).
    install_exception_handlers(app)

    # ── Agents ──────────────────────────────────────────────────────────
    @app.get("/api/ai/agents",
             dependencies=[Depends(require_permission("agent.config.manage"))])
    async def list_agents():
        rows = await asyncio.to_thread(agent_repo.list_all)
        return _ok(rows)

    @app.get("/api/ai/agents/{agent_key}",
             dependencies=[Depends(require_permission("agent.config.manage"))])
    async def get_agent(agent_key: str):
        row = await asyncio.to_thread(agent_repo.get, agent_key)
        if not row:
            return _err("Agente não encontrado.", status=404)
        return _ok(row)

    @app.put("/api/ai/agents/{agent_key}")
    async def save_agent(request: Request, agent_key: str, body: dict):
        # RBAC granular: criar, duplicar e editar são permissões distintas.
        #  - agente NOVO (chave inexistente): exige agent.create, ou
        #    agent.duplicate quando a requisição veio do botão "Duplicar"
        #    (body.duplicate=true);
        #  - agente EXISTENTE: exige agent.config.manage.
        # O prompt continua gated por agent.prompts.edit (mais abaixo).
        # Default-allow legado (has_permission → True sem usuário) preservado.
        existing = await asyncio.to_thread(agent_repo.get, agent_key)
        is_create = existing is None
        if is_create:
            needed = "agent.duplicate" if body.get("duplicate") else "agent.create"
        else:
            needed = "agent.config.manage"
        if not has_permission(request, needed):
            raise PermissionDeniedError()
        model_config = body.get("model_config", {})
        if not isinstance(model_config, dict):
            return _err("model_config deve ser um objeto.")
        tool_names = body.get("tool_names")
        if tool_names is not None and not isinstance(tool_names, list):
            return _err("tool_names deve ser uma lista ou null.")
        routing_targets = body.get("routing_targets")
        if routing_targets is not None and not isinstance(routing_targets, list):
            return _err("routing_targets deve ser uma lista ou null.")
        hooks_config = body.get("hooks_config", {})
        if hooks_config is not None and not isinstance(hooks_config, dict):
            return _err("hooks_config deve ser um objeto.")
        version_mode = body.get("version_mode", "new")
        if version_mode not in ("new", "amend"):
            return _err("version_mode deve ser 'new' ou 'amend'.")
        # Plano 22: the default agent is the engine's fallback (build_for_contact
        # cascade always lands on it) — it must never be disabled.
        if agent_key == agent_repo.DEFAULT_AGENT_KEY and not bool(body.get("enabled", True)):
            return _err("O agente padrão não pode ser desativado.", status=400)
        # Prompt é permissão separada (agent.prompts.edit). Quem só tem
        # agent.config.manage edita modelo/tools/roteamento mas NÃO altera o
        # prompt: preservamos o prompt atual (agente novo nasce sem prompt →
        # cai no DEFAULT_SYSTEM_PROMPT). "hide, don't disable" no backend.
        prompt = body.get("prompt", "")
        if not has_permission(request, "agent.prompts.edit"):
            # Sem permissão de editar prompt: preserva o prompt atual (agente
            # existente) ou nasce vazio (novo/duplicado → cai no prompt padrão).
            prompt = (existing or {}).get("prompt", "") or ""
        row = await asyncio.to_thread(
            agent_repo.save, agent_key,
            display_name=body.get("display_name", ""),
            prompt=prompt,
            prompt_key=body.get("prompt_key", ""),
            model_config=model_config,
            tool_names=tool_names,
            enabled=bool(body.get("enabled", True)),
            description=body.get("description", ""),
            is_router=bool(body.get("is_router", False)),
            is_default=bool(body.get("is_default", False)),
            routing_targets=routing_targets,
            hooks_config=hooks_config or {},
            change_note=body.get("change_note"),
            version_mode=version_mode,
        )
        _emit_changed("agent", agent_key)
        logger.info("AI agent saved: %s (v%s, prompt=%s)",
                    agent_key, row.get("version"), version_mode)
        return _ok(row)

    @app.put("/api/ai/agents/{agent_key}/prompt",
             dependencies=[Depends(require_permission("agent.prompts.edit"))])
    async def save_agent_prompt(agent_key: str, body: dict):
        """Patch only an agent's inline prompt, preserving its other fields.

        Used by the onboarding wizard (which knows the prompt but not the full
        agent payload). The agent must already exist — the default is seeded at
        boot; for any other key the caller uses the full PUT above.
        """
        existing = await asyncio.to_thread(agent_repo.get, agent_key)
        if not existing:
            return _err("Agente não encontrado.", status=404)
        # Plano 34 F3: saneia os campos JSON da linha existente antes de repassá-los
        # ao save() — uma linha suja não pode virar tripla codificação neste patch.
        clean = _coerce_agent_json_fields(existing)
        row = await asyncio.to_thread(
            agent_repo.save, agent_key,
            display_name=existing.get("display_name") or "",
            prompt=body.get("prompt", ""),
            prompt_key=existing.get("prompt_key", ""),
            model_config=clean["model_config"],
            tool_names=clean["tool_names"],
            enabled=bool(existing.get("enabled", True)),
            description=existing.get("description", ""),
            is_router=bool(existing.get("is_router", False)),
            is_default=bool(existing.get("is_default", False)),
            routing_targets=clean["routing_targets"],
            hooks_config=clean["hooks_config"],
            change_note=body.get("change_note"),
        )
        _emit_changed("agent", agent_key)
        logger.info("AI agent prompt saved: %s (v%s)", agent_key, row.get("version"))
        return _ok(row)

    @app.delete("/api/ai/agents/{agent_key}",
                dependencies=[Depends(require_permission("agent.config.manage"))])
    async def delete_agent(agent_key: str):
        if agent_key == agent_repo.DEFAULT_AGENT_KEY:
            return _err("O agente padrão não pode ser excluído.", status=400)
        # Refuse while the agent is still a routing target of some router, so a
        # router never points at a vanished agent. The user removes it there first.
        try:
            agents = await asyncio.to_thread(agent_repo.list_all)
            routers = [
                a["agent_key"] for a in agents
                if a.get("is_router") and agent_key in (a.get("routing_targets") or [])
            ]
            if routers:
                return _err(
                    "Agente é destino de roteamento de: " + ", ".join(routers)
                    + ". Remova-o desses roteadores antes de excluir.",
                    status=409,
                )
        except Exception:
            pass
        deleted = await asyncio.to_thread(agent_repo.delete, agent_key)
        if not deleted:
            return _err("Agente não encontrado.", status=404)
        _emit_changed("agent", agent_key)
        logger.info("AI agent deleted: %s", agent_key)
        return _ok({"deleted": True})

    # ── History / rollback (plano 06) ───────────────────────────────────
    # O histórico do agente inteiro É o versionamento (histórico/comparar/
    # restaurar), então exige agent.prompts.version — igual à trilha dedicada do
    # prompt. Como o snapshot empacota prompt + config, o rollback só restaura a
    # config se o usuário também tiver agent.config.manage (senão preserva a atual).
    @app.get("/api/ai/agents/{agent_key}/history",
             dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def agent_history(agent_key: str):
        return _ok(await asyncio.to_thread(agent_repo.list_history, agent_key))

    @app.post("/api/ai/agents/{agent_key}/rollback/{version}",
              dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def agent_rollback(request: Request, agent_key: str, version: int):
        # Restaura só o que o usuário pode mudar: config só com agent.config.manage;
        # o prompt já está liberado (a rota exige agent.prompts.version).
        preserve_config = not has_permission(request, "agent.config.manage")
        row = await asyncio.to_thread(
            agent_repo.rollback, agent_key, version, False, preserve_config)
        if not row:
            return _err("Versão não encontrada.", status=404)
        _emit_changed("agent", agent_key)
        logger.info("AI agent rolled back: %s -> v%s (nova v%s)",
                    agent_key, version, row.get("version"))
        return _ok(row)

    # ── Dedicated prompt version trail (git-like, additive to /history) ──
    @app.get("/api/ai/agents/{agent_key}/prompt/history",
             dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def agent_prompt_history(agent_key: str):
        return _ok(await asyncio.to_thread(agent_prompt_repo.list_history, agent_key))

    @app.get("/api/ai/agents/{agent_key}/prompt/history/{version}",
             dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def agent_prompt_version(agent_key: str, version: int):
        row = await asyncio.to_thread(agent_prompt_repo.get_version, agent_key, version)
        if not row:
            return _err("Versão não encontrada.", status=404)
        return _ok(row)

    @app.get("/api/ai/agents/{agent_key}/prompt/diff",
             dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def agent_prompt_diff(
        agent_key: str,
        from_version: int = Query(..., alias="from"),
        to_version: int = Query(..., alias="to"),
    ):
        d = await asyncio.to_thread(
            agent_prompt_repo.diff, agent_key, from_version, to_version)
        if d is None:
            return _err("Versão não encontrada.", status=404)
        return _ok(d)

    @app.patch("/api/ai/agents/{agent_key}/prompt/history/{version}",
               dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def agent_prompt_rename(agent_key: str, version: int, body: dict):
        row = await asyncio.to_thread(
            agent_prompt_repo.rename_version, agent_key, version, body.get("note"))
        if not row:
            return _err("Versão não encontrada.", status=404)
        logger.info("AI agent prompt version renamed: %s v%s", agent_key, version)
        return _ok(row)

    @app.delete("/api/ai/agents/{agent_key}/prompt/history/{version}",
                dependencies=[Depends(require_permission("agent.prompts.delete"))])
    async def agent_prompt_delete(agent_key: str, version: int):
        ok = await asyncio.to_thread(
            agent_prompt_repo.delete_version, agent_key, version)
        if not ok:
            return _err("Versão não encontrada.", status=404)
        logger.info("AI agent prompt version deleted: %s v%s", agent_key, version)
        return _ok({"version": version})

    @app.post("/api/ai/agents/{agent_key}/prompt/restore/{version}",
              dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def agent_prompt_restore(agent_key: str, version: int):
        row = await asyncio.to_thread(agent_prompt_repo.restore, agent_key, version)
        if not row:
            return _err("Versão não encontrada.", status=404)
        _emit_changed("agent", agent_key)
        logger.info("AI agent prompt restored: %s -> v%s (nova agente v%s)",
                    agent_key, version, row.get("version"))
        return _ok(row)

    @app.get("/api/ai/prompts/{prompt_key}/history",
             dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def prompt_history(prompt_key: str):
        return _ok(await asyncio.to_thread(prompt_repo.list_history, prompt_key))

    @app.post("/api/ai/prompts/{prompt_key}/rollback/{version}",
              dependencies=[Depends(require_permission("agent.prompts.version"))])
    async def prompt_rollback(prompt_key: str, version: int):
        row = await asyncio.to_thread(prompt_repo.rollback, prompt_key, version)
        if not row:
            return _err("Versão não encontrada.", status=404)
        _emit_changed("prompt", prompt_key)
        return _ok(row)

    @app.get("/api/ai/tools/{name}/history",
             dependencies=[Depends(require_permission("agent.tools.manage"))])
    async def tool_history(name: str):
        return _ok(await asyncio.to_thread(tool_repo.list_history, name))

    @app.post("/api/ai/tools/{name}/rollback/{version}",
              dependencies=[Depends(require_permission("agent.tools.manage"))])
    async def tool_rollback(name: str, version: int):
        row = await asyncio.to_thread(tool_repo.rollback, name, version)
        if not row:
            return _err("Versão não encontrada.", status=404)
        _emit_changed("tool", name)
        return _ok(row)

    # ── Prompts ─────────────────────────────────────────────────────────
    @app.get("/api/ai/prompts",
             dependencies=[Depends(require_permission("agent.prompts.edit"))])
    async def list_prompts():
        rows = await asyncio.to_thread(prompt_repo.list_all)
        return _ok(rows)

    @app.get("/api/ai/prompts/{prompt_key}",
             dependencies=[Depends(require_permission("agent.prompts.edit"))])
    async def get_prompt(prompt_key: str):
        row = await asyncio.to_thread(prompt_repo.get, prompt_key)
        if not row:
            return _err("Prompt não encontrado.", status=404)
        return _ok(row)

    @app.put("/api/ai/prompts/{prompt_key}",
             dependencies=[Depends(require_permission("agent.prompts.edit"))])
    async def save_prompt(prompt_key: str, body: dict):
        row = await asyncio.to_thread(prompt_repo.save, prompt_key, body.get("body", ""))
        _emit_changed("prompt", prompt_key)
        logger.info("AI prompt saved: %s (v%s)", prompt_key, row.get("version"))
        return _ok(row)

    # ── Variables ───────────────────────────────────────────────────────
    @app.get("/api/ai/variables",
             dependencies=[Depends(require_permission("agent.variables.manage"))])
    async def list_variables():
        rows = await asyncio.to_thread(variable_repo.list_all)
        return _ok(rows)

    @app.put("/api/ai/variables/{name}",
             dependencies=[Depends(require_permission("agent.variables.manage"))])
    async def save_variable(name: str, body: dict):
        # A1 (plano 31 F6): a regex de render (agent_factory._PLACEHOLDER_RE) só
        # casa {identificador} — um nome fora do padrão nunca renderiza (vira
        # peso morto no banco). Paridade com o NAME_RE do VariablesEditor.js.
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", name or ""):
            return _err("Nome inválido: use letras, números e _ (começando por "
                        "letra, máx. 64 caracteres).", status=400)
        row = await asyncio.to_thread(
            variable_repo.save, name, body.get("value", "")
        )
        _emit_changed("variable", name)
        return _ok(row)

    @app.delete("/api/ai/variables/{name}",
                dependencies=[Depends(require_permission("agent.variables.manage"))])
    async def delete_variable(name: str):
        deleted = await asyncio.to_thread(variable_repo.delete, name)
        if not deleted:
            return _err("Variável não encontrada.", status=404)
        _emit_changed("variable", name)
        return _ok({"deleted": True})

    # plano 51 (01 F3): history + rollback de variável — paridade de revert com
    # agentes/tools (a melhoria agêntica precisa reverter qualquer edição).
    @app.get("/api/ai/variables/{name}/history",
             dependencies=[Depends(require_permission("agent.variables.manage"))])
    async def variable_history(name: str):
        rows = await asyncio.to_thread(variable_repo.list_history, name)
        return _ok(rows)

    @app.get("/api/ai/variables/{name}/history/{version}",
             dependencies=[Depends(require_permission("agent.variables.manage"))])
    async def variable_snapshot(name: str, version: int):
        snap = await asyncio.to_thread(variable_repo.get_snapshot, name, version)
        if snap is None:
            return _err("Versão não encontrada.", status=404)
        return _ok(snap)

    @app.post("/api/ai/variables/{name}/rollback/{version}",
              dependencies=[Depends(require_permission("agent.variables.manage"))])
    async def rollback_variable(name: str, version: int):
        row = await asyncio.to_thread(variable_repo.rollback, name, version)
        if row is None:
            return _err("Versão não encontrada.", status=404)
        _emit_changed("variable", name)
        logger.info("AI variable rollback: %s → v%s (nova v%s)",
                    name, version, row.get("version"))
        return _ok(row)

    # ── Tools (code-in-DB) ──────────────────────────────────────────────
    @app.get("/api/ai/tools",
             dependencies=[Depends(require_permission("agent.tools.manage"))])
    async def list_ai_tools():
        rows = await asyncio.to_thread(tool_repo.list_all)
        return _ok(rows)

    @app.get("/api/ai/tools/{name}",
             dependencies=[Depends(require_permission("agent.tools.manage"))])
    async def get_ai_tool(name: str):
        row = await asyncio.to_thread(tool_repo.get, name)
        if not row:
            return _err("Tool não encontrada.", status=404)
        return _ok(row)

    @app.put("/api/ai/tools/{name}",
             dependencies=[Depends(require_permission("agent.tools.manage"))])
    async def save_ai_tool(name: str, body: dict):
        dependencies = body.get("dependencies", [])
        if dependencies is not None and not isinstance(dependencies, list):
            return _err("dependencies deve ser uma lista.")
        existing = await asyncio.to_thread(tool_repo.get, name)
        is_builtin = bool(existing and existing.get("kind") == "builtin")
        # Gate P63: code-in-DB tools are born DISABLED. A human must explicitly
        # flip ``enabled`` (and the operator must set ai_tools_code_enabled) before
        # arbitrary Python from the DB ever executes. Default False, not True.
        # Built-in (core) tools are core functionality → default enabled True.
        enabled = bool(body.get("enabled", True if is_builtin else False))
        row = await asyncio.to_thread(
            tool_repo.save, name,
            description=body.get("description", ""),
            code=body.get("code", ""),
            dependencies=dependencies or [],
            enabled=enabled,
        )
        _emit_changed("tool", name)
        # Code-in-DB needs a process restart so the installer re-materialises,
        # re-imports and re-registers the tool. Opt out with restart=false.
        if body.get("restart", True):
            schedule_restart(f"ai_tool saved: {name}")
        logger.info("AI tool saved: %s (v%s)", name, row.get("version"))
        return _ok(row)

    @app.delete("/api/ai/tools/{name}",
                dependencies=[Depends(require_permission("agent.tools.manage"))])
    async def delete_ai_tool(name: str):
        existing = await asyncio.to_thread(tool_repo.get, name)
        # Builtin deletável sem row ai_tools (ex.: seed falhou) ainda entra no
        # fluxo de tombstone — senão cairia no ramo genérico e retornaria 404
        # com a tool registrada. Row kind='code' com nome de builtin (recriação
        # pós-delete, D8) segue o fluxo genérico de tool de código.
        from agent import ai_builtin_tools
        is_builtin_delete = (
            (existing or {}).get("kind") == "builtin"
            or (existing is None and name in ai_builtin_tools.DELETABLE_BUILTINS)
        )
        if is_builtin_delete:
            # Plano 30 WS3 (D2/D8): delete REAL de builtin, restrito à allowlist
            # DELETABLE_BUILTINS. Grava o tombstone (config.deleted_builtin_tools)
            # pra tool não voltar no boot (os dois caminhos de re-seed pulam
            # nomes tombados), apaga as rows ai_tools + tool_overrides e
            # desregistra do processo vivo. Sem rota de reinstalar — recriação
            # é manual (tool code-in-DB ou editar a config no banco).
            from db.repositories import tool_override_repo
            if name not in ai_builtin_tools.DELETABLE_BUILTINS:
                return _err("Tools core não podem ser excluídas (apenas editadas ou desativadas).", status=400)
            await asyncio.to_thread(ai_builtin_tools.tombstone_builtin, name)
            await asyncio.to_thread(tool_repo.delete, name)
            await asyncio.to_thread(tool_override_repo.delete, name)
            try:
                deps.agent_handler.unregister_tool(name)
                deps.agent_handler.refresh_tool_overrides()
            except Exception as e:
                logger.warning("delete builtin '%s': unregister falhou (%s)", name, e)
            _emit_changed("tool", name)
            schedule_restart(f"builtin tool deleted: {name}")
            logger.info("Builtin tool '%s' deletada (tombstone gravado)", name)
            return _ok({"deleted": True, "tombstoned": True})
        deleted = await asyncio.to_thread(tool_repo.delete, name)
        if not deleted:
            return _err("Tool não encontrada.", status=404)
        _emit_changed("tool", name)
        schedule_restart(f"ai_tool deleted: {name}")
        return _ok({"deleted": True})

    @app.post("/api/ai/tools/{name}/reinstall",
              dependencies=[Depends(require_permission("agent.tools.manage"))])
    async def reinstall_ai_tool(name: str):
        """Re-run the installer for one tool (status->pending + restart)."""
        row = await asyncio.to_thread(tool_repo.get, name)
        if not row:
            return _err("Tool não encontrada.", status=404)
        await asyncio.to_thread(tool_repo.set_status, name, "pending", None)
        _emit_changed("tool", name)
        schedule_restart(f"ai_tool reinstall: {name}")
        return _ok({"reinstalling": name})

    @app.post("/api/ai/restart",
              dependencies=[Depends(require_permission("agent.tools.manage"))])
    async def restart_engine():
        """Restart the server so code-in-DB tool changes take effect."""
        schedule_restart("ai engine manual restart")
        return _ok({"restarting": True})
