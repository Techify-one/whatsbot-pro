# Plano 46 — Novos canais: Instagram, Messenger, E-mail e Widget de site (mestre)

> **Status:** 🟨 PARCIAL — Messenger, Instagram e Widget concluídos; **falta só o canal E-mail** (§5, §11) · **Data:** 2026-07-09 · **Escopo:** grande (4 canais + habilitadores no core)
> **Origem:** pedido do usuário ("colocar Instagram e Messenger", depois "email também (uso Gmail)", depois "widget de chat de site como o Chatwoot"). **Método:** engenharia reversa do Chatwoot (clone local lido `arquivo:linha`), pesquisa das APIs atuais da Meta/Google com verificação adversarial (21/24 afirmações confirmadas; 3 corrigidas — ver D-corr), e mapeamento do core do WhatsBot (`arquivo:linha` verificado por sub-agente Explore).
> Os 4 canais entram como **plugins de canal** no ponto de extensão já existente (subclasse de `channels.base.Channel`), no mesmo molde de `whatsapp_cloud` e `telegram`. O core muda pouco e **sem `if provider ==`**: só ganha 1 costura de segurança (validação de assinatura Meta) + 1 infra nova (entrega via WebSocket p/ o widget). Instagram e Messenger são quase idênticos ao `whatsapp_cloud`; E-mail espelha o long-poll do `telegram`; o Widget é o **inverso** de todos (entrada = navegador POSTa num endpoint público; saída = empurra pro navegador via WS).
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. Este mestre indexa 5 sub-planos; execute pelas Waves (§6).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ | Instagram usa **"Instagram API with Instagram Login"** (host `graph.instagram.com`, escopos `instagram_business_*`), **não** o caminho Facebook-Login/Página. | Sub-plano 03. Sem dependência de Página do Facebook; conta profissional de IG basta. É o caminho que a Meta recomenda e p/ onde o Chatwoot migrou. |
| **D2** ✅ | Messenger e Instagram compartilham uma **base `meta_graph`** (mesmo corpo `{recipient}{message}`, mesma janela 24h, mesma assinatura). Diferem só em host, token, escopos e namespace de id (PSID×IGSID). | Sub-planos 02+03 herdam a base criada no 01. Dois **providers separados**, uma base comum. |
| **D3** ✅ | Validação **`X-Hub-Signature-256`** entra no **POST do webhook do core** como costura **opt-in por provider** (hook/capability), com `app_secret` por canal. **Sem `if provider ==`**. | Sub-plano 01, Fase A. Fecha buraco de segurança (hoje o POST não valida assinatura nenhuma — `server/routes/channel_webhook.py:291`). GOWA/telegram não são afetados. |
| **D4** ✅ | Mídia de saída p/ Meta/IG vai por **URL pública** (reusa `public_base_url` + `statics/`), não upload. | Sub-plano 01 expõe helper de URL pública; 02/03 usam. IG/Messenger buscam a mídia pela URL — precisa ser publicamente alcançável. |
| **D5** ✅ | E-mail: modo padrão = **IMAP poll + SMTP** num loop de lifecycle (molde `telegram/lifecycle.py`), auth por **App Password primeiro**, XOAUTH2/OAuth como upgrade opt-in. Inbound-parse (webhook) é 2º modo opcional. | Sub-plano 04. Evita a avaliação **CASA** do Google (não embarcar um app Google único com escopo restrito p/ todos os clientes). |
| **D6** ✅ | Threading de e-mail: reusa `messages.msg_id` (Message-ID) + `messages.reply_to_msg_id` (In-Reply-To); adiciona estado por-conversa (subject, References) + cursor de poll por-canal. | Sub-plano 04. Casa incoming In-Reply-To/References contra Message-IDs salvos p/ rotear ao thread. |
| **D7** ✅ | Widget = infra nova: **rota pública isenta de auth** (inbound) + **WebSocket por-visitante** (não o `/ws` do operador) + **SDK estático** em `/plugins/<id>/static` + identidade por **session token + HMAC** + gating por **allowed-domains**. `send_text` **entrega via WS** (inverte o contrato). | Sub-plano 05. É o único canal cuja saída é dona pelo WhatsBot, não uma API externa. |
| **D8** ✅ | IG precisa de **loop de refresh de token de 60 dias** (hook de lifecycle, gated por capability). Messenger (token de Página long-lived / System User) e e-mail-senha **não** precisam. | Sub-plano 01 cria o hook genérico; 03 o usa. Token IG não-renovado em 60d **morre em silêncio**. |
| **D9** ✅ | Identidade de contato generaliza além de telefone: `chat_id` = PSID/IGSID/e-mail/session-token do visitante. O core já indexa contato por `(channel_id, phone)` (`agent/handler.py:241`) — o id opaco entra no lugar do "phone". | Todos os sub-planos. Sem coluna nova de contato; o valor opaco vira a chave. |
| **D10** ✅ | Os 4 canais são **plugins importáveis** em `assets/plugin_examples/<id>/` + zip em `assets/channel_plugins/`, **não auto-instalados** (só `gowa` é — `plugins/bootstrap.py BUNDLED_AUTO_INSTALL`). | Todos. Core segue provider-agnóstico. |
| **D11** ✅ | Segredos (app_secret, tokens, senhas IMAP, hmac_token) vão em **`channel_credentials`** (repo já existente), **nunca** em `config` plaintext. Criptografia-at-rest de verdade fica p/ um plano de hardening à parte (flag, não bloqueia). | Todos. Chatwoot criptografa (`encrypts`); WhatsBot hoje não — ver Riscos. |

