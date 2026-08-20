// @ts-check
//
// Media-upload hook (Plano 23 · D3; FILA no plano 64; limites por canal no 65).
// Owns the attach menu, the hidden image/document <input> refs, the pending
// media queue (preview + caption + per-item send mode) and the optimistic send
// of each item (insert temp bubble → call the send API → reconcile _status /
// steer to a template when the 24h window is closed).
//
// Plano 64: `pendingMedia` (um objeto) virou `pendingQueue` (uma lista). Um
// gesto — arrastar, colar ou escolher no seletor — pode trazer N arquivos, e
// cada entrada carrega seu `sendMode` (a zona onde foi solto: "foto/vídeo" vs
// "arquivo"), que decide a rota. O envio é SEQUENCIAL: dá pseudo-progresso
// ("2 de 5") de graça, evita colisão de nome em disco e mantém o tratamento de
// falha parcial simples (falhou um, marca esse e segue os demais — P4).
//
// Plano 65: cada anexo é pré-validado contra os limites que o CANAL declara
// (`mediaLimits`, vindos do payload da conversa) e um arquivo fora do padrão
// abre um popup ANTES de virar bolha. Isso é ortogonal aos tetos do plano 64:
//
//   plano 64 (`uploadLimits`) = "o SERVIDOR aguenta?" — teto global de 50 MB por
//                               arquivo e 10 por gesto, espelhado no middleware
//                               413. Vale para todo canal.
//   plano 65 (`mediaLimits`)  = "este CANAL aceita?"  — formato/tamanho que o
//                               provider declara (a Meta é exigente; GOWA e
//                               Telegram não declaram nada e nunca bloqueiam).
//
// Os dois rodam em sequência no enfileiramento: primeiro o teto global (barato e
// universal), depois a regra do canal.
//
// Behavior-preserving para o caminho de 1 item: mesmas formas de mensagem
// otimista (sandbox vs operator vs nota privada), mesmo blob/object-URL
// handling, mesmo steering de `session_window_closed`.
//
// Plano 124: a legenda deixou de morar aqui. A fila virou uma BANDEJA que
// convive com o compositor (ela não o substitui mais), então o texto digitado
// no compositor É a legenda — `confirmQueue(caption)` a recebe por parâmetro e
// o estado `mediaCaption` deixou de existir. Duas consequências:
//
//   • a janela de 24h fechada NÃO descarta mais a fila — com o texto servindo de
//     legenda, limpar significaria perder texto E anexos de uma vez só por ter
//     esbarrado na janela;
//   • a legenda de uma NOTA PRIVADA carrega as menções internas (@atendente/
//     @time): as rotas `/private-image` e `/private-document` sempre aceitaram
//     `mentions`, só ninguém as mandava daqui.
//
// ⚠️ Nenhuma das duas tem teste automatizado — este repo não tem harness de
// montagem de componente (sem build step, sem jsdom). A parte testável da
// decisão de envio mora em `services/composerSubmit.js`; aqui, a rede é o
// roteiro manual do plano 124.
import { useState, useRef, useEffect } from 'preact/hooks';
import { sendPrivateAudio, sendPrivateImage, sendPrivateDocument } from '../../../services/api.js';
import { buildQueueItems, audioQueueItem, progressLabel } from '../../../services/mediaQueue.js';
import { applyUploadLimits, limitsMessage, MAX_FILES_PER_DROP } from '../../../services/uploadLimits.js';
import { checkMediaFile, limitsSummary, isAttachmentOfKind } from '../../../services/mediaLimits.js';
import { captionTargetIndex } from '../../../services/composerSubmit.js';
import { notify } from '../../../services/notify.js';
import { toComposerNfc } from '../../../utils/composerPaste.js';

