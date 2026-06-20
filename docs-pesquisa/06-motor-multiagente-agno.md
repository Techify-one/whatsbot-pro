# 06 — Motor multi-agente com Agno

> **Status:** Pesquisa de arquitetura. **Nenhum código foi alterado.** Este é o documento mais
> profundo do conjunto: descreve como trocar o `AgentHandler` singleton por um motor multi-agente
> dirigido pelo banco, usando a biblioteca **[Agno](https://github.com/agno-agi/agno)**, num desenho
> inspirado no motor de IA do `/opt/gerenciamento-ia` — porém **bem mais enxuto**.
>
> Relações com outros docs:
> - **[01 — Inbox e conversas](01-inbox-e-conversas.md)**: a `Conversation` é a unidade de execução do
>   agente; o `conversation_id` mapeia para a `session` do Agno. Participação do bot por inbox vem de lá.
> - **[02 — Canais e providers](02-canais-e-providers.md)**: cada `Inbox`/`Channel` é onde um agente é
>   "plugado". O agente que responde depende do inbox que recebeu a mensagem.
> - **[07 — Auditoria](07-auditoria.md)**: mudanças em prompt/agente/tools/overrides são as ações
>   **mais sensíveis** do produto e precisam de trilha de auditoria; execuções de IA já são rastreadas
>   em `executions`/`execution_steps`.
>
> **Decisão do cliente (2026-06-18 — atualizada):** multi-agentes com Agno; tools, prompts, agentes
> e variáveis **no banco — INCLUSIVE o código Python das tools (code-in-DB)**, no estilo
> `/opt/gerenciamento-ia` (materialização `.py` + `pip install` + `importlib.reload` +
> `install_status` + versionamento/histórico). **Sem ofertas, sem produtos, sem embeddings/RAG** no
> começo. Deploy: **uma empresa**, server-hosted via Coolify/Docker.
>
> Esta decisão **inverte** a recomendação que a versão anterior deste documento fazia na §5 (que
> preferia "tools pré-registradas em código"). Os motivos do cliente são: (a) **mudar comportamento
> sem mexer em código nem fazer deploy**, e (b) **permitir que a própria IA debugue e crie tools**.
> O contexto torna isso defensável — **empresa única, self-hosted, sem tenants não-confiáveis, só
> admin/IA escrevem código** (modelo de ameaça baixo). A §5 foi reescrita para refletir a escolha e
> detalhar o mecanismo; a §5.4 traz as **mitigações de segurança obrigatórias**.
>
> O cliente também levantou: "**às vezes pode ser um plugin disso, se fizer sentido**" — ou seja,
> empacotar o motor (tabelas `ai_*`, UI, installer) como um **plugin** do WhatsBot. A nova §5.5
> analisa plugin vs core. A decisão "**motor embutido no processo do WhatsBot vs serviço Python
> separado**" segue em aberto — a §7 a analisa e recomenda (e ela muda de peso agora que code-in-DB
> entrou).

---

## 1. O que existe hoje no WhatsBot

### 1.1 Um handler singleton, tudo global

O coração da IA é o `AgentHandler` (`agent/handler.py`) — **um único objeto**, construído uma vez no
boot e referenciado globalmente. Tudo nele é de instância única e compartilhado por todos os contatos:

```python
class AgentHandler:
    def __init__(self, api_key, system_prompt, ..., model="deepseek/deepseek-v4-pro", ...):
        self.system_prompt = system_prompt      # UM prompt global
        self.model = model                       # UM modelo global
        self._tool_schemas: list[dict] = []      # UM conjunto de tools global
        self._tool_executors: dict[str, ...] = {}
        for schema, executor in CORE_TOOLS:      # tools fixas, vindas do código
            self._register_tool(schema, executor)
```

Características relevantes (linhas de `agent/handler.py`):

- **Prompt único** — `self.system_prompt` é a string global. `_build_system_prompt()` (L434) a
  enriquece em runtime com info do contato, tags, contexto de grupo, fragmentos de plugin e
  data/hora, mas a **base** é uma só, vinda da config global (`config["system_prompt"]`).
- **Modelo único** — `self.model` (texto), `self.audio_model`, `self.image_model`. Trocar exige
  `update_config()`.
- **Registry de tools em código** — `CORE_TOOLS` (em `agent/tools/__init__.py`) é uma lista de
  `(schema, executor)` definida em **arquivos `.py` versionados no git**. Plugins adicionam tools via
  `register_plugin_tools()`. **Nenhuma** tool nasce do banco — o banco (`tool_overrides`) só guarda
  *overrides* (enabled, description, display_label) sobre tools que já existem em código.
- **Dispatch genérico** — `_dispatch_tool()` (L226) resolve `name → executor` no registry e chama.
  Não há `if/elif` por nome (regra do projeto). Bom: a abstração de "tool = (schema, callable)" já
  está madura e é reaproveitável pelo Agno.
- **Memória/sessão própria** — `ContactMemory` (`agent/memory.py`) lê/escreve `messages` por contato
  via repos SQLAlchemy Core; o histórico enviado ao LLM são as últimas N mensagens
  (`get_context_messages`). É a "session" caseira do WhatsBot.
- **Chamada ao LLM** — cliente OpenAI/AsyncOpenAI apontando para o proxy Techify
  (`base_url=LLM_API_BASE_URL`), loop manual de tool-calling (uma rodada de follow-up em L686/898).

### 1.2 O bus de filters já reescreve prompt, mensagens e tools

O WhatsBot **já tem** os pontos de extensão que um motor multi-agente precisaria — só que hoje servem
a plugins, não a "agentes". O pipeline aplica, em ordem (ver `aprocess_message`, L545+):

| Filter | Onde | O que permite |
|--------|------|---------------|
| `filter.system_prompt` | L576 | Reescrever **todo** o system prompt antes do LLM |
| `filter.llm.messages` | L586 | Reescrever a lista de mensagens (formato OpenAI) |
| `filter.llm.tools` | L593 | Trocar/filtrar os schemas de tools enviados |
| `filter.tool.args` / `filter.tool.result` | L648 / L674 | Interceptar argumentos e resultado de cada tool |
| `tool.before` / `tool.after`, `llm.before` / `llm.after` | events | Observabilidade de cada call |

> **Implicação:** o bus de filters é, na prática, um "ponto de injeção de configuração por
> mensagem". Um motor multi-agente configurável **poderia** ser construído inteiramente sobre esses
> filters (escolher prompt/tools/modelo por inbox via `filter.system_prompt` + `filter.llm.tools`).
> Mas isso não escala para roteamento entre agentes, sessões por agente, structured output e
> guardrails — daí a decisão de adotar o Agno como motor, e não esticar o handler atual.

### 1.3 Por que isso não escala para multi-agente configurável

| Necessidade do produto Pro | Bloqueio no desenho atual |
|----------------------------|---------------------------|
| **Vários agentes** (comercial, suporte, financeiro…) com prompts/modelos/tools distintos | Há **um** singleton com prompt/modelo/tools globais. Não existe entidade "agente". |
| **Escolher o agente por inbox/conversa** | O handler não conhece inbox nem conversa; só `phone` (contato). |
| **Configurar agentes no banco** (sem deploy) | Prompt/modelo vêm da config global key-value; tools vêm de **código**. Mudar exige editar `.py` e reiniciar. |
| **Roteamento entre agentes** (handoff comercial→suporte) | `transfer_to_human` só passa para humano; não há transferência **entre IAs**. |
| **Sessão/histórico por agente** | `ContactMemory` é por contato, não por (contato, agente). Trocar de agente mistura o histórico. |
| **Hooks/guardrails declarativos** (call_limit, requires_prior_call) | Não existem; só os filters genéricos de plugin. |
| **Structured output validado** | Hoje o "split em mensagens" é pedido via instrução no prompt + parse manual de JSON. Frágil. |

A conclusão é a mesma do [00-visao-geral](00-visao-geral.md): as três suposições "**1 número / 1
agente / sem usuários**" estão cravadas no código. Este doc ataca a segunda — **1 agente** — trocando
o singleton por um motor que monta agentes a partir do banco.

---

## 2. Requisitos

Derivados do pedido do cliente e do desenho do `/opt/gerenciamento-ia` (versão enxuta):

**Funcionais**

- R1. **Agentes configuráveis no banco**: nome, prompt (com placeholders), modelo + parâmetros
  (temperature etc.), lista de tools habilitadas, hooks por tool, alvos de roteamento, flag de
  router, enabled, versão.
- R2. **Prompts no banco** com placeholders renderizados em runtime (ex: `{nome_contato}`,
  `{data_hora}`, `{tags}`), reaproveitando o que `_build_system_prompt` já injeta.
