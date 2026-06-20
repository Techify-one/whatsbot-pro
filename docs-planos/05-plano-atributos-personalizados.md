# 05 — Plano de implementação: Atributos personalizados (contato e conversa)

> Plano acionável derivado de [docs-pesquisa/05-atributos-personalizados.md](../docs-pesquisa/05-atributos-personalizados.md).
> Escopo: coluna JSON única (`JSON().with_variant(JSONB, "postgresql")`) em `contacts` e `conversations`, tabela `custom_attribute_definitions`, endpoints REST de definições + valores, tool de IA e UI de definição/edição.
> Versão alvo: **WhatsBot Pro** (server-hosted, single-company, multi-user).
>
> **Decisão de escopo (P54):** precisamos de atributos personalizados de **CONTATO e de CONVERSA**
> (igual ao Chatwoot, que separa por `attribute_model`). Ambos são MVP. A entidade `conversations`
> vem do plano 01, então o escopo conversation entra assim que o 01 existir (Fase 5) — mas o desenho
> (UNIQUE por `(key, applies_to)`, repo genérico por tabela, componente de campo reutilizável) já
> nasce preparado para os dois escopos desde a Fase 1; não é um "talvez futuro".
>
> **Banco (decisão global):** Postgres é o backend de referência do Pro. A coluna de valores é
> `JSONB` no Postgres (suporta `@>`, `has_key`, índice **GIN**) e `JSON` (TEXT + json1) no SQLite. O
> filtro pesado por atributo (Fase 6 / plano 08) pode **exigir Postgres**; no SQLite degrada para
> índice de expressão por key.

---

## Visão geral da abordagem

Seguimos a recomendação da pesquisa (§4.2/§4.3):

- **Definições** numa tabela relacional `custom_attribute_definitions` (metadados: chave, rótulo, tipo, escopo, opções, regex…). O campo `applies_to` (`contact|conversation`) separa os dois escopos (P54), permitindo a **mesma key** nos dois (P51).
- **Valores** numa **coluna JSON única por registro**: `contacts.custom_attributes` e (na Fase 5, quando o plano 01 entregar `conversations`) `conversations.custom_attributes`, tipadas com `JSON().with_variant(JSONB(), "postgresql")` — **JSONB no Postgres** (GIN/`@>`/`has_key`), JSON no SQLite.
- Validação **no backend** por tipo, consultando a definição.
- Reaproveitar o endpoint `PUT /api/contacts/{phone}/info` ([server/routes/contacts.py:971](../server/routes/contacts.py)) estendendo o payload, e adicionar CRUD de definições.
- Tool core nova para a IA seguindo o contrato do projeto (`agent/tools/<name>.py` + tupla em `CORE_TOOLS`). **A IA pode gravar todos os atributos no MVP** (P53); a coluna `writable_by_ai` (default true) fica planejada para a fase de RBAC (plano 03).
- RBAC: criar/editar/excluir **definições** = admin; gravar **valores** = atendente + IA (depende do plano 03).

Decisão técnica importante (precedente do projeto): hoje JSON é persistido como `Text` serializado à mão (`messages.reactions` em [db/tables.py:92](../db/tables.py)). Para `custom_attributes` usamos o **tipo JSON nativo** do SQLAlchemy — é o que habilita filtragem do lado do banco (plano 08) sem varrer tudo em Python.

---

## Pré-requisito técnico: helper `_json_type()` e mutation tracking

Antes de qualquer migration, adicionar em [db/tables.py](../db/tables.py) um helper dialect-agnóstico (no topo, junto dos imports):

```python
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

def _json_type():
    # JSONB no Postgres (GIN, @>, has_key); JSON genérico no SQLite (TEXT + json1).
    return JSON().with_variant(JSONB(), "postgresql")
```