### Correções da verificação adversarial (fatos de API pinados)

| # | Afirmação inicial | Correção verificada (usar esta) |
|---|-------------------|----------------------------------|
| **Dc1** | Graph version v21.0 (Messenger) | **v25.0** é a atual (lançada 2026-02-18). Manter **configurável** (`graph_api_version`, default recente ~v23.0–v25.0), como o `whatsapp_cloud` já faz. |
| **Dc2** | Instagram Basic Display API "sunset em 04/09/2025" | Foi **04/12/2024** (anúncio em 04/09/2024 + 90 dias). Irrelevante p/ nós (não usamos Basic Display), mas não citar a data errada no guia. |
| **Dc3** | Escopos do IG-via-Facebook-Login | `instagram_basic` + `instagram_manage_messages` + `pages_manage_metadata` + `pages_show_list` + `business_management`. **Não** usamos esse caminho (D1), mas o guia menciona a diferença. |

---

## 1. Resumo executivo

O WhatsBot já tem um **sistema de plugins de canal** maduro (plano 02/11/32/33): um provider é uma subclasse de `channels.base.Channel` que declara `provider_descriptor()` (dirige o formulário do frontend), `parse_inbound()` (webhook→`InboundEvent`), `send_text`/`send_media`, `capabilities` e a identidade de dedup — e o core faz o resto **sem conhecer o provider por nome**. Adicionar um canal = shipar um plugin; o core não muda.

- **Instagram + Messenger** encaixam quase 1:1 no precedente `whatsapp_cloud` (Meta Graph API, webhook `path`). O core **já faz** o handshake `GET hub.challenge` genérico (`server/routes/channel_webhook.py:274`). Falta: uma base `meta_graph`, os dois providers, e **1 costura no core** (validar `X-Hub-Signature-256` no POST — hoje inexistente).
- **E-mail** espelha o long-poll do `telegram` (`ctx.spawn_task` + `ctx.ingest_event`) mas com IMAP/MIME/threading; ou, opcionalmente, inbound-parse via a rota webhook do core. Sem janela de 24h, sem templates — mais simples que Meta, exceto o threading e o OAuth do Gmail.
- **Widget de site** é o único que **inverte** o contrato: inbound é um endpoint público que o navegador chama; outbound tem que **empurrar pro navegador via WebSocket**. ~90% reusa o que existe (descriptor, identidade, pipeline de inbound, IA por-canal, handoff humano); ~10% é infra nova (WS por-visitante + SDK estático + rotas públicas + identidade de sessão/HMAC).

