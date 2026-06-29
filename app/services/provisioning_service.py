"""Techify key provisioning / onboarding service (Plano 23 · Fase B6).

Owns the first-run setup-wizard provisioning flow previously inline in
``server/routes/setup.py``: resolving the connected WhatsApp number, fetching the
current Techify provisioning number, disabling the AI for that contact, sending
the provisioning message (with the reach-out-timelock manual fallback), arming the
key polling, and polling Techify for the provisioned API key (saving it to config
once ready).

Branch by Abstraction — the routes resolve permission, then delegate the flow
here. Behavior preserved byte-for-byte (legacy suite is the contract).
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import quote

import httpx
import segno

from config.settings import (
    TECHIFY_PROVISION_MESSAGE,
    TECHIFY_PROVISION_NUMBER,
    TECHIFY_REQUEST_APIKEY_URL,
    TECHIFY_SERVICE_NUMBER_URL,
)
from db.repositories import conversation_repo
from gowa.client import GOWASendError

logger = logging.getLogger(__name__)


def wa_deep_link(number: str, message: str) -> str:
    """Build a wa.me click-to-chat link that pre-fills ``message`` to ``number``."""
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote(message)}"


def qr_data_uri(url: str) -> str:
    """Render ``url`` as a PNG data URI QR code (scanning it opens WhatsApp)."""
    try:
        return segno.make(url, error="m").png_data_uri(scale=5, border=2)
    except Exception as e:  # never let QR rendering break provisioning
        logger.warning("Setup: failed to render provisioning QR: %s", e)
        return ""


async def fetch_provision_number() -> str:
    """Fetch the current Techify provisioning number from /service_number.

    Falls back to TECHIFY_PROVISION_NUMBER when the endpoint is unreachable or
    returns an unexpected body.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(TECHIFY_SERVICE_NUMBER_URL)
        resp.raise_for_status()
        data = resp.json()
        number = str(data.get("phone", "")).strip() if isinstance(data, dict) else ""
        if number:
            return number
        logger.warning("Setup: /service_number returned no phone, using fallback")
    except Exception as e:
        logger.warning("Setup: failed to fetch service number (%s), using fallback", e)
    return TECHIFY_PROVISION_NUMBER


async def request_key(deps) -> tuple[str, dict]:
    """Send the Techify provisioning message and arm the key polling.

    Returns a ``(kind, data)`` tuple the route turns into a response:
      * ``("no_number", {})`` — the WhatsApp number isn't known yet (route → _err);
      * ``("manual", {...})`` — reach-out timelock; the user must send by hand;
      * ``("send_failed", {"error": str})`` — the send failed (route → _err);
      * ``("sent", {"number": str})`` — provisioning message sent, polling armed.
    """
    gowa_client = deps.gowa_client
    agent_handler = deps.agent_handler
    state = deps.state

    # Resolve the connected WhatsApp number (digits only).
    number = (state.bot_phone or "").split(":")[0].strip()
    if not number:
        number = (await asyncio.to_thread(gowa_client.get_own_number) or "").strip()
        if number:
            state.bot_phone = number
    if not number:
        return ("no_number", {})

    provision_number = await fetch_provision_number()

    # The Techify provisioning number is a support/automation contact — the bot
    # must never auto-reply to it. Force AI off for that contact before the message
    # goes out, so it stays disabled even after Techify replies.
    try:
        def _disable_provision_ai():
            contact = agent_handler._get_contact(provision_number)
            contact.set_ai_enabled(False)  # legacy contact flag (back-compat)
            # P17: the AI gate is per-conversation now, so the contact flag no
            # longer silences the bot. Eagerly resolve the provisioning contact's
            # conversation and pause it (ai_active=0) so the bot never auto-replies
            # to Techify — even after Techify answers (the inbound reuses this open,
            # paused conversation instead of a fresh one).
            jid = f"{provision_number}@s.whatsapp.net"
            conv = conversation_repo.resolve_for_contact(contact.id, jid)
            conversation_repo.set_ai_active(conv["id"], 0)
        await asyncio.to_thread(_disable_provision_ai)
    except Exception as e:
        logger.warning(
            "Setup: could not disable AI for provisioning contact %s: %s",
            provision_number, e,
        )

    def _arm_polling():
        state.setup_key_number = number
        state.setup_key_requested_at = time.time()

    try:
        await asyncio.to_thread(
            gowa_client.send_message, provision_number, TECHIFY_PROVISION_MESSAGE
        )
    except GOWASendError as e:
        logger.error("Setup: failed to send provisioning message: %s", e)
        if getattr(e, "error_type", "") == "reachout_timelock":
            # WhatsApp's anti-spam restriction on first messages to new contacts
            # (error 463): the bot can't open the chat, but the *user* can, by hand,
            # from the phone linked to this session. Arm the key polling anyway and
            # hand the frontend everything it needs to guide that manual send —
            # once it goes out, the key lands in the config automatically.
            _arm_polling()
            wa_link = wa_deep_link(provision_number, TECHIFY_PROVISION_MESSAGE)
            logger.info(
                "Setup: reach-out timelock; falling back to manual send, "
                "polling key for %s", number,
            )
            return ("manual", {
                "status": "manual",
                "number": number,
                "provision_number": provision_number,
                "provision_message": TECHIFY_PROVISION_MESSAGE,
                "wa_link": wa_link,
                "qr_data_uri": qr_data_uri(wa_link),
            })
        return ("send_failed", {"error": str(e)})

    _arm_polling()
    logger.info("Setup: provisioning message sent, polling key for %s", number)
    return ("sent", {"number": number})


async def key_status(deps) -> dict:
    """Poll Techify for the provisioned API key; save it once ready.

    Returns the ``{"status": ...}`` payload the route returns verbatim.
    """
    settings = deps.settings
    agent_handler = deps.agent_handler
    state = deps.state

    number = state.setup_key_number
    if not number:
        return {"status": "pending"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                TECHIFY_REQUEST_APIKEY_URL, json={"number": number}
            )
        if resp.status_code != 200:
            logger.warning("Setup: Techify returned HTTP %s", resp.status_code)
            return {"status": "error"}
        data = resp.json()
    except Exception as e:
        logger.warning("Setup: key-status poll failed: %s", e)
        return {"status": "error"}

    if not isinstance(data, dict):
        logger.warning("Setup: Techify returned a non-object body")
        return {"status": "error"}

    status = data.get("status", "pending")
    api_key = data.get("api_key", "")
    account_url = data.get("account_url", "")
    access_token = data.get("access_token", "")

    if status == "ready" and api_key:
        settings["openrouter_api_key"] = api_key
        if account_url:
            settings["account_url"] = account_url
        if access_token:
            settings["access_token"] = access_token
        settings.save()
        agent_handler.update_config(
            api_key=api_key,
            audio_model=settings.get("audio_model", "google/gemini-2.5-flash"),
            image_model=settings.get("image_model", "google/gemini-2.5-flash"),
            max_context_messages=settings.get("max_context_messages", 10),
        )
        logger.info("Setup: API key provisioned and saved.")
        return {"status": "ready"}

    return {"status": status}
