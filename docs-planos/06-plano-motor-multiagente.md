# Plano de implementação — Motor multi-agente com Agno (WhatsBot Pro)

> **Escopo:** evoluir o motor de IA do WhatsBot — hoje **AGNO já em produção** como motor de
> raciocínio + tool-calling — para um motor **multi-agente dirigido pelo banco**, com **config-in-DB**
> (agentes, prompts, variáveis) e **code-in-DB** (o código Python das tools vive em `ai_tools`),
> **installer de tools**, **roteamento por handoff** (`active_agent_key` na conversa), **isolamento do
> runner**, **hot-reload** e **decisão core-vs-plugin (híbrido)**.
>
> **Base de pesquisa:** [docs-pesquisa/06-motor-multiagente-agno.md](../docs-pesquisa/06-motor-multiagente-agno.md).
> **Referência concreta (produção):** [REF-gerenciamento-ia-code-in-db.md](REF-gerenciamento-ia-code-in-db.md)
> — o padrão code-in-DB do `gerenciamento-ia` (DDL `ai_*`, `tool_installer`, `dynamic_registry`,
> `model_factory`, handoff depth 5, structured output `LLMResponse`). Este plano replica a **lógica**
> dele em SQLAlchemy Core.
>
> **Banco de dados (decisão global Pro):** projetar para SQLite **e** Postgres. As colunas JSON
> (`model_config`, `tool_names`, `hooks_config`, `routing_targets`, `dependencies`) hoje são gravadas
> como **JSON-em-TEXT** nos dois backends (foi o que shipou na Fase 1) — endurecer para
> `JSON().with_variant(JSONB, "postgresql")` é trabalho **opcional de endurecimento** (Postgres ganha
> inspeção/índice), não pré-requisito.

---

## Estado atual (WF1, 2026-06-20)

Este plano é o **único dos 8 do WF1 que já tem código** — e mesmo assim está parcial/divergente.
O motor AGNO + config-in-DB foi "puxado pra frente" (Onda 5 antecipada), operando **por `phone`**, sem
inbox/conversa/RBAC/runtime. Fonte da verdade: `_RECONCILIACAO-WF1.md §4 (Plano 06)`, verificado
`arquivo:linha` contra o working tree em `b673a61` (idêntico a `58586e1` + os 5 arquivos do kill-switch
P62). **Este plano é COMPLETUDE + ENDURECIMENTO, não greenfield.**

### Legenda de fases

| Fase | Estado | Resumo (1 linha) |
|------|--------|-------------------|
| **F0 — Spike AGNO** | ✅ **feito** | AGNO em produção (`agent/agno_engine.py`); `agno` **já PINADO** (`agno>=2.6,<3`, `openai>=2,<3` em `requirements.txt:1-2`). Pin exato = endurecimento opcional. |
| **F1 — Fundação de dados** | 🟡 **parcial** | Tabelas `ai_*` + 3 `*_history` + repos com versão/snapshot + seed criados; **schema mais pobre** que o plano. `executions.agent_key/total_tokens/total_cost_usd` **já populados** (writers dedicados). |
| **F2 — Agente configurável** | 🔶 **divergente** | Via `agent/agent_factory.build_for_contact` + `agno_engine`, **não** `ai_engine/runner.run_conversation`; **sem `output_schema`/LLMResponse**; flag `ai_engine_enabled` default **OFF**. `executions` populado (critério atendido). |
| **F3 — Code-in-DB** | 🟡 **parcial** | Installer feito (`agent/ai_tool_installer.py`); tool nasce `enabled=False` (P63); **runner subprocess+RLIMIT+timeout NÃO feito** — `exec_module` in-process, mitigado por kill-switch P62 = **retrofit Onda 2**. |
| **F4 — Multi-agente por inbox** | ⬜ **nao_feito** | Bloqueado por 01/02 (sem `inboxes`/`conversations`, sem `default_agent_key`/`active_agent_key`); sem frontend `ai/`. |
| **F5 — Handoff/routing** | ⬜ **nao_feito** | Sem `transferir_para_outro_agente`, `ai_engine/routing.py`, `active_agent_key`, `routing_steps`. |
| **F6 — Hot-reload/versionamento** | 🟡 **parcial** | Hot-reload de **dado** (lê DB por request) + versionamento nos repos + evento `ai.config.changed`; **sem** `dynamic_registry` com cache/TTL, **sem** `history`/`rollback` API, **sem** `hooks_config`. Legado **não** aposentado. |
| **F7 — UI como plugin** | ⬜ **nao_feito** (opcional) | Tudo no core, como recomendado. Não fazer nesta versão. |

> Posição na sequência viva (relatório §4): **Onda 0** = VAZIA — tudo o que ela continha (pin AGNO,
> popular `executions`, `server/dev.py`) **já está FEITO e commitado** no working tree; o kill-switch P62
> também já está feito. **Onda 2** = retrofit P62 (isolar code-in-DB sobre o
> `SubprocessService` do plano 09). **Onda 4** = completar 06 (migration do schema rico + binding
> agente↔inbox + handoff/routing + API history/rollback + frontend). Ver §"Posicionamento por onda".

### O que JÁ EXISTE no código (âncoras verificadas)

- **Motor AGNO em produção:** `agent/agno_engine.py` é o motor de raciocínio + tool-calling; o loop
  OpenAI foi removido (P65 cumprida no sentido "motor é AGNO"). `requirements.txt:1` traz
  `agno>=2.6,<3` e `:2` `openai>=2,<3` — **já PINADOS** (com range), exatamente o que o plano/relatório
  pediam. Pin exato resolvido é endurecimento opcional.
- **Tabelas `ai_*`:** `db/tables.py:222-319` define `ai_agents`, `ai_prompts`, `ai_variables`,
  `ai_tools`, `ai_agents_history`, `ai_prompts_history`, `ai_tools_history` (migration
  `20260619_0007_ai_engine_tables.py`, `revision="0007_ai_engine_tables"`,
  `down_revision="0006_contact_mention"`). **`CORE_TABLES` tem 20 tabelas** (13 originais + 7 `ai_*`).
- **Colunas em `executions`:** `db/tables.py:157-159` tem `agent_key`, `total_tokens` (default 0),
  `total_cost_usd` (default 0.0). **`execution_steps` (`:164-173`) NÃO tem `agent_key`.** Nenhuma das
  duas tem `routing_steps`.
- **Repos + versionamento:** `db/repositories/{ai_agent_repo,ai_prompt_repo,ai_variable_repo,
  ai_tool_repo}.py` com `version += 1` + snapshot em `*_history`.
- **Factory single-agent:** `agent/agent_factory.py` — `build_for_contact(handler, contact)`
  (`:84-121`) retorna `None` quando `ai_engine_enabled` está OFF (`:91`), caindo no caminho legado;
  `render_template` (`:42`), `seed_default_agent` (`:59`).
