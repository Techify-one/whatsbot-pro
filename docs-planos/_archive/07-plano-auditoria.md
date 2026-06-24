# 07 — Plano de Implementação: Trilha de Auditoria (WhatsBot Pro)

> Plano acionável derivado de [docs-pesquisa/07-auditoria.md](../docs-pesquisa/07-auditoria.md).
> Objetivo: registrar de forma estruturada, append-only e imutável "quem fez o quê, quando, sobre qual recurso, antes/depois" no painel WhatsBot Pro (server-hosted, single-company, multi-user).
>
> **Escopo deste plano:** tabela `audit_log` append-only, camada de captura mista (dependency FastAPI em rotas sensíveis + handler `*` do bus de events), catálogo de ações, imutabilidade (trigger/role só em Postgres), conformidade LGPD (mascaramento + retenção), e tela de visualização/filtro/export.
>
> **Não cobre:** o sistema de usuários/RBAC em si (plano 03), o modelo de conversas (plano 01), inboxes (plano 02) nem o motor de IA (plano 06). Este plano **consome** esses planos como fontes de ator e de recursos auditáveis.

---

## Dependências de outros planos

Ordem de execução recomendada e o que precisa estar pronto antes de cada fase:

| Precondição | Vem de | Necessário para |
|---|---|---|
| Tabela `users` + `request.state.user` populado pelo middleware de auth | **Plano 03 (RBAC)** Fase 4 (`server/deps.py`, middleware em `server/app.py:221`) | Fase 1 (ator real `actor_user_id`/IP), Fase 2 (gating `audit.read`) |
| Permissão `audit.read` no catálogo + `Require(...)` | **Plano 03** Fase 1 (catálogo em §82-83) e Fase 4 (`Require`) | Fase 2 (endpoints e tela gated) |
| Eventos de domínio já emitidos pelo bus (`config.changed`, `tool_override.changed`, `plugin.enabled/disabled`, etc.) | **Já existe** ([plugins/events.py](../plugins/events.py)) | Fase 0 (handler `*`) |
| Eventos de conversa (`conversation.assign/transfer/resolve`) | **Plano 01/02** | Fase 1/Fase 4 (ampliar catálogo) — opcionais até existirem |
| `actor_type = ai` (decisão do motor de IA) | **Plano 06** | Fase 1 (apenas o enum; a captura de ações de IA é incremental) |

**Pode começar isolado:** a **Fase 0** (tabela + repo + handler `*` com `actor_type=system`/`actor_label="—"`) não depende do RBAC e já entrega valor. As fases 1+ exigem o plano 03.

---

## Visão geral das fases

| Fase | Título | Depende de | Entrega |
|---|---|---|---|
| 0 | Fundação: tabela `audit_log` + repo com sanitização + handler de bus `*` | nada | Auditoria automática de config/tools/plugins, sem ator HTTP |
| 1 | Ator real: middleware `request_id` + contextvar + dependency `audit()` nas rotas sensíveis | Plano 03 (Fase 4) | Ator/IP/request_id ricos + before/after explícito |
| 2 | UI + operação: tela `AuditLog.js` + endpoints `GET /api/audit[/export]` + job de retenção | Fase 1, Plano 03 (`audit.read`) | Consulta/filtro/export gated; purge por retenção |
| 3 | Endurecimento: imutabilidade no Postgres (trigger/role) + mascaramento PII configurável + pseudonimização LGPD | Fase 0-2 | Append-only forte + conformidade LGPD |

---

## Fase 0 — Fundação (independente do RBAC)

### Objetivo
Criar a infraestrutura de persistência append-only e capturar, via bus de events, as ações de domínio que **já são emitidas hoje** — sem precisar de usuários/RBAC. Ator fica como `system` enquanto o plano 03 não existir.

### Passos

