# Plano de Implementação — Filtros avançados de conversas (WhatsBot Pro)

> **Status:** PLANO acionável. Deriva da pesquisa em
> [`docs-pesquisa/08-filtros.md`](../docs-pesquisa/08-filtros.md).
> **Tenancy:** uma empresa, servidor único, multi-usuário, **sem multi-tenant**.
> **Escopo deste plano:** API de filtros híbrida (query params no MVP → payload estruturado
> estilo Chatwoot na fase 2), tradução payload→SQL **dialect-agnóstica** (SQLAlchemy Core,
> SQLite + Postgres), tabela `saved_filters`, `GET /api/conversations/filter-schema`,
> integração com os campos novos (status/assignee/inbox via doc 01, tags via tabelas
> existentes, atributos custom via doc 05), recorte por inbox do RBAC (doc 03), e a UI de
> filtros/chips/views salvas na lista de conversas.

---

## Estado atual (WF1, 2026-06-20)

> Reconciliação verificada `arquivo:linha` em `_RECONCILIACAO-WF1.md §"Plano 08"` (working tree
> `b673a61`). **Este plano é GREENFIELD: todas as fases estão `nao_feito`.** Nenhum artefato de filtros
> existe no código (grep vazio: sem `db/filters/*`, sem `GET /api/conversations`, sem `conversation_repo`,
> sem `FilterBar.js`/`Conversations.js`, sem `saved_filters`/`saved_filter_repo`/`server/routes/saved_filters.py`).
> O único caminho de filtro hoje é o **degenerado** `q + archived` em `/api/contacts`
> (`contacts.py:45-48` → `contact_repo.list_contacts:360`).

**Por que o plano permanece como "completude + endurecimento" e não foi reduzido a stub:** o plano
constrói sobre tabelas que **ainda não existem** (`conversations`, `users`, `custom_attributes`). Logo,
o valor está na ESPECIFICAÇÃO detalhada (DDL, allowlist anti-injection, tradução dialect-agnóstica,
endpoints, índices, keyset) que será implementada quando as dependências chegarem. Nada aqui se reconstrói
do zero — o que existe e pode ser **reaproveitado** está catalogado em §0.

**Posição na sequência viva (relatório §4 / reconciliação §1.3 item 25):** este plano é **Onda 5+**.
A sequência completa é:

| Onda | Conteúdo | Relação com o 08 |
|---|---|---|
| **0** | Endurecimento do que já shippou (pin `agno`, popular `executions`, `server/dev.py`, `CLAUDE.md`) | — |
| **1** | Plano 09 — `SubprocessService` (fundação runtime) | — |
| **2** | Retrofit P62 — isolar o code-in-DB sobre o `SubprocessService` | — |
| **3** | RBAC (03) + Inbox/Conversas (01) | **destrava o valor real do 08** (`conversations`, `users`, `current_user`) |
| **4** | Completar o motor 06 (binding inbox, handoff/routing) | — |
| **5+** | 02, 04, 05 (independentes) → **08 entra DEPOIS de 01/05/03** | este plano |

**Legenda de estado por fase (verificado WF1):**

| Fase | Estado | Resumo |
|---|---|---|
| **0 — deps (01/02/03/05)** | `nao_feito` | Nenhuma tabela base existe (`grep` vazio em `db/`). |
| **§1 — módulo `db/filters/*`** | `nao_feito` | Greenfield. Reaproveita switch de dialeto (`db/upsert.py:26-27`) e busca textual (`contact_repo._contact_ids_matching_message:324`). |
| **1 — MVP (query params)** | `nao_feito` | Sem `GET /api/conversations`. |
| **2 — query builder (POST /filter)** | `nao_feito` | Sem `POST /api/conversations/filter`. |
| **3 — views salvas (`saved_filters`)** | `nao_feito` | Sem tabela/repo/rotas. Migration vai para o head real (≥0009). |
| **4 — refinos (WS/ordenação)** | `nao_feito` | — |
| **§5–8 — SQL/perf/RBAC/WS** | `nao_feito` | Regras transversais; nada implementado. |

> **Decisões já incorporadas (não re-litigar):** P76, P77, P78, P79, P80, P81 (+ FQ4 que o encerra),
> P82, P83, P55, P56. As do Lote 3 (P62/P64/P65/P67) **não tocam este plano diretamente** — pertencem
> aos planos 06/09 — mas a regra de Alembic linear (P82) que elas reforçam vale aqui (ver §4.2).

---

## 0. Pontos de integração reais (confirmados no código, drift de linha corrigido)

> **Drift WF1:** os planos foram escritos sobre um snapshot pré-AGNO. Os offsets antigos estão
> marcados como ~aprox.; use as âncoras por `grep` (nome de função / rota) na implementação, nunca a
> linha hardcoded.

- **`db/tables.py:41` (`contacts = Table(...)`)** — `contacts`: hoje só `is_archived`, `is_pinned`,
  `unread_count`, `has_unread_mention`, `updated_at`. **Não existem** `status`, `assignee_id`,
  `inbox_id`, `custom_attributes`. As entidades filtráveis são criadas pelos docs 01/02/05.
- **`db/tables.py:116` (`tags`) / `:125` (`contact_tags`)** — `tags` (`id`, `name` UNIQUE, `color`) +
  `contact_tags` (N:N `contact_id`/`tag_id`). Já existem. Falta o **filtro por tag** (hoje só
  exibição/ação em massa). O índice `idx_ct_tag` (`contact_tags.c.tag_id`) ajuda a subquery por tag.
- **`db/tables.py:324` (`CORE_TABLES = frozenset(t.name for t in metadata.sorted_tables)`)** — *(plano
  citava `:207`; ~117 linhas de drift por causa das tabelas `ai_*` adicionadas no `0007`)*. Qualquer
  `Table` novo (ex.: `saved_filters`) entra automaticamente na migração SQLite→Postgres.
