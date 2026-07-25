# Plano 46 · Sub-plano 03 — Canal Instagram Direct (Instagram Login)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** médio · **Mestre:** [00-mestre](46-plano-canais-meta-email-widget-00-mestre.md) · **Depende de:** 01-A, 01-B, 01-C
> **Método:** engenharia reversa do Chatwoot (`Channel::Instagram`, `Instagram::SendOnInstagramService`, `Webhooks::InstagramController`, `Instagram::RefreshOauthTokenService`) + docs Meta (verificado, julho/2026). **D1:** caminho **Instagram API with Instagram Login** (`graph.instagram.com`), sem Página do Facebook.
> **Como usar:** preencha o "Status de execução" de cada fase antes da próxima.

## Objetivo
Plugin `instagram`: DM do Instagram via "Instagram API with Instagram Login". Quase igual ao Messenger (base `meta_graph`) — difere em host (`graph.instagram.com`), token (IG User token 60d **com refresh**, 01-C), escopos e id (IGSID). MVP = colar token; OAuth (Instagram Business Login) vira fase 2.

> ⚠️ **A base `meta_graph`/`media_urls` NÃO está mais no core** (plano 76·F9, 2026-07-23 — decisão do usuário de zips autossuficientes). Ela vive em `assets/plugin_examples/facebook_messenger/{meta_graph,media_urls}.py`. **Este plugin carrega a PRÓPRIA cópia** desses dois arquivos (copiar do Messenger para `assets/plugin_examples/instagram/`), importados relativamente (`from .meta_graph import …`). Consequência: um fix na API da Meta precisa ser aplicado nas DUAS cópias. Se a duplicação incomodar no futuro, promover a base a um pacote compartilhado explícito — não é o caso agora.

## Fatos pinados (usar exatamente — confirmados na verificação)
| Item | Valor |
|------|-------|
| Webhook object | `instagram`; DM em `entry[].messaging[]` (`sender.id`=IGSID, `recipient.id`=IG_ID); comentários em `entry[].changes[]` (fase 2) |
| Inbound | `POST /api/webhook/instagram/{channel_id}` |
| Campos DM | `messages, message_reactions, message_echoes, messaging_postbacks, messaging_seen, messaging_referral, messaging_optins, messaging_handover, standby` |
| Assinatura | `X-Hub-Signature-256` (01-A) com `app_secret` |
| Send | `POST https://graph.instagram.com/v25.0/<IG_ID>/messages` (ou `/me/messages`), `Authorization: Bearer <IG_USER_TOKEN>` |
| Text body | `{"recipient":{"id":"<IGSID>"},"message":{"text":"..."}}` — **sem** `messaging_product`/`to`/`type` (isso é WhatsApp); texto UTF-8 ≤1000 bytes |
| Mídia body | `{"recipient":{"id":"<IGSID>"},"message":{"attachment":{"type":"image\|audio\|video\|file","payload":{"url":"<URL pública>"}}}}` (por URL ou `attachment_id`; imagens ≤8MB/10, áudio/vídeo/pdf ≤25MB) |
| Ações | typing/seen/react via `{"recipient":{"id":"<IGSID>"},"sender_action":"typing_on\|mark_seen\|react\|unreact"}` |
| Identidade contato | **IGSID** (app+conta-scoped, estável por par usuário↔conta) |
| Assinar por conta | `POST https://graph.instagram.com/v25.0/{IG_ID}/subscribed_apps?subscribed_fields=...` |
| Janela | 24h; fora só `messaging_type=MESSAGE_TAG`+`tag=HUMAN_AGENT` (humano, 7d). Sem HSM/template |
| Permissões | `instagram_business_basic`, `instagram_business_manage_messages` (+ `_manage_comments`, `_content_publish` p/ extras) — **novos** nomes (antigos `business_*` deprecados 27/01/2025) |
| Token | IG User token long-lived **60 dias**; refresh `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=…` (token ≥24h e não-expirado) → +60d. ⚠️ >60d sem refresh = **morto permanente** |
| Pré-req operador | ligar Instagram app → Configurações → Mensagens → Ferramentas conectadas → "Permitir acesso a mensagens" (senão send/subscribe falham em silêncio) |

## Capabilities / descriptor
```
ChannelCapabilities(qr=False, templates=False, groups=False, presence=True,   # sender_action typing/seen
  reactions=True, media=True, inbound_route="path", session_window_hours=24,
  token_refresh=True,   # 01-C
  required_credentials=("ig_id","access_token","app_secret","verify_token"))
```
- `provider="instagram"`, `label="Instagram"`, `color="pink"` (ou roxo).
- `credential_fields`: `ig_id`(text; auto-descobrível via `/me`), `access_token`(secret), `app_secret`(secret), `verify_token`(token_suggest). `config_fields`: `graph_api_version`(text, default `v25.0`), `human_agent_tag`(bool).
- `identity_from_credentials(creds)` → `AccountIdentity("ig_id", creds["ig_id"])` (conhecido no create). Chatwoot tem índice UNIQUE em `instagram_id`.

---

