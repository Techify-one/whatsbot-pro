# Plano 50 — Teto em toda leitura de coleção (paginação + limites contra sobrecarga)

> **Status:** EM EXECUÇÃO · **Data:** 2026-07-15 · **Escopo:** grande · **Branch:** `feature/paginacao-teto-colecoes`
> **Origem:** pergunta do usuário ("o sistema tem paginação pra não pesar? … qualquer parte que voltasse muitos dados tinha que ter proteção").
> Política transversal: **toda leitura de coleção tem teto** — paginação real onde o dado cresce sem limite (mensagens, contatos, usage) e `clamp_limit(limit, default, cap)` onde já há `LIMIT` mas o parâmetro é livre.
> **Como usar:** preencher o "Status de execução" de cada fase ANTES de avançar.

---

## Decisões travadas (não reabrir)

| # | Decisão |
|---|---------|
| D1 | Prioridade: histórico de mensagens > contatos > usage > export, depois os `min(limit,cap)`, depois defesa em profundidade. |
| D2 | Nada em produção quebra — mudanças **aditivas**/retrocompatíveis; assinaturas de repo ganham params **opcionais** (`limit=None` ⇒ caminho legado byte-idêntico). |
| D3 | **Paginação real** (server-side) onde o dado cresce; **não** adotar virtualização (`react-window`) agora. |
| D4 | Reusar modelos que já existem no repo (`_coerce_int` de `db/filters/spec.py:61`; `Executions.js`/`AuditLog.js` para limit+offset+Prev/Next). |
| D5 | Postgres é o único backend (keyset `before_id` + `LIMIT/OFFSET` usam índices existentes). |

## Contratos fixos

**Mensagens (keyset):** `GET .../messages?limit=50&before_id=<id|omit>` → `{ ..., messages: [oldest→newest da página], has_more: bool }`. Sem `before_id` = página mais recente; com `before_id` = as `limit` anteriores (id < before_id).

**Listas (limit/offset):** `GET /api/contacts?q=&archived=&limit=50&offset=0` → `{ items, total?, has_more }`.

**Helper (F0):** `server/pagination.py` — `clamp_limit(value, default, cap)`, `clamp_offset(value)`, constantes `PAGE_MSGS/CAP_MSGS`, `PAGE_LIST/CAP_LIST` (50/200).

## Roadmap

```
WAVE 0  F0(helper) → F1(cap limites livres)
WAVE 1  F2(caracterização) → F3(backend msgs) → F4(frontend scroll-up)
WAVE 2  F5(/api/contacts) · F6(cap busca) → F7(/contacts) · F8(sidebar)
WAVE 3  F9(usage backend) → F10(CostsDashboard)
WAVE 4  F11(export)
WAVE 5  F12(caps admin) · F13(batch fan-out)
```

Disciplina: verde a cada fase; caracterização ANTES de mexer no chat; um refactor por commit.

---

### Fase 0 — Helper de cap + política
Criar `server/pagination.py` com `clamp_limit` + constantes.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** Criado `server/pagination.py` com `clamp_limit(value, default, cap)`, `clamp_offset(value)` e constantes `PAGE_MSGS/CAP_MSGS = 50/200`, `PAGE_LIST/CAP_LIST = 50/200`, `MAX_OFFSET`.
- **Como foi feito / decisões:** Optado por módulo novo (contrato §4.3) em vez de promover `_coerce_int` — o molde de coerção é o mesmo (`int()` + clamp + default no fail), mas mantém `db/filters/spec.py` intacto e dá um ponto único fora da camada de filtros. Docstring declara a política transversal.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** Asserções do plano OK (`clamp_limit(9e6,50,200)==200`, `(None,…)==50`, `(-5,…)>=1`, `'30'→30`, `'abc'→50`, `0→1`); import sem ciclo. Suíte `tests/test_endpoints.py` **1265 passed, 0 failed** (baseline pré-F0).

---

