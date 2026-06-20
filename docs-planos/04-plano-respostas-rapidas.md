# Plano de Implementação — Respostas Rápidas (Canned Responses) — WhatsBot Pro

> **Status:** PLANO acionável. Deriva da pesquisa em
> [`docs-pesquisa/04-respostas-rapidas.md`](../docs-pesquisa/04-respostas-rapidas.md).
> **Tenancy:** uma empresa, servidor único, multi-usuário, **sem multi-tenant**.
>
> **Escopo deste plano (pós-decisões 2026-06-19):** uma **lista global única** de respostas
> rápidas — tabela `quick_replies` simples (`id`, `short_code` UNIQUE global, `content`,
> timestamps), repositório + endpoints CRUD, gatilho `/` no composer **reusando o mecanismo de
> autocomplete de `@menção` já existente**, **texto puro** (sem variáveis `{{...}}`), cache no
> client + evento de invalidação, e tela de gestão (CRUD) em Preact gateada por
> `quickreply.manage`.
>
> **Simplificações aplicadas (Lote 2):**
> - **P42** — quick replies **SEM escopo**: lista global única, `WHERE` trivial. **Removidas** as
>   colunas `scope`/`inbox_id`/`user_id`, os índices parciais e a precedência. `short_code` com
>   **UNIQUE global**.
> - **P47** — **SEM variáveis** `{{...}}` no MVP: texto puro. **Removidos** o parser
>   `resolveVariables`, o catálogo de placeholders e o preview.
> - **P41** — bloquear `short_code` duplicado (unicidade global).
> - **P43/P48** — atendente cria/edita (lista global); uma só tela gateada por `quickreply.manage`,
>   escondendo opções sem permissão.
>
> A feature é **greenfield** — não existe nada de respostas rápidas hoje.

---

## 0. Estado atual (pontos de integração reais — confirmados no código)

### Frontend — composer e autocomplete `@menção`
Tudo vive em **`web/static/js/components/contacts/ContactDetail.js`** (componente único do chat):

- Estado do input: `const [input, setInput] = useState('')` (`ContactDetail.js:36`).
- `<textarea>` do composer: `ContactDetail.js:1290-1299` — `value=${input}`,
  `onInput=${handleInputChange}`, `onKeyDown=${handleKeyDown}`, `ref=${inputRef}`.
- Estado do menu de menção: `const [mentionMenu, setMentionMenu] = useState(null)`
  (`ContactDetail.js:53`) — shape `{ query, start, index }`.
- **Detecção do token** sob o cursor: `updateMentionMenu(el, val)` (`ContactDetail.js:203-209`)
  faz `val.slice(0, pos).match(/(?:^|\s)@([\p{L}\p{N}_]*)$/u)` e abre/fecha o menu.
  Hoje só roda em grupos (`if (sandbox || !(contact && contact.is_group)) { setMentionMenu(null); return; }`).
- **Candidatos** (puro, usado por render + teclas): `getMentionCandidates(query)`
  (`ContactDetail.js:191-200`) — filtra `members`, fatia em 8.
- **Aplicação** do item escolhido: `applyMention(cand)` (`ContactDetail.js:212-230`) — monta
  `before + insert + after`, `setInput(newVal)`, fecha o menu e reposiciona o caret via
  `setSelectionRange` num `setTimeout(...,0)`.
- **Navegação por teclado** já tratada: `handleKeyDown` (`ContactDetail.js:382-412`) intercepta
  ArrowDown/ArrowUp/Enter/Tab/Escape **quando `mentionMenu` está aberto**, e só então cai no
  `Enter`→`handleSend` (`ContactDetail.js:408-411`).
- **Dropdown** renderizado em `ContactDetail.js:1265-1289` (IIFE dentro do JSX), usando classes
  `wa-*` (`bg-wa-panel`, `border-wa-border`, `bg-wa-hover`, `text-wa-text`) — já dark-mode-safe.
- **Disparo do input** (`handleInputChange`, `ContactDetail.js:233-253`) chama `updateMentionMenu`
  e a lógica de presença; envio é `handleSend` (`ContactDetail.js:447`).

