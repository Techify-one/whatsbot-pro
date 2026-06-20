# Plano de Implementação — RBAC, Usuários e Permissões (WhatsBot Pro)

> **Status:** PLANO acionável. Deriva da pesquisa em
> [`docs-pesquisa/03-rbac-usuarios-permissoes.md`](../docs-pesquisa/03-rbac-usuarios-permissoes.md).
> **Tenancy:** uma empresa, servidor único, multi-usuário, **sem multi-tenant**.
> **Modelo escolhido:** RBAC à mão (tabelas no próprio DB) + scoping relacional de inbox.
>
> **Escopo deste plano:** tabelas `users / roles / permissions / role_permissions / user_roles /
> user_sessions / inbox_members`, hashing **Argon2id** (passlib), sessões opaque token server-side,
> catálogo de permissões, middleware de autorização (adaptando `server/auth.py` + `server/app.py`),
> exemptions de webhook intactas, e telas de admin de usuários.

---

## 0. Estado atual (pontos de integração reais)

Levantado direto no código (não da pesquisa):

- **`server/auth.py`** (41 linhas) — SHA-256 puro (`hash_password = sha256(salt+password)`), token
  determinístico global (`generate_token`), `verify_token`, `auth_required`. Sem usuários, sem
  identidade, sem expiração, sem revogação por usuário.
- **`server/app.py:204-245`** — `auth_middleware`:
  - `_AUTH_EXEMPT_PREFIXES = ("/static/", "/statics/", "/plugins/", "/api/auth/")` (`:207`)
  - `_AUTH_EXEMPT_EXACT = {"/api/webhook", "/health"}` (`:208`) — **GOWA posta aqui sem credencial; intocável.**
  - `_SPA_PATHS` (`:215`) = páginas SPA + screens de plugin (`/`, `/painel`, `/sandbox`, `/costs`,
    `/executions`, `/plugins`, `/tools`, `/wizard` + paths de plugin).
  - O middleware só protege `/api/*` quando `auth_required(settings)` é verdadeiro (`:234`), espera
    `Authorization: Bearer <token>` e chama `verify_token` (`:239`). **Não anexa identidade.**
- **`server/app.py:46-61`** — `@dataclasses.dataclass ServerDeps`: container de dependências passado a
  cada módulo de rota via `register_routes(app, deps)`.
- **`server/app.py:301-319`** — todos os módulos de rota são registrados via `X.register_routes(app, deps)`.
  Padrão: cada arquivo em `server/routes/*.py` define `def register_routes(app, deps):` e declara rotas
  com `@app.post(...)` dentro (fecha sobre `deps`).
- **`server/routes/auth.py`** — `POST /api/auth/login` (rate-limit por IP via `state.login_attempts`,
  `_LOGIN_WINDOW_SECONDS=15min`, `_LOGIN_MAX_FAILURES=5`) e `GET /api/auth/check`. Login compara hash
  global e devolve `{"token": ...}`.
- **`server/state.py:72`** `AppState` — tem `login_attempts: dict[str, deque[float]]` (`:108`).
- **`db/tables.py`** — `metadata = MetaData()` (`:30`), 12 `Table` objects (Core, sem ORM declarativo),
  `CORE_TABLES = frozenset(...)` (`:207`). PKs `Integer autoincrement=True`; timestamps em `Float`
  (epoch) — ex.: `contacts.created_at/updated_at` (`:61-62`). **Convenção real do projeto: timestamps
  são `Float` epoch, NÃO `TEXT CURRENT_TIMESTAMP`** (o DDL da pesquisa usava TEXT; aqui seguimos o
  padrão do código).
- **`db/repositories/config_repo.py`** — padrão de repo: `from db.engine import get_engine`,
  `from db.tables import X`, statements Core, `with get_engine().connect()/.begin()`, `db.upsert.upsert`.
- **`db/alembic/versions/`** — última migração `20260603_0006_contact_mention.py`. Baseline
  `20260510_0001_baseline.py`. Nomenclatura: `AAAAMMDD_NNNN_descricao.py`.
