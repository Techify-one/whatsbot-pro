# Plano de Implementação — Caixa de Entrada e Conversas (WhatsBot Pro)

> **Status:** PLANO acionável. Deriva da pesquisa em
> [`docs-pesquisa/01-inbox-e-conversas.md`](../docs-pesquisa/01-inbox-e-conversas.md).
> **Tenancy:** uma empresa, servidor único, multi-usuário, **sem multi-tenant** (nenhuma
> tabela ganha `account_id`).
> **Modelo escolhido (decisão do cliente, 2026-06-18):** Chatwoot de 3 níveis —
> **Contact (a pessoa) → ContactInbox (`source_id` por inbox) → Conversation (várias por
> contato)**. **Schema nasce no formato final; UI simplifica no MVP** (uma linha por conversa
> ativa derivada).
>
> **Escopo deste plano:** tabelas `contact_inboxes` + `conversations`, reescopo semântico de
> `contacts`, coluna `messages.conversation_id`, migration/backfill idempotente a partir da
> `contacts` atual, máquina de estados **`open`/`closed (resolved)`** (P3 — sem `pending`/`snoozed` no
> MVP), atribuição (`assignee_user_id`) + fila de não-atribuídas, **cascata de IA global → inbox →
> conversa** (P5 — `conversations.ai_active` é o gate por conversa; `contacts.ai_enabled` SAI do gate),
> reescrita do webhook/handler para operar por conversa, endpoints `GET/PATCH /api/conversations`,
> eventos WS, eventos do bus de plugins, e a tela de lista de conversas no frontend.
>
> **Decisões aplicadas (DECISOES.md, fonte da verdade):** P1 (stubs sem FK), P2 (sempre reabrir a
> mesma conversa), P3 (só `open`/`closed`), P4 (nasce `open` na fila), **P5 — cascata
> global→inbox→conversa, SEM nível de contato; o toggle age na conversa**, P6 (`display_id` global via
> tabela-contador `UPDATE…RETURNING`), P7 (auto-resolução off), P8 (grupos = conversa normal, VISÍVEIS
> com badge de grupo), P9 (visibilidade por membership de inbox), P10 (archive ortogonal ao status),
> P11 (merge fora do MVP), P12 (`source_id` = JID + LID, guardar ambos), P18 (índice único
> `(channel_id, external_msg_id)`).

---

## 0. Estado atual (pontos de integração reais, confirmados no código)

- **`db/tables.py:41-65`** — `contacts` com `phone` UNIQUE (chave de negócio fundida pessoa+canal),
  `ai_enabled` (`:51`), flags ad-hoc `is_archived/is_pinned/unread_count/has_unread_mention`.
- **`db/tables.py:79-96`** — `messages` com FK `contact_id`; **sem** `conversation_id`. Índices
  `idx_msg_contact_ts`, `idx_msg_id`.
- **`db/tables.py:144-168`** — `executions`/`execution_steps`: ciclo de UMA resposta da IA, NÃO a
  conversa de atendimento. Não confundir nem reaproveitar.
- **`db/tables.py:207`** — `CORE_TABLES = frozenset(t.name for t in metadata.sorted_tables)` —
  derivada do metadata; tabelas novas entram automaticamente (a migração SQLite→Postgres já as
  cobre).
- **`db/alembic/versions/`** — última revisão `20260603_0006_contact_mention.py`. Padrão de
  migration: `revision`/`down_revision` string, `upgrade()`/`downgrade()` com `op.add_column` etc.
- **`db/repositories/contact_repo.py:49`** — `get_or_create(phone, default_ai_enabled)`: resolve por
  variantes BR de telefone (`_br_phone_variants`, `:31`), cria a linha. É o ponto onde hoje "nasce" a
  unidade de trabalho.
- **`db/repositories/message_repo.py:14`** — `add(contact_id, role, content, ...)`; todas as queries
  filtram por `contact_id`. **Não há** `conversation_id` em lugar nenhum.
- **`agent/memory.py:66-193`** — `ContactMemory`: carrega `ai_enabled` (`:87`), persiste via
  `set_ai_enabled` (`:191-193`) → `contact_repo.update(ai_enabled=...)`.
- **`agent/tools/transfer_to_human.py:51-53`** — handoff atual: `ctx.contact.set_ai_enabled(False)` +
  tag `transferido_atendente`. **Não atribui a ninguém.**
- **`server/routes/webhook.py`** — gate da IA em 3 sites: `:834`, `:860`, `:966`
  (`if contact.ai_enabled and settings.get("auto_reply", True)`). Alerta de handoff
  `human_transfer_alert` emitido em `:617-622`.
- **`server/routes/contacts.py`** — `POST /api/contacts/{phone}/toggle-ai` (vide pesquisa
  `:929-938`).
- **`server/state.py:61`** — `ConnectionManager.broadcast(event, data)`. Nenhum evento de conversa
  hoje.
- **`web/static/js/components/contacts/Contacts.js`** — raiz da sidebar; filtro só por `showArchived`
  (`:28`), `handleToggleAI` (`:66-76`).

**Conclusão:** hoje "1 contato = 1 thread infinita + 1 bit `ai_enabled`". Precisamos introduzir
ContactInbox + Conversation **sem quebrar o histórico** (mantendo `contact_id` em `messages` e
`phone` em `contacts` por compat durante a transição).

---

## Dependências de outros planos

