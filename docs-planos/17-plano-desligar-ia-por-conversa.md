# Plano de Implementação — 17: Desligar IA **por conversa** e desatribuir (fila "Não atribuídas")

> Hoje o botão **"Desativar IA"** do menu de contexto (clique-direito na sidebar) age **por contato/telefone**:
> grava só `contacts.ai_enabled = 0` e **não toca na conversa**. Dois problemas: (1) desliga a IA para
> **todas** as conversas daquele número (um contato pode ter N conversas/canais); (2) a conversa **continua
> atribuída à IA** (`active_agent_key` não-nulo) — então **não cai** na fila "Não atribuídas" para um humano
> assumir.
>
> **Decisão do produto (travada):** o toggle passa a ser **100% por conversa**; o conceito de "chave-mestra
> por contato" (`contacts.ai_enabled`) é **removido** da UI e do gate de resposta. Ao **desligar** a IA numa
> conversa: ela é **desatribuída** (limpa `active_agent_key` e `assignee_user_id`), `ai_active=0`, e cai na
> fila "Não atribuídas". Ao **religar**: `ai_active=1` e a conversa volta a ser atribuída ao agente default.
>
> **Escopo:** (1) endpoint de conversa que faz a transição atômica (espelha `set_status('closed')`);
> (2) `ensure_ai_agent` ganha guard de `ai_active` (não reatribuir à IA uma conversa pausada na próxima
> mensagem); (3) gate de resposta deixa de checar `contacts.ai_enabled`; (4) frontend — clique-direito e ação
> em massa passam a operar por `conversation_id`, badge "IA OFF" lê `ai_active` da linha.
>
> **Fora de escopo:** **dropar a coluna** `contacts.ai_enabled` (vira deprecated; remoção física fica para
> migration futura — ver §6); precisão multi-canal do gate de leitura (`_conversation_ai_active` usa
> `get_open_for_contact`, per-contato — documentado em §6); redesenho da sidebar.

---

## 0. Estado atual VERIFICADO (2026-06-22, branch `developer`)

> Re-ancorar por `grep` (nome de função/rota/evento) na implementação — nunca por número de linha fixo.

### O gate de resposta da IA (3 pontos)
- [`server/routes/webhook.py`](../server/routes/webhook.py): a decisão "a IA deve responder?" é
  `contact.ai_enabled AND settings.get("auto_reply", True) AND _conversation_ai_active(contact)` em **3
  lugares**: `~:562` (pré-cheque), `~:585-586` (individual) e `~:712-713` (negado). **O gate por conversa
  já existe.**
- `_conversation_ai_active(contact)` (`webhook.py:38-50`) lê `conversation_repo.get_open_for_contact(contact.id)["ai_active"]`,
  **fail-open** (`True` se não resolver). ⚠️ usa `get_open_for_contact` (per-**contato**, não per-inbox) → em
  multi-canal pode ler a conversa "errada" (ver §6).