1. **Tabela `audit_log` em [db/tables.py](../db/tables.py)** — adicionar `Table` Core + índices, após `tool_overrides` (e incluir em `CORE_TABLES`, que é derivado automaticamente de `metadata.sorted_tables` na linha 207, então basta declarar a tabela):

   Colunas (todas alinhadas ao estilo do projeto — epoch float em `created_at`, `Text` para JSON, FK **lógica** sem `ForeignKey()` para não cascatear delete):
   - `id` Integer PK autoincrement
   - `actor_user_id` Integer (nullable — FK lógica para `users.id`, **sem** `ForeignKey`/cascade)
   - `actor_type` Text not null server_default `"system"` (`system` | `user` | `ai`)
   - `actor_label` Text (nullable — snapshot do nome no momento)
   - `action` Text not null
   - `resource_type` Text not null
   - `resource_id` Text (nullable — string p/ cobrir phone/jid/uuid)
   - `before_json` Text (nullable — JSON já mascarado)
   - `after_json` Text (nullable — JSON já mascarado)
   - `ip_address` Text (nullable)
   - `request_id` Text (nullable)
   - `created_at` Float not null

   Índices: `idx_audit_created (created_at)`, `idx_audit_actor (actor_user_id, created_at)`, `idx_audit_resource (resource_type, resource_id)`, `idx_audit_action (action, created_at)`.

   > Decisão MVP: `before_json`/`after_json` como `Text` + `json.dumps` (portável SQLite/Postgres). JSONB fica como pergunta aberta §1.

2. **Migration Alembic** — `db/alembic/versions/20260618_0007_audit_log.py` (ajustar prefixo de data/numeração para vir DEPOIS da migration do plano 03; se 03 usar `0007`, esta vira `0008`). Gerar com `alembic revision -m "audit_log table + indexes"` e **revisar à mão** (autogenerate pode não pegar índices nomeados corretamente). `upgrade()` cria a tabela e os 4 índices; `downgrade()` faz `drop_table`. `init_db()` aplica `alembic upgrade head` no boot ([db/connection.py](../db/connection.py)), então nada mais é necessário.

3. **Catálogo de ações** — `db/audit_actions.py` (novo, módulo de constantes, não repo). Define:
   - `AuditAction` — constantes string namespaced (`recurso.verbo`) do catálogo §"Catálogo de ações" abaixo.
   - `ResourceType` — constantes (`user`, `role`, `inbox`, `conversation`, `config`, `tool`, `plugin`, `billing`, `data`).
   - `AUDITABLE_EVENTS: dict[str, tuple[str, str]]` — mapa `event_name → (action, resource_type)` usado pelo handler de bus (allowlist). Começo mínimo:
     ```
     "config.changed":        (config.update,    config)
     "tool_override.changed": (tool.override,    tool)
     "plugin.enabled":        (plugin.enable,    plugin)
     "plugin.disabled":       (plugin.disable,   plugin)
     "plugin.settings.changed":(plugin.settings_update, plugin)
     ```
     (Eventos de alto volume — `message.sent/received` — **ficam de fora** por padrão; ver pergunta aberta §4.)

4. **`db/repositories/audit_repo.py` (novo)** — padrão Core idêntico aos repos atuais (`with get_engine().begin()` para escrita, `connect()` para leitura). API:
   - `add(*, actor_user_id, actor_type, actor_label, action, resource_type, resource_id=None, before=None, after=None, ip_address=None, request_id=None, created_at=None) -> int` — serializa `before`/`after` com `json.dumps` **após** passar pela sanitização centralizada (§sanitização). `created_at` default `time.time()`. Retorna o id inserido.
   - `query(*, actor_user_id=None, actor_type=None, resource_type=None, resource_id=None, action=None, ts_from=None, ts_to=None, limit=50, offset=0) -> list[dict]` — `select(audit_log)` com `where` dinâmico + `order_by(created_at.desc())` + limit/offset.
   - `count(**same_filters) -> int` — para paginação.
   - `purge(older_than_epoch: float) -> int` — **única deleção permitida** (política de retenção, Fase 2). Documentar no docstring que NÃO existe `update`/`delete` por id.
   - `_sanitize(data: dict | None) -> dict | None` — privado; aplica a denylist de chaves sensíveis (substitui por `"***"`), recursivo em dicts aninhados. Denylist inicial: `openrouter_api_key`, `access_token`, `api_key`, `apikey`, `password`, `password_hash`, `token`, `secret`, `credentials`, `authorization`. (Esses nomes vêm de [config/settings.py](../config/settings.py) e do plano 03; confirmados como segredos reais no projeto: `openrouter_api_key`, `access_token`.)

