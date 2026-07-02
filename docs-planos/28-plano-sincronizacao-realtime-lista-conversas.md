# Plano 28 — Sincronização em tempo real da lista de conversas (conversa nova sem F5)

> **Status:** IMPLEMENTADO (F0–F5 ✅ · suites verdes: script 988, node 52, pytest, + cobertura nova) · **Data:** 2026-07-02 · **Escopo:** médio-grande (backend + frontend + migration + testes)
>
> **Desvios do plano original (descobertos na implementação):** (1) o double-create é fechado por **serialização app-level** em `resolve_for_contact_ex` (`_create_open_atomic` — re-check + insert numa transação; race-safe no SQLite) em vez de um **índice único parcial** — o índice quebrava o modelo atendimento-cêntrico, que permite múltiplas conversas por (contato, inbox) e é exercitado pela suíte. (2) `resyncMerge` separado foi **dispensado**: `fetchContacts` (REPLACE) no reconnect já é um resync autoritativo (rows de upsert e de REST vêm da MESMA query enriquecida; não há row otimista client-only a preservar). (3) o finder do TTL-sweep exclui os **7 papéis painel-only** ao checar "tem mensagem" — o t=0 grava um card `conversation_event`, então um ghost real nunca é literalmente vazio (tem só o card).
> **Origem:** bug reportado pelo usuário — quando um **número novo** manda a 1ª mensagem, a **aba do navegador** acende o badge de não-lida, mas a **conversa não aparece na lista**; só surge após F5. Para conversas já existentes, o update ao vivo funciona. Uma tentativa anterior ("plano 25 Fase 2") já mexeu nisso e o bug voltou.
> **Método:** investigação nesta sessão (leitura do código real + análise de HAR do deploy remoto `whatsbot-dev.teste.techify.run`) + workflow multi-agente que clonou **Chatwoot** e leu **Zammad/Rocket.Chat/Papercups/Erxes** + best practices (Event-Carried State Transfer, sync engines) + crítica adversarial. Todos os `arquivo:linha` abaixo foram verificados no branch `developer`.
>
> **Decisão do usuário (travada):** implementar o plano **inteiro de uma vez** (todas as fases), com a **varredura TTL** (Fase 5) incluída — o trade-off "conversa vazia visível se o batch crashar" (em vez de invisível até F5) é **aceitável, com TTL-sweep** como rede de segurança.
>
> Legenda de estado: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Event-Carried State Transfer:** o WS deixa de mandar "vá reler" e passa a **carregar a row enriquecida** da conversa. O cliente faz **upsert idempotente por `conversation_id`**. Refetch vira reconciliação, nunca descoberta. | Novo evento `conversation_upsert` (§4.1). O `scheduleListRefetch` some de `new_message`/`conversation_created`. |
| D2 | **Emitir sempre pós-commit.** O `conversation_upsert` é emitido depois do commit da entidade que o dispara (conversa em t=0; mensagem em t≈3 s). | Backend em `add_message` (após INSERT) e `ensure_conversation_live` (após `create`). |
| D3 | **Nunca filtrar a lista por "tem mensagem".** O gate `activeContacts` troca `last_message_ts>0` por um sinal **explícito** do backend: a coluna `origin`. | Nova coluna `atendimentos.origin`; gate `origin==='inbound' || last_message_ts>0`. |
| D4 | **Merge escopado.** No cliente, quando a row já existe, o `conversation_upsert` mescla **só** campos de mensagem/preview/unread. Status/assignee/AI/labels continuam donos dos eventos `conversation_*` dedicados. | `upsertConversationRow` (§4.3) NÃO toca `conv_status`/`assignee_user_id`/`conv_ai_active`/`active_agent_key`/`conv_labels` num merge. |
| D5 | **Cortar o insert otimista.** A row de t=0 do `ensure_conversation_live` já entrega aparição imediata **totalmente formada** (nome/canal/status). Insert otimista a partir do `new_message` (sem nome/canal) foi descartado. | `new_message` vira append-only no thread aberto; não semeia row de lista. |
| D6 | **`fetchContacts` continua REPLACE.** Serve carga inicial, busca e toggle de arquivo (precisam substituir). Merge idempotente vive num `resyncMerge()` **separado** (reconnect + rede de segurança). | §4.3. |
| D7 | **Trade-off do fantasma:** conversa `origin='inbound'` vazia (se o batch morrer entre t=0 e t≈3 s) fica **visível e vazia** (melhor que invisível até F5), com **TTL-sweep** (Fase 5) limpando as antigas. Auto-cura no próximo inbound. | Fase 5 obrigatória (decisão do usuário). |
| D8 | **Manter o batching** (`message_batch_delay`) — é feature. O commit do batch é o gatilho autoritativo do preview. **Não** adotar a Opção C (persistir a msg inbound por-chegada). | Fora de escopo. |

