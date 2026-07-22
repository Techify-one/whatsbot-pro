#!/usr/bin/env python3
"""Injetor manual dos payloads do Plano 75 (NÃO é teste automatizado).

Posta no webhook LOCAL os payloads **oficiais da Meta** (apêndice §12 do
plano) para validar no painel, sem depender de a Meta mandar de verdade —
inclusive os casos que não dá para disparar de propósito (falha de entrega).

O nome ``manual_*`` mantém o arquivo fora da coleta do pytest (que só pega
``test_*.py``), no mesmo espírito de ``tests/manual_cloud_api_test.py``.

Uso::

    ./venv/bin/python tests/manual_plano75_inject.py --list
    ./venv/bin/python tests/manual_plano75_inject.py --case contacts
    ./venv/bin/python tests/manual_plano75_inject.py --all
    ./venv/bin/python tests/manual_plano75_inject.py --case failed --code 131047

Requer o dev server rodando (``./linux_start.sh``, porta 8090). O endpoint
``/api/webhook/{provider}/{channel_id}`` é isento de auth por desenho, então
não precisa de token.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_BASE = "http://127.0.0.1:8090"
DEFAULT_CHANNEL = "teste"          # canal whatsapp_cloud do dev (tabela channels)
DEFAULT_PHONE = "5599999990075"    # número fake exclusivo deste teste
PHONE_NUMBER_ID = "106540352242922"


def _wamid(suffix: str) -> str:
    """wamid sintético estável por caso (o formato real da Meta é opaco)."""
    return f"wamid.PLANO75{suffix.upper().replace('_', '')}=="


def envelope(phone: str, *, messages: list | None = None,
             statuses: list | None = None, with_contact: bool = True) -> dict:
    """Envelope comum do webhook da Meta (entry[].changes[].value)."""
    value: dict = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "5562999999999",
                     "phone_number_id": PHONE_NUMBER_ID},
    }
    if messages is not None:
        if with_contact:
            value["contacts"] = [
                {"profile": {"name": "Teste Plano 75"}, "wa_id": phone}]
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "0", "changes": [{"value": value,
                                               "field": "messages"}]}]}


def _base_msg(phone: str, case: str, msg_type: str) -> dict:
    return {"from": phone, "id": _wamid(case),
            "timestamp": str(int(time.time())), "type": msg_type}


# ── Casos (payloads verbatim do apêndice §12 do plano) ──────────────────────

def case_contacts(phone: str) -> dict:
    """§12.1 — o caso REAL de produção (msg 633446): bolha vazia."""
    msg = _base_msg(phone, "contacts", "contacts")
    msg["contacts"] = [{
        "name": {"first_name": "Barbara", "last_name": "Johnson",
                 "formatted_name": "Barbara J. Johnson"},
        "org": {"company": "Social Tsunami"},
        "phones": [{"phone": "+1 (415) 555-0829", "wa_id": "14125550829",
                    "type": "MOBILE"}],
    }]
    return envelope(phone, messages=[msg])


def case_order(phone: str) -> dict:
    """§12.2 — pedido do catálogo."""
    msg = _base_msg(phone, "order", "order")
    msg["order"] = {
        "catalog_id": "194836987003835", "text": "Love these!",
        "product_items": [
            {"product_retailer_id": "di9ozbzfi4", "quantity": 2,
             "item_price": 30, "currency": "USD"},
            {"product_retailer_id": "nqryix03ez", "quantity": 1,
             "item_price": 25, "currency": "USD"},
        ],
    }
    return envelope(phone, messages=[msg])


def case_system(phone: str) -> dict:
    """§12.3 — cliente trocou de número. Sem array contacts[] (de propósito)."""
    msg = _base_msg(phone, "system", "system")
    msg["system"] = {
        "body": "User Sheena Nelson changed from 16505551234 to 12195555358",
        "wa_id": "12195555358", "type": "user_changed_number"}
    return envelope(phone, messages=[msg], with_contact=False)


def case_unsupported(phone: str) -> dict:
    """§12.4 — tipo não suportado pelo WhatsApp Business."""
    msg = _base_msg(phone, "unsupported", "unsupported")
    msg["unsupported"] = {"type": "edit"}
    msg["errors"] = [{"code": 131051, "title": "Message type unknown",
                      "message": "Message type unknown",
                      "error_data": {"details": "Message type is currently not supported."}}]
    return envelope(phone, messages=[msg])


def case_list_reply(phone: str) -> dict:
    """§12.6 — bug LATENTE: hoje lê interactive.text, chave inexistente."""
    msg = _base_msg(phone, "listreply", "interactive")
    msg["context"] = {"from": PHONE_NUMBER_ID, "id": _wamid("ourlist")}
    msg["interactive"] = {"type": "list_reply", "list_reply": {
        "id": "priority_express", "title": "Priority Mail Express",
        "description": "Next Day to 2 Days"}}
    return envelope(phone, messages=[msg])


def case_button_reply(phone: str) -> dict:
    """§12.6 — idem, sub-tipo button_reply."""
    msg = _base_msg(phone, "buttonreply", "interactive")
    msg["context"] = {"from": PHONE_NUMBER_ID, "id": _wamid("ourbuttons")}
    msg["interactive"] = {"type": "button_reply", "button_reply": {
        "id": "cancel-button", "title": "Cancel"}}
    return envelope(phone, messages=[msg])


def case_button(phone: str) -> dict:
    """§12.7 — quick-reply de template. REGRESSÃO: 40 linhas em prod, saída não pode mudar."""
    msg = _base_msg(phone, "button", "button")
    msg["context"] = {"from": PHONE_NUMBER_ID, "id": _wamid("ourtemplate")}
    msg["button"] = {"payload": "Unsubscribe", "text": "Unsubscribe"}
    return envelope(phone, messages=[msg])


def case_unknown(phone: str) -> dict:
    """Tipo inventado — exercita o fallback genérico E a rede de segurança do core (F3)."""
    msg = _base_msg(phone, "unknown", "tipo_inventado_75")
    msg["tipo_inventado_75"] = {"foo": "bar"}
    return envelope(phone, messages=[msg])


def case_text(phone: str) -> dict:
    """Mensagem de texto normal — REGRESSÃO: tem que continuar idêntica."""
    msg = _base_msg(phone, "text", "text")
    msg["text"] = {"body": "mensagem de texto normal (regressão do plano 75)"}
    return envelope(phone, messages=[msg])


def case_reply(phone: str) -> list[dict]:
    """Frente C — duas mensagens: a original e a resposta CITANDO ela.

    Devolve DOIS envelopes; o 2º carrega ``context.id`` apontando para o 1º.
    É a reprodução do bug que o cliente relatou ("nao ta aparecendo para voce
    no sistema parece").
    """
    original_id = _wamid("replyorig")
    first = _base_msg(phone, "replyorig", "text")
    first["id"] = original_id
    first["text"] = {"body": "mensagem original que vai ser citada"}

    second = _base_msg(phone, "replyquote", "text")
    second["context"] = {"from": phone, "id": original_id}
    second["text"] = {"body": "esta é a RESPOSTA citando a mensagem acima"}
    return [envelope(phone, messages=[first]),
            envelope(phone, messages=[second])]


def case_failed(phone: str, code: int, *, msg_id: str | None = None) -> dict:
    """§12.8 — a Meta avisa que NÃO entregou (o pedido original do usuário).

    Precisa de uma mensagem de SAÍDA já existente para marcar; use
    ``--seed-outbound`` para o script criar uma antes.
    """
    errors_by_code = {
        131047: "More than 24 hours have passed since the recipient last replied to the sender number.",
        131049: "This message was not delivered to maintain healthy ecosystem engagement.",
        131026: "Unable to deliver message.",
        132001: "The template does not exist in the specified language or the template has not been approved.",
        999999: "Codigo desconhecido de proposito (testa o fallback)",
    }
    title = errors_by_code.get(code, "Unknown error")
    return envelope(phone, statuses=[{
        "id": msg_id or _wamid("outbound"),
        "status": "failed",
        "timestamp": str(int(time.time())),
        "recipient_id": phone,
        "errors": [{"code": code, "title": title, "message": title,
                    "error_data": {"details": title},
                    "href": "/documentation/business-messaging/whatsapp/support/error-codes"}],
    }])


CASES = {
    "text": ("regressão: texto normal", case_text),
    "contacts": ("§12.1 card de contato — O CASO REAL (msg 633446)", case_contacts),
    "order": ("§12.2 pedido do catálogo", case_order),
    "system": ("§12.3 cliente trocou de número", case_system),
    "unsupported": ("§12.4 tipo não suportado", case_unsupported),
    "list_reply": ("§12.6 interactive/list_reply (bug latente)", case_list_reply),
    "button_reply": ("§12.6 interactive/button_reply (bug latente)", case_button_reply),
    "button": ("§12.7 quick-reply de template (regressão)", case_button),
    "unknown": ("tipo inventado — fallback + rede de segurança F3", case_unknown),
    "reply": ("frente C: mensagem + resposta CITANDO ela", case_reply),
    "failed": ("§12.8 falha de entrega (bolha vermelha + card)", None),
}


def _seed_outbound(phone: str) -> str:
    """Cria uma mensagem de SAÍDA no banco para a falha ter o que marcar.

    Usa os repos do core (SQLAlchemy Core) — não escreve SQL cru.
    """
    import os
    for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            os.environ.setdefault("DATABASE_URL", line.split("=", 1)[1].strip())
    from db.connection import init_db
    from db.repositories import contact_repo, message_repo

    init_db()
    contact = contact_repo.get_or_create(phone)
    msg_id = _wamid("outbound")
    message_repo.add(contact["id"], "assistant",
                     "Mensagem de saída de teste (plano 75) — deve virar vermelha.",
                     ts=time.time(), msg_id=msg_id, status="sent")
    print(f"  · seed: mensagem de saída criada para {phone} com msg_id={msg_id}")
    return msg_id


def post(base: str, channel: str, payload: dict, *, provider: str = "whatsapp_cloud") -> None:
    url = f"{base}/api/webhook/{provider}/{channel}"
    resp = httpx.post(url, json=payload, timeout=30.0)
    body = resp.text[:300]
    print(f"  → POST {url} … {resp.status_code} {body}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL,
                        help="channel_id do canal whatsapp_cloud (tabela channels)")
    parser.add_argument("--phone", default=DEFAULT_PHONE)
    parser.add_argument("--case", help="um caso específico (ver --list)")
    parser.add_argument("--all", action="store_true", help="roda todos menos o failed")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--code", type=int, default=131047,
                        help="código de erro da Meta para --case failed")
    parser.add_argument("--seed-outbound", action="store_true",
                        help="cria a mensagem de saída no banco antes do --case failed")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="pausa entre envios (o webhook agrupa por message_batch_delay)")
    args = parser.parse_args()

    if args.list:
        print("Casos disponíveis:\n")
        for key, (desc, _) in CASES.items():
            print(f"  {key:<14} {desc}")
        return 0

    if not args.case and not args.all:
        parser.error("informe --case <nome> ou --all (ou --list)")

    names = [k for k in CASES if k != "failed"] if args.all else [args.case]
    for name in names:
        if name not in CASES:
            print(f"caso desconhecido: {name} (use --list)")
            return 2
        desc, builder = CASES[name]
        print(f"\n[{name}] {desc}")

        if name == "failed":
            msg_id = _seed_outbound(args.phone) if args.seed_outbound else None
            post(args.base_url, args.channel,
                 case_failed(args.phone, args.code, msg_id=msg_id))
        else:
            payload = builder(args.phone)
            for env in (payload if isinstance(payload, list) else [payload]):
                post(args.base_url, args.channel, env)
                time.sleep(args.delay)
        time.sleep(args.delay)

    print(f"\nPronto. Abra a conversa do número {args.phone} no painel "
          f"({args.base_url}) e confira as bolhas.")
    print("Lembrete: o webhook agrupa mensagens por message_batch_delay "
          "(padrão 3s) — espere alguns segundos antes de olhar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
