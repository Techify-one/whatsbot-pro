import base64
import logging
import mimetypes
import time
from pathlib import Path

from db.repositories import contact_repo, conversation_repo, message_repo, tag_repo, usage_repo, inbox_repo

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL_ID = "default"

# channel_id -> inbox_id cache. Inboxes are created at migration/boot and only
# change on a restart (same model as channels), so a process-lifetime cache is
# safe and keeps ContactMemory construction off the DB on the hot path.
_INBOX_BY_CHANNEL: dict[str, int] = {}


def resolve_inbox_id(channel_id: str) -> int:
    """Inbox id that owns ``channel_id`` (plano 11). Cached; falls back to the
    default inbox (id=1) if resolution fails so a save never blows up."""
    cid = channel_id or DEFAULT_CHANNEL_ID
    cached = _INBOX_BY_CHANNEL.get(cid)
    if cached is not None:
        return cached
    try:
        inbox = inbox_repo.get_or_create_for_channel(cid)
        inbox_id = int(inbox["id"])
    except Exception:
        logger.exception("Falha ao resolver inbox do canal %s; usando default", cid)
        inbox_id = conversation_repo.DEFAULT_INBOX_ID
    _INBOX_BY_CHANNEL[cid] = inbox_id
    return inbox_id


class TagRegistry:
    """Global tag registry backed by SQLite tags table."""

    def __init__(self):
        self._tags: dict[str, dict] = {}
        self._load()

    def _load(self):
        self._tags = tag_repo.get_all()

    def save(self):
        pass  # Each mutation already commits to DB

    def all(self) -> dict[str, dict]:
        return dict(self._tags)

    def create(self, name: str, color: str) -> bool:
        if name in self._tags:
            return False
        if tag_repo.create(name, color):
            self._tags[name] = {"color": color}
            return True
        return False

    def update(self, old_name: str, *, new_name: str | None = None, color: str | None = None) -> bool:
        if old_name not in self._tags:
            return False
        if not tag_repo.update(old_name, new_name=new_name, color=color):
            return False
        if color:
            self._tags[old_name]["color"] = color
        if new_name and new_name != old_name:
            self._tags[new_name] = self._tags.pop(old_name)
        return True

    def delete(self, name: str) -> bool:
        if name not in self._tags:
            return False
        if tag_repo.delete(name):
            del self._tags[name]
            return True
        return False

    def get(self, name: str) -> dict | None:
        return self._tags.get(name)


