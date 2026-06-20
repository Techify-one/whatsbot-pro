# 01 — Caixa de entrada e conversas (Inbox & Conversations)

> Pesquisa de arquitetura para introduzir o conceito de **conversa** (estilo Chatwoot) no
> WhatsBot: abrir/encerrar conversas, fila de não-atribuídas, atribuição/transferência
> entre atendentes, e controle de participação do agente de IA por inbox.
>
> **Decisão do cliente (2026-06-18) — modelo de conversa estilo Chatwoot de TRÊS níveis:**
> **Contact (a pessoa) → ContactInbox (a identidade da pessoa num canal/número MEU
> específico) → Conversation (várias por contato, reabríveis/encerráveis).** Caso de uso
> explícito: "um mesmo cliente pode me procurar por números MEUS diferentes (inboxes
> diferentes) → é uma conversa diferente, porém o MESMO contato". Ou seja: **múltiplas
> conversas por contato** (não apenas 1 conversa ativa por contato-inbox). O cliente pediu:
> "para facilitar por enquanto, vamos pensar se compensa fazer isso, mas considere pois é o
> que quero" → o **modelo de DADOS já nasce no formato final** (3 tabelas, múltiplas
> conversas/contact_inboxes), mas a **UI/MVP pode simplificar** (ver §3.6 "Compensa fazer
> agora?").
>
> **Decisão de produto de referência:** uma empresa, server-hosted, multi-usuário, **sem**
> multi-tenant. A abstração de canal/inbox genérica é tratada em
> [`02-canais-e-providers.md`](02-canais-e-providers.md); usuários e permissões em
> [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md); atributos
> personalizados em [`05-atributos-personalizados.md`](05-atributos-personalizados.md);
> filtros e saved views em [`08-filtros.md`](08-filtros.md).

---

## 1. O que existe hoje

A unidade de trabalho atual é o **contato**, não a conversa. Não há entidade "conversa",
status (aberta/encerrada), atribuição a atendente, nem transferência.

### Modelo de dados

- **`contacts`** — chave de negócio `phone` (única). Carrega campos que hoje fazem o papel
  de "estado de conversa" mas amarrados ao contato:
  `db/tables.py:41-66` — colunas relevantes:
  - `ai_enabled` (`db/tables.py:51`) — liga/desliga a IA **por contato** (não por inbox).
  - `is_pinned` (`:56`), `is_archived` (`:54`), `archived_by_app` (`:55`),
    `unread_count` (`:58`), `unread_ai_count` (`:59`), `has_unread_mention` (`:60`),
    `can_send` (`:57`).
  - Índices: `idx_contacts_updated` (`:64`), `idx_contacts_archived` (`:65`).
- **`messages`** — FK `contact_id` (`db/tables.py:79-96`). Cada mensagem pertence
  diretamente a um contato; **não há `conversation_id`**. Colunas: `role`, `content`, `ts`,
  `media_type`, `media_path`, `status`, `msg_id`, `revoked`, `reactions`,
  `reply_to_msg_id`. Índices `idx_msg_contact_ts` (`:95`), `idx_msg_id` (`:96`).
- **`observations`**, **`usage`**, **`contact_tags`**, **`unread_msg_ids`** — todos FK em
  `contacts.id`. (`db/tables.py:68-141`)
- **`executions` / `execution_steps`** (`db/tables.py:144-168`) — tracking de uma execução
  webhook→resposta. Tem `status` (`running`/...) e `phone`, mas é o ciclo de **uma resposta
  da IA**, não a conversa de atendimento. Não confundir.
- `CORE_TABLES` (`db/tables.py:207`) — usada pela migração SQLite→Postgres; toda tabela
  nova precisa entrar aqui implicitamente (é derivada do metadata).

### Estado de "atendimento" hoje (ad-hoc, no contato)

- **"Bot vs humano"** é representado por `contacts.ai_enabled`. Não há fila de humanos nem
  noção de "quem está atendendo".
- **Tool `transfer_to_human`** (`agent/tools/transfer_to_human.py`): o handoff atual é
  rudimentar — desabilita a IA do contato (`set_ai_enabled(False)`, `:51`), cria/aplica a
  tag `transferido_atendente` (`:52-53`) e retorna um feedback ao LLM. **Não atribui a
  ninguém** — só "tira o bot do caminho".
- `ContactMemory.set_ai_enabled` (`agent/memory.py:191-193`) persiste em `contacts`.
- Endpoint `POST /api/contacts/{phone}/toggle-ai` (`server/routes/contacts.py:929-938`).
- O webhook respeita `contact.ai_enabled` antes de auto-responder
  (`server/routes/webhook.py:834, 860, 966`).
- Após `transfer_to_human`, o webhook emite o alerta WS `human_transfer_alert`
  (`server/routes/webhook.py:617-622`) — único "evento de handoff" existente.

### Frontend

- **Sidebar / lista** — `web/static/js/components/contacts/Contacts.js` (componente raiz,
  `:14`) + `ContactList.js`. Lista por contato, filtro só por `archived`
  (`Contacts.js:28` `showArchived`) e busca textual (`:17`). Não há filtro por status,
  assignee ou inbox.
- **Detalhe/chat** — `ContactDetail.js`; painel lateral de info `ContactInfoPanel.js`;
  menu de contexto `ContextMenu.js`.
- **Toggle de IA** no painel: `handleToggleAI` (`Contacts.js:66-76`) chama
  `toggleContactAI`.
- Roteamento SPA em `web/static/js/app.js`; telas extras chegam via `GearMenu`.

### WebSocket

- `ConnectionManager.broadcast(event, data)` em `server/state.py:61-67`. Eventos hoje
  emitidos do webhook (`server/routes/webhook.py`): `new_message` (vários),
  `contact_info_updated`, `human_transfer_alert` (`:619`), `contact_ai_toggled` (`:620`),
  `tags_changed`, `contact_tags_updated`, `messages_read`, `message_reaction`,
  `message_revoked`, `message_deleted`, `status`. **Nenhum evento de conversa.**

### Auth

- Senha única, **sem usuários** (vide `server/state.py:108` `login_attempts`). Logo, hoje
  não há "atendente" a quem atribuir. Isso é pré-requisito deste doc e vem de
  [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md).

### Conclusão do estado atual

Tudo é **1 contato = 1 thread infinita**, com um único bit de "IA on/off". Pior: hoje o
**contato e a identidade-no-número estão fundidos** — a chave de negócio é `phone`, então
"a pessoa" e "o número dela num canal meu" são a mesma linha. O modelo decidido (Chatwoot,
§3) separa isso em três entidades: **Contact** (a pessoa) ↔ **ContactInbox** (a identidade
da pessoa numa inbox/número meu, onde o `phone`/`source_id` passa a morar) ↔
**Conversation** (a thread de atendimento, com status/atribuição, **várias por contato**).
Precisamos introduzir essas entidades sem quebrar o histórico por contato existente.

---

## 2. Requisitos da feature (derivados do pedido do cliente)

1. **Caixa de entrada estilo Chatwoot** — lista de **conversas** (não de contatos), com
   visões/filtros por status e por atendente. (acoplado a [`08-filtros.md`](08-filtros.md))
2. **Abrir / encerrar conversa** — uma conversa tem ciclo de vida; ao chegar mensagem de um
   contato sem conversa aberta, abre-se uma; um atendente pode **encerrar (resolver)**.
3. **Reabertura** — se o cliente volta a falar depois de encerrada, decidir entre reabrir a
   mesma ou criar nova conversa (ver §3.4).
4. **Ver conversas NÃO atribuídas** — uma fila/visão de "Não atribuídas".
5. **Atribuir a si mesmo** — "pegar" uma conversa da fila.
6. **Passar/transferir para outro atendente** — reassinar uma conversa a outro usuário
   (e, opcionalmente, a um time).
7. **Controlar se o agente de IA participa de uma inbox** — equivalente ao Chatwoot
   "connect an agent bot to an inbox": ligar/desligar o bot **por inbox**, com handoff
   bot↔humano. Hoje o controle é por contato (`ai_enabled`); a feature exige o nível inbox
   **e** preservar o override por conversa.
8. **(implícito) atribuição automática** — opcional: round-robin / fila de não-atribuídas,
   com cap por atendente (ver §5).
9. **(implícito) RBAC** — quem pode atribuir a outros, transferir, encerrar, reabrir, e ver
   conversas de terceiros é definido pelos papéis de
   [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md).

---

## 3. Modelo de dados proposto

### 3.1 Referência: o modelo de TRÊS níveis do Chatwoot (confirmado no schema)

O Chatwoot modela três entidades distintas (confirmado em `db/schema.rb`, ref [14]):

**1. `contacts` — a PESSOA.** A identidade não é o telefone; o telefone é só um atributo.
Colunas (resumo): `name`, `email`, `phone_number`, `identifier`, `additional_attributes`
(jsonb), `custom_attributes` (jsonb), `account_id`, `blocked`, `company_id`. Note que
`phone_number` **não tem unique** — a unicidade é por `email` e por `[identifier,
account_id]`. Uma pessoa = uma linha em `contacts`, independente de quantos números/canais
use.

**2. `contact_inboxes` — a IDENTIDADE da pessoa NUMA inbox (canal/número meu).** É a tabela
de junção que liga `contact` ↔ `inbox` e guarda o `source_id`, o identificador da pessoa
**naquele canal** (no WhatsApp = o número/JID dela; no e-mail = o e-mail; no widget = o
hash de sessão). Colunas: `contact_id`, `inbox_id`, `source_id` (NOT NULL), `created_at`,
`updated_at`, `hmac_verified`, `pubsub_token`. **Unique index em `[inbox_id, source_id]`**
— é assim que o Chatwoot resolve o contato ao chegar uma mensagem (procura
`[inbox_id, source_id]`; achou → reusa o contato; não achou → cria contato + contact_inbox).
Uma pessoa tem **um `contact_inbox` por inbox** em que aparece → **N contact_inboxes por
contato**.

**3. `conversations` — a THREAD de atendimento.** Escopada a um inbox, a um contato **e a um
`contact_inbox`**. Campos centrais:

- `display_id` (id sequencial por conta, mostrado ao usuário; unique em `[account_id,
  display_id]`),
- `status` — enum inteiro `open(0) / resolved(1) / pending(2) / snoozed(3)`,
- `priority` — enum `low / medium / high / urgent`,
- `inbox_id`, `contact_id`, **`contact_inbox_id`** (FK pra junção acima), `assignee_id`
  (agente), `team_id`,
- `waiting_since` (quando o contato mandou a última msg que aguarda resposta),
- `snoozed_until`, `last_activity_at` (dirige auto-resolução),
- `contact_last_seen_at`, `agent_last_seen_at`, `custom_attributes` (jsonb).

Crucialmente: **o Chatwoot NÃO impõe "uma conversa ativa por contato_inbox"**. Não há índice
único de unicidade sobre conversas abertas — um mesmo `contact_inbox` pode ter **várias
conversas** (cada episódio é uma conversa; reabrir é criar/abrir outra). A "conversa ativa
atual" é **derivada** (a `open` mais recente daquele contact_inbox), não uma restrição de
schema. (Referências [1][2][3][5][6][14])

**Múltiplos números MEUS, mesma pessoa (caso do cliente):** se o cliente Fulano me escreve
no meu número A e no meu número B, são **duas inboxes** (doc 02) → **dois contact_inboxes**
(`[inbox_A, jid_fulano]` e `[inbox_B, jid_fulano]`) → **conversas distintas**, mas
**resolvendo para o MESMO `contact`** (desde que eu saiba que é a mesma pessoa — ver §3.4
sobre o limite disso). Isso é exatamente o que o cliente pediu.

Como descartamos multi-tenant, **não** teremos `account_id` em nenhuma tabela — esse é o
maior corte em relação ao schema do Chatwoot. Onde o Chatwoot usa `[account_id, display_id]`
nós usamos um `display_id` global simples; onde usa `[inbox_id, source_id]` mantemos
idêntico (não depende de account).

### 3.2 DDL ilustrativo — as TRÊS tabelas (`contacts` reescopado, `contact_inboxes`, `conversations`)

Convenção do projeto: SQLAlchemy 2.0 Core (`db/tables.py`), timestamps como epoch `Float`,
enums como `Text` (SQLite não tem enum nativo; validação na camada de repo), JSON como
`Text`. DDL abaixo é **ilustrativo** — as tabelas reais nascem de `Table()` no metadata +
migration Alembic. As três tabelas já nascem no **formato final** (múltiplas conversas,
múltiplos contact_inboxes); a simplificação fica na UI (§3.6).

```sql
-- 1) Contact: a PESSOA. phone deixa de ser a chave (vira atributo do contact_inbox).
--    Reaproveita a tabela `contacts` atual (migração no §3.5), mas semanticamente é "a pessoa".
CREATE TABLE contacts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT,
    email             TEXT,
    -- phone permanece por compat e como "telefone primário" exibível, mas NÃO é mais a
    -- identidade-no-canal: essa mora em contact_inboxes.source_id (ver §3.5).
    phone             TEXT,
    -- ...demais colunas de pessoa já existentes (profession, company, address...)
    custom_attributes TEXT    NOT NULL DEFAULT '{}',  -- JSON; ver doc 05 (atributos de CONTATO)
    created_at        REAL    NOT NULL,
    updated_at        REAL    NOT NULL
);

-- 2) ContactInbox: a IDENTIDADE da pessoa numa inbox/número MEU. Junção contact <-> inbox.
CREATE TABLE contact_inboxes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id        INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    inbox_id          INTEGER NOT NULL REFERENCES inboxes(id)  ON DELETE CASCADE,  -- ver doc 02
    source_id         TEXT    NOT NULL,           -- id da pessoa NAQUELE canal (WhatsApp: número/JID)
    created_at        REAL    NOT NULL,
    updated_at        REAL    NOT NULL
);
-- Espelha o Chatwoot: ao chegar msg, resolve por [inbox_id, source_id].
CREATE UNIQUE INDEX uq_contact_inbox_inbox_source ON contact_inboxes (inbox_id, source_id);
CREATE INDEX idx_contact_inbox_contact ON contact_inboxes (contact_id);

-- 3) Conversation: a THREAD de atendimento. VÁRIAS por contact_inbox (sem unicidade rígida).
CREATE TABLE conversations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    display_id        INTEGER NOT NULL,          -- id legível, sequencial global (ver §9 q6)
    inbox_id          INTEGER NOT NULL REFERENCES inboxes(id)  ON DELETE CASCADE,  -- ver doc 02
    contact_id        INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    contact_inbox_id  INTEGER NOT NULL REFERENCES contact_inboxes(id) ON DELETE CASCADE,
    status            TEXT    NOT NULL DEFAULT 'open',  -- open|pending|resolved|snoozed
    assignee_user_id  INTEGER          REFERENCES users(id) ON DELETE SET NULL,    -- ver doc 03
    team_id           INTEGER          REFERENCES teams(id) ON DELETE SET NULL,    -- opcional
    priority          TEXT,                       -- low|medium|high|urgent | NULL
    snoozed_until     REAL,                       -- epoch; relevante p/ status=snoozed
    opened_at         REAL    NOT NULL,           -- quando virou open (1ª vez ou reabertura)
    resolved_at       REAL,                       -- quando foi resolved
    waiting_since     REAL,                       -- última msg do contato aguardando resposta
    last_activity_at  REAL    NOT NULL,           -- dirige ordenação e auto-resolução
    custom_attributes TEXT    NOT NULL DEFAULT '{}',  -- JSON; ver doc 05 (atributos de CONVERSA)
    created_at        REAL    NOT NULL,
    updated_at        REAL    NOT NULL
);

CREATE INDEX idx_conv_inbox_status     ON conversations (inbox_id, status);
CREATE INDEX idx_conv_assignee_status  ON conversations (assignee_user_id, status);
CREATE INDEX idx_conv_contact          ON conversations (contact_id);
CREATE INDEX idx_conv_contact_inbox    ON conversations (contact_inbox_id);
CREATE INDEX idx_conv_last_activity    ON conversations (last_activity_at);
-- "Conversa ativa atual" = a open/ativa mais recente de um contact_inbox. É uma CONSULTA
-- (ORDER BY last_activity_at DESC LIMIT 1 WHERE status='open'), NÃO um índice de unicidade.
```

> Notas:
> - **Não há índice único** forçando "1 conversa ativa por contato-inbox" — essa era a
>   abordagem anterior e foi **descartada** pela decisão do cliente (múltiplas conversas). A
>   noção de "conversa atual" é derivada por query (§3.4).
> - `custom_attributes` aparece em **`contacts` E `conversations`** — padroniza com
>   [`05-atributos-personalizados.md`](05-atributos-personalizados.md), que define atributos
>   custom **tanto de contato quanto de conversa**.
> - `priority`, `team_id`, `snoozed` podem entrar em fase posterior (§8) sem mudar o resto.

### 3.3 Ligação com `messages` (adicionar `conversation_id`)

```sql
ALTER TABLE messages ADD COLUMN conversation_id INTEGER
    REFERENCES conversations(id) ON DELETE CASCADE;
CREATE INDEX idx_msg_conversation_ts ON messages (conversation_id, ts);
```

- **Manter `contact_id` em `messages`** (mantém todo o código atual funcionando) e
  **adicionar** `conversation_id` ao lado. `contact_id` vira derivável de
  `conversation.contact_id`, mas mantê-lo evita reescrever `message_repo`, índices e queries
  de uma vez só.
- **Backfill (migration Alembic):** ver §3.5 — para cada `contact` atual cria-se 1
  `contact_inbox` (na inbox WhatsApp default, `source_id = phone`) + 1 `conversation`
  (status inferido: `resolved` se `is_archived`, senão `open`), e seta-se
  `messages.conversation_id` em massa por `contact_id`.

### 3.4 Múltiplas conversas por contato-inbox + "conversa atual" derivada

**Decisão do cliente:** múltiplas conversas por contato (modelo Chatwoot puro). Cada episódio
de atendimento pode ser uma conversa; o histórico de um `contact_inbox` é
**N `resolved` + 0..1 ativa**. **Não** há restrição de unicidade no schema (§3.2). A noção de
"conversa ativa atual" — a que a UI mostra como aberta — é **derivada por consulta**:

```sql
-- conversa "atual" de um contact_inbox: a ativa mais recente
SELECT * FROM conversations
WHERE contact_inbox_id = :cib AND status IN ('open','pending','snoozed')
ORDER BY last_activity_at DESC LIMIT 1;
```

Isso preserva o benefício prático de "uma thread visível por contato" **sem** travar o
modelo: amanhã a UI pode listar todos os episódios sem migração de schema.

**Resolução do contato + abertura/reabertura (regra concreta).** Ao chegar mensagem inbound
(`inbox_id` conhecido pelo doc 02; `source_id` = número/JID do remetente):
1. **Resolver `contact_inbox`** por `[inbox_id, source_id]` (unique). Se não existir, criar —
   e, se não houver um `contact` ao qual ligar, criar o `contact` também. (No WhatsApp de hoje,
   1 inbox só → 1 contact por número; o merge de pessoas que usam números diferentes é item
   futuro, §3.4.1.)
2. **Procurar conversa ativa** (`status in open|pending|snoozed`) daquele `contact_inbox_id`,
   a mais recente. Se existir → anexar a mensagem; se `snoozed` e `snoozed_until` passou, ou
   `pending`, promover para `open`.
3. Se não existir ativa → procurar a última `resolved` daquele `contact_inbox`. Se resolvida
   há menos de `conversation_reopen_window` (config, ex.: 24h) → **reabrir** (status→`open`,
   `opened_at`=agora, `resolved_at`=NULL); senão → **criar nova conversa** (novo
   `display_id`). A escolha "reabrir vs sempre nova" é configurável (§9 q2).
4. Emitir eventos de conversa (§7).

#### 3.4.1 Esclarecendo "números diferentes" (ambiguidade do pedido)

O pedido "mesmo cliente por números diferentes = conversa diferente, mesmo contato" tem dois
casos bem distintos:

- **Caso PRINCIPAL (suportado de fábrica) — MEUS números diferentes.** A mesma pessoa
  escreve para inboxes/números **meus** diferentes (ex.: número de Vendas e número de
  Suporte). Cada inbox gera um `contact_inbox` próprio (`[inbox_A, jid]`, `[inbox_B, jid]`),
  **mas o `source_id` (o número/JID da pessoa) é o mesmo** → consigo apontar ambos para o
  **mesmo `contact`**. Conversas distintas, mesma pessoa. É exatamente isso que a decisão
  pede, e o schema das 3 tabelas já resolve.
- **Caso FUTURO — telefones DELA diferentes.** A mesma pessoa me escreve de dois aparelhos
  /chips **dela** (dois `source_id` diferentes) na mesma inbox. Aqui eu **não tenho como
  saber automaticamente** que é a mesma pessoa — viram dois `contact` distintos. Unificar
  exige **MERGE de contatos** (operador escolhe "estes dois contatos são a mesma pessoa" →
  re-aponta os `contact_inboxes` e reatribui conversas para um único `contact`, descartando o
  outro). O Chatwoot tem essa feature de merge ([15]). **Fora do MVP**; o schema (phone como
  atributo do `contact_inbox`, não do `contact`) já deixa o caminho aberto.

### 3.5 Impacto no `contacts` atual e migração (backfill)

Hoje `contacts.phone` é a chave de negócio: **o "contato" e a "identidade no número" estão
fundidos numa única linha**. No modelo novo:

- O **`contact`** passa a ser "a pessoa" — `phone` permanece na linha por compat e como
  "telefone primário exibível", mas **deixa de ser a identidade-no-canal**.
- A **identidade-no-canal** migra para `contact_inboxes.source_id` (o número/JID na inbox).
- As **mensagens** ganham `conversation_id` (§3.3) e passam a pertencer a uma conversa.

**Migração one-time (Alembic), idempotente, não-destrutiva:**
1. Garantir a inbox WhatsApp default (criada pelo doc 02). Seja `INBOX_WA` o id dela.
2. Para **cada** linha de `contacts` (= cada pessoa+número de hoje):
   - criar **1 `contact_inbox`**: `(contact_id=esse, inbox_id=INBOX_WA, source_id=phone)`;
   - criar **1 `conversation`**: `(contact_id, inbox_id=INBOX_WA, contact_inbox_id=acima,
     status = 'resolved' se is_archived senão 'open', display_id sequencial,
     opened_at/last_activity_at = último `ts` de msg ou `updated_at`,
     ai_active = ai_enabled — ver §6)`;
   - `UPDATE messages SET conversation_id = <essa conversa> WHERE contact_id = <esse>`.
3. Flags ad-hoc do contato (`unread_count`, `is_pinned`, `has_unread_mention`...) podem ser
   **copiadas para a conversa** ou mantidas no contato durante a transição (decidir na fase 1;
   ver §9 q10 sobre `is_archived`).

O SQLite original fica preservado para rollback (padrão do projeto). Como `phone` continua na
linha de `contacts`, todo código que ainda lê `contacts.phone` segue funcionando até ser
migrado para resolver via `contact_inboxes`.

### 3.6 Compensa fazer agora? (custo/benefício)

O cliente pediu para **considerar** o modelo de 3 níveis "mesmo que para facilitar a UI seja
simplificada por enquanto". A separação que importa:

| Camada | Custo de já fazer no formato final | Custo de adiar |
|---|---|---|
| **Schema (3 tabelas)** | **Barato.** São tabelas **novas** (`contact_inboxes`, `conversations`) + um backfill linear. Nenhuma reescrita de produto. | **Caro.** Adiar = depois migrar dados vivos (contatos+mensagens+flags) de um modelo fundido para um separado, com app em produção. Migração dolorosa e arriscada. |
| **UI (lista agrupada por conversa, multi-conversa, merge)** | **Cara.** Lista de conversas, abas por status/assignee, header com ações, picker de "outras conversas deste contato". | **Barato adiar.** Dá pra entregar uma UI que mostra só a "conversa atual" (§3.4) e parecer idêntica à de hoje. |

**Recomendação (alinhada à decisão):** **modelar o banco no formato final desde já** (3
tabelas, múltiplas conversas, `source_id` no contact_inbox) **+ simplificar a UI no MVP**
(mostrar só a conversa ativa atual por contato, derivada; sem tela de "todas as conversas do
contato" nem merge ainda). Assim evitamos a migração dolorosa de dados depois, e a UI rica
entra incrementalmente sem tocar no schema. É o melhor dos dois mundos e responde
diretamente ao "compensa fazer isso?": **sim, no banco; não precisa, na UI inicial.**

### 3.7 Relação com `inboxes` e `users`

- `inbox_id` referencia a tabela de inbox/canal definida em
  [`02-canais-e-providers.md`](02-canais-e-providers.md). No MVP haverá **uma** inbox
  (WhatsApp via GOWA). O modelo já fica multi-inbox-ready — e é justamente o multi-inbox que
  habilita o caso "meus números diferentes" (§3.4.1).
- `assignee_user_id` / `team_id` referenciam `users`/`teams` de
  [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md). Enquanto o doc 03 não
  existir, `assignee_user_id` pode ficar nulo/desabilitado (só status funciona).

---

## 4. Máquina de estados da conversa

Estados (alinhados ao Chatwoot, com Zendesk/Intercom como contraste):

```
                 ┌─────────────────────────────────────────────┐
                 │                                             reopen
 (msg inbound)   ▼                                               │
   ─────────► [ OPEN ] ──resolve──► [ RESOLVED ] ──(msg inbound dentro da janela)─┘
      ▲          │  ▲                    │
      │          │  │                    └──(msg inbound fora da janela)──► nova conversa
      │      snooze │ unsnooze /                                            (OPEN)
      │          ▼  │ inbound
   (bot devolve │ [ SNOOZED ] (acorda em snoozed_until → OPEN)
    p/ humano)  │
      │     mark_pending │ ▲
      └──[ PENDING ]◄────┘ │
            (bot atende /  │
         aguardando cliente)
            └──open (handoff p/ humano)──► OPEN
```

| Estado | Significado | Análogo Chatwoot | Análogo Zendesk |
|---|---|---|---|
| `open` | Ativa, requer ação de humano. | `open` | `New`/`Open` |
| `pending` | Bot a atende **ou** aguardando o cliente; não precisa de humano agora. | `pending` | `Pending`/`On-hold` |
| `snoozed` | Adiada até `snoozed_until`; reaparece automaticamente. | `snoozed` | (sem equivalente direto) |
| `resolved` | Encerrada. Reabre por regra (§3.4). | `resolved` | `Solved`/`Closed` |

**Contraste de modelos** (refs [9][10]): Zendesk é **ticket-cêntrico** com cadeia mais longa
(`New→Open→Pending→On-hold→Solved→Closed`, e `Closed` é terminal/imutável). Intercom é
**conversa-cêntrico** e enxuto (`open / snoozed / closed`). O Chatwoot fica no meio com 4
estados — **adotamos o conjunto do Chatwoot** por ser o pedido explícito do cliente e o mais
simples que cobre handoff bot↔humano. Decisão de design: **não** ter um estado `closed`
separado de `resolved` no MVP (Intercom-style: resolved já é o terminal; reabertura cobre o
"volta a falar").

### Transições e quem pode fazê-las (ligado ao RBAC — doc 03)

| Transição | Gatilho | Ator permitido (proposta; final no doc 03) |
|---|---|---|
| `(nenhuma)→open` | 1ª msg inbound sem conversa ativa | sistema (webhook) |
| `open→pending` | bot assume / aguardando cliente | sistema (bot por inbox), atendente |
| `pending→open` | handoff bot→humano; cliente respondeu | sistema (bot/`transfer_to_human`), atendente |
| `open/pending→snoozed` | atendente adia | atendente (assignee ou admin) |
| `snoozed→open` | `snoozed_until` venceu, ou nova msg inbound | sistema |
| `open/pending→resolved` | atendente encerra; auto-resolve por inatividade | atendente (assignee ou admin); sistema |
| `resolved→open` | reabertura (regra §3.4) ou clique "Reabrir" | sistema; atendente/admin |

- **Auto-resolução** opcional (Chatwoot tem): `open` parada > `auto_resolve_after` →
  `resolved`. Driven por `last_activity_at` num background task
  (`server/background.py` já tem o padrão de varredura periódica).
- Toda transição deve ser **auditável** (quem/quando) — ver §7 (eventos) e tabela de
  eventos de conversa abaixo (§3 pode ganhar `conversation_events`/timeline numa fase 2).

---

## 5. Atribuição e transferência

### 5.1 Manual (MVP)

- **Atribuir a si** ("pegar"): `assignee_user_id = current_user.id`. Da fila de
  não-atribuídas (`assignee_user_id IS NULL AND status='open'`).
- **Transferir/passar**: setar `assignee_user_id` para outro usuário (e/ou `team_id`).
  Quem pode transferir a terceiros é gate de RBAC (doc 03): tipicamente assignee atual ou
  admin/supervisor. Transferir **não** muda `status` (continua `open`), só o dono.
- **Desatribuir**: `assignee_user_id = NULL` (volta pra fila).
- Endpoint único `PATCH /api/conversations/{id}` aceitando `{assignee_user_id?, team_id?,
  status?, priority?, snoozed_until?}` cobre atribuir/transferir/resolver/reabrir/snooze de
  forma uniforme (estilo "conversation update API" do Chatwoot).

### 5.2 Fila de não-atribuídas

Visão derivada de query, não tabela: `WHERE assignee_user_id IS NULL AND status IN
('open','pending')`, ordenada por `waiting_since`/`created_at` (FIFO, como o "Assignment V2"
do Chatwoot que começa pela mais antiga). É a base da tela "Não atribuídas" (§7) e de um
filtro salvo de [`08-filtros.md`](08-filtros.md).

### 5.3 Atribuição automática (opcional, pós-MVP)

Estratégias (refs [7][8][12]):

| Estratégia | Como | Trade-off |
|---|---|---|
| **Manual only** (MVP) | Ninguém é auto-atribuído; tudo cai na fila. | Zero complexidade; exige disciplina dos atendentes. |
| **Round-robin** | Distribui sequencialmente entre atendentes **online** da inbox. | Justo e simples; ignora carga real (um atendente lento acumula). |
| **Balanced / least-busy** | Escolhe quem tem menos conversas `open`. | Distribui por carga; precisa contar conversas em tempo real. |
| **Round-robin + cap** (Chatwoot "Agent Capacity Policy") | Round-robin pulando quem está no/acima do limite de `open`; se todos no cap, fica na fila. | Melhor experiência; mais estado (online status, caps). |

Pré-requisitos: **presença/online de atendentes** e **caps por atendente** — ambos
dependem de usuários (doc 03). Config por inbox: `auto_assignment_enabled`,
`assignment_strategy`, `max_open_per_agent`. Recomendação: **só manual no MVP**, round-robin
simples como fase 2, caps como fase 3.

### 5.4 "Fila" vs "Time" (team)

Chatwoot separa **assignee** (pessoa) de **team** (grupo/fila). `team_id` permite rotear "pra
fila de Vendas" e depois um atendente daquela fila pega. No MVP, `team_id` pode ser nulo e
toda a operação girar em torno de `assignee_user_id` + a fila global de não-atribuídas. Times
entram quando o doc 03 modelar grupos de usuários.

---

## 6. Participação do agente de IA por inbox (bot ↔ humano)

### 6.1 Como o Chatwoot faz (referência)

Conecta-se um **Agent Bot** a um **inbox** ("Bot Configuration → escolher bot → Save").
Comportamento (refs [4]):

- Conversas novas naquele inbox nascem com status **`pending`** (o bot está cuidando).
- O bot recebe eventos e responde via API.
- **Handoff bot→humano:** o bot muda o status para **`open`** → a conversa fica disponível
  para um humano (e pode ser auto-atribuída).
- **Devolver ao bot:** o atendente muda o status de volta para **`pending`** → volta pra
  fila do bot.

Ou seja: o **status** é o sinal de quem está no comando — `pending`=bot, `open`=humano.

### 6.2 Adaptação ao WhatsBot

Hoje o "bot on/off" é `contacts.ai_enabled` (global por contato) e o handoff é a tool
`transfer_to_human` que só faz `set_ai_enabled(False)` + tag. Proposta de evolução **sem
descartar o que existe**:

1. **Flag por inbox**: `inboxes.agent_bot_enabled` (doc 02) — equivalente ao "Enable agent
   bot for inbox". Default da inbox WhatsApp = ligado (preserva comportamento atual).
2. **Override por conversa**: manter um bit efetivo `ai_active` na conversa. Resolução em
   cascata na hora de decidir se a IA responde:
   ```
   ia_responde = inbox.agent_bot_enabled
                 AND conversa.status in ('open','pending')   # não responde resolved
                 AND conversa.ai_active                       # override por conversa
                 AND settings.auto_reply
   ```
   `conversa.ai_active` substitui o atual `contacts.ai_enabled` no nível certo (conversa, não
   contato). **Migração:** backfill `conversation.ai_active = contacts.ai_enabled`. Manter
   `contacts.ai_enabled` como "default para novas conversas daquele contato" (compat com a
   tool e o endpoint `toggle-ai`, que passam a operar a conversa ativa).
3. **`transfer_to_human` evoluída** (`agent/tools/transfer_to_human.py`): em vez de só
   `set_ai_enabled(False)`, passa a:
   - `conversation.ai_active = False`,
   - `conversation.status = 'open'` (sinal "humano assume", como Chatwoot),
   - opcionalmente atribuir à fila/round-robin (§5),
   - manter a tag `transferido_atendente` e o alerta WS `human_transfer_alert`
     (`server/routes/webhook.py:619`) — agora carregando `conversation_id`.
   O contrato/nome da tool **não muda** (regra do CLAUDE.md: nome de tool é identidade).
4. **Devolver ao bot**: ação de UI "Reativar IA" → `conversation.ai_active = True`
   (+ status `pending` se quisermos espelhar Chatwoot). Espelha o `toggle-ai` atual.
5. **Decisão de status na chegada**: se `inbox.agent_bot_enabled` e a IA vai responder,
   conversas novas podem nascer `pending` (bot cuidando) e só virar `open` no handoff —
   opcional; no MVP é aceitável nascer `open` e usar `ai_active` como o único sinal, deixando
   a semântica `pending`=bot para a fase de paridade total com Chatwoot.

> **Acoplamento com plugins:** o gate de IA pode ser exposto também via os filtros existentes
> (`filter.llm.messages` / `filter.system_prompt` retornando `None` aborta a chamada ao LLM),
> mas a decisão canônica deve viver no core (cascata acima), não num plugin — plugins ficam
> para casos custom (ex.: horário de funcionamento, já existente).

---

## 7. Impacto no frontend e novos eventos WebSocket

### 7.1 Frontend

A sidebar deixa de ser "lista de contatos" e passa a ser **lista de conversas**. Como o
modelo permite **múltiplas conversas por contato** (§3.4), o mesmo contato **pode** aparecer
em mais de uma linha — mas no **MVP a UI simplifica** (§3.6): mostra **uma linha por
conversa ativa atual** (a derivada), parecendo a lista de hoje. A tela de "todas as conversas
deste contato" (e a navegação entre episódios) entra em fase posterior, sem mudar schema.
Mudanças concretas:

- **`Contacts.js` → `Conversations.js`** (ou refatorar internamente): item da lista exibe
  contato + badge de status + avatar do assignee. A lógica de unread/pin/archive migra para
  o nível conversa (mantendo os campos no contato por compat durante a transição).
- **Painel "outras conversas deste contato"** (fase posterior): no detalhe, listar as demais
  conversas (resolvidas e, no futuro multi-inbox, de outros números meus) do mesmo `contact`.
- **Filtros/visões** (acopla [`08-filtros.md`](08-filtros.md)): abas/dropdown por
  **status** (Abertas / Pendentes / Resolvidas / Adiadas), por **assignee**
  ("Minhas" / "Não atribuídas" / "Todas"), e por **inbox** (quando houver >1). Substitui o
  toggle único `showArchived` (`Contacts.js:28`).
- **Tela "Não atribuídas"** — visão dedicada (fila FIFO) com botão "Pegar" (atribuir a si).
- **Header da conversa** (`ContactDetail.js`): botões
  **Atribuir** (dropdown de atendentes; gated por RBAC), **Transferir**, **Resolver** /
  **Reabrir**, **Adiar (snooze)**, e o toggle **IA on/off** já existente
  (`handleToggleAI`, `Contacts.js:66`) agora operando `conversation.ai_active`.
- **Indicador de assignee** e de status em cada item e no header.
- Modo escuro: seguir as regras do CLAUDE.md (`wa-*`, `.wa-field`) em toda tela nova.

### 7.2 Novos eventos WebSocket

Emitir via `ConnectionManager.broadcast` (`server/state.py:61`). Propostos:

| Evento | `data` | Quando |
|---|---|---|
| `conversation_created` | `{conversation_id, contact_phone, inbox_id, status}` | nova conversa aberta |
| `conversation_status_changed` | `{conversation_id, status, prev_status, by_user_id?}` | open↔pending↔resolved↔snoozed |
| `conversation_assigned` | `{conversation_id, assignee_user_id, team_id?, by_user_id?}` | atribuição/transferência/desatribuição |
| `conversation_updated` | `{conversation_id, fields:{...}}` | priority/snoozed_until/custom_attributes |
| `conversation_ai_toggled` | `{conversation_id, ai_active}` | substitui/estende `contact_ai_toggled` |

Reusar `human_transfer_alert` (`webhook.py:619`) adicionando `conversation_id`. Os
`new_message` existentes passam a carregar `conversation_id` no payload para o frontend
rotear a mensagem à conversa certa.

### 7.3 Bus de plugins (eventos internos)

Espelhar os eventos do core no bus de plugins do CLAUDE.md (events fire-and-forget):
`conversation.created`, `conversation.status_changed`, `conversation.assigned`,
`conversation.resolved`, `conversation.reopened`. Permite automações de terceiros (ex.:
notificar Slack ao atribuir) sem tocar no core — mesma filosofia dos eventos
`message.saved`/`contact.tagged` já existentes.

---

## 8. Faseamento / MVP

**Fase 0 — pré-requisitos** (outros docs): inbox genérica ([02]) e usuários/RBAC ([03]).
Sem `users`, "atribuir" não tem a quem; sem `inboxes`, o `inbox_id` é fixo na default.

**Fase 1 — MVP "conversa + status"** (entrega o núcleo do pedido):
- As **3 tabelas no formato final** (§3.2): `contacts` reescopado + `contact_inboxes`
  (`source_id`, unique `[inbox_id, source_id]`) + `conversations` (subset:
  `id, display_id, inbox_id, contact_id, contact_inbox_id, status, assignee_user_id,
  opened_at, resolved_at, last_activity_at, ai_active, created_at, updated_at`). **Sem**
  índice de unicidade sobre conversas ativas — múltiplas conversas é o modelo final (§3.4).
- `messages.conversation_id` + backfill (§3.5: contact → 1 contact_inbox + 1 conversa).
- Webhook resolve `contact_inbox` por `[inbox_id, source_id]`, abre/anexa/reabre conversa
  (regra §3.4) e injeta `conversation_id` nos eventos.
- Endpoint `PATCH /api/conversations/{id}` (status + assignee) e `GET /api/conversations`
  (com filtros básicos: status, assignee=me/unassigned/all).
- Frontend: lista de conversas **simplificada** (uma linha por conversa atual derivada —
  §3.6) com abas Abertas/Resolvidas/Não-atribuídas/Minhas; header com Atribuir-a-mim,
  Transferir, Resolver, Reabrir.
- Gate de IA por conversa (`ai_active`) + `transfer_to_human` evoluída.

**Fase 2 — paridade Chatwoot**: `snoozed`/`snoozed_until` + auto-resolução, `priority`,
flag `inbox.agent_bot_enabled` real (por inbox), eventos do bus de plugins, timeline/auditoria
de transições (`conversation_events`), **UI multi-conversa** (painel "outras conversas deste
contato" — §7.1; o schema já suporta, é só UI).

**Fase 3 — automação + multi-canal**: round-robin, presença online de atendentes, caps por
atendente (Agent Capacity), `team_id`/filas por time, saved views ([08]), e — quando o doc 02
trouxer múltiplos números meus — o caso "mesma pessoa em inboxes minhas diferentes" (mesmo
contact, contact_inboxes distintos) e o **merge de contatos** (§3.4.1).

---

## 9. Perguntas em aberto

1. ~~1-por-vez vs múltiplas conversas por contato~~ **DECIDIDO (cliente):** múltiplas
   conversas por contato (modelo Chatwoot, §3.4); schema no formato final, UI simplificada no
   MVP (§3.6). Pergunta residual: a UI mostra só a conversa atual no MVP — quando habilitar o
   painel "outras conversas deste contato"?
2. **Reabertura**: qual a janela `conversation_reopen_window`? Reabrir a `resolved` ou sempre
   criar nova conversa? Configurável por inbox?
3. **`resolved` vs `closed`**: precisamos de um estado terminal imutável (Zendesk-style) além
   de `resolved`, ou `resolved`+reabertura basta (Intercom-style)?
4. **Status inicial com bot ativo**: conversas nascem `open` (simples) ou `pending` quando o
   bot da inbox está ligado (paridade Chatwoot)?
5. **Relação `ai_active` (conversa) × `ai_enabled` (contato) × `agent_bot_enabled` (inbox)**:
   confirmar a cascata e o que o `toggle-ai` atual passa a controlar.
6. **`display_id`**: sequência global ou por inbox? Como gerar de forma concorrência-safe em
   SQLite e Postgres (sequence vs `MAX()+1` com lock)?
7. **Auto-resolução por inatividade**: ligar por padrão? Qual timeout? Reabre se o cliente
   volta?
8. **Grupos de WhatsApp** (`contacts.is_group`): cada grupo é uma conversa? Faz sentido
   "atribuir"/"resolver" um grupo? Possivelmente grupos ficam fora do fluxo de atendimento.
9. **Quem vê o quê**: atendente vê só as conversas dele ou a fila inteira? (decisão de [03])
10. **Migração de `is_archived`**: arquivado vira `resolved`? Ou archive continua ortogonal a
    status?
11. **Merge de contatos** (§3.4.1): precisamos do merge já na fase 2/3, ou o caso "telefones
    diferentes da mesma pessoa" é raro o bastante para ficar manual/adiado? Como re-apontar
    `contact_inboxes` e reatribuir conversas com segurança?
12. **`source_id` no WhatsApp**: usar o número (`5511...`) ou o JID (`...@s.whatsapp.net` /
    `lid`)? Precisa casar com a forma como o doc 02 identifica o remetente e com o
    `group_mentions` (phone vs lid). Definir antes do backfill (§3.5).

---

## 10. Referências

1. [Chatwoot — Database Schema (DrawSQL)](https://drawsql.app/templates/chatwoot)
2. [Chatwoot Developer Docs — List all conversations (status/inbox_id/assignee fields)](https://developers.chatwoot.com/api-reference/conversations-api/list-all-conversations)
3. [Chatwoot DeepWiki — Core Data Models (Conversation/Contact/ContactInbox/Inbox)](https://deepwiki.com/chatwoot/chatwoot/3-core-data-models)
4. [Chatwoot — How to use Agent bots (pending/open handoff, return to bot)](https://www.chatwoot.com/hc/user-guide/articles/1677497472-how-to-use-agent-bots)
5. [Chatwoot DeepWiki — Data Models](https://deepwiki.com/chatwoot/chatwoot/2.1-data-models)
6. [Chatwoot DeepWiki — Inboxes and Channels](https://deepwiki.com/chatwoot/chatwoot/3.5-inboxes-and-channels)
7. [Chatwoot — Assigning conversations in a round-robin fashion](https://www.chatwoot.com/hc/user-guide/articles/1677696868-assigning-conversations-in-a-round_robin-fashion)
8. [Chatwoot — Agent Capacity Policies (per-agent caps)](https://www.chatwoot.com/hc/user-guide/articles/1741998212-agent-capacity)
9. [Zendesk — Ticket trigger conditions/actions reference (status flow)](https://support.zendesk.com/hc/en-us/articles/4408893545882-Ticket-trigger-conditions-and-actions-reference)
10. [Zendesk → Intercom migration: status mapping (state machine contrast)](https://clonepartner.com/blog/zendesk-to-intercom-migration-the-2026-technical-guide)
11. [Chatwoot — How to handoff conversations from bot to agent (issue)](https://github.com/chatwoot/rasa-agent-bot-demo/issues/12)
12. [Chatwoot — Assignments / Auto-assign Conversations to Agents](https://www.chatwoot.com/features/assignments/)
13. [Chatwoot — Glossary (terminologia: inbox, conversation, agent)](https://www.chatwoot.com/hc/user-guide/articles/1677141565-chatwoot-glossary)
14. [Chatwoot — `db/schema.rb` (schema canônico: `contacts`, `contact_inboxes` com `source_id` + unique `[inbox_id, source_id]`, `conversations` com `contact_inbox_id`/`inbox_id`/`contact_id`/`display_id`/`status`)](https://github.com/chatwoot/chatwoot/blob/develop/db/schema.rb)
15. [Chatwoot — Merge contacts (unificar duas pessoas; caso "telefones diferentes da mesma pessoa")](https://www.chatwoot.com/hc/user-guide/articles/1677580195-how-to-merge-contacts)
