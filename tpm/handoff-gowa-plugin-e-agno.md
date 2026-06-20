# Handoff — WhatsBot: motor Agno no banco (versão base) + GOWA como plugin (versão Pro)

> **Para quem vai implementar.** Este documento separa o trabalho em duas versões:
> **(1) versão base/free — a prioridade — só o motor Agno com agentes/tools/prompts no banco, no CORE;**
> **(2) versão Pro — GOWA como provider-plugin de canal e o resto.** Define o que já está decidido, dá um
> passo-a-passo para implementar **sem quebrar o sistema atual**, e lista as decisões ainda em aberto.
>
> **Comece pela Seção 2 (motor Agno) — é o que entra na base.** A Seção 1 (GOWA-plugin) é Pro.
>
> Base de pesquisa: pasta `docs-pesquisa/`. Para a **base**, o doc relevante é
> `06-motor-multiagente-agno.md` (ignorando suas partes de inbox/RBAC/auditoria, que são Pro). Para a
> **Pro**, ver também `00-visao-geral.md` e `02-canais-e-providers.md`. Este doc é o **plano de ação**;
> os docs de pesquisa são o **porquê** detalhado, com referências.

---

## 0. Contexto: duas versões e prioridade

Vão existir **duas versões** do produto:

- **Versão base / FREE** — o WhatsBot que evolui a partir do atual. **A ÚNICA coisa nova nesta versão é
  o motor Agno: agentes, tools e prompts configuráveis no BANCO** em vez de hardcoded. **GOWA e o motor
  de IA ficam no CORE** (não viram plugin).
- **Versão Pro** — sobre a base, ganha **tudo o mais**: GOWA como provider-plugin de canal, inbox/conversa,
  multi-canal, RBAC/usuários, auditoria, atributos custom, filtros avançados, e o motor de IA
  eventualmente empacotado/extraível como plugin.

> **ESCOPO DA VERSÃO BASE (cliente, 2026-06-18):** **a única feature nova da base é a Mudança B — Agno
> com agentes/tools/prompts no banco (config-in-DB / code-in-DB), no CORE.** Tudo que aparece nos outros
> docs de pesquisa — **caixa de entrada/conversa (doc 01), RBAC/usuários (doc 03), auditoria (doc 07),
> canais/GOWA-plugin (doc 02)** — é **versão Pro** e **NÃO entra neste plano da base**.
>
> Consequência técnica direta: na base **não existem** as entidades `Inbox`, `Conversation` nem `User`.
> Portanto o motor Agno da base é ancorado no **contato** (modelo atual, `ContactMemory`/`messages`),
> **não** em `conversation_id`/`inbox`. Onde o doc 06 fala de "conversa = sessão", na base isso vira
> "**contato = sessão**". As colunas `inboxes.default_agent_key`/`conversations.active_agent_key` do
> doc 06 são **Pro**.

> **A base é SINGLE-AGENT.** Multi-agente, roteamento e handoff entre IAs **NÃO entram na base** — ela
> roda **um agente** (configurável no banco). O motivo de trocar para o Agno mesmo sendo single-agent é
> **deixar a fundação pronta**: o Agno já suporta multi-agente/`Team`/handoff nativamente, então quem
> quiser adicionar isso depois implementa por conta própria sobre uma base que **já dá suporte** —
> sem reescrever o motor. A arquitetura não fecha a porta, mas o produto público não expõe multi-agente.

Por isso a ordem de leitura recomendada é **Seção 2 (Mudança B) primeiro**, depois Seção 1.

**Implicação para quem implementa:** introduzir as abstrações novas mantendo o caminho antigo
funcionando atrás de **flag de configuração** durante a transição, aposentando o código velho só quando
o novo estiver validado. Nada de "big bang".

As duas mudanças desta entrega:

- **Mudança B (PRIORITÁRIA — versão base/free, no CORE):** **tools, prompts e a configuração do agente no
  banco** (config-in-DB; o código das tools também no banco = code-in-DB), motor inspirado no
  `/opt/gerenciamento-ia`, usando **Agno** como runtime — **single-agent** na base. Substitui o
  `AgentHandler` singleton (prompt/modelo/tools globais e hardcoded) por um motor dirigido pelo banco.
  **Isto é o coração do pedido.** (Multi-agente fica de fora — ver caixa acima.)
- **Mudança A (versão Pro):** **GOWA deixa de ser core cravado e vira provider-PLUGIN** de canal (e o
  motor de IA pode ser extraído como plugin). Quem só usa Cloud API / Telegram / e-mail não roda o GOWA.

> **Nota de dependências:** o `requirements.txt` já lista `agno` e `openai`. Validar versões num venv
> limpo antes de avançar (conflito potencial `pydantic`/`sqlalchemy`/`openai` — ver doc 06 §7.4).

---

## 1. Mudança A — GOWA como provider-plugin  *(versão PRO — pode vir depois)*

> Esta seção é a preocupação da **versão Pro**. Na versão base/free o GOWA **continua no core** como
> está hoje. Leia a Seção 2 primeiro — é a prioridade.

### 1.1 Estado atual (o acoplamento a quebrar)

O WhatsBot hoje é "1 app = 1 número WhatsApp via GOWA", cravado no core:

| Acoplamento | Arquivo:linha | O que está cravado |
|---|---|---|
| Subprocess GOWA | `gowa/manager.py:36-210` | `GOWAManager` (porta 3000 default, watchdog, `start/stop/restart/_watchdog`) |
| Device fixo | `gowa/client.py:12,52` | `_DEFAULT_DEVICE_NAME = "whatsbot"`; `X-Device-Id` fixo em todo request |
| `ensure_device()` pega `devices[0]` | `gowa/client.py:112-141` | não há como endereçar device B |
| Instanciação global | `main.py:57-58` | `GOWAManager(...)` e `GOWAClient(...)` criados antes do app |
| Injeção | `server/app.py:65-125` | guardados em `ServerDeps` (`gowa_manager`, `gowa_client`) |
| Lifespan | `server/app.py:142-183` | 4 tasks **hardcoded** (`start_gowa_task`, `status_poll_loop`, `qr_poll_loop`, `avatar_fetch_task`); shutdown faz `task.cancel()` + `gowa_manager.stop()` |
| Start do GOWA | `server/background.py:19-44` | `start_gowa_task` chama `gowa_manager.start()` + `ensure_device()` |
| Webhook | `server/routes/webhook.py` | endpoint único `/api/webhook`, **não lê `device_id`** do payload |

### 1.2 O que o sistema de plugins NÃO tem hoje (e precisa ganhar)

O loader (`plugins/loader.py`) só faz `importlib` do módulo e registra **tools, prompts, events,
filters, routes, screens, migrations**. Para o GOWA virar plugin com segurança, faltam **3 capacidades
de runtime — todas CORE** (um plugin não pode fornecê-las; ele depende delas para rodar):

- **(i) Lifecycle de plugin real** — `setup(ctx)` / `teardown(ctx)` **aguardados** pelo host. Hoje:
  - não há gancho de init no loader (só import);
  - `app.startup`/`app.shutdown` são fire-and-forget (`emit_event` usa `run_coroutine_threadsafe` sem
    `Future.result()` — o shutdown **não espera** os handlers de plugin);
  - o toggle de plugin (`plugins/restart.py:42-80`) usa `os._exit(0)` após ~1,5s — **pula finalizers**,
    então um subprocess aberto pelo plugin viraria órfão.
  - **Ação:** ganchos `setup/teardown` chamados e aguardados; rodar `teardown` **antes** do `os._exit`;
    registro estilo "Disposable"/`async_on_unload` no `ctx` para o plugin declarar cleanups.
- **(ii) Supervisor de tasks de fundo** — registry onde core e plugins registram corrotinas de longa
  duração (loop com `stop_event`), com restart classificado + backoff/rate-limit. Hoje as 4 tasks são
  uma lista local no lifespan. **Habilita Telegram long-poll e e-mail IMAP como plugin.**
