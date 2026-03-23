import base64
import json
import logging
import mimetypes
import time
from pathlib import Path

from typing import TYPE_CHECKING

from openai import OpenAI

if TYPE_CHECKING:
    from usage.tracker import UsageTracker

logger = logging.getLogger(__name__)


SAVE_CONTACT_TOOL = {
    "type": "function",
    "function": {
        "name": "save_contact_info",
        "description": (
            "Salva informações pessoais do contato quando ele mencionar dados como "
            "nome, email, profissão, empresa, ou qualquer observação importante. "
            "Chame esta função SEMPRE que o usuário revelar dados pessoais na conversa."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nome completo do contato",
                },
                "email": {
                    "type": "string",
                    "description": "Email do contato",
                },
                "profession": {
                    "type": "string",
                    "description": "Profissão ou cargo do contato",
                },
                "company": {
                    "type": "string",
                    "description": "Empresa onde trabalha",
                },
                "observation": {
                    "type": "string",
                    "description": "Qualquer outra informação relevante sobre o contato",
                },
            },
            "required": [],
        },
    },
}


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
        self.info: dict = {"name": "", "email": "", "profession": "", "company": "", "observations": []}
        self.messages: list[dict] = []
        self.ai_enabled: bool = True
        self.unread_count: int = 0
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
                default_info = {"name": "", "email": "", "profession": "", "company": "", "observations": []}
                self.info = data.get("info", default_info)
                if old_notes and not any(self.info.values()):
                    self.info["observations"] = [old_notes]
                self.messages = data.get("messages", [])
                self.ai_enabled = data.get("ai_enabled", True)
                self.unread_count = data.get("unread_count", 0)
                self.created_at = data.get("created_at", time.time())
                self.updated_at = data.get("updated_at", time.time())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load memory for %s: %s", self.phone, e)

    def save(self):
        self.updated_at = time.time()
        data = {
            "phone": self.phone,
            "info": self.info,
            "messages": self.messages,
            "ai_enabled": self.ai_enabled,
            "unread_count": self.unread_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error("Failed to save memory for %s: %s", self.phone, e)

    def add_message(self, role: str, content: str, *,
                    media_type: str | None = None, media_path: str | None = None):
        entry: dict = {"role": role, "content": content, "ts": time.time()}
        if media_type:
            entry["media_type"] = media_type
        if media_path:
            entry["media_path"] = media_path
        self.messages.append(entry)
        self.save()

    def increment_unread(self):
        self.unread_count += 1
        self.save()

    def mark_as_read(self):
        if self.unread_count > 0:
            self.unread_count = 0
            self.save()

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
        # Filter out transcription-only messages before slicing
        eligible = [m for m in self.messages if m.get("role") != "transcription"]
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
        for key in ("name", "email", "profession", "company"):
            val = kwargs.get(key, "")
            if val:
                self.info[key] = val
        observation = kwargs.get("observation", "")
        if observation and observation not in self.info.get("observations", []):
            self.info.setdefault("observations", []).append(observation)
        self.save()

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
        usage_tracker: "UsageTracker | None" = None,
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
        self.usage_tracker = usage_tracker
        self.pricing_fn = pricing_fn

    def _record_usage(self, phone: str, call_type: str, model: str, response) -> None:
        """Extract usage from an OpenAI-compatible response and record it."""
        if not self.usage_tracker:
            return
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
            self.usage_tracker.record(
                phone=phone,
                call_type=call_type,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
            )
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

    def _get_contact(self, phone: str) -> ContactMemory:
        if phone not in self._contacts:
            self._contacts[phone] = ContactMemory(phone, self.memory_dir)
        return self._contacts[phone]

    def _build_system_prompt(self, contact: ContactMemory) -> str:
        """Build system prompt with contact info injected."""
        prompt = self.system_prompt
        info_summary = contact.get_info_summary()
        if info_summary:
            prompt += (
                f"\n\n--- Informações sobre este contato ({contact.phone}) ---\n"
                f"{info_summary}\n"
                "--- Fim das informações ---"
            )
        return prompt

    def process_message(self, sender: str, text: str, *,
                        save_user_message: bool = True,
                        image_path: str | None = None,
                        audio_path: str | None = None) -> str:
        """Process an incoming message and return the AI response.

        If *image_path* is provided the image is sent to a vision-capable model.
        If *audio_path* is provided the text should already contain a placeholder
        like ``[Áudio recebido]`` — the LLM will see that label.
        """
        if not self.api_key:
            return "[WhatsBot] API key não configurada."

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
                tools=[SAVE_CONTACT_TOOL],
                tool_choice="auto",
                max_tokens=1024,
            )

            self._record_usage(sender, "text", self.model, response)
            msg = response.choices[0].message

            # Handle tool calls (save contact info)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.function.name == "save_contact_info":
                        try:
                            args = json.loads(tc.function.arguments)
                            contact.update_info(**args)
                            logger.info("Saved contact info for %s: %s", sender, args)
                        except (json.JSONDecodeError, Exception) as e:
                            logger.warning("Failed to parse tool call for %s: %s", sender, e)

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

            contact.add_message("assistant", reply)
            logger.info("Processed message from %s", sender)
            return reply

        except Exception as e:
            logger.error("LLM error for %s: %s", sender, e)
            error_msg = str(e)
            if "401" in error_msg or "unauthorized" in error_msg.lower():
                return "[WhatsBot] API key inválida. Verifique sua chave OpenRouter."
            if "429" in error_msg or "rate" in error_msg.lower():
                return "[WhatsBot] Limite de requisições atingido. Tente novamente em instantes."
            return "[WhatsBot] Erro ao processar mensagem. Tente novamente."

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

    def save_operator_message(self, phone: str, text: str) -> dict:
        """Save a manually sent message (from the operator) without LLM processing."""
        contact = self._get_contact(phone)
        contact.add_message("assistant", text)
        return contact.messages[-1]

    def clear_conversation(self, sender: str):
        contact = self._get_contact(sender)
        contact.messages.clear()
        contact.save()

    def clear_all_conversations(self):
        for contact in self._contacts.values():
            contact.messages.clear()
            contact.save()
        self._contacts.clear()
