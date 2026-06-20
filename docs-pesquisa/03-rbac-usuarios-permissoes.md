# 03 — RBAC, Usuários e Permissões

> **Status:** Fase de PESQUISA. Nenhum código é alterado aqui. Este documento estuda como
> sair da senha única compartilhada para um modelo **multi-usuário com grupos de acesso**,
> compara modelos de autorização (RBAC / ABAC / ReBAC) e bibliotecas, e propõe um desenho
> acionável de schema, autenticação e enforcement.
>
> **Decisão de tenancy (do [`00-visao-geral.md`](00-visao-geral.md)):** uma empresa, servidor
> único, multi-usuário, **sem multi-tenant**. O desenho não deve fechar portas para multi-tenant
> no futuro, mas não paga o custo dele agora.
>
> **Documentos relacionados:** [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md) (de onde vêm
> `Inbox`, `Conversation`, `InboxMember`, atribuição/transferência), [`02-canais-e-providers.md`](02-canais-e-providers.md)
> (cada `Inbox` é 1:1 com um `Channel`), [`07-auditoria.md`](07-auditoria.md) (trilha de "quem fez o quê",
> que só faz sentido depois que existir "quem").

---

## 1. O que existe hoje

A autenticação atual é uma **senha única compartilhada por toda a instância**. Não há usuários,
papéis, nem rastreabilidade de autoria. Todo o estado de auth vive na tabela `config` (key-value).

### `server/auth.py` (arquivo inteiro — ~41 linhas)

Funções utilitárias, todas baseadas em **SHA-256 com salt próprio** (não é um KDF de senha):

- `generate_salt()` (linha ~8) — `secrets.token_hex(32)`.
- `hash_password(password, salt)` (linha ~13) — `sha256(salt + password)`. **SHA-256 puro**, sem
  custo de trabalho (sem stretching), inadequado para senhas (ver §6).
- `generate_token(password_hash, salt)` (linha ~18) — token de sessão **determinístico**:
  `sha256(password_hash + salt + "session")`. Como é determinístico, é o mesmo token para todos,
  só muda quando a senha muda. Não tem expiração, não é por-usuário, não é revogável individualmente.
- `verify_token(token, settings)` (linha ~28) — recalcula o token esperado a partir de
  `web_password_hash` + `web_password_salt` (lidos da config) e compara com `hmac.compare_digest`.
- `auth_required(settings)` (linha ~38) — auth está "ligada" sse existe `web_password_hash`.

### Middleware em `server/app.py` (linhas ~204–245)

```text
_AUTH_EXEMPT_PREFIXES = ("/static/", "/statics/", "/plugins/", "/api/auth/")   # ~L207
_AUTH_EXEMPT_EXACT    = {"/api/webhook", "/health"}                            # ~L208
_SPA_PATHS            = {"/", "/painel", "/sandbox", "/costs", ... , "/wizard"} # ~L215 (+ telas de plugin)

@app.middleware("http")                                                        # ~L220
async def auth_middleware(request, call_next):
    # SPA pages, assets, /api/auth/*, /api/webhook, /health → passam direto
    # Só protege /api/* quando auth_required(settings):
    #   espera header  Authorization: Bearer <token>  e valida verify_token()
    #   senão → 401 {"ok": False, "error": "Não autenticado."}
```

Pontos-chave do estado atual, que orientam o redesenho:

- **`/api/webhook` é isento** (`_AUTH_EXEMPT_EXACT`) — e **precisa continuar isento**, é o GOWA
  fazendo POST sem credencial de usuário. Idem `/health`. Ver §6.
- **`/api/auth/*` é isento** — é onde mora o login. Continua.
- **Telas SPA e estáticos são públicos** — só `/api/*` exige token. A proteção de verdade é na
  API; o frontend é só UX (esconde botões, mas não é a fonte de verdade).
- **Não há autorização** — uma vez autenticado, o usuário pode chamar qualquer `/api/*`. Não
  existe "esse token pode `conversation.reply` mas não `users.manage`".
- **Token global, sem identidade** — o backend não sabe *qual* usuário fez a requisição. Toda a
  feature de auditoria ([07]) e a de atribuição de conversa ([01]) dependem de resolver isso.

> Implicação: precisamos de **(a) identidade** (tabela `users` + login que devolve quem é o
> usuário), **(b) autorização** (papéis/permissões consultados a cada request) e **(c)** manter
> as isenções de webhook/health intactas.

---

## 2. Requisitos

### Grupos de acesso pedidos pelo cliente

