import json
import logging
import time
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)


class ContactMemory:
    """Persistent per-contact memory stored as a JSON file.

    File structure:
    {
        "phone": "5511999999999",
        "notes": "Informações pessoais do contato...",
        "messages": [{"role": "user"|"assistant", "content": "...", "ts": 1234567890}, ...],
        "created_at": 1234567890,
        "updated_at": 1234567890
    }
    """

    def __init__(self, phone: str, memory_dir: Path):
        self.phone = phone
        self.file_path = memory_dir / f"{phone}.json"
        self.notes: str = ""
        self.messages: list[dict] = []
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self._load()

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.notes = data.get("notes", "")
                self.messages = data.get("messages", [])
                self.created_at = data.get("created_at", time.time())
                self.updated_at = data.get("updated_at", time.time())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load memory for %s: %s", self.phone, e)

    def save(self):
        self.updated_at = time.time()
        data = {
            "phone": self.phone,
            "notes": self.notes,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error("Failed to save memory for %s: %s", self.phone, e)

    def add_message(self, role: str, content: str):
        self.messages.append({
            "role": role,
            "content": content,
            "ts": time.time(),
        })
        self.save()

    def get_context_messages(self, limit: int) -> list[dict]:
        """Return the last N messages formatted for the LLM (without ts)."""
        recent = self.messages[-limit:] if len(self.messages) > limit else self.messages
        return [{"role": m["role"], "content": m["content"]} for m in recent]


class AgentHandler:
    """Processes incoming WhatsApp messages using OpenRouter LLM."""

    def __init__(
        self,
        api_key: str,
        system_prompt: str,
        max_context_messages: int = 10,
        inactivity_timeout_min: int = 30,
        model: str = "openai/gpt-4o-mini",
        memory_dir: Path | None = None,
    ):
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.max_context_messages = max_context_messages
        self.inactivity_timeout = inactivity_timeout_min * 60
        self.model = model
        self.memory_dir = memory_dir or Path.home() / ".config" / "WhatsBot" / "contacts"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._contacts: dict[str, ContactMemory] = {}
        self._client: OpenAI | None = None

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

    def _get_contact(self, phone: str) -> ContactMemory:
        if phone not in self._contacts:
            self._contacts[phone] = ContactMemory(phone, self.memory_dir)
        return self._contacts[phone]

    def _build_system_prompt(self, contact: ContactMemory) -> str:
        """Build system prompt with contact notes injected."""
        prompt = self.system_prompt
        if contact.notes:
            prompt += (
                f"\n\n--- Informações sobre este contato ({contact.phone}) ---\n"
                f"{contact.notes}\n"
                "--- Fim das informações ---"
            )
        return prompt

    def process_message(self, sender: str, text: str) -> str:
        """Process an incoming message and return the AI response."""
        if not self.api_key:
            return "[WhatsBot] API key não configurada."

        contact = self._get_contact(sender)
        contact.add_message("user", text)

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
                max_tokens=1024,
            )
            reply = response.choices[0].message.content.strip()
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

    def clear_conversation(self, sender: str):
        contact = self._get_contact(sender)
        contact.messages.clear()
        contact.save()

    def clear_all_conversations(self):
        for contact in self._contacts.values():
            contact.messages.clear()
            contact.save()
        self._contacts.clear()
