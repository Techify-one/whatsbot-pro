# Plano 33 — Correções de WebSocket: badge de agente não atualiza + última mensagem "às vezes" some

> **Status:** PLANEJAMENTO · **Data:** 2026-07-06 · **Escopo:** pequeno/médio (1 broadcast faltando no handoff da IA · 1 ressync de thread no reconnect · 1 heartbeat no cliente WS · 1 correção de dedup de conteúdo idêntico · 1 guard de race de perda de mensagem, independente/por último). **Sem migration** (não toca schema).
> **Origem:** teste do usuário no roteamento hub-and-spoke — "o websocket parece com problemas: não atualiza o agente atribuído (badge) e a última mensagem que o cliente manda às vezes não aparece (às vezes funciona, às vezes não)". Refinado por investigação multiagente adversarial (13 agentes, rastreio backend + frontend + infra WS, cada hipótese verificada contra o código) — todas as afirmações abaixo vêm com `arquivo:linha` verificado nesta sessão.
> **O quê/por quê:** são **dois** sintomas com **duas causas-raiz distintas**, nenhuma é "o WS em si":
> 1. **Badge (determinístico):** o handoff feito pela IA (`transferir_agente`) troca `active_agent_key` no banco mas **não emite nenhum evento WS** — o verbo `conversation.agent_changed` está fora do mapa de projeção. O caminho **manual** (reatribuir pela UI) emite `conversation_updated` e por isso atualiza ao vivo. É a assimetria que prova a causa.
> 2. **Última mensagem (intermitente):** a mensagem é **sempre salva** (por isso aparece no refresh), mas o único sinal ao vivo é **um** `new_message` sem replay; num buraco de conexão (sleep/troca de rede/blip meio-morto que não dispara `onclose`) o evento se perde e o reconnect **só ressincroniza a sidebar, nunca o thread aberto**. Uma causa secundária estreita (dedup de conteúdo idêntico) some balões repetidos ("ok"/"ok").
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima. **Verde a cada fase.** **Um refactor por commit.** F1 (badge) e F2 (ressync) são independentes e de maior impacto — comece por elas.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-06) | Consertar o badge **reusando o evento `conversation_updated` que o frontend já consome**, emitindo-o do handoff da IA com `active_agent_key` — NÃO criar um evento WS novo. | F1 é backend-only: um `broadcast('conversation_updated', {...active_agent_key})` após `set_agent`. Zero mudança no consumidor da sidebar/board. |
| **D2** ✅ (2026-07-06) | A correção **principal** da última mensagem é **ressincronizar o thread aberto no reconnect** (hoje só a sidebar é refetchada). Heartbeat e `close()` no prune são **reforço**, não o conserto. | F2 (linchpin) primeiro; F3 (heartbeat + prune-close) depois. Sem F2, heartbeat sozinho não elimina o "só no refresh". |
| **D3** ✅ (2026-07-06) | Corrigir o dedup de conteúdo idêntico **escopando-o a balões OTIMISTAS** (sem `msg_id`), não descartando inbound legítimo que traz `msg_id`. | F4 troca `findDuplicateIndex` por um casamento só-otimista — preserva o colapso do echo do operador. |
| **D4** ✅ (2026-07-06) | O **race pop-then-cancel** (perda real de mensagem no orquestrador) é um bug **de verdade porém independente** e de **polaridade inversa** ao sintoma do usuário (a msg pisca e some do DB também). Fica **em escopo neste plano** como F6 (P2), mas **independente** e executado **por último**. | F6 não bloqueia F1–F5 e pode rodar em qualquer ordem depois delas. Guard via flag `state.processing`, não re-enfileiramento (evita duplicar). |
| **Princípio fixo** | Mudança **aditiva** e best-effort: um broadcast a mais nunca pode derrubar o handoff; um refetch a mais nunca pode quebrar o thread. Nada de novo estado de navegação; nada de schema. | Todos os try/except defensivos; sem migration; painel só ganha dados que já sabe renderizar. |

---

## 1. Resumo executivo

O painel é dirigido por eventos WS de granularidade fina (`new_message`, `conversation_updated`, `conversation_upsert`, …). Dois pontos furam essa malha:

- **Badge:** a troca de agente pela **IA** não tem broadcast — o verbo de domínio `conversation.agent_changed` **não está** no mapa `_LIFECYCLE_WS_EVENT` ([ws_projections.py:45-54](../app/services/ws_projections.py)), então o projetor retorna sem emitir ([:68-71](../app/services/ws_projections.py)). A troca **manual** emite `conversation_updated` com `active_agent_key` ([conversation_service.py:436](../app/services/conversation_service.py)) e por isso atualiza ao vivo. **Determinístico** (nunca atualiza no handoff da IA), não intermitente.
- **Última mensagem:** a mensagem inbound é sempre persistida pelo batch ([messaging_service.py:805-813](../app/services/messaging_service.py)); o único sinal ao vivo no thread aberto é **um** `new_message` otimista, sem replay ([message_ingest_service.py:466-479](../app/services/message_ingest_service.py)). Numa conexão meio-morta (o browser não dispara `onclose`), esse frame se perde e o reconnect **só refetcha a sidebar** ([useConversationWsEvents.js:83-86](../web/static/js/components/contacts/hooks/useConversationWsEvents.js)) — o thread aberto fica stale até F5/re-seleção. **Intermitente** por depender do estado da conexão. Some ainda uma causa estreita: dois inbounds de **conteúdo idêntico** (<30s, mesmo role) são **mesclados** em vez de anexados ([useConversationWsEvents.js:459](../web/static/js/components/contacts/hooks/useConversationWsEvents.js) + [messages.js:41-47](../web/static/js/services/messages.js)).