- **Installer code-in-DB:** `agent/ai_tool_installer.py` — `_ensure_parent_package` (`:53`),
  `_import_tool_module` (`:68`) com `spec.loader.exec_module(module)` **in-process** (`:77`);
  comentário **⚠️ SECURITY DEBT (P62)** no cabeçalho (`:18`). Materializa
  `storages/ai_tools/<name>.py`, instala deps via `pkg_deps`, grava `install_status`.
- **Rotas + gates:** `server/routes/ai_engine.py` — CRUD REST de agents/prompts/variables/ai_tools;
  `save_ai_tool` cria tool `enabled=False` (gate P63 real). `config/settings.py:94`
  `ai_engine_enabled=False` (env `WHATSBOT_AI_ENGINE`), `:102` `ai_tools_code_enabled=False`
  (env `WHATSBOT_AI_TOOLS_CODE`). `server/routes/config.py` expõe/aceita ambos no GET/PUT `/api/config`.
  `server/app.py::create_app` só roda o installer code-in-DB quando `ai_tools_code_enabled` (gate P62).

### O que FALTA (delta para completar + endurecer)

1. **F0 — ✅ FEITO:** `agno` e `openai` **já pinados** em `requirements.txt:1-2` (`agno>=2.6,<3`,
   `openai>=2,<3`). Pin exato resolvido é endurecimento **opcional**, não falta. *(Onda 0 item #1 — já
   concluído.)*
2. **F1 (completar):** o **schema ficou mais pobre** que este plano. Falta:
   `ai_agents.description/hooks_config/routing_targets/is_router`; `ai_prompts.kind` + PK composta;
   `execution_steps.agent_key`; `executions.routing_steps`. **O writer de `executions` JÁ POPULA**
   `executions.agent_key/total_tokens/total_cost_usd` — via os writers dedicados `add_usage`/`set_agent_key`
   em `db/repositories/execution_repo.py:49-72` (chamados por `agent/execution.py` → `add_execution_usage`
   em `agent/handler.py:286,316` e `set_execution_agent_key` em `agent/handler.py:866,1001`). O
   `execution_repo.complete` (`:38-47`) **não** grava essas colunas, mas isso é por design — elas são
   escritas por funções separadas. → critério-de-pronto da F1/F2 (popular `executions`) **já atendido**.
   *(`executions` populado = ✅ FEITO; colunas do schema rico = Onda 4 item #19.)*
3. **F2 (alinhar/completar):** o caminho hoje é `agent_factory.build_for_contact` + `agno_engine`, não o
   `ai_engine/runner.run_conversation` HTTP-portável previsto. **Sem `output_schema`/`LLMResponse`** — o
   split usa parse JSON manual, **endurecido pelo PR #8** (`71ed713`, histórico do assistant em JSON;
   1/10 → 15/15 com tools). **P64 foi rebaixado** (Lote 3) a fase futura opcional → **não** é requisito
   de fase inicial. A flag default segue **OFF** (P65 = motor AGNO, não config-in-DB default).
4. **F3 (retrofit):** o installer roda `exec_module` **in-process** (mitigado só pelo kill-switch P62).
   O **runner subprocess + RLIMIT + timeout (P62 dia-1)** é **retrofit Onda 2**, reusando o
   `SubprocessService` do plano 09 (P67 = retrofit, não mais ADIADO).
5. **F4/F5 (nao_feito):** dependem de 01 (inbox/conversa) e 02 (binding por inbox); colunas de binding
   (`default_agent_key`/`active_agent_key`) e do schema rico (routing) faltam → migration futura
   (Onda 4).
6. **F6 (completar):** falta `ai_engine/dynamic_registry.py` (cache/TTL), endpoints
   `history`/`rollback`, `hooks_config` declarativo. O legado **não** foi aposentado (caminho principal
   com flag OFF; a aposentadoria só vale quando a config-in-DB virar default).

---

## 0. Premissas e amarração com o código atual

> ⚠️ **Drift de linhas:** os planos foram escritos sobre um snapshot pré-AGNO. As **âncoras semânticas**
> (nomes de função, registros de rota, sites do gate) continuam válidas; os **offsets numéricos não**.
> Na implementação, **localize por `grep`**, nunca por linha hardcoded. Os números abaixo são
> aproximados (marcados `~`) e servem só de orientação.

Pontos de integração reais (verificados no código):

- **Motor AGNO (substituiu o loop OpenAI):** `agent/agno_engine.py` é o motor de raciocínio +
  tool-calling; `agent/handler.py` (`AgentHandler`) é dono de tudo em volta (system prompt + filtros,
  histórico, lista de tools, eventos `llm.before/after`, usage, `track_step`, save, `split_messages`) e
  delega o miolo ao `agno_engine`. **Não** existe mais um loop OpenAI a remover (P65 cumprida no sentido
  motor).
- **Factory single-agent (config-in-DB):** `agent/agent_factory.py::build_for_contact` (`~:84`) lê
  `ai_agents` quando `ai_engine_enabled` está ligado; com a flag OFF (default), devolve `None` e o
  handler usa o caminho legado (prompt/modelo da config global). `seed_default_agent` (`~:59`) semeia o
  agente `default` a partir da config.
- **Tools core:** `agent/tools/__init__.py` — `CORE_TOOLS = [(schema, executor), ...]` (hoje
  `save_contact_info` e `transfer_to_human`). Contrato: schema dict + `execute(ctx, args)`.
- **Pipeline de mensagem:** `server/routes/webhook.py` — `register_routes(app, deps)`, `_run_one_cycle`,
  `_orchestrate` acumulam o batch e chamam o handler (`aprocess_message`). O envio é `_send_reply`, que
  aplica `filter.reply.*` e chama `gowa_client.send_message`. `_maybe_transcribe` cobre os filtros de
  transcrição. *(Offsets antigos `~:419/:794/:1026/:872/:990/:461/:683` — confirmar por grep.)*
- **Bus de filters/events:** `plugins/events.py` — `apply_filter`, `apply_filter_sync`, `emit`,
  `emit_with_filter`. Os filters `filter.system_prompt`, `filter.llm.messages`, `filter.llm.tools`,
  `filter.tool.args/result`, `filter.reply.*` já existem e devem continuar funcionando.
- **Installer code-in-DB (referência: loader de plugins):** `agent/ai_tool_installer.py` já espelha o
  padrão de `plugins/loader.py` (`_ensure_parent_package` / `spec_from_file_location`) sob o pacote
  `whatsbot_ai_tools`. Roda `exec_module` **in-process** (`~:77`) — ver retrofit Onda 2.
- **Camada de dados:** `db/tables.py` (Core, `Table` objects — `executions` `~:144`, `execution_steps`
  `~:164`, `tool_overrides` `~:202`, `ai_*` `~:222-319`); `db/engine.py::get_engine`;
  `db/connection.py::init_db` roda `alembic upgrade head`. Migrations em `db/alembic/versions/`
  (**HEAD real = `0008_plugin_installed_deps`**).
