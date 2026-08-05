"""REST do plugin (mountado em `/api/plugins/retornos`) — plano 76 F7.

Shell fino sobre `repo`/`catalog`/`rules`. Envelope `{"ok", "data"|"error"}` e gate RBAC por
rota (`plugin_permission`): `view` para leitura, `edit` para escrever configuração/retorno/mensagem,
`monitor` para operar os controles em andamento.

⚠️ Ordem importa no FastAPI: rotas FIXAS (`/configuracoes/reorder`, `/configuracoes/import`) são
declaradas ANTES das dinâmicas (`/configuracoes/{configuracao_id}`), senão `reorder` cairia no conversor
de `configuracao_id` e voltaria 422 (mesma armadilha do NestJS citada no plano).
"""

from __future__ import annotations

import json
import logging
import os
import time

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from plugins.context import audit, plugin_permission

from . import catalog, contrib, dispatcher, evalctx, media_kinds, repo, rules

logger = logging.getLogger("plugin.retornos")

router = APIRouter()

PLUGIN_ID = "retornos"


def _err(msg: str, status: int = 400):
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


def _ok(data=None):
    return {"ok": True, "data": data}


# ── Metadata (catálogo + listas reais da instância) ───────────────────────────

@router.get("/metadata", dependencies=[plugin_permission("view")])
async def get_metadata():
    """Catálogo do core + listas reais da instância + o que OUTROS plugins contribuíram.

    O externo vem sempre fresco (`force=True`): abrir a tela é o momento em que o
    operador espera ver os campos do plugin que acabou de ativar/configurar.
    """
    meta = catalog.metadata()
    meta["opcoes"] = _dynamic_options()
    externos = await contrib.asnapshot(force=True)
    meta["grupos"].extend(externos["grupos"])
    meta["campos"].extend(externos["campos"])
    for chave, lista in (externos["opcoes"] or {}).items():
        meta["opcoes"].setdefault(chave, lista)
    meta["grupo_externos_label"] = catalog.GRUPO_EXTERNOS_LABEL
    # Padrão global da pausa entre mensagens: é o que um retorno SEM pausa própria usa —
    # a tela mostra o número no placeholder do campo, em vez de dizer só "padrão".
    try:
        meta["pausa_mensagens_padrao"] = int(
            getattr(dispatcher.load_settings(), "delay_between_messages_seconds", 2) or 0)
    except Exception:  # noqa: BLE001 — settings ruins não podem derrubar a tela
        meta["pausa_mensagens_padrao"] = 2
    meta["pausa_mensagens_max"] = repo.MAX_PAUSA_MENSAGENS_SEG
    return _ok(meta)


def _dynamic_options() -> dict:
    """Listas reais lidas dos repos do core (sem rede — tudo local)."""
    out: dict[str, list] = {"users": [], "conv_labels": [],
                            "contact_tags": [], "channels": [], "ai_agents": []}
    try:
        from db.repositories import user_repo
        out["users"] = [{"value": str(u["id"]), "label": u.get("name") or u.get("email") or ""}
                        for u in (user_repo.list_all() or [])]
    except Exception:  # noqa: BLE001
        pass
    try:
        from db.repositories import conversation_label_repo
        out["conv_labels"] = [{"value": l["name"], "label": l["name"]}
                              for l in (conversation_label_repo.get_all() or [])]
    except Exception:  # noqa: BLE001
        pass
    try:
        from db.repositories import tag_repo
        out["contact_tags"] = [{"value": n, "label": n}
                               for n in sorted((tag_repo.get_all() or {}).keys())]
    except Exception:  # noqa: BLE001
        pass
    try:
        from db.repositories import channel_repo
        out["channels"] = [{"value": c["id"],
                            "label": c.get("display_name") or c["id"]}
                           for c in (channel_repo.list_all() or [])]
    except Exception:  # noqa: BLE001
        pass
    try:
        from db.repositories import agent_repo
        out["ai_agents"] = [{"value": a["agent_key"],
                             "label": a.get("display_name") or a["agent_key"]}
                            for a in (agent_repo.list_all() or [])]
    except Exception:  # noqa: BLE001
        pass
    return out


