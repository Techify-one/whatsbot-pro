"""Ações de um retorno — executa cada mensagem reusando o core (plano 76 F5).

Regras travadas:

* **Canal autoritativo**: o canal é sempre re-derivado da conversa a cada disparo
  (`evalctx.load_target`), nunca o snapshot do controle — escrever na inbox errada CRIARIA
  uma conversa nova em vez de anexar à original (precedente `agendamento_retorno`).
* **Gate da janela de 24h (D3)**: `outbound_router.session_open(...) == False` ⇒ a ação é
  SUBSTITUÍDA por uma nota privada de aviso ao atendente. Sem templates HSM no MVP.
* **`ia_responde_agora`** é o equivalente honesto do `@Bia` do Nexus: chama
  `agent_handler.aprocess_message` (a instrução do retorno é o turno sintético do usuário) e
  ENVIA a resposta ao cliente — reuso da lógica de `_run_private_ai`
  (`server/routes/contacts.py`). NÃO re-checa o interruptor global `auto_reply` (a configuração
  ativa É a intenção de responder — decisão do usuário), mas checa SEMPRE os gates por
  conversa com dados frescos: IA da conversa desligada, humano assumido sem agente, ou a
  tag `transferido_atendente` ⇒ o retorno vira nota privada em vez de falar com o cliente.
  Ao acionar a IA, o retorno registra ANTES do disparo uma nota privada com a instrução
  INTEIRA. A ordem não é cosmética: `run_turn` só entrega o texto ao modelo através do
  HISTÓRICO, e com `save_user_message=False` o turno sintético não é salvo — então é a
  nota (lida como `[Nota privada do operador]: …`) que carrega a instrução até o LLM,
  exatamente como o "@ia" privado do core (`_run_private_ai` salva a nota e só então roda
  a IA). Gravada depois, a instrução não chegava ao modelo em chamada nenhuma — ele
  respondia apenas pelo histórico — e o atendente lia o pedido só após a resposta. A nota
  é também o único rastro no fio da conversa de que o disparo veio do retorno; longa, é
  recolhida pelo painel num chip de uma linha (`isCollapsibleCard`), com seta pra expandir.
* **Supressão de eco (`_mark_echo`)**: o core só ignora o eco do provedor daquilo que ELE
  mandou (`state.recently_sent`, populado em `messaging_service._send_reply`). Como aqui o
  envio vai DIRETO ao `outbound_router`, sem esse registro, o `_ingest_echo` salvava a mesma
  mensagem uma 2ª vez com `status='operator'` (bolha "Manual" duplicada em canais que ecoam,
  ex.: Messenger/GOWA). Todo envio deste módulo registra o eco ANTES de sair (pelo corpo) e o
  id externo DEPOIS (cobre mídia sem legenda, que o eco não casaria pelo texto).
* O dispatcher roda em thread; toda coroutine do core vai ao loop principal por
  `run_coroutine_threadsafe` (`_run_on_loop`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from plugins.context import broadcast, get_channel_runtime, get_deps

from .settings import JANELA_24H_MESSAGE_DEFAULT

logger = logging.getLogger("plugin.retornos")

PLUGIN_ID = "retornos"
# Autor exibido no card das notas privadas escritas por este plugin.
NOTE_AUTHOR = "Retorno Automático"

_LOOP: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Registra o loop principal (chamado do laço async do lifecycle)."""
    global _LOOP
    if loop is not None:
        _LOOP = loop


def _run_on_loop(coro, *, timeout: float = 120.0):
    """Executa uma coroutine do core no loop principal, a partir da thread do ciclo."""
    if _LOOP is None:
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("loop principal indisponível (runtime não conectado)")
    fut = asyncio.run_coroutine_threadsafe(coro, _LOOP)
    return fut.result(timeout=timeout)


# ── Render de placeholders ────────────────────────────────────────────────────

class _SafeDict(dict):
    def __missing__(self, key):  # placeholder desconhecido fica literal
        return "{" + key + "}"


