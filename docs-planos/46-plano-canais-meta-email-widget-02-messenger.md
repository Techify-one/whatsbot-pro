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
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _()_ • **Pendências:** _()_ • **Verificação:** _(fixtures de `entry[].messaging[]` + teste de send mockado)_

---

## Fase 02.2 — Janela 24h + tag HUMAN_AGENT + handover 🟢
**Objetivo:** respeitar a janela de 24h e permitir resposta humana até 7 dias.
**Itens:**
1. `[paralelo]` Usar `OutboundRouter.session_open(channel_id, last_inbound_ts)` (`channels/outbound.py:48`) — capabilities já tem `session_window_hours=24`. Fora da janela: se `human_agent_tag` on **e** a conversa está com humano (handoff, tag `transferido_atendente`) → `MESSAGE_TAG`+`HUMAN_AGENT`; senão marcar a mensagem `failed` com motivo claro. ⚠️ `HUMAN_AGENT` é tripwire de compliance — **só** p/ resposta humana, nunca p/ a IA (mestre §7).
2. `[adiado]` Handover Protocol (`pass_thread_control`/app `263902037430900`) — **não** necessário no modelo single-app (a IA/humano alternam internamente). Fica documentado; implementar só se um 2º app Meta dividir a Página.

**Pronto quando:** um send fora das 24h sem humano é marcado `failed` com mensagem explicativa; com humano designado + toggle on, sai com a tag.

#### Status de execução — Fase 02.2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _()_ • **Pendências:** _()_ • **Verificação:** _()_

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
- [ ] `parse_inbound` (fixtures texto/anexo/echo/receipt/reaction) verde.
- [ ] Send texto+mídia mockado; `appsecret_proof` presente; `messaging_type` correto.
- [ ] Assinatura inválida rejeitada (01-A).
- [ ] Janela 24h: fora → `failed` ou `HUMAN_AGENT` conforme handoff.
- [ ] Modo escuro na config screen.
- [ ] Dedup por `page_id` (dois canais mesma Página → 409).
- [ ] Suíte `tests/` verde no Postgres.
