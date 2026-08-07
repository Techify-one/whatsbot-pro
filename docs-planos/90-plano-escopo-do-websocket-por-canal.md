# Plano 90 — O WebSocket para de entregar conversa de canal que o operador não pode ver

> **Status:** PLANEJAMENTO · **Data:** 2026-07-28 · **Escopo:** médio/grande (backend; zero migration)
> **Origem:** achado **#1 🔴 CONFIRMED** do [registro 45](45-registro-bugs-riscos-realtime.md) (2026-07-09) e limitação **L1** da [avaliação 44](44-avaliacao-realtime-websocket-vs-chatwoot.md) — ambos são **registros de análise, não planos** ("nenhum código foi alterado"). Este é o plano executável. Reencontrado em 2026-07-28 durante a auditoria dos planos 88/89. **Método:** 4 agentes de inventário + síntese, leitura do código real (`arquivo:linha`), medição numa instância de produção real via MCP vault, e verificação manual dos pontos estruturais.
> O REST aplica escopo de canal rigoroso ([authz.py:77-90](../server/authz.py#L77)); o WebSocket **não aplica nenhum**. `ConnectionManager.active` é uma `list[WebSocket]` sem identidade ([state.py:58](../server/state.py#L58)) e o `/ws` chega a **resolver o usuário do token e descartá-lo** (`kind, _user = …`, [websocket.py:27](../server/routes/websocket.py#L27)). Resultado: **42 eventos distintos** de **98 call sites** vão para todos os sockets logados — incluindo o texto integral de cada mensagem e a linha enriquecida da conversa (nome, telefone, preview, etiquetas, atributos do contato).
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ **Filtro no chokepoint** (`ConnectionManager.broadcast`), nunca nos call sites | São **98** call sites no core + 23 via `plugins.context.broadcast` ([plugins/context.py:145-157](../plugins/context.py#L145)), que despeja no MESMO manager. Uma regra no chokepoint cobre **plugins de graça, sem mudar a API deles**. E plugins emitem eventos do NÚCLEO (o `protocolos` manda `new_message`; o `janela_72h` manda `contact_tags_updated`) — uma regra só nas rotas do core deixaria todos vazando |
| D2 | ✅ **Decisão binária** (entrega / não entrega), nunca payload variável por usuário | Preserva o `json.dumps` único de [state.py:69](../server/state.py#L69) — a decisão de engenharia que o doc 44 §2.5 elogia. Payload por destinatário = N serializações por evento |
| D3 | ✅ **Fail-open é o default; escopar é ALLOWLIST, não denylist** | Assimetria de custo: evento vazado é invisível para o operador; evento **calado** vira "o sistema parou" (bolha que não sobe, tique congelado, sirene muda) e gera chamado. E não há replay para sinal one-shot |
| D4 | ✅ **Resolver 100% síncrono; miss de cache ENTREGA e aquece em background** | `broadcast` roda no event loop. Ir ao banco inline o bloquearia; um `await` novo antes do `gather` **reordenaria eventos concorrentes**. O miss vira fail-open **por construção**, não por política |
| D5 | ✅ **`USER` vence `INBOX`** | `GET /api/atendimentos/assignable-agents` devolve **todos** os usuários ativos sem filtro de canal ([conversations.py:272-292](../server/routes/conversations.py#L272)) e o alvo de menção é qualquer `user_id` ([contacts.py:1377-1392](../server/routes/contacts.py#L1377)) — dá para ser mencionado/atribuído num canal onde não se é membro. Filtrar por canal mataria a sirene e o badge "@" |
| D6 | ✅ **Regra da UNIÃO** para eventos de contato | `unread_conversation_count` já conta um contato se **qualquer** conversa dele cair num canal visível ([unread_repo.py:73-92](../db/repositories/unread_repo.py#L73)). Interseção divergiria WS e REST |
| D7 | ✅ **`ws_scope_mode` como config key** (`off\|shadow\|enforce`) | Reverte **sem deploy**. É ao mesmo tempo feature flag, seam de teste e rollback |
| D8 | ✅ **Achado #5 (sessão revogada) ENTRA; achado #6 (token na query string) FICA DE FORA** | #5 usa a MESMA infra (registro socket→user + sweep periódico) e sem ele o escopo tem furo real. #6 é ortogonal (é sobre *como a credencial chega*), exige mudança coordenada cliente+servidor e janela de deploy — plano separado |
| D9 | ✅ **A sugestão literal do registro 45** (*"no mínimo não incluir `content`/PII para conexões escopadas"*, [45:56](45-registro-bugs-riscos-realtime.md)) **é rejeitada como mitigação barata** | Para saber quem é "escopado" já é preciso a identidade no socket — não é mais barato; quebra a serialização única (D2); um `new_message` redigido ainda renderiza bolha vazia (regressão visível); e telefone/`conversation_id`/`media_path` continuariam vazando |

---

## 1. Resumo executivo

O escopo de leitura do WhatsApp já existe e funciona — no REST. Um atendente que não é membro de um canal recebe **404** ao abrir a conversa ([conversations.py:314-320](../server/routes/conversations.py#L314)), e isso está travado por teste ([test_conversation_read_isolation.py:72-95](../tests/test_conversation_read_isolation.py#L72)). O WebSocket ignora tudo isso: o mesmo operador **recebe ao vivo, no navegador**, o texto das mensagens daquele canal.

A correção é dar **identidade ao socket** (o `/ws` já a calcula e joga fora) e filtrar **dentro** do `broadcast`, por um registry puro que classifica cada evento em `GLOBAL | INBOX | CONTACT | USER | PERMISSION`. Tudo que não estiver classificado **é entregue** — escopar é allowlist (D3).

O caminho é deliberadamente conservador: **identidade primeiro** (sem filtrar nada), depois **modo shadow** medindo em produção o que *seria* cortado, e só então o enforce. Os dois eventos que o servidor **já sabe endereçar** (`mention_created`, `agent_transfer_alert`) podem ir para produção sozinhos, na frente de tudo, com regressão nula por construção.

---

## 2. Como funciona hoje (mapa)

### 2.1 O motor de fan-out

```python
def __init__(self):
    self.active: list[WebSocket] = []          # ← sem nenhum metadado  (state.py:58)

async def connect(self, websocket: WebSocket):
    await websocket.accept()
    self.active.append(websocket)              # ← nada sobre quem é    (state.py:60-62)

async def broadcast(self, event: str, data: dict):
    message = json.dumps({"event": event, "data": data})
    targets = list(self.active)                # ← todos                (state.py:68-70)
```

E o handshake, que **tem** a identidade e a descarta:

```python
kind, _user = await asyncio.to_thread(resolve_request_token, token)   # websocket.py:27
if kind != "user": ...close(4401)
await ws_manager.connect(websocket)                                    # websocket.py:33 — `_user` some
```

✅ **Verificado:** ninguém fora de `state.py` lê `ws_manager.active` (grep em `server/`, `app/`, `agent/`, `plugins/`), então trocar a estrutura é seguro.

### 2.2 O que sai pelo cano (medido em 2026-07-28)

**42 nomes de evento distintos**, **98 call sites** no core + 23 via o seam de plugin. Os mais sensíveis:

| Evento | O que carrega | Locator no payload | Classe proposta |
|---|---|---|---|
| `new_message` | **texto integral**, telefone, `media_path`, legenda, e o alvo citado hidratado ([realtime_broadcast.py:90-119](../app/services/realtime_broadcast.py#L90)) | `channel_id` **inconsistente** (~9 de ~44 sites omitem); **nunca** `inbox_id` | `INBOX` |
| `conversation_upsert` | **o mais rico**: linha inteira — `contact_name`, `contact_phone`, `last_message` (preview do texto), `labels`, `contact_tags`, `custom_attributes` ([conversation_query.py:73-110](../db/repositories/conversation_query.py#L73)) | **`inbox_id` E `channel_id`** ✅ | `INBOX` |
| `mention_created` | `preview` de 120 chars da **nota privada** + telefone + autor ([contacts.py:1421](../server/routes/contacts.py#L1421)) | **`inbox_id` E `mentioned_user_ids`** ✅ | `USER` |
| `agent_transfer_alert` | quem recebeu a conversa ([conversation_service.py:127-131](../app/services/conversation_service.py#L127)) | **`assignee_user_id`** ✅ | `USER` |
| `operator_typing` | telefone + **nome do atendente** ([contacts.py:2172](../server/routes/contacts.py#L2172)) | `channel_id`, `conversation_id` | `INBOX` |
| `group_participants_changed` | JIDs/telefones **e nomes** de todos os participantes | nenhum | `INBOX` |
| 8 eventos de ciclo de vida | status/atribuição/arquivamento da conversa | **`inbox_id`** ✅ ([ws_projections.py:45-54](../app/services/ws_projections.py#L45)) | `INBOX` |

⚠️ **O que PIOROU desde o registro 45**: a superfície saiu de ~25 para 42 eventos, e o evento mais rico em PII do sistema (`conversation_upsert`) **nasceu depois** do registro.
✅ **O que MELHOROU para o conserto** (e o registro 45 não sabia): três dos eventos mais sensíveis **já trazem a chave de escopo pronta** — dá para decidir sem ida ao banco.
😖 **O detalhe mais embaraçoso**: `mention_created` e `agent_transfer_alert` já carregam o destinatário e o filtro é feito **no cliente** ([useConversationWsEvents.js:274-278](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L274), [:393-402](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L393)). O código admite: *"O broadcast vai a todos os clientes; o frontend filtra por `assignee_user_id === currentUserId`"* ([conversation_service.py:120-122](../app/services/conversation_service.py#L120)). O servidor **sabe** o destinatário e manda para todos assim mesmo.

### 2.3 Dimensão real do vazamento (produção, 2026-07-28)

| Canal | Conversas | Quem NÃO é membro |
|---|---|---|
| Atendimento (principal) | 14.341 (95,6%) | ninguém |
| Telegram | 570 | 1 dos 13 |
| WhatsApp oficial (disparo) | 47 | 1 dos 13 |
| Avisos (integração externa) | 25 | 7 dos 13 |
| Site | 4 | 12 dos 13 |

5 dos 13 usuários são `admin` → veem tudo legitimamente (curto-circuito em [rbac_repo.py:76-77](../db/repositories/rbac_repo.py#L76)). O vazamento efetivo atinge **~599 conversas (4%)** e **8 operadores**.

**Leitura honesta:** é vazamento real de PII de cliente entre operadores, **mas não é um incêndio**. Isso não é motivo para não corrigir — é motivo para corrigir **sem parar o mundo**: shadow antes do enforce, e prioridade para o que o servidor já sabe endereçar.

### 2.4 A peça que falta

`visible_inbox_ids` é **request-bound** ([authz.py:77-90](../server/authz.py#L77) chama `current_user(request)`), inutilizável no WS. Precisa ser extraída para uma versão por `user_id`, com a versão de request delegando — garantindo **uma única definição** de "o que eu vejo" para REST e WS. É exatamente o que o achado #1 pede.

---

## 3. Arquitetura

### 3.1 Identidade no socket

`self.active` vira `dict[WebSocket, SocketScope]` — iterar um dict devolve as chaves, então `targets = list(self.active)` ([state.py:70](../server/state.py#L70)) continua **byte-compatível**.

```python
@dataclass
class SocketScope:
    user_id: int | None                # None = instalação aberta
    inbox_ids: frozenset[int] | None    # None = vê tudo (admin / read_all / aberta)
    session_ref: str | None             # HASH do token (nunca em claro) — achado #5
    refreshed_at: float
```

⚠️ **Assinatura keyword-only com default** em `connect(ws, *, scope=None)` e nenhum parâmetro posicional novo em `broadcast`. Motivo duro: a suíte monkeypatcha `broadcast(event, data)` com 2 posicionais ([test_tool_call_broadcast.py:18-24](../tests/test_tool_call_broadcast.py#L18), [test_plano75_error_card.py:149-157](../tests/test_plano75_error_card.py#L149), `tests/endpoints/test_p25_unread_badge_and_ingest.py:81-93`).

O escopo é calculado no handshake, **em `to_thread`** (são 2 idas ao banco: `user_has_permission` + `inbox_ids_for_user`) — a linha 27 já roda assim.

### 3.2 Injeção, não import

`server/state.py` **não pode** importar `server/authz.py` nem repos (ciclo de import + mata a testabilidade do manager). Seam:

```python
ws_manager.set_scope_resolver(fn)    # fn(event, data, scope) -> bool
```

**Resolver ausente ⇒ `broadcast` byte-idêntico ao de hoje.** Ligado no `create_app` ([app.py:123](../server/app.py#L123)).

### 3.3 Registry puro de classificação — `server/ws_scope.py`

| Classe | Regra | Eventos |
|---|---|---|
| `GLOBAL` | sempre entrega | `status`, `qr_update`, `gowa_status`, `config_saved`, `tags_changed`, `tools_changed`, `quick_replies_changed`, `conversation_labels_registry_changed`, `human_transfer_alert` (⚠️ sem locator — [messaging_service.py:596](../app/services/messaging_service.py#L596)) |
| `INBOX` | `scope.inbox_ids is None or inbox in scope.inbox_ids` | `conversation_upsert`, os 8 de ciclo de vida, `new_message`, `message_*`, `chat_presence`, `ai_typing`, `operator_typing`, `avatar_updated`, `group_participants_changed` |
| `CONTACT` | união das inboxes do contato ∩ visíveis ≠ ∅ (D6) | `contact_info_updated`, `contact_tags_updated`, `contact_ai_toggled`, `messages_read`, `contact_pinned/archived/deleted` |
| `USER` | `scope.user_id ∈ payload[…]` — **ignora canal** (D5) | `agent_transfer_alert`, `mention_created` |
| `PERMISSION` | socket tem a permissão | `low_balance` → `billing.manage`; `plugin_melhorias_ai_event` → admin |
| `UNRESOLVED` | **entrega + conta + loga** | evento classificado cujo locator falta no payload |
| *não listado* | **entrega** (D3) | todo evento de plugin e todo evento novo |

### 3.4 `channel_id` → `inbox_id` sem pagar banco por evento

| Tier | Como | Cobre |
|---|---|---|
| **1** | o payload **já traz** `inbox_id` — custo zero | `conversation_upsert`, os 8 de ciclo de vida, `mention_created` |
| **2** | **denormalizar `inbox_id` no emit** (o trabalho estrutural) | `new_message` — 14+ sites que já têm `channel_id` só ganham `inbox_id`; ~12 sem locator nenhum são o trabalho de fato |
| **3** | caches TTL/LRU em memória, no padrão de [ai_settings.py:51-52](../channels/ai_settings.py#L51) | `channel_id→inbox_id` (cardinalidade = 7 canais), `conversation_id→inbox_id` (**`OrderedDict` com cap**), `contact→inboxes` |

⚠️ **Não repetir os achados #10 e #11 do registro 45** (`presence_conv_cache`, `typing_state`): dict cru com TTL só no valor = chave eterna = leak. Cap + poda FIFO obrigatórios.

---

## 4. Fases / Roadmap

```
WAVE 0   F0a (identidade no socket)                              🔴 base de tudo
              ├──────────────────────────┐
WAVE 1   F0b (modo shadow)          F0c (USER scope)             🟢 F0c pode ir a produção SOZINHA
              │
WAVE 2   F1 (motor de escopo)                                    🔴 [depende de: F0b]
              │
              ├────────┬────────┬────────┬────────┐
WAVE 3   F2(já têm)  F3(sweep)  F4(cache)  F5(sessão)            🟢 todas paralelas
              │        │        │
              │        │        └──> F6 (classe CONTACT)         🟢 [depende de: F4]
              └────────┴─────────────────┘
WAVE 4   F7 (virar a chave: enforce)                             🔴 sozinha
              │
WAVE 5   F8 (PERMISSION scope — opcional)                        🟢
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|------------|-------|-------|----------------|
| 0 | F0a | identidade no socket + endpoint de diagnóstico | 🔴 [bloqueia: tudo] | baixo | Dois logins mostram `inbox_ids` distintos; **nada é filtrado** |
| 1 | F0b | modo shadow (mede, não corta) | 🟢 [depende de: F0a] | baixo | 48h em produção com entrega **byte-idêntica** + tabela do que seria cortado |
| 1 | F0c | `USER` scope (`mention_created`, `agent_transfer_alert`) | 🟢 [depende de: F0a] | **baixo** | Menção a A gera 1 entrega (A) e 0 no socket de B; som/toast seguem funcionando para A |
| 2 | F1 | `ws_scope.py` + `set_scope_resolver` + wiring | 🔴 [depende de: F0b] | médio | Testes com sockets falsos verdes; `mode=off` indistinguível da baseline |
| 3 | F2 | classificar o que **já** tem `inbox_id` | 🟢 | médio | Evento de canal B não chega no socket do membro só de A |
| 3 | F3 | sweep dos emit sites de `new_message` | 🟢 | médio | `unresolved["new_message"]` cai a ≈0 em 24h de tráfego real |
| 3 | F4 | caches de resolução | 🟢 | médio | `resolved_from_cache / total > 99%`; teste prova que o loop nunca toca o banco |
| 3 | F5 | sweep de escopo + revalidação de sessão (**achado #5**) | 🟢 | médio | Tirar alguém de um canal reflete no socket em ≤60s, sem reconexão |
| 3 | F6 | classe `CONTACT` (união) | 🟢 [depende de: F4] | médio | Contato em canal visível + invisível: o evento **chega** |
| 4 | F7 | `ws_scope_mode = enforce` | 🔴 | **alto** | Teste de isolamento de WS verde **e** `would_drop` no mesmo patamar do shadow |
| 5 | F8 | `PERMISSION` scope (opcional) | 🟢 | baixo | `low_balance` só para quem tem `billing.manage` |

**Disciplina:** caracterização **antes** (o shadow É a caracterização, com tráfego real); **verde a cada fase**; **um refactor por commit**; nunca avançar para o enforce com `unresolved` alto.

---

### Fase 0a — Parar de descartar a identidade (🔴 base)

**Objetivo:** o socket passar a saber quem é. **Nada é filtrado nesta fase.**

**Itens**
1. `[sequencial]` `self.active` vira `dict[WebSocket, SocketScope]` ([state.py:58](../server/state.py#L58)); `disconnect` vira `pop(ws, None)`; `connect(ws, *, scope=None)` keyword-only (§3.1).
2. `[sequencial]` Extrair `visible_inbox_ids_for_user(user_id)` de [authz.py:77-90](../server/authz.py#L77), com a versão de request delegando (§2.4).
3. `[sequencial]` [websocket.py:27](../server/routes/websocket.py#L27): renomear `_user` → `user`, calcular o escopo em `to_thread`, passar no `connect`.
4. `[paralelo]` `GET /api/admin/ws-sockets` (gate de admin) listando `{user_id, email, inbox_ids, connected_at}` por socket — é o que torna as fases seguintes **observáveis**.
5. `[paralelo]` `session_ref` guarda **hash** do token, nunca o token em claro (a lista é lida por endpoint).

**Pronto quando:** com dois logins simultâneos (1 admin, 1 atendente escopado), o endpoint mostra `inbox_ids: null` e `inbox_ids: [21, 19, …]`. **A suíte inteira passa sem nenhuma alteração de teste** — se algum teste precisou mudar, a assinatura quebrou a compatibilidade (§3.1) e o item 1 está errado.

#### Status de execução — Fase 0a
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 0b — Modo shadow (🟢, depende de F0a)

**Objetivo:** medir **em produção** o que o enforce cortaria, antes de cortar. É a caracterização deste plano.

**Itens**
1. `[sequencial]` Config key `ws_scope_mode ∈ off|shadow|enforce` (default **`shadow`**).
2. `[sequencial]` Em shadow, o resolver calcula o veredito de todo evento e incrementa `would_drop[event][user_id]` e `unresolved[event]` — **mas sempre entrega**.
3. `[paralelo]` `GET /api/admin/ws-scope-stats` expõe os dois contadores.

**Pronto quando:** ≥48h em produção; a tabela empírica de "o que seria cortado" e "o que não sei classificar" existe; e um teste comprova que **o conjunto de mensagens recebidas em `shadow` é idêntico ao de `off`**.

⚠️ O `unresolved` é a **lista de tarefas da Fase 3**, medida com tráfego real — não por grep.

#### Status de execução — Fase 0b
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 0c — `USER` scope nos dois eventos já endereçados (🟢, depende de F0a — **pode ir a produção sozinha**)

**Objetivo:** fechar o item mais embaraçoso do inventário com **regressão nula por construção**.

**Contexto:** `mention_created` ([contacts.py:1421-1429](../server/routes/contacts.py#L1421)) carrega `mentioned_user_ids` **e** o `preview` de 120 chars de uma **nota privada**; `agent_transfer_alert` ([conversation_service.py:127-131](../app/services/conversation_service.py#L127)) carrega `assignee_user_id`. O cliente **já descarta** o que não é dele ([useConversationWsEvents.js:274-278](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L274), [:393-402](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L393)) — entregar só ao destinatário é um subconjunto **estrito** do comportamento atual.

**Itens**
1. `[sequencial]` Classificar os dois como `USER` e entregar só aos `user_id` que o próprio payload nomeia.
2. `[sequencial]` `scope.user_id is None` (instalação aberta) ⇒ entrega (não inventar tier "anônimo parcial").
3. `[paralelo]` Verificar se o menu de @menção do cliente restringe os mencionáveis aos membros do canal — o backend **não** restringe ([contacts.py:1377-1392](../server/routes/contacts.py#L1377)). *A confirmar antes de dimensionar a exceção de D5.*

**Pronto quando:** dois sockets logados; uma menção a `user_id=A` gera **exatamente 1** entrega (a de A) e **0** na de B; o toast, o som e o badge "@" continuam funcionando para A.

#### Status de execução — Fase 0c
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 1 — Motor de escopo (🔴, depende de F0b)

**Objetivo:** o registry puro + o seam, sem classificar quase nada ainda.

**Itens**
1. `[sequencial]` `server/ws_scope.py` — **100% puro** (sem I/O, sem import de repo): `classify(event, data) -> Classe` + `should_deliver(classe, data, scope, resolvers) -> bool`.
2. `[sequencial]` `ws_manager.set_scope_resolver(fn)` + wiring em [app.py:123](../server/app.py#L123). **Resolver ausente ⇒ comportamento atual** (§3.2).
3. `[paralelo]` Bateria de testes com sockets falsos (§6.1).

**Pronto quando:** os testes do manager passam; com `ws_scope_mode=off` a entrega é **indistinguível** da baseline.

#### Status de execução — Fase 1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 2 — Classificar o que já tem `inbox_id` (🟢, paralela com F3/F4/F5)

**Objetivo:** cobrir o evento mais rico em PII (`conversation_upsert`) **sem tocar em nenhum emit site**.

**Itens**
1. `[sequencial]` Registry cobre: `conversation_upsert`, os 8 de ciclo de vida ([ws_projections.py:45-54](../app/services/ws_projections.py#L45)), `conversation_created`/`_status_changed` do listener ([message_listeners.py:87,108](../agent/message_listeners.py#L87)), `mention_created`.
2. `[paralelo]` Confirmar em shadow que nenhum desses aparece em `unresolved` (se aparecer, o locator não está onde se pensava).

**Pronto quando (observável):** atendente membro só do canal A, conectado. Mensagem chega no canal B ⇒ o socket **não** recebe `conversation_upsert` e a linha **não aparece** na sidebar sem F5 — exatamente o que o REST já faz ([test_conversation_read_isolation.py](../tests/test_conversation_read_isolation.py)). A mesma ação no canal A ⇒ recebe (**controle positivo obrigatório**).

#### Status de execução — Fase 2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 3 — Sweep dos emit sites de `new_message` (🟢, maior volume)

**Objetivo:** dar locator ao evento mais sensível. **Guiada pelo contador `unresolved` da F0b, não por grep.**

**Itens**
1. `[paralelo]` Lote A — sites que **já têm** `channel_id`, só ganham `inbox_id`: `messaging_service.py:285,309,442,553,664,914,1028,1161`; `message_ingest_service.py:312,517,531`; `template_service.py:284`; `channel_webhook.py:177,517,592`.
2. `[paralelo]` Lote B — sites **sem locator nenhum** (o trabalho de fato): `messaging_service.py:67-69` (bolha de erro), `:682`, `:932`, `:951`, `:1093`, `:1109`, `:1141`; `system_notices.py:465` (só `conversation_id`); `storages/plugins/protocolos/logic.py:3346`.
3. `[sequencial]` **Sandbox** ([sandbox.py:49,86,105](../server/routes/sandbox.py#L49)) não pertence a canal nenhum ⇒ marcar `sandbox: true` no payload e classificar `GLOBAL` (ou `USER` = quem disparou, se o `current_user` estiver disponível — *a confirmar*). **Sem isso, o enforce cala a tela de teste.**
4. `[sequencial]` Preferir enriquecer o builder único ([realtime_broadcast.py:90-119](../app/services/realtime_broadcast.py#L90)) a tocar 44 sites à mão, onde o site passa por ele.

**Pronto quando:** após deploy em shadow, `unresolved["new_message"]` cai a ≈0 em 24h de tráfego real.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 4 — Caches de resolução (🟢, independente)

**Objetivo:** decidir sem ir ao banco, sem vazar memória.

**Itens**
1. `[paralelo]` `channel_id → inbox_id` ([inbox_repo.get_by_channel](../db/repositories/inbox_repo.py)) — cardinalidade = nº de canais (**7** em prod), praticamente permanente.
2. `[paralelo]` `conversation_id → inbox_id` — **cardinalidade ilimitada** ⇒ `OrderedDict` com cap (~5.000) + poda FIFO. *A confirmar: `conversations.inbox_id` é imutável após a criação (grep por `update(...).values(inbox_id=…)`) — se for, dispensa TTL, só cap.*
3. `[paralelo]` `contact → frozenset[inbox_id]` para a classe `CONTACT`, TTL 30s + cap.
4. `[sequencial]` **Miss ⇒ entrega + `asyncio.create_task` de warm** (D4). Nenhum `await` novo antes do `gather`.
5. `[sequencial]` Invalidação via `register_core_sync_listener` ([plugins/events.py:230-242](../plugins/events.py#L230) — o mesmo seam do `ws_projections`) em `channel.created/updated/deleted`.

**Pronto quando:** em shadow, `resolved_from_cache / total > 99%`; e um teste que **patcha `get_engine` para levantar** durante um `broadcast` prova que nenhum caminho de decisão o toca.

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 5 — Sweep de escopo + revalidação de sessão (🟢) — **resolve o achado #5**

**Objetivo:** escopo obsoleto é furo de escopo. Sem isto, quem perdeu a permissão continua com o escopo antigo até reconectar.

**Itens**
1. `[sequencial]` Task periódica (60s) no supervisor que recalcula o escopo de **todos** os sockets. Otimização: `inbox_member_repo.inbox_ids_by_user()` já devolve `{user_id: [inbox_id,…]}` **em uma query** ⇒ o sweep inteiro custa 2 queries, independente do nº de sockets.
2. `[sequencial]` No mesmo passe, revalidar `session_repo.get_valid` + `user.is_active`; falhou ⇒ `close(4401)`. **É literalmente o que o achado #5 pede.**
3. `[paralelo]` Bônus: `session_repo.delete`/`delete_for_user` chamam `ws_manager.drop_user(user_id)` para derrubada **imediata** no logout/desativação.
4. `[paralelo]` Registrar que `set_inboxes_for_user` e a mudança de cargo **não emitem evento nenhum hoje** — por isso o sweep é a **corretude**, e emits futuros seriam só latência.

**Pronto quando:** tirar um usuário de um canal pela tela Usuários reflete no socket dele em ≤60s **sem reconexão**; desativar o usuário derruba o socket em ≤60s.

#### Status de execução — Fase 5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 6 — Classe `CONTACT`, regra da união (🟢, depende de F4)

**Objetivo:** cobrir a família só-`phone` **sem** quebrar contato compartilhado entre canais.

**Contexto:** `contacts.phone` é globalmente único, então um contato pode ter conversas em N canais. `contact_info_updated` ([messaging_service.py:568,581](../app/services/messaging_service.py#L568)), `contact_tags_updated`, `contact_ai_toggled`, `messages_read` ([:888](../app/services/messaging_service.py#L888)), `message_status`/`_reaction`/`_revoked`/`_deleted` **não têm canal nenhum** — o hook do cliente casa por `c.phone === phone`. Fail-closed aqui quebraria a atualização de um contato **visível** por outro canal.

**Itens**
1. `[sequencial]` Implementar a união (D6): entrega se **qualquer** canal do contato estiver visível.
2. `[paralelo]` Conferir a coerência com [unread_repo.py:73-92](../db/repositories/unread_repo.py#L73) — é dele que a regra vem.

**Pronto quando:** contato com conversa no canal A (visível) e B (invisível) ⇒ o atendente de A **recebe** `contact_tags_updated`. Contato exclusivo de B ⇒ não recebe.

#### Status de execução — Fase 6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 7 — Virar a chave (🔴 sozinha)

**Itens**
1. `[sequencial]` `ws_scope_mode = enforce`.
2. `[sequencial]` Monitorar `would_drop` por 24h.

**Pronto quando:** o teste de isolamento de WS (§6.3) passa **e** em produção `would_drop` fica **no mesmo patamar do shadow**. Um salto significa que algum evento legítimo passou a ser cortado ⇒ voltar para `shadow` (config key, sem deploy) e investigar.

#### Status de execução — Fase 7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 8 — `PERMISSION` scope (🟢 opcional)

`low_balance` entrega hoje **dado financeiro + `account_url` de recarga** a todo atendente ([balance_monitor.py:124-128](../server/balance_monitor.py#L124)) ⇒ gate `billing.manage`. `plugin_melhorias_ai_event` re-emite eventos crus do executor Claude Code ⇒ gate admin. Trivial depois do motor.

#### Status de execução — Fase 8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

## 5. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | **Calar evento legítimo** (o risco principal) | O painel "para" — bolha não sobe, tique congela | (a) fail-open por default (D3); (b) **shadow obrigatório antes do enforce** — números de tráfego real, não raciocínio; (c) `ws_scope_mode=off` reverte **sem deploy**; (d) o cliente já refaz o estado de lista na reconexão ([useConversationWsEvents.js:146-150](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L146)) |
| R2 | **Calar sinal one-shot** (som/toast/sirene) | **Não existe replay** — um `agent_transfer_alert` calado é perdido para sempre | `agent_transfer_alert`/`mention_created` são `USER`, nunca `INBOX` (D5). `human_transfer_alert` fica `GLOBAL` (só tem `{phone, enabled, duration}`) até ser enriquecido. **Regra: nada que emite som entra em `INBOX` sem enriquecimento prévio** |
| R3 | **Bloquear o event loop** | `broadcast` roda no loop; um resolver que vá ao banco trava o fan-out | Resolver síncrono; miss nunca consulta inline (D4); teste que faz `get_engine` levantar durante o broadcast |
| R4 | **Reordenar eventos** | Um `await` novo antes do `gather` ([state.py:84](../server/state.py#L84)) reordenaria eventos concorrentes — falha silenciosa | Consequência direta de R3: nenhum `await` novo é introduzido |
| R5 | **Leak de memória nos caches** | Repetir os achados #10/#11 do registro 45 | `OrderedDict` com cap + poda FIFO; TTL no valor **não** basta |
| R6 | **Escopo obsoleto** | `set_inboxes_for_user` e mudança de cargo **não emitem evento** hoje | Sweep de 60s (F5) é a corretude. `channel.members_changed` existe mas **não traz os user_ids no payload público** ⇒ invalidação por ele seria global |
| R7 | **Badge global defasado** | `unread_conversation_count` conta contato compartilhado; calar o gatilho de refetch diverge o título da aba do próprio servidor | Regra da união (F6) cobre o compartilhado; **documentar como limitação conhecida** no caso puro |
| R8 | **Quebrar a suíte** | 3 testes monkeypatcham `broadcast` com 2 posicionais | Nenhum parâmetro **posicional** novo; o filtro mora **dentro** do `broadcast`, então os espiões continuam funcionando |
| R9 | **Calar plugin** | Nenhum evento de plugin tem chave de escopo | Fail-open por não-classificação; a decisão inteira mora num único módulo puro auditável |
| R10 | **Sandbox mudo** | `sandbox.py:49,86,105` não tem locator | Tratamento explícito na F3, **antes** de qualquer enforce |
| R11 | **`custom_permissions=1` é a armadilha** | O conjunto explícito **substitui as roles** e pula o curto-circuito de admin ([rbac_repo.py:61-70](../db/repositories/rbac_repo.py#L61)) — um custom com role admin, sem `read_all`, **é escopado** | É o perfil dos 8 atendentes de produção. **Obrigatório na matriz de teste** |

---

## 6. Testes

### 6.1 Dá para testar o `ConnectionManager` sem socket real? **Sim.**

`broadcast` só chama `ws.send_text(str)` ([state.py:77](../server/state.py#L77)) e `ws.close()` na poda ([:98](../server/state.py#L98)). Um `FakeWS` com `accept`/`send_text`/`close` basta — **sem ASGI, sem servidor, sem banco**. É onde a maior parte da cobertura deve viver.

### 6.2 Bateria

| Nível | Arquivo | O que trava |
|---|---|---|
| **Puro** | `tests/test_ws_scope_classify.py` | Tabela sobre `classify(event, data)`: `GLOBAL`, `INBOX` com/sem locator, `CONTACT`, `USER`, **evento desconhecido ⇒ entrega**. Sem I/O — maior densidade de valor |
| **Manager com fakes** | `tests/test_ws_scope_fanout.py` | 3 sockets (admin `None`, escopado `{1}`, escopado `{2}`): `conversation_upsert` com `inbox_id=1` ⇒ 2 de 3; `status` ⇒ 3 de 3; evento não classificado ⇒ 3 de 3 (**trava o fail-open**); `mention_created` com `mentioned_user_ids=[7]` ⇒ só o socket do 7 |
| **Compat de assinatura** | idem | `broadcast("x", {})` com 2 posicionais continua válido; `connect(ws)` sem `scope` continua válido |
| **Loop limpo** | idem | `get_engine` patchado para levantar; um `broadcast` completo não o toca (**trava R3/R4**) |
| **Shadow** | idem | `mode="shadow"` ⇒ contadores sobem **e** a entrega é idêntica a `off` |
| **Integração** | `tests/test_ws_scope_isolation.py` | §6.3 |

### 6.3 O análogo do precedente de isolamento

Reusar o **mesmo fixture** de [test_conversation_read_isolation.py:26-70](../tests/test_conversation_read_isolation.py#L26), que já monta o cenário exato: dois inboxes e um `_scoped_user` **custom** com `conversation.read`, sem `read_all`, membro só de A — **precisamente o perfil dos 8 atendentes de produção**, e que passa pela armadilha do R11.

```python
with client.websocket_connect(f"/ws?token={token}") as ws:
    ws.receive_json(); ws.receive_json(); ws.receive_json()   # status/gowa_status/qr_update do handshake
    <ação que gera new_message/conversation_upsert no canal B>
    assert <nenhum evento de conversa recebido>               # timeout curto
    <a MESMA ação no canal A>
    assert <recebido>                                          # CONTROLE POSITIVO — obrigatório
```

⚠️ O **controle positivo** é o que impede o teste de "passar" porque o WS parou de funcionar — mesmo padrão de `test_own_inbox_conversation_is_visible`.

Casos de compatibilidade no mesmo arquivo: **admin** recebe os dois; **instalação aberta** recebe os dois; **custom + role admin sem `read_all`** ⇒ **é escopado** (trava [rbac_repo.py:61-70](../db/repositories/rbac_repo.py#L61)).

*A confirmar no primeiro spike da F0a:* que `client.websocket_connect` do TestClient conviva com o `asyncio.to_thread(user_repo.has_any)` do handshake ([websocket.py:23](../server/routes/websocket.py#L23)). Provável que sim, mas **todo o critério de aceitação de ponta-a-ponta depende disso**. Plano B: injetar sockets falsos no manager do app construído e disparar as ações pelo `client` REST/webhook — cobertura equivalente, ergonomia pior.

---

## 7. Perguntas em aberto

**P1 — Menção/atribuição cross-canal: consertar a leitura ou proibir na origem?**
⏸️ **DECISÃO DE PRODUTO, não técnica.** Hoje é possível mencionar/atribuir alguém num canal do qual ele não é membro ([conversations.py:272-292](../server/routes/conversations.py#L272), [contacts.py:1377-1392](../server/routes/contacts.py#L1377)) — o alerta toca, mas a conversa dá **404** na leitura ([conversations.py:319](../server/routes/conversations.py#L319)). Duas saídas: (a) manter a exceção `USER` (D5) e consertar a leitura, ou (b) proibir mencionar/atribuir a não-membros na origem. **Recomendação:** (a) agora (é o que D5 assume e não quebra fluxo existente); levar (b) como pergunta de produto separada.

**P2 — `has_user_mention` no `conversation_upsert` sai sempre `False`.**
⏸️ **FORA DESTE PLANO, mas registrado.** `get_row_for_broadcast` chama `get_with_channel(conv_id)` **sem** `current_user_id` ([conversation_repo.py:654-670](../db/repositories/conversation_repo.py#L654)), então a subquery vira `literal(False)` ([conversation_query.py:64-70](../db/repositories/conversation_query.py#L64)) e um upsert pode **apagar** o badge "@" que o refetch tinha aceso. É o **único** caso em que o payload precisaria variar por usuário — o que quebraria D2. **Caminho barato:** *remover* `has_user_mention` do payload e deixar o cliente derivar de `mention_created`, que com a F0c passa a ser entregue só ao destinatário (tornando a derivação correta).

**P3 — Achado #6 (token na query string).**
⏸️ **PLANO SEPARADO** (D8). Nota de arquitetura: se um dia for feito com "ticket de conexão", esse ticket é o lugar natural para **carregar o escopo já pré-computado**, eliminando as 2 queries do connect.

**P4 — Três telas abrem `/ws` sem `?token=`.**
⏸️ **HIGIENE À PARTE.** `protocolos_tab.js:814`, `ScheduleTabs.js:117` e `ToolsUnified.js:114` abrem `new WebSocket('/ws')` sem token ⇒ levam `close(4401)` em qualquer instalação com usuários ([websocket.py:24-31](../server/routes/websocket.py#L24)). **Esse live-reload já está morto hoje** — o plano não pode assumir que funciona nem contabilizar sua quebra como regressão própria.

**P5 — `/api/executions` não é escopado por canal.**
⏸️ Buraco análogo, fora deste plano (levantado de passagem no inventário). Merece verificação própria.

---

## 8. Checklist de verificação

Por fase:
- [ ] F0a: dois logins mostram `inbox_ids` distintos no `/api/admin/ws-sockets`; **suíte inteira verde sem alterar teste nenhum**
- [ ] F0b: 48h em shadow; entrega comprovadamente idêntica a `off`; tabela `would_drop`/`unresolved` publicada
- [ ] F0c: menção a A ⇒ 1 entrega (A), 0 em B; som/toast/badge seguem para A
- [ ] F1: testes com `FakeWS` verdes; `mode=off` indistinguível da baseline
- [ ] F2: evento do canal B não chega ao membro só de A; **controle positivo** (canal A chega) verde
- [ ] F3: `unresolved["new_message"]` ≈ 0 em 24h; **Sandbox tratado**
- [ ] F4: `resolved_from_cache > 99%`; teste do `get_engine` que levanta passa
- [ ] F5: remoção de membro reflete em ≤60s sem reconexão; desativar derruba o socket
- [ ] F6: contato compartilhado A+B ⇒ o atendente de A recebe
- [ ] F7: `would_drop` no mesmo patamar do shadow por 24h

Transversal:
- [ ] Suíte no Postgres de teste verde (`WHATSBOT_TEST_DB_URL`)
- [ ] Matriz de perfis: admin · atendente escopado · **custom_permissions=1 com role admin sem `read_all`** (R11) · instalação aberta
- [ ] Nenhuma migration (este plano não toca o schema)
- [ ] `session_ref` guarda **hash**, nunca o token em claro
- [ ] Nenhum parâmetro posicional novo em `broadcast`/`connect`
- [ ] Rollback provado: `ws_scope_mode=off` em produção restaura o comportamento atual **sem deploy**

---

## 9. Apêndice — arquivos-chave

**Novos**
- `server/ws_scope.py` — registry puro de classificação
- `tests/test_ws_scope_classify.py` · `tests/test_ws_scope_fanout.py` · `tests/test_ws_scope_isolation.py`

**Alterados (core)**
- [server/state.py](../server/state.py) — §58-70: `active` vira dict + `SocketScope` + `set_scope_resolver`
- [server/routes/websocket.py](../server/routes/websocket.py) — §23-33: parar de descartar o `user`, calcular o escopo
- [server/authz.py](../server/authz.py) — §77-90: extrair `visible_inbox_ids_for_user(user_id)`
- [server/app.py](../server/app.py) — §123: wiring do resolver
- [app/services/realtime_broadcast.py](../app/services/realtime_broadcast.py) — §90-119: `inbox_id` no envelope de `new_message`
- [app/services/messaging_service.py](../app/services/messaging_service.py) · [app/services/message_ingest_service.py](../app/services/message_ingest_service.py) · [server/routes/channel_webhook.py](../server/routes/channel_webhook.py) · [server/system_notices.py](../server/system_notices.py) · [server/routes/sandbox.py](../server/routes/sandbox.py) — sweep da F3
- [server/background.py](../server/background.py) — sweep de escopo/sessão da F5

**Lidos, não alterados** (contexto obrigatório)
- [plugins/context.py](../plugins/context.py) — §145-157: por que o chokepoint cobre plugins de graça
- [db/repositories/rbac_repo.py](../db/repositories/rbac_repo.py) — §61-77: curto-circuito de admin **e** a armadilha do `custom_permissions`
- [db/repositories/unread_repo.py](../db/repositories/unread_repo.py) — §73-92: de onde vem a regra da união
- [app/services/ws_projections.py](../app/services/ws_projections.py) — §45-54: os eventos que já têm `inbox_id`
- [tests/test_conversation_read_isolation.py](../tests/test_conversation_read_isolation.py) — o fixture a reusar

**Registros que originaram este plano**
- [45 — registro de bugs realtime](45-registro-bugs-riscos-realtime.md) — achado #1 (este plano), #5 (entra na F5), #6 (fica de fora), #10/#11 (o erro de cache a não repetir)
- [44 — avaliação realtime vs Chatwoot](44-avaliacao-realtime-websocket-vs-chatwoot.md) — L1 e §7.2 item 12 (o padrão portado aqui)
