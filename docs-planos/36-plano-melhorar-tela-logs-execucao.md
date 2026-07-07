# Plano 36 — Melhorar a tela de logs de Execução (conversation_id, período, tools com resultado, contexto/histórico, tokens/modelo/agente)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-07 · **Escopo:** médio (1 migration Alembic, ~5 pontos de código backend, 1 arquivo frontend grande, testes). **Origem:** pedido do usuário (comparação com a tela de Execuções do Nexus) — quer filtrar por id de conversa e período, e ver args+resultado das tools, tokens, histórico enviado à IA, modelo e agente que respondeu.
> **Método:** leitura do código real (`arquivo:linha` abaixo, verificados nesta sessão) + varredura com sub-agentes `Explore`. Toda afirmação de `arquivo:linha` foi conferida.
> **O quê/por quê:** a tela `/executions` já existe (lista + timeline de steps), mas (a) filtra só por telefone/status — insuficiente porque **telefone não é único entre canais** (Telegram/Instagram/etc.), (b) não mostra `agent_key`/tokens/modelo/`routing_steps` embora a API já os retorne, (c) **não persiste** o resultado das tools nem o histórico/contexto enviado ao LLM (só a contagem). Este plano adiciona `conversation_id`/`channel` na execução, persiste tool-result + contexto (com pruning/kill-switch), estende os filtros (período + conversa) e enriquece o frontend — **incrementalmente** sobre a tela atual.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase.** **Um refactor por commit.** As três frentes (A=schema/writer backend, B=persistência de tool-result+contexto, C=frontend) têm dependências explícitas nas waves abaixo.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-07) | Filtro/identificação da conversa deve ser por **`conversation_id` real**, não por telefone — "pode ser uma caixa do Telegram, Instagram e até outros". | Nova coluna `executions.conversation_id` (+ canal desnormalizado) + backfill NULL nas antigas. Filtro por telefone continua existindo, mas o de conversa é o principal. **F1 (migration) é bloqueante.** |
| **D2** ✅ (2026-07-07) | **Persistir o contexto completo** enviado à IA (system prompt + array de mensagens), com **pruning/retenção** para o banco não crescer sem limite. | Novo step type `llm_context` (ou coluna dedicada — ver P1). Truncamento de mensagens grandes + kill-switch de config + poda reaproveitando `max_executions`/`delete_older_than`. |
| **D3** ✅ (2026-07-07) | Escopo de UI **incremental** sobre a tela atual (mantém lista + timeline). Adiciona filtro de período + conversa, cabeçalho com agente/modelo/tokens/conversa/canal, e cards estruturados por tipo de step (tools com args+result colapsável). | Sem redesenho do `Executions.js`; só extensão. Reaproveita `useUrlState`, paginação e auto-refresh existentes. |
| **D4** ✅ (2026-07-07) | **NÃO incluir**: chave do prompt, custos (`total_cost_usd`), tempo médio, tokens 24h, embedding diário. | O frontend **não** exibe custo nem métricas agregadas; `total_cost_usd` continua sendo gravado no backend (não removido), apenas não surfaçado. |
| **Princípio fixo** | Mudança **aditiva e best-effort**: nenhuma persistência nova pode derrubar o turno (todo `track_step` já é no-op tolerante). `step_type`/`agent_key` são identidade — não renomear os existentes. Postgres-only. Modo escuro obrigatório nas telas tocadas. | Toda escrita nova vai por `track_step`/repo com `try/except` implícito; sem `if/elif` por nome de tool; classes `wa-*`/`.wa-field` no frontend. |

---

## 1. Resumo executivo

