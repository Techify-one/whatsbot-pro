"""Contact CRUD and messaging endpoints."""

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import File, Form, UploadFile
from gowa.client import GOWASendError

from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    gowa_client = deps.gowa_client
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings
    statics_senditems_dir = deps.statics_senditems_dir

    async def _send_read_receipts(phone: str, msg_ids: list[str]):
        """Send read receipts to GOWA in background (best-effort)."""
        for mid in msg_ids:
            try:
                await asyncio.to_thread(gowa_client.mark_as_read, mid, phone)
                logger.info("[ReadReceipt] Sent for %s msg %s", phone, mid)
            except Exception as e:
                logger.warning("[ReadReceipt] Failed for %s msg %s: %s", phone, mid, e)

    @app.get("/api/contacts")
    async def list_contacts(q: str = ""):
        """List all contacts with summary info."""
        def _list():
            contacts_dir = agent_handler.memory_dir
            results = []
            for f in contacts_dir.glob("*.json"):
                if f.stem.startswith("_"):
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    phone = data.get("phone", f.stem)
                    info = data.get("info", {})
                    msgs = data.get("messages", [])
                    # Skip transcription messages for preview
                    visible = [m for m in msgs if m.get("role") not in ("transcription", "system_notice")]
                    last = visible[-1] if visible else None
                    # Build last message preview with media indicator
                    last_content = ""
                    if last:
                        lmt = last.get("media_type")
                        if lmt == "image":
                            last_content = last.get("content", "")[:80] or "\U0001f4f7 Imagem"
                        elif lmt == "audio":
                            last_content = "\U0001f3a4 \u00c1udio"
                        else:
                            last_content = (last.get("content") or "")[:80]
                    is_group = data.get("is_group", False)
                    group_name = data.get("group_name", "")
                    results.append({
                        "id": data.get("id"),
                        "phone": phone,
                        "name": group_name if is_group else info.get("name", ""),
                        "last_message": last_content,
                        "last_message_role": last["role"] if last else "",
                        "last_message_ts": last.get("ts", 0) if last else 0,
                        "msg_count": len(msgs),
                        "unread_count": data.get("unread_count", 0),
                        "unread_ai_count": data.get("unread_ai_count", 0),
                        "ai_enabled": data.get("ai_enabled", True),
                        "is_group": is_group,
                        "group_name": group_name,
                        "updated_at": data.get("updated_at", 0),
                    })
                except Exception:
                    continue
            results.sort(key=lambda c: c["updated_at"], reverse=True)
            if q:
                ql = q.lower()
                results = [c for c in results if ql in c["name"].lower()
                           or ql in c["phone"]
                           or ql in c.get("group_name", "").lower()]
            return results
        return _ok(await asyncio.to_thread(_list))

    @app.get("/api/contacts/{phone}")
    async def get_contact(phone: str):
        """Return full contact data including conversation history."""
        def _load():
            fp = agent_handler.memory_dir / f"{phone}.json"
            if not fp.exists():
                return None, []
            data = json.loads(fp.read_text(encoding="utf-8"))
            msg_ids: list[str] = []
            # Mark as read when viewing contact
            if data.get("unread_count", 0) > 0 or data.get("unread_ai_count", 0) > 0:
                data["unread_count"] = 0
                data["unread_ai_count"] = 0
                msg_ids = data.pop("unread_msg_ids", [])
                data["unread_msg_ids"] = []
                fp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                if phone in agent_handler._contacts:
                    agent_handler._contacts[phone].unread_count = 0
                    agent_handler._contacts[phone].unread_ai_count = 0
                    agent_handler._contacts[phone].unread_msg_ids.clear()
            return data, msg_ids
        data, msg_ids = await asyncio.to_thread(_load)
        if data is None:
            return _err("Contato não encontrado.", status=404)
        if msg_ids:
            asyncio.create_task(_send_read_receipts(phone, msg_ids))
        return _ok(data)

    @app.post("/api/contacts/{phone}/send")
    async def send_to_contact(phone: str, body: dict):
        """Send a manual message to a contact (operator-initiated, no LLM)."""
        message = (body.get("message") or "").strip()
        if not message:
            return _err("Campo 'message' é obrigatório.")

        # Track sent message to filter GOWA echo-backs (must be before send)
        state.recently_sent[f"{phone}:{message[:120]}"] = time.time()

        # Try to send via GOWA — always save message (with status on failure)
        send_failed = False
        error_msg = ""
        try:
            await asyncio.to_thread(gowa_client.send_message, phone, message)
        except GOWASendError as e:
            logger.error("[Send] Failed to send message to %s: %s", phone, e)
            send_failed = True
            error_msg = str(e)
        except Exception as e:
            logger.error("[Send] Failed to send message to %s: %s", phone, e)
            send_failed = True
            error_msg = str(e)

        # Always save to contact memory (with status="failed" if send failed)
        try:
            msg_data = await asyncio.to_thread(
                agent_handler.save_operator_message, phone, message,
                status="failed" if send_failed else None,
            )
        except Exception as e:
            logger.error("[Send] Failed to save message for %s: %s", phone, e)
            return _err(f"Erro ao salvar mensagem: {e}", status=500)

        if send_failed:
            # Broadcast error event for frontend toast/error bubble
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "error",
                    "content": f"Falha ao enviar mensagem: {error_msg}",
                    "ts": time.time(),
                },
            })
            return _err(f"Falha ao enviar mensagem: {error_msg}", status=500)

        logger.info("[Send] Manual message to %s: %s", phone, message[:80])

        # Broadcast to all WS clients
        await ws_manager.broadcast("new_message", {
            "phone": phone,
            "message": msg_data,
        })

        return _ok({"message": "Mensagem enviada."})

    @app.post("/api/contacts/{phone}/retry-send")
    async def retry_send_to_contact(phone: str, body: dict):
        """Retry sending a message that previously failed."""
        message = (body.get("message") or "").strip()
        if not message:
            return _err("Campo 'message' é obrigatório.")

        # Track for echo-back filtering
        state.recently_sent[f"{phone}:{message[:120]}"] = time.time()

        try:
            await asyncio.to_thread(gowa_client.send_message, phone, message)
        except GOWASendError as e:
            logger.error("[Retry] Failed to resend to %s: %s", phone, e)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "error",
                    "content": f"Falha ao reenviar mensagem: {e}",
                    "ts": time.time(),
                },
            })
            return _err(f"Falha ao reenviar: {e}", status=500)
        except Exception as e:
            logger.error("[Retry] Failed to resend to %s: %s", phone, e)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "error",
                    "content": f"Erro inesperado ao reenviar: {e}",
                    "ts": time.time(),
                },
            })
            return _err(f"Erro ao reenviar: {e}", status=500)

        # Mark the existing failed message as sent (remove status)
        try:
            await asyncio.to_thread(agent_handler.mark_message_sent, phone, message)
        except Exception as e:
            logger.error("[Retry] Failed to update message status for %s: %s", phone, e)

        state.msg_count += 1
        logger.info("[Retry] Resent to %s: %s", phone, message[:80])
        return _ok({"message": "Mensagem reenviada."})

    @app.post("/api/contacts/{phone}/send-image")
    async def send_image_to_contact(
        phone: str,
        image: UploadFile = File(...),
        caption: str = Form(""),
    ):
        """Send an image to a contact (operator-initiated)."""
        suffix = Path(image.filename or "img.png").suffix or ".png"
        dest = statics_senditems_dir / f"{int(time.time() * 1000)}{suffix}"
        content = await image.read()
        dest.write_bytes(content)

        try:
            await asyncio.to_thread(gowa_client.send_image, phone, str(dest), caption)
        except GOWASendError as e:
            logger.error("[Send] Failed to send image to %s: %s", phone, e)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "error",
                    "content": f"Falha ao enviar imagem: {e}",
                    "ts": time.time(),
                },
            })
            return _err(f"Falha ao enviar imagem: {e}", status=500)
        except Exception as e:
            logger.error("[Send] Failed to send image to %s: %s", phone, e)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "error",
                    "content": f"Erro inesperado ao enviar imagem: {e}",
                    "ts": time.time(),
                },
            })
            return _err(f"Erro ao enviar imagem: {e}", status=500)

        # Relative path for storage and frontend
        rel_path = f"statics/senditems/{dest.name}"
        msg_data = {
            "role": "assistant",
            "content": caption,
            "ts": time.time(),
            "media_type": "image",
            "media_path": rel_path,
        }
        contact = agent_handler._get_contact(phone)
        contact.add_message("assistant", caption, media_type="image", media_path=rel_path)

        await ws_manager.broadcast("new_message", {"phone": phone, "message": msg_data})
        logger.info("[Send] Image sent to %s", phone)
        return _ok({"message": "Imagem enviada."})

    @app.post("/api/contacts/{phone}/send-audio")
    async def send_audio_to_contact(
        phone: str,
        audio: UploadFile = File(...),
    ):
        """Send an audio file to a contact (operator-initiated)."""
        suffix = Path(audio.filename or "voice.ogg").suffix or ".ogg"
        dest = statics_senditems_dir / f"{int(time.time() * 1000)}{suffix}"
        content = await audio.read()
        dest.write_bytes(content)

        try:
            await asyncio.to_thread(gowa_client.send_audio, phone, str(dest))
        except GOWASendError as e:
            logger.error("[Send] Failed to send audio to %s: %s", phone, e)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "error",
                    "content": f"Falha ao enviar áudio: {e}",
                    "ts": time.time(),
                },
            })
            return _err(f"Falha ao enviar áudio: {e}", status=500)
        except Exception as e:
            logger.error("[Send] Failed to send audio to %s: %s", phone, e)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {
                    "role": "error",
                    "content": f"Erro inesperado ao enviar áudio: {e}",
                    "ts": time.time(),
                },
            })
            return _err(f"Erro ao enviar áudio: {e}", status=500)

        rel_path = f"statics/senditems/{dest.name}"
        msg_data = {
            "role": "assistant",
            "content": "[Áudio]",
            "ts": time.time(),
            "media_type": "audio",
            "media_path": rel_path,
        }
        contact = agent_handler._get_contact(phone)
        contact.add_message("assistant", "[Áudio]", media_type="audio", media_path=rel_path)

        await ws_manager.broadcast("new_message", {"phone": phone, "message": msg_data})
        logger.info("[Send] Audio sent to %s", phone)
        return _ok({"message": "Áudio enviado."})

    @app.post("/api/contacts/{phone}/presence")
    async def send_presence_to_contact(phone: str, body: dict):
        """Send typing/stop presence indicator to a contact (operator-initiated)."""
        action = body.get("action", "start")
        await asyncio.to_thread(gowa_client.send_chat_presence, phone, action)
        return _ok({"status": "ok"})

    @app.post("/api/contacts/{phone}/read")
    async def mark_contact_read(phone: str):
        """Mark all messages from this contact as read (reset unread_count)."""
        def _mark():
            contact = agent_handler._get_contact(phone)
            return contact.mark_as_read()
        msg_ids = await asyncio.to_thread(_mark)
        if msg_ids:
            asyncio.create_task(_send_read_receipts(phone, msg_ids))
        return _ok({"message": "Marcado como lido."})

    @app.post("/api/contacts/{phone}/toggle-ai")
    async def toggle_contact_ai(phone: str, body: dict):
        """Enable or disable AI auto-reply for a specific contact."""
        enabled = body.get("enabled")
        if enabled is None:
            return _err("Campo 'enabled' é obrigatório.")
        def _toggle():
            contact = agent_handler._get_contact(phone)
            contact.set_ai_enabled(bool(enabled))
            return contact.ai_enabled
        result = await asyncio.to_thread(_toggle)
        await ws_manager.broadcast("contact_ai_toggled", {
            "phone": phone,
            "ai_enabled": result,
        })
        return _ok({"ai_enabled": result})

    @app.put("/api/contacts/{phone}/info")
    async def update_contact_info(phone: str, body: dict):
        """Update contact info fields (name, email, profession, company, observations)."""
        def _update():
            contact = agent_handler._get_contact(phone)
            # Update scalar fields via update_info
            contact.update_info(
                name=body.get("name", ""),
                email=body.get("email", ""),
                profession=body.get("profession", ""),
                company=body.get("company", ""),
            )
            # Observations: replace entire list (update_info only appends)
            if "observations" in body:
                contact.info["observations"] = [
                    o for o in body["observations"] if isinstance(o, str) and o.strip()
                ]
                contact.save()
            return contact.info
        info = await asyncio.to_thread(_update)
        return _ok(info)
