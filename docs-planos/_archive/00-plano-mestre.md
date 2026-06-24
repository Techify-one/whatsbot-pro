# 00 — Plano-Mestre: WhatsBot Pro (orquestração dos 9 planos)

> **Status:** PLANO-MESTRE de orquestração. Consolida e sequencia os 9 planos de
> implementação individuais em [`docs-planos/01..09`](.) e a visão geral em
> [`docs-pesquisa/00-visao-geral.md`](../docs-pesquisa/00-visao-geral.md).
>
> **Sincronizado com [`DECISOES.md`](DECISOES.md) (Lote 2, 2026-06-19).** As 74 perguntas funcionais
> estão DECIDIDAS; só a auditoria (P67–P75) segue adiada. Simplificações do Lote 2 já refletidas nas
> ondas: respostas rápidas viram texto puro/global/sem variáveis (P42+P47); cifragem sai do MVP
> (P15); Agno desde o início, sem coexistência longa (P65); só Linux/Docker (P29); cascata de IA sem
> nível de contato (P5). Decisão global de banco: **Postgres pode ser exigido na versão Pro**.
>
> **Não substitui** os planos individuais — eles continuam sendo a fonte de verdade de fases,
> arquivos, migrations e endpoints. Este documento responde **"em que ordem executar?"**, mapeia
> as **dependências entre planos**, define **ondas/marcos** com critério de pronto, lista os
> **riscos transversais e pontos de migração de dados**, e **consolida todas as perguntas em
> aberto** dos 9 planos para o Thiago responder de uma vez.
>
> **Restrição:** nenhum código de produção é alterado por este documento. É roteiro.

---

## 1. Visão geral da versão Pro e objetivo da migração

### 1.1 O que é o WhatsBot Pro

O WhatsBot hoje é um **bot de WhatsApp com IA empacotado como EXE Windows**, com três
suposições cravadas no código: **1 número, 1 agente, sem usuários**. A autenticação é uma senha
única compartilhada; a config é global key-value; o GOWA é um subprocesso cravado no core; e a
unidade de trabalho é o `contact` (uma thread infinita por telefone).

O **WhatsBot Pro** reposiciona o produto como um **sistema de atendimento server-hosted,
single-company, multi-usuário** (Coolify/Docker), no qual **a IA é apenas um dos atendentes**.
Decisão arquitetural central (validada 2026-06-18): a **caixa de entrada/atendimento é CORE**; os
**canais (providers) são plugáveis**, inclusive o próprio GOWA, que **nasce como provider-plugin**
no v1.

**NÃO é multi-tenant.** Nenhuma tabela ganha `account_id`; o desenho apenas evita fechar portas
para multi-tenant futuro.

### 1.2 As entidades novas (o coração da migração)

A maior parte do esforço é introduzir três entidades novas e re-escopar o que hoje é global:

```
User ──< InboxMember >── Inbox ──1:1── Channel(Provider: gowa | cloud_api | telegram | ...)
 │                          │
 │                          │ 1:N
 │                          ▼
 └─assigned_to─< Conversation >── contact ── Contact ──< ContactInbox(source_id) >── Inbox
                     │  status: open|pending|resolved|snoozed
                     ▼
                 Message (já existe; ganha conversation_id e channel_id)

Agent (config code-in-DB: prompt/model/tools/vars + CÓDIGO Python das tools) ──> Inbox
AuditLog ── registra ações de ── User
QuickReply / CustomAttributeDef / SavedFilter ── escopo ── Inbox/Contact/Conversation/User
```

### 1.3 Decisões já tomadas (não re-litigar — só executar)

- **Caixa de entrada é CORE; providers plugáveis.** GOWA já nasce **provider-plugin** no v1.
- Isso exige **3 capacidades de runtime CORE** (plano 09): (i) lifecycle de plugin
  `setup/teardown` aguardado, (ii) supervisor de background-tasks, (iii) serviço de subprocesso
  gerenciado. **As 3 são CORE — não podem ser plugin** (infra que os plugins consomem).
- **Modelo Chatwoot de 3 níveis:** Contact → ContactInbox(`source_id`) → Conversation (várias por
  contato, reabríveis). Schema nasce no formato final; UI simplifica no MVP.
- **Code-in-DB:** agentes, prompts, variáveis **E o código Python das tools** vivem no banco
  (estilo `/opt/gerenciamento-ia`), com installer (`pip install` + `importlib.reload`) e runner
  isolado em subprocesso.
- **RBAC simples** (tabelas no próprio DB; sem ABAC/ReBAC/OpenFGA). Admin controla tudo;
  atendente só a tela de resposta + suas inboxes.
- **Stack mantida:** Python 3.11, FastAPI, SQLAlchemy 2.0 **Core** (sem ORM declarativo) +
  Alembic, SQLite default / Postgres opcional, frontend Preact+HTM **sem build step**, bus de
  events (fire-and-forget) + filters (interceptive).
- **Simplificações do Lote 2 (DECISOES.md, 2026-06-19), já assumidas:**
  - **Cascata de IA sem nível de contato (P5):** IA global → inbox → conversa. `contacts.ai_enabled`
    sai do gate; o `toggle-ai` age na **conversa**.
  - **Sem cifragem no MVP (P15):** tokens/credenciais em texto puro no banco; sem chave mestra.
    Risco aceito; cifrar antes de produção séria.
  - **Só Linux/Docker (P29):** die-with-parent via `PR_SET_PDEATHSIG`; Job Object/Windows adiado.
  - **Respostas rápidas mínimas (P42+P47):** texto puro, lista **global única**, **sem escopo** e
    **sem variáveis `{{...}}`**.
  - **Agno-first (P65):** construir o motor sobre o Agno desde a 1ª fase; legado só como fallback
    mínimo/curtíssimo, sem coexistência longa.
- **Banco — decisão global (DECISOES.md):** projetar para SQLite **e** Postgres, mas **não sacrificar
  uma boa solução Postgres por causa do SQLite**. A versão **Pro pode exigir Postgres** para recursos
  que dependem dele (JSONB+GIN nos filtros, append-only forte da auditoria).

### 1.4 Objetivo da migração

Transformar a base single-número/single-agente/sem-usuários numa **plataforma de atendimento
multi-usuário com múltiplos canais e múltiplos agentes de IA**, **sem quebrar instalações
existentes** (migração de dados idempotente a partir do `contacts` plano atual) e **sem perder o
sistema de plugins** já maduro — pelo contrário, estendendo-o (canais, lifecycle, runtime).

---

## 2. Grafo de dependências entre os planos

### 2.1 Os planos

| # | Plano | Papel |
|---|-------|-------|
| 09 | Fundação de runtime (lifecycle/supervisor/subprocesso) | **Base de plataforma** |
| 01 | Inbox e conversas (Contact→ContactInbox→Conversation) | **Base de domínio** |
| 02 | Canais e providers (contrato, registry, GOWA-plugin, multi-número) | Canais |
| 03 | RBAC, usuários e permissões | **Base de identidade** |
| 04 | Respostas rápidas (canned responses) | Operação |
| 05 | Atributos personalizados (contato/conversa) | Operação |
| 06 | Motor multi-agente Agno (code-in-DB) | IA |
| 07 | Auditoria (trilha append-only) | Transversal |
| 08 | Filtros avançados de conversas + views salvas | Operação |

### 2.2 Grafo (quem precisa de quem)

```
                 ┌──────────────────────────────────────────────┐
                 │  09 FUNDAÇÃO DE RUNTIME (i/ii/iii)            │  ← não depende de ninguém
                 │  setup/teardown · supervisor · subprocesso   │
                 └───────────────┬──────────────────────────────┘
                                 │ habilita
                                 ▼
        ┌────────────────────────────────┐     ┌───────────────────────────┐
        │ 02 CANAIS/PROVIDERS             │     │ 03 RBAC / USUÁRIOS        │
        │ contrato+registry (Fase 0)      │     │ users/roles/sessions      │  ← independente p/
        │ GOWA-plugin (Fase 3 ⟸ 09)       │     │ (Fase 1-3 independentes)  │     começar
        │ cria: channels, inbox default   │     │ cria: users, inbox_members│
        └───────────┬─────────────────────┘     └─────────────┬─────────────┘
                    │ inboxes/channels                          │ users/current_user
                    ▼                                           ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │ 01 INBOX E CONVERSAS                                               │
        │ contact_inboxes, conversations, messages.conversation_id          │
        │ FK → inboxes(02/stub) · FK → users(03/nullable) · channel_id(02)  │
        │ backfill: contact → 1 contact_inbox + 1 conversa                  │
        └───────────┬──────────────────────────────────────────────────────┘
                    │ conversations + conversation_repo.list
        ┌───────────┼───────────────┬───────────────┬─────────────────┐
        ▼           ▼               ▼               ▼                 ▼
   ┌─────────┐ ┌─────────┐   ┌────────────┐  ┌────────────┐   ┌──────────────┐
   │ 04 QUICK│ │ 05 CUSTOM│   │ 06 MOTOR   │  │ 08 FILTROS │   │ 07 AUDITORIA │
   │ REPLIES │ │ ATTRS    │   │ AGNO       │  │            │   │  (⏸️ ADIADA) │
   │ texto/  │ │ Fase1-4  │   │ Agno-first │  │ depende de │   │ Fase0 indep. │
   │ global  │ │ indep. de│   │ desde F0;  │  │ 01+05+03   │   │ Fase1 ⟸ 03   │
   │ 1 fase  │ │ 01; F5⟸01│   │ F4-5⟸01+02 │  │            │   │              │
   │ indep.  │ │ F6⟸08    │   │            │  │            │   │              │
   └─────────┘ └─────────┘   └────────────┘  └────────────┘   └──────────────┘
   P42+P47: sem escopo                P65: sem coexistência longa c/ legado
```