- **(iii) Serviço de subprocesso gerenciado** — `Popen` em **process group** + **die-with-parent**
  (`PR_SET_PDEATHSIG` no Linux / Job Object `KILL_ON_JOB_CLOSE` no Windows) + parada graciosa
  (SIGTERM→timeout→SIGKILL) + **PID file / stale-kill no boot** (essencial p/ não duplicar sessão
  WhatsApp) + watchdog com rate-limit + readiness probe. **É o que falta para o GOWA virar plugin.**

### 1.3 Contrato de canal (core)

Criar no **core** (ver doc 02 §3.2 para a interface ilustrativa completa):

- Interface `Channel` (ABC) com: `start/stop/status`, `send_text/send_media`, `parse_inbound(raw) ->
  list[evento_normalizado]`, e opcionais `get_qr/mark_read/send_presence/react/revoke/send_template`.
- `ChannelRegistry` (substitui o `gowa_client` global): duas camadas — `provider name → Provider class`
  e `channel_id → Channel instance`.
- **Evento normalizado de entrada** (doc 02 §3.1): dict com `channel_id, provider, kind, external_msg_id,
  chat_id, sender_id, text, media_*`, `raw`, etc. É o mesmo formato que `filter.message.before_save` já
  manipula.
- Tabelas **core** (Alembic, sem prefixo de plugin): `channels` (doc 02 §4) e `channel_credentials`
  (cifrada — tokens por canal). Plugin **não** cria tabela de canal; lê/grava via API que o core expõe.

### 1.4 Ponto de extensão de canal para plugins

- **Manifest**: novo campo `entry.channels: channels` (módulo `storages/plugins/<id>/channels.py`).
- **Export**: o módulo exporta `CHANNEL_PROVIDERS = [GOWAChannel, ...]` (espelha `CORE_TOOLS`).
- **Loader**: ao carregar plugin com `entry.channels`, importar e chamar
  `channel_registry.register_provider(cls)` (análogo a `register_plugin_tools`). Disable desregistra.

### 1.5 Webhook roteado por canal

- Core expõe rota genérica **`/api/webhook/{provider}/{channel_id}`** (path explícito = canal — mais à
  prova de erro que adivinhar por campo).
- Core resolve `channel_id → Channel` no registry e chama `channel.parse_inbound(raw)` → eventos → pipeline.
- O plugin **não** abre rota própria de webhook (mas ainda pode ter rotas de UI/config sob
  `/api/plugins/<id>/...`).
- GOWA v8 manda `device_id` no topo do payload → usar como confirmação/fallback. GOWA é multi-device no
  v8 (`POST /devices`, `X-Device-Id`): **1 processo, N devices** é o modo recomendado (doc 02 §4, Opção A).

### 1.6 Passo-a-passo sugerido (ordem importa — não quebra o atual)

> **Regra de ouro:** construir as capacidades genéricas **validando-as num provider barato** antes de
> mexer no GOWA. O GOWA (subprocesso) é o caso mais difícil — deixe por último.

1. **(i) Lifecycle de plugin aguardado** — adicionar `setup/teardown` ao loader; tornar
   `app.startup/shutdown` aguardados; rodar `teardown` antes do `os._exit` no toggle; expor registro de
   cleanup no `ctx`. *Não toca no GOWA ainda.*
2. **(ii) Supervisor de tasks de fundo** — generalizar as 4 tasks hardcoded do lifespan num registry com
   `stop_event` + restart/backoff. Expor aos plugins via `ctx`.
3. **Contrato `Channel` + `ChannelRegistry` + tabelas `channels`/`channel_credentials`** no core
   (Alembic). Manter `gowa_client`/`gowa_manager` antigos funcionando em paralelo (flag) — nada removido.
4. **Validar (i)+(ii) com um provider simples** — um "provider de teste" (loop trivial) ou Cloud API
   **webhook-only** (sem subprocesso). Provar que registra, recebe webhook roteado, envia, e desliga limpo.
