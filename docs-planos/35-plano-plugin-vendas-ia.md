# Plano 35 — Plugin `vendas_ia`: a camada de vendas do Nexus BIA no WhatsBot (catálogo read-only + busca híbrida + palavra-chave→oferta + agentes)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-07 · **Escopo:** grande (1 plugin novo, ~11 arquivos, 1 migration própria, seed/adaptação de 4 agentes — **zero mudança no core**).
> **Origem:** pedido do usuário (Thiago) — trazer para o WhatsBot as capacidades de venda da IA do Nexus ("BIA"): consultar ofertas/produtos/FAQ, casar palavra-chave→oferta, e busca híbrida (embedding + full-text). O WhatsBot vai ser distribuído ⇒ **tudo vira plugin**, nada no core.
> **Método:** investigação nesta sessão — workflow de 7 subagentes lendo o código Nexus (`/opt/nexus/gerenciamento-ia/ai/src`, `/opt/nexus/produtos`) + inspeção **read-only** do banco de produção `RBNexusDB@10.8.100.5`, cruzada com leitura do código real do WhatsBot (`arquivo:linha` verificados abaixo). Reports em `scratchpad/nexus-reports/`. Memórias: [[nexus-production-db]], [[nexus-catalog-port-to-whatsbot]].
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase.** **Um refactor/feature por commit.** Nunca avançar com teste vermelho não-explicado. Como não há core sendo alterado, o risco de regressão é baixo — o foco é o plugin funcionar de ponta a ponta.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-07) | **Ler o `RBNexusDB` direto** (2ª conexão SQLAlchemy read-only nas settings do plugin). NÃO replicar CRUD de ofertas/produtos/FAQ. | Nenhuma tabela de catálogo no WhatsBot. A busca vetorial roda **no servidor do Nexus** ⇒ o Postgres do WhatsBot **não precisa de pgvector**. |
| **D2** ✅ (2026-07-07) | O Nexus (módulo `produtos`) **já mantém os embeddings frescos** (sync incremental por `content_hash`). | **SEM job de sync** no plugin. O WhatsBot só **embeda a query** na hora da busca; nunca re-embeda o corpo. |
| **D3** ✅ (2026-07-07) | **Busca híbrida** reusando `gerenciamento_ia_embeddings_{oferta,curso,faq}` (`vector(768)`, modelo `qwen/qwen3-embedding-8b` via OpenRouter): CTE `semantic` (cosine ×0.7) + `fulltext` (`ts_rank` ×0.3), fallback ILIKE. | O plugin gera o embedding da query via **OpenRouter** (mesmo modelo/dims dos vetores armazenados — obrigatório pra o cosine bater). |
| **D4** ✅ (2026-07-07) | **Palavra-chave determinística pré-LLM:** substring (lower, `;`-split) em `key_words` das ofertas `is_active_for_ia` → **fixa a oferta** na conversa + **força o agente comercial** (`active_agent_key`), pulando o roteador. | Um handler de plugin em fase de **ingest** (roda antes do turno de IA em lote). Espelha a lógica de `triggers.py:43` + `chatwoot_handler.py:85-181` do Nexus. |
| **D5** ✅ (2026-07-07) | **Estado de IA por conversa = híbrido:** tabela `plugin_vendas_ia_conversa` (fonte de verdade, estado rico) + **espelho** de `oferta_atual` em `conversations.custom_attributes` (padrão do plugin `protocolos`) + injeção do bloco **"OFERTA EM FOCO"** via `PROMPT_FRAGMENT`. | Nada de mensagem sintética: a oferta entra na camada **system prompt**, não como turno de usuário. "IA atual" NÃO vira atributo — usa `conversations.active_agent_key` nativo. |
| **D6** ✅ (2026-07-07) | **Migrar E adaptar** os 4 prompts de agentes do Nexus (ROUTER 8KB / COMERCIAL 29KB / SUPORTE 6KB / FECHAMENTO 4KB) para `ai_agents`, **remapeando as tools** Chatwoot→WhatsBot. Nomes das 3 tools de busca = **idênticos aos do Nexus** (`pesquisar_ofertas`, `pesquisar_informacoes_cursos`, `pesquisar_perguntas_frequentes`) para minimizar edição dos prompts. | Seed **não-destrutivo** (só cria se o `agent_key` não existir). Fechamento/protocolo, `gerar_deep_link`, `enviar_imagens_oferta` ficam **fora de escopo**. |
| **D7** ✅ (2026-07-07) | **Guardas de mensagem** (do Nexus): (a) **inbound** — regex que **não ativa a IA** (mas mantém a msg no painel), espelha `mensagens_ignoradas_usuario`/`check_ignored_messages_user`; (b) **outbound** — regex que **bloqueia** a IA de mandar erro do OpenRouter/alucinação ao cliente, espelha `mensagens_ignoradas_ia`/`is_ignored_ia_message`. | Fase F9. Seams prontos, **zero core**: inbound via `filter.llm.messages`→`None` (salva mas não responde — ≠ `before_save`→`None` que descarta); outbound via `filter.reply.raw`/`filter.reply.part`→`None`. Colocação (plugin irmão `guarda_ia` vs dentro de `vendas_ia`) = **P6**. |
| **Princípio fixo** | **Zero core.** Tudo via seams de plugin já existentes (tools/filters/events/prompt-fragments/migrations/settings/screen). Se algo exigir mudar o core, **para e vira pergunta** (P-aberta), não se contorna com hack. Segredos (DSN Nexus, chave OpenRouter) só em settings do plugin — nunca em URL/log. Modo escuro obrigatório na tela de config. | O plugin é auto-contido e distribuível sozinho; só ativa na instalação que tiver o Nexus. |

---

## 1. Resumo executivo

O Nexus "BIA" é um runtime multi-agente (Python/AGNO, atrás do Chatwoot) cujo **motor** (roteador hub-and-spoke, tools, guardrails, config-in-DB) o **WhatsBot já tem** — o plano 29 diz explicitamente que foi "portado do nexus `gerenciamento-ia`". O que **falta** no WhatsBot é a **camada de domínio (vendas)**: consultar o catálogo de ofertas/produtos/FAQ, casar palavra-chave→oferta, e a busca híbrida (embedding + full-text). Este plano entrega isso como **um único plugin** `vendas_ia` que **lê o banco do Nexus direto** (fonte única, sem duplicar dados nem re-embedar), expõe **3 tools de busca** com os mesmos nomes do Nexus, um **filtro de palavra-chave** que fixa a oferta e força o agente comercial, um **estado por conversa** (tabela + espelho em `custom_attributes`), a **injeção da oferta em foco** no system prompt, e o **seed adaptado dos 4 agentes** do Nexus. Nada no core muda.

---

## 2. Como funciona hoje (mapa)

### 2.1 No Nexus (o que vamos replicar) — `arquivo:linha` verificados

