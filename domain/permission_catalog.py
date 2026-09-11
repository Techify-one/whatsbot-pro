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
    ("contact.import",        "Importar lista de contatos (CSV)"),
    ("inbox.manage",          "Criar/editar inboxes e membros"),
    ("channel.manage",        "Configurar canais/números"),
    ("settings.manage",       "Configurações globais"),
    # Abas de "Configurações Gerais" — acesso granular por aba (a aba "Sons" é
    # PESSOAL e não exige permissão). Quem tinha ``settings.manage`` recebeu as
    # três na migração ``*_settings_tab_perms``; elas gateiam TAMBÉM a escrita,
    # via ``config.settings.CONFIG_KEY_PERMISSION`` no PUT /api/config.
    ("settings.general",      "Configurações: aba Geral (avisos de sistema no chat)"),
    ("settings.advanced",     "Configurações: aba Avançado (domínio, execuções, auditoria)"),
    ("settings.notifications", "Configurações: aba Notificações"),
    ("plugins.manage",        "Ativar/desativar/configurar plugins"),
    ("billing.manage",        "Recargas/saldo (Techify)"),
    # IA / agente — permissões granulares (substituem o antigo agent.manage,
    # que dava tudo). Ver ``migração *_granular_ai_and_contact_import``.
    ("agent.config.manage",    "IA: configuração do agente (modelo, tools, roteamento)"),
    ("agent.create",           "IA: criar novos agentes"),
    ("agent.duplicate",        "IA: duplicar agentes"),
    # Prompt do agente — permissões granulares (substituem o antigo
    # agent.prompts.manage, que cobria tudo). Ver ``migração *_granular_prompt``.
    ("agent.prompts.edit",     "IA: editar o prompt do agente"),
    ("agent.prompts.version",  "IA: versionar o prompt (histórico, comparar, restaurar)"),
    ("agent.prompts.delete",   "IA: apagar versões do histórico do prompt"),
    ("agent.tools.manage",     "IA: gerenciar tools (código e overrides)"),
    ("agent.variables.manage", "IA: gerenciar variáveis"),
    ("quickreply.manage",     "Respostas rápidas"),
    ("template.create",       "Criar templates (WhatsApp Cloud)"),
    ("template.delete",       "Apagar templates (WhatsApp Cloud)"),
    ("users.manage",          "Criar/editar/desativar usuários e grupos de permissão"),
    ("audit.read",            "Ler trilha de auditoria"),
    ("audit.manage",          "Ativar/desativar a trilha de auditoria"),
    # Plano 24 — maximizar permissões (CRUD)
    ("contact.delete",           "Excluir contato (e todas as conversas)"),
    ("conversation.delete",      "Excluir conversa (apaga histórico)"),
    ("tag.manage",               "Criar/editar/excluir etiquetas (de contato)"),
    ("conversation_label.manage", "Criar/editar/excluir etiquetas de conversa"),
    ("sandbox.use",              "Usar o chat de teste (sandbox)"),
    ("usage.read",               "Ver custos e uso da API"),
    ("custom_attribute.manage",  "Definir campos personalizados"),
    ("execution.read",           "Ver trilha de execuções"),
    ("execution.delete",         "Expurgar execuções"),
    ("database.manage",          "Migração e manutenção do banco"),
    # Chaves de API — a ÚNICA permissão nova do plano de API. Governa
    # *emitir/revogar* chave, NUNCA *usar* a API (quem pode fazer algo no painel
    # pode fazer via chave, com as permissões do próprio dono). Admin-only de
    # propósito: NÃO entra em ``ROLE_DEFAULTS`` — senão um gestor cunharia uma
    # chave com os próprios poderes.
    ("apikey.manage",            "Emitir/revogar chaves de API"),
    # Webhooks de SAÍDA (fase 8): quem cadastra o endpoint para onde os eventos
    # da instalação são empurrados. Admin-only pelo mesmo motivo da chave.
    ("webhook.manage",           "Cadastrar/remover webhooks de saída"),
]


