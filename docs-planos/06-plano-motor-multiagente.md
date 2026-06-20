# Plano de implementação — Motor multi-agente com Agno (WhatsBot Pro)

> **Escopo:** substituir o `AgentHandler` singleton (`agent/handler.py`) por um motor multi-agente
> dirigido pelo banco, usando **Agno 2.x**, com **code-in-DB** (agentes, prompts, variáveis e o
> **código Python das tools** vivem no banco), **installer de tools** (materializa `.py` +
> `pip install` + `importlib.reload` + `install_status`), **roteamento por handoff**
> (`active_agent_key` na conversation), **mitigações de segurança**, **hot-reload** e
> **decisão core-vs-plugin (híbrido)**.
>
> **Base de pesquisa:** [docs-pesquisa/06-motor-multiagente-agno.md](../docs-pesquisa/06-motor-multiagente-agno.md).
> **Referência concreta (produção):** [REF-gerenciamento-ia-code-in-db.md](REF-gerenciamento-ia-code-in-db.md)
> — o padrão code-in-DB do `gerenciamento-ia` (DDL `ai_*`, `tool_installer`, `dynamic_registry`,
> `model_factory`, handoff depth 5, structured output `LLMResponse`). Este plano replica a **lógica**
> dele em SQLAlchemy Core.
> Este plano assume as decisões já tomadas pelo cliente (code-in-DB; motor embutido com runner de tools
> isolado; híbrido core+plugin; inbox/conversas como core; GOWA provider-plugin; RBAC simples).
>
> **Decisão arquitetural-chave (P65 — Agno-first):** o motor é construído **diretamente sobre o Agno
> desde a primeira fase**. NÃO há período longo de coexistência com o `AgentHandler` legado: o handler
> antigo permanece apenas como **fallback mínimo e curtíssimo** durante o gap de paridade de uma única
> fase, e é aposentado assim que o Agno cobre o comportamento atual. Isso reduz código duplicado e evita
> manter dois caminhos vivos por meses.
>
> **Banco de dados (decisão global Pro):** projetar para SQLite **e** Postgres. As colunas JSON
> (`model_config`, `tool_names`, `hooks_config`, `routing_targets`, `dependencies`) usam
> `JSON().with_variant(JSONB, "postgresql")` — em Postgres viram **JSONB** (melhor para inspeção/índice),
> em SQLite ficam JSON-em-TEXT. Postgres é o backend de referência do Pro.
>
> **Restrição:** nenhum código de produção é alterado por este plano — ele é o roteiro de execução.

---

## 0. Premissas e amarração com o código atual

Pontos de integração reais (verificados no código):

- **Handler singleton**: `agent/handler.py` — `AgentHandler.__init__` (L43) carrega `system_prompt`,
  `model`, `_tool_schemas`/`_tool_executors` (L78-85) a partir de `CORE_TOOLS`. Métodos relevantes:
  `aprocess_message` (path async usado pelo webhook), `_dispatch_tool`, `_build_system_prompt`,
  `register_plugin_tools`, `register_plugin_prompts`, `refresh_tool_overrides`, `known_tool_names`.
- **Tools core**: `agent/tools/__init__.py` — `CORE_TOOLS = [(schema, executor), ...]`
  (hoje só `save_contact_info` e `transfer_to_human`). Contrato: schema dict + `execute(ctx, args)`.
- **Pipeline de mensagem**: `server/routes/webhook.py` — `register_routes(app, deps)` (L419),
  `_run_one_cycle` (L794) e `_orchestrate` (L1026) acumulam o batch e chamam
  `agent_handler.aprocess_message(...)` (L872, L990). O envio é `_send_reply` (L461), que aplica
  `filter.reply.*` e chama `gowa_client.send_message`. `_maybe_transcribe` (L683) cobre os filtros de
  transcrição.
- **Bus de filters/events**: `plugins/events.py` — `apply_filter`, `apply_filter_sync`, `emit`,
  `emit_with_filter`. Os filters `filter.system_prompt`, `filter.llm.messages`, `filter.llm.tools`,
  `filter.tool.args/result`, `filter.reply.*` já existem e devem continuar funcionando.
- **Loader de plugins (referência para o installer)**: `plugins/loader.py` —
  `_ensure_parent_package` (L263) cria o pacote sintético `whatsbot_plugins`; `_import_package`
  (L273) / `_import_submodule` (L291) usam `importlib.util.spec_from_file_location`. O installer de
  `ai_tools` deve **espelhar** esse padrão sob o pacote `whatsbot_ai_tools`.
- **Camada de dados**: `db/tables.py` (Core, `Table` objects — `executions` L144, `execution_steps`
  L158, `tool_overrides` L192); `db/engine.py::get_engine` (L156); `db/connection.py::init_db` (L36)
  roda `alembic upgrade head`. Migrations em `db/alembic/versions/` (última: `0006_contact_mention`).