### 2.3 Tabela de dependências fortes (bloqueantes)

| Plano | Depende de (bloqueante) | Por quê |
|-------|-------------------------|---------|
| **09** | — | Fundacional. Começa primeiro. |
| **02** | **09** (para a Fase 3: GOWA-plugin/subprocesso) | A extração do GOWA exige as capacidades i/ii/iii. Fases 0-2 do 02 (contrato/registry/Cloud webhook-only) **não** dependem de 09. |
| **03** | — (para Fases 1-3) | RBAC por papel é independente. Scoping por inbox (Fase 4) depende de `inbox_members` (01/02). |
| **01** | **02** (`inboxes`/`channels`) + **03** (`users`) | `conversations` referencia `inboxes(id)` e `assignee_user_id→users(id)`. Mitigado por **stubs** (ver §4.3). |
| **04** | — / **03** (gate `quickreply.manage`) | **Simplificado (P42+P47):** texto puro, **global, sem escopo, sem variáveis** — uma fase só, greenfield independente. Caem as fases de escopo inbox/user e de variáveis. |
| **05** | — (Fases 1-4 contato) / **01** (Fase 5 conversa) / **08** (Fase 6 índices) | Fases 1-4 independentes. **Atributos de CONTATO e de CONVERSA** (P54). Filtros: JSONB+GIN no Postgres, índice de expressão no SQLite (P55 + decisão de banco). |
| **06** | **01** (Fases 4-5) + **02** (Fase 4) + **03** (Fase 3) + **09** (runner reusa subprocesso) | **Agno-first (P65):** motor sobre o Agno desde a Fase 0; legado só fallback curto. Fases 0-3 operam por `phone` como hoje. |
| **07** | — (Fase 0) / **03** (Fase 1 ator real) | Fase 0 (bus `*` + `actor=system`) entrega valor sem RBAC. |
| **08** | **01** (`conversations`) + **05** (`custom_attributes`) + **03** (`current_user`/scoping) | Filtro degrada sobre `contacts` se 01 atrasar. |

### 2.4 Observações sobre a base

- **A base de runtime (09) e a base de domínio (01) + RBAC (03) são o tripé fundacional.** Tudo o
  mais se ancora nelas.
- **09 vem antes de tudo** porque destrava o GOWA-plugin (02 Fase 3) e o runner de tools (06
  Fase 3). Suas Fases 1-3 (lifecycle + supervisor) podem rodar **em paralelo** com 02 Fase 0 e 03
  Fases 1-3, que não tocam subprocesso.
- **01 é o gargalo de integração**: depende de 02 (inboxes) e 03 (users). A estratégia acordada
  nos planos é **stubs mínimos** (`inboxes` stub no 01 §1.1, `inbox_members` stub no 03 §1.1,
  `assignee_user_id` nullable sem FK) para destravar 01 sem esperar 02/03 completos.

---

## 3. Sequenciamento em ondas/marcos

Cada onda agrupa fases de planos diferentes que fazem sentido entregar juntas. O critério de
pronto de cada marco é **verificável** (build/teste/fluxo manual).

> **Princípio de ordenação das migrations Alembic (DECIDIDO — P82, linear):** as migrations
> encadeiam linearmente por `down_revision` na ordem em que forem implementadas, apontando para o
> head real no momento do merge. Reservas conhecidas: 02 cria `channels` antes de 01 (FK
> `conversation.channel_id→channels`); 03 cria `users` antes de 01 (FK `assignee_user_id`) ou 01 usa
> nullable-sem-FK. **Sem branches Alembic.**

### Onda 0 — Fundação de runtime (plataforma)

**Planos/fases:** 09 Fases 1-3 (lifecycle aguardado + fim do `os._exit` cego + supervisor de
tasks). Pode rodar **em paralelo** com Onda 1.

**Entrega:** `setup/teardown` de plugin chamados e aguardados; teardown roda antes do hard-exit do
toggle (sem órfãos); as 4 tasks core migram para o `TaskSupervisor`; `ctx.spawn_task` reservado.

**Critério de pronto do marco:**
- Plugin com `entry.lifecycle` tem `setup` no startup e `teardown` aguardado no shutdown (um
  teardown que dorme 2s atrasa o shutdown ~2s, não é cortado).
- Disable de plugin roda `on_unload` **antes** de "Restarting now".
- As 4 tasks core rodam pelo supervisor; uma task `PERMANENT` que falha é relançada com backoff e
  vira `crashed` ao estourar 3/60s sem derrubar o app.
- `tests/test_endpoints.py` verde.

### Onda 1 — Contrato de canal + identidade (sem subprocesso)

**Planos/fases:** 02 Fase 0 (contrato `Channel` + `ChannelRegistry` + tabelas `channels`/
`channel_credentials` + adapter GOWA interno + "1 canal default" + `entry.channels`); 03 Fases 1-3
(tabelas RBAC + Argon2id + sessões server-side + middleware de auth, preservando exemptions de
webhook/health).

**Entrega:** o GOWA continua rodando como hoje, mas **através do registry** (canal `default`);
usuários reais com sessões opaque token; bootstrap do 1º admin.

**Critério de pronto do marco:**
- Instalação atual recebe/responde mensagens passando pelo `ChannelRegistry` (sem
  `if provider=="gowa"` no handler).
- `POST /api/auth/bootstrap` cria 1º admin quando DB vazio; login cria `user_sessions`; `me`
  retorna roles+permissions; **`POST /api/webhook` e `/health` continuam abertos** (regressão
  crítica testada).
- Um plugin de teste com `entry.channels` é descoberto e `register_provider` é chamado.

### Onda 2 — Domínio de conversas (o núcleo do produto)

**Planos/fases:** 01 Fases 1a-1e (schema `contact_inboxes`/`conversations` + `messages.
conversation_id` + reescopo de `contacts`; backfill idempotente; repos + webhook + handler por
conversa; API `GET/PATCH /api/conversations` + eventos WS/bus; frontend lista de conversas + abas
+ fila não-atribuídas); 03 Fase 4 (autorização `Depends(Require)` + scoping por inbox, agora que
`inbox_members`/`conversations` existem).

**Entrega:** o sistema opera por **conversa** (abrir/atribuir/resolver/reabrir), com gate de IA
por conversa (cascata IA global → inbox → conversa, **sem nível de contato** — P5;
`contacts.ai_enabled` sai do gate), fila de não-atribuídas e RBAC efetivo.

**Critério de pronto do marco:**
- Num clone do `whatsbot.db` de produção, após `alembic upgrade head`:
  `COUNT(conversations)==COUNT(contacts)`, `messages.conversation_id` 100% preenchido, re-rodar não
  duplica (idempotente).
- Mensagem inbound cria/anexa a conversa correta; IA só responde quando a cascata (global → inbox →
  conversa) permite; `transfer_to_human` põe a conversa em `open`+`ai_active=0`.
- Atendente vê só conversas das suas inboxes; admin vê tudo; `PUT /api/config` por atendente → 403.
- Fluxo manual no painel: pegar conversa da fila, resolver, reabrir — tudo via WS sem reload, modo
  escuro OK.

### Onda 3 — Operação do atendente

**Planos/fases:** 04 (respostas rápidas — **uma fase só:** texto puro, **global, sem escopo, sem
variáveis** — P42+P47); 05 Fases 1-4 (atributos custom de contato: schema, endpoints, UI, tool de
IA). **Auditoria (07) está ⏸️ ADIADA** (P67–P75) — fica para o fim, fora do MVP.

**Entrega:** atalhos `/oi-anna` no composer (texto puro, lista global); atributos personalizados de
contato editáveis por humano e IA.

**Critério de pronto do marco:**
- Atendente digita `/` → dropdown filtrado (lista global única) → expande `content` (texto puro, sem
  `{{...}}`), sem enviar. `short_code` com UNIQUE global; criação/edição gateada por
  `quickreply.manage`.
- Admin cria atributo `plano` (list) e `vip` (checkbox); aparecem no painel do contato; IA grava
  `plano=premium` via tool; valor inválido rejeitado.

### Onda 4 — Multi-canal de verdade

**Planos/fases:** 02 Fases 1-3 (provider de teste validando i+ii; **WhatsApp Cloud API**
webhook-only — tokens **em texto puro no MVP** (sem cifragem, P15); **serviço de subprocesso
gerenciado** + **GOWA extraído para `storages/plugins/gowa/`** + multi-número); 09 Fases 4-6
(subprocesso gerenciado consolidado + exposição via `ctx.spawn_subprocess` + provider de teste).
**Só Linux/Docker (P29):** die-with-parent via `PR_SET_PDEATHSIG`; Job Object/Windows adiado.

> **Acoplamento forte 02↔09:** a Fase 3 do 02 (GOWA-plugin) **consome** a Fase 4 do 09
> (subprocesso). Entregar juntas. O serviço de subprocesso é descrito em ambos; tratar o 09 como o
> dono da implementação e o 02 como consumidor (ver pergunta P82 sobre `runtime/` vs `server/`).

**Entrega:** dois números coexistindo (GOWA + Cloud API ou 2× GOWA); quem não usa GOWA não roda o
binário; tela de Canais; tokens em **texto puro** no MVP (cifragem revisitada antes de produção
séria).