# ── Configurações ────────────────────────────────────────────────────────────────────

@router.get("/configuracoes", dependencies=[plugin_permission("view")])
async def list_configuracoes():
    configuracoes = repo.list_configuracoes()
    for r in configuracoes:
        retornos = repo.list_retornos(r["id"])
        r["n_retornos"] = len(retornos)
        r["n_mensagens"] = sum(len(repo.list_mensagens(p["id"])) for p in retornos)
    return _ok(configuracoes)


@router.post("/configuracoes", dependencies=[plugin_permission("edit")])
async def create_configuracao(body: dict):
    nome = (body or {}).get("nome") or "Nova configuração"
    configuracao = repo.create_configuracao({**(body or {}), "nome": nome})
    audit(PLUGIN_ID, "configuracao.create", after={"id": configuracao.get("id"), "nome": nome})
    return _ok(configuracao)


@router.post("/configuracoes/reorder", dependencies=[plugin_permission("edit")])
async def reorder_configuracoes(body: dict):
    ids = (body or {}).get("ids") or []
    repo.reorder_configuracoes(ids)
    audit(PLUGIN_ID, "configuracao.reorder", after={"ids": ids})
    return _ok(repo.list_configuracoes())


@router.post("/configuracoes/import", dependencies=[plugin_permission("edit")])
async def import_configuracao(body: dict):
    """Importa uma configuração exportada daqui (ou uma árvore compatível com o Nexus)."""
    payload = (body or {}).get("configuracao") or body or {}
    if not isinstance(payload, dict):
        return _err("JSON inválido.")
    campos = {k: v for k, v in payload.items() if k in repo.CAMPOS_CONFIGURACAO}
    campos["nome"] = (campos.get("nome") or "Configuração importada")
    campos["ativo"] = False  # importada nasce DESATIVADA (revisar antes de ligar)
    nova = repo.create_configuracao(campos)
    for retorno in (payload.get("retornos") or []):
        p_campos = {k: v for k, v in retorno.items() if k in repo.CAMPOS_RETORNO}
        novo_retorno = repo.create_retorno(nova["id"], p_campos)
        for msg in (retorno.get("mensagens") or []):
            m_campos = {k: v for k, v in msg.items() if k in repo.CAMPOS_MENSAGEM}
            repo.create_mensagem(novo_retorno["id"], m_campos)
    audit(PLUGIN_ID, "configuracao.import", after={"id": nova.get("id"), "nome": nova.get("nome")})
    return _ok(repo.configuracao_full(nova["id"]))


@router.get("/configuracoes/{configuracao_id}", dependencies=[plugin_permission("view")])
async def get_configuracao(configuracao_id: int):
    configuracao = repo.configuracao_full(configuracao_id)
    if not configuracao:
        return _err("Configuração não encontrada.", status=404)
    return _ok(configuracao)


@router.put("/configuracoes/{configuracao_id}", dependencies=[plugin_permission("edit")])
async def update_configuracao(configuracao_id: int, body: dict):
    antes = repo.get_configuracao(configuracao_id)
    if not antes:
        return _err("Configuração não encontrada.", status=404)
    depois = repo.update_configuracao(configuracao_id, body or {})
    audit(PLUGIN_ID, "configuracao.update",
          before={k: antes.get(k) for k in (body or {}) if k in repo.CAMPOS_CONFIGURACAO},
          after={k: (depois or {}).get(k) for k in (body or {}) if k in repo.CAMPOS_CONFIGURACAO})
    return _ok(depois)


@router.delete("/configuracoes/{configuracao_id}", dependencies=[plugin_permission("edit")])
async def delete_configuracao(configuracao_id: int):
    antes = repo.get_configuracao(configuracao_id)
    if not antes:
        return _err("Configuração não encontrada.", status=404)
    repo.delete_configuracao(configuracao_id)
    audit(PLUGIN_ID, "configuracao.delete", before={"id": configuracao_id, "nome": antes.get("nome")})
    return _ok({"id": configuracao_id})


