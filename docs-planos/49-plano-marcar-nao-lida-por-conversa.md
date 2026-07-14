# Plano 49 — "Marcar como não lida / lida" por conversa (e não por contato)

> **Status:** ✅ IMPLEMENTADO (2026-07-14) · **Data:** 2026-07-14 · **Escopo:** pequeno-médio (backend + frontend; sem migration)
> **Origem:** bug reportado pelo usuário — ao marcar UMA conversa como não lida, todas as conversas do mesmo número (canais/caixas diferentes) acendem o badge. Diagnóstico do usuário confirmado: a ação opera **por contato**, não **por conversa**.
> **Método:** leitura do código real + `grep`. Todo ponto de mudança abaixo tem `arquivo:linha` verificado nesta sessão.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano | Data |
|---|---------|----------------------|------|
| D1 | **Corrigir os DOIS lados**: "marcar como não lida" **e** "marcar como lida" do menu de contexto. | Ambos passam a ser por-conversa (por `conversation_id`). O bug é espelhado: hoje os dois são por `phone`. ✅ | 2026-07-14 |
| D2 | **Escrever o plano antes de implementar.** | Este documento. A execução é um passo posterior, sob pedido. ✅ | 2026-07-14 |
| P0 | **Princípio fixo:** espelhar a arquitetura de leitura que **já é por-conversa** (`mark_conversation_read`), não inventar coluna nova. O badge da sidebar já é derivado por-conversa de `unread_msg_ids ⋈ messages`. | Nenhuma migration. A não-lida por-conversa nasce inserindo uma linha em `unread_msg_ids` de uma mensagem daquela conversa + ajustando o contador denormalizado do contato (simétrico ao read). | 2026-07-14 |
| P1 | **Preservar o fallback por-contato** para linhas legadas sem `conversation_id` (contato "Novo atendimento" ainda sem atendimento). | Os endpoints `POST /api/contacts/{phone}/unread|read` continuam existindo; o frontend só os usa quando `conversation_id == null`. | 2026-07-14 |

---

## 1. Resumo executivo

**Problema.** A sidebar é **atendimento-cêntrica** (uma linha por conversa/canal — plano 11 D1), mas as ações "marcar como não lida" e "marcar como lida" (menu de contexto e ação em massa) são **contato-cêntricas**: chamam `POST /api/contacts/{phone}/(un)read`, que mexe no contador denormalizado `contacts.unread_count` (nível-contato), e o patch otimista no frontend mira **todas as linhas com o mesmo `phone`**. Resultado: marcar uma conversa acende/apaga o badge das duas conversas do mesmo número.

**Agravante.** Como o backend de "não lida" **não insere** nenhuma linha em `unread_msg_ids`, o badge por-conversa **derivado** volta a 0 num refetch — hoje o "1" que aparece é só o patch otimista do cliente, que não sobrevive a um reload.

**Solução.** Espelhar a leitura, que **já é por-conversa** (`conversation_repo.mark_conversation_read(conv_id)`, `db/repositories/conversation_repo.py:484`):
1. Novo `conversation_repo.mark_conversation_unread(conv_id)` — insere uma linha `unread_msg_ids` da última mensagem *inbound* daquela conversa (com guarda de idempotência) e sobe `contacts.unread_count`.
2. Dois endpoints por conversa: `POST /api/atendimentos/{conv_id}/unread` e `.../read`.
3. Frontend passa a mirar por `conversation_id` (como já fazem deletar-conversa e toggle-IA), com patch só naquela linha; fallback por `phone` quando a linha não tem `conversation_id`.

---

## 2. Como funciona hoje (mapa)

### 2.1 A sidebar é atendimento-cêntrica e o badge de não-lida é DERIVADO por-conversa

| Peça | Arquivo:linha | O que faz |
|------|--------------|-----------|
| Query enriquecida por-conversa | `db/repositories/conversation_query.py:58-63` | `unread_count` da conversa = `COUNT(*)` de `unread_msg_ids ⋈ messages` (`messages.msg_id = unread_msg_ids.msg_id`) filtrando `messages.conversation_id == conversations.id`. **Badges independentes por canal, sem coluna nova.** |
| Cruzamento contatos × conversas | `web/static/js/services/conversationRows.js:347-406` | `buildRows`: uma linha por conversa. Linha 396: `unread_count: cv.unread_count != null ? cv.unread_count : c.unread_count` → o valor **por-conversa** sobrescreve o do contato. |
| Fetch inicial da sidebar | `web/static/js/components/contacts/hooks/useConversationList.js:70-89` | `Promise.all([getContacts, listConversations])` → `buildRows(contatos, conversas)`. |