| Grupo | Escopo pretendido |
|-------|-------------------|
| **ADM** | Controla **tudo**: usuários, papéis, configurações, canais/inboxes, plugins, recargas/billing, auditoria, todas as conversas. |
| **ATENDENTE** | Só a **tela de responder conversas** e **apenas os inboxes atribuídos a ele**. Não vê configurações, plugins, billing, nem conversas de inboxes onde não é membro. |
| **GESTOR / OPERAÇÕES** (o "grupo de plugins/configurações/painéis/recargas") | Acesso a configurações, plugins, painéis e recargas — mas **não** necessariamente gestão de usuários/papéis (isso fica com o ADM). Pode ou não atender conversas (decidir; ver §10). |

### Requisitos derivados

1. **ADM superusuário** — bypass implícito de checagem fina (todo `*`), ou um papel "admin" que
   detém todas as permissões. Sempre deve existir ao menos um ADM (não deletar o último).
2. **Atendente restrito a inboxes** — não basta o papel; a visibilidade de conversa depende de
   **membership de inbox**. Um atendente só lê/responde conversas de inboxes em que é `InboxMember`
   (ver [01] e [02]). Isso é uma regra **relacional** (usuário↔inbox), não puramente de papel.
3. **Atribuição de conversa** — uma conversa pode ser atribuída a um atendente específico ([01]).
   A tela "minhas conversas" filtra por `assignee = current_user`. "Não atribuídas" é visível para
   quem tem `conversation.assign` dentro dos seus inboxes.
4. **Ligação com canais** ([02]) — cada `Inbox` é 1:1 com um `Channel`. Restringir por inbox já
   restringe por canal/número implicitamente.
5. **Auditoria** ([07]) — toda ação relevante registra `user_id`. Login multiusuário é
   **pré-requisito** da auditoria.
6. **Bootstrap suave** — instalação existente (senha única) precisa migrar sem quebrar; primeiro
   boot pós-update deve permitir criar o primeiro ADM (ver §6).

---

## 3. Comparação dos modelos: RBAC vs ABAC vs ReBAC

### Resumo

- **RBAC (NIST/ANSI INCITS 359-2004)** — permissões agrupadas em **papéis** (job functions);
  usuário recebe papéis, papel detém permissões. O *Core RBAC* define USERS, ROLES, OPS, OBS,
  PRMS, SESSIONS; há extensões *Hierarchical* (herança de papéis), *Static/Dynamic Separation of
  Duty*. Ideal quando os papéis são estáveis e o contexto não importa muito.
- **ABAC** — decisão baseada em **atributos** (do usuário, do recurso, da ação, do ambiente:
  hora, IP, etc.). Mais flexível e dinâmico; vale a pena quando você começa a escrever muito
  `if` condicional sobre atributos. Custo: políticas mais difíceis de auditar/raciocinar.
- **ReBAC (Google Zanzibar / OpenFGA / SpiceDB)** — decisão baseada em **relacionamentos** entre
  entidades (`user X é membro do time Y`, `documento Z pertence à pasta W`). Brilha em grafos de
  permissão profundos (Drive, GitHub). Raramente um app é 100% relacional — costuma ser uma
  fatia de um sistema maior.

### Tabela de prós/contras (para o caso WhatsBot)

| Critério | RBAC | ABAC | ReBAC |
|---|---|---|---|
| **Fit p/ 3 papéis fixos** | Excelente — é exatamente o caso de uso | Exagerado | Exagerado |
| **Curva de aprendizado** | Baixa | Média/Alta (linguagem de política) | Alta (modelar grafo + serviço) |
| **Precisa de serviço externo?** | Não (tabelas no próprio DB) | Não obrigatório | Geralmente sim (OpenFGA/SpiceDB são daemons) |
| **Fit SQLite/Postgres embutido** | Perfeito (4–5 tabelas) | Bom | Ruim — quer um store próprio/daemon |
| **"Atendente só vê seus inboxes"** | Precisa de complemento relacional (membership) | Resolve via atributo `inbox_ids` | Resolve nativamente (`inbox#member@user`) |
| **Auditabilidade da política** | Alta (papel→permissão é legível) | Média | Média (grafo) |
| **Operação / dependências** | Zero deps novas | Lib opcional | +1 serviço para subir, versionar, backupar |
| **Risco p/ um time pequeno** | Baixo | Médio | Alto |

### Recomendação: **RBAC simples + um "scoping" relacional de inbox por cima**

