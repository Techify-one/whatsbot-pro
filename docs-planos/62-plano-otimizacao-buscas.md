# Plano 62 — Otimização das buscas (sidebar + tela Contatos): reescrita da query, índices, busca no SQL e frontend

> **Status:** CÓDIGO COMPLETO — F0–F6 + F7 CONCLUÍDAS e commitadas (branch `developer`, **sem push**); F8 adiado (janela). Falta só: aplicar o DDL em produção (§9.4) e dar o push · **Data:** 2026-07-20 · **Escopo:** médio-grande
>
> **Medição ponta-a-ponta pós-Wave 0** (camada de repo real, `list_contacts_page`, contra os DADOS DE PRODUÇÃO em sessão read-only, 14.513 contatos):
>
> | Caminho | Antes (plano) | Depois (medido) |
> |---|---|---|
> | Sidebar sem busca (limit 50) | ~35,8 s | **213 ms** |
> | Tela Contatos sem busca (limit 15, name) | ~35,8 s | **136 ms** |
> | Busca "maria" (limit 15, name) | ~20 s | **1.102 ms** |
> | Busca "jo" (sidebar, sem limit, 628 matches) | ~20 s | **1.268 ms** |
> | Busca por conteúdo de mensagem | ~20 s | **1.069 ms** |
>
> O ~1 s residual da BUSCA acima é o pós-filtro Python que a **F5** eliminou (agora paginado no SQL com trigram). A revisão de perf da F5, em escala de 600k mensagens, mediu **2,2–2,9× de aceleração adicional** nos casos comuns (3+ chars) e a busca por conteúdo passou a cobrir **todo o histórico** (antes ~5 dias). ⚠️ **A F5 depende dos índices trigram existirem em produção** (§9.4): dar push ANTES do DDL deixa a busca por conteúdo em full-scan (degradada) ou em erro 500 (sem `f_unaccent`).
> **Commits:** `729160e` (F1) · `6aa7e0d` (F0) · `fc12929` (F2) · `c6c846b` (F3) · `252f27b` (F4) · `04ff820` (F7).
> **Origem:** pedido do usuário — "a barra de pesquisa principal demora e a barra de buscar na tela de contatos também; o banco tem muitos dados e preciso de otimização (índices, caches, etc.)". **Método:** diagnóstico multi-agente 100% somente-leitura: leitura do código com `arquivo:linha`, `EXPLAIN (ANALYZE, BUFFERS)` das queries reais compiladas do SQLAlchemy contra o Postgres de produção (`whatsbot@203.0.113.30`), estatísticas `pg_stat_*`, e verificação adversarial independente de cada achado (12 gargalos confirmados, 6 falsos-positivos descartados).
> **Causa-raiz (medida, não estimada):** a lentidão NÃO é volume/I/O (banco de 279 MB, cache hit 99,87%) — é **plano de execução**. O planner subestima o self-join `lm` (última msg por contato, `MAX(ts)`) em `rows=1` (real: 14.540) e escolhe Nested Loop+Materialize com **210,9 milhões de comparações descartadas** → a query de listagem/busca de contatos leva **19,6 s** (e **35,8 s** com `LIMIT 15/30**, pior!). A mesma query reescrita como LATERAL roda em **280 ms (70×)** — validado em produção via EXPLAIN. Agravantes: busca com `q` roda SEM `LIMIT` + filtro em Python; tela Contatos dispara 1 request **por tecla** (sem debounce); nenhum request é cancelado.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Apenas planejar** — nada foi alterado ainda ✅ (2026-07-20) | Diagnóstico foi todo read-only (`default_transaction_read_only=on`); nenhum índice/config criado. Execução só após aprovação. |
| D2 | Instância em **produção** (Coolify, Empresa Exemplo) não pode quebrar | Mudanças aditivas e retrocompatíveis; **verde a cada fase**; caracterização da busca ANTES de mexer no matching (F1/F5); um refactor por commit. |
| D3 | Postgres é o único backend (plano 29) | Livre para usar `LATERAL`, `DISTINCT ON`, `unaccent`, `pg_trgm`, índice parcial — sem preocupação com SQLite. |
| D4 | O cluster `203.0.113.30` é **compartilhado** (Chatwoot/Nexus no mesmo Postgres) | Nada que exija restart do Postgres entra no caminho crítico (pg_stat_statements vira fase opcional com janela combinada); CPU economizada beneficia todo o cluster. |

---

## 1. Resumo executivo

As duas barras de busca convergem no mesmo endpoint (`GET /api/contacts?q=`) e na mesma query (`build_list_contacts_query`), que hoje é patológica: o subquery `lm` (última mensagem visível por contato via self-join `MAX(ts)`) é mal estimado pelo planner (`rows=1` vs 14.540 reais), degenerando em Nested Loop+Materialize com ~211M comparações — **19,6 s por busca**, 100% CPU com cache quente. Com `LIMIT` (caminho paginado) fica **pior** (35,8 s: o `conv` também degenera, ~421M comparações). Em cima disso: (a) com `q` o backend carrega **todas** as ~14,5k linhas e filtra nome/telefone/tag **em Python** (fold de acentos), então nem o `limit=15` da tela Contatos protege; (b) a tela Contatos dispara **1 request por tecla** (sem debounce) e nada cancela os requests antigos; (c) a sidebar tem race de resposta fora de ordem (resultado velho sobrescreve o novo — o usuário vê "a busca voltou atrás").

O plano ataca em 3 ondas: **Wave 0** entrega o grosso (reescrever `lm`/`conv` como LATERAL — 70× medido — + índice em `messages(ts)` + debounce/cancelamento no frontend) sem mudar comportamento; **Wave 1** move o matching de busca para o SQL (`unaccent` + `pg_trgm`, ambas disponíveis no servidor mas não instaladas) habilitando paginação real com `q` e removendo o teto de ~5 dias da busca por conteúdo; **Wave 2** limpa cargas de fundo (avatar sweep roda a query pesada 2×/varredura + N+1 por contato, em loop praticamente contínuo) e higiene de infra opcional.

Resultado esperado: busca da sidebar de ~20 s → **<0,5 s** já na Wave 0; tela Contatos de ~36 s/lote → sub-segundo; na Wave 1, busca paginada de verdade (custo por página, não por universo).

---

## 2. Como funciona hoje (mapa)

### 2.1 — Fluxo das duas barras

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| Barra da **sidebar** (hub) | [ContactList.js:463-466](../web/static/js/components/contacts/ContactList.js#L463) → [useConversationList.js:98-121](../web/static/js/components/contacts/hooks/useConversationList.js#L98) | Debounce 300 ms ([:178-181](../web/static/js/components/contacts/hooks/useConversationList.js#L178)); com `q` chama `getContacts(q, false)` **sem opts** → `/api/contacts?q=` **sem limit** (caminho legado, lista completa) + `listConversations({limit:200})`; cruza tudo client-side (`buildRows` [conversationRows.js:378](../web/static/js/services/conversationRows.js#L378)); [:118](../web/static/js/components/contacts/hooks/useConversationList.js#L118) `setHasMore(false)` — modo busca não pagina. |
| ⚠️ Race na sidebar | [useConversationList.js:104-120](../web/static/js/components/contacts/hooks/useConversationList.js#L104) | `Promise.all(...).then(setContacts)` **sem token de sequência nem AbortController** — resposta velha (pesada, ~20 s) sobrescreve a nova (ex.: limpar a busca restaura a lista e depois a busca velha chega e clobbera + `hasMore=false`). O padrão `alive` existe no mesmo arquivo ([:74-87](../web/static/js/components/contacts/hooks/useConversationList.js#L74)) mas não foi aplicado aqui. |
| Barra da **tela Contatos** | [ContactsListScreen.js:564-568](../web/static/js/components/ContactsListScreen.js#L564) | `onInput → setSearch` **SEM debounce** → [:347](../web/static/js/components/ContactsListScreen.js#L347) `resetKey` muda → [useInfiniteScroll.js:35-50](../web/static/js/hooks/useInfiniteScroll.js#L35) refaz `fetchPage(0)` **a cada tecla**. `fetchPage` ([:335-343](../web/static/js/components/ContactsListScreen.js#L335)) chama `GET /api/contacts?q=&limit=15&offset=0&sort=name`. Flag `alive` descarta resposta stale na UI, mas o request/query continua rodando. |
| Endpoint | [contacts.py:249-284](../server/routes/contacts.py#L249) | Sem `limit` (sidebar) → `list_contacts` FULL; com `limit` (tela Contatos) → `list_contacts_page`. |
| ⚠️ Repo com `q` | [contact_repo.py:352-363](../db/repositories/contact_repo.py#L352) | Executa `build_list_contacts_query` **SEM LIMIT**, shape de todas as ~14,5k rows, tags batch de todos, `_apply_q_filter` ([:297-319](../db/repositories/contact_repo.py#L297)) com `fold()` Python, e só então fatia `results[offset:offset+limit]`. **Cada página do scroll repete TODO o trabalho.** O `limit=15` da tela Contatos é ilusório. |
| Query pesada | [contact_search.py:128-252](../db/search/contact_search.py#L128) | `lm` = self-join `m1 ⋈ (SELECT contact_id, MAX(ts) … GROUP BY)` ([:149-174](../db/search/contact_search.py#L149)); `conv` = idem com `MAX(id)` de atendimentos ([:177-199](../db/search/contact_search.py#L177)); `msg_count` = COUNT correlato por contato ([:201-206](../db/search/contact_search.py#L201)) — **sem nenhum consumidor** (frontend só usa `status.msg_count` de `/api/status`). |
| Scan de conteúdo | [contact_search.py:107-125](../db/search/contact_search.py#L107) | `ORDER BY ts DESC LIMIT 5000` + join contacts; **não existe índice em `messages(ts)` sozinho** → Parallel Seq Scan de 626k rows + top-N sort (115–137 ms); depois `fold()` unicode por caractere sobre as 5000 em Python. Cap de 5000 cobre só **~5 dias** (1.154 msgs/dia). |
| Refetch por WS | [useConversationWsEvents.js:86-91](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L86), [:134-138](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L134), [:274](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L274), [:299-307](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L299) | Reconexão WS / archived / status-change re-disparam `fetchContacts(searchRef.current)` — com busca ativa, re-roda a query pesada. |
| Callers secundários do caminho FULL | [NewConversationModal.js:174](../web/static/js/components/contacts/NewConversationModal.js#L174); [ContactsListScreen.js:668](../web/static/js/components/ContactsListScreen.js#L668); [background.py:219-220](../server/background.py#L219) | Modal Nova Conversa (`getContacts(q)` sem limit); pós-criar contato (`getContacts('')` full só p/ achar o id); **avatar sweep** (full 2×/varredura + N+1 `channel_id_for_contact` por contato ([background.py:234-235](../server/background.py#L234) → [conversation_repo.py:492-506](../db/repositories/conversation_repo.py#L492)) + `sleep(0.5)`/contato ([:244](../server/background.py#L244)) ⇒ sweep de ~2 h > intervalo de 30 min ([:14](../server/background.py#L14)) = carga contínua). |
| Hub de atendimentos (SEM busca) | [conversations.py:96-118](../server/routes/conversations.py#L96) + [conversation_repo.py:429-457](../db/repositories/conversation_repo.py#L429) | **Saudável: 35 ms** (LIMIT no SQL, subqueries keyset via `idx_msg_conversation_ts`). Não tem `q` próprio — por isso a busca da sidebar cai no caminho pesado de contatos. Serve de **gabarito** para a reescrita. |

### 2.2 — Números medidos em produção (EXPLAIN ANALYZE, cache 100% quente — CPU puro)

| Query | Tempo | Diagnóstico |
|-------|-------|-------------|
| `list_contacts` sem LIMIT (busca com `q`, sidebar) | **19.618 ms** | Nested Loop+Materialize; `Rows Removed by Join Filter: 210.931.780`; hash join `lm` estimado rows=1, real 14.540 |
| `list_contacts` LIMIT 30 (paginação sem `q`) | **35.809 ms** | PIOR: `conv` também vira nested loop (+210,4M) ≈ 421M comparações; Sort precisa das 14,5k rows antes do LIMIT |
| `list_contacts` sort=name sem LIMIT (tela Contatos c/ busca) | **20.197 ms** | Mesmo defeito |
| Mesma query com `enable_nestloop=off` (prova) | **450 ms** (43×) | Hash Left Join — confirma que é plano, não volume |
| Reescrita **DISTINCT ON** (validada via EXPLAIN) | **408 ms** (48×) | Usa `idx_msg_contact_ts` existente |
| Reescrita **LATERAL LIMIT 1** (validada via EXPLAIN) | **280 ms** (70×) | Mesmo padrão do hub saudável |
| Scan de conteúdo (`ORDER BY ts DESC LIMIT 5000`) | **115–137 ms** | Parallel Seq Scan 626k rows — falta índice em `ts` |
| Hub `list_conversations` LIMIT 100 | **35 ms** | Saudável |
| COUNT de paginação | **1,7 ms** | Saudável |

Contexto do servidor: `messages`=626.873 rows/217 MB (+~1.154/dia), `contacts`=14.508, `atendimentos`=14.713; DB total 279 MB; cache hit 99,87%; `shared_buffers`=1GB, `work_mem`=48MB, `random_page_cost`=4, `statement_timeout`=0, `max_connections`=1000; **`pg_trgm` 1.6 e `unaccent` 1.1 disponíveis mas NÃO instaladas**; `pg_stat_statements` não instalado (`shared_preload_libraries` vazio). Pool SQLAlchemy default 5+10 ([engine.py:96-102](../db/engine.py#L96)) — buscas empilhadas de ~20 s saturam o pool e enfileiram webhook/saves atrás delas.

---

## 3. Inventário / análise

### 3.1 — Gargalos confirmados (verificação adversarial independente)

| # | Gargalo | Onde | Severidade | Risco do fix | Esforço |
|---|---------|------|------------|--------------|---------|
| G1 | Subqueries `lm`/`conv` degeneram o plano (19,6–35,8 s; 211–421M comparações) | [contact_search.py:149-199](../db/search/contact_search.py#L149) | **ALTA** | Médio | M |
| G2 | Com `q`: query sem LIMIT + filtro `fold()` em Python — cada tecla/página paga o universo | [contact_repo.py:352-363](../db/repositories/contact_repo.py#L352), [:297-319](../db/repositories/contact_repo.py#L297) | **ALTA** | Médio | M-L |
| G3 | Sidebar usa caminho legado sem limit (payload até ~4 MB p/ `q='a'`, ≥8.494 contatos) | [useConversationList.js:105](../web/static/js/components/contacts/hooks/useConversationList.js#L105), [api.js:165-174](../web/static/js/services/api.js#L165) | **ALTA** | Baixo | S-M |
| G4 | Tela Contatos sem debounce — 1 query pesada por TECLA | [ContactsListScreen.js:564-568](../web/static/js/components/ContactsListScreen.js#L564) | **ALTA** | Baixo | S |
| G5 | Nenhum cancelamento de request; sidebar com race fora-de-ordem (clobber real) | [useConversationList.js:104-120](../web/static/js/components/contacts/hooks/useConversationList.js#L104), [useInfiniteScroll.js:35-50](../web/static/js/hooks/useInfiniteScroll.js#L35) | ALTA (amplificador) | Baixo | S |
| G6 | Scan de conteúdo sem índice em `messages(ts)` (115–137 ms/busca; cap 5000 ≈ 5 dias) | [contact_search.py:107-125](../db/search/contact_search.py#L107) | baixa hoje, cresce linear | Baixo | S |
| G7 | `msg_count` COUNT correlato sem NENHUM consumidor (custo morto) | [contact_search.py:201-206](../db/search/contact_search.py#L201), [contact_repo.py:263](../db/repositories/contact_repo.py#L263) | média | Baixo | S |
| G8 | Avatar sweep: query pesada full 2×/sweep + N+1 por contato, sweep (~2 h) > intervalo (30 min) | [background.py:219-244](../server/background.py#L219) | média | Baixo | S-M |
| G9 | Refetch por WS re-executa a busca pesada com termo ativo | [useConversationWsEvents.js:86-91](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L86) | baixa | Baixo | S |
| G10 | Callers secundários no caminho FULL (modal Nova Conversa; pós-criação) | [NewConversationModal.js:174](../web/static/js/components/contacts/NewConversationModal.js#L174), [ContactsListScreen.js:668](../web/static/js/components/ContactsListScreen.js#L668) | baixa | Baixo | S |
| G11 | Tela Contatos paga o scan de mensagens mas não usa `match_snippet`/`match_msg_id` (só a sidebar usa — [ContactList.js:534](../web/static/js/components/contacts/ContactList.js#L534), [:608-613](../web/static/js/components/contacts/ContactList.js#L608)) | [contact_repo.py:297-316](../db/repositories/contact_repo.py#L297) | baixa | Baixo | S |
| G12 | Sidebar em modo busca renderiza TODAS as rows sem virtualização + 4-5 passadas de filtro client-side | [ContactList.js:531-649](../web/static/js/components/contacts/ContactList.js#L531), [useConversationFilters.js:59-93](../web/static/js/components/contacts/hooks/useConversationFilters.js#L59) | baixa | Médio | M |

### 3.2 — Falsos positivos descartados (NÃO gastar esforço aqui)

| Suspeita | Por que NÃO é o problema |
|----------|--------------------------|
| "O banco tem muitos dados" / precisa de mais RAM/hardware | DB = 279 MB, cache hit 99,87%, **zero I/O** nos planos medidos — é CPU desperdiçada por plano ruim. 626k mensagens é pequeno para Postgres. |
| Vacuum/bloat na tabela grande (`messages`) | Dead tuples = 0,2%, autovacuum em dia. (`atendimentos` tem 49,5% dead, mas são 10 MB — irrelevante p/ a busca; vira nota de higiene na Wave 2.) |
| Hub de atendimentos lento | Medido: **35 ms** com LIMIT 100. O padrão keyset dele é o gabarito da correção. |
| `ANALYZE` / estatísticas desatualizadas resolvem o plano | Não — o misestimate é de **seletividade de join** composto (`contact_id AND ts=MAX(ts)`), estrutural à forma da query. Só reescrita resolve (provado: `enable_nestloop=off` → 450 ms). |
| O fold Python das 5000 msgs é o vilão | Medido ~200-240 ms/busca ≈ **2%** da latência atual. Só vira relevante DEPOIS do G1 corrigido (aí a Wave 1 o elimina de vez). |
| `work_mem`/config do Postgres baixos | `work_mem`=48MB é generoso; nenhum sort espilhou para disco (temp_files=43 na vida toda do DB). Ajustes de config são higiene (Wave 2), não correção. |
| Cache de aplicação como PRIMEIRA medida | Com a query em 280 ms e paginada, cache TTL vira otimização marginal — só reavaliar depois (P4). Cachear uma query de 20 s seria esconder o defeito. |

---

## 4. Fases / Roadmap

```
WAVE 0 — o grande ganho (sem mudar comportamento visível)
  F0(caracterização) ─→ F1 🔴 (reescrita lm/conv + remover msg_count)   [bloqueia: F2? não · F5 sim]
                        F2 🟢 (índice messages(ts) — migration)          [independente]
                        F3 🟢 (frontend: debounce + cancelamento + limit nos callers)
           (F1, F2, F3 podem rodar em PARALELO após F0; F1 é o coração)

WAVE 1 — busca no SQL (paginação real com q)      [depende de: F1]
  F4 🔴 (extensões unaccent+pg_trgm + índices trigram — migration)
  F5 🔴 (matching q no SQL + LIMIT real + flag include_messages)  [depende de: F4]
  F6 🟢 (sidebar paginada no modo busca + refetch WS barato)      [depende de: F5]

WAVE 2 — cargas de fundo e higiene (independente das Waves 0-1 entre si)
  F7 🟢 (avatar sweep leve)  ·  F8 🟢 (infra DB opcional: pg_stat_statements, timeouts, índices mortos)
```

| Wave | Fase | Workstream | Paralelização | Risco | Pronto quando |
|------|------|-----------|---------------|-------|---------------|
| 0 | F0 | Testes | 🔴 primeiro | Baixo | Caracterização da busca verde |
| 0 | F1 | Backend/SQL | 🔴 após F0 | Médio | Busca < 0,5 s; suíte verde |
| 0 | F2 | DB/migration | 🟢 com F1/F3 | Baixo | Scan de conteúdo < 10 ms |
| 0 | F3 | Frontend | 🟢 com F1/F2 | Baixo | 1 request por pausa; sem clobber |
| 1 | F4 | DB/migration | 🔴 [depende de: F1] | Médio | Extensões + índices criados |
| 1 | F5 | Backend/SQL | 🔴 [depende de: F4] | Médio | `q` pagina no SQL |
| 1 | F6 | Frontend | 🟢 [depende de: F5] | Baixo | Busca da sidebar paginada |
| 2 | F7 | Backend | 🟢 | Baixo | Sweep sem query pesada |
| 2 | F8 | Infra/DBA | 🟢 (janela) | Médio | Telemetria ligada |

Disciplina (regras do repo): **verde a cada fase**; **caracterização ANTES** de mexer na busca (fluxo crítico do operador); **um refactor por commit**; nunca avançar com teste vermelho não-explicado.

---

### Fase F0 — Caracterização da busca (rede de segurança)

**Objetivo:** congelar o comportamento observável da busca antes de tocar na query, para F1/F5 provarem equivalência.

Itens:
1. `[sequencial]` Testes de caracterização (em `tests/`, padrão dos existentes, contra `WHATSBOT_TEST_DB_URL`): dado um conjunto semeado de contatos/mensagens/tags com acentos e caixa mista, `list_contacts_page` com e sem `q` retorna os MESMOS ids, na MESMA ordem (recency e name), mesmos campos (`match_snippet`, `match_msg_id`, `conv_*`, `last_msg_*`); busca por nome com/sem acento; busca por conteúdo de mensagem; busca por tag; `q` de 1 char não dispara o scan ([contact_search.py:83](../db/search/contact_search.py#L83)).
2. `[paralelo]` Registrar num teste o caso de **empate de `ts`** (2 msgs do mesmo contato com ts idêntico): hoje o self-join `MAX(ts)` pode duplicar a row do contato; a reescrita LATERAL retorna 1. Decidir asserção pelo comportamento NOVO (dedupe é correção, não regressão) e documentar.

**Pronto quando:** suíte de caracterização verde contra o código ATUAL.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** criado [tests/characterization/test_busca_contatos_characterization.py](../tests/characterization/test_busca_contatos_characterization.py) — 12 testes que congelam `contact_repo.list_contacts_page` com/sem `q`: ordem recency (pinned primeiro + `coalesce(ts visível, updated_at)` DESC, `_PREVIEW_EXCLUDED` fora do `last_msg_*`), ordem name (`coalesce(nullif(name,''), phone)`), `q` por nome acento/caixa nos dois sentidos, telefone-substring, group_name, tag acentuada, conteúdo de mensagem (`match_snippet` com texto original acentuado + `match_msg_id` da msg mais recente; private_note casa, tool_call não), `q` de 1 char sem scan de conteúdo, paginação com `q` (fatia Python, `total`/`has_more`), `archived=True`, shape (`msg_count` int ≥ 0 sem valor exato; `conv_*`) e o empate de `ts`.
- **Como foi feito / decisões:** dataset próprio do módulo com prefixo de telefone `77900062…` (não-BR, sem colapso de variantes; não colide com o seed do conftest); asserções de ordem/pertencimento RELATIVAS (filtradas aos phones do arquivo) — robustas ao DB de processo compartilhado. Empate de `ts` (item 2): asserção declara o comportamento NOVO (1 row) com `xfail(strict=False)` — hoje XFAIL (o self-join `MAX(ts)` duplica de fato), vira PASS quando a F1 (LATERAL) entrar. Caracterizado também: grupo com `contacts.name` vazio ordena por PHONE no `sort=name` (o ORDER BY não olha `group_name`), embora exiba `group_name`.
- **Problemas / pendências:** nenhum — o comportamento real bateu com o descrito no plano.
- **Verificação:** `venv/bin/python -m pytest tests/characterization/test_busca_contatos_characterization.py -q` → **12 passed, 1 xfailed** (2 execuções, determinístico) contra o Postgres de teste (`WHATSBOT_TEST_DB_URL`).

---

### Fase F1 — Reescrever `lm`/`conv` (o 70×) e remover `msg_count` morto 🔴

**Objetivo:** eliminar o plano degenerado. Alvo: busca de 19,6 s → ~0,3–0,5 s; paginação sem `q` de 35,8 s → sub-segundo.

Itens:
1. `[sequencial]` Em [contact_search.py:149-174](../db/search/contact_search.py#L149): trocar o subquery `lm` (self-join `MAX(ts)`) por **LEFT JOIN LATERAL** `(SELECT … FROM messages WHERE contact_id = contacts.id AND role NOT IN (…) ORDER BY ts DESC LIMIT 1)` — validado em produção: 280 ms. Em SQLAlchemy Core: `select(...).where(...).order_by(...).limit(1).lateral()` + `outerjoin(..., true())`. Alternativa aceitável: `DISTINCT ON (contact_id) … ORDER BY contact_id, ts DESC` (408 ms) — ver P1.
2. `[sequencial]` Mesma reescrita para `conv` ([contact_search.py:177-199](../db/search/contact_search.py#L177)): LATERAL `ORDER BY id DESC LIMIT 1` sobre `conversations` (elimina o segundo nested loop do caminho com LIMIT).
3. `[paralelo]` Remover `msg_count` do SELECT ([contact_search.py:201-206](../db/search/contact_search.py#L201), [:222](../db/search/contact_search.py#L222)) e do shape ([contact_repo.py:263](../db/repositories/contact_repo.py#L263) — manter a chave com `0` fixo por compat de shape, ou remover de vez; grep confirmou zero consumidores front/back).
4. `[sequencial]` Rodar o `EXPLAIN (ANALYZE, BUFFERS)` da query nova em produção (read-only) e colar o antes/depois neste plano.

**Pronto quando:** caracterização F0 verde inalterada; `tests/test_endpoints.py` verde; EXPLAIN em produção sem Nested Loop+Materialize nos joins `lm`/`conv`; busca na sidebar percebida como instantânea (<1 s ponta-a-ponta).

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-20) — item 4 (EXPLAIN em produção) executado na revisão adversarial, números abaixo
- **O que foi feito:** [db/search/contact_search.py](../db/search/contact_search.py) `build_list_contacts_query`: `lm` e `conv` reescritos como **LEFT JOIN LATERAL** (`SELECT … WHERE contact_id = contacts.id ORDER BY ts DESC / id DESC LIMIT 1`, `.correlate(contacts).lateral(...)` + `.outerjoin(…, true())`); `msg_count` virou `literal(0)` (chave mantida — preserva o shape; zero consumidores). Labels do SELECT externo, ORDER BY (recency e name) e filtros archived/inbox_ids inalterados. Docstrings do módulo/função atualizados (motivo: planner misestimate do self-join `MAX(ts)`, ~70× em produção). No teste de caracterização, o `xfail` do empate de `ts` foi removido — a LATERAL deduplica e o teste passa de verdade.
- **Como foi feito / decisões:** P1 decidido = **(a) LATERAL** (280 ms medidos × 408 ms do DISTINCT ON). `.correlate(contacts)` explícito nas duas laterais (não depender do auto-correlate). SQL compilado confirmado: 2× `LATERAL`, zero `GROUP BY`.
- **Problemas / pendências:** Suítes `tests/endpoints/` têm falhas de poluição de estado quando rodadas em diretório inteiro (18 falhas IDÊNTICAS com e sem a F1; 31/31 passam em isolamento) — pré-existentes, não relacionadas.
- **Verificação:** `tests/characterization/test_busca_contatos_characterization.py` → **13 passed** (sem xfail); `tests/test_endpoints.py` (standalone) → **1389 passed, 2 failed** — as 2 falhas (`agent_transfer_alert`) reproduzem byte-idênticas com o `contact_search.py` do HEAD (pré-existentes); SQL compilado salvo e inspecionado (LATERAL presente, sem GROUP BY de messages).
- **EXPLAIN (ANALYZE, BUFFERS) em produção (item 4, read-only, 2026-07-20 na revisão):** query recency sem LIMIT (14.512 contatos): **196,4 ms** (antes: 19.618 ms — ~100×). Plano: Seq Scan contacts → por contato `Limit 1` sobre `Index Scan Backward using idx_msg_contact_ts` (Rows Removed by Filter: 1) e `Index Scan using idx_atend_contact` + top-1 sort; sem Materialize, sem GROUP BY de `messages`; shared hit=123.983, zero reads. Variante `sort=name LIMIT 15 OFFSET 0` (tela Contatos): **134,8 ms** (antes: 35.809 ms — ~265×), top-N heapsort 40kB. Ambas com JIT ~9 ms incluso.

---

### Fase F2 — Índice `messages(ts)` 🟢 [paralelo com F1/F3]

**Objetivo:** o scan de conteúdo (`ORDER BY ts DESC LIMIT 5000`) vira index scan reverso com early-termination (137 ms → poucos ms).

Itens:
1. `[sequencial]` Migration Alembic criando índice btree em `messages (ts DESC)`. Recomendado **parcial**: `WHERE role NOT IN ('tool_call','system_notice','conversation_event','system')` — casa o predicado de [contact_search.py:112-113](../db/search/contact_search.py#L112) e exclui os 21,6% de rows painel-only. ⚠️ Gotchas de migration: revision id ≤32 chars (coluna `alembic_version` é varchar(32)); o dev-server com `--reload` observando `db/` roda `alembic upgrade head` **no banco vivo do checkout** ao salvar a migration — criar o arquivo com o serviço parado ou ciente disso.
2. `[sequencial]` Deploy: `init_db()` roda `alembic upgrade head` no boot do container. `CREATE INDEX` normal em 217 MB leva poucos segundos (lock de escrita curto em `messages` durante o boot — aceitável; ver Riscos para a opção CONCURRENTLY).

**Pronto quando:** `EXPLAIN` do scan mostra `Index Scan Backward` no índice novo; migration round-trip (`upgrade`/`downgrade`) OK no banco de teste.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** migration `db/alembic/versions/20260720_0059_idx_msg_ts_visible.py` (revision `0059_idx_msg_ts_visible`, 23 chars; down_revision `0058_merge_p50_p57`) criando o índice parcial `idx_msg_ts_visible` em `messages (ts DESC) WHERE role NOT IN ('tool_call','system_notice','conversation_event','system')`, idempotente (`IF NOT EXISTS`/`IF EXISTS`); + declaração espelho em `db/tables.py` (`Index("idx_msg_ts_visible", messages.c.ts.desc(), postgresql_where=…)`).
- **Como foi feito / decisões:** predicado copia os 4 roles hardcoded do scan em [contact_search.py:112-113](../db/search/contact_search.py#L112) (não há constante compartilhável — é subconjunto deliberado de `LIST_PANEL_ONLY_ROLES`: transcription/private_note/error seguem pesquisáveis e ficam no índice); `op.execute` com SQL literal (mesmo texto do plano) em vez de `op.create_index` p/ garantir `IF NOT EXISTS` + `ts DESC` exatos.
- **Problemas / pendências:** `tests/test_alembic_hygiene.py` já falhava ANTES desta fase (2 falhas pré-existentes: merge revision `0058_merge_p50_p57` viola o teste de cadeia linear + prefixos duplicados 0037/0042/0043/0046/0052 fora da allowlist) — a F2 não adicionou nenhuma falha (comparado antes/depois; `test_single_alembic_head` verde, 0059 é filho linear único do head).
- **Verificação:** round-trip `upgrade head → downgrade -1 → upgrade head` OK no banco de teste (psql confirma índice presente/ausente/presente); `compare_metadata` (autogenerate) = 0 diffs; EXPLAIN ANALYZE com 20k rows seed: `Index Scan using idx_msg_ts_visible` (Limit 5000, 3.4 ms, 139 shared buffer hits) — early-termination confirmada.

---

### Fase F3 — Frontend: debounce, cancelamento e tetos 🟢 [paralelo com F1/F2]

**Objetivo:** 1 request por pausa de digitação (não por tecla), sem clobber de resposta velha, sem callers full desnecessários.

Itens (todos `[paralelo]` entre si):
1. **Debounce na tela Contatos** ([ContactsListScreen.js:564-568](../web/static/js/components/ContactsListScreen.js#L564)): 300 ms antes de propagar ao `resetKey` (espelhar o padrão de [useConversationList.js:178-181](../web/static/js/components/contacts/hooks/useConversationList.js#L178)).
2. **Token de sequência na sidebar** ([useConversationList.js:104-120](../web/static/js/components/contacts/hooks/useConversationList.js#L104)): counter capturado no closure; só `setContacts` se `token === tokenAtual` (corrige o clobber + `hasMore=false` errado). O padrão `alive` de [:74-87](../web/static/js/components/contacts/hooks/useConversationList.js#L74) já é o precedente interno.
3. **AbortController** em `request()` de [api.js](../web/static/js/services/api.js) (opcional, param `signal`), usado pelos fetchs de listagem e abortado no debounce/resetKey. Nota honesta: aborta o fetch, não a thread/SQL no servidor — o alívio real do banco vem de F1 + debounce; isso corrige corrida e slots do browser.
4. **Limit nos callers secundários**: `NewConversationModal.js:174` passa `{limit: 20}`; pós-criação ([ContactsListScreen.js:668](../web/static/js/components/ContactsListScreen.js#L668)) troca o `getContacts('')` full por `GET /api/contacts/{phone}` do recém-criado.
5. **Refetch por WS com busca ativa** ([useConversationWsEvents.js:86-91](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L86)): quando `searchRef.current` não-vazio, re-executar só o `listConversations` (barato) e manter os resultados de busca atuais.

**Pronto quando:** digitar 8 letras na tela Contatos gera ≤2 requests (DevTools Network); limpar a busca na sidebar durante um request lento NUNCA volta a lista velha; `node --test` dos módulos puros verde.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** (1) debounce de 300ms na tela Contatos (`debouncedSearch` alimenta `fetchPage`/`resetKey`; o input segue imediato); (2) token de sequência (`fetchSeqRef`) no `fetchContacts` E no `loadMore` do `useConversationList` — resposta fora de ordem nunca sobrescreve a lista nova nem faz append obsoleto; (3) `signal` opcional (AbortController) em `httpClient.request()`, `getContacts` (`opts.signal`) e `listConversations` (2º arg `reqOpts`), usado nos dois caminhos quentes com abort do request anterior; AbortError engolido em todos os call sites; (4) `NewConversationModal` passa `{limit: 20}` e consome o envelope `{items}`; (5) pós-criação na tela Contatos usa `GET /api/contacts/{phone}` (`getContact(phone, false, null, {limit:1})`) em vez de `getContacts('')` full; (6) refetch por WS com busca ativa: cache curto (TTL 30s) do universo `getContacts(q)` dentro do `fetchContacts` — refetch com a MESMA query só repete o `listConversations` (barato).
- **Como foi feito / decisões:** abort resolvido por rejeição natural do fetch (AbortError) engolida nos callers, não por envelope sentinela — o token de sequência é a proteção primária de estado, o abort só libera o slot do browser. O item 5 do plano (refetch barato com busca ativa) foi implementado DENTRO do `useConversationList` (cache por query), então o `useConversationWsEvents` não mudou de lógica (só comentário) — todos os gatilhos WS se beneficiam automaticamente. Bônus: o efeito de busca debounced do `useConversationList` agora pula a execução do mount (o initial load já cobria; o duplicado passaria a abortar o fetch inicial).
- **Problemas / pendências:** com `limit=20` server-side no `NewConversationModal`, os filtros client-side (grupos/`contact_type`) podem reduzir as sugestões abaixo de 8 em queries que casam muitos grupos — aceitável (teto do plano). Cache de busca (item 6) tem staleness máxima de 30s para contatos NOVOS que casem a query durante busca ativa (linhas existentes seguem patchadas ao vivo pelos eventos WS).
- **Verificação:** `node --test` dos 13 arquivos `*.test.js` sob `web/static/js` — 211 testes verdes; `node --input-type=module --check` em cada arquivo alterado (6 arquivos) — OK; diff revisado (deps de hooks, closures, crase em comentário htm).

---

### Fase F4 — Extensões `unaccent` + `pg_trgm` e índices de busca 🔴 [Wave 1; depende de F1]

**Objetivo:** habilitar matching acento/caixa-insensível NO SQL, indexável.

Itens:
1. `[sequencial]` Migration: `CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS pg_trgm;` (ambas disponíveis no servidor, `installed_version` NULL). ⚠️ Requer privilégio — confirmar o user do `DATABASE_URL` da instância prod (P5); se não for superuser, criar as extensões manualmente ANTES do deploy e a migration vira `IF NOT EXISTS` idempotente.
2. `[sequencial]` Função wrapper **IMMUTABLE** `f_unaccent(text)` (a `unaccent()` nativa é STABLE e não pode indexar) via `op.execute` na migration.
3. `[paralelo]` Índices GIN trigram: `contacts (f_unaccent(lower(name)) gin_trgm_ops)` e `messages (f_unaccent(lower(content)) gin_trgm_ops)`. O de `messages` (115 MB heap) é o maior custo de criação — ver Riscos (CONCURRENTLY × boot).

**Pronto quando:** `\dx` mostra as extensões; `EXPLAIN` de um `ILIKE f_unaccent(...)` usa os índices GIN.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** migration `20260720_0060_trgm_unaccent_search.py` (revision `0060_trgm_unaccent_search`, down `0059_idx_msg_ts_visible`): `CREATE EXTENSION IF NOT EXISTS unaccent/pg_trgm`, wrapper IMMUTABLE `f_unaccent(text)` (`PARALLEL SAFE STRICT`), índices GIN trigram `idx_contacts_name_trgm` (`contacts.f_unaccent(lower(name))`) e `idx_msg_content_trgm` (parcial em `messages`, mesmo predicado de 4 roles do `idx_msg_ts_visible`). Downgrade dropa índices + função, NÃO as extensões (podem ser compartilhadas). Os 2 índices foram espelhados em `db/tables.py`.
- **Como foi feito / decisões:** P2=b confirmado — os statements do upgrade são o contrato exato com o DDL que o orquestrador aplica manualmente (CONCURRENTLY) em prod; `IF NOT EXISTS`/`CREATE OR REPLACE` tornam o boot no-op lá e criador em installs novos. O espelho em `tables.py` FOI necessário: `compare_metadata` contra o test DB pós-upgrade acusou 2 `remove_index` sem ele. Espelhado com expressão bound `func.f_unaccent(func.lower(col)).label(...)` + `postgresql_ops={label: "gin_trgm_ops"}` + `postgresql_using="gin"` (a forma `Index(text(...))` pura não se associa à tabela fora da definição inline); re-validado → 0 diffs. Nenhum uso de `metadata.create_all` no repo (schema sempre via Alembic), então o espelho referenciar `f_unaccent` é seguro.
- **Problemas / pendências:** logo após o bulk insert do seed, o EXPLAIN escolheu Seq Scan mesmo com `ANALYZE` — pending list do GIN (fastupdate) recém-populada infla o custo; minutos depois (merge da pending list) a MESMA query passou a usar o Bitmap Index Scan (0,2 ms). Em prod, após o `CREATE INDEX CONCURRENTLY`, o índice nasce sem pending list — não é um problema real, mas vale saber ao validar com EXPLAIN logo após bulk load.
- **Verificação:** (a) round-trip no `whatsbot_test_b`: upgrade head → `\dx` (unaccent 1.1 + pg_trgm 1.4), `\df f_unaccent`, 2 índices em `pg_indexes` → downgrade -1 (índices e função somem; extensões ficam) → upgrade head de novo sem erro (idempotente). (b) `SELECT f_unaccent(lower('Orçamento São João'))` = `'orcamento sao joao'` ✓. (c) seed de 5k msgs + `ANALYZE`: EXPLAIN ANALYZE da query exata (`LIKE '%xyzabc%' AND role NOT IN (…)`) usa **Bitmap Index Scan em idx_msg_content_trgm** (0,2 ms); `idx_contacts_name_trgm` idem em `contacts`; seed deletado. (d) `pytest tests/test_alembic_hygiene.py`: mesmas 2 falhas pré-existentes (merge 0058 + prefixos duplicados 0037/0042/0043/0046/0052), zero menções a 0060; `test_single_alembic_head` verde.

---

### Fase F5 — Matching de `q` no SQL + paginação real 🔴 [depende de F4]

**Objetivo:** o caminho com `q` pagina NO BANCO — custo por página, não por universo. Elimina `_apply_q_filter`/fold Python do caminho quente.

Itens:
1. `[sequencial]` Em [contact_search.py](../db/search/contact_search.py): novo builder de cláusula `q` — `f_unaccent(lower(name)) LIKE %q%` OR `phone LIKE` OR tag (EXISTS em `contact_tags⋈tags` com o mesmo unaccent) OR `id IN (match de conteúdo)`. O match de conteúdo vira subquery/CTE sobre `messages` usando o índice trigram de F4 (**substitui o cap de 5000 — a busca passa a cobrir TODO o histórico**) — manter `MIN_SCAN_QUERY_LEN` como guarda.
2. `[sequencial]` [contact_repo.py:352-363](../db/repositories/contact_repo.py#L352): o ramo com `q` passa a aplicar a cláusula no `WHERE` + `LIMIT/OFFSET` no SQL (igual ao ramo sem `q`); `total` via a count query com a mesma cláusula. `match_snippet`/`match_msg_id` calculados só para as rows da PÁGINA (o `fold`/`match_snippet` Python sobrevive aqui, agora sobre ≤15 conteúdos, não 5000).
3. `[paralelo]` Param `include_messages` (default `true` por compat) no endpoint ([contacts.py:249-284](../server/routes/contacts.py#L249)): a tela Contatos passa `false` (não renderiza snippet — G11) e pula o match de conteúdo.
4. `[sequencial]` Validar paridade contra F0: os testes de caracterização devem continuar verdes (mesmos resultados; divergências de collation/fold documentadas — ver Riscos).

**Pronto quando:** F0 verde; EXPLAIN do caminho com `q` mostra LIMIT efetivo no plano; busca por conteúdo encontra mensagem com >5 dias (impossível hoje).

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-07-20) — ⚠️ **NÃO subir para produção antes do DDL da §9.4**
- **O que foi feito:** o matching de `q` saiu do Python e virou cláusula SQL. Em [db/search/contact_search.py](../db/search/contact_search.py): `build_q_clause(q, *, include_messages=True)` (OR de `contacts.name` + `contacts.group_name` + `contacts.phone` + tags + conteúdo de mensagem), `build_content_matches_query(q, contact_ids)` (`DISTINCT ON (contact_id)` da mensagem casada mais recente), helpers `_escape_like`/`_folded`/`_folded_pattern`/`_folded_match`/`message_content_predicate` e a constante `SEARCH_EXCLUDED_ROLES` (os 4 roles dos índices parciais 0059/0060, antes literais duplicados). Em [db/repositories/contact_repo.py](../db/repositories/contact_repo.py): `list_contacts_page` tem UM só caminho — `WHERE` + `LIMIT/OFFSET` no SQL com ou sem `q`, `total` pelo `build_count_contacts_query` com a MESMA cláusula; `_apply_q_filter` (órfão) foi removido e substituído por `_matched_by_contact_fields` + `_decorate_content_matches` (snippet só para as linhas da página que casaram SÓ por conteúdo). Param `include_messages: bool = True` em `list_contacts`/`list_contacts_page` e no `GET /api/contacts` ([server/routes/contacts.py](../server/routes/contacts.py)). Novos testes: [tests/test_busca_sql_paginacao.py](../tests/test_busca_sql_paginacao.py) (19 testes).
- **Como foi feito / decisões:**
  - **🔴 ILIKE, não LIKE — bug REAL descoberto na execução.** Produção **e** os bancos de teste rodam com `lc_collate = 'C'`, onde `lower()` só rebaixa ASCII: `lower('ORÇAMENTO')` = `'orÇamento'`, então `f_unaccent(lower('ORÇAMENTO'))` = `'orCamento'` (maiúscula ASCII deixada pelo `unaccent` DEPOIS do `lower`). Foldar a query do mesmo jeito não resolve (as maiúsculas residuais caem em posições diferentes de cada lado: coluna `'orcamento'` × query `'orCamento'`), então a busca acentuada MAIÚSCULA falharia silenciosamente — os testes F0 (`óLiViA`, `ORÇAMENTO`) pegaram na hora. **`ILIKE` fecha o buraco**: o que o `unaccent` deixa é ASCII puro, e case-insensitivity ASCII funciona sob a collation C. A expressão do lado da COLUNA continua byte-idêntica ao índice 0060 (`f_unaccent(lower(col))`) e `gin_trgm_ops` suporta `~~*` igual a `~~` (comprovado no EXPLAIN). A alternativa correta-por-construção (indexar `lower(f_unaccent(col))`, que folda certo numa passada) exigiria recriar os 2 índices da 0060 → registrado como follow-up, não feito (F5 não toca `db/alembic/`).
  - **Tag/conteúdo como `IN (SELECT …)` NÃO-correlacionado**, não `EXISTS` correlacionado: dentro de um `OR` o Postgres não consegue transformar um `EXISTS` correlacionado em semi-join, então ele reexecutaria o subplano **por linha de contato**. A forma não-correlacionada vira `hashed SubPlan` — avaliada UMA vez, que é o que permite varrer `idx_msg_content_trgm` uma única vez (confirmado no plano: `hashed SubPlan 2` → `Bitmap Index Scan on idx_msg_content_trgm`).
  - **Metacaracteres escapados**: `%`/`_`/`\` digitados pelo operador viram literais (o `in` do Python que a cláusula substitui não tinha coringas). Sempre bind param — nunca f-string.
  - **`MESSAGE_SCAN_CAP` sai do caminho quente**: a busca por conteúdo passa a cobrir TODO o histórico (o teto de 5000 cobria ~5 dias em produção). `MIN_SCAN_QUERY_LEN` **fica** — `q` de 1 char continua casando nome/telefone/tag/grupo e nunca conteúdo.
  - **Superfície de nome**: a cláusula testa as colunas CRUAS `contacts.name` E `contacts.group_name`. O filtro Python testava o `name` já shaped (que para grupo JÁ É o `group_name`), ou seja ignorava o `contacts.name` cru de um grupo. É um superconjunto minúsculo e deliberado.
  - `contact_ids_matching_message` foi **mantida** (marcada LEGACY): saiu do caminho de busca mas `tests/test_endpoints.py` ainda a chama direto para provar a guarda `MIN_SCAN_QUERY_LEN`.
  - Divergência `fold()` Python × fold SQL documentada no docstring de `fold`: `casefold()` expande `ß`→`ss` e o NFKD quebra ligaduras (`ﬁ`→`fi`), o que `lower()`/`unaccent` não fazem; e o `unaccent` translitera `Æ`→`AE`, que o NFKD não. Paridade mirada em PT-BR; exóticos aceitos.
- **Problemas / pendências:**
  - ⚠️ **BLOQUEADOR DE DEPLOY**: com o código da F5, `GET /api/contacts?q=` **falha** em qualquer banco sem o DDL da F4. Medido contra produção (read-only): `ERRO: (psycopg.errors.UndefinedFunction) function f_unaccent(text) does not exist`, e `select count(*) from pg_indexes where indexname in ('idx_contacts_name_trgm','idx_msg_content_trgm')` = **0**. O caminho SEM busca não é afetado (173 ms, 14.516 contatos). Ou seja: **aplique a §9.4 em produção ANTES de dar push/deploy nesta fase** — antes disso a busca das duas barras quebra (a listagem continua de pé).
  - Follow-up (opcional, exige migration nova): recriar os índices da 0060 como `lower(f_unaccent(col))` e trocar o `ILIKE` por `LIKE`. Ganho = fold correto numa passada + pattern totalmente lowercase; sem isso o `ILIKE` já entrega o comportamento certo, então não é urgente.
  - A F6 (sidebar paginada) segue pendente: com `limit=None` a busca da sidebar ainda materializa todos os matches num payload só — agora barato no SQL, mas grande na rede.
  - `idx_contacts_name_trgm` **não** é usado no plano: como o `OR` tem ramos não-indexáveis (`group_name`, `phone`, subplano de tags), o Postgres cai num Seq Scan de `contacts` — 12 ms com 2 mil contatos, ~sub-100 ms esperado com os 14,5 mil de produção. Aceitável; o índice caro (`messages`) é o que importa e ESSE é usado.
- **Verificação:**
  - (a) `pytest tests/characterization/test_busca_contatos_characterization.py` → **13/13 verdes** (o contrato da F0). As 2 falhas iniciais (`óLiViA`, `ORÇAMENTO`) eram o bug de collation acima — corrigido no CÓDIGO, nenhum teste alterado.
  - (b) `pytest tests/test_busca_sql_paginacao.py` → **19/19 verdes** (páginas disjuntas + união = universo filtrado, `total` = COUNT SQL, `include_messages=False` no match e no total, fold acento/caixa nos 2 sentidos incl. ACENTUADA MAIÚSCULA, tag acentuada, grupo por `group_name`, `q` de 1 char sem conteúdo, mensagem ANTIGA encontrável, coringas literais).
  - (c) `venv/bin/python tests/test_endpoints.py` → **1389 passed, 2 failed** — só as 2 pré-existentes (`agent_transfer_alert emitido`, `duration da config global`).
  - (d) **Prova de uso do índice** (seed de 2.000 contatos + 20.000 mensagens no `whatsbot_test_a`, `ANALYZE`, EXPLAIN da query real compilada, seed removido depois): o plano da busca `q='hipoglicemiante'` com `LIMIT 15` traz `hashed SubPlan 2 → Bitmap Heap Scan on messages → **Bitmap Index Scan on idx_msg_content_trgm** (Index Cond: f_unaccent(lower(content)) ~~* '%hipoglicemiante%')`, `Execution Time: 17,3 ms` — e o `Limit` aparece no topo do plano (paginação efetiva). O COUNT com a mesma cláusula reusa o mesmo Bitmap Index Scan. A query de snippet (`build_content_matches_query`) roda por `idx_msg_contact_ts` sobre os ids da página (0,12 ms).

**Revisão adversarial — LENTE 1 (paridade de comportamento), 2026-07-20:** APROVADA. Dataset adversarial (nomes com Ç/ã/É/ü, caixa mista, grupo com `group_name` vazio, tag acentuada, telefone com/sem 9, conteúdo acentuado, `private_note`/`transcription`/`error`/`tool_call`/`system`/`system_notice`, mensagem vazia, contato arquivado, empate de `ts`) semeado no `whatsbot_test_a`; ~32 queries (1/2/3+ chars, acentuada/não, maiúscula, telefone, tag, conteúdo, coringa `%`/`_`, inexistente) comparando o conjunto de ids do código NOVO contra a lógica ANTIGA reimplementada em Python (fold + mesmos predicados + scan de 5000 + elif de decoração). Resultado: **`old_ids ⊆ new_ids` em TODAS as queries — zero regressão** (nenhuma linha que o antigo devolvia sumiu). Confirmado: (a) roles pesquisáveis idênticos (tool_call/system_notice/system/conversation_event fora; private_note/transcription/error dentro); (b) `match_snippet`/`match_msg_id` só nas linhas que casaram SÓ por conteúdo (linha que casa por nome NÃO decora); (c) snippet = mensagem mais recente que casa, com texto acentuado original preservado; (d) escopo arquivado correto; (e) `q` vazia não filtra; 1-char não toca conteúdo; coringas viram literais; `include_messages=False` tira só o ramo de conteúdo; paginação SQL internamente consistente (`página == full[slice]` no sort determinístico `name`). **Única diferença observável** = o superconjunto DELIBERADO e DOCUMENTADO: um GRUPO cujo `contacts.name` CRU casa a busca (mas o `group_name`/nome shaped não) agora aparece — só ADICIONA resultados, nunca remove; coerente com o docstring de `build_q_clause` e o item "Superfície de nome". A não-determinância de empate no sort `recency` (`coalesce(ts,updated_at)` sem tiebreak) é pré-existente (mesmo `ORDER BY` no antigo e no novo), não introduzida pela F5. Re-rodados contra o `whatsbot_test_a`: caracterização **13/13**, `test_busca_sql_paginacao` **19/19**.

---

### Fase F6 — Sidebar paginada no modo busca 🟢 [depende de F5]

**Objetivo:** acabar com o payload multi-MB do modo busca.

Itens:
1. `[sequencial]` [useConversationList.js:98-121](../web/static/js/components/contacts/hooks/useConversationList.js#L98): com `q`, usar o envelope paginado (`getContacts(q, archived, {limit: SIDEBAR_PAGE, offset})`) com scroll infinito (remover o `setHasMore(false)` de [:118](../web/static/js/components/contacts/hooks/useConversationList.js#L118); sentinela de [ContactList.js:653](../web/static/js/components/contacts/ContactList.js#L653) deixa de excluir `search`).
2. `[paralelo]` Reavaliar G12 (virtualização/memoização) — provavelmente desnecessário com páginas de 50.

**Pronto quando:** buscar `a` na sidebar transfere ≤ ~100 KB por página (DevTools); scroll carrega mais resultados.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (2026-07-20) — frontend only; depende da paginação real da F5 no caminho com `q`
- **O que foi feito:** (1) [useConversationList.js](../web/static/js/components/contacts/hooks/useConversationList.js): o modo BUSCA passou a pedir `getContacts(q, false, {limit: SIDEBAR_PAGE, offset})` e a ler o envelope `{items, total, has_more}` — some o payload único com TODOS os matches; `setHasMore(false)` deu lugar ao `has_more` do servidor. (2) `loadMore` deixou de sair cedo com `searchRef.current` e ganhou o ramo de busca (`loadSearchPage`), preservando token de sequência + AbortController da F3. (3) A janela de atendimentos (`listConversations limit 200`) é buscada UMA vez por query e fixada em `searchConvsRef` — as páginas 2+ cruzam contra a MESMA janela. (4) [ContactList.js](../web/static/js/components/contacts/ContactList.js): sentinela passou de `hasMore && !search` para `hasMore`. (5) Cache de 30s da F3 REMOVIDO (ver decisões); comentário correspondente em [useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js) atualizado para não descrever um mecanismo que não existe mais.
- **Como foi feito / decisões:**
  - **Cache de 30s (item 4 do escopo): removido, não re-chaveado.** Ele existia só porque a busca vinha inteira num payload de ~4 MB; com página de 50 o refetch é barato, e manter o cache (mesmo chaveado por `q|offset`) só serviria à página 1 e ainda introduziria até 30s de defasagem em nome/preview após evento WS. O refetch por WS com busca ativa agora recarrega a 1ª página e descarta as páginas já roladas — limitação aceita e documentada no comentário.
  - **`offsetRef` conta CONTATOS lidos** (unidade da paginação do servidor), nunca linhas produzidas — o cruzamento é n:m. O token de sequência é checado ANTES de qualquer escrita em `offsetRef`, então uma página que volta depois de a query mudar não corrompe o cursor da query nova.
  - **`loadedQueryRef` (novo):** o modo do `loadMore` passou a ser decidido pela query que produziu a lista CARREGADA, não por `searchRef` (que muda a cada tecla). Sem isso havia uma janela real de 300 ms (o debounce da busca) em que `searchRef` já era a query nova enquanto as linhas/o cursor ainda eram do modo conversa-first — rolar ali anexaria linhas de busca sobre a lista errada com o cursor errado.
  - **`SEARCH_EMPTY_PAGE_CHAIN = 3` (novo):** o cruzamento é dirigido por CONTATOS, mas só emite linha para contato cujo atendimento está na janela de 200 (ou que não tem atendimento nenhum) — uma página inteira de 50 contatos pode render ZERO linhas. Como o `IntersectionObserver` só re-dispara quando o DOM muda, uma página que não anexa nada CONGELARIA o scroll com `hasMore` ligado. Então uma página vazia encadeia automaticamente a próxima (até 3 seguidas) e, esgotado o crédito, `hasMore` vai a `false` — fim limpo de lista em vez de lista travada.
  - **Sem duplicar/perder linhas no append (item 5 do escopo):** páginas de contatos são disjuntas e a janela de atendimentos é FIXA por query, então o cruzamento por página não repete nem re-embaralha; a dedup por `rowKeyFor` (`conv:<id>` / `phone:<phone>`, a mesma chave do Preact e da seleção) fica como cinto de segurança contra o re-ordenamento que qualquer paginação por offset sofre se chegar mensagem nova durante a rolagem.
  - Mantido `archived=false` no `getContacts` da busca (plano 54 — a view é decidida pelo filtro de ATENDIMENTOS, `buildRows` honra `archivedView`); o plano escreve `getContacts(q, archived, …)` como abreviação.
  - Mantido `include_messages` no default (`true`): a sidebar RENDERIZA o trecho casado e usa `match_msg_id` no `onSelect`.
- **Problemas / pendências:**
  - **Limitação pré-existente, não introduzida aqui (mas agora mais visível):** o universo de linhas da busca é capado pela janela de `listConversations limit 200` — contato que casa `q` mas cujo atendimento está fora das 200 conversas mais recentes da view não vira linha (`buildRows` faz `continue`). Antes isso ficava escondido porque todos os contatos eram cruzados de uma vez; agora aparece como páginas vazias (absorvidas pelo encadeamento) e, no limite, como fim de lista antecipado. **A correção de verdade é servidor-side** (buscar ATENDIMENTOS por `q`, não contatos × janela) — candidata a uma fase futura; fora do escopo frontend-only da F6.
  - Enquanto o encadeamento roda com a página 1 vazia, a tela mostra "Nenhum contato encontrado" + "Carregando mais…" ao mesmo tempo (cosmético).
  - Uma página cujas linhas sejam TODAS duplicatas (só possível se a ordenação do servidor mudar entre páginas) não encadeia — o encadeamento olha as linhas produzidas, não as efetivamente anexadas. Corner case de corner case; o mesmo artefato de offset pagination já existe no modo conversa-first.
  - **Follow-up (item 6, deliberadamente NÃO feito):** a tela Contatos ([ContactsListScreen.js](../web/static/js/components/ContactsListScreen.js)) pode passar `include_messages=false` (param criado pela F5) — ela não renderiza `match_snippet`. Fora do escopo desta fase.
  - **Dependência da F5:** a paginação por offset só é correta se `list_contacts_page` com `q` tiver ordenação TOTAL e determinística (empate desempatado por `id`); caso contrário páginas vizinhas repetem/pulam contatos. A dedup por `rowKeyFor` mascara a repetição, mas não o pulo.
- **Verificação:** (a) `node --input-type=module --check < <arquivo>` (a forma via stdin, por causa do gotcha de crase em comentário dentro de `html\`…\``) nos 3 arquivos alterados — OK; um comentário com crase em `ContactList.js` de fato quebrou o módulo no 1º check e foi corrigido; (b) `for t in $(find web/static/js -name '*.test.js'); do node --test "$t"; done` — 13/13 arquivos verdes, `# fail 0` em todos; (c) releitura do diff caçando deps de hook (`loadSearchPage` é `[]`, só lê refs; `loadMore` é `[hasMore, loadSearchPage]`), closure stale (resolvida com `loadedQueryRef`), offset duplicado (token antes da escrita) e append após troca de query (token + abort); (d) conferido contra o diff em voo da F5 que o envelope `{items, total, has_more}` e o default `include_messages=true` batem com o que o cliente assume. Sem execução do app (serviço parado de propósito) — validação de runtime na interface fica para o teste manual com DevTools (critério "≤ ~100 KB por página").