**Critério de pronto do marco:**
- Adicionar canal Cloud API persiste credenciais (texto puro no MVP, P15); handshake `hub.challenge`
  funciona; mensagem de teste normaliza e responde pelo mesmo canal dentro da janela 24h.
- GOWA roda como **plugin**; desabilitá-lo derruba o subprocesso limpo (sem órfão); matar o Python
  mata o GOWA (die-with-parent via `PR_SET_PDEATHSIG`, Linux); boot mata GOWA stale.
- Dois canais GOWA (2 números) conectam por QR próprio e respondem pelo número de origem.
- Instalação existente (canal default da Onda 1) continua conectada após virar plugin.

### Onda 5 — Motor multi-agente (IA)

**Planos/fases:** 06 Fases 0-6 (**Agno-first** — P65: spike Agno → tabelas `ai_*` → agente
configurável **sobre o Agno desde o início**, com o legado só como fallback mínimo/curtíssimo →
code-in-DB com installer + runner isolado → multi-agente por inbox + CRUD → roteamento por handoff →
hot-reload/versionamento; structured output via `output_schema` Pydantic do Agno — P64); 05 Fase 5
(atributos de conversa, agora que `conversations` existe).

**Entrega:** vários agentes do banco, escolha por inbox, código de tool editável pelo painel sem
deploy, handoff entre IAs, hot-reload. **Sem período longo de coexistência com o handler legado**
(P65).

**Critério de pronto do marco:**
- Com o motor Agno, resposta com paridade de comportamento ao handler legado (mesmo prompt/modelo/
  tools); legado mantido só como fallback curto até a paridade estar comprovada; `executions`
  registra `agent_key`.
- Criar/editar tool pelo painel reflete sem deploy; tool com dep não-allowlisted falha com
  `install_status=failed`; tool com `while True` morta por timeout sem travar o webhook; worker sem
  a chave do LLM no ambiente.
- 2+ agentes em inboxes distintos respondem corretamente; handoff comercial→suporte preserva
  contexto; loop cortado em depth 5.
- Editar prompt/agente reflete na próxima mensagem **sem restart**.

### Onda 6 — Busca, segmentação e endurecimento

**Planos/fases:** 08 Fases 1-3 (filtros básicos por query params → query builder estruturado
estilo Chatwoot + custom attrs + tags set-ops → views salvas `saved_filters`); 05 Fase 6
(índices de custom attrs `filterable` — **JSONB+GIN no Postgres**, índice de expressão no SQLite);
06 Fase 7-8 opcionais (extrair UI como plugin / aposentar handler legado).
**Auditoria (07) continua ⏸️ ADIADA (P67–P75)** — será a última coisa, se for feita; quando entrar,
a imutabilidade append-only forte é garantia de **Postgres** (P74 + decisão de banco).

**Entrega:** filtros multi-dimensionais com chips/views salvas; (opcional) handler legado
aposentado.

**Critério de pronto do marco:**
- `GET /api/conversations?status=open&assignee=me&labels=lead` e `POST /api/conversations/filter`
  (payload Chatwoot) filtram em SQLite **e** Postgres; views salvas aplicáveis em 1 clique; scope
  global só admin.
- `EXPLAIN` mostra uso de índice nos filtros típicos (GIN no Postgres); keyset pagination.

---

## 4. Riscos transversais e pontos de migração de dados

### 4.1 Migração de instalações existentes (single-número) — o risco número 1

Instalações atuais têm `contacts` plano (1 telefone = 1 thread), sessão GOWA ativa, senha única, e
possivelmente plugins já populados. A migração precisa ser **idempotente e não-destrutiva**:

| Ponto de migração | Plano | Risco | Mitigação |
|-------------------|-------|-------|-----------|
| **Backfill contact → contact_inbox + conversation** | 01 §2.2 | Duplicação se a migration rodar 2×; `source_id` errado | Guard "só roda se `conversations` vazia"; invariantes verificáveis num clone. **DECIDIDO (P12): `source_id` = JID + LID (estilo Evolution), guardar ambos** — backfill precisa do JID de cada contato existente; chave primária a detalhar na implementação do 01. |
| **`messages.conversation_id` em massa** | 01 §1.5 | UPDATE gigante / NULL residual | `UPDATE` por contato (não JOIN gigante); critério `COUNT(WHERE NULL)==0` |
| **Drop do UNIQUE de `contacts.phone`** | 01 §2.1 | SQLite não dropa constraint via ALTER | `batch_alter_table` (recria a tabela) — testar em clone |
| **"1 canal default" GOWA** | 02 §0.6 | Número conectado perde a sessão | Migration insere `channels(id=default, device_id=whatsbot)`; preserva sessão |
| **GOWA vira plugin em instalação já populada** | 02 §3.2 / P22 | `bootstrap_initial_plugins` só copia em pasta vazia → instalação existente NÃO recebe o plugin `gowa` e o número desconecta | Bootstrap especial no upgrade: se existe canal default GOWA, copiar `assets/plugin_examples/gowa/` e `enabled=1`, preservando a sessão (P22) |
| **Senha única → bootstrap de admin** | 03 P34 | Instalação sem senha fica inacessível ou insegura | Forçar bootstrap (bloqueia `/api/*` até criar 1º admin); env headless `WHATSBOT_ADMIN_EMAIL/PASSWORD` para Docker (P34) |
| **`is_archived` ortogonal ao status** | 01 P10 | Perda de semântica de arquivo | **DECIDIDO (P10): archive ortogonal ao status** (`is_archived` é flag independente; dá para arquivar conversa aberta). Sem backfill arquivado→`resolved`. |
| **Tokens/credenciais de canal em texto puro** | 02 P15 | **MVP sem cifragem** — tokens do WABA e credenciais ficam legíveis no banco | **Risco aceito conscientemente (P15)**; sem chave mestra no MVP. **Revisitar e cifrar antes de produção séria** (a chave mestra do P15 original fica adiada). |

### 4.2 Riscos de runtime/arquitetura

- **`os._exit(0)` no toggle pula finalizers** (09 Fase 2): hoje um subprocesso de plugin viraria
  órfão. **DECIDIDO (P22/P25): restart-do-processo no MVP** — teardown aguardado antes do hard-exit
  (timeout fixo ~10s, P31) + die-with-parent. **Risco residual:** hot-unload sem restart fica fora do
  MVP (downtime de poucos segundos a cada toggle de plugin).
- **Sessão WhatsApp duplicada** se dois GOWAs subirem: **mitigação** stale-kill no boot (PID-file) +
  die-with-parent via `PR_SET_PDEATHSIG` (09 Fase 4, **só Linux/Docker — P29**), tornando `pkill` dos
  launchers redundante. Job Object/Windows adiado.
- **Code-in-DB é a maior superfície de segurança** (06 Fase 3): execução de código Python que vive
  no banco. **Mitigação:** runner em subprocesso isolado (RLIMIT_*, timeout, sem chave do
  LLM/credenciais), edição = papel ADM, gate humano para "IA cria tool" (P63). **DECIDIDO (P66): sem
  allowlist de dependências no MVP** para facilitar — ⚠️ aumenta a superfície; **revisitar no
  endurecimento**. Seccomp/AppArmor a confirmar nos testes do Thiago em Docker/Linux (P62). Modelo de
  ameaça é baixo (1 empresa, servidor próprio) mas não nulo.
- **Conflito de versões do Agno** com `pydantic`/`sqlalchemy`/`openai` (06 Fase 0): de-risk no
  spike antes de qualquer schema; pinar versões.
- **JSON mutation tracking** (05): `JSON/JSONB` não detectam mutação in-place — **sempre reatribuir
  o dict inteiro** no UPDATE. Documentar no CLAUDE.md.
- **Workers do uvicorn > 1** quebram invalidação de cache local do hot-reload (06 P57): **DECIDIDO
  (P57): 1 worker no dia-1**; TTL curto + invalidação por evento bastam; reavaliar com mecanismo de
  sincronização (TTL/`LISTEN/NOTIFY`) se a carga exigir.
- **Banco de dados — decisão global (DECISOES.md):** projetar para SQLite **e** Postgres, mas a
  versão **Pro pode exigir Postgres** quando o SQLite não der para fazer algo de forma limpa.
  Recursos Postgres-only documentados, degradando com elegância no SQLite:
  - **Filtros de custom attrs (P55):** Postgres libera **JSONB + índice GIN**; no SQLite, índice de
    expressão só para campos `filterable`.
  - **Imutabilidade da auditoria (P74, adiada):** append-only forte via **trigger/role do Postgres**
    passa a ser caminho aceitável de exigir (auditoria é feature Pro); no SQLite, disciplina de app.
  - **`display_id` (P6)** e **índices de quick replies (P42)** funcionam nos dois sem custo (a
    tabela-contador e os índices parciais são portáveis) — sem trava.

### 4.3 Riscos de coordenação entre planos (ordem/stubs)

- **Stubs duplicados:** 01 cria stub de `inboxes`; 03 cria stub de `inbox_members`; 04/05/08 criam
  FKs nullable-sem-amarração. **Risco:** divergência entre o stub e a versão final do dono. **Regra:**
  o plano "dono" (02 para `inboxes`/`channels`; 01 para `conversations`/`contact_inboxes`; 03 para
  `users`/`inbox_members`) faz `ALTER` **aditivo** sobre o stub, nunca recria. As FKs reais entram
  por migration de amarração quando o dono chega (`batch_alter_table` no SQLite).