- **`db/repositories/contact_repo.py:360` (`def list_contacts(q, archived)`)** — monta **SQL raw via
  `sqlalchemy.text()`** (`SELECT c.*` + LEFT JOIN da última mensagem + subqueries de contagem),
  `WHERE c.is_archived = :archived`. O `q` casa em nome/telefone e, via
  `_contact_ids_matching_message` (`:324`, usada em `:446`), no conteúdo de mensagens. **Único critério
  hoje: archived + texto.** É o ponto a estender/duplicar para o caminho de filtros — **sem reescrever**.
- **`server/routes/contacts.py:45-48` (`@app.get("/api/contacts")` → `list_contacts(q="", archived=False)`)**
  — é onde os params novos do MVP entrariam **se** fosse o endpoint legado; por P76 os filtros novos vão
  no caminho de conversas, não aqui.
- **`server/routes/contacts.py:28` (`register_routes(app, deps)`)** — `deps` expõe `agent_handler`,
  `gowa_client`, `ws_manager`, `state`, `settings`. Padrão de registro de todos os módulos de rota.
- **`server/app.py:18` (import) / `:328-344` (`*.register_routes(app, deps)`)** — *(plano citava
  `:304-319`; ~24 linhas de drift)*. Um módulo novo (`server/routes/saved_filters.py`) entra:
  - import na linha 18 (lista `from server.routes import logs, sandbox, config, … ai_engine`);
  - chamada `saved_filters.register_routes(app, deps)` no bloco `:328-344` (hoje termina em
    `ai_engine_routes.register_routes(app, deps)` na `:344`).
- **`db/upsert.py:26-27` (`_insert_for_current_dialect()` → `get_engine().dialect.name`)** —
  `sqlite`/`postgresql`. **Precedente exato** do switch por dialeto que a §5 (JSON) e os índices
  parciais vão reusar.
- **`db/alembic/versions/`** — **cadeia real verificada (HEAD = `0008_plugin_installed_deps`)**:
  ```
  0001_baseline → 0002_message_revoked → 0003_message_reactions → 0004_message_reply_to
    → 0005_contact_pinned → 0006_contact_mention → 0007_ai_engine_tables
    → 0008_plugin_installed_deps   (HEAD)
  ```
  **Os slots 0007 e 0008 JÁ FORAM CONSUMIDOS** (AGNO + `pkg_deps`). A migration `saved_filters`
  **não** usa 0006/0007/0008 — encadeia linear a partir do head real no momento de implementar (ver §4.2).
- **Frontend** — `web/static/js/components/contacts/Contacts.js`: `search`, `showArchived` + ref,
  `fetchContacts(q)` chama `getContacts(q, showArchived)`, recarga em `[showArchived]` e debounce 300ms
  em `[search]`. `globalTags`/`handleBulkTag` são para ação em massa, não filtro. *(offsets antigos
  `:17/:28/:284/:287-289/:345/:367-371/:208` aproximados — confirmar por grep; o doc 01 deve criar
  `Conversations.js` ao lado.)*
- **`web/static/js/services/api.js`** — `getContacts(q, archived)` monta query com `q` e `archived`
  apenas *(offset antigo `:132-137`, aproximado)*. Ponto a estender / acrescentar
  `getConversations(params)` / `filterConversations(payload)`.

**Conclusão:** o filtro de hoje é degenerado (1 booleano + 1 texto). Este plano constrói o
filtro multi-dimensional **assumindo a conversa como unidade filtrável** (doc 01), com graceful
degradation enquanto as entidades não existem.

---

## Dependências de outros planos

| Precisa estar pronto | De onde vem | Por quê | Mitigação se ausente |
|---|---|---|---|
| Tabela **`conversations`** (`status`, `assignee_user_id`, `inbox_id`, `last_activity_at`, `custom_attributes`) + repo `conversation_repo.list(filters)` | [`01-plano-inbox-e-conversas`] (§1.4, §3.2) | Dimensões R1/R2/R3/R6 vivem na conversa; o filtro JOINa/lê dela | **Fallback degradado**: enquanto `conversations` não existir, filtrar sobre `contacts` (status/assignee/inbox ausentes no `filter-schema`; só `tags`/`last_activity`/`q`/`archived`). Endpoint canônico é `/api/conversations` (P76). |
| Tabela **`inboxes`** | [`02-canais-e-providers`] (stub no plano 01 §1.1) | Dimensão `inbox_id` e os valores do `filter-schema` | Omitir `inbox` do schema enquanto a tabela não existir. |
| Tabela **`users`** + `inbox_members` + helper **`current_user`** / `Require` | [`03-plano-rbac-usuarios`] (§3.2 `auth.py`) | `assignee=me` (resolve o id no servidor), recorte por inbox do atendente, `saved_filters.created_by`, scope global=admin | Sem `users`: `assignee` filtra por id cru (`me`/`unassigned` desabilitados ou `me`→501); `saved_filters.created_by` NULLABLE; sem recorte por inbox (instância single-operator). |
| Tabela **`custom_attribute_definitions`** + coluna JSON `custom_attributes` (`JSON().with_variant(JSONB,"postgresql")` via helper `_json_type()`) | [`05-plano-atributos-personalizados`] (§1.1) | Dimensões `cattr:<key>` no schema + tradução JSON→SQL | Omitir `cattr:*` do `filter-schema`; o tradutor ignora `attribute_key` começando com `cattr:` se a coluna não existir. |

**Estado das dependências (WF1):** `conversations` (01), `inboxes` (02), `users`/`inbox_members`/
`current_user` (03), `custom_attribute_definitions`/`custom_attributes`/`_json_type()` (05) — **todas
ausentes hoje** (`nao_feito` em todos os planos respectivos). Bloqueiam o valor real; sem 01 só entrega
o fallback degradado sobre `contacts`.

