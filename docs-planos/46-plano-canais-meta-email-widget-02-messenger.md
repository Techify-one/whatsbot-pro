# Plano 46 · Sub-plano 02 — Canal Facebook Messenger

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** médio · **Mestre:** [00-mestre](46-plano-canais-meta-email-widget-00-mestre.md) · **Depende de:** 01-A, 01-B
> **Método:** engenharia reversa do Chatwoot (`Channel::FacebookPage`, `Facebook::SendOnFacebookService`, `Integrations::Facebook::MessageParser`) + docs oficiais da Meta (verificado, julho/2026, Graph **v25.0**).
> **Como usar:** preencha o "Status de execução" de cada fase antes da próxima.

## Objetivo
Plugin `facebook_messenger`: caixa de entrada de Página do Facebook via Messenger Platform (Meta Graph API, webhook push), no molde do `whatsapp_cloud` + base `meta_graph` (01-B). MVP = colar token (P4); embedded signup vira fase 2.

## Fatos pinados (usar exatamente)
| Item | Valor |
|------|-------|
| Webhook object | `page`; roteia por `entry[].id` (PAGE_ID) → canal |
| Inbound | `POST /api/webhook/facebook_messenger/{channel_id}`; corpo `entry[].messaging[]` (`sender.id`=PSID, `recipient.id`=PAGE_ID, `message.mid`, `message.text`, `message.attachments[].payload.url`) |
| Campos p/ assinar | `messages, messaging_postbacks, message_deliveries, message_reads, message_echoes, messaging_handovers, standby` |
| Assinatura | `X-Hub-Signature-256: sha256=HMAC_SHA256(app_secret, raw_body)` (01-A) |
| Send | `POST https://graph.facebook.com/v25.0/me/messages?access_token=<PAGE_TOKEN>` (+ `appsecret_proof`), `messaging_type` **obrigatório** |
| Text body | `{"recipient":{"id":"<PSID>"},"messaging_type":"RESPONSE","message":{"text":"..."}}` |
| Mídia body | `{"recipient":{"id":"<PSID>"},"message":{"attachment":{"type":"image\|audio\|video\|file","payload":{"url":"<URL pública>","is_reusable":true}}}}` |
| Identidade contato | **(PAGE_ID, PSID)** — PSID é page-scoped; nunca só PSID |
| Perfil | `GET /v25.0/{PSID}?fields=first_name,last_name,profile_pic` (precisa da feature "Business Asset User Profile Access"; devolve `{}` se negado) |
| Janela | 24h (RESPONSE/UPDATE); fora dela só `messaging_type=MESSAGE_TAG` + `tag=HUMAN_AGENT` (humano, 7d) |
| ⚠️ Tags mortas | `CONFIRMED_EVENT_UPDATE/ACCOUNT_UPDATE/POST_PURCHASE_UPDATE` → erro 100 desde **2026-04-27**. Só `HUMAN_AGENT` sobrevive |
| Token | Page access token derivado de user token long-lived = **não expira** por tempo; System User token p/ SaaS |
| Permissões | `pages_messaging, pages_manage_metadata, pages_show_list, pages_read_engagement, business_management` (Advanced Access p/ páginas de terceiros) |
| appsecret_proof | `HMAC_SHA256(access_token, app_secret)` hex, em toda chamada Graph |

## Capabilities / descriptor
```
ChannelCapabilities(qr=False, templates=False, groups=False, presence=False,
  reactions=True, media=True, inbound_route="path", session_window_hours=24,
  required_credentials=("page_id","page_access_token","app_secret","verify_token"))
```
- `provider="facebook_messenger"`, `label="Facebook"`, `color="blue"`.
- `credential_fields`: `page_id`(text), `page_access_token`(secret), `app_secret`(secret), `verify_token`(token_suggest). `config_fields`: `graph_api_version`(text, default `v25.0`), `human_agent_tag`(bool, "responder fora das 24h como agente humano").
- `identity_from_credentials(creds)` → `AccountIdentity("page_id", creds["page_id"])` (conhecido no create — precedente `whatsapp_cloud.phone_number_id`).
- `post_create`: `{kind:"webhook_url", path:"/api/webhook/facebook_messenger/{channel_id}"}` (mostra a URL de callback p/ colar no App Dashboard — molde `whatsapp_cloud`).

---

## Fase 02.1 — Provider MVP (colar token) 🟢 [depende de: 01-A, 01-B]
**Objetivo:** inbound+outbound de texto/mídia funcionando com token colado, no modo dev da Meta (contas com papel no app).

