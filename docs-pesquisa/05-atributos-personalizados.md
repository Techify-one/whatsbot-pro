# 05 — Atributos personalizados (contato e conversa)

> Pesquisa de arquitetura. Documento de design — não implementa nada.
> Relacionado: [01 — Conversas](01-conversas.md) (entidade `conversations`), [03 — Permissões](03-permissoes.md) (quem cria/edita definições e valores), [08 — Filtros e busca](08-filtros-busca.md) (consultar/filtrar por atributo).

## Resumo executivo

O cliente quer **atributos personalizados** (custom attributes): campos definidos pelo
administrador e preenchidos por atendentes ou pela IA, aplicáveis tanto ao **contato**
quanto à **conversa**. É o mesmo recurso que o Chatwoot chama de *Custom Attributes*.

O design proposto separa **definição** (metadados do campo: chave, rótulo, tipo, escopo)
de **valor** (o dado preenchido por contato/conversa). Para a tabela de definições, uma
tabela relacional comum. Para os valores, a recomendação — considerando que o WhatsBot
roda em **SQLite (default)** e **Postgres (opcional)** com **SQLAlchemy 2.0 Core** — é
uma **coluna JSON única** por registro (`contacts.custom_attributes`,
`conversations.custom_attributes`), com `with_variant()` para mapear `JSONB` no Postgres e
`JSON` (json1) no SQLite. Discutimos abaixo o trade-off contra EAV e por que JSON vence
neste contexto, com ressalvas de indexação/filtragem que ligam ao doc 08.

---

## 1. O que existe hoje

### 1.1 Campos fixos em `contacts`

A tabela `contacts` ([db/tables.py](../db/tables.py)) tem um conjunto **fechado** de
colunas de informação, todas `Text` com `server_default=""`:

```
name, email, profession, company, address
```

Além disso há flags operacionais (`ai_enabled`, `is_group`, `is_pinned`, `unread_count`,
`has_unread_mention`, …) e a relação com tags via `contact_tags`. Observações ficam numa
tabela separada (`observations`, uma linha por nota).

Características relevantes para esta feature:

- **Schema rígido**: adicionar um campo novo (ex.: "CPF", "plano contratado") exige
  migração Alembic + mudança em repos + frontend. Não há jeito de o admin criar um campo
  pela UI.
- **Não há entidade `conversation`** ainda — os atributos de conversa dependem do doc 01.
  Até lá, "atributo de conversa" não tem onde morar.
- **JSON já é usado como `Text`** no projeto: `messages.reactions` guarda
  `{emoji: [reactor,...]}` como string JSON numa coluna `Text` (não como tipo JSON nativo).
  Isso é um precedente — mas para atributos personalizados conviria usar o tipo JSON nativo
  (ver §4) para habilitar consultas.

### 1.2 `ContactInfoPanel.js`

O painel de info do contato
([web/static/js/components/contacts/ContactInfoPanel.js](../web/static/js/components/contacts/ContactInfoPanel.js))
renderiza os campos fixos a partir de um array **hardcoded** no componente:

```js
const fields = [
  { key: 'name', label: 'Nome', placeholder: 'Nome do contato' },
  { key: 'email', label: 'Email', placeholder: 'email@exemplo.com' },
  { key: 'profession', label: 'Profissão', placeholder: 'Ex: Desenvolvedor' },
  { key: 'company', label: 'Empresa', placeholder: 'Nome da empresa' },
  { key: 'address', label: 'Endereço', placeholder: 'Rua, número, bairro' },
];
```

Todos são `<input type="text">`. O componente também já gerencia tags e observações.
Salva via `updateContactInfo(phone, form)` + `updateContactTags(phone, tags)`.

Para suportar atributos personalizados, esse array passa a ter uma parte **dinâmica**
(carregada da API de definições) e a renderização precisa variar por tipo
(text/number/date/select/checkbox/link), não só `type="text"`.

---

## 2. Requisitos

**Funcionais**

1. Admin define atributos pela UI: chave, rótulo, tipo, escopo (contato | conversa),
   opções (para list), obrigatoriedade.
