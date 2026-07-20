// @ts-check
//
// Media-upload hook (Plano 23 · D3) — extracted verbatim from ContactDetail.js.
// Owns the attach menu, the hidden image/document <input> refs, the pending-media
// confirmation overlay state (preview + caption), and the optimistic send of an
// image / document / recorded-audio item (insert temp bubble → call the send API
// → reconcile _status / steer to a template when the 24h window is closed).
//
// Behavior-preserving: same optimistic-message shapes (sandbox vs operator),
// same blob/object-URL handling, same `session_window_closed` steering.
import { useState, useRef, useEffect } from 'preact/hooks';
import { sendPrivateAudio, sendPrivateImage, sendPrivateDocument } from '../../../services/api.js';
import { notify } from '../../../services/notify.js';

// Client-side input ceiling (matches the server transcode teto). Larger inputs
// are refused up front; conforming/oversized-but-convertible files pass and the
// server decides (block vs. transcode) per channel capability — plano 65.
const MAX_VIDEO_INPUT_BYTES = 200 * 1024 * 1024;

/**
 * @param {Object} opts
 * @param {{sendImage:Function, sendAudio:Function, sendDocument?:Function}} opts.api - effective send API.
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
 */
export function useMediaUpload({
  api, phone, conversationId, channelId, sandbox, sessionClosed, currentUser = null,
  mode = 'reply', aiReadPrivate = false, aiReplyInChat = true,
  setContactData, updateMsgByLocalId, openTemplatePicker,
}) {
  const [sending, setSending] = useState(false);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  // pendingMedia: { type: 'image'|'audio'|'document'|'video', file?, blob?, filename?, previewUrl? }
  const [pendingMedia, setPendingMedia] = useState(null);
  // Caption typed in the media-confirmation overlay (image/document/video).
  const [mediaCaption, setMediaCaption] = useState('');
  const fileInputRef = useRef(null);
  const docInputRef = useRef(null);
  const videoInputRef = useRef(null);
  const attachMenuRef = useRef(null);

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

  function requestImageSend(file) {
    if (!file || sending || pendingMedia) return;
    const previewUrl = URL.createObjectURL(file);
    setPendingMedia({ type: 'image', file, previewUrl });
  }

  function handleFileSelected(e) {
    const file = e.target.files[0];
    if (file) requestImageSend(file);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handleDocSelected(e) {
    const file = e.target.files[0];
    if (file && !sending && !pendingMedia) {
      setPendingMedia({ type: 'document', file, filename: file.name });
    }
    if (docInputRef.current) docInputRef.current.value = '';
  }

  function handleVideoSelected(e) {
    const file = e.target.files[0];
    if (file && !sending && !pendingMedia) {
      if (file.size > MAX_VIDEO_INPUT_BYTES) {
        notify('Vídeo muito grande (máx. 200 MB). Reduza o arquivo e tente novamente.',
          { kind: 'error' });
      } else {
        const previewUrl = URL.createObjectURL(file);
        setPendingMedia({ type: 'video', file, filename: file.name, previewUrl });
      }
    }
    if (videoInputRef.current) videoInputRef.current.value = '';
  }

  function handlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) requestImageSend(file);
        return;
      }
    }
  }

  // Audio path: the recorder hook produces { type:'audio', blob, filename, previewUrl }.
  function setPendingAudio(item) {
    setPendingMedia(item);
  }

  function cancelPendingMedia() {
    if (pendingMedia?.previewUrl) URL.revokeObjectURL(pendingMedia.previewUrl);
    setPendingMedia(null);
    setMediaCaption('');
  }

  async function confirmPendingMedia() {
    if (!pendingMedia || sending) return;
    // Nota privada com mídia (áudio/imagem/documento) fica só no painel — nunca vai ao
    // contato — então ignora a janela de 24h.
    const isPrivate = mode === 'private';
    const isPrivateAudio = pendingMedia.type === 'audio' && isPrivate;
    // Video has no private-note path — it always goes to the contact.
    if (pendingMedia.type === 'video' && isPrivate) {
      notify('Vídeo não pode ser enviado como nota privada.', { kind: 'error' });
      cancelPendingMedia();
      return;
    }
    // 24h window closed (WhatsApp Cloud): media also requires a template.
    if (sessionClosed && !isPrivate) {
      cancelPendingMedia();
      openTemplatePicker();
      return;
    }
    const media = pendingMedia;
    const caption = mediaCaption.trim();
    setPendingMedia(null);
    setMediaCaption('');
    setSending(true);

    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const localUrl = media.previewUrl
      || (media.blob || media.file ? URL.createObjectURL(media.blob || media.file) : null);

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

    let optimistic, sendPromise;
    if (media.type === 'image' && isPrivate) {
      // Imagem como nota privada (só no painel).
      optimistic = { ...privateNote, content: caption || '[Imagem]',
                     media_type: 'image', media_path: localUrl };
      sendPromise = sendPrivateImage(phone, media.file, caption, { conversationId, channelId });
    } else if (media.type === 'document' && isPrivate) {
      // Documento como nota privada (só no painel).
      const docContent = caption
        ? `[Documento enviado: ${media.filename}]\n${caption}`
        : `[Documento enviado: ${media.filename}]`;
      optimistic = { ...privateNote, content: docContent,
                     media_type: 'document', media_path: localUrl };
      sendPromise = sendPrivateDocument(phone, media.file, caption, { conversationId, channelId });
    } else if (media.type === 'image') {
      optimistic = { ...base, content: caption, media_type: 'image', media_path: localUrl };
      sendPromise = api.sendImage(phone, media.file, caption, conversationId, channelId);
    } else if (media.type === 'document') {
      const verb = sandbox ? 'recebido' : 'enviado';
      const docContent = caption
        ? `[Documento ${verb}: ${media.filename}]\n${caption}`
        : `[Documento ${verb}: ${media.filename}]`;
      optimistic = { ...base, content: docContent,
                     media_type: 'document', media_path: localUrl };
      sendPromise = api.sendDocument(phone, media.file, caption, conversationId, channelId);
    } else if (media.type === 'video') {
      optimistic = { ...base, content: caption || '[Vídeo]',
                     media_type: 'video', media_path: localUrl };
      sendPromise = api.sendVideo(phone, media.file, caption, conversationId, channelId);
    } else if (isPrivateAudio) {
      // Panel-only audio note (role private_note). "IA lê" / "IA responde no chat"
      // toggles ride along so the backend can transcribe + optionally run the AI.
      optimistic = { ...privateNote, content: '[Áudio]',
                     media_type: 'audio', media_path: localUrl };
      sendPromise = sendPrivateAudio(phone, media.blob, media.filename, {
        aiRead: aiReadPrivate, aiReply: aiReplyInChat, conversationId, channelId,
      });
    } else {
      optimistic = { ...base, content: '[Áudio]', media_type: 'audio', media_path: localUrl };
      sendPromise = api.sendAudio(phone, media.blob, media.filename, conversationId, channelId);
    }
    optimistic = { ...optimistic, ts: Date.now() / 1000, _localId: localId,
                   _status: 'sending', _isLocalBlob: true };

    setContactData(prev => prev ? {
      ...prev,
      messages: [...(prev.messages || []), optimistic],
    } : prev);
    const isPrivateNote = isPrivate
      && (media.type === 'image' || media.type === 'document' || media.type === 'audio');
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
        } else {
          updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
        }
      } else {
        updateMsgByLocalId(localId, () => sandbox
          ? { _status: res.ok ? null : 'failed' }
          : { _status: res.ok ? null : 'failed', status: res.ok ? 'operator' : 'failed' });
        if (res && !res.ok && res.data && res.data.reason === 'session_window_closed'
            && conversationId != null) {
          openTemplatePicker();
        } else if (res && !res.ok && res.error) {
          // Surface a clear rejection (e.g. video blocked: too big / bad format /
          // codec — plano 65 F5A) instead of a silent failed bubble.
          notify(res.error, { kind: 'error' });
        }
      }
    } catch (err) {
      console.error('Send media error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
    }
    setSending(false);
  }

  return {
    sending,
    attachMenuOpen, setAttachMenuOpen, attachMenuRef,
    pendingMedia, setPendingMedia, setPendingAudio,
    mediaCaption, setMediaCaption,
    fileInputRef, docInputRef, videoInputRef,
    handleAttachClick, pickImage, pickDocument, pickVideo,
    handleFileSelected, handleDocSelected, handleVideoSelected, handlePaste,
    requestImageSend, cancelPendingMedia, confirmPendingMedia,
  };
}
