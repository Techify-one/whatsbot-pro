"""Permission catalog + default role matrix (plano 03 Fase 1).

Single source of truth for the seed, the authorization checker, and the admin
UI. ``admin`` is a short-circuit (role key == 'admin' ⇒ bypass) and is NOT listed
in ROLE_DEFAULTS — that avoids "forgot to grant the new permission to admin".

``PERMISSION_CATALOG`` itself now lives in ``domain.permission_catalog`` (plano 23
Fase E1) so the data layer can read it without importing ``server`` (breaks a
db→server import cycle). It is re-exported here so existing importers keep
working via ``server.permissions``. The role matrix below stays here — it is a
server-policy concern, not a data-layer one.
"""

from domain.permission_catalog import PERMISSION_CATALOG

# Role keys + pt-BR labels (admin/gestor/atendente are the system roles).
ROLE_LABELS = {
    "admin": "Administrador",
    "gestor": "Gestor",
    "atendente": "Atendente",
}

# admin via short-circuit; NOT listed here.
ROLE_DEFAULTS = {
    "gestor": {
        "conversation.read", "conversation.reply", "conversation.assign",
        "conversation.resolve", "contact.read", "contact.write", "contact.import",
        "channel.manage", "settings.manage", "plugins.manage", "billing.manage",
        # IA granular (substitui o antigo agent.manage — gestor recebe todas).
        # Prompt dividido em edit/version/delete (substitui agent.prompts.manage).
        "agent.config.manage", "agent.create", "agent.duplicate",
        "agent.prompts.edit", "agent.prompts.version", "agent.prompts.delete",
        "agent.tools.manage", "agent.variables.manage",
        "quickreply.manage", "template.create", "template.delete", "audit.read",
        # Plano 24 — gestor recebe as 9 novas (database.manage fica admin-only)
        "contact.delete", "conversation.delete", "tag.manage",
        "conversation_label.manage", "sandbox.use", "usage.read",
        "custom_attribute.manage", "execution.read", "execution.delete",
    },
    "atendente": {
        "conversation.read", "conversation.reply", "conversation.resolve",
        "contact.read", "quickreply.manage",
    },
}

ALL_PERMISSION_KEYS = [k for k, _ in PERMISSION_CATALOG]