@router.post("/configuracoes/{configuracao_id}/duplicate", dependencies=[plugin_permission("edit")])
async def duplicate_configuracao(configuracao_id: int):
    original = repo.configuracao_full(configuracao_id)
    if not original:
        return _err("Configuração não encontrada.", status=404)
    campos = {k: original.get(k) for k in repo.CAMPOS_CONFIGURACAO}
    campos["nome"] = f"{original.get('nome') or 'Configuração'} (cópia)"
    campos["ativo"] = False
    campos.pop("posicao", None)
    nova = repo.create_configuracao(campos)
    for retorno in original.get("retornos") or []:
        novo_retorno = repo.create_retorno(nova["id"], {k: retorno.get(k) for k in repo.CAMPOS_RETORNO})
        for msg in retorno.get("mensagens") or []:
            repo.create_mensagem(novo_retorno["id"],
                                 {k: msg.get(k) for k in repo.CAMPOS_MENSAGEM})
    audit(PLUGIN_ID, "configuracao.duplicate",
          after={"id": nova.get("id"), "origem": configuracao_id})
    return _ok(repo.configuracao_full(nova["id"]))


@router.post("/configuracoes/{configuracao_id}/import", dependencies=[plugin_permission("edit")])
async def import_para_configuracao(configuracao_id: int, body: dict):
    """Substitui ESTA configuração pelo JSON (mesmo formato do `/export`).

    Destrutivo de propósito: é o par do "Exportar JSON" no cabeçalho da configuração aberta.
    `posicao` e `ativo` continuam sendo da instância (ver `repo.replace_configuracao`).
    """
    payload = (body or {}).get("configuracao") or body or {}
    if not isinstance(payload, dict):
        return _err("JSON inválido.")
    if "retornos" in payload and not isinstance(payload["retornos"], list):
        return _err("JSON inválido: 'retornos' precisa ser uma lista.")
    if not any(k in payload for k in (*repo.CAMPOS_CONFIGURACAO, "retornos")):
        return _err("JSON inválido: não parece uma configuração exportada do Retorno Automático.")
    antes = repo.configuracao_full(configuracao_id)
    if not antes:
        return _err("Configuração não encontrada.", status=404)
    depois = repo.replace_configuracao(configuracao_id, payload)
    if not depois:
        return _err("Configuração não encontrada.", status=404)
    audit(PLUGIN_ID, "configuracao.replace",
          before={"id": configuracao_id, "nome": antes.get("nome"),
                  "n_retornos": len(antes.get("retornos") or [])},
          after={"id": configuracao_id, "nome": depois.get("nome"),
                 "n_retornos": len(depois.get("retornos") or [])})
    return _ok(depois)


@router.get("/configuracoes/{configuracao_id}/export", dependencies=[plugin_permission("view")])
async def export_configuracao(configuracao_id: int):
    configuracao = repo.configuracao_full(configuracao_id)
    if not configuracao:
        return _err("Configuração não encontrada.", status=404)
    for chave in ("id", "created_at", "updated_at"):
        configuracao.pop(chave, None)
    for retorno in configuracao.get("retornos") or []:
        for chave in ("id", "configuracao_id", "created_at", "updated_at", "proxima_mensagem_index"):
            retorno.pop(chave, None)
        for msg in retorno.get("mensagens") or []:
            for chave in ("id", "retorno_id", "created_at", "updated_at"):
                msg.pop(chave, None)
    return _ok(configuracao)


# ── Retornos ────────────────────────────────────────────────────────────────────

@router.post("/configuracoes/{configuracao_id}/retornos/reorder", dependencies=[plugin_permission("edit")])
async def reorder_retornos(configuracao_id: int, body: dict):
    repo.reorder_retornos(configuracao_id, (body or {}).get("ids") or [])
    return _ok(repo.list_retornos(configuracao_id))


@router.post("/configuracoes/{configuracao_id}/retornos", dependencies=[plugin_permission("edit")])
async def create_retorno(configuracao_id: int, body: dict):
    if not repo.get_configuracao(configuracao_id):
        return _err("Configuração não encontrada.", status=404)
    retorno = repo.create_retorno(configuracao_id, body or {})
    audit(PLUGIN_ID, "retorno.create", after={"configuracao_id": configuracao_id, "id": retorno.get("id")})
    return _ok(retorno)