Para **uma empresa com 3 papéis**, ReBAC e ABAC são over-engineering: trazem uma linguagem de
política e/ou um serviço externo (OpenFGA/SpiceDB) para um problema que cabe em 4–5 tabelas
SQL. A literatura é consistente nisso: **RBAC quando os papéis são estáveis e o contexto não
muda**; só suba para ABAC/ReBAC quando começar a escrever muito `if` por atributo ou quando a
autorização virar "tudo é relacionamento".

O **único** requisito que o RBAC clássico não cobre sozinho é *"atendente só vê os inboxes
atribuídos a ele"* — isso é uma relação **usuário↔inbox**, não um papel. A solução **não** é
adotar ReBAC inteiro; é tratar isso como um caso pontual:

- **RBAC decide a *capacidade*** (`conversation.reply`, `inbox.manage`, `settings.manage`, …).
- **A membership de inbox decide o *escopo*** (em *quais* inboxes a capacidade vale), via a tabela
  `inbox_members` que já vem do [01]/[02]. O ADM (que tem `inbox.manage_all` / é admin) ignora o
  escopo e enxerga todos.

Isso é, na prática, um RBAC com um pequeno toque relacional embutido na query — o padrão
pragmático que a indústria descreve como "blend": começa RBAC e adiciona escopo só onde dói, sem
puxar um motor de política inteiro. Se um dia o produto virar multi-tenant ou ganhar hierarquias
de time complexas, aí sim reavaliar ABAC/ReBAC — mas o schema de RBAC abaixo não impede essa
evolução.

---

## 4. Bibliotecas vs implementação própria

| Opção | Modelo | In-process? | Serviço externo | Fit SQLite/PG | Maturidade | Veredito p/ WhatsBot |
|---|---|---|---|---|---|---|
| **Rolar à mão** (tabelas + `Depends`) | RBAC | Sim | Não | Perfeito | N/A (você controla) | **Recomendado** |
| **Casbin (pycasbin)** | RBAC/ABAC/ReBAC | Sim (lib) | Não | Bom (adapters p/ SQLAlchemy) | Alta, multi-linguagem | Plausível se quiser ABAC depois; mas adiciona um DSL e um arquivo de modelo p/ um caso de 3 papéis |
| **Oso / Polar** | Policy-as-code | Sim (lib) | Não (Oso Cloud é pago) | Bom | Alta | Poderoso, mas a linguagem Polar é peso morto p/ 3 papéis |
| **OpenFGA** | ReBAC (Zanzibar) | Não | **Sim** (daemon) | Store próprio | Alta (Auth0/Okta) | Over-engineering + +1 serviço no Coolify |
| **Permify** | ReBAC | Não | **Sim** (daemon) | Store próprio | Média/Alta | Idem |
| **Authzed/SpiceDB** | ReBAC | Não | **Sim** (daemon) | Store próprio | Alta (mais fiel ao Zanzibar) | Idem; centraliza auditoria mas é canhão p/ mosca |

### Recomendação: **implementação própria (RBAC à mão)**

Motivos:

1. **Custo operacional zero.** O projeto roda como container único (Coolify) ou EXE Windows; subir
   e versionar um daemon de autorização (OpenFGA/SpiceDB/Permify) contradiz a simplicidade do
   produto e a decisão "sem multi-tenant". Casbin é in-process e mais defensável, mas o próprio
   ecossistema reconhece que com Casbin **o time assume toda a manutenção da política, sincronização
   e auditoria** — exatamente o que teríamos de fazer à mão de qualquer jeito, só que com uma
   abstração e um arquivo de modelo a mais para aprender.
2. **O fit com SQLAlchemy 2.0 Core já existente é perfeito.** Papéis/permissões viram 4–5 `Table`
   em `db/tables.py` + repos em `db/repositories/`, no mesmíssimo padrão do resto do app. O enforcement
   vira um `Depends` do FastAPI.
3. **Auditabilidade.** Com `role_permissions` como tabela, "quem pode o quê" é uma query trivial —
   bom para a tela de admin e para a auditoria do [07].
4. **Escape hatch.** Se no futuro a complexidade crescer (multi-tenant, hierarquias), Casbin
   (in-process, já fala Python/SQLAlchemy) é o próximo passo natural sem trocar de paradigma.

> **Regra prática:** comece com papéis fixos (enum) e suba para `role_permissions` granular só
> quando precisar de papéis customizados pelo cliente (ver faseamento, §9).

---

## 5. Modelo de dados proposto

