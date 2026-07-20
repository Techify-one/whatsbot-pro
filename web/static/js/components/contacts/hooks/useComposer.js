// @ts-check
//
// Composer hook (Plano 23 · D3) — THE crown jewel of the chat panel, extracted
// verbatim from ContactDetail.js. Owns the text input + send modes + optimistic
// send + retry + reply-to quoting + emoji insertion + typing presence.
//
// Highest-risk area — behavior-preserving line by line:
//   • input/mode/private-AI-flags/replyingTo/emoji-open/template-picker state,
//   • optimistic send (temp bubble with _localId → reconcile _status / msg_id on
//     ack; drop the optimistic copy if the server broadcast already arrived with
//     the same msg_id, e.g. a plugin rewrote the outgoing text),
//   • private-note send (panel-only) with IA-read / IA-reply flags,
//   • quoted reply (reply_to_msg_id), retry of a failed manual send,
//   • typing presence (start on first keystroke, debounced stop after 3s),
//   • textarea auto-resize, auto-focus on open, emoji outside-click close,
//   • 24h-window steering to the template picker (Cloud API).
//
// The @mention / quick-reply autocomplete lives in useTokenAutocomplete; this
// hook calls `updateMenus(el, val)` on input and `closeMentionMenu()` on send.
import { useState, useEffect, useRef } from 'preact/hooks';
import { sendPresence, sendPrivateMessage, retrySend } from '../../../services/api.js';
import { toWhatsAppMarkup } from '../../../utils/formatWhatsApp.js';

const INPUT_MAX_HEIGHT = 120;

/**
 * @param {Object} opts
 * @param {{sendText:Function}} opts.api - effective send API (sandbox-aware).
 * @param {string} opts.phone
 * @param {any} opts.conversationId
 * @param {any} opts.channelId
 * @param {boolean} opts.sandbox
 * @param {boolean} opts.sessionClosed - 24h window closed (WhatsApp Cloud).
 * @param {(updater:(prev:any)=>any)=>void} opts.setContactData
 * @param {(localId:string, updater:(m:any)=>any)=>void} opts.updateMsgByLocalId
 * @param {(el:HTMLTextAreaElement, val:string)=>void} opts.updateMenus - autocomplete tick.
 * @param {()=>void} opts.closeMentionMenu - close @mention menu on send.
 * @param {()=>void} opts.openTemplatePicker
 */