**Itens:**
1. `[paralelo]` `channels.py`: subclasse de `MetaGraphChannel` (01-B) com `host="graph.facebook.com"`, `provider="facebook_messenger"`, capabilities acima, descriptor acima.
2. `[paralelo]` `send_text`/`send_media`: montar corpo Messenger (tabela acima), `access_token` como query + `appsecret_proof` (HMAC do token com `app_secret`). `messaging_type=RESPONSE`; se fora das 24h e `human_agent_tag` on → `MESSAGE_TAG`+`tag=HUMAN_AGENT`. Capturar `message_id` da resposta como `external_msg_id`.
3. `[paralelo]` `parse_inbound`: caminha `entry[].messaging[]` (herdado de 01-B); mapear `message`(texto/anexo), `postback`, `delivery`/`read`(receipt), `message.is_echo`→`direction=out` (`source=echo`), `reaction`. Anexos: baixar `payload.url` já no resolver (`download_media`). ⚠️ dedup por `mid` (echo corre com o send — molde do delay de 2s do Chatwoot; aqui o dedup por `msg_id` do `message_ingest_service.py:381` já cobre).
4. `[paralelo]` `verify_inbound_signature` (01-A) com `app_secret`.
5. `[paralelo]` `status()`: `GET /v25.0/{page_id}?fields=name&access_token=…` (ping) — molde `whatsapp_cloud.status:167`.
6. `[paralelo]` `routes.py`: `POST /subscribe` (chama `POST /v25.0/{page_id}/subscribed_apps?subscribed_fields=…&access_token=…`) e `GET /webhook-status` (molde `whatsapp_cloud/routes.py`). Screen `config:true` com botão "Assinar webhook".
7. `[paralelo]` Enriquecimento de contato: `GET /v25.0/{PSID}?fields=first_name,last_name,profile_pic` → `sender_name`/avatar; tratar `{}` (feature negada) → fallback ao PSID.
8. `[paralelo]` `plugin.yaml` (`entry: channels, routes`; `permissions: channel.provider, net.outbound`) + `static/facebook_messenger.js` (config screen, cores `wa-*`).

**Pronto quando:** com um canal criado (token colado) e o webhook assinado, uma DM enviada à Página (por uma conta testadora) vira conversa no painel; a resposta da IA/operador chega no Messenger; um anexo de imagem entra e sai; assinatura inválida é rejeitada (01-A).

#### Status de execução — Fase 02.1
**Estado:** ✅ Concluída (2026-07-22)
- **O que foi feito:** plugin [assets/plugin_examples/facebook_messenger/](../assets/plugin_examples/facebook_messenger/) — `channels.py` (`FacebookMessengerChannel(MetaGraphChannel)`: capabilities, descriptor, `identity_from_credentials`→`page_id`, `contact_type`→`facebook`, `status()` pingando o nó da Página, `send_text`/`send_media`), `routes.py` (`/info`, `/channels`, `POST /subscribe` → `POST /{page_id}/subscribed_apps` com os 7 campos, `GET /webhook-status`), `static/facebook_messenger.js` (screen `config:true`: copiar a URL de callback, assinar o webhook, ver campos assinados e se há App Secret), `plugin.yaml`. Zip importável em `assets/channel_plugins/facebook_messenger-plugin.zip` (D10 — não auto-instalado). `page_id` entrou em `NON_SECRET_CRED_KEYS` (identificador público) e `facebook` no catálogo de tipos de contato do painel ([contactTypes.js](../web/static/js/services/contactTypes.js)).
- **Decisões:** `parse_inbound`/`verify_inbound_signature`/`download_media`/perfil vêm inteiros da base 01-B (o plugin não reimplementa nada). `appsecret_proof` em toda chamada Graph quando há `app_secret`. Mídia por URL pública (D4) — sem `public_base_url` o envio falha com mensagem acionável em vez de mandar link quebrado. `graph_api_version` e `human_agent_tag` são `config_fields` (não credenciais).
- **Pendências:** perfil do usuário depende da feature "Business Asset User Profile Access" (P-02.2) — sem ela o nome cai no PSID, como previsto no MVP.
- **Verificação:** [tests/test_facebook_messenger.py](../tests/test_facebook_messenger.py) — descriptor/capabilities/identidade, corpo da Send API + `appsecret_proof`, mídia por URL pública (`image`/`file`), e a rota real de webhook com assinatura válida/ inválida.

---

## Fase 02.2 — Janela 24h + tag HUMAN_AGENT + handover 🟢
**Objetivo:** respeitar a janela de 24h e permitir resposta humana até 7 dias.
**Itens:**
1. `[paralelo]` Usar `OutboundRouter.session_open(channel_id, last_inbound_ts)` (`channels/outbound.py:48`) — capabilities já tem `session_window_hours=24`. Fora da janela: se `human_agent_tag` on **e** a conversa está com humano (handoff, tag `transferido_atendente`) → `MESSAGE_TAG`+`HUMAN_AGENT`; senão marcar a mensagem `failed` com motivo claro. ⚠️ `HUMAN_AGENT` é tripwire de compliance — **só** p/ resposta humana, nunca p/ a IA (mestre §7).
2. `[adiado]` Handover Protocol (`pass_thread_control`/app `263902037430900`) — **não** necessário no modelo single-app (a IA/humano alternam internamente). Fica documentado; implementar só se um 2º app Meta dividir a Página.

