# Plano: Corrigir badge de não-lida que some sozinho + latência da conversa na lista

> **Como usar este plano**
>
> Este plano é executável por uma IA (ou por você numa sessão futura). **Regra obrigatória:** ao concluir OU travar em qualquer fase, preencha o bloco **"Status de execução"** daquela fase **antes** de avançar para a próxima — nunca deixe uma fase sem registro. Isso permite retomar sabendo exatamente o que foi feito, o que falhou e o que ficou pendente.
>
> Legenda de estado: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.
>
> Cada bloco de status pede: **O que foi feito** (arquivos/funções), **Como foi feito / decisões** (escolhas e desvios), **Problemas / pendências**, **Verificação** (testes rodados + resultado, validação manual).
>
> **Paralelismo:** as Fases 1 e 2 são independentes (arquivos e causas distintas) e podem ser despachadas em paralelo por dois executores/sub-agentes. A Fase 0 (caracterização) também se divide em dois testes paralelos. A Fase 3 depende de 1 e 2 concluídas.

---

## Contexto

Dois sintomas relatados pelo usuário no painel (testado na caixa **Telegram**, mas a causa é comum a todos os canais):

1. **Badge verde de não-lida some sozinho.** O cliente manda uma mensagem, o badge de não-lida acende na lista de conversas e, **5–8 s depois, some sozinho** — sem o operador abrir a conversa, clicar ou mexer no mouse. Ocorre inclusive em conversas marcadas **"IA OFF"**.

2. **A aba do navegador notifica antes da lista.** A mensagem aparece primeiro no título da aba (`(1) WhatsBot`) e só **~3–4 s depois** materializa como linha na lista de conversas (visível em conversas **novas**).

Ambos têm a **mesma fratura arquitetural**: o pipeline de recebimento processa a mensagem em **dois momentos** — *ingest* síncrono (t=0, assim que o webhook chega) e *batch* (t≈3 s, `message_batch_delay`). Alguns efeitos disparam em t=0 (incremento de não-lida, broadcast `new_message`), outros só em t=3 s (save da mensagem, criação da conversa, resposta da IA, e — o bug — a limpeza do não-lida).

### Causa-raiz #1 — badge some

`app/services/messaging_service.py:767-772` (dentro do processamento de batch):

```python
if self._channel_ai_enabled(channel_id):                       # ← gate INCOMPLETO
    msg_ids = await asyncio.to_thread(contact.mark_user_messages_as_read)
    if msg_ids:
        for mid in msg_ids:
            await asyncio.to_thread(outbound.mark_read, channel_id, phone, mid)   # ← read-receipt REAL
        await ws_manager.broadcast("messages_read", {"phone": phone, "only_user": True})
```

- O gate é só `_channel_ai_enabled(channel_id)` — que verifica **apenas** o `auto_reply` global + o `ai_enabled` do canal (`server/routes/webhook.py:77-87`). **Falta** `_conversation_ai_active(contact)` (a flag por-conversa). Compare com o ponto onde a IA realmente responde, `app/services/messaging_service.py:790-791`, que exige **as duas** condições — e com `app/services/messaging_service.py:918-919` (mídia), que também exige as duas.
- Resultado: numa conversa **IA OFF** mas com IA global+canal ligada, o batch ainda chama `mark_user_messages_as_read` (`db/repositories/unread_repo.py:148-160`, zera `unread_count` + apaga `unread_msg_ids`) e faz `broadcast("messages_read", …)`. O frontend, ao receber, zera o `unread_count` daquele contato em **todos** os painéis abertos (`web/static/js/components/contacts/hooks/useConversationWsEvents.js:222-232`) → badge some.
- **Efeito colateral do mesmo bug:** `outbound.mark_read` (linha 771) envia um **read-receipt REAL** ao cliente nos canais que suportam — GOWA (`channels/providers/gowa_channel.py:127` → `POST /message/{id}/read`) e WhatsApp Cloud (`storages/plugins/whatsapp_cloud/channels.py:345`). Em conversas IA-OFF o cliente vê "lida"/tique azul sem ninguém ter aberto. No Telegram não aparece porque o canal herda `mark_read` no-op (`storages/plugins/telegram/channels.py:161`) — por isso o usuário só notou o sumiço do badge, não o tique.