5. **Handler de bus `*` (interno, NÃO plugin)** — `server/audit_listener.py` (novo). O bus atual ([plugins/events.py](../plugins/events.py)) registra handlers **por plugin_id** via `register(plugin_id, event_name, handler)`. Para um handler de núcleo, reservar um `plugin_id` sentinela `"__core_audit__"`:
   - Função `audit_event_handler(ctx, payload)`:
     ```
     spec = AUDITABLE_EVENTS.get(ctx.event_name)
     if not spec: return
     action, rtype = spec
     actor = current_actor()            # contextvar (Fase 1); Fase 0 → ("system", None, None)
     audit_repo.add(actor_user_id=actor.id, actor_type=actor.type, actor_label=actor.label,
                    action=action, resource_type=rtype,
                    resource_id=payload.get("phone") or payload.get("id") or payload.get("plugin_id"),
                    after=payload,        # sanitizado dentro do add()
                    ip_address=actor.ip, request_id=actor.request_id)
     ```
   - Função `register_audit_listener()` chamada no lifespan, **após** `_set_events_runtime(...)` ([server/app.py:148](../server/app.py)): `from plugins.events import register; register("__core_audit__", "*", audit_event_handler)`.

   > Nota de design: o handler `*` roda fire-and-forget em `asyncio.create_task` ([events.py:168](../plugins/events.py)) → não bloqueia a request (atende R7). Como roda fora da pilha de DI, em Fase 0 o ator é `system`; em Fase 1 o ator vem da contextvar propagada pelo middleware.

6. **Wiring no lifespan** — em [server/app.py:148](../server/app.py), logo após `_set_events_runtime(_loop, agent_handler)`, chamar `register_audit_listener()`.

### Arquivos desta fase
- Editar: [db/tables.py](../db/tables.py) (tabela + índices)
- Criar: `db/alembic/versions/20260618_00XX_audit_log.py`
- Criar: `db/audit_actions.py`
- Criar: `db/repositories/audit_repo.py`
- Criar: `server/audit_listener.py`
- Editar: [server/app.py](../server/app.py) (lifespan, ~linha 148)

### Critério de pronto
- `alembic upgrade head` cria `audit_log` em SQLite **e** Postgres (rodar `WHATSBOT_TEST_DB_URL` apontando p/ Postgres).
- Alterar uma config pelo painel (`PUT /api/config`) gera uma linha em `audit_log` com `action="config.update"`, `actor_type="system"`, `after_json` **sem** o valor de `openrouter_api_key` (aparece `"***"`).
- Habilitar/desabilitar um plugin gera linha `plugin.enable`/`plugin.disable`.
- Nenhum `UPDATE`/`DELETE` em `audit_log` existe no código (grep no `audit_repo.py` mostra só `insert`/`select` + o `purge`).

---

## Fase 1 — Ator real (depende do Plano 03)

### Objetivo
Dar ao registro o ator humano (`actor_user_id`, `actor_label`), `ip_address` e `request_id`, tanto nas rotas (via dependency) quanto no handler de bus (via contextvar). Adicionar before/after explícito nas rotas sensíveis do catálogo.

### Passos