Os consertos são pequenos e independentes: F1 (badge) é 1 broadcast no backend; F2 (thread resync) é o linchpin no frontend; F3/F4 são reforços; F6 é um bug latente separado.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 Badge — o handoff da IA não emite WS (assimetria com o manual)
- **IA:** `transferir_agente.execute` faz `conversation_repo.set_agent(conv["id"], target)` — UPDATE puro, **sem broadcast** ([transferir_agente.py:111](../agent/tools/transferir_agente.py)) — e só emite o evento de domínio `emit_domain_sync(ConversationAgentChanged(...))` ([:119-125](../agent/tools/transferir_agente.py)).
- O DTO `ConversationAgentChanged` carrega `{conversation_id, from_agent, to_agent, reason, ts}` — **NÃO** tem `active_agent_key` ([domain/events.py:158-167](../domain/events.py)); vira o bus `conversation.agent_changed` ([:207](../domain/events.py)).
- O único projetor WS core consulta `_LIFECYCLE_WS_EVENT.get(event_name)` e, para `conversation.agent_changed`, **retorna `None`** → `return` sem `ws_manager.broadcast` ([ws_projections.py:68-71](../app/services/ws_projections.py)). O verbo está **deliberadamente ausente** do mapa (documentado em [:27-31](../app/services/ws_projections.py); o mapa tem só created/status_changed/assigned/ai_toggled/archived/updated/deleted, [:45-54](../app/services/ws_projections.py)).
- **Prova da assimetria — o caminho manual funciona:** `conversation_service.set_agent` ([:428-442](../app/services/conversation_service.py)) faz `_broadcast(deps, "conversation_updated", "conversation.updated", updated, ...)` ([:436](../app/services/conversation_service.py)); o `_broadcast` inclui `active_agent_key` no payload ([:88](../app/services/conversation_service.py)). Por isso reatribuir pela UI atualiza o badge ao vivo, e o handoff da IA não.
- **Consumidor de frontend já existe:** `useWebSocket` assina `conversation_updated` ([useWebSocket.js:40-48](../web/static/js/hooks/useWebSocket.js)); `applyConversationEvent` → `conversationPatch.js:65-77` patcha `ev.active_agent_key` ([:71](../web/static/js/services/conversationPatch.js)); o badge é renderizado a partir de `active_agent_key` na lista/board ([Attendances.js:248-259](../web/static/js/components/attendances/Attendances.js), AssigneeChip em [ContactList.js](../web/static/js/components/contacts/ContactList.js)).
- ⚠️ **Ressalva (escopo):** o painel lateral de detalhe `ConversationInfoPanel`/`AssigneePicker` **não** faz merge de `active_agent_key` vindo de `conversation_updated` (só mescla `custom_attributes` via `convAttrPatch`) — o rótulo **daquele painel** continuaria só atualizando no refresh, **igual ao gap do handoff manual** ([AssigneePicker.js:77-79](../web/static/js/components/contacts/AssigneePicker.js)). Ver P1.

### 2.2 Última mensagem — perda em gap de conexão, sem ressync do thread
- **Persistência garantida:** o batch salva a mensagem combinada via `add_message` ([messaging_service.py:805-813](../app/services/messaging_service.py)), independente de qualquer broadcast. Por isso a mensagem **aparece no refresh**.
- **Único sinal ao vivo:** um `new_message` otimista por mensagem crua, `role="user"`, **sem replay** ([message_ingest_service.py:466-479](../app/services/message_ingest_service.py)). O `add_message` do batch emite só `conversation_upsert` (sidebar), nunca `new_message` ([memory.py:240-271](../agent/memory.py)).
- **Reconnect só cobre a sidebar:** `onWsConnect` chama `scheduleListRefetch()` (refetch da **lista**) e **nada** para o thread aberto ([useConversationWsEvents.js:83-86](../web/static/js/components/contacts/hooks/useConversationWsEvents.js)). O `new_message` é **append-only** ao thread aberto desde o plano 28 ([:489-496](../web/static/js/components/contacts/hooks/useConversationWsEvents.js)) — sem re-load.
- **Cliente sem heartbeat:** `wsBus` só reconecta em `sock.onclose` (timer de 3s, [wsBus.js:27,32](../web/static/js/services/wsBus.js)). Numa conexão **meio-morta** (sleep do laptop, troca de rede, blip de NAT), o browser não dispara `onclose` → nunca reconecta. O uvicorn tem `ws_ping_interval` protocolar (~20s) que mitiga o NAT-idle puro e fecha sockets mortos no **servidor** em ~40s, mas o frame de close não chega ao cliente meio-morto → o cliente não aprende que caiu.
- **Prune do servidor não fecha o socket:** ao podar um socket lento (timeout de send), o `disconnect()` só remove da lista e **não** chama `websocket.close()` ([state.py:64-66,85-87](../server/state.py)) — o cliente continua achando que está vivo.

