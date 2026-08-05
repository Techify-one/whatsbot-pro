"""Bus de eventos — entrada, reset e cancelamento das configurações (plano 76 F6).

* `message.saved` (mensagem do CLIENTE já gravada — nunca `message.received`, que é
  emitido ANTES do INSERT): re-arma o controle existente conforme `on_reply` da configuração
  (**reset** ou **cancel** — D8, o campo é de fato lido) ou ROTEIA a conversa para a
  primeira configuração ativa (por `posicao`) que aceite a conversa. NÃO existe gate de entrada:
  quem filtra são as condições de cada RETORNO, avaliadas na hora do disparo.
* `conversation.status_changed` / `.archived` / `.assigned` / `.ai_toggled` e
  `contact.ai_toggled`: cancelam o controle conforme os toggles da configuração
  (`cancel_on_resolve`, `cancel_on_assign_human`, `cancel_on_ai_off`).
* `app.startup`: solta locks órfãos deixados por um shutdown no meio de um ciclo.

O plugin **não** assina `message.sent`: a resposta que ele mesmo provoca emitiria o evento
de volta e criaria um laço (CLAUDE.md §Boas práticas).
"""

from __future__ import annotations

import logging
import time

from . import dispatcher, evalctx, repo

logger = logging.getLogger("plugin.retornos")

PLUGIN_ID = "retornos"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _enabled() -> bool:
    try:
        from db.repositories import config_repo
        return bool(config_repo.get(f"plugin.{PLUGIN_ID}.enabled", True))
    except Exception:  # noqa: BLE001
        return True


def _resolve_conversation(payload: dict):
    """``(conv enriquecida, contact)`` a partir de ``phone`` (+ ``channel_id`` quando vem)."""
    from db.repositories import contact_repo, conversation_repo, inbox_repo

    phone = (payload or {}).get("phone")
    if not phone:
        return None, None
    contact = contact_repo.get_by_phone(phone)
    if not contact:
        return None, None

    conv = None
    channel_id = (payload or {}).get("channel_id")
    if channel_id:
        try:
            inbox = inbox_repo.get_by_channel(channel_id)
            if inbox:
                conv = (conversation_repo.get_open_for_contact_inbox(contact["id"], inbox["id"])
                        or conversation_repo.get_latest_for_contact_inbox(contact["id"], inbox["id"]))
        except Exception:  # noqa: BLE001
            conv = None
    if conv is None:
        conv = (conversation_repo.get_open_for_contact(contact["id"])
                or conversation_repo.get_latest_for_contact(contact["id"]))
    if conv is None:
        return None, None
    return conversation_repo.get_with_channel(int(conv["id"])), contact


def _arm(*, configuracao: dict, conv: dict, contact: dict | None, now: float,
         motivo: str) -> dict | None:
    """Cria/re-arma o controle da conversa no PRIMEIRO retorno da configuração."""
    retorno = repo.first_retorno(configuracao["id"])
    ctx = evalctx.build_ctx(conv=conv, contact=contact, configuracao=configuracao,
                            controle={"last_client_ts": now, "disparos_enviados": 0},
                            now=now)
    next_at = (dispatcher.proximo_para_retorno(retorno, ctx, now=now, base=now)
               if retorno else now + 60.0)
    ctrl = repo.upsert_controle(
        conversation_id=int(conv["id"]), configuracao_id=int(configuracao["id"]),
        contact_id=conv.get("contact_id"),
        phone=conv.get("contact_phone") or (contact or {}).get("phone") or "",
        contact_name=conv.get("contact_name") or (contact or {}).get("name") or "",
        channel_id=conv.get("channel_id") or "default",
        inbox_id=conv.get("inbox_id"),
        retorno_atual_id=(retorno or {}).get("id"),
        next_at=next_at, last_client_ts=now)
    repo.add_log("armed", configuracao_id=configuracao["id"], controle_id=ctrl.get("id"),
                 conversation_id=conv["id"], retorno_id=(retorno or {}).get("id"),
                 data={"motivo": motivo, "configuracao": configuracao.get("nome"),
                       "next_at": next_at})
    try:
        from plugins.context import broadcast
        broadcast("retornos_changed", {"ts": now})
    except Exception:  # noqa: BLE001
        pass
    return ctrl


def _cancelar_por_conversa(conversation_id, *, flag: str, motivo: str) -> None:
    """Cancela o controle da conversa se a configuração dele autoriza (`flag`)."""
    if not conversation_id:
        return
    try:
        ctrl = repo.get_controle_by_conversation(int(conversation_id))
        if not ctrl or ctrl.get("status") != repo.STATUS_ACTIVE:
            return
        configuracao = repo.get_configuracao(ctrl.get("configuracao_id"))
        if not configuracao or not configuracao.get(flag):
            return
        repo.cancel_by_conversation(int(conversation_id), motivo)
        from plugins.context import broadcast
        broadcast("retornos_changed", {"ts": time.time()})
    except Exception as e:  # noqa: BLE001 — handler nunca quebra o pipeline
        logger.debug("retornos: cancelamento (%s) falhou: %s", motivo, e)


