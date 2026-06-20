# Plano de Implementação — Respostas Rápidas (Canned Responses) — WhatsBot Pro

> **Status:** PLANO acionável — **completude + endurecimento** (não greenfield destrutivo). Deriva da
> pesquisa em [`docs-pesquisa/04-respostas-rapidas.md`](../docs-pesquisa/04-respostas-rapidas.md) e foi
> reconciliado contra o código real (WF1, ver bloco abaixo).
> **Tenancy:** uma empresa, servidor único, multi-usuário, **sem multi-tenant**.
>
> **Escopo deste plano (pós-decisões 2026-06-19):** uma **lista global única** de respostas
> rápidas — tabela `quick_replies` simples (`id`, `short_code` UNIQUE global, `content`,
> timestamps), repositório + endpoints CRUD, gatilho `/` no composer **reusando o mecanismo de
> autocomplete de `@menção` já existente**, **texto puro** (sem variáveis `{{...}}`), cache no
> client + evento de invalidação, e tela de gestão (CRUD) em Preact gateada por
> `quickreply.manage`.
>
> **Simplificações aplicadas (Lote 2, P42/P47):**
> - **P42** — quick replies **SEM escopo**: lista global única, `WHERE` trivial. **Removidas** as
>   colunas `scope`/`inbox_id`/`user_id`, os índices parciais e a precedência. `short_code` com
>   **UNIQUE global**.
> - **P47** — **SEM variáveis** `{{...}}` no MVP: texto puro. **Removidos** o parser
>   `resolveVariables`, o catálogo de placeholders e o preview.
> - **P41** — bloquear `short_code` duplicado (unicidade global).
> - **P43/P48** — atendente cria/edita (lista global); uma só tela gateada por `quickreply.manage`,
>   escondendo opções sem permissão.

---

## Estado atual (WF1, 2026-06-20)

> Verificado pelo WF1 (8 subagentes read-only) contra o working tree em `b673a61` (árvore de código =
> `58586e1`). Resultado canônico em [`_RECONCILIACAO-WF1.md` §"Plano 04"](_RECONCILIACAO-WF1.md).

**Esta feature é greenfield e AUTOCONTIDA (Fase 1 sem FKs — pode começar imediatamente).** A
reconciliação confirmou por grep que **nenhum artefato de respostas rápidas existe**: sem tabela
`quick_replies`, sem `db/repositories/quick_reply_repo.py`, sem `server/routes/quick_replies.py`, sem
migration. **Os pontos de integração existem e são válidos** — só precisam ser estendidos, não
reconstruídos.

**⚠️ BOOT-BREAKER (corrigido nesta revisão):** a versão anterior deste plano mandava encadear a
migration a partir do head `0006_contact_mention`. **O head real hoje é `0008_plugin_installed_deps`**
(os slots 0007 e 0008 foram consumidos por `ai_engine_tables` + `plugin_installed_deps` DEPOIS que o
plano foi escrito). Se ignorado, `alembic upgrade head` ramifica a cadeia e **quebra o boot**. Toda
referência abaixo já foi corrigida para `down_revision = 0008_plugin_installed_deps`, slot **0009**
(P82, encadeamento linear — ver §2.2).

### Legenda de fases

| Fase | Estado | Observação |
|------|--------|------------|
| **Fase 1** — lista global, texto puro (tabela + migration + repo + rotas + gatilho `/` + tela) | **nao_feito** | Greenfield, **autocontido (sem FKs)** — pode iniciar já. Pontos de integração no composer/`api.js`/registro de rotas confirmados. |
| **Fase 2** — cache + invalidação + validação `short_code` + filtro por trecho | **nao_feito** | Depende da Fase 1. |
| **Fase 3** — autorização fina (RBAC `quickreply.manage`) | **nao_feito** — **bloqueada pelo plano 03** | Sem `server/deps.py`/`permissions.py`/`users` ainda (greenfield no 03). Até lá, auth atual (senha única). |
| **Fase 4** (futuro) — mídia/anexos (P46) | fora do MVP | — |
| **Fase 5** (futuro) — variáveis `{{...}}` (P47) | fora do MVP | — |
| **Fase 6** (futuro) — escopo por inbox/usuário (P42) | fora do MVP | — |

