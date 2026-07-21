# Plano 64 — Arrastar arquivos para dentro da conversa e enviar (estilo WhatsApp/Telegram Web)

> **Status:** ✅ IMPLEMENTADO (2026-07-21, branch `feature/plano-64-arrastar-arquivos`) · **Data:** 2026-07-20 · **Escopo:** grande
>
> **Origem:** pedido do usuário — *"arrastar arquivos para dentro de uma conversa do cliente e mandar para ele… comportamento semelhante ao telegram, whatsapp e outros"*. Investigação por 8 sub-agentes em paralelo (mapa técnico consolidado e re-verificado contra a árvore de trabalho).
> **Método:** leitura do código real com `arquivo:linha` (6 exploradores + gap-fill + síntese, `grep`/`sed`/`wc -l` para medir), e **4 perguntas de escopo respondidas** em §0. Nenhuma afirmação de memória.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

**Causa-raiz (verificada):** **não existe** drag-and-drop de arquivo em lugar nenhum do painel — `grep -rn "dataTransfer|onDrop|onDragOver"` em `web/static/js/components/contacts/` e `shell/` → **0 matches**; o único DnD do app é reordenar cards de kanban via `text/plain` ([AttendanceBoard.js:32](../web/static/js/components/AttendanceBoard.js#L32)). O envio de mídia hoje é 100% por **seletor de arquivo / microfone / colar**, com **um único item por vez**: o hook [useMediaUpload.js:37](../web/static/js/components/contacts/hooks/useMediaUpload.js#L37) guarda `pendingMedia` (objeto único), e três guardas (`:76`, `:89`, `:120`) bloqueiam um segundo item. O pipeline de envio, porém, **já é unificado e funciona** ponta-a-ponta: `api.sendImage|sendAudio|sendDocument` → `uploadRequest` (FormData+fetch, [httpClient.js:120](../web/static/js/services/httpClient.js#L120)) → `POST /api/contacts/{phone}/send-{image,audio,document}` ([contacts.py:1706/1750/1795](../server/routes/contacts.py#L1706)) → `MessagingService.send_media` ([messaging_service.py:187](../app/services/messaging_service.py#L187)) → `OutboundRouter.send_media` ([outbound.py:91](../channels/outbound.py#L91)) → `<Provider>.send_media` ([base.py:308](../channels/base.py#L308)). **Enviar arquivo arbitrário como documento já funciona** (é o `else` catch-all de todo provider) — dropar arquivos **não exige mudança de canal/provider**. O trabalho é: (1) frontend de arrastar/soltar + multi-arquivo + pré-via; (2) blindagens que hoje faltam (limite de tamanho, colisão de nome, guarda global de drop, XSS armazenado).

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** | **Foto vs Arquivo = zonas divididas estilo Telegram** ✅ (2026-07-20) | O overlay de drop se parte em **duas metades**: "Foto ou vídeo" (comprimida, rota `/send-image` p/ imagem, `/send-video` p/ vídeo — ver D-extra) e "Arquivo" (original, rota `/send-document`). O modo é decidido **no gesto** (onde soltou), não num passo posterior. Cada arquivo carrega seu `sendMode ∈ {media, file}`. |
| **D2** | **Limite de tamanho = cliente + middleware** ✅ (2026-07-20) | Validação no navegador (erro instantâneo) **E** middleware `content-length` no backend (HTTP 413) — cliente é contornável. Tetos: **50 MB/arquivo, 10 arquivos/drop** (configuráveis; ver P1). Não troca o `await file.read()` por streaming nesta rodada (fica anotado como dívida em §6/P2). |
| **D3** | **Corrigir o XSS armazenado neste plano, como fase própria** ✅ (2026-07-20) | Fase dedicada (Wave 3): `Content-Disposition: attachment` p/ tudo fora de uma allow-list inline segura + extensão derivada do **MIME validado** (não do nome do cliente) + entropia no nome em disco. Fecha XSS **e** a colisão de nome (G5) de uma vez. |
| **D4** | **Extras adjacentes TODOS dentro** ✅ (2026-07-20): guarda global de drop no `window`; unificar `Ctrl+V` (paste) com o drop; vídeo inline (`kind="video"`); soltar sobre a linha da conversa na sidebar | Sobe o escopo p/ **grande**. Cada um vira fase: guarda global (Wave 0), paste unificado (dentro de F-frontend), vídeo inline (Wave 2), drop na sidebar (Wave 4). |
| **D5** | Postgres é o único backend; nada em produção pode quebrar ⇒ **aditivo/retrocompatível** | Reusar os 3 endpoints existentes (sem endpoint batch — N arquivos = N chamadas sequenciais). O contrato de resposta atual (`{message}`) é mantido; ids extras (G9) são **opcionais** e não bloqueiam. |
| **D6** | Reusar o que já existe (padrão do repo) | Nada de novo pipeline: `send_media(kind=...)` já roteia tudo. O caminho ótico do frontend reusa `confirmPendingMedia` generalizado para uma **fila**, não um item. |

---

## 1. Resumo executivo

O operador quer arrastar um ou vários arquivos para dentro da conversa aberta e enviá-los ao cliente, como no WhatsApp/Telegram Web. Hoje o painel **só** anexa via seletor/microfone/colar, **um arquivo por vez**, e **não** tem drag-and-drop nenhum — mas o **backend de envio de mídia já está pronto e é genérico** (imagem, áudio, documento, e por baixo vídeo/sticker via `send_media`).

A solução: (1) **overlay de drop com zonas divididas** (Telegram-style — "Foto/vídeo" vs "Arquivo") montado na raiz do painel de conversa ([ContactDetail.js:350](../web/static/js/components/contacts/ContactDetail.js#L350)); (2) generalizar `pendingMedia` de **item único → fila** com pré-via em grade + legenda + envio **sequencial** com pseudo-progresso ("2 de 5 enviados"), reusando os 3 endpoints existentes; (3) **blindagens** que hoje faltam — guarda global de `dragover/drop` no `window` (senão soltar fora do alvo **navega o navegador para fora e destrói o app**), limite de tamanho (cliente + middleware 413), e a correção do **XSS armazenado** (`Content-Disposition` + extensão do MIME + entropia de nome, que também mata a colisão de nome de arquivo em uploads paralelos); (4) **extras**: unificar o `Ctrl+V`, vídeo inline (`kind="video"`) e drop sobre a conversa na sidebar.

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| Hook de mídia (item único) | [useMediaUpload.js:37](../web/static/js/components/contacts/hooks/useMediaUpload.js#L37) | `pendingMedia = useState(null)` — **um** objeto `{type, file?, blob?, filename?, previewUrl?}`. |
| Guardas de "só 1" | [:76](../web/static/js/components/contacts/hooks/useMediaUpload.js#L76), [:89](../web/static/js/components/contacts/hooks/useMediaUpload.js#L89), [:120](../web/static/js/components/contacts/hooks/useMediaUpload.js#L120) | `requestImageSend`, `handleDocSelected`, `confirmPendingMedia` abortam se já há `pendingMedia`. |
| ⚠️ Seam assimétrico | [:75](../web/static/js/components/contacts/hooks/useMediaUpload.js#L75) exporta `requestImageSend`; o caminho de documento é **inline** em `handleDocSelected` ([:87](../web/static/js/components/contacts/hooks/useMediaUpload.js#L87)) e **não exportado** | Não há `requestDocumentSend`. `filename` só é setado no path de documento ([:90](../web/static/js/components/contacts/hooks/useMediaUpload.js#L90)) e a bolha lê `media.filename` ([:164](../web/static/js/components/contacts/hooks/useMediaUpload.js#L164)) → um drop que esquecer o `filename` renderiza `[Documento enviado: undefined]`. |
| Colar (analogia mais próxima do drop) | [:95](../web/static/js/components/contacts/hooks/useMediaUpload.js#L95), ligado só no `<textarea>` ([Composer.js:337](../web/static/js/components/contacts/Composer.js#L337)) | 5 limitações: **só imagem**, **só o 1º item** (`return` após match), **escopo textarea**, **silencioso** p/ não-imagem, **bloqueado** por `pendingMedia`. |
| Envio (o miolo reusável) | `confirmPendingMedia` [:119-242](../web/static/js/components/contacts/hooks/useMediaUpload.js#L119) | monta a bolha ótica (`_localId`, `_status:'sending'`, `_isLocalBlob:true` [:192](../web/static/js/components/contacts/hooks/useMediaUpload.js#L192)), chama `api.sendImage/sendAudio/sendDocument`, reconcilia por `_localId`. |
| Camada de API | [api.js:360/365/386](../web/static/js/services/api.js#L360) | `sendImage(phone,file,caption,convId,chId)` / `sendAudio(phone,blob,filename,…)` / `sendDocument(phone,file,caption,…)` → `uploadRequest`. |
| Transporte | [httpClient.js:120](../web/static/js/services/httpClient.js#L120) | `uploadRequest(path,fields)` — `FormData` + `fetch`. **fetch não expõe progresso de upload** (G8). |
| Rotas backend | [contacts.py:1706/1750/1795](../server/routes/contacts.py#L1706) | 3 rotas `@app.post`, cada uma `UploadFile` + `caption`(exceto áudio)/`conversation_id`/`channel_id` `Form`. Retornam **só** `{message:"..."}` — sem `msg_id`/`media_path` (G9). |
| Ordem de gates (fixa) | [contacts.py:1712-1735](../server/routes/contacts.py#L1712) | `permission_denied("conversation.reply")` → `_inbox_send_denied` → `_is_sandbox_contact` → `_channel_for` → `_session_window_block` (**antes** de escrever o arquivo) → `write_bytes` → `messaging.send_media`. |
| Nome em disco (⚠️ colisão) | [contacts.py:1730](../server/routes/contacts.py#L1730), [:1774](../server/routes/contacts.py#L1774), [sandbox.py:134](../server/routes/sandbox.py#L134) | `{ms}{suffix}` (imagem/áudio) / `{ms}_{stem}{suffix}` (doc) — **entropia só no milissegundo**: 2 uploads no mesmo ms se **sobrescrevem** (G5). |
| Tail de envio | [messaging_service.py:187](../app/services/messaging_service.py#L187) | grava `media_path="statics/outbox/{name}"` (**relativo, sem `/`** [:252](../app/services/messaging_service.py#L252)), `add_message(role=assistant,status=operator)`, broadcast `new_message` (**sem `_id`/`conversation_id`** [:274](../app/services/messaging_service.py#L274)) + `emit_with_filter("message.sent")`. |
| ⚠️ Falha de mídia | [messaging_service.py:235-243](../app/services/messaging_service.py#L235) | `raise GOWASendError` → só `error_bubble`; **não persiste linha**, arquivo fica órfão, `/retry-send` ([contacts.py:1652](../server/routes/contacts.py#L1652), **só texto**) não recupera (G10). |
| Camada de canal | [base.py:308](../channels/base.py#L308) `send_media` abstract; [outbound.py:91](../channels/outbound.py#L91) | `ChannelCapabilities.media` é **um bool** ([base.py:25](../channels/base.py#L25)) — não dá pra perguntar "sabe vídeo?". `OutboundRouter.send_media` é o **único** send que **não** chama `supports()` (G12). |
| Providers `send_media` | GOWA [gowa_channel.py:302](../channels/providers/gowa_channel.py#L302) · Telegram [telegram/channels.py:212](../assets/plugin_examples/telegram/channels.py#L212) · Cloud [whatsapp_cloud/channels.py:391](../assets/plugin_examples/whatsapp_cloud/channels.py#L391) · Widget [website/channels.py:166](../assets/plugin_examples/website/channels.py#L166) | Todos terminam num `else → document`. GOWA: `image`→`/send/image`, `audio`→`/send/audio`(`ptt=true`, **sem caption**), **else**→`/send/file`. `bin/gowa` tem `/send/video` e `/send/sticker` (via `strings`), mas o client Python **não** os expõe (G11). |
| Render de mídia | [MediaContent.js:14](../web/static/js/components/contacts/MediaContent.js#L14) | `url = isLocalBlob ? src : '/' + src` (invariante do `/` inicial). `<video controls>` **já é renderizado** [:65](../web/static/js/components/contacts/MediaContent.js#L65). Fallback "indisponível" via `MediaWithFallback` [:12](../web/static/js/components/contacts/MediaContent.js#L12). |
| Armazenamento/serving | `statics_outbox_dir` [app.py:126](../server/app.py#L126); mount `/statics` **auth-exempt** [app.py:466](../server/app.py#L466) | **`statics/outbox/`**, NÃO `senditems/` (GOWA apaga `senditems/` ~1.5s pós-envio). Sem `Content-Disposition`, sem thumbnail/resize/transcode (zero libs de imagem em `requirements.txt`), sem retenção. |
| ⚠️ Sem guarda global de drop | `grep preventDefault` em window `dragover/drop` → **0** | Soltar arquivo **fora** do alvo faz o navegador **navegar para o arquivo** e destruir o estado do app (G2 — perda de dados). |
| ⚠️ XSS armazenado (pré-existente) | CSP `script-src 'self' 'unsafe-inline'` [app.py:562](../server/app.py#L562); sem `Content-Disposition` | `suffix` controlado pelo cliente → `statics/outbox/x.html` servido same-origin como `text/html` → executa. `nosniff` **não** ajuda (o tipo é corretamente adivinhado). DnD amplia muito (arrastar « escolher no seletor). |

---

## 3. Inventário / análise

### 3.1 O coração — o modelo de dados do frontend (item → fila)

O trabalho principal é generalizar **um** `pendingMedia` para uma **fila** `pendingQueue`, cada entrada carregando seu modo de envio:

```js
// entrada da fila (contrato interno do hook — ilustrativo, não implementar aqui)
{ id, file|blob, filename, kind: 'image'|'video'|'audio'|'document',
  sendMode: 'media'|'file',        // D1 — decidido pela zona onde soltou
  caption: '',                     // por-item OU compartilhada (P3)
  previewUrl,                      // objectURL p/ thumb (revogar no unmount)
  _status: 'queued'|'sending'|'sent'|'failed', error? }
```

`sendMode` mapeia para a rota:

| `kind` do arquivo | zona "Foto ou vídeo" (`sendMode=media`) | zona "Arquivo" (`sendMode=file`) |
|---|---|---|
| `image/*` | `/send-image` (inline, comprimida) | `/send-document` (anexo original) |
| `video/*` | `/send-video` (player — Wave 2) **ou** `/send-document` se vídeo inline não entrar | `/send-document` |
| áudio / pdf / zip / qualquer outro | `/send-document` (não há "foto" p/ eles) | `/send-document` |

⚠️ **Arquivo não-visual solto na zona "Foto/vídeo"** cai em documento mesmo (não há como comprimir um `.zip` em "foto") — o overlay deve deixar isso claro (a zona "Foto/vídeo" só faz sentido p/ image/video; o resto ignora a distinção).

### 3.2 Segunda dimensão — envio sequencial e reconciliação

- **Sequencial** (D6/§5): a fila envia um por vez, reusando o miolo de `confirmPendingMedia`. Isso: (a) dá pseudo-progresso grátis ("2 de 5"); (b) **contorna a colisão de nome** (G5) mesmo antes do fix de entropia — dois uploads nunca colidem no mesmo ms; (c) simplifica o tratamento de falha parcial (para na 1ª falha? continua e marca? — ver P4).
- **Reconciliação**: cada item tem seu `_localId`; a bolha ótica ([:192](../web/static/js/components/contacts/hooks/useMediaUpload.js#L192)) já existe e é reusada N vezes. O `new_message` de mídia **não** traz `_id` ([messaging_service.py:274](../app/services/messaging_service.py#L274)), então a reconciliação por `_localId` (comportamento de hoje [:213-223](../web/static/js/components/contacts/hooks/useMediaUpload.js#L213)) permanece — nenhuma regressão.

### 3.3 Itens a construir

| # | Item | Onde | Abordagem | Risco | Esforço |
|---|------|------|-----------|-------|---------|
| I0 | **Guarda global de drop** no `window` (`dragover`/`drop` → `preventDefault`) | novo effect em [App.js](../web/static/js/components/shell/App.js) ou `<script>` em [web/index.html](../web/index.html) | 2 listeners; ignora quando o alvo é a zona de drop da conversa. Evita perda de estado (G2). | baixo | S |
| I1 | **Fila `pendingQueue`** (generaliza `pendingMedia`) + `sendMode` por item | [useMediaUpload.js:37-252](../web/static/js/components/contacts/hooks/useMediaUpload.js#L37) | trocar `useState(null)`→lista; manter `pendingMedia` como shim (1º item) p/ não quebrar o `Composer` até o F3. Remover as 3 guardas de "só 1". | médio | L |
| I2 | `requestDocumentSend` **exportado** + `requestFilesDrop(files, sendMode)` | [useMediaUpload.js:87](../web/static/js/components/contacts/hooks/useMediaUpload.js#L87) | extrai o inline de `handleDocSelected` p/ função exportada; `requestFilesDrop` classifica cada `File` por `kind`/`sendMode` e enfileira (sempre setando `filename` — evita `undefined` [:164](../web/static/js/components/contacts/hooks/useMediaUpload.js#L164)). | médio | M |
| I3 | **Envio sequencial** da fila (loop sobre o miolo de `confirmPendingMedia`) | [useMediaUpload.js:119-242](../web/static/js/components/contacts/hooks/useMediaUpload.js#L119) | fatorar `sendOne(item)` do corpo atual; `confirmQueue()` itera com `for…await`; estado `sentCount/total`. Falha parcial: ver P4. | médio | L |
| I4 | **Overlay de drop com zonas divididas** (Telegram) | novo, montado na raiz [ContactDetail.js:350](../web/static/js/components/contacts/ContactDetail.js#L350) | `<div class="relative">` na raiz + overlay `absolute inset-0` como último filho; 2 metades ("Foto ou vídeo" / "Arquivo"), realce na metade sob o cursor; só visível durante `dragover` com `types.includes('Files')`. Dark mode: `wa-*` + checar `bg-black/50`. | médio | L |
| I5 | **Pré-via multi-arquivo** (grade/filmstrip) + legenda + Escape + remover item | [Composer.js:63-125](../web/static/js/components/contacts/Composer.js#L63) (overlay de confirmação atual) | grade de thumbs; por-item remover; `Esc` fecha; legenda compartilhada (P3); botão "Enviar" mostra "N de M". `.wa-field` na legenda. | médio | L |
| I6 | **Unificar `Ctrl+V`** com o drop | [useMediaUpload.js:95](../web/static/js/components/contacts/hooks/useMediaUpload.js#L95) | `handlePaste` passa a chamar `requestFilesDrop` (todos os itens, qualquer tipo, sem bloquear por fila). Fecha as 5 limitações. | baixo | S |
| I7 | **Entropia de nome + extensão do MIME** (mata G5 + base do XSS) | [contacts.py:1730/1774](../server/routes/contacts.py#L1730), [sandbox.py:134](../server/routes/sandbox.py#L134) | `{ms}_{uuid4().hex[:8]}{ext}` onde `ext` vem do **MIME validado** (`mimetypes.guess_extension`), não do nome do cliente. | baixo | S |
| I8 | **`Content-Disposition: attachment`** p/ mídia fora da allow-list inline | serving de `/statics/outbox` — ver §4.3 | rota dedicada (ou header no mount) que força download p/ tudo que não seja `{jpg,jpeg,png,webp,gif,mp4,ogg,mp3,pdf}`; allow-list inline pequena e explícita. | médio | M |
| I9 | **Middleware `content-length`** (413) escopado aos paths de upload | novo `@app.middleware` em [app.py](../server/app.py#L493) | recusa antes de ler o corpo; teto configurável (P1). Precedente: cap de zip [plugins.py:118](../server/routes/plugins.py#L118). | baixo | S |
| I10 | **Validação de tamanho/quantidade no cliente** | [useMediaUpload.js](../web/static/js/components/contacts/hooks/useMediaUpload.js) `requestFilesDrop` | `file.size > MAX` → toast e descarta o item; `> MAX_FILES` → toast e trunca. Constantes espelham o backend. | baixo | S |
| I11 | **Vídeo inline** `kind="video"` | [gowa/client.py](../gowa/client.py) `send_video` + 1 `elif` em [gowa_channel.py:302](../channels/providers/gowa_channel.py#L302) | novo método client (`/send/video`); Telegram/Cloud **já** mapeiam vídeo; `MediaContent.js:65` **já** renderiza. `else` degrada p/ documento em provider sem suporte. | baixo | M |
| I12 | **Drop na linha da conversa (sidebar)** | [ContactList.js](../web/static/js/components/contacts/ContactList.js) | `onDrop` por linha → abre pré-via daquele contato (sem trocar de conversa) OU envia direto. Reusa `requestFilesDrop` com o `phone` da linha. | médio | M |
| I13 | (opcional, D5) retornar `msg_id`/`media_path` no `_ok` | [contacts.py:1747/1841](../server/routes/contacts.py#L1747) | destrava reconciliação fina (G9). **Não bloqueia** — a reconciliação por `_localId` já basta. | baixo | S |

### Falsos positivos descartados

| Suspeita | Por que NÃO |
|----------|-------------|
| Precisa de endpoint **batch** (`files: list[UploadFile]`) | Não. N arquivos = N chamadas sequenciais aos 3 endpoints existentes. Batch exigiria um **novo contrato de falha parcial** (`send_media` devolve 1 resultado). D5/D12. |
| Precisa de **nova capability** `send_document`/`send_video` p/ o drop | Não p/ documento — é o `else` de todo provider, funciona hoje. Só o **vídeo inline** (I11) se beneficia de gating, e mesmo assim o `else→document` já degrada com segurança. |
| Precisa **subir** limite de corpo | Ao contrário — **não existe limite nenhum** (Starlette: file part **ilimitado**; uvicorn/proxy sem `limit_*`). O trabalho é **introduzir** teto (I9), não elevar. |
| Trocar `uploadRequest` (fetch) por XHR p/ **progresso real** | Fora de escopo v1 (P5). `fetch` não dá progresso de upload; pseudo-progresso "N de M" (sequencial, I3) resolve a percepção. XHR mexeria num transporte compartilhado (CSV import, zip de plugin). |
| Montar o overlay no `Composer` | Impossível — `Composer` retorna **fragmento** com 4 irmãos de topo ([Composer.js:52](../web/static/js/components/contacts/Composer.js#L52)); não há wrapper p/ `relative`. Montar na raiz [ContactDetail.js:350](../web/static/js/components/contacts/ContactDetail.js#L350). |
| Reusar `Contacts.js` como alvo de drop | Ele já tem `relative` mas **cobre os painéis de info** também. A raiz do `ContactDetail` é o limite exato da conversa. |
| Thumbnail/resize no servidor | Zero libs de imagem no `requirements.txt`; WhatsApp/GOWA já comprime "foto" no lado do provider. Pré-via usa `objectURL` do próprio arquivo no cliente. |

---

## 4. Contrato fixo (frontend e backend paralelizam contra isto)

### 4.1 — Backend (mantém os 3 endpoints; só muda nome-em-disco + tetos)

```
POST /api/contacts/{phone}/send-image      (multipart)  image, caption, conversation_id, channel_id
POST /api/contacts/{phone}/send-document   (multipart)  document, caption, conversation_id, channel_id
POST /api/contacts/{phone}/send-video      (multipart)  video, caption, conversation_id, channel_id   ← NOVO (I11), espelha send-image
  → 200 { ok:true,  data:{ message:"...", msg_id?, media_path? } }        (msg_id/media_path opcionais — I13)
  → 413 { ok:false, error:"Arquivo excede N MB" }                          (middleware I9)
  → 409 { ok:false, error:"Fora da janela de 24h…", data:{reason:"session_window_closed"} }  (inalterado)
  → 500 { ok:false, error:"Falha ao enviar …: <detalhe>" }                (inalterado)

Nome em disco (I7):  {ms}_{uuid4().hex[:8]}{ext}   ext = mimetypes.guess_extension(mime_validado)
Serving (I8):        Content-Disposition: attachment  para todo mime fora da allow-list inline
                     allow-list inline = image/jpeg,png,webp,gif · video/mp4 · audio/ogg,mpeg · application/pdf
```

### 4.2 — Frontend (o hook expõe a fila; o overlay dirige o `sendMode`)

- `requestFilesDrop(fileList, sendMode)` — classifica cada arquivo (`kind` por `file.type`), valida tamanho/qtd (I10), enfileira com `sendMode` (D1) e `filename` sempre setado.
- Overlay de drop (I4): visível só durante `dragover` com `Files`; 2 metades; a metade sob o cursor define `sendMode`.
- Pré-via (I5): grade de thumbs; legenda compartilhada; "Enviar (N)"; `Esc`/clique-fora cancela; remover item individual.
- Envio (I3): sequencial; header/botão mostra "Enviando 2 de 5…"; cada item reconcilia por `_localId`.
- Guarda global (I0): `window` `dragover/drop` `preventDefault` sempre; o overlay da conversa faz `stopPropagation` no seu próprio drop.

---

## 5. Fases / Roadmap

```
WAVE 0  I0(guarda global) · I7(nome+ext) · I9(middleware 413)          ← 3 blindagens independentes, em paralelo
           │ (I7 não bloqueia nada; I0/I9 são pré-requisito de segurança do drop)
WAVE 1  I1(fila) → I2(requestFilesDrop) → I3(envio sequencial)          ← núcleo do hook, sequencial entre si
           │ (barreira: I3 pronto habilita a UI)                        [I6 paste unificado agrupa aqui, depende de I2]
WAVE 2  I4(overlay drop) · I5(pré-via) · I10(validação cliente) · I11(vídeo inline)   ← UI + vídeo, em paralelo [dependem de Wave 1]
           │ (barreira: feature de drop completa)
WAVE 3  I8(Content-Disposition/XSS)                                     ← 🔴 fase de segurança, sozinha [independe da UI, mas revisar junto]
WAVE 4  I12(drop na sidebar) · I13(ids no _ok, opcional)               ← extras finais [dependem do núcleo]
```

| Wave | Fase | Workstream | Paralelização | Risco | Pronto quando |
|------|------|-----------|:---:|:---:|---|
| 0 | F0 | I0 guarda global de drop | 🟢 | baixo | soltar arquivo fora do alvo **não** navega o browser |
| 0 | F1 | I7 nome+extensão do MIME | 🟢 | baixo | 2 uploads no mesmo ms geram nomes distintos; ext vem do MIME |
| 0 | F2 | I9 middleware 413 | 🟢 | baixo | `curl -F` de 60 MB → HTTP 413; 40 MB passa |
| 1 | F3 | I1 fila | 🔴 [bloqueia F4/F5] | médio | painel envia ≥2 arquivos escolhidos pelo seletor, um após o outro |
| 1 | F4 | I2 requestFilesDrop + I6 paste | 🔴 [depende F3] | médio | `Ctrl+V` de 2 imagens enfileira ambas; classificação por tipo correta |
| 1 | F5 | I3 envio sequencial | 🔴 [depende F3] | médio | fila de 5 mostra "N de 5" e reconcilia cada bolha |
| 2 | F6 | I4 overlay zonas divididas | 🟢 [depende Wave 1] | médio | arrastar mostra 2 metades; soltar na de cima=foto, na de baixo=arquivo |
| 2 | F7 | I5 pré-via multi + legenda | 🟢 [depende Wave 1] | médio | grade de thumbs, remover item, `Esc`, legenda; dark mode legível |
| 2 | F8 | I10 validação cliente | 🟢 | baixo | arquivo >50 MB → toast, não sobe; 11º arquivo → toast e trunca |
| 2 | F9 | I11 vídeo inline | 🟢 | baixo | `.mp4` na zona "Foto/vídeo" no GOWA vira player, não anexo |
| 3 | F10 | I8 Content-Disposition/XSS | 🔴 [sozinha] | médio | `x.html` enviado é baixado (attachment), não executado; allow-list inline renderiza |
| 4 | F11 | I12 drop na sidebar | 🟢 [depende núcleo] | médio | soltar sobre "Maria" abre pré-via/envia p/ Maria sem trocar de conversa |
| 4 | F12 | I13 ids no _ok (opcional) | 🟢 | baixo | resposta traz `msg_id`; reconciliação fina (não regressão se ausente) |

**Disciplina:** verde a cada fase; **caracterização ANTES** de mexer no fluxo de envio de mídia (F3 toca o miolo de `confirmPendingMedia`); **um refactor por commit**; nunca avançar com teste vermelho não-explicado; testar **dark mode** em toda tela nova (overlay + pré-via).

---

### Fase F0 — Guarda global de drop 🟢
**Objetivo:** impedir que soltar um arquivo fora do alvo navegue o navegador e destrua o estado do app.
**Itens:**
1. `[sequencial]` Adicionar effect em [App.js](../web/static/js/components/shell/App.js) (ou `<script>` inline em [web/index.html](../web/index.html)) com `window.addEventListener('dragover'|'drop', e => e.preventDefault())`.
2. `[sequencial]` Garantir que o overlay da conversa (F6) faça `stopPropagation`/`preventDefault` no próprio `drop` para não conflitar.
**Pronto quando:** arrastar um `.pdf` e soltar sobre a sidebar/área vazia **não** abre o PDF nem recarrega; o app permanece na conversa. `node tests/frontend/check_imports.mjs` verde.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída
- **O que foi feito:** Guarda global de `dragover`/`drop` no `window` adicionada em `web/static/js/components/shell/App.js` (novo `useEffect`, antes do effect de `popstate`).
- **Como foi feito / decisões:** Um único par de listeners que só chama `preventDefault()` quando `dataTransfer.types` inclui `Files` — assim arrastar texto/seleção dentro do app continua funcionando. Não filtra por alvo: as zonas de drop reais recebem o evento antes (bubbling) e fazem o seu trabalho; a guarda só mata o default de navegar.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `node tests/frontend/check_imports.mjs` verde; suíte de endpoints 1500/0 (não afetada).

---

### Fase F1 — Nome em disco com entropia + extensão do MIME 🟢
**Objetivo:** dois uploads simultâneos nunca se sobrescrevem; a extensão gravada vem do MIME validado, não do nome do cliente (base do fix de XSS).
**Itens:**
1. `[paralelo]` Em [contacts.py:1730](../server/routes/contacts.py#L1730) (imagem), [:1774](../server/routes/contacts.py#L1774) (áudio) e no doc ([:~1820](../server/routes/contacts.py#L1795)): trocar `{ms}{suffix}` por `{ms}_{uuid4().hex[:8]}{ext}`, `ext = mimetypes.guess_extension(mime) or fallback`.
2. `[paralelo]` Mesma troca em [sandbox.py:134](../server/routes/sandbox.py#L134).
3. `[sequencial]` Caracterização: golden/asserção de que `send-image`/`-document` continuam gravando e enviando (mock GOWA `.images`/`.files`).
**Pronto quando:** dois POSTs de imagem "no mesmo instante" (teste) geram arquivos distintos em `statics/outbox/`; `venv/bin/python tests/test_endpoints.py` sem **novas** falhas (2 pré-existentes conhecidas).

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:** Novo módulo `server/upload_names.py` (`extension_for` + `unique_media_name`) e as 6 gravações em disco migradas: `send-image`, `send-audio`, `send-document`, `private-audio`, `private-image/document` (`server/routes/contacts.py`) e `_save_upload` (`server/routes/sandbox.py`).
- **Como foi feito / decisões:** Nome em disco = `{ms}_{uuid4[:8]}{ext}`. A extensão vem do **MIME validado** (`mimetypes.guess_extension` + overrides para `audio/ogg→.ogg`, `image/jpeg→.jpg`, etc.); o sufixo do cliente só é consultado quando o MIME é ausente/genérico (`application/octet-stream`), e nos dois caminhos uma allow-list negativa (`DANGEROUS_EXTENSIONS`: html/svg/js/php/…) força `.bin`. O nome ORIGINAL continua indo ao contato (o `filename` passado ao canal não mudou) — só o nome em disco foi reescrito.
- **Problemas / pendências:** Nenhuma. Arquivos já gravados com o nome antigo seguem servíveis (nada foi renomeado retroativamente).
- **Verificação:** 6 checks novos em `tests/test_endpoints.py` (seção "Upload hardening (plano 64)"): 2 uploads seguidos geram nomes distintos com formato `ms_uuid8.ext`; `.html` enviado como documento nasce `.bin` em disco mas mantém `payload.html` para o contato. Suíte 1500/0.

---

### Fase F2 — Middleware de limite de corpo (413) 🟢
**Objetivo:** recusar uploads acima do teto antes de lê-los na RAM.
**Itens:**
1. `[sequencial]` Novo `@app.middleware("http")` em [app.py](../server/app.py#L493) (junto dos existentes em `:493`/`:562`/`:588`), escopado aos paths `.../send-image|send-audio|send-document|send-video|import` e sandbox equivalentes: se `Content-Length > MAX_UPLOAD_BYTES` → 413 `{ok:false,error}`.
2. `[sequencial]` Constante `MAX_UPLOAD_BYTES` (default 50 MB) e `MAX_FILES_PER_DROP` (10) — configuráveis (P1).
**Pronto quando:** `curl -F image=@60mb.bin .../send-image` → **413**; 40 MB → segue o fluxo normal. Sem segredo no log.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída
- **O que foi feito:** Novo `server/upload_limits.py` (`MAX_UPLOAD_BYTES`=50 MB, `MAX_FILES_PER_DROP`=10, `is_upload_path`, `too_large_response`) + middleware `upload_size_limit` em `server/app.py`.
- **Como foi feito / decisões:** Middleware escopado por regex nos paths de upload (send-image/audio/video/document, private-*, contacts/import, sandbox/*) que recusa com **413** olhando só o `Content-Length` declarado — barato e antes de qualquer leitura de corpo. Constantes hardcoded (P1) e espelhadas no cliente em `web/static/js/services/uploadLimits.js`.
- **Problemas / pendências:** Um cliente que mente no `Content-Length` (chunked) ainda passa — cobrir isso exigiria contar bytes no stream, o que só faz sentido junto com a troca de `await file.read()` por streaming (P2, adiado).
- **Verificação:** 2 checks novos: corpo acima do teto → 413 com envelope `{ok:false,error}`; abaixo do teto → 200 normal. Suíte 1500/0.

---

### Fase F3 — Fila `pendingQueue` (item → lista) 🔴 [bloqueia F4/F5]
**Objetivo:** o hook passa a segurar N itens pendentes, mantendo `pendingMedia` como shim do 1º item até o Composer migrar.
**Itens:**
1. `[sequencial]` **Caracterização primeiro**: teste `node --test` do estado atual de `useMediaUpload` (envio de 1 imagem/doc, reconciliação por `_localId`) — âncora antes de mexer.
2. `[sequencial]` [useMediaUpload.js:37](../web/static/js/components/contacts/hooks/useMediaUpload.js#L37): `pendingQueue = useState([])`; derivar `pendingMedia = pendingQueue[0] ?? null` p/ retrocompat do Composer.
3. `[sequencial]` Remover as 3 guardas de "só 1" ([:76](../web/static/js/components/contacts/hooks/useMediaUpload.js#L76), [:89](../web/static/js/components/contacts/hooks/useMediaUpload.js#L89), [:120](../web/static/js/components/contacts/hooks/useMediaUpload.js#L120)).
**Pronto quando:** escolhendo 2 arquivos pelo seletor (com `multiple`), ambos aparecem na fila e são enviados; caracterização verde antes e depois.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída
- **O que foi feito:** `useMediaUpload` passou de `pendingMedia` (objeto) para `pendingQueue` (lista). As 3 guardas de "só 1" saíram. `pendingMedia` continua exportado como shim (= 1º item da fila). Inputs de arquivo ganharam `multiple`.
- **Como foi feito / decisões:** A **caracterização** foi feita extraindo a lógica pura para módulos novos e testando-a com `node --test` (o repo não tem harness de hook/preact — todos os 15 testes de frontend existentes são de módulos puros, então criar um seria um desvio de padrão): `web/static/js/services/mediaQueue.js` (classificação `kind`/`sendMode`, montagem dos itens, rótulo de progresso) e `web/static/js/services/uploadLimits.js` (tetos). O hook virou casca fina em volta deles. Cada item carrega `kind` (era `type`) — o Composer foi ajustado.
- **Problemas / pendências:** Não há teste automatizado do hook em si (só dos módulos puros que ele orquestra); a verificação do wiring é manual.
- **Verificação:** 26 checks novos em `mediaQueue.test.js`. Suíte de frontend 298/298, endpoints 1500/0, `check_imports.mjs` verde.

---

### Fase F4 — `requestFilesDrop` + paste unificado 🔴 [depende F3]
**Objetivo:** uma única porta de entrada classifica e enfileira arquivos de qualquer origem (drop, seletor, colar).
**Itens:**
1. `[sequencial]` Extrair o inline de `handleDocSelected` ([:87](../web/static/js/components/contacts/hooks/useMediaUpload.js#L87)) num `requestDocumentSend` **exportado**.
2. `[sequencial]` `requestFilesDrop(fileList, sendMode)`: p/ cada `File`, decide `kind` por `file.type` e a rota por §3.1; **sempre** seta `filename` (evita `[Documento enviado: undefined]` [:164](../web/static/js/components/contacts/hooks/useMediaUpload.js#L164)).
3. `[sequencial]` `handlePaste` ([:95](../web/static/js/components/contacts/hooks/useMediaUpload.js#L95)) passa a chamar `requestFilesDrop(items, 'media')` — todos os itens, qualquer tipo.
4. `[sequencial]` Adicionar `multiple` aos inputs de arquivo ([Composer.js:53-66](../web/static/js/components/contacts/Composer.js#L53)).
**Pronto quando:** `Ctrl+V` com 2 imagens enfileira as 2; colar um trecho de texto **não** vira anexo; seletor com `multiple` enfileira vários.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída
- **O que foi feito:** `requestDocumentSend` exportado, `requestFilesDrop(files, sendMode)` como porta de entrada única (drop, colar e seletor passam por ela) e `handlePaste` unificado.
- **Como foi feito / decisões:** `requestFilesDrop` valida tetos → classifica cada `File` por `file.type` + a zona do gesto → enfileira com `filename` SEMPRE preenchido (`filenameFor` tem default por kind, matando o `[Documento enviado: undefined]`). O paste deixou as 5 limitações antigas: aceita qualquer tipo de arquivo, TODOS os itens do clipboard, não é bloqueado por fila cheia e só faz `preventDefault` quando havia arquivo (colar texto segue nativo). Áudio/PDF/zip caem em documento nas DUAS zonas — `/send-audio` é nota de voz PTT sem legenda, então anexar um `.mp3` por lá seria errado.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** Testes de `classifyFile`/`filenameFor`/`buildQueueItems` cobrem as duas zonas, arquivo sem `type` e lista vazia/nula.

---

### Fase F5 — Envio sequencial da fila 🔴 [depende F3]
**Objetivo:** enviar a fila item a item, com pseudo-progresso e reconciliação por item.
**Itens:**
1. `[sequencial]` Fatorar `sendOne(item)` do corpo de `confirmPendingMedia` ([:119-242](../web/static/js/components/contacts/hooks/useMediaUpload.js#L119)).
2. `[sequencial]` `confirmQueue()`: `for (const item of queue) await sendOne(item)`; estado `sentCount/total`; tratar falha parcial (P4).
3. `[sequencial]` ⚠️ Sandbox arity trap: se passar opções novas, passar como **objeto** ou atualizar [Sandbox.js:172-174](../web/static/js/components/Sandbox.js#L172) (args posicionais são silenciosamente descartados).
**Pronto quando:** fila de 5 mostra "Enviando 3 de 5…"; cada bolha ótica reconcilia; falha no 3º segue a política de P4 (não trava os demais / marca o 3º).

#### Status de execução — Fase F5
**Estado:** ✅ Concluída
- **O que foi feito:** `sendOne(item, caption)` fatorado do corpo de `confirmPendingMedia`; `confirmQueue()` itera com `for…await`; estado `sentCount`/`sendTotal` + `sendProgressLabel`.
- **Como foi feito / decisões:** Sequencial (D6): pseudo-progresso de graça, sem colisão de nome e falha parcial simples. **P4 = continuar**: item falho vira bolha `failed` e o lote segue; ao fim, um toast resume ("2 de 5 arquivos falharam"). Exceção: `session_window_closed` vale para a conversa inteira → para o lote e abre o seletor de template. **Legenda compartilhada vai só no PRIMEIRO item** (repetir a mesma legenda em 5 arquivos poluiria a conversa; mesmo critério do Telegram). Vídeo degrada para documento quando a API efetiva não expõe `sendVideo` — o que cobre o sandbox e o *arity trap* do `Sandbox.js` sem tocar nele (nenhum argumento posicional novo foi introduzido).
- **Problemas / pendências:** `fetch` não dá progresso por bytes (P5, decidido): a granularidade é o arquivo.
- **Verificação:** Endpoints 1500/0; frontend 298/298.

---

### Fase F6 — Overlay de drop com zonas divididas 🟢 [depende Wave 1]
**Objetivo:** o gesto de soltar decide foto vs arquivo (Telegram-style).
**Itens:**
1. `[sequencial]` Envolver a raiz de [ContactDetail.js:350](../web/static/js/components/contacts/ContactDetail.js#L350) com `relative`; montar overlay `absolute inset-0` como último filho (o early-return `if (!phone)` em [:274](../web/static/js/components/contacts/ContactDetail.js#L274) já garante ausência no estado vazio).
2. `[sequencial]` Mostrar só quando `dragover` com `e.dataTransfer.types.includes('Files')`; 2 metades ("Foto ou vídeo" / "Arquivo"); realçar a metade sob o cursor; `drop` chama `requestFilesDrop(files, zona)`.
3. `[sequencial]` Dark mode: `wa-*`/`.wa-field`; se usar `bg-black/50`, checar contraste nos 2 temas.
**Pronto quando:** arrastar um arquivo mostra as 2 metades; soltar em cima envia como foto/vídeo, embaixo como arquivo; `dragleave` some o overlay.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída
- **O que foi feito:** Hook novo `hooks/useDropZone.js` + componente `DropOverlay.js`, ligados na raiz do `ContactDetail` (que ganhou `relative` + os 4 handlers de drag).
- **Como foi feito / decisões:** Contagem de profundidade (`drag depth`) num ref para o overlay não piscar ao cruzar filhos. A zona é decidida pela metade da raiz sob o cursor (`clientY` vs meio do `getBoundingClientRect`), e `setZone` com o mesmo valor não re-renderiza (bail-out do preact), então mover o cursor dentro da mesma metade é de graça. O overlay é `pointer-events-none` — se capturasse o ponteiro, entrar nele geraria `dragleave` na raiz e piscaria. O drop faz `stopPropagation` para a guarda global (F0) não vê-lo como "soltou fora". Desligado quando o operador não pode responder, quando a janela de 24h está fechada (só template resolve) ou com lote em voo. Cores: `bg-black/70` + `text-white` + `wa-teal` — o overlay é escuro nos dois temas por construção (é uma camada sobre a conversa), então não depende do modo.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `check_imports.mjs` verde; sintaxe dos módulos `html\`\`` checada com `node --input-type=module --check`. Validação visual do arrasto é manual (não há harness de DOM).

---

### Fase F7 — Pré-via multi-arquivo + legenda 🟢 [depende Wave 1]
**Objetivo:** antes de enviar, mostrar a grade de arquivos com legenda e controle por item.
**Itens:**
1. `[sequencial]` Reformar o overlay de confirmação atual ([Composer.js:63-125](../web/static/js/components/contacts/Composer.js#L63)) para grade/filmstrip de thumbs (`objectURL`).
2. `[sequencial]` Remover item; `Esc`/clique-fora cancela tudo (liberar `objectURL`); legenda compartilhada (P3); botão "Enviar (N)".
3. `[sequencial]` Semear a legenda com o texto já digitado no composer, se houver ([ContactDetail.js:111](../web/static/js/components/contacts/ContactDetail.js#L111)) — P6.
**Pronto quando:** dropar 3 arquivos abre a grade; remover 1 deixa 2; `Esc` cancela; legenda vai junto; dark mode legível.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída
- **O que foi feito:** Componente novo `MediaQueuePreview.js` substitui o overlay de confirmação de 1 item no `Composer`: grade de miniaturas com scroll horizontal, remoção por item, legenda compartilhada, `Esc` para cancelar e botão "Enviar (N)".
- **Como foi feito / decisões:** Item ÚNICO de imagem mantém a prévia grande de antes (não regride a UX de 1 arquivo); ≥2 itens viram filmstrip de 84px. Vídeo mostra o primeiro frame via `<video muted preload=metadata>` + play sobreposto; documento mostra ícone + extensão + nome. Áudio continua com o `AudioPlayer` inteiro e sem legenda. O `Esc` só cancela quando não há lote em voo. Como a fila é esvaziada ao confirmar (as bolhas óticas já estão no fio), o "Enviando N de M…" ganhou uma faixa própria no Composer.
- **Problemas / pendências:** **P6 (semear a legenda com o texto já digitado) NÃO foi implementado** — o texto do composer e a legenda seguem separados. Era "nice-to-have" e mexer nisso arriscaria perder o rascunho do operador ao cancelar o lote.
- **Verificação:** `check_imports.mjs` verde; suíte de frontend 298/298.

---

### Fase F8 — Validação de tamanho/quantidade no cliente 🟢
**Objetivo:** feedback instantâneo antes de gastar rede.
**Itens:**
1. `[sequencial]` Em `requestFilesDrop`: `file.size > MAX_UPLOAD_BYTES` → toast ([notify.js](../web/static/js/services/notify.js)) e descarta o item; total > `MAX_FILES_PER_DROP` → toast e trunca.
2. `[sequencial]` Constantes espelham o backend (P1).
**Pronto quando:** arrastar um arquivo de 80 MB mostra toast e não sobe; arrastar 15 arquivos envia 10 e avisa do corte.

#### Status de execução — Fase F8
**Estado:** ✅ Concluída
- **O que foi feito:** Validação de tamanho/quantidade no cliente, dentro de `requestFilesDrop`.
- **Como foi feito / decisões:** Implementada junto com F4 (é a primeira coisa que a porta de entrada única faz), via `applyUploadLimits`/`limitsMessage` de `services/uploadLimits.js` — puros e testados. Arquivo acima de 50 MB é descartado com toast; acima de 10 arquivos trunca e avisa. O teto de 10 vale para a FILA inteira, não por gesto (soltar 6 + 6 não acumula 12).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** 14 checks em `uploadLimits.test.js` (limite exato, limite+1, corte por quantidade contando só o que passou no tamanho, mensagens).

---

### Fase F9 — Vídeo inline (`kind="video"`) 🟢
**Objetivo:** vídeo solto na zona "Foto/vídeo" vira player, não anexo.
**Itens:**
1. `[sequencial]` `send_video` em [gowa/client.py](../gowa/client.py) (`/send/video`, timeout ~60s como `send_file`).
2. `[sequencial]` 1 `elif kind=="video"` em [gowa_channel.py:302](../channels/providers/gowa_channel.py#L302); Telegram/Cloud **já** mapeiam; `else→document` continua degradando p/ provider sem suporte.
3. `[sequencial]` Rota `POST .../send-video` espelhando `send-image` ([contacts.py:1706](../server/routes/contacts.py#L1706)); `api.sendVideo`; classificar `video/*`+`sendMode=media` p/ ela.
**Pronto quando:** `.mp4` na zona "Foto/vídeo" no GOWA chega como vídeo tocável; no provider sem `/send/video`, chega como documento (sem erro).

#### Status de execução — Fase F9
**Estado:** ✅ Concluída
- **O que foi feito:** `send_video` em `gowa/client.py`, `elif kind == "video"` em `gowa_channel.py`, rota `POST /api/contacts/{phone}/send-video`, `api.sendVideo` e `sendVideo` no `_api` do `ContactDetail`.
- **Como foi feito / decisões:** Confirmei por inspeção do binário (`grep -a -o '/send/[a-z-]+' bin/gowa`) que `/send/video` existe. Mesmo assim a chamada degrada: um `GOWASendError` de tipo `api` (ex.: 404 num GOWA antigo) cai em `send_file`, então o vídeo chega como documento em vez de falhar. Telegram (`_MEDIA_METHOD`) e WhatsApp Cloud já mapeavam vídeo; o widget trata qualquer kind. No frontend, `kind='video'` degrada para documento quando a API efetiva não expõe `sendVideo` — é o que cobre o sandbox.
- **Problemas / pendências:** O sandbox não ganhou rota de vídeo (vídeo solto lá vira documento, sem erro).
- **Verificação:** 4 checks novos ("Contacts — Send Video"): 200, `send_video` chamado, extensão `.mp4` vinda do MIME, e a linha persistida com `media_type=video`. Suíte 1505/0.

---

### Fase F10 — Blindagem XSS: Content-Disposition + allow-list inline 🔴 [sozinha]
**Objetivo:** um arquivo malicioso enviado é baixado, nunca executado no domínio do painel.
**Itens:**
1. `[sequencial]` Servir `/statics/outbox/*` com `Content-Disposition: attachment` para todo `Content-Type` **fora** da allow-list inline segura (`image/jpeg,png,webp,gif`, `video/mp4`, `audio/ogg,mpeg`, `application/pdf`) — via rota dedicada que embrulha o `StaticFiles`, ou header no response.
2. `[sequencial]` Confirmar que a extensão gravada (F1) já vem do MIME validado — impede `x.html` de nascer.
3. `[sequencial]` (a confirmar) Se o mount `/statics` é `StaticFiles` puro ([app.py:466](../server/app.py#L466)), avaliar rota `GET /statics/outbox/{name}` própria que injeta o header (precedente: placeholder de avatar [app.py:451](../server/app.py#L451)).
**Pronto quando:** enviar um `.html`/`.svg` e abrir a URL → o navegador **baixa** (não renderiza/executa); um `.png` legítimo continua inline no chat.

#### Status de execução — Fase F10
**Estado:** ✅ Concluída
- **O que foi feito:** Rota dedicada `GET /statics/outbox/{name}` em `server/app.py`, registrada ANTES do mount `/statics` (mesmo truque do placeholder de avatar, que já é o precedente do repo), forçando `Content-Disposition: attachment` para todo MIME fora de uma allow-list inline.
- **Como foi feito / decisões:** A allow-list é exatamente o que o painel precisa renderizar embutido — `image/jpeg,png,webp,gif`, `video/mp4,webm`, `audio/ogg,mpeg,mp4,wav,webm`, `application/pdf`. Tudo fora dela é servido como `application/octet-stream` **e** com `attachment`, os dois juntos (só o header já bastaria, mas trocar o `Content-Type` fecha a brecha de um navegador que ignore o header). O arquivo não some: continua acessível, só é baixado em vez de renderizado. Path traversal (`/`, `\\`, `..`) e arquivo inexistente devolvem 404. Somado ao F1 (um `.html` nem chega a nascer com essa extensão), são duas barreiras independentes.
- **Problemas / pendências:** A rota cobre `statics/outbox/` (o que o operador envia). `statics/media/` (mídia que o GOWA baixa do cliente) continua no mount puro — é a mesma classe de risco, mas o conteúdo vem do WhatsApp, não de um upload direto, e mexer nela estava fora do escopo do plano.
- **Verificação:** 9 checks novos (seção "Statics outbox — Content-Disposition"): `.png` legítimo segue inline e sem header; `.html`/`.svg`/`.xlsx` viram `attachment` + `octet-stream`; inexistente → 404; traversal não serve. Suíte 1515/0.

---

### Fase F11 — Drop na linha da conversa (sidebar) 🟢 [depende núcleo]
**Objetivo:** arrastar direto para um contato na sidebar e enviar sem abrir a conversa.
**Itens:**
1. `[sequencial]` `onDragOver/onDrop` por linha em [ContactList.js](../web/static/js/components/contacts/ContactList.js); realce da linha sob o cursor.
2. `[sequencial]` No drop, abrir a pré-via (F7) apontada ao `phone` daquela linha (recomendado) — reusa `requestFilesDrop`.
**Pronto quando:** soltar um arquivo sobre "Maria" abre a pré-via de envio p/ Maria; a conversa atual não muda até confirmar.

#### Status de execução — Fase F11
**Estado:** ✅ Concluída
- **O que foi feito:** `onDragEnter/Over/Leave/Drop` por linha em `ContactList.js` (com realce da linha sob o cursor) + handoff `droppedFiles` de `Contacts.js` para `ContactDetail.js`.
- **Como foi feito / decisões:** **Desvio do plano, consciente:** o plano dizia "abre a prévia daquele contato SEM trocar de conversa". Implementado como "**troca** para aquela conversa e abre a prévia lá". Fazer sem trocar exigiria um segundo host de prévia + uma segunda cópia da máquina de envio fora do `ContactDetail` — muito código duplicado para um ganho pequeno. O que o P7 realmente protege (**não enviar às cegas para o contato errado**) está garantido: nada sai sem o operador confirmar na prévia, agora com a conversa do destinatário aberta na frente dele, o que é MAIS explícito que uma prévia flutuante. O handoff usa um `token` incremental (não a identidade dos arquivos), então soltar o mesmo arquivo duas vezes seguidas funciona; e o painel só consome quando `droppedFiles.phone === phone`, evitando despejar na conversa errada se a troca ainda não propagou. Arquivos soltos na sidebar entram como `sendMode='media'` (a linha não tem duas metades).
- **Problemas / pendências:** Sem realce/estado de drop no modo de seleção em lote (desligado ali de propósito).
- **Verificação:** `check_imports.mjs` verde; sintaxe checada. Validação do gesto é manual.

---

### Fase F12 — (opcional) ids no `_ok` 🟢
**Objetivo:** reconciliação fina por `msg_id` (não-regressão se ausente).
**Itens:**
1. `[sequencial]` [contacts.py:1747/1841](../server/routes/contacts.py#L1747): incluir `msg_id`/`media_path` no `_ok` (e opcionalmente `_id`/`conversation_id` no broadcast [messaging_service.py:274](../app/services/messaging_service.py#L274)).
**Pronto quando:** resposta traz `msg_id`; a UI reconcilia por ele quando presente, senão cai no `_localId` (sem regressão).

#### Status de execução — Fase F12
**Estado:** ✅ Concluída
- **O que foi feito:** `send_media` passou a devolver `media_path` junto do `msg_id` já existente, e as 4 rotas (`send-image`, `send-video`, `send-audio`, `send-document`) incluem ambos no `_ok`.
- **Como foi feito / decisões:** Aditivo e não-quebrante (D5): a chave `message` do envelope continua idêntica e a UI segue reconciliando por `_localId` — os ids são um extra para quem quiser reconciliação fina depois. Saiu barato porque `send_media` já calculava as duas coisas internamente.
- **Problemas / pendências:** O frontend ainda NÃO usa `msg_id` na reconciliação (continua no `_localId`) — o campo está disponível para uma evolução futura. O broadcast `new_message` de mídia também segue sem `_id`.
- **Verificação:** 3 checks novos no `/send-image`: `media_path` começa com `statics/outbox/`, a chave `msg_id` existe e a mensagem original foi preservada. Suíte 1518/0.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Guarda global de drop (I0) | Sem ela, soltar fora do alvo **navega o browser e destrói o estado** (perda de dados) | F0 é **Wave 0** — entra antes de qualquer UI de drop. |
| Colisão de nome (G5) | 2 uploads no mesmo ms se **sobrescrevem** | Entropia `uuid4` (I7/F1) **+** envio sequencial (I3) — redundância proposital. |
| Sem limite de corpo | `.read()` carrega o arquivo **inteiro na RAM**; drop de 2 GB derruba o servidor | Teto no cliente (I10) **+** middleware 413 (I9). ⚠️ `read()`→streaming fica como dívida (P2) — o teto de 50 MB limita o dano. |
| XSS armazenado (§2) | `.html`/`.svg` enviado executa same-origin (CSP `unsafe-inline`) | F10: `Content-Disposition: attachment` + extensão do MIME (F1). |
| Persistência `statics/` | `statics/` é bind/named volume; **`persistence_check` só guarda `storages/`**, não `statics/` — deploy pode reportar "persistente" e a mídia sumir | Não introduzir regressão; degradar arquivo faltante (placeholder 200, não 404) como já faz avatar/`MediaWithFallback`. Anotar em docs de deploy. |
| Drift instalado×bundled | Cópias em `storages/plugins/{telegram,whatsapp_cloud,website}` **diferem** de `assets/plugin_examples/` | Vídeo (I11) e qualquer capability nova devem **degradar** quando o código declarante não está instalado (default permissivo no backend / opt-in no frontend). GOWA re-exporta a classe core (sem drift). |
| GOWA áudio sem caption | `/send/audio` **não** tem parâmetro de legenda | Não oferecer legenda p/ áudio (comportamento de hoje); overlay não mostra caption p/ item de áudio. |
| Sandbox arity trap | Args posicionais novos são **silenciosamente descartados** em [Sandbox.js:172](../web/static/js/components/Sandbox.js#L172) | Passar opções novas como **objeto** ou atualizar as 3 linhas. |
| Template `html\`\`` | Crase/`${}` em **comentário** dentro de `html\`...\`` fecha o template e quebra o módulo | `node --input-type=module --check < file`; `node tests/frontend/check_imports.mjs`. |
| Dark mode | Overlay/pré-via novos podem ficar ilegíveis no escuro | `wa-*`/`.wa-field`; checar `bg-black/50`; testar com `.dark` ligado. |
| RBAC aberto | Sem usuário RBAC, os endpoints de mídia ficam **abertos** (memória `rbac-enforce-sem-writer-api-aberta`) | Fora do escopo — não piorar; os gates existentes (`conversation.reply`, `_inbox_send_denied`) são mantidos. |
| Falha parcial de fila | Um item falha no meio de 5 | P4 — recomendação: **continuar**, marcar o item como `failed`, não abortar os demais. |

---

## 7. Perguntas em aberto

- **P1 — Tetos exatos (tamanho/arquivos)?** ⏸️ ADIADO (default proposto). Recomendo **50 MB/arquivo, 10 arquivos/drop**, como constantes no backend espelhadas no cliente. (WhatsApp aceita ~16 MB mídia / ~100 MB documento; 50 MB é um meio-termo seguro dado que não há streaming ainda — P2.) Ajustar depois é trivial. Config global vs hardcoded: começar hardcoded.
- **P2 — Trocar `await file.read()` por streaming-para-disco?** ⏸️ ADIADO por D2. O teto de 50 MB (P1) limita o pico de RAM; streaming (`shutil.copyfileobj` em chunks) é a correção real mas mexe nos 3 endpoints. Fica como **dívida** anotada — reabrir se o teto subir.
- **P3 — Legenda: compartilhada ou por-arquivo?** ✅ DECIDIDO (2026-07-20): **compartilhada** no v1 (uma legenda p/ o drop inteiro; tecnicamente cada request já leva a sua, então por-arquivo é evolução barata depois). WhatsApp Web usa por-arquivo; deixamos como P-futura.
- **P4 — Política de falha parcial na fila?** ⏸️ ADIADO (recomendação). **Continuar** enviando os demais e marcar o item falho como `failed` na bolha (não abortar o lote). Como retry de mídia está desabilitado ([MessageBubble.js:112](../web/static/js/components/contacts/MessageBubble.js#L112)), o operador re-arrasta o item falho — aceitável no v1.
- **P5 — Progresso real por bytes?** ✅ DECIDIDO: **não** no v1. `fetch` não expõe progresso de upload; pseudo-progresso "N de M" (sequencial) basta. XHR mexeria em transporte compartilhado (CSV/zip) — evolução isolada se pedirem.
- **P6 — Semear a legenda com o texto já digitado no composer?** ⏸️ ADIADO (recomendação: **sim**, custo ~zero — [ContactDetail.js:111](../web/static/js/components/contacts/ContactDetail.js#L111)). Nice-to-have de F7.
- **P7 — Drop na sidebar: abre pré-via ou envia direto?** ✅ DECIDIDO: **abre a pré-via** apontada ao contato (não envia às cegas) — evita envio acidental ao contato errado.

---

## 8. Apêndice — arquivos-chave

**Backend**
- [server/routes/contacts.py:1706/1750/1795](../server/routes/contacts.py#L1706) — rotas `send-image/audio/document` (+ nova `send-video` I11); nome-em-disco I7; ids opcionais I13.
- [server/routes/sandbox.py:134](../server/routes/sandbox.py#L134) — mesmo nome-em-disco (I7).
- [server/app.py:493](../server/app.py#L493) — middleware 413 (I9); [:466](../server/app.py#L466)/[:451](../server/app.py#L451) — serving `/statics` + precedente de placeholder p/ Content-Disposition (I8).
- [app/services/messaging_service.py:187](../app/services/messaging_service.py#L187) — tail de envio (broadcast/emit; ids opcionais I13).
- [gowa/client.py](../gowa/client.py) + [channels/providers/gowa_channel.py:302](../channels/providers/gowa_channel.py#L302) — `send_video` (I11).
- [channels/base.py:25](../channels/base.py#L25)/[:308](../channels/base.py#L308) + [channels/outbound.py:91](../channels/outbound.py#L91) — capability `media` (bool) e `send_media` (referência; sem mudança obrigatória).

**Frontend**
- [web/static/js/components/contacts/hooks/useMediaUpload.js:37-252](../web/static/js/components/contacts/hooks/useMediaUpload.js#L37) — fila (I1), `requestFilesDrop`/paste (I2/I6), envio sequencial (I3), validação cliente (I10).
- [web/static/js/components/contacts/ContactDetail.js:350](../web/static/js/components/contacts/ContactDetail.js#L350) — raiz `relative` + overlay de drop (I4); [:128](../web/static/js/components/contacts/ContactDetail.js#L128) wiring do hook; [:111](../web/static/js/components/contacts/ContactDetail.js#L111) seed de legenda.
- [web/static/js/components/contacts/Composer.js:52-125](../web/static/js/components/contacts/Composer.js#L52) — inputs `multiple` (I2) + pré-via multi (I5).
- [web/static/js/components/contacts/ContactList.js](../web/static/js/components/contacts/ContactList.js) — drop na sidebar (I12).
- [web/static/js/components/shell/App.js](../web/static/js/components/shell/App.js) / [web/index.html](../web/index.html) — guarda global (I0).
- [web/static/js/services/api.js:360-406](../web/static/js/services/api.js#L360) + [httpClient.js:120](../web/static/js/services/httpClient.js#L120) — `sendImage/Audio/Document` (+ `sendVideo`); transporte.
- [web/static/js/components/Sandbox.js:172](../web/static/js/components/Sandbox.js#L172) — arity trap (I3).
- [web/static/js/components/contacts/MediaContent.js:14/65](../web/static/js/components/contacts/MediaContent.js#L14) — render (invariante `/`; `<video>` já pronto).

**Referência (não mudar; só ler)**
- [assets/plugin_examples/telegram/channels.py:212](../assets/plugin_examples/telegram/channels.py#L212), [.../whatsapp_cloud/channels.py:391](../assets/plugin_examples/whatsapp_cloud/channels.py#L391), [.../website/channels.py:166](../assets/plugin_examples/website/channels.py#L166) — mapas `send_media` por provider.
- [server/routes/plugins.py:118](../server/routes/plugins.py#L118) — precedente de cap de tamanho.

**Testes**
- [tests/test_endpoints.py:899](../tests/test_endpoints.py#L899) — idiom multipart (`files={...}`, mock `send_file`/`send_image`).
- [tests/fakes.py:32](../tests/fakes.py#L32) — `FakeGowaClient` (`.images`/`.files`/`.audios`/`.sent`).
- `web/static/js/**/*.test.js` — `node --test`; `tests/frontend/check_imports.mjs`.

**Docs a corrigir de passagem** (stale `senditems`→`outbox`): [CLAUDE.md:416](../CLAUDE.md#L416)/[:746](../CLAUDE.md#L746), [Dockerfile:54](../Dockerfile#L54), [docs/DEPLOY_COOLIFY.md:24](../docs/DEPLOY_COOLIFY.md#L24).

---

## 9. Checklist de verificação

- [x] caracterização de `useMediaUpload` verde **antes e depois** de F3 — feita nos módulos puros extraídos (`mediaQueue`/`uploadLimits`), padrão do repo
- [x] `venv/bin/python tests/test_endpoints.py` sem novas falhas no Postgres — **1518 passed / 0 failed** (baseline era 1491/0)
- [x] `node --test` nos módulos puros novos/alterados — 298/298
- [x] `node tests/frontend/check_imports.mjs` verde
- [x] guarda global: soltar fora do alvo **não** navega o browser *(código em F0; conferência visual pendente)*
- [x] `curl -F` acima do teto → **413**; abaixo → envia *(coberto por teste automatizado em vez de curl)*
- [x] 2 uploads simultâneos → nomes de arquivo distintos (sem sobrescrita)
- [x] `.html`/`.svg` enviado → **baixado** (attachment), não executado; `.png` legítimo inline
- [x] drop multi-arquivo: zonas foto/arquivo, grade de prévia, "N de M", reconciliação por bolha *(código completo; conferência visual pendente)*
- [x] `Ctrl+V` de 2 imagens enfileira ambas; colar texto não vira anexo *(código completo; conferência visual pendente)*
- [x] vídeo inline no GOWA vira player; provider sem suporte degrada p/ documento
- [ ] modo escuro legível no overlay e na prévia — **PENDENTE (visual)**
- [ ] reload / back-forward do navegador sem quebrar estado — **PENDENTE (visual)**
- [x] sem segredo em URL/log; acesso a dados só via SQLAlchemy Core com bind params
