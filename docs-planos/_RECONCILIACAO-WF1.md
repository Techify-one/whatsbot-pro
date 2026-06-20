# _RECONCILIACAO-WF1 — Reconciliação plano ↔ código (WhatsBot Pro)

> Resultado do **WF1**: 8 subagentes read-only verificaram, fase a fase e com evidência
> `arquivo:linha`, os planos **01, 02, 03, 04, 05, 06, 08, 09** contra o **código real**.
> Plano **07 (auditoria) ficou FORA** (P68–P75 adiados). Trilha = **Pro completo**.
>
> - **Verificado contra**: working tree em `b673a61` — 2 commits de docs à frente de `58586e1`
>   (`d016c5c` add planos, `b673a61` remove handoff TPM); a **árvore de código** é idêntica à de
>   `58586e1`, **mais os 5 arquivos não commitados do kill-switch P62** (ver §"Estado do working tree").
> - **Precedência aplicada** (de `_LEIA-PRIMEIRO.md §1`): código real > `_REAVALIACAO-capability-map`
>   > `_REAVALIACAO-relatorio` > `DECISOES.md` > planos 01–10 > `docs-pesquisa/`.
> - **Sequência viva** das ondas = `_REAVALIACAO-relatorio.md §4` (não a do `00-plano-mestre`).
> - Gerado em **2026-06-20**.

---

## 0. TL;DR (leia isto)

1. **Dos 8 planos, só o 06 (motor multiagente) tem código.** E mesmo ele está **parcial/divergente**:
   o motor AGNO + config-in-DB foi "puxado pra frente" (Onda 5 antecipada), operando **por `phone`**,
   sem inbox/conversa/RBAC/runtime. Os planos **01, 02, 03, 04, 05, 08, 09 são greenfield** (`nao_feito`
   em todas as fases) — confirmado por ausência total de artefatos (grep vazio em `db/`, `server/`,
   `agent/`, `web/`, `config/`).
2. **A maior mudança transversal é a renumeração de migrations.** Os slots **0007 e 0008 já foram
   consumidos** (`0007_ai_engine_tables`, `0008_plugin_installed_deps`). **Todo plano que reservava
   0007/0008 renumera para 0009+**, encadeando linear a partir do head real `0008_plugin_installed_deps`
   (P82). **⚠️ Boot-breaker no plano 04**: ele manda usar `down_revision = head 0006` — se não for
   corrigido, `alembic upgrade head` ramifica a cadeia e **quebra o boot**.
3. **O RCE do code-in-DB JÁ está mitigado por padrão** (kill-switch `ai_tools_code_enabled`, default OFF
   — os 5 arquivos não commitados deste commit). Logo o checklist **"P0 gate admin-only" do relatório §6
   está OBSOLETO**. O que falta é (a) RBAC (plano 03) e (b) isolamento por subprocesso (retrofit P62/P67
   sobre o plano 09).
4. **Decisões do Lote 3 já reconciliam vários itens do plano 06**: P64 (output_schema) **rebaixado** a
   opcional; P65 **cumprida** no sentido "motor é AGNO" (config-in-DB segue atrás de flag OFF); P67
   **reclassificado** de ADIADO para "retrofit". O texto dos planos 06 e 09 é que ainda não reflete isso.
5. **Drift de números de linha em todos os planos** (foram escritos sobre um snapshot pré-AGNO). As
   âncoras semânticas (funções, registros de rota, sites do gate) continuam válidas; os offsets, não.
   Usar `grep`, nunca linha hardcoded, na implementação.

---

## 1. Tabela-resumo

### 1.1 — O que mudou desde o plano (por plano)

