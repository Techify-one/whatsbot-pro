# Guia de configuração dos provedores — Meta (Instagram/Messenger), E-mail (Gmail) e Widget

> **Companheiro do Plano 46** ([00-mestre](46-plano-canais-meta-email-widget-00-mestre.md)). Este documento é o **runbook do operador**: o que fazer FORA do código (painéis da Meta, Google, DNS, embed) para os 4 canais funcionarem. Não é plano de implementação.
> **Verificado em julho/2026.** Graph API atual = **v25.0** (a Meta sobe ~a cada trimestre — o campo `graph_api_version` do canal deixa isso configurável). ⚠️ Datas/telas dos painéis da Meta e do Google **mudam** — se algo estiver diferente, o conceito continua o mesmo.

---

## Sumário
- [Parte A — Meta (Instagram Direct + Facebook Messenger)](#parte-a)
- [Parte B — E-mail (Gmail e outros)](#parte-b)
- [Parte C — Widget de site](#parte-c)
- [Apêndice — checklists rápidos](#apendice)

---

<a name="parte-a"></a>
## Parte A — Meta: Instagram Direct + Facebook Messenger

Instagram e Messenger usam a **mesma plataforma** (Meta Graph API + webhooks), então o setup do **app Meta** é compartilhado; só o produto e os tokens diferem. Faça o **A0 (base)** uma vez, depois A1 (Messenger) e/ou A2 (Instagram).

### ⚠️ Antes de tudo: entenda os 2 modos e o prazo
- **Development mode:** o app só fala com contas que têm **papel no app** (admin/dev/testador). Serve pra você **testar** com a sua própria Página/conta IG. **Não** atende clientes reais.
- **Live mode + Advanced Access:** necessário pra atender o público / Páginas de clientes. Exige **App Review** + **Business Verification**.
- **Prazo real:** Business Verification e App Review levam **de dias a semanas**. Comece cedo. Enquanto isso, teste tudo em Development mode com contas testadoras.

### A0 — Criar o app Meta (base, uma vez)
1. Acesse **developers.facebook.com** → **My Apps** → **Create App** → tipo **Business**.
2. Anote **App ID** e, em **App Settings → Basic**, o **App Secret** (clique em "Show"). → no WhatsBot, o App Secret vira o campo **`app_secret`** de cada canal (usado pra validar a assinatura `X-Hub-Signature-256` dos webhooks). **Nunca** exponha o App Secret no navegador.
3. **App Settings → Basic:** preencha Privacy Policy URL, App Icon (1024×1024), categoria, e um **Data Deletion** callback/URL (exigidos no App Review).
4. **(Produção) Business Verification:** conecte o app a um **Meta Business portfolio** (Business Settings) e complete a verificação do negócio (razão social/endereço/documentos batendo com os oficiais). Sem isso, o Advanced Access é negado.
5. Escolha um **Verify Token** (uma string qualquer que você inventa) — você vai colar a mesma no WhatsBot e no painel da Meta. No WhatsBot é o campo **`verify_token`** (o botão "Sugerir" gera uma).

### A0.1 — A URL de webhook do WhatsBot
Cada canal criado no WhatsBot te dá uma **Callback URL** (mostrada no card após criar — `post_create`):
- Messenger: `https://SEU_DOMINIO/api/webhook/facebook_messenger/<channel_id>`
- Instagram: `https://SEU_DOMINIO/api/webhook/instagram/<channel_id>`

`SEU_DOMINIO` = seu `public_base_url` (o WhatsBot precisa estar acessível por **HTTPS público** — a Meta só entrega webhook em HTTPS válido). Em dev local sem domínio, use um túnel (Cloudflare Tunnel / ngrok).

**Como funciona o handshake:** ao salvar o webhook no painel da Meta, ela faz um `GET` na Callback URL com `hub.verify_token`; o WhatsBot responde o `hub.challenge` **se** o `verify_token` bater (o core já faz isso). Depois, cada evento chega por `POST` **assinado** com `X-Hub-Signature-256` — por isso o `app_secret` tem que estar no canal.

---

### A1 — Facebook Messenger (Página do Facebook)

**Pré-requisitos:** uma **Página do Facebook** e ser **admin** dela.

**Passo a passo (painel Meta):**
1. No app (A0): **Add Product → Messenger → Set Up**.
2. **Token de acesso da Página:** na seção **Token Generation**, escolha a Página no dropdown → copie o **Page Access Token**. → no WhatsBot é o campo **`page_access_token`**. Anote também o **Page ID** → campo **`page_id`**.
   - ⚠️ Esse token do dropdown é **curto**. Pra um token durável (recomendado em produção): troque um user token curto por um **long-lived** (`GET /v25.0/oauth/access_token?grant_type=fb_exchange_token&...`) e então pegue o page token via `GET /me/accounts` — page token derivado de user token long-lived **não expira** por tempo. Melhor ainda p/ servidor: um **System User token** (Business Settings → System Users).
3. **Webhook:** em Messenger → **Configure Webhooks** → **Callback URL** = a URL do WhatsBot (A0.1) + **Verify Token** = o `verify_token` do canal → **Verify and Save**.
4. **Assinar os campos** (webhook fields): marque `messages`, `messaging_postbacks`, `message_deliveries`, `message_reads`, `message_echoes`, `messaging_handovers`, `standby`.
5. **Assinar a Página ao app:** clique **Add Subscriptions** na Página (ou o WhatsBot faz via botão "Assinar" na config do canal → `POST /v25.0/{PAGE_ID}/subscribed_apps`).

**No WhatsBot:** crie um canal **Facebook** e preencha `page_id`, `page_access_token`, `app_secret`, `verify_token`. Teste enviando uma DM à Página **de uma conta testadora**.

**Permissões p/ App Review (produção):** `pages_messaging`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `business_management`. Peça também a capability **Human Agent** se for responder fora das 24h.

**Janela de mensagens:** você responde livre por **24h** após a última mensagem do cliente. Fora disso, só com `HUMAN_AGENT` (resposta **humana**, até 7 dias). ⚠️ As tags antigas `CONFIRMED_EVENT_UPDATE`/`ACCOUNT_UPDATE`/`POST_PURCHASE_UPDATE` **morreram em 27/04/2026** (erro 100).

**SaaS (várias Páginas de clientes):** não use o dropdown por cliente — use **Facebook Login for Business**: o cliente autoriza, você chama `GET /me/accounts` e recebe um page token por Página. (Isso é a fase 2 do sub-plano 02; o MVP é colar token.)

---

### A2 — Instagram Direct (Instagram API with Instagram Login)

> **Use o caminho "Instagram Login"** (host `graph.instagram.com`), **não** o "Facebook Login"/Página. É o que a Meta recomenda e **não** exige Página do Facebook — basta uma **conta profissional (Business/Creator)** do Instagram.

**Pré-requisitos:**
- Conta do Instagram **profissional** (Business ou Creator).
- No app **IG**: **Configurações do Instagram → Mensagens → Ferramentas conectadas → ligar "Permitir acesso a mensagens"**. ⚠️ **Sem isso, o envio e a assinatura falham em silêncio.**

**Passo a passo (painel Meta):**
1. No app (A0): **Add Product → Instagram → API setup with Instagram login** (o produto "Instagram", caminho Instagram Login).
2. Anote **Instagram App ID** e **Instagram App Secret**. ⚠️ **Não são** o App ID/Secret do app do Facebook — são dois números diferentes dentro do mesmo app, e trocar um pelo outro devolve `Invalid platform app` no login.
3. **Webhook:** em Instagram → Webhooks → **Callback URL** = URL do canal IG no WhatsBot (A0.1) + **Verify Token** = `verify_token` do canal → salvar. ⚠️ É **um cadastro manual e único por app**, de propósito: a Meta guarda UMA `callback_url` por app+objeto, então registrar por API sobrescreveria a de qualquer outro sistema no mesmo app (foi assim que o webhook do Chatwoot foi derrubado em 2026-08). O painel mostra a URL pronta para copiar em **Plugins → Instagram → Configurar**.
4. **Assinar os campos** (object `instagram`): `messages`, `messaging_postbacks`, `message_reactions`, `messaging_seen`. ⚠️ Não peça `messaging_handover`, `standby`, `messaging_optins` nem `messaging_referral` sem Acesso Avançado — um campo barrado faz a Meta recusar a chamada INTEIRA com `(#200) … permissions`. `message_echoes` costuma ser barrado também. (Comentários: `comments`, `live_comments` — hoje o parser descarta, ver plano 121 · P4.)
5. **URI de redirect do OAuth:** em Instagram → *Configuração da API com login do Instagram* → **"URIs de redirecionamento do OAuth"**, cole a URI que o painel mostra em **Plugins → Instagram → Configurar**. Precisa bater **byte a byte** (a Meta compara a URI da autorização com a da troca do código).
6. **Conectar a conta:** no painel, **Plugins → Instagram → Configurar → Conectar com Instagram**. O login do Instagram abre, você autoriza, e o WhatsBot preenche sozinho `access_token`, `ig_id` e a expiração, e já assina a conta (`POST /v25.0/{IG_ID}/subscribed_apps`). Não há token para copiar e colar.

**No WhatsBot:** crie um canal **Instagram** na tela Canais preenchendo só **`app_id`** (Instagram App ID), **`app_secret`** (Instagram App Secret) e **`verify_token`**; depois clique em **Conectar com Instagram** na aba Configurar do plugin. Teste com uma DM **de uma conta testadora**.

**Permissões p/ App Review (produção):** `instagram_business_basic`, `instagram_business_manage_messages` (+ `instagram_business_manage_comments`, `instagram_business_content_publish` se for usar comentários/publicação). ⚠️ São os **novos** nomes (os antigos `business_basic`/`business_manage_messages` foram deprecados em **27/01/2025**).

**⚠️ Token de 60 dias:** o IG User token expira em 60 dias e o WhatsBot **renova automaticamente** — loop supervisionado que varre os canais a cada 6h e renova assim que faltarem **15 dias** (plano 121 · D4). A renovação não usa app secret nem interação humana. Se ela falhar, o painel avisa na hora (card do canal + opcionalmente um grupo do Telegram, configurável na aba Configurar do plugin). **Token que passa dos 60 dias morre em definitivo** — não existe como trocar um token vencido, só reconectar; é por isso que a margem é larga e o alerta é obrigatório.

**⚠️ Por que NÃO o caminho "Instagram via login do Facebook".** Ele existiu no plugin entre 2026-07-24 e 2026-08-14 e foi **revertido**: usa `instagram_manage_messages`, que exige Acesso Avançado, e sem ele a Meta simplesmente não entrega o webhook — *"Webhooks will only be sent if the person using your app has a role on the app"*. Na prática o administrador trocava DM normalmente e **nenhuma mensagem de terceiro chegava**, sem erro em lugar nenhum. Medido em 2026-08-14 em dois apps independentes; no mesmo minuto, o token de Instagram Login enxergava as MESMAS conversas. Ver [121-plano-instagram-login.md](121-plano-instagram-login.md).

**Janela:** igual ao Messenger — 24h + `HUMAN_AGENT` (7 dias, humano). Instagram **não** tem templates/HSM; a única alavanca fora das 24h é a tag humana.

---

### A3 — App Review (para ir ao ar com clientes reais)
Necessário pra **Advanced Access** nas permissões de mensagem. A submissão precisa de:
- **Caso de uso** claro por escrito.
- **Screencast end-to-end** mostrando o fluxo real: login/consentimento concedendo cada permissão **+** uma mensagem indo (app → Meta) e voltando (Meta → app). ⚠️ Em 2025-2026 **screenshots estáticos não são aceitos** — tem que ser vídeo.
- **Instruções passo-a-passo + credenciais de teste** pro revisor.
- **≥1 chamada de API bem-sucedida** com a permissão em Standard Access antes de pedir Advanced.
- **Business Verification** concluída.
- Integrações multi-tenant passam também pela verificação de **Tech Provider**.

**Dica:** o Chatwoot (open-source) tolera propositalmente os bots de teste da Meta (erros 9010/100 viram "contato desconhecido") pra não travar o review — o WhatsBot faz o mesmo (sub-plano 03).

---

<a name="parte-b"></a>
## Parte B — E-mail (Gmail e outros)

Há dois caminhos. **Comece pelo B1 (App Password)** — é o mais simples e **evita completamente** a verificação/CASA do Google.

### B1 — Gmail com **App Password** (recomendado, sem OAuth)
> Funciona pra Gmail pessoal e Workspace. Zero projeto no Google Cloud, zero tela de consentimento, zero CASA.
1. Ative a **Verificação em 2 etapas** na Conta Google (obrigatório p/ liberar senhas de app).
2. Vá em **Conta Google → Segurança → Senhas de app** → gere uma **senha de app de 16 dígitos** (escolha "Email"/"Outro").
3. No WhatsBot, crie um canal **E-mail** (modo senha) com:
   - `username` = seu e-mail; `password` = a senha de app de 16 dígitos.
   - IMAP: `imap.gmail.com` / `993` (SSL). SMTP: `smtp.gmail.com` / `587` (STARTTLS). (já vêm pré-preenchidos.)
4. O WhatsBot faz **poll IMAP** (busca e-mails novos periodicamente) e responde por **SMTP**, mantendo o threading (Re:/In-Reply-To).

**Observações:**
- O toggle "apps menos seguros" **não existe mais** (desde 2022 no pessoal; ~mai/2025 no Workspace) — mas **senhas de app continuam funcionando** com 2FA ligado.
- Um admin do Workspace pode **desligar senhas de app** na organização — nesse caso, use o B2 (OAuth) ou peça pro admin liberar.
- **Outros provedores** (IMAP genérico, cPanel, etc.): mesmo formulário, com host/porta do provedor e a senha normal ou de app.

### B2 — Gmail/Microsoft com **OAuth (XOAUTH2)** (opcional, mais robusto)
> Use se não puder usar senha de app, ou pra Microsoft 365 (que **exige** OAuth — o basic auth foi desligado; o SMTP-AUTH basic aposenta ~30/04/2026).

**⚠️ A pegadinha do Google (CASA):** qualquer escopo que **lê conteúdo** de e-mail (incluindo o `https://mail.google.com/` que o IMAP/SMTP precisa) é **RESTRITO**. Um app "em produção" com escopo restrito exige verificação do Google **+ CASA** (avaliação de segurança anual, ~US$500–8.000, semanas). Por isso **o WhatsBot NÃO embarca um app Google compartilhado** — **você** traz o seu projeto. Três formas de evitar a CASA:
- **(Melhor) Google Workspace + consent screen "Internal":** se o projeto pertence à sua organização Workspace, o consent é "Internal" → **sem verificação, sem CASA, sem limite de 100 usuários**. Ideal se você tem Workspace.
- **App Password (B1):** não tem app OAuth nenhum, então nada disso se aplica.
- **"Testing" (≤100 users):** sem verificação, MAS o **refresh token morre em 7 dias** → ruim p/ um bot sempre-ligado. Evite.

**Passos (Google, resumido):**
1. **console.cloud.google.com** → criar projeto → **Enable API** (Gmail API) → **OAuth consent screen** (marque **Internal** se tiver Workspace) → adicionar o escopo `https://mail.google.com/`.
2. Criar **OAuth Client ID** (tipo Web) com **redirect URI** = `https://SEU_DOMINIO/api/plugins/email/oauth/callback` (o WhatsBot mostra a URI exata).
3. No WhatsBot, canal E-mail modo **OAuth** → "Conectar Gmail" → autorizar → o WhatsBot guarda o `refresh_token` e renova o `access_token` sozinho.

**Microsoft 365 (Azure):**
1. **portal.azure.com** → Azure AD → App registrations → novo app (single-tenant, no seu tenant).
2. Permissões delegadas: `IMAP.AccessAsUser.All`, `SMTP.Send`, `offline_access`. Consentimento do admin do tenant, se pedido.
3. Garanta que o **SMTP AUTH** está habilitado na caixa (muitos tenants desligam por padrão).
4. `imap`: `outlook.office365.com:993` · `smtp`: `smtp.office365.com:587`. ⚠️ O usuário do login tem que ser o **UPN** (não um alias).

### B3 — (avançado) Inbound-parse
Se você roda um MTA/relay (Postal, Mailu, Amazon SES, Mailgun, SendGrid, Postmark), pode receber e-mail por **webhook** em vez de poll: aponte o **MX**/route do domínio pro provedor e o endpoint pro WhatsBot (`/api/webhook/email/<channel_id>`). Latência menor, mas exige domínio + DNS + host público. (Sub-plano 04, fase opcional.)

---

<a name="parte-c"></a>
## Parte C — Widget de site

O widget é uma caixinha de chat que você cola em qualquer site. Não precisa de conta em provedor nenhum — só do WhatsBot acessível por HTTPS público.

### C1 — Criar e instalar
1. No WhatsBot, crie um canal **Site (Widget)**. Ele gera um **`widget_token`** (público) automaticamente e mostra o **snippet de instalação**.
2. Cole o snippet antes de `</body>` no seu site:
   ```html
   <script>(function(d,t){var g=d.createElement(t);g.src="https://SEU_DOMINIO/plugins/website/static/sdk.js";
   g.async=true;d.body.appendChild(g);g.onload=function(){window.WhatsBotChat.run({widgetToken:'SEU_WIDGET_TOKEN',baseUrl:'https://SEU_DOMINIO'})}})(document,'script')</script>
   ```
3. Em **Domínios permitidos** (`allowed_domains`) do canal, liste os sites onde o widget pode aparecer (ex.: `www.seusite.com`). Isso bloqueia o widget em domínios que copiarem seu token.

### C2 — Personalização
Na tela de configuração do canal: título/subtítulo de boas-vindas, cor, **formulário pré-chat** (pedir nome/e-mail antes de iniciar), habilitar **upload de arquivo**.

### C3 — Identificar usuários logados (HMAC — opcional)
Se o seu site tem login e você quer reconhecer o usuário (histórico entre dispositivos), use a validação HMAC:
1. Copie o **`hmac_token`** (secreto) do canal.
2. No **seu backend** (nunca no navegador), calcule `identifier_hash = HMAC_SHA256(identifier, hmac_token)` e passe pro widget via `setUser(identifier, { identifier_hash, name, email })`.
   - Python: `hmac.new(hmac_token.encode(), identifier.encode(), hashlib.sha256).hexdigest()`
   - Node: `crypto.createHmac('sha256', hmac_token).update(identifier).digest('hex')`
3. Ligue **"Exigir validação de identidade"** (`hmac_mandatory`) pra recusar visitantes sem hash válido.

⚠️ **Segurança:** o `widget_token` é **público** (fica no HTML) — ele só seleciona o canal, não autoriza nada sensível. O `hmac_token` é **secreto** e só vive no seu backend.

---

<a name="apendice"></a>
## Apêndice — checklists rápidos

**Messenger** — [ ] App Business criado · [ ] App Secret anotado · [ ] Produto Messenger · [ ] Page token (de preferência System User) · [ ] Webhook (Callback URL + Verify Token) · [ ] Campos assinados · [ ] Página assinada · [ ] canal WhatsBot preenchido · [ ] (produção) Business Verification + App Review + Human Agent.

**Instagram** — [ ] Conta IG profissional · [ ] "Permitir acesso a mensagens" **ligado** · [ ] Produto Instagram (Instagram Login) · [ ] IG App Secret · [ ] Webhook + campos · [ ] IG User token + IG_ID · [ ] conta assinada · [ ] canal WhatsBot preenchido · [ ] (produção) Business Verification + App Review com escopos `instagram_business_*`.

**E-mail (Gmail rápido)** — [ ] 2FA ligado · [ ] senha de app de 16 dígitos · [ ] canal E-mail com IMAP `imap.gmail.com:993` + SMTP `smtp.gmail.com:587` · [ ] teste enviando um e-mail pra caixa.

**Widget** — [ ] canal criado · [ ] snippet colado no site · [ ] domínios permitidos preenchidos · [ ] (opcional) HMAC configurado no backend.

---

### Prazos/pré-requisitos que travam go-live (não deixe pra última hora)
| Provedor | Item | Prazo típico |
|----------|------|--------------|
| Meta (IG/Messenger) | Business Verification | dias |
| Meta | App Review (Advanced Access) c/ screencast | dias a semanas |
| Google (OAuth restrito, se NÃO usar Workspace Internal/App Password) | Verificação + CASA | semanas a meses + custo |
| Todos os canais Meta/Widget/inbound-parse | Domínio HTTPS público (`public_base_url`) | imediato (mas obrigatório) |
