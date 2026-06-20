# 07 — Auditoria (trilha de ações dos usuários)

> Pesquisa de arquitetura. **Nenhum código foi alterado.** Documento de referência para implementar uma trilha de auditoria ("quem fez o quê, quando, sobre qual recurso, antes/depois") no WhatsBot.
>
> Documentos relacionados:
> - **03 — Permissões/RBAC**: quem pode ler a auditoria (`audit.read`) e quem gera as ações auditadas (actor).
> - **06 — Motor de IA**: mudanças no system prompt, agente, tools e overrides precisam ser auditadas (são as mais sensíveis do produto).
> - **02 — Inboxes** e **01 — Conversas**: recursos auditáveis (abrir/atribuir/transferir/resolver conversa, credenciais de inbox).

---

## 1. O que existe hoje

### 1.1 Bus de Events/Filters (matéria-prima, não auditoria)

O sistema de plugins já tem um **bus de events** que emite eventos para praticamente toda mudança de estado do sistema (ver `CLAUDE.md` → "Events e Filters"). Eventos hoje emitidos que são candidatos diretos a virar linhas de auditoria:

| Evento interno | Origem | Relevância para auditoria |
|---|---|---|
| `config.changed` | `PUT /api/config` (com `keys_changed`) | Alta — inclui `system_prompt`, modelo, thresholds |
| `tool_override.changed` | `PUT /api/tools/{name}` | Alta — muda comportamento do motor de IA (doc 06) |
| `contact.updated` | `PUT /api/contacts/{phone}/info` | Média |
| `contact.ai_toggled` | `POST /api/contacts/{phone}/toggle-ai` | Média |
| `contact.tagged` / `contact.untagged` | `PUT /api/contacts/{phone}/tags` | Média |
| `tag.created` / `tag.updated` / `tag.deleted` | endpoints de tag | Baixa/Média |
| `plugin.enabled` / `plugin.disabled` / `plugin.settings.changed` | lifecycle de plugin | Alta — muda o código que roda |
| `message.sent` (`source ∈ {ai, operator, ...}`) | resposta IA / operador | Média (volume alto) |
| `execution.started` / `execution.ended` | wrappers async | rastreio técnico |

A chave especial `*` em `EVENT_HANDLERS` permite a um único handler **observar todo evento emitido**. Esse é o gancho mais barato para a primeira versão da auditoria (ver §4b).

### 1.2 Tabelas `executions` / `execution_steps`

Já existe rastreio de execuções do agente (`db/tables.py:144-168`):

```python
executions(id, phone, trigger_type, status, started_at, completed_at, error)
execution_steps(id, execution_id, step_type, status, data, ts)
```

Isso é **observabilidade do pipeline de IA** (webhook → LLM → tool calls → resposta). Útil para depurar uma execução, mas:

- O "ator" é o **contato/telefone**, não um **usuário do painel**.
- Registra passos técnicos (tool call, llm_request), não intenção de negócio ("Fulano transferiu a conversa para Beltrano").
- Não tem `actor_user_id`, `resource_type`, nem `before`/`after`.
- É volátil/ruidoso por natureza (uma execução = N steps); não foi pensado para retenção longa nem para imutabilidade.

### 1.3 Por que nada disso é "auditoria de usuário"

| Critério | Bus de events | executions/steps | Auditoria de usuário (esta feature) |
|---|---|---|---|
| Persistente? | Não (fire-and-forget em memória) | Sim | **Sim** |
| Tem ator humano (`user_id`)? | Não (o WhatsBot não tem usuários hoje) | Não (telefone) | **Sim** |
| Captura before/after? | Parcial (alguns payloads) | Não | **Sim** |
| Imutável / append-only? | N/A | Não | **Sim** |
| Pensado para compliance/retenção? | Não | Não | **Sim** |
| Foco | extensibilidade de plugin | debug do pipeline IA | **prestação de contas** |

