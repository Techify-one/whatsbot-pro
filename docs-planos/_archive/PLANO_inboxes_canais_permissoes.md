# Plano: Refatoração `channels` ↔ `inboxes` + Permissões por Caixa de Entrada

> **Status:** proposta para implementação (não implementado).
> **Autor do diagnóstico:** investigação no banco dev (Postgres) + leitura do código.
> **Objetivo:** corrigir 2 bugs reportados e deixar a arquitetura `channels`/`inboxes`/permissões sólida para evoluir.

---

## 1. Resumo dos bugs reportados

1. **Lista de "Caixas de entrada" na tela de Usuários mostra caixas antigas/inexistentes e com nome errado.**
   A tela lista a tabela `inboxes` inteira (inclui órfãs de canais já excluídos) e mostra o `inbox.name` (snapshot velho), não o `display_name` atual do canal.

2. **Remover um usuário de uma caixa não bloqueia leitura nem envio.**
   - Causa direta no caso do Thiago: o papel **"teste" concede `conversation.read_all`**, que faz `visible_inbox_ids()` retornar `None` ("vê tudo") — a participação por caixa é **ignorada**.
   - Causa estrutural: mesmo para usuários escopados, **o envio não verifica caixa** (só a leitura/listagem verifica). Endpoints de envio não chamam nenhuma checagem de inbox.

---

## 2. Modelo de dados atual

### Tabela `channels` (config de provider/transporte — plano 11/21)
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | TEXT (PK) | snake_case; p/ GOWA é o device id, senão `<provider>_<hex>` |
| `provider` | TEXT | `gowa` \| `whatsapp_cloud` \| `telegram` \| … |
| `display_name` | TEXT | **nome de exibição atual** (fonte de verdade do nome) |
| `enabled` | INTEGER | 1/0 |
| `gowa_device_id` | TEXT | só GOWA |
| `gowa_isolation` | TEXT | `shared` default |
| `config` | TEXT(JSON) | inclui sub-objeto `ai` (plano 21) e filtros JID |
| `connected` / `logged_in` | INTEGER | estado de conexão |
| `own_phone` | TEXT | número próprio |
| `last_error` | TEXT | |
| `created_at` / `updated_at` | DOUBLE | |

### Tabela `inboxes` (modelo estilo Chatwoot — hub de FKs)
| Coluna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER (PK) | **identidade referenciada por conversas/contatos/membros** |
| `name` | TEXT | ⚠️ snapshot do nome do canal na criação — **não sincronizado** |
| `channel_type` | TEXT | `whatsapp` default (legado; hoje quase sempre `whatsapp`) |
| `channel_id` | TEXT | aponta p/ `channels.id` — **SEM FK** |
| `agent_bot_enabled` | INTEGER | 1/0 (IA por caixa, legado) |
| `default_agent_key` | TEXT | roteamento de agente por caixa |
| `created_at` / `updated_at` | DOUBLE | |

### Quem depende de `inboxes.id` (por isso NÃO dá pra simplesmente apagar a tabela)
- `conversations.inbox_id` → FK `inboxes.id` (ON DELETE CASCADE)
- `contact_inboxes.inbox_id` → FK `inboxes.id` (ON DELETE CASCADE)
- `inbox_members.inbox_id` → FK `inboxes.id` (ON DELETE CASCADE)

> **Conclusão de arquitetura:** `inboxes` é o ponto central do modelo conversacional. `channels` é a camada de transporte/credenciais. Eles **devem ser 1:1** (um canal = uma caixa), mas hoje não há sincronização. A refatoração mantém as duas tabelas e **garante o invariante 1:1**.