1. **Middleware de `request_id` + contextvar de ator** — estender o `auth_middleware` em [server/app.py:221](../server/app.py) (ou um middleware novo encadeado logo após). Responsabilidades:
   - Gerar `request_id` (`uuid4().hex`) e pôr em `request.state.request_id`.
   - Após o auth do plano 03 popular `request.state.user`, setar uma `contextvar` global de ator (`server/audit_context.py`, novo): `set_current_actor(ActorCtx(id=user.id, type="user", label=user.name, ip=client_ip(request), request_id=request_id))`.
   - `reset` da contextvar no `finally` (token do `ContextVar.set`).
   - `client_ip(request)`: ler `X-Forwarded-For` (primeiro IP) com fallback `request.client.host` (importante atrás do proxy do Coolify).

   > `contextvars` propaga automaticamente para `asyncio.create_task` no MESMO contexto. Como o handler `*` é agendado via `asyncio.run_coroutine_threadsafe` em outro loop-thread ([events.py:172](../plugins/events.py)), a contextvar do request **não** propaga sozinha. Solução: capturar o ator no momento do `emit` síncrono não é trivial — ver pergunta aberta §2. Recomendação MVP: o handler de bus lê `get_current_actor()` que retorna o último ator setado por thread; para emits originados em rotas (a maioria) o ator estará correto; para emits de jobs/IA cai em `system`/`ai`.

2. **`server/audit_context.py` (novo)** — define `ActorCtx` (dataclass), a `ContextVar`, `set_current_actor()`, `reset_current_actor()`, `get_current_actor() -> ActorCtx` (default `ActorCtx(id=None, type="system", label=None, ip=None, request_id=None)`).

3. **Dependency `audit(...)` para rotas** — `server/audit_dep.py` (novo):
   - `audit(action: str, resource_type: str)` → retorna uma dependency assíncrona que monta um `AuditEntry` mutável e o anexa em `request.state.audit`. A rota preenche `resource_id`, `before`, `after` e a gravação acontece **após** a resposta. Duas opções de flush:
     - (a) a própria rota chama `request.state.audit.flush()` ao final (explícito, controle total de before/after);
     - (b) um `BackgroundTask`/middleware grava no fim se `request.state.audit` foi preenchido.
   - Recomendação: **(a) explícito** nas rotas que precisam de before/after; a dependency apenas pré-popula ator/IP/request_id. Para before/after, a rota lê o estado antes da mutação e depois — usando os repos existentes (ex.: `config_repo.get_all()` antes/depois em `PUT /api/config`).

4. **Instrumentar rotas sensíveis** — aplicar `Depends(audit(...))` + flush nas rotas do catálogo. Pontos de integração reais (alguns dependem dos planos 01/02/03):
   - `server/routes/config.py` (`PUT /api/config`) → `config.update`. **Já emite `config.changed`** então o bus cobre; a dependency agrega IP/before/after. Evitar **duplicidade**: ou cobre por bus, ou por dependency — não os dois. Recomendação: rotas com bus → manter bus; rotas SEM evento (auth, users, export) → dependency. (ver pergunta aberta §6.)
   - `server/routes/auth.py` (login/logout — plano 03) → `auth.login`/`auth.login_failed`/`auth.logout` (sem evento de bus → **dependency obrigatória**).
   - `server/routes/users.py` (plano 03) → `user.create/update/disable/delete/password_reset`, `role.*`.
   - Rotas de inbox/conversa (planos 01/02) → `inbox.*`, `conversation.*`.
   - `server/routes/admin.py` (`POST /api/admin/migrate-to-postgres`) → `db.migrate_to_postgres`.
   - Exportações (`GET /api/audit/export`, export de contatos se existir) → `data.export`.

### Arquivos desta fase
- Criar: `server/audit_context.py`, `server/audit_dep.py`
- Editar: [server/app.py](../server/app.py) (middleware ~linha 221)
- Editar: `server/audit_listener.py` (usar `get_current_actor()`)
- Editar: rotas do catálogo (principalmente `auth.py`, `users.py` do plano 03; `admin.py`)

### Critério de pronto
- Login pelo painel gera `auth.login` com `actor_user_id` correto, `ip_address` preenchido e `request_id` não-nulo.
- Alterar config logado gera linha com `actor_user_id`/`actor_label` reais (não `system`).
- `before_json`/`after_json` mostram o diff de `config.update` (com segredos mascarados).
- Login com senha errada gera `auth.login_failed` (sem `actor_user_id`, com IP).

---

## Fase 2 — UI + operação

### Objetivo
Tela de consulta com filtros e export, gated por `audit.read`; job de retenção configurável.

