"""Envio de mídia pela fachada ``/api/v1`` (plano 151 · F6).

O teste que dá nome ao plano é
``test_imagem_com_kind_document_chega_ao_canal_como_documento``: é o pedido do
operador — mandar uma imagem **como arquivo**, sem recompressão — e ele só passa
enquanto o ``kind`` for do chamador. No dia em que alguém "melhorar" a rota
inferindo o tipo do ``Content-Type``, ele fica vermelho.

O resto trava o contrato: a v1 **delega** (nunca reimplementa), recusa alvo
ambíguo em vez de chutar o canal, respeita RBAC e escopo de caixa do dono da
chave, e não deixa nenhum dos três caminhos de entrada (multipart / url /
base64) carregar um corpo sem teto.

    venv/bin/python -m pytest tests/integration/test_v1_media.py -q
"""

from __future__ import annotations

import base64
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from channels.base import ChannelCapabilities
from channels.outbound import OutboundRouter
from db.repositories import (contact_repo, inbox_member_repo, inbox_repo,
                             message_repo, session_repo, user_repo)
from server.auth import generate_session_token
from server.upload_limits import MAX_UPLOAD_BYTES


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
PDF = b"%PDF-1.4\n" + b"\x00" * 200
OGG = b"OggS" + b"\x00" * 200
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200

MEDIA = "/api/v1/messages/media"
LINK = "/api/v1/messages/media/link"


# ── harness ─────────────────────────────────────────────────────────────────

@pytest.fixture
def api(build_app):
    """App hermética com GOWA falso + sessão de admin (a v1 aceita as duas portas)."""
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    user = user_repo.create(email=f"v1media-{uuid.uuid4().hex}@test.local",
                            name="V1 Mídia", password_hash="test-only",
                            role_keys=["admin"])
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    built.client.headers["Authorization"] = f"Bearer {token}"
    yield built
    built.client.headers.pop("Authorization", None)
    session_repo.delete(token)
    user_repo.delete(user["id"])


def _upload(api, phone: str, kind: str, name: str, blob: bytes, mime: str, **extra):
    data = {"phone": phone, "kind": kind}
    data.update({k: v for k, v in extra.items() if v is not None})
    return api.client.post(MEDIA, files={"file": (name, io.BytesIO(blob), mime)},
                           data=data)


def _rows(phone: str) -> list[dict]:
    contact = contact_repo.get_by_phone(phone)
    return message_repo.get_all(contact["id"]) if contact else []


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode()


# ── §6 — a v1 NÃO reimplementa o envio ──────────────────────────────────────

def test_a_rota_de_midia_chama_o_servico_compartilhado(api):
    """A trava do plano: mídia sai por ``MessagingService.send_media_upload``, a
    MESMA função das quatro rotas do painel. Uma segunda implementação mandaria
    para o JID errado (ghost-send do 9º dígito), fora da janela do canal e sem
    calar o ciclo da IA — e nada disso apareceria como erro."""
    from app.services.messaging_service import MessagingService

    fake = AsyncMock(return_value={
        "ok": True, "msg_id": "MID9", "media_path": "statics/outbox/x.png",
        "conversation_id": 7, "channel_id": "default", "kind": "image",
        "sandbox": False})
    with patch.object(MessagingService, "send_media_upload", fake):
        r = _upload(api, "5511970152001", "image", "foto.png", PNG, "image/png")
    assert r.status_code == 201, r.text
    assert r.json() == {"sent": True, "msg_id": "MID9", "conversation_id": 7,
                        "channel_id": "default", "kind": "image",
                        "media_path": "statics/outbox/x.png", "sandbox": False}
    assert fake.await_count == 1


# ── cada kind entrega e persiste ────────────────────────────────────────────