| Precisa estar pronto | De onde vem | Por quê | Mitigação se ausente |
|---|---|---|---|
| Tabela **`inboxes`** + inbox WhatsApp default | [`02-canais-e-providers`] | `contact_inboxes.inbox_id` e `conversations.inbox_id` referenciam `inboxes(id)` | **Stub local**: criar `inboxes` mínima neste plano (Fase 1a) e marcar como "owned" pelo doc 02 quando ele chegar (ver §1.1 e Perguntas q1). |
| Tabela **`users`** + sessão com `current_user` | [`03-rbac-usuarios`] (PLANO já existe) | `conversations.assignee_user_id` FK em `users(id)`; "atribuir a mim" precisa de `current_user` | `assignee_user_id` fica NULLABLE; endpoints de atribuição retornam 501/no-op até `users` existir. Status funciona sem usuários. |
| Tabela **`teams`** | [`03-rbac-usuarios`] (fase 2 de lá) | `conversations.team_id` opcional | Coluna NULLABLE; ignorada no MVP. |
| `custom_attributes` (contrato JSON) | [`05-atributos-personalizados`] | Coluna `custom_attributes` em `contacts` e `conversations` | Criar a coluna agora (`TEXT DEFAULT '{}'`); o doc 05 só define o schema lógico. |
| Saved views / filtros | [`08-filtros`] | Abas de status/assignee na UI | MVP usa filtros fixos (query params); saved views são fase posterior. |

**Ordem recomendada:** doc 02 (inbox) → doc 03 (users) → **este plano**. Se 02/03 atrasarem, a
Fase 1a deste plano cria stubs mínimos para destravar o schema de conversas.

---

## 1. Modelo de dados

### 1.1 Stub `inboxes` (Fase 1a — só se o doc 02 não chegou antes)

Tabela mínima para destravar as FKs. Quando o doc 02 entregar a versão completa, ele faz
`ALTER TABLE` aditivo (provider config etc.) — **não** recria.

```python
# db/tables.py  (ilustrativo)
inboxes = Table(
    "inboxes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False, server_default="WhatsApp"),
    Column("channel_type", Text, nullable=False, server_default="whatsapp"),  # provider plugin id
    Column("agent_bot_enabled", Integer, nullable=False, server_default="1"), # gate IA por inbox
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
```

A inbox default (`id` conhecido, ex.: 1) é semeada na própria migration de backfill.

### 1.2 `contacts` reescopado (a PESSOA)

- **Manter a tabela** e todas as colunas existentes (zero churn de leitura).
- **Remover o UNIQUE de `phone`** (`db/tables.py:45`) — `phone` vira atributo/telefone-primário
  exibível, **não** mais a identidade-no-canal. A identidade migra para `contact_inboxes.source_id`.
  > **Nota SQLite:** SQLite não dropa constraint via `ALTER`; remover o unique exige
  > **batch_alter_table** do Alembic (recria a tabela). Postgres usa `DROP CONSTRAINT`. Ver §2.1.
- **Adicionar** `custom_attributes TEXT NOT NULL DEFAULT '{}'` (contrato do doc 05).
- As flags ad-hoc (`is_archived/is_pinned/unread_count/has_unread_mention/can_send`) **permanecem no
  contato** na Fase 1 (transição); migram para a conversa na Fase 2 (ver Perguntas q10).
  - **`is_archived` (P10):** archive é **ortogonal ao status**. No backfill ele NÃO vira `resolved`;
    a conversa nasce `open` (P4) independentemente de `is_archived`. A flag de arquivo migra para a
    conversa como `conversations.is_archived` (coluna independente do `status`).
- **`contacts.ai_enabled` (P5 — MUDANÇA):** **sai do gate de IA.** Não é mais consultado na cascata
  nem usado como "default para novas conversas". Fica como coluna **aposentada/ignorada** (mantida
  fisicamente por compat de schema durante a transição; o `toggle-ai` deixa de escrevê-la — ver §5).

### 1.3 `contact_inboxes` (a IDENTIDADE da pessoa numa inbox minha)

```python
contact_inboxes = Table(
    "contact_inboxes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("inbox_id", Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False),
    Column("source_id", Text, nullable=False),   # chave de resolução (= JID; ver P12 abaixo)
    Column("source_jid", Text),                  # JID estável (...@s.whatsapp.net / ...@g.us)
    Column("source_lid", Text),                  # LID (multi-device) quando disponível
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
Index("uq_contact_inbox_inbox_source", contact_inboxes.c.inbox_id, contact_inboxes.c.source_id, unique=True)
Index("idx_contact_inbox_contact", contact_inboxes.c.contact_id)
Index("idx_contact_inbox_lid", contact_inboxes.c.inbox_id, contact_inboxes.c.source_lid)
```

`[inbox_id, source_id]` UNIQUE é a chave de resolução do remetente (espelha Chatwoot).

**`source_id` = JID + LID (P12, estilo Evolution — DECIDIDO):** guardamos **ambos**. `source_jid`
(ex.: `5511999999999@s.whatsapp.net`) é a identidade estável e vira o `source_id` (chave de
resolução); `source_lid` guarda o LID quando o WhatsApp o emitir (multi-device). A resolução do
remetente no webhook (§3.4) tenta casar por `source_id`/`source_jid` **e** por `source_lid` (índice
`idx_contact_inbox_lid`), igual ao `group_mentions` que já indexa por dígitos de phone **e** de lid.
O `phone` legível continua em `contacts.phone` (atributo, não-único). Merge de contatos com telefones
diferentes da mesma pessoa fica **fora do MVP** (P11) — o schema já deixa o caminho aberto.

### 1.4 `conversations` (a THREAD de atendimento)