- **App wiring:** `server/app.py::create_app` monta `ServerDeps`, chama `register_routes` de cada
  módulo, gate do installer code-in-DB atrás de `ai_tools_code_enabled`. Lifespan sobe background tasks.

**Decisões de plataforma:** o motor é **core** (factory + run_conversation + roteamento + installer); a
**UI/CRUD** começa no core e pode ser extraída como plugin depois (Fase 7). O runner de tools code-in-DB
deve rodar em **subprocess isolado** (P62) — hoje in-process, retrofit pendente (Onda 2).

---

## Dependências de outros planos

Este plano **não** pode ser concluído sem partes de outros planos. Ordem de prontidão (sequência viva,
relatório §4):

1. **Plano 09 — Fundação Runtime** (BLOQUEANTE para o **retrofit P62** da Fase 3): entrega o
   `runtime/subprocess_service.py` (`SubprocessService`, `start_new_session` + `PR_SET_PDEATHSIG` +
   killpg + stale-kill). O tool_runner code-in-DB (hoje in-process) migra para esse serviço (P67 =
   retrofit). **Premissa invertida:** o plano 09 assumia que o tool_runner ainda não existia — ele
   **já existe in-process** → dependência cruzada **06⇄09** = Onda 2.
2. **Plano 01 — Inbox e conversas** (BLOQUEANTE para Fases 4-5): precisa das tabelas `conversations`
   (com `active_agent_key`) e do conceito Contact → ContactInbox → Conversation, de
   `resolve_conversation(phone, inbox_id)` e da cascata de IA **global → inbox → conversa (P5)**.
   **Fases 1-3 deste plano não dependem disso** (operam por `phone`, como hoje).
3. **Plano 02 — Canais e providers** (BLOQUEANTE para Fase 4): tabela `inboxes`/`channels` para
   `inboxes.default_agent_key`.
4. **Plano 03 — RBAC** (DESEJÁVEL para Fase 3/4): papel `adm` exclusivo para editar `ai_tools.code` /
   `ai_agents` / `ai_prompts`. **Enquanto o RBAC não existe, o RCE do code-in-DB já está mitigado pelo
   kill-switch `ai_tools_code_enabled` (default OFF, P62)** — o checklist "P0 gate admin-only" do
   relatório §6 está **OBSOLETO**. As telas de edição de código ficam atrás do gate provisório (única
   conta admin) e `changed_by` fica nullable até o RBAC.
5. **Plano 07 — Auditoria** (DESEJÁVEL, não bloqueante; P68–P75 adiados): as tabelas `*_history` já
   gravam snapshot; o plano 07 consome esses dados na tela de auditoria quando for retomado.

---

## Visão de arquitetura (alvo)

```
webhook (_run_one_cycle)
   └─> ai_engine.run_conversation(conversation_id, agent_key, text)   [core, in-process]
         ├─ agent_factory.build(agent_key, session_id=conversation_id)
         │     ├─ render_prompt (ai_prompts + ai_variables + placeholders)
         │     ├─ apply_filter_sync("filter.system_prompt", ...)
         │     ├─ resolve_tools(tool_names)  → union(CORE_TOOLS, plugin, ai_tools)
         │     │     └─ apply_filter_sync("filter.llm.tools", ...)
         │     ├─ model_factory: OpenAILike(base_url=LLM_API_BASE_URL), id="provider/modelo"
         │     └─ Agent(model, tools, session_id)   [output_schema=LLMResponse: futuro opcional, P64]
         ├─ agent.arun(text)  (loop de tool-calling do Agno — JÁ É O MOTOR HOJE)
         │     └─ tool de ai_tools → tool_runner (SUBPROCESS + RLIMIT + timeout — RETROFIT P62/Onda 2)
         ├─ handoff? → set active_agent_key + recursão (depth ≤ 5)                          (P60)
         └─ devolve partes  → _send_reply (filter.reply.*) → gowa
```

**P65 (cumprida = motor):** o **motor de raciocínio JÁ é o AGNO** (loop OpenAI removido). O que falta é
o **config-in-DB virar o caminho default**: hoje `ai_engine_enabled` é default **OFF** (`agent_factory`
devolve `None` → caminho legado). "Agno-first" significa motor, **não** config-in-DB ligado por padrão.
O `AgentHandler` segue dono do contexto/pipeline e delega o miolo ao `agno_engine`.

---

## Fase 0 — Spike de validação do Agno — ✅ FEITO

**Estado:** o spike foi concluído e o AGNO está **em produção** (`agent/agno_engine.py`). Uma resposta
real do proxy Techify via `OpenAILike` funciona; usage/tool-calls são extraídos de `run_output.metrics`
(`RunMetrics.input_tokens/output_tokens`) e gravados via `AgentHandler._record_usage_tokens`. A
transcrição de áudio/descrição de imagem continua em chamadas diretas ao cliente OpenAI (não-agênticas).

**O que JÁ EXISTE:** `requirements.txt:1-2` traz `agno>=2.6,<3` e `openai>=2,<3` (**já pinados** com
range); `agent/agno_engine.py` monta `OpenAILike` + `Agent` único por mensagem (stateless por request),
embrulha cada tool num `agno.tools.function.Function` preservando filtros/eventos, e extrai reply via
`_extract_reply` (última msg `assistant` sem tool calls). **`server/dev.py:64` já passa
`ai_engine_enabled`** ao handler (`ai_engine_enabled=settings.get("ai_engine_enabled", False)`, com
comentário explícito de paridade com `main.py` — sem ele a config-in-DB nunca ligaria sob
`uvicorn --reload`).

**O que FALTA:** nada bloqueante nesta fase. **Pin de `agno`/`openai` já feito** (`requirements.txt:1-2`)
e **`server/dev.py` já propaga `ai_engine_enabled`** (`:64`). Único endurecimento **opcional**: trocar o
range por pin exato da versão resolvida em produção, para reprodutibilidade máxima. *(Esforço baixo;
não é requisito.)*

**Critério de pronto:** ✅ atingido — `requirements.txt` com `agno`/`openai` pinados (range);
`pip install -r requirements.txt` reproduzível; `server/dev.py` propaga a flag (`:64`).

---

## Fase 1 — Fundação de dados (tabelas `ai_*` + ALTERs) — 🟡 PARCIAL

**Estado:** as tabelas `ai_*` + 3 `*_history` + repos com versão/snapshot + seed do agente `default`
**já existem** (migration `0007_ai_engine_tables`). O writer de `executions` **já popula**
`agent_key/total_tokens/total_cost_usd` (writers dedicados). **O que falta é o schema rico** — o schema
ficou mais pobre que este plano. Esta fase passa a ser **completar o schema rico**.

### O que JÁ EXISTE no código