DDL ilustrativo (dialeto-agnóstico; na prática vira `Table` objects em `db/tables.py` + migração
Alembic). Prefixos e tipos seguem o padrão do projeto (SQLite default, Postgres opcional).

```sql
-- Usuários
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,            -- (Postgres: BIGSERIAL / UUID)
    email         TEXT NOT NULL UNIQUE,
    name          TEXT,
    password_hash TEXT NOT NULL,                  -- PHC string (algoritmo embutido — ver §6)
    is_active     INTEGER NOT NULL DEFAULT 1,
    last_login_at TEXT,
    created_at    TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at    TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
-- Obs.: NÃO há coluna 'salt' separada. Usar Argon2id/bcrypt via PHC string
--       ($argon2id$v=19$m=...$<salt>$<hash>) que já carrega salt + parâmetros
--       embutidos. O 'salt' próprio do auth.py atual é descontinuado (ver §6).

-- Papéis (no MVP podem ser fixos; tabela permite customização futura)
CREATE TABLE roles (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,             -- 'admin' | 'gestor' | 'atendente'
    name        TEXT NOT NULL,                    -- rótulo exibido (pt-BR)
    is_system   INTEGER NOT NULL DEFAULT 0,       -- 1 = embutido, não deletável
    created_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

-- Permissões (catálogo)
CREATE TABLE permissions (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,             -- 'conversation.reply', 'settings.manage', ...
    description TEXT
);

-- Papel ↔ Permissão (N:N)
CREATE TABLE role_permissions (
    role_id       INTEGER NOT NULL REFERENCES roles(id)       ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- Usuário ↔ Papel (N:N; suporta usuário com mais de um papel)
CREATE TABLE user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- Sessões server-side (recomendado — ver §6). Opcional se for JWT puro.
CREATE TABLE user_sessions (
    id          TEXT PRIMARY KEY,                 -- token opaco (secrets.token_urlsafe)
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    expires_at  TEXT NOT NULL,
    last_seen_at TEXT,
    user_agent  TEXT,
    ip          TEXT
);
```

### Ligação usuário ↔ inbox (escopo do atendente)

**Não criar tabela nova** — reusar `inbox_members` que vem do [01]/[02]:

```sql
-- (definida em 01/02; reproduzida aqui só para a ligação RBAC↔escopo)
CREATE TABLE inbox_members (
    inbox_id INTEGER NOT NULL REFERENCES inboxes(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    role     TEXT,                                 -- papel DENTRO do inbox (ex.: 'agent'), opcional
    PRIMARY KEY (inbox_id, user_id)
);
```

Regra de decisão para conversas (pseudo-SQL do enforcement):

```text
pode_ver_conversa(user, conv):
    if user é admin (ou tem 'conversation.read_all'):  -> True
    else: -> EXISTS(inbox_members WHERE inbox_id = conv.inbox_id AND user_id = user.id)
          AND user tem 'conversation.read'
```

### Catálogo inicial de permissões

Agrupado por domínio (`<recurso>.<ação>`). Permissões "_all" são o escopo global que o ADM tem.

| Permissão | Significado | admin | gestor | atendente |
|---|---|:--:|:--:|:--:|
| `conversation.read` | ler conversas dos inboxes em que é membro | ✅ | ✅ | ✅ |
| `conversation.read_all` | ler conversas de **qualquer** inbox (ignora membership) | ✅ | ➖ | ➖ |
| `conversation.reply` | responder conversa | ✅ | ✅ | ✅ |
| `conversation.assign` | atribuir/transferir conversa (inclui não-atribuídas) | ✅ | ✅ | ➖ |
| `conversation.resolve` | encerrar/reabrir conversa | ✅ | ✅ | ✅ |
| `contact.read` / `contact.write` | ler/editar dados de contato | ✅ | ✅ | ✅/➖ |
| `inbox.manage` | criar/editar inboxes e seus membros | ✅ | ➖ | ➖ |
| `channel.manage` | configurar canais/números ([02]) | ✅ | ✅ | ➖ |
| `settings.manage` | configurações globais do WhatsBot | ✅ | ✅ | ➖ |
| `plugins.manage` | ativar/desativar/configurar plugins | ✅ | ✅ | ➖ |
| `billing.manage` | recargas/saldo (Techify) | ✅ | ✅ | ➖ |
| `agent.manage` | prompt/modelo/tools do(s) agente(s) ([06]) | ✅ | ✅ | ➖ |
| `quickreply.manage` | respostas rápidas ([04]) | ✅ | ✅ | ✅(global?)|
| `users.manage` | criar/editar/desativar usuários e papéis | ✅ | ➖ | ➖ |
| `audit.read` | ler trilha de auditoria ([07]) | ✅ | ✅ | ➖ |

