# 08 — Filtros avançados de conversas (segmentação da lista)

> Pesquisa de arquitetura. **Nenhum código foi alterado.** Este documento descreve
> como evoluir os filtros da lista de conversas (sidebar) do WhatsBot conforme as
> novas entidades (inboxes/canais — doc 02; status/assignee — doc 01; atributos
> personalizados — doc 05; RBAC — doc 03) forem introduzidas.

Escopo do cliente: *"melhorar os filtros com base nas novas funcionalidades"*.
Hoje só dá pra filtrar por texto, arquivado e tags porque as outras entidades
(inbox, status de conversa, atendente, atributos custom) **ainda não existem**.
Este doc projeta o filtro composto e as views salvas que se apoiam nessas
entidades futuras.

---

## 1. O que existe hoje

A lista de conversas é a sidebar do componente principal de chat.

**Frontend** — `web/static/js/components/contacts/Contacts.js`:
- Estado de busca: `const [search, setSearch] = useState('')` (linha ~17), com debounce de
  300ms que chama `fetchContacts(search)` (linha ~368-371).
- Estado de arquivado: `const [showArchived, setShowArchived] = useState(false)` (linha ~28);
  alternar dispara recarga via efeito em `[showArchived]` (linha ~345).
- Filtro por tags: hoje **não há filtro server-side por tag na lista**. As tags vêm
  embutidas em cada contato (`c.tags`) e a UI usa-as para *exibição* e *ações em massa*
  (`handleBulkTag`, `globalTags`), não para reduzir a lista. (A segmentação por tag é,
  portanto, um requisito a construir, não algo já pronto.)

**Service** — `web/static/js/services/api.js` (`getContacts`, linha ~132):
```js
export async function getContacts(q = '', archived = false) {
  const params = [];
  if (archived) params.push('archived=true');
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  ...
  return request('GET', `/api/contacts${query}`);
}
```
Só dois parâmetros: `q` (texto) e `archived` (bool).

**Backend** — `server/routes/contacts.py`:
```py
@app.get("/api/contacts")                                  # linha ~45
async def list_contacts(q: str = "", archived: bool = False):
    results = await asyncio.to_thread(contact_repo.list_contacts, q, archived)
```

**Repositório** — `db/repositories/contact_repo.py` (`list_contacts`, linha ~360):
- Monta SQL raw com `WHERE c.is_archived = :archived` (linha ~387).
- O `q` faz match em nome/telefone e, via `_contact_ids_matching_message` (linha ~324),
  também no conteúdo das mensagens.
- Não há nenhum critério além de archived + texto.

**Tabela** — `db/tables.py` (`contacts`, linha ~41): colunas relevantes hoje
`phone, name, ai_enabled, is_group, is_archived, is_pinned, unread_count,
has_unread_mention, updated_at`. **Não existem** `status`, `assignee_id`,
`inbox_id`, nem coluna de atributos personalizados. Tags vivem em `tags` +
`contact_tags` (N:N).

**Conclusão:** o filtro atual é um caso degenerado (1 dimensão booleana + 1 busca
textual). A feature pedida transforma isso num filtro **multi-dimensional** apoiado
em entidades que serão criadas pelos docs 01/02/05.

---

## 2. Requisitos

Permitir filtrar/segmentar a lista de conversas por:

| # | Critério | Origem (doc) | Tipo de valor | Operadores úteis |
|---|---|---|---|---|
| R1 | **Inbox / canal** | 02 | id (enum de inboxes) | is / is_not / in |
| R2 | **Status da conversa** (open/pending/resolved/snoozed…) | 01 | enum | is / is_not / in |
| R3 | **Atendente atribuído** (assignee) | 01 + 03 | id de usuário, ou `unassigned` / `me` | is / is_not / is_present / is_not_present |
| R4 | **Tags / labels** | tags+contact_tags (hoje) | lista | contains_any / contains_all / not_contains |
| R5 | **Atributos personalizados** | 05 | string/number/bool/date/enum | is / is_not / contains / gt / lt / present |
| R6 | **Período / última atividade** (`updated_at`, `last_message_ts`, `created_at`) | contacts (hoje) | timestamp | gt / lt / between / "últimos N dias" |
| R7 | **Texto livre** (busca) | hoje | string | já existe (`q`) |
| R8 | **Combinação de critérios** com AND/OR | — | — | conector entre filtros |
| R9 | **Views/segmentos salvos** (reusar um conjunto de filtros) | novo (seção 6) | — | — |