> **Conclusão:** a expansão de `/atalho` é um **segundo menu** (`quickReplyMenu`) clonado do
> de `@menção`, com gatilho `/` e candidatos vindos do banco em vez de `members`. O caminho de
> envio (`handleSend` → `sendMessage` → backend → GOWA) **não muda**.

### Frontend — service e navegação
- **`web/static/js/services/api.js`**: helper `request(method, path, body)` (`api.js:26-39`) com
  auth headers, `Content-Type`, tratamento de 401. Cada endpoint é uma função
  `export async function xyz()` → `request(...)`. É onde entram as funções de quick-replies.
- **GearMenu / navegação principal**: telas de gestão (tags, tools, plugins) já existem como itens
  de menu; a tela de CRUD segue o mesmo padrão.

### Backend — dados e rotas
- **`db/tables.py`** (13 `Table`, Core): padrão de `tags` (`tables.py:116-122`) é o molde — PK
  `Integer autoincrement`, colunas tipadas, `Index(...)`, `unique=True` em coluna.
- **`db/repositories/`**: um arquivo por domínio. `tag_repo.py` é o molde exato (select/insert/
  update/delete via Core, `with get_engine().connect()` / `begin()`).
- **`server/routes/`**: cada módulo expõe `register_routes(app, deps)`; importados em
  `server/app.py:18` e registrados em `server/app.py:304-319`.
- **`server/helpers.py`**: `_ok(data)` / `_err(msg, status)` — formato `{ok, data, error}`.
- **Repos chamados das rotas via `asyncio.to_thread`** (ver `server/routes/tags.py`).
- **Alembic**: versões em `db/alembic/versions/`. Última: `20260603_0006_contact_mention.py`
  (revision `0006_contact_mention`). A próxima migration encadeia a partir do HEAD vigente
  (encadeamento linear — P82).

### Dependências RBAC (para a Fase 4 — autorização fina)
- **`db/repositories/` não tem `user_repo` ainda** — vem do plano 03 (RBAC). O plano 03
  (`docs-planos/03-plano-rbac-usuarios.md`) define `users`, `user_sessions`, `server/deps.py`
  (`current_user`, `Require`), e o catálogo de permissões.
- **`requirements.txt`** não muda para esta feature (sem deps novas, pip ou JS).

---

## 1. Decisões de design (já tomadas — não re-litigar)

1. **Lista global única (P42):** sem escopo. Toda resposta rápida é visível a todos os atendentes.
   Sem colunas `scope`/`inbox_id`/`user_id`, sem precedência, sem índices parciais. `WHERE`
   trivial (`SELECT * FROM quick_replies ORDER BY short_code`).
2. **`short_code` único global (P41):** não dá pra criar dois atalhos com o mesmo `short_code`.
   `UNIQUE` na coluna.
3. **Texto puro (P46/P47):** `content` é texto literal, **sem** variáveis `{{...}}` no MVP. Mídia
   também fica para depois (P46).
4. **Expansão client-side (pesquisa §5):** o atendente vê/edita antes de enviar; envio segue o
   caminho atual sem modificação.
5. **`short_code` armazenado sem a barra** (`oi-anna`); `/` é só o gatilho da UI.
6. **Validação de `short_code` no front (P45):** minúsculas, sem espaços/acentos, não começar com
   `/`, com feedback de erro. Menu abre só com match (comportamento Chatwoot/Slack).
7. **Atendente cria/edita (P43); tela gateada por `quickreply.manage` (P48).** Sem RBAC pronto
   (pré-Fase 4), tudo protegido pelo auth atual (senha única).
8. **Cache no client + evento `whatsbot:quick-replies-changed` (P44).**
9. **Acesso a dados** sempre via SQLAlchemy Core. **Tema escuro** obrigatório (`wa-*` / `.wa-field`).
10. **Banco:** a tabela é trivial e idêntica em SQLite e Postgres — sem necessidade de recursos
    Postgres-only aqui (a decisão global de banco não impacta esta feature).

---

## 2. Modelo de dados — tabela `quick_replies`

### 2.1 `db/tables.py` (novo `Table`)

