"""``/api/v1/messages`` — envio e leitura de mensagens (domínio 2 de D6).

**O envio de texto chama ``MessagingService.send_text``** — a MESMA função do
handler do painel (refactor R-txt da fase 5). É o ponto mais importante da
fachada: uma segunda implementação mandaria mensagem fora da janela de 24h, para
o JID errado (ghost-send do 9º dígito), sem @menção de grupo, sem dedupe de eco e
sem calar o ciclo da IA em andamento — e nada disso apareceria como erro.

Multicanal: o mesmo número pode ter conversa em VÁRIAS caixas. O integrador
escolhe por ``conversation_id`` (preferido) ou por ``channel_id``; a resolução
usa ``get_open_for_contact_inbox``, **nunca** ``get_open_for_contact`` (que é
contact-scoped e funde canais).

Gates: ``conversation.reply`` para escrever, ``conversation.read`` para ler.
"""

from __future__ import annotations

import asyncio

from fastapi import Depends, Request

from db.repositories import (contact_repo, conversation_repo, inbox_repo,
                             message_repo)
from server.pagination import CAP_MSGS, PAGE_MSGS, clamp_limit, clamp_offset
from server.routes.v1._common import (V1_PREFIX, V1Error, message_dto, not_found,
                                      require, visible_inboxes)


class AmbiguousTarget(Exception):
    """O contato tem conversa aberta em MAIS DE UMA caixa e o corpo não escolheu."""

    def __init__(self, options):
        self.options = options
        super().__init__("alvo ambíguo")


def _resolve_target(phone: str, conversation_id, channel_id):
    """``(conversation_id, channel_id)`` do alvo do envio.

    ⚠️ **Nunca usa ``get_open_for_contact``.** Esse resolvedor é contact-scoped e
    FUNDE canais: com o mesmo número atendido em duas caixas, ele devolveria uma
    conversa qualquer das duas e a mensagem sairia pelo canal errado — o §8 do
    plano de API e o guardrail ``test_guardrail_no_new_channel_blind_resolvers``
    (plano 37 P4) existem exatamente para isso.

    A escada, então, é explícita:

    * ``conversation_id`` ⇒ é ele, sem adivinhação;
    * ``channel_id`` ⇒ a conversa ABERTA do contato **naquela inbox**
      (``get_open_for_contact_inbox``);
    * nenhum dos dois ⇒ olha as conversas abertas do contato. **Uma** ⇒ usa.
      **Nenhuma** ⇒ deixa o serviço abrir pelo caminho padrão. **Mais de uma** ⇒
      levanta :class:`AmbiguousTarget`, e a rota devolve 409 listando as opções.
      Recusar é o único comportamento honesto aqui: escolher por conta própria é
      mandar mensagem de cliente pelo canal errado, em silêncio.
    """
    if conversation_id:
        conv = conversation_repo.get_with_channel(int(conversation_id))
        if conv is None:
            return None, None
        return conv["id"], conv.get("channel_id")

    contact = contact_repo.get_by_phone(phone)
    if contact is None:
        return None, (str(channel_id) if channel_id else None)

    if channel_id:
        inbox = inbox_repo.get_by_channel(str(channel_id))
        if inbox is None:
            return None, str(channel_id)
        conv = conversation_repo.get_open_for_contact_inbox(contact["id"], inbox["id"])
        return (conv["id"] if conv else None), str(channel_id)

    open_convs = conversation_repo.list_conversations(
        status="open", contact_ids=[contact["id"]], limit=10)
    if len(open_convs) > 1:
        raise AmbiguousTarget([
            {"conversation_id": c["id"], "channel_id": c.get("channel_id"),
             "inbox_id": c.get("inbox_id")} for c in open_convs])
    if open_convs:
        c = open_convs[0]
        return c["id"], c.get("channel_id")
    return None, None


