# Plano 29 — Motor de agentes: guardrails + routing hub-and-spoke (nexus) · único roteador · Postgres-only

> **Status:** PLANEJAMENTO · **Data:** 2026-07-02 · **Escopo:** grande (3 eixos: agent engine + config/DB constraint + infra Postgres-only)
>
> **Origem:** pedido do Thiago em cima de duas investigações desta sessão — (1) o problema de routing `roteador → comercial → roteador → humano` que hoje é estruturalmente impossível no WhatsBot, e (2) o estudo do sistema **nexus** (`/opt/nexus/gerenciamento-ia`), que é o **ancestral de design** do `ai_agents` do WhatsBot (mesmas colunas `is_router`/`routing_targets`/`hooks_config`/`tool_names`/`prompt_key`/`model_config`) e já resolve esses pontos de forma mais madura. Somam-se dois requisitos novos: **um único agente roteador** e **matar o SQLite (Postgres-only)**.
> **Método:** leitura do código real dos DOIS repositórios + workflow multi-agente (8 leitores) comparando subsistema a subsistema. Todos os `arquivo:linha` abaixo foram verificados no branch `developer` (head Alembic = `0034_conversation_origin`; suites `test_endpoints.py` 990 + `pytest tests/` verdes).
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. Verde a cada fase; **caracterização ANTES** de mexer no fluxo de routing; **um refactor por commit**.
>
> Legenda de estado de execução: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.
> Legenda de paralelização: `🟢 PODE AGRUPAR` (sem dependência) · `🔴 FAÇA SOZINHA` (sequencial/bloqueante).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Portar do nexus só pontos específicos, NÃO frameworks.** O bus de filters/plugins do WhatsBot já é mais extensível que o pipeline de regras do nexus. | Eixo A é cirúrgico. Ver §6 "Falsos positivos / não portar". |
| D2 | **Um único agente roteador** (`is_router=True`). Evita a bagunça de roteadores mútuos com allowlists cruzadas (a causa real do ping-pong observado no QA do motor). | Eixo B. Só o roteador tem `routing_targets`; os demais são **folhas** que voltam pro roteador. Simplifica o Eixo A (revisita só precisa reativar **o** roteador). |
| D3 | **WhatsBot passa a ser Postgres-only.** Matar o caminho SQLite no código. | Eixo C. Fail-fast se `DATABASE_URL` não for Postgres. Remove branches, `database.json`, `migration_postgres`, `migrate_json`. |
| D4 | **Não está em produção/distribuído** (memória `refactor-rollout-context`) ⇒ **refactor agressivo, sem stopgap de compatibilidade**. Alvo dev/prod é Postgres (memória `postgres-dev-target`, host `203.0.113.60`). | Sem camada de compat SQLite; o EXE Windows zero-config deixa de valer (ver P1). |
| D5 | **Ordem:** Eixos A + B **primeiro** (feature, rodam verdes no backend atual antes de dropar SQLite), Eixo C **por último** (infra isolada). | Waves 0–2 = A/B; Wave 3 = C. Assim a feature de routing é validada em dual-backend antes de a base mudar. |
| D6 | **Nexus NÃO conserta o "`call_limit` queima em falha"** (o contador dele também incrementa antes de rodar). Consertar isso é ir **além** do nexus — decisão de design (P4), não port direto. | Item A1 fica com escopo honesto; o fix do contador é opcional/adiado. |

---

## 1. Resumo executivo

Três eixos independentes que se reforçam:

- **Eixo A — Motor de agentes.** Portar do nexus os guardrails e o routing hub-and-spoke que o WhatsBot não tem: **threading do motivo do handoff** entre hops (hoje os hops são cegos), **revisita controlada** (hoje o `seen` mata `roteador→comercial→roteador`), **escalação automática pra humano** ao esgotar o routing, `requires_prior_call` **success-aware**, mensagens de bloqueio que **orientam a saída**, teto global de tool calls, e gate de humano **desacoplado** do flag `ai_active`.
- **Eixo B — Único roteador.** Enforçar no máximo um `is_router=True` (app-level + índice único parcial). Torna o modelo de routing um hub-and-spoke limpo.
- **Eixo C — Postgres-only.** Remover o caminho SQLite do código (engine, upsert, alembic batch-mode, testes, endpoints de migração) e o mecanismo `database.json`.

