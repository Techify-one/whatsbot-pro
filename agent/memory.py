import base64
import logging
import mimetypes
import time
import uuid
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
    """Global tag registry backed by the tags table."""

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
    """Persistent per-contact memory backed by the database.

    Maintains an in-memory cache of contact metadata for fast access.
    Messages and usage are stored directly in the database (not cached in memory).
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
        """Lazy-load all messages from the database."""
        return message_repo.get_all(self.id)

    def save(self):
        """Persist current contact metadata to the database."""
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

    def _resolve_conversation(self, role: str, *,
                              reopen: bool | None = None) -> tuple[dict | None, int | None, str | None]:
        """Resolve (idempotent get-or-create + reopen-if-closed) the atendimento
        thread for a save. PURE — no side effects, so the caller decides WHEN the
        lifecycle reactions run (``add_message`` keeps them AFTER the INSERT,
        byte-identical to before; the ingest runs them at t=0).

        ``resolve_for_contact_ex`` is idempotent: the 2nd resolve of the same thread
        returns ``transition=None``. That is the mechanism that makes the
        created/reopened announcements fire EXACTLY ONCE across the two-phase ingest
        (materialize, t=0) + batch (save, t≈batch_delay) pipeline (plano 25 Fase 2).

        Uma conversa closed é reaberta tanto por mensagem do cliente ("user") quanto
        por resposta do atendente/IA ("assistant"); roles painel-only NÃO reabrem.
        Returns ``(conv, conversation_id, transition)``; on a resolution error returns
        ``(None, None, None)`` (fail-soft — the save still happens, just unlinked)."""
        try:
            # plano 28: stamp provenance on a brand-new thread — a customer message
            # ('user') opens an 'inbound' conversation (shows on the sidebar at t=0
            # via the origin gate); anything else (AI/operator/panel-only) is 'outbound'.
            origin = "inbound" if role == "user" else "outbound"
            # reopen=None → regra padrão (user/assistant reabrem; painel-only não). Um caller
            # pode forçar reopen=False p/ NÃO reabrir uma conversa fechada (ex.: a mensagem
            # de avaliação enviada no FECHAR do protocolo não deve reabrir o atendimento).
            reopen_closed = (role in ("user", "assistant")) if reopen is None else bool(reopen)
            # regra "ignorar abertura": um caller que força reopen=False p/ NÃO reabrir também
            # NÃO deve ABRIR um atendimento num contato NOVO — cria a conversa já FECHADA (a
            # mensagem fica salva/visível, sem atendimento aberto nem protocolo). reopen=None
            # (regra padrão) e reopen=True seguem criando aberta. Uma conversa já existente cai
            # no ramo de reabrir/manter do repo, então create_closed não a afeta.
            create_closed = (reopen is False)
            # plano 38 F1: seed a brand-new conversation's ai_active from the
            # PER-CHANNEL "IA padrão p/ novos contatos" toggle (self._default_ai_enabled,
            # already resolved via ai_settings in handler._get_contact) instead of only
            # the global default. Only applies on CREATE — a reopen never re-seeds.
            seed = 1 if self._default_ai_enabled else 0
            conv, transition = conversation_repo.resolve_for_contact_ex(
                self.id, self._jid(), reopen_if_closed=reopen_closed,
                inbox_id=self.inbox_id, origin=origin, create_closed=create_closed,
                ai_active_seed=seed)
            return conv, conv["id"], transition
        except Exception:
            logger.exception("Falha ao resolver conversa para %s", self.phone)
            return None, None, None

    def _run_lifecycle_reactions(self, conversation_id: int, transition: str | None,
                                 conv: dict | None, role: str) -> None:
        """Drive the after-resolve lifecycle reactions for one resolve: the automatic
        created/reopened notice card, the ``conversation_created`` /
        ``conversation_status_changed`` list broadcasts (sidebar/kanban re-file live)
        and the ``conversation.reopened`` bus verb (plano 23 Fase C5 — they live in
        :mod:`agent.message_listeners`). Runs SYNCHRONOUSLY in the caller's thread (a
        sync ``add_message`` must still broadcast — the reopen-assertion suite checks
        this right after the call). Exactly-once: only the resolve that actually
        transitioned carries a non-None ``transition``; a reused thread no-ops inside
        :func:`on_message_persisted`. Defensive — a failed reaction never breaks the
        caller."""
        try:
            from agent.message_listeners import on_message_persisted
            on_message_persisted(
                conversation_id, self.id, self.phone,
                transition=transition, conv=conv, role=role)
        except Exception:
            logger.exception("Falha nas reações de message.persisted para %s", self.phone)

    def _broadcast_conversation_upsert(self, conversation_id: int, role: str) -> None:
        """Emit the ``conversation_upsert`` list-row broadcast (plano 28). Delegates
        to :func:`agent.message_listeners.broadcast_conversation_upsert`, which gates
        panel-only roles and is fully defensive (never breaks the save)."""
        try:
            from agent.message_listeners import broadcast_conversation_upsert
            broadcast_conversation_upsert(conversation_id, role)
        except Exception:
            logger.exception("Falha ao emitir conversation_upsert para %s", self.phone)

    @staticmethod
    def _notify_private_enabled() -> bool:
        """A conta optou por notificar mensagens privadas? (config global, default off)."""
        try:
            from db.repositories import config_repo
            return bool(config_repo.get("notify_private_messages", False))
        except Exception:
            return False

    def _notify_private_unread(self, conversation_id: int, msg_id: str) -> None:
        """Marca a nota privada como não-lida (badge verde + aba) reaproveitando o
        encanamento de msg_id: ``increment_unread`` bump o ``contacts.unread_count`` E
        insere a linha ``unread_msg_ids`` (que a subquery por-conversa conta). Depois
        força o ``conversation_upsert`` para a sidebar refletir ao vivo. Todos os
        caminhos de leitura (mark_as_read / mark_conversation_read / …) já limpam isso
        sem mudança. Defensivo — nunca quebra o save."""
        try:
            from db.repositories import unread_repo
            from agent.message_listeners import emit_conversation_upsert_row
            unread_repo.increment_unread(self.id, msg_id)
            emit_conversation_upsert_row(conversation_id)
        except Exception:
            logger.exception("Falha ao notificar nota privada para %s", self.phone)

    def add_message(self, role: str, content: str, *,
                    media_type: str | None = None, media_path: str | None = None,
                    status: str | None = None, msg_id: str | None = None,
                    reply_to_msg_id: str | None = None,
                    sent_by_user_id: int | None = None,
                    sent_by_name: str | None = None,
                    reopen: bool | None = None) -> dict:
        # plano 01 Fase 2: resolve/stamp the atendimento thread centrally, so every
        # save site (inbound batch/media/group + outbound) links conversation_id sem
        # tocar webhook.py. plano 25 Fase 2: the resolve is now a shared helper (also
        # used by the ingest's ensure_conversation_live); the lifecycle reactions stay
        # AFTER the INSERT — byte-identical ordering for any save not preceded by an
        # ingest materialization (user message row first, then the created notice card).
        conv, conversation_id, transition = self._resolve_conversation(role, reopen=reopen)
        # Notificação de nota privada (opt-in ``notify_private_messages``): dá à nota um
        # ``msg_id`` sintético para ela PARTICIPAR do encanamento de não-lida baseado em
        # msg_id — o mesmo que uma mensagem de cliente usa. Assim ela acende o badge verde
        # POR-CONVERSA (subquery ``unread_msg_ids ⋈ messages.msg_id`` por conversa) E a
        # contagem da aba (``contacts.unread_count``), e é limpa por TODOS os caminhos de
        # leitura existentes sem código novo. Som fica de fora (nunca toca hoje). Off (ou
        # msg_id já presente) → comportamento legado byte-a-byte (msg_id=None).
        notify_private = False
        if role == "private_note" and msg_id is None and self._notify_private_enabled():
            msg_id = "pn:" + uuid.uuid4().hex
            notify_private = True
        saved = message_repo.add(
            self.id, role, content,
            media_type=media_type, media_path=media_path,
            status=status, msg_id=msg_id, reply_to_msg_id=reply_to_msg_id,
            conversation_id=conversation_id,
            sent_by_user_id=sent_by_user_id, sent_by_name=sent_by_name,
        )
        if conversation_id is not None:
            try:
                conversation_repo.touch_activity(conversation_id)
            except Exception:
                logger.exception("Falha ao atualizar last_activity da conversa %s", conversation_id)
            # plano 23 Fase C5 (§2.4): the hot path EMITS the PUBLIC ``message.persisted``
            # signal (for plugins/audit) and then drives the lifecycle listener directly
            # (kept here AFTER the INSERT to preserve the old ordering). Driving it
            # directly (not via the bus) keeps it exactly-once and synchronous.
            self._emit_message_persisted(conversation_id, role, msg_id, saved)
            self._run_lifecycle_reactions(conversation_id, transition, conv, role)
            if notify_private:
                # Bump o unread baseado em msg_id (contato + subquery por-conversa) e
                # FORÇA o upsert do row (a nota é preview-excluded, então o broadcast
                # normal a barraria — mas o unread precisa chegar na sidebar).
                self._notify_private_unread(conversation_id, msg_id)
            else:
                # plano 28: Event-Carried State Transfer — after the INSERT (preview/unread
                # now real), push the authoritative list row so the sidebar upserts it by
                # conversation_id without a stale refetch. Panel-only roles are gated out
                # inside the helper. Never breaks the save.
                self._broadcast_conversation_upsert(conversation_id, role)
        # Touch updated_at
        contact_repo.update(self.id)
        # Return the inserted row (id/ts/conversation_id/…) so callers that need
        # the freshly-saved message — e.g. broadcasting a private_note with its
        # conversation_id — use it directly instead of re-reading via
        # ``message_repo.get_last`` (which returns the contact's LAST row and, in a
        # burst of concurrent saves, can be a DIFFERENT message → wrong _id/ts).
        return saved

    def ensure_conversation_live(self, role: str = "user",
                                 reopen: bool | None = None) -> int | None:
        """Materialize the atendimento thread at INGEST time (t=0) WITHOUT saving a
        message (plano 25 Fase 2, bug #2 — "aba notifica antes da lista").

        ``reopen=False`` materializa/exibe a conversa mas mantém uma FECHADA fechada (não
        reabre no t=0) — usado pela regra "ignorar abertura" (direção received): a mensagem
        aparece, mas a conversa continua resolvida.

        The receive pipeline is two-phase: the inbound message is only saved by the
        batch (t≈``message_batch_delay``), which is also where ``add_message`` would
        create/announce a brand-new conversation. Until then the conversa-cêntrica
        sidebar has no row to show, so a NEW conversation appears ~3-4 s after the tab
        badge. This resolves/creates the conversation NOW and fires the
        created/reopened lifecycle reactions, so the panel's conversation list
        materializes the row immediately (the ingest's ``new_message`` also carries the
        returned ``conversation_id`` so the row updates in place).

        Idempotent / exactly-once: the later batch ``add_message`` re-resolves the SAME
        thread → ``transition=None`` → no re-announce. Deliberately does NOT save a
        message and does NOT emit ``message.persisted`` (those belong to the real save
        in the batch). Returns the conversation_id (``None`` if resolution failed).
        Best-effort — never blocks ingest."""
        conv, conversation_id, transition = self._resolve_conversation(role, reopen=reopen)
        if conversation_id is not None:
            try:
                conversation_repo.touch_activity(conversation_id)
            except Exception:
                logger.exception("Falha ao atualizar last_activity da conversa %s", conversation_id)
            self._run_lifecycle_reactions(conversation_id, transition, conv, role)
            # plano 28 Fase 4: aparição em t=0. Emit the fully-formed list row NOW
            # (name/channel/status; origin='inbound', last_message_ts=0) so the sidebar
            # shows the brand-new conversation immediately — the gate keys on
            # origin=='inbound', not on a message existing yet. The batch's later
            # add_message re-emits with the real preview (guard-merged on the client).
            self._broadcast_conversation_upsert(conversation_id, role)
        return conversation_id

    def _emit_message_persisted(self, conversation_id: int, role: str,
                                msg_id: str | None, saved: dict | None) -> None:
        """Emit the PUBLIC ``message.persisted`` bus event AFTER the INSERT (plano 23
        Fase C5). Payload is the clean decoupling contract
        ``{conversation_id, contact_id, role, msg_id, ts}``.

        Uses the fire-and-forget :func:`plugins.events.emit` (NOT ``emit_with_filter``):
        ``message.persisted`` is a low-level persistence SIGNAL on the universal hot
        path — it dispatches to plugin ``EVENT_HANDLERS`` but is NOT interceptable via
        ``filter.event.before_emit`` (a plugin must not be able to veto a row that is
        already committed). This also keeps it OUT of the ``filter.event.before_emit``
        capture the characterization recorder uses, so existing event goldens are
        untouched (additive, zero golden churn). Best-effort — a failed schedule (or
        no bus loop wired, e.g. the legacy sync test) never breaks the save."""
        try:
            from plugins.events import emit as _emit
            _emit("message.persisted", {
                "conversation_id": conversation_id,
                "contact_id": self.id,
                "role": role,
                "msg_id": msg_id,
                "ts": (saved or {}).get("ts") or time.time(),
            })
        except Exception:
            logger.debug("message.persisted emit falhou para %s", self.phone)

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

        # 12.2 (plano 31 F5): colapsa respostas ASSISTANT idênticas ADJACENTES —
        # um loop degenerado ("mesma resposta 3x seguidas") não se realimenta
        # pelo contexto. Vale SÓ para a montagem do contexto do LLM; o histórico
        # persistido e o painel não mudam.
        deduped: list[dict] = []
        for m in result:
            if (deduped
                    and m.get("role") == "assistant"
                    and deduped[-1].get("role") == "assistant"
                    and isinstance(m.get("content"), str)
                    and m.get("content")
                    and m["content"] == deduped[-1].get("content")):
                continue
            deduped.append(m)
        return deduped

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
        for obs in self.info.get("observations", []):
            parts.append(f"Obs: {obs}")
        # Email/Profissão/Empresa/Endereço are now custom attributes (seeded as
        # default contact attributes) — listed by `_custom_attr_lines` below, so
        # they're NOT repeated here.
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
                # plano 37: atributos DESTA conversa = a do canal deste ContactMemory
                # (self.inbox_id), não a mais recente de qualquer canal do contato.
                conv = conversation_repo.get_open_for_contact_inbox(self.id, self.inbox_id)
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