- **Numeração Alembic colidente:** vários planos reservam revisões na mesma data. **DECIDIDO
  (P82/P75): encadear linear** pelo head real no momento do merge; sem branches.
- **Duplicidade auditoria rota×bus** (P73, ⏸️ ADIADA): regra invariante prevista — rota com evento de
  bus → cobre por bus; rota sem evento → dependency `audit()`. **Só vale quando a auditoria sair do
  adiamento.**

---

## 5. Perguntas em aberto consolidadas

> Reúne **todas** as perguntas das seções "Perguntas em aberto" dos 9 planos, **renumeradas
> globalmente (P1..P83)** e agrupadas por tema. Cada uma traz contexto + opções + recomendação do
> plano de origem. A referência ao plano de origem está entre colchetes (ex.: `[01·q5]`).
>
> **Status (sincronizado com [`DECISOES.md`](DECISOES.md), 2026-06-19):** as **74** perguntas
> funcionais estão **✅ DECIDIDAS** — a decisão de cada uma aparece anotada ao lado, preservando o
> contexto original. Só a **auditoria (P67–P75, 9 perguntas)** segue **⏸️ ADIADA** por escolha do
> Thiago (será a última coisa a ser feita, se for feita). A fonte da verdade das decisões é o
> `DECISOES.md`.

### Tema A — Modelo de conversa, ciclo de vida e identidade (plano 01)

**P1. Ordem com docs 02 e 03 / FKs para `inboxes` e `users`.** `[01·q1]`
- **✅ DECIDIDO (2026-06-19):** **Stubs (opção a)** — 01 cria esqueletos de
  `inboxes`/`assignee_user_id` sem FK; 02/03 fazem ALTER aditivo depois.
- *Contexto:* `conversations` referencia `inboxes(id)` e `assignee_user_id→users(id)`, que podem
  não existir quando 01 rodar.
- *Opções:* (a) 01 cria stubs mínimos de `inboxes` e `assignee_user_id` **sem** FK, 02/03 fazem
  ALTER aditivo; (b) bloquear 01 até 02/03 prontos.
- *Recomendação:* **(a)** — stub de `inboxes` no 01, `assignee_user_id` nullable sem FK na Fase 1,
  FK quando `users` existir.

**P2. Janela de reabertura de conversa.** `[01·q2]`
- **✅ DECIDIDO (2026-06-19):** **sempre reabrir a mesma conversa** quando o cliente volta a falar
  (não criar nova). Combina com P3.
- *Contexto:* cliente volta a falar após `resolved` → reabrir a mesma ou criar nova?
- *Opções:* (a) reabrir se `resolved` há < N horas, senão nova; (b) sempre nova; (c) sempre reabrir
  a última.
- *Recomendação:* **(a)** com `N=24h` configurável por inbox (`conversation_reopen_window_hours`).

**P3. `resolved` vs `closed` terminal.** `[01·q3]`
- **✅ DECIDIDO (2026-06-19):** **só `open`/`closed` (resolved)** no MVP. Resolvida some do painel de
  abertas; nova mensagem do cliente reabre. Estado "aguardando" fica para o futuro.
- *Contexto:* Zendesk tem `closed` imutável; Intercom só `resolved`+reabertura.
- *Recomendação:* **só `resolved`** no MVP (Intercom-style); reabertura cobre "voltou a falar".

**P4. Status inicial com bot ativo: `open` ou `pending`?** `[01·q4]`
- **✅ DECIDIDO (2026-06-19):** conversa **nasce `open`** e entra na fila; indicador de "IA ativa"
  mostra que o robô está atendendo.
- *Contexto:* Chatwoot nasce `pending` quando há agent bot; `open` é mais simples.
- *Recomendação:* **nascer `open`** no MVP, usando `ai_active` como sinal de "bot atendendo";
  `pending`=bot na Fase 2.

**P5. Cascata de IA (`ai_active` × `ai_enabled` × `agent_bot_enabled`).** `[01·q5]`
- **✅ DECIDIDO (2026-06-19) ⚠️ MUDANÇA:** cascata de IA = **IA global → inbox → conversa (SEM nível
  de contato)**. Não precisa desligar a IA por contato. O `toggle-ai` age na **conversa**;
  `contacts.ai_enabled` **sai do gate** (aposentado/ignorado).
- *Contexto:* inbox→conversa→contato(default); `toggle-ai` passa a operar a conversa ativa.
- *Recomendação:* confirmar a cascata da §5 do plano 01 exatamente; `contacts.ai_enabled` vira
  "default para novas conversas". **Muda a semântica do `toggle-ai` — precisa do OK do Thiago.**

**P6. `display_id`: global ou por inbox? Como gerar concorrência-safe?** `[01·q6]`
- **✅ DECIDIDO (2026-06-19):** **global por conta via tabela-contador** (`UPDATE … RETURNING n`
  atômico) — única opção portável SQLite+Postgres e concorrência-safe (sem o race do `MAX()+1`).
  Escopo global (não por inbox). Se um dia só Postgres, dá para trocar por SEQUENCE — não precisa.
- *Opções:* (a) `MAX()+1` na transação write; (b) tabela contador com UPDATE atômico; (c) Postgres
  SEQUENCE (precisa funcionar em SQLite também).
- *Recomendação:* **global + tabela contador** (`conversation_counters`) — dialect-agnóstico, sem
  race.

**P7. Auto-resolução por inatividade.** `[01·q7]`
- **✅ DECIDIDO (2026-06-19):** **desligada**, fica como extra para depois.
- *Recomendação:* **desligada por padrão** no MVP; introduzir na Fase 2 (`auto_resolve_after_hours`,
  default off).

**P8. Grupos de WhatsApp (`is_group=1`) viram conversa?** `[01·q8]`
- **✅ DECIDIDO (2026-06-19):** grupos viram **conversa normal**, com **badge de "é grupo"**. **NÃO
  ocultar** grupos das filas (diverge da recomendação original).
- *Opções:* (a) grupos viram conversa normal; (b) grupos fora do fluxo de atendimento.
- *Recomendação:* **(a) criar a conversa** (não quebrar histórico) mas **ocultar grupos das filas**
  na UI por padrão.

**P9. Visibilidade: atendente vê só as dele ou a fila inteira?** `[01·q9]` (decisão final no 03)
- **✅ DECIDIDO (2026-06-19):** **modelo Chatwoot de membership** — atendente só vê/atua nas inboxes
  em que é membro; fora delas, não vê nada. Dentro, conforme permissão.
- *Recomendação:* atendente vê **suas inboxes** (fila não-atribuídas + suas conversas);
  `conversation.read_all` libera tudo. Alinhar com 03.

**P10. Migração de `is_archived`: vira `resolved`?** `[01·q10]`
- **✅ DECIDIDO (2026-06-19):** **archive ortogonal ao status (opção b)** — dá para arquivar
  conversas mesmo abertas; `is_archived` é flag independente do status. (Sem backfill
  arquivado→`resolved`.)
- *Opções:* (a) arquivado→`resolved` na conversa; (b) archive ortogonal a status.
- *Recomendação:* **(a)** no backfill, mantendo `is_archived` no contato por compat.

**P11. Merge de contatos (telefones diferentes da mesma pessoa).** `[01·q11]`
- **✅ DECIDIDO (2026-06-19):** **fora do MVP**, previsto para o futuro. Schema deixa o caminho
  aberto.
- *Recomendação:* **fora do MVP**; schema já deixa o caminho aberto (`phone` é atributo do
  `contact_inbox`). Fase 3.

**P12. `source_id` no WhatsApp: número (`5511...`) ou JID (`...@s.whatsapp.net`/`lid`)?** `[01·q12]`
- **✅ DECIDIDO (2026-06-19):** **JID + LID (opção b, estilo Evolution)**, priorizando estabilidade —
  guardar ambos. (Diverge da recomendação original de número normalizado.) Detalhe de qual é a chave
  primária a decidir na implementação do 01.
- *Contexto:* precisa casar com 02 e `group_mentions` (phone vs lid). **Decidir antes do backfill.**
- *Opções:* (a) número normalizado; (b) JID puro (mais estável p/ `lid`/multi-device).
- *Recomendação:* **(a) número normalizado** no MVP (mínimo atrito), guardando JID em
  `custom_attributes`/coluna futura se 02 exigir.

### Tema B — Canais, providers, runtime de subprocesso (planos 02 e 09)

**P13. Webhook por device no GOWA (confirmar na build empacotada).** `[02·q1]`
- **✅ DECIDIDO (2026-06-19):** rotear pela combinação **`device_id` do payload + path por canal
  (opção a)**; confirmar nos testes que `device_id` vem em todos os tipos de evento.
- *Contexto:* a v8 manda `device_id` no topo do payload, mas o webhook é global (não por device).
- *Opções:* (a) confiar no `body["device_id"]` + path do canal; (b) `--webhook` por device se a
  build suportar.
- *Recomendação:* **(a)** path como fonte primária + `device_id` como confirmação; confirmar
  empiricamente que `device_id` vem em **todos** os tipos de evento.

**P14. Suportar `dedicated_process` (Opção B) já no MVP?** `[02·q2]`
- **✅ DECIDIDO (2026-06-19):** MVP só **Opção A (1 processo, N devices)**; coluna `gowa_isolation` já
  no schema para habilitar dedicated depois.