Notas de produto:
- **Não-atribuído** (R3) e **sem tags** (R4) são casos comuns o suficiente para
  merecerem operador de "ausência" (`is_not_present` / `not_contains`).
- Filtros de tempo relativos ("últimos 7 dias") devem ser resolvidos **no servidor**
  no momento da query (não congelar o timestamp na view salva).

---

## 3. Dimensões filtráveis e de onde vêm

Mapeamento de cada filtro para a entidade que o doc correspondente cria. Enquanto a
entidade não existir, o filtro fica **desabilitado/oculto** na UI (graceful: o
endpoint só anuncia as dimensões disponíveis — ver §4).

| Dimensão | Coluna/tabela esperada | Status hoje |
|---|---|---|
| inbox/canal (R1) | `conversations.inbox_id` ou `contacts.inbox_id` → tabela `inboxes` (doc 02) | **não existe** |
| status (R2) | `conversations.status` (doc 01) | **não existe** |
| assignee (R3) | `conversations.assignee_id` → `users` (doc 01/03) | **não existe** |
| tags (R4) | `tags` + `contact_tags` (já existem em `db/tables.py`) | **existe** (falta o filtro) |
| atributos custom (R5) | coluna JSON/JSONB (ex.: `contacts.custom_attributes`) + schema em tabela própria (doc 05) | **não existe** |
| última atividade (R6) | `contacts.updated_at` / `last_message_ts` | **existe** |
| texto (R7) | nome/telefone/conteúdo de mensagem | **existe** |

> **Decisão de modelagem pendente (cruza com docs 01/02):** o WhatsBot hoje é
> "1 contato = 1 conversa" (a tabela `contacts` carrega o estado da conversa:
> `unread_count`, `is_archived`, `is_pinned`). Os docs 01/02 podem introduzir uma
> tabela `conversations` separada (1 contato → N conversas, cada uma com
> status/assignee/inbox). **A API de filtros deve ser desenhada já assumindo
> "conversa" como a unidade filtrável** — mesmo que no MVP "conversa" ainda seja
> sinônimo de "contato". Isso evita reescrever o filtro quando a separação chegar.
> Os campos `status`/`assignee_id`/`inbox_id` ficam na entidade-conversa.

---

## 4. Desenho da API de filtros

### Opção A — query params simples (achatado)

```
GET /api/contacts?archived=false&status=open&assignee=me&inbox=3&tags=lead,vip&since=7d
```
- **Prós:** trivial de implementar; cacheável; bookmarkável; estende o endpoint atual
  sem quebra.
- **Contras:** só expressa **AND** entre dimensões e **OR implícito dentro** de uma
  lista (`tags=lead,vip`). Não há como pedir `status=open OR status=pending` vs
  `(inbox=3 AND status=open)`. Operadores ficam embutidos em convenções de string
  (`since=7d`, `assignee=unassigned`) — frágil. Atributos custom arbitrários não
  cabem bem.

### Opção B — corpo de filtro estruturado (query builder, estilo Chatwoot)