### O toggle do clique-direito (contact-level)
- [`ContextMenu.js`](../web/static/js/components/contacts/ContextMenu.js#L59): `onClick=${() => { onToggleAI(phone, !aiEnabled); onClose(); }}`
  — passa **só `phone`**, embora o menu já receba `conv` (com `conv.id`).
- `Contacts.js` `handleToggleAI(phone, enabled)` (`~:317`) → `toggleContactAI(phone, enabled)` (`api.js:277`)
  → `POST /api/contacts/{phone}/toggle-ai` (`contacts.py:1139`): chama `contact.set_ai_enabled(...)` (grava
  **só** `contacts.ai_enabled`), emite WS `contact_ai_toggled` + notice `ai_on`/`ai_off`. **Nada na tabela
  `conversations`.**
- **Ação em massa** (`Contacts.js:499`): `phones.map(p => toggleContactAI(p, enabled))` — também por telefone.
- Badge **"IA OFF"** renderiza por `c.ai_enabled === false` ([`ContactList.js`](../web/static/js/components/contacts/ContactList.js#L442)).

### O endpoint por-conversa JÁ existe (mas não está ligado na UI)
- `POST /api/conversations/{id}/ai` ([`conversations.py`](../server/routes/conversations.py#L381) `set_ai`) →
  `conversation_repo.set_ai_active(conv_id, active)` ([`conversation_repo.py`](../db/repositories/conversation_repo.py#L417),
  grava **só** `ai_active`) → `_broadcast("conversation_ai_toggled", ...)` + `_emit_notice("ai_on"/"ai_off")`.
- `setConversationAi(id, active)` (`api.js:441`) existe e é **importado** em `ConversationHeaderActions.js`,
  mas **nunca chamado** (`grep` confirma 0 call sites efetivos).

### Atribuição / fila "Não atribuídas"
- `isUnassigned = (c) => c.assignee_user_id == null && !c.active_agent_key` ([`Contacts.js`](../web/static/js/components/contacts/Contacts.js#L39));
  usado em `matchesAssignment` (`:47`), contagem da tab (`:828`) e filtro avançado `'none'` (`:70`).
- **Precedente direto:** `set_status(conv_id, 'closed')` (`conversation_repo.py:396-406`) **já zera**
  `assignee_user_id = None` **e** `active_agent_key = None` na mesma transação — é exatamente o efeito desejado.
- `assign_agent(conv_id, *, assignee_user_id, active_agent_key, ai_active=None)` (`conversation_repo.py:432`)
  faz a escrita atômica das 3 colunas.
- ⚠️ `ensure_ai_agent(contact_id, agent_key)` (`conversation_repo.py:447`, chamado em
  [`handler.py:1236`](../agent/handler.py#L1236)) **reatribui** `active_agent_key` quando ninguém humano pegou —
  **sem checar `ai_active`**. Então hoje, mesmo pausando a IA, a **próxima** mensagem reatribui a conversa à IA.

### Avisos de sistema
- `ai_on`/`ai_off` (grupo `ai`, gate `system_notice_ai`) e `unassigned` (grupo `assignment`, gate
  `system_notice_assignment`) já existem em [`server/system_notices.py`](../server/system_notices.py)
  (`FORMATTERS`/`EVENT_GROUP_OF`).

---

## 1. Decisões de design (travadas)

1. **Toggle 100% por conversa.** O clique-direito e a ação em massa passam a operar por `conversation_id`.
   `contacts.ai_enabled` deixa de ser editável pela UI e sai do gate de resposta.
2. **Desligar = pausar + desatribuir** (espelha `set_status('closed')`): numa transação,
   `ai_active=0`, `active_agent_key=None`, `assignee_user_id=None`. A conversa cai na fila "Não atribuídas".
3. **Religar = reativar + reatribuir ao agente default:** `ai_active=1` + `active_agent_key =`
   `resolve_active_agent_key`/`default_agent_key` da inbox (ou o `DEFAULT_AGENT_KEY`). Assim a linha **sai** da
   fila e volta a aparecer como "atribuída à IA" imediatamente (não espera a próxima mensagem).
4. **`ensure_ai_agent` ganha guard `ai_active`:** vira no-op quando `conv.ai_active == 0` — uma conversa
   pausada **nunca** é reatribuída à IA por uma mensagem que chegue depois.
5. **Gate de resposta:** remover `contact.ai_enabled` dos 3 pontos do `webhook.py`; gate vira
   `settings.get("auto_reply", True) AND _conversation_ai_active(contact)`. (Com o plano 20 o `auto_reply`
   migra de UI mas continua existindo.)
6. **Coluna `contacts.ai_enabled` vira deprecated**, **não** é dropada agora (evita migration destrutiva e
   quebra de `get_or_create`/backfill). Para de ser lida/escrita pela UI e pelo gate; permanece no schema.
   Novas conversas passam a herdar o `ai_active` inicial de `default_ai_enabled` (ver §2.4).
7. **Badge "IA OFF"** passa a ler `c.ai_active === 0` (a linha da sidebar já carrega `ai_active` —
   `Contacts.js` patch `:794`).

---

## 2. Backend

### 2.1. `conversation_repo.set_conversation_ai(conv_id, active)` (transição atômica)
Nova função (ou estender `set_ai_active`) em [`conversation_repo.py`](../db/repositories/conversation_repo.py),
espelhando `set_status('closed')`:
- `active=0` → `_update(conv_id, {"ai_active": 0, "active_agent_key": None, "assignee_user_id": None})`.
- `active=1` → resolver o agente default (`_default_agent_key_for_inbox(inbox_id)` / `DEFAULT_AGENT_KEY`) e
  `_update(conv_id, {"ai_active": 1, "active_agent_key": <default>})` (assignee permanece como está).
- Retorna o row enriquecido (para o payload do WS).

### 2.2. Rota `POST /api/conversations/{id}/ai`
Atualizar `set_ai` (`conversations.py:381`) para chamar `set_conversation_ai` e emitir **ambos** os eventos:
- `await _broadcast(deps, "conversation_assigned", "conversation.assigned", conv)` — reposiciona a linha
  (fila "Não atribuídas").
- `await _broadcast(deps, "conversation_ai_toggled", "conversation.ai_toggled", conv)` — atualiza o badge.
- `await _emit_notice(request, conv, "ai_on" if active else "ai_off")` — card no fio (gate `system_notice_ai`).
- (Opcional) quando `active=0`, emitir também `"unassigned"`? **Não** — evita 2 cards; o `ai_off` já comunica.

### 2.3. Guard em `ensure_ai_agent`
Em `ensure_ai_agent` (`conversation_repo.py:447`): após resolver `conv`, **retornar sem escrever** se
`not conv.get("ai_active")` (além do guard de `assignee_user_id` que já existe). Garante (4).

### 2.4. Gate de resposta + default das conversas novas
- `webhook.py` (`:562`, `:585-586`, `:712-713`): trocar `contact.ai_enabled and settings.get("auto_reply", True) and _conversation_ai_active(contact)`
  por `settings.get("auto_reply", True) and _conversation_ai_active(contact)`.
- Conversas novas: em `conversation_repo.create` (`:55`), setar `ai_active` a partir de
  `settings["default_ai_enabled"]` (hoje a coluna tem `server_default=1` e o default vem de `contacts.ai_enabled`).
  Manter `_conversation_ai_active` fail-open.

---

## 3. Frontend

- **`ContextMenu.js`:** `onToggleAI` passa a receber/usar `conv.id`. Renderizar o item só quando `canAct`
  (`conv && conv.id != null`); o rótulo lê `conv.ai_active` (não `aiEnabled` do contato). Em rows legadas
  (`conversation_id === null`) ocultar/disabilitar.
- **`Contacts.js`:** `handleToggleAI` passa a chamar `setConversationAi(convId, enabled)` e faz update
  **otimista por `conversation_id`** (patch `ai_active`, e ao desligar zera `active_agent_key`/`assignee_user_id`
  localmente para a linha cair na fila na hora). A **ação em massa** (`:499`) itera as conversas selecionadas
  (`conv.id`), não os phones.
- **Badge:** `ContactList.js:442` passa a ler `c.ai_active === 0`.
- **WS:** `conversation_assigned` e `conversation_ai_toggled` já são tratados em `onConversationChanged`
  (`Contacts.js:783-800`, patch `ai_active`/`active_agent_key`/`assignee_user_id`). Sem handler novo.
- Remover a referência morta a `setConversationAi` em `ConversationHeaderActions.js` (ou ligá-la a um toggle
  no header, se desejado — fora do escopo mínimo).

---

## 4. Testes (`tests/test_endpoints.py`)
- **Desligar desatribui:** conversa com `active_agent_key` setado + `ai_active=1`. `POST /conversations/{id}/ai`
  `{active:false}` → 200; `select`: `ai_active=0`, `active_agent_key IS NULL`, `assignee_user_id IS NULL`
  (cai na fila).
- **Religar reatribui:** `{active:true}` → `ai_active=1`, `active_agent_key = <default>`.
- **Guard do `ensure_ai_agent`:** com `ai_active=0`, simular o caminho do handler e asserir que
  `active_agent_key` **não** é re-stampado.
- **Gate:** webhook com `auto_reply=True`, `ai_active=0` → **não** responde; `ai_active=1` → responde
  (independe de `contacts.ai_enabled`).
- **Notice:** `ai_off` gravado no fio quando `system_notice_ai` ligado.

---

## 5. Checklist
- [ ] `conversation_repo.set_conversation_ai(conv_id, active)` (transição atômica espelhando `set_status('closed')`).
- [ ] Rota `POST /conversations/{id}/ai` emite `conversation_assigned` + `conversation_ai_toggled` + `ai_on/off`.
- [ ] `ensure_ai_agent` guard `ai_active`.
- [ ] Gate `webhook.py` (3 sites) sem `contact.ai_enabled`.
- [ ] `conversation_repo.create` herda `ai_active` de `default_ai_enabled`.
- [ ] `ContextMenu.js`/`Contacts.js` toggle + ação em massa por `conversation_id`; badge lê `ai_active`.
- [ ] Testes 4.x; rodar `python tests/test_endpoints.py`.
- [ ] Contraste no modo escuro do badge/menu.

---

## 6. Riscos e fora de escopo
- **Multi-canal:** `_conversation_ai_active` e `ensure_ai_agent` resolvem por `get_open_for_contact`
  (per-contato). Para 1 contato com conversas em 2 inboxes o gate de **leitura** pode olhar a conversa errada.
  A **escrita** (toggle) é precisa (usa `conv.id`). Mitigação ideal: o webhook já resolve a conversa específica
  — passar `conversation_id` ao gate. Documentado; melhoria incremental.
- **`contacts.ai_enabled` deprecated, não dropada.** `get_or_create(default_ai_enabled=...)`
  (`contact_repo.py:49`) ainda popula a coluna; o gate só deixa de lê-la. Remoção física = migration futura.
- **Race fail-open:** `_conversation_ai_active` devolve `True` em falha — preserva comportamento atual
  (a IA nunca silencia por erro de resolução), mas pode responder uma conversa recém-pausada numa corrida rara.
- **Ação em massa:** semântica muda de "por contato" para "por conversa selecionada" — alinhado à decisão.
- **Cruza com plano 20:** `auto_reply` e `default_ai_enabled` migram de UI (painel → tela da IA) mas continuam
  existindo como config keys; este plano só os referencia.
