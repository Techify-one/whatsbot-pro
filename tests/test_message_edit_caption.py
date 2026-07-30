"""Plano 87 — a legenda editada não pode ficar congelada na coluna.

``mediaCaptionOf`` (web/static/js/services/messageView.js) dá precedência
ABSOLUTA a ``messages.media_caption`` sobre o ``content``. Então, quando o
cliente edita a legenda de uma mídia do lado dele (WhatsApp/Telegram → evento
``kind="edited"``, espelhado em server/routes/channel_webhook.py via
``message_repo.mark_edited``), atualizar só o ``content`` faz o balão desenhar a
legenda ANTIGA com o selo "editada" ao lado — e o erro sobrevive ao F5, porque a
coluna é o que fica no banco. Foi exatamente a regressão que o plano 87
introduziu ao dar precedência à coluna sem ensinar a edição a mexer nela.

Os três casos que DEFINEM o contrato:
  * mídia COM legenda        → a coluna acompanha o novo texto
  * mídia SEM legenda (NULL) → continua NULL (o fallback por prefixo fica intacto)
  * texto puro (NULL)        → continua NULL (edição de texto não inventa legenda)

    venv/bin/python -m pytest tests/test_message_edit_caption.py -q
"""

from __future__ import annotations

import pytest

from db.repositories import contact_repo, message_repo


@pytest.fixture
def contact_id(_engine_ready) -> int:
    return contact_repo.get_or_create("5511900000087")["id"]


def _add(contact_id: int, content: str, **kw) -> int:
    """Insere e devolve o id de banco da linha."""
    saved = message_repo.add(contact_id, "user", content, **kw)
    db_id = saved.get("id")
    assert db_id, f"message_repo.add não devolveu id: {saved}"
    return int(db_id)


def _caption_of(db_id: int):
    """A coluna crua — ``_row_to_dict`` omite a chave quando é NULL/vazia."""
    return (message_repo.get_by_db_id(db_id) or {}).get("media_caption")


def test_edit_updates_caption_of_media_that_had_one(contact_id):
    """O caso da regressão: foto com legenda 'R$ 100' editada para 'R$ 1000'."""
    db_id = _add(contact_id, "R$ 100", media_type="image",
                 media_path="statics/x.jpg", media_caption="R$ 100")

    edited_ts = message_repo.mark_edited(db_id, "R$ 1000")

    assert edited_ts is not None
    row = message_repo.get_by_db_id(db_id)
    assert row["content"] == "R$ 1000"
    assert row["media_caption"] == "R$ 1000", (
        "a coluna ficou na legenda antiga — o balão desenharia 'R$ 100' com o "
        "selo 'editada' ao lado, e o F5 não corrigiria")


def test_edit_does_not_invent_a_caption_for_media_without_one(contact_id):
    """Mídia sem legenda (NULL) segue NULL: o fallback por prefixo é o dono."""
    db_id = _add(contact_id, "[Áudio recebido]", media_type="audio",
                 media_path="statics/x.ogg")
    assert _caption_of(db_id) is None

    message_repo.mark_edited(db_id, "[Transcrição do áudio]: bom dia")

    assert _caption_of(db_id) is None
    assert message_repo.get_by_db_id(db_id)["content"] == "[Transcrição do áudio]: bom dia"


def test_edit_of_plain_text_never_gains_a_caption(contact_id):
    """Edição de mensagem de TEXTO (o fluxo do operador) não cria legenda."""
    db_id = _add(contact_id, "bom dia")

    message_repo.mark_edited(db_id, "boa tarde")

    assert _caption_of(db_id) is None
    assert message_repo.get_by_db_id(db_id)["content"] == "boa tarde"