- **Migration `20260619_0007_ai_engine_tables.py`** (`revision="0007_ai_engine_tables"`,
  `down_revision="0006_contact_mention"`) — **JÁ CONSUMIDA, não renumerar.**
- **`db/tables.py:222-319`** define:
  - `ai_agents` (`:222`): `agent_key` (PK), `display_name`, `prompt_key`, `model_config` (TEXT JSON),
    `tool_names` (TEXT JSON, null="all"), `enabled`, `version`, `updated_at`.
  - `ai_prompts` (`:239`): `prompt_key` (PK simples), `body`, `version`, `updated_at`.
  - `ai_variables` (`:251`): `name` (PK), `value`, `category`, `updated_at`.
  - `ai_tools` (`:262`): `name` (PK), `description`, `code`, `dependencies` (TEXT JSON), `enabled`,
    `install_status` (`pending|installing|ok|failed`), `install_error`, `installed_deps`, `version`,
    `updated_at`.
  - `ai_agents_history` / `ai_prompts_history` / `ai_tools_history` (`:286/:298/:310`): snapshot por save.
- **`executions` (`:157-159`)**: já tem `agent_key`, `total_tokens` (default 0), `total_cost_usd`
  (default 0.0) **e os writers que as populam**: `execution_repo.add_usage`/`set_agent_key`
  (`db/repositories/execution_repo.py:49-72`), expostos por `agent/execution.py`
  (`add_execution_usage`/`set_execution_agent_key`) e chamados no pipeline vivo —
  `add_execution_usage` em `agent/handler.py:286,316` (toda mensagem) e `set_execution_agent_key`
  em `agent/handler.py:866,1001` (caminho config-in-DB).
- **Repos** com `version += 1` + snapshot; **`seed_default_agent`** semeando o agente `default`.

### O que FALTA (delta para completar)

Criar **uma nova migration** (não tocar a `0007`) com os ALTERs do schema rico:

> **Numeração Alembic (P82 — linear):** `down_revision = head real no momento de implementar` (hoje
> `0008_plugin_installed_deps`); `revision`/número = **próximo livre (≥ 0009)**. **NÃO** usar
> `0006`/`0007`/`0008` como slot novo — ramifica a cadeia e **quebra o boot**. A `0007_ai_engine_tables`
> **já foi consumida (não renumerar)**. Se a sequência viva colocar 03/01 antes (o que é provável), o
> head no momento de implementar o 06 já será posterior a `0008` — encadear nesse head real.

- **`ALTER TABLE ai_agents`** adicionar: `description TEXT`, `hooks_config` (JSON/TEXT default `{}`),
  `routing_targets` (JSON/TEXT default `[]`), `is_router INTEGER default 0`.
- **`ALTER TABLE ai_prompts`** adicionar `kind TEXT default 'default'`. (PK composta `(prompt_key, kind)`
  exige recriação de tabela no SQLite via `batch_alter_table` — só fazer se o multi-kind for usado;
  caso contrário manter PK simples e tratar `kind` como coluna comum no MVP.)
- **`ALTER TABLE execution_steps`** adicionar `agent_key TEXT` (hoje ausente, `:164-173`).
- **`ALTER TABLE executions`** adicionar `routing_steps TEXT` (JSON dos saltos de handoff).
- **`db/tables.py`** — refletir as colunas novas nos `Table` objects correspondentes.
- **`db/repositories/execution_repo.py`** — **popular `executions.agent_key/total_tokens/total_cost_usd`
  já está FEITO** via os writers dedicados `add_usage`/`set_agent_key` (`:49-72`), ligados ao pipeline por
  `agent/execution.py` + `agent/handler.py:286,316,866,1001`. (O `complete` em `:38-47` não grava essas
  colunas — por design; elas têm writers próprios.) **Não há trabalho aqui.**

**Endurecimento opcional (Postgres):** migrar as colunas JSON-em-TEXT (`model_config`, `tool_names`,
`hooks_config`, `routing_targets`, `dependencies`) para `JSON().with_variant(JSONB, "postgresql")` —
JSONB ganha inspeção/índice GIN. Não bloqueia nada; é polimento. Manter o caminho único de
encode/decode JSON nos repos (portabilidade SQLite/Postgres).

**Migration Alembic:** escrever à mão (não confiar no autogenerate para `ALTER` em SQLite — usar batch
mode quando recriar tabela). Garantir SQLite **e** Postgres.

**Critério de pronto:** `alembic upgrade head` aplica os ALTERs nos dois backends; `ai_agents` ganha
`description/hooks_config/routing_targets/is_router`, `execution_steps` ganha `agent_key`, `executions`
ganha `routing_steps`; **o writer de `executions.agent_key/total_tokens/total_cost_usd` já popula essas
colunas** (✅ feito — não é critério pendente); `tests/test_endpoints.py` verde.

---

## Fase 2 — Um agente configurável (agent_factory + run_conversation) — 🔶 DIVERGENTE

