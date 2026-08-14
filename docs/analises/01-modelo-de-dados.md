# WhatsBot — Modelo de dados para análises

> Dicionário de dados do banco Postgres do WhatsBot, escrito para ser **contexto consumível por uma IA analista**. Aqui está só o **dicionário** (o que cada tabela/coluna guarda) e o **mapa de junções** (como juntar). As **receitas de SQL** ficam na doc 03 — não as repita aqui.
>
> Convenções válidas em todo o banco:
> - **Todos os timestamps são epoch float em UTC** (colunas `Float`/`DOUBLE PRECISION`, ex.: `ts`, `opened_at`, `created_at`). Não são `TIMESTAMP` SQL. Para bucketizar "no dia", sempre `(to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date`.
> - **Flags booleanas são `Integer` 0/1** (ex.: `ai_active`, `is_archived`), não `boolean`.
> - **JSON**: colunas nativas `JSONB` no core (ex.: `custom_attributes`, `model_config`); nos plugins, JSON é `TEXT` (ex.: `fields`, `payload`).
> - Nomes de tabela/coluna estão em **inglês** (como no banco); a prosa está em PT-BR.
> - `file:line` referem-se a arquivos sob `/home/thiago/whatsbot-pro/whatsbot-pro/`.

---

## 1. Glossário (termos canônicos — use exatamente estes)

| Termo | Definição | Tabela |
|---|---|---|
| **Atendimento** | Uma conversa/ticket do core. É a unidade de "atendimentos abertos/fechados". Renomeada de `conversations` do Chatwoot. | `atendimentos` (alias Python `conversations = atendimentos`, `db/tables.py:853`) |
| **Conversa (nativa)** | O MESMO objeto `atendimentos`, sem o plugin de protocolos. "Quantidade de conversas nativas" = contagem de `atendimentos`. | `atendimentos` |
| **Protocolo** | Um ticket do **plugin** `protocolos`, por-contato, com ciclo próprio. **DISTINTO** do atendimento core. Nunca confunda com a tabela `atendimentos`. | `plugin_protocolos_protocolos` |
| **Contato** | Um número/pessoa; funde todos os canais de um mesmo número. | `contacts` |
| **Atendente** | Uma linha em `users` (RBAC; papel `atendente\|gestor\|admin` via `user_roles`→`roles.key`). Não existe entidade "atendente" separada. | `users` |
| **IA / agente** | Agente de IA, identificado por `messages.agent_key` (não-nulo) e rastreado em `executions`. | `ai_agents`, `executions` |

### Discriminadores canônicos do remetente de uma mensagem (tabela `messages`)

Não existe coluna `direction` nem `source` em `messages`. A identidade do remetente é **derivada** de `(role, status, agent_key, sent_by_user_id)`:

| Remetente | Discriminador |
|---|---|
| **Cliente** | `role='user'` |
| **IA** | `role='assistant' AND agent_key IS NOT NULL` (tipicamente `status='sent'`, `execution_id` setado). `agent_key` é o discriminador confiável. |
| **Atendente humano (envio pelo painel)** | `role='assistant' AND status='operator' AND sent_by_user_id IS NOT NULL`. `sent_by_user_id` é o ÚNICO lugar com o atendente específico; `sent_by_name` = snapshot do nome. |
| **Echo** (operador digitou no próprio celular) | `role='assistant' AND status='operator' AND sent_by_user_id IS NULL AND agent_key IS NULL` |
| **Cards painel-only** (NÃO vão ao WhatsApp) | `role IN ('tool_call','system_notice','transcription','private_note','error','conversation_event','system')` |

O enum `source` do event-bus (`ai`/`operator`/`private_ai`/`retry`/`echo`/`template`) **não é persistido** — `private_ai` é indistinguível de `ai` no banco, e `echo` de `operator` só pela nulidade de `sent_by_user_id`/`sent_by_name`.

---

## 2. Mapa de junções

O core é um modelo de 3 níveis (**inbox → contact_inbox → atendimento**), com `contacts` de um lado e `messages` do outro.

```
channels ──1:1── inboxes ──1:N── atendimentos ──1:N── messages
                    │                  │  ▲                │
                    │                  │  │ conversation_id │ (nullable)
 contacts ──1:N── contact_inboxes ─────┘  └─────────────────┘
    │                                      contact_id (NOT NULL, chave primária de posse)
    ├──1:N── messages          (messages.contact_id)
    ├──1:N── usage             (usage.contact_id  ← ÚNICO link de usage; sem conversation_id/agent_key!)
    ├──N:N── tags              (via contact_tags)
    └──1:N── observations
```

### Chaves de junção (todas verificadas em `db/tables.py`)