- *Opções:* (a) só Opção A (1 processo, N devices); (b) já expor `gowa_isolation=dedicated_process`.
- *Recomendação:* **(a)** no MVP, com a coluna `gowa_isolation` já no schema (custo zero) para
  habilitar depois.

**P15. Origem da chave mestra de cifragem (`WHATSBOT_SECRET_KEY`).** `[02·q3]`
- **✅ DECIDIDO (2026-06-19) ⚠️ MUDANÇA:** **MVP sem cifragem** — tokens/credenciais em **texto puro**
  no banco; **sem chave mestra**. Anula a chave mestra do P15 original. ⚠️ Risco aceito; **revisitar e
  cifrar** antes de produção séria.
- *Contexto:* Docker tem `.env`; EXE Windows não.
- *Opções:* (a) env + arquivo gerado no 1º boot; (b) DPAPI no Windows; (c) derivar de segredo
  existente.
- *Recomendação:* **(a)** — env quando presente; senão `storages/secret.key` no 1º boot;
  documentar que perder o arquivo invalida tokens. DPAPI como melhoria futura.

**P16. Mídia da Cloud API: baixar/cachear como `media_path` ou referenciar URL temporária?** `[02·q4]`
- **✅ DECIDIDO (2026-06-19):** **baixar e cachear** em `statics/media/` (opção a).
- *Opções:* (a) baixar e cachear em `statics/media/`; (b) referenciar URL temporária (expira).
- *Recomendação:* **(a)** — mantém o player do inbox idêntico ao GOWA.

**P17. Janela de 24h na UI (sinalização ao operador).** `[02·q5]`
- **✅ DECIDIDO (2026-06-19):** **bloquear texto livre + template fora da janela (opção a)**, mas
  **depois** que o principal funcionar.
- *Opções:* (a) bloquear input livre + seletor de template fora da janela; (b) só avisar.
- *Recomendação:* **(a)** — rastrear "último inbound" por conversa; fora da janela, bloquear texto
  livre e oferecer template.

**P18. Idempotência de webhook por `(channel_id, external_msg_id)`.** `[02·q6]`
- **✅ DECIDIDO (2026-06-19):** índice único `(channel_id, external_msg_id)` (implementado no plano
  01).
- *Contexto:* Meta/Telegram reentregam; pertence ao modelo de mensagens (plano 01).
- *Recomendação:* **índice único `(channel_id, external_msg_id)` implementado no plano 01**; aqui só
  garantir `external_msg_id` normalizado por todo provider.

**P19. Sincronização de templates Cloud API.** `[02·q7]`
- **✅ DECIDIDO (2026-06-19):** **upload pelo painel** + sincronizar **sob demanda** (quando alguém
  abrir/buscar na API), **sem** sync periódico em segundo plano. *(Ripple P15: tokens do WABA **sem
  cifragem** no MVP.)*
- *Opções:* (a) sincronizar do WABA (task no supervisor); (b) cadastro manual; (c) ambos.
- *Recomendação:* **(a) sob demanda** (botão "sincronizar") na Fase 2, evoluindo para periódica.
  Submissão de templates fora de escopo.

**P20. Como descobrir/exibir o número real de um device GOWA de forma confiável.** `[02·q8]`
- **✅ DECIDIDO (2026-06-19):** **capturar o número após o login** e salvar em `channels` (opção a);
  aceitar vazio até o 1º login.
- *Opções:* (a) `own_phone` cacheado em `channels` atualizado pós-login; (b) consultar a cada status.
- *Recomendação:* **(a)** persistir `own_phone` no login; aceitar vazio até o 1º login.

**P21. Forma exata do contrato de export de provider e do lifecycle.** `[02·q9]`
- **✅ DECIDIDO (2026-06-19):** contrato **só declarativo** via `entry.channels`/`entry.lifecycle`
  (opção a); sem registro imperativo no MVP.
- *Opções:* (a) declarativo via `entry.channels`/`entry.lifecycle`; (b) também registro imperativo
  (`register(registry)`).
- *Recomendação:* **(a) declarativo**, consistente com o resto do sistema de plugins; sem registro
  imperativo no MVP.

**P22. Disable de plugin sem matar o processo todo (teardown vs `os._exit`).** `[02·q10]`
- **✅ DECIDIDO (2026-06-19):** **teardown aguardado antes do `os._exit` (opção a)**. Hot-unload fica
  para o futuro. *(= P25.)*
- *Opções:* (a) manter `os._exit` mas rodar `teardown` aguardado antes; (b) hot-unload em runtime.
- *Recomendação:* **(a)** no MVP (teardown aguardado + die-with-parent); hot-unload como evolução
  futura. *(Cruza com P77.)*

**P23. Bootstrap do GOWA-plugin em instalações existentes.** `[02·q11]`
- **✅ DECIDIDO (2026-06-19):** **bootstrap especial no upgrade (opção a)** garante `gowa` presente +
  `enabled=1`, preservando a sessão.
- *Contexto:* `bootstrap_initial_plugins` só copia em pasta vazia; instalação atual não receberia o
  plugin `gowa` e poderia desconectar o número.
- *Opções:* (a) bootstrap especial no upgrade que garante `gowa` presente + `enabled=1`; (b) import
  manual via `.zip`.
- *Recomendação:* **(a)** — na migration da Fase 3, se existir canal default GOWA, copiar o plugin e
  ativá-lo preservando a sessão.

**P24. API do core para o provider ler/gravar tabelas de canal.** `[02·q12]`
- **✅ DECIDIDO (2026-06-19):** provider lê/grava via **`ctx.channel_registry` (opção a)**.
- *Opções:* (a) métodos no `ChannelRegistry` passados via `ctx`; (b) objeto de serviço dedicado.
- *Recomendação:* **(a)** — expor no `ChannelRegistry` via `ctx.channel_registry`; centraliza
  cifragem/mascaramento.

**P25. Disable de plugin: restart-do-processo vs hot-unload em runtime?** `[09·1]`
- **✅ DECIDIDO (2026-06-19):** **restart-do-processo no MVP (opção A)**. Mesma decisão do P22.
- *Opções:* (A) manter restart-do-processo (teardown antes do `os._exit`) — simples, mas derruba
  todos os canais por segundos; (B) hot-unload por plugin — sem downtime, mas exige `unregister(owner)`
  em todos os registries.
- *Recomendação:* **(A) no MVP**; (B) como evolução quando o downtime de toggle incomodar.
  *(Mesma decisão que P22 — responder uma vez.)*

**P26. Onde mora o supervisor e o serviço de subprocesso: `server/` ou novo pacote `runtime/`?** `[09·2]`
- **✅ DECIDIDO (2026-06-19):** **novo pacote `runtime/` (opção B)**. Atualizar a árvore no CLAUDE.md.
- *Opções:* (A) `server/supervisor.py` + `server/subprocess_service.py` (perto do lifespan); (B)
  pacote `runtime/` dedicado.
- *Recomendação:* **(B) `runtime/`** — deixa explícito que são capacidades fundacionais; atualizar a
  árvore no CLAUDE.md.

**P27. `stop_event` por-task vs `state.stop_event` global?** `[09·3]`
- **✅ DECIDIDO (2026-06-19):** supervisor usa **`task.cancel()` nativo (opção A)**;
  `state.stop_event` global mantido só por compat na transição.
- *Opções:* (A) supervisor usa `task.cancel()` e mantém `state.stop_event` só p/ compat; (B) cada
  task com seu `stop_event`.
- *Recomendação:* **(A)** — `cancel()` é idiomático; migrar as 4 corrotinas legadas gradualmente.

**P28. Eventos do bus para o supervisor (`task.crashed`, etc.)?** `[09·4]`
- **✅ DECIDIDO (2026-06-19):** **emitir eventos no bus** (`task.crashed`,
  `subprocess.crashed/restarted`) — opção B, só na transição.
- *Opções:* (A) não emitir no bus (só log+WS); (B) adicionar `task.crashed`/`subprocess.crashed`/
  `subprocess.restarted` a `KNOWN_EVENTS`.
- *Recomendação:* **(B)** — barato e alinhado ao padrão observador; emitir só na transição (não no
  crash-loop).

**P29. Die-with-parent no Windows: Job Object via `ctypes` ou `pywin32`?** `[09·5]`
- **✅ DECIDIDO (2026-06-19) ⚠️ MUDANÇA:** **só Linux/Docker por enquanto** — Windows fora do escopo
  do Pro. Die-with-parent via **`PR_SET_PDEATHSIG` (Linux)**. Job Object do Windows **adiado**
  (implementar só se voltar a empacotar EXE). Stale-kill no boot continua valendo.
- *Opções:* (A) `ctypes` direto na Win32 API (sem dep); (B) `pywin32` (API limpa, +dep no bundle).
- *Recomendação:* **(A) `ctypes`** com fallback para stale-kill + `taskkill /T`; reavaliar (B) se o
  `ctypes` provar frágil no PyInstaller. Stale-kill no boot já cobre o pior caso.

**P30. Persistir health de tasks/subprocessos em tabelas core já no MVP?** `[09·6]`
- **✅ DECIDIDO (2026-06-19):** **só memória no MVP (opção A)**.
- *Opções:* (A) só memória; (B) tabelas `runtime_tasks`/`runtime_subprocesses` + migration.
- *Recomendação:* **(A) só memória no MVP**; tabelas só quando o plano 07 pedir histórico.

