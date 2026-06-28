# Plano 23 · Sub-plano 03 — Plugins: eventos de domínio & extensibilidade

> Parte do [Plano 23 — Mestre](23-plano-refatoracao-00-mestre.md).

## 3. Catálogo de EVENTOS DE DOMÍNIO — auditado (já existe? / relocar / faltante)

Convenção: `<agregado>.<verbo no passado>`, payload **primitivo (DTO), nunca a row crua**, emitido **do service, APÓS o commit**. Reusa o bus (`emit_with_filter`); entra em `KNOWN_EVENTS`.

> **C1 NÃO é "introduzir 12 do zero".** É: **normalizar payload** (row→DTO) dos que já existem + **relocar** emit p/ service + **adicionar os 5 faltantes**. Teste de caracterização: **cada evento dispara EXATAMENTE 1× por ação** (antes e depois).

### 3.1 Conversa / Atendimento

| Evento | Já no bus? | Ação C1 | Payload-alvo (DTO) |
|---|---|---|---|
| `conversation.created` | **SIM** (`conversations.py` via `_broadcast`) | normalizar row→DTO + relocar p/ service | `{conversation_id, contact_id, phone, channel_id, inbox_id, ts}` |
| `conversation.status_changed` | **SIM** | normalizar + relocar | `{conversation_id, from_status, to_status, actor, ts}` |
| `conversation.assigned` | **SIM** | normalizar + relocar; **distinguir** de unassigned | `{conversation_id, assignee_user_id, previous_assignee, actor, ts}` |
| `conversation.ai_toggled` | **SIM** (+ `contact.ai_toggled`) | normalizar; `scope:"conversation"\|"contact"` | `{conversation_id, contact_id, ai_active, scope, actor, ts}` |
| `conversation.archived` | **SIM** | normalizar | `{conversation_id, archived, actor, ts}` |
| `conversation.updated` / `.deleted` | **SIM** | normalizar (genéricos — manter) | DTO mínimo |
| `conversation.reopened` | **NÃO** | **ADICIONAR** (cliente reabre fechada) | `{conversation_id, contact_id, phone, previous_status, trigger:"inbound", ts}` |
| `conversation.unassigned` | parcial (vem como assigned c/ null) | **ADICIONAR** verbo distinto | `{conversation_id, previous_assignee, actor, ts}` |
| `conversation.transferred_to_human` | **NÃO** | **ADICIONAR** (tool `transfer_to_human`) | `{conversation_id, contact_id, reason, ts}` |
| `conversation.agent_changed` | **NÃO** | **ADICIONAR** (handoff entre agentes IA) | `{conversation_id, from_agent, to_agent, reason, ts}` |
| `conversation.attribute_set` | **NÃO** | **ADICIONAR** (custom attribute) | `{conversation_id, key, value, actor, ts}` |
| `conversation.ai_takeover` | interno (dedupe, não bus) | **PROMOVER a evento** (1×/conv) | `{conversation_id, agent_key, ts}` |
| `message.persisted` | **NÃO** (interno) | **ADICIONAR** (após INSERT em `add_message`) — desacopla `memory.py` | `{conversation_id, contact_id, role, msg_id, ts}` |

### 3.2 Canal (mínimo — `connected/disconnected` dobrados em `status_changed`)

| Evento | Já? | Ação |
|---|---|---|
| `channel.created` / `.updated` / `.deleted` | parcial | normalizar/adicionar no `channel_service` |
| `channel.status_changed` | adicionar | `{channel_id, status, is_connected, is_logged_in, ts}` — **inclui** conectado/desconectado via campo `status` (NÃO criar `connected`/`disconnected` separados — adiados) |

### 3.3 Resposta IA (outbound) — **ADIADOS** (sem consumidor hoje)

`reply.ready` / `reply.delivered`: **não enviar agora.** `filter.reply.raw`/`filter.reply.parts`/`filter.reply.part` já cobrem o atendimento. Adicionar quando um plugin pedir (aditivo).

### 3.4 Frontend (client-side, via `registry.emit`) — infra existe, core não emite

| Evento client | Onde adicionar a emissão |
|---|---|
| `ui.conversation.opened` | mount de `ContactDetail` |
| `ui.conversation.selected` | `selectContact` |
| `ui.message.received` | handler `new_message` |

---

## 4. Costuras de extensão (consolidação — NÃO SDK gigante)