### Estado real hoje (dev) — evidência do problema
`channels` (4): `default`→"WhatsApp", `telegram`→"Telegram", `teste`→"oficial", `teste2gowa`→"teste2gowa".
`inboxes` (7):
| id | name | channel_id | situação |
|---|---|---|---|
| 1 | WhatsApp | default | ok |
| 2 | Vendas | vendas | **órfã** (canal não existe) |
| 3 | teste numero oficial | teste | nome velho (canal é "oficial") |
| 4 | teste não oficial | whatsapp_teste | **órfã** |
| 5 | testee | testee | **órfã** |
| 6 | teste telegram | telegram | nome velho (canal é "Telegram") |
| 7 | teste2gowa | teste2gowa | ok |

---

## 3. Decisão de arquitetura

**Manter `channels` e `inboxes`, com `inboxes` como espelho 1:1 sincronizado de `channels`.**

Princípios:
1. **Fonte de verdade do nome = `channels.display_name`.** A UI exibe sempre o nome do canal, nunca o `inbox.name` cru. `inbox.name` passa a ser apenas cache/compat e é mantido em sincronia.
2. **Invariante 1:1 garantido pelo código + integridade referencial:**
   - criar canal → cria inbox;
   - renomear canal → renomeia inbox;
   - excluir canal → exclui inbox (cascateando conversas/contatos/membros, ou bloqueando — ver §4.3);
   - `inboxes.channel_id` ganha integridade (FK ou limpeza garantida).
3. **Permissão por caixa só faz sentido para quem NÃO tem `conversation.read_all`.** Documentar e corrigir os papéis.

### Alternativa descartada (registrar, não fazer agora)
**Fundir as duas tabelas** (canal vira a própria inbox) seria mais limpo conceitualmente, mas exige migrar todas as FKs `inbox_id` (INTEGER) para `channel_id` (TEXT) em `conversations`, `contact_inboxes`, `inbox_members`, `messages` indiretamente, etc. Alto risco, baixo retorno agora. **Não fazer.** O modelo 1:1 sincronizado entrega o mesmo resultado prático com risco baixo.

---

## 4. Mudanças no backend

### 4.1 Sincronizar nome na edição do canal (Bug 1 — nome velho)
**Arquivo:** [server/routes/channels.py](server/routes/channels.py) — `PUT /api/channels/{channel_id}` (~linha 244-253).
Quando `display_name` muda, atualizar a inbox correspondente:

```python
if fields:
    row = await asyncio.to_thread(channel_repo.update, channel_id, **fields)
    if "display_name" in fields:
        inbox = await asyncio.to_thread(inbox_repo.get_by_channel, channel_id)
        if inbox:
            await asyncio.to_thread(inbox_repo.update, inbox["id"],
                                    name=fields["display_name"] or channel_id)
```

