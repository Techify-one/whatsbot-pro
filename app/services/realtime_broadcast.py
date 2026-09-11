"""Builders for real-time WebSocket payloads (plano 57 · plano 75 F10-live).

Kept free of heavy imports so the payload shape can be unit-tested in isolation
and reused by both inbound save sites (batch text/media in ``messaging_service``
and group-no-mention in ``message_ingest_service``) without a circular import.

A única dependência de banco é o lookup best-effort da citação
(:func:`_attach_quoted`), importado LAZY dentro da função — o módulo continua
importável sem engine e a falha do lookup nunca impede o broadcast.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _attach_quoted(msg: dict, saved: dict) -> None:
    """Hidrata ``msg["quoted"]`` quando a mensagem que chega AO VIVO cita outra.

    A hidratação da F10 (plano 75) só existia na rota de PÁGINA de mensagens; o
    payload de WebSocket copiava ``reply_to_msg_id`` mas nunca o alvo. Com a
    conversa ABERTA na tela do operador, a citação do cliente a uma mensagem
    antiga (fora da janela keyset já carregada) chegava e renderizava "Mensagem
    original indisponível" até dar F5 — exatamente o caso que originou o plano,
    só que ao vivo.

    Mesma FORMA e mesmo corte (``QUOTED_SNIPPET_MAX``) da rota de página: o dict
    vem pronto de :func:`db.repositories.message_repo.get_by_msg_ids`, então o
    balão renderiza igual tendo chegado por carga de página ou por WS.

    Regras (idênticas às da F10):

    * **Escopo obrigatório** — a busca é presa ao ``conversation_id`` da linha
      salva; sem ele (linha legada sem conversa) NÃO se busca nada, para jamais
      arrastar conteúdo de outra conversa para o painel.
    * **Igualdade exata** — nunca normalizar o prefixo ``WAID:`` nem usar
      ``LIKE`` (R12: quatro formatos de ``msg_id`` convivem em produção e cada um
      só casa consigo mesmo).
    * **Best-effort** — qualquer falha só loga; o broadcast segue sem ``quoted``
      e o frontend cai no fallback que já existe.
    * **Custo zero no caminho comum** — sem ``reply_to_msg_id`` (a imensa
      maioria) não há nenhum acesso ao banco.
    """
    target = saved.get("reply_to_msg_id")
    conv_id = saved.get("conversation_id")
    if not target or conv_id is None:
        return
    try:
        from db.repositories import message_repo
        found = message_repo.get_by_msg_ids([target], conversation_id=int(conv_id))
        quoted = found.get(target)
        if quoted:
            msg["quoted"] = quoted
    except Exception:
        logger.exception(
            "Falha ao hidratar a citação %s da conversa %s no broadcast", target, conv_id)


def build_inbound_saved_message(saved: dict,
                                *, supersedes: list[str] | None = None) -> dict:
    """Build the AUTHORITATIVE ``new_message.message`` payload emitted AFTER the
    inbound INSERT (plano 57).

    Fecha a janela "broadcast-antes-do-save": o ``new_message`` do ingest sai no
    t=0 (antes do INSERT, sem ``_id``); este sai DEPOIS, carregando a identidade
    real da linha (``_id``/``msg_id``/``ts`` do banco). O frontend reconcilia no
    lugar com a cópia otimista do t=0 via ``msg_id``/``_id`` (dedup), então a
    entrega dupla é inofensiva.

    ``supersedes`` = a lista de ``msg_id`` que um batch combinou nesta única linha.
    ⚠️ **Nenhum call site do core o passa desde o plano 146**: o batch de texto
    deixou de mesclar e grava uma linha por mensagem, cada uma reconciliando com a
    própria bolha otimista pelo seu ``msg_id``. O parâmetro é MANTIDO de propósito
    — um rollback do core volta a produzir a linha combinada, e um cliente sem esta
    maquinaria mostraria bolha duplicada diante dele. Quando presente, o frontend o
    usa para colapsar as bolhas otimistas das mensagens anteriores na linha
    combinada; o ``msg_id`` da própria linha é excluído (ele reconcilia no lugar,
    preservando a posição da bolha).

    Quando a linha CITA outra mensagem, o alvo também vai hidratado em
    ``quoted`` (plano 75 — ver :func:`_attach_quoted`); é o único ponto em que
    esta função toca o banco, e só para mensagens com ``reply_to_msg_id``.

    Args:
        saved: o dict retornado por ``message_repo.add`` / ``ContactMemory.add_message``
            (tem ``id``/``role``/``content``/``ts``/``msg_id``/``conversation_id``/…).
        supersedes: msg_ids combinados no batch (opcional).

    Returns:
        O dict ``message`` pronto para ``ws_manager.broadcast("new_message", …)``.
    """
    msg: dict = {
        "role": saved.get("role", "user"),
        "content": saved.get("content"),
        "ts": saved.get("ts"),
        "authoritative": True,
    }
    if saved.get("msg_id"):
        msg["msg_id"] = saved["msg_id"]
    if saved.get("id") is not None:
        msg["_id"] = saved["id"]
    if saved.get("conversation_id") is not None:
        msg["conversation_id"] = saved["conversation_id"]
    if saved.get("media_type"):
        msg["media_type"] = saved["media_type"]
        msg["media_path"] = saved.get("media_path")
        # plano 87: a legenda verbatim do cliente, para o balão não ter que
        # adivinhá-la dentro do ``content`` composto. Só sai quando existe.
        if saved.get("media_caption"):
            msg["media_caption"] = saved["media_caption"]
    if saved.get("reply_to_msg_id"):
        msg["reply_to_msg_id"] = saved["reply_to_msg_id"]
        # plano 75 (F10 ao vivo): manda também o ALVO da citação. Um único lookup
        # indexado (``idx_msg_id``), só para mensagens que de fato citam alguém.
        _attach_quoted(msg, saved)
    if supersedes:
        own = saved.get("msg_id")
        extra = [m for m in supersedes if m and m != own]
        if extra:
            msg["supersedes"] = extra
    return msg
