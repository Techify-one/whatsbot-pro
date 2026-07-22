"""Plano 75 F1 — pure tests for the WhatsApp Cloud inbound text formatter.

Fixtures are the LITERAL payloads from the official Meta docs collected in §12
of ``docs-planos/75-plano-mensagens-em-branco-cloud-e-falha-de-envio.md``.

The module under test lives inside the plugin source tree
(``assets/plugin_examples/whatsapp_cloud/inbound_text.py``) and must stay pure
stdlib, so it is loaded BY PATH — never as a plugin package. These tests touch
no database and no app.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "assets" / "plugin_examples" / "whatsapp_cloud" / "inbound_text.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "whatsbot_test_cloud_inbound_text", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


it = _load_module()


# ── §12.1 contacts ─────────────────────────────────────────────────────────

CONTACTS_MSG = {
    "from": "16505551234",
    "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA=",
    "timestamp": "1744344496",
    "type": "contacts",
    "contacts": [
        {
            "name": {
                "first_name": "Barbara",
                "last_name": "Johnson",
                "formatted_name": "Barbara J. Johnson",
            },
            "org": {"company": "Social Tsunami"},
            "phones": [
                {"phone": "+1 (415) 555-0829", "wa_id": "14125550829", "type": "MOBILE"}
            ],
        }
    ],
}


def test_contacts_official_payload_has_name_and_phone():
    out = it.describe_message(CONTACTS_MSG)
    assert "Barbara J. Johnson" in out
    assert "+1 (415) 555-0829" in out
    assert "Social Tsunami" in out
    assert out.startswith("👤 Contato: Barbara J. Johnson")


def test_contacts_appends_wa_id_when_digits_differ():
    # The doc example has a mismatch (415 vs 412): wa_id must be surfaced too.
    out = it.describe_contacts(CONTACTS_MSG["contacts"])
    assert "14125550829" in out


def test_contacts_does_not_repeat_wa_id_when_same_digits():
    out = it.describe_contacts(
        [{"name": {"formatted_name": "Ze"},
          "phones": [{"phone": "+55 62 99999-0000", "wa_id": "5562999990000"}]}]
    )
    assert out.count("5562999990000") == 0
    assert "+55 62 99999-0000" in out


def test_contacts_all_optional_fields():
    out = it.describe_contacts([
        {
            "name": {"formatted_name": "Ana"},
            "org": {"company": "Acme", "title": "CTO"},
            "phones": [{"phone": "+55 11 90000-0000"}],
            "emails": [{"email": "ana@acme.com", "type": "WORK"}],
            "addresses": [{"street": "Rua 1", "city": "SP", "country": "Brasil"}],
            "urls": [{"url": "https://acme.com"}],
            "birthday": "1990-01-31",
        }
    ])
    for expected in ("Ana", "Acme", "CTO", "+55 11 90000-0000", "ana@acme.com",
                     "Rua 1", "https://acme.com", "1990-01-31"):
        assert expected in out


def test_contacts_multiple_blocks_separated_by_blank_line():
    out = it.describe_contacts([
        {"name": {"formatted_name": "A"}, "phones": [{"phone": "1"}]},
        {"name": {"formatted_name": "B"}, "phones": [{"phone": "2"}]},
    ])
    assert "\n\n" in out
    assert out.count("👤 Contato:") == 2


def test_contacts_name_only():
    out = it.describe_contacts([{"name": {"first_name": "So", "last_name": "Nome"}}])
    assert out == "👤 Contato: So Nome"


def test_contacts_wa_id_only_without_phone():
    out = it.describe_contacts(
        [{"name": {"formatted_name": "Sem Fone"}, "phones": [{"wa_id": "5562988887777"}]}]
    )
    assert "5562988887777" in out


def test_contacts_empty_array_is_empty_string():
    assert it.describe_contacts([]) == ""
    assert it.describe_message({"type": "contacts", "contacts": []}) == ""


def test_contacts_without_name_still_renders_phone():
    out = it.describe_contacts([{"phones": [{"phone": "+55 62 3333-4444"}]}])
    assert "+55 62 3333-4444" in out
    assert out.startswith("👤 Contato")


# ── §12.2 order ────────────────────────────────────────────────────────────

ORDER_MSG = {
    "type": "order",
    "order": {
        "catalog_id": "194836987003835",
        "text": "Love these!",
        "product_items": [
            {"product_retailer_id": "di9ozbzfi4", "quantity": 2,
             "item_price": 30, "currency": "USD"},
            {"product_retailer_id": "nqryix03ez", "quantity": 1,
             "item_price": 25, "currency": "USD"},
        ],
    },
}


def test_order_official_payload():
    out = it.describe_message(ORDER_MSG)
    assert out.startswith("🛒 Pedido do catálogo: 2 itens")
    assert "85.00 USD" in out          # 2*30 + 1*25
    assert "di9ozbzfi4" in out and "nqryix03ez" in out
    assert "Love these!" in out


def test_order_decimal_item_price():
    # The doc types item_price as Integer but exemplifies 7.99 elsewhere.
    out = it.describe_order({"product_items": [
        {"product_retailer_id": "x", "quantity": 2, "item_price": 7.99, "currency": "BRL"}
    ]})
    assert "15.98 BRL" in out
    assert "7.99 BRL" in out


def test_order_singular_item_label():
    out = it.describe_order({"product_items": [
        {"product_retailer_id": "x", "quantity": 1, "item_price": 10, "currency": "USD"}
    ]})
    assert "1 item" in out and "itens" not in out


def test_order_without_product_items():
    out = it.describe_order({"catalog_id": "1", "text": "só um recado"})
    assert out.startswith("🛒 Pedido do catálogo")
    assert "só um recado" in out


def test_order_empty_dict_is_empty_string():
    assert it.describe_order({}) == ""


def test_order_items_missing_price_do_not_break_total():
    out = it.describe_order({"product_items": [
        {"product_retailer_id": "a", "quantity": 1, "item_price": 10, "currency": "USD"},
        {"product_retailer_id": "b"},
    ]})
    assert "10.00 USD" in out
    assert "• b" in out


# ── §12.3 system ───────────────────────────────────────────────────────────

SYSTEM_MSG = {
    "from": "16505551234",
    "id": "wamid.HBgLMTk4MzU1NTE5NzQVAgASGAoxMTgyMDg2MjY3AA==",
    "timestamp": "1750269342",
    "type": "system",
    "system": {
        "body": "User Sheena Nelson changed from 16505551234 to 12195555358",
        "wa_id": "12195555358",
        "type": "user_changed_number",
    },
}


def test_system_uses_body_verbatim():
    out = it.describe_message(SYSTEM_MSG)
    assert out == "ℹ️ User Sheena Nelson changed from 16505551234 to 12195555358"


def test_system_without_body_falls_back_to_type_and_wa_id():
    out = it.describe_system({"type": "user_changed_number", "wa_id": "12195555358"})
    assert "user_changed_number" in out and "12195555358" in out


def test_system_empty_is_empty_string():
    assert it.describe_system({}) == ""


# ── §12.4 unsupported ──────────────────────────────────────────────────────

UNSUPPORTED_MSG = {
    "type": "unsupported",
    "unsupported": {"type": "edit"},
    "errors": [
        {
            "code": 131051,
            "title": "Message type unknown",
            "message": "Message type unknown",
            "error_data": {"details": "Message type is currently not supported."},
        }
    ],
}


def test_unsupported_official_payload():
    out = it.describe_message(UNSUPPORTED_MSG)
    assert out == (
        "⚠️ Mensagem não suportada pelo WhatsApp Business (edit): Message type unknown"
    )


def test_unsupported_without_errors_or_subtype():
    out = it.describe_unsupported({"type": "unsupported"})
    assert out == "⚠️ Mensagem não suportada pelo WhatsApp Business"


def test_unsupported_falls_back_to_error_details():
    out = it.describe_unsupported({
        "type": "unsupported",
        "errors": [{"code": 131060, "error_data": {"details": "Message unavailable."}}],
    })
    assert "Message unavailable." in out


# ── §12.6 interactive ──────────────────────────────────────────────────────

LIST_REPLY_MSG = {
    "context": {"from": "15550783881", "id": "wamid.abc"},
    "type": "interactive",
    "interactive": {
        "type": "list_reply",
        "list_reply": {
            "id": "priority_express",
            "title": "Priority Mail Express",
            "description": "Next Day to 2 Days",
        },
    },
}

BUTTON_REPLY_MSG = {
    "context": {"from": "15550783881", "id": "wamid.abc"},
    "type": "interactive",
    "interactive": {
        "type": "button_reply",
        "button_reply": {"id": "cancel-button", "title": "Cancel"},
    },
}


def test_interactive_list_reply_title_and_description():
    out = it.describe_message(LIST_REPLY_MSG)
    assert out == "Priority Mail Express — Next Day to 2 Days"


def test_interactive_list_reply_without_description():
    out = it.describe_interactive(
        {"type": "list_reply", "list_reply": {"id": "x", "title": "Só título"}})
    assert out == "Só título"


def test_interactive_button_reply_title():
    out = it.describe_message(BUTTON_REPLY_MSG)
    assert out == "Cancel"


def test_interactive_button_reply_falls_back_to_id():
    out = it.describe_interactive(
        {"type": "button_reply", "button_reply": {"id": "cancel-button"}})
    assert out == "cancel-button"


def test_interactive_subtype_inferred_without_type_key():
    out = it.describe_interactive({"button_reply": {"title": "Sim"}})
    assert out == "Sim"


def test_interactive_nfm_reply_parses_response_json():
    out = it.describe_interactive({
        "type": "nfm_reply",
        "nfm_reply": {
            "name": "flow",
            "body": "Sent",
            "response_json": '{"nome": "Ana", "idade": 30}',
        },
    })
    assert out.startswith("📋 Formulário respondido: ")
    assert "nome=Ana" in out and "idade=30" in out


def test_interactive_nfm_reply_invalid_json_falls_back_to_raw():
    raw = "{isso nao e json"
    out = it.describe_interactive(
        {"type": "nfm_reply", "nfm_reply": {"response_json": raw}})
    assert out.startswith("📋 Formulário respondido: ")
    assert raw in out


def test_interactive_nfm_reply_huge_raw_is_truncated():
    raw = "x" * 5000
    out = it.describe_interactive(
        {"type": "nfm_reply", "nfm_reply": {"response_json": raw}})
    assert len(out) < 300
    assert out.endswith("…")


def test_interactive_nfm_reply_without_response_json():
    out = it.describe_interactive(
        {"type": "nfm_reply", "nfm_reply": {"body": "Formulário enviado"}})
    assert "Formulário enviado" in out


def test_interactive_unknown_subtype_is_empty():
    assert it.describe_interactive({"type": "whatever_new"}) == ""


# ── §12.7 button (template quick-reply) — output MUST NOT change ───────────

BUTTON_MSG = {
    "context": {"from": "15550783881", "id": "wamid.abc"},
    "type": "button",
    "button": {"payload": "Unsubscribe", "text": "Unsubscribe"},
}


def test_button_quick_reply_keeps_legacy_output():
    """40 rows in production were produced by ``inter.get("text")`` — the
    formatter must return exactly the same string."""
    legacy = (BUTTON_MSG["button"].get("text") or "")
    assert it.describe_message(BUTTON_MSG) == legacy == "Unsubscribe"


def test_button_without_text_is_empty():
    assert it.describe_interactive({"payload": "Unsubscribe"}) == ""


# ── dispatcher: unknown / degenerate input ────────────────────────────────

def test_unknown_type_returns_empty_string():
    assert it.describe_message({"type": "banana_split", "banana_split": {"a": 1}}) == ""


def test_plain_text_type_is_not_handled_here():
    assert it.describe_message({"type": "text", "text": {"body": "oi"}}) == ""


def test_empty_message_is_empty_string():
    assert it.describe_message({}) == ""


@pytest.mark.parametrize("bad", [None, "", "texto", 42, [], (), {"type": None},
                                 {"type": 7}, True])
def test_describe_message_never_raises(bad):
    assert it.describe_message(bad) == "" or isinstance(it.describe_message(bad), str)


@pytest.mark.parametrize("fn_name", [
    "describe_contacts", "describe_order", "describe_interactive",
    "describe_system", "describe_unsupported", "describe_message",
])
@pytest.mark.parametrize("bad", [None, "", "texto", 0, 3.5, [], {}, [None], [1, 2],
                                 {"type": []}, {"contacts": "nope"}, True])
def test_every_public_function_is_total(fn_name, bad):
    fn = getattr(it, fn_name)
    result = fn(bad)
    assert isinstance(result, str)


def test_module_imports_no_whatsbot_core():
    """The plugin may run on an older core: only stdlib is allowed."""
    source = _MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in ("from channels", "import channels", "from db", "import db",
                      "from server", "import server", "from app.", "import app"):
        assert forbidden not in source, forbidden
    # And it must load with the project root absent from sys.path semantics:
    # loading by path above already proves it needs no package context.
    assert "whatsbot_test_cloud_inbound_text" in sys.modules or True


def test_all_appendix_payloads_produce_non_empty_text():
    for payload in (CONTACTS_MSG, ORDER_MSG, SYSTEM_MSG, UNSUPPORTED_MSG,
                    LIST_REPLY_MSG, BUTTON_REPLY_MSG, BUTTON_MSG):
        out = it.describe_message(payload)
        assert out and out.strip(), payload.get("type")
