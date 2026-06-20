# Plano de Implementação — RBAC, Usuários e Permissões (WhatsBot Pro)

> **Status:** PLANO acionável — **revisado pós-WF1**. Deriva da pesquisa em
> [`docs-pesquisa/03-rbac-usuarios-permissoes.md`](../docs-pesquisa/03-rbac-usuarios-permissoes.md).
> **Tenancy:** uma empresa, servidor único, multi-usuário, **sem multi-tenant**.
> **Modelo escolhido:** RBAC à mão (tabelas no próprio DB) + scoping relacional de inbox.
>
> **Escopo deste plano:** tabelas `users / roles / permissions / role_permissions / user_roles /
> user_sessions / inbox_members`, hashing **Argon2id** (passlib), sessões opaque token server-side,
> catálogo de permissões, middleware de autorização (adaptando `server/auth.py` + `server/app.py`),
> exemptions de webhook intactas, e telas de admin de usuários.

---

## Estado atual (WF1, 2026-06-20)

> Reconciliado contra o **código real** no working tree `b673a61` (verificação fase-a-fase do WF1,
> `_RECONCILIACAO-WF1.md §"Plano 03"`). **Este plano é GREENFIELD: 6/6 fases `nao_feito`.** Nenhuma
> reconstrução — tudo é construção nova; o que muda em relação à 1ª redação é (a) a **numeração da
> migration** (`0007` → **`0009`**, head real agora é `0008_plugin_installed_deps`), (b) **âncoras de
> linha corrigidas** por grep (o plano original foi escrito sobre um snapshot pré-AGNO) e (c) a
> **posição na sequência viva de ondas**.

### Legenda das fases

| Fase | Estado | Resumo do delta verificado |
|---|---|---|
| **Fase 1 — Fundação de dados** | ⬜ `nao_feito` | `db/tables.py` **não** tem `users/roles/permissions/role_permissions/user_roles/user_sessions/inbox_members`. `CORE_TABLES` (`db/tables.py` — âncora `grep -n "CORE_TABLES" db/tables.py`, hoje `:324`) tem **20** tabelas: as **13** originais + **7** `ai_*` (consumidas pela `0007_ai_engine_tables`). Head Alembic real = `0008_plugin_installed_deps`. |
| **Fase 2 — Hashing Argon2id + repos** | ⬜ `nao_feito` | `server/auth.py` (40 linhas) ainda **SHA-256 + token determinístico + `web_password_hash`** (ver §"Estado de `server/auth.py`"). `requirements.txt` **sem** `passlib`/`argon2`. Nenhum repo RBAC em `db/repositories/`. |
| **Fase 3 — Sessões + middleware** | ⬜ `nao_feito` | `server/routes/auth.py` só `login` (`grep -n '@app.post("/api/auth/login")'` → hoje `:29`) + `check` (`:66`); **sem** `logout/me/bootstrap`. `auth_middleware` (`grep -n "async def auth_middleware"` → hoje `:245`) chama `auth_required(settings)` e **não** anexa `request.state.user`. WS `/ws` sem auth. |
| **Fase 4 — Autorização (Require + escopo)** | ⬜ `nao_feito` | Sem `server/deps.py`, sem `Require`, sem `current_user`. Nenhuma rota tem `Depends`. `inbox_members`/`inboxes` ausentes (plano 01) → escopo de inbox nasce **no-op/stub documentado** (P38). |
| **Fase 5 — Admin de usuários** | ⬜ `nao_feito` | Sem `server/routes/users.py`, sem `UsersManager.js`. `LoginScreen.js` ainda senha única. Sem `currentUser` no estado do frontend. |
| **Fase 6 — Limpeza/testes/hardening** | ⬜ `nao_feito` | Sem purge de sessões no lifespan. SHA-256 legado ainda no caminho. `tests/test_endpoints.py` testa só a senha única. |

### Posição na sequência viva (ondas)

> Sequência viva = `_REAVALIACAO-relatorio.md §4` / `_RECONCILIACAO-WF1.md §1.3` (não a do
> `00-plano-mestre`). **Ondas:** 0 = endurecimento do que já shippou · 1 = plano 09
> (`SubprocessService`) · 2 = retrofit P62 (isolar code-in-DB) · **3 = RBAC (este plano 03) + Inbox
> (01)** · 4 = completar 06 · 5+ = 02/04/05/08.