```python
conversations = Table(
    "conversations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("display_id", Integer, nullable=False),   # id legível global via tabela-contador (P6, §1.6)
    Column("inbox_id", Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False),
    Column("contact_id", Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    Column("contact_inbox_id", Integer, ForeignKey("contact_inboxes.id", ondelete="CASCADE"), nullable=False),
    Column("status", Text, nullable=False, server_default="open"),     # P3: SÓ open|closed (closed = "resolved")
    Column("is_archived", Integer, nullable=False, server_default="0"),# P10: archive ORTOGONAL ao status
    Column("assignee_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),  # nullable até doc 03
    Column("team_id", Integer),                       # FK p/ teams quando existir (doc 03 fase 2)
    Column("priority", Text),                         # low|medium|high|urgent | NULL (fase 2)
    Column("ai_active", Integer, nullable=False, server_default="1"),  # gate IA por conversa (P5)
    Column("opened_at", Float, nullable=False),
    Column("resolved_at", Float),                     # ts em que virou closed
    Column("waiting_since", Float),                   # última msg do contato aguardando resposta
    Column("last_activity_at", Float, nullable=False),
    Column("custom_attributes", Text, nullable=False, server_default="{}"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
Index("idx_conv_inbox_status", conversations.c.inbox_id, conversations.c.status)
Index("idx_conv_assignee_status", conversations.c.assignee_user_id, conversations.c.status)
Index("idx_conv_contact", conversations.c.contact_id)
Index("idx_conv_contact_inbox", conversations.c.contact_inbox_id)
Index("idx_conv_last_activity", conversations.c.last_activity_at)
Index("idx_conv_archived", conversations.c.is_archived)
Index("uq_conv_display_id", conversations.c.display_id, unique=True)
```

> **`status` só `open`/`closed` (P3).** `closed` é o "resolved". Não há `pending`/`snoozed` no MVP
> (ficam para o futuro quando entrar o estado "aguardando"). A conversa **nasce `open`** (P4) e entra
> na fila; o indicador de "IA ativa" (`ai_active`) mostra que o robô está atendendo.
> **`is_archived` é ortogonal (P10):** dá para arquivar uma conversa **aberta**; arquivar NÃO muda o
> `status`. Filtrar a lista por `is_archived` é dimensão independente do `status`.
>
> **Sem** índice de unicidade sobre conversas ativas — múltiplas conversas por contact_inbox é o
> modelo final. "Conversa atual" é derivada por query (§3.2).
>
> **FK `assignee_user_id → users(id)`:** se `users` ainda não existir (doc 03 não aplicado), criar a
> coluna **sem** a FK na Fase 1 e adicionar a FK numa migration posterior (evita falha de ordem de
> migrations). Ver Perguntas q1.

### 1.5 `messages.conversation_id`

```python
# adicionar em messages (db/tables.py:79)
Column("conversation_id", Integer, ForeignKey("conversations.id", ondelete="CASCADE")),  # nullable
Index("idx_msg_conversation_ts", messages.c.conversation_id, messages.c.ts)
```

- **Manter `contact_id`** em `messages` (todo o `message_repo` continua funcionando). `conversation_id`
  é aditivo e nullable; é populado no backfill e em todo novo INSERT.

### 1.6 `display_id` sequencial concorrência-safe — **tabela-contador (P6, DECIDIDO)**

`display_id` é o número legível da conversa, **global por conta** (não por inbox — alinha com
Chatwoot/Zendesk/Intercom). Estratégia **decidida (P6): tabela-contador**, a única portável
SQLite+Postgres com o mesmo código e sem o race do `MAX()+1`:

```python
conversation_counters = Table(
    "conversation_counters", metadata,
    Column("name", Text, primary_key=True),     # ex.: "conversation_display_id"
    Column("next_value", Integer, nullable=False, server_default="1"),
)
```

Alocação atômica dentro da MESMA transação que cria a conversa (`get_engine().begin()`):

```sql
-- Postgres: UPDATE ... RETURNING (1 round-trip atômico)
UPDATE conversation_counters
   SET next_value = next_value + 1
 WHERE name = 'conversation_display_id'
RETURNING next_value - 1 AS display_id;

-- SQLite: UPDATE seguido de SELECT na mesma transação write (serializada por busy_timeout=5000)
UPDATE conversation_counters SET next_value = next_value + 1 WHERE name = 'conversation_display_id';
SELECT next_value - 1 AS display_id FROM conversation_counters WHERE name = 'conversation_display_id';
```

> **Por que não `MAX()+1`?** A pesquisa confirmou: Chatwoot usa SEQUENCE-por-conta (Postgres-only via
> trigger); como precisamos do **mesmo código em SQLite + Postgres**, a tabela-contador é a opção
> portável e concorrência-safe. `UPDATE…RETURNING` no Postgres é 1 round-trip; em SQLite o par
> UPDATE+SELECT roda dentro da transação write serializada. A linha-semente
> `('conversation_display_id', N)` é criada na migration (N = `MAX(display_id)+1` após o backfill, ou 1
> num DB vazio). Se um dia o backend for só Postgres, dá para trocar por `SEQUENCE` sem mudar a API do
> repo — mas **não é necessário**.

### 1.7 `CORE_TABLES`

Nada a fazer: `db/tables.py:207` deriva do metadata; as 4 tabelas novas (`inboxes`,
`contact_inboxes`, `conversations`, `conversation_counters`) entram automaticamente na migração
SQLite→Postgres.

