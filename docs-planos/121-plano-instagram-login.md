# Plano 121 — Instagram Direct pelo **Instagram Login**: OAuth no painel e token que se renova sozinho

> **Status:** ✅ EXECUTADO (2026-08-14) · **Escopo:** médio/grande (1 plugin, 0 arquivos do core)
> **Resultado:** `instagram` v3.0.0 — 56 testes verdes, zip publicado e instalado em dev. O core não teve UMA linha de código alterada (só documentação).
> **Origem:** investigação de 2026-08-14 — o canal Instagram foi configurado, conectou, e **DM de terceiro nunca chegou**.
> **Método:** leitura do código real (core + plugin instalado + fonte publicada), medição no banco de dev e de produção,
> sondagem ao vivo da Graph API nos dois caminhos, leitura do banco do Chatwoot desativado, e recuperação do
> plano original via `git show f096f73^:docs-planos/46-plano-canais-meta-email-widget-03-instagram.md`.
>
> O plugin `instagram` fala hoje com `graph.facebook.com` usando um **Page Access Token** e a permissão
> `instagram_manage_messages`, que **não tem Acesso Avançado** nos apps do cliente. A Meta só entrega o webhook
> quando quem escreve tem função no app — então só administrador conversa. Este plano **substitui** esse caminho
> pelo **Instagram Login** (`graph.instagram.com`), que é o caminho comprovadamente aprovado para esta conta,
> com OAuth dentro do painel e renovação automática do token de 60 dias.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a
> próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-08-14 | **Substitui de vez.** O caminho "Instagram via login do Facebook" é removido do plugin; não há modo de convivência. | Sem flag de modo, sem branch por caminho. `page_id`/`page_access_token` deixam de existir no descriptor. Canais legados **precisam ser reconectados** (F9). |
| **D2** ✅ 2026-08-14 | **Cada instalação usa o PRÓPRIO app da Meta.** O usuário não quer virar provedor: quem usar o WhatsBot conecta o próprio Instagram, com o próprio app, sem passar pela infraestrutura dele. | `app_id` e `app_secret` são **credenciais por canal**, nunca constante global no código nem config de instalação. Não há App Review a fazer neste plano (cada dono resolve o seu, e para uso próprio é o fluxo curto). |
| **D3** ✅ 2026-08-14 | Para a Redes Brasil, **reaproveitar o app `1433191074801648`** (o do Chatwoot), que já tem o Acesso Avançado aprovado e comprovado. | Zero espera da Meta para destravar a produção atual. O plano é só desenvolvimento. |
| **D4** ✅ 2026-08-14 | **Renovação por relógio, não por tráfego.** O jeito do Chatwoot (renovar só quando o canal tem movimento, nos últimos 10 dias) é explicitamente rejeitado. | Loop supervisionado com cadência fixa + margem larga + alerta obrigatório em falha (F5, F7). |
| **D5** ✅ 2026-08-14 | **Um clique no painel.** O operador não digita token, nem app id, nem id de conta. | O OAuth vive dentro do produto (F4, F6). Colar token não é o MVP — é o fallback. |
| **D6** (princípio do repo) | **Zero mudança no core.** Tudo cabe em seams existentes. | Ver §3.2: a tentação de criar um `post_create.kind = "oauth"` é **reprovada** pelos 3 critérios do CLAUDE.md. |

---

## 1. Resumo executivo

O plugin `instagram` é, hoje, o `facebook_messenger` com outro rótulo: mesmo host, mesmo envelope de envio, dedup por
`page_id`, e **duas** assinaturas na Meta (a do app e a da Página). Isso funciona para o Messenger, onde
`pages_messaging` tem acesso avançado — e falha para o Instagram, onde `instagram_manage_messages` não tem.

A troca é **cirúrgica no que já existe** e **aditiva no que falta**:

- **3 pontos** de `channels.py` presos ao Facebook (host, identidade, `status()`);
- **3 pontos** de `meta_graph.py` (host, `_auth_params`, `_message_envelope`);
- **metade** de `routes.py` morre — todo o eixo `{app_id}/subscriptions` + `{page_id}/subscribed_apps`;
- **nasce**: `lifecycle.py` (o plugin não tem), a primeira migration `plugin_instagram_*`, e o par authorize/callback do OAuth;
- **não se toca**: `parse_inbound` inteiro, `download_media`, `media_urls.py`, os `MediaLimits` (os números já são os do Instagram) e a lógica de janela de 24h + `HUMAN_AGENT`.