- **Repos**: `db/repositories/` (`config_repo`, `execution_repo`, `tool_override_repo`, …). Padrão:
  `with get_engine().begin()/connect()`.
- **App wiring**: `server/app.py::create_app` (L65) monta `ServerDeps` (L47) e chama os
  `register_routes` de cada módulo (L304+). O lifespan (L143) sobe background tasks (L163).

**Decisões de plataforma:** o motor é **core** (núcleo: factory + run_conversation + roteamento +
installer); a **UI/CRUD** começa no core e pode ser extraída como plugin depois (Fase 7). O runner de
tools code-in-DB roda em **subprocess isolado** (não in-process).

---

## Dependências de outros planos

Este plano **não** pode ser concluído sem partes de outros planos. Ordem de prontidão:

1. **Plano 01 — Inbox e conversas** (BLOQUEANTE para Fases 4-5): precisa das tabelas
   `conversations` (com a coluna `active_agent_key` adicionada aqui) e do conceito
   Contact → ContactInbox → Conversation, além de `resolve_conversation(phone, inbox_id)` e da flag
   `agent_bot_enabled` por inbox. **Fases 1-3 deste plano não dependem disso** (operam por `phone`,
   como hoje).
2. **Plano 02 — Canais e providers** (BLOQUEANTE para Fase 4): tabela `inboxes`/`channels` para
   `inboxes.default_agent_key`. GOWA já como provider-plugin exige os 3 runtimes core (lifecycle de
   plugin awaited, supervisor de background-tasks, serviço de subprocesso). O **runner de tools**
   (Fase 3) reaproveita o "serviço de subprocesso gerenciado" desse plano se ele existir; caso
   contrário, este plano cria um runner mínimo próprio.
3. **Plano 03 — RBAC** (BLOQUEANTE para Fase 3): papel `adm` exclusivo para editar `ai_tools.code` /
   `ai_agents` / `ai_prompts`. Enquanto o RBAC não existe, as telas de edição de código ficam atrás de
   um gate provisório (única conta admin atual) e a coluna `changed_by` fica nullable.
4. **Plano 07 — Auditoria** (DESEJÁVEL, não bloqueante): as tabelas `*_history` são criadas aqui e já
   gravam snapshot before/after; o plano 07 consome esses dados na tela de auditoria.

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
         │     ├─ model_factory: OpenAILike(base_url=LLM_API_BASE_URL), id="provider/modelo"  (P64)
         │     └─ Agent(model, tools, session_id, output_schema=LLMResponse)               (P64)
         ├─ agent.arun(text)  (loop de tool-calling do Agno; saída estruturada Pydantic)
         │     └─ tool de ai_tools → tool_runner (SUBPROCESS + RLIMIT + timeout, §segurança)  (P62)
         ├─ handoff? → set active_agent_key + recursão (depth ≤ 5)                          (P60)
         └─ devolve LLMResponse.mensagens_para_usuario  → _send_reply (filter.reply.*) → gowa