**Ordem recomendada (sequência viva):** Onda 3 entrega 03 (`rbac_users`) + 01 (`inbox_conversations`,
`backfill`); Onda 5+ entrega 05 (`custom_attributes`) e então **este plano**. Dentro do 08:
**Fase 1** (params simples) → **Fase 2** (payload estruturado) → **Fase 3** (views salvas). A Fase 1
pode entregar parcialmente sobre `contacts` se 01 atrasar (graceful), mas o valor real exige
`conversations`.

> **IMPORTANTE — alinhamento de modelagem (decidido, não re-litigar):** o doc 01 já decidiu a tabela
> `conversations` separada (Chatwoot 3 níveis). Portanto **todo este plano trata "conversa" como
> a unidade filtrável** e o endpoint canônico dos filtros é **`/api/conversations`** (decisão
> **P76**). O `GET /api/contacts` **NÃO** recebe filtros novos — fica restrito a **`q` e `archived`
> (legado)** até a migração do frontend para a lista de conversas (doc 01 §6). Os filtros novos são
> construídos **no caminho de conversas**; reaproveita-se apenas a lógica de busca textual de
> `contact_repo` (`_contact_ids_matching_message:324`).

---

## 1. Arquitetura do módulo de filtros  *(nao_feito — greenfield)*

Criar um módulo **isolado e reutilizável** que traduz a especificação de filtro em cláusulas
SQLAlchemy Core, consumido tanto pelo endpoint de query params (Fase 1) quanto pelo payload
estruturado (Fase 2) e pelas views salvas (Fase 3).

**Arquivos novos:**

- `db/filters/__init__.py` — exports públicos.
- `db/filters/registry.py` — **allowlist** `attribute_key → (coluna/expr, tipo, operadores válidos)`.
  É a fonte única de verdade que o `filter-schema`, o validador e o tradutor consomem. Nunca
  aceitar nome de coluna vindo do cliente direto (anti-injection / anti-vazamento).
- `db/filters/operators.py` — vocabulário fixo `OPS: dict[str, callable]` (mapeia operador →
  função que recebe `(col_expr, values)` e devolve uma cláusula Core).
- `db/filters/translate.py` — `build_where(payload, ctx) -> ColumnElement` (combina cláusulas com
  `and_()`/`or_()` conforme `query_operator`), `resolve_relative_time(value)`,
  `cattr_expr(conn, key)` (switch por dialeto, §5), e os helpers especiais (tags N:N).
- `db/filters/schema.py` — `available_dimensions(deps) -> list[dict]` (introspecciona o engine via
  `inspect()` para só anunciar o que existe; junta `custom_attribute_definitions` quando o doc 05
  estiver presente).
- `db/filters/spec.py` — normaliza a **entrada híbrida** (query params achatados **e** payload
  estruturado) numa única estrutura interna `FilterSpec` (`list[Clause]` + `sort` + `cursor`).
  Garante que Fase 1 e Fase 2 caem no mesmo `build_where`.

**Estrutura interna (`Clause`):**

```py
@dataclass
class Clause:
    attribute_key: str          # "status" | "assignee" | "inbox_id" | "labels" | "cattr:plano" | "last_activity"
    operator: str               # "equal_to" | "in" | "is_present" | ...
    values: list                # sempre lista (single vira [x]); [] para present/not_present
    query_operator: str | None  # "AND" | "OR" | None (último item)
```

**Registry (allowlist) — esqueleto:**

```py
# db/filters/registry.py  (ilustrativo — colunas reais vêm do doc 01/05)
FILTERABLE = {
    "status":        Dim(col=lambda T: T.conversations.c.status,           kind="enum", ops={"equal_to","not_equal_to","in"}),
    "assignee":      Dim(col=lambda T: T.conversations.c.assignee_user_id, kind="id",   ops={"equal_to","not_equal_to","in","is_present","is_not_present"}, special="assignee"),
    "inbox_id":      Dim(col=lambda T: T.conversations.c.inbox_id,         kind="id",   ops={"equal_to","in"}),
    "last_activity": Dim(col=lambda T: T.conversations.c.last_activity_at, kind="ts",   ops={"greater_than","less_than","between"}, special="reltime"),
    "labels":        Dim(col=None, kind="tags", ops={"contains_any","contains_all","not_contains"}, special="tags"),
    # cattr:* resolvido dinamicamente a partir de custom_attribute_definitions (doc 05)
}
```

**Operadores (`OPS`):**

```py
OPS = {
    "equal_to":       lambda c, v: c == v[0],
    "not_equal_to":   lambda c, v: c != v[0],
    "in":             lambda c, v: c.in_(v),
    "is_present":     lambda c, v: c.isnot(None),
    "is_not_present": lambda c, v: c.is_(None),
    "greater_than":   lambda c, v: c > v[0],
    "less_than":      lambda c, v: c < v[0],
    "between":        lambda c, v: c.between(v[0], v[1]),
    "contains":       lambda c, v: func.lower(c).contains(str(v[0]).lower()),
    "does_not_contain": lambda c, v: ~func.lower(c).contains(str(v[0]).lower()),
    # contains_any/contains_all/not_contains tratados no helper de tags (subquery), não aqui
}
```

> Os operadores Core (`==`, `.in_`, `.isnot`, `>`, `.between`, `.contains`) emitem SQL correto
> para SQLite **e** Postgres — caminho dialect-agnóstico natural. Só **tags N:N** e **JSON custom
> attrs** precisam de tratamento por dialeto/subquery (§5).

**Validação (anti-injection, OBRIGATÓRIA — item crítico do plano):**

