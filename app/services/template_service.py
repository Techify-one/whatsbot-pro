"""WhatsApp Cloud templates + 24h session-window service (Plano 23 · Fase B6).

Owns the template + session-window logic previously inline in
``server/routes/channels.py`` for STARTING a brand-new conversation (no
conversation row yet, plano 21): the 24h free-text window resolution
(``session-state``), the channel-scoped template list / create / delete, and the
``send-template`` flow (send → save operator message → WS broadcast → bus emit).

Branch by Abstraction — the routes resolve permission + body validation + 404,
then delegate here. Behavior preserved byte-for-byte (legacy suite is the
contract). Capability-driven: never branches on provider name — it asks the
``deps.outbound_router`` whether the channel ``supports("templates")`` and whether
its session is open.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from db.repositories import (channel_repo, contact_repo, conversation_repo,
                             inbox_repo, message_repo)
from plugins.events import emit_with_filter

# ── Regras de forma: DECLARADAS PELO PROVIDER (plano 92 · F1) ────────────
#
# Até o plano 92 estas constantes viviam aqui: categorias, formatos de
# cabeçalho, tipos de botão, limites de mistura, whitelist de MIME e o teto de
# 16 MiB. Todas são vocabulário da Graph API da Meta — e o core não deve
# conhecer o vocabulário de um provedor. Agora quem as declara é o próprio
# canal, em ``ChannelCapabilities.template_spec`` (:class:`channels.base.TemplateSpec`),
# e este módulo apenas AVALIA. Mesma política/mecanismo de ``MediaLimits``.
#
# ``spec = None`` ⇒ o core NÃO restringe: deixa passar e quem recusa é o
# provedor, com a mensagem dele. É a escolha consciente de não manter no core
# uma cópia envelhecida das regras da Meta.

logger = logging.getLogger(__name__)

# Avisado UMA vez por canal, para um zip de plugin velho (sem spec) não passar
# despercebido nem poluir o log a cada request.
_SPEC_WARNED: set = set()


def spec_for(deps, channel_id: str):
    """A :class:`TemplateSpec` do canal, ou ``None`` quando ele não declara."""
    try:
        inst = deps.outbound_router.get(channel_id)
        spec = getattr(getattr(inst, "capabilities", None), "template_spec", None)
    except Exception:  # noqa: BLE001 — falta de spec nunca derruba a rota
        spec = None
    if spec is None and channel_id not in _SPEC_WARNED:
        _SPEC_WARNED.add(channel_id)
        logger.warning(
            "Canal %s não declara template_spec: o core não validará a forma do "
            "template (categoria, botões, upload) — quem recusa passa a ser o "
            "provedor. Atualize o plugin do canal.", channel_id)
    return spec


def _allowed(spec, campo: str) -> frozenset:
    """O conjunto declarado para ``campo``, ou vazio (= não restringe)."""
    return frozenset(getattr(spec, campo, None) or ()) if spec is not None else frozenset()


def _cap(spec, campo: str) -> int:
    return int(getattr(spec, campo, 0) or 0) if spec is not None else 0


def validate_name(name: str):
    """Nome do template. Genérico DE PROPÓSITO — o formato aceito é do provedor,
    mas um nome vazio nunca serve, então este piso fica no core."""
    if not name:
        return "Nome inválido: informe um nome."
    return None


def validate_category(spec, category: str):
    permitidas = _allowed(spec, "categories")
    if permitidas and category not in permitidas:
        return f"category deve ser uma de {sorted(permitidas)}."
    return None


def normalize_header_media(spec, header_format, header_handle):
    """Validate the media-header pair. Returns ``(format, handle, error)``.

    Both empty ⇒ ``(None, None, None)`` (text header path, unchanged). Only one
    of the two present is a client error — a media header without its handle
    would be silently dropped by the provider.
    """
    fmt = (header_format or "").strip().upper() or None
    handle = (header_handle or "").strip() or None
    if fmt is None and handle is None:
        return (None, None, None)
    permitidos = _allowed(spec, "header_formats")
    if fmt is not None and permitidos and fmt not in permitidos:
        return (None, None,
                f"header_format deve ser um de {sorted(permitidos)}.")
    if fmt is None or handle is None:
        return (None, None,
                "Cabeçalho de mídia exige header_format e header_handle juntos.")
    return (fmt, handle, None)


def normalize_buttons(spec, raw):
    """Validate/normalize the button list. Returns ``(buttons, error)``.

    ``buttons`` is ``None`` when nothing was sent (no BUTTONS component). Each
    item keeps only the keys its type uses, so nothing extra leaks to the
    provider.
    """
    if raw is None:
        return (None, None)
    if not isinstance(raw, list):
        return (None, "buttons deve ser uma lista.")
    if not raw:
        return (None, None)
    max_botoes = _cap(spec, "buttons_max")
    if max_botoes and len(raw) > max_botoes:
        return (None, f"No máximo {max_botoes} botões por template.")
    tipos_ok = _allowed(spec, "button_types")
    max_por_tipo = (getattr(spec, "button_type_max", None) or {}) if spec is not None else {}
    max_texto = _cap(spec, "button_text_max")

    out: list[dict] = []
    counts: dict[str, int] = {}
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return (None, f"Botão {i}: formato inválido.")
        btype = (item.get("type") or "").strip().upper()
        if not btype:
            return (None, f"Botão {i}: type é obrigatório.")
        if tipos_ok and btype not in tipos_ok:
            return (None,
                    f"Botão {i}: type deve ser um de {sorted(tipos_ok)}.")
        counts[btype] = counts.get(btype, 0) + 1
        limit = max_por_tipo.get(btype)
        if limit is not None and counts[btype] > limit:
            return (None, f"No máximo {limit} botão(ões) do tipo {btype}.")

        text = (item.get("text") or "").strip()
        if btype != "COPY_CODE":
            if not text:
                return (None, f"Botão {i}: texto é obrigatório.")
            if max_texto and len(text) > max_texto:
                return (None,
                        f"Botão {i}: texto deve ter até {max_texto} caracteres.")

        btn: dict = {"type": btype}
        if btype == "QUICK_REPLY":
            btn["text"] = text
        elif btype == "URL":
            url = (item.get("url") or "").strip()
            if not url:
                return (None, f"Botão {i}: url é obrigatória.")
            btn["text"] = text
            btn["url"] = url
            example = item.get("example")
            if isinstance(example, list):
                example = example[0] if example else ""
            example = (example or "").strip() if isinstance(example, str) else ""
            if "{{" in url:
                if not example:
                    return (None, f"Botão {i}: URL com variável exige um exemplo.")
                btn["example"] = [example]
            elif example:
                btn["example"] = [example]
        elif btype == "PHONE_NUMBER":
            phone = (item.get("phone_number") or "").strip()
            if not phone:
                return (None, f"Botão {i}: phone_number é obrigatório.")
            btn["text"] = text
            btn["phone_number"] = phone
        elif btype == "COPY_CODE":
            example = item.get("example")
            if isinstance(example, list):
                example = example[0] if example else ""
            example = (example or "").strip() if isinstance(example, str) else ""
            if not example:
                return (None, f"Botão {i}: código de exemplo é obrigatório.")
            btn["example"] = example
        else:
            # Tipo que este core não conhece (o provider declarou, o core não
            # precisa entender): repassa o texto e deixa o provedor decidir.
            if text:
                btn["text"] = text
        out.append(btn)
    return (out, None)


def validate_example_upload(spec, mime: str, size: int):
    """Validate a header-example upload BEFORE hitting the provider.

    Returns an error string, or ``None`` when acceptable.
    """
    mime = (mime or "").split(";")[0].strip().lower()
    permitidos = _allowed(spec, "upload_mimes")
    if permitidos and mime not in permitidos:
        return ("Tipo de arquivo não suportado neste canal. Aceitos: "
                + ", ".join(sorted(permitidos)) + ".")
    if size <= 0:
        return "Arquivo vazio."
    teto = _cap(spec, "upload_max_bytes")
    if teto and size > teto:
        return f"Arquivo maior que {teto // (1024 * 1024)} MB."
    return None


async def upload_template_example(deps, channel_id: str, *, file_bytes: bytes,
                                  mime: str, filename: str):
    """Upload a media sample to the provider. Returns ``("ok", data)`` or
    ``("failed", error)``."""
    result = await asyncio.to_thread(
        deps.outbound_router.upload_template_example, channel_id,
        file_bytes, mime, filename)
    if not result.get("ok"):
        return ("failed", result.get("error"))
    return ("ok", {"handle": result.get("handle")})


def session_state(deps, channel_id: str, phone: str) -> dict:
    """Session/window state for starting a conversation on ``channel_id`` (plano 21).

    Resolves the contact's latest conversation IN this channel's inbox (if any) and
    computes the 24h free-text window from its last inbound. Synchronous DB work —
    run via ``asyncio.to_thread`` from the route.
    """
    outbound = deps.outbound_router
    templates_supported = outbound.supports(channel_id, "templates")
    contact = contact_repo.get_by_phone(phone)
    conv = None
    if contact:
        inbox = inbox_repo.get_by_channel(channel_id)
        if inbox:
            conv = conversation_repo.get_latest_for_contact_inbox(
                contact["id"], inbox["id"])
    conv_id = conv["id"] if conv else None
    last_ts = (message_repo.last_inbound_ts(conversation_id=conv_id)
               if conv_id else None)
    return {
        "templates_supported": templates_supported,
        # ``by_human=True``: só o painel do operador consulta este endpoint (é o
        # "Nova conversa"). Ver a nota em routes/conversations.py.
        "session_open": outbound.session_open(channel_id, last_ts, by_human=True),
        "has_conversation": conv is not None,
        "conversation_id": conv_id,
        "last_inbound_ts": last_ts,
    }


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _channel_badge_sync(deps, channel_id: str) -> dict:
    """``{id, name, phone}`` do canal — quem é o remetente do template.

    O painel mostra isso no cabeçalho do modal (enviar/criar/selecionar) para o
    atendente saber DE QUAL número o template vai sair. O número vem de
    ``channels.own_phone`` (persistido pelo sweep de identidade); quando ainda
    está vazio, pergunta ao provider uma vez via ``status()`` e persiste — assim
    a instalação que não roda o sweep (sem o plugin gowa) também mostra o número.
    Genérico: nenhum ``if provider ==``; um provider que não expõe número
    simplesmente devolve ``phone=None`` e o painel mostra só o nome.
    """
    try:
        row = channel_repo.get(channel_id) or {}
    except Exception:  # noqa: BLE001
        row = {}
    name = (row.get("display_name") or "").strip() or channel_id
    phone = _digits(row.get("own_phone"))
    if not phone:
        try:
            inst = deps.outbound_router.get(channel_id)
            st = inst.status() if inst is not None else {}
            phone = _digits((st or {}).get("own_phone")
                            or (st or {}).get("display_phone_number"))
            if phone:
                channel_repo.set_status(channel_id, own_phone=phone)
        except Exception:  # noqa: BLE001
            phone = ""
    return {"id": channel_id, "name": name, "phone": phone or None}


async def channel_badge(deps, channel_id: str) -> dict:
    """Async wrapper de :func:`_channel_badge_sync` (DB + provider bloqueiam)."""
    return await asyncio.to_thread(_channel_badge_sync, deps, channel_id)


def supports_templates(deps, channel_id: str) -> bool:
    return deps.outbound_router.supports(channel_id, "templates")


async def list_templates(deps, channel_id: str) -> list[dict]:
    return await asyncio.to_thread(deps.outbound_router.list_templates, channel_id)


# Tipos de cabeçalho de template que o painel sabe desenhar como bolha de mídia.
# Não é vocabulário de provedor: é a lista do que ``MediaContent.js`` renderiza.
_SAVEABLE_MEDIA = {"image", "video", "document"}


def sanitize_media(deps, media_type, media_path):
    """Valida a mídia que o cliente PEDIU para gravar junto do template.

    O caminho vem do navegador, então nunca é usado como veio: só passa a forma
    exata ``statics/outbox/<arquivo>`` — sem barra, sem ``..`` — que é o mesmo
    diretório de onde ``/send-image`` grava e de onde a rota
    ``GET /statics/outbox/{name}`` serve. Um caminho fora disso deixaria o painel
    pedir um arquivo arbitrário do disco.

    Falha é SEMPRE macia: devolve ``(None, None)`` e a mensagem é gravada como
    texto (o comportamento anterior). Recusar o envio seria pior — quando isto é
    chamado o template já foi entregue ao cliente, e uma miniatura ausente no
    histórico não justifica devolver erro de um envio que deu certo.

    :returns: ``(media_type, media_path)`` normalizados, ou ``(None, None)``.
    """
    kind = (media_type or "").strip().lower()
    path = (media_path or "").strip().lstrip("/")
    if not kind and not path:
        return (None, None)
    if kind not in _SAVEABLE_MEDIA:
        logger.warning("[Template] media_type ignorado: %r", media_type)
        return (None, None)
    prefix = "statics/outbox/"
    name = path[len(prefix):] if path.startswith(prefix) else ""
    if not name or "/" in name or "\\" in name or ".." in name:
        logger.warning("[Template] media_path fora de %s: %r", prefix, media_path)
        return (None, None)
    outbox = getattr(deps, "statics_outbox_dir", None)
    if outbox is not None:
        try:
            if not (Path(outbox) / name).is_file():
                logger.warning("[Template] media_path inexistente: %r", media_path)
                return (None, None)
        except OSError as e:  # noqa: BLE001 — nome esquisito não derruba o save
            logger.warning("[Template] media_path irresolvível (%s): %r", e, media_path)
            return (None, None)
    return (kind, prefix + name)


async def send_template(deps, channel_id: str, *, phone: str, template_name: str,
                        language: str, components, preview_text: str,
                        sent_by_user_id: int | None = None,
                        sent_by_name: str | None = None,
                        media_type: str | None = None,
                        media_path: str | None = None):
    """Send an approved template and persist the operator message (plano 21).

    Returns one of:
      * ``("send_failed", error)`` — the provider send failed (route → 502);
      * ``("save_failed", error)`` — sent but saving the message failed (→ 500);
      * ``("ok", {message, msg_id, phone})`` — success.

    Saving the operator message creates the contact + conversation in this
    channel's inbox, so the new thread appears in the sidebar like a normal first
    send; then it broadcasts ``new_message`` and emits ``message.sent``.

    ``media_type``/``media_path`` (plano 119) gravam o CABEÇALHO de mídia do
    template junto da mensagem, para o histórico mostrar a imagem que saiu em vez
    de só o texto do corpo. Opcionais e validados por :func:`sanitize_media`;
    inválidos ⇒ grava texto puro, como antes.
    """
    outbound = deps.outbound_router
    result = await asyncio.to_thread(
        outbound.send_template, channel_id, phone, template_name,
        lang=language, components=components or None)
    if not result.ok:
        return ("send_failed", result.error)

    msg_id = result.external_msg_id or None
    preview = (preview_text or "").strip() or f"📋 Template: {template_name}"
    m_kind, m_path = sanitize_media(deps, media_type, media_path)
    try:
        msg_data = await asyncio.to_thread(
            deps.agent_handler.save_operator_message, phone, preview,
            status="operator", msg_id=msg_id, channel_id=channel_id,
            sent_by_user_id=sent_by_user_id, sent_by_name=sent_by_name,
            media_type=m_kind, media_path=m_path)
    except Exception as e:  # noqa: BLE001
        return ("save_failed", str(e))

    try:
        await deps.ws_manager.broadcast("new_message", {
            "phone": phone, "channel_id": channel_id, "message": msg_data})
    except Exception:
        pass
    await emit_with_filter("message.sent", {
        "phone": phone, "channel_id": channel_id, "text": preview, "msg_id": msg_id,
        "conversation_id": (msg_data or {}).get("conversation_id"),
        "media_type": m_kind, "media_path": m_path,
        "source": "template", "status": "operator",
        "template_name": template_name, "ts": time.time(),
    })
    return ("ok", {"message": "Template enviado.", "msg_id": msg_id, "phone": phone})


async def create_template(deps, channel_id: str, *, name: str, category: str,
                          language: str, body_text: str, header_text: str | None,
                          footer_text: str | None, body_examples, header_examples,
                          header_format: str | None = None,
                          header_handle: str | None = None,
                          buttons: list | None = None):
    """Create a template on ``channel_id``. Returns ``("ok", data)`` or
    ``("failed", error)``."""
    result = await asyncio.to_thread(
        deps.outbound_router.create_template, channel_id, name,
        category=category, language=language, body_text=body_text,
        header_text=header_text, footer_text=footer_text,
        body_examples=body_examples or None,
        header_examples=header_examples or None,
        header_format=header_format or None,
        header_handle=header_handle or None,
        buttons=buttons or None)
    if not result.get("ok"):
        return ("failed", result.get("error"))
    return ("ok", {
        "message": "Template enviado para aprovação da Meta.",
        "id": result.get("id"), "status": result.get("status"),
        "category": result.get("category"), "name": name,
    })


async def delete_template(deps, channel_id: str, name: str):
    """Delete a template (all languages). Returns ``("ok", data)`` or
    ``("failed", error)``."""
    result = await asyncio.to_thread(
        deps.outbound_router.delete_template, channel_id, name)
    if not result.get("ok"):
        return ("failed", result.get("error"))
    return ("ok", {"message": "Template apagado.", "name": name})