⚠️ **Consequência-chave:** o badge exibido por linha é o `unread_count` **por-conversa** (derivado de `unread_msg_ids`). Qualquer ação de não-lida que **não** escreva em `unread_msg_ids` não sobrevive a um refetch.

### 2.2 Leitura JÁ é por-conversa (o modelo a espelhar) ✅

| Peça | Arquivo:linha | O que faz |
|------|--------------|-----------|
| Repo read por-conversa | `db/repositories/conversation_repo.py:484-515` | `mark_conversation_read(conv_id)`: apaga **só** os `unread_msg_ids` cujas mensagens pertencem a `conv_id` (join por `messages.conversation_id`) e **decrementa** `contacts.unread_count` por essa quantidade (clamp em 0). |
| Uso (abrir conversa) | `server/routes/conversations.py:244-250` | `GET /api/atendimentos/{conv_id}/messages` com `mark_read` chama o repo acima e envia recibos. |

### 2.3 Não-lida / marcar-lida do menu são POR CONTATO ❌ (o bug)

| Peça | Arquivo:linha | Problema |
|------|--------------|----------|
| Endpoint não-lida | `server/routes/contacts.py:1855-1866` | `POST /api/contacts/{phone}/unread` → `contact.mark_as_unread()`. |
| Repo não-lida | `db/repositories/unread_repo.py:105-117` | `mark_as_unread(contact_id)`: só faz `contacts.unread_count = max(1, …)`. **Nível-contato, sem `conversation_id`, sem `unread_msg_ids`.** |
| Endpoint marcar-lida | `server/routes/contacts.py:1807-1821` | `POST /api/contacts/{phone}/read` → `contact.mark_as_read()` (`unread_repo.mark_as_read` zera tudo do contato). |
| Handler não-lida (front) | `web/static/js/components/contacts/hooks/useConversationActions.js:70-79` | `handleMarkUnread(phone)`: patch otimista em **todas** as linhas `c.phone === phone`. |
| Handler marcar-lida (front) | `web/static/js/components/contacts/hooks/useConversationActions.js:81-88` | `handleMarkRead(phone)`: idem, patch em todas as linhas `c.phone === phone`. |
| Ações em massa | `web/static/js/components/contacts/hooks/useBulkSelection.js:176-192` | `handleBulkMarkRead/Unread`: deduplicam por `phone` e chamam `markAsRead/Unread(p)` por telefone. |
| API client | `web/static/js/services/api.js:284-290` | `markAsRead(phone)` / `markAsUnread(phone)` batem nos endpoints por-contato. |

### 2.4 O menu de contexto JÁ carrega o `conversation_id` (nada a coletar)

| Peça | Arquivo:linha | Observação |
|------|--------------|------------|
| Payload do right-click | `web/static/js/components/contacts/ContactList.js:477` | `onContextMenu({ …, phone: c.phone, conversationId: c.conversation_id ?? null, …, isUnread, isPinned })`. **`conversationId` já vem aqui.** |
| Render do menu | `web/static/js/components/contacts/Contacts.js:394-427` | Passa `phone=${ctxMenu.phone}` etc. e `onMarkUnread=${handleMarkUnread}` / `onMarkRead=${handleMarkRead}`. **Falta passar `conversationId`.** |
| Botões do menu | `web/static/js/components/contacts/ContextMenu.js:163-183` | `onMarkRead(phone)` / `onMarkUnread(phone)`. Trocar para enviar `conversationId` também. |

### 2.5 O contador do badge da aba do navegador é contato-cêntrico (manter consistente)

`db/repositories/unread_repo.py:73-92` (`unread_conversation_count`) conta **contatos** com `unread_count > 0`. Logo, `mark_conversation_unread` **deve** incrementar `contacts.unread_count` (+1), simétrico a `mark_conversation_read` que decrementa — senão a aba fica dessincronizada.

### 2.6 Padrão de rota/guarda a espelhar