A forma da solução: **Wave 0** cria os habilitadores no core (assinatura Meta, URL pública de mídia, hook de refresh de token, infra de WS público do widget) + a base `meta_graph`. **Waves 1–2** shipam os 4 plugins, paralelizáveis.

---

## 2. Como funciona hoje (mapa do core — verificado)

| Costura | Onde | Observação |
|---------|------|------------|
| Contrato de canal | `channels/base.py:79` (`Channel`), `:18` (`ChannelCapabilities`), `:50` (`AccountIdentity`), `:139` (`provider_descriptor`), `:316` (`parse_inbound`) | Todos os providers herdam. `required_credentials`/`session_window_hours`/`inbound_route` em `ChannelCapabilities`. |
| Webhook por-provider (core) | `server/routes/channel_webhook.py:274` (GET handshake), `:291` (POST inbound), `:343-347` (`parse_inbound`→`_dispatch_events`) | GET valida `verify_token` (`:283`). **POST NÃO valida assinatura** — buraco. `_dispatch_events` já trata message/reaction/receipt/echo. |
| Funil de inbound | `app/services/message_ingest_service.py:332` (`ingest_event`), `:191` (`_resolve_inbound_media`→`download_media`), `:376` (echo por `direction="out"`) | Contato via `agent_handler._get_contact(phone, channel_id)` (`agent/handler.py:241`, chave `(channel_id, phone)`). Mídia baixada por `inst.download_media`. |
| Registro de provider | `plugins/loader.py:285` (`CHANNEL_PROVIDERS`→`channel_providers`), `channels/registry.py:29` (`register_provider` lê `cls.provider`), `server/app.py:152` (registro por plugin), `:161` (materialização no boot) | Plugin exporta `CHANNEL_PROVIDERS=[Cls]`; `Cls.provider` amarra a string. |
| Loop de background (plugin) | `plugins/context.py:316` (`ctx.spawn_task`), `runtime/supervisor.py:148` (`stop_owner`), `plugins/lifecycle.py:63` (`setup(ctx)`) | `telegram/lifecycle.py:192` é o precedente do poll. Cancelado no disable via `stop_owner(plugin_id)`. |
| Roteador de saída | `channels/outbound.py:30` (`OutboundRouter`), `:80` (`send_text`), `:48` (`session_open` — janela 24h) | Reply agêntico e envio do operador passam por aqui, com a chave `(channel_id, phone)` da conversa. |
| Descriptor→frontend | `app/services/channel_service.py:277` (`provider_descriptor`), `:454` (create), `web/static/js/components/channels/DescriptorFields.js` (tipos `text/secret/token_suggest/multiselect/generated/bool`), `notices.js` (`webhook_url`/`autoconfigure`) | Formulário 100% dirigido pelo descriptor. |
| Realtime + estático + auth-exempt | `server/state.py:68` (`ws_manager.broadcast`), `server/routes/websocket.py:16` (`/ws`), `plugins/context.py:146` (`broadcast`), `server/app.py:682-690` (mount de `/plugins/<id>/static` + router `/api/plugins/<id>`), `server/app.py:447` (`_AUTH_EXEMPT_PREFIXES`) | `/plugins/` (estático) **já é isento** de auth; `/api/plugins/<id>/…` **não é**. `/ws` é do operador. |
| Dedup de identidade | `channels/dedup.py`, `app/services/channel_service.py:58` (`credential_identity`), `:76` (`_guard_duplicate`→409) | `identity_from_credentials`/`account_identity` do provider; core compara. |

