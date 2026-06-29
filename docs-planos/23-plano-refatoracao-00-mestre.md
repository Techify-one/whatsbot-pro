# Plano 23 — Refatoração & Arquitetura do WhatsBot — MESTRE/ÍNDICE

> **Tipo:** Plano arquitetural (NÃO é implementação — é o blueprint a executar fase a fase).
> **Status do sistema:** pré-produção, não distribuído → **rollout agressivo permitido**, com **verde a cada fase** e **caracterização ANTES de tocar fluxo crítico** (webhook/mensageria/agente).
> **Autor do plano:** arquiteto-chefe. Revisado contra 3 críticas adversariais (over-engineering/YAGNI, completude, sequenciamento) e contra o código real (claims verificados — ver §1.3).

---

## Índice dos sub-planos

- [01 — Backend: camada de serviço](23-plano-refatoracao-01-backend-camada-servico.md) — workstreams A2/A2b (caracterização) + B1–B6 + F2; decomposição de webhook/contacts/handler em services.
- [02 — Frontend: decomposição](23-plano-refatoracao-02-frontend-decomposicao.md) — workstream D0–D4; quebrar Contacts/ContactDetail/ChannelsManager sem build + slots.
- [03 — Plugins: eventos & extensibilidade](23-plano-refatoracao-03-plugins-eventos-extensibilidade.md) — §3 eventos de domínio + §4 costuras + workstream C0–C5.
- [04 — Dados, redundâncias & tooling](23-plano-refatoracao-04-dados-redundancias-tooling.md) — §5 redundâncias + A0/A1/G0/G1 (testes/CI) + E1/E2/E3 (repos/DB) + G2.

---

## 0. Objetivo e princípios de design

O WhatsBot é, na prática, um **modular monolith funcional e rico**, mas com a espinha dorsal de serviço **ausente** e uma **migração inacabada** (planos 11 conversa-cêntrico e 13 transportes-como-plugins) que dominou a saúde de três subsistemas (webhook, agente, repos). Os problemas são de **organização**, não de design fundamental: o event/filter bus, o RBAC, o audit trail, a camada de dados Core e a costura de slots/filtros/override de UI são todos **sólidos e já existem**. O que falta é:

- **(a)** dar ao core uma camada de serviço **fina e pragmática** nos domínios centrais (conversa/atendimento, mensageria, canal, agente);
- **(b)** **normalizar e relocar** a emissão de eventos de domínio (hoje emitidos da rota com payload de row crua) para a camada de serviço, com payload primitivo (DTO), **sem duplicar** os que já existem;
- **(c)** **consolidar** as costuras de extensão já existentes e **fechar só as lacunas que o plugin de atendimento demonstravelmente precisa hoje** — não construir um SDK gigante;
- **(d)** matar a duplicação e o dead code que congelaram regressões silenciosas.

### Princípios (não reabrir)

1. **Routes finas → app/services (casos de uso) → db/repositories (dados puros).** Uma camada de serviço pragmática. **SEM** uma camada `domain/` de entidades separada (as invariantes são finas: open/closed/reopened + janela 24h — moram como constantes/funções no topo do próprio service). Só sobrevive em `domain/` o que tem motivo concreto: `domain/events.py` (catálogo de eventos, lar neutro para quebrar ciclo db↔server) e `domain/permission_catalog.py` (quebra um ciclo de import real).
2. **Service só onde há workflow** (multi-repo, efeito colateral, reuso por >1 entry point, transação atômica). CRUD periférico de folha (tags, quick_replies, saved_filters) **fica leve no repo/rota**. Mas atenção: `inboxes`, `custom_attributes`, `conversation_labels` **NÃO são periféricos** — são estado de domínio de atendimento e entram no mapa de Conversa (§2.3).
3. **Frontend SEM build step.** ESM puro + HTM + import-map. Decompor god-components em módulos/hooks/services ESM. Type-check **opcional** dev-time via `tsc --checkJs` + JSDoc — **opt-in por arquivo** (`// @ts-check`), escopado aos módulos novos/extraídos e aos **contratos de costura** (slot ctx, filter value, event payload, allowlist de API), **não** ao projeto inteiro (evita parede de warnings em legado). Runner de teste JS: `node --test` sobre os módulos **puros** extraídos (sem DOM/Preact) — respeita "sem build step".
4. **`<400` linhas é teto soft, não cota.** Módulos coesos de 250–400 linhas são bem-vindos. O alvo é coesão, não fragmentação — não estilhaçar pipelines coesos em N micro-arquivos só para bater número.
5. **Consolidar costuras existentes, fechar lacunas do atendimento, base pronta pra expandir — sem SDK gigante.** Emitir um evento/filter/slot novo é uma mudança **aditiva** (MINOR semver) — custa ~2 linhas adicioná-lo depois. Logo: **zero custo em adiar** seams especulativos e **custo real** (contrato mantido pra sempre + não-refatorável-por-baixo) em enviar seams sem consumidor.
6. **Parallel Change com Contract obrigatório.** Todo *Expand* (emitir novo ao lado do velho) tem ticket de *Contract* agendado (velho vira listener do novo, fonte única). Velho+novo coexistindo pra sempre = pior que antes.