| Mecanismo | Onde (Nexus) | Essência |
|---|---|---|
| Carregar ofertas p/ match | `ai/src/services/database.py:123` | `SELECT id, name, offercode, key_words FROM produtos_ofertas WHERE is_active_for_ia = true` (cache 5 min) |
| Match de palavra-chave | `ai/src/rules/triggers.py:43` `check_keyword_match` | `msg.lower()`; para cada oferta, `key_words.split(";")`, `kw.strip().lower() in msg` → **substring**, primeiro match vence |
| Atribuir oferta + forçar comercial | `ai/src/webhooks/chatwoot_handler.py:85-109` e `:158-181` | grava `codigo_oferta_atual`/`curso_de_interesse`/`tipo_de_atendimento` (custom attrs Chatwoot) **sem LLM** e força `tipo="COMERCIAL"` (pula o ROUTER) |
| Oferta em foco no prompt | `ai/src/services/prompt_context.py:56` `_oferta_em_foco` | resolve `{oferta_em_foco}` a partir do `codigo_oferta_atual` salvo |
| Busca híbrida (SQL) | `ai/src/tools/pesquisar_ofertas.py:68-126` (espelhado em `pesquisar_cursos.py`/`pesquisar_faq.py`) | 2 CTEs: `semantic` (`1-(embedding <=> :vec)`, HNSW, LIMIT 10) + `fulltext` (`ts_rank(search_vector, plainto_tsquery('portuguese', :q))`, GIN, LIMIT 10) → `FULL OUTER JOIN`, `score = sem*0.7 + ft*0.3`, `DISTINCT ON`, `LIMIT 5`; fallback ILIKE se o embedding falhar |
| Embedding da query | `ai/src/services/embeddings.py:42-83`; literal `::vector` em `:144` | `OpenAI(base_url="https://openrouter.ai/api/v1").embeddings.create(model=qwen/qwen3-embedding-8b, input=..., dimensions=768)`; `embedding_to_pg` → `"[0.1,0.2,...]"` |
| O que é vetorizado | `ai/src/services/embedding_sync.py:80-103` | oferta = `name+description+key_words+bonus`; curso = `nome+descricao+topicos+publico_alvo+palavras_chave+observacoes`; faq = `question+answer` |

**Banco Nexus (`RBNexusDB`, schema `public`)** — o read-side (trust `\d+`, o Prisma está desatualizado):
- `produtos_ofertas`: `id`(uuid), `offercode`(único, handle estável), `name`, `description`, `key_words`(`;`-sep), `is_active_for_ia`(gate), `valor_atual`(texto livre), `bonus`, `link_pagina_vendas`, `link_checkout`, `tempo_acesso`, `prompt_dinamico_ia`(instrução — **não expor ao cliente**), `lotes`(jsonb, vazio hoje). 7 linhas (3 IA-ativas).
- `produtos_produtos`: `id`, `nome`, `descricao`, `carga_horaria`(`HH:MM:SS`), `instrutores`, `certificado`(bool), `topicos`(`;`-sep), `publico_alvo`, `active_ia`(gate), `oferta_id`(FK→ofertas, nullable), `oficial`(bool), `observacoes`, `palavras_chave`. 97 linhas (13 IA-ativas).
- `produtos_faq`: `id`, `question`, `answer`, `id_ofertas`(**text[] de uuids** ou `'*'`=todas), `order`, `active`. 35 linhas (10 com `{*}`).
- `gerenciamento_ia_embeddings_{oferta,curso,faq}`: `<pk>_id`(FK cascade), `content_text`, `content_hash`(md5), `search_vector tsvector GENERATED ('portuguese')` (GIN), `embedding vector(768)` (HNSW cosine). Contagens: 5 / 13 / 35.
- Config (variáveis Nexus, valores atuais): `hybrid_semantic_weight=0.7`, `hybrid_fulltext_weight=0.3`, `hybrid_limit=5`, `embedding_dims=768`, `openrouter_model_embedding=qwen/qwen3-embedding-8b`.

### 2.2 No WhatsBot (o que reusamos — NÃO re-implementar) — `arquivo:linha` verificados

| Capacidade WhatsBot | Onde | Uso no plugin |
|---|---|---|
| Motor AGNO + hub-and-spoke | `ai_engine/routing.py:39` `run_with_routing`; `agent/tools/transferir_agente.py`; `transfer_to_human` | Os agentes seedados usam `transferir_agente`/`transfer_to_human` nativos |
| Router único enforced | índice parcial `ux_ai_agents_single_router` (`db/tables.py`), demote em `agent_repo.save` (`db/repositories/agent_repo.py:186-231`) | ⚠️ cuidado ao seedar o roteador (ver P1) |
| Config-in-DB de agentes | `ai_agents` (`db/tables.py:579-657`), `agent_repo.save` (`db/repositories/agent_repo.py:120`) | Seed dos 4 agentes |
| Resolução do agente ativo | `agent/agent_factory.py:204` `resolve_active_agent_key` (precedência conversation→inbox→default); build em `:290` `build_for_contact` | Forçar comercial = gravar `conversations.active_agent_key` |
| Coluna do agente ativo | `db/tables.py:426` `conversations.active_agent_key` | "IA atual" — **substitui** o atributo Chatwoot |
| `custom_attributes` de conversa (JSONB, GIN) | `db/tables.py:436` | Espelho de `oferta_atual` |
| Injeção automática de atributos no prompt | `agent/memory.py:511` `get_info_summary` → `:528` `_custom_attr_lines` | O valor espelhado aparece sozinho; a instrução rica vai via fragment |
| Definir/gravar atributo | `db/repositories/custom_attribute_repo.py:91` `ensure_system_definition`, `:187` `set_values`, `:177` `get_values` | Semear a def `oferta_atual` (escopo `conversation`, `is_system`) e gravar |
| Guardrails de tool | `ai_engine/hooks.py` (`requires_prior_call` success-aware + `call_limit`), config via `ai_agents.hooks_config` | Configurar cursos/faq p/ exigir `pesquisar_ofertas` antes — **sem código novo** |
| Seam: tools de plugin | `plugins/loader.py` (`entry.tools`→`CORE_TOOLS=[(schema,exec)]`); `ToolContext` `plugins/context.py:247` | As 3 tools de busca |
| Seam: prompt fragment | `PROMPT_FRAGMENTS=[callable]` (`plugins/loader.py:257`; `handler.register_plugin_prompts` `agent/handler.py:124`); montado em `agent/handler.py:271` `_build_system_prompt`→`prompt_builder.build_system_prompt` | Bloco "OFERTA EM FOCO" |
| Seam: filtro/evento de ingest | `filter.message.before_save` (`app/services/message_ingest_service.py:446`), `message.received` (`:482`), `message.saved` (`:491`) | Match de palavra-chave (ver §2.3) |
| Seam: DB do plugin + migration | `make_plugin_db` (`plugins/context.py`); migrator com prefixo `plugin_<id>_` obrigatório (`plugins/migrator.py`) | Tabela `plugin_vendas_ia_conversa` |
| Espelho custom_attributes (padrão pronto) | `assets/plugin_examples/protocolos/logic.py:494-552` (`set_values(conversations,...)` + `ensure_system_definition`) | Copiar o padrão |
| Settings declarativas + screen config | `assets/plugin_examples/protocolos/{settings.py,plugin.yaml}` | DSN Nexus, chave OpenRouter, pesos, toggle |
| Client OpenAI-compatível | `agent/llm.py` + `config/settings.py:17` `LLM_API_BASE_URL`; chave em `config["openrouter_api_key"]` | ⚠️ é a chave do **Techify**, não do OpenRouter — o plugin usa sua **própria** chave OpenRouter (ver §2.4) |

