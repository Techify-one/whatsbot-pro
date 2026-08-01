"""Plano 75 — a citação que chega AO VIVO também vem hidratada (defeito B).

A F10 hidratava ``quoted`` só na rota de PÁGINA de mensagens. O payload de
WebSocket (``build_inbound_saved_message``) copiava ``reply_to_msg_id`` e mais
nada, então — com a conversa ABERTA na tela — a mensagem do cliente citando algo
antigo chegava ao vivo e o balão mostrava "Mensagem original indisponível" até
recarregar a página. Este arquivo trava o conserto:

* alvo existente ⇒ o payload do broadcast carrega ``quoted`` com o trecho;
* alvo inexistente ⇒ broadcast normal, sem ``quoted`` e sem exceção;
* mensagem SEM citação ⇒ nenhum lookup extra (custo zero no caminho comum);
* alvo em OUTRA conversa ⇒ não vaza (escopo obrigatório);
* ``msg_id`` com prefixo ``WAID:`` casa por IGUALDADE EXATA (R12);
* falha do banco ⇒ o broadcast continua (best-effort).

    venv/bin/python -m pytest tests/integration/test_quoted_message_realtime_payload.py -q
"""

from __future__ import annotations

import time

from app.services import realtime_broadcast
from app.services.realtime_broadcast import build_inbound_saved_message
from db.repositories import contact_repo, conversation_repo, message_repo

# Forma real do id importado do Chatwoot — de propósito, para travar a regra de
# igualdade exata (proibido normalizar o prefixo).
TARGET_MSG_ID = "WAID:wamid.HBgNNTUxMTk4ODg4ODg4OBUCABIYFjNFQjA3Rjc3RUVFMkJCREIzODlFOTk="
TARGET_BODY = (
    "CLIENTE EXEMPLO 2 — contrato 000001, plano 300 MEGA, vencimento dia 5, endereço "
    "Avenida Exemplo 200, ponto de referência em frente ao mercado municipal"
)


def _mk_conversation(phone: str) -> tuple[int, int]:
    """Contato + conversa. Devolve ``(contact_id, conversation_id)``."""
    contact = contact_repo.get_or_create(phone)
    conv, _created = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net")
    return contact["id"], conv["id"]


def _inbound(contact_id: int, conv_id: int, *, reply_to: str | None) -> dict:
    """Salva a mensagem que CHEGA (o que o webhook faria) e devolve a linha."""
    return message_repo.add(
        contact_id, "user", "e o que acontece com esse contrato?",
        msg_id=f"live-{conv_id}-{int(time.time() * 1000)}",
        reply_to_msg_id=reply_to, conversation_id=conv_id)


def test_live_reply_carries_quoted(seed):
    """O caso do plano, ao vivo: alvo antigo ⇒ ``quoted`` no payload do WS."""
    contact_id, conv_id = _mk_conversation("5511900000771")
    target = message_repo.add(contact_id, "user", TARGET_BODY,
                              msg_id=TARGET_MSG_ID, conversation_id=conv_id,
                              ts=time.time() - 86400)
    saved = _inbound(contact_id, conv_id, reply_to=TARGET_MSG_ID)

    msg = build_inbound_saved_message(saved)

    assert msg["reply_to_msg_id"] == TARGET_MSG_ID
    quoted = msg.get("quoted")
    assert quoted, "a mensagem ao vivo precisa chegar com a citação hidratada"
    assert quoted["msg_id"] == TARGET_MSG_ID
    assert quoted["_id"] == target["id"]
    assert quoted["role"] == "user"
    assert quoted["content"], "conteúdo vazio era exatamente o bug"
    assert TARGET_BODY.startswith(quoted["content"])


def test_live_quoted_has_same_shape_as_the_page_route(seed):
    """Formato IDÊNTICO ao da rota de página — senão o balão renderia diferente
    conforme a mensagem tenha vindo por carga de página ou por WebSocket."""
    contact_id, conv_id = _mk_conversation("5511900000772")
    message_repo.add(contact_id, "user", TARGET_BODY, msg_id=TARGET_MSG_ID,
                     conversation_id=conv_id, ts=time.time() - 86400)
    saved = _inbound(contact_id, conv_id, reply_to=TARGET_MSG_ID)

    quoted = build_inbound_saved_message(saved)["quoted"]
    from_page = message_repo.get_by_msg_ids([TARGET_MSG_ID],
                                            conversation_id=conv_id)[TARGET_MSG_ID]

    assert quoted == from_page
    assert set(quoted) == {"_id", "msg_id", "role", "content", "media_type",
                           "media_caption", "status", "sent_by_name"}
    assert len(TARGET_BODY) > message_repo.QUOTED_SNIPPET_MAX, "fixture precisa estourar o teto"
    assert len(quoted["content"]) == message_repo.QUOTED_SNIPPET_MAX
    assert "media_path" not in quoted and "raw" not in quoted


