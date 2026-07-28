"""Plano 75 F2 + F9 — testes de unidade do ``parse_inbound`` dos providers.

Escopo:
  * **F2** — todo ``type`` que a Cloud API entrega vira TEXTO legível na bolha
    (nunca mais ``content=''``), e ``text``/mídia continuam **byte-idênticos**.
  * **F9** — a citação feita pelo cliente é capturada (``context.id`` no Cloud,
    ``reply_to_message.message_id`` no Telegram) e gravada em
    ``InboundEvent.reply_to_msg_id`` **sem nenhuma normalização de id**.

Fixtures = os payloads LITERAIS da doc oficial da Meta reunidos no §12 do plano
(e, no Telegram, a reprodução real em produção: 74057 → 74056).

Os providers são instanciados com ``registry=None`` + ``credentials={}``: nada
aqui toca rede, banco ou o app FastAPI. Os módulos dos plugins são carregados
POR CAMINHO, registrando um pacote sintético em ``sys.modules`` — exatamente o
que o ``plugins/loader.py`` faz — para o import relativo ``.inbound_text``
resolver.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLES = _ROOT / "assets" / "plugin_examples"


def _load_plugin_module(plugin_id: str, modname: str, alias: str):
    """Load ``<plugin>/<modname>.py`` as a submodule of a synthetic package.

    Mirrors ``plugins.loader._import_package`` / ``_import_submodule`` so the
    plugin's relative imports (``from .inbound_text import …``) resolve.
    """
    plugin_dir = _EXAMPLES / plugin_id
    pkg_name = f"whatsbot_test_pkg_{alias}"
    if pkg_name not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            pkg_name, plugin_dir / "__init__.py",
            submodule_search_locations=[str(plugin_dir)])
        assert pkg_spec is not None and pkg_spec.loader is not None
        pkg = importlib.util.module_from_spec(pkg_spec)
        sys.modules[pkg_name] = pkg
        pkg_spec.loader.exec_module(pkg)

    full = f"{pkg_name}.{modname}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, plugin_dir / f"{modname}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_cloud_mod = _load_plugin_module("whatsapp_cloud", "channels", "wa_cloud")
_tg_mod = _load_plugin_module("telegram", "channels", "telegram")


@pytest.fixture(scope="module")
def cloud():
    return _cloud_mod.WhatsAppCloudChannel("ch_cloud_test", registry=None,
                                           credentials={})


@pytest.fixture(scope="module")
def telegram():
    return _tg_mod.TelegramChannel("ch_tg_test", registry=None,
                                   credentials={"bot_token": "1:AA"})


def _parse(cloud, msg: dict, name_by_wa: dict | None = None):
    return cloud._parse_message(msg, "ch_cloud_test", "PNID",
                                name_by_wa or {}, {})


def _envelope(msg: dict) -> dict:
    """Wrap one ``messages[]`` item in the real Meta webhook envelope."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "15550783881",
                                 "phone_number_id": "PNID"},
                    "contacts": [{"wa_id": msg.get("from", ""),
                                  "profile": {"name": "Cliente"}}],
                    "messages": [msg],
                },
            }],
        }],
    }


# ══════════════════════════════════════════════════════════════════════════
# Fixtures — payloads literais do §12 do plano
# ══════════════════════════════════════════════════════════════════════════

CONTACTS_MSG = {
    "from": "16505551234",
    "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA=",
    "timestamp": "1744344496",
    "type": "contacts",
    "contacts": [{
        "name": {"first_name": "Barbara", "last_name": "Johnson",
                 "formatted_name": "Barbara J. Johnson"},
        "org": {"company": "Social Tsunami"},
        "phones": [{"phone": "+1 (415) 555-0829", "wa_id": "14125550829",
                    "type": "MOBILE"}],
    }],
}

ORDER_MSG = {
    "from": "16505551234", "id": "wamid.ORDER", "timestamp": "1744344497",
    "type": "order",
    "order": {
        "catalog_id": "194836987003835", "text": "Love these!",
        "product_items": [
            {"product_retailer_id": "di9ozbzfi4", "quantity": 2,
             "item_price": 30, "currency": "USD"},
            {"product_retailer_id": "nqryix03ez", "quantity": 1,
             "item_price": 25, "currency": "USD"},
        ],
    },
}