@pytest.mark.parametrize("phone,kind,name,blob,mime,wire,content", [
    ("5511970152011", "image", "foto.png", PNG, "image/png", "image", "olha"),
    ("5511970152012", "document", "contrato.pdf", PDF, "application/pdf", "file",
     "[Documento enviado: contrato.pdf]\nolha"),
])
def test_cada_kind_entrega_e_persiste(api, phone, kind, name, blob, mime, wire, content):
    r = _upload(api, phone, kind, name, blob, mime, caption="olha")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sent"] is True and body["kind"] == kind
    assert body["media_path"].startswith("statics/outbox/")
    assert body["conversation_id"]

    row = [m for m in _rows(phone) if m.get("media_type")][-1]
    assert row["media_type"] == kind
    assert row["content"] == content
    assert [k for k, _ in api.gowa_client.sent] == [wire]


def test_video_usa_o_endpoint_proprio_do_canal(api):
    """Vídeo tem rota própria no GOWA (``/send/video``), com degradação para
    documento num binário antigo — por isso ele não aparece em ``.files`` como
    o documento aparece."""
    phone = "5511970152013"
    r = _upload(api, phone, "video", "clipe.mp4", MP4, "video/mp4", caption="olha")
    assert r.status_code == 201, r.text
    assert [c[0] for c in api.gowa_client.calls] == ["send_video"]
    row = [m for m in _rows(phone) if m.get("media_type")][-1]
    assert row["media_type"] == "video"
    assert row["content"] == "olha"


def test_audio_entrega_como_nota_de_voz(api):
    phone = "5511970152020"
    r = _upload(api, phone, "audio", "voz.ogg", OGG, "audio/ogg")
    assert r.status_code == 201, r.text
    row = [m for m in _rows(phone) if m.get("media_type")][-1]
    assert row["media_type"] == "audio"
    assert row["content"] == "[Áudio]"
    assert [k for k, _ in api.gowa_client.sent] == ["audio"]


# ── O PEDIDO: imagem como arquivo ───────────────────────────────────────────

def test_imagem_com_kind_document_chega_ao_canal_como_documento(api):
    """A razão de este plano existir.

    ``kind`` é do CHAMADOR, nunca do MIME: um ``image/png`` com
    ``kind=document`` sai por ``/send/file`` (``documentMessage``), que não
    recomprime — a foto chega com a qualidade original. Inferir o kind do
    ``Content-Type`` faria exatamente o oposto do que foi pedido.
    """
    phone = "5511970152030"
    r = _upload(api, phone, "document", "certificado.png", PNG, "image/png")
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "document"

    assert [k for k, _ in api.gowa_client.sent] == ["file"], (
        "imagem mandada como documento NÃO pode cair em /send/image")
    assert api.gowa_client.files[-1]["filename"] == "certificado.png"

    row = [m for m in _rows(phone) if m.get("media_type")][-1]
    assert row["media_type"] == "document"


def test_o_filename_do_corpo_manda_no_nome_que_o_cliente_ve(api):
    """Um Worker que gera o PDF em memória manda uma parte com nome genérico; o
    nome que o destinatário vê (e de onde sai o MIME no fio) vem do campo."""
    phone = "5511970152031"
    r = _upload(api, phone, "document", "blob", PDF, "application/pdf",
                filename="certificado-joao.pdf")
    assert r.status_code == 201, r.text
    assert api.gowa_client.files[-1]["filename"] == "certificado-joao.pdf"


# ── validação de entrada: 400, nunca 500 ───────────────────────────────────

@pytest.mark.parametrize("kind", ["sticker", "gif", "", "IMAGE"])
def test_kind_fora_do_conjunto_e_400(api, kind):
    r = _upload(api, "5511970152040", kind, "foto.png", PNG, "image/png")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_kind"


def test_legenda_em_audio_e_recusada_em_vez_de_descartada(api):
    """Aceitar-e-descartar faria o integrador descobrir pelo relato do cliente."""
    r = _upload(api, "5511970152041", "audio", "voz.ogg", OGG, "audio/ogg",
                caption="ouça isso")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "caption_not_supported"


