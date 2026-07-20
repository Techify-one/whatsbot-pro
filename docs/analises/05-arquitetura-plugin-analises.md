# WhatsBot — Arquitetura proposta do plugin `analises` (reuso do motor melhorias)

> **Proposta / a discutir — o escopo atual é SÓ documentação; nada implementado.**
> Este documento descreve *como* construir o plugin `analises` reusando o motor do
> plugin `melhorias` (plano 51). Nenhuma tabela, rota ou executor foi criado. As
> decisões abaixo são recomendações para o Thiago validar antes de qualquer código.

---

## 0. Resumo executivo

O plugin `analises` é uma **IA analista interna**: um agente conversacional que lê
**todas** as tabelas do WhatsBot (SELECT em `contacts`/`messages`/`atendimentos`/
`usage`/`plugin_protocolos_*`/…) e escreve **só nas próprias** tabelas
(`plugin_analises_*`). Ele responde perguntas do gestor ("quantos atendimentos a IA
fechou hoje?", "qual atendente teve mais protocolos abertos?", "que padrão de resposta
converte melhor?") e produz relatórios.

A recomendação central é **clonar a substância de transporte + chat agêntico do
`melhorias`** (cliente HMAC, guard HMAC, chat CRUD + streaming SSE→WS, frontend de chat)
e **substituir só a superfície de dados**: onde o `melhorias` expõe rotas de
*escrita versionada* de agentes/tools/prompts, o `analises` expõe rotas de **leitura
read-all** (todas as tabelas) + **escrita confinada** a `plugin_analises_*`. Isso casa
1:1 com a decisão do Thiago ("lê tudo, escreve só as próprias") e **não coloca
credenciais de banco no executor externo** — o que é a propriedade de segurança que
torna "IA externa com acesso ao banco" aceitável.

---

## 1. Decisão do Thiago (o contrato)

Do CANON (decisões canônicas) e do brief:

- **Plugin agêntico INTERNO** (`analises`): parte do produto, ativado pela UI de plugins.
- **Lê TODAS as tabelas** (SELECT): dicionário completo do banco disponível ao agente.
- **Escreve SÓ nas próprias** tabelas (`plugin_analises_*`) — inicialmente. Expansível
  depois, mas por ora **só análises**.
- **Escrita de volta no core (se algum dia) via API REST do WhatsBot, NUNCA SQL cru** —
  os repos aplicam `display_id`/índice único/`conversation_event`/broadcasts/RBAC; SQL
  cru corromperia estado. Isso vale como regra dura para a Fase 3 (write-back de tags de
  conversão etc.).
- Relatórios externos (Telegram) são **contexto**, fora de escopo implementar aqui.

Consequência arquitetural direta: a fronteira "lê tudo / escreve só as próprias" precisa
ser **enforçada por construção** — não pode depender do agente "se comportar". Os dois
modelos de cérebro abaixo diferem exatamente em *onde* essa fronteira é imposta.

---

## 2. Veredito de reuso do `melhorias` (o que carrega vs. o que troca)

O plugin `melhorias` separa limpo uma **substância genérica de transporte + chat
agêntico** de um **payload de domínio (melhoria)**. O plano 51 já foi desenhado como um
padrão de **duas apps** no executor (uma app = tool-registry + system-prompt + guides;
HMAC/runner/SSE/relogin compartilhados) — o `analises` é uma segunda app desse padrão
(mapa `melhorias-engine.md §5`).

### 2.1 Carrega quase VERBATIM (renomear `PLUGIN_ID` + namespace de config + prefixo de tabela)

| Arquivo do `melhorias` | O que é | Acoplamento a trocar |
|---|---|---|
| `ai_client.py` | Cliente HMAC — assina `METHOD\npath\nts\nrequestId\nbody`, resolve config DB>env>default, todas as chamadas de ciclo de vida (`start`/`send`/`approve`/`cancel`/`resume`/`relogin_*`/`auth_check`/`open_stream`) e consumo do SSE (`ai_client.py:101-106` assinatura, `:114-125` headers, `:145-216` lifecycle) | Só o namespace `plugin.melhorias.*` (`PLUGIN_ID`, `ai_client.py:38`) → `plugin.analises.*` |
| `hmac_guard.py` | Dependency FastAPI que valida os callbacks: assinatura sobre **bytes crus** (`hmac_guard.py:83`), janela 60s (`:77`), nonce LRU anti-replay (`:39-51`), e — o primitivo de segurança — `X-WB-On-Behalf-Of` obrigatório resolvendo `user_repo.get()` e carimbando `request.state.user` (`:93-109`) | Só `_feature_active()` lê `plugin.melhorias.generator_backend` (`:54-57`) → re-apontar |
| `chat_logic.py` | CRUD de conversa/mensagem/aprovação, consumidor **SSE→WS** com backoff (`chat_logic.py:396-436`), resume-from-history (`:284-302`), resolução de imagem base64 com confinamento em `statics/` (`resolve_image_parts:312-370`), parser de frame SSE (`parse_sse_frame:375-393`), re-emissão dos 9 eventos do executor como `broadcast("plugin_melhorias_ai_event", …)` (`_ws_emit:209-217`) | Acoplamento fino: `start_conversation` chama `logic.get_suggestion`/`generation.build_initial_message` (`:229-281`) e `on_conversation_status` chama `logic.finalize_agentic_suggestion` (`:447-471`). O `suggestion_id` é só "id da entidade pai" threadado — trocar por `analysis_id` |
| `internal_routes.py:60-105` | **Write-through** genérico: `/messages`, `/approvals`, `/conversation-status` — persistência de chat agnóstica de domínio | Nenhum (é substrato de chat puro) |
| `routes.py:177-397` | Wrappers de transporte: `/conversations/*` (`:177-285`), config GET/PUT (`:344-397`), relogin/test-connection (`:290-335`) | Só rótulos que citam "melhoria" |
| `static/chat.js` + `static/chat_core.js` | Componente `AgenticChat` completo (cards de texto/tool/aprovação/erro, assinatura do `/ws` via `wsBus` filtrando por `conversation_id` — `chat.js:31-37`) + o reducer **puro** `reduceAiEvent` (`chat_core.js:11-85`, testado por `chat_core.test.js`) | Nada específico de melhoria |
| `static/ai_section.js` + `static/relogin.js` | Form de config do executor (URL/secret/modelo/callback) + proxy de relogin OAuth | Só rótulos |
| `migrations/003_ai_chat.sql` | Schema do chat agêntico: `_ai_conversations` (id TEXT uuid), `_ai_messages` (append-only), `_ai_approvals` (id TEXT = approvalId, `approved` NULL=pendente para idempotência) | Só o prefixo de tabela → `plugin_analises_ai_*` |

**Ponto-chave da re-emissão WS (decisão P2 do plano 51):** o gateway consome o SSE do
executor *server-side* e re-emite cada evento pelo **`/ws` que o operador já tem
autenticado** — o browser não fala com o executor. Herdar isso significa: um único
socket autenticado, sem SSE cross-origin, RBAC do core aplicado na borda
(`chat_logic._consume_stream` → `_ws_emit`, mapa `melhorias-engine.md §1`).

### 2.2 TROCA (específico de melhoria → específico de análise)

| Arquivo do `melhorias` | Por que é de melhoria | O que vira no `analises` |
|---|---|---|
| `generation.py` | É o **construtor de payload/leitura-de-DB** do domínio melhoria: `build_analysis_payload` (`:142-314`) reconstrói o *turno da IA* sendo criticado — cadeia de agentes de `executions.routing_steps`, tools usadas, prompt inline de cada agente renderizado com variáveis, histórico filtrado, a resposta marcada | **Dropar OU trocar por um builder de contexto de análise** (ver §6). Como o analista puxa dados *via rotas de leitura* durante o chat, provavelmente **não** precisa de um payload pré-montado — o "contexto de sistema" (docs 01–04) entra no system-prompt do executor, não num payload por-requisição |
| `logic.py` | CRUD de "sugestão", statuses (`pendente`/`em_chat`/`aprovada`/`recusada`), deep-links do painel, `conversation_event`, refresh de snapshot de contato | Entidade paralela **"análise/relatório"** (`plugin_analises_analyses`) — pedido + estado, sem o conceito de "sugestão de resposta" |
| `internal_routes.py:110-391` | A **superfície de "tools"/DB** que a IA recebe: READ de config de IA (`/agents`, `/tools`, `/variables`, `/message-trace`, gate `agent.config.manage`) + MUTATION versionada (save/rollback de agentes/prompts/tools/overrides/variáveis, cada uma reaplicando `authz.acheck` — `internal_routes.py:50-55`) | **Substituir inteiro** por: (a) rotas de **LEITURA read-all** cobrindo todas as tabelas (contacts/messages/atendimentos/usage/tags/protocolos…) — read-only, e (b) escrita **só** `plugin_analises_*` (salvar achado/relatório). Ver §3 e §5 |
| `static/panel.js` (633 linhas) | Tabela filtrável de sugestões + modal de detalhe | Dashboard/lista de análises + trigger (ver Fase 1) |
| `static/extends.js` | Item de menu-de-contexto "Gerar melhoria" + ação de seleção em lote | Trigger diferente: botão no dashboard / execução agendada |
| `events.py` | Backfill + sync de rename de contato específico de melhoria | Reescrever ou dropar |
| `routes.py:61-167` + `migrations/001`,`002` | Rotas e schema de `_suggestions`/`_suggestion_messages` | Não existem no `analises` |

**Resumo por acoplamento** (mapa `melhorias-engine.md §5`):

| Genérico (copiar, re-namespace) | Acoplado a melhoria (reescrever) |
|---|---|
| `ai_client.py`, `hmac_guard.py` | `generation.py` (builder de contexto/DB) |
| `chat_logic.py` (chat CRUD + SSE→WS + resume + imagens) | `logic.py` (entidade "sugestão") |
| `internal_routes.py:60-105` (write-through) | `internal_routes.py:110-391` (superfície de "tools"/DB da IA) |
| `routes.py:177-397` (`/conversations/*`, config, relogin) | `static/panel.js`, `static/extends.js`, `events.py` |
| `static/chat.js`, `chat_core.js`, `ai_section.js`, `relogin.js` | `migrations/001`,`002`; `routes.py:61-167` |
| `migrations/003_ai_chat.sql` | — |

---

## 3. Dois modelos de "cérebro"

A pergunta de fundo: **onde roda o loop agêntico e onde vive a fronteira read-all/write-own?**

### (A) Executor EXTERNO (padrão `melhorias`, `:8015`) — RECOMENDADO

O loop de raciocínio + tool-calling roda no executor Claude Code externo
(`whatsbot-ai-server.service` em `203.0.113.10:8015`, o mesmo que o Thiago já tem para
o `melhorias`). O executor **NÃO tem credenciais de banco**. Todo acesso a dados acontece
*dentro do gateway* (o plugin `analises`, neste repo), alcançado só por callbacks HMAC
assinados:

```
Browser (gestor, cookie/token RBAC do core)
  │  fetch + /ws (wsBus)                       ← auth = sessão/RBAC do core
  ▼
Plugin "analises" (o GATEWAY, neste repo)
  │  outbound: httpx POST/GET HMAC-assinado    (ai_client.py, clone)
  │  inbound:  /public/_internal/* rotas HMAC   (internal_routes.py, REESCRITO)
  │            ├─ LEITURA read-all  → get_engine() SELECT em qualquer tabela
  │            └─ ESCRITA confinada → só plugin_analises_*  (+ Fase 3: API core)
  ▼
Executor Claude Code :8015 (Node + Claude Agent SDK, NÃO neste repo)
     detém OAuth (~/.claude), 1 runner por conversa, ZERO creds de banco
```

- **Fronteira imposta na borda, não no agente:** o executor só consegue ler/escrever o
  que o gateway expõe como rota. "Read-all" = rotas de SELECT genéricas; "write-own" =
  só rotas que gravam em `plugin_analises_*`. Não há como o executor emitir um
  `UPDATE atendimentos` — essa rota simplesmente não existe.
- **Atua como usuário sob RBAC:** `X-WB-On-Behalf-Of` é obrigatório
  (`hmac_guard.py:93`); resolve `user_repo.get(id)` → `request.state.user`
  (`:98-109`); cada rota reaplica `authz.acheck(request, key)` (`internal_routes.py:50-55`).
  O analista age **como um gestor específico**, sujeito ao mesmo RBAC + seam ABAC do core.
  Instalação aberta (sem usuários) ⇒ default-allow, idêntico ao core.
- **Auditável:** todo callback carrega `request_id` + `on_behalf_of`
  (`hmac_guard.py:110`); as decisões passam por `authz.acheck`.

**Trade-offs:** requer o executor externo de pé (mais infra); cada consulta que o agente
pode rodar precisa ser pré-exposta como rota (ou uma rota de "query estruturada"
parametrizada — ver §4). Em troca: **fronteira de segurança real**, sem creds de banco no
processo externo, e reuso máximo do que já existe.

### (B) In-process (AGNO ou chamada Claude com tools de DB dentro do WhatsBot)

O loop agêntico roda **dentro** do processo do WhatsBot. Duas variantes:

- Reusar a infra AGNO já presente (`agent/agno_engine.py` + `ai_agents`) com tools que
  chamam `get_engine()` direto; ou
- Uma chamada direta ao cliente Claude com um conjunto de tools de DB (SELECT + escrita
  `plugin_analises_*`) definidas no próprio plugin.

Sem executor externo, sem HMAC, sem clone do transporte do `melhorias`. Mais simples de
escrever (é "só" um agente + tools locais). A fronteira read-all/write-own vira uma regra
**dentro do processo**: as tools de escrita só tocam `plugin_analises_*` porque foi assim
que foram escritas — não há isolamento de processo.

**Trade-offs:** carga pesada (raciocínio + tool-calling de análise) roda **no processo do
app**, competindo com o webhook/IA de atendimento; um bug numa tool pode escrever fora do
confinamento (a barreira é convenção de código, não arquitetura); e **o reuso do
`melhorias` não paga** — nada do transporte/chat/streaming se aproveita. Ganha simplicidade,
perde isolamento e não reaproveita o `:8015` que já existe.

### Recomendação

**Modelo (A), reusando o `:8015` que o Thiago já opera para o `melhorias`.** Motivos:

1. Casa exatamente com "lê tudo, escreve só as próprias": a fronteira é **estrutural**
   (rotas expostas), não confiança no agente.
2. **Não coloca credenciais de banco no executor externo** — o executor só fala HMAC; o
   banco nunca sai do gateway. Essa é a resposta segura para "IA externa com acesso ao
   banco".
3. Reaproveita RBAC/ABAC/auditoria do core via a ponte on-behalf-of, sem inventar um
   caminho de auth paralelo.
4. Reuso máximo do código já validado (transporte, chat, streaming, frontend, migração 003).
5. O executor já é **multi-app** por desenho do plano 51 — o `analises` é uma segunda app
   (guides + tool-registry + system-prompt próprios), HMAC/runner/SSE/relogin compartilhados.

O modelo (B) fica como alternativa "leve" caso o Thiago não queira depender do executor
externo — mas então documenta-se que o reuso do `melhorias` é abandonado e a carga vai
pro processo do app.

> Nota de dormência (herdada): um clone de (A) herda o gate
> `generator_backend == "external"` **E** `is_configured()` (URL + secret ≥32 chars).
> Enquanto fechado, **todo `_internal/*` retorna 404** (`hmac_guard.py:63-64`) — finge
> que a rota não existe. Documentar isso para o analista não ficar "misteriosamente
> escuro" antes de configurar URL+secret.

---

## 4. Segurança (a parte inegociável)

A regra dura do CANON: **NUNCA dar SQL cru de escrita no core.** O motivo não é paranoia —
os repos do core aplicam invariantes que um `INSERT`/`UPDATE` cru destruiria:
`display_id` sequencial e índice único parcial de conversa aberta (`atendimentos`,
`db/tables.py:455,468`), emissão de `conversation_event`, broadcasts WS, e RBAC. SQL cru
de escrita corrompe estado silenciosamente.

Princípios de segurança do plugin `analises`:

1. **Read-only em TUDO que é core.** As rotas de leitura (`_internal/query/*`) fazem só
   `SELECT` via `get_engine().connect()` (conexão sem transação). Nenhuma rota de leitura
   abre `begin()`. Recomendado ainda: a leitura roda sob um **role Postgres SELECT-only**
   dedicado (defesa em profundidade — mesmo um bug de rota não consegue escrever).
2. **Escrita confinada a `plugin_analises_*`.** Só as rotas de escrita do próprio domínio
   (`_internal/analyses/*`, `_internal/findings/*`, `_internal/reports/*`) abrem
   transação, e só tocam tabelas com o prefixo `plugin_analises_`. Nenhuma rota de escrita
   toca tabela do core.
3. **Sem query arbitrária livre por default.** Duas opções para a superfície de leitura:
   - **(preferida) rotas de query estruturadas** — endpoints parametrizados por dimensão
     (`/query/atendimentos?bucket=day&split=ia_vs_humano&from=…&to=…`), que montam SQL
     seguro com bind params. O agente escolhe *parâmetros*, não escreve SQL.
   - **(avançado) uma rota `/query/sql` read-only** que aceita SQL só se: começa com
     `SELECT`/`WITH`, roda sob role SELECT-only, com `statement_timeout` + `LIMIT`
     forçado. Mais flexível, mais superfície de risco — adotar só se o analista precisar
     de exploração livre e sob o role SELECT-only.
4. **RBAC por rota via on-behalf-of.** Toda rota reusa `authz.acheck(request, key)` com a
   permissão apropriada (ver §5 abaixo os keys sugeridos). Não inventar auth paralela —
   é o mesmo primitivo do `melhorias` (`internal_routes.py:50-55`, `hmac_guard.py:93-109`).
5. **Auditoria.** HMAC já carrega `request_id` + `on_behalf_of`. Registrar em
   `plugin_analises_audit` (opcional) cada consulta pesada / cada escrita, com o
   `on_behalf_of` e o texto da query, para trilha.
6. **Write-back no core (Fase 3) só via API REST do WhatsBot**, com o token/sessão de um
   usuário — **nunca** SQL cru. Ex.: aplicar uma etiqueta de conversa `venda` vai por
   `POST /api/...` (que passa pelos repos e emite `conversation_event`/broadcast), não por
   `INSERT INTO atendimento_label_links`.

---

## 5. Esboço das tabelas `plugin_analises_*`

Prefixo obrigatório `plugin_analises_` (o migrator recusa o contrário). Timestamps são
`DOUBLE PRECISION` epoch float UTC (paridade com o core). O bloco de chat agêntico
(`_ai_conversations`/`_ai_messages`/`_ai_approvals`) é **copiado da migração 003 do
melhorias** re-prefixado. As tabelas de domínio abaixo são novas.

```sql
-- 001_domain.sql  — entidade "análise" (pedido + estado)
CREATE TABLE IF NOT EXISTS plugin_analises_analyses (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',        -- pergunta/objetivo em PT-BR
    kind            TEXT NOT NULL DEFAULT 'ad_hoc',   -- ad_hoc | scheduled | report
    prompt          TEXT NOT NULL DEFAULT '',         -- a pergunta do gestor
    status          TEXT NOT NULL DEFAULT 'pendente', -- pendente|em_chat|concluida|erro
    requester_user_id INTEGER,                        -- FK LÓGICA users.id (snapshot)
    requester_name  TEXT,
    ai_conversation_id TEXT,                          -- link p/ plugin_analises_ai_conversations.id
    model           TEXT NOT NULL DEFAULT '',
    period_from     DOUBLE PRECISION,                 -- janela analisada (opcional)
    period_to       DOUBLE PRECISION,
    created_at      DOUBLE PRECISION NOT NULL,
    updated_at      DOUBLE PRECISION NOT NULL,
    completed_at    DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS plugin_analises_analyses_status_idx
    ON plugin_analises_analyses (status);

-- achados estruturados (uma linha por métrica/insight) — permite dashboard e re-uso
CREATE TABLE IF NOT EXISTS plugin_analises_findings (
    id              SERIAL PRIMARY KEY,
    analysis_id     INTEGER NOT NULL,
    metric_key      TEXT NOT NULL,                    -- ex: atendimentos_fechados_ia_dia
    label           TEXT NOT NULL DEFAULT '',         -- rótulo PT-BR
    value_num       DOUBLE PRECISION,                 -- valor numérico (quando aplicável)
    value_text      TEXT,                             -- valor/insight textual
    dimensions      TEXT,                             -- JSON: {atendente, dia, canal, ...}
    confidence      TEXT,                             -- READY|PARCIAL|BLOQUEADO (ver docs 01-04)
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS plugin_analises_findings_analysis_idx
    ON plugin_analises_findings (analysis_id);

-- relatórios gerados (markdown + JSON) — o "output contract"
CREATE TABLE IF NOT EXISTS plugin_analises_reports (
    id              SERIAL PRIMARY KEY,
    analysis_id     INTEGER,                          -- NULL se relatório agendado standalone
    title           TEXT NOT NULL DEFAULT '',
    body_md         TEXT NOT NULL DEFAULT '',         -- narrativa PT-BR
    body_json       TEXT,                             -- bloco estruturado p/ automação
    period_from     DOUBLE PRECISION,
    period_to       DOUBLE PRECISION,
    generated_by_user_id INTEGER,                     -- on-behalf-of que gerou
    created_at      DOUBLE PRECISION NOT NULL
);

-- agendamentos (Fase 2) — relatório recorrente (ex: diário p/ Telegram)
CREATE TABLE IF NOT EXISTS plugin_analises_schedules (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    cron            TEXT NOT NULL,                     -- ou campos hora/dia simples
    report_kind     TEXT NOT NULL,                    -- template do relatório
    target          TEXT,                             -- destino (ex: telegram:grupo_id)
    enabled         INTEGER NOT NULL DEFAULT 0,
    last_run_at     DOUBLE PRECISION,
    created_at      DOUBLE PRECISION NOT NULL,
    updated_at      DOUBLE PRECISION NOT NULL
);

-- trilha de auditoria de consultas/escritas (opcional mas recomendado)
CREATE TABLE IF NOT EXISTS plugin_analises_audit (
    id              SERIAL PRIMARY KEY,
    on_behalf_of    INTEGER,                           -- users.id (RBAC)
    request_id      TEXT,                              -- do header HMAC
    action          TEXT NOT NULL,                     -- query|write_finding|write_report
    detail          TEXT,                              -- SQL/params/resumo
    created_at      DOUBLE PRECISION NOT NULL
);
```

```sql
-- 002_ai_chat.sql  — CÓPIA re-prefixada da migração 003 do melhorias
--   plugin_analises_ai_conversations (id TEXT uuid, parent_id = analysis_id, status ACTIVE/COMPLETED/…)
--   plugin_analises_ai_messages       (append-only: role/content/tool_name/tool_input/tool_result/token_usage)
--   plugin_analises_ai_approvals      (id TEXT = approvalId; approved NULL=pendente p/ idempotência)
-- Nota: para um analista READ-ONLY (Fase 1), _ai_approvals fica DORMENTE
-- (só é load-bearing quando a IA escreve — mapa melhorias-engine §5 caveat 2).
```

> No `melhorias`, o parente do chat é `suggestion_id` (`plugin_melhorias_ai_conversations.suggestion_id`,
> `migrations/003_ai_chat.sql:11`). No `analises` vira `analysis_id`.

---

## 6. Como as docs 01–04 entram

As docs desta pasta (`docs/analises/01`–`04`) **não** são consumidas por humanos apenas —
elas são o **"conhecimento de sistema" injetado no system-prompt do agente analista**
(no lado do executor `:8015`, na app `analises`). Divisão:

- **Dicionário de dados (docs 01–03)** → o mapa de tabelas/colunas + os **discriminadores
  canônicos** (do CANON): cliente = `messages.role='user'`; IA = `role='assistant' AND
  agent_key IS NOT NULL`; atendente humano = `role='assistant' AND status='operator' AND
  sent_by_user_id IS NOT NULL`; cards painel-only = `role IN
  ('tool_call','system_notice',…)`. Colunas confirmadas neste checkout:
  `messages(role, agent_key, status, sent_by_user_id, conversation_id, ts)`
  (`db/tables.py:113-144`), `atendimentos(status, opened_at, resolved_at,
  assignee_user_id, origin, custom_attributes)` (`db/tables.py:435-452`).
- **Regras e caveats (doc 04 + decision-brief)** → as **limitações conhecidas** que o
  agente precisa respeitar para não mentir:
  - "Fechado POR atendente" no core **não é confiável** — o close ZERA
    `assignee_user_id` e `resolved_at` é volátil (só o último fechamento sobrevive).
    Workaround: trilha `messages(role='conversation_event')` (ator só no texto PT-BR).
  - `has_ai`/`executions` é COMPLEMENTO (tokens/custo), não o split IA×humano confiável —
    derive o split das `messages`.
  - Sem coluna de conversão hoje → análise de "estratégia que converte" é **PARCIAL/
    BLOQUEADA** até existir um sinal (etiqueta `venda`, atributo, campo de protocolo).
  - Timestamps são epoch float UTC → **sempre** bucketize "no dia" com
    `(to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date`.
- **Cookbook de SQL Postgres (doc 04)** → exemplos prontos que o agente adapta. O campo
  `confidence` em `plugin_analises_findings` carrega esse `READY/PARCIAL/BLOQUEADO` para o
  gestor saber o grau de confiança de cada número.

Exemplo de consulta que o agente rodaria via a rota de leitura (split IA×humano por dia,
derivado das `messages`, TZ SP — casa com a convenção recomendada do CANON):

```sql
-- Atendimentos abertos hoje, classificados por quem respondeu (IA / humano / misto)
WITH turnos AS (
  SELECT
    m.conversation_id,
    bool_or(m.role = 'assistant' AND m.agent_key IS NOT NULL)                              AS tem_ia,
    bool_or(m.role = 'assistant' AND m.status = 'operator'
            AND m.sent_by_user_id IS NOT NULL)                                             AS tem_humano
  FROM messages m
  WHERE m.conversation_id IS NOT NULL
  GROUP BY m.conversation_id
)
SELECT
  CASE
    WHEN t.tem_ia AND t.tem_humano THEN 'misto'
    WHEN t.tem_ia                  THEN 'ia_only'
    WHEN t.tem_humano              THEN 'humano_only'
    ELSE 'sem_resposta'
  END AS classe,
  COUNT(*) AS qtd
FROM atendimentos a
JOIN turnos t ON t.conversation_id = a.id
WHERE (to_timestamp(a.opened_at) AT TIME ZONE 'America/Sao_Paulo')::date
      = (now() AT TIME ZONE 'America/Sao_Paulo')::date
GROUP BY 1
ORDER BY qtd DESC;
```

A **instrumentação recomendada** (colunas net-new que o decision-brief lista: primeira
resposta ts, `closed_by_user_id`, histórico de transição de status, flag resolvedor
IA×humano, label de conversão) entra como **backlog** — documentada aqui, **não**
implementada agora (decisão do CANON: "documentar hoje + apêndice recomendado; NÃO
implementar agora").

---

## 7. Fases sugeridas

| Fase | Escopo | Superfície nova | Fronteira |
|---|---|---|---|
| **1 — Análises read-only sob demanda (chat)** | Gestor abre o chat do `analises`, pergunta em PT-BR, o agente puxa dados via rotas de **leitura read-all** e responde. Grava só `plugin_analises_analyses`/`_findings`/`_reports`/`_ai_*`. `_ai_approvals` dormente (nada é escrito no core). | Clone do transporte+chat (§2.1) + rotas `_internal/query/*` read-only + tabelas de domínio (§5) | Read-all no core, write-own em `plugin_analises_*` |
| **2 — Relatórios agendados / Telegram** | `plugin_analises_schedules` dispara relatórios recorrentes (task de fundo estilo `server/background.py` no gateway, ou trigger externo). Output em `plugin_analises_reports` (md + JSON). O empurrão pro Telegram é **automação externa** (fora de escopo — o plugin só *gera e persiste* o relatório). | `_schedules` + task de agendamento + template de relatório | Igual à Fase 1 (ainda read-only no core) |
| **3 — Write-back no core via API** | O analista aplica resultados de volta no core: ex. etiqueta de conversa `venda` (ground-truth de conversão), atributo personalizado `produto`/`valor`. **SÓ via API REST do WhatsBot** (repos aplicam índice/`conversation_event`/broadcast/RBAC), NUNCA SQL cru. Aqui o `_ai_approvals` volta a ser load-bearing (cada escrita no core exige aprovação humana ✓/✕, herdado do `melhorias`). | Rotas de write-back que chamam a API do core sob on-behalf-of + reativar o gate de aprovação | Escreve no core **só** pela API, com aprovação + RBAC + auditoria |

Sequência recomendada: **começar pela Fase 1** (valor imediato, risco mínimo — read-only),
validar o dicionário/caveats das docs 01–04 com perguntas reais, e só então avaliar 2 e 3.
A Fase 3 depende de uma decisão de produto ainda aberta (qual é o sinal de conversão —
CANON: "a firmar com o uso").

---

## Apêndice — checklist de clone (referência rápida)

1. `cp -r melhorias/ analises/`; renomear `PLUGIN_ID`/namespace `plugin.melhorias.*` →
   `plugin.analises.*`; prefixo de tabela `plugin_melhorias_` → `plugin_analises_`.
2. Manter: `ai_client.py`, `hmac_guard.py`, `chat_logic.py` (partes de chat),
   `internal_routes.py:60-105`, `routes.py:177-397`, `static/{chat,chat_core,ai_section,relogin}.js`,
   migração de chat (003 → `002_ai_chat.sql` re-prefixada).
3. Reescrever: `generation.py` → builder de contexto de análise (ou dropar);
   `logic.py` → entidade "análise"; `internal_routes.py:110-391` → leitura read-all +
   escrita `plugin_analises_*`; `static/{panel,extends}.js` → dashboard/trigger;
   `events.py`; nova migração `001_domain.sql` (§5).
4. Executor `:8015`: adicionar a app `analises` (guides + tool-registry read-only +
   system-prompt com as docs 01–04). HMAC/runner/SSE/relogin compartilhados.
5. RBAC: definir keys `plugin.analises.{view,run,configure}` no `plugin.yaml` (bloco
   `rbac`), reusar `authz.acheck` — não inventar auth paralela.
6. Testar o gate de dormência: sem URL+secret, `_internal/*` retorna 404 (esperado).