---

## 1. Decisões já tomadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|----------------------|
| D1 | Frontend **sem build step** (ESM + HTM + import-map) | Decompor em hooks/services ESM; `tsc --checkJs` opt-in dev-time; `node --test` só em módulos puros |
| D2 | Backend ganha **camada de serviço pragmática** (routes→services→repos) nos 4 domínios centrais | **UMA** camada de serviço; **sem** `domain/` de entidades; CRUD de folha fica leve |
| D3 | **Consolidar** costuras existentes (slots/filtros/override + events/filters), fechar só lacunas do atendimento | Catálogo de seams **cortado ao que o plugin usa hoje**; resto adiado (aditivo = grátis depois) |
| D4 | Rollout **agressivo** (não há produção), **mas seguro** | Verde a cada fase; caracterização ANTES de fluxo crítico; um refactor por commit |

### 1.1 Diagnóstico — God-files (medidos no código, não estimados)

| Arquivo | Linhas | Razões-de-mudança empilhadas |
|---|---:|---|
| `tests/test_endpoints.py` | **3805** | 56 `section()`, ~847 `check()`, 40 domínios, 30+ imports de internals privados |
| `server/routes/webhook.py` | **2027** | parse + classify + batch + transcrição + envio/split + broadcast + dispatch + **handler legado `/api/webhook` (fallback ativo)** |
| `web/static/js/components/contacts/Contacts.js` | **1849** | filtros + sidebar + 12 handlers WS + bulk + resize + deep-link |
| `web/static/js/components/contacts/ContactDetail.js` | **1754** | compositor + gravação áudio + upload + optimistic send + @menções + 8 tipos de bolha |
| `server/routes/contacts.py` | **1683** | 27 rotas + 9 helpers; HTTP + negócio + dados em closures |
| `agent/handler.py` | **1552** | tool registry + cliente OpenAI + transcrição + prompt + loop LLM (sync+async) + roteamento + CRUD |
| `web/static/js/components/ChannelsManager.js` | **1447** | 18 componentes/funções num módulo, 3 forms gigantes |
| `server/routes/channels.py` | **721** | 18 endpoints + templates + janela 24h + 7 repos |
| `db/repositories/contact_repo.py` | **693** | CRUD + unread (8 fns) + observações + search + SQL `.format()` + N+1 |
| `server/app.py` | **664** | 8 responsabilidades no `create_app`/`lifespan` |
| `agent/memory.py` | **564** | estado + broadcasts WS + lifecycle notices (já via lazy import) |
| `agent/agno_engine.py` | **505** | **god-file omitido do rascunho** — motor AGNO sync+async + reply-extraction |

### 1.2 Mistura de responsabilidades (SRP)

Em **todos** os domínios centrais, as três camadas (HTTP, negócio, dados) estão **fundidas**. As rotas (`webhook.py`, `contacts.py`, `conversations.py`, `channels.py`) fazem validação HTTP **+** decisão de canal/janela 24h/sandbox **+** orquestração de envio **+** `asyncio.to_thread(repo)` direto **+** broadcast WS **+** emit de evento. O `AgentHandler` é um God Object (~46 métodos). Os repos "ricos" (`contact_repo`, `conversation_repo`) embutem **regras de lifecycle** (limpar assignee ao fechar, reatribuir IA) cujo *evento* é montado **fora**, na rota. Nenhuma regra crítica é testável isoladamente sem subir o FastAPI inteiro.

### 1.3 Premissas FALSAS do rascunho corrigidas (verificadas no código)

> Estas correções são **bloqueantes** — executar o rascunho como estava quebraria produção.