- R3. **Variáveis/config global no banco** (ex: nome da empresa, horário, tom) referenciáveis pelos
  prompts — um "ConfigStore" lido pelo renderer.
- R4. **Tools** disponíveis aos agentes — conviver com as tools core (`CORE_TOOLS`) e de plugin já
  existentes; permitir ativar/configurar por agente via banco.
- R5. **Vincular agente ↔ inbox** (qual agente atende cada inbox — doc 02) e **agente ↔ conversa**
  (qual agente está "no controle" de uma conversa — doc 01), com a sessão do Agno chaveada por
  `conversation_id`.
- R6. **Roteamento multi-agente** (handoff comercial→suporte→financeiro) com profundidade máxima
  (anti-loop), registrando os saltos.
- R7. **Hot-reload de config**: alterar agente/prompt/tool no painel reflete sem (ou com mínimo)
  restart.
- R8. **Versionamento** de agentes/tools/prompts + histórico (auditoria — doc 07).
- R9. **Execuções rastreadas** (tokens, custo, tool calls, saltos de roteamento) — estender o que
  `executions`/`execution_steps` já fazem.

**Não-funcionais**

- R10. **Reaproveitar o pipeline atual** (webhook → batch → handler) e o bus de events/filters dos
  plugins, sem quebrá-los.
- R11. **Provider continua sendo o proxy Techify** (OpenAI-compatible, `base_url=LLM_API_BASE_URL`).
- R12. **Persistência portável** (SQLite default / Postgres) — coerente com a camada de dados atual.
- R13. **Code-in-DB com segurança proporcional**: o código Python das tools vive no banco e é
  materializado/instalado/recarregado em runtime (decisão do cliente). Como o modelo de ameaça é
  baixo (empresa única, self-hosted, só admin/IA escrevem — não é SaaS multi-tenant), isso é
  aceitável **desde que** acompanhado das mitigações da §5.4 (papel ADM exclusivo para editar código,
  auditoria before/after de toda criação/edição, isolamento de execução, allowlist/limites de deps,
  timeouts).
- R14. **Cortar do dia-1**: embeddings/pgvector, produtos, ofertas, busca semântica (decisão do
  cliente).

---

## 3. Visão geral do Agno e mapeamento de conceitos

### 3.1 O que é o Agno

