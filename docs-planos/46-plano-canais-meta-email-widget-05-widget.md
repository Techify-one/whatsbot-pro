# Plano 46 · Sub-plano 05 — Canal Widget de site (live-chat embarcável)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** grande · **Mestre:** [00-mestre](46-plano-canais-meta-email-widget-00-mestre.md) · **Depende de:** 01-D (infra WS público)
> **Método:** engenharia reversa do Chatwoot (`Channel::WebWidget`, SDK `app/javascript/sdk`, iframe `app/javascript/widget`, `RoomChannel`, `ActionCableListener`, `Widget::TokenService`, HMAC em `contacts_controller.rb`) + design consenso (Intercom/Crisp/tawk.to). **D7:** WS por-visitante dedicado, rota pública isenta, SDK estático, identidade session+HMAC, allowed-domains.
> **Como usar:** preencha o "Status de execução" de cada fase antes da próxima.

## Objetivo
Plugin `website`: uma caixinha de chat embarcável que o cliente cola em qualquer site (o "Site" do Chatwoot). É o **inverso** dos outros canais: inbound = endpoint público que o navegador POSTa; outbound = `send_text` **empurra pro navegador via WebSocket** (não chama API externa). ~90% reusa (descriptor, identidade, pipeline de inbound, IA por-canal, handoff humano); ~10% é a infra nova da Wave 0 (01-D).

## Modelo de tokens (do Chatwoot — replicar a semântica, não o código)
| Token | Análogo Chatwoot | Papel | Segredo? |
|-------|------------------|-------|----------|
| `widget_token` | `website_token` (`SecureRandom.base58(24)`) | id público do widget; seleciona o canal/inbox; **vive no HTML de todo site** | **NÃO** (público) |
| `hmac_token` | `hmac_token` | segredo p/ validar identidade (`setUser`) — só no backend do cliente | **SIM** |
| `session_token` | JWT `{source_id, inbox_id}` (`X-Auth-Token`) + `pubsub_token` | id opaco por-visitante; autoriza o REST + o WS daquele visitante | por-sessão |
| cookie de retorno | `cw_conversation` | reconhece visitante recorrente (mesma conversa) | — |

⚠️ **A regra de ouro:** `widget_token` é público (será copiado p/ outros sites) → **não autoriza nada sensível**, só seleciona o canal. Auth de verdade = `session_token` por-visitante (+ HMAC opcional). E **nunca** conectar o visitante ao `/ws` do operador (vaza todas as conversas) — usar o WS dedicado do 01-D.

## Capabilities / descriptor
```
ChannelCapabilities(qr=False, templates=False, groups=False, presence=True,   # agente digitando → visitante
  reactions=False, media=True, inbound_route="path",   # rota pública própria
  session_window_hours=0,
  required_credentials=())   # sem credencial que o operador digita
```
- `provider="website"`, `label="Site (Widget)"`, `color="teal"`.
- `credential_fields`: `widget_token`(**`generated`**, read-only, auto-mintado no create — é o que o operador copia), `hmac_token`(secret, auto-gerado, "revelar"). `config_fields`: `allowed_domains`(text/multiselect), `hmac_mandatory`(bool), `welcome_title`/`welcome_tagline`(text), `widget_color`(text), `pre_chat_form`(bool + campos), `enable_file_upload`(bool).
- `identity_from_credentials(creds)` → `AccountIdentity("widget_token", creds["widget_token"])` (dois canais não compartilham token).
- `post_create`: **novo kind `"embed_snippet"`** (análogo a `webhook_url`) — o `notices.js` renderiza o `<script>` de instalação com `widget_token` + `public_base_url`. (Adicionar o render desse kind é a única mudança no frontend do core — provider-agnóstica.)

## Tabelas novas (migrations `plugin_website_*`)
| Tabela | Colunas | Uso |
|--------|---------|-----|
| `plugin_website_sessions` | `session_token`, `channel_id`, `chat_id`, `identifier`(nullable, se HMAC), `hmac_verified`, `created_at`, `last_seen` | sessão do visitante (chave de contato + roteamento do WS) |

`chat_id` = `session_token` (ou `identifier` quando HMAC-verificado, p/ persistir histórico entre dispositivos). É o análogo do "phone" (D9).

---