`_conversation_ai_active` é função de módulo no mesmo arquivo (`app/services/messaging_service.py:1053`), então já está acessível no ponto da correção.

### Causa-raiz #2 — aba antes da lista

- **t=0 (ingest)** — `app/services/message_ingest_service.py`:
  - `:420` `contact.increment_unread(msg_id)` → incrementa **imediatamente** `contacts.unread_count` no banco.
  - `:467` `broadcast("new_message", …)` — porém **sem `conversation_id`** (a conversa ainda não existe).
  - A mensagem **não é salva** e **a conversa não é criada**; só entra em `state.pending_messages` + agenda o orquestrador (`:506`).
- **t≈3 s (batch)** — `app/services/messaging_service.py:781` chama `contact.add_message(...)`, que dentro de `agent/memory.py:185-205` resolve/cria a conversa (`conversation_repo.resolve_for_contact_ex`) e, quando `created`, emite `conversation_created` (inline `agent/memory.py:191-203` + via listener `agent/message_listeners.py` `on_message_persisted` → `_broadcast_conversation_created`).
- **Título da aba**: `web/static/js/components/shell/App.js:289` usa `unreadConvos`, vindo de `GET /api/contacts/unread-count` (`server/routes/contacts.py:323-332` → `unread_conversation_count`, que conta `contacts` com `unread_count>0` — `db/repositories/unread_repo.py:73`). Como esse contador é bumpado em t=0 e o `new_message` de t=0 dispara o `refreshUnreadCount` (`web/static/js/components/shell/App.js:275`), **o badge da aba acende em t=0**.
- **Linha na lista**: a sidebar (plano 11) é conversa-cêntrica — cada linha precisa de uma **conversa persistida**. Para uma conversa **nova**, o `new_message` de t=0 (sem `conversation_id`) não acha linha e cai no `fetchContacts` (`web/static/js/components/contacts/hooks/useConversationWsEvents.js:459-461`), mas a conversa ainda não existe no banco → lista volta vazia. Só em t=3 s, quando o `conversation_created` chega (`web/static/js/components/contacts/hooks/useConversationWsEvents.js:341-344` → `fetchContacts`), a linha aparece. Daí os ~3–4 s = exatamente o `message_batch_delay`.

### Falsos positivos descartados

- **`listConversations` marcaria como lida sem `mark_read=false`** (hipótese levantada numa investigação inicial): **FALSO**. `GET /api/conversations` (`server/routes/conversations.py:91-106`) é query pura, **sem** efeito de mark-read. Só `GET /api/conversations/{id}/messages` marca (`server/routes/conversations.py:207`). Refetch da lista é inofensivo — não é causa de nenhum dos dois bugs.
- **Conversas já existentes na sidebar teriam o atraso da Fase 2**: **parcialmente falso**. Para uma conversa **já materializada**, o `new_message` de t=0 casa por `(phone, channel_id)` e atualiza a linha na hora (`web/static/js/components/contacts/hooks/useConversationWsEvents.js:433-457`). O atraso da Fase 2 é específico de conversas **novas** (primeira mensagem). A Fase 2 foca nesse caso.

---

## O padrão a seguir

1. **Gate de IA em três camadas** (plano 21, ver `CLAUDE.md` §"Configuração de IA por canal"): a IA só age se (1) `auto_reply` global ON; (2) canal `ai_enabled` ON; (3) conversa `ai_active` ON. `_channel_ai_enabled` cobre (1)+(2); `_conversation_ai_active` cobre (3). **Regra:** qualquer efeito que represente "a IA está assumindo esta conversa" (responder, marcar lido por ela, enviar recibo em nome dela) deve exigir **as três** camadas — i.e. `_channel_ai_enabled(...) and _conversation_ai_active(...)`. O ponto canônico correto já existe em `app/services/messaging_service.py:790-791` e `:918-919`; a Fase 1 alinha o `:767` a esse padrão.