5. **(iii) Serviço de subprocesso gerenciado** no core (process group, die-with-parent, stale-kill, etc.).
   Fazer o **próprio core** passar a usá-lo para o GOWA primeiro (endurecimento), ainda como core.
6. **GOWA-plugin** — empacotar `gowa/manager.py` + `gowa/client.py` atrás de `GOWAChannel(Channel)` num
   plugin que consome a capacidade (iii) via `ctx`. Parametrizar `device_id` por canal (remover o fixo
   `"whatsbot"` e o `devices[0]`). Migrar o webhook para a rota por canal.
7. **Aposentar o caminho antigo** (instanciação em `main.py`/`app.py`, `start_gowa_task` hardcoded) só
   depois que o plugin estiver validado.

**Não quebrar:** instalações de 1 número devem migrar transparente para "canal único" (criar 1 row em
`channels` apontando para o device GOWA atual). Segredos da Cloud API **nunca** em `config` plaintext —
sempre `channel_credentials` cifrada.

---

## 2. Mudança B — Tools/prompts/config do agente no banco (motor Agno, single-agent)  *(PRIORIDADE — versão base/free, no CORE)*

> **Esta é a entrega principal e vem primeiro.** Na versão base/free, o motor (factory, `ai_engine.run`,
> tabelas `ai_*`, tool installer) vive **no CORE** — **não** é plugin nesta versão, e roda **um agente
> só**. O empacotamento da UI/CRUD como plugin e o multi-agente são extensões futuras/Pro (§2.2, §2.8).

### 2.1 Estado atual (o que troca)

`agent/handler.py` é um **singleton global**: um prompt (`config["system_prompt"]`), um modelo, um
conjunto de tools vindas de **código** (`CORE_TOOLS` em `agent/tools/__init__.py` + tools de plugin).
Pontos-chave: `__init__` (L43-89), `_register_tool` (L91-128), `register_plugin_tools` (L130-137),
`_dispatch_tool` (L226-246, dispatch genérico por registry — **manter esse padrão**), `_build_system_prompt`
(L434-543), `aprocess_message`/`process_message` (L545+). O bus de **filters** já reescreve prompt,
mensagens e tools (`filter.system_prompt`, `filter.llm.messages`, `filter.llm.tools`) — é a ponte para
plugar o Agno **sem reescrever os plugins** (doc 06 §8.2).

### 2.2 O que está decidido (cliente, 2026-06-18)

- **Agno** como runtime do agente (série v2.x, agentes stateless, `db` unificado SQLite/Postgres). **Na
  base roda single-agent** — o Agno é escolhido por ser leve e por **já dar suporte nativo** a
  multi-agente/`Team`/handoff, deixando a porta aberta para quem quiser estender depois (não exposto na base).
- **Prompts, variáveis, config do agente E o código Python das tools ficam no banco** (**code-in-DB**),
  estilo `/opt/gerenciamento-ia`: materializar `.py` + `pip install` deps + `importlib.reload` +
  `install_status` + versionamento/histórico. Motivo: mudar comportamento **sem deploy** e deixar a
  **IA criar/corrigir tools**.
- **Cortar do dia-1:** multi-agente/roteamento/handoff (fundação fica pronta, mas não implementado),
  embeddings/pgvector, produtos, ofertas, busca semântica.
- **Motor EMBUTIDO** no processo do WhatsBot (factory/orquestração in-process), **MAS a execução das tools
  code-in-DB roda num runner ISOLADO** (subprocess/worker com limites de SO + timeout).
- **Provider de LLM continua o proxy Techify** (OpenAI-compatible, `base_url=LLM_API_BASE_URL`), via
  `agno.models.openai.like.OpenAILike`.
- **Híbrido core/plugin:** núcleo do motor no **core** (tabelas `ai_*`, factory, `run`, ponte
  hooks↔filters, tool installer). A **UI/CRUD** pode ser extraída como plugin — **começar tudo no core**
  e extrair depois, se quiser.

### 2.3 Modelo de dados (core, Alembic, prefixo `ai_`)

Ver DDL completo no doc 06 §4. Resumo:

- `ai_agents` — `agent_key` (PK, identidade), `display_name`, `prompt_key`/`prompt_template`,
  `model_config` (JSON), `tool_names` (JSON array), `hooks_config` (JSON), `enabled`, `version`.
  + `ai_agents_history` (snapshot por save). **Na base há só 1 row** (o agente default). As colunas de
  multi-agente — `routing_targets` (JSON), `is_router` — **podem já existir no schema** (custo zero,
  evita migration futura) mas **não são usadas na base**; ficam reservadas para quem implementar
  multi-agente depois. *(Decisão barata: incluí-las ou omití-las — ver §4.)*
- `ai_prompts` — `(prompt_key, kind)` PK, `body` (template com `{placeholders}`), `version`.
- `ai_variables` — config global referenciável pelos prompts (`name`, `value`, `category`).
- `ai_tools` — **code-in-DB**: `name` (PK, identidade), `description`, `code` (fonte Python),
  `dependencies` (JSON), `enabled`, `install_status` (`pending|installing|ok|failed`), `install_error`,
  `version`, `created_by/updated_by`. + `ai_tools_history`.
- **Estender** `executions`/`execution_steps` com `agent_key`, `total_tokens`, `total_cost_usd`.
  (`routing_steps` só faz sentido com roteamento → fica para quem implementar multi-agente.)
- **Vínculo agente↔sessão NA BASE: `session_id` = o contato** (não há `Conversation`). Reusar
  `ContactMemory`/`messages` como sessão. Na base **não há "agente ativo por conversa"** — roda o agente
  default global. (`contacts.active_agent_key`, `conversations.active_agent_key`,
  `inboxes.default_agent_key` só aparecem quando multi-agente/inbox/conversa forem implementados —
  fora da base.)

> Portabilidade: arrays como **JSON em TEXT** nos dois backends (SQLite não tem array). Em Postgres pode
> virar JSONB depois — mas mantenha um só caminho de (de)serialização.

### 2.4 Registry de tools = união de 3 fontes

O `agent_factory` resolve tools por `name` da união de: (1) `CORE_TOOLS` (código), (2) tools de plugin,
(3) tools de `ai_tools` (banco). **Precedência sugerida em colisão: código > banco** (banco nunca
sequestra tool core; logar warning). Precedência de habilitação: agente só vê tool em `tool_names[]`
**E** `tool_overrides.enabled=1` **E** (se code-in-DB) `ai_tools.install_status='ok'`.

### 2.5 Tool installer (code-in-DB) — fluxo

Disparado ao salvar/ativar tool e no boot para tools `enabled=1` (doc 06 §5.3):

1. **Materializa** `ai_tools.code` em `.py` numa pasta gerenciada dedicada (ex.: `storages/ai_tools/<name>.py`,
   fora de `agent/` e de `storages/plugins/`, ignorada por git; nome validado por regex).
2. **Instala deps** declaradas, **filtradas por allowlist**; falha → `install_status='failed'` + a tool
   **não entra no registry** (fail-closed).
3. **Importa/recarrega** sob pacote namespaced (`whatsbot_ai_tools.<name>`, espelha `whatsbot_plugins.<id>`).
4. **Valida assinatura** — mesmo contrato de `CORE_TOOLS` (schema dict + `execute(ctx, args)`).
5. **Grava status** `ok` + bump `version` + snapshot em `ai_tools_history`.

### 2.6 Mitigações de segurança OBRIGATÓRIAS (contrapartida do code-in-DB)

Não são opcionais (doc 06 §5.4). **Importante:** todas as mitigações abaixo são **técnicas e independem
de RBAC/auditoria** (que são Pro) — ou seja, **funcionam na base sem precisar de usuários nem do sistema
de auditoria do doc 07**:

- **Isolamento por PROCESSO, não in-process** (Python não é sandboxável in-process de forma confiável):
  executar tools de `ai_tools` num subprocess/worker dedicado com `RLIMIT_CPU`/`RLIMIT_AS` + timeout
  rígido; se o host permitir, seccomp/AppArmor. Tools de `CORE_TOOLS`/plugin (código revisado) podem
  continuar in-process.
