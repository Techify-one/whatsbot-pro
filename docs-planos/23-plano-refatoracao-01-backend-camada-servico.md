# Plano 23 · Sub-plano 01 — Backend: camada de serviço

> Parte do [Plano 23 — Mestre](23-plano-refatoracao-00-mestre.md). Arquitetura-alvo backend: ver §2.1–§2.4 no mestre.

## Fases (workstream A-caracterização + B + F2)

### WAVE 0 (caracterização)

#### Fase A2 — **CARACTERIZAÇÃO do pipeline crítico** 🔴 (BLOQUEANTE)
- **Objetivo:** golden master de `POST /api/webhook/gowa/default → ingest → batch → reply` ANTES de qualquer extração de mensageria.
- **Capturar** (Printer normaliza `ts`/`msg_id`/latência/ordem não-determinística): (a) `messages` gravados, (b) args de `send_message`, (c) **conjunto + sequência** de eventos/filtros disparados. **Checklist de ramos obrigatório** (cobre o "golden missed a branch"): `classify_jid` (person/person_lid/group/newsletter/broadcast) × cada media type × echo × group_no_mention × `split_messages` on/off × transcrição.
- **Arquivos:** `tests/endpoints/test_webhook_characterization.py` + `tests/fakes.py`.
- **Risco:** baixo (só escreve teste). **Bloqueia F2, B3.**

#### Fase A2b — **Caracterizações dedicadas dos outros fluxos críticos** 🔴 (BLOQUEANTE p/ B4/B5/C1)
- **Objetivo:** os endpoints HTTP de A1 (asserts de status) **não** protegem sequência de eventos. Capturar goldens de SEQUÊNCIA:
  - **lifecycle** (assign→close limpa assignee; reopen reatribui; ai_toggle conversa vs contato; cada evento dispara **1×**) → bloqueia B4 + C1;
  - **agent-turn** (`aprocess` → tool_calls → `ai_takeover` dedupe → routing hop → usage record) → bloqueia B5;
  - **agno reply-extraction** (`_extract_reply` última msg assistant sem tool_calls; `split_messages` JSON-array on/off — "crítico" no CLAUDE.md);
  - **group_mentions** (`resolve_incoming`/`resolve_outgoing` incl. `@todos→@everyone`);
  - **executions/execution_steps** (1 row por turn) → ímã de regressão silenciosa;
  - **audit trail** (evento → audit row, com resource_id) → bloqueia B4/B5/C1;
  - **kill-switch P62** (`ai_tools_code_enabled` OFF não executa código do DB) → bloqueia B5;
  - **sandbox + generate_improvement** (golden reply/tool_calls/usage) → bloqueia a migração de caller de C-1.
- **Risco:** baixo (só testes).

### WAVE 1

#### Fase B1 — `server/deps.py`: DI reutilizável 🔴
- **Objetivo:** `require_permission(key)`, `get_channel_or_404`, `get_*_service()` (Depends) + `dependency_overrides` nos testes. `server/ports.py` com `BroadcastPort` (services não importam `server.app`).
- **Aplicar incrementalmente** em `ai_engine.py`, `admin.py`, `plugins.py` (R16). **NÃO** mexer em contacts/conversations ainda.
- **R16 NÃO é baixo risco** — muda o caminho de autorização (default-allow em legado). **Exigir** caracterização de RBAC verde (rotas 403/200 com/sem usuário; seam `filter.authz.decision` ainda aplicado), troca **mecânica 1-a-1**, não em lote. **Manter commit separado de B2** (blast radius/bisect).
- **Risco:** médio.

#### Fase B2 — Helpers backend puros 🟢 (commit separado de B1)
- **Objetivo:** R3/R4/R5/R6/R7/R20/R-aud — `error_bubble.py`, `format_media_content()`, `apply_message_filter()`, `phone_from_ack_payload()`, `usage_repo` shape, `system_notices.emit_for_contact()`, reconciliar nomes `AUDITABLE_EVENTS`. Remover imports mortos (`emit as emit_event`, `variable_repo`, `ALL_TOOLS`).
- **Risco:** baixo (cada helper é Sprout isolado).

### WAVE 2

> Todas dependem de **A2/A2b** verdes. **Branch by Abstraction:** service nasce como abstração, rota delega, lógica migra com golden verde a cada passo. **Filtros de pré-ação nascem NO commit do service** (§4.2).

#### Fase F2 — **Aposentar fallback `/api/webhook` + `event_actions_service`** 🔴
- **Objetivo (reenquadrado — NÃO "deletar dead code"):**
  1. **Spike de medição (meio dia):** confirmar em runtime qual handler o GOWA atinge e tracejar quais funções de `webhook.py` são alcançáveis **só** pelo legado `@app.post("/api/webhook")` vs. compartilhadas pelo caminho genérico. **Substituir "~850" pelo número medido.**
  2. **Paridade ANTES de remover:** implementar + caracterizar no caminho VIVO as regressões congeladas que hoje só existem no legado — `chat.archived` (em `_apply_contact_metadata`) e transcrição de áudio de echo (em `_ingest_echo`).
  3. Extrair `event_actions_service.py` (set_reaction/mark_revoked/roster) **channel-aware** (passar `channel_id` ao `_get_contact` — corrige bug multicanal). Unificar `_resolve_presence_conv_id`. **Escopo GOWA** (telegram/cloud têm inbound próprio — ver F-nota).
  4. **Só então** remover o handler exato + sua exempção em `app.py` `_AUTH_EXEMPT_EXACT`. Confirmar que nenhum provider/doc instrui POST em `/api/webhook` exato.