def render(template: str, *, contact_name: str | None, controle: dict | None = None,
           retorno: dict | None = None) -> str:
    nome = (contact_name or "").strip() or "cliente"
    values = _SafeDict(
        cliente=nome,
        primeiro_nome=nome.split(" ")[0],
        disparos=int((controle or {}).get("disparos_enviados") or 0),
        proximo_disparo=int((controle or {}).get("disparos_enviados") or 0) + 1,
        retorno=(retorno or {}).get("nome") or "",
        # Alias legado: mensagens escritas quando o retorno se chamava "passo" continuam
        # renderizando o mesmo valor em vez de virar literal "{passo}".
        passo=(retorno or {}).get("nome") or "",
    )
    try:
        return str(template or "").format_map(values)
    except Exception:  # noqa: BLE001 — template quebrado nunca impede o disparo
        return str(template or "")


# ── Primitivas ────────────────────────────────────────────────────────────────

def post_private_note(*, phone: str, channel_id: str, body: str) -> dict:
    """Escreve uma nota privada (painel-only) no atendimento + broadcast ao vivo."""
    deps = get_deps()
    ah = getattr(deps, "agent_handler", None) if deps else None
    if ah is None:
        raise RuntimeError("agent_handler indisponível (runtime não conectado)")
    cm = ah._get_contact(phone, channel_id=channel_id)
    saved = cm.add_message("private_note", body, sent_by_name=NOTE_AUTHOR, reopen=False)
    try:
        broadcast("new_message", {
            "phone": phone,
            "channel_id": channel_id,
            "message": {
                "role": "private_note", "content": body,
                "ts": saved.get("ts"), "status": None,
                "conversation_id": saved.get("conversation_id"),
                "_id": saved.get("id"), "msg_id": saved.get("msg_id"),
                "sent_by_name": NOTE_AUTHOR,
            },
        })
    except Exception:  # noqa: BLE001 — broadcast nunca quebra o disparo
        logger.debug("retornos: broadcast new_message falhou")
    return saved


def _echo_state():
    """``state.messaging`` do core (caches de dedup do eco) ou ``None``."""
    deps = get_deps()
    return getattr(deps, "state", None) if deps else None


def _mark_echo_text(*, channel_id: str, phone: str, body: str) -> str | None:
    """Registra o texto que estamos prestes a enviar em ``state.recently_sent``.

    Mesma chave/janela que o `_ingest_echo` consome. Devolve a chave para o caller
    poder desfazer se o envio falhar (espelha `messaging_service`).
    """
    text = (body or "").strip()
    state = _echo_state()
    if state is None or not text:
        return None
    key = f"{channel_id}:{phone}:{text[:120]}"
    try:
        state.recently_sent[key] = time.time()
    except Exception:  # noqa: BLE001 — dedup é best-effort, nunca impede o envio
        return None
    return key


def _unmark_echo_text(key: str | None) -> None:
    state = _echo_state()
    if state is None or not key:
        return
    try:
        state.recently_sent.pop(key, None)
    except Exception:  # noqa: BLE001
        pass


def _mark_echo_id(*, channel_id: str, msg_id: str | None) -> None:
    """Marca o id externo como já ingerido — cobre o eco de mídia sem legenda."""
    state = _echo_state()
    if state is None or not msg_id:
        return
    try:
        state.processed_messages.add(f"{channel_id}:{msg_id}")
    except Exception:  # noqa: BLE001
        pass


def _send_text(*, phone: str, channel_id: str, body: str) -> str | None:
    _registry, outbound_router, _ingest = get_channel_runtime()
    if outbound_router is None:
        raise RuntimeError("outbound_router indisponível")
    echo_key = _mark_echo_text(channel_id=channel_id, phone=phone, body=body)
    try:
        res = outbound_router.send_text(channel_id, phone, body)
    except Exception:
        _unmark_echo_text(echo_key)
        raise
    if not getattr(res, "ok", False):
        _unmark_echo_text(echo_key)
        raise RuntimeError(getattr(res, "error", None) or "envio falhou")
    msg_id = getattr(res, "external_msg_id", None)
    _mark_echo_id(channel_id=channel_id, msg_id=msg_id)
    return msg_id