```python
quick_replies = Table(
    "quick_replies",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("short_code", Text, nullable=False, unique=True),  # atalho SEM a barra, UNIQUE global
    Column("content", Text, nullable=False),                  # texto puro (sem placeholders)
    Column("created_at", Float, nullable=False),              # epoch (padrão do projeto p/ ts)
    Column("updated_at", Float, nullable=False),
)
```

> Sem colunas `scope`/`inbox_id`/`user_id`/`created_by` (removidas por P42). O `UNIQUE` na coluna
> `short_code` já cria o índice único global — não precisa de `Index(...)` extra nem de índices
> parciais.

### 2.2 Migration Alembic

Arquivo: `db/alembic/versions/<DATA>_<NNNN>_quick_replies.py` (gerar com
`alembic revision -m "quick_replies table"` e **revisar à mão** — não confiar no autogenerate com
Core). `down_revision` = HEAD vigente no momento (encadeamento linear — P82).

- `upgrade()`:
  - `op.create_table("quick_replies", ...)` com `sa.Column("short_code", sa.Text(), nullable=False)`
    e `sa.UniqueConstraint("short_code", name="uq_quick_replies_short_code")`.
- `downgrade()`: `op.drop_table("quick_replies")`.

> **Sem migration de amarração de FK** — não há FKs (sem `inbox_id`/`user_id`/`created_by`). A
> migration é autocontida e independente dos planos 01/02/03. Funciona idêntica em SQLite e
> Postgres.

**Critério de pronto:** `alembic upgrade head` cria a tabela em SQLite e Postgres; inserir dois
registros com o mesmo `short_code` viola o `UNIQUE`.

---

## 3. Camada de dados — `db/repositories/quick_reply_repo.py` (novo)

Espelha `tag_repo.py` (Core puro, `get_engine()`). Assinaturas propostas:

```python
def create(*, short_code: str, content: str) -> dict | None   # None em violação de unicidade
def get_by_id(qr_id: int) -> dict | None
def get_by_short_code(short_code: str) -> dict | None
def update(qr_id: int, *, short_code=None, content=None) -> dict | None  # None se short_code colidir
def delete(qr_id: int) -> bool
def list_all() -> list[dict]   # lista global única — usada pela tela de gestão E pelo autocomplete
def exists(short_code: str, exclude_id: int | None = None) -> bool  # checagem amigável pré-insert
```

Detalhes:
- `create`/`update` setam `created_at`/`updated_at` com `time.time()` (epoch float, padrão do
  projeto). Capturar `IntegrityError` como rede de segurança e retornar `None`.
- `list_all()` é um `select(quick_replies).order_by(quick_replies.c.short_code)` — sem `WHERE` de
  escopo. É a mesma lista que abastece a tela de gestão e o autocomplete do composer.
- `exists()` valida unicidade ANTES de inserir, para erro amigável (não depender só do
  `IntegrityError`).

**Critério de pronto:** `python -c "from db.repositories import quick_reply_repo as q; ..."`
cria/lista/atualiza/deleta; unicidade global respeitada.

---

## 4. Backend — endpoints REST (`server/routes/quick_replies.py`, novo)

Módulo novo com `register_routes(app, deps)`, importado em `server/app.py:18` e registrado junto às
demais em `server/app.py:304-319`. Formato `{ok, data, error}` via `server.helpers._ok/_err`.
Repos via `asyncio.to_thread`.

| Método | Endpoint | Descrição | Autorização (Fase 4) |
|---|---|---|---|
| GET | `/api/quick-replies` | Lista **toda** a lista global (mesma lista pro autocomplete e pra gestão) | Atendente autenticado |
| POST | `/api/quick-replies` | Cria (`short_code`, `content`) | `quickreply.manage` |
| PUT | `/api/quick-replies/{id}` | Edita `short_code`/`content` | `quickreply.manage` |
| DELETE | `/api/quick-replies/{id}` | Remove | `quickreply.manage` |

> **Um único GET** — sem `/all` separado nem `?inbox_id=`. A lista é global, então o autocomplete e
> a tela de gestão consomem o mesmo endpoint.