## Fase 05.1 — SDK estático + iframe 🟢 [depende de: 01-D]
**Objetivo:** o `<script>` de embed carrega o widget num iframe na origem do WhatsBot.
**Itens:**
1. `[paralelo]` `static/sdk.js` (loader host-page): injeta o botão/bolha + um `<iframe src="{baseUrl}/plugins/website/static/widget.html?widget_token=…">`; ponte host↔iframe via `postMessage` com prefixo namespaced (validar `event.origin`). Servido de `/plugins/<id>/static/` (já público/isento — `server/app.py:682`).
2. `[paralelo]` `static/widget.html` + app do iframe (Preact/HTM — o importmap do core cobre `preact`/`htm`; sem build): UI do chat, conecta no REST público (05.2) + WS (05.3). Cores `wa-*`/`.wa-field` (modo escuro).
3. `[paralelo]` Snippet de embed no `post_create` (`embed_snippet`):
   ```html
   <script>(function(d,t){var g=d.createElement(t);g.src="{public_base_url}/plugins/website/static/sdk.js";
   g.async=true;d.body.appendChild(g);g.onload=function(){window.WhatsBotChat.run({widgetToken:'<token>',baseUrl:'{public_base_url}'})}})(document,'script')</script>
   ```
4. `[paralelo]` CSP: servir `widget.html` com `frame-ancestors` derivado de `allowed_domains` (senão só a lista permitida embeda). O `sdk.js` precisa ser alcançável cross-origin (headers CORS na rota estática/pública).

**Pronto quando:** colar o snippet num HTML de teste renderiza a bolha; clicar abre o iframe do WhatsBot; um domínio fora de `allowed_domains` é bloqueado por CSP.

#### Status de execução — Fase 05.1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _(iframe vs inline; onde vive o CSP por-canal)_ • **Pendências:** _()_ • **Verificação:** _(embed em página de teste; allowed_domains bloqueia)_

---

## Fase 05.2 — Rota pública de inbound (navegador → servidor) 🟢
**Objetivo:** o navegador cria sessão e posta mensagem, autorizado por token (não por login de operador).
**Itens:**
1. `[sequencial]` `GET config` (bootstrap): valida `widget_token` → valida `Origin` contra `allowed_domains` → minta/resume `session_token` (`plugin_website_sessions`) → devolve config do widget + `session_token` (cookie de retorno). Rate-limit por IP (molde Chatwoot: 5 sessões novas/h/IP).
2. `[sequencial]` `POST messages` (header `X-Session-Token`): valida sessão (+ `identifier_hash` se `hmac_mandatory`) → monta o dict de inbound → `parse_inbound`→`ingest_event` (reusa TODO o pipeline: filtros, `message.saved`, IA). `chat_id`=`session_token`. Rate-limit (molde: 6 conversas/12h/IP).
3. `[sequencial]` `POST identify` (setUser/HMAC): valida `identifier_hash = HMAC_SHA256(hmac_token, identifier)` (`hmac.compare_digest`); marca `hmac_verified`; re-chaveia contato p/ `identifier` (histórico entre dispositivos). `POST typing`/`read`/`upload` (MIME+tamanho gated).
4. `[sequencial]` Onde montar: sob `/api/webhook/website/{channel_id}` (isento, core) OU prefixo isento novo (01-D). Decidido em 01-D/P-01D1.

**Pronto quando:** o widget de teste cria uma sessão, posta "oi", e a conversa aparece no painel (com a IA respondendo se o canal tem `ai_enabled`); origem não-listada é recusada; `hmac_mandatory` recusa sem hash válido.

#### Status de execução — Fase 05.2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _()_ • **Pendências:** _()_ • **Verificação:** _(curl de config+messages; rate-limit; HMAC)_

---

## Fase 05.3 — Saída via WebSocket (servidor → navegador) 🟢 [o "coração"]
**Objetivo:** a resposta (IA ou humano) chega no navegador ao vivo — `send_text` **entrega**, não chama API.
**Itens:**
1. `[sequencial]` O visitante conecta no WS dedicado (01-D): `/api/plugins/website/ws?session=<token>` → validado (origem + session) → registrado em `{session_token → set[WebSocket]}`.
2. `[sequencial]` `send_text(chat_id, text)`/`send_media`: **não** faz HTTP externo. Persistência já ocorre no pipeline; o provider **empurra** pro navegador via a ponte do 01-D (`deliver(session_token, {role:"assistant", content, msg_id, ts})` ou `broadcast` por-sessão). Retorna `SendResult(ok=True, external_msg_id=<uuid sintético>)`.
3. `[sequencial]` Presença inversa: agente digitando → evento `webchat.typing` p/ o visitante (o oposto de ler presença do contato). `read`/unread análogos.
4. `[sequencial]` Fila offline: se não há socket (aba fechada), a mensagem já está persistida; ao reconectar (`GET config`/reabrir), o widget re-sincroniza as últimas N (molde `syncLatestMessages`). Reconexão com backoff+jitter; ordenar/dedup por `msg_id`/seq.