### Passos

1. **Endpoints REST** — `server/routes/audit.py` (novo), registrado em [server/app.py:304-319](../server/app.py) junto aos demais (`audit.register_routes(app, deps)`). Formato `{ok, data, error}`. Todos sob `Depends(Require("audit.read"))` (plano 03):
   | Método | Endpoint | Descrição |
   |---|---|---|
   | GET | `/api/audit?actor=&actor_type=&resource_type=&resource_id=&action=&from=&to=&limit=&offset=` | lista paginada via `audit_repo.query()` + `count()`. Retorna `{items, total, limit, offset}`. Chama repos via `asyncio.to_thread`. |
   | GET | `/api/audit/actions` | catálogo de `action`/`resource_type` distintos (para popular os selects de filtro) — `SELECT DISTINCT`. |
   | GET | `/api/audit/export?<mesmos filtros>&format=csv\|json` | streaming da consulta filtrada (sem limit). Responde `text/csv` ou `application/json`. **Esta rota também é auditada** (`data.export`) se decidirmos auditar a auditoria (pergunta §6). |

2. **Tela frontend `AuditLog.js`** — `web/static/js/components/AuditLog.js` (Preact + HTM, sem build). Registrar no menu da engrenagem (`GearMenu`) **somente** quando o usuário tem `audit.read` (gating de UI vem do plano 03 — provavelmente um `permissions` no objeto de user). Conteúdo:
   - Barra de filtros: select de usuário (de `/api/users` do plano 03), select de `resource_type` e `action` (de `/api/audit/actions`), date-range (`from`/`to`), busca por `resource_id`.
   - Tabela: timestamp · ator (`actor_label` + badge colorido `user`/`system`/`ai`) · ação · recurso (`resource_type:resource_id`) · IP. Paginação (limit/offset).
   - Linha expansível: diff `before_json`/`after_json` em JSON formatado (já mascarado pelo backend).
   - Botão "Exportar" → baixa CSV/JSON da consulta filtrada.
   - **Modo escuro obrigatório** ([CLAUDE.md](../CLAUDE.md) §Tema): usar classes `wa-*` (`bg-wa-panel`, `text-wa-text`, `border-wa-border`, `bg-wa-hover`) e `.wa-field` em inputs/selects. Badges de `actor_type` com cores cobertas pelo fallback de `custom.css` ou `wa-*`. Adicionar a rota SPA `/auditoria` em `_SPA_PATHS` ([server/app.py:215](../server/app.py)).

3. **Config de retenção** — chave `audit_retention_days` em `config` (default **365**, sugestão 365-730). Exposta na tela de Configurações do core (é config do core, não de plugin → OK no `ConfigPanel.js`).

4. **Job de retenção (purge)** — em [server/background.py](../server/background.py), nova coroutine `audit_purge_loop(deps)` no padrão das outras (`while not state.stop_event.is_set()` + sleep granular como em `avatar_fetch_task` linha 229). Roda 1×/dia: lê `audit_retention_days`, calcula `cutoff = time.time() - days*86400`, chama `audit_repo.purge(cutoff)` via `asyncio.to_thread`, loga a contagem removida. Registrar a task no lifespan ([server/app.py:163-168](../server/app.py)) junto das demais `asyncio.create_task(...)`.

   > O purge é a **única** deleção permitida em `audit_log` (política de retenção, R9) — distinta de UPDATE/DELETE programático que continua proibido (§3.4 da pesquisa).

### Arquivos desta fase
- Criar: `server/routes/audit.py`
- Editar: [server/app.py](../server/app.py) (registrar rota + SPA path + task)
- Criar: `web/static/js/components/AuditLog.js`
- Editar: `web/static/js/components/GearMenu.js` (entrada de menu gated), `app.js` (rota SPA), `ConfigPanel.js` (campo `audit_retention_days`)
- Editar: [server/background.py](../server/background.py) (`audit_purge_loop`)

