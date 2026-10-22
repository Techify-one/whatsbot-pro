"""Tela Arquivos (plano 179): ver o que ocupa espaço e apagar a mídia das conversas.

Toda regra de filesystem mora em :mod:`server.file_manager` (allowlist de duas pastas,
ids opacos revalidados a cada operação). Aqui ficam o que exige banco, escopo e plugins:

* **Escopo (D2)** — LISTAGEM e PRÉVIA respeitam o ``ConversationAccessScope``: quem não
  vê tudo só recebe arquivos ligados a mensagens de conversas que já pode abrir, e o id
  opaco NÃO é segredo (a autorização é refeita pelo caminho). A EXCLUSÃO não é recortada
  por caixa de entrada: quem tem ``files.manage`` apaga qualquer arquivo (decisão P1).
* **Arquivo de plugin (D3)** — uma coluna ``media_path`` em ``plugin_*`` declara o arquivo
  como DO plugin: ele sai das conversas mas NUNCA do disco. Fail-closed: qualquer erro na
  descoberta ⇒ nada de ``statics/outbox`` é apagado nessa chamada.
* **Auditoria** — uma linha por chamada, só contagens e nomes em disco (conversa nunca
  entra na trilha).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict

from fastapi import Depends, Request
from fastapi.responses import FileResponse, Response

from db import audit_actions
from db.repositories import media_files_repo, message_repo, plugin_media_repo
from db.search.contact_search import fold, match_snippet
from server import audit_listener
from server import file_manager as fm
from server.authz import conversation_access_scope
from server.avatars import avatar_version
from server.deps import install_exception_handlers, require_permission
from server.helpers import _err, _ok

logger = logging.getLogger(__name__)

PERMISSION = "files.manage"
CHAT_TYPES = ("individual", "group")
#: Itens por evento ``message_media_updated`` (o painel aplica por lote).
WS_MESSAGES_PER_EVENT = 200
#: Nomes em disco que entram numa linha de auditoria.
AUDIT_MAX_PATHS = 100


# ── nomes que o remetente deu ───────────────────────────────────────────────

def _sender_name(media_name, media_type, content_head: str) -> str:
    """O nome que o remetente deu ao arquivo: o gravado em ``media_name`` e, para
    documento anterior à coluna, o de dentro de ``[Documento recebido: x.pdf]``."""
    name = fm.clean_name(media_name)
    if not name and media_type == "document":
        name = fm.name_from_content(content_head)
    return name


def _sender_names(paths: list[str]) -> dict[str, str]:
    """``media_path`` → nome dado pelo remetente, onde se sabe. Foto/vídeo/áudio não têm
    nome próprio no WhatsApp e ficam com o nome em disco."""
    names: dict[str, str] = {}
    for row in media_files_repo.sender_name_rows(paths):
        path = row["media_path"]
        if path in names:
            continue
        name = _sender_name(row["media_name"], row["media_type"], row["content_head"] or "")
        if name:
            names[path] = name
    return names


# ── seletor (filtros que precisam do banco) ─────────────────────────────────

def _build_selector(prefixes: list[str], scope, *, q: str, contact_id, contact_text: str,
                    inbox_id, chat_type: str, orphans: bool):
    """Os filtros que precisam do banco, num callable só para ``list_entries``.

    Devolve ``None`` quando nenhum filtro está ligado (listagem sem custo extra).
    O callable devolve ``None`` para descartar o arquivo, ou os campos extras da linha
    (``match`` = ``name``/``text`` e, para texto, ``snippet``).
    """
    q = (q or "").strip()
    contact_text = (contact_text or "").strip()
    chat_type = chat_type if chat_type in CHAT_TYPES else ""
    unscoped = scope is None or scope.is_unrestricted

    allowed: set[str] | None = None
    if media_files_repo.needs_db_filter(
            scope, contact_id=contact_id, contact_text=contact_text,
            inbox_id=inbox_id, chat_type=chat_type):
        allowed = media_files_repo.allowed_paths(
            prefixes, scope=scope, contact_id=contact_id, contact_text=contact_text,
            inbox_id=inbox_id, chat_type=chat_type)

    referenced: set[str] | None = None
    if orphans:
        # "Sem conversa" = nenhuma mensagem aponta para o arquivo (D2: só quem vê TODAS as
        # conversas). Quem tem escopo não tem o que listar aqui — arquivo sem conversa
        # nunca aparece para ele —, então o resultado é vazio por construção.
        if not unscoped:
            return lambda entry, db_path: None
        referenced = media_files_repo.all_paths(prefixes)

    folded_q = fold(q)
    name_hits: set[str] = set()
    text_hits: dict[str, str] = {}
    if folded_q:
        for row in media_files_repo.text_rows(
                prefixes, q, scope=scope, contact_id=contact_id, contact_text=contact_text,
                inbox_id=inbox_id, chat_type=chat_type):
            path = row["media_path"]
            sender = _sender_name(row["media_name"], row["media_type"], row["content"] or "")
            shown = fm.final_name(path.rsplit("/", 1)[-1], sender)
            if folded_q in fold(shown):
                name_hits.add(path)
            text = fm.searchable_text(row["content"] or "")
            if text and folded_q in fold(text):
                text_hits.setdefault(path, match_snippet(text, folded_q))
                continue
            caption = (row.get("media_caption") or "").strip()
            if caption and folded_q in fold(caption):
                text_hits.setdefault(path, match_snippet(caption, folded_q))

    if allowed is None and referenced is None and not folded_q:
        return None

    def select(entry: fm.Entry, db_path: str) -> dict | None:
        if allowed is not None and db_path not in allowed:
            return None
        if referenced is not None and db_path in referenced:
            return None
        if not folded_q:
            return {}
        if folded_q in fold(entry.rel.rsplit("/", 1)[-1]) or db_path in name_hits:
            return {"match": "name"}
        if db_path in text_hits:
            return {"match": "text", "snippet": text_hits[db_path]}
        return None

    return select


def _prefixes(manager: fm.FileManager, roots=None) -> list[str]:
    return [f"statics/{fm.ROOTS[r]}/" for r in (roots or fm.ROOTS)]


# ── rotas ───────────────────────────────────────────────────────────────────

def register_routes(app, deps):
    install_exception_handlers(app)
    settings = deps.settings
    ws_manager = deps.ws_manager
    gate = [Depends(require_permission(PERMISSION))]

    def _manager() -> fm.FileManager:
        # Lido a cada request (não capturado): os testes apontam ``deps.file_manager``
        # para um ``tmp_path`` e NUNCA para o ``statics/`` real.
        return deps.file_manager

    def _plugin_name(plugin_id: str | None) -> str:
        registry = getattr(deps, "plugins_registry", None)
        loaded = getattr(registry, "loaded", {}) if registry is not None else {}
        entry = loaded.get(plugin_id) if plugin_id else None
        manifest = getattr(entry, "manifest", None)
        return (getattr(manifest, "name", None) or plugin_id or "desconhecido")

    def _decorate(manager: fm.FileManager, root: str, items: list[dict], scope) -> None:
        """Acrescenta "de onde veio" e "de quem é" às linhas de UMA página."""
        paths = [manager.db_path(root, r["rel"]) for r in items]
        origin = media_files_repo.origins(paths, scope)
        try:
            owners = plugin_media_repo.owned_paths(paths)
        except plugin_media_repo.PluginMediaError:
            logger.warning("[Files] não consegui descobrir arquivos de plugin", exc_info=True)
            owners = {}
        for r, path in zip(items, paths):
            info = origin.get(path)
            r["orphan"] = info is None
            if info:
                label = ((info.get("group_name") if info.get("is_group") else info.get("name")) or "")
                r["origin"] = {
                    "conversation_id": info.get("conversation_id"),
                    "message_id": info["message_id"],
                    "contact_id": info["contact_id"],
                    "phone": info["phone"],
                    "name": label.lstrip("~").strip() or info["phone"],
                    "is_group": bool(info.get("is_group")),
                    "avatar_v": avatar_version(settings, info["phone"]),
                    "msg_ts": info["msg_ts"],
                    "channel_name": info.get("inbox_name") or None,
                }
            if path in owners:
                pid = owners[path]
                r["plugin"] = {"id": pid, "name": _plugin_name(pid)}

    @app.get("/api/files/usage", dependencies=gate)
    async def files_usage(request: Request, refresh: bool = False):
        manager = _manager()
        scope = await asyncio.to_thread(conversation_access_scope, request)
        scoped = not scope.is_unrestricted
        if refresh:
            await asyncio.to_thread(manager.invalidate)

        def work() -> dict:
            usage = manager.usage(include_system=not scoped)
            out: dict = {"disk": usage["disk"], "scoped": scoped}
            if not scoped:
                out["roots"] = usage["roots"]
                out["system_bytes"] = usage["system_bytes"]
                return out
            # Quem tem escopo vê o disco (infraestrutura) e SÓ o total do que pode abrir.
            allowed = media_files_repo.allowed_paths(_prefixes(manager), scope=scope)
            files = size = 0
            for root in fm.ROOTS:
                for e in manager.scan(root):
                    if manager.db_path(root, e.rel) in allowed:
                        files += 1
                        size += e.size
            out["visible"] = {"files": files, "bytes": size}
            return out

        return _ok(await asyncio.to_thread(work))

    @app.get("/api/files/contacts", dependencies=gate)
    async def files_contacts(request: Request, q: str = "", limit: int = 8):
        """Contatos e grupos que ``q`` nomeia (com foto e número), para o seletor
        "Contato ou grupo". Só leitura — nada é criado."""
        scope = await asyncio.to_thread(conversation_access_scope, request)
        rows = await asyncio.to_thread(media_files_repo.suggest_contacts, q, limit, scope)
        for r in rows:
            r["avatar_v"] = avatar_version(settings, r["phone"])
        return _ok(rows)

    @app.get("/api/files/inboxes", dependencies=gate)
    async def files_inboxes(request: Request):
        """Canais para o filtro "Canal", já recortados pelo escopo. A tela não tem
        ``inbox.manage``, então não pode usar ``GET /api/inboxes``."""
        scope = await asyncio.to_thread(conversation_access_scope, request)
        return _ok(await asyncio.to_thread(media_files_repo.visible_inboxes, scope))

    @app.get("/api/files", dependencies=gate)
    async def files_list(
        request: Request,
        root: str = "received", sort: str = "date", order: str = "desc", kind: str = "",
        orphans: bool = False, offset: int = 0, limit: int = 100,
        q: str = "", contact_id: int | None = None, contact: str = "",
        inbox_id: int | None = None, chat_type: str = "",
        date_from: float | None = None, date_to: float | None = None,
    ):
        """Uma página (plana) da pasta ``root``.

        ``date_from``/``date_to`` são epoch em segundos (o navegador converte o dia
        escolhido na PRÓPRIA meia-noite) e ``date_to`` é exclusivo. ``contact_id`` é o
        contato exato escolhido no seletor e vence o ``contact`` digitado.
        """
        manager = _manager()
        if root not in fm.ROOTS:
            return _err("Pasta inválida.", status=400)
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        scope = await asyncio.to_thread(conversation_access_scope, request)

        def work() -> dict:
            selector = _build_selector(
                _prefixes(manager, [root]), scope, q=q, contact_id=contact_id,
                contact_text=contact, inbox_id=inbox_id, chat_type=chat_type,
                orphans=orphans)
            page = manager.list_entries(
                root, sort=sort, order=order, kind=kind, date_from=date_from,
                date_to=date_to, selector=selector, offset=offset, limit=limit,
                name_resolver=_sender_names)
            _decorate(manager, root, page["items"], scope)
            return page

        try:
            page = await asyncio.to_thread(work)
        except fm.FileAccessError:
            return _err("Pasta inválida.", status=400)
        return _ok(page)

    @app.get("/api/files/{file_id}/preview", dependencies=gate)
    async def files_preview(file_id: str, request: Request, download: bool = False):
        manager = _manager()
        try:
            root, rel, target, is_dir = await asyncio.to_thread(manager.resolve, file_id)
        except fm.FileAccessError:
            return Response(status_code=404)
        if is_dir:
            return Response(status_code=404)
        db_path = manager.db_path(root, rel)
        # O id não é segredo: a autorização é refeita pelo CAMINHO. Arquivo fora do que
        # a pessoa abre responde 404, igual a um id inexistente (nunca 403).
        scope = await asyncio.to_thread(conversation_access_scope, request)
        if not await asyncio.to_thread(media_files_repo.visible_to, scope, db_path):
            return Response(status_code=404)
        # ``None`` = a tela não mostra inline (HTML/SVG nunca, para o conteúdo ativo que
        # um contato mandou não rodar na origem do app).
        shown = await asyncio.to_thread(fm.preview_mime, target)
        inline = not download and shown is not None
        names = await asyncio.to_thread(_sender_names, [db_path])
        download_name = fm.final_name(target.name, names.get(db_path))
        return FileResponse(
            str(target), media_type=shown or "application/octet-stream",
            filename=download_name,
            content_disposition_type="inline" if inline else "attachment",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
        )

    @app.post("/api/files/delete", dependencies=gate)
    async def files_delete(body: dict):
        ids = body.get("ids") if isinstance(body, dict) else None
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            return _err("Selecione ao menos um item.", status=400)
        manager = _manager()

        def work() -> dict:
            files, refused = manager.expand(ids)
            entries = [(root, rel) for root, rel, _ in files]
            db_paths = [manager.db_path(root, rel) for root, rel in entries]

            # Quais arquivos são de plugin? Fail-closed: erro ⇒ tudo de ``outbox`` fica.
            fail_closed = False
            try:
                owners = plugin_media_repo.owned_paths(db_paths)
            except plugin_media_repo.PluginMediaError:
                logger.warning("[Files] falha ao descobrir arquivos de plugin — guardando "
                               "todo arquivo de statics/outbox", exc_info=True)
                owners, fail_closed = {}, True

            kept_plugin = kept_unknown = 0
            to_unlink: list[tuple[str, str]] = []
            kept_paths: list[str] = []
            for (root, rel), path in zip(entries, db_paths):
                if path in owners:
                    kept_plugin += 1
                    kept_paths.append(path)
                elif fail_closed and root == "sent":
                    kept_unknown += 1
                    kept_paths.append(path)
                else:
                    to_unlink.append((root, rel))

            result = manager.delete_files(to_unlink)
            result.failed += refused

            # As mensagens saem do arquivo nos dois casos: removido do disco ou só
            # tirado das conversas (arquivo de plugin). Falha de unlink NÃO marca.
            affected_paths = list(result.removed_paths) + kept_paths
            rows = media_files_repo.messages_for_paths(affected_paths)
            message_ids = [r["id"] for r in rows]
            marked = message_repo.mark_media_removed(message_ids) if message_ids else 0
            by_conv: dict[int, list[int]] = defaultdict(list)
            for r in rows:
                if r.get("conversation_id") is not None:
                    by_conv[int(r["conversation_id"])].append(int(r["id"]))
            return {
                "result": result, "marked": marked, "by_conv": by_conv,
                "kept_plugin": kept_plugin, "kept_unknown": kept_unknown,
                "kept_paths": kept_paths,
            }

        try:
            outcome = await asyncio.to_thread(work)
        except fm.FileAccessError:
            return _err("Itens demais de uma vez. Apague em lotes menores.", status=400)

        result: fm.DeleteResult = outcome["result"]
        payload = {
            "files": result.files,
            "bytes": result.bytes,
            "failed": result.failed,
            "messages": outcome["marked"],
            "kept_plugin": outcome["kept_plugin"],
            "kept_unknown": outcome["kept_unknown"],
        }

        # Auditoria: UMA linha por chamada. Só contagens e o nome em disco — nunca o
        # nome dado pelo remetente, a legenda, o texto da mensagem nem o telefone.
        on_disk = [p.rsplit("/", 1)[-1] for p in (result.removed_paths + outcome["kept_paths"])]
        roots = {("received" if p.startswith("statics/media/") else "sent")
                 for p in (result.removed_paths + outcome["kept_paths"])}
        await asyncio.to_thread(
            audit_listener.record,
            action=audit_actions.AuditAction.FILES_DELETE,
            resource_type=audit_actions.ResourceType.FILE,
            after={**payload, "root": roots.pop() if len(roots) == 1 else "mixed",
                   "paths": on_disk[:AUDIT_MAX_PATHS]})

        # Tempo real: um evento POR CONVERSA (roteado só a quem a vê) e um de contagens
        # (sem conversation_id) para a tela Arquivos de outra aba recarregar.
        for conv_id, msg_ids in outcome["by_conv"].items():
            for i in range(0, len(msg_ids), WS_MESSAGES_PER_EVENT):
                await ws_manager.broadcast("message_media_updated", {
                    "conversation_id": conv_id,
                    "messages": [{"_id": mid, "media_state": "removed", "media_path": None}
                                 for mid in msg_ids[i:i + WS_MESSAGES_PER_EVENT]],
                })
        await ws_manager.broadcast("storage_changed", {
            "files": result.files, "bytes": result.bytes, "messages": outcome["marked"]})
        logger.info("[Files] delete: %d arquivos (%d bytes), %d mantidos de plugin, "
                    "%d falhas", result.files, result.bytes, outcome["kept_plugin"], result.failed)
        return _ok(payload)