| # | Claim do rascunho | Realidade verificada | Correção no plano |
|---|---|---|---|
| **C-1** | `process_message` sync + `run_sync` são "dead code, zero chamadores" → DELETAR (B5/R18) | **FALSO.** `server/routes/sandbox.py:57` chama `agent_handler.process_message(...)`; `generate_improvement` (`handler.py:1370`) usa cliente **sync** (`_get_client`) | Não é deleção grátis. É **migração de caller** (alto risco): migrar sandbox p/ `aprocess_message`, decidir isolar cliente sync de `generate_improvement` em `llm_clients.py`, **só então** remover. Métrica de dead-code corrigida (não conta sync loop). |
| **C-2** | handler legado `@app.post("/api/webhook")` = "~850 linhas dead code" → DELETAR (F2/R19) | **FALSO.** É **fallback ativo intencional**, auth-exempt (`app.py` `_AUTH_EXEMPT_EXACT`). `main.py:60`/`dev.py:51` apontam GOWA p/ `/api/webhook/gowa/default` (vivo), mas o legado fica registrado p/ rollback. O legado ainda contém o **batch orchestrator + reply pipeline vivos** nos quais o caminho genérico delega | Reenquadrar F2 de "deletar dead code" para **"aposentar fallback ativo"**: spike de medição da fronteira viva/legado + paridade de `chat.archived`/echo-audio **implementada e caracterizada** no caminho novo ANTES de remover. Substituir "~850" pelo número **medido**. |
| **C-3** | "Adicionar priority no registry.js (gotcha SlotFill: ordena por carga)" (§4.1) | **FALSO.** `registry.js` **já tem** `priority` (linhas 21–22, 38–42, 71–75) + `emit`/`on` (98–104) + `overrideRoute` (82) | D1 reescrito: priority já existe. O trabalho real é (a) **adicionar slots novos nos pontos de render**, (b) fazer o core **realmente chamar `registry.emit`** (`ui.conversation.*` — hoje ninguém emite), (c) trocar `...coreApi` por allowlist, (d) consolidar `overrideRoute`+`ModalHost` (seams existentes ignorados pelo rascunho). |
| **C-4** | "Eventos de domínio de conversa no bus: 0 → 12" (§3.1, métrica 7.1) | **FALSO.** `conversations.py` `_broadcast(deps, ws_event, bus_event, conv)` (linha 31) **já** faz `emit_with_filter`. `KNOWN_EVENTS` (`plugins/events.py:64-65`) **já** registra `conversation.created/.status_changed/.assigned/.archived/.ai_toggled/.updated/.deleted` + `contact.ai_toggled` | C1 reenquadrado de "introduzir 12" para **"normalizar payload (DTO, não row) + relocar emit p/ service pós-commit + adicionar ~5 verbos faltantes"**. Métrica vira tabela "já emitido? / onde / payload atual". **Risco crítico:** emitir de novo sem relocar = **dupla emissão** (regressão que o plano deveria prevenir). |

### 1.4 Subsistemas acoplados ao bus que o rascunho ignorou (mapear ANTES de mexer)

| Subsistema | Arquivos | Por que importa pro refactor |
|---|---|---|
| **Audit trail (plano 07)** | `server/audit_listener.py`, `server/audit_context.py`, `db/audit_actions.py`, `server/routes/audit.py` | Subscreve **o mesmo bus** via `AUDITABLE_EVENTS` (allowlist por `event_name`, lê **chaves específicas do payload** p/ `_resource_id`). Relocar emit (B4/B5/C1) ou trocar payload row→DTO **pode dropar o resource_id e parar de auditar silenciosamente**. Bônus: `AUDITABLE_EVENTS` tem **divergência de nomes** (`contact.toggle_ai` vs bus `contact.ai_toggled`; `tag.create` vs `tag.created`) → entra no mapa de redundâncias. **Compliance:** export de dados é auditado (`audit.py`). |
| **AI engine config-in-DB** | `server/routes/ai_engine.py` (303L, 24 handlers), `agent/agent_factory.py` (239L), `agent/agno_engine.py` (505L) | **Superfície de execução de código**: `ai_tools` guarda Python arbitrário executado quando `ai_tools_code_enabled=True` (kill-switch P62). Decompor `AgentHandler` (B5) + `filter.agent.resolve` interagem com o agente montado-do-DB. O kill-switch + seus testes são **caracterização-crítica** antes de tocar o stack do agente. |
| **Execuções (tracking)** | `server/execution.py`, tabelas `executions`/`execution_steps`, `track_step` (espalhado em `handler.py`) | Todo service da Wave 2 que "broadcast_and_emit pós-commit" precisa **threadar `execution_id`** ou a população de `execution_steps` quebra silenciosamente (ímã de regressão como `chat.archived`). |
| **Migração Postgres** | `db/migration_postgres.py` | Copia row-a-row via Core e recusa destino não-vazio. Qualquer mudança de serialização (E1) precisa de **round-trip test SQLite→PG**. |
| **WS layer (backend)** | `server/routes/websocket.py`, `ws_manager.broadcast` injetado no startup | ~30 nomes de evento WS que o front faz switch. Services não podem `import server.app` p/ broadcast → precisam de uma **porta** (interface) injetada. |

### 1.5 Lacunas core↔plugin REAIS (o que o plugin `atendimentos` v1.7.0 precisa e não tem)