### 2.3 A janela de tempo (o achado que destrava "forçar comercial no mesmo turno")

Sequência inbound verificada:
1. Cada mensagem que chega passa por `message_ingest_service.py`: **`filter.message.before_save`** (`:446`) → salva → **`message.received`** (`:482`) → **`message.saved`** (`:491`, conversa **já existe**).
2. Depois do `message_batch_delay`, `messaging_service._process_batch` roda: `add_message("user", combined)` (`:815`) → **`aprocess_message`** (`:839`/`:1001`) → `build_for_contact` lê `active_agent_key`.

⇒ Todo o ingest (com filtro + eventos) de **cada** mensagem termina **antes** do turno de IA em lote. Então um handler `message.saved` que grava `conversations.active_agent_key='comercial'` **está gravado a tempo** de o build do agente ler. **Hook escolhido: `message.saved`** (a conversa já existe; `before_save` roda antes do save/da conversa e é pior para isto).

### 2.4 Falsos positivos descartados

| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| "Precisa de pgvector no Postgres do WhatsBot" | ❌ Descartado | A SQL vetorial (`<=>`, `::vector`, `tsvector`) roda **no servidor do Nexus** via a 2ª conexão; o psycopg do WhatsBot só manda texto. (D1) |
| "Precisa de um job de sync de embeddings" | ❌ Descartado | O módulo `produtos` do Nexus já re-embeda por `content_hash`. O plugin só embeda a **query**. (D2) |
| "Reusar o client LLM do WhatsBot (Techify) p/ embeddings" | ❌ Descartado | `config["openrouter_api_key"]` guarda a chave do **proxy Techify** (nome legado), não do OpenRouter; e o Techify pode nem expor `/embeddings`. Os vetores foram feitos com `qwen/qwen3-embedding-8b`@768 **no OpenRouter** — a query PRECISA do mesmo modelo. ⇒ chave OpenRouter **própria** nas settings do plugin. |
| "Precisa re-implementar roteador/guardrails" | ❌ Descartado | `run_with_routing` + `transferir_agente` + `hooks_config`/`requires_prior_call` já são nativos e são um superset do Nexus. |
| "`{oferta_em_foco}` resolve como `ai_variables`" | ❌ Descartado | `render_template` (`agent/agent_factory.py:113`) só substitui `{}` de `ai_variables` **estáticos**; valor por-conversa vem por `PROMPT_FRAGMENT`. |
| "`enviar_imagens_oferta` precisa ser portado" | ❌ Descartado | `produtos_ofertas_imagens` tem **0 linhas**; fora de escopo (D6). |

---

## 3. Mudanças de infraestrutura

**Nenhuma no core.** Este é o ponto do plano. A única "infra" é interna ao plugin:
- 1 migration própria `migrations/001_initial.sql` criando `plugin_vendas_ia_conversa` (prefixo obrigatório).
- 1 **2ª engine SQLAlchemy** read-only para o Nexus (criada dentro do plugin, `sslmode=require`), separada do `get_engine()` do WhatsBot.
- 1 def de atributo de sistema `oferta_atual` (escopo `conversation`) semeada no boot via `custom_attribute_repo.ensure_system_definition` (não é schema novo; usa `custom_attribute_definitions` que já existe).

**Versões de Postgres (verificado 2026-07-07) — não impacta o plano.** WhatsBot = **PG 12.22** (driver `psycopg[binary]>=3.1`); Nexus = **PG 15.15 + pgvector 0.8.1**. A separação de responsabilidades cobre o gap: **todo recurso sensível a versão roda no Nexus (PG15)** — `pgvector`/`<=>`/`vector(768)`/HNSW, `tsvector` GENERATED, `plainto_tsquery('portuguese')` — e o WhatsBot só **envia SQL como texto e recebe linhas** (psycopg3 fala com PG15 sem problema; e como a projeção NÃO seleciona a coluna `embedding` crua, o psycopg do WhatsBot nunca precisa decodificar o tipo `vector`). No lado do WhatsBot (PG12) o plugin usa **só DDL trivial** — `plugin_vendas_ia_conversa` (`BIGINT`/`TEXT`/`JSONB`/`DOUBLE PRECISION` + `INSERT ... ON CONFLICT`, tudo ≥PG9.5) e o `conversations.custom_attributes` (JSONB+GIN) que já existe. **Nenhum recurso ≥PG13 é usado no lado do WhatsBot.** ⚠️ Ortogonal ao plano: PG12 está EOL (sem patches) — o usuário planeja subir para um PG bem mais atual em produção; isso é um ganho de ops geral e, de quebra, **habilita a variante autossuficiente do P5** (catálogo+embeddings dentro do WhatsBot, que aí sim exigiria pgvector no PG do WhatsBot). Para ESTE plano, nenhum upgrade é necessário.

---

## 4. Fases / Roadmap

### 4.1 Diagrama de dependências (waves)

```
WAVE 0 (fundação)
   F0 (scaffold + manifest + settings)          🔴 solo — bloqueia tudo
        │
   F1 (nexus_db.py: 2ª engine read-only + smoke) 🔴 solo — bloqueia F2/F5/F6
        │
        ├───────────────┬──────────────────────────────┐
WAVE 1  │ (busca)        │ (estado)                      │
   F2 (embeddings+search)🟢    F4 (migration + state + mirror)🟢   ← F2 e F4 em paralelo
        │                       │
   F3 (tools.py: 3 tools)🔴     │
        │  [depende de F2]      │
        └───────────────┬───────┘
WAVE 2  (automação)      │
   F5 (keyword→fixa oferta+força comercial)🟢  [depende de F1+F4]
   F6 (prompt fragment "OFERTA EM FOCO")🟢      [depende de F1+F4]   ← F5 e F6 em paralelo
        │
WAVE 3  (agentes + UI)
   F7 (seed/adapt 4 agentes)🔴  [depende de F3 — tool_names precisam existir]
   F8 (tela config dark-mode + testes e2e)🟢  [depende de tudo]
```

### 4.2 Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F0 | Scaffold | 🔴 | baixo | plugin aparece em `/plugins`, ativa sem `load_error`, settings abrem |
| 0 | F1 | Conexão Nexus | 🔴 | médio | `SELECT` read-only no `produtos_ofertas` retorna as 3 ofertas IA-ativas via engine do plugin |
| 1 | F2 | Busca | 🟢 | médio | função de busca híbrida retorna linhas p/ uma query; fallback ILIKE quando o embedding falha |
| 1 | F3 | Tools | 🔴 `[dep F2]` | baixo | 3 tools aparecem em `/tools`; chamadas manuais retornam JSON coerente |
| 1 | F4 | Estado | 🟢 | baixo | migration cria `plugin_vendas_ia_conversa`; gravar/ler estado + espelho em `custom_attributes` funciona |
| 2 | F5 | Palavra-chave | 🟢 `[dep F1+F4]` | médio | mandar "failover de links" fixa a oferta e o próximo turno já roda no agente comercial |
| 2 | F6 | Oferta em foco | 🟢 `[dep F1+F4]` | baixo | com oferta fixada, o system prompt do agente contém o bloco "OFERTA EM FOCO" com nome/código |
| 3 | F7 | Agentes | 🔴 `[dep F3]` | médio | 4 agentes seedados (não-destrutivo); comercial usa as 3 tools; guardrails ativos |
| 3 | F8 | UI + e2e | 🟢 | baixo | tela de config legível no dark; fluxo ponta-a-ponta valida; suíte verde |
| — | F9 | Guardas de msg | 🟢 (independente de F1-F8) | baixo | inbound regex não ativa a IA (msg fica no painel); outbound regex não chega ao cliente |

