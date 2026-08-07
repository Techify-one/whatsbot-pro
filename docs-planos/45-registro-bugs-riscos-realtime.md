# 45 — Registro de bugs e riscos (Real-time + Pipeline de mensagens)

> **Tipo:** registro de achados (não é plano — nenhum código foi alterado).
> **Data:** 2026-07-09.
> **Origem:** auditoria com 5 agentes caça-bugs por lente (races, thread-safety, segurança/escopo,
> memória, performance/DB) → dedup → **verificação adversarial** (um 2º agente cético tentando
> refutar cada achado, com o código na mão). **16 achados sobreviveram** (CONFIRMED/PLAUSIBLE);
> **2 foram refutados** (ex.: a suposta race na lista `self.active` — considerada **sã por
> confinamento no event loop**).
> **Contexto:** ver [44 — Avaliação da arquitetura Real-time vs Chatwoot](44-avaliacao-realtime-websocket-vs-chatwoot.md).

**Legenda de severidade** (ajustada após a verificação): 🔴 alta · 🟠 média · 🟡 baixa.
Cada achado tem um `verdict`: **CONFIRMED** (o cético confirmou com o código) ou **PLAUSIBLE**
(mecânica confirmada, cenário concreto depende de carga).

| # | Sev | Título | Arquivo |
|---|---|---|---|
| 1 | 🔴 | Fan-out global vaza mensagens de todas as conversas para todo operador (ignora RBAC/inbox) | `server/state.py:68` |
| 2 | 🔴 | Busca da sidebar faz full-scan da tabela `messages` + fold em Python a cada request | `db/search/contact_search.py:89` |
| 3 | 🟠 | Echo suppression derruba mensagens legítimas do cliente que casam texto recente | `app/services/message_ingest_service.py:401` |
| 4 | 🟠 | `recently_sent` gravado antes do envio e não limpo em falha → suprime inbound de msg nunca entregue | `app/services/messaging_service.py:383` |
| 5 | 🟠 | Sessão revogada/expirada continua recebendo o stream no socket aberto (sem re-auth por evento) | `server/routes/websocket.py:55` |
| 6 | 🟠 | Token de sessão trafega na query string do `/ws` (exposição em logs de proxy) | `server/routes/websocket.py:23` |
| 7 | 🟠 | Poda de `processed_messages` descarta entradas arbitrárias (set sem ordem) → enfraquece idempotência | `app/services/message_ingest_service.py:539` |
| 8 | 🟡 | `processed_messages` cresce sem limite nos caminhos de echo/operator | `app/services/message_ingest_service.py:264` |
| 9 | 🟡 | Broadcast threaded engole exceção da corrotina (Future descartado) → perda silenciosa de evento | `plugins/context.py:153` |
| 10 | 🟡 | `presence_conv_cache` cresce sem limite (TTL só regula recomputação, nunca remove chave) | `server/routes/channel_webhook.py:74` |
| 11 | 🟡 | `typing_state` nunca é removido — um registro por (canal, telefone) para sempre | `server/routes/channel_webhook.py:201` |
| 12 | 🟡 | `state.sending` acumula chave por conversa (set True/False, nunca `pop`) | `app/services/messaging_service.py:705` |
| 13 | 🟡 | Reconnect WS com timer fixo de 3s sem jitter/backoff (thundering herd no restart) | `web/static/js/services/wsBus.js:126` |
| 14 | 🟡 | Pool de conexões default (15) abaixo da concorrência de `to_thread` → starvation/timeout | `db/engine.py:96` |
| 15 | 🟡 | Query de conversas repete 6 subqueries correlacionadas por linha para a última mensagem | `db/repositories/conversation_query.py:86` |
| 16 | 🟡 | `GET /api/contacts` sem paginação + `msg_count` como subquery correlacionada por contato | `db/search/contact_search.py:181` |

---

## 🔴 Alta

### 1. Fan-out global do WebSocket vaza mensagens de TODAS as conversas para TODO operador — CONFIRMED