### 2.3 Última mensagem — dedup de conteúdo idêntico mescla em vez de anexar
- No efeito de `new_message`, uma mensagem genuinamente nova (msg_id não encontrado, `byId=-1`) cai no dedup de conteúdo `findDuplicateIndex` ([useConversationWsEvents.js:459-471](../web/static/js/components/contacts/hooks/useConversationWsEvents.js)); `sameMessage` casa **mesmo role E (mesmo ts OU mesmo content dentro de 30s)** ([messages.js:41-47](../web/static/js/services/messages.js)).
- Dois inbounds idênticos separados por **mais** que `message_batch_delay` (batches distintos → 2 linhas no DB) e **menos** que 30s → o 2º é **mesclado** no balão do 1º em vez de virar balão novo. Reprodução limpa de "última msg só depois do refresh" para "ok"/"ok", "?"/"?". Estreito (só conteúdo idêntico, mesmo role, <30s).

### 2.4 (independente) Race pop-then-cancel — perda real de mensagem (polaridade inversa)
- `_orchestrate` faz snapshot + **pop** de `pending_messages` para `items` ([messaging_service.py:1085-1089](../app/services/messaging_service.py)) e só **depois** aguarda o lock ([:1106](../app/services/messaging_service.py)) + sleep `ai_sequential_delay` (~2s, [:1110](../app/services/messaging_service.py)) + os awaits pré-save, persistindo o texto só em [:812](../app/services/messaging_service.py).
- `schedule_orchestrator` **cancela** a task rodando ([:738-744](../app/services/messaging_service.py)) porque `state.sending[key]` só vira `True` **depois** do LLM ([:692](../app/services/messaging_service.py) via [:850/:1015](../app/services/messaging_service.py)). Um inbound novo na janela pop→persist cancela a task e **descarta o `items` já retirado** → mensagem perdida (some do DB também). **Não é o sintoma do usuário** (a msg pisca ao vivo e some no refresh — inverso), mas é bug real.

### 2.5 Descartados na verificação (não perseguir)
| Candidato | Veredicto | Por quê |
|---|---|---|
| Conversa divergente dropar balão inbound (conversation_id ≠ aberto) | **REFUTADO** | `resolve_for_contact_ex` **reabre o MESMO id** no churn hub-and-spoke; só cria id novo se nunca houve conversa ([conversation_repo.py:246-254](../db/repositories/conversation_repo.py)). E o refresh escopa por conversa — se caísse em outra, o refresh também não mostraria. |
| Exceção "engolida" no `conversation_upsert` (Future não inspecionado) | **REFUTADO como causa** | `ws_manager.broadcast` nunca levanta (try/except por cliente, [state.py:68-88](../server/state.py)); o Future completa normal. Staleness exige o frame **não ser entregue** (rede), não exceção. |

---

## 3. Inventário / análise

| Item | Onde | O que falta | Abordagem | Sintoma | Risco | Esforço |
|---|---|---|---|---|---|---|
| I1 · broadcast do handoff da IA | [transferir_agente.py:111](../agent/tools/transferir_agente.py) | nenhum evento WS | após `set_agent`, `broadcast('conversation_updated', {conversation_id, contact_id, active_agent_key: target})` best-effort | badge | baixo | S |
| I2 · ressync do thread aberto no reconnect | [useConversationWsEvents.js:83-86](../web/static/js/components/contacts/hooks/useConversationWsEvents.js) | só a sidebar é refetchada | no `onWsConnect`, re-fetch das mensagens da conversa selecionada (reusar o loader da seleção), pulando o 1º connect | última msg | médio | S |
| I3 · heartbeat ping/pong no cliente | [wsBus.js:54-77](../web/static/js/services/wsBus.js) | sem ping; onclose-only | `setInterval` ~25s enviando `{action:'ping'}`, interceptar `pong` no `onmessage`, `sock.close()` se pong atrasar | última msg | médio | M |
| I4 · `close()` ao podar socket lento | [state.py:85-87](../server/state.py) | prune sem close | fechar o socket **só no laço de prune do broadcast** (best-effort, com timeout), NÃO no `disconnect()` compartilhado | última msg | baixo | S |
| I5 · dedup só contra balão otimista | [useConversationWsEvents.js:459](../web/static/js/components/contacts/hooks/useConversationWsEvents.js) | mescla inbound com msg_id | casar dedup só com entradas sem `msg_id` (otimistas) via `sameMessage` | última msg | baixo | S |
| I6 · guard do race pop-then-cancel | [messaging_service.py:738-744,1085-1124](../app/services/messaging_service.py) | cancela task com `items` já popado | flag `state.processing[key]` setada no pop e checada no `schedule_orchestrator` (não cancelar; tail spawna follow-up) | (independente) | médio | M |
| I7 · testes | [tests/test_endpoints.py](../tests/test_endpoints.py) + JS unit | cobertura zero | broadcast-assert do handoff (backend); JS unit do patch/dedup | ambos | baixo | M |

---

## 4. Mudanças de infraestrutura (por camada)

- **Backend/agent tool:** I1 (broadcast no `transferir_agente`).
- **Backend/infra WS:** I4 (`close()` no prune).
- **Backend/orquestrador:** I6 (guard `state.processing`) — independente.
- **Frontend/hooks:** I2 (ressync do thread), I5 (dedup otimista).
- **Frontend/wsBus:** I3 (heartbeat).
- **Testes:** I7.
- **Sem DB/migration.** Head Alembic (`0037_drop_ai_variables_category`) intacto.