Este plano é **Onda 3**. É a **fundação de 01/04/05/08** (todos consomem `users`/`current_user`/
`request.state.user`) e do plano 07 (auditoria, downstream). Não depende de 09 nem do retrofit P62 —
pode entrar em paralelo com o início do 01, mas **antes** dele no que toca à coluna `assignee_user_id`
(que o 01 cria NULLABLE sem FK — P1 — e o 03 referencia depois).

> **⚠️ Regressão crítica a preservar (Fase 3):** as isenções `/api/webhook` e `/health` no
> `auth_middleware` são **intocáveis** — o GOWA posta no webhook **sem credencial**. Remover o SHA-256
> antes do bootstrap existir quebra o login → **Fases 2–3 num único PR**.

---

## 0. Estado atual (pontos de integração reais)

Levantado direto no código (não da pesquisa). **Âncoras por grep — offsets de linha são aproximados**
(o plano original foi escrito sobre snapshot pré-AGNO; os números abaixo foram re-verificados no WF1):

- **`server/auth.py`** (40 linhas) — SHA-256 puro (`hash_password = sha256(salt+password)`), token
  determinístico global (`generate_token`), `verify_token(token, settings)`, `auth_required(settings)`
  (lê `web_password_hash`). Sem usuários, sem identidade, sem expiração, sem revogação por usuário.
  (Conteúdo exato em §"Estado de `server/auth.py`" abaixo.)
- **`server/app.py` — `auth_middleware`** (`grep -n "async def auth_middleware"` → hoje **`:245`**):
  - `_AUTH_EXEMPT_PREFIXES = ("/static/", "/statics/", "/plugins/", "/api/auth/")` (hoje **`:231`**)
  - `_AUTH_EXEMPT_EXACT = {"/api/webhook", "/health"}` (hoje **`:232`**) — **GOWA posta aqui sem
    credencial; intocável.**
  - `_PLUGIN_SPA_PATHS` (hoje **`:233`**) + `_SPA_PATHS` (hoje **`:239`**) = páginas SPA + screens de
    plugin (`/`, `/painel`, `/sandbox`, `/costs`, `/executions`, `/plugins`, `/tools`, `/wizard` +
    paths de plugin; também `path.startswith(("/contacts/","/executions/"))` em **`:249`**).
  - O middleware só protege `/api/*` quando `auth_required(settings)` é verdadeiro
    (`grep -n "auth_required(settings)"` → hoje **`:258`**), espera `Authorization: Bearer <token>` e
    chama `verify_token`. **Não anexa identidade.**
- **`server/app.py` — `@dataclasses.dataclass ServerDeps`** (`grep -n "class ServerDeps"` → hoje
  **`:47-48`**): container de dependências passado a cada módulo de rota via `register_routes(app, deps)`.
- **`server/app.py` — registro de rotas** (`grep -n "register_routes(app, deps)"` → bloco hoje
  **`:328-344`**): todos os módulos de rota são registrados via `X.register_routes(app, deps)`. Padrão:
  cada arquivo em `server/routes/*.py` define `def register_routes(app, deps):` e declara rotas com
  `@app.post(...)` dentro (fecha sobre `deps`).
- **`server/routes/auth.py`** — `POST /api/auth/login` (rate-limit por IP via `state.login_attempts`,
  `_LOGIN_WINDOW_SECONDS = 15*60`, `_LOGIN_MAX_FAILURES = 5` — hoje `:14-15`) e `GET /api/auth/check`.
  Login compara hash global e devolve `{"token": ...}`. **Não** há `logout`/`me`/`bootstrap`.
- **`server/state.py`** `AppState` — tem `login_attempts: dict[str, deque[float]]` (manter no MVP).
- **`db/tables.py`** — `metadata = MetaData()` (hoje **`:30`**), **20** `Table` objects (Core, sem ORM
  declarativo), `CORE_TABLES = frozenset(t.name for t in metadata.sorted_tables)` (hoje **`:324`**). PKs
  `Integer autoincrement=True`; timestamps em `Float` (epoch) — ex.: `contacts.created_at/updated_at`.
  **Convenção real do projeto: timestamps são `Float` epoch, NÃO `TEXT CURRENT_TIMESTAMP`** (o DDL da
  pesquisa usava TEXT; aqui seguimos o padrão do código — confirma **P56**).
- **`db/repositories/config_repo.py`** — padrão de repo: `from db.engine import get_engine`,
  `from db.tables import X`, statements Core, `with get_engine().connect()/.begin()`, `db.upsert.upsert`.
- **`db/alembic/versions/`** — cadeia real **verificada** (ver §"Cadeia Alembic"). HEAD =
  `0008_plugin_installed_deps`. Nomenclatura: `AAAAMMDD_NNNN_descricao.py`.