- **Caracterização antes:** A2 + paridade `chat.archived`/echo-audio.
- **Risco:** **alto** (fluxo crítico). Gate em spike + A2 verde.

#### Fase B3 — `messaging_service` (UM módulo) + `message_ingest_service` 🔴
- **Objetivo:** extrair de `webhook.py` (enxuto pós-F2) e `contacts.py` o pipeline de saída reutilizável **como UM módulo** com funções internas (`_run_batch`, `_send_reply`, `_send_media`, `resolve_channel`, `session_window_guard`, sandbox, `persist_outbound`, `broadcast_and_emit`, `error_bubble`). R14 (mídia ×3) cai aqui. **NÃO** estilhaçar em 3 services preemptivamente — promover um submódulo só quando ganhar **2º caller** (ex.: operator-send + AI-reply compartilharem `_send_media`). Deixar crescer a ~400-500L primeiro.
- **`broadcast_and_emit`:** **lift/generalizar** o `_broadcast` que já existe em `conversations.py` (R-bc), não greenfield. Threadar `execution_id` (§1.4).
- **`server/state.py` → `MessagingState`** encapsulado, chave `(channel_id, phone)`.
- **Caracterização antes:** A2 + `contacts.send/send-image/send-audio/send-document`.
- **Risco:** alto.

#### Fase B4 — `conversation_service` (posse como SEÇÃO, não service à parte) 🔴
- **Objetivo:** mover lifecycle de `conversations.py`/`contacts.py`/repos p/ o service. **Unificar** `set_ai`/`assign_agent`/`toggle_contact_ai` (política divergente) num `_transfer()` helper **dentro de `conversation_service`** — **NÃO** um `conversation_ownership_service` separado (são ~3 funções, 1 agregado = 1 service). `VALID_TRANSITIONS` no topo. Repo vira dados puros (política de defaults sai do repo → mata lazy-imports). **Inserir aqui** `filter.conversation.before_assign` + relocar `filter.conversation.before_status` (já existe inline). Emitir DTOs (C1 normaliza junto).
- **Domínios que entram aqui (não "periféricos"):** `inboxes` (alvo de roteamento de `conversation.created`), `custom_attributes` (= `attribute_set`), `conversation_labels` (sidebar/kanban do atendimento). `saved_filters`/`quick_replies`/`tags` ficam CRUD-leaf.
- **Caracterização antes:** A2b lifecycle + audit row.
- **Risco:** médio-alto.

#### Fase B5 — `agent_run_service` + decompor `AgentHandler` (4 módulos) 🔴
- **Objetivo:** §2.3 — extrair `tool_registry`, `llm.py` (clients+transcriber+encoder), `prompt_builder`; `agent_run_service.run_turn()`. **Reconciliar** caminho `Agent` estático vs. `ai_agents` config-in-DB (`agent_factory`). Inserir `filter.agent.resolve` + `filter.conversation.assignment` (round-robin no `transfer_to_human`). **Migrar callers do sync** (sandbox → `aprocess_message`; `generate_improvement` → decidir async ou cliente sync isolado em `llm.py`) **antes** de remover `process_message`/`run_sync` (C-1). Renomear "OpenRouter"→"Techify" nas mensagens visíveis.
- **Caracterização antes:** A2b agent-turn + agno reply-extraction + kill-switch P62 + sandbox/improvement.
- **Risco:** **alto**.

#### Fase B6 — `channel_service` + `template_service` + `provisioning_service` 🟢
- **Objetivo:** decompor `channels.py` (721) e `setup.py`. R15 (`ChannelRegistry.instantiate()`), R17 (config metadata).
- **Dependência circular corrigida:** o rascunho punha "invalidação de cache por evento `channel.updated`" em B6, mas o evento só nasce em C3. **Decisão:** introduzir o evento mínimo `channel.updated`/`config.changed` **dentro de B6** (antecipa a parte de C3 que B6 precisa) **OU** manter invalidação por chamada direta agora e trocar p/ evento em C3. Tornar explícito no diagrama.
- **Risco:** médio. 🟢 (channel+template juntos; provisioning separado).

> **F-nota (parity de canais):** `event_actions_service`/inbound-consolidation é **GOWA-scoped** nesta rodada. Telegram (long-poll) e Cloud (HSM/janela 24h) têm inbound próprio. Verificar que cada channel-plugin emite um **`InboundEvent` normalizado** (seam que telegram/cloud deveriam usar) — ou sinalizar como trabalho futuro p/ não deixar drift.