2. Tipos suportados (alinhados ao Chatwoot): **text, number, date, list (enum), checkbox
   (boolean), link (URL)**. Opcionalmente *currency*/*percent* depois.
3. Atendente preenche/edita valores no painel do contato e na sidebar da conversa.
4. A **IA** pode ler e escrever valores (via tool — ver §7.3).
5. Filtrar/buscar contatos e conversas por valor de atributo (liga ao doc 08).
6. Funciona **igual** em SQLite e Postgres.

**Não-funcionais**

- Validação por tipo no backend (não confiar só no frontend).
- `attribute_key` é identidade estável: não renomear depois de criado (quebra valores).
- Permissões: criar/editar **definições** é ação de admin; preencher **valores** é ação de
  atendente (doc 03).
- Modo escuro / `wa-field` no frontend (regra de tema do projeto).

---

## 3. Definições de atributo

Uma tabela relacional comum guarda os **metadados** dos campos. Espelha o modelo do
Chatwoot (`custom_attribute_definitions`), cujos campos são `attribute_display_name`,
`attribute_display_type`, `attribute_key`, `attribute_values`, `attribute_model`,
`regex_pattern`, `regex_cue`
([Chatwoot API](https://developers.chatwoot.com/api-reference/custom-attributes/add-a-new-custom-attribute)).
No Chatwoot, `attribute_display_type` e `attribute_model` são **enums inteiros**
(0=text,1=number,2=currency,3=percent,4=link,5=date,6=list,7=checkbox; e
0=conversation_attribute,1=contact_attribute). Para o WhatsBot recomendo **strings legíveis**
no banco (mais portável e auto-documentado), mantendo a mesma semântica.

### 3.1 DDL ilustrativo (SQLAlchemy Core / `db/tables.py`)

```python
custom_attribute_definitions = Table(
    "custom_attribute_definitions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # chave única do campo, snake_case. Identidade — NUNCA renomear.
    Column("attribute_key", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    # text | number | date | list | checkbox | link
    Column("type", Text, nullable=False, server_default="text"),
    # escopo: contact | conversation
    Column("applies_to", Text, nullable=False),
    # opções para type=list (enum). JSON array de strings. Null fora de 'list'.
    Column("options", _json_type(), nullable=True),
    Column("required", Integer, nullable=False, server_default="0"),
    Column("description", Text, nullable=False, server_default=""),
    # validação extra p/ text/link (Chatwoot: regex_pattern + regex_cue)
    Column("regex_pattern", Text, nullable=True),
    Column("regex_cue", Text, nullable=True),
    # ordenação na UI
    Column("position", Integer, nullable=False, server_default="0"),
    Column("created_by", Integer, nullable=True),  # FK -> users (doc 03)
    Column("created_at", Text, nullable=False),
    # unicidade POR escopo: a mesma key pode existir p/ contact e p/ conversation
    UniqueConstraint("attribute_key", "applies_to",
                     name="uq_attr_key_scope"),
)
```

`_json_type()` é o helper dialect-agnóstico descrito em §4.3 (JSONB no Postgres, JSON no
SQLite). Para `options` (array pequeno) o tipo JSON serve bem nos dois bancos.

Migração: `alembic revision --autogenerate -m "custom attribute definitions"` + revisão
manual (o autogenerate não acerta `with_variant`/`server_default` JSON em todos os casos).

---

## 4. Armazenamento dos valores

Aqui está o dilema central. Há três estratégias clássicas para dados definidos por usuário
([raz samuel — JSONB vs EAV](https://www.razsamuel.com/postgresql-jsonb-vs-eav-dynamic-data/),
[coussej — Replacing EAV with JSONB](https://coussej.github.io/2016/01/14/Replacing-EAV-with-JSONB-in-PostgreSQL/),
[Cybertec — EAV, don't do it](https://www.cybertec-postgresql.com/en/entity-attribute-value-eav-design-in-postgresql-dont-do-it/)).

### 4.1 Opção (a) — EAV (Entity-Attribute-Value)

Uma tabela de valores: uma linha por (entidade, atributo).

```python
custom_attribute_values = Table(
    "custom_attribute_values",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("definition_id", Integer,
           ForeignKey("custom_attribute_definitions.id"), nullable=False),
    Column("entity_type", Text, nullable=False),   # contact | conversation
    Column("entity_id", Integer, nullable=False),  # contacts.id / conversations.id
    Column("value_text", Text),
    Column("value_number", Float),
    Column("value_date", Text),
    Column("value_bool", Integer),
    UniqueConstraint("definition_id", "entity_type", "entity_id"),
)
```

**Prós**: cada valor tipado em coluna própria → `CHECK`/índice por coluna; consultas
relacionais "normais"; integridade referencial com a definição.
**Contras**: para montar a "ficha completa" de um contato com N atributos preciso de N
linhas / pivot / joins; escrita de vários atributos = várias linhas + manutenção de índice;
ocupa ~3x mais espaço que JSON e é mais lento para ler/escrever em lote
([raz samuel](https://www.razsamuel.com/postgresql-jsonb-vs-eav-dynamic-data/)).
A literatura desaconselha EAV salvo necessidade real
([Cybertec](https://www.cybertec-postgresql.com/en/entity-attribute-value-eav-design-in-postgresql-dont-do-it/)).
Em **SQLite** o problema é pior: sem tipos rígidos por coluna e com joins menos otimizados,
o pivot fica verboso.

### 4.2 Opção (b) — Coluna JSON/JSONB no próprio registro (RECOMENDADA)

Uma coluna por entidade guarda o mapa `{attribute_key: value}`:

```python
# em contacts e conversations, adicionar:
Column("custom_attributes", _json_type(), nullable=False,
       server_default="{}"),
```

Exemplo de conteúdo:

```json
{ "cpf": "123.456.789-00", "plano": "premium",
  "data_renovacao": "2026-09-01", "vip": true }
```

**Prós**: a ficha inteira vem numa leitura (sem join/pivot); escrever vários atributos é um
`UPDATE` só; menos espaço e mais rápido que EAV; no Postgres, **JSONB** indexável por GIN e
com operadores ricos (`->`, `->>`, `@>`, `has_key`); no SQLite, json1 oferece
`json_extract`/`->>` para filtrar. Encaixa com a feature ser **opcional e variável** por
instalação.
**Contras**: sem FK da definição para o valor (a coerência key↔definição é garantida na
aplicação); validação por tipo é responsabilidade da app — em JSON não dá `CHECK`/`ENUM`
diretos ([EDB anti-patterns](https://www.enterprisedb.com/blog/postgresql-anti-patterns-unnecessary-jsonhstore-dynamic-columns)).
Mas como aqui **não há JOIN entre os atributos** (são dados de ficha, não chaves
relacionais), essa é exatamente a situação em que JSON é a escolha certa.

#### Opção (c) — colunas dinâmicas (`ALTER TABLE` por atributo)

Descartada: criar/derrubar coluna a cada definição estoura o limite de colunas,
embaralha migrações e é frágil em SQLite (`ALTER TABLE` limitado). Não escala com
campos definidos por usuário.

### 4.3 Recomendação considerando SQLite **e** Postgres + Core

**Coluna JSON única (opção b)**, com tipo escolhido por dialeto via `with_variant()`:

```python
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

def _json_type():
    # JSONB no Postgres (indexável por GIN, operadores @>, has_key...),
    # JSON genérico no SQLite (mapeia p/ TEXT + json1 em runtime).
    return JSON().with_variant(JSONB(), "postgresql")
```

Por que isso funciona nos dois bancos:

- **SQLite**: não tem `JSONB`. O tipo `JSON` do SQLAlchemy persiste como `TEXT` e
  serializa/deserializa via `json` em Python; as funções **json1** (`json_extract`,
  operador `->>`) permitem filtrar do lado do banco
  ([SQLAlchemy/SQLite JSON](https://docs.sqlalchemy.org/en/14/dialects/postgresql.html)).
- **Postgres**: `JSONB` dá storage binário, dedup de chaves, operadores e **índice GIN**.
- **Core dialect-agnóstico**: `Column[...]['cpf']` (bracket/path) compila para o operador
  certo em cada dialeto; o código de repo permanece único.

Observação importante sobre o **precedente do projeto**: hoje JSON é guardado como `Text`
(ex.: `messages.reactions`) e serializado na mão. Para `custom_attributes` recomendo usar o
tipo `JSON`/`JSONB` **nativo** do SQLAlchemy (não `Text`) — é o que habilita a filtragem do
lado do banco (§5) sem carregar tudo para a aplicação. Manter `Text` obrigaria varredura em
Python e mataria a performance de filtros do doc 08.

> **Cuidado conhecido (mutation tracking)**: `JSON`/`JSONB` puros não detectam mutação
> *in-place* de dict. Sempre **reatribuir o dicionário inteiro** no `UPDATE`
> (`contacts.c.custom_attributes: novo_dict`), nunca `obj["k"] = v` esperando flush
> ([Beware of JSON fields in SQLAlchemy](https://amercader.net/blog/beware-of-json-fields-in-sqlalchemy/)).
> Como o WhatsBot usa **Core** (statements explícitos, sem ORM/`Session`), isso é natural.

---

## 5. Validação por tipo e consulta/filtro

### 5.1 Validação (backend, ao gravar valor)

Tabela de regras por `type`:

| type     | validação                                                         | normalização armazenada |
|----------|------------------------------------------------------------------|-------------------------|
| text     | string; se `regex_pattern`, casar (msg de erro = `regex_cue`)     | string                  |
| number   | conversível para float/int                                       | número JSON (não string) |
| date     | ISO `YYYY-MM-DD`                                                  | string ISO              |
| list     | valor ∈ `definition.options`                                     | string (uma opção)      |
| checkbox | bool                                                             | `true`/`false` JSON     |
| link     | URL válida (`http(s)://`)                                         | string                  |

Guardar number como número e checkbox como bool no JSON (não como texto) permite que o
Postgres compare numericamente/booleanamente — vantagem do JSONB sobre o EAV "tudo-texto"
([raz samuel](https://www.razsamuel.com/postgresql-jsonb-vs-eav-dynamic-data/)).
`required` é checado no submit do formulário e no PUT.

### 5.2 Consulta / filtro (liga ao doc 08)

Filtrar "contatos com `plano = premium`" ou "conversas com `vip = true`":

**Postgres (JSONB):**

```sql
-- igualdade textual
SELECT * FROM contacts WHERE custom_attributes ->> 'plano' = 'premium';
-- contém (usa GIN)
SELECT * FROM contacts WHERE custom_attributes @> '{"plano":"premium"}';
-- número
SELECT * FROM conversations
 WHERE (custom_attributes ->> 'score')::numeric > 80;
```

Índice: `CREATE INDEX ix_contacts_cattr ON contacts USING GIN (custom_attributes);`
acelera `@>`/`has_key`. Para um atributo muito filtrado, índice de expressão:
`CREATE INDEX ... ((custom_attributes ->> 'plano'));`.

**SQLite (json1):**

```sql
SELECT * FROM contacts
 WHERE json_extract(custom_attributes, '$.plano') = 'premium';
```

SQLite não tem GIN; para um campo muito filtrado, criar **índice de expressão**:
`CREATE INDEX ix_c_plano ON contacts (json_extract(custom_attributes,'$.plano'));`.

**Em Core (mesmo código nos dois bancos):**

```python
from db.tables import contacts
stmt = select(contacts).where(
    contacts.c.custom_attributes["plano"].as_string() == "premium"
)
```

O builder de filtros do doc 08 deve traduzir cada (atributo, operador) para a expressão
Core acima, consultando a **definição** para saber o tipo (string vs number vs bool) e
escolher `as_string()/as_float()/as_boolean()`. Recomendação de performance: oferecer
índices só para os atributos que o usuário marcar como "filtrável" (evita inchar todo
`UPDATE` com manutenção de índice).

---

## 6. Impacto no frontend

### 6.1 Tela de definição (ADM) — nova

Tela (provavelmente sob o menu da engrenagem, restrita a admin — doc 03) para CRUD de
definições: lista + formulário com `display_name`, `attribute_key` (auto-derivada do nome,
imutável após criar), `type` (select), `applies_to` (contact|conversation),
`options` (quando `type=list`), `required`, `description`, `regex_pattern`/`regex_cue`
(quando text/link). Reusar `.wa-field` e classes `wa-*` (tema escuro).

### 6.2 Renderização dinâmica no `ContactInfoPanel.js`

O array `fields` hardcoded ganha uma seção dinâmica vinda de
`GET /api/custom-attributes?applies_to=contact`. Render por tipo:

| type     | controle                                              |
|----------|------------------------------------------------------|
| text     | `<input type="text">` (`.wa-field`)                   |
| number   | `<input type="number">`                              |
| date     | `<input type="date">` (segue `color-scheme` do tema) |
| list     | `<select>` com `options`                             |
| checkbox | `<input type="checkbox">`                            |
| link     | `<input type="url">` + render como `<a>` em leitura  |

O `form` ganha um objeto `customAttributes`; o save envia junto (ou em endpoint próprio,
§7.2). Reaproveitar o padrão de `updateContactInfo`.

### 6.3 Sidebar da conversa

Quando o doc 01 entregar a entidade `conversations` e seu painel lateral, repetir o mesmo
renderizador para `applies_to=conversation`. Idealmente extrair um componente
`<CustomAttributeField>` reutilizado por contato e conversa.

---

## 7. Impacto no backend

### 7.1 Rotas de **definições** (CRUD) — admin (doc 03)

| Método | Endpoint                          | Descrição                                  |
|--------|-----------------------------------|--------------------------------------------|
| GET    | `/api/custom-attributes`          | Lista definições (`?applies_to=contact\|conversation`) |
| POST   | `/api/custom-attributes`          | Cria definição (valida `key`, tipo, options) |
| PUT    | `/api/custom-attributes/{id}`     | Edita rótulo/options/required/desc (NÃO a key/tipo após uso) |
| DELETE | `/api/custom-attributes/{id}`     | Remove definição (decidir: limpar valores órfãos no JSON) |

### 7.2 Gravação de **valores** — atendente (doc 03)

Reaproveitar os endpoints de info existentes, estendendo o payload:

- `PUT /api/contacts/{phone}/info` aceita `custom_attributes: {key: value}` →
  valida contra as definições `applies_to=contact` → merge no JSON → `UPDATE`.
- `PUT /api/conversations/{id}` (doc 01) idem para `applies_to=conversation`.

Repo: `custom_attribute_repo` com `list_definitions()`, `upsert_definition()`,
`set_value(entity, key, value)` (valida + reatribui o dict — §4.3) e
`get_values(entity)`. Tudo em Core via `get_engine()` + `db.tables`, rodando em
`asyncio.to_thread`.

### 7.3 IA

Tool core nova (ex.: `set_custom_attribute`) seguindo o contrato do projeto
(`agent/tools/<name>.py` + tupla em `CORE_TOOLS`): recebe `(key, value)`, resolve a
definição, valida e grava. As definições podem ser injetadas no system prompt
("você pode preencher: plano, cpf, …") para a IA saber o que existe. Sem `if/elif` por nome
— dispatch genérico do handler.

### 7.4 Permissões (doc 03)

- **Definições**: criar/editar/excluir = papel **admin**.
- **Valores**: ler/gravar = **atendente** (e IA). Definir no doc 03 se há atributos
  "somente-admin" (campo extra `editable_by` na definição) — sugerido como fase 2.

---

## 8. Faseamento / MVP

**Fase 1 (MVP)** — só **contato**, aproveitando que `contacts` já existe:
1. Tabela `custom_attribute_definitions` + coluna `contacts.custom_attributes` (JSON variant).
2. Tipos: text, number, list, checkbox (deixar date/link p/ fase 1.5 se apertar).
3. CRUD de definições (admin) + render dinâmico no `ContactInfoPanel`.
4. Validação por tipo no backend; gravação via `PUT /api/contacts/{phone}/info`.

**Fase 2** — **conversa** (depende do doc 01):
5. Coluna `conversations.custom_attributes` + sidebar da conversa.
6. Tool de IA `set_custom_attribute`.

**Fase 3** — **filtros** (doc 08):
7. Filtro por atributo na busca; índices GIN (PG) / expressão (SQLite) para campos
   marcados como filtráveis; flag `editable_by`/`filterable` na definição.

---

## 9. Perguntas em aberto

1. **`attribute_key` imutável**: confirmar regra de não-renomear e o que fazer com valores
   ao **excluir** uma definição (limpar do JSON num batch? deixar órfão? soft-delete?).
2. **Escopo de unicidade da key**: a mesma key pode coexistir em `contact` e `conversation`
   (o UNIQUE proposto permite). Confirmar com o cliente.
3. **Atributos calculados/IA-only vs manuais**: precisa marcar quais campos a IA pode
   escrever? (campo `writable_by_ai`?)
4. **Tipo number — inteiro vs decimal/moeda**: o Chatwoot tem currency/percent separados.
   Entra no MVP ou fica como `number` cru?
5. **Migrar campos fixos** (`profession`, `company`, `address`) para atributos
   personalizados no futuro, ou conviver com os dois? (compatibilidade de histórico).
6. **Performance de filtro em SQLite**: aceitável depender de índice de expressão por
   atributo, ou limitar nº de atributos filtráveis? (ligar com doc 08).
7. **Mutation tracking**: padronizar no repo o "reatribui o dict inteiro" — documentar para
   plugins que toquem em `custom_attributes`.

---

## 10. Referências

**Chatwoot — Custom Attributes**
- [Add a new custom attribute (API)](https://developers.chatwoot.com/api-reference/custom-attributes/add-a-new-custom-attribute) — campos `attribute_display_name`, `attribute_display_type` (0=text…7=checkbox), `attribute_key`, `attribute_values`, `attribute_model` (0=conversation,1=contact), `regex_pattern`, `regex_cue`.
- [How to create and use custom attributes (User Guide)](https://www.chatwoot.com/hc/user-guide/articles/1677502327-how-to-create-and-use-custom-attributes)
- [Custom Attributes — feature page](https://www.chatwoot.com/features/custom-attributes/)
- [feat: Custom attributes — issue #2863](https://github.com/chatwoot/chatwoot/issues/2863)

**EAV vs JSON/JSONB**
- [PostgreSQL JSONB vs. EAV — raz samuel](https://www.razsamuel.com/postgresql-jsonb-vs-eav-dynamic-data/) (storage ~3x, writes, validação)
- [Replacing EAV with JSONB in PostgreSQL — coussej](https://coussej.github.io/2016/01/14/Replacing-EAV-with-JSONB-in-PostgreSQL/)
- [EAV design in PostgreSQL — don't do it! — Cybertec](https://www.cybertec-postgresql.com/en/entity-attribute-value-eav-design-in-postgresql-dont-do-it/)
- [PostgreSQL anti-patterns: unnecessary json/hstore dynamic columns — EDB](https://www.enterprisedb.com/blog/postgresql-anti-patterns-unnecessary-jsonhstore-dynamic-columns) (limitação de CHECK/ENUM em JSON)
- [Laravel Custom Fields: JSON, EAV, or Same Table?](https://laraveldaily.com/post/laravel-custom-fields-json-eav-model-same-table)

**SQLAlchemy JSON / SQLite vs Postgres**
- [PostgreSQL dialect — JSON/JSONB (SQLAlchemy 1.4 docs)](https://docs.sqlalchemy.org/en/14/dialects/postgresql.html) (`has_key`, `@>`, GIN)
- [Beware of JSON fields in SQLAlchemy — Adrià Mercader](https://amercader.net/blog/beware-of-json-fields-in-sqlalchemy/) (mutation tracking)
- [ORM JSON + UniqueConstraint para SQLite e Postgres — discussion #9530](https://github.com/sqlalchemy/sqlalchemy/discussions/9530)
- [Use JSONB as alias for JSON — discussion #8160](https://github.com/sqlalchemy/sqlalchemy/discussions/8160) (`with_variant`)
</content>
</invoke>