SYSTEM_MSG = {
    "from": "16505551234",
    "id": "wamid.HBgLMTk4MzU1NTE5NzQVAgASGAoxMTgyMDg2MjY3AA==",
    "timestamp": "1750269342", "type": "system",
    "system": {"body": "User Sheena Nelson changed from 16505551234 to 12195555358",
               "wa_id": "12195555358", "type": "user_changed_number"},
}

UNSUPPORTED_MSG = {
    "from": "16505551234", "id": "wamid.UNSUP", "timestamp": "1744344498",
    "type": "unsupported", "unsupported": {"type": "edit"},
    "errors": [{"code": 131051, "title": "Message type unknown",
                "message": "Message type unknown",
                "error_data": {"details": "Message type is currently not supported."}}],
}

LIST_REPLY_MSG = {
    "from": "16505551234", "id": "wamid.LIST", "timestamp": "1744344499",
    "context": {"from": "15550783881", "id": "wamid.CTX_LIST"},
    "type": "interactive",
    "interactive": {"type": "list_reply",
                    "list_reply": {"id": "priority_express",
                                   "title": "Priority Mail Express",
                                   "description": "Next Day to 2 Days"}},
}

BUTTON_REPLY_MSG = {
    "from": "16505551234", "id": "wamid.BTNREPLY", "timestamp": "1744344500",
    "context": {"from": "15550783881", "id": "wamid.CTX_BTNREPLY"},
    "type": "interactive",
    "interactive": {"type": "button_reply",
                    "button_reply": {"id": "cancel-button", "title": "Cancel"}},
}

TEMPLATE_BUTTON_MSG = {
    "from": "16505551234", "id": "wamid.BTN", "timestamp": "1744344501",
    "context": {"from": "15550783881", "id": "wamid.CTX_BTN"},
    "type": "button",
    "button": {"payload": "Unsubscribe", "text": "Unsubscribe"},
}

TEXT_MSG = {
    "from": "16505551234", "id": "wamid.TEXT", "timestamp": "1744344502",
    "type": "text", "text": {"body": "Can I get more info about this?"},
}


# ══════════════════════════════════════════════════════════════════════════
# F2 — todo tipo vira texto legível
# ══════════════════════════════════════════════════════════════════════════

def test_contacts_vira_texto_com_nome_e_telefone(cloud):
    """O caso real (msg 633446): o card trazia o telefone que o atendente
    pediu e a bolha nascia vazia."""
    ev = _parse(cloud, CONTACTS_MSG)
    assert ev.media_type == "contacts"
    assert ev.text, "bolha muda — regressão do bug que este plano conserta"
    assert "Barbara J. Johnson" in ev.text
    assert "+1 (415) 555-0829" in ev.text
    assert "Social Tsunami" in ev.text
    # plano 82 (regressão): vCard é fala REAL do cliente ⇒ segue kind="message".
    assert ev.kind == "message"


def test_order_vira_texto_com_itens(cloud):
    ev = _parse(cloud, ORDER_MSG)
    assert ev.media_type == "order"
    assert "Pedido do catálogo" in ev.text
    assert "di9ozbzfi4" in ev.text and "nqryix03ez" in ev.text
    assert "Love these!" in ev.text
    # plano 82 (regressão): pedido do catálogo é fala REAL ⇒ segue kind="message".
    assert ev.kind == "message"


def test_system_usa_o_body_pronto_da_meta(cloud):
    ev = _parse(cloud, SYSTEM_MSG)
    assert ev.media_type == "system"
    assert "User Sheena Nelson changed from" in ev.text
    # plano 82: system é AVISO DE CICLO DE VIDA (não fala do cliente) ⇒ kind próprio,
    # roteado para o card painel-only sem IA/conversa/automação.
    assert ev.kind == "system"
    assert ev.media_extras["system_type"] == "user_changed_number"
    assert ev.media_extras["wa_id"] == "12195555358"
    assert ev.media_extras["body"] == SYSTEM_MSG["system"]["body"]


def test_system_nao_traz_contacts_entao_sender_name_fica_vazio(cloud):
    """§12.3: o webhook de ``system`` não tem ``contacts[]``. O ingest só chama
    ``set_wa_name`` quando ``event.sender_name`` é verdadeiro
    (message_ingest_service.py:409), então o nome existente não é apagado."""
    events = cloud.parse_inbound({
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "PNID"},
            "messages": [SYSTEM_MSG],
        }}]}],
    })
    assert len(events) == 1
    assert events[0].sender_name == ""