### 4.1 Validação e autorização
- **Formato do `short_code` (P45):** regex `^[a-z0-9][a-z0-9_-]*$` (minúsculas, sem espaços/acentos,
  não começa com `/`). Normalizar no backend também: `strip()`, lowercase, remover `/` inicial.
  A validação primária é no front (P45), o backend é a rede de segurança.
- **Unicidade global (P41):** `exists()` para erro amigável (`_err("O atalho '/X' já existe.")`);
  `IntegrityError` como rede de segurança.
- **Autorização (P43/P48)** — via `deps.current_user` + `Require`/permissão:
  - Criar/editar/deletar exige `quickreply.manage`. Pela P43, **atendentes também recebem essa
    permissão** (a lista é global e qualquer atendente pode gerir) — a definição de quais papéis
    têm `quickreply.manage` vive no plano 03; por padrão, atribuir a todos os papéis de atendimento.
  - GET é aberto a qualquer atendente autenticado (precisa do autocomplete).
- **Permissão nova** a registrar no catálogo do plano 03 (`server/permissions.py`):
  `("quickreply.manage", "Criar/editar respostas rápidas")`.

**Fallback pré-Fase 4 (sem RBAC pronto):** sem `current_user`/permissões, todos os endpoints ficam
protegidos só pelo auth atual (senha única). Autorização fina entra na Fase 4.

**Critério de pronto:** `tests/test_endpoints.py` cobre GET/POST/PUT/DELETE + unicidade global.

---

## 5. Frontend — gatilho `/` no composer (`ContactDetail.js`)

**Clonar o mecanismo de `@menção`.** Mudanças cirúrgicas, todas em
`web/static/js/components/contacts/ContactDetail.js`:

1. **Carregar a lista (cache no client — P44)** — novo `useState([])` `quickReplies` carregado uma
   vez (ex.: no boot do app ou na 1ª montagem do chat), **não** por conversa (a lista é global).
   Invalidar via evento global `whatsbot:quick-replies-changed` (a tela de gestão dispara ao
   salvar/excluir). Opcional: refresh por foco da janela se multi-aba incomodar.

2. **Novo estado** `const [quickReplyMenu, setQuickReplyMenu] = useState(null)` — shape igual ao
   `mentionMenu`: `{ query, start, index }`.

3. **Detecção do `/`** — `updateQuickReplyMenu(el, val)` análoga a `updateMentionMenu`
   (`ContactDetail.js:203-209`), regex `val.slice(0, pos).match(/(?:^|\s)\/([\w-]*)$/)`.
   - Abrir **só** com match **E** havendo candidatos (evita conflito com mensagens que começam com
     `/`, ex. URLs — comportamento Chatwoot/Slack, idêntico ao `@menção`). `Escape` fecha.
   - **Não** restringir a grupos (vale para qualquer conversa).
   - Chamar dentro de `handleInputChange` (`ContactDetail.js:233-236`), ao lado de
     `updateMentionMenu`. Mutua-exclusão natural (gatilhos `@` vs `/` distintos).

4. **Candidatos** — `getQuickReplyCandidates(query)` análogo a `getMentionCandidates`
   (`ContactDetail.js:191-200`): filtra por `short_code.includes(q)` (idealmente também por trecho
   do `content`), fatia em 8. Sem precedência (lista única).

5. **Aplicação** — `applyQuickReply(cand)` análogo a `applyMention` (`ContactDetail.js:212-230`):
   - `insert = cand.content` (texto puro literal — **sem** resolução de variáveis).
   - `setInput(before + insert + after)`, fecha menu, reposiciona caret.
   - **NÃO** chama `handleSend` — o atendente revisa.

6. **Teclado** — estender `handleKeyDown` (`ContactDetail.js:382-412`) com bloco gêmeo ao do
   `mentionMenu` (linhas 385-404) para `quickReplyMenu` (ArrowDown/Up/Enter/Tab/Escape).

