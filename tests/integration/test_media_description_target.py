"""Plano 133 — a descrição/transcrição da mídia é colada na LINHA DA MÍDIA.

O laço de mídia do batch salva uma linha por mídia e guarda o retorno do
``INSERT``, mas descartava essa identidade na hora de colar a descrição: chamava
``agent_handler.update_last_user_message_content``, que **reprocura** a última
mensagem ``role='user'`` da conversa por ``ORDER BY ts DESC`` — **sem exigir
``media_type``**.

Enquanto o ``ts`` era o relógio do INSERT a mídia (salva por último) sempre
vencia e o alvo saía certo por acidente. O **plano 129** passou a gravar o ``ts``
REAL do provedor, e o acaso acabou: qualquer linha ``role='user'`` da conversa
com ``ts`` maior — tipicamente um texto entregue no mesmo segundo — vira o alvo.
Efeito triplo em produção (5 linhas, 3 conversas, 19–20/08/2026):

1. o prefixo interno ``[Descrição da imagem]:`` vira **bolha pública** (a linha
   de texto tem ``media_type=NULL``, então o painel não o esconde);
2. o **texto original do cliente é destruído** (o ``UPDATE`` troca o ``content``
   inteiro);
3. a linha da imagem fica **sem** a descrição — turnos futuros perdem a foto.

Este arquivo é a rede da correção:

* ``test_image_description_lands_on_image_row`` — o caso da produção (texto com
  ``ts`` posterior + imagem com ``ts`` anterior). **Vermelho antes da F1.**
* ``test_audio_transcription_lands_on_audio_row`` — o mesmo gatilho no áudio.
* ``test_two_media_each_keep_their_own_description`` — duas imagens: cada
  descrição na SUA linha (hoje colapsam numa só, a 2ª sobrescrevendo a 1ª).

Driver: a rota GOWA real (``POST /api/webhook/gowa/default``), com o
``payload.timestamp`` virando ``event.ts`` verbatim — o mesmo molde de
[test_inbound_provider_ts_ordering.py](test_inbound_provider_ts_ordering.py).

    WHATSBOT_TEST_DB_URL=... venv/bin/python -m pytest \
        tests/integration/test_media_description_target.py -q
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

from db.repositories import contact_repo, message_repo


# Epoch fixo bem no passado (Set/2020) — ver test_inbound_provider_ts_ordering.
BASE = 1_600_000_000.0

IMG_DESC = "uma foto de um roteador com a luz vermelha acesa"
IMG_DESC_2 = "um print da tela de configuração"
AUDIO_TEXT = "boa tarde, o sinal caiu de novo aqui"


def _drain(built, channel_id: str, phone: str) -> None:
    """Espera o batch do orquestrador terminar, na loop do TestClient."""
    async def _run():
        for _ in range(6):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_run)


def _new_phone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


def _post(built, phone: str, payload_extra: dict, msg_id: str, ts: float) -> None:
    """POST um inbound GOWA e drena o batch."""
    payload: dict = {
        "from": f"{phone}@s.whatsapp.net",
        "id": msg_id,
        "from_name": "Cliente Teste",
        "timestamp": ts,
        **payload_extra,
    }
    r = built.client.post("/api/webhook/gowa/default",
                          json={"event": "message", "payload": payload})
    assert r.status_code == 200, r.text
    _drain(built, "default", phone)


def _rows(phone: str) -> list[dict]:
    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, "o inbound precisa ter materializado o contato"
    return message_repo.get_all(contact["id"])


def _by_msg_id(phone: str, msg_id: str) -> dict:
    row = next((r for r in _rows(phone) if r.get("msg_id") == msg_id), None)
    assert row is not None, f"linha {msg_id} não encontrada em {_rows(phone)}"
    return row


# ── Caso da produção: texto com ts posterior rouba a descrição da imagem ─────

def test_image_description_lands_on_image_row(build_app):
    """VERMELHO antes da F1, VERDE depois (conversas 13043/13045/1519).

    O texto do cliente tem ``ts`` do provedor MAIOR que o da imagem (1 segundo
    bastou em produção). A descrição tem de ir para a linha ``media_type='image'``
    e o texto do cliente tem de sobreviver intacto."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "image_transcription_enabled": True,
    })
    phone = _new_phone()
    text_id = f"txt_{uuid.uuid4().hex[:10]}"
    img_id = f"img_{uuid.uuid4().hex[:10]}"
    client_text = "o aparelho não conecta na rede"

    # 1º: o TEXTO, com ts do provedor POSTERIOR (BASE+100).
    _post(built, phone, {"body": client_text}, text_id, BASE + 100)
    # 2º: a IMAGEM, com ts do provedor ANTERIOR (BASE+50) — entrega fora de ordem.
    with patch.object(built.agent_handler, "describe_image", return_value=IMG_DESC):
        _post(built, phone, {"image": {"path": "statics/media/router.jpg"}},
              img_id, BASE + 50)

    img = _by_msg_id(phone, img_id)
    txt = _by_msg_id(phone, text_id)

    assert txt.get("media_type") is None, txt
    assert txt.get("content") == client_text, (
        "o texto original do cliente NÃO pode ser sobrescrito pela descrição da "
        f"imagem; content gravado={txt.get('content')!r}")
    assert img.get("media_type") == "image", img
    assert img.get("content") == f"[Descrição da imagem]: {IMG_DESC}", (
        "a descrição tem de ser colada na LINHA DA IMAGEM (a que o INSERT acabou "
        f"de criar); content gravado={img.get('content')!r}")

    # O card privado continua existindo e correto (D4 — não muda).
    cards = [r for r in _rows(phone) if r.get("role") == "transcription"]
    assert [c.get("content") for c in cards] == [IMG_DESC], cards