def test_unsupported_explica_o_motivo(cloud):
    ev = _parse(cloud, UNSUPPORTED_MSG)
    assert ev.media_type == "unsupported"
    assert "não suportada" in ev.text
    assert "edit" in ev.text
    assert "Message type unknown" in ev.text


def test_interactive_list_reply_mostra_o_que_foi_escolhido(cloud):
    ev = _parse(cloud, LIST_REPLY_MSG)
    assert ev.media_type == "interactive"      # dado histórico — não renomear
    assert "Priority Mail Express" in ev.text
    assert "Next Day to 2 Days" in ev.text


def test_interactive_button_reply_mostra_o_botao_clicado(cloud):
    ev = _parse(cloud, BUTTON_REPLY_MSG)
    assert ev.media_type == "interactive"
    assert ev.text == "Cancel"


def test_tipo_inventado_nunca_vira_bolha_muda(cloud):
    msg = {"from": "16505551234", "id": "wamid.NEW", "timestamp": "1744344503",
           "type": "quantum_holo", "quantum_holo": {"foo": "bar"}}
    ev = _parse(cloud, msg)
    assert ev.text == '⚠️ Mensagem do tipo "quantum_holo" não suportada'
    assert ev.media_type == "quantum_holo"
    # media_extras preservado — plugins consomem via o evento message.saved.
    assert ev.media_extras["unsupported_type"] == "quantum_holo"
    assert ev.media_extras["payload"] == {"foo": "bar"}


def test_todos_os_tipos_do_apendice_produzem_texto(cloud):
    for msg in (CONTACTS_MSG, ORDER_MSG, SYSTEM_MSG, UNSUPPORTED_MSG,
                LIST_REPLY_MSG, BUTTON_REPLY_MSG, TEMPLATE_BUTTON_MSG, TEXT_MSG):
        events = cloud.parse_inbound(_envelope(msg))
        assert len(events) == 1, msg["type"]
        assert events[0].text.strip(), f"texto vazio para type={msg['type']}"


# ══════════════════════════════════════════════════════════════════════════
# F2 — REGRESSÃO: texto e mídia têm que sair byte-idênticos
# ══════════════════════════════════════════════════════════════════════════

def test_regressao_texto_normal_inalterado(cloud):
    ev = _parse(cloud, TEXT_MSG, {"16505551234": "Cliente"})
    assert ev.text == "Can I get more info about this?"
    assert ev.media_type is None
    assert ev.media_extras == {}
    assert ev.sender_name == "Cliente"
    assert ev.external_msg_id == "wamid.TEXT"


def test_regressao_button_de_template_byte_identico(cloud):
    """As 40 linhas ``media_type='interactive'`` em produção são ``type:
    'button'`` (quick-reply de template) — o texto não pode mudar."""
    ev = _parse(cloud, TEMPLATE_BUTTON_MSG)
    legacy = (TEMPLATE_BUTTON_MSG["button"].get("text") or "")
    assert ev.text == legacy == "Unsubscribe"
    assert ev.media_type == "interactive"
    assert ev.media_extras == {"payload": TEMPLATE_BUTTON_MSG["button"]}


@pytest.mark.parametrize("kind", ["image", "audio", "video", "document", "sticker"])
def test_regressao_midia_inalterada(cloud, kind):
    msg = {"from": "16505551234", "id": f"wamid.{kind}", "timestamp": "1744344504",
           "type": kind,
           kind: {"id": "MEDIA_ID", "mime_type": "application/octet-stream",
                  "sha256": "abc", "filename": "f.bin"}}
    ev = _parse(cloud, msg)
    assert ev.media_type == kind
    assert ev.text == ""            # sem legenda ⇒ sem texto (a bolha é a mídia)
    assert ev.media_path is None
    assert ev.media_extras["media_id"] == "MEDIA_ID"
    assert ev.media_extras["mime_type"] == "application/octet-stream"


def test_regressao_midia_com_legenda_usa_a_legenda(cloud):
    msg = {"from": "16505551234", "id": "wamid.IMG", "timestamp": "1744344505",
           "type": "image", "image": {"id": "M1", "caption": "olha isso"}}
    ev = _parse(cloud, msg)
    assert ev.text == "olha isso"
    assert ev.media_extras["caption"] == "olha isso"


def test_regressao_location_inalterada(cloud):
    msg = {"from": "16505551234", "id": "wamid.LOC", "timestamp": "1744344506",
           "type": "location", "location": {"latitude": -16.6, "longitude": -49.2,
                                            "name": "Loja", "address": "Rua X"}}
    ev = _parse(cloud, msg)
    assert ev.media_type == "location"
    assert ev.text == ""
    assert ev.media_extras == {"lat": -16.6, "lng": -49.2,
                               "name": "Loja", "address": "Rua X"}