> 📋 **Virou plano executável em 2026-07-28: [90 — escopo do WebSocket por canal](90-plano-escopo-do-websocket-por-canal.md)**, que também absorve o **#5** (sessão revogada — mesma infra de sweep). O **#6** (token na query string) fica **de fora**, com justificativa registrada lá. Re-auditado com o código de hoje: o achado continua exato, e a superfície **cresceu** de ~25 para **42 eventos** — inclusive o `conversation_upsert` (nome + telefone + preview do texto + etiquetas + atributos do contato), que nasceu **depois** deste registro. Medição em produção: o vazamento efetivo atinge ~599 conversas (4%) e 8 operadores. ⚠️ A correção sugerida abaixo ("no mínimo não incluir `content`/PII") foi **rejeitada** como mitigação barata no plano 90 (D9) — ela exige a mesma identidade no socket, quebra a serialização única e ainda deixa telefone e metadado vazando.

- **Categoria:** privacidade / vazamento de escopo · **Local:** [server/state.py:68](../server/state.py#L68)
- **Descrição:** a camada REST aplica escopo de inbox rigoroso (`server/authz.py` `visible_inbox_ids`/
  `can_access_inbox`; `_inbox_hidden` em `server/routes/conversations.py`). **O WebSocket não tem
  nenhum desses controles.** `ConnectionManager.broadcast` itera `self.active` — uma lista plana de
  sockets **sem identidade de usuário/inbox/canal** — e envia o evento para todos. Os eventos
  `new_message` carregam o **conteúdo completo** da mensagem (`role, content, ts, media`) + `phone` +
  `channel_id`. O mesmo vale para `message_reaction`, `message_revoked`, `chat_presence`, `ai_typing`.
- **Cenário de falha:** deploy com `rbac_enforce=on`, inboxes Vendas e Suporte; operador Bob só é
  membro de Suporte. Cliente manda mensagem ao número de Vendas. No REST a conversa fica **oculta**
  para Bob, mas o webhook faz `broadcast('new_message', {phone, channel_id, message:{content:'meu CPF
  é 123...'}})` que fana out para **todos** → o navegador de Bob recebe o texto/telefone/canal de uma
  conversa que ele não tem autorização de ver. **Vazamento de PII entre operadores e entre canais.**
- **Correção:** amarrar identidade ao socket no `connect` (guardar `user` + `visible_inbox_ids`) e
  filtrar cada broadcast por escopo (entregar `new_message`/`message_*`/`chat_presence` só se o
  inbox/canal estiver no conjunto visível; `None` = admin/legacy = tudo). Adicionar `inbox_id` ao
  payload de emit para a decisão. Enquanto não houver segmentação, no mínimo **não incluir `content`/PII**
  no broadcast para conexões escopadas. *(É a raiz **L1** do doc 44 e o §12 do roteiro.)*

### 2. Busca da sidebar faz full-scan de TODA a tabela `messages` + fold em Python — CONFIRMED

- **Categoria:** N+1 / full-scan · **Local:** [db/search/contact_search.py:89](../db/search/contact_search.py#L89)
- **Descrição:** `contact_ids_matching_message` (chamado por `contact_repo.list_contacts` sempre que
  `q != ''`) executa `SELECT id, contact_id, content FROM messages JOIN contacts` filtrando só por
  `is_archived`, `content != ''` e `role NOT IN (internos)`, `ORDER BY ts DESC`, **SEM LIMIT**. Depois
  itera **todas** as linhas em Python aplicando `unicodedata.normalize`/fold caractere-a-caractere.
  Não há índice em `messages.content` (o match é em Python) → **sequential scan da tabela inteira** a
  cada request. O endpoint `GET /api/contacts?q=...` alimenta a barra de busca (tipicamente por keystroke).
- **Cenário de falha:** conta com 200k mensagens: cada tecla digitada streama as 200k linhas para o
  processo Python e faz fold Unicode de cada conteúdo, segurando uma conexão do pool por centenas de ms
  a segundos. Com múltiplos operadores buscando, satura CPU **e** o pool (ver #14).
- **Correção:** empurrar o filtro de texto para o SQL — `ILIKE`/`unaccent` (extensão do Postgres) ou
  coluna/índice `tsvector`/`pg_trgm` em `messages.content`, com `LIMIT`; debounce no frontend. Evitar o
  fold Python sobre a tabela inteira.

---

## 🟠 Média

### 3. Echo suppression derruba mensagens legítimas do cliente que casam texto recente — CONFIRMED

- **Categoria:** race / perda de mensagem · **Local:** [app/services/message_ingest_service.py:401](../app/services/message_ingest_service.py#L401)
- **Descrição:** no caminho de **inbound genuíno** (`direction=='in'`, já passado o branch de echo
  `is_from_me`), o código checa `state.recently_sent` por `channel:phone:text[:120]` e, se houver
  entrada dos últimos 30s, faz `return` — **descartando a mensagem** (sem salvar, sem broadcast, sem
  IA). Como `recently_sent` é populado em **todo** envio (resposta IA, sends do operador,
  transcrição), qualquer mensagem do cliente cujo texto **igual** ao que o bot/operador acabou de
  enviar é engolida silenciosamente. Não é echo real — é colisão de texto.
- **Cenário de falha:** bot envia "Bom dia!" (grava `recently_sent`). Cliente responde "Bom dia!" 5s
  depois → `ingest_event` vê `<30s` e faz `return`: a mensagem do cliente **nunca é salva, nunca
  aparece no painel, e a IA nunca responde**. Idem para confirmações curtas ("Ok"/"Sim") ou dois
  membros de grupo mandando a mesma frase curta perto de um envio do bot.
- **Correção:** não usar match de texto fuzzy para suprimir inbound genuíno. Restringir a supressão
  `recently_sent` ao branch de echo (`_ingest_echo`) e deduplicar echoes por `external_msg_id` /
  `processed_messages`, não por texto.

### 4. `recently_sent` gravado antes do envio e nunca limpo em falha — CONFIRMED

- **Categoria:** race / perda de mensagem · **Local:** [app/services/messaging_service.py:383](../app/services/messaging_service.py#L383)
- **Descrição:** `send_reply` faz `state.recently_sent[sent_key] = time.time()` **antes** do
  `asyncio.to_thread(outbound.send_text)`. Quando o envio **falha** (`send_result.ok` falso), a função
  faz broadcast de erro e retorna **sem** dar `pop` no `sent_key`. A limpeza periódica só remove
  entradas > 60s, mas a janela de supressão de inbound é 30s → uma chave-fantasma de uma mensagem que
  **nunca foi entregue** suprime inbound do cliente por 30s. Os sends do operador têm o mesmo formato.
- **Cenário de falha:** resposta "Confirmado" da IA falha ao enviar; `recently_sent` ainda tem a
  chave. Em 30s o cliente manda "Confirmado" → `ingest_event` descarta como echo-fantasma de uma
  mensagem nunca entregue.
- **Correção:** gravar `recently_sent` só **após** envio bem-sucedido, ou dar `pop` no branch de falha
  (e em todo early-return). Melhor: chavear o dedup pelo id externo retornado pelo envio.

### 5. Sessão revogada/expirada continua recebendo o stream no socket aberto — CONFIRMED

- **Categoria:** autorização · **Local:** [server/routes/websocket.py:55](../server/routes/websocket.py#L55)
- **Descrição:** o token é validado **uma única vez** no handshake. Depois o socket fica em
  `ConnectionManager.active` **indefinidamente**; o loop keep-alive só trata `ping`, **nunca revalida**
  o token/permissão. Um operador que fez logout, teve a sessão revogada, foi desativado
  (`is_active=false`) ou teve o cargo alterado **continua recebendo todo o fluxo de eventos** enquanto
  mantiver o socket aberto. Combinado com o fan-out global (#1), é acesso contínuo às mensagens de
  todas as conversas mesmo após a revogação.
- **Cenário de falha:** operador é demitido; o admin desativa o usuário e/ou apaga a sessão. O
  navegador dele mantém o `/ws` vivo e segue recebendo `new_message` em tempo real até fechar a aba.
- **Correção:** revalidar periodicamente no keep-alive (a cada N pings re-resolver token + `is_active`;
  fechar 4401 se inválido) e/ou manter um registro `sessão→socket` para derrubar ativamente no
  logout/deactivate. Definir um TTL máximo de conexão.

### 6. Token de sessão trafega na query string do `/ws` — CONFIRMED

- **Categoria:** exposição de credencial · **Local:** [server/routes/websocket.py:23](../server/routes/websocket.py#L23)
- **Descrição:** o `/ws` lê a credencial de `websocket.query_params.get('token')` — o token de sessão
  opaco (longa duração) ou o token legado determinístico. Diferente do REST (header `Authorization`),
  a URL do WS carrega o token em texto. Query strings são rotineiramente logadas por proxies
  reversos/ingress (o alvo roda atrás do Coolify), CDNs, e ficam em histórico/Referer. Ambos os
  caminhos usam o mesmo resolver → um token capturado num log `GET /ws?token=<t>` é **replayável** como
  `Authorization: Bearer <t>` no REST = sequestro de sessão.
- **Correção:** autenticar via subprotocolo (`Sec-WebSocket-Protocol`) ou 1ª mensagem pós-`accept`; ou
  emitir um **ticket de conexão** de uso único e curta duração (obtido por POST autenticado) e passá-lo
  na URL no lugar do token de sessão.

### 7. Poda de `processed_messages` descarta entradas arbitrárias (set sem ordem) — CONFIRMED

- **Categoria:** confiabilidade / idempotência · **Local:** [app/services/message_ingest_service.py:539](../app/services/message_ingest_service.py#L539)
- **Descrição:** quando `processed_messages` passa de 5000, faz `for item in list(set)[:2500]: discard`.
  Como é um **set** (sem ordem), `list()` devolve ordem **arbitrária** → descarta 2500 chaves quaisquer,
  **não as mais antigas**, quebrando a garantia de que os dedup keys **recentes** sobrevivem. Além
  disso, a poda só roda no caminho inbound `trigger_ai`; echo, `send_media` e private-AI adicionam sem
  nunca podar.
- **Cenário de falha:** número de alto volume cruza 5000 ids; o GOWA reentrega um webhook (retry em
  reconexão/timeout) cujo `dedup_key` foi descartado aleatoriamente → a mensagem é **reprocessada**:
  segunda resposta da IA + segunda gravação no histórico.
- **Correção:** usar FIFO/LRU com ordem de inserção (`OrderedDict`, `popitem(last=False)`) e mover a
  poda para um ponto compartilhado (ou sweep periódico).

---

## 🟡 Baixa

### 8. `processed_messages` cresce sem limite nos caminhos de echo/operator — CONFIRMED
- **Local:** [message_ingest_service.py:264](../app/services/message_ingest_service.py#L264) · **resource-leak**
- A poda (trim >5000) só roda no tail do inbound genuíno. O branch de echo retorna antes; `_ingest_echo`,
  `send_media` (operador) e private-AI adicionam **sem** trim. Sob tráfego dominado por echoes do próprio
  celular ou sends do operador, o set cresce sem limite (leak lento). **Fix:** trim num helper compartilhado
  invocado por todos os sites que adicionam.

### 9. Broadcast threaded engole exceção da corrotina (Future descartado) — CONFIRMED
- **Local:** [plugins/context.py:153](../plugins/context.py#L153) · **error-handling**
- `plugins.context.broadcast` agenda `run_coroutine_threadsafe(...)` e **descarta o Future**; o try/except
  só cobre o **agendamento**, não a execução. `ConnectionManager.broadcast` faz `json.dumps` no topo,
  antes de qualquer try. Um payload não-serializável (`datetime`/`Decimal`/`bytes`) faz a corrotina
  levantar `TypeError` — e como o Future é descartado, **o evento some sem log** no ponto da falha.
  **Fix:** `add_done_callback` que loga `f.exception()`; mover o `json.dumps` para dentro de try.

### 10. `presence_conv_cache` cresce sem limite (TTL só regula recomputação) — CONFIRMED
- **Local:** [channel_webhook.py:74](../server/routes/channel_webhook.py#L74) · **memory-leak**
- `_resolve_presence_conv_id` cacheia `(conv_id, now+30)` por `(channel_id, phone)`. O TTL só decide se
  o **valor** é recomputado — a **chave nunca é removida**. Sem sweep em lugar nenhum. Cada telefone
  distinto que já disparou presença grava uma chave permanente. **Cenário:** 30k clientes ao longo de
  meses → 30k chaves permanentes, liberadas só no restart. **Fix:** podar chaves com `expires_at < now`
  (no início da função ou em sweep periódico), ou LRU com cap.

### 11. `typing_state` nunca é removido — CONFIRMED
- **Local:** [channel_webhook.py:201](../server/routes/channel_webhook.py#L201) · **memory-leak**
- `state.typing_state[(channel_id, phone)]` é gravado em todo evento de presença; os call sites só
  **atualizam** o valor (`active=False`), **nunca deletam** (grep confirma: nenhum `pop`/`del`). Mesma
  classe do #10 — um registro por contato distinto para sempre. **Fix:** `state.typing_state.pop(key, None)`
  após o ciclo, ou sweep de entradas `active=False` antigas.

### 12. `state.sending` acumula chave por conversa (nunca `pop`) — CONFIRMED
- **Local:** [messaging_service.py:705](../app/services/messaging_service.py#L705) · **memory-leak**
- `_send_with_typing_guard` faz `sending[key]=True` e no finally `=False` — nunca remove. Ao contrário de
  `state.processing`, que **é** limpo com `.pop`. Resíduo `{(channel_id, phone): False}` por conversa que
  a IA já respondeu. **Fix:** trocar `= False` por `.pop(key, None)` no finally (`False` é equivalente a
  ausência, pois os leitores usam `.get`).

### 13. Reconnect WS com timer fixo de 3s sem jitter/backoff — CONFIRMED
- **Local:** [wsBus.js:126](../web/static/js/services/wsBus.js#L126) · **reliability**
- `onclose` agenda `setTimeout(_connect, 3000)` — fixo, sem jitter/backoff/teto. No restart do servidor
  (redeploy Coolify, `os._exit` de toggle de plugin) **todos** os clientes reconectam em lockstep a cada
  3s, batendo no backend recém-iniciado (o GOWA leva ~5s) — reconnect storm. **Fix:** Full Jitter
  `Math.random()*Math.min(30000, 500*2**attempt)`, reset em `onopen` estável.

### 14. Pool de conexões default (15) abaixo da concorrência de `to_thread` — PLAUSIBLE
- **Local:** [db/engine.py:96](../db/engine.py#L96) · **db-pool**
- `create_engine()` é chamado **sem** `pool_size`/`max_overflow` → default 5+10 = **15** conexões. Todo
  DB roda via `asyncio.to_thread` (executor `min(32, cpu+4)`) e o thread pool do AnyIO (40). O nº de
  threads que podem pedir checkout (dezenas) é muito maior que 15 → quando satura, o checkout bloqueia
  até `pool_timeout` (30s) e levanta. **Cenário:** múltiplos operadores + rajada de webhooks → pico de
  20+ requisições esgota as 15 conexões, as demais travam 30s → HTTP 500. **Fix:** dimensionar
  `pool_size`/`max_overflow`/`pool_timeout` explicitamente (via env), alinhados ao limiter do AnyIO; se
  atrás de PgBouncer, considerar `NullPool`.

### 15. Query de conversas repete 6 subqueries correlacionadas por linha — CONFIRMED
- **Local:** [conversation_query.py:86](../db/repositories/conversation_query.py#L86) · **efficiency**
- `enriched_columns()` monta a última-mensagem do preview como **6 scalar subqueries correlacionadas**
  separadas (content, role, ts, media_type, status, msg_id), cada uma um `SELECT ... ORDER BY ts DESC
  LIMIT 1` para a **mesma** linha. Com `limit=100`, até **600 execuções** de subquery por load de
  sidebar. **Fix:** um único `LEFT JOIN LATERAL (SELECT ... ORDER BY ts DESC LIMIT 1)` que retorna todas
  as colunas numa passada.

### 16. `GET /api/contacts` sem paginação + `msg_count` correlacionado por contato — CONFIRMED
- **Local:** [contact_search.py:181](../db/search/contact_search.py#L181) · **efficiency**
- `build_list_contacts_query` inclui `msg_count` como subquery correlacionada `COUNT(*) FROM messages
  WHERE contact_id = contacts.id` (por linha) + `MAX(ts) GROUP BY contact_id` sobre a tabela inteira. O
  route `GET /api/contacts` chama `list_contacts` **sem** `limit/offset` (ao contrário de
  `list_conversations`, que pagina). **Cenário:** milhares de contatos + centenas de milhares de
  mensagens → cada request materializa todos os contatos com N counts, segurando conexão por segundos.
  **Fix:** paginar (limit/offset ou keyset); trocar `msg_count` correlacionado por agregado juntado uma
  vez, ou removê-lo do payload de lista.

---

## Achados refutados na verificação (2)

A verificação adversarial **rejeitou 2 achados** — registrados aqui para transparência:

- **Suposta data race na lista `self.active`** do `ConnectionManager`: rejeitada. A mutação é
  **confinada ao event loop** (chamadas de outros threads passam pela ponte `run_coroutine_threadsafe`),
  e o `broadcast` usa snapshot `list(self.active)` — as ops são atômicas sob o GIL. **É seguro** enquanto
  ninguém fizer `active.append` cru de outro thread (hoje ninguém faz).
- Um segundo achado de menor confiança foi rejeitado por não reproduzir o cenário de falha com o código
  na mão.

Isso é um **sinal de qualidade**: os pontos mais delicados de concorrência do fan-out foram
raciocinados corretamente pelo autor original.