7. **Dropdown** — clonar o JSX de `ContactDetail.js:1265-1289` num segundo IIFE com `quickReplyMenu`.
   Mostrar `short_code` (com `/` na frente) e preview truncado do `content`. Reusar as classes
   `wa-*` (dark-mode-safe), mesmo posicionamento acima do textarea.

> **Sem `resolveVariables` / catálogo de placeholders / preview** — removido por P47. O conteúdo é
> inserido literal.

**Critério de pronto:** digitar `/oi` abre o dropdown filtrado; Enter/clique insere o `content`
literal no textarea sem enviar; navegação por teclado funciona; legível no modo escuro.

---

## 6. Frontend — tela de gestão (CRUD)

Novo componente **`web/static/js/components/QuickReplies.js`** (default export, Preact+HTM, sem
build), no padrão das telas de gestão existentes (tags/tools).

- **Lista**: `short_code` (prefixado `/`), preview do `content`, ações editar/excluir.
- **Form criar/editar** (modal ou inline):
  - `short_code` — `<input class="wa-field">`, validado ao vivo com `^[a-z0-9][a-z0-9_-]*$`
    (minúsculas, sem espaços/acentos, não começa com `/` — P45), mostrando o erro.
  - `content` — `<textarea class="wa-field">` (texto puro).
- **Excluir** com confirmação.
- **Tema**: `wa-*` + `.wa-field` em todos os campos (regra CLAUDE.md). Testar com modo escuro ligado.
- Ao salvar/excluir: `window.dispatchEvent(new Event('whatsbot:quick-replies-changed'))` para o
  composer recarregar (P44).

> **Sem seletor de escopo, sem seletor de inbox, sem preview de placeholders** — removidos por
> P42/P47. Form de dois campos apenas.

### 6.1 Service (`web/static/js/services/api.js`)
Adicionar (padrão `request(...)`):
```js
export async function getQuickReplies()           // GET    /api/quick-replies
export async function createQuickReply(data)      // POST   /api/quick-replies
export async function updateQuickReply(id, data)  // PUT    /api/quick-replies/{id}
export async function deleteQuickReply(id)         // DELETE /api/quick-replies/{id}
```

### 6.2 Navegação / acesso (RBAC — P48)
Registrar a tela no menu (GearMenu / navegação principal). **Esconder** o item de quem não tem
`quickreply.manage` (não mostrá-lo travado — P48). Pré-Fase 4, a tela fica visível sob o auth atual.

**Critério de pronto:** quem tem `quickreply.manage` cria/edita/exclui atalhos pela tela; composer
recarrega após salvar; o item fica oculto sem a permissão; modo escuro OK.

---

## 7. Testes

Estender **`tests/test_endpoints.py`** (FastAPI TestClient, SQLite temporário):
- CRUD completo de `/api/quick-replies` (criar, listar, editar, excluir).
- Unicidade global (dois `short_code` iguais → erro amigável).
- (Pós-RBAC) acesso sem `quickreply.manage` → 403 nos endpoints de escrita.

Frontend: sem framework JS de teste no projeto — validação manual (digitar `/`, expandir, conferir
inserção literal e dark mode).

**Critério de pronto:** suíte passa com as novas checagens (`check(...)`).

---

## 8. Faseamento / ordem de execução

### Fase 1 — Lista global, texto puro (entrega o `/oi-anna` imediato)
Tabela `quick_replies` (sem FKs), migration, repo (`create`/`list_all`/`update`/`delete`/`exists`),
endpoints CRUD (protegidos pelo auth atual), service em `api.js`, gatilho `/` no composer expandindo
`content` literal, tela de gestão.
**Critério de pronto:** atendente digita `/` no composer, seleciona um atalho, o texto expande sem
enviar; gestor cria/edita/exclui pela tela; tudo legível no dark mode.

### Fase 2 — Cache + invalidação + polimento
Cache no client da lista global, evento `whatsbot:quick-replies-changed` (P44), validação ao vivo do
`short_code` (P45), filtro por trecho do `content` no dropdown.
**Critério de pronto:** salvar na tela recarrega o composer sem reload; validação bloqueia
`short_code` inválido com mensagem clara.