- **Frontend** — `web/static/js/services/api.js`: token em `localStorage['whatsbot_token']`
  (`_getToken` `:8`), injetado como `Authorization: Bearer` em `_authHeaders` (`:11-14`); em 401 limpa
  o token. `web/static/js/components/LoginScreen.js` é a tela de login (senha única hoje).
  `web/static/js/app.js`: `GearMenu` (`:105`) monta os itens de menu; já filtra screens de plugin por
  `!s.config` (`:287`). Não há `currentUser` no estado hoje.
- **`requirements.txt`** — tem `fastapi`, `sqlalchemy>=2.0,<3.0`. **NÃO** tem passlib/argon2.

> **Dependência cruzada:** `inbox_members`, `inboxes` e `conversations` são definidas nos planos [01]
> (inbox/conversas) e [02] (canais/providers). Este plano **referencia** o escopo de inbox e só
> implementa a fatia RBAC dele. Ver §"Dependências de outros planos".

---

## Fase 1 — Fundação de dados (tabelas + migração + seed)

**Objetivo:** criar o schema RBAC e semear papéis-sistema, catálogo de permissões e `role_permissions`
default, sem ainda tocar no enforcement.

### 1.1 `db/tables.py` — novos `Table` objects

Adicionar (padrão Core já existente; timestamps em `Float` epoch como o resto do projeto):

```python
users = Table("users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False, server_default=""),
    Column("password_hash", Text, nullable=False),   # PHC string Argon2id (salt embutido)
    Column("is_active", Integer, nullable=False, server_default="1"),
    Column("last_login_at", Float),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)
Index("idx_users_email", users.c.email)

roles = Table("roles", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", Text, nullable=False, unique=True),     # 'admin'|'gestor'|'atendente'
    Column("name", Text, nullable=False),                 # rótulo pt-BR
    Column("is_system", Integer, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),
)

permissions = Table("permissions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", Text, nullable=False, unique=True),     # 'conversation.reply' etc.
    Column("description", Text, nullable=False, server_default=""),
)

role_permissions = Table("role_permissions", metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
    Column("permission_id", Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False),
    PrimaryKeyConstraint("role_id", "permission_id"),
)

user_roles = Table("user_roles", metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
    PrimaryKeyConstraint("user_id", "role_id"),
)

user_sessions = Table("user_sessions", metadata,
    Column("id", Text, primary_key=True),                 # secrets.token_urlsafe(32)
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("last_seen_at", Float),
    Column("user_agent", Text),
    Column("ip", Text),
)
Index("idx_sessions_user", user_sessions.c.user_id)
Index("idx_sessions_expires", user_sessions.c.expires_at)
```

`inbox_members` **NÃO** é criada aqui — vem do plano [01]/[02] (P38: bloquear scoping até o [01]). Se
este plano for entregue antes do [01], criar um **stub mínimo** (`inbox_id`, `user_id`, `role`, PK
composta) com FK só para `users.id` (a FK para `inboxes.id` entra quando a tabela existir). Enquanto o
[01] não chega, o escopo de inbox é **no-op documentado** (atendente vê tudo).

> Importar `PrimaryKeyConstraint`/`ForeignKey`/`Index` no bloco `from sqlalchemy import (...)` de
> `db/tables.py:18` (alguns já estão lá — conferir).

### 1.2 Migração Alembic

Arquivo: `db/alembic/versions/20260620_0007_rbac_users.py` (ajustar a data).

`upgrade()`:
1. `op.create_table(...)` para as 6 tabelas + índices.
2. **Seed dos papéis-sistema** (`is_system=1`): `admin`, `gestor`, `atendente` via `op.bulk_insert`.
3. **Seed do catálogo de permissões** (§1.3) via `op.bulk_insert`.
4. **Seed de `role_permissions`** default (matriz §1.3) — resolver IDs por `key` com
   `op.get_bind()` + `select`.

`downgrade()`: drop das 6 tabelas (ordem reversa por FK).