def test_missing_target_broadcasts_without_quoted(seed):
    """Alvo apagado / nunca recebido: broadcast normal, sem inventar citação."""
    contact_id, conv_id = _mk_conversation("5511900000773")
    saved = _inbound(contact_id, conv_id, reply_to="WAID:wamid.NAO-EXISTE-AO-VIVO")

    msg = build_inbound_saved_message(saved)

    assert msg["reply_to_msg_id"] == "WAID:wamid.NAO-EXISTE-AO-VIVO"
    assert "quoted" not in msg
    assert msg["authoritative"] is True, "o broadcast continua íntegro"


def test_message_without_reply_does_no_lookup(seed, monkeypatch):
    """Custo zero no caminho comum: sem citação, o banco nem é consultado."""
    contact_id, conv_id = _mk_conversation("5511900000774")
    saved = _inbound(contact_id, conv_id, reply_to=None)

    calls: list[tuple] = []
    original = message_repo.get_by_msg_ids

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(message_repo, "get_by_msg_ids", _spy)
    msg = build_inbound_saved_message(saved)

    assert calls == [], "mensagem sem citação não pode custar uma query"
    assert "quoted" not in msg and "reply_to_msg_id" not in msg


def test_reply_does_exactly_one_lookup(seed, monkeypatch):
    """Uma citação ⇒ UMA query (não uma por campo do payload)."""
    contact_id, conv_id = _mk_conversation("5511900000775")
    message_repo.add(contact_id, "user", TARGET_BODY, msg_id=TARGET_MSG_ID,
                     conversation_id=conv_id)
    saved = _inbound(contact_id, conv_id, reply_to=TARGET_MSG_ID)

    calls: list[tuple] = []
    original = message_repo.get_by_msg_ids

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(message_repo, "get_by_msg_ids", _spy)
    assert build_inbound_saved_message(saved).get("quoted")
    assert len(calls) == 1
    assert calls[0][1].get("conversation_id") == conv_id, "a busca é escopada"


def test_other_conversation_does_not_leak(seed):
    """Escopo obrigatório: o mesmo ``msg_id`` em outra conversa não é hidratado."""
    other_contact, other_conv = _mk_conversation("5511900000776")
    leaked_id = "WAID:wamid.SEGREDO-AO-VIVO"
    message_repo.add(other_contact, "user", "dados sigilosos do outro cliente",
                     msg_id=leaked_id, conversation_id=other_conv)

    contact_id, conv_id = _mk_conversation("5511900000777")
    saved = _inbound(contact_id, conv_id, reply_to=leaked_id)

    msg = build_inbound_saved_message(saved)
    assert "quoted" not in msg, "citação de outra conversa jamais pode vazar ao vivo"


def test_no_conversation_scope_skips_hydration(seed):
    """Linha legada sem ``conversation_id``: sem escopo, não se busca nada."""
    contact_id, conv_id = _mk_conversation("5511900000778")
    message_repo.add(contact_id, "user", TARGET_BODY, msg_id=TARGET_MSG_ID,
                     conversation_id=conv_id)
    saved = message_repo.add(contact_id, "user", "sem conversa",
                             msg_id="live-sem-conversa",
                             reply_to_msg_id=TARGET_MSG_ID)

    msg = build_inbound_saved_message(saved)
    assert msg["reply_to_msg_id"] == TARGET_MSG_ID
    assert "quoted" not in msg


def test_waid_prefix_matches_exactly(seed):
    """R12: quatro formatos de id convivem; cada um só casa consigo mesmo."""
    contact_id, conv_id = _mk_conversation("5511900000779")
    message_repo.add(contact_id, "user", "original importado do Chatwoot",
                     msg_id=TARGET_MSG_ID, conversation_id=conv_id)

    hit = build_inbound_saved_message(
        _inbound(contact_id, conv_id, reply_to=TARGET_MSG_ID))
    assert hit["quoted"]["content"] == "original importado do Chatwoot"

    stripped = TARGET_MSG_ID.split("WAID:", 1)[1]
    miss = build_inbound_saved_message(
        _inbound(contact_id, conv_id, reply_to=stripped))
    assert "quoted" not in miss, "normalizar o prefixo WAID: é proibido"


def test_lookup_failure_never_blocks_the_broadcast(seed, monkeypatch):
    """Best-effort: banco fora do ar durante a hidratação ⇒ payload sai igual,
    só sem ``quoted`` (o frontend já tem o fallback)."""
    contact_id, conv_id = _mk_conversation("5511900000780")
    message_repo.add(contact_id, "user", TARGET_BODY, msg_id=TARGET_MSG_ID,
                     conversation_id=conv_id)
    saved = _inbound(contact_id, conv_id, reply_to=TARGET_MSG_ID)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("conexão perdida")

    monkeypatch.setattr(message_repo, "get_by_msg_ids", _boom)
    msg = build_inbound_saved_message(saved)

    assert "quoted" not in msg
    assert msg["_id"] == saved["id"] and msg["authoritative"] is True


def test_helper_is_defensive_with_garbage_input():
    """``_attach_quoted`` nunca levanta — nem sem banco inicializado."""
    payload: dict = {}
    realtime_broadcast._attach_quoted(payload, {})
    realtime_broadcast._attach_quoted(payload, {"reply_to_msg_id": "X"})
    realtime_broadcast._attach_quoted(
        payload, {"reply_to_msg_id": "X", "conversation_id": "nao-numero"})
    assert payload == {}