| De | Para | Coluna | Observação |
|---|---|---|---|
| `atendimentos` | `contacts` | `atendimentos.contact_id → contacts.id` | FK CASCADE, NOT NULL |
| `atendimentos` | `inboxes` | `atendimentos.inbox_id → inboxes.id` | FK CASCADE, NOT NULL |
| `atendimentos` | `contact_inboxes` | `atendimentos.contact_inbox_id → contact_inboxes.id` | identidade pessoa-no-canal |
| `inboxes` | `channels` | `inboxes.channel_id → channels.id` | FK CASCADE, **nullable** (NULL = canal removido → inbox órfã) |
| `contact_inboxes` | `contacts` / `inboxes` | `contact_id` / `inbox_id` | UNIQUE`(inbox_id, source_id)` — 1 identidade por (canal, JID) |
| `messages` | `contacts` | `messages.contact_id → contacts.id` | **NOT NULL** — chave primária de posse (funde canais) |
| `messages` | `atendimentos` | `messages.conversation_id → atendimentos.id` | FK CASCADE, **nullable** — linhas legadas/não-vinculadas ficam NULL |
| `messages` | `users` | `messages.sent_by_user_id → users.id` | **FK lógica, sem constraint** — atendente que enviou manual |
| `messages` | `executions` | `messages.execution_id → executions.id` | **FK lógica, sem constraint** — liga resposta da IA ao turno |
| `atendimentos` | `users` | `atendimentos.assignee_user_id → users.id` | **FK lógica, sem constraint** — dono humano. ⚠️ ZERADO ao fechar (ver §3) |
| `atendimentos` | `ai_agents` | `atendimentos.active_agent_key → ai_agents.agent_key` | agente vinculado. ⚠️ ZERADO ao fechar |
| `executions` | `atendimentos` | `executions.conversation_id → atendimentos.id` | **coluna solta, sem FK**, nullable (legado = NULL) |
| `execution_steps` | `executions` | `execution_steps.execution_id → executions.id` | FK CASCADE (steps morrem com a execução) |
| `usage` | `contacts` | `usage.contact_id → contacts.id` | FK CASCADE. **⚠️ ÚNICO link de `usage`** |
| `users` | `roles` | via `user_roles(user_id, role_id)` | N:N |
| `roles` | `permissions` | via `role_permissions(role_id, permission_id)` | N:N |
| `atendimentos` | `atendimento_labels` | via `atendimento_label_links(conversation_id, label_id)` | N:N — etiquetas de CONVERSA (≠ tags de contato) |
| `contacts` | `tags` | via `contact_tags(contact_id, tag_id)` | N:N — tags de CONTATO |
| `plugin_protocolos_protocolos` | `contacts` | `.contact_id → contacts.id` | sem FK física |
| `plugin_protocolos_atendimentos` | `atendimentos` + protocolo | `.conversation_id → atendimentos.id`, `.protocolo_id → plugin_protocolos_protocolos.id` | tabela de VÍNCULO/CICLO (ver §5) |

### ⚠️ Armadilhas de junção (críticas para análise)

1. **`usage` NÃO tem `conversation_id`, `execution_id` nem `agent_key`** — só `contact_id`. Não dá para fatiar tokens/custo por atendimento nem por agente a partir de `usage`. Custo **por agente** só sai de `executions.total_cost_usd`/`total_tokens` (agregado por-turno, atribuído ao agente FINAL). As duas fontes (`usage` × `executions`) não têm chave comum e podem divergir.
2. **`messages.conversation_id` é nullable** — mensagens legadas / turnos não rastreados têm NULL; joins por conversa perdem essas linhas. A chave de posse sempre presente é `contact_id`.
3. **`executions.conversation_id` é coluna solta e nullable** — populada só no caminho novo (plano 36); linhas legadas ficam NULL. `executions.phone` colide entre canais — não use `phone` como chave de conversa.
4. **`team_id` existe em `atendimentos` mas NÃO há tabela `teams`** nem writer — rollup por time é impossível hoje.

---

## 3. Dicionário por tabela — CORE

### `contacts` (`db/tables.py:60-89`)
Um número/pessoa; funde canais. `phone` é UNIQUE.