---

### Fase F0 — Scaffold do plugin + manifest + settings
**Objetivo:** criar o esqueleto `storages/plugins/vendas_ia/` que carrega limpo e expõe as settings.
**Itens:**
- `plugin.yaml`: `id: vendas_ia`, `entry: {tools: tools, events: events, filters: filters, prompts: prompts, settings: settings}`, `migrations: migrations`, 1 `screen` `config:true` (`/vendas_ia/config`), `permissions: [db.write]`, `rbac` opcional (`view`). Espelhar o formato de `assets/plugin_examples/protocolos/plugin.yaml`.
- `__init__.py` vazio; estrutura de pastas (`migrations/`, `static/`).
- `settings.py` — `class Settings(BaseModel)` com: `nexus_dsn`(secret), `openrouter_api_key`(secret), `embedding_model`(default `qwen/qwen3-embedding-8b`), `embedding_dims`(int, default 768), `hybrid_semantic_weight`(0.7), `hybrid_fulltext_weight`(0.3), `hybrid_limit`(5), `keyword_enabled`(bool, default True), `keyword_target_agent_key`(str, default `comercial`), `search_enabled`(bool, default True). `[sequencial]` — base de tudo.
**Pronto quando:** o card aparece em `/plugins`, ativa sem `load_error`, e o modal "Configurar" abre com os campos.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:** `storages/plugins/vendas_ia/` com `plugin.yaml` (entry tools/events/prompts/settings/routes + migrations + 1 screen + rbac view/config), `__init__.py`, `settings.py` (Pydantic: `nexus_dsn`/`openrouter_api_key` secret, modelo/dims, pesos, toggles) e `_config.py` (leitor `plugin.vendas_ia.*`).
- **Como foi feito / decisões:** Espelhado o `protocolos`. Secrets com `json_schema_extra={"format":"password"}`. Permissions `[db.write, llm.tool]`.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `load_manifest` parseia; `discover_and_load` carrega sem `load_error`; `Settings()` instancia com defaults (dims=768, sw=0.7).

---

### Fase F1 — `nexus_db.py`: 2ª engine read-only + smoke test
**Objetivo:** conexão isolada e read-only ao `RBNexusDB`, com helpers de query.
**Itens:**
- `nexus_db.py`: `get_nexus_engine()` — `create_engine(settings.nexus_dsn, pool_pre_ping=True, connect_args={"sslmode":"require","prepare_threshold":None})`, cacheado (singleton), **nunca** o `get_engine()` do WhatsBot. Helpers: `fetch_ofertas_ativas()`, `fetch_oferta_by_offercode()`, `run_hybrid(sql, params)` — todos `with engine.connect()` (sem `begin()`; read-only). Bind params **nomeados** (`:q`) — nunca `%s` (convenção do repo).
- Tratar falha de conexão de forma **defensiva**: sem DSN configurado ⇒ as tools/keyword viram no-op logado, não derrubam o boot.
- `[sequencial]` — bloqueia F2/F5/F6.
**Pronto quando:** um teste manual (`fetch_ofertas_ativas()`) retorna as 3 ofertas `is_active_for_ia=true` do Nexus.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:** `nexus_db.py` — `get_nexus_engine()` (singleton chaveado pelo DSN, `pool_pre_ping`, `connect_args={sslmode:require, prepare_threshold:None}`), `run_read`, `fetch_ofertas_ativas` (cache 300s), `fetch_oferta_by_offercode`, `resolve_offer_id` (offercode↔uuid-string), `ping`, `counts`. · **Decisões:** Defensivo — sem DSN ⇒ `[]`/no-op (nunca derruba o boot); DSN alterado reconstrói a engine sem restart. · **Pendências:** password do DSN precisa ser URL-encoded (documentado). · **Verificação:** `ping()`→OK e `fetch_ofertas_ativas()` retorna as 3 ofertas IA-ativas do Nexus (smoke real).

---

### Fase F2 — `embeddings.py` + `search.py`: busca híbrida + fallback
**Objetivo:** reproduzir a busca híbrida do Nexus sobre o banco do Nexus.
**Itens:**
- `embeddings.py`: `generate_query_embedding(text)` — `OpenAI(base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key).embeddings.create(model=settings.embedding_model, input=text, dimensions=settings.embedding_dims)`; `embedding_to_pg(vec)` → `"[" + ",".join(f"{x:.8f}") + "]"`. Falha ⇒ levanta/retorna `None` (o caller cai no ILIKE).
- `search.py`: `search_ofertas(q)`, `search_cursos(q, offercode=None, nomes=None)`, `search_faq(q, oferta_id=None)`. Cada uma:
  1. gera embedding → monta a SQL de 2 CTEs (`semantic` cosine `1-(embedding <=> :vec)` LIMIT 10 + `fulltext` `ts_rank(search_vector, plainto_tsquery('portuguese', :q))` LIMIT 10) → `FULL OUTER JOIN`, `score = sem*:sw + ft*:fw`, `DISTINCT ON`, `LIMIT :limit`. Pesos/limit das settings. **Não** projetar `prompt_dinamico_ia`.
  2. `except` → `_fallback_ilike` (ILIKE em `name`/`key_words`/`nome`/`question`).
  - ⚠️ **FAQ**: `id_ofertas` é `text[]` de **uuid** (não offercode) — filtro `WHERE :oferta_id = ANY(id_ofertas) OR '*' = ANY(id_ofertas)`; converter offercode→uuid antes (via `fetch_oferta_by_offercode`).
  - ⚠️ **cursos**: o `FULL OUTER JOIN` do Nexus junta por `nome` (não `id`) — manter.
- `[paralelo]` com F4.
**Pronto quando:** `search_ofertas("failover de links")` retorna a oferta certa; desligar/estragar a chave OpenRouter cai no ILIKE e ainda retorna algo.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:** `embeddings.py` (`generate_query_embedding` via OpenRouter `qwen/qwen3-embedding-8b`@768 + `embedding_to_pg`) e `search.py` (`search_ofertas`/`search_cursos`/`search_faq`, 2 CTEs semantic+fulltext, `FULL OUTER JOIN`, score ponderado, `DISTINCT ON`, fallback ILIKE). SQL do Nexus portado de `%s` → binds nomeados; `%s::vector` → `CAST(:vec AS vector)`. · **Decisões:** helper `_query_vector` (sem chave OpenRouter ⇒ fallback silencioso; erro de API ⇒ warning enxuto, sem traceback). FAQ resolve offercode→id; cursos com 3 modos (offercode/nomes ILIKE ANY/intenção). Não projeta `prompt_dinamico_ia`. · **Pendências:** cada busca híbrida = 1 chamada OpenRouter (aceitável, corpus pequeno). · **Verificação:** smoke real — `search_ofertas("failover de links")` retorna O06C57F42 com score 0.58 (topo); chave inválida cai no ILIKE e ainda retorna; cursos/faq híbridos com score.