| Plano | Estado geral | O que mudou desde que o plano foi escrito |
|---|---|---|
| **01 — Inbox e Conversas** | `nao_feito` (5/5 fases) | Gate de IA **ainda no modelo pré-P5** (`contacts.ai_enabled` nos 3 sites do webhook). Migrations `inbox_conversations`/`backfill` renumeram **0007/0008 → 0009/0010**. Sidebar segue "1 contato = 1 thread". Drift de linhas (`:834/:860/:966` → `:844/:870/:991`). |
| **02 — Canais e Providers** | `nao_feito` (5/5 fases) | Nada do pacote `channels/`, `ChannelRegistry`, `entry.channels`, runtime/subprocesso existe. Migration `channels` renumera **0007 → 0009**. P15 (sem cifragem) e P29 (só Linux) confirmados como direção; `gowa/` segue monolítico no core, device singleton. |
| **03 — RBAC e Usuários** | `nao_feito` (6/6 fases) | Schema RBAC inteiro ausente; `server/auth.py` ainda **SHA-256 + senha única** (sem Argon2id, sem `users`, sem bootstrap P34). Migration `rbac_users` renumera **0007 → 0009**. É **fundação** de 01/02/04/05/08. |
| **04 — Respostas Rápidas** | `nao_feito` (Fase 1–2; 3 bloqueada por RBAC) | Greenfield e **autocontido** (Fase 1 sem FKs — pode começar já). **⚠️ Boot-breaker**: plano usa `down_revision=0006`; corrigir para `0008_plugin_installed_deps` (slot **0009**). P42/P47 (sem escopo, texto puro) já refletidos no plano. |
| **05 — Atributos Personalizados** | `nao_feito` (6/6 fases) | Sem `custom_attribute_definitions`, sem `contacts.custom_attributes`, sem helper `_json_type()`. Migration renumera **0007 → 0009**. Âncoras (`contacts.py` PUT info, `get_full_contact`, `CORE_TOOLS`, `get_info_summary`) batem. Fase 5/6 dependem de 01/08. |
| **06 — Motor Multiagente** | **parcial/divergente** (único com código) | Fase 0 **feito** (AGNO em prod, mas `agno` **sem pin**). Fase 1 **parcial** (tabelas `ai_*` criadas, schema mais pobre que o plano). Fase 2 **divergente** (via `agent_factory`, não `ai_engine/runner`; sem `output_schema`). Fase 3 **parcial** (installer in-process, mitigado por kill-switch). Fases 4/5 **bloqueadas** por 01/02. P64↓/P65✓/P67→retrofit. |
| **08 — Filtros** | `nao_feito` (todas as fases) | Módulo `db/filters/*` ausente; sem `GET /api/conversations`; só o filtro degenerado `q+archived` em `/api/contacts`. **Depende de 01 (conversations) + 05 (custom_attributes) + 03 (users)**. P76/P81/FQ4 alinhados; migration `saved_filters` é 0009+ (depois de 01/05). |
| **09 — Fundação Runtime** | `nao_feito` (6/6 fases) | Sem pacote `runtime/`, sem supervisor, sem `SubprocessService`. `os._exit` cego sem teardown; 4 tasks core hardcoded; GOWA com `Popen` cru (sem process-group/pdeathsig/killpg/stale-kill). **Premissa invertida**: o tool_runner code-in-DB **já existe in-process** → cria a dependência cruzada 06⇄09 (retrofit P62/P67 = Onda 2). Sem migrations (P30). |

### 1.2 — Matriz de fases (estado por fase)

| Plano | Fases (em ordem) | Resumo |
|---|---|---|
| 01 | 1a · 1b · 1c · 1d · 1e | ⬜ ⬜ ⬜ ⬜ ⬜ — tudo `nao_feito` |
| 02 | F0 · F1 · F2 · F3 · F4 | ⬜ ⬜ ⬜ ⬜ ⬜ — tudo `nao_feito` |
| 03 | F1 · F2 · F3 · F4 · F5 · F6 | ⬜ ⬜ ⬜ ⬜ ⬜ ⬜ — tudo `nao_feito` |
| 04 | F1 · F2 · F3 · (F4–6 futuro) | ⬜ ⬜ ⬜ — `nao_feito`; F4–6 cortadas do MVP (P42/P46/P47) |
| 05 | pré · F1 · F2 · F3 · F4 · F5 · F6 | ⬜ ⬜ ⬜ ⬜ ⬜ ⬜ ⬜ — tudo `nao_feito` |
| **06** | F0 · F1 · F2 · F3 · F4 · F5 · F6 · F7 | ✅ 🟡 🔶 🟡 ⬜ ⬜ 🟡 ⬜ — **feito · parcial · divergente · parcial · nao_feito · nao_feito · parcial · nao_feito** |
| 08 | F0 · §1 · F1 · F2 · F3 · F4 · §5-8 | ⬜ ⬜ ⬜ ⬜ ⬜ ⬜ ⬜ — tudo `nao_feito` |
| 09 | F1 · F2 · F3 · F4 · F5 · F6 | ⬜ ⬜ ⬜ ⬜ ⬜ ⬜ — tudo `nao_feito` |

Legenda: ✅ feito · 🟡 parcial · 🔶 divergente · ⬜ nao_feito.

### 1.3 — Itens acionáveis priorizados por onda (sequência viva, relatório §4)

> Ondas: **0** = endurecimento do que já shippou · **1** = plano 09 (Fases 1–4, `SubprocessService`) ·
> **2** = retrofit P62 (isolar code-in-DB) · **3** = RBAC (03) + Inbox (01) · **4** = completar 06 ·
> **5+** = 02, 04, 05 (independentes) · 08 (depende de 01/05/03, entra **após** eles).

| # | Onda | Item | Plano | Esforço | Risco |
|---|---|---|---|---|---|
| 1 | **0** | Pinar `agno` (e `openai`) em `requirements.txt` (hoje sem pin) | 06 | baixo | regressão silenciosa em build futuro (agno 3.x) |
| 2 | **0** | Popular `executions.agent_key/total_tokens/total_cost_usd` (colunas existem, writer não grava) | 06 | baixo | critério-de-pronto da Fase 2 não atendido |
| 3 | **0** | `server/dev.py` passar `ai_engine_enabled` ao handler (config-in-DB nunca liga no dev) | 06/runtime | baixo | feature invisível em dev/hot-reload |
| 4 | **0** | Atualizar `CLAUDE.md` (8 plugins→só `lembretes`; "11 tabelas"→**20** com `ai_*`; documentar motor AGNO) | — | baixo | doc enganando quem implementa |