**Estado:** já existe um agente configurável vindo do banco — porém pelo caminho `agent/
agent_factory.build_for_contact` + `agno_engine`, **não** pelo `ai_engine/runner.run_conversation`
HTTP-portável previsto. O split usa **JSON manual** (endurecido pelo PR #8), **não** `output_schema`. A
flag `ai_engine_enabled` é default **OFF** (não "agno-default"). Esta fase passa a ser **convergir o
caminho** (extrair o runner portável) e **alinhar P64** (opcional), sem reconstruir o motor.

### O que JÁ EXISTE no código

- **`agent/agent_factory.py`**: `build_for_contact(handler, contact)` (`~:84`) carrega o agente do banco
  via `ai_agent_repo` quando `ai_engine_enabled` está ligado; com a flag OFF (`~:91`), retorna `None` e o
  handler usa o caminho legado. `render_template(body, variables)` (`~:42`) resolve placeholders;
  `seed_default_agent` (`~:59`) garante paridade com a config global.
- **`agent/agno_engine.py`**: monta `OpenAILike(base_url=LLM_API_BASE_URL, ...)` + `Agent` único por
  mensagem; histórico montado das `messages` (P58 — fonte de verdade, não `agno_sessions`); filtros
  `filter.system_prompt`/`filter.llm.messages`/`filter.llm.tools`/`filter.tool.*` preservados; usage de
  `run_output.metrics`.
- **`server/routes/ai_engine.py`**: já expõe `GET/POST/PUT/DELETE /api/ai/{agents,prompts,variables,
  tools}` (CRUD read+write). `ai_engine_enabled` exposto em `/api/config` (`server/routes/config.py`).

### O que FALTA (delta para convergir + completar)

- **Extrair o runner portável `ai_engine/runner.py::run_conversation(conversation_id_or_phone,
  agent_key, text) -> str`** (assinatura HTTP-portável, pesquisa §7.4), encapsulando o que hoje está
  espalhado entre `build_for_contact` + `agno_engine` + handler. Internamente:
  1. resolve `agent_key` (param ou `default`).
  2. monta histórico das últimas N `messages` (P58); `apply_filter_sync("filter.llm.messages", ...)`.
  3. `agent = factory.build(...)`; `result = await agent.arun(text)`.
  4. persiste assistant message + usage (`usage_repo`) + `executions`/`execution_steps` **com
     `agent_key`** (+ `total_tokens`/`total_cost_usd` — writers `add_execution_usage`/
     `set_execution_agent_key` **já existem**; o runner extraído reusa-os).
  5. devolve as partes; o envio segue **fora** do motor (no webhook), preservando `filter.reply.*`.
- **`ai_engine/agent_factory.py::build(agent_key, session_id)`** (generalizar o `build_for_contact`
  atual): carregar config do agente, `render_prompt` (extrair o helper compartilhado de
  `AgentHandler._build_system_prompt`), `resolve_tools(tool_names)` = union de `CORE_TOOLS` + plugin +
  `ai_tools` respeitando `tool_overrides.enabled` e a **precedência P61 (código > plugin > banco)** —
  banco nunca sequestra tool core (o registry atual já no-op-a colisão; **falta o badge na UI**).
- **`ai_engine/model_factory.py::build(agent_cfg) -> OpenAILike`** — multi-provider `provider/modelo`
  (`openai/...`, `google/...`, `anthropic/...`) com auto-detecção de prefixo; tuning em cascata
  `model_config` do agente > variável `{param}_{agent_key}` (`ai_variables`) > global `{param}`
  (REF §3); mapeia `temperature`, `top_p`, `max_output_tokens→max_completion_tokens`,
  `thinking_level→reasoning_effort`.
- **`output_schema`/`LLMResponse` (P64) — FUTURO OPCIONAL, NÃO dia-1.** **P64 foi rebaixado** (Lote 3) a
  fase futura opcional: o split por JSON manual foi endurecido pelo PR #8 (`71ed713`; 1/10 → 15/15 com
  tools) e é robusto o suficiente. **Não marcar como requisito de fase inicial.** Reavaliar só
  se/quando handoff/routing (F5) exigirem saída estruturada de verdade. Quando entrar, viveria em
  `ai_engine/schemas.py` (`LLMResponse{mensagens_para_usuario, private_message}`, `silent_output` por
  código, não pelo LLM).
- **Integração no webhook** — o gate hoje é a flag `ai_engine_enabled` (bool, default **OFF**) no
  handler, **não** `config["ai_engine"]="agno"`. Quando a config-in-DB virar default, o webhook chama
  `ai_engine.run_conversation(phone, agent_key="default", text=combined)`; até lá, `build_for_contact`
  devolve `None` e o handler legado responde (paridade garantida). `agent_key="default"` nesta fase
  (conversa/inbox vêm nas Fases 4-5).
- **Endpoints read-only de validação** já existem (`GET /api/ai/agents`, `.../{key}`).

**Critério de pronto:** com `ai_engine_enabled` ligado, uma mensagem real recebe resposta **com paridade
de comportamento** ao handler legado (mesmo prompt, modelo e tools core/plugin), agora com a config
vinda do banco; `executions` registra `agent_key="default"` + tokens/custo (writers **já existem** —
`add_execution_usage`/`set_execution_agent_key`); o caminho legado segue funcionando com a flag OFF (default). Filters de plugin
(`horario_funcionamento`, `blacklist`, `auto_signature`) continuam atuando. O `output_schema` **não** é
exigido aqui (P64 rebaixado).

---

## Fase 3 — Code-in-DB: tool installer + runner isolado — 🟡 PARCIAL (runner = retrofit Onda 2)

**Estado:** o **installer já existe** e funciona (`agent/ai_tool_installer.py`): materializa
`storages/ai_tools/<name>.py`, instala deps via `pkg_deps`, importa via `importlib`, grava
`install_status`; tool criada via API nasce `enabled=False` (gate P63). **Mas o runner isolado
(subprocess + RLIMIT + timeout — P62 dia-1) NÃO foi feito:** o código do banco roda `exec_module`
**in-process** (`~:77`), mitigado apenas pelo **kill-switch `ai_tools_code_enabled`** (default OFF).
**Isolar isso é RETROFIT da Onda 2**, sobre o `SubprocessService` do plano 09 (P67 = retrofit).

### 3.1 Tool installer — ✅ FEITO

`agent/ai_tool_installer.py` espelha o loader de plugins. Fluxo (disparado ao salvar/ativar uma tool e
no boot para tools `enabled=1`, **somente quando `ai_tools_code_enabled`**):
1. **Materializa** — escreve `ai_tools.code` em `storages/ai_tools/<name>.py` (user-writable, no
   `.gitignore`). `name` validado por regex `^[a-z][a-z0-9_]{0,63}$`.
2. **Instala deps** — `install_status='installing'`; `pip install` via `pkg_deps` das `dependencies`.
   **P66 — SEM allowlist no MVP** (risco aceito; não criar `dep_allowlist.py`). Falha →
   `install_status='failed'` + `install_error`; tool **não** entra no registry (fail-closed).
3. **Importa/recarrega** — `_ensure_parent_package` (`~:53`) cria `whatsbot_ai_tools`;
   `_import_tool_module` (`~:68`) usa `spec_from_file_location` + `exec_module` (`~:77`).
4. **Valida assinatura** — `_validate_schema` (`~:84`): módulo deve expor `(schema dict + execute(ctx,
   args))`. Errado → `failed`.
5. **Grava status** — `install_status='ok'` + `version += 1` + snapshot em `ai_tools_history`.

### 3.2 Runner isolado — ⬜ NÃO FEITO = RETROFIT (Onda 2, P62/P67)

> **⚠️ Dívida de segurança documentada.** O comentário **SECURITY DEBT (P62)** em
> `agent/ai_tool_installer.py:18` registra que o `exec_module` roda in-process. **O RCE está mitigado
> por padrão pelo kill-switch** (`ai_tools_code_enabled` default OFF) — o operador tem de ligar
> conscientemente. **O checklist "P0 gate admin-only" do relatório §6 está OBSOLETO** (a mitigação
> shipou depois dele).

O isolamento **não é mais ADIADO**: virou **retrofit** sobre o `SubprocessService` que o **plano 09**
(Onda 1) entrega. Premissa invertida — o tool_runner já existe in-process, então em vez de "criar do
zero" o trabalho é **migrar** o ponto de execução para subprocesso. Quando o 09 entregar o serviço:

- **`ai_engine/tool_runner.py` + `ai_engine/tool_worker.py`** — worker dedicado (ex.
  `python -m ai_engine.tool_worker`) que recebe `(tool_name, args, ctx_safe)` via IPC (stdin/stdout JSON
  ou socket) e devolve resultado/erro, **executado pelo `SubprocessService` do plano 09**
  (`start_new_session` + `PR_SET_PDEATHSIG` POSIX, P29 — **só Linux/Docker** no escopo Pro).