O plugin já usa, hoje: **4 slots** (`gear.menu.items`, `conversation.info.panel`, `conversation.header.actions`, `attendances.toolbar`), **1 filtro client** (`filter.conversation.beforeResolve`), **`overrideRoute('attendances', …)`** (registry.js:82 — first-wins, EXCLUSIVO, é o mecanismo **primário**, ignorado pelo rascunho), **`ModalHost.openModal`** (modal async do `resolve_form`), e os events/filters técnicos de backend.

**Faltam — e é o coração deste plano, cortado ao essencial:**
1. **~5 verbos de evento de conversa genuinamente ausentes** do bus: `reopened`, `unassigned` (distinto de assigned), `transferred_to_human`, `agent_changed`, `attribute_set`, `ai_takeover` (existe como dedupe interno, não como evento de bus). + **normalizar payload** dos ~7 que já existem (row→DTO) + **relocar** p/ service pós-commit.
2. **Filtros de pré-ação** que o atendimento usa de fato: `filter.conversation.assignment` (round-robin no `transfer_to_human`) e `filter.conversation.before_assign`. Os demais (before_archive/before_attributes/etc.) **adiados** (aditivos).
3. **Slots de UI no fluxo principal** que o atendimento pediria: começar com **2** (`sidebar.row.badges`, `chat.header.banner`); os outros 3 adiados.
4. Compatibilidade de `overrideRoute('attendances')`/`ModalHost` com a decomposição de `Contacts.js`/`ContactDetail.js` (D2/D3) — **documentar como o plugin compõe vs. sobrepõe** ou D2/D3 quebram o claim de rota silenciosamente.

---

## 2. Arquitetura-alvo

### 2.1 Backend — antes → depois

**ANTES (atual):**
```
server/routes/webhook.py (2027)  ──┐
server/routes/contacts.py (1683) ──┤  HTTP + negócio + dados + broadcast + emit
server/routes/conversations.py   ──┤  tudo em closures de register_routes;
server/routes/channels.py (721)  ──┤  asyncio.to_thread(repo) direto;
agent/handler.py (1552, God)     ──┤  eventos emitidos da ROTA com payload de row crua
                                   └──→ db/repositories/*  (com regra de negócio embutida)
```

**DEPOIS (alvo — UMA camada de serviço, sem `domain/` de entidades):**
```
server/
  deps.py                      ← wiring: get_*_service(), require_permission(), get_channel_or_404()
  routes/                      ← FINAS: validar HTTP → Depends(service) → DTO → _ok/_err. Zero query, zero regra.
    webhook.py                 ← registra rota genérica + delega ao MessageIngestService
    contacts_crud.py · contacts_io.py · messaging.py
    conversations.py · conversation_templates.py · channels.py

app/services/                  ← CASOS DE USO (orquestram repos + emitem eventos pós-commit + broadcast)
  messaging_service.py         ← UM módulo: _run_batch, _send_reply, _send_media, resolve_channel,
                                  session_window_guard, sandbox, persist_outbound, broadcast_and_emit,
                                  error_bubble  (split em submódulos só quando surgir 2º caller — §6 B3)
  message_ingest_service.py    ← dedup, contato channel-aware, echo-suppression, before_save, received/saved
  event_actions_service.py     ← InboundEvent não-mensagem (reaction/revoke/roster/ack), CHANNEL-AWARE (GOWA-scoped)
  conversation_service.py      ← create/resolve_for_contact_ex, set_status, archive, attributes +
                                  SEÇÃO de posse: assign()/set_ai()/set_agent() c/ _transfer() helper
                                  (regras de transição como VALID_TRANSITIONS no topo — SEM domain/conversation.py)
  agent_run_service.py         ← build_spec → run_turn → routing hop → record usage (absorve aprocess_message)
  channel_service.py · template_service.py · provisioning_service.py
  improvement_service.py · contact_import.py / contact_export.py

domain/                        ← MÍNIMO (só o que quebra ciclo de import)
  events.py                    ← catálogo tipado de domain events (dataclasses) + emit_domain()
  permission_catalog.py        ← PERMISSION_CATALOG (move de server/) — quebra ciclo db→server

agent/                         ← AgentHandler decomposto em ~4 módulos coesos (não 7 — ver §2.3)
  handler.py (fachada fina) · tool_registry.py · llm.py (clients+encoder+transcriber) · prompt_builder.py
db/repositories/               ← DADOS PUROS (sem regra, sem to_thread, sem lazy-import de política)
  _mapping.py (row_to_dict / coerce_json / media_preview) · unread_repo · observation_repo
  contact_query · conversation_query · db/search/contact_search.py
server/
  bootstrap/{plugins,channels,ai_engine}.py · lifespan.py · http/{middleware,spa}.py
  ports.py                     ← BroadcastPort (interface p/ ws_manager) — services não importam server.app
plugins/runtime.py             ← PluginRuntime único (mata 8 globais de context.py)
```