def register_routes(app, deps):
    from app.services.messaging_service import MessagingContext, MessagingService
    from channels import ai_settings as _ais

    settings = deps.settings

    def _channel_ai_enabled(channel_id: str) -> bool:
        if not settings.get("auto_reply", True):
            return False
        return bool(_ais.value(channel_id, "ai_enabled", True))

    messaging = MessagingService(MessagingContext(
        deps=deps, agent_handler=deps.agent_handler, ws_manager=deps.ws_manager,
        state=deps.state, settings=settings, outbound=deps.outbound_router,
        channel_ai_enabled=_channel_ai_enabled,
    ))

    @app.post(f"{V1_PREFIX}/messages", status_code=201, tags=["messages"],
              summary="Enviar mensagem de texto",
              dependencies=[Depends(require("conversation.reply"))])
    async def send_message(body: dict, request: Request):
        """Envia texto pelos MESMOS trilhos do painel.

        Corpo: ``phone`` (obrigatório), ``message`` (obrigatório),
        ``conversation_id`` **ou** ``channel_id`` (recomendado em instalação
        multicanal), ``reply_to`` (msg_id citado).

        Respostas notáveis: **409** ``session_window_closed`` fora da janela de
        24h num canal Meta (o mesmo bloqueio que o painel dá) e **403** quando o
        dono da chave não é membro da caixa de destino.
        """
        phone = (body.get("phone") or "").strip()
        if not phone:
            raise V1Error("Campo 'phone' é obrigatório.", code="missing_field")
        message = body.get("message") or ""

        try:
            conversation_id, channel_id = await asyncio.to_thread(
                _resolve_target, phone, body.get("conversation_id"),
                body.get("channel_id"))
        except AmbiguousTarget as e:
            raise V1Error(
                "Este contato tem conversa aberta em mais de uma caixa. Informe "
                "'conversation_id' ou 'channel_id' para escolher por onde enviar.",
                status=409, code="ambiguous_target", details={"options": e.options})
        if body.get("conversation_id") and conversation_id is None:
            raise not_found("Conversa não encontrada.")

        async def _inbox_guard():
            from app.services.messaging_service import resolve_inbox_id
            inbox_id = await asyncio.to_thread(
                resolve_inbox_id, conversation_id, channel_id)
            from server import authz
            if not authz.can_access_inbox(request, inbox_id):
                return {"ok": False, "reason": "inbox_forbidden", "status": 403,
                        "message": "Sem acesso a esta caixa de entrada."}
            return None

        user = getattr(request.state, "user", None)
        result = await messaging.send_text(
            phone=phone, message=message,
            conversation_id=conversation_id, channel_id=channel_id,
            reply_to=body.get("reply_to"),
            sent_by_user_id=(user.get("id") if user else None),
            sent_by_name=(user.get("name") if user else None),
            inbox_guard=_inbox_guard,
        )
        if not result.get("ok"):
            raise V1Error(result["message"], status=result.get("status", 400),
                          code=result.get("reason") or "send_failed")
        return {"sent": True, "msg_id": result.get("msg_id"),
                "conversation_id": result.get("conversation_id"),
                "channel_id": result.get("channel_id"),
                "sandbox": result.get("sandbox", False)}

    @app.get(f"{V1_PREFIX}/conversations/{{conv_id}}/messages", tags=["messages"],
             summary="Ler a thread de uma conversa (paginada)",
             dependencies=[Depends(require("conversation.read"))])
    async def list_messages(conv_id: int, request: Request,
                            limit: int = PAGE_MSGS,
                            before_id: int | None = None,
                            after_id: int | None = None,
                            around_id: int | None = None):
        """Página cronológica (oldest→newest) da conversa.

        Paginação keyset: ``before_id`` traz as anteriores, ``after_id`` as
        seguintes e ``around_id`` a janela CENTRADA numa mensagem. As três são
        mutuamente exclusivas. **Nunca** marca a conversa como lida — a leitura
        programática de um integrador não pode zerar o badge do operador.
        """
        anchors = [a for a in (before_id, after_id, around_id) if a is not None]
        if len(anchors) > 1:
            raise V1Error("Use apenas uma âncora: before_id, after_id ou around_id.",
                          code="conflicting_anchors")
        conv = await asyncio.to_thread(conversation_repo.get, conv_id)
        if conv is None:
            raise not_found("Conversa não encontrada.")
        vis = visible_inboxes(request)
        if vis is not None and conv.get("inbox_id") not in vis:
            raise not_found("Conversa não encontrada.")
        page_limit = clamp_limit(limit, PAGE_MSGS, CAP_MSGS)
        rows, window = await asyncio.to_thread(
            message_repo.read_window, page_limit, before_id=before_id,
            after_id=after_id, around_id=around_id, conversation_id=conv_id)
        return {
            "items": [message_dto(m) for m in rows],
            "has_more_older": window["has_more_older"],
            "has_more_newer": window["has_more_newer"],
            "anchor_id": window["anchor_id"],
            "limit": page_limit,
        }

    @app.get(f"{V1_PREFIX}/conversations/{{conv_id}}/messages/search", tags=["messages"],
             summary="Buscar texto DENTRO de uma conversa",
             dependencies=[Depends(require("conversation.read"))])
    async def search_messages(conv_id: int, request: Request, q: str = "",
                              limit: int = PAGE_MSGS, offset: int = 0):
        """Lista de ocorrências da thread, mais recente primeiro, com trecho.

        Mesmo motor (``db.search.message_search``) e mesmo escopo de caixa da
        tela — uma busca não pode ser a porta lateral para ler uma conversa
        fora do escopo da chave. ``q`` com menos de 3 caracteres devolve vazio.
        """
        conv = await asyncio.to_thread(conversation_repo.get, conv_id)
        if conv is None:
            raise not_found("Conversa não encontrada.")
        vis = visible_inboxes(request)
        if vis is not None and conv.get("inbox_id") not in vis:
            raise not_found("Conversa não encontrada.")
        from db.search import message_search
        data = await asyncio.to_thread(
            message_search.search_in_conversation,
            q=q or "", conversation_id=conv_id,
            limit=clamp_limit(limit, PAGE_MSGS, CAP_MSGS),
            offset=clamp_offset(offset))
        return data

    @app.post(f"{V1_PREFIX}/conversations/{{conv_id}}/read", tags=["messages"],
              summary="Marcar a conversa como lida",
              dependencies=[Depends(require("conversation.read"))])
    async def mark_read(conv_id: int, request: Request):
        """Espelha ``POST /api/atendimentos/{id}/read`` do painel: marca a
        conversa como lida e manda os recibos pelo canal DELA.

        ⚠️ NÃO emite ``conversation_upsert``. Esse evento carrega uma linha de
        conversa ENRIQUECIDA (o painel a insere direto na sidebar); mandar um
        ``{id}`` parcial plantaria uma linha cega ali. A rota do painel também
        não emite — o badge some pelo caminho normal.
        """
        conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
        if conv is None:
            raise not_found("Conversa não encontrada.")
        vis = visible_inboxes(request)
        if vis is not None and conv.get("inbox_id") not in vis:
            raise not_found("Conversa não encontrada.")
        msg_ids = await asyncio.to_thread(
            conversation_repo.mark_conversation_read, conv_id)
        if msg_ids:
            outbound = deps.outbound_router
            channel_id = conv.get("channel_id") or "default"
            phone = conv.get("contact_phone") or ""
            for mid in msg_ids:
                # Nota privada notificada usa msg_id sintético ("pn:…") que não
                # existe no provedor — nunca mandar recibo dele.
                if str(mid).startswith("pn:"):
                    continue
                try:
                    await asyncio.to_thread(outbound.mark_read, channel_id, phone, mid)
                except Exception:  # noqa: BLE001 — recibo é best-effort
                    pass
        return {"conversation_id": conv_id, "marked": len(msg_ids)}