### Critério de pronto
- Usuário com `audit.read` vê a tela `/auditoria`; usuário sem a permissão não vê a entrada no menu **e** recebe 403 ao chamar `/api/audit`.
- Filtros por usuário/recurso/ação/período retornam o subconjunto correto; paginação funciona.
- Export CSV abre no Excel com colunas legíveis; export JSON é válido.
- Tela legível no modo escuro (testar com `.dark` ligado).
- Com `audit_retention_days=1` e linhas antigas semeadas, o purge remove as linhas acima do limite (testável chamando `audit_repo.purge` direto num teste).

---

## Fase 3 — Endurecimento (imutabilidade forte + LGPD)

### Objetivo
Tornar a imutabilidade defensável no banco (Postgres) e completar a postura LGPD.

### Passos

1. **Imutabilidade no Postgres** — migration Alembic condicional ao dialeto (`if op.get_bind().dialect.name == "postgresql"`):
   - **Opção A (trigger):** `CREATE FUNCTION audit_log_no_mod()` que `RAISE EXCEPTION` + `CREATE TRIGGER ... BEFORE UPDATE OR DELETE ON audit_log`. **Problema:** bloqueia também o `purge` da Fase 2. Solução: o purge usa uma sessão com `SET LOCAL session_replication_role = replica` (desativa triggers) OU a trigger permite DELETE quando `created_at < cutoff` — frágil. Recomendação: trigger só em `UPDATE` (proíbe edição), e DELETE permitido só pela política de retenção.
   - **Opção B (role):** documentar/provisionar um role de aplicação com `GRANT INSERT, SELECT ON audit_log` (sem UPDATE/DELETE) + um role separado de manutenção para o purge. Mais robusto, porém depende de operação fora do Alembic (provisão de roles no Postgres do Coolify).
   - **SQLite:** sem roles/sem enforcement de trigger equivalente confiável → fica na disciplina de app (o repo não expõe update/delete). Documentar limitação (pergunta aberta §7).
   - Recomendação MVP: **trigger BEFORE UPDATE** no Postgres (bloqueia edição), DELETE liberado, purge controlado pela app. App-level já garante o resto.

2. **Mascaramento de PII configurável** — evoluir `_sanitize` do `audit_repo`:
   - Estratégias: `mask` (parcial, ex.: `+5511****1234` para telefone), `redact` (`"***"`), `hash` (HMAC determinístico com salt secreto — mantém correlação sem expor o dado).
   - Config `audit_pii_strategy` (default `redact` para segredos sempre; PII de contato configurável `mask`/`hash`/`off`).
   - Salt de hash guardado em config (gerado no 1º boot, nunca exposto na UI).
   - Mascarar **na ingestão** (no `add()`), nunca na leitura.

3. **Pseudonimização LGPD (direito à eliminação)** — `audit_repo.pseudonymize_subject(resource_id)`: substitui PII (`resource_id`, campos de `before/after`) por um token estável, **preservando a ação e o ator** (a trilha continua sendo evidência de prestação de contas). Decisão jurídica em pergunta aberta §5. Acionado a partir do fluxo de "esquecer titular" (futuro).

4. **Auditar acesso à auditoria** (opcional — pergunta §6): registrar `data.export`/`audit.read` quando alguém consulta/exporta a trilha.

### Arquivos desta fase
- Criar: `db/alembic/versions/20260618_00XX_audit_log_immutable.py` (trigger Postgres condicional)
- Editar: `db/repositories/audit_repo.py` (`_sanitize` com estratégias + `pseudonymize_subject`)
- Editar: `ConfigPanel.js` (`audit_pii_strategy`)

### Critério de pronto
- Em Postgres, um `UPDATE audit_log SET ...` manual falha com exceção da trigger; `INSERT`/`SELECT` funcionam; `purge` da Fase 2 ainda funciona.
- Telefone aparece mascarado em `before/after` quando `audit_pii_strategy=mask`.
- `pseudonymize_subject(phone)` remove a PII das linhas daquele titular mantendo `action`/`actor`/`created_at`.

---

## Catálogo de ações (referência consolidada)

`action` = `recurso.verbo` (namespaced). Definidas como constantes em `db/audit_actions.py`.