---

### Fase F3 — `tools.py`: as 3 tools de busca
**Objetivo:** expor a busca como tools do LLM, com os nomes do Nexus.
**Itens:**
- `CORE_TOOLS = [(schema, execute), ...]` com `pesquisar_ofertas(course_name?, offer_name?, descricao_desejada?)`, `pesquisar_informacoes_cursos(offercode?, cursos?, intencao?)`, `pesquisar_perguntas_frequentes(id_oferta?, pergunta?)`. Cada `execute(ctx, args)` chama `search.py` e retorna `json.dumps(rows)`; strings PT-BR de "nada encontrado".
- Schemas com `display_label` PT-BR; descriptions escritas como instrução clara pro LLM (funcionam sem customização).
- `[sequencial]` `[depende de F2]`.
**Pronto quando:** aparecem em `/tools`; invocação manual retorna JSON coerente das 3.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-07) — **REVISADA** (2026-07-07, pedido do usuário)
- **1ª versão:** as 3 tools eram tools de PLUGIN (`tools.py` `CORE_TOOLS`) — editáveis/desligáveis na tela Tools, mas NÃO apagáveis e com toggle `search_enabled` na config do plugin.
- **Revisão (pedido do usuário):** as 3 tools viraram **`ai_tools` kind='code'** (config-in-DB) — **editáveis, com histórico e APAGÁVEIS na tela Tools NATIVA**, iguais às do Nexus/`transferir_agente`. O código de cada tool vive em `tool_code/<name>.py` (SCHEMA + `execute` que faz bootstrap: `sys.path`+`init_engine` via `DATABASE_URL` do env → delega à `vendas_ia.search`). `tools_seed.py` faz `tool_repo.save(kind="code")` (não-destrutivo) + liga o kill-switch `ai_tools_code_enabled`. **Removidos:** `entry.tools`, `tools.py`, o toggle `search_enabled`. · **Decisões:** tools que acessam banco/rede são feitas assim (o `tool_runner` recomenda plugin/core, mas o usuário quer apagáveis-no-banco); rodam num **subprocesso isolado** por chamada (~7s com embedding — recomendado `WHATSBOT_AI_TOOL_TIMEOUT=30` em produção). · **Pendências:** kill-switch é global (liga todas as code tools); precisa reiniciar para registrarem (botão na tela de diagnóstico). · **Verificação:** E2E via `agent.tool_runner` real — `describe` devolve os schemas; `execute` isolado retorna O2F2C6561 (ILIKE), O06C57F42 (híbrido) e FAQs; plugin carrega com `tools=0`.

---

### Fase F4 — Estado por conversa: migration + `state.py` + espelho
**Objetivo:** guardar o estado de vendas e espelhar a oferta atual no core.
**Itens:**
- `migrations/001_initial.sql`: `CREATE TABLE plugin_vendas_ia_conversa (conversation_id BIGINT PRIMARY KEY, offercode TEXT, offer_name TEXT, matched_keyword TEXT, offers_presented JSONB DEFAULT '[]', updated_at DOUBLE PRECISION)` — prefixo `plugin_vendas_ia_` obrigatório.
- `state.py`: `set_offer(conv_id, offercode, name, keyword)` (upsert na tabela do plugin) + `get_state(conv_id)`; **espelho** best-effort respeitando um toggle: no boot `ensure_system_definition(attribute_key="oferta_atual", display_name="Oferta atual", applies_to="conversation", is_system=True)`, e ao setar → `custom_attribute_repo.set_values(conversations_table, conv_id, {"oferta_atual": offercode})`. Copiar o padrão de `protocolos/logic.py:494-552`.
- `[paralelo]` com F2/F3.
**Pronto quando:** migration aplica no boot (aparece em `plugin_migrations`); gravar/ler estado funciona; o valor aparece em `conversations.custom_attributes`.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:** `migrations/001_initial.sql` (`plugin_vendas_ia_conversa`: conversation_id BIGINT PK, offercode, offer_name, offer_id, matched_keyword, offers_presented JSONB, updated_at) + `state.py` (`set_offer`/`get_state`/`mark_offers_presented` + espelho `oferta_atual` em `conversations.custom_attributes` respeitando `mirror_offer_attribute`; `ensure_attribute_defs` semeia `oferta_atual`+`perfil_cliente`). · **Decisões:** padrão de espelho copiado de `protocolos/logic.py`; guardei `offer_id` (uuid-string) além do offercode. · **Pendências:** ⚠️ o migrator faz split por `;` ANTES de tirar comentários — comentários da migration não podem conter `;` (removidos). · **Verificação:** migration aplica (aparece em `plugin_migrations`, `applied=[1]`); set/get + espelho conferidos no E2E.

---

### Fase F5 — Palavra-chave → fixa oferta + força comercial
**Objetivo:** replicar a triagem determinística do Nexus.
**Itens:**
- `events.py`: `EVENT_HANDLERS = {"message.saved": on_message_saved}`. Handler (respeita `keyword_enabled`): filtra por `is_group`/`source` se preciso → `ofertas = fetch_ofertas_ativas()` (cache) → match substring (`kw.strip().lower() in text.lower()`, `;`-split, primeiro vence) espelhando `triggers.py:43`. No match:
  1. resolve a conversa aberta do contato (`conversation_repo.get_open_for_contact`),
  2. `state.set_offer(conv_id, offercode, name, kw)` (+ espelho),
  3. grava `conversations.active_agent_key = settings.keyword_target_agent_key` (via `conversation_repo` ou update direto — **verificar** o helper nativo; ver P2).
- ⚠️ Guardas do Nexus a portar: só casar quando a IA está no comando (não sobrescrever atendimento humano — checar `_conversation_ai_active`/tag `transferido_atendente`); não re-forçar se a conversa já está em outro agente definido pelo humano.
- `[paralelo]` com F6. `[depende de F1 (ofertas) + F4 (estado)]`.
**Pronto quando:** enviar "failover de links" (fora de horário de atendente) fixa `O06C57F42` e o **próximo turno** roda no agente comercial (verificável no card de `conversation_event`/nos steps de execução).

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:** `events.py` — `EVENT_HANDLERS={"message.saved": on_message_saved, "app.startup": on_startup}`. Match substring (lower, `;`-split, primeiro vence) espelhando `triggers.py:43` → resolve contato+conversa aberta → `state.set_offer` (+espelho) → força o agente comercial via `conversation_repo.set_agent`. · **Decisões (P2):** `set_agent` é o helper nativo (não há `set_active_agent_key`). Guarda `_ai_in_command` (replica `_conversation_ai_active`: pula grupos, IA pausada, humano atribuído, tag `transferido_atendente`). Só força se o agente-alvo existe (senão fixa a oferta e loga). · **Pendências:** não emite `conversation_event` (a troca de agente é observável via `active_agent_key`/steps) — opcional. · **Verificação:** E2E — "failover de links" fixa O06C57F42 + força `comercial` no MESMO turno; com humano atribuído NÃO rouba a conversa.

---

