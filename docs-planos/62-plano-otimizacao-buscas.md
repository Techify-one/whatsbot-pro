# Plano 62 — Otimização das buscas (sidebar + tela Contatos): reescrita da query, índices, busca no SQL e frontend

> **Status:** EM EXECUÇÃO — Wave 0 + F4 + F7 CONCLUÍDAS e commitadas (branch `developer`, **sem push**); F5/F6 pendentes; F8 adiado · **Data:** 2026-07-20 · **Escopo:** médio-grande
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
> O ~1 s que sobra na BUSCA é exatamente o que a **F5** ataca (shaping de 14,5k rows + `fold()` Python + scan de conteúdo, tudo fora do SQL). ⚠️ **F5 depende do DDL da F4 estar aplicado em produção** (`CREATE INDEX CONCURRENTLY` manual — P2=b); sem os índices trigram, mover o matching para o SQL pode ficar MAIS lento que hoje.
>
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
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(…)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

### Fase F6 — Sidebar paginada no modo busca 🟢 [depende de F5]

**Objetivo:** acabar com o payload multi-MB do modo busca.

Itens:
1. `[sequencial]` [useConversationList.js:98-121](../web/static/js/components/contacts/hooks/useConversationList.js#L98): com `q`, usar o envelope paginado (`getContacts(q, archived, {limit: SIDEBAR_PAGE, offset})`) com scroll infinito (remover o `setHasMore(false)` de [:118](../web/static/js/components/contacts/hooks/useConversationList.js#L118); sentinela de [ContactList.js:653](../web/static/js/components/contacts/ContactList.js#L653) deixa de excluir `search`).
2. `[paralelo]` Reavaliar G12 (virtualização/memoização) — provavelmente desnecessário com páginas de 50.

**Pronto quando:** buscar `a` na sidebar transfere ≤ ~100 KB por página (DevTools); scroll carrega mais resultados.

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(…)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

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