- **Least privilege do runner** — sem chave do LLM, sem credenciais admin, sem escrita no banco principal.
- **Gate de edição de código** — na base, "admin" = a **senha única compartilhada** atual (não há RBAC).
  A tela/endpoint de editar `ai_tools.code` (e `ai_agents`/`ai_prompts`) é protegida por ela. Quando "a IA
  cria uma tool", nasce `enabled=0`/`install_status='pending'` até alguém revisar e ativar (**gate humano**).
  *(Na Pro, isso passa a ser o papel ADM do RBAC — doc 03.)*
- **Histórico/versionamento embutido nas próprias tabelas `ai_*_history`** (snapshot before/after por save).
  Isso já dá rollback e rastro de mudança **sem depender do sistema de auditoria do doc 07** (que é Pro).
- **Deps validadas por allowlist**; preferir `pip install --require-hashes`/lockfile. Ideal de médio prazo:
  venv pré-congelado sem `pip install` arbitrário em runtime.
- **Timeouts + fail-closed**; validação AST leve antes de instalar (defesa em profundidade, **não** a
  barreira principal — a barreira é o isolamento por processo).

### 2.7 Integração com o pipeline (sem reescrever plugins)

- Ponto de substituição: **uma camada acima** de `AgentHandler.process_message` — onde `_process_batch`
  chama o handler hoje, passa a chamar `ai_engine.run(contact/phone, text)` (doc 06 §8.1, mas **chaveado
  por contato na base** e com **o agente default**, não por `conversation_id`/seleção de agente). Manter
  o `AgentHandler` atual como **fallback atrás de flag** durante a transição.
- **Ponte hooks↔filters** (doc 06 §8.2): aplicar `filter.system_prompt` ao prompt renderizado antes de
  instanciar o `Agent`; `filter.llm.messages`/`filter.llm.tools` como pre-hooks; `filter.tool.args/result`
  como `@tool(pre_hook/post_hook)`; `filter.reply.*` continua no `_send_reply`. Assim
  `horario_funcionamento`, `blacklist`, `auto_signature` etc. **continuam funcionando sem alteração**.
- **Sessão/memória (recomendado):** **continuar montando o histórico de `messages`/`ContactMemory`** e
  passar ao Agno como contexto (uma fonte de verdade — `messages` é o que a UI mostra), em vez de deixar
  o Agno gerir `agno_sessions`. Reavaliar memória autônoma do Agno depois.

### 2.8 Multi-agente / roteamento — FORA do escopo da base (fundação para extensão futura)

**A base não tem multi-agente nem handoff entre IAs.** Esta seção existe só para registrar **o que a
escolha do Agno deixa preparado** — para quem quiser implementar por conta própria depois, com suporte
nativo do framework (não é trabalho a fazer agora):

- O Agno já traz `Team` (route/coordinate/broadcast) e handoff entre agentes nativamente (doc 06 §6).
- O caminho documentado (doc 06 §6.2) seria: tool `transferir_para_outro_agente(agent_key, motivo)`,
  `routing_targets[]` por agente, um "agente ativo" por sessão (na base seria por contato), profundidade
  máx. anti-loop e `routing_steps` no `executions`.
- Como a base usa o mesmo runtime (Agno) e o mesmo registry de tools/prompts no banco, adicionar isso
  **não exige reescrever o motor** — só ligar as peças que já ficam reservadas (§2.3).

> Para o produto público: o motor roda **um agente**. Não construir tool de transferência, `Team`, nem
> seleção de agente. `transfer_to_human` (que só desliga a IA do contato) **continua existindo** como hoje.

### 2.9 Hot-reload + versionamento

- **Dado** (agente/prompt/variável, `enabled`/`description` de tool): invalidação de cache por evento
  (`ai.config.changed`) + fallback de polling TTL (~60s p/ uvicorn `--workers > 1`). **Sem restart.**
- **Código** (`ai_tools.code` mudou): passa pelo tool installer (recarrega só aquele módulo). Falha →
  `failed`, fail-closed.