### Fase F6 — `prompts.py`: bloco "OFERTA EM FOCO"
**Objetivo:** injetar a oferta fixada no system prompt (equivalente ao `{oferta_em_foco}`).
**Itens:**
- `prompts.py`: `PROMPT_FRAGMENTS = [oferta_em_foco_fragment]`. O callable recebe `(contact_memory, prompt_context)`: pega a conversa aberta → `state.get_state(conv_id)` → se há `offercode`, `fetch_oferta_by_offercode()` no Nexus → retorna um bloco read-only ("O cliente está interessado na oferta **{name}** (código {offercode}). Conduza focado nela; não precisa chamar `pesquisar_ofertas` de novo." + preço/links relevantes, **sem** `prompt_dinamico_ia` cru se for instrução interna). Sem oferta ⇒ retorna `""` (injeta nada).
- Opcional (setting): fragmentos `{cursos_disponiveis}`/`{ofertas}` (catálogo resumido) — só se os prompts migrados dependerem; senão deixar as tools fazerem o trabalho (mantém o contexto enxuto).
- `[paralelo]` com F5. `[depende de F1+F4]`.
**Pronto quando:** com uma oferta fixada, inspecionar o system prompt do turno mostra o bloco com nome+código; sem oferta, o bloco some.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:** `prompts.py` — `PROMPT_FRAGMENTS=[oferta_em_foco_fragment]` `(contact, ctx)→str`: resolve offercode (tabela do plugin → fallback no atributo `oferta_atual`) → `fetch_oferta_by_offercode` → bloco "## OFERTA EM FOCO" com nome/offercode/valor/bônus/acesso/links. Sem oferta ⇒ `""`. · **Decisões:** fallback no custom_attribute permite que o próprio comercial fixe a oferta via `set_custom_attribute` e o fragment enxergue. `prompt_dinamico_ia` nunca é projetado, então não vaza. · **Pendências:** não injeta `{cursos_disponiveis}`/`{ofertas}` (as tools fazem o trabalho — contexto enxuto). · **Verificação:** E2E — com oferta fixada, o fragment contém "OFERTA EM FOCO" + código + nome; sem oferta, retorna `""`.

---

### Fase F7 — Seed + adaptação dos 4 agentes
**Objetivo:** trazer ROUTER/COMERCIAL/SUPORTE/FECHAMENTO do Nexus para `ai_agents`, adaptados.
**Itens:**
- Extrair os 4 prompts do Nexus: `SELECT tipo_atendimento, prompt FROM gerenciamento_ia_prompts WHERE chave_do_prompt='GERAL'` (COMERCIAL≈29KB, ROUTER≈8KB, SUPORTE≈6KB, FECHAMENTO≈4KB).
- **Adaptar** cada prompt (remapear tools e conceitos):

  | Tool/conceito Nexus | Vira no WhatsBot |
  |---|---|
  | `pesquisar_ofertas` / `pesquisar_informacoes_cursos` / `pesquisar_perguntas_frequentes` | **mesmos nomes** (tools do plugin) |
  | `solicitar_roteamento` / `transferir_para_outro_agente` | `transferir_agente` (nativo) |
  | `transferir_para_humano` | `transfer_to_human` (nativo) |
  | `atualizar_atributos_chatwoot` | `set_custom_attribute` (nativo) |
  | `{oferta_em_foco}` / `{cursos_disponiveis}` / `{ofertas_xml}` | injeção por `PROMPT_FRAGMENT` (F6) — remover o placeholder do texto |
  | `enviar_imagens_oferta`, `gerar_deep_link`, fechamento/protocolo | **fora de escopo** — remover das instruções |

- Seed **não-destrutivo** via `agent_repo.save(...)` (só cria se `agent_key` inexistente — checar `get()` antes). `agent_key` do comercial = `settings.keyword_target_agent_key` (default `comercial`). `hooks_config` do comercial: `{"pesquisar_informacoes_cursos": {"requires_prior_call": "pesquisar_ofertas"}, "pesquisar_perguntas_frequentes": {"requires_prior_call": "pesquisar_ofertas"}}`.
- ⚠️ **Roteador único** (P1): seedar `is_router=True` pode colidir com o índice `ux_ai_agents_single_router` se já existir um roteador. `agent_repo.save` demota outros (`:186-231`), mas **isso altera o setup do usuário** → decidir em P1 (seedar router só sob confirmação, ou seedar só os spokes).
- `tool_names` por agente (WhatsBot):

  | agent_key | is_router | tool_names |
  |---|---|---|
  | `roteador` | (P1) | `transferir_agente`, `transfer_to_human` |
  | `comercial` | não | `pesquisar_ofertas`, `pesquisar_informacoes_cursos`, `pesquisar_perguntas_frequentes`, `transferir_agente`, `set_custom_attribute` |
  | `suporte` | não | `pesquisar_informacoes_cursos`, `pesquisar_perguntas_frequentes`, `transferir_agente`, `set_custom_attribute` |
  | `fechamento` | não | `transferir_agente` (tools de fechamento fora de escopo — agente vira mínimo ou é omitido — ver P3) |

- Onde rodar o seed: função idempotente chamada no bootstrap do plugin (ou botão "Semear agentes" na tela de config, pra ser explícito e não-surpresa). `[depende de F3]`.
**Pronto quando:** os agentes existem em `ai_agents`, o comercial lista as 3 tools, e um turno forçado a comercial de fato as usa com os guardrails ativos.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída (2026-07-07)
- **O que foi feito:** Prompts adaptados versionados em `seed_prompts/{roteador,comercial,suporte,fechamento}.md` (remap `solicitar_roteamento`/`transferir_para_outro_agente`→`transferir_agente`; `transferir_para_humano`→`transfer_to_human`; `atualizar_atributos_chatwoot`→`set_custom_attribute`; seção de imagens + `consultar_plataforma_de_vendas` removidas; preâmbulo autoritativo WhatsBot). `agents_seed.py` semeia via `agent_repo.save` (não-destrutivo: só cria se `get()` é None), comercial com 5 tools + `hooks_config` (`requires_prior_call: pesquisar_ofertas`). · **Decisões (P1):** roteador só é semeado automaticamente se NÃO houver roteador; `force_router=True` (ação explícita) rebaixa o atual, com aviso no relatório. **(P3):** fechamento é MÍNIMO (só `transferir_agente` + despedida). · **Pendências:** o prompt `suporte` do Nexus é triagem-clone (adaptado, não reescrito). · **Verificação:** E2E — 4 agentes criados (idempotente na 2ª chamada); comercial tem as 5 tools + guardrail + prompt inline >5KB; `get_router()=="roteador"`.

---

### Fase F8 — Tela de config (dark-mode) + validação e2e
**Objetivo:** UI de configuração legível e um passe ponta-a-ponta.
**Itens:**
- `static/config.js`: screen `config:true` (renderizada no modal "Configurar"). Pode ser só o `PluginSettingsForm` declarativo (se as settings bastarem) **ou** uma tela custom com: teste de conexão Nexus, botão "Semear agentes", contadores (nº de ofertas/cursos/faq IA-ativos). Usar classes `wa-*`/`.wa-field` — **testar no modo escuro**.
- Validação e2e (sandbox ou Evolution): (a) "quero saber de failover de links" → oferta fixada + comercial responde focado; (b) pergunta genérica de preço → comercial chama `pesquisar_ofertas`; (c) dúvida de curso → `pesquisar_informacoes_cursos` bloqueado até haver oferta (guardrail); (d) FAQ.
- `[depende de tudo]`.
**Pronto quando:** tela legível no dark; os 4 cenários passam; suíte do repo verde no Postgres de teste.