> **Origem dos itens #3 e #4**: não vêm de um delta de plano — são as inconsistências de escopo WF1
> registradas em `_LEIA-PRIMEIRO.md §4` (itens 4 e 6) e na Onda 0 do `_REAVALIACAO-relatorio.md §4`.
> Listados aqui por pertencerem ao endurecimento "do que já shippou", **não** como achados do fan-out
> por plano (que confirmou cada um dos demais itens com evidência `arquivo:linha`).
| 5 | **0** | *(FEITO — neste commit)* kill-switch `ai_tools_code_enabled` default OFF + tool nasce `enabled=False` | 06 | — | — (mitigação P62 já aplicada) |
| 6 | **1** | Fase 1: `plugins/lifecycle.py` (setup/teardown aguardado) + `PluginContext`/`on_unload` + wiring no lifespan | 09 | médio | shutdown trava se plugin estoura timeout ~10s (P31) |
| 7 | **1** | Fase 2: `schedule_restart(on_before_exit=…)` roda teardown antes do `os._exit`; passar em enable/disable/delete | 09 | baixo | deadlock no `run_coroutine_threadsafe` se loop já parando |
| 8 | **1** | Fase 3: `runtime/supervisor.py` (TaskSupervisor + backoff) + migrar 4 tasks core + corrigir cancel-sem-await + `task.crashed` em KNOWN_EVENTS (P28) | 09 | médio | regressão na reconexão GOWA/QR (preservar watchdog 3/60s, P27) |
| 9 | **1** | Fase 4: `runtime/subprocess_service.py` (`start_new_session`+pdeathsig POSIX+killpg+PID-file/stale-kill+readiness); `gowa/manager` delega; `subprocess.crashed/restarted` (P28/P29) | 09 | alto | **maior alavanca e maior risco**: stale-kill errado mata PID reciclado; pode perder sessão WhatsApp |
| 10 | **1** | Fase 5: `set_runtime` injeta supervisor+subprocess; `ctx.spawn_task/spawn_subprocess`; `server/routes/runtime.py` + `RuntimePanel.js` + WS | 09 | médio | endpoints admin-only só com auth atual até RBAC |
| 11 | **1** | Fase 6: plugin `runtime_probe` (enabled=0) + testes unidade supervisor/subprocesso + cobertura `/api/runtime/*` | 09 | baixo | — |
| 12 | **1** | Atualizar texto do plano 09 (P67 não é mais ADIADO; tool_runner já existe in-process) | 09 | baixo | só doc/sequenciamento |
| 13 | **2** | **Retrofit P62/P67**: migrar `ai_tool_installer` de `exec_module` in-process → subprocesso isolado (RLIMIT+timeout) sobre o `SubprocessService` | 06 | alto | RCE só mitigado por kill-switch até aqui; alto se operador ligar sem isolamento |
| 14 | **3** | Plano 03 Fases 1–2: 6 tabelas RBAC + `0009_rbac_users` (seed roles/permissions) + `server/permissions.py` + Argon2id (`passlib[argon2]`) + 5 repos | 03 | médio | remover SHA-256 quebra login até bootstrap existir; seed por key em SQLite+PG |
| 15 | **3** | Plano 03 Fases 3–6: sessões server-side + `auth_middleware` com `request.state.user` + `server/deps.py` (Require) + `users.py` + frontend + **preservar isenções `/api/webhook` e `/health`** | 03 | alto | **regressão crítica**: GOWA posta sem credencial; WS handshake; invariante "último admin" |
| 16 | **3** | Plano 01 Fases 1a–1b: `0009_inbox_conversations` (stub `inboxes`, `contact_inboxes`, `conversations`, `conversation_counters`, `messages.conversation_id`, drop unique `contacts.phone`, `+custom_attributes`) + `0010_backfill` idempotente | 01 | alto | `batch_alter_table` recria tabela no SQLite (risco em DB grande); `down_revision` **deve** ser `0008_plugin_installed_deps` |
| 17 | **3** | Plano 01 Fases 1c–1e: `conversation_repo`/`contact_inbox_repo` (resolve_inbound P2, display_id P6) + **reescrever gate de IA nos 3 sites para cascata global→inbox→conversa (P5)** + evoluir `transfer_to_human` + `conversations.py` + frontend (abas/fila/badge grupo/toggle por conversa) | 01 | alto | coração da mudança; regressão no fluxo de mensagens; coexistir com motor AGNO stateless (gate fica no webhook, §5.6) |
| 18 | **3** | Plano 04 Fase 3: gatear escrita de quick replies por `quickreply.manage` (depois do RBAC) | 04 | médio | bloqueado até 03 |
| 19 | **4** | Migration `0009+` das colunas faltantes do schema rico do 06 (`execution_steps.agent_key`, `executions.routing_steps`, `ai_agents.routing_targets/hooks_config/is_router/description`, `ai_prompts.kind`) | 06 | médio | sem isso handoff/routing (F5/F6) não têm onde gravar |
| 20 | **4** | Completar 06: binding agente↔inbox (`default_agent_key`/`active_agent_key`), handoff/routing (`transferir_para_outro_agente`, `routing.py`), API `history`/`rollback`, frontend AI (Agents/Prompts/Variables/Tools) | 06 | alto | depende de inbox(01)+RBAC(03); CRUD hoje só via REST cru |
| 21 | **4** | Alinhar doc↔código de P64 (`output_schema`/LLMResponse) — opcional, só se handoff exigir | 06 | médio | DECISOES já rebaixou; é alinhamento, não bug |
| 22 | **5+** | **Plano 04 Fase 1** (quick replies, **autocontido — pode iniciar imediatamente**): tabela + `0009_quick_replies` (`down_revision=0008_plugin_installed_deps`) + repo + rotas + gatilho `/` no composer + tela | 04 | médio | **corrigir down_revision** (senão boot quebra); cuidado no composer p/ não quebrar `mentionMenu` |
| 23 | **5+** | Plano 05 Fases 1–4: `_json_type()` + `custom_attribute_definitions` + `contacts.custom_attributes` + `0009_custom_attributes` + repo/validate + rotas + frontend + tool IA `set_custom_attribute` | 05 | médio | migration JSON/JSONB `with_variant` à mão (autogenerate não acerta), testar SQLite+PG; mutation tracking documentado |
| 24 | **5+** | Plano 02 Fases 0–3: pacote `channels/` + `entry.channels` no loader + tabelas + `0009_channels` + plugin `whatsapp_cloud` + `channels.py`/UI + extrair GOWA p/ plugin + multi-número | 02 | alto | refactor do parsing do webhook sem regressão; extração do GOWA pode perder sessão; depende do runtime (09) p/ Fase 3 |
| 25 | **5+** | Plano 08 módulo `db/filters/*` + Fases 1–3 (`GET/POST /api/conversations`, `cattr:*`, tags N:N, `saved_filters`) | 08 | alto | **depende de 01+05+03**; anti-injection (allowlist de key/operador) crítico; só entrega fallback degradado sem 01 |