def _send_media(*, phone: str, channel_id: str, kind: str, alvo: str, caption: str,
                filename: str | None) -> str | None:
    _registry, outbound_router, _ingest = get_channel_runtime()
    if outbound_router is None:
        raise RuntimeError("outbound_router indisponível")
    echo_key = _mark_echo_text(channel_id=channel_id, phone=phone, body=caption)
    try:
        res = outbound_router.send_media(channel_id, phone, kind, alvo,
                                         caption=caption, filename=filename)
    except Exception:
        _unmark_echo_text(echo_key)
        raise
    if not getattr(res, "ok", False):
        _unmark_echo_text(echo_key)
        raise RuntimeError(getattr(res, "error", None) or "envio de mídia falhou")
    msg_id = getattr(res, "external_msg_id", None)
    _mark_echo_id(channel_id=channel_id, msg_id=msg_id)
    return msg_id


def _save_outbound(*, phone: str, channel_id: str, body: str, msg_id: str | None,
                   media_type: str | None = None, media_path: str | None = None) -> None:
    deps = get_deps()
    ah = getattr(deps, "agent_handler", None) if deps else None
    if ah is None:
        return
    cm = ah._get_contact(phone, channel_id=channel_id)
    saved = cm.add_message("assistant", body, status="sent", msg_id=msg_id,
                           media_type=media_type, media_path=media_path,
                           sent_by_name=NOTE_AUTHOR, reopen=False)
    try:
        broadcast("new_message", {
            "phone": phone, "channel_id": channel_id,
            "message": {
                "role": "assistant", "content": body, "ts": saved.get("ts"),
                "status": "sent", "conversation_id": saved.get("conversation_id"),
                "_id": saved.get("id"), "msg_id": saved.get("msg_id"),
                "media_type": media_type, "media_path": media_path,
                "sent_by_name": NOTE_AUTHOR,
            },
        })
    except Exception:  # noqa: BLE001
        logger.debug("retornos: broadcast da mensagem enviada falhou")


# ── Gates ─────────────────────────────────────────────────────────────────────

def session_open(*, channel_id: str, conversation_id: int) -> bool:
    """A janela de texto livre do canal está aberta? (GOWA/Telegram: sempre)."""
    _registry, outbound_router, _ingest = get_channel_runtime()
    if outbound_router is None:
        return False
    try:
        from db.repositories import message_repo
        last_in = message_repo.last_inbound_ts(conversation_id=int(conversation_id))
    except Exception:  # noqa: BLE001
        last_in = None
    try:
        return bool(outbound_router.session_open(channel_id, last_in))
    except Exception:  # noqa: BLE001
        logger.debug("retornos: session_open falhou — assumindo fechada", exc_info=True)
        return False


def ai_allowed(*, conv: dict, contact: dict | None) -> tuple[bool, str]:
    """A IA pode falar nesta conversa AGORA? (dados frescos, sem o gate global)."""
    if int(conv.get("ai_active") or 0) != 1:
        return False, "IA desligada na conversa"
    if conv.get("assignee_user_id") and not conv.get("active_agent_key"):
        return False, "conversa assumida por um atendente"
    try:
        from agent.tools.transfer_to_human import TRANSFER_TAG
        from db.repositories import tag_repo
        if contact and contact.get("id") is not None:
            if TRANSFER_TAG in (tag_repo.get_contact_tags(int(contact["id"])) or []):
                return False, "conversa transferida para atendente humano"
    except Exception:  # noqa: BLE001
        logger.debug("retornos: checagem da tag de transferência falhou", exc_info=True)
    return True, ""


# ── IA responde agora ─────────────────────────────────────────────────────────