| Coluna | Tipo | Significado / como usar |
|---|---|---|
| `id` | Integer PK | chave de posse referenciada por `messages`, `usage`, `atendimentos`, protocolos |
| `phone` | Text, UNIQUE | número/JID normalizado |
| `name` | Text | nome do contato |
| `email` / `profession` / `company` / `address` | Text | info extraída (preenchida por tool calling da IA) |
| `ai_enabled` | Integer 0/1 | gate de IA **no nível do contato** (distinto de `atendimentos.ai_active`) |
| `is_group` | Integer 0/1 | 1 = grupo. Filtre grupos fora em análises de atendimento 1:1 |
| `group_name` | Text | nome do grupo |
| `is_archived` | Integer 0/1 | arquivamento (chat-level) |
| `is_pinned` | Integer 0/1 | fixado no topo |
| `contact_type` | Text, default `'outros'` | tipo herdado do canal que criou o contato: `whatsapp` (GOWA+Cloud), `telegram`, `outros`. Backfill legado = `whatsapp`. Gravado só no INSERT. |
| `custom_attributes` | JSONB, default `{}` | atributos personalizados de escopo **contato** (definições em `custom_attribute_definitions` com `applies_to='contact'`) |
| `created_at` | Float epoch | **1º contato** — primitivo para a regra de re-engajamento (15/30 dias). Nunca re-tipado |
| `updated_at` | Float epoch | última atualização da linha |

### `atendimentos` (`db/tables.py:426-468`) — a conversa/ticket do core
Alias Python `conversations`. No máx. **1 aberto por (contato, inbox)** (índice parcial `uq_atend_open_contact_inbox WHERE status='open'`).

| Coluna | Tipo | Significado / como usar |
|---|---|---|
| `id` | Integer PK | referenciado por `messages.conversation_id`, `executions.conversation_id`, vínculo de protocolos |
| `display_id` | Integer, UNIQUE | número sequencial **global** amigável (de `atendimento_counters`) — não é per-contato |
| `inbox_id` | Integer FK→`inboxes.id` | canal/inbox |
| `contact_id` | Integer FK→`contacts.id` | o contato |
| `contact_inbox_id` | Integer FK→`contact_inboxes.id` | identidade pessoa-no-canal |
| `status` | Text, default `'open'` | **Apenas `open` \| `closed`** (não existe `resolved`/`pending`/`snoozed`). Igualdade simples de string |
| `is_archived` | Integer 0/1 | ortogonal a `status`. **Sem timestamp de arquivamento** |
| `assignee_user_id` | Integer, FK lógica→`users.id` | atendente humano dono. ⚠️ **ZERADO (NULL) ao fechar** (`conversation_repo.py:641`) — conversa fechada não tem assignee na linha. Só populado enquanto `open` |
| `team_id` | Integer | ⚠️ **coluna morta** — não há tabela `teams` nem writer |
| `priority` | Text | prioridade (texto livre) |
| `ai_active` | Integer 0/1, default 1 | gate de IA **no nível da conversa** (3º nível do gate global→canal→conversa) |
| `active_agent_key` | Text → `ai_agents.agent_key` | agente vinculado à conversa. ⚠️ **ZERADO ao fechar** |
| `origin` | Text, nullable | quem iniciou: `inbound` (cliente) / `outbound` / `manual` (operador OU IA) / `imported`. Carimbado no CREATE pelo role da 1ª msg (`inbound if role='user' else outbound`, `agent/memory.py:307`). ⚠️ `outbound`/`manual` **misturam humano E IA** — para isolar humano, cruze com `messages.sent_by_user_id NOT NULL` da 1ª msg assistant. `manual` = backfill legado (migration 0034); `imported` = **valor morto** (nenhum runtime o escreve) |
| `opened_at` | Float epoch | abertura. **NÃO é resetado no reopen** — reaberto hoje ainda mostra o `opened_at` original |
| `resolved_at` | Float epoch, nullable | timestamp de fechamento. ⚠️ **VOLÁTIL**: setado no close, **apagado (NULL) no reopen**; re-close sobrescreve → só o ÚLTIMO fechamento sobrevive. Não conta fechamentos intermediários |
| `waiting_since` | Float epoch, nullable | ⚠️ **COLUNA MORTA** — declarada mas nunca escrita por nenhum código. Sempre NULL |
| `last_activity_at` | Float epoch | bump a cada atividade (ordenação da sidebar) |
| `custom_attributes` | JSONB, default `{}` | atributos personalizados de escopo **conversa** (`applies_to='conversation'`). É onde o plugin protocolos espelha campos do ciclo (`mirror_atendimento_to_core`) |
| `created_at` / `updated_at` | Float epoch | inserção / última atualização |

**Não existe:** `first_reply`/`first_response_at` (nenhum timestamp de primeira resposta), `closed_by_user_id`, `archived_at`, histórico durável de status. Transições ficam só como cards `messages(role='conversation_event')` (ator só no texto PT-BR, sem user_id).

### `inboxes` (`db/tables.py:379-394`) — nível 1 (uma por canal)

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | Integer PK | |
| `name` | Text, default `'WhatsApp'` | nome de exibição |
| `channel_type` | Text, default `'whatsapp'` | |
| `channel_id` | Text FK→`channels.id`, nullable | link 1:1 ao canal; NULL = canal removido |
| `agent_bot_enabled` | Integer 0/1 | gate de IA nível-2 (canal) |
| `default_agent_key` | Text | agente default para conversas novas dessa inbox |
| `created_at` / `updated_at` | Float epoch | |