### 2.2 Domínio + eventos (o vetor de plataformização) — SEM camada de entidades

A regra de transição é **fina** e mora no próprio service:

```python
# app/services/conversation_service.py  (topo do arquivo — sem domain/conversation.py)
VALID_TRANSITIONS = {"open": {"closed"}, "closed": {"open"}, "reopened": {"closed"}}

# domain/events.py — payload tipado, primitivo, NUNCA a row crua
@dataclass(frozen=True)
class ConversationAssigned:
    conversation_id: int; contact_id: int; phone: str
    assignee_user_id: int | None; previous_assignee: int | None
    actor: str | None; ts: float

EVENT_NAME = {ConversationAssigned: "conversation.assigned", ...}

def emit_domain(evt) -> None:            # wrapper de emit_with_filter
    emit_with_filter(EVENT_NAME[type(evt)], asdict(evt))
```

Cada transição: (1) valida (constante no service), (2) persiste via repo num escopo transacional, (3) **emite o domain event APÓS o commit** com payload **DTO**. `system_notices` **e** o audit listener **e** os plugins viram subscribers do mesmo evento — não call sites paralelos. **Mas:** ao normalizar payload (row→DTO), garantir que `AUDITABLE_EVENTS._resource_id` ainda resolve (§1.4) — teste de caracterização do audit row pareado.

### 2.3 `agent/handler.py` (1552) — antes → depois (4 módulos, não 7)

| Hoje (God Object) | Vira |
|---|---|
| tool registry (`_register_tool`, `_dispatch_tool`, …) | `agent/tool_registry.py` |
| `transcribe_audio/describe_image/transcribe_document` + `_get_client/_get_async_client/_record_usage*` + encoder de mensagem/imagem | `agent/llm.py` (clients + transcriber + encoder coesos) |
| `_build_system_prompt` (~140 linhas PT-BR) | `agent/prompt_builder.py` (seções nomeadas + `filter.system_prompt`) |
| `aprocess_message` + `_continue_routing/_run_routing_hop` | `app/services/agent_run_service.py` |
| `process_message` (sync) + `run_sync` | **MIGRAR callers primeiro** (sandbox, improvement) — depois remover (ver C-1) |
| `generate_improvement` | `app/services/improvement_service.py` (decidir async vs cliente sync isolado) |
| (restante: config, cache ContactMemory, delega) | `AgentHandler` = **fachada fina** |

`agent_run_service` deve **reconciliar os dois caminhos de agente**: `Agent` único estático **vs.** `ai_agents` config-in-DB (`agent_factory`, gate `ai_engine_enabled`). `filter.agent.resolve` e o `prompt_builder`/`tool_registry` decompostos precisam funcionar nos dois. **Kill-switch P62 (`ai_tools_code_enabled`) é caracterização-crítica antes de B5.**

### 2.4 `agent/memory.py` (564) — desacoplar (nice-to-have, NÃO conserto de ciclo)

> Correção: o "ciclo agent→server" **já está mitigado** por lazy import (`memory.py:193,246,267,292`). Não vender como conserto urgente — vender como **fonte única de evento**.

`add_message` é o **hot path universal** (toda mensagem salva, inbound + AI). Trocar broadcast/notice inline por listeners de um evento `message.persisted` é **Wave 3, com golden de caracterização** cobrindo o ramo AI-save e o dedupe de `ai_takeover` ANTES. `_build_image_content`/marcação de roles → `agent/llm.py`. `_custom_attr_lines` → `prompt_builder`. `TagRegistry` → `agent/tag_registry.py`.

### 2.5 Frontend — antes → depois (sem build step)

**ANTES:** 3 god-components ESM (Contacts 1849 / ContactDetail 1754 / ChannelsManager 1447) com estado+negócio+HTTP+render inline. `api.js` (1003) god-service. **Seams já existentes ignorados:** `overrideRoute`, `ModalHost`, `priority` no registry.