⚠️ **Gotchas que tornam algo obrigatório:**
- O POST do webhook (`channel_webhook.py:291`) faz `request.json()` e nunca valida `X-Hub-Signature-256` → qualquer um pode forjar inbound Meta. **Tem que** validar sobre o **body cru** (`await request.body()` ANTES do parse) — re-serializar o JSON quebra o HMAC.
- `/api/plugins/<id>/…` **não** é auth-exempt → o endpoint público do widget precisa ir sob `/api/webhook/` (já isento) **ou** ganhar exceção nova em `_AUTH_EXEMPT_PREFIXES` (`server/app.py:447`).
- Mídia de saída p/ Meta vai por **URL pública** — WhatsBot serve `statics/` e captura `public_base_url` (`GET /api/config`), então dá pra montar o link; mas GOWA passa **path local** — os providers Meta precisam do link, não do path.
- Token IG de 60 dias **morre em silêncio** se não renovado → precisa de loop de refresh (D8).

---

## 3. Inventário — o que cada canal exige

| Canal | Molde | Transporte inbound | Identidade (`chat_id`) | Janela | Token / auth | Novo no core? | Risco | Esforço |
|-------|-------|--------------------|------------------------|--------|--------------|---------------|-------|---------|
| **Messenger** | `whatsapp_cloud` | webhook `path` (`entry[].messaging[]`) | PSID (page-scoped) | 24h + tag `HUMAN_AGENT` (7d) | Page access token (long-lived/System User) | assinatura (D3) | médio | M |
| **Instagram** | `whatsapp_cloud` | webhook `path` (`entry[].messaging[]` + `changes[]` p/ comentários) | IGSID | 24h + `HUMAN_AGENT` (7d) | IG User token 60d **+ refresh** (D8) | assinatura (D3) + loop refresh | médio-alto | M |
| **E-mail** | `telegram` (poll) | IMAP poll (default) **ou** inbound-parse (`path`) | e-mail (lowercased) | nenhuma | App Password (default) **ou** XOAUTH2/OAuth | nenhum (usa `spawn_task`); tabela threads/cursor | médio | L |
| **Widget** | novo (inverso) | rota pública POST (navegador) | session token do visitante | nenhuma | website_token (público) + session token + HMAC opcional | **WS por-visitante + rota pública + SDK estático** | alto | L |

### Falsos positivos descartados

| "Parece problema" | Por que NÃO é |
|-------------------|---------------|
| "Precisa mudar o funil `ingest_event` p/ aceitar id não-telefone" | Não. `agent/handler.py:241` já chaveia por `(channel_id, phone)` — o valor é opaco; PSID/IGSID/e-mail/session-token entram como "phone". D9. |
| "Precisa `if provider ==` no core p/ os canais Meta" | Não. `parse_inbound`/`send_*`/descriptor são polimórficos; `_dispatch_events` (`channel_webhook.py:97`) já trata message/reaction/receipt genericamente. |
| "O widget precisa reusar o `/ws` do operador" | **Perigoso** e desnecessário — o `/ws` é do painel (vaza eventos de todas as conversas). Widget usa WS próprio por-visitante (D7). |
| "E-mail precisa de webhook igual aos outros" | Não — IMAP é **poll** (`spawn_task`), o inverso do push. Inbound-parse é opcional. |
| "Gmail exige a API do Gmail + CASA" | Não p/ o MVP — App Password + IMAP/SMTP evita OAuth e CASA por completo (D5). |
| "Templates/HSM p/ IG/Messenger" | Não existem — a única alavanca fora das 24h é a tag `HUMAN_AGENT` (humano, 7d). `capabilities.templates=False`. |

---

## 4. Habilitadores no core (Wave 0) — resumo (detalhe no sub-plano 01)

| Habilitador | Onde | Por quê |
|-------------|------|---------|
| Costura de assinatura `X-Hub-Signature-256` | `server/routes/channel_webhook.py:291` (POST) + hook no `Channel` | Segurança dos webhooks Meta (D3). Opt-in por provider; lê `app_secret` de `channel_credential_repo`. |
| Base `MetaGraphChannel` | novo `channels/providers/…` **ou** dentro dos plugins | Fatorar host/token/id/escopos; IG e Messenger herdam (D2). |
| Helper de **URL pública de mídia** | reusa `public_base_url` + `statics/senditems` | Enviar mídia por link p/ Meta (D4). |
| Hook de **refresh de token** (lifecycle) | molde `server/background.py` (sweep) / `spawn_task` | IG 60d (D8); gated por capability. |
| Infra de **WS público por-visitante** + rota pública isenta | `_AUTH_EXEMPT_PREFIXES` (`server/app.py:447`) + registry de conexões no plugin | Widget (D7). |
| Migrations (Postgres) | `db/alembic/versions/` (core) e/ou `plugin_<id>_*` (plugin) | threads/cursor de e-mail; sessões do widget. |