**Conclusão**: o bus é a fonte de eventos perfeita para *alimentar* a auditoria, mas a auditoria em si precisa de uma tabela dedicada, append-only, com ator de painel — que só existe a partir do RBAC (doc 03). Auditoria de **negócio** (ação de usuário, com intenção e contexto) é diferente de log **técnico** (debug, stack trace, métrica). A primeira é evidência; o segundo é diagnóstico. ([Sonar](https://www.sonarsource.com/resources/library/audit-logging/), [EnterpriseReady](https://www.enterpriseready.io/features/audit-log/))

---

## 2. Requisitos

**Funcionais**
- R1. Registrar, de forma estruturada, toda ação relevante de um usuário: **quem** (ator), **o quê** (ação), **quando** (timestamp), **sobre o quê** (recurso), e **estado antes/depois** quando aplicável.
- R2. Distinguir ator `user` (humano logado), `system` (job/automação) e `ai` (decisão do motor de IA — ver doc 06).
- R3. Tela de consulta com filtros por usuário, tipo de recurso, ação e período; visível só a quem tem `audit.read` (doc 03).
- R4. Exportação (CSV/JSON) da trilha filtrada.
- R5. Capturar `ip_address` e `request_id` (correlação com logs técnicos).

**Não-funcionais**
- R6. **Append-only**: a aplicação só insere e lê; nunca UPDATE/DELETE programático.
- R7. Escrita **assíncrona** / não-bloqueante — não pode travar a request principal (`asyncio.to_thread`, igual aos repos atuais). ([Agnite](https://agnitestudio.com/blog/audit-logging-saas/))
- R8. **Não vazar segredos** (tokens, API keys, senhas) nem PII bruta sensível — mascarar antes de persistir (LGPD, §6).
- R9. Retenção configurável (default sugerido 1–2 anos). ([EnterpriseReady](https://www.enterpriseready.io/features/audit-log/), [Last9](https://last9.io/blog/gdpr-log-management/))
- R10. Funcionar em SQLite e Postgres (mesma camada SQLAlchemy 2.0 Core do resto do projeto).

---

## 3. Modelo de dados

Tabela dedicada `audit_log` (Core, `Table` em `db/tables.py`). Escolha de **tabela dedicada** em vez de "audit columns" (created_by/updated_by espalhadas nas tabelas) porque queremos: histórico completo (não só o último que tocou), append-only real, e um único ponto de consulta. Audit columns continuam úteis como complemento barato, mas não substituem a trilha. ([Medium/Surbhi Singh](https://medium.com/@singh.surbhicse/creating-audit-table-to-log-insert-update-and-delete-changes-in-flask-sqlalchemy-f2ca53f7b02f))

### 3.1 Campos

| Campo | Tipo | Descrição |
|---|---|---|
| `id` | INTEGER PK autoincrement | identificador sequencial |
| `actor_user_id` | INTEGER (nullable) | FK lógica para `users.id` (doc 03); `NULL` para `system`/`ai` |
| `actor_type` | TEXT | `system` \| `user` \| `ai` |
| `actor_label` | TEXT (nullable) | nome/identificação legível no momento do evento (snapshot — sobrevive a rename/delete do usuário) |
| `action` | TEXT | verbo da ação, namespaced: `user.create`, `conversation.transfer`, `config.update`, … |
| `resource_type` | TEXT | `user`, `role`, `inbox`, `conversation`, `config`, `tool`, `plugin`, `billing`, … |
| `resource_id` | TEXT (nullable) | id do recurso afetado (string para cobrir phone/jid/uuid) |
| `before_json` | TEXT (nullable) | snapshot do estado anterior (JSON, já mascarado) |
| `after_json` | TEXT (nullable) | snapshot do estado novo (JSON, já mascarado) |
| `ip_address` | TEXT (nullable) | IP do ator (de `request.client.host` / `X-Forwarded-For`) |
| `request_id` | TEXT (nullable) | correlação com logs técnicos (gerado por middleware) |
| `created_at` | FLOAT | epoch (consistente com `started_at`/`ts` já usados no projeto) |

> Em Postgres, `before_json`/`after_json` podem ser `JSONB` para indexar/consultar campos internos; em SQLite ficam como `TEXT`. Para manter dialect-agnóstico no Core, usar `Text` e serializar com `json.dumps`, ou um tipo customizado que vira `JSONB` no dialeto Postgres. Recomendação MVP: `Text` (simples e portável).

### 3.2 DDL ilustrativo (Postgres)

```sql
CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT,                          -- FK lógica para users(id)
    actor_type    TEXT    NOT NULL DEFAULT 'user', -- system | user | ai
    actor_label   TEXT,
    action        TEXT    NOT NULL,
    resource_type TEXT    NOT NULL,
    resource_id   TEXT,
    before_json   JSONB,
    after_json    JSONB,
    ip_address    TEXT,
    request_id    TEXT,
    created_at    DOUBLE PRECISION NOT NULL
);

CREATE INDEX idx_audit_created   ON audit_log (created_at);
CREATE INDEX idx_audit_actor     ON audit_log (actor_user_id, created_at);
CREATE INDEX idx_audit_resource  ON audit_log (resource_type, resource_id);
CREATE INDEX idx_audit_action    ON audit_log (action, created_at);
```

### 3.3 Equivalente SQLAlchemy Core (`db/tables.py`)

```python
audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("actor_user_id", Integer),                 # FK lógica -> users.id
    Column("actor_type", Text, nullable=False, server_default="user"),
    Column("actor_label", Text),
    Column("action", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Text),
    Column("before_json", Text),
    Column("after_json", Text),
    Column("ip_address", Text),
    Column("request_id", Text),
    Column("created_at", Float, nullable=False),
)
Index("idx_audit_created", audit_log.c.created_at)
Index("idx_audit_actor", audit_log.c.actor_user_id, audit_log.c.created_at)
Index("idx_audit_resource", audit_log.c.resource_type, audit_log.c.resource_id)
Index("idx_audit_action", audit_log.c.action, audit_log.c.created_at)
```

### 3.4 Append-only na prática

A imutabilidade é **convenção + defesa**, não só boa intenção ([EnterpriseReady](https://www.enterpriseready.io/features/audit-log/), [SaaS Masters](https://saasmasters.pro/en/blog/audit-logging-compliance-trails-saas-complete-guide)):

1. **Camada de app**: `audit_repo` expõe só `add()` e queries de leitura. Nunca `update`/`delete`.
2. **Banco (opcional, recomendado em Postgres)**: trigger `BEFORE UPDATE OR DELETE` que levanta exceção; ou um role de aplicação com `INSERT, SELECT` apenas (sem `UPDATE`/`DELETE`).
3. **Diferença vs. registro técnico**: ao apagar um recurso (ex.: usuário), **não** apagar suas linhas de auditoria — elas registram a própria criação e deleção. Por isso `actor_user_id` é FK *lógica* (sem `ON DELETE CASCADE`) + `actor_label` guarda o nome em snapshot.

---

## 4. Como capturar eventos — três abordagens

### (a) Decorator / dependency nas rotas FastAPI

Uma dependency (`Depends`) ou decorator que envolve a rota: lê o usuário autenticado (de `request.state.user`, doc 03), captura `ip_address` e `request_id`, e — após a operação — grava a linha.

```python
# ilustrativo
async def audit(action: str, resource_type: str):
    async def _dep(request: Request):
        ctx = AuditCtx(
            actor_user_id=request.state.user.id,
            actor_type="user",
            ip_address=client_ip(request),
            request_id=request.state.request_id,
            action=action, resource_type=resource_type,
        )
        request.state.audit = ctx   # a rota preenche resource_id/before/after e dá flush
        return ctx
    return _dep

@router.put("/api/users/{uid}", dependencies=[Depends(audit("user.update", "user"))])
async def update_user(...): ...
```

- **Prós**: contexto HTTP completo (ator, IP, request_id) de graça; semântica de negócio explícita ("user.update", não um genérico "UPDATE users"); fácil de mascarar campos por rota.
- **Contras**: precisa instrumentar cada rota sensível manualmente (pode esquecer alguma); before/after exige a rota ler o estado antes e depois.

### (b) Reaproveitar o bus de events (handler `*`)

Um handler interno (não um plugin) assinando `*` que mapeia eventos do bus → linhas de auditoria, com uma allowlist de eventos auditáveis e um mapper evento→(action, resource_type).

```python
AUDITABLE = {
    "config.changed":        ("config.update", "config"),
    "tool_override.changed": ("tool.override", "tool"),
    "plugin.enabled":        ("plugin.enable", "plugin"),
    "contact.ai_toggled":    ("conversation.toggle_ai", "conversation"),
    # ...
}

def audit_event_handler(ctx, payload):
    spec = AUDITABLE.get(ctx.event_name)
    if not spec: return
    action, rtype = spec
    audit_repo.add(actor_user_id=current_actor(), actor_type="user",
                   action=action, resource_type=rtype,
                   resource_id=payload.get("phone") or payload.get("id"),
                   after_json=sanitize(payload), ...)
```

- **Prós**: cobertura ampla de graça — todo evento de domínio já existe; um único ponto de manutenção; pega ações disparadas fora de rotas (jobs, AI).
- **Contras**: o bus é fire-and-forget e roda **fora do escopo da request** → não tem `ip_address`/`request_id`/ator HTTP de forma confiável (precisa propagar o ator via contextvar). Payloads variam de evento para evento → mapeamento before/after fica heterogêneo. Eventos de alto volume (`message.sent`) poluiriam a trilha se não filtrados.

### (c) SQLAlchemy event listeners no nível de tabela/ORM

Listeners (`after_insert`/`after_update`/`before_delete` em mapper events, ou `before_flush`/`after_flush` em session events) que detectam mudanças e gravam diffs automaticamente — abordagem do `django-auditlog` e do `sqlalchemy_audit`. ([SQLAlchemy ORM Events](https://docs.sqlalchemy.org/en/20/orm/events.html), [sqlalchemy_audit](https://pypi.org/project/sqlalchemy_audit/), [django-auditlog](https://github.com/jazzband/django-auditlog))

- **Prós**: captura **toda** mudança de dado automaticamente (nada escapa); before/after vêm "de graça" via `get_history()`/`session.dirty`; diff por campo robusto.
- **Contras (decisivo aqui)**: o WhatsBot usa **SQLAlchemy 2.0 Core, sem ORM declarativo** (ver `CLAUDE.md`). Mapper/session events são recursos do **ORM** — não existem para `Table` + `connection.execute(insert())`. Adotar isso exigiria introduzir a camada ORM só para auditoria, contrariando a arquitetura. Além disso, listeners de tabela registram **mudança de dado** (técnico), não **intenção de negócio** ("transferir conversa" vira "UPDATE conversations SET assignee_id=…"), e não conhecem o ator HTTP sem propagar contexto via contextvar mesmo assim.

### Recomendação: misto (a) + (b)

| | Ator HTTP (IP/req_id) | Cobertura | Semântica de negócio | Encaixa na arquitetura Core |
|---|---|---|---|---|
| (a) Dependency nas rotas | ✅ | manual | ✅ explícita | ✅ |
| (b) Bus `*` | ⚠️ via contextvar | ampla | ⚠️ por mapeamento | ✅ |
| (c) SQLAlchemy listeners | ⚠️ | total | ❌ técnica | ❌ (sem ORM) |

**Plano**: descartar (c) (incompatível com Core-only). Usar **(a) dependency nas rotas sensíveis** como fonte primária (ator/IP/req_id ricos + ações explícitas) e **(b) handler de bus** como rede de captura para ações de domínio e disparadas por jobs/IA. Propagar o ator atual via `contextvar` setada pelo middleware de auth (doc 03), para que o handler de bus saiba quem foi mesmo fora da pilha de DI. Centralizar mascaramento (§6) no `audit_repo.add()` para que ambas as fontes passem pelo mesmo filtro.

---

## 5. Catálogo inicial de ações a auditar

`action` namespaced `recurso.verbo`. `resource_type` entre parênteses.

**Autenticação & RBAC (doc 03)**
- `auth.login` / `auth.login_failed` / `auth.logout` (user)
- `user.create` / `user.update` / `user.disable` / `user.delete` (user)
- `user.password_reset` (user) — *nunca* logar a senha/hash
- `role.create` / `role.update` / `role.delete` / `role.assign` (role)

**Inboxes & credenciais (doc 02)**
- `inbox.create` / `inbox.update` / `inbox.delete` (inbox)
- `inbox.credentials.update` (inbox) — *valores mascarados*; logar só "credencial alterada", não o segredo

**Conversas (doc 01)**
- `conversation.open` / `conversation.assign` / `conversation.transfer` / `conversation.resolve` / `conversation.reopen` (conversation)
- `conversation.toggle_ai` (conversation)
- `conversation.export` (conversation) — exportar histórico é ação sensível (LGPD)

**Motor de IA (doc 06)**
- `config.update` (config) — com `keys_changed`; destaque para `system_prompt`
- `agent.prompt.update` (config) — mudança do prompt do agente
- `tool.override` (tool) — enable/disable/description de tool
- `model.change` (config) — troca de modelo do LLM

**Plugins**
- `plugin.enable` / `plugin.disable` / `plugin.install` / `plugin.delete` / `plugin.settings.update` (plugin)

**Billing / créditos**
- `billing.recharge` / `billing.apikey.provision` (billing) — *sem* expor a chave

**Dados**
- `data.export` (qualquer) — exportações em geral (CSV de contatos, etc.)
- `db.migrate_to_postgres` (config) — migração de backend

---

## 6. Imutabilidade, retenção e LGPD

### 6.1 Imutabilidade
Já tratada em §3.4: repo só-insere; opcionalmente trigger/role no Postgres; nunca cascatear delete sobre `audit_log`.

### 6.2 Retenção
- Config `audit_retention_days` (default sugerido **365–730**). ([EnterpriseReady](https://www.enterpriseready.io/features/audit-log/))
- Job periódico (reaproveitar `server/background.py`) que **arquiva/expurga** linhas acima do limite. O expurgo por política de retenção é a *única* deleção permitida — distinta de UPDATE/DELETE programático (que continua proibido).
- LGPD/GDPR não fixam prazo: guardar só pelo tempo necessário ao propósito; reter indefinidamente aumenta risco e exposição. ([Last9](https://last9.io/blog/gdpr-log-management/), [Konfirmity](https://www.konfirmity.com/blog/gdpr-logging-and-monitoring))

### 6.3 LGPD / dados pessoais em logs
Auditoria registra PII por natureza (nomes, telefones, e-mails dos contatos). Cuidados:

- **Nunca logar segredos**: API keys (`openrouter_api_key`), `access_token`, senhas/hashes, credenciais de inbox. No `audit_repo.add()`, manter uma **denylist de chaves** que são removidas/substituídas por `"***"` antes de serializar `before_json`/`after_json`. Para `config.update`, logar apenas os **nomes** das chaves alteradas (`keys_changed`) quando o valor for sensível — não o valor.
- **Mascarar PII** quando o valor não for essencial à evidência: estratégias = *mask* (parcial: `+5511****1234`), *redact* (substituir) ou *hash* determinístico com salt secreto (mantém correlação sem expor o dado). Mascarar **na ingestão**, não na leitura. ([dev.to/PII](https://dev.to/polliog/pii-in-your-logs-is-a-gdpr-time-bomb-heres-how-to-defuse-it-307l), [hoop.dev](https://hoop.dev/blog/gdpr-compliant-log-masking-protecting-pii-in-production-systems), [databunker](https://databunker.org/use-case/gdpr-compliant-logging/))
- **Data subject rights**: ao atender pedido de eliminação de um titular, decidir política — normalmente a trilha de auditoria é **base legal de retenção** (obrigação/legítimo interesse de prestação de contas) e pode ser preservada, mas considerar **pseudonimizar** o `resource_id`/PII ali, mantendo a ação. Documentar essa decisão jurídica (pergunta aberta §9).
- **Acesso restrito**: ler a auditoria é em si uma ação a controlar (`audit.read`, doc 03) — e idealmente acessos à auditoria também são auditados.

---

## 7. Impacto no código e no frontend

### 7.1 Backend (onde plugar)
- `db/tables.py`: adicionar `audit_log` (+ índices). Migration Alembic `alembic revision -m "audit_log"`.
- `db/repositories/audit_repo.py` (novo): `add(...)` (com sanitização centralizada §6.3) + `query(filters)` + `purge(older_than)`. Padrão Core: `with get_engine().begin()` para insert, `connect()` para leitura.
- `server/app.py`:
  - **(a)** dependency/decorator `audit(...)` aplicada às rotas do catálogo §5.
  - **(b)** registrar o handler de bus `*` (allowlist `AUDITABLE`) no startup (junto da inicialização do bus de events).
  - Middleware que gera `request_id` e popula a `contextvar` do ator atual (depende do middleware de auth do doc 03).
- `server/background.py`: job de retenção (purge).
- Endpoints REST (formato `{ok,data,error}`): `GET /api/audit?actor=&resource_type=&resource_id=&action=&from=&to=&limit=&offset=` e `GET /api/audit/export` — ambos protegidos por `audit.read`.

### 7.2 Frontend (Preact, sem build)
- Nova tela `AuditLog.js` no menu da engrenagem, **visível só com `audit.read`** (gating de UI vem do doc 03).
- Filtros: usuário (select de `users`), tipo de recurso, ação, intervalo de datas; paginação.
- Tabela: timestamp · ator (`actor_label` + badge `user`/`system`/`ai`) · ação · recurso · IP. Detalhe expansível mostrando diff `before`/`after` (JSON formatado, com segredos já mascarados pelo backend).
- Botão "Exportar" (CSV/JSON da consulta filtrada).
- **Regras de tema**: usar classes `wa-*` e `.wa-field` (modo escuro), conforme `CLAUDE.md`.

---

## 8. Faseamento / MVP

**Fase 0 — fundação (barato)**
- Tabela `audit_log` + migration + `audit_repo.add()` com sanitização (denylist de segredos).
- Um único **handler de bus `*`** com `AUDITABLE` mínimo: `config.changed`, `tool_override.changed`, `plugin.enabled/disabled`. Já entrega valor sem instrumentar rotas.

**Fase 1 — rotas críticas + ator real (depende do doc 03)**
- Middleware de `request_id` + contextvar de ator.
- Dependency `audit(...)` nas rotas de auth, users/roles, inbox/credenciais, conversas (assign/transfer/resolve), exportações e billing.
- Capturar `ip_address`/`request_id`/`actor_user_id`.

**Fase 2 — UI e operação**
- Tela `AuditLog.js` com filtros + export, gated por `audit.read`.
- Job de retenção + config `audit_retention_days`.

**Fase 3 — endurecimento**
- Trigger/role append-only no Postgres.
- Mascaramento de PII configurável (mask/redact/hash com salt).
- Política de pseudonimização para pedidos LGPD.

---

## 9. Perguntas em aberto

1. **Ator fora da request**: melhor mecanismo para propagar o usuário ao handler de bus — `contextvar` setada no middleware? Como cobrir ações disparadas por jobs/IA (atribuir `system`/`ai`)?
2. **`actor_type = ai`**: quando o motor de IA "decide" algo auditável (ex.: tool call que altera dado), registramos como `ai` com `actor_user_id` = dono da inbox? (cruza com doc 06.)
3. **JSONB vs TEXT**: vale criar um tipo Core que vire `JSONB` no Postgres (consulta interna a `before/after`) ou manter `TEXT` por simplicidade/portabilidade SQLite?
4. **Volume**: incluir `message.sent`/`message.received` na auditoria (alto volume) ou deixá-los só no histórico de mensagens?
5. **LGPD / direito à eliminação**: a trilha de auditoria é base legal de retenção (preservar) ou deve pseudonimizar PII do titular eliminado? Validar com jurídico.
6. **Auditar o acesso à auditoria**: registrar leituras/exports da própria trilha?
7. **Imutabilidade no SQLite**: SQLite não tem roles; basta a disciplina de app + tooling externo (backup), ou aceitamos que append-only forte só existe no Postgres?
8. **Reuso de `execution_steps`**: vale unificar com auditoria ou mantê-los separados (técnico vs. negócio)? Recomendação atual: **separados**.

---

## 10. Referências

- [Enterprise Ready — SaaS App Guide to Audit Logging](https://www.enterpriseready.io/features/audit-log/)
- [SaaS Masters — Audit Logging and Compliance Trails for Your SaaS](https://saasmasters.pro/en/blog/audit-logging-compliance-trails-saas-complete-guide)
- [Sonar — Audit Logging Best Practices, Components & Challenges](https://www.sonarsource.com/resources/library/audit-logging/)
- [Agnite Studio — Audit Logging Design in SaaS Systems](https://agnitestudio.com/blog/audit-logging-saas/)
- [Chris Dermody — Best practices for audit logging in a SaaS app](https://chrisdermody.com/best-practices-for-audit-logging-in-a-saas-business-app/)
- [SQLAlchemy 2.0 — ORM Events](https://docs.sqlalchemy.org/en/20/orm/events.html)
- [SQLAlchemy 2.1 — Tracking object & Session changes with Events](https://docs.sqlalchemy.org/en/21/orm/session_events.html)
- [sqlalchemy_audit (PyPI)](https://pypi.org/project/sqlalchemy_audit/)
- [Medium — Creating an audit table with SQLAlchemy events](https://medium.com/@singh.surbhicse/creating-audit-table-to-log-insert-update-and-delete-changes-in-flask-sqlalchemy-f2ca53f7b02f)
- [django-auditlog (GitHub)](https://github.com/jazzband/django-auditlog)
- [django-auditlog — Usage docs](https://django-auditlog.readthedocs.io/en/latest/usage.html)
- [dev.to — PII in Your Logs Is a GDPR Time Bomb](https://dev.to/polliog/pii-in-your-logs-is-a-gdpr-time-bomb-heres-how-to-defuse-it-307l)
- [hoop.dev — GDPR-Compliant Log Masking](https://hoop.dev/blog/gdpr-compliant-log-masking-protecting-pii-in-production-systems)
- [Databunker — GDPR-Compliant Logging](https://databunker.org/use-case/gdpr-compliant-logging/)
- [Last9 — GDPR Log Management](https://last9.io/blog/gdpr-log-management/)
- [Konfirmity — GDPR Logging and Monitoring](https://www.konfirmity.com/blog/gdpr-logging-and-monitoring)