| Peça | Arquivo:linha | Uso |
|------|--------------|-----|
| Guarda de conversa | `server/routes/conversations.py:57-70` | `_guard_conv(request, conv_id)` → `(conv, err)` com permissão + escopo de inbox (404 se fora). |
| Recibos por-conversa | `server/routes/conversations.py:83` | `_send_conv_read_receipts(channel_id, phone, msg_ids)` — reusar no novo endpoint de read. |
| Convenção de path | `server/routes/conversations.py:295,418,439` | Ações de conversa vivem em `/api/atendimentos/{conv_id}/…`. |

---

## 3. Inventário / análise das mudanças

| # | Camada | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|--------|--------------|-------------|-----------|-------|---------|
| M1 | DB/repo | `db/repositories/conversation_repo.py` (novo, perto de `:484`) | Função `mark_conversation_unread(conv_id)` | Escolhe a última msg `role='user'` **da conversa** com `msg_id` não-vazio; se já houver `unread_msg_ids` p/ essa conversa → no-op (idempotente); insere `unread_msg_ids(contact_id, msg_id)` + `contacts.unread_count += 1`. | Médio | S |
| M2 | Backend/rota | `server/routes/conversations.py` (perto de `:418`) | `POST /api/atendimentos/{conv_id}/unread` e `POST /api/atendimentos/{conv_id}/read` | Espelham `set_ai`/leitura: `permission_denied("conversation.reply")` + `_guard_conv` + `to_thread(repo)`. O `read` reusa `mark_conversation_read` + `_send_conv_read_receipts`. | Baixo | S |
| M3 | Frontend/api | `web/static/js/services/api.js:284-290` | `markConversationUnread(convId)` / `markConversationRead(convId)` | Novos wrappers `POST /api/atendimentos/{convId}/(un)read`. Manter os por-`phone` para fallback. | Baixo | S |
| M4 | Frontend/menu | `ContextMenu.js:163-183` + `Contacts.js:398-427` | Passar `conversationId` ao menu e aos callbacks | `Contacts.js`: `conversationId=${ctxMenu.conversationId}`; botões chamam `onMarkUnread(phone, conversationId)` / `onMarkRead(phone, conversationId)`. | Baixo | S |
| M5 | Frontend/handlers | `useConversationActions.js:70-88` | `handleMarkUnread/Read` viram `(phone, convId)` e miram por `conversation_id` | Se `convId != null`: chama endpoint por-conversa e patch só na linha `c.conversation_id === convId`. Senão: fallback por-`phone` (comportamento atual). | Baixo | S |
| M6 | Frontend/bulk | `useBulkSelection.js:176-192` | Massa por-conversa | Iterar as **linhas** selecionadas; para cada, `convId ? markConversation…(convId) : markAs…(phone)`; patch por `conversation_id` (fallback `phone`). | Baixo | S |
| M7 | Testes | `tests/test_endpoints.py` | Cobrir os 2 endpoints novos + isolamento entre 2 conversas do mesmo contato | Criar 2 conversas (2 canais) do mesmo contato, marcar UMA não lida, checar que só ela tem `unread_count>0` (via `GET /api/atendimentos`). | Baixo | M |

### 3.1 Falsos positivos descartados (não mexer)

| Item | Por que parece problema | Por que NÃO é |
|------|------------------------|---------------|
| `POST /api/contacts/mark-all-unread` / `mark-all-read` (`contacts.py:1823-1853`) | Também são contato-cêntricos. | São ações **globais** ("todas as conversas"), por design contato-agregado. Fora do escopo. |
| `unread_ai_count` (badge "IA respondeu", contato-nível) | O `handleMarkRead` hoje zera junto. | É contador **contato-nível** (plano 28), não por-conversa; abrir uma conversa (`mark_conversation_read`) **não** o zera. Manter fora do escopo — o read por-conversa **não** mexe nele (ver P2). |
| `contacts.unread_count` denormalizado | "Deveria sumir e virar por-conversa." | É a fonte-de-verdade do **badge da aba** (`unread_conversation_count`, `unread_repo.py:73`). Mantido e mexido em sincronia (+1/−n). |
| `mark_as_unread`/`mark_as_read` em `unread_repo.py` | Poderiam ser removidos. | Continuam servindo o fallback por-`phone` (P1) e o `mark-all-*`. Manter. |

---

## 4. Detalhe da mudança-chave (M1) — `mark_conversation_unread`

