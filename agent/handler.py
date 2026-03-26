import base64
import dataclasses
import json
import logging
import mimetypes
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from openai import OpenAI

from agent.tools import ALL_TOOLS

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ProcessResult:
    """Result of process_message with optional tool call metadata."""
    reply: str
    tool_calls: list[dict] = dataclasses.field(default_factory=list)
    contact_info: dict | None = None


class ContactMemory:
    """Persistent per-contact memory stored as a JSON file.

    File structure:
    {
        "phone": "5511999999999",
        "info": {"name": "", "email": "", "profession": "", "company": "", "observations": []},
        "messages": [{"role": "user"|"assistant", "content": "...", "ts": 1234567890}, ...],
        "created_at": 1234567890,
        "updated_at": 1234567890
    }
    """

    def __init__(self, phone: str, memory_dir: Path):
        self.phone = phone
        self.file_path = memory_dir / f"{phone}.json"
        self.id: int | None = None
        self.info: dict = {"name": "", "email": "", "profession": "", "company": "", "address": "", "observations": []}
        self.messages: list[dict] = []
        self.usage: list[dict] = []
        self.ai_enabled: bool = True
        self.unread_count: int = 0
        self.unread_ai_count: int = 0
        self.unread_msg_ids: list[str] = []
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self._load()

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Migrate old "notes" format to structured "info"
                old_notes = data.get("notes", "")
                default_info = {"name": "", "email": "", "profession": "", "company": "", "address": "", "observations": []}
                self.info = data.get("info", default_info)
                if old_notes and not any(self.info.values()):
                    self.info["observations"] = [old_notes]
                self.messages = data.get("messages", [])
                self.usage = data.get("usage", [])
                self.ai_enabled = data.get("ai_enabled", True)
                self.id = data.get("id")
                self.unread_count = data.get("unread_count", 0)
                self.unread_ai_count = data.get("unread_ai_count", 0)
                self.unread_msg_ids = data.get("unread_msg_ids", [])
                self.created_at = data.get("created_at", time.time())
                self.updated_at = data.get("updated_at", time.time())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load memory for %s: %s", self.phone, e)

    def save(self):
        self.updated_at = time.time()
        data = {
            "id": self.id,
            "phone": self.phone,
            "info": self.info,
            "messages": self.messages,
            "usage": self.usage,
            "ai_enabled": self.ai_enabled,
            "unread_count": self.unread_count,
            "unread_ai_count": self.unread_ai_count,
            "unread_msg_ids": self.unread_msg_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error("Failed to save memory for %s: %s", self.phone, e)

    def add_message(self, role: str, content: str, *,
                    media_type: str | None = None, media_path: str | None = None,
                    status: str | None = None, msg_id: str | None = None):
        entry: dict = {"role": role, "content": content, "ts": time.time()}
        if media_type:
            entry["media_type"] = media_type
        if media_path:
            entry["media_path"] = media_path
        if status:
            entry["status"] = status
        if msg_id:
            entry["msg_id"] = msg_id
        self.messages.append(entry)
        self.save()

    def increment_unread(self, msg_id: str | None = None):
        self.unread_count += 1
        if msg_id:
            self.unread_msg_ids.append(msg_id)
        self.save()

    def increment_unread_ai(self):
        self.unread_ai_count += 1
        self.save()

    def mark_as_read(self) -> list[str]:
        """Reset unread count and return the list of unread msg_ids (for read receipts)."""
        msg_ids = list(self.unread_msg_ids)
        if self.unread_count > 0 or msg_ids or self.unread_ai_count > 0:
            self.unread_count = 0
            self.unread_ai_count = 0
            self.unread_msg_ids.clear()
            self.save()
        return msg_ids

    def set_ai_enabled(self, enabled: bool):
        self.ai_enabled = enabled
        self.save()

    def get_context_messages(self, limit: int) -> list[dict]:
        """Return the last N messages formatted for the LLM (without ts).

        For the most recent image message from the user, include a base64 data
        URI so the vision model can see it.  Older images are replaced with a
        placeholder to keep token usage reasonable.
        Transcription messages (role="transcription") are excluded from LLM context.
        """
        # Filter out transcription-only and failed messages before slicing
        eligible = [m for m in self.messages
                    if m.get("role") not in ("transcription", "tool_call") and m.get("status") != "failed"]
        recent = eligible[-limit:] if len(eligible) > limit else eligible

        # Find the index of the last user image message (within *recent*)
        last_image_idx = -1
        for i in range(len(recent) - 1, -1, -1):
            if recent[i].get("media_type") == "image" and recent[i]["role"] == "user":
                last_image_idx = i
                break

        result: list[dict] = []
        for i, m in enumerate(recent):
            mt = m.get("media_type")
            if mt == "image" and m["role"] == "user":
                if i == last_image_idx:
                    # Build vision content array with base64
                    content = _build_image_content(m.get("media_path", ""), m.get("content", ""))
                else:
                    content = m.get("content") or "[Imagem enviada pelo contato]"
                result.append({"role": m["role"], "content": content})
            else:
                result.append({"role": m["role"], "content": m.get("content", "")})
        return result

    def update_info(self, **kwargs):
        """Update contact info fields. Only overwrites non-empty values."""
        for key in ("name", "email", "profession", "company", "address"):
            val = kwargs.get(key, "")
            if val:
                self.info[key] = val
        observation = kwargs.get("observation", "")
        if observation and observation not in self.info.get("observations", []):
            self.info.setdefault("observations", []).append(observation)
        self.save()

    def add_usage(self, call_type: str, model: str,
                  prompt_tokens: int, completion_tokens: int,
                  total_tokens: int, cost_usd: float) -> None:
        self.usage.append({
            "call_type": call_type,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "ts": time.time(),
        })
        self.save()

    def get_usage_summary(self, start_ts: float | None = None,
                          end_ts: float | None = None) -> dict:
        """Return aggregated usage stats for this contact."""
        filtered = self.usage
        if start_ts is not None:
            filtered = [u for u in filtered if u.get("ts", 0) >= start_ts]
        if end_ts is not None:
            filtered = [u for u in filtered if u.get("ts", 0) <= end_ts]

        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                  "cost_usd": 0.0, "call_count": 0, "by_type": {}}
        for u in filtered:
            totals["prompt_tokens"] += u.get("prompt_tokens", 0)
            totals["completion_tokens"] += u.get("completion_tokens", 0)
            totals["total_tokens"] += u.get("total_tokens", 0)
            totals["cost_usd"] += u.get("cost_usd", 0.0)
            totals["call_count"] += 1
            ct = u.get("call_type", "text")
            bt = totals["by_type"].setdefault(ct, {
                "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "call_count": 0,
            })
            bt["cost_usd"] += u.get("cost_usd", 0.0)
            bt["prompt_tokens"] += u.get("prompt_tokens", 0)
            bt["completion_tokens"] += u.get("completion_tokens", 0)
            bt["total_tokens"] += u.get("total_tokens", 0)
            bt["call_count"] += 1
        return totals

    def get_info_summary(self) -> str:
        """Format contact info for injection into system prompt."""
        parts = []
        if self.info.get("name"):
            parts.append(f"Nome: {self.info['name']}")
        if self.info.get("email"):
            parts.append(f"Email: {self.info['email']}")
        if self.info.get("profession"):
            parts.append(f"Profissão: {self.info['profession']}")
        if self.info.get("company"):
            parts.append(f"Empresa: {self.info['company']}")
        if self.info.get("address"):
            parts.append(f"Endereço: {self.info['address']}")
        for obs in self.info.get("observations", []):
            parts.append(f"Obs: {obs}")
        return "\n".join(parts)


