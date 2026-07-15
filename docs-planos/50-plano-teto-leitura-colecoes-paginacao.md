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
**Estado:** ⬜ Não iniciada

---

### Fase 3 — Backend: keyset de mensagens
`get_by_conversation`/`get_all` com `limit`/`before_id` opcionais; endpoints devolvem `has_more`.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada

---

### Fase 4 — Frontend: carregar mensagens anteriores (scroll-up)

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada

---

### Fase 5 — `/api/contacts` com limit/offset

#### Status de execução — Fase 5
**Estado:** ⬜ Não iniciada

---

### Fase 6 — Cap do full-scan de busca

#### Status de execução — Fase 6
**Estado:** ⬜ Não iniciada

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
**Estado:** ⬜ Não iniciada

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
