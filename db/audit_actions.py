"""Audit action catalogue (plano 07).

Constants (not a repo): ``action`` strings (``recurso.verbo``), ``resource_type``
strings, and ``AUDITABLE_EVENTS`` — the allowlist mapping a bus event name to
``(action, resource_type)`` so the core ``*`` listener knows what to persist.

High-volume events (``message.sent``/``message.received``) are intentionally OUT
(already in the ``messages`` history). Adding a new auto-audited event = add one
line to ``AUDITABLE_EVENTS``.
"""

from __future__ import annotations


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
    "contact.updated":        (AuditAction.CONTACT_UPDATE, ResourceType.CONTACT),
    "contact.ai_toggled":     (AuditAction.CONTACT_TOGGLE_AI, ResourceType.CONTACT),
    "contact.tagged":         (AuditAction.CONTACT_TAGGED, ResourceType.CONTACT),
    "tag.created":            (AuditAction.TAG_CREATE, ResourceType.TAG),
    "tag.updated":            (AuditAction.TAG_UPDATE, ResourceType.TAG),
    "tag.deleted":            (AuditAction.TAG_DELETE, ResourceType.TAG),
}
