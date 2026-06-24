# Plano de Implementação — 11: Runtime Multi-canal Durável (fechar o ciclo entrada→conversa→saída por canal)

> Plano de **fechamento** que liga o runtime agêntico ao modelo de canais/inbox que **já existe no
> banco**, tornando o WhatsBot multi-canal de verdade (GOWA + WhatsApp Cloud API hoje; Telegram/Email/…
> amanhã, sem `if provider ==`). Derivado da discussão de 2026-06-20 (opção **Durável** escolhida pelo
> Thiago) e da reconciliação dos planos [`01-inbox-e-conversas`](01-plano-inbox-e-conversas.md) e
> [`02-canais-e-providers`](02-plano-canais-e-providers.md) contra o **código real** (não contra o
> snapshot WF1, que está defasado — ver §0).
>
> **Gatilho concreto:** um canal `whatsapp_cloud` ("teste") foi conectado, o webhook da Meta foi
> aprovado (handshake OK) e mensagens chegam — mas **não caem no WhatsBot**: o POST do webhook por-canal
> apenas parseia e descarta (TODO Fase 0.5 em [`server/routes/channel_webhook.py:82`](../server/routes/channel_webhook.py#L82)).
> Este plano fecha esse ciclo da forma durável, não com adapter descartável.
>
> **Escopo:** (1) ingresso único por `InboundEvent`; (2) roteamento de **inbox por canal** (destravar o
> `DEFAULT_INBOX_ID` hardcoded); (3) `ContactMemory`/handler cientes de canal; (4) roteador de **saída por
> canal** via `ChannelRegistry`; (5) pipeline **capability-aware** (presença, @menção, templates/janela 24h).
>
> **Fora de escopo:** RBAC fino dos endpoints (plano 03 — já há schema/repos; gating fica `auth_required`);
> extração do GOWA para plugin + subprocesso gerenciado (plano 02 Fase 3 + plano 09); cifragem de
> credenciais em repouso (P15 — dívida aceita); download/cache de mídia da Cloud (P16 — fase própria abaixo).

---

## 0. Estado atual VERIFICADO (2026-06-20, working tree pós-`ee2d963`)

> ⚠️ Os docs `01`/`02` marcam quase tudo como `nao_feito` (snapshot WF1 2026-06-20). **O código está muito
> à frente.** Tudo abaixo foi confirmado por `grep`/leitura com âncora `arquivo:linha`. **Na
> implementação, re-ancore por `grep` (nome de função/rota), nunca por número fixo.**

### O que JÁ existe (não reconstruir)

- **Abstração de canal completa.** `channels/base.py` (`Channel`, `ChannelCapabilities` com
  `qr/templates/groups/presence/reactions/media` + `inbound_route`), `channels/events.py`
  (`InboundEvent`), `channels/registry.py` (`ChannelRegistry`). Wired em
  [`server/app.py:107-127`](../server/app.py#L107) (`register_provider(GOWAChannel)`, descoberta de
  `entry.channels` de plugins, `add_channel`). Em `ServerDeps` (`server/app.py:68,177`).
- **Tabelas de canal.** `channels`, `channel_credentials` ([`db/tables.py:203-221`](../db/tables.py#L203)).
  Repos `channel_repo.py`, `channel_credential_repo.py`. No banco: canal `default` (provider `gowa`) +
  canal `teste` (provider `whatsapp_cloud`, credenciais `access_token/app_secret/phone_number_id/verify_token`
  preenchidas e **mascaradas** na API).
- **Modelo de inbox de 3 níveis (plano 01) — schema + repos + uso PARCIAL no runtime.**
  - Tabelas: `inboxes` (com `channel_id` → `channels.id`, `default_agent_key`, `agent_bot_enabled`),
    `contact_inboxes` (identidade por canal, unique `(inbox_id, source_id=JID)`), `conversations`
    (`inbox_id`, `status open|closed`, `ai_active`, `assignee_user_id`, `active_agent_key`,
    `display_id`), `conversation_counters`. `messages.channel_id` e `messages.conversation_id` **existem**
    ([`db/tables.py:300-373`](../db/tables.py#L300)).
  - Repos: `inbox_repo`, `contact_inbox_repo`, `conversation_repo`.
  - **Runtime já resolve conversa:** `ContactMemory.add_message` chama
    `conversation_repo.resolve_for_contact(self.id, self._jid(), reopen_if_closed=...)`
    ([`agent/memory.py:148-160`](../agent/memory.py#L148)) e carimba `conversation_id` em toda mensagem.
  - **AGNO já honra a cadeia conversa→inbox→default** para escolher agente
    ([`agent/agent_factory.py:98-105`](../agent/agent_factory.py#L98)).
- **Webhook por-canal + handshake Cloud.** `GET/POST /api/webhook/{provider}/{channel_id}`
  ([`server/routes/channel_webhook.py`](../server/routes/channel_webhook.py)): GET valida `verify_token`
  e ecoa `hub.challenge`; POST parseia via `registry.get(channel_id).parse_inbound(raw)` e **registra**.
  Isento de auth em `_AUTH_EXEMPT_PREFIXES` (`/api/webhook/`).
- **Provider Cloud completo.** `assets/plugin_examples/whatsapp_cloud/channels.py`:
  `WhatsAppCloudChannel` com `parse_inbound` (texto/mídia/location/reaction/interactive + `statuses`),
  `send_text`/`send_media`/`send_template`/`react`/`mark_read`, `status()` (ping Graph). Produz
  `InboundEvent` canônico. Mídia só registra `media_id` (download = P16, ainda TODO).
- **Rotas REST de inbox/conversa** já registradas (`inboxes`, `conversations` em `server/app.py:20`).

### O GAP (o que falta para multi-canal de verdade)

1. **🔴 Ingresso do Cloud é descartado.** [`channel_webhook.py:80-84`](../server/routes/channel_webhook.py#L80):
   parseia `InboundEvent` e retorna — **não** alimenta o pipeline agêntico. (TODO Fase 0.5.)
2. **🔴 Inbox hardcoded.** `conversation_repo.resolve_for_contact` usa
   **`inbox_id=DEFAULT_INBOX_ID`** fixo ([`conversation_repo.py:86-100`](../db/repositories/conversation_repo.py#L86)).
   Toda conversa cai na inbox default, **independente do canal** → Cloud e GOWA colidiriam na mesma thread.
3. **🔴 Saída acoplada ao GOWA.** O pipeline lê `gowa_client = deps.gowa_client`
   ([`webhook.py:436`](../server/routes/webhook.py#L436)) e responde via `gowa_client.send_message`
   ([`webhook.py:539`](../server/routes/webhook.py#L539)) — fixo, não `registry.get(channel_id).send_text()`.
4. **🟠 Runtime phone-keyed.** `agent_handler._contacts: dict[str, ContactMemory]` e
   `_get_contact(phone)` ([`agent/handler.py:74,640`](../agent/handler.py#L640)); `ContactMemory(phone)`
   ([`agent/memory.py:66`](../agent/memory.py#L66)) não carrega `channel_id`/`inbox_id`. Sem isso a saída
   não sabe por qual canal responder.
5. **🟠 Parse do GOWA inline (não atrás do contrato).** A cadeia `if media_type is None:`
   ([`webhook.py:53-313`](../server/routes/webhook.py#L53)) + o handler `webhook` (`:1111`) produzem um
   dict ad-hoc e batcheiam — não passam por `Channel.parse_inbound` nem por `InboundEvent`.
6. **🟠 Pipeline não consulta capacidades.** Manda "digitando" (`send_chat_presence`,
   [`webhook.py:520`](../server/routes/webhook.py#L520)) e resolve @menção pra todos
   ([`webhook.py:528-530`](../server/routes/webhook.py#L528)) sem olhar `ChannelCapabilities`
   (`presence=False`/`groups=False` no Cloud). Sem janela de 24h / template fallback.

---

## 1. Princípio de arquitetura (alvo)

Convergir **todo provider** num ingresso único e rotear a saída pelo canal da conversa:

```
GOWA webhook  ─┐
Cloud webhook ─┼─→ Channel.parse_inbound(raw) → InboundEvent ─→ ingest_event(event)
Telegram …    ─┘                                                      │
                                  resolve_inbox_for_channel(event.channel_id)        (GAP #2)
                                  resolve contato + contact_inbox + conversa          (já existe, generalizar)
                                                      │
                                        AgentHandler (capability-aware)               (GAP #6)
                                                      │
                                  OutboundRouter.send(conv.channel_id, ...)           (GAP #3/#4)
                                  └─ registry.get(channel_id).send_text()/send_media()
```

Regras invioláveis (espelham os planos 01/02):
- **Um `Channel` novo = parse + send + capabilities. Zero mudança no core.** Nenhum `if provider ==` no
  handler/pipeline.
- **Uma inbox por canal** (`inboxes.channel_id`). O canal `default` (GOWA) usa a inbox default existente
  (migração de compat). Conversas são **separadas por canal** (via `contact_inboxes`), mesmo número.
- **Contato pode permanecer unificado por `phone`** no MVP (dedup cross-canal é evolução); a **conversa**
  é que é por-canal. (Decisão D2 abaixo.)
- **Saída sempre por `registry.get(conv.channel_id)`** — nunca `gowa_client` direto.
- **Capacidades dirigem o comportamento** (presença, grupos, @menção, templates) — nunca o nome do provider.

---

## 2. Decisões a confirmar (defaults propostos)

| # | Questão | Default proposto (durável) | Impacto |
|---|---|---|---|
| **D1** | Conversa unificada ou separada por canal p/ a mesma pessoa? | **Separada** (1 inbox por canal; `contact_inboxes` resolve por JID). | É a própria espinha do durável; já suportado pelo schema. |
| **D2** | Identidade de contato cross-canal | **Unificada por `phone` no MVP** (1 row em `contacts`, N `contact_inboxes`). Dedup avançado depois. | Evita refactor profundo de `contacts`; conversas já ficam separadas. |
| **D3** | De-phone-key o `agent_handler._contacts` agora? | **Não totalmente** — manter cache por chave composta `(channel_id, phone)`; passar `channel_id` ao `ContactMemory`. | Menor risco; suficiente para roteamento correto de saída. |
| **D4** | Mídia inbound da Cloud (download Graph) | **Fase própria (5)**, depois do ciclo texto funcionar (P16). | Texto responde já; mídia vem logo após. |
| **D5** | Janela de 24h / templates fora dela | **Fase própria (6)**, após o principal (P17). | Não bloqueia o "responde ponta-a-ponta". |

> Se algum default não servir, ajustar **antes** da Fase 2 (onde o roteamento de inbox é cravado).

---

## 3. Fases

### Fase 0 — Ingresso único por `InboundEvent` (sem mudar comportamento do GOWA)
> Objetivo: criar o funil comum sem regressão. **Autocontida.**

- **0.1** Extrair o parse inline do GOWA ([`webhook.py:53-313`](../server/routes/webhook.py#L53) +
  lógica do handler até ~`:1480`) para uma função pura `parse_gowa_inbound(raw) -> list[InboundEvent]`,
  reusada por `GOWAChannel.parse_inbound`. **Comportamento idêntico** (cobrir com os testes atuais).
- **0.2** Definir `ingest_event(event: InboundEvent)` — o ponto único que recebe eventos de **qualquer**
  canal e dispara o batch/pipeline. Inicialmente o GOWA passa a chamá-lo (refactor incremental: começar
  pelo caminho de texto, manter os demais).
- **0.3** Chave de batch passa de `phone` para `(channel_id, chat_id)` (acumulador em `webhook.py`),
  preservando o merge por `message_batch_delay`.

**Pronto:** GOWA continua recebendo/respondendo igual, agora via `parse_gowa_inbound → InboundEvent →
ingest_event`. `tests/test_endpoints.py` verde.

### Fase 1 — Inbox por canal (destravar `DEFAULT_INBOX_ID`)
> Objetivo: cada canal tem sua inbox; conversas roteadas por canal.

- **1.1** Migration Alembic (head real no momento; número ≥ próximo livre, **P82 linear**): garantir
  **1 inbox por canal** — backfill: inbox default ↔ canal `default`; criar inbox para o canal `teste`
  (e para todo canal existente sem inbox). `inboxes.channel_id` populado.
- **1.2** Generalizar `conversation_repo.resolve_for_contact(contact_id, jid, *, inbox_id, ...)` —
  receber a **inbox** (resolvida do canal) em vez de `DEFAULT_INBOX_ID` fixo
  ([`conversation_repo.py:86-100`](../db/repositories/conversation_repo.py#L86)). Idem
  `contact_inbox_repo.get_or_create` (já parametrizado por `inbox_id`).
- **1.3** `inbox_repo.get_by_channel(channel_id)` (novo) + cache. `ingest_event` resolve a inbox do
  `event.channel_id` e passa adiante.

**Pronto:** uma mensagem do canal `teste` cria/usa conversa na inbox do `teste`; do GOWA, na inbox
default. Duas conversas separadas para o mesmo número em canais diferentes (D1).

### Fase 2 — `ContactMemory`/handler cientes de canal + roteador de saída
> Objetivo: a resposta sai pelo canal de origem.

- **2.1** `ContactMemory(phone, *, channel_id, inbox_id)` ([`agent/memory.py:66`](../agent/memory.py#L66))
  carrega o canal; `add_message` usa a inbox do canal ao resolver conversa.
- **2.2** `AgentHandler._get_contact` ([`agent/handler.py:640`](../agent/handler.py#L640)) passa a
  chavear por `(channel_id, phone)` (D3) e injetar `channel_id`.
- **2.3** **`OutboundRouter`** (novo, `channels/outbound.py` ou método do registry): `send_text/send_media/
  send_presence/react/mark_read(channel_id, ...)` → `registry.get(channel_id)`. Substituir os sites
  `gowa_client.*` do `_send_reply` e afins ([`webhook.py:520,539,666,…`](../server/routes/webhook.py#L539))
  por `OutboundRouter`. `group_mentions.init` resolve o client do canal quando aplicável.
- **2.4** `channel_webhook.py` POST: trocar o TODO ([`:82`](../server/routes/channel_webhook.py#L82)) por
  `for ev in events: await ingest_event(ev)` (filtrando `kind=="message"`; `receipt`/`reaction` → eventos
  do bus existentes).

**Pronto:** mensagem do WhatsApp **oficial** (Cloud) cai no WhatsBot, vira conversa, o agente responde e a
resposta volta **pelo número oficial** (Graph API). GOWA inalterado.

### Fase 3 — Pipeline capability-aware
> Objetivo: tirar as suposições GOWA-only do pipeline.

- Consultar `registry.get(channel_id).capabilities` antes de: enviar "digitando"
  (`presence`), resolver @menção / lógica de grupo (`groups`), reagir (`reactions`). Cloud
  (`presence=False/groups=False`) **pula** esses passos sem `if provider ==`.
- Centralizar a checagem para que Telegram/Email entrem só implementando `capabilities`.

**Pronto:** nenhum passo GOWA-only roda em canal que não o suporta; sem exceções/erros no log do Cloud.

### Fase 4 — UX de inbox multi-canal (frontend)
> Objetivo: operador distingue e opera os canais.

- Indicador de canal na lista/header (ícone+cor por provider) e, com **≥2 inboxes**, o rail de inboxes
  (FQ1/FQ7 do plano 01/10). Reaproveitar `ChannelsManager` existente.
- Tema `wa-*`/`.wa-field` (regra do CLAUDE.md), testar modo escuro.

**Pronto:** dá pra ver de qual canal cada conversa veio e responder no contexto certo.

### Fase 5 — Mídia inbound da Cloud (P16)
- `WhatsAppCloudChannel.parse_inbound`: resolver `media_id` → `GET /{media_id}` → URL temporária →
  baixar e cachear em `statics/media/` no mesmo esquema de `media_path` do GOWA. Liga transcrição/descrição
  (já existentes) para o Cloud.

**Pronto:** áudio/imagem/documento recebidos no número oficial aparecem no histórico e são transcritos.

### Fase 6 — Janela de 24h + templates (P17/P19)
- Rastrear "último inbound" por conversa (Cloud); fora da janela, **bloquear texto livre** e oferecer
  **template** (HSM já implementado em `send_template`). Sync de templates sob demanda (P19).

**Pronto:** operador é avisado/forçado a template fora das 24h; envio não falha silenciosamente.

---

## 4. Artefatos por categoria

- **Migrations:** 1 (inbox-por-canal + backfill, Fase 1; P82 linear no head real).
- **Módulos novos:** `parse_gowa_inbound` (Fase 0), `ingest_event` (Fase 0), `OutboundRouter`
  (`channels/outbound.py`, Fase 2), `inbox_repo.get_by_channel` (Fase 1).
- **Mudanças core:** `webhook.py` (parse extraído, batch por `(channel_id, chat_id)`, saída via router),
  `channel_webhook.py` (ingest), `conversation_repo.resolve_for_contact` (inbox param),
  `agent/memory.py` (ContactMemory ciente de canal), `agent/handler.py` (`_get_contact` por `(channel_id,
  phone)`).
- **Frontend:** indicador de canal + rail de inboxes (Fase 4).
- **Deps novas:** nenhuma.

---

## 5. Dependências e sequência

- **Plano 01 (Inbox):** este plano é o **fechamento runtime** do 01 (schema/repos já existem; falta ligar).
  Não recria o modelo — consome e generaliza (`resolve_for_contact` por inbox).
- **Plano 02 (Canais):** fecha a **Fase 0.5/2** dele (ingest + saída por canal) sem depender da Fase 3
  (GOWA-plugin) nem do plano 09 (subprocesso). GOWA segue como adapter core (`GOWAChannel`).
- **Plano 03 (RBAC):** endpoints de canal/inbox ficam `auth_required`; gating fino quando o 03 ligar.
- **Plano 09 (Runtime):** **não** é pré-requisito deste plano (não extraímos o GOWA aqui). Vem depois,
  para rodar GOWA como plugin/subprocesso gerenciado.

**Ordem interna sugerida:** Fase 0 → 1 → 2 (aqui o Cloud já responde) → 3 → 4 → 5 → 6.

---

## 6. Riscos / cuidados

- **Regressão no GOWA** ao extrair o parse (Fase 0) — blindar com os testes de webhook existentes antes de
  mexer; refactor incremental por tipo de evento.
- **Backfill de inbox** (Fase 1) precisa ser idempotente e preservar a inbox default ↔ canal `default`
  (não duplicar conversas existentes).
- **Echo/loop:** não chamar send dentro de handler de `message.sent`; manter o filtro `recently_sent`
  ([`webhook.py:533`](../server/routes/webhook.py#L533)) por `(channel_id, ...)`.
- **Idempotência inbound** por `(channel_id, external_msg_id)` (P18) — a Meta reentrega; usar o
  `external_msg_id` do `InboundEvent` para deduplicar no save.
- **Credenciais em texto puro** (P15) — manter mascaramento na borda; não logar `raw` com token.

---

## 7. Critério de pronto (do plano todo)

- Mensagem do **WhatsApp oficial (Cloud)** cria conversa na inbox do canal e o agente responde **pelo
  número oficial**; mensagem do **GOWA** segue idêntica, em conversa separada.
- **Nenhum** `if provider ==` no handler/pipeline; comportamento dirigido por `ChannelCapabilities`.
- Adicionar um 3º canal (ex.: Telegram) exige **só** um `Channel` novo (parse+send+capabilities).
- Mídia da Cloud baixa/cacheia e transcreve (Fase 5); fora da janela 24h oferece template (Fase 6).
- `tests/test_endpoints.py` verde + novos testes: ingest por-canal, roteamento de saída, inbox-por-canal.