- **Limites de SO no worker:** `RLIMIT_CPU`, `RLIMIT_AS` (memória), `timeout` rígido por chamada (mata
  runaway). **seccomp/AppArmor é upgrade pós-MVP** — depende de o container Coolify rodar com
  privilégios (a confirmar nos testes do Thiago; ver Perguntas em aberto).
- **Least privilege:** o worker **não** recebe a chave do LLM, nem credenciais admin, nem conexão de
  escrita ao banco principal; só o `ctx` mínimo.
- **Fail-closed:** timeout/crash do worker vira feedback de erro ao LLM, **nunca** derruba o webhook.
- **Eventos no bus (P28):** o `SubprocessService` emite `subprocess.crashed/restarted`; o supervisor,
  `task.crashed`. O tool_runner pode reutilizá-los.

Só a execução do código de `ai_tools` sai do processo; `CORE_TOOLS`/plugin (código revisado) seguem
in-process.

### 3.3 Mitigações de segurança (estado)

- **Kill-switch `ai_tools_code_enabled` (P62, FEITO)** — default **OFF**; gate em `server/app.py`
  (installer só roda com a flag); env `WHATSBOT_AI_TOOLS_CODE`. **Mitigação imediata do RCE.**
- **Gate "IA cria tool" (P63, FEITO)** — `save_ai_tool` em `server/routes/ai_engine.py` cria a tool
  `enabled=False`/`install_status='pending'` até um humano (ADM, quando houver RBAC) revisar e ativar.
- **Isolamento do runner (P62 dia-1) — RETROFIT (Onda 2)** — subprocess + `RLIMIT_*` + timeout, sobre o
  `SubprocessService` do plano 09. É a barreira de contenção real depois do kill-switch.
- **SEM allowlist de dependências (P66)** — risco aceito; não criar `dep_allowlist.py`.
- **Edição de código = papel ADM (Plano 03)** — quando o RBAC existir, escrita de `ai_tools`/`ai_agents`/
  `ai_prompts` exige grupo `adm`. Hoje, gate provisório (única conta admin) + kill-switch.
- **Auditoria before/after** — toda criação/edição grava snapshot em `*_history` (consumido pelo
  plano 07 quando retomado).
- **Validação estática leve (AST)** — recusa/alerta imports obviamente perigosos antes de instalar
  (defesa em profundidade barata, não barreira principal). **Não feito** — opcional, complementa o
  isolamento.

### Endpoints REST (estado)

- `POST/PUT /api/ai/tools` (cria/edita) → dispara installer. ✅ existe (`save_ai_tool`).
- `GET /api/ai/tools`, `GET /api/ai/tools/{name}` (lista/detalhe + `install_status`/`install_error`). ✅
- `DELETE /api/ai/tools/{name}`. ✅
- `POST /api/ai/tools/{name}/reinstall` (re-roda installer). ⬜ **falta** (ou via re-save).

**Critério de pronto (completar/endurecer):** criar/editar tool pelo painel reflete sem deploy (já
funciona com a flag ligada); tool com dependência inexistente falha com `install_status='failed'` +
`install_error` legível (já funciona); **após o retrofit:** tool com `while True` é morta pelo timeout do
runner sem travar o webhook, e o worker **não** tem a chave do LLM no ambiente; tool proposta pela IA
nasce `pending` e só funciona após ativação humana (já funciona, P63); toda mudança aparece em
`ai_tools_history`.

---

## Fase 4 — Multi-agente por inbox + CRUD completo (UI) — ⬜ NÃO FEITO

**Objetivo:** vários agentes, escolha por inbox, painel de edição. **Bloqueado pelos Planos 01 e 02**
(sem `inboxes`/`conversations`, sem colunas de binding). **P60 confirmado, "sem trabalho agora"** até o
01 entregar as inboxes — `ai_agents` segue como fonte única de verdade (o multi-agente Team foi removido
em `58586e1`). É **Onda 4**.

Migration (**rodar depois** das tabelas de inbox/conversa dos planos 01/02):
- **Migration `ai_agent_links` (P60 — binding por coluna):**
  - `ALTER TABLE inboxes ADD COLUMN default_agent_key TEXT REFERENCES ai_agents(agent_key)`.
  - `ALTER TABLE conversations ADD COLUMN active_agent_key TEXT REFERENCES ai_agents(agent_key)`.

  > **Numeração Alembic (P82):** `down_revision = head real no momento de implementar` (será posterior a
  > `0008_plugin_installed_deps` — o 01/02/03 já terão consumido `0009`/`0010`/...); `revision` = próximo
  > livre (**≥ 0009**). A `0007_ai_engine_tables` **já foi consumida — não renumerar**. Esta migração
  > `ai_agent_links` (futura) e a migração do schema rico da F1 encadeiam **uma na outra** se entrarem na
  > mesma onda (a 2ª aponta para a 1ª).

Pipeline (depende do plano 01):
```python
conversation = resolve_conversation(phone, inbox_id)   # plano 01
# cascata de IA global → inbox → conversa (P5); contacts.ai_enabled sai do gate
if not ia_ativa(global, inbox, conversation): return    # humano atende
agent_key = conversation.active_agent_key or inbox.default_agent_key or "default"
reply = await ai_engine.run_conversation(conversation.id, agent_key, text)
```
- `run_conversation` passa a usar `conversation.id` como `session_id` (sessão = conversa). Histórico
  continua vindo de `messages` filtrado pela conversa (P58).

UI (frontend — Preact + HTM, sem build step; regras de **modo escuro** do CLAUDE.md obrigatórias — usar
`wa-*` e `.wa-field`):
- `web/static/js/components/ai/AgentsManager.js` — lista/CRUD de agentes (nome, prompt, modelo, tools,
  hooks, routing_targets, is_router, enabled).
- `web/static/js/components/ai/PromptsEditor.js` — CRUD `ai_prompts` (preview de placeholders).
- `web/static/js/components/ai/VariablesEditor.js` — CRUD `ai_variables`.
- `web/static/js/components/ai/ToolsEditor.js` — editor de código das tools (ADM-only), com
  `install_status`, `install_error`, botão "Reinstalar", histórico de versões, **badge de colisão P61**.
- Rota SPA + entrada no `GearMenu`.

Endpoints REST (escrita — já existem em `server/routes/ai_engine.py`; falta gatear por ADM no plano 03):
- `POST/PUT/DELETE /api/ai/agents`, `/api/ai/prompts`, `/api/ai/variables`.
- `PUT /api/inboxes/{id}/default-agent` (ou no plano 02).

**Critério de pronto:** criar 2+ agentes pelo painel; associar agentes distintos a inboxes distintas;
mensagem em cada inbox respondida pelo agente certo; editar prompt/modelo/tools reflete no comportamento;
tudo versionado e auditado.