(✅ = tem, ➖ = não tem; valores de `gestor`/`atendente` são proposta inicial — validar §10.)

O **admin** pode ser modelado de dois jeitos: (a) papel `admin` que detém *todas* as permissões
do catálogo (linha por linha em `role_permissions`), ou (b) **curto-circuito** no checker
(`if user.is_admin: return True`). A opção (b) é mais simples e à prova de "esqueci de dar a
permissão nova ao admin"; recomenda-se (b) com `roles.key == 'admin'` ⇒ bypass.

---

## 6. Autenticação

### 6.1 Hashing de senha — migrar para Argon2id

O `hash_password` atual é **SHA-256 puro** (`sha256(salt+password)`), sem custo de trabalho —
inadequado para senhas (vulnerável a brute-force/GPU). Recomendação OWASP 2025/2026: **Argon2id**
(memory-hard, RFC 9106), parâmetros mínimos `m=19 MiB, t=2, p=1` (mais forte: `m=64 MiB, t=3, p=1`,
~100 ms/verify). Alternativa aceitável: **bcrypt** (`cost≥12`).

- Adotar **`passlib[argon2]`** (`CryptContext(schemes=["argon2"], deprecated="auto")`) ou
  diretamente **`argon2-cffi`**. O hash vira uma **PHC string** que já embute algoritmo + salt +
  parâmetros — por isso **a coluna `salt` separada some** do schema novo (o `generate_salt` do
  `auth.py` deixa de ser usado para senha de usuário).
- **Migração dos hashes legados:** não dá para converter SHA-256→Argon2 sem a senha em claro.
  Estratégia: na introdução do multiusuário, a senha única antiga **não migra para um usuário**;
  em vez disso o **bootstrap** (abaixo) cria o primeiro ADM com senha nova. (Opcional: aceitar o
  login legado uma vez e re-hashear na primeira autenticação bem-sucedida, mas como o modelo muda
  de "senha global" para "usuário+senha", o bootstrap explícito é mais limpo.)

### 6.2 Sessão: server-side (cookie) vs JWT

| Critério | Sessão server-side (cookie opaco) | JWT |
|---|---|---|
| Revogação imediata (logout, desativar usuário) | **Trivial** (apaga a row) | Exige blacklist/short-TTL+refresh |
| Stateless / escala horizontal | Precisa de store compartilhado | Sim |
| Fit do projeto | Container único + Postgres/SQLite já presentes | Bom, mas revogação é dor |
| Frontend | Same-origin SPA (Preact) — cookie é natural | Token em header (como hoje) |

**Recomendação: sessão server-side** com token opaco (`secrets.token_urlsafe(32)`) guardado em
`user_sessions`, entregue em **cookie `HttpOnly; Secure; SameSite=Strict`**. Razões: o frontend é
**same-origin** (a SPA Preact é servida pelo mesmo FastAPI), revogação é trivial (desativar um
atendente = `DELETE FROM user_sessions WHERE user_id=…`), e não precisamos da apatridia do JWT
(não há fan-out de microsserviços). Há `Postgres`/`SQLite` disponível para o store; sem Redis novo.

> Se preferir minimizar mudança no frontend (que hoje manda `Authorization: Bearer <token>`),
> dá para manter o token no header em vez de cookie — o mesmo token opaco de `user_sessions`. A
> tabela e a lógica não mudam; só o transporte. **CSRF:** com cookie, aplicar SameSite=Strict +
> (se houver formulários cross-site) double-submit token; com Bearer header, CSRF não se aplica.

### 6.3 Fluxo de login

```
POST /api/auth/login {email, password}
  → busca user por email (is_active=1)
  → pwd_context.verify(password, user.password_hash)   # Argon2id
  → se ok: cria row em user_sessions (expires_at = now + N dias)
           seta cookie HttpOnly  whatsbot_session=<token>   (ou devolve no corpo p/ Bearer)
           atualiza users.last_login_at
  → 200 {ok, data:{user:{id,name,email,roles,permissions}}}

POST /api/auth/logout  → apaga a sessão atual, limpa o cookie
GET  /api/auth/me      → devolve usuário atual + roles + permissões efetivas (p/ o frontend)
```

### 6.4 Primeiro admin / bootstrap

Como não há tabela de usuários hoje, o primeiro boot pós-update precisa de um caminho seguro:

- **Opção recomendada — wizard de bootstrap:** se `SELECT count(*) FROM users = 0`, a SPA força
  uma tela "criar primeiro administrador" (análoga ao `/wizard` atual). Endpoint
  `POST /api/auth/bootstrap` **só funciona enquanto não existir nenhum usuário** (auto-trava
  depois). Cria o user com papel `admin`.
- **Alternativa CLI/env:** semear via variável de ambiente (`WHATSBOT_ADMIN_EMAIL` /
  `WHATSBOT_ADMIN_PASSWORD`) no primeiro boot (útil em Docker/Coolify headless).
- **Seed de papéis/permissões:** uma migração Alembic insere os 3 papéis-sistema (`admin`,
  `gestor`, `atendente`) e o catálogo de permissões com `role_permissions` default (a tabela do §5).
  Papéis `is_system=1` não são deletáveis.

### 6.5 Adaptar o middleware sem quebrar webhooks

O middleware atual (§1) só precisa de três mudanças, **preservando todas as isenções**:

1. **Manter `_AUTH_EXEMPT_EXACT = {"/api/webhook", "/health"}`** intacto — o GOWA continua
   postando sem credencial. **Crítico:** nenhuma checagem nova pode tocar `/api/webhook`.
2. **Trocar `verify_token` por resolução de sessão:** ler o cookie (ou Bearer), buscar em
   `user_sessions` (não expirada) → carregar `user`; se inválido → 401. Em vez de comparar com um
   hash global, o middleware passa a **anexar `request.state.user`** (identidade) para os handlers
   e para a auditoria ([07]).
3. **Autorização fica nas rotas, não no middleware** (§7) — o middleware só garante "autenticado";
   o `Depends` por permissão garante "autorizado". Isso evita uma tabela gigante de path→permissão
   no middleware e mantém a regra perto da rota.

> Os endpoints de **plugin** (`/api/plugins/<id>/*`) hoje passam pela mesma proteção `/api/*`.
> Manter assim: um endpoint de plugin exige sessão válida. (Permissão fina por plugin é fase 2.)

---

## 7. Enforcement

### 7.1 Backend (fonte de verdade) — `Depends` do FastAPI

Padrão **PermissionChecker** (dependency de classe), declarativo por rota:

```python
# server/deps.py  (ilustrativo)
from fastapi import Depends, HTTPException, Request

async def current_user(request: Request):
    user = getattr(request.state, "user", None)   # setado pelo middleware
    if not user:
        raise HTTPException(401, "Não autenticado.")
    return user

class Require:
    def __init__(self, *perms: str):
        self.perms = perms
    async def __call__(self, user=Depends(current_user)):
        if user.is_admin:                          # curto-circuito do admin (§5)
            return user
        if not set(self.perms).issubset(user.permissions):
            raise HTTPException(403, "Permissão negada.")
        return user

# uso na rota:
@router.post("/api/contacts/{phone}/messages")
async def reply(..., user = Depends(Require("conversation.reply"))):
    ...
```

Para o **escopo de inbox** (atendente só nos seus), a checagem de papel não basta — a rota (ou o
repo) precisa aplicar o filtro relacional:

```python
async def conversation_in_scope(conv_id: int, user = Depends(Require("conversation.read"))):
    conv = await get_conversation(conv_id)
    if not user.is_admin and "conversation.read_all" not in user.permissions:
        if not await is_inbox_member(conv.inbox_id, user.id):   # inbox_members
            raise HTTPException(403, "Fora do seu escopo.")
    return conv
```

E nas **listagens** (sidebar de conversas), filtrar no SQL: `WHERE inbox_id IN (inboxes do user)`
em vez de buscar tudo e filtrar depois (performance + não vazar metadados).

### 7.2 Frontend (somente UX) — Preact

O frontend **esconde** telas/botões conforme as permissões devolvidas por `GET /api/auth/me`
(`user.permissions` / `user.roles`), mas **nunca** é a barreira de segurança — o backend re-checa
tudo. Concretamente:

- `app.js` guarda `currentUser` no estado; o `GearMenu` filtra itens (Configurações, Plugins,
  Billing, Usuários) por permissão (igual já filtra screens de plugin com `config:true`).
- A tela de **atendente** vira a default quando o usuário só tem `conversation.*` — abre direto na
  caixa de entrada, sem engrenagem de admin.
- Botões "atribuir", "transferir", "encerrar" aparecem condicionalmente.
- Mesmo que um atendente "force" a URL `/plugins`, o `GET /api/plugins` retorna **403** — a regra
  vale no servidor.

---