def _build_image_content(media_path: str, caption: str = "") -> list[dict] | str:
    """Build an OpenAI vision content array from a local image file.

    Returns a plain placeholder string if the file cannot be read.
    """
    try:
        p = Path(media_path)
        if not p.is_absolute():
            # Resolve relative to project root
            p = Path(__file__).resolve().parent.parent / p
        if not p.exists():
            return caption or "[Imagem enviada pelo contato]"
        data = p.read_bytes()
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        b64 = base64.b64encode(data).decode()
        parts: list[dict] = [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
        if caption:
            parts.append({"type": "text", "text": caption})
        else:
            parts.append({"type": "text", "text": "O contato enviou esta imagem."})
        return parts
    except Exception:
        return caption or "[Imagem enviada pelo contato]"


class AgentHandler:
    """Processes incoming WhatsApp messages using OpenRouter LLM."""

    def __init__(
        self,
        api_key: str,
        system_prompt: str,
        max_context_messages: int = 10,
        inactivity_timeout_min: int = 30,
        model: str = "openai/gpt-4o-mini",
        audio_model: str = "google/gemini-2.0-flash-001",
        image_model: str = "google/gemini-2.0-flash-001",
        memory_dir: Path | None = None,
        pricing_fn=None,
    ):
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.max_context_messages = max_context_messages
        self.inactivity_timeout = inactivity_timeout_min * 60
        self.model = model
        self.audio_model = audio_model
        self.image_model = image_model
        self.memory_dir = memory_dir or Path.home() / ".config" / "WhatsBot" / "contacts"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._contacts: dict[str, ContactMemory] = {}
        self._client: OpenAI | None = None
        self.pricing_fn = pricing_fn
        self.split_messages: bool = True
        self._id_lock = threading.Lock()

    def _record_usage(self, phone: str, call_type: str, model: str, response) -> None:
        """Extract usage from an OpenAI-compatible response and record it."""
        try:
            usage = getattr(response, "usage", None)
            if not usage:
                return
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            total_tokens = getattr(usage, "total_tokens", 0) or 0
            cost_usd = 0.0
            if self.pricing_fn:
                prompt_price, completion_price = self.pricing_fn(model)
                cost_usd = (prompt_tokens * prompt_price) + (completion_tokens * completion_price)
            contact = self._get_contact(phone)
            contact.add_usage(call_type, model, prompt_tokens, completion_tokens, total_tokens, cost_usd)
            logger.debug("Usage recorded for %s: %s %s tokens=%d cost=%.6f",
                         phone, call_type, model, total_tokens, cost_usd)
        except Exception as e:
            logger.warning("Failed to record usage: %s", e)

    def _get_client(self) -> OpenAI:
        if self._client is None or self._client.api_key != self.api_key:
            self._client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.api_key,
            )
        return self._client

    def update_config(
        self,
        api_key: str | None = None,
        system_prompt: str | None = None,
        max_context_messages: int | None = None,
        inactivity_timeout_min: int | None = None,
        model: str | None = None,
        audio_model: str | None = None,
        image_model: str | None = None,
        split_messages: bool | None = None,
    ):
        if api_key is not None:
            self.api_key = api_key
            self._client = None
        if system_prompt is not None:
            self.system_prompt = system_prompt
        if max_context_messages is not None:
            self.max_context_messages = max_context_messages
        if inactivity_timeout_min is not None:
            self.inactivity_timeout = inactivity_timeout_min * 60
        if model is not None:
            self.model = model
        if audio_model is not None:
            self.audio_model = audio_model
        if image_model is not None:
            self.image_model = image_model
        if split_messages is not None:
            self.split_messages = split_messages

    def transcribe_audio(self, audio_path: str, phone: str = "") -> str:
        """Transcribe an audio file using the configured audio model."""
        if not self.api_key:
            return ""
        try:
            p = Path(audio_path)
            if not p.is_absolute():
                p = Path(__file__).resolve().parent.parent / p
            if not p.exists():
                logger.warning("Audio file not found for transcription: %s", audio_path)
                return ""
            data = p.read_bytes()
            b64 = base64.b64encode(data).decode()
            # Determine format from extension
            ext = p.suffix.lower().lstrip(".")
            if ext in ("oga", "ogg", "opus"):
                fmt = "ogg"
            elif ext == "mp3":
                fmt = "mp3"
            elif ext == "wav":
                fmt = "wav"
            else:
                fmt = "ogg"

            client = self._get_client()
            response = client.chat.completions.create(
                model=self.audio_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": b64, "format": fmt},
                        },
                        {
                            "type": "text",
                            "text": "Transcreva este áudio fielmente em português. Retorne apenas a transcrição, sem comentários adicionais.",
                        },
                    ],
                }],
                max_tokens=2048,
            )
            self._record_usage(phone, "audio", self.audio_model, response)
            result = response.choices[0].message.content.strip()
            logger.info("Audio transcribed (%d chars): %s", len(result), result[:80])
            return result
        except Exception as e:
            logger.error("Audio transcription failed: %s", e)
            return ""

    def describe_image(self, image_path: str, phone: str = "") -> str:
        """Describe an image using the configured image model."""
        if not self.api_key:
            return ""
        try:
            p = Path(image_path)
            if not p.is_absolute():
                p = Path(__file__).resolve().parent.parent / p
            if not p.exists():
                logger.warning("Image file not found for description: %s", image_path)
                return ""
            data = p.read_bytes()
            mime = mimetypes.guess_type(str(p))[0] or "image/png"
            b64 = base64.b64encode(data).decode()

            client = self._get_client()
            response = client.chat.completions.create(
                model=self.image_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                        {
                            "type": "text",
                            "text": "Descreva detalhadamente o conteúdo desta imagem em português.",
                        },
                    ],
                }],
                max_tokens=1024,
            )
            self._record_usage(phone, "image", self.image_model, response)
            result = response.choices[0].message.content.strip()
            logger.info("Image described (%d chars): %s", len(result), result[:80])
            return result
        except Exception as e:
            logger.error("Image description failed: %s", e)
            return ""

    def _next_contact_id(self) -> int:
        """Return the next sequential contact ID (thread-safe)."""
        with self._id_lock:
            counter_file = self.memory_dir / "_counter.json"
            next_id = 1
            if counter_file.exists():
                try:
                    data = json.loads(counter_file.read_text(encoding="utf-8"))
                    next_id = data.get("next_id", 1)
                except (json.JSONDecodeError, OSError):
                    pass
            counter_file.write_text(
                json.dumps({"next_id": next_id + 1}), encoding="utf-8"
            )
            return next_id

    def ensure_contact_ids(self) -> None:
        """Assign sequential IDs to existing contacts that lack one (migration)."""
        counter_file = self.memory_dir / "_counter.json"
        next_id = 1
        if counter_file.exists():
            try:
                data = json.loads(counter_file.read_text(encoding="utf-8"))
                next_id = data.get("next_id", 1)
            except (json.JSONDecodeError, OSError):
                pass

        needs_id: list[tuple[float, Path, dict]] = []
        max_existing = 0
        for f in self.memory_dir.glob("*.json"):
            if f.stem.startswith("_"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                cid = data.get("id")
                if cid is not None:
                    max_existing = max(max_existing, cid)
                else:
                    needs_id.append((data.get("created_at", 0), f, data))
            except (json.JSONDecodeError, OSError):
                continue

        next_id = max(next_id, max_existing + 1)
        needs_id.sort(key=lambda x: x[0])

        for _, f, data in needs_id:
            data["id"] = next_id
            f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            next_id += 1

        counter_file.write_text(
            json.dumps({"next_id": next_id}), encoding="utf-8"
        )
        logger.info("Contact IDs ensured: %d migrated, next_id=%d", len(needs_id), next_id)

    def _get_contact(self, phone: str) -> ContactMemory:
        if phone not in self._contacts:
            self._contacts[phone] = ContactMemory(phone, self.memory_dir)
        contact = self._contacts[phone]
        if contact.id is None:
            contact.id = self._next_contact_id()
            contact.save()
        return contact

    def _build_system_prompt(self, contact: ContactMemory) -> str:
        """Build system prompt with contact info and current date/time injected."""
        prompt = self.system_prompt
        info_summary = contact.get_info_summary()
        if info_summary:
            prompt += (
                f"\n\n--- Informações já conhecidas sobre este contato ({contact.phone}) ---\n"
                f"{info_summary}\n"
                "IMPORTANTE: Use estas informações na conversa. "
                "NÃO pergunte dados que já estão listados acima (ex: nome, email, etc).\n"
                "--- Fim das informações ---"
            )
        _BRT = timezone(timedelta(hours=-3))
        now = datetime.now(_BRT)
        dias = ["segunda-feira", "terça-feira", "quarta-feira",
                "quinta-feira", "sexta-feira", "sábado", "domingo"]
        prompt += (
            f"\n\n--- Data e hora atual ---\n"
            f"Data: {now.strftime('%d/%m/%Y')} ({dias[now.weekday()]})\n"
            f"Hora: {now.strftime('%H:%M')}\n"
            "--- Fim ---"
        )
        if self.split_messages:
            prompt += (
                "\n\n--- Formato de resposta ---\n"
                "IMPORTANTE: Você DEVE responder SEMPRE em formato JSON array de strings.\n"
                "Cada string é uma mensagem separada que será enviada no WhatsApp.\n"
                "Regras de divisão:\n"
                "- Saudação separada do conteúdo (ex: \"Oi! Tudo bem?\" como primeira msg)\n"
                "- Cada ideia ou tópico em mensagem separada\n"
                "- Mensagens curtas: 1 a 3 linhas cada, no máximo\n"
                "- Total: geralmente 2 a 5 partes\n"
                "- Estilo informal brasileiro de WhatsApp\n"
                "- NÃO use markdown nem formatação especial\n"
                "Exemplo:\n"
                '[\"Oi! Tudo bem? 😊\", \"Então, sobre o que você perguntou...\", '
                '\"A resposta é X porque Y\", \"Qualquer dúvida me fala!\"]\n'
                "Retorne APENAS o JSON array, sem texto antes ou depois.\n"
                "--- Fim do formato ---"
            )
        return prompt

    def process_message(self, sender: str, text: str, *,
                        save_user_message: bool = True,
                        save_response: bool = True,
                        image_path: str | None = None,
                        audio_path: str | None = None) -> ProcessResult:
        """Process an incoming message and return the AI response.

        If *image_path* is provided the image is sent to a vision-capable model.
        If *audio_path* is provided the text should already contain a placeholder
        like ``[Áudio recebido]`` — the LLM will see that label.
        """
        if not self.api_key:
            return ProcessResult(reply="[WhatsBot] API key não configurada.")

        contact = self._get_contact(sender)

        # Determine media metadata for storage
        media_type: str | None = None
        media_path: str | None = None
        if image_path:
            media_type = "image"
            media_path = image_path
        elif audio_path:
            media_type = "audio"
            media_path = audio_path

        if save_user_message:
            contact.add_message("user", text or "", media_type=media_type, media_path=media_path)

        context_messages = contact.get_context_messages(self.max_context_messages)

        messages = [
            {"role": "system", "content": self._build_system_prompt(contact)},
            *context_messages,
        ]

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=ALL_TOOLS,
                tool_choice="auto",
                max_tokens=1024,
            )

            self._record_usage(sender, "text", self.model, response)
            msg = response.choices[0].message

            # Handle tool calls generically
            executed_tools: list[dict] = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError as e:
                        logger.warning("Failed to parse tool args for %s: %s", sender, e)
                        args = {}

                    # Dispatch tool execution
                    if tool_name == "save_contact_info":
                        try:
                            contact.update_info(**args)
                        except Exception as e:
                            logger.warning("Failed to execute %s for %s: %s", tool_name, sender, e)

                    executed_tools.append({"tool": tool_name, "args": args})
                    logger.info("Tool call for %s: %s(%s)", sender, tool_name, args)

                # If model only called tools without text, do a follow-up call
                if not msg.content:
                    messages.append(msg.model_dump())
                    for tc in msg.tool_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": "Informações salvas com sucesso.",
                        })
                    follow_up = client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=1024,
                    )
                    self._record_usage(sender, "text", self.model, follow_up)
                    reply = follow_up.choices[0].message.content.strip()
                else:
                    reply = msg.content.strip()
            else:
                reply = msg.content.strip()

            if save_response:
                contact.add_message("assistant", reply)
            logger.info("Processed message from %s", sender)

            # Snapshot contact info if any tool modified it
            updated_info = None
            if any(tc.get("tool") == "save_contact_info" for tc in executed_tools):
                updated_info = dict(contact.info)
                # Deep copy observations list
                updated_info["observations"] = list(updated_info.get("observations", []))

            return ProcessResult(reply=reply, tool_calls=executed_tools, contact_info=updated_info)

        except Exception as e:
            logger.error("LLM error for %s: %s", sender, e)
            error_msg = str(e)
            if "401" in error_msg or "unauthorized" in error_msg.lower():
                return ProcessResult(reply="[WhatsBot] API key inválida. Verifique sua chave OpenRouter.")
            if "429" in error_msg or "rate" in error_msg.lower():
                return ProcessResult(reply="[WhatsBot] Limite de requisições atingido. Tente novamente em instantes.")
            return ProcessResult(reply="[WhatsBot] Erro ao processar mensagem. Tente novamente.")

    def test_api_key(self, api_key: str) -> tuple[bool, str]:
        """Test if an API key is valid."""
        try:
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5,
            )
            return True, "API key válida!"
        except Exception as e:
            return False, f"Erro: {e}"

    def save_assistant_message(self, phone: str, text: str) -> dict:
        """Save an assistant (bot) message to contact memory after successful send."""
        contact = self._get_contact(phone)
        contact.add_message("assistant", text)
        return contact.messages[-1]

    def save_operator_message(self, phone: str, text: str,
                              status: str | None = None) -> dict:
        """Save a manually sent message (from the operator) without LLM processing."""
        contact = self._get_contact(phone)
        contact.add_message("assistant", text, status=status)
        return contact.messages[-1]

    def mark_message_sent(self, phone: str, content: str) -> dict | None:
        """Find the most recent failed message with matching content and mark as sent."""
        contact = self._get_contact(phone)
        for msg in reversed(contact.messages):
            if msg.get("status") == "failed" and msg.get("content") == content:
                msg.pop("status", None)
                contact.save()
                return msg
        return None

    def clear_conversation(self, sender: str):
        contact = self._get_contact(sender)
        contact.messages.clear()
        contact.save()

    def clear_all_conversations(self):
        for contact in self._contacts.values():
            contact.messages.clear()
            contact.save()
        self._contacts.clear()
