"""Núcleo do plugin ``melhorias`` (auto-contido, imports só absolutos).

Concentra TODA a lógica de dados + regras; ``routes.py`` é casca fina. Importa
apenas ``plugins.context`` / ``db.repositories`` / SQLAlchemy / ``. generation``,
então é importável standalone nos testes.

Fluxo (decisão do produto — geração NA APROVAÇÃO):
- ``create_suggestion``: o operador solicita a melhoria de uma resposta da IA. Guarda
  um PEDIDO ``pendente`` (mensagem + nota do operador, sem análise) e posta na conversa
  um aviso de sistema com link para o painel. NÃO chama o gerador.
- ``decide_suggestion(..., "aprovada")``: gera a análise (via ``generation.get_generator``)
  a partir dos snapshots do pedido, grava ``analysis``/``model`` e marca ``aprovada``.
- ``decide_suggestion(..., "recusada")``: só marca ``recusada`` (sem gerar).
"""

from __future__ import annotations

import logging
import os
import time

from sqlalchemy import bindparam, text

from plugins.context import broadcast, get_deps, make_plugin_db
from db.repositories import (config_repo, contact_repo, conversation_repo,
                             message_repo)

from . import generation

logger = logging.getLogger(__name__)

PLUGIN_ID = "melhorias"
_TABLE = "plugin_melhorias_suggestions"
_STATUSES = ("pendente", "aprovada", "recusada")


def now() -> float:
    return time.time()


_CONFIG_BACKFILL_FLAG = f"plugin.{PLUGIN_ID}.config_backfilled"


def backfill_core_config() -> None:
    """One-time: migra os valores legados do core (``improvement_model`` /
    ``improvement_prompt``) para o namespace do plugin (``plugin.melhorias.model``
    / ``.prompt``), preservando a config de quem já usava o recurso quando ele
    ficou no core. Idempotente via flag; best-effort."""
    try:
        if config_repo.get(_CONFIG_BACKFILL_FLAG):
            return
        old_model = config_repo.get("improvement_model", "") or ""
        old_prompt = config_repo.get("improvement_prompt", "") or ""
        if old_model and not _setting("model"):
            config_repo.set(f"plugin.{PLUGIN_ID}.model", old_model)
        if old_prompt and not _setting("prompt"):
            config_repo.set(f"plugin.{PLUGIN_ID}.prompt", old_prompt)
        config_repo.set(_CONFIG_BACKFILL_FLAG, True)
    except Exception as e:  # noqa: BLE001
        logger.debug("melhorias: backfill de config falhou: %s", e)


def _setting(name: str, default: str = "") -> str:
    return config_repo.get(f"plugin.{PLUGIN_ID}.{name}", default) or default