/**
 * @param {Object} opts
 * @param {{sendImage:Function, sendAudio:Function, sendDocument?:Function, sendVideo?:Function}} opts.api - effective send API.
 * @param {string} opts.phone
 * @param {any} opts.conversationId
 * @param {any} opts.channelId
 * @param {boolean} opts.sandbox
 * @param {boolean} opts.sessionClosed - 24h window closed (WhatsApp Cloud).
 * @param {string} opts.mode - composer mode ('reply' | 'private'); drives private-audio.
 * @param {boolean} opts.aiReadPrivate - "IA lê" toggle (private mode only).
 * @param {boolean} opts.aiReplyInChat - "IA responde no chat" toggle (private mode only).
 * @param {(updater:(prev:any)=>any)=>void} opts.setContactData
 * @param {(localId:string, updater:(m:any)=>any)=>void} opts.updateMsgByLocalId
 * @param {()=>void} opts.openTemplatePicker
 * @param {()=>Promise<any>|any} [opts.onSent] - chamado após ao menos um ACK bem-sucedido.
 * @param {Object|null} opts.mediaLimits - limites por tipo declarados pelo canal.
 * @param {((text:string)=>any)|null} [opts.collectMentions] - menções internas da legenda (nota privada).
 * @param {(()=>void)|null} [opts.resetMentions] - zera as menções escolhidas após enviar.
 * @param {(()=>void)|null} [opts.onQueued] - um gesto acabou de ACRESCENTAR anexo(s)
 *   à bandeja (plano 124 · F8). Chamado UMA vez por gesto e só quando algo de fato
 *   entrou; o consumidor devolve o foco ao compositor para o `Enter` enviar.
 */
