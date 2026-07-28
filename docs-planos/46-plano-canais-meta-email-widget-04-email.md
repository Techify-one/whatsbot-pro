# Plano 46 · Sub-plano 04 — Canal E-mail (IMAP/SMTP + OAuth opcional)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** grande · **Mestre:** [00-mestre](46-plano-canais-meta-email-widget-00-mestre.md) · **Depende de:** nada (independente; usa só `ctx.spawn_task`) — pode começar já na Wave 0.
> **Método:** engenharia reversa do Chatwoot (`Channel::Email`, `Inboxes::FetchImapEmailsJob`, `Imap::ImapMailbox`, `ConversationReplyMailer`, `BaseRefreshOauthTokenService`) + survey OSS (FreeScout/Zammad) + pesquisa Gmail/Microsoft (verificado). **D5:** IMAP-poll default, App Password primeiro, OAuth opt-in. **D6:** threading por Message-ID/In-Reply-To/References.
> **Como usar:** preencha o "Status de execução" de cada fase antes da próxima.

## Objetivo
Plugin `email`: caixa de entrada de e-mail. Inbound = **loop IMAP** (molde `telegram/lifecycle.py`), outbound = **SMTP** (MIME com threading). Auth por **App Password** (padrão, sem OAuth) ou **XOAUTH2** (Gmail/Microsoft, opt-in). Inbound-parse (webhook) = 2º modo opcional. É o único canal que **não** é clone do whatsapp_cloud — precisa MIME parse/build + threading + cursor de poll.

## Decisão-chave: por que IMAP+App Password é o default (evitar CASA do Google)
- Qualquer escopo do Google que **lê conteúdo** (`gmail.readonly/modify`, e o `https://mail.google.com/` que o IMAP/SMTP XOAUTH2 exige) é **RESTRITO** → app "em produção" precisa de verificação + **CASA** (avaliação de segurança anual, ~US$500–8.000, semanas). **Não embarcar** um app Google único do WhatsBot com escopo restrito p/ todos os clientes.
- Escapes que cabem num self-host single-tenant: **(1) App Password + IMAP/SMTP** = zero OAuth, zero CASA (usuário liga 2FA e gera senha de app de 16 dígitos — ainda funciona em 2026); **(2)** Workspace com consent screen **"Internal"** = sem verificação; **(3)** projeto próprio em "Testing" (≤100 users, mas refresh token morre em 7 dias — ruim p/ bot).
- ⇒ MVP: App Password. OAuth/XOAUTH2 é upgrade cujo ônus é do **usuário** (projeto Google/Azure dele). Microsoft **exige** XOAUTH2 (basic auth desligado; SMTP-AUTH basic aposenta ~30/04/2026).

## Fatos pinados
| Item | Valor |
|------|-------|
| IMAP Gmail | `imap.gmail.com:993` SSL · SMTP `smtp.gmail.com:587` STARTTLS |
| IMAP Microsoft | `outlook.office365.com:993` · SMTP `smtp.office365.com:587` (XOAUTH2 obrigatório; user = UPN) |
| XOAUTH2 (SASL) | `base64("user=" + email + "\x01auth=Bearer " + access_token + "\x01\x01")` |
| Escopo Gmail p/ IMAP+SMTP | `https://mail.google.com/` (restrito) |
| Escopo Microsoft | `offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send` |
| Identidade contato | endereço remetente (From, lowercased) |
| Identidade canal (dedup) | `AccountIdentity(kind="email", value=<caixa lowercased>)` |
| Threading | `In-Reply-To`=Message-ID da última inbound; `References`=cadeia; Message-ID próprio determinístico; dedup por Message-ID |
| Quote strip | obrigatório (senão a IA vê o thread inteiro citado) — lib Python `email_reply_parser`/`talon` |
| Cursor de poll | persistir por-canal (UIDVALIDITY+UIDNEXT); **restart não pode reprocessar** (são pessoas reais) |
| Self-loop | filtrar `From==caixa` + carimbar header próprio; dropar `Auto-Submitted`/`List-Id`/`Precedence:bulk`/bounce |

## Capabilities / descriptor
```
ChannelCapabilities(qr=False, templates=False, groups=False, presence=False,
  reactions=False, media=True, inbound_route="poll",   # "path" no modo inbound-parse
  session_window_hours=0,   # e-mail não tem janela
  required_credentials=("username",))   # + imap/smtp hosts (têm default p/ Gmail)
```
- `provider="email"`, `label="E-mail"`, `color="gray"`.
- `credential_fields` (modo senha): `username`(text=e-mail), `password`(secret, help "Gmail: gere uma senha de app com 2FA ligado"), `imap_host`/`imap_port`/`smtp_host`/`smtp_port`(text, defaults Gmail pré-preenchidos). Modo OAuth: `form_component` "Conectar Gmail/Microsoft" + `routes.py` de callback.
- `config_fields`: `auth_mode`(select: password/xoauth2), `inbound_mode`(select: imap_poll/inbound_parse), `poll_interval`, `since_days`, `folder`(default INBOX).
- Config do provider (auth_mode, botão Conectar, status de subscription) mora na **screen `config:true` do plugin**, não no form de edição do core (regra CLAUDE.md).