def test_regressao_reaction_continua_sendo_evento_de_reacao(cloud):
    msg = {"from": "16505551234", "id": "wamid.REACT", "timestamp": "1744344507",
           "type": "reaction", "reaction": {"emoji": "👍", "message_id": "wamid.ALVO"}}
    ev = _parse(cloud, msg)
    assert ev.kind == "reaction"
    assert ev.media_extras == {"emoji": "👍", "reacted_message_id": "wamid.ALVO"}


def test_regressao_receipt_de_status_inalterado(cloud):
    events = cloud.parse_inbound({"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID"},
        "statuses": [{"id": "wamid.S", "status": "failed",
                      "timestamp": "1751142888", "recipient_id": "16505551234",
                      "errors": [{"code": 131049, "title": "nope"}]}],
    }}]}]})
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "receipt" and ev.direction == "out"
    assert ev.media_extras["status"] == "failed"
    assert ev.media_extras["errors"][0]["code"] == 131049


# ══════════════════════════════════════════════════════════════════════════
# F9 — citação do cliente (Cloud)
# ══════════════════════════════════════════════════════════════════════════

def test_cloud_context_id_vira_reply_to(cloud):
    msg = dict(TEXT_MSG, context={"from": "15550783881", "id": "wamid.ALVO"})
    ev = _parse(cloud, msg)
    assert ev.reply_to_msg_id == "wamid.ALVO"
    assert ev.text == "Can I get more info about this?"   # texto intocado


def test_cloud_sem_context_nao_tem_reply_to(cloud):
    assert _parse(cloud, TEXT_MSG).reply_to_msg_id is None


def test_cloud_reply_to_id_nao_e_normalizado():
    """F.P.#7 / R12: ``WAID:wamid.…`` é o id das 301k mensagens importadas do
    Chatwoot. Casamento é por IGUALDADE EXATA — mexer no prefixo quebraria as
    citações que hoje funcionam."""
    ch = _cloud_mod.WhatsAppCloudChannel("ch_cloud_test", registry=None,
                                         credentials={})
    raw_id = "WAID:wamid.HBgNNTU2Mjk5…3EB07F77EEE2BBDB389E66"
    ev = ch._parse_message(dict(TEXT_MSG, context={"id": raw_id}),
                           "ch_cloud_test", "PNID", {}, {})
    assert ev.reply_to_msg_id == raw_id


def test_cloud_referred_product_e_forwarded_nao_sao_citacao(cloud):
    msg = dict(TEXT_MSG, context={"from": "15550783881", "forwarded": True,
                                  "referred_product": {"catalog_id": "C",
                                                       "product_retailer_id": "P"}})
    assert _parse(cloud, msg).reply_to_msg_id is None


def test_cloud_context_invalido_nao_quebra(cloud):
    for bad in ("nope", 42, [], {"id": None}, {"id": ""}, {"id": 123}):
        ev = _parse(cloud, dict(TEXT_MSG, context=bad))
        assert ev.reply_to_msg_id is None
        assert ev.text == "Can I get more info about this?"


@pytest.mark.parametrize("msg,expected", [
    (CONTACTS_MSG, None),
    (LIST_REPLY_MSG, "wamid.CTX_LIST"),
    (BUTTON_REPLY_MSG, "wamid.CTX_BTNREPLY"),
    (TEMPLATE_BUTTON_MSG, "wamid.CTX_BTN"),
])
def test_cloud_citacao_vale_para_todo_tipo(cloud, msg, expected):
    """``context`` é campo de MENSAGEM, não de tipo. R10: em button/interactive
    ele está SEMPRE presente e aponta para a NOSSA mensagem com os botões —
    citação legítima (é como o WhatsApp mostra no celular), não filtrada."""
    assert _parse(cloud, msg).reply_to_msg_id == expected


def test_cloud_citacao_sobrevive_ao_parse_inbound_completo(cloud):
    msg = dict(TEXT_MSG, context={"from": "15550783881", "id": "wamid.ALVO"})
    events = cloud.parse_inbound(_envelope(msg))
    assert [e.reply_to_msg_id for e in events] == ["wamid.ALVO"]


# ══════════════════════════════════════════════════════════════════════════
# F9 — citação do cliente (Telegram)
# ══════════════════════════════════════════════════════════════════════════