> O projeto roda `alembic upgrade head` no boot (`init_db()`); DBs legados sem `alembic_version` são
> stampados em `0001_baseline` antes. Usar tipos genéricos (`sa.Integer/sa.Text/sa.Float`) — nunca
> dialeto-específico — para rodar em SQLite **e** Postgres.

### 1.3 Catálogo de permissões + matriz default

Constante única em **`server/permissions.py`** (novo) — fonte de verdade pro seed, pro checker e pra
tela de admin:

```python
PERMISSION_CATALOG = [
    ("conversation.read",     "Ler conversas dos inboxes em que é membro"),
    ("conversation.read_all", "Ler conversas de qualquer inbox (ignora membership)"),
    ("conversation.reply",    "Responder conversa"),
    ("conversation.assign",   "Atribuir/transferir conversa"),
    ("conversation.resolve",  "Encerrar/reabrir conversa"),
    ("contact.read",          "Ler dados de contato"),
    ("contact.write",         "Editar dados de contato"),
    ("inbox.manage",          "Criar/editar inboxes e membros"),
    ("channel.manage",        "Configurar canais/números"),
    ("settings.manage",       "Configurações globais"),
    ("plugins.manage",        "Ativar/desativar/configurar plugins"),
    ("billing.manage",        "Recargas/saldo (Techify)"),
    ("agent.manage",          "Prompt/modelo/tools do agente"),
    ("quickreply.manage",     "Respostas rápidas"),
    ("users.manage",          "Criar/editar/desativar usuários e papéis"),
    ("audit.read",            "Ler trilha de auditoria"),
]

ROLE_DEFAULTS = {  # admin via curto-circuito; NÃO listar aqui
    "gestor":    {"conversation.read","conversation.reply","conversation.assign",
                  "conversation.resolve","contact.read","contact.write","channel.manage",
                  "settings.manage","plugins.manage","billing.manage","agent.manage",
                  "quickreply.manage","audit.read"},
    "atendente": {"conversation.read","conversation.reply","conversation.resolve",
                  "contact.read","quickreply.manage"},
}
```

> Matriz default já reflete as decisões P32 (gestor **atende** → tem `conversation.*`), P33
> (`users.manage` **só do admin** → não aparece em `gestor`/`atendente`) e P36/P43 (`quickreply.manage`
> fica no **atendente**, pois a lista é global e o atendente edita).
> **`admin` = curto-circuito** (`roles.key == 'admin'` ⇒ `is_admin=True` ⇒ bypass total). Não semear
> `role_permissions` do admin — evita "esqueci de dar a permissão nova ao admin".

**Critério de pronto:** `alembic upgrade head` cria as 6 tabelas em SQLite e Postgres; `SELECT key FROM
roles` = 3 linhas; `count(permissions)` = 16; `role_permissions` populada conforme a matriz.

---

## Fase 2 — Hashing Argon2id + repos

**Objetivo:** substituir SHA-256 por Argon2id e criar a camada de acesso a dados.

### 2.1 Dependência nova

`requirements.txt`: `passlib[argon2]>=1.7.4` (puxa `argon2-cffi`). Validar build no Dockerfile (Linux)
e no PyInstaller (incluir `argon2`/`argon2-cffi` em hidden-imports do `*.spec` se necessário).

### 2.2 `server/auth.py` — reescrever

- `CryptContext(schemes=["argon2"], deprecated="auto")` com parâmetros OWASP
  (`argon2__memory_cost=19456` (19 MiB), `argon2__time_cost=2`, `argon2__parallelism=1`).
- Novas funções:
  - `hash_password_argon(password) -> str` (PHC string; salt embutido, **sem coluna salt**).
  - `verify_password(password, phc_hash) -> bool`.
  - `new_session_token() -> str` → `secrets.token_urlsafe(32)`.
- `auth_enabled() -> bool`: auth está ligada sse `user_repo.count() > 0` (substitui `auth_required`
  baseado em `web_password_hash`).
- **Remover** `hash_password(password,salt)`/`generate_token`/`verify_token` (SHA-256). **P34 decidiu
  forçar bootstrap do 1º admin** — não há modo legado "senha única" a manter; o caminho antigo sai por
  completo.

