"""Sandbox (debug chat) endpoints.

Runs the full agent pipeline locally — same as a real WhatsApp conversation —
but never touches GOWA: nothing is delivered over WhatsApp. Inbound messages are
saved as `user` messages, the AI reply is split/saved cleanly and every leg is
broadcast over WebSocket so open chat views update live.
"""

import asyncio
import logging
import time
from pathlib import Path

from fastapi import Depends, File, Form, UploadFile

from db.repositories import channel_repo, config_repo
from server.deps import require_permission, install_exception_handlers
from server.execution import (
    astart_execution, aend_execution, atrack_step, prune_executions,
    astamp_execution_channel,
)
from server.helpers import _ok, _err, parse_split_reply
from server.upload_names import unique_media_name

# Config-key prefix flagging a contact as a sandbox/test number. Operator sends
# from the official chat check this and stay local instead of hitting GOWA.
SANDBOX_CONTACT_PREFIX = "sandbox_contact."

logger = logging.getLogger(__name__)


def register_routes(app, deps):
    agent_handler = deps.agent_handler
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings
    statics_outbox_dir = deps.statics_outbox_dir

    install_exception_handlers(app)

    async def _broadcast_user_message(phone: str, content: str, *,
                                      media_type: str | None = None,
                                      media_path: str | None = None):
        """Broadcast an inbound (customer) message to all WS clients."""
        msg: dict = {"role": "user", "content": content, "ts": time.time()}
        if media_type:
            msg["media_type"] = media_type
            msg["media_path"] = media_path
        await ws_manager.broadcast("new_message", {"phone": phone, "message": msg})

    async def _sandbox_reply(phone: str) -> list[str]:
        """Run the agent on the contact's current context, then persist + broadcast
        the reply parts cleanly (no JSON-array brackets). Returns the parts."""
        # Flag this contact as a sandbox/test number so operator sends from the
        # official chat are kept local instead of failing a real GOWA send.
        try:
            await asyncio.to_thread(
                config_repo.set, f"{SANDBOX_CONTACT_PREFIX}{phone}", True,
            )
        except Exception as e:
            logger.warning("[Sandbox] could not flag %s as sandbox: %s", phone, e)

        # B5 (C-1): the sync ``process_message`` was removed; the sandbox now
        # awaits the async ``aprocess_message`` directly (it already runs on the
        # event loop). Same ProcessResult contract — reply/tool_calls/usage.
        result = await agent_handler.aprocess_message(
            phone, "",
            save_user_message=False, save_response=False,
        )

        if result.tool_calls:
            try:
                await deps.broadcast_tool_calls(phone, result.tool_calls, result.contact_info,
                                                agent_key=result.agent_key)
            except Exception as e:
                logger.error("[Sandbox] broadcast_tool_calls failed for %s: %s", phone, e)

        reply = (result.reply or "").strip()
        if not reply:
            return []

        # System notices ([WhatsBot] ...) are shown verbatim, never split.
        if reply.startswith("[WhatsBot]"):
            contact = agent_handler._get_contact(phone)
            contact.add_message("system_notice", reply)
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {"role": "system_notice", "content": reply, "ts": time.time()},
            })
            return []

        if settings.get("split_messages", True):
            parts = parse_split_reply(reply)
        else:
            parts = [reply]

        sent: list[str] = []
        for part in parts:
            if not part.strip():
                continue
            await asyncio.to_thread(
                agent_handler.save_assistant_message, phone, part, status="sent",
                agent_key=result.agent_key,
            )
            await ws_manager.broadcast("new_message", {
                "phone": phone,
                "message": {"role": "assistant", "content": part,
                            "ts": time.time(), "status": "sent"},
            })
            sent.append(part)
        return sent

    async def _after_send():
        """Bump the message counter, broadcast status and prune old executions."""
        state.msg_count += 1
        await ws_manager.broadcast("status", {
            "connected": state.connected,
            "msg_count": state.msg_count,
            "auto_reply_running": state.auto_reply_running,
            "bot_phone": state.bot_phone,
            "bot_name": state.bot_name,
        })
        try:
            await asyncio.to_thread(
                prune_executions,
                settings.get("max_executions", 200),
                settings.get("execution_retention_days", 0),
            )
        except Exception:
            pass

    def _save_upload(upload: UploadFile, content: bytes, default_name: str) -> str:
        """Persist an uploaded file under statics/outbox and return its rel path."""
        dest = statics_outbox_dir / unique_media_name(
            upload.content_type, upload.filename or default_name,
            default_ext=(Path(default_name).suffix or ".bin"))
        dest.write_bytes(content)
        return f"statics/outbox/{dest.name}"

    @app.post("/api/sandbox/send",
              dependencies=[Depends(require_permission("sandbox.use"))])
    async def sandbox_send(body: dict):
        """Process a text message through the agent pipeline (local, no GOWA)."""
        phone = (body.get("phone") or "").strip()
        message = (body.get("message") or "").strip()
        if not phone:
            return _err("Campo 'phone' é obrigatório.")
        if not message:
            return _err("Campo 'message' é obrigatório.")

        logger.info("[Sandbox] Message from %s: %s", phone, message[:80])
        exec_id = await astart_execution(phone, "sandbox")
        try:
            await atrack_step("webhook_received", {"phone": phone, "message_preview": message[:200]})
            contact = agent_handler._get_contact(phone)
            await astamp_execution_channel(
                contact, channel_repo.primary_channel_id() or "default",
                channel_label="Sandbox")
            contact.add_message("user", message)
            await _broadcast_user_message(phone, message)

            replies = await _sandbox_reply(phone)

            await atrack_step("response_sent", {
                "phone": phone, "reply_preview": "\n".join(replies)[:200],
            })
            await aend_execution(exec_id)
        except Exception as e:
            logger.error("[Sandbox] Error processing message: %s", e)
            await aend_execution(exec_id, error=str(e))
            return _err(f"Erro ao processar mensagem: {e}", status=500)

        await _after_send()
        logger.info("[Sandbox] Reply to %s: %s", phone, (replies[0] if replies else "")[:80])
        return _ok({"reply": "\n".join(replies), "replies": replies, "phone": phone})

    @app.post("/api/sandbox/send-image",
              dependencies=[Depends(require_permission("sandbox.use"))])
    async def sandbox_send_image(
        phone: str = Form(...),
        caption: str = Form(""),
        image: UploadFile = File(...),
    ):
        """Process an image message through the agent pipeline (local, no GOWA).

        The image is described by the configured `image_model` so the main
        (text-only) LLM can act on its content, mirroring the webhook media
        flow (see webhook.py `_maybe_transcribe`). Without this step the raw
        image would be inlined into the request to a text-only model.
        """
        phone = phone.strip()
        if not phone:
            return _err("Campo 'phone' é obrigatório.")
        rel_path = _save_upload(image, await image.read(), "img.png")
        abs_path = str(statics_outbox_dir / Path(rel_path).name)
        caption = caption or ""

        logger.info("[Sandbox] Image from %s", phone)
        exec_id = await astart_execution(phone, "sandbox")
        try:
            await atrack_step("webhook_received", {"phone": phone, "media": "image"})
            contact = agent_handler._get_contact(phone)
            await astamp_execution_channel(
                contact, channel_repo.primary_channel_id() or "default",
                channel_label="Sandbox")
            contact.add_message("user", caption, media_type="image", media_path=rel_path)
            await _broadcast_user_message(phone, caption, media_type="image", media_path=rel_path)

            description = ""
            if settings.get("image_transcription_enabled", True):
                try:
                    description = await asyncio.to_thread(
                        agent_handler.describe_image, abs_path, phone,
                    )
                except Exception as e:
                    logger.error("[Sandbox] Image description failed for %s: %s", phone, e)

            if description:
                desc_prefix = f"[Descrição da imagem]: {description}"
                new_content = f"{desc_prefix}\n{caption}" if caption else desc_prefix
                await asyncio.to_thread(
                    agent_handler.update_last_user_message_content, phone, new_content,
                )
                contact.add_message("transcription", description)
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "message": {"role": "transcription", "content": description,
                                "ts": time.time()},
                })

            replies = await _sandbox_reply(phone)
            await atrack_step("response_sent", {
                "phone": phone, "reply_preview": "\n".join(replies)[:200],
            })
            await aend_execution(exec_id)
        except Exception as e:
            logger.error("[Sandbox] Error processing image: %s", e)
            await aend_execution(exec_id, error=str(e))
            return _err(f"Erro ao processar imagem: {e}", status=500)

        await _after_send()
        return _ok({"replies": replies, "phone": phone})

    @app.post("/api/sandbox/send-audio",
              dependencies=[Depends(require_permission("sandbox.use"))])
    async def sandbox_send_audio(
        phone: str = Form(...),
        audio: UploadFile = File(...),
    ):
        """Process an audio message through the agent pipeline (local, no GOWA).

        The audio is transcribed so the LLM can act on its content, mirroring the
        webhook media flow (see webhook.py `_run_one_cycle`).
        """
        phone = phone.strip()
        if not phone:
            return _err("Campo 'phone' é obrigatório.")
        rel_path = _save_upload(audio, await audio.read(), "voice.ogg")
        abs_path = str(statics_outbox_dir / Path(rel_path).name)

        logger.info("[Sandbox] Audio from %s", phone)
        exec_id = await astart_execution(phone, "sandbox")
        try:
            await atrack_step("webhook_received", {"phone": phone, "media": "audio"})
            contact = agent_handler._get_contact(phone)
            await astamp_execution_channel(
                contact, channel_repo.primary_channel_id() or "default",
                channel_label="Sandbox")
            contact.add_message("user", "[Áudio recebido]", media_type="audio", media_path=rel_path)
            await _broadcast_user_message(phone, "[Áudio recebido]",
                                          media_type="audio", media_path=rel_path)

            transcription = ""
            try:
                transcription = await asyncio.to_thread(
                    agent_handler.transcribe_audio, abs_path, phone,
                )
            except Exception as e:
                logger.error("[Sandbox] Transcription failed for %s: %s", phone, e)

            if transcription:
                await asyncio.to_thread(
                    agent_handler.update_last_user_message_content, phone,
                    f"[Transcrição do áudio]: {transcription}",
                )
                contact.add_message("transcription", transcription)
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "message": {"role": "transcription", "content": transcription,
                                "ts": time.time()},
                })

            replies = await _sandbox_reply(phone)
            await atrack_step("response_sent", {
                "phone": phone, "reply_preview": "\n".join(replies)[:200],
            })
            await aend_execution(exec_id)
        except Exception as e:
            logger.error("[Sandbox] Error processing audio: %s", e)
            await aend_execution(exec_id, error=str(e))
            return _err(f"Erro ao processar áudio: {e}", status=500)

        await _after_send()
        return _ok({"replies": replies, "phone": phone})

    @app.post("/api/sandbox/send-document",
              dependencies=[Depends(require_permission("sandbox.use"))])
    async def sandbox_send_document(
        phone: str = Form(...),
        caption: str = Form(""),
        document: UploadFile = File(...),
    ):
        """Process a document message through the agent pipeline (local, no GOWA)."""
        phone = phone.strip()
        if not phone:
            return _err("Campo 'phone' é obrigatório.")
        filename = document.filename or "arquivo"
        rel_path = _save_upload(document, await document.read(), filename)

        # Content format understood by ContactDetail's document renderer.
        content = f"[Documento recebido: {filename}]"
        if caption.strip():
            content = f"{content}\n{caption.strip()}"

        logger.info("[Sandbox] Document from %s: %s", phone, filename)
        exec_id = await astart_execution(phone, "sandbox")
        try:
            await atrack_step("webhook_received", {"phone": phone, "media": "document"})
            contact = agent_handler._get_contact(phone)
            await astamp_execution_channel(
                contact, channel_repo.primary_channel_id() or "default",
                channel_label="Sandbox")
            contact.add_message("user", content, media_type="document", media_path=rel_path)
            await _broadcast_user_message(phone, content,
                                          media_type="document", media_path=rel_path)

            abs_path = str(statics_outbox_dir / Path(rel_path).name)
            transcription = ""
            if settings.get("document_transcription_enabled", True):
                try:
                    transcription = await asyncio.to_thread(
                        agent_handler.transcribe_document, abs_path, phone, filename, "",
                    )
                except Exception as e:
                    logger.error("[Sandbox] Document transcription failed for %s: %s", phone, e)

            if transcription:
                doc_prefix = f"[Conteúdo do documento]: {transcription}"
                new_content = f"{content}\n{doc_prefix}"
                await asyncio.to_thread(
                    agent_handler.update_last_user_message_content, phone, new_content,
                )
                contact.add_message("transcription", transcription)
                await ws_manager.broadcast("new_message", {
                    "phone": phone,
                    "message": {"role": "transcription", "content": transcription,
                                "ts": time.time()},
                })

            replies = await _sandbox_reply(phone)
            await atrack_step("response_sent", {
                "phone": phone, "reply_preview": "\n".join(replies)[:200],
            })
            await aend_execution(exec_id)
        except Exception as e:
            logger.error("[Sandbox] Error processing document: %s", e)
            await aend_execution(exec_id, error=str(e))
            return _err(f"Erro ao processar documento: {e}", status=500)

        await _after_send()
        return _ok({"replies": replies, "phone": phone})

    @app.post("/api/sandbox/send-video",
              dependencies=[Depends(require_permission("sandbox.use"))])
    async def sandbox_send_video(
        phone: str = Form(...),
        caption: str = Form(""),
        video: UploadFile = File(...),
    ):
        """Process a video message through the agent pipeline (local, no GOWA).

        Video is not transcribed/described; the raw clip is stored and the agent
        reacts to the caption (mirrors the received-media flow — plano 65)."""
        phone = phone.strip()
        if not phone:
            return _err("Campo 'phone' é obrigatório.")
        filename = video.filename or "video.mp4"
        rel_path = _save_upload(video, await video.read(), filename)
        caption = caption or ""

        logger.info("[Sandbox] Video from %s: %s", phone, filename)
        exec_id = await astart_execution(phone, "sandbox")
        try:
            await atrack_step("webhook_received", {"phone": phone, "media": "video"})
            contact = agent_handler._get_contact(phone)
            await astamp_execution_channel(
                contact, channel_repo.primary_channel_id() or "default",
                channel_label="Sandbox")
            contact.add_message("user", caption, media_type="video", media_path=rel_path)
            await _broadcast_user_message(phone, caption, media_type="video", media_path=rel_path)

            replies = await _sandbox_reply(phone)
            await atrack_step("response_sent", {
                "phone": phone, "reply_preview": "\n".join(replies)[:200],
            })
            await aend_execution(exec_id)
        except Exception as e:
            logger.error("[Sandbox] Error processing video: %s", e)
            await aend_execution(exec_id, error=str(e))
            return _err(f"Erro ao processar vídeo: {e}", status=500)

        await _after_send()
        return _ok({"replies": replies, "phone": phone})

    @app.post("/api/sandbox/clear",
              dependencies=[Depends(require_permission("sandbox.use"))])
    async def sandbox_clear(body: dict):
        """Clear conversation history for a sandbox phone number."""
        phone = (body.get("phone") or "").strip()
        if phone:
            agent_handler.clear_conversation(phone)
        else:
            agent_handler.clear_all_conversations()
        return _ok({"message": "Conversa limpa."})