**Regra de ouro (documentar no repo):** `JSON`/`JSONB` não detectam mutação in-place de dict. **Sempre reatribuir o dicionário inteiro** no UPDATE (`values(custom_attributes=novo_dict)`), nunca `obj["k"] = v`. Como o projeto é Core puro (statements explícitos, sem Session), isso é natural — mas precisa estar escrito no docstring do repo novo e citado no CLAUDE.md.

---

## Fase 1 — Schema + camada de dados (contato)

### 1.1 Tabela de definições + coluna JSON em `contacts`

Editar [db/tables.py](../db/tables.py):

```python
custom_attribute_definitions = Table(
    "custom_attribute_definitions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("attribute_key", Text, nullable=False),      # snake_case, IDENTIDADE — nunca renomear
    Column("display_name", Text, nullable=False),
    Column("type", Text, nullable=False, server_default="text"),   # text|number|date|list|checkbox|link
    Column("applies_to", Text, nullable=False),         # contact|conversation
    Column("options", _json_type(), nullable=True),     # JSON array de strings (só p/ type=list)
    Column("required", Integer, nullable=False, server_default="0"),
    Column("description", Text, nullable=False, server_default=""),
    Column("regex_pattern", Text, nullable=True),
    Column("regex_cue", Text, nullable=True),
    Column("position", Integer, nullable=False, server_default="0"),
    Column("created_by", Integer, nullable=True),       # FK -> users (plano 03), nullable por ora
    Column("created_at", Float, nullable=False),        # epoch float, padrão do projeto (P56)
    Column("deleted_at", Float, nullable=True),         # soft-delete (P49): NULL = ativa
    # P51: UNIQUE por (key, escopo) -> a MESMA key pode existir em contact e conversation
    UniqueConstraint("attribute_key", "applies_to", name="uq_attr_key_scope"),
)
Index("idx_cad_applies_to", custom_attribute_definitions.c.applies_to)

# Em contacts, adicionar:
Column("custom_attributes", _json_type(), nullable=False, server_default="{}"),
```

Notas:
- `created_at` segue o padrão do projeto (epoch `Float`), não `Text`, divergindo do DDL ilustrativo da pesquisa para manter consistência com as demais tabelas.
- `created_by` fica `nullable=True` agora; vira FK real quando o plano 03 entregar `users`.
- `CORE_TABLES` ([db/tables.py:207](../db/tables.py)) é derivado de `metadata.sorted_tables`, então a nova tabela entra automaticamente na lista usada pela migração SQLite→Postgres.

### 1.2 Migration Alembic

Criar `db/alembic/versions/<data>_0007_custom_attributes.py`:
- `down_revision = "0006_contact_mention"` (última atual — ver [db/alembic/versions/](../db/alembic/versions/)).
- `upgrade()`:
  - `op.create_table("custom_attribute_definitions", ...)` com as colunas acima (incluindo `deleted_at` Float nullable — soft-delete, P49). Para a coluna `options`, usar `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")`.
  - `op.create_index("idx_cad_applies_to", ...)`.
  - `op.add_column("contacts", sa.Column("custom_attributes", <json variant>, nullable=False, server_default="{}"))`.
- `downgrade()`: `drop_column` + `drop_table`.

**Atenção:** o autogenerate do Alembic **não** acerta `with_variant`/`server_default` JSON de forma confiável — escrever a migration **à mão** (espelhando o estilo de [20260603_0005_contact_pinned.py](../db/alembic/versions/20260603_0005_contact_pinned.py)). Testar `alembic upgrade head` em SQLite **e** Postgres.

### 1.3 Repositório `custom_attribute_repo`

Criar `db/repositories/custom_attribute_repo.py` (Core puro, `get_engine()` + `db.tables`, síncrono — chamado via `asyncio.to_thread`):

