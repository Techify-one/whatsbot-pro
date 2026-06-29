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

/**
 * @param {Object} opts
 * @param {{sendImage:Function, sendAudio:Function, sendDocument?:Function}} opts.api - effective send API.
 * @param {string} opts.phone
 * @param {any} opts.conversationId
 * @param {any} opts.channelId
 * @param {boolean} opts.sandbox
 * @param {boolean} opts.sessionClosed - 24h window closed (WhatsApp Cloud).
 * @param {(updater:(prev:any)=>any)=>void} opts.setContactData
 * @param {(localId:string, updater:(m:any)=>any)=>void} opts.updateMsgByLocalId
 * @param {()=>void} opts.openTemplatePicker
 */
export function useMediaUpload({
  api, phone, conversationId, channelId, sandbox, sessionClosed,
  setContactData, updateMsgByLocalId, openTemplatePicker,
}) {
  const [sending, setSending] = useState(false);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  // pendingMedia: { type: 'image'|'audio'|'document', file?, blob?, filename?, previewUrl? }
  const [pendingMedia, setPendingMedia] = useState(null);
  // Caption typed in the media-confirmation overlay (image/document only).
  const [mediaCaption, setMediaCaption] = useState('');
  const fileInputRef = useRef(null);
  const docInputRef = useRef(null);
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
    // 24h window closed (WhatsApp Cloud): media also requires a template.
    if (sessionClosed) {
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
      : { role: 'assistant', status: 'operator' };

    let optimistic, sendPromise;
    if (media.type === 'image') {
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
    try {
      const res = await sendPromise;
      updateMsgByLocalId(localId, () => sandbox
        ? { _status: res.ok ? null : 'failed' }
        : { _status: res.ok ? null : 'failed', status: res.ok ? 'operator' : 'failed' });
      if (res && !res.ok && res.data && res.data.reason === 'session_window_closed'
          && conversationId != null) {
        openTemplatePicker();
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
    fileInputRef, docInputRef,
    handleAttachClick, pickImage, pickDocument,
    handleFileSelected, handleDocSelected, handlePaste,
    requestImageSend, cancelPendingMedia, confirmPendingMedia,
  };
}