- Cada save em `ai_agents`/`ai_prompts`/`ai_tools` faz `version += 1` + snapshot em `*_history`.

### 2.10 Faseamento sugerido (doc 06 §10)

1. **Fundação de dados** — criar `ai_*` (+ `*_history`); estender `executions`. Seed: **1 agente "default"**
   cujo prompt = `config["system_prompt"]` atual (paridade total com hoje).
2. **O agente (único) configurável** — `agent_factory.build(session_id=contato)` → `agno.Agent` com
   `OpenAILike` (Techify), prompt renderizado, tools do registry (union, **ainda sem** code-in-DB).
   `ai_engine.run` substitui (atrás de flag) a chamada ao handler. Plugar filters (manter contrato de
   tools — §2.4/§2.7). **Critério: respostas idênticas às de hoje.**
3. **Code-in-DB** — installer + runner isolado + gate por senha única + histórico em `ai_*_history`
   (§2.6; **sem** RBAC nem auditoria do doc 07 — isso é Pro). **Critério: criar/editar tool pelo painel
   reflete sem deploy; tool ruim falha fechada sem derrubar o webhook.**
4. **CRUD no painel + hot-reload** — telas para editar prompt/variáveis/tools/config do agente (no core
   na base); invalidação de cache por evento + TTL.
5. **Refinos** — structured output do Agno p/ "split de mensagens"; hooks declarativos; "IA propõe tool".

> **Fora do escopo da base:** **multi-agente / roteamento / handoff** (fundação fica pronta no Agno —
> §2.8), agente por **inbox** (`inboxes.default_agent_key`), sessão por **conversa**, RBAC/papel ADM,
> sistema de auditoria do doc 07, e o empacotamento da UI como plugin. Tudo isso é Pro ou extensão futura.

---

## 3. Decisões já tomadas (não reabrir)

- **Prioridade: tools/prompts/config do agente no banco (Mudança B) vem primeiro e entra na versão
  base/free, no CORE.** A plugin-ização (Mudança A) é da versão Pro.
- **A base é SINGLE-AGENT.** Multi-agente/roteamento/handoff **não** entram na base; o Agno é adotado
  para deixar a **fundação pronta** (suporte nativo) caso alguém queira estender depois (§2.8).
- Tenancy: **uma empresa, server-hosted** (Coolify/Docker). Sem multi-tenant (mas não fechar a porta).
- Motor de IA: **Agno**, **embutido no core**, **code-in-DB** (com runner de tools isolado).
- **Na versão base, GOWA e motor de IA ficam no CORE** (não viram plugin).
- **Na versão Pro, GOWA vira provider-plugin** — e, quando virar, nasce já como plugin (não built-in
  temporário para mover depois). O motor de IA pode ter a UI/CRUD extraída como plugin.
- Cortar dia-1: embeddings, produtos, ofertas, RAG.
- Provider de LLM: **proxy Techify** (OpenAI-compatible).
- Capacidades de runtime (i)+(ii)+(iii) — só necessárias para a Mudança A (Pro) — são **CORE**, não plugin.

---

## 4. Decisões em aberto (precisam de resposta antes/durante)

### GOWA / canais (doc 02 §10)
1. Isolamento do runner de subprocesso no dia-1: subprocess + `RLIMIT_*` + timeout basta, ou já
   seccomp/AppArmor? **O host Coolify/Docker permite seccomp/AppArmor?**
2. GOWA multi-número: 1 processo / N devices (Opção A, recomendada) confirmado, com Opção B (N processos)
   como fallback por canal?
3. Roteamento de webhook: path por canal (`/api/webhook/{provider}/{channel_id}`) como padrão, ok?

### Agno / IA (base — doc 06 §11)
4. **Sessão:** Agno `db` (`agno_*`) ou continuar montando histórico de `messages`? (recomendado: `messages`).
5. **Colunas de multi-agente no schema:** incluir `routing_targets`/`is_router` em `ai_agents` já agora
   (custo zero, evita migration futura) mesmo sem usar na base, ou omitir e adicionar quando alguém
   implementar multi-agente? (§2.3)
