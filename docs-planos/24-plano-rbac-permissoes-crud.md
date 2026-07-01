# Plano 24 — RBAC: maximizar permissões (CRUD) na tela de Cargos

> **Status:** ✅ IMPLEMENTADO (2026-07-01). Fases 0–5 completas — catálogo + migration
> `0032_more_permissions`, backend enforce (gaps + reatribuições), split limpo do plugin
> `atendimentos`, hides de frontend (P48) e testes (966 verdes em SQLite; migration validada
> em Postgres). Histórico do plano abaixo preservado para referência.
> **Revisado/reajustado em 2026-07-01** (auditoria multi-agente de re-validação contra o código
> atual — ver §0). O plano segue válido; os ajustes de drift foram incorporados.
> **Origem:** maximizar as permissões da tela `/users/roles/*` (ex.: esconder o ícone
> "Informações do contato", "Fechar atendimento" vindo do plugin, excluir/editar contatos…).
> **Método:** auditoria multi-agente de **todas** as rotas backend (`server/routes/*`) e
> ações de UI (`web/static/js/components/*`) — 144 candidatos triados + verificação manual.

## 0. Reajustes de 2026-07-01 (drift desde a escrita) — LER PRIMEIRO

Re-validação contra o código atual confirmou que a estrutura e as decisões do plano continuam
corretas, mas houve drift. **Antes de implementar, aplique estes ajustes** (já refletidos nas
seções abaixo):

1. **Head de migration mudou: `0027` → `0031`.** Surgiram 4 migrations depois do plano
   (`0028_default_contact_attrs`, `0029_cpf_not_system`, `0030_ai_agent_inline_prompt`,
   `0031_ai_agent_prompt_history`). O nome `0028` **já está tomado**. A nova migration é
   **`20260701_0032_more_permissions.py`** com `down_revision="0031_ai_agent_prompt_history"`.
2. **Plugin `atendimentos` já tem 3 chaves, não 2.** Foi adicionada `manage_team_views` no merge
   de Kanban views. O plugin foi **refatorado**: `panel.js` **não existe mais** — as affordances
   de fechar/reabrir/atribuir viraram **drag no Kanban** em `atendimentos_tab.js`. Ver §4.3 reescrita.
3. **Plugin `atendimentos` agora é git-tracked em `assets/plugin_examples/atendimentos/`** (idêntico
   à cópia `storages/plugins/atendimentos/`). **Edite a cópia tracked** — senão não versiona.
4. **Superfícies novas (Kanban views CRUD + `ConversationLabelEditor.js`)** — ver §4.4.
5. **Números de linha derraparam** em vários arquivos. As refs `arquivo:linha` deste plano são
   **aproximadas** — localize por nome de função/rota (grep), não pela linha literal.

## Decisões do usuário (2026-06-29)

1. ✅ **Separar** `conversation.delete` de `conversation.resolve` (atendente perde o hard-delete).
2. ✅ **Promover** `conversation_label.manage` (CRUD de etiquetas de conversa hoje 100% ungated).
3. ✅ **Plugin `atendimentos` = split LIMPO (opção b).** Nada em produção ⇒ **sem
   scaffolding, sem fallback, sem chaves-OR**. As rotas vão direto para as chaves finais.
4. ✅ **Maximizar:** incluir a cauda-longa de granularidade como chaves firmes (não opcionais).

> **Princípio geral (nada em produção):** como confirmado na memória do projeto, o WhatsBot
> **não está distribuído** — a refatoração pode ser agressiva. Logo, **o plano vai direto ao
> estado-alvo final**: criamos todo o catálogo + migration **primeiro** e, a partir daí, cada
> gate já usa a chave definitiva. Não existe fase de "stopgap com chave temporária".

---

## 1. Resumo executivo

O RBAC (plano 03) já funciona, mas a cobertura é incompleta. A auditoria revelou 3 classes:

1. **Gaps de segurança no backend** — rotas mutantes **sem gate nenhum**. _Confirmado por
   inspeção:_ `tags.py`, `sandbox.py` e o CRUD global de `conversation_labels.py` têm **zero**
   `permission_denied`. Hoje qualquer atendente apaga a taxonomia inteira ou gasta crédito Techify.