**P31. Timeout de teardown por plugin no shutdown/disable?** `[09·7]`
- **✅ DECIDIDO (2026-06-19):** **timeout fixo (~10s) e seguir (opção A)**.
- *Opções:* (A) timeout fixo (~10s) e seguir; (B) configurável por plugin no manifest.
- *Recomendação:* **(A) timeout fixo** com log de aviso e `os._exit` como rede final.

### Tema C — RBAC, usuários, sessões (plano 03)

**P32. GESTOR atende conversas?** `[03·1]`
- **✅ DECIDIDO (2026-06-19):** GESTOR **atende** (opção a); quem não atende é só não receber
  membership de inbox.
- *Opções:* (a) gestor tem `conversation.*`; (b) gestor só administrativo.
- *Recomendação:* **(a)** — gestor que não atende é só não receber membership de inbox.

**P33. GESTOR gerencia usuários?** `[03·2]`
- **✅ DECIDIDO (2026-06-19):** `users.manage` **exclusivo do admin (opção a)**.
- *Opções:* (a) `users.manage` exclusivo do admin; (b) gestor também cria atendentes.
- *Recomendação:* **(a) exclusivo do admin** (menor superfície de risco).

**P34. Migração da senha única (forçar bootstrap vs manter modo legado).** `[03·3]`
- **✅ DECIDIDO (2026-06-19):** update **força criar 1º admin (opção a)** + env headless para Docker.
- *Opções:* (a) update força criar 1º admin; (b) manter modo "sem senha" até ativar RBAC; (c)
  aceitar a senha única antiga uma vez e migrar.
- *Recomendação:* **(a) forçar bootstrap** + env headless `WHATSBOT_ADMIN_EMAIL/PASSWORD` para
  Docker.

**P35. Transporte da sessão: cookie HttpOnly vs Bearer header.** `[03·4]`
- **✅ DECIDIDO (2026-06-19):** **Bearer token opaco no MVP (opção a)**; cookie HttpOnly depois.
  Catálogo de permissões já é granular (ver nota P35 abaixo).
- *Opções:* (a) manter Bearer no MVP; (b) migrar já para cookie HttpOnly (imune a XSS, exige CSRF).
- *Recomendação:* **(a) Bearer no MVP** com o token opaco de `user_sessions`; cookie HttpOnly numa
  2ª iteração (só muda o transporte).

**P36. Quick replies: globais ou por inbox/usuário?** `[03·5]` *(cruza com P53)*
- **✅ DECIDIDO (2026-06-19):** **globais, atendente edita (opção a)**. *(Ripple P42: sem escopo no
  MVP — só lista global. = P43.)*
- *Opções:* (a) globais, atendente edita; (b) globais, só admin/gestor; (c) por inbox/usuário.
- *Recomendação:* aguardar plano 04; default provisório **(a)** (`quickreply.manage` no atendente).

**P37. Recuperação de senha / SMTP disponível?** `[03·6]`
- **✅ DECIDIDO (2026-06-19):** **admin reseta** numa tela simples (opção a); SMTP no futuro.
- *Opções:* (a) admin reseta senha (temporária); (b) SMTP para link de reset.
- *Recomendação:* **(a)** no MVP; SMTP é fase futura.

**P38. `inbox_members` — quem cria a tabela?** `[03·7]`
- **✅ DECIDIDO (2026-06-19):** **bloquear scoping até o plano 01 (opção b)**; se 03 vier antes, stub
  + FK depois.
- *Opções:* (a) 03 cria stub mínimo agora; (b) bloquear scoping até 01.
- *Recomendação:* **(b)** — não duplicar schema; entregar RBAC por papel e ligar o scoping com 01.
  Se 03 vier antes do 01, criar o stub e migrar a FK depois.

**P39. Política de sessão (duração, lembrar-me, sessões simultâneas).** `[03·8]`
- **✅ DECIDIDO (2026-06-19):** sessão **expira em 14 dias, mas editável** (config no banco/tela). Sem
  refresh, sem limite simultâneo no MVP; "logout-all" disponível.
- *Opções:* curta (1 dia)+refresh; longa (14-30 dias); limite N sessões; "logout em todos".
- *Recomendação:* **expires = 14 dias**, sem refresh no MVP, sem limite simultâneo; "logout-all" via
  `session_repo.delete_for_user`.

**P40. Multi-papel por usuário (`user_roles` N:N) já no MVP?** `[03·9]`
- **✅ DECIDIDO (2026-06-19):** **1 papel por usuário no MVP** (UI single). Schema pode permanecer N:N
  (custo zero) para expandir depois.
- *Opções:* (a) N:N desde já (schema já é N:N); (b) 1 papel por usuário.
- *Recomendação:* **(a) manter N:N** (custo zero); UI do MVP pode expor seleção única.

### Tema D — Respostas rápidas (plano 04)

**P41. Colisão de `short_code` entre escopos — de-dup ou mostrar todos?** `[04·1]`
- **✅ DECIDIDO (2026-06-19):** **bloquear `short_code` duplicado** — unicidade **global** enforced
  (sem escopos, ver P42).
- *Opções:* (a) de-dup por precedência user>inbox>global; (b) mostrar todos rotulados.
- *Recomendação:* híbrido — quando houver homônimos, manter ambos com badge de escopo, o mais
  específico primeiro.

**P42. Índice único por escopo — parcial vs coluna gerada.** `[04·2]`
- **✅ DECIDIDO (2026-06-19) ⚠️ MUDANÇA:** **quick replies SEM escopo no MVP** — lista **global
  única**, resolvida por um `WHERE` simples. Sem colunas `scope`/`inbox_id`/`user_id`. `short_code`
  com **UNIQUE global** (sem índice parcial). Escopo por inbox/usuário fica para o futuro.
- *Opções:* (a) 3 índices parciais com `sqlite_where`/`postgresql_where`; (b) coluna gerada +
  UNIQUE com COALESCE.
- *Recomendação:* **(a) índices parciais** na migration; declarar só o não-único no `tables.py`.

**P43. Política RBAC — quem cria o quê (quick replies).** `[04·3]` *(cruza com P36)*
- **✅ DECIDIDO (2026-06-19):** **atendente também cria/edita** (a lista é global). Gate por
  `quickreply.manage`. *(Ripple P42: sem escopo a moderar.)*
- *Opções:* (a) admin/gestor criam global+inbox+user-de-qualquer-um; atendente só os próprios
  `user`; (b) atendente também sugere globais (com moderação).
- *Recomendação:* **(a)**, alinhado ao `quickreply.manage`; sem moderação no MVP.

**P44. Carregamento da lista no composer — refetch vs cache.** `[04·4]`
- **✅ DECIDIDO (2026-06-19):** **cache no client + evento** `whatsbot:quick-replies-changed` (opção
  b); refresh por foco se multi-aba incomodar.
- *Opções:* (a) refetch por abertura de conversa; (b) cache no client invalidado por evento.
- *Recomendação:* **(b) cache + evento** `whatsbot:quick-replies-changed`; somar refresh por foco se
  multi-aba virar problema.

**P45. Conflito do gatilho `/` com mensagens que começam com barra.** `[04·5]`
- **✅ DECIDIDO (2026-06-19):** validação de `short_code` no **front-end** (minúsculas, sem
  espaços/acentos, não começar com `/`, mostrando o erro); menu abre só com match (Chatwoot/Slack).
- *Opções:* (a) abrir o menu só com match E candidatos, `Escape` fecha; (b) gatilho duplo `//`.
- *Recomendação:* **(a)** — comportamento do Chatwoot/Slack, igual ao `@menção` atual.

**P46. Suporte a mídia/anexos em atalhos.** `[04·6]`
- **✅ DECIDIDO (2026-06-19):** **só texto** no começo; evoluir para mídia depois. (Reservar colunas
  `media_*` nullable na 1ª migration é opcional.)
- *Opções:* (a) só texto nas Fases 1-5; (b) modelar `media_path`/`media_type` nullable já agora.
- *Recomendação:* texto primeiro; **considerar reservar `media_type`/`media_path` nullable** na
  migration da Fase 1 (custo baixo) para evitar nova migration. Decidir se vale poluir o schema.

**P47. `{{agent.*}}`/`{{inbox.*}}` antes dos planos 02/03.** `[04·7]`
- **✅ DECIDIDO (2026-06-19) ⚠️ MUDANÇA:** **MVP sem variáveis** — respostas rápidas são **texto
  puro**, sem `{{...}}`. Variáveis ficam para fase futura. (Simplifica: sem parser, sem preview.)
- *Opções:* (a) resolver para string vazia; (b) esconder do catálogo/preview.
- *Recomendação:* **(a)+(b)** — string vazia na expansão e ocultar do preview enquanto a fonte não
  existir.

**P48. Tela de gestão na navegação — atendente vê?** `[04·8]`
- **✅ DECIDIDO (2026-06-19):** **esconder** opções sem permissão (não mostrá-las travadas). Uma só
  tela, gateada por `quickreply.manage`. *(Ripple P42: sem opções de escopo a gatear.)*
- *Opções:* (a) atendente vê a tela com escopo travado em `user`; (b) tela só para
  `quickreply.manage`.
- *Recomendação:* **(a)** — uma só tela, opções de escopo gateadas.

### Tema E — Atributos personalizados (plano 05)

**P49. Exclusão de definição e valores órfãos.** `[05·1]`
- **✅ DECIDIDO (2026-06-19):** **soft-delete (opção c)** + ação opcional de limpar órfãos.
- *Opções:* (a) deixar órfão no JSON; (b) limpar em batch (caro); (c) soft-delete (`deleted_at`).
- *Recomendação:* **(c) soft-delete** no MVP (esconde da UI, valor permanece) + ação opcional
  "limpar valores órfãos".