```python
# Definições
def list_definitions(applies_to: str | None = None) -> list[dict]   # só ativas (deleted_at IS NULL)
def get_definition(def_id: int) -> dict | None
def get_definitions_map(applies_to: str) -> dict[str, dict]   # key -> definição ativa (p/ validação)
def create_definition(**fields) -> dict                       # valida key única por (key, applies_to)
def update_definition(def_id: int, **fields) -> dict          # NÃO altera attribute_key/type/applies_to
def delete_definition(def_id: int) -> None                    # SOFT-DELETE: set deleted_at = now (P49)
def purge_orphan_values(applies_to: str) -> int               # ação admin opcional (batch, P49)

# Valores (genérico por entidade; fase 1 contact, fase 5 conversation)
def set_values(entity_table, entity_id: int, partial: dict) -> dict
    # 1. carrega custom_attributes atual; 2. merge partial; 3. reatribui dict INTEIRO no UPDATE
def get_values(entity_table, entity_id: int) -> dict
```

- `set_values` deve **reatribuir o dict inteiro** (regra mutation tracking) e atualizar `updated_at` do registro.
- Validação por tipo vive num módulo `db/repositories/custom_attribute_validate.py` (ou função no repo): tabela de regras da pesquisa §5.1 — text/regex, number→float (`number` cru, P52), date→ISO `YYYY-MM-DD`, list→∈options, checkbox→bool, link→URL. Normaliza number como número JSON e checkbox como bool JSON (não string).
- `delete_definition` faz **soft-delete** (P49): grava `deleted_at = time.time()`; a definição some das listagens/validação, mas os valores permanecem no JSON dos registros. `purge_orphan_values` (opcional, acionada pelo admin) varre os registros e remove do JSON as keys sem definição ativa — é o batch "limpar órfãos".

### 1.4 Critério de pronto (Fase 1)

- `alembic upgrade head` cria a tabela + coluna em SQLite e Postgres sem erro.
- Teste unitário do repo: criar definição, gravar valor válido, rejeitar valor inválido por tipo, ler de volta o JSON. Verificável via novo bloco em `tests/test_endpoints.py` ou script ad-hoc com `WHATSBOT_TEST_DB_URL` apontando pra Postgres.

---

## Fase 2 — Endpoints REST (definições + valores) — contato

### 2.1 CRUD de definições (admin)

