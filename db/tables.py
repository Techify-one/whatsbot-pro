"""SQLAlchemy Core table definitions for WhatsBot.

These ``Table`` objects are the single source of truth for the database
schema. They are NOT mapped ORM classes — there is no ``DeclarativeBase``, no
``Session``, no identity map. Repositories import the tables and build
``select()/insert()/update()`` against them directly, preserving the
explicit-SQL feel of the original ``sqlite3`` code while letting SQLAlchemy
handle dialect differences. The Alembic baseline revision is generated from
this metadata.

Timestamps are stored as Unix epoch floats for backwards compatibility with
existing data (column type ``Float``). A future migration could move them to
``TIMESTAMP``; that is intentionally out of scope.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


def _json_type():
    """Dialect-agnostic JSON column type (plano 05).

    JSONB on PostgreSQL (enables GIN, ``@>``, ``has_key`` for filtering),
    generic JSON (TEXT + json1) on SQLite. This is the first NATIVE JSON column
    in the project — earlier JSON (``messages.reactions``) is hand-serialized Text.

    Mutation-tracking rule (Core, no Session): JSON/JSONB do NOT track in-place
    dict mutation. ALWAYS reassign the whole dict in an UPDATE
    (``values(custom_attributes=new_dict)``), never ``obj["k"] = v``.
    """
    return JSON().with_variant(JSONB(), "postgresql")


config = Table(
    "config",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)


contacts = Table(
    "contacts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("phone", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False, server_default=""),
    Column("email", Text, nullable=False, server_default=""),
    Column("profession", Text, nullable=False, server_default=""),
    Column("company", Text, nullable=False, server_default=""),
    Column("address", Text, nullable=False, server_default=""),
    Column("ai_enabled", Integer, nullable=False, server_default="1"),
    Column("is_group", Integer, nullable=False, server_default="0"),
    Column("group_name", Text, nullable=False, server_default=""),
    Column("is_archived", Integer, nullable=False, server_default="0"),
    Column("archived_by_app", Integer, nullable=False, server_default="0"),
    Column("is_pinned", Integer, nullable=False, server_default="0"),
    Column("can_send", Integer, nullable=False, server_default="1"),
    Column("unread_count", Integer, nullable=False, server_default="0"),
    Column("unread_ai_count", Integer, nullable=False, server_default="0"),
    Column("has_unread_mention", Integer, nullable=False, server_default="0"),
    # Custom attributes (plano 05) — native JSON/JSONB, owned by this plan for
    # the contact scope. conversations.custom_attributes is owned by plano 01.
    Column("custom_attributes", _json_type(), nullable=False, server_default="{}"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
Index("idx_contacts_updated", contacts.c.updated_at)
Index("idx_contacts_archived", contacts.c.is_archived)


observations = Table(
    "observations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("idx_obs_contact", observations.c.contact_id)


messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False, server_default=""),
    Column("ts", Float, nullable=False),
    Column("media_type", Text),
    Column("media_path", Text),
    Column("status", Text),
    Column("msg_id", Text),
    Column("revoked", Integer, nullable=False, server_default="0"),
    Column("reactions", Text),  # JSON: {emoji: [reactor, ...]}
    Column("reply_to_msg_id", Text),  # GOWA msg_id of the quoted message (reply)
    # plano 01: thread de atendimento. Aditivo e nullable (contact_id permanece).
    # FK por nome (conversations é definida adiante).
    Column("conversation_id", Integer,
           ForeignKey("conversations.id", ondelete="CASCADE")),
)
Index("idx_msg_contact_ts", messages.c.contact_id, messages.c.ts)
Index("idx_msg_id", messages.c.msg_id)
Index("idx_msg_conversation_ts", messages.c.conversation_id, messages.c.ts)


usage = Table(
    "usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("call_type", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("prompt_tokens", Integer, nullable=False, server_default="0"),
    Column("completion_tokens", Integer, nullable=False, server_default="0"),
    Column("total_tokens", Integer, nullable=False, server_default="0"),
    Column("cost_usd", Float, nullable=False, server_default="0.0"),
    Column("ts", Float, nullable=False),
)
Index("idx_usage_contact_ts", usage.c.contact_id, usage.c.ts)
Index("idx_usage_ts", usage.c.ts)


tags = Table(
    "tags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False, unique=True),
    Column("color", Text, nullable=False),
)


contact_tags = Table(
    "contact_tags",
    metadata,
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)
Index("idx_ct_tag", contact_tags.c.tag_id)


# Quick replies (canned responses) — plano 04. Global single list (P42, no scope),
# plain text (P47, no variables). short_code stored WITHOUT the leading slash.
quick_replies = Table(
    "quick_replies",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("short_code", Text, nullable=False, unique=True),  # atalho SEM a barra, UNIQUE global (P41)
    Column("content", Text, nullable=False),                  # texto puro (sem placeholders, P47)
    Column("created_at", Float, nullable=False),              # epoch (P56)
    Column("updated_at", Float, nullable=False),
)


# Custom attribute definitions (plano 05). applies_to separates contact|conversation
# scopes (P54); the same key may exist in both (P51, UNIQUE per (key, applies_to)).
# Soft-delete via deleted_at (P49). Values live in <entity>.custom_attributes JSON.
custom_attribute_definitions = Table(
    "custom_attribute_definitions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("attribute_key", Text, nullable=False),            # snake_case, IDENTIDADE — nunca renomear
    Column("display_name", Text, nullable=False),
    Column("type", Text, nullable=False, server_default="text"),   # text|number|date|list|checkbox|link
    Column("applies_to", Text, nullable=False),               # contact|conversation
    Column("options", _json_type(), nullable=True),           # JSON array de strings (só p/ type=list)
    Column("required", Integer, nullable=False, server_default="0"),
    Column("description", Text, nullable=False, server_default=""),
    Column("regex_pattern", Text, nullable=True),
    Column("regex_cue", Text, nullable=True),
    Column("position", Integer, nullable=False, server_default="0"),
    # plano 05 Fase 6: marca defs que entram no filter-schema e ganham índice (plano 08).
    Column("filterable", Integer, nullable=False, server_default="0"),
    # plano 19: atributos PADRÃO do contato (built-in) — semeados no boot, protegidos
    # contra delete/rename. Mesma estrutura dos personalizados; 1 = atributo de sistema.
    Column("is_system", Integer, nullable=False, server_default="0"),
    Column("created_by", Integer, nullable=True),             # FK -> users (plano 03), nullable por ora
    Column("created_at", Float, nullable=False),              # epoch float (P56)
    Column("deleted_at", Float, nullable=True),               # soft-delete (P49): NULL = ativa
    UniqueConstraint("attribute_key", "applies_to", name="uq_attr_key_scope"),
)
Index("idx_cad_applies_to", custom_attribute_definitions.c.applies_to)


# Channels + credentials (plano 02 Fase 0). The core talks to providers through
# the ChannelRegistry; credentials are TEXT in the MVP (P15 — no encryption,
# masked at the API boundary). conversations/messages.channel_id is plano 01.
channels = Table(
    "channels",
    metadata,
    Column("id", Text, primary_key=True),                 # "default" (snake_case)
    Column("provider", Text, nullable=False),             # gowa | whatsapp_cloud | telegram | test
    Column("display_name", Text, nullable=False, server_default=""),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("gowa_device_id", Text, nullable=True),        # X-Device-Id (gowa only)
    Column("gowa_isolation", Text, nullable=False, server_default="shared"),  # shared|dedicated_process
    Column("config", Text, nullable=True),                # JSON: non-secret per-channel prefs
    Column("connected", Integer, nullable=False, server_default="0"),
    Column("logged_in", Integer, nullable=False, server_default="0"),
    Column("own_phone", Text, nullable=True),
    Column("last_error", Text, nullable=True),
    # Soft-delete (plano inboxes/canais §4.3-c): "excluir" um canal o arquiva
    # (esconde da UI) em vez de apagar, preservando o histórico de conversas da
    # sua inbox. Hard-delete continua disponível via ?purge=true.
    Column("archived", Integer, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

channel_credentials = Table(
    "channel_credentials",
    metadata,
    Column("channel_id", Text,
           ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
    Column("key", Text, nullable=False),                  # access_token | verify_token | bot_token | ...
    Column("value", Text, nullable=False),                # TEXT in MVP (P15 — masked at API edge)
    UniqueConstraint("channel_id", "key", name="uq_channel_credentials"),
)


# ── RBAC (plano 03 Fase 1) ────────────────────────────────────────────────
# Users, roles, permissions + sessions. Argon2id password hashes (PHC string,
# salt embedded). admin is a short-circuit (role key == 'admin' ⇒ bypass) and is
# NOT seeded into role_permissions. inbox_members comes with plano 01.
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False, server_default=""),
    Column("password_hash", Text, nullable=False),       # Argon2id PHC string
    Column("is_active", Integer, nullable=False, server_default="1"),
    # custom_permissions=1 ⇒ this user's effective permission set is the explicit
    # grants in user_permissions, REPLACING roles entirely (no role, no admin
    # short-circuit). custom_permissions=0 ⇒ permissions come from roles.
    Column("custom_permissions", Integer, nullable=False, server_default="0"),
    Column("last_login_at", Float),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
Index("idx_users_email", users.c.email)

roles = Table(
    "roles",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", Text, nullable=False, unique=True),     # admin|gestor|atendente
    Column("name", Text, nullable=False),
    Column("is_system", Integer, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),
)

permissions = Table(
    "permissions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", Text, nullable=False, unique=True),     # conversation.reply etc.
    Column("description", Text, nullable=False, server_default=""),
    # Plugin RBAC (plano "RBAC para Plugins" §3.1): NULL for core perms; the
    # plugin id + group label for perms declared in a plugin's ``rbac:`` block.
    Column("plugin_id", Text, nullable=True),
    Column("group_label", Text, nullable=True),
)
Index("idx_permissions_plugin", permissions.c.plugin_id)

role_permissions = Table(
    "role_permissions",
    metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
    Column("permission_id", Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False),
    PrimaryKeyConstraint("role_id", "permission_id"),
)

user_roles = Table(
    "user_roles",
    metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
    PrimaryKeyConstraint("user_id", "role_id"),
)

# Per-user explicit permission grants — only consulted when the user's
# users.custom_permissions flag is set. Lets an admin give a single user a set of
# permissions outside of any role ("completely customizable").
user_permissions = Table(
    "user_permissions",
    metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("permission_id", Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False),
    PrimaryKeyConstraint("user_id", "permission_id"),
)
Index("idx_user_permissions_user", user_permissions.c.user_id)

user_sessions = Table(
    "user_sessions",
    metadata,
    Column("id", Text, primary_key=True),                 # secrets.token_urlsafe(32)
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("last_seen_at", Float),
    Column("user_agent", Text),
    Column("ip", Text),
)
Index("idx_sessions_user", user_sessions.c.user_id)
Index("idx_sessions_expires", user_sessions.c.expires_at)


# ── Inbox e Conversas (plano 01 Fase 1) ────────────────────────────────────
# Modelo 3 níveis: Contact → ContactInbox (identidade da pessoa num canal) →
# Conversation (thread de atendimento). assignee_user_id/team_id são NULLABLE
# SEM FK por robustez de ordem de migration (P1). channel_id liga p/ channels.id
# "default" (plano 02 faz ALTER aditivo depois). conversation_id em messages é
# aditivo. custom_attributes de conversa é JSON/JSONB nativo (NUNCA TEXT — coord
# plano 05). status só open|closed (P3); is_archived ortogonal (P10).
inboxes = Table(
    "inboxes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False, server_default="WhatsApp"),
    Column("channel_type", Text, nullable=False, server_default="whatsapp"),
    # → channels.id. FK com ON DELETE CASCADE (plano inboxes/canais §4.5):
    # garante o invariante 1:1 e a limpeza automática num purge. Nullable: inboxes
    # cujo canal foi removido ficam com channel_id NULL (órfãs preservadas, escondidas
    # da UI pelo JOIN de list_with_channel) em vez de violar a FK.
    Column("channel_id", Text, ForeignKey("channels.id", ondelete="CASCADE")),
    Column("agent_bot_enabled", Integer, nullable=False, server_default="1"),  # gate IA nível 2
    Column("default_agent_key", Text),                          # plano 06: agente default da inbox
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

contact_inboxes = Table(
    "contact_inboxes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("inbox_id", Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False),
    Column("source_id", Text, nullable=False),                  # chave de resolução = JID
    Column("source_jid", Text),
    Column("source_lid", Text),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("inbox_id", "source_id", name="uq_contact_inbox_inbox_source"),
)
Index("idx_contact_inbox_contact", contact_inboxes.c.contact_id)
Index("idx_contact_inbox_lid", contact_inboxes.c.inbox_id, contact_inboxes.c.source_lid)

# Which panel users (agents) belong to an inbox. A non-admin user without
# ``conversation.read_all`` only sees conversations of the inboxes they are a
# member of. Managed from the channel editor (one inbox per channel). admin and
# anyone holding ``conversation.read_all`` bypass membership and see everything.
inbox_members = Table(
    "inbox_members",
    metadata,
    Column("inbox_id", Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", Float, nullable=False),
    PrimaryKeyConstraint("inbox_id", "user_id"),
)
Index("idx_inbox_members_user", inbox_members.c.user_id)

conversations = Table(
    "conversations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("display_id", Integer, nullable=False),              # sequencial humano (P6)
    Column("inbox_id", Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("contact_inbox_id", Integer,
           ForeignKey("contact_inboxes.id", ondelete="CASCADE"), nullable=False),
    Column("status", Text, nullable=False, server_default="open"),  # open|closed (P3)
    Column("is_archived", Integer, nullable=False, server_default="0"),  # ortogonal (P10)
    Column("assignee_user_id", Integer),                        # NULLABLE sem FK (P1)
    Column("team_id", Integer),                                 # NULLABLE sem FK
    Column("priority", Text),
    Column("ai_active", Integer, nullable=False, server_default="1"),   # gate IA nível 3
    Column("active_agent_key", Text),                          # plano 06: agente da conversa
    Column("opened_at", Float, nullable=False),
    Column("resolved_at", Float),
    Column("waiting_since", Float),
    Column("last_activity_at", Float, nullable=False),
    Column("custom_attributes", _json_type(), nullable=False, server_default="{}"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("display_id", name="uq_conv_display_id"),
)
Index("idx_conv_inbox_status", conversations.c.inbox_id, conversations.c.status)
Index("idx_conv_assignee_status", conversations.c.assignee_user_id, conversations.c.status)
Index("idx_conv_contact", conversations.c.contact_id)
Index("idx_conv_contact_inbox", conversations.c.contact_inbox_id)
Index("idx_conv_last_activity", conversations.c.last_activity_at)
Index("idx_conv_archived", conversations.c.is_archived)

# Contador atômico de display_id (P6) — INSERT/UPDATE na mesma transação do create.
conversation_counters = Table(
    "conversation_counters",
    metadata,
    Column("name", Text, primary_key=True),
    Column("next_value", Integer, nullable=False, server_default="1"),
)


# Etiquetas de conversa (estilo Chatwoot labels) — registro GLOBAL próprio, SEPARADO
# das tags de contato (tags/contact_tags). Decisão Thiago: etiquetas pertencem à
# conversa, não ao contato. Atribuição N:N via conversation_label_links.
conversation_labels = Table(
    "conversation_labels",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False, unique=True),
    Column("color", Text, nullable=False, server_default="#6b7280"),
    Column("position", Integer, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),
)

conversation_label_links = Table(
    "conversation_label_links",
    metadata,
    Column("conversation_id", Integer,
           ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
    Column("label_id", Integer,
           ForeignKey("conversation_labels.id", ondelete="CASCADE"), primary_key=True),
)
Index("idx_conv_label_links_label", conversation_label_links.c.label_id)


unread_msg_ids = Table(
    "unread_msg_ids",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("msg_id", Text, nullable=False),
)
Index("idx_unread_contact", unread_msg_ids.c.contact_id)


executions = Table(
    "executions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("phone", Text, nullable=False),
    Column("trigger_type", Text, nullable=False, server_default="webhook"),
    Column("status", Text, nullable=False, server_default="running"),
    Column("started_at", Float, nullable=False),
    Column("completed_at", Float),
    Column("error", Text),
    # Which DB-driven agent handled this execution + aggregate cost/usage.
    # Populated only when the AI engine (ai_engine_enabled) is active; null/0
    # otherwise so the legacy path is unaffected.
    Column("agent_key", Text),
    Column("total_tokens", Integer, nullable=False, server_default="0"),
    Column("total_cost_usd", Float, nullable=False, server_default="0.0"),
    Column("routing_steps", Text),                             # plano 06: JSON dos saltos de handoff
)
Index("idx_exec_started", executions.c.started_at)


execution_steps = Table(
    "execution_steps",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("execution_id", Integer, ForeignKey("executions.id", ondelete="CASCADE"), nullable=False),
    Column("step_type", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="ok"),
    Column("data", Text),
    Column("ts", Float, nullable=False),
    Column("agent_key", Text),                                  # plano 06: qual agente executou o passo
)
Index("idx_step_exec", execution_steps.c.execution_id)


plugins = Table(
    "plugins",
    metadata,
    Column("id", Text, primary_key=True),
    Column("version", Text, nullable=False, server_default=""),
    Column("enabled", Integer, nullable=False, server_default="0"),
    Column("installed_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("load_error", Text),
    # JSON array of pip specs already installed for this plugin's manifest
    # ``dependencies`` (cache marker: the loader skips pip on boot when this
    # equals the manifest's declared deps).
    Column("installed_deps", Text, nullable=False, server_default="[]"),
)


plugin_migrations = Table(
    "plugin_migrations",
    metadata,
    Column("plugin_id", Text, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("applied_at", Float, nullable=False),
)


tool_overrides = Table(
    "tool_overrides",
    metadata,
    Column("name", Text, primary_key=True),
    Column("plugin_id", Text),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("description", Text),
    Column("display_label", Text),
    Column("updated_at", Float, nullable=False),
)
Index("idx_tool_overrides_plugin", tool_overrides.c.plugin_id)


# --------------------------------------------------------------------------- #
# AI engine — config-in-DB + code-in-DB (prefix ``ai_``)
# --------------------------------------------------------------------------- #
# These tables move the agent's prompt/model/tools out of code and into the DB,
# so behaviour can change without a deploy. All JSON-shaped values are stored as
# JSON-encoded TEXT (portable across SQLite/Postgres), mirroring ``config``.

ai_agents = Table(
    "ai_agents",
    metadata,
    # ``agent_key`` is identity — never rename (breaks executions.agent_key).
    Column("agent_key", Text, primary_key=True),
    Column("display_name", Text, nullable=False, server_default=""),
    Column("prompt_key", Text, nullable=False, server_default=""),
    # JSON object: {model, temperature, top_p, max_tokens, ...}
    Column("model_config", Text, nullable=False, server_default="{}"),
    # JSON array of tool names, or null/"all" meaning every registered tool.
    Column("tool_names", Text),
    Column("enabled", Integer, nullable=False, server_default="1"),
    # plano 06: roteamento/handoff (consumido pelo motor de routing futuro).
    Column("description", Text, nullable=False, server_default=""),
    Column("is_router", Integer, nullable=False, server_default="0"),
    Column("routing_targets", Text),                          # JSON array de agent_keys
    Column("hooks_config", Text, nullable=False, server_default="{}"),  # plano 06: hooks declarativos
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", Float, nullable=False),
)


ai_prompts = Table(
    "ai_prompts",
    metadata,
    # ``prompt_key`` is identity — referenced by ai_agents.prompt_key.
    Column("prompt_key", Text, primary_key=True),
    # Template body with ``{placeholder}`` slots resolved from ai_variables.
    Column("body", Text, nullable=False, server_default=""),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", Float, nullable=False),
)


ai_variables = Table(
    "ai_variables",
    metadata,
    # Global values referenceable by prompts via ``{name}``.
    Column("name", Text, primary_key=True),
    Column("value", Text, nullable=False, server_default=""),
    Column("category", Text, nullable=False, server_default=""),
    Column("updated_at", Float, nullable=False),
)


ai_tools = Table(
    "ai_tools",
    metadata,
    # ``name`` is identity (== schema function name; == usage.call_type).
    Column("name", Text, primary_key=True),
    # 'code' = user-authored, runs ISOLATED in a subprocess (gated by
    # ai_tools_code_enabled). 'builtin' = a seeded core tool, runs IN-PROCESS
    # with the live ToolContext (handler/DB), always available, can't be deleted.
    Column("kind", Text, nullable=False, server_default="code"),
    Column("description", Text, nullable=False, server_default=""),
    # Python source materialised to storages/ai_tools/<name>.py and imported.
    Column("code", Text, nullable=False, server_default=""),
    # JSON array of pip specs (e.g. ["httpx>=0.27,<0.28"]).
    Column("dependencies", Text, nullable=False, server_default="[]"),
    Column("enabled", Integer, nullable=False, server_default="1"),
    # pending | installing | ok | failed — gates registration (fail-closed).
    Column("install_status", Text, nullable=False, server_default="pending"),
    Column("install_error", Text),
    # JSON array — the dependency specs last successfully installed (cache
    # marker: pip is skipped on boot when this equals ``dependencies``).
    Column("installed_deps", Text, nullable=False, server_default="[]"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("updated_at", Float, nullable=False),
)


# History tables — one snapshot row per save (rollback + change trail). The
# ``snapshot`` column holds the full JSON-encoded row as it was after the save.
ai_agents_history = Table(
    "ai_agents_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_key", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("snapshot", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("idx_ai_agents_hist", ai_agents_history.c.agent_key, ai_agents_history.c.version)


ai_prompts_history = Table(
    "ai_prompts_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("prompt_key", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("snapshot", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("idx_ai_prompts_hist", ai_prompts_history.c.prompt_key, ai_prompts_history.c.version)


ai_tools_history = Table(
    "ai_tools_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("snapshot", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("idx_ai_tools_hist", ai_tools_history.c.name, ai_tools_history.c.version)


# Audit trail (plano 07). Append-only "who did what, when, to which resource,
# before/after". FK to users.id is LOGICAL (no ForeignKey/cascade — the trail
# must survive user deletion). before/after are TEXT JSON, masked at ingestion
# (P-LGPD). No UPDATE/DELETE by id in the repo — only purge() for retention.
audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("actor_user_id", Integer, nullable=True),                       # logical FK -> users.id
    Column("actor_type", Text, nullable=False, server_default="system"),   # system | user | ai
    Column("actor_label", Text, nullable=True),                            # name snapshot at the time
    Column("action", Text, nullable=False),                                # recurso.verbo (db/audit_actions)
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Text, nullable=True),                            # string: phone/jid/uuid/id
    Column("before_json", Text, nullable=True),                            # JSON, already masked
    Column("after_json", Text, nullable=True),                             # JSON, already masked
    Column("ip_address", Text, nullable=True),
    Column("request_id", Text, nullable=True),
    Column("created_at", Float, nullable=False),                           # epoch float (P56)
)
Index("idx_audit_created", audit_log.c.created_at)
Index("idx_audit_actor", audit_log.c.actor_user_id, audit_log.c.created_at)
Index("idx_audit_resource", audit_log.c.resource_type, audit_log.c.resource_id)
Index("idx_audit_action", audit_log.c.action, audit_log.c.created_at)


# Saved conversation filters (Chatwoot-style): each user can name and persist one
# or more inbox filter presets. ``user_id`` is NULL for legacy single-password
# sessions (no user identity) → those presets are shared on that install. ``spec``
# is the full filter snapshot (status/assignment/sort/tags/advanced clauses).
saved_conversation_filters = Table(
    "saved_conversation_filters",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=True),                 # logical FK -> users.id; NULL = legacy/shared
    Column("name", Text, nullable=False),
    Column("spec", _json_type(), nullable=False),              # {statusFilter, assignmentTab, sortBy, tagFilter, advFilters}
    Column("position", Integer, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
Index("idx_saved_filters_user", saved_conversation_filters.c.user_id,
      saved_conversation_filters.c.position)


# Set of core table names — used by the SQLite → Postgres migration helper to
# distinguish what belongs to the app vs. plugin-owned tables.
CORE_TABLES = frozenset(t.name for t in metadata.sorted_tables)