---

## 2. Apêndice — Cadeia Alembic real e renumeração coordenada

**Cadeia atual** (verificada em `db/alembic/versions/`):

```
0001_baseline → 0002_message_revoked → 0003_message_reactions → 0004_message_reply_to
  → 0005_contact_pinned → 0006_contact_mention → 0007_ai_engine_tables → 0008_plugin_installed_deps  (HEAD)
```

**`0007` e `0008` foram consumidas** por `ai_engine_tables` (AGNO) e `plugin_installed_deps` (pkg_deps),
**após** a redação dos planos. Cada plano reservou um slot que agora colide.

| Plano | Migration planejada | Slot reservado (no plano) | Novo slot | `down_revision` correto |
|---|---|---|---|---|
| 01 | `inbox_conversations` | 0007 | **0009** | `0008_plugin_installed_deps` |
| 01 | `backfill_conversations` | 0008 | **0010** | `0009_inbox_conversations` (encadeia no próprio 01) |
| 02 | `channels` | 0007 | **0009** | `0008_plugin_installed_deps` |
| 03 | `rbac_users` | 0007 | **0009** | `0008_plugin_installed_deps` |
| 04 | `quick_replies` | (cita head 0006) | **0009** | `0008_plugin_installed_deps` **⚠️ boot-breaker se ignorado** |
| 05 | `custom_attributes` | 0007 | **0009** | `0008_plugin_installed_deps` |
| 06 | `ai_engine_tables` | 0007 | **0007 (já consumido)** | `0006_contact_mention` — **não renumerar** |
| 06 | `ai_agent_links` (futura) | 0008 | **0009+** | head real no momento (`0008…` ou posterior) |
| 08 | `saved_filters` | (cita head 0006) | **0009+** | head produzido por 01/05 (entram antes) |
| 09 | — (P30: sem migration) | — | — | — |

> **⚠️ Coordenação inter-planos (P82).** A tabela acima mostra **cada plano isoladamente** pegando
> "0009". Na prática **só um** pode ser `0009` — o encadeamento é **linear na ordem real de
> implementação**: a primeira migration a entrar usa `down_revision=0008_plugin_installed_deps` e vira
> `0009`; a próxima aponta para essa e vira `0010`; e assim por diante. **A regra é "head real no
> momento de implementar", não o número fixo do plano.** Pela sequência viva (relatório §4), a ordem
> provável é: 09 (sem migration) → 03 (`rbac_users`) → 01 (`inbox_conversations`, `backfill`) → 06
> (`ai_agent_links`) → 02/04/05/08.

---

## 3. Estado do working tree (kill-switch P62)

Estes 5 arquivos estavam **modificados e não commitados** no início do WF1 — e são exatamente a
**implementação do kill-switch P62** (mitigação imediata do RCE do code-in-DB, decisão do Lote 3,
`DECISOES.md:258-265`). Os subagentes os enxergaram como código real (working tree). Vão pro GitHub
**neste mesmo commit** do relatório (decisão do Thiago).

