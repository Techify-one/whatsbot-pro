import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { sendMessage, retrySend, sendImage, sendAudio, sendPresence } from '../../services/api.js';
import { SendIcon, BackArrowIcon, DefaultAvatar, GroupAvatar, EmojiIcon, AttachIcon, MicIcon, DoubleCheckIcon, ClockIcon, FailedIcon, RetryIcon, StopIcon } from './icons.js';
import { formatBubbleTime } from './utils.js';

const html = htm.bind(h);

// ── Contact Detail (WhatsApp Web chat panel) ─────────────────────

export function ContactDetail({ phone, onBack, messages, info, contact, onAvatarClick, contactTyping, setContactData }) {
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordDuration, setRecordDuration] = useState(0);
  // pendingMedia: { type: 'image'|'audio', file, blob, filename, previewUrl }
  const [pendingMedia, setPendingMedia] = useState(null);
  const chatRef = useRef(null);
  const fileInputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const recordTimerRef = useRef(null);
  const presenceTimerRef = useRef(null);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => { setInput(''); }, [phone]);

  // Send typing presence to contact (debounced)
  function handleInputChange(e) {
    const val = e.target.value;
    setInput(val);
    if (!phone) return;
    // Send "start" on first keystroke, then debounce "stop" after 3s of inactivity
    if (val.trim()) {
      if (!presenceTimerRef.current) {
        sendPresence(phone, 'start').catch(() => {});
      }
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = setTimeout(() => {
        sendPresence(phone, 'stop').catch(() => {});
        presenceTimerRef.current = null;
      }, 3000);
    } else {
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = null;
      sendPresence(phone, 'stop').catch(() => {});
    }
  }

  // Clean up presence timer on unmount or phone change
  useEffect(() => {
    return () => {
      if (presenceTimerRef.current) {
        clearTimeout(presenceTimerRef.current);
        presenceTimerRef.current = null;
        if (phone) sendPresence(phone, 'stop').catch(() => {});
      }
    };
  }, [phone]);

  // Helper to find and update a message by its local ID
  function updateMsgByLocalId(localId, updater) {
    setContactData(prev => {
      if (!prev) return prev;
      const msgs = (prev.messages || []).map(m =>
        m._localId === localId ? { ...m, ...updater(m) } : m
      );
      return { ...prev, messages: msgs };
    });
  }

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    // Stop typing presence
    clearTimeout(presenceTimerRef.current);
    presenceTimerRef.current = null;
    sendPresence(phone, 'stop').catch(() => {});

    setSending(true);
    setInput('');

    // Add message optimistically
    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const msgTs = Date.now() / 1000;
    setContactData(prev => prev ? {
      ...prev,
      messages: [...(prev.messages || []), {
        role: 'assistant', content: text, ts: msgTs,
        _localId: localId, _status: 'sending',
      }],
    } : prev);

    try {
      const res = await sendMessage(phone, text);
      if (res.ok) {
        updateMsgByLocalId(localId, () => ({ _status: null }));
      } else {
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    } catch (err) {
      console.error('Send error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
    }
    setSending(false);
  }

  async function handleRetry(localId, text) {
    updateMsgByLocalId(localId, () => ({ _status: 'sending', status: null }));
    try {
      const res = await retrySend(phone, text);
      if (res.ok) {
        updateMsgByLocalId(localId, () => ({ _status: null, status: null }));
      } else {
        updateMsgByLocalId(localId, () => ({ _status: 'failed', status: 'failed' }));
      }
    } catch (err) {
      console.error('Retry error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed', status: 'failed' }));
    }
  }

  function handleAttachClick() {
    if (fileInputRef.current) fileInputRef.current.click();
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

  function cancelPendingMedia() {
    if (pendingMedia?.previewUrl) URL.revokeObjectURL(pendingMedia.previewUrl);
    setPendingMedia(null);
  }

  async function confirmPendingMedia() {
    if (!pendingMedia || sending) return;
    const media = pendingMedia;
    setPendingMedia(null);
    setSending(true);

    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const localUrl = media.previewUrl || URL.createObjectURL(media.blob || media.file);

    if (media.type === 'image') {
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'assistant', content: '', ts: Date.now() / 1000,
          media_type: 'image', media_path: localUrl, _localId: localId, _status: 'sending', _isLocalBlob: true,
        }],
      } : prev);
      try {
        const res = await sendImage(phone, media.file);
        updateMsgByLocalId(localId, () => ({ _status: res.ok ? null : 'failed' }));
      } catch (err) {
        console.error('Send image error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    } else {
      // audio
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'assistant', content: '[Áudio]', ts: Date.now() / 1000,
          media_type: 'audio', media_path: localUrl, _localId: localId, _status: 'sending', _isLocalBlob: true,
        }],
      } : prev);
      try {
        const res = await sendAudio(phone, media.blob, media.filename);
        updateMsgByLocalId(localId, () => ({ _status: res.ok ? null : 'failed' }));
      } catch (err) {
        console.error('Send audio error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    }
    setSending(false);
  }

  // Convert a WebM/Opus blob to WAV so GOWA accepts it (audio/wav is allowed)
  async function convertToWav(webmBlob) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    try {
      const arrayBuf = await webmBlob.arrayBuffer();
      const audioBuf = await ctx.decodeAudioData(arrayBuf);
      const numCh = audioBuf.numberOfChannels;
      const rate = audioBuf.sampleRate;
      const length = audioBuf.length;
      const bytesPerSample = 2; // 16-bit
      const blockAlign = numCh * bytesPerSample;
      const dataLen = length * blockAlign;
      const buf = new ArrayBuffer(44 + dataLen);
      const v = new DataView(buf);
      // RIFF header
      const w = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
      w(0, 'RIFF'); v.setUint32(4, 36 + dataLen, true);
      w(8, 'WAVE'); w(12, 'fmt ');
      v.setUint32(16, 16, true); v.setUint16(20, 1, true); // PCM
      v.setUint16(22, numCh, true); v.setUint32(24, rate, true);
      v.setUint32(28, rate * blockAlign, true); v.setUint16(32, blockAlign, true);
      v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, dataLen, true);
      // Interleaved PCM samples
      const channels = [];
      for (let c = 0; c < numCh; c++) channels.push(audioBuf.getChannelData(c));
      let off = 44;
      for (let i = 0; i < length; i++) {
        for (let c = 0; c < numCh; c++) {
          const s = Math.max(-1, Math.min(1, channels[c][i]));
          v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
          off += 2;
        }
      }
      return new Blob([buf], { type: 'audio/wav' });
    } finally {
      ctx.close();
    }
  }

  async function handleMicClick() {
    if (recording) {
      // Stop recording
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      return;
    }

    // Start recording — prefer OGG/Opus (accepted by GOWA), fallback to WebM then convert to WAV
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      const useOgg = MediaRecorder.isTypeSupported('audio/ogg;codecs=opus');
      const mimeType = useOgg ? 'audio/ogg;codecs=opus' : 'audio/webm;codecs=opus';
      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        setRecording(false);
        clearInterval(recordTimerRef.current);
        setRecordDuration(0);

        if (chunks.length === 0) return;

        // Prepare audio blob in a format GOWA accepts
        let audioBlob, audioFilename;
        if (useOgg) {
          audioBlob = new Blob(chunks, { type: 'audio/ogg' });
          audioFilename = 'voice.ogg';
        } else {
          const webmBlob = new Blob(chunks, { type: 'audio/webm' });
          audioBlob = await convertToWav(webmBlob);
          audioFilename = 'voice.wav';
        }

        const previewUrl = URL.createObjectURL(audioBlob);
        setPendingMedia({ type: 'audio', blob: audioBlob, filename: audioFilename, previewUrl });
      };

      recorder.start();
      setRecording(true);
      setRecordDuration(0);
      recordTimerRef.current = setInterval(() => setRecordDuration(d => d + 1), 1000);
    } catch (err) {
      console.error('Microphone access error:', err);
    }
  }

  function formatRecordTime(secs) {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  // Empty state — no contact selected
  if (!phone) {
    return html`
      <div class="wa-empty-bg flex flex-col items-center justify-center h-full">
        <div class="mb-8">
          <svg width="250" viewBox="0 0 303 172" class="opacity-20">
            <path fill="#8696a0" d="M229.565 160.229c32.874-12.676 53.009-32.508 53.009-54.669 0-39.356-56.792-71.26-126.87-71.26C85.627 34.3 28.835 66.204 28.835 105.56c0 20.655 17.776 39.174 45.883 51.974a8.372 8.372 0 014.773 5.573l.988 4.89a4.186 4.186 0 006.107 3.312l6.212-3.106a8.372 8.372 0 016.456-.37c12.157 3.96 25.676 6.13 39.95 6.13 7.096 0 14.038-.519 20.772-1.517a8.372 8.372 0 016.164 1.136l7.155 4.479a4.186 4.186 0 006.355-3.438l.247-5.287a8.372 8.372 0 013.636-6.223 8.372 8.372 0 017.258-1.314l17.4 4.64a4.186 4.186 0 005.096-2.013l3.47-6.587a8.372 8.372 0 017.09-4.41z"/>
          </svg>
        </div>
        <h2 class="text-wa-text text-[32px] font-light mb-2">WhatsBot</h2>
        <p class="text-wa-secondary text-[14px] text-center max-w-[450px] leading-[20px]">
          Envie e receba mensagens. Selecione um contato para começar.
        </p>
        <div class="mt-10 flex items-center gap-2 text-wa-secondary text-[12px]">
          <svg viewBox="0 0 10 12" width="10" height="12"><path fill="#8696a0" d="M5.063 0C2.272 0 .006 2.274.006 5.078v1.715L0 6.792v.7l.006.007v.206C.006 9.708 2.272 12 5.063 12h.037C7.89 12 10.1 9.708 10.1 6.905v-.2l.007-.008v-.7l-.007-.001V5.078C10.1 2.274 7.89 0 5.1 0h-.037zm0 1.2h.037c2.146 0 3.837 1.71 3.837 3.878v1.138l-.87.862v.827c0 2.168-1.69 3.895-3.837 3.895h-.037c-2.147 0-3.857-1.727-3.857-3.895v-.827l-.87-.862V5.078c0-2.168 1.71-3.878 3.857-3.878z"/></svg>
          Criptografia de ponta a ponta
        </div>
      </div>
    `;
  }

  const isGroup = contact && contact.is_group;
  const displayName = isGroup ? (contact.group_name || phone) : ((info && info.name) || phone);
  const hasText = input.trim().length > 0;

  return html`
    <div class="flex flex-col h-full">
      <!-- Header -->
      <div class="h-[59px] flex items-center px-4 bg-wa-panel border-b border-wa-border shrink-0">
        <button onClick=${onBack} class="lg:hidden text-wa-icon hover:text-wa-text mr-2 shrink-0">
          <${BackArrowIcon} />
        </button>
        <div onClick=${onAvatarClick} class="w-[40px] h-[40px] rounded-full overflow-hidden shrink-0 mr-[13px] cursor-pointer">
          ${isGroup
            ? html`<${GroupAvatar} size=${40} />`
            : html`<${DefaultAvatar} size=${40} />`
          }
        </div>
        <div class="flex-1 min-w-0 cursor-pointer" onClick=${onAvatarClick}>
          <div class="text-wa-text text-[16px] leading-tight truncate">${displayName}</div>
          ${contactTyping
            ? html`<div class="text-wa-teal text-[13px] leading-tight">${contactTyping === 'audio' ? 'gravando áudio...' : 'digitando...'}</div>`
            : isGroup ? html`<div class="text-wa-secondary text-[13px] leading-tight">Grupo</div>`
            : info && info.name ? html`<div class="text-wa-secondary text-[13px] leading-tight">${phone}</div>` : null
          }
        </div>
      </div>

      <!-- Chat area with doodle pattern -->
      <div ref=${chatRef} class="flex-1 overflow-y-auto wa-scrollbar wa-chat-pattern py-2 px-[4%] lg:px-[7%]">
        ${!messages || messages.length === 0
          ? html`<div class="text-center text-wa-secondary py-8 text-[14px]">
              <span class="bg-white/80 rounded-lg px-3 py-1.5 text-[12.5px] shadow-sm">Nenhuma mensagem ainda</span>
            </div>`
          : messages.map((m, i) => {
              const isUser = m.role === 'user';
              const isTranscription = m.role === 'transcription';
              const isSystemNotice = m.role === 'system_notice';
              const isToolCall = m.role === 'tool_call';
              const isError = m.role === 'error';
              const isFirst = i === 0 || messages[i - 1].role !== m.role;

              if (isTranscription) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #2d1b4e; color: #d4bfff; border: 1px solid #4a2d7a;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
                        Transcrição privada
                      </span>
                      <span>${m.content}</span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isSystemNotice) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #1b2e4e; color: #93c5fd; border: 1px solid #1e40af;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                        Mensagem do Sistema
                      </span>
                      <span>${m.content}</span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isToolCall) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #2d1b0e; color: #fbbf24; border: 1px solid #78350f;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/></svg>
                        Ferramenta IA
                      </span>
                      <span>${m.content}</span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isError) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[85%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #fef2f2; color: #dc2626; border: 1px solid #fecaca;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                        Erro no envio
                      </span>
                      <span>${m.content}</span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              const isFailed = m._status === 'failed' || m.status === 'failed';
              const isSending = m._status === 'sending';

              return html`
                <div key=${m._localId || i} class="flex ${isUser ? 'justify-start' : 'justify-end'} ${isFirst ? 'mt-[12px]' : 'mt-[2px]'}">
                  <div class="wa-bubble max-w-[65%] rounded-[7.5px] px-[9px] pt-[6px] pb-[8px] text-[14.2px] leading-[19px] whitespace-pre-wrap relative ${
                    isUser
                      ? `bg-wa-incoming text-wa-text ${isFirst ? 'msg-tail-in rounded-tl-none' : ''}`
                      : `${isFailed ? 'text-wa-text' : 'bg-wa-outgoing text-wa-text'} ${isFirst ? 'msg-tail-out rounded-tr-none' : ''}`
                  }" style="${isFailed ? 'background: #fce8e8;' : ''}">
                    ${m.media_type === 'image' ? html`
                      <img
                        src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}"
                        alt="Imagem"
                        class="rounded-[4px] max-w-full max-h-[300px] mb-1 cursor-pointer"
                        style="min-width:120px"
                        onClick=${() => window.open(m._isLocalBlob ? m.media_path : '/' + m.media_path, '_blank')}
                        loading="lazy"
                      />
                      ${m.content && m.content !== '[Imagem enviada pelo contato]'
                        ? html`<span>${m.content}</span>`
                        : null}
                    ` : m.media_type === 'audio' ? html`
                      <audio controls preload="none" class="max-w-full mb-1" style="min-width:240px">
                        <source src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}" type="audio/wav" />
                        <source src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}" type="audio/ogg" />
                        <source src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}" type="audio/mpeg" />
                      </audio>
                      ${m.content && m.content !== '[Áudio recebido]' && m.content !== '[Áudio]'
                        ? html`<span class="block text-[12px] text-wa-secondary italic">${m.content}</span>`
                        : null}
                    ` : html`<span>${m.content}</span>`}
                    <span class="float-right ml-[8px] mt-[4px] text-[11px] leading-[15px] whitespace-nowrap text-wa-secondary">
                      ${!isUser ? (
                        isFailed ? html`<${FailedIcon} />${!m.media_type && m._localId ? html`<${RetryIcon} onClick=${() => handleRetry(m._localId, m.content)} />` : ''}` :
                        isSending ? html`<${ClockIcon} />` :
                        html`<${DoubleCheckIcon} />`
                      ) : ''}${formatBubbleTime(m.ts)}
                    </span>
                  </div>
                </div>
              `;
            })
        }
      </div>

      <!-- Hidden file input for image upload -->
      <input
        ref=${fileInputRef}
        type="file"
        accept="image/*"
        class="hidden"
        onChange=${handleFileSelected}
      />

      <!-- Media confirmation overlay -->
      ${pendingMedia ? html`
        <div class="flex flex-col items-center bg-wa-panel border-t border-wa-border px-[16px] py-[12px] shrink-0 gap-[10px]">
          ${pendingMedia.type === 'image' ? html`
            <img src=${pendingMedia.previewUrl} class="max-h-[200px] max-w-full rounded-[8px] object-contain" />
          ` : html`
            <audio controls preload="auto" class="w-full max-w-[320px]">
              <source src=${pendingMedia.previewUrl} type="audio/wav" />
              <source src=${pendingMedia.previewUrl} type="audio/ogg" />
            </audio>
          `}
          <div class="flex gap-[12px]">
            <button
              type="button"
              onClick=${cancelPendingMedia}
              class="px-[16px] py-[6px] rounded-[8px] text-[13px] bg-wa-hover text-wa-text border border-wa-border hover:bg-wa-inputBg transition-colors"
            >Cancelar</button>
            <button
              type="button"
              onClick=${confirmPendingMedia}
              disabled=${sending}
              class="px-[16px] py-[6px] rounded-[8px] text-[13px] bg-wa-outgoing text-wa-text border border-wa-border hover:opacity-90 transition-colors disabled:opacity-50 flex items-center gap-[6px]"
            ><${SendIcon} /> Enviar</button>
          </div>
        </div>
      ` : ''}

      <!-- Input area -->
      ${pendingMedia ? '' : recording ? html`
        <div class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
          <div class="flex-1 flex items-center gap-3 mx-[5px]">
            <span class="w-[10px] h-[10px] rounded-full bg-red-500 animate-pulse shrink-0"></span>
            <span class="text-red-500 text-[15px] font-medium">${formatRecordTime(recordDuration)}</span>
            <span class="text-wa-secondary text-[14px]">Gravando...</span>
          </div>
          <button
            type="button"
            onClick=${handleMicClick}
            class="p-[8px] shrink-0"
          >
            <${StopIcon} />
          </button>
        </div>
      ` : html`
        <form onSubmit=${handleSend} class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
          <button type="button" class="p-[8px] shrink-0" tabindex="-1">
            <${EmojiIcon} />
          </button>
          <button type="button" class="p-[8px] shrink-0" tabindex="-1" onClick=${handleAttachClick}>
            <${AttachIcon} />
          </button>
          <div class="flex-1 mx-[5px]">
            <input
              type="text"
              value=${input}
              onInput=${handleInputChange}
              onPaste=${handlePaste}
              placeholder="Digite uma mensagem"
              disabled=${sending}
              class="w-full bg-wa-inputBg text-wa-text text-[15px] rounded-[8px] px-[12px] py-[9px] border border-wa-border outline-none placeholder-wa-secondary disabled:opacity-50"
            />
          </div>
          ${hasText ? html`
            <button
              type="submit"
              disabled=${sending}
              class="p-[8px] shrink-0 text-wa-iconActive transition-colors disabled:opacity-50"
            >
              <${SendIcon} />
            </button>
          ` : html`
            <button type="button" class="p-[8px] shrink-0 text-wa-icon" tabindex="-1" onClick=${handleMicClick}>
              <${MicIcon} />
            </button>
          `}
        </form>
      `}
    </div>
  `;
}