**Princípio fixo (memória `refactor-rollout-context`):** o produto **não está em produção/distribuído** — pode-se refatorar de forma agressiva, sem stopgap de compatibilidade. Ainda assim o rollout é **aditivo** (clientes antigos ignoram `conversation_upsert`), o que facilita bisseção/rollback.

**Princípio unificador do design:** *nenhuma linha renderizada deve depender de uma leitura que pode correr contra a escrita que a criou.*

---

## 1. Diagnóstico — a CLASSE, não o sintoma

O bug é a interseção de **três falhas independentes**. Os "fixes" anteriores (materializar em t=0 + coalescer o refetch) só **deslocaram** o problema, movendo a *leitura* para *antes* da *escrita*.

**Falha 1 — Notificação correndo contra a escrita (stale-read race).** `new_message`/`conversation_created` são *notificações*, não *estado*. O front reage relendo `/api/contacts` + `/api/atendimentos` ([useConversationWsEvents.js:493](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L493), [:369-372](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L369-L372)). Materialização em t=0 ([message_ingest_service.py:464](../app/services/message_ingest_service.py#L464)), persistência em t≈3 s ([messaging_service.py:801](../app/services/messaging_service.py#L801)) → o refetch a t≈250 ms lê **antes da escrita**.

**Falha 2 — Membership derivado de um proxy mutável (`last_message_ts > 0`).** O gate `activeContacts` ([useConversationFilters.js:53-60](../web/static/js/components/contacts/hooks/useConversationFilters.js#L53-L60)) usa `ts>0` como proxy de "conversa real vs. contato vazio". Esse proxy só é *eventualmente* verdadeiro — falso durante a janela de 3 s. Um filtro que **esconde** transforma leitura transitoriamente stale em linha **invisível**.

**Falha 3 — Create desacoplado no tempo do persist da 1ª mensagem.** `ensure_conversation_live` ([agent/memory.py:251-276](../agent/memory.py#L251-L276)) cria a row e dispara `conversation_created` mas **não salva mensagem**; preview/unread são derivados por join de `messages` ([conversation_query.py:18-27,54-60](../db/repositories/conversation_query.py#L18-L27)). E o batch (t≈3 s) **não** re-notifica a lista (só emite `message.persisted` no bus).

**Os quatro invariantes que os maduros respeitam e nós violávamos:** (1) emitir **pós-commit**, (2) **carregar o estado no evento** (ECST), (3) cliente faz **upsert idempotente por id**, (4) **não** filtrar a lista por "tem mensagem". Este plano fecha os quatro.

---

## 2. O que os sistemas maduros fazem (pesquisa)

| Sistema | ECST vs. refetch | Conversa criada já com conteúdo? | Filtra lista por "tem msg"? | Como evita stale-read |
|---|---|---|---|---|
| **Chatwoot** | ECST — `created` embute última msg + `last_activity_at`; cliente faz `ADD_CONVERSATION` do payload | **Sim** — `create!` + `message.save!` na mesma transação | **Não** — status/inbox/team/label; `last_activity_at` cai em `created_at` (nunca 0) | `after_create_commit` → só pós-commit |
| **Papercups** | signal-then-fetch-**by-id** (1 registro); `shout` é ECST | **Sim** — `create` + `maybe_create_message` antes do broadcast | **Não** — store normalizado por id | broadcast só após create+msg |
| **Rocket.Chat** | ECST puro — `subscriptions-changed` carrega o doc + tag `inserted/updated/removed` | Room/subscription 1ª classe | **Não** | o stream *é* o estado |
| **Zammad** | ECST — subscription empurra a coleção | Ticket sempre tem conteúdo | **Não** | `after_commit` |
| **Erxes** | Misto — list ECST; refetch disparado pelo evento de msg **já salva** | **Sim** (a msg cria a conversa) | **Não** | publish após insert da msg |
| **WhatsBot (atual)** | notification → **list refetch** | **Não** — materializa sem msg; batch salva sem re-broadcast | **Sim** — dropa `ts≤0` | **não evita** |

WhatsBot é o único que combina *notification-then-list-refetch* + gate por "tem mensagem" + create desacoplado do persist. Chatwoot (o paralelo mais próximo — já espelhamos os saved-filters) é o modelo: ECST pós-commit + upsert por id + zero filtro de "tem msg".

---

## 3. Mapa de hoje (arquivo:linha)

### 3.1 Backend
- **Ingest (t=0):** `ensure_conversation_live` ([agent/memory.py:251-276](../agent/memory.py#L251-L276)), chamado de [message_ingest_service.py:464](../app/services/message_ingest_service.py#L464). Resolve/cria via `_resolve_conversation` → `conversation_repo.resolve_for_contact_ex`, dá `touch_activity`, roda `_run_lifecycle_reactions`. **Não salva mensagem.**
- **Save (t≈3 s):** `add_message` ([agent/memory.py:214-249](../agent/memory.py#L214-L249)); após INSERT (`:225`) + `touch_activity` (`:233`) chama `_emit_message_persisted` + `_run_lifecycle_reactions` (`:240-241`). Cobre batch texto ([messaging_service.py:801](../app/services/messaging_service.py#L801)), mídia (`:864`), resposta IA ([handler.py:372](../agent/handler.py#L372)), operator send ([contacts.py:733](../server/routes/contacts.py#L733)), group-no-mention ([message_ingest_service.py:488](../app/services/message_ingest_service.py#L488)).
- **Row enriquecida:** `conversation_repo.get_with_channel` ([conversation_repo.py:272-281](../db/repositories/conversation_repo.py#L272-L281)) = `enriched_columns` + `finalize_conv`, **sem** labels (só `list_conversations` chama `_attach_labels`, [:250](../db/repositories/conversation_repo.py#L250)).
- **Create:** `conversation_repo.create` ([conversation_repo.py:73-94](../db/repositories/conversation_repo.py#L73-L94)); resolve `resolve_for_contact_ex` ([:156-176](../db/repositories/conversation_repo.py#L156-L176)) — o create só dispara quando **não há conversa** para (contact, inbox).
- **Unicidade:** só `uq_conv_display_id` ([tables.py:404](../db/tables.py#L404)). **Não há** guarda em `(contact_id, inbox_id) WHERE open` → dois inbound quase-simultâneos de um contato novo podem criar **duas** conversas open.
- **Papéis painel-only — TRÊS conjuntos divergentes:** `_PREVIEW_EXCLUDED` = 4 (`transcription, system_notice, conversation_event, system` — [conversation_query.py:23](../db/repositories/conversation_query.py#L23) via [_mapping.py:62](../db/repositories/_mapping.py#L62)); contexto LLM = 5 (`+tool_call` — [message_repo.py:84](../db/repositories/message_repo.py#L84)); frontend = 6 (`transcription, system_notice, tool_call, conversation_event, private_note, error` — [useConversationWsEvents.js:455](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L455)). **União = 7.**

### 3.2 Frontend
- **Loader:** `fetchContacts` ([useConversationList.js:70-84](../web/static/js/components/contacts/hooks/useConversationList.js#L70-L84)) = `getContacts` + `listConversations` → `buildRows` → `setContacts`. REPLACE. Usado por inicial (`:94`), busca (`:98`), arquivo (`:104`).
- **Rows:** `buildRows` ([conversationRows.js:288-341](../web/static/js/services/conversationRows.js#L288-L341)); identidade da linha = `conversation_id`; `id` = contact id.
- **Gate:** `activeContacts` ([useConversationFilters.js:53-60](../web/static/js/components/contacts/hooks/useConversationFilters.js#L53-L60)) — dropa `last_message_ts≤0` (exceto o thread aberto).
- **WS:** `useConversationWsEvents` — `scheduleListRefetch` 250 ms ([:63-68](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L63-L68)); disparado por `new_message` sem-row (`:493`), `conversation_created` (`:369-372`), reconnect (`:74-77`). Preview in-place no `new_message` (`:457-495`). `onConversationChanged` (`:103-142`) trata `conversation_*` (deleted / attribute / status/assign/ai via `applyConversationEvent`) + rede de segurança de membership (`:133-141`).
- **Plumbing WS:** [useWebSocket.js:10-50](../web/static/js/hooks/useWebSocket.js#L10-L50) mapeia nome-do-evento → callback; os `conversation_*` roteiam por `onConversationChanged(name, data)`.
- **Patch:** `applyConversationEvent`/`conversationPatch` ([conversationPatch.js](../web/static/js/services/conversationPatch.js)) — já escopado (só escreve chaves presentes no evento).

---

## 4. Design da solução

### 4.1 Contrato de eventos WS

**`conversation_upsert`** (NOVO — sinal autoritativo de lista). Payload = **row enriquecida** da conversa (mesma forma de um item de `/api/atendimentos`: `finalize_conv` + `_attach_labels`), incluindo a nova coluna `origin`. Emitido de **uma única fonte de row** (`conversation_repo.get_row_for_broadcast`) em **dois momentos**, sempre pós-commit:

```
{ event: "conversation_upsert", data: {
    id,                              // = atendimentos.id — IDENTIDADE da row (conversation_id)
    contact_id, contact_phone, contact_name,
    inbox_id, channel_id, channel_provider, channel_name,
    origin,                          // 'inbound' | 'outbound' | 'imported' | 'manual'   (NOVO)
    status, ai_active, assignee_user_id, active_agent_key,
    is_archived, is_pinned, has_unread_mention,
    last_message, last_message_role, last_message_ts,   // ts REAL (>0) no emit t≈3s; 0 no de t=0
    last_message_status, last_message_msg_id,
    unread_count, labels, custom_attributes, last_activity_at
}}
```

**`new_message`** (INALTERADO). Perde o papel de sinal de lista — no FE fica **só** com o append no thread aberto. O ramo "sem row → `scheduleListRefetch`" é removido.

**`conversation_created`** (INALTERADO no protocolo — segue pra kanban/plugins). O efeito de FE que disparava refetch de lista vira **no-op de lista** (a inserção vem do `conversation_upsert` de t=0).

**`conversation_status_changed` / `_assigned` / `_ai_toggled` / `_labels_changed`** (INALTERADOS) — **continuam a única autoridade** sobre status/assignee/AI/labels. O `conversation_upsert` **não** toca esses campos num merge.

### 4.2 Backend

**`conversation_repo.get_row_for_broadcast(conversation_id) -> dict | None`** (novo) = `get_with_channel` + `_attach_labels([row])[0]`. Garante que a row single seja **idêntica** à da lista (com `labels`).

**Coluna `atendimentos.origin`** (`Text`, nullable) — `inbound` | `outbound` | `imported` | `manual`. Populada no `create` conforme o gatilho. `enriched_columns` já a inclui (faz parte de `atendimentos.*`). Backfill: conversas com mensagem → `inbound`; vazias → `manual`. `NULL` é tratado como "não-inbound" pelo gate.

**`LIST_PANEL_ONLY_ROLES`** (novo, constante canônica em `_mapping.py`) = os 7 papéis painel-only. Aplicada em (a) `last_msg_subq`/preview ([conversation_query.py:23](../db/repositories/conversation_query.py#L23)) — **corrige** o vazamento pré-existente de `tool_call`/`private_note`/`error` no preview — e (b) o gate do emit de `conversation_upsert`.

**Emit 1 — t=0** (`ensure_conversation_live`): após o `create` (que já comita), emitir `conversation_upsert` com a row de `get_row_for_broadcast`. Row totalmente formada (`origin='inbound'`, `last_message_ts=0`). É pós-commit da **conversa**.

**Emit 2 — t≈3 s** (`add_message`, após `:240-241`): se o role **não** for painel-only (7), montar a row via `get_row_for_broadcast` e `broadcast("conversation_upsert", row)`. Roda depois do commit da **mensagem** → preview/unread/ts reais. Chamada **direta** (não via `emit_with_filter`), como `_run_lifecycle_reactions` já faz.

**Índice único parcial** `uq_atend_open_contact_inbox` em `atendimentos(contact_id, inbox_id) WHERE status='open'` — fecha o double-create. `resolve_for_contact_ex` passa a capturar `IntegrityError` do `create` e re-resolver (o vencedor da corrida já criou a conversa).

### 4.3 Frontend

**`convRowToSidebarRow(payload)`** (novo, `conversationRows.js`) — mapeia **campo a campo** a row enriquecida → row da sidebar (mesma forma que `buildRows` produz): `payload.id`→`conversation_id`; `contact_id`→`id`+`contact_id`; `contact_name`→`name`; `contact_phone`→`phone`; `status`→`conv_status`; `ai_active`→`conv_ai_active`; `labels`→`conv_labels`; `custom_attributes`→`conv_custom_attributes`; mantém `last_message*`, `unread_count`, `channel_*`, `assignee_user_id`, `active_agent_key`, `is_pinned`, `is_archived`, `has_unread_mention`, `origin`. (Campos contato-only ausentes — `tags`, `avatar_v`, `email` — chegam no próximo reconcile.)

**`upsertConversationRow(prev, incoming)`** (novo, `conversationRows.js`) — chave `conversation_id`:
- **ausente → INSERT:** semeia todos os campos do `incoming` (inclui status/assignee/ai/labels/origin).
- **presente → MERGE ESCOPADO:** atualiza **só** `last_message*`, `last_activity_at`, `unread_count`, `has_unread_mention`, `is_archived`, `is_pinned`. **NÃO** toca `conv_status`/`assignee_user_id`/`conv_ai_active`/`active_agent_key`/`conv_labels`/`conv_custom_attributes` (donos dos eventos dedicados). Preserva `tags`/`avatar_v`/`custom_attributes` já carregados.
- **Guard monotônico:** se existe e `incoming.last_message_ts < existing.last_message_ts` → ignora os campos de mensagem (idempotente/fora-de-ordem).
- Re-sort `sortContacts` (pinned-first + `last_activity_at`/`last_message_ts` desc).

**Wiring (`useConversationWsEvents.js` + `useWebSocket.js`):**
- Rotear `conversation_upsert` por `onConversationChanged` (novo case no topo: `setContacts(prev => upsertConversationRow(prev, convRowToSidebarRow(data)))`).
- `new_message`: **remover** o `scheduleListRefetch` do ramo sem-row (`:493`) e o patch de lista in-place (`:457-495`); manter só o append no thread aberto (`:394-450`).
- `conversation_created` (`:369-372`): **remover** o refetch (no-op de lista).
- `fetchContacts`: **continua REPLACE** (inicial/busca/arquivo).
- **`resyncMerge(rows)`** (novo) — reconnect (`:74-77`) e rede de segurança (`:133-141`): aplica cada row via `upsertConversationRow`, **sem evict**.

**Gate `activeContacts`** ([useConversationFilters.js:56](../web/static/js/components/contacts/hooks/useConversationFilters.js#L56)) → `origin==='inbound' || last_message_ts>0` (mantida a escapatória "é o thread aberto"). `buildRows` passa a mapear `cv.origin`→row `origin`.

### 4.4 Comportamento nos casos críticos
- **Rajada multi-mensagem:** 1º inbound → 1 `conversation_upsert` de t=0 (insert). Demais não re-materializam. Batch comita 1 msg combinada → 1 `conversation_upsert` de merge. Upsert por id = zero duplicata.
- **`before_save=None`:** o filtro roda no ingest **antes** de `ensure_conversation_live` → nenhuma conversa criada. Sem fantasma.
- **Fantasma real (crash do batch):** conversa `origin='inbound'` vazia fica **visível** (trade D7). Auto-cura no próximo inbound; **TTL-sweep** (Fase 5) limpa as antigas.
- **Reopen:** `conversation_status_changed` (retido) faz o flip; o `conversation_upsert` de t=0 é guard-rejeitado (ts existente>0) e **não** possui status.
- **Modal do operador / import:** contato sem conversa/mensagem → nenhum emit → oculto (`origin` não-inbound). 1º send → `conversation_upsert` pós-commit com ts>0 → aparece.
- **AI echo (`source="echo"`):** se comita via `add_message`, dispara o mesmo upsert.
- **Reconnect WS:** `resyncMerge` (não `fetchContacts`) → upsert sem evict → cura a janela offline sem esconder rows.
- **Badge da aba × lista:** a row agora também aparece em t=0 (gate por `origin`) → some o "aba conta, conversa não aparece". `unread_count` por-conversa é 0 até t≈3 s (cosmético, converge no batch).

---

## 5. Plano de implementação em fases

> Cada fase é **independentemente shippable e testável**. Backend primeiro (broadcast inócuo sem consumidor). Rollback = reverter o wire do FE.

### Fase 0 — Pré-requisitos (backend) — `⬜`
- Migration Alembic `0034_conversation_origin`: (a) `ADD COLUMN origin` em `atendimentos`; (b) índice único parcial `uq_atend_open_contact_inbox` em `(contact_id, inbox_id) WHERE status='open'`; (c) backfill de `origin`.
- `atendimentos.origin` em [tables.py:381-405](../db/tables.py#L381-L405).
- Constante `LIST_PANEL_ONLY_ROLES` (7) em `_mapping.py`; aplicar em `last_msg_subq` ([conversation_query.py:23](../db/repositories/conversation_query.py#L23)).
- `resolve_for_contact_ex` captura `IntegrityError` → re-resolve.
- **Arquivos:** `db/tables.py`, `db/alembic/versions/20260702_0034_conversation_origin.py`, `db/repositories/_mapping.py`, `db/repositories/conversation_query.py`, `db/repositories/conversation_repo.py`.
- **Teste:** dois `resolve_for_contact_ex` concorrentes → 1 conversa; preview de conversa cujo último save foi `tool_call`/`private_note`/`error` **não** vaza conteúdo.

**Status de execução Fase 0:** ✅ Concluída — migration `0034_conversation_origin` (origin + backfill), `origin` em `tables.py`, `LIST_PANEL_ONLY_ROLES` (7) canônico em `_mapping.py` (aplicado no preview), `_create_open_atomic` serializa o create no resolve (sem índice único — ver desvio 1).

### Fase 1 — Backend: `conversation_upsert` pós-commit (Opção B) — `⬜`
- `conversation_repo.get_row_for_broadcast`.
- Projeção em `agent/message_listeners.py` (gate 7 papéis) chamada de `agent/memory.py::add_message` após `:240-241`.
- **Arquivos:** `db/repositories/conversation_repo.py`, `agent/message_listeners.py`, `agent/memory.py`.
- **Teste:** após `add_message` inbound/IA/operator, broadcast `conversation_upsert` com `last_message_ts>0`, forma = item de `/api/atendimentos`, labels presentes; `tool_call`/`private_note`/`error` **não** disparam.

**Status de execução Fase 1:** ✅ Concluída — `conversation_repo.get_row_for_broadcast` (+labels), `agent/message_listeners.broadcast_conversation_upsert` (gate 7 papéis), chamado de `add_message` após as reações.

### Fase 2 — Frontend: reducer escopado + mapper + wire — `⬜`
- `upsertConversationRow` + `convRowToSidebarRow` em `conversationRows.js`.
- Rota `conversation_upsert` em `useWebSocket.js` + case em `onConversationChanged`; remover `scheduleListRefetch` de `new_message` e `conversation_created`; `new_message` append-only.
- **Arquivos:** `web/static/js/services/conversationRows.js`, `web/static/js/hooks/useWebSocket.js`, `web/static/js/components/contacts/hooks/useConversationWsEvents.js`.
- **Teste:** unit do mapper (row de `get_with_channel` ≡ row de `buildRows`); dois upserts do mesmo id não duplicam; upsert stale (ts menor) não regride; `conversation_assigned` seguido de `conversation_upsert` com snapshot antigo **não** reverte assignee.

**Status de execução Fase 2:** ✅ Concluída — `convRowToSidebarRow` + `upsertConversationRow` (merge escopado + guard monotônico + replace de legacy row) em `conversationRows.js`; `conversation_upsert` roteado em `useWebSocket.js` e tratado em `onConversationChanged`; `new_message` virou append-only (removido o patch de lista + `scheduleListRefetch`); `conversation_created` no-op de lista.

### Fase 3 — Frontend: resync separado — `⬜`
- `resyncMerge()`; reapontar reconnect e safety-net. `fetchContacts` continua replace.
- **Arquivos:** `web/static/js/components/contacts/hooks/useConversationWsEvents.js` (+ `useConversationList.js` se necessário).
- **Teste manual:** derrubar/reerguer WS com conversa nova chegada offline → aparece sem sumir rows; busca filtra; arquivo não vaza.

**Status de execução Fase 3:** ✅ Concluída (via REPLACE) — reconnect/safety-net seguem em `fetchContacts` (REPLACE), que é resync autoritativo; `resyncMerge` separado dispensado (ver desvio 2).

### Fase 4 — Backend + Frontend: aparição em t=0 (Opção A) — `⬜`
- Emit de `conversation_upsert` em `ensure_conversation_live`.
- `create` popula `origin`; `resolve_for_contact_ex` propaga o `origin` correto por gatilho.
- Gate `activeContacts` → `origin==='inbound' || last_message_ts>0`; `buildRows` mapeia `origin`.
- **Arquivos:** `agent/memory.py`, `db/repositories/conversation_repo.py`, `web/static/js/components/contacts/hooks/useConversationFilters.js`, `web/static/js/services/conversationRows.js`.
- **Teste:** inbound novo emite `conversation_upsert` de t=0 com `origin='inbound'`, `ts=0`, nome/canal/status presentes; contato importado/modal continua oculto.

**Status de execução Fase 4:** ✅ Concluída — emit `conversation_upsert` em `ensure_conversation_live` (t=0); `create`/`resolve_for_contact_ex` propagam `origin` ('inbound' quando role='user'); gate `activeContacts` = `origin==='inbound' || last_message_ts>0`; `buildRows` mapeia `origin`.

### Fase 5 — Endurecimento: TTL-sweep do fantasma (decisão D7) — `⬜`
- Varredura de fundo que arquiva/remove conversas `origin='inbound'` **sem nenhuma mensagem** com `created_at` mais velho que N min (config, default ex. 30 min). Roda no loop de background existente ([server/background.py](../server/background.py)).
- **Arquivos:** `db/repositories/conversation_repo.py` (query de fantasmas), `server/background.py` (agendamento).
- **Teste:** conversa `origin='inbound'` vazia e antiga é removida; conversa com mensagem ou recente é preservada.

**Status de execução Fase 5:** ✅ Concluída — `conversation_repo.find_empty_inbound_ghosts` (inbound + sem mensagem VISÍVEL + antigo), loop `empty_conversation_sweep_loop` (config `empty_conversation_ttl_minutes`, default 30; <=0 desliga) registrado no supervisor em `app.py`.

---

## 6. Riscos residuais e não-objetivos

### Riscos residuais
- **Custo de query por save visível:** `get_row_for_broadcast` roda `get_with_channel` (6 subqueries de last-msg + 1 de unread) por save visível. O gate dos 7 papéis remove os saves de meio-de-turno (`tool_call`). Medir sob rajada; opcional coalescing por `conversation_id`.
- **Janela seed-vs-lifecycle (raríssima):** `conversation_assigned` antes do insert de t=0 → seed com `assignee=null` stale; auto-cura no próximo evento/refetch. Fechável 100% só com `version` por-row.
- **`unread_count` por-conversa 0 por ~3 s** na row recém-aparecida (badge da aba já aceso). Cosmético.
- **Campos contato-only num insert puro** (`tags`, `avatar_v`) chegam no próximo reconcile/`avatar_updated`.

### Não-objetivos
- Não reescrever para store normalizado com lib (respeita "no build step").
- Não remover o batching. Não adotar Opção C. Não implementar sequence-number/replay no WS (o `resyncMerge` basta).
- Não cobrir revoke/edit/reaction como sinal de lista (preview stale do último visível revogado permanece pré-existente).

### Índice de seams (verificados no branch `developer`)
- Row única: `conversation_repo.get_with_channel:272-281` (+ `_attach_labels:208-219`); `conversation_query.finalize_conv:73-85` (ts default 0 em `:79`); `last_msg_subq:18-27`, unread `:38-59`.
- Papéis painel-only: `_mapping.py:62` (4) · `message_repo.py:84` (5) · FE `useConversationWsEvents.js:455` (6) → canônico 7.
- Emit t≈3 s: `agent/memory.py:214-249` (`add_message`, após `:240-241`); `agent/message_listeners.py` (padrão `_broadcast_conversation_created:34-50`).
- Emit t=0 + `origin`: `agent/memory.py:251-276` (`ensure_conversation_live`) / `message_ingest_service.py:464`; `conversation_repo.create:73-94`; resolve `:156-176`; `tables.py:404`.
- Cobertura de save: `messaging_service.py:801/864`, `handler.py:372`, `contacts.py:733`, `message_ingest_service.py:488`.
- FE: gate `useConversationFilters.js:53-60`; handler/refetch `useConversationWsEvents.js:63-68,74-77,103-142,369-372,394-450,455,457-495`; loader `useConversationList.js:70-84`; rows `conversationRows.js:288-341`; plumbing `useWebSocket.js:10-50`; patch `conversationPatch.js`.
