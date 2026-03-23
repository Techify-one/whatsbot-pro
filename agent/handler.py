import json
import logging
import time
from pathlib import Path

from openai import OpenAI

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

    def add_message(self, role: str, content: str):
        self.messages.append({
            "role": role,
            "content": content,
            "ts": time.time(),
        })
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
        """Return the last N messages formatted for the LLM (without ts)."""
        recent = self.messages[-limit:] if len(self.messages) > limit else self.messages
        return [{"role": m["role"], "content": m["content"]} for m in recent]

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

    def process_message(self, sender: str, text: str, save_user_message: bool = True) -> str:
        """Process an incoming message and return the AI response."""
        if not self.api_key:
            return "[WhatsBot] API key não configurada."

        contact = self._get_contact(sender)
        if save_user_message:
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
                tools=[SAVE_CONTACT_TOOL],
                tool_choice="auto",
                max_tokens=1024,
            )

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