### Fase 1 — Capar os `limit` livres
`/api/executions` e `/api/webhook-payloads` passam o `limit` por `clamp_limit`.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** `clamp_limit` aplicado em `/api/executions` ([executions.py:82](../server/routes/executions.py#L82), + `clamp_offset` no offset) e `/api/webhook-payloads` ([logs.py:60](../server/routes/logs.py#L60)).
- **Como foi feito / decisões:** Varredura `grep "limit: int" server/routes` confirmou que os demais já estão protegidos: `conversations.py:104` (`min(limit,200)`), `audit.py:51` (`min(limit,200)`), `gowa-logs`/`logs.py:79` (`min(limit,5000)`), `channel-webhook-payloads` (`min(limit,_RECENT_CAP)`). `/api/logs` lê de `deque(maxlen=500)` — teto natural, deixado como está (falso positivo do plano).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** Testes de regressão adicionados em `test_endpoints.py` (`?limit=99999` → HTTP 200 e itens ≤ 200 nos dois endpoints). Suíte **1269 passed, 0 failed**.

---

### Fase 2 — Caracterização do fluxo de chat
Fixar (testes) o comportamento atual de abrir/carregar mensagens antes de paginar.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** Seção nova em `test_endpoints.py` ("Chat: caracterização pré-paginação (plano 50 F2)"): conversa com 130 msgs, fixa (a) baseline "traz tudo" — endpoint devolve o mesmo total que `get_by_conversation` (sem teto), (b) shape `messages` na raiz do `data`, (c) ordem cronológica (ts crescente) + minhas 130 msgs em ordem, (d) `session_open` presente, (e) `mark_read=false` não altera `unread_count`.
- **Como foi feito / decisões:** Descoberta importante — criar a conversa emite 1 card `conversation_event` ('created') com ts real, então o total do repo é 131 (130 + 1). O baseline compara **endpoint == repo** (ambos sem teto) em vez de número fixo, e valida ordem só entre as msgs `msg-NNN`. Endpoint alvo confirmado: `GET /api/atendimentos/{conv_id}/messages`. Precedente achado: `contacts.py:628` **já** usa `message_repo.last_inbound_ts()` (query dedicada) — a F3 vai replicar isso em `conversations.py:283` (hoje calcula `max(ts)` da página, o risco da janela Cloud).
- **Problemas / pendências:** Nenhuma. Os 3 baselines "traz tudo" são exatamente o que a F3 vai atualizar conscientemente.
- **Verificação:** Suíte **1277 passed, 0 failed** (8 checagens novas).

---

### Fase 3 — Backend: keyset de mensagens
`get_by_conversation`/`get_all` com `limit`/`before_id` opcionais; endpoints devolvem `has_more`.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** Repo — `get_all`/`get_by_conversation` ganharam `*, limit=None, before_id=None`, com SELECT compartilhado (`_select_messages`): `limit=None` = tudo `ORDER BY ts` (byte-idêntico legado); `limit` = newest `limit` via `ts DESC, id DESC` + `id < before_id`, revertido p/ cronológico. Endpoints — `GET /api/atendimentos/{id}/messages` (`conversations.py`) e `GET /api/contacts/{phone}` (multicanal + legado, `contacts.py`) leem `limit`/`before_id`, capam via `clamp_limit(..., PAGE_MSGS, CAP_MSGS)`, over-fetch por 1 p/ `has_more` (dropa a extra mais antiga), devolvem `{messages, has_more, ...}`.
- **Como foi feito / decisões:** (1) `has_more` sem 2ª query — pede `limit+1`, se veio a mais → dropa índice 0 (a mais antiga da lista cronológica). (2) **Janela Cloud 24h**: troquei o `max(ts da página)` de `conversations.py:283` pela query dedicada `message_repo.last_inbound_ts(conversation_id=...)` — mesmo precedente já usado em `contacts.py`; a paginação não fecha mais a janela por engano (risco do plano). (3) `/api/conversations/*` continua funcionando (middleware reescreve p/ `/api/atendimentos/*`, handler único). (4) Default do endpoint agora é a página recente (mudança consciente do baseline da F2).
- **Correção de teste (keyset id↔ts):** O cursor é por `id` mas ordena por `ts`; isso exige `id` e `ts` crescerem juntos (verdade em produção). O teste F2 original usava `ts` no passado enquanto o card `created` pegava `ts=agora` → inversão artificial. Corrigido p/ `ts` realista crescente. Frontend deve usar `_id` (chave exposta por `_row_to_dict`) como valor de `before_id`.
- **Problemas / pendências:** F4 (frontend) vai consumir `has_more`/`before_id` (usar `_id` como cursor).
- **Verificação:** Testes F3 (caminhada keyset reconstrói a thread inteira sem dup/gap; conversa longa `has_more=True`; curta `has_more=False`; caminho `/api/contacts/{phone}` também pagina). Suíte **1287 passed, 0 failed**.

---

### Fase 4 — Frontend: carregar mensagens anteriores (scroll-up)

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (verificação de navegador manual pendente — ver abaixo)
- **O que foi feito:** (1) `api.js` — `getContact`/`getConversationMessages` aceitam `opts={limit, beforeId}` (retrocompatível). (2) `shapeConvData` repassa `has_more`. (3) `useConversationSelection` — novo `loadOlder()` (cursor = menor `_id` carregado; fetch SEM re-marcar lida; **prepend** com dedup por `_id`; guarda `loadingOlderRef` contra concorrência) + `loadingOlder` state, expostos no return. (4) `ContactDetail` — sentinela `IntersectionObserver` no topo (+ botão fallback "Carregar mensagens anteriores"), **âncora de scroll** (`prependingRef`+`anchorRef`: guarda `scrollHeight/scrollTop` antes do prepend, soma o delta depois → viewport não salta), e o auto-scroll-pro-fim só roda quando NÃO é prepend. (5) `Contacts.js` passa `loadOlder/loadingOlder/hasMore=contactData.has_more`.
- **Como foi feito / decisões:** Cursor é o `_id` (chave exposta por `_row_to_dict`; o `before_id` do backend compara com `messages.id`). Sandbox não passa os novos props → defaults (`loadOlder=null`, `hasMore=false`) desativam o sentinela; sandbox segue intacto (D3/plano). Fallback de botão além do observer (acessibilidade + ambientes sem IO).
- **Problemas / pendências:** **Verificação de navegador não executada** — o ambiente é pasta compartilhada e há regra de não subir/reiniciar o servido :8090 sem confirmar ([[project-whatsbot-pro]]). Recomendado ao operador: abrir conversa >200 msgs (carrega 50, ancorada no fim), rolar ao topo (carrega +50 sem salto), chegar ao início (para de carregar), receber msg nova (auto-scroll pro fim ainda funciona).
- **Verificação:** `node --check` nos 5 arquivos JS OK; `node --test conversationRows.test.js` **47 pass** (inclui `has_more` no `shapeConvData`); suíte backend **1287 passed** (sem regressão).

---

### Fase 5 — `/api/contacts` com limit/offset

#### Status de execução — Fase 5
**Estado:** ✅ Concluída
- **O que foi feito:** `contact_search.build_count_contacts_query` (COUNT barato só do WHERE archived+inbox). `contact_repo`: extraídos `_shape_contact_row` + `_apply_q_filter`; novo `list_contacts_page(q, archived, inbox_ids, *, limit=None, offset=0) → {items, total, has_more}`; `list_contacts` legado agora delega (`["items"]`, byte-idêntico). Endpoint `GET /api/contacts` pagina **só quando `limit` é passado** (envelope), senão mantém a lista legada. `api.js getContacts(q, archived, {limit, offset})` retrocompatível.
- **Como foi feito / decisões:** A busca `q` filtra em Python **pós-SELECT**, então: SEM `q` pagina no SQL (`LIMIT/OFFSET` + COUNT); COM `q` carrega tudo, filtra e fatia a página em Python (`total` = tamanho do filtrado). Retrocompat crítico (plano: "sidebar/`/contacts` ainda montam até F7/F8") garantido por só envelopar quando `limit` existe — os callers atuais (`server/background.py` varredura de avatares; sidebar; `/contacts`) não passam `limit` e recebem o shape antigo.
- **Problemas / pendências:** F7/F8 vão migrar os callers para passar `{limit, offset}` e consumir o envelope. Nota P1 do plano resolvida por aditividade (não trocar o shape default agora).
- **Verificação:** Testes F5 (envelope; caminhar páginas cobre o universo sem dup; `limit=99999` ≤ 200; busca paginada; sem `limit` = lista legada). Suíte **1297 passed, 0 failed**; `node --check api.js` OK.

---

### Fase 6 — Cap do full-scan de busca

#### Status de execução — Fase 6
**Estado:** ✅ Concluída
- **O que foi feito:** `contact_ids_matching_message` (`contact_search.py`) — `.limit(MESSAGE_SCAN_CAP=5000)` sobre o `order_by(ts.desc())` (varre só as 5000 msgs mais recentes, não a tabela inteira) + guarda `len(folded_q) >= MIN_SCAN_QUERY_LEN(2)` (busca de 1 char não aciona o scan).
- **Como foi feito / decisões:** Bound simples (plano P2 opção a), não FTS — trade-off documentado no código: um match que só exista em mensagens muito antigas (além das 5000 recentes) pode escapar; nome/telefone/tag **não** usam o scan (casam sempre, inclusive 1 char). FTS/tsvector fica como caminho futuro (P2 opção b).
- **Problemas / pendências:** Nenhuma. (Observado ruído de teardown do harness contra o Postgres compartilhado — não afeta o resultado; `RESULTS` estável.)
- **Verificação:** Testes F6 (1 char não casa por conteúdo; match 2+ chars segue funcionando — regressão coberta; `contact_ids_matching_message('a')=={}`). Suíte **1301 passed, 0 failed**.

---

### Fase 7 — Tela `/contacts` server-side

#### Status de execução — Fase 7
**Estado:** ⬜ Não iniciada

---

### Fase 8 — Sidebar com scroll infinito

#### Status de execução — Fase 8
**Estado:** ⬜ Não iniciada

---

### Fase 9 — Usage por contato com teto

#### Status de execução — Fase 9
**Estado:** ✅ Concluída
- **O que foi feito:** `usage_repo.by_contact`/`detail` ganharam `*, limit=None, offset=0` (aditivo); novos `count_by_contact`/`count_detail` p/ o `total`. Endpoints `/api/usage/by-contact` e `/api/usage/contact/{phone}` envelopam `{items, total, has_more}` **só com `limit`** (senão lista legada). `by_contact` já ordena por custo desc = top-N gastadores.
- **Como foi feito / decisões:** No `by_contact` paginado, o agregado `by_type` é **restringido aos contatos da página** (`WHERE contact_id IN page_ids`, ou `false()` p/ página vazia) — evita reagregar a base inteira. `summary`/`global_summary` (agregados de 1 linha) ficam intactos (não paginados — falso positivo do plano). Retrocompat pelo mesmo padrão da F5 (só envelopa com `limit`).
- **Problemas / pendências:** F10 (CostsDashboard) consome esse envelope — fica no checkpoint de frontend.
- **Verificação:** Testes F9 (envelope; página respeita limit; caminhar cobre o total; `by_type` presente na página; detail paginado; sem `limit` = lista; summary intacto). Suíte **1312 passed, 0 failed**.

---

### Fase 10 — CostsDashboard paginação

#### Status de execução — Fase 10
**Estado:** ⬜ Não iniciada

---

### Fase 11 — Export sem N+1 + streaming

#### Status de execução — Fase 11
**Estado:** ⬜ Não iniciada

---

### Fase 12 — Caps admin/config (defesa em profundidade)

#### Status de execução — Fase 12
**Estado:** ⬜ Não iniciada

---

### Fase 13 — Batch para os fan-outs

#### Status de execução — Fase 13
**Estado:** ⬜ Não iniciada