**P50. Keys desconhecidas no PUT de valores.** `[05·2]`
- **✅ DECIDIDO (2026-06-19):** **erro 400 (opção a)**.
- *Opções:* (a) erro 400; (b) ignorar; (c) aceitar e gravar livre.
- *Recomendação:* **(a) erro 400** — garante coerência key↔definição.

**P51. Escopo de unicidade da key (`UNIQUE(attribute_key, applies_to)`).** `[05·3]`
- **✅ DECIDIDO (2026-06-19):** **permitir a mesma key** em contact e conversation (opção a),
  expondo o escopo na UI.
- *Opções:* (a) permitir a mesma key em contact e conversation; (b) key globalmente única.
- *Recomendação:* **(a) permitir** (alinhado ao Chatwoot), expondo o escopo na UI.

**P52. `number`: inteiro vs decimal/moeda.** `[05·4]`
- **✅ DECIDIDO (2026-06-19):** `number` **cru no MVP (opção a)**; currency/percent depois.
- *Opções:* (a) só `number` cru; (b) já incluir currency/percent.
- *Recomendação:* **(a) `number` cru** no MVP; currency/percent como formatação futura.

**P53. Atributos graváveis pela IA.** `[05·5]`
- **✅ DECIDIDO (2026-06-19):** IA grava **todos** os atributos no MVP (opção a); flag
  `writable_by_ai` planejada.
- *Opções:* (a) IA grava todos; (b) flag `writable_by_ai` por definição.
- *Recomendação:* **(a) todos no MVP**, com `writable_by_ai` planejado para a fase RBAC, default
  true.

**P54. Migrar campos fixos (`profession`, `company`, `address`) para atributos custom?** `[05·6]`
- **✅ DECIDIDO (2026-06-19):** **conviver (opção a)** — campos fixos continuam colunas; custom são
  aditivos. **Precisamos de atributos tanto de CONTATO quanto de CONVERSA** (igual Chatwoot).
- *Opções:* (a) conviver com os dois; (b) migrar os fixos para o JSON (quebra histórico/prompt).
- *Recomendação:* **(a) conviver** — campos fixos continuam colunas; custom são aditivos.

**P55. Performance de filtro em SQLite (sem GIN).** `[05·7]` *(cruza com P74)*
- **✅ DECIDIDO (2026-06-19):** índice de expressão **só para campos `filterable` (opção a)**,
  decidido junto com o plano 08. *(Decisão global de banco: no Postgres liberamos JSONB + índice GIN,
  bem melhor; no SQLite fica o índice de expressão.)*
- *Opções:* (a) índice de expressão só para campos `filterable`; (b) sem índice (scan).
- *Recomendação:* **(a) índice por campo `filterable`** (decidido junto com o plano 08), limitando o
  número de atributos filtráveis.

**P56. `created_at` como epoch Float vs ISO Text.** `[05·8]`
- **✅ DECIDIDO (2026-06-19):** **epoch Float** (consistência com o projeto).
- *Recomendação:* **epoch `Float`** para consistência com o resto do projeto. Confirmar se há
  preferência por ISO para legibilidade em queries manuais.

### Tema F — Motor multi-agente / code-in-DB (plano 06)

**P57. Workers do uvicorn em produção (Coolify).** `[06·1]`
- **✅ DECIDIDO (2026-06-19):** **1 worker no MVP** (invalidação por evento + cache curto bastam).
  Reavaliar mecanismo de sincronização se a carga exigir multi-worker.
- *Opções:* (a) 1 worker (invalidação por evento basta); (b) N workers (exige TTL/`LISTEN/NOTIFY`).
- *Recomendação:* **1 worker no dia-1** com invalidação por evento + TTL 60s; reavaliar se a carga
  exigir.

**P58. Sessão: Agno `db` vs montar histórico de `messages`.** `[06·2]`
- **✅ DECIDIDO (2026-06-19):** histórico **montado das `messages` (opção b)**, uma fonte de verdade.
- *Opções:* (a) Agno `db` (duplica histórico); (b) montar das `messages` (uma fonte de verdade).
- *Recomendação:* **(b)** no início; reavaliar (a) só se quiser a memória autônoma do Agno.

**P59. `ai_variables` dedicada vs prefixo em `config`.** `[06·3]`
- **✅ DECIDIDO (2026-06-19):** **tabela dedicada (opção a)**.
- *Opções:* (a) tabela dedicada (tem `category`); (b) prefixo `ai.var.<name>` em `config`.
- *Recomendação:* **(a) tabela dedicada** (barata de reverter; melhor p/ UI).

**P60. Granularidade agente↔inbox.** `[06·4]`
- **✅ DECIDIDO (2026-06-19):** **coluna `default_agent_key` (opção a)**; handoff cobre multi-agente
  na conversa.
- *Opções:* (a) coluna `default_agent_key` (simples); (b) tabela de junção `ai_inbox_agents` (vários
  agentes/router por inbox).
- *Recomendação:* **(a) coluna** no MVP; o handoff já cobre multi-agente dentro da conversa.

**P61. Precedência em colisão de nome de tool.** `[06·5]`
- **✅ DECIDIDO (2026-06-19):** **código > plugin > banco (opção a)**, com warning + badge.
- *Opções:* (a) código > plugin > banco; (b) banco ganha (override total).
- *Recomendação:* **(a) código > plugin > banco**, com warning logado e badge de colisão na UI.

**P62. Nível de isolamento do runner de code-in-DB no dia-1.** `[06·6]`
- **✅ DECIDIDO (2026-06-19):** **subprocess + RLIMIT + timeout (opção a)** no dia-1. Thiago vai
  testar no Docker/Linux. *(Em aberto: o container Coolify roda com privilégios para seccomp? —
  confirmar nos testes.)*
- *Opções:* (a) subprocess + `RLIMIT_*` + timeout; (b) + seccomp/AppArmor; (c) microVM/Firecracker.
- *Recomendação:* **(a)** no dia-1. **Preciso saber:** o container Coolify/Docker roda com
  privilégios para aplicar seccomp/AppArmor (para subir à (b) sem reprojeto)?

**P63. Gate da IA criando tools.** `[06·7]`
- **✅ DECIDIDO (2026-06-19):** **gate humano (opção a)** — nasce `pending` até ADM aprovar.
- *Opções:* (a) sempre nasce `enabled=0`/`pending` até ADM aprovar; (b) modo que ativa tools da IA
  direto.
- *Recomendação:* **(a) gate humano** por padrão; (b) como flag explícita e auditada se o cliente
  insistir.

**P64. Structured output para o split de mensagens.** `[06·8]`
- **✅ DECIDIDO (2026-06-19):** **structured output via Pydantic `output_schema` do Agno (opção a)** —
  é o que o gerenciamento-ia faz (`LLMResponse{ mensagens_para_usuario, private_message }`, com
  `silent_output` controlado por código). **Multi-provider confirmado** via OpenRouter
  (`provider/modelo`, auto-detecção de prefixo, tuning por agente: `temperature_<agente>` > global).
- *Opções:* (a) migrar para `output_schema` Pydantic do Agno (robusto, muda o prompt); (b) manter o
  parse JSON atual.
- *Recomendação:* **(a)** como refino na Fase 6 (não bloqueante); validar que não quebra prompts
  existentes.

**P65. Tempo de coexistência legacy × Agno.** `[06·9]`
- **✅ DECIDIDO (2026-06-19) ⚠️ MUDANÇA:** **ir direto para o Agno desde o início**, sem período
  longo de coexistência. Construir o motor sobre o Agno desde a 1ª fase; manter o legado só como
  fallback mínimo/curtíssimo se algo não tiver paridade. Reduz código duplicado.
- *Opções:* (a) curto (1-2 semanas em prod, depois remove); (b) longo (meses como fallback).
- *Recomendação:* **(a)** — coexistir só até a paridade estar comprovada; adaptador fino se a
  migração de algum recurso atrasar.

**P66. Allowlist de dependências.** `[06·10]`
- **✅ DECIDIDO (2026-06-19):** **não bloquear dependências no MVP (sem allowlist)** para facilitar.
  ⚠️ Aumenta a superfície de risco do code-in-DB — revisitar no endurecimento.
- *Opções:* (a) allowlist fixa; (b) qualquer dep com aprovação ADM por item; (c) deps pré-congeladas
  num venv de build (runtime sem `pip install`).
- *Recomendação:* **(a) allowlist fixa** + aprovação ADM por exceção no dia-1; mirar **(c)** no
  médio prazo. `--require-hashes`/lockfile onde viável.

**P67. Reaproveitar o serviço de subprocesso do plano 02/09 para o tool_runner?** `[06·11]`
- **⏸️ ADIADO:** decidir depois (reusar serviço de subprocesso 02/09 para o tool_runner).
- *Opções:* (a) runner reusa o serviço gerenciado; (b) runner próprio independente.
- *Recomendação:* **(a)** se 02/09 entregarem primeiro e a API servir; senão **(b)** mínimo,
  refatorando depois.

### Tema G — Auditoria e LGPD (plano 07)

> ⏸️ **Tema de auditoria ADIADO por escolha do Thiago** — será a última coisa a ser feita (se for
> feita). P68–P75 ficam em aberto; retomar quando a auditoria entrar no escopo.