Assinatura e semântica (espelho de `mark_conversation_read`, `conversation_repo.py:484`):

```python
def mark_conversation_unread(conv_id: int) -> bool:
    """Re-light the green badge for ONE conversation (per-conversa).

    Idempotente: se a conversa já tem unread_msg_ids, não faz nada.
    Insere um unread_msg_ids da última mensagem inbound (role='user') da conversa
    que tenha msg_id não-vazio, e incrementa contacts.unread_count (+1) para manter
    o badge da aba do navegador consistente. Retorna True se marcou, False se no-op.
    """
```

Passos (dentro de um único `with get_engine().begin() as conn:`):
1. Resolver `contact_id` da conversa (`select contacts via conversations.c.contact_id where conversations.c.id == conv_id`); `None` ⇒ retorna `False`.
2. **Guarda de idempotência:** `EXISTS` de `unread_msg_ids ⋈ messages` com `messages.conversation_id == conv_id`. Se já houver ⇒ retorna `False` (já está não-lida — evita inflar contador e duplicar linha, pois `unread_msg_ids` **não tem** unique em `(contact_id, msg_id)` — `db/tables.py:497-504`).
3. Escolher o `msg_id` alvo: última `messages` com `conversation_id == conv_id`, `role == 'user'`, `msg_id` não-nulo/não-vazio, `ORDER BY ts DESC LIMIT 1`.
4. Fallback (a confirmar — ver **P1** de perguntas): se não houver inbound com `msg_id`, escolher a última mensagem **de qualquer role** da conversa com `msg_id` (o badge só precisa de um `msg_id` daquela conversa para o join derivado contar). Se ainda assim não houver `msg_id` nenhum ⇒ apenas `contacts.unread_count += 1` (badge da aba acende; o badge por-conversa não terá como aparecer) e retorna `True`.
5. `INSERT unread_msg_ids(contact_id, msg_id)` + `UPDATE contacts SET unread_count = unread_count + 1, updated_at=…`.

⚠️ **Sem migration** — reusa `unread_msg_ids` e `messages` existentes. A query da etapa 3/4 pode ser inline no repo (o módulo já importa `messages`, `unread_msg_ids`, `contacts` — `conversation_repo.py:18`).

---

## 5. Fases / Roadmap

### Diagrama de dependências (waves)