### Fase 3 — Autorização fina (RBAC)
Depende do plano 03 (`current_user`, `Require`, `server/deps.py`, permissão `quickreply.manage`).
Gatear os endpoints de escrita, 403 para quem não tem a permissão, esconder o item de menu (P48).
**Critério de pronto:** quem não tem `quickreply.manage` não vê a tela nem escreve; testes de 403
passam.

### Fase 4 (futuro) — Mídia/anexos (P46)
Atalho que insere imagem/documento/áudio pré-definido, casando com `sendImage`/`sendAudio`/
`sendDocument` já existentes. **Fora do escopo do MVP.**

### Fase 5 (futuro) — Variáveis `{{...}}` (P47)
Parser de variáveis no front, catálogo de placeholders (`{{contact.name}}`, `{{agent.name}}`, …) e
preview na tela. **Cortada do MVP** por P47 — texto puro até lá.

### Fase 6 (futuro) — Escopo por inbox/usuário (P42)
Reintroduzir `scope`/`inbox_id`/`user_id`, índices parciais e precedência quando houver demanda.
**Cortada do MVP** por P42 — lista global única até lá.

---

## Dependências de outros planos

- **Plano 03 (RBAC/usuários)** — `docs-planos/03-plano-rbac-usuarios.md`: necessário para a
  **Fase 3** (autorização fina) e a permissão `quickreply.manage`. Fornece tabela `users`,
  `current_user`, `server/deps.py` (`Require`) e o catálogo de permissões. **Já existe como plano.**
- **Planos 01/02 (conversas/inboxes):** **não são dependência do MVP** — a lista é global e sem
  escopo (P42). Só voltam a importar se a Fase 6 (escopo por inbox) for retomada no futuro.

> **A Fase 1 é totalmente independente** de qualquer outro plano (tabela autocontida, sem FKs). É o
> caminho recomendado para começar.

---

## Perguntas em aberto

1. **Colisão de `short_code` entre escopos — de-dup ou mostrar todos?**
   - ✅ DECIDIDO (2026-06-19): **N/A — sem escopo (P42).** Lista global única, `short_code` UNIQUE
     global (P41), então não há homônimos a desambiguar. Pergunta cortada.

2. **Índice único por escopo — parcial vs coluna gerada.**
   - ✅ DECIDIDO (2026-06-19): **N/A — sem escopo (P42).** Basta `UNIQUE` na coluna `short_code`
     (idêntico em SQLite e Postgres). Sem índices parciais. Pergunta cortada.

3. **Política RBAC — quem cria o quê.**
   - ✅ DECIDIDO (2026-06-19): **atendente também cria/edita (P43).** Lista global; criar/editar/
     deletar exige `quickreply.manage`, atribuída aos papéis de atendimento no plano 03. Sem
     moderação no MVP.

4. **Carregamento da lista no composer — refetch vs cache.**
   - ✅ DECIDIDO (2026-06-19): **cache no client + evento `whatsbot:quick-replies-changed` (P44)**;
     refresh por foco da janela se multi-aba incomodar.

5. **Conflito do gatilho `/` com mensagens que começam com barra.**
   - ✅ DECIDIDO (2026-06-19): **abrir o menu só com match E havendo candidatos; `Escape` fecha**
     (comportamento Chatwoot/Slack, idêntico ao `@menção`).

6. **Suporte a mídia/anexos em atalhos.**
   - ✅ DECIDIDO (2026-06-19): **só texto no MVP (P46);** mídia vira a Fase 4 futura. **Não** reservar
     colunas `media_*` agora (P47 deixou o schema mínimo).

7. **Variáveis `{{...}}`.**
   - ✅ DECIDIDO (2026-06-19): **fora do MVP (P47) — texto puro.** Sem parser, sem catálogo de
     placeholders, sem preview. Vira a Fase 5 futura. Pergunta cortada.

8. **Tela de gestão na navegação — atendente vê?**
   - ✅ DECIDIDO (2026-06-19): **uma só tela, gateada por `quickreply.manage` (P48);** o item de menu
     é **escondido** de quem não tem a permissão (não mostrado travado). Como atendentes recebem
     `quickreply.manage` (P43), eles veem a tela.