| Arquivo | Mudança |
|---|---|
| `config/settings.py` | `+ai_tools_code_enabled` (default **False**) + env `WHATSBOT_AI_TOOLS_CODE` |
| `server/app.py` | gate no `create_app`: installer code-in-DB só roda se `ai_tools_code_enabled` |
| `server/routes/ai_engine.py` | tool criada via API nasce `enabled=False` (gate P63 real, antes era `True`) |
| `server/routes/config.py` | `ai_tools_code_enabled` exposto/aceito no GET/PUT `/api/config` |
| `agent/ai_tool_installer.py` | comentário de **SECURITY DEBT (P62)** documentando a dívida in-process |

**Consequência para os planos**: o checklist **"P0 — gate admin-only" do `_REAVALIACAO-relatorio.md §6`
está OBSOLETO** (a mitigação shipou depois). O que resta é (a) separação de papéis = **RBAC, plano 03**
e (b) isolamento por subprocesso = **retrofit P62/P67 sobre o plano 09** (Onda 2).

---

## 4. Seções por plano

### Plano 01 — Inbox e Conversas — `nao_feito` (5/5)

**Fases**
- **1a schema** — `nao_feito`. `db/tables.py:45` (`contacts.phone` ainda UNIQUE), `:51` (`ai_enabled`
  presente), `:79` (`messages` sem `conversation_id`); grep por `inboxes/contact_inboxes/conversations/
  conversation_counters` em `db/` = 0 hits. Schema intocado.
- **1b backfill** — `nao_feito`. Nenhuma migration de backfill; head real = `0008_plugin_installed_deps`.
- **1c repos+webhook+handler** — `nao_feito`. Sem `conversation_repo`/`contact_inbox_repo`; gate de IA
  ainda `contact.ai_enabled and settings.get("auto_reply")` em `webhook.py:844,870,991`;
  `transfer_to_human.py:51` ainda chama `set_ai_enabled(False)`; `memory.py:191` legado.
- **1d API+WS** — `nao_feito`. Sem `server/routes/conversations.py`; sem eventos `conversation_*`.
- **1e frontend** — `nao_feito`. `Contacts.js:28` só `showArchived`; `:66` toggle IA por phone; `api.js`
  sem `listConversations/patchConversation/assignMe`. Sidebar "1 contato = 1 thread".

**Divergências vs decisões**
- **P5** — código ainda **pré-P5**: `contacts.ai_enabled` é o único gate (`webhook.py:844/870/991`). O
  plano está **correto** (descreve a cascata global→inbox→conversa); só não foi feito. Reconciliar com
  relatório §5.6 (gate permanece no webhook, não no motor AGNO stateless).
- **— (drift)** — plano cita `:834/:860/:966`; sites reais em `:844/:870/:991`. Lógica idêntica.
- **P82** — renumerar `inbox_conversations`→0009, `backfill`→0010 (ver §2).

**Migrations a renumerar**: `inbox_conversations` 0007→**0009** (`down=0008_plugin_installed_deps`);
`backfill_conversations` 0008→**0010** (`down=0009_inbox_conversations`).

**Dependências quebradas**: `inboxes` (plano 02 — mitigado por stub local); `users`/`current_user`
(plano 03 — `assignee_user_id` nasce NULLABLE sem FK, P1); `inbox_members`/`teams` (plano 03). Plano 01
é **Onda 3** (depois de 09/03). Binding agente↔inbox é trabalho do 06 que **depende** deste plano.

**Incertezas**: coordenação exata do número da migration depende da ordem real (todos os planos disputam
0009+); se o binding agente↔inbox entra no 01 ou no 06.

---

### Plano 02 — Canais e Providers — `nao_feito` (5/5)

**Fases** — todas `nao_feito`: sem pacote `channels/` (`ls channels/` → inexistente), sem contrato
`Channel`/`ChannelRegistry`/`InboundEvent`, sem tabelas `channels`/`channel_credentials`, sem
`entry.channels` no loader, sem refactor de parsing do webhook (`webhook.py:1093` é o único
`@app.post('/api/webhook')`), sem `runtime/`, sem plugin `whatsapp_cloud`, sem `server/routes/channels.py`
nem `ChannelsManager.js`. `gowa/` segue monolítico no core, device singleton (`gowa/client.py:12,52,57,142`).

**Divergências vs decisões**
- **P15** — sem cifragem (alinhado, MVP). Ao implementar: **não** adicionar `cryptography`; espelhar o
  mascaramento que já existe em `/api/config` (chave do LLM) na borda de `channels.py`.
- **P26** — pacote `runtime/` não existe (esperado); criar e atualizar a árvore no `CLAUDE.md`.
- **P29** — `gowa/manager.py` ainda tem `CREATE_NO_WINDOW`; ao extrair, focar POSIX
  (`start_new_session`+`PR_SET_PDEATHSIG`), sem Job Object.

**Migrations a renumerar**: `channels` 0007→**0009** (`down=0008_plugin_installed_deps`).

**Dependências quebradas**: Fase 0 (`entry.channels`) destrava todas as outras; Fase 1 depende de
mudar `plugins/context.py`/`restart.py` (teardown — pré-requisito p/ Fase 3); FK `conversation.channel_id`
exige coordenar ordem de migration com o plano 01; gating admin de `/api/channels` espera o RBAC (03).