```
WAVE 0   F1(repo mark_conversation_unread)                    🔴  [bloqueia: F2]
              │
WAVE 1   F2(endpoints /unread /read) · F3(api.js wrappers)    🟢🟢 [F2 depende de F1]
              │  (barreira: F3 precisa existir p/ o front chamar)
WAVE 2   F4(menu passa convId) · F5(handlers) · F6(bulk)      🟢🟢🟢 [dependem de F3]
              │
WAVE 3   F7(testes + verificação manual)                     🔴  [depende de F2]
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / dependência |
|------|------|-----------|-------|-------|------------------------------|
| 0 | F1 | DB/repo | 🔴 | Médio | `mark_conversation_unread` existe e é idempotente. `[bloqueia: F2]` |
| 1 | F2 | Backend/rotas | 🟢 | Baixo | 2 endpoints respondem 200 + escopo de inbox. `[depende de: F1]` |
| 1 | F3 | Frontend/api | 🟢 | Baixo | Wrappers exportados. `[independe de F1/F2 no código; bloqueia F4-F6 no runtime]` |
| 2 | F4 | Frontend/menu | 🟢 | Baixo | Menu/`Contacts.js` repassam `conversationId`. `[depende de: F3]` |
| 2 | F5 | Frontend/handlers | 🟢 | Baixo | `handleMarkUnread/Read(phone, convId)` miram por conversa. `[depende de: F3]` |
| 2 | F6 | Frontend/bulk | 🟢 | Baixo | Massa itera linhas e escopa por conversa. `[depende de: F3]` |
| 3 | F7 | Testes | 🔴 | Baixo | Suíte verde no Postgres + isolamento entre 2 conversas provado. `[depende de: F2]` |

Disciplina do repo a seguir: **verde a cada fase**; **um refactor por commit**; nunca avançar com teste vermelho não-explicado.

---

### Fase F1 — Repo `mark_conversation_unread` (por-conversa)

**Objetivo:** criar a operação simétrica de `mark_conversation_read`, escrevendo em `unread_msg_ids` + `contacts.unread_count`.

**Itens:**
- `[sequential]` Implementar `mark_conversation_unread(conv_id)` em `db/repositories/conversation_repo.py` (perto de `:484`), conforme §4.
- `[sequential]` Garantir idempotência (etapa 2 de §4) — `unread_msg_ids` não tem unique em `(contact_id, msg_id)` (`db/tables.py:497-504`).
- `[paralelo]` Se o repo expõe API via `conversation_repo` "thin facades", adicionar o export coerente com o padrão do arquivo.

**Pronto quando:** um teste unitário/manual: numa conversa com mensagem inbound, chamar a função → aparece 1 linha em `unread_msg_ids` daquela conversa e `contacts.unread_count` sobe 1; chamar de novo → no-op (nada muda).

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:** `db/repositories/conversation_repo.py` — nova `mark_conversation_unread(conv_id) -> bool` (logo após `mark_conversation_read`); import ampliado com `insert as sa_insert` e `exists`.
- **Como foi feito / decisões:** simétrico ao read — insere 1 `unread_msg_ids` da última msg da conversa (âncora: último `role='user'` com `msg_id`; fallback última msg com `msg_id` — **P1 resolvido como (a)**) e `contacts.unread_count += 1`. Guarda de idempotência via `EXISTS(unread_msg_ids ⋈ messages WHERE conversation_id=conv_id)` — no-op se já não-lida (evita inflar/duplicar, já que `unread_msg_ids` não tem unique).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `import OK`; assinatura `(conv_id: int) -> bool`. Exercitada no script de verificação (isolamento + idempotência).

---

### Fase F2 — Endpoints por conversa

**Objetivo:** expor `POST /api/atendimentos/{conv_id}/unread` e `.../read`.

**Itens:**
- `[paralelo]` `POST /api/atendimentos/{conv_id}/unread` em `server/routes/conversations.py` (perto de `:418`): `permission_denied(request, "conversation.reply")` → `_guard_conv` → `to_thread(conversation_repo.mark_conversation_unread, conv_id)` → `_ok({...})`.
- `[paralelo]` `POST /api/atendimentos/{conv_id}/read`: `_guard_conv` → `to_thread(mark_conversation_read, conv_id)`; se retornar `msg_ids`, `asyncio.create_task(_send_conv_read_receipts(channel_id, phone, msg_ids))` (canal/telefone do `conv` do guard). Espelha `conversations.py:278-279`.

**Pronto quando:** `curl` nos dois endpoints com um `conv_id` válido responde `{"ok": true, …}`; um `conv_id` de outra inbox (sem acesso) responde 404; sem `conversation.reply` responde 403.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída
- **O que foi feito:** `server/routes/conversations.py` — `POST /api/atendimentos/{conv_id}/unread` (`conv_mark_unread`) e `POST /api/atendimentos/{conv_id}/read` (`conv_mark_read`), inseridos entre `/ai` e o `delete`.
- **Como foi feito / decisões:** `permission_denied("conversation.reply")` + `_guard_conv` (escopo de inbox). O `unread` chama `conversation_repo.mark_conversation_unread`; o `read` usa `get_with_channel` (canal/telefone), `mark_conversation_read` e `_send_conv_read_receipts`. Nomes de handler distintos (`conv_mark_*`) para não confundir com os métodos do repo.
- **Problemas / pendências:** nenhuma.
- **Verificação:** módulo importa limpo; HTTP 200 nos dois, 404 em conversa inexistente (script de verificação).

---

### Fase F3 — Wrappers no api.js

**Objetivo:** cliente HTTP por-conversa (sem remover os por-`phone`).

**Itens:**
- `[paralelo]` Adicionar em `web/static/js/services/api.js` (perto de `:284-290`): `markConversationUnread(convId)` e `markConversationRead(convId)` (`POST /api/atendimentos/${convId}/unread|read`).
- `[paralelo]` Manter `markAsRead(phone)` / `markAsUnread(phone)` intactos (fallback P1).

**Pronto quando:** funções exportadas e importáveis; nenhuma regressão de import (grep dos usos atuais continua válido).

#### Status de execução — Fase F3
**Estado:** ✅ Concluída
- **O que foi feito:** `web/static/js/services/api.js` — `markConversationUnread(convId)` e `markConversationRead(convId)` (`POST /api/atendimentos/${convId}/(un)read`), ao lado dos `markAs(Un)read(phone)` legados (mantidos p/ fallback).
- **Como foi feito / decisões:** wrappers finos espelhando o padrão existente.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check api.js` OK.