@router.put("/retornos/{retorno_id}", dependencies=[plugin_permission("edit")])
async def update_retorno(retorno_id: int, body: dict):
    if not repo.get_retorno(retorno_id):
        return _err("Retorno não encontrado.", status=404)
    retorno = repo.update_retorno(retorno_id, body or {})
    return _ok(retorno)


@router.delete("/retornos/{retorno_id}", dependencies=[plugin_permission("edit")])
async def delete_retorno(retorno_id: int):
    if not repo.delete_retorno(retorno_id):
        return _err("Retorno não encontrado.", status=404)
    audit(PLUGIN_ID, "retorno.delete", before={"id": retorno_id})
    return _ok({"id": retorno_id})


# ── Mensagens ─────────────────────────────────────────────────────────────────

@router.post("/retornos/{retorno_id}/mensagens/reorder", dependencies=[plugin_permission("edit")])
async def reorder_mensagens(retorno_id: int, body: dict):
    repo.reorder_mensagens(retorno_id, (body or {}).get("ids") or [])
    return _ok(repo.list_mensagens(retorno_id))


def _anexo_incompativel(atual: dict | None, body: dict) -> str | None:
    """O anexo EFETIVO (o que ficaria salvo) casa com o tipo EFETIVO da mensagem?

    Checa o merge (`atual` + o que o PUT manda), e não só o corpo: trocar SÓ o tipo de uma
    mensagem que já tem arquivo é justamente o jeito mais fácil de acabar com um `.mp4`
    numa mensagem de "Imagem".
    """
    tipo = str(body.get("tipo", (atual or {}).get("tipo") or "")).strip()
    nome = body.get("file_name", (atual or {}).get("file_name"))
    caminho = body.get("media_path", (atual or {}).get("media_path"))
    url = body.get("media_url", (atual or {}).get("media_url"))
    # A URL só é julgada quando termina em extensão conhecida (link sem extensão passa —
    # não dá pra saber o que tem do outro lado sem baixar).
    for candidato in (nome, caminho, url):
        problema = media_kinds.erro_de_incompatibilidade(tipo, candidato)
        if problema:
            return problema
    return None


@router.post("/retornos/{retorno_id}/mensagens", dependencies=[plugin_permission("edit")])
async def create_mensagem(retorno_id: int, body: dict):
    if not repo.get_retorno(retorno_id):
        return _err("Retorno não encontrado.", status=404)
    problema = _anexo_incompativel(None, body or {})
    if problema:
        return _err(problema, status=415)
    return _ok(repo.create_mensagem(retorno_id, body or {}))


@router.put("/mensagens/{msg_id}", dependencies=[plugin_permission("edit")])
async def update_mensagem(msg_id: int, body: dict):
    atual = repo.get_mensagem(msg_id)
    if not atual:
        return _err("Mensagem não encontrada.", status=404)
    problema = _anexo_incompativel(atual, body or {})
    if problema:
        return _err(problema, status=415)
    return _ok(repo.update_mensagem(msg_id, body or {}))


@router.delete("/mensagens/{msg_id}", dependencies=[plugin_permission("edit")])
async def delete_mensagem(msg_id: int):
    if not repo.delete_mensagem(msg_id):
        return _err("Mensagem não encontrada.", status=404)
    return _ok({"id": msg_id})


# ── Upload de mídia (vai para statics/outbox do core) ─────────────────────────