**Incertezas**: offsets do plano divergem (lifespan, auth-exempt, SPA, webhook); acoplamento do motor
AGNO ao roteamento de saída por canal não foi avaliado.

---

### Plano 03 — RBAC e Usuários — `nao_feito` (6/6)

**Fases** — todas `nao_feito`. `db/tables.py` sem `users/roles/permissions/role_permissions/user_roles/
user_sessions/inbox_members` (CORE_TABLES tem **20**: as **13** originais + **7** `ai_*`). `server/auth.py` (40 linhas)
ainda **SHA-256 + token determinístico + `web_password_hash`**; `requirements.txt` sem `passlib|argon2`.
`server/routes/auth.py` só `login`+`check` (sem `logout/me/bootstrap`). `auth_middleware`
(`server/app.py:245`) não anexa `request.state.user`. Sem `server/deps.py`, sem `Require`, sem
`server/routes/users.py`, sem `UsersManager.js`.

**Divergências vs decisões**
- **P82** — `rbac_users` 0007→**0009** (`down=0008_plugin_installed_deps`).
- **P34** — bootstrap do 1º admin e remoção do SHA-256 não feitos (sem divergência de intenção).
- **— (drift)** — âncoras de linha do §0 desatualizadas (`auth_middleware:245`, `_AUTH_EXEMPT_*:231-232`,
  `_SPA_PATHS:239`, registro de rotas `:328-344`). Estrutura compatível.

**Migrations a renumerar**: `rbac_users` 0007→**0009** (`down=0008_plugin_installed_deps`).

**Dependências quebradas**: `inboxes/inbox_members` (plano 01) → inbox-scoping no-op/stub (P38, não
bloqueia o RBAC por papel); `channels.manage` sem `channels` (P38); plano 07 depende de
`request.state.user.id` (Fase 3).

**Incertezas**: onda exata do 03 não confirmada por leitura direta do relatório §4; `LoginScreen.js` não
lido por inteiro (classificado por `api.js`/`app.js`); `.spec` PyInstaller (argon2 hidden-imports) não
inspecionado.

> **Atenção crítica de regressão** (Fase 3): preservar isenções `/api/webhook` e `/health` no
> `auth_middleware` — o GOWA posta sem credencial. Remover o SHA-256 antes do bootstrap existir quebra o
> login → coordenar Fases 2–3 num único PR.

---

### Plano 04 — Respostas Rápidas — `nao_feito` (Fases 1–2; 3 bloqueada)

**Fases**
- **Fase 1 (lista global texto puro)** — `nao_feito`, greenfield. Sem tabela `quick_replies`, sem
  `quick_reply_repo.py`, sem `server/routes/quick_replies.py`, sem migration. **Os pontos de integração
  existem e são válidos**: composer/menção em `ContactDetail.js` (`:36,53,191,…,1265`), `api.js:26`,
  registro de rotas `server/app.py:328-344` (plano cita `304-319` — drift ~24 linhas).
- **Fase 2 (cache+invalidação+validação short_code+filtro)** — `nao_feito` (depende da Fase 1).
- **Fase 3 (RBAC `quickreply.manage`)** — `nao_feito`, **bloqueada pelo plano 03**.
- **Fases 4–6 (mídia, variáveis, escopo)** — fora do MVP (P46/P47/P42).

**Divergências vs decisões**
- **P82** — plano assume head `0006`; head real é `0008`. **Setar `down_revision=0008_plugin_installed_deps`
  e numerar 0009. ⚠️ Se ignorado, `alembic upgrade head` ramifica e quebra o boot.**
- **P42/P47** — schema mínimo (sem escopo, texto puro) já reflete as decisões. Sem divergência de design.

**Migrations a renumerar**: `quick_replies` → **0009** (`down=0008_plugin_installed_deps`).

**Dependências quebradas**: Fase 3 depende de `server/deps.py`+`permissions.py`+`users` (plano 03). A
Fase 1, porém, é **autocontida (sem FKs)** — **pode começar imediatamente**.

**Incertezas**: `quickreply.manage` ainda não está no catálogo (só citada em `DECISOES.md:205`); drift de
linhas no `ContactDetail.js` confirmado por grep, não linha-a-linha.

---

### Plano 05 — Atributos Personalizados — `nao_feito` (6/6)

**Fases** — todas `nao_feito`. Sem helper `_json_type()`/`JSONB`/`with_variant` (`db/tables.py` só tem
JSON-as-Text em `messages.reactions`). Sem `custom_attribute_definitions`, sem `contacts.custom_attributes`,
sem `custom_attribute_repo`/`validate`, sem `server/routes/custom_attributes.py`, sem
`CustomAttributesManager.js`/`CustomAttributeField.js`, sem tool `set_custom_attribute`. **Âncoras batem**:
`contacts.py:971` (PUT info), `contact_repo.py:466` (`get_full_contact`), `__init__.py:27` (CORE_TOOLS),
`memory.py:286` (`get_info_summary`).