> `inbox_repo.update` já aceita `name` (está em `_UPDATABLE`, [inbox_repo.py:19](db/repositories/inbox_repo.py#L19)).

### 4.2 Garantir inbox na criação (já existe, revisar)
`POST /api/channels` já chama `inbox_repo.get_or_create_for_channel(cid, name=display_name)` ([channels.py:220-225](server/routes/channels.py#L220)). Manter, mas trocar o `try/except: pass` silencioso por log de warning (uma falha aqui é a origem de inconsistência).

### 4.3 Excluir inbox junto com o canal (Bug 1 — caixas órfãs)
**Arquivo:** [server/routes/channels.py](server/routes/channels.py) — `DELETE /api/channels/{channel_id}` (~linha 319-342).
Após `channel_repo.delete`, remover a inbox do canal. **Decisão de produto necessária** (ver §7, pergunta A):

- **Opção 4.3-a (cascata):** excluir a inbox e deixar o FK `ON DELETE CASCADE` levar conversas/contatos/membros junto. Simples, mas **apaga histórico de conversas** daquele canal.
- **Opção 4.3-b (bloquear se houver conversas):** se a inbox tiver conversas, recusar a exclusão do canal (HTTP 409) e orientar arquivar. Preserva histórico.
- **Opção 4.3-c (soft-delete):** marcar canal/inbox como `archived` e esconder da UI sem apagar dados. Melhor a longo prazo; exige coluna nova.

Esboço (opção a):
```python
inbox = await asyncio.to_thread(inbox_repo.get_by_channel, channel_id)
ok = await asyncio.to_thread(channel_repo.delete, channel_id)
if inbox:
    await asyncio.to_thread(inbox_repo.delete, inbox["id"])  # FK CASCADE leva o resto
```
(É preciso **criar `inbox_repo.delete(inbox_id)`** — não existe hoje.)

### 4.4 Adicionar a função `inbox_repo.delete`
**Arquivo:** [db/repositories/inbox_repo.py](db/repositories/inbox_repo.py).
```python
from sqlalchemy import delete as sa_delete
def delete(inbox_id: int) -> bool:
    with get_engine().begin() as conn:
        res = conn.execute(sa_delete(inboxes).where(inboxes.c.id == inbox_id))
    return res.rowcount > 0
```

### 4.5 Integridade referencial `inboxes.channel_id` → `channels.id`
Hoje é `Column("channel_id", Text)` sem FK ([db/tables.py:331](db/tables.py)). Duas abordagens (ver §7, pergunta B):
- **Mínima (recomendada agora):** **não** adicionar FK (canais usam id TEXT; manter flexível p/ inboxes "sem canal" legadas), mas **garantir limpeza** via §4.3 + migração de limpeza (§6.1) + endpoint de listagem filtrando órfãs (§4.6).
- **Forte:** adicionar FK `channel_id → channels.id ON DELETE CASCADE`. Requer limpar órfãs **antes** (senão a migração falha) e aceitar que excluir canal apaga a inbox automaticamente. Combina com 4.3-a.

### 4.6 Endpoint que alimenta a tela de Usuários só deve listar caixas vivas, com nome do canal
**Arquivos:** [server/routes/users.py:48-63](server/routes/users.py#L48) (`GET /api/roles`) e [server/routes/inboxes.py:19](server/routes/inboxes.py#L19) (`GET /api/inboxes`).
Trocar `inbox_repo.list_all()` por uma listagem que faz **JOIN com `channels`**, retornando só inboxes com canal existente e o `display_name` atual:

```python
def list_with_channel() -> list[dict]:
    # SELECT i.id, COALESCE(c.display_name, i.name) AS name,
    #        i.channel_id, c.provider, c.enabled
    # FROM inboxes i JOIN channels c ON c.id = i.channel_id
    # ORDER BY c.display_name
```
- `JOIN` (não `LEFT JOIN`) já elimina as órfãs da lista.
- `name` retornado passa a ser o `display_name` do canal.
- Opcional: incluir `provider`/`enabled` p/ a UI mostrar badge e esconder canais desativados.

> Manter `list_all()` para usos internos; criar `list_with_channel()` novo para os endpoints de UI.

### 4.7 Enforcement de caixa no ENVIO (Bug 2 — estrutural)
Criar um helper único em [server/authz.py](server/authz.py):
```python
def can_access_inbox(request: Request, inbox_id: int | None) -> bool:
    vis = visible_inbox_ids(request)
    return vis is None or (inbox_id is not None and inbox_id in vis)
```
Aplicar nos caminhos de envio que hoje não checam:
- `POST /api/contacts/{phone}/send` — [contacts.py:313](server/routes/contacts.py#L313): resolver a inbox da conversa/canal alvo e, se `not can_access_inbox(...)`, retornar 403/404.
- `POST /api/conversations/{conv_id}/send-template` — [conversations.py:535](server/routes/conversations.py#L535): a conversa já tem `inbox_id`; checar antes de enviar (espelhar o `_inbox_hidden` usado na leitura).
- Revisar também outras ações mutadoras por conversa (assumir, resolver, atribuir, reagir, deletar msg) — garantir que todas passem por `_inbox_hidden`/`can_access_inbox`. Várias rotas de leitura em [conversations.py](server/routes/conversations.py) já usam `_inbox_hidden` (linhas ~120/195/217/433); usar o mesmo padrão nas de escrita.

---

## 5. Permissões — decisão e correção

### 5.1 Semântica de `conversation.read_all` vs caixa
Hoje (correto por design): `visible_inbox_ids()` retorna `None` (vê tudo) se o usuário tem `conversation.read_all` ([authz.py:45](server/authz.py#L45)). Ou seja, **a participação por caixa só restringe quem NÃO tem `read_all`.**

**Problema atual:** o papel **"teste" (role_id 4)** concede `conversation.read_all` — por isso o Thiago vê tudo independentemente da caixa. Permissões do papel "teste" hoje: `contact.read`, `conversation.assign`, `conversation.read`, **`conversation.read_all`**, `conversation.reply`, `quickreply.manage`, `users.manage`.

**Ações (ver §7, pergunta C):**
1. **Correção de dados:** revisar quais papéis devem ter `read_all`. Provavelmente só `admin`/`gestor`/`supervisor`. Remover de `atendente` e de papéis de teste como "teste". (Isso é config no banco — `role_permissions` — feito pela tela de Grupos de permissão ou seed.)
2. **Garantir que `atendente` (e papéis escopados) NÃO tenham `read_all`** no seed padrão. Conferir o seed RBAC (procurar onde `role_permissions` é populado na migração/bootstrap).
3. **Decisão de produto:** `read_all` deve realmente furar o escopo por caixa? Recomendado **sim** (admin/gestão precisa ver tudo) — então a regra atual está certa e o conserto é só de dados/seed. Documentar isso na UI ("Administradores veem todas, independente da seleção" já aparece na tela de Usuários — estender o texto para qualquer papel com `read_all`).

### 5.2 Enforcement no envio
Ver §4.7 — sem isso, um usuário escopado (sem `read_all`) ainda conseguiria enviar para caixas que não vê.

---

## 6. Migrations (Alembic)

### 6.1 Limpeza de inboxes órfãs (one-shot)
Nova revisão Alembic (linear a partir da head atual). `upgrade()`:
1. Apagar `inbox_members` de inboxes órfãs.
2. Apagar `contact_inboxes`/`conversations` órfãs **somente se** a decisão §4.3 for cascata; caso contrário, **reparentar/avisar** (ver pergunta A).
3. Apagar as `inboxes` cujo `channel_id` não existe em `channels` (e `channel_id IS NOT NULL`).
4. Sincronizar `inbox.name = channels.display_name` para todas as inboxes vivas (corrige nomes velhos como "teste telegram" → "Telegram").

```sql
-- exemplo (revisar à luz da decisão de cascata):
UPDATE inboxes i SET name = c.display_name
  FROM channels c WHERE c.id = i.channel_id;
DELETE FROM inboxes
  WHERE channel_id IS NOT NULL
    AND channel_id NOT IN (SELECT id FROM channels);
```
> Rodar `alembic revision -m "cleanup orphan inboxes + sync names"` e escrever o upgrade idempotente. Validar em SQLite **e** Postgres (sintaxe de UPDATE...FROM difere — usar SQLAlchemy Core ou dialetos).

### 6.2 (Opcional) FK `inboxes.channel_id`
Se adotar §4.5 forte: nova revisão adicionando a FK com `ON DELETE CASCADE`, **depois** da limpeza 6.1. Em SQLite, FK nova exige `batch_alter_table`.

---

## 7. Decisões de produto a confirmar antes de implementar

- **A) Excluir um canal deve apagar o histórico de conversas daquela caixa?**
  (a) cascata apaga tudo · (b) bloquear exclusão se houver conversas · (c) soft-delete/arquivar. **Recomendo (c)** a médio prazo; (b) como passo seguro imediato.
- **B) Adicionar FK `inboxes.channel_id → channels.id`?**
  Recomendo **não agora** (manter limpeza por código + JOIN na listagem); reavaliar com o soft-delete.
- **C) Quais papéis mantêm `conversation.read_all`?**
  Recomendo: `admin`, `gestor`, `supervisor` sim; `atendente` e papéis de teste **não**. Confirmar.

---

## 8. Frontend

### 8.1 Tela de Usuários — lista de caixas
**Arquivo:** [web/static/js/components/UsersManager.js](web/static/js/components/UsersManager.js) (~linhas 163-178, 245-250).
- A lista passa a vir já filtrada (só caixas vivas) e com `name = display_name` do canal, vindo de `GET /api/roles`/`GET /api/inboxes` ajustados em §4.6. Nenhuma mudança de payload de salvamento (`inbox_ids` continua igual).
- Opcional: mostrar badge do provider (GOWA/Cloud/Telegram) ao lado do nome, p/ casar visualmente com a tela de Canais.

### 8.2 Texto de ajuda sobre escopo
Atualizar o texto "Administradores veem todas, independente da seleção" para refletir a regra real: **qualquer papel com `read_all`** ignora a seleção de caixas. (Idealmente, a UI desabilita/avisa o picker de caixas quando o papel selecionado tem `read_all`.)

---

## 9. Testes (tests/test_endpoints.py)

Adicionar cobertura:
1. **Sync de nome:** criar canal → editar `display_name` → `GET /api/inboxes` reflete o novo nome.
2. **Exclusão:** excluir canal → a inbox some de `GET /api/inboxes`/`GET /api/roles` (e, conforme decisão A, conversas tratadas).
3. **Listagem sem órfãs:** seed com inbox órfã → endpoint de UI não a retorna.
4. **Escopo de leitura:** usuário sem `read_all`, membro só da inbox X, não vê conversas da inbox Y (já parcialmente coberto).
5. **Escopo de envio (novo):** usuário sem `read_all`, não-membro da inbox da conversa → `POST /send` e `POST /send-template` retornam 403/404.
6. **`read_all` fura escopo:** usuário com `read_all` envia/lê em qualquer caixa mesmo sem membership.
7. **Migração de limpeza:** teste de migração apagando órfãs e sincronizando nomes (se houver harness de migração; senão, teste de repo).

---

## 10. Ordem de implementação sugerida

1. **(dados, rápido)** Corrigir papéis: remover `conversation.read_all` de "teste"/"atendente" no seed e no banco dev (§5.1). — Resolve o sintoma imediato do Thiago.
2. `inbox_repo.delete()` + `inbox_repo.list_with_channel()` (§4.4, §4.6).
3. Sync de nome no `PUT /channels` e exclusão de inbox no `DELETE /channels` (§4.1, §4.3).
4. Migração de limpeza de órfãs + sync de nomes (§6.1).
5. Endpoints de UI usando `list_with_channel` + frontend exibindo `display_name` (§4.6, §8).
6. Helper `can_access_inbox` + enforcement nos envios e ações mutadoras (§4.7, §5.2).
7. (Opcional) FK e/ou soft-delete conforme decisões A/B (§4.5, §6.2).
8. Testes (§9).

---

## 11. Riscos e cuidados

- **Postgres + SQLite:** dev usa Postgres; prod pode usar SQLite. Toda SQL de migração precisa funcionar nos dois (preferir SQLAlchemy Core a SQL cru; `UPDATE ... FROM` não existe em SQLite). Rodar a suíte com `WHATSBOT_TEST_DB_URL` apontando p/ ambos.
- **Apagar dados:** a decisão A pode destruir histórico — confirmar com o usuário antes de qualquer cascata. Fazer backup do banco dev antes de rodar a migração de limpeza.
- **`DEFAULT_INBOX_ID = 1`:** [agent/memory.py](agent/memory.py) cai na inbox 1 quando a resolução falha. Garantir que a inbox 1 (canal `default`) nunca seja excluída (o `DELETE` já bloqueia o canal `default`).
- **Cache de inbox:** `_INBOX_BY_CHANNEL` em [agent/memory.py:16](agent/memory.py#L16) tem vida de processo; após mudanças de inbox, um restart limpa. Não cachear id de inbox excluída.
- **Não renomear identidades:** `inbox.id`, `channels.id` e chaves de permissão são identidade — não mudar.