#### Status de execução — Fase F8
**Estado:** ✅ Concluída (2026-07-07) — atualizada na revisão F3
- **O que foi feito:** `routes.py` (`GET /status`, `POST /seed` [agentes + tools + kill-switch], `POST /test-connection`, gated por `plugin_permission`) + `static/config.js` (página de diagnóstico dark-mode: conexão/chave, contadores, estado do **kill-switch de tools de código** + chips das 3 tools `ai_tools` (com `install_status`), chips dos 4 agentes, botão "Semear agentes + tools" com checkboxes "ligar tools de código"/"substituir roteador" e botão "Reiniciar para aplicar" → `POST /api/plugins/restart`). · **Decisões (P4):** settings via form declarativo (modal "Configurar"); a screen é `config:false` (página do menu da engrenagem, `requires:view`) para diagnóstico+seed — não reimplementa o form. Cores 100% `wa-*`. · **Pendências:** validação e2e nos 4 cenários rodada via lógica (E2E), não pelo chat GOWA ao vivo (o guardrail `requires_prior_call` já é nativo/testado no plano 29). · **Verificação:** suíte do repo verde (`test_endpoints.py` 1086 passed / 0 failed); E2E ponta-a-ponta passa; `discover_and_load` sem `load_error`.

---

### Fase F9 — Guardas de mensagem (inbound ignore + outbound block)
**Objetivo:** portar as duas guardas do Nexus. **Independente das F0–F8** (pode ser feita a qualquer momento; ver P6 sobre colocação — recomendado: plugin `guarda_ia` **bundled** via `BUNDLED_AUTO_INSTALL`, default-on em todo install).
**Itens:**
- **Inbound — "não ativar a IA" por regex** (espelha `rules/filters.py:check_ignored_messages_user`): `FILTERS = {"filter.llm.messages": guard_inbound}`. O handler pega o último texto de usuário na lista → normaliza (**NFKD** strip de acento + `lower`) → testa contra a lista `ignore_inbound_patterns` (setting): **match exato** OU **regex** (heurística do Nexus: padrão que começa com `^`, termina com `$`, ou contém `\` é tratado como regex; senão substring/exato). Se casar → **retorna `None`** ⇒ o LLM **não é chamado**, mas a mensagem **já está salva** no painel (ingest). ⚠️ NÃO usar `filter.message.before_save`→`None` (descartaria a msg do painel).
- **Outbound — "não mandar erro/alucinação" por regex** (espelha `response_dispatcher.py:is_ignored_ia_message`): `FILTERS = {"filter.reply.raw": guard_outbound}`. Testa a resposta contra `block_outbound_patterns` (setting — ex.: `request timed out`, `error`, strings de erro do OpenRouter, padrões de alucinação). Se casar → **retorna `None`** (nada é enviado). **Logar** o bloqueio e (opcional) gravar um card painel-only (`error`/`system_notice`) pro operador ver que a IA produziu algo bloqueado. Alternativa mais granular: `filter.reply.part` (pula só a parte ruim).
- **Settings** (Pydantic, no plugin escolhido): `ignore_inbound_patterns` (lista/CSV), `block_outbound_patterns` (lista/CSV), toggles `ignore_inbound_enabled`/`block_outbound_enabled`. Semear defaults sensatos (saudações/stickers no inbound; erros comuns de provider no outbound).
- `[paralelo total]` — não depende de nenhuma outra fase.
**Pronto quando:** uma msg que casa o inbound-regex fica no painel sem resposta da IA; uma resposta da IA que casa o outbound-regex não chega ao cliente (e é logada).

#### Status de execução — Fase F9
**Estado:** ✅ Concluída (2026-07-07) — plugin IRMÃO `guarda_ia`
- **O que foi feito:** Plugin separado `storages/plugins/guarda_ia/` (`matcher.py` porta fiel de `rules/filters.py` do Nexus: NFKD sem acento + lower, heurística regex `^`/`$`/`\` senão exato; `filters.py`: `filter.llm.messages`→None (inbound, extrai último texto de usuário incl. content-array de visão) e `filter.reply.raw`→None (outbound); `settings.py` com padrões CSV/JSON + toggles). · **Decisões (P6):** implementado como plugin IRMÃO standalone (NÃO dentro de `vendas_ia`, NÃO adicionado a `BUNDLED_AUTO_INSTALL` para manter zero-core — o bundling é 1 linha na tupla, decisão do usuário). Defaults: inbound = ruído (emoji/ok/kk, match exato); outbound = erros de provedor (regex substring, ex.: `(?is)^.*timed out.*$`). · **Pendências:** não grava card painel-only no bloqueio outbound (só loga WARNING) — opcional. · **Verificação:** matcher + filtros testados (inbound "ok"→None, texto→passa; outbound "Request timed out"→None, resposta→passa); `discover_and_load` carrega os 2 filtros sem erro.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Ordem filtro-vs-build (forçar comercial no mesmo turno) | Agente forçado só valeria no turno seguinte | **Resolvido** (§2.3): usar `message.saved` (ingest, antes do batch AI). Validar na F5 que o 1º turno já roda comercial. |
| Embedding da query (modelo/dims) | Cosine sem sentido se modelo/dims ≠ dos vetores | Travar `embedding_model=qwen/qwen3-embedding-8b` + `dims=768` nas settings; chave **OpenRouter própria** (não a do Techify). Fallback ILIKE sempre presente. |
| 2ª engine ao Nexus | Boot travar / vazar credencial / SSL | Engine lazy + `pool_pre_ping`; `sslmode=require`; DSN só em setting `secret`; falha ⇒ no-op logado, nunca derruba o WhatsBot. |
| FAQ `id_ofertas` = uuid, não offercode | Join errado (nunca casa) | Converter offercode→uuid antes; `= ANY(id_ofertas) OR '*' = ANY(...)`. |
| Roteador único (`ux_ai_agents_single_router`) | Seed de router colide/rebaixa o router do usuário | P1: não seedar router automaticamente; oferecer via botão com aviso, ou só spokes. Seed dos demais é `upsert_ignore` (não-destrutivo). |
| Sobrescrever atendimento humano | Palavra-chave "roubar" conversa já com humano | Handler só age se `_conversation_ai_active` e sem tag `transferido_atendente`. |
| `prompt_dinamico_ia` vazar pro cliente | Instrução interna virar texto de venda | Nunca projetar essa coluna nas tools; no fragment, usar só se for conteúdo de cliente. |
| Custo/latência do embedding por busca | Cada busca = 1 chamada OpenRouter | Aceitável (corpus minúsculo); logar; opcional cache de query. |
| Modo escuro na tela de config | Ilegível | Classes `wa-*`/`.wa-field`; testar com dark ligado. |
| Restart de plugin | Enable/disable derruba handlers/tools | Comportamento nativo (supervisor relança); nada especial. |
| DSN/segredo em log/URL | Vazamento | Nunca logar DSN/chave; `secret` no schema; sem query string com senha. |
| Versão de Postgres (WhatsBot PG12 × Nexus PG15) | Recurso vetorial/tsvector não existir no PG12 | **Não impacta** (ver §3): pgvector/HNSW/tsvector rodam no Nexus (PG15); WhatsBot só faz DDL trivial + recebe linhas. Não projetar a coluna `embedding` (psycopg não precisa decodificar `vector`). PG12 é EOL — upgrade em produção é recomendado (ortogonal a este plano; futuro P5). |

---

## 6. Perguntas em aberto

- **P1 — Seedar o roteador automaticamente?** ⏸️ ADIADO (decidir na F7). Contexto: WhatsBot força 1 roteador (índice único). (a) seedar `roteador` via `agent_repo.save` (rebaixa o router atual do usuário — intrusivo); (b) seedar **só** comercial/suporte e deixar o usuário apontar o roteador dele pros novos alvos; (c) botão "Semear roteador" com aviso explícito. **Recomendação:** (c) — não surpreender o setup do usuário.
- **P2 — Como gravar `active_agent_key`?** ⏸️ A confirmar na F5. Existe helper em `conversation_repo` (ex.: `set_active_agent_key`)? Se não, `update` direto na tabela `conversations`. **Recomendação:** usar/estender o repo nativo (sem tocar em lógica de core), só a gravação.
- **P3 — Manter o agente FECHAMENTO?** ⏸️ ADIADO. As tools de fechamento (protocolo) estão fora de escopo. (a) omitir o agente; (b) seedar um "fechamento" mínimo só com `transferir_agente`/despedida. **Recomendação:** (b), leve, pra preservar o desenho de 4 agentes do Nexus.
- **P4 — Tela de config: declarativa ou custom?** ⏸️ Decidir na F8. Settings declarativas bastam pro MVP; a tela custom (teste de conexão + semear + contadores) é um plus. **Recomendação:** declarativa primeiro, custom se sobrar tempo.
- **P5 — Distribuição p/ clientes sem Nexus?** ✅ DECIDIDO (2026-07-07): fora de escopo agora. O plugin lê o Nexus; quem não tem, não instala. Uma variante autossuficiente (tabelas próprias + CRUD + sync) é trabalho futuro, não este plano.
- **P6 — Guardas de mensagem (F9): core, plugin, ou plugin _bundled_?** ⏸️ ADIADO (aguarda decisão final do usuário). Contexto: as guardas são **genéricas** (sem regra de negócio) e a de saída é uma **rede de segurança** que todo install deveria ter ligada. Três opções: (a) **plugin `guarda_ia` bundled** — auto-instalado/ligado por padrão via `BUNDLED_AUTO_INSTALL` em [plugins/bootstrap.py](../plugins/bootstrap.py) (hoje `("gowa",)`); core segue mínimo, todo install distribuído já nasce protegido, isolado/desligável; único toque no core = **1 item na tupla**, não lógica; (b) **plugin irmão opt-in** — igual (a) mas sem bundling (usuário instala o `.zip`); risco de "esquecer de instalar" e vazar erro; (c) **no core da IA** — default-on garantido, mas exige seção no painel de config core + persistência das regex + testes de core, contra o objetivo "core mínimo/distribuível" e redundante com os seams (`filter.llm.messages`/`filter.reply.raw`) que já resolvem sem core. **Recomendação:** (a) plugin `guarda_ia` **bundled** — concilia "core mínimo" com "ligado por padrão em todo install". NÃO dentro de `vendas_ia` (a guarda é útil sem o Nexus).

---

## 7. Apêndice — arquivos-chave

**Plugin novo (tudo em `storages/plugins/vendas_ia/`):**
- `plugin.yaml` · `__init__.py` · `settings.py` · `nexus_db.py` · `embeddings.py` · `search.py` · `tools.py` · `events.py` · `filters.py`(se preciso) · `prompts.py` · `state.py` · `migrations/001_initial.sql` · `static/config.js`

**Core do WhatsBot que o plugin CONSOME (não altera) — referência:**
- `agent/agent_factory.py:204` (`resolve_active_agent_key`), `:290` (`build_for_contact`), `:113` (`render_template`)
- `agent/memory.py:511/528` (injeção de atributos no prompt)
- `agent/handler.py:124/271` (registro de fragments / build do system prompt)
- `db/tables.py:426` (`active_agent_key`), `:436` (`conversations.custom_attributes`), `:579-657` (`ai_agents`)
- `db/repositories/agent_repo.py:120` (`save`), `:186-231` (single-router demote)
- `db/repositories/custom_attribute_repo.py:91/177/187` (`ensure_system_definition`/`get_values`/`set_values`)
- `app/services/message_ingest_service.py:446/482/491` (filtro/eventos de ingest)
- `app/services/messaging_service.py:815/839/1001/1139` (batch AI + `_conversation_ai_active`)
- `ai_engine/hooks.py` (guardrails), `ai_engine/routing.py:39` (`run_with_routing`)
- Referência de padrão: `assets/plugin_examples/protocolos/{plugin.yaml,settings.py,logic.py:494-552}`

**Nexus (fonte a portar) — referência:**
- `ai/src/rules/triggers.py:43`, `ai/src/webhooks/chatwoot_handler.py:85-181`, `ai/src/services/prompt_context.py:56`
- `ai/src/tools/pesquisar_ofertas.py:68-126`, `ai/src/services/embeddings.py:42-83/144`, `embedding_sync.py:80-103`, `services/database.py:123`
- Banco `RBNexusDB@10.8.100.5`: `produtos_{ofertas,produtos,faq}`, `gerenciamento_ia_embeddings_{oferta,curso,faq}`, `gerenciamento_ia_prompts`

---

## 8. Checklist de verificação (por mudança)

- [ ] Plugin ativa sem `load_error`; desativa e o WhatsBot segue de pé.
- [ ] `nexus_db` conecta read-only (`sslmode=require`), sem segredo em log/URL.
- [ ] Busca híbrida retorna resultados; **fallback ILIKE** funciona com a chave OpenRouter inválida.
- [ ] As 3 tools aparecem em `/tools` e retornam JSON coerente.
- [ ] Migration `plugin_vendas_ia_conversa` aplica e reverte limpo (round-trip); prefixo `plugin_vendas_ia_` aceito pelo migrator.
- [ ] Palavra-chave fixa a oferta e força o comercial **no mesmo turno**; não rouba conversa de humano.
- [ ] Bloco "OFERTA EM FOCO" aparece no system prompt com oferta fixada; some sem oferta; `prompt_dinamico_ia` nunca vaza.
- [ ] Seed dos agentes é **não-destrutivo** (não sobrescreve agentes/roteador existentes sem confirmação).
- [ ] Guardrail `requires_prior_call` bloqueia cursos/faq antes de `pesquisar_ofertas`.
- [ ] Tela de config legível no **modo escuro** (`wa-*`/`.wa-field`).
- [ ] `tests/test_endpoints.py` e a suíte **verde no Postgres** (`WHATSBOT_TEST_DB_URL`).
- [ ] Restart de plugin (enable/disable) recarrega tools/eventos sem quebrar.
- [ ] **F9 inbound:** msg que casa o regex de ignore fica **salva no painel** mas a IA **não responde** (via `filter.llm.messages`→`None`, não `before_save`).
- [ ] **F9 outbound:** resposta da IA que casa o regex de bloqueio **não chega ao cliente** e é **logada** (via `filter.reply.raw`/`part`→`None`).
