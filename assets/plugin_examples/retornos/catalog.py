"""Catálogo de CAMPOS e OPERADORES do construtor de regras (plano 76 §5.3).

PURO (sem DB, sem rede): é a fonte única do vocabulário que o motor (`rules.py`), a API
(`/metadata`) e a UI (`RegraBuilder.js`) compartilham. Porte de `filtros-metadata.ts` do
Nexus, mantendo os **ids originais** (`chatwoot.*` / `virtual.*` / `modulo.*`) para que uma
árvore exportada do Nexus possa ser importada aqui, e acrescentando os campos que só o
WhatsBot tem (`wb.*`).

Rótulo do grupo `chatwoot` é exibido como **"Configurações Gerais Whatsbot"** (o WhatsBot
não fala Chatwoot),
mas o id do campo continua `chatwoot.…` por compatibilidade de import/export.

Campo `temporal=True` é o que gera candidato de agendamento em
:func:`rules.proximo_instante` — o resto é só gate.
"""

from __future__ import annotations

# ── Tipos de valor e operadores permitidos por tipo ───────────────────────────

OPERADORES: dict[str, str] = {
    "eq": "é igual a",
    "neq": "é diferente de",
    "exists": "está preenchido",
    "not_exists": "está vazio",
    "contains": "contém",
    "not_contains": "não contém",
    "starts_with": "começa com",
    "ends_with": "termina com",
    "gt": "é maior que",
    "gte": "é maior ou igual a",
    "lt": "é menor que",
    "lte": "é menor ou igual a",
    "between": "está entre",
    "in": "é um de",
    "not_in": "não é nenhum de",
    "has_any": "tem alguma de",
    "has_all": "tem todas de",
}

# Operadores oferecidos por tipo de campo (a UI filtra o select por aqui).
OPERADORES_POR_TIPO: dict[str, tuple[str, ...]] = {
    "text": ("eq", "neq", "contains", "not_contains", "starts_with", "ends_with",
             "exists", "not_exists"),
    "number": ("eq", "neq", "gt", "gte", "lt", "lte", "between", "exists", "not_exists"),
    "date": ("eq", "neq", "gt", "gte", "lt", "lte", "between", "exists", "not_exists"),
    "time": ("eq", "neq", "gt", "gte", "lt", "lte", "between"),
    "select": ("eq", "neq", "in", "not_in", "exists", "not_exists"),
    "enum": ("eq", "neq", "in", "not_in"),
    "multi-select": ("has_any", "has_all", "not_in", "exists", "not_exists"),
}

# Operadores que consomem uma LISTA de valores (a UI mostra multi-seleção).
OPERADORES_LISTA = ("in", "not_in", "has_any", "has_all")
# Operadores que não consomem valor nenhum.
OPERADORES_SEM_VALOR = ("exists", "not_exists")

GRUPOS: dict[str, str] = {
    "chatwoot": "Configurações Gerais Whatsbot",
    "virtual": "Momento do disparo",
    "modulo": "Configuração (este plugin)",
}

# Rótulo da seção que agrupa TUDO que vem de outros plugins (`contrib.py`). A UI o
# desenha como um cabeçalho antes do primeiro grupo `origem == "plugin"`.
GRUPO_EXTERNOS_LABEL = "Configurações (outros plugins)"

# ── Campos ────────────────────────────────────────────────────────────────────
#
# `options` = origem das opções resolvida pelo endpoint `/metadata` (o catálogo é puro):
#   users | conv_labels | contact_tags | channels | ai_agents | <lista estática>
#
# NÃO existe campo de "caixa de entrada": no WhatsBot o inbox é 1:1 com o canal
# (`inboxes.channel_id`), então o `chatwoot.caixa_id` do Nexus era o MESMO filtro que
# `wb.canal`, com id numérico em vez do id do canal. Ficou só `wb.canal` — o vocabulário
# que a UI do WhatsBot usa. Árvore importada do Nexus que traga `chatwoot.caixa_id` cai
# na regra de campo desconhecido (condição falsa).