### 4.1 UI — consolidar o que existe + 2 slots novos (não 5)

> `priority`, `emit`/`on`, `overrideRoute`, `ModalHost` **JÁ EXISTEM**. O trabalho é **consolidar + documentar como contrato versionado** e adicionar **só** os 2 slots que o atendimento usa hoje.

| Item | Estado | Ação |
|---|---|---|
| `priority` no registry | **existe** | só documentar (rascunho errou ao "adicionar") |
| `overrideRoute('attendances')` | **existe** (first-wins exclusivo) | **documentar contrato**; D2/D3 devem preservar o claim de rota |
| `ModalHost.openModal` | **existe** | documentar como seam de modal async |
| `sidebar.row.badges` (slot) | **novo** | adicionar ponto de render em `ContactList` row; ctx `{row}` — badge SLA/prioridade |
| `chat.header.banner` (slot) | **novo** | adicionar acima das msgs em `ContactDetail`; ctx `{conv}` — faixa "atendimento atual" |
| `chat.composer.*`, `message.context.menu.items` | **adiados** | aditivos; adicionar on-demand |

### 4.2 Filtros de pré-ação (backend) — **inseridos NO commit do service, não em wave separada**

> **Correção de sequenciamento:** filtros são SEAMS — nascem no mesmo commit do service (B3/B4/B5). C2 vira só "registrar `KNOWN_FILTERS` + documentar + warning de typo", **não** "inserir os pontos depois" (isso reabriria os arquivos críticos = 2º risco).

| Filtro | Local (service) | Quando | `None` faz |
|---|---|---|---|
| `filter.conversation.assignment` | `agent_run_service` no `transfer_to_human` | B5 | plugin reescreve destino (**round-robin** — o atendimento precisa) |
| `filter.conversation.before_assign` | `conversation_service.assign` | B4 | aborta atribuição |
| `filter.agent.resolve` | `agent_run_service` antes do spec | B5 | plugin troca agente |
| `filter.conversation.before_status` | **já existe** (inline na rota) | — | relocar p/ service (uniformizar) |
| before_agent_change / before_ai_toggle / before_archive / before_attributes / create_defaults / list_where / row | **ADIADOS** | — | aditivos; sob demanda |

### 4.3 Filtros client — **adiados** (atendimento usa `filter.conversation.beforeResolve` que já existe)

`filter.sidebar.row`, `filter.composer.beforeSend`, `filter.*.contextmenu.items`: adicionar quando o plugin pedir.

### 4.4 O mínimo de "SDK interno"

- **`KNOWN_FILTERS`** em `plugins/events.py` (espelho de `KNOWN_EVENTS`): warning informativo se nome desconhecido (mata o typo silencioso tipo `filter.replay.part`). Não bloqueia (plugins definem filtros próprios).
- **`emit_domain(event)`** + dataclasses em `domain/events.py` (contrato tipado).
- **`system_notices.register_notice(event_type, group, formatter)`** + `register_notice_group()` via `plugins.context` — plugin registra avisos próprios sem patch no core. **Reconciliar** os 4+ pontos de edição atuais + a divergência de nomes com `AUDITABLE_EVENTS`.
- **Frontend `PLUGIN_SERVICES` allowlist** em `plugins/api.js`: substituir `...coreApi` (vaza ~120 fns incl. `createUser/deleteRole`) por allowlist **curada e versionada**. **Deny-list concreta** derivada das fns sensíveis (users/roles/permissions/admin/migrate). **Sequência crítica:** congelar a allowlist **ANTES** do split de `api.js` (D0), grandfathering tudo que `atendimentos` importa hoje.
- **`WHATSBOT_API_VERSION` como semver real**: MINOR = aditivo (novo evento/filter/slot); MAJOR = remover/renomear/mudar payload. Costuras instáveis marcadas `experimental:true` p/ destravar o atendimento sem congelar `1.0`.
- **JSDoc typedefs dos contratos** (prioridade do type-check): slot ctx por nome, filter value por nome, event payload por evento, assinaturas do `PLUGIN_SERVICES`. Enviar como módulo de typedefs que plugins `@import` p/ checagem dev-time.
- **PII boundary:** payloads de domain event carregam `phone`/`contact_id` (PII) entregues a **todo** subscriber incl. plugins de 3º. Nota de design: gating por-permissão da entrega do bus é trabalho futuro (não bloqueia v1).