def _tg_update(msg: dict) -> dict:
    return {"update_id": 1, "message": msg}


# Reprodução real em produção (canal telegram_9bf7bdfc, conversa 138,
# 2026-07-22 09:54): a msg 74057 responde a 74056 e o campo nascia NULL.
TG_REPLY_UPDATE = _tg_update({
    "message_id": 74057, "date": 1753181652,
    "chat": {"id": 5551234, "type": "private"},
    "from": {"id": 5551234, "first_name": "Thiago"},
    "text": "teste respondendo a mensagem",
    "reply_to_message": {"message_id": 74056, "date": 1753181624,
                         "chat": {"id": 5551234, "type": "private"},
                         "from": {"id": 5551234, "first_name": "Thiago"},
                         "text": "teste"},
})


def test_telegram_reply_to_message_vira_reply_to(telegram):
    events = telegram.parse_inbound(TG_REPLY_UPDATE)
    assert len(events) == 1
    ev = events[0]
    assert ev.reply_to_msg_id == "74056"
    # O external_msg_id é o inteiro em string ⇒ os dois lados casam por
    # igualdade exata, sem normalização (verificado em produção).
    assert ev.external_msg_id == "74057"
    assert ev.text == "teste respondendo a mensagem"


def test_telegram_sem_reply_nao_tem_reply_to(telegram):
    events = telegram.parse_inbound(_tg_update({
        "message_id": 74056, "date": 1753181624,
        "chat": {"id": 5551234, "type": "private"},
        "from": {"id": 5551234, "first_name": "Thiago"},
        "text": "teste"}))
    assert events[0].reply_to_msg_id is None


@pytest.mark.parametrize("bad", ["nope", 42, [], None, {}, {"message_id": None},
                                 {"message_id": ""}])
def test_telegram_reply_to_message_malformado_nao_quebra(telegram, bad):
    events = telegram.parse_inbound(_tg_update({
        "message_id": 9, "date": 1, "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "first_name": "T"}, "text": "oi",
        "reply_to_message": bad}))
    assert events[0].reply_to_msg_id is None
    assert events[0].text == "oi"


def test_telegram_citacao_em_midia_com_legenda(telegram):
    events = telegram.parse_inbound(_tg_update({
        "message_id": 74058, "date": 1753181700,
        "chat": {"id": 5551234, "type": "private"},
        "from": {"id": 5551234, "first_name": "Thiago"},
        "caption": "olha",
        "photo": [{"file_id": "SMALL"}, {"file_id": "BIG"}],
        "reply_to_message": {"message_id": 74056}}))
    ev = events[0]
    assert ev.reply_to_msg_id == "74056"
    assert ev.media_type == "image"
    assert ev.media_extras["media_id"] == "BIG"
    assert ev.text == "olha"


def test_regressao_telegram_texto_e_midia_inalterados(telegram):
    events = telegram.parse_inbound(_tg_update({
        "message_id": 1, "date": 2, "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "first_name": "Ana", "last_name": "Paula"},
        "voice": {"file_id": "V1", "duration": 3, "mime_type": "audio/ogg"}}))
    ev = events[0]
    assert ev.media_type == "audio"
    assert ev.media_extras == {"media_id": "V1", "mime_type": "audio/ogg",
                               "duration_ms": 3000, "is_voice_note": True}
    assert ev.sender_name == "Ana Paula"
    assert ev.chat_id == "7"
    assert ev.reply_to_msg_id is None


# ══════════════════════════════════════════════════════════════════════════
# Import defensivo do formatter (F2 item 4)
# ══════════════════════════════════════════════════════════════════════════

def test_formatter_ausente_nao_impede_o_plugin_de_carregar(cloud):
    """Se o zip rodar sem ``inbound_text.py``, ``describe_message`` cai num stub
    que devolve ``""`` — o parse continua, com o fallback genérico, e o
    ``button`` de template continua byte-idêntico."""
    original = _cloud_mod.describe_message
    _cloud_mod.describe_message = lambda msg: ""
    try:
        assert _parse(cloud, TEMPLATE_BUTTON_MSG).text == "Unsubscribe"
        assert _parse(cloud, CONTACTS_MSG).text == \
            '⚠️ Mensagem do tipo "contacts" não suportada'
        assert _parse(cloud, TEXT_MSG).text == "Can I get more info about this?"
    finally:
        _cloud_mod.describe_message = original
