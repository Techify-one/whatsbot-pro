# Plano de Implementação — 19: Atributos **padrão** do contato (estrutura unificada) + fix do CPF

> A IA chamou a tool de cadastrar **CPF**, o **card apareceu** no fio, mas o CPF **não foi salvo** nas
> informações do contato. Causa raiz (verificada): **não existe** coluna `cpf` nem **definição** de atributo
> `cpf` numa instalação normal — `cpf` só aparece em [`seed_demo.py:178`](../seed_demo.py#L178), um seeder de
> demonstração que **não roda no boot**. Então a tool não tinha onde gravar:
> - `set_custom_attribute(key="cpf")` → "atributo não existe" devolvido ao LLM, **nada gravado**;
> - `save_contact_info(cpf=…)` → `update_info` **descarta** kwargs desconhecidos (só aceita
>   name/email/profession/company/address/observation);
> - em **ambos** os casos o **card é mostrado mesmo assim**, porque ele é montado só com `{tool, args}` e
>   **ignora o resultado** da tool.
>
> **Decisão do produto (travada — opção A, incremental):** introduzir **atributos padrão do contato** com a
> **mesma estrutura** dos atributos personalizados (plano 05). Adicionar uma flag `is_system` em
> `custom_attribute_definitions`, **semear** definições de sistema no boot (começando por **`cpf`**, escopo
> contato) e renderizá-las no painel "Dados do contato" junto com os personalizados. As colunas escalares
> atuais (name/email/profession/company/address) **permanecem** como estão (a busca depende de `contacts.name`).
>
> **Escopo:** (1) coluna `is_system` + seed idempotente de definições de sistema; (2) proteção na UI/CRUD
> (não apagar/renomear atributo de sistema); (3) **2 correções sistêmicas**: o card da tool passa a refletir o
> **resultado**, e gravar um atributo dispara **refresh ao vivo** do painel. **Fora de escopo:** opção B
> (migrar as colunas escalares para o modelo unificado via `source_column`) — fica documentada como evolução.

---

## 0. Estado atual VERIFICADO (2026-06-22, branch `developer`)

### Sistema de atributos personalizados (plano 05)
- Tabela [`custom_attribute_definitions`](../db/tables.py#L176): `attribute_key` (snake, IDENTIDADE),
  `display_name`, `type` (`text|number|date|list|checkbox|link`), `applies_to` (`contact|conversation`),
  `options` (JSON, p/ `list`), `required`, `description`, `regex_pattern`, `regex_cue`, `position`,
  `filterable`, `created_by`, `created_at`, `deleted_at` (soft-delete). `UniqueConstraint(attribute_key, applies_to)`.
  **Não há** coluna `is_system`.
- Valores: `contacts.custom_attributes` JSON (`tables.py:81`) e `conversations.custom_attributes` JSON
  (`tables.py:385`). Escrita read-modify-write via `custom_attribute_repo.set_values`.
- Repo: `db/repositories/custom_attribute_repo.py` (`get_definitions_map(scope)`, `set_values(table, id, {k:v})`,
  CRUD de definições). Validação em `custom_attribute_validate.py` (`validate_value(definition, value)`).
- UI admin: [`CustomAttributesManager.js`](../web/static/js/components/CustomAttributesManager.js) (cria/edita
  definições) + rotas `/api/custom-attributes` (GET por scope, POST/PUT/DELETE).
- Render no contato: [`ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js) carrega
  `getCustomAttributes('contact')` (`:60`) e mostra cada def via [`CustomAttributeField.js`](../web/static/js/components/contacts/CustomAttributeField.js).
  **Itera as DEFINIÇÕES** — um valor em `custom_attributes` sem definição correspondente **não aparece**.

### Campos escalares atuais
- `name/email/profession/company/address` são **colunas reais** de `contacts` (`contact_repo` `_DEFAULTS ~:69`).
- `ContactMemory.update_info(**kwargs)` ([`memory.py:377-390`](../agent/memory.py#L377)) só aceita
  `name/email/profession/company/address` + `observation`; **qualquer outro kwarg (ex.: `cpf`) é silenciosamente
  descartado**. `set_info_fields` (`:392`) idem (lista `allowed`).

### As tools e o card
- `set_custom_attribute` ([`agent/tools/set_custom_attribute.py`](../agent/tools/set_custom_attribute.py)):
  valida contra a definição; se a `key` não existe no escopo, tenta o **outro** escopo (fallback "perdoador",
  `:72-78`); se não existe em nenhum, devolve erro ao LLM. Sucesso → `ca_repo.set_values(...)`, retorna `None`.
- `save_contact_info` (`agent/tools/save_contact_info.py`) → `ctx.contact.update_info(**args)`; **sem campo
  `cpf`**.
- **Card** = [`_broadcast_tool_calls`](../server/routes/webhook.py#L311) monta o texto **só** com
  `tc["tool"]` + `tc["args"]` (`:320-323`) e grava `contact.add_message("tool_call", ...)` **incondicionalmente**
  — não consulta o resultado/erro. O resultado da tool nem é guardado: `agno_engine` faz
  `executed.append({"tool": name, "args": args})` (`:171`/`:215`) e **descarta** o `feedback`.
- **Refresh ao vivo:** `_broadcast_tool_calls` só emite `contact_info_updated` quando há `contact_info`
  (`:336`), e `contact_info` só é populado pelo caminho do `save_contact_info` (`handler.py:1043/1181`).
  **`set_custom_attribute` não dispara refresh** — o painel aberto só atualiza ao reabrir.

---

## 1. Decisões de design (travadas)

1. **Opção A (incremental).** Adicionar `is_system` (Integer, default 0) a `custom_attribute_definitions`.
   Manter as colunas escalares. Novos campos padrão (CPF e futuros) vivem em `contacts.custom_attributes` JSON,
   renderizados no mesmo painel.
2. **Seed de sistema idempotente no boot.** Começar com **`cpf`** (escopo `contact`, `type=text`,
   `display_name="CPF"`, `is_system=1`, `required=0`, **sem `regex_pattern` estrito** — ver §5). `ensure`-style
   (nunca sobrescreve edições do usuário; respeita soft-delete).
3. **Proteção:** atributo `is_system` **não pode** ser apagado nem ter a `attribute_key`/scope renomeada pela UI
   (pode editar `display_name`/`description`/`required`/`position`). Backend recusa `DELETE`/rename de
   `is_system` com 400.
4. **A IA escreve atributo de sistema** normalmente (já que `cpf` passa a ser uma definição válida no escopo
   `contact`, `set_custom_attribute(key="cpf", scope="contact")` resolve e grava no contato — fim do bug).
5. **Card reflete o resultado.** Guardar o resultado/erro da tool no `executed` e o card renderiza estado de
   **sucesso/erro/onde gravou**, em vez de sempre "feito".
6. **Refresh ao vivo** após escrita de atributo (contato/conversa) por tool — emitir WS para o painel aberto
   recarregar (sem reabrir).

---

## 2. Backend — schema + seed
- **Migration Alembic:** `ALTER TABLE custom_attribute_definitions ADD COLUMN is_system INTEGER NOT NULL DEFAULT 0`.
- **Seed:** módulo `seed_system_attributes(settings)` chamado no boot (junto do bootstrap), `ensure`-idempotente:
  `cpf` (contato). Estrutura extensível (lista de dicts) para adicionar outros padrão depois.
- **CRUD guard:** nas rotas `PUT`/`DELETE /api/custom-attributes/{id}`, recusar alteração de `attribute_key`/
  `applies_to` e `DELETE` quando `is_system=1` (mantém `display_name`/`description` editáveis).
- `get_definitions_map`/listagens incluem `is_system` no payload para a UI badge-ar e proteger.

## 3. Backend — card reflete resultado + refresh
- **Guardar resultado:** em `agno_engine` (`:171`/`:215`) incluir `"result": feedback` (e, no
  `set_custom_attribute`, retornar info estruturada de sucesso, ex.: `"OK: cpf gravado no contato"`, em vez de
  `None`, OU um marcador de erro). Manter compat: `feedback` `None`/erro-string já existe.
- **Card:** `_broadcast_tool_calls` (`webhook.py:320`) renderiza ⚠️/erro quando `tc["result"]` indica falha, e
  pode anexar "onde gravou" quando o fallback de escopo disparou. Não muda o role `tool_call`.
- **Refresh:** após `add_message("tool_call", ...)`, se a tool foi `set_custom_attribute` (ou qualquer escrita
  de atributo), emitir `contact_info_updated` (contato) **ou** `conversation_updated` (conversa) com o JSON novo,
  para `ContactInfoPanel`/`ConversationInfoPanel` recarregarem ao vivo. (Os painéis já escutam esses eventos.)

## 4. Frontend
- `CustomAttributesManager.js`: badge "Sistema" nos `is_system`, esconder/desabilitar Excluir e o campo de
  `attribute_key`/scope (só leitura).
- `ContactInfoPanel.js`/`CustomAttributeField.js`: nenhuma mudança estrutural — `cpf` aparece por ser uma
  definição `contact` ativa. (Opcional: ordenar `is_system` no topo via `position`.)
- Garantir que o painel recarrega ao receber o WS de refresh (§3).

---

## 5. Testes
- **Seed:** boot cria a definição `cpf` (contact, `is_system=1`); 2º boot não duplica (idempotente).
- **Fim do bug:** `set_custom_attribute(key="cpf", value="123…", scope="contact")` → `select`:
  `contacts.custom_attributes->>'cpf'` preenchido; aparece no `GET` do contato.
- **Proteção:** `DELETE /api/custom-attributes/{id}` de um `is_system` → 400; `PUT` mudando `attribute_key` → 400.
- **Card com erro:** chamar `set_custom_attribute` com key inexistente → o card sinaliza erro (não "feito").
- **Refresh:** escrita de atributo emite `contact_info_updated`/`conversation_updated`.

---

## 6. Checklist
- [ ] Migration `is_system`.
- [ ] `seed_system_attributes` (boot, idempotente, `cpf` contato).
- [ ] Guard de CRUD para `is_system` (no delete/rename).
- [ ] `agno_engine` guarda `result`; card reflete sucesso/erro/onde gravou.
- [ ] Refresh ao vivo (`contact_info_updated`/`conversation_updated`) após escrita de atributo.
- [ ] UI: badge "Sistema" + proteção em `CustomAttributesManager`.
- [ ] Testes 5.x; modo escuro.

---

## 7. Riscos e evolução (fora de escopo)
- **Regex estrito no CPF** rejeitaria valor válido mal-formatado e voltaria a "salvar" sem salvar (erro ao LLM).
  Por isso o seed entra **sem** `regex_pattern` (ou com um permissivo). Validação de CPF real = melhoria futura.
- **Fallback de escopo "perdoador"** (`set_custom_attribute:72-78`) ainda pode mandar um atributo pro escopo
  errado se a key existir nos dois. Com `cpf` só em `contact`, o caso some; manter o fallback mas **reportar o
  escopo no card** (§3) evita confusão.
- **Opção B (unificação total):** mapear as colunas escalares (name/email/…) a definições de sistema via novo
  `source_column` e renderizar tudo numa UI única. Maior migração; preservar índice de busca por `name`. Fica
  como plano futuro — esta entrega é aditiva e não quebra nada.