**DEPOIS (ESM puro; container/presentational + hooks + services agrupados POR DOMÍNIO):**
```
web/static/js/
  hooks/
    useConversationFilters · useSidebarRows · useConversationRealtime · useChatSelection
    useBulkConversationActions · useSidebarResize
    useComposer · useAudioRecorder · useMediaUpload · useTokenAutocomplete (@menção + /quick-reply) · useMessageActions
  services/                                ← por DOMÍNIO, não por função
    contacts.js · conversations.js · channels.js · tags.js        ← fatiar api.js
    httpClient.js (request + uploadRequest, trata 401 1×)         ← mata wrapper duplicado
    conversationRows.js (buildRows/shapeConvData/clauseMatches — PURO, node --test)
    messages.js (mergeIncoming/isDuplicate/mediaPreviewLabel — PURO, mata dedup duplicado)
    conversationPatch.js (applyConversationEvent — Contacts↔Attendances)
    wsBus.js (singleton pub/sub — N sockets → 1)
  utils/phone.js (formatPhoneDisplay canônico — mata 8 cópias, 2 divergentes)
  components/
    contacts/Contacts.js (container fino) · MessageBubble · SystemMessageCard (data-driven, corrige cores cruas)
            · MediaContent · Composer
    channels/ (ChannelForm, ChannelEditForm, AiSettingsFields, QRConnect, ChannelCard, constants.js)
    ScreenRouter · GearMenu · AuthGate · routes/screenRegistry.js
  plugins/ (registry.js, Slot.js, api.js, ModalHost.js — CONSOLIDADOS: priority JÁ existe;
            allowlist PLUGIN_SERVICES; overrideRoute+ModalHost documentados como contrato)
jsconfig.json (checkJs OFF global; opt-in `// @ts-check` por módulo novo + contratos)
```

---

## 6. Roadmap — visão consolidada

**Workstreams:** **A**=Fundações/Testes · **B**=Backend Serviço · **C**=Plataformização · **D**=Frontend · **E**=Repos/DB · **F**=Plugins-core/Channels · **G**=Tooling.

Regra global: **verde a cada fase**; **caracterização ANTES** de fluxo crítico; **um refactor por commit**; nunca avançar com golden vermelho não-explicado. 🟢 = PODE AGRUPAR · 🔴 = FAÇA SOZINHA.

### Resumo do roadmap (dependências)

```
WAVE 0  A0─A1─G0─D0─G1min · C0(Expand antecipado: 5 verbos → destrava atendimento já)
        A2(BLOQUEIA F2/B3) · A2b(BLOQUEIA B4/B5/C1)
           │
WAVE 1  B1(R16 c/ RBAC char) │ B2 │ E1(JSONText CORTADO)
           │
WAVE 2  F2(spike+paridade ANTES) → B3(1 módulo) → B4(posse=seção) → B5(migra sync ANTES) · B6(evento próprio)
        [filtros de pré-ação nascem AQUI, no commit do service]
           │
WAVE 3  C1(normaliza+reloca, 1×/ação) ─ C2 · C3 · C4(alias qr) → C5(CONTRACT: WS vira listener)
           │
WAVE 4  D1(slots novos, priority já existe) → D2 → D3(último/conservador) · D4
           │