`inbox_members(inbox_id, user_id)` (`db/tables.py:416-424`) = quais usuários do painel veem cada inbox (escopo de visibilidade).

### `contact_inboxes` (`db/tables.py:396-408`) — nível 2 (contato dentro de um canal)

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | Integer PK | |
| `contact_id` | Integer FK→`contacts.id` | |
| `inbox_id` | Integer FK→`inboxes.id` | |
| `source_id` | Text, NOT NULL | chave de resolução = o JID. UNIQUE`(inbox_id, source_id)` |
| `source_jid` | Text, nullable | |
| `source_lid` | Text, nullable | identidade alternativa `@lid` do WhatsApp |
| `created_at` / `updated_at` | Float epoch | |

### `messages` (`db/tables.py:108-149`) — histórico completo
Ver os **discriminadores canônicos de remetente** em §1. `ts` é o **único** timestamp por mensagem.

| Coluna | Tipo | Significado / como usar |
|---|---|---|
| `id` | Integer PK | |
| `contact_id` | Integer FK→`contacts.id`, **NOT NULL** | posse (funde canais). Sempre presente |
| `role` | Text, NOT NULL | `user` (cliente) / `assistant` (IA ou operador) / cards painel-only. Base do discriminador |
| `content` | Text | texto da mensagem / texto do card |
| `ts` | Float epoch | timestamp — âncora de toda métrica de tempo |
| `media_type` | Text, nullable | `image`/`audio`/`video`/`sticker`/`document`/`location`/… |
| `media_path` | Text, nullable | caminho do arquivo, ou `geo:lat,lng` |
| `status` | Text, nullable | delivery/autoria: `sent`→`delivered`→`read` (forward-only, sobrescrito no lugar); `operator` (humano/echo); `failed`. **Sem histórico de quando cada estágio ocorreu** |
| `msg_id` | Text, nullable | id externo GOWA/canal |
| `revoked` | Integer, default 0 | 0=não, 1=apagada pra todos, 2=pra mim |
| `reactions` | Text (JSON) | `{emoji: [reactor,...]}` |
| `reply_to_msg_id` | Text, nullable | msg_id GOWA da mensagem citada |
| `conversation_id` | Integer FK→`atendimentos.id`, **nullable** | o atendimento. NULL em linhas legadas/não-vinculadas |
| `sent_by_user_id` | Integer, FK lógica→`users.id`, nullable | **o atendente humano** que enviou manual (painel). ÚNICO lugar com o user_id específico. NULL em IA, echo e cliente |
| `sent_by_name` | Text, nullable | **snapshot** do nome do atendente no envio (não faz join) |
| `agent_key` | Text, nullable → `ai_agents.agent_key` | agente de IA que produziu a msg `assistant`/`tool_call`. NULL em não-IA. Discriminador confiável da IA |
| `execution_id` | Integer, FK lógica→`executions.id`, nullable | liga resposta da IA ao turno em `executions` (latência de IA, tokens/custo do turno) |

Cards painel-only (`role IN ('tool_call','system_notice','transcription','private_note','error','conversation_event','system')`) **não são mensagens reais do WhatsApp** — exclua-os de contagens de "mensagens trocadas". Precisão: a blacklist de contexto do LLM (`message_repo.py:156-157`) cobre **6** deles (não `private_note`); `conversation_event` também sai da preview da sidebar e não conta como não-lida.

**`conversation_event`** é a única trilha durável de ciclo de vida da conversa (created, status_closed, status_open, status_reopened_auto, assigned, unassigned, tag_added, ai_on/ai_off, ai_takeover, agent_changed, …). **O ator está SÓ no texto PT-BR de `content`** (nome de exibição, sem user_id) — a linha não carrega `sent_by_user_id`/`agent_key`. Grupos gated por config (default ON): grupo desligado ⇒ card não é gravado.

### `users` + `roles` + `user_roles` (`db/tables.py:288-343`)
O atendente É uma linha em `users`. O papel vem de `user_roles → roles.key`.

**`users`** (`288-304`):
| Coluna | Tipo | Significado |
|---|---|---|
| `id` | Integer PK | referenciado por `messages.sent_by_user_id`, `atendimentos.assignee_user_id`, `audit_log.actor_user_id`, protocolos |
| `email` | Text, UNIQUE (lowercase) | |
| `name` | Text | nome de exibição (o `sent_by_name`/`assignee_name`/`actor_label` são snapshots disto) |
| `password_hash` | Text | Argon2id — nunca exponha |
| `is_active` | Integer 0/1 | |
| `custom_permissions` | Integer 0/1 | 1 = grants explícitos por-usuário substituem os do papel |
| `last_login_at` / `created_at` / `updated_at` | Float epoch | |

