# Plano 65 — Envio de vídeo pelo painel (canal WhatsApp Cloud): caminho `type:"video"` + validar/bloquear ou converter

> **Status:** PLANEJAMENTO · **Data:** 2026-07-20 · **Escopo:** médio
> **Origem:** investigação nesta conversa — um `.mp4` enviado pelo painel ao canal WhatsApp Cloud (oficial) falha (bolha vermelha), enquanto no Telegram funciona. Causa-raiz confirmada em produção (DB `whatsbot@203.0.113.30`) + código + docs oficiais da Meta.
> **Método:** leitura do código real (`arquivo:linha` verificados), consulta ao DB de produção (read-only), e pesquisa na doc oficial da Cloud API.
> O painel só oferece **Imagem** e **Documento**. Um `.mp4` só cabe em *Documento* → é enviado à Cloud como `type:"document"` com mime `video/mp4`, que a Meta **recusa** (documento aceita só PDF/Office/TXT). Este plano cria o caminho de **vídeo de verdade** (`type:"video"`) e decide, para arquivos fora do padrão aceito, entre **BLOQUEAR com mensagem clara** ou **CONVERTER com ffmpeg**.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|-----------------------|
| D1 | ✅ (2026-07-20) O problema a resolver é envio de vídeo pelo **canal WhatsApp Cloud**; Telegram e GOWA já entregam `.mp4` como arquivo. | Foco no provider Cloud; Telegram/GOWA só precisam do `kind="video"` roteado (degradação segura garantida). |
| D2 | ✅ (2026-07-20) A janela de atendimento é de **24h** (não 14h — confusão do usuário). Dentro da janela = vídeo livre; fora = só template. | O caminho de vídeo reusa o gate `_session_window_block` existente. Template **com header de vídeo** (fora da janela) é feature maior → fica **fora de escopo** deste plano (ver P2). |
| D3 | ⏸️ EM ABERTO — **converter** (ffmpeg) vs **bloquear** (validar + avisar). É a decisão central deste plano. | Ver **P1**. As fases-base (1–4) são comuns às duas opções; a Fase 5 bifurca (5A bloquear · 5B converter). |

**Princípio fixo:** produção usa este banco/instância; nada de stopgap frágil. O caminho de vídeo deve ser **capability-driven** (nunca `if provider ==`), coerente com plano 11/33.

---

## 1. Resumo executivo

Hoje não existe envio de vídeo no painel — apenas *Imagem* e *Documento*. Um `.mp4` vai como `type:"document"`, que a Cloud API rejeita. A solução tem duas camadas:

1. **Camada base (obrigatória):** adicionar um caminho de **vídeo real** ponta-a-ponta — anexo "Vídeo" no painel → rota `POST /api/contacts/{phone}/send-video` → `messaging.send_media(kind="video")` → provider `send_media` (Cloud já monta `type:"video"`). Isso sozinho conserta o envio de qualquer `.mp4` bem-formado ≤16 MB.
2. **Camada de política (decisão D3):** para arquivos fora do padrão aceito pela Cloud (não-mp4/3gp, >16 MB, codec ≠ H.264/AAC, >1 faixa de áudio), escolher entre **(A) bloquear** com mensagem clara ("só é possível enviar vídeo em MP4 até 16 MB") ou **(B) converter** com ffmpeg para mp4 H.264/AAC ≤16 MB. Recomendação: **A como default**, com **B opcional e com degradação graciosa** (converte se ffmpeg existir; senão bloqueia). Ver P1.

---

## 2. Como funciona hoje (mapa)

### 2.1 Envio de mídia — cadeia completa