**P68. JSONB vs TEXT para `before_json`/`after_json`.** `[07·1]`
- **⏸️ ADIADO** (auditoria).
- *Opções:* (a) TEXT puro (portável); (b) tipo Core que vira JSONB no Postgres (query interna).
- *Recomendação:* **(a) TEXT no MVP**; JSONB só se aparecer necessidade de filtrar por campo do
  diff.

**P69. Propagação do ator ao handler de bus (fora da request).** `[07·2]`
- **⏸️ ADIADO** (auditoria).
- *Opções:* (a) handler lê `get_current_actor()` por-thread; (b) capturar o ator no `emit` e anexar
  ao payload; (c) instrumentar só por dependency as ações com ator humano, bus só `system`/`ai`.
- *Recomendação:* **(c)+(a)** — rotas críticas usam dependency (ator confiável); bus cobre o resto
  como `system`/`ai`.

**P70. `actor_type = ai`: como atribuir.** `[07·3]`
- **⏸️ ADIADO** (auditoria).
- *Opções:* (a) `ai`, `actor_user_id=NULL`, `actor_label="agente <nome>"`; (b) `ai` com
  `actor_user_id`=dono da inbox.
- *Recomendação:* **(a)** — `ai` é tipo de ator distinto; rastrear o agente no `actor_label`.

**P71. Volume: auditar `message.sent`/`message.received`?** `[07·4]`
- **⏸️ ADIADO** (auditoria).
- *Opções:* (a) fora da auditoria; (b) só `message.sent source=operator` (envio manual conta como
  ação humana).
- *Recomendação:* **(a) por padrão**, com (b) futuro se compliance exigir.

**P72. LGPD / direito à eliminação do titular.** `[07·5]`
- **⏸️ ADIADO** (auditoria).
- *Opções:* (a) preservar a trilha como base legal; (b) pseudonimizar a PII mantendo a ação; (c)
  excluir as linhas do titular.
- *Recomendação:* **(b) pseudonimizar** (Fase 3). **Validar com jurídico** antes de fixar.

**P73. Auditar o acesso à própria auditoria + evitar duplicidade rota×bus.** `[07·6]`
- **⏸️ ADIADO** (auditoria).
- *Opções:* (i) auditar `audit.read`/`data.export` ou não; (ii) regra rota-com-bus vs rota-sem-bus.
- *Recomendação:* (i) auditar **apenas exports** (`data.export`); (ii) invariante: "rota com evento
  de bus → cobre por bus; rota sem evento → dependency".

**P74. Imutabilidade no SQLite.** `[07·7]`
- **⏸️ ADIADO** (auditoria). *(Nota da decisão global de banco: append-only forte via trigger/role do
  Postgres passa a ser caminho aceitável de exigir — auditoria é feature Pro.)*
- *Opções:* (a) aceitar que append-only forte só existe no Postgres (disciplina de app no SQLite);
  (b) trigger SQLite `BEFORE UPDATE/DELETE` com exceção p/ purge.
- *Recomendação:* **(a)** — append-only forte é garantia de Postgres (deployment Pro recomendado);
  no SQLite fica a disciplina de app. Documentar.

**P75. Numeração da migration de auditoria vs plano 03.** `[07·8]` *(cruza com P83)*
- **⏸️ ADIADO** (auditoria). *(Quando entrar, segue o encadeamento linear decidido em P82.)*
- *Opções:* (a) numerar `0008` assumindo 03 antes; (b) dependência explícita no `down_revision`.
- *Recomendação:* **(a)** com `down_revision` apontando para a head do 03; resolver no merge.

### Tema H — Filtros e views salvas (plano 08)

**P76. Endpoint canônico: `/api/conversations` ou estender `/api/contacts`?** `[08·1]`
- **✅ DECIDIDO (2026-06-19):** filtros canônicos em **`/api/conversations` (opção a)**;
  `/api/contacts` só `q`/`archived` legado.
- *Opções:* (a) filtros só em `/api/conversations`; (b) duplicar nos dois na transição; (c) só
  `/api/contacts` até a UI migrar.
- *Recomendação:* **(a)** — filtros no caminho de conversas (unidade filtrável final);
  `/api/contacts` segue só com `q`/`archived` para o legado.

**P77. Tags são por contato ou por conversa?** `[08·2]` *(cruza com 01)*
- **✅ DECIDIDO (2026-06-19):** **reusar a tag do contato** para a conversa (opção a);
  `conversation_tags` só se precisar no futuro.
- *Opções:* (a) reusar `contact_tags` (tag do contato dono); (b) criar `conversation_tags` (labels
  por conversa, fiel ao Chatwoot).
- *Recomendação:* **(a)** no MVP; `conversation_tags` só se o produto pedir labels por conversa.

**P78. OR aninhado (grupos) é necessário?** `[08·3]`
- **✅ DECIDIDO (2026-06-19):** AND/OR **plano no MVP (opção a)**; aninhado só com demanda.
- *Opções:* (a) AND/OR plano; (b) grupos aninhados desde já.
- *Recomendação:* **(a) plano** no MVP/Fase 2; aninhado na Fase 4 só com demanda.

**P79. Escopo das views globais (`saved_filters.scope`).** `[08·4]`
- **✅ DECIDIDO (2026-06-19):** escopo **`user`/`global` (opção a)**; `team`/`inbox` quando o 03
  entregar teams.
- *Opções:* (a) só `user`/`global`; (b) incluir `team`/`inbox` desde já.
- *Recomendação:* **(a)** no MVP; `team`/`inbox` quando o 03 entregar teams.

**P80. Tamanho de página e scroll infinito.** `[08·5]`
- **✅ DECIDIDO (2026-06-19):** **página 30 + scroll infinito (opção a)**, cursor opaco, teto ~100.
- *Opções:* página 30/50/100; scroll infinito vs "carregar mais".
- *Recomendação:* **página 30 + scroll infinito** (cursor opaco); `limit` ajustável com teto (~100).

**P81. `archived`: filtro ou toggle dedicado?** `[08·6]` *(cruza com P10)*
- **✅ DECIDIDO (2026-06-19):** **manter toggle dedicado (opção a)** + expor `archived` como dimensão
  no filter-schema. *(Comentário sobre ordenação da lista — ver nota P81 abaixo, a confirmar.)*
- *Opções:* (a) manter toggle dedicado (compat `showArchived`); (b) virar `status=archived`/
  `resolved`.
- *Recomendação:* **manter o toggle dedicado** no MVP + expor `archived` como dimensão no
  `filter-schema`; reconciliar com o status quando o 01 definir.

**P82. Encadeamento de revisões Alembic.** `[08·7]` *(cruza com P75)*
- **✅ DECIDIDO (2026-06-19):** encadeamento **linear (opção a)** — cada migration aponta para o head
  real no momento de implementar. Sem branches.
- *Opções:* (a) encadear linear depois da última migration existente; (b) branch + merge revision.
- *Recomendação:* **(a) linear** — setar `down_revision` para o head real no momento da
  implementação. Evitar branches Alembic.

**P83. Filtros expostos ao agente LLM como tool?** `[08·8]`
- **✅ DECIDIDO (2026-06-19):** **fora de escopo agora (opção a)**; ideia registrada.
- *Opções:* (a) fora de escopo agora; (b) expor tool core/plugin lendo `conversation_repo.list`.
- *Recomendação:* **(a) fora de escopo**; registrar como ideia (a infra fica reutilizável).

---

### Perguntas que se repetem entre planos (responder uma vez)

Estas decisões aparecem em mais de um plano e devem ser respondidas de forma única:

- **Senha única → bootstrap de admin:** P34 (origem) impacta a migração de todas as instalações.
- **Disable de plugin: restart vs hot-unload:** P22 e P25 são a mesma decisão.
- **Numeração/encadeamento Alembic:** P75 e P82 (resolver no merge, linear).
- **Índices de custom attrs `filterable`:** P55 e a Fase 6 do 05 / Fase do 08 (decidir junto).
- **Quick replies — política RBAC:** P36 e P43.
- **`archived` como status ou flag:** P10 e P81.
- **Serviço de subprocesso compartilhado** entre GOWA-plugin (02) e tool_runner (06): P67 +
  P26/P29.
- **Tags por contato vs conversa:** P77 cruza com o modelo do 01.

---

## 6. Resumo executivo da execução

1. **Onda 0** (09 Fases 1-3) — fundação de runtime, em paralelo com a Onda 1. **Só Linux/Docker.**
2. **Onda 1** (02 Fase 0 + 03 Fases 1-3) — contrato de canal via registry + usuários/sessões.
3. **Onda 2** (01 completo + 03 Fase 4) — domínio de conversas + RBAC efetivo (cascata de IA sem
   nível de contato). **Marco-pivô.**
4. **Onda 3** (04 texto/global/sem-variáveis + 05 contato) — operação do atendente. **Auditoria (07)
   adiada.**
5. **Onda 4** (02 Fases 1-3 + 09 Fases 4-6) — Cloud API + GOWA-plugin + multi-número. **Sem cifragem
   no MVP.**
6. **Onda 5** (06 completo — Agno-first + 05 conversa) — motor multi-agente code-in-DB.
7. **Onda 6** (08 + 05 índices) — filtros/views (JSONB+GIN no Postgres) + aposentar legado.
   **Auditoria/LGPD continua adiada.**

**Tripé fundacional:** 09 (runtime) → 02/03 (canal+identidade) → 01 (conversas). Sem ele, nada se
ancora. Os planos de operação (04/05/08) e IA (06) penduram no 01. A auditoria (07) está fora do
escopo do MVP por decisão do Thiago.