**Pronto quando:** um send fora das 24h sem humano é marcado `failed` com mensagem explicativa; com humano designado + toggle on, sai com a tag.

#### Status de execução — Fase 02.2
**Estado:** ✅ Concluída (2026-07-22)
- **O que foi feito:** capability nova **`human_window_hours`** ([channels/base.py](../channels/base.py)) = janela ESTENDIDA válida só p/ envio HUMANO; `OutboundRouter.session_open(..., by_human=False)` ([channels/outbound.py](../channels/outbound.py)) a considera, e `_session_window_block` ([server/routes/contacts.py](../server/routes/contacts.py)) passa `by_human=True` (todo call site dele é ação de operador). O Messenger declara `session_window_hours=24` + `human_window_hours=168`. No provider, `_post_with_window_fallback` reenvia UMA vez como `messaging_type=MESSAGE_TAG` + `tag=HUMAN_AGENT` **só** se o toggle `human_agent_tag` estiver ligado E a conversa estiver com humano (tag `transferido_atendente`); senão devolve `failed` com texto explicativo.
- **Decisões:** a decisão da tag é REATIVA (dispara no erro de janela da própria Meta), não pré-calculada — provider fica sem estado, sem clock skew e sem leitura extra de DB no caminho feliz. O gate de humano reusa o sinal de handoff que o core já mantém, então **a IA nunca alcança a tag** (falha fechada: qualquer erro na consulta ⇒ "não é humano" ⇒ sem tag).
- **Pendências:** 02.3 (embedded signup) segue adiada, como previsto; Handover Protocol permanece documentado e não implementado (single-app).
- **Verificação:** [tests/test_facebook_messenger.py](../tests/test_facebook_messenger.py) — fora da janela sem toggle ⇒ erro claro e SEM retry; com toggle mas conversa da IA ⇒ sem retry; com handoff humano ⇒ retry com `MESSAGE_TAG`/`HUMAN_AGENT`; erro não-de-janela nunca reenviado; `session_open` (2 dias: fecha p/ IA, abre p/ humano; 10 dias: fecha p/ ambos).

---

## Fase 02.3 — (opcional) Embedded signup (Facebook Login for Business) ⏸️
**Objetivo:** trocar "colar token" por conectar a Página via OAuth e listar `GET /me/accounts`.
**Itens:** `routes.py` com o fluxo FB Login for Business → user token long-lived → `GET /me/accounts` → page token (não-expira) → grava + assina. `post_create.kind="autoconfigure"` + `form_component`. Preferir **System User token** p/ produção multi-tenant. Adiado (P4).

#### Status de execução — Fase 02.3
**Estado:** ⬜ Não iniciada (adiada)

---

## Riscos específicos
| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| PSID page-scoped | Mesmo humano tem PSID diferente por Página | Chave de contato = `(channel_id, PSID)` — já é `(channel_id, phone)` no core (D9). |
| Echo loop | `is_echo` re-ingerido → IA responde a si mesma | `direction=out`+`source=echo`+dedup por `mid` (existe). |
| URL de mídia expira | CDN URL some | Baixar no `parse_inbound`/resolver, nunca guardar a URL. |
| Tag HUMAN_AGENT | Uso pela IA = perda de acesso | Gated pelo estado de handoff humano, nunca pela IA. |
| Tags mortas 2026-04-27 | Erro 100 | Só usar `HUMAN_AGENT`. |
| appsecret_proof | Se "Require App Secret" ligado, chamadas sem proof falham | Sempre enviar `appsecret_proof`. |

## Perguntas em aberto
- **P-02.1:** guardar `page_access_token` como não-expira (user-derived) ou instruir System User no guia? ✅ Guia recomenda System User p/ produção; MVP aceita qualquer page token.
- **P-02.2:** perfil do usuário exige a feature "Business Asset User Profile Access" (app review) — sem ela, `{}`. ⏸️ Aceitar fallback ao PSID no MVP.

## Checklist
- [x] `parse_inbound` (fixtures texto/anexo/echo/receipt/reaction) verde.
- [x] Send texto+mídia mockado; `appsecret_proof` presente; `messaging_type` correto.
- [x] Assinatura inválida rejeitada (01-A).
- [x] Janela 24h: fora → `failed` ou `HUMAN_AGENT` conforme handoff.
- [x] Modo escuro na config screen (classes `wa-*` + `.wa-field`).
- [x] Dedup por `page_id` (`identity_from_credentials` → o guard genérico do core devolve 409).
- [x] Suíte `tests/` verde no Postgres.
- [ ] **Validação em campo** (exige app Meta + Página reais): assinar o webhook, receber uma DM e responder.