Eixo A é **puro código + config** (sem migração de schema, exceto observabilidade que reusa coluna JSON já existente). Eixo B tem **uma** migração dialect-agnóstica. Eixo C é a mudança de infra grande e é feita por último, isolada.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 Routing dentro do turno
- **Loop multi-hop:** `run_with_routing` ([ai_engine/routing.py:29-58](../ai_engine/routing.py#L29-L58)). Teto `MAX_ROUTING_DEPTH = 5` **hardcoded** ([:26](../ai_engine/routing.py#L26)). **Bloqueia revisita:** `seen = {first_agent_key}` + `if nxt in seen: break` ([:41,47-49](../ai_engine/routing.py#L41-L49)) → mata `A→B→A`. Ao estourar depth/ciclo, só dá `break` e retorna o último resultado — **não escala pra humano**.
- **Driver:** `run_turn` / `_continue_routing` / `_run_routing_hop` ([app/services/agent_run_service.py](../app/services/agent_run_service.py)). ⚠️ **Contexto congelado:** `context_messages` é montado **uma vez** ([:187](../app/services/agent_run_service.py#L187)) e reusado idêntico em cada hop ([:83](../app/services/agent_run_service.py#L83)). A resposta do assistant só é salva **no fim** ([:276](../app/services/agent_run_service.py#L276)) → o output do hop intermediário nem entra no histórico do próximo hop. **Este é o gap central do objetivo de routing.**
- **Tool de handoff:** `transferir_agente` ([agent/tools/transferir_agente.py](../agent/tools/transferir_agente.py)). Recebe `motivo` mas só o **loga** ([:80](../agent/tools/transferir_agente.py#L80)) e emite `ConversationAgentChanged(reason=...)` ([:87-93](../agent/tools/transferir_agente.py#L87-L93)) — **o motivo nunca chega ao próximo hop**. Tem allowlist de destino do roteador ([:74-77](../agent/tools/transferir_agente.py#L74-L77)).
- **Tool de humano:** `transfer_to_human` ([agent/tools/transfer_to_human.py](../agent/tools/transfer_to_human.py)) — desativa IA, desatribui, cria a tag `transferido_atendente` ([:54-55](../agent/tools/transfer_to_human.py#L54-L55)) **que ninguém lê como gate**. É tool core disponível a **todos** os agentes por default.

### 2.2 Guardrails / hooks
- `check_hooks(hooks_config, tool_name, executed)` ([ai_engine/hooks.py:35-62](../ai_engine/hooks.py#L35-L62)). `call_limit` ([:50-54](../ai_engine/hooks.py#L50-L54)): `_ran_count >= limit`. `requires_prior_call` ([:56-60](../ai_engine/hooks.py#L56-L60)): aceita **só string** e testa `_ran_count == 0` — ⚠️ **só exige que o prior tenha RODADO, não que tenha tido SUCESSO**. Default único: `_DEFAULT_HOOKS = {"transferir_agente": {"call_limit": 1}}` ([:27](../ai_engine/hooks.py#L27)).
- ⚠️ **Contador conta falhas:** o entrypoint anexa a entrada em `executed` **depois** do dispatch, com `result` (mesmo string de erro) e **sem** `skipped` ([agent/agno_engine.py:174,218](../agent/agno_engine.py#L174)) → uma chamada que "rodou e falhou" conta pro `call_limit`. Chamada bloqueada por hook é anexada com `skipped:True,blocked` ([:154,198](../agent/agno_engine.py#L154)) — essa não conta (correto).
- ⚠️ **Estado zera por-hop:** `run_async`/`run_sync` criam `executed = []` novo a cada hop ([agno_engine.py:414,465](../agent/agno_engine.py#L414)). Logo `call_limit`/`requires_prior_call` são **per-hop**, não per-turno. Quem trava o ping-pong entre hops é o `seen`, não o hook.
- **Sem teto global:** `_build_single_agent` **não** passa `Agent(tool_call_limit=...)` ao AGNO ([agno_engine.py:274](../agent/agno_engine.py#L274)) — nenhum freio no total de tool calls por run.
- Mensagens de bloqueio ([hooks.py:53,59](../ai_engine/hooks.py#L53)) são becos sem saída ("Não a chame de novo") — **não nomeiam** a rota de escape.

### 2.3 Gate de IA (3 camadas, plano 21)
- Global `auto_reply` → canal `ai_enabled` → conversa `ai_active`. O gate por-conversa `_conversation_ai_active` ([app/services/messaging_service.py:~1077](../app/services/messaging_service.py#L1077)) lê **só** `ai_active`. ⚠️ Não checa se há `assignee_user_id` humano; se o flag dessincronizar, a IA responde por cima do humano.

### 2.4 Único roteador — pontos de leitura/escrita de `is_router`
- Schema: `is_router` / `routing_targets` ([db/tables.py:570-571](../db/tables.py#L570)).
- Write path: `agent_repo.save` ([db/repositories/agent_repo.py:90-159](../db/repositories/agent_repo.py#L90-L159)) — hoje **não valida** unicidade de roteador.
- API: `POST/PUT` agente ([server/routes/ai_engine.py:75-98](../server/routes/ai_engine.py#L75-L98)); validação de `routing_targets` como lista ([:76-77](../server/routes/ai_engine.py#L76)).
- UI: `AgentsManager.js` — toggle `isRouter` ([:149](../web/static/js/components/ai/AgentsManager.js#L149)), envia `routing_targets` só quando router ([:244](../web/static/js/components/ai/AgentsManager.js#L244)), chip "router" ([:652](../web/static/js/components/ai/AgentsManager.js#L652)).

### 2.5 Footprint SQLite (Eixo C)
- **Resolução de URL + PRAGMAs + branches:** `db/engine.py` — `resolve_database_url` ENV>`database.json`>sqlite default ([:63-75](../db/engine.py#L63-L75)); `init_engine` branches sqlite/psycopg ([:98-135](../db/engine.py#L98-L135)); PRAGMAs WAL/foreign_keys/busy_timeout ([:142-153](../db/engine.py#L142-L153)); `is_sqlite`/`is_postgres`/`get_sqlite_path` ([:170-180](../db/engine.py#L170-L180)).
- **UPSERT dialect branch:** `db/upsert.py:26-34` ([../db/upsert.py](../db/upsert.py#L26-L34)).
- **Alembic batch-mode (SQLite-only):** `render_as_batch=is_sqlite` ([db/alembic/env.py:39,45,55,62](../db/alembic/env.py#L39)). ~8 migrations usam `batch_alter_table`.
- **Migração obsoleta:** `db/migration_postgres.py` (endpoint SQLite→PG) e `db/migrate_json.py` (JSON→SQLite). Endpoint admin ([server/routes/admin.py](../server/routes/admin.py)).
- **Testes:** default temp SQLite ([tests/conftest.py:184-198](../tests/conftest.py#L184), [tests/support.py:32-33](../tests/support.py#L32)); `WHATSBOT_TEST_DB_URL` já permite Postgres.
- **Export:** `db/__init__.py` exporta `is_sqlite`.

---

## 3. Design da solução — como o nexus resolve (referência)

O loop do nexus (`/opt/nexus/gerenciamento-ia/ai/src/services/ai_service.py::_route`) sustenta o hub-and-spoke com quatro invariantes que o WhatsBot **viola** hoje:

| Invariante do nexus | WhatsBot hoje | Fase que fecha |
|---|---|---|
| Spoke devolve pro roteador **com o motivo** injetado na msg do próximo hop (`[REDIRECIONAMENTO de X]\nMotivo: …`) | motivo só é logado; hops cegos | **A2** |
| **Revisita permitida** (re-invocação roda com `skip_history`), freio é o depth-cap configurável | `seen` mata a revisita; depth hardcoded | **A3** |
| Estouro do cap → **`transferir_para_humano` automático** | `break` silencioso | **A4** |
| `requires_prior_call` **success-aware** (prior que falhou não satisfaz) + bloqueio que **nomeia** `solicitar_roteamento` | só checa "rodou"; bloqueio sem saída | **A1** |
| Gating **por `tool_names`** (roteador tem `transferir_humano`, spoke não) | tool core p/ todos por default | **A6** |

O padrão nexus **`solicitar_roteamento`** (o spoke não escolhe o destino; só sinaliza "fora do meu escopo" e o roteador re-decide) combina perfeitamente com o **único roteador** (Eixo B): spokes viram folhas que sempre voltam pro hub.

---

## 4. Inventário / análise

### 4.1 Eixo A — Motor de agentes (portar/adaptar)

| # | Item | Arquivo(s) `:linha` | O que muda | Migração? | Risco | Esforço |
|---|------|---------------------|------------|-----------|-------|---------|
| A-i1 | Threadar **motivo** do handoff | routing.py:29-58 · agent_run_service.py:66-146 · transferir_agente.py:80 | injetar msg sintética `[REDIRECIONAMENTO de X] Motivo:…` antes do context congelado; carregar o `motivo` do `executed_tools` até o `run_hop` | Não | médio | M |
| A-i2 | **Revisita controlada** | routing.py:41-49 · agent_run_service.py | remover `if nxt in seen: break`; permitir revisita c/ flag `is_reinvoke` (reduzir/omitir output próprio); `MAX_ROUTING_DEPTH`→config `ai_max_route_depth` | Não | médio | M |
| A-i3 | `requires_prior_call` **success-aware** | hooks.py:56-60 | portar `_FAILURE_MARKERS`/`_result_ok`; usar `result` de `executed[]` (já disponível); prior que falhou não satisfaz | Não | baixo | S |
| A-i4 | **Escalar pra humano** ao estourar cap/ciclo | agent_run_service.py · routing.py | no fim anômalo, disparar `transfer_to_human(motivo="Limite de roteamento…")` **no caller** (routing.py fica puro) | Não | baixo | S |
| A-i5 | Bloqueio que **orienta a saída** | hooks.py:53,59 | mensagem nomeia `transferir_agente` (parametrizar pelo nome real, não hardcode) | Não | baixo | S |
| A-i6 | **Gate de humano** desacoplado do `ai_active` | messaging_service.py:~1077 · conversation_repo.py | bloquear IA quando conversa tem `assignee_user_id` humano E `active_agent_key` não-IA; tag `transferido_atendente` como 2ª trava; fail-open no erro | Não | baixo | S |
| A-i7 | `default_call_limit` global por-tool (config) | hooks.py:46-54 · config/settings.py | ler `ai_tool_call_limit_per_tool` (default configurável; `0`=ilimitado); aplicar a tool sem `call_limit` próprio; substitui/mantém `_DEFAULT_HOOKS` | Não | baixo | S |
| A-i8 | Teto global de tool calls no Agent AGNO | agno_engine.py:274-280 · config/settings.py | `Agent(tool_call_limit=ai_tool_call_limit_total)` — **a confirmar suporte na versão do AGNO** | Não | médio | S |
| A-i9 | `requires_prior_call` aceitar **lista** | hooks.py:56-60 | `isinstance(prior,(str,list))` + iterar | Não | baixíssimo | S |
| A-i10 | Observabilidade: `reason` + `routing_halted` | routing.py · agent_run_service.py · execution_repo.py | `routing_steps` `{from,to,depth}`→`{…,reason}`; `track_step('routing_halted',{reason,chain})` no fim anômalo | Não (reusa JSON) | baixo | S |
| A-i11 | (opcional/além-nexus) `call_limit` **não queima em falha** | hooks.py · agno_engine.py | não contar tentativa com marcador de falha; combinar com teto de tentativas totais | Não | médio | S |

### 4.2 Eixo B — Único roteador

| # | Item | Arquivo(s) `:linha` | O que muda | Migração? | Risco | Esforço |
|---|------|---------------------|------------|-----------|-------|---------|
| B-i1 | Enforce app-level | agent_repo.py:90 · ai_engine.py:75-98 | ao salvar `is_router=True`, **rebaixar** qualquer outro roteador (semântica radio) OU rejeitar (ver P2) | — | baixo | S |
| B-i2 | Constraint no banco | nova migration Alembic (após 0034) | índice único parcial `WHERE is_router = 1` (Postgres partial index; SQLite também suporta) | **Sim** (dialect-agnóstica) | baixo | S |
| B-i3 | UI radio | AgentsManager.js:149,244 | ao ligar `isRouter` num agente, avisar/rebaixar o roteador atual; só roteador mostra `routing_targets` (já é assim) | — | baixo | S |

### 4.3 Eixo C — Postgres-only

| # | Item | Arquivo(s) `:linha` | O que muda | Risco | Esforço |
|---|------|---------------------|------------|-------|---------|
| C-i1 | Fail-fast Postgres | db/engine.py:63-75,98 · db/connection.py:41-47 | `resolve_database_url`: se não houver `DATABASE_URL`/`database.json` Postgres → **erro claro** (sem sqlite default) | médio | S |
| C-i2 | Remover branches SQLite | db/engine.py:98-153,170-180 · db/upsert.py:26-34 · db/__init__.py | tirar PRAGMAs, `check_same_thread`, `is_sqlite`/`get_sqlite_path`, `_attach_sqlite_pragmas`; upsert só `postgresql.insert`; sempre `pool_pre_ping` | médio | M |
| C-i3 | Alembic sem batch-mode | db/alembic/env.py:39,45,55,62 | `render_as_batch=False` (ou remover); manter migrations existentes (histórico) | baixo | S |
| C-i4 | Testes → Postgres | tests/conftest.py:184-198 · tests/support.py | exigir `WHATSBOT_TEST_DB_URL` Postgres; remover fallback temp-SQLite (ver P3) | médio | M |
| C-i5 | Limpar migração legada | db/migration_postgres.py · db/migrate_json.py · server/routes/admin.py · db/engine.py:44-82 | remover endpoint SQLite→PG, `database.json` read/write, JSON→SQLite | médio | M |
| C-i6 | Docs | CLAUDE.md · README | atualizar seções de backend/distribuição | baixo | S |

### 4.4 Falsos positivos / NÃO portar (D1, evitar over-engineering)

| Item do nexus | Por que NÃO portar |
|---|---|
| Pipeline de regras declarativo (`rules/pipeline.py`) | O bus de filters do WhatsBot (`filter.message.before_save`, `filter.reply.part`, …) já cobre e é **mais** extensível; reconstruir seria regredir. Portar só o gate de humano (A-i6). |
| Versionamento por trigger Postgres | WhatsBot versiona em código (`agent_repo.save`); triggers acoplam ao Postgres de forma desnecessária mesmo indo Postgres-only. |
| Hot-reload de código de tool (`importlib.reload`) | Tools code-in-DB rodam isoladas e o path está **OFF** (kill-switch `ai_tools_code_enabled`). Mexer toca segurança sem ganho. |
| `output_schema` (structured output) pro split | Alto acoplamento com `_extract_reply` multi-hop; `_salvage_split_array` já cobre a maioria. |
| Guarda `seq`/`is_current`/`commit` no orquestrador | O `_orchestrate` já resolve duplicação por cancel-and-respawn; reescrever sem ganho. |
| UTM replacer · escape por `offercode` · hot-reload `threading.Timer` | Domínio-específico da Escola / ganho marginal frente ao cache de config já existente. |

---

## 5. Fases / Roadmap

```
WAVE 0  A0(caracterização routing+hooks) 🔴
           │ (barreira: A0 valida o baseline antes de qualquer mudança de fluxo)
WAVE 1  A1 · A5 · A7 · A9 · B1  🟢        ← guardrails + único roteador (tudo em paralelo, sem dep. de routing)
           │
WAVE 2  A2 🔴 → A3 🔴 → A4 · A10 🟢       ← routing core: motivo ANTES de revisita; escalar+obs depois
           │ (A6 gating transfer_to_human: 🟢 config/convenção, a qualquer momento após A0)
           │ (A8 teto global tool_call_limit: 🟢 após confirmar AGNO — P5)
           │ (A11 call_limit-não-queima: 🟢 opcional, após P4)
           │ (barreira D5: Eixos A+B verdes em dual-backend ANTES de C)
WAVE 3  C0 🔴 → C1 · C2 🟢 → C3 · C4 🟢 → C5   ← Postgres-only, isolado, por último
```

Mapa item→fase: A1={A-i3,A-i5,A-i7,A-i9} · A2={A-i1} · A3={A-i2} · A4={A-i4} · A5={A-i6} · A6={A-i9→gating}… (ver cada fase).

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | A0 | Caracterização | 🔴 | baixo | golden-master do routing + hooks atuais commitado e verde |
| 1 | A1 | Guardrails (hooks.py) | 🟢 | baixo | success-aware + block-msgs + default_limit + lista, tudo verde |
| 1 | A5 | Gate humano | 🟢 | baixo | IA não responde conversa com humano atribuído |
| 1 | A7 | Observabilidade base | 🟢 | baixo | `routing_steps` carrega `reason` |
| 1 | B1 | Único roteador | 🟢 | baixo | impossível ter 2 `is_router` (app + índice + UI) |
| 2 | A2 | Threading do motivo | 🔴 [bloqueia A3] | médio | roteador VÊ o motivo do handoff no hop seguinte |
| 2 | A3 | Revisita controlada | 🔴 [dep A2] | médio | `roteador→comercial→roteador` roda no mesmo turno |
| 2 | A4 | Escalar pra humano | 🟢 [dep A3] | baixo | estourar o cap → `transfer_to_human` automático |
| 2 | A6 | Gating transfer_to_human | 🟢 | baixo | só o roteador tem a tool (config `tool_names`) |
| 2 | A8 | Teto global tool calls | 🟢 [P5] | médio | `Agent(tool_call_limit=…)` ativo (se AGNO suportar) |
| 3 | C0 | Fail-fast Postgres | 🔴 [bloqueia C1+] | médio | subir sem `DATABASE_URL` Postgres → erro claro |
| 3 | C1 | Remover branches SQLite | 🟢 [dep C0] | médio | engine/upsert/`__init__` sem `sqlite` |
| 3 | C2 | Alembic sem batch | 🟢 [dep C0] | baixo | `render_as_batch` removido; `upgrade head` ok em PG |
| 3 | C3 | Testes → Postgres | 🟢 [dep C1] | médio | suíte verde exigindo PG; sem temp-SQLite |
| 3 | C4 | Limpar migração legada | 🟢 [dep C1] | médio | `migration_postgres`/`migrate_json`/`database.json` removidos |
| 3 | C5 | Docs | 🟢 | baixo | CLAUDE.md reflete Postgres-only |

---

### Fase A0 — Caracterização do routing + hooks (rede de segurança)
**Objetivo:** capturar o comportamento ATUAL antes de mexer no fluxo crítico (disciplina do repo).
**Itens:**
- `[sequencial]` Testes golden-master cobrindo: (a) `run_with_routing` com `seen` barrando `A→B→A` (comportamento atual — vai MUDAR em A3, o teste será atualizado junto), (b) `check_hooks` `call_limit`/`requires_prior_call` atuais, (c) o cenário single-agent (sem handoff) inalterado. Base: `tests/test_routing_engine.py`, `tests/test_hooks.py`, `tests/test_agent_routing.py` já existem — estender.
**Pronto quando:** os novos testes de caracterização passam e documentam o baseline; nenhum comportamento novo ainda.

#### Status de execução — Fase A0
**Estado:** ✅ Concluída (commit `c533c79`)
- **O que foi feito:** golden-masters estendidos em `tests/test_hooks.py` e `tests/test_routing_engine.py`: (a) `seen` barrando `A→B→A` (fixado de propósito, atualizado em A3), (b) `call_limit`/`requires_prior_call` atuais, (c) single-agent sem handoff intocado.
- **Como foi feito / decisões:** Fake runner puro (sem DB/LLM) espelhando o contrato de `run_with_routing`; asserts no shape exato de `steps`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** suíte verde antes de qualquer mudança de comportamento.

---

### Fase A1 — Guardrails declarativos (tudo em `ai_engine/hooks.py`, 1 PR)
**Objetivo:** guardrails config-in-DB mais fortes, sem tocar routing.
**Itens:**
- `[paralelo]` **A-i3** success-aware: portar `_FAILURE_MARKERS` + inspeção de `result` de `executed[]`; `requires_prior_call` só satisfeito por prior sem marcador de falha.
- `[paralelo]` **A-i5** bloqueio que orienta: reescrever as 2 mensagens ([hooks.py:53,59](../ai_engine/hooks.py#L53)) nomeando a rota de escape (nome real da tool de transferência).
- `[paralelo]` **A-i7** `default_call_limit` global: nova config key `ai_tool_call_limit_per_tool`; aplicar em `check_hooks` a toda tool sem `call_limit` próprio.
- `[paralelo]` **A-i9** `requires_prior_call` como lista.
**Pronto quando:** `tests/test_hooks.py` estendido verde: prior que retornou "não existe" NÃO libera a dependente; tool sem `call_limit` respeita o default global; lista de priors funciona; mensagem de bloqueio cita a tool de escape.

#### Status de execução — Fase A1
**Estado:** ✅ Concluída (commit `50f3df3`)
- **O que foi feito:** `ai_engine/hooks.py` reescrito: `_FAILURE_MARKERS` do nexus + regra de prefixo "erro"; `requires_prior_call` aceita str OU lista; `default_call_limit` global via config `ai_tool_call_limit_per_tool` (0 = ilimitado, só vale pra tool sem `call_limit` próprio); mensagens de bloqueio citam a rota de escape (`transferir_agente`).
- **Como foi feito / decisões:** `_prior_satisfied` considera a ÚLTIMA chamada não-pulada da tool (retry após falha libera); hint de escape omitido quando a tool bloqueada É a própria escape.
- **Problemas / pendências:** markers por substring PT-BR seguem o risco mapeado na seção 6 (mitigado pela lista curta + prefixo).
- **Verificação:** `tests/test_hooks.py` 32 checagens verdes.

---

### Fase A5 — Gate de humano desacoplado do `ai_active`
**Objetivo:** a IA para de responder quando há humano no comando, independente de flag dessincronizado.
**Itens:**
- `[sequencial]` Em `_conversation_ai_active` ([messaging_service.py:~1077](../app/services/messaging_service.py#L1077)): além de `ai_active`, bloquear quando a conversa aberta tem `assignee_user_id` humano E `active_agent_key` não é agente IA. Fail-open no erro (mantém o default atual).
- `[paralelo]` Belt-and-suspenders: tratar a tag `transferido_atendente` (criada em [transfer_to_human.py:54-55](../agent/tools/transfer_to_human.py#L54)) como sinal de bloqueio; garantir que reabrir/limpar transferência remova a tag.
**Pronto quando:** teste: conversa com `assignee_user_id` humano + `ai_active` dessincronizado em 1 → IA **não** responde. Conversa normal (sem humano) → responde como hoje.

#### Status de execução — Fase A5
**Estado:** ✅ Concluída (commit `1ce9b18`)
- **O que foi feito:** `_conversation_ai_active` bloqueia quando: `ai_active=0`, OU `assignee_user_id` humano sem `active_agent_key`, OU o contato tem a tag `transferido_atendente` (constante `TRANSFER_TAG` em `transfer_to_human.py`). Fail-open em exceção.
- **Como foi feito / decisões:** tag lida fresh via `tag_repo.get_contact_tags` (não do cache); `_clear_transfer_tag` em `conversation_service` remove a tag (e sincroniza o cache do handler) ao reabrir com IA ligada e no toggle-ai enable.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/test_human_gate.py` 7 verdes (inclui o cenário dessincronizado `ai_active=1` + humano atribuído → IA calada).

---

### Fase A7 — Observabilidade base (`reason` no routing_steps)
**Objetivo:** tornar o routing diagnosticável (reusa coluna JSON, sem migração).
**Itens:**
- `[sequencial]` `run_with_routing` passa a montar `{from,to,depth,reason}` (o `reason` vem do `motivo` da tool call — depende de A2 carregar o motivo até o loop; se A7 for feito antes de A2, deixar `reason=None` e completar em A2/A10).
**Pronto quando:** `routing_steps` gravado carrega `reason` quando houve motivo.

#### Status de execução — Fase A7
**Estado:** ✅ Concluída (junto com A2, commit `8d24e5e`)
- **O que foi feito:** `run_with_routing` monta `steps` como `{from, to, depth, reason}`; o `reason` vem do motivo real da `transferir_agente` via callback `get_reason`.
- **Como foi feito / decisões:** feita junto com A2 (o motivo já estava threadado — evitou o passo intermediário `reason=None`).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/test_routing_engine.py` + `tests/test_routing_motivo.py` cobrem o shape com reason.

---

### Fase B1 — Único agente roteador
**Objetivo:** no máximo um `is_router=True` no sistema.
**Itens:**
- `[sequencial]` **B-i1** `agent_repo.save`: ao salvar `is_router=True`, rebaixar qualquer outro roteador (radio) — ver P2. Validar também na API ([ai_engine.py:97](../server/routes/ai_engine.py#L97)).
- `[sequencial]` **B-i2** migration Alembic (down_revision = `0034_conversation_origin`; manter head linear): índice único parcial `CREATE UNIQUE INDEX ... ON ai_agents (is_router) WHERE is_router = 1`. Guardada/idempotente (padrão das migrations do repo).
- `[paralelo]` **B-i3** UI: ao ligar `isRouter`, avisar que rebaixará o roteador atual; `routing_targets` só no roteador (já é assim).
**Pronto quando:** tentar criar/editar um 2º roteador → rebaixa o anterior (ou erro claro, conforme P2); o índice barra a violação no banco; teste `test_endpoints.py` cobre.

#### Status de execução — Fase B1
**Estado:** ✅ Concluída (commit `9bcdee2`)
- **O que foi feito:** semântica radio conforme P2(a): `agent_repo.save` com `is_router=True` rebaixa qualquer outro roteador na MESMA transação (`_demote_other_routers`, com bump de versão + snapshot em `ai_agents_history` do rebaixado); migration `0035_single_router` cria índice único parcial `ux_ai_agents_single_router ON ai_agents(is_router) WHERE is_router=1`, rebaixando duplicatas antes (mantém o de `updated_at` mais recente); banner de aviso na UI (`AgentsManager.js`) mostrando quem será rebaixado.
- **Como foi feito / decisões:** enforce no repo (não na rota) pra cobrir qualquer caller; `get_router()` novo.
- **Problemas / pendências:** nenhuma; migration guardada/idempotente/reversível.
- **Verificação:** `tests/test_agent_routing.py` 27 verdes (inclui rebaixamento + histórico); `test_alembic_hygiene` verde com a 0035.

---

### Fase A2 — Threading do motivo do handoff  🔴 [bloqueia A3]
**Objetivo:** o próximo hop enxerga POR QUE recebeu a conversa (o coração do hub-and-spoke).
**Itens:**
- `[sequencial]` Carregar o `motivo` da última `transferir_agente` do `executed_tools` até o `run_hop` (hoje `run_with_routing` não conhece a tool call — passar via callback/param).
- `[sequencial]` Em `_run_routing_hop` ([agent_run_service.py:66-92](../app/services/agent_run_service.py#L66)): injetar, antes do `context_messages` congelado, uma msg sintética estilo nexus: `{'role':'user','content': f'[REDIRECIONAMENTO de {from_agent}]\nMotivo: {motivo}\n\n(responda a mensagem atual do cliente)'}`.
- `[paralelo]` Fechar o `reason` do A7 com o motivo real.
**Pronto quando:** teste multi-hop: comercial faz `transferir_agente(roteador, motivo="oferta X não existe")` → o hop do roteador recebe o motivo no contexto (verificável no `messages` montado / nos `routing_steps`).

#### Status de execução — Fase A2
**Estado:** ✅ Concluída (commit `8d24e5e`, junto com A7)
- **O que foi feito:** `run_with_routing` recebe callback `get_reason()` (extrai o motivo da última `transferir_agente` de `executed_tools` via `_last_transfer_reason`); `_run_routing_hop` injeta a msg sintética estilo nexus DEPOIS do contexto congelado: `[REDIRECIONAMENTO de {agent}]\nMotivo: {motivo}\n\n(responda a mensagem atual do cliente)` como turno `user`.
- **Como foi feito / decisões:** `run_with_routing` continua puro (sem DB) — o motivo entra por callback; sem motivo, a sintética diz "sem motivo informado" (nunca omite o marcador de redirecionamento).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/test_routing_motivo.py` — o hop de destino recebe o motivo no `messages` montado e o `steps[].reason` carrega o texto.

---

### Fase A3 — Revisita controlada  🔴 [depende de A2]
**Objetivo:** `roteador → comercial → roteador` roda no mesmo turno (impossível hoje).
**Itens:**
- `[sequencial]` Remover `if nxt in seen: break` ([routing.py:47-49](../ai_engine/routing.py#L47)). Permitir revisita, sinalizando ao hop que é re-invocação (`is_reinvoke`) para reduzir/omitir o próprio output anterior (equivalente ao `skip_history` do nexus). Manter o guard imediato `nxt == current` (barra `A→A`).
- `[sequencial]` `MAX_ROUTING_DEPTH=5` → config `ai_max_route_depth`.
- ⚠️ **Ordem crítica:** só fazer DEPOIS de A2 — relaxar o `seen` sem o motivo threadado = ping-pong `A→B→A→B` até o cap.
- Atualizar o teste de caracterização A0 que fixava o `seen` (comportamento mudou de propósito).
**Pronto quando:** teste ponta-a-ponta: `roteador→comercial(oferta não existe, devolve)→roteador` executa os 3 hops no mesmo turno, bounded pelo cap; single-agent inalterado.

#### Status de execução — Fase A3
**Estado:** ✅ Concluída (commit `0c6cf96`, DEPOIS de A2 como exigido)
- **O que foi feito:** `if nxt in seen: break` removido; revisita permitida com `run_hop(nxt, is_reinvoke=nxt in seen)` (equivalente ao `skip_history` do nexus — a sintética de reinvoke pede resposta direta sem repetir o output anterior); guard imediato `nxt == current` mantido (barra `A→A`); `MAX_ROUTING_DEPTH` virou config `ai_max_route_depth` (default 5).
- **Como foi feito / decisões:** golden-master de A0 que fixava o `seen` atualizado de propósito no mesmo commit.
- **Problemas / pendências:** nenhuma.
- **Verificação:** e2e `roteador→comercial→roteador` roda os 3 hops no mesmo turno bounded pelo cap (`tests/test_routing_motivo.py`); single-agent inalterado (`test_routing_engine.py` 26 verdes).

---

### Fase A4 — Escalar pra humano ao esgotar o routing  🟢 [depende de A3]
**Objetivo:** rede de segurança — se ninguém resolve, cai no humano (não `break` silencioso).
**Itens:**
- `[sequencial]` No caller (`_continue_routing`/`run_turn`), ao detectar fim por depth/ciclo, disparar `transfer_to_human(motivo="Limite de roteamento atingido — nenhum agente resolveu")`. **Manter `run_with_routing` puro** (sem DB) — a chamada real fica no caller.
- `[paralelo]` **A10** = `track_step('routing_halted', {reason, chain})` no mesmo ponto.
**Pronto quando:** teste: cadeia que nunca resolve estoura o cap → `transfer_to_human` é chamado + `routing_halted` registrado.

#### Status de execução — Fase A4
**Estado:** ✅ Concluída (commit `6275d46`, inclui A10)
- **O que foi feito:** `run_with_routing` devolve `(result, steps, halted)` — `halted={"reason":"depth_exhausted","pending":<key>}` via `for/else` quando o cap estoura com handoff pendente; o caller (`_continue_routing`) dispara `handler._dispatch_tool(contact, "transfer_to_human", {"reason": "Limite de roteamento atingido ({chain}). Nenhum agente conseguiu resolver."})` (motivo literal do nexus) e registra `track_step("routing_halted", {reason, pending, chain}, status="error")`.
- **Como foi feito / decisões:** `run_with_routing` permaneceu puro (sem DB); a chamada forçada entra em `combined` com `forced: True`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** teste de cadeia que nunca resolve → `transfer_to_human` chamado 1× + `routing_halted` gravado (`tests/test_routing_motivo.py`).

---

### Fase A6 — Gating de `transfer_to_human` só no roteador  🟢
**Objetivo:** só o roteador escala pra humano (requisito de design).
**Itens:**
- `[sequencial]` Documentar/semear a convenção: `transfer_to_human` só no `tool_names` do agente roteador; spokes recebem `transferir_agente` (destino=roteador). O mecanismo já existe (`AgentSpec.tool_names` → `handler._select_active_tools`). Opcional: validação/aviso na UI quando um spoke tem `transfer_to_human`.
- Atualizar `seed_demo.py` para refletir o padrão.
**Pronto quando:** um agente spoke configurado sem `transfer_to_human` não recebe a tool no `_select_active_tools`; teste cobre.

#### Status de execução — Fase A6
**Estado:** ✅ Concluída (commit `8030fab`)
- **O que foi feito:** convenção hub-and-spoke semeada: `seed_demo.py` dá aos spokes `tool_names` explícito com `transferir_agente` (destino=roteador) e SEM `transfer_to_human` + sufixo de prompt orientando a devolução; roteador mantém `tool_names=None` (todas). UI (`AgentsManager.js`): hint âmbar quando um spoke seleciona `transfer_to_human` explicitamente.
- **Como foi feito / decisões:** convenção + aviso (não hard-block) — o mecanismo `AgentSpec.tool_names → _select_active_tools` já existia.
- **Problemas / pendências:** nenhuma.
- **Verificação:** teste cobre spoke sem `transfer_to_human` não recebendo a tool no `_select_active_tools`.

---

### Fase A8 — Teto global de tool calls no Agent  🟢 [ver P5]
**Objetivo:** freio de loop desenfreado de tools por run.
**Itens:**
- `[sequencial]` Confirmar suporte a `Agent(tool_call_limit=…)` na versão do AGNO em uso; se sim, passar de config `ai_tool_call_limit_total` em `_build_single_agent` ([agno_engine.py:274](../agent/agno_engine.py#L274)). Validar que atingir o teto devolve resposta graciosa (não exceção).
**Pronto quando:** run que tenta N+1 tool calls para no teto sem crashar; se AGNO não suportar, marcar P5 e pular.

#### Status de execução — Fase A8
**Estado:** ✅ Concluída (commit `dd3c574`; P5 confirmado)
- **O que foi feito:** AGNO 2.6.18 suporta `Agent(tool_call_limit=…)` com overflow gracioso (mensagem, não exceção) — confirmado. `_resolve_tool_call_limit()` em `agno_engine.py`: env `WHATSBOT_TOOL_CALL_LIMIT` > config `ai_tool_call_limit_total` (default 25) > `DEFAULT_TOOL_CALL_LIMIT`; `0` desliga.
- **Como foi feito / decisões:** resolvido por run (config viva sem restart); env vence pra operação/debug.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/test_tool_call_limit.py` verde (run que tenta N+1 para no teto sem crash).

---

### Fase C0 — Fail-fast Postgres  🔴 [bloqueia C1+]
**Objetivo:** deixar de aceitar SQLite; erro claro se não houver Postgres.
**Itens:**
- `[sequencial]` `resolve_database_url` ([db/engine.py:63-75](../db/engine.py#L63)): remover o fallback `sqlite:///…`; se `DATABASE_URL`/`database.json` ausente ou não-Postgres → `RuntimeError` com mensagem acionável ("configure DATABASE_URL=postgresql+psycopg://…").
**Pronto quando:** subir sem Postgres → erro claro no boot; subir com PG → normal.

#### Status de execução — Fase C0
**Estado:** ✅ Concluída (commit `ee6356d`)
- **O que foi feito:** `resolve_database_url` sem fallback SQLite: URL ausente ou não-`postgresql*` → `RuntimeError` acionável ("configure DATABASE_URL=postgresql+psycopg://…"), sempre com credenciais redigidas. Criado `.env` na raiz (gitignored) com `DATABASE_URL` (dev) e `WHATSBOT_TEST_DB_URL` (whatsbot_test) — `linux_start.sh` já carrega.
- **Como foi feito / decisões:** em C0 o `database.json` ainda era aceito (desde que Postgres); ele morreu de vez no C4.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/test_pg_only.py` (inclui teste de que a senha não vaza na mensagem de erro); boot dev normal com PG.

---

### Fase C1 — Remover branches SQLite  🟢 [depende de C0]
**Objetivo:** tirar todo o código SQLite-específico.
**Itens:**
- `[paralelo]` `db/engine.py`: remover `_attach_sqlite_pragmas`, `check_same_thread`, `is_sqlite`/`get_sqlite_path`, `_sqlite_path`, branch sqlite de `init_engine`; `pool_pre_ping=True` sempre; manter `prepare_threshold=None` do psycopg.
- `[paralelo]` `db/upsert.py:26-34`: só `postgresql.insert`, remover `_insert_for_current_dialect`.
- `[paralelo]` `db/__init__.py`: remover export `is_sqlite`; ajustar quem importava.
- Varrer os ~30 arquivos que referenciam `sqlite` (grep) e limpar usos remanescentes (repos, `_mapping.py`, `contact_search.py`, `filters/translate.py`).
**Pronto quando:** `grep -rn sqlite` (fora de migrations/histórico e testes) volta vazio; app sobe em PG; suíte verde.

#### Status de execução — Fase C1
**Estado:** ✅ Concluída (commits `95865c7` parte + `8139a6f`)
- **O que foi feito:** `db/engine.py` sem ramo SQLite (`_attach_sqlite_pragmas`, `check_same_thread`, `is_sqlite`/`get_sqlite_path`, `_sqlite_path`; `pool_pre_ping=True` sempre; `prepare_threshold=None` mantido) — entrou no commit C4 porque a remoção do `database.json` já obrigava mexer no arquivo. `db/upsert.py` importa `postgresql.insert` direto; `db/__init__.py` exports enxutos; `db/connection.py` perdeu `db_path` e o stamp automático de baseline SQLite; `plugins/migrator.py` `_portable_sql` traduz sempre; `seed_demo.py` lê `DATABASE_URL` (env/.env); docstrings "SQLite" atualizadas.
- **Como foi feito / decisões:** ⚠️ **ordem real do Eixo C reordenada: C0 → C3 → C4 → C1 → C2** (o plano dizia C1 antes de C3/C4). Motivo: verde-por-commit — remover os branches SQLite antes de os testes rodarem em PG quebraria a suíte; e o `admin.py` legado importava `is_sqlite`/`migration_postgres`.
- **Problemas / pendências:** menções a SQLite restantes são apenas históricas (migrations antigas, docstrings de shim) — nada funcional.
- **Verificação:** `grep -rni sqlite` no código do app volta só comentários históricos; suíte completa verde em PG.

---

### Fase C2 — Alembic sem batch-mode  🟢 [depende de C0]
**Objetivo:** simplificar o env de migração (batch é SQLite-only).
**Itens:**
- `[sequencial]` `db/alembic/env.py:39,45,55,62`: `render_as_batch=False`/remover a lógica `is_sqlite`. **Não** reescrever as migrations históricas (o `batch_alter_table` já aplicado é inócuo em PG via `op` normal; deixá-las). Novas migrations usam `op.alter_column`/`op.drop_constraint` direto.
**Pronto quando:** `alembic upgrade head` do zero em Postgres limpo funciona; head continua `0034_conversation_origin` (+ a nova de B-i2). Teste de hygiene (`test_alembic_hygiene`) verde.

#### Status de execução — Fase C2
**Estado:** ✅ Concluída (commit `ff847d0`)
- **O que foi feito:** `db/alembic/env.py` sem `render_as_batch`/lógica `is_sqlite`; `_resolve_connectable` cai em `resolve_database_url()` (env) quando a URL não veio programaticamente; `alembic.ini` com `sqlalchemy.url` vazio (sem default sqlite pra CLI).
- **Como foi feito / decisões:** migrations históricas com `batch_alter_table` NÃO foram reescritas (inócuas em PG), conforme o plano.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `alembic upgrade head` do zero em PG limpo funciona (é o reset de cada sessão de teste); `test_alembic_hygiene` verde; head = `0035_single_router`.

---

### Fase C3 — Testes → Postgres  🟢 [depende de C1]
**Objetivo:** a suíte roda contra Postgres.
**Itens:**
- `[sequencial]` `tests/conftest.py:184-198` / `tests/support.py`: exigir `WHATSBOT_TEST_DB_URL` Postgres; remover o fallback temp-SQLite (ver P3 — banco de teste efêmero por-sessão em PG, com schema recriado). `test_postgres_roundtrip.py` deixa de ser "opcional".
- Ajustar CI/scripts de teste locais para exportar a URL de teste.
**Pronto quando:** `pytest tests/` e `test_endpoints.py` verdes apontando pra um Postgres de teste; sem nenhum caminho SQLite nos testes.

#### Status de execução — Fase C3
**Estado:** ✅ Concluída (commit `27672a5`; executada ANTES de C4/C1 — ver nota em C1)
- **O que foi feito:** helper central `tests/pg.py`: `test_db_url()` (env `WHATSBOT_TEST_DB_URL` > linha do `.env` na raiz), `reset_schema` (`DROP SCHEMA public CASCADE` + `CREATE` + `alembic upgrade head`) e `init_test_engine(reset=True)` — P3(a). Bootstraps de `conftest`, `test_endpoints`, `test_audit`, `test_agent_routing`, `test_quick_replies_edge`, `test_gowa_plugin` migrados; `test_postgres_roundtrip` reescrito com banco efêmero `<test>_fresh` (skip sem CREATEDB) validando FKs CASCADE + `ux_ai_agents_single_router`.
- **Como foi feito / decisões:** guarda de segurança: o dbname PRECISA conter "test" (a menos de `WHATSBOT_TEST_DB_ALLOW_ANY=1`) — impossível apontar a suíte pro banco vivo `whatsbot` por engano.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `pytest tests/` e `tests/test_endpoints.py` (990 checagens) verdes contra `whatsbot_test` (PG 12.22 em 203.0.113.60).

---

### Fase C4 — Limpar migração legada  🟢 [depende de C1]
**Objetivo:** remover o que só existia para o mundo SQLite.
**Itens:**
- `[paralelo]` Remover `db/migration_postgres.py`, `db/migrate_json.py`, o endpoint `POST /api/admin/migrate-to-postgres` + status ([server/routes/admin.py](../server/routes/admin.py)) e o read/write de `database.json` ([db/engine.py:44-82](../db/engine.py#L44)).
- `[paralelo]` Frontend: remover a tela "Settings → Banco → Migrar agora" e chamadas relacionadas (a confirmar em `ConfigPanel.js`/componente de admin de banco).
**Pronto quando:** endpoints/telas de migração sumiram; `grep database.json` vazio; app sobe lendo só `DATABASE_URL`.

#### Status de execução — Fase C4
**Estado:** ✅ Concluída (commit `95865c7`)
- **O que foi feito:** removidos `db/migration_postgres.py`, `db/migrate_json.py`, os endpoints `POST /api/admin/migrate-to-postgres`(+`/status`), a tela Settings → Banco (`DatabaseSettings.js` + seção no `ConfigPanel.js`) e o read/write de `database.json` no `db/engine.py`. `repair_postgres_sequences` sobreviveu movido intacto pra `db/pg_maintenance.py`, exposto em `POST /api/admin/repair-sequences` (mesma permissão `database.manage`).
- **Como foi feito / decisões:** audit action `db.migrate_to_postgres` removida; characterization RBAC atualizada pro endpoint novo; `test_pg_only` ganhou o teste "database.json não é mais lido".
- **Problemas / pendências:** instalações antigas com `database.json` precisam mover a URL para a env `DATABASE_URL` (mensagem de erro do boot orienta).
- **Verificação:** `grep database.json` no código volta só docstrings/testes que documentam a remoção; app dev sobe lendo só a env.

---

### Fase C5 — Docs
**Objetivo:** a documentação reflete Postgres-only.
**Itens:**
- `[sequencial]` `CLAUDE.md`: seção "Banco de dados" (remover ordem de resolução com sqlite default, `database.json`, migrate-to-postgres), "Stack" (SQLite deixa de ser default), gotchas de WAL/SQLite. Registrar a decisão de distribuição (ver P1).
**Pronto quando:** CLAUDE.md sem instruções SQLite; nota de distribuição registrada.

#### Status de execução — Fase C5
**Estado:** ✅ Concluída (commit deste documento)
- **O que foi feito:** `CLAUDE.md` atualizado: Stack e seção "Banco de dados" Postgres-only (sem ordem de resolução com sqlite/`database.json`, sem migrate-to-postgres), gotchas de WAL/SQLite removidos, tabela de endpoints com `repair-sequences`, seção de testes com `WHATSBOT_TEST_DB_URL`, seção do motor com os guardrails/routing do Eixo A. Nota de distribuição registrada (P1a). Este plano preenchido fase a fase.
- **Como foi feito / decisões:** P1 assumido como (a) server/cloud-only — registrado no CLAUDE.md; decisão final de produto continua com o Thiago.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `grep -i sqlite CLAUDE.md` sem instruções operacionais (só menções históricas/decisão).

#### Follow-up pós-C5 (fix)
Duas descobertas na verificação final, corrigidas em commit próprio:
1. **`test_postgres_roundtrip` nunca rodava de verdade** — o fixture conectava no DB de manutenção `postgres` (encoding SQL_ASCII neste servidor → psycopg3 devolve bytes e o handshake do dialeto quebra com `TypeError`), que o skip mascarava como "sem CREATEDB". Fix: conexão admin no próprio banco de teste + `CREATE DATABASE … TEMPLATE template0 ENCODING 'UTF8'`. Os 3 testes agora executam (e passam).
2. **Bug real de instalação fresh no Postgres**: a migration 0013 semeia a inbox default com `id=1` explícito, o que NÃO avança a sequence — o 1º INSERT implícito em `inboxes` num banco recém-nascido colidiria na PK. Fix: `_run_alembic_upgrade()` (boot e testes) chama `repair_postgres_sequences` após o `upgrade head` (idempotente).

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Relaxar `seen` (A3) sem A2 | ping-pong `A→B→A→B` até o cap, custo de N chamadas LLM | **Ordem obrigatória A2→A3**; depth-cap baixo (ex. 4); motivo threadado faz o roteador decidir diferente e terminar |
| `_FAILURE_MARKERS` por substring PT-BR (A-i3) | tool que retorna "não encontrado" legítimo é falso-bloqueada | lista curta/configurável; preferir contrato de retorno `[ERRO]`; ver P4 |
| `default_call_limit` global (A-i7) | agente que depende de N chamadas de uma tool quebra | default alto/`0`=ilimitado; override por-tool vence |
| `Agent(tool_call_limit)` (A8) | versão do AGNO pode não suportar / lançar exceção ao atingir | confirmar (P5) antes; testar retorno gracioso; pular se não suportado |
| Único roteador (B) | rebaixar o roteador atual sem o usuário perceber | UI avisa (radio); ver P2 |
| Índice único parcial (B-i2) | dado existente com 2 roteadores → migration falha | migration rebaixa duplicatas antes de criar o índice; idempotente |
| Fail-fast Postgres (C0) | dev sem PG local não sobe mais | mensagem acionável + doc; usar o PG dev compartilhado (`203.0.113.60`) |
| Segredo na URL | `DATABASE_URL` com senha em log/erro | redação já usada em `admin`/`get_database_url`; manter ao remover código |
| Alembic head (B-i2 + C2) | quebrar linearidade (histórico com 0021/0032/0034 duplicados allowlisted) | nova migration revisa `0034_conversation_origin`; `test_alembic_hygiene` verde |
| Migrar testes (C3) | perder a rapidez do temp-SQLite; flakiness de PG | banco de teste efêmero por-sessão; schema recriado; rodar em CI com serviço PG |
| Distribuição EXE (C) | usuário final Windows não tem Postgres | **P1 — decisão de produto**; assumir server/cloud enquanto não distribuído |

---

## 7. Perguntas em aberto

**P1 — Modelo de distribuição pós-Postgres-only.** O CLAUDE.md descreve "EXE Windows zero-config" com SQLite. Postgres-only quebra isso (não há PG numa máquina de usuário final). Contexto: memória `refactor-rollout-context` diz que **não está distribuído** e a refatoração pode ser agressiva; alvo é server/Coolify + PG. Opções: (a) **produto vira server/cloud-only** (recomendado dado o rumo multi-inbox/RBAC/multicanal), (b) EXE passa a exigir Postgres embarcado/externo (complexo). ▶️ **Executado assumindo (a)** — registrado no CLAUDE.md; a decisão final de produto continua com o Thiago (reverter exigiria reintroduzir um backend local, não apenas reverter estes commits).

**P2 — Enforce do único roteador: rebaixar vs rejeitar.** Ao ligar `is_router` num 2º agente: (a) **auto-rebaixar** o roteador atual (semântica radio, melhor UX — recomendado) ou (b) **rejeitar** com erro "já existe um roteador (X)". ✅ Recomendação: **(a) auto-rebaixar com aviso na UI**; a API aplica atômico dentro de `agent_repo.save`.

**P3 — Banco de teste Postgres.** Como prover PG nos testes sem perder velocidade: (a) schema efêmero por-sessão no PG dev/CI (recomendado), (b) container PG local no CI, (c) `testcontainers`. ✅ Recomendação: **(a)** com `WHATSBOT_TEST_DB_URL` obrigatório; documentar no CLAUDE.md/CI.

**P4 — `call_limit` deve queimar em falha? (A-i11).** Ir além do nexus e **não** contar tentativa falha no `call_limit` (permite 1 retry após erro)? Risco: loop de tool que sempre falha. ✅ Recomendação: **não contar falha no `call_limit`, MAS** combinar com o teto global de tool calls (A8) como freio anti-loop. Marcar como opcional (fora do caminho crítico).

**P5 — AGNO suporta `Agent(tool_call_limit=…)`?** ✅ **Confirmado na A8**: AGNO 2.6.18 suporta e o overflow é gracioso (mensagem ao usuário, sem exceção). Implementado via config `ai_tool_call_limit_total` (env `WHATSBOT_TOOL_CALL_LIMIT` vence).

**P6 — Retorno automático ao roteador (fora de escopo aqui).** O QA do motor nota que hoje a conversa "gruda" no especializado até fechar. Este plano NÃO adiciona retorno automático por inatividade — o padrão hub-and-spoke (spoke devolve via `transferir_agente(roteador)`) + fechar a conversa já cobrem. Registrar como melhoria futura.

---

## 8. Apêndice — arquivos-chave por camada

**Motor de agentes (Eixo A):**
- `ai_engine/hooks.py` (A1, A9, A11)
- `ai_engine/routing.py` (A2, A3, A4, A10)
- `app/services/agent_run_service.py` (A2, A3, A4)
- `agent/tools/transferir_agente.py` (A2 — motivo)
- `agent/tools/transfer_to_human.py` (A4, A5, A6)
- `agent/agno_engine.py` (A8, A11)
- `app/services/messaging_service.py` (A5 — gate humano)
- `db/repositories/conversation_repo.py` (A5)
- `db/repositories/execution_repo.py` (A10)
- `config/settings.py` (A7/A8 config keys)
- `seed_demo.py` (A6 — padrão hub-and-spoke)

**Único roteador (Eixo B):**
- `db/repositories/agent_repo.py` · `server/routes/ai_engine.py` · `web/static/js/components/ai/AgentsManager.js`
- nova migration em `db/alembic/versions/` (down_revision `0034_conversation_origin`)

**Postgres-only (Eixo C):**
- `db/engine.py` · `db/upsert.py` · `db/__init__.py` · `db/connection.py`
- `db/alembic/env.py`
- `db/migration_postgres.py` · `db/migrate_json.py` (remover)
- `server/routes/admin.py` (remover endpoint de migração)
- `tests/conftest.py` · `tests/support.py`
- `CLAUDE.md`

**Testes:** `tests/test_hooks.py` · `tests/test_routing_engine.py` · `tests/test_agent_routing.py` · `tests/test_endpoints.py` · `tests/test_postgres_roundtrip.py`

---

## 9. Checklist de verificação (aplicar a cada mudança)

- [x] `tests/test_endpoints.py` verde (990 checagens, PG)
- [x] `pytest tests/` verde (hooks, routing, agent_routing, postgres_roundtrip)
- [x] Cenário ponta-a-ponta `roteador→comercial→roteador→transfer_to_human` funciona no mesmo turno (A2+A3+A4) — `tests/test_routing_motivo.py`
- [x] `requires_prior_call` bloqueia quando o prior falhou ("oferta não existe") (A1)
- [x] IA não responde conversa com humano atribuído (A5) — `tests/test_human_gate.py`
- [x] Impossível ter 2 agentes `is_router` (app + índice + UI) (B)
- [x] Migration round-trip em Postgres limpo; head Alembic linear (`test_alembic_hygiene` verde) (B, C2)
- [x] App sobe SÓ com `DATABASE_URL` Postgres; erro claro sem ela (C0) — `tests/test_pg_only.py`
- [x] `grep -rn sqlite` fora de histórico/migrations volta vazio (C1) — restam só comentários históricos
- [x] Sem segredo (senha da URL) em log/erro (teste dedicado em `test_pg_only`)
- [x] Telas novas/alteradas (UI do roteador) legíveis no modo escuro (classes `wa-*`)
- [x] Um refactor por commit; verde a cada fase; caracterização (A0) antes de A2/A3 — ordem real do Eixo C: C0→C3→C4→C1→C2 (nota na Fase C1)