1. `attribute_key` ∈ `FILTERABLE` (ou `cattr:<key>` com `<key>` ∈ defs do doc 05). Caso contrário → 400.
2. `operator` ∈ `Dim.ops` daquela dimensão. Caso contrário → 400.
3. `values` coerência por `kind` (number→float, ts→epoch/relativo, enum→∈valores conhecidos).
4. **Nunca** interpolar `attribute_key`/`operator` em string SQL; só usar como chave de lookup.

> Esta allowlist é o ponto de segurança nº 1 do plano (registrado na reconciliação §"Plano 08" como
> "anti-injection crítico"). Toda key/operador vindo do cliente é validado contra o registry **antes**
> de virar expressão Core — nunca há concatenação de nome de coluna/operador em SQL.

**Critério de pronto (módulo):** teste unitário `tests/test_filters.py` que monta `FilterSpec`
a partir de (a) query params e (b) payload estruturado, gera o mesmo `WHERE` compilado, e valida
rejeição de `attribute_key`/operador fora do allowlist. Roda em SQLite e (com
`WHATSBOT_TEST_DB_URL`) Postgres.

---

## 2. Fase 1 — Filtros básicos (MVP, query params)  *(nao_feito)*

**Objetivo:** entregar o grosso do valor (status/assignee/inbox/tags + AND) sobre o endpoint de
conversas, com risco baixo e sem infra nova além do módulo §1.

### 2.1 Endpoint

Estender `GET /api/conversations` (criado no doc 01) com query params:

```
GET /api/conversations?status=open&assignee=me&inbox_id=3&labels=lead,vip&since=7d&q=texto&cursor=...&limit=30
```

- `status` — single ou CSV (`open,pending`) → vira `in`.
- `assignee` — `me` (resolve `current_user.id` no servidor, doc 03) | `unassigned` (`is_not_present`)
  | `<id>` | CSV de ids → `in`.
- `inbox_id` — single ou CSV.
- `labels` — CSV de nomes de tag → `contains_any` (OR dentro da dimensão).
- `since` — relativo (`7d`, `24h`, `30d`) → resolvido no servidor para `last_activity > now-N` (R6).
- `q` — texto (reusa `_contact_ids_matching_message` de `contact_repo.py:324`).
- `cursor`/`limit` — keyset pagination (`(last_activity_at, id)`), ver §6. **Página default 30,
  scroll infinito, cursor opaco, `limit` com teto ~100** (P80).

**Semântica Fase 1: só AND entre dimensões.** OR dentro de uma dimensão via CSV/`in`/`contains_any`.
(AND/OR **plano** explícito chega na Fase 2 — P78; sem grupos aninhados no MVP.)

> Se o doc 01 ainda não criou `conversations`, o mesmo handler cai no fallback degradado sobre
> `contacts` (sem status/assignee/inbox) — ver Dependências.

### 2.2 Repositório

Implementar `conversation_repo.list(filters: FilterSpec, *, viewer)` (o doc 01 já reserva
`list(filters)`; este plano define o **contrato `filters` = `FilterSpec`** e a tradução).

- Constrói `select(...)` com JOIN `contacts` (nome/phone/avatar) + `users` (nome do assignee) +
  subquery da última mensagem (espelhando o LEFT JOIN de `contact_repo.list_contacts:360`).
- `where_clause = build_where(filters, ctx)` (§1).
- **Recorte obrigatório por inbox do RBAC** sobreposto como `and_()` extra (§7), NUNCA confiando no
  cliente.
- `ORDER BY last_activity_at DESC, id DESC` + keyset (`WHERE (last_activity_at, id) < (:c_ts, :c_id)`).
- Retorna `{items: [...], next_cursor: str|None}`.

> O parâmetro hoje em `contact_repo.list_contacts(q, archived)` (`:360`) permanece **intocado** para a
> UI legada; a lógica nova vive em `conversation_repo`, não reescreve a função existente.

### 2.3 Frontend

- `services/api.js`: adicionar `getConversations(params)` (monta querystring a partir de um objeto
  de filtros) — espelha `getContacts`. Manter `getContacts` para o legado.
- `Contacts.js` (ou novo `Conversations.js` do doc 01): estado `const [filters, setFilters] =
  useState({})`; passar a `fetchContacts`/`fetchConversations`. `showArchived` continua como
  **toggle dedicado** (decisão **P81**) — não vira `status=archived`.
- **Barra de chips** acima da lista: cada filtro ativo vira chip removível
  (`Status: Aberto ✕`, `Atendente: eu ✕`, `Tag: lead ✕`). Componente novo
  `components/contacts/FilterBar.js` + `FilterChip.js`.
- Dropdowns rápidos de Status / Atendente / Inbox alimentados pelo `filter-schema` (§4).
- **Modo escuro:** chips/dropdowns usam `bg-wa-panel`, `text-wa-text`, `border-wa-border`,
  `bg-wa-hover`; inputs com `.wa-field` (regra do CLAUDE.md).

### 2.4 `filter-schema` (entra já na Fase 1)

`GET /api/conversations/filter-schema` retorna **só as dimensões que existem na instalação atual**
(introspecção via `sqlalchemy.inspect(engine)`): se `conversations` não existe → sem
status/assignee/inbox; se doc 05 não aplicado → sem `cattr:*`. Inclui, por dimensão, o tipo de
input (enum/select, autocomplete de usuário, multiselect de tags, date-picker, texto/número), os
operadores válidos, e os valores possíveis quando enumeráveis (status fixos; inboxes; tags via
`tag_repo.get_all`). **`archived` é exposto como dimensão no schema** (P81), embora a UI também o
ofereça via toggle dedicado. Desacopla a UI das migrations.