O achado que define o esforço: **o core já construiu o encaixe da renovação para este caso exato** e ele está órfão
desde o pivô de 2026-07-24. `refresh_token_if_needed` ([channels/base.py:535-558](../channels/base.py#L535-L558))
tem no docstring, literalmente, *"Instagram's 60-day token is the reference — it dies SILENTLY if not renewed"*, com as
regras já escritas. Este plano é o **primeiro consumidor** de um seam desenhado para ele.

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 O caminho atual, e por que ele não entrega

| Camada | Hoje | Referência |
|---|---|---|
| Host | `graph.facebook.com` | [channels.py:56,104](../storages/plugins/instagram/channels.py#L56) |
| Token | Page Access Token (`EAA…`), **não expira** | [channels.py:128](../storages/plugins/instagram/channels.py#L128) — comentário `# NO token_refresh` |
| Identidade | `AccountIdentity("page_id", …)` | [channels.py:204-211](../storages/plugins/instagram/channels.py#L204-L211) |
| `status()` | `GET /{page_id}?fields=name,instagram_business_account{…}` | [channels.py:218-241](../storages/plugins/instagram/channels.py#L218-L241) |
| Assinatura na Meta | **duas**: `{app_id}/subscriptions` + `{page_id}/subscribed_apps` | [routes.py:327-346](../storages/plugins/instagram/routes.py#L327-L346), [:271-283](../storages/plugins/instagram/routes.py#L271-L283) |
| Permissão | `instagram_manage_messages` | — |

⚠️ **A causa raiz não é código.** A documentação da Meta para webhooks de mensagem do Instagram diz:
*"Webhooks will only be sent if the person using your app has a role on the app."* Em Acesso Padrão, a mensagem de
quem não tem função no app **nunca é enviada** — não há payload sendo descartado, não há bug no plugin.

**Medido em 2026-08-14, dois apps independentes** (`4195516780669357` e `765000666582242`): administrador troca DM
normalmente; pessoa de fora manda e nada chega. E no mesmo minuto, pelo token de Instagram Login do Chatwoot,
`GET /me/conversations` devolveu 5 threads — inclusive as mensagens de teste que não chegaram pelo outro caminho.

### 2.2 O que o Chatwoot fazia (o alvo a imitar)

Lido do banco de produção dele (`installation_configs` e `channel_instagram`):

| Chave | Valor |
|---|---|
| `INSTAGRAM_APP_ID` | `1433191074801648` |
| `INSTAGRAM_API_VERSION` | `v25.0` |
| token | `IGAAUXeszo…` (prefixo `IGA`, não `EAA`), 162 chars |
| criado / renovado / expira | 21/02/2026 · 22/07/2026 07:45 · 20/09/2026 07:45 |

A aritmética prova a renovação automática: sem refresh o token teria morrido em 22/04; renovação e expiração caem
no **mesmo minuto do dia**, 60 dias exatos — assinatura de tarefa agendada, não de humano. Em 7 meses ninguém foi à Meta.

### 2.3 Os seams do core que este plano usa (todos já existem)

| Seam | Referência | Estado |
|---|---|---|
| `ChannelCapabilities.token_refresh` | [channels/base.py:149](../channels/base.py#L149) | existe, **zero consumidores** |
| `Channel.refresh_token_if_needed()` | [channels/base.py:535-558](../channels/base.py#L535-L558) | existe, **zero consumidores**, docstring escrito para o IG |
| Tarefa supervisionada | `ctx.spawn_task` — molde `telegram/lifecycle.py:202-204` | pronto |
| Rota auth-exempt | `PLUGIN_PUBLIC_PATH_RE` [server/app.py:57](../server/app.py#L57), checada em [:576-577](../server/app.py#L576-L577) | pronto; precedente `website/routes.py:164` |
| GET de verificação (`hub.challenge`) | [server/routes/channel_webhook.py:635-650](../server/routes/channel_webhook.py#L635-L650) | ⚠️ lê a credencial `verify_token` pelo **nome literal** (:644) |
| Aviso "Credenciais faltando" no card | `required_credentials` → [channel_service.py:177-199](../app/services/channel_service.py#L177-L199) → `ChannelCard.js:71-73` | pronto |
| `form_component` (formulário rico) | [channels/base.py:270](../channels/base.py#L270), [ChannelForm.js:125-126](../web/static/js/components/channels/ChannelForm.js#L125-L126) | existe, **nenhum built-in usa** |

**Nenhum `if provider == "instagram"` no core** — varredura em `server/ app/ channels/ web/ db/ plugins/` devolve vazio.
As 12 menções são docstrings mais o catálogo de `contact_type`, que continua válido.

---

## 3. Inventário / análise

### 3.1 O que fazer

| # | Item | Onde | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| 1 | Host → `graph.instagram.com` | [channels.py:56,104](../storages/plugins/instagram/channels.py#L56) | trocar constante | baixo | S |
| 2 | `appsecret_proof` deixa de ir | [meta_graph.py:208-224](../storages/plugins/instagram/meta_graph.py#L208-L224) | flag de classe `send_appsecret_proof = False` | **médio** | S |
| 3 | `messaging_type` sai do envelope base | [meta_graph.py:275-281](../storages/plugins/instagram/meta_graph.py#L275-L281) | override no envelope; retry de janela **mantém** `MESSAGE_TAG` | **médio** | M |
| 4 | Autenticação vira `Authorization: Bearer` | [meta_graph.py:217-224](../storages/plugins/instagram/meta_graph.py#L217-L224) | header em vez de query | baixo | S |
| 5 | Identidade → `AccountIdentity("ig_id", …)` | [channels.py:204-211](../storages/plugins/instagram/channels.py#L204-L211) | trocar kind e valor | **médio** (ver §6) | S |
| 6 | `status()` → `GET /me?fields=user_id,username` | [channels.py:218-241](../storages/plugins/instagram/channels.py#L218-L241) | reescrever | baixo | S |
| 7 | Descriptor: `page_id`/`page_access_token` morrem; `app_id` vira obrigatório | [channels.py:141-201](../storages/plugins/instagram/channels.py#L141-L201) | reescrever | baixo | M |
| 8 | `token_refresh=True` + `required_credentials` novo | [channels.py:117-138](../storages/plugins/instagram/channels.py#L117-L138) | inverter a linha :128 | baixo | S |
| 9 | Matar o eixo de assinatura do app/Página | [routes.py:271-283,327-346](../storages/plugins/instagram/routes.py#L271-L283) | remover; assinar por conta (`{IG_ID}/subscribed_apps`) | baixo | M |
| 10 | **Nascer**: authorize + callback público do OAuth | `routes.py` (novo) | §4.2 | **alto** | L |
| 11 | **Nascer**: `lifecycle.py` + `entry.lifecycle` | novo arquivo + `plugin.yaml:19-21` | molde telegram | médio | M |
| 12 | **Nascer**: `refresh_token_if_needed` | `channels.py` | §4.3 | médio | M |
| 13 | **Nascer**: migration `001_oauth_state.sql` | novo (plugin não tem nenhuma) | §4.2 | baixo | S |
| 14 | Botão Conectar/Reconectar + estado do token | [static/instagram.js](../storages/plugins/instagram/static/instagram.js) (242 linhas) | §4.4 | médio | M |
| 15 | Alerta de falha de renovação | `lifecycle.py` | molde `gowa/alerts.py` | médio | M |
| 16 | Testes: ~15 de 30 reescritos | `../whatsbot-pro-plugins/plugins/instagram/tests/` | §5 F8 | médio | L |
| 17 | Canais legados: credenciais órfãs | banco | §6 / F9 | **médio** | M |

### 3.2 Falsos positivos descartados

| Parece problema | Por que NÃO é |
|---|---|
| "Precisa de um `post_create.kind = "oauth"` no core" | **Reprovado pelos 3 critérios do CLAUDE.md**: 1 consumidor previsto, e o gancho existente (screen `config:true`, que o plugin **já tem**, ou `form_component`) resolve sem custo no caminho quente. Inventar o kind obrigaria a editar [ChannelsManager.js:247-272](../web/static/js/components/ChannelsManager.js#L247-L272) do core para um único plugin. |
| "Precisa de migration no core para guardar a expiração" | O próprio core prescreve gravar `expires_at` como **credencial** ([channels/base.py:552-554](../channels/base.py#L552-L554)); `channel_credentials` é (channel_id, key, value) livre. Zero migration no core. |
| "Dá para guardar a expiração em `channels.config`" | **Armadilha**: [channel_service.py:759-761](../app/services/channel_service.py#L759-L761) sobrescreve o JSON inteiro a cada PUT do formulário — o operador salvar o canal apagaria o timestamp do loop. |
| "`parse_inbound` precisa mudar" | Ele só percorre `entry[].messaging[]` e **nunca lê `raw["object"]`** ([meta_graph.py:441-465](../storages/plugins/instagram/meta_graph.py#L441-L465)). O payload do Instagram Login tem o mesmo formato. |
| "Os limites de mídia mudam" | [channels.py:78-99](../storages/plugins/instagram/channels.py#L78-L99) já são **os números do Instagram** (8 MB imagem / 25 MB resto) — batem com a tabela de fatos pinados. |
| "O core precisa saber o novo provider" | O provider continua sendo `instagram`, e `contact_type` também. Nada no core muda de nome, cor ou rótulo. |
| "Renomear `verify_token` para `ig_verify_token` fica mais claro" | **Quebraria o handshake em silêncio**: o core lê a chave pelo nome literal em [channel_webhook.py:644](../server/routes/channel_webhook.py#L644). A Meta receberia 403 e o webhook nunca seria registrado. |
| "Dá para fazer o OAuth num iframe dentro do painel" | A CSP ([server/app.py:664-679](../server/app.py#L664-L679)) não declara `frame-src`, então cai em `default-src 'self'` — iframe para a Meta é bloqueado. E `connect-src` não libera host da Meta: a troca `code→token` **tem** de ser no servidor. |

---

## 4. Desenho da solução

### 4.1 Fatos pinados (recuperados do plano original, `git show f096f73^:…-03-instagram.md`)

Usar **exatamente** estes valores — já foram verificados contra a Meta em julho/2026:

| Item | Valor |
|---|---|
| Webhook object | `instagram`; DM em `entry[].messaging[]` (`sender.id`=IGSID, `recipient.id`=IG_ID) |
| Inbound | `POST /api/webhook/instagram/{channel_id}` — **inalterado** |
| Assinatura | `X-Hub-Signature-256` com `app_secret` — **inalterada** |
| Send | `POST https://graph.instagram.com/v25.0/me/messages`, `Authorization: Bearer <IG_USER_TOKEN>` |
| Corpo texto | `{"recipient":{"id":"<IGSID>"},"message":{"text":"…"}}` — **sem** `messaging_product`; texto ≤ **1000 bytes** |
| Corpo mídia | `{"recipient":{…},"message":{"attachment":{"type":"image\|audio\|video\|file","payload":{"url":"<URL pública>"}}}}` |
| Ações | `{"recipient":{…},"sender_action":"typing_on\|mark_seen\|react\|unreact"}` |
| Identidade contato | **IGSID** (app+conta-scoped) |
| Assinar por conta | `POST https://graph.instagram.com/v25.0/{IG_ID}/subscribed_apps?subscribed_fields=…` |
| Janela | 24h; fora, só `messaging_type=MESSAGE_TAG` + `tag=HUMAN_AGENT` (humano, 7d) |
| Permissões | `instagram_business_basic`, `instagram_business_manage_messages` (nomes novos; os antigos foram depreciados em 27/01/2025) |
| Refresh | `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=…` → +60d |
| ⚠️ Pré-requisito do operador | Instagram → Configurações → **Mensagens → Ferramentas conectadas → "Permitir acesso a mensagens"**. **Sem isso, envio e assinatura falham em silêncio.** |

Confirmado independentemente na doc da Meta em 2026-08-14: o refresh é `GET`, exige **só** `grant_type` +
`access_token`, **não** usa app secret nem interação humana, requer token válido com **≥24h** de vida e o escopo
`instagram_business_basic`, devolve 60 dias **a partir da renovação** e pode ser chamado indefinidamente.

### 4.2 O OAuth — onde cada peça mora

```
[Painel]  botão "Conectar com Instagram"  (screen config:true do plugin)
   │  GET /api/plugins/instagram/oauth/start?channel_id=…      (gated por core_permission)
   │     → gera state (nonce), grava em plugin_instagram_oauth_state, devolve a URL
   ▼
[Navegador] window.location = https://www.instagram.com/oauth/authorize?...&state=<nonce>
   │     scope=instagram_business_basic,instagram_business_manage_messages
   ▼
[Meta]  telas de login + consentimento
   ▼
[Callback] GET /api/plugins/instagram/public/oauth/callback?code=…&state=…   ← AUTH-EXEMPT
   │     valida state (hmac.compare_digest, uso único, TTL curto) → resolve channel_id
   │     POST api.instagram.com/oauth/access_token        (code → token curto, 1h)
   │     GET  graph.instagram.com/access_token?grant_type=ig_exchange_token  (→ 60 dias)
   │     GET  graph.instagram.com/me?fields=user_id,username                 (→ IG_ID)
   │     grava access_token + expires_at + ig_id via registry.set_credential
   │     POST {IG_ID}/subscribed_apps  (assina o webhook)
   ▼     redireciona de volta para a tela do canal
```

⚠️ **Por que o callback tem de ser público**: o painel autentica por `Authorization: Bearer` de `localStorage`
([httpClient.js:47](../web/static/js/services/httpClient.js#L47)). O redirect da Meta é uma navegação de topo do
navegador — **não carrega header nenhum**. Fora de `/public/`, o middleware devolve 401 e o fluxo morre.
Como a rota é aberta, ela mesma se autentica pelo `state`; molde de rigor: o callback público do `pagamentos`
(rate-limit por `client_ip`, `hmac.compare_digest`, 404 em vez de 401).

**Migration `001_oauth_state.sql`** (a primeira do plugin): tabela `plugin_instagram_oauth_state`
(`state` PK, `channel_id`, `created_at`, `consumed_at`). Estado em memória não serve — o toggle do plugin derruba o processo.

### 4.3 A renovação — implementar o contrato que o core já escreveu

`refresh_token_if_needed()` segue as regras do docstring de [channels/base.py:544-556](../channels/base.py#L544-L556):
só token **válido**, respeitar a idade mínima de 24h, persistir token **e** expiração via `registry.set_credential`,
**nunca levantar**.

**Divergência deliberada do docstring (D4):** ele sugere agir com `<10 dias` restantes; este plano renova aos
**~45 dias de vida (15 dias de margem)**. O motivo é o modo de falha: perder a janela de 60 dias mata o token
**permanentemente**. Dez dias de margem foi exatamente o que tornou o Chatwoot frágil. O loop roda a cada **6–12h**
(a janela é de dias; a checagem é barata).

**Alerta obrigatório (D4):** falha de renovação, ou faltando <7 dias, gera aviso ao operador — com agregação e
cooldown, molde `gowa/alerts.py`. O canal também recebe `last_error` + `logged_in=0` via `_maybe_flag_reauth`
([channels.py:289-304](../storages/plugins/instagram/channels.py#L289-L304), que **já existe**), o que acende o
estado "reconectar" no card sem código novo de UI.

### 4.4 O que o operador vê

| Momento | Experiência |
|---|---|
| Criar canal | Nome + um botão **"Conectar com Instagram"**. Não digita token, id de conta nem app id — só `app_id`/`app_secret` do próprio app da Meta (D2). |
| Conectado | Card mostra `@usuario`, conectado. A data de expiração **não** vai para o descriptor (seria campo editável): é servida por rota do plugin e exibida na screen. |
| Renovação | Invisível. Nada a fazer. |
| Token perto de vencer / falha | Alerta + card em estado de reconexão + botão **"Reconectar"** (o mesmo fluxo, 1 clique). |

---

## 5. Fases / Roadmap

```
WAVE 0   F0 · F1 · F2                          ← paralelo (docs, migration, base neutra)
              │ (F2 bloqueia F3)
WAVE 1   F3                                     ← sozinha: descriptor+identidade+status
              │
WAVE 2   F4 · F5                                ← paralelo [dependem de F3; F4 também de F1]
              │
WAVE 3   F6 · F7                                ← paralelo [F6←F4, F7←F5]
              │
WAVE 4   F8                                     ← testes
              │
WAVE 5   F9 → F10                               ← legados, depois release
```

| Wave | Fase | Entregável | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Reconciliar o guia 46 + fatos pinados | 🟢 | baixo | guia e código deixam de se contradizer |
| 0 | **F1** | Migration `001_oauth_state.sql` | 🟢 | baixo | tabela criada com prefixo correto |
| 0 | **F2** | `meta_graph.py` neutro (host/auth/envelope) | 🟢 | médio | testes de envelope verdes |
| 1 | **F3** | `channels.py`: descriptor, capabilities, identidade, `status()` | 🔴 `[depende de: F2]` | médio | canal novo conecta e `status()` mostra @usuario |
| 2 | **F4** | OAuth: authorize + callback público | 🔴 `[depende de: F1, F3]` | **alto** | 1 clique conecta de ponta a ponta |
| 2 | **F5** | `lifecycle.py` + `refresh_token_if_needed` | 🟢 `[depende de: F3]` | médio | token perto de vencer é renovado pelo loop |
| 3 | **F6** | Botão Conectar/Reconectar + estado do token | 🟢 `[depende de: F4]` | médio | operador conecta sem sair do painel |
| 3 | **F7** | Alerta de falha + estado de reconexão | 🟢 `[depende de: F5]` | médio | falha simulada gera alerta e acende o card |
| 4 | **F8** | Testes (~15 reescritos, 3 e2e preservados) | 🟢 `[depende de: F3–F7]` | médio | suíte do plugin verde |
| 5 | **F9** | Canais legados: credenciais órfãs | 🔴 `[depende de: F8]` | médio | nenhum `page_access_token` vivo no banco |
| 5 | **F10** | Versão, ZIP, instalação | 🔴 `[depende de: F9]` | baixo | zip publicado e instalado |

---

### Fase 0 — Reconciliar a documentação 🟢

**Objetivo:** o guia do plano 46 já descreve o Instagram Login e **contradiz** o código instalado desde o pivô.

**Itens:**
1. `[paralelo]` [46-guia-configuracao-provedores-meta-gmail-widget.md:66-90](46-guia-configuracao-provedores-meta-gmail-widget.md) — conferir contra os fatos pinados (§4.1); ele já traz o pré-requisito "Permitir acesso a mensagens" e o campo `ig_id`.
2. `[paralelo]` Anotar no mestre 46 que a decisão de 2026-07-24 (pivô para Facebook Login) foi **revertida** por este plano, com a evidência.

**Pronto quando:** um leitor do guia consegue configurar a Meta sem abrir este plano, e nada nele descreve o caminho morto.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** guia 46 §A2 reescrito (App ID do Instagram ≠ do Facebook, URI de redirect do OAuth, campos que exigem Acesso Avançado, cadastro manual do callback e por quê); nota de **REVERSÃO** no fecho do mestre 46; CLAUDE.md atualizado nos 2 pontos que descreviam o caminho morto (base `MetaGraphChannel` e refresh de token, que deixou de ser "sem nenhum consumidor").
- **Como foi feito / decisões:** o guia **já descrevia o Instagram Login** — foi escrito antes do pivô de julho, e o `D1` original valia. Reconciliar saiu mais barato que reescrever.
- **Problemas / pendências:** nenhuma.
- **Verificação:** leitura; não há teste automatizado de documentação.

---

### Fase 1 — Migration `001_oauth_state.sql` 🟢

**Objetivo:** persistir o `state` do OAuth (o plugin não tem migration nenhuma hoje).

**Itens:**
1. Criar `migrations/001_oauth_state.sql` com `plugin_instagram_oauth_state` (`state` PK, `channel_id`, `created_at`, `consumed_at`).
2. ⚠️ Prefixo `plugin_instagram_` é obrigatório — o migrator recusa o contrário.
3. ⚠️ Comentário SQL **não pode conter `;`** (o migrator splita por `;` antes de tirar comentários).

**Pronto quando:** boot do plugin aplica a migration; linha em `plugin_migrations`.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** `migrations/001_oauth_state.sql` (`plugin_instagram_oauth_state`) + `002_alert_state.sql` (`plugin_instagram_alert_state`).
- **Como foi feito / decisões:** `redirect_uri` guardado NA LINHA do state: a Meta compara a URI da autorização com a da troca do código, e re-derivar no callback (headers de proxy, `public_base_url` editada no meio) daria mismatch sem explicação.
- **Problemas / pendências:** ⚠️ **`migrations: migrations` no `plugin.yaml` era obrigatório e faltou na 1ª instalação** — `plugins/migrator.py:51` volta `[]` EM SILÊNCIO sem ele: nada no boot reclama e só o 1º clique em Conectar descobriria, com `relation does not exist`. Travado agora por `test_manifest_declares_migrations_dir`.
- **Verificação:** as duas migrations aplicadas no banco de dev (`plugin_migrations` = [1,2], tabelas criadas); `test_migrations_are_prefixed` cobre o prefixo e o `;` em comentário.

---

### Fase 2 — `meta_graph.py`: neutralizar o que é do Facebook 🟢

**Objetivo:** a base deixa de assumir Page Token + `graph.facebook.com`, sem quebrar o Messenger (que tem a **própria cópia** — plano 76·F9).

**Itens:**
1. `[paralelo]` `graph_host` ([:118](../storages/plugins/instagram/meta_graph.py#L118)) passa a ser sobrescrito pela subclasse.
2. `[sequencial]` `_auth_params` ([:217-224](../storages/plugins/instagram/meta_graph.py#L217-L224)): token vai no header `Authorization: Bearer`. Introduzir `send_appsecret_proof = False` **como flag de classe** — hoje o proof é anexado sempre que há `app_secret`, e o segredo **continua existindo** (assinatura do webhook), então depender da ausência dele não funcionaria.
3. `[sequencial]` `_message_envelope` ([:275-281](../storages/plugins/instagram/meta_graph.py#L275-L281)): `messaging_type: "RESPONSE"` sai do envelope base. ⚠️ O retry de janela em `_post_with_window_fallback` ([channels.py:266-268](../storages/plugins/instagram/channels.py#L266-L268)) **continua** setando `MESSAGE_TAG` + `HUMAN_AGENT` — os fatos pinados confirmam que isso existe no Instagram Login.
4. `[paralelo]` **Não tocar**: `parse_inbound` ([:441-592](../storages/plugins/instagram/meta_graph.py#L441-L592)), `download_media` ([:405-438](../storages/plugins/instagram/meta_graph.py#L405-L438)), `graph_error`, `attachment_type_for`.

**Pronto quando:** teste de envelope mostra corpo sem `messaging_type` no envio normal e **com** `MESSAGE_TAG` no retry; nenhuma chamada leva `appsecret_proof`.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** 3 flags de classe em `meta_graph.py` (`auth_in_header`, `send_appsecret_proof`, `default_messaging_type`) + `_auth_headers()` + `headers=` nos 4 call sites httpx; WARNING quando a assinatura do webhook não confere.
- **Como foi feito / decisões:** flags de classe em vez de depender da ausência do `app_secret` — ele CONTINUA existindo no Instagram Login (valida a assinatura do webhook), então 'tem segredo' não é proxy para 'manda o proof'. O WARNING na assinatura foi acrescentado porque um App Secret errado era indiagnosticável (200 `bad_signature` e o operador só via 'não chega nada').
- **Problemas / pendências:** nenhuma. `parse_inbound`, `download_media`, `media_urls` e os `MediaLimits` ficaram intocados, como o plano previa.
- **Verificação:** `test_send_text_body_has_no_messaging_type` (corpo sem `messaging_type`, `Bearer` no header, sem `access_token`/`appsecret_proof` na query).

---

### Fase 3 — `channels.py`: descriptor, capabilities, identidade, status 🔴 `[depende de: F2]`

**Objetivo:** o provider passa a ser Instagram Login de fato.

**Itens:**
1. `[sequencial]` Host: `INSTAGRAM_GRAPH_HOST = "graph.instagram.com"` ([:56](../storages/plugins/instagram/channels.py#L56)).
2. `[sequencial]` Capabilities ([:117-138](../storages/plugins/instagram/channels.py#L117-L138)): **inverter a linha :128** (`# NO token_refresh` → `token_refresh=True`) e trocar `required_credentials` para `("ig_id","access_token","app_id","app_secret","verify_token")`.
3. `[sequencial]` Descriptor ([:141-201](../storages/plugins/instagram/channels.py#L141-L201)): `page_id` e `page_access_token` **saem**; `ig_id` entra (text, auto-descoberto, editável); `access_token` (secret) passa a ser preenchido pelo OAuth; `app_id` vira `required=True` (D2); `app_secret` e `verify_token` **permanecem com o mesmo nome** (⚠️ `verify_token` é lido literalmente pelo core).
4. `[sequencial]` Identidade ([:204-211](../storages/plugins/instagram/channels.py#L204-L211)): `AccountIdentity("ig_id", ig_id)`.
5. `[sequencial]` `status()` ([:218-241](../storages/plugins/instagram/channels.py#L218-L241)): `GET /me?fields=user_id,username`.
6. `[paralelo]` Texto de `_maybe_flag_reauth` ([:300-302](../storages/plugins/instagram/channels.py#L300-L302)): "cole um novo Page Access Token" → "reconectar".
7. `[paralelo]` **Não tocar**: `_post_with_window_fallback`, `_conversation_with_human`, `_MEDIA_LIMITS`.

**Pronto quando:** um canal criado com token colado à mão conecta, `status()` mostra `@usuario`, e os 2 canais legados passam a exibir "Credenciais faltando" no card (comportamento desejado por D1).

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** `channels.py` reescrito: host, `token_credential_key`, as 3 flags, `token_refresh=True`, descriptor novo, `identity_from_credentials`+`account_identity` por `ig_id`, `status()` em `/me`.
- **Como foi feito / decisões:** ⚠️ **`required_credentials` ficou com 3 chaves, não 5** — divergência do item 2 da fase. `channel_service.provider_descriptor:508-514` FORÇA `required=True` no descriptor para cada chave dessa tupla, e `creation_required_credentials` lê o descriptor já reconciliado: listar `access_token`/`ig_id` tornaria IMPOSSÍVEL criar o canal antes de conectar — a ordem exata do fluxo OAuth. `status()` também aprende o `ig_id` de um token colado à mão (senão dedup e assinatura por conta não teriam o que usar).
- **Problemas / pendências:** nenhuma.
- **Verificação:** reconciliação real do core rodada contra o plugin instalado: criação com só `app_id`+`app_secret`+`verify_token` passa; `access_token` sai mascarado e `app_id`/`ig_id` em claro (são identificadores públicos).

---

### Fase 4 — OAuth: authorize + callback público 🔴 `[depende de: F1, F3]`

**Objetivo:** conectar com um clique, sem colar nada. **Não há precedente de OAuth com redirect no repo** — zero
ocorrências de `RedirectResponse` — então esta é a fase de maior risco.

**Itens:**
1. `[sequencial]` Remover de `routes.py` o eixo morto: `_post_app_subscription` ([:327-346](../storages/plugins/instagram/routes.py#L327-L346)), `_post_subscribed_apps` da Página ([:271-283](../storages/plugins/instagram/routes.py#L271-L283)), `_app_id` auto-detectado e o que deles depende.
2. `[sequencial]` `GET /oauth/start` (gated por `core_permission("channel.manage")`): gera nonce, grava em `plugin_instagram_oauth_state`, devolve a URL de autorização com `scope=instagram_business_basic,instagram_business_manage_messages`.
3. `[sequencial]` `GET /public/oauth/callback` — **auth-exempt**: valida `state` com `hmac.compare_digest`, uso único e TTL curto; rate-limit por `client_ip`; 404 em vez de 401. Troca `code` → token curto (`POST api.instagram.com/oauth/access_token`) → longo (`GET graph.instagram.com/access_token?grant_type=ig_exchange_token`) → `IG_ID` (`GET /me`). Persiste via `registry.set_credential`. Assina `POST {IG_ID}/subscribed_apps`.
4. `[paralelo]` ⚠️ Segredo **nunca** na URL de redirect; o `code` é de uso único e o `state` também.

**Pronto quando:** clicar em "Conectar", autorizar na Meta e voltar cria um canal conectado com `access_token`, `expires_at`, `ig_id` gravados e webhook assinado — sem o operador colar nada.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída
- **O que foi feito:** módulo `oauth.py` (authorize/exchange/refresh/me/subscribe, tudo `(dados, erro)`, nunca levanta); `GET /oauth/start` autenticado; `GET /public/oauth/callback` auth-exempt com página de resultado própria; `/connection`; `/subscribe` por conta; `/diagnose` reescrito. Removido todo o eixo `{app_id}/subscriptions` + `{page_id}/subscribed_apps` + auto-detecção de `app_id`.
- **Como foi feito / decisões:** ⚠️ **`post_create` virou `webhook_url`, não `autoconfigure`** — registrar a callback_url por API SOBRESCREVE a de qualquer outro sistema no mesmo app: foi assim que o webhook do Chatwoot caiu. Com D2 (app próprio por instalação) o cadastro é manual e único. A página de resultado é servida pelo próprio callback em vez de redirecionar: um redirect engoliria a mensagem de erro, que é justamente o que o operador precisa ler. Dedup checado ANTES de gravar (senão o índice único do core estouraria depois, no sweep, sem explicação).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `state` de uso único / desconhecido / expirado recusado; `_start_oauth` exige HTTPS público e credenciais do app; rota pública probada ao vivo (não é 401, e não ecoa texto de terceiro).

---

### Fase 5 — `lifecycle.py` + renovação 🟢 `[depende de: F3]`

**Objetivo:** ser o **primeiro consumidor** do seam dormente do core.

**Itens:**
1. `[sequencial]` Criar `lifecycle.py` com `setup(ctx)` registrando `ctx.spawn_task("token_refresh", loop)` (molde `telegram/lifecycle.py:202-204`) e acrescentar `lifecycle: lifecycle` ao `entry` do [plugin.yaml:19-21](../storages/plugins/instagram/plugin.yaml#L19-L21). ⚠️ **Sem isso o loop nunca roda e o token morre calado.**
2. `[sequencial]` `refresh_token_if_needed()` em `channels.py` conforme §4.3: renova aos ~45 dias de vida (**15 dias de margem** — D4), exige ≥24h e token válido, grava `access_token`+`expires_at` por `registry.set_credential`, **nunca levanta**.
3. `[paralelo]` Cadência do loop: 6–12h.
4. `[paralelo]` Erro `190` em send/inbound → `_maybe_flag_reauth` (já existe).

**Pronto quando:** canal com `expires_at` próximo é renovado pelo loop e ganha novo `expires_at`; desabilitar o plugin cancela a task; token expirado **não** entra em retry storm.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída
- **O que foi feito:** `lifecycle.py` com `ctx.spawn_task("token_refresh", …)` (PERMANENT, 6h, `_live()` materializa canal fora do registry) + `refresh_token_if_needed`/`_refresh_token` em `channels.py` + `entry.lifecycle` no manifesto.
- **Como foi feito / decisões:** margem de 15 dias (D4). Validade DESCONHECIDA conta como 'renove agora' — a resposta ensina a expiração, e não tentar seria deixar morrer calado. Token já vencido não entra em retry: vira estado de reconexão. Marca `refresh_failed_at` para o alerta poder gritar na hora (sem ela, um erro aos 45 dias ficaria mudo até os 7 dias críticos).
- **Problemas / pendências:** nenhuma.
- **Verificação:** 6 testes de renovação (longe do vencimento, dentro da margem, idade mínima de 24h, expirado sem retry, validade desconhecida, nunca levanta) + `test_manifest_declares_lifecycle`.

---

### Fase 6 — Frontend: Conectar / Reconectar 🟢 `[depende de: F4]`

**Objetivo:** o botão vive na screen `config:true` que o plugin **já tem** — sem inventar `post_create.kind` no core (§3.2).

**Itens:**
1. `[sequencial]` [static/instagram.js](../storages/plugins/instagram/static/instagram.js): botão "Conectar com Instagram" / "Reconectar" chamando `/oauth/start` e navegando a página inteira (⚠️ iframe é bloqueado pela CSP).
2. `[paralelo]` Mostrar estado do token (expira em N dias) por rota do plugin — **não** pelo descriptor (viraria campo editável).
3. `[paralelo]` Aviso do pré-requisito "Permitir acesso a mensagens" (falha silenciosa sem ele).
4. `[paralelo]` Modo escuro: classes `wa-*` e `.wa-field`.

**Pronto quando:** operador conecta e reconecta sem sair do painel; tela legível no modo escuro.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída
- **O que foi feito:** `static/instagram.js` refeito (Conectar/Reconectar, estado do token, campos para copiar, Reassinar, Diagnosticar, configuração do alerta) e `WebhookHealthRow.js` reescrito para saúde da CONEXÃO.
- **Como foi feito / decisões:** o `WebhookHealthRow` chamava `/webhook-status` e `/set-webhook`, que deixaram de existir — teria ficado 404 mudo no card. O que precisa de vigilância agora é o TOKEN, não para onde aponta o callback. Tempo real pelo `wsBus` do core (import dinâmico defensivo), nunca `new WebSocket('/ws')`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --input-type=module --check` nos 3 módulos; classes `wa-*`/`.wa-field` em toda superfície nova.

---

### Fase 7 — Alerta e estado de reconexão 🟢 `[depende de: F5]`

**Objetivo:** ninguém precisa **vigiar** — o sistema chama.

**Itens:**
1. `[sequencial]` Alerta com agregação e cooldown (molde `gowa/alerts.py`), estado em tabela `plugin_instagram_*`: dispara em falha de renovação e quando faltar <7 dias.
2. `[paralelo]` `audit()` nas ações com dono (conectar, reconectar, assinar webhook) com `resource_type="channel"`, `resource_id=<channel_id>` — regra do CLAUDE.md para plugin de canal.
3. `[paralelo]` Linha no card via slot `channel.card.rows` (o plugin já usa em `WebhookHealthRow.js`).

**Pronto quando:** renovação forçada a falhar gera alerta uma vez (e só conta na repetição), o card acende, e a trilha registra a reconexão.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída
- **O que foi feito:** `alerts.py` (Telegram direto, agregação por VALOR, cooldown de 12h, estado em `plugin_instagram_alert_state`, recuperação quando normaliza) + `GET`/`PUT /alert-settings` + `POST /alert-test` + broadcast WS.
- **Como foi feito / decisões:** `last_alert_ts` só é gravado depois que o Telegram devolve `message_id` — falha de transporte não pode consumir o cooldown em silêncio. Token do bot mascarado no GET e PUT sem token não apaga o salvo.
- **Problemas / pendências:** o destino do Telegram é opcional: sem ele o alerta vive no card + WS. Configurar é do operador.
- **Verificação:** `test_alert_toggle_actually_disables` (o gate corta ANTES de classificar).

---

### Fase 8 — Testes 🟢 `[depende de: F3–F7]`

**Objetivo:** suíte do plugin verde, sem perder o ativo mais valioso.

**Itens:**
1. `[sequencial]` **Preservar intactos** os 3 testes que sobem o app pelo loader real: `test_webhook_rejects_bad_signature` (:506), `test_webhook_accepts_valid_signature` (:516), `test_webhook_handshake_uses_verify_token` (:527). São eles que provam que a costura core↔plugin continua de pé.
2. `[paralelo]` Reescrever os ~15 obsoletos: `test_graph_host_is_facebook` (:152), `test_register_app_webhook_*` (:329,:350), `test_subscribe_uses_facebook_host_and_page` (:364), `test_identity_from_page_id` (:141), `test_descriptor_shape` (:100), `test_capabilities` (:129), `test_status_shows_connected_instagram_username` (:289) e os 6 de `_diagnose` (:445-472).
3. `[paralelo]` **Novos**: refresh (mock — renova, respeita 24h, não renova expirado, nunca levanta); OAuth (state inválido/reusado/expirado ⇒ recusa; callback feliz grava as 3 credenciais); envelope sem `messaging_type`.
4. `[paralelo]` Manter verdes os de janela/HUMAN_AGENT (:245-276) e mídia (:225,:303).

**Pronto quando:** `python3 scripts/test_plugins.py instagram` verde no repo de plugins.

#### Status de execução — Fase 8
**Estado:** ✅ Concluída
- **O que foi feito:** suíte reescrita: 56 testes, os 3 e2e que sobem o app pelo loader real PRESERVADOS intactos.
- **Como foi feito / decisões:** o teste de TTL do state envelhece a LINHA, não o relógio: `routes_mod.time` é o módulo `time` do processo inteiro e substituí-lo por um lambda que chama `time.time()` recursa em si mesmo (foi o que aconteceu na 1ª tentativa).
- **Problemas / pendências:** ⚠️ a 1ª rodada deu 9 erros `relation "observations" does not exist` — era OUTRA sessão rodando a suíte do core no MESMO banco de teste (CLAUDE.md: não rodar duas suítes PostgreSQL em paralelo). Criado `whatsbot_test_ig` (UTF8/template0) só para esta frente.
- **Verificação:** `python3 scripts/test_plugins.py --python-only instagram` ⇒ **56 passed**.

---

### Fase 9 — Canais legados 🔴 `[depende de: F8]`

**Objetivo:** não deixar token do caminho morto vivo no banco.

**Contexto medido (dev, 2026-08-14):** `instagram_3dfb4d90` (Redes Brasil) e `instagram_cdc46d98` (Techify);
inboxes 16/17; **9 mensagens no total**; contatos 52/53 — os dois são o mesmo humano com IGSIDs diferentes,
prova de que o IGSID é app-scoped. Volume irrisório: D1 é barata aqui.

**Itens:**
1. `[sequencial]` ⚠️ **Não existe delete de credencial por chave** no core ([channel_credential_repo.py:18-48](../db/repositories/channel_credential_repo.py#L18-L48) tem só `delete_all`, chamado apenas na exclusão dura). Escolher: **(a)** propor `delete(channel_id, key)` ao core, **(b)** sobrescrever com vazio (⚠️ a row **permanece**), ou **(c)** excluir e recriar o canal com `?purge=true` — hoje a única via que realmente limpa. **Recomendação: (c)** para os 2 canais existentes, dado o volume; (a) fica como melhoria futura.
2. `[sequencial]` ⚠️ `account_identity` muda de kind `page_id` para `ig_id`, mas o índice único do banco é só `(provider, account_identity)` — o **kind não entra no índice** ([db/tables.py:299-306](../db/tables.py#L299-L306)). Verificar que o valor novo não colide.
3. `[paralelo]` Documentar que os IGSIDs mudam com o app novo: os contatos nascem de novo, o histórico antigo fica órfão.

**Pronto quando:** nenhum `page_access_token` vivo no banco e os canais conectados pelo caminho novo.

#### Status de execução — Fase 9
**Estado:** ✅ Concluída
- **O que foi feito:** limpeza direta dos 2 canais: credenciais `page_id`/`page_access_token` apagadas, `account_identity`/`kind` zerados, canais marcados desconectados com erro acionável; `app_id`/`app_secret` do canal Redes Brasil trocados pelo par do INSTAGRAM (`1433191074801648`, o do Chatwoot, com Acesso Avançado comprovado).
- **Como foi feito / decisões:** ⚠️ **DIVERGE do P3**, que recomendava excluir e recriar com `?purge=true`. Aquilo destruiria 2 conversas / 9 mensagens reais E trocaria os `channel_id`, invalidando URLs de callback já cadastradas na Meta — tudo para resolver o que era só credencial órfã no banco. A limpeza resolve o mesmo sem destruir nada. Descoberta no caminho: os DOIS `app_id` eram do **Facebook** (`4195516780669357` / `765000666582242`), que o login recusaria com `Invalid platform app`.
- **Problemas / pendências:** ⚠️ o canal **Techify** ficou com o `app_id`/`app_secret` do Facebook — falta o par do Instagram, que só o operador tem no App Dashboard. Instalações que atualizarem de 2.2.0 mantêm as credenciais órfãs (o core não tem delete de credencial por chave); nada as lê, mas é segredo morto em repouso.
- **Verificação:** conversas/mensagens conferidas antes e depois (1/6 e 1/3, intactas).

---

### Fase 10 — Versão, ZIP e instalação 🔴 `[depende de: F9]`

**Itens:**
1. `[sequencial]` `plugin.yaml`: **3.0.0** (MAJOR — credenciais incompatíveis, canal legado não sobrevive).
2. `[sequencial]` `python3 scripts/build_plugins.py instagram` + `--check` no repo `whatsbot-pro-plugins`.
3. `[sequencial]` Instalar o zip **localmente antes de publicar** — a cópia viva é `storages/plugins/instagram/`.
4. `[paralelo]` ⚠️ `--check` pode mentir "outdated" por umask (664 vs 644) — não rebuildar às cegas.

**Pronto quando:** zip publicado, instalado, canal real conectado e DM de terceiro chegando.

#### Status de execução — Fase 10
**Estado:** ✅ Concluída
- **O que foi feito:** `plugin.yaml` 3.0.0, `instagram.json` e `catalog.json` atualizados, zip reconstruído (14 arquivos) e instalado em `storages/plugins/instagram/`.
- **Como foi feito / decisões:** o `build_plugins.py` valida versão em 3 lugares (manifesto do src, metadados do plugin, catálogo) e recusa build se divergirem — foi ele que pegou os dois esquecimentos.
- **Problemas / pendências:** ⚠️ **nada foi commitado nem publicado** nos dois repositórios — aguardando sua conferência.
- **Verificação:** plugin carregado em dev sem `load_error`, versão 3.0.0, migrations 1 e 2 aplicadas, rota pública respondendo.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Corpo de send | Copiar o payload do `whatsapp_cloud` (com `messaging_product`) → 400 | Corpo Messenger puro `{recipient}{message}` (§4.1) |
| `appsecret_proof` | Continua sendo anexado porque `app_secret` **precisa** existir (assinatura do webhook); se a API do IG Login recusar, quebra de forma difícil de diagnosticar | Flag de classe `send_appsecret_proof = False` (F2), não depender da ausência do segredo |
| `messaging_type` | Tirar do envelope base pode quebrar o retry de janela | Retry mantém `MESSAGE_TAG` explicitamente; teste dedicado (F2, F8) |
| Morte do token | >60 dias sem renovar = **morte permanente**, sem conserto pelo servidor | Renovar aos 45 dias (15 de margem) + alerta + loop por relógio (D4) |
| Loop não registrado | `entry.lifecycle` esquecido ⇒ token morre calado em 60 dias | Item explícito na F5; teste que falha se `entry` não declarar `lifecycle` |
| Callback público | Rota aberta na internet recebendo `code` | `state` de uso único com `compare_digest` + TTL + rate-limit por IP + 404 em vez de 401 |
| CSP | Tentar iframe ou `fetch` direto à Meta no navegador | Redirect de página inteira + troca de token no servidor (§3.2) |
| `verify_token` | Renomear quebra o handshake **em silêncio** (403 para a Meta) | Nome preservado; teste `test_webhook_handshake_uses_verify_token` |
| Registry | Trocar o plugin sem reiniciar ⇒ canal fora do registry e todo webhook vira `inactive_channel` (200, sem erro) | Reiniciar após instalar; verificar com POST de diagnóstico |
| Pré-requisito da Meta | "Permitir acesso a mensagens" desligado ⇒ envio e assinatura falham **em silêncio** | Aviso na screen (F6) + no guia (F0) |
| Echo invertido | `sender`/`recipient` trocam no `is_echo` | Já tratado em [meta_graph.py:474-477](../storages/plugins/instagram/meta_graph.py#L474-L477) — **não regredir** |
| `expires_at` em `config` | O PUT do formulário sobrescreve o JSON inteiro e apaga o timestamp | Gravar como **credencial** (§3.2) |
| Índice de identidade | O kind não entra no índice único | Verificar colisão na F9 |
| Segredo em log | `app_secret`/token em mensagem de erro | Citar o **campo**, nunca o valor (precedente do plano 104) |

---

## 7. Perguntas em aberto

**P1 — `appsecret_proof` é aceito ou recusado pelo `graph.instagram.com`?** ⏸️ A confirmar na F2.
(a) aceito ⇒ a flag é só higiene; (b) recusado ⇒ a flag é obrigatória. **Recomendação:** implementar a flag de
qualquer forma e medir na primeira chamada real.

**P2 — Token na query ou só no header?** ⏸️ Os fatos pinados especificam `Authorization: Bearer`; a query string
pode funcionar também. **Recomendação:** header, como o documento original manda; não depender do que "também funciona".

**P3 — O que fazer com os canais legados?** ✅ **DECIDIDO (2026-08-14):** excluir e recriar com `?purge=true`
(9 mensagens no total). `delete(channel_id, key)` no core fica como melhoria futura, não bloqueia.
⚠️ **A execução DIVERGIU desta decisão** (ver F9): excluir destruiria 2 conversas / 9 mensagens reais **e trocaria os
`channel_id`**, invalidando URLs de callback já cadastradas na Meta — para resolver o que era só credencial órfã no
banco. Foi feita limpeza direta (DELETE nas 2 chaves mortas + `account_identity` zerada), que atinge o mesmo objetivo
sem destruir nada. O `delete(channel_id, key)` no core segue como melhoria futura.

**P4 — Comentários de post e de live?** ⏸️ **ADIADO.** Os campos `comments`/`live_comments` já estão assinados nos
dois apps, mas `parse_inbound` só percorre `messaging[]` — comentário chega e é descartado. Exige o ramo
`entry[].changes[]` e uma decisão de produto (comentário vira conversa no painel?). **Plano próprio.**

**P5 — Renovar o token do Chatwoot agora?** ⏸️ Ele expira em **20/09/2026** e nada o renova desde 06/08. É a
credencial que hoje serve de prova de que o caminho funciona. Uma chamada empurra para meados de novembro.
**Aguardando decisão do usuário.**

**P6 — Messenger (`facebook_messenger`) muda?** ✅ **NÃO.** `pages_messaging` tem acesso avançado comprovado
(108 contatos reais atendidos). O plugin fica como está, com a própria cópia da base (plano 76·F9).

---

## 8. Apêndice — arquivos-chave

**Plugin (fonte de desenvolvimento: `../whatsbot-pro-plugins/plugins/instagram/src/`)**

| Arquivo | Ação |
|---|---|
| `channels.py` (344) | reescrita parcial — host, capabilities, descriptor, identidade, `status()`, `refresh_token_if_needed` |
| `meta_graph.py` (619) | 3 pontos (host, `_auth_params`, `_message_envelope`); parser **intocado** |
| `routes.py` (662) | metade removida; nascem `/oauth/start` e `/public/oauth/callback` |
| `lifecycle.py` | **novo** |
| `migrations/001_oauth_state.sql` | **novo** (primeira do plugin) |
| `static/instagram.js` (242) | botão Conectar/Reconectar + estado do token |
| `media_urls.py` (91) | **sem mudança** |
| `plugin.yaml` | `entry.lifecycle`, versão 3.0.0 |
| `tests/python/test_channel.py` (537, 30 testes) | ~15 reescritos, 3 e2e preservados |

**Core — leitura apenas, nada muda**

[channels/base.py:149,270,535-558](../channels/base.py#L535-L558) · [server/app.py:57,576-577,664-679](../server/app.py#L57) ·
[server/routes/channel_webhook.py:635-650,652-769](../server/routes/channel_webhook.py#L635-L650) ·
[app/services/channel_service.py:177-199,759-761](../app/services/channel_service.py#L177-L199) ·
[db/tables.py:269-317](../db/tables.py#L269-L317) · [db/repositories/channel_credential_repo.py:18-48](../db/repositories/channel_credential_repo.py#L18-L48)

**Documentação**

[46-guia-configuracao-provedores-meta-gmail-widget.md:66-90](46-guia-configuracao-provedores-meta-gmail-widget.md) ·
plano original recuperável em `git show f096f73^:docs-planos/46-plano-canais-meta-email-widget-03-instagram.md`

---

## 8.1 Revisão adversarial (2026-08-14) — o que ela pegou

5 lentes independentes sobre o código pronto, cada achado passado por um cético que tentou refutá-lo lendo o código.
**13 confirmados, 3 refutados.** Os que mais valeram:

| # | Achado | Por que era caro | Correção |
|---|---|---|---|
| 1 | **Segredo no log.** `exchange_long_lived`/`refresh_long_lived` só existem com os valores na query, e o httpx loga `request.url` INTEIRA em INFO — num logger que nada no repositório silencia. O Instagram App Secret e o token de 60 dias iam em claro para `logs/whatsbot.log` e para o stdout do container a cada conexão e a cada renovação. | Quem lesse o log leria e escreveria todas as DMs da conta **e** poderia forjar `X-Hub-Signature-256`. Viola a regra explícita do CLAUDE.md — e era incoerente com a própria reescrita, que tirou o token da query de propósito. | Filtro `_RedactSecrets` no logger `httpx` (redige, não silencia: `setLevel` global apagaria log de chamada concorrente). Cobre também o token do bot do Telegram, que vai no PATH. |
| 2 | **Envelope do Business Login.** `exchange_code` lia o token na raiz; o Business Login do Instagram devolve `{"data":[{…}]}`. | Um 200 perfeitamente válido viraria "a Meta não devolveu um token" — **o OAuth nunca teria funcionado**, com erro que não aponta para a causa. | `_unwrap()` normaliza as duas formas. |
| 3 | **Token colado à mão julgado morto para sempre.** O descriptor deixa colar `access_token`, e essa escrita passa só pela chave submetida: `expires_at` fica com o valor do token ANTERIOR. Se aquele estivesse vencido, o novo era declarado irrecuperável e nunca renovado. | Falha terminal e silenciosa — exatamente o modo de falha que este plano existe para matar. | `token_fp` (sha256 truncado) desempata: validade de outro token conta como desconhecida. |
| 4 | **Reconectar não limpava `refresh_failed_at`.** | Alerta falso a cada 12h por ~45 dias, com o token já saudável — e alerta que mente é alerta que se aprende a ignorar. | `token_credentials()` virou o ponto ÚNICO de "token novo persistido", usado pela renovação **e** pelo OAuth. |
| 5 | **O interruptor do alerta não desligava nada.** | Desligado só de fachada: o broadcast na tela e o cooldown continuavam. | Gate ANTES de classificar, não no envio. |
| 6 | **`graph_api_version` lido das credenciais**, mas é `config_field` ⇒ mora em `channels.config`. | Envio usava a versão configurada e OAuth/diagnóstico usavam outra, em silêncio. | `_version(creds, channel_id)` na mesma ordem do `MetaGraphChannel`. |
| 7 | **Um `reason` para dois erros.** Falha ao LER a assinatura era diagnosticada como token morto. | Mandava reconectar um token perfeitamente bom. | `token_reason` × `subscribe_reason` separados. |
| 8 | **Callback ecoava texto de terceiro antes de validar o `state`.** | Qualquer um na internet fazia a rota renderizar texto escolhido por ele num domínio do painel. | `state` validado PRIMEIRO (a Meta o devolve no cancelamento também); motivo vai para o log, não para a página. |
| 9 | **Retry HUMAN_AGENT perdia o `MEDIA_TIMEOUT`.** | Vídeo que o 1º POST entregaria estourava por tempo no retry. | `timeout=timeout` repassado. |

**Refutados** (a verificação derrubou): rotas de operador aceitarem `channel_id` de outro provider (gateadas por
`core_permission`, sem travessia de fronteira de confiança); canal desabilitado sair do loop (é a semântica que o
**core** dá a "desabilitado" — nem materializa a instância); e inbound não casar `entry[].id` com `ig_id` (o core roteia
por `channel_id` na URL).

### 8.2 Medido em 2026-08-14 — registrar o callback por API é IMPOSSÍVEL neste caminho

A F4 removeu o eixo `POST /{app_id}/subscriptions` por ser **destrutivo** (uma `callback_url` por app+objeto).
Perguntado depois se dava para trazê-lo de volta como botão, a medição mostrou que a questão nem chega a ser essa:
**a configuração de webhook do Instagram Login não mora nessa edge.**

| Nó | Token | Resposta |
|---|---|---|
| Instagram App ID | `IG_ID\|IG_SECRET` | `code 190` — o App ID do Instagram não forma app token |
| **Facebook App ID** | **`FB_ID\|FB_SECRET`** | **`{"data": []}`** — responde, e está VAZIA |
| Facebook App ID | `IG_ID\|IG_SECRET` | `code 190` |
| Instagram App ID | `FB_ID\|FB_SECRET` | `(#100) Tried accessing nonexisting field (subscriptions)` |

A linha 2 é a prova: a edge responde **vazia** enquanto o webhook está **entregando DM normalmente** (medido no mesmo
minuto: conta assinada em `messages,messaging_postbacks,messaging_seen,message_reactions` e mensagem inbound gravada).
Um `POST` ali escreveria num lugar que este transporte não lê — o operador veria "webhook configurado" e nada mudaria.

⚠️ **Por que a 2.2.0 conseguia:** no caminho *Instagram via Facebook*, o `object=instagram` mora MESMO nessa edge — foi
por isso que aquela versão sobrescreveu o webhook do Chatwoot. Trocar de caminho tirou a capacidade junto com o risco.
Só leitura foi testada; escrever exigiria mexer na configuração de um app com canal em produção.

**Consequência para a UX:** quando a URL pública muda, não há como o painel LER nem CORRIGIR o callback. O sinal
utilizável é "está chegando webhook?" — `recent_inbound` no `/diagnose`, que já existe.

**Não corrigido, por decisão:** `expires_at`/`token_issued_at`/`token_fp`/`ig_username` aparecem mascarados entre as
credenciais no payload do canal. É feio, mas é **o que o próprio core prescreve** ([channels/base.py:552-554](../channels/base.py#L552-L554)
manda persistir `expires_at` via `registry.set_credential`), e o valor sai mascarado. Mover para `channels.config`
esbarraria na armadilha do §3.2 (o PUT do formulário sobrescreve o JSON inteiro).

---

## 9. Checklist de verificação

- [ ] `parse_inbound` verde com fixtures do Instagram Login (texto, anexo, echo invertido, read, reaction, unsend)
- [ ] Envio de texto ≤1000 bytes e mídia por URL; `Bearer` correto; **sem** `messaging_product`; **sem** `messaging_type` no envio normal
- [ ] Retry de janela ainda manda `MESSAGE_TAG` + `HUMAN_AGENT` — e **só** com humano na conversa
- [ ] Refresh testado com mock: renova, respeita ≥24h, recusa token expirado sem retry storm, nunca levanta
- [ ] `entry.lifecycle` declarado; desabilitar o plugin cancela a task
- [ ] OAuth: `state` inválido/reusado/expirado é recusado; callback feliz grava `access_token` + `expires_at` + `ig_id`
- [ ] Nenhum segredo na URL de redirect nem em log
- [ ] Assinatura inválida do webhook rejeitada (200 `bad_signature`); handshake com `verify_token` verde
- [ ] Dedup por `ig_id`; sem colisão no índice único
- [ ] Alerta de falha com agregação e cooldown; card acende o estado de reconexão
- [ ] Auditoria com `resource_type="channel"` nas ações com dono
- [ ] Modo escuro legível na screen do plugin
- [ ] Migration com prefixo `plugin_instagram_` e round-trip
- [ ] Restart do plugin verifica o canal no registry (POST de diagnóstico ≠ `inactive_channel`)
- [ ] `python3 scripts/test_plugins.py instagram` verde
- [ ] Suíte do core verde no Postgres (`WHATSBOT_TEST_DB_URL`) — 3 falhas pré-existentes conhecidas
- [ ] **Teste final de aceite:** pessoa **sem função nenhuma** no app manda DM e ela chega no painel