6. **Isolamento do runner code-in-DB no dia-1:** subprocess+`RLIMIT_*`+timeout, ou microVM/Firecracker?
7. **Gate da IA criando tools:** sempre nasce `pending` esperando aprovação humana, ou existe modo em
   que tools propostas pela IA entram ativas? (trade-off autonomia × segurança).
8. **Allowlist de deps:** lista fixa ou qualquer dep com aprovação por item? Usar `--require-hashes`
   desde já?
9. **Precedência tool código × banco** em colisão de `name`: código ganha (proposta) — confirmar + como
   sinalizar na UI.
10. **`ai_variables` dedicada vs prefixo em `config`** — decidir antes do schema.
11. **uvicorn `--workers > 1` em produção?** (muda a estratégia de hot-reload).
12. **Migração do `AgentHandler`:** quanto tempo rodar os dois em paralelo (flag) antes de aposentar o singleton?
13. **`output_schema` (structured output)** vs "JSON array de strings via prompt" para o split de mensagens?

---

## 5. Regras para NÃO quebrar o sistema

- **Tudo atrás de flag durante a transição.** O `AgentHandler` singleton continua funcionando até o
  motor novo estar validado. Aposentar só no fim.
- **Não puxar Pro/extensões para a base.** Multi-agente/roteamento/handoff, inbox/conversa, RBAC/usuários
  e auditoria (doc 07) **não** entram na base — ela é **single-agent**, ancorada no **contato**. O Agno só
  deixa a fundação pronta para quem quiser estender (§2.8).
- **Contrato de tool é identidade.** `name` de tool nunca renomeado (quebra `usage.call_type` e
  `tool_overrides`). Vale também para `agent_key`, `prompt_key`, `ai_tools.name`.
- **Dispatch genérico por registry** — nunca `if/elif` por nome de tool (regra do projeto, `_dispatch_tool`).
- **Tabelas de domínio (`ai_*`) são core/Alembic** — o migrator de plugin **força** prefixo
  `plugin_<id>_*` e recusa o contrário; plugin não cria nem altera tabela core.
- **Code-in-DB só com as mitigações da §2.6** — isolamento por processo, gate por senha, histórico,
  allowlist, timeouts/fail-closed. Sem elas, **não** liberar o code-in-DB.
- **Validar deps do Agno num venv limpo** antes de adotar (conflito pydantic/sqlalchemy/openai).

---

## 6. Arquivos-âncora (onde mexer)

| Tema | Arquivos | Versão |
|---|---|---|
| **Motor IA (Agno)** | `agent/handler.py`, `agent/tools/__init__.py`, `agent/memory.py`, **novos** `ai_engine/` + `db/repositories/agent_repo.py`/`tool_repo.py`/`prompt_repo.py` | **Base** |
| **Banco** | `db/tables.py`, `db/alembic/versions/` (6 migrations hoje, naming `YYYYMMDD_NNNN_desc.py`) — novas tabelas `ai_*` | **Base** |
| **Deps** | `requirements.txt` (já tem `agno`, `openai`) | **Base** |
| GOWA / canais | `gowa/manager.py`, `gowa/client.py`, `main.py:57-58`, `server/app.py:65-183`, `server/background.py`, `server/routes/webhook.py` | Pro |
| Plugins (capacidades de runtime) | `plugins/loader.py`, `plugins/context.py`, `plugins/restart.py`, `plugins/manifest.py`, `plugins/events.py` | Pro |

> Docs de pesquisa de referência. **Para a versão base, o único relevante é
> `docs-pesquisa/06-motor-multiagente-agno.md`** (motor Agno, code-in-DB) — lendo-o **ignorando** as
> partes de inbox/conversa (doc 01), RBAC (doc 03) e auditoria (doc 07), que são **Pro**. Para a versão
> Pro, ver também `00-visao-geral.md` e `02-canais-e-providers.md` (GOWA-plugin/canais).