---

## 5. Índice dos sub-planos

| Sub-plano | Arquivo | Cobre | Estado |
|-----------|---------|-------|--------|
| **01 — Core & base Meta** | ~~`…-01-core-prep.md`~~ (apagado) | Assinatura Meta no core, base `meta_graph`, URL pública de mídia, hook de refresh, infra de WS público | ✅ **concluído** (2026-07-22). ⚠️ A base `meta_graph`/`media_urls` foi depois **movida do core para dentro dos plugins** pelo plano 76·F9 (`37c8f19`) — cada canal Meta carrega a própria cópia. O hook `token_refresh` do 01-C ficou **sem consumidor** (o Instagram reverteu o refresh de 60 dias) |
| **02 — Messenger** | ~~`…-02-messenger.md`~~ (apagado) | Provider `facebook_messenger` | ✅ **concluído** (2026-07-22). Falta só validação em campo com app/Página reais. Fase 02.3 (embedded signup) adiada por decisão |
| **03 — Instagram** | ~~`…-03-instagram.md`~~ (apagado) | Provider `instagram` — **login via Facebook** (pivot de 2026-07-24), não Instagram Login | ✅ **concluído** (v2.2.0 em `assets/`, `storages/` e zip). Fase 03.3 (comentários/story/OAuth) adiada. Pendência menor: ação "Reautorizar" no card do canal |
| **04 — E-mail** | [`…-04-email.md`](46-plano-canais-meta-email-widget-04-email.md) | Provider `email` (IMAP poll + SMTP; OAuth opt-in; inbound-parse opcional) | ⬜ **o único pendente** — zero linhas escritas. Independente (toca só `spawn_task`) |
| **05 — Widget** | ~~`…-05-widget.md`~~ (apagado) | Provider `website` (SDK + WS + identidade) | ✅ **concluído** |
| **Guia de operador** | [`46-guia-…`](46-guia-configuracao-provedores-meta-gmail-widget.md) | Passo-a-passo Meta (app, webhook, review, verificação), Gmail/OAuth, embed do widget | — (documento à parte, não-plano; **fica**) |

> Os sub-planos concluídos foram apagados em 2026-07-30 depois de auditados contra o código. O texto integral
> continua recuperável no git: `git show 0e84e09 -- docs-planos/…-01-core-prep.md` (idem 02), `f096f73` (03).

---

## 6. Waves, dependências e paralelização

```
WAVE 0 (habilitadores — barreira p/ Meta e Widget)
  ┌───────────────────────────────────────────────────────────────┐
  │ 01-A  Assinatura X-Hub-Signature-256 no core   🔴 (bloqueia 02,03) │
  │ 01-B  Base MetaGraphChannel + URL pública mídia 🟢                 │
  │ 01-C  Hook de refresh de token (lifecycle)      🟢  (usado por 03) │
  │ 01-D  Infra WS público por-visitante + rota exempt 🟢 (bloqueia 05)│
  └───────────────────────────────────────────────────────────────┘
        │ barreira: 01-A + 01-B prontos                    │ 01-D pronto
        ▼                                                  ▼
WAVE 1  02 Messenger 🟢   03 Instagram 🟢 [usa 01-C]        05 Widget 🟢
        (02 e 03 em paralelo — herdam a base do 01-B)      (paralelo aos Meta)
                                                    ▲
WAVE 1' 04 E-mail 🟢  ─── independente das outras (só usa spawn_task; pode ir já na Wave 0/1)
```