export function useMediaUpload({
  api, phone, conversationId, channelId, sandbox, sessionClosed, currentUser = null,
  mode = 'reply', aiReadPrivate = false, aiReplyInChat = true,
  setContactData, updateMsgByLocalId, openTemplatePicker, onSent = null,
  mediaLimits = null, collectMentions = null, resetMentions = null, onQueued = null,
}) {
  const [sending, setSending] = useState(false);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  // Anexo recusado pelas regras do canal: `{reason, message, detail}` → popup.
  const [rejection, setRejection] = useState(null);
  // Fila de itens pendentes de confirmação. Cada item:
  // { id, file?|blob?, kind:'image'|'video'|'audio'|'document', sendMode, filename,
  //   previewUrl?, _status, error? }
  const [pendingQueue, setPendingQueue] = useState([]);
  // Espelho da fila em ref: o enfileiramento precisa saber quanto espaço sobra
  // ANTES de criar objectURLs, e o cleanup de unmount precisa da lista atual
  // (um `useEffect(..., [])` fecharia sobre a lista inicial, vazia).
  const queueRef = useRef(pendingQueue);
  queueRef.current = pendingQueue;
  // Pseudo-progresso "N de M" do lote em voo (o `fetch` não expõe progresso de
  // bytes — P5; a granularidade é o arquivo).
  const [sentCount, setSentCount] = useState(0);
  const [sendTotal, setSendTotal] = useState(0);
  // A legenda do lote NÃO mora mais aqui (plano 124): é o texto do compositor,
  // entregue a `confirmQueue(caption)` no momento do envio.
  const fileInputRef = useRef(null);
  const docInputRef = useRef(null);
  const videoInputRef = useRef(null);
  const attachMenuRef = useRef(null);

  // Shim de retrocompatibilidade: quem ainda pensa em "um item pendente" lê o
  // primeiro da fila.
  const pendingMedia = pendingQueue.length ? pendingQueue[0] : null;

  // Close the attach menu on outside click
  useEffect(() => {
    if (!attachMenuOpen) return;
    function onDocClick(e) {
      if (attachMenuRef.current && !attachMenuRef.current.contains(e.target)) {
        setAttachMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [attachMenuOpen]);

  function handleAttachClick() {
    // Always show the picker (image vs. arbitrary document).
    if (api.sendDocument) {
      setAttachMenuOpen(o => !o);
    } else {
      fileInputRef.current?.click();
    }
  }

  function pickImage() {
    setAttachMenuOpen(false);
    fileInputRef.current?.click();
  }

  function pickDocument() {
    setAttachMenuOpen(false);
    docInputRef.current?.click();
  }

  function pickVideo() {
    setAttachMenuOpen(false);
    videoInputRef.current?.click();
  }

  // Pré-validação do anexo contra os limites que o CANAL declara (tamanho/formato
  // — no WhatsApp oficial, as regras da Meta). Fora do padrão: abre o popup e o
  // anexo nem entra na fila, então nunca vira bolha com erro. Sandbox e nota
  // privada não saem para o provedor → não são validados.
  function mediaAllowed(file, kind) {
    if (sandbox || mode === 'private') return true;
    const bad = checkMediaFile(file, kind, mediaLimits);
    if (!bad) return true;
    setRejection(bad);
    return false;
  }

  function dismissRejection() {
    setRejection(null);
  }

  // Um arquivo classificado como `document` que o canal NÃO aceita como
  // documento, mas aceita como vídeo/áudio, segue pelo caminho daquele tipo em
  // vez de ser recusado (plano 65). Dirigido pelos limites declarados — o core
  // nunca conhece provider por nome.
  function reroutedKind(file, kind) {
    if (sandbox || mode === 'private' || kind !== 'document') return kind;
    if (!checkMediaFile(file, 'document', mediaLimits)) return kind;
    if (isAttachmentOfKind(file, 'video', mediaLimits)) return 'video';
    if (isAttachmentOfKind(file, 'audio', mediaLimits)) return 'audio';
    // Imagem pelo mesmo motivo: o WhatsApp oficial não aceita JPG/PNG COMO
    // documento, então arrastar uma foto na zona de arquivo era recusado com o
    // popup "Formato de documento não suportado". Enviar como imagem é a única
    // forma de entregá-la — bloquear seria pior que reencaminhar.
    if (isAttachmentOfKind(file, 'image', mediaLimits)) return 'image';
    return kind;
  }

  // ── Porta de entrada única (plano 64 · F4) ──────────────────────
  // Drop, colar e seletor de arquivo passam TODOS por aqui: valida os tetos,
  // classifica cada arquivo pelo `sendMode` do gesto e enfileira.
  /**
   * @param {ArrayLike<any>} files
   * @param {'media'|'file'} sendMode - zona onde foi solto (D1).
   */
  function requestFilesDrop(files, sendMode = 'media') {
    if (sending) return 0;
    const limited = applyUploadLimits(files);
    const warning = limitsMessage(limited);
    if (warning) notify(warning, { kind: 'error' });
    if (!limited.accepted.length) return 0;

    // O teto de quantidade vale para a FILA inteira, não só para este gesto:
    // soltar 6 e depois mais 6 não pode acumular 12. O corte acontece ANTES de
    // montar os itens — criar objectURL para um arquivo que seria descartado
    // vazaria a URL (nada a revogaria depois).
    const room = Math.max(0, MAX_FILES_PER_DROP - queueRef.current.length);
    const admitted = limited.accepted.slice(0, room);
    if (limited.accepted.length > admitted.length) {
      notify(`Só é possível enviar ${MAX_FILES_PER_DROP} arquivos por vez.`, { kind: 'error' });
    }
    if (!admitted.length) return 0;

    const items = buildQueueItems(admitted, sendMode, {
      makePreviewUrl: (f) => { try { return URL.createObjectURL(f); } catch { return null; } },
    });
    // Regra do CANAL (plano 65), item a item. O primeiro recusado abre o popup;
    // os demais recusados são só descartados — abrir N popups seria inutilizável.
    // O objectURL do item descartado é revogado aqui mesmo.
    const keep = [];
    let firstBad = null;
    for (const item of items) {
      const kind = reroutedKind(item.file, item.kind);
      const bad = (sandbox || mode === 'private')
        ? null : checkMediaFile(item.file, kind, mediaLimits);
      if (bad) {
        if (!firstBad) firstBad = bad;
        if (item.previewUrl) { try { URL.revokeObjectURL(item.previewUrl); } catch {} }
        continue;
      }
      if (kind === item.kind) {
        keep.push(item);
        continue;
      }
      // Rerroteado (documento → imagem/áudio/vídeo): o item nasceu como
      // documento e por isso veio SEM `previewUrl` (só imagem/vídeo ganham
      // miniatura no `buildQueueItems`). A prévia precisa da URL local, senão o
      // player abre vazio (0:00) / a miniatura não aparece e o operador não
      // consegue conferir antes de enviar.
      const previewUrl = item.previewUrl
        || (() => { try { return URL.createObjectURL(item.file); } catch { return null; } })();
      keep.push({ ...item, kind, previewUrl });
    }
    if (firstBad) setRejection(firstBad);
    if (!keep.length) return 0;

    // O updater fica PURO (preact pode reexecutá-lo): nada de efeito colateral.
    setPendingQueue(prev => prev.concat(keep));
    // Plano 124 · F8 — aviso de "entrou anexo por um GESTO do operador". É de
    // propósito um callback e não um `useEffect` sobre `pendingQueue.length`:
    // o efeito também dispararia ao remover um item e ao esvaziar a fila no
    // envio, roubando o foco em dois momentos que ninguém pediu.
    //
    // Com algum arquivo RECUSADO o foco não se move: o popup de recusa acabou de
    // abrir, e devolver o foco ao compositor atrás dele deixaria um `Enter`
    // distraído enviar o lote sem o operador ter lido o aviso.
    if (onQueued && !firstBad) onQueued();
    return keep.length;
  }

  /** Compat: caminho antigo de "escolher uma imagem" — vira uma fila de 1. */
  function requestImageSend(file) {
    if (!file) return;
    requestFilesDrop([file], 'media');
  }

  /** Caminho de documento, agora exportado (era inline em handleDocSelected). */
  function requestDocumentSend(file) {
    if (!file) return;
    requestFilesDrop([file], 'file');
  }

  /** Vídeo escolhido no menu de anexo (plano 65) — sempre caminho de vídeo. */
  function requestVideoSend(file) {
    if (!file) return;
    requestFilesDrop([file], 'media');
  }

  function handleFileSelected(e) {
    requestFilesDrop(e.target.files, 'media');
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handleDocSelected(e) {
    requestFilesDrop(e.target.files, 'file');
    if (docInputRef.current) docInputRef.current.value = '';
  }

  function handleVideoSelected(e) {
    requestFilesDrop(e.target.files, 'media');
    if (videoInputRef.current) videoInputRef.current.value = '';
  }

  // Colar (Ctrl+V) unificado com o drop (plano 64 · F6/I6): aceita QUALQUER
  // tipo de arquivo e TODOS os itens do clipboard, não só a 1ª imagem. Colar
  // texto continua caindo no comportamento nativo do textarea.
  function handlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (file) files.push(file);
    }
    if (files.length) {
      e.preventDefault();
      requestFilesDrop(files, 'media');
      return;
    }

    // ── Texto colado em NFD (plano 132 · F7) ───────────────────────────
    //
    // Mora aqui, e não num segundo handler, porque `onPaste` é UM só evento:
    // dois ouvintes disputando o mesmo `preventDefault` seria bug de ordem.
    // Colagem já em NFC — a esmagadora maioria — não é tocada e segue pelo
    // caminho NATIVO do navegador, que é o que funciona hoje.
    const raw = e.clipboardData?.getData?.('text/plain');
    const nfc = toComposerNfc(raw);
    if (nfc === raw) return;

    const el = e.target;
    if (!el || typeof el.value !== 'string') return;
    e.preventDefault();

    // `insertText` insere no caret / substitui a seleção pelo caminho do próprio
    // navegador: preserva a pilha de DESFAZER (um `el.value = …` a mataria) e
    // dispara `input`, então o state do Preact se atualiza sozinho e não há
    // aritmética de índice para errar — que é justamente como o splice do
    // autocomplete errava (F5).
    let ok = false;
    try {
      ok = document.execCommand('insertText', false, nfc);
    } catch { ok = false; }
    if (ok) return;

    // Retaguarda para quem não suporta execCommand: como já demos
    // preventDefault, deixar passar significaria PERDER a colagem.
    const start = el.selectionStart != null ? el.selectionStart : el.value.length;
    const end = el.selectionEnd != null ? el.selectionEnd : start;
    el.value = el.value.slice(0, start) + nfc + el.value.slice(end);
    const caret = start + nfc.length;
    el.setSelectionRange(caret, caret);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // Audio path: the recorder hook produces { type:'audio', blob, filename, previewUrl }.
  //
  // Substituir a fila (em vez de acrescentar) é seguro porque o microfone só
  // existe no compositor quando NÃO há nada pendente — com bandeja cheia o botão
  // já é o de enviar. Se essa condição mudar no `Composer`, isto vira perda
  // silenciosa de anexos e precisa virar "acrescenta" ou "recusa com aviso".
  function setPendingAudio(item) {
    if (!item) return;
    if (!mediaAllowed({ name: item.filename, size: item.blob && item.blob.size }, 'audio')) {
      if (item.previewUrl) { try { URL.revokeObjectURL(item.previewUrl); } catch {} }
      return;
    }
    setPendingQueue([audioQueueItem({
      blob: item.blob, filename: item.filename, previewUrl: item.previewUrl,
    })]);
    // Idem F8: um clipe recém-gravado também responde ao `Enter` (o texto vai
    // como mensagem separada — `text_then_media`).
    if (onQueued) onQueued();
  }

  /** Remove um item da fila (botão "×" na prévia), liberando o objectURL. */
  function removePendingItem(id) {
    setPendingQueue(prev => {
      const item = prev.find(i => i.id === id);
      if (item?.previewUrl) { try { URL.revokeObjectURL(item.previewUrl); } catch {} }
      return prev.filter(i => i.id !== id);
    });
  }

  function cancelPendingMedia() {
    setPendingQueue(prev => {
      for (const item of prev) {
        if (item.previewUrl) { try { URL.revokeObjectURL(item.previewUrl); } catch {} }
      }
      return [];
    });
  }

  // ── Envio de UM item (miolo reusado pela fila) ──────────────────
  /**
   * @param {any} item
   * @param {string} caption
   * @param {{mentions?:any[], mention_inbox?:boolean}|null} [mentions]
   *   Menções internas da legenda — só a NOTA PRIVADA as aceita.
   */
  async function sendOne(item, caption, mentions = null) {
    const isPrivate = mode === 'private';
    const isPrivateAudio = item.kind === 'audio' && isPrivate;
    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const source = item.blob || item.file;
    const localUrl = item.previewUrl
      || (source ? URL.createObjectURL(source) : null);

    // In sandbox the media is "received from the customer" (role 'user');
    // otherwise it is a manual operator send (status='operator').
    const base = sandbox
      ? { role: 'user' }
      : { role: 'assistant', status: 'operator',
          sent_by_name: (currentUser && currentUser.name) || undefined };

    // Nota privada (imagem/documento/áudio) carrega o autor desde a bolha
    // otimista (plano 53) — igual ao envio normal via `base` acima.
    const privateNote = {
      role: 'private_note', status: null,
      sent_by_name: (currentUser && currentUser.name) || undefined,
    };

    // Vídeo só vai inline quando a API efetiva expõe `sendVideo` (o sandbox e
    // clientes antigos não expõem) — senão degrada para documento, que todo
    // provider sabe enviar.
    const kind = (item.kind === 'video' && !api.sendVideo) ? 'document' : item.kind;

    // Menções internas (@atendente / @time) que o operador escreveu na legenda.
    // Só as rotas de nota privada as aceitam — ver o cabeçalho do arquivo.
    const mentionOpts = mentions
      ? { mentions: mentions.mentions, mentionInbox: mentions.mention_inbox }
      : {};

    let optimistic, sendPromise;
    if (kind === 'image' && isPrivate) {
      // Imagem como nota privada (só no painel).
      optimistic = { ...privateNote, content: caption || '[Imagem]',
                     media_type: 'image', media_path: localUrl };
      sendPromise = sendPrivateImage(phone, item.file, caption,
                                     { conversationId, channelId, ...mentionOpts });
    } else if ((kind === 'document' || kind === 'video') && isPrivate) {
      // Documento/vídeo como nota privada (só no painel).
      const docContent = caption
        ? `[Documento enviado: ${item.filename}]\n${caption}`
        : `[Documento enviado: ${item.filename}]`;
      optimistic = { ...privateNote, content: docContent,
                     media_type: 'document', media_path: localUrl };
      sendPromise = sendPrivateDocument(phone, item.file, caption,
                                        { conversationId, channelId, ...mentionOpts });
    } else if (kind === 'image') {
      optimistic = { ...base, content: caption, media_type: 'image', media_path: localUrl };
      sendPromise = api.sendImage(phone, item.file, caption, conversationId, channelId);
    } else if (kind === 'video') {
      optimistic = { ...base, content: caption || '[Vídeo]',
                     media_type: 'video', media_path: localUrl };
      sendPromise = api.sendVideo(phone, item.file, caption, conversationId, channelId);
    } else if (kind === 'document') {
      const verb = sandbox ? 'recebido' : 'enviado';
      const docContent = caption
        ? `[Documento ${verb}: ${item.filename}]\n${caption}`
        : `[Documento ${verb}: ${item.filename}]`;
      optimistic = { ...base, content: docContent,
                     media_type: 'document', media_path: localUrl };
      sendPromise = api.sendDocument(phone, item.file, caption, conversationId, channelId);
    } else if (isPrivateAudio) {
      // Panel-only audio note (role private_note). "IA lê" / "IA responde no chat"
      // toggles ride along so the backend can transcribe + optionally run the AI.
      optimistic = { ...privateNote, content: '[Áudio]',
                     media_type: 'audio', media_path: localUrl };
      sendPromise = sendPrivateAudio(phone, item.blob, item.filename, {
        aiRead: aiReadPrivate, aiReply: aiReplyInChat, conversationId, channelId,
      });
    } else {
      optimistic = { ...base, content: '[Áudio]', media_type: 'audio', media_path: localUrl };
      // `blob` = clipe do gravador; `file` = arquivo anexado que o canal aceita
      // como áudio mas não como documento (rerroteado em `reroutedKind`). Sem o
      // fallback, o anexo chegava aqui com `blob` undefined e o envio falhava.
      sendPromise = api.sendAudio(phone, source, item.filename, conversationId, channelId);
    }
    optimistic = { ...optimistic, ts: Date.now() / 1000, _localId: localId,
                   _status: 'sending', _isLocalBlob: true };

    setContactData(prev => prev ? {
      ...prev,
      messages: [...(prev.messages || []), optimistic],
    } : prev);
    const isPrivateNote = isPrivate
      && (kind === 'image' || kind === 'document' || kind === 'video' || kind === 'audio');
    try {
      const res = await sendPromise;
      if (isPrivateNote) {
        // Plano 53 — padrão serverCopyArrived (mesmo do useComposer): se a cópia
        // do broadcast WS já chegou como linha própria (relógio defasado fura a
        // janela de 30s), dropa a bolha; senão adota ts/_id/msg_id/autor do
        // servidor. `media_path` local (blob) é preservado — P1.
        if (res.ok && res.data) {
          const saved = res.data;
          setContactData(prev => {
            if (!prev || !prev.messages) return prev;
            const serverCopyArrived = saved._id != null
              && prev.messages.some(m => m._id === saved._id && m._localId !== localId);
            const messages = serverCopyArrived
              ? prev.messages.filter(m => m._localId !== localId)
              : prev.messages.map(m => m._localId === localId
                  ? { ...m, _status: null,
                      ...(saved._id != null ? { _id: saved._id } : {}),
                      ...(saved.msg_id ? { msg_id: saved.msg_id } : {}),
                      ...(saved.ts != null ? { ts: saved.ts } : {}),
                      ...(saved.sent_by_name ? { sent_by_name: saved.sent_by_name } : {}) }
                  : m);
            return { ...prev, messages };
          });
          return { ok: true };
        }
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
        return { ok: false, error: res && res.error };
      }
      // Recusa por regra do canal que só dá pra saber no servidor (codec do vídeo,
      // transcode indisponível). Some com a bolha otimista — nada foi enviado — e
      // mostra o mesmo popup da pré-validação (plano 65).
      const limitReason = (res && !res.ok && res.data
        && ['too_big', 'bad_format', 'bad_codec'].includes(res.data.reason))
        ? res.data.reason : null;
      if (limitReason) {
        setContactData(prev => (prev && prev.messages) ? {
          ...prev,
          messages: prev.messages.filter(m => m._localId !== localId),
        } : prev);
        setRejection({
          reason: limitReason === 'too_big' ? 'too_big' : 'bad_format',
          message: res.error || 'Arquivo não compatível com este canal.',
          detail: limitsSummary(kind, mediaLimits && mediaLimits[kind]),
        });
        return { ok: false, error: res.error, rejected: true };
      }

      // Identidade estável na bolha otimista — é o que impede a mídia de
      // aparecer DUPLICADA no fio até o F5.
      //
      // O caminho de TEXTO (`useComposer`) sempre adotou o `msg_id` que o POST
      // devolve; o de mídia nunca adotou, embora `/send-image`, `/send-audio` e
      // `/send-document` também o devolvam. Sem `msg_id` e sem `_id`, a bolha
      // não tem identidade nenhuma, e a reconciliação do broadcast `new_message`
      // cai na heurística legada de `sameMessage`: mesmo papel + mesmo conteúdo
      // dentro de 30s. Ela falha em três situações reais e cada uma vira uma
      // segunda bolha idêntica:
      //   • o upload demora mais de 30s (arquivo grande / uplink lento) — a
      //     bolha carimba `ts` no INÍCIO do envio e o servidor no fim;
      //   • relógio do cliente defasado do servidor (o mesmo bug que o plano 53
      //     consertou nas notas privadas, aqui sem o `_id` que o salvou lá);
      //   • o conteúdo diverge por construção — vídeo (`[Vídeo]` na bolha, `""`
      //     no servidor) e documento sem legenda.
      // Com o `msg_id` adotado, o WS reconcilia pela identidade e a heurística
      // deixa de importar. Sandbox não tem id de provedor: segue no caminho antigo.
      const ackMsgId = (!sandbox && res && res.ok && res.data && res.data.msg_id) || null;
      if (ackMsgId) {
        setContactData(prev => {
          if (!prev || !prev.messages) return prev;
          // A cópia do servidor pode ter chegado ANTES do ACK e não ter casado
          // com a bolha (é exatamente o cenário do bug): aí a bolha é descartada
          // em vez de virar a segunda metade do par.
          const serverCopyArrived = prev.messages.some(
            m => m.msg_id === ackMsgId && m._localId !== localId);
          const messages = serverCopyArrived
            ? prev.messages.filter(m => m._localId !== localId)
            : prev.messages.map(m => m._localId === localId
                ? { ...m, _status: null, status: 'operator', msg_id: ackMsgId }
                : m);
          return { ...prev, messages };
        });
      } else {
        updateMsgByLocalId(localId, () => sandbox
          ? { _status: res.ok ? null : 'failed' }
          : { _status: res.ok ? null : 'failed', status: res.ok ? 'operator' : 'failed' });
      }
      if (res && !res.ok && res.data && res.data.reason === 'session_window_closed'
          && conversationId != null) {
        openTemplatePicker();
        return { ok: false, error: res.error, sessionClosed: true };
      }
      return { ok: !!(res && res.ok), error: res && res.error };
    } catch (err) {
      console.error('Send media error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      return { ok: false, error: String(err) };
    }
  }

  // ── Envio da FILA (sequencial) ──────────────────────────────────
  // Falha parcial (P4): NÃO aborta o lote — marca o item como falho na bolha e
  // segue para o próximo. A exceção é a janela de 24h fechada, que vale para a
  // conversa inteira: aí para e abre o seletor de template.
  /** @param {string} [captionArg] - legenda do lote (o texto do compositor). */
  async function confirmQueue(captionArg = '') {
    if (!pendingQueue.length || sending) return false;
    const isPrivate = mode === 'private';
    // 24h window closed (WhatsApp Cloud): media also requires a template.
    // ⚠️ A fila NÃO é descartada aqui (plano 124): com o texto do compositor
    // servindo de legenda, limpar significaria jogar fora texto E anexos de uma
    // vez só por ter esbarrado na janela. O operador escolhe o template — ou
    // desiste — com o trabalho dele intacto.
    if (sessionClosed && !isPrivate) {
      openTemplatePicker();
      return false;
    }
    const queue = pendingQueue;
    const caption = String(captionArg || '').trim();
    // Menções internas só valem para a nota privada; resolvê-las fora disso
    // gastaria trabalho para um destino que não as aceita.
    const mentions = (isPrivate && caption && collectMentions)
      ? collectMentions(caption) : null;
    setPendingQueue([]);
    setSentCount(0);
    setSendTotal(queue.length);
    setSending(true);

    // A legenda vai num item só, e é o ÚLTIMO que a aceita — regra pura em
    // `captionTargetIndex`, compartilhada com a bandeja para que o selo mostrado
    // ao operador e o envio nunca discordem. `done` é o índice do item corrente
    // (ele só é incrementado depois do envio).
    const captionIndex = caption ? captionTargetIndex(queue) : -1;

    let done = 0;
    let failures = 0;
    let rejected = 0;
    for (const item of queue) {
      const itemCaption = done === captionIndex ? caption : '';
      const res = await sendOne(item, itemCaption, itemCaption ? mentions : null);
      done += 1;
      setSentCount(done);
      if (!res.ok) failures += 1;
      if (res.rejected) rejected += 1;
      if (res.sessionClosed) break;
    }
    // Recusa por limite do canal já tem popup próprio — não vira toast também.
    if (failures > rejected && queue.length > 1) {
      notify(
        failures === queue.length
          ? 'Nenhum arquivo pôde ser enviado.'
          : `${failures} de ${queue.length} arquivos falharam. Arraste-os de novo para tentar outra vez.`,
        { kind: 'error' });
    }
    setSending(false);
    setSentCount(0);
    setSendTotal(0);
    if (mentions && resetMentions) resetMentions();
    const successes = done - failures;
    if (successes > 0 && onSent) {
      try { await onSent(); } catch (_) { /* envio confirmou; recarga é best-effort */ }
    }
    return successes > 0;
  }

  // Libera os objectURLs vivos quando a conversa é trocada / o painel desmonta.
  // O cleanup lê a fila por REF (ver `queueRef` lá em cima).
  useEffect(() => () => {
    for (const item of queueRef.current) {
      if (item.previewUrl) { try { URL.revokeObjectURL(item.previewUrl); } catch {} }
    }
  }, []);

  return {
    sending,
    attachMenuOpen, setAttachMenuOpen, attachMenuRef,
    pendingQueue, setPendingQueue, pendingMedia, setPendingAudio, removePendingItem,
    rejection, dismissRejection,
    sentCount, sendTotal, sendProgressLabel: sending ? progressLabel(sentCount, sendTotal) : '',
    fileInputRef, docInputRef, videoInputRef,
    handleAttachClick, pickImage, pickDocument, pickVideo,
    handleFileSelected, handleDocSelected, handleVideoSelected, handlePaste,
    requestImageSend, requestDocumentSend, requestVideoSend, requestFilesDrop,
    cancelPendingMedia, confirmQueue,
  };
}