Agno é um framework Python para construir agentes e sistemas multi-agente, com runtime de produção
próprio (AgentOS). A versão atual é a **série 2.x** (no momento da pesquisa, **v2.6.x**, jun/2026 —
[GitHub Releases](https://github.com/agno-agi/agno/releases), [v2 Changelog](https://docs.agno.com/other/v2-changelog)).
Foi o antigo *Phidata*, rebatizado para Agno na transição para a 2.x.

Principais mudanças da v1 → v2 relevantes para nós ([v2 Changelog](https://docs.agno.com/other/v2-changelog)):

- **Agentes stateless** — `Agent`/`Team` não acumulam estado em atributos; sessão, métricas e flags
  passam por parâmetro a cada `run()`. Casa perfeitamente com nosso modelo "monta o agente a partir
  do banco a cada mensagem".
- **`db` unificado** — o antigo `storage` foi abolido; um único parâmetro `db` (ex: `SqliteDb`,
  `PostgresDb`) cobre sessões, memória, evals e tracing
  ([Database overview](https://docs.agno.com/basics/database/overview)).
- **Memória** — `enable_user_memories=True` no agente; persistência gerida pelo `db`
  ([Memory](https://docs.agno.com/memory/overview)).
- **AgentOS** — runtime FastAPI stateless que serve agentes como API, com sessões, RBAC, tracing e
  audit ([AgentOS overview](https://docs.agno.com/agent-os/overview)). **Provavelmente não usaremos o
  AgentOS** (já temos FastAPI), mas vale conhecer (ver §7).

Performance (auto-publicada pela Agno, M4, out/2025 — [Performance](https://docs.agno.com/performance)):
instanciar um `Agent` ≈ **3 µs** e ≈ **6,6 KiB** de memória. Isso importa porque nosso desenho monta
um agente novo (a partir da config do banco) **a cada mensagem** — o overhead do framework é
desprezível perto da latência do LLM. A própria doc ressalva que o gargalo real é a inferência.

### 3.2 Comparação breve (escolha já feita, só para contexto)

| Framework | Posicionamento | Multi-agente | Persistência de estado | Overhead instanciação¹ |
|-----------|----------------|--------------|------------------------|------------------------|
| **Agno** | Pure-Python, foco em performance + runtime de produção (AgentOS) | `Team` (route/coordinate/broadcast/tasks) + handoff por tool | `db` unificado (SQLite/Postgres), sessions + memory | **~3 µs** |
| **LangGraph** | Grafo de estado explícito (nós/arestas), baixo nível | Topologias customizadas (supervisor/worker) | `checkpointer` robusto (semântica/episódica) | ~1.587 µs (529×) |
| **CrewAI** | Role-based, alto nível, prototipagem rápida | `Crew` (papéis + tarefas) | Básica | ~210 µs (70×) |
| **OpenAI Agents SDK** | SDK oficial, abstração mínima, ecossistema OpenAI | `handoff` entre agentes | Delegada ao dev / Responses API | — |
| **PydanticAI** | "FastAPI para agentes", validação Pydantic forte | Limitado (sem team maduro) | Básica | ~171 µs (57×) |

¹ Benchmarks auto-publicados pela Agno ([Performance](https://docs.agno.com/performance); [ZenML — Agno vs LangGraph](https://www.zenml.io/blog/agno-vs-langgraph), [LangWatch — frameworks 2025](https://langwatch.ai/blog/best-ai-agent-frameworks-in-2025-comparing-langgraph-dspy-crewai-agno-and-more)).

Para o nosso caso (um servidor, multi-agente configurável, OpenAI-compatible, persistência
SQLite/Postgres que já temos), Agno encaixa pela leveza, pelo `db` portável e pela abstração de
`Team`/handoff pronta. A decisão já está tomada; esta tabela só contextualiza.

### 3.3 Mapeamento Agno ↔ entidades do banco do WhatsBot

| Conceito Agno | Doc / classe | Entidade no banco WhatsBot | Observação |
|---------------|--------------|----------------------------|------------|
| **`Agent`** (model, instructions, tools, db, session) — [ref](https://docs.agno.com/reference/agents/agent) | `agno.agent.Agent` | linha em `agents` | montado em runtime pela `agent_factory` |
| **Model** (`OpenAILike`, base_url, api_key) — [openai-like](https://docs.agno.com/models/openai-like) | `agno.models.openai.like.OpenAILike` | `agents.model_config` JSON | aponta para o proxy Techify |
| **Tool** (função Python / Toolkit / `@tool`) — [tools](https://docs.agno.com/basics/tools/overview) | callable / `Toolkit` | nome em `agents.tool_names[]`; defn (código) em `ai_tools` **ou** em `CORE_TOOLS`/plugins | ver §5 (code-in-DB — decisão do cliente — + convivência com tools de código) |
| **Tool hooks** (pre/post) — [hooks](https://docs.agno.com/basics/tools/hooks) | `@tool(pre_hook=..., post_hook=...)` / `tool_hooks` | `agents.hooks_config` JSON | call_limit / requires_prior_call viram closures |
| **Team** (route/coordinate/...) — [teams](https://docs.agno.com/teams/overview) | `agno.team.Team` | conjunto de `agents` + `routing_targets[]` | ver §6 |
| **Session** (`session_id`, histórico) — [sessions](https://docs.agno.com/sessions/persisting-sessions/overview) | param `session_id` | `conversation_id` (doc 01) | a conversa **é** a sessão |
| **Db** (`SqliteDb`/`PostgresDb`) — [database](https://docs.agno.com/basics/database/overview) | `agno.db.sqlite` / `agno.db.postgres` | mesmo engine do WhatsBot | tabelas `agno_*` ou histórico próprio (ver §8) |
| **Memory** (`enable_user_memories`) — [memory](https://docs.agno.com/memory/overview) | flag no `Agent` | tabela `contacts`/`observations` (já temos) | manter o nosso, evitar duplicar |
| **Structured output** (`output_schema`) — [structured](https://docs.agno.com/input-output/structured-output/agent) | Pydantic model | — | substitui o parse de JSON do "split messages" |
| **Hooks/guardrails** (pre/post-hooks) — [hooks](https://docs.agno.com/basics/hooks/overview) | `pre_hooks`/`post_hooks` | reaproveitar filters atuais (§8) | ponte com `filter.system_prompt` etc. |

Esqueleto de uso (apontando para o proxy Techify):

```python
from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from agno.db.sqlite import SqliteDb     # ou agno.db.postgres.PostgresDb

agent = Agent(
    model=OpenAILike(
        id="deepseek/deepseek-v4-pro",          # agents.model_config["model_id"]
        api_key=config["openrouter_api_key"],    # chave Techify (nome legado)
        base_url=LLM_API_BASE_URL,               # https://llm.techify.one/api/v1
        temperature=0.7,
    ),
    description="...",                            # renderizado de prompts/agents
    instructions=[...],
    tools=[save_contact_info, transfer_to_human],# resolvidos de tool_names[]
    db=SqliteDb(db_file="storages/whatsbot.db"),
    session_id=str(conversation_id),             # = a conversa (doc 01)
    add_history_to_context=True,
    num_history_runs=10,
)
result = agent.run(text)                          # ou .arun() no FastAPI
```

---

## 4. Modelo de dados enxuto proposto

Adaptado do `/opt/gerenciamento-ia`, **cortando** ofertas, produtos e embeddings. Prefixo `ai_` para
o domínio de IA (análogo a como os docs 01/02/07 nomeiam seus domínios). DDL ilustrativo (SQLite;
em Postgres `TEXT[]` vira `text[]`/JSONB e `JSON` vira `JSONB`).

### 4.1 `ai_agents` — agentes configuráveis

```sql
CREATE TABLE ai_agents (
    agent_key       TEXT PRIMARY KEY,            -- snake_case estável (IDENTIDADE — não renomear)
    display_name    TEXT NOT NULL,
    description     TEXT,
    -- prompt: ou referencia uma chave em ai_prompts, ou guarda o template inline
    prompt_key      TEXT REFERENCES ai_prompts(prompt_key),
    prompt_template TEXT,                         -- template com placeholders {var}
    model_config    TEXT NOT NULL DEFAULT '{}',   -- JSON: {model_id, temperature, max_tokens, ...}
    tool_names      TEXT NOT NULL DEFAULT '[]',   -- JSON array de nomes de tools habilitadas
    hooks_config    TEXT NOT NULL DEFAULT '{}',   -- JSON: {tool: {call_limit, requires_prior_call}}
    routing_targets TEXT NOT NULL DEFAULT '[]',   -- JSON array de agent_key para handoff
    is_router       INTEGER NOT NULL DEFAULT 0,    -- 1 = atua como roteador
    enabled         INTEGER NOT NULL DEFAULT 1,
    version         INTEGER NOT NULL DEFAULT 1,    -- bump a cada save (auditoria)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Histórico de versões (auditoria — doc 07). Snapshot completo a cada save.
CREATE TABLE ai_agents_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    snapshot    TEXT NOT NULL,                     -- JSON da linha inteira
    changed_by  INTEGER,                           -- users.id (doc 03)
    changed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 4.2 `ai_prompts` — prompts versionados

```sql
CREATE TABLE ai_prompts (
    prompt_key   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'default',  -- "tipoAtendimento" do gerenciamento-ia
    body         TEXT NOT NULL,                     -- template com placeholders {var}
    version      INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (prompt_key, kind)                  -- unique (chave, tipo), como no original
);
```

### 4.3 `ai_variables` — config global referenciável pelos prompts

```sql
CREATE TABLE ai_variables (
    name       TEXT PRIMARY KEY,
    value      TEXT,
    category   TEXT,                                -- agrupador na UI
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

> **Alternativa:** poderíamos reusar a tabela `config` existente com prefixo `ai.var.<name>`, evitando
> uma tabela nova. Recomendo **tabela dedicada** porque `ai_variables` tem `category` (UI) e semântica
> própria; mas é uma decisão barata de reverter.

### 4.4 `ai_tools` — código das tools no banco (**decisão do cliente — code-in-DB**)

Tabela central da decisão de code-in-DB (ver §5). Guarda o **fonte Python** da tool, suas
dependências e o estado de instalação. Espelha `gerenciamento_ia_tools` do projeto de referência.

```sql
CREATE TABLE ai_tools (
    name           TEXT PRIMARY KEY,               -- IDENTIDADE (= call_type em usage); não renomear
    description    TEXT NOT NULL DEFAULT '',       -- descrição exposta ao LLM (schema da tool)
    code           TEXT NOT NULL,                  -- fonte Python completa da tool
    dependencies   TEXT NOT NULL DEFAULT '[]',     -- JSON array de pacotes pip (SQLite não tem array)
    enabled        INTEGER NOT NULL DEFAULT 1,
    install_status TEXT NOT NULL DEFAULT 'pending',-- pending | installing | ok | failed
    install_error  TEXT,                            -- traceback/stderr da última instalação falha
    version        INTEGER NOT NULL DEFAULT 1,      -- bump a cada save (auditoria)
    created_by     INTEGER,                         -- users.id (doc 03) — só papel ADM (§5.4)
    updated_by     INTEGER,                         -- users.id do último editor
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Histórico completo de versões (auditoria — doc 07). Snapshot do código a cada save.
CREATE TABLE ai_tools_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    snapshot    TEXT NOT NULL,                      -- JSON da linha inteira (inclui o code)
    changed_by  INTEGER,                            -- users.id (doc 03)
    changed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

> **Portabilidade SQLite ↔ Postgres.** SQLite não tem array nativo: `dependencies` é uma coluna
> `TEXT` com **JSON** (`["requests==2.32.0", ...]`), decodificada na aplicação. Em Postgres a mesma
> coluna pode virar `JSONB` (ou `text[]`, como no `/opt/gerenciamento-ia`). Para o WhatsBot, manter
> **JSON em ambos** simplifica os repos (um só caminho de (de)serialização) e é coerente com como
> `model_config`/`tool_names`/`hooks_config` já são tratados em §4.1.

### 4.5 `ai_executions` — rastreamento (estender o que já existe)

O WhatsBot já tem `executions`/`execution_steps`. Recomendo **estender** essas tabelas com colunas de
IA, em vez de criar paralelas:

```sql
ALTER TABLE executions      ADD COLUMN agent_key       TEXT;     -- agente que respondeu
ALTER TABLE executions      ADD COLUMN routing_steps   TEXT;     -- JSON: saltos de handoff
ALTER TABLE executions      ADD COLUMN total_tokens    INTEGER;
ALTER TABLE executions      ADD COLUMN total_cost_usd  REAL;
ALTER TABLE execution_steps ADD COLUMN agent_key       TEXT;     -- qual agente fez o passo
```

### 4.6 Amarração com inbox (doc 02) e conversa (doc 01)

Dois vínculos, em níveis diferentes:

**Agente ↔ inbox** (qual agente *atende* um inbox — config). Inbox é 1:1 com canal (doc 02). O modo
mais simples e suficiente para "uma empresa" é **um agente padrão por inbox**:

```sql
-- via coluna no inbox (doc 02 define a tabela inboxes/channels):
ALTER TABLE inboxes ADD COLUMN default_agent_key TEXT REFERENCES ai_agents(agent_key);
-- a flag inboxes.agent_bot_enabled (doc 01 §6) continua mandando "se o bot responde".
```

> Se no futuro um inbox precisar de **vários** agentes (um router + especialistas), trocar a coluna
> por uma tabela de junção `ai_inbox_agents(inbox_id, agent_key, role)`. Para o MVP, a coluna basta.

**Agente ↔ conversa** (qual agente está *no controle agora* — runtime). A conversa é a unidade de
execução; o handoff troca o agente "ativo" dela:

```sql
-- na tabela conversations (doc 01 §3.2):
ALTER TABLE conversations ADD COLUMN active_agent_key TEXT REFERENCES ai_agents(agent_key);
```

E o `conversation_id` **é** o `session_id` do Agno (R5). Assim, histórico, métricas e memória do Agno
ficam chaveados pela conversa — trocar de agente **dentro** da mesma conversa preserva o contexto
(o que o `ContactMemory` por-contato de hoje não consegue separar).

### 4.7 O que manter vs cortar (resumo)

| Do `/opt/gerenciamento-ia` | No WhatsBot enxuto |
|----------------------------|--------------------|
| `agentes` (key, model_config, prompt, tool_names, hooks, routing, is_router, version) | **Manter** → `ai_agents` |
| `prompts` (chave, tipo, prompt) | **Manter** → `ai_prompts` |
| `variaveis` (config global + ConfigStore) | **Manter** → `ai_variables` (ou prefixo em `config`) |
| `tools` com `code` + `dependencies` + `install_status` | **Manter** → `ai_tools` (code-in-DB — decisão do cliente; ver §5) |
| `dynamic_registry` (cache + polling TTL) | **Manter** o conceito (§9) |
| `agent_factory.build_agent` | **Manter** (núcleo do motor — §8) |
| `tool_installer` (materializa .py + uv pip install) | **Manter, com mitigações** → §5.3/§5.4 (sandbox, ADM-only, auditoria, allowlist de deps) |
| Roteamento multi-hop (depth ≤ 5) | **Manter** (§6) |
| embeddings / pgvector | **Cortar** |
| produtos / ofertas / busca semântica | **Cortar** |
| histórico/versionamento + AiExecution | **Manter** → `*_history` + estender `executions` |

---

## 5. Estratégia de tools — code-in-DB (decisão do cliente)

Esta é a **decisão mais importante e mais sensível** do documento. Há duas filosofias; o cliente
**escolheu a A (code-in-DB)**. A comparação abaixo permanece honesta — A *é* mais arriscada que B —
mas a recomendação muda porque, **neste contexto específico**, o risco é gerenciável e o ganho
operacional é o objetivo declarado do produto.

### 5.1 Opção A — code-in-DB + materialização + pip install (estilo gerenciamento-ia) — **escolhida**

O `tool_installer` guarda o **fonte Python** da tool em `ai_tools.code` (mais `description` e
`dependencies`), materializa um `.py` numa pasta gerenciada, roda `pip install` das `dependencies`
declaradas, faz `importlib.reload`, valida a assinatura e grava `install_status`. **Máxima
flexibilidade**: criar/corrigir uma tool é um INSERT/UPDATE no banco, sem deploy — e a **própria IA**
pode propor/escrever uma tool nova (debug e auto-extensão, motivo explícito do cliente).

**Riscos (concretos — continuam reais, mitigados na §5.4):**

- **Execução de código arbitrário (ACE)** — qualquer código gravado no banco roda com os privilégios
  do processo que o executa. Um fluxo `LLM → JSON → exec/import` é explorável até por **prompt
  injection** (cf. CVE-2024-6982 / LoLLMs — [CyberArk: Anatomy of an LLM RCE](https://www.cyberark.com/resources/threat-research-blog/anatomy-of-an-llm-rce)).
- **Supply-chain** — `pip install <pacote-do-banco>` em runtime é superfície de ataque
  (typosquatting, código em `setup.py`/`__init__`) — [OWASP LLM03:2025 Supply Chain](https://www.indusface.com/learning/owasp-llm-supply-chain/).
- **Persistência** — código malicioso gravado no banco sobrevive a restarts (o mecanismo legítimo de
  persistência vira vetor).
- **Python não é sandboxável in-process de forma confiável** — `RestrictedPython` e afins têm
  histórico de bypass via MRO/`__subclasses__` — [notas sobre sandbox de código não confiável](https://gist.github.com/mavdol/2c68acb408686f1e038bf89e5705b28c). Daí a §5.4 isolar por **processo**, não por restrição de built-ins.
- **OWASP LLM Top 10 2025**: isso é **LLM06 — Excessive Agency** e amplia o blast radius de **LLM01 —
  Prompt Injection** — [OWASP Top 10 for LLM Apps](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
  ([PDF v2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)).

### 5.2 Opção B — tools pré-registradas em código, ativadas/configuradas pelo banco — **não escolhida**

Tools vivem **em código versionado** (como `CORE_TOOLS` e tools de plugin hoje). O banco guarda só
**configuração**: quais tools cada agente usa (`ai_agents.tool_names`), descrições/labels
(`tool_overrides`), hooks (`ai_agents.hooks_config`). **Nenhum** código novo é carregado em runtime.
Era a recomendação da versão anterior deste doc — segura por design, mas exige deploy/PR para cada
tool nova, o que **contraria os dois objetivos do cliente** (mudar sem deploy; IA criar tools).

| Aspecto | A (code-in-DB) — **escolhida** | B (registradas, config no banco) |
|---------|----------------|----------------------------------|
| Flexibilidade | Altíssima (qualquer lógica sem deploy; IA pode criar/corrigir) | Limitada ao conjunto existente (plugins estendem, mas com deploy) |
| Superfície de ataque | Grande (ACE, supply-chain, persistência) — **mitigada na §5.4** | Pequena (código novo exige PR/deploy) |
| Auditoria | Exige trilha própria (`ai_tools_history` + doc 07) — código muda sem git | Trivial (git blame, CI, review) |
| Velocidade de update | Instantânea | Requer deploy |
| Adequação a LLM (OWASP LLM06) | Perigosa **sem** isolamento; aceitável **com** §5.4 num modelo de ameaça baixo | Segura por design |

Referências: [OWASP LLM06 — Excessive Agency](https://owasp.org/www-project-top-10-for-large-language-model-applications/),
[Flatt — LLM framework vulns](https://flatt.tech/research/posts/llm-framework-vulns-exposed/),
[OWASP Top 10 AI Agents 2026](https://blog.alexewerlof.com/p/owasp-top-10-ai-llm-agents).

### 5.3 Recomendação — code-in-DB, e por que é defensável aqui

**Adotar a Opção A (code-in-DB), conforme a decisão do cliente.** A recomendação anterior preferia B
por um motivo legítimo — **B é objetivamente mais segura** — mas esse motivo pressupunha um modelo de
ameaça que **não é o deste produto**. O contraste é o eixo da decisão:

- **Onde code-in-DB é perigoso (e a recomendação anterior fazia sentido):** SaaS **multi-tenant**,
  onde *tenants não-confiáveis* poderiam injetar código que roda no mesmo host de outros clientes;
  superfície exposta à internet; muitos editores anônimos. Aí ACE = comprometer todos os tenants.
- **Onde estamos (decisão do cliente):** **uma empresa, self-hosted (Coolify/Docker), sem
  multi-tenant** (doc 00). Os únicos atores que escrevem código de tool são **o administrador da
  própria empresa e a IA** — ambos já dentro do perímetro de confiança. Não há "tenant hostil".
  O modelo de ameaça é **baixo**: o pior caso (código ruim do admin/IA) é equivalente ao admin já
  poder fazer SSH no servidor. O ganho — mudar comportamento sem deploy e deixar a IA estender o
  próprio toolset — é exatamente o que o produto quer entregar.

Ou seja: **o risco residual de code-in-DB neste contexto é da mesma ordem do acesso que o admin já
tem**, e as mitigações da §5.4 o trazem para um nível operável. Em troca, ganha-se o recurso. Por
isso a inversão é justificável — e estaria *errada* num produto SaaS multi-tenant.

**Convivência com tools/plugins de código (não é "ou um ou outro"):**

- O registry do `agent_factory` é a **união** de três fontes, resolvidas por `name`: (1) `CORE_TOOLS`
  (código), (2) tools de plugin (`_tool_executors`), (3) tools de `ai_tools` (code-in-DB). Em colisão
  de nome, o registry loga warning e mantém a de maior precedência (sugestão: código > banco, para o
  banco nunca "sequestrar" uma tool core; documentar a regra).
- `tool_overrides` continua valendo como camada de enabled/description/label **por tool**; habilitação
  **por agente** vem de `ai_agents.tool_names[]`. Precedência sugerida: o agente só vê tools em
  `tool_names[]` **e** com `tool_overrides.enabled=1` **e** (para code-in-DB) `ai_tools.install_status='ok'`.
- `hooks_config` (call_limit, requires_prior_call) viram **tool hooks do Agno** (`@tool(pre_hook=...)`)
  montados por closure na factory — como no gerenciamento-ia.

#### Mecanismo do "tool installer" (adaptado do gerenciamento-ia)

Fluxo, disparado ao salvar/ativar uma tool (`POST/PUT` no painel) e no boot para tools `enabled=1`:

1. **Materializa** — escreve `ai_tools.code` num `.py` numa **pasta gerenciada dedicada** (ex.:
   `storages/ai_tools/<name>.py`, fora de `agent/` e fora de `storages/plugins/`; user-writable,
   ignorada pelo git). O nome do arquivo deriva de `ai_tools.name` (validado por regex `^[a-z][a-z0-9_]{0,63}$`).
2. **Instala deps** — `install_status='installing'`; roda `pip install` das `dependencies` declaradas,
   **filtradas pela allowlist (§5.4)** e com `--require-hashes` quando viável. Falha → `install_status='failed'`,
   `install_error=<stderr/traceback>`, e a tool **não** entra no registry (fail-closed).
3. **Importa/recarrega** — `importlib.import_module` (ou `importlib.reload` se já carregado) do módulo
   materializado, sob um pacote namespaced (ex.: `whatsbot_ai_tools.<name>`), espelhando como os
   plugins são importados hoje (`whatsbot_plugins.<id>`).
4. **Valida a assinatura** — confere que o módulo expõe o contrato esperado (schema dict + `execute(ctx, args)`,
   o mesmo contrato de `CORE_TOOLS`). Assinatura errada → `failed`.
5. **Grava status** — `install_status='ok'` + bump de `version` + snapshot em `ai_tools_history`
   (`changed_by`). O `dynamic_registry` (§9) passa a oferecer a tool aos agentes.

> **Por que pasta gerenciada e não `exec()` direto:** materializar em `.py` + importlib dá um módulo
> real (traceback legível, `importlib.reload` previsível, isolável por subprocess na §5.4) e reaproveita
> o padrão de import já validado pelo loader de plugins — em vez de `exec(string)` no processo.

> **SQLite vs Postgres no installer:** `dependencies` é lido como JSON em ambos (§4.4). O resto do
> fluxo (materialização, pip, importlib) independe do backend de banco.

### 5.4 Mitigações de segurança obrigatórias (já que adotamos code-in-DB)

Code-in-DB **só** é aceitável com estas defesas — não são opcionais; são a contrapartida da decisão:

- **Isolamento de execução por processo, não in-process.** Como Python não é sandboxável de forma
  confiável dentro do mesmo interpretador, executar tools de `ai_tools` num **subprocess/worker
  dedicado** com limites de SO: `RLIMIT_CPU`/`RLIMIT_AS` (CPU e memória), `timeout` rígido por
  chamada (mata runaway), e — se o host permitir — **seccomp/AppArmor** (libera só os syscalls
  necessários) ou um runner mais forte (gVisor `runsc`/microVM Firecracker) quando o cliente exigir
  blindagem extra. Ver [openedx/codejail](https://github.com/openedx/codejail) (virtualenv + AppArmor + subprocess),
  [dida.do — secure Python sandbox para LLM agents](https://dida.do/blog/setting-up-a-secure-python-sandbox-for-llm-agents),
  [running untrusted Python — Healey](https://healeycodes.com/running-untrusted-python-code),
  [guia de sandboxing 2026](https://manveerc.substack.com/p/ai-agent-sandboxing-guide),
  [SmolVM vs Firecracker](https://particula.tech/blog/smolvm-vs-firecracker-sandbox-ai-generated-code).
  Esse isolamento **muda o peso da §7** (embutido vs separado): code-in-DB empurra a execução de tools
  para fora do processo do webhook.
- **Least privilege do runner.** O worker que roda tools **não** recebe a chave do LLM, nem credenciais
  de admin, nem conexão de escrita ao banco principal; acessa só o que a tool legitimamente precisa
  (passado via `ctx`), e idealmente com **egress filtering** de rede.
- **Edição de código é exclusiva do papel ADM (liga ao [doc 03 — RBAC](03-rbac-usuarios-permissoes.md)).**
  Criar/editar `ai_tools.code` (e `ai_agents`/`ai_prompts`) é a ação mais privilegiada do produto:
  **só** o grupo `adm` pode. Atendentes/plugins **nunca** veem essa tela nem o endpoint. Quando "a IA
  cria uma tool", o artefato fica `enabled=0`/`install_status='pending'` até um ADM revisar e ativar
  (gate humano) — a IA propõe, o humano libera.
- **Auditoria before/after de TODA mudança (liga ao [doc 07 — Auditoria](07-auditoria.md)).** Toda
  criação/edição de tool, agente, prompt e variável grava em `*_history` o snapshot **antes e depois**,
  `changed_by` e timestamp. São as ações mais sensíveis do produto (doc 07 já as classifica assim).
  Rollback = restaurar um snapshot anterior.
- **Dependências validadas e limitadas.** `dependencies` passa por **allowlist** de pacotes; instalar
  algo fora dela exige aprovação ADM explícita. Preferir `pip install --require-hashes` com versões
  pinadas/lockfile para evitar troca silenciosa de artefato — atenção à limitação conhecida de que
  `--require-hashes` **não** cobre build-system deps ([pip — secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/),
  [pip issue #13984](https://github.com/pypa/pip/issues/13984),
  [Semgrep — the lockfile](https://semgrep.dev/blog/2026/the-best-free-open-source-supply-chain-tool-the-lockfile/)).
  Ideal de médio prazo: deps **pré-congeladas** num venv de build, com runtime sem `pip install`
  arbitrário (a tool escolhe entre o que já está instalado).
- **Timeouts e fail-closed.** Toda chamada de tool tem timeout; tool que estoura é abortada e o erro
  vira feedback ao LLM (não trava o webhook). Tool com `install_status≠'ok'` **não** é oferecida.
- **Validação estática leve antes de instalar.** Parse AST do `code` recusando imports/operações
  obviamente perigosas como sinal de alerta na UI (defesa em profundidade, **não** a barreira
  principal — a barreira é o isolamento por processo).

### 5.5 Empacotar o motor como plugin?

O cliente perguntou se "isso poderia ser um plugin". O WhatsBot tem um sistema de plugins maduro
(migrations com prefixo `plugin_<id>_*`, settings declarativas, telas Preact, events/filters, toggle
com restart). Vale analisar empacotar o motor IA (tabelas `ai_*`, installer, UI de agentes/tools/prompts)
como **plugin** vs implementá-lo no **core**.

| Critério | Tudo plugin | Tudo core | **Híbrido (recomendado)** |
|----------|-------------|-----------|---------------------------|
| Prefixo de tabela | **Atrito**: o migrator **força** `plugin_<id>_*`; as tabelas viriam `plugin_aiengine_agents` etc. — feio e quebra os nomes `ai_*` deste doc, dos docs 01/02/07 e o `ALTER TABLE executions/conversations/inboxes` (não dá pra um plugin alterar tabelas do core) | `ai_*` limpas, e os `ALTER TABLE` em `executions`/`conversations`/`inboxes` são triviais | tabelas `ai_*` + ALTERs ficam no **core** (Alembic); plugin não precisa criar tabela de domínio |
| Substituir o `AgentHandler` | **Bloqueio**: o motor **substitui** o ponto onde o `_process_batch` chama o handler — acoplamento profundo com o pipeline. Plugin só tem events/filters; não há filtro que diga "use este motor em vez do handler". Daria pra forçar com `filter.llm.messages`/`filter.system_prompt`, mas roteamento entre agentes, sessão por agente e structured output **não** cabem nesses hooks (a própria §1.2 já constata isso) | Natural: o core decide chamar `ai_engine.run_conversation` | núcleo do motor (factory, run_conversation, roteamento, hooks→filters) no **core** |
| Reload / restart | Toggle de plugin = `os._exit` + supervisor; conviver com o installer (que também recarrega) duplica mecanismos de reload | Hot-reload por evento já desenhado (§9), sem restart para mudança de dado | core controla reload de config (dado) e o installer controla reload de código |
| UI (CRUD agentes/tools/prompts) | **Encaixe natural**: telas Preact de plugin, `config:true`, settings declarativas — exatamente o que a UI do motor precisa, sem inchar o core | Inchaço do core com telas e endpoints de um domínio grande | **UI/CRUD como plugin** (telas + endpoints REST sob `/api/plugins/<id>/...`) consumindo o núcleo do core |
| Manutenção | Um pacote isolado, exportável | Tudo num lugar, mas o core cresce | separação limpa: contrato fino core↔UI |

**Recomendação: híbrido — núcleo do motor no CORE, UI/CRUD opcionalmente como PLUGIN.**

- **No core** ficam: as tabelas `ai_*` (Alembic, sem o prefixo de plugin), os `ALTER TABLE` em
  `executions`/`conversations`/`inboxes`, a `agent_factory`, o `ai_engine.run_conversation`, o
  roteamento (§6), a ponte hooks↔filters (§8.2) e o **tool installer** com as mitigações da §5.4. Isso
  é inevitável: o motor **substitui** o `AgentHandler` no pipeline (§8.1) e um plugin, pelo modelo
  atual de events/filters, **não alcança esse nível de substituição com elegância** — e plugins não
  podem alterar tabelas do core nem fugir do prefixo `plugin_<id>_*`.
- **Como plugin** pode ficar a **camada de apresentação/CRUD**: telas Preact de edição de agentes,
  prompts, variáveis e tools, e os endpoints REST que leem/escrevem as tabelas `ai_*`. O plugin chama
  uma API estável do core (ex.: `from ai_engine import save_agent, list_agents, ...`); o core não
  depende do plugin. Vantagem: a UI evolui/desliga sem mexer no motor, e fica exportável.

Tentar "tudo plugin" esbarra em dois bloqueios estruturais — o **prefixo obrigatório de tabela** e a
**impossibilidade de um plugin substituir o `AgentHandler`** no pipeline — então não é recomendado.
"Tudo core" funciona e é a opção mais simples se não quisermos a separação; o híbrido só extrai a UI
para um plugin quando/se isso trouxer valor. **Decisão default: começar tudo no core (menos peças) e
extrair a UI para plugin como refino**, se o cliente quiser poder desligá-la/versioná-la à parte.

---

## 6. Roteamento multi-agente com Agno

Duas formas de fazer um agente "passar a bola" para outro:

### 6.1 `Team` (orquestração pelo Agno)

Um `Team` com um líder que delega aos membros, em quatro modos
([Teams overview](https://docs.agno.com/teams/overview), [delegation](https://docs.agno.com/teams/delegation)):

| Modo | Comportamento | Custo (LLM calls) |
|------|---------------|-------------------|
| `route` | Líder escolhe **um** membro e retorna a resposta dele direto | Baixo |
| `coordinate` (padrão) | Líder decompõe, delega a vários e **sintetiza** | Alto |
| `broadcast` | Delega a **todos** em paralelo | Médio |
| `tasks` | Loop autônomo de subtarefas | Mais alto |

```python
from agno.team import Team
team = Team(mode="route", members=[comercial, suporte, financeiro], model=OpenAILike(...))
team.print_response(text)     # líder roteia para o membro certo
```

**Prós:** orquestração pronta, contexto compartilhado, menos código nosso.
**Contras:** o "líder" é mais um LLM call (latência/custo); o modelo de transferência é mais "tudo
de uma vez" do que o **handoff sequencial com estado** que o gerenciamento-ia faz (comercial **vira**
suporte e a conversa continua com suporte).

### 6.2 Handoff por tool (estilo gerenciamento-ia)

Cada agente expõe uma tool `transferir_para_outro_agente(agent_key, motivo)` (espelhando o
`routing_targets[]` do banco). Quando o LLM a chama, o motor:

1. valida que `agent_key` ∈ `routing_targets` do agente atual (anti-rota-arbitrária);
2. atualiza `conversations.active_agent_key` (a conversa muda de dono);
3. **re-executa** com o novo agente, **mantendo a mesma `session_id` (= conversation_id)**;
4. incrementa um contador de profundidade; **aborta em depth > 5** (anti-loop, como no original);
5. registra o salto em `executions.routing_steps`.

```python
def run_with_routing(conversation_id, text, depth=0):
    if depth > MAX_ROUTING_DEPTH:           # 5
        return fallback_reply()
    agent_key = get_active_agent(conversation_id)
    agent = agent_factory.build(agent_key, session_id=str(conversation_id))
    result = agent.arun(text)
    if (target := result.handoff_target):    # tool transferir_para_outro_agente foi chamada
        if target in agent_meta(agent_key).routing_targets:
            set_active_agent(conversation_id, target)
            record_routing_step(conversation_id, agent_key, target)
            return run_with_routing(conversation_id, text, depth + 1)
    return result
```

**Prós:** handoff **sequencial com persistência** do agente ativo (exatamente o comportamento
desejado), controle fino de profundidade/validação, e nada impede que o "agente que recebe" seja na
verdade o **humano** (reusar `transfer_to_human` — doc 01 §6). **Contras:** é código nosso (mas
pequeno e previsível).

### 6.3 Recomendação

**Handoff por tool (6.2) como mecanismo principal**, porque casa com o modelo "agente ativo por
conversa" (§4.6) e com o handoff humano que o doc 01 já desenha. Manter o `Team` do Agno como
**opção avançada/futura** para cenários de colaboração (vários agentes contribuindo para uma resposta).
Começar com **um agente por inbox + handoff opcional** é o mínimo viável e o mais barato em latência.

---

## 7. Embutido vs serviço separado — análise central

A pergunta em aberto do cliente. Contexto: **uma empresa, um servidor (Coolify/Docker)**, sem
multi-tenant.

### 7.1 Opção EMBUTIDO — Agno no mesmo processo do WhatsBot

A `agent_factory` e o motor rodam dentro do FastAPI atual; o webhook chama o motor in-process.

### 7.2 Opção SERVIÇO SEPARADO — microserviço Python com o Agno (ou AgentOS)

Um segundo container ("ai-engine") exposto por REST/gRPC; o WhatsBot chama via HTTP. Poderia ser o
[AgentOS](https://docs.agno.com/agent-os/overview) (FastAPI stateless pronto) ou um serviço próprio.

### 7.3 Trade-offs

| Critério | Embutido | Serviço separado |
|----------|----------|------------------|
| **Latência** | Menor — sem hop de rede; chamada in-process | +1 round-trip HTTP por mensagem (pequeno na LAN, mas real) |
| **Conflito de libs** | **Risco**: Agno traz seu próprio stack (pydantic v2, sqlalchemy, openai) — possível choque de versões com o WhatsBot | Isolado: cada serviço com suas deps; zero conflito |
| **Reaproveitar bus de events/filters** | **Trivial** — o motor está no mesmo processo, vê `apply_filter`/`emit` direto | **Difícil** — bus é in-process; replicar via HTTP/eventos é trabalho extra e perde a simplicidade |
| **Acesso ao banco** | Direto (mesmo engine SQLAlchemy, repos atuais) | Precisa de DB compartilhado (Postgres) ou API — SQLite local **não** dá (volume não compartilhável) |
| **Sessão/memória** | Reusa `ContactMemory`/repos | Duplica ou expõe via API |
| **Escala** | Limitada ao processo do WhatsBot (mas é 1 empresa, 1 servidor) | Escala independente (irrelevante para 1 empresa hoje) |
| **Deploy Coolify** | **Mais simples** — 1 container, sem orquestração extra | +1 serviço no compose, +1 healthcheck, +1 superfície de rede |
| **Isolamento de falha** | Crash do motor pode derrubar o webhook | Falha isolada; WhatsBot degrada com graça |
| **Restart por config** (hot-reload) | Já temos `schedule_restart` (plugins) | Restart independente do motor |
| **Caminho para code-in-DB (DECIDIDO — §5)** | **Ruim** *se* a tool rodasse no processo principal; OK se as tools de `ai_tools` rodarem num **subprocess/worker isolado** (§5.4) chamado pelo motor embutido | **Bom** — runner sandboxado natural fora do core, mas o webhook ainda fala HTTP com ele |
| **Complexidade total** | **Baixa** | Média/alta (rede, contrato de API, observabilidade distribuída) |

### 7.4 Recomendação

**EMBUTIDO para o motor (factory/orquestração/roteamento), com a EXECUÇÃO DE TOOLS code-in-DB num
runner isolado.** A decisão de code-in-DB (§5) **não** obriga a tirar o motor inteiro do processo: ela
obriga a tirar **só a execução do código das tools** do processo do webhook (§5.4). O desenho que
concilia as duas decisões é:

- **Motor embutido** — `agent_factory`, `run_conversation`, roteamento e a ponte hooks↔filters rodam
  in-process no FastAPI atual (mantém os ganhos abaixo).
- **Runner de tools isolado** — quando um agente chama uma tool de `ai_tools`, o motor a executa num
  **subprocess/worker sandboxado** (limites de SO, sem credenciais, timeout — §5.4), e recebe o
  resultado de volta. Tools de `CORE_TOOLS`/plugin (código revisado) podem continuar in-process.

Razões para manter o **motor** embutido (uma empresa, um servidor):

1. **Reaproveitar o bus de events/filters in-process é o maior ativo do WhatsBot** — o motor precisa
   ler/escrever no mesmo pipeline (filters reescrevem prompt/tools; events alimentam auditoria). Pôr
   o motor fora do processo torna isso um problema de integração distribuída sem ganho real para
   "1 empresa".
2. **Latência e simplicidade de deploy** — sem hop de rede, sem segundo container, sem DB obrigatório
   compartilhado (SQLite default continua funcionando).
3. **Custo de escala é teórico hoje** — 1 empresa não justifica a complexidade de um microserviço.

**Mitigar os contras do embutido:**

- **Conflito de libs**: fixar versões e validar `pydantic`/`sqlalchemy`/`openai` num venv limpo antes
  de adotar; o Agno é leve e OpenAI-compatible, então o atrito tende a ser pequeno.
- **Isolamento de falha**: envelopar o motor com o mesmo tratamento de erro robusto do handler atual
  (try/except → fallback de mensagem), nunca deixar exceção do Agno derrubar o webhook.

**Quando reconsiderar (mover o motor inteiro para serviço separado):** (a) se o runner isolado de
tools (§5.4) acabar exigindo tanto isolamento (microVM/Firecracker, egress filtering pesado) que valha
a pena empurrar **todo** o motor para fora — mas note que mesmo então só a *execução de tools* precisa
do isolamento forte, não a orquestração; (b) se um dia virar **multi-tenant** com escala independente
(aí code-in-DB deixaria de ser defensável no formato da §5 e exigiria reprojeto); (c) se houver
conflito de dependências insolúvel. Desenhar a `agent_factory` por trás de uma **interface fina** (uma
função `run_conversation(conversation_id, agent_key, text) -> reply`) para que trocar "in-process" por
"chamada HTTP" no futuro seja localizado.

---

## 8. Integração com o pipeline atual

O fluxo de hoje (CLAUDE.md → "Fluxo de mensagens"): GOWA → webhook → batch (acumula por
`message_batch_delay`) → `agent_handler.process_message()` → `gowa_client.send_message()`.

### 8.1 Onde o Agno entra

O ponto de substituição é **uma camada acima** do `AgentHandler.process_message`. Hoje o `_process_batch`
chama o handler singleton. No motor multi-agente:

```python
# pseudo — substitui a chamada direta ao handler singleton
async def _process_batch(phone, text, ...):
    conversation = resolve_conversation(phone, inbox_id)        # doc 01
    if not inbox.agent_bot_enabled:                              # doc 01 §6 — bot ligado?
        return                                                   # humano atende
    agent_key = conversation.active_agent_key or inbox.default_agent_key
    reply = await ai_engine.run_conversation(conversation.id, agent_key, text)
    await gowa_client.send_message(phone, reply)
```

`ai_engine.run_conversation` é o novo motor: monta o agente (`agent_factory.build`), aplica roteamento
(§6) e devolve a resposta. O `AgentHandler` atual pode ser **mantido como fallback** (config "motor
legado") durante a transição, ou **encapsulado** — `agent_factory` pode até construir um "agente"
que internamente chama o handler antigo, para migração incremental.

### 8.2 Como reusar filters e events (sem reescrever)

O Agno tem seus próprios `pre_hooks`/`post_hooks` e `tool_hooks`. A ponte é fazer esses hooks
**chamarem o bus existente**, preservando todos os plugins que já dependem dele:

| Filter/event atual | Onde plugar no Agno |
|--------------------|---------------------|
| `filter.system_prompt` | aplicar ao `description`/`instructions` **renderizados** antes de instanciar o `Agent` |
| `filter.llm.messages` | `pre_hook` do agente reescrevendo as mensagens montadas |
| `filter.llm.tools` | filtrar `tool_names` resolvidos antes de passar ao `Agent` |
| `filter.tool.args` / `filter.tool.result` | `pre_hook`/`post_hook` de cada tool (`@tool(pre_hook=...)`) |
| `tool.before` / `tool.after`, `llm.before` / `llm.after` | hooks emitindo os mesmos events |
| `filter.reply.raw` / `parts` / `part` | no `_send_reply` (já fora do handler — vale para qualquer motor) |

```python
# agent_factory: aplicar os filters do WhatsBot ao montar o agente
rendered_prompt = render_prompt(agent_cfg, conversation)         # placeholders + ai_variables
rendered_prompt = apply_filter_sync("filter.system_prompt", rendered_prompt, {"phone": phone})
tool_names = apply_filter_sync("filter.llm.tools", agent_cfg.tool_names, {"phone": phone})
agent = Agent(description=rendered_prompt, tools=resolve_tools(tool_names), ...)
```

Assim, os plugins de filter (`horario_funcionamento`, `blacklist`, `auto_signature` etc.) continuam
funcionando **sem alteração** — eles operam sobre prompt/tools/reply, que continuam existindo.

### 8.3 Sessão/memória: Agno `db` vs nosso `ContactMemory`

Duas escolhas:

- **(a) Deixar o Agno gerir a sessão** (`db=SqliteDb/PostgresDb`, `session_id=conversation_id`,
  `add_history_to_context=True`) — tabelas `agno_sessions` no mesmo banco. Menos código nosso; histórico
  do Agno separado do nosso `messages`.
- **(b) Continuar montando o histórico nós mesmos** (de `messages`/`ContactMemory`) e passar ao Agno
  como contexto — mantém **uma** fonte de verdade (`messages` é o que a UI mostra).

**Recomendação:** **(b)** no início — `messages` já é a fonte de verdade da timeline na UI, e duplicar
em `agno_sessions` cria divergência. Passamos as últimas N mensagens (como `get_context_messages` já
faz) para o agente. Memória de fatos do contato (`enable_user_memories`) também fica **com o nosso**
sistema (`contacts`/`observations` + tool `save_contact_info`), evitando dois lugares para a mesma
informação. Reavaliar (a) se quisermos os recursos de memória autônoma do Agno.

---

## 9. Hot-reload de config e versionamento

O gerenciamento-ia usa um `dynamic_registry`: cache em memória de agentes/tools, **polling com TTL
~60s**, fallback para código legado. Duas estratégias para o WhatsBot:

| Estratégia | Como | Prós | Contras |
|------------|------|------|---------|
| **Polling TTL** (estilo original) | `agent_factory` cacheia `ai_agents`/`ai_prompts`/`ai_variables` e revalida a cada N s (ou checa `MAX(version)`) | Simples, sem acoplamento; tolera múltiplos processos | Latência de até N s para refletir mudança |
| **Invalidação por evento** | PUT no painel emite `ai.config.changed` (bus) → factory limpa o cache; em multi-processo, `schedule_restart` ou um canal Postgres `LISTEN/NOTIFY` | Reflexo imediato | Mais peças; em SQLite multi-worker o evento é local ao processo |

**Recomendação:** **invalidação por evento + fallback de polling**. Como o motor é embutido (§7), o
PUT em `/api/agents/{key}` pode invalidar o cache do `agent_factory` no mesmo processo na hora; um
TTL curto (ex: 60s) cobre o caso de o uvicorn rodar com `--workers > 1`. Mudar um
agente/prompt/variável é só **dado** — **não precisa de restart**, basta invalidar o cache.

**Code-in-DB tem dois níveis de "reload" (§5):**

- **Dado** (agente/prompt/variável, e `enabled`/`description` de uma tool) — invalidação de cache, a
  quente, sem restart.
- **Código** (o `ai_tools.code` mudou) — passa pelo **tool installer** (§5.3): materializa o `.py`,
  reinstala deps se mudaram, `importlib.reload`, valida, grava `install_status`. Isso recarrega **só
  aquele módulo de tool**, sem reiniciar o servidor inteiro (diferente de **enable/disable de plugin**,
  que exige `schedule_restart`). Se a reinstalação/reload falhar, a tool fica `failed` e **não** entra
  no registry — fail-closed, o motor segue com as demais tools.

**Versionamento:** cada save em `ai_agents`/`ai_prompts`/`ai_tools` faz `version += 1` e grava snapshot
em `*_history` com `changed_by` (usuário — doc 03). Isso alimenta a **auditoria** (doc 07), que lista
mudanças em prompt/agente/tools/**código de tool** como as mais sensíveis do produto. Rollback =
restaurar um snapshot (no caso de tool, o rollback dispara um novo ciclo do installer).

---

## 10. Faseamento / MVP

1. **Fundação de dados** — criar `ai_agents`, `ai_prompts`, `ai_variables`, `ai_tools` (+ `*_history`);
   estender `executions`/`execution_steps` com `agent_key`/`routing_steps`. Migração Alembic (tabelas
   `ai_*` no **core**, §5.5). Seed: **um** agente "default" cujo prompt = `config["system_prompt"]`
   atual (paridade total com hoje).
2. **Um agente configurável no banco** — `agent_factory.build(agent_key, session_id)` montando um
   `agno.Agent` com `OpenAILike` (proxy Techify), prompt renderizado (placeholders + `ai_variables`),
   tools resolvidas do registry (union `CORE_TOOLS`/plugin; ainda **sem** code-in-DB nesta fase).
   `ai_engine.run_conversation` substitui (atrás de flag) a chamada ao `AgentHandler.process_message`.
   Pluga os filters atuais (§8.2). **Sem roteamento ainda.** Critério de aceite: respostas idênticas
   ao handler de hoje, com a config vinda do banco.
3. **Code-in-DB (tool installer + runner isolado)** — `ai_tools` ativa: materialização `.py` + pip
   (allowlist) + `importlib.reload` + `install_status` (§5.3), **execução de tool num subprocess/worker
   sandboxado** com limites/timeout (§5.4). Gate ADM (doc 03) na edição de código + auditoria
   before/after (doc 07). Critério de aceite: criar/editar uma tool pelo painel reflete sem deploy; uma
   tool ruim falha fechada sem derrubar o webhook; toda mudança fica auditada.
4. **Multi-agente por inbox** — `inboxes.default_agent_key` (doc 02); o webhook escolhe o agente pelo
   inbox. Painel para CRUD de agentes/prompts/variáveis/tools (com versionamento e auditoria) —
   **avaliar empacotar essa UI como plugin** (§5.5). Hot-reload por evento (§9).
5. **Roteamento (handoff)** — tool `transferir_para_outro_agente` + `routing_targets[]` +
   `conversations.active_agent_key` + profundidade ≤ 5 + `routing_steps` (§6.2). Integrar com o
   handoff **humano** (doc 01 §6).
6. **Refinos** — structured output do Agno para o "split em mensagens" (substitui o parse de JSON
   frágil); hooks declarativos (call_limit/requires_prior_call) via `hooks_config`; tool hooks;
   "a IA propõe tool nova" (artefato `enabled=0`/`pending` até ADM aprovar — §5.4).
7. **(Futuro / fora do MVP)** — `Team` do Agno para colaboração; isolamento mais forte do runner de
   tools (microVM/Firecracker) se o cliente exigir blindagem extra (§5.4/§7).

---

## 11. Perguntas em aberto

1. **Sessão**: Agno `db` (tabelas `agno_*`) ou continuar montando histórico de `messages` (§8.3)?
   Recomendado (b), mas confirmar se a memória autônoma do Agno será desejada cedo.
2. **Granularidade agente↔inbox**: um agente por inbox (coluna) basta, ou já prever vários agentes
   por inbox (tabela de junção + router)? (§4.6)
3. **Precedência de habilitação de tool**: `ai_agents.tool_names[]` ∩ `tool_overrides.enabled` — qual
   ganha em conflito? Proposta: interseção (ambos precisam permitir).
4. **`ai_variables` dedicada vs prefixo em `config`** (§4.3) — decisão barata, mas decidir antes do
   schema.
5. **Workers do uvicorn**: roda com `--workers > 1` em produção (Coolify)? Isso muda a estratégia de
   hot-reload (precisa de TTL/NOTIFY, não só invalidação local). (§9)
6. **Isolamento do runner de code-in-DB** (§5.4): qual nível no dia-1 — subprocess com `RLIMIT_*` +
   timeout (mais simples, suficiente para o modelo de ameaça baixo) ou já partir para seccomp/AppArmor/
   microVM? O host Coolify/Docker permite seccomp/AppArmor? (decide quanto da §5.4 é viável de cara)
7. **Gate da IA criando tools** (§5.4): "a IA cria uma tool" sempre nasce `enabled=0`/`pending` e
   espera aprovação ADM, ou existe um modo (config) em que tools propostas pela IA já entram ativas?
   (trade-off entre autonomia pedida pelo cliente e segurança)
8. **Allowlist de dependências** (§5.4): partir de uma allowlist fixa de pacotes pip ou permitir
   qualquer dep com aprovação ADM por item? Usar `--require-hashes`/lockfile desde já?
9. **Plugin vs core para a UI do motor** (§5.5): extrair a UI/CRUD de agentes/tools/prompts como
   plugin desde o início, ou deixar tudo no core e extrair depois? (recomendação: core primeiro)
10. **Precedência tool de código × tool de banco** (§5.3): em colisão de `name`, `CORE_TOOLS`/plugin
    ganha de `ai_tools` (proposta) — confirmar a regra e como sinalizar a colisão na UI.
11. **Custo de roteamento**: handoff sequencial multiplica chamadas LLM por salto. Definir o teto de
    profundidade (5?) e o comportamento de fallback ao estourar.
12. **Migração do `AgentHandler`**: rodar os dois em paralelo (flag) por quanto tempo? Quando aposentar
    o singleton?
13. **`output_schema` (structured output)** vs o atual "JSON array de strings via prompt": migrar o
    split de mensagens para Pydantic validado? (mais robusto, mas muda o contrato do prompt)

---

## 12. Referências

**Agno (oficial)**
- [GitHub — agno-agi/agno](https://github.com/agno-agi/agno) · [Releases](https://github.com/agno-agi/agno/releases)
- [Docs (home)](https://docs.agno.com/) · [v2 Changelog](https://docs.agno.com/other/v2-changelog)
- [Agent (reference)](https://docs.agno.com/reference/agents/agent)
- [Tools — overview](https://docs.agno.com/basics/tools/overview) · [Criando tools](https://docs.agno.com/basics/tools/creating-tools/overview) · [Decorator `@tool`](https://docs.agno.com/reference/tools/decorator) · [Tool hooks](https://docs.agno.com/basics/tools/hooks) · [Toolkits](https://docs.agno.com/basics/tools/creating-tools/toolkits)
- [Models — OpenAI-like](https://docs.agno.com/models/openai-like) · [OpenRouter gateway](https://docs.agno.com/integrations/models/gateways/openrouter/overview)
- [Teams — overview](https://docs.agno.com/teams/overview) · [delegation](https://docs.agno.com/teams/delegation) · [coordinate](https://docs.agno.com/teams/coordinate)
- [Database — overview](https://docs.agno.com/basics/database/overview) · [SQLite](https://docs.agno.com/database/sqlite) · [Session storage](https://docs.agno.com/database/session-storage) · [Memory](https://docs.agno.com/memory/overview) · [Sessions — persisting](https://docs.agno.com/sessions/persisting-sessions/overview)
- [Structured output](https://docs.agno.com/input-output/structured-output/agent) · [Hooks (pre/post)](https://docs.agno.com/basics/hooks/overview)
- [Performance](https://docs.agno.com/performance) · [AgentOS](https://docs.agno.com/agent-os/overview) · [Instalação](https://docs.agno.com/how-to/install)

**Comparação de frameworks**
- [ZenML — Agno vs LangGraph](https://www.zenml.io/blog/agno-vs-langgraph)
- [LangWatch — melhores frameworks 2025](https://langwatch.ai/blog/best-ai-agent-frameworks-in-2025-comparing-langgraph-dspy-crewai-agno-and-more)
- [Langfuse — comparação de agentes](https://langfuse.com/blog/2025-03-19-ai-agent-comparison)
- [DigitalOcean — Understanding Agno](https://www.digitalocean.com/community/conceptual-articles/agno-fast-scalable-multi-agent-framework)

**Segurança (code-in-db / tools de LLM)**
- [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/) · [PDF v2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)
- [OWASP LLM03:2025 — Supply Chain](https://www.indusface.com/learning/owasp-llm-supply-chain/)
- [CyberArk — Anatomy of an LLM RCE](https://www.cyberark.com/resources/threat-research-blog/anatomy-of-an-llm-rce)
- [Flatt — LLM framework vulns exposed](https://flatt.tech/research/posts/llm-framework-vulns-exposed/)
- [OWASP Top 10 AI/LLM Agents 2026 (Ewerlöf)](https://blog.alexewerlof.com/p/owasp-top-10-ai-llm-agents)
- [AI agent sandboxing guide 2026](https://manveerc.substack.com/p/ai-agent-sandboxing-guide) · [SmolVM vs Firecracker](https://particula.tech/blog/smolvm-vs-firecracker-sandbox-ai-generated-code) · [Notas sobre sandbox de código não confiável](https://gist.github.com/mavdol/2c68acb408686f1e038bf89e5705b28c)

**Execução isolada de código Python (code-in-DB)**
- [openedx/codejail — execução segura via virtualenv + AppArmor + subprocess](https://github.com/openedx/codejail)
- [dida.do — secure Python sandbox para LLM agents](https://dida.do/blog/setting-up-a-secure-python-sandbox-for-llm-agents)
- [Running Untrusted Python Code — Andrew Healey](https://healeycodes.com/running-untrusted-python-code)
- [UBOS — sandboxing untrusted Python](https://ubos.tech/news/sandboxing-untrusted-python-code-secure-execution-strategies-and-ubos-solutions/)

**Supply chain / pip seguro**
- [pip — Secure installs (`--require-hashes`)](https://pip.pypa.io/en/stable/topics/secure-installs/) · [pip lock](https://pip.pypa.io/en/stable/cli/pip_lock/)
- [pip issue #13984 — `--require-hashes` não cobre build-system deps](https://github.com/pypa/pip/issues/13984)
- [PEP 751 — formato de lockfile](https://peps.python.org/pep-0751/) · [Semgrep — the lockfile (2026)](https://semgrep.dev/blog/2026/the-best-free-open-source-supply-chain-tool-the-lockfile/)

**Internas (WhatsBot)**
- [`00-visao-geral.md`](00-visao-geral.md) · [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md) · [`02-canais-e-providers.md`](02-canais-e-providers.md) · [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md) · [`07-auditoria.md`](07-auditoria.md)
- Código: `agent/handler.py` (singleton, filters, dispatch), `agent/tools/__init__.py` (`CORE_TOOLS`), `agent/memory.py` (`ContactMemory`), `plugins/events.py` (bus de events/filters).
