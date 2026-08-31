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

from fastapi import Depends, File, Form, Request, UploadFile

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


    # ── Envio de MÍDIA (plano 151 · F5) ──────────────────────────────────────
    #
    # Duas rotas, não uma: o FastAPI não declara um corpo que seja
    # ``multipart/form-data`` E ``application/json`` com parâmetros tipados, e
    # despachar na mão por ``Content-Type`` sobre um ``Request`` cru mentiria no
    # ``openapi.json`` — que é valor DECLARADO desta fachada ("pronto para
    # codegen"). Duas rotas mantêm o schema honesto.

    async def _send_media(request: Request, *, phone: str, kind: str,
                          data: bytes, filename: str | None,
                          content_type: str | None, caption: str,
                          conversation_id, channel_id) -> dict:
        """Cauda comum das duas rotas — valida, resolve o alvo e delega.

        Chama ``MessagingService.send_media_upload``, a MESMA função das quatro
        rotas do painel (R-media). Uma segunda implementação mandaria para o JID
        errado, fora da janela do canal, sem calar a IA — e nada disso apareceria
        como erro.
        """
        from app.services.messaging_service import MEDIA_KINDS

        if not phone:
            raise V1Error("Campo 'phone' é obrigatório.", code="missing_field")
        if kind not in MEDIA_KINDS:
            raise V1Error(
                "Campo 'kind' inválido. Use um de: " + ", ".join(MEDIA_KINDS) + ".",
                code="invalid_kind")
        if kind == "audio" and (caption or "").strip():
            # Recusar em vez de descartar em silêncio: ``/send/audio`` é nota de
            # voz (PTT) e o protocolo não carrega legenda. Aceitar-e-descartar
            # faria o integrador descobrir pelo relato do cliente, não na 1ª
            # chamada. (O painel não tem esse campo, então lá não há como errar.)
            raise V1Error(
                "Áudio é enviado como nota de voz e não aceita legenda. "
                "Envie a legenda como uma mensagem de texto separada.",
                code="caption_not_supported")
        if not data:
            raise V1Error("O arquivo está vazio.", code="empty_file")

        try:
            conv_id, chan_id = await asyncio.to_thread(
                _resolve_target, phone, conversation_id, channel_id)
        except AmbiguousTarget as e:
            raise V1Error(
                "Este contato tem conversa aberta em mais de uma caixa. Informe "
                "'conversation_id' ou 'channel_id' para escolher por onde enviar.",
                status=409, code="ambiguous_target", details={"options": e.options})
        if conversation_id and conv_id is None:
            raise not_found("Conversa não encontrada.")

        async def _inbox_guard():
            from app.services.messaging_service import resolve_inbox_id
            inbox_id = await asyncio.to_thread(resolve_inbox_id, conv_id, chan_id)
            from server import authz
            if not authz.can_access_inbox(request, inbox_id):
                return {"ok": False, "reason": "inbox_forbidden", "status": 403,
                        "message": "Sem acesso a esta caixa de entrada."}
            return None

        user = getattr(request.state, "user", None)
        result = await messaging.send_media_upload(
            phone=phone, kind=kind, data=data, filename=filename,
            content_type=content_type, caption=caption,
            conversation_id=conv_id, channel_id=chan_id,
            sent_by_user_id=(user.get("id") if user else None),
            sent_by_name=(user.get("name") if user else None),
            inbox_guard=_inbox_guard)
        if not result.get("ok"):
            raise V1Error(result["message"], status=result.get("status", 400),
                          code=result.get("reason") or "send_failed")
        return {"sent": True, "msg_id": result.get("msg_id"),
                "conversation_id": result.get("conversation_id"),
                "channel_id": result.get("channel_id"),
                "kind": result.get("kind"),
                "media_path": result.get("media_path"),
                "sandbox": result.get("sandbox", False)}

    @app.post(f"{V1_PREFIX}/messages/media", status_code=201, tags=["messages"],
              summary="Enviar mídia (upload multipart)",
              dependencies=[Depends(require("conversation.reply"))])
    async def send_media_message(
        request: Request,
        file: UploadFile = File(...),
        phone: str = Form(""),
        kind: str = Form(""),
        caption: str = Form(""),
        filename: str = Form(""),
        conversation_id: str = Form(""),
        channel_id: str = Form(""),
    ):
        """Envia imagem, áudio, documento ou vídeo pelos MESMOS trilhos do painel.

        Corpo `multipart/form-data`: `file` (o arquivo), `phone`, `kind`
        (`image` · `audio` · `document` · `video`), `caption` (opcional; **não**
        aceita em `audio`), `filename` (opcional — o nome que o destinatário vê;
        default: o nome da parte enviada), `conversation_id` **ou** `channel_id`
        (recomendado em instalação multicanal).

        ⚠️ **`kind` é seu, e nunca é deduzido do tipo do arquivo.** Mandar um
        `.png` com `kind=document` entrega a imagem **como arquivo**, sem
        recompressão — é assim que se preserva a qualidade de um certificado, um
        comprovante ou uma arte. Com `kind=image` a mesma foto é recomprimida
        pelo WhatsApp. Os dois caminhos existem de propósito.

        Respostas notáveis: **409** `session_window_closed` (fora da janela num
        canal Meta) e `ambiguous_target` (o número tem conversa aberta em mais de
        uma caixa); **413**/**415** quando o canal declara limite de tamanho ou
        formato para aquele `kind`; **403** quando o dono da chave não é membro
        da caixa de destino.
        """
        return await _send_media(
            request, phone=(phone or "").strip(), kind=(kind or "").strip(),
            data=await file.read(),
            filename=(filename or "").strip() or file.filename,
            content_type=file.content_type, caption=caption or "",
            conversation_id=conversation_id, channel_id=channel_id)

    @app.post(f"{V1_PREFIX}/messages/media/link", status_code=201, tags=["messages"],
              summary="Enviar mídia por URL ou base64",
              dependencies=[Depends(require("conversation.reply"))])
    async def send_media_link(body: dict, request: Request):
        """Mesma entrega da rota multipart, para quem já tem o arquivo em outro lugar.

        Corpo JSON: `phone`, `kind`, **`url` OU `content_base64`** (nunca os
        dois), `filename` (**obrigatório** — ver abaixo), `caption`,
        `content_type`, `conversation_id`/`channel_id`.

        ⚠️ **`filename` é obrigatório aqui.** O tipo que o WhatsApp anuncia ao
        destinatário sai da extensão desse nome; sem ela o arquivo chega como
        anexo genérico (`application/octet-stream`) e o cliente não consegue
        abri-lo com um duplo clique.

        **A URL é buscada pelo servidor, com guards.** Só `http`/`https`,
        redirecionamento **não** é seguido, o teto de tamanho vale durante o
        download (não no `Content-Length` declarado), timeout de 10 s, e todo
        endereço interno é recusado — rede privada, loopback e o endpoint de
        metadados da nuvem. Uma URL bloqueada devolve **400 `blocked_host`**, não
        um 500.

        Prefira o multipart quando o arquivo estiver em memória (é o caso de um
        Worker que acabou de gerar um PDF): `url` exige o arquivo publicamente
        alcançável e `content_base64` infla o corpo em ~33%.
        """
        import base64
        import mimetypes

        from server.upload_limits import (MAX_UPLOAD_BYTES, base64_exceeds,
                                          too_large_message)

        phone = (body.get("phone") or "").strip()
        if not phone:
            raise V1Error("Campo 'phone' é obrigatório.", code="missing_field")
        name = (body.get("filename") or "").strip()
        if not name:
            raise V1Error(
                "Campo 'filename' é obrigatório (a extensão dele define o tipo "
                "que o destinatário vê).", code="missing_field")

        url = (body.get("url") or "").strip()
        encoded = (body.get("content_base64") or "").strip()
        if url and encoded:
            raise V1Error("Informe 'url' OU 'content_base64', nunca os dois.",
                          code="conflicting_source")
        if not url and not encoded:
            raise V1Error("Informe 'url' ou 'content_base64'.", code="missing_field")

        content_type = (body.get("content_type") or "").strip() or None
        if encoded:
            # O teto é medido no COMPRIMENTO DA STRING: decodificar para depois
            # medir já colocou o arquivo inteiro na RAM, que é o que o teto
            # existe para impedir. Este caminho não passa pelo middleware de
            # upload (o corpo é JSON, não multipart).
            if base64_exceeds(encoded):
                raise V1Error(too_large_message(), status=413, code="too_big")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise V1Error("'content_base64' não é base64 válido.",
                              code="invalid_base64") from None
        else:
            from app.services.remote_media import (RemoteMediaError, TOO_BIG,
                                                   fetch_remote_media)
            try:
                data, observed = await fetch_remote_media(
                    url, max_bytes=MAX_UPLOAD_BYTES)
            except RemoteMediaError as e:
                raise V1Error(e.message,
                              status=(413 if e.reason == TOO_BIG else 400),
                              code=e.reason) from None
            content_type = content_type or observed

        content_type = content_type or mimetypes.guess_type(name)[0]
        return await _send_media(
            request, phone=phone, kind=(body.get("kind") or "").strip(),
            data=data, filename=name, content_type=content_type,
            caption=body.get("caption") or "",
            conversation_id=body.get("conversation_id"),
            channel_id=body.get("channel_id"))

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