**Tabela de fases**

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / Sub-plano |
|------|------|-----------|-------|-------|----------------------------|
| 0 | 01-A | Assinatura Meta no core | 🔴 | médio | Sub-plano 01 §A — POST rejeita HMAC inválido, GOWA/telegram intactos, testes verdes. **[bloqueia 02, 03]** |
| 0 | 01-B | Base `meta_graph` + URL pública de mídia | 🟢 | baixo | 01 §B — base importável, unit test do parser `messaging[]`. |
| 0 | 01-C | Hook de refresh de token | 🟢 | baixo | 01 §C — loop registrável por capability, no-op p/ quem não declara. |
| 0 | 01-D | Infra WS público + rota isenta | 🟢 | alto | 01 §D — WS por-token conecta, `broadcast` por-sessão não vaza p/ outras sessões. **[bloqueia 05]** |
| 1 | 02 | Messenger | 🟢 | médio | Sub-plano 02 — inbound/outbound end-to-end no modo dev da Meta. **[depende de: 01-A, 01-B]** |
| 1 | 03 | Instagram | 🟢 | médio | Sub-plano 03 — idem + refresh 60d agendado. **[depende de: 01-A, 01-B, 01-C]** |
| 1 | 05 | Widget | 🟢 | alto | Sub-plano 05 — mensagem do navegador vira conversa; resposta volta ao navegador ao vivo. **[depende de: 01-D]** |
| 0/1 | 04 | E-mail | 🟢 | médio | Sub-plano 04 — IMAP poll cria conversa; reply threada. **[independente]** |

**O que pode ser paralelizado:** dentro da Wave 0, `01-B/01-C/01-D` são independentes entre si (só `01-A` é bloqueante e deve ir sozinha, pois mexe no core). Na Wave 1, **Messenger, Instagram, Widget e E-mail** são 4 workstreams paralelos (canais não se tocam; cada um é um plugin isolado). E-mail nem depende da Wave 0 — pode começar imediatamente.

**Disciplina do repo a seguir:** verde a cada fase; caracterização ANTES de mexer no POST do webhook do core (01-A toca fluxo crítico compartilhado); um refactor por commit; nunca avançar com teste vermelho não-explicado.

---

## 7. Riscos e cuidados (transversais)

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Assinatura sobre body cru | `request.json()` re-normaliza; HMAC sobre o dict parseado **não bate** | Ler `await request.body()` ANTES de qualquer parse; `hmac.compare_digest` sobre o hex após `sha256=` (01-A). |
| Regressão no webhook compartilhado | 01-A toca a rota que GOWA/telegram usam | Costura **opt-in** (só valida se o provider declara `app_secret`/capability); caracterização + testes de GOWA verdes antes/depois. |
| Echo / loop | is_echo (Meta) e o próprio Sent (e-mail) re-entram e a IA responde a si mesma | Reusar `direction="out"`/`source="echo"` + dedup por `msg_id` (já existe, `message_ingest_service.py:381`); e-mail filtra `From==caixa` + header próprio. |
| URLs de mídia Meta expiram | Guardar a CDN URL como `media_path` quebra depois | Baixar+cachear já no `parse_inbound`/resolver (molde `whatsapp_cloud.download_media:605`). |
| Morte silenciosa de token | IG 60d / OAuth de e-mail expiram sem erro até um send falhar | Loop de refresh (D8) + marcar `last_error`/reauth no card (molde sweep de identidade). |
| **Widget: `website_token` é público** | Ele vive no HTML de todo site — não pode autorizar nada sensível | Auth real = session token por-visitante + (opcional) HMAC + allowed-domains; **nunca** expor o `/ws` do operador ao visitante (D7). |
| Segredos em claro | `config` é plaintext; Chatwoot criptografa tokens | Usar `channel_credentials` (D11); flag p/ plano de hardening de criptografia-at-rest (não bloqueia). |
| Postgres (único backend) | Tabelas novas (threads/cursor de e-mail, sessões do widget) | Migrations Alembic; se dono-plugin, prefixo `plugin_<id>_` obrigatório (migrator recusa o contrário). |
| Modo escuro | Telas novas (config de e-mail/OAuth, config do widget, o próprio widget) | Classes `wa-*`/`.wa-field`; testar com `.dark` ligado (regra do CLAUDE.md). |
| Restart de plugin | Loops de e-mail/refresh/WS têm que morrer no disable | `ctx.spawn_task` (owner=plugin) → `stop_owner` no teardown (já é o padrão). |
| Pré-requisitos de operador (Meta/Google) | App Review + Business Verification (Meta) e verificação/CASA ou App Password (Google) têm **lead time de dias a semanas** e podem bloquear go-live | Guia de operador dedicado; no modo dev a Meta só fala com contas com papel no app — testar assim primeiro. |