CAMPOS: tuple[dict, ...] = (
    # ── Atendimento (ids do Nexus preservados) ──
    {"id": "chatwoot.agente_id", "label": "Atendente atribuído", "grupo": "chatwoot",
     "tipo": "select", "options": "users"},
    {"id": "chatwoot.status", "label": "Status da conversa", "grupo": "chatwoot",
     "tipo": "enum",
     "options": [{"value": "open", "label": "Aberta"},
                 {"value": "closed", "label": "Fechada"}]},
    {"id": "chatwoot.etiquetas", "label": "Etiquetas da conversa", "grupo": "chatwoot",
     "tipo": "multi-select", "options": "conv_labels"},
    {"id": "contato.tags", "label": "Etiquetas do contato", "grupo": "chatwoot",
     "tipo": "multi-select", "options": "contact_tags"},
    {"id": "chatwoot.criado_em", "label": "Conversa criada em", "grupo": "chatwoot",
     "tipo": "date"},
    {"id": "chatwoot.ultima_atividade", "label": "Última atividade", "grupo": "chatwoot",
     "tipo": "date"},
    # ── Atendimento (só WhatsBot) ──
    {"id": "wb.canal", "label": "Canal", "grupo": "chatwoot",
     "tipo": "select", "options": "channels"},
    {"id": "wb.contact_type", "label": "Tipo de contato", "grupo": "chatwoot",
     "tipo": "enum",
     "options": [{"value": "whatsapp", "label": "WhatsApp"},
                 {"value": "telegram", "label": "Telegram"},
                 {"value": "facebook", "label": "Facebook"},
                 {"value": "instagram", "label": "Instagram"},
                 {"value": "website", "label": "Site"},
                 {"value": "outros", "label": "Outros"}]},
    {"id": "wb.agente_ia", "label": "Agente de IA ativo", "grupo": "chatwoot",
     "tipo": "select", "options": "ai_agents"},
    {"id": "wb.ia_ativa", "label": "IA ativa na conversa", "grupo": "chatwoot",
     "tipo": "enum",
     "options": [{"value": "sim", "label": "Sim"}, {"value": "nao", "label": "Não"}]},
    {"id": "wb.grupo", "label": "É um grupo", "grupo": "chatwoot",
     "tipo": "enum",
     "options": [{"value": "sim", "label": "Sim"}, {"value": "nao", "label": "Não"}]},
    # ── Momento do disparo (relógio, na tz da configuração) ──
    {"id": "virtual.hora_atual", "label": "Hora de disparo", "grupo": "virtual",
     "tipo": "time", "temporal": True},
    {"id": "virtual.data_atual", "label": "Data de disparo", "grupo": "virtual",
     "tipo": "date", "temporal": True},
    {"id": "virtual.dia_semana", "label": "Dia da semana para disparo", "grupo": "virtual",
     "tipo": "enum",
     "options": [{"value": "0", "label": "Domingo"}, {"value": "1", "label": "Segunda"},
                 {"value": "2", "label": "Terça"}, {"value": "3", "label": "Quarta"},
                 {"value": "4", "label": "Quinta"}, {"value": "5", "label": "Sexta"},
                 {"value": "6", "label": "Sábado"}]},
    # ── Configuração (estado do próprio controle) ──
    {"id": "modulo.n_disparos_enviados", "label": "Nº de disparos já enviados",
     "grupo": "modulo", "tipo": "number"},
    {"id": "modulo.hora_ultimo_contato", "label": "Hora da última mensagem do cliente",
     "grupo": "modulo", "tipo": "time"},
    {"id": "modulo.horas_desde_ultimo_contato", "label": "Horas desde a última mensagem do cliente",
     "grupo": "modulo", "tipo": "number", "temporal": True},
    {"id": "modulo.dias_desde_ultimo_contato", "label": "Dias desde a última mensagem do cliente",
     "grupo": "modulo", "tipo": "number", "temporal": True},
    {"id": "modulo.dias_calendario_desde_contato",
     "label": "Dias de calendário desde a última mensagem", "grupo": "modulo",
     "tipo": "number", "temporal": True},
    {"id": "modulo.minutos_desde_ultimo_contato",
     "label": "Minutos desde a última mensagem do cliente", "grupo": "modulo",
     "tipo": "number", "temporal": True},
)

_BY_ID = {c["id"]: c for c in CAMPOS}

# Campos contribuídos por OUTROS plugins (`contrib.py` os registra depois de sanear).
# Só o CATÁLOGO é mutável — o vocabulário do core acima continua imutável, e um campo
# externo nunca sobrescreve um do core (`registrar_externos` descarta a colisão).
_EXTERNOS: dict[str, dict] = {}


def registrar_externos(campos) -> None:
    """Substitui o conjunto de campos externos conhecidos (chamado por `contrib`)."""
    global _EXTERNOS
    novos: dict[str, dict] = {}
    for c in campos or []:
        cid = str((c or {}).get("id") or "")
        if cid and cid not in _BY_ID:
            novos[cid] = dict(c)
    _EXTERNOS = novos  # troca atômica: a thread do dispatcher lê sem lock


def campos_externos() -> list[dict]:
    return [dict(c) for c in _EXTERNOS.values()]


def campo_core(campo_id: str) -> dict | None:
    """Definição do campo no vocabulário do CORE (ignora contribuições de plugin)."""
    return _BY_ID.get(str(campo_id or ""))


def campo(campo_id: str) -> dict | None:
    """Definição do campo, ou ``None`` se desconhecido (condição avalia como falsa)."""
    cid = str(campo_id or "")
    return _BY_ID.get(cid) or _EXTERNOS.get(cid)


def tipo_de(campo_id: str) -> str:
    c = campo(campo_id)
    return (c or {}).get("tipo", "text")


def is_temporal(campo_id: str) -> bool:
    return bool((campo(campo_id) or {}).get("temporal"))


def operadores_de(campo_id: str) -> tuple[str, ...]:
    return OPERADORES_POR_TIPO.get(tipo_de(campo_id), OPERADORES_POR_TIPO["text"])


def metadata() -> dict:
    """Payload servido em ``GET /metadata`` (sem as listas dinâmicas, que a rota anexa)."""
    return {
        "grupos": [{"id": k, "label": v} for k, v in GRUPOS.items()],
        "campos": [dict(c) for c in CAMPOS],
        "operadores": [{"id": k, "label": v} for k, v in OPERADORES.items()],
        "operadores_por_tipo": {k: list(v) for k, v in OPERADORES_POR_TIPO.items()},
        "operadores_lista": list(OPERADORES_LISTA),
        "operadores_sem_valor": list(OPERADORES_SEM_VALOR),
        "tipos_mensagem": [
            {"value": "text", "label": "Mensagem de texto (vai ao cliente)"},
            {"value": "private_note", "label": "Nota privada (só o atendente vê)"},
            {"value": "ia_responde_agora", "label": "A IA responde agora (instrução)"},
            {"value": "image", "label": "Imagem"},
            {"value": "audio", "label": "Áudio"},
            {"value": "video", "label": "Vídeo"},
            {"value": "document", "label": "Documento"},
        ],
    }