---

## Fase 5 — Roteamento por handoff (active_agent_key) — ⬜ NÃO FEITO

**Objetivo:** handoff sequencial entre IAs preservando a conversa (REF §4 — `active_agent_key` na
conversa, depth ≤ 5). Confirma **P60** (handoff cobre multi-agente dentro da conversa). Depende da F4 (e
do plano 01 para `conversations.active_agent_key` + da migração `routing_steps`/`agent_key` da F1).
É **Onda 4**.

- Tool core nova `transferir_para_outro_agente(agent_key, motivo)` em
  `agent/tools/transferir_agente.py` (adicionar a `CORE_TOOLS`). Schema + `execute` sinalizam o alvo de
  handoff (não enviam mensagem; o motor intercepta). Agente intermediário fica silencioso (controlado
  por código, REF §4/§5).
- `ai_engine/routing.py` — `run_with_routing(conversation_id, text, depth=0)` (REF §4):
  1. `if depth > MAX_ROUTING_DEPTH (5): fallback`.
  2. monta agente do `active_agent_key`, executa.
  3. **proteção de loop:** se re-invocar o mesmo agente, desliga o histórico para não repetir.
  4. se a tool de handoff foi chamada e `target ∈ routing_targets` do agente atual:
     `conversations.active_agent_key = target`; registra salto em `executions.routing_steps` (+
     `execution_steps.agent_key`); recursão `depth+1`, **mesma `session_id` = conversation.id**.
  5. valida alvo contra `routing_targets` (anti-rota-arbitrária).
- Integração com handoff **humano:** reusar `transfer_to_human` (já existe) — o "agente que recebe" pode
  ser o humano (plano 01). **Nota:** o `transfer_to_human` atual chama `set_ai_enabled(False)`; com a
  cascata P5 do plano 01, isso evolui para desligar a IA **na conversa** (não no contato).

**Critério de pronto:** agente comercial transfere para suporte; conversa continua com suporte mantendo
contexto; `executions.routing_steps` registra os saltos; loop infinito é cortado em depth 5 com mensagem
de fallback; transferência para humano funciona.

---

## Fase 6 — Hot-reload, versionamento e history/rollback — 🟡 PARCIAL

**Objetivo:** mudar config sem restart; hooks declarativos; navegar histórico e fazer rollback.

### O que JÁ EXISTE

- **Hot-reload de dado:** o caminho config-in-DB **lê o banco por request** (factory monta o agente do
  DB a cada mensagem) → editar agente/prompt/variável reflete sem restart. Evento `ai.config.changed`
  emitido no PUT do painel (`server/routes/ai_engine.py::_emit_changed`).
- **Versionamento:** os repos já fazem `version += 1` + snapshot em `*_history` a cada save.

### O que FALTA (completar)

- **`ai_engine/dynamic_registry.py`** (REF §2): cache em memória de `ai_agents`/`ai_prompts`/
  `ai_variables`/`ai_tools` com **invalidação por evento + fallback de polling TTL (~60s)**. **P57 — 1
  worker do uvicorn no MVP**, então invalidação por evento (`ai.config.changed` → limpa cache no mesmo
  processo) + TTL curto bastam. Não projetar para multi-worker (reavaliar com `LISTEN/NOTIFY` em
  Postgres só se um dia subir `--workers > 1`). **Dois níveis de reload:** *dado* = invalidação a quente;
  *código* (`ai_tools.code`) = passa pelo installer (`importlib.reload` só daquele módulo). Hoje o
  reload existe por "lê o DB por request"; falta a camada de **cache** para tirar a leitura do hot path.
- **Endpoints `GET /api/ai/{agents|tools|prompts}/{key}/history` e `POST .../rollback/{version}`** —
  **não existem.** O rollback de tool re-dispara o installer. Os dados já estão em `*_history`.
- **Hooks declarativos (`hooks_config`)** — **não existem** (a coluna falta no schema atual; criada na
  F1). `hooks_config` (`call_limit`, `requires_prior_call`) viram closures montados na factory; estado
  vive na closure e reseta a cada mensagem. Mapeia para `filter.tool.args` do bus.

### Aposentar o legado — NÃO AGORA (P65 = motor, não config-in-DB default)

> **Importante:** P65 está **cumprida no sentido motor** (loop OpenAI removido; o motor de raciocínio é o
> AGNO). **Mas o caminho principal de runtime ainda é o legado** (`AgentHandler` com prompt/modelo da
> config global), porque `ai_engine_enabled` é default **OFF**. A aposentadoria do legado só faz sentido
> **quando a config-in-DB virar o default e tiver paridade comprovada em produção** — não há fase
> dedicada de remoção meses depois, mas também **não** se remove o legado enquanto a flag estiver OFF por
> padrão. Migrar os 4 call sites de transcrição (`_maybe_transcribe`) e `save_assistant_message` para o
> motor só quando a virada acontecer.

**Critério de pronto:** o `dynamic_registry` cacheia config-in-DB e invalida por evento; editar
prompt/agente reflete na próxima mensagem sem restart (já vale); editar código de tool recarrega só
aquele módulo; histórico de versões navegável e rollback funcional (API nova); `hooks_config`
declarativo funciona; testes verdes.

---

## Fase 7 — (Opcional) Extrair UI/CRUD como plugin — ⬜ NÃO FEITO (recomendado: não fazer)

**Objetivo:** se o cliente quiser desligar/versionar a UI à parte (pesquisa §5.5 — híbrido).

- Manter o **núcleo no core** (factory, run_conversation, routing, installer, tabelas `ai_*`).
- Mover telas Preact + endpoints de CRUD para `storages/plugins/ai_engine_ui/`, consumindo uma API
  estável do core. O core **não** depende do plugin. As tabelas `ai_*` permanecem no core (plugin não
  cria tabela sem prefixo `plugin_<id>_`).

**Critério de pronto:** desabilitar o plugin remove a UI sem afetar o motor. **Default: não fazer nesta
versão — começar tudo no core (pesquisa §5.5).**

---

## Posicionamento por onda (sequência viva — relatório §4)

> Ondas: **0** = endurecimento do que já shippou · **1** = plano 09 (`SubprocessService`) ·
> **2** = retrofit P62 (isolar code-in-DB) · **3** = RBAC (03) + Inbox (01) · **4** = completar 06 ·
> **5+** = 02, 04, 05 · 08 (depois de 01/05/03).