**Auth & RBAC (plano 03):** `auth.login`, `auth.login_failed`, `auth.logout`, `user.create`, `user.update`, `user.disable`, `user.delete`, `user.password_reset` (*nunca logar senha/hash*), `role.create`, `role.update`, `role.delete`, `role.assign`.

**Inboxes & credenciais (plano 02):** `inbox.create`, `inbox.update`, `inbox.delete`, `inbox.credentials_update` (*valor mascarado; logar só "alterada"*).

**Conversas (plano 01):** `conversation.open`, `conversation.assign`, `conversation.transfer`, `conversation.resolve`, `conversation.reopen`, `conversation.toggle_ai`, `conversation.export`.

**Motor de IA (plano 06):** `config.update` (com `keys_changed`; destaque `system_prompt`), `agent.prompt_update`, `tool.override`, `model.change`.

**Plugins:** `plugin.enable`, `plugin.disable`, `plugin.install`, `plugin.delete`, `plugin.settings_update`.

**Billing:** `billing.recharge`, `billing.apikey_provision` (*sem expor a chave*).

**Dados:** `data.export`, `db.migrate_to_postgres`.

Cada uma mapeada a um `resource_type` (`user`/`role`/`inbox`/`conversation`/`config`/`tool`/`plugin`/`billing`/`data`). As que já têm evento de bus entram em `AUDITABLE_EVENTS` (captura por handler `*`); as demais entram via dependency `audit()`.

---

## Notas de integração com o código atual

- **Bus de events** ([plugins/events.py:129](../plugins/events.py)): `register(plugin_id, event_name, handler)` aceita qualquer string de `plugin_id`; usar o sentinela `"__core_audit__"` para o handler de núcleo. O `*` é dispatch-only e roda **após** os subscribers específicos ([events.py:146,168](../plugins/events.py)) — perfeito para o auditor passivo. Handler `*` precisa ser **defensivo** (try/except interno) pois um erro só loga warning ([events.py:236](../plugins/events.py)).
- **Eventos confirmados emitidos hoje** (matéria-prima para `AUDITABLE_EVENTS`): `config.changed`, `tool_override.changed`, `plugin.enabled`, `plugin.disabled`, `plugin.settings.changed`, `contact.updated`, `contact.ai_toggled`, `contact.tagged`, `contact.untagged`, `tag.created/updated/deleted`, `execution.started/ended`. (Lista de `KNOWN_EVENTS` em [plugins/events.py:39](../plugins/events.py).)
- **Padrão de repo** ([db/repositories/config_repo.py](../db/repositories/config_repo.py)): seguir 1:1 — `get_engine().begin()` para escrita, `connect()` para leitura, statements Core de `db/tables`, `json.dumps(ensure_ascii=False)`.
- **Não bloquear a request** (R7): chamadas a `audit_repo` das rotas via `asyncio.to_thread` (como todos os repos hoje); o handler de bus já roda em task separada.
- **`CORE_TABLES`** ([db/tables.py:207](../db/tables.py)) é derivado de `metadata.sorted_tables` → `audit_log` entra automaticamente e será copiada na migração SQLite→Postgres ([db/migration_postgres.py](../db/migration_postgres.py)) sem mudança.
- **Lifespan** ([server/app.py:143-183](../server/app.py)): registrar o listener após `_set_events_runtime` (linha 148) e a task de purge junto das outras (linha 163).

---

## Perguntas em aberto

1. **JSONB vs TEXT para `before_json`/`after_json`.**
   - Contexto: TEXT é portável SQLite/Postgres; JSONB permite consultar/indexar campos internos do diff no Postgres.
   - Opções: (a) TEXT puro — simples, portável, sem query interna; (b) tipo Core customizado que vira `JSONB` no Postgres e `TEXT` no SQLite — mais trabalho, ganha query interna só no Postgres.
   - Recomendação: **(a) TEXT no MVP**. Migrar para JSONB só se aparecer necessidade real de filtrar por campo do diff.

