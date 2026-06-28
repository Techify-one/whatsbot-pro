"""Permission catalog (plano 03 Fase 1) — neutral home to break a db→server cycle.

``PERMISSION_CATALOG`` is the single source of truth for the core permission keys.
It lives here (rather than in ``server/``) so the data layer
(``db.repositories.rbac_repo.list_catalog``) can read it without importing
``server`` — which previously forced a lazy ``from server.permissions import …``
inside a function to avoid a circular import.

``server.permissions`` re-exports this symbol so existing importers keep working
via the old path, and continues to own the role matrix
(``ROLE_LABELS``/``ROLE_DEFAULTS``/``ALL_PERMISSION_KEYS``), which are
server-policy concerns, not data-layer ones.
"""

from __future__ import annotations

PERMISSION_CATALOG: list[tuple[str, str]] = [
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