@router.post("/upload", dependencies=[plugin_permission("edit")])
async def upload_media(file: UploadFile = File(...), tipo: str = Form("")):
    """Grava o anexo em `statics/outbox/` e devolve o caminho relativo a persistir.

    `tipo` é o tipo da MENSAGEM que vai carregar o anexo: com ele, um vídeo escolhido numa
    mensagem de "Imagem" é recusado AQUI, e não no disparo (onde o provedor recusa e o
    cliente fica sem o retorno). Vazio = sem checagem (compat com chamada antiga).
    """
    from plugins.context import get_deps
    from server.upload_names import unique_media_name

    problema = media_kinds.erro_de_incompatibilidade(
        (tipo or "").strip(), file.filename, file.content_type)
    if problema:
        return _err(problema, status=415)

    deps = get_deps()
    destino_dir = getattr(deps, "statics_outbox_dir", None) if deps else None
    if destino_dir is None:
        destino_dir = os.path.join(os.getcwd(), "statics", "outbox")
    os.makedirs(str(destino_dir), exist_ok=True)
    nome = unique_media_name(file.content_type, file.filename)
    caminho = os.path.join(str(destino_dir), nome)
    conteudo = await file.read()
    if not conteudo:
        return _err("Arquivo vazio.")
    with open(caminho, "wb") as fh:
        fh.write(conteudo)
    return _ok({"media_path": f"statics/outbox/{nome}",
                "url": f"/statics/outbox/{nome}",
                "file_name": file.filename or nome})


# ── Preview (avalia uma árvore contra uma conversa real) ──────────────────────

@router.post("/preview", dependencies=[plugin_permission("view")])
async def preview(body: dict):
    """Roda o motor do BACKEND sobre uma conversa real — confere o preview da UI."""
    body = body or {}
    conv_id = body.get("conversation_id")
    arvore = body.get("arvore") or {"regras": []}
    if isinstance(arvore, str):
        try:
            arvore = json.loads(arvore)
        except (TypeError, ValueError):
            return _err("Árvore inválida.")
    configuracao = {"tz_offset_hours": body.get("tz_offset_hours", -3)}
    if not conv_id:
        return _err("Informe a conversa (conversation_id) para o preview.")
    conv, contact = evalctx.load_target(conversation_id=int(conv_id))
    if not conv:
        return _err("Conversa não encontrada.", status=404)
    ctrl = repo.get_controle_by_conversation(int(conv_id))
    # Estamos na thread do event loop: o seam externo tem de ser resolvido com `await`
    # (o caminho síncrono de `build_ctx` seria inerte aqui e o preview mentiria).
    extra = await contrib.acontexto(conv=conv, contact=contact, configuracao=configuracao,
                                    controle=ctrl)
    ctx = evalctx.build_ctx(conv=conv, contact=contact, configuracao=configuracao,
                            controle=ctrl, extra=extra)
    resultado = rules.avaliar(arvore, ctx)
    # O contexto vai COMPLETO (inclusive as chaves reservadas `__*`): a UI reavalia as
    # árvores localmente com `static/rules.js` a cada edição, sem novo round-trip.
    return _ok({"resultado": bool(resultado), "contexto": ctx,
                "conversa": {"id": conv.get("id"), "display_id": conv.get("display_id"),
                             "contato": conv.get("contact_name") or conv.get("contact_phone"),
                             "canal": conv.get("channel_id")},
                "proximo_instante": rules.proximo_instante(arvore, ctx)})


# ── Monitor ───────────────────────────────────────────────────────────────────

@router.get("/monitor/controles", dependencies=[plugin_permission("view")])
async def list_controles(status: str | None = None, configuracao_id: int | None = None,
                         disparos: int | None = None,
                         next_from: float | None = None, next_to: float | None = None,
                         limit: int = 50, offset: int = 0):
    """Página da tabela do monitor.

    Devolve `{items, total, limit, offset}` — mesmo contrato do `/monitor/logs`: a tela
    precisa do TOTAL para paginar, e ele sai de `repo.count_controles` com a MESMA
    cláusula da página.

    `disparos` é o número EXATO digitado no filtro (`0` inclusive). `next_from`/`next_to`
    são epochs — quem converte o DIA escolhido no calendário é o navegador, no fuso dele,
    igual ao resto do painel.
    """
    limit = max(1, min(int(limit or 50), 1000))
    offset = max(0, int(offset or 0))
    filtros = {"status": status, "configuracao_id": configuracao_id, "disparos": disparos,
               "next_from": next_from, "next_to": next_to}
    return _ok({"items": repo.list_controles(**filtros, limit=limit, offset=offset),
                "total": repo.count_controles(**filtros),
                "limit": limit, "offset": offset})


