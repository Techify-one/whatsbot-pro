# Plano — RBAC para Plugins

> **Status:** proposta (não implementado). Documento de trabalho — arquivar em
> `docs-planos/_archive/` quando a implementação terminar.
>
> **Objetivo:** permitir que um plugin declare suas próprias permissões de
> usuário (ver/editar/excluir cada funcionalidade) de um jeito que (1) apareça
> automaticamente na tela de Usuários, agrupado pelo plugin, (2) seja enforçável
> no backend, e (3) deixe um encaixe pronto para ABAC futuro sem reescrever os
> call sites. Tudo sem o plugin tocar no core.

---

## 1. Decisões de design (fechadas com o usuário)

| Tema | Decisão |
|---|---|
| **Granularidade** | **Chaves livres** por plugin. O schema aceita qualquer chave; a convenção forte (empurrada pelo `/new-plugin` e pela doc) é `view` / `edit` / `delete`. |
| **ABAC** | Só **RBAC** no v1. Reusa o scoping de inbox que já existe. Deixar um **encaixe** (`filter.authz.decision`) para regras por atributo (ex: horário) depois, sem quebrar call sites. |
| **Default (RBAC off / usuário legado)** | **Seguir o core**: sem usuário ou `rbac_enforce` off ⇒ libera tudo. Não quebra single-password. |
| **UI** | Permissões de plugin entram no **`PermissionPicker` existente**, como um grupo a mais por plugin. Zero UI nova. |

---

## 2. Modelo

### 2.1 Formato da chave

`plugin.<id>.<key>` — o prefixo `plugin.` é **reservado** e evita colisão com os
domínios do core (`conversation.*`, `contact.*`, …). O `<key>` é a chave livre
declarada pelo plugin (ex: `view`). Resultado: `plugin.lembretes.view`.

### 2.2 Fonte de verdade

A tabela `permissions` **já é** a fonte de verdade (seedada na migração 0012;
`role_permissions` e `user_permissions` têm FK pra `permissions.id`). Logo, perms
de plugin **precisam virar linhas** nessa tabela para serem atribuíveis. O
catálogo estático em `server/permissions.py` continua sendo o seed/validação do
**core**; o catálogo efetivo passa a ser **core + linhas de plugin**.

---

## 3. Mudanças por camada

### 3.1 Banco — migração `0024_plugin_permissions`

Adicionar à tabela `permissions` duas colunas nullable (não-quebrador; linhas do
core ficam com `NULL`):

```python
op.add_column("permissions", sa.Column("plugin_id", sa.Text(), nullable=True))
op.add_column("permissions", sa.Column("group_label", sa.Text(), nullable=True))
op.create_index("idx_permissions_plugin", "permissions", ["plugin_id"])
```

- `plugin_id` — `NULL` para perms do core; `<id>` para perms de plugin.
- `group_label` — rótulo do grupo no `PermissionPicker` (default = `name` do plugin).

Refletir as colunas em `db/tables.py` (objeto `permissions`).

### 3.2 Manifesto — bloco `rbac:` (novo, separado do `permissions:` de capability)

`plugins/manifest.py`:

- Adicionar campo `rbac: dict` ao `PluginManifest` (default `{}`).
- Parsear e validar em `_build_manifest`: `rbac.group` (str, opcional) e
  `rbac.permissions` = lista de `{key, label}`. Validar `key` com regex
  `^[a-z][a-z0-9_.]{0,48}$` (permite `view`, `orders.export`, etc.).
- Incluir `rbac` em `to_public_dict()`.

Exemplo de `plugin.yaml`:

```yaml
rbac:
  group: "Lembretes"          # opcional; default = name do plugin
  permissions:
    - { key: view,   label: "Ver lembretes" }
    - { key: edit,   label: "Criar/editar lembretes" }
    - { key: delete, label: "Excluir lembretes" }
```

> **Nota:** o campo legado `permissions:` (capabilities `llm.tool`, `db.write`…)
> continua existindo e independente. `rbac:` é exclusivamente sobre permissões de
> usuário.

### 3.3 Registro no load

`plugins/loader.py` (ou função nova `plugins/rbac.py::sync_plugin_permissions`):

- No `_process_one`, **para plugin ativado**: upsert de cada
  `plugin.<id>.<key>` em `permissions` (`key`, `description=label`,
  `plugin_id=<id>`, `group_label=group`). Usar `db.upsert.upsert` (dialect-agnóstico).