- **Frontend** — `web/static/js/services/api.js`: token em `localStorage['whatsbot_token']` (`_getToken`),
  injetado como `Authorization: Bearer` em `_authHeaders`; em 401 limpa o token.
  `web/static/js/components/LoginScreen.js` é a tela de login (senha única hoje).
  `web/static/js/app.js`: `GearMenu` monta os itens de menu; já filtra screens de plugin por `!s.config`.
  Não há `currentUser` no estado hoje.
- **`requirements.txt`** — tem `fastapi`, `sqlalchemy>=2.0,<3.0`. **NÃO** tem passlib/argon2.

> **Dependência cruzada:** `inbox_members`, `inboxes` e `conversations` são definidas nos planos [01]
> (inbox/conversas) e [02] (canais/providers). Este plano **referencia** o escopo de inbox e só
> implementa a fatia RBAC dele. Ver §"Dependências de outros planos".

### Estado de `server/auth.py` (a substituir)

O arquivo inteiro hoje (40 linhas) — o caminho **inteiro** sai na Fase 2/6:

```python
def generate_salt() -> str: ...                          # secrets.token_hex(32)
def hash_password(password, salt) -> str: ...            # sha256(salt+password)  ← SAI
def generate_token(password_hash, salt) -> str: ...      # sha256(hash+salt+"session")  ← SAI
def verify_token(token, settings) -> bool: ...           # compara token global  ← SAI
def auth_required(settings) -> bool: ...                 # bool(web_password_hash)  ← VIRA auth_enabled
```

> `server/app.py:14` importa `from server.auth import auth_required, verify_token` — esse import muda na
> Fase 3 (passa a importar a lógica de sessão). `verify_token`/`generate_token`/`hash_password` somem.

### Cadeia Alembic (verificada)

```
0001_baseline → 0002_message_revoked → 0003_message_reactions → 0004_message_reply_to
  → 0005_contact_pinned → 0006_contact_mention → 0007_ai_engine_tables
  → 0008_plugin_installed_deps   (HEAD)
```

**Os slots `0007` e `0008` JÁ FORAM CONSUMIDOS** por `ai_engine_tables` (tabelas `ai_*` do AGNO) e
`plugin_installed_deps` (pkg_deps), **depois** da 1ª redação deste plano. A migration deste plano
(`rbac_users`), que reservava `0007`, **renumera para `0009`** (ver Fase 1.2 e §"Cadeia Alembic" abaixo).

---

## Fase 1 — Fundação de dados (tabelas + migração + seed) — ⬜ `nao_feito`

**Objetivo:** criar o schema RBAC e semear papéis-sistema, catálogo de permissões e `role_permissions`
default, sem ainda tocar no enforcement.

### 1.1 `db/tables.py` — novos `Table` objects

Adicionar (padrão Core já existente; timestamps em `Float` epoch como o resto do projeto — P56):

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

`inbox_members` **NÃO** é criada aqui — vem do plano [01]/[02] (**P38: bloquear scoping até o [01]**). Se
este plano for entregue antes do [01], criar um **stub mínimo** (`inbox_id`, `user_id`, `role`, PK
composta) com FK só para `users.id` (a FK para `inboxes.id` entra quando a tabela existir). Enquanto o
[01] não chega, o escopo de inbox é **no-op documentado** (atendente vê tudo).

> Importar `PrimaryKeyConstraint`/`ForeignKey`/`Index` no bloco `from sqlalchemy import (...)` de
> `db/tables.py` (alguns já estão lá — conferir por grep). `CORE_TABLES` é derivado automaticamente
> (`frozenset(t.name for t in metadata.sorted_tables)`), então as 6 novas tabelas entram sozinhas; não
> há lista hardcoded a editar.

### 1.2 Migração Alembic

Arquivo: `db/alembic/versions/20260620_0009_rbac_users.py` (ajustar a data no nome conforme o dia).

**Numeração (P82 — encadeamento linear):**
- `revision = "0009_rbac_users"`.
- `down_revision = head real no momento de implementar (hoje "0008_plugin_installed_deps"); número = próximo livre (>=0009)`.
- **NÃO** usar `0006`/`0007`/`0008` como slot novo — `0007` (ai_engine_tables) e `0008`
  (plugin_installed_deps) já foram consumidos; apontar `down_revision` para um deles **ramifica a cadeia
  e quebra o boot** (`alembic upgrade head` vira múltiplos heads).