### 2.3 Repositórios novos em `db/repositories/` (padrão `config_repo.py`)

- **`user_repo.py`**: `count()`, `count_active_admins()`, `get_by_email`, `get_by_id`, `create`,
  `update`, `set_active`, `set_password`, `update_last_login`, `list_users()` (join roles), `delete`.
- **`role_repo.py`**: `list_roles()`, `get_by_key`, `create/update/delete` (bloquear delete de
  `is_system=1`), `permissions_for_role(role_id)`, `set_role_permissions(role_id, perm_keys)`.
- **`permission_repo.py`**: `list_all()`, `ensure_catalog(catalog)` (upsert idempotente do catálogo no
  boot — cobre permissões novas adicionadas em código sem nova migração).
- **`user_role_repo.py`**: `roles_for_user(user_id)`, `set_user_roles(user_id, role_keys)` (schema N:N
  mantido — P40; a UI envia 1 papel só, mas o repo aceita lista para abrir caminho a multi-papel depois).
- **`session_repo.py`**: `create`, `get_active(token)` (valida `expires_at > now`),
  `touch(token, ts)`, `delete(token)`, `delete_for_user(user_id)`, `purge_expired()`.

### 2.4 Serviço de permissões efetivas

Em `server/permissions.py`: `effective_permissions(user_id) -> set[str]` (une permissões de todos os
papéis via `user_roles → role_permissions → permissions`) e `is_admin(user_id) -> bool` (algum papel
com `key=='admin'`).

**Critério de pronto:** teste unitário — `verify_password` True/False corretos; PHC começa com
`$argon2id$`; `effective_permissions(gestor)` retorna o set esperado; `is_admin(admin)` True.

---

## Fase 3 — Sessões server-side + middleware (autenticação)

**Objetivo:** trocar token global determinístico por sessões opaque token, anexar `request.state.user`
e preservar 100% das exemptions.

### 3.1 Reescrever `server/routes/auth.py`

Manter o rate-limit por IP (`state.login_attempts`). Endpoints (todos sob `/api/auth/*`, **já exempt**
em `_AUTH_EXEMPT_PREFIXES`):

| Método | Endpoint | Comportamento |
|--------|----------|---------------|
| POST | `/api/auth/login` | `{email,password}` → `user_repo.get_by_email` (is_active) → `verify_password` → cria `user_sessions` (`expires_at = now + session_ttl`, default **14 dias**, config editável — P39) → retorna `{token}` opaco no corpo (**Bearer no MVP** — P35) + `{user:{id,name,email,roles,permissions}}`. Atualiza `last_login_at` |
| POST | `/api/auth/logout` | `session_repo.delete(token)` |
| GET | `/api/auth/me` | resolve sessão (Bearer) → `{user:{id,name,email,roles[],permissions[]}}`; 401 se sem sessão |
| POST | `/api/auth/bootstrap` | **só se `user_repo.count()==0`**; cria 1º admin (papel `admin`); auto-trava |
| GET | `/api/auth/check` | compat frontend: `{authenticated, needs_bootstrap}` |

**Bootstrap headless (P34):** no startup, se `count(users)==0` e existem `WHATSBOT_ADMIN_EMAIL`/
`WHATSBOT_ADMIN_PASSWORD`, semear admin automaticamente (Docker/Coolify). Em `init_db()`/lifespan.
Fora desse caso, DB vazio **força** o fluxo `POST /api/auth/bootstrap` (sem modo legado sem senha).

**Duração de sessão (P39):** a TTL é lida de uma config no banco (key `session_ttl_days`, default
**14**), editável pela tela de Configurações. `expires_at = now + session_ttl_days*86400`. Sem refresh
e sem limite de sessões simultâneas no MVP; "logout-all" via `session_repo.delete_for_user`.

### 3.2 Adaptar `server/app.py:204-245` — `auth_middleware`