async def _ia_coro(*, phone: str, instrucao: str, channel_id: str) -> int:
    """Um turno do AGNO com a instrução do retorno, enviando as partes ao cliente.

    A instrução chega ao modelo pela NOTA PRIVADA que o chamador grava logo antes (o
    histórico é o único caminho: `run_turn` descarta o texto quando
    `save_user_message=False`). Ela continua sendo passada aqui por fidelidade ao
    contrato de `aprocess_message` e para o dia em que o core usar o turno sintético.
    """
    deps = get_deps()
    ah = getattr(deps, "agent_handler", None) if deps else None
    if ah is None:
        raise RuntimeError("agent_handler indisponível")

    result = await ah.aprocess_message(
        phone, instrucao, save_user_message=False, save_response=False,
        channel_id=channel_id)
    reply = (getattr(result, "reply", "") or "").strip()
    if not reply:
        return 0

    from channels import ai_settings
    from server.helpers import parse_split_reply
    split = True
    try:
        split = bool(ai_settings.value(channel_id, "split_messages", True))
    except Exception:  # noqa: BLE001
        pass
    parts = parse_split_reply(reply) if split else [reply]

    enviadas = 0
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        msg_id = await asyncio.to_thread(
            _send_text, phone=phone, channel_id=channel_id, body=part)
        await asyncio.to_thread(
            ah.save_assistant_message, phone, part, msg_id=msg_id, status="sent",
            channel_id=channel_id, agent_key=getattr(result, "agent_key", None))
        enviadas += 1
        try:
            broadcast("new_message", {
                "phone": phone, "channel_id": channel_id,
                "message": {"role": "assistant", "content": part, "ts": time.time(),
                            "status": "sent", "msg_id": msg_id},
            })
        except Exception:  # noqa: BLE001
            pass
    return enviadas


# ── Executor único de uma mensagem do retorno ───────────────────────────────────

_MEDIA_KINDS = {"image": "image", "audio": "audio", "video": "video", "document": "document"}


def _media_target(msg: dict) -> tuple[str, str | None]:
    """``(alvo, filename)`` — URL pública se houver, senão o caminho local do arquivo."""
    url = (msg.get("media_url") or "").strip()
    path = (msg.get("media_path") or "").strip()
    filename = (msg.get("file_name") or "").strip() or None
    if url:
        return url, filename
    if not path:
        raise RuntimeError("mensagem de mídia sem arquivo nem URL")
    if not os.path.isabs(path) and not os.path.exists(path):
        # Caminho relativo salvo pela UI (ex.: statics/outbox/x.jpg) → resolve na raiz.
        raiz = os.getcwd()
        alt = os.path.join(raiz, path)
        if os.path.exists(alt):
            path = alt
    if not os.path.exists(path):
        raise RuntimeError(f"arquivo de mídia não encontrado: {path}")
    return path, filename or os.path.basename(path)


def aviso_janela_24h(*, contact_name: str, controle: dict | None,
                     retorno: dict | None, cfg=None) -> str:
    """Texto da nota privada de janela fechada, já com os placeholders resolvidos.

    O texto é a setting `janela_24h_message` (editável no modal "Configurar" do plugin);
    em branco significa "não me avise" e o caller pula a nota. `cfg` é passado pelo
    dispatcher (já carregado no ciclo); ausente, é lido na hora — settings ruins nunca
    derrubam o disparo, então cai no default de fábrica.
    """
    if cfg is None:
        try:
            from .dispatcher import load_settings  # import tardio: dispatcher importa actions
            cfg = load_settings()
        except Exception:  # noqa: BLE001
            logger.debug("retornos: settings indisponíveis — usando o aviso padrão",
                         exc_info=True)
    bruto = getattr(cfg, "janela_24h_message", None)
    if bruto is None:
        bruto = JANELA_24H_MESSAGE_DEFAULT
    if not str(bruto).strip():
        return ""
    return render(str(bruto), contact_name=contact_name, controle=controle, retorno=retorno)