---

## Fases (workstream C)

### WAVE 0

#### Fase C0 — **Emitir os 5 verbos faltantes das ROTAS atuais (Expand antecipado)** 🟢
- **Objetivo (entrega de valor na semana 1-2):** desacoplar a plataformização da extração de serviço. Não é preciso service limpo p/ **emitir** evento — emitir `conversation.reopened/.transferred_to_human/.agent_changed/.attribute_set/.ai_takeover` + `conversation.unassigned` das **closures atuais** (`conversations.py`, `webhook.py` `_maybe_emit_ai_takeover`, tool `transfer_to_human`). Adicionar a `KNOWN_EVENTS`. **Aditivo, baixo risco** → destrava o plugin `atendimentos` JÁ.
- **Nota:** payload pode ficar como está agora; a **normalização row→DTO** vem em C1 quando os services existirem (mover o emit junto).
- **Risco:** baixo. **NÃO** mexer nos ~7 que já emitem (evitar dupla emissão).

### WAVE 3 — Plataformização (normalizar + relocar + fechar Contract)

> **Expand-Contract.** C0 (Wave 0) já fez o Expand antecipado dos 5 verbos. Aqui: **normalizar payloads** (row→DTO), **relocar emit p/ services**, e **fechar o Contract** (broadcast WS vira listener do domain event).

#### Fase C1 — Normalizar payloads + relocar emit + `domain/events.py` 🔴
- **Objetivo:** `domain/events.py` (dataclasses §3) + `emit_domain()`. **Mover** os emits das rotas (B3-B5 já criaram os services) p/ **dentro dos services, pós-commit**, com payload **DTO** (não row). **Garantir 1×/ação** (teste de caracterização antes/depois). **Verificar `AUDITABLE_EVENTS._resource_id`** ainda resolve com o DTO (senão audit para silenciosamente).
- **Risco:** médio (toca emit de fluxo vivo). Depende de B3-B5 + A2b audit.

#### Fase C2 — `KNOWN_FILTERS` + documentação de contrato 🟢 (com C1)
- **Objetivo:** os **pontos** de filtro já nasceram em B4/B5 (§4.2). Aqui: registrar `KNOWN_FILTERS` (espelho de `KNOWN_EVENTS`) + warning de typo + documentar contratos versionados + `experimental:true`.
- **Risco:** baixo (aditivo).

#### Fase C3 — `system_notices` extensível + eventos de canal 🟢
- **Objetivo:** `register_notice/register_notice_group` via `plugins.context`; grupos auto-declaram config key (mata espelhamento). Evento `channel.status_changed` (do `ChannelRegistry`/`channel_service`). Refatorar os 19 `_f_*` formatters via helper with-actor. Reconciliar com `AUDITABLE_EVENTS`.
- **Risco:** baixo-médio.

#### Fase C4 — Consolidar `plugins/` core 🟢
- **Objetivo:** `PluginRuntime` único (mata 8 globais de `context.py`); tabela `_ENTRY_SPECS` (mata `_load_plugin_module` 8 blocos); `plugins/bootstrap.py` (tira lógica gowa hardcoded do loader); remover validação duplicada loader↔events; `plugins/semver.py`. **Contrato de canal:** corrigir `GOWAChannel.qr()`→`get_qr()` via **alias deprecado por uma wave** (Expand-Contract — canais são providers bundled cujo frontend chama as rotas), **não** rename hard.
- **Caracterização antes:** lifecycle de plugin (G1-mínimo já provê `build_test_app`): carregar gowa/telegram/whatsapp_cloud bundled, `enable→migrations→wiring→broadcast` + método QR.
- **Risco:** médio (ordem de wiring).

#### Fase C5 — **Contract: broadcast WS vira listener do domain event** 🔴
- **Objetivo (fonte única — sem isso a duplicação aumenta):** migrar cada broadcast WS de lifecycle p/ ser disparado por um **listener** do domain event correspondente; o frontend continua recebendo o mesmo WS event. Par-a-par: `conversation_created` broadcast (`memory.py:194`/`conversations.py`) → listener de `ConversationCreated`; status broadcast → listener de `StatusChanged`/`Reopened`; `message.persisted` → listeners que hoje são inline em `add_message` (broadcast + lifecycle notice).
- **Risco:** médio. **Obrigatória** (fecha o Parallel Change).