Mudanças cirúrgicas, **preservando `_AUTH_EXEMPT_EXACT` e `_AUTH_EXEMPT_PREFIXES` exatamente**:

```text
async def auth_middleware(request, call_next):
    path = request.url.path
    # 1. SPA pages + static + plugins-static → passam (igual hoje, :225)
    # 2. _AUTH_EXEMPT_EXACT {"/api/webhook","/health"} → passam (CRÍTICO, :227)
    # 3. _AUTH_EXEMPT_PREFIXES inclui "/api/auth/" → passam (login/bootstrap, :229)
    # 4. demais /api/*:
    #    token = Authorization: Bearer <token opaco>   (P35 — sem cookie no MVP)
    #    sess = await to_thread(session_repo.get_active, token)
    #    if not sess: 401 {"ok":False,"error":"Não autenticado."}
    #    user = user_repo.get_by_id + effective_permissions + is_admin
    #    request.state.user = user_obj    # identidade p/ handlers e auditoria [07]
    #    touch sessão (throttle: só se last_seen > 60s)
```

Atenção:
- O middleware é definido dentro de `create_app` (escopo de `deps`) — pode importar repos diretamente
  (`from db.repositories import user_repo, session_repo`), sem mudar assinaturas.
- Repos são síncronos → `await asyncio.to_thread(...)` (padrão do projeto).
- **`auth_required` → `auth_enabled` (count de users).** Pré-bootstrap (`count==0`): bloquear `/api/*`
  exceto `/api/auth/*`, webhook, health — o único caminho é `POST /api/auth/bootstrap`.
- **WebSocket `/ws`**: o middleware HTTP não cobre o handshake WS do mesmo modo. Resolver auth do WS
  separadamente em `server/routes/websocket.py` (ler o token Bearer do handshake — query param
  `?token=` ou subprotocolo, já que `Authorization` em WS é limitado no browser). Subtarefa explícita —
  hoje `/ws` não exige token.

### 3.3 `ServerDeps`

Manter `ServerDeps` (`server/app.py:46-61`) enxuto — repos são módulos importáveis direto nas
rotas/deps; não inflar o dataclass.

**Critério de pronto:**
- `POST /api/auth/bootstrap` cria admin quando DB vazio; 2ª chamada erra "já existe admin".
- `login` válido cria row em `user_sessions` e devolve o token Bearer no corpo; `me` retorna roles+permissions.
- **`POST /api/webhook` responde sem credencial** (regressão crítica testada). `GET /health` aberto.
- Desativar usuário + `delete_for_user` ⇒ próxima request 401.

---

## Fase 4 — Autorização (Depends por permissão + escopo de inbox)

### 4.1 `server/deps.py` (novo)

```python
async def current_user(request):
    user = getattr(request.state, "user", None)
    if not user: raise HTTPException(401, "Não autenticado.")
    return user

class Require:
    def __init__(self, *perms): self.perms = perms
    async def __call__(self, user=Depends(current_user)):
        if user.is_admin: return user                       # curto-circuito admin
        if not set(self.perms).issubset(user.permissions):
            raise HTTPException(403, "Permissão negada.")
        return user

async def conversation_in_scope(conv_id, user=Depends(Require("conversation.read"))):
    # admin / conversation.read_all → ignora escopo
    # senão EXISTS inbox_members(conv.inbox_id, user.id)  (depende de [01]/[02])
```

> As rotas hoje são declaradas com `@app.post(...)` dentro de `register_routes(app, deps)`.
> `Depends(Require(...))` funciona nesse formato — basta adicionar `user = Depends(Require("..."))`
> na assinatura da função da rota. **Não exige refactor para `APIRouter`.**

### 4.2 Anexar `Depends(Require(...))` às rotas existentes

