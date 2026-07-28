"""Audit action catalogue (plano 07).

Constants (not a repo): ``action`` strings (``recurso.verbo``), ``resource_type``
strings, and ``AUDITABLE_EVENTS`` — the allowlist mapping a bus event name to
``(action, resource_type)`` so the core ``*`` listener knows what to persist.

High-volume events (``message.sent``/``message.received``) are intentionally OUT
(already in the ``messages`` history). Adding a new auto-audited event = add one
line to ``AUDITABLE_EVENTS``.

Plugins do NOT touch this file: they call ``plugins.context.audit(...)``, whose
action string is namespaced by the plugin id and validated against
:data:`PLUGIN_ACTION_RE` (see docs/PLUGINS_AUDITAVEIS.md). The catalogue here
stays the core's own vocabulary.
"""

from __future__ import annotations

import re


class ResourceType:
    USER = "user"
    ROLE = "role"
    INBOX = "inbox"
    CONVERSATION = "conversation"
    CONFIG = "config"
    TOOL = "tool"
    PLUGIN = "plugin"
    AGENT = "agent"
    CONTACT = "contact"
    TAG = "tag"
    BILLING = "billing"
    DATA = "data"
    CHANNEL = "channel"


class AuditAction:
    # Auth & RBAC (plano 03)
    AUTH_LOGIN = "auth.login"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGOUT = "auth.logout"
    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    USER_DISABLE = "user.disable"
    USER_DELETE = "user.delete"
    USER_PASSWORD_RESET = "user.password_reset"
    ROLE_ASSIGN = "role.assign"
    # Config / IA engine (plano 06)
    CONFIG_UPDATE = "config.update"
    TOOL_OVERRIDE = "tool.override"
    AGENT_UPDATE = "agent.update"
    # Plugins
    PLUGIN_ENABLE = "plugin.enable"
    PLUGIN_DISABLE = "plugin.disable"
    PLUGIN_UPDATE = "plugin.update"
    PLUGIN_SETTINGS_UPDATE = "plugin.settings_update"
    PLUGIN_INSTALL = "plugin.install"
    PLUGIN_UNINSTALL = "plugin.uninstall"
    # Channels (auditoria de canais) — o canal é infraestrutura: quem criou, quem
    # trocou a credencial e quem desconectou o número precisa ficar na trilha.
    CHANNEL_CREATE = "channel.create"
    CHANNEL_UPDATE = "channel.update"
    CHANNEL_DELETE = "channel.delete"
    CHANNEL_RESTORE = "channel.restore"
    CHANNEL_MEMBERS_UPDATE = "channel.members_update"
    CHANNEL_SESSION = "channel.session"
    CHANNEL_DUPLICATE_REFUSED = "channel.duplicate_refused"
    # Contacts / tags
    CONTACT_UPDATE = "contact.update"
    CONTACT_TOGGLE_AI = "contact.toggle_ai"
    CONTACT_TAGGED = "contact.tagged"
    TAG_CREATE = "tag.create"
    TAG_UPDATE = "tag.update"
    TAG_DELETE = "tag.delete"
    # Data
    DATA_EXPORT = "data.export"


# Bus event name -> (action, resource_type). Allowlist consumed by the core
# audit listener (server/audit_listener.py). Only events ALREADY emitted today.
AUDITABLE_EVENTS: dict[str, tuple[str, str]] = {
    "config.changed":         (AuditAction.CONFIG_UPDATE, ResourceType.CONFIG),
    "tool_override.changed":  (AuditAction.TOOL_OVERRIDE, ResourceType.TOOL),
    "plugin.enabled":         (AuditAction.PLUGIN_ENABLE, ResourceType.PLUGIN),
    "plugin.disabled":        (AuditAction.PLUGIN_DISABLE, ResourceType.PLUGIN),
    "plugin.updated":         (AuditAction.PLUGIN_UPDATE, ResourceType.PLUGIN),
    "plugin.settings.changed": (AuditAction.PLUGIN_SETTINGS_UPDATE, ResourceType.PLUGIN),
    "plugin.imported":        (AuditAction.PLUGIN_INSTALL, ResourceType.PLUGIN),
    "plugin.deleted":         (AuditAction.PLUGIN_UNINSTALL, ResourceType.PLUGIN),
    "contact.updated":        (AuditAction.CONTACT_UPDATE, ResourceType.CONTACT),
    "contact.ai_toggled":     (AuditAction.CONTACT_TOGGLE_AI, ResourceType.CONTACT),
    "contact.tagged":         (AuditAction.CONTACT_TAGGED, ResourceType.CONTACT),
    "tag.created":            (AuditAction.TAG_CREATE, ResourceType.TAG),
    "tag.updated":            (AuditAction.TAG_UPDATE, ResourceType.TAG),
    "tag.deleted":            (AuditAction.TAG_DELETE, ResourceType.TAG),
    # Canais. ``channel.status_changed`` fica DE FORA de propósito: é um read de
    # status (roda a cada poll do painel) e inundaria a trilha.
    "channel.created":        (AuditAction.CHANNEL_CREATE, ResourceType.CHANNEL),
    "channel.updated":        (AuditAction.CHANNEL_UPDATE, ResourceType.CHANNEL),
    "channel.deleted":        (AuditAction.CHANNEL_DELETE, ResourceType.CHANNEL),
    "channel.restored":       (AuditAction.CHANNEL_RESTORE, ResourceType.CHANNEL),
    "channel.members_changed": (AuditAction.CHANNEL_MEMBERS_UPDATE, ResourceType.CHANNEL),
    "channel.session_action": (AuditAction.CHANNEL_SESSION, ResourceType.CHANNEL),
    "channel.duplicate_refused": (AuditAction.CHANNEL_DUPLICATE_REFUSED, ResourceType.CHANNEL),
}


# ── Seam de auditoria para plugins ────────────────────────────────────────────
#
# Um plugin não edita ``AUDITABLE_EVENTS`` (o core não conhece plugin por nome).
# Ele chama ``plugins.context.audit(<plugin_id>, "<verbo>", ...)``; a ação final é
# ``<plugin_id>.<verbo>`` — namespaced, então nunca colide com a vocabulário do
# core acima nem com a de outro plugin. Esta regex é o contrato validado no write
# path (server.audit_listener.record): id de plugin + pelo menos um segmento.
PLUGIN_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}$")


def namespaced_action(plugin_id: str, action: str) -> str:
    """``("protocolos", "config.update")`` -> ``"protocolos.config.update"``.

    Idempotente: uma ação que JÁ vem prefixada com o id do plugin passa intacta,
    então tanto ``audit("protocolos", "config.update")`` quanto
    ``audit("protocolos", "protocolos.config.update")`` gravam a mesma coisa.
    """
    pid = str(plugin_id or "").strip()
    act = str(action or "").strip()
    if not pid:
        return act
    return act if act == pid or act.startswith(f"{pid}.") else f"{pid}.{act}"