def execute(msg: dict, *, controle: dict, configuracao: dict, conv: dict,
            contact: dict | None, retorno: dict | None = None, cfg=None) -> dict:
    """Executa UMA mensagem do retorno. Devolve ``{ok, tipo, detalhe, substituida}``.

    Nunca deixa uma falha de envio derrubar as outras mensagens do retorno — o dispatcher
    conta o disparo se ao menos uma sair (D6).
    """
    phone = (conv.get("contact_phone") or controle.get("phone") or "").strip()
    channel_id = conv.get("channel_id") or controle.get("channel_id") or "default"
    conv_id = int(conv.get("id") or controle.get("conversation_id") or 0)
    nome = (conv.get("contact_name") or (contact or {}).get("name")
            or controle.get("contact_name") or "")
    tipo = str(msg.get("tipo") or "private_note")
    corpo = render(msg.get("content") or "", contact_name=nome, controle=controle,
                   retorno=retorno)

    if not phone:
        return {"ok": False, "tipo": tipo, "detalhe": "conversa sem telefone"}

    # Nota privada nunca sai para o cliente → sem gate de janela.
    if tipo == "private_note":
        post_private_note(phone=phone, channel_id=channel_id, body=corpo)
        return {"ok": True, "tipo": tipo, "detalhe": "nota privada"}

    # D3 — fora da janela de 24h, avisa o atendente em vez de falhar no provedor.
    if not session_open(channel_id=channel_id, conversation_id=conv_id):
        aviso = aviso_janela_24h(contact_name=nome, controle=controle, retorno=retorno, cfg=cfg)
        if aviso:
            post_private_note(phone=phone, channel_id=channel_id, body=aviso)
        return {"ok": True, "tipo": tipo, "detalhe": "janela_24h", "substituida": True}

    if tipo == "ia_responde_agora":
        permitido, motivo = ai_allowed(conv=conv, contact=contact)
        if not permitido:
            aviso = (f"🤖 O retorno automático ia acionar a IA, mas não agiu: {motivo}. "
                     f"Instrução do retorno: {corpo}")
            post_private_note(phone=phone, channel_id=channel_id, body=aviso)
            return {"ok": True, "tipo": tipo, "detalhe": f"ia_bloqueada: {motivo}",
                    "substituida": True}
        # A nota vem ANTES do disparo, e não é ordem cosmética: `private_note` entra no
        # contexto do LLM (não está na lista-negra de roles do message_repo) e é o ÚNICO
        # veículo da instrução — `run_turn` descarta o texto do turno sintético quando
        # `save_user_message=False`. Gravada depois, a instrução não alcançava a chamada
        # que ela anuncia (a IA respondia só pelo histórico) e o atendente lia o pedido
        # depois da resposta. Mesmo desenho do "@ia" privado do core
        # (`server/routes/contacts.py::_run_private_ai`): salva a nota, então roda a IA.
        # A instrução vai INTEIRA (o painel recolhe a nota longa num chip de uma
        # linha, com seta pra expandir) — cortada, o atendente não conseguiria
        # auditar o que de fato foi pedido à IA.
        post_private_note(
            phone=phone, channel_id=channel_id,
            body=f"🤖 Retorno automático acionou a IA. Instrução: {corpo}")
        enviadas = _run_on_loop(_ia_coro(phone=phone, instrucao=corpo,
                                         channel_id=channel_id))
        if not enviadas:
            post_private_note(
                phone=phone, channel_id=channel_id,
                body="🤖 A IA não respondeu ao acionamento do retorno automático.")
            return {"ok": False, "tipo": tipo, "detalhe": "a IA não produziu resposta"}
        return {"ok": True, "tipo": tipo, "detalhe": f"IA respondeu ({enviadas} parte(s))"}

    if tipo == "text":
        msg_id = _send_text(phone=phone, channel_id=channel_id, body=corpo)
        _save_outbound(phone=phone, channel_id=channel_id, body=corpo, msg_id=msg_id)
        return {"ok": True, "tipo": tipo, "detalhe": "texto enviado"}

    kind = _MEDIA_KINDS.get(tipo)
    if kind:
        alvo, filename = _media_target(msg)
        msg_id = _send_media(phone=phone, channel_id=channel_id, kind=kind, alvo=alvo,
                             caption=corpo, filename=filename)
        local = (msg.get("media_path") or "").strip() or None
        _save_outbound(phone=phone, channel_id=channel_id, body=corpo, msg_id=msg_id,
                       media_type=kind, media_path=local)
        return {"ok": True, "tipo": tipo, "detalhe": f"{kind} enviado"}

    return {"ok": False, "tipo": tipo, "detalhe": f"tipo de mensagem desconhecido: {tipo}"}