---

### Fase F7 — Avatar sweep leve 🟢 [Wave 2; independente]

**Objetivo:** tirar a query pesada e o N+1 da carga de fundo contínua.

Itens:
1. `[sequencial]` [background.py:219-220](../server/background.py#L219): trocar `list_contacts('')` (full pesado, 2×) por uma listagem mínima (`id, phone, is_group`) — função nova e barata no repo.
2. `[sequencial]` [background.py:234-235](../server/background.py#L234): resolver `channel_id` de TODOS os contatos numa única query batch (DISTINCT ON/LATERAL sobre `conversations`) em vez de 2 queries por contato (~29k queries/sweep hoje).
3. `[paralelo]` Rever o ritmo: sweep atual (~2 h com `sleep(0.5)` × 14,5k) já excede `AVATAR_REFRESH_INTERVAL=1800s` — documentar/ajustar para ciclo alinhado.

**Pronto quando:** log do sweep sem chamadas a `list_contacts`; nº de queries por sweep cai de ~29k para ~2 + 1/avatar baixado.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** (1) `contact_repo.list_avatar_targets()` — SELECT simples de `id/phone/is_group/is_archived` de TODOS os contatos (arquivados incluídos), sem joins — substitui as 2 chamadas full de `list_contacts` no sweep. (2) `conversation_repo.latest_channel_id_by_contact(contact_ids)` — batch em UMA query (`DISTINCT ON (contact_id)` + `ORDER BY contact_id, last_activity_at DESC, id DESC` + outer join `inboxes`) espelhando a semântica de `channel_id_for_contact` (que foi mantido — sem outros callers, mas API pública preservada). (3) Loop do sweep em `server/background.py` reescrito: 1 query de lista + 1 query batch por sweep; `sleep(0.5)` continua por tentativa de refresh (contato COM canal), erro por contato segue isolado (`logger.debug`), broadcast `avatar_updated` intacto via `refresh_and_broadcast`.
- **Como foi feito / decisões:** "mais recente" = `last_activity_at DESC` (semântica real de `get_latest_for_contact`, NÃO `id DESC` — o `id DESC` entrou só como tiebreak determinístico); contato sem conversa fica AUSENTE do dict (o loop pula, igual ao `None` antigo); conversa com inbox/canal removido mapeia para `None` (outer join, igual ao enriched). Batch é best-effort (`{}` em falha, nunca levanta). `AVATAR_REFRESH_INTERVAL` inalterado; adicionado comentário no sleep documentando que com ~14k contatos o passo excede o intervalo (o intervalo vira piso entre sweeps, não agenda fixa) — item 3 resolvido como documentação, sem mudar timing.
- **Problemas / pendências:** nenhuma. O `IN` com ~14k ids fica bem abaixo do teto de bind params do Postgres; se a base crescer muito, chunkar é trivial.
- **Verificação:** (a) grep: zero chamadas de `list_contacts` em `server/background.py`; (b) `pytest tests/characterization/test_busca_contatos_characterization.py` — 13 passed; (c) `python tests/test_gowa_plugin.py` (cobre o wiring do `avatar_fetch` task) — 109 passed; (d) teste novo `tests/test_latest_channel_id_batch.py` — 5 passed (2 conversas → canal da mais recente; sem conversa → ausente; input vazio; equivalência batch × `channel_id_for_contact` 1-a-1 no mesmo dataset). Banco de teste `whatsbot_test_a`.

---

### Fase F8 — Higiene de infra do Postgres (opcional, janela combinada) 🟢

**Objetivo:** telemetria e guard-rails; nada aqui é pré-requisito das outras fases.

| Item | Ação | Cuidado |
|------|------|---------|
| `pg_stat_statements` + `track_io_timing` | `shared_preload_libraries` + **restart do Postgres** + `CREATE EXTENSION` | ⚠️ Cluster compartilhado (Chatwoot/Nexus) — janela combinada (P3) |
| `statement_timeout` | Por role/app da aplicação (ex.: 30 s) — não global | Evita queries fugitivas seguraram conexões (hoje 0 = sem teto) |
| `random_page_cost` | 4 → 1.1 se storage SSD (a confirmar) | Favorece os índices novos nos planos |
| Índices mortos (`idx_scan=0`): `idx_contacts_cattr_gin` 3,9MB, `idx_contacts_updated`, `recon_cw_*`/`watermark_cw_*` (sobras da migração Chatwoot), `idx_pp_proto_assignee` | Observar 30 dias pós-mudanças e dropar | ⚠️ `idx_contacts_updated`: confirmar se a query nova (coalesce com `updated_at`) passa a usá-lo antes de dropar |
| `atendimentos` 49,5% dead tuples / 1,1% HOT | `fillfactor=80` e/ou autovacuum por tabela | Tabela pequena (10 MB) — cosmético |
| Sessão psql manual ativa há ~1h40 (pid 738041, conferência `cw_id` inbox 21) | Confirmar com o dono (provavelmente sessão de conferência da migração Chatwoot deste próprio time) e encerrar | Não é do WhatsBot; compete por CPU |

**Pronto quando:** decisões P3/itens aplicados registrados aqui, com janela e resultado.

#### Status de execução — Fase F8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(…)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Reescrita LATERAL muda semântica em empate de `ts` | Self-join `MAX(ts)` pode duplicar contato em empate; LATERAL retorna 1 row | F0 congela o comportamento; tratar dedupe como correção documentada (teste explícito) |
| `fold()` Python ≠ `unaccent` SQL em 100% dos casos | `casefold()`+NFKD cobre mais que `lower()`+unaccent (ex.: ß→ss, ligaturas) | F0 com casos acentuados PT-BR; divergências exóticas documentadas como aceitas; F5 só substitui o matching depois da paridade provada nos casos reais |
| Migration roda no boot do deploy (Coolify) | `CREATE INDEX` segura lock de escrita em `messages` durante o boot | Índice btree F2 = segundos (aceitável). O GIN trigram de F4 em `messages` é mais lento: preferir criação **manual CONCURRENTLY fora do boot** + migration idempotente (`IF NOT EXISTS`), OU `autocommit_block()` do Alembic — decidir em P2 |
| Dev-server com `--reload` observa `db/` e aplica `alembic upgrade head` no banco vivo do checkout ao salvar migration | Migration incompleta aplicada sem querer | Parar o serviço antes de criar/editar migrations; nunca renomear migration aplicada |
| `alembic_version` varchar(32) | Revision id longo estoura no upgrade | Ids curtos; nome descritivo vai no filename |
| `CREATE EXTENSION` exige privilégio | Deploy falha se o user do app não puder | P5: confirmar user; fallback = criar extensão manualmente antes, migration idempotente |
| Ordenação `sort=name`/`recency` deve permanecer byte-idêntica | UI depende da ordem (pinned no topo etc.) | Asserções de ordem no F0 |
| Buscas concorrentes durante a transição | Deploy meio-aplicado | Cada fase é atômica por commit/deploy; F1 não muda API nem shape (exceto `msg_count`→0, sem consumidor) |
| `prepare_threshold=None` (PgBouncer compat) | Sem plano cacheado — cada query re-planeja | Efeito positivo aqui (pega o plano novo na hora); nada a fazer |
| Postgres compartilhado com Chatwoot/Nexus | Restart (F8) afeta outros sistemas | F8 é opcional e com janela combinada; Waves 0-1 não exigem restart |

---

## 6. Perguntas em aberto

| # | Pergunta | Estado | Contexto / recomendação |
|---|----------|--------|--------------------------|
| P1 | `lm`/`conv` como (a) LATERAL ou (b) DISTINCT ON? | ⏸️ ADIADO (decidir no F1) | Medido: LATERAL 280 ms × DISTINCT ON 408 ms. **Recomendação: (a) LATERAL** — mais rápido, mesmo padrão do hub saudável (gabarito), e o planner o estima bem. DISTINCT ON é fallback se o `.lateral()` do SQLAlchemy Core complicar a manutenção. |
| P2 | Índice GIN trigram em `messages.content`: criar no boot (migration pura) ou manual CONCURRENTLY + migration idempotente? | ⏸️ ADIADO (decidir no F4) | Tabela de 115 MB heap; GIN build pode levar dezenas de segundos segurando escrita. **Recomendação: (b) manual CONCURRENTLY** fora do horário de pico + migration `IF NOT EXISTS`. |
| P3 | Instalar `pg_stat_statements` (requer restart do cluster compartilhado)? | ⏸️ ADIADO | Vale muito para monitorar o pós-mudança, mas exige janela combinada com Chatwoot/Nexus. **Recomendação: sim, na primeira janela de manutenção.** |
| P4 | Micro-cache TTL (2–5 s) por `(q, archived, inbox_ids)` no backend? | ⏸️ ADIADO | Pós-F1/F5 a query fica sub-segundo e paginada — cache provavelmente desnecessário. Reavaliar só se sobrar lentidão com múltiplos operadores. |
| P5 | O user do `DATABASE_URL` da instância prod pode `CREATE EXTENSION`? | ⏸️ A CONFIRMAR (antes do F4) | O diagnóstico usou `postgres` (superuser), mas o app pode conectar com outro user. Checar `SELECT current_user` pela app ou a env no Coolify. |
| P6 | Aposentar o caminho legado sem-limit de `GET /api/contacts` (sem `q`)? | ⏸️ ADIADO (pós-F6/F7) | Depois de F6 (sidebar paginada) e F7 (sweep sem list_contacts), o único caller full que resta deve ser auditado; se zero, capar `limit` no endpoint como guard-rail. |

---

## 7. Apêndice — arquivos-chave

**Backend / SQL**
- [db/search/contact_search.py](../db/search/contact_search.py) — builder da query pesada (F1), scan de conteúdo (F2/F5), cláusula `q` nova (F5)
- [db/repositories/contact_repo.py](../db/repositories/contact_repo.py) — `list_contacts_page` ramo `q` (F5), shape `msg_count` (F1)
- [db/repositories/_mapping.py](../db/repositories/_mapping.py) — `_PREVIEW_EXCLUDED` (predicados dos índices parciais)
- [server/routes/contacts.py](../server/routes/contacts.py) — endpoint, param `include_messages` (F5), teto de `limit` (P6)
- [server/background.py](../server/background.py) — avatar sweep (F7)
- [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — `channel_id_for_contact` batch (F7); gabarito keyset
- [db/alembic/versions/](../db/alembic/versions/) — migrations F2/F4

**Frontend**
- [web/static/js/components/ContactsListScreen.js](../web/static/js/components/ContactsListScreen.js) — debounce (F3), pós-criação (F3), `include_messages=false` (F5)
- [web/static/js/components/contacts/hooks/useConversationList.js](../web/static/js/components/contacts/hooks/useConversationList.js) — token de sequência (F3), busca paginada (F6)
- [web/static/js/components/contacts/hooks/useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js) — refetch barato com busca ativa (F3)
- [web/static/js/services/api.js](../web/static/js/services/api.js) — `signal`/AbortController (F3), opts de paginação
- [web/static/js/components/contacts/NewConversationModal.js](../web/static/js/components/contacts/NewConversationModal.js) — limit (F3)
- [web/static/js/components/contacts/ContactList.js](../web/static/js/components/contacts/ContactList.js) — sentinela de scroll no modo busca (F6)
- [web/static/js/hooks/useInfiniteScroll.js](../web/static/js/hooks/useInfiniteScroll.js) — interação com debounce/abort (F3)

**Testes**
- [tests/test_endpoints.py](../tests/test_endpoints.py) + caracterização nova (F0) — contra `WHATSBOT_TEST_DB_URL` (nome do banco precisa conter `test`)

---

## 8. Checklist de verificação (aplicar a cada fase)

- [ ] Caracterização F0 verde (mesmos resultados de busca, mesma ordem)
- [ ] `tests/test_endpoints.py` verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`)
- [ ] `node --test` nos módulos JS puros alterados
- [ ] `EXPLAIN (ANALYZE, BUFFERS)` antes/depois colado no Status de execução da fase (produção, read-only)
- [ ] Migration round-trip (upgrade + downgrade) no banco de teste; revision id ≤32 chars
- [ ] Deploy: boot do container aplica `alembic upgrade head` sem lock prolongado (F4: índice GIN criado CONCURRENTLY antes, se P2=b)
- [ ] Validação manual: digitar nas DUAS barras com DevTools Network aberto — nº de requests, tamanho de payload, tempo de resposta
- [ ] Limpar a busca durante um request em voo — a lista NÃO pode regredir para o resultado velho
- [ ] Sem segredo/senha em URL ou log; acesso a dados só via SQLAlchemy Core com bind params

---

# 9. HANDOFF — como continuar este plano (leia isto primeiro)

> Esta seção é auto-contida: um agente novo consegue retomar o trabalho lendo só ela + as seções 4 (fases) e 5 (riscos). Escrita em 2026-07-20, após a execução de F0–F4 e F7.

## 9.1 — Onde o trabalho parou

| Fase | Estado | Commit | Arquivos |
|---|---|---|---|
| F0 caracterização | ✅ feito | `6aa7e0d` | `tests/characterization/test_busca_contatos_characterization.py` (399 linhas, 13 testes) |
| F1 LATERAL | ✅ feito | `729160e` | `db/search/contact_search.py` |
| F2 índice `ts` | ✅ feito | `fc12929` | `db/alembic/versions/20260720_0059_idx_msg_ts_visible.py`, `db/tables.py` |
| F3 frontend | ✅ feito | `c6c846b` | `ContactsListScreen.js`, `NewConversationModal.js`, `useConversationList.js`, `useConversationWsEvents.js`, `api.js`, `httpClient.js` |
| F4 trigram/unaccent | ✅ feito (código) · ⏸️ **DDL NÃO aplicado em produção** | `252f27b` | `db/alembic/versions/20260720_0060_trgm_unaccent_search.py`, `db/tables.py` |
| F5 busca no SQL | ✅ feito — 3 lentes adversariais (paridade/segurança/perf); fix de perf 2-char aplicado · ⏸️ **deploy bloqueado até o DDL da §9.4** (sem `f_unaccent` a busca dá 500) | `69be898` | `db/search/contact_search.py`, `db/search/__init__.py`, `db/repositories/contact_repo.py`, `server/routes/contacts.py`, `tests/test_busca_sql_paginacao.py` |
| F6 sidebar paginada | ✅ feito — fix da race cross-modo aplicado após revisão | `c5b9dca` | `useConversationList.js`, `ContactList.js`, `useConversationWsEvents.js` |
| F7 avatar sweep | ✅ feito | `04ff820` | `contact_repo.py`, `conversation_repo.py`, `server/background.py`, `tests/test_latest_channel_id_batch.py` |
| F8 infra | ⬜ adiado — exige janela (restart do Postgres compartilhado) | — | — |
| Doc do plano | ✅ | `e90e67a` | este arquivo |

**⚠️ Nada foi para produção.** Os commits estão só na branch local `developer` — **sem `git push`**. Como o Coolify faz deploy automático no push, o push é decisão do Thiago. O banco de produção também **não** recebeu nenhum DDL.

## 9.2 — Estado do ambiente

- **`whatsbot.service`** (dev, systemd) está **rodando** na porta **8090** (`linux_start.sh`, uvicorn `--reload`). Só sobe/para com `sudo systemctl`.
- ⚠️ Ele observa `db/` e roda `alembic upgrade head` **no banco de DEV** ao salvar migration. **Pare o serviço antes de criar/editar migrations.** As migrations 0059 e 0060 já foram aplicadas ao banco de dev por esse caminho.
- Head do Alembic: **`0060_trgm_unaccent_search`** (head único).
- A API de dev exige autenticação (retorna 401 sem token) — para medir latência, use a camada de repo direto (ver 9.5), não o HTTP.

## 9.3 — Credenciais dos bancos

As senhas ficam em **`.env.plano62-credenciais`** na raiz do repo — arquivo **gitignored** (`.gitignore:18` cobre `.env.*`). **Nunca copie senha para dentro de `docs-planos/`** ou de qualquer arquivo rastreado: o histórico do git é permanente e este repositório vai para o GitHub.

| Ambiente | Host | Banco | Observação |
|---|---|---|---|
| **Produção** (Empresa Exemplo / Coolify) | `203.0.113.30:5432` | `whatsbot` | Cluster **compartilhado** com Chatwoot e Nexus. Dono: `ExemploDB_owner`. **Somente leitura** salvo autorização explícita |
| Dev (o `whatsbot.service` aponta pra cá) | `203.0.113.60:5432` | `whatsbot` | URL canônica no `.env` da raiz |
| Teste (suíte) | `203.0.113.60:5432` | `whatsbot_test` | `WHATSBOT_TEST_DB_URL`; nome precisa conter `test` (o runner faz `DROP SCHEMA public`) |
| Teste paralelo A / B | `203.0.113.60:5432` | `whatsbot_test_a`, `whatsbot_test_b` | Criados para rodar suítes simultâneas sem colisão |

**Investigar produção — sempre nesta forma (read-only forçado):**
```bash
source .env.plano62-credenciais
export PGOPTIONS='-c default_transaction_read_only=on'
PGPASSWORD="$PROD_PGPASSWORD" psql -h "$PROD_PGHOST" -U "$PROD_PGUSER" -d "$PROD_PGDATABASE"
```

## 9.4 — DDL pendente em produção (pré-requisito da F5)

Decisão P2=b: os objetos da F4 são criados **manualmente com `CONCURRENTLY`** fora do boot (o GIN sobre `messages` é grande demais para segurar lock durante o deploy). A migration `0060` é idempotente (`IF NOT EXISTS`), então vira no-op quando o container subir.

**Requer autorização explícita do Thiago — é escrita no banco vivo.** `CONCURRENTLY` não pode rodar dentro de transação (não use `psql -1`), e a sessão **não** pode ter `default_transaction_read_only`:

```sql
-- 1) extensões (trusted; o dono do banco cria sem superuser)
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2) wrapper IMMUTABLE (a unaccent() nativa é STABLE e não indexa)
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
  AS $$ SELECT public.unaccent('public.unaccent', $1) $$
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT;

-- 3) índices trigram (CONCURRENTLY: lento, mas sem lock de escrita)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_contacts_name_trgm
  ON contacts USING gin (f_unaccent(lower(name)) gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_msg_content_trgm
  ON messages USING gin (f_unaccent(lower(content)) gin_trgm_ops)
  WHERE role NOT IN ('tool_call','system_notice','conversation_event','system');

-- 4) marcar a migration como aplicada (evita o boot tentar de novo)
--    só se o container ainda NÃO subiu com o código novo:
-- UPDATE alembic_version SET version_num = '0060_trgm_unaccent_search';
```
Depois: `ANALYZE contacts; ANALYZE messages;` e confirmar com `\di+ idx_*_trgm` que os índices ficaram `valid` (um `CONCURRENTLY` que falha deixa índice inválido — dropar e refazer).

> **⚠️ ORDEM IMPORTA — não dê `git push` antes deste DDL.** A revisão de performance da F5 mediu (dataset de 14k contatos / 600k mensagens): **com `f_unaccent` mas SEM os índices trigram**, toda busca por conteúdo cai em Parallel Seq Scan das mensagens (leticia 832 ms, orçamento 2.167 ms em pure-exec no teste — pior em produção, com mensagens mais longas e heap de 115 MB). E **sem a função `f_unaccent`**, a busca retorna erro 500 (`function does not exist`). Ou seja: deploy antes do DDL = **busca quebrada ou degradada a full-scan**. Aplique o DDL (passos 1–3) e o `ANALYZE`, confirme os índices `valid`, e só então faça o push.
>
> **`idx_contacts_name_trgm` é PESO MORTO — pode pular o passo 3 dele.** A mesma revisão provou por `EXPLAIN` que o planner **nunca** usa esse índice na busca: como o `WHERE` é um `OR` com ramos não-indexáveis (`group_name`, `phone LIKE`, subplano de tags), ele faz Index Scan em `idx_contacts_archived` + Filter e o GIN de `name` fica ocioso — mantido em todo INSERT/UPDATE de contato sem nunca acelerar nada. O índice de `contacts` é pequeno (a migration 0060 o cria no boot de qualquer jeito, custo de segundos), então **não precisa criá-lo com `CONCURRENTLY`**; deixe o boot criá-lo. O único índice que **precisa** ser pré-criado com `CONCURRENTLY` é o `idx_msg_content_trgm` (sobre `messages`, 115 MB). Candidato a `DROP` futuro se a manutenção de escrita incomodar (fora do escopo deste plano).

## 9.5 — Como medir (o que provou os ganhos)

**Latência real da busca contra os dados de produção** (roda o código Python de verdade, read-only):
```bash
source .env.plano62-credenciais
PGOPTIONS='-c default_transaction_read_only=on' venv/bin/python -c "
from db.engine import init_engine; init_engine('$PROD_DATABASE_URL')
from db.repositories import contact_repo
import time
a=time.perf_counter(); r=contact_repo.list_contacts_page(q='maria', limit=15, sort='name')
print(f'{(time.perf_counter()-a)*1000:.0f} ms · itens={len(r[\"items\"])} total={r[\"total\"]}')"
```
⚠️ `init_engine()` exige a URL como argumento posicional (não lê `DATABASE_URL` sozinha).

**Compilar o SQL real para `EXPLAIN`:**
```bash
venv/bin/python -c "
from sqlalchemy.dialects import postgresql
from db.search import contact_search
s = contact_search.build_list_contacts_query(archived=False, inbox_ids=None, sort='recency')
print(str(s.compile(dialect=postgresql.dialect(),
      compile_kwargs={'literal_binds': True, 'render_postcompile': True})) + ';')"
