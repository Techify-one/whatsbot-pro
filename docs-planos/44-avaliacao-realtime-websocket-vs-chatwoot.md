# 44 — Avaliação da arquitetura Real-time / WebSocket do WhatsBot vs Chatwoot

> **Tipo:** avaliação técnica (não é plano de implementação — nenhum código foi alterado).
> **Data:** 2026-07-09.
> **Escopo:** como o WhatsBot entrega eventos em tempo real hoje, comparado ao Chatwoot,
> com foco em **qualidade de código**, **performance** e **confiabilidade para múltiplos canais e usuários**.
> **Método:** 35 subagentes num workflow de auditoria + 2 agentes de pesquisa de sistemas grandes +
> leitura em 1ª mão dos dois códigos (Chatwoot clonado via sparse-checkout e inspecionado).
> Cada bug passou por **verificação adversarial** (um 2º agente tentando refutar).
>
> **Documentos relacionados:**
> - [45 — Registro de bugs e riscos (real-time + pipeline)](45-registro-bugs-riscos-realtime.md) — os 16 achados verificados, com arquivo:linha, cenário de falha e correção.

---

## 0. Sumário executivo

**Veredito de qualidade.** O núcleo de WebSocket do WhatsBot é **bem-feito para o alvo atual** —
uma única instância, poucos operadores por deployment. O `ConnectionManager` tem decisões
maduras (serialize-once, fan-out concorrente com timeout por socket, poda de half-open) e o
**cliente do frontend (`wsBus.js`) é sofisticado** (bus singleton, heartbeat de aplicação,
detecção de half-open, resync na reconexão). Isso é qualidade acima da média para um projeto
desse porte.

**Porém** há **três limitações estruturais** que impedem o sistema de crescer com confiança para
"muitos canais e usuários", exatamente o objetivo declarado:

| # | Limitação estrutural | Consequência |
|---|---|---|
| **L1** | **Fan-out GLOBAL sem escopo** — todo evento vai para **todos** os sockets; a filtragem por conversa/canal é 100% client-side | (a) **Vazamento de dados**: sob RBAC, um operador que não é membro de um inbox recebe no navegador o conteúdo (PII, telefone, texto) das conversas daquele inbox. (b) **Custo O(eventos × operadores)**: a banda e o trabalho do cliente crescem com a atividade **total** do sistema, não com o que cada operador olha. |
| **L2** | **Preso a 1 processo** — estado de conexões, batching, dedup, caches e locks são todos in-RAM por processo; **não há backplane (Redis/pub-sub)** | **Impossível escalar horizontalmente**. Com 2+ réplicas: painéis perdem eventos ao vivo da outra réplica; GOWA duplicado briga pela sessão; loops de fundo duplicam varreduras; mídia some entre réplicas. Adicionar `--workers N` **também não ajuda** (fragmenta o mesmo estado in-process). |
| **L3** | **Sem sequence number / replay** — o broadcast é fire-and-forget; um cliente momentaneamente desconectado **perde o evento** (não há buffer nem cursor) | Após reconectar, o painel fica com **estado defasado silenciosamente** até um refetch. O resync atual (refetch cego de sidebar + thread aberta) cobre só a superfície principal e pode perder o que mudou fora da janela. |

**Comparação-chave com o Chatwoot.** O Chatwoot resolve exatamente L1/L2/L3 com um padrão
maduro e barato: **um único canal (`RoomChannel`) com muitos `stream_from` por `pubsub_token`**
(escopo por usuário/conta), **backbone Redis** (multi-processo), **broadcast direcionado calculado
por evento** (só os tokens certos: membros do inbox + admins + contato) **executado
assincronamente no Sidekiq**, e um **cliente com delta-sync time-windowed** na reconexão. Nenhuma
dessas ideias exige a stack do Chatwoot — todas são portáveis para FastAPI.