---

## 5. Fases / Roadmap

```
WAVE 0  (independentes — maior impacto, 2 em paralelo)
   F1(badge: broadcast no handoff da IA)        F2(ressync do thread no reconnect)
        │  [backend-only, determinístico]             │  [frontend, linchpin da última msg]
        └───────────────── sem barreira ─────────────┘

WAVE 1  (reforços da última msg, 2 em paralelo)
   F3(heartbeat ping/pong + close no prune)     F4(dedup só contra balão otimista)
        │  [depende conceitualmente de F2]            │  [independente]

WAVE 2
   F5(testes)                    ← depende de F1..F4
   F6(guard race pop-then-cancel) ← INDEPENDENTE, por último (bug separado, D4/P2)
```

| Wave | Fase | Workstream | 🟢/🔴 | Sintoma | Pronto quando |
|---|---|---|---|---|---|
| 0 | F1 | backend (agent tool) | 🟢 | badge | handoff da IA atualiza o badge da sidebar/board ao vivo |
| 0 | F2 | frontend (hook) | 🟢 | última msg | reconnect re-carrega o thread aberto, não só a sidebar |
| 1 | F3 | frontend (wsBus) + backend (state) | 🟢 [após F2] | última msg | socket meio-morto é detectado e reconecta |
| 1 | F4 | frontend (hook) | 🟢 | última msg | "ok"/"ok" (<30s) aparecem como 2 balões ao vivo |
| 2 | F5 | testes | 🟢 [após F1..F4] | ambos | suíte verde + asserts novos |
| 2 | F6 | backend (orquestrador) | 🟢 [independente, por último] | (perda real) | inbound na janela pop→persist não é descartado |

---

### Fase F1 — Badge: broadcast `conversation_updated` no handoff da IA  🟢
**Objetivo:** paridade com o handoff manual — a troca de agente pela IA atualiza o badge ao vivo.
**Itens:**
- [sequencial] Em [agent/tools/transferir_agente.py](../agent/tools/transferir_agente.py), logo após `conversation_repo.set_agent(conv["id"], target)` ([:111](../agent/tools/transferir_agente.py)), adicionar broadcast **best-effort** reusando o evento que o front já consome:
  ```python
  try:
      from plugins.context import broadcast
      broadcast("conversation_updated",
                {"conversation_id": conv["id"],
                 "contact_id": ctx.contact.id,
                 "active_agent_key": target})
  except Exception:
      logger.debug("broadcast conversation_updated (handoff) falhou p/ conversa %s", conv["id"])
  ```
  Thread-safe a partir da worker do AGNO (o `broadcast` usa `run_coroutine_threadsafe`, [plugins/context.py](../plugins/context.py)). **Não** mutar/reusar o dict do `ConversationAgentChanged` (ele não tem `active_agent_key` e o dict é compartilhado no fan-out de plugins) — montar payload novo.
- [sequencial] Confirmar que `conversationPatch.js:71` lê `ev.active_agent_key` **top-level** (é o formato acima) e que o badge da lista/board reflete. Nenhuma mudança de frontend necessária para sidebar/board.
- [paralelo/opcional — ver P1] Se o "header/painel de detalhe" também precisar atualizar ao vivo: adicionar em `ConversationInfoPanel`/`AssigneePicker` o merge de `active_agent_key` vindo de `conversation_updated` (hoje só mescla `custom_attributes`).
**Pronto quando:** com a conversa aberta, um handoff da IA (`transferir_agente`) muda o chip do agente na sidebar/board **sem refresh**; o handoff manual continua funcionando (não regrediu). O card "trocou de agente" (system-notice) continua aparecendo.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** em [agent/tools/transferir_agente.py](../agent/tools/transferir_agente.py), logo após `conversation_repo.set_agent(conv["id"], target)`, adicionado um `broadcast("conversation_updated", {...})` best-effort (try/except), reusando o evento que o front já consome. Nenhuma mudança de frontend (sidebar/board já patcham via `conversationPatch.js`).
- **Como foi feito / decisões:** payload NOVO `{"conversation_id": conv["id"], "contact_id": ctx.contact.id, "active_agent_key": target}` (não muta o DTO `ConversationAgentChanged`, compartilhado no fan-out e sem `active_agent_key`). `broadcast` importado de `plugins.context` (thread-safe via `run_coroutine_threadsafe` a partir da worker do AGNO). O `emit_domain_sync(ConversationAgentChanged(...))` continua intacto. **P1: painel de detalhe NÃO incluído** (decisão P1 = opção a).
- **Problemas / pendências:** nenhuma. Verificado que `useWebSocket` roteia `conversation_updated` → `onConversationChanged` → `applyConversationEvent`, que `conversationPatch.js:71` lê `ev.active_agent_key` top-level, e que o matching casa por `conversation_id`/`contact_id` (ambos no payload).
- **Verificação:** testes de routing verdes após a mudança (`test_spoke_router_enforcement` + `test_routing_motivo`: 11 pytest; `test_routing_engine`: 26 standalone). Assert de broadcast dedicado em F5. Teste manual ao vivo pendente (validação do usuário).

---