2. **Propagação do ator ao handler de bus (fora da request).**
   - Contexto: o `*` roda via `run_coroutine_threadsafe` em outro loop-thread; a `contextvar` do request não propaga automaticamente.
   - Opções: (a) o handler lê `get_current_actor()` por-thread (correto para emits originados em rotas, cai em `system` para jobs/IA) — simples mas com janelas de corrida em concorrência alta; (b) capturar o ator no momento do `emit` e anexá-lo ao payload (`payload["_actor"]`) — exige tocar nos call-sites de `emit`; (c) instrumentar **só por dependency** as ações com ator humano e deixar o bus apenas para `system`/`ai`.
   - Recomendação: **(c) + (a)** — rotas críticas com ator humano usam a dependency (ator confiável); o bus cobre o resto marcando `system`/`ai`. Evita corrida e duplicidade.

3. **`actor_type = ai`: como atribuir.**
   - Contexto: quando o motor de IA (plano 06) executa uma tool que altera dado, quem é o ator?
   - Opções: (a) `actor_type="ai"`, `actor_user_id=NULL`, `actor_label="agente <nome>"`; (b) `ai` com `actor_user_id` = dono da inbox.
   - Recomendação: **(a)** — `ai` é um tipo de ator distinto; rastrear o agente em `actor_label`. Cruza com plano 06; só implementar quando houver ações de IA auditáveis.

4. **Volume: auditar `message.sent`/`message.received`?**
   - Contexto: altíssimo volume; já existem no histórico de `messages`.
   - Opções: (a) **fora** da auditoria (recomendado); (b) só `message.sent source=operator` (mensagem manual do atendente conta como ação humana).
   - Recomendação: **(a) por padrão**, com possibilidade futura de (b) se compliance exigir rastrear envios manuais de atendentes.

5. **LGPD / direito à eliminação do titular.**
   - Contexto: a trilha contém PII (telefone/nome/email dos contatos). Pedido de eliminação conflita com retenção da evidência.
   - Opções: (a) preservar a trilha como base legal (obrigação/legítimo interesse de prestação de contas); (b) pseudonimizar a PII do titular mantendo a ação; (c) excluir as linhas do titular.
   - Recomendação: **(b) pseudonimizar** (Fase 3) — preserva a ação/ator/timestamp e remove a PII. **Validar com jurídico** antes de fixar a política.

6. **Auditar o acesso à própria auditoria + evitar duplicidade rota×bus.**
   - Contexto: (i) ler/exportar a trilha é ação sensível; (ii) algumas rotas têm evento de bus E poderiam ter dependency → linha duplicada.
   - Opções: para (i) — auditar `audit.read`/`data.export` ou não. Para (ii) — regra clara: "rota com evento de bus → cobre por bus; rota sem evento → dependency".
   - Recomendação: para (i) auditar **apenas exports** (`data.export`), não cada leitura (ruído). Para (ii) adotar a regra acima como invariante de projeto.

7. **Imutabilidade no SQLite.**
   - Contexto: SQLite não tem roles; trigger de bloqueio é possível mas também travaria o purge e é contornável por quem tem acesso ao arquivo.
   - Opções: (a) aceitar que append-only forte só existe no Postgres; disciplina de app + backups no SQLite; (b) trigger SQLite `BEFORE UPDATE`/`BEFORE DELETE` com `RAISE(ABORT,...)` e exceção para o purge.
   - Recomendação: **(a)** — append-only forte é garantia de Postgres (deployment Pro recomendado); no SQLite fica a disciplina de app. Documentar claramente.

8. **Numeração da migration vs. plano 03.**
   - Contexto: o plano 03 reserva `20260618_0007_rbac_users.py`. Esta tabela precisa vir depois (FK lógica para `users`).
   - Opções: (a) numerar `0008` assumindo 03 antes; (b) tornar a dependência explícita no `down_revision`.
   - Recomendação: **(a)** com `down_revision` apontando para a head do plano 03 quando ele existir; se a auditoria for mergeada antes do RBAC, usar `0007` e o RBAC ajusta. Resolver no momento do merge.