---

## 8. Perguntas em aberto

- **P1 — WS do widget: reusar `/ws` filtrado ou WS dedicado?** ✅ DECIDIDO (2026-07-09): **WS dedicado por-visitante** dono do plugin (`/api/plugins/website/ws?session=…`, auth-exempt, registry `{session→sockets}`). (a) reusar `/ws` com salas → risco de vazar eventos do operador; (b) WS dedicado → isolado, é o modelo `pubsub_token` do Chatwoot. Recomendo (b).
- **P2 — E-mail: inbound padrão IMAP-poll ou inbound-parse?** ✅ DECIDIDO: **IMAP-poll default** (não precisa de host público nem MTA), inbound-parse como 2º modo (p/ quem roda Postal/Mailu/SES/Mailgun). Melhor p/ self-host atrás de NAT.
- **P3 — Criptografia-at-rest de segredos agora?** ⏸️ ADIADO: usar `channel_credentials` já resolve "não vazar na URL/logs"; criptografia real vira plano de hardening. Flag no Risco.
- **P4 — Onboarding Meta: embedded signup ou colar token?** ✅ DECIDIDO: **colar token (MVP)** como o `whatsapp_cloud` faz hoje; Facebook Login for Business / Instagram Business Login (embedded signup, `post_create.kind='autoconfigure'`+`form_component`) fica como fase 2 de cada sub-plano.
- **P5 — Credenciais do app Meta: por-canal ou env global?** ✅ DECIDIDO: **por-canal** (`app_id`/`app_secret`/`verify_token`/`access_token` como `credential_fields`), com fallback opcional a env global. Mantém o modelo por-canal e evita 1 app compartilhado forçar CASA/escopo amplo.
- **P6 — Comentários/story do IG no MVP?** ⏸️ ADIADO: MVP cobre **DM** (`entry[].messaging[]`). Comentários (`entry[].changes[]`) e story-reply entram como fase 2 do sub-plano 03.

---

## 9. Apêndice — arquivos-chave por camada

**Core (Wave 0):**
- `server/routes/channel_webhook.py:291` — POST inbound (adicionar validação de assinatura).
- `server/app.py:447` — `_AUTH_EXEMPT_PREFIXES` (rota pública do widget).
- `channels/base.py` — hook `verify_signature`/capability de assinatura; capability de refresh.
- `channels/outbound.py` — (sem mudança estrutural; conferir `session_open` p/ tag HUMAN_AGENT).
- `app/services/message_ingest_service.py:191` — resolver de mídia (URL Meta / MIME e-mail).
- `server/background.py` — molde do loop de refresh de token.
- `db/alembic/versions/` — migrations (se tabelas de core).

**Plugins novos (`assets/plugin_examples/<id>/` + zip em `assets/channel_plugins/`):**
- `facebook_messenger/` — `channels.py`, `routes.py` (subscribe/oauth), `plugin.yaml`, `static/`.
- `instagram/` — `channels.py`, `lifecycle.py` (refresh), `routes.py` (oauth/subscribe), `plugin.yaml`, `static/`.
- `email/` — `channels.py`, `lifecycle.py` (IMAP poll + OAuth refresh), `routes.py` (OAuth callback/inbound-parse), `settings.py`, `migrations/`, `plugin.yaml`, `static/`.
- `website/` — `channels.py`, `routes.py` (rota pública + WS), `static/sdk.js` + app do iframe, `migrations/` (sessões), `plugin.yaml`.