**Pronto quando:** com o widget aberto, a resposta da IA aparece no navegador em <1s sem reload; dois visitantes distintos **não** veem a mensagem um do outro; fechar/reabrir a aba re-carrega o histórico; o handoff humano (mensagem do operador) chega pelo mesmo caminho.

#### Status de execução — Fase 05.3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _(deliver via registry vs broadcast por-sessão)_ • **Pendências:** _()_ • **Verificação:** _(isolamento entre 2 sessões; offline+reconnect)_

---

## Fase 05.4 — Pré-chat form, upload, identidade e reuso do que existe 🟢
**Objetivo:** amarrar o widget ao resto do WhatsBot sem lógica nova.
**Itens:**
1. `[paralelo]` Pré-chat form (nome/email) → cria/nomeia o contato antes da conversa; upload de arquivo (MIME allowlist + tamanho) → `statics/senditems`.
2. `[paralelo]` Reuso automático: **IA por-canal** (plano 21 — o inbox do widget tem seu `ai_enabled`/agente), **handoff humano** (plano 29 — tag `transferido_atendente`/`assignee_user_id` já silencia a IA e roteia p/ humano; a resposta humana sai pelo mesmo `send_text`→WS **sem lógica nova**), **dedup de identidade** (plano 32), **`public_base_url`** (baseUrl), **`statics/senditems`** (mídia).
3. `[paralelo]` `plugin.yaml` (`entry: channels, routes`; `migrations/`; screen `config:true` p/ personalização) + RBAC opcional.

**Pronto quando:** pré-chat captura nome/email; upload chega no painel; ligar/desligar a IA do canal e transferir p/ humano funcionam no widget sem código específico.

#### Status de execução — Fase 05.4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _()_ • **Pendências:** _()_ • **Verificação:** _()_

---

## Riscos específicos
| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| `widget_token` público | Copiado p/ outros sites | Só seleciona o canal; auth real = session token + HMAC + allowed_domains. |
| Reusar `/ws` do operador | Vazar todas as conversas ao visitante | WS **dedicado** por-sessão (01-D); jamais o `/ws`. |
| `Origin`/Referer forjável | Não é auth de verdade | Usar allowed_domains como gate de abuso/CORS; auth = session token + HMAC. |
| `postMessage` inseguro | `targetOrigin:'*'` vaza; não validar origin injeta comando | Sempre origin exato; iframe sandbox **sem** `allow-same-origin`. |
| Reconnect storm | Loop apertado derruba o servidor | Backoff+jitter; re-sync no reconnect. |
| Ordem/duplicidade | WS não garante ordem sob reconexão | `msg_id`/seq server-side; dedup no cliente. |
| `send_text` não tem handle p/ o navegador | Contrato `Channel` dobra aqui | Threadar registry/`deliver` do 01-D na instância; documentar a exceção. |
| Abuso | Endpoints públicos são ímã de spam | Rate-limit por IP/sessão; MIME/tamanho no upload; CAPTCHA/pré-chat opcional. |

## Perguntas em aberto
- **P-05.1:** iframe (isolamento CSS/segurança) confirmado como padrão (todos os vendors usam). ✅
- **P-05.2:** `send_text` alcança o registry via ponte injetada (01-D) — decidir registry-no-plugin vs broadcast-por-sessão em 01-D. ⏸️
- **P-05.3:** persistência de sessão anônima entre reloads = cookie no domínio do iframe (molde `cw_conversation`). ✅

## Checklist
- [ ] Embed em página de teste renderiza + conecta; `allowed_domains` bloqueia origem não-listada (CSP).
- [ ] `GET config`/`POST messages` autorizados por token (não por login); rate-limit ativo.
- [ ] Resposta da IA/operador chega ao navegador ao vivo; 2 sessões isoladas.
- [ ] HMAC `setUser` verificado; `hmac_mandatory` recusa sem hash.
- [ ] Offline+reconnect re-sincroniza; sem duplicata.
- [ ] Handoff humano e IA-por-canal funcionam sem lógica nova.
- [ ] Migrations `plugin_website_*` round-trip.
- [ ] Modo escuro no widget e na config screen.
- [ ] Restart de plugin fecha os WS; suíte `tests/` verde no Postgres.