**Critério de pronto (Fase 1 schema):** `alembic upgrade head` cria as tabelas num SQLite vazio e
num Postgres vazio; `python tests/test_endpoints.py` continua passando; `CORE_TABLES` inclui as 4
tabelas novas.

---

## 2. Migrations Alembic

### 2.1 `20260619_0007_inbox_conversations.py` — schema novo

`upgrade()`:
1. `op.create_table("inboxes", ...)` (se doc 02 não criou; senão pular — checar via inspector).
2. `op.create_table("contact_inboxes", ...)` + unique index `uq_contact_inbox_inbox_source`.
3. `op.create_table("conversations", ...)` + índices.
4. `op.add_column("messages", conversation_id)` + `op.create_index("idx_msg_conversation_ts", ...)`.
5. **Remover unique de `contacts.phone`**: usar
   `with op.batch_alter_table("contacts") as batch:` (recria a tabela no SQLite, in-place no
   Postgres) — dropar o unique e re-adicionar `phone` como índice **não-único**
   `idx_contacts_phone` (lookup ainda rápido).
6. `op.add_column("contacts", custom_attributes TEXT NOT NULL server_default "{}")`.
7. `op.create_table("conversation_counters", ...)` (P6 — tabela-contador do `display_id`). A
   linha-semente `('conversation_display_id', 1)` é inserida aqui; a migration de backfill (0008)
   reposiciona `next_value` para `MAX(display_id)+1` ao final.

> **Idempotência das migrations (P82 — encadeamento linear):** cada revisão aponta para o head real no
> momento de implementar; sem branches. Antes de criar `inboxes`/`conversation_counters`, checar com o
> inspector se a tabela já existe (caso o doc 02 a tenha criado) para não duplicar.

`downgrade()`: inverso (drop tabelas, drop coluna, restaurar unique de phone via batch).

### 2.2 `20260619_0008_backfill_conversations.py` — backfill de dados (data migration)

Idempotente e não-destrutiva. Roda só se `conversations` estiver vazia (guard).

Pseudocódigo:
```python
def upgrade():
    conn = op.get_bind()
    now = time.time()
    # 1) garantir inbox default
    inbox_id = _ensure_default_inbox(conn, now)   # INSERT se não houver linha; retorna id
    next_display = 1
    # 2) para cada contact existente (INCLUI grupos — P8: grupo vira conversa normal)
    for c in conn.execute(sa.text(
            "SELECT id, phone, is_archived, is_group, updated_at FROM contacts")):
        # 2a) contact_inbox 1:1.
        #     source_id = JID (P12). No backfill derivamos o JID a partir do phone:
        #       grupo  -> f"{phone}@g.us"
        #       pessoa -> f"{phone}@s.whatsapp.net"
        #     (o número cru fica em contacts.phone; o LID é desconhecido no histórico -> source_lid=NULL,
        #      preenchido on-the-fly quando o webhook trouxer o LID daquele contato).
        jid = f"{c.phone}@g.us" if c.is_group else f"{c.phone}@s.whatsapp.net"
        cib_id = upsert contact_inboxes (contact_id=c.id, inbox_id, source_id=jid,
                                         source_jid=jid, source_lid=None)
        # 2b) última atividade = MAX(ts) das mensagens, fallback updated_at
        last_ts = SELECT MAX(ts) FROM messages WHERE contact_id=c.id  OR c.updated_at
        # P4 + P10: conversa NASCE 'open' independentemente de is_archived.
        #           is_archived é ORTOGONAL ao status (não vira 'closed').
        status      = "open"
        is_archived = c.is_archived
        display_id  = next_display; next_display += 1   # global; o counter é reposicionado no passo 3
        conv_id = INSERT conversations (
            display_id, inbox_id, contact_id=c.id, contact_inbox_id=cib_id,
            status="open", is_archived=is_archived,
            ai_active=1,                       # P5: ai_enabled SAI do gate; default ligado
            opened_at=last_ts, resolved_at=None,
            last_activity_at=last_ts, created_at=now, updated_at=now)
        # 2c) ligar mensagens em massa
        UPDATE messages SET conversation_id=conv_id WHERE contact_id=c.id
    # 3) reposicionar o contador de display_id (P6) para o próximo valor livre
    UPDATE conversation_counters SET next_value=:next_display
     WHERE name='conversation_display_id'
```

Notas:
- **Grupos (`is_group=1`) — P8:** viram **conversa normal** e **aparecem nas filas** (NÃO ocultar). A
  UI marca com **badge de grupo**. `assignee`/`closed` funcionam igual.
- **`source_id` = JID (P12):** derivado do `phone` no backfill (`@g.us` para grupo,
  `@s.whatsapp.net` para pessoa). O `source_lid` fica `NULL` no histórico e é preenchido quando o
  webhook trouxer o LID. Ajustar o webhook (§4.1) para resolver por JID **e** LID na mesma virada.
- **`ai_active=1` no backfill (P5):** `contacts.ai_enabled` saiu do gate, então não é mais lido aqui;
  toda conversa migrada nasce com a IA ligada (o gate efetivo é global→inbox→conversa).
- **`is_archived` ortogonal (P10):** copiado de `contacts.is_archived`, sem virar `closed`.
- Rodar `UPDATE messages` por contato (não um JOIN gigante) para previsibilidade e progresso.

**Critério de pronto (migrations):** num DB de produção clonado (cópia do `whatsbot.db`), após
`alembic upgrade head`: (a) `COUNT(conversations) == COUNT(contacts)`; (b)
`COUNT(messages WHERE conversation_id IS NULL) == 0`; (c) cada `contact_inbox` único por
`[inbox_id, source_id]`; (d) re-rodar o upgrade não duplica nada (guard idempotente).