@router.get("/monitor/filtros", dependencies=[plugin_permission("view")])
async def monitor_filtros():
    """Vocabulário do select de configuração do monitor."""
    return _ok({
        "configuracoes": [{"id": c["id"], "nome": c.get("nome") or "",
                           "ativo": c.get("ativo"), "posicao": c.get("posicao")}
                          for c in repo.list_configuracoes()],
    })


@router.get("/monitor/stats", dependencies=[plugin_permission("view")])
async def monitor_stats():
    return _ok(repo.stats())


@router.get("/monitor/logs", dependencies=[plugin_permission("view")])
async def monitor_logs(conversation_id: int | None = None, configuracao_id: int | None = None,
                       evento: str | None = None, limit: int = 50, offset: int = 0):
    """Página do log de eventos (aba **Eventos**).

    Devolve `{items, total, limit, offset, eventos}` — a tela precisa do TOTAL para paginar
    (contar sobre a página daria "1 de 50" para sempre) e da lista de tipos para o filtro.
    """
    limit = max(1, min(int(limit or 50), 1000))
    offset = max(0, int(offset or 0))
    filtros = {"conversation_id": conversation_id, "configuracao_id": configuracao_id,
               "evento": (evento or None)}
    return _ok({"items": repo.list_logs(**filtros, limit=limit, offset=offset),
                "total": repo.count_logs(**filtros),
                "limit": limit, "offset": offset,
                "eventos": repo.log_eventos()})


@router.post("/monitor/controles/{controle_id}/cancel",
             dependencies=[plugin_permission("monitor")])
async def cancel_controle(controle_id: int):
    ctrl = repo.get_controle(controle_id)
    if not ctrl:
        return _err("Agendamento não encontrado.", status=404)
    repo.set_status(controle_id, repo.STATUS_CANCELLED, last_error="cancelado manualmente")
    repo.add_log("cancelled", configuracao_id=ctrl.get("configuracao_id"), controle_id=controle_id,
                 conversation_id=ctrl.get("conversation_id"),
                 data={"motivo": "manual"})
    audit(PLUGIN_ID, "controle.cancel",
          before={"id": controle_id, "status": ctrl.get("status")},
          after={"status": repo.STATUS_CANCELLED})
    return _ok(repo.get_controle(controle_id))


@router.post("/monitor/controles/{controle_id}/reset",
             dependencies=[plugin_permission("monitor")])
async def reset_controle(controle_id: int):
    """Volta o agendamento ao primeiro retorno e reagenda (zera disparos e tentativas)."""
    ctrl = repo.get_controle(controle_id)
    if not ctrl:
        return _err("Agendamento não encontrado.", status=404)
    configuracao = repo.get_configuracao(ctrl.get("configuracao_id"))
    if not configuracao:
        return _err("A configuração deste agendamento não existe mais.", status=409)
    conv, contact = evalctx.load_target(conversation_id=int(ctrl["conversation_id"]))
    if not conv:
        return _err("A conversa deste agendamento não existe mais.", status=409)
    now = time.time()
    retorno = repo.first_retorno(configuracao["id"])
    ctx = evalctx.build_ctx(conv=conv, contact=contact, configuracao=configuracao, controle=ctrl, now=now)
    next_at = (dispatcher.proximo_para_retorno(retorno, ctx, now=now, base=now)
               if retorno else now + 60.0)
    atualizado = repo.update_controle(
        controle_id, status=repo.STATUS_ACTIVE, retorno_atual_id=(retorno or {}).get("id"),
        retorno_started_at=now, tentativas_retorno=0, disparos_enviados=0,
        next_at=next_at, processing=0, processing_since=None, last_error=None)
    repo.add_log("reset", configuracao_id=configuracao.get("id"), controle_id=controle_id,
                 conversation_id=ctrl.get("conversation_id"),
                 data={"motivo": "manual", "next_at": next_at})
    audit(PLUGIN_ID, "controle.reset", after={"id": controle_id, "next_at": next_at})
    return _ok(atualizado)