### Fase F2 — Última msg: ressincronizar o thread aberto no reconnect (LINCHPIN)  🟢
**Objetivo:** qualquer reconexão WS recupera as mensagens perdidas no gap, não só a lista.
**Itens:**
- [sequencial] Em [useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js), no `onWsConnect` ([:83-86](../web/static/js/components/contacts/hooks/useConversationWsEvents.js)): além de `scheduleListRefetch()`, re-carregar o **thread aberto** — re-fetch das mensagens da conversa selecionada (reusar o mesmo loader da seleção; `selectedConvIdRef.current` / `selectedRef.current`). Pular o **1º connect** (mesmo padrão do `wsConnectedOnceRef` já usado para a sidebar) para não duplicar o load inicial.
- [sequencial] Garantir que o re-load do thread seja **idempotente** com o append otimista (reconcilia por `msg_id`, não duplica) — reusar o caminho de load do detalhe já existente, que já dedupa.
- [paralelo] Cobrir o caso de conversa aberta só por `conversation_id` (sem phone) — usar o loader por convId (evita o gap do bufKey `conv:${id}` citado na investigação).
**Pronto quando:** desconectar a rede por >5s com uma conversa aberta, mandar uma mensagem do cliente nesse intervalo, reconectar → a mensagem **aparece sozinha** no thread (sem F5). O append normal (sem gap) continua funcionando.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** extraído um callback ref-based `reloadOpenThread` em [useConversationSelection.js](../web/static/js/components/contacts/hooks/useConversationSelection.js) que re-carrega o thread aberto em background; fiado por [Contacts.js](../web/static/js/components/contacts/Contacts.js) até [useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js), onde o `onWsConnect` (após pular o 1º connect) agora chama `reloadOpenThreadRef.current()` além do `scheduleListRefetch()`.
- **Como foi feito / decisões:** `reloadOpenThread` reusa o MESMO loader da seleção (`getConversationMessages(convId)` → `shapeConvData`, senão `getContact(phone)`) e o MESMO dedup R12 (`isDuplicateMessage`) do merge pré/durante-fetch, então é idempotente com o append otimista. Lê refs (`selectedRef`/`selectedConvIdRef`) → estável ([]-dep) e safe em closure WS. **Não** é o `useEffect` da seleção reaproveitado: é um callback aditivo separado que NÃO liga o spinner, NÃO reseta o `openPanel` nem zera badges de não-lidas (reconnect não pode piscar/reordenar o thread). Cobre o caso conversa-só-por-convId via `bufKey` `conv:${id}`. O 1º connect é pulado pelo `wsConnectedOnceRef` já existente (mesmo guard da sidebar).
- **Problemas / pendências:** nenhuma. `node --check` OK nos 3 arquivos; 116 JS units existentes seguem verdes.
- **Verificação:** manual (DevTools offline toggle) — pendente validação do usuário; sem harness de socket real na suíte (P3).

---

### Fase F3 — Última msg: heartbeat ping/pong no cliente + `close()` no prune  🟢 [reforço, após F2]
**Objetivo:** detectar o socket meio-morto que não dispara `onclose` e forçar o reconnect (que agora ressincroniza — F2).
**Itens:**
- [sequencial] **Heartbeat no `wsBus`** ([wsBus.js](../web/static/js/services/wsBus.js)): em `sock.onopen`, iniciar `setInterval` (~25s) enviando `{action:'ping'}`; rastrear `lastPongAt`; se `now - lastPongAt > timeout` (ex.: 2 ciclos), chamar `sock.close()` (dispara `onclose` → reconnect de 3s já existente). **Interceptar o `pong` DENTRO do `onmessage`** ([:62](../web/static/js/services/wsBus.js)) antes do fan-out (o fan-out não tem subscriber para `pong` e o ignoraria). Limpar o interval em `onclose` e no teardown. O servidor já responde `pong` a `{action:'ping'}` ([server/routes/websocket.py:59-60](../server/routes/websocket.py)) — **sem** mudança de backend aqui.
- [sequencial] **`close()` no prune** ([state.py:85-87](../server/state.py)): ao remover um socket lento no laço de broadcast, chamar `await websocket.close()` best-effort (try/except, idealmente com timeout curto para um close travado não segurar o fan-out). **NÃO** adicionar `close()` ao `ConnectionManager.disconnect()` compartilhado (ele também roda no `WebSocketDisconnect` limpo, onde um close extra é redundante/pode levantar).
**Pronto quando:** simular half-open (DevTools → throttling offline sem fechar a aba, ou suspender/retomar) → dentro de ~1 heartbeat o cliente reconecta e (via F2) recupera o thread. Conexões saudáveis não sofrem reconnect espúrio.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** (a) heartbeat no cliente em [wsBus.js](../web/static/js/services/wsBus.js); (b) `close()` best-effort no laço de prune do broadcast em [server/state.py](../server/state.py).
- **Como foi feito / decisões:** heartbeat — `_startHeartbeat(sock)` iniciado no `sock.onopen`; `setInterval` de **25s** (`HEARTBEAT_INTERVAL_MS`); a cada tick, se `Date.now() - _lastPongAt > 40s` (`HEARTBEAT_TIMEOUT_MS`, tolera 1 pong perdido e fica acima do ping protocolar ~20s do uvicorn → sem reconnect espúrio em link saudável), `sock.close()` (dispara `onclose` → reconnect de 3s → F2). O `pong` é interceptado **dentro do `onmessage`, ANTES do fan-out** (`if (msg.event === 'pong') { _lastPongAt = Date.now(); return; }`) — o fan-out não tem subscriber pra `pong`. Interval limpo em `onclose` e em `_teardown` (`_stopHeartbeat`). Prune — no laço `for ws in dead` do `broadcast`, após `self.disconnect(ws)`, `await asyncio.wait_for(ws.close(), timeout=1.0)` em try/except. **NÃO** adicionado ao `disconnect()` compartilhado (que também roda no `WebSocketDisconnect` limpo). Backend do pong já existia ([websocket.py:59-60](../server/routes/websocket.py)) — sem mudança lá.
- **Problemas / pendências:** nenhuma. O `setInterval` de 25s não dispara nos JS units curtos e o teardown limpa o interval (sem vazar timer).
- **Verificação:** `node --check` + 6 units do `wsBus.test.js` verdes; `state.py` parseia e `test_tool_call_broadcast` verde. Teste manual half-open (offline toggle/suspend-resume) pendente (validação do usuário).