# ── Handlers ──────────────────────────────────────────────────────────────────

def on_message_saved(ctx, payload: dict) -> None:
    """Mensagem do cliente: reset/cancel do controle, ou entrada numa configuração."""
    try:
        if not _enabled():
            return
        payload = payload or {}
        now = float(payload.get("ts") or time.time())
        # Atalho: sem configuração ativa não há nada a fazer (uma consulta em vez de quatro em
        # toda mensagem recebida). Um controle órfão de configuração desativada é cancelado pelo
        # próprio ciclo (`process_controle` → "configuracao_inativa").
        configuracoes_ativas = repo.list_configuracoes(only_active=True)
        if not configuracoes_ativas:
            return
        conv, contact = _resolve_conversation(payload)
        if not conv:
            return
        conv_id = int(conv["id"])
        is_group = int((contact or {}).get("is_group") or 0) == 1

        ctrl = repo.get_controle_by_conversation(conv_id)
        if ctrl and ctrl.get("status") == repo.STATUS_ACTIVE:
            configuracao = repo.get_configuracao(ctrl.get("configuracao_id"))
            if not configuracao or not configuracao.get("ativo"):
                repo.cancel_by_conversation(conv_id, "configuração inativa")
                return
            if str(configuracao.get("on_reply") or "reset").lower() == "cancel":
                repo.cancel_by_conversation(conv_id, "cliente respondeu (on_reply=cancel)")
                return
            _arm(configuracao=configuracao, conv=conv, contact=contact, now=now,
                 motivo="reset_cliente_respondeu")
            return

        # Sem controle ativo → a primeira configuração ativa (por `posicao`) que aceite a conversa
        # assume. Quem filtra de fato são as condições dos RETORNOS, avaliadas no disparo.
        for configuracao in configuracoes_ativas:
            if is_group and not configuracao.get("apply_to_groups"):
                continue
            _arm(configuracao=configuracao, conv=conv, contact=contact, now=now, motivo="entrada")
            return
    except Exception as e:  # noqa: BLE001
        logger.debug("retornos.on_message_saved falhou: %s", e, exc_info=True)


def on_conversation_status_changed(ctx, payload: dict) -> None:
    payload = payload or {}
    if str(payload.get("status") or "") != "closed":
        return
    _cancelar_por_conversa(payload.get("conversation_id"), flag="cancel_on_resolve",
                           motivo="atendimento resolvido")


def on_conversation_archived(ctx, payload: dict) -> None:
    payload = payload or {}
    if not int(payload.get("is_archived") or 0):
        return
    _cancelar_por_conversa(payload.get("conversation_id"), flag="cancel_on_resolve",
                           motivo="atendimento arquivado")


def on_conversation_assigned(ctx, payload: dict) -> None:
    payload = payload or {}
    if not payload.get("assignee_user_id"):
        return
    _cancelar_por_conversa(payload.get("conversation_id"),
                           flag="cancel_on_assign_human",
                           motivo="atendimento assumido por um atendente")


def on_conversation_ai_toggled(ctx, payload: dict) -> None:
    payload = payload or {}
    if int(payload.get("ai_active") or 0) == 1:
        return
    _cancelar_por_conversa(payload.get("conversation_id"), flag="cancel_on_ai_off",
                           motivo="IA desligada na conversa")


def on_contact_ai_toggled(ctx, payload: dict) -> None:
    """A IA do CONTATO foi desligada — cancela o controle da conversa dele."""
    payload = payload or {}
    if payload.get("ai_enabled"):
        return
    try:
        conv, _contact = _resolve_conversation(payload)
        if conv:
            _cancelar_por_conversa(conv.get("id"), flag="cancel_on_ai_off",
                                   motivo="IA desligada no contato")
    except Exception as e:  # noqa: BLE001
        logger.debug("retornos.on_contact_ai_toggled falhou: %s", e)


def on_startup(ctx, payload: dict) -> None:
    """Solta locks órfãos e apara o log (as migrations já rodaram neste ponto)."""
    try:
        soltos = repo.recover_stale_locks(older_than=time.time())
        if soltos:
            logger.info("[Retornos] %s lock(s) órfão(s) liberado(s) no boot", soltos)
        repo.prune_logs(keep=5000)
    except Exception as e:  # noqa: BLE001
        logger.debug("retornos.on_startup falhou: %s", e)


EVENT_HANDLERS = {
    "message.saved": on_message_saved,
    "conversation.status_changed": on_conversation_status_changed,
    "conversation.archived": on_conversation_archived,
    "conversation.assigned": on_conversation_assigned,
    "conversation.ai_toggled": on_conversation_ai_toggled,
    "contact.ai_toggled": on_contact_ai_toggled,
    "app.startup": on_startup,
}
