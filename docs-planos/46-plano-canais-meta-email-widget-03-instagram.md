# Plano 46 · Sub-plano 03 — Canal Instagram Direct (login via Facebook)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** médio · **Mestre:** [00-mestre](46-plano-canais-meta-email-widget-00-mestre.md) · **Depende de:** 01-A, 01-B, 01-C
> **Método:** engenharia reversa do Chatwoot (`Channel::Instagram`, `Instagram::SendOnInstagramService`, `Webhooks::InstagramController`, `Instagram::RefreshOauthTokenService`) + docs Meta (verificado, julho/2026). **D1:** caminho **Instagram API with Instagram Login** (`graph.instagram.com`), sem Página do Facebook.
> **Como usar:** preencha o "Status de execução" de cada fase antes da próxima.

> 🔄 **Revisão 2026-07-24 — pivot para login via Facebook (decisão do usuário: "deixe como é no Chatwoot").** O plugin deixou de usar o caminho **Instagram Login** (`graph.instagram.com` + IG User token de 60 dias com refresh) e passou a usar **Instagram messaging via login do Facebook** — o caminho LEGACY do Chatwoot `Channel::FacebookPage` (+ `instagram_id`): uma **Página do Facebook** conectada à conta profissional do Instagram, tudo em **`graph.facebook.com`** com o **Page Access Token** (não expira por tempo). Consequências desta revisão, todas já aplicadas no código: **(a)** credenciais viram `page_id`/`page_access_token`/`app_secret`/`verify_token`/`app_id`(opcional, auto-detectado pelo Page token via `GET /app`) — não há mais `access_token`(IG)/`ig_id`; **(b)** **dedup por `page_id`** (Chatwoot: `Channel::FacebookPage` é unique em `page_id`; o `instagram_id` é atributo secundário só de roteamento inbound, que o core faz pelo `channel_id` na URL) — some o `account_identity()`/descoberta de `ig_id` via `/me`; **(c)** o send **reusa o fluxo do Messenger** (`/me/messages` com `messaging_type=RESPONSE`) — o override `_message_envelope` que dropava `messaging_type` foi **removido** (era só do `graph.instagram.com`); **(d)** o webhook é `object=instagram` registrado em `graph.facebook.com/{app_id}/subscriptions` + assinatura da **Página** em `graph.facebook.com/{page_id}/subscribed_apps` (autoconfigure igual ao Messenger, `app_id` auto-detectado — a criação do canal já aponta o webhook pra instância, sem passo manual); **(e)** **removido** todo o refresh de token de 60 dias — `lifecycle.py` deletado, capability `token_refresh=False`, permission `runtime.task` fora do manifest; um erro 190 (token revogado) ainda marca o canal para reautorização (reconectar a Página), mas não há renovação automática. `plugin.yaml` bump 1.0.0 → **2.0.0**. As seções abaixo escritas para o modelo antigo ficam por registro histórico; o que vale é esta revisão.

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
**Estado:** ✅ Concluída (2026-07-23)
- **O que foi feito:** plugin `instagram` criado em `assets/plugin_examples/instagram/` (import-only, zip em `assets/channel_plugins/instagram-plugin.zip`), 100% sobre o ponto de extensão de canal — ZERO mudança no core além de uma linha curada em `web/static/js/services/contactTypes.js` (rótulo/cor de brand do tipo `instagram`; o tipo já seria descoberto do descriptor sem isso). Carrega a PRÓPRIA cópia de `meta_graph.py` + `media_urls.py` (irmãs importadas por `from .meta_graph`), idênticas às do `facebook_messenger`. `channels.py` = subclasse de `MetaGraphChannel` com `graph_host="graph.instagram.com"`, `token_credential_key="access_token"`, `contact_type="instagram"`, descriptor (color `pink`, creds `access_token`/`app_secret`/`verify_token`/`ig_id`-opcional, config `graph_api_version`/`human_agent_tag`), `status()` via `/me?fields=user_id,username`, `identity_from_credentials`+`account_identity` (ig_id, descoberto do `/me` quando em branco e persistido de volta), a janela de 24h + fallback HUMAN_AGENT herdada, e `verify_inbound_signature` herdada. `routes.py` = `/info` + `/channels` + `POST /autoconfigure` (registra callback no app + assina a conta) + `POST /subscribe` + `POST /set-webhook` + `GET /webhook-status`. `static/` = tela `config:true` (copia a URL de callback, aviso do pré-requisito "Permitir acesso a mensagens", botão "Registrar webhook na Meta" = autoconfigure) + `extends.js`/`WebhookHealthRow.js` (linha de saúde no slot `channel.card.rows` comparando o callback configurado com a URL desta instância + botão "Configurar webhook").
- **Decisões:** (1) ⚠️ corpo de send SEM `messaging_type` — `_message_envelope` sobrescrito para `{recipient}{message}` puro (copiar o `RESPONSE` do Messenger, ou o `messaging_product` do Cloud, dá 400); o HUMAN_AGENT re-adiciona `messaging_type=MESSAGE_TAG` explicitamente. (2) **Autoconfigure igual ao Messenger** (decisão do usuário — ao salvar as credenciais o webhook já aponta pra instância): `post_create.kind="autoconfigure"` chama `/api/plugins/instagram/autoconfigure`, que registra o Callback URL no APP (`POST graph.facebook.com/{app_id}/subscriptions` com `object=instagram` e o app token `{app_id}|{app_secret}`) **e** assina a CONTA (`POST graph.instagram.com/{ig_id}/subscribed_apps`, Bearer IG). ⚠️ duas hosts: app-level em graph.facebook.com (o app é Meta), conta em graph.instagram.com. `app_id` é credencial **obrigatória** (`required_credentials`) — o token IG não permite auto-detectar o app_id (diferente do Messenger, que detecta pelo page token). Fallback "cole a URL à mão" só quando a instância não tem HTTPS público. `WebhookHealthRow` + `/set-webhook` (botão "Configurar webhook" no card) repontam o callback com 1 clique. (3) `ig_id` opcional: dedup no create quando presente, senão resolvido pós-conexão pelo sweep genérico via `account_identity()` (descoberto do `/me` e persistido).
- **Pendências:** _(nenhuma no MVP; comentários/story/OAuth = 03.3)_
- **Verificação:** `tests/test_instagram.py` (21 checagens) verde: descriptor/capabilities/identidade, corpo de send sem `messaging_type`, mídia por URL pública, janela 24h + HUMAN_AGENT (IA nunca usa a tag), erro 190 → reautorização, descoberta de `ig_id` via `/me`, refresh do token (renova perto de expirar, pula se longe/novo demais/expirado), e a costura de assinatura 01-A ponta-a-ponta no webhook do core (bad/valid signature + handshake do verify_token). Fixtures `messaging[]` IG (texto/anexo/echo-invertido/read/reaction) já cobertas pela base compartilhada em `tests/test_meta_graph_core.py`.

