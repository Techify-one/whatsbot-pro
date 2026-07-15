# Plano 50 — Teto em toda leitura de coleção (paginação + limites contra sobrecarga)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-15 · **Escopo:** grande
> **Origem:** pergunta do usuário ("o sistema tem paginação pra não pesar? … qualquer parte que voltasse muitos dados tinha que ter proteção"). **Método:** varredura backend + frontend por 2 sub-agentes `Explore` + leitura direta dos pontos-chave, tudo com `arquivo:linha` verificado.
> O padrão dominante nas partes antigas é **"buscar tudo e renderizar tudo"** — sem `LIMIT` no backend, sem paginação, e no frontend `.map()` de todos os itens sem virtualização. Com base grande (ex.: uma conversa com milhares de mensagens, ou 100k contatos) a query, o payload e o DOM colapsam. O objetivo deste plano é estabelecer uma **política transversal: toda leitura de coleção tem teto** — via paginação real onde o dado cresce sem limite (mensagens, contatos, usage) e `min(limit, cap)` onde já há `LIMIT` mas o parâmetro é livre.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | Prioridade: **histórico de mensagens > lista de contatos > usage por contato > export**, depois os `min(limit,cap)` (🟠), depois defesa em profundidade (🟡) ✅ (2026-07-15) | As waves seguem essa ordem. Wave 1 = mensagens; Wave 2 = contatos; Wave 3 = usage; Wave 4 = export; Wave 5 = 🟡. |
| D2 | Nada em produção pode quebrar ⇒ mudanças **aditivas** e retrocompatíveis; sem stopgap descartável | Assinaturas de repo ganham parâmetros **opcionais** (`limit=None` ⇒ caminho legado byte-idêntico). Callers internos (LLM, manutenção) não mudam. |
| D3 | **Paginação real** (server-side) onde o dado cresce; **não** adotar biblioteca de virtualização agora ✅ (2026-07-15) | Paginar de N em N já mantém o DOM pequeno sem `react-window`. Virtualização fica como melhoria futura (P5), não bloqueia. |
| D4 | Reusar os **modelos que já existem** no repo em vez de inventar padrão novo | Cap de input: molde de `db/filters/spec.py:61` (`_coerce_int(v, 50, 1, 200)`). Paginação de tela: molde de `Executions.js`/`AuditLog.js` (limit+offset+Prev/Next). Busca+cap em dropdown: `NewConversationModal` (`getContacts(q)` + `.slice`). |
| D5 | Postgres é o único backend | Keyset (`before_id`) e `LIMIT/OFFSET` usam índices existentes; sem preocupação com SQLite. |

---

## 1. Resumo executivo