---

## 3. Camada de repositórios (data access)

Seguir o padrão Core do projeto (`with get_engine().begin()/connect()`, statements de `db/tables`).

### 3.1 `db/repositories/contact_inbox_repo.py` (novo)
- `resolve_or_create(inbox_id, source_id, contact_defaults) -> dict` — SELECT por
  `[inbox_id, source_id]`; se não existir, cria `contact` (ou liga a um existente — ver §3.2) +
  `contact_inbox`. **Ponto central** que substitui o `contact_repo.get_or_create(phone)` do webhook.
- `get_by_id(id)`, `list_by_contact(contact_id)`.

### 3.2 `db/repositories/conversation_repo.py` (novo)
- `create(inbox_id, contact_id, contact_inbox_id, ai_active) -> dict` — aloca `display_id`
  (§1.6, tabela-contador) na mesma transação; nasce `status='open'` (P4).
- `get_last_for_contact_inbox(contact_inbox_id) -> dict | None` — a query derivada:
  `WHERE contact_inbox_id=:cib ORDER BY last_activity_at DESC LIMIT 1`. (Há **uma** thread por
  contact_inbox no modelo MVP — P2 sempre reabre a mesma.)
- `resolve_inbound(inbox_id, source_id_or_lid) -> dict` — **regra P2: SEMPRE reabrir a mesma
  conversa.** Resolve o `contact_inbox` (por `source_id`/`source_jid` ou `source_lid`) → pega a última
  conversa daquele contact_inbox → se ela está `closed`, **reabre** (`status='open'`,
  `resolved_at=NULL`, emite `conversation.reopened`); se não existe nenhuma, **cria** uma `open`.
  **Não** cria nova conversa quando o cliente volta a falar (sem janela de tempo). Esta é a função que
  o webhook chama por mensagem.
- `update_status(id, status, by_user_id=None)` — só aceita `open`/`closed` (P3); rejeita o resto.
- `set_archived(id, archived, by_user_id=None)` — flag ortogonal ao status (P10).
- `set_assignee(id, user_id, by_user_id=None)`, `set_ai_active(id, active)`,
  `touch_activity(id, ts, waiting_since=None)`.
- `list(filters) -> list[dict]` — filtros: `status` (`open`/`closed`), `is_archived`,
  `assignee` (`me`/`unassigned`/`all`/id), `inbox_id`, paginação por `last_activity_at`. Join com
  `contacts` (nome/phone/avatar/`is_group` p/ badge — P8) e `users` (nome do assignee).
  **Visibilidade por membership de inbox (P9):** o `list` filtra pelas inboxes em que o `current_user`
  é membro (`inbox_members`, do doc 03); fora delas, não retorna nada. `conversation.read_all`
  (admin/gestor) ignora o filtro de membership.
- `count_unassigned()`, `get_full(id)` (com contato + última mensagem).

### 3.3 `db/repositories/message_repo.py` (editar)
- `add(...)` ganha kwarg **opcional** `conversation_id` (`message_repo.py:14`). Default `None` mantém
  compat; o webhook passa o valor resolvido.
- Novos helpers de leitura por conversa (`get_context_by_conversation`, `get_all_by_conversation`) —
  **aditivos**, não substituem os por `contact_id` na Fase 1.

### 3.4 `agent/memory.py` (editar)
- `ContactMemory` ganha awareness de `conversation_id` e `ai_active`. **P5:** `set_ai_enabled` deixa de
  escrever `contacts.ai_enabled` e passa a operar **só** `conversation_repo.set_ai_active(conv_id, ...)`
  — o toggle age na **conversa**. `contacts.ai_enabled` sai do gate (não é mais lido como default). Ver
  §5 (cascata de IA global→inbox→conversa).

**Critério de pronto (repos):** testes unitários novos em `tests/` cobrindo `resolve_inbound`
(criação, reabertura dentro da janela, nova conversa fora da janela, múltiplas conversas) num SQLite
temporário.

---

## 4. Webhook e handler operando por conversa

### 4.1 `server/routes/webhook.py` (editar — o coração da mudança)

Hoje o webhook resolve por `contact_repo.get_or_create(phone)` e checa `contact.ai_enabled` em 3
sites (`:834`, `:860`, `:966`). Mudanças:

1. **Resolução do remetente (P12)** → trocar `get_or_create(phone)` por
   `conversation_repo.resolve_inbound(inbox_id=INBOX_WA, source_id_or_lid=<JID e/ou LID do payload>)`.
   Passar **o JID** (`sender_jid`/`chat_id` do payload GOWA) como `source_id`/`source_jid` e o **LID**
   quando o payload trouxer (preenche `source_lid` na 1ª vez). Retorna
   `{conversation, contact_inbox, contact}`. Aplica P2 (sempre reabrir a mesma conversa). No MVP
   `INBOX_WA` é a inbox default fixa; multi-inbox vem do doc 02.
2. **Salvar a mensagem** com `conversation_id` em todos os sites de save inbound (batch_text,
   batch_media, group_no_mention — os 3 sites citados no CLAUDE.md).
   - **Idempotência (P18):** o INSERT de mensagem respeita o índice único
     `(channel_id, external_msg_id)` (ver doc 02 para a coluna `channel_id`; no MVP single-channel use
     a inbox default + `msg_id` do GOWA como `external_msg_id`). Webhook duplicado não duplica linha.