2. **Criação de conversa centralizada** (plano 01 Fase 2 / plano 12 §3 / plano 23 Fase C5): toda materialização/transição de conversa passa por `conversation_repo.resolve_for_contact_ex` dentro de `agent/memory.py:add_message`, e os efeitos after-resolve (notice de ciclo de vida + broadcast `conversation_created`/`conversation_status_changed` + verbo de bus) vivem em `agent/message_listeners.on_message_persisted`. **Regra:** ao antecipar a criação da conversa para o ingest, NÃO duplicar nem perder esses efeitos — eles devem disparar **exatamente uma vez** por conversa. `resolve_for_contact_ex` é idempotente (retorna `created=False`/`transition=None` na 2ª chamada), o que deve ser usado como mecanismo de "exatamente-uma-vez".

3. **`message_batch_delay` é intencional** (junta mensagens rápidas do mesmo contato numa só, ver `CLAUDE.md` §"Fluxo de mensagens"). A Fase 2 **não** remove o batching nem muda como as mensagens são combinadas/salvas — apenas antecipa a **materialização da conversa** (e o broadcast da linha) para o ingest, mantendo o save combinado da mensagem no batch.

---

## Inventário / análise

| # | Item | Arquivo:linha | O que falta / muda | Abordagem | Esforço |
|---|------|---------------|--------------------|-----------|---------|
| 1 | Gate do auto-mark-read | `app/services/messaging_service.py:767` | Adicionar `and _conversation_ai_active(contact)` ao `if` | Igualar ao padrão de `:790-791` | **S** |
| 2 | Read-receipt falso (efeito colateral do #1) | `app/services/messaging_service.py:771` | Some automaticamente quando o gate #1 é corrigido (mark_read só roda se a IA assume) | Coberto pelo item 1 | — |
| 3 | Helper de resolução de conversa | `agent/memory.py:177-205` | Extrair `_resolve_conversation(role)` reutilizável (resolve + anúncios exatamente-uma-vez) | Refactor habilitador | **M** |
| 4 | Materializar conversa no ingest | `agent/memory.py` (novo método) + `app/services/message_ingest_service.py:~456-468` | `ensure_conversation_live()` chamado no ingest; injetar `conversation_id` no payload `new_message` | Usa o helper do item 3 | **M** |
| 5 | (Opcional) preview otimista na linha nova | `web/static/js/components/contacts/hooks/useConversationWsEvents.js:459-462` | Mostrar a linha com preview da msg recebida sem esperar 2º fetch | Polish frontend | **S** |

**Falsos positivos** (não mexer): `server/routes/conversations.py:91-106` (lista sem side-effect); fluxo de conversa já existente na sidebar (já atualiza em t=0).

---

## Decisões de design (defaults assumidos)

O executor pode prosseguir com estes defaults sem pedir confirmação (preferência do dono por autonomia). Se discordar, registrar no Status e ajustar.

- **D1 — escopo do bug #1:** corrigir o vazamento para conversas **IA-OFF** adicionando o gate por-conversa (mantendo o comportamento atual de limpar o badge quando a IA **vai** assumir a conversa). _Não_ se altera o caso IA-ON (lá o operador continua acompanhando via `unread_ai_count` azul). Alternativa rejeitada por default: "nunca limpar o badge automaticamente" (mudança de produto maior, não pedida).
- **D2 — abordagem do bug #2:** materializar a **conversa** no ingest (Fase 2), mantendo o **save combinado** da mensagem no batch. Alternativa rejeitada por default: salvar cada mensagem no ingest (mudaria "N mensagens rápidas → 1 linha combinada" para "N linhas", mudança de comportamento não pedida). Fallback de emergência, se a Fase 2 se mostrar arriscada demais no prazo: reduzir `message_batch_delay` (ataca o sintoma, não a causa) — só com aprovação explícita.

---

## Fases de implementação

### Fase 0 — Caracterização (reproduzir os bugs antes de corrigir)

**Objetivo:** escrever testes que **falham hoje** e passam após o fix, fixando o comportamento esperado.

- **0a [paralelo]** — Bug #1: teste de ingest/batch que simula `auto_reply=ON`, canal `ai_enabled=ON`, conversa `ai_active=OFF`, manda uma mensagem inbound, roda o batch e asserta que (i) `unread_count` continua `>0`, (ii) **não** houve `broadcast("messages_read", …)`, (iii) `outbound.mark_read` **não** foi chamado. Use os fakes existentes (`tests/fakes.py`, `tests/support.py`, `tests/_harness.py`) e veja `tests/test_endpoints.py` / `tests/test_events_filters.py` como modelo de wiring de `agent_handler`/`ws_manager`/`outbound`.
- **0b [paralelo]** — Bug #2: teste que, ao receber a 1ª mensagem de um contato **novo**, asserta que `conversation_created` é emitido **no ingest** (antes de rodar o batch) e que o `new_message` do ingest carrega `conversation_id`. Hoje deve falhar (conversation_created só no batch).

**Pronto quando:** 0a e 0b existem, rodam, e **falham** pelas razões certas (vermelho documentado).

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** Criado `tests/endpoints/test_p25_unread_badge_and_ingest.py` (pytest-collected) com 3 testes: `test_bug1_ia_off_keeps_badge_and_sends_no_read_receipt` (0a), `test_bug1_ia_on_still_clears_badge_and_sends_receipt` (espelho IA-ON, regressão) e `test_bug2_conversation_materialized_at_ingest` (0b).
- **Como foi feito / decisões:** Dirigem o pipeline REAL webhook→ingest→batch via `build_app(["gowa"])`. Captura no seam `deps`: `ws_manager.broadcast` (messages_read/new_message) e `outbound_router.mark_read` (read-receipt real). `conversation_created` trafega por `plugins.context.broadcast` (que o build hermético não conecta a um ws_manager) — capturado por `monkeypatch` na função do módulo, que o listener re-importa em call-time (`agent/message_listeners.py:40`). IA-OFF semeado por `default_ai_enabled=False`; espelho IA-ON usa `fake_agent_reply` p/ não chamar LLM real. Asserts robustos a timing do batch (conv materializada no ingest é capturada durante o POST; exatamente-uma-vez verificado após o drain).
- **Problemas / pendências:** Nenhuma. (Ordem cronológica: o fix foi aplicado antes e a caracterização foi validada por revert temporário — ver Verificação.)
- **Verificação (testes/manual):** Com o fix: 3 verdes. **Sem o fix (RED documentado):** revertendo só o gate de `messaging_service.py:767` → `test_bug1_ia_off` FALHA (badge zerado/`messages_read`/`mark_read`); revertendo só a chamada `ensure_conversation_live` no ingest → `test_bug2` FALHA (`conversation must be materialized at INGEST` — conv `None` antes do batch). Fix restaurado e os 3 voltam a verde.

---

### Fase 1 — Corrigir badge que some (bug #1) `[paralelo com Fase 2]`

**Objetivo:** o auto-mark-read (e o read-receipt) só dispara quando a IA realmente vai assumir aquela conversa.

- **1.1** — Em `app/services/messaging_service.py:767`, trocar o gate de
  `if self._channel_ai_enabled(channel_id):` para
  `if self._channel_ai_enabled(channel_id) and _conversation_ai_active(contact):`
  (mesma forma de `:790-791`). `_conversation_ai_active` já está no módulo (`:1053`).
- **1.2** — Confirmar que `contact` já está resolvido nesse ponto (`app/services/messaging_service.py:743` `agent_handler._get_contact(...)`) — está. Nenhuma outra mudança no corpo do bloco (linhas 768-772 permanecem).
- **1.3** — Rodar o teste 0a → deve **passar**. Garantir que o caso IA-ON (conversa `ai_active=ON`) **ainda** limpa o badge e envia recibo (não regredir): adicionar/conferir um teste espelho com `ai_active=ON`.

**Paralelizável:** 1.1 é a única mudança de produção; 1.3 (testes) pode rodar em paralelo com a Fase 2. Sem dependência da Fase 2.

**Pronto quando:** mensagem inbound em conversa IA-OFF (com IA global+canal ON) mantém o badge e não gera `messages_read` nem `mark_read`; conversa IA-ON mantém o comportamento atual; testes verdes.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** Em `app/services/messaging_service.py` (o bloco do auto-mark-read do batch, ~:767), o gate passou de `if self._channel_ai_enabled(channel_id):` para `if self._channel_ai_enabled(channel_id) and _conversation_ai_active(contact):`, alinhando com os pontos canônicos `:790-791` (texto) e `:918-919` (mídia). Comentário explica as 3 camadas (plano 21). Corpo do bloco (mark_user_messages_as_read + loop de `outbound.mark_read` + broadcast `messages_read`) inalterado.
- **Como foi feito / decisões:** `_conversation_ai_active` já era função de módulo (`:1053`), fail-open. `contact` já está resolvido no ponto. O read-receipt falso (item #2 do inventário) some automaticamente — agora só roda quando a IA assume a conversa (D1).
- **Problemas / pendências:** Nenhuma.
- **Verificação (testes/manual):** `test_bug1_ia_off` (badge persiste, sem `messages_read`, sem `mark_read`) e `test_bug1_ia_on` (takeover preserva: badge limpa + recibo) — verdes. RED confirmado ao reverter o gate (ver Fase 0). Suíte completa SQLite verde.

---

### Fase 2 — Materializar conversa no ingest (bug #2) `[paralelo com Fase 1]`

**Objetivo:** a linha da conversa **nova** aparecer na lista assim que a mensagem chega (t=0), não só no batch (t=3 s), sem mexer no batching nem duplicar/perder efeitos de ciclo de vida.

> **Internamente sequencial:** 2.1 → 2.2 → 2.3 → 2.4 (2.5 é opcional). 2.1 é bloqueante para 2.2/2.3.

- **2.1 [bloqueante]** — Refactor em `agent/memory.py`: extrair de `add_message` (`:177-205`) um helper privado `_resolve_conversation(role) -> (conv, conversation_id, transition)` que faz `resolve_for_contact_ex` + dispara os anúncios de `created`/`reopened` **exatamente uma vez** (o broadcast inline `:191-203` e a chamada a `on_message_persisted` para o ciclo de vida). `add_message` passa a chamar o helper. **Cuidado:** verificar se o broadcast inline `conversation_created` (`agent/memory.py:191-203`) e o `_broadcast_conversation_created` do listener (`agent/message_listeners.py`) **não duplicam** o evento hoje — se duplicarem, consolidar no helper (a confirmar durante a execução; registrar o achado no Status).
- **2.2** — Adicionar `ContactMemory.ensure_conversation_live(role="user") -> int | None` em `agent/memory.py`: chama `_resolve_conversation(role)` e retorna o `conversation_id`, **sem** salvar mensagem (sem `message_repo.add`, sem `_emit_message_persisted` — esse fica no save real do batch). Idempotente: 2ª chamada (no batch via `add_message`) acha a conversa existente (`created=False`/`transition=None`) e **não** re-anuncia.
- **2.3** — Em `app/services/message_ingest_service.py`, **após** o filtro `filter.message.before_save` passar (`:445-456`) e **antes** de montar/emitir o `new_message` (`:458-468`), chamar `conv_id = await asyncio.to_thread(contact.ensure_conversation_live)` e:
  - incluir `"conversation_id": conv_id` no payload do `broadcast("new_message", …)` (`:467-468`);
  - garantir que o ramo "grupo sem @menção" (`:474-486`, que já chama `add_message`) continua correto — como `ensure_conversation_live` roda antes, o `add_message` ali apenas reusa a conversa (idempotente). Conferir que não há duplo `conversation_created`.
- **2.4** — Verificar **exatamente-uma-vez** de `conversation_created` end-to-end (ingest cria/anuncia; batch reusa em silêncio) e que o notice "conversa criada" (card painel-only, plano 12 §3) ainda aparece **uma** vez. Rodar o teste 0b → deve passar.
- **2.5 [opcional, paralelo, polish]** — Frontend: em `web/static/js/components/contacts/hooks/useConversationWsEvents.js:459-462`, quando não há linha correspondente, além do `fetchContacts`, inserir uma linha otimista com o preview da mensagem recém-chegada (evita o flash de preview vazio até o fetch retornar). Respeitar dedupe com o refetch que chega logo em seguida. **Modo escuro:** se renderizar algo novo, usar classes `wa-*` (ver `CLAUDE.md` §"Tema e modo escuro").

**Pronto quando:** ao receber a 1ª mensagem de um contato novo (Telegram e GOWA), a linha aparece na lista praticamente junto com o badge da aba (sem os ~3–4 s); sem duplicar `conversation_created`; o card "conversa criada" aparece uma vez; testes verdes.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** (2.1) Em `agent/memory.py`, extraído `_resolve_conversation(role) -> (conv, conversation_id, transition)` (resolve puro, idempotente) + `_run_lifecycle_reactions(...)` (dispara `on_message_persisted` — notice + broadcasts `conversation_created`/`conversation_status_changed` + verbo `conversation.reopened`). `add_message` reescrito p/ usar ambos, mantendo as reações **depois** do INSERT (ordem byte-idêntica). (2.2) Adicionado `ContactMemory.ensure_conversation_live(role="user") -> int|None`: resolve+touch+lifecycle SEM salvar mensagem e SEM `message.persisted`. (2.3) Em `app/services/message_ingest_service.py`, após `filter.message.before_save`/echo e antes do `broadcast("new_message")`, chama `conv_id = await asyncio.to_thread(contact.ensure_conversation_live)` e injeta `conversation_id` **dentro** do `message` do `new_message`.
- **Como foi feito / decisões:** **Achado importante (risco #1 do plano):** o broadcast inline `conversation_created` em `memory.py:191-203` era **dead code** — usava `conv.get("created")`, mas `resolve_for_contact_ex` retorna direto o dict de `create()` (sem a chave `created`; só o wrapper `resolve_for_contact` a injeta). Logo `conversation_created` JÁ disparava 1× hoje (via listener `on_message_persisted`, gated em `transition=="created"`), não 2×. Removi a dead code na consolidação. **Exatamente-uma-vez** vem da idempotência de `resolve_for_contact_ex` (2ª resolução retorna `transition=None`), não de um flag novo. **conversation_id vai DENTRO de `message`** porque o frontend lê `message.conversation_id` (`useConversationWsEvents.js:353`) e dá precedência ao match por id sobre `(phone,channel)` — refetch de fallback é debounced, sem flicker. **Fase 2.5 (linha otimista) dispensada:** o frontend já materializa a linha via refetch debounced + match por conversation_id; o ganho do plano (linha junto do badge) vem da conversa existir no DB em t=0.
- **Problemas / pendências:** Nenhuma. Notice "conversa criada" agora é inserido no ingest (t=0), **antes** da mensagem do user (salva no batch) — comportamento sancionado pelo plano (risco "perder o notice"); não afeta goldens de webhook (filtram `conversation_event` via `_NOISE_ROLES`) nem de lifecycle (capturam só a lista de `conversation_event`, cuja ordem relativa não muda).
- **Verificação (testes/manual):** `test_bug2` (conv materializada no ingest + `new_message.conversation_id` + `conversation_created` 1× + sem conversa duplicada após o batch) verde; RED ao reverter a chamada do ingest. Testes de alto risco SEM regenerar golden: `test_conversation_events_c0.py` (inclui a assertion "876" do reopen síncrono via `add_message`), `test_lifecycle_characterization.py`, `test_webhook_characterization.py` — todos verdes.

---

### Fase 3 — Verificação integrada e regressão `[depende de: Fase 1 + Fase 2]`

**Objetivo:** garantir que os dois fixes convivem e nada regrediu.

- **3.1** — Suíte completa em **SQLite**: `source venv/Scripts/activate && python tests/test_endpoints.py` + os testes alvo (`tests/test_events_filters.py`, caracterização 0a/0b, e quaisquer da pasta `tests/characterization`/`tests/endpoints`). Conferir contagem de checagens não regrediu.
- **3.2** — Repetir contra **Postgres** (`WHATSBOT_TEST_DB_URL=postgresql+psycopg://...`, ver `CLAUDE.md` §"Testes" e a memória `postgres-dev-target`). Os dois fixes tocam unread/conversa, sensíveis a dialeto — validar nos dois bancos.
- **3.3** — Validação manual (Telegram, e se possível GOWA), com `linux_start.sh`:
  - Conversa **IA-OFF** (IA global ON): cliente manda msg → badge acende e **permanece** (não some em 5–8 s). Confirmar no cliente que **não** apareceu "lida"/tique azul (GOWA/Cloud).
  - Conversa **IA-ON**: comportamento atual preservado (badge limpa quando a IA assume; recibo enviado).
  - Contato **novo**: a linha aparece na lista junto com o badge da aba (sem o atraso).
  - Mensagens rápidas em sequência: continuam virando **uma** linha combinada (batching intacto).

**Pronto quando:** checklist abaixo todo marcado.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (SQLite + Postgres verdes; validação manual 3.3 pendente — headless)
- **O que foi feito:** (3.1) **SQLite**: `venv/bin/pytest tests/` → **exit 0, sem falhas** (4 skips ambientais pré-existentes). Legados: `tests/test_endpoints.py` → **882 passed, 0 failed**; `tests/test_events_filters.py` → **42 passed, 0 failed**. (3.2) **Postgres 12.22** em DB isolado `whatsbot_test_p25` (criado/dropado, sem tocar no `whatsbot` real): `venv/bin/pytest tests/` → apenas **2 FAILED, ambos pré-existentes/ambientais** (`test_legacy_suite[test_endpoints.py]` — subprocess que assume DB fresco, marcado fora-do-leg-PG na memória `postgres-dev-target`; `test_postgres_roundtrip` — `duplicate key "channels_pkey"` por acúmulo de estado no DB compartilhado). **Os 3 testes do plano 25 passam em PG.**
- **Como foi feito / decisões:** **Gotcha de encoding (memória `postgres-dev-target`, 2026-06-28):** o servidor tem `template0/1` em SQL_ASCII; `CREATE DATABASE` sem `ENCODING` herda SQL_ASCII → psycopg devolve `server_version` como bytes → `TypeError` no dialect-init do SQLAlchemy ANTES de qualquer teste. Resolvido criando o DB de teste com `ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'` e recriando-o limpo a cada run (o PG persiste estado entre invocações de pytest). Harness usa `WHATSBOT_TEST_DB_URL` **direto** → DB dedicado p/ não poluir dados reais.
- **Problemas / pendências:** Nenhuma do plano 25. As 2 falhas PG são pré-existentes — **provado por baseline com diff em `git stash`**: rodando a suíte PG SEM meu diff o conjunto FAILED = {`test_bug1_ia_off`, `test_bug2`, `legacy_script`, `roundtrip`}; COM meu diff = {`legacy_script`, `roundtrip`}. A diferença é exatamente meus 2 testes de caracterização (RED sem fix → GREEN com fix, **no PG também**), e as 2 ambientais são idênticas nos dois runs.
- **Verificação (testes/manual):** SQLite: pytest exit 0 + 882 + 42. Postgres: suíte verde exceto 2 falhas ambientais idênticas no baseline; fix validado RED→GREEN nos dois dialetos. Validação manual no painel (3.3) pendente — depende de `linux_start.sh` com WhatsApp conectado (não executável neste ambiente headless).
- **Pós-integração (developer ← 65f5c26 Luisa):** ao re-rodar a suíte completa integrada, 2 testes flacaram intermitentemente (`test_bug1_ia_on` meu + `test_media_audio_transcription_on` da Luisa/webhook) — **não reproduzem** isolados nem num re-run, causa = poluição de estado/timing no DB de sessão compartilhado (cache 30s de `ai_settings` + `ai_sequential_delay` de 2s no orquestrador + drain). Blindei `tests/endpoints/test_p25_*`: `ai_settings.reset_cache()` por teste (leitura fresca do canal) + `_drain_orchestrator` robusto (espera a task sumir por 2 polls consecutivos, cobrindo re-agendamento). Stress de 4× suíte completa em paralelo: **meus P25 nunca mais flacaram**; suíte serial final = **0 falhas**. `test_media_audio` (não meu, roda ANTES dos meus) é flake raro pré-existente do harness, fora do escopo do plano.

---

## Riscos e cuidados

- **Duplicação de `conversation_created` (Fase 2):** o maior risco. Há um broadcast inline em `agent/memory.py:191-203` **e** um no listener `on_message_persisted`. Antes de antecipar para o ingest, **confirmar** se hoje dispara 1× ou 2× e consolidar — senão a sidebar pode refetchar duas vezes ou logar inconsistência. Registrar o achado.
- **Perder o notice "conversa criada":** se a materialização migrar para o ingest mas o ciclo de vida (`_emit_lifecycle_notice`) continuar só no batch, o card some (no batch a conversa já existe → `transition=None`). Por isso 2.2 chama o ciclo de vida no ingest e o batch reusa em silêncio.
- **Idempotência inbound em grupo sem @menção:** esse ramo (`message_ingest_service.py:474-486`) já chama `add_message` no ingest. Garantir que `ensure_conversation_live` rodando antes não cause duplo anúncio.
- **Echo / filtro before_save:** `ensure_conversation_live` deve rodar **só após** a supressão de echo (`message_ingest_service.py:400-406`) e o filtro `filter.message.before_save` (`:445-449`) — uma mensagem filtrada/echo **não** deve criar conversa.
- **SQLite vs Postgres:** `mark_user_messages_as_read` e `resolve_for_contact_ex` rodam nos dois dialetos — validar ambos (Fase 3.2).
- **Concorrência entre contatos:** o serviço de batch é por `(channel_id, phone)`; a mudança de gate (Fase 1) é local e não introduz estado compartilhado. Sem novo risco de cross-talk.
- **Double-fetch no frontend (Fase 2):** em t=0 passam a sair `conversation_created` **e** `new_message`, ambos podendo chamar `fetchContacts`. É inofensivo (a conversa já existe no DB), mas se gerar flicker, considerar debounce no handler. Não bloquear o fix por isso.
- **Sem migration:** nenhuma das fases altera schema — não criar revision Alembic.

---

## Checklist de verificação

- [x] Fase 0: testes de caracterização 0a/0b escritos e falhando pelas razões certas (RED por revert temporário, depois verdes).
- [x] Bug #1: conversa IA-OFF (IA global+canal ON) mantém o badge; sem `messages_read`; sem `mark_read`. (`test_bug1_ia_off`)
- [x] Bug #1: conversa IA-ON preserva o comportamento (badge limpa + recibo) — não regrediu. (`test_bug1_ia_on`)
- [x] Bug #1: cliente em GOWA/Cloud **não** recebe "lida" falso em conversa IA-OFF. (coberto: `outbound.mark_read` não é chamado — provado por `test_bug1_ia_off`; validação no cliente real fica na 3.3 manual)
- [~] Bug #2: contato novo → linha aparece junto com o badge da aba (sem 3–4 s). (backend provado: conversa materializada no ingest + `new_message.conversation_id`; confirmação visual = 3.3 manual)
- [x] Bug #2: `conversation_created` dispara **exatamente 1×**; card "conversa criada" aparece 1×. (`test_bug2`; achado: já era 1× hoje — inline era dead code)
- [x] Batching intacto: mensagens rápidas continuam virando 1 linha combinada. (Fase 2 só antecipa a materialização da conversa; save combinado segue no batch — goldens de webhook verdes)
- [x] `python tests/test_endpoints.py` verde em **SQLite**. (882 passed, 0 failed; + pytest `tests/` exit 0; + `test_events_filters.py` 42/0)
- [x] Suíte verde em **Postgres** (`WHATSBOT_TEST_DB_URL=...`, DB UTF8 isolado). Só 2 falhas pré-existentes/ambientais (`legacy_script` subprocess + `roundtrip` channels_pkey dupe), idênticas no baseline (diff em `git stash`). Os 3 testes do plano passam em PG (RED→GREEN).
- [x] (Se 2.5) tela legível no **modo escuro** (classes `wa-*`). N/A — Fase 2.5 dispensada (sem UI nova; frontend já materializa a linha via refetch debounced + match por `conversation_id`).
- [x] Cada bloco "Status de execução" preenchido.
```