**16 achados verificados** (2 foram refutados na verificação adversarial). Além das 3 limitações
estruturais, os destaques acionáveis de curto prazo: busca da sidebar faz **full-scan da tabela
`messages`** a cada tecla; a **supressão de echo derruba mensagens legítimas** do cliente; o **pool
de conexões do DB** está no default (15) muito abaixo da concorrência de `to_thread`; a query de
preview repete **6 subqueries correlacionadas por linha**; e **três dicts de estado crescem sem
limite** (vazamento de memória lento). Ver documento [45](45-registro-bugs-riscos-realtime.md).

---

## 1. Metodologia

1. **Leitura em 1ª mão** dos dois códigos:
   - WhatsBot: `server/state.py`, `server/routes/websocket.py`, `plugins/context.py`,
     `web/static/js/services/wsBus.js`, `web/static/js/hooks/useWebSocket.js`, `db/engine.py`,
     `main.py`, e os call sites de `broadcast(`.
   - Chatwoot: clonado por `git clone --depth 1 --filter=blob:none --sparse` e inspecionado —
     `app/channels/room_channel.rb`, `app/channels/application_cable/`,
     `app/listeners/action_cable_listener.rb`, `config/cable.yml`,
     `app/javascript/shared/helpers/BaseActionCableConnector.js`.
2. **Workflow de auditoria (35 subagentes)**: 6 agentes mapeando subsistemas do WhatsBot, 3
   dissecando o Chatwoot, 3 de boas práticas, 5 caça-bugs por lente (races, thread-safety,
   segurança/escopo, memória, performance/DB) → dedup → **verificação adversarial** de cada achado.
3. **2 agentes de pesquisa** de "o que sistemas grandes recomendam" (Slack, Discord, Figma,
   Linear, Chatwoot) e "stacks Python para escalar real-time" (broadcaster, Redis Pub/Sub/Streams,
   Centrifugo, Soketi, Mercure, Pushpin; armadilhas de asyncio; sizing de pool).

---

## 2. Como o WhatsBot faz real-time hoje

### 2.1 Servidor — `ConnectionManager` ([server/state.py](../server/state.py))

- **Estado**: `self.active: list[WebSocket]` — uma **lista plana**, sem lock, sem rooms, sem índice
  por usuário/conversa/canal, sem cap de conexões.