## 8. Impacto no código

| Arquivo / área | Mudança |
|---|---|
| `server/auth.py` | Reescrever: trocar SHA-256 por `passlib[argon2]` (`hash_password`/`verify_password`); remover token determinístico global; adicionar criação/validação de sessão (`user_sessions`). Manter assinaturas compatíveis onde der. |
| `server/app.py` (middleware ~L204–245) | Resolver sessão→`request.state.user` em vez de comparar token global; **manter** `_AUTH_EXEMPT_EXACT`/`_AUTH_EXEMPT_PREFIXES` (webhook, health, `/api/auth/*`, estáticos, plugins-static). |
| `server/deps.py` (novo) | `current_user`, `Require(*perms)`, `conversation_in_scope`. |
| `db/tables.py` | `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `user_sessions` (e reuso de `inbox_members` do [01]/[02]). |
| `db/alembic/versions/` | Migração: cria tabelas + seed de papéis-sistema, catálogo de permissões e `role_permissions` default. |
| `db/repositories/` | `user_repo`, `role_repo`, `session_repo` (padrão `get_engine()` Core, como os demais). |
| `server/routes/auth.py` | `login`, `logout`, `me`, `bootstrap`; mantém-se isento no middleware. |
| `server/routes/users.py` (novo) | `/api/users` CRUD + `/api/roles`, `/api/permissions` (todos sob `Require("users.manage")`). |
| Rotas existentes | Anexar `Depends(Require(...))` por endpoint (config→`settings.manage`, plugins→`plugins.manage`, billing→`billing.manage`, contatos/mensagens→`conversation.*` + escopo de inbox). |
| Frontend `web/static/js/` | Tela de **login**; tela **Gestão de usuários** (só admin); `currentUser`/permissões no estado; `GearMenu` e botões condicionais; default-screen por papel. Seguir regras de tema escuro do `CLAUDE.md` (classes `wa-*`, `.wa-field`). |
| [`07-auditoria.md`](07-auditoria.md) | Passa a ter `user_id` real para registrar autoria. |

---

## 9. Faseamento / MVP

**Fase 1 — Identidade + papéis fixos (MVP):**
- Tabelas `users` + `user_sessions`; Argon2id; login/logout/me; bootstrap do primeiro admin.
- **Papéis fixos por enum** (`admin`, `gestor`, `atendente`) com mapa de permissões **em código**
  (constante) — ainda *sem* `roles`/`permissions`/`role_permissions` no banco. Mais rápido de
  entregar e cobre 100% do pedido do cliente.
- Middleware resolvendo sessão; `Depends(Require(...))` nas rotas; escopo de inbox via
  `inbox_members` (já do [01]). Frontend: login + esconder telas por papel.

**Fase 2 — Permissões granulares no banco:**
- Migrar o mapa em código para as tabelas `roles/permissions/role_permissions/user_roles`.
- Tela de **Gestão de usuários/papéis** (criar papéis customizados, marcar permissões).
- Útil quando o cliente quiser variar quem vê billing/plugins sem mexer em código.

**Fase 3 (se surgir necessidade):**
- ABAC leve (horário, IP) ou Casbin in-process; multi-tenant. Só se o produto evoluir para lá.

> Regra de ouro: **comece com 3 papéis fixos** (Fase 1). Suba para granular (Fase 2) apenas quando
> houver demanda real por papéis customizados — não pré-construir o motor de política.

---

## 10. Perguntas em aberto

1. **GESTOR atende conversas?** O "grupo de plugins/config/painéis/recargas" também responde
   conversas, ou é puramente administrativo (sem `conversation.*`)? (Tabela do §5 assume que sim.)
2. **GESTOR gerencia usuários?** `users.manage` é exclusivo do ADM, ou o gestor também cria
   atendentes? (Proposta: exclusivo do ADM.)
3. **Multi-papel por usuário?** Um usuário pode ser `gestor` **e** `atendente` ao mesmo tempo
   (justifica `user_roles` N:N) ou cada usuário tem exatamente um papel (basta enum)? Isso decide
   Fase 1 (enum) vs já ir para N:N.
4. **Quick replies** ([04]) são globais ou por inbox/usuário? Define se `quickreply.manage` é do
   atendente ou só de admin/gestor.
5. **Recuperação de senha:** há SMTP/serviço de e-mail disponível? Sem e-mail, o escopo de "reset"
   fica em **admin reseta a senha de um usuário** (define senha temporária) — sem fluxo de
   "esqueci minha senha" por link. Confirmar.
6. **Política de sessão:** duração (dias?), "lembrar-me", limite de sessões simultâneas,
   logout-all-devices? Cookie vs Bearer no transporte (§6.2).
7. **Migração da senha única:** a senha global atual deve continuar funcionando durante a
   transição, ou o update **força** criar o primeiro admin (recomendado)?
8. **Plugins e permissões:** algum plugin precisa de permissão própria (ex.: um painel só para
   gestor)? No MVP todo endpoint de plugin exige só "autenticado". Avaliar declarar permissões no
   `plugin.yaml` numa fase futura.

---

## 11. Referências

**Modelos de autorização**
- NIST/CSRC — Role-Based Access Control (projeto e FAQ): https://csrc.nist.gov/projects/role-based-access-control e https://csrc.nist.gov/projects/role-based-access-control/faqs
- NIST — glossário RBAC: https://csrc.nist.gov/glossary/term/role_based_access_control
- Wikipedia — Role-based access control (Core/Hierarchical, INCITS 359-2004): https://en.wikipedia.org/wiki/Role-based_access_control
- Oso — RBAC vs ABAC vs PBAC: https://www.osohq.com/learn/rbac-vs-abac-vs-pbac
- Oso — RBAC vs ABAC vs ReBAC (qual paradigma): https://www.osohq.com/learn/rbac-vs-abac-vs-rebac-what-is-the-best-access-policy-paradigm
- Permit.io — Choosing the Right Authorization Model: https://www.permit.io/blog/rbac-vs-abac-and-rebac-choosing-the-right-authorization-model
- Pangea — RBAC vs ReBAC vs ABAC: https://pangea.cloud/blog/rbac-vs-rebac-vs-abac/
- DEV (kanywst) — RBAC vs ABAC vs ReBAC, how to choose: https://dev.to/kanywst/rbac-vs-abac-vs-rebac-how-to-choose-and-implement-access-control-models-3i2d

**Bibliotecas / serviços**
- Authzed — Casbin vs SpiceDB (in-process vs serviço, trade-offs): https://authzed.com/blog/casbin
- PkgPulse — OpenFGA vs Permify vs SpiceDB (Zanzibar, 2026): https://www.pkgpulse.com/guides/openfga-vs-permify-vs-spicedb-zanzibar-authorization-2026
- Oso — OpenFGA alternatives: https://www.osohq.com/learn/openfga-alternatives
- Oso — SpiceDB alternatives: https://www.osohq.com/learn/spicedb-alternatives-authorization-tools-comparison

**Autenticação / FastAPI / hashing**
- David Muraya — A Practical Guide to FastAPI Security: https://davidmuraya.com/blog/fastapi-security-guide/
- David Muraya — JWT Authentication in FastAPI: https://davidmuraya.com/blog/fastapi-jwt-authentication/
- greeden.me — FastAPI security design (JWT/OAuth2, cookie sessions, RBAC/scopes, CSRF): https://blog.greeden.me/en/2025/10/14/a-beginners-guide-to-serious-security-design-with-fastapi-authentication-authorization-jwt-oauth2-cookie-sessions-rbac-scopes-csrf-protection-and-real-world-pitfalls/
- PropelAuth — FastAPI Auth with Dependency Injection: https://www.propelauth.com/post/fastapi-auth-with-dependency-injection
- Permit.io — FastAPI RBAC Full Implementation Tutorial: https://www.permit.io/blog/fastapi-rbac-full-implementation-tutorial
- DEV (moadennagi) — Role-based access control using FastAPI (PermissionChecker): https://dev.to/moadennagi/role-based-access-control-using-fastapi-h59
- FastAPI docs — Dependencies / Security: https://fastapi.tiangolo.com/reference/dependencies/
- AquilaX — Password Hashing: bcrypt vs Argon2 vs scrypt: https://aquilax.ai/blog/password-hashing-bcrypt-argon2
- guptadeepak.com — Complete Guide to Password Hashing (Argon2 vs bcrypt, OWASP/RFC 9106): https://guptadeepak.com/the-complete-guide-to-password-hashing-argon2-vs-bcrypt-vs-scrypt-vs-pbkdf2-2026/
- johal.in — Passlib Python Hashes: Argon2 Password Storage: https://johal.in/passlib-python-hashes-argon2-password-storage-2026/

**Documentos internos relacionados**
- [`00-visao-geral.md`](00-visao-geral.md) · [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md) · [`02-canais-e-providers.md`](02-canais-e-providers.md) · [`07-auditoria.md`](07-auditoria.md)