def test_arquivo_vazio_e_400(api):
    r = _upload(api, "5511970152042", "image", "vazio.png", b"", "image/png")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_file"


def test_url_e_base64_juntos_sao_400(api):
    r = api.client.post(LINK, json={
        "phone": "5511970152043", "kind": "document", "filename": "a.pdf",
        "url": "https://exemplo.com/a.pdf", "content_base64": _b64(PDF)})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "conflicting_source"


def test_link_sem_nenhuma_fonte_e_400(api):
    r = api.client.post(LINK, json={"phone": "5511970152044", "kind": "document",
                                    "filename": "a.pdf"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_field"


def test_link_sem_filename_e_400(api):
    """Sem extensão o MIME no fio degrada para ``application/octet-stream`` e o
    destinatário recebe um anexo genérico que não abre com duplo clique."""
    r = api.client.post(LINK, json={"phone": "5511970152045", "kind": "document",
                                    "content_base64": _b64(PDF)})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_field"


def test_base64_invalido_e_400(api):
    r = api.client.post(LINK, json={"phone": "5511970152046", "kind": "document",
                                    "filename": "a.pdf",
                                    "content_base64": "não é base64!!!"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_base64"


# ── base64: o caminho do Worker que já tem os bytes ────────────────────────

def test_base64_entrega_o_arquivo(api):
    phone = "5511970152050"
    r = api.client.post(LINK, json={
        "phone": phone, "kind": "document", "filename": "certificado.pdf",
        "content_base64": _b64(PDF)})
    assert r.status_code == 201, r.text
    assert [k for k, _ in api.gowa_client.sent] == ["file"]
    assert api.gowa_client.files[-1]["filename"] == "certificado.pdf"


# ── SSRF: a rota /link não abre a rede interna ─────────────────────────────

def test_url_para_endereco_interno_e_400_sem_conexao(api):
    """Com ``conversation.reply`` bastaria um POST para varrer a rede interna.
    O guard recusa ANTES de qualquer conexão."""
    r = api.client.post(LINK, json={
        "phone": "5511970152060", "kind": "document", "filename": "x.pdf",
        "url": "http://10.0.0.5:5432/"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "blocked_host"
    assert api.gowa_client.sent == []


def test_url_para_metadados_da_nuvem_e_400(api):
    r = api.client.post(LINK, json={
        "phone": "5511970152061", "kind": "document", "filename": "x.pdf",
        "url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "blocked_host"


def test_esquema_nao_http_e_400(api):
    r = api.client.post(LINK, json={
        "phone": "5511970152062", "kind": "document", "filename": "x.pdf",
        "url": "file:///etc/passwd"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_scheme"


# ── os tetos de tamanho, nos três caminhos ─────────────────────────────────

def test_multipart_acima_do_teto_e_413(api):
    """O middleware recusa pelo ``Content-Length`` ANTES de ler o corpo — só
    porque a rota da v1 está em ``_UPLOAD_PATH_RE`` (plano 151 · I9)."""
    big = b"\x00" * (MAX_UPLOAD_BYTES + 1024)
    r = _upload(api, "5511970152070", "document", "grande.bin", big,
                "application/octet-stream")
    assert r.status_code == 413
    # DTO da v1, não o envelope do painel: este middleware é o único ponto em
    # que a fachada responde sem passar pelo handler de ``V1Error``.
    assert r.json()["error"]["code"] == "too_big"


def test_base64_acima_do_teto_e_413(api):
    """O caminho JSON não passa pelo middleware: o teto é medido pelo
    comprimento da string, sem decodificar."""
    size = MAX_UPLOAD_BYTES + 1024
    encoded = "A" * ((size + 2) // 3 * 4)
    r = api.client.post(LINK, json={
        "phone": "5511970152071", "kind": "document", "filename": "grande.bin",
        "content_base64": encoded})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_big"


# ── multicanal, janela e limites vêm do serviço (não são recriados aqui) ───

def test_alvo_ambiguo_e_409(api):
    """Contato com conversa aberta em DUAS caixas ⇒ recusa, não um chute."""
    from db.repositories import conversation_repo

    with patch.object(contact_repo, "get_by_phone", lambda p: {"id": 42}), \
         patch.object(conversation_repo, "list_conversations",
                      lambda **kw: [{"id": 1, "channel_id": "a", "inbox_id": 1},
                                    {"id": 2, "channel_id": "b", "inbox_id": 2}]):
        r = _upload(api, "5511970152080", "image", "foto.png", PNG, "image/png")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ambiguous_target"
    assert r.json()["error"]["details"]["options"]


def test_janela_fechada_do_canal_vira_409(api):
    caps = ChannelCapabilities(media=True, groups=True, inbound_route="path",
                               session_window_hours=24)
    with patch.object(OutboundRouter, "capabilities", lambda self, cid: caps):
        r = _upload(api, "5511970152081", "image", "foto.png", PNG, "image/png")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "session_window_closed"


def test_limite_do_canal_vira_413(api):
    from channels.base import MediaLimits

    caps = ChannelCapabilities(
        media=True, inbound_route="path",
        media_limits={"image": MediaLimits(max_bytes=10, extensions=(".png",))})
    with patch.object(OutboundRouter, "capabilities", lambda self, cid: caps):
        r = _upload(api, "5511970152082", "image", "foto.png", PNG, "image/png")
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_big"


# ── RBAC e escopo de caixa ─────────────────────────────────────────────────

def test_sem_conversation_reply_e_403(build_app):
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    user = user_repo.create(email=f"v1ro-{uuid.uuid4().hex}@test.local",
                            name="Só leitura", password_hash="test-only",
                            role_keys=[])
    user_repo.set_custom_permissions(user["id"], ["conversation.read"])
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    try:
        r = built.client.post(
            MEDIA, headers={"Authorization": f"Bearer {token}"},
            files={"file": ("foto.png", io.BytesIO(PNG), "image/png")},
            data={"phone": "5511970152090", "kind": "image"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "forbidden"
    finally:
        session_repo.delete(token)
        user_repo.delete(user["id"])


def test_caixa_alheia_e_403(build_app):
    """Escopo de DADOS = membresia de inbox do dono da chave. Um usuário escopado
    que não é membro de caixa nenhuma não escreve em lugar nenhum."""
    built = build_app(["gowa"], settings_overrides={"message_batch_delay": 0})
    user = user_repo.create(email=f"v1scope-{uuid.uuid4().hex}@test.local",
                            name="Escopado", password_hash="test-only",
                            role_keys=[])
    user_repo.set_custom_permissions(user["id"], ["conversation.reply"])
    token = generate_session_token()
    session_repo.create(token, user["id"], user_agent="pytest", ip="127.0.0.1")
    try:
        assert inbox_member_repo.inbox_ids_for_user(user["id"]) == []
        r = built.client.post(
            MEDIA, headers={"Authorization": f"Bearer {token}"},
            files={"file": ("foto.png", io.BytesIO(PNG), "image/png")},
            data={"phone": "5511970152091", "kind": "image"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "inbox_forbidden"
    finally:
        session_repo.delete(token)
        user_repo.delete(user["id"])


# ── OpenAPI ────────────────────────────────────────────────────────────────

def test_as_duas_rotas_aparecem_no_openapi(api):
    """O esquema é gerado das assinaturas; rota nova entra sozinha — mas se um
    dia alguém trocar por um ``Request`` cru com dispatch por Content-Type, o
    corpo some do schema e o codegen do integrador quebra em silêncio."""
    schema = api.client.get("/api/v1/openapi.json").json()
    assert MEDIA in schema["paths"]
    assert LINK in schema["paths"]
    multipart = schema["paths"][MEDIA]["post"]["requestBody"]["content"]
    assert "multipart/form-data" in multipart
    assert "application/json" in schema["paths"][LINK]["post"]["requestBody"]["content"]