> Drift de números de linha: os planos foram escritos sobre um snapshot pré-AGNO. As âncoras
> **semânticas** (nomes de função, registro de rotas, tabela `tags` como molde) continuam válidas; os
> **offsets** estão aproximados e foram re-ancorados por grep nesta revisão (ver §0). **Sempre use
> `grep`, nunca linha hardcoded, na implementação.**

### Posicionamento na sequência de ondas (relatório §4 / `_RECONCILIACAO-WF1` §1.3)

> Ondas vivas: **0** = endurecimento do que já shippou · **1** = plano 09 (`SubprocessService`) ·
> **2** = retrofit P62 (isolar code-in-DB) · **3** = RBAC (03) + Inbox (01) · **4** = completar 06 ·
> **5+** = 02, **04**, 05 (independentes) · 08 (depois de 01/05/03).

- **Fase 1–2 deste plano = Onda 5+** (autocontido, quick win — pode começar a qualquer momento sem
  esperar 09/03/01). É o caminho recomendado para entregar o `/oi-anna` imediato.
- **Fase 3 deste plano = Onda 3** (depende do RBAC, plano 03 — gatear escrita por `quickreply.manage`,
  item #18 da matriz priorizada).

---

## 0. Pontos de integração reais (re-ancorados por grep, 2026-06-20)

### Frontend — composer e autocomplete `@menção`
Tudo vive em **`web/static/js/components/contacts/ContactDetail.js`** (componente único do chat).
Âncoras confirmadas por grep (offsets aproximados — confira por nome de função, não por linha):

- Estado do input: `const [input, setInput] = useState('')` (`ContactDetail.js:36`).
- Estado do menu de menção: `const [mentionMenu, setMentionMenu] = useState(null)`
  (`ContactDetail.js:53`) — shape `{ query, start, index }`.
- **Candidatos** (puro, usado por render + teclas): `getMentionCandidates(query)`
  (`ContactDetail.js:191`) — filtra `members`, fatia em 8.
- **Detecção do token** sob o cursor: `updateMentionMenu(el, val)` (`ContactDetail.js:203`)
  faz `val.slice(0, pos).match(/(?:^|\s)@([\p{L}\p{N}_]*)$/u)` e abre/fecha o menu.
  Hoje só roda em grupos (`if (sandbox || !(contact && contact.is_group)) { setMentionMenu(null); return; }`).
- **Aplicação** do item escolhido: `applyMention(cand)` (`ContactDetail.js:212`) — monta
  `before + insert + after`, `setInput(newVal)`, fecha o menu e reposiciona o caret via
  `setSelectionRange` num `setTimeout(...,0)`.
- **Disparo do input** (`handleInputChange`, `ContactDetail.js:233`) chama `updateMentionMenu`
  (`:236`) e a lógica de presença.
- **Navegação por teclado** já tratada: `handleKeyDown` (`ContactDetail.js:382`) intercepta
  ArrowDown/ArrowUp/Enter/Tab/Escape **quando `mentionMenu` está aberto** (`:385`), e só então cai no
  `Enter`→`handleSend` (`:410`).
- **`<textarea>` do composer**: `ContactDetail.js:1294-1295` — `onInput=${handleInputChange}`,
  `onKeyDown=${handleKeyDown}` (form em `:1228`).
- **Dropdown** renderizado num IIFE dentro do JSX (`ContactDetail.js:1265-1289`), usando classes
  `wa-*` (`bg-wa-panel`, `border-wa-border`, `bg-wa-hover`, `text-wa-text`) — já dark-mode-safe.
- Envio é `handleSend` (`ContactDetail.js:447`).

> **Conclusão:** a expansão de `/atalho` é um **segundo menu** (`quickReplyMenu`) clonado do
> de `@menção`, com gatilho `/` e candidatos vindos do banco em vez de `members`. O caminho de
> envio (`handleSend` → `sendMessage` → backend → GOWA) **não muda**. **Cuidado cirúrgico para não
> quebrar o `mentionMenu` existente** (mutua-exclusão pelos gatilhos `@` vs `/`).

### Frontend — service e navegação
- **`web/static/js/services/api.js`**: helper `async function request(method, path, body)`
  (`api.js:26`) com auth headers, `Content-Type`, tratamento de 401. Cada endpoint é uma função
  `export async function xyz()` → `request(...)` (ex.: `getConfig` em `:41`). É onde entram as funções
  de quick-replies.
- **GearMenu / navegação principal**: telas de gestão (tags, tools, plugins) já existem como itens
  de menu; a tela de CRUD segue o mesmo padrão.

### Backend — dados e rotas
- **`db/tables.py`** (20 `Table` Core — as 13 originais + 7 `ai_*`): o padrão de `tags`
  (`tables.py:116`) é o molde — PK `Integer autoincrement`, colunas tipadas, `unique=True` em coluna.
- **`db/repositories/`**: um arquivo por domínio. `tag_repo.py` é o molde exato (select/insert/
  update/delete via Core, `with get_engine().connect()` / `begin()`).
- **`server/routes/`**: cada módulo expõe `register_routes(app, deps)`; importados em
  `server/app.py:18` e registrados em **`server/app.py:328-344`** (o plano antigo citava `304-319` —
  drift ~24 linhas, confirmado por grep).
- **`server/helpers.py`**: `_ok(data)` / `_err(msg, status)` — formato `{ok, data, error}`.
- **Repos chamados das rotas via `asyncio.to_thread`** (ver `server/routes/tags.py`).
- **Alembic**: versões em `db/alembic/versions/`. **HEAD real = `0008_plugin_installed_deps`** (cadeia
  `0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007_ai_engine_tables → 0008_plugin_installed_deps`,
  verificada por grep em `down_revision`). A próxima migration encadeia a partir do HEAD real no
  momento de implementar (encadeamento linear — P82).

### Dependências RBAC (para a Fase 3 — autorização fina)
- **`db/repositories/` não tem `user_repo` ainda** — vem do plano 03 (RBAC), que está **greenfield**
  (`nao_feito` em todas as 6 fases). O `server/auth.py` ainda é SHA-256 + senha única; não há `users`,
  `user_sessions`, `server/deps.py` (`current_user`, `Require`) nem catálogo de permissões
  (`server/permissions.py`). O plano 03 (`docs-planos/03-plano-rbac-usuarios.md`) os define.
- **`requirements.txt`** não muda para esta feature (sem deps novas, pip ou JS).

---

## 1. Decisões de design (já tomadas — não re-litigar)

1. **Lista global única (P42):** sem escopo. Toda resposta rápida é visível a todos os atendentes.
   Sem colunas `scope`/`inbox_id`/`user_id`, sem precedência, sem índices parciais. `WHERE`
   trivial (`SELECT * FROM quick_replies ORDER BY short_code`).
2. **`short_code` único global (P41):** não dá pra criar dois atalhos com o mesmo `short_code`.
   `UNIQUE` na coluna.
3. **Texto puro (P46/P47):** `content` é texto literal, **sem** variáveis `{{...}}` no MVP. Mídia
   também fica para depois (P46). Reservar colunas `media_*` agora é **opcional** (P46) — o schema
   mínimo abaixo não as inclui.
4. **Expansão client-side (pesquisa §5):** o atendente vê/edita antes de enviar; envio segue o
   caminho atual sem modificação.
5. **`short_code` armazenado sem a barra** (`oi-anna`); `/` é só o gatilho da UI.
6. **Validação de `short_code` no front (P45):** minúsculas, sem espaços/acentos, não começar com
   `/`, com feedback de erro. Menu abre só com match (comportamento Chatwoot/Slack).
7. **Atendente cria/edita (P43); tela gateada por `quickreply.manage` (P48).** Sem RBAC pronto
   (pré-Fase 3), tudo protegido pelo auth atual (senha única).
8. **Cache no client + evento `whatsbot:quick-replies-changed` (P44).**
9. **Acesso a dados** sempre via SQLAlchemy Core. **Tema escuro** obrigatório (`wa-*` / `.wa-field`).
10. **Banco (decisão global 2026-06-18):** a tabela é trivial e idêntica em SQLite e Postgres — sem
    necessidade de recursos Postgres-only aqui (P42: índice único de coluna funciona nos dois). A
    decisão global de banco não impacta esta feature.
11. **Timestamps em epoch Float (P56):** `created_at`/`updated_at` em `time.time()` — consistente com
    o resto do projeto.

---

## 2. Modelo de dados — tabela `quick_replies`

### 2.1 `db/tables.py` (novo `Table`)

Espelhar o padrão de `tags` (`db/tables.py:116`).

```python
quick_replies = Table(
    "quick_replies",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("short_code", Text, nullable=False, unique=True),  # atalho SEM a barra, UNIQUE global
    Column("content", Text, nullable=False),                  # texto puro (sem placeholders)
    Column("created_at", Float, nullable=False),              # epoch (P56 — padrão do projeto)
    Column("updated_at", Float, nullable=False),
)
```

> Sem colunas `scope`/`inbox_id`/`user_id`/`created_by` (removidas por P42). O `UNIQUE` na coluna
> `short_code` já cria o índice único global — não precisa de `Index(...)` extra nem de índices
> parciais. Adicionar o `Table` ao `CORE_TABLES` (hoje 20 tabelas → 21).

### 2.2 Migration Alembic ⚠️ (boot-breaker corrigido)

Arquivo: `db/alembic/versions/<DATA>_0009_quick_replies.py` (gerar com
`alembic revision -m "quick_replies table"` e **revisar à mão** — não confiar no autogenerate com
Core).

- **`down_revision` = head real no momento de implementar (hoje `0008_plugin_installed_deps`);
  número = próximo livre (>=0009)** — P82, encadeamento linear. **NÃO usar 0006/0007/0008 como slot
  novo** (ramifica a cadeia e quebra o boot, como mandava a versão antiga deste plano).
- Como esta é uma **única** migration (não cria 2+), não há encadeamento interno a coordenar. Se,
  por ordem de execução, outra migration entrar primeiro (ex.: 03 `rbac_users` vira `0009`), então
  `quick_replies` aponta para o head produzido por ela (`0009_rbac_users` → vira `0010`). **A regra é
  "head real no momento de implementar", não o número fixo `0009`.**

- `upgrade()`:
  - `op.create_table("quick_replies", ...)` com `sa.Column("short_code", sa.Text(), nullable=False)`,
    `sa.Column("content", sa.Text(), nullable=False)`, `sa.Column("created_at", sa.Float(),
    nullable=False)`, `sa.Column("updated_at", sa.Float(), nullable=False)` e
    `sa.UniqueConstraint("short_code", name="uq_quick_replies_short_code")`.
- `downgrade()`: `op.drop_table("quick_replies")`.

> **Sem migration de amarração de FK** — não há FKs (sem `inbox_id`/`user_id`/`created_by`). A
> migration é autocontida e independente dos planos 01/02/03. Funciona idêntica em SQLite e
> Postgres.

**Critério de pronto:** `alembic upgrade head` cria a tabela em SQLite e Postgres **sem ramificar a
cadeia** (`alembic heads` mostra um único head); inserir dois registros com o mesmo `short_code` viola
o `UNIQUE`.

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
- `create`/`update` setam `created_at`/`updated_at` com `time.time()` (epoch float, P56). Capturar
  `IntegrityError` como rede de segurança e retornar `None`.
- `list_all()` é um `select(quick_replies).order_by(quick_replies.c.short_code)` — sem `WHERE` de
  escopo. É a mesma lista que abastece a tela de gestão e o autocomplete do composer.
- `exists()` valida unicidade ANTES de inserir, para erro amigável (não depender só do
  `IntegrityError`).
- **Acesso a dados sempre via SQLAlchemy Core** — `with get_engine().connect()` (leitura) /
  `begin()` (escrita), statements de `db/tables`. Nunca `sqlite3` direto.

**Critério de pronto:** `python -c "from db.repositories import quick_reply_repo as q; ..."`
cria/lista/atualiza/deleta; unicidade global respeitada.

---

## 4. Backend — endpoints REST (`server/routes/quick_replies.py`, novo)

Módulo novo com `register_routes(app, deps)`, importado em `server/app.py:18` e registrado junto às
demais em **`server/app.py:328-344`** (ao lado de `tags.register_routes(app, deps)`). Formato
`{ok, data, error}` via `server.helpers._ok/_err`. Repos via `asyncio.to_thread`.

| Método | Endpoint | Descrição | Autorização (Fase 3) |
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
- **Autorização (P43/P48)** — via `deps.current_user` + `Require`/permissão (vindos do plano 03):
  - Criar/editar/deletar exige `quickreply.manage`. Pela P43, **atendentes também recebem essa
    permissão** (a lista é global e qualquer atendente pode gerir) — a definição de quais papéis
    têm `quickreply.manage` vive no plano 03; por padrão, atribuir a todos os papéis de atendimento.
  - GET é aberto a qualquer atendente autenticado (precisa do autocomplete).
- **Permissão nova** a registrar no catálogo do plano 03 (`server/permissions.py`):
  `("quickreply.manage", "Criar/editar respostas rápidas")` (já antecipada em `DECISOES.md` P35/§Notas;
  ainda não está no catálogo porque o plano 03 é greenfield).

**Fallback pré-Fase 3 (sem RBAC pronto):** sem `current_user`/permissões, todos os endpoints ficam
protegidos só pelo auth atual (senha única, `server/auth.py`). Autorização fina entra na Fase 3.

**Critério de pronto:** `tests/test_endpoints.py` cobre GET/POST/PUT/DELETE + unicidade global.

---

## 5. Frontend — gatilho `/` no composer (`ContactDetail.js`)

**Clonar o mecanismo de `@menção`.** Mudanças cirúrgicas, todas em
`web/static/js/components/contacts/ContactDetail.js` — **sem quebrar o `mentionMenu` existente**:

1. **Carregar a lista (cache no client — P44)** — novo `useState([])` `quickReplies` carregado uma
   vez (ex.: no boot do app ou na 1ª montagem do chat), **não** por conversa (a lista é global).
   Invalidar via evento global `whatsbot:quick-replies-changed` (a tela de gestão dispara ao
   salvar/excluir). Opcional: refresh por foco da janela se multi-aba incomodar.

2. **Novo estado** `const [quickReplyMenu, setQuickReplyMenu] = useState(null)` — shape igual ao
   `mentionMenu` (`ContactDetail.js:53`): `{ query, start, index }`.

3. **Detecção do `/`** — `updateQuickReplyMenu(el, val)` análoga a `updateMentionMenu`
   (`ContactDetail.js:203`), regex `val.slice(0, pos).match(/(?:^|\s)\/([\w-]*)$/)`.
   - Abrir **só** com match **E** havendo candidatos (evita conflito com mensagens que começam com
     `/`, ex. URLs — comportamento Chatwoot/Slack, idêntico ao `@menção`). `Escape` fecha.
   - **Não** restringir a grupos (vale para qualquer conversa — diferente do `mentionMenu`, que hoje
     só abre em grupos).
   - Chamar dentro de `handleInputChange` (`ContactDetail.js:233`), ao lado de `updateMentionMenu`
     (`:236`). Mutua-exclusão natural (gatilhos `@` vs `/` distintos).

4. **Candidatos** — `getQuickReplyCandidates(query)` análogo a `getMentionCandidates`
   (`ContactDetail.js:191`): filtra por `short_code.includes(q)` (idealmente também por trecho
   do `content`), fatia em 8. Sem precedência (lista única).

5. **Aplicação** — `applyQuickReply(cand)` análogo a `applyMention` (`ContactDetail.js:212`):
   - `insert = cand.content` (texto puro literal — **sem** resolução de variáveis).
   - `setInput(before + insert + after)`, fecha menu, reposiciona caret.
   - **NÃO** chama `handleSend` — o atendente revisa.

6. **Teclado** — estender `handleKeyDown` (`ContactDetail.js:382`) com bloco gêmeo ao do
   `mentionMenu` (`:385`) para `quickReplyMenu` (ArrowDown/Up/Enter/Tab/Escape). Manter a precedência
   correta: se ambos os menus estivessem abertos (não devem, pelos gatilhos distintos), tratar um por
   vez antes de cair no `Enter`→`handleSend` (`:410`).

7. **Dropdown** — clonar o JSX de `ContactDetail.js:1265-1289` num segundo IIFE com `quickReplyMenu`.
   Mostrar `short_code` (com `/` na frente) e preview truncado do `content`. Reusar as classes
   `wa-*` (dark-mode-safe), mesmo posicionamento acima do textarea.

> **Sem `resolveVariables` / catálogo de placeholders / preview** — removido por P47. O conteúdo é
> inserido literal.

**Critério de pronto:** digitar `/oi` abre o dropdown filtrado; Enter/clique insere o `content`
literal no textarea sem enviar; navegação por teclado funciona; **o `@menção` em grupos continua
funcionando**; legível no modo escuro.

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
- **Tema**: `wa-*` + `.wa-field` em todos os campos (regra CLAUDE.md — cores cruas como `bg-white`
  têm fallback, mas hex inline NÃO; testar com modo escuro ligado).
- Ao salvar/excluir: `window.dispatchEvent(new Event('whatsbot:quick-replies-changed'))` para o
  composer recarregar (P44).

> **Sem seletor de escopo, sem seletor de inbox, sem preview de placeholders** — removidos por
> P42/P47. Form de dois campos apenas.

### 6.1 Service (`web/static/js/services/api.js`)
Adicionar (padrão `request(...)`, `api.js:26`):
```js
export async function getQuickReplies()           // GET    /api/quick-replies
export async function createQuickReply(data)      // POST   /api/quick-replies
export async function updateQuickReply(id, data)  // PUT    /api/quick-replies/{id}
export async function deleteQuickReply(id)         // DELETE /api/quick-replies/{id}
```

### 6.2 Navegação / acesso (RBAC — P48)
Registrar a tela no menu (GearMenu / navegação principal, full-page como Plugins/Tools — FQ6).
**Esconder** o item de quem não tem `quickreply.manage` (não mostrá-lo travado — P48). Pré-Fase 3, a
tela fica visível sob o auth atual (não há `current_user` ainda).

**Critério de pronto:** quem tem `quickreply.manage` cria/edita/exclui atalhos pela tela; composer
recarrega após salvar; o item fica oculto sem a permissão (Fase 3); modo escuro OK.

---

## 7. Testes

Estender **`tests/test_endpoints.py`** (FastAPI TestClient, SQLite temporário):
- CRUD completo de `/api/quick-replies` (criar, listar, editar, excluir).
- Unicidade global (dois `short_code` iguais → erro amigável).
- (Pós-RBAC, Fase 3) acesso sem `quickreply.manage` → 403 nos endpoints de escrita.

Frontend: sem framework JS de teste no projeto — validação manual (digitar `/`, expandir, conferir
inserção literal e dark mode; confirmar que o `@menção` em grupos não regrediu).

**Critério de pronto:** suíte passa com as novas checagens (`check(...)`).

---

## 8. Faseamento / ordem de execução

### Fase 1 — Lista global, texto puro (entrega o `/oi-anna` imediato) — `nao_feito`, **autocontido**
Tabela `quick_replies` (sem FKs), migration **`0009_quick_replies` com `down_revision =
0008_plugin_installed_deps`** (head real hoje), repo (`create`/`list_all`/`update`/`delete`/`exists`),
endpoints CRUD (protegidos pelo auth atual), service em `api.js`, gatilho `/` no composer expandindo
`content` literal, tela de gestão.
**Onda 5+** — pode começar imediatamente (não depende de 09/03/01).
**Critério de pronto:** atendente digita `/` no composer, seleciona um atalho, o texto expande sem
enviar; gestor cria/edita/exclui pela tela; tudo legível no dark mode; `alembic heads` continua único.

### Fase 2 — Cache + invalidação + polimento — `nao_feito`
Cache no client da lista global, evento `whatsbot:quick-replies-changed` (P44), validação ao vivo do
`short_code` (P45), filtro por trecho do `content` no dropdown.
**Onda 5+** (sequência da Fase 1).
**Critério de pronto:** salvar na tela recarrega o composer sem reload; validação bloqueia
`short_code` inválido com mensagem clara.

### Fase 3 — Autorização fina (RBAC) — `nao_feito`, **bloqueada pelo plano 03**
Depende do plano 03 (`current_user`, `Require`, `server/deps.py`, permissão `quickreply.manage` no
`server/permissions.py`). Gatear os endpoints de escrita, 403 para quem não tem a permissão, esconder
o item de menu (P48).
**Onda 3** (junto/depois do RBAC — item #18 da matriz priorizada do WF1).
**Critério de pronto:** quem não tem `quickreply.manage` não vê a tela nem escreve; testes de 403
passam.

### Fase 4 (futuro) — Mídia/anexos (P46)
Atalho que insere imagem/documento/áudio pré-definido, casando com `sendImage`/`sendAudio`/
`sendDocument` já existentes. **Fora do escopo do MVP** (P46 deixou só texto; reservar colunas
`media_*` agora é opcional e este plano optou por não reservar).

### Fase 5 (futuro) — Variáveis `{{...}}` (P47)
Parser de variáveis no front, catálogo de placeholders (`{{contact.name}}`, `{{agent.name}}`, …) e
preview na tela. **Cortada do MVP** por P47 — texto puro até lá.

### Fase 6 (futuro) — Escopo por inbox/usuário (P42)
Reintroduzir `scope`/`inbox_id`/`user_id`, índices parciais e precedência quando houver demanda.
**Cortada do MVP** por P42 — lista global única até lá. (Só volta a importar se ligada ao plano 01.)

---

## Dependências de outros planos

- **Plano 03 (RBAC/usuários)** — `docs-planos/03-plano-rbac-usuarios.md` (**greenfield**, `nao_feito`):
  necessário para a **Fase 3** (autorização fina) e a permissão `quickreply.manage`. Fornece tabela
  `users`, `current_user`, `server/deps.py` (`Require`), `server/permissions.py` e Argon2id. **Já
  existe como plano.** A Fase 3 é **Onda 3** (item #18 do WF1).
- **Planos 01/02 (conversas/inboxes):** **não são dependência do MVP** — a lista é global e sem
  escopo (P42). Só voltam a importar se a Fase 6 (escopo por inbox) for retomada no futuro.

> **A Fase 1 é totalmente independente** de qualquer outro plano (tabela autocontida, sem FKs). É o
> caminho recomendado para começar (quick win, Onda 5+). **Único cuidado crítico: `down_revision` =
> head real (`0008_plugin_installed_deps` hoje), não 0006/0007/0008 como slot novo.**

---

## Coordenação Alembic (P82) — resumo

| Migration | Slot reservado no plano antigo | Slot/`down_revision` corretos |
|---|---|---|
| `quick_replies` | citava head `0006` ⚠️ boot-breaker | **`down_revision = 0008_plugin_installed_deps`**, número = próximo livre (**0009** hoje) |

> Os slots **0007 e 0008 JÁ foram consumidos** por `0007_ai_engine_tables` (AGNO) e
> `0008_plugin_installed_deps` (pkg_deps) — não reaproveitar nenhum deles. Pela sequência viva
> (relatório §4), a ordem provável é 09 (sem migration) → 03 (`rbac_users`) → 01
> (`inbox_conversations`/`backfill`) → 06 (`ai_agent_links`) → 02/**04**/05/08; se outra migration
> entrar antes desta, `quick_replies` encadeia no head que ela produzir (e vira 0010+). **A regra é
> "head real no momento de implementar", não o número fixo.**

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