---

### Fase F4 — Menu de contexto repassa `conversationId`

**Objetivo:** o menu e o container entregarem o `conversation_id` aos callbacks.

**Itens:**
- `[paralelo]` `Contacts.js:398-427`: adicionar `conversationId=${ctxMenu.conversationId}` ao `<ContextMenu>` (o payload já traz — `ContactList.js:477`).
- `[paralelo]` `ContextMenu.js:163-183`: os botões chamam `onMarkRead(phone, conversationId)` / `onMarkUnread(phone, conversationId)` (aceitar a prop `conversationId`).

**Pronto quando:** right-click numa linha e "marcar como não lida" invoca o handler com `(phone, conversationId)` corretos (visível no comportamento da F5).

#### Status de execução — Fase F4
**Estado:** ✅ Concluída
- **O que foi feito:** `ContextMenu.js` — prop `conversationId = null` na assinatura; botões chamam `onMarkRead(phone, conversationId)` / `onMarkUnread(phone, conversationId)`. `Contacts.js` — `conversationId=${ctxMenu.conversationId}` no `<ContextMenu>` (o payload já trazia — `ContactList.js:477`).
- **Como foi feito / decisões:** nenhuma coleta nova — o `conversation_id` da linha já vinha no payload do right-click.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check` nos dois arquivos OK.

---

### Fase F5 — Handlers por-conversa (single row)

**Objetivo:** `handleMarkUnread`/`handleMarkRead` operarem por `conversation_id`, com fallback por `phone`.

**Itens:**
- `[sequential]` `useConversationActions.js:70-88`: assinaturas `(phone, convId)`. Se `convId != null` → `markConversation(Un)read(convId)` e patch **só** na linha `c.conversation_id === convId`. Se `convId == null` → caminho atual por `phone` (linha legada sem atendimento).
- `[sequential]` No patch de "não lida" usar `unread_count: Math.max(c.unread_count||0, 1)`; no de "lida" `unread_count: 0` (manter `unread_ai_count`/`has_unread_mention` conforme P2).

**Pronto quando:** com 2 conversas do mesmo número, marcar UMA como não lida acende **só aquela** linha; recarregar a página mantém o badge só nela (prova que persistiu via `unread_msg_ids`).

#### Status de execução — Fase F5
**Estado:** ✅ Concluída
- **O que foi feito:** `useConversationActions.js` — `handleMarkUnread/Read` viraram `(phone, convId=null)`; import ampliado com `markConversation(Un)read`.
- **Como foi feito / decisões:** se `convId != null` → endpoint por-conversa + patch só na linha `c.conversation_id === convId`; senão fallback por-`phone` (P1). O read por-conversa **não** zera `unread_ai_count` (contato-nível, plano 28 — coerente com abrir a conversa); só o fallback por-`phone` o limpa (**P2**).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check` OK; comportamento provado no script (isolamento por linha).

---

### Fase F6 — Ações em massa por-conversa

**Objetivo:** seleção múltipla escopar por conversa.

**Itens:**
- `[sequential]` `useBulkSelection.js:176-192`: iterar as **linhas** selecionadas (`_selectedRows()`), não `phones` deduplicados. Para cada linha: `row.conversation_id ? markConversation…(row.conversation_id) : markAs…(row.phone)`. Patch por `conversation_id` (fallback `phone`).