3. **Gate da IA (P5 — cascata global → inbox → conversa, SEM nível de contato)** nos 3 sites:
   substituir `contact.ai_enabled and settings.auto_reply` por:
   ```
   ia_responde = settings.get("ai_global_enabled", True)   # NÍVEL 1: global (IA da conta toda)
                 AND inbox.agent_bot_enabled                # NÍVEL 2: inbox
                 AND conversation.status == "open"          # P3: só open
                 AND conversation.ai_active                 # NÍVEL 3: conversa (o toggle age aqui)
                 AND settings.get("auto_reply", True)
   ```
   `contacts.ai_enabled` **NÃO entra** na expressão (P5).
4. **`touch_activity`**: a cada inbound, atualizar `last_activity_at` e `waiting_since` da conversa.
5. **Eventos**: todos os `new_message` passam a carregar `conversation_id`; emitir
   `conversation_created`/`conversation_status_changed` quando a `resolve_inbound` abrir/reabrir
   (§6). O `human_transfer_alert` (`:617-622`) passa a carregar `conversation_id`.

### 4.2 `agent/handler.py` (editar)
- O dispatch já é por `phone`/`ContactMemory`. Propagar `conversation_id` no `ContactMemory`
  (`handler.py:431`) para que os saves de resposta da IA (`message.sent`) gravem `conversation_id` e
  os eventos do bus carreguem o id. Sem `if/elif` por tool (regra do CLAUDE.md).

### 4.3 `agent/tools/transfer_to_human.py` (editar — handoff evoluído)
Manter o **nome** da tool (identidade). Trocar o corpo (`:51-53`):
- `conversation_repo.set_ai_active(conv_id, False)`
- `conversation_repo.update_status(conv_id, "open")` (sinal "humano assume", estilo Chatwoot)
- (opcional, fase 2) atribuir à fila / round-robin
- manter tag `transferido_atendente` + alerta WS `human_transfer_alert` (agora com `conversation_id`)
- `ToolContext` precisa expor a conversa atual — adicionar `ctx.conversation_id`/`ctx.conversation`.

**Critério de pronto (webhook):** com Evolution API (ou webhook mockado nos testes), uma mensagem
inbound cria/anexa a conversa correta, grava `messages.conversation_id`, e a IA só responde quando a
cascata permite. `transfer_to_human` põe a conversa em `open` + `ai_active=0` e emite o alerta com
`conversation_id`.

---

## 5. Gate de IA: cascata **global → inbox → conversa** (P5 — DECIDIDO, MUDANÇA do Lote 2)

A cascata tem **3 níveis** (SEM nível de contato). `contacts.ai_enabled` **sai do gate**.

| Ordem | Nível | Coluna / config | Papel |
|---|---|---|---|
| 1 | Global | config key `ai_global_enabled` (default `True`) | liga/desliga a IA **da conta toda** |
| 2 | Inbox | `inboxes.agent_bot_enabled` (default `1`) | liga/desliga o bot **na inbox** |
| 3 | Conversa | `conversations.ai_active` (default `1`) | override **por conversa** — é o que `toggle-ai` e `transfer_to_human` mexem |

A IA só responde se **todos** os níveis permitem **e** `status=='open'` (P3) **e** `auto_reply` global.
`contacts.ai_enabled` não participa.

- **Decisão canônica vive no core** (a cascata acima no webhook, §4.1), não em plugin. Plugins
  continuam podendo abortar via `filter.llm.messages`/`filter.system_prompt → None` (casos custom:
  horário de funcionamento etc.).
- **`POST /api/contacts/{phone}/toggle-ai`** (`server/routes/contacts.py`) **muda de semântica (P5):**
  passa a operar a **conversa ativa** daquele contato (`conversation_repo.set_ai_active`) e **deixa de
  escrever `contacts.ai_enabled`**. O caminho canônico no frontend novo é
  `PATCH /api/conversations/{id}` com `{ai_active}`; o endpoint legado por phone é mantido só por
  compat e redireciona para a conversa ativa. Evento WS passa a ser `conversation_ai_toggled` (o
  `contact_ai_toggled` é mantido por compat, espelhando a conversa ativa).

**Critério de pronto:** desligar a IA numa conversa não afeta outra conversa do mesmo contato;
desligar `agent_bot_enabled` da inbox silencia toda a inbox; desligar `ai_global_enabled` silencia
tudo; `contacts.ai_enabled` não tem mais efeito sobre o gate.

---

## 6. Endpoints REST e eventos

### 6.1 `server/routes/conversations.py` (novo)
Registrar via `register_routes(app, deps)` (padrão `ServerDeps`). Endpoints:

| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/api/conversations` | Lista com filtros `?status=&assignee=me\|unassigned\|all&inbox_id=&cursor=` |
| GET | `/api/conversations/unassigned-count` | Badge da fila |
| GET | `/api/conversations/{id}` | Detalhe (contato + status + assignee + última msg) |
| GET | `/api/conversations/{id}/messages?limit=N` | Mensagens por `conversation_id` |
| PATCH | `/api/conversations/{id}` | Atualiza `{status?, is_archived?, assignee_user_id?, team_id?, ai_active?}` — cobre atribuir/transferir/resolver(`closed`)/reabrir(`open`)/arquivar/toggle-IA de forma uniforme (estilo Chatwoot). `status` só aceita `open`/`closed` (P3). `is_archived` é ortogonal (P10). `priority`/`snooze` ficam para a Fase 2. |
| POST | `/api/conversations/{id}/assign-me` | Atalho "pegar" (assignee = current_user) |

- **RBAC (P9 — visibilidade por membership de inbox)**: gate por permissões do doc 03
  (`conversation.read/reply/assign/resolve`); o atendente só vê/atua nas **inboxes em que é membro**;
  `conversation.read_all` (admin/gestor) libera tudo. Enquanto doc 03 não existir, liberar para a senha
  única atual.
- **Validação de transições** de status no repo: só `open`↔`closed` (P3); rejeitar valores inválidos
  com 409. `is_archived` não é transição de status (flag independente).

### 6.2 Eventos WebSocket (via `ConnectionManager.broadcast`, `server/state.py:61`)

| Evento | `data` | Quando |
|---|---|---|
| `conversation_created` | `{conversation_id, display_id, contact_phone, inbox_id, status}` | nova conversa |
| `conversation_status_changed` | `{conversation_id, status, prev_status, by_user_id?}` | transição de status |
| `conversation_assigned` | `{conversation_id, assignee_user_id, team_id?, by_user_id?}` | atribuição/transferência |
| `conversation_archived` | `{conversation_id, is_archived, by_user_id?}` | arquivar/desarquivar (P10) |
| `conversation_updated` | `{conversation_id, fields:{...}}` | custom_attributes (priority/snooze só na Fase 2) |
| `conversation_ai_toggled` | `{conversation_id, ai_active}` | estende `contact_ai_toggled` |

Todos os `new_message` ganham `conversation_id`. `human_transfer_alert` ganha `conversation_id`.

### 6.3 Eventos do bus de plugins (fire-and-forget)
Emitir em paralelo aos WS, via `emit`/`emit_with_filter` do bus:
`conversation.created`, `conversation.status_changed`, `conversation.assigned`,
`conversation.resolved`, `conversation.reopened`. Permite automações de terceiros sem tocar no core.
(Documentar no CLAUDE.md na seção de eventos.)

**Critério de pronto:** `tests/test_endpoints.py` ganha bloco cobrindo `GET/PATCH /api/conversations`
(listar por status, assignee=unassigned/me, atribuir, resolver, reabrir); eventos WS observáveis no
TestClient.

---

## 7. Frontend (Preact + HTM, sem build)

Seguir regras de modo escuro do CLAUDE.md (`wa-*`, `.wa-field`) em toda tela nova.

### 7.1 Lista de conversas (MVP simplificado)
- **`web/static/js/components/contacts/Contacts.js`** → introduzir `Conversations.js` (ou refatorar
  internamente). Item = contato + badge de status + **badge de grupo quando `is_group` (P8)** +
  (quando houver) avatar do assignee. MVP: **uma linha por conversa ativa derivada** (parece a lista
  de hoje).
- **Abas/dropdown de filtro** (P3 — só `open`/`closed`) substituindo o `showArchived`
  (`Contacts.js:28`): **Abertas** / **Resolvidas** / **Não atribuídas** / **Minhas**. (Sem aba
  "Pendentes" no MVP.) O toggle **Arquivadas** continua como dimensão **independente** (P10 — archive
  ortogonal ao status). Mapeiam para query params do `GET /api/conversations`.
- **Grupos (P8):** aparecem normalmente nas filas (NÃO ocultar), apenas marcados com badge de grupo.
- **Tela "Não atribuídas"**: fila FIFO (ordenada por `waiting_since`) com botão **Pegar**
  (`POST /assign-me`). Respeita membership de inbox (P9).

### 7.2 Header da conversa (`ContactDetail.js`)
Botões: **Atribuir a mim**, **Transferir** (dropdown de atendentes; gated por RBAC),
**Resolver** (`status='closed'`) / **Reabrir** (`status='open'`), **Arquivar/Desarquivar**
(`is_archived`, ortogonal ao status — P10), e o toggle **IA on/off** existente (`handleToggleAI`,
`Contacts.js:66`) agora operando **`conversation.ai_active`** via `PATCH /api/conversations/{id}`
(P5 — o toggle age na conversa, não no contato). **Adiar (snooze)** fica para a Fase 2.

### 7.3 Serviços/estado
- `web/static/js/services/api.js`: adicionar chamadas `listConversations`, `patchConversation`,
  `assignMe`, `getConversationMessages`.
- WS handlers em `app.js`: reagir a `conversation_*` para atualizar a lista/header ao vivo.
- Roteamento de `new_message` por `conversation_id`.

**Critério de pronto:** abrir o painel, ver abas funcionando, pegar uma conversa da fila, resolver e
reabrir — tudo refletindo via WS sem reload; tudo legível no modo escuro.

---

## 8. Dependências novas (pip / JS)

- **Nenhuma dependência pip nova** prevista para este plano (SQLAlchemy Core + Alembic já presentes).
  O contador de `display_id` é SQL puro.
- **Nenhuma lib JS nova** (Preact/HTM já vendorizados).
- Argon2/passlib e `users` vêm do plano 03, não daqui.

---

## 9. Faseamento (resumo acionável)

- **Fase 1a — schema:** stub `inboxes` (se preciso) + `contact_inboxes` + `conversations` +
  `messages.conversation_id` + reescopo de `contacts` (drop unique phone, +custom_attributes).
  Migrations 0007/0008. **Pronto:** `alembic upgrade head` limpo em SQLite e Postgres.
- **Fase 1b — backfill:** data migration idempotente (contact → 1 contact_inbox + 1 conversa;
  `messages.conversation_id` em massa). **Pronto:** invariantes de §2.2 num clone de produção.
- **Fase 1c — repos + webhook + handler:** `resolve_inbound`, gate de IA por cascata,
  `transfer_to_human` evoluída. **Pronto:** webhook opera por conversa (teste Evolution/mock).
- **Fase 1d — API + WS:** `conversations.py`, eventos WS + bus. **Pronto:** testes de endpoint.
- **Fase 1e — frontend:** lista de conversas + abas + fila não-atribuídas + ações no header.
  **Pronto:** fluxo manual ponta-a-ponta no painel.
- **Fase 2 — paridade Chatwoot (fora do MVP):** estado `pending`/"aguardando" (P3 adiou),
  `snoozed`/auto-resolução por inatividade (P7 — desligada no MVP; background task em
  `server/background.py`), `priority`, `agent_bot_enabled` real por inbox (multi-inbox do doc 02),
  timeline/auditoria (`conversation_events`), UI multi-conversa ("outras conversas deste contato"),
  merge de contatos (P11).
- **Fase 3 — automação + multi-canal:** round-robin, presença online, caps por atendente,
  `team_id`/filas, saved views (doc 08), merge de contatos (§3.4.1 da pesquisa).

---

## Perguntas em aberto

> Todas as perguntas funcionais deste plano foram **decididas** (DECISOES.md, P1–P12). Mantidas
> abaixo com o registro da decisão para rastreabilidade.

1. **Ordem com docs 02 e 03 / FKs para `inboxes` e `users`.**
   - ✅ **DECIDIDO (2026-06-19): P1 — stubs sem FK.** Este plano cria stubs mínimos de `inboxes` e
     `assignee_user_id` **sem FK**; os docs 02/03 fazem `ALTER` aditivo (e adicionam a FK) depois.
     Destrava o trabalho sem retrabalho de schema.

2. **Janela de reabertura (`conversation_reopen_window`).**
   - ✅ **DECIDIDO (2026-06-19): P2 — sempre reabrir a mesma conversa.** Quando o cliente volta a
     falar, **reabre a mesma** conversa (sem janela de tempo, sem criar nova). Combina com P3
     (resolvida some do painel; nova mensagem reabre). A regra de "janela 24h" foi descartada.

3. **`resolved` vs `closed` terminal.**
   - ✅ **DECIDIDO (2026-06-19): P3 — só `open`/`closed (resolved)` no MVP.** Resolvida some do painel
     de abertas; nova mensagem do cliente reabre. Estado "aguardando" (`pending`) fica para o futuro.

4. **Status inicial com bot ativo: `open` ou `pending`?**
   - ✅ **DECIDIDO (2026-06-19): P4 — nascer `open` na fila.** O indicador de "IA ativa" (`ai_active`)
     mostra que o robô está atendendo. `pending` fica para a Fase 2.

5. **Cascata de IA.**
   - ✅ **DECIDIDO (2026-06-19): P5 (MUDANÇA) — cascata global → inbox → conversa, SEM nível de
     contato.** `contacts.ai_enabled` **sai do gate** (aposentado/ignorado). O toggle passa a agir na
     **conversa** (`conversations.ai_active`). Ver §5 reescrita.

6. **`display_id`: global ou por inbox? Como gerar concorrência-safe?**
   - ✅ **DECIDIDO (2026-06-19): P6 — global, via tabela-contador** (`conversation_counters`) com
     `UPDATE … RETURNING n` atômico (portável SQLite+Postgres, sem o race do `MAX()+1`). Escopo
     global, como Chatwoot/Zendesk/Intercom. Ver §1.6.

7. **Auto-resolução por inatividade.**
   - ✅ **DECIDIDO (2026-06-19): P7 — desligada.** Fica como extra para depois (Fase 2).

8. **Grupos de WhatsApp (`contacts.is_group=1`).**
   - ✅ **DECIDIDO (2026-06-19): P8 — grupos viram conversa normal E aparecem nas filas com badge de
     grupo.** NÃO ocultar grupos das filas (diverge da recomendação original). `assignee`/`closed`
     funcionam igual.

9. **Visibilidade: atendente vê só as dele ou a fila inteira?**
   - ✅ **DECIDIDO (2026-06-19): P9 — membership de inbox (modelo Chatwoot).** Atendente só vê/atua nas
     inboxes em que é membro; fora delas, não vê nada. `conversation.read_all` (admin/gestor) libera
     tudo. Catálogo de permissões no doc 03.

10. **Migração de `is_archived`: vira `resolved`?**
    - ✅ **DECIDIDO (2026-06-19): P10 — archive ORTOGONAL ao status.** `is_archived` é flag
      independente; dá para arquivar conversa **aberta**. No backfill NÃO vira `closed` — a conversa
      nasce `open` e copia `is_archived` do contato.

11. **Merge de contatos (telefones diferentes da mesma pessoa).**
    - ✅ **DECIDIDO (2026-06-19): P11 — fora do MVP.** Schema deixa o caminho aberto (`phone` é
      atributo; identidade no `contact_inbox`). Previsto para o futuro.

12. **`source_id` no WhatsApp: número ou JID?**
    - ✅ **DECIDIDO (2026-06-19): P12 — JID + LID (estilo Evolution), guardar ambos.** `source_id`/
      `source_jid` = JID estável; `source_lid` = LID (multi-device) quando disponível. A resolução do
      remetente casa por JID **e** LID. Ver §1.3 e §2.2 (backfill deriva o JID do phone). Detalhe de
      qual coluna é a chave primária resolvido na implementação (chave de resolução = `source_id`).