A tela de Execuções registra, por turno, uma linha `executions` + uma timeline de `execution_steps` (JSON por step). O backend **já captura** muita coisa que o frontend ignora (`agent_key`, `total_tokens`, `model` dentro dos steps, `routing_steps`). Três lacunas reais exigem backend novo: (1) não existe `conversation_id` na execução — só `phone`, que colide entre canais; (2) o **resultado** das tools nunca é salvo (só `args`); (3) o **histórico/contexto** enviado ao LLM só é salvo como contagem. O plano: **F1** adiciona colunas `conversation_id`/`channel_id`/`channel_label` (migration + writer + repos); **F2** persiste tool-result no step `tool_executed`; **F3** persiste o contexto completo num step `llm_context` (com truncamento, kill-switch e pruning); **F4** estende endpoints/repos com filtros `date_from`/`date_to`/`conversation_id`; **F5** enriquece o `Executions.js` (cabeçalho + cards por step + filtros + coluna agente); **F6** testes. F1 é a barreira; F2/F3 são independentes entre si; F5 depende de F1+F4.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 Tabelas
- `executions` — [db/tables.py:498-516](../db/tables.py#L498-L516): `id, phone, trigger_type, status, started_at, completed_at, error, agent_key, total_tokens, total_cost_usd, routing_steps`. Índice `idx_exec_started` em `started_at`. **Sem `conversation_id`/`channel_id`.**
- `execution_steps` — [db/tables.py:519-530](../db/tables.py#L519-L530): `id, execution_id, step_type, status, data(JSON TEXT), ts, agent_key`. Índice `idx_step_exec` em `execution_id`.
- `conversations` = alias de `atendimentos` — [db/tables.py:411-440](../db/tables.py#L411-L440) (alias [:767](../db/tables.py#L767)): `id, inbox_id, contact_id, status, active_agent_key…`. **O canal vem via `inbox_id → inboxes → channel`, não por coluna direta.**
- `channels` — [db/tables.py:218-256](../db/tables.py#L218-L256): `id` (slug: `"gowa"`/`"telegram_1cfe2138"`/`"default"`), `provider`, `display_name`.

### 2.2 Writer + repo
- Core sync: [agent/execution.py](../agent/execution.py) — `create_execution:40`, `complete_execution:49`, `track_step:59` (no-op se sem execução no contextvar, [:66-68](../agent/execution.py#L66-L68)), `add_execution_usage:76`, `set_execution_agent_key:91`, `set_execution_routing_steps:102`, `set_current_step_agent:35`, `prune_executions:118`.
- Async wrappers: [server/execution.py](../server/execution.py) — `astart_execution:35`, `aend_execution:52`, `atrack_step:71` (emitem eventos `execution.started`/`ended`).
- Repo: [db/repositories/execution_repo.py](../db/repositories/execution_repo.py) — `create:14`, `add_step:23`, `complete:46`, `add_usage:57`, `set_agent_key:74`, `set_routing_steps:84`, `get_by_id:94`, `list_executions:120` (filtros exatos `phone`/`status` + `step_count` + `duration_ms`), `count:159`, `prune:173`, `delete_older_than:186`, `get_webhook_payloads:193`.

### 2.3 Step types emitidos hoje
| step_type | Emitido de | `data` (resumo) |
|---|---|---|
| `webhook_received` | [messaging_service.py:762](../app/services/messaging_service.py#L762); sandbox | `phone, items[]` |
| `batch_accumulated` | [messaging_service.py:788](../app/services/messaging_service.py#L788) | `text_count, media_count, combined_preview` |
| `media_processed` | [agent/llm.py:203,252,327…](../agent/llm.py#L203) | `type, model, *_length` |
| `llm_request` | [agno_engine.py:475,494,534,549](../agent/agno_engine.py#L475) | `model, engine, context_messages` (**só a contagem**), `tools` (nomes) |
| `llm_response` | [agno_engine.py:499,506,554,561](../agent/agno_engine.py#L499) | `model, engine, prompt_tokens, completion_tokens, has_tool_calls` |
| `tool_executed` | [agno_engine.py:178](../agent/agno_engine.py#L178) (async), [:224](../agent/agno_engine.py#L224) (sync) | `tool, args` — **sem `result`** |
| `channel_send` | [messaging_service.py:383,392](../app/services/messaging_service.py#L383) | `channel_id, phone, part, total_parts` |
| `response_sent` | [messaging_service.py:431](../app/services/messaging_service.py#L431); sandbox | `phone, channel_id, parts, reply_preview` |
| `routing_halted` | [agent_run_service.py:223](../app/services/agent_run_service.py#L223) | `reason, pending, chain` |
| `error` | vários | `error, phase` |

### 2.4 Pontos de injeção (verificados)
- **Criação da execução com canal em escopo:** `_run_one_cycle(self, channel_id, phone, items)` [messaging_service.py:749](../app/services/messaging_service.py#L749); `astart_execution(phone, "webhook")` [:760](../app/services/messaging_service.py#L760); `contact = _get_contact(phone, channel_id=…)` [:770](../app/services/messaging_service.py#L770) — `contact.id`/`contact.inbox_id` disponíveis logo após. A conversa já está materializada no ingest ([message_ingest_service.py:464](../app/services/message_ingest_service.py#L464) `ensure_conversation_live`), então `conversation_repo.get_open_for_contact_inbox(contact.id, contact.inbox_id)` ([conversation_repo.py:192](../db/repositories/conversation_repo.py#L192)) é uma leitura barata.
- **4 call sites sandbox:** [sandbox.py:141/185/245/306](../server/routes/sandbox.py#L141) — `astart_execution(phone, "sandbox")`, canal `default`; `contact` resolvido logo abaixo.
- **Contexto completo (system prompt + mensagens) montado:** primeiro hop em [agent_run_service.py:313-322](../app/services/agent_run_service.py#L313-L322) (array `messages` completo antes de `agno_engine.run_async` em [:348](../app/services/agent_run_service.py#L348)); hops de routing em [:134](../app/services/agent_run_service.py#L134). Boundary alternativo: [agno_engine.py:466-481](../agent/agno_engine.py#L466) (onde `split_messages` separa `system_prompt`/`convo` e já há `track_step("llm_request")`).
- **Tool result:** só existe em memória (`executed`) e no evento `tool.after` ([agno_engine.py:165-169](../agent/agno_engine.py#L165)); o `track_step("tool_executed", {tool, args})` omite o result.
- **agent_key na execução:** [agent_run_service.py:294](../app/services/agent_run_service.py#L294) `set_execution_agent_key`.
- **Pruning existente:** `prune_executions(settings.get("max_executions", 200))` chamado em [messaging_service.py:1060](../app/services/messaging_service.py#L1060) e [sandbox.py:118](../server/routes/sandbox.py#L118); `delete_older_than` via `DELETE /api/executions?days=N` ([executions.py:43](../server/routes/executions.py#L43)).

### 2.5 Endpoints + frontend
- [server/routes/executions.py](../server/routes/executions.py): `GET /api/executions` (params `limit/offset/phone/status`, perm `execution.read`, [:14](../server/routes/executions.py#L14)); `GET /api/executions/{id}` ([:32](../server/routes/executions.py#L32)); `DELETE ?days=N` ([:43](../server/routes/executions.py#L43), perm `execution.delete`).
- API client: [api.js:742](../web/static/js/services/api.js#L742) `getExecutions`, [:747](../web/static/js/services/api.js#L747) `getExecution`.
- Frontend: [web/static/js/components/Executions.js](../web/static/js/components/Executions.js) (~431 linhas, arquivo único). Lista [:342-429](../web/static/js/components/Executions.js#L342) — colunas `#, Telefone, Tipo, Status, Início, Duração, Steps`; filtro de telefone [:349](../web/static/js/components/Executions.js#L349) + select de status [:356](../web/static/js/components/Executions.js#L356); PAGE_SIZE=30; auto-refresh 5s; `useUrlState`. Detalhe `ExecutionDetail` [:92-191](../web/static/js/components/Executions.js#L92) — header + timeline `StepBadge` (`STEP_COLORS` [:24-35](../web/static/js/components/Executions.js#L24)) + `JsonBlock` cru [:73](../web/static/js/components/Executions.js#L73). **Não renderiza** `agent_key`/`total_tokens`/`model`/`routing_steps`. `STEP_COLORS` tem `'gowa_send'` **morto** (não mais emitido).

### 2.6 Falsos positivos descartados
| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| "Basta filtrar por telefone (já existe)" | ❌ | Telefone colide entre canais (D1). Precisa `conversation_id`. |
| "O contexto já está salvo, só surfaçar" | ❌ | `llm_request.data.context_messages` é **só a contagem** ([agno_engine.py:478](../agent/agno_engine.py#L478)). O array nunca é persistido. |
| "O result da tool está no step" | ❌ | `track_step("tool_executed", {tool, args})` **omite** result ([agno_engine.py:178,224](../agent/agno_engine.py#L178)). |
| "Precisa adicionar `channel_id` em `conversations`" | ❌ | O canal já é alcançável via `inbox_id→inboxes→channel`; para exibir na lista, desnormalizar na `executions` evita join (ver P2). |
| "`agent_key`/tokens/modelo precisam de captura nova" | ❌ | Já capturados; só faltam no frontend. `agent_key` na execução ([agent_run_service.py:294](../app/services/agent_run_service.py#L294)) e por-step; `model`/tokens no `data` dos steps `llm_*`. |
| "Remover `total_cost_usd`" | ❌ | D4 pede só **não exibir**; a coluna continua sendo escrita (não é removida). |

---

## 3. Fases / Roadmap

### Diagrama de dependências (waves)

```
WAVE 0   F1(migration + writer + repo: conversation_id/channel)   🔴 sozinha (barreira; bloqueia F4,F5)
              │
WAVE 1   F2(tool result)  ·  F3(contexto + kill-switch + pruning)  ·  F4(endpoints/repo: filtros)
              │  F2/F3 independentes entre si (🟢)                    │  F4 [depende de: F1]
              │                                                       │ (barreira: F4 bloqueia F5)
WAVE 2   F5(frontend incremental)   [depende de: F1, F4; usa F2/F3]   🔴 sozinha (arquivo único grande)
              │
WAVE 3   F6(testes de integração + pruning + filtros)   [depende de: F1..F5]   🔴 sozinha
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando (resumo) |
|---|---|---|---|---|---|
| 0 | **F1** `conversation_id`/`channel` na execução | `db/tables.py`, `db/alembic/`, `execution_repo.py`, `agent/execution.py`, `server/execution.py`, `messaging_service.py`, `sandbox.py` | 🔴 barreira | médio | migration round-trip; execuções novas gravam `conversation_id`+canal; antigas NULL |
| 1 | **F2** Persistir **result** da tool | `agent/agno_engine.py` | 🟢 | baixo | `tool_executed.data.result` (truncado) aparece no `get_by_id` |
| 1 | **F3** Persistir **contexto** + kill-switch + pruning | `agent_run_service.py`/`agno_engine.py`, `config/settings.py`, `execution_repo.py` | 🟢 | médio | step `llm_context` com system+mensagens (truncadas) quando ligado; poda respeita retenção |
| 1 | **F4** Filtros de endpoint/repo | `execution_repo.py`, `server/routes/executions.py`, `api.js` | 🟢 `[depende de: F1]` | baixo | `GET /api/executions?conversation_id=&date_from=&date_to=` filtra corretamente |
| 2 | **F5** Frontend incremental | `web/static/js/components/Executions.js` | 🔴 sozinha | médio | filtros período+conversa; cabeçalho agente/modelo/tokens/conversa/canal; cards por step; `gowa_send` removido; dark mode OK |
| 3 | **F6** Testes | `tests/` | 🔴 sozinha | baixo | suíte verde no Postgres de teste cobrindo filtros + persistência |

---

### Fase F1 — `conversation_id` + canal na execução (barreira)
**Objetivo:** toda execução nova carrega o `conversation_id` real e o canal (para filtrar por conversa e exibir o provider na lista sem join).

**Itens:**
- `[sequencial]` **Schema** — em [db/tables.py:498-516](../db/tables.py#L498-L516) adicionar a `executions`: `conversation_id` (Integer, nullable, FK opcional → `atendimentos.id` **sem cascade** para não apagar execuções ao arquivar conversa — a confirmar P2), `channel_id` (Text, nullable), `channel_label` (Text, nullable, desnormalizado = `display_name`/`provider`). Índice novo `idx_exec_conversation` em `conversation_id`.
- `[sequencial]` **Migration** — `alembic revision -m "executions conversation_id + channel"` (próximo número após 0039). `op.add_column` das 3 colunas + `op.create_index`. Downgrade simétrico. Backfill: **não** (antigas ficam NULL, aceitável — D1). Revisar o autogenerate (não deixar mexer em tabela alheia).
- `[sequencial]` **Writer** — estender `create_execution`/`astart_execution` para aceitar `conversation_id`, `channel_id`, `channel_label` opcionais → `execution_repo.create(...)` ([execution_repo.py:14](../db/repositories/execution_repo.py#L14)) grava as colunas. Manter assinatura retrocompatível (kwargs default `None`).
- `[paralelo]` **Call site webhook** — em [messaging_service.py:760](../app/services/messaging_service.py#L760): mover a resolução de `contact` para antes do `astart_execution` (ou resolver `conversation_id` logo após `_get_contact` [:770](../app/services/messaging_service.py#L770) e passar por um setter). Resolver `conversation_id` via `conversation_repo.get_open_for_contact_inbox(contact.id, contact.inbox_id)`; `channel_label` via `channel_repo.get(channel_id)` (`display_name`/`provider`). **Cheap read** (linha já existe). Se a leitura falhar, seguir com NULL (best-effort).
- `[paralelo]` **Call sites sandbox** — [sandbox.py:141/185/245/306](../server/routes/sandbox.py#L141): canal `default`; resolver conversa do `contact` após `_get_contact`. `channel_label = "Sandbox"` (ou `default`).
- `[sequencial]` **Repo read** — `get_by_id`/`list_executions` já fazem `select(executions)` (pegam as colunas novas automaticamente); confirmar que o `dict(row)` propaga.

**Pronto quando:** rodar um turno real (ou teste) → a linha `executions` tem `conversation_id` preenchido e `channel_label`; migration `upgrade`/`downgrade` roda limpa; suíte de endpoints continua verde.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:**
  - `db/tables.py` — 3 colunas em `executions` (`conversation_id` Integer, `channel_id` Text, `channel_label` Text) + `Index("idx_exec_conversation")`.
  - `db/alembic/versions/20260707_0042_executions_conversation_channel.py` — migration guardada/idempotente (`add_column`×3 + `create_index`), downgrade simétrico, `down_revision = "0041_seed_audit_manage"` (head atual verificado via `alembic heads`).
  - `db/repositories/execution_repo.py` — `create(...)` ganhou kwargs opcionais (`conversation_id/channel_id/channel_label`); novo `set_channel(...)` (update parcial best-effort).
  - `agent/execution.py` — `create_execution(...)` repassa os kwargs; novo `set_execution_channel(...)` (lê contextvar, try/except).
  - `server/execution.py` — re-export de `set_execution_channel`; `aset_execution_channel(...)` (async) + `astamp_execution_channel(contact, channel_id, channel_label=None)` (resolve conversa via `conversation_repo.get_open_for_contact_inbox` + label via `channel_repo.get`, tudo best-effort).
  - `app/services/messaging_service.py` — chama `astamp_execution_channel(contact, channel_id)` logo após `_get_contact` (não moveu o `astart_execution`).
  - `server/routes/sandbox.py` — 4 call sites: `astamp_execution_channel(contact, "default", channel_label="Sandbox")` após cada `_get_contact`.
- **Como foi feito / decisões:**
  - **P2 → coluna SOLTA sem FK** (decisão do usuário): execução é log histórico; FK travaria/cascataria ao apagar conversa. Filtro é só igualdade; canal desnormalizado evita join.
  - **Não movi `_get_contact` acima do `astart_execution`** (mitigação do risco): a execução nasce primeiro e o canal/conversa é carimbado via setter depois (best-effort), como o plano recomenda.
  - Toda escrita nova é best-effort (try/except; setters no-op fora de contexto) — nunca derruba o turno.
- **Problemas / pendências:** `tests/test_alembic_hygiene.py` tem 2 falhas **pré-existentes** (merge revision `0040` + prefixo duplicado `0037`), vindas de commits anteriores (`34dad43`/`5fa6bfc`) da reconciliação de heads — não relacionadas a esta fase (a `0042` é linear e de prefixo único). Fora de escopo.
- **Verificação:** migration round-trip `upgrade→downgrade→upgrade` limpo no Postgres de teste; colunas+índice presentes; smoke `create(kwargs)`+`set_channel`+`get_by_id` OK; `python tests/test_endpoints.py` → **1086 passed, 0 failed** (inclui o fluxo de sandbox que agora executa o stamp).

---

### Fase F2 — Persistir o **resultado** das tools
**Objetivo:** o step `tool_executed` passa a carregar o `result` (truncado) além dos `args`, para o painel mostrar entrada **e** saída de cada tool.

**Itens:**
- `[sequencial]` Em [agno_engine.py:178](../agent/agno_engine.py#L178) (async) e [:224](../agent/agno_engine.py#L224) (sync): após o executor retornar (o result já existe no fluxo do `tool.after` [:165-169](../agent/agno_engine.py#L165)), incluir `result` no `data` do `track_step("tool_executed", …)`. **Truncar** para um limite (const nova, ex. `TOOL_RESULT_MAX_CHARS = 4000`) com sufixo `"… (truncado)"` — o result pode ser um JSON grande (ex. `pesquisar_ofertas`).
- `[sequencial]` Registrar também `error` quando a tool falhar (se disponível no ponto), mantendo `status` do step coerente.
- `[paralelo]` Confirmar que não há `if/elif` por nome de tool (regra do repo) — a mudança é genérica no dispatch.

**Pronto quando:** um turno com tool call → `get_by_id` retorna o step `tool_executed` com `args` **e** `result` (truncado); tools sem retorno mostram result vazio/None sem quebrar.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:** `agent/agno_engine.py` — const `TOOL_RESULT_MAX_CHARS = 4000` + helper `_truncate_result(value, limit)`; ambos os entrypoints (async `_make_async_entrypoint` e sync `_make_sync_entrypoint`) agora gravam `result` (truncado) no `track_step("tool_executed", {tool, args, result})`.
- **Como foi feito / decisões:** truncamento genérico com sufixo `"… (truncado)"`; `None` passa como `None` (tool sem retorno não quebra). Coerção defensiva `str(value)` em try/except. Mudança **genérica no dispatch** — nenhum `if/elif` por nome de tool (regra do repo). O caminho de tool bloqueada/pulada não emite `tool_executed` (inalterado). Não há variável de `error` separada no ponto (o `_dispatch_tool` retorna a mensagem de erro como `feedback`), então o result cobre também falhas.
- **Problemas / pendências:** nenhuma.
- **Verificação:** smoke do `_truncate_result` (None/curto/truncado/não-str) OK; `tests/test_tool_runner.py`, `tests/test_tool_call_broadcast.py`, `tests/test_tool_call_limit.py` verdes (rodados individualmente — rodar juntos conflita no DROP SCHEMA por processo).

---

### Fase F3 — Persistir o **contexto/histórico** enviado à IA (com kill-switch e pruning)
**Objetivo:** salvar o system prompt + o array de mensagens realmente enviado ao LLM, de forma consultável, sem estourar o banco.

**Itens:**
- `[sequencial]` **Novo step type `llm_context`** (ver P1 — step vs coluna) emitido no ponto onde o array completo existe: preferir o boundary do engine [agno_engine.py:466-481](../agent/agno_engine.py#L466) (já emite `llm_request` — anexar o contexto ali, ou emitir `llm_context` adjacente), cobrindo tanto o primeiro hop quanto os de routing por passar pela mesma função. Alternativa: [agent_run_service.py:313-322](../app/services/agent_run_service.py#L313) + [:134](../app/services/agent_run_service.py#L134) (dois pontos).
- `[sequencial]` **Truncamento** — cada mensagem do array truncada por `LLM_CONTEXT_MSG_MAX_CHARS` (ex. 2000) e o total por um teto; **remover/anonimizar base64** de mídia (o `raw` pode conter áudio base64) para não inchar. Guardar `role` + `content` truncado + flag `truncated`.
- `[sequencial]` **Kill-switch de config** — nova chave em [config/settings.py](../config/settings.py) `DEFAULT_CONFIG`, ex. `execution_capture_context` (bool, **default a decidir — P3**). Quando OFF, não emite `llm_context` (mantém só a contagem atual em `llm_request`). Adicionar à allow-list de config se houver validação de chaves.
- `[sequencial]` **Pruning/retenção** — o pruning por quantidade já existe (`prune_executions(max_executions)` [messaging_service.py:1060](../app/services/messaging_service.py#L1060)); como `execution_steps` tem FK `ON DELETE CASCADE`, podar a execução já remove o contexto. Adicionar (a) config de retenção por dias opcional aplicada no mesmo loop (reusar `delete_older_than`), e (b) documentar que ligar a captura aumenta o tamanho por execução — considerar reduzir `max_executions` default se a captura estiver ON (a confirmar).
- `[paralelo]` Garantir que a emissão é best-effort (o `track_step` já é no-op tolerante fora de contexto; envolver a serialização/truncamento em try/except para nunca quebrar o turno).

**Pronto quando:** com `execution_capture_context=ON`, um turno grava um step `llm_context` com system prompt + mensagens truncadas (sem base64); com OFF, não grava; poda por quantidade/dias remove execução + contexto junto (CASCADE).

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:**
  - `config/settings.py` — 2 chaves em `CONFIG_KEYS`: `execution_capture_context` (bool, **default False** = kill-switch OFF, exposta+writable) e `execution_retention_days` (int, default 0, exposta+writable). Como derivam de `CONFIG_KEYS`, entram automaticamente em `DEFAULT_CONFIG`/GET/allow-list do PUT.
  - `agent/agno_engine.py` — `import re`; consts `LLM_CONTEXT_MSG_MAX_CHARS=2000`, `LLM_CONTEXT_TOTAL_MAX_CHARS=20000`; helpers `_context_capture_enabled()` (lê o kill-switch, best-effort), `_scrub_and_truncate()` (remove base64 data-URI + runs longos de base64, trunca por msg), `_capture_llm_context(system_prompt, convo, model_id)` (emite step `llm_context` com system + mensagens truncadas). Chamado após o `track_step("llm_request")` nos dois paths (`run_async`/`run_sync`).
  - `agent/execution.py` — `import time`; `prune_executions(max_keep, retention_days=0)` agora também poda por idade via `delete_older_than` quando `retention_days>0` (CASCADE leva os steps junto).
  - Call sites de poda passam `execution_retention_days`: `app/services/messaging_service.py` (`_after_send`-equivalente) e `server/routes/sandbox.py` (`_after_send`).
- **Como foi feito / decisões:**
  - **P3 → kill-switch OFF por default** (decisão do usuário): conservador, banco não cresce até ligar. Toggle na tela em F5.
  - **P1 já decidido no plano → step `llm_context`** (não coluna): reaproveita `execution_steps` (JSON + CASCADE) e cobre 1º hop + hops de routing pelo mesmo boundary do engine.
  - Boundary escolhido: o do engine (`run_async`/`run_sync`), onde `system_prompt` e `convo` já existem juntos — cobre routing sem tocar em `agent_run_service.py` (evita colisão com a outra IA que mexe lá).
  - Base64 scrub defensivo: convo já é texto (transcrições), mas o scrub protege contra qualquer blob; runs ≥200 chars do charset base64 viram `[base64 removido]` (texto normal tem espaços, não casa).
  - Toda a captura é best-effort (try/except engole; `track_step` no-op fora de contexto) — nunca quebra o turno.
- **Problemas / pendências:** nenhuma. (Não reduzi `max_executions` default quando a captura está ON — a retenção por dias/quantidade + kill-switch OFF já seguram o tamanho; deixado como config do operador.)
- **Verificação:** contra o Postgres de teste — capture ON emite `llm_context` (system + 3 msgs, base64 removido); OFF não emite nada; truncação por msg OK em texto com espaços; poda por dias remove execução antiga e preserva a recente (CASCADE). `tests/test_routing_engine.py` (26/0) e `tests/test_agent_routing.py` (29/0) verdes.

---

### Fase F4 — Filtros de endpoint/repo (período + conversa)
**Objetivo:** a API permite filtrar por `conversation_id` e por intervalo de datas, além do `phone`/`status` atuais.

**Itens:**
- `[sequencial]` **Repo** — estender `list_executions` ([execution_repo.py:120](../db/repositories/execution_repo.py#L120)) e `count` ([:159](../db/repositories/execution_repo.py#L159)) com params `conversation_id: int|None`, `date_from: float|None`, `date_to: float|None` (epoch). `where`: `executions.c.conversation_id == …`, `executions.c.started_at >= date_from`, `< date_to`. Manter `phone`/`status`.
- `[sequencial]` **Endpoint** — em [executions.py:14](../server/routes/executions.py#L14) adicionar query params `conversation_id`, `date_from`, `date_to` (aceitar `YYYY-MM-DD` e converter para epoch no boundary; `date_to` = fim do dia inclusivo). Repassar ao repo. Manter formato `{ok, data:{items, total}}`.
- `[paralelo]` **API client** — `getExecutions(params)` ([api.js:742](../web/static/js/services/api.js#L742)) já repassa `params`; confirmar que os novos campos passam na query string.

**Pronto quando:** `GET /api/executions?conversation_id=123` retorna só as execuções daquela conversa; `?date_from=2026-07-01&date_to=2026-07-07` filtra o intervalo; `total` reflete o filtro.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:**
  - `db/repositories/execution_repo.py` — helper `_exec_filters(...)` compartilhado; `list_executions` e `count` ganharam `conversation_id`, `date_from`, `date_to` (epoch) mantendo `phone`/`status`.
  - `server/routes/executions.py` — query params `conversation_id`/`date_from`/`date_to` + `_parse_date()` (aceita `YYYY-MM-DD` **ou** epoch; `date_to` = fim do dia inclusivo via `<` estrito no repo; entrada inválida vira None e dropa o filtro em vez de 500). Formato de resposta `{ok, data:{items, total}}` inalterado.
  - `web/static/js/services/api.js` — `getExecutions` agora dropa params vazios/null (blank filter não vira `""`=422 no endpoint tipado).
- **Como foi feito / decisões:** where-builder único evita drift entre list e count. `date_from >=` / `date_to <` (semi-aberto com fim-de-dia inclusivo). Datas em UTC (consistente com `started_at` = `time.time()`).
- **Problemas / pendências:** nenhuma.
- **Verificação:** repo — filtro por `conversation_id`, janela de data (dia inclusivo) e combinação conv+data conferidos contra o Postgres de teste; `_parse_date` (yyyy-mm-dd/empty/invalid/end_of_day) OK; `node --check api.js` OK; `python tests/test_endpoints.py` → **1086 passed, 0 failed** (retrocompat da assinatura).

---

### Fase F5 — Frontend incremental (`Executions.js`)
**Objetivo:** surfaçar tudo que o backend agora fornece, mantendo a estrutura da tela.

**Itens (lista):**
- `[paralelo]` **Filtros novos** — ao lado do filtro de telefone/status ([Executions.js:348-367](../web/static/js/components/Executions.js#L348)): input `ID conversa` (numérico) e dois inputs `date` (De/Até), integrados ao `useUrlState` e resetando `page`. Usar `.wa-field` (dark mode).
- `[paralelo]` **Coluna(s) na lista** — adicionar `Agente` (de `agent_key`) e opcionalmente `Tokens` (`total_tokens`) e `Canal` (`channel_label`) às colunas [:377-408](../web/static/js/components/Executions.js#L377). Manter `#, Conversa` (novo, de `conversation_id`) — priorizar Conversa sobre Telefone conforme D1.
- `[sequencial]` **Cabeçalho do detalhe** — em `ExecutionDetail` [:113-140](../web/static/js/components/Executions.js#L113) exibir: agente (`agent_key`), modelo (derivado do step `llm_request/llm_response`), tokens (`total_tokens` ou soma dos `llm_response`), conversa (`conversation_id` clicável), canal (`channel_label`), e `routing_steps` (cadeia de handoff) quando presente.
- `[sequencial]` **Cards por tipo de step** — substituir o `JsonBlock` cru [:73-88](../web/static/js/components/Executions.js#L73) por renderização estruturada **por step_type** (via mapa/registro, sem `if/elif` gigante): `tool_executed` → tool + `args` e `result` colapsáveis; `llm_request`/`llm_response` → modelo + tokens; `llm_context` → lista de mensagens (role + conteúdo) expansível + system prompt; demais → fallback `JsonBlock`. Manter deep-link `?step=`.
- `[paralelo]` **Limpeza** — remover `'gowa_send'` morto de `STEP_COLORS` [:24-35](../web/static/js/components/Executions.js#L24); adicionar cor para `llm_context`.
- `[sequencial]` **Dark mode** — todas as áreas novas com classes `wa-*`/`.wa-field`; testar com `.dark` ligado.

**Pronto quando:** recarregar `/executions` → filtros de período e conversa funcionam e persistem na URL; abrir um detalhe → cabeçalho mostra agente/modelo/tokens/conversa/canal; steps de tool mostram args+result colapsáveis; step de contexto mostra o histórico; tudo legível no modo escuro; `gowa_send` sumiu do código.

#### Status de execução — Fase F5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F6 — Testes
**Objetivo:** garantir os novos filtros e a nova persistência contra o Postgres de teste.

**Itens:**
- `[paralelo]` **Filtros** — em `tests/` (endpoints): criar execuções com `conversation_id`/`started_at` variados e assertar `GET /api/executions?conversation_id=`, `?date_from=&date_to=`.
- `[paralelo]` **Persistência** — assertar que um turno com tool grava `tool_executed.data.result` (truncado) e, com `execution_capture_context=ON`, grava `llm_context` (sem base64, truncado); com OFF, não grava.
- `[paralelo]` **Pruning** — assertar que `prune_executions` remove execução + steps (CASCADE) e que a retenção por dias funciona.
- `[paralelo]` **Migration round-trip** — `upgrade`/`downgrade` da migration de F1 (o helper `tests/pg.py` recria o schema por processo).

**Pronto quando:** suíte verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`), cobrindo filtros novos + persistência + pruning.

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 4. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Persistência do contexto | Crescimento do banco (contexto pode ser grande, incl. base64 de mídia no `raw`) | Truncamento por mensagem + teto total; **remover base64**; kill-switch (`execution_capture_context`, default a decidir P3); pruning por quantidade (CASCADE) + dias |
| Result de tool grande | JSON enorme (ex. `pesquisar_ofertas`) infla o step | `TOOL_RESULT_MAX_CHARS` com sufixo de truncamento |
| FK `conversation_id` | Apagar/arquivar conversa poderia cascatear e sumir com execuções | Sem `ON DELETE CASCADE` na FK (ou nullable sem FK — P2); execuções são log histórico |
| Ordem da migration | Colidir com heads do Alembic (repo teve merge de heads recente) | Conferir `alembic heads` antes; gerar em cima do head atual; downgrade simétrico |
| Mover `_get_contact` acima do `astart_execution` | Alterar a ordem pode mudar timing/efeitos colaterais | Preferir **não mover**: resolver `conversation_id` após `_get_contact` e gravar via setter/`update` na execução já criada (best-effort) |
| Best-effort | Uma exceção na captura nova derrubar o turno | Toda escrita nova em try/except; `track_step` já é no-op fora de contexto |
| Postgres-only | Comportamento de índice parcial/tipos | Seguir padrões do repo (Core, sem batch-mode); testar no Postgres de teste |
| Modo escuro | Filtros/cards novos ilegíveis no `.dark` | Classes `wa-*`/`.wa-field`; testar com tema escuro |
| `total_cost_usd` | Remover por engano (D4 pede só ocultar) | Não tocar na coluna nem no writer; apenas não exibir no frontend |

---

## 5. Perguntas em aberto

**P1 — Contexto: novo step type `llm_context` vs coluna dedicada na `executions`?**
✅ DECIDIDO (2026-07-07): **step type `llm_context`**. Contexto: reaproveita `execution_steps` (JSON + CASCADE + timeline), é aditivo (nenhuma migration extra além de F1), e casa com o boundary do engine que já emite `llm_request` — cobrindo primeiro hop e hops de routing pelo mesmo ponto. (a) step `llm_context` ✅ recomendado; (b) coluna `executions.llm_context` — descartada (menos flexível para multi-hop, exigiria coluna grande TEXT).

**P2 — `executions.conversation_id`: FK real ou coluna solta? E `channel` desnormalizado?**
⏸️ ADIADO para a execução de F1. Contexto: apagar/arquivar conversa não deve sumir com o log. (a) FK **sem cascade** (integridade + join fácil) — recomendado se não atrapalhar o `DROP SCHEMA` dos testes; (b) coluna Integer **sem FK** (mais solta, zero risco de cascade). `channel_label` desnormalizado é recomendado nos dois casos (evita join na lista). Decidir ao escrever a migration.

**P3 — Default do kill-switch `execution_capture_context`: ON ou OFF?**
⏸️ ADIADO — decisão do usuário na execução. (a) **OFF por default** (conservador: banco não cresce até o operador ligar para depurar) — recomendado; (b) **ON por default** (a feature "histórico enviado à IA" funciona out-of-the-box, com pruning segurando o tamanho). Sugestão: OFF + toggle visível na tela de Execuções ou em Configurações.

**P4 — Modelo "openai/gemini": exibir o slug cru ou um rótulo amigável?**
⏸️ ADIADO (cosmético, F5). O `model` vem como slug do proxy (ex. `deepseek/deepseek-v4-pro`). (a) exibir o slug cru — simples; (b) derivar um rótulo/família (provider) — mais bonito, mas exige mapa. Recomendação: slug cru no MVP.

---

## 6. Checklist de verificação

- [ ] Migration de F1 faz `upgrade`/`downgrade` limpo (round-trip) no Postgres de teste.
- [ ] Execução nova grava `conversation_id` + `channel_label`; execuções antigas seguem com NULL sem quebrar a lista.
- [ ] `tool_executed.data.result` presente e truncado; tools sem retorno não quebram.
- [ ] `execution_capture_context=ON` grava `llm_context` (sem base64, truncado); OFF não grava.
- [ ] Pruning por quantidade e por dias remove execução + steps (CASCADE).
- [ ] `GET /api/executions` filtra por `conversation_id` e por `date_from`/`date_to`; `total` coerente.
- [ ] Frontend: filtros de período/conversa persistem na URL (back/forward); cabeçalho mostra agente/modelo/tokens/conversa/canal; cards de tool com args+result; step de contexto legível.
- [ ] `gowa_send` removido de `STEP_COLORS`.
- [ ] Telas novas legíveis no **modo escuro** (`.dark`).
- [ ] `tests/test_endpoints.py` verde; suíte verde no Postgres (`WHATSBOT_TEST_DB_URL`).
- [ ] Nenhum segredo/base64 grande vazando na URL ou no log.

---

## 7. Apêndice — arquivos-chave (por camada)

**DB / migration**
- [db/tables.py](../db/tables.py) — `executions` (498-516): 3 colunas + índice.
- `db/alembic/versions/00XX_*.py` — nova migration (colunas + índice).
- [db/repositories/execution_repo.py](../db/repositories/execution_repo.py) — `create`, `list_executions`, `count` (filtros + colunas novas).

**Backend — writer/serviços**
- [agent/execution.py](../agent/execution.py) / [server/execution.py](../server/execution.py) — `create_execution`/`astart_execution` (params novos).
- [app/services/messaging_service.py](../app/services/messaging_service.py) — `_run_one_cycle` (:749, resolver `conversation_id`/canal); pruning (:1060).
- [server/routes/sandbox.py](../server/routes/sandbox.py) — 4 call sites (:141/185/245/306).
- [agent/agno_engine.py](../agent/agno_engine.py) — `tool_executed` result (:178/:224); `llm_context` (:466-481).
- [app/services/agent_run_service.py](../app/services/agent_run_service.py) — contexto (:313-322, :134); `agent_key` (:294).
- [config/settings.py](../config/settings.py) — `execution_capture_context` + retenção.
- [server/routes/executions.py](../server/routes/executions.py) — query params novos.

**Frontend**
- [web/static/js/components/Executions.js](../web/static/js/components/Executions.js) — filtros, cabeçalho, cards por step, `STEP_COLORS`.
- [web/static/js/services/api.js](../web/static/js/services/api.js) — `getExecutions` (:742).

**Testes**
- `tests/` — filtros, persistência (tool result + contexto), pruning, migration round-trip.