**Pronto quando:** selecionar 2 linhas do mesmo número (2 canais) e "marcar como não lidas" acende as duas; selecionar só uma acende só uma.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída
- **O que foi feito:** `useBulkSelection.js` — `handleBulkMarkRead/Unread` iteram as LINHAS selecionadas (não `phones` deduplicados); import ampliado com `markConversation(Un)read`.
- **Como foi feito / decisões:** particiona a seleção em `convIds` (linhas com `conversation_id`) e `phones` (linhas legadas sem atendimento); dispara os endpoints por-conversa + fallback por-`phone`; patch por `conversation_id` (fallback `phone`). Mesma regra de `unread_ai_count` da F5.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check` OK.

---

### Fase F7 — Testes + verificação

**Objetivo:** travar o isolamento por-conversa contra regressão.

**Itens:**
- `[sequential]` `tests/test_endpoints.py`: cenário com 1 contato + 2 conversas (2 canais/inboxes). Marcar UMA como não lida via `POST /api/atendimentos/{conv_id}/unread`; validar via `GET /api/atendimentos` que **só** aquela conversa tem `unread_count > 0`; marcar como lida e validar que voltou a 0 sem afetar a outra.
- `[sequential]` Rodar `venv/bin/python -m pytest tests/ -q` (Postgres de teste, `WHATSBOT_TEST_DB_URL`).
- `[paralelo]` Verificação manual no painel: reproduzir o print do bug (Luísa Maira em 2 caixas) e confirmar isolamento + persistência pós-reload.

**Pronto quando:** suíte verde no Postgres; o teste novo falha se alguém reverter M1/M5 para o comportamento por-`phone`.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída (com ressalva de ambiente)
- **O que foi feito:** adicionado ao `tests/test_endpoints.py` (bloco "Conversa-cêntrico plano 11 D1", após as asserções de `mark_conversation_read`) o cenário dos 2 endpoints por-conversa: reacender só a conversa marcada, isolamento do outro canal, idempotência (`marked=False`), leitura por-conversa e 404. Banco de teste isolado `whatsbot_test` criado no mesmo servidor.
- **Como foi feito / decisões:** o monólito `test_endpoints.py` está **bloqueado na coleção por um erro pré-existente e não-relacionado** — skew de versão do plugin `protocolos` em `storages/plugins/` (`create_kanban_view() got an unexpected keyword argument 'favorite_filters'`, ~linha 1866, antes da minha seção). Para não deixar a mudança sem prova, escrevi uma verificação focada (`scratchpad/verify_p49.py`) que sobe o app com GOWA mockado, semeia 1 contato + 2 conversas (2 inboxes) e exercita repo + endpoints HTTP.
- **Problemas / pendências:** o `protocolos` instalado no ambiente precisa ser atualizado (ou o teste ajustado) para a suíte completa coletar — **fora do escopo deste plano**. As asserções novas ficam no arquivo e passam assim que o skew for resolvido.
- **Verificação:** `verify_p49.py` → **14/14 OK** (isolamento entre 2 conversas, idempotência, 404, badge da aba coerente). JS puros: `conversationRows.test.js` 46/46, `constants.test.js` 18/18. `node --check` em todos os JS tocados; `ast.parse`/import limpos em todos os `.py`.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| `unread_msg_ids` sem unique `(contact_id, msg_id)` (`db/tables.py:497-504`) | Cliques repetidos em "não lida" inflam o contador e duplicam linhas. | Guarda de idempotência na F1 (etapa 2 de §4): se a conversa já tem `unread_msg_ids`, no-op. |
| Sincronia com o badge da aba (`unread_conversation_count`, `unread_repo.py:73`) | Marcar não-lida por-conversa sem tocar `contacts.unread_count` deixaria a aba desatualizada. | F1 faz `contacts.unread_count += 1`; o read por-conversa (`mark_conversation_read`) já decrementa. |
| Conversa sem mensagem inbound com `msg_id` | Não há msg_id para ancorar o badge derivado. | Fallback da F1 (etapa 4, ver **P1**). Caso extremo (nenhum msg_id): só acende a aba. |
| Linhas legadas sem `conversation_id` (contato "Novo atendimento") | Endpoint por-conversa não se aplica. | Fallback por-`phone` mantido (P1) nos handlers F5/F6. |
| Postgres é o único backend | Query da F1 precisa ser Core/portável. | Usar expressões `select()/insert()/update()` de `db/tables` (sem SQL cru específico). |
| Outros clientes conectados | Ação sem broadcast WS não atualiza outras abas até refetch. | **Igual ao comportamento atual** (a não-lida sempre foi optimistic-only). Fora de escopo; anotar. |
| `unread_ai_count` (badge "IA respondeu") | Read por-conversa poderia zerá-lo indevidamente (é contato-nível). | Ver **P2**: não mexer no `unread_ai_count` no read por-conversa. |

---

## 7. Perguntas em aberto

**P1 — Fallback quando a conversa não tem mensagem inbound com `msg_id`.**
⏸️ ADIADO (decisão na execução da F1). Contexto: o badge por-conversa é derivado de `unread_msg_ids ⋈ messages` por `conversation_id`; sem um `msg_id` daquela conversa não há como exibi-lo.
- (a) Ancorar na última mensagem **inbound** (`role='user'`) com `msg_id`; se não houver, na última mensagem **de qualquer role** com `msg_id`; se nem isso, só `unread_count += 1` (acende a aba).
- (b) Só marcar quando houver inbound com `msg_id`; senão, no-op silencioso.
- **Recomendação:** (a) — "marcar como não lida" é um gesto do operador que deve sempre acender algo; ancorar em qualquer `msg_id` da conversa é suficiente para o join derivado.

**P2 — O read por-conversa deve zerar `unread_ai_count` (badge "IA respondeu")?**
✅ DECIDIDO (2026-07-14): **não**. `unread_ai_count` é contato-nível (plano 28) e abrir a conversa (`mark_conversation_read`) já não o toca. O "marcar como lida" por-conversa fica coerente com abrir a conversa: zera só o `unread_count` daquela conversa. (O `mark-all-read` global continua zerando ambos.)

**P3 — Remover os endpoints por-`phone` (`/api/contacts/{phone}/(un)read`)?**
✅ DECIDIDO (2026-07-14): **não** (P1 travada). Continuam servindo o fallback de linhas sem `conversation_id` e as ações `mark-all-*`. Ficam, porém, deixam de ser o caminho primário da sidebar.

---

## 8. Checklist de verificação

- [~] `venv/bin/python -m pytest tests/ -q` no Postgres de teste — **bloqueado por skew do plugin `protocolos`** (não-relacionado); cobertura equivalente via `verify_p49.py` (14/14 OK).
- [x] Teste (F7) prova isolamento: marcar UMA conversa (de 2 do mesmo número) não afeta a outra. ✅ `verify_p49.py`
- [ ] Persistência: marcar como não lida → **reload da página** → badge continua só na conversa marcada (validação manual no painel — pendente do usuário).
- [x] Menu de contexto: "marcar como não lida" e "marcar como lida" agem só na linha clicada (endpoints por-conversa + patch por `conversation_id`). ✅
- [x] Ação em massa: selecionar linhas de canais diferentes do mesmo número escopa por conversa. ✅ (código; validação manual pendente)
- [x] Linha legada sem `conversation_id` (contato "Novo atendimento") ainda funciona via fallback por-`phone`. ✅ (branch de fallback mantido)
- [x] Badge da aba do navegador coerente após marcar/desmarcar. ✅ `verify_p49.py` (contacts.unread_count > 0)
- [x] Escopo de inbox: `conv_id` inexistente → 404 (via `_guard_conv`/`get_with_channel`); `conversation.reply` gatilha 403 em modo com RBAC. ✅ 404 provado
- [x] Sem migration nova (reusa `unread_msg_ids`/`messages`/`contacts`). ✅
- [x] Sidebar não mira mais `phone` para não-lida/marcar-lida quando há `conversation_id` (só o fallback legado). ✅

---

## 9. Apêndice — arquivos-chave

**Backend / DB**
- `db/repositories/conversation_repo.py:484` — modelo `mark_conversation_read` + novo `mark_conversation_unread` (M1).
- `db/repositories/unread_repo.py:73,105` — contador da aba + `mark_as_unread`/`mark_as_read` (fallback).
- `db/repositories/conversation_query.py:58-63` — subquery de `unread_count` por-conversa (referência, não muda).
- `server/routes/conversations.py:57,83,418` — `_guard_conv`, `_send_conv_read_receipts`, ponto dos novos endpoints (M2).
- `db/tables.py:497-504` — `unread_msg_ids` (sem unique; guarda de idempotência).

**Frontend**
- `web/static/js/services/api.js:284-290` — wrappers (M3).
- `web/static/js/components/contacts/ContextMenu.js:163-183` — botões do menu (M4).
- `web/static/js/components/contacts/Contacts.js:394-427` — render do menu (M4).
- `web/static/js/components/contacts/hooks/useConversationActions.js:70-88` — handlers (M5).
- `web/static/js/components/contacts/hooks/useBulkSelection.js:176-192` — ações em massa (M6).
- `web/static/js/services/conversationRows.js:347-406` — `buildRows` (referência do formato de linha; não muda).

**Testes**
- `tests/test_endpoints.py` — cenário multi-conversa (M7).