| Camada | Arquivo:linha | Papel |
|--------|---------------|-------|
| Frontend — menu anexo | [Composer.js:251-262](../web/static/js/components/contacts/Composer.js#L251-L262) | Só dois botões: 🖼️ Imagem, 📄 Documento |
| Frontend — inputs ocultos | [Composer.js:53-66](../web/static/js/components/contacts/Composer.js#L53-L66) | `fileInputRef` (`accept="image/*"`, L57) e `docInputRef` (sem `accept`) |
| Frontend — hook de mídia | [useMediaUpload.js:36](../web/static/js/components/contacts/hooks/useMediaUpload.js#L36), [:87-93](../web/static/js/components/contacts/hooks/useMediaUpload.js#L87-L93), [:119-242](../web/static/js/components/contacts/hooks/useMediaUpload.js#L119-L242) | `pendingMedia.type ∈ {image,audio,document}`; `confirmPendingMedia` monta a bolha otimista e chama `api.sendImage/sendAudio/sendDocument`. **Não há `video`.** |
| Frontend — API service | [api.js:360](../web/static/js/services/api.js#L360) `sendImage`, [:386](../web/static/js/services/api.js#L386) `sendDocument`, [:365](../web/static/js/services/api.js#L365) `sendAudio` | `uploadRequest` multipart p/ `/send-*`. **Não há `sendVideo`.** |
| Frontend — wiring efetivo | [ContactDetail.js:55-56](../web/static/js/components/contacts/ContactDetail.js#L55-L56) | `_api = { sendText, sendImage, sendAudio, sendDocument }` |
| Rota REST | [contacts.py:1706-1748](../server/routes/contacts.py#L1706-L1748) (image), [:1751-1793](../server/routes/contacts.py#L1751-L1793) (audio), [:1795-1849](../server/routes/contacts.py#L1795-L1849) (document) | Gate de janela (`_session_window_block`, def em [:190](../server/routes/contacts.py#L190)) → grava em `statics/outbox/` → `messaging.send_media(kind=...)` |
| Serviço | [messaging_service.py:187-260](../app/services/messaging_service.py#L187-L260) | Tail unificado (R14): send → persist (`media_type=kind`) → broadcast → emit `message.sent` |
| Router de saída | [outbound.py:91-99](../channels/outbound.py#L91-L99) | Repassa `kind` ao provider, **sem normalizar** |

### 2.2 O que cada provider faz com `kind="video"` (⚠️ chave da solução)

| Provider | Arquivo:linha | `kind="video"` hoje | `kind="document"` c/ mp4 |
|----------|---------------|---------------------|--------------------------|
| **WhatsApp Cloud** | [whatsapp_cloud/channels.py:384-388](../assets/plugin_examples/whatsapp_cloud/channels.py#L384-L388) | ✅ Monta `type:"video"` corretamente (já existe!) | ❌ `type:"document"` + `video/mp4` → **Meta recusa** (não é tipo de documento) |
| **GOWA** | [gowa_channel.py:302-314](../channels/providers/gowa_channel.py#L302-L314) | Cai no `else` → `send_file` (WhatsApp renderiza mp4) — funciona | igual (send_file) — funciona |
| **Telegram** | [telegram/channels.py:208-228](../assets/plugin_examples/telegram/channels.py#L208-L228), map [:433](../assets/plugin_examples/telegram/channels.py#L433) | `_MEDIA_METHOD["video"]` → `sendVideo` **(a confirmar se a chave `video` existe no map)**; se ausente, degrada p/ `sendDocument` — funciona | `sendDocument` — funciona |

**Conclusão:** o provider Cloud **já sabe** enviar `type:"video"`. Falta apenas **rotear `kind="video"` até ele** — o gap é do painel/rota, não do provider.

### 2.3 Como o painel conhece as capacidades do canal

O detalhe do contato traz flags que o frontend consome: `contact.templates_supported`, `contact.session_open`, `contact.revoke_supported`, `contact.edit_supported` ([ContactDetail.js:89-98](../web/static/js/components/contacts/ContactDetail.js#L89-L98)). Padrão capability-driven — é onde uma flag nova (ex.: limite de vídeo por canal) se encaixaria, se necessário.

### 2.4 Render do vídeo já existe

[MediaContent.js:65-68](../web/static/js/components/contacts/MediaContent.js#L65-L68) já renderiza `media_type === 'video'` com `<video controls>`. A bolha de saída de vídeo aparece sem trabalho extra de render.

### 2.5 ffmpeg (⚠️ decisivo para a opção "converter")

- **Dev:** `/usr/bin/ffmpeg` + `/usr/bin/ffprobe` presentes.
- **Produção (Docker/Coolify):** o [Dockerfile:14-16](../Dockerfile#L14-L16) instala **só `curl unzip`** — **não há ffmpeg**. Precedente: `_gif_to_mp4` ([whatsapp_cloud/channels.py:284-309](../assets/plugin_examples/whatsapp_cloud/channels.py#L284-L309)) já usa `shutil.which("ffmpeg")` e **degrada graciosamente** (sem ffmpeg → cai p/ documento). Ou seja, **hoje conversão de vídeo NÃO funcionaria em produção** sem alterar o Dockerfile.

### 2.6 Limites oficiais da Cloud API (referência para validação/conversão)

| Item | Regra Cloud API |
|------|-----------------|
| Formato de vídeo | `video/mp4` e `video/3gpp` (`.3gp`) apenas |
| Codec | H.264 (vídeo) + AAC (áudio) |
| Faixas de áudio | 1 faixa **ou nenhuma** ("single audio stream or no audio stream only") |
| Tamanho máx. vídeo | **16 MB** (upload e link) |
| Documento | 100 MB, mas tipos = PDF/DOC/PPT/XLS/TXT (mp4 **não** entra) |
| Erro típico | `131053` (media upload error) quando mime/codec não bate |

---

## 3. Inventário / análise das mudanças

| # | Item | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|------|---------------|-------------|-----------|-------|---------|
| I1 | Rota `POST /send-video` | novo em [contacts.py](../server/routes/contacts.py) (espelha [:1706-1748](../server/routes/contacts.py#L1706-L1748)) | inexistente | Copiar `send-image`, trocar `kind="video"`, `error_label="vídeo"`, validação de vídeo | baixo | S |
| I2 | Validação de vídeo (server) | novo helper em [contacts.py](../server/routes/contacts.py) ou módulo `channels/video_validate.py` | inexistente | Extensão mp4/3gp + tamanho ≤16 MB; opcional ffprobe p/ codec/áudio (degrada se ausente) | médio | M |
| I3 | API service `sendVideo` | [api.js:386](../web/static/js/services/api.js#L386) (após `sendDocument`) | inexistente | `uploadRequest('/send-video', {video, caption})` | baixo | S |
| I4 | Wiring `_api` | [ContactDetail.js:55-56](../web/static/js/components/contacts/ContactDetail.js#L55-L56) | falta `sendVideo` | Adicionar `sendVideo` ao objeto | baixo | S |
| I5 | Anexo "Vídeo" + input | [Composer.js:53-66](../web/static/js/components/contacts/Composer.js#L53-L66), [:251-262](../web/static/js/components/contacts/Composer.js#L251-L262) | sem opção vídeo | Novo `videoInputRef` (`accept="video/mp4,video/3gpp"`) + item de menu "🎬 Vídeo" | baixo | S |
| I6 | Hook: tipo `video` | [useMediaUpload.js:36](../web/static/js/components/contacts/hooks/useMediaUpload.js#L36), [:87-93](../web/static/js/components/contacts/hooks/useMediaUpload.js#L87-L93), [:169-179](../web/static/js/components/contacts/hooks/useMediaUpload.js#L169-L179) | sem branch vídeo | `pendingMedia.type='video'` + preview `<video>` + branch `api.sendVideo` + validação client (tamanho/extensão) | médio | M |
| I7 | Overlay de confirmação (preview de vídeo) | [Composer.js:69-125](../web/static/js/components/contacts/Composer.js#L69-L125) | só image/document/audio | Adicionar caso `type==='video'` com `<video>` | baixo | S |
| I8 | **[Opção A]** Mensagem de bloqueio | frontend (hook I6) + rota (I1/I2) | inexistente | Client barra e mostra aviso; server retorna 4xx com `error` claro | baixo | S |
| I9 | **[Opção B]** ffmpeg no Docker | [Dockerfile:14-16](../Dockerfile#L14-L16) | ffmpeg ausente | `apt-get install ffmpeg` (inclui ffprobe) | médio | S |
| I10 | **[Opção B]** Transcode ≤16 MB | novo em `channels/video_transcode.py` (padrão de `_gif_to_mp4`) | inexistente | ffprobe → decidir → ffmpeg H.264/AAC, target bitrate por duração p/ caber em 16 MB | **alto** | L |
| I11 | Teste de endpoint | [test_endpoints.py:907-911](../tests/test_endpoints.py#L907-L911) | sem cobertura vídeo | Adicionar `POST /send-video → 200` (mp4 válido) + caso bloqueado (413/415) | baixo | S |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o problema |
|----------|--------------------------|
| "Foi a janela de 24h" | ❌ O cliente mandou "oi" às 11:05/06/08 e o vídeo falhou ~11:38 — janela **aberta**. Imagem foi enviada com sucesso ao mesmo contato/canal às 09:58. É tipo de mídia, não janela. |
| "O provider Cloud não sabe mandar vídeo" | ❌ [whatsapp_cloud/channels.py:384-388](../assets/plugin_examples/whatsapp_cloud/channels.py#L384-L388) já monta `type:"video"`. Falta só rotear `kind="video"`. |
| "É bug do provider Telegram/GOWA" | ❌ Ambos entregam mp4 como arquivo (send_file/sendDocument). O plano só garante que `kind="video"` role até eles sem regressão. |
| "As linhas `media_type='video'` de 07-13 em prod provam que o painel manda vídeo" | ❌ Aquelas linhas têm `media_path=NULL` e `msg_id=NULL` → são **histórico importado (migração Chatwoot)**, não envio real via painel. Não servem de contraprova. |
| "Basta mudar o mime do upload de documento" | ❌ Mesmo com mime certo, `type:"document"` + `video/mp4` é recusado pela Meta. O correto é `type:"video"`. |

---

## 4. Fases / Roadmap

### Diagrama de dependências

```
WAVE 0   F1(backend: rota+validação) · F2(frontend: anexo+hook+api)      ← paralelos (contrato: /send-video + kind=video)
              │  (barreira: F1 e F2 precisam existir para o fluxo E2E)
WAVE 1   F3(integração E2E + testes)                                     ← depende de F1+F2
              │  (barreira: decisão D3/P1 escolhe o ramo)
WAVE 2   F5A(BLOQUEAR)  XOR  F5B(CONVERTER)                              ← escolher UM (P1)
WAVE 3   F6(regressão + verificação final)
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / Nota |
|------|------|-----------|-------|-------|----------------------|
| 0 | F1 | Backend: rota `/send-video` + validação | 🟢 | baixo | rota responde e roteia `kind="video"` |
| 0 | F2 | Frontend: anexo "Vídeo" + hook + `sendVideo` | 🟢 | médio | painel envia vídeo; bolha otimista renderiza |
| 1 | F3 | Integração E2E + testes | 🔴 | médio | `[depende de: F1,F2]` mp4 válido chega ao contato Cloud |
| 2 | F5A | **Opção A** — bloquear + mensagem | 🟢 (se P1=A) | baixo | arquivo fora do padrão → aviso claro, nada enviado |
| 2 | F5B | **Opção B** — converter (ffmpeg) | 🔴 (se P1=B) | alto | arquivo fora do padrão → transcodifica ≤16 MB e envia |
| 3 | F6 | Regressão + verificação | 🔴 | baixo | suíte verde; sem regressão em image/doc/audio |

> Nota: F5A e F5B são **mutuamente exclusivas** pela decisão P1. O recomendado (ver P1) é implementar **F5A como base** e deixar F5B como incremento opcional gated em `shutil.which("ffmpeg")` — nesse caso F5B **estende** F5A (converte quando dá, bloqueia quando não dá), e as duas coexistem.

---

### Fase F1 — Backend: rota `/send-video` + validação de vídeo

**Objetivo:** aceitar upload de vídeo, validar contra os limites da Cloud e roteá-lo como `kind="video"`.

**Itens:**
- [sequencial] Criar `POST /api/contacts/{phone}/send-video` em [contacts.py](../server/routes/contacts.py) espelhando `send-image` ([:1706-1748](../server/routes/contacts.py#L1706-L1748)): mesmas guardas (`permission_denied("conversation.reply")`, `_inbox_send_denied`, `_is_sandbox_contact`, `_channel_for`, `_session_window_block`), grava em `statics/outbox/`, chama `messaging.send_media(channel_id, phone, kind="video", dest, caption, emit_text=caption, error_label="vídeo", ...)`.
- [paralelo] Helper de validação `validate_video(dest, channel_id)` (novo `channels/video_validate.py` ou local): checa **extensão** (`.mp4`/`.3gp`), **tamanho** (≤16 MB — só p/ canais com `session_window_hours>0`/Cloud, ou aplicar a todos por simplicidade), e — **se ffprobe existir** — codec H.264/AAC e nº de faixas de áudio ≤1. Sem ffprobe, valida só extensão+tamanho (degrada, igual `_gif_to_mp4`). Retorna motivo estruturado (`too_big` / `bad_format` / `bad_codec` / `ok`).
- [sequencial] Na rota: se `validate_video` reprovar → **não grava/nao envia**, retorna `_err(...)` com `status` apropriado (413 tamanho / 415 formato) e `error` legível. (Este ramo é o gancho da Opção A; na Opção B, chama transcode antes de reprovar.)
- ⚠️ Reusar o gate de janela existente: fora da janela o `_session_window_block` já devolve o bloqueio que o front redireciona p/ template (comportamento atual mantido).

**Pronto quando:** `POST /send-video` com um `.mp4` H.264/AAC ≤16 MB retorna 200 e o vídeo chega ao contato Cloud; com `.mkv`/`>16 MB` retorna 4xx com mensagem clara e **nada** é enviado (sem bolha órfã).

#### Status de execução — Fase F1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F2 — Frontend: anexo "Vídeo" + hook + `sendVideo`

**Objetivo:** oferecer envio de vídeo no painel com preview e validação client-side.

**Itens:**
- [paralelo] `api.js`: adicionar `sendVideo(phone, file, caption, conversationId, channelId)` após [:386](../web/static/js/services/api.js#L386), apontando a `/api/contacts/{phone}/send-video`.
- [paralelo] `ContactDetail.js:55-56`: incluir `sendVideo` no `_api`.
- [paralelo] `Composer.js`: novo `videoInputRef` com `accept="video/mp4,video/3gpp"` (junto de [:53-66](../web/static/js/components/contacts/Composer.js#L53-L66)) e item "🎬 Vídeo" no menu de anexo ([:251-262](../web/static/js/components/contacts/Composer.js#L251-L262)); no overlay de confirmação ([:69-125](../web/static/js/components/contacts/Composer.js#L69-L125)) adicionar o caso `type==='video'` com `<video controls>` de preview.
- [sequencial] `useMediaUpload.js`: adicionar `pickVideo`/`handleVideoSelected`, `pendingMedia.type='video'` ([:36](../web/static/js/components/contacts/hooks/useMediaUpload.js#L36)); em `confirmPendingMedia` ([:169-179](../web/static/js/components/contacts/hooks/useMediaUpload.js#L169-L179)) novo branch → `optimistic.media_type='video'` + `api.sendVideo`. **Validação client** (tamanho ≤16 MB via `file.size`, extensão) antes de enfileirar — é o gancho da Opção A no front.
- ⚠️ Modo escuro: usar classes `wa-*`/`.wa-field`; o `<video>` de preview não precisa de tinta especial, mas o item de menu segue o padrão dos existentes.

**Pronto quando:** ao anexar um `.mp4`, o painel mostra preview de vídeo, envia, e a bolha renderiza com `<video controls>` (via [MediaContent.js:65](../web/static/js/components/contacts/MediaContent.js#L65)); reload mantém a mensagem.

#### Status de execução — Fase F2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F3 — Integração E2E + testes (barreira)

**Objetivo:** provar o fluxo ponta-a-ponta e travar regressão com teste.

**Itens:**
- [sequencial] `[depende de: F1,F2]` Teste em [test_endpoints.py:907-911](../tests/test_endpoints.py#L907-L911): `POST /send-video` com mp4 válido → 200; caso reprovado (>16 MB ou formato) → 4xx. GOWA/LLM já mockados na suíte.
- [paralelo] Confirmar a chave `video` no `_MEDIA_METHOD` do Telegram ([:433](../assets/plugin_examples/telegram/channels.py#L433)); se ausente, adicionar `("sendVideo","video")` (senão degrada p/ `sendDocument` — aceitável, mas `sendVideo` é o correto).
- [paralelo] Validar no canal Cloud real de teste (opcional, manual) usando [tests/manual_cloud_api_test.py](../tests/manual_cloud_api_test.py) como referência — **não** rodar contra número de cliente.

**Pronto quando:** suíte verde no Postgres de teste; envio manual de vídeo ao Cloud chega como vídeo reproduzível.

#### Status de execução — Fase F3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F5A — Opção A: BLOQUEAR + mensagem clara (se P1=A, ou base do híbrido)

**Objetivo:** quando o arquivo não atende à Cloud, barrar com aviso claro, sem tentar enviar.

**Itens:**
- [paralelo] Client (`useMediaUpload.js`): antes de enfileirar, se `file.size > 16MB` ou extensão ∉ {mp4,3gp} → **não cria bolha**, mostra aviso ("Só é possível enviar vídeo em **MP4 até 16 MB**. Converta o arquivo e tente novamente."). Reusar o mecanismo de erro já existente (bolha `role:'error'` via WS ou toast).
- [paralelo] Server (F1/F2 já retornam 4xx): a rota devolve `error` legível por motivo (`too_big`→"acima de 16 MB", `bad_format`→"formato não suportado (use MP4)", `bad_codec`→"codec não suportado (use H.264/AAC)"). O front mapeia para a bolha de erro.
- ⚠️ Sem ffprobe em produção, `bad_codec` não é detectável no client nem no server → nesse caso o arquivo passa e **pode** falhar na Meta com 131053; o front deve mostrar o erro do provider de forma amigável (não a string crua). Melhoria: propagar `131053` → texto "O WhatsApp recusou o vídeo (codec/formato). Reexporte em MP4 H.264/AAC.".

**Pronto quando:** anexar `.mkv` ou `.mp4` de 40 MB → aviso imediato, nada enviado; um mp4 com codec exótico que a Meta recuse → bolha de erro amigável (não a string técnica).

#### Status de execução — Fase F5A
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F5B — Opção B: CONVERTER com ffmpeg (se P1=B / incremento opcional)

**Objetivo:** aceitar qualquer vídeo e transcodificar para mp4 H.264/AAC ≤16 MB antes de enviar.

**Itens:**
- [sequencial] `Dockerfile:14-16`: adicionar `ffmpeg` ao `apt-get install` (traz `ffprobe`). ⚠️ Aumenta a imagem (~100–200 MB) — decidir se aceitável (ver P1).
- [sequencial] Novo `channels/video_transcode.py` no padrão de `_gif_to_mp4` ([whatsapp_cloud/channels.py:284-309](../assets/plugin_examples/whatsapp_cloud/channels.py#L284-L309)): `shutil.which("ffmpeg")` guard → ffprobe lê duração/codec/áudio → decide se precisa transcodificar → ffmpeg `-c:v libx264 -c:a aac -movflags +faststart -pix_fmt yuv420p`, com **bitrate-alvo calculado pela duração** para caber em 16 MB (ex.: `target_bitrate = (16*8*1024 / duration) - audio_bitrate`, com downscale de resolução se necessário). Timeout defensivo; limpa temporário.
- [sequencial] Integrar na rota F1: se `validate_video` reprovar por formato/codec/tamanho → tentar `transcode`; sucesso → seguir envio com o mp4 resultante; falha/ausência de ffmpeg → **cair na Opção A** (bloqueio com mensagem). Degradação graciosa idêntica ao GIF.
- ⚠️ Rodar em `asyncio.to_thread` (já é o padrão do send). Transcodes longos podem estourar timeouts de proxy/upload — impor **teto de duração/tamanho de entrada** (ex.: recusar >5 min ou >200 MB de entrada) para não travar o worker.

**Pronto quando:** anexar um `.mov`/`.webm`/mp4 de 40 MB → o painel envia um mp4 ≤16 MB reproduzível no WhatsApp; sem ffmpeg (simular) → cai no aviso da Opção A.

#### Status de execução — Fase F5B
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F6 — Regressão + verificação final (barreira)

**Objetivo:** garantir zero regressão nos envios existentes e coerência de UX.

**Itens:**
- [sequencial] Rodar suíte no Postgres de teste; confirmar image/audio/document intactos.
- [paralelo] Verificar modo escuro do novo item/preview; verificar bolha otimista → reconciliação (status operator) igual aos outros.
- [paralelo] Conferir GOWA e Telegram: enviar vídeo por eles (não deve regredir).

**Pronto quando:** checklist da §7 todo verde.

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| ffmpeg ausente em produção | Opção B silenciosamente não converte (como o GIF hoje) | Guard `shutil.which` + fallback p/ Opção A; se escolher B, **obrigatório** editar o Dockerfile (F5B) |
| Codec/faixas de áudio | Sem ffprobe não dá p/ validar H.264/AAC/1-áudio; Meta recusa com 131053 | Opção A: mensagem amigável no 131053. Opção B: transcode normaliza |
| Transcode longo trava o worker | ffmpeg síncrono em request | `asyncio.to_thread` + timeout + teto de duração/tamanho de entrada |
| Alvo de 16 MB | Vídeo longo pode não caber sem perder muita qualidade | Bitrate por duração + downscale; se ainda >16 MB, degradar p/ Opção A (avisar) |
| Bolha órfã em falha | Se persistir antes do envio | Manter o padrão atual: `messaging.send_media` só persiste **após** send OK ([messaging_service.py:228-260](../app/services/messaging_service.py#L228-L260)) |
| `if provider ==` | Introduzir ramo por nome de provider | Manter capability-driven; `kind="video"` é genérico, cada provider já resolve o seu |
| Janela 24h fora | Vídeo livre fora da janela é impossível (só template) | Reusar `_session_window_block`; template com header de vídeo fica p/ P2 (fora de escopo) |
| Modo escuro | Item/preview novo ilegível | Usar `wa-*`; testar com `.dark` ligado |
| Segredos | — | Nenhum segredo novo; nada na URL |

---

## 6. Perguntas em aberto

**P1 — Converter (ffmpeg) ou bloquear (validar + avisar)?** ⏸️ A DECIDIR (é a decisão central, D3)
- Contexto: produção **não tem ffmpeg** ([Dockerfile:14-16](../Dockerfile#L14-L16)); a Cloud aceita só mp4/3gp H.264/AAC ≤16 MB.
- (a) **Bloquear (Opção A)** — barato, robusto, funciona em prod hoje; operador converte manualmente. Downside: fricção p/ o operador; codec exótico ainda pode falhar na Meta (mitigado por mensagem amigável).
- (b) **Converter (Opção B)** — melhor UX (aceita qualquer vídeo); custo: +ffmpeg na imagem, CPU/tempo por request, risco de timeout/qualidade em vídeos grandes/longos.
- **Recomendação:** implementar **F1–F3 + F5A (bloquear) como base** e entregar **F5B (converter) como incremento opcional** com degradação graciosa (converte quando ffmpeg existe; senão bloqueia). Assim o bug é corrigido já (mp4 válido passa a funcionar) e a conversão vira um upgrade sem risco de regressão. Se o usuário priorizar UX total, promover F5B e editar o Dockerfile.

**P2 — Vídeo FORA da janela de 24h (template com header de vídeo)?** ⏸️ ADIADO (fora de escopo)
- Requer criar/gerir **templates com header `format:"VIDEO"`** e upload via **Resumable Upload API** (`header_handle`) — feature bem maior que o envio livre. O plano atual cobre só **dentro da janela** (caso do bug). Recomendação: tratar em plano próprio se houver demanda.

**P3 — Validar codec sem ffprobe?** ⏸️ Depende de P1
- Se P1=A e sem ffmpeg/ffprobe em prod, só dá p/ validar extensão+tamanho; codec fica a cargo do erro 131053 da Meta (mensagem amigável). Se P1=B, ffprobe entra junto com ffmpeg e resolve.

---

## 7. Checklist de verificação

- [ ] `POST /api/contacts/{phone}/send-video` com mp4 H.264/AAC ≤16 MB → **200** e vídeo chega ao contato Cloud (reproduzível).
- [ ] Arquivo >16 MB ou não-mp4 → **4xx** com mensagem clara; **nenhuma** bolha órfã persistida.
- [ ] Bolha otimista de vídeo renderiza `<video controls>` e reconcilia p/ `status='operator'` após envio.
- [ ] Reload (F5) e back/forward mantêm a mensagem de vídeo.
- [ ] `tests/test_endpoints.py` **verde** no Postgres (`WHATSBOT_TEST_DB_URL`), incluindo o novo teste de `/send-video`.
- [ ] Sem regressão em `send-image` / `send-audio` / `send-document`.
- [ ] Telegram e GOWA continuam enviando vídeo (não regrediram).
- [ ] Modo escuro: item "Vídeo" e preview legíveis com `.dark`.
- [ ] **[Se P1=B]** `ffmpeg` presente na imagem Docker; transcode gera mp4 ≤16 MB; sem ffmpeg → cai no bloqueio (degradação graciosa).
- [ ] Nenhum `if provider ==` novo; caminho permanece capability-driven.
- [ ] Nenhum segredo em URL/log.

---

## 8. Apêndice — arquivos-chave

**Backend**
- [server/routes/contacts.py](../server/routes/contacts.py) — nova rota `/send-video` (espelha `send-image` :1706-1748); gate `_session_window_block` :190.
- [app/services/messaging_service.py](../app/services/messaging_service.py) — `send_media` :187-260 (tail reusado, `media_type=kind`).
- [channels/outbound.py](../channels/outbound.py) — `send_media` :91-99 (roteia `kind` ao provider).
- `channels/video_validate.py` (novo) — validação; `channels/video_transcode.py` (novo, Opção B).

**Providers**
- [assets/plugin_examples/whatsapp_cloud/channels.py](../assets/plugin_examples/whatsapp_cloud/channels.py) — `send_media` :310-388 (já monta `type:"video"`), `_gif_to_mp4` :284-309 (padrão de degradação ffmpeg).
- [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py) — `send_media` :302-314.
- [assets/plugin_examples/telegram/channels.py](../assets/plugin_examples/telegram/channels.py) — `send_media` :208-228, `_MEDIA_METHOD` :433 (confirmar chave `video`).

**Frontend**
- [web/static/js/services/api.js](../web/static/js/services/api.js) — `sendVideo` novo (após :386).
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — `_api` :55-56; flags de capability :89-98.
- [web/static/js/components/contacts/Composer.js](../web/static/js/components/contacts/Composer.js) — inputs :53-66; menu :251-262; overlay :69-125.
- [web/static/js/components/contacts/hooks/useMediaUpload.js](../web/static/js/components/contacts/hooks/useMediaUpload.js) — tipo `video` :36/:87-93/:169-179.
- [web/static/js/components/contacts/MediaContent.js](../web/static/js/components/contacts/MediaContent.js) — render de vídeo :65-68 (já pronto).

**Infra / testes**
- [Dockerfile](../Dockerfile) — :14-16 (adicionar ffmpeg na Opção B).
- [tests/test_endpoints.py](../tests/test_endpoints.py) — :907-911 (modelo do teste de send).
- [tests/manual_cloud_api_test.py](../tests/manual_cloud_api_test.py) — referência de teste manual do provider Cloud.