Hoje três leituras crescem **sem teto nenhum** e travam a interface ao escalar: (1) **abrir uma conversa** carrega o histórico **inteiro** (`message_repo.get_by_conversation`/`get_all`, sem `LIMIT` — [message_repo.py:76](../db/repositories/message_repo.py#L76), [:65](../db/repositories/message_repo.py#L65)), renderizado todo no DOM sem "carregar anteriores" ([ContactDetail.js:338](../web/static/js/components/contacts/ContactDetail.js#L338)); (2) **lista de contatos** (`GET /api/contacts` sem `limit/offset` — [contacts.py:247](../server/routes/contacts.py#L247)), com **full-scan da tabela `messages`** a cada busca ([contact_search.py:72](../db/search/contact_search.py#L72)); (3) **uso por contato** (`usage_repo.by_contact`/`detail` sem `LIMIT` — [usage_repo.py:121](../db/repositories/usage_repo.py#L121)). Além disso, dois endpoints têm `LIMIT` na query mas **não capam o parâmetro** de input (`/api/executions`, `/api/webhook-payloads`). A solução é uma política única: **keyset pagination** para o chat (carregar as N mais recentes + "carregar anteriores" ao rolar pra cima), **limit/offset** para as listas/relatórios (copiando `Executions`/`AuditLog`), e **`clamp_limit`** compartilhado para capar todo parâmetro `limit` livre. Já existe o alvo-modelo dentro do próprio repo (`get_context` já tem `.limit()`, mas só o LLM usa; `/api/atendimentos` e `/api/audit` já capam em 200) — este plano generaliza o padrão.

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| Msgs de uma conversa (painel) | [conversations.py:259](../server/routes/conversations.py#L259) → [message_repo.py:76-89](../db/repositories/message_repo.py#L76) | `get_by_conversation(conv_id)` — `select(messages).where(conversation_id==…).order_by(ts)` **sem `.limit()`**. Traz a thread inteira. |
| Msgs contato-cêntrico (painel legado) | [contacts.py:633](../server/routes/contacts.py#L633) → [message_repo.py:65-73](../db/repositories/message_repo.py#L65); caminho multicanal em [contacts.py:622](../server/routes/contacts.py#L622) | `get_all(contact_id)` funde **todos** os canais do número, **sem `.limit()`**. |
| ⚠️ Já existe o caminho capado | [message_repo.py:119](../db/repositories/message_repo.py#L119) `get_context`, [:150](../db/repositories/message_repo.py#L150) `get_context_by_conversation` | Ambos com `.limit(fetch_limit)` — mas **só o LLM usa**; o painel usa as versões sem limite. É o molde de keyset a generalizar. |
| Loader de msgs (frontend) | [useConversationSelection.js:191-194](../web/static/js/components/contacts/hooks/useConversationSelection.js#L191), reload em [:245-248](../web/static/js/components/contacts/hooks/useConversationSelection.js#L245) | `getConversationMessages(convId)` / `getContact(phone)` — **sem limit**. `api.js` [:205](../web/static/js/services/api.js#L205) e [:194](../web/static/js/services/api.js#L194). |
| Render do histórico | [ContactDetail.js:338](../web/static/js/components/contacts/ContactDetail.js#L338) `messages.map(...)`; scroll em [:137-149](../web/static/js/components/contacts/ContactDetail.js#L137) | Renderiza tudo. O efeito de scroll só rola até o fim / até uma msg-alvo — **não há "carregar anteriores"** ao rolar pra cima. |
| Lista de contatos | [contacts.py:247-262](../server/routes/contacts.py#L247) → [contact_repo.py:235](../db/repositories/contact_repo.py#L235) → [contact_search.py:109](../db/search/contact_search.py#L109) `build_list_contacts_query` | Endpoint só aceita `q`/`archived`. Query termina em `.order_by(...)` **sem `.limit()`**. `getContacts` [api.js:161](../web/static/js/services/api.js#L161) sem paginação. |
| ⚠️ Busca = full-scan de `messages` | [contact_search.py:72-106](../db/search/contact_search.py#L72) `contact_ids_matching_message` | `select(messages⋈contacts).order_by(ts.desc())` **sem `.limit()`**, iterado com `.mappings()` e `fold()` em Python, linha a linha. Varre a tabela `messages` **inteira** a cada busca. |
| Sidebar (merge) | [useConversationList.js:70-89](../web/static/js/components/contacts/hooks/useConversationList.js#L70), [conversationRows.js:347-406](../web/static/js/services/conversationRows.js#L347) `buildRows` | `Promise.all([getContacts(TODOS), listConversations({limit:200})])`; `buildRows` itera **todos** os contatos (≥1 linha por contato). Render em [ContactList.js:473](../web/static/js/components/contacts/ContactList.js#L473) sem virtualização/scroll infinito. |
| Tela `/contacts` (full-page) | [ContactsListScreen.js:333](../web/static/js/components/ContactsListScreen.js#L333) `getContacts('',false)`; paginação client `PAGE_SIZE=15` [:31](../web/static/js/components/ContactsListScreen.js#L31), `slice` [:508](../web/static/js/components/ContactsListScreen.js#L508) | Carrega **tudo** e fatia em memória. Não é paginação de servidor. |
| Usage por contato | [usage.py:88](../server/routes/usage.py#L88) → [usage_repo.py:121-169](../db/repositories/usage_repo.py#L121) `by_contact` | Uma linha por contato com uso, `order_by(cost desc)` **sem `.limit()`**. |
| Usage detail (por contato) | [usage.py:97](../server/routes/usage.py#L97) → [usage_repo.py:172-186](../db/repositories/usage_repo.py#L172) `detail` | Todos os registros brutos do contato (um por chamada LLM), **sem `.limit()`**. |
| Dashboard de custos | [CostsDashboard.js:95-96](../web/static/js/components/CostsDashboard.js#L95), render [:324](../web/static/js/components/CostsDashboard.js#L324) | `getUsageByContact` sem paginação; `sorted.map` = uma `<tr>` por contato; busca é client-side. |
| Export de contatos | [contacts.py:350](../server/routes/contacts.py#L350) → [contact_query.py:151-192](../db/repositories/contact_query.py#L151) `list_for_export` | `select(contacts).where(is_group==0)` **sem `.limit()`**, e **N+1**: uma query de tags por contato no loop ([:172-177](../db/repositories/contact_query.py#L172)). |
| 🟠 `limit` não capado | [executions.py:60-96](../server/routes/executions.py#L60) `/api/executions`; [logs.py:54-68](../server/routes/logs.py#L54) `/api/webhook-payloads` | `limit=50` repassado **direto** ao repo (que tem `.limit(limit)`), sem `min(...)`. `?limit=9999999` puxa tudo. |
| ✅ Modelos corretos | [conversations.py:104](../server/routes/conversations.py#L104) `min(limit,200)`; [audit.py:51](../server/routes/audit.py#L51); [logs.py:79](../server/routes/logs.py#L79) `min(limit,5000)`; [spec.py:61](../db/filters/spec.py#L61) `_coerce_int`; [Executions.js:597](../web/static/js/components/Executions.js#L597)/[AuditLog.js:212](../web/static/js/components/AuditLog.js#L212) limit+offset+Prev/Next; `NewConversationModal` `getContacts(q)`+`.slice(0,8)` | São os padrões que este plano generaliza. |

---

## 3. Inventário / análise

Severidade: 🔴 cresce sem teto e sem proteção · 🟠 tem `LIMIT` mas input livre · 🟡 pequeno hoje (defesa em profundidade).

| # | Sev | Item | Ponto de mudança (`arquivo:linha`) | O que falta | Abordagem | Risco | Esforço |
|---|-----|------|-----------------------------------|-------------|-----------|-------|---------|
| I0 | — | Helper de cap + política | **novo** `server/pagination.py` (ou promover `db/filters/spec.py:61` `_coerce_int`) | Não há helper compartilhado | `clamp_limit(v, default, cap)` + constantes `PAGE_MSGS=50/CAP=200`, `PAGE_LIST=50/CAP=200` | Baixo | S |
| I1 | 🔴 | Keyset de mensagens (repo) | [message_repo.py:76](../db/repositories/message_repo.py#L76), [:65](../db/repositories/message_repo.py#L65) | Sem `limit`/cursor | `get_by_conversation(id, *, limit=None, before_id=None)` e `get_all(...)` idem; `None`⇒legado | Médio | M |
| I2 | 🔴 | Endpoints de msgs paginados | [conversations.py:259](../server/routes/conversations.py#L259), [contacts.py:622-633](../server/routes/contacts.py#L622) | Sem params | `?limit&before_id` → `min` via I0; devolver `{messages, has_more}` | Médio | M |
| I3 | 🔴 | Loader + scroll-up (frontend) | [useConversationSelection.js:191](../web/static/js/components/contacts/hooks/useConversationSelection.js#L191), [ContactDetail.js:137](../web/static/js/components/contacts/ContactDetail.js#L137),[:338](../web/static/js/components/contacts/ContactDetail.js#L338), [api.js:194-208](../web/static/js/services/api.js#L194) | Carrega tudo; sem "anteriores" | Carrega página nova; sentinela no topo (IntersectionObserver) → prepend + âncora de scroll | Alto | L |
| I4 | 🔴 | `/api/contacts` paginado | [contacts.py:247](../server/routes/contacts.py#L247) → [contact_repo.py:235](../db/repositories/contact_repo.py#L235) → [contact_search.py:109-224](../db/search/contact_search.py#L109) | Sem `limit/offset` | `limit/offset` (cap via I0) + `.limit().offset()` na query | Médio | M |
| I5 | 🔴 | Cap do full-scan de busca | [contact_search.py:72-106](../db/search/contact_search.py#L72) | Varre `messages` inteira | Bound a K recentes (`.limit(SCAN_CAP)`) ou FTS; ver P2 | Médio | M |
| I6 | 🔴 | Tela `/contacts` server-side | [ContactsListScreen.js:31](../web/static/js/components/ContactsListScreen.js#L31),[:333](../web/static/js/components/ContactsListScreen.js#L333),[:508](../web/static/js/components/ContactsListScreen.js#L508) | Fatia client-side | Trocar por limit/offset + Prev/Next (molde `Executions.js`) | Médio | M |
| I7 | 🔴 | Sidebar scroll infinito | [useConversationList.js:70](../web/static/js/components/contacts/hooks/useConversationList.js#L70), [conversationRows.js:347](../web/static/js/services/conversationRows.js#L347), [ContactList.js:445-473](../web/static/js/components/contacts/ContactList.js#L445) | Sem paginação/scroll | Dirigir por `/api/atendimentos` paginado (já tem limit/offset); append por página; ver P3 | Alto | L |
| I8 | 🔴 | Usage `by_contact` + `detail` | [usage_repo.py:121](../db/repositories/usage_repo.py#L121),[:172](../db/repositories/usage_repo.py#L172); [usage.py:88](../server/routes/usage.py#L88),[:97](../server/routes/usage.py#L97) | Sem `LIMIT` | `by_contact`: top-N + offset; `detail`: limit/offset | Baixo | M |
| I9 | 🔴 | CostsDashboard paginação | [CostsDashboard.js:95](../web/static/js/components/CostsDashboard.js#L95),[:324](../web/static/js/components/CostsDashboard.js#L324) | Renderiza tudo | Prev/Next server-side (molde `Executions.js`) | Baixo | M |
| I10 | 🔴 | Export sem N+1 + stream | [contacts.py:350](../server/routes/contacts.py#L350), [contact_query.py:151-192](../db/repositories/contact_query.py#L151) | N+1 tags + tudo em memória | Batch de tags (reusar [contact_query.py:94-98](../db/repositories/contact_query.py#L94)) + `StreamingResponse` CSV; ver P4 | Médio | M |
| I11 | 🟠 | Capar `limit` livre | [executions.py:60-96](../server/routes/executions.py#L60), [logs.py:54-68](../server/routes/logs.py#L54) | Sem `min(...)` | `clamp_limit` (I0) antes de passar ao repo | Baixo | S |
| I12 | 🟡 | Caps admin/config | list endpoints de tools/agents/variables/users/roles/channels/etc. | Sem teto (pequeno hoje) | Aplicar `clamp_limit` onde barato; defesa em profundidade | Baixo | M |
| I13 | 🟡 | Fan-out de requests | [Attendances.js:140](../web/static/js/components/attendances/Attendances.js#L140) `getConversationLabelsFor` (1/atend.), [ChannelsManager.js:217](../web/static/js/components/ChannelsManager.js#L217) `getChannelStatus` (1/canal) | 1 request por item | Endpoint batch; ver P6 | Médio | M |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|----------|-------------------------|
| Capar `get_context`/`get_context_by_conversation` (LLM) | Já têm `.limit(fetch_limit)` ([message_repo.py:145](../db/repositories/message_repo.py#L145),[:177](../db/repositories/message_repo.py#L177)). São o **modelo**, não o problema. |
| Paginar agregados globais de usage (`summary`/`global_summary`) | São `SUM/COUNT` ([usage_repo.py:70](../db/repositories/usage_repo.py#L70),[:95](../db/repositories/usage_repo.py#L95)) — retornam 1 linha. Seguros. |
| Paginar tags/config/plugins/roles/quick-replies/… | Dataset naturalmente pequeno (uma linha por entidade administrada). Entram só como 🟡 (I12), não como 🔴. |
| Adotar `react-window`/virtualização agora | D3: paginar de N em N já mantém o DOM pequeno sem lib nova. Virtualização é P5 (futuro). |
| Version histories (`*_history`) sem limit | Crescem lentamente (uma linha por save, por chave). Baixo risco; cabem em I12 se sobrar. |
| `/api/logs` sem cap explícito | Lê de deque em memória ([logs.py:44](../server/routes/logs.py#L44)) — teto real = tamanho do buffer, não o banco. Deixar como está. |
| `get_context` já cobre o painel | **Não** — o painel usa `get_by_conversation`/`get_all` (sem limit). São funções distintas. |

---

## 4. Contratos fixos (frontend e backend paralelizam contra estes)

**4.1 — Mensagens (keyset, mais recentes primeiro por página):**
```
GET /api/atendimentos/{conv_id}/messages?limit=50&before_id=<msg_id|omit>
GET /api/contacts/{phone}?limit=50&before_id=<msg_id|omit>        (caminho legado/multicanal idem)

200 { ok, data: { ...contact/conv..., messages: [oldest→newest da página], has_more: bool } }
```
- Sem `before_id` ⇒ a página **mais recente** (as `limit` últimas). Com `before_id` ⇒ as `limit` msgs **anteriores** àquela (id < before_id).
- `has_more` = existe ao menos 1 msg mais antiga que a página atual (para o frontend decidir se arma o sentinela).
- Repo: fetch newest-first com `.limit(limit)` (+ `where id < before_id`), retorna a lista **cronológica** (oldest→newest). `limit=None` mantém o caminho legado (tudo) para callers internos.

**4.2 — Listas (limit/offset, molde `/api/atendimentos`):**
```
GET /api/contacts?q=&archived=&limit=50&offset=0
200 { ok, data: { items: [...], total: N, has_more: bool } }   (ver P1 sobre o shape)
```
- `limit` capado por `clamp_limit(limit, 50, 200)`. `offset` ≥ 0.

**4.3 — Helper central (I0):**
```python
# server/pagination.py
def clamp_limit(value: int | None, default: int, cap: int) -> int: ...
PAGE_MSGS, CAP_MSGS = 50, 200
PAGE_LIST, CAP_LIST = 50, 200
```

---

## 5. Fases / Roadmap

```
WAVE 0  F0(helper) ─── F1(cap 🟠)                         ← F1 depende de F0
           │ (F0 desbloqueia os caps de input de TODAS as waves)
           ▼
WAVE 1  F2(caracterização) ─▶ F3(backend msgs) ─▶ F4(frontend scroll-up)   ← 🔴 crítico, sequencial
                                    │
   (após F0+F2, os BACKENDS de hotspots são independentes entre si:)
WAVE 2  F5(/api/contacts) · F6(cap busca) ──▶ F7(/contacts) · F8(sidebar)
WAVE 3  F9(usage backend) ──▶ F10(CostsDashboard)
WAVE 4  F11(export)
WAVE 5  F12(caps 🟡) · F13(batch fan-out)                 ← defesa em profundidade
```

> **Paralelização agressiva:** depois de F0 e da caracterização (F2), os **backends** F3, F5/F6, F9 e F11 tocam repos/endpoints disjuntos → podem ser feitos em paralelo (🟢). Os **frontends** que dependem deles (F4, F7/F8, F10) seguem sequenciais dentro de cada hotspot. F4, F8 e F3 são os únicos de risco **alto** (scroll/merge) — fazer sozinhos, com caracterização antes.

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / Sub-plano |
|------|------|-----------|-------|-------|---------------------------|
| 0 | F0 | Helper `clamp_limit` + constantes | 🔴 [bloqueia: F1,F2+…] | Baixo | `clamp_limit(9e9,50,200)==200`; import ok |
| 0 | F1 | Capar `/api/executions` + `/api/webhook-payloads` | 🟢 [depende: F0] | Baixo | `?limit=99999` devolve ≤ cap |
| 1 | F2 | Caracterização do chat (antes de mexer) | 🔴 [bloqueia: F3,F4] | Baixo | Testes atuais do fluxo de msgs verdes e fixados |
| 1 | F3 | Backend keyset de mensagens | 🔴 [depende: F2] | Médio | `curl` §4.1 devolve 50 + `has_more`; `before_id` pagina |
| 1 | F4 | Frontend scroll-up (carregar anteriores) | 🔴 [depende: F3] | Alto | Abrir conversa longa carrega 50; rolar ao topo carrega +50 sem "pular" |
| 2 | F5 | `/api/contacts` limit/offset | 🟢 [depende: F0] | Médio | `?limit=50&offset=0` devolve ≤50 |
| 2 | F6 | Cap do full-scan de busca | 🟢 [depende: F0] | Médio | Busca não varre `messages` inteira (EXPLAIN/limite) |
| 2 | F7 | Tela `/contacts` server-side | 🟢 [depende: F5] | Médio | Prev/Next busca no servidor; sem `slice` client |
| 2 | F8 | Sidebar scroll infinito | 🔴 [depende: F5] | Alto | Sidebar carrega 1ª página; rolar ao fim carrega +página |
| 3 | F9 | Usage `by_contact`/`detail` com teto | 🟢 [depende: F0] | Baixo | `by_contact` top-N + offset; `detail` limit/offset |
| 3 | F10 | CostsDashboard paginação | 🟢 [depende: F9] | Baixo | Prev/Next server-side; DOM ≤ página |
| 4 | F11 | Export: batch tags + streaming | 🟢 [depende: F0] | Médio | Export de N grande sem N+1 nem OOM |
| 5 | F12 | Caps 🟡 admin/config | 🟢 | Baixo | `clamp_limit` aplicado; suíte verde |
| 5 | F13 | Endpoints batch p/ fan-out | 🟢 | Médio | 1 request cobre N itens (labels/status) |

**Disciplina (regras do repo):** verde a cada fase; **caracterização ANTES** de mexer no chat (F2 → F3/F4); **um refactor por commit**; nunca avançar com teste vermelho não explicado.

---

### Fase 0 — Helper de cap + política 🔴 [bloqueia tudo]
**Objetivo:** um único ponto que capa `limit` e nomeia os defaults, para todo o resto reusar.
**Itens:**
1. `[sequencial]` Criar `server/pagination.py` com `clamp_limit(value, default, cap)` (trata `None`/negativo/str→int) + constantes `PAGE_MSGS/CAP_MSGS`, `PAGE_LIST/CAP_LIST`. Alternativa aceitável (D4): promover `_coerce_int` de [db/filters/spec.py:61](../db/filters/spec.py#L61) para módulo compartilhado e reusar.
2. `[paralelo]` Docstring curta declarando a **política**: "todo endpoint que lista coleção passa o `limit` por `clamp_limit`; coleções que crescem sem teto (mensagens/contatos/usage) usam paginação real (§4)".

**Pronto quando:** `clamp_limit(9_000_000, 50, 200) == 200`, `clamp_limit(None, 50, 200) == 50`, `clamp_limit(-5, 50, 200) >= 1`; módulo importa sem ciclo.

#### Status de execução — Fase 0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(escolhas + porquê; desvios)_
- **Problemas / pendências:** _(pendências/decisões)_
- **Verificação:** _(testes + resultado)_

---

### Fase 1 — Capar os `limit` livres (🟠) 🟢 [depende: F0]
**Objetivo:** nenhum parâmetro `limit` de input passa reto ao banco.
**Itens:**
1. `[paralelo]` [executions.py:62](../server/routes/executions.py#L62): `limit = clamp_limit(limit, 50, 200)` antes do [:93](../server/routes/executions.py#L93) `list_executions`.
2. `[paralelo]` [logs.py:55](../server/routes/logs.py#L55) (`/api/webhook-payloads`): `limit = clamp_limit(limit, 50, 200)` antes do [:61](../server/routes/logs.py#L61) `get_webhook_payloads` (e do fallback in-memory `[-limit:]`).
3. `[paralelo]` Varredura de fechamento: `grep -rn "limit: int" server/routes` — qualquer outro que repasse `limit` sem `min` ganha `clamp_limit` (exceto os que já capam: `conversations.py:104`, `audit.py:51`, `logs.py:79`).

**Pronto quando:** `GET /api/executions?limit=99999` e `/api/webhook-payloads?limit=99999` retornam **≤ cap**; os já-capados seguem iguais.

#### Status de execução — Fase 1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 2 — Caracterização do fluxo de chat 🔴 [bloqueia: F3, F4]
**Objetivo:** fixar o comportamento atual de carregar/abrir mensagens ANTES de paginar, para pegar regressão.
**Itens:**
1. `[sequencial]` Em [tests/test_endpoints.py](../tests/test_endpoints.py), garantir/adicionar casos que hoje passam: abrir conversa retorna as mensagens na ordem cronológica; `mark_read=false` não zera badge; buffer de WS (pré/durante fetch) ainda mescla. Anotar o shape atual da resposta (`messages` na raiz do `data`).
2. `[sequencial]` Registrar o baseline: quantas msgs uma conversa de teste com >120 mensagens devolve hoje (todas) — vira a asserção "antes" que a paginação vai mudar de forma **consciente**.

**Pronto quando:** os testes de mensagens estão verdes e cobrem ordem + merge de WS; o baseline "traz tudo" está explícito num teste (que a F3 vai atualizar).

#### Status de execução — Fase 2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 3 — Backend: keyset de mensagens 🔴 [depende: F2]
**Objetivo:** os endpoints do painel devolvem a **página mais recente** + cursor de "anteriores".
**Itens:**
1. `[sequencial]` [message_repo.py:76](../db/repositories/message_repo.py#L76) `get_by_conversation` e [:65](../db/repositories/message_repo.py#L65) `get_all`: adicionar `*, limit: int | None = None, before_id: int | None = None`. Com `limit`: `order_by(ts.desc(), id.desc()).limit(limit)` (+ `where id < before_id` quando dado), depois **reverter** para cronológico. `limit=None` ⇒ query atual byte-idêntica (D2). Usa `idx_msg_conversation_ts`.
2. `[sequencial]` [conversations.py:259](../server/routes/conversations.py#L259): ler `limit`/`before_id` do request, `clamp_limit(limit, PAGE_MSGS, CAP_MSGS)`, chamar o repo paginado, e devolver `has_more` (ex.: pedir `limit+1` e cortar, ou um `exists` do id anterior). A resolução de `agent_name` ([:262-270](../server/routes/conversations.py#L262)) e os `last_inbound_ts`/hints ([:283](../server/routes/conversations.py#L283)) operam **sobre a página** — reconferir que `last_inbound_ts` de janela Cloud use a fonte correta (o repo `last_inbound_ts`, não o max da página) para não fechar a janela por paginação. ⚠️ ver Riscos.
3. `[sequencial]` [contacts.py:622](../server/routes/contacts.py#L622) e [:633](../server/routes/contacts.py#L633): idem para o caminho multicanal e o legado `get_all`.
4. `[paralelo]` Atualizar o teste-baseline da F2 para o novo shape (`messages` = última página, `has_more`).

**Pronto quando:** `curl …/messages` sem `before_id` devolve as **50 últimas** em ordem cronológica + `has_more:true` numa conversa longa; com `before_id=<id da 1ª da página>` devolve as 50 anteriores; conversa curta devolve tudo + `has_more:false`.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 4 — Frontend: carregar mensagens anteriores (scroll-up) 🔴 [depende: F3]
**Objetivo:** abrir a conversa carrega só a página recente; rolar pro topo carrega as anteriores sem "pular".
**Itens:**
1. `[sequencial]` [api.js:194-208](../web/static/js/services/api.js#L194): `getContact`/`getConversationMessages` aceitam `{limit, beforeId}` e montam a query.
2. `[sequencial]` [useConversationSelection.js:191](../web/static/js/components/contacts/hooks/useConversationSelection.js#L191): no load inicial não passa `beforeId` (página recente); guardar `hasMore` + o `before_id` (menor id carregado). Nova ação `loadOlder()` que busca a página anterior e **prepend**a em `contactData.messages` (dedup pelo `_id`, como o merge de WS já faz).
3. `[sequencial]` [ContactDetail.js:137-149](../web/static/js/components/contacts/ContactDetail.js#L137),[:338](../web/static/js/components/contacts/ContactDetail.js#L338): sentinela no topo da lista (IntersectionObserver) que dispara `loadOlder()` quando `hasMore`; **âncora de scroll** — capturar `scrollHeight` antes do prepend e restaurar `scrollTop += (novo - antigo)` para a viewport não saltar. O auto-scroll-to-bottom existente só vale no load inicial / nova msg, **não** ao carregar anteriores. Fallback aceitável (D3/P): botão "Carregar mensagens anteriores" no topo em vez do sentinela.
4. `[paralelo]` Sandbox ([Sandbox.js:126](../web/static/js/components/Sandbox.js#L126)) herda o mesmo loader — conferir que não quebra.

**Pronto quando:** conversa com >200 msgs abre instantânea mostrando as 50 recentes ancoradas no fim; rolar até o topo carrega +50 e a posição visual **não salta**; chegar no começo para de carregar (`has_more:false`); nova mensagem recebida ainda faz auto-scroll pro fim.

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 5 — `/api/contacts` com limit/offset 🟢 [depende: F0]
**Objetivo:** o endpoint de contatos devolve página, não a tabela.
**Itens:**
1. `[sequencial]` [contact_search.py:109](../db/search/contact_search.py#L109) `build_list_contacts_query`: aceitar `limit`/`offset` e aplicar `.limit().offset()` após o `.order_by` [:220](../db/search/contact_search.py#L220).
2. `[sequencial]` [contact_repo.py:235](../db/repositories/contact_repo.py#L235) `list_contacts`: propagar `limit`/`offset` (opcionais; `None` = tudo, p/ callers internos — D2).
3. `[sequencial]` [contacts.py:247-262](../server/routes/contacts.py#L247): ler `limit`/`offset`, `clamp_limit(limit, PAGE_LIST, CAP_LIST)`, devolver `{items, total?, has_more}` (shape ver P1). ⚠️ `avatar_v` continua por item.
4. `[paralelo]` [api.js:161](../web/static/js/services/api.js#L161) `getContacts(q, archived, {limit, offset})` — retrocompatível (sem opts = 1ª página).

**Pronto quando:** `GET /api/contacts?limit=50&offset=0` devolve ≤50 + `has_more`; sem `limit` (caller interno) ainda devolve tudo; a sidebar/`/contacts` ainda montam (compat até F7/F8).

#### Status de execução — Fase 5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 6 — Cap do full-scan de busca 🟢 [depende: F0]
**Objetivo:** buscar por conteúdo de mensagem não varre a tabela `messages` inteira.
**Itens:**
1. `[sequencial]` [contact_search.py:72-106](../db/search/contact_search.py#L72) `contact_ids_matching_message`: limitar a varredura às K mensagens mais recentes (`.limit(SCAN_CAP)` sobre o `order_by(ts.desc())`) — bound de trabalho por busca. Documentar o trade-off (matches em mensagens muito antigas podem escapar) — ver P2.
2. `[paralelo]` Exigir `len(folded_q) >= 2` antes de acionar o scan (busca de 1 char não dispara full-scan).

**Pronto quando:** busca em base grande responde em tempo constante (não proporcional ao total de mensagens); `EXPLAIN`/medição confirma o teto; nomes/telefone ainda casam (esse ramo não usa o scan).

#### Status de execução — Fase 6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 7 — Tela `/contacts` server-side 🟢 [depende: F5]
**Objetivo:** a tela full-page pagina no servidor, não em memória.
**Itens:**
1. `[sequencial]` [ContactsListScreen.js:333](../web/static/js/components/ContactsListScreen.js#L333): buscar `getContacts(q, archived, {limit, offset})` por página; remover o `slice(start, start+PAGE_SIZE)` [:508](../web/static/js/components/ContactsListScreen.js#L508) e o `PAGE_SIZE=15` client [:31](../web/static/js/components/ContactsListScreen.js#L31).
2. `[sequencial]` Prev/Next lendo `has_more`/`total` (molde [Executions.js:1056](../web/static/js/components/Executions.js#L1056)); reset de página na busca (debounce).

**Pronto quando:** navegar páginas dispara fetch server-side; DOM tem no máx. uma página; busca reinicia na página 1; modo escuro legível.

#### Status de execução — Fase 7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 8 — Sidebar com scroll infinito 🔴 [depende: F5]
**Objetivo:** a sidebar de conversas carrega por página e cresce ao rolar, sem baixar todos os contatos.
**Itens:**
1. `[sequencial]` [useConversationList.js:70-89](../web/static/js/components/contacts/hooks/useConversationList.js#L70): dirigir a lista pela paginação de `/api/atendimentos` (que **já** tem limit/offset — [conversations.py:104](../server/routes/conversations.py#L104)); acumular páginas em `offset` crescente; a chamada `getContacts` deixa de ser "todos" e passa a enriquecer **a página** (ver P3 sobre o join contatos×conversas em `buildRows` [conversationRows.js:347](../web/static/js/services/conversationRows.js#L347)).
2. `[sequencial]` [ContactList.js:445](../web/static/js/components/contacts/ContactList.js#L445): sentinela no fim (IntersectionObserver) → carrega próxima página e **append**a (dedup por `conversation_id`, reusando `upsertConversationRow`/`sortContacts` [conversationRows.js:503](../web/static/js/services/conversationRows.js#L503)).
3. `[sequencial]` Preservar filtros/abas: os filtros client-side ([conversationRows.js:120](../web/static/js/services/conversationRows.js#L120)) passam a rodar sobre as páginas carregadas — ⚠️ decidir se filtros que precisam do dataset inteiro viram server-side (P3).

**Pronto quando:** a sidebar abre com 1 página (~50); rolar ao fim carrega a próxima; contatos sem conversa ainda aparecem (P3); WS `conversation_upsert` continua consistente; busca/arquivados refazem da página 1.

#### Status de execução — Fase 8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 9 — Usage por contato com teto 🟢 [depende: F0]
**Objetivo:** relatórios de uso não retornam linha por contato/registro sem limite.
**Itens:**
1. `[sequencial]` [usage_repo.py:121](../db/repositories/usage_repo.py#L121) `by_contact`: aceitar `limit`/`offset`; já ordena por `cost desc` ([:136](../db/repositories/usage_repo.py#L136)) ⇒ "top-N gastadores" + paginação. O `by_type_stmt` ([:144](../db/repositories/usage_repo.py#L144)) deve casar só os contatos da página (ou seguir agregando e ser filtrado em Python — medir).
2. `[sequencial]` [usage_repo.py:172](../db/repositories/usage_repo.py#L172) `detail`: aceitar `limit`/`offset` (registros brutos crescem por contato).
3. `[sequencial]` [usage.py:88](../server/routes/usage.py#L88),[:97](../server/routes/usage.py#L97): repassar com `clamp_limit`.

**Pronto quando:** `by_contact?limit=50` devolve top-50 por custo + `has_more`; `contact/{phone}?limit=50` pagina os registros; agregados globais (`summary`) intactos.

#### Status de execução — Fase 9
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 10 — CostsDashboard paginação 🟢 [depende: F9]
**Objetivo:** o dashboard renderiza uma página de contatos, não todos.
**Itens:**
1. `[sequencial]` [CostsDashboard.js:95](../web/static/js/components/CostsDashboard.js#L95): `getUsageByContact` com `{limit, offset}`; Prev/Next server-side (molde `Executions.js`).
2. `[paralelo]` A busca ([:146](../web/static/js/components/CostsDashboard.js#L146)) client-side vira busca por página (ou server-side se P1 padronizar); render [:324](../web/static/js/components/CostsDashboard.js#L324) limitado à página.

**Pronto quando:** o dashboard mostra top-N + Prev/Next; DOM ≤ página; totais (summary) seguem corretos; modo escuro ok.

#### Status de execução — Fase 10
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 11 — Export sem N+1 + streaming 🟢 [depende: F0]
**Objetivo:** exportar contatos não estoura memória nem faz N+1.
**Itens:**
1. `[sequencial]` [contact_query.py:151-192](../db/repositories/contact_query.py#L151) `list_for_export`: substituir a query de tags por-contato no loop ([:172-177](../db/repositories/contact_query.py#L172)) por **um** batch (reusar o helper de tags em lote de [contact_query.py:94-98](../db/repositories/contact_query.py#L94)).
2. `[sequencial]` [contacts.py:350](../server/routes/contacts.py#L350) export: emitir CSV via `StreamingResponse` (gerador que pagina o cursor) em vez de materializar tudo — ver P4 (cap alternativo com header `X-Contacts-Truncated`, molde `audit/export`).

**Pronto quando:** export de base grande completa sem pico de memória e sem N+1 (uma query de tags); CSV íntegro; tags corretas por linha.

#### Status de execução — Fase 11
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 12 — Caps 🟡 (defesa em profundidade) 🟢
**Objetivo:** endpoints de admin/config pequenos hoje não viram buraco amanhã.
**Itens:**
1. `[paralelo]` Aplicar `clamp_limit` (I0) onde houver `limit` de input nos list endpoints de tools/agents/variables/users/roles/channels/quick-replies/custom-attributes/histories; onde não há `limit` e o dataset é realmente pequeno, apenas documentar o limite natural (não paginar).

**Pronto quando:** nenhum list endpoint aceita `limit` ilimitado; suíte verde; nada de UX muda perceptivelmente.

#### Status de execução — Fase 12
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase 13 — Batch para os fan-outs 🟢
**Objetivo:** parar de disparar 1 request por item em telas de N itens.
**Itens:**
1. `[paralelo]` Endpoint batch de etiquetas de atendimento (substitui [Attendances.js:140](../web/static/js/components/attendances/Attendances.js#L140) `getConversationLabelsFor` por-item) e de status de canal (substitui [ChannelsManager.js:217](../web/static/js/components/ChannelsManager.js#L217) `getChannelStatus` por-canal) — ver P6.

**Pronto quando:** abrir Atendimentos (modo etiqueta) e Canais faz **1** request agregada, não N.

#### Status de execução — Fase 13
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Keyset de msgs | `order_by(ts)` com ts empatado perde estabilidade ao paginar | Desempate por `id` (`ts.desc(), id.desc()`); cursor por `before_id` (chave estável), não offset. |
| Janela Cloud 24h | Paginar o histórico faz o `last_inbound_ts` do handler ([conversations.py:283](../server/routes/conversations.py#L283)) ler só a página → janela "fecha" errada | Manter o cálculo da janela via `message_repo.last_inbound_ts` (query dedicada, [message_repo.py:216](../db/repositories/message_repo.py#L216)), **nunca** o `max(ts)` da página. |
| Scroll-up (F4) | Prepend faz a viewport "saltar"; auto-scroll-to-bottom brigando com carregar-anteriores | Âncora de scroll (salvar/restaurar `scrollHeight`); auto-bottom só no load inicial / nova msg, guardado por flag. |
| Merge de WS + página | `new_message` durante paginação pode duplicar/desordenar | Reusar o dedup por `_id` e o `isDuplicateMessage` já existentes; append só no fim, prepend só no topo. |
| Sidebar (F8) | `buildRows` cruza contatos×conversas; paginar por conversa pode sumir com contatos-sem-conversa e quebrar filtros client-side | P3 decide o modelo (conversa-first + enriquecimento por página); filtros que exigem dataset inteiro migram server-side. Fazer sozinho, com caracterização. |
| Full-scan de busca (F6) | Bound de scan faz matches antigos sumirem | Trade-off documentado; caminho futuro = FTS/`unaccent` no Postgres (P2). Nomes/telefone não usam o scan. |
| Compat de shape | Trocar `data` de lista→`{items,...}` quebra callers | P1: manter retrocompat (envelopar sem quebrar) ou migrar callers junto, com teste. |
| Postgres | Suíte precisa do banco de teste | `WHATSBOT_TEST_DB_URL` com `test` no nome (trava de segurança); `DROP SCHEMA` por processo. |
| Modo escuro | Telas novas (Prev/Next, botão "carregar anteriores") ilegíveis | `wa-*`/`.wa-field`; testar com `.dark` (regra do CLAUDE.md). |
| Caracterização | Paginar sem baseline esconde regressão no chat | F2 é barreira: só mexe em msgs (F3/F4) com testes de mensagens verdes e fixados. |

---

## 7. Perguntas em aberto

- **P1 — Shape da resposta paginada de listas.** ⏸️ ADIADO (default: aditivo). Contexto: `/api/contacts` hoje devolve `data: [...]`; paginação quer `total`/`has_more`. Opções: (a) `data: {items, total, has_more}` e migrar os 2 callers (sidebar, `/contacts`) juntos; (b) manter `data: [...]` e mandar `total`/`has_more` em headers. **Recomendação:** (a) — mais explícito, e os callers mudam nesta mesma wave. Padronizar igual em usage.
- **P2 — Busca por conteúdo: bound simples vs FTS.** ⏸️ ADIADO (default: bound). Opções: (a) `.limit(SCAN_CAP)` nas mensagens recentes (rápido, perde matches antigos); (b) `tsvector`/FTS com `unaccent` no Postgres (completo, migration + índice). **Recomendação:** (a) agora (F6), (b) como plano futuro se a busca profunda virar requisito.
- **P3 — Modelo da sidebar paginada.** ⏸️ ADIADO (decidir no início da F8). Contexto: `buildRows` cruza `getContacts`(todos) × `listConversations`(200). Opções: (a) conversa-first — página vem de `/api/atendimentos`, enriquecimento de contato por página (contatos-sem-conversa aparecem por um fetch separado/aba); (b) manter o merge mas paginar `getContacts` e casar por página. **Recomendação:** (a) — alinha com o endpoint que já pagina; tratar "contatos sem conversa" como caso à parte. Filtros que precisam do todo → server-side.
- **P4 — Export: streaming vs cap.** ⏸️ ADIADO (default: streaming + N+1 fix). Opções: (a) `StreamingResponse` paginando o cursor (exporta tudo, memória constante); (b) hard cap (ex.: 10000) + header `X-Contacts-Truncated` (molde `audit/export` [audit.py:22](../server/routes/audit.py#L22)). **Recomendação:** (a); adotar (b) só se streaming CSV complicar. O N+1 de tags é corrigido em ambos.
- **P5 — Virtualização de listas.** ⏸️ ADIADO (default: NÃO, D3). Paginar de N em N já mantém o DOM pequeno. Reabrir só se uma página única precisar renderizar milhares (ex.: histórico com página gigante) — aí `react-window` vendorizado.
- **P6 — Endpoints batch (F13).** ⏸️ ADIADO. Contexto: labels-por-atendimento e status-por-canal disparam N requests. Opções: (a) endpoint que recebe lista de ids e devolve mapa; (b) já embutir os dados no payload da lista (labels no item de `/api/atendimentos`, status no de `/api/channels`). **Recomendação:** (b) quando o dado é barato de juntar; (a) quando é caro/volátil (status de canal muda).

---

## 8. Apêndice — arquivos-chave

**Backend — helper/política**
- `server/pagination.py` — **novo** (`clamp_limit`, constantes). Alt.: [db/filters/spec.py:61](../db/filters/spec.py#L61).

**Backend — mensagens (Wave 1)**
- [db/repositories/message_repo.py:65](../db/repositories/message_repo.py#L65),[:76](../db/repositories/message_repo.py#L76) — keyset em `get_all`/`get_by_conversation`.
- [server/routes/conversations.py:259](../server/routes/conversations.py#L259) e [server/routes/contacts.py:622](../server/routes/contacts.py#L622),[:633](../server/routes/contacts.py#L633) — params + `has_more`.

**Backend — contatos/usage/export (Waves 2–4)**
- [db/search/contact_search.py:72](../db/search/contact_search.py#L72),[:109](../db/search/contact_search.py#L109) — cap do scan + limit/offset na lista.
- [db/repositories/contact_repo.py:235](../db/repositories/contact_repo.py#L235); [db/repositories/contact_query.py:151](../db/repositories/contact_query.py#L151) (export N+1).
- [db/repositories/usage_repo.py:121](../db/repositories/usage_repo.py#L121),[:172](../db/repositories/usage_repo.py#L172); [server/routes/usage.py:88](../server/routes/usage.py#L88),[:97](../server/routes/usage.py#L97).
- [server/routes/contacts.py:247](../server/routes/contacts.py#L247) (lista), [:350](../server/routes/contacts.py#L350) (export).

**Backend — caps 🟠/🟡**
- [server/routes/executions.py:60](../server/routes/executions.py#L60); [server/routes/logs.py:54](../server/routes/logs.py#L54).

**Frontend**
- [web/static/js/services/api.js:161](../web/static/js/services/api.js#L161),[:194](../web/static/js/services/api.js#L194),[:205](../web/static/js/services/api.js#L205),[:833](../web/static/js/services/api.js#L833) — params de paginação.
- [web/static/js/components/contacts/hooks/useConversationSelection.js:191](../web/static/js/components/contacts/hooks/useConversationSelection.js#L191); [.../ContactDetail.js:137](../web/static/js/components/contacts/ContactDetail.js#L137),[:338](../web/static/js/components/contacts/ContactDetail.js#L338).
- [web/static/js/components/contacts/hooks/useConversationList.js:70](../web/static/js/components/contacts/hooks/useConversationList.js#L70); [.../ContactList.js:445](../web/static/js/components/contacts/ContactList.js#L445); [web/static/js/services/conversationRows.js:347](../web/static/js/services/conversationRows.js#L347),[:503](../web/static/js/services/conversationRows.js#L503).
- [web/static/js/components/ContactsListScreen.js:31](../web/static/js/components/ContactsListScreen.js#L31),[:508](../web/static/js/components/ContactsListScreen.js#L508); [web/static/js/components/CostsDashboard.js:95](../web/static/js/components/CostsDashboard.js#L95),[:324](../web/static/js/components/CostsDashboard.js#L324).

**Testes**
- [tests/test_endpoints.py](../tests/test_endpoints.py) — caracterização do chat (F2) + casos de paginação (msgs/contatos/usage/export/caps).

---

## 9. Checklist de verificação

- [ ] `clamp_limit` capa todo `limit` de input (executions, webhook-payloads, contacts, usage) → `?limit=99999` nunca excede o cap.
- [ ] Abrir conversa longa (>200 msgs) carrega só a página recente; rolar ao topo carrega +N **sem salto** de scroll; começo para de carregar.
- [ ] Nova mensagem recebida ainda faz auto-scroll pro fim (não regrediu com a paginação).
- [ ] Janela Cloud 24h (`session_open`) correta mesmo com histórico paginado.
- [ ] `/api/contacts?limit=&offset=` pagina; caller interno sem `limit` ainda recebe tudo.
- [ ] Busca por conteúdo não varre a tabela `messages` inteira (medição/EXPLAIN).
- [ ] Tela `/contacts` e sidebar: DOM ≤ uma página; scroll/Prev-Next busca no servidor; contatos-sem-conversa ainda aparecem.
- [ ] Usage `by_contact` (top-N + Prev/Next) e `detail` paginados; `summary` global intacto.
- [ ] Export: sem N+1 de tags, sem pico de memória, CSV íntegro.
- [ ] `venv/bin/python -m pytest tests/test_endpoints.py -q` **verde** no Postgres (`WHATSBOT_TEST_DB_URL`).
- [ ] `node --test` verde nos módulos puros tocados (`conversationRows.js` e afins).
- [ ] Telas novas (Prev/Next, "carregar anteriores") legíveis no **modo escuro** (`wa-*`/`.wa-field`).
- [ ] Nenhum segredo em URL; reload / voltar-avançar não quebra paginação nem o chat.
