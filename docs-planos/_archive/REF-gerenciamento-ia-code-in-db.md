# Referência — padrão "code-in-DB" do gerenciamento-ia

> Investigação de `/opt/nexus/gerenciamento-ia` (somente leitura) para servir de base concreta ao
> **plano 06 (motor multi-agente)**. Resolve P64 e detalha o mecanismo real já em produção lá.
> Data: 2026-06-18.

## 1. Esquema do banco (tabelas-chave)

**`gerenciamento_ia_tools`** — source of truth do código Python das tools:
- `name` (PK), `code` (módulo .py completo), `dependencies TEXT[]`, `enabled BOOL`,
  `version INT`, `install_status` (`pending|ok|failed`), `install_error`, `updated_at`, `updated_by`.
- `+ description`, `+ param_descriptions JSONB` (para a UI; não injetados no .py).
- Tabela espelho **`_tools_history (name, version, code, dependencies, ...)`** — PK `(name, version)`.

**`gerenciamento_ia_agentes`** — config do agente:
- `agent_key` (PK, ex.: `ROUTER`, `COMERCIAL`), `display_name`, `model_config JSONB`
  (`{model_id, temperature, top_p, ...}`), `prompt_template`, `prompt_key`, `tool_names TEXT[]`,
  `hooks_config JSONB`, `routing_targets TEXT[]`, `is_router BOOL`, `enabled`, `version`.
- Espelho **`_agentes_history (agent_key, version, snapshot JSONB)`**.

**`gerenciamento_ia_prompts`** — prompts por tipo de atendimento:
- `slug` (único), `prompt`, `chave_do_prompt`, `tipo_atendimento`; único em
  `(chave_do_prompt, tipo_atendimento)`.

**`gerenciamento_ia_variaveis`** — config/tuning dinâmico:
- `slug` (único), `nome`, `valor`, `categoria`, `descricao`, `template JSON?`.

> **Adaptação WhatsBot:** mesmas tabelas com prefixo do projeto (`ai_tools`, `ai_agents`,
> `ai_prompts`, `ai_variables` + `_history`), em **SQLAlchemy Core** (não Prisma). `TEXT[]` →
> `JSON().with_variant(...)` ou tabela de junção, já que SQLite não tem array nativo.

## 2. Materialização e execução (`tool_installer.py`)

Pipeline **code-in-DB → executável**:
1. **Boot:** `materialize_all_enabled()` — seleciona tools `enabled=true`, escreve cada uma em
   `src/tools/_dynamic/{name}.py` (com header automático; o arquivo é só p/ inspeção/debug, a fonte
   é o banco).
2. **Deps:** `_uv_pip_install(deps)` — `uv pip install --python <exe> <deps>`, timeout 300s; falha
   grava `install_error`.
3. **Carga:** `install_tool(name)` — materializa → instala deps → `importlib.reload` (ou
   `import_module`) do módulo `src.tools._dynamic.{name}` → valida que a função exportada existe →
   marca `install_status='ok'|'failed'`.

**Registry runtime (`dynamic_registry.py`)** — singleton com cache TTL 60s:
- `get_tool_callable(name)`: refresh se TTL expirou → se row `enabled` e `install_status='ok'`,
  importa do dinâmico; senão **fallback** para tool legada em `src/tools/{name}.py`.
- Cache versionado `{name: (version, callable)}`; muda versão → `importlib.reload`.

**Admin endpoints (`main.py`)**: `POST /admin/tools/{name}/sync` (instala + `reload_now()`),
`GET /admin/tools` (lista status), `POST /admin/agents/reload` (ignora TTL).

> **Adaptação WhatsBot:** o `uv pip install` + `importlib.reload` **deve rodar no runner isolado**
> (subprocess + RLIMIT + timeout, P62), não no processo do servidor. No MVP **sem allowlist de deps**
> (P66 — risco aceito, revisitar). Materialização em `storages/` (gravável no EXE).

## 3. Multi-provider via OpenRouter (`model_factory.py`) — resolve P64

- Base URL OpenRouter (OpenAI-compatible); chave de `ConfigStore`.
- `model_id` no formato **`provider/modelo`** (`openai/gpt-...`, `google/gemini-...`,
  `anthropic/claude-...`); auto-detecção de prefixo quando vem sem `/`.