- **Broadcast** ([state.py:68](../server/state.py#L68)): serializa o envelope **uma vez**
  (`json.dumps`), tira um snapshot defensivo `list(self.active)`, e faz **fan-out concorrente** via
  `asyncio.gather`, com cada envio embrulhado em `asyncio.wait_for(..., timeout=SEND_TIMEOUT=5.0)`.
  Sockets que estouram o timeout/lançam são **podados e ativamente fechados** (`ws.close()`) para o
  cliente disparar `onclose` → reconectar. **Nunca levanta** (swallow total) — um socket ruim não
  quebra a entrega aos demais nem a ação que disparou o broadcast.
- **Auth do `/ws`** ([websocket.py](../server/routes/websocket.py)): token via `?token=` na query
  string, resolvido em `asyncio.to_thread` (hit no DB por conexão). Sob RBAC exige `kind=='user'`;
  senão aceita user OU o token legado de senha única; instalação aberta não checa nada. O
  `_user` resolvido é **descartado** — o gate é binário (autenticado sim/não), **sem autorização
  por conteúdo**.
- **Estado inicial** empurrado no connect (`status`, `gowa_status`, `qr_update`) hidrata a UI na hora.
- **Keep-alive** orientado pelo cliente (o servidor só responde `pong` a `{"action":"ping"}`).
- **Ponte thread→loop** ([plugins/context.py:153](../plugins/context.py#L153)): chamadas de outros
  threads (plugins, repos síncronos) usam `asyncio.run_coroutine_threadsafe(ws_manager.broadcast(...), loop)`.

### 2.2 Emissão de eventos

- ~30–39 call sites chamam `broadcast(` (rotas de contatos/conversas/sandbox, webhook, avatares,
  balance monitor, `ws_projections` para o ciclo de vida da conversa). Catálogo de ~25 eventos
  (`new_message`, `message_reaction`, `conversation_upsert`, `ai_typing`, `avatar_updated`, …).
- **Payloads são enxutos** (mídia trafega por `media_path`, não base64) — bom.
- **Ponto fraco**: `new_message` é emitido em **~39 lugares** com shapes de payload divergentes
  (`channel_id`/`id` ora presentes, ora ausentes) — risco de drift; e fora do ciclo de vida da
  conversa, cada mutação chama o broadcast **inline manualmente** (esquecer um deixa o painel
  desatualizado até um refetch).

### 2.3 Frontend — `wsBus.js` ([web/static/js/services/wsBus.js](../web/static/js/services/wsBus.js))

- **Bus singleton**: 1 socket por aba, fan-out para todos os subscribers (elimina o bug anterior de
  N sockets por componente).
- **Heartbeat de aplicação** (ping 25s / timeout 40s) + detecção de half-open dos dois lados
  (cliente + poda do servidor) — robusto a sleep/NAT/carrier blip.
- **Resync na reconexão**: `onWsConnect` refaz fetch da sidebar (debounce) + reload da thread aberta,
  idempotente por `msg_id`.
- **Fraquezas**: reconnect com **timer fixo de 3s, sem jitter nem backoff** (reconnect storm no
  restart do servidor); resync cobre **só** sidebar + thread aberta (avatars/tags/plugins/kanban que
  mudaram no gap se perdem); **sem sequence/replay** (refetch cego); um socket por aba (sem
  SharedWorker); `event→setState` sem coalescing (render churn em rajadas).

### 2.4 Pipeline de mensagens (concorrência)

- Batching por `(channel_id, phone)`, typing orchestrator, dedup de echo (`recently_sent`), lock
  sequencial por canal (`channel_ai_locks`). Modelo **single-event-loop** dá atomicidade sem locks de
  thread.
- **Fraquezas** (ver doc 45): DB calls **síncronos** dentro de `_run_one_cycle` (bloqueiam o loop
  para todos os contatos); dicts que **crescem sem limite**; eviction não-FIFO de `processed_messages`;
  colisão de chave em `recently_sent` que derruba mensagens.

### 2.5 Forças reais (mérito de engenharia, não inflar nem diminuir)

- Serialize-once + `gather` + `SEND_TIMEOUT` → **sem head-of-line blocking**; latência de pior caso
  limitada a 5s (não ao timeout TCP do SO).
- Poda + `close()` ativo de half-open → fecha o buraco onde um socket morto nunca reconectava.
- Thread-safety **por confinamento no loop** (ponte `run_coroutine_threadsafe`), sem locks nem contenção.
- Cliente `wsBus` com heartbeat + resync já cobre boa parte da confiabilidade que muitos projetos ignoram.
- Payloads WS enxutos; mídia fora do canal WS.
- Banco já **multi-process-safe** (`pool_pre_ping`, `prepare_threshold=None` para PgBouncer).

---

## 3. Como o Chatwoot faz real-time (ActionCable)

> Fonte: código clonado localmente. As ideias abaixo são o que vale copiar — nenhuma depende de Rails.

### 3.1 Um canal, muitos streams por token ([room_channel.rb](https://github.com/chatwoot/chatwoot/blob/develop/app/channels/room_channel.rb))

Existe **um único** `RoomChannel`. O escopo **não** é feito com muitas classes de canal — é feito com
`stream_from` por tópico, dentro desse único canal:

```ruby
def ensure_stream
  stream_from pubsub_token                                   # stream PRIVADO por usuário/contato
  stream_from "account_#{@current_account.id}" if @current_user.is_a?(User)  # stream da conta (só agentes)
end
```

- **Auth = posse do `pubsub_token`**: um token opaco não-adivinhável por usuário (`has_secure_token`).
  `current_user` faz `User.find_by!(pubsub_token:, id:)` — token **e** id têm que casar, senão a
  assinatura é rejeitada. **Autorização = membership de conta**: `@current_user.accounts.find(account_id)`
  levanta se o usuário não é membro. Um token da conta A não assina a conta B.

### 3.2 Broadcast **direcionado**, nunca global ([action_cable_listener.rb](https://github.com/chatwoot/chatwoot/blob/develop/app/listeners/action_cable_listener.rb))

Há **um método por evento de domínio**. Cada método **calcula o conjunto exato de destinatários** e
publica só para eles:

```ruby
def message_created(event)
  message, account = extract_message_and_account(event)
  conversation = message.conversation
  tokens = user_tokens(account, conversation.inbox.members) + contact_tokens(conversation.contact_inbox, message)
  broadcast(account, tokens, MESSAGE_CREATED, message.push_event_data)
end

def broadcast(account, tokens, event_name, data)
  return if tokens.blank?
  payload = data.merge(account_id: account.id)
  payload[:performer] = Current.user&.push_event_data if Current.user.present?   # quem executou a ação
  ::ActionCableBroadcastJob.perform_later(tokens.uniq, event_name, payload)      # ASSÍNCRONO (Sidekiq)
end
```

- `user_tokens` = tokens dos **membros do inbox** + **todos os admins da conta**. `contact_tokens` = o
  token do contato (pulado para mensagens privadas/atividade). Ou seja: **as regras de visibilidade
  (RBAC) estão codificadas no cálculo dos destinatários** — um agente que não é membro daquele inbox
  **nunca recebe** a mensagem.
- O broadcast roda **fora do request**, num job do Sidekiq (fila `critical`) → o controller retorna na
  hora e um socket lento nunca bloqueia a ação.
- **`performer` carimbado** em cada payload → o front atribui a ação ("atribuído por X") sem refetch.

### 3.3 Backbone Redis ([config/cable.yml](https://github.com/chatwoot/chatwoot/blob/develop/config/cable.yml))

```yaml
default: &default
  adapter: redis
  url: <%= ENV.fetch('REDIS_URL', ...) %>
  channel_prefix: chatwoot_<env>_action_cable
```

- Qualquer processo (web **ou** worker) chama `ActionCable.server.broadcast(token, ...)`; a mensagem vai
  ao **Redis pub/sub** e o processo que **detém** aquele socket a reentrega. É isso que torna correto o
  broadcast originar num worker Sidekiq enquanto o socket vive num processo web — **totalmente
  desacoplados pelo Redis**. É o que destrava **multi-processo/multi-réplica**.

### 3.4 Presença + cliente

- **Presença**: `OnlineStatusTracker` usa **Redis ZSET com timestamp como score** + TTL
  (`PRESENCE_DURATION` 20s usuários / 90s contatos); heartbeat do cliente a cada 20s renova; entradas
  velhas são podadas por `zremrangebyscore`. Detecta "saiu" sem depender do `close` do socket.
- **Cliente** (`BaseActionCableConnector` + `ReconnectService`): **backoff exponencial nativo** do
  `@rails/actioncable` (~3s→30s com jitter); **delta-sync time-windowed** na reconexão (refetch só das
  linhas `updatedWithin = segundosDesconectado + 15s`, respeitando filtros ativos, + mensagens após o
  último id da thread aberta); **hard reload** se o gap > 3h (`MAX_DISCONNECT_SECONDS`); defesa em
  profundidade `isAValidEvent(data.account_id === currentAccountId)`; typing com **auto-off de 30s** se
  o `typing_off` se perder.

### 3.5 Escala

- **Web e worker separados** (Procfile / `docker-compose.production.yaml`), escalam por dimensões
  diferentes. **Sidekiq** tira todo side-effect do request (broadcast, webhooks, automações). **Redis**
  tem 3 papéis (pub/sub do cable + broker do Sidekiq + presença/locks). **Sticky sessions** + read
  timeout do LB ~3600s são obrigatórios com múltiplos web. **AnyCable** (Go/gRPC) é o upgrade quando o
  gargalo vira nº de conexões (~3–4× menos RAM por socket). Números oficiais: 4c/4GB ≈ 10k conversas/dia,
  8c/8GB ≈ 20k/dia; acima → horizontal.

---

## 4. Comparação lado a lado

| Dimensão | **WhatsBot (hoje)** | **Chatwoot** |
|---|---|---|
| **Escopo de entrega** | Fan-out **global** — todo evento a todos os sockets; filtro no cliente | `stream_from` por `pubsub_token` + `account_#{id}`; **destinatários calculados por evento** (membros do inbox + admins + contato) |
| **Autorização no canal** | Binária (autenticado sim/não); `_user` descartado; **sem RBAC no WS** | RBAC codificado no cálculo de tokens; token da conta A não vê conta B |
| **Backbone** | Lista in-memory, **1 processo** (sem Redis) | **Redis pub/sub** adapter → **N processos web/worker** |
| **Onde roda o broadcast** | Inline no request/thread (fire-and-forget) | **Assíncrono** no Sidekiq (fila `critical`), fora do request |
| **Auth do socket** | Token (sessão/senha) na **query string** | `pubsub_token` (capability token) não-adivinhável |
| **Reconexão** | Fixo 3s, **sem jitter/backoff** | Backoff exponencial + jitter (nativo) |
| **Resync pós-reconexão** | Refetch cego (sidebar + thread), **sem replay** | **Delta-sync time-windowed** + hard reload se gap > 3h |
| **Sequence/replay** | ❌ nenhum — eventos no gap **se perdem** | Sem seq no transporte, mas merges **idempotentes por id** + delta-refetch autoritativo |
| **Presença** | `ai_typing` pontual; sem presença de operador | Redis ZSET + TTL + heartbeat; typing exclui o autor, auto-off 30s |
| **Escala horizontal** | ❌ impossível hoje (estado in-process) | ✅ web+worker separados, Redis, AnyCable como upgrade |
| **Atribuição de ação** | Não carimbada no payload | `performer` em cada evento |
| **Backpressure** | `SEND_TIMEOUT` por socket (bom) | Idem em nível de ActionCable + broadcast fora do request |
| **Fan-out concorrente** | ✅ `asyncio.gather` + timeout (bem-feito) | ✅ via Redis + processo dono do socket |
| **Heartbeat de app** | ✅ `wsBus` 25/40s (bem-feito) | ✅ presença 20s |

**Leitura da tabela:** o WhatsBot **empata ou ganha** nos detalhes de baixo nível do fan-out
concorrente e do heartbeat do cliente — o time fez um trabalho sólido ali. O Chatwoot ganha
decisivamente nas **três dimensões estruturais** (escopo/RBAC no canal, backbone multi-processo,
resync com delta-sync) que são justamente o que importa para "muitos canais e usuários".

---

## 5. O que sistemas grandes recomendam

### 5.1 Padrões por sistema

- **Slack — Flannel (edge cache/gateway):** uma camada de borda separada segura as conexões e serve o
  cache de bootstrap do time; roteamento por afinidade (consistent hashing) evita reconnection storms
  baterem no core. *Lição:* separe "quem segura a conexão" de "fonte da verdade". (Over-engineering no
  nosso porte, mas o princípio de separar a camada WS vale.)
- **Discord — Gateway + sequence/resume:** todo evento carrega um `s` (sequence number); o cliente
  guarda o último `s` + `session_id`; na queda manda **Resume** e o servidor **reenvia em ordem os
  eventos perdidos**. Fan-out é por **guild/canal**, nunca global (biblioteca *Manifold* agrupa por nó
  para fan-out em lote). *Lição:* **sequence + replay é o mecanismo canônico** para não perder evento na
  reconexão; escopo por sala é obrigatório.
- **Figma / Linear — sync engine:** servidor é a **autoridade que ordena os eventos** (dispensa vetor
  de versão no cliente); **LWW** (last-write-wins) por propriedade resolve conflito sem CRDT/OT; **delta
  sync desde um cursor**; offline-first. *Lição:* para painel operacional (não editor de texto), **LWW
  arbitrado pelo servidor** é suficiente e muito mais simples.
- **Chatwoot — inbox scoping:** escopo por conversa/inbox/conta via token; presença e roteamento
  compartilham a mesma infra Redis pub/sub. *(Detalhe na seção 3.)*

### 5.2 Padrões transversais da indústria

| Padrão | Por que | Aplicação no WhatsBot |
|---|---|---|
| **Backplane pub/sub (Redis)** | Múltiplos processos precisam entregar o mesmo evento a sockets que podem estar em processos diferentes | Introduzir **antes** de rodar 2+ réplicas/workers; Postgres continua a fonte da verdade |
| **Sequence numbers + catch-up/replay** | Reconexão é o normal em mobile; sem replay a UI dessincroniza até refresh | `seq` global + ring buffer (~500 eventos / 5 min); cliente manda `since=last_seq` no reconnect |
| **At-least-once + idempotência no cliente** | Exactly-once é impossível; deduplicar por id é barato | `INSERT ... ON CONFLICT` por `msg_id` (já existe `db.upsert`) + dedupe por `msg_id` na renderização |
| **Backpressure / slow-consumer** | 1 cliente lento pode estourar a memória do processo | O `SEND_TIMEOUT` já cobre parte; adicionar teto de fila por socket / QoS (descartar `ai_typing`, nunca `new_message`) |
| **Presença via heartbeat + TTL** | Detecta "saiu" sem depender do close do socket (crítico: atendentes usam o painel no celular) | Chave Redis com `EXPIRE`, renovada a cada ~30s |
| **Sticky sessions vs stateless** | WS é stateful/long-lived; com múltiplas réplicas precisa de afinidade OU estado externalizado | Só relevante ao adotar multi-réplica; com backbone Redis, sticky deixa de ser requisito de **corretude** |
| **Backoff exponencial + jitter** | Evita thundering herd na volta do servidor | Trocar o `setTimeout(_connect, 3000)` fixo por Full Jitter |

### 5.3 Stacks Python (opções de backbone, para quando escalar)

| Solução | Modelo | Replay/durabilidade | Observação |
|---|---|---|---|
| **Redis Pub/Sub direto** (`redis.asyncio`) | você implementa a camada | ❌ (fire-and-forget) | Mais simples, ~1ms/msg. **Escolha proporcional ao WhatsBot** |
| **Redis Streams** | idem | ✅ (consumer groups + ACK) | Use pontualmente onde precisa de replay |
| `encode/broadcaster` | lib embutida (Redis/Postgres/Kafka) | depende do backend | **Arquivada desde ago/2025** — fixar `0.3.0` ou reimplementar fino |
| **Centrifugo / Soketi** | servidor WS dedicado (Go) | ✅ (history/presence prontos) | Vale quando presença/rooms/escala viram requisito de 1ª classe — **hoje é desproporcional** |
| Postgres `LISTEN/NOTIFY` | via broadcaster | ❌ | Limite de 8000 bytes/payload; usar só como "acorde e busque por id" |

**Armadilhas de asyncio confirmadas no WhatsBot** (fontes na pesquisa): (1) o `run_coroutine_threadsafe`
de `plugins.context.broadcast` **descarta o Future** → exceção na corrotina (ex.: `json.dumps` de um
payload não-serializável) some silenciosamente (ver bug #9); (2) `asyncio.to_thread` + pool síncrono do
SQLAlchemy: o thread pool do AnyIO tem 40 tokens e o pool do DB tem 15 (default) → **contenção/starvation**
sob carga (ver bug #14); (3) **nunca bloquear o loop** — DB síncrono no path async derruba conexões WS por
timeout de ping (ver bug do `_run_one_cycle`).

---

## 6. Avaliação de qualidade por dimensão

Nota honesta (0–5) com justificativa. "Alvo atual" = 1 instância, poucos operadores. "Alvo declarado"
= muitos canais e usuários.

| Dimensão | Nota (alvo atual) | Nota (alvo declarado) | Justificativa |
|---|---|---|---|
| **Núcleo do fan-out WS** | 4.5 | 2.5 | Excelente no baixo nível (serialize-once, gather, timeout, poda). Cai no alvo declarado por ser global e single-process. |
| **Emissão / catálogo de eventos** | 3.5 | 2.5 | Payloads enxutos e ponte thread-safe. Duplicação de ~39 call sites de `new_message` e acoplamento inline geram risco de drift. |
| **Cliente frontend (`wsBus`)** | 4.5 | 3.5 | Sofisticado (singleton, heartbeat, half-open, resync). Falta jitter/backoff e delta-sync/replay. |
| **Pipeline de concorrência** | 3.5 | 2.5 | Modelo single-loop dá atomicidade. Mas DB síncrono no `_run_one_cycle`, dicts sem poda e echo suppression por texto são riscos reais sob carga. |
| **Deploy / escala horizontal** | 4.0 | 1.5 | Ótimo para 1 box (simples, GOWA supervisionado, DB pronto p/ pooler). Impossível multi-réplica sem reescrita. |
| **Camada de dados sob carga** | 3.0 | 2.0 | Índices certos e ECST no broadcast. Mas pool default, N+1 de preview, busca full-scan e executor compartilhado com transcrição. |
| **Segurança do canal WS** | 3.0 | 2.0 | Auth funcional e RBAC-aware no handshake. Mas token na URL, sem re-auth por evento, e o fan-out global vaza dados sob RBAC. |

**Resumo:** um **produto single-instance de boa qualidade** que precisa de **três mudanças
estruturais** para virar um produto multi-canal/multi-usuário confiável.

---

## 7. Roteiro de melhorias priorizado

> Priorização por **(valor ÷ risco)**. Nada aqui foi implementado — é recomendação.

### 7.1 Quick wins (baixo risco, alto valor — não mudam arquitetura)

1. **Backoff exponencial + jitter no reconnect** ([wsBus.js:126](../web/static/js/services/wsBus.js#L126)):
   `delay = Math.random() * Math.min(30000, 500 * 2**attempt)`, reset em `onopen` estável. Mata o
   reconnect storm no redeploy (o `os._exit` de toggle de plugin torna isso **real**, não teórico).
2. **Logar falha do broadcast threaded** ([context.py:153](../plugins/context.py#L153)): anexar
   `add_done_callback` ao Future para não engolir exceção; mover o `json.dumps` para dentro de try. (bug #9)
3. **Pool de DB explícito via env** ([engine.py:96](../db/engine.py#L96)): `pool_size`/`max_overflow`/
   `pool_timeout` dimensionados junto com o limiter do AnyIO. (bug #14)
4. **`LATERAL JOIN` no preview de conversas** ([conversation_query.py:86](../db/repositories/conversation_query.py#L86)):
   trocar 6 subqueries correlacionadas por 1 LATERAL (~6× menos probes por load de sidebar). (bug #15)
5. **Paginar `GET /api/contacts`** e trocar `msg_count` correlacionado por agregado. (bug #16)
6. **Busca da sidebar via SQL** (`ILIKE`/`unaccent`/`pg_trgm` + LIMIT) em vez de full-scan + fold em
   Python. (bug #2)
7. **Corrigir echo suppression**: deduplicar echo por `external_msg_id`, não por texto; setar
   `recently_sent` só após envio bem-sucedido. (bugs #3, #4)
8. **Podar os dicts que vazam**: `presence_conv_cache`, `typing_state`, `sending` (trocar por `.pop`,
   TTL ou LRU). (bugs #10, #11, #12)
9. **FIFO/LRU no `processed_messages`** (hoje evicta chaves arbitrárias) + podar em ponto compartilhado. (bugs #7, #8)
10. **DB calls síncronos → `to_thread` no `_run_one_cycle`** (hoje congelam o loop para todos os contatos).
11. **Graceful WS close no shutdown** do lifespan (`await ws.close(code=1001)`) antes do uvicorn forçar 1012/1006.

### 7.2 Médio prazo (escopo + segurança — resolve L1 e L3, ainda single-process)

12. **Escopo por tópico** (padrão Chatwoot, portado): amarrar identidade + inboxes visíveis ao socket
    no connect; entregar cada evento só ao escopo certo. Modelo de **dois níveis**: stream de conta
    (sidebar/previews/badges) + stream por conversa (mensagens/reações/typing). Fecha o **vazamento de
    PII** (bug #1) e corta banda.
13. **Um adapter de real-time centralizado** (papel do `ActionCableListener`): um único ouvinte dos
    eventos internos (`message.saved`, `conversation.*`) que traduz cada um em push escopado — elimina
    os ~39 broadcasts inline espalhados.
14. **Sequence number + ring buffer + resync híbrido**: `seq` monotônico em todo broadcast; cliente
    manda `since=last_seq` no reconnect; gap curto → replay, gap longo → snapshot REST. Resolve L3.
15. **Re-autorização periódica do socket** + **ticket de conexão** de uso único (em vez de token de
    sessão na URL). (bugs #5, #6)
16. **Carimbar `performer`** nos eventos para atribuição multi-operador ao vivo.
17. **Delta-sync time-windowed** no cliente (em vez de refetch cego): registrar `disconnectTime`,
    refazer só `updatedWithin`.

### 7.3 Estrutural (resolve L2 — habilita multi-réplica)

18. **Backbone Redis pub/sub** de forma **aditiva**: `ConnectionManager.broadcast` passa a **publicar**
    no Redis; uma task de fundo por processo lê e faz fan-out só para os sockets locais. A assinatura
    `ws_manager.broadcast(event, data)` **não muda** — nenhuma rota é reescrita. Roda igual com 1 worker.
19. **Separar worker tier** (empurrar transcrição/webhooks/analytics para fora do path do webhook).
20. **Object storage** (S3/MinIO) para mídia (hoje é disco local por instância).
21. **Leader election** para os loops de fundo + **um único dono do GOWA/webhook** por conta.
22. **Sticky sessions** no LB (só necessário até o backbone Redis; depois vira otimização, não corretude).

### 7.4 Caminho incremental de menor risco (ordem sugerida)

`7.1 (quick wins)` → `Passo 2 do 7.3 (Redis pub/sub aditivo, sem trocar rotas)` → `7.2 §12 (escopo por
tópico sobre o canal Redis)` → `7.2 §14 (sequence+replay)` → `restante do 7.3 (worker tier, object
storage, leader election)`. Cada passo é reversível e **não exige trocar FastAPI/Starlette**.

---

## 8. Conclusão

O WhatsBot tem um **núcleo de real-time de qualidade** para o que é hoje, com decisões de baixo nível
melhores que a média (fan-out concorrente com timeout, cliente com heartbeat e resync). O que o separa
de um sistema pronto para **muitos canais e usuários** não são detalhes — são **três escolhas
estruturais** (fan-out global, single-process, sem replay) que o Chatwoot resolve com padrões maduros e
**portáveis para Python sem reescrever o app**: escopo por tópico, backbone Redis pub/sub, e sequence +
delta-sync. O roteiro da seção 7 ataca isso em ordem de risco crescente, começando por quick wins que já
melhoram performance e confiabilidade **sem tocar na arquitetura**.

Os bugs concretos (que podem atrapalhar o sistema **hoje**, mesmo single-instance) estão no documento
[45 — Registro de bugs e riscos](45-registro-bugs-riscos-realtime.md).