| Arquivo de rota | Permissão exigida |
|---|---|
| `server/routes/config.py` (PUT `/api/config`) | `settings.manage` |
| `server/routes/plugins.py` (enable/disable/settings/import/delete/restart) | `plugins.manage` |
| `server/routes/whatsapp.py` (reconnect/logout) | `channel.manage` |
| `server/routes/contacts.py` (send/retry/image/audio/react/delete) | `conversation.reply` (+ `conversation_in_scope`) |
| `server/routes/contacts.py` (info/tags/pin) | `contact.write` / `conversation.assign` conforme ação |
| `server/routes/tags.py` | `contact.write` (CRUD de tags globais) |
| `server/routes/usage.py` | `billing.manage` ou `audit.read` |
| `server/routes/tools.py` | `agent.manage` |
| `server/routes/admin.py` (migrate-to-postgres) | só admin / `settings.manage` |
| `server/routes/executions.py` | `audit.read` |
| `server/routes/logs.py` | `settings.manage` |
| `server/routes/sandbox.py` | `agent.manage` |
| `server/routes/setup.py` (wizard/request-apikey) | só admin |

`server/routes/webhook.py` → **nunca** recebe `Require` (é o GOWA). `server/routes/auth.py` → aberto.
Endpoints de **plugin** (`/api/plugins/<id>/*`, montados em `app.include_router` `:324`) → só
"autenticado" no MVP; permissão fina por plugin é fase futura.

### 4.3 Filtro relacional nas listagens

Listagens de conversa/contato filtram no SQL por `inbox_id IN (inboxes do user)` quando o usuário não é
admin e não tem `conversation.read_all`. Depende das tabelas do [01]/[02]; sem elas, o atendente vê
tudo (degradação documentada).

**Critério de pronto:**
- Atendente (`conversation.read`) em `PUT /api/config` → 403.
- Gestor em `GET /api/plugins` → 200; atendente → 403.
- Admin → 200 em tudo (curto-circuito).
- `tests/test_endpoints.py`: matriz papel × endpoint (≥1 endpoint por permissão).

---

## Fase 5 — Admin de usuários (backend + frontend)