> **Ordenação da lista — DECIDIDA (FQ4 encerra P81):** ordenar por **última atividade da conversa,
> mais recente no topo** (a conversa sobe ao chegar/sair mensagem), válida para todas as abas. É o que
> este plano assume (`ORDER BY last_activity_at DESC`). O comentário antigo "conversas não subirem ao
> chegar mensagem" foi **retirado**. (Se um dia se quiser fila estrita por ordem de chegada só em "Não
> atribuídas", é ajuste futuro no `ORDER BY`/keyset de §2.2/§6.)

**Critério de pronto (Fase 1):** `GET /api/conversations?status=open&assignee=me&labels=lead`
retorna a lista filtrada e paginada; `filter-schema` lista exatamente as dimensões presentes;
chips na UI aplicam/removem filtros e disparam refetch; `tests/test_endpoints.py` ganha bloco
cobrindo filtros por status, assignee (me/unassigned/id), inbox, labels e `since`.

---

## 3. Fase 2 — Query builder estruturado (payload Chatwoot) + OR + custom attrs  *(nao_feito)*

**Objetivo:** OR explícito, atributos custom (doc 05), uniformidade entre campos nativos e custom,
e base para views salvas.

### 3.1 Endpoint

```
POST /api/conversations/filter
```

Body estilo Chatwoot (pesquisa §4 Opção B):

```jsonc
{
  "payload": [
    { "attribute_key": "status",      "filter_operator": "equal_to",    "values": ["open"],        "query_operator": "OR" },
    { "attribute_key": "status",      "filter_operator": "equal_to",    "values": ["pending"],     "query_operator": "AND" },
    { "attribute_key": "assignee",    "filter_operator": "is_present",   "values": [],              "query_operator": "AND" },
    { "attribute_key": "labels",      "filter_operator": "contains_any", "values": ["lead","vip"],  "query_operator": "AND" },
    { "attribute_key": "cattr:plano", "filter_operator": "equal_to",     "values": ["premium"],     "query_operator": null }
  ],
  "sort": "-last_activity",
  "cursor": null,
  "limit": 30
}
```

- `attribute_key`: canônico; custom usa prefixo `cattr:<key>` (doc 05).
- `filter_operator`: vocabulário fixo (§1, OPS + tags + present/not_present).
- `values`: sempre array (vazio para present/not_present).
- `query_operator`: `AND`|`OR`|`null` no último item.
- Envelope de resposta padrão `{"ok", "data", "error"}`.

**Reuso total do módulo §1:** `spec.from_payload(body)` → `FilterSpec` → `build_where`. O mesmo
`conversation_repo.list` serve os dois endpoints.

> A UI básica (chips, §2.3) continua chamando o `GET` simples; o **"Filtro avançado"** (drawer com
> linhas `[atributo ▾][operador ▾][valor][AND/OR ▾]`) usa o `POST /filter`.

### 3.2 Combinação AND/OR

`build_where` agrupa cláusulas conforme `query_operator` entre itens consecutivos. **MVP:
AND/OR plano** (estilo Chatwoot), **sem grupos aninhados** (decisão **P78**). Implementar como
fold sequencial: acumula em grupos OR quando o conector é `OR`, junta grupos com `and_()`.
Grupos OR aninhados só entram se houver demanda real (Fase 4 opcional).

### 3.3 Custom attributes (JSON, doc 05) — tradução dialect-agnóstica

A coluna é `conversations.custom_attributes` / `contacts.custom_attributes`, tipada
`JSON().with_variant(JSONB(), "postgresql")` (doc 05 §1). Extração via helper com switch por
dialeto (precedente: `db/upsert.py:26-27`):

```py
def cattr_expr(conn, key: str, *, scope="conversation"):
    col = (conversations if scope == "conversation" else contacts).c.custom_attributes
    if conn.engine.dialect.name == "postgresql":
        return col.op("->>")(key)            # texto; cast p/ ::numeric quando number
    return func.json_extract(col, f"$.{key}")  # SQLite json1 (>=3.9)
```

- Comparações numéricas exigem cast (`CAST(json_extract(...) AS REAL)` no SQLite;
  `(... ->> 'k')::numeric` no Postgres) — o tipo vem da definição (doc 05).
- `is_present`/`is_not_present` para custom: `json_extract IS NULL` / `IS NOT NULL` (SQLite) e
  `?`/`->>` no Postgres.
- Validar `cattr:<key>` contra `custom_attribute_definitions` (escopo conversation/contact) na
  entrada **e** na execução (a def pode ter sido removida — ver §4 views).

### 3.4 Tags N:N (R4) — subquery (idêntico nos dois bancos)

**Decisão P77: reusar a tag do contato para a conversa** — NÃO criar `conversation_tags` no MVP.
Sem JSON: subquery sobre `contact_tags` + `tags` (já existem em `db/tables.py:116/125`). A tag da
conversa = tag do contato dono, então a cláusula casa `conversations.contact_id IN (subquery)`.
(`conversation_tags` separada fica para o futuro, só se o produto pedir labels por conversa.)

```py
def tag_clause(op, names):
    sub = (select(contact_tags.c.contact_id)
           .join(tags, tags.c.id == contact_tags.c.tag_id)
           .where(tags.c.name.in_(names)))
    if op == "contains_any":  return conversations.c.contact_id.in_(sub)
    if op == "not_contains":  return conversations.c.contact_id.notin_(sub)
    if op == "contains_all":
        sub_all = sub.group_by(contact_tags.c.contact_id)\
                     .having(func.count(func.distinct(tags.c.name)) == len(names))
        return conversations.c.contact_id.in_(sub_all)
```

**Critério de pronto (Fase 2):** `POST /api/conversations/filter` aceita o payload, resolve AND/OR
plano, filtra por `cattr:*` em SQLite **e** Postgres (teste com `WHATSBOT_TEST_DB_URL`), e por tags
(`contains_any/all/not_contains`); drawer de filtro avançado funcional no frontend; rejeição de
operador/atributo inválido com 400.