---

### Fase F4 — Última msg: dedup só contra balão otimista  🟢 [independente]
**Objetivo:** dois inbounds de conteúdo idêntico (<30s) aparecem como 2 balões, sem quebrar o colapso do echo do operador.
**Itens:**
- [sequencial] Em [useConversationWsEvents.js:459](../web/static/js/components/contacts/hooks/useConversationWsEvents.js), trocar `findDuplicateIndex(message, prev.messages)` por uma busca que **só case entradas otimistas** (sem `msg_id` estável / com `_localId`), ex.:
  ```js
  const dupIdx = prev.messages
    ? prev.messages.findIndex(m => !m.msg_id && sameMessage(m, message))
    : -1;
  ```
  (importar `sameMessage` de [services/messages.js](../web/static/js/services/messages.js)). Assim um inbound "ok" com `msg_id` B **não** absorve o "ok" já assentado (que tem `msg_id` A) → **APPEND**; e o echo do operador (mídia/texto), cujo otimista não tem `msg_id`/tem `_localId`, **ainda colapsa** — sem regressão de balão duplicado.
- [sequencial] Aplicar o **mesmo escopo** ao buffer de carregamento (`isDuplicateMessage` contra `pendingWsMessages`, [:435-437](../web/static/js/components/contacts/hooks/useConversationWsEvents.js)).
**Pronto quando:** mandar "ok" e depois "ok" com ~5s de intervalo (batches distintos) → **2 balões** ao vivo; enviar mídia pelo operador → **1 balão** (echo colapsa no otimista). JS unit cobre ambos.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** helper `optimisticDupIndex(message, list)` que só casa entradas **sem `msg_id`** via `sameMessage`, aplicado aos DOIS call sites em [useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js): o dedup do thread aberto (antes `findDuplicateIndex`) e o dedup do buffer de carregamento (antes `isDuplicateMessage`). **Nota (F5):** o helper foi depois movido para o módulo puro `services/messages.js` (exportado) para ser testável com `node --test`; o hook o importa de lá.
- **Como foi feito / decisões:** framing (a) do plano (`!m.msg_id && sameMessage`), escolhido sobre "pular dedup quando o incoming tem msg_id" porque preserva o colapso do echo do operador: o otimista EXISTENTE (sem msg_id) é a entrada casada, e o echo incoming (com msg_id) funde nele (merge de msg_id/status em seguida). Dois inbounds distintos "ok"/"ok" (cada um com seu msg_id) não casam → APPEND. O caminho de reconciliação por msg_id (linhas ~455-467) fica intacto. NÃO alterei os merges buffer-vs-servidor (selection hook + reloadOpenThread), que dedupam contra dados autoritativos do DB por conteúdo — correto lá.
- **Problemas / pendências:** o buffer de load só contém WS messages (todas com msg_id), então o dedup do buffer vira efetivamente no-op — seguro (o merge posterior contra o servidor dedupa; não há double-delivery de mesmo msg_id no bus). Áudio/mídia do operador: o otimista tem `_localId`/sem `msg_id` → ainda colapsa.
- **Verificação:** 116 JS units verdes + `node --check`. Unit dedicado do dedup só-otimista em F5.

---