# ── Mesmo gatilho no áudio ───────────────────────────────────────────────────

def test_audio_transcription_lands_on_audio_row(build_app):
    """Zero linhas afetadas em produção, mas o site do bug é o MESMO laço —
    a diferença é só qual `maybe_transcribe` roda."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "audio_transcription_mode": "received",
        "audio_transcription_target": "private",
    })
    phone = _new_phone()
    text_id = f"txt_{uuid.uuid4().hex[:10]}"
    aud_id = f"aud_{uuid.uuid4().hex[:10]}"
    client_text = "obrigado, era só isso"

    _post(built, phone, {"body": client_text}, text_id, BASE + 100)
    with patch.object(built.agent_handler, "transcribe_audio", return_value=AUDIO_TEXT):
        _post(built, phone, {"audio": {"path": "statics/media/nota.ogg"}},
              aud_id, BASE + 50)

    aud = _by_msg_id(phone, aud_id)
    txt = _by_msg_id(phone, text_id)

    assert txt.get("content") == client_text, txt.get("content")
    assert txt.get("media_type") is None, txt
    assert aud.get("media_type") == "audio", aud
    assert aud.get("content") == f"[Transcrição do áudio]: {AUDIO_TEXT}", aud.get("content")

    # Nenhuma linha SEM media_type pode carregar prefixo interno de IA.
    leaked = [r for r in _rows(phone)
              if r.get("role") == "user" and not r.get("media_type")
              and str(r.get("content") or "").startswith("[Transcrição do áudio]:")]
    assert leaked == [], leaked


# ── Duas mídias: cada descrição na sua linha ─────────────────────────────────

def test_two_media_each_keep_their_own_description(build_app):
    """Com a reprocura, as DUAS descrições caem na mesma linha (a 2ª sobrescreve
    a 1ª) e uma delas some para sempre. Mirando pelo ``id`` do INSERT, cada
    imagem fica com a sua."""
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "image_transcription_enabled": True,
    })
    phone = _new_phone()
    text_id = f"txt_{uuid.uuid4().hex[:10]}"
    a_id = f"imga_{uuid.uuid4().hex[:10]}"
    b_id = f"imgb_{uuid.uuid4().hex[:10]}"

    _post(built, phone, {"body": "segue"}, text_id, BASE + 500)
    with patch.object(built.agent_handler, "describe_image", return_value=IMG_DESC):
        _post(built, phone, {"image": {"path": "statics/media/a.jpg"}}, a_id, BASE + 50)
    with patch.object(built.agent_handler, "describe_image", return_value=IMG_DESC_2):
        _post(built, phone, {"image": {"path": "statics/media/b.jpg"}}, b_id, BASE + 60)

    assert _by_msg_id(phone, a_id).get("content") == f"[Descrição da imagem]: {IMG_DESC}"
    assert _by_msg_id(phone, b_id).get("content") == f"[Descrição da imagem]: {IMG_DESC_2}"
    assert _by_msg_id(phone, text_id).get("content") == "segue"


# ── Sandbox: os 3 sites gêmeos (bug LATENTE — plano 133 · F2) ────────────────
#
# O sandbox usa o ``ts`` do INSERT (não há provedor), então a reprocura por
# ``ts DESC`` acertava o alvo e o bug nunca se manifestou. Estas rotas não
# tinham NENHUMA cobertura automatizada; sem elas a F2 iria para produção
# verificada só no olho. Aqui a asserção é a mesma das três de cima: a
# descrição/transcrição termina na linha da mídia, com a string idêntica à que o
# código inline produzia antes de passar por ``format_media_content``.

def _sandbox_media(built, route: str, field: str, filename: str,
                   data: dict) -> None:
    from unittest.mock import AsyncMock, patch
    from agent.handler import ProcessResult
    with patch.object(built.agent_handler, "aprocess_message",
                      new=AsyncMock(return_value=ProcessResult(reply="ok",
                                                              tool_calls=[]))):
        r = built.client.post(
            f"/api/sandbox/{route}",
            files={field: (filename, b"\x00conteudo binario", "application/octet-stream")},
            data=data)
    assert r.status_code == 200, r.text


def _sandbox_media_row(phone: str, media_type: str) -> dict:
    rows = [r for r in _rows(phone) if r.get("media_type") == media_type]
    assert len(rows) == 1, rows
    return rows[0]


def test_sandbox_image_description_lands_on_image_row(build_app):
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "image_transcription_enabled": True})
    phone = _new_phone()
    caption = "olha esse erro"
    from unittest.mock import patch
    with patch.object(built.agent_handler, "describe_image", return_value=IMG_DESC):
        _sandbox_media(built, "send-image", "image", "erro.png",
                       {"phone": phone, "caption": caption})
    row = _sandbox_media_row(phone, "image")
    # Prefixo primeiro, legenda depois — o mesmo que o código inline montava.
    assert row.get("content") == f"[Descrição da imagem]: {IMG_DESC}\n{caption}", row


def test_sandbox_audio_transcription_lands_on_audio_row(build_app):
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "audio_transcription_mode": "received"})
    phone = _new_phone()
    from unittest.mock import patch
    with patch.object(built.agent_handler, "transcribe_audio", return_value=AUDIO_TEXT):
        _sandbox_media(built, "send-audio", "audio", "nota.ogg", {"phone": phone})
    row = _sandbox_media_row(phone, "audio")
    assert row.get("content") == f"[Transcrição do áudio]: {AUDIO_TEXT}", row


def test_sandbox_document_transcription_lands_on_document_row(build_app):
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "document_transcription_enabled": True})
    phone = _new_phone()
    doc_text = "contrato de prestacao de servicos"
    from unittest.mock import patch
    with patch.object(built.agent_handler, "transcribe_document", return_value=doc_text):
        _sandbox_media(built, "send-document", "document", "contrato.pdf",
                       {"phone": phone, "caption": "segue"})
    row = _sandbox_media_row(phone, "document")
    # Documento é INVERTIDO: rótulo + legenda primeiro, prefixo depois.
    assert row.get("content") == (
        f"[Documento recebido: contrato.pdf]\nsegue\n[Conteúdo do documento]: {doc_text}"), row
