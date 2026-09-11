# Plano 154 — Visibilidade de conversa por time, para quem está na mesma caixa mas não é do time

> **Status:** PLANEJAMENTO · **Data:** 2026-09-10 · **Escopo:** pequeno-médio (1 coluna + 1 helper SQL reaproveitado em 3 pontos + CRUD existente + 1 checkbox)
> **Origem:** pedido direto do usuário, na tela `/users` → aba Times → editor de um time (ver captura anexada ao pedido: espaço em branco entre "Membros" e os botões Cancelar/Salvar). Extensão do [plano 153 — Times](153-plano-times-teams.md), que reservou `atendimentos.team_id` como ETIQUETA e travou explicitamente (D2) que time não afeta visibilidade. Este plano reabre D2, sob duas decisões novas tomadas pelo usuário na conversa que originou este plano (ver §0).
> **Método:** leitura do código real com `arquivo:linha` conferido em `server/authz.py`, `db/repositories/conversation_repo.py`, `server/routes/conversations.py`, `server/routes/v1/*.py`, `db/tables.py`, `db/repositories/team_repo.py`, `server/routes/teams.py`, `web/static/js/components/TeamsManager.js`. Nada de memória — cada citação abaixo foi lida nesta sessão.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela **ANTES** de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ *(usuário, nesta conversa)* | O toggle é **POR TIME** (coluna nova em `teams`), não uma config global. Vive no formulário de edição de time que já existe em `TeamsManager.js`, no espaço que o usuário apontou (entre "Membros" e os botões). | Coluna nova em `teams`, não em `config`. Sem tela nova — só um campo a mais no form que já existe. |
| **D2** ✅ *(usuário, nesta conversa)* | **Default = visível** (comportamento atual preservado). É uma restrição **OPT-IN**: times novos e os que já existem (ex: "Comercial") continuam do jeito que estão até alguém marcar a restrição. | Coluna `nullable=False, server_default="0"` (0 = sem restrição = todo mundo da caixa vê, igual hoje). Nenhum time existente muda de comportamento no dia do deploy. |
| **D3** ✅ *(usuário, nesta conversa)* | Quando restrito, o efeito é **só esconder da LISTAGEM** (sidebar/`/api/atendimentos`, `/filter`, `/count`, e os espelhos em `/api/v1`). **NÃO** bloqueia abrir a conversa direto por ID/link — sem o 404 que `_inbox_hidden`/`_guard_conv` já dão pra caixa fora de escopo, e sem tocar nenhum path de escrita (assign/reply/resolve/delete/react). | A mudança fica **inteira dentro das 3 funções de listagem** de `conversation_repo.py` (`list_conversations`, `list_filtered`, `count_tab_counts`). Zero mudança em `_inbox_hidden`, `_guard_conv`, `can_access_inbox`, `MessagingService`, ou em qualquer rota de escrita. |
| **D4** ✅ *(herdada do plano 153, reafirmada)* | Conversa com `team_id IS NULL` nunca é afetada — sempre visível a quem já tem acesso à caixa, como hoje. | A cláusula nova é sempre `team_id IS NULL OR ...` — o primeiro braço garante isso trivialmente. |
| **D5** ✅ *(decisão de engenharia deste plano — ver §7 P1)* | Quem tem `conversation.read_all` (ou é admin) **sempre** vê tudo, com ou sem restrição — mesmo bypass que `visible_inbox_ids` já dá pra caixa. A cláusula nova só entra quando o usuário JÁ está escopado por caixa (`inbox_ids is not None` — ver [`server/authz.py:88-90`](../server/authz.py#L88-L90)). | Nenhuma lógica de RBAC nova em `authz.py`: o gate reaproveita o mesmo sinal que a caixa já usa (ver §2.3). |
| **D6** ✅ *(decisão de engenharia deste plano)* | A cláusula SQL fica em `conversation_repo.py` (repo), não em `server/authz.py`. | `authz.py` continua devolvendo só `list[int] | None` de inboxes — não vira dono de uma segunda dimensão de escopo. Ver trade-off completo em §7 P1. |

---

## 1 — Resumo executivo

Hoje um usuário vê uma conversa se, e somente se, for membro da caixa (`inbox_members`) dela — ou tiver `conversation.read_all`/for admin. `atendimentos.team_id` (plano 153) é hoje só uma etiqueta: nunca entra em nenhuma query de visibilidade (D2 do plano 153, citada literalmente: *"pertencer a um time não amplia nem restringe quais conversas um usuário vê"*).

Este plano dá a cada time um interruptor (`teams.restrict_visibility`, default desligado) que, quando ligado, esconde da **listagem** as conversas daquele time para quem está na mesma caixa mas não é membro do time — sem tocar em nada além das 3 funções de listagem que já recebem `inbox_ids`/`current_user_id`. Achado que barateia o escopo: essas 3 funções (`list_conversations`, `list_filtered`, `count_tab_counts`) já são o ÚNICO ponto de leitura usado tanto pelo painel quanto pela fachada `/api/v1` ([server/routes/v1/conversations.py:71,111,137](../server/routes/v1/conversations.py#L71)) — uma mudança ali cobre as duas superfícies de graça, sem tocar rota nenhuma além do CRUD de time que já existe.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 Visibilidade de conversa hoje: só por caixa, binário

[`server/authz.py:77-90`](../server/authz.py#L77-L90) — `visible_inbox_ids(request)`:
```python
def visible_inbox_ids(request: Request) -> list[int] | None:
    user = current_user(request)
    if user is None:
        return None  # legacy/open — no scoping
    if rbac_repo.user_has_permission(user["id"], "conversation.read_all"):
        return None  # admin (short-circuit) ou explicit read_all ⇒ sees all
    return inbox_member_repo.inbox_ids_for_user(user["id"])
```
`None` = sem escopo (vê tudo); lista (mesmo vazia) = escopado por caixa. Não existe hoje nenhuma hierarquia gestor/atendente — `gestor` e `atendente` estão igualmente sujeitos a `inbox_members` por padrão ([server/permissions.py:24-46](../server/permissions.py#L24-L46), nenhum dos dois tem `conversation.read_all`).

### 2.2 Onde isso vira SQL — os 3 pontos exatos (e só eles)

[`db/repositories/conversation_repo.py`](../db/repositories/conversation_repo.py):
- `list_conversations(..., inbox_ids=None, current_user_id=None, ...)` (:514-518) → filtro em **:540-542**:
  ```python
  if inbox_ids is not None:
      stmt = stmt.where(conversations.c.inbox_id.in_(inbox_ids) if inbox_ids else sa_false())
  ```
- `list_filtered(where, *, inbox_ids=None, current_user_id=None, ...)` (:554-556) → filtro idêntico em **:563-565**.
- `count_tab_counts(where, *, inbox_ids=None, current_user_id=None)` (:574-575) → filtro idêntico em **:609-611**. Já usa `current_user_id` para `mine_filter`/`mention_filter` (:583-593) — o parâmetro não é novo aqui.

**Achado que barateia o plano**: `current_user_id` **já é parâmetro nas três**, e **todo call site que passa `inbox_ids` não-`None` também passa `current_user_id`** — conferido por grep em todo o repo (excluindo testes):
- Painel: [`server/routes/conversations.py:177-178,233-234,267-268`](../server/routes/conversations.py#L177) — as 3 chamadas passam `inbox_ids=visible_inbox_ids(request)` e `current_user_id=(_u.get("id") if _u else None)` juntos.
- `/api/v1`: [`server/routes/v1/conversations.py:71-72,111-112,137-138`](../server/routes/v1/conversations.py#L71) — idem, via `visible_inboxes(request)` (alias de `visible_inbox_ids`, [`_common.py:77-78`](../server/routes/v1/_common.py#L77)).
- A ÚNICA chamada que **não** passa nenhum dos dois é [`server/routes/v1/messages.py:75`](../server/routes/v1/messages.py#L75) (`list_conversations(status="open", contact_ids=[...], limit=10)`) — um helper interno de roteamento de webhook, sem usuário/request. Como `inbox_ids` fica `None` ali, a cláusula nova (gated em `inbox_ids is not None`, D5/D6) **não entra** — comportamento intacto, nenhum caso a tratar.

Conclusão: **gatear a cláusula nova em `if inbox_ids is not None:`** (mesmo braço que já escopa por caixa) é seguro — nunca há um `current_user_id=None` nesse braço em produção, e o único chamador sem usuário também não tem `inbox_ids`.

### 2.3 Conversa única e paths de escrita — NÃO tocam nesta mudança (D3)

- `_inbox_hidden(request, inbox_id)` ([conversations.py:108-115](../server/routes/conversations.py#L108-L115)) e `_guard_conv` ([:118-130](../server/routes/conversations.py#L118-L130)) continuam comparando só `inbox_id` contra `visible_inbox_ids(request)`. Não recebem `team_id`, não são tocados.
- `can_access_inbox` ([`authz.py:93-102`](../server/authz.py#L93-L102)) idem — usado por `MessagingService`/envio, sem mudança.
- Isso é a materialização direta de D3: abrir a conversa por link/ID continua funcionando para quem está na caixa, restrito ou não pelo time.

### 2.4 A tabela `teams` e o form que já existe

[`db/tables.py:539-547`](../db/tables.py#L539-L547):
```python
teams = Table(
    "teams", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
```
`db/repositories/team_repo.py` — `create(name, description="")` / `update(team_id, *, name=None, description=None)`, ambos `with get_engine().begin()`. `server/routes/teams.py` — `POST /api/teams` e `PUT /api/teams/{id}`, gated `users.manage` (D5 do plano 153), aceitam `name`/`description`/`member_user_ids` do body.

**O lugar exato do toggle**: [`web/static/js/components/TeamsManager.js:145-157`](../web/static/js/components/TeamsManager.js#L145-L157) — o bloco "Membros" (label + checkboxes de usuário) termina na linha 156 (`</div></div>`), e o `<div class="flex gap-2 justify-end">` dos botões Cancelar/Salvar começa na 157. É **exatamente** o espaço em branco que o usuário marcou na captura de tela. O `onClick` do botão Salvar (:161-168) já monta o payload `{name, description, member_user_ids}` — ganha mais uma chave.

### 2.5 Convenção de coluna booleana no repo: `Integer` 0/1, nunca `Boolean`

Zero usos de `sa.Boolean` em `db/tables.py` (grep confirmado) — todo flag é `Integer, nullable=False, server_default="0"/"1"` (ex: `contacts.is_pinned`, `contacts.ai_enabled`, [db/tables.py:71-80](../db/tables.py#L71-L80)). Na escrita, o padrão do repo é `1 if enabled else 0` na camada de repositório — precedentes diretos: [`tool_repo.py:102`](../db/repositories/tool_repo.py#L102), [`plugin_repo.py:57`](../db/repositories/plugin_repo.py#L57), [`contact_repo.py:53`](../db/repositories/contact_repo.py#L53).

### 2.6 Nenhum bump de versão de plugin necessário

Este plano não toca `KNOWN_EVENTS`/`KNOWN_FILTERS`, não adiciona símbolo público a `entry.*`, não muda contrato de plugin. `server/authz.py` e `db/repositories/conversation_repo.py` estão **fora da superfície versionada por design** (CLAUDE.md: *"`db.repositories` e companhia ficam de fora de propósito — são dependência real, não API declarada"*). `WHATSBOT_API_VERSION` continua `1.9.0`.

---

## 3 — Inventário das mudanças

| # | Item | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| **I1** | Coluna `teams.restrict_visibility` | Migration nova + [db/tables.py:539-547](../db/tables.py#L539-L547) | Não existe | baixo | S |
| **I2** | `team_repo.create`/`update` ganham `restrict_visibility` | [team_repo.py:29,40-51](../db/repositories/team_repo.py#L29) | Faltam o parâmetro e a conversão `1 if ... else 0` | baixo | S |
| **I3** | `_team_visible_clause(current_user_id)` — helper SQL novo | `conversation_repo.py` (novo, ao lado de `_notify_private_enabled`) | Não existe | médio | S |
| **I4** | `list_conversations`/`list_filtered`/`count_tab_counts` aplicam a cláusula | [:540-542](../db/repositories/conversation_repo.py#L540), [:563-565](../db/repositories/conversation_repo.py#L563), [:609-611](../db/repositories/conversation_repo.py#L609) | Falta a linha em cada (mesmo `if inbox_ids is not None:`, D5/D6) | 🔴 alto se pulado em só 1 dos 3 (inconsistência entre listagem e contagem) | S |
| **I5** | `import teams, team_members` em `conversation_repo.py` | [conversation_repo.py:19-21](../db/repositories/conversation_repo.py#L19) | Faltam no import agregado | baixo | S |
| **I6** | `server/routes/teams.py` — `create_team`/`update_team` repassam `restrict_visibility` | [teams.py:35-49,52-70](../server/routes/teams.py#L35) | Falta ler `body.get("restrict_visibility")` e passar ao repo | baixo | S |
| **I7** | `TeamsManager.js` — checkbox no editor + badge no card fechado | [:104-121](../web/static/js/components/TeamsManager.js#L104) (badge, opcional) e [:145-166](../web/static/js/components/TeamsManager.js#L145) (checkbox + payload do Salvar) | Não existe | baixo | S |
| **I8** | Testes (backend) | `tests/integration/test_teams.py` (extensão) | Ver Fase 4 | baixo | M |
| **I9** | Docs (CLAUDE.md, condicional) | `CLAUDE.md` | Ver §7 P2 | baixo | S |

### 3.1 Falsos positivos descartados

| Item | Por que parece problema | Por que NÃO é |
|---|---|---|
| "Precisa mudar `_inbox_hidden`/`_guard_conv`/`can_access_inbox`" | É onde a caixa hoje bloqueia leitura/escrita individual | D3: a restrição é só de LISTAGEM. Abrir direto continua igual — nenhum desses três é tocado. |
| "Precisa de `visible_team_ids` em `authz.py`, simétrico a `visible_inbox_ids`" | Parece o padrão óbvio a replicar | A forma de dado é diferente: `visible_inbox_ids` devolve uma LISTA de ids porque a comparação é direta (`inbox_id IN (...)`). Aqui a regra depende de DOIS dados por linha (`team_id` da conversa + flag do time) — não dá para reduzir a uma lista de ids sem já fazer o JOIN, que é trabalho de repo, não de authz. Ver §7 P1 para o trade-off completo. |
| "Precisa de bump de `WHATSBOT_API_VERSION`" | Toda mudança de schema no core parece candidata | Nenhum nome novo entra em `KNOWN_EVENTS`/`KNOWN_FILTERS`/`entry.*` — fora da superfície versionada (§2.6). |
| "Precisa de WS/evento novo pra sidebar reagir ao vivo quando o admin liga a restrição" | Parece o padrão de `conversation_assigned` do plano 153 | Fora de escopo deste plano — ver §7 P3 (a config passa a valer no próximo fetch/reload da listagem, não é um campo por-conversa que muda ao vivo). |
| "Times sem nenhum membro, com restrição ligada, escondem a conversa de todo mundo (inclusive de quem a criou)" | Parece um estado quebrado | É esperado e simétrico a R6 do plano 153 ("time vazio é permitido"): quem precisar ver desliga a restrição ou entra no time — não é um bug, é a consequência direta do desenho pedido. |

---

## 4 — Fases

```
WAVE 0   F1 (schema: coluna em teams)                                    🔴
              │
WAVE 1   F2 (repo: team_repo + conversation_repo._team_visible_clause)   🔴  [depende de: F1]
              │
WAVE 2   F3 (REST: teams.py repassa o campo)                             🔴  [depende de: F2]
              │
WAVE 3   F4 (frontend: checkbox em TeamsManager.js)  🟢 · F5 (testes)  🟢  [ambas dependem de: F3]
              │
WAVE 4   F6 (docs)  🟢
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** | Schema | 🔴 | baixo | `teams.restrict_visibility` existe, `nullable=False, server_default="0"`; `alembic upgrade head`/`downgrade -1` limpos |
| 1 | **F2** | Repo (team_repo + conversation_repo) | 🔴 | médio | `_team_visible_clause` aplicada nos 3 pontos; teste manual via SQL/Python prova a cláusula |
| 2 | **F3** | REST | 🔴 | baixo | `POST`/`PUT /api/teams` gravam e devolvem `restrict_visibility` |
| 3 | **F4** | Frontend | 🟢 | baixo | Checkbox aparece no editor, no espaço indicado; salvar reflete no card |
| 3 | **F5** | Testes | 🟢 | baixo | Suíte de `test_teams.py` cobre os 3 cenários do §6 |
| 4 | **F6** | Docs | 🟢 | baixo | `test_docs_hygiene.py` verde (se a linha entrar) |

**F4 e F5 são paralelizáveis entre si** (arquivos disjuntos: um é `.js`, outro é `tests/integration/test_teams.py`) — despache as duas juntas depois de F3.

---

### Fase 1 — Schema (🔴 sozinha)

**Objetivo:** a coluna existe, com o default que preserva o comportamento atual (D2).

**Itens** *(`[sequencial]`)*:
1. **I1** — nova migration Alembic `db/alembic/versions/20260910_0068_team_restrict_visibility.py` (confirmar que `0067_teams` [ainda é o head](../db/alembic/versions/20260910_0067_teams.py) antes de fixar o número — `alembic heads`):
   ```python
   def upgrade() -> None:
       op.add_column("teams", sa.Column(
           "restrict_visibility", sa.Integer(), nullable=False, server_default="0"))

   def downgrade() -> None:
       op.drop_column("teams", "restrict_visibility")
   ```
2. Espelhar em [db/tables.py:539-547](../db/tables.py#L539-L547): `Column("restrict_visibility", Integer, nullable=False, server_default="0")`, com um comentário curto de uma linha (`# 1 = esconde da listagem pra quem está na caixa mas não é do time — plano 154`) — no MESMO estilo dos outros flags da tabela (ex: `agent_bot_enabled` em `inboxes`, [db/tables.py:500](../db/tables.py#L500)).

**Pronto quando:** `alembic upgrade head` e `alembic downgrade -1` + upgrade de novo rodam limpos no Postgres de teste; `select restrict_visibility from teams` devolve `0` para o time "Comercial" já existente (prova de D2 — nada muda pra quem já tem dado).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** Migration `0068_team_restrict_visibility` (add_column `teams.restrict_visibility`, `Integer, nullable=False, server_default="0"`) + espelhada em `db/tables.py:547`.
- **Como foi feito / decisões:** Sem desvio do plano. Confirmado `0067_teams` como head antes de fixar o revision id.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** Testado contra `WHATSBOT_TEST_DB_URL` (não a `DATABASE_URL` de produção/dev, que é a instância sob systemd): `alembic upgrade head` → `downgrade -1` → `upgrade head` limpos; `information_schema.columns` confirma `restrict_visibility integer NOT NULL DEFAULT 0`.

---

### Fase 2 — Repo: `team_repo` + `_team_visible_clause` (🔴 sozinha) `[depende de: F1]`

**Objetivo:** a regra de visibilidade existe em SQL e o CRUD de time sabe gravar/ler o campo.

**Itens** *(`[sequencial]` — cada um depende do anterior)*:
1. **I2** — [`team_repo.py`](../db/repositories/team_repo.py):
   - `create(name, description="", restrict_visibility=False)` (:29) → `insert(teams).values(..., restrict_visibility=1 if restrict_visibility else 0)` (molde: [`tool_repo.py:102`](../db/repositories/tool_repo.py#L102)).
   - `update(team_id, *, name=None, description=None, restrict_visibility=None)` (:40) → só grava se `restrict_visibility is not None`, convertendo `1 if restrict_visibility else 0` (molde: [`tool_override_repo.py:114`](../db/repositories/tool_override_repo.py#L114), padrão de "campo opcional distinto de `None`" já usado em `name`/`description` na mesma função).
2. **I5** — [`conversation_repo.py:19-21`](../db/repositories/conversation_repo.py#L19): acrescentar `teams, team_members` ao import agregado de `db.tables`.
3. **I3** — novo helper privado em `conversation_repo.py` (ao lado de `_notify_private_enabled`, [:468-478](../db/repositories/conversation_repo.py#L468)):
   ```python
   def _team_visible_clause(current_user_id: int | None):
       """Uma conversa some da LISTAGEM (não do acesso direto — D3 do plano 154)
       se: (a) não tem time, OU (b) o time não está com restrict_visibility
       ligado, OU (c) o usuário É membro daquele time. Só é chamada quando o
       usuário já está escopado por caixa (inbox_ids is not None) — quem tem
       conversation.read_all/admin nunca passa por aqui (D5)."""
       restricted_team_ids = select(teams.c.id).where(teams.c.restrict_visibility == 1)
       member_team_ids = (select(team_members.c.team_id)
                          .where(team_members.c.user_id == current_user_id)
                          if current_user_id is not None else select(team_members.c.team_id).where(sa_false()))
       return or_(
           conversations.c.team_id.is_(None),
           conversations.c.team_id.notin_(restricted_team_ids),
           conversations.c.team_id.in_(member_team_ids),
       )
   ```
   ⚠️ Verificar se `or_`/`sa_false` já estão importados no arquivo (usar os mesmos que `list_conversations` já usa para `sa_false()`, [:542](../db/repositories/conversation_repo.py#L542)) — se `or_` não estiver, importar de `sqlalchemy`.
4. **I4** — aplicar a cláusula nos 3 pontos, sempre dentro do `if inbox_ids is not None:` já existente (D5/D6):
   - [`list_conversations` :540-542](../db/repositories/conversation_repo.py#L540)
   - [`list_filtered` :563-565](../db/repositories/conversation_repo.py#L563)
   - [`count_tab_counts` :609-611](../db/repositories/conversation_repo.py#L609)
   ```python
   if inbox_ids is not None:
       stmt = stmt.where(conversations.c.inbox_id.in_(inbox_ids) if inbox_ids else sa_false())
       stmt = stmt.where(_team_visible_clause(current_user_id))
   ```

**Pronto quando:** um teste Python direto (fixture de banco) prova que, com um time `restrict_visibility=1` e uma conversa nele: (a) membro do time vê via `list_conversations`; (b) não-membro da mesma caixa NÃO vê; (c) usuário com `conversation.read_all` vê (porque `inbox_ids` chega `None` e o braço nem executa); (d) conversa com `team_id=None` sempre aparece pra quem tem acesso à caixa.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** `team_repo.create`/`update` ganharam `restrict_visibility` (conversão `1 if ... else 0`, `update` só grava se não-`None`). `conversation_repo.py` importou `teams, team_members` e `or_`; novo helper `_team_visible_clause(current_user_id)` ao lado de `_notify_private_enabled`; aplicado dentro do `if inbox_ids is not None:` já existente nas 3 funções (`list_conversations`, `list_filtered`, `count_tab_counts`).
- **Como foi feito / decisões:** Sem desvio do plano — `or_` não estava importado no arquivo, foi acrescentado ao import agregado de `sqlalchemy` (conforme o aviso em I3).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** Script Python ad-hoc contra `WHATSBOT_TEST_DB_URL` criando 2 usuários + 1 time restrito (1 membro) + 1 conversa com esse `team_id`: membro vê via `list_conversations`; não-membro da mesma caixa NÃO vê nem em `list_conversations` nem em `list_filtered`; usuário com `inbox_ids=None` (equivalente a `read_all`) vê; `count_tab_counts` diverge em exatamente 1 entre membro e não-membro (mesma conversa). Dados de teste limpos ao final (time e usuários deletados).

---

### Fase 3 — REST (🔴 sozinha) `[depende de: F2]`

**Objetivo:** o CRUD que já existe aceita e devolve o campo novo.

**Itens** *(`[sequencial]`)*:
1. **I6** — [`server/routes/teams.py`](../server/routes/teams.py):
   - `create_team` (:35-49): ler `restrict_visibility = bool(body.get("restrict_visibility"))` e passar a `team_repo.create(name, description, restrict_visibility=restrict_visibility)`.
   - `update_team` (:52-70): `restrict_visibility = body.get("restrict_visibility")` (mantém `None` se ausente — não força default ao editar só o nome), passar direto a `team_repo.update(..., restrict_visibility=restrict_visibility)` (o repo já trata `None` como "não mexe", I2).
   - Nenhuma mudança em `_serialize` (:17-18) — `**team` já espalha todas as colunas de `teams`, `restrict_visibility` chega de graça assim que a coluna existir (mesmo achado que o plano 153 documentou para `team_id` em `enriched_columns`).

**Pronto quando:** `curl -X POST /api/teams -d '{"name":"Suporte","restrict_visibility":true}'` devolve `{"team": {..., "restrict_visibility": 1}}`; `PUT` sem a chave preserva o valor anterior; `GET /api/teams` lista o campo pra todos os times.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** `create_team` lê `restrict_visibility` do body (`bool(...)`, default falsy) e repassa ao repo. `update_team` lê o campo preservando `None` quando ausente (repo já trata `None` como "não mexe"). `_serialize` não mudou — `**team` já espalha a coluna nova.
- **Como foi feito / decisões:** Sem desvio do plano.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** Coberto pelos testes HTTP da Fase 5 (rodados juntos ao final) — `POST`/`PUT /api/teams` gravam e devolvem `restrict_visibility` corretamente, e `PUT` parcial preserva o valor.

---

### Fase 4 — Frontend: checkbox no editor (🟢 paralela a F5) `[depende de: F3]`

**Objetivo:** o usuário liga/desliga a restrição no lugar exato que apontou na captura de tela.

**Itens** *(`[sequencial]`)*:
1. **I7a** — [`TeamsManager.js`](../web/static/js/components/TeamsManager.js) `TeamCard` (:90-93): novo state `const [restrictVisibility, setRestrictVisibility] = useState(!!team.restrict_visibility);`, resetado no mesmo `useEffect` de `editing` (:95-101).
2. **I7b** — entre o bloco "Membros" (termina :156) e os botões (começa :157), inserir:
   ```js
   html`
     <label class="flex items-start gap-2 cursor-pointer">
       <input type="checkbox" class="mt-0.5" checked=${restrictVisibility}
         onChange=${(e) => setRestrictVisibility(e.target.checked)} />
       <span class="text-[13px] text-wa-text">
         Restringir às conversas deste time
         <span class="block text-[12px] text-wa-secondary">
           Quando ligado, atendentes da mesma caixa que não são deste time deixam de ver essas conversas na lista (mas ainda podem abri-las por link direto).
         </span>
       </span>
     </label>
   `
   ```
   Usar classes `wa-*`/texto claro em ambos os temas (regra de modo escuro do `CLAUDE.md`) — `text-wa-text`/`text-wa-secondary` já cobrem isso, sem cor crua nova.
3. **I7c** — no `onClick` do botão Salvar (:161-168), acrescentar `restrict_visibility: restrictVisibility` ao payload passado a `onSave`.
4. **I7d** *(opcional, polish)* — no card fechado (:112-117), ao lado do badge "N membro(s)", um badge curto tipo "🔒 Restrito" quando `team.restrict_visibility` for truthy, mesmo padrão visual do badge existente.

**Pronto quando:** abrir o editor de um time mostra o checkbox no espaço indicado; marcar, salvar e reabrir mantém o estado; modo escuro ligado, texto legível.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída
- **O que foi feito:** `TeamCard` ganhou state `restrictVisibility` (reset no mesmo `useEffect` de `editing`), o checkbox no espaço entre "Membros" e os botões Cancelar/Salvar, a chave `restrict_visibility` no payload do `onSave`, e o badge opcional "🔒 Restrito" no card fechado ao lado do badge de membros.
- **Como foi feito / decisões:** `NewTeamForm` (criação) não ganhou o campo — D1 fala do "formulário de edição de time que já existe", e o item I7 do inventário só cita `TeamCard`. Time novo nasce com o default do backend (0/desligado, D2).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** Revisão visual do JSX/HTM gerado (classes `wa-*`/`text-wa-secondary`, sem cor crua) — validação funcional no navegador fica pendente de um teste manual de UI (não executado nesta sessão; ver checklist §9).

---

### Fase 5 — Testes (🟢 paralela a F4) `[depende de: F3]`

**Objetivo:** os três cenários do §6 ficam travados por teste HTTP real, no mesmo arquivo que já cobre times.

**Itens** *(`[paralelo]` entre si, todos em `tests/integration/test_teams.py`)*:
1. CRUD: criar time com `restrict_visibility=true`, confirmar na resposta e num `GET /api/teams` subsequente; `PUT` parcial (só `name`) preserva o valor.
2. Cenário de visibilidade — fixture com 2 usuários na MESMA inbox (`inbox_members`), um deles também membro do time restrito:
   - Conversa A (`team_id` = time restrito): o membro do time a vê em `GET /api/atendimentos`; o não-membro NÃO a vê na listagem, mas recebe 200 (não 404) em `GET /api/atendimentos/{A.id}` — prova direta de D3.
   - Conversa B (`team_id` = time NÃO restrito): ambos veem.
   - Conversa C (`team_id = None`): ambos veem.
   - Usuário com `conversation.read_all`: vê A, B e C independente de membership.
3. Repetir o cenário 2 (só a parte de listagem) contra `POST /api/atendimentos/filter` e `/count` — os 3 pontos de I4 não podem divergir.
4. Migration round-trip: `alembic upgrade head` + `downgrade -1` + `upgrade head`, limpo.

**Pronto quando:** `venv/bin/python -m pytest tests/integration/test_teams.py` verde; `venv/bin/python -m pytest tests/contracts` continua verde (nenhuma superfície de plugin tocada, golden não muda).

#### Status de execução — Fase 5
**Estado:** ✅ Concluída
- **O que foi feito:** Em `tests/integration/test_teams.py`: `test_team_crud_restrict_visibility` (grava/lê o campo, `PUT` parcial preserva), `test_team_default_restrict_visibility_is_off` (D2), e `test_restrict_visibility_hides_from_listing_not_from_direct_access` (cenário completo do §5 — inbox dedicado via `_mk_inbox`/`_open_conv_in_inbox` para não mexer na membership do inbox default compartilhado, 2 times + 3 usuários + 3 conversas, cobrindo listagem simples, `/filter` e `/count` via POST, acesso direto por ID (D3) e `conversation.read_all` (D5)).
- **Como foi feito / decisões:** Round-trip de migration já verificado manualmente na Fase 1 (não duplicado como teste automatizado — o repo não tem precedente de teste de migration isolado por revision, e o `alembic upgrade/downgrade/upgrade` já rodou limpo contra o Postgres de teste).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `venv/bin/python -m pytest tests/integration/test_teams.py -q` → 15 passed. `venv/bin/python -m pytest tests/contracts -q` → verde, golden de plugin API inalterado (nenhuma superfície tocada).

**Nota sobre `pytest` (core inteiro) vs. suíte isolada:** rodar a suíte inteira sem filtro produz um conjunto de falhas que **varia entre execuções** (confirmado rodando 2x seguidas com conjuntos diferentes) — sintoma clássico de contaminação de estado no Postgres de teste COMPARTILHADO entre arquivos (`_engine_ready` é `scope="session"`, um só schema pro processo inteiro). Achado concreto: [tests/contracts/test_close_assignee_filter.py:96](../tests/contracts/test_close_assignee_filter.py#L96) fecha uma conversa do telefone `5511970000001`; [tests/integration/test_teams.py:127](../tests/integration/test_teams.py#L127) (`test_team_delete_clears_conversation_team_id_not_break_it`, teste PRÉ-EXISTENTE, não tocado por este plano) reusa o MESMO telefone — quando `contracts` roda antes de `integration` na coleta, `_open_conv` (que não reabre fechada por padrão) pega a conversa já fechada de outro arquivo e o `assert conv["status"] == "open"` quebra. As demais falhas (`test_alembic_hygiene` — revision `0058_merge_p50_p57` com dois pais, prefixos de sequence duplicados 0037/0042/0043/0046/0052; `test_audit_matrix_is_complete` — `AUDITABLE_EVENTS` de canal sem célula no golden; `test_quoted_reply_ingest`, `test_inbound_provider_ts_ordering`, `test_legacy_scripts`) são de arquivos/migrations de outras eras (plano 50, RBAC de canal) que este plano nunca tocou — confirmado por `git log` nos arquivos envolvidos. Nenhuma falha, em nenhuma das duas rodadas, apontou para `_team_visible_clause`, `team_repo`, `server/routes/teams.py` ou `TeamsManager.js`; os 3 testes NOVOS deste plano passaram consistentemente nas duas rodadas em que a suíte completa chegou a executá-los. Fora de escopo consertar essas pré-existências aqui.

---

### Fase 6 — Documentação (🟢, ao final)

**Objetivo:** decidir com orçamento medido se isto vira uma linha no `CLAUDE.md` (ver §7 P2).

**Itens**:
1. Medir `wc -c CLAUDE.md` antes de decidir (mesmo procedimento do plano 153, Fase 9).
2. Se houver orçamento: 1 linha ⚠️ no bloco "IA, agentes e transcrição" (perto da linha que o plano 153 já deixou sobre D1 de time), citando que `teams.restrict_visibility` só afeta LISTAGEM, não acesso direto — candidato real a armadilha ("por que o link ainda abre?").
3. **Não** criar `docs/ATENDIMENTOS.md` nem qualquer guia novo — não há um guia de área para RBAC/visibilidade de conversa hoje, e criar um do zero por causa de 1 coluna é desproporcional (decisão consciente, registrar se alguém perguntar depois).

**Pronto quando:** `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py` verde.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída
- **O que foi feito:** Medido `wc -c CLAUDE.md` = 78.410 (teto 150k) — orçamento sobrando. Acrescentada 1 linha ⚠️ logo após a linha que o plano 153 já deixou sobre `team_id`/`assignee_user_id` (bloco "IA, agentes e transcrição"), citando que `restrict_visibility` só afeta a LISTAGEM (P2 opção b). Nenhum guia novo criado (`docs/ATENDIMENTOS.md` etc.) — decisão consciente, já registrada no próprio plano.
- **Como foi feito / decisões:** Sem desvio do plano.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py` verde.

---

## 5 — Cenários de visibilidade (referência para os testes da Fase 5)

| Usuário | Membro da caixa? | Membro do time restrito? | `conversation.read_all`? | Vê conversa A (do time restrito) na LISTAGEM? | Abre A por ID direto? |
|---|---|---|---|---|---|
| U1 | sim | sim | não | ✅ sim | ✅ sim |
| U2 | sim | não | não | 🚫 não | ✅ sim (D3) |
| U3 | não | — | não | 🚫 não (fora de escopo de caixa, como hoje) | 🚫 não (404, como hoje — `_inbox_hidden` intacto) |
| U4 (admin/read_all) | irrelevante | não | sim | ✅ sim | ✅ sim |

Conversa B (mesmo time, mas `restrict_visibility=0`) e conversa C (`team_id=None`): todos os membros da caixa veem, em qualquer linha da tabela acima.

---

## 6 — Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | Aplicar a cláusula em só 2 dos 3 pontos de listagem | `list_conversations` e `/filter` escondem a conversa mas `/count` ainda soma ela na aba "Todas" — contador e lista divergem | I4 é um item único cobrindo os 3 file:line, com teste da Fase 5 item 3 comparando os três |
| R2 | Esquecer o `if inbox_ids is not None:` e aplicar a cláusula sempre | Usuário com `conversation.read_all` deixaria de ver conversas de time restrito de que não é membro — quebra D5 e regride o comportório de admin | A cláusula fica DENTRO do braço existente, nunca um `if` novo e paralelo |
| R3 | `current_user_id=None` chegar ao braço que já tem `inbox_ids` não-`None` | `_team_visible_clause` com `current_user_id=None` faria `member_team_ids` vazio — qualquer time restrito ficaria invisível a UM usuário sem identidade que ainda assim tivesse `inbox_ids` (não deveria existir, mas...) | Confirmado por grep (§2.2) que isso nunca acontece nos call sites reais; o helper trata `None` explicitamente com uma subquery vazia em vez de estourar, então mesmo no caso teórico o resultado é "não vê", nunca uma exceção |
| R4 | Confundir esta mudança com D2 do plano 153 e "consertar" achando que D2 ficou errada | D2 permanece verdadeira como estava ESCRITA no momento (nenhuma visibilidade por time existia); este plano é uma decisão NOVA e posterior, não uma correção da 153 | Este arquivo cita D2 explicitamente em vez de reescrever o plano 153 — histórico preservado |
| R5 | Time com `restrict_visibility=1` e zero membros | Ninguém enxerga as conversas daquele time na listagem, nem quem criou (só quem tem `read_all`) | Comportamento esperado, simétrico a R6 do plano 153 (time vazio é permitido) — não é bug |

---

## 7 — Perguntas em aberto

**P1 — A cláusula fica em `conversation_repo.py` (repo) ou vira uma função nova em `server/authz.py`, simétrica a `visible_inbox_ids`?**
✅ **DECIDIDO nesta sessão (D6): fica no repo.**
Contexto: `visible_inbox_ids` devolve uma lista de ids porque a decisão "quais inboxes eu vejo" não depende de nenhuma coluna da própria conversa — é resolvida inteiramente a partir do usuário. Já "quais times me escondem uma conversa" depende do `team_id` **daquela linha específica** — não dá para pré-computar como lista de ids sem antes decidir por linha, o que É trabalho de query, não de política de usuário.
(a) **Repo (`_team_visible_clause`)** — a assinatura dos 3 pontos de listagem já recebe `current_user_id`; o helper vira só mais uma cláusula composta no `WHERE`, no mesmo espírito de `inbox_ids`.
(b) `authz.py` ganha `visible_team_ids(request) -> ...` — obrigaria a inventar uma forma de representar "visível seguindo uma regra por-linha" fora do SQL (ou fazer o JOIN dentro de `authz.py`, que hoje não importa `db.tables` nem monta `select()` nenhum — mudaria a natureza do módulo).
**Recomendação mantida: (a).**

**P2 — Uma linha nova no `CLAUDE.md`?**
⏸️ **RECOMENDADO: decidir na Fase 6, com orçamento medido** (mesmo processo do plano 153 P4).
(a) Sem linha nova — é uma extensão pequena de uma feature já documentada (Times).
(b) 1 linha ⚠️ perto da que o plano 153 já deixou, citando que a restrição só vale pra listagem — candidato a armadilha real ("por que o link ainda funciona?").
**Recomendação: (b), se sobrar orçamento.**

**P3 — A sidebar de quem está com a conversa aberta e perde a visibilidade (porque outro admin ligou a restrição enquanto ela olhava a lista) precisa de um evento ao vivo?**
⏸️ **FORA DE ESCOPO nesta entrega**, por não ter sido pedido e por não haver precedente de outro toggle de configuração (ex: `ai_history_exclude_patterns`) empurrando refresh ao vivo pra sidebar de terceiros. A lista se corrige no próximo fetch/reload — mesmo comportamento que qualquer outra mudança de escopo (ex: sair de um time) já tem hoje.

---

## 8 — Apêndice — arquivos-chave

**Backend / schema**
- Migration nova (`db/alembic/versions/20260910_0068_team_restrict_visibility.py`) + [db/tables.py:539-547](../db/tables.py#L539-L547).

**Backend / repositórios**
- [db/repositories/team_repo.py](../db/repositories/team_repo.py) — `create`/`update` ganham `restrict_visibility`.
- [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — import de `teams`/`team_members` (:19-21), `_team_visible_clause` (novo), aplicado em `list_conversations` (:540), `list_filtered` (:563), `count_tab_counts` (:609).

**Backend / rotas**
- [server/routes/teams.py](../server/routes/teams.py) — `create_team`/`update_team` repassam o campo.

**Frontend**
- [web/static/js/components/TeamsManager.js](../web/static/js/components/TeamsManager.js) — checkbox no `TeamCard` (:90-175), payload do Salvar (:161-168).

**Testes**
- [tests/integration/test_teams.py](../tests/integration/test_teams.py) — extensão (cenários do §5).

**Docs**
- `CLAUDE.md` — condicional (P2).

---

## 9 — Checklist de verificação

- [x] `alembic upgrade head` e `alembic downgrade -1` + upgrade de novo, limpos no Postgres de teste
- [x] `venv/bin/python -m pytest tests/integration/test_teams.py` verde, cobrindo os cenários do §5 (15 passed)
- [x] `venv/bin/python -m pytest tests/contracts` verde (nenhuma mudança na superfície de plugin — golden não mudou)
- [x] `venv/bin/python -m pytest` (core inteiro) — **investigado, não fica 100% verde, mas por causas PRÉ-EXISTENTES e sem relação com este plano** (ver nota abaixo). `test_teams.py` isolado continua 15/15; nenhuma falha aponta pra `_team_visible_clause`/`team_repo`/`teams.py`/`TeamsManager.js`.
- [ ] Reload manual: criar/editar um time, marcar "Restringir às conversas deste time", salvar, recarregar a página e confirmar que o checkbox permanece marcado — **pendente (teste de UI no navegador não executado nesta sessão)**
- [x] Reload manual: com o time restrito, logar como um atendente da mesma caixa que não é do time — a conversa some da lista mas abre normalmente por link direto (D3) — **coberto pelo teste automatizado equivalente** (`test_restrict_visibility_hides_from_listing_not_from_direct_access`)
- [x] Reload manual: usuário com `conversation.read_all` (ou admin) continua vendo tudo, restrito ou não — **coberto pelo teste automatizado equivalente**
- [ ] Modo escuro ligado: checkbox e texto de ajuda legíveis no editor de time — **pendente (teste de UI no navegador não executado nesta sessão; revisão de código usa só classes `wa-*`)**
- [x] Nenhum segredo em log/URL/payload (feature não envolve credencial)
- [x] `git status` limpo de WIP alheio nos arquivos tocados antes de começar (WIP do plano 153 em `db/tables.py`/`conversation_repo.py` confirmado e preservado — este plano só ACRESCENTOU código, não reverteu nada da frente em andamento)