```

**Baseline × atual (medido, mesma máquina e dados):**

| Caminho | Antes | Depois |
|---|---|---|
| Listagem sem busca (limit 50 / limit 15) | ~35,8 s | **213 ms / 136 ms** |
| Busca por nome (limit 15) | ~20 s | **1.102 ms** |
| Busca sem limit (628 matches) | ~20 s | **1.268 ms** |
| Busca por conteúdo de mensagem | ~20 s | **1.069 ms** |
| `EXPLAIN` da query base, produção | 19.618 ms | **196 ms** (sem limit) / **135 ms** (limit 15) |

## 9.6 — Como rodar os testes (e o que já falha de antes)

```bash
# suíte da busca (o guarda-costas das próximas fases) — 13 testes
venv/bin/python -m pytest tests/characterization/test_busca_contatos_characterization.py -q
# resolvedor batch do sweep (F7) — 5 testes
venv/bin/python -m pytest tests/test_latest_channel_id_batch.py -q
# suíte grande de endpoints: rode como SCRIPT (sob pytest dá SystemExit na coleção)
venv/bin/python tests/test_endpoints.py
# JS puro
for t in $(find web/static/js -name '*.test.js'); do node --test "$t"; done
```
Para rodar duas suítes ao mesmo tempo, prefixe cada uma com `WHATSBOT_TEST_DB_URL=$TEST_DB_URL_A` / `_B` (nunca duas no mesmo banco).

**Falhas PRÉ-EXISTENTES — não são regressão, não perca tempo:**
- `tests/test_endpoints.py`: 2 falhas (`agent_transfer_alert emitido`, `duration da config global`) — reproduzem idênticas no `HEAD` anterior.
- `tests/endpoints/`: 18 falhas no run de diretório (`test_p26`, `test_p27`, `test_p36`) — poluição de estado order-dependent; os mesmos arquivos passam 31/31 em isolamento.
- `tests/test_alembic_hygiene.py`: 2 falhas (a merge `0058_merge_p50_p57` viola o teste de cadeia linear; prefixos duplicados `0037/0042/0043/0046/0052` fora da allowlist). `test_single_alembic_head` está verde.
- `tests/characterization/`: 3 falhas em `test_sandbox_improve_characterization.py` só no run de diretório; 9/9 em isolamento.

## 9.7 — Contexto técnico que não está no código

- **A causa da lentidão era o PLANO, não o volume.** O banco tem 279 MB com 99,87% de cache hit — zero I/O. O self-join `MAX(ts)` fazia o planner estimar `rows=1` (real: 14.540) e cair em Nested Loop+Materialize com ~211M comparações descartadas. `ANALYZE` **não** corrige (é seletividade de join, não estatística velha) — só a reescrita resolve. Prova: `SET enable_nestloop=off` derrubava de 19,6 s para 450 ms.
- **Os índices parciais usam 4 roles** (`tool_call`, `system_notice`, `conversation_event`, `system`) — os do scan de conteúdo em `contact_ids_matching_message`. **Não confunda** com `_PREVIEW_EXCLUDED` (7 roles, inclui `private_note`/`transcription`/`error`), que é o filtro do *preview* da lista. Se mudar um, revise o outro.
- **`msg_count` virou `literal(0)`** na listagem — o COUNT correlato não tinha nenhum consumidor (frontend nem backend). A chave continua no payload só para preservar o shape.
- **Tie-breaker `id DESC`** na lateral `lm`: sem ele, duas mensagens visíveis com `ts` idêntico deixavam a escolha a critério do planner.
- **Volume de produção:** 626.873 mensagens (+~1.154/dia), 14.508 contatos, 14.713 atendimentos. O cap de 5.000 do scan de conteúdo cobre só **~5 dias** de histórico — a F5 remove essa limitação.
- **`pg_stat_statements` não está instalado** (`shared_preload_libraries` vazio) — não há telemetria agregada; toda medição foi por `EXPLAIN` manual.

## 9.8 — Armadilhas descobertas na execução (não repita)

| Armadilha | Como evitar |
|---|---|
| Um subagente rodou `git stash pop` e restaurou um stash antigo alheio, sujando ~30 arquivos | **Proibir comandos git de escrita** em qualquer agente paralelo (`commit`/`stash`/`checkout --`/`reset`). Para provar pré-existência de falha, troque o arquivo na mão (`git show HEAD:arq > tmp`), nunca com stash |
| Vários agentes no mesmo checkout se pisando | Dê a cada um uma lista **disjunta** de arquivos e diga explicitamente o que NÃO tocar |
| Duas suítes pytest no mesmo banco de teste | O bootstrap faz `DROP SCHEMA public` por processo → use `whatsbot_test_a`/`_b` |
| Banco de teste novo com acento quebrado | O cluster `203.0.113.60` cria em `SQL_ASCII`; force `ENCODING 'UTF8' TEMPLATE template0` |
| `revision` do Alembic longo demais | `alembic_version.version_num` é `varchar(32)` — id curto, nome descritivo vai no filename |
| Editar migration com o `whatsbot.service` ligado | Ele aplica `alembic upgrade head` no banco de dev ao salvar — pare o serviço antes |
| Crase ou `${}` dentro de comentário em template `html\`...\`` | Fecha o template e quebra o módulo; `node --check` simples dá falso negativo — use `node --input-type=module --check < arquivo` |
| GIN recém-criado parece não ser usado | A *pending list* (fastupdate) faz o planner escolher Seq Scan até o merge; rode `ANALYZE` e re-teste |

## 9.9 — Próximos passos sugeridos, em ordem

1. **Decisão do Thiago: `git push`?** A Wave 0 sozinha entrega o grosso e **não depende de nada em produção**. Recomendado validar antes na interface (as duas barras, com DevTools: nº de requests e tempo).
2. **Decisão do Thiago: aplicar o DDL da 9.4 em produção?** Só necessário se for seguir com a F5.
3. **F5** (seção 4, "Fase F5") — mover o matching de `q` para o SQL com `f_unaccent`/trigram e paginar no banco; elimina o ~1 s residual e o teto de 5 dias da busca por conteúdo. Só comece **depois** do passo 2. Guarda-costas: os 13 testes da F0 precisam continuar verdes; divergências entre o `fold()` Python (`casefold`+NFKD) e o `unaccent` SQL devem ser documentadas.
4. **F6** — sidebar paginada no modo busca (hoje ainda traz todos os matches num payload só).
5. **F8** — higiene de infra, quando houver janela combinada com Chatwoot/Nexus.