# ── Agrupamento para a tela de permissões (metadado de EXIBIÇÃO) ──────────────
# Cada permissão pertence a um ``tier`` ("core" = sistema, "plugin" = extensão) e
# a um ``group`` (subcabeçalho). É puramente apresentação (não persiste no banco):
# ``rbac_repo.list_catalog`` anexa ``tier``/``group`` a cada item e o
# ``PermissionPicker`` renderiza dois níveis (Sistema vs Plugins → grupos).
#
# ``template.*`` é uma permissão core, mas por decisão de produto aparece na área
# de **Plugins** (a capability é do canal WhatsApp Cloud, um plugin bundled).
#
# CORE_GROUP_ORDER fixa a ordem dos subcabeçalhos core na tela; o frontend também
# a conhece. Chaves core sem entrada caem em ("core", "Outros").
CORE_GROUP_ORDER: list[str] = [
    "Atendimentos e conversas",
    "Contatos e etiquetas",
    "Canais e inboxes",
    "IA e agente",
    "Configurações e sistema",
    "Usuários e auditoria",
]

_G_ATEND = "Atendimentos e conversas"
_G_CONTACT = "Contatos e etiquetas"
_G_CHANNEL = "Canais e inboxes"
_G_AI = "IA e agente"
_G_SETTINGS = "Configurações e sistema"
_G_USERS = "Usuários e auditoria"

PERMISSION_GROUPS: dict[str, tuple[str, str]] = {
    # Atendimentos e conversas
    "conversation.read": ("core", _G_ATEND),
    "conversation.read_all": ("core", _G_ATEND),
    "conversation.reply": ("core", _G_ATEND),
    "conversation.assign": ("core", _G_ATEND),
    "conversation.resolve": ("core", _G_ATEND),
    "conversation.delete": ("core", _G_ATEND),
    "conversation_label.manage": ("core", _G_ATEND),
    "quickreply.manage": ("core", _G_ATEND),
    # Contatos e etiquetas
    "contact.read": ("core", _G_CONTACT),
    "contact.write": ("core", _G_CONTACT),
    "contact.import": ("core", _G_CONTACT),
    "contact.delete": ("core", _G_CONTACT),
    "tag.manage": ("core", _G_CONTACT),
    "custom_attribute.manage": ("core", _G_CONTACT),
    # Canais e inboxes
    "inbox.manage": ("core", _G_CHANNEL),
    "channel.manage": ("core", _G_CHANNEL),
    # IA e agente
    "agent.config.manage": ("core", _G_AI),
    "agent.create": ("core", _G_AI),
    "agent.duplicate": ("core", _G_AI),
    "agent.prompts.edit": ("core", _G_AI),
    "agent.prompts.version": ("core", _G_AI),
    "agent.prompts.delete": ("core", _G_AI),
    "agent.tools.manage": ("core", _G_AI),
    "agent.variables.manage": ("core", _G_AI),
    "sandbox.use": ("core", _G_AI),
    # Configurações e sistema
    "settings.manage": ("core", _G_SETTINGS),
    "settings.general": ("core", _G_SETTINGS),
    "settings.advanced": ("core", _G_SETTINGS),
    "settings.notifications": ("core", _G_SETTINGS),
    "plugins.manage": ("core", _G_SETTINGS),
    "billing.manage": ("core", _G_SETTINGS),
    "database.manage": ("core", _G_SETTINGS),
    # Usuários e auditoria
    "users.manage": ("core", _G_USERS),
    "audit.read": ("core", _G_USERS),
    "audit.manage": ("core", _G_USERS),
    "apikey.manage": ("core", _G_USERS),
    "webhook.manage": ("core", _G_USERS),
    "usage.read": ("core", _G_USERS),
    "execution.read": ("core", _G_USERS),
    "execution.delete": ("core", _G_USERS),
    # Templates — chave core exibida na área de Plugins (pedido de produto).
    "template.create": ("plugin", "Templates (WhatsApp Cloud)"),
    "template.delete": ("plugin", "Templates (WhatsApp Cloud)"),
}


def group_for(key: str) -> tuple[str, str]:
    """``(tier, group)`` de exibição para uma chave core. Fallback ("core", "Outros")."""
    return PERMISSION_GROUPS.get(key, ("core", "Outros"))