**Referências (ler antes de implementar):**
- `assets/plugin_examples/whatsapp_cloud/channels.py` — molde Graph API.
- `assets/plugin_examples/telegram/lifecycle.py` — molde poll-loop.
- `assets/plugin_examples/telegram/channels.py` — molde descriptor + autoconfigure.

---

## 10. Checklist de verificação (todo o esforço)

- [ ] `venv/bin/python -m pytest tests/ -q` verde no **Postgres** (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome).
- [ ] Caracterização do POST do webhook do core (GOWA/telegram) verde ANTES e DEPOIS de 01-A.
- [ ] Cada provider: `parse_inbound` unit-testado com payload real (fixtures); `send_text`/`send_media` mockados.
- [ ] `node --test` nos módulos JS puros de channels (se tocar `web/static/js/components/channels/`).
- [ ] Modo escuro legível nas telas novas (config de e-mail/OAuth, config do widget, widget embarcado).
- [ ] Migration round-trip (`alembic upgrade head` + downgrade) das tabelas novas; prefixo `plugin_<id>_` se dono-plugin.
- [ ] Restart de plugin cancela loops (IMAP/refresh/WS) via `stop_owner`.
- [ ] Nenhum segredo em URL/log; tokens/senhas só em `channel_credentials`.
- [ ] `X-Hub-Signature-256`: POST forjado (assinatura errada) é rejeitado; assinatura válida passa.
- [ ] Widget: visitante NÃO recebe eventos de outras conversas pelo WS; `allowed_domains` bloqueia origem não-listada.

---

## 11. Status de execução — Mestre

**Estado:** 🟨 **Parcial — 3 dos 4 canais no ar; falta só o E-mail** (auditado contra o código em 2026-07-30)
- **O que foi feito:** Wave 0 (sub-plano 01: as 4 costuras do core) concluída em 2026-07-22; Wave 1 Messenger (02) em
  2026-07-22 e Instagram (03) em 2026-07-23, com pivot para login via Facebook em 2026-07-24; Widget (05) concluído.
  Ver a coluna "Estado" do §5 — os sub-planos concluídos foram apagados, o texto está no git.
- **Como foi feito / decisões:** dois desvios relevantes ao que este mestre especifica. **(1)** D2/P-01B1 previa a base
  `meta_graph` como módulo **compartilhado do core**; o plano 76·F9 a moveu para **dentro de cada plugin** (`37c8f19`),
  em nome de zips autossuficientes — hoje há duas cópias (`facebook_messenger` e `instagram`), e um fix na API da Meta
  precisa ser aplicado nas duas. **(2)** D1 previa Instagram por "Instagram API with Instagram Login"
  (`graph.instagram.com` + token de 60 dias com refresh); o usuário decidiu em 2026-07-24 seguir o caminho do Chatwoot
  (Página do Facebook + Page Access Token em `graph.facebook.com`), o que **eliminou** o refresh de token e deixou o
  hook `ChannelCapabilities.token_refresh` do 01-C no core **sem nenhum consumidor**.
- **Problemas / pendências:** o sub-plano **04 (E-mail) não tem uma linha escrita** — é todo o trabalho restante deste
  plano. Pendências menores herdadas: validação em campo do Messenger (app/Página reais), ação "Reautorizar" no card do
  canal Instagram, e decidir o destino do hook `token_refresh` órfão (manter documentado como seam ou remover).
  As fases opcionais 02.3 (embedded signup) e 03.3 (comentários/story/OAuth) seguem **adiadas por decisão**, não por falha.
- **Verificação:** `tests/test_facebook_messenger.py` + `tests/test_meta_graph_core.py` (38 testes) e
  `tests/test_instagram.py` (28 testes) verdes; paridade `assets/` ↔ `storages/` ↔ zip conferida por `diff -r` no Instagram.