`POST /api/contacts/filter` (ou `GET` com body via `/search`), payload inspirado
direto no [Chatwoot Conversation Filter API](https://developers.chatwoot.com/api-reference/conversations/conversations-filter):

```jsonc
{
  "payload": [
    { "attribute_key": "status",   "filter_operator": "equal_to",     "values": ["open"],       "query_operator": "AND" },
    { "attribute_key": "assignee", "filter_operator": "is_present",    "values": [],             "query_operator": "AND" },
    { "attribute_key": "inbox_id", "filter_operator": "equal_to",      "values": [3],            "query_operator": "AND" },
    { "attribute_key": "labels",   "filter_operator": "contains_any",  "values": ["lead","vip"], "query_operator": "AND" },
    { "attribute_key": "cattr:plano", "filter_operator": "equal_to",   "values": ["premium"],    "query_operator": null }
  ],
  "page": 1,
  "sort": "-last_activity"
}
```
- `attribute_key`: nome canônico da dimensão. Atributos custom usam prefixo
  (`cattr:<chave>`) para distingui-los dos campos nativos (doc 05).
- `filter_operator`: vocabulário fixo (ver tabela abaixo).
- `values`: array (uniformiza single e múltiplos valores; vazio para
  `is_present`/`is_not_present`).
- `query_operator`: `AND` | `OR` | `null` (no último item), igual ao Chatwoot.

**Vocabulário de operadores recomendado** (subconjunto pragmático; Chatwoot expõe
`equal_to`, `not_equal_to`, `contains`, `does_not_contain`, `is_present`,
`is_not_present` — ver [How to use Conversation Filters](https://www.chatwoot.com/hc/user-guide/articles/1677688192-how-to-use-conversation-filters)):

| Operador | Aplica a | Semântica |
|---|---|---|
| `equal_to` / `not_equal_to` | enum, id, string, número | igualdade |
| `in` | enum, id | valor ∈ lista (OR dentro da dimensão) |
| `contains` / `does_not_contain` | string, custom text | substring (case-insensitive) |
| `contains_any` / `contains_all` / `not_contains` | labels/tags | semântica de conjunto N:N |
| `is_present` / `is_not_present` | assignee, custom attr | NULL/NOT NULL (cobre "não-atribuído") |
| `greater_than` / `less_than` / `between` | número, data, última atividade | comparação |

- **Prós:** expressa AND/OR explícito; uniforme para campos nativos e custom;
  alinhado a uma indústria consolidada (Chatwoot, Intercom, Linear — todos usam
  `attribute + operator + value` com conector lógico). Encaixa direto em views
  salvas (§6): basta persistir o `payload`. Frontend genérico (um renderizador de
  linha de filtro serve todas as dimensões).
- **Contras:** mais código de tradução payload→SQL; precisa validação rigorosa de
  `attribute_key`/`filter_operator` (allowlist, anti-injection); não é GET puro.

### Recomendação para o MVP

**Híbrido faseado:**
1. **MVP imediato:** estender o `GET /api/contacts` atual com params simples
   (Opção A) para as 3 dimensões de maior valor: `status`, `assignee`, `inbox` (+
   `tags` já como lista). Só **AND** entre elas. Zero infra nova, entrega rápida,
   compatível com a UI atual.
2. **Fase 2:** introduzir `POST /api/contacts/filter` com o payload estruturado
   (Opção B) quando entrarem atributos custom (doc 05), OR explícito e views
   salvas. A UI básica de filtros (chips) continua chamando o endpoint simples;
   o "filtro avançado" usa o endpoint estruturado.

Em ambos os casos, expor **`GET /api/contacts/filter-schema`** retornando as
dimensões disponíveis **no estado atual da instalação** (só inclui `inbox` se o
doc 02 já estiver presente; só inclui um `cattr:*` por atributo definido no doc 05).
Isso desacopla o frontend das migrations: a UI renderiza exatamente o que existe.

Manter o envelope padrão do projeto: `{"ok": bool, "data": ..., "error": ...}`.

---

## 5. Tradução para SQL com SQLAlchemy Core (dialect-agnóstica)

Regra do projeto: **sempre SQLAlchemy Core**, `Table` objects de `db/tables.py`,
bind params nomeados, leitura via `get_engine().connect()`. Nada de `sqlite3`
direto nem `?`/`%s`.

### Padrão geral — montar `WHERE` a partir do payload

Construir uma lista de cláusulas e combiná-las com `and_()` / `or_()`. Cada
`attribute_key` mapeia para uma coluna/expressão via um **registry allowlist** (não
aceitar nomes de coluna vindos do cliente diretamente — risco de injection / vazar
colunas):

```py
from sqlalchemy import select, and_, or_, func
from db.tables import contacts  # + conversations, contact_tags, tags (futuros)

# Allowlist: attribute_key -> (coluna SQLAlchemy, tipo)
FILTERABLE = {
    "status":     (conversations.c.status,      "enum"),
    "assignee":   (conversations.c.assignee_id, "id"),
    "inbox_id":   (conversations.c.inbox_id,    "id"),
    "last_activity": (contacts.c.updated_at,    "ts"),
    # tags e cattr:* têm tratamento especial (subquery / JSON) — ver abaixo
}

OPS = {
    "equal_to":      lambda col, v: col == v[0],
    "not_equal_to":  lambda col, v: col != v[0],
    "in":            lambda col, v: col.in_(v),
    "is_present":    lambda col, v: col.isnot(None),
    "is_not_present":lambda col, v: col.is_(None),
    "greater_than":  lambda col, v: col > v[0],
    "less_than":     lambda col, v: col < v[0],
    "contains":      lambda col, v: func.lower(col).contains(str(v[0]).lower()),
}
```

> Os operadores Core (`==`, `.in_`, `.isnot`, `>`, `.contains`) já emitem SQL
> correto para SQLite **e** Postgres — é o caminho dialect-agnóstico natural. Só os
> dois casos abaixo (tags N:N e JSON) precisam de atenção por dialeto.

### Tags / labels (R4) — relação N:N

Sem JSON: usar subquery sobre `contact_tags` + `tags` (ambas já existem). Funciona
idêntico nos dois bancos:

```py
def tag_clause(op, names):
    sub = (select(contact_tags.c.contact_id)
           .join(tags, tags.c.id == contact_tags.c.tag_id)
           .where(tags.c.name.in_(names)))
    if op == "contains_any":
        return contacts.c.id.in_(sub)
    if op == "not_contains":
        return contacts.c.id.notin_(sub)
    if op == "contains_all":
        # exige todos: count distinto == len(names)
        sub_all = sub.group_by(contact_tags.c.contact_id)\
                     .having(func.count(func.distinct(tags.c.name)) == len(names))
        return contacts.c.id.in_(sub_all)
```

### Atributos personalizados (R5) — JSON / JSONB (liga ao doc 05)

O doc 05 deve definir onde os custom attributes ficam. Recomendação para casar com
filtros performáticos:

- **SQLite:** coluna `TEXT` com JSON serializado; filtrar via `func.json_extract(col, '$.<chave>')`.
- **Postgres:** coluna `JSONB`; filtrar via operador `->>` (texto) ou `@>` (containment),
  indexável por GIN.

Para manter **uma só base de código** apesar das funções divergirem, isolar a
extração num helper que escolhe pelo dialeto do engine (mesma estratégia do
`db/upsert.py` do projeto):

```py
from sqlalchemy import text, func

def cattr_expr(conn, key: str):
    name = conn.engine.dialect.name
    if name == "postgresql":
        # custom_attributes JSONB -> ->> retorna texto
        return text("custom_attributes ->> :k").bindparams(k=key)
    # sqlite (default)
    return func.json_extract(contacts.c.custom_attributes, f"$.{key}")
```

> `func.json_extract` existe no SQLite (≥3.9) e o `->>`/`@>` no Postgres; ambos os
> dialetos suportam JSON no SQLAlchemy, mas as funções **não** são as mesmas — daí
> o switch por `dialect.name` (ver
> [discussão SQLAlchemy #9530](https://github.com/sqlalchemy/sqlalchemy/discussions/9530)
> e [searching Postgres JSON columns](https://medium.com/code-on-a-boat/searching-postgres-json-columns-using-sqlalchemy-aece6ae5b0e9)).
> Comparações numéricas exigem cast (`CAST(json_extract(...) AS REAL)` no SQLite;
> `(custom_attributes->>'k')::numeric` no Postgres).

### Performance / índices

Filtros por status/assignee/inbox vão rodar a cada abertura da lista — precisam de índice.

- **Índices compostos** alinhados ao uso real. A lista quase sempre filtra +
  ordena por última atividade, então índices que terminam em `updated_at DESC`
  cobrem filtro + ordenação:
  - `CREATE INDEX ix_conv_status_updated ON conversations (status, updated_at DESC);`
  - `CREATE INDEX ix_conv_assignee_updated ON conversations (assignee_id, updated_at DESC);`
  - `CREATE INDEX ix_conv_inbox_status ON conversations (inbox_id, status);`
- **Índices parciais** (ambos SQLite e Postgres suportam `WHERE`) para casos
  hot e seletivos, ex. fila de não-atribuídas abertas:
  - `... ON conversations (updated_at DESC) WHERE assignee_id IS NULL AND status='open';`
- **JSONB no Postgres:** `CREATE INDEX ix_conv_cattr ON contacts USING gin (custom_attributes);`
  acelera `@>`/`?`. GIN derruba consultas de segundos para centenas de ms quando há
  volume (referência geral de indexação JSONB). **SQLite não tem GIN**: para um
  atributo custom muito usado, considerar *expression index*
  (`CREATE INDEX ... ON contacts (json_extract(custom_attributes,'$.plano'))`) ou,
  se virar gargalo, promover esse atributo a coluna real.
- Migrations Alembic (`db/alembic/versions/`) — criar os índices junto com as
  colunas nos docs 01/02/05; o WhatsBot aplica `alembic upgrade head` no boot.
- **Paginação obrigatória** quando a lista cresce: o endpoint atual devolve tudo.
  Com filtros, manter keyset pagination por `(updated_at, id)` em vez de `OFFSET`
  grande (mantém uso do índice).
- Evitar `func.lower(col).contains(...)` em colunas grandes sem índice — para busca
  textual pesada, manter a estratégia atual (`_contact_ids_matching_message`) e não
  misturá-la no caminho quente dos filtros estruturados.

---

## 6. Views / segmentos salvos (opcional, Fase 2)

Padrão consolidado: Chatwoot ("folders"/"segments"), Intercom ("custom views"),
Linear ("saved views") — todos persistem **o conjunto de filtros** e mostram no
sidebar para reuso (ver [Chatwoot custom segments](https://www.chatwoot.com/hc/user-guide/articles/1677698771-group-chats-with-filters-save-as-folders),
[Intercom custom views](https://www.intercom.com/help/en/articles/6588834-organize-your-inbox-with-custom-views-and-folders),
[Linear filters](https://linear.app/docs/filters)).

Como o WhatsBot armazena o filtro como `payload` JSON (§4 Opção B), salvar uma view
é só **persistir esse payload**.

### DDL ilustrativo (Alembic / `db/tables.py`)

```py
saved_filters = Table(
    "saved_filters", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("scope", Text, nullable=False, server_default="user"),  # 'user' | 'global'
    Column("created_by", Integer, ForeignKey("users.id", ondelete="CASCADE")),  # doc 03
    Column("query_json", Text, nullable=False),   # o payload de filtros (TEXT no SQLite, pode virar JSONB no PG)
    Column("sort", Text, server_default="-last_activity"),
    Column("position", Integer, nullable=False, server_default="0"),  # ordem no sidebar
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
```

Endpoints sugeridos:
- `GET /api/saved-filters` — lista views visíveis ao usuário (próprias + globais).
- `POST /api/saved-filters` — cria a partir de um payload de filtro.
- `PUT /api/saved-filters/{id}` / `DELETE /api/saved-filters/{id}`.
- `GET /api/contacts/filter?view=<id>` (ou injetar `query_json` no `POST /filter`).

Notas:
- `scope=global` exige permissão de admin (doc 03); `scope=user` é privada.
- Resolver tempos relativos **na execução**, não na gravação (ver §2).
- Validar o `query_json` contra o mesmo allowlist de `attribute_key`/operadores no
  momento de salvar **e** de executar (atributo custom pode ter sido removido depois).

---

## 7. Impacto no frontend e RBAC

### Frontend (`Contacts.js` + novos componentes)

- **Barra de filtros / chips de filtros ativos** acima da `ContactList`: cada
  critério aplicado vira um chip removível (`Status: Aberto ✕`, `Atendente: eu ✕`).
  Estado novo no `Contacts.js`, ex. `const [filters, setFilters] = useState([])`,
  passado ao `fetchContacts`. Hoje só há `search`/`showArchived`; o `showArchived`
  pode ser absorvido como mais um filtro (`status archived`) ou manter o toggle
  dedicado.
- **`getContacts`** em `services/api.js` ganha os params novos (MVP) ou um
  `filterContacts(payload)` que faz `POST /api/contacts/filter` (Fase 2).
- **Painel de filtro avançado** (Fase 2): modal/drawer com linhas
  `[atributo ▾] [operador ▾] [valor] [AND/OR ▾]`, alimentado por
  `GET /api/contacts/filter-schema`. Um único componente de linha genérico cobre
  todas as dimensões (o tipo do atributo decide o input: select para enum/inbox,
  autocomplete de usuários para assignee, multiselect para tags, date-picker para
  período, texto/número para custom).
- **Salvar view:** botão "Salvar filtro" → `POST /api/saved-filters`; views salvas
  listadas no menu (engrenagem / topo da sidebar) para aplicação 1-clique.
- **Modo escuro / legibilidade:** todos os componentes novos seguem a regra do
  `CLAUDE.md` — usar `wa-*` (`bg-wa-panel`, `text-wa-text`, `border-wa-border`) e
  `.wa-field` nos inputs/selects. Chips e dropdowns precisam de contraste nos dois
  temas.
- **WS / tempo real:** ao chegar `new_message`/mudança de status, a UI deve
  re-avaliar se a conversa ainda casa com os filtros ativos (Intercom/Chatwoot
  atualizam views "em tempo real"). MVP simples: refetch debounced quando um evento
  WS toca uma conversa fora do conjunto atual.

### RBAC (doc 03)

- **Atendente só filtra dentro dos seus inboxes.** O backend deve **interceptar** o
  filtro de inbox: se o usuário não é admin, intersectar `inbox_id` solicitado com
  os inboxes a que ele pertence (e default = só os dele). Nunca confiar no cliente
  para esse recorte — aplicar como cláusula obrigatória no `WHERE`, em cima do
  filtro do usuário.
- `assignee=me` resolve para o id do usuário autenticado no servidor.
- Views `scope=global` só podem ser criadas/editadas por admins; o
  `filter-schema` pode esconder dimensões que o papel não enxerga.
- Como o WhatsBot hoje tem auth simples (senha única, sem `users`), **isto depende
  inteiramente da entrega do doc 03**; até lá, o filtro roda sem recorte por
  atendente (instância single-operator).

---

## 8. Faseamento / MVP

1. **Fase 0 (pré-requisitos):** docs 01 (status/assignee), 02 (inboxes), 05
   (custom attrs) criam as colunas/tabelas + índices. Sem elas, não há o que filtrar.
2. **Fase 1 — Filtros básicos (MVP):** estender `GET /api/contacts` com
   `status`, `assignee` (`me`/`unassigned`/id), `inbox`, `tags` (lista) — só AND.
   Chips na UI + `filter-schema` para descobrir o que existe. Índices compostos.
   Entrega o grosso do valor com risco baixo.
3. **Fase 2 — Query builder + OR + custom attrs:** `POST /api/contacts/filter` com
   payload estruturado, operadores completos, AND/OR, atributos JSON/JSONB.
4. **Fase 3 — Views salvas:** tabela `saved_filters`, endpoints CRUD, UI de salvar
   e listar segmentos, scope user/global com RBAC.
5. **Fase 4 — Refinamentos:** atualização de views em tempo real via WS, ordenação
   configurável, nested groups (OR de grupos AND, estilo Linear) se houver demanda.

---

## 9. Perguntas em aberto

1. **Conversa vs. contato:** os docs 01/02 vão criar uma tabela `conversations`
   separada (1 contato → N conversas), ou status/assignee/inbox ficam direto em
   `contacts`? A modelagem dos filtros muda (JOIN vs. coluna direta). **Decisão
   bloqueante** — alinhar com 01/02 antes de implementar.
2. **Onde moram os custom attributes (doc 05):** coluna JSON única em `contacts` vs.
   tabela EAV (`contact_id, key, value`)? JSON é mais simples e casa com o payload;
   EAV é mais indexável no SQLite. Define a §5.
3. **OR aninhado** é realmente necessário no produto, ou AND entre dimensões + OR
   dentro de uma dimensão (via `in`/`contains_any`) basta? Chatwoot oferece AND/OR
   plano; Linear oferece grupos aninhados. Plano é bem mais simples — começar plano.
4. **Escopo das views globais:** por usuário, por inbox, por "time" (doc 01 menciona
   teams)? Impacta a coluna `scope` e o RBAC.
5. **Limite de paginação:** o endpoint atual devolve a lista inteira. Com filtros +
   instalações grandes, introduzir keyset pagination — definir o tamanho de página e
   se o frontend faz scroll infinito.
6. **Archived como filtro vs. toggle:** absorver `is_archived` no modelo de status
   ou manter o toggle dedicado atual? (UX + compatibilidade com `showArchived`.)
7. **Filtros aplicáveis pelo agente LLM?** Há valor em expor "listar conversas
   abertas não-atribuídas" como tool? Fora do escopo da sidebar, mas a mesma
   infra serviria.

---

## 10. Referências

- Chatwoot — Conversation Filter API (payload `attribute_key`/`filter_operator`/`values`/`query_operator`): https://developers.chatwoot.com/api-reference/conversations/conversations-filter
- Chatwoot — Conversations List API (status, labels, inbox_id, team_id, `q`): https://developers.chatwoot.com/api-reference/conversations/conversations-list
- Chatwoot — How to use Conversation Filters (operadores equal/not equal/present/not present, AND/OR): https://www.chatwoot.com/hc/user-guide/articles/1677688192-how-to-use-conversation-filters
- Chatwoot — Group chats with filters, save as folders (custom views/segments): https://www.chatwoot.com/hc/user-guide/articles/1677698771-group-chats-with-filters-save-as-folders
- Chatwoot — Group contacts using filters & save as segments: https://www.chatwoot.com/hc/user-guide/articles/1677748953-group-your-contacts-using-filters-and-save-them-as-custom-segments
- Chatwoot — Custom Filters (GitHub issue #3183): https://github.com/chatwoot/chatwoot/issues/3183
- Chatwoot — Custom attributes in API filters (issue #3736): https://github.com/chatwoot/chatwoot/issues/3736
- Intercom — Organize your Inbox with custom views and folders: https://www.intercom.com/help/en/articles/6588834-organize-your-inbox-with-custom-views-and-folders
- Intercom — How do filters work? (AND/OR): https://www.intercom.com/help/en/articles/2410715-how-do-filters-work
- Intercom — Multiple Filter Search Request (API): https://developers.intercom.com/docs/references/rest-api/api.intercom.io/models/multiple_filter_search_request
- Linear — Filters (advanced filters, grupos AND/OR aninhados, saved views): https://linear.app/docs/filters
- SQLAlchemy — JSON com SQLite e Postgres (discussão #9530): https://github.com/sqlalchemy/sqlalchemy/discussions/9530
- Searching Postgres JSON columns using SQLAlchemy (->> / @>): https://medium.com/code-on-a-boat/searching-postgres-json-columns-using-sqlalchemy-aece6ae5b0e9
- JSONB no SQLAlchemy (índices GIN, performance): https://www.geeksforgeeks.org/python/jsonb-sqlalchemy/
