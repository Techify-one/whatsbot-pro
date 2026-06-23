"""Permission catalog + default role matrix (plano 03 Fase 1).

Single source of truth for the seed, the authorization checker, and the admin
UI. ``admin`` is a short-circuit (role key == 'admin' ⇒ bypass) and is NOT listed
in ROLE_DEFAULTS — that avoids "forgot to grant the new permission to admin".
"""

PERMISSION_CATALOG = [
    ("conversation.read",     "Ler conversas dos inboxes em que é membro"),
    ("conversation.read_all", "Ler conversas de qualquer inbox (ignora membership)"),
    ("conversation.reply",    "Responder conversa"),
    ("conversation.assign",   "Atribuir/transferir conversa"),
    ("conversation.resolve",  "Encerrar/reabrir conversa"),
    ("contact.read",          "Ler dados de contato"),
    ("contact.write",         "Editar dados de contato"),
    ("inbox.manage",          "Criar/editar inboxes e membros"),
    ("channel.manage",        "Configurar canais/números"),
    ("settings.manage",       "Configurações globais"),
    ("plugins.manage",        "Ativar/desativar/configurar plugins"),
    ("billing.manage",        "Recargas/saldo (Techify)"),
    ("agent.manage",          "Prompt/modelo/tools do agente"),
    ("quickreply.manage",     "Respostas rápidas"),
    ("template.create",       "Criar templates (WhatsApp Cloud)"),
    ("template.delete",       "Apagar templates (WhatsApp Cloud)"),
    ("users.manage",          "Criar/editar/desativar usuários e grupos de permissão"),
    ("audit.read",            "Ler trilha de auditoria"),
]

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
        "conversation.resolve", "contact.read", "contact.write", "channel.manage",
        "settings.manage", "plugins.manage", "billing.manage", "agent.manage",
        "quickreply.manage", "template.create", "template.delete", "audit.read",
    },
    "atendente": {
        "conversation.read", "conversation.reply", "conversation.resolve",
        "contact.read", "quickreply.manage",
    },
}

ALL_PERMISSION_KEYS = [k for k, _ in PERMISSION_CATALOG]