**Divergências vs decisões**
- **P56 / P82** — migration `custom_attributes` 0007→**0009** (`down=0008_plugin_installed_deps`).

**Migrations a renumerar**: `custom_attributes` 0007→**0009** (`down=0008_plugin_installed_deps`).

**Dependências quebradas**: Fase 5 (atributos de conversa) depende de `conversations` (plano 01); Fase 6
(filtros/índices) depende do plano 08; gate admin/atendente depende do RBAC (03 — `created_by` nasce
nullable). Fases 1–4 **não** são bloqueadas (apenas o gate fica como TODO atrás de auth de sessão).

**Incertezas**: offsets pré-AGNO; verificação por ausência total de artefatos (estática, sem rodar
alembic/testes).

---

### Plano 06 — Motor Multiagente — **parcial/divergente** (único com código)

**Fases**
- **F0 spike AGNO** — **feito**. `requirements.txt:1` (`agno`), `agent/agno_engine.py` (em produção).
  ⚠️ `agno` **sem pin** (plano/relatório pediam `>=2.6,<3`).
- **F1 fundação de dados** — **parcial**. Tabelas `ai_agents/ai_prompts/ai_variables/ai_tools` + 3
  `*_history` + repos com versão/snapshot + `seed_default_agent` (`0007_ai_engine_tables`, `db/tables.py:222-319`).
  **Schema mais pobre que o plano**: `ai_agents` sem `description/hooks_config/routing_targets/is_router`;
  `ai_prompts` sem `kind`/PK composta; JSON como TEXT (não `JSONB`); `executions` ganhou
  `agent_key/total_tokens/total_cost_usd` (mas **writer não popula**) e **não** `routing_steps`;
  `execution_steps` sem `agent_key`.
- **F2 agente configurável** — **divergente**. Implementado via `agent_factory.build_for_contact`
  (`:84-121`) + `agno_engine`, **não** via `ai_engine/runner.py`/`run_conversation`. **Sem
  `output_schema`/LLMResponse** (split via JSON manual). Flag `ai_engine_enabled` default **OFF**, não
  "agno-default".
- **F3 code-in-DB** — **parcial**. Installer feito (`ai_tool_installer.py`: materializa
  `storages/ai_tools/<name>.py`, pip via `pkg_deps`, `importlib`, `install_status`); tool nasce
  `enabled=False` (P63). **Runner subprocess+RLIMIT+timeout (P62) NÃO feito** — `exec_module` roda
  **in-process** (`:77`), mitigado só pelo kill-switch `ai_tools_code_enabled` (default OFF).
- **F4 multi-agente por inbox** — `nao_feito`. **Bloqueado por 01/02** (sem `inboxes/conversations`,
  sem `default_agent_key/active_agent_key`); sem frontend `ai/`.
- **F5 handoff/routing** — `nao_feito`. Sem `transferir_para_outro_agente`/`routing.py`/`active_agent_key`.
- **F6 hot-reload/versionamento** — **parcial**. Hot-reload de **dado** (lê DB por request); versionamento
  nos repos; evento `ai.config.changed`. **Sem** `dynamic_registry` com cache/TTL, **sem** endpoints
  `history`/`rollback`, **sem** `hooks_config`. Legado **não** aposentado (caminho principal com flag OFF).
- **F7 UI como plugin** — `nao_feito` (opcional; tudo no core, como recomendado).

**Divergências vs decisões**
- **P64** — sem `output_schema`; mas **DECISOES Lote 3 rebaixou P64** a opcional → só alinhar o plano.
- **P65** — **cumprida** no sentido "motor é AGNO" (loop OpenAI removido); o **config-in-DB** segue
  atrás de flag OFF. "Agno-first" = motor de raciocínio, não config-in-DB. Não re-litigar.
- **P62** — code-in-DB in-process, mitigado por kill-switch → **dívida documentada**; retrofit = Onda 2.
  Checklist "P0 admin-only" do relatório §6 **OBSOLETO**.
- **P60** — sem `inboxes/conversations`; bloqueado por 01/02 (direção confirmada, "sem trabalho agora").
- **P61** — precedência código>plugin>banco cumprida no backend (registry no-op); badge na UI pendente.

**Migrations**: `0007_ai_engine_tables` **já consumida** (não renumerar). `ai_agent_links` (futura,
colunas de binding) → **0009+** (`down=0008_plugin_installed_deps` ou head posterior).

**Dependências quebradas**: F4/F5 dependem de 01 (e 02 p/ binding por inbox); isolamento da F3 depende do
`SubprocessService` (09 — retrofit); edição ADM-only depende do RBAC (03, hoje substituído pelo
kill-switch); colunas do schema rico (hooks/routing) faltam → bloqueiam F5/F6 até nova migration.

**Incertezas**: não inspecionado o corpo de `execution_repo` p/ confirmar 100% que o writer não grava
tokens; extração de usage/`agent_key` via `agno_engine.metrics` não relida; a flag do plano (`config['ai_engine']`
default `agno`) difere do código (`ai_engine_enabled` bool default False).

---

### Plano 08 — Filtros — `nao_feito` (todas)

