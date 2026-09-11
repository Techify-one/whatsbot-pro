"""Plano 118 — descrição de imagem por DIREÇÃO e transcrição independente da IA.

Dois pedidos empilhados, travados aqui:

* **(A)** a descrição de imagem ganhou direções (``image_transcription_mode`` =
  recebidas/enviadas/privadas), como o áudio já tinha. Antes ela era um booleano
  lido num ponto só do pipeline de ENTRADA: imagem do operador, eco do celular e
  nota privada nunca passavam por ``describe_image``;
* **(B)** transcrição/descrição NUNCA dependeu da IA do canal estar ligada — o
  formulário é que escondia os campos. O 1º teste transforma isso em asserção
  para deixar de ser afirmação.

Cobre também o defeito B1 do plano: a transcrição do áudio ENVIADO pelo operador
lia o config GLOBAL e ignorava o override do canal.

    venv/bin/python -m pytest tests/integration/test_media_transcription_directions.py -q
"""

from __future__ import annotations

import asyncio
import io
import json

from unittest.mock import patch

import pytest

from channels import ai_settings
from db.repositories import channel_repo, contact_repo, message_repo


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
OGG = b"OggS" + b"\x00" * 100
DESC = "uma foto de um gato laranja"
TRANSCRIPT = "olá, gostaria de saber o preço"


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def channel_ai():
    """Aplica overrides de IA no canal ``default`` e RESTAURA o config verbatim.

    O canal ``default`` é compartilhado por toda a sessão (o banco de teste é um
    só, ver ``tests/pg.py``): devolvê-lo a ``{"ai": {}}`` em vez de ao valor
    exato de antes deixaria rastro para os arquivos seguintes. Guarda a string
    crua e recoloca, e invalida o cache de 30s do ``ai_settings`` nas duas pontas.
    """
    row = channel_repo.get("default") or {}
    before = row.get("config")
    if before is not None and not isinstance(before, str):
        before = json.dumps(before)

    def _apply(ai: dict) -> None:
        cur = (channel_repo.get("default") or {}).get("config")
        if isinstance(cur, str) and cur:
            try:
                cur = json.loads(cur)
            except ValueError:
                cur = {}
        if not isinstance(cur, dict):
            cur = {}
        cur["ai"] = ai
        channel_repo.update("default", config=json.dumps(cur))
        ai_settings.reset_cache("default")

    yield _apply

    channel_repo.update("default", config=before)
    ai_settings.reset_cache("default")


def _rows(phone: str, role: str) -> list[dict]:
    contact = contact_repo.get_by_phone(phone)
    assert contact is not None, f"contato {phone} não materializado"
    return [m for m in message_repo.get_all(contact["id"]) if m["role"] == role]


def _drain(built, phone: str, channel_id: str = "default") -> None:
    """Espera o orquestrador de lote terminar (mesma receita da caracterização)."""
    async def _run():
        for _ in range(6):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:
                pass
            await asyncio.sleep(0)

    built.client.portal.call(_run)


def _post_image(built, phone: str, route: str = "send-image", **data):
    return built.client.post(
        f"/api/contacts/{phone}/{route}",
        files={"image": ("foto.png", io.BytesIO(PNG), "image/png")}, data=data)


def _echo_image(built, phone: str, msg_id: str):
    return built.client.post("/api/webhook/gowa/default", json={
        "event": "message", "payload": {
            "from": f"{phone}@s.whatsapp.net", "id": msg_id,
            "is_from_me": True, "from_name": "Eu",
            "image": {"path": "statics/media/cat.jpg"}}})


# ── (B) a IA do canal desligada não cala a transcrição ───────────────────────

def test_imagem_recebida_e_descrita_com_a_ia_do_canal_desligada(build_app, channel_ai):
    """O gate de IA (global ``auto_reply`` + ``ai_enabled`` do canal) não alcança
    a transcrição: no lote ela roda ANTES dele. Era a dor do usuário — o backend
    já fazia isso, só não havia onde configurar."""
    phone = "5511970011001"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "image_transcription_enabled": True,
    })
    channel_ai({"ai_enabled": False})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC):
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{phone}@s.whatsapp.net", "id": "img_aioff_1",
                "from_name": "Cliente",
                "image": {"path": "statics/media/cat.jpg"}}})
        assert r.status_code == 200, r.text
        _drain(built, phone)

    assert [c["content"] for c in _rows(phone, "transcription")] == [DESC]
    assert built.gowa_client.sent == [], "IA desligada não pode responder"


# ── (A) imagem enviada pelo operador (painel) ────────────────────────────────

def test_imagem_do_operador_descrita_quando_enviadas_marcado(build_app, channel_ai):
    phone = "5511970011002"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    channel_ai({"image_transcription_mode": "received,sent"})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC) as spy:
        assert _post_image(built, phone).status_code == 200
    assert spy.call_count == 1
    assert [c["content"] for c in _rows(phone, "transcription")] == [DESC]


def test_imagem_do_operador_nao_descrita_sem_enviadas(build_app, channel_ai):
    """Default do parque: só "Recebidas". Cada descrição é chamada de visão paga —
    ligar "Enviadas" por padrão cobraria todo mundo sem pedir."""
    phone = "5511970011003"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    channel_ai({"image_transcription_mode": "received"})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC) as spy:
        assert _post_image(built, phone).status_code == 200
    assert spy.call_count == 0
    assert _rows(phone, "transcription") == []


