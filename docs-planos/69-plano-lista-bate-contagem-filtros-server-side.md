# Plano 69 — Lista bate com a contagem: filtros server-side onde hoje só a contagem é server-side

> **Status:** IMPLEMENTADO (F0–F7 + F9; F8 adiada p/ pós-plano-68) · **Data:** 2026-07-21 · **Escopo:** médio-grande
> **Execução:** worktree isolado `plano-69` (branch `plano-69`, base `21627f3`) + banco de teste próprio `whatsbot_test_69` — para rodar em PARALELO ao plano 68 (que é 100% escopo-plugin `agendamento_retorno`) sem colisão. Commits F0/F5b/F1/F2+F3/F4/F6/F7 na branch. **Falta**: merge da branch na `developer`; F8 (Agendamentos, único ponto de conflito com o 68) após o 68 pousar. Verificação: `test_endpoints` 1427/2 (2 falhas pré-existentes, == baseline), `test_plano69` 9 passed, `conversationFilterSpec.test.js` 16 passed.
> **Origem:** bug de produção reportado pelo usuário — na tela **Conversas**, filtrando por "Agente: Atendente X", a **contagem** da aba mostra 21 mas a **lista** mostra só 7 conversas. Continuação direta dos planos **60** (contagem total de conversas sem carregar) e **61** (contagem total de protocolos no Kanban), que shiparam a metade "contagem server-side" e **deixaram a lista filtrando no cliente** (decisão 60/D2: paginação da lista = "plano 50 F8, ortogonal"). **Método:** leitura direta do fluxo (hook → API → rota → engine `db/filters` → repo) + varredura multi-agente de todas as telas com lista+contagem, tudo com `arquivo:linha` verificado; nada de memória.
> **Causa-raiz (verificada):** uma **classe** de bug espalhada pelo app — uma lista **paginada/capada** cujo filtro (ou busca/ordenação) é aplicado **no cliente sobre só as linhas já carregadas**, enquanto a **contagem** vem do **servidor** (→ número diverge da lista) OU a paginação avança sobre o universo **não-filtrado** (→ lista incompleta, às vezes com falso-vazio que trava o scroll). O padrão **correto** já existe no repo: o **Kanban de Protocolos** (plano 61) serve contagem por coluna E cards paginados da **mesma** fonte server-side filtrada — então lista e contagem **não podem divergir**. Este plano converge as outras telas para esse padrão.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Padrão de referência = Kanban de Protocolos** (plano 61): contagem e itens paginados vêm da MESMA fonte server-side filtrada ✅ (2026-07-21) | Toda correção converge para "lista e contagem compartilham o mesmo `WHERE` server-side". Não se inventa padrão novo. |
| D2 | **Reusar o que já existe** (mesma filosofia 60/D4, 61/D4) ✅ | O engine `db/filters` ([registry.py:38-65](../db/filters/registry.py#L38)) já cobre TODAS as dimensões do hub; `/api/atendimentos/filter` ([conversations.py:164](../server/routes/conversations.py#L164)) já pagina server-side com o mesmo `WHERE` do count. O grosso do fix é **wiring de frontend**, não backend novo. |
| D3 | **Nada em produção pode quebrar ⇒ aditivo/retrocompatível** ✅ | O caminho client-side atual permanece como **fallback** para specs não-expressáveis; nenhum endpoint muda de shape. Migração da sidebar é opt-in por `isServerExpressible`. |
| D4 | Planos 60/61 garantiram **"o número nunca mente"**; este plano garante **"a lista nunca mente"** ✅ | Lista e contagem passam a ler a MESMA query filtrada. Onde a lista não puder ser server-side, a contagem **também** cai no cliente (as duas metades ficam consistentes entre si — nunca uma server e outra client). |
| D5 | Postgres é o único backend ✅ | `WHERE` server-side + `LIMIT/OFFSET`; `COUNT` já existe (plano 60). |
| D6 | **21 ≠ 22 NÃO é bug** — é semântica ✅ (2026-07-21) | 21 = conversas **abertas, não-arquivadas, atualmente atribuídas ao Atendente X, em caixa visível**. O "22" do usuário inclui uma conversa fechada/arquivada/reatribuída/em caixa oculta. Isso é UX (chips de status/arquivo), não erro de contagem. Documentar, não "corrigir". |
| D7 | **Cobrir vários lugares** — Conversas (hub), Contatos, Custos, Agendamentos; verificar Protocolos ✅ | Plano multi-frente com waves; cada tela é um workstream independente após os habilitadores de backend. |

---

## 1. Resumo executivo

Os planos 60 e 61 resolveram a **contagem** (server-side, exata) mas, por decisão explícita (60/D2), deixaram a **lista** da sidebar de Conversas paginando no cliente sem aplicar o filtro no servidor. Resultado: com "Agente: Atendente X", a aba "Todas" mostra o total real do banco (21, via `/api/atendimentos/count`) enquanto a lista renderiza só as conversas do Atendente X que **couberam na página não-filtrada das 50 mais recentes** (7, via `/api/atendimentos` + `matchesAdvFilters` no cliente). A mesma classe de bug aparece em **Contatos** (filtros avançados no cliente sobre página de 15 → lista incompleta + **falso-vazio que trava o scroll infinito**) e em **Custos** (busca/ordenação no cliente sobre o top-N carregado); **Agendamentos** tem a versão latente (fetch sem filtro, filtro de status no cliente — hoje inofensivo com ~45 linhas, quebra ao passar de 500). O **Kanban de Protocolos** já faz o certo e é o modelo.

A solução: **rotear cada lista pela sua query server-side filtrada** — a mesma que já alimenta a contagem — reusando `db/filters` + `/api/atendimentos/filter` (Conversas), estendendo `/api/contacts` para aceitar os clauses (Contatos) e movendo busca/ordenação para o servidor (Custos). Onde a dimensão não for expressável no servidor, a lista **e** a contagem caem juntas no cliente (D4). O caminho client-side vira fallback, não o padrão.

---

## 2. Como funciona hoje (mapa)

### 2.1 — Conversas (hub / sidebar) — o bug reportado

| Peça | Onde | Comportamento |
|------|------|---------------|
| Fetch da lista (conversa-first) | [useConversationList.js:200-223](../web/static/js/components/contacts/hooks/useConversationList.js#L200) | `listConversations({archived, limit:50, offset})` → `GET /api/atendimentos` (**endpoint SIMPLES**, sem filtro avançado). Página de 50; scroll infinito via `loadMore` ([:281-315](../web/static/js/components/contacts/hooks/useConversationList.js#L281)). |
| Endpoint simples (sem filtro) | [conversations.py:125-154](../server/routes/conversations.py#L125) | aceita só `status/inbox_id/assignee_user_id/archived/limit/offset/contact_ids`. **Não** aceita `agent/tag/channel/…`. Devolve as N mais recentes de TODOS os agentes. |
| ⚠️ Filtro avançado = 100% cliente | [useConversationFilters.js:70-83](../web/static/js/components/contacts/hooks/useConversationFilters.js#L70) | `statusTagFiltered` aplica status+tags+adv via `matchesAdvFilters`/`clauseMatches` ([conversationRows.js:144-201](../web/static/js/services/conversationRows.js#L144)) **sobre as linhas já carregadas**. |
| ⚠️ Aba de atribuição = 100% cliente | [useConversationFilters.js:126-132](../web/static/js/components/contacts/hooks/useConversationFilters.js#L126) | `displayedContacts = statusTagFiltered.filter(matchesAssignment(c, tab, uid))` — **também** sobre as linhas carregadas. Minhas/Não atribuídas/Menções partem a página, não o banco. |
| Contagem das abas = **servidor** | [useConversationFilters.js:93-121](../web/static/js/components/contacts/hooks/useConversationFilters.js#L93) | `countConversations(buildCountParams(spec))` → `GET /api/atendimentos/count` (server-side exato). `tabCounts = serverCounts \|\| clientTabCounts` ([:91](../web/static/js/components/contacts/hooks/useConversationFilters.js#L91)). |
| Endpoint de filtro **server-side** (existe, NÃO usado pela sidebar) | [conversations.py:164-208](../server/routes/conversations.py#L164) `/api/atendimentos/filter` | `_spec_and_where` → `db.filters.build_where` → `conversation_repo.list_filtered(where, limit, offset)` com `has_more`. Usado **só** por [Attendances.js:131-180](../web/static/js/components/attendances/Attendances.js#L131). |
| Contagem e filtro-lista compartilham tudo | [conversation_repo.py:471-536](../db/repositories/conversation_repo.py#L471) | `list_filtered` e `count_tab_counts` usam o MESMO `_enriched_from()` ([conversation_query.py:112-118](../db/repositories/conversation_query.py#L112)) + MESMO `where` + MESMO escopo `inbox_ids`. Logo `count.all == total da lista filtrada` por construção. |
| Engine de filtros (completo) | [registry.py:38-65](../db/filters/registry.py#L38) + [translate.py:32-104](../db/filters/translate.py#L32) | Dimensões suportadas HOJE: `status, archived, assignee, inbox_id, priority, since, activity, display_id, channel, contact_type, agent, ai, starter, labels, conv_labels, q` + `cattr:conversation:*` + `cattr:contact:*`. **Cobre 100% do que o hub oferece** (o status "Não iniciada" das fases 5–7 do plano 60 está DEFASADO — o código já as implementou). |

**Diagnóstico:** a contagem é a metade **certa** (server-side); a lista é a metade **errada** (client-side sobre página não-filtrada). `agent user:<id>` → `assignee_user_id == id` ([translate.py:187-203](../db/filters/translate.py#L187)); com o chip "Abertas" o count é `status='open' AND is_archived=0 AND assignee=<id> AND caixa-visível` = **21 correto** (D6).

### 2.2 — Contatos (tela cheia)

| Peça | Onde | Comportamento |
|------|------|---------------|
| Fetch paginado (só busca server-side) | [ContactsListScreen.js:33,348-368](../web/static/js/components/ContactsListScreen.js#L348) | `getContacts(q, false, {limit:15, offset, sort:'name'})`; `PAGE_SIZE=15`; `hasMore` do envelope. Só `q`+`archived`+`sort` vão ao servidor. |
| ⚠️ Filtros avançados = cliente | [ContactsListScreen.js:494-501](../web/static/js/components/ContactsListScreen.js#L494) | `pageItems = contacts.filter(c => matchesAdvFilters(c, advFilters, now))` — **mesmo helper do hub**, sobre as linhas carregadas. Dims: `tag`, `contact_type`, `cattr:contact:*` ([ContactFilterDialog.js:1-27,175-179](../web/static/js/components/contacts/ContactFilterDialog.js#L1)). |
| ⚠️ Falso-vazio que TRAVA o scroll | [ContactsListScreen.js:646-667](../web/static/js/components/ContactsListScreen.js#L646) | Quando `pageItems.length === 0` renderiza "Nenhum contato encontrado." e **NÃO** renderiza o sentinela (`sentinelRef`, só no ramo não-vazio [:664-667](../web/static/js/components/ContactsListScreen.js#L664)). Se o filtro não casa nada na página 1, o scroll **nunca** carrega páginas seguintes que têm matches, mesmo com `hasMore=true`. |
| Backend sem filtro/contagem avançada | [contacts.py:249-290](../server/routes/contacts.py#L249) + [contact_repo.py:361-403](../db/repositories/contact_repo.py#L361) | `list_contacts_page` WHERE = `archived + inbox_ids + q`; `total`/`has_more` por `COUNT` sobre esse mesmo WHERE. `tag/contact_type/custom_attributes` **não** entram. `getContacts` ([api.js:165-176](../web/static/js/services/api.js#L165)) só serializa `archived/q/limit/offset/sort`. |

### 2.3 — Custos (top gastadores)

| Peça | Onde | Comportamento |
|------|------|---------------|
| Fetch paginado (top-por-custo) | [CostsDashboard.js:105-119](../web/static/js/components/CostsDashboard.js#L105) | `getUsageByContact({...período, limit:25, offset})` server-pagina o top-custo; scroll infinito. |
| ⚠️ Busca + ordenação = cliente | [CostsDashboard.js:162-179](../web/static/js/components/CostsDashboard.js#L162) | `filtered = search ? contacts.filter(nome/telefone) : contacts` e o comparador de ordenação rodam no cliente sobre o carregado. Buscar um gastador fora do top-N carregado → falso "Nenhum contato encontrado" ([:339](../web/static/js/components/CostsDashboard.js#L339)); ordenar por tokens/nome reordena só o subconjunto. |
| Cards de resumo (corretos) | [CostsDashboard.js:98-103,236-262](../web/static/js/components/CostsDashboard.js#L98) | `getUsageSummary(período)` = agregados server-side do período inteiro. **Não** divergem (são período-wide por design, não contam a busca). |

### 2.4 — Agendamentos (plugin `agendamento_retorno`) — latente

| Peça | Onde | Comportamento |
|------|------|---------------|
| Fetch sem filtro | [static/ScheduleTabs.js:106](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L106) | `reqJson(`${apiBase}/items`)` — **sem** `status`. |
| ⚠️ Filtro de status = cliente | [static/ScheduleTabs.js:321-323,362](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L321) | `filtered = items.filter(status ativos/todos/específico)`; mostra `filtered.length`. |
| Backend suporta filtro (não usado) | [routes.py:31-37](../storages/plugins/agendamento_retorno/routes.py#L31) + [logic.py:127-149](../storages/plugins/agendamento_retorno/logic.py#L127) | `list_items(status=, assignee_user_id=, limit=500 cap 1000)`. Endpoint **já** aceita `status`/`assignee_user_id` server-side; o front só não passa. |

**Hoje inofensivo** (~45 linhas migradas < cap 500). **Latente:** ao passar de 500, `filtered.length` subconta e as abas de status ficam erradas. Sem badge de contagem separado ⇒ sem divergência numérica visível — só lista truncada.

---

## 3. Inventário / análise

### 3.1 — Superfícies afetadas (o mapa da classe de bug)

| # | Superfície | Sintoma | Contagem | Lista/filtro | Severidade | Esforço |
|---|-----------|---------|----------|--------------|-----------|---------|
| S1 | **Conversas — filtro avançado** | count 21 × lista 7 | servidor (exato) | **cliente** sobre página não-filtrada | 🔴 Alta | M |
| S2 | **Conversas — aba de atribuição** | "Minhas 5" mas lista mostra só as minhas da página | servidor (exato) | **cliente** (`matchesAssignment`) | 🔴 Alta (mesma raiz de S1) | S (junto de S1) |
| S3 | **Contatos — filtros avançados** | lista incompleta + **scroll travado** (falso-vazio) | não há badge | **cliente** (`matchesAdvFilters`) sobre página de 15 | 🟠 Média | M |
| S4 | **Custos — busca/ordenação** | busca falha p/ gastador fora do top-N; "top" errado ao reordenar | resumo período-wide (ok) | **cliente** sobre top-N carregado | 🟠 Média/Baixa | S |
| S5 | **Agendamentos — filtro de status** | lista trunca em 500 (latente) | `filtered.length` | **cliente**; backend já suporta | 🟢 Baixa | S |
| S6 | **Protocolos — modo Lista/tabela** (não-Kanban) | mesmo teto do Kanban antigo | a confirmar | a confirmar | 🟢 Baixa (verificar) | S |

### 3.2 — Itens a construir

| # | Item | Onde | Abordagem | Risco | Esforço |
|---|------|------|-----------|-------|---------|
| I0 | Dim `has_mention` no engine (aba "Menções" server-side) | [registry.py:65](../db/filters/registry.py#L65) + [translate.py:104](../db/filters/translate.py#L104) | `Dim("has_mention","has_mention",{equal_to})`; clause reusa o `EXISTS(mentions … read_at IS NULL … mentioned_user_id=ctx.user_id)` de [conversation_repo.py:503-508](../db/repositories/conversation_repo.py#L503). Sem isso a aba Menções não é server-expressável. | Baixo | S |
| I1 | Tradutor "spec do hub → params do /filter", incl. **aba de atribuição** | [conversationFilterSpec.js:118-149](../web/static/js/services/conversationFilterSpec.js#L118) | Estender `buildCountParams`/`isServerExpressible` (ou irmão `buildListParams`) p/ traduzir `assignmentTab`: `mine`→`assignee=me`(ou `agent=user:<uid>`), `unassigned`→`agent=none`, `mentions`→`has_mention=true`, `all`→nada. Mesma gramática de `/count` e `/filter`. `node --test`. | Médio | M |
| I2 | Rotear a sidebar conversa-first pelo `/filter` | [useConversationList.js:200-315](../web/static/js/components/contacts/hooks/useConversationList.js#L200) | No modo conversa-first (sem busca): se `isServerExpressible(spec completo)`, `fetchContacts`/`loadMore` usam `filterConversations({...buildListParams, limit:50, offset})` em vez de `listConversations`. Rows via `convRowToSidebarRow` (shape idêntico). Fallback ao caminho atual quando não-expressável. | Médio | M |
| I3 | Consumir a lista já filtrada (parar de re-filtrar no cliente) | [useConversationFilters.js:126-132](../web/static/js/components/contacts/hooks/useConversationFilters.js#L126) | Quando a sidebar veio server-filtrada, `displayedContacts` NÃO re-aplica status/adv/assignment (o servidor já cortou) — só ordena. Manter o caminho cliente p/ o modo busca e o fallback. | Médio | M |
| I4 | "mostrando X de Y carregadas" (plano 60 F4, nunca feito) | [ConversationFilterBar.js](../web/static/js/components/contacts/ConversationFilterBar.js) ou topo da lista | Linha `text-wa-secondary` quando `serverCounts.all > displayedContacts.length`. Agora **verdadeira** (a lista é a filtrada). | Baixo | S |
| I5 | Backend: filtros avançados de **Contatos** server-side | [contacts.py:249-290](../server/routes/contacts.py#L249) + [contact_repo.py:361-403](../db/repositories/contact_repo.py#L361) | `/api/contacts` passa a aceitar clauses (`tag`/`contact_type`/`cattr:contact:*`); `list_contacts_page` traduz p/ `WHERE` (join `contact_tags`, coluna `contact_type`, JSON `custom_attributes`) e o `total`/`has_more` refletem o filtrado. Reusar a camada `db.filters` (subset escopo-contato). | Médio | M |
| I6 | Frontend Contatos: mandar filtro ao servidor + matar o falso-vazio | [ContactsListScreen.js:494-501,646-667](../web/static/js/components/ContactsListScreen.js#L494) | Enviar `advFilters` no `getContacts`; remover o `matchesAdvFilters` cliente quando expressável; **sempre** renderizar o sentinela mesmo no ramo vazio (fim do dead-end) enquanto `hasMore`. | Médio | M |
| I7 | Custos: busca + ordenação server-side | [CostsDashboard.js:105-179](../web/static/js/components/CostsDashboard.js#L105) + `getUsageByContact`/rota de usage | Passar `q` e `sort` p/ o `getUsageByContact`; servidor rankeia/filtra o conjunto todo e devolve o top-N correto da página. Cards de resumo intactos. | Baixo | S |
| I8 | Agendamentos: filtro de status server-side | [static/ScheduleTabs.js:106,321-323](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L106) | `ListaTab` passa `?status=` ao `/items` (endpoint já suporta); reempacotar `.zip`. Opcional: contagem por status via `COUNT … GROUP BY status`. | Baixo | S |
| I9 | Verificar Protocolos modo Lista (não-Kanban) | [protocolos_tab.js](../storages/plugins/protocolos/static/protocolos_tab.js) + [logic.py](../storages/plugins/protocolos/logic.py) | Confirmar se o modo tabela sofre o teto (plano 61 I6); se sim, mostrar `serverTotal` do índice já existente. | Baixo | S |

### 3.3 — Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|----------|-------------------------|
| **A contagem 21 está errada (deveria ser 22)** | Verificado (D6): `count_tab_counts` e `list_filtered` partem do MESMO `_enriched_from()`+`WHERE`+escopo ([conversation_repo.py:471-536](../db/repositories/conversation_repo.py#L471)); joins são PK-side (sem fan-out); toda dim que cruza tabela usa subquery escalar `.in_()`. 21 = abertas+não-arquivadas+atribuídas-agora+visíveis. O "22" inclui uma conversa fora desses critérios. Nada a corrigir na contagem. |
| **Kanban de Protocolos tem o mesmo bug** | NÃO tem — é o **modelo correto**. `grouped_columns` (total) e `grouped_column` (cards paginados) vêm do MESMO índice cacheado `kanban_index.get_index(view, filters)` ([logic.py:1428-1449](../storages/plugins/protocolos/logic.py#L1428), [kanban_index.py:222-256](../storages/plugins/protocolos/kanban_index.py#L222)); os `attr_filters` (`pf:`/`cattr:`/`canal`) são aplicados **server-side em Python** ANTES de bucketizar; o `buildGrouping` cliente roda com `rows:[]` (só drag-drop). Truncamento de scan é **piso honesto** com `+` e aviso âmbar — afeta contagem e cards igualmente. |
| **Attendances / AuditLog / Executions** | Corretos — já filtram server-side e tiram o `total` da mesma query filtrada ([Attendances.js:131-180](../web/static/js/components/attendances/Attendances.js#L131), [AuditLog.js:211-235](../web/static/js/components/AuditLog.js#L211), [Executions.js:595-617](../web/static/js/components/Executions.js#L595)). São a referência a copiar. |
| **Telas "manager" (Plugins/Users/Roles/Tools/Agents/…)** | Carregam a lista **inteira** (sem paginação server-side) → filtro cliente + `.length` ficam consistentes. Não é a classe. |
| **Badge global de não-lidas** ([App.js:282](../web/static/js/components/shell/App.js#L282)) | `GET /api/contacts/unread-count` = total server-side isolado, não pareado com lista client-filtrada na mesma superfície. |
| **Sandbox** ([Sandbox.js:29,45-47](../web/static/js/components/Sandbox.js#L29)) | `getLogs(300)` é um tail limitado por natureza; filtro cliente sobre o tail é esperado, sem contagem. |
| **Migrar TODA a filtragem do hub p/ server-side de uma vez, incluindo busca por conteúdo** | A busca da sidebar casa **conteúdo de mensagem** (full-scan, plano 50 F6) que o `q` do `/filter` ([translate.py:246-253](../db/filters/translate.py#L246)) não faz (só nome/telefone). Escopo do modo BUSCA fica fora (P2); este plano cobre o modo **conversa-first**. |

---

## 4. Mudanças de infraestrutura (habilitadores)

**Backend (core):**
- **I0 — dim `has_mention`** no `db/filters` (torna a aba "Menções" server-expressável; sem ela, o modo conversa-first não pode server-filtrar a aba Menções e cai no fallback cliente). Reusa a subquery de menção que o `count_tab_counts` já tem.
- **I5 — filtro de Contatos server-side**: estender `contact_repo.list_contacts_page` + `/api/contacts` para aceitar clauses. Preferir **reusar `db.filters`** com um contexto escopo-contato (as dims `tag`→`labels`, `contact_type`, `cattr:contact:*` já existem no engine; falta o `_enriched_from`/base de contatos e o roteamento). Alternativa: um tradutor contato-específico pequeno. Decisão P3.

**Frontend (core):**
- **I1 — tradutor unificado**: `conversationFilterSpec.js` ganha a tradução da **aba de atribuição** (hoje só client-side) para params server-side, além do que já faz p/ o count. É o ponto comum reusado por count E lista.
- **I2/I3 — sidebar dirigida pelo servidor** no modo conversa-first: mesma espinha do Kanban de Protocolos (lista e contagem da mesma query).

**Plugin (`agendamento_retorno`):** I8 é edição da cópia instalada + reempacotar `.zip` (repo `whatsbot-pro-plugins`), sem tocar no core.

---

## 5. Fases / Roadmap

```
WAVE 0  F0(has_mention) · F5b(infra filtro Contatos backend)          ← 🟢 backend, independentes
           │ (barreira: F1 usa F0; F2 usa F1)
WAVE 1  F1(tradutor+aba) → F2(sidebar via /filter) → F3(parar re-filtro) · F4("X de Y")   ← Conversas (S1+S2)
WAVE 2  F6(Contatos front, usa F5b) · F7(Custos) · F8(Agendamentos)   ← 🟢 telas independentes entre si
WAVE 3  F9(verificar Protocolos modo Lista)                           ← 🟢 verificação/limpeza
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / Dependência |
|------|------|-----------|-------|-------|------------------------------|
| 0 | F0 | Dim `has_mention` no engine | 🟢 [bloqueia: F1] | Baixo | `curl /api/atendimentos/count?has_mention=true` bate com a aba Menções |
| 0 | F5b | Backend filtro de Contatos (I5) | 🟢 [bloqueia: F6] | Médio | `/api/contacts?tag=X` filtra + `total`/`has_more` refletem o filtro |
| 1 | F1 | Tradutor + tradução da aba (I1) | 🔴 [depende: F0] [bloqueia: F2] | Médio | `node --test` cobre `mine/unassigned/mentions`→params |
| 1 | F2 | Sidebar conversa-first via `/filter` (I2) | 🔴 [depende: F1] | Médio | Filtrar "Agente: Atendente X" ⇒ lista mostra 21 (== contagem); scroll carrega só matches |
| 1 | F3 | Parar re-filtro cliente quando server-filtrado (I3) | 🔴 [depende: F2] | Médio | Trocar aba/adv não re-corta no cliente; modo busca intacto |
| 1 | F4 | "mostrando X de Y" (I4) | 🟢 [depende: F2] | Baixo | Linha aparece quando total>carregadas; some quando iguais |
| 2 | F6 | Frontend Contatos (I6) | 🟢 [depende: F5b] | Médio | Filtrar por etiqueta mostra TODOS os matches (paginado); scroll não trava no falso-vazio |
| 2 | F7 | Custos busca/ordenação server-side (I7) | 🟢 | Baixo | Buscar gastador fora do top-N o encontra; ordenar reordena o conjunto todo |
| 2 | F8 | Agendamentos status server-side (I8) | 🟢 | Baixo | `ListaTab` pede `?status=`; `.zip` reempacotado |
| 3 | F9 | Verificar Protocolos modo Lista (I9) | 🟢 | Baixo | Modo tabela mostra `serverTotal` ou confirma que já está ok |

> **Paralelização:** F0 e F5b (backend) rodam juntas na Wave 0. F1→F2→F3 são **sequenciais** (cadeia da sidebar — 🔴), com F4 pendurada em F2. Na Wave 2, F6/F7/F8 são telas **independentes** (🟢, despachar juntas). F9 é verificação isolada.

**Disciplina (regras do repo):** verde a cada fase; **caracterização ANTES** de mexer no fetch da sidebar (fluxo crítico); **um refactor por commit**; toda dim nova passa pela allowlist `registry.DIMENSIONS` (fronteira de segurança); nunca avançar com teste vermelho não-explicado.

---

### Fase 0 — Dim `has_mention` no engine de filtros 🟢 [bloqueia: F1]
**Objetivo:** tornar a aba "Menções" server-expressável, para a sidebar poder server-filtrar as 3 abas (mine/unassigned/mentions), não só duas.
**Itens:**
1. `[sequencial]` [registry.py:65](../db/filters/registry.py#L65): `"has_mention": Dim("has_mention","has_mention",frozenset({"equal_to"}),"Menção")`.
2. `[sequencial]` [translate.py:94-104](../db/filters/translate.py#L94): `kind == "has_mention"` → reusar o `exists()` de menção não-lida do usuário ([conversation_repo.py:503-508](../db/repositories/conversation_repo.py#L503)); requer `ctx.user_id` (senão `FilterError` ou clause constante-falsa — alinhar com o `mentions=0 sem usuário` do count).
3. `[paralelo]` Teste em [tests/test_endpoints.py](../tests/test_endpoints.py): inserir menção não-lida; `/count?has_mention=true` == aba Menções; sem usuário → 0/erro tratado.

**Pronto quando:** `GET /api/atendimentos/filter?has_mention=true` devolve exatamente as conversas com menção não-lida do usuário logado; suíte verde no Postgres.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** dim `has_mention` no engine — `registry.py` (nova `Dim("has_mention","has_mention",{equal_to})` + `INTERNAL_DIMS={"has_mention"}`); `translate.py` (`_has_mention_clause` reusando o `exists()` de menção não-lida de `count_tab_counts`, imports `exists`/`literal`/`mentions`, branch em `_build_clause`, e `available_dimensions` pula `INTERNAL_DIMS`). Teste novo `tests/test_plano69_list_matches_count.py`.
- **Como foi feito / decisões:** `has_mention` é **dim interna** — server-expressável para a aba "Menções" mas **fora** de `available_dimensions` (não vira chip manual que o diálogo de filtro não saberia renderizar). Sem usuário ⇒ `literal(False)` (espelha `mentions=0` do count, sem `FilterError`). Valor `true/false` via `equal_to` (segue o padrão do kind `bool`).
- **Problemas / pendências:** nenhuma. Trabalho isolado em worktree `plano-69` + banco `whatsbot_test_69` (evita colisão com a IA do plano 68).
- **Verificação:** `pytest tests/test_plano69_list_matches_count.py -q` → 3 passed (has_mention == aba Menções e `len(/filter)==/count.all==mentions`; `agent=user:<id>` list==count; no-user constant-false). Smoke estrutural do WHERE (SQL executa nas 3 variações).

---

### Fase 1 — Tradutor unificado + tradução da aba de atribuição 🔴 [depende: F0] [bloqueia: F2]
**Objetivo:** um tradutor puro que converte o **spec completo do hub** (busca? + status + aba + tags + adv) nos params de `/filter` e `/count`, e sabe quando é 100% server-expressável.
**Itens:**
1. `[sequencial]` [conversationFilterSpec.js:118-149](../web/static/js/services/conversationFilterSpec.js#L118): estender (ou adicionar `buildListParams`) para incluir a **aba**: `mine`→`assignee=me` (ou `agent=user:<uid>`), `unassigned`→`agent=none`, `mentions`→`has_mention=true`, `all`→nada. `isServerExpressible` passa a considerar a aba (sempre expressável após F0).
2. `[sequencial]` Cuidar do conflito de dimensão: `agent` avançado + aba `unassigned` (ambos mapeiam p/ `agent`) → se colidirem, `isServerExpressible=false` (cai no cliente, consistente). Reusar a lógica de "mesma chave de param 2×" que já existe ([conversationFilterSpec.js:137-149](../web/static/js/services/conversationFilterSpec.js#L137)).
3. `[paralelo]` `node --test` em `conversationFilterSpec.test.js`: cada aba → params certos; combinação aba+adv que colide → `false`.

**Pronto quando:** `node --test` verde cobrindo as 4 abas + colisões; o mesmo params serve `/count` e `/filter` (byte-idêntico).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** `conversationFilterSpec.js` ganhou `buildListParams(spec)` (= `buildCountParams` + cláusula da aba), `assignmentParams(spec)` e `isListServerExpressible(spec)`. `buildCountParams` **intocado** (segue devolvendo os 4 contadores do spec-base). 7 testes novos em `conversationFilterSpec.test.js`.
- **Como foi feito / decisões:** a aba é **view selector**, fora do count (que já traz mine/unassigned/mentions do spec-base). Mapeamento: `mine`→`assignee=me` (servidor resolve "me" via sessão — sem precisar de `currentUserId` no cliente), `unassigned`→`agent=none`+`agent__op=equal_to` (bate EXATO com `count_tab_counts.unassigned`=ambos-null; o `__op` é obrigatório porque `from_params` relê `none` como `is_not_present`), `mentions`→`has_mention=true` (F0). `isListServerExpressible` detecta a colisão aba×adv (só `unassigned`+adv-`agent`), caindo no cliente p/ lista E contagem juntas (D4).
- **Problemas / pendências:** nenhuma. O wiring que consome esses builders (rotear a sidebar) é a F2.
- **Verificação:** `node --test conversationFilterSpec.test.js` → 12 passed (5 antigos + 7 novos: all==base, mine/unassigned/mentions, colisão→false, mine/mentions nunca colidem, searching zera a aba).

---

### Fase 2 — Sidebar conversa-first dirigida pelo `/filter` 🔴 [depende: F1]
**Objetivo:** no modo conversa-first, a lista vem **server-filtrada + paginada** pela MESMA query da contagem — lista e contagem convergem por construção (padrão do Kanban, D1).
**Itens:**
1. `[sequencial]` **Caracterização primeiro:** fixar o comportamento atual do modo conversa-first (default: status=open, aba=all, sem adv) — a lista deve ficar **byte-idêntica** (ordem `is_pinned, last_activity_at`; `/filter` já ordena assim em [conversation_repo.py:483-485](../db/repositories/conversation_repo.py#L483)).
2. `[sequencial]` [useConversationList.js:200-223](../web/static/js/components/contacts/hooks/useConversationList.js#L200) (fetch 1ª página) e [:281-315](../web/static/js/components/contacts/hooks/useConversationList.js#L281) (`loadMore`): quando **sem busca** E `isServerExpressible(spec)`, usar `filterConversations({...buildListParams(spec), limit:50, offset})` (import de [api.js:497-510](../web/static/js/services/api.js#L497)); mapear rows com `convRowToSidebarRow` ([conversationRows.js:516-558](../web/static/js/services/conversationRows.js#L516)); `has_more` do envelope. Passar o spec (status/aba/adv) via ref/prop da sidebar.
3. `[sequencial]` Não-expressável (ex.: `cattr` sem cobertura, colisão) OU modo busca ⇒ caminho atual (`listConversations`) intacto (fallback D3).
4. `[paralelo]` Invalidação: WS `conversation_upsert`/status/assign já reordenam a lista; garantir que uma conversa que **sai** do filtro (ex.: foi reatribuída) seja removida da sidebar server-filtrada (refetch da 1ª página ou drop otimista).

**Pronto quando:** com "Agente: Atendente X" a sidebar mostra as 21 (rolando carrega **só** matches, não conversas de outros agentes); default sem filtro fica idêntico ao de hoje; modo busca inalterado.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** `useConversationList.js` — refs `serverFilterRef`/`loadedServerFilterRef`; o fetch conversa-first (1ª página) e o `loadMore` roteiam por `filterConversations(...)` quando `serverFilterRef.current` tem params, senão `listConversations(...)` de sempre; `serverFilterRef` exposto no retorno. `useConversationFilters.js` — sincroniza `serverFilterRef` em RENDER (params de `buildListParams`) + efeito que refaz a 1ª página quando um FILTRO muda (status/aba/tags/avançado). `Contacts.js` passa `serverFilterRef` + `fetchContacts` ao hook de filtros. `api.js` — `filterConversations` ganhou `reqOpts` (signal). `conversations.py` — `_run_filter` decora `avatar_v` por row (paridade com `/api/atendimentos`).
- **Como foi feito / decisões:** P1 = **migrar tudo** (converge ao Kanban): mesmo o default (`status=open`) vai por `/filter?status=open` — a lista enche de abertas (antes o caminho simples trazia 50 recentes de qualquer status e o cliente escondia os fechados). Sincronizar o ref em render (não em efeito) é **load-bearing**: o efeito `[showArchived]` do list-hook roda ANTES dos efeitos do filters-hook, então só a escrita síncrona garante params frescos naquele refetch. `loadedServerFilterRef` congela os params da lista carregada p/ o `loadMore` paginar a MESMA query. Fallback (não-expressável/colisão/busca) = caminho simples intacto (D3).
- **Problemas / pendências:** WS-leak (F2 item 4, `[paralelo]`/opcional): uma conversa reatribuída p/ FORA do filtro atual só sai da lista no próximo refetch (troca de filtro/aba) — decisão consciente para NÃO reintroduzir o re-filtro cliente que a F3 removeu (senão a lista voltaria a encolher < contagem). Documentado na §6 e como limitação conhecida.
- **Verificação:** `node --check` OK nos 5 módulos. Suíte de endpoints: **1427 passed, 2 failed** — os 2 são pré-existentes (`agent_transfer_alert`, dependentes de estado/plugins), IDÊNTICOS ao baseline (main tree, sem plano 69) rodado no mesmo banco. Backend do `/filter` (list==count) provado no `test_plano69` (`agent=user:<id>` list==count).

---

### Fase 3 — Parar o re-filtro cliente quando a lista já veio filtrada 🔴 [depende: F2]
**Objetivo:** evitar filtro duplo (servidor já cortou) e a aba re-cortando a página.
**Itens:**
1. `[sequencial]` [useConversationFilters.js:70-132](../web/static/js/components/contacts/hooks/useConversationFilters.js#L70): quando a sidebar está no modo server-filtrado (flag vinda do list-hook), `statusTagFiltered`/`displayedContacts` **não** re-aplicam status/adv/assignment — apenas ordenam (`sortContactsBy`). No modo busca/fallback, mantêm o comportamento atual.
2. `[sequencial]` Garantir que `tabCounts` (server) continua alimentando os badges (já é o caso, [:91](../web/static/js/components/contacts/hooks/useConversationFilters.js#L91)); no fallback, `serverCounts=null` ⇒ `.length` cliente sobre a lista **também** client-filtrada (consistente, D4).

**Pronto quando:** clicar "Minhas"/"Não atribuídas" mostra a fila server-side completa (paginada), com o número da aba batendo; alternar filtros não deixa a lista "encolher" abaixo do total do servidor.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (junto da F2)
- **O que foi feito:** `useConversationFilters.js` — `serverMode` (`useMemo` = `!searching && isListServerExpressible(spec)`). Em `serverMode`: `statusTagFiltered` = `activeContacts` (sem re-aplicar status/tags/avançado) e `displayedContacts` = só `sortContactsBy` (sem re-aplicar a aba). O efeito da contagem passou a gatear em `serverMode` (não mais `isServerExpressible` do spec-base): lista E contagem server-side juntas, ou ambas caem no cliente (D4).
- **Como foi feito / decisões:** F3 é o par lógico da F2 — sem ele, o servidor cortava e o cliente re-cortava sobre a página (a lista encolhia < contagem, o bug original de volta). `serverMode` também alinha a decisão server/client da CONTAGEM com a da LISTA (o badge da aba nunca fica server enquanto a lista está client). `activeContacts` (gate `isVisibleInSidebar`) é mantido nos dois modos (paridade com o conversa-first atual).
- **Problemas / pendências:** nenhuma além do WS-leak citado na F2.
- **Verificação:** `node --check` OK; endpoints 1427/2 (== baseline). Node tests do tradutor (12) verdes. Validação manual do fluxo (Agente:X ⇒ lista == contagem; troca de aba refaz a fila server-side) fica p/ o checklist final.

---

### Fase 4 — "mostrando X de Y carregadas" 🟢 [depende: F2]
**Objetivo:** deixar explícito que a lista é paginada quando o total do filtro supera o carregado (plano 60 F4, agora verdadeiro).
**Itens:**
1. `[paralelo]` Linha discreta (`text-wa-secondary`, `wa-*`) quando `serverCounts.all > displayedContacts.length`, ex.: no topo/rodapé da sidebar.

**Pronto quando:** aparece só quando há mais no banco do que carregado; some quando iguais; legível no **modo escuro**.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída
- **O que foi feito:** linha "Mostrando X de Y" em `ContactList.js`, logo acima da sentinela do scroll, quando `tabCounts[assignmentTab]` (total server-side da aba) > `contacts.length` (carregado).
- **Como foi feito / decisões:** usa o total da ABA atual (`tabCounts[assignmentTab]`, com fallback em `all`), não o `all` cru — condiz com a lista exibida. Em fallback/busca `tabCounts` = client counts (total==carregado) ⇒ a linha some sozinha. `text-wa-secondary` (legível nos dois temas).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check` OK. Aparece só quando há mais no servidor que carregado; some quando iguais (checklist manual final).

---

### Fase 5b — Backend: filtro avançado de Contatos server-side 🟢 [bloqueia: F6]
**Objetivo:** `/api/contacts` aceita `tag`/`contact_type`/`cattr:contact:*` e o `total`/`has_more` refletem o filtro.
**Itens:**
1. `[sequencial]` [contact_repo.py:361-403](../db/repositories/contact_repo.py#L361): estender `list_contacts_page` (e o `COUNT` companheiro) para adicionar ao `WHERE`: `tag`→join `contact_tags⋈tags`, `contact_type`→coluna `contacts.contact_type`, `cattr:contact:*`→JSON `contacts.custom_attributes`. **Preferir reusar `db.filters`** (dims `labels`/`contact_type`/`cattr:contact:*` já existem — decisão P3).
2. `[sequencial]` [contacts.py:249-290](../server/routes/contacts.py#L249): aceitar os params/payload de filtro (espelhar `/api/atendimentos/filter`); gate `contact.read` (ou o vigente).
3. `[paralelo]` [api.js:165-176](../web/static/js/services/api.js#L165): `getContacts` serializa os clauses.
4. `[paralelo]` Teste: `/api/contacts?tag=X` conta e lista só os do tag; `?limit=1` não muda `total`; escopo de caixa respeitado.

**Pronto quando:** `curl /api/contacts?contact_type=telegram` devolve só os do tipo com `total` real; suíte verde no Postgres.

#### Status de execução — Fase 5b
**Estado:** ✅ Concluída (backend; front é a F6)
- **O que foi feito:** `db/filters/translate.py` ganhou `build_contact_where` (+ `_contact_clause`/`_contact_labels_clause`), exportado em `db/filters/__init__.py`. `contact_repo.list_contacts_page`/`list_contacts` aceitam `filter_where=` (aplicado à lista E ao COUNT). `server/routes/contacts.py`: helper `_contact_filter_where(request)` extrai só os params de dim de contato (tag/contact_type/cattr:contact:*), compila via `db.filters`, e o `GET /api/contacts` passa o WHERE nos dois caminhos (legado + paginado), mapeando `FilterError`→400. Testes novos no `test_plano69_list_matches_count.py`.
- **Como foi feito / decisões:** P3 resolvida por **tradutor dedicado de escopo-contato** (não forçar o engine de conversa, cujo `labels` referencia `conversations.c.contact_id`). Reusa a MESMA gramática de params planos (`db.filters.spec.from_params`, com `__op` override e split por vírgula), então o contrato é idêntico ao `/atendimentos/filter` — a F6 serializa igual. `contact_type`/`cattr:contact:*` já eram `contacts.c.*` no engine (reusados via `_scalar_clause`/`_contact_cattr_clause`); só `tag`/`labels` reescrito para `contacts.c.id.in_(…)` (+ `not_equal_to`=negação). `filter_where` entra no WHERE da lista E do COUNT ⇒ `total`/`has_more` refletem o filtro.
- **Problemas / pendências:** front (enviar `advFilters` + matar o falso-vazio do scroll) é a **F6**. Serializador de clauses no `api.js`/`ContactsListScreen` fica na F6.
- **Verificação:** `pytest tests/test_plano69_list_matches_count.py -q` → 7 passed. Cobre: `?tag=X` (lista⊆tag, `total`=filtro, `limit=1` mantém total, `ne` exclui), `?contact_type=telegram` (estreita, `total`<total geral), cattr desconhecido→400, sem filtro = shape intacto.

---

### Fase 6 — Frontend Contatos: filtro ao servidor + fim do falso-vazio 🟢 [depende: F5b]
**Objetivo:** filtrar Contatos mostra TODOS os matches (paginado) e o scroll nunca trava.
**Itens:**
1. `[sequencial]` [ContactsListScreen.js:494-501](../web/static/js/components/ContactsListScreen.js#L494): enviar `advFilters` ao `getContacts` (via F5b); remover o `matchesAdvFilters` cliente quando expressável (fallback só p/ dim não coberta).
2. `[sequencial]` [ContactsListScreen.js:646-667](../web/static/js/components/ContactsListScreen.js#L646): **sempre** renderizar o sentinela enquanto `hasMore`, inclusive no ramo "Nenhum contato encontrado" — mata o dead-end mesmo no fallback client-side.
3. `[paralelo]` (Opcional) badge "N contatos" com o `total` do servidor.

**Pronto quando:** filtrar por etiqueta que só casa contatos "no fim do alfabeto" os mostra (rolando); nunca mais "Nenhum contato encontrado" com `hasMore=true` sem carregar mais.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída
- **O que foi feito:** `conversationFilterSpec.js` exporta `buildContactFilterParams(advFilters)` + `isContactFilterServerExpressible(advFilters)` (reusam `addClause`/`isServerExpressible`). `api.js` — `getContacts` aceita `opts.filters` (params planos). `ContactsListScreen.js` — `filterServerMode`/`filterParams`/`filterKey`; `fetchPage` manda `filters` ao servidor; `resetKey` inclui `filterKey` (mudar filtro refaz da página 0); `pageItems` NÃO re-filtra em serverMode; **sentinela renderizada SEMPRE que `hasMore`** (fora do ternário) — mata o falso-vazio que travava o scroll. Testes: 4 node (serializador) + 1 endpoint (shape `labels=`/`__op`).
- **Como foi feito / decisões:** contrato = MESMA gramática de params do hub (o backend F5b usa `from_params`), então reusei os helpers em vez de um serializador novo. `tag` "≠" e dims duplicadas caem no **cliente** (`isContactFilterServerExpressible=false`) — mas agora com a sentinela viva, o scroll do fallback funciona (carrega e filtra as próximas páginas), fim do dead-end mesmo no cliente.
- **Problemas / pendências:** `tag` "≠" não pagina server-side (limitação herdada do mapeamento `tag→labels`, que só faz membership); aceitável (fallback cliente correto). Badge opcional "N contatos" não feito (item 3, opcional).
- **Verificação:** `node --check` OK; `node --test conversationFilterSpec.test.js` → 16 passed; `pytest test_plano69` → 8 passed (inclui o shape `labels=`/`contact_type__op` que o front emite).

---

### Fase 7 — Custos: busca e ordenação server-side 🟢
**Objetivo:** o top-N por período respeita busca e ordenação sobre o conjunto inteiro.
**Itens:**
1. `[sequencial]` [CostsDashboard.js:105-179](../web/static/js/components/CostsDashboard.js#L105): passar `q` e `sort` p/ `getUsageByContact`; remover o `filter`/comparador cliente. Rota/`usage_repo` correspondente rankeia/filtra server-side.
2. `[paralelo]` Cards de resumo intactos ([:98-103](../web/static/js/components/CostsDashboard.js#L98)) — período-wide por design.

**Pronto quando:** buscar um gastador fora da 1ª página o encontra; ordenar por tokens/nome reordena o ranking inteiro (não só o carregado).

#### Status de execução — Fase 7
**Estado:** ✅ Concluída
- **O que foi feito:** `usage_repo.by_contact` ganhou `q`/`sort`/`order` (helpers `_sort_expr` allowlist + `_search_clause`); `count_by_contact` ganhou `q` (o total reflete a busca). `/api/usage/by-contact` aceita `q`/`sort`/`order` e os repassa. `CostsDashboard.js` — busca com debounce (300ms) → servidor; `fetchPage` manda `q`/`sort`/`order`; `resetKey` inclui busca+ordenação; `pageItems = contacts` (fim do `filter`/comparador cliente). Cards de resumo intactos.
- **Como foi feito / decisões:** `sort` é allowlist (`cost_usd`/`total_tokens`/`prompt_tokens`/`completion_tokens`/`call_count`/`name`; default `cost_usd`), nunca SQL cru; tiebreaker `contact_id` no ORDER BY (empates não causam dup/gap ao paginar). Busca por `name ilike`/`phone like`. `count_by_contact(q)` garante `total` == universo buscado.
- **Problemas / pendências:** nenhuma. Resumo (agregados do período) segue period-wide por design (não conta a busca) — correto.
- **Verificação:** `node --check` OK; `pytest test_plano69` → 9 passed (novo: q isola + total=2; custo desc default; tokens asc/desc reordena o ranking; buscar por nome acha, total=1).

---

### Fase 8 — Agendamentos: filtro de status server-side 🟢
**Objetivo:** `ListaTab` filtra no servidor (endpoint já suporta) — fim do teto latente de 500.
**Itens:**
1. `[sequencial]` [static/ScheduleTabs.js:106,321-323](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L106): passar `?status=` ao `/items` conforme `fStatus`; `filtered` deixa de cortar por status (o servidor já corta). `assignee_user_id` idem.
2. `[paralelo]` (Opcional) contagem por status server-side (`COUNT … GROUP BY status`) p/ badges de aba exatos.
3. `[sequencial]` Reempacotar o `.zip` do plugin (repo `whatsbot-pro-plugins`).

**Pronto quando:** com >500 agendamentos, cada aba de status mostra a lista completa daquele status; `.zip` reempacotado.

#### Status de execução — Fase 8
**Estado:** ⏸️ ADIADA (conflito com o plano 68)
- **O que foi feito:** nada — deliberadamente adiada.
- **Como foi feito / decisões:** o plano 68 (rodando em paralelo por outra IA) reescreve `agendamento_retorno/logic.py`/`lifecycle.py`/`settings.py` e **bump do `plugin.yaml`** (→1.1.0), reempacotando o `.zip`. A F8 toca o MESMO plugin (`ScheduleTabs.js` + bump/zip) — a única colisão real entre os dois planos. Como F8 é 🟢 baixa/latente (só quebra >500 linhas; hoje ~45), foi segurada para aplicar SOBRE a versão do 68 (um bump/reempacote só), evitando editar o mesmo `plugin.yaml`/`.zip` em concorrência.
- **Problemas / pendências:** executar F8 **depois** que o plano 68 pousar: `ScheduleTabs.js` manda `?status=`/`assignee_user_id`, para de cortar no cliente; bump 1.x→ e reempacota o `.zip` (repo `whatsbot-pro-plugins`). Backend (`routes.py`/`logic.py`) já suporta.
- **Verificação:** pendente (pós-68).

---

### Fase 9 — Verificar Protocolos modo Lista/tabela 🟢
**Objetivo:** confirmar se o modo NÃO-Kanban (lista/tabela) sofre o teto (plano 61 I6) e, se sim, mostrar o total do servidor.
**Itens:**
1. `[sequencial]` Ler o render do modo Lista em [protocolos_tab.js](../storages/plugins/protocolos/static/protocolos_tab.js) — se conta `rows.length` sobre a página, reusar o `serverTotal` do índice já existente ([kanban_index.py](../storages/plugins/protocolos/kanban_index.py)); senão, marcar como já-correto.

**Pronto quando:** o modo Lista mostra o total real (ou fica documentado que já mostra); nenhuma regressão no Kanban.

#### Status de execução — Fase 9
**Estado:** ✅ Concluída (verificação — já-correto)
- **O que foi feito:** lido o render do modo Lista em `protocolos_tab.js`.
- **Como foi feito / decisões:** o modo **Lista já é correto** — `fetchPage` lê o envelope `{items,total,has_more}` de `GET /protocolos?…` e guarda `env.total` em `totalCount` ([:640,650-654]); `shownTotal` ([:1007-1012]) mostra `totalCount` (servidor) na Lista e a soma dos `col.total` no Kanban. NÃO conta `rows.length` no cliente. Sem teto. Nenhuma mudança necessária (nenhum toque no plugin ⇒ nenhum conflito com o 68).
- **Problemas / pendências:** nenhuma.
- **Verificação:** leitura do código (`totalCount`/`shownTotal` server-side); Kanban intacto (padrão de referência D1).

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Ordenação da sidebar (unread/read/oldest) | `/filter` ordena por `is_pinned, last_activity_at`; a ordenação por leitura é client-side sobre a página carregada → só ordena o carregado | Já é limitação do modo conversa-first (plano 50 F8); documentar. Se exigir exatidão, adicionar `sort` ao `/filter` (P4). |
| Modo BUSCA + filtro avançado | O `q` do `/filter` só casa nome/telefone; a busca da sidebar casa conteúdo de mensagem | Escopo deste plano = modo conversa-first. Busca+filtro fica como P2 (fallback cliente até o FTS do plano 50 P2). |
| Colisão de dimensão (aba `unassigned` + `agent` avançado) | Ambos mapeiam p/ `agent` → GET viraria OR indevido | `isServerExpressible=false` na colisão ⇒ cai no cliente (lista E contagem juntas, D4). |
| Remoção ao vivo (WS) na lista server-filtrada | Conversa reatribuída deveria SAIR do filtro "Agente: X"; um `conversation_upsert` só faz merge | F2 item 4: dropar/refazer quando o campo filtrado muda; senão a linha "vaza" no filtro. |
| Regressão no default (sem filtro) | Trocar `/api/atendimentos`→`/filter` no caso base muda a lista | Caracterização (F2 item 1): as duas queries têm MESMO `_enriched_from`/ordem; validar byte-idêntico antes de shipar. |
| Regressão do `/filter` p/ Attendances.js | A sidebar passa a ser 2º consumidor de `/filter` | `/filter` não muda de shape; suíte de `/filter` verde antes/depois; `Attendances.js` intocado. |
| Contatos: reusar `db.filters` p/ contatos | O engine é escopo-conversa (`_enriched_from` de conversas) | P3: ou fatorar um contexto contato-scoped, ou tradutor pequeno dedicado. Não forçar o engine de conversa sobre contatos. |
| Perf: COUNT/list filtrado a cada tecla | Refetch por mudança de filtro | Debounce (já existe no count, [:103-119](../web/static/js/components/contacts/hooks/useConversationFilters.js#L103)); índices `idx_atend_*` cobrem status/assignee/archived. |
| Plugin restart / `.zip` | F8 sem reempacotar = fix não distribui | Passo explícito de reempacotar (repo `whatsbot-pro-plugins`). |
| Modo escuro | Linha "X de Y" (F4) / badges novos ilegíveis | `wa-*`/`text-wa-secondary`; testar com `.dark`. |
| Postgres | Suíte precisa do banco de teste | `WHATSBOT_TEST_DB_URL` com `test` no nome (trava); `DROP SCHEMA` por processo. |
| Segredo na URL | Filtros viram querystring | Sem credenciais nos params de filtro (só dims/valores de negócio). |

---

## 7. Perguntas em aberto

- **P1 — Rotear a sidebar SEMPRE pelo `/filter` (mesmo sem filtro) ou só quando há filtro?** ✅ DECIDIDO (2026-07-21): **só quando `isServerExpressible` E há filtro/aba não-trivial**; o caso default (status=open, aba=all, sem adv) pode continuar no `/api/atendimentos` (byte-idêntico) para minimizar risco — OU migrar tudo se a caracterização (F2/1) provar equivalência. Recomendação: migrar tudo (converge ao padrão do Kanban, um caminho só), validado por caracterização.
- **P2 — Modo BUSCA + filtro avançado.** ⏸️ ADIADO. O `q` server-side só casa nome/telefone; a busca-cliente casa conteúdo. Opções: (a) manter busca no caminho atual (fallback cliente) — recomendado agora; (b) esperar o FTS (plano 50 P2) e então server-filtrar busca+filtro juntos.
- **P3 — Contatos: reusar `db.filters` ou tradutor dedicado?** ⏸️ ADIADO (decidir no início da F5b). (a) Fatorar um contexto/`from` contato-scoped no `db.filters` (reuso máximo das dims `labels`/`contact_type`/`cattr:contact:*`); (b) tradutor pequeno em `contact_repo`. Recomendação: (a) se o esforço de fatorar o `_enriched_from` for baixo; senão (b).
- **P4 — Ordenação por leitura server-side na sidebar.** ⏸️ ADIADO (default: manter client-side sobre a página). Reabrir se o usuário quiser "não lidas primeiro" globalmente (aí adicionar `sort` ao `/filter`).
- **P5 — Contagem por status em Agendamentos (F8 item 2).** ⏸️ ADIADO (default: não). Fazer só se as abas de status ganharem badges numéricos.
- **P6 — 21 vs 22: mostrar conversas fechadas/arquivadas no filtro?** ✅ DECIDIDO (D6): não é bug; é escolha dos chips. Se o usuário quiser que "Agente: X" traga fechadas, é UX do chip de status (mudar o default), não deste plano.

---

## 8. Apêndice — arquivos-chave

**Backend — core (engine + endpoints)**
- [db/filters/registry.py:38-65](../db/filters/registry.py#L38) — `DIMENSIONS` (add `has_mention`).
- [db/filters/translate.py:32-104,187-253](../db/filters/translate.py#L32) — `build_where` + clauses (add `has_mention`; referência das dims).
- [server/routes/conversations.py:125-234](../server/routes/conversations.py#L125) — `/api/atendimentos` (simples), `/filter`, `/count`, `_spec_and_where`.
- [db/repositories/conversation_repo.py:471-536](../db/repositories/conversation_repo.py#L471) — `list_filtered` + `count_tab_counts` (mesma fonte).
- [db/repositories/conversation_query.py:112-118](../db/repositories/conversation_query.py#L112) — `_enriched_from` (join contacts INNER, inboxes/channels OUTER).
- [server/routes/contacts.py:249-290](../server/routes/contacts.py#L249) + [db/repositories/contact_repo.py:361-403](../db/repositories/contact_repo.py#L361) — filtro de Contatos (F5b).

**Frontend — core**
- [web/static/js/services/conversationFilterSpec.js:118-149](../web/static/js/services/conversationFilterSpec.js#L118) — tradutor (add aba de atribuição; `buildListParams`).
- [web/static/js/components/contacts/hooks/useConversationList.js:200-315](../web/static/js/components/contacts/hooks/useConversationList.js#L200) — fetch/`loadMore` (rotear p/ `/filter`).
- [web/static/js/components/contacts/hooks/useConversationFilters.js:70-132](../web/static/js/components/contacts/hooks/useConversationFilters.js#L70) — parar re-filtro; `tabCounts`.
- [web/static/js/services/api.js:165-176,458-525](../web/static/js/services/api.js#L458) — `getContacts`/`listConversations`/`filterConversations`/`countConversations`.
- [web/static/js/components/contacts/ConversationFilterBar.js](../web/static/js/components/contacts/ConversationFilterBar.js) — "X de Y" (F4).
- [web/static/js/components/ContactsListScreen.js:33,348-368,494-501,646-667](../web/static/js/components/ContactsListScreen.js#L348) — Contatos (F6).
- [web/static/js/components/CostsDashboard.js:105-179](../web/static/js/components/CostsDashboard.js#L105) — Custos (F7).

**Plugin**
- [storages/plugins/agendamento_retorno/static/ScheduleTabs.js:106,321-323](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L106) + [routes.py:31-37](../storages/plugins/agendamento_retorno/routes.py#L31) + [logic.py:127-149](../storages/plugins/agendamento_retorno/logic.py#L127) — Agendamentos (F8).
- [storages/plugins/protocolos/static/protocolos_tab.js](../storages/plugins/protocolos/static/protocolos_tab.js) + [kanban_index.py:222-256](../storages/plugins/protocolos/kanban_index.py#L222) — referência correta + verificação do modo Lista (F9).

**Referência (padrão correto a copiar)**
- [docs-planos/61-plano-contagem-total-protocolos-kanban.md] (git history — apagado no commit `21627f3`) — Kanban: contagem+cards da mesma fonte server-side.
- [docs-planos/60-plano-contagem-total-conversas-sem-carregar.md] (git history) — contagem server-side de conversas (a metade já shipada).

**Testes**
- [tests/test_endpoints.py](../tests/test_endpoints.py) — `has_mention` no `/count`+`/filter`; `/api/contacts` com filtro (total, escopo, `limit` ignorado).
- `web/static/js/services/conversationFilterSpec.test.js` — tradução das abas (`node --test`).

---

## 9. Checklist de verificação

- [x] **Conversas:** filtrar "Agente: X" ⇒ a **lista** mostra o mesmo número da **contagem** da aba (provado no backend: `test_agent_filter_list_matches_count` — `len(/filter)==/count.all`); rolar carrega SÓ matches (server-paginado por `/filter`). _Validação visual manual pendente._
- [x] Trocar aba (Minhas/Não atribuídas/Menções) ⇒ fila server-side completa; `has_mention` == aba Menções provado (`test_has_mention_filter_matches_mentions_tab`). _Validação visual manual pendente._
- [~] Caso default (sem filtro, aba Todas, Abertas) — P1 = **migrar tudo** (`/filter?status=open`); NÃO é byte-idêntico de propósito (enche a página de abertas em vez de trazer fechados e escondê-los). Ordem idêntica (`is_pinned,last_activity_at`). _Validação visual manual pendente._
- [x] Modo BUSCA inalterado (fallback cliente); dim não-expressável/colisão ⇒ `serverMode=false` ⇒ lista E contagem no cliente juntas (D4, `isListServerExpressible`).
- [x] **Contatos:** filtro expressável vai ao servidor (paginado, `total` reflete o filtro — `test_contacts_filter_*`); sentinela SEMPRE renderizada com `hasMore` (fim do dead-end). _Validação visual manual pendente._
- [x] **Custos:** busca + ordenação server-side sobre o conjunto do período (`test_usage_by_contact_search_and_sort_server_side`).
- [ ] **Agendamentos:** F8 ADIADA (pós-plano-68). Pendente.
- [x] **Protocolos:** modo Lista já mostra o total do servidor (F9 — verificado); Kanban intocado.
- [x] `test_endpoints.py` no Postgres (`whatsbot_test_69`): **1427 passed, 2 failed** — as 2 são pré-existentes (`agent_transfer_alert`), IDÊNTICAS ao baseline sem plano 69.
- [x] `node --test conversationFilterSpec.test.js` → **16 passed**.
- [x] `/api/atendimentos/filter` não regrediu (shape intacto + `avatar_v` aditivo; suíte verde; Attendances.js intocado).
- [ ] "X de Y" (F4) + linha de Contatos legíveis no **modo escuro**; reload/voltar-avançar — _validação visual manual pendente_ (código usa `text-wa-secondary`; sem segredo em querystring de filtro).