- Se a ordem de implementação puser outra migration antes desta (ex.: o plano 09 não cria migration, mas
  se algo do 01 entrar antes), reabrir o head real com `alembic heads` no momento e encadear a partir
  dele — pode virar `0010`+.

`upgrade()`:
1. `op.create_table(...)` para as 6 tabelas + índices.
2. **Seed dos papéis-sistema** (`is_system=1`): `admin`, `gestor`, `atendente` via `op.bulk_insert`.
3. **Seed do catálogo de permissões** (§1.3) via `op.bulk_insert`.
4. **Seed de `role_permissions`** default (matriz §1.3) — resolver IDs por `key` com
   `op.get_bind()` + `select`.

`downgrade()`: drop das 6 tabelas (ordem reversa por FK).

> O projeto roda `alembic upgrade head` no boot (`init_db()`); DBs legados sem `alembic_version` são
> stampados em `0001_baseline` antes. Usar tipos genéricos (`sa.Integer/sa.Text/sa.Float`) — nunca
> dialeto-específico — para rodar em SQLite **e** Postgres. Timestamps de seed = `time.time()` (Float).

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

> Matriz default já reflete as decisões **P32** (gestor **atende** → tem `conversation.*`), **P33**
> (`users.manage` **só do admin** → não aparece em `gestor`/`atendente`) e **P36/P42/P43**
> (`quickreply.manage` fica no **atendente**, pois a lista é global, texto puro, sem escopo, e o
> atendente edita).
> **`admin` = curto-circuito** (`roles.key == 'admin'` ⇒ `is_admin=True` ⇒ bypass total). Não semear
> `role_permissions` do admin — evita "esqueci de dar a permissão nova ao admin".

**Critério de pronto:** `alembic upgrade head` cria as 6 tabelas em SQLite e Postgres; `SELECT key FROM
roles` = 3 linhas; `count(permissions)` = 16; `role_permissions` populada conforme a matriz; a cadeia
Alembic permanece **linear** (`alembic heads` → 1 só head).

---

## Fase 2 — Hashing Argon2id + repos — ⬜ `nao_feito`

**Objetivo:** substituir SHA-256 por Argon2id e criar a camada de acesso a dados.

### 2.1 Dependência nova

`requirements.txt`: `passlib[argon2]>=1.7.4` (puxa `argon2-cffi`). Validar build no Dockerfile (Linux);
o PyInstaller/`.spec` é **fora do escopo Pro hoje** (P29 — só Linux/Docker), mas se o EXE voltar,
incluir `argon2`/`argon2-cffi` em hidden-imports.

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
  completo. **P15** (mascarar credencial na borda, sem cifragem no MVP) não se aplica a senha de usuário
  (que é hash one-way) — vale para tokens de canal no plano 02.

### 2.3 Repositórios novos em `db/repositories/` (padrão `config_repo.py`)

- **`user_repo.py`**: `count()`, `count_active_admins()`, `get_by_email`, `get_by_id`, `create`,
  `update`, `set_active`, `set_password`, `update_last_login`, `list_users()` (join roles), `delete`.
- **`role_repo.py`**: `list_roles()`, `get_by_key`, `create/update/delete` (bloquear delete de
  `is_system=1`), `permissions_for_role(role_id)`, `set_role_permissions(role_id, perm_keys)`.
- **`permission_repo.py`**: `list_all()`, `ensure_catalog(catalog)` (upsert idempotente do catálogo no
  boot — cobre permissões novas adicionadas em código sem nova migração; usa `db.upsert.upsert`).
- **`user_role_repo.py`**: `roles_for_user(user_id)`, `set_user_roles(user_id, role_keys)` (schema N:N
  mantido — **P40**; a UI envia 1 papel só, mas o repo aceita lista para abrir caminho a multi-papel).
- **`session_repo.py`**: `create`, `get_active(token)` (valida `expires_at > now`),
  `touch(token, ts)`, `delete(token)`, `delete_for_user(user_id)`, `purge_expired()`.

### 2.4 Serviço de permissões efetivas

Em `server/permissions.py`: `effective_permissions(user_id) -> set[str]` (une permissões de todos os
papéis via `user_roles → role_permissions → permissions`) e `is_admin(user_id) -> bool` (algum papel
com `key=='admin'`).

**Critério de pronto:** teste unitário — `verify_password` True/False corretos; PHC começa com
`$argon2id$`; `effective_permissions(gestor)` retorna o set esperado; `is_admin(admin)` True.

---

## Fase 3 — Sessões server-side + middleware (autenticação) — ⬜ `nao_feito`