| Onda | Itens do plano 06 |
|------|-------------------|
| **0** | **VAZIA — tudo já FEITO e commitado.** Pin `agno`/`openai` (F0) ✅ (`requirements.txt:1-2`). Popular `executions.agent_key/total_tokens/total_cost_usd` (F1/F2) ✅ (writers `add_execution_usage`/`set_execution_agent_key`). `server/dev.py` passar `ai_engine_enabled` ✅ (`server/dev.py:64`). Kill-switch P62 ✅. *(Endurecimento opcional restante: pin exato da versão de `agno`.)* |
| **1** | — (06 não tem item próprio; consome o `SubprocessService` do plano 09 na onda seguinte). |
| **2** | **Retrofit P62/P67**: migrar `ai_tool_installer` de `exec_module` in-process → subprocesso isolado (RLIMIT+timeout) sobre o `SubprocessService` do 09 (F3.2). |
| **3** | — (06 espera o RBAC/Inbox; binding agente↔inbox é trabalho do 06 que **depende** do 01). |
| **4** | Migration do schema rico (F1: `ai_agents.description/hooks_config/routing_targets/is_router`, `ai_prompts.kind`, `execution_steps.agent_key`, `executions.routing_steps`). Convergir runner (F2). Binding agente↔inbox + handoff/routing (F4/F5). `dynamic_registry` + `history`/`rollback` + `hooks_config` (F6). Frontend AI (F4). Alinhar P64 só se handoff exigir. |
| **5+** | — (06 não depende de 02/04/05; F7 opcional fica para o fim, se for feita). |

---

## Resumo de artefatos

**Migrations Alembic:**
- `0007_ai_engine_tables` — **JÁ CONSUMIDA** (tabelas `ai_*` + colunas `executions.agent_key/
  total_tokens/total_cost_usd`). **Não renumerar.**
- **Schema rico (F1, futura)** — `down_revision = head real no momento` (hoje `0008_plugin_installed_deps`;
  provavelmente posterior na ordem real); número **≥ 0009**. ALTER em `ai_agents`/`ai_prompts`/
  `execution_steps`/`executions`.
- **`ai_agent_links` (F4, futura)** — `down_revision` = head real (posterior; depois de 01/02);
  número **≥ 0009**; encadeia na migration do schema rico se entrarem juntas.

**Módulos backend que JÁ EXISTEM:** `agent/agno_engine.py`, `agent/agent_factory.py`,
`agent/ai_tool_installer.py`, `db/repositories/{ai_agent_repo,ai_prompt_repo,ai_variable_repo,
ai_tool_repo}.py`, `server/routes/ai_engine.py`, tabelas `ai_*` em `db/tables.py`,
`seed_default_agent`.

**Módulos backend a criar (completar):** `ai_engine/{__init__,runner,agent_factory,model_factory,
routing,tool_runner,tool_worker,dynamic_registry}.py` (refatorar/extrair o que hoje está em
`agent/agent_factory.py` + `agent/agno_engine.py`; `tool_runner`/`tool_worker` = retrofit Onda 2);
`agent/tools/transferir_agente.py` (F5); `ai_engine/schemas.py` só se P64 voltar (opcional). **Sem
`dep_allowlist.py` (P66).**

**Edições backend:** migration do schema rico + `db/tables.py` (colunas novas); `.gitignore` já cobre
`storages/ai_tools/`; webhook (virada para `run_conversation` quando a config-in-DB for default).
*(Já FEITO, sem trabalho: `db/repositories/execution_repo.py` já popula
`agent_key/total_tokens/total_cost_usd` via `add_usage`/`set_agent_key`; `requirements.txt:1-2` já pina
`agno`/`openai`; `server/dev.py:64` já passa `ai_engine_enabled`.)*

**Frontend (a criar — F4):** `web/static/js/components/ai/{AgentsManager,PromptsEditor,VariablesEditor,
ToolsEditor}.js` + rota SPA + entrada no `GearMenu` (regras de modo escuro; badge de colisão P61).

**Dependências:** `agno>=2.6,<3` + `openai>=2,<3` (**já pinados** em `requirements.txt:1-2`). Sem JS novo.

---

## Perguntas em aberto

1. **Workers do uvicorn (Coolify).** ✅ **DECIDIDO: 1 worker (P57)** no MVP — invalidação por evento +
   cache curto (TTL ~60s) bastam. `LISTEN/NOTIFY` (Postgres) só se a carga exigir multi-worker.
2. **Sessão: Agno `db` vs montar de `messages`.** ✅ **DECIDIDO: montar de `messages` (P58)** — uma fonte
   de verdade. **Já é o comportamento do `agno_engine`.**
3. **`ai_variables` dedicada vs prefixo em `config`.** ✅ **DECIDIDO: tabela dedicada (P59)** — **já
   criada.**
4. **Granularidade agente↔inbox.** ✅ **DECIDIDO: coluna `default_agent_key` por inbox (P60)**; handoff
   cobre multi-agente na conversa. **Direção confirmada, sem trabalho agora** (espera o plano 01).
5. **Colisão de nome de tool.** ✅ **DECIDIDO: código > plugin > banco (P61)** — backend já no-op-a a
   colisão; **falta o badge na UI.**
6. **Isolamento do runner code-in-DB.** ✅ **DECIDIDO: subprocess + `RLIMIT_*` + timeout (P62)** no
   dia-1, só Linux/Docker (`PR_SET_PDEATHSIG`, P29). **HOJE: in-process, mitigado pelo kill-switch
   `ai_tools_code_enabled` (default OFF).** Isolamento = **retrofit Onda 2** sobre o `SubprocessService`
   do plano 09 (P67). **A confirmar nos testes do Thiago:** o container Coolify roda com privilégios para
   seccomp/AppArmor? (não bloqueia o MVP).
7. **Gate da IA criando tools.** ✅ **DECIDIDO: gate humano (P63)** — tool nasce `enabled=0`/`pending`.
   **Já FEITO** (`save_ai_tool` cria `enabled=False`).
8. **Structured output (split).** ✅ **DECIDIDO: `output_schema` Pydantic do Agno (P64) — REBAIXADO a
   fase futura opcional (Lote 3).** O split por JSON manual foi endurecido pelo PR #8 (`71ed713`). **Não
   é requisito de fase inicial.** Reavaliar só se handoff/routing exigirem.
9. **Coexistência legacy × Agno.** ✅ **DECIDIDO: P65 cumprida (motor é AGNO; loop OpenAI removido).** O
   **config-in-DB** segue atrás de `ai_engine_enabled` (default OFF) — "Agno-first" = motor, não
   config-in-DB default. Aposentar o legado só quando a config-in-DB virar default com paridade.
10. **Allowlist de dependências.** ✅ **DECIDIDO: SEM allowlist no MVP (P66)** — risco aceito;
    revisitar no endurecimento. Não criar `dep_allowlist.py`.
11. **Reaproveitar o `SubprocessService` do plano 09 para o tool_runner?** ✅ **DECIDIDO (Lote 3):
    P67 = RETROFIT** (não mais ADIADO). O runner já existe in-process; quando o plano 09 (Onda 1)
    entregar o `SubprocessService`, migrar o installer para ele (Onda 2).