## Fase 03.1 — Provider MVP (colar token) 🟢 [depende de: 01-A, 01-B]
**Objetivo:** inbound+outbound de DM (texto/mídia) com token colado, no modo dev da Meta.
**Itens:**
1. `[paralelo]` `channels.py`: subclasse de `MetaGraphChannel` (01-B) com `host="graph.instagram.com"`, corpo de send **sem** `messaging_product` (⚠️ maior divergência do whatsapp_cloud — copiar o payload do Cloud dá 400).
2. `[paralelo]` `parse_inbound` (herda `entry[].messaging[]` de 01-B): `message`(texto/anexo), `is_echo`→`direction=out` (⚠️ sender/recipient **invertem** no echo), `is_deleted`→revoked/deleted, `read`(receipt via `messaging_seen`), `reaction`, `reply_to.mid`→`reply_to_msg_id`. Story-reply/mention e comentários → fase 03.3.
3. `[paralelo]` Anexos: baixar `payload.url` (ou `story_media_url`) no resolver.
4. `[paralelo]` `verify_inbound_signature` (01-A). `status()` = `GET /v25.0/me?fields=user_id,username` (Bearer).
5. `[paralelo]` `routes.py`: `POST /subscribe` (`{IG_ID}/subscribed_apps`), `config:true` screen com aviso do pré-requisito "Permitir acesso a mensagens".
6. `[paralelo]` Enriquecimento: `GET /v25.0/{IGSID}?fields=name,username,profile_pic` → `sender_name`/avatar. ⚠️ tratar erros: `230` (usuário nunca te mandou DM → não dá p/ ler perfil), `9010`/`100` (criar contato "Desconhecido (IG: …)") — comportamentos que o Chatwoot tolera p/ passar o bot de review.
7. `[paralelo]` `plugin.yaml` (`entry: channels, lifecycle, routes`) + `static/instagram.js`.

**Pronto quando:** DM à conta IG (por testador) vira conversa; resposta chega no Instagram; anexo entra/sai; `is_echo` não gera loop; assinatura inválida rejeitada.

#### Status de execução — Fase 03.1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _()_ • **Pendências:** _()_ • **Verificação:** _(fixtures `messaging[]` IG, inclusive is_echo com sender/recipient invertidos)_

---

## Fase 03.2 — Refresh de token 60d (lifecycle) 🟢 [depende de: 01-C]
**Objetivo:** renovar o IG User token antes dos 60 dias — sem isso o canal morre em silêncio.
**Itens:**
1. `[paralelo]` `lifecycle.py`: `setup(ctx)` registra `ctx.spawn_task("token_refresh", loop)` (molde `telegram/lifecycle.py:181`). O loop varre os canais `instagram` a cada ~6–12h e chama `inst.refresh_token_if_needed()`.
2. `[paralelo]` `refresh_token_if_needed()`: renova só quando token válido, `updated_at ≥ 24h` e `expires_at < now+10d` (regra do Chatwoot `refresh_oauth_token_service.rb:34-46`); `GET /refresh_access_token?grant_type=ig_refresh_token&access_token=…` → grava `access_token`+`expires_at` via `channel_credential_repo.set`. Falha → mantém o token velho + `last_error`.
3. `[paralelo]` Erro `190` em send/inbound → marcar canal `reauthorization_required` (molde `last_error`/`logged_in=0` do sweep de identidade) + ação "Reautorizar" no card.

**Pronto quando:** um canal com token perto de expirar é renovado pelo loop (ganha novo `expires_at`); erro 190 vira estado de reautorização visível; disable cancela o loop.

#### Status de execução — Fase 03.2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _()_ • **Pendências:** _()_ • **Verificação:** _(mock do refresh; enable/disable cancela)_

---

## Fase 03.3 — (opcional) Comentários, story, OAuth ⏸️
- **Comentários/menções** (`entry[].changes[].value`, `field:comments`) + **private reply** (`recipient={comment_id}`, 1 msg em 7d): `parse_inbound` ganha o ramo `changes[]`.
- **Story reply/mention**: tipos `ig_story`/`story_mention`, fetch do objeto de story.
- **OAuth Instagram Business Login** (trocar colar-token): `routes.py` com authorize `https://www.instagram.com/oauth/authorize?...scope=instagram_business_basic,instagram_business_manage_messages` → code → `POST api.instagram.com/oauth/access_token` (short 1h) → `GET graph.instagram.com/access_token?grant_type=ig_exchange_token` (long 60d) → descobre `IG_ID` via `/me`. `post_create.kind="autoconfigure"`+`form_component`. Adiado (P4/P6).

#### Status de execução — Fase 03.3
**Estado:** ⬜ Não iniciada (adiada)

---

## Riscos específicos
| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Corpo de send | Copiar payload do whatsapp_cloud (com `messaging_product`) → 400 | Corpo Messenger puro `{recipient}{message}`. |
| Morte de token 60d | Sem refresh, canal morre sem erro até um send falhar | Loop 03.2 + reautorização visível. |
| Echo invertido | sender/recipient trocam no `is_echo` → mensagem no lado errado | Tratar explicitamente (Chatwoot `base_message_text.rb:29-39`). |
| Pré-req "Permitir acesso a mensagens" | Send/subscribe falham em silêncio | Aviso na config screen + no guia. |
| Reactions subscritas mas não tratadas (Chatwoot) | — | WhatsBot pode ir além e mapear reaction→`message_reaction` (o `_dispatch_events` já trata). |
| IGSID scoped | Não correlaciona com telefone/WhatsApp | Chave `(channel_id, IGSID)` (D9). |

## Perguntas em aberto
- **P-03.1:** descobrir `IG_ID` automaticamente no create (via `/me`) ou pedir no form? ✅ Auto-descobrir quando possível; manter campo `ig_id` editável.
- **P-03.2:** cadência do loop de refresh. ✅ 6–12h basta (janela de refresh é de dias); barato.

## Checklist
- [ ] `parse_inbound` IG (fixtures texto/anexo/echo-invertido/read/reaction/unsend) verde.
- [ ] Send texto ≤1000 bytes + mídia por URL; `Bearer` correto; sem `messaging_product`.
- [ ] Refresh 60d testado (mock); enable/disable cancela loop; erro 190 → reautorização.
- [ ] Assinatura inválida rejeitada.
- [ ] Dedup por `ig_id`.
- [ ] Modo escuro na config screen.
- [ ] Suíte `tests/` verde no Postgres.