```

**P65 (Agno-first):** o `ai_engine` é o **único** caminho de runtime a partir da Fase 2. O
`AgentHandler` legado sobrevive apenas atrás da flag `config["ai_engine"]` como **fallback de
emergência durante o gap de paridade** (Fase 2 → Fase 3), e é aposentado já na Fase 6. Não há
coexistência longa nem flag mantida por meses.

---

## Fase 0 — Spike de validação do Agno (de-risk)

**Objetivo:** provar que o Agno 2.x convive com o stack atual antes de qualquer schema.

Passos:
1. Criar venv limpo isolado e instalar `agno` (série 2.x) + as deps atuais
   (`requirements.txt`). Verificar choque de versões de `pydantic`/`sqlalchemy`/`openai` (gotcha
   conhecido — pesquisa §7.3). Registrar as versões resolvidas.
2. Script throwaway (fora de `agent/`, em `/tmp` ou `scratch/`): instanciar
   `Agent(model=OpenAILike(base_url=LLM_API_BASE_URL, api_key=<techify>, id="openai/gpt-4o"),
   output_schema=LLMResponse)` no formato **`provider/modelo`** (P64, multi-provider via
   OpenRouter/Techify), uma tool simples, e rodar `await agent.arun("oi")` contra o proxy Techify real.
3. Validar que `agent.arun()` retorna o `output_schema` Pydantic (`LLMResponse`) + metadados de tool
   calls + usage (tokens) — necessário para alimentar `executions`/`usage` e o split de mensagens (P64).
4. Medir latência e overhead de instanciar o `Agent` por chamada (esperado desprezível).

**Critério de pronto:** Agno instalado sem quebrar `pip install -r requirements.txt`; uma resposta real
do proxy Techify via `OpenAILike`; usage/tool-calls extraíveis do resultado. Documentar as versões
pinadas que entrarão no `requirements.txt`.

**Dependências novas (pip):** `agno>=2.6,<3` (versão exata a fixar após o spike). Sem JS novo nesta
fase.

---

## Fase 1 — Fundação de dados (tabelas `ai_*` + ALTERs)

**Objetivo:** schema completo no core, sem ainda mudar o pipeline.

Arquivos a criar:
- `db/alembic/versions/20260620_0007_ai_engine.py` — migration:
  - `CREATE TABLE ai_agents` — colunas (REF §1): `agent_key` (PK, TEXT, identidade),
    `display_name`, `description`, `prompt_key` (FK `ai_prompts`), `prompt_template` (TEXT),
    `model_config` (JSON/JSONB default `{}` — `{model_id, temperature, top_p, ...}`), `tool_names`
    (JSON/JSONB default `[]`), `hooks_config` (JSON/JSONB default `{}`), `routing_targets`
    (JSON/JSONB default `[]`), `is_router` (INT), `enabled` (INT default 1), `version` (INT default 1),
    `created_at`, `updated_at`. Colunas JSON usam `JSON().with_variant(JSONB, "postgresql")` (JSONB em
    Postgres, JSON-em-TEXT em SQLite — decisão global de banco).
  - `CREATE TABLE ai_agents_history` — `id` PK autoinc, `agent_key`, `version`, `snapshot` (JSON),
    `changed_by` (INT nullable, users.id), `changed_at`.
  - `CREATE TABLE ai_prompts` — `prompt_key`, `kind` (default `default`), `body` (TEXT), `version`,
    `updated_at`; PK composta `(prompt_key, kind)`.
  - `CREATE TABLE ai_variables` — **tabela dedicada (P59)**: `name` (PK), `value` (TEXT), `category`
    (para agrupar na UI), `description`, `updated_at`. Decisão P59: tabela própria, **não** prefixo em
    `config` — tem semântica e `category` próprios, melhor para a UI e para o tuning por agente
    (`{param}_{agent_key}`).
  - `CREATE TABLE ai_tools` — `name` (PK, identidade = `call_type` em `usage`), `description`,
    `code` (TEXT), `dependencies` (JSON/JSONB default `[]`), `enabled`, `install_status`
    (`pending|installing|ok|failed`), `install_error` (TEXT), `version`, `created_by`/`updated_by`
    (INT nullable), `created_at`, `updated_at`. Tool criada pela IA nasce com `enabled=0` /
    `install_status='pending'` (gate humano — P63).
  - `CREATE TABLE ai_tools_history` — análoga a `ai_agents_history` (snapshot inclui `code`).
  - `ALTER TABLE executions ADD COLUMN agent_key TEXT`, `routing_steps TEXT` (JSON),
    `total_tokens INTEGER`, `total_cost_usd REAL`.
  - `ALTER TABLE execution_steps ADD COLUMN agent_key TEXT`.
  - **NÃO** adicionar `active_agent_key`/`default_agent_key` aqui — **P60** define agente↔inbox via
    coluna `default_agent_key` em `inboxes` (e `active_agent_key` em `conversations` para handoff); ambas
    pertencem às tabelas dos planos 01/02 e são adicionadas na Fase 4 (depois delas).
- `db/tables.py` — adicionar os `Table` objects `ai_agents`, `ai_agents_history`, `ai_prompts`,
  `ai_variables`, `ai_tools`, `ai_tools_history` (Core, espelhando a migration). Adicionar as novas
  colunas aos `Table` `executions`/`execution_steps` existentes.
- `db/repositories/ai_agent_repo.py`, `ai_prompt_repo.py`, `ai_variable_repo.py`, `ai_tool_repo.py` —
  CRUD via Core (`get`, `list_all`, `save`, `set_enabled`, `delete`). Cada `save` faz `version += 1` e
  grava snapshot em `*_history`. Decodificar/encodar JSON (`model_config`, `tool_names`, `hooks_config`,
  `routing_targets`, `dependencies`) num único caminho (regra de portabilidade SQLite/Postgres —
  JSON-em-TEXT em ambos).
- Seed idempotente (bloco no `db/connection.py::init_db` ou módulo `db/seeds/ai_default_agent.py`): um
  agente `default` cujo `prompt_template` = `config["system_prompt"]` atual, `model_config` =
  `{model_id, temperature}` lidos da config global, `tool_names` = nomes de `CORE_TOOLS`. Garante
  paridade com hoje.

**Migration Alembic:** gerar com `alembic revision -m "ai engine tables"` e revisar (não confiar 100%
no autogenerate para os `ALTER` em SQLite — usar batch mode se necessário).

**Critério de pronto:** `alembic upgrade head` cria as tabelas em SQLite e Postgres; o seed insere o
agente `default`; `tests/test_endpoints.py` continua verde; nenhum caminho de runtime ainda usa as
tabelas.

---

## Fase 2 — Um agente configurável (agent_factory + run_conversation atrás de flag)

**Objetivo:** montar o agente do banco e responder com paridade ao handler atual, **sem** code-in-DB e
**sem** roteamento. Operar por `phone` (conversa ainda não existe).

Arquivos a criar (núcleo do motor, **core**):
- `ai_engine/__init__.py` — interface fina pública: `run_conversation(conversation_id_or_phone,
  agent_key, text) -> str` (assinatura propositalmente HTTP-portável, pesquisa §7.4).
- `ai_engine/agent_factory.py`:
  - `build(agent_key, session_id) -> agno.agent.Agent`:
    1. carrega config do agente (cache na Fase 6) via `ai_agent_repo`.
    2. `render_prompt(agent_cfg, ctx)` — resolve placeholders (`{nome_contato}`, `{data_hora}`,
       `{tags}`, etc.) reusando a lógica de `AgentHandler._build_system_prompt` (extrair para helper
       compartilhado) + substituição de variáveis via `ai_variable_repo`.
    3. `apply_filter_sync("filter.system_prompt", rendered, {"phone": phone})`.
    4. `resolve_tools(agent_cfg.tool_names)` → union de `CORE_TOOLS` + plugin (`_tool_executors`) +
       `ai_tools` (Fase 3); aplica `apply_filter_sync("filter.llm.tools", ...)`; respeita
       `tool_overrides.enabled`. **Precedência em colisão de nome (P61): código > plugin > banco** —
       banco nunca sequestra tool core; warning logado + badge de colisão na UI (espelha o
       `_register_tool` atual, que no-op-a colisão).
    5. instancia o model via **`model_factory.build(agent_cfg)` (P64)**: `OpenAILike(base_url=
       LLM_API_BASE_URL, api_key=config["openrouter_api_key"], id=model_id, ...)` onde `model_id` está no
       formato **`provider/modelo`** (`openai/...`, `google/...`, `anthropic/...`), com auto-detecção de
       prefixo quando vem sem `/`. Tuning em cascata: `model_config` do agente > variável
       `{param}_{agent_key}` (em `ai_variables`) > global `{param}` (REF §3). Mapeia
       `temperature`, `top_p`, `max_output_tokens→max_completion_tokens`,
       `thinking_level→reasoning_effort`.
    6. instancia `Agent(model=..., tools=..., session_id=session_id,
       output_schema=LLMResponse, ...)` — **structured output via `output_schema` Pydantic do Agno
       (P64)**. `LLMResponse{ mensagens_para_usuario: list[str], private_message: bool }` é o schema
       visto pelo LLM; `silent_output` é **controlado por código, nunca pelo LLM** (definido em
       `ai_engine/schemas.py`, espelhando REF §5). Isso substitui o parse JSON manual do split atual já
       desde a Fase 2 — não é refino tardio.
  - Adaptador `to_agno_tool(schema, executor)` — converte o contrato WhatsBot `(schema dict,
    execute(ctx, args))` para o formato de tool do Agno (função/`@tool`), num closure que monta o
    `ToolContext` (de `plugins.context`), aplica `filter.tool.args`/`filter.tool.result` e emite
    `tool.before`/`tool.after`. Reaproveita a abstração já madura de `_dispatch_tool`.
- `ai_engine/model_factory.py` — `build(agent_cfg) -> OpenAILike` (multi-provider `provider/modelo` +
  tuning em cascata, P64; ver passo 5 acima).
- `ai_engine/schemas.py` — `LLMResponse` (visto pelo LLM) e `AgentResponse` interno
  (`+ silent_output` controlado por código), P64.
- `ai_engine/runner.py` — `run_conversation`:
  1. resolve `agent_key` (param ou `default`).
  2. monta histórico das últimas N mensagens via `message_repo`/`ContactMemory` (**P58 — fonte de
     verdade = `messages`**; passamos o contexto pro Agno, **não** usamos `agno_sessions`/`db` do Agno,
     para ter uma única timeline coerente com o painel).
  3. `apply_filter_sync("filter.llm.messages", ...)`.
  4. `agent = factory.build(...)`; `result = await agent.arun(text)` → `LLMResponse` estruturado.
  5. persiste assistant message, usage (tokens/custo via `usage_repo`), `executions`/`execution_steps`
     com `agent_key` (+ `total_tokens`/`total_cost_usd` em `executions`).
  6. devolve `LLMResponse.mensagens_para_usuario` (já é a lista de partes — dedup preservando ordem,
     REF §5). O envio via `_send_reply` continua **fora** do motor (no webhook), então `filter.reply.*`
     seguem funcionando; o split por JSON manual do handler legado sai de cena.

Integração no pipeline:
- `server/routes/webhook.py` — nos pontos `_run_one_cycle` (L872, L990) o **caminho default é o Agno**
  (P65). A flag `config["ai_engine"]` existe só como **fallback de emergência curtíssimo** durante o gap
  de paridade (Fase 2→3), e o default já é `"agno"`:
  ```python
  if config_flag("ai_engine", default="agno") != "legacy":
      reply = await ai_engine.run_conversation(phone, agent_key="default", text=combined)
  else:  # fallback mínimo de emergência — removido na Fase 6
      result = await agent_handler.aprocess_message(...)
  ```
  `agent_key="default"` nesta fase (conversa/inbox vêm nas Fases 4-5).
- `server/app.py` — instanciar o `ai_engine` em `create_app` e injetá-lo em `ServerDeps` (L47/L114),
  análogo ao `agent_handler`.

Endpoints REST novos (read-only nesta fase, para validação):
- `GET /api/ai/agents`, `GET /api/ai/agents/{key}` — em `server/routes/ai_engine.py` (novo módulo,
  registrado em `server/app.py` junto aos demais L304+).

**Critério de pronto:** com o motor Agno (default), uma mensagem real recebe resposta **com paridade
de comportamento** ao handler legado (mesmo prompt, modelo e tools core/plugin), agora com a config
vinda do banco e o split via `output_schema` (`LLMResponse`, P64); `executions` registra
`agent_key="default"` + tokens/custo; o fallback de emergência `ai_engine=legacy` ainda funciona (será
removido na Fase 6). Filters de plugin (`horario_funcionamento`, `blacklist`, `auto_signature`)
continuam atuando.

---

## Fase 3 — Code-in-DB: tool installer + runner isolado

**Objetivo:** tools cujo código vive em `ai_tools` ficam materializadas, instaladas, recarregadas e
**executadas em subprocess isolado**. Fase mais sensível (segurança).

### 3.1 Tool installer

Arquivo: `ai_engine/tool_installer.py` (espelha o loader de plugins — `plugins/loader.py` L263-303).
Fluxo (pesquisa §5.3), disparado ao salvar/ativar uma tool e no boot para tools `enabled=1`:
1. **Materializa** — escreve `ai_tools.code` em `storages/ai_tools/<name>.py` (pasta user-writable, no
   `.gitignore`, fora de `agent/` e `storages/plugins/`). `name` validado por regex
   `^[a-z][a-z0-9_]{0,63}$`.
2. **Instala deps** — `install_status='installing'`; `pip install` (ou `uv pip install --python <exe>`,
   timeout ~300s, REF §2) das `dependencies`. **P66 — SEM allowlist de dependências no MVP** (risco
   aceito conscientemente; revisitar no endurecimento). Falha → `install_status='failed'` +
   `install_error`; tool **não** entra no registry (fail-closed).
3. **Importa/recarrega** — `importlib.util.spec_from_file_location` sob pacote `whatsbot_ai_tools.<name>`
   (criar `_ensure_parent_package` análogo ao dos plugins); `importlib.reload` se já carregado.
4. **Valida assinatura** — módulo deve expor o contrato `(schema dict + execute(ctx, args))`. Errado →
   `failed`.
5. **Grava status** — `install_status='ok'` + `version += 1` + snapshot em `ai_tools_history`.
6. `dynamic_registry` (Fase 6) passa a oferecer a tool.

### 3.2 Runner isolado (execução fora do processo do webhook)

Arquivo: `ai_engine/tool_runner.py`. **P62 — runner = subprocess + RLIMIT + timeout no dia-1.** Só a
execução do código de `ai_tools` sai do processo; `CORE_TOOLS`/plugin (código revisado) continuam
in-process. **Só Linux/Docker** no escopo Pro (P29 ripple) — usar `PR_SET_PDEATHSIG` para o worker
morrer com o pai.

- Subprocess worker dedicado (`ai_engine/tool_worker.py`, ex.: `python -m ai_engine.tool_worker`) que
  recebe `(tool_name, args, ctx_safe)` via IPC (stdin/stdout JSON ou socket) e devolve resultado/erro.
- Limites de SO no worker: `RLIMIT_CPU`, `RLIMIT_AS` (memória), `timeout` rígido por chamada (mata
  runaway). **seccomp/AppArmor é um upgrade pós-MVP** — depende de o container Coolify rodar com
  privilégios (ponto a confirmar nos testes do Thiago no Docker/Linux; ver Perguntas em aberto).
- **Least privilege:** o worker **não** recebe a chave do LLM, nem credenciais admin, nem conexão de
  escrita ao banco principal; recebe só o `ctx` mínimo que a tool precisa.
- **Fail-closed:** timeout/crash do worker vira feedback de erro ao LLM, **nunca** derruba o webhook
  (envelopar em try/except como o handler atual faz).
- Reaproveitar o "serviço de subprocesso gerenciado" do Plano 02 se disponível; senão, runner próprio
  mínimo aqui.

### 3.3 Mitigações de segurança (MVP)

- **SEM allowlist de dependências (P66)** — o MVP não bloqueia pacotes pip (risco aceito
  conscientemente; revisitar no endurecimento). NÃO criar `dep_allowlist.py`.
- **Isolamento do runner (P62)** — subprocess + `RLIMIT_*` + timeout (§3.2) é a barreira principal de
  contenção no dia-1.
- **Edição de código = papel ADM** (Plano 03) — endpoints de escrita de `ai_tools`/`ai_agents`/
  `ai_prompts` exigem grupo `adm`. Atendentes nunca veem.
- **Auditoria before/after** — toda criação/edição grava snapshot em `*_history` com `changed_by` +
  timestamp (Plano 07).
- **Gate "IA cria tool" (P63)** — artefato proposto pela IA nasce `enabled=0`/`install_status='pending'`
  até um ADM revisar e ativar. Sem modo de autonomia total no MVP.
- **Validação estática leve (AST)** — recusa/alerta imports obviamente perigosos antes de instalar
  (defesa em profundidade barata, não barreira principal). Não substitui o isolamento.

Endpoints REST novos:
- `POST/PUT /api/ai/tools` (cria/edita; ADM-only) → dispara installer.
- `GET /api/ai/tools`, `GET /api/ai/tools/{name}` (lista/detalhe + `install_status`/`install_error`).
- `POST /api/ai/tools/{name}/reinstall` (re-roda installer).
- `DELETE /api/ai/tools/{name}`.

**Critério de pronto:** criar/editar uma tool pelo painel reflete sem deploy; uma tool com dependência
inexistente falha com `install_status='failed'` e `install_error` legível; uma tool com `while True` é
morta pelo timeout do runner sem travar o webhook; uma tool proposta pela IA nasce `pending` e só
funciona após ADM ativar (P63); toda mudança aparece em `ai_tools_history` com `changed_by`. Demonstrar
que o worker não tem a chave do LLM no ambiente.

---

## Fase 4 — Multi-agente por inbox + CRUD completo (UI)

**Objetivo:** vários agentes, escolha por inbox, painel de edição. **Depende dos Planos 01 e 02.**

Migration (rodar **depois** das tabelas de inbox/conversa dos planos 01/02):
- `db/alembic/versions/20260620_0008_ai_agent_links.py` (P60 — agente↔inbox via coluna):
  - `ALTER TABLE inboxes ADD COLUMN default_agent_key TEXT REFERENCES ai_agents(agent_key)`.
  - `ALTER TABLE conversations ADD COLUMN active_agent_key TEXT REFERENCES ai_agents(agent_key)`.

Pipeline:
- `server/routes/webhook.py` — substituir `agent_key="default"` por:
  ```python
  conversation = resolve_conversation(phone, inbox_id)   # plano 01
  if not inbox.agent_bot_enabled: return                 # humano atende
  agent_key = conversation.active_agent_key or inbox.default_agent_key or "default"
  reply = await ai_engine.run_conversation(conversation.id, agent_key, text)
  ```
- `run_conversation` passa a usar `conversation.id` como `session_id` (sessão = conversa, pesquisa
  §4.6/§5 R5). Histórico continua vindo de `messages` filtrado pela conversa.

UI (frontend — Preact + HTM, sem build step; regras de modo escuro do CLAUDE.md obrigatórias):
- `web/static/js/components/ai/AgentsManager.js` — lista/CRUD de agentes (nome, prompt, modelo, tools,
  hooks, routing_targets, is_router, enabled).
- `web/static/js/components/ai/PromptsEditor.js` — CRUD `ai_prompts` (com preview de placeholders).
- `web/static/js/components/ai/VariablesEditor.js` — CRUD `ai_variables`.
- `web/static/js/components/ai/ToolsEditor.js` — editor de código das tools (ADM-only), com
  `install_status`, `install_error`, botão "Reinstalar", histórico de versões.
- Rota SPA + entrada no `GearMenu`. Usar classes `wa-*` e `.wa-field` (modo escuro).

Endpoints REST (escrita):
- `POST/PUT/DELETE /api/ai/agents`, `/api/ai/prompts`, `/api/ai/variables` (ADM-only para escrita).
- `PUT /api/inboxes/{id}/default-agent` (ou no plano 02).

**Critério de pronto:** criar 2+ agentes pelo painel; associar agentes distintos a inboxes distintos;
mensagem em cada inbox é respondida pelo agente certo; editar prompt/modelo/tools de um agente reflete
no comportamento; tudo versionado e auditado.

---

## Fase 5 — Roteamento por handoff (active_agent_key)

**Objetivo:** handoff sequencial entre IAs preservando a conversa (REF §4 — `active_agent_key` na
conversa, depth ≤ 5). Confirma **P60** (handoff cobre multi-agente dentro da conversa).

- Tool core nova `transferir_para_outro_agente(agent_key, motivo)` em
  `agent/tools/transferir_agente.py` (adicionar a `CORE_TOOLS`). Schema + `execute` sinalizam o alvo de
  handoff (não enviam mensagem; o motor intercepta). Agente intermediário fica `silent_output=True`
  (controlado por código, REF §4/§5).
- `ai_engine/routing.py` — `run_with_routing(conversation_id, text, depth=0)` (REF §4):
  1. `if depth > MAX_ROUTING_DEPTH (5): fallback`.
  2. monta agente do `active_agent_key`, executa.
  3. **proteção de loop:** se re-invocar o mesmo agente, desliga o histórico para não repetir (REF §4).
  4. se a tool de handoff foi chamada e `target ∈ routing_targets` do agente atual:
     `conversations.active_agent_key = target`; registra salto em `executions.routing_steps` (+
     `execution_steps`); recursão `depth+1`, **mesma `session_id` = conversation.id**.
  5. valida alvo contra `routing_targets` (anti-rota-arbitrária).
- Integração com handoff **humano**: reusar `transfer_to_human` (já existe) — o "agente que recebe"
  pode ser o humano (plano 01 §6).

**Critério de pronto:** agente comercial transfere para suporte; conversa continua com suporte mantendo
contexto; `executions.routing_steps` registra os saltos; loop infinito é cortado em depth 5 com
mensagem de fallback; transferência para humano funciona.

---

## Fase 6 — Hot-reload, versionamento e aposentadoria do legado

**Objetivo:** mudar config sem restart; hooks declarativos; **remover o caminho legado** (P65).

- `ai_engine/dynamic_registry.py` (REF §2): cache em memória de `ai_agents`/`ai_prompts`/
  `ai_variables`/`ai_tools` com **invalidação por evento + fallback de polling TTL (~60s)** —
  **P57: 1 worker do uvicorn no MVP**, então invalidação por evento + cache curto bastam:
  - PUT no painel emite `ai.config.changed` no bus → factory limpa cache no mesmo processo.
  - TTL curto (~60s) é a rede de segurança; só fica relevante se um dia subirmos `--workers > 1`
    (reavaliar com mecanismo de sincronização, ex. `LISTEN/NOTIFY` em Postgres). Não projetar para
    multi-worker agora.
  - **Dois níveis de reload:** *dado* (agente/prompt/variável/`enabled`/`description`) = invalidação a
    quente, sem restart; *código* (`ai_tools.code`) = passa pelo installer (`importlib.reload` só
    daquele módulo), sem reiniciar o servidor.
- **Versionamento**: já implementado nos repos (Fase 1) — `version += 1` + snapshot. Adicionar
  endpoint `GET /api/ai/{agents|tools|prompts}/{key}/history` e `POST .../rollback/{version}`
  (rollback de tool re-dispara o installer).
- **Hooks declarativos** (REF §6): `hooks_config` (`call_limit`, `requires_prior_call`) viram
  `@tool(pre_hook=)` closures montados na factory; estado vive na closure e reseta a cada mensagem.
  Mapeia bem para `filter.tool.args` do bus.
- **Aposentar o `AgentHandler` legado (P65):** como o motor já nasceu Agno-first, aqui removemos o
  caminho `ai_engine=legacy` do webhook (o fallback de emergência das Fases 2-3 deixa de existir).
  Migrar os 4 call sites de transcrição (`_maybe_transcribe`) e `save_assistant_message` para o motor.
  `agent/handler.py` é marcado como deprecado (ou reduzido a adaptador fino chamado pela factory, se
  algum recurso ainda não tiver paridade — situação que deve ser curtíssima).

**Critério de pronto:** editar um prompt/agente no painel reflete na próxima mensagem **sem restart**;
editar código de tool recarrega só aquele módulo; histórico de versões navegável e rollback funcional;
o webhook chama **só** `ai_engine.run_conversation` (sem ramo legacy); testes verdes; nenhum
comportamento perdido (transcrição, save de mensagem, split via `output_schema`, filters de reply).

---

## Fase 7 — (Opcional) Extrair UI/CRUD como plugin

**Objetivo:** se o cliente quiser desligar/versionar a UI à parte (pesquisa §5.5 — híbrido).

- Manter o **núcleo no core** (factory, run_conversation, routing, installer, tabelas `ai_*`).
- Mover telas Preact + endpoints de CRUD para `storages/plugins/ai_engine_ui/`, consumindo uma API
  estável do core (`from ai_engine import save_agent, list_agents, ...`). O core **não** depende do
  plugin. As tabelas `ai_*` permanecem no core (plugin não pode criar tabela sem prefixo
  `plugin_<id>_`).

**Critério de pronto:** desabilitar o plugin remove a UI sem afetar o motor (agentes seguem
respondendo). **Default: não fazer nesta versão — começar tudo no core (pesquisa §5.5).**

> **Nota (P65):** a antiga "Fase 8 — aposentar o `AgentHandler` legado" foi **incorporada à Fase 6**.
> Como o motor é Agno-first desde a Fase 2, não há fase dedicada de remoção do legado meses depois — a
> aposentadoria acontece assim que a paridade é confirmada, ainda na Fase 6.

---

## Resumo de artefatos

**Migrations Alembic:** `0007_ai_engine` (tabelas `ai_*` + ALTER em executions/execution_steps),
`0008_ai_agent_links` (ALTER em inboxes/conversations — depende dos planos 01/02).

**Novos módulos backend:** `ai_engine/{__init__,agent_factory,model_factory,schemas,runner,routing,
tool_installer,tool_runner,tool_worker,dynamic_registry}.py` (**sem `dep_allowlist.py`** — P66);
`db/repositories/{ai_agent_repo,ai_prompt_repo,ai_variable_repo,ai_tool_repo}.py`;
`server/routes/ai_engine.py`; `agent/tools/transferir_agente.py`.

**Edições backend:** `db/tables.py` (Tables novas + colunas), `db/connection.py` (seed),
`server/app.py` (wiring `ai_engine` em `ServerDeps` + register_routes), `server/routes/webhook.py`
(troca da chamada ao handler atrás de flag), `agent/tools/__init__.py` (nova tool core),
`.gitignore` (`storages/ai_tools/`), `requirements.txt` (`agno`).

**Frontend:** `web/static/js/components/ai/{AgentsManager,PromptsEditor,VariablesEditor,
ToolsEditor}.js` + rota SPA + entrada no `GearMenu` (regras de modo escuro).

**Dependências novas:** pip `agno>=2.6,<3` (+ o que ele puxar; validar conflito no spike). Sem JS novo.

---

## Perguntas em aberto

1. **Workers do uvicorn em produção (Coolify).** ✅ **DECIDIDO (2026-06-19): (a) 1 worker (P57)** no
   MVP — invalidação por evento + cache curto (TTL ~60s de fallback) bastam. Reavaliar com mecanismo de
   sincronização (`LISTEN/NOTIFY` em Postgres) só se a carga exigir multi-worker.

2. **Sessão: Agno `db` vs montar histórico de `messages`.** ✅ **DECIDIDO (2026-06-19): (b) montar das
   `messages` (P58)** — uma fonte de verdade, coerente com a UI. Não usar `agno_sessions`/`db` do Agno.

3. **`ai_variables` dedicada vs prefixo em `config`.** ✅ **DECIDIDO (2026-06-19): (a) tabela dedicada
   (P59)** — `category`/`description` próprios, melhor para a UI e para o tuning por agente.

4. **Granularidade agente↔inbox.** ✅ **DECIDIDO (2026-06-19): (a) coluna `default_agent_key` por inbox
   (P60)**; o handoff (Fase 5) cobre multi-agente dentro da conversa.

5. **Precedência em colisão de nome de tool.** ✅ **DECIDIDO (2026-06-19): (a) código > plugin > banco
   (P61)** — banco nunca sequestra tool core; warning logado + badge de colisão na UI.

6. **Nível de isolamento do runner de code-in-DB no dia-1.** ✅ **DECIDIDO (2026-06-19): (a) subprocess
   + `RLIMIT_*` + timeout (P62)** no dia-1, só Linux/Docker (`PR_SET_PDEATHSIG`, P29). seccomp/AppArmor
   é upgrade pós-MVP. **A confirmar nos testes do Thiago:** o container Coolify roda com privilégios
   para aplicar seccomp/AppArmor? (não bloqueia o MVP; só define se dá para subir o isolamento depois
   sem reprojeto).

7. **Gate da IA criando tools.** ✅ **DECIDIDO (2026-06-19): (a) gate humano (P63)** — tool proposta
   pela IA nasce `enabled=0`/`install_status='pending'` até um ADM aprovar. Sem modo de autonomia total
   no MVP.

8. **Structured output para o split de mensagens.** ✅ **DECIDIDO (2026-06-19): (a) `output_schema`
   Pydantic do Agno (P64)** — `LLMResponse{mensagens_para_usuario, private_message}`, `silent_output`
   por código. **Não é mais refino da Fase 6: entra já na Fase 2** (é o caminho default do motor
   Agno-first). Validar que não quebra prompts existentes ao migrar.

9. **Tempo de coexistência legacy × Agno.** ✅ **DECIDIDO (2026-06-19): Agno-first (P65)** — sem
   coexistência longa. O legado é só fallback de emergência curtíssimo nas Fases 2-3 e é removido na
   Fase 6, assim que a paridade é confirmada.

10. **Allowlist de dependências.** ✅ **DECIDIDO (2026-06-19): SEM allowlist no MVP (P66)** — risco
    aceito conscientemente, revisitar no endurecimento. Não criar `dep_allowlist.py`.

11. **Reaproveitar o "serviço de subprocesso gerenciado" do Plano 02 para o tool_runner?**
    ⏸️ **ADIADO (P67)** — decidir depois. No MVP, se o Plano 02 entregar o serviço primeiro e a API
    servir, reusar; senão runner próprio mínimo (Fase 3), refatorando para reuso depois.