def test_canal_legado_com_booleano_nao_descreve_o_envio_do_operador(build_app, channel_ai):
    """D3 — canal que nunca foi re-salvo: o booleano legado vale ``{received}``."""
    phone = "5511970011004"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    channel_ai({"image_transcription_enabled": True})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC) as spy:
        assert _post_image(built, phone).status_code == 200
    assert spy.call_count == 0


# ── B1: o áudio do operador lia o config GLOBAL ──────────────────────────────

def test_audio_do_operador_honra_o_canal_e_nao_o_global(build_app, channel_ai):
    """Antes o envio pelo painel chamava o helper com ``settings=self.settings``
    (o config global), então marcar/desmarcar "Enviadas" no canal era inerte."""
    phone = "5511970011005"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "audio_transcription_mode": "received,sent",   # GLOBAL manda transcrever
    })
    channel_ai({"audio_transcription_mode": "received"})   # o canal não
    with patch.object(built.agent_handler, "transcribe_audio",
                      return_value=TRANSCRIPT) as spy:
        r = built.client.post(
            f"/api/contacts/{phone}/send-audio",
            files={"audio": ("voz.ogg", io.BytesIO(OGG), "audio/ogg")})
        assert r.status_code == 200, r.text
    assert spy.call_count == 0, "o override do canal tem de vencer o global"


def test_audio_do_operador_transcrito_quando_o_canal_marca_enviadas(build_app, channel_ai):
    phone = "5511970011006"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0,
        "audio_transcription_mode": "received",        # GLOBAL não manda
    })
    channel_ai({"audio_transcription_mode": "received,sent"})
    with patch.object(built.agent_handler, "transcribe_audio",
                      return_value=TRANSCRIPT) as spy:
        r = built.client.post(
            f"/api/contacts/{phone}/send-audio",
            files={"audio": ("voz.ogg", io.BytesIO(OGG), "audio/ogg")})
        assert r.status_code == 200, r.text
    assert spy.call_count == 1
    assert [c["content"] for c in _rows(phone, "transcription")] == [TRANSCRIPT]


# ── nota privada ─────────────────────────────────────────────────────────────

def test_imagem_em_nota_privada_descrita_quando_privadas_marcado(build_app, channel_ai):
    phone = "5511970011007"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    channel_ai({"image_transcription_mode": "received,private"})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC) as spy:
        assert _post_image(built, phone, route="private-image").status_code == 200
    assert spy.call_count == 1
    assert [c["content"] for c in _rows(phone, "transcription")] == [DESC]
    # A nota privada NUNCA vai ao contato (invariante do _save_private_media).
    assert built.gowa_client.sent == []
    assert [m["role"] for m in _rows(phone, "private_note")] == ["private_note"]


def test_imagem_em_nota_privada_nao_descrita_sem_privadas(build_app, channel_ai):
    phone = "5511970011008"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    channel_ai({"image_transcription_mode": "received,sent"})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC) as spy:
        assert _post_image(built, phone, route="private-image").status_code == 200
    assert spy.call_count == 0
    assert _rows(phone, "transcription") == []


# ── eco do próprio celular ───────────────────────────────────────────────────

def test_eco_de_imagem_descrito_uma_vez_quando_enviadas_marcado(build_app, channel_ai):
    """A imagem que o atendente mandou pelo WhatsApp do CELULAR (fora do painel).

    Uma descrição só, e como card privado: mandá-la ao chat viraria outro
    ``message.sent`` → eco → nova descrição."""
    phone = "5511970011009"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    channel_ai({"image_transcription_mode": "received,sent"})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC) as spy:
        assert _echo_image(built, phone, "echoimg_1").status_code == 200
    assert spy.call_count == 1
    assert [c["content"] for c in _rows(phone, "transcription")] == [DESC]
    assert len(_rows(phone, "assistant")) == 1, "o eco não pode duplicar a linha"
    assert built.gowa_client.sent == [], "eco não produz envio"


def test_eco_de_imagem_nao_descrito_sem_enviadas(build_app, channel_ai):
    phone = "5511970011010"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    channel_ai({"image_transcription_mode": "received"})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC) as spy:
        assert _echo_image(built, phone, "echoimg_2").status_code == 200
    assert spy.call_count == 0
    assert _rows(phone, "transcription") == []


def test_eco_da_imagem_que_o_painel_enviou_nao_e_descrito_de_novo(build_app, channel_ai):
    """A trava de eco (``processed_messages``) vem ANTES da descrição: a imagem
    enviada pelo painel é descrita UMA vez (no envio), não duas."""
    phone = "5511970011011"
    built = build_app(["gowa"], settings_overrides={
        "auto_reply": False, "message_batch_delay": 0})
    channel_ai({"image_transcription_mode": "received,sent"})
    with patch.object(built.agent_handler, "describe_image", return_value=DESC) as spy:
        r = _post_image(built, phone)
        assert r.status_code == 200, r.text
        msg_id = (r.json().get("data") or {}).get("msg_id")
        assert msg_id
        assert _echo_image(built, phone, msg_id).status_code == 200
    assert spy.call_count == 1, "descrição (e cobrança) uma vez só"
    assert len(_rows(phone, "assistant")) == 1
    assert len(_rows(phone, "transcription")) == 1