**`roles`** (`306-314`): `id` PK, `key` UNIQUE (`admin`\|`gestor`\|`atendente`), `name`, `is_system`.
**`user_roles`** (`337-343`): PK composta `(user_id, role_id)` — N:N usuário↔papel.
**`permissions`** / `role_permissions` / `user_permissions`: grafo RBAC (relevante só para autorização, não para volume de atendimento).

Para atribuir um atendente por `id` a algo, junte `messages.sent_by_user_id`/`atendimentos.assignee_user_id`/`plugin_protocolos_*.assignee_user_id` em `users.id`. Os `*_name` são snapshots congelados — para nome fiel atual, sempre join em `users`.

### `channels` (`db/tables.py:233-271`)
Canal físico (provider). `id` é Text (`'default'`, snake_case), não Integer.

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | Text PK | referenciado por `inboxes.channel_id` |
| `provider` | Text | `gowa` \| `whatsapp_cloud` \| `telegram` \| `test` |
| `display_name` | Text | |
| `enabled` | Integer 0/1 | |
| `gowa_isolation` | Text, default `shared` | `shared` \| `dedicated_process` (proxy por número) |
| `connected` / `logged_in` | Integer 0/1 | estado da sessão |
| `own_phone` | Text, nullable | número da própria conta (device-scoped) |
| `account_identity` / `account_identity_kind` | Text, nullable | dedup de conta (plano 32) |
| `archived` | Integer 0/1 | soft-delete (esconde da UI, preserva histórico) |
| `last_error` | Text, nullable | último erro |
| `created_at` / `updated_at` | Float epoch | |

### `executions` (`db/tables.py:539-575`) — um turno de agente por mensagem inbound

| Coluna | Tipo | Significado / como usar |
|---|---|---|
| `id` | Integer PK | referenciado por `messages.execution_id` |
| `phone` | Text | ⚠️ **colide entre canais** — não use como chave de conversa |
| `trigger_type` | Text, default `'webhook'` | |
| `status` | Text | `running` / `completed` / `failed` |
| `started_at` / `completed_at` | Float epoch | latência de IA = `completed_at - started_at` |
| `error` | Text | texto de erro no fail |
| `agent_key` | Text | **o agente FINAL** do turno. Em turnos multi-agente (router→spoke), todos os tokens são atribuídos ao ÚLTIMO |
| `total_tokens` / `total_cost_usd` | Integer / Float | agregado do turno (somado sobre as chamadas de LLM). **Custo é congelado no write** e vira `0.0` quando o modelo falta no cache de preços |
| `routing_steps` | JSON | `[{from,to,depth,reason}]` — cadeia de handoff |
| `conversation_id` | Integer, **coluna solta sem FK**, nullable | o atendimento. Populado só no caminho novo; legado NULL |
| `channel_id` / `channel_label` | Text | canal denormalizado |
| `input_text` / `output_text` | Text | msg do cliente / resposta final da IA |
| `msg_id` | Text | msg_id WhatsApp da origem |
| `has_ai` | Integer 0/1 | 1 sse o turno realmente invocou o modelo (tem step `llm_*`). Base do "só execuções com IA" — mas turno só-transcrição-de-mídia pode ficar `has_ai=0` |

⚠️ **Executions são podadas** (por contagem e idade); `execution_steps` cascateiam. Relatório histórico de longo prazo em nível de step é lossy.

### `execution_steps` (`db/tables.py:578-589`) — trace por-passo do turno

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | Integer PK | |
| `execution_id` | Integer FK→`executions.id` CASCADE | |
| `step_type` | Text | `webhook_received`, `batch_accumulated`, `llm_context`, `llm_request`, `llm_response`, `tool_executed`, `media_processed`, `channel_send`, `response_sent`, `routing_halted`, `error` |
| `status` | Text | `ok` / `error` |
| `data` | JSON | args/result de tool, model, messages, etc. (não indexado) |
| `ts` | Float epoch | |
| `agent_key` | Text | agente que rodou este passo (atribuição por-hop). ⚠️ **steps NÃO carregam tokens/custo** — não dá para custear um turno por agente |

Tool calls = `step_type='tool_executed'`; transferências pra humano = `data->>'tool'='transfer_to_human'` (+ `routing_halted`). Não há coluna/contador dedicado.