---

## Fase 03.2 — Refresh de token 60d (lifecycle) 🟢 [depende de: 01-C]
**Objetivo:** renovar o IG User token antes dos 60 dias — sem isso o canal morre em silêncio.
**Itens:**
1. `[paralelo]` `lifecycle.py`: `setup(ctx)` registra `ctx.spawn_task("token_refresh", loop)` (molde `telegram/lifecycle.py:181`). O loop varre os canais `instagram` a cada ~6–12h e chama `inst.refresh_token_if_needed()`.
2. `[paralelo]` `refresh_token_if_needed()`: renova só quando token válido, `updated_at ≥ 24h` e `expires_at < now+10d` (regra do Chatwoot `refresh_oauth_token_service.rb:34-46`); `GET /refresh_access_token?grant_type=ig_refresh_token&access_token=…` → grava `access_token`+`expires_at` via `channel_credential_repo.set`. Falha → mantém o token velho + `last_error`.
3. `[paralelo]` Erro `190` em send/inbound → marcar canal `reauthorization_required` (molde `last_error`/`logged_in=0` do sweep de identidade) + ação "Reautorizar" no card.

**Pronto quando:** um canal com token perto de expirar é renovado pelo loop (ganha novo `expires_at`); erro 190 vira estado de reautorização visível; disable cancela o loop.

#### Status de execução — Fase 03.2
**Estado:** ❌ REVERTIDA (2026-07-24) — o pivot para login via Facebook (ver "Revisão" no topo) usa um **Page Access Token que não expira por tempo**, então TODO o refresh de 60 dias foi removido: `lifecycle.py` deletado, `refresh_token_if_needed()` e a capability `token_refresh` removidos, `runtime.task` fora do manifest. Um erro 190 ainda vira estado de reautorização (reconectar a Página). O texto abaixo descreve o modelo antigo (Instagram Login) e fica só por registro histórico.
<br>~~**Estado:** ✅ Concluída (2026-07-23)~~
- **O que foi feito:** `lifecycle.py` registra `ctx.spawn_task("token_refresh", loop)` (molde `telegram/lifecycle.py`), varrendo os canais `instagram` a cada 6h e chamando `inst.refresh_token_if_needed()`; `entry.lifecycle: lifecycle` + `permissions: runtime.task` no manifest. `refresh_token_if_needed()` renova só token válido, `token_updated_at ≥ 24h` e `expires_at < now+10d` (`GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token`), grava `access_token`+`expires_at`+`token_updated_at` via `registry.set_credential` e limpa `last_error`; expiração desconhecida ⇒ renova 1× pra aprender a data. Erro 190/OAuth em send ou refresh ⇒ `_maybe_flag_reauth` (`last_error` + `logged_in=0`, não-destrutivo — mantém `enabled=1`), estado que o card exibe.
- **Decisões:** cadência 6h (janela de refresh é de dias, barato); loop supervisionado cancelado no disable via `stop_owner` (o supervisor derruba a task); token expirado NÃO é trocado (surfaces como reautorização em vez de retry storm).
- **Pendências:** ação explícita "Reautorizar" no card (o `last_error` já aparece; um botão dedicado é polimento futuro).
- **Verificação:** `tests/test_instagram.py` cobre renova-perto-de-expirar (ganha `expires_at` ~60d à frente + chama `/refresh_access_token`), pula-se-longe (40d), pula-se-novo-demais (<24h), e expirado→reautorização. Cancelamento do loop no disable é garantido pelo supervisor (mesmo mecanismo já testado no telegram).

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