### Fase F5 — Testes  🟢 [após F1..F4]
**Objetivo:** travar o comportamento onde há harness.
**Itens:**
- [paralelo] **Backend (pytest, [tests/test_endpoints.py](../tests/test_endpoints.py) ou unit dedicado):** com `ws_manager.broadcast` mockado/capturado, um `transferir_agente.execute` bem-sucedido emite `conversation_updated` com `active_agent_key == target` (F1). Reusar o padrão de mock de GOWA/LLM da suíte.
- [paralelo] **JS unit** (padrão [conversationPatch.test.js](../web/static/js/services/conversationPatch.test.js)/[conversationRows.test.js](../web/static/js/services/conversationRows.test.js)): dedup só-otimista (F4) — inbound com `msg_id` distinto e mesmo conteúdo faz APPEND; echo sem `msg_id` colapsa.
- [paralelo] Nota no plano: o comportamento de reconnect (F2/F3) é validado **manualmente** (offline toggle) — sem harness de socket real na suíte.
**Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde no `WHATSBOT_TEST_DB_URL`; os JS units passam; o teste manual de reconnect documentado no status.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** (backend) novo `tests/test_transfer_broadcast.py` com 2 testes — o handoff da IA emite `conversation_updated` com `active_agent_key==target`+`conversation_id`+`contact_id` (F1), e um broadcast que levanta não derruba o handoff (best-effort). (JS) o helper `optimisticDupIndex` foi **movido de volta para o módulo puro** `services/messages.js` (exportado) e o hook passou a importá-lo — para ficar testável com `node --test` (o hook importa preact e não é importável no runner). 5 units novos em `messages.test.js` cobrindo APPEND de 2 inbounds com msg_id distinto, colapso do echo otimista sem msg_id, e o guard de regressão vs `findDuplicateIndex`.
- **Como foi feito / decisões:** teste backend chama `transferir_agente.execute` direto (sem app/loop) e monkeypatcha `plugins.context.broadcast` (o `execute` faz `from plugins.context import broadcast` em call-time, então o patch é visto). Fixture `transfer_world` mirrora o `routing_world` dos testes de routing (seed_default_agent + roteador33/comercial33 + conversa aberta no roteador). NÃO toquei em `tests/test_endpoints.py` (arquivo multi-lane) — teste em arquivo próprio.
- **Problemas / pendências:** nenhuma. Reconnect (F2/F3) segue validação manual (P3) — sem harness de socket real.
- **Verificação:** `venv/bin/python -m pytest tests/test_transfer_broadcast.py -q` → 2 passed; `node --test web/static/js/services/*.test.js` → 120 passed (17 em messages.test.js). Suíte pytest completa: ver checklist final.

---

### Fase F6 — Guard do race pop-then-cancel  🟢 [independente, por último — D4/P2]
**Objetivo:** impedir a **perda real** de uma mensagem que chega na janela pop→persist do orquestrador.
**Itens:**
- [sequencial] Adicionar `state.processing: dict[tuple, bool]` em [server/state.py](../server/state.py) (+ property shim em `ServerState`, análogo a `sending`).
- [sequencial] Em `_orchestrate`, **logo após o pop** ([messaging_service.py:1089](../app/services/messaging_service.py)), setar `state.processing[key]=True`; limpar no `finally` ([:1121-1124](../app/services/messaging_service.py)) com `state.processing.pop(key, None)`.
- [sequencial] Em `schedule_orchestrator` ([:738-744](../app/services/messaging_service.py)), **estender o guard**: `if state.sending.get(key) or state.processing.get(key): return` — **não cancelar**; a mensagem nova fica em `pending_messages` e o tail já existente ([:1115-1118](../app/services/messaging_service.py)) spawna um orquestrador follow-up que a processa como batch fresco.
- ⚠️ **Não** re-enfileirar `items` no `except CancelledError` (um cancel após o persist [:812](../app/services/messaging_service.py) mas antes de `sending=True` duplicaria a mensagem). O guard por flag é a via correta. Mídia é persistida item-a-item ([:897](../app/services/messaging_service.py)) → o guard cobre ambos; mover só o pop **não** basta.
**Pronto quando:** enviar 2 mensagens do cliente com <2s entre elas (dentro do `ai_sequential_delay`) → **nenhuma** some (ambas persistem e são respondidas), sem resposta/mensagem duplicada.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** novo dict `state.processing: dict[tuple, bool]` em [server/state.py](../server/state.py) (`MessagingState` + property shim em `AppState`, análogo a `sending`). Em [app/services/messaging_service.py](../app/services/messaging_service.py): `state.processing[key]=True` logo após o pop do batch (`_orchestrate`), limpo em `state.processing.pop(key, None)` no `finally`; e o guard do `schedule_orchestrator` estendido para `if state.sending.get(key) or state.processing.get(key): return` (não cancela).
- **Como foi feito / decisões:** o guard cobre a janela pop→persist que a flag `sending` (só a fase de SEND) não cobria — um inbound nessa janela cancelava a task e descartava o `items` já popado (mensagem perdida do DB). Com o guard, o inbound fica em `pending_messages` e o **tail** existente (spawn de follow-up se `pending_messages.get(key)`) o processa como batch fresco. **Correção de corretude verificada:** entre o tail (spawn) e o `finally` que limpa `processing` NÃO há `await` — então o webhook (task separada, só interleava em await points) não pode inserir uma mensagem "cega" nessa janela; ela ou chegou antes (tail spawna) ou chegará depois de limpar (agenda orquestrador fresco normalmente). **NÃO** re-enfileirei `items` no `except CancelledError` (duplicaria após o persist). Clear posto ANTES do pop de `processing_tasks` no finally.
- **Problemas / pendências:** sem teste automatizado dedicado (o race é timing-dependent; o plano não pede harness — cenário manual "2 msgs <2s"). Nenhuma regressão nos testes existentes que exercitam o orquestrador.
- **Verificação:** `state.py`/`messaging_service.py` parseiam; 26 testes de `test_webhook_characterization` + `test_conversation_race` + `test_human_gate` (9) verdes após a mudança.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Broadcast no handoff (F1) | Um erro no broadcast derrubar o `transferir_agente` | `try/except` best-effort (o próprio `emit_domain_sync` já é defensivo); montar payload novo, não mutar o DTO compartilhado. |
| Payload sem `active_agent_key` | Reusar o DTO cru (`to_agent`) não atualiza o badge (o front lê `active_agent_key`) | Emitir `active_agent_key=target` explicitamente (D1). |
| Ressync do thread (F2) | Refetch a cada reconnect pesar em conexões instáveis | Debounce/pular 1º connect; reusar loader que já dedupa; é 1 fetch por reconnect, não por evento. |
| Heartbeat (F3) | `pong` cair no fan-out sem subscriber e ser ignorado | Interceptar `pong` DENTRO do `onmessage` antes do fan-out; limpar interval em onclose/teardown. |
| `close()` no prune (F3) | Fechar no `disconnect()` compartilhado quebra o path de `WebSocketDisconnect` limpo | Fechar **só** no laço de prune do broadcast, best-effort + timeout. |
| Dedup otimista (F4) | Afrouxar demais reintroduz balão duplicado do echo | Casar só entradas **sem `msg_id`** (otimistas); manter o cleanup por msg_id do composer. |
| Guard `state.processing` (F6) | Deadlock se a flag não for limpa | Limpar no `finally`; o tail follow-up cobre a mensagem pendente sem cancel. |
| Painel de detalhe (P1) | Usuário achar que "ainda não atualiza" se olhar o `ConversationInfoPanel` | Decidir P1: incluir o merge lá ou documentar que sidebar/board é a fonte ao vivo. |