Criar `server/routes/custom_attributes.py` (seguindo o padrão `def register_routes(app, deps)` dos outros módulos em [server/routes/](../server/routes/), ex.: [server/routes/tags.py:16](../server/routes/tags.py)), registrado em [server/app.py](../server/app.py) — adicionar o módulo ao import da linha 18 e chamar `custom_attributes.register_routes(app, deps)` junto das demais (linhas ~304-312):

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/custom-attributes` | Lista definições (`?applies_to=contact\|conversation`) |
| POST | `/api/custom-attributes` | Cria (valida key snake_case, tipo, options p/ list) |
| PUT | `/api/custom-attributes/{id}` | Edita display_name/options/required/description/regex/position — **NÃO** key/type/applies_to |
| DELETE | `/api/custom-attributes/{id}` | **Soft-delete** (P49): grava `deleted_at`; valores preservados no JSON |
| POST | `/api/custom-attributes/purge-orphans?applies_to=contact\|conversation` | Ação admin opcional: batch que remove do JSON as keys sem definição ativa (P49) |

- Formato de resposta `{"ok", "data", "error"}` (helper `_ok`/`_err` já usados no projeto).
- Emitir eventos no bus: `custom_attribute.created/updated/deleted` (via `emit_with_filter`, seguindo o padrão de `tag.created` etc.) — útil para plugins e cache de frontend.
- Proteção por papel admin: hoje não há RBAC; deixar TODO marcado e o gate atrás do middleware de auth que o plano 03 entregará (ver Dependências). No MVP, exige sessão autenticada.

### 2.2 Gravação de valores — estender info do contato

Editar `update_contact_info` em [server/routes/contacts.py:971](../server/routes/contacts.py):
- Aceitar `custom_attributes: {key: value}` no body.
- Carregar `get_definitions_map("contact")`, validar cada par; **key sem definição ativa → erro 400** com mensagem clara (P50), nunca gravar livre; checar `required` no submit.
- Persistir via `custom_attribute_repo.set_values(contacts, contact.id, valid_partial)`.
- Incluir os valores no payload do evento `contact.updated` e na resposta.

Também estender a leitura: `GET /api/contacts/{phone}` usa `contact_repo.get_full_contact` ([db/repositories/contact_repo.py:466](../db/repositories/contact_repo.py)) — adicionar `custom_attributes` no `_row_to_dict`/`get_full_contact` (a coluna passa a existir na linha; basta expô-la no dict).

### 2.3 Critério de pronto (Fase 2)

- Via `curl`/TestClient: criar definição `plano` (type=list, options `["free","premium"]`), `PUT /info` com `custom_attributes:{plano:"premium"}` grava; `{plano:"x"}` retorna erro de validação; `GET /contacts/{phone}` devolve `custom_attributes`.
- Novos checks em `tests/test_endpoints.py` (CRUD de definições + round-trip de valor + validação rejeitada).

---

## Fase 3 — Frontend: definição (admin) + edição no painel de contato

### 3.1 Serviços de API

Adicionar em [web/static/js/services/api.js](../web/static/js/services/api.js):
`getCustomAttributes(appliesTo)`, `createCustomAttribute(def)`, `updateCustomAttribute(id, def)`, `deleteCustomAttribute(id)`. `updateContactInfo` passa a enviar `custom_attributes` no corpo.

### 3.2 Tela de definição (admin) — nova

Criar `web/static/js/components/CustomAttributesManager.js`:
- Lista + formulário: `display_name`, `attribute_key` (auto-derivada do nome via slugify snake_case, **imutável após criar**), `type` (select), `applies_to` (contact|conversation; conversation desabilitado/oculto até a fase de conversas), `options` (editor de lista quando type=list), `required`, `description`, `regex_pattern`/`regex_cue` (quando text/link).
- Registrar a tela no menu da engrenagem (mesma mecânica de `ConfigPanel`/`ToolsManager` em [web/static/js/components/](../web/static/js/components/) e na navegação de `app.js`). Item visível só para admin (gate do plano 03).
- **Tema escuro obrigatório:** usar classes `wa-*` e `.wa-field` em todos os inputs/selects (regra do CLAUDE.md). Testar com modo escuro ligado.

### 3.3 Componente reutilizável `CustomAttributeField`

Criar `web/static/js/components/contacts/CustomAttributeField.js` — render por tipo (pesquisa §6.2):

| type | controle |
|------|----------|
| text | `<input type="text" class="wa-field">` |
| number | `<input type="number" class="wa-field">` |
| date | `<input type="date" class="wa-field">` (segue `color-scheme`) |
| list | `<select class="wa-field">` com options |
| checkbox | `<input type="checkbox">` |
| link | `<input type="url" class="wa-field">`; em leitura, `<a>` |

Reutilizável por contato e (fase posterior) conversa.

### 3.4 Integrar no `ContactInfoPanel.js`

Editar [web/static/js/components/contacts/ContactInfoPanel.js](../web/static/js/components/contacts/ContactInfoPanel.js):
- O array `fields` hardcoded continua para os campos fixos; abaixo dele, uma **seção dinâmica** que faz fetch de `getCustomAttributes('contact')` e renderiza um `<CustomAttributeField>` por definição.
- `form` ganha sub-objeto `customAttributes` inicializado de `info.custom_attributes` no `useEffect` de sync (linha ~32).
- `onSave`/`updateContactInfo` envia `custom_attributes` junto.

### 3.5 Critério de pronto (Fase 3)

- Admin cria um atributo `cpf` (text) e `vip` (checkbox) pela tela nova; eles aparecem no painel do contato; preencher e salvar persiste; reabrir mostra os valores; tudo legível no modo escuro.

---

## Fase 4 — Tool de IA (`set_custom_attribute`)

Seguindo o contrato do projeto (CLAUDE.md, espelhando [agent/tools/save_contact_info.py](../agent/tools/save_contact_info.py)):

- Criar `agent/tools/set_custom_attribute.py` com `SET_CUSTOM_ATTRIBUTE_TOOL` (schema) + `execute(ctx, args)`:
  - `args = {key, value}`. Resolve a definição (`applies_to=contact`), valida, grava via repo. Retorna `None` (segue mensagem default) ou string de erro pro LLM.
- Registrar a tupla em `CORE_TOOLS` em [agent/tools/__init__.py:27](../agent/tools/__init__.py). **Sem if/elif por nome** — dispatch já é genérico.
- Injetar as definições disponíveis no system prompt: estender `get_info_summary`/montagem do prompt em [agent/memory.py:286](../agent/memory.py) (ou no handler) para listar "Atributos que você pode preencher: cpf, plano (free|premium), …". Mostrar também os valores já preenchidos junto da seção "Informações já conhecidas".
- A tool vira row automática em `tool_overrides` (via `_register_tool`), aparecendo na tela `/tools` para o admin customizar/desligar.

**Critério de pronto:** numa conversa de teste (Evolution API ou sandbox), a IA detecta "meu plano é premium" e grava `plano=premium`; valor inválido é rejeitado e a IA recebe o erro.

---

## Fase 5 — Conversa (escopo MVP — depende do plano 01 para a entidade)

> Atributos de conversa são **MVP** (P54), não opcional. Esta fase só fica depois das 1–4 porque
> precisa da entidade `conversations` do plano 01. Todo o desenho (repo genérico, UNIQUE por escopo,
> componente de campo) já está pronto para conversation desde a Fase 1.

Quando `conversations` existir ([plano 01](01-plano-conversas.md)):
- Adicionar `conversations.custom_attributes` (`_json_type()`, server_default `"{}"`) via nova migration.
- `set_values`/`get_values` já são genéricos por tabela — reutilizar.
- Habilitar `applies_to=conversation` na tela de definições e renderizar `CustomAttributeField` na sidebar da conversa (reuso do componente).
- Endpoint `PUT /api/conversations/{id}` aceita `custom_attributes`.

**Critério de pronto:** atributo de escopo conversation aparece e persiste na sidebar da conversa.

---

## Fase 6 — Filtros e índices (depende do plano 08)

- Builder de filtros traduz `(atributo, operador, valor)` para expressão Core, consultando a definição para escolher `as_string()/as_float()/as_boolean()` (pesquisa §5.2).
- Flag `filterable` na definição (migration adicional) para criar índice **só nos campos marcados** (P55) — limita o número de atributos filtráveis e evita índice em tudo:
  - **Postgres (referência):** `CREATE INDEX ... USING GIN (custom_attributes)` para os operadores JSONB (`@>`, `has_key`) e/ou índice de expressão B-tree por key (`((custom_attributes ->> 'plano'))`) para igualdade/ordenação. Caminho preferido.
  - **SQLite (degradação):** índice de expressão `(json_extract(custom_attributes,'$.plano'))` por key marcada. Sem GIN; filtro pesado/multi-atributo pode **exigir Postgres** (documentar e degradar com elegância — decisão global de banco).

**Critério de pronto:** filtrar contatos/conversas por `plano=premium` retorna o conjunto correto; no Postgres o plano de execução usa o índice GIN/expressão; no SQLite usa o índice de expressão da key marcada `filterable`.

---

## Dependências de outros planos

- **Plano 03 (Permissões/RBAC):** gate de admin nos endpoints/tela de **definições** e gate de atendente nos **valores**. Sem ele, MVP só exige sessão autenticada e marca os gates como TODO. `created_by` vira FK real para `users`.
- **Plano 01 (Conversas):** entidade `conversations` é pré-requisito da Fase 5 (atributos de conversa). Fases 1–4 não dependem dele.
- **Plano 08 (Filtros e busca):** consome a coluna JSON; a Fase 6 deste plano é a parte de schema/índice que o 08 precisa.

---

## Resumo de artefatos

**Migrations:** `0007_custom_attributes` (tabela `custom_attribute_definitions` com `deleted_at` para soft-delete + coluna `custom_attributes` em contacts); fase 5: coluna em conversations; fase 6: flag `filterable` + índices (GIN/expressão no Postgres, expressão no SQLite).
**Backend novo:** `db/repositories/custom_attribute_repo.py`, `db/repositories/custom_attribute_validate.py`, `server/routes/custom_attributes.py`, `agent/tools/set_custom_attribute.py`, helper `_json_type()` em `db/tables.py`.
**Backend editado:** `db/tables.py`, `server/routes/contacts.py` (PUT/GET info), `db/repositories/contact_repo.py` (expor `custom_attributes`), `agent/tools/__init__.py`, `agent/memory.py` (prompt), `tests/test_endpoints.py`.
**Frontend novo:** `CustomAttributesManager.js`, `contacts/CustomAttributeField.js`.
**Frontend editado:** `services/api.js`, `contacts/ContactInfoPanel.js`, `app.js` (rota/menu).
**Dependências pip/JS novas:** nenhuma (JSON/JSONB já no SQLAlchemy; frontend sem build step).

---

## Perguntas em aberto

> Todas as decisões funcionais deste plano (P49–P56) estão **fechadas**. As entradas abaixo são
> mantidas como rastro histórico; cada decisão já foi incorporada no corpo do plano.

1. **Exclusão de definição e valores órfãos.** ✅ DECIDIDO (2026-06-19): **(c) soft-delete** da definição (`deleted_at`/`deleted` na própria tabela; esconde da UI, preserva o valor no JSON) + ação administrativa **opcional** "limpar valores órfãos" (batch UPDATE) para quem quiser higienizar. (P49)

2. **Keys desconhecidas no PUT de valores.** ✅ DECIDIDO (2026-06-19): **(a) erro 400** com mensagem clara, garantindo coerência key↔definição na aplicação (o JSON não tem FK). (P50)

3. **Escopo de unicidade da key.** ✅ DECIDIDO (2026-06-19): **(a) permitir a mesma key** em `contact` e `conversation` (UNIQUE por `(attribute_key, applies_to)`), expondo o escopo claramente na UI — alinhado ao Chatwoot (`attribute_model`). (P51)

4. **`number`: inteiro vs decimal/moeda.** ✅ DECIDIDO (2026-06-19): **(a) `number` cru** no MVP; currency/percent ficam para fase posterior (só formatação de exibição, mesmo storage numérico). (P52)

5. **Atributos graváveis pela IA.** ✅ DECIDIDO (2026-06-19): **(a) IA grava todos** no MVP; flag `writable_by_ai` (default true) planejada para a fase de RBAC (plano 03). (P53)

6. **Migrar campos fixos (`profession`, `company`, `address`) para atributos personalizados.** ✅ DECIDIDO (2026-06-19): **(a) conviver** — campos fixos continuam colunas; custom são aditivos. Reforço da decisão: **precisamos de atributos tanto de CONTATO quanto de CONVERSA** (igual Chatwoot) — ambos os escopos são MVP, não só contato. (P54)

7. **Performance de filtro em SQLite.** ✅ DECIDIDO (2026-06-19): **(a) índice só para campos marcados `filterable`** (decidido junto com o plano 08). Caminho de banco: **Postgres é o backend de referência** — filtros usam **JSONB + índice GIN** (e/ou índice de expressão por key); SQLite usa índice de expressão `json_extract(...)`. Recurso de filtro pesado pode **exigir Postgres** (degradar com elegância no SQLite). (P55 + decisão global de banco)

8. **`created_at` como epoch Float vs ISO Text.** ✅ DECIDIDO (2026-06-19): **epoch `Float`** para consistência com o projeto (já refletido no DDL). (P56)