export function useComposer({
  api, phone, conversationId, channelId, sandbox, sessionClosed, currentUser = null,
  setContactData, updateMsgByLocalId, updateMenus, closeMentionMenu, openTemplatePicker,
  collectMentions = null, resetMentions = null,
}) {
  const [input, setInput] = useState('');
  const [emojiOpen, setEmojiOpen] = useState(false);
  // mode: 'reply' sends to the contact; 'private' stays in the panel only
  const [mode, setMode] = useState('reply');
  // Private-mode AI flags. aiReadPrivate=false → AI ignores the note entirely.
  // aiReplyInChat only shown when aiReadPrivate is on; off → AI reply stays as private note.
  const [aiReadPrivate, setAiReadPrivate] = useState(false);
  const [aiReplyInChat, setAiReplyInChat] = useState(true);
  // Message being replied to (quoted) — drives the preview bar above the input.
  const [replyingTo, setReplyingTo] = useState(null);
  const inputRef = useRef(null);
  const emojiRef = useRef(null);
  const presenceTimerRef = useRef(null);

  // Reset composer state when switching conversation.
  useEffect(() => {
    setInput('');
    setMode('reply');
    setAiReadPrivate(false);
    setAiReplyInChat(true);
    setReplyingTo(null);
    setEmojiOpen(false);
  }, [phone]);

  // Auto-focus message input when opening a chat
  useEffect(() => {
    if (phone && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [phone]);

  // Auto-resize textarea up to ~6 lines, then scroll
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, INPUT_MAX_HEIGHT) + 'px';
  }, [input]);

  // Close the emoji picker on outside click
  useEffect(() => {
    if (!emojiOpen) return;
    function onDocClick(e) {
      if (emojiRef.current && !emojiRef.current.contains(e.target)) {
        setEmojiOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [emojiOpen]);

  // Clean up presence timer on unmount or phone change
  useEffect(() => {
    return () => {
      if (presenceTimerRef.current) {
        clearTimeout(presenceTimerRef.current);
        presenceTimerRef.current = null;
        if (phone) sendPresence(phone, 'stop', conversationId, channelId).catch(() => {});
      }
    };
  }, [phone]);

  // Insert an emoji at the caret position in the message input (keeps the
  // picker open for multiple picks, WhatsApp-style).
  function insertEmoji(em) {
    const el = inputRef.current;
    const cur = el ? el.value : input;
    const start = (el && el.selectionStart != null) ? el.selectionStart : cur.length;
    const end = (el && el.selectionEnd != null) ? el.selectionEnd : cur.length;
    const newVal = cur.slice(0, start) + em + cur.slice(end);
    setInput(newVal);
    setTimeout(() => {
      if (el) {
        el.focus();
        const caret = start + em.length;
        el.setSelectionRange(caret, caret);
      }
    }, 0);
  }

  // Send typing presence to contact (debounced)
  function handleInputChange(e) {
    const val = e.target.value;
    setInput(val);
    updateMenus(e.target, val);
    if (!phone || sandbox) return;
    // Send "start" on first keystroke, then debounce "stop" after 3s of inactivity
    if (val.trim()) {
      if (!presenceTimerRef.current) {
        sendPresence(phone, 'start', conversationId, channelId).catch(() => {});
      }
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = setTimeout(() => {
        sendPresence(phone, 'stop', conversationId, channelId).catch(() => {});
        presenceTimerRef.current = null;
      }, 3000);
    } else {
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = null;
      sendPresence(phone, 'stop', conversationId, channelId).catch(() => {});
    }
  }

  async function handleSend(e) {
    e.preventDefault();
    closeMentionMenu();
    // Collapse the composer's **bold** authoring markup to WhatsApp's *bold*
    // wire format so the recipient (and the stored/rendered copy) sees clean bold.
    const text = toWhatsAppMarkup(input.trim());
    if (!text) return;

    // 24h window closed (WhatsApp Cloud): free text can't be sent — steer the
    // operator to an approved template instead of letting Meta reject it.
    if (sessionClosed && mode !== 'private') {
      openTemplatePicker();
      return;
    }

    // Stop typing presence
    clearTimeout(presenceTimerRef.current);
    presenceTimerRef.current = null;
    if (!sandbox) sendPresence(phone, 'stop', conversationId, channelId).catch(() => {});

    setInput('');
    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const msgTs = Date.now() / 1000;

    if (mode === 'private') {
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'private_note', content: text, ts: msgTs, status: null,
          sent_by_name: (currentUser && currentUser.name) || undefined,
          _localId: localId, _status: 'sending',
        }],
      } : prev);
      // Menções (@atendente / @time) resolvidas do texto final; zera após enviar.
      const mm = collectMentions ? collectMentions(text) : { mentions: [], mention_inbox: false };
      try {
        const res = await sendPrivateMessage(phone, text, {
          aiRead: aiReadPrivate,
          aiReply: aiReadPrivate ? aiReplyInChat : true,
          conversationId,
          channelId,  // plano 37 (C1): conversa nova em canal não-default não misfila
          mentions: mm.mentions,
          mentionInbox: mm.mention_inbox,
        });
        if (res.ok && res.data) {
          // Plano 53 — mesmo padrão serverCopyArrived do envio normal (abaixo),
          // com identidade `_id`: se a cópia do broadcast WS já chegou (relógio
          // do cliente defasado fura a janela de dedup de 30s e ela é apendada
          // como linha própria), dropa a bolha otimista; senão a bolha adota os
          // campos do servidor (ts/_id/msg_id/autor) e vira o espelho da row.
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
      } catch (err) {
        console.error('Private send error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
      if (resetMentions) resetMentions();
      inputRef.current?.focus();
      return;
    }

    // Quoted reply (only meaningful when the target has a GOWA msg_id).
    const replyTo = (replyingTo && replyingTo.msg_id) ? replyingTo.msg_id : null;
    setReplyingTo(null);

    // Add message optimistically. In sandbox you play the customer (role 'user');
    // otherwise it is a manual operator send (status='operator').
    setContactData(prev => prev ? {
      ...prev,
      messages: [...(prev.messages || []), sandbox
        ? { role: 'user', content: text, ts: msgTs, _localId: localId, _status: 'sending',
            reply_to_msg_id: replyTo || undefined }
        : { role: 'assistant', content: text, ts: msgTs, status: 'operator',
            sent_by_name: (currentUser && currentUser.name) || undefined,
            _localId: localId, _status: 'sending', reply_to_msg_id: replyTo || undefined }],
    } : prev);

    try {
      const res = await api.sendText(phone, text, replyTo, conversationId, channelId);
      if (res.ok) {
        const msgId = res.data?.msg_id || null;
        if (sandbox) {
          updateMsgByLocalId(localId, () => ({ _status: null }));
        } else {
          // A plugin may have rewritten the outgoing text (e.g. appended a
          // signature), so the server's broadcast copy can differ in content.
          // If that copy already arrived (same msg_id), drop our optimistic
          // bubble to avoid a duplicate; otherwise just attach the msg_id.
          setContactData(prev => {
            if (!prev || !prev.messages) return prev;
            const serverCopyArrived = msgId
              && prev.messages.some(m => m.msg_id === msgId && m._localId !== localId);
            const messages = serverCopyArrived
              ? prev.messages.filter(m => m._localId !== localId)
              : prev.messages.map(m => m._localId === localId
                  ? { ...m, _status: null, status: 'operator', msg_id: msgId }
                  : m);
            return { ...prev, messages };
          });
        }
      } else {
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
        // Backend gate raced ahead of the UI (window closed between load and send):
        // steer to a template when we have a conversation to send it through.
        if (res && res.data && res.data.reason === 'session_window_closed'
            && conversationId != null) {
          openTemplatePicker();
        }
      }
    } catch (err) {
      console.error('Send error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
    }
    inputRef.current?.focus();
  }

  async function handleRetry(localId, text) {
    updateMsgByLocalId(localId, () => ({ _status: 'sending', status: 'operator' }));
    try {
      const res = await retrySend(phone, text, conversationId, channelId);
      if (res.ok) {
        updateMsgByLocalId(localId, () => ({ _status: null, status: 'operator' }));
      } else {
        updateMsgByLocalId(localId, () => ({ _status: 'failed', status: 'failed' }));
      }
    } catch (err) {
      console.error('Retry error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed', status: 'failed' }));
    }
  }

  // Stop typing presence imperatively (used when sending media before the form).
  function stopPresence() {
    clearTimeout(presenceTimerRef.current);
    presenceTimerRef.current = null;
  }

  return {
    input, setInput, mode, setMode,
    aiReadPrivate, setAiReadPrivate, aiReplyInChat, setAiReplyInChat,
    replyingTo, setReplyingTo, emojiOpen, setEmojiOpen,
    inputRef, emojiRef,
    insertEmoji, handleInputChange, handleSend, handleRetry, stopPresence,
  };
}