---

## 7. Perguntas em aberto

**P1 — O "header/painel de detalhe" precisa atualizar o agente ao vivo?**
✅ DECIDIDO (2026-07-06): **NÃO por enquanto** — opção (a). F1 conserta o badge da sidebar/board; o painel lateral `ConversationInfoPanel`/`AssigneePicker` fica como está (mesmo gap do handoff manual). O item "paralelo/opcional" de F1 sobre esse painel **não** será feito nesta rodada. Reavaliar se o usuário passar a depender daquele painel para ver o agente ao vivo.

**P2 — F6 entra neste plano ou vira plano próprio?**
✅ DECIDIDO (2026-07-06): **fica neste plano 33** como F6. É bug real, independente e de polaridade inversa ao sintoma relatado (D4) — não bloqueia F1–F5 e será executado **por último**, mas está **em escopo** (não vira plano 34).

**P3 — Harness de teste para reconnect (F2/F3)?**
✅ DECIDIDO: **manual** (offline toggle no DevTools). Não há harness de socket real na suíte; F5 cobre F1 (backend) e F4 (JS unit); F2/F3 ficam com teste manual documentado.

---

## 8. Checklist de verificação

- [ ] Handoff da IA (`transferir_agente`) atualiza o chip do agente na sidebar/board **ao vivo** (sem F5/refresh); manual continua ok.
- [ ] Card system-notice de "troca de agente" continua aparecendo (não regrediu).
- [ ] Com a conversa aberta: desligar a rede, cliente manda msg, religar → a mensagem **aparece sozinha** no thread.
- [ ] Socket meio-morto (suspend/resume ou offline toggle) reconecta dentro de ~1 heartbeat.
- [ ] Conexão saudável não sofre reconnect espúrio; sem loop de reconnect.
- [ ] "ok" + "ok" (~5s) → **2 balões** ao vivo; echo de mídia do operador → **1 balão**.
- [ ] (F6) 2 msgs do cliente <2s não perdem nenhuma; sem duplicação de resposta.
- [ ] `venv/bin/python -m pytest tests/ -q` verde no `WHATSBOT_TEST_DB_URL`; JS units do dedup passam.
- [ ] Nenhuma mudança de schema/migration; head Alembic `0037` intacto.
- [ ] Telas/cards afetados legíveis no **modo escuro** (nada de cor nova crua).

---

## Apêndice — arquivos-chave (por fase)

- **F1:** [agent/tools/transferir_agente.py](../agent/tools/transferir_agente.py) (`execute`/`set_agent`), [plugins/context.py](../plugins/context.py) (`broadcast`), ref. da assimetria [app/services/conversation_service.py](../app/services/conversation_service.py) (`set_agent`/`_broadcast`), consumidor [web/static/js/services/conversationPatch.js](../web/static/js/services/conversationPatch.js), [web/static/js/components/attendances/Attendances.js](../web/static/js/components/attendances/Attendances.js).
- **F2:** [web/static/js/components/contacts/hooks/useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js) (`onWsConnect`), loader da seleção em [web/static/js/components/contacts/hooks/](../web/static/js/components/contacts/hooks/).
- **F3:** [web/static/js/services/wsBus.js](../web/static/js/services/wsBus.js), [server/state.py](../server/state.py) (`ConnectionManager`/prune), [server/routes/websocket.py](../server/routes/websocket.py) (pong já existente).
- **F4:** [web/static/js/components/contacts/hooks/useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js), [web/static/js/services/messages.js](../web/static/js/services/messages.js) (`sameMessage`/`findDuplicateIndex`).
- **F5:** [tests/test_endpoints.py](../tests/test_endpoints.py), [web/static/js/services/conversationPatch.test.js](../web/static/js/services/conversationPatch.test.js), [web/static/js/services/conversationRows.test.js](../web/static/js/services/conversationRows.test.js).
- **F6:** [server/state.py](../server/state.py), [app/services/messaging_service.py](../app/services/messaging_service.py) (`_orchestrate`/`schedule_orchestrator`).