WAVE 5  E2 · E3(FK) · G2
```

### Todas as fases (one-liner + onde está detalhada)

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Sub-plano |
|---|---|---|---|---|---|
| 0 | A0 — pytest + harness único + CI | A (Tooling) | 🔴 | baixo | [04](23-plano-refatoracao-04-dados-redundancias-tooling.md) |
| 0 | A1 — `tests/fakes.py` + dividir `test_endpoints.py` | A (Tooling) | 🟢 | baixo | [04](23-plano-refatoracao-04-dados-redundancias-tooling.md) |
| 0 | G0 — Launchers + pin de deps + dead-config | G (Tooling) | 🟢 | baixo | [04](23-plano-refatoracao-04-dados-redundancias-tooling.md) |
| 0 | D0 — Helpers front puros + allowlist congelada + type-check opt-in | D (Frontend) | 🟢 | baixo | [02](23-plano-refatoracao-02-frontend-decomposicao.md) |
| 0 | C0 — Emitir os 5 verbos faltantes das rotas atuais (Expand antecipado) | C (Plataformização) | 🟢 | baixo | [03](23-plano-refatoracao-03-plugins-eventos-extensibilidade.md) |
| 0 | A2 — Caracterização do pipeline crítico (BLOQUEANTE) | A (Testes) | 🔴 | baixo | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 0 | A2b — Caracterizações dedicadas dos outros fluxos críticos (BLOQUEANTE) | A (Testes) | 🔴 | baixo | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 0 | G1-mínimo — `build_test_app(plugins=[...])` | G (Tooling) | 🟢 | baixo | [04](23-plano-refatoracao-04-dados-redundancias-tooling.md) |
| 1 | B1 — `server/deps.py`: DI reutilizável | B (Backend) | 🔴 | médio | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 1 | B2 — Helpers backend puros | B (Backend) | 🟢 | baixo | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 1 | E1 — `db/repositories/_mapping.py` + mover catálogo | E (Repos/DB) | 🔴 | médio | [04](23-plano-refatoracao-04-dados-redundancias-tooling.md) |
| 2 | F2 — Aposentar fallback `/api/webhook` + `event_actions_service` | F (Plugins-core/Channels) | 🔴 | alto | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 2 | B3 — `messaging_service` (UM módulo) + `message_ingest_service` | B (Backend) | 🔴 | alto | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 2 | B4 — `conversation_service` (posse como SEÇÃO) | B (Backend) | 🔴 | médio-alto | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 2 | B5 — `agent_run_service` + decompor `AgentHandler` (4 módulos) | B (Backend) | 🔴 | alto | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 2 | B6 — `channel_service` + `template_service` + `provisioning_service` | B (Backend) | 🟢 | médio | [01](23-plano-refatoracao-01-backend-camada-servico.md) |
| 3 | C1 — Normalizar payloads + relocar emit + `domain/events.py` | C (Plataformização) | 🔴 | médio | [03](23-plano-refatoracao-03-plugins-eventos-extensibilidade.md) |
| 3 | C2 — `KNOWN_FILTERS` + documentação de contrato | C (Plataformização) | 🟢 | baixo | [03](23-plano-refatoracao-03-plugins-eventos-extensibilidade.md) |
| 3 | C3 — `system_notices` extensível + eventos de canal | C (Plataformização) | 🟢 | baixo-médio | [03](23-plano-refatoracao-03-plugins-eventos-extensibilidade.md) |
| 3 | C4 — Consolidar `plugins/` core | C (Plataformização) | 🟢 | médio | [03](23-plano-refatoracao-03-plugins-eventos-extensibilidade.md) |
| 3 | C5 — Contract: broadcast WS vira listener do domain event | C (Plataformização) | 🔴 | médio | [03](23-plano-refatoracao-03-plugins-eventos-extensibilidade.md) |
| 4 | D1 — Slots novos + emissão `ui.*` + allowlist (priority já existe) | D (Frontend) | 🔴 | baixo-médio | [02](23-plano-refatoracao-02-frontend-decomposicao.md) |
| 4 | D2 — Decompor `Contacts.js` (1849) | D (Frontend) | 🔴 | médio | [02](23-plano-refatoracao-02-frontend-decomposicao.md) |
| 4 | D3 — Decompor `ContactDetail.js` (1754) | D (Frontend) | 🔴 | médio | [02](23-plano-refatoracao-02-frontend-decomposicao.md) |
| 4 | D4 — `ChannelsManager.js` (1447) + `app.js` (906) | D (Frontend) | 🟢 | médio | [02](23-plano-refatoracao-02-frontend-decomposicao.md) |
| 5 | E2 — Decompor `contact_repo.py` (693) + `conversation_repo.py` | E (Repos/DB) | 🟢 | médio | [04](23-plano-refatoracao-04-dados-redundancias-tooling.md) |
| 5 | E3 — FK / Alembic estrutural | E (Repos/DB) | 🔴 | médio | [04](23-plano-refatoracao-04-dados-redundancias-tooling.md) |
| 5 | G2 — Fixtures de teste para plugins (completo) | G (Tooling) | 🟢 | baixo | [04](23-plano-refatoracao-04-dados-redundancias-tooling.md) |

---

## 7. Estratégia de testes e métricas de sucesso

### 7.1 Estratégia de testes (por tipo)

1. **Caracterização (golden master)** — ANTES de F2/B3/B4/B5/D2/D3. Printer normaliza `ts`/`msg_id`/latência/ordem. Documenta comportamento **real** (incl. bugs); corrige depois como mudança **visível no diff**.
2. **Caracterização de SEQUÊNCIA + CONJUNTO de eventos/filtros** (A2/A2b) — protege contra mudança silenciosa de ordem ao mover lógica (fan-out usa `create_task`, sem ordem garantida). **Checklist de ramos** explícito p/ webhook (classify × media × echo × split) — "golden missed a branch" não pode acontecer.
3. **Caracterização dos subsistemas-acoplados** — audit row por ação auditável; `executions/execution_steps` 1×/turn; kill-switch P62; agno reply-extraction (split on/off); group_mentions (incl. `@todos→@everyone`). **Bloqueantes** p/ B4/B5/C1/F2.
4. **Service tests (unidade)** — pós-extração, via `dependency_overrides` sem GOWA/LLM. Substituir os 30+ imports de internals privados por testes de superfície pública/HTTP.
5. **Repo tests** — caracterizar shape de saída antes de `_mapping.py`.
6. **Frontend** — `node --test` em módulos **puros** (`conversationRows`, `messages`, `phone`); `tsc --checkJs --noEmit` opt-in como compilador-rede nos módulos extraídos e nos **contratos de costura**. Render/optimistic-send: smoke manual (sem runner de comportamento) → D3 conservadora.
7. **Contrato de eventos/filtros** — cada domain event: emitido pós-commit, payload primitivo, subscriber recebe, **1×/ação**; não-regressão de `KNOWN_EVENTS`/`KNOWN_FILTERS`; `AUDITABLE_EVENTS` resolve resource_id.
8. **Migração PG** — round-trip SQLite→PG para qualquer mudança de coluna (E1/E3).
9. **Hermético + CI** — `data_dir`/`plugins_dir` tmp; `raise_server_exceptions=True`; PR bloqueia em falha; matrix SQLite + Postgres; CI de hygiene Alembic (1 head, sem prefixo duplicado).

### 7.2 Disciplina de Parallel Change

Cada *Expand* (C0: emitir verbo novo; C1: emitir DTO ao lado) **tem o ticket de fechamento C5** (broadcast WS vira listener, fonte única). Não deixar velho+novo indefinidamente — "se a contração não roda, você termina pior do que começou" (Fowler). **Rollback agressivo:** como F2 aposenta um fallback e B5 migra o sync, o checklist de ramos de A2 + o golden são a rede; opcional `WHATSBOT_NEW_PIPELINE` flag de vida-curta gateando MessageIngest/Messaging por uma release se um golden faltar um ramo.

### 7.3 Métricas de sucesso

| Métrica | Baseline | Alvo |
|---|---|---|
| Maior arquivo backend (linhas) | 3805 / 2027 | < 400 por módulo (teto soft) |
| Maior componente frontend | 1849 | < 400 (container) |
| Dead/fallback removido (legado webhook, **medido**; sync após migrar callers) | **a medir** (spike F2) | número medido, não "~1040" |
| `formatPhoneDisplay` / harness `check()` | 8 / 15 | 1 / 0 (pytest) |
| Acesso a `agent_handler._get_contact/_contacts` das rotas | 39× | 0 (API pública) |
| **Verbos de conversa faltantes** no bus | 5 ausentes (+7 já existem) | 0 ausentes; **payload DTO**; emit no service; **1×/ação** |
| Dupla-emissão de evento de conversa | 0 | 0 (não regredir!) |
| Slots de UI novos no fluxo principal | (priority/override/modal já existem) | +2 (`sidebar.row.badges`, `chat.header.banner`) |
| Filtros de pré-ação de conversa | 1 (inline na rota) | 3 (assignment, before_assign, before_status) no service |
| `...coreApi` leak (fns sensíveis) | ~120 expostas | allowlist curada + deny-list (users/roles/admin) |
| Sockets WS simultâneos | 3-4 | 1 (singleton) |
| Audit trail sob refactor | acoplado, frágil | resource_id resolve pós-DTO (caracterizado) |
| Cobertura: caracterização webhook + lifecycle + agent + audit + executions | 0 | golden verde, **bloqueantes** |
| CI de testes + hygiene Alembic | inexistente | pytest gate de PR (SQLite+PG) + 1 head / sem prefixo dup |

---

## 8. Perguntas em aberto

1. **`generate_improvement`:** migrar para cliente **async** (uniformiza, mata o sync) ou manter um cliente sync **isolado** em `agent/llm.py` (menos churn)? Decisão fecha o destino do path sync em B5.
2. **F2 spike:** qual o número **real** de linhas alcançáveis só pelo legado `/api/webhook`? E o fallback deve ser **removido** ou mantido atrás de flag por uma release (rollback)? Define o risk/escopo de F2.
3. **`ai_engine` config-in-DB:** `agent_run_service` reconcilia os dois caminhos num só fluxo, ou mantém dois com `filter.agent.resolve` escolhendo? Impacta a forma de `prompt_builder`/`tool_registry`.
4. **PII no bus:** payloads de domain event carregam `phone`/`contact_id` a plugins de 3º. Gating por-permissão da entrega do bus é v1 ou futuro? (proposta: futuro — não bloqueia atendimento).
5. **`overrideRoute('attendances')` × D2/D3:** o plugin **sobrepõe a rota inteira do chat** ou **compõe via slots**? Definir antes de decompor `ContactDetail.js` (senão quebra silencioso).
6. **`channel.status_changed` em B6 ou C3:** introduzir o evento mínimo já em B6 (p/ invalidação de cache) ou manter chamada direta até C3? (proposta: evento mínimo em B6 p/ não referenciar seam inexistente).
7. **Telegram/Cloud inbound:** confirmar que ambos já emitem `InboundEvent` normalizado, ou marcar a consolidação multi-canal como trabalho futuro (esta rodada é GOWA-scoped)?
8. **Runner de comportamento frontend:** aceitar smoke manual para render/optimistic-send (D3), ou investir num runner DOM headless sem build step (ex.: `node --test` + happy-dom) — vale o custo?
