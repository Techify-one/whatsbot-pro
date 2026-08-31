# Plano 151 — Envio de mídia pela fachada `/api/v1`: imagem, áudio, documento, vídeo — e imagem COMO documento

> **Status:** ✅ EXECUTADO (2026-08-31) · **Data:** 2026-08-31 · **Escopo:** médio — um refactor habilitador no core (`R-media`), duas rotas novas na v1, um guard de rede novo, zero migração, zero mudança de UI
> **Origem:** pedido do operador — "quero enviar imagem normal no WhatsApp, áudio e também arquivos normais (e imagens como arquivos)" via API, para integrações externas (o caso concreto: um Cloudflare Worker que gera o certificado do aluno em PDF e o entrega no WhatsApp dele).
> **Método:** leitura do código real com `arquivo:linha` conferido em `server/routes/contacts.py`, `server/routes/v1/`, `app/services/messaging_service.py`, `channels/` e `server/upload_*.py`. Nada de memória.
>
> **O achado que define a forma do plano:** enviar mídia por API **já funciona hoje** — as quatro rotas do painel (`send-image`/`send-audio`/`send-document`/`send-video`) aceitam `X-Api-Key`, e `kind="document"` com um `.png` já vai como documento sem recompressão. O que **não** existe é isso na superfície **versionada**, e o que existe está **duplicado quatro vezes**: cada rota repete sandbox → canal → takeover → wire → janela de 24h → gravar → validar → transcodificar → mapear os seis parâmetros por-kind. Este plano não inaugura capacidade: ele **termina o refactor R14/R-txt** que o plano 131 deixou pela metade e expõe o resultado na v1.
>
> **Resultado final (2026-08-31):** as 7 fases executadas. Suíte completa do core rodada DUAS vezes — antes da F5 e depois de tudo — com **o mesmo conjunto de 4 falhas**, todas pré-existentes e provadas alheias: `test_alembic_hygiene` ×2 (merge `0058_merge_p50_p57` + prefixos duplicados; este plano não cria migração), `test_audit_matrix_is_complete` (deriva em eventos de canal/plugin) e `test_f4_inbound_save_failure_leaves_a_trace` (flake de ordem, **passa isolado**). Novos: **83 testes** (12 caracterização + 25 SSRF + 17 tetos + 29 v1). `contacts.py` encolheu de **2356 para 2172** linhas. Sem migração, sem bump de API de plugin, sem mudança de UI. **Validado também numa instância viva pelo domínio real** — chave de API emitida pelo endpoint real, entrega chegando a um aparelho, em DOIS providers (Telegram e GOWA/WhatsApp) com a mesma rota e o mesmo `curl`: §10.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela **ANTES** de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** | A rota nova **NÃO reimplementa nada**. Ela chama `MessagingService.send_media` ([messaging_service.py:426](../app/services/messaging_service.py#L426)), como as quatro do painel. | Precedente literal: o §"⚠️ `MessagingService.send_text` — por que a extração NÃO era opcional" de [docs/API_REST.md](../docs/API_REST.md). Uma segunda implementação mandaria para o JID errado, fora da janela, sem calar a IA — e nada disso apareceria como erro. |
| **D2** | O `kind` é **EXPLÍCITO no corpo, jamais inferido do MIME**. | É a decisão que entrega o pedido "imagem como arquivo". Inferir do `content_type` faria exatamente o contrário do que o operador pediu. Precedente vivo no frontend: `classifyFile(file, sendMode)` ([mediaQueue.js:23](../web/static/js/services/mediaQueue.js#L23)) já é dirigido pelo **gesto** (zona "Foto ou vídeo" × zona "Arquivo"), não pelo tipo do arquivo. A API ganha o mesmo contrato que a UI já tem. |
| **D3** | O upload **multipart é o caminho primário**; URL e base64 são secundários. | Para o caso que originou o pedido (Worker gerando PDF em memória), `FormData` + `fetch` é nativo, sem infraestrutura extra. URL exige o arquivo publicamente alcançável; base64 infla o corpo em ~33%. Ambos existem para quem **já tem** o arquivo num endereço (CRM, Windmill), não como caminho de primeira escolha. |
| **D4** | **Nenhuma permissão nova.** Mídia gateia em `conversation.reply`, igual ao texto. | D5 do plano 131 proíbe catálogo novo na v1. Quem pode responder pelo painel pode responder por chave. |
| **D5** | O comportamento das quatro rotas do painel **não muda**. | O refactor é de extração pura. Caracterização **antes** (F1), e as rotas passam a delegar mantendo o `_err`/`_ok` que já devolviam. |
| **D6** | Sem bump de `WHATSBOT_API_VERSION`. | `MessagingService` não está na superfície versionada — só `Channel.send_media` está no golden (`tests/goldens/plugin_api_surface.json`), e a interface do provider **não muda**. Confirmar rodando `tests/contracts/test_plugin_api_surface.py` na F6. |

---

## 1 — Resumo executivo

Quatro rotas de mídia do painel repetem, cada uma, a mesma sequência de nove passos; só os últimos três (`send → persist → broadcast → emit`) foram unificados no R14. Os seis passos de **preparo** (gravar em disco, validar limites do canal, transcodificar áudio/vídeo, e mapear os seis parâmetros que variam por `kind`) continuam copiados. A fachada `/api/v1` não tem mídia nenhuma — só texto.

Este plano: (a) sobe o preparo para o serviço como `MessagingService.send_media_upload` (**R-media**), (b) faz as quatro rotas do painel delegarem sem mudar comportamento, (c) publica **duas rotas novas** na v1 — multipart e link/base64 — que chamam a mesma função, (d) fecha o buraco do guard de upload de 50 MB, que hoje não cobre caminho novo, e (e) documenta em [docs/API_REST.md](../docs/API_REST.md).

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 As quatro rotas do painel e a duplicação medida

| Rota | Linha | Campo | Preparo próprio |
|---|---|---|---|
| `POST /api/contacts/{phone}/send-image` | [contacts.py:1840](../server/routes/contacts.py#L1840) | `image` | limites → grava |
| `POST /api/contacts/{phone}/send-audio` | [contacts.py:1896](../server/routes/contacts.py#L1896) | `audio` | limites **ou** `AudioLimits` → grava → `validate_audio` → `transcode_to_limits` → move |
| `POST /api/contacts/{phone}/send-document` | [contacts.py:1988](../server/routes/contacts.py#L1988) | `document` | limites → grava |
| `POST /api/contacts/{phone}/send-video` | [contacts.py:2050](../server/routes/contacts.py#L2050) | `video` | grava → `validate_video` → `transcode_to_limits` → move |

As quatro repetem, na mesma ordem: `permission_denied` → `_inbox_send_denied` → `_is_sandbox_contact` → `_channel_for` → `_operator_took_over` → `_wire_target` → `_session_window_block` → gravar/validar → `messaging.send_media(...)` → mapear erro.

**Sete dos nove passos já existem como função compartilhada; dois não:**

| Peça | Onde está hoje | Compartilhada? |
|---|---|---|
| `is_sandbox_contact` | [messaging_service.py:85](../app/services/messaging_service.py#L85) | ✅ (R-txt) |
| `resolve_channel_id` | [messaging_service.py:92](../app/services/messaging_service.py#L92) | ✅ |
| `wire_target` | [messaging_service.py:113](../app/services/messaging_service.py#L113) | ✅ |
| `resolve_inbox_id` | [messaging_service.py:141](../app/services/messaging_service.py#L141) | ✅ |
| `session_window_block` | [messaging_service.py:161](../app/services/messaging_service.py#L161) | ✅ |
| `abort_ai_cycle` | [messaging_service.py:1932](../app/services/messaging_service.py#L1932) | ✅ |
| `_media_limits_block` | **closure** em [contacts.py:251](../server/routes/contacts.py#L251) | ❌ — casca fina sobre `media_limits.validate_upload` ([media_limits.py:85](../channels/media_limits.py#L85)) que devolve `JSONResponse`, forma que a v1 não consegue mapear |
| `_operator_took_over` | **closure** em [contacts.py:285](../server/routes/contacts.py#L285) | ❌ — casca sobre `abort_ai_cycle`, mas fechada sobre `deps` |

### 2.2 A tabela por-`kind` — o coração do que está duplicado

Cada rota monta **seis** parâmetros diferentes para a mesma chamada de `send_media`. Extraí a matriz do código real:

| `kind` | `content` (persistido/bolha) | `emit_text` | `caption` p/ o canal | `filename` | `transcribe` | `error_label` | `default_ext` |
|---|---|---|---|---|---|---|---|
| `image` | `caption` | `caption` | `caption` | — | ✅ | "imagem" | `.png` |
| `audio` | `"[Áudio]"` | `""` | **não manda** | — | ✅ | "áudio" | `.ogg` |
| `document` | `"[Documento enviado: {nome}]"` (+ `\n{caption}`) | `caption` | `caption` | `safe_name` | ❌ | "documento" | `.bin` |
| `video` | `caption or "[Vídeo]"` | `caption` | `caption` | — | ❌ | "vídeo" | `.mp4` |

⚠️ Essa tabela é a razão de o preparo não poder virar "um `if kind ==` na rota nova": ela **é** a regra, e uma segunda cópia dela divergiria em silêncio — foi exatamente o que aconteceu com as duas cópias de `send_template` até o plano 119.

### 2.3 O que `send_media` já faz (e que não pode ser recriado)

[messaging_service.py:426-563](../app/services/messaging_service.py#L426-L563): envio pelo `wire` real (ghost-send do 9º dígito), `state.processed_messages` com prefixo de canal (dedupe de eco), `filter.conversation.before_reopen`, `contact.add_message`, broadcast `new_message`, `emit message.sent` (`source="operator"`), e a cauda de transcrição gateada **pelo canal** (`self.maybe_transcribe`, plano 118 B1).

⚠️ **`send_media` devolve `{"ok", "msg_id", "media_path"}` e NÃO devolve `conversation_id`** ([:563](../app/services/messaging_service.py#L563)) — apesar de tê-lo em mãos (`_saved`, usado no emit da linha 520). A v1 precisa dele no DTO. É acréscimo aditivo; o painel ignora.

### 2.4 O guard de 50 MB é por LISTA DE CAMINHOS

[upload_limits.py:27](../server/upload_limits.py#L27): `_UPLOAD_PATH_RE` casa os caminhos de upload **por regex ancorada no sufixo da rota**, e o middleware `upload_size_limit` ([app.py:733](../server/app.py#L733)) recusa antes de ler o corpo na RAM.

⚠️ **Uma rota nova de upload que não entre nessa regex não tem teto nenhum** — o corpo inteiro vai para a memória do processo. É o achado mais importante desta investigação e vira item próprio (F4·I2).

### 2.5 O caminho do GOWA — por que "imagem como documento" funciona

[gowa_channel.py:338-359](../channels/providers/gowa_channel.py#L338-L359) despacha **por `kind`, nunca pelo MIME**: `image`→`/send/image`, `audio`→`/send/audio`, `video`→`/send/video`, **qualquer outra coisa**→`send_file`→`/send/file` ([client.py:493](../gowa/client.py#L493)), que é `documentMessage` no protocolo — sem recompressão.

Dois detalhes que a rota nova precisa preservar:
- O MIME enviado ao GOWA sai de `mimetypes.guess_type(send_name)` sobre **o nome que o destinatário vê** ([client.py:505](../gowa/client.py#L505)), não do conteúdo. Sem extensão no `filename` → `application/octet-stream`.
- O nome **em disco** é reescrito por `unique_media_name` ([upload_names.py:76](../server/upload_names.py#L76)) a partir do MIME **validado**, neutralizando extensão executável no navegador ([upload_names.py:29](../server/upload_names.py#L29), XSS armazenado). O `filename` original viaja separado.

### 2.6 O que a v1 já tem e reaproveita de graça

- `_resolve_target` ([v1/messages.py:34](../server/routes/v1/messages.py#L34)) — `conversation_id` → `channel_id` → conversa única aberta → **409 `ambiguous_target`**. A rota de mídia usa a MESMA função; nada novo.
- `require("conversation.reply")`, `V1Error`, `visible_inboxes` ([v1/_common.py](../server/routes/v1/_common.py)).
- `message_dto` ([v1/_common.py:150](../server/routes/v1/_common.py#L150)) já expõe `media_type`/`media_path`/`media_caption`.
- O `openapi.json` é gerado das assinaturas ([v1/__init__.py:36](../server/routes/v1/__init__.py#L36)) — rota nova entra sozinha no schema.

### 2.7 Canal sempre-aberto × canal com janela

`ChannelCapabilities` do GOWA ([gowa_channel.py:92](../channels/providers/gowa_channel.py#L92)) não declara `session_window_hours` ⇒ default `0` ⇒ `session_window_block` devolve `None` sempre ([messaging_service.py:181](../app/services/messaging_service.py#L181)). Num canal Meta, a mesma função bloqueia com **409 `session_window_closed`**. A rota nova herda os dois comportamentos sem saber o nome de provider nenhum.

---

## 3 — O desenho

```
                    ┌──────────────────────────────────────┐
  painel (4 rotas)  │                                      │
  ──────────────────┤  MessagingService.send_media_upload  │   ← R-media (F2)
  v1 multipart      │  (grava · valida · transcodifica ·   │
  ──────────────────┤   mapeia a tabela por-kind)          │
  v1 link/base64    │                                      │
  ──────────────────┘                 │                    │
        ▲                             ▼                    │
        │                   MessagingService.send_media    │   ← já existe (R14)
   fetch_remote_media                 │                    │
   (SSRF-safe, F3)     send → persist → broadcast → emit   │
                                      └────────────────────┘
```

### 3.1 `MessagingService.send_media_upload` (novo — R-media)

Assinatura proposta (ilustrativa, não é implementação):

```python
async def send_media_upload(self, *, phone: str, kind: str,
                            data: bytes, filename: str | None,
                            content_type: str | None,
                            caption: str = "",
                            conversation_id=None, channel_id=None,
                            sent_by_user_id=None, sent_by_name=None,
                            inbox_guard=None) -> dict
```

Faz, nesta ordem — **a ordem é contrato**, copiada das rotas do painel:

1. `is_sandbox_contact(phone)`
2. `resolve_channel_id(phone, conversation_id, channel_id)`
3. `inbox_guard()` (callable, mesmo padrão de `send_text`; **depois** do desvio de sandbox)
4. `abort_ai_cycle` (o antigo `_operator_took_over`)
5. `wire_target(phone, conversation_id)`
6. `session_window_block(...)` — pulado quando sandbox
7. **preparo por kind** (§2.2 + limites/transcode) → `dest`
8. `self.send_media(...)` com os seis parâmetros da tabela
9. devolve `{"ok", "msg_id", "media_path", "conversation_id", "channel_id", "kind", "sandbox"}` ou `{"ok": False, "reason", "message", "status"}`

⚠️ **A ordem sandbox→inbox_guard é a mesma de `send_text`** e está travada por contrato lá ("um contato de sandbox nunca passou pelo gate de inbox"). Inverter muda o comportamento do painel.

### 3.2 As duas rotas novas

| Rota | Content-Type | Corpo |
|---|---|---|
| `POST /api/v1/messages/media` | `multipart/form-data` | `file` (arquivo) · `phone` · `kind` · `caption?` · `filename?` · `conversation_id?` · `channel_id?` |
| `POST /api/v1/messages/media/link` | `application/json` | `phone` · `kind` · `url` **ou** `content_base64` · `filename` · `caption?` · `conversation_id?` · `channel_id?` |

**Por que duas e não uma:** o FastAPI não declara uma rota que aceite `multipart` **e** `application/json` com parâmetros tipados; fazer dispatch manual por `Content-Type` sobre `Request` cru **quebra o `openapi.json`**, que é valor declarado da fachada ("pronto para codegen", [docs/API_REST.md](../docs/API_REST.md)). Duas rotas mantêm o schema honesto. Ver **P1**.

`kind` ∈ `image` · `audio` · `document` · `video`. Valor fora disso ⇒ **400 `invalid_kind`** (nunca cair no `else` do provider por acidente).

### 3.3 `fetch_remote_media` — o guard de rede (novo, F3)

A rota `/link` faz o servidor buscar uma URL escolhida pelo chamador. Isso é **SSRF por construção** e precisa de guard próprio; o precedente no repo é o `follow_redirects=False` do dispatcher de webhooks ([webhook_dispatcher.py:212](../server/webhook_dispatcher.py#L212)), que é bem menos do que basta aqui.

Regras obrigatórias, todas travadas por teste:

| # | Regra | Por quê |
|---|---|---|
| G1 | Só `http`/`https` | `file://`, `gopher://` leem disco/rede interna |
| G2 | `follow_redirects=False` | Redirect é o bypass clássico de allowlist de host |
| G3 | Resolver o host e **recusar** loopback, privado (RFC1918), link-local, CGNAT, IPv6 ULA e `169.254.169.254` | Sem isso, `conversation.reply` vira port-scanner da rede interna e leitor de metadata de cloud |
| G4 | Cap de tamanho **no streaming**, não no `Content-Length` | O header é declarado pelo servidor remoto; mentir nele é trivial |
| G5 | Timeout curto (10 s, o mesmo do dispatcher) | Uma URL que pendura prende um worker |
| G6 | Erro vira **400 `remote_fetch_failed`**, com a razão, nunca 500 | Um alvo inalcançável é entrada inválida, não bug do WhatsBot |

⚠️ **G3 não é opcional nem "depois".** Sem ele a rota é uma escalada: a chave de API vale para quem só tem `conversation.reply`, e um `POST` com `url: http://10.0.0.5:5432/` daria um oráculo de rede interna a partir da internet.

### 3.4 O que **não** muda

- As quatro rotas do painel: mesma URL, mesmos campos, mesmas respostas (D5).
- `send_media` (R14): a cauda continua idêntica, ganhando só `conversation_id` no retorno.
- A UI: nenhuma tela é tocada. A zona "Arquivo" já manda imagem como documento ([mediaQueue.js:23](../web/static/js/services/mediaQueue.js#L23)).
- O catálogo de eventos/filtros: `message.sent` já sai com `media_type`/`media_path`; nada de nome novo, logo **nenhum bump de API de plugin** (D6).
- Schema do banco: **zero migração**.

---

## 4 — Inventário das mudanças

| # | Item | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| **I1** | Caracterização das 4 rotas de mídia | `tests/core/characterization/` | Não existe teste que fixe o comportamento atual das 4 rotas antes do refactor | 🔴 alto se pulado | M |
| **I2** | `send_media` devolver `conversation_id` | [messaging_service.py:563](../app/services/messaging_service.py#L563) | Aditivo; `_saved` já está em mãos na linha 520 | baixo | S |
| **I3** | `media_limits_block` como **veredito de domínio** | [contacts.py:251](../server/routes/contacts.py#L251) → `messaging_service` | Hoje devolve `JSONResponse`; a v1 não mapeia isso. Mesmo tratamento que `session_window_block` recebeu no R-txt | baixo | S |
| **I4** | `MessagingService.send_media_upload` (R-media) | `app/services/messaging_service.py` | Não existe. Absorve §2.2 + limites + transcode de áudio/vídeo | médio | L |
| **I5** | As 4 rotas do painel delegam | [contacts.py:1840](../server/routes/contacts.py#L1840), [:1896](../server/routes/contacts.py#L1896), [:1988](../server/routes/contacts.py#L1988), [:2050](../server/routes/contacts.py#L2050) | Mantêm `permission_denied`, o `_err`/`_ok` e a dica de codec 131053 ([:2139](../server/routes/contacts.py#L2139)) | médio | M |
| **I6** | `fetch_remote_media` SSRF-safe | `app/services/` (módulo novo) | Não existe helper de download por URL no core — só `Channel.download_media` por plugin ([message_ingest_service.py:207](../app/services/message_ingest_service.py#L207)) | 🔴 alto | M |
| **I7** | `POST /api/v1/messages/media` (multipart) | `server/routes/v1/messages.py` | Reusa `_resolve_target` ([:34](../server/routes/v1/messages.py#L34)) e `require("conversation.reply")` | baixo | M |
| **I8** | `POST /api/v1/messages/media/link` (JSON) | idem | `url` **xor** `content_base64`; `filename` obrigatório (é ele que define o MIME no fio — §2.5) | médio | M |
| **I9** | `_UPLOAD_PATH_RE` cobrir a rota nova | [upload_limits.py:27](../server/upload_limits.py#L27) | **Sem isso a rota nova não tem teto de 50 MB** | 🔴 alto | S |
| **I10** | Cap de tamanho no caminho `/link` | `fetch_remote_media` + decode do base64 | O middleware de upload olha `Content-Length` de multipart; um base64 de 200 MB dentro de JSON **não passa por ele** | 🔴 alto | S |
| **I11** | Testes da v1 | `tests/integration/test_v1_media.py` (novo) | Hoje `test_v1_facade.py` tem 27 testes / 401 linhas, nenhum de mídia | baixo | M |
| **I12** | Documentação | [docs/API_REST.md](../docs/API_REST.md) + ≤2 linhas no `CLAUDE.md` | `CLAUDE.md` está em 76.538 chars; teto 90.000 ([test_docs_hygiene.py:40](../tests/contracts/test_docs_hygiene.py#L40)) | baixo | S |

### 4.1 Falsos positivos descartados

| Item | Por que parece problema | Por que NÃO é |
|---|---|---|
| "Precisa de permissão `message.send`" | Envio parece merecer chave própria | D5 do plano 131 proíbe catálogo novo na v1. Texto já gateia em `conversation.reply`; mídia é o mesmo ato. |
| "Precisa bump de `WHATSBOT_API_VERSION`" | Estamos acrescentando superfície | Só `Channel.send_media` está no golden, e a **interface do provider não muda**. `MessagingService` está fora da superfície versionada de propósito. Verificar na F6, não presumir. |
| "Precisa de migração" | Mídia nova no banco | `messages` já tem `media_type`/`media_path`/`media_caption`; `media_caption` desde `0063_msg_media_caption`. Nada novo. |
| "`send-document` re-roteia imagem para `/send/image`" | Seria o bug óbvio | Não existe: o dispatch é por `kind` puro ([gowa_channel.py:341](../channels/providers/gowa_channel.py#L341)), sem sniffing. Verificado. |
| "A UI precisa de um botão 'enviar como arquivo'" | Pedido menciona imagem como arquivo | **Já existe** — zona "Arquivo" do compositor, `sendMode: 'file'`, travado por teste em `mediaQueue.test.js:20`. |
| "Usar `get_open_for_contact` para achar a conversa pelo telefone" | É o resolvedor mais direto | É **channel-blind** e funde canais; barrado por `test_guardrail_no_new_channel_blind_resolvers` ([test_multichannel_routing.py:396](../tests/integration/test_multichannel_routing.py#L396)). Usar `_resolve_target`, que já existe. |
| "Fazer uma rota só, com dispatch por Content-Type" | Menos superfície | Quebra o `openapi.json`, que é valor declarado da fachada. Ver P1. |

---

## 5 — Fases e paralelização

```
WAVE 0   F1 (caracterização)  🔴  ─── barreira: nada de refactor sem rede
              │
WAVE 1   F2 (R-media: I2·I3·I4·I5)  🔴   ·   F3 (SSRF: I6)  🟢   ·   F4·I2 (guards: I9·I10)  🟢
              │                                    │                      │
              └──────────────── barreira: F2 entrega a função que F5 consome
                                                   │
WAVE 2   F5 (rotas v1: I7·I8)  🔴  [depende de: F2, F3, F4]
              │
WAVE 3   F6 (testes: I11)  🟢   ·   F7 (docs: I12)  🟢
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** | Caracterização | 🔴 | baixo | As 4 rotas do painel têm teste que fixa o comportamento atual — **verde antes de qualquer refactor** |
| 1 | **F2** | Backend / refactor | 🔴 | médio | `send_media_upload` existe; as 4 rotas delegam; F1 continua verde **sem editar os testes de F1** |
| 1 | **F3** | Backend / rede | 🟢 | alto | `fetch_remote_media` recusa G1–G6, cada uma com teste |
| 1 | **F4** | Backend / guards | 🟢 | alto | `_UPLOAD_PATH_RE` cobre a rota nova; base64 acima do teto é 413 |
| 2 | **F5** | Fachada v1 | 🔴 | médio | As duas rotas respondem; aparecem em `GET /api/v1/openapi.json` |
| 3 | **F6** | Testes | 🟢 | baixo | `tests/integration/test_v1_media.py` verde no Postgres |
| 3 | **F7** | Docs | 🟢 | baixo | `docs/API_REST.md` atualizado; `test_docs_hygiene.py` verde |

**F3 e F4 são 🟢 e podem sair junto com F2** — não tocam `messaging_service.py` nem `contacts.py`. **F2 é 🔴 e sozinha**: mexe nas quatro rotas de mídia do painel, que estão no caminho quente do atendimento.

---

### Fase 1 — Caracterização das quatro rotas (🔴 sozinha)

**Objetivo:** fixar o comportamento atual antes de mover uma linha.

**Itens** *(todos `[paralelo]` entre si — são testes independentes)*:
1. Caso feliz de cada kind: `msg_id` e `media_path` na resposta; linha em `messages` com o `media_type` certo e o `content` da tabela §2.2.
2. `document` com **imagem**: `media_type == "document"`, `content == "[Documento enviado: foto.png]"`, e o canal recebe `kind="document"` (fake provider — [tests/fake_provider.py](../tests/fake_provider.py)).
3. Bloqueio de limite de canal: 413/415 com `{"reason": ...}` e **nenhum arquivo órfão** em `statics/outbox/`.
4. Bloqueio de janela de 24h num canal com `session_window_hours > 0`: 409 **antes** de gravar o arquivo.
5. Sandbox: nada vai ao provider, a linha é salva local.
6. `_operator_took_over`: `state.ai_abort_epochs` incrementa a cada envio de mídia.
7. Vídeo com codec recusado: a dica do 131053 ([contacts.py:2139](../server/routes/contacts.py#L2139)) sai como 422 `bad_codec`.

**Pronto quando:** `venv/bin/python -m pytest tests/core/characterization -k media` verde, e cada teste falha se você comentar a linha que ele protege (prova de que ele protege algo).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-31)
- **O que foi feito:** novo `tests/integration/characterization/test_operator_media_characterization.py` — 12 testes cobrindo os 7 casos do plano: a tabela por-kind das 4 rotas (content/caption/filename), **imagem com `kind=document` saindo por `/send/file`**, bloqueio de limite (413 `too_big` / 415 `bad_format`) sem órfão em `statics/outbox/`, janela de 24h fechada (409, antes de gravar) **e o par que prova o contrário** (canal sempre-aberto nunca bloqueado), sandbox sem tocar o provedor, `ai_abort_epochs` incrementando por envio, e o 131053 → 422 `bad_codec`.
- **Como foi feito / decisões:** (a) **desvio do plano**: o arquivo foi para `tests/integration/characterization/`, não `tests/core/characterization/` — os testes precisam da app inteira + Postgres, e a pasta de `core` só tem unidades puras (agno/group_mentions); a de `integration` já tem 9 caracterizações do mesmo tipo. (b) Sem golden: asserção explícita, para cada linha do teste apontar a regra que protege. (c) Os bloqueios são caracterizados trocando a **capability declarada** (`patch.object(OutboundRouter, "capabilities", ...)`), nunca o provider — assim o próprio teste demonstra que o guard é dirigido por declaração. (d) `FakeGowaClient.sent` é a prova de "por qual endpoint foi" (`image` × `file`).
- **Problemas / pendências:** nenhuma. `video_limits` devolve `None` para o GOWA (sem `media_limits["video"]` e sem janela), então o caso feliz de vídeo não precisa de ffprobe nem de patch.
- **Verificação:** `venv/bin/python -m pytest tests/integration/characterization/test_operator_media_characterization.py -q` ⇒ **12 passed**. Prova de que a rede pega: três mutações aplicadas em `contacts.py` (documento despachado como `kind="image"`; `_media_limits_block` removido; `_operator_took_over` + guard de janela removidos) ⇒ **5 testes vermelhos**; revertido, verde de novo. Linha de base para F2: `contacts.py` = **2356** linhas, `messaging_service.py` = **2041**.

---

### Fase 2 — R-media: o preparo sobe para o serviço (🔴 sozinha) `[depende de: F1]`

**Objetivo:** uma função de serviço faz o preparo; as quatro rotas do painel delegam sem mudar de comportamento.

**Itens** *(`[sequencial]` — cada um depende do anterior)*:
1. **I2** — `send_media` passa a devolver `conversation_id` ([messaging_service.py:563](../app/services/messaging_service.py#L563)). Aditivo; nenhum call site atual quebra.
2. **I3** — `media_limits_block(outbound, channel_id, kind, filename, size) -> dict | None` sobe para `messaging_service` como **veredito** (`{"message", "reason", "status"}`), no molde exato de `session_window_block` ([:161](../app/services/messaging_service.py#L161)). A closure de [contacts.py:251](../server/routes/contacts.py#L251) vira atalho que embrulha no `_err`.
3. **I4** — `MessagingService.send_media_upload` com a sequência da §3.1 e a tabela da §2.2 como **um único dicionário de constantes por kind** (`_MEDIA_KIND_SPEC`), nunca um `if/elif` espalhado.
4. **I4b** — o preparo de **áudio** (`audio_limits` → `validate_audio` → `transcode_to_limits` → mover, [contacts.py:1930-1975](../server/routes/contacts.py#L1930)) e de **vídeo** ([contacts.py:2096-2126](../server/routes/contacts.py#L2096)) entram na função. ⚠️ Os dois têm ordens **diferentes** de propósito — áudio valida limites simples *antes* de gravar quando o canal **não** declara `AudioLimits`; vídeo sempre grava antes porque precisa do `ffprobe`. **Preservar as duas ordens**, não "harmonizar".
5. **I5** — as quatro rotas viram: gate de permissão → ler o `UploadFile` → `send_media_upload(...)` → mapear o dict de erro para `_err`. A dica de codec 131053 fica **na rota** (é formatação de mensagem, não regra).

**Pronto quando:** F1 verde **sem que nenhum teste de F1 tenha sido editado**. `wc -l server/routes/contacts.py` encolhe (medir antes/depois e registrar no bloco abaixo). Envio manual dos 4 tipos pelo painel funciona, inclusive imagem pela zona "Arquivo".

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-31)
- **O que foi feito:** em `app/services/messaging_service.py` — **I2** `send_media` passou a devolver `conversation_id` (aditivo, `_saved` já estava em mãos); **I3** `media_limits_block(outbound, channel_id, kind, filename, size) -> dict | None` como veredito de domínio, no molde exato de `session_window_block`; **I4** `MessagingService.send_media_upload` + `_prepare_media_file` + a constante `_MEDIA_KIND_SPEC` (a tabela por-kind num lugar só) e `MEDIA_KINDS`; helpers `_unlink`/`_replace_outbox_file`. Em `server/routes/contacts.py` — **I5** as quatro rotas viraram gate de permissão → `_send_media_upload` → `_media_error`/`_media_ok`, e a closure morta `_media_limits_block` (mais os imports `audio_validate`/`video_validate`) saiu.
- **Como foi feito / decisões:** (a) ⚠️ **o §3.1 do plano estava ERRADO sobre a ordem** e foi corrigido no código: as rotas de mídia chamam o gate de caixa **ANTES** do desvio de sandbox — o oposto de `send_text`. Não é detalhe: um contato de sandbox numa caixa alheia responde **403** na mídia e **200** no texto. D5 manda preservar, então `send_media_upload` chama `inbox_guard` como primeiro passo, com o ⚠️ escrito no docstring. (b) As rotas do painel passaram a usar `_inbox_guard_veredict` (dict) em vez de `_inbox_send_denied` (JSONResponse) — a resposta resultante é byte-idêntica e some mais um bloco duplicado. (c) A dica do 131053 ficou **na rota** de vídeo: é formatação de mensagem, não regra; o serviço devolve `provider_error` cru para ela inspecionar. (d) **As ordens de preparo de áudio e vídeo foram preservadas separadas**, com o porquê no docstring de `_prepare_media_file` (R4 do plano).
- **Problemas / pendências:** nenhuma.
- **Verificação:** as **12 caracterizações da F1 continuam verdes SEM TEREM SIDO EDITADAS**. Suítes vizinhas de mídia (transcrição, descrição, eco, legenda editada) verdes: 25 passed. Suíte completa do core rodada com `-p no:randomly`: **4 falhas, todas pré-existentes e provadas alheias** — `test_alembic_hygiene` ×2 (o merge `0058_merge_p50_p57` e prefixos duplicados; `git status db/alembic/` limpo, este plano não cria migração), `test_audit_matrix_is_complete` (deriva em eventos de **canal/plugin**) e `test_f4_inbound_save_failure_leaves_a_trace` (o flake de ordem já documentado — **passa isolado**). Medida do encolhimento: `contacts.py` **2356 → 2172** linhas (−261/+77 no diff).

---

### Fase 3 — `fetch_remote_media`: o guard de SSRF (🟢 paralela a F2 e F4)

**Objetivo:** buscar uma URL escolhida pelo chamador sem virar oráculo de rede interna.

**Itens** *(`[paralelo]`)*:
1. Módulo novo em `app/services/` com `async def fetch_remote_media(url, *, max_bytes, timeout=10.0) -> tuple[bytes, str | None]` (bytes + content-type observado).
2. Implementar G1–G6 da §3.3. A checagem de IP (G3) roda sobre o endereço **resolvido**, não sobre o texto do host — `http://localhost.meudominio.com` resolvendo para `127.0.0.1` tem de ser recusado.
3. Um teste por guard, com o motivo no nome (`test_recusa_ip_privado`, `test_recusa_redirect`, `test_corta_no_teto_mesmo_com_content_length_mentindo`).

**Pronto quando:** os seis guards têm teste verde e a função nunca levanta exceção não-tratada — só devolve erro de domínio.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-08-31)
- **O que foi feito:** novo `app/services/remote_media.py` — `fetch_remote_media(url, *, max_bytes, timeout=10.0) -> (bytes, content_type|None)`, `RemoteMediaError` (a ÚNICA exceção que a função levanta) e o vocabulário de motivos (`bad_scheme`/`blocked_host`/`too_big`/`unreachable`/`bad_status`). Testes em `tests/integration/test_remote_media_ssrf.py` — 25 casos, um por guard.
- **Como foi feito / decisões:** (a) **G3 ficou mais forte que o plano pedia**: além de checar o IP *resolvido*, a conexão é feita contra o IP **já aprovado**, com `Host:` original e `extensions={"sni_hostname": ...}` — fecha a janela de DNS rebinding entre a checagem e o connect, que sobraria se deixássemos o httpx resolver de novo. Verificado contra a internet real que o TLS continua verificando o certificado (`example.com` pelo IP ⇒ 200; com SNI errado ⇒ `ConnectError`). (b) G3 recusa se **qualquer** endereço resolvido for interno — aceitar "basta um ser público" é o bypass canônico (registro duplo). (c) Redirect não é só ignorado: vira erro explícito, senão o integrador passa horas achando que o arquivo é que está errado. (d) `_is_blocked_ip` desembrulha `ipv4_mapped` antes de julgar (`::ffff:127.0.0.1`).
- **Problemas / pendências:** o `MockTransport` dos testes curto-circuita a camada de conexão, então o IP-pinning em si não é exercido por teste automatizado — foi validado à mão contra a rede real (acima). Um teste que dependesse da internet seria pior.
- **Verificação:** `venv/bin/python -m pytest tests/integration/test_remote_media_ssrf.py -q` ⇒ **25 passed**.

---

### Fase 4 — Os dois tetos de tamanho (🟢 paralela a F2 e F3)

**Objetivo:** nenhum caminho novo carrega um corpo ilimitado para a RAM.

**Itens** *(`[paralelo]`)*:
1. **I9** — acrescentar `v1/messages/media` a `_UPLOAD_PATH_RE` ([upload_limits.py:27](../server/upload_limits.py#L27)). ⚠️ A regex é ancorada (`^...$`) e a rota da v1 **não** tem o prefixo `contacts/{phone}/` — não dá para "aproveitar" o grupo existente.
2. **I10** — o caminho `/link` não passa pelo middleware de upload (o corpo é JSON). O teto tem de ser aplicado **duas vezes**: no `max_bytes` de `fetch_remote_media` e no decode do `content_base64` (recusar **antes** de decodificar, pelo comprimento da string — base64 tem tamanho previsível).
3. Teste que prova o teto nos **três** caminhos: multipart, url, base64.

**Pronto quando:** um arquivo de 60 MB é recusado com 413 nos três caminhos, e o processo não cresce de memória durante a recusa.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-08-31)
- **O que foi feito:** `server/upload_limits.py` — `v1/messages/media` entrou em `_UPLOAD_PATH_RE` (grupo próprio, como o plano avisou: a v1 não tem o prefixo `contacts/{phone}/`), mais dois helpers: `too_large_message()` (a MESMA frase, sem o envelope `{ok:false}` que a v1 não pode devolver) e `base64_exceeds(encoded)`. Testes em `tests/core/test_upload_limits.py` (17 casos).
- **Como foi feito / decisões:** (a) `base64_exceeds` recebe a **string**, não o comprimento: com o comprimento cru a conta `(n*3)//4` superestima em até 2 bytes e a fronteira do JSON não batia com a do multipart — o integrador descobriria um limite diferente só por ter trocado a forma de envio. Com a string dá para descontar o padding e a fronteira fica exata. (b) O ⚠️ de "rota nova sem entrada aqui = sem teto nenhum" ficou como comentário **na própria regex**, que é onde alguém está olhando quando erra.
- **Problemas / pendências:** o teto ponta-a-ponta nos três caminhos (multipart/url/base64) vira teste de rota na F6 — aqui só existe a unidade. O caminho `url` já está coberto pelo G4 da F3.
- **Verificação:** `venv/bin/python -m pytest tests/core/test_upload_limits.py -q` ⇒ **17 passed**.

---

### Fase 5 — As duas rotas da v1 (🔴 sozinha) `[depende de: F2, F3, F4]`

**Objetivo:** publicar a superfície versionada.

**Itens** *(`[sequencial]`)*:
1. **I7** — `POST /api/v1/messages/media` (multipart) em [v1/messages.py](../server/routes/v1/messages.py), ao lado de `send_message` ([:107](../server/routes/v1/messages.py#L107)). Reusa `_resolve_target` (409 `ambiguous_target`) e o `_inbox_guard` que `send_message` já monta.
2. **I8** — `POST /api/v1/messages/media/link` (JSON), `url` **xor** `content_base64` (os dois juntos ⇒ 400 `conflicting_source`).
3. Validação de entrada, tudo 400 e nunca 500: `kind` fora do conjunto (`invalid_kind`); `filename` ausente no `/link` (`missing_field` — sem ele o MIME no fio degrada, §2.5); `caption` presente com `kind="audio"` (**400 `caption_not_supported`** — silenciar faria o integrador acreditar que a legenda saiu; ver **P2**).
4. Resposta: `{sent, msg_id, conversation_id, channel_id, kind, media_path, sandbox}`.
5. Docstrings ricas — elas **são** a documentação do `openapi.json` ([v1/__init__.py:36](../server/routes/v1/__init__.py#L36)).

**Pronto quando:** `curl -F file=@cert.pdf -F phone=... -F kind=document -H "X-Api-Key: ..."` entrega o PDF no WhatsApp; `GET /api/v1/openapi.json` lista as duas rotas com o corpo certo; uma chave sem `conversation.reply` toma 403.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-08-31)
- **O que foi feito:** em `server/routes/v1/messages.py` — `_send_media` (cauda comum: valida, resolve o alvo, monta o `inbox_guard`, delega, mapeia o veredito para o DTO da v1) e as duas rotas: **`POST /api/v1/messages/media`** (multipart) e **`POST /api/v1/messages/media/link`** (JSON, `url` XOR `content_base64`). Docstrings ricas — são elas que viram o `openapi.json`.
- **Como foi feito / decisões:** (a) **P1 resolvido como (a)**: duas rotas. (b) **P2 resolvido como (a)**: `caption` com `kind=audio` é **400 `caption_not_supported`**. (c) **P3 resolvido como (a)**: `/link` nasce ligada — G1–G6 entregues por inteiro na F3. (d) `phone`/`kind` são `Form("")` e não `Form(...)`: com o obrigatório do FastAPI, `kind=""` devolvia um **422 cru** em vez do 400 `invalid_kind` que lista os valores válidos — a validação tem de ser nossa para a mensagem servir. (e) O 413 do middleware de upload passou a falar o DTO da superfície (`too_large_response(path=...)`): é o ÚNICO ponto em que a v1 responde sem passar pelo handler de `V1Error`, e sem isso o integrador escreveria um caso especial só para o erro de tamanho.
- **Problemas / pendências:** nenhuma. **P4 segue ADIADO** (reagir/editar/apagar continuam fora da v1).
- **Verificação:** `curl` de ponta a ponta contra um **uvicorn real** (app hermética, banco de teste, GOWA falso) com `X-Api-Key` de um usuário que só tem `conversation.reply` + membresia de UMA caixa: PDF por multipart ⇒ 201; **PNG com `kind=document` ⇒ 201 e `msg_id=FAKE_SENT_FILE`** (foi por `/send/file`, não por `/send/image`); base64 ⇒ 201; URL pública real ⇒ 201 (e o nome em disco virou `.bin`, porque `text/html` é extensão perigosa — a defesa de XSS do plano 64 funcionando ponta a ponta); `http://10.0.0.5:5432/` e `169.254.169.254` ⇒ **400 `blocked_host`**, instantâneo; 60 MB por multipart e por base64 ⇒ **413 `too_big`** nos dois, com o DTO da v1; chave inválida ⇒ 401; legenda em áudio ⇒ 400.

---

### Fase 6 — Testes da fachada (🟢 paralela a F7)

**Objetivo:** o contrato da v1 fica travado.

**Itens** *(`[paralelo]`)*: novo `tests/integration/test_v1_media.py` com — cada kind entrega e persiste; **imagem com `kind=document` chega ao canal como `document`** (o teste que representa o pedido do operador); alvo ambíguo ⇒ 409; janela fechada ⇒ 409; `kind` inválido ⇒ 400; sem `conversation.reply` ⇒ 403; escopo de inbox alheia ⇒ 403; `url` + `content_base64` juntos ⇒ 400; URL privada ⇒ 400. Mais: rodar `tests/contracts/test_plugin_api_surface.py` para **confirmar D6** (nenhum bump).

**Pronto quando:** `venv/bin/python -m pytest tests/integration/test_v1_media.py tests/integration/test_v1_facade.py tests/contracts` verde no Postgres de teste.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-08-31)
- **O que foi feito:** novo `tests/integration/test_v1_media.py` — **29 testes**: a trava de que a v1 **delega** (`send_media_upload` mockado, `await_count == 1`); cada kind entrega e persiste; vídeo pelo endpoint próprio do canal; **`test_imagem_com_kind_document_chega_ao_canal_como_documento`** (o teste que representa o pedido); `filename` do corpo mandando no nome que o cliente vê; validação (`invalid_kind` ×4, `caption_not_supported`, `empty_file`, `conflicting_source`, `missing_field` ×2, `invalid_base64`); base64 feliz; SSRF (`blocked_host` ×2, `bad_scheme`); tetos nos três caminhos; `ambiguous_target` 409; janela 409; limite 413; RBAC 403 e escopo de caixa 403; o `openapi.json` listando as duas rotas com o `requestBody` certo.
- **Como foi feito / decisões:** o teste de vídeo ficou separado do parametrize dos outros kinds — o `FakeGowaClient` não define `send_video`, então ele cai no `__getattr__` (registra em `.calls`, não em `.sent`). Isso não é lacuna do fake: reflete que `/send/video` é endpoint próprio do GOWA, com degradação para documento num binário antigo. Asserção sobre `.calls`, com o porquê no docstring.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `venv/bin/python -m pytest tests/integration/test_v1_media.py -q` ⇒ **29 passed**. `tests/contracts` inteiro ⇒ **verde sem regenerar golden**, o que **CONFIRMA D6**: `MessagingService` está fora da superfície versionada e a interface do provider não mudou, logo **nenhum bump de `WHATSBOT_API_VERSION`**.

---

### Fase 7 — Documentação (🟢 paralela a F6)

**Objetivo:** o integrador acha isso sem ler código.

**Itens** *(`[paralelo]`)*:
1. **[docs/API_REST.md](../docs/API_REST.md)** — na seção "Fachada `/api/v1`", acrescentar `media` à tabela de módulos e uma subseção **"Envio de mídia"** com: as duas rotas, o conjunto de `kind`, **por que `kind` é explícito e não inferido do MIME** (D2, com o caso "imagem como documento" nomeado), a tabela por-kind da §2.2 resumida, os guards de SSRF, e o exemplo `curl` do certificado em PDF.
2. **`CLAUDE.md`** — **no máximo 2 linhas**, no bloco da API REST: a regra (`kind` explícito, nunca inferido do MIME) + o ⚠️ (rota de upload nova exige entrada em `_UPLOAD_PATH_RE`, senão não tem teto). Arquivo está em 76.538 chars, teto 90.000.
3. Verificar que `test_docs_hygiene.py` continua verde (ele prova que nada sumiu de `CLAUDE.md` ∪ `docs/`).

**Pronto quando:** `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py` verde e um leitor que nunca viu o repo consegue mandar um PDF só com o `docs/API_REST.md`.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-08-31)
- **O que foi feito:** `docs/API_REST.md` ganhou a seção **"Envio de mídia pela v1"** (as duas rotas, por que `kind` é explícito com o caso "imagem como documento" nomeado, a tabela por-kind, o papel do `filename` no MIME do fio, os seis guards de SSRF, os dois tetos, a tabela de códigos de erro, o exemplo `curl` do certificado, o link para o gotcha de persistência de `statics/` em [docs/OPERACAO.md](../docs/OPERACAO.md) e a explicação do refactor R-media). A linha de `v1/messages.py` na tabela de módulos e o índice "Onde as coisas ficam" foram atualizados. `CLAUDE.md` ganhou **duas** linhas: a regra (`kind` do chamador, nunca do MIME) e o ⚠️ (rota de upload nova exige entrada em `_UPLOAD_PATH_RE`; `content_base64` tem teto próprio).
- **Como foi feito / decisões:** o mecanismo, a história e os números ficaram no guia; no `CLAUDE.md` só a regra e o aviso, como manda a política do plano 139.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py -q` ⇒ **2 passed**. `CLAUDE.md` em **75.438 caracteres** (teto 90.000).

---

## 6 — Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | Rota `/link` sem G3 | Escalada: `conversation.reply` vira scanner da rede interna e leitor de metadata de cloud (`169.254.169.254`) | F3 é pré-requisito de F5, não "melhoria depois". Um teste por guard |
| R2 | `_UPLOAD_PATH_RE` esquecido | Rota nova sem teto ⇒ corpo de 500 MB na RAM ⇒ OOM do processo | I9 é item próprio, com teste nos três caminhos (F4) |
| R3 | Refactor da F2 mudar o painel em silêncio | Mídia do operador é caminho quente; regressão só aparece em produção | F1 **antes**, e a regra "F1 verde sem editar F1" |
| R4 | Harmonizar as ordens de preparo de áudio e vídeo | Elas são diferentes **de propósito** (§F2·I4b); unificar quebra o transcode de um dos dois | Comentário no código dizendo por quê + teste de cada um |
| R5 | `kind` inferido do MIME "por conveniência" | Mata o pedido central (imagem como documento) e o faz em silêncio | D2 travada; teste `test_imagem_com_kind_document_vai_como_documento` |
| R6 | `filename` sem extensão no `/link` | MIME degrada para `application/octet-stream` e o WhatsApp mostra anexo genérico (§2.5) | `filename` obrigatório no `/link` + nota no `docs/API_REST.md` |
| R7 | Duas implementações da tabela por-kind | Divergência silenciosa — precedente real: as duas cópias de `send_template` até o plano 119 | Um único `_MEDIA_KIND_SPEC` no serviço; a rota nunca decide |
| R8 | Usar `get_open_for_contact` para resolver o alvo | Manda a mídia pelo canal errado quando o número existe em duas caixas | Reusar `_resolve_target`; o guardrail [test_multichannel_routing.py:396](../tests/integration/test_multichannel_routing.py#L396) barra o resolvedor cego |
| R9 | Duas frentes no mesmo arquivo | `messaging_service.py` (2.041 linhas) está com WIP não-commitado nesta árvore (`git status`) | Conferir `git status` antes de começar a F2; não abrir a F2 junto de outra frente que toque o mesmo arquivo |
| R10 | Órfão em `statics/outbox/` | Falha depois de gravar deixa arquivo sem linha em `messages` | Preservar a ordem atual (validar antes de gravar onde já é assim) e o `unlink` no caminho de bloqueio do vídeo |
| R11 | `statics/` não persistente no deploy | A mídia enviada vira 404 depois do redeploy | Não é regressão deste plano, mas o `docs/API_REST.md` deve **linkar** o gotcha de [docs/OPERACAO.md](../docs/OPERACAO.md) na seção nova — quem integra por API é quem mais vai reparar |

---

## 7 — Perguntas em aberto

**P1 — Uma rota com dispatch por `Content-Type`, ou duas rotas?**
⏸️ **RECOMENDADO: (a) duas rotas.**
Contexto: o FastAPI não declara uma rota que aceite `multipart/form-data` e `application/json` com parâmetros tipados.
(a) `POST /media` (multipart) + `POST /media/link` (JSON) — o `openapi.json` fica honesto e o codegen funciona, ao custo de dois caminhos no docs.
(b) Uma rota lendo `Request` cru e despachando por header — superfície menor, mas o schema mente sobre o corpo, e "pronto para codegen" é valor **declarado** da fachada ([docs/API_REST.md](../docs/API_REST.md)).
**Recomendação: (a).** A v1 existe justamente para ser previsível para quem gera cliente a partir do schema.

**P2 — `caption` com `kind="audio"`: 400 ou ignorar?**
⏸️ **RECOMENDADO: 400 `caption_not_supported`.**
Contexto: `/send/audio` do GOWA é nota de voz (PTT) e não aceita legenda; a rota do painel hoje simplesmente não repassa.
(a) **400** — o integrador descobre na primeira chamada, não no relato do cliente.
(b) Ignorar em silêncio, como o painel faz — mas o painel **não tem** campo de legenda no áudio, então lá não há como o operador se enganar. Numa API, o campo existe e aceitar-e-descartar é a pior das opções.
**Recomendação: (a).**

**P3 — A rota `/link` nasce ligada ou atrás de chave de configuração?**
⏸️ **RECOMENDADO: ligada, com os guards obrigatórios.**
(a) Ligada + G1–G6 — o guard é a proteção, e uma chave desligada por padrão só empurra o integrador para o multipart (que já resolve o caso principal).
(b) Atrás de `v1_media_link_enabled` (default OFF) — mais conservador, mas cria configuração para um risco que o guard já cobre, e configuração desligada tende a virar "por que não funciona?" no suporte.
**Recomendação: (a)** — e, se a F3 não puder entregar G3 completo, então a rota `/link` **sai do escopo** desta entrega em vez de nascer com kill-switch.

**P4 — A v1 ganha também `PATCH`/reagir/apagar mídia?**
⏸️ **ADIADO.** Fora do escopo. Reagir/editar/apagar existem no painel e são outro domínio; misturar aqui alargaria a superfície versionada sem pedido.

---

## 8 — Apêndice — arquivos-chave

**Backend / serviço**
- [app/services/messaging_service.py](../app/services/messaging_service.py) — `send_media` (:426), retorno (:563), resolvedores (:85, :92, :113, :141, :161), `abort_ai_cycle` (:1932). **Ganha** `send_media_upload` e `media_limits_block`.
- `app/services/remote_media.py` — **novo** (`fetch_remote_media`, G1–G6).

**Backend / rotas**
- [server/routes/contacts.py](../server/routes/contacts.py) — 4 rotas (:1840, :1896, :1988, :2050) e as closures (:251, :285). **Encolhe.**
- [server/routes/v1/messages.py](../server/routes/v1/messages.py) — `_resolve_target` (:34), `send_message` (:107). **Ganha** as duas rotas de mídia.
- [server/routes/v1/_common.py](../server/routes/v1/_common.py) — `message_dto` (:150), `V1Error`, `require`.

**Backend / guards**
- [server/upload_limits.py](../server/upload_limits.py) — `MAX_UPLOAD_BYTES` (:22), `_UPLOAD_PATH_RE` (:27).
- [server/upload_names.py](../server/upload_names.py) — `extension_for` (:57), `unique_media_name` (:76).

**Canais (leitura — não muda)**
- [channels/media_limits.py](../channels/media_limits.py) (:85) · [channels/audio_validate.py](../channels/audio_validate.py) (:50) · [channels/video_validate.py](../channels/video_validate.py) (:66) · `audio_transcode.py` · `video_transcode.py`
- [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py) (:338 dispatch por kind, :92 capabilities) · [gowa/client.py](../gowa/client.py) (:416 `send_image`, :493 `send_file`)

**Testes**
- `tests/core/characterization/` — **novo** (F1)
- `tests/integration/test_v1_media.py` — **novo** (F6)
- [tests/integration/test_v1_facade.py](../tests/integration/test_v1_facade.py) · [tests/integration/test_multichannel_routing.py](../tests/integration/test_multichannel_routing.py) (:396 guardrail) · [tests/contracts/test_plugin_api_surface.py](../tests/contracts/test_plugin_api_surface.py) · [tests/contracts/test_docs_hygiene.py](../tests/contracts/test_docs_hygiene.py) (:40) · [tests/fake_provider.py](../tests/fake_provider.py)

**Docs**
- [docs/API_REST.md](../docs/API_REST.md) — seção nova · `CLAUDE.md` — ≤2 linhas

**Frontend** — nenhum arquivo. A zona "Arquivo" já entrega imagem como documento ([mediaQueue.js:23](../web/static/js/services/mediaQueue.js#L23)).

---

## 9 — Checklist de verificação

- [x] `venv/bin/python -m pytest tests/core/characterization` verde **antes** de começar a F2
- [x] Depois da F2, os testes de F1 continuam verdes **sem terem sido editados**
- [x] `venv/bin/python -m pytest` (core inteiro) verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome, **um pytest por vez**)
- [x] `venv/bin/python -m pytest tests/contracts` verde — em especial `test_plugin_api_surface.py` (confirma D6: sem bump) e `test_docs_hygiene.py`
- [x] `venv/bin/python -m pytest tests/integration/test_v1_media.py tests/integration/test_v1_facade.py` verde — 29 + 27 passed
- [x] `node --test web/static/js/services/mediaQueue.test.js` verde — **21 passed**, e o arquivo não foi tocado (`git status` limpo)
- [ ] ⚠️ **PENDENTE — não executado:** envio manual pelo painel dos 4 tipos, mais **imagem pela zona "Arquivo"**, com o modo escuro ligado. O caminho está coberto por 12 caracterizações automatizadas (que passam), mas ninguém clicou na tela; é a única verificação do checklist que exige um humano, já que este plano refatorou o caminho quente do atendimento (R3).
- [x] `GET /api/v1/openapi.json` lista as duas rotas com o corpo correto (travado por `test_as_duas_rotas_aparecem_no_openapi`)
- [x] `curl` de ponta a ponta: PDF por multipart e por `url`, com `X-Api-Key` de um usuário que só tem `conversation.reply` + membresia numa caixa — feito contra um uvicorn real sobre a app hermética
- [x] Chave sem `conversation.reply` ⇒ 403; chave escopada em outra caixa ⇒ 403
- [x] Arquivo de 60 MB recusado com 413 nos **três** caminhos (multipart, url, base64)
- [x] `url` apontando para IP privado / loopback / `169.254.169.254` ⇒ 400, e **nenhuma** conexão feita
- [x] Nenhum órfão em `statics/outbox/` depois dos testes de bloqueio (413/415/409)
- [x] Sem segredo em URL, em log ou no corpo de `message.sent`
- [x] Zero migração criada (confirmar `db/alembic/versions/` inalterado)
- [x] `git status` limpo de WIP alheio em `app/services/messaging_service.py` antes de abrir a F2 (R9)

---

## 10 — Validação numa instância viva, pelo domínio real (2026-08-31)

A verificação da §9 roda contra a app hermética (banco de teste, GOWA falso). Esta
seção registra a validação **complementar**: o mesmo código, exposto pelo domínio
público de uma instância de desenvolvimento (HTTPS atrás de proxy), com uma **chave
de API emitida pelo endpoint real** e entrega chegando a um aparelho de verdade.

### O que foi montado (padrão recomendado, não atalho de teste)

Um **usuário dedicado à integração** — `custom_permissions` com `conversation.reply`
+ `conversation.read` e nada mais, senha desabilitada, membro de **uma** caixa — e uma
chave emitida no nome dele. É exatamente a receita do §"Chave por usuário" de
[docs/API_REST.md](../docs/API_REST.md): como a chave **não tem escopo próprio**, quem
limita o alcance é a membresia de caixa do dono.

### Resultados

| # | O que foi enviado | Canal | Resultado |
|---|---|---|---|
| 1 | `kind=image` (PNG, multipart) | Telegram | **201** — saiu por `sendPhoto` |
| 2 | `kind=document`, mesmo PNG | Telegram | **201** — saiu por `sendDocument` com `disable_content_type_detection`, sem recompressão |
| 3 | `kind=image`, para uma conversa de **outra caixa** | — | **403 `inbox_forbidden`**, antes de qualquer envio |
| 4 | `kind=image` (mesmo PNG) | GOWA / WhatsApp | **201** — saiu por `/send/image` |
| 5 | `kind=document`, mesmo PNG, `filename` próprio | GOWA / WhatsApp | **201** — saiu por `/send/file` ⇒ `documentMessage`, qualidade original |

**O que cada linha prova, e que a suíte hermética não provava:**

- **1×2 e 4×5** — a promessa central do plano (D2) atravessa o fio real: o **mesmo
  arquivo**, mudando só o `kind`, sai por dois métodos diferentes do provedor. Nenhum
  código de canal foi tocado neste plano; quem escolhe o método é o provider.
- **1,2 × 4,5** — a **mesma rota, o mesmo `curl`**, dois providers distintos. Só o
  `conversation_id` mudou. É a resposta operacional a "a API funciona para todos os
  canais?": funciona para todo canal cujo `ChannelCapabilities.media` seja verdadeiro,
  porque o core não conhece nenhum deles por nome.
- **3** — o escopo por caixa da chave **não é decorativo**: é o `can_access_inbox` do
  painel, o mesmo que barra um atendente. A 4 só passou depois de a caixa ser
  concedida ao dono da chave.

⚠️ **Efeito colateral observado, e é comportamento correto:** no canal do teste 1 a
descrição de imagem estava ligada para mensagens **enviadas**, então a foto do
operador gerou uma linha `transcription` e **uma chamada paga de visão**. Não é da
rota nova — é `image_transcription_mode` do canal, que por decisão de projeto **não
depende da IA do canal estar ligada** (ver [docs/CANAIS.md](../docs/CANAIS.md)). Quem
for automatizar envio de imagem em volume deve conferir essa chave antes.

### Limpeza

O usuário e a chave criados para este teste são artefatos de uma instância de
desenvolvimento e **devem ser revogados** quando o teste terminar — chave de
integração viva sem integração do outro lado é superfície aberta de graça.
