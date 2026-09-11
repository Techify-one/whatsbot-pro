# Plano 153 — Times (Teams): agrupar atendentes, filtrar e transferir conversa por time

> **Status:** PLANEJAMENTO · **Data:** 2026-09-10 · **Escopo:** médio-grande (schema + 2 repos + serviço/eventos + REST + 3 telas/fluxos de frontend + filtro client+server-side)
> **Origem:** pedido direto do usuário — feature "Times" como no Chatwoot: (1) vincular usuários a um time com descrição, configurável na aba Usuários; (2) filtrar conversas por time na sidebar; (3) um usuário pode estar em vários times; (4) "Atribuir time" no menu de contexto da conversa, abaixo de "Atribuir atendente"; (5) tudo no core.
> **Método:** leitura do código real com `arquivo:linha` conferido em `db/tables.py`, `db/repositories/`, `app/services/conversation_service.py`, `server/routes/conversations.py`, `plugins/events.py`, e nos componentes Preact de `web/static/js/components/contacts/`. Nada de memória — cada citação abaixo foi lida nesta sessão.
>
> **O achado que define a forma do plano:** o schema **já reservou** o campo (`atendimentos.team_id`, [db/tables.py:547](../db/tables.py#L547)) desde o plano 01, sem FK, "por robustez de ordem de migration" — a tabela `teams` nunca existiu. E o precedente de "usuário pertence a vários X" **já existe pronto pra copiar**: `inbox_members` ([db/tables.py:524](../db/tables.py#L524) + [inbox_member_repo.py](../db/repositories/inbox_member_repo.py)) é uma N:N usuário↔inbox com as duas direções escritas, testadas em produção. Este plano não inventa um padrão novo — ele instancia um padrão que já existe duas vezes no repo (RBAC e inboxes) pela terceira vez, e liga o fio que faltava no dado que já estava lá.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela **ANTES** de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** | Time e atendente individual são **INDEPENDENTES**. Atribuir a um time NÃO limpa `assignee_user_id`, e vice-versa. | `assign_team()` é uma função NOVA e PARALELA a `assign()`/`assign_unified()`, nunca passa por `_transfer()` (que é o cotovelo dos três campos assignee/agent/ai). Escreve só `team_id`. |
| **D2** | Sem efeito de RBAC/visibilidade nesta fase — pertencer a um time não amplia nem restringe quais conversas um usuário vê. | Nenhuma mudança em `inbox_members`, `visible_inboxes`, `can_access_inbox` ou nos repos de listagem. Time é campo + filtro, ponto. |
| **D3** | Eventos novos no bus: `conversation.team_assigned` / `conversation.team_unassigned`, simétricos a `conversation.assigned`/`.unassigned`. | Bump **MINOR** `WHATSBOT_API_VERSION` 1.8.0 → **1.9.0** em [plugins/semver.py:90](../plugins/semver.py#L90), entrada em [KNOWN_EVENTS](../plugins/events.py#L100), changelog, regenerar golden. |
| **D4** | FK real: `atendimentos.team_id → teams.id ON DELETE SET NULL` (hoje é `Integer` puro, sem FK — [db/tables.py:547](../db/tables.py#L547)). | A migration cria `teams` + `team_members` **e** adiciona a FK na coluna existente. Deletar um time nunca quebra uma conversa — ela só perde a etiqueta. |
| **D5** ✅ *(decisão de engenharia deste plano, não pedida explicitamente — ver §7 P1)* | A aba "Times" reaproveita a permissão **`users.manage`** — a MESMA que já gateia "Usuários" e "Grupos de permissão" (mesma tela, mesmo menu). Nenhuma permissão nova. | Sem entrada em `domain/permission_catalog.py`, sem migration de permissão, sem mudança em `ROLE_DEFAULTS`. `server/routes/teams.py` usa `permission_denied(request, "users.manage")`, cópia de [roles.py:42](../server/routes/roles.py#L42). |
| **D6** ✅ | "Atribuir time" numa conversa reaproveita **`conversation.assign`** — a MESMA permissão de "Atribuir atendente" ([ContextMenu.js:67](../web/static/js/components/contacts/ContextMenu.js#L67)). | Mesmo público (gestor+admin, não atendente — confirmado em [server/permissions.py:24-46](../server/permissions.py#L24-L46): nem `gestor` nem `atendente` têm hoje um privilégio de atribuição diferente para "time" vs "pessoa"). |
| **D7** ✅ | O WS event fica **`conversation_assigned`** (reaproveitado, não um nome novo) — só o `bus_event` varia. | Seguindo o idioma já usado por `set_ai`/`assign`/`assign_unified` ([conversation_service.py:502](../app/services/conversation_service.py#L502): `ws_event` constante, `bus_event` variável). Zero mudança em [useWebSocket.js](../web/static/js/hooks/useWebSocket.js) — o nome já está na lista de eventos repassados. |

---

## 1 — Resumo executivo

Hoje um atendimento tem `assignee_user_id` (pessoa) mas nenhum conceito de "time/departamento". O schema já reservou uma coluna `team_id` sem nunca ter sido escrita. Este plano: (a) cria as tabelas `teams` (CRUD simples: nome + descrição) e `team_members` (N:N usuário↔time, copiando `inbox_members` linha por linha), com FK real na coluna já existente; (b) adiciona `conversation_repo.set_team` + `conversation_service.assign_team` (paralelos aos de atendente, nunca via `_transfer`) com dois eventos novos no bus; (c) publica `server/routes/teams.py` (CRUD) e estende os dois pontos de dados que o frontend já usa para atendente (`GET /api/atendimentos/assignable-agents` ganha `teams`; nasce `POST /api/atendimentos/{id}/assign-team`); (d) entrega as três superfícies pedidas — aba "Times" em Configurações → Usuários, opção "Atribuir time" no menu de contexto (mesmo padrão de flyout de "Atribuir atendente"), e a dimensão `team` nos dois filtros existentes (client-side do ícone de funil + server-side dos filtros salvos).

Achado que barateou o escopo: como o SELECT de listagem de conversas já seleciona a tabela `atendimentos` **inteira** ([conversation_query.py:74](../db/repositories/conversation_query.py#L74)), `team_id` **já chega de graça** em toda listagem REST assim que a coluna existir — não precisa tocar `list_conversations`/`get_with_channel`/`get_row_for_broadcast`. O único lugar que filtra campos manualmente é o payload do WS de atribuição (`_broadcast`) e o "patch" que a sidebar aplica nele — os dois precisam de uma linha nova cada, ou o campo chega no banco mas nunca acende na tela (ver Risco R1).

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 O precedente a copiar: `inbox_members`

| Peça | Onde | O que faz |
|---|---|---|
| Tabela | [db/tables.py:524-532](../db/tables.py#L524-L532) | N:N `(inbox_id, user_id)`, PK composta, FK `ON DELETE CASCADE` nos dois lados, index em `user_id` |
| Migration | [20260620_0021_inbox_members.py](../db/alembic/versions/20260620_0021_inbox_members.py) | 15 linhas: `create_table` + `create_index`; downgrade simétrico |
| Repo | [inbox_member_repo.py](../db/repositories/inbox_member_repo.py) | `member_ids(inbox_id)`, `set_members(inbox_id, user_ids)`, `inbox_ids_for_user(user_id)`, `set_inboxes_for_user(user_id, inbox_ids)`, `inbox_ids_by_user()` (bulk) |
| Wiring | [server/routes/users.py:49-51,112-114,167-173](../server/routes/users.py#L49) | Chamado DIRETO dentro do POST/PUT de usuário — sem rota própria de associação |

`teams`/`team_members` copiam essa forma quase literalmente — só o lado "canal" vira "time".

### 2.2 A coluna já reservada, sem FK

[db/tables.py:534-547](../db/tables.py#L534-L547) (tabela `atendimentos`, aliasada `conversations` em [db/tables.py:971](../db/tables.py#L971)):
```python
Column("assignee_user_id", Integer),   # NULLABLE sem FK (P1)
Column("team_id", Integer),            # NULLABLE sem FK
```
Comentário em [db/tables.py:482](../db/tables.py#L482): "NULLABLE SEM FK por robustez de ordem de migration (P1)". Já serializado (read-only) em [server/routes/v1/_common.py:139](../server/routes/v1/_common.py#L139), nunca escrito.

### 2.3 Atribuição de atendente — o padrão a replicar (e o que NÃO replicar)

- Repo puro: [conversation_repo.py:863-864](../db/repositories/conversation_repo.py#L863) — `set_assignee(conv_id, assignee_user_id)`, um `_update()` de uma coluna.
- Política + eventos SEMPRE no serviço, nunca no repo nem na rota ([conversation_service.py:407-428](../app/services/conversation_service.py#L407-L428)): `_transfer()` é o cotovelo que escreve `assignee_user_id`/`active_agent_key`/`ai_active` ATOMICAMENTE porque esses três campos são **mutuamente exclusivos** por design (D1 do plano 10 — atribuir a uma pessoa desliga a IA, atribuir à IA limpa a pessoa). **Time não entra nesse cotovelo** (D1 deste plano) — precisa da própria função simples, no molde de `set_agent()` ([conversation_service.py:590-605](../app/services/conversation_service.py#L590-L605)), que também é "campo isolado, sem exclusão mútua com nada".
- `assign()` ([conversation_service.py:457-513](../app/services/conversation_service.py#L457-L513)) é o modelo de forma exata para `assign_team()`: aplica um filtro `before_*` opcional, escolhe o `bus_event` por `if team_id else`, chama `_broadcast`, emite o system-notice.
- `_broadcast()` ([conversation_service.py:74-97](../app/services/conversation_service.py#L74-L97)) monta um dict **manual** de campos (não é um `dict(conv)` cru) — `team_id` **não está na lista** e precisa entrar aqui ou o WS nunca carrega o campo, mesmo com o banco certo.

### 2.4 REST de atribuição — os dois pontos exatos de extensão

- [server/routes/conversations.py:271-291](../server/routes/conversations.py#L271-L291) — `GET /api/atendimentos/assignable-agents`, gated por `conversation.read`, devolve `{"users": [...], "ai_agents": [...]}`. Ganha uma terceira chave `"teams"`.
- [server/routes/conversations.py:540-559](../server/routes/conversations.py#L540-L559) — `POST /api/atendimentos/{conv_id}/assign`, modelo EXATO (gate `conversation.assign` → `_guard_conv` → `conv_svc.assign(...)`) para o novo `POST /api/atendimentos/{conv_id}/assign-team`.

### 2.5 Frontend — a MESMA fonte de dados alimenta menu de contexto E os dois filtros

Achado que simplifica a arquitetura do frontend: `agentsUsers`/`agentsAi` — usados tanto no menu de contexto quanto nos dois sistemas de filtro — vêm de **UMA ÚNICA chamada**, [useConversationActions.js:305](../web/static/js/components/contacts/hooks/useConversationActions.js#L305) (`getAssignableAgents()`), e são passados por props através de [Contacts.js:143,438-439,618-619](../web/static/js/components/contacts/Contacts.js#L143) até `ContextMenu` E `ConversationFilterBar`/`ConversationFilterDialog`. Estender essa MESMA chamada com `teams` entrega os dados certos aos dois lugares pedidos (#2 e #4) sem um segundo round-trip nem um segundo caminho de props.

- **Menu de contexto**: [ContextMenu.js](../web/static/js/components/contacts/ContextMenu.js) controla qual flyout está aberto com um enum de string `openSub` (`'tags' | 'assign' | null`, [:32](../web/static/js/components/contacts/ContextMenu.js#L32)). O bloco "Atribuir atendente" ([:284-320](../web/static/js/components/contacts/ContextMenu.js#L284-L320)) usa `<AssigneeList>` ([AssigneeList.js](../web/static/js/components/contacts/AssigneeList.js)), cujo contrato `onPick(payload)` é `{kind:'none'|'user',userId}|{kind:'ai',agentKey}` — **mutuamente exclusivo por design** (o próprio propósito de `AssigneeList` é "só UM dono por vez"). Time é ortogonal (D1), então NÃO estende `AssigneeList` — ver §7 P2.
- **Filtro client-side** (ícone "sliders", [ConversationFilterBar.js:104-107](../web/static/js/components/contacts/ConversationFilterBar.js#L104-L107) → [ConversationFilterDialog.js](../web/static/js/components/contacts/ConversationFilterDialog.js)): dimensão `agent` em `CORE_DIMENSIONS` ([:34](../web/static/js/components/contacts/ConversationFilterDialog.js#L34)) e seu branch em `ValueInput` ([:202-209](../web/static/js/components/contacts/ConversationFilterDialog.js#L202-L209)) são o molde. Avaliado client-side em `clauseMatches`/`matchOne` ([conversationRows.js:141-212](../web/static/js/services/conversationRows.js#L141-L212)).
- **Filtro server-side** (filtros salvos / `/api/atendimentos/filter`): `DIMENSIONS["assignee"]` ([registry.py:43-45](../db/filters/registry.py#L43-L45)) + `_assignee_clause()` ([translate.py:212-232](../db/filters/translate.py#L212-L232)) são o molde EXATO — mesma coluna nullable, mesmo conjunto de operadores.

### 2.6 ⚠️ O ponto que quebraria em silêncio se pulado: o "patch" da sidebar é um allowlist manual, não um merge genérico

O WS que atualiza a linha da sidebar ao vivo (`conversation_assigned`, `conversation_ai_toggled`, etc.) passa por `applyConversationEvent` → `conversationPatch(row, ev)` em [conversationPatch.js:67-83](../web/static/js/services/conversationPatch.js#L67-L83) — que copia campo por campo, EXPLICITAMENTE (`if (ev.assignee_user_id !== undefined) patch.assignee_user_id = ...`). **NÃO é um merge genérico do payload.** Adicionar `team_id` ao dict do `_broadcast()` do backend (§2.3) sem adicionar a linha simétrica aqui faz o banco gravar certo, o REST devolver certo, e a tela **nunca acender** até um F5 — o mesmo tipo de divergência silenciosa que o `CLAUDE.md` já registra para `_MEDIA_KIND_SPEC` e `send_template`. Achado desta investigação, não documentado em lugar nenhum ainda.

### 2.7 RBAC — por que reaproveitar permissão em vez de criar uma nova é seguro

[domain/permission_catalog.py](../domain/permission_catalog.py) é a fonte única (sincronizada no boot por `rbac_repo.sync_core_permissions()`, [rbac_repo.py:92-113](../db/repositories/rbac_repo.py#L92-L113)). `users.manage` já gateia TANTO "Usuários" quanto "Grupos de permissão" ([roles.py:1](../server/routes/roles.py#L1): *"All gated by `users.manage`"*) — as duas abas existentes da MESMA tela. `users.manage` não está em `ROLE_DEFAULTS["gestor"]` ([server/permissions.py:24-46](../server/permissions.py#L24-L46)) — é admin-only hoje, no mesmo grupo que `apikey.manage`/`webhook.manage`/`database.manage`. `conversation.assign` ESTÁ em `ROLE_DEFAULTS["gestor"]` mas não em `["atendente"]` — exatamente o público que já usa "Atribuir atendente".

---

## 3 — Inventário das mudanças

| # | Item | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| **I1** | Tabelas `teams` / `team_members` + FK em `team_id` | Migration nova + [db/tables.py](../db/tables.py) | Não existem | baixo | M |
| **I2** | `team_repo.py` (CRUD) | `db/repositories/` (novo) | Não existe | baixo | S |
| **I3** | `team_member_repo.py` (N:N) | `db/repositories/` (novo) | Não existe — copia `inbox_member_repo.py` | baixo | S |
| **I4** | `conversation_repo.set_team()` | [conversation_repo.py:863](../db/repositories/conversation_repo.py#L863) (ao lado) | Não existe | baixo | S |
| **I5** | `conversation_service.assign_team()` | [conversation_service.py](../app/services/conversation_service.py) (ao lado de `assign`) | Não existe. NÃO passa por `_transfer` (D1) | médio | M |
| **I6** | `_broadcast()` ganha `team_id` no payload | [conversation_service.py:84-96](../app/services/conversation_service.py#L84-L96) | Campo ausente do dict manual | 🔴 alto se pulado (§2.6) | S |
| **I7** | `conversation.team_assigned`/`.team_unassigned` no catálogo | [plugins/events.py:100-108](../plugins/events.py#L100-L108) | Não existem. MESMO commit do call site (I5) | médio (guard de teste) | S |
| **I8** | Bump `WHATSBOT_API_VERSION` 1.8.0→1.9.0 + changelog + golden | [plugins/semver.py:90](../plugins/semver.py#L90), [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md) | — | baixo | S |
| **I9** | `server/routes/teams.py` (CRUD) | novo, gated `users.manage` (D5) | Não existe. Molde: [roles.py](../server/routes/roles.py) | baixo | M |
| **I10** | `assignable-agents` ganha `teams` | [conversations.py:271-291](../server/routes/conversations.py#L271-L291) | Falta a chave | baixo | S |
| **I11** | `POST /api/atendimentos/{id}/assign-team` | [conversations.py](../server/routes/conversations.py) (ao lado de `:540`) | Não existe | baixo | S |
| **I12** | `conversationPatch.js` ganha `team_id` | [:67-83](../web/static/js/services/conversationPatch.js#L67-L83) | Falta a linha (§2.6) | 🔴 alto se pulado | S |
| **I13** | `buildRows`/`convRowToSidebarRow` ganham `team_id` | [conversationRows.js:564,674](../web/static/js/services/conversationRows.js#L564) | Faltam as 2 linhas | médio | S |
| **I14** | `api.js` — funções novas (getTeams/createTeam/updateTeam/deleteTeam/assignTeam) | `web/static/js/services/api.js` | Não existem | baixo | S |
| **I15** | `useConversationActions.js` busca+guarda `teams` | [:58-59,305](../web/static/js/components/contacts/hooks/useConversationActions.js#L58) | Extensão do fetch existente | baixo | S |
| **I16** | `Contacts.js` — encaminha `teams` como prop | [:143,438-439,618-619](../web/static/js/components/contacts/Contacts.js#L143) | 3 pontos de props | baixo | S |
| **I17** | `TeamPickerList.js` (novo, sibling de `AssigneeList.js`) | `web/static/js/components/contacts/` | Não existe — ver §7 P2 | baixo | S |
| **I18** | `ContextMenu.js` — item "Atribuir time" | [:284-320](../web/static/js/components/contacts/ContextMenu.js#L284-L320) (bloco irmão) | Não existe | médio | M |
| **I19** | `UsersManager.js` — `SUBTABS` + branch `'teams'` | [:225-228,343](../web/static/js/components/UsersManager.js#L225-L228) | Falta a entrada | baixo | S |
| **I20** | `TeamsManager.js` (novo, CRUD) | `web/static/js/components/` | Não existe — molde: [RolesManager.js](../web/static/js/components/RolesManager.js) | médio | M |
| **I21** | `CORE_DIMENSIONS`/`ValueInput` ganham `team` | [ConversationFilterDialog.js:34,202-209](../web/static/js/components/contacts/ConversationFilterDialog.js#L34) | Falta a entrada | baixo | S |
| **I22** | `advClauseLabel` ganha `team` | [ConversationFilterBar.js:81-94](../web/static/js/components/contacts/ConversationFilterBar.js#L81-L94) | Falta o helper de rótulo | baixo | S |
| **I23** | `clauseMatches`/`matchOne` ganham `dim==='team'` | [conversationRows.js:141-212](../web/static/js/services/conversationRows.js#L141-L212) | Falta o branch | baixo | S |
| **I24** | `DIMENSIONS["team"]` + `_team_clause()` | [registry.py:38-68](../db/filters/registry.py#L38-L68), [translate.py:126-150,212-232](../db/filters/translate.py#L212-L232) | Não existem. Molde: `assignee` | baixo | S |
| **I25** | Testes (backend + frontend) | `tests/`, `*.test.js` | Ver Fase 8 | baixo | M |
| **I26** | Docs (`PLUGIN_BUS.md`, changelog, `CLAUDE.md` se aplicável) | `docs/` | Ver Fase 9 | baixo | S |

### 3.1 Falsos positivos descartados

| Item | Por que parece problema | Por que NÃO é |
|---|---|---|
| "`team_id` precisa entrar em `list_conversations`/`get_with_channel`" | Parece faltar código de leitura | Já vem de graça: `enriched_columns()` seleciona a tabela `conversations` INTEIRA ([conversation_query.py:74](../db/repositories/conversation_query.py#L74)), então `team_id` está em TODO row assim que a coluna existir. Zero mudança necessária ali. |
| "Precisa de permissão nova `teams.manage`" | Time é um conceito novo, "merece" permissão própria | D5: reaproveitar `users.manage` é o padrão que a própria "Grupos de permissão" já segue na MESMA tela. Ver §2.7. |
| "Atribuir time deve passar por `_transfer()`" | É o cotovelo de atribuição existente | D1: `_transfer` existe porque assignee/agente/IA são mutuamente exclusivos. Time não exclui nada — é campo isolado, como `set_agent()`/`set_ai_active()` puros. |
| "Estender `AssigneeList.js` com uma 3ª seção 'Times'" | Reaproveita o componente que já existe | O contrato `onPick` de `AssigneeList` é "substitua o dono" (mutuamente exclusivo). Time não substitui nada — precisa do próprio contrato `onPick(teamId|null)`. Ver §7 P2. |
| "É preciso um novo WS event `conversation_team_assigned`" | Parece a simetria óbvia com o bus event novo | D7: o WS event fica `conversation_assigned` (reaproveitado) — só o `bus_event` (plugin) varia, no MESMO idioma que `set_ai`/`assign_unified` já usam. Criar um WS novo exigiria editar `useWebSocket.js` à toa. |
| "Bloquear `DELETE /api/teams/{id}` se houver conversa vinculada" (como `roles.py` bloqueia role em uso) | Precedente direto em `delete_role` | A FK é `ON DELETE SET NULL` (D4) de propósito — deletar um time só limpa a etiqueta das conversas, nunca quebra nada. Bloquear seria inconsistente com o próprio desenho da FK. |

---

## 4 — O desenho

```
Schema (I1)
   │
   ├─→ Repos: team_repo, team_member_repo, conversation_repo.set_team (I2-I4)
   │       │
   │       └─→ Serviço+eventos: assign_team, _broadcast(team_id), bus catalog+bump (I5-I8)
   │               │
   │               └─→ REST: teams.py CRUD, assignable-agents+teams, assign-team (I9-I11)
   │                       │
   │                       └─→ Plumbing frontend: api.js, useConversationActions, Contacts.js,
   │                           conversationPatch.js, conversationRows.js (I12-I16)
   │                               │
   │                 ┌─────────────┼─────────────────┐
   │                 ▼             ▼                 ▼
   │           Tela "Times"   Menu de contexto   Filtro (client+server)
   │           (I19-I20)      (I17-I18)          (I21-I24)
   │
   └─→ (paralelo, a qualquer momento após I1) Testes de schema/migration
```

Cada seta é uma barreira real: o item de baixo CHAMA o de cima (não dá pra escrever `assign_team` sem `set_team`; não dá pra escrever a tela sem o endpoint).

---

## 5 — Fases e paralelização

```
WAVE 0   F1 (schema)                                                    🔴
              │
WAVE 1   F2 (repos + serviço + eventos de domínio)  [depende de: F1]    🔴
              │
WAVE 2   F3 (REST)                                  [depende de: F2]    🔴
              │
WAVE 3   F4 (plumbing frontend)                     [depende de: F3]    🔴
              │
WAVE 4   F5 (tela Times)  🟢  ·  F6 (menu de contexto)  🟢  ·  F7 (filtro)  🟢   [todas depende de: F4]
              │
WAVE 5   F8 (testes)  🟢  ·  F9 (docs)  🟢
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** | Schema | 🔴 | baixo | `teams`/`team_members` existem; `atendimentos.team_id` tem FK; `alembic upgrade head` roda limpo em ambas as direções |
| 1 | **F2** | Repos + serviço + eventos | 🔴 | médio | `assign_team()` grava e emite `conversation.team_assigned`/`.team_unassigned`; `test_bus_catalogue_matches_producers` verde |
| 2 | **F3** | REST | 🔴 | baixo | `GET /api/teams`, `POST/PUT/DELETE`, `assignable-agents` com `teams`, `assign-team` — todos respondendo |
| 3 | **F4** | Plumbing frontend | 🔴 | 🔴 alto se pulado (§2.6) | `team_id` sobrevive a um F5: aparece em `buildRows`, no WS patch, e em `convRowToSidebarRow` |
| 4 | **F5** | Tela "Times" | 🟢 | baixo | Aba nova em Configurações → Usuários, CRUD funcionando, modo escuro legível |
| 4 | **F6** | Menu de contexto | 🟢 | médio | "Atribuir time" aparece abaixo de "Atribuir atendente", mesmo padrão visual, atribui/desatribui |
| 4 | **F7** | Filtro | 🟢 | baixo | Dimensão "Time" aparece nos dois filtros (funil + salvos), filtra de verdade |
| 5 | **F8** | Testes | 🟢 | baixo | Suíte completa verde no Postgres de teste |
| 5 | **F9** | Docs | 🟢 | baixo | `test_docs_hygiene.py` verde; `docs/PLUGIN_BUS.md` com o par de eventos |

**F5, F6 e F7 são as três únicas fases verdadeiramente paralelizáveis** — despache as três juntas depois de F4. Elas tocam arquivos praticamente disjuntos (só `Contacts.js` já foi editado em F4 para todas passarem `teams` como prop — nenhuma das três volta a editá-lo).

---

### Fase 1 — Schema (🔴 sozinha)

**Objetivo:** as tabelas existem, a coluna já reservada ganha FK.

**Itens** *(`[sequencial]` — uma migration só)*:
1. **I1** — nova migration Alembic (nome `<YYYYMMDD>_0067_teams.py` — confirmar que `0066_ai_tools_plugin_id` [ainda é o head](../db/alembic/versions/20260821_0066_ai_tools_plugin_id.py) antes de fixar o número):
   - `create_table("teams", id PK, name Text NOT NULL, description Text NOT NULL server_default="", created_at Float, updated_at Float)`.
   - `create_table("team_members", team_id FK→teams.id CASCADE, user_id FK→users.id CASCADE, created_at Float, PK composta (team_id, user_id))` + `create_index("idx_team_members_user", "team_members", ["user_id"])` — cópia literal de [20260620_0021_inbox_members.py](../db/alembic/versions/20260620_0021_inbox_members.py).
   - `create_foreign_key` em `atendimentos.team_id → teams.id`, `ondelete="SET NULL"` (D4). ⚠️ Postgres exige que a coluna já exista (existe) e que não haja linha órfã (não há — a coluna nunca foi escrita).
   - `create_index("idx_atend_team_status", "atendimentos", ["team_id", "status"])` — espelha [idx_atend_assignee_status](../db/tables.py#L566).
   - `downgrade()`: drop da FK, do índice novo, de `team_members`, de `teams`, na ordem inversa.
2. Espelhar em [db/tables.py](../db/tables.py): `teams`/`team_members` como `Table` objects (ao lado de `inbox_members`, mesmo estilo de comentário); no `Column("team_id", Integer)` de `atendimentos` ([:547](../db/tables.py#L547)), adicionar `ForeignKey("teams.id", ondelete="SET NULL")` e atualizar o comentário de bloco ([:482](../db/tables.py#L482)) — ele hoje diz que os dois campos são "sem FK"; depois desta fase só `assignee_user_id` continua assim.
3. Índice novo `Index("idx_atend_team_status", atendimentos.c.team_id, atendimentos.c.status)` em `db/tables.py`, ao lado de [:566](../db/tables.py#L566).

**Pronto quando:** `alembic upgrade head` e `alembic downgrade -1` rodam limpos no Postgres de teste; `db/tables.py` bate com o schema resultante (guard de drift do repo, se houver, verde); nenhuma linha em `atendimentos` tem `team_id` não-NULL apontando pra time inexistente (trivialmente verdade — a coluna nunca foi escrita).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** migration `20260910_0067_teams.py` (`teams`, `team_members`, FK `fk_atend_team_id` em `atendimentos.team_id`, índice `idx_atend_team_status`); espelhado em `db/tables.py` (Tables `teams`/`team_members`, FK na coluna, comentário de bloco atualizado).
- **Como foi feito / decisões:** cópia estrutural de `20260620_0021_inbox_members.py`; FK nomeada explicitamente (`fk_atend_team_id`, R4) para não depender do autogerado do Alembic.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `alembic upgrade head` e `alembic downgrade -1` + upgrade de novo, limpos, no Postgres de teste (`WHATSBOT_TEST_DB_URL`); `alembic heads` confirma cadeia linear única.

---

### Fase 2 — Repositórios, serviço e eventos de domínio (🔴 sozinha) `[depende de: F1]`

**Objetivo:** dá para atribuir/desatribuir um time a uma conversa, com o bus avisando quem quiser saber.

**Itens** *(`[sequencial]` — cada um depende do anterior)*:
1. **I2** — `db/repositories/team_repo.py`: `list_all() -> list[dict]`, `get(team_id) -> dict|None`, `create(name, description="") -> dict`, `update(team_id, name=None, description=None) -> dict|None`, `delete(team_id) -> bool`. Padrão `with get_engine().begin()`/`.connect()`, sem bloqueio de exclusão (§3.1 — a FK já cuida).
2. **I3** — `db/repositories/team_member_repo.py`, cópia estrutural de [inbox_member_repo.py](../db/repositories/inbox_member_repo.py): `member_ids(team_id)`, `set_members(team_id, user_ids)`, `team_ids_for_user(user_id)` (prova direta do requisito #3 — usuário em vários times), `member_ids_by_team()` (bulk, para a tela de Times listar contagem/membros sem N+1 queries).
3. **I4** — `conversation_repo.set_team(conv_id, team_id: int | None) -> dict | None: return _update(conv_id, {"team_id": team_id})`, ao lado de [set_assignee](../db/repositories/conversation_repo.py#L863).
4. **I5** — `conversation_service.assign_team(deps, conv, team_id, *, actor_id=None, actor_name=None) -> dict | None`, no MOLDE de [assign()](../app/services/conversation_service.py#L457-L513) mas SEM passar por `_transfer` (D1): grava via `conversation_repo.set_team`, escolhe `bus_event = "conversation.team_assigned" if team_id else "conversation.team_unassigned"`, chama `_broadcast(deps, "conversation_assigned", bus_event, updated, previous_team_id=...)` (D7). Sem filtro `before_*` novo nem system-notice nesta fase (§7 P3 — decide se vale a pena).
5. **I6** — `_broadcast()` ([:84-96](../app/services/conversation_service.py#L84-L96)) ganha `"team_id": conv.get("team_id")` no dict do payload — **sem isto, F4/F7 não têm o que ler ao vivo** (§2.6).
6. **I7** — `plugins/events.py`: acrescentar `"conversation.team_assigned"`, `"conversation.team_unassigned"` a `KNOWN_EVENTS` ([:100-108](../plugins/events.py#L100-L108)), com o comentário de payload ao lado (`conversation_id, team_id, previous_team_id, ts` — igual ao molde de `.assigned`/`.unassigned`).
7. **I8** — `plugins/semver.py:90` — bump `WHATSBOT_API_VERSION = "1.9.0"`. Entrada no topo de [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md) (nível MINOR — nome novo no catálogo, no mesmo commit do call site). Rodar `UPDATE_PLUGIN_API_SURFACE=1 venv/bin/python -m pytest tests/contracts/test_plugin_api_surface.py` pra regenerar `tests/goldens/plugin_api_surface.json`.

**Pronto quando:** `venv/bin/python -m pytest tests/contracts/test_plugin_api_surface.py` verde (inclui `test_bus_catalogue_matches_producers` reconhecendo o call site de `assign_team`); um teste manual (script Python ou teste novo) chamando `assign_team` grava `team_id`, emite os dois eventos certos conforme o valor, e o dict de `_broadcast` carrega `team_id`.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** `db/repositories/team_repo.py` (CRUD) + `team_member_repo.py` (N:N, cópia de `inbox_member_repo.py`); `conversation_repo.set_team`; `conversation_service.assign_team` (ao lado de `set_agent`, NÃO passa por `_transfer`); `_broadcast` ganhou `team_id`; `conversation.team_assigned`/`.team_unassigned` em `KNOWN_EVENTS`; bump `WHATSBOT_API_VERSION` 1.8.0→1.9.0 + changelog + golden regenerado.
- **Como foi feito / decisões:** o scanner de `test_bus_catalogue_matches_producers` só reconhece um evento como "produzido" quando o NOME LITERAL é passado direto a uma chamada com `emit`/`broadcast` no nome — um `bus_event` resolvido por variável (como em `assign()`) não bate. `assign_team` por isso usa `if team_id: _broadcast(..., "conversation.team_assigned", ...) else: _broadcast(..., "conversation.team_unassigned", ...)` com os dois literais em call sites distintos, em vez de uma única ternária atribuída a uma variável.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/contracts/test_plugin_api_surface.py` (5/5) verde, golden regenerado só com os 2 nomes novos + versão; `tests/integration/test_teams.py` prova `assign_team` gravando e emitindo os dois eventos certos conforme o valor (via `filter.event.before_emit`).

---

### Fase 3 — REST (🔴 sozinha) `[depende de: F2]`

**Objetivo:** publicar os endpoints que o frontend vai consumir.

**Itens** *(`[sequencial]`)*:
1. **I9** — `server/routes/teams.py` (novo), registrado em `create_app`/router assembly no MESMO ponto que `roles.py` (verificar `server/app.py` ou o módulo de wiring de rotas): `GET /api/teams` (lista com `member_user_ids` via `team_member_repo.member_ids_by_team()`), `POST /api/teams` (`{name, description?, member_user_ids?}`), `PUT /api/teams/{id}`, `DELETE /api/teams/{id}` — todos `permission_denied(request, "users.manage")` (D5), no molde de [roles.py](../server/routes/roles.py).
2. **I10** — [assignable_agents()](../server/routes/conversations.py#L271-L291) ganha `"teams": [{"id": t["id"], "name": t["name"]} for t in team_repo.list_all()]` na resposta.
3. **I11** — `POST /api/atendimentos/{conv_id}/assign-team`, body `{"team_id": int | null}`, gated `conversation.assign` (D6), no molde EXATO de [assign()](../server/routes/conversations.py#L540-L559): `_guard_conv` → `conv_svc.assign_team(deps, _conv, team_id, actor_id=..., actor_name=...)` → `_ok({"conversation": conv})`.

**Pronto quando:** `curl` autenticado cria/lista/edita/apaga um time; `assignable-agents` traz `teams`; `assign-team` muda `team_id` da conversa e a resposta reflete.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** `server/routes/teams.py` (CRUD, gated `users.manage`), registrado em `server/app.py` ao lado de `roles_routes`; `assignable_agents()` ganhou a chave `"teams"`; `POST /api/atendimentos/{id}/assign-team` (gated `conversation.assign`, molde exato de `/assign`).
- **Como foi feito / decisões:** seguiu o molde de `server/routes/roles.py`/`users.py` à risca; `team_repo` entrou no import agregado de `db.repositories` já existente em `conversations.py`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/integration/test_teams.py` — CRUD via `client` autenticado, `assignable-agents` trazendo `teams`, `assign-team` mudando `team_id` e refletindo na resposta.

---

### Fase 4 — Plumbing frontend compartilhado (🔴 sozinha) `[depende de: F3]`

**Objetivo:** `team_id` sobrevive do banco até a tela, ao vivo, sem F5. Habilitador de F5/F6/F7 — todas dependem desta.

**Itens** *(`[sequencial]`, arquivos pequenos)*:
1. **I14** — `web/static/js/services/api.js`: `getTeams()`, `createTeam(data)`, `updateTeam(id, data)`, `deleteTeam(id)`, `assignTeam(convId, teamId)` — mesmo padrão HTTP das funções de `roles`/`assign` já existentes ali.
2. **I15** — [useConversationActions.js:58-59,305](../web/static/js/components/contacts/hooks/useConversationActions.js#L58): novo state `teams` (`useState([])`), populado de `getAssignableAgents()` (que agora traz `teams` — I10) no mesmo `.then()` que já popula `agentsUsers`/`agentsAi`; exportado no objeto de retorno do hook ao lado deles ([:342](../web/static/js/components/contacts/hooks/useConversationActions.js#L342)).
3. **I16** — [Contacts.js:143,438-439,618-619](../web/static/js/components/contacts/Contacts.js#L143): desestruturar `teams` do hook e passar como prop para `ContextMenu` E `ConversationFilterBar`, nos MESMOS 3 pontos que já passam `agentsUsers`/`agentsAi`.
4. **I12** — [conversationPatch.js:72](../web/static/js/services/conversationPatch.js#L72) (⚠️ §2.6, o item que quebra em silêncio se pulado): `if (ev.team_id !== undefined) patch.team_id = ev.team_id;`, junto de `assignee_user_id`. Atualizar o `@typedef ConversationRow`/`ConversationEvent` no topo do arquivo com `team_id`.
5. **I13** — [conversationRows.js](../web/static/js/services/conversationRows.js): `buildRows()` ([:564](../web/static/js/services/conversationRows.js#L564), junto de `assignee_user_id: cv.assignee_user_id,`) ganha `team_id: cv.team_id,`; `convRowToSidebarRow()` ([:674](../web/static/js/services/conversationRows.js#L674)) ganha `team_id: p.team_id,`.

**Pronto quando:** `node --test web/static/js/services/conversationPatch.test.js web/static/js/services/conversationRows.test.js` verde (mesmo sem teste novo ainda — os existentes não podem quebrar); no painel, atribuir um time via `curl` direto no endpoint da F3 e ver a mudança refletir na sidebar SEM F5 (prova de que o WS carrega o campo).

#### Status de execução — Fase 4
**Estado:** ✅ Concluída
- **O que foi feito:** `api.js` (getTeams/createTeam/updateTeam/deleteTeam/assignTeam); `useConversationActions.js` (`teams` state + `handleAssignTeam`); `Contacts.js`/`ContactList.js` encaminham `teams` como prop (3 pontos + 1 hop extra em `ContactList.js` não previsto no inventário original); `conversationPatch.js` ganhou `team_id`; `conversationRows.js` (`buildRows`/`convRowToSidebarRow`) ganharam `team_id`.
- **Como foi feito / decisões:** achado além do inventário original (I16): `Contacts.js` passa `channels`/`agentsUsers`/`agentsAi` para `<ContactList>`, que só ENTÃO os repassa para `<ConversationFilterBar>` — um segundo hop de props que o plano não tinha mapeado. `teams` precisou do mesmo hop em `ContactList.js` (destructuring da prop + forward), senão o filtro do funil nunca veria a lista de times.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test` dos dois arquivos de teste (107→108, verde); atribuir time via REST e ver `team_id` no payload de `_broadcast` (provado indiretamente pelo teste de eventos do bus + pela leitura do código de `conversationPatch.js`/`conversationRows.js`).

---

### Fase 5 — Tela "Times" (🟢 paralela a F6/F7) `[depende de: F4]`

**Objetivo:** requisito #1 — CRUD de time com descrição e picklist de membros, na aba nova.

**Itens** *(`[sequencial]` dentro da fase)*:
1. **I19** — [UsersManager.js:225-228](../web/static/js/components/UsersManager.js#L225-L228): `SUBTABS` ganha `{ id: 'teams', label: 'Times' }`; branch em [:343](../web/static/js/components/UsersManager.js#L343) renderiza `<TeamsManager>` quando `view === 'teams'`; deep-link `sub: 'teams'` no mesmo padrão de `sub: 'roles'`.
2. **I20** — `web/static/js/components/TeamsManager.js` (novo), no molde de [RolesManager.js](../web/static/js/components/RolesManager.js): lista de times (nome, descrição, contagem de membros), criar/editar com formulário (nome, descrição, picklist de usuários — reaproveita a lista de usuários que o backend de `/api/teams` GET já teria que fornecer, ou um fetch a `getUsers()` já existente em `api.js`), excluir com `ConfirmModal`. Usar classes `wa-*`/`.wa-field` (regra de modo escuro do `CLAUDE.md`).

**Pronto quando:** aba "Times" aparece ao lado de "Grupos de permissão"; criar/editar/excluir um time funciona; um usuário aparece marcável em mais de um time ao mesmo tempo (requisito #3); legível com o modo escuro ligado.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída
- **O que foi feito:** `TeamsManager.js` (novo, molde `RolesManager.js` — lista, criar/editar inline com picklist de membros via checkbox, excluir com `ConfirmModal`); `UsersManager.js` ganhou a sub-aba "Times" (`SUBTABS`, branch de render, helper `_subtabPath`); `useDeepLink.js` ganhou `sub: 'teams'` no registry de `/users` (achado além do inventário: sem isso `/users/teams/<id>` cairia no branch `else` e geraria uma URL errada, tratando o id do time como id de usuário).
- **Como foi feito / decisões:** picklist de membros reaproveita `getUsers()` (já existente em `api.js`), no mesmo padrão de checkbox-list que `UserForm` já usa para caixas de entrada.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check` em todos os arquivos tocados; fluxo de CRUD provado indiretamente pelos testes de `tests/integration/test_teams.py` (mesmos endpoints que a tela consome).

---

### Fase 6 — Menu de contexto "Atribuir time" (🟢 paralela a F5/F7) `[depende de: F4]`

**Objetivo:** requisito #4 — a opção pedida, no lugar pedido, com o padrão visual pedido.

**Itens** *(`[sequencial]`)*:
1. **I17** — `web/static/js/components/contacts/TeamPickerList.js` (novo, sibling pequeno de [AssigneeList.js](../web/static/js/components/contacts/AssigneeList.js) — ver §7 P2 pela razão de não estender `AssigneeList`): busca client-side (mesmo padrão de `search`/`q`), opção "Nenhum time" quando `currentTeamId != null` (equivalente ao `canUnassign`), lista de times filtrada por texto, `onPick(teamId | null)`.
2. **I18** — [ContextMenu.js](../web/static/js/components/contacts/ContextMenu.js): terceiro valor de `openSub` (`'team'`, ao lado de `'tags'`/`'assign'`, [:32](../web/static/js/components/contacts/ContextMenu.js#L32)); bloco irmão do de "Atribuir atendente" ([:284-320](../web/static/js/components/contacts/ContextMenu.js#L284-L320)), **logo abaixo dele** (ordem pedida pelo usuário), rotulado **"Atribuir time"**, mesmo padrão de `onMouseEnter`/`openSubmenu`/`scheduleClose`/`flyoutCls`/`flyoutRef`/`flyoutTop`; gate `showTeamSection = (teams.length > 0 || currentTeamId != null) && can('conversation.assign')` (D6). O hook de posicionamento do flyout ([:136-144](../web/static/js/components/contacts/ContextMenu.js#L136-L144)) precisa incluir `teams.length` nas dependências do `useEffect`.
3. `onPick` do `TeamPickerList` chama `assignTeam(conversationId, teamId)` (I14) e fecha o submenu, no mesmo padrão que `pickAssign` já faz para atendente.

**Pronto quando:** botão direito numa conversa mostra "Atribuir time" logo abaixo de "Atribuir atendente"; abre o flyout com busca + lista; escolher um time atribui (linha muda ao vivo — prova que F4 está correta); "Nenhum time" desatribui; um usuário sem `conversation.assign` não vê a opção (mesmo comportamento de "Atribuir atendente" hoje).

#### Status de execução — Fase 6
**Estado:** ✅ Concluída
- **O que foi feito:** `TeamPickerList.js` (novo, sibling de `AssigneeList.js`, contrato `onPick(teamId|null)`); `ContextMenu.js` ganhou o bloco "Atribuir time" logo abaixo de "Atribuir atendente", terceiro valor de `openSub` (`'team'`), gate `showTeamSection`.
- **Como foi feito / decisões:** seguiu §7 P2 à risca — nenhuma extensão de `AssigneeList`. `teams.length` entrou nas deps do `useLayoutEffect` de posicionamento do flyout (junto de `humanAgents.length`/`aiAgents.length`).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check`; comportamento de atribuir/desatribuir provado end-to-end por `tests/integration/test_teams.py::test_assign_team_via_rest_sets_and_clears` (mesmo endpoint que o `onPick` do `TeamPickerList` chama via `handleAssignTeam`/`assignTeam`).

---

### Fase 7 — Filtro por time (🟢 paralela a F5/F6) `[depende de: F4]`

**Objetivo:** requisito #2 — dimensão "Time" nos dois sistemas de filtro que já existem.

**Itens** *(`[paralelo]` entre si — client-side e server-side não se tocam)*:
1. **I21** — [ConversationFilterDialog.js](../web/static/js/components/contacts/ConversationFilterDialog.js): `CORE_DIMENSIONS` ([:34](../web/static/js/components/contacts/ConversationFilterDialog.js#L34)) ganha `{ key: 'team', label: 'Time', ops: ['eq','ne'], valueType: 'team' }`; `ValueInput` ganha um branch `t === 'team'` análogo ao de `agent` ([:202-209](../web/static/js/components/contacts/ConversationFilterDialog.js#L202-L209)), usando a prop `teams` (I16) em vez de `agentsUsers`/`agentsAi`.
2. **I22** — [ConversationFilterBar.js](../web/static/js/components/contacts/ConversationFilterBar.js): `advClauseLabel` ([:81-94](../web/static/js/components/contacts/ConversationFilterBar.js#L81-L94)) ganha o caso `dim === 'team'` (rótulo do chip do filtro ativo), lendo de `teams` (nova prop, [:237](../web/static/js/components/contacts/ConversationFilterBar.js#L237)).
3. **I23** — [conversationRows.js](../web/static/js/services/conversationRows.js): `matchOne()` ([:141-151](../web/static/js/services/conversationRows.js#L141-L151)) ganha `if (dim === 'team') return String(c.team_id) === value;`; `clauseMatches()` ([:207](../web/static/js/services/conversationRows.js#L207)) inclui `'team'` na lista de dims multi-select (`dim === 'channel' || ... || dim === 'team'`).
4. **I24** — [db/filters/registry.py](../db/filters/registry.py): `DIMENSIONS["team"] = Dim("team", "team", frozenset({"equal_to","in","is_present","is_not_present"}), "Time")` ([:38-68](../db/filters/registry.py#L38-L68)). [db/filters/translate.py](../db/filters/translate.py): dispatch `if kind == "team": return _team_clause(op, values)` ([:126-150](../db/filters/translate.py#L126-L150)) + `_team_clause(op, values)` no MOLDE EXATO de `_assignee_clause` ([:212-232](../db/filters/translate.py#L212-L232)), mas sobre `conversations.c.team_id` e sem o caso especial `"me"`.

**Pronto quando:** o ícone de funil oferece "Time" como dimensão, com a lista de times pra escolher; filtrar por um time mostra só as conversas daquele time; um filtro salvo com a dimensão "Time" sobrevive a um F5 (prova de que o server-side também entende); `GET /api/atendimentos/filter-schema` lista `team`.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída
- **O que foi feito:** `CORE_DIMENSIONS`/`MULTI_TYPES`/`ValueInput` em `ConversationFilterDialog.js`; `DIM_LABELS`/`_teamLabel`/`advClauseLabel` em `ConversationFilterBar.js`; `matchOne`/`clauseMatches` em `conversationRows.js`; `DIMENSIONS["team"]` + `_team_clause` em `registry.py`/`translate.py`.
- **Como foi feito / decisões:** achado além do inventário I21-I24: `web/static/js/services/conversationFilterSpec.js` — o tradutor client-side que serializa `advFilters` em query params para `/api/atendimentos/filter`/`count` — tinha sua PRÓPRIA lista fechada de dims genéricas (`addClause`/`clauseParamKey`) que não incluía `'team'`; sem essa entrada, `isServerExpressible` devolveria `false` para qualquer cláusula de time e o filtro salvo cairia silenciosamente no fallback client-side, nunca sobrevivendo a um F5 — exatamente o critério de "pronto" desta fase. Adicionado `'team'` aos dois pontos genéricos do arquivo.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/integration/test_teams.py::test_team_filter_equal_to_and_is_not_present` prova `/api/atendimentos/filter?team=<id>` e `?team=none` server-side; `test_team_dimension_listed_in_filter_schema` prova que `/api/atendimentos/filter-schema` lista `team`; `node --test` cobre `matchOne`/`clauseMatches` client-side.

---

### Fase 8 — Testes (🟢 paralela a F9)

**Objetivo:** o contrato fica travado.

**Itens** *(`[paralelo]`)*:
1. Backend: `tests/integration/` novo (ou extensão de um arquivo de atendimentos existente) — CRUD de time; `team_ids_for_user` com um usuário em 2+ times (requisito #3, prova direta); `assign_team`/`assign-team` gravando e emitindo os dois eventos certos conforme o valor (`team_id` presente vs `None`); `assignable-agents` trazendo `teams`; filtro server-side (`registry`+`translate`) com `equal_to`/`in`/`is_present`/`is_not_present`; RBAC — usuário sem `users.manage` toma 403 no CRUD de times, sem `conversation.assign` toma 403 no `assign-team`.
2. `tests/contracts/test_plugin_api_surface.py` — já rodado na F2, reconfirmar aqui verde depois de tudo (golden não deve mudar de novo).
3. Frontend: `node --test web/static/js/services/conversationRows.test.js web/static/js/services/conversationPatch.test.js` — os testes existentes continuam verdes e, se fizer sentido, um caso novo por arquivo cobrindo `team_id`.
4. Migration round-trip: `alembic upgrade head` + `alembic downgrade -1` + `upgrade head` de novo, limpo.

**Pronto quando:** `venv/bin/python -m pytest` (core inteiro) verde no Postgres de teste; `node --test` dos dois arquivos verde.

#### Status de execução — Fase 8
**Estado:** ✅ Concluída
- **O que foi feito:** `tests/integration/test_teams.py` (novo, 12 casos): CRUD via REST + RBAC (403 sem `users.manage`/`conversation.assign`), `team_ids_for_user` com usuário em 2 times, `assign_team` gravando e emitindo os dois eventos certos (via `filter.event.before_emit`), `assignable-agents` trazendo `teams`, filtro server-side (`equal_to`/`is_not_present` via `team=<id>`/`team=none`), D1 (time não mexe no assignee) e D4 (deletar time preserva a conversa). `conversationRows.test.js`/`conversationPatch.test.js` ganharam cobertura de `team_id` (assertions novas em testes existentes + 1 teste novo).
- **Como foi feito / decisões:** reaproveitou o fixture `client` (não `build_app`) e o padrão de captura de eventos de `tests/integration/api/test_conversation_events.py` (`filter.event.before_emit` sobre um loop real).
- **Problemas / pendências:** `tests/integration/characterization/test_audit_characterization.py::test_audit_matrix_is_complete` falha na suíte completa — **pré-existente, não relacionado a este plano** (drift de `channel.*`/`plugin.imported`/`plugin.deleted` no `AUDITABLE_EVENTS`, nada sobre `team`/`conversation.team_*`). Confirmado por inspeção do assert (lista só nomes de canal/plugin).
- **Verificação:** `venv/bin/python -m pytest tests/integration/test_teams.py` — 12/12 verde. `venv/bin/python -m pytest tests/contracts` — verde (inclui `test_plugin_api_surface.py` e `test_docs_hygiene.py`). `node --test` dos 2 arquivos — 108/108 verde. Suíte completa (`tests/integration`, exceto o pré-existente acima) — ver nota abaixo.

---

### Fase 9 — Documentação (🟢 paralela a F8)

**Objetivo:** o próximo a mexer aqui acha isso sem ler código.

**Itens** *(`[paralelo]`)*:
1. [docs/PLUGIN_BUS.md](../docs/PLUGIN_BUS.md) — linha nova na tabela de eventos `conversation.*` (perto de `.pinned`/`.labeled`, [:72-73](../docs/PLUGIN_BUS.md#L72)) documentando `conversation.team_assigned`/`.team_unassigned` (payload: `conversation_id, team_id, previous_team_id, ts`).
2. [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md) — já criada na F2 (I8); revisar redação.
3. `CLAUDE.md` — **decisão de escopo, não obrigatório** (ver §7 P4): se algo aqui vira ⚠️ digno do arquivo raiz (candidato: "time e atendente são independentes — não confundir com o cotovelo de `_transfer`"), no máximo 2 linhas, no bloco de "IA, agentes e transcrição" ou num bloco de "Atendimentos" a criar. Verificar teto de caracteres antes ([test_docs_hygiene.py](../tests/contracts/test_docs_hygiene.py)).
4. **Não** mexer em [docs/MODELO_DE_DADOS.md](../docs/MODELO_DE_DADOS.md) — esse arquivo documenta especificamente as "20 tabelas" do índice do `CLAUDE.md` (config/contacts/messages/.../ai_*); `teams`/`team_members`/`inbox_members` são de outra família (RBAC/inbox) e nunca tiveram entrada lá — o comentário em `db/tables.py` (Fase 1) é a documentação canônica, no mesmo padrão de `inbox_members`.

**Pronto quando:** `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py` verde.

#### Status de execução — Fase 9
**Estado:** ✅ Concluída
- **O que foi feito:** [docs/PLUGIN_BUS.md](../docs/PLUGIN_BUS.md) ganhou a linha de `conversation.team_assigned`/`.team_unassigned`; [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md) ganhou a entrada `## 1.9.0`; `CLAUDE.md` ganhou 1 bullet ⚠️ (D1: time é independente do assignee, não usar `_transfer`) no bloco "IA, agentes e transcrição" — orçamento de caracteres medido antes (78 113 → segue abaixo do teto de 90 000).
- **Como foi feito / decisões:** P4 (b) — havia orçamento de sobra (~12k chars), então a linha entrou. `docs/MODELO_DE_DADOS.md` NÃO foi tocado (decisão do plano — `teams`/`team_members` são da família RBAC/inbox, não das "20 tabelas" documentadas ali).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py` — 2/2 verde (tamanho + fatos preservados).

---

## 6 — Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | `conversationPatch.js` é allowlist manual (§2.6) | `team_id` grava certo no banco, REST devolve certo, mas a sidebar SÓ atualiza depois de F5 — parece "funcionou" em teste manual de API e falha silenciosamente na tela | I12 é item próprio na F4, com o teste de "atribuir por curl e ver mudar ao vivo" no critério de pronto |
| R2 | Confundir `assign_team` com o cotovelo `_transfer` | Um dev que só olhar `assign()`/`assign_unified()` por cima pode "unificar" e acoplar time à exclusão mútua de assignee/agente/IA, quebrando D1 | D1 travada; comentário em `assign_team()` explicando por que NÃO usa `_transfer` |
| R3 | Catálogo de bus sem call site no mesmo commit | `test_bus_catalogue_matches_producers` fica vermelho | I7 e I5 na MESMA fase (F2), nunca separados |
| R4 | FK nova em coluna já existente, sem backfill necessário mas com risco de nome de constraint colidir | Postgres recusa a migration se já existir uma constraint com nome igual (baixa chance, mas `atendimentos` tem várias) | Nomear a FK explicitamente (`fk_atend_team_id`) em vez de deixar o Alembic autogerar |
| R5 | `TeamPickerList.js` divergir visualmente de `AssigneeList.js` (dois componentes parecidos, mantidos por pessoas diferentes no futuro) | Um ganha um ajuste de estilo/acessibilidade que o outro não recebe | Reaproveitar o MÁXIMO de classes/estrutura de `AssigneeList.js` ao escrever `TeamPickerList.js` (copiar o esqueleto, não reinventar) |
| R6 | Times sem membro nenhum sendo atribuíveis a conversas | Não é bug — um time pode nascer vazio e ganhar membros depois (igual RBAC permite role sem usuário) | Nenhuma validação de "time precisa ter ≥1 membro" — decisão consciente, não lacuna |
| R7 | `DELETE /api/teams/{id}` sem confirmação dupla | Perde a etiqueta de todas as conversas daquele time de uma vez (ainda que sem quebrar nada, por D4) | `ConfirmModal` no frontend (I20), como `RolesManager` já faz para roles |

---

## 7 — Perguntas em aberto

**P1 — Reaproveitar `users.manage` ou criar `teams.manage`?**
✅ **DECIDIDO (2026-09-10, por engenharia — D5): reaproveitar `users.manage`.**
Contexto: o usuário não pediu granularidade de permissão para times; a tela onde a aba vive já usa uma permissão única para as duas abas existentes.
(a) **`users.manage`** — zero permissão nova, consistente com "Grupos de permissão" na mesma tela. Trade-off: só admin cria/edita times (gestor não pode, hoje).
(b) `teams.manage` nova — mais granular, permitiria dar a um gestor o poder de organizar times sem dar acesso a criar login. Custo: entrada em `PERMISSION_CATALOG`, decisão de incluir ou não em `ROLE_DEFAULTS["gestor"]`, uma permissão a mais pra manter.
**Recomendação mantida: (a).** Se no futuro um gestor precisar gerenciar times sem `users.manage`, é uma migration aditiva trivial — não vale pagar o custo agora sem um pedido concreto.

**P2 — Estender `AssigneeList.js` com uma 3ª seção "Times", ou componente novo `TeamPickerList.js`?**
✅ **DECIDIDO (2026-09-10, por engenharia): componente novo.**
Contexto: `AssigneeList.onPick` é `{kind:'none'|'user',userId}|{kind:'ai',agentKey}` — um contrato de "substitua o dono único", porque assignee/agente SÃO mutuamente exclusivos (D1 do plano 10, que criou o componente). Time NÃO é mutuamente exclusivo com nada (D1 deste plano).
(a) **Componente novo, pequeno, cópia estrutural** — contrato próprio `onPick(teamId|null)`, sem herdar a semântica de exclusão que não se aplica.
(b) Estender `AssigneeList` com `kind:'team'` — economiza um arquivo, mas obriga o componente compartilhado a saber que um dos seus "kinds" não exclui os outros dois, complicando toda leitura futura do componente.
**Recomendação mantida: (a).**

**P3 — `assign_team` ganha system-notice card no fio (tipo "🔵 Time: Suporte")?**
⏸️ **RECOMENDADO: não, nesta entrega.**
Contexto: `assign()`/`assign_unified()` emitem um card visível na conversa via `_emit_notice` ([system_notices.py](../server/system_notices.py), grupo `"assignment"` já existente e extensível por `register_notice`). O usuário não pediu isso para time.
(a) **Sem notice card agora** — menor escopo, adere ao pedido literal. Fácil de adicionar depois: `register_notice("team_assigned", "assignment", _f_team_assigned)` é aditivo, reaproveitando o MESMO grupo/gate de config que "assigned"/"unassigned" já usam.
(b) Incluir agora, por paridade visual com "Atribuir atendente".
**Recomendação: (a)** — mas se o usuário quiser paridade visual completa, é uma extensão pequena e sem risco de arquitetura (o mecanismo já é genérico).

**P4 — Uma linha nova no `CLAUDE.md`?**
⏸️ **RECOMENDADO: avaliar no fim, provavelmente não.**
Contexto: o arquivo tem teto de caracteres travado por teste ([test_docs_hygiene.py](../tests/contracts/test_docs_hygiene.py)) e a política do plano 139 é "regra + aviso, ≤2 linhas, só se for armadilha não-óbvia".
(a) Sem linha nova — "time independe de atendente" é uma decisão de produto direta, não uma armadilha que alguém vá pisar sem avisar.
(b) Duas linhas no bloco de atendimentos, citando D1 (não usar `_transfer`) — candidato real a ⚠️ porque É o tipo de coisa que um dev apressado "unificaria" errado.
**Recomendação: (b), mas só se sobrar orçamento de caracteres** — medir com `wc -c CLAUDE.md` antes de decidir.

**P5 — Endpoint de escrita de `team_id` na fachada pública `/api/v1`?**
⏸️ **FORA DE ESCOPO**, por pedido explícito do usuário (mudança "no core", sem menção à v1). Nota de baixo custo para o futuro: como `team_id` já é lido em [v1/_common.py:139](../server/routes/v1/_common.py#L139), expor um PATCH de atribuição na v1 seria pequeno depois que este plano existir — mas não faz parte desta entrega.

---

## 8 — Apêndice — arquivos-chave

**Backend / schema**
- Migration nova (`db/alembic/versions/`) + [db/tables.py](../db/tables.py) (`teams`, `team_members`, FK em `team_id`, índice novo).

**Backend / repositórios**
- `db/repositories/team_repo.py` — novo.
- `db/repositories/team_member_repo.py` — novo.
- [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — `set_team` (:863 ao lado).

**Backend / serviço e bus**
- [app/services/conversation_service.py](../app/services/conversation_service.py) — `assign_team` (novo, ao lado de `assign` :457), `_broadcast` (:84-96, ganha `team_id`).
- [plugins/events.py](../plugins/events.py) — `KNOWN_EVENTS` (:100-108).
- [plugins/semver.py](../plugins/semver.py) — `WHATSBOT_API_VERSION` (:90).

**Backend / rotas**
- `server/routes/teams.py` — novo (CRUD).
- [server/routes/conversations.py](../server/routes/conversations.py) — `assignable_agents` (:271-291), `assign-team` novo (ao lado de `:540`).

**Backend / filtros**
- [db/filters/registry.py](../db/filters/registry.py) — `DIMENSIONS["team"]` (:38-68).
- [db/filters/translate.py](../db/filters/translate.py) — dispatch (:126-150) + `_team_clause` (novo, ao lado de `_assignee_clause` :212).

**Frontend / dados e plumbing**
- `web/static/js/services/api.js` — funções novas de times.
- [web/static/js/components/contacts/hooks/useConversationActions.js](../web/static/js/components/contacts/hooks/useConversationActions.js) — `teams` state (:58,305).
- [web/static/js/components/contacts/Contacts.js](../web/static/js/components/contacts/Contacts.js) — props (:143,438-439,618-619).
- [web/static/js/services/conversationPatch.js](../web/static/js/services/conversationPatch.js) — `conversationPatch` (:67-83).
- [web/static/js/services/conversationRows.js](../web/static/js/services/conversationRows.js) — `buildRows` (:564), `convRowToSidebarRow` (:674), `matchOne`/`clauseMatches` (:141-212).

**Frontend / telas e menu**
- `web/static/js/components/TeamsManager.js` — novo.
- [web/static/js/components/UsersManager.js](../web/static/js/components/UsersManager.js) — `SUBTABS`/branch (:225-228,343).
- `web/static/js/components/contacts/TeamPickerList.js` — novo.
- [web/static/js/components/contacts/ContextMenu.js](../web/static/js/components/contacts/ContextMenu.js) — bloco novo (:284-320 ao lado), `openSub` (:32).
- [web/static/js/components/contacts/ConversationFilterDialog.js](../web/static/js/components/contacts/ConversationFilterDialog.js) — `CORE_DIMENSIONS`/`ValueInput` (:34,202-209).
- [web/static/js/components/contacts/ConversationFilterBar.js](../web/static/js/components/contacts/ConversationFilterBar.js) — `advClauseLabel` (:81-94).

**Testes**
- `tests/integration/` — novo (times, assign-team, filtros).
- [tests/contracts/test_plugin_api_surface.py](../tests/contracts/test_plugin_api_surface.py) — regenerar golden.
- [web/static/js/services/conversationRows.test.js](../web/static/js/services/conversationRows.test.js), [conversationPatch.test.js](../web/static/js/services/conversationPatch.test.js).

**Docs**
- [docs/PLUGIN_BUS.md](../docs/PLUGIN_BUS.md), [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md), `CLAUDE.md` (condicional — P4).

---

## 9 — Checklist de verificação

- [x] `alembic upgrade head` e `alembic downgrade -1` limpos no Postgres de teste (round-trip completo confirmado)
- [~] `venv/bin/python -m pytest` (core inteiro) — `tests/contracts` verde; `tests/integration` verde À EXCEÇÃO de `test_audit_matrix_is_complete`, que já falhava ANTES deste plano (drift de eventos `channel.*`/`plugin.imported`/`plugin.deleted`, nada sobre `team`) — não corrigido aqui por estar fora de escopo
- [x] `venv/bin/python -m pytest tests/contracts` verde — `test_plugin_api_surface.py` (bump 1.8.0→1.9.0 confirmado no golden) e `test_docs_hygiene.py` inclusos
- [x] `node --test web/static/js/services/conversationRows.test.js web/static/js/services/conversationPatch.test.js` verde (108/108)
- [x] `tests/integration/test_teams.py` (novo) prova via HTTP real: CRUD, RBAC (403 sem `users.manage`/`conversation.assign`), 2+ times por usuário, `assign-team` independente do assignee (D1), delete preserva conversa (D4), eventos do bus corretos, filtro server-side (`team=<id>`/`team=none`) e `filter-schema` listando `team`
- [ ] Reload manual (NÃO executado nesta sessão — sem instância rodando/navegador): criar um time com descrição, marcar o mesmo usuário em 2 times, editar, excluir pela tela
- [ ] Reload manual: filtrar a sidebar pelo funil por "Time", salvar o filtro e recarregar a página
- [ ] Reload manual: botão direito numa conversa → "Atribuir time" abaixo de "Atribuir atendente", atribui/desatribui, linha muda AO VIVO sem F5
- [x] Usuário sem `users.manage` toma 403 em `/api/teams` (provado por teste); ⚠️ ocultação da aba na UI não testada em navegador
- [x] Usuário sem `conversation.assign` toma 403 em `/assign-team` (provado por teste); ⚠️ ocultação do item no menu não testada em navegador
- [x] Deletar um time com conversa atribuída: a conversa sobrevive, só perde a etiqueta (D4) — provado por teste
- [ ] Modo escuro ligado: tela "Times" e o flyout "Atribuir time" legíveis (NÃO verificado visualmente — classes `wa-*`/`.wa-field` usadas em todo o código novo, por convenção, mas sem confirmação em navegador)
- [x] Nenhum segredo em log/URL/payload de evento (times não carregam credencial nenhuma)
- [x] `git status` limpo de WIP alheio em `conversation_service.py`/`conversationRows.js` antes de abrir F2/F4 (só `agent_run_service.py`/`test_routing_reason.py` tinham WIP alheio, arquivos não tocados por este plano)