### `usage` (`db/tables.py:152-166`) — uma linha por chamada de LLM cobrável
⚠️ **Só tem `contact_id` como link.** Sem `conversation_id`, sem `execution_id`, sem `agent_key`.

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | Integer PK | |
| `contact_id` | Integer FK→`contacts.id` | **ÚNICO** eixo relacional |
| `call_type` | Text | `text` (resposta principal + cada hop de routing) / `audio` / `image` / `document`. Para tool de código, `call_type == ai_tools.name` |
| `model` | Text | id do modelo usado |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | Integer | tokens |
| `cost_usd` | Float | custo USD (congelado no write; `0.0` se o preço faltar no cache) |
| `ts` | Float epoch | timestamp — eixo temporal (`idx_usage_ts`) |

`usage` é a única fonte de tokens/custo **por dia** e **por tipo de chamada** que sobrevive a longo prazo, mas **não fatiável por agente nem por conversa**. Custo por agente só via `executions`.

### `ai_agents` (`db/tables.py:643-670`) — identidade/config do agente

| Coluna | Tipo | Significado |
|---|---|---|
| `agent_key` | Text PK | **identidade — nunca renomear** (`executions.agent_key`/`messages.agent_key` apontam pra cá) |
| `display_name` | Text | nome exibido ("IA - <NOME>") |
| `prompt` | Text | prompt inline (fonte da verdade; `{placeholder}` resolvidos de `ai_variables`) |
| `prompt_key` | Text | **legado** — não participa mais da resolução |
| `model_config` | JSONB | `{model, temperature, ...}` |
| `tool_names` | JSONB | array de nomes de tool, ou null/"all" = todas |
| `enabled` | Integer 0/1 | |
| `is_router` | Integer 0/1 | roteador único (índice parcial `ux_ai_agents_single_router`) |
| `is_default` | Integer 0/1 | agente padrão de novas conversas E fallback de runtime (radio único) |
| `routing_targets` | JSONB | agent_keys alcançáveis |
| `version` / `updated_at` | Integer / Float | versionamento (snapshot em `ai_agents_history`) |

`ai_variables` (name/value), `ai_tools` (name/code — `name == usage.call_type`) e `ai_prompts` (legado, não lido) são **config**, não dados de atividade.

### `tags` + `contact_tags` (`db/tables.py:169-184`) — tags de CONTATO

- **`tags`**: `id` PK, `name` UNIQUE, `color`. Tags globais.
- **`contact_tags`**: PK composta `(contact_id, tag_id)` — N:N contato↔tag.
- Tag legada: `transferido_atendente` (`TRANSFER_TAG`). Já sinalizou o gate de humano; hoje **não é escrita por ninguém** (a `transfer_to_human` deixou de aplicá-la) — o gate é `atendimentos.ai_active`/`assignee_user_id`. Linhas antigas ainda são removidas quando a IA reassume.

### `atendimento_labels` + `atendimento_label_links` (`db/tables.py:482-499`) — etiquetas de CONVERSA
**Separadas** das tags de contato (aplicam-se ao atendimento, não ao número).

- **`atendimento_labels`**: `id` PK, `name` UNIQUE, `color` (default `#6b7280`), `position`, `created_at`.
- **`atendimento_label_links`**: PK composta `(conversation_id, label_id)` — N:N. ⚠️ a coluna se chama `conversation_id` (nome congelado, contrato de plugin), aponta para `atendimentos.id`.
- **Candidata a ground-truth de conversão**: uma etiqueta `venda` aqui é a convenção recomendada para marcar "venda que deu certo" (não existe coluna de resultado hoje).

### `custom_attribute_definitions` (`db/tables.py:203-226`) — definições de atributos personalizados
Os **valores** vivem em `contacts.custom_attributes` / `atendimentos.custom_attributes` (JSONB); aqui está o catálogo/schema.

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | Integer PK | |
| `attribute_key` | Text | snake_case, **identidade** (é a chave dentro do JSON) |
| `display_name` | Text | rótulo |
| `type` | Text, default `text` | `text\|number\|date\|list\|checkbox\|link` |
| `applies_to` | Text | `contact` \| `conversation` — separa os dois escopos. UNIQUE`(attribute_key, applies_to)` |
| `options` | JSONB, nullable | array de strings (só `type=list`) |
| `required` / `filterable` / `is_system` | Integer 0/1 | obrigatório / entra no filtro / atributo de sistema (protegido) |
| `position` | Integer | ordem |
| `created_by` | Integer, nullable | FK lógica → users |
| `created_at` | Float epoch | |
| `deleted_at` | Float epoch, nullable | **soft-delete**: NULL = ativa |

