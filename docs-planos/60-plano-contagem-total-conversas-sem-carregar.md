# Plano 60 — Contagem real de conversas por filtro, sem carregar todas

> **Status:** PLANEJAMENTO · **Data:** 2026-07-18 · **Escopo:** médio-grande
> **Origem:** pedido do usuário — na tela principal **Conversas**, ao filtrar (ex.: Status = Todas), as abas mostram "Todas 300" porque `300` é o teto do que foi **carregado**, não o total real. Com 15 mil conversas importadas ele quer ver **"Todas 15.000"** sem baixar as 15 mil linhas. Depois, a mesma ideia na tela **Contatos** (fica para uma onda posterior). **Método:** leitura direta do fluxo (frontend hook → API → repo → engine de filtros), tudo com `arquivo:linha` verificado; nada de memória.
> A causa-raiz é que **os contadores das abas são `.length` de arrays filtrados no cliente** sobre um conjunto carregado com teto (`listConversations({limit:200})` + `getContacts` sem teto), então nunca passam do que foi carregado. A solução é um **`COUNT` no servidor** que respeita o filtro ativo e devolve os totais das abas (`all/mine/unassigned/mentions`) — reusando o engine de filtros injection-safe que já existe (`db/filters`), estendido para cobrir as dimensões que hoje só existem no cliente.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | Prioridade = **tela Conversas** (hub). Contatos vem **depois** ✅ (2026-07-18) | Waves 0–3 = hub; Contatos vira Wave 4, decisão-gated (amarra ao plano 50 F7). |
| D2 | Mostrar o **total real do filtro** sem carregar todas as linhas ✅ (2026-07-18) | `COUNT(*)` no servidor (ignora `limit/offset`); a sidebar continua renderizando só a página carregada (paginação real = plano 50 F8, ortogonal). |
| D3 | Nada em produção pode quebrar ⇒ mudanças **aditivas** e retrocompatíveis | Endpoint novo `/api/atendimentos/count` (não altera o shape de `/api/atendimentos`); `tabCounts` cai no `.length` cliente como fallback quando o count não chegou/falhou. |
| D4 | Reusar o que já existe em vez de inventar padrão novo | Count usa o engine `db/filters` (registry+translate) já usado por `/api/atendimentos/filter`; a construção de `spec`/`where` é fatorada de `_run_filter` ([conversations.py:138](../server/routes/conversations.py#L138)). |
| D5 | Postgres é o único backend | `COUNT(*) FILTER (WHERE …)` (agregação condicional do Postgres) resolve as 4 abas numa query; índices `idx_atend_*` ([tables.py:457-462](../db/tables.py#L457)) cobrem status/assignee/archived. |
| D6 | O número **nunca pode mentir** | Enquanto uma dimensão de filtro só existir no cliente (ex.: `canal`, `tipo de contato`) e ainda não no engine, a aba cai no `.length` cliente (comportamento de hoje) em vez de mostrar um total **errado**. Cada onda que estende o engine amplia a cobertura do count exato. |

---

## 1. Resumo executivo

Na tela **Conversas**, as abas **Minhas / Não atribuídas / Menções / Todas** exibem contadores que são o **tamanho de arrays filtrados no navegador** ([useConversationFilters.js:83-88](../web/static/js/components/contacts/hooks/useConversationFilters.js#L83)), montados a partir de um fetch **com teto** (`listConversations({archived, limit:200})` — [useConversationList.js:91](../web/static/js/components/contacts/hooks/useConversationList.js#L91), capado em 200 no backend — [conversations.py:104](../server/routes/conversations.py#L104)) cruzado com `getContacts` (sem teto). Logo, o maior número possível é ~o tamanho do conjunto carregado — nunca o total do banco. Com 15 mil conversas, o usuário quer ver o total **verdadeiro** por filtro sem baixar tudo.

A solução: um endpoint **`/api/atendimentos/count`** que roda um `COUNT(*)` (com agregação condicional) sobre o mesmo `WHERE` injection-safe do engine `db/filters`, escopado às caixas visíveis, e devolve `{all, mine, unassigned, mentions}` — o mesmo shape do `tabCounts`. O frontend passa a alimentar as abas com esse total real (com fallback ao `.length` cliente). O engine hoje cobre `status/assignee/labels/conv_labels/…` mas **não** cobre dimensões que só existem no filtro cliente (`canal`, `tipo de contato`, `ia`, `iniciador`, `atividade lt`, `agente ai:/none`, `cattr` de contato) — este plano as adiciona ao engine em ondas, priorizando **canal** e **tipo de contato** (usados por esta instância: Exemplo_bot × Avisos Curseduca; WhatsApp × Telegram). A sidebar continua sem paginar (isso é o plano 50 F8) — este plano é **ortogonal** e entrega o número real **agora**; o tradutor filtro→spec que ele cria é justamente o que o plano 50 F8 vai reusar para paginar server-side.

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| Fetch da sidebar (com teto) | [useConversationList.js:82-106](../web/static/js/components/contacts/hooks/useConversationList.js#L82) | `Promise.all([getContacts(q,false), listConversations({archived, limit:200})])` → `buildRows`. **Teto de 200 conversas**; sem paginação/append. |
| Backend capa o limit | [conversations.py:104](../server/routes/conversations.py#L104) | `limit = max(1, min(limit, 200))`. `/api/atendimentos` nunca devolve >200. |
| Merge → linhas | [conversationRows.js:378-449](../web/static/js/services/conversationRows.js#L378) `buildRows` | 1 linha por conversa carregada + linha legada por contato **sem** atendimento. Linhas legadas (de `getContacts`, sem teto) podem **inflar** o conjunto além de 200 (por isso a tela mostra "300", não "200"). |
| ⚠️ Filtros = 100% no cliente | [useConversationFilters.js:59-93](../web/static/js/components/contacts/hooks/useConversationFilters.js#L59) | `activeContacts` → `statusTagFiltered` (status+tags+adv) → `tabCounts` (`.length`) e `displayedContacts` (+assignment). Trocar aba/status/filtro **não** refaz fetch — filtra o array carregado. |
| Contadores das abas | [useConversationFilters.js:83-88](../web/static/js/components/contacts/hooks/useConversationFilters.js#L83) | `{ all: statusTagFiltered.length, mine: …filter(assignee===uid).length, unassigned: …filter(isUnassigned).length, mentions: …filter(has_user_mention).length }`. **Tamanho do array carregado.** |
| Render dos badges | [ContactList.js:464](../web/static/js/components/contacts/ContactList.js#L464) → [ConversationFilterBar.js:475-478](../web/static/js/components/contacts/ConversationFilterBar.js#L475) | `counts.mine/mentions/unassigned/all` viram os números das abas. |
| Predicados cliente | [conversationRows.js:31-48](../web/static/js/services/conversationRows.js#L31) (`matchesStatus`/`matchesAssignment`/`isUnassigned`), [:120-189](../web/static/js/services/conversationRows.js#L120) (`clauseMatches`/`matchesAdvFilters`/`matchesTags`) | Dimensões do cliente: `channel, contact_type, tag, conv_label, agent(user:/ai:/none), status, ai(on/off), starter(customer/operator), activity(gt/lt/days_before), cattr:(contact\|conversation):*`. |
| ⚠️ Engine server-side (já existe) | [db/filters/registry.py:38-56](../db/filters/registry.py#L38) `DIMENSIONS`; [db/filters/translate.py:31-86](../db/filters/translate.py#L31) `build_where` | Dimensões do servidor: `status, archived, assignee, inbox_id, priority, since, display_id, labels(=tags de contato), conv_labels, q` + `cattr:*` (**escopo conversa apenas**). Cada valor vira bind param; allowlist é a fronteira de segurança. |
| Endpoint de filtro (não usado pelo hub) | [conversations.py:123-156](../server/routes/conversations.py#L123) `/api/atendimentos/filter` | Usado **só** pelo plugin atendimentos ([Attendances.js:124](../web/static/js/components/attendances/Attendances.js#L124)). Devolve `{conversations, count: len(rows)}` — ⚠️ `count` = **tamanho da página**, NÃO o total. Nenhum endpoint hoje devolve total real de filtro. |
| Repo: listagem filtrada | [conversation_repo.py:441-458](../db/repositories/conversation_repo.py#L441) `list_filtered(where, …)` | `select(*_enriched_columns).select_from(_enriched_from()).where(where).limit().offset()`. É o molde para o count (mesmo `where`, sem `limit/offset`, `COUNT` no lugar do SELECT enriquecido). |
| Repo: count existente (parcial) | [conversation_repo.py:608-613](../db/repositories/conversation_repo.py#L608) `count(*, status=None)` | `select(func.count()).select_from(conversations)` — só `status`, sem escopo de caixa nem filtro. Ponto de partida a generalizar. |
| Colunas + índices de conversa | [tables.py:433-468](../db/tables.py#L433) | `status, is_archived, assignee_user_id, active_agent_key, ai_active, origin, inbox_id, last_activity_at`. Índices: `idx_atend_inbox_status`, `idx_atend_assignee_status`, `idx_atend_archived`, `idx_atend_last_activity`. **Sem** índice em `active_agent_key` (irrelevante p/ COUNT em 15k). |
| Subqueries do enriquecido | [conversation_query.py:58-72](../db/repositories/conversation_query.py#L58) | `unread_subq` (não-lidas por conversa) e `user_mention_subq` (menção não-lida do usuário → `has_user_mention`). O count de `mentions` reusa `user_mention_subq` num `FILTER`. |

---

## 3. Inventário / análise

### 3.1 — Mapa de dimensões: cliente × servidor (o coração do problema)

| Dim do filtro-cliente | Onde (cliente) | Dim/coluna no servidor | Cobertura hoje | Ação |
|---|---|---|---|---|
| `status` (chip) | [conversationRows.js:31](../web/static/js/services/conversationRows.js#L31) | `status` enum ([registry.py:39](../db/filters/registry.py#L39)) | ✅ total | mapear no tradutor |
| `assignment` aba (mine) | [conversationRows.js:44](../web/static/js/services/conversationRows.js#L44) | `assignee_user_id = uid` (coluna) | ✅ total | FILTER no count |
| `assignment` aba (unassigned) | [conversationRows.js:45](../web/static/js/services/conversationRows.js#L45) `isUnassigned` = sem assignee **e** sem `active_agent_key` | colunas `assignee_user_id IS NULL AND active_agent_key IS NULL` | ✅ total | FILTER no count |
| `tag` (funil + adv) = etiquetas do **contato** | [conversationRows.js:67](../web/static/js/services/conversationRows.js#L67), `matchesTags` [:185](../web/static/js/services/conversationRows.js#L185) | `labels` ([registry.py:52](../db/filters/registry.py#L52), via `contact_tags⋈tags`) | ✅ total | mapear `tag`→`labels` |
| `conv_label` = etiquetas do **atendimento** | [conversationRows.js:68](../web/static/js/services/conversationRows.js#L68) | `conv_labels` ([registry.py:53](../db/filters/registry.py#L53)) | ✅ total | mapear `conv_label`→`conv_labels` |
| `cattr:conversation:*` | [conversationRows.js:126](../web/static/js/services/conversationRows.js#L126) | `cattr:*` (escopo conversa — [translate.py:185](../db/filters/translate.py#L185)) | ✅ total | mapear |
| `agent` `user:<id>` | [conversationRows.js:71](../web/static/js/services/conversationRows.js#L71) | `assignee equal_to <id>` | ✅ parcial | mapear |
| `agent` `ai:<key>` / `none` | [conversationRows.js:70-72](../web/static/js/services/conversationRows.js#L70) | — (`assignee` não vê `active_agent_key`) | ❌ falta | Wave 3: estender `assignee`/nova dim `agent` |
| `activity lt`/`days_before` (mais recente que / há exatamente N dias) | [conversationRows.js:154-163](../web/static/js/services/conversationRows.js#L154) | `since greater_than` = "ativa desde" (≈ `lt`) | ⚠️ parcial (só um sentido) | Wave 3: dim `activity` completa |
| `channel` (canal do atendimento) | [conversationRows.js:65](../web/static/js/services/conversationRows.js#L65) | — | ❌ falta | **Wave 2** (alta prioridade) |
| `contact_type` (whatsapp/telegram/…) | [conversationRows.js:66](../web/static/js/services/conversationRows.js#L66) | — | ❌ falta | **Wave 2** (alta prioridade) |
| `ai` (on/off por conversa) | [conversationRows.js:141-146](../web/static/js/services/conversationRows.js#L141) | — (`ai_active` não é dim) | ❌ falta | Wave 3 |
| `starter` (customer/operator via `origin`) | [conversationRows.js:147-153](../web/static/js/services/conversationRows.js#L147) | — (`origin` não é dim) | ❌ falta | Wave 3 |
| `cattr:contact:*` | [conversationRows.js:126](../web/static/js/services/conversationRows.js#L126) | — (server cattr só conversa) | ❌ falta | Wave 3 |
| busca (caixa de texto) | [useConversationList.js:90](../web/static/js/components/contacts/hooks/useConversationList.js#L90) `getContacts(q)` | `q` (nome/telefone — [translate.py:165](../db/filters/translate.py#L165)) | ⚠️ parcial (server `q` só nome/telefone; busca-cliente também casa conteúdo de mensagem) | mapear `q`; documentar gap (P4) |

### 3.2 — Itens a construir

| # | Item | Ponto de mudança (`arquivo:linha`) | Abordagem | Risco | Esforço |
|---|------|-----------------------------------|-----------|-------|---------|
| I0 | Repo `count_tab_counts(where, …)` | **novo** em [conversation_repo.py](../db/repositories/conversation_repo.py) (perto de `list_filtered` [:441](../db/repositories/conversation_repo.py#L441)) | `SELECT count(*) AS all, count(*) FILTER(WHERE assignee=:uid) AS mine, count(*) FILTER(WHERE assignee IS NULL AND active_agent_key IS NULL) AS unassigned, count(*) FILTER(WHERE <user_mention_subq>) AS mentions FROM _enriched_from() WHERE <where> AND <inbox scope>`. `where=None` ⇒ total geral. | Médio | M |
| I1 | Endpoint `/api/atendimentos/count` (GET+POST) | **novo** em [conversations.py](../server/routes/conversations.py#L123) (ao lado de `filter_get`/`filter_post`) | Fatorar de `_run_filter` ([:138](../server/routes/conversations.py#L138)) um helper `_spec_and_where(request, payload, params)`; o count o reusa e chama I0. Gate `conversation.read`. | Baixo | S |
| I2 | Tradutor puro filtro-cliente → params do count | **novo** `web/static/js/services/conversationFilterSpec.js` (ou dentro de [conversationRows.js](../web/static/js/services/conversationRows.js)) | `buildCountParams({search, statusFilter, tagFilter, advFilters})` → `{status?, labels?, conv_labels?, q?, 'cattr:…'?, since?, assignee? …}` (só dims cobertas). `isServerExpressible(search, statusFilter, tagFilter, advFilters)` → bool (D6). `node --test`. | Médio | M |
| I3 | Wire no hook de filtros | [useConversationFilters.js:83-88](../web/static/js/components/contacts/hooks/useConversationFilters.js#L83) | Fetch debounced do count quando `isServerExpressible`; `serverCounts` state; `tabCounts` = `serverCounts ?? {…client .length}`. Fallback cliente durante loading/erro/dim-não-coberta. | Médio | M |
| I4 | (Opcional) "mostrando X de Y" | [ConversationFilterBar.js](../web/static/js/components/contacts/ConversationFilterBar.js) ou header da lista | Linha discreta "200 de 15.000 carregadas" quando total>carregadas — prepara o terreno do plano 50 F8. `wa-*`. | Baixo | S |
| I5 | Dim `channel` no engine | [registry.py:56](../db/filters/registry.py#L56) + [translate.py:60-86](../db/filters/translate.py#L60) | `Dim("channel","channel",{in,equal_to})`; clause = `inbox_id IN (select id from inboxes where channel_id IN :vals)`. | Baixo | S |
| I6 | Dim `contact_type` no engine | idem | `Dim("contact_type","enum-ish",{in,equal_to})`; clause = `contact_id IN (select id from contacts where contact_type IN :vals)`. | Baixo | S |
| I7 | Dims `ai`, `starter`, `activity` completa, `agent`(ai/none), `cattr` de contato | idem + [translate.py](../db/filters/translate.py) | `ai`→`ai_active`; `starter`→`origin`; `activity`→`last_activity_at` `<`/`>`/faixa; `agent`→`active_agent_key`/`assignee`/none; cattr-contact → join `contacts.custom_attributes`. | Médio | M |
| I8 | (Wave 4) count da tela **Contatos** | [ContactsListScreen.js:668](../web/static/js/components/ContactsListScreen.js#L668) + `contact_repo` | Amarrar ao plano 50 F7 (server-side). Decisão P5. | Médio | M |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|----------|-------------------------|
| Migrar a filtragem do hub inteira para server-side agora | É o plano 50 F8 (sidebar paginada), escopo maior e de risco alto. Este plano só precisa do **número** — filtragem cliente continua, count vem à parte. O tradutor (I2) é o ponto comum reusável. |
| Reusar o `count: len(rows)` de `/api/atendimentos/filter` ([conversations.py:156](../server/routes/conversations.py#L156)) | É o tamanho da **página** (≤limit), não o total. Enganoso — não serve. |
| `conversation_repo.count(status=…)` ([:608](../db/repositories/conversation_repo.py#L608)) resolve | Só cobre `status`, sem escopo de caixa nem filtro/assignment. Serve de esqueleto, não de solução. |
| Contar no cliente pedindo `limit` gigante | Viola D2 (baixar tudo) e o plano 50 (teto de leitura). O ponto é **não** carregar as 15k. |
| Índice novo em `active_agent_key` para o FILTER "unassigned" | `COUNT` com FILTER sobre ~15k linhas é trivial (<10ms) mesmo em seq scan parcial; os índices `idx_atend_*` já cobrem o grosso. Reabrir só se a base passar de centenas de milhares (P3). |
| Tela Contatos junto (mesmo plano) | D1: Contatos é "depois". Além disso a tela Contatos é 100% cliente ([ContactsListScreen.js:486-508](../web/static/js/components/ContactsListScreen.js#L486)) e o plano 50 F7 já prevê torná-la server-side — o count dela deve nascer junto com essa paginação. Vira Wave 4 decisão-gated. |

---

## 4. Contrato fixo (frontend e backend paralelizam contra este)

**4.1 — Endpoint de contagem (mesma gramática de `/api/atendimentos/filter`):**
```
GET  /api/atendimentos/count?status=open&labels=vip,lead&channel=Exemplo_bot&...
POST /api/atendimentos/count   { match?, filters:[{attribute_key, filter_operator, values}] }

200 { ok, data: { all: N, mine: N, unassigned: N, mentions: N } }
```
- Mesmos params/payload que `/api/atendimentos/filter` (reusa `db.filters.from_params`/`from_payload` + `build_where` + `_filter_context` + `visible_inbox_ids`). **`limit`/`offset` são ignorados** (é contagem total).
- `all` = total que casa o `WHERE`; `mine`/`unassigned`/`mentions` = partições por assignment/menção **dentro** do mesmo `WHERE` (agregação condicional). `mine`/`mentions` = `0` sem usuário autenticado.
- O `WHERE` do count **não** inclui o eixo de assignment (as abas particionam por ele) — igual ao `statusTagFiltered` do cliente ([useConversationFilters.js:71-81](../web/static/js/components/contacts/hooks/useConversationFilters.js#L71)).

**4.2 — Tradutor puro (I2), contrato de saída:**
```js
buildCountParams({ search, statusFilter, tagFilter, advFilters })
// → { status?, labels?: string[], conv_labels?: string[], q?, since?, 'cattr:<k>'?, channel?, contact_type?, ... }
isServerExpressible({ search, statusFilter, tagFilter, advFilters }) // → bool
```
- `statusFilter='all'` ⇒ sem `status`. `tagFilter` ⇒ `labels`. Cláusulas `adv` cobertas ⇒ params correspondentes. Dimensão **não** coberta pela onda atual ⇒ `isServerExpressible=false` (D6, cai no `.length` cliente).

---

## 5. Fases / Roadmap

```
WAVE 0  F0(repo count) ─▶ F1(endpoint /count)                 ← 🔴 base do backend
           │
   (F2 pode começar em paralelo contra o contrato §4:)
WAVE 1  F2(tradutor puro) ──▶ F3(wire hook) · F4(mostrando X de Y)   ← MVP: total real p/ filtros cobertos
                                    │ (barreira: F3 precisa de F1+F2)
WAVE 2  F5(dim channel) · F6(dim contact_type)                ← 🟢 engine, independentes entre si
WAVE 3  F7(ai · starter · activity · agent · cattr-contato)   ← paridade total; remove o fallback D6
WAVE 4  F8(tela Contatos)                                     ← decisão-gated (amarra plano 50 F7)
```

> **Paralelização:** F2 (tradutor puro, testável com `node --test`) pode ser escrito **em paralelo** a F0/F1, pois só depende do contrato §4. F5 e F6 são adições **isoladas** de allowlist no engine (não colidem) → 🟢 juntas após F0. Cada dim nova no engine (F5/F6/F7) ganha uma linha no tradutor (F2) — trocas pequenas e localizadas.

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / Sub-plano |
|------|------|-----------|-------|-------|---------------------------|
| 0 | F0 | Repo `count_tab_counts` | 🔴 [bloqueia: F1] | Médio | count por FILTER bate com `list_filtered` len em base de teste |
| 0 | F1 | Endpoint `/api/atendimentos/count` | 🔴 [depende: F0] | Baixo | `curl …/count?status=open` devolve `{all,mine,unassigned,mentions}` |
| 1 | F2 | Tradutor puro + `isServerExpressible` | 🟢 [depende: contrato §4] | Médio | `node --test` do módulo verde |
| 1 | F3 | Wire `useConversationFilters` | 🔴 [depende: F1,F2] | Médio | Abrir hub mostra total real nas abas; filtro cobertos atualizam o número |
| 1 | F4 | "mostrando X de Y" (opcional) | 🟢 [depende: F3] | Baixo | Linha aparece quando total>carregadas; some quando igual |
| 2 | F5 | Dim `channel` | 🟢 [depende: F0] | Baixo | Filtrar por canal dá total exato daquele canal |
| 2 | F6 | Dim `contact_type` | 🟢 [depende: F0] | Baixo | Filtrar por tipo (WhatsApp/Telegram) dá total exato |
| 3 | F7 | `ai`/`starter`/`activity`/`agent`/cattr-contato | 🟢 [depende: F0] | Médio | Toda combinação de filtro do hub é `isServerExpressible` |
| 4 | F8 | Tela Contatos | 🟢 [depende: P5] | Médio | Total real de contatos por filtro (via plano 50 F7) |

**Disciplina (regras do repo):** verde a cada fase; **um refactor por commit**; nunca avançar com teste vermelho não explicado; toda dim nova no engine passa pela allowlist ([registry.py](../db/filters/registry.py)) — é a fronteira de segurança.

---

### Fase 0 — Repo: `count_tab_counts` 🔴 [bloqueia: F1]
**Objetivo:** uma função de repo que devolve os 4 totais das abas a partir de um `WHERE` já construído (injection-safe), respeitando o escopo de caixas.
**Itens:**
1. `[sequencial]` Adicionar `count_tab_counts(where, *, inbox_ids=None, current_user_id=None) -> dict` perto de [conversation_repo.py:441](../db/repositories/conversation_repo.py#L441). Uma query: `select(func.count().label('all'), func.count().filter(assignee==uid).label('mine'), func.count().filter(and_(assignee.is_(None), active_agent_key.is_(None))).label('unassigned'), func.count().filter(<user_mention exists>).label('mentions')).select_from(_enriched_from())`; aplicar `where` (se não-None) e o escopo `inbox_id.in_(inbox_ids)` idêntico a `list_filtered` ([:450-452](../db/repositories/conversation_repo.py#L450)). `current_user_id is None` ⇒ `mine=mentions=0` (não montar o subquery de menção).
2. `[sequencial]` Reusar o `user_mention_subq` de [conversation_query.py:64-70](../db/repositories/conversation_query.py#L64) para o FILTER de `mentions` (expor um helper ou replicar o `exists()`).
3. `[paralelo]` ⚠️ `func.count().filter(...)` do SQLAlchemy Core gera `COUNT(*) FILTER (WHERE …)` (Postgres) — confirmar o SQL no log em base de teste (D5).

**Pronto quando:** numa base de teste com conversas conhecidas, `count_tab_counts(None)['all'] == count(*)` da tabela (não-arquivadas via `where`/escopo), e `mine+unassigned+outros == all`; com um `where` de status, bate com `len(list_filtered(where, limit=10_000))`.

#### Status de execução — Fase 0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas + porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado / ficou para depois)_
- **Verificação:** _(testes rodados + resultado; validação manual)_

---

### Fase 1 — Endpoint `/api/atendimentos/count` 🔴 [depende: F0]
**Objetivo:** expor o total por filtro via HTTP, reusando a construção de spec/where que `/filter` já faz.
**Itens:**
1. `[sequencial]` Em [conversations.py:138](../server/routes/conversations.py#L138), extrair de `_run_filter` um helper `async def _spec_and_where(request, payload, *, params) -> (spec, where)` (a parte de `from_params/from_payload` + `_filter_context` + `build_where`, com o mesmo tratamento de `FilterError`→400). `_run_filter` passa a chamá-lo (refactor sem mudança de comportamento — um commit).
2. `[sequencial]` Adicionar `GET /api/atendimentos/count` e `POST /api/atendimentos/count` (espelham `filter_get`/`filter_post` [:123-136](../server/routes/conversations.py#L123)), gate `conversation.read`; chamam `_spec_and_where` e `conversation_repo.count_tab_counts(where, inbox_ids=visible_inbox_ids(request), current_user_id=…)`; devolvem `_ok({all, mine, unassigned, mentions})`.
3. `[paralelo]` `api.js`: `countConversations(params)` (molde de `filterConversations` [:468](../web/static/js/services/api.js#L468)).
4. `[paralelo]` Teste de endpoint em [tests/test_endpoints.py](../tests/test_endpoints.py): inserir N conversas (mix de status/assignee), checar `count` bate; `?limit=1` **não** afeta o total; filtro inválido → 400.

**Pronto quando:** `curl /api/atendimentos/count` sem filtro devolve o total real; com `status=open` devolve só as abertas; escopo de caixa respeitado (usuário sem caixa vê 0); suíte verde no Postgres.

#### Status de execução — Fase 1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 2 — Tradutor puro filtro-cliente → params do count 🟢 [depende: contrato §4]
**Objetivo:** converter o estado de filtro do hub em params do `/count`, e saber quando o filtro é 100% expressável no servidor (senão, cai no cliente — D6).
**Itens:**
1. `[sequencial]` Criar `web/static/js/services/conversationFilterSpec.js` (PURO, sem preact/DOM/rede) com `buildCountParams({search, statusFilter, tagFilter, advFilters})` e `isServerExpressible(...)`. Mapear as dims **cobertas na onda atual**: `statusFilter`(≠all)→`status`; `tagFilter`→`labels`; adv `tag`→`labels`, `conv_label`→`conv_labels`, `status`→`status`, `agent user:`→`assignee`, `cattr:conversation:`→`cattr:`, `activity`(sentido coberto)→`since`; `search`→`q`. Dims fora da onda ⇒ `isServerExpressible=false`.
2. `[sequencial]` `node --test` cobrindo: filtro vazio (status=open default) → `{status:'open'}` e `isServerExpressible=true`; multi-select de tags → `labels` OR; uma cláusula `channel` (antes da Wave 2) → `isServerExpressible=false`.
3. `[paralelo]` Reusar/alinhar com `normalizeSpec` ([conversationRows.js:206](../web/static/js/services/conversationRows.js#L206)) para não duplicar a semântica de multi-select.

**Pronto quando:** `node --test` do módulo verde; `isServerExpressible` retorna `false` para toda dim ainda não implementada no engine (nunca produz número errado).

#### Status de execução — Fase 2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 3 — Wire dos contadores reais no hook 🔴 [depende: F1, F2]
**Objetivo:** as abas mostram o total real do banco para o filtro ativo, com fallback seguro.
**Itens:**
1. `[sequencial]` Em [useConversationFilters.js:83-88](../web/static/js/components/contacts/hooks/useConversationFilters.js#L83): novo state `serverCounts` (null inicialmente). `useEffect` (debounce ~300ms) sobre `[search, statusFilter, tagFilter, advFilters, showArchived]`: se `isServerExpressible(...)`, `countConversations(buildCountParams(...) + {archived})` → set `serverCounts`; senão `setServerCounts(null)`. Cancelar request obsoleta (guarda `alive`, igual [useConversationList.js:67-80](../web/static/js/components/contacts/hooks/useConversationList.js#L67)).
2. `[sequencial]` `tabCounts` = `serverCounts ?? { all: statusTagFiltered.length, … }` (fallback cliente — mantém o comportamento atual quando o count não chegou/não se aplica). `ConversationFilterBar` **não muda** (já consome `counts.*`).
3. `[paralelo]` Invalidação: quando um WS `conversation_upsert`/status/assign muda a lista, o effect já reroda (deps incluem os filtros; adicionar um "nonce" que o WS incrementa se necessário para forçar refetch do total após mudanças). Best-effort — total é ~estático entre importações.
4. `[paralelo]` `archived` (view arquivadas) precisa entrar no filtro do count (o cliente hoje separa via fetch; no count, mandar `archived=true/false`). Confirmar que o engine trata `archived` (dim `bool` — [translate.py:67-69](../db/filters/translate.py#L67)).

**Pronto quando:** abrir o hub com 15k conversas mostra "Todas 15.000" (e os splits corretos) sem baixar as linhas; trocar Status=Abertas atualiza para o total de abertas; ativar um filtro ainda-não-coberto cai no número cliente sem erro; a sidebar segue mostrando só a página carregada.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 4 — "mostrando X de Y carregadas" (opcional) 🟢 [depende: F3]
**Objetivo:** deixar explícito que a lista está paginada quando o total supera o carregado — prepara o plano 50 F8.
**Itens:**
1. `[paralelo]` Linha discreta (ex.: no rodapé/topo da sidebar ou ao lado das abas) "200 de 15.000" quando `serverCounts.all > displayedContacts.length`. Classes `wa-*`/`text-wa-secondary` (modo escuro).

**Pronto quando:** a linha aparece só quando há mais no banco do que carregado; some quando iguais; legível no modo escuro.

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 5 — Dim `channel` no engine 🟢 [depende: F0]
**Objetivo:** contar por canal (Exemplo_bot × Avisos Curseduca) com total exato.
**Itens:**
1. `[sequencial]` [registry.py:56](../db/filters/registry.py#L56): `"channel": Dim("channel","channel",{equal_to,in},"Canal")`.
2. `[sequencial]` [translate.py:60-86](../db/filters/translate.py#L60): novo `kind == "channel"` → `conversations.c.inbox_id.in_(select(inboxes.c.id).where(inboxes.c.channel_id.in_(values)))` (valores = ids textuais de canal, iguais aos usados no filtro-cliente [conversationRows.js:65](../web/static/js/services/conversationRows.js#L65) e no `channelOptions` de [useConversationList.js:66-80](../web/static/js/components/contacts/hooks/useConversationList.js#L66)).
3. `[paralelo]` I2: mapear adv `channel`→`channel`; remover `channel` da lista de "não expressável".
4. `[paralelo]` Teste: `?channel=<id>` conta só as conversas daquele canal.

**Pronto quando:** filtrar por um canal dá o total exato dele; multi-select = soma dos canais; `node --test` do tradutor cobre `channel`.

#### Status de execução — Fase 5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 6 — Dim `contact_type` no engine 🟢 [depende: F0]
**Objetivo:** contar por tipo de contato (whatsapp/telegram/outros) com total exato.
**Itens:**
1. `[sequencial]` [registry.py:56](../db/filters/registry.py#L56): `"contact_type": Dim("contact_type","contact_type",{equal_to,in},"Tipo de contato")`.
2. `[sequencial]` [translate.py](../db/filters/translate.py): `kind == "contact_type"` → `conversations.c.contact_id.in_(select(contacts.c.id).where(contacts.c.contact_type.in_(values)))` (coluna `contacts.contact_type`, migration 0050).
3. `[paralelo]` I2 mapeia `contact_type`; teste dedicado.

**Pronto quando:** filtrar por tipo dá total exato; multi-select soma; tradutor cobre `contact_type`.

#### Status de execução — Fase 6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 7 — Paridade total: `ai` · `starter` · `activity` · `agent` · cattr-contato 🟢 [depende: F0]
**Objetivo:** toda dimensão do filtro-cliente é expressável no servidor ⇒ `isServerExpressible` sempre `true`; o número real cobre 100% dos filtros.
**Itens:**
1. `[paralelo]` `ai`: `Dim("ai","ai",{equal_to})` → `conversations.c.ai_active == (1 if 'on' else 0)`.
2. `[paralelo]` `starter`: `Dim("starter","starter",{equal_to})` → `origin == 'inbound'` (customer) / `!=` (operator). Cuidar de `NULL` (legado = operator, igual [conversationRows.js:150](../web/static/js/services/conversationRows.js#L150)).
3. `[paralelo]` `activity` completa: cobrir `lt`(mais recente que) / `gt`(mais antigo que) / `days_before`(faixa do dia) sobre `last_activity_at`, alinhado a [conversationRows.js:154-163](../web/static/js/services/conversationRows.js#L154) — ⚠️ o `since` atual só faz `>`; adicionar a nova dim `activity` (não remover `since`, usado pelo plugin atendimentos).
4. `[paralelo]` `agent` `ai:<key>`/`none`: estender o mapeamento — `ai:` → `active_agent_key == key`; `none` → `assignee IS NULL AND active_agent_key IS NULL` (= `isUnassigned`).
5. `[paralelo]` cattr de **contato**: `cattr:contact:<k>` → join em `contacts.custom_attributes` (hoje [translate.py:185](../db/filters/translate.py#L185) só usa `conversations.custom_attributes`). Distinguir escopo pelo prefixo.
6. `[paralelo]` I2: remover **todas** as dims da lista de não-expressável; `isServerExpressible` volta `true` para qualquer combinação; simplificar o fallback (só loading/erro).

**Pronto quando:** qualquer filtro montado no diálogo avançado produz um total real; `node --test` cobre cada dim; nenhum filtro cai mais no `.length` cliente (exceto durante o loading do request).

#### Status de execução — Fase 7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 8 — Tela Contatos (Wave 4, decisão-gated) 🟢 [depende: P5]
**Objetivo:** a tela **Contatos** mostra o total real por filtro sem carregar todos os contatos.
**Itens:**
1. `[sequencial]` Decidir P5 (amarrar ao plano 50 F7, que torna `/api/contacts` server-side com `limit/offset`). Com o endpoint paginado, expor `total`/`has_more` e trocar "Exibindo X - Y de Z" ([ContactsListScreen.js:668](../web/static/js/components/ContactsListScreen.js#L668), hoje `Z = filtered.length` cliente) pelo `total` do servidor.
2. `[sequencial]` `contact_repo.count(...)` respeitando busca/filtro (ou reusar o count que o endpoint paginado do plano 50 F7 já devolver).

**Pronto quando:** a tela Contatos mostra o total real do filtro; DOM ≤ uma página; sem `slice` cliente (converge com plano 50 F7).

#### Status de execução — Fase 8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| `COUNT(*) FILTER` no Core | SQLAlchemy pode não emitir `FILTER` como esperado no Postgres | Confirmar SQL gerado no log (F0 item 3); fallback = 4 subqueries `scalar_subquery` numa só linha SELECT. |
| Duas fontes de verdade de filtro | Cliente e servidor divergem em semântica (ex.: `activity gt/lt`, `q` casando conteúdo) → número não bate com a lista | D6: `isServerExpressible` só habilita o total quando a dim é fielmente traduzível; enquanto não for, cai no cliente. Waves 5–7 fecham o gap. Documentar o `q` (P4). |
| Base do `where` × assignment | Incluir assignment no `where` do count zeraria as outras abas | O `where` do count **exclui** o eixo assignment; as abas vêm dos `FILTER` (§4.1) — espelha `statusTagFiltered` ([useConversationFilters.js:71-81](../web/static/js/components/contacts/hooks/useConversationFilters.js#L71)). |
| Escopo de caixas | Count sem `inbox_ids` vazaria conversas de caixas ocultas | Passar `visible_inbox_ids(request)` idêntico a `list_filtered` ([:450-452](../db/repositories/conversation_repo.py#L450)); usuário sem caixa ⇒ 0. |
| Arquivadas | Esquecer `archived` no count conta a caixa errada | F3 item 4: mandar `archived` no filtro; dim `bool` já existe ([translate.py:67](../db/filters/translate.py#L67)). |
| Perf em base grande | COUNT frequente a cada tecla de filtro | Debounce ~300ms (F3); COUNT indexado é barato (<10ms em 15k); só refaz quando o filtro muda. |
| Allowlist de segurança | Dim nova mal-adicionada abre injeção | Toda dim passa por `registry.DIMENSIONS` + `translate` com bind params — mesma fronteira validada do plano 08; nunca interpolar valor. |
| Postgres | Suíte precisa do banco de teste | `WHATSBOT_TEST_DB_URL` com `test` no nome (trava); `DROP SCHEMA` por processo. |
| Modo escuro | Linha "X de Y" (F4) ilegível | `wa-*`/`text-wa-secondary`; testar com `.dark`. |
| Regressão de `/filter` | Fatorar `_spec_and_where` pode mudar comportamento do plugin atendimentos | Refactor em commit isolado, com a suíte de `/filter` verde antes/depois; `Attendances.js` intocado. |

---

## 7. Perguntas em aberto

- **P1 — Endpoint dedicado `/count` vs `total` embutido em `/filter`.** ✅ DECIDIDO (2026-07-18): **endpoint dedicado** `/api/atendimentos/count`. Motivo: o hub precisa dos **4 totais de aba** (all/mine/unassigned/mentions) numa chamada, e o `/filter` devolve linhas (payload pesado) — separar mantém o count leve e cacheável. Alternativa (b) — adicionar `total` ao `/filter` — fica como bônus se o plugin atendimentos quiser paginação real depois.
- **P2 — Como apresentar quando a dim não é expressável (Waves 1–2).** ✅ DECIDIDO (2026-07-18): **fallback ao `.length` cliente** (D6) — nunca mostrar total errado. Conforme as ondas estendem o engine, mais filtros ganham o total real. Sem badge de "aproximado".
- **P3 — Índice para o FILTER "unassigned".** ⏸️ ADIADO (default: NÃO). `COUNT` em 15k é trivial. Reabrir só se a base passar de ~centenas de milhares (aí índice parcial `WHERE assignee_user_id IS NULL AND active_agent_key IS NULL`).
- **P4 — Busca (`q`): nome/telefone vs conteúdo de mensagem.** ⏸️ ADIADO (default: `q`=nome/telefone). O `q` do servidor ([translate.py:165](../db/filters/translate.py#L165)) casa nome/telefone; a busca-cliente também casa conteúdo de mensagem (full-scan — plano 50 F6). Com busca ativa, o total pode subestimar matches por conteúdo. Opções: (a) aceitar (nome/telefone cobre a maioria) + nota; (b) esperar o FTS do plano 50 P2 e então casar. **Recomendação:** (a) agora; a busca por conteúdo vira `isServerExpressible=false` se precisar de exatidão.
- **P5 — Tela Contatos: agora ou junto do plano 50 F7.** ⏸️ ADIADO (decidir no início da Wave 4). Contexto: a tela é 100% cliente ([ContactsListScreen.js:486-508](../web/static/js/components/ContactsListScreen.js#L486)) e o plano 50 F7 já prevê server-side. Opções: (a) fazer o count de Contatos **dentro** da Wave 4 deste plano reusando o endpoint paginado do plano 50 F7; (b) deixar o count de Contatos como item do próprio plano 50 F7. **Recomendação:** (a) se o plano 50 F7 ainda não rodou; (b) se já rodou (o `total` já existirá).
- **P6 — Invalidação do total ao vivo.** ⏸️ ADIADO (default: refetch por mudança de filtro + nonce WS best-effort). O total muda devagar (importações em lote), então não precisa ser real-time; F3 item 3 cobre o suficiente.

---

## 8. Apêndice — arquivos-chave

**Backend — count core + endpoint (Wave 0)**
- [db/repositories/conversation_repo.py:441](../db/repositories/conversation_repo.py#L441) (`list_filtered`, molde), [:608](../db/repositories/conversation_repo.py#L608) (`count`, esqueleto) — **novo** `count_tab_counts`.
- [db/repositories/conversation_query.py:58-72](../db/repositories/conversation_query.py#L58) — `unread_subq`/`user_mention_subq` reusados no FILTER de `mentions`.
- [server/routes/conversations.py:123-156](../server/routes/conversations.py#L123) — fatorar `_spec_and_where`; **novo** `GET/POST /api/atendimentos/count`.

**Backend — engine de filtros (Waves 2–3)**
- [db/filters/registry.py:38-56](../db/filters/registry.py#L38) — `DIMENSIONS`: adicionar `channel`, `contact_type`, `ai`, `starter`, `activity`, `agent`.
- [db/filters/translate.py:47-86](../db/filters/translate.py#L47) — `_build_clause`: novos `kind`s + cattr de contato ([:175-186](../db/filters/translate.py#L175)).

**Frontend**
- [web/static/js/services/conversationFilterSpec.js](../web/static/js/services/conversationFilterSpec.js) — **novo** (tradutor puro + `isServerExpressible`), com `node --test`.
- [web/static/js/services/conversationRows.js:31-189](../web/static/js/services/conversationRows.js#L31),[:206](../web/static/js/services/conversationRows.js#L206) — semântica-fonte das dims (referência do tradutor).
- [web/static/js/services/api.js:468](../web/static/js/services/api.js#L468) — **novo** `countConversations`.
- [web/static/js/components/contacts/hooks/useConversationFilters.js:83-93](../web/static/js/components/contacts/hooks/useConversationFilters.js#L83) — `serverCounts` + wire.
- [web/static/js/components/contacts/ConversationFilterBar.js:475-478](../web/static/js/components/contacts/ConversationFilterBar.js#L475) — consumidor (não muda) dos badges.
- [web/static/js/components/ContactsListScreen.js:668](../web/static/js/components/ContactsListScreen.js#L668) — Wave 4 (total real).

**Testes**
- [tests/test_endpoints.py](../tests/test_endpoints.py) — `/api/atendimentos/count` (total, escopo de caixa, `limit` ignorado, filtro inválido→400) + dims novas.
- `web/static/js/services/conversationFilterSpec.test.js` — **novo** (`node --test`).

---

## 9. Checklist de verificação

- [ ] `GET/POST /api/atendimentos/count` devolve `{all,mine,unassigned,mentions}`; `all == count(*)` do filtro; `?limit=1` **não** altera o total.
- [ ] Escopo de caixa respeitado (usuário sem caixa → 0); gate `conversation.read`.
- [ ] Hub com base grande mostra o **total real** nas abas sem carregar todas as linhas; a sidebar segue com a página carregada.
- [ ] Trocar Status/aba/etiqueta atualiza o total; filtro ainda-não-coberto cai no `.length` cliente **sem** número errado (D6).
- [ ] `channel` e `contact_type` (Wave 2) dão total exato; multi-select soma.
- [ ] (Wave 3) toda combinação do diálogo avançado é `isServerExpressible` → total real.
- [ ] `venv/bin/python -m pytest tests/test_endpoints.py -q` **verde** no Postgres (`WHATSBOT_TEST_DB_URL`).
- [ ] `node --test` verde no `conversationFilterSpec.js` (e módulos puros tocados).
- [ ] Refactor `_spec_and_where` não regride `/api/atendimentos/filter` (plugin atendimentos ok).
- [ ] Linha "X de Y" (se F4) legível no **modo escuro**; nenhum segredo em URL; reload/voltar-avançar não quebra o count.