- **Disable**: manter as linhas (atribuições sobrevivem ao toggle).
- **Delete** (`DELETE /api/plugins/{id}` em `server/routes/plugins.py`): apagar
  `WHERE plugin_id = :id` — `role_permissions`/`user_permissions` caem por FK cascade.

### 3.4 Catálogo dinâmico

- `db/repositories/rbac_repo.py`: nova função `list_catalog()` que retorna
  core (de `PERMISSION_CATALOG`) **+** linhas com `plugin_id IS NOT NULL` da
  tabela `permissions`, cada uma com `{key, description, plugin_id, group_label}`.
- `server/routes/users.py:63` (`/api/roles`): trocar a lista estática
  `PERMISSION_CATALOG` por `rbac_repo.list_catalog()`.
- **Validação**: `_VALID_PERMISSION_KEYS` em `users.py` e `roles.py` hoje vem de
  `ALL_PERMISSION_KEYS` (estático). Passar a unir com as keys de plugin (consultar
  `permissions` table). Senão, criar usuário/role com perm de plugin é rejeitado.

### 3.5 Enforcement backend (com encaixe ABAC)

`server/authz.py`:

- Centralizar a decisão numa função `check(request, permission_key) -> bool`
  (hoje a lógica está espalhada em `has_permission`/`permission_denied`).
- Dentro dela, **após** a checagem RBAC atual, aplicar o filtro novo
  `filter.authz.decision` (no bus de filtros que já existe) passando
  `{user, permission_key, allow}`. O filtro pode rebaixar `allow → deny`.
  **No v1 nenhum avaliador é embarcado** — o seam só existe. Regras por atributo
  (ex: horário) viram um plugin de filtro depois, sem tocar nos call sites.

`plugins/context.py` — helper para o router do plugin:

```python
from plugins.context import plugin_permission

@router.delete("/items/{id}", dependencies=[plugin_permission("delete")])
async def delete_item(id: int): ...
```

- `plugin_permission(key)` devolve uma dependency FastAPI que: lê
  `request.url.path`, extrai o `<id>` de `/api/plugins/<id>/...`, monta
  `plugin.<id>.<key>` e chama `authz.permission_denied` (mesma semântica de 403;
  default-allow quando legado/RBAC off). O plugin não precisa saber o próprio id.

### 3.6 Frontend (zero UI nova)

- **`web/static/js/components/PermissionPicker.js`**: hoje agrupa por domínio
  (split em `.`). Estender: quando a perm tem `plugin_id`, agrupar por
  `group_label` (seção "Plugin: Lembretes") em vez de domínio. O `/api/roles`
  já passa a mandar esse metadata (3.4).
- **`web/static/js/components/PluginScreen.js`**: passar prop
  `can(key)` = `hasPermission(user, 'plugin.' + pluginId + '.' + key)`.
- **`web/static/js/app.js`**: o manifesto da screen ganha campo opcional
  `requires` (ex: `requires: view`). No filtro de screens do GearMenu, esconder a
  screen quando faltar `plugin.<id>.<requires>` (mesmo padrão "hide, don't disable").
  Exige `app.js` ter o `user` em mãos (já tem, via auth).

---

## 4. Faseamento

1. **Declarativo + visível** (3.1–3.4): schema, migração, registro no load, merge
   no catálogo, agrupamento no picker. As perms já aparecem e são atribuíveis —
   **sem enforcement ainda** (espelha como o RBAC do core nasceu). Entregável
   testável: criar um role com `plugin.lembretes.view` e ver no picker.
2. **Enforcement** (3.5 + 3.6 screen-gating): dependency `plugin_permission()` nas
   rotas + esconder screen sem `view`.
3. **Encaixe ABAC + DX** (filtro `filter.authz.decision` + docs + `/new-plugin`).

---

## 5. Compatibilidade

- Plugins **sem** bloco `rbac:` → nenhuma perm registrada → comportam-se como hoje
  (acessíveis). **Não-quebrador.**
- Instalações legado / RBAC off → default-allow preservado.
- Colunas novas em `permissions` são nullable → linhas do core intactas.
- Migração SQLite→Postgres: o endpoint admin reflete a tabela `permissions` com as
  colunas novas automaticamente.