### 5.1 `server/routes/users.py` (novo) — sob `Require("users.manage")`

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/users` | lista usuários + papéis + is_active |
| POST | `/api/users` | cria `{email,name,password,role_keys[]}` |
| PUT | `/api/users/{id}` | edita nome/email/papéis |
| POST | `/api/users/{id}/password` | **admin reseta senha** (define temporária — P37, sem SMTP) |
| POST | `/api/users/{id}/logout-all` | encerra todas as sessões do usuário (`session_repo.delete_for_user` — P39) |
| POST | `/api/users/{id}/activate` / `/deactivate` | deactivate → `session_repo.delete_for_user` |
| DELETE | `/api/users/{id}` | remove (bloquear o **último admin**) |
| GET | `/api/roles` | papéis + permissões |
| GET | `/api/permissions` | catálogo (checkboxes da UI) |
| PUT | `/api/roles/{id}/permissions` | (Fase 2 da pesquisa) editar permissões de papel customizado |

**Invariante crítica:** nunca deletar/desativar o último usuário admin ativo
(`user_repo.count_active_admins() > 1`). Registrar em `server/app.py:301-319`:
`users_routes.register_routes(app, deps)`.

### 5.2 Frontend

- **`LoginScreen.js`**: campo único → `email + senha`; chamar `POST /api/auth/login`; guardar token
  (se Bearer) e `currentUser`.
- **`services/api.js`**: `login(email,password)`, `logout()`, `getMe()`, `bootstrap(...)`. **Mantém
  Bearer no MVP** (P35): o token opaco de `user_sessions` continua em `localStorage['whatsbot_token']` +
  `Authorization: Bearer` (mudança mínima sobre o que já existe). Cookie HttpOnly fica para 2ª iteração.
- **`app.js`**: ao montar, `getMe()` → `currentUser` (com `permissions[]`, `roles[]`). `GearMenu`
  (`:105`) filtra itens por permissão (Configurações→`settings.manage`, Plugins→`plugins.manage`,
  Custos→`billing.manage`, Usuários→`users.manage`) — análogo ao filtro `!s.config` (`:287`).
  Default screen por papel: usuário só com `conversation.*` abre direto na caixa de entrada.
- **`UsersManager.js`** (novo): CRUD de usuários, atribuição de papel (**seletor de papel ÚNICO** —
  P40: 1 papel por usuário no MVP, ainda que o schema seja N:N), reset de senha pelo admin (P37 — sem
  SMTP, gera senha temporária), e botão "encerrar todas as sessões" do usuário (P39 — logout-all).
  Item no `GearMenu` visível só com `users.manage` (**que é exclusivo do admin** — P33). Nova rota SPA
  `/usuarios` → adicionar a `_SPA_PATHS` (`server/app.py:216`) e ao bloco de rotas frontend
  (`server/app.py:274-288`).
- **Wizard de bootstrap**: reaproveitar o padrão do `/wizard` (SetupWizard); se `check`/`me` indica
  `needs_bootstrap`, renderizar "criar primeiro administrador" antes de tudo.
- **Modo escuro (obrigatório, CLAUDE.md):** classes `wa-*` (`bg-wa-panel`, `text-wa-text`,
  `border-wa-border`) e `.wa-field` em inputs; testar com `.dark` ligado.

**Critério de pronto:** DB vazio força criar 1º admin; admin cria atendente que loga e vê só a caixa;
deletar o último admin é bloqueado; telas legíveis no modo escuro.

---

## Fase 6 — Limpeza, testes e hardening

- **Higiene de sessões**: task de background (`server/background.py`, padrão `deps`/`state`) chamando
  `session_repo.purge_expired()` periodicamente; registrar na lista do `lifespan`
  (`server/app.py:163-168`).
- **Remover** o caminho SHA-256 legado de `server/auth.py` e as configs
  `web_password_hash`/`web_password_salt` (P34 força bootstrap — sem modo legado a manter).
- **Auditoria [07]**: `request.state.user.id` já existe → autoria disponível nos eventos do bus
  (`config.changed`, `contact.updated`, …). Deixar o hook pronto (a tabela de auditoria é do [07]).
- **Testes** (`tests/test_endpoints.py`, já usa TestClient + SQLite temporário): bootstrap,
  login/logout/me, matriz papel×permissão, isenção de webhook/health pós-RBAC, expiração de sessão,
  bloqueio do último admin, rate-limit de login.
- **Docker/PyInstaller**: validar build/empacotamento de `argon2-cffi`.

**Critério de pronto:** suíte verde; webhook/health comprovadamente isentos; nenhum `/api/*` (exceto
auth/webhook/health) acessível sem sessão; admin gerencia usuários ponta a ponta.

---

## Resumo de arquivos

**Novos:** `server/permissions.py`, `server/deps.py`, `server/routes/users.py`,
`db/repositories/{user_repo,role_repo,permission_repo,user_role_repo,session_repo}.py`,
`db/alembic/versions/20260620_0007_rbac_users.py`, `web/static/js/components/UsersManager.js`.

**Editados:** `db/tables.py` (6 tabelas + índices); `server/auth.py` (Argon2id; remover SHA-256/token
global; sessões); `server/routes/auth.py` (login/logout/me/bootstrap); `server/app.py` (middleware
`:204-245`; registro de `users` `:301-319`; `_SPA_PATHS`/rotas frontend `:216`/`:274-288`; purge no
lifespan `:163-168`); rotas existentes (`config/plugins/whatsapp/contacts/tags/usage/tools/admin/
executions/logs/sandbox`) anexando `Depends(Require(...))`; `web/static/js/components/LoginScreen.js`,
`web/static/js/services/api.js`, `web/static/js/app.js`; `requirements.txt` (`passlib[argon2]`);
`tests/test_endpoints.py`; `CLAUDE.md` (documentar RBAC ao final).

---

## Dependências de outros planos

1. **[01] Inbox e Conversas** — define `inboxes`, `conversations`, `inbox_members`. O **escopo de
   inbox do atendente** (`conversation_in_scope` + filtro de listagens) só funciona pleno com elas.
   Sem o [01], entregar RBAC por papel e deixar o scoping como no-op documentado (atendente vê tudo).
2. **[02] Canais e Providers** — `Inbox` é 1:1 com `Channel`; `channel.manage` cobre isso. Sem [02], a
   permissão existe mas não há o que gerir.
3. **[07] Auditoria** — consome `request.state.user.id` criado aqui; é downstream (depende deste).
4. **[04] Respostas rápidas** — **resolvido** (P36/P42/P43): lista **global, sem escopo**, atendente
   edita. `quickreply.manage` fica no atendente e **não** há escopo de inbox/usuário a gatear aqui. A
   matriz default já reflete isso; sem dependência aberta.
5. **[06] Motor multiagente** — `agent.manage` cobre prompt/modelo/tools; sem [06] protege as telas
   atuais de agente/tools.

---

## Perguntas em aberto

> Todas as perguntas funcionais deste plano foram **decididas** (P32–P40 no `DECISOES.md`). As
> decisões já estão **incorporadas no corpo do plano acima**; abaixo fica o rastro.

1. **GESTOR atende conversas?** — **✅ DECIDIDO (2026-06-19): (a) gestor atende.** (P32) Gestor tem
   `conversation.*` na matriz default; gestor que não atende é só não receber membership de inbox.

2. **GESTOR gerencia usuários?** — **✅ DECIDIDO (2026-06-19): (a) exclusivo do admin.** (P33)
   `users.manage` só do admin (curto-circuito). Gestor **não** cria/edita usuários.

3. **Migração da senha única (forçar bootstrap vs modo legado):** — **✅ DECIDIDO (2026-06-19): (a)
   forçar criar 1º admin.** (P34) Update bloqueia `/api/*` até bootstrap; env headless
   `WHATSBOT_ADMIN_EMAIL`/`WHATSBOT_ADMIN_PASSWORD` para Docker/Coolify.

4. **Transporte da sessão: cookie HttpOnly vs Bearer header.** — **✅ DECIDIDO (2026-06-19): (a) Bearer
   token opaco no MVP.** (P35) Mesmo token opaco de `user_sessions` via `Authorization: Bearer`; cookie
   HttpOnly fica para 2ª iteração (só muda o transporte, não a tabela/lógica).

5. **Quick replies: globais ou por inbox/usuário?** — **✅ DECIDIDO (2026-06-19): globais, atendente
   edita, sem escopo.** (P36/P42/P43) Lista global única, gate por `quickreply.manage` (que fica com o
   atendente). **Não há escopo de inbox/usuário a modelar** (ripple P42) — a permissão `quickreply.manage`
   existe no catálogo e nada mais precisa ser gateado por escopo neste plano.

6. **Recuperação de senha / SMTP disponível?** — **✅ DECIDIDO (2026-06-19): (a) admin reseta.** (P37)
   `POST /api/users/{id}/password` define senha temporária; **sem SMTP** no MVP.

7. **`inbox_members` — quem cria a tabela?** — **✅ DECIDIDO (2026-06-19): (b) bloquear scoping até o
   [01].** (P38) Não duplicar schema; entregar RBAC por papel e ligar o scoping quando o [01] entregar
   `inboxes`/`inbox_members`. Se [03] for entregue antes do [01], criar o **stub mínimo** (`inbox_id`,
   `user_id`, `role`, PK composta, FK só para `users.id`) e adicionar a FK para `inboxes.id` depois.

8. **Política de sessão (duração, lembrar-me, simultâneas).** — **✅ DECIDIDO (2026-06-19): 14 dias,
   editável.** (P39) `expires_at = now + sessão (default 14 dias)`, com a duração **configurável** (key
   no banco / tela de Configurações). **Sem refresh** e **sem limite simultâneo** no MVP; **"logout-all"**
   via `session_repo.delete_for_user` exposto na tela de usuários.

9. **Multi-papel por usuário (`user_roles` N:N) já no MVP?** — **✅ DECIDIDO (2026-06-19): 1 papel por
   usuário no MVP.** (P40) Schema permanece **N:N** (custo zero, abre porta para multi-papel depois), mas
   a **UI expõe seleção única** e cada usuário recebe exatamente 1 papel no MVP.