### `audit_log` (`db/tables.py:807-822`) — trilha append-only
FK a `users` é **lógica** (a trilha sobrevive à exclusão do usuário).

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | Integer PK | |
| `actor_user_id` | Integer, FK lógica→users, nullable | quem fez a ação |
| `actor_type` | Text, default `system` | `system` \| `user` \| `ai` |
| `actor_label` | Text | snapshot do nome no momento |
| `action` | Text | `recurso.verbo` (ex.: `auth.login`, `contact.update`, `agent.update`, `tag.create`, `role.assign`, `config.update`) |
| `resource_type` / `resource_id` | Text | recurso alvo (`resource_id` é str: phone/jid/uuid/id) |
| `before_json` / `after_json` | Text (JSON mascarado) | antes/depois |
| `ip_address` / `request_id` | Text | |
| `created_at` | Float epoch | |

⚠️ **`audit_log` NÃO registra ciclo de vida de conversa**: não há `conversation.create`/`assign`/`close`, e `message.sent`/`message.received` são **excluídos de propósito**. Portanto "qual atendente iniciou/fechou qual atendimento" **não sai do audit_log** — derive de `messages` + `atendimentos.origin`.

---

## 4. Tabelas do plugin `protocolos`

Instalado em `storages/plugins/protocolos/`. **Sem FKs físicas** — os vínculos são por coluna (`contact_id`→`contacts.id`, `conversation_id`→`atendimentos.id`, `assignee_user_id`→`users.id`). Timestamps `DOUBLE PRECISION` epoch; JSON como `TEXT`.

### `plugin_protocolos_protocolos` — a entidade Protocolo (`001_initial.sql:8-21`, `003:31`)
Um protocolo agrupa N atendimentos de UM contato; no máx. 1 aberto por contato (índice parcial `WHERE status='aberto'`). **Para CONTAR protocolos, use esta tabela.**

| Coluna | Tipo | Significado / como usar |
|---|---|---|
| `id` | SERIAL PK | o "número" real e estável do protocolo (inteiro) |
| `contact_id` | Integer, NOT NULL | → `contacts.id` |
| `contact_phone` / `contact_name` | Text (snapshot) | coluna CLIENTE |
| `status` | Text, default `'aberto'` | **`aberto` \| `fechado`** — ciclo próprio, independente do `atendimentos.status` |
| `assignee_user_id` | Integer, nullable | → `users.id` do atendente DONO do protocolo. ⚠️ nullable (sem atendente = só no "geral") |
| `assignee_name` | Text (snapshot) | coluna ATENDENTE (congelado; para nome fiel, join em `users`) |
| `fields` | Text (JSON legado) | extras hoje normalizados em `plugin_protocolos_protocolo_extras` |
| `obs` | Text | observação (migrada para rótulo extra) |
| `opened_at` | Float epoch | **DATA DE ABERTURA** — filtrável por `opened_from`/`opened_to` na API |
| `closed_at` | Float epoch, nullable | **DATA DE FECHAMENTO**; NULL enquanto aberto. ⚠️ **VOLÁTIL**: `reopen` faz `closed_at=NULL`; refechar sobrescreve → só o ÚLTIMO fechamento sobrevive. **Não há filtro por `closed_at` na API** — "fechados no dia" exige SQL direto |
| `created_at` / `updated_at` | Float epoch | |

⚠️ **Atribuição no fechamento**: se já havia atendente no protocolo, ele é **preservado**; só marca "quem finalizou" quando não havia atendente prévio. Então `protocolos.assignee_user_id` é o DONO, não necessariamente quem fechou. Para "quem executou a resolução", use o ciclo (abaixo).

### ⚠️ `plugin_protocolos_atendimentos` — FALSO AMIGO da tabela core `atendimentos` (`001_initial.sql:36-47`, `005:7`)

> **ATENÇÃO — NÃO CONFUNDA.** Apesar do nome, **esta tabela NÃO é a lista de atendimentos do core**. A tabela de atendimentos do core é `atendimentos` (alias `conversations`). Esta é a tabela de **VÍNCULO/CICLO** do plugin: liga uma conversa do core (`conversation_id`) a um protocolo, e carrega os campos de resolução **por-ciclo**.

Diferenças essenciais:

| | core `atendimentos` (= `conversations`) | `plugin_protocolos_atendimentos` |
|---|---|---|
| O que é | a conversa/ticket do core | tabela de vínculo/ciclo do plugin |
| PK | `atendimentos.id` | `id` próprio (id de ciclo) |
| Cardinalidade por conversa | 1 conversa = 1 linha | **N linhas por conversa** — uma por ciclo aberto→resolvido (o UNIQUE em `conversation_id` foi DROPADO em `002:6`) |
| Conteúdo | mensagens, status, canal, IA, assignee do core | metadados do ciclo: `started_at`/`ended_at`, `assignee_*`, `fields`/`obs`; **SEM mensagens** |