def _base_url() -> str:
    """URL base do painel para os links do plugin.

    Mesma cadeia do alerta de reconexão do gowa: config global ``public_base_url``
    (capturada/auto-corrigida no core) → env (``WHATSBOT_PUBLIC_URL`` /
    ``PUBLIC_BASE_URL``) → ``http://localhost:{web_port}``. Sempre absoluta, pra o
    link no aviso de sistema funcionar quando aberto fora do painel."""
    base = str(config_repo.get("public_base_url", "") or "").strip().rstrip("/")
    if base:
        return base
    env = (os.environ.get("WHATSBOT_PUBLIC_URL")
           or os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    port = config_repo.get("web_port", 8080) or 8080
    return f"http://localhost:{port}"


def _panel_deep_link(sid: int) -> str:
    """URL absoluta do painel focando esta sugestão (``?detail=<id>``)."""
    base = _base_url()
    return f"{base}/melhorias?detail={sid}" if base else f"/melhorias?detail={sid}"


def _conversation_deep_link(conversation_id, message_db_id) -> str | None:
    """URL absoluta da conversa ancorada na mensagem marcada (``?message=<id>``).
    Mesmo formato do permalink de mensagem do core."""
    if conversation_id is None:
        return None
    base = _base_url()
    path = f"/conversations/{conversation_id}"
    if message_db_id is not None:
        path += f"?message={message_db_id}"
    return f"{base}{path}" if base else path


def _channel_for_conversation(conversation_id) -> str:
    """Canal (channel_id) da conversa marcada; 'default' quando ausente/legado."""
    if conversation_id:
        try:
            conv = conversation_repo.get_with_channel(int(conversation_id))
        except (TypeError, ValueError):
            conv = None
        if conv and conv.get("channel_id"):
            return conv["channel_id"]
    return "default"


def _suggestion_dict(row) -> dict:
    """Row → dict do painel/API, com deep-links computados."""
    d = dict(row)
    d["panel_url"] = _panel_deep_link(d["id"])
    d["conversation_url"] = _conversation_deep_link(
        d.get("conversation_id"), d.get("message_db_id"))
    return d


# ── Escrita ──────────────────────────────────────────────────────────────────

def create_suggestion(*, phone: str, target_message: dict, feedback: str = "",
                      conversation_id=None,
                      requester_user_id=None, requester_name: str = "",
                      ) -> tuple[dict | None, str | None]:
    """Registra um PEDIDO pendente + posta o aviso de sistema. NÃO gera a análise."""
    target_message = target_message or {}
    content = (target_message.get("content") or "").strip()
    if not content:
        return None, "Mensagem inválida para análise."

    row = contact_repo.get_by_phone(phone)
    if not row:
        return None, "Contato não encontrado."
    contact_id = row["id"]
    contact_name = row.get("name") or row.get("phone") or phone

    # Valida posse do conversation_id (nunca injetar em conversa de outro contato).
    conv_id = conversation_id
    if conv_id is not None:
        try:
            conv = conversation_repo.get(int(conv_id))
        except (TypeError, ValueError):
            conv = None
        if not conv or conv.get("contact_id") != contact_id:
            conv_id = None

    message_db_id = target_message.get("_id")
    message_ts = target_message.get("ts") or now()
    ts = now()

    with make_plugin_db() as conn:
        sid = conn.execute(text(
            f"INSERT INTO {_TABLE} (contact_id, contact_phone, contact_name, "
            "conversation_id, message_db_id, message_ts, message_content, "
            "requester_user_id, requester_name, feedback, analysis, model, status, "
            "requested_at, decided_at, approver_user_id, approver_name, "
            "created_at, updated_at) VALUES (:contact_id, :contact_phone, "
            ":contact_name, :conversation_id, :message_db_id, :message_ts, "
            ":message_content, :requester_user_id, :requester_name, :feedback, '', "
            "'', 'pendente', :requested_at, NULL, NULL, '', :created_at, :updated_at) "
            "RETURNING id"), {
                "contact_id": contact_id, "contact_phone": row.get("phone") or phone,
                "contact_name": contact_name, "conversation_id": conv_id,
                "message_db_id": message_db_id, "message_ts": float(message_ts),
                "message_content": content,
                "requester_user_id": requester_user_id,
                "requester_name": requester_name or "",
                "feedback": (feedback or "").strip(),
                "requested_at": ts, "created_at": ts, "updated_at": ts,
            }).scalar_one()

    _post_system_notice(contact_id, phone, conv_id, sid)
    broadcast("plugin_melhorias_changed", {"id": sid, "action": "created"})
    return get_suggestion(sid), None


def _post_system_notice(contact_id: int, phone: str, conversation_id, sid: int) -> None:
    """Aviso painel-only ``role="system"`` na conversa marcada, com link ao painel.
    Best-effort: falha aqui nunca quebra a criação do pedido."""
    try:
        # O link vira um BOTÃO no card de sistema do painel: o frontend
        # (SystemMessageCard) reconhece o token ``[[cta:RÓTULO|URL]]`` e o
        # renderiza como botão de ação em vez de um link inline.
        text_p = ("🔧 Sugestão de melhoria enviada ao painel de aprovação.\n"
                  + f"[[cta:Ir para sugestão|{_panel_deep_link(sid)}]]")
        channel_id = _channel_for_conversation(conversation_id)
        if conversation_id is not None:
            saved = message_repo.add(contact_id, "system", text_p,
                                     conversation_id=int(conversation_id))
        else:
            # Sem conversa marcada: ancora na conversa mais recente do inbox default.
            deps = get_deps()
            agent_handler = getattr(deps, "agent_handler", None) if deps else None
            if agent_handler is None:
                saved = message_repo.add(contact_id, "system", text_p)
            else:
                cm = agent_handler._get_contact(phone, channel_id=channel_id)
                saved = cm.add_message("system", text_p)
        note = {"role": "system", "content": text_p,
                "ts": (saved or {}).get("ts", now()), "status": None, "msg_id": None,
                "conversation_id": (saved or {}).get("conversation_id")}
        if saved and saved.get("id"):
            note["_id"] = saved["id"]
        broadcast("new_message", {"phone": phone, "channel_id": channel_id, "message": note})
    except Exception as e:  # noqa: BLE001
        logger.debug("melhorias: aviso de sistema falhou: %s", e)


def decide_suggestion(sid: int, status: str, *, handler=None,
                      approver_user_id=None, approver_name: str = "",
                      ) -> tuple[dict | None, str | None]:
    """Aprova (gera a análise) ou recusa um pedido pendente.

    Só transita de ``pendente`` (senão retorna erro → 409 na rota). ``handler`` é
    necessário para gerar a análise ao aprovar (o gerador direto usa o cliente LLM
    do handler); com ``handler=None`` grava um fallback."""
    if status not in ("aprovada", "recusada"):
        return None, "Status inválido."
    row = get_suggestion(sid)
    if not row:
        return None, "Sugestão não encontrada."
    if row.get("status") != "pendente":
        return None, "Sugestão já foi decidida."

    analysis = ""
    model = ""
    if status == "aprovada":
        target = {"content": row.get("message_content") or "",
                  "ts": row.get("message_ts") or 0,
                  "_id": row.get("message_db_id")}
        if handler is None:
            analysis = "[WhatsBot] Não foi possível gerar a análise (handler indisponível)."
        else:
            ctx = generation.GenContext(
                handler=handler, phone=row.get("contact_phone") or "",
                target_message=target, feedback=row.get("feedback") or "",
                conversation_id=row.get("conversation_id"),
                model_override=_setting("model"), prompt_override=_setting("prompt"))
            res = generation.get_generator().generate(ctx)
            analysis, model = res.analysis, res.model

    ts = now()
    with make_plugin_db() as conn:
        conn.execute(text(
            f"UPDATE {_TABLE} SET status = :status, analysis = :analysis, "
            "model = :model, decided_at = :decided_at, "
            "approver_user_id = :approver_user_id, approver_name = :approver_name, "
            "updated_at = :updated_at WHERE id = :id"), {
                "status": status, "analysis": analysis, "model": model,
                "decided_at": ts, "approver_user_id": approver_user_id,
                "approver_name": approver_name or "", "updated_at": ts, "id": sid,
            })
    broadcast("plugin_melhorias_changed", {"id": sid, "action": status})
    return get_suggestion(sid), None


def refresh_contact_snapshot(phone: str) -> int:
    """Contato renomeado → atualiza o snapshot (contact_name/contact_phone) de TODAS
    as sugestões daquele contato, para que exibição E busca reflitam o nome atual.

    Lê o nome vigente direto do banco (não confia no payload do evento) e casa por
    ``contact_id`` (estável). Só escreve nas linhas que de fato mudaram. Best-effort:
    nunca levanta — uma falha aqui não pode quebrar a atualização do contato no core.
    Retorna o nº de sugestões atualizadas."""
    phone = (phone or "").strip()
    if not phone:
        return 0
    try:
        row = contact_repo.get_by_phone(phone)
        if not row:
            return 0
        contact_id = row["id"]
        cur_phone = row.get("phone") or phone
        # Mesmo fallback do create_suggestion: sem nome, exibe o telefone.
        display = (row.get("name") or "").strip() or cur_phone
        with make_plugin_db() as conn:
            res = conn.execute(text(
                f"UPDATE {_TABLE} SET contact_name = :name, contact_phone = :phone, "
                "updated_at = :ts WHERE contact_id = :cid "
                "AND (contact_name <> :name OR contact_phone <> :phone)"), {
                    "name": display, "phone": cur_phone, "ts": now(), "cid": contact_id,
                })
        changed = res.rowcount or 0
    except Exception as e:  # noqa: BLE001
        logger.debug("melhorias: refresh_contact_snapshot falhou: %s", e)
        return 0
    if changed:
        try:
            broadcast("plugin_melhorias_changed",
                      {"action": "contact_renamed", "contact_id": contact_id})
        except Exception as e:  # noqa: BLE001
            logger.debug("melhorias: broadcast pós-rename falhou: %s", e)
    return changed


# ── Leitura ──────────────────────────────────────────────────────────────────

def get_suggestion(sid: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text(f"SELECT * FROM {_TABLE} WHERE id = :id"), {"id": sid}
        ).mappings().first()
    return _suggestion_dict(row) if row else None


def _int_list(v) -> list[int]:
    out: list[int] = []
    for x in (v if isinstance(v, list) else [v]):
        if x is None or str(x).strip() == "":
            continue
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out


def _str_list(v, allowed=None) -> list[str]:
    out: list[str] = []
    for x in (v if isinstance(v, list) else [v]):
        if x is None or str(x).strip() == "":
            continue
        s = str(x).strip()
        if allowed is None or s in allowed:
            out.append(s)
    return out


def list_suggestions(*, status=None, requester_user_id=None, approver_user_id=None,
                     contact_id: int | None = None, conversation_id: int | None = None,
                     model: str | None = None, q: str | None = None,
                     requested_from: float | None = None, requested_to: float | None = None,
                     decided_from: float | None = None, decided_to: float | None = None,
                     limit: int = 200, offset: int = 0) -> list[dict]:
    """Lista sugestões com TODOS os campos do painel filtráveis (SQL puro)."""
    where = ["1=1"]
    params: dict = {}
    binds: list = []
    lim = max(1, min(int(limit or 200), 500))
    off = max(0, int(offset or 0))

    stats = _str_list(status, allowed=_STATUSES)
    if stats:
        where.append("status IN :stats")
        params["stats"] = stats
        binds.append(bindparam("stats", expanding=True))
    ruids = _int_list(requester_user_id)
    if ruids:
        where.append("requester_user_id IN :ruids")
        params["ruids"] = ruids
        binds.append(bindparam("ruids", expanding=True))
    auids = _int_list(approver_user_id)
    if auids:
        where.append("approver_user_id IN :auids")
        params["auids"] = auids
        binds.append(bindparam("auids", expanding=True))
    if contact_id is not None:
        where.append("contact_id = :cid")
        params["cid"] = int(contact_id)
    if conversation_id is not None:
        where.append("conversation_id = :conv")
        params["conv"] = int(conversation_id)
    if model:
        where.append("model LIKE :model")
        params["model"] = f"%{model}%"
    if q:
        where.append("(message_content LIKE :q OR feedback LIKE :q OR analysis LIKE :q "
                     "OR contact_name LIKE :q OR contact_phone LIKE :q)")
        params["q"] = f"%{q}%"
    if requested_from is not None:
        where.append("requested_at >= :rfrom")
        params["rfrom"] = float(requested_from)
    if requested_to is not None:
        where.append("requested_at <= :rto")
        params["rto"] = float(requested_to)
    if decided_from is not None:
        where.append("decided_at >= :dfrom")
        params["dfrom"] = float(decided_from)
    if decided_to is not None:
        where.append("decided_at <= :dto")
        params["dto"] = float(decided_to)

    sql = (f"SELECT * FROM {_TABLE} WHERE " + " AND ".join(where)
           + " ORDER BY (status = 'pendente') DESC, requested_at DESC"
           + " LIMIT :limit OFFSET :offset")
    stmt = text(sql)
    if binds:
        stmt = stmt.bindparams(*binds)
    with make_plugin_db() as conn:
        rows = conn.execute(stmt, {**params, "limit": lim, "offset": off}).mappings().all()
    return [_suggestion_dict(r) for r in rows]