---

## 4. Fase 3 — Views / segmentos salvos  *(nao_feito)*

### 4.1 Tabela `saved_filters` (`db/tables.py`)

```py
saved_filters = Table(
    "saved_filters", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("scope", Text, nullable=False, server_default="user"),   # 'user' | 'global' (P79 — sem team/inbox no MVP)
    Column("created_by", Integer, nullable=True),  # doc 03 — FK p/ users.id só quando a tabela existir (ver nota abaixo)
    Column("query_json", _json_type(), nullable=False),            # o payload §3.1 (JSON nativo; JSONB no PG)
    Column("sort", Text, nullable=False, server_default="-last_activity"),
    Column("position", Integer, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),                   # epoch float (P56 — padrão do projeto)
    Column("updated_at", Float, nullable=False),
)
Index("idx_saved_filters_owner", saved_filters.c.created_by, saved_filters.c.scope)
```

- `query_json` usa o helper `_json_type()` (`JSON().with_variant(JSONB,"postgresql")`) introduzido
  pelo doc 05 — **não** serializar à mão como `Text` (segue o precedente de `custom_attributes`).
  Se o doc 05 ainda não tiver criado o helper, replicá-lo localmente em `db/tables.py`. *(Hoje
  `db/tables.py` só tem JSON-as-Text em `messages.reactions:92`; o `_json_type()` ainda não existe —
  é entregue pelo doc 05, ou replicado aqui.)*
- `created_at`/`updated_at` epoch `Float` (**P56**, padrão do projeto, não `Text`).
- **`created_by` — FK condicional**: declarar como `Integer` puro (sem `ForeignKey`) enquanto a tabela
  `users` (doc 03) não existir na mesma `MetaData`. Um `ForeignKey("users.id")` para uma tabela ausente
  passa na definição do `Table`, mas **quebra no `create_all`/migration** (a constraint referencia um
  alvo inexistente). Adicionar o FK via `ALTER TABLE` (ou na própria migration) só depois que o doc 03
  criar `users`. No MVP single-operator a coluna fica `NULL`.
- `CORE_TABLES` (`db/tables.py:324`) inclui automaticamente — cobre a migração SQLite→Postgres.

### 4.2 Migration Alembic  *(encadeamento linear — P82, corrigido)*

`db/alembic/versions/<data>_<n>_saved_filters.py`:

- `down_revision` = **head real no momento de implementar** (hoje `0008_plugin_installed_deps`);
  número = **próximo livre (≥ 0009)**. **NUNCA** usar 0006/0007/0008 como slot novo — eles já foram
  consumidos (`0006_contact_mention`, `0007_ai_engine_tables`, `0008_plugin_installed_deps`) e
  ramificar a cadeia **quebra o boot** (`alembic upgrade head` viraria branch).
- **Coordenação inter-planos (P82):** este plano é **Onda 5+**, depois de 01 (`inbox_conversations`
  → 0009, `backfill_conversations` → 0010) e 05 (`custom_attributes`). Então, na ordem real provável,
  `saved_filters` **não** será 0009: encadeia no head produzido pelas migrations de 01/05 que entrarem
  antes. **Regra prática:** rode `alembic heads` no momento de implementar, aponte `down_revision` para
  o que sair, e use o próximo número sequencial livre. **Sem branches Alembic.**
- `upgrade()`: `op.create_table("saved_filters", ...)` (escrever **à mão**, autogenerate não acerta
  `with_variant`/`server_default` JSON — espelhar `20260603_0005_contact_pinned.py`) + os índices.
- `downgrade()`: `op.drop_table("saved_filters")`.
- Testar `alembic upgrade head` em SQLite **e** Postgres.

### 4.3 Repositório `db/repositories/saved_filter_repo.py` (novo)

```py
def list_for_user(user_id: int | None) -> list[dict]   # próprias + globais, ORDER BY position
def get(id: int) -> dict | None
def create(name, scope, query_json, sort, created_by) -> dict   # valida query_json contra allowlist
def update(id, **fields) -> dict
def delete(id) -> None
def reorder(ids: list[int]) -> None                    # position
```

- **Validar `query_json`** contra o mesmo allowlist (§1) **ao salvar e ao executar** (uma def custom
  pode ter sido removida; um operador inválido nunca deve persistir).
- Padrão de acesso Core (`with get_engine().begin()/connect()`), nada de `sqlite3`.

### 4.4 Endpoints `server/routes/saved_filters.py` (novo) + registro em `server/app.py`

| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/api/saved-filters` | Lista views visíveis (próprias + globais). |
| POST | `/api/saved-filters` | Cria a partir de um payload de filtro (`scope=global` exige admin). |
| PUT | `/api/saved-filters/{id}` | Edita (dono ou admin). |
| DELETE | `/api/saved-filters/{id}` | Remove (dono ou admin). |
| POST | `/api/saved-filters/reorder` | Reordena no sidebar. |
| POST | `/api/conversations/filter` | aceita `{"view_id": N}` como atalho (carrega `query_json` da view). |

- Registrar no padrão: import na `server/app.py:18` (lista `from server.routes import …`) +
  `saved_filters.register_routes(app, deps)` junto das demais no bloco **`server/app.py:328-344`**
  *(plano antigo citava `:304-319` — drift de ~24 linhas)*.
- RBAC: `scope=global` create/update/delete → `Depends(Require("..."))` admin (doc 03); resolver
  tempos relativos **na execução**, não na gravação.

### 4.5 Frontend

- `services/api.js`: `listSavedFilters`, `createSavedFilter`, `updateSavedFilter`,
  `deleteSavedFilter`, `applySavedFilter(id)`.
- UI: botão **"Salvar filtro"** (a partir dos chips/drawer ativos) → `POST /api/saved-filters`;
  lista de views salvas no topo da sidebar / menu da engrenagem para aplicação 1-clique.
  Componente `components/contacts/SavedFilters.js`.
- Modo escuro: `wa-*` + `.wa-field`.

**Critério de pronto (Fase 3):** criar uma view a partir de um filtro ativo, recarregar a página,
aplicá-la em 1 clique e ver a lista filtrada; `scope=global` bloqueado para não-admin; tempos
relativos reavaliados a cada aplicação; bloco em `tests/test_endpoints.py` cobrindo CRUD + scope.

---

## 5. SQL dialect-agnóstico — regras consolidadas

- **Tudo via SQLAlchemy Core** (`Table` objects de `db/tables.py`, bind params nomeados). Nunca
  `sqlite3` direto nem `?`/`%s`. Leitura `with get_engine().connect()`, escrita `.begin()`.
- Operadores nativos (`==`, `.in_`, `.isnot`, `>`, `.between`, `.contains`) — dialect-agnósticos.
- **Único switch por dialeto**: JSON custom attrs (`cattr_expr`, §3.3) — precedente `db/upsert.py:26-27`.
- Tags N:N via subquery (§3.4) — idêntico nos dois bancos.
- `_json_type()` (`JSON().with_variant(JSONB,"postgresql")`) para `saved_filters.query_json` e
  (do doc 05) para `custom_attributes` — **reatribuir o dict inteiro** no UPDATE (JSON não detecta
  mutação in-place; regra do doc 05).

---

## 6. Performance, índices e paginação

- **Índices compostos** terminando em `last_activity_at DESC` (cobrem filtro + ordenação). Já
  reservados pelo doc 01 §1.4: `idx_conv_inbox_status`, `idx_conv_assignee_status`,
  `idx_conv_last_activity`. **Este plano adiciona** (na migration própria, se ainda não existirem):
  - `ix_conv_status_lastact (status, last_activity_at DESC)`
  - `ix_conv_assignee_lastact (assignee_user_id, last_activity_at DESC)`
- **Índice parcial** (SQLite e Postgres suportam `WHERE`) para a fila quente de não-atribuídas
  abertas: `... ON conversations (last_activity_at DESC) WHERE assignee_user_id IS NULL AND status='open'`.
  Criar via `op.create_index(..., postgresql_where=.../ sqlite_where=...)` no Alembic.
- **JSONB + GIN no Postgres (caminho de referência do Pro)**: filtros por custom attrs
  (`cattr:*`) usam `custom_attributes` tipado `JSONB` com `CREATE INDEX ... USING gin
  (custom_attributes)` (entregue pelo doc 05). Pela **decisão global de banco**, o Postgres é o
  backend de referência do Pro e filtros por custom attrs **podem exigi-lo** quando o volume crescer
  — JSONB+GIN é a solução boa, não contorcida pelo SQLite. **SQLite degrada com elegância**: sem
  GIN, usa *expression index* só para os campos `filterable` (`json_extract(custom_attributes,
  '$.plano')` — **P55**, decidido junto deste plano) ou, em último caso, promover a coluna real
  (decisão do doc 05). O `filter-schema` continua anunciando `cattr:*` nos dois; só a performance
  difere.
- **Keyset pagination obrigatória** por `(last_activity_at, id)` (não `OFFSET` grande) — mantém o uso
  do índice. `cursor` **opaco** (base64 do par). **Página default 30, scroll infinito** (decisão
  **P80**); `limit` ajustável por query param com **teto ~100**.
- Busca textual pesada (`q`) continua isolada em `_contact_ids_matching_message`
  (`contact_repo.py:324`) — **não** misturar no caminho quente dos filtros estruturados; aplicar
  como `conversations.contact_id IN (ids)` só quando `q` presente.

**Critério de pronto:** `EXPLAIN QUERY PLAN` (SQLite) / `EXPLAIN` (Postgres) mostram uso de índice
para os filtros típicos (status+last_activity, assignee+last_activity, fila não-atribuídas); a
lista responde paginada via cursor.

---

## 7. RBAC — recorte por inbox (doc 03)

- **Recorte obrigatório no servidor**, sobreposto ao filtro do usuário como `and_()` extra em
  `conversation_repo.list`: se o usuário **não** é admin e não tem `conversation.read_all`,
  intersectar `inbox_id` com os inboxes a que ele pertence (`inbox_members`, doc 03/01). Nunca
  confiar no `inbox_id` enviado pelo cliente — ele só restringe **dentro** do conjunto permitido.
- `assignee=me` → resolve `current_user.id` no servidor (`deps.current_user`, doc 03 §3.2).
- `saved_filters scope=global` → create/update/delete só admin (`Depends(Require(...))`).
- `filter-schema` pode **omitir** dimensões/inboxes que o papel não enxerga.
- Enquanto o doc 03 não entregar `users`/`inbox_members`: instância single-operator, sem recorte;
  `assignee=me` → 501/no-op; `created_by` NULLABLE.

> **Estado WF1:** `server/auth.py` ainda é **SHA-256 + senha única** (sem `users`, sem Argon2id, sem
> `current_user`/`Require`). Todo o recorte desta seção fica como fallback single-operator até o
> plano 03 (Onda 3) chegar. **Preservar as isenções `/api/webhook` e `/health`** quando o RBAC mexer
> no `auth_middleware` (o GOWA posta sem credencial) — invariante crítico do plano 03.

**Critério de pronto:** com doc 03 presente, atendente não-admin vê só conversas dos seus inboxes
mesmo pedindo `inbox_id` de outro; admin vê tudo; teste em `tests/test_endpoints.py`.

---

## 8. Tempo real (WS) — atualização das views

- Ao chegar evento WS que toca uma conversa (`new_message`, mudança de status/assignee — eventos do
  doc 01 via `server/state.py` `ConnectionManager.broadcast`), a UI deve **reavaliar** se a conversa
  ainda casa com os filtros ativos.
- **MVP:** refetch debounced quando um evento WS toca uma conversa potencialmente fora do conjunto
  atual (simples, suficiente).
- **Refinamento (Fase 4):** avaliação client-side do `FilterSpec` contra o payload do evento para
  inserir/remover sem refetch.

---

## 9. Faseamento (resumo) e critérios de pronto

| Fase | Estado WF1 | Entrega | Critério de pronto |
|---|---|---|---|
| **0** (deps) | `nao_feito` | docs 01/02/05/03 criam colunas/tabelas/índices/`current_user` | `alembic upgrade head` cria `conversations`+`custom_attributes`+`users`; `tests/test_endpoints.py` verde |
| **1 — MVP** | `nao_feito` | módulo `db/filters/*`, `GET /api/conversations` com params (status/assignee/inbox/labels/since/q), `filter-schema`, chips na UI, índices, keyset pagination | Lista filtrada+paginada; schema reflete o que existe; bloco de testes de filtros |
| **2 — Query builder** | `nao_feito` | `POST /api/conversations/filter` (payload Chatwoot), AND/OR plano, `cattr:*` (SQLite+PG), tags set-ops, drawer avançado | Payload traduz nos dois bancos; rejeita inválidos com 400 |
| **3 — Views salvas** | `nao_feito` | `saved_filters` + migration (head real, ≥0009) + repo + endpoints CRUD/reorder + UI salvar/listar, scope user/global RBAC | CRUD + aplicação 1-clique + scope global só admin |
| **4 — Refinos** | `nao_feito` | atualização de view em tempo real via WS, ordenação configurável, (se houver demanda) grupos OR aninhados estilo Linear | Views reagem a eventos WS sem refetch |

---

## Dependências de outros planos

(consolidado — ver tabela detalhada no topo)

1. **`conversations` + `conversation_repo.list(filters)`** — doc 01 (bloqueante para o valor real;
   fallback degradado sobre `contacts`). *(Onda 3)*
2. **`inboxes`** — doc 02 (stub no doc 01); sem ela, omitir `inbox` do schema. *(Onda 5+)*
3. **`users` + `inbox_members` + `current_user`/`Require`** — doc 03 (`assignee=me`, recorte por
   inbox, `saved_filters.created_by`, scope global). *(Onda 3)*
4. **`custom_attribute_definitions` + `custom_attributes` JSON + `_json_type()`** — doc 05
   (`cattr:*` + `query_json`). *(Onda 5+)*

> **Migration `saved_filters` (P82):** `down_revision` = head real no momento de implementar
> (≥0009, depois de 01 `inbox_conversations`/`backfill` e 05 `custom_attributes`). Rodar
> `alembic heads` na hora; **não** usar 0006/0007/0008. Ver §4.2.

---

## Perguntas em aberto

> Todas decididas (DECISOES.md §Tema H). Mantidas aqui para rastreabilidade.

1. **Endpoint canônico: `/api/conversations` ou estender `/api/contacts`?**
   - **✅ DECIDIDO (2026-06-19): (a)** (P76) — filtros canônicos só em `/api/conversations`;
     `/api/contacts` permanece com **apenas `q`/`archived`** para o legado. Incorporado nas §2.1 e §3.1.

2. **Tags são por contato ou por conversa?**
   - **✅ DECIDIDO (2026-06-19): (a)** (P77) — **reusar a tag do contato** para a conversa
     (`conversations.contact_id IN subquery`); **não** criar `conversation_tags` no MVP (só se o
     produto pedir labels por conversa no futuro). Incorporado na §3.4.

3. **OR aninhado (grupos) é necessário?**
   - **✅ DECIDIDO (2026-06-19): (a)** (P78) — **AND/OR plano** no MVP; grupos aninhados só com
     demanda real (Fase 4 opcional). Incorporado na §3.2.

4. **Escopo das views globais.**
   - **✅ DECIDIDO (2026-06-19): (a)** (P79) — `saved_filters.scope` só **`user`/`global`** no MVP;
     `team`/`inbox` quando o doc 03 entregar teams. Incorporado na §4.1/§4.4.

5. **Tamanho de página e scroll infinito.**
   - **✅ DECIDIDO (2026-06-19): página 30 + scroll infinito** (P80) — cursor **opaco**, `limit`
     ajustável por query param com **teto ~100**. Incorporado na §2.1 e §6.

6. **`archived`: filtro ou toggle dedicado?**
   - **✅ DECIDIDO (2026-06-19): (a)** (P81) — **manter o toggle dedicado** + expor `archived`
     como dimensão no `filter-schema`. Incorporado nas §2.3/§2.4. **Ordenação encerrada por FQ4**:
     ordenar por última atividade, mais recente no topo — a conversa sobe ao chegar mensagem.
     Comentário "não subir" **retirado**. Ver §2.4/§6/§8.

7. **Encadeamento de revisões Alembic.**
   - **✅ DECIDIDO (2026-06-19): (a)** (P82) — encadeamento **linear**; ao implementar, setar
     `down_revision` para o **head real do repositório naquele momento** (hoje
     `0008_plugin_installed_deps`; na prática ≥0009 depois de 01/05). Sem branches. **Nunca** reusar
     0006/0007/0008 (já consumidos). Incorporado na §4.2.

8. **Filtros expostos ao agente LLM como tool?**
   - **✅ DECIDIDO (2026-06-19): (a)** (P83) — **fora de escopo** agora; ideia registrada. A infra
     (`FilterSpec` + `conversation_repo.list`) fica reutilizável se virar tool depois.