class ContactMemory:
    """Persistent per-contact memory backed by SQLite.

    Maintains an in-memory cache of contact metadata for fast access.
    Messages and usage are stored directly in SQLite (not cached in memory).
    """

    def __init__(self, phone: str, default_ai_enabled: bool = True, *,
                 channel_id: str = DEFAULT_CHANNEL_ID, inbox_id: int | None = None):
        self.phone = phone
        self._default_ai_enabled = default_ai_enabled
        # Channel/inbox this memory belongs to (plano 11). The contact row stays
        # unified by phone (D2); the CONVERSATION is per-channel via inbox_id.
        self.channel_id = channel_id or DEFAULT_CHANNEL_ID
        self.inbox_id = inbox_id if inbox_id is not None else resolve_inbox_id(self.channel_id)
        self.id: int | None = None
        self.info: dict = {"name": "", "email": "", "profession": "", "company": "", "address": "", "observations": []}
        self.tags: list[str] = []
        self.ai_enabled: bool = True
        self.is_group: bool = False
        self.group_name: str = ""
        self.is_archived: bool = False
        self.archived_by_app: bool = False
        self.can_send: bool = True
        self.unread_count: int = 0
        self.unread_ai_count: int = 0
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
        self._load()

    def _load(self):
        data = contact_repo.get_or_create(self.phone, default_ai_enabled=self._default_ai_enabled)
        self.id = data["id"]
        self.ai_enabled = data["ai_enabled"]
        self.is_group = data["is_group"]
        self.group_name = data["group_name"]
        self.is_archived = data["is_archived"]
        self.archived_by_app = data["archived_by_app"]
        self.can_send = data.get("can_send", True)
        self.unread_count = data["unread_count"]
        self.unread_ai_count = data["unread_ai_count"]
        self.created_at = data["created_at"]
        self.updated_at = data["updated_at"]

        # Load info fields
        observations = contact_repo.get_observations(self.id)
        self.info = {
            "name": data["name"],
            "email": data["email"],
            "profession": data["profession"],
            "company": data["company"],
            "address": data["address"],
            "observations": observations,
        }

        # Load tags
        self.tags = tag_repo.get_contact_tags(self.id)

    @property
    def messages(self) -> list[dict]:
        """Lazy-load all messages from SQLite."""
        return message_repo.get_all(self.id)

    def save(self):
        """Persist current contact metadata to SQLite."""
        self.updated_at = time.time()
        contact_repo.update(
            self.id,
            name=self.info.get("name", ""),
            email=self.info.get("email", ""),
            profession=self.info.get("profession", ""),
            company=self.info.get("company", ""),
            address=self.info.get("address", ""),
            ai_enabled=1 if self.ai_enabled else 0,
            is_group=1 if self.is_group else 0,
            group_name=self.group_name,
            is_archived=1 if self.is_archived else 0,
            can_send=1 if self.can_send else 0,
            unread_count=self.unread_count,
            unread_ai_count=self.unread_ai_count,
        )

    def _jid(self) -> str:
        """Reconstruct the WhatsApp JID, mirroring the 0013 backfill (source_id)."""
        suffix = "g.us" if self.is_group else "s.whatsapp.net"
        return f"{self.phone}@{suffix}"

    def add_message(self, role: str, content: str, *,
                    media_type: str | None = None, media_path: str | None = None,
                    status: str | None = None, msg_id: str | None = None,
                    reply_to_msg_id: str | None = None):
        # plano 01 Fase 2: resolve/stamp the atendimento thread centrally, so every
        # save site (inbound batch/media/group + outbound) links conversation_id sem
        # tocar webhook.py. Inbound user message reabre uma conversa closed.
        conversation_id = None
        conv = None
        transition = None  # "created" | "reopened" | None (plano 12 §3)
        try:
            # Uma conversa closed é reaberta tanto por mensagem do cliente ("user")
            # quanto por resposta do atendente/IA ("assistant") — qualquer um dos dois
            # lados voltando a falar reativa o atendimento (comportamento Chatwoot).
            # Roles painel-only (private_note, system, tool_call, …) NÃO reabrem.
            conv, transition = conversation_repo.resolve_for_contact_ex(
                self.id, self._jid(), reopen_if_closed=(role in ("user", "assistant")),
                inbox_id=self.inbox_id)
            conversation_id = conv["id"]
            # New thread → tell the panel so the inbox row shows its assignee
            # (e.g. "IA padrão") live, without waiting for a full refetch.
            if conv.get("created"):
                try:
                    from plugins.context import broadcast
                    broadcast("conversation_created", {
                        "conversation_id": conv["id"],
                        "contact_id": self.id,
                        "status": conv.get("status"),
                        "assignee_user_id": conv.get("assignee_user_id"),
                        "active_agent_key": conv.get("active_agent_key"),
                        "ai_active": conv.get("ai_active"),
                    })
                except Exception:
                    logger.debug("conversation_created broadcast falhou para %s", self.phone)
        except Exception:
            logger.exception("Falha ao resolver conversa para %s", self.phone)
        message_repo.add(
            self.id, role, content,
            media_type=media_type, media_path=media_path,
            status=status, msg_id=msg_id, reply_to_msg_id=reply_to_msg_id,
            conversation_id=conversation_id,
        )
        if conversation_id is not None:
            try:
                conversation_repo.touch_activity(conversation_id)
            except Exception:
                logger.exception("Falha ao atualizar last_activity da conversa %s", conversation_id)
            # plano 12 §3: aviso automático no fio quando o atendimento (re)abre por
            # uma mensagem do cliente. Painel-only; gateado por config (grupo status).
            # Emitido APÓS o save para casar com a ordem ao vivo (a msg inbound já
            # foi transmitida no recebimento, antes deste batch).
            if transition in ("created", "reopened") and conv is not None:
                self._emit_lifecycle_notice(conversation_id, transition, conv, role)
            # plano 11 D1: a nova conversa precisa materializar AO VIVO na sidebar
            # conversa-cêntrica / lista de conversas — sinal independente do gate de
            # aviso (este é uma atualização de lista, não um card no fio).
            if transition == "created" and conv is not None:
                self._broadcast_conversation_created(conversation_id, conv)
            # Reabertura closed→open (por mensagem do cliente OU resposta do
            # atendente/IA): a lista de conversas (sidebar + kanban) precisa
            # re-filtrar a conversa de volta pra "Abertas" AO VIVO. O backend já
            # reativou a row (set_status 'open'), mas sem um evento de status o painel
            # mantém o conv_status antigo e não refetcha — daí a conversa só reaparecia
            # após F5. Espelha o broadcast do path manual Resolver/Reabrir
            # (server/routes/conversations.py). Independente do gate de aviso (é
            # atualização de lista, não card no fio).
            elif transition == "reopened" and conv is not None:
                self._broadcast_conversation_status(conversation_id, conv)
                # plano 23 Fase C0: promote the automatic reopen to a distinct domain
                # event (``conversation.reopened``) on the plugin bus. The status WS
                # broadcast above is for the panel list; this is for plugin subscribers.
                # Defensive — a failed emit never breaks the inbound save.
                try:
                    from plugins.events import emit_with_filter_sync
                    emit_with_filter_sync("conversation.reopened", {
                        "conversation_id": conversation_id,
                        "contact_id": self.id,
                        "phone": self.phone,
                        "previous_status": "closed",
                        "trigger": "inbound",
                        "ts": time.time(),
                    })
                except Exception:
                    logger.debug("conversation.reopened emit falhou para %s", self.phone)
        # Touch updated_at
        contact_repo.update(self.id)

    def _broadcast_conversation_created(self, conversation_id: int, conv: dict):
        """Fire a ``conversation_created`` WS event so the conversa-cêntrica sidebar
        and the conversation list add the new per-channel thread without a reload
        (plano 11 D1). Fire-and-forget; lazy import avoids an agent→server cycle."""
        try:
            from plugins.context import broadcast
            broadcast("conversation_created", {
                "conversation_id": conversation_id,
                "display_id": conv.get("display_id"),
                "contact_id": self.id,
                "phone": self.phone,
                "inbox_id": conv.get("inbox_id"),
                "status": conv.get("status"),
            })
        except Exception:
            logger.exception("Falha ao emitir conversation_created para %s", self.phone)

    def _broadcast_conversation_status(self, conversation_id: int, conv: dict):
        """Fire a ``conversation_status_changed`` WS event when an inbound (client) or
        outbound (operator/AI) message reopens a closed conversation, so the
        conversation list (sidebar + kanban) re-files it into "Abertas" live — without
        this, the reopen only surfaced as a painel-only card (which the list ignores)
        and the row stayed hidden until a full page reload. Payload mirrors the manual
        Resolver/Reabrir path (server/routes/conversations.py ``_broadcast``).
        Fire-and-forget; lazy import avoids an agent→server import cycle."""
        try:
            from plugins.context import broadcast
            broadcast("conversation_status_changed", {
                "conversation_id": conversation_id,
                "display_id": conv.get("display_id"),
                "contact_id": self.id,
                "phone": self.phone,
                "inbox_id": conv.get("inbox_id"),
                "status": conv.get("status"),
                "assignee_user_id": conv.get("assignee_user_id"),
                "active_agent_key": conv.get("active_agent_key"),
                "ai_active": conv.get("ai_active"),
                "is_archived": conv.get("is_archived"),
            })
        except Exception:
            logger.exception("Falha ao emitir conversation_status_changed para %s", self.phone)

    def _emit_lifecycle_notice(self, conversation_id: int, transition: str, conv: dict, role: str = "user"):
        """Surface an automatic conversation-lifecycle card (plano 12 §3).

        ``reopened`` ⇒ a conversa fechada voltou a ficar ativa (cliente mandou
        mensagem se ``role == "user"``, atendente/IA respondeu caso contrário);
        ``created`` ⇒ nova conversa. O gate de config (grupo ``status``) decide se
        algo é gravado. Lazy import evita ciclo agent→server no carregamento do módulo.
        """
        try:
            from server import system_notices
            if transition == "reopened":
                event_type = ("status_reopened_auto" if role == "user"
                              else "status_reopened_auto_agent")
                system_notices.emit_conversation_notice(
                    event_type=event_type, conversation_id=conversation_id,
                    contact_id=self.id, phone=self.phone)
            elif transition == "created":
                system_notices.emit_conversation_notice(
                    event_type="created", conversation_id=conversation_id,
                    contact_id=self.id, phone=self.phone,
                    display_id=conv.get("display_id"))
        except Exception:
            logger.exception("Falha ao emitir aviso de ciclo de vida para %s", self.phone)

    def get_unread_msg_ids(self) -> list[str]:
        """Return unread message IDs from the database."""
        from sqlalchemy import select

        from db.engine import get_engine
        from db.tables import unread_msg_ids
        with get_engine().connect() as conn:
            rows = conn.execute(
                select(unread_msg_ids.c.msg_id).where(unread_msg_ids.c.contact_id == self.id)
            ).all()
        return [r.msg_id for r in rows]

    def increment_unread(self, msg_id: str | None = None):
        self.unread_count += 1
        contact_repo.increment_unread(self.id, msg_id)

    def increment_unread_ai(self):
        self.unread_ai_count += 1
        contact_repo.increment_unread_ai(self.id)

    def mark_mention(self):
        """Flag that the bot was @mentioned in this group (unread)."""
        contact_repo.set_mention(self.id)

    def mark_as_read(self) -> list[str]:
        """Reset unread count and return the list of unread msg_ids (for read receipts)."""
        msg_ids = contact_repo.mark_as_read(self.id)
        self.unread_count = 0
        self.unread_ai_count = 0
        return msg_ids

    def mark_user_messages_as_read(self) -> list[str]:
        """Reset only unread_count (user messages), preserving unread_ai_count."""
        msg_ids = contact_repo.mark_user_messages_as_read(self.id)
        self.unread_count = 0
        return msg_ids

    def mark_as_unread(self):
        """Mark this contact as unread — re-light the in-app green badge."""
        contact_repo.mark_as_unread(self.id)
        if self.unread_count < 1:
            self.unread_count = 1

    def set_ai_enabled(self, enabled: bool):
        self.ai_enabled = enabled
        contact_repo.update(self.id, ai_enabled=1 if enabled else 0)

    def set_tags(self, tags: list[str]):
        self.tags = list(tags)
        tag_repo.set_contact_tags(self.id, self.tags)

    def add_tag(self, tag_name: str):
        if tag_name not in self.tags:
            self.tags.append(tag_name)
            tag_repo.add_contact_tag(self.id, tag_name)

    def remove_tag(self, tag_name: str):
        if tag_name in self.tags:
            self.tags.remove(tag_name)
            tag_repo.remove_contact_tag(self.id, tag_name)

    def get_context_messages(self, limit: int) -> list[dict]:
        """Return the last N messages formatted for the LLM (without ts).

        For the most recent image message from the user, include a base64 data
        URI so the vision model can see it.  Older images are replaced with a
        placeholder to keep token usage reasonable.
        """
        recent = message_repo.get_context(self.id, limit)

        # Find the index of the last user image message that still needs the
        # binary inlined.  If the content already has a textual description
        # (added by the image_transcription pipeline), keep it as text — the
        # main LLM may not support image input.
        last_image_idx = -1
        for i in range(len(recent) - 1, -1, -1):
            m = recent[i]
            if m.get("media_type") == "image" and m["role"] == "user":
                if "[Descrição da imagem]:" not in (m.get("content") or ""):
                    last_image_idx = i
                break

        result: list[dict] = []
        for i, m in enumerate(recent):
            mt = m.get("media_type")
            if m["role"] == "private_note":
                content = m.get("content", "")
                result.append({"role": "user",
                               "content": f"[Nota privada do operador]: {content}"})
            elif mt == "image" and m["role"] == "user":
                if i == last_image_idx:
                    content = _build_image_content(m.get("media_path", ""), m.get("content", ""))
                else:
                    content = m.get("content") or "[Imagem enviada pelo contato]"
                result.append({"role": m["role"], "content": content})
            else:
                content = m.get("content", "")
                if m["role"] == "assistant" and m.get("status") == "operator":
                    content = f"[Mensagem do operador humano]: {content}"
                result.append({"role": m["role"], "content": content})
        return result

    def set_wa_name(self, wa_name: str) -> None:
        """Set contact name from WhatsApp pushName if no manual name exists."""
        current = self.info.get("name", "")
        if current and not current.startswith("~"):
            return
        new_name = f"~{wa_name}"
        if current != new_name:
            self.info["name"] = new_name
            contact_repo.update(self.id, name=new_name)

    def update_info(self, **kwargs):
        """Update contact info fields. Only overwrites non-empty values."""
        fields_to_update = {}
        for key in ("name", "email", "profession", "company", "address"):
            val = kwargs.get(key, "")
            if val:
                self.info[key] = val
                fields_to_update[key] = val
        if fields_to_update:
            contact_repo.update(self.id, **fields_to_update)
        observation = kwargs.get("observation", "")
        if observation and observation not in self.info.get("observations", []):
            self.info.setdefault("observations", []).append(observation)
            contact_repo.add_observation(self.id, observation)

    def set_info_fields(self, fields: dict) -> None:
        """Replace scalar contact fields from an explicit human edit (panel).

        Unlike ``update_info`` (LLM auto-fill, which only overwrites non-empty
        values), this writes every provided key UNCONDITIONALLY — an empty
        string is treated as an intentional clear. Only known scalar columns
        are accepted; keys absent from ``fields`` are left untouched.
        """
        allowed = ("name", "email", "profession", "company", "address")
        # These columns are NOT NULL (server_default ""), so coerce a JSON null
        # to the empty-string clear — matching the custom_attributes None path
        # and avoiding an IntegrityError from a non-panel caller sending null.
        to_write = {k: ("" if fields[k] is None else fields[k])
                    for k in allowed if k in fields}
        if not to_write:
            return
        for key, val in to_write.items():
            self.info[key] = val
        contact_repo.update(self.id, **to_write)

    def add_usage(self, call_type: str, model: str,
                  prompt_tokens: int, completion_tokens: int,
                  total_tokens: int, cost_usd: float) -> None:
        usage_repo.add(self.id, call_type, model, prompt_tokens,
                       completion_tokens, total_tokens, cost_usd)

    def get_usage_summary(self, start_ts: float | None = None,
                          end_ts: float | None = None) -> dict:
        """Return aggregated usage stats for this contact."""
        return usage_repo.summary(self.id, start_ts, end_ts)

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
        # Custom attributes (plano 05): tell the AI which attributes it may fill
        # (via set_custom_attribute) and the values already set. Both scopes:
        # contact-level and the currently-open conversation (plano 54).
        parts.extend(self._custom_attr_lines("contact"))
        parts.extend(self._custom_attr_lines("conversation"))
        return "\n".join(parts)

    def _custom_attr_lines(self, applies_to: str) -> list[str]:
        """Prompt lines for custom attributes the AI may fill in the given scope.

        Lists each defined attribute with type hints + the current value so the
        AI can fill it via set_custom_attribute. Conversation-scoped attributes
        are read from the contact's currently-open conversation; if there is no
        open conversation, the section is omitted.
        """
        try:
            from db.repositories import custom_attribute_repo as _ca_repo
            defs = _ca_repo.list_definitions(applies_to)
            if not defs or not self.id:
                return []
            if applies_to == "conversation":
                from db.tables import conversations as _entity_tbl
                conv = conversation_repo.get_open_for_contact(self.id)
                if not conv:
                    return []
                entity_id = conv["id"]
                label = (
                    "Atributos personalizados DESTA CONVERSA que você pode preencher "
                    "(use set_custom_attribute com scope='conversation'):"
                )
            else:
                from db.tables import contacts as _entity_tbl
                entity_id = self.id
                label = (
                    "Atributos personalizados do contato que você pode preencher "
                    "(use set_custom_attribute):"
                )
            values = _ca_repo.get_values(_entity_tbl, entity_id)
            lines = []
            for d in defs:
                key = d["attribute_key"]
                hint = ""
                if d.get("type") == "list" and d.get("options"):
                    hint = f" (opções: {', '.join(map(str, d['options']))})"
                elif d.get("type") == "checkbox":
                    hint = " (true/false)"
                cur = values.get(key)
                cur_str = f" = {cur}" if cur not in (None, "") else ""
                lines.append(f"- {key}{hint}{cur_str}")
            if not lines:
                return []
            return [label, *lines]
        except Exception:
            return []

    def get_tags_summary(self) -> str:
        """Return comma-separated list of tag names for prompt injection."""
        return ", ".join(self.tags) if self.tags else ""


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