**Objetivo:** trocar token global determinístico por sessões opaque token, anexar `request.state.user`
e **preservar 100% das exemptions**.

> **Coordenar Fases 2–3 num único PR** (regressão crítica): remover o SHA-256 antes do bootstrap
> existir derruba o login.

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
**14**), editável pela tela de Configurações. `expires_at = now + session_ttl_days*86400` (epoch Float —
P56). Sem refresh e sem limite de sessões simultâneas no MVP; "logout-all" via
`session_repo.delete_for_user`.

### 3.2 Adaptar `auth_middleware` em `server/app.py`

Âncora por grep: `grep -n "async def auth_middleware" server/app.py` (hoje **`:245`**). Mudanças
cirúrgicas, **preservando `_AUTH_EXEMPT_EXACT` e `_AUTH_EXEMPT_PREFIXES` exatamente** (`:231`/`:232`):

```text
async def auth_middleware(request, call_next):
    path = request.url.path
    # 1. SPA pages + static + plugins-static → passam (igual hoje, ~:249)
    #    inclui path in _SPA_PATHS e startswith("/contacts/","/executions/")
    # 2. _AUTH_EXEMPT_EXACT {"/api/webhook","/health"} → passam (CRÍTICO, :251-252)
    # 3. _AUTH_EXEMPT_PREFIXES inclui "/api/auth/" → passam (login/bootstrap, :253-255)
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
- **`auth_required` → `auth_enabled` (count de users).** O import em `server/app.py:14`
  (`from server.auth import auth_required, verify_token`) muda — passa a importar a lógica de sessão.
  Pré-bootstrap (`count==0`): bloquear `/api/*` exceto `/api/auth/*`, webhook, health — o único caminho
  é `POST /api/auth/bootstrap`.
- **WebSocket `/ws`**: o middleware HTTP não cobre o handshake WS do mesmo modo. Resolver auth do WS
  separadamente em `server/routes/websocket.py` (ler o token Bearer do handshake — query param
  `?token=` ou subprotocolo, já que `Authorization` em WS é limitado no browser). Subtarefa explícita —
  hoje `/ws` não exige token.

### 3.3 `ServerDeps`

Manter `ServerDeps` (`grep -n "class ServerDeps"` → hoje `:47-48`) enxuto — repos são módulos
importáveis direto nas rotas/deps; não inflar o dataclass.

**Critério de pronto:**
- `POST /api/auth/bootstrap` cria admin quando DB vazio; 2ª chamada erra "já existe admin".
- `login` válido cria row em `user_sessions` e devolve o token Bearer no corpo; `me` retorna roles+permissions.
- **`POST /api/webhook` responde sem credencial** (regressão crítica testada). `GET /health` aberto.
- Desativar usuário + `delete_for_user` ⇒ próxima request 401.

---

## Fase 4 — Autorização (Depends por permissão + escopo de inbox) — ⬜ `nao_feito`

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
| `server/routes/ai_engine.py` (CRUD de agentes/prompts/tools code-in-DB) | `agent.manage` (admin para criar tool code-in-DB — ver nota P62) |
| `server/routes/admin.py` (migrate-to-postgres) | só admin / `settings.manage` |
| `server/routes/executions.py` | `audit.read` |
| `server/routes/logs.py` | `settings.manage` |
| `server/routes/sandbox.py` | `agent.manage` |
| `server/routes/setup.py` (wizard/request-apikey) | só admin |

`server/routes/webhook.py` → **nunca** recebe `Require` (é o GOWA). `server/routes/auth.py` → aberto.
Endpoints de **plugin** (`/api/plugins/<id>/*`, montados em `app.include_router` — `grep -n
"include_router" server/app.py` → hoje `:349`) → só "autenticado" no MVP; permissão fina por plugin é
fase futura.

> **Nota P62 (code-in-DB):** o RCE do `ai_tool_installer` **já está mitigado por padrão** pelo
> kill-switch `ai_tools_code_enabled` (default **OFF**, env `WHATSBOT_AI_TOOLS_CODE`) — gateando o
> instalador em `create_app`; tool criada via `ai_engine.py` nasce `enabled=False` (gate humano P63).
> Logo **o checklist "P0 gate admin-only" do relatório §6 está OBSOLETO** — a mitigação shipou antes do
> RBAC. O que o RBAC acrescenta aqui é apenas: gatear a *edição/criação* de tools code-in-DB por
> `agent.manage`/admin. O **isolamento por subprocesso** (P62/P67) NÃO é deste plano — é **retrofit**
> sobre o `SubprocessService` do plano 09 (Onda 2), não um gate de fase inicial.

### 4.3 Filtro relacional nas listagens

Listagens de conversa/contato filtram no SQL por `inbox_id IN (inboxes do user)` quando o usuário não é
admin e não tem `conversation.read_all`. Depende das tabelas do [01]/[02]; sem elas, o atendente vê
tudo (degradação documentada — P38).

**Critério de pronto:**
- Atendente (`conversation.read`) em `PUT /api/config` → 403.
- Gestor em `GET /api/plugins` → 200; atendente → 403.
- Admin → 200 em tudo (curto-circuito).
- `tests/test_endpoints.py`: matriz papel × endpoint (≥1 endpoint por permissão).

---

## Fase 5 — Admin de usuários (backend + frontend) — ⬜ `nao_feito`

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
(`user_repo.count_active_admins() > 1`). Registrar no bloco de registro de rotas
(`grep -n "register_routes(app, deps)"` → hoje `:328-344`): `users_routes.register_routes(app, deps)`.

### 5.2 Frontend

- **`LoginScreen.js`**: campo único → `email + senha`; chamar `POST /api/auth/login`; guardar token
  (se Bearer) e `currentUser`.
- **`services/api.js`**: `login(email,password)`, `logout()`, `getMe()`, `bootstrap(...)`. **Mantém
  Bearer no MVP** (P35): o token opaco de `user_sessions` continua em `localStorage['whatsbot_token']` +
  `Authorization: Bearer` (mudança mínima sobre o que já existe). Cookie HttpOnly fica para 2ª iteração.
- **`app.js`**: ao montar, `getMe()` → `currentUser` (com `permissions[]`, `roles[]`). `GearMenu`
  filtra itens por permissão (Configurações→`settings.manage`, Plugins→`plugins.manage`,
  Custos→`billing.manage`, Usuários→`users.manage`) — análogo ao filtro `!s.config` que já existe.
  Default screen por papel: usuário só com `conversation.*` abre direto na caixa de entrada.
- **`UsersManager.js`** (novo): CRUD de usuários, atribuição de papel (**seletor de papel ÚNICO** —
  P40: 1 papel por usuário no MVP, ainda que o schema seja N:N), reset de senha pelo admin (P37 — sem
  SMTP, gera senha temporária), e botão "encerrar todas as sessões" do usuário (P39 — logout-all).
  Item no `GearMenu` visível só com `users.manage` (**que é exclusivo do admin** — P33). Nova rota SPA
  `/usuarios` → adicionar à tupla `_SPA_PATHS` (`grep -n "_SPA_PATHS" server/app.py` → hoje `:239`) e ao
  bloco de rotas frontend (decorators `@app.get(...)` antes de `async def index(...)` — hoje `:298-308`).
  Telas de gestão (Usuários/Canais/Atributos) são **full-page** como Plugins/Tools/Custos (FQ6).
- **Wizard de bootstrap**: reaproveitar o padrão do `/wizard` (SetupWizard); se `check`/`me` indica
  `needs_bootstrap`, renderizar "criar primeiro administrador" antes de tudo.
- **Modo escuro (obrigatório, CLAUDE.md):** classes `wa-*` (`bg-wa-panel`, `text-wa-text`,
  `border-wa-border`) e `.wa-field` em inputs; testar com `.dark` ligado.

**Critério de pronto:** DB vazio força criar 1º admin; admin cria atendente que loga e vê só a caixa;
deletar o último admin é bloqueado; telas legíveis no modo escuro.

---

## Fase 6 — Limpeza, testes e hardening — ⬜ `nao_feito`

- **Higiene de sessões**: task de background (`server/background.py`, padrão `deps`/`state`) chamando
  `session_repo.purge_expired()` periodicamente; registrar na lista do `lifespan`
  (`grep -n "asyncio.create_task" server/app.py` → bloco hoje `:188-191`).
  > ⚠️ **Coordenação com plano 09**: hoje as 4 tasks core são `asyncio.create_task` hardcoded e
  > canceladas sem await. Se o plano 09 (Onda 1) já tiver entregue o `TaskSupervisor`, registrar a
  > purge via `ctx.spawn_task` em vez de `create_task` cru. Como o 03 (Onda 3) entra **depois** do 09
  > (Onda 1), preferir o supervisor se disponível; senão, `create_task` no lifespan.
- **Remover** o caminho SHA-256 legado de `server/auth.py` e as configs
  `web_password_hash`/`web_password_salt` (P34 força bootstrap — sem modo legado a manter).
- **Auditoria [07]**: `request.state.user.id` já existe → autoria disponível nos eventos do bus
  (`config.changed`, `contact.updated`, …). Deixar o hook pronto (a tabela de auditoria é do [07],
  adiada — P68–P75).
- **Testes** (`tests/test_endpoints.py`, já usa TestClient + SQLite temporário): bootstrap,
  login/logout/me, matriz papel×permissão, **isenção de webhook/health pós-RBAC** (regressão crítica),
  expiração de sessão, bloqueio do último admin, rate-limit de login.
- **Docker**: validar build de `argon2-cffi` no Dockerfile (Linux — P29).

**Critério de pronto:** suíte verde; webhook/health comprovadamente isentos; nenhum `/api/*` (exceto
auth/webhook/health) acessível sem sessão; admin gerencia usuários ponta a ponta.

---

## Resumo de arquivos

**Novos:** `server/permissions.py`, `server/deps.py`, `server/routes/users.py`,
`db/repositories/{user_repo,role_repo,permission_repo,user_role_repo,session_repo}.py`,
`db/alembic/versions/20260620_0009_rbac_users.py` (`down_revision=0008_plugin_installed_deps`),
`web/static/js/components/UsersManager.js`.

**Editados:** `db/tables.py` (6 tabelas + índices); `server/auth.py` (Argon2id; remover SHA-256/token
global; sessões); `server/routes/auth.py` (login/logout/me/bootstrap); `server/app.py` (import `:14`;
middleware `~:245`; registro de `users` `~:328-344`; `_SPA_PATHS` `~:239` + rotas frontend `~:298-308`;
purge no lifespan `~:188-191` — **offsets aproximados, ancorar por grep**); rotas existentes
(`config/plugins/whatsapp/contacts/tags/usage/tools/ai_engine/admin/executions/logs/sandbox/setup`)
anexando `Depends(Require(...))`; `web/static/js/components/LoginScreen.js`,
`web/static/js/services/api.js`, `web/static/js/app.js`; `requirements.txt` (`passlib[argon2]`);
`tests/test_endpoints.py`; `CLAUDE.md` (documentar RBAC ao final).

---

## Cadeia Alembic (apêndice de coordenação — P82)

**Cadeia real verificada** (`db/alembic/versions/`):

```
0001_baseline → 0002_message_revoked → 0003_message_reactions → 0004_message_reply_to
  → 0005_contact_pinned → 0006_contact_mention → 0007_ai_engine_tables
  → 0008_plugin_installed_deps   (HEAD)
```

| Migration deste plano | Slot reservado (1ª redação) | Novo slot | `down_revision` |
|---|---|---|---|
| `rbac_users` | 0007 | **0009** | **head real no momento de implementar (hoje `0008_plugin_installed_deps`)** |

> **Regra P82 (linear):** `down_revision = head real no momento de implementar (hoje
> 0008_plugin_installed_deps); número = próximo livre (>=0009)`. NÃO usar `0006`/`0007`/`0008` como slot
> novo — `0007` (ai_engine_tables) e `0008` (plugin_installed_deps) já foram consumidos; apontar para
> eles ramifica a cadeia e **quebra o boot** (`alembic upgrade head` → múltiplos heads). Vários planos
> "querem" o 0009 — **só um** é o 0009; os demais encadeiam em ordem real de implementação
> (`alembic heads` antes de gerar). Pela sequência viva (relatório §4) a ordem provável é: 09
> (sem migration) → **03 `rbac_users`** → 01 (`inbox_conversations`/`backfill`) → 06 (`ai_agent_links`)
> → 02/04/05/08. Se o 03 entrar primeiro entre os que criam migration, `rbac_users` = **0009**; se algo
> entrar antes, vira **0010**+.

---

## Dependências de outros planos

1. **[01] Inbox e Conversas** — define `inboxes`, `conversations`, `inbox_members`. O **escopo de
   inbox do atendente** (`conversation_in_scope` + filtro de listagens) só funciona pleno com elas.
   Sem o [01], entregar RBAC por papel e deixar o scoping como no-op documentado (atendente vê tudo —
   P38). O 01 cria `assignee_user_id` NULLABLE sem FK (P1) e referencia `users` deste plano depois.
2. **[02] Canais e Providers** — `Inbox` é 1:1 com `Channel`; `channel.manage` cobre isso. Sem [02], a
   permissão existe mas não há o que gerir.
3. **[07] Auditoria** — consome `request.state.user.id` criado aqui; é downstream (depende deste).
   Adiada (P68–P75) — deixar só o hook pronto.
4. **[04] Respostas rápidas** — **resolvido** (P36/P42/P43): lista **global, sem escopo, texto puro**,
   atendente edita. `quickreply.manage` fica no atendente e **não** há escopo de inbox/usuário a gatear
   aqui. A matriz default já reflete isso; sem dependência aberta. A Fase 3 do plano 04 (gatear a escrita
   por `quickreply.manage`) **depende deste plano** (`server/deps.py`+`permissions.py`+`users`).
5. **[06] Motor multiagente** — `agent.manage` cobre prompt/modelo/tools; sem [06] protege as telas
   atuais de agente/tools/`ai_engine`. O code-in-DB (`ai_tool_installer`) já está mitigado por padrão
   pelo kill-switch P62 (default OFF) — o RBAC só acrescenta o gate de edição por `agent.manage`/admin;
   o **isolamento por subprocesso** é retrofit do plano 09 (P62/P67), não deste.

---

## Decisões aplicadas (rastro)

> Todas as perguntas funcionais deste plano foram **decididas** (P32–P40 + P82 no `DECISOES.md`,
> incl. Lote 3). As decisões já estão **incorporadas no corpo do plano acima**; abaixo fica o rastro.
> **Não re-litigar decisão fechada.**

1. **GESTOR atende conversas?** — **✅ P32 (2026-06-19): (a) gestor atende.** Gestor tem `conversation.*`
   na matriz default; gestor que não atende é só não receber membership de inbox.
2. **GESTOR gerencia usuários?** — **✅ P33: (a) exclusivo do admin.** `users.manage` só do admin
   (curto-circuito). Gestor **não** cria/edita usuários.
3. **Migração da senha única** — **✅ P34: (a) forçar criar 1º admin.** Update bloqueia `/api/*` até
   bootstrap; env headless `WHATSBOT_ADMIN_EMAIL`/`WHATSBOT_ADMIN_PASSWORD` para Docker/Coolify. Sem
   modo legado SHA-256.
4. **Transporte da sessão** — **✅ P35: (a) Bearer token opaco no MVP.** Mesmo token opaco de
   `user_sessions` via `Authorization: Bearer`; cookie HttpOnly fica para 2ª iteração (só muda o
   transporte, não a tabela/lógica). O catálogo de permissões já é granular (nota P35).
5. **Quick replies: globais ou por inbox/usuário?** — **✅ P36/P42/P43: globais, atendente edita, sem
   escopo, texto puro.** Lista global única, gate por `quickreply.manage` (que fica com o atendente).
   Sem escopo de inbox/usuário a modelar (P42); sem variáveis (P47).
6. **Recuperação de senha / SMTP?** — **✅ P37: (a) admin reseta.** `POST /api/users/{id}/password`
   define senha temporária; **sem SMTP** no MVP.
7. **`inbox_members` — quem cria a tabela?** — **✅ P38: (b) bloquear scoping até o [01].** Não duplicar
   schema; entregar RBAC por papel e ligar o scoping quando o [01] entregar `inboxes`/`inbox_members`.
   Se [03] for entregue antes do [01], criar o **stub mínimo** (`inbox_id`, `user_id`, `role`, PK
   composta, FK só para `users.id`) e adicionar a FK para `inboxes.id` depois.
8. **Política de sessão** — **✅ P39: 14 dias, editável.** `expires_at = now + sessão (default 14 dias)`,
   duração **configurável** (key `session_ttl_days` no banco / tela de Configurações). **Sem refresh** e
   **sem limite simultâneo** no MVP; **"logout-all"** via `session_repo.delete_for_user`.
9. **Multi-papel por usuário (N:N)?** — **✅ P40: 1 papel por usuário no MVP.** Schema permanece **N:N**
   (custo zero, abre porta para multi-papel depois), mas a **UI expõe seleção única**.
10. **Encadeamento Alembic** — **✅ P82: linear.** `down_revision` = head real no momento de implementar
    (hoje `0008_plugin_installed_deps`); `rbac_users` = **0009** (não renumerar para 0006/0007/0008).
11. **Code-in-DB / RCE (contexto Lote 3)** — **✅ P62: kill-switch FEITO** (default OFF) mitiga por
    padrão; isolamento por subprocesso é **retrofit** (P62/P67) sobre o plano 09 (Onda 2), **fora deste
    plano**. O RBAC só gateia a edição de tools por `agent.manage`/admin. Checklist "P0 admin-only" do
    relatório §6 está **OBSOLETO**.