Colunas relevantes:

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | SERIAL PK | id do ciclo |
| `protocolo_id` | Integer, NOT NULL | → `plugin_protocolos_protocolos.id` |
| `conversation_id` | Integer, NOT NULL | → **core `atendimentos.id`** (a conversa) — aparece VÁRIAS vezes (uma por ciclo) |
| `contact_id` | Integer, NOT NULL | → `contacts.id` |
| `assignee_user_id` | Integer, nullable | → `users.id` do agente que **RESOLVEU o ciclo** (capturado do `current_user` no `/resolve`). Use este para "quem executou a resolução" |
| `assignee_name` | Text (snapshot) | ATENDENTE do ciclo |
| `started_at` | Float epoch | **INÍCIO** do ciclo (criação) |
| `ended_at` | Float epoch, nullable | **FIM** — gravado ao RESOLVER o atendimento. Fonte para "atendimentos resolvidos por atendente no dia" (mais granular que o protocolo) |
| `fields` / `obs` | Text | extras/observação do ciclo |
| `created_at` / `updated_at` | Float epoch | |

### `plugin_protocolos_avaliacoes` — avaliação por FECHAMENTO (`015_avaliacoes.sql:9-26`)
Nasce **1 linha por fechamento/link enviado** (1:N por protocolo — reabrir+refechar gera nova linha). Serve de **log de fechamentos** (contorna a volatilidade de `closed_at`), **mas só existe se o envio de avaliação/link estiver configurado no fechamento** — pode haver fechamentos sem linha aqui.

| Coluna | Tipo | Significado |
|---|---|---|
| `id` | SERIAL PK | |
| `id_protocol` | Text, UNIQUE | chave pública da URL (número exibido, formato `DDMMYYYY-HHMMSS.mmm-RRRRR`) |
| `protocolo_id` | Integer, NOT NULL | → `plugin_protocolos_protocolos.id` |
| `contact_id` | Integer, NOT NULL | → `contacts.id` |
| `conversation_id` | Integer, nullable | conversa mais recente no fechamento |
| `channel_id` | Text | |
| `assignee_user_id` | Integer, nullable | → `users.id` do atendente (ATENDENTE do fechamento) |
| `assignee_name` / `contact_phone` / `contact_name` | Text (snapshots) | |
| `nota` | Integer, nullable | 1..5; NULL até responder |
| `sugestao` | Text | texto da avaliação |
| `answered_at` | Float epoch, nullable | NULL = pendente. `created_at` ≈ momento do fechamento |
| `answered_ip` | Text | |
| `created_at` / `updated_at` | Float epoch | `created_at` = 1 por fechamento → log fiel de fechamentos |

Demais tabelas do plugin (`plugin_protocolos_campos_extras`, `plugin_protocolos_protocolo_extras`, `plugin_protocolos_kanban_views`, `plugin_protocolos_user_view_prefs`) guardam valores de rótulos extras e config de Kanban — **não relevantes para contagens** de abertos/fechados por atendente.

**Nota sobre "número de protocolo"**: coexistem 3 formas — o `plugin_protocolos_protocolos.id` (inteiro, o real e estável); o número da nota privada de abertura `PROT-AAAAMMDD-HHMMSS-<id>`; e o `id_protocol` público da avaliação `DDMMYYYY-HHMMSS.mmm-RRRRR`. Para juntar/contar, use sempre o `id` inteiro.

---

## 5. Resumo das colunas mortas / voláteis (marque em qualquer análise)

| Coluna | Estado | Consequência |
|---|---|---|
| `atendimentos.waiting_since` | **MORTA** — nunca escrita | Sempre NULL. "Esperando desde" precisa ser derivado de `messages.ts` |
| `atendimentos.team_id` | **MORTA** — sem tabela `teams` nem writer | Rollup por time impossível |
| `atendimentos.resolved_at` | **VOLÁTIL** — apagada no reopen, sobrescrita no re-close | Só o último fechamento; subconta "fechados no dia" |
| `atendimentos.assignee_user_id` | **ZERADO ao fechar** | Conversa fechada não tem assignee; "fechado por atendente" não sai da linha (use `messages(role='conversation_event')` ou o ciclo do protocolo) |
| `atendimentos.active_agent_key` | **ZERADO ao fechar** | — |
| `atendimentos.opened_at` | **NÃO resetado no reopen** | Reaberto hoje mostra `opened_at` original |
| `messages.status` (sent/delivered/read) | **sobrescrito no lugar** | Sem histórico de quando cada estágio ocorreu |
| `plugin_protocolos_protocolos.closed_at` | **VOLÁTIL** — zerada no reopen | Use `plugin_protocolos_avaliacoes.created_at` como log fiel de fechamentos (quando existir) |
| `ai_prompts` / `ai_agents.prompt_key` | **LEGADO** — não lidos na resolução | Ignore para atividade |