## Tabelas novas (migrations `plugin_email_*`)
| Tabela | Colunas | Uso |
|--------|---------|-----|
| `plugin_email_cursor` | `channel_id`, `uidvalidity`, `uidnext`, `last_synced_at` | cursor de poll IMAP por-canal (restart não reprocessa) |
| `plugin_email_threads` | `conversation_id`, `channel_id`, `subject`, `last_inbound_message_id`, `references_chain` | estado de threading p/ montar reply (In-Reply-To/References) |
| `plugin_email_seen` | `channel_id`, `message_id`, `seen_at` | dedup de Message-ID (TTL) |

Reusar `messages.msg_id` (Message-ID) e `messages.reply_to_msg_id` (In-Reply-To) que já existem no core.

---

## Fase 04.1 — Loop IMAP inbound + App Password 🟢 [independente]
**Objetivo:** poll IMAP cria/roteia conversa a partir de e-mail recebido, com auth por senha/app-password.
**Itens:**
1. `[paralelo]` `lifecycle.py`: `setup(ctx)` registra `ctx.spawn_task("imap_poll", loop)` (molde `telegram/lifecycle.py:113-195`). Loop varre canais `email` (auth_mode=password ou xoauth2), conecta IMAP (`imaplib`/`imapclient`), `UID SEARCH SINCE <cursor>`/`UNSEEN`, `BODY.PEEK[]` (não marcar `\Seen`), dedup por Message-ID (`plugin_email_seen`), parse MIME (`email.parser`), monta `InboundEvent`, `ctx.ingest_event(ev)`. Persistir cursor (`plugin_email_cursor`) **antes** de avançar. Mutex por-canal p/ não duplicar (molde Chatwoot Redis mutex → aqui um lock em processo/DB).
2. `[paralelo]` `parse_inbound`/MIME→`InboundEvent`: `chat_id=sender_id=From.lower()`, `sender_name=From display`, `external_msg_id=Message-ID`, `reply_to_msg_id=In-Reply-To`, `text=`corpo **sem quote** (04.3), `media_*` de anexos MIME (decodificar base64/quoted-printable, salvar em `statics/media/`), `ts=Date`.
3. `[paralelo]` Threading de inbound: casar `In-Reply-To`/`References` contra `messages.msg_id` salvos → rotear ao thread existente; senão nova conversa. (Chatwoot: 4 estratégias; MVP = In-Reply-To → References → nova.)
4. `[paralelo]` Loop-guards: dropar `From==caixa` (self), `Auto-Submitted`/bounce/`List-Id`/`Precedence:bulk`.
5. `[paralelo]` `status()` = probe de login IMAP+SMTP. `identity_from_credentials` → `AccountIdentity("email", username.lower())`.
6. `[paralelo]` `plugin.yaml` (`entry: channels, lifecycle, routes, settings`; `migrations/`) + `static/email.js`.

**Pronto quando:** um e-mail novo na caixa vira conversa no painel em ≤ `poll_interval`; um **reply** do cliente cai na MESMA conversa (threading por In-Reply-To); restart **não** reprocessa e-mails já vistos; anexo aparece.

#### Status de execução — Fase 04.1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _(lib IMAP; onde mora o mutex)_ • **Pendências:** _()_ • **Verificação:** _(fixtures .eml: novo, reply, com anexo; cursor persiste)_

---

## Fase 04.2 — SMTP outbound + threading 🟢
**Objetivo:** resposta do operador/IA sai por SMTP como reply threadado.
**Itens:**
1. `[paralelo]` `send_text`/`send_media`: construir MIME (`email.message.EmailMessage`) com `From`=caixa, `To`=contato, `Subject`=`Re: <subject>` (de `plugin_email_threads`), `Message-ID` determinístico próprio, `In-Reply-To`=último Message-ID inbound, `References`=cadeia; corpo text/plain (+ html opcional); anexos via `add_attachment`. Enviar via `aiosmtplib`/`smtplib` (STARTTLS), auth LOGIN (app-password) ou XOAUTH2 (04.4). Persistir o `Message-ID` de saída em `messages.msg_id` p/ o reply do cliente threadar de volta (molde Chatwoot `send_on_email_service.rb:13`).
2. `[paralelo]` Atualizar `plugin_email_threads` (subject, last inbound Message-ID, References) a cada mensagem.

**Pronto quando:** resposta chega ao cliente como reply na mesma thread (cliente vê "Re:" agrupado); o reply do cliente volta pra mesma conversa.

#### Status de execução — Fase 04.2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _()_ • **Pendências:** _()_ • **Verificação:** _(headers In-Reply-To/References corretos; round-trip de thread)_

---

## Fase 04.3 — Quote/signature stripping 🟢
**Objetivo:** o corpo que vai pra IA/painel é só a resposta nova, sem o thread citado.
**Itens:** integrar `email_reply_parser` (ou `talon`) no parse (04.1) p/ extrair a parte "reply" e descartar "quoted". Guardar o corpo completo separado (p/ o painel), mandar só o "reply" como `text`/contexto da IA. É análogo ao `history_filter` do WhatsBot, mas na camada de parse. (Se usar inbound-parse Postmark, ele já dá `StrippedTextReply`.)