**Fases** — todas `nao_feito`. Sem `db/filters/*` (`FilterSpec/build_where`), sem `GET /api/conversations`,
sem `conversation_repo`, sem `FilterBar.js`/`Conversations.js`, sem `saved_filters`/`saved_filter_repo`/
`server/routes/saved_filters.py`. O único caminho é o filtro degenerado `q+archived` em
`/api/contacts` (`contacts.py:45-48` → `contact_repo.list_contacts:360`). Blocos reaproveitáveis: switch
de dialeto (`db/upsert.py:26-27`) e busca textual (`contact_repo._contact_ids_matching_message:324`).

**Divergências vs decisões**
- **P82** — `saved_filters` aponta para o head real no momento (já 0009+ depois de 01/05), não 0006.
- **P76** — alinhado (`/api/conversations` canônico ainda precisa ser criado pelo plano 01).

**Migrations a renumerar**: `saved_filters` → **0009+** (depois das migrations de 01/05).

**Dependências quebradas**: `conversations` (01), `inboxes` (02), `users`/`inbox_members`/`current_user`
(03), `custom_attribute_definitions`/`custom_attributes`/`_json_type()` (05) — **todas ausentes**.
Bloqueiam o valor real de todo o plano; sem 01 só entrega fallback degradado sobre `contacts`. P55
(índice de expressão `filterable`) depende da coluna do 05.

**Incertezas**: planos 01/02/03/05 não lidos em si (só confirmado que as tabelas não existem); número
exato da migration depende da ordem de execução; FQ4 já fecha P81 (texto do plano desatualizado).

---

### Plano 09 — Fundação Runtime — `nao_feito` (6/6)

**Fases** — todas `nao_feito`. Sem `plugins/lifecycle.py`/`PluginContext`/`on_unload` (`loader.py:32-50,
199-271` não reconhece `entry.lifecycle`); `schedule_restart` com `os._exit` cego sem teardown
(`restart.py:42,78`); sem pacote `runtime/`; 4 tasks core hardcoded e canceladas **sem await**
(`app.py:187-198`); `task.crashed/subprocess.crashed/subprocess.restarted` ausentes de `KNOWN_EVENTS`
(`events.py:39-66`, P28); `gowa/manager.py:122-150` com `Popen` cru (sem `start_new_session`/pdeathsig/
killpg/stale-kill/readiness); sem `server/routes/runtime.py`/`RuntimePanel.js`; sem plugin `runtime_probe`.

**Divergências vs decisões**
- **P26** — criar pacote `runtime/` + atualizar `CLAUDE.md` (só não feito).
- **P29** — focar POSIX (`start_new_session`+pdeathsig); `CREATE_NO_WINDOW` vira no-op de compat.
- **P28** — adicionar os 3 eventos a `KNOWN_EVENTS`.
- **P67** — **texto do plano desatualizado**: P67 não é mais ADIADO (Lote 3 → retrofit). E **premissa
  invertida**: o plano assume que o tool_runner code-in-DB ainda não existe, mas ele **já existe
  in-process** (`ai_tool_installer.py`, capability-map:120) → dependência cruzada **06⇄09** = Onda 2.

**Migrations a renumerar**: **nenhuma** (P30 — health só em memória, sem migration).

**Dependências quebradas**: 09 é **fundacional** e não depende de outros planos para começar (Onda 1).
Dependências de **saída**: extração do GOWA (02) e retrofit P62 (06) ficam **bloqueados até a Fase 4
entregar o `SubprocessService`**. Em aberto: se o `SubprocessService` abraça o `pkg_deps` (pip até 600s
no boot, fora de supervisor).

**Incertezas**: avaliação estática (grep/read); estado `nao_feito` de alta confiança (todos os artefatos
ausentes do filesystem); `restart.py` não lido por inteiro (mas sem `on_before_exit`/`run_teardown`).

---

## 5. Síntese para o Thiago

- **Nada do MVP Pro de inbox/canais/RBAC/filtros/atributos/runtime existe ainda** — só o motor de IA
  (parcial). O esforço real está **todo à frente**.
- **A primeira coisa a fazer é mecânica e barata (Onda 0)**: pinar `agno`, popular `executions`, fixar
  `server/dev.py`, atualizar `CLAUDE.md`. O kill-switch P62 já está feito (vai neste commit).
- **A maior alavanca técnica é o plano 09 (Onda 1)** — o `SubprocessService` destrava o retrofit de
  segurança (Onda 2) **e** a extração do GOWA (plano 02). É também o de maior risco.
- **Cuidado operacional nº 1**: ao gerar **qualquer** migration nova, `down_revision` = head real
  (`0008_plugin_installed_deps` hoje). O plano 04 é o caso mais perigoso (cita 0006 → quebra o boot).
- **Cuidado operacional nº 2**: o RBAC (plano 03) mexe no `auth_middleware` — **preservar as isenções
  `/api/webhook` e `/health`** ou o GOWA para de entregar mensagens.
- **Quick wins independentes**: o plano 04 (Fase 1) é autocontido e pode começar a qualquer momento; o
  plano 05 (Fases 1–4) só depende de si mesmo.