---

## 6. Testes (em `tests/test_endpoints.py`)

- `/api/roles` retorna perms de plugin no catálogo, com `plugin_id`/`group_label`.
- Criar role com perm de plugin passa na validação; perm inexistente é rejeitada.
- Rota de plugin com `plugin_permission("delete")` → 403 sem a perm, 200 com.
- Default-allow: sem usuário (legado) a rota responde 200 mesmo sem perm.
- Delete do plugin remove as linhas de `permissions` (e atribuições por cascade).

---

## 7. Entregáveis de documentação (parte da implementação)

> A doc durável NÃO vai num arquivo solto — vai nos lares que já existem. Os
> trechos abaixo são inseridos **junto com o código** (quando a feature existir),
> não antes (o CLAUDE.md descreve o que existe hoje).

### 7.1 Inserir em `CLAUDE.md` → seção "Sistema de plugins" (referência)

Subseção nova **"RBAC de plugins"**:

> Um plugin declara permissões de usuário no bloco `rbac:` do `plugin.yaml`
> (distinto do `permissions:` de capability). Cada permissão vira a chave
> `plugin.<id>.<key>` registrada na tabela `permissions` no load (upsert),
> aparecendo automaticamente no `PermissionPicker` agrupada pelo plugin. Enforce
> nas rotas do plugin com a dependency `plugin_permission("<key>")` (infere o id
> do path, retorna 403; default-allow quando legado/RBAC off). Esconda a screen
> sem permissão com `requires:` no manifesto da screen. ABAC por atributo é um
> ponto de extensão: o filtro `filter.authz.decision` pode rebaixar allow→deny
> (nenhum avaliador embarcado no core). Perms somem por cascade ao deletar o plugin.

E adicionar na tabela de filters:

| Filter | Local | `value` | `None` faz | `ctx.extras` |
|--------|-------|---------|------------|--------------|
| `filter.authz.decision` | `authz.check` após o RBAC | `dict {user, permission_key, allow}` | trata como `allow=False` (nega) | `permission_key` |

### 7.2 Inserir em `.claude/commands/new-plugin.md` (playbook de autoria)

- **Passo 1 (requisitos):** nova pergunta — "Quais funcionalidades têm controle
  de acesso? Para cada uma, quais ações (ver/editar/excluir)?" → gera o bloco `rbac:`.
- **Passo 3 (gerar estrutura):** adicionar ao exemplo de `plugin.yaml` o bloco
  `rbac:` com a convenção `view`/`edit`/`delete`; e ao exemplo de `routes.py` o uso de
  `dependencies=[plugin_permission("...")]`; e ao `static/<id>.js` o uso de `can(key)`.
- **Regras importantes:** "RBAC do plugin é declarativo no manifest; nunca cheque
  permissão na mão — use `plugin_permission()`. Convenção de chaves: `view`/`edit`/`delete`."

---

## 8. Arquivos tocados (resumo)

| Arquivo | Mudança |
|---|---|
| `db/alembic/versions/20260624_0024_plugin_permissions.py` | **novo** — colunas `plugin_id`/`group_label` + índice |
| `db/tables.py` | refletir colunas novas em `permissions` |
| `plugins/manifest.py` | parsear/validar bloco `rbac:`; incluir em `to_public_dict()` |
| `plugins/loader.py` (ou `plugins/rbac.py` novo) | upsert de perms no load |
| `plugins/context.py` | `plugin_permission(key)` dependency |
| `plugins/events.py` | registrar o filtro `filter.authz.decision` (seam) |
| `server/authz.py` | `check()` central + aplicar `filter.authz.decision` |
| `db/repositories/rbac_repo.py` | `list_catalog()` (core + plugin) |
| `server/routes/users.py` | catálogo dinâmico + validação estendida |
| `server/routes/roles.py` | validação estendida |
| `server/routes/plugins.py` | DELETE remove perms `WHERE plugin_id` |
| `web/static/js/components/PermissionPicker.js` | agrupar por `group_label` |
| `web/static/js/components/PluginScreen.js` | prop `can(key)` |
| `web/static/js/app.js` | `requires:` esconde screen |
| `tests/test_endpoints.py` | casos da seção 6 |
| `CLAUDE.md` / `.claude/commands/new-plugin.md` | doc (seção 7) |