**Pronto quando:** um reply que cita 20 linhas antigas vira 1–2 linhas de conteúdo novo no contexto da IA; o painel ainda mostra o e-mail completo.

#### Status de execução — Fase 04.3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _()_ • **Decisões:** _(lib escolhida)_ • **Pendências:** _()_ • **Verificação:** _()_

---

## Fase 04.4 — (opcional) OAuth XOAUTH2 (Gmail/Microsoft) ⏸️
**Objetivo:** conectar Gmail/Microsoft por OAuth em vez de app-password (Microsoft praticamente exige).
**Itens:**
1. `routes.py`: endpoint "Conectar" → redirect OAuth (Google: `accounts.google.com/o/oauth2/v2/auth?access_type=offline&prompt=consent&scope=https://mail.google.com/`; Microsoft: `login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize` com IMAP.AccessAsUser.All+SMTP.Send+offline_access) → callback troca code por `refresh_token` (usa `public_base_url` p/ redirect_uri).
2. Refresh helper (molde Chatwoot `BaseRefreshOauthTokenService`): renova `access_token` quando <5min p/ expirar, persiste `{access_token, refresh_token, expires_on}` em `channel_credentials`; usado pelo loop IMAP **e** pelo send.
3. XOAUTH2 no IMAP e no SMTP (SASL, fórmula acima). Microsoft: user = UPN (`preferred_username`), não alias.
4. ⚠️ Guia deixa claro: o **usuário** cria o próprio projeto Google Cloud / registro Azure; Workspace "Internal" evita verificação. Não embarcar app Google restrito compartilhado.

#### Status de execução — Fase 04.4
**Estado:** ⬜ Não iniciada (adiada)

---

## Fase 04.5 — (opcional) Inbound-parse (webhook) ⏸️
**Objetivo:** 2º modo de inbound p/ quem roda MTA/relay (Postal/Mailu/SES/Mailgun/SendGrid/Postmark).
**Itens:** `inbound_mode=inbound_parse` → o relay POSTa o e-mail (raw MIME ou JSON) em `/api/webhook/email/{channel_id}` (rota do core, já isenta); `parse_inbound` recebe o corpo em vez do IMAP. `post_create.kind="webhook_url"` mostra o endereço/endpoint p/ apontar o MX/route. Latência menor que poll, mas exige host público + DNS/MX. Verificar assinatura do vendor (Mailgun HMAC / Postmark basic-auth / SES SNS).

#### Status de execução — Fase 04.5
**Estado:** ⬜ Não iniciada (adiada)

---

## Riscos específicos
| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Reprocessar backlog no restart | Re-responder e-mails velhos (pessoas reais!) | Cursor persistido em DB + dedup por Message-ID (≠ do telegram, que usa offset em memória). |
| Self-loop | O próprio Sent volta pela caixa/All-Mail | Filtrar `From==caixa` + header próprio; poll só INBOX. |
| Threading frágil | Cliente sem In-Reply-To → thread quebra | Fallback References; (fase 2) VERP `reply+<uuid>@` como Chatwoot. |
| CASA/verificação Google | Escopo restrito trava produção | Default App Password; OAuth é ônus do usuário; Workspace Internal evita. |
| Refresh token "Testing" 7d | Bot cai em 7 dias | Instruir "In production"/"Internal" no guia; App Password não tem isso. |
| Microsoft basic auth off | IMAP/SMTP senha não conecta | Microsoft = XOAUTH2 (04.4). |
| Charset/HTML | Corpo não-UTF8, só HTML | Decodificar pelo charset MIME; converter HTML→texto p/ a IA, guardar HTML p/ painel. |
| NUL bytes | Postgres rejeita `\x00` em text | Sanitizar (molde Chatwoot `mailbox_sanitizer`). |

## Perguntas em aberto
- **P-04.1:** IMAP IDLE (near-real-time) ou poll periódico? ✅ Poll no MVP (simples, robusto); IDLE como otimização depois.
- **P-04.2:** lib de quote-strip. ⏸️ `email_reply_parser` (MIT) como default; avaliar `talon`.
- **P-04.3:** VERP `reply+<uuid>@` precisa de domínio próprio + inbound-parse → só no modo 04.5. MVP threada por headers.

## Checklist
- [ ] Fixtures `.eml` (novo, reply, anexo, HTML-only, charset não-UTF8) → `InboundEvent` correto.
- [ ] Cursor persiste; restart não reprocessa; dedup por Message-ID.
- [ ] Reply SMTP com In-Reply-To/References; round-trip de thread.
- [ ] Quote strip: contexto da IA sem citação.
- [ ] Self-loop filtrado.
- [ ] Migrations `plugin_email_*` round-trip; prefixo correto.
- [ ] Restart de plugin cancela o loop IMAP.
- [ ] Modo escuro na config screen.
- [ ] Suíte `tests/` verde no Postgres.
