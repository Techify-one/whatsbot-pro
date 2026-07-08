import dataclasses
import logging
import time

from openai import OpenAI, AsyncOpenAI

from agent.memory import ContactMemory, TagRegistry
from agent.tools import CORE_TOOLS
from agent.tool_registry import ToolRegistry
from agent import llm, prompt_builder
from channels import ai_settings
from db.repositories import message_repo
from plugins.context import ToolContext

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ProcessResult:
    """Result of an agent turn with optional tool call metadata.

    ``aborted=True`` marks a DELIBERATE empty reply (filter abort / agent
    resolution error already sinalizado) — callers must not treat it as the
    engine having run and produced nothing (plano 31 F4).
    """
    reply: str
    tool_calls: list[dict] = dataclasses.field(default_factory=list)
    contact_info: dict | None = None
    aborted: bool = False


class AgentHandler:
    """Processes incoming WhatsApp messages using OpenRouter LLM."""

    def __init__(
        self,
        api_key: str,
        max_context_messages: int = 10,
        inactivity_timeout_min: int = 30,
        audio_model: str = "google/gemini-2.5-flash",
        image_model: str = "google/gemini-2.5-flash",
        document_model: str = "google/gemini-2.5-flash",
        improvement_model: str = "",
        improvement_prompt: str = "",
        pricing_fn=None,
        default_ai_enabled: bool = True,
    ):
        self.api_key = api_key
        self.max_context_messages = max_context_messages
        self.inactivity_timeout = inactivity_timeout_min * 60
        # Media-transcription models (direct, non-agentic LLM calls). The
        # agent's prompt/model/tools are resolved per-request from the DB via
        # agent.agent_factory — there is no in-code prompt/model anymore.
        self.audio_model = audio_model
        self.image_model = image_model
        self.document_model = document_model
        # Model used for the one-shot "improvement analysis" of a flagged AI
        # reply. Empty → fall back to ``self.model`` (the chat model).
        self.improvement_model = improvement_model
        # System prompt of that same analysis. Empty → the code default in
        # app.services.improvement_service.DEFAULT_IMPROVEMENT_PROMPT.
        self.improvement_prompt = improvement_prompt
        self.default_ai_enabled = default_ai_enabled
        # Keyed by (channel_id, phone) — plano 11 D3.
        self._contacts: dict[tuple[str, str], ContactMemory] = {}
        self._client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None
        self.pricing_fn = pricing_fn
        self.split_messages: bool = True
        self.tag_registry = TagRegistry()

        # Tool registry — extracted to ``agent.tool_registry.ToolRegistry``
        # (Plano 23 · Fase B5). The handler holds one instance and delegates the
        # public surface (registration / override resolution / dispatch). Core
        # tools are registered at construction; plugins call
        # ``register_plugin_tools`` after the loader runs.
        self._tools = ToolRegistry()
        for schema, executor in CORE_TOOLS:
            self._tools.register_tool(schema, executor)

    # ── tool-registry delegation (Fase B5) ──────────────────────────────────
    # Thin facade over ``ToolRegistry`` so existing callers (agno_engine,
    # ai_tool_installer, ai_builtin_tools, routes, tests) keep their method/attr
    # names. State lives on ``self._tools``; these expose it read-through.
    @property
    def _tool_originals(self) -> dict[str, dict]:
        return self._tools._tool_originals

    @property
    def _tool_default_labels(self) -> dict[str, str]:
        return self._tools._tool_default_labels

    @property
    def _tool_schemas(self) -> list[dict]:
        return self._tools._tool_schemas

    @property
    def _disabled_tools(self) -> set[str]:
        return self._tools._disabled_tools

    @property
    def _tool_executors(self) -> dict[str, tuple[callable, str | None]]:
        return self._tools._tool_executors

    @property
    def _prompt_fragments(self) -> list[tuple[callable, str | None]]:
        return self._tools._prompt_fragments

    def _register_tool(self, schema, executor, plugin_id=None) -> None:
        self._tools.register_tool(schema, executor, plugin_id=plugin_id)

    def register_plugin_tools(self, plugin_id, tools) -> None:
        """Register tools from a plugin. Called by the plugin loader."""
        self._tools.register_plugin_tools(plugin_id, tools)

    def register_ai_tools(self, tools) -> int:
        """Register code-in-DB tools (``ai_tools``). Returns the count registered."""
        return self._tools.register_ai_tools(tools)

    def override_tool(self, schema, executor, plugin_id=None) -> None:
        """Replace an already-registered tool's schema + executor in place."""
        self._tools.override_tool(schema, executor, plugin_id=plugin_id)

    def unregister_tool(self, name: str) -> None:
        """Remove a tool from the registry (e.g. a disabled built-in tool)."""
        self._tools.unregister_tool(name)

    def register_plugin_prompts(self, plugin_id, fragments) -> None:
        """Register prompt fragments from a plugin. Called by the plugin loader."""
        self._tools.register_plugin_prompts(plugin_id, fragments)

    def known_tool_names(self) -> set[str]:
        """Names of every tool currently registered (core + plugin)."""
        return self._tools.known_tool_names()

    def is_tool_active(self, name: str) -> bool:
        """Registrada e não desabilitada por override (entra no schema do LLM)."""
        return self._tools.is_tool_active(name)

    def refresh_tool_overrides(self) -> None:
        """Re-read ``tool_overrides`` and rebuild the effective tool schemas."""
        self._tools.refresh_tool_overrides()

    def list_tools(self) -> list[dict]:
        """Return metadata for every registered tool, with override state merged."""
        return self._tools.list_tools()

    def _make_tool_ctx(
        self,
        contact: ContactMemory,
        plugin_id: str | None = None,
    ) -> ToolContext:
        return self._tools.make_tool_ctx(
            contact, self, self.tag_registry, plugin_id=plugin_id)

    def _dispatch_tool(
        self,
        contact: ContactMemory,
        name: str,
        args: dict,
    ) -> str | None:
        """Run a tool by name and return an optional follow-up feedback string."""
        return self._tools.dispatch(contact, self, self.tag_registry, name, args)

    # ── LLM clients / usage / media delegation (Fase B5) ─────────────────────
    # Cohesive LLM concerns live in ``agent.llm``; the handler keeps thin
    # delegates (same names) so webhook/sandbox/messaging_service/tests are
    # unchanged. State (``_client`` / ``_async_client`` / ``pricing_fn`` /
    # config) stays on the handler; ``agent.llm`` reads it through ``self``.
    def _record_usage(self, phone: str, call_type: str, model: str, response) -> None:
        """Extract usage from an OpenAI-compatible response and record it."""
        llm.record_usage(self, phone, call_type, model, response)

    def _record_usage_tokens(self, phone: str, call_type: str, model: str,
                             prompt_tokens: int, completion_tokens: int,
                             total_tokens: int) -> None:
        """Record usage from explicit token counts (AGNO metrics path)."""
        llm.record_usage_tokens(self, phone, call_type, model,
                                prompt_tokens, completion_tokens, total_tokens)

    def _get_client(self) -> OpenAI:
        return llm.get_client(self)

    def _get_async_client(self) -> AsyncOpenAI:
        return llm.get_async_client(self)

    def update_config(
        self,
        api_key: str | None = None,
        max_context_messages: int | None = None,
        inactivity_timeout_min: int | None = None,
        audio_model: str | None = None,
        image_model: str | None = None,
        document_model: str | None = None,
        improvement_model: str | None = None,
        improvement_prompt: str | None = None,
        split_messages: bool | None = None,
        default_ai_enabled: bool | None = None,
    ):
        if api_key is not None:
            self.api_key = api_key
            self._client = None
            self._async_client = None
        if max_context_messages is not None:
            self.max_context_messages = max_context_messages
        if inactivity_timeout_min is not None:
            self.inactivity_timeout = inactivity_timeout_min * 60
        if audio_model is not None:
            self.audio_model = audio_model
        if image_model is not None:
            self.image_model = image_model
        if document_model is not None:
            self.document_model = document_model
        if improvement_model is not None:
            self.improvement_model = improvement_model
        if improvement_prompt is not None:
            self.improvement_prompt = improvement_prompt
        if split_messages is not None:
            self.split_messages = split_messages
        if default_ai_enabled is not None:
            self.default_ai_enabled = default_ai_enabled

    def transcribe_audio(self, audio_path: str, phone: str = "") -> str:
        """Transcribe an audio file using the configured audio model."""
        return llm.transcribe_audio(self, audio_path, phone)

    def describe_image(self, image_path: str, phone: str = "") -> str:
        """Describe an image using the configured image model."""
        return llm.describe_image(self, image_path, phone)

    def transcribe_document(
        self,
        document_path: str,
        phone: str = "",
        file_name: str = "",
        mimetype: str = "",
    ) -> str:
        """Read/transcribe a document (PDF, DOCX, plain text) into text."""
        return llm.transcribe_document(self, document_path, phone, file_name, mimetype)

    def _get_contact(self, phone: str, *, channel_id: str = "default") -> ContactMemory:
        # Cache key is (channel_id, phone) — plano 11 D3. The contact row stays
        # unified by phone (same contact_id across channels), but each channel's
        # ContactMemory carries its own inbox so the CONVERSATION is per-channel.
        key = (channel_id, phone)
        if key not in self._contacts:
            # Per-channel default (plano 21): a new contact's AI follows the
            # channel's own "IA padrão p/ novos contatos", falling back to the
            # global default when the channel hasn't overridden it.
            default_ai = bool(ai_settings.value(
                channel_id, "default_ai_enabled", self.default_ai_enabled))
            self._contacts[key] = ContactMemory(
                phone, default_ai_enabled=default_ai, channel_id=channel_id)
        return self._contacts[key]

    def iter_cached_contacts(self, phone: str) -> list[ContactMemory]:
        """Every cached ContactMemory for ``phone`` across channels (plano 11).

        Panel actions (mark-read, archive, …) update the DB and use this to keep
        each channel-variant's in-memory cache coherent without resurrecting one.
        """
        return [cm for (_ch, ph), cm in self._contacts.items() if ph == phone]

    def drop_cached_contact(self, phone: str) -> None:
        """Evict all cached ContactMemory variants for ``phone`` (e.g. on delete)."""
        for key in [k for k in self._contacts if k[1] == phone]:
            self._contacts.pop(key, None)

    def _select_active_tools(self, agent_spec) -> list[dict]:
        """Return the effective tool schemas, restricted to the agent's selection."""
        return self._tools.select_active_tools(agent_spec)

    @staticmethod
    def _encode_history_for_split(context_messages: list[dict]) -> list[dict]:
        """Re-encode assistant turns as JSON arrays for the split_messages format."""
        return llm.encode_history_for_split(context_messages)

    def _build_system_prompt(self, contact: ContactMemory,
                             base_prompt: str | None = None,
                             split_messages: bool | None = None) -> str:
        """Build system prompt with contact info and current date/time injected.

        Delegates to ``agent.prompt_builder`` (Fase B5). ``base_prompt`` is the
        DB-resolved agent prompt; the dynamic sections (group context, contact
        info, tags, date, plugin fragments, split-messages format) layer on top.
        """
        return prompt_builder.build_system_prompt(
            self, contact, base_prompt=base_prompt, split_messages=split_messages)

    async def aprocess_message(self, sender: str, text: str, *,
                               save_user_message: bool = True,
                               save_response: bool = True,
                               image_path: str | None = None,
                               audio_path: str | None = None,
                               disable_tools: bool = False,
                               channel_id: str = "default") -> ProcessResult:
        """Run one agent turn (async, cancellable) and return the ProcessResult.

        Delegates to ``app.services.agent_run_service.run_turn`` (Fase B5); the
        handler stays the facade owning config / the tool registry / clients /
        ``ContactMemory``. Imported lazily to avoid an import cycle (the service
        imports ``ProcessResult`` from here), mirroring B3/B4.
        """
        from app.services import agent_run_service
        return await agent_run_service.run_turn(
            self, sender, text,
            save_user_message=save_user_message,
            save_response=save_response,
            image_path=image_path,
            audio_path=audio_path,
            disable_tools=disable_tools,
            channel_id=channel_id,
        )

    def test_api_key(self, api_key: str) -> tuple[bool, str]:
        """Test if an API key is valid."""
        return llm.test_api_key(api_key)

    def _emit_resolution_error(self, contact, sender: str, exc: Exception) -> None:
        """Isolate a broken agent resolution to this one conversation.

        Logs the failure and writes a painel-only ``error`` card to the affected
        conversation (broadcast live), WITHOUT sending anything to the client. The
        rest of the service keeps running — only this message is dropped.
        """
        logger.error("Resolução de agente falhou para %s: %s", sender, exc)
        try:
            from db.repositories import conversation_repo
            conv = (conversation_repo.get_open_for_contact_scoped(contact)
                    if getattr(contact, "id", None) else None)
            conversation_id = conv["id"] if conv else None
            content = ("[WhatsBot] Não foi possível resolver o agente de IA desta "
                       "conversa. Verifique a configuração de Agentes.")
            msg = message_repo.add(
                contact.id, "error", content, conversation_id=conversation_id)
            from plugins.context import broadcast
            broadcast("new_message", {
                "phone": sender,
                "channel_id": getattr(contact, "channel_id", "default"),
                "message": msg,
            })
        except Exception:
            logger.exception(
                "Falha ao gravar card de erro de resolução para %s", sender)

    def generate_improvement(self, phone: str, target_message: dict,
                             feedback: str, *,
                             conversation_id: int | None = None) -> str:
        """One-shot quality analysis of an AI reply flagged as incorrect.

        Delegates to ``app.services.improvement_service`` (Fase B5). DIRECT,
        non-agentic LLM call via the ISOLATED SYNC client (``_get_client``).
        ``conversation_id`` (plano 31 F3) escopa a análise à conversa da
        resposta marcada (canal + histórico); ``None`` = comportamento legado."""
        from app.services import improvement_service
        return improvement_service.generate_improvement(
            self, phone, target_message, feedback,
            conversation_id=conversation_id)

    def _ensure_conversation_agent(self, contact, agent_spec) -> None:
        """Attribute the active conversation to the AI agent that is answering so
        the inbox shows its assignee chip (e.g. "IA padrão") sempre que a IA
        responde. Best-effort: skips chats a human took over and broadcasts a
        single assignment event when the binding actually changed."""
        try:
            from db.repositories import conversation_repo, agent_repo
            agent_key = agent_spec.agent_key if agent_spec else agent_repo.DEFAULT_AGENT_KEY
            conv = conversation_repo.ensure_ai_agent(
                contact.id, agent_key, getattr(contact, "inbox_id", None))
            if not conv:
                return
            try:
                from plugins.context import broadcast
                broadcast("conversation_assigned", {
                    "conversation_id": conv["id"],
                    "contact_id": contact.id,
                    "status": conv.get("status"),
                    "assignee_user_id": conv.get("assignee_user_id"),
                    "active_agent_key": conv.get("active_agent_key"),
                    "ai_active": conv.get("ai_active"),
                })
            except Exception:
                logger.debug("conversation_assigned broadcast falhou para %s", contact.phone)
        except Exception:
            logger.exception("Falha ao atribuir agente à conversa de %s",
                             getattr(contact, "phone", "?"))

    def save_assistant_message(self, phone: str, text: str, *,
                               msg_id: str | None = None,
                               status: str = "sent",
                               channel_id: str = "default") -> dict:
        """Save an assistant (bot) message to contact memory after successful send."""
        contact = self._get_contact(phone, channel_id=channel_id)
        contact.add_message("assistant", text, msg_id=msg_id, status=status)
        return message_repo.get_last(contact.id) or {"role": "assistant", "content": text, "ts": time.time()}

    def save_operator_message(self, phone: str, text: str, *,
                              status: str | None = None,
                              msg_id: str | None = None,
                              reply_to_msg_id: str | None = None,
                              sent_by_user_id: int | None = None,
                              sent_by_name: str | None = None,
                              channel_id: str = "default",
                              reopen: bool | None = None) -> dict:
        """Save a manually sent message (from the operator) without LLM processing.

        ``channel_id`` decides which inbox owns the conversation the message lands
        in (plano 11) — so an operator-initiated message routed through a specific
        channel is saved in that channel's conversation, not always 'default'.

        ``sent_by_user_id``/``sent_by_name`` gravam QUEM (operador logado) enviou —
        o nome (snapshot) é exibido no painel no lugar de "Manual". None quando não
        há usuário logado (instalação legada/aberta) → cai em "Manual"."""
        contact = self._get_contact(phone, channel_id=channel_id)
        contact.add_message("assistant", text, status=status, msg_id=msg_id,
                            reply_to_msg_id=reply_to_msg_id,
                            sent_by_user_id=sent_by_user_id, sent_by_name=sent_by_name,
                            reopen=reopen)
        return message_repo.get_last(contact.id) or {"role": "assistant", "content": text, "ts": time.time()}

    def mark_message_sent(self, phone: str, content: str,
                          msg_id: str | None = None) -> dict | None:
        """Find the most recent failed message with matching content and mark as sent."""
        contact = self._get_contact(phone)
        message_repo.update_status(contact.id, content, "sent", msg_id=msg_id)
        return {"content": content}

    def update_last_user_message_content(self, phone: str, new_content: str,
                                         channel_id: str = "default") -> None:
        """Update the content of the last user message (e.g., with transcription).

        Plano 37 (B5): escopa à conversa do CANAL do turno (o ``ContactMemory`` é
        construído com ``channel_id`` → carrega ``inbox_id``), evitando a corrida
        cross-canal em que a transcrição sobrescreveria a última msg de outro canal.
        Fail-open: sem conversa aberta naquele inbox, cai no contact-global."""
        contact = self._get_contact(phone, channel_id=channel_id)
        from db.repositories import conversation_repo
        conv = conversation_repo.get_open_for_contact_scoped(contact)
        msg = message_repo.get_last_user_message(
            contact.id, conv["id"] if conv else None)
        if msg and msg.get("_id"):
            message_repo.update_content(msg["_id"], new_content)

    def clear_conversation(self, sender: str):
        contact = self._get_contact(sender)
        message_repo.delete_all(contact.id)

    def clear_all_conversations(self):
        for contact in self._contacts.values():
            message_repo.delete_all(contact.id)
        self._contacts.clear()