2. **Falta de "esconder" no frontend (P48 — hide, don't disable)** — a **maioria**: a ação já
   é gated no backend, mas a UI nunca esconde o controle. Concentrado em componentes que hoje
   **nem recebem o `currentUser`**.
3. **Granularidade ausente** — ações destrutivas/sensíveis que reusam uma chave coarse
   (excluir contato = `contact.write`; ver custos = `billing.manage`; migrar DB = `settings.manage`).

| Categoria | Qtde | Itens |
|---|---|---|
| Novas chaves **core** | **10** | `contact.delete`, `conversation.delete`, `tag.manage`, `conversation_label.manage`, `sandbox.use`, `usage.read`, `custom_attribute.manage`, `execution.read`, `execution.delete`, `database.manage` |
| Gaps backend que **reusam** chave existente | ~7 | tag-em-contato, archive, webhook-payloads, save-api-key, plugin reads, ai reads |
| Superfícies de **frontend hide** | ~17 | ícone info-contato, Composer, ContextMenu, AssigneePicker, painéis, GearMenu… |
| Novas chaves do **plugin** `atendimentos` | 4 | `resolve`, `assign`, `config`, `delete` (split limpo de `edit`) |

---

## 2. Como o RBAC funciona hoje (mapa)

| Camada | Arquivo | Papel |
|---|---|---|
| Catálogo core | [domain/permission_catalog.py](../domain/permission_catalog.py) | `PERMISSION_CATALOG` = 18 tuplas `(key, label)` |
| Matriz de defaults | [server/permissions.py](../server/permissions.py) | `ROLE_DEFAULTS` (gestor/atendente); `admin` = `'*'` |
| Decisão | [server/authz.py](../server/authz.py) | `check`/`acheck`/`permission_denied` + seam ABAC `filter.authz.decision` |
| Dependency | [server/deps.py](../server/deps.py) | `require_permission("x")` → `acheck` |
| Data | [db/repositories/rbac_repo.py](../db/repositories/rbac_repo.py) | `list_catalog`, `user_permissions`, `_insert_role_permissions`, `upsert_plugin_permission` |
| API catálogo/perms | [users.py](../server/routes/users.py) / [roles.py](../server/routes/roles.py) / [auth.py](../server/routes/auth.py) | `/api/users`,`/api/roles` (gated `users.manage`), `/api/auth/me` → `user.permissions` |
| Gate de UI | [web/static/js/utils/permissions.js](../web/static/js/utils/permissions.js) | `hasPermission(user, key)` — **P48: hide, don't disable** |
| Picker | [PermissionPicker.js](../web/static/js/components/PermissionPicker.js) | agrupa pelo **prefixo antes do 1º `.`** (grupo novo = automático) |
| Plugin RBAC | [plugins/rbac.py](../plugins/rbac.py) + [plugins/context.py](../plugins/context.py) | `plugin.<id>.<key>`; `plugin_permission("key")`; tela via `screens[].requires` |

### ⚠️ Gotcha que torna a migration OBRIGATÓRIA

O `PERMISSION_CATALOG` estático é lido **só para exibir**. O motor de grant/check junta na
**tabela** `permissions`. **Não há seed em runtime** — os únicos escritores são migrations
Alembic e `upsert_plugin_permission` (plugins). Se você adicionar a tupla **sem** migration:
a chave aparece no picker e é aceita pelo validador, mas **nunca persiste o grant**
(`_insert_role_permissions` resolve key→id via `permissions.c.key.in_(...)`; sem linha ⇒ id
nulo ⇒ chave descartada em silêncio). `admin` (`'*'`) nunca precisa de grant.

---

## 3. Decisões arquiteturais

- **Core vs Plugin:** "Fechar atendimento" e "configurar campos" são do **plugin** → `plugin.atendimentos.*`.
  Ícone "Informações do contato" e excluir/editar contato são **core**.
- **Naming:** `<grupo>.<verbo>`, minúsculo. O prefixo antes do `.` **é** o cabeçalho do grupo.
  Grupos de 1 item (TAG, SANDBOX, USAGE, DATABASE…) são consistentes com os já existentes
  (BILLING, AUDIT, PLUGINS hoje têm 1 item cada).
- **Nome da chave é identidade:** nunca renomear chave lançada (quebra grants/`usage`).
- **Default-allow legado:** todo gate passa quando não há usuário (`request.state.user is None`) —
  cobre instalação single-password/aberta e o wizard de 1ª execução. **Por design.**

---

## 4. Catálogo proposto

### 4.1 — Novas chaves CORE (10)

| Chave | Label (pt-BR) | Grupo | Default | Enforce backend (chave FINAL) | Hide frontend |
|---|---|---|---|---|---|
| `contact.delete` | Excluir contato (e todas as conversas) | `contact` | gestor | `contacts.py` `delete_contact` (~ln 640; era `contact.write`) | `ContactInfoPanel.js` (~ln 455-466) "Apagar contato" |
| `conversation.delete` | Excluir conversa (apaga histórico) | `conversation` | gestor | `conversations.py` `DELETE /api/conversations/{id}` (~ln 384; era `conversation.resolve`) | `ContextMenu.js` (~ln 260-277) "Apagar conversa" |
| `tag.manage` | Criar/editar/excluir etiquetas (de contato) | `tag` | gestor | `tags.py:28/46/78` create/update/delete (**ungated**) | gerenciador de tags globais |
| `conversation_label.manage` | Criar/editar/excluir etiquetas de conversa | `conversation_label` | gestor | `conversation_labels.py:46/63/82` (**ungated**) | gerenciador de etiquetas de conversa |
| `sandbox.use` | Usar o chat de teste (sandbox) | `sandbox` | gestor | `sandbox.py:127/160/223/281/344` (**todos ungated**) | `GearMenu.js` entrada + rota SPA `/sandbox` |
| `usage.read` | Ver custos e uso da API | `usage` | gestor | `usage.py:77/88/97` (era `billing.manage`) | tela "Custos"/Usage |
| `custom_attribute.manage` | Definir campos personalizados | `custom_attribute` | gestor | `custom_attributes.py:40/81/124/141` (era `settings.manage`) | editor de definição de campos |
| `execution.read` | Ver trilha de execuções | `execution` | gestor | `executions.py:14/32` (era `settings.manage`) | tela de execuções |
| `execution.delete` | Expurgar execuções | `execution` | gestor | `executions.py:43` `DELETE /api/executions` (era `settings.manage`) | botão "Limpar execuções" |
| `database.manage` | Migração e manutenção do banco | `database` | **admin-only** | `admin.py:43/55/141/147` database/migrate/repair (era `settings.manage`) | tela Settings → Banco |

**Notas:**
- `conversation.delete` / `contact.delete` — destrutivos; hoje reusam a chave de _edição_.
  Separar permite "editar sem deletar". Default `gestor` (admin via `'*'`).
- `tag.manage` vs aplicar tag a um contato: a **biblioteca global** (create/update/delete) é
  `tag.manage`; **aplicar/remover tag de um contato** (`set_contact_tags`) é `contact.write`
  (é edição do contato) — ver §4.2.
- `usage.read` split de `billing.manage`: separa **ver custos** de **recarregar saldo**.
  `billing.manage` passa a ser só recarga (`/api/balance` recharge, link Techify).
- `database.manage` = **endurecimento**: migrar/reparar o DB é a operação mais destrutiva.
  Default **admin-only** (nenhum role recebe; só `'*'`). Hoje está sob `settings.manage`,
  então o `gestor` **perde** essa capacidade — intencional. (Flip trivial se quiser dar a gestor.)
- `execution.read`+`execution.delete`: tira execuções de `settings.manage` e dá grupo próprio
  com read/delete separados.

### 4.2 — Gaps que REUSAM chave existente (sem chave nova)

**Backend (adicionar gate com a chave final):**

| Onde | Chave | Observação |
|---|---|---|
| `tags.py:96` `set_contact_tags` (`PUT /contacts/{phone}/tags`) | `contact.write` | Edição do contato (não `tag.manage`). **Ungated hoje.** |
| `conversations.py:414` `POST /{id}/archive` | `conversation.resolve` | **BUG:** mutação gated só por `conversation.read`. Subir ao tier de lifecycle. |
| `channel_webhook.py:351` `GET /channel-webhook-payloads` | `settings.manage` | Gêmeo de `/webhook-payloads`. Vaza corpos/telefones/base64. **Ungated.** |
| `config.py:108` `POST /config/test-key` + `setup.py:61` `GET /setup/key-status` | `settings.manage` | **Salvam a api_key** sem gate (write disfarçada). Igualar ao `PUT /api/config`. |
| `plugins.py:217` `export_plugin` + `:46` `list_plugins` + `:173` `get_plugin_settings` | `plugins.manage` | Vazam fonte/settings/secrets. Manter `:78` `public_manifest` **aberto** (boot do frontend). |
| `ai_engine.py` GETs (50/55/126/141/154/168/173/189/213/218) | `agent.manage` | Vazam system prompt + **código Python** das `ai_tools`. Preferir dependency router-level no prefixo `/api/ai`. |

**Frontend (esconder — P48):**

| Superfície / arquivo | Chave | O que esconder |
|---|---|---|
| `ContactDetail.js:226/232/259-268` + `Contacts.js:269/279-299` | `contact.read` | **Ex. #1:** ícone/painel "Informações do contato" (avatar/nome + `InfoIcon` + render do painel). |
| `ContactInfoPanel.js:240-253/454-460/382-404/406-443` + `ContextMenu.js:98-106` | `contact.write` | Inputs, "Salvar", custom attrs, observações, "Editar Contato". |
| `ContextMenu.js:140-161` + `ContactInfoPanel.js:255-379` | `contact.write` | Editores de tag-em-contato (par do gate `set_contact_tags`). |
| `ContextMenu.js:250-259/129-138/84-97` | `contact.write` | Arquivar/Fixar, **toggle-IA do contato**. |
| `Composer.js:205-324` + `ContactDetail.js:331-335/347-389` | `conversation.reply` | **Ex. usuário:** enviar/imagem/áudio/mic/anexo/privada, react, apagar msg, retry. Esconder **Composer inteiro** (+ banner "somente leitura") e ações de bolha. |
| `ContextMenu.js:107-127/84-97` + `ConversationInfoPanel.js:230-252/225-227` | `conversation.reply` | Marcar lido/não lido, **toggle-IA da conversa** (`onToggleAI(conv.id)` → `/conversations/{id}/ai`), "Salvar atributos", `ConversationLabelEditor`. |
| `ContextMenu.js:230-243` + `attendances/AttendanceList.js:49-52` | `conversation.resolve` | "Marcar resolvida"/"Reabrir" inline. |
| `ContextMenu.js:163-228` + `AssigneePicker.js:91-156` + `AttendanceList.js:53-60` | `conversation.assign` | Submenu Atribuir, dropdown `AssigneePicker` (render incondicional hoje), atribuir inline. |
| `ContactDetail.js:258-268` + `Contacts.js:298-309` | `conversation.read` | Ícone/painel "Informações da conversa". |
| `shell/GearMenu.js` (~ln 127-138) | `billing.manage` | Link externo "Saldo e Recargar" (hoje só condicional a `accountUrl`, sem gate). |
| `Contacts.js:226-227/169-174` | `settings.manage` | Interruptor **global** da IA (`auto_reply`). |
| `Composer.js:279-298` | `quickreply.manage` | Picker de respostas rápidas in-composer (some junto se o Composer for escondido). |
| tela Usage / Execuções / Banco / campos personalizados | `usage.read` / `execution.read` / `database.manage` / `custom_attribute.manage` | Esconder as entradas no `GearMenu` + render das telas pelas chaves novas. |

> **Pré-requisito de TODOS os hides (Fase 0):** componentes que **não recebem `currentUser`**
> hoje — `ContactDetail.js`, `ContextMenu.js`, `ContactInfoPanel.js` (que **nem importa**
> `hasPermission`), `AssigneePicker.js`, `attendances/AttendanceList.js`. Threadar `user` como
> prop de `Contacts.js`/`Attendances.js` (que já chamam `getMe()`). Referência: `ConversationHeaderActions.js:40,53`.

### 4.3 — Plugin `atendimentos` — split LIMPO (opção b)  ⟵ REESCRITO 2026-07-01

> **Editar a cópia git-tracked:** o plugin vive em **`assets/plugin_examples/atendimentos/`**
> (versionado) e é espelhado em `storages/plugins/atendimentos/` (idêntico, runtime). **Todas as
> edições de `plugin.yaml`/`routes.py`/`static/*.js` vão na cópia `assets/plugin_examples/`.**

**Estado ATUAL (não mais 2 chaves):** o plugin já tem **3 chaves** — `view`, `edit` e
`manage_team_views` (esta última adicionada no merge de Kanban views). `edit` ainda cobre tudo
que muda no atendimento. **Ninguém tem `edit`/`manage_team_views` concedido por padrão** (plugin
perms nascem admin-only) ⇒ o split **não quebra ninguém**. Vamos direto às chaves finais
(mantendo as 3 existentes + 4 novas):

```yaml
# assets/plugin_examples/atendimentos/plugin.yaml → rbac.permissions
- { key: view,              label: "Ver atendimentos" }                          # existente
- { key: edit,              label: "Editar campos/observações do atendimento" }  # existente (escopo reduzido)
- { key: manage_team_views, label: "Criar/editar visualizações de EQUIPE no Kanban" } # existente (NÃO mexer)
- { key: resolve,           label: "Fechar/reabrir atendimento e resolver conversa" }  # NOVO
- { key: assign,            label: "Atribuir/reatribuir atendimentos" }          # NOVO
- { key: config,            label: "Configurar campos e avaliação do plugin" }   # NOVO
- { key: delete,            label: "Excluir atendimentos" }                      # NOVO (reservado: sem rota ainda)
```

> ⚠️ **`panel.js` não existe mais** — o plugin foi refatorado para Kanban. As affordances de
> fechar/reabrir/atribuir são **drag entre colunas** em `static/atendimentos_tab.js`, sem hide no
> frontend (o gate é 100% backend; o drop falha se o usuário não tiver a permissão). Números de
> linha abaixo são aproximados — localize por nome de rota/função.

| Chave | Rotas (`assets/plugin_examples/atendimentos/routes.py`) — chave FINAL | Frontend |
|---|---|---|
| `view` | list/get atendimento, `/roles`, `/kanban-views` GET, `/conversas/{id}/anchor`, `/field-defs` GET, `/protocol-config` GET (mantêm) | render da tab/painel |
| `edit` | `/contacts/{id}/atendimento/ensure`, `/atendimentos/{id}/fields` PUT, `/atendimentos/{id}/set-attr` (mantêm `edit`) | Salvar/Iniciar; drag "por atributo" |
| `resolve` | `/atendimentos/{id}/close` (**Fechar**), `/atendimentos/{id}/reopen`, `/conversas/{id}/resolve` (eram `edit`) | drag close/reopen no Kanban (`atendimentos_tab.js` ~ln 193-194); popup `resolve_form.js` via `extends.js` |
| `assign` | `/atendimentos/{id}/assign` (era `edit`) | drag "por atendente" (`atendimentos_tab.js` ~ln 117-120) |
| `config` | `/field-defs` PUT, `/protocol-config` PUT (eram `edit`) | screen `atendimentos-config` `requires:` `edit`→`config`; `config.js` (~ln 29) `can('edit')`→`can('config')` |
| `manage_team_views` | `/kanban-views` POST/PUT/DELETE — **já gated** (base `view` + check inline `_gate_view_write`) | `atendimentos_tab.js` `canTeam`/`canEditView` (já usa) — **NÃO mexer** |
| `delete` | _(reservado — sem rota DELETE; declarar p/ aparecer no picker)_ | botão futuro via `can('delete')` |

> **Sem fallback:** as rotas trocam direto `edit`→chave-fina (não "edit OR resolve"). Depois do
> deploy, o admin marca as caixinhas novas nos cargos que devem ter cada poder.
> **Default de grant:** plugin perms **não** têm `ROLE_DEFAULTS` — nascem admin-only. Se quiser
> que `gestor` já receba `resolve`/`assign`/`config`, isso é feito **na UI de Cargos** (ou um
> seed manual), não no catálogo core.

### 4.4 — Superfícies NOVAS (pós-merge Kanban views) — decisões

| Superfície | Onde | Estado atual | Decisão para este plano |
|---|---|---|---|
| **Kanban views CRUD** | `atendimentos/routes.py` `POST/PUT/DELETE /kanban-views` + `PUT /kanban-views/{id}/my-pref` | Gated: base `view` + `_gate_view_write` (view de EQUIPE exige `manage_team_views`; PESSOAL exige ownership OU `manage_team_views`) | **Manter como está.** É a única superfície RBAC já bem-feita. `create_kanban_view` (POST) tem só `view` de base ⇒ qualquer um com leitura cria view **pessoal** — **aceito por design** (view pessoal é inócua; compartilhar vira EQUIPE e cai no `manage_team_views`). Documentado aqui para não "corrigir" por engano. |
| **`ConversationLabelEditor.js`** | `web/static/js/components/ConversationLabelEditor.js` | Self-contained, sem `hasPermission`; expõe criar/editar etiqueta de conversa inline | **É o alvo concreto do hide por `conversation_label.manage`** (§4.1). Threadar `currentUser` + import `hasPermission` (entra na Fase 0). O CRUD **global** (registry) é `conversation_labels.py` POST/PUT/DELETE — ungated hoje, gate em Fase 2. A associação por-conversa (`PUT /conversations/{id}/labels`) **já** é `conversation.reply` — não é alvo. |

---

## 5. Receitas (referência de implementação)

### 5.1 — Adicionar UMA permissão CORE (6 passos)

1. **`domain/permission_catalog.py`** — anexar `(key, "label")`. `ALL_PERMISSION_KEYS` e
   `_valid_permission_keys()` pegam automático (re-export em `server/permissions.py`).
2. **`server/permissions.py`** — adicionar a key ao(s) papel(is) em `ROLE_DEFAULTS`. **Não**
   adicionar `admin`. (Documenta política; **não** semeia o DB — sincronizar à mão com a migration.)
3. **Migration Alembic (OBRIGATÓRIA)** — copiar o padrão idempotente de
   [20260620_0021_template_permissions.py](../db/alembic/versions/20260620_0021_template_permissions.py).
   `down_revision = "0031_ai_agent_prompt_history"` (**head atual confirmado em 2026-07-01** — NÃO
   é mais `0027`). Insert da key se ausente + grant aos roles se ausente. Tabela `permissions` core:
   só `key`+`description` (não setar `plugin_id`/`group_label`). `init_db()` roda
   `alembic upgrade head` no boot ⇒ **restart**.
4. **`PermissionPicker.js`** — geralmente **nada**: agrupa pelo prefixo antes do `.`; prefixo novo
   vira seção nova automática. (Opcional: marcar inerte em `INERT_PERMISSIONS`.)
5. **Enforce backend** — `dependencies=[Depends(require_permission("x"))]` (preferido) ou inline
   `denied = permission_denied(request, "x"); if denied: return denied`.
6. **Hide frontend** — `import { hasPermission }`; `user` via `getMe()`;
   `${hasPermission(user, 'x') ? html\`…\` : null}`.

**Cache:** não há cache de permissão (grants valem na hora). Mas a migration roda só no boot ⇒
**restart**. Usuários `custom_permissions=1` não recebem grant automático — re-marcar na UI.

### 5.2 — Adicionar UMA permissão de PLUGIN (3 toques, zero core)

1. **Declarar** em `plugin.yaml` sob `rbac.permissions` (regex `^[a-z][a-z0-9_.]{0,48}$`).
2. **Registro automático** no load (enabled): `sync_plugin_permissions` upserta `plugin.<id>.<key>`.
   Aparece no picker como "Plugin: <group>". **Restart** para materializar. Disable mantém linhas;
   delete remove por FK cascade.
3. **Enforce:** rota → `dependencies=[plugin_permission("key")]`; tela → `screens[].requires: key`;
   elemento → prop `can(key)` (`PluginScreen.js:61`) ou `api.services.hasPermission(...)` em slots custom.

---

## 6. Plano faseado (estado-alvo limpo)

### Fase 0 — Plumbing de frontend (pré-requisito de todos os hides)
- **Threadar `currentUser` de fato** (não recebem identidade hoje): `ContactDetail.js`,
  `ContactInfoPanel.js`, `Composer.js`, `ConversationLabelEditor.js`. `ContextMenu.js` já recebe
  `{currentUserId}` mas precisa do **user completo** (com `permissions[]`) para `hasPermission`.
- **Já carregam identidade — falta só o check `hasPermission`** (menos trabalho que o previsto):
  `ConversationInfoPanel.js` (já importa `hasPermission` e gateia resolve/reopen),
  `AssigneePicker.js` (faz `getMe()` interno) e `attendances/AttendanceList.js` (recebe
  `{currentUserId}`). Para esses, basta adicionar os checks nas affordances, não re-threadar user.
- `ContactInfoPanel.js`: adicionar `import { hasPermission }`. Sem mudança de backend.
- **Referência de padrão correto:** `ConversationHeaderActions.js` e `shell/GearMenu.js` (usam
  `getMe()` + `hasPermission` e já gateiam suas ações).

### Fase 1 — Catálogo + migration (TODAS as 10 chaves de uma vez) ⟵ vem antes do enforce
- `domain/permission_catalog.py`: 10 tuplas novas.
- `server/permissions.py` `ROLE_DEFAULTS`: grant das 9 a `gestor` (`database.manage` fica
  **admin-only** — não entra em nenhum role).
- Migration `20260701_0032_more_permissions.py` (`down_revision="0031_ai_agent_prompt_history"` —
  head atual; `0028` já está tomado por `default_contact_attrs`) no padrão idempotente do `0021`:
  insert das 10 + grant ao `gestor` das 9.
- **Restart** do worker (migration roda no boot). Após isso, **todas as chaves finais existem**.

### Fase 2 — Backend enforce (chaves FINAIS, sem stopgap)
- **Gaps de segurança:** `tags.py` create/update/delete → `tag.manage`; `conversation_labels.py:46/63/82`
  → `conversation_label.manage`; `sandbox.py` (todos) → `sandbox.use`; `channel_webhook.py:351` →
  `settings.manage`; `config.py:108`+`setup.py:61` → `settings.manage`; `plugins.py:217/46/173` →
  `plugins.manage`; `ai_engine.py` GETs → `agent.manage`.
- **Reatribuições de chave:** `tags.py:96` `set_contact_tags` → `contact.write`; `conversations.py:414`
  archive `conversation.read`→`conversation.resolve`; `contacts.py:615` delete → `contact.delete`;
  `conversations.py:391` DELETE → `conversation.delete`; `usage.py` → `usage.read`; `executions.py`
  read→`execution.read`, delete→`execution.delete`; `custom_attributes.py` → `custom_attribute.manage`;
  `admin.py` → `database.manage`.
- Rodar `tests/test_endpoints.py` (atualizar expectativas — ver Fase 5).

### Fase 3 — Frontend hide (P48, chaves finais)
- `ContactInfoPanel`: esconder Salvar/inputs/observações/custom-attrs por `contact.write`;
  "Apagar contato" por `contact.delete`.
- `ContactDetail`: gatilho do painel de contato por `contact.read`; **Composer inteiro** (+ banner)
  e ações de bolha por `conversation.reply`; picker `/atalho` por `quickreply.manage`.
- `ContextMenu`: Editar/Tags/Arquivar/Fixar/toggle-IA-contato por `contact.write`; resolver/reabrir
  por `conversation.resolve`; apagar conversa por `conversation.delete`; Atribuir por
  `conversation.assign`; marcar lido/**toggle-IA-conversa** por `conversation.reply`.
- `AssigneePicker` + `ConversationInfoPanel`: assign por `conversation.assign`; label editor +
  "Salvar atributos" por `conversation.reply`; info-conversa por `conversation.read`.
- `attendances/AttendanceList.js`: Fechar/Reabrir (`conversation.resolve`), Atribuir (`conversation.assign`).
- `GearMenu`/`Contacts.js`: link Saldo (`billing.manage`); toggle global `auto_reply` (`settings.manage`);
  entradas Usage (`usage.read`), Execuções (`execution.read`), Banco (`database.manage`),
  campos personalizados (`custom_attribute.manage`); botão "Limpar execuções" (`execution.delete`);
  entrada Sandbox + rota SPA (`sandbox.use`).

### Fase 4 — Plugin `atendimentos` (split limpo)
- **Editar a cópia git-tracked** `assets/plugin_examples/atendimentos/` (não só `storages/`).
- `plugin.yaml`: adicionar `resolve`/`assign`/`config`/`delete` (manter `view`/`edit`/**`manage_team_views`**).
- `routes.py`: trocar gates direto — close/reopen/`conversas/{id}/resolve` → `resolve`; assign →
  `assign`; field-defs/protocol PUT → `config`. `ensure`/`fields`/`set-attr` mantêm `edit`.
  **NÃO tocar** nas rotas `/kanban-views*` (já gated por `manage_team_views` + `_gate_view_write`).
- Frontend: `atendimentos_tab.js` gatear drag (close/reopen→`resolve`, assign→`assign`) — o gate
  hoje é só backend; opcionalmente esconder/desabilitar o drop sem permissão; `config.js`
  (~ln 29) `can('edit')`→`can('config')`; screen `atendimentos-config` `requires: edit`→`config`.
  (`panel.js` não existe mais — ver §4.3.)
- Toggle/restart do plugin para materializar as rows. (Re-marcar as chaves nos cargos via UI.)

### Fase 5 — Testes + validação
- `tests/test_endpoints.py`: ajustar para os novos gates (ex.: usuário `atendente` agora recebe 403
  em delete-contact / sandbox / usage / executions; admin e legacy continuam passando). Adicionar
  checagens para `tag.manage`, `conversation_label.manage`, etc.
- Validação visual em **modo escuro** de qualquer banner/estado novo (ex.: Composer "somente leitura").
- Rodar contra SQLite **e** Postgres (memória: PG dev target validado).

---

## 7. Pontos resolvidos (eram perguntas em aberto)

1. ✅ **`conversation.delete`:** SEPARAR (decisão do usuário).
2. ✅ **`conversation_label.manage`:** chave própria (gap de segurança real, CRUD ungated).
3. ✅ **Plugin `atendimentos`:** split LIMPO (opção b) — sem fallback, rotas direto nas chaves finais.
4. ✅ **toggle-IA (divergência):** resolvido **pelo call-site**, sem unificar chaves. O `ContextMenu`
   chama `onToggleAI(conv.id)` → `POST /conversations/{id}/ai` (`conversation.reply`) ⇒ esconder essa
   affordance por `conversation.reply`. O toggle por-**contato** (outra affordance) usa `contact.write`.
   Os dois endpoints permanecem como estão; cada botão esconde pela chave que ele realmente chama.
5. ✅ **pin/archive:** esconder por `contact.write` (paridade com o backend `contacts.py:636/662`).
6. ✅ **Cauda longa:** incluída como firme (`usage.read`, `custom_attribute.manage`, `execution.read`,
   `execution.delete`, `database.manage`) — você pediu o máximo. `channel.delete_purge` ficou **de
   fora** (gating por query-param `?purge=true` no mesmo endpoint é estranho; `channel.manage` já é
   admin/gestor-only).
7. ✅ **`tag.manage` vs `set_contact_tags`:** `tag.manage` = biblioteca global; `contact.write` =
   aplicar/remover tag num contato.

**Único ajuste de política para você confirmar:** `database.manage` proposto como **admin-only**
(o `gestor` perde migrar/reparar o DB, que hoje cai em `settings.manage`). É o endurecimento
recomendado. Se preferir manter no `gestor`, é só adicioná-lo ao `ROLE_DEFAULTS["gestor"]` e ao
grant da migration.

---

## 8. Apêndice — arquivos-chave

- Catálogo/policy: `domain/permission_catalog.py`, `server/permissions.py`
- Enforce: `server/authz.py`, `server/deps.py`
- Data: `db/repositories/rbac_repo.py`
- API: `server/routes/{users,roles,auth}.py`
- Migration template: `db/alembic/versions/20260620_0021_template_permissions.py` (**head atual: `0031_ai_agent_prompt_history`** — a nova migration é `0032_more_permissions`)
- Frontend: `web/static/js/utils/permissions.js`, `components/{PermissionPicker,RolesManager,UsersManager}.js`, `services/api.js`
- Plugin RBAC: `plugins/{manifest,rbac,context}.py`, `components/{PluginScreen,shell/GearMenu}.js`
- Plugin atendimentos: `storages/plugins/atendimentos/{plugin.yaml,routes.py,static/*.js}`