- Tuning em cascata: `model_config` do agente > variável `{param}_{agent_key}` > global `{param}`.
  Map: `temperature`, `top_p`, `max_output_tokens→max_completion_tokens`,
  `thinking_level→reasoning_effort`.
- Instancia `OpenAIChat(**kwargs)` do **Agno**.

> **Adaptação WhatsBot:** trocar base URL do OpenRouter pelo **proxy Techify** (já é
> OpenAI-compatible — `LLM_API_BASE_URL`). O mesmo padrão `provider/modelo` funciona. Confirma o
> multi-provider que o Thiago pediu (OpenAI/Google/etc. por baixo do mesmo cliente).

## 4. Roteamento / handoff (`ai_service.py`)

- `_route()` com **proteção de loop** (detecta re-invocação do mesmo agente; desliga histórico p/
  não repetir) e **profundidade máxima 5** (`max_route_depth`).
- Tools de reroute (`solicitar_roteamento`, `transferir_para_outro_agente`): agente intermediário
  fica `silent_output=True`; cria `RoutingStep` para auditoria.
- Router lê `custom_attributes.tipo_de_atendimento` (no Chatwoot deles) para decidir o destino.

> **Adaptação WhatsBot:** o "agente ativo" vive na **conversa** (`active_agent_key`, decidido no
> plano 06); `RoutingStep` vira passos em `execution_steps`. Depth ≤ 5.

## 5. Structured output / split de mensagens — resolve P64

- Schema visto pelo LLM (`output_schema`): **`LLMResponse{ mensagens_para_usuario: list[str],
  private_message: bool }`** (máx. 2 mensagens por instrução, não validação).
- Schema interno: `AgentResponse{ mensagens_para_usuario, silent_output, private_message }` —
  `silent_output` é **controlado por código, nunca pelo LLM**.
- Parsing com precedência: pydantic direto > dict > extrai JSON de string (``` ```json ``` ```/```` ``` ````
  /fallback string única).
- Dispatcher: dedup preservando ordem; envia uma a uma com `delay_entre_mensagens` (config).

> **Adaptação WhatsBot:** substitui o parse JSON manual do split atual por `output_schema` Pydantic
> (decisão P64 = opção a). Validar que não quebra prompts existentes (refino na Fase 6 do plano 06,
> não bloqueante).

## 6. Hooks declarativos (`hooks_interpreter.py`)

- `hooks_config JSONB` por agente: `{tool: {call_limit, requires_prior_call: [...]}}`.
- `build_tool_hook()` valida antes de cada tool: dependências chamadas com sucesso + limite de
  chamadas na execução. Estado vive na closure, reseta a cada mensagem.

> **Adaptação WhatsBot:** opcional no MVP; bom para limitar tools caras. Mapeia bem para o conceito
> de `filter.tool.args` do bus de plugins já existente.

## 7. Arquivos de referência (em `/opt/nexus/gerenciamento-ia/ai/src/`)

| Arquivo | Responsabilidade |
|---|---|
| `../sql/create_dynamic_agents.sql` | DDL das tabelas dinâmicas |
| `services/tool_installer.py` | materializar + instalar + reload de tools |
| `services/dynamic_registry.py` | cache TTL + resolução runtime (dinâmico↔legado) |
| `services/agent_factory.py` | factory única que monta o Agent Agno do banco |
| `agents/model_factory.py` | multi-provider OpenRouter + tuning |
| `services/ai_service.py` | orquestração + roteamento/handoff |
| `services/hooks_interpreter.py` | hooks declarativos (call_limit, requires_prior_call) |
| `services/response_dispatcher.py` | dedup + split + envio com delay |
| `services/prompt_context.py` | placeholders lazy no prompt |
| `services/config_store.py` | cache TTL das variáveis |
| `main.py` | webhooks + admin endpoints (sync/reload) |

> **Nota:** o gerenciamento-ia usa Postgres + psycopg2 + Prisma (schema). O WhatsBot deve replicar a
> **lógica**, não as ferramentas: SQLAlchemy Core, suporte SQLite+Postgres, runner isolado, e a
> materialização em `storages/`. O framework de agente (Agno) e o padrão OpenRouter/OpenAI-compatible
> são reaproveitáveis diretamente (apontando para o proxy Techify).
