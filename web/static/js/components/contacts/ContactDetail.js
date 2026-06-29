import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { sendMessage, retrySend, sendImage, sendAudio, sendDocument, sendPresence, sendPrivateMessage, getGroupMembers, deleteMessage, reactToMessage, getQuickReplies, generateImprovement } from '../../services/api.js';
import { SendIcon, BackArrowIcon, DefaultAvatar, GroupAvatar, EmojiIcon, AttachIcon, MicIcon, SingleCheckIcon, DoubleCheckIcon, ClockIcon, FailedIcon, RetryIcon, StopIcon, InfoIcon } from './icons.js';
import { formatBubbleTime, isSameDay, formatDateSeparator, avatarUrl } from './utils.js';
import { formatWhatsApp } from '../../utils/formatWhatsApp.js';
import { AudioPlayer } from './AudioPlayer.js';
import { MessageContextMenu, CopyIcon, TrashIcon, ReplyIcon, LinkIcon, ImproveIcon, copyToClipboard } from './MessageContextMenu.js';
import { EmojiPicker } from './EmojiPicker.js';
import { ConversationHeaderActions } from './ConversationHeaderActions.js';
import { TemplatePicker } from './TemplatePicker.js';
import { TemplateIcon } from './icons.js';
import { Slot } from '../../plugins/Slot.js';
import { emit as emitClientEvent } from '../../plugins/registry.js';

const html = htm.bind(h);

// Renders an <img>/<video> for a message's media and, if the file fails to load
// (e.g. the server lost the file under statics/ — wiped on a deploy without a
// persistent volume), swaps to a neutral "indisponível" placeholder instead of
// the broken-image icon. Local blobs (optimistic, just-sent) never fall back.
function MediaWithFallback({ kind, src, isLocalBlob, alt, className, style, onClick }) {
  const [failed, setFailed] = useState(false);
  const url = isLocalBlob ? src : '/' + src;
  if (failed && !isLocalBlob) {
    const label = kind === 'video' ? 'Vídeo indisponível'
      : kind === 'sticker' ? 'Figurinha indisponível' : 'Imagem indisponível';
    return html`
      <div class="flex items-center gap-2 rounded-[4px] mb-1 px-3 py-4 bg-wa-hover text-wa-secondary text-[13px]"
           style="min-width:140px" title="O arquivo de mídia não está mais disponível no servidor.">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
          <circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>
        </svg>
        <span>${label}</span>
      </div>`;
  }
  if (kind === 'video') {
    return html`<video controls preload="metadata" src=${url} class=${className}
      style=${style} onError=${() => setFailed(true)}></video>`;
  }
  return html`<img src=${url} alt=${alt} class=${className} style=${style}
    onClick=${onClick} loading="lazy" onError=${() => setFailed(true)} />`;
}

// Quick-reaction emojis shown in the message context menu bar (WhatsApp-style).
const QUICK_REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🙏'];

// The operator's own current reaction on a message (stored under reactor "me").
function myReaction(message) {
  const r = message.reactions;
  if (!r) return null;
  for (const [emoji, reactors] of Object.entries(r)) {
    if (Array.isArray(reactors) && reactors.includes('me')) return emoji;
  }
  return null;
}

// ── Contact Detail (WhatsApp Web chat panel) ─────────────────────

export function ContactDetail({ phone, conversationId = null, channelId = null, onBack, messages, info, contact, onAvatarClick, onOpenConversationInfo = null, contactTyping, aiResponding = false, setContactData, globalTags, groupParticipantsChanged = null, sandbox = false, api = null, scrollToMsg = null, onScrolledToMsg = null }) {
  // Effective send API. Sandbox injects local (no-GOWA) endpoints; the contact
  // chat uses the real ones.
  const _api = {
    sendText: sendMessage, sendImage, sendAudio, sendDocument,
    ...(api || {}),
  };
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordDuration, setRecordDuration] = useState(0);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  // mode: 'reply' sends to the contact; 'private' stays in the panel only
  const [mode, setMode] = useState('reply');
  // Private-mode AI flags. aiReadPrivate=false → AI ignores the note entirely.
  // aiReplyInChat only shown when aiReadPrivate is on; off → AI reply stays as private note.
  const [aiReadPrivate, setAiReadPrivate] = useState(false);
  const [aiReplyInChat, setAiReplyInChat] = useState(true);
  // pendingMedia: { type: 'image'|'audio', file, blob, filename, previewUrl }
  const [pendingMedia, setPendingMedia] = useState(null);
  // Caption typed in the media-confirmation overlay (image/document only).
  const [mediaCaption, setMediaCaption] = useState('');
  // Group @mention autocomplete: list of participants + open menu state.
  const [members, setMembers] = useState([]);
  // mentionMenu: { query, start (index of '@' in input), index (highlighted) } | null
  const [mentionMenu, setMentionMenu] = useState(null);
  // Quick replies (plano 04): global list loaded once + the "/atalho" menu.
  // quickReplyMenu: { query, start (index of '/' in input), index (highlighted) } | null
  const [quickReplies, setQuickReplies] = useState([]);
  const [quickReplyMenu, setQuickReplyMenu] = useState(null);
  // Per-message context menu: { x, y, message, isFromMe } | null
  const [msgMenu, setMsgMenu] = useState(null);
  // Delete confirmation dialog: { message, isFromMe } | null
  const [deleteDialog, setDeleteDialog] = useState(null);
  // Improvement-analysis dialog for a flagged AI reply: { message } | null
  const [improveDialog, setImproveDialog] = useState(null);
  const [improveText, setImproveText] = useState('');
  const [improveLoading, setImproveLoading] = useState(false);
  const [improveError, setImproveError] = useState('');
  // Message being replied to (quoted) — drives the preview bar above the input.
  const [replyingTo, setReplyingTo] = useState(null);
  const chatRef = useRef(null);
  const fileInputRef = useRef(null);
  const docInputRef = useRef(null);
  const attachMenuRef = useRef(null);
  const emojiRef = useRef(null);
  const inputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const recordTimerRef = useRef(null);
  const presenceTimerRef = useRef(null);

  // Remember a message to focus (e.g. opened from a search hit) until it renders,
  // so the messages-driven scroll below jumps to it instead of to the bottom.
  const pendingScrollRef = useRef(null);
  useEffect(() => {
    pendingScrollRef.current = scrollToMsg != null ? String(scrollToMsg) : null;
  }, [scrollToMsg, phone]);

  // Scroll a message into view and flash it briefly. Returns false if the message
  // isn't rendered (e.g. outside the loaded window). Used by the search-hit jump
  // and by clicking a reply quote.
  function focusMessage(mid, { smooth = false } = {}) {
    if (mid == null || !chatRef.current) return false;
    const el = chatRef.current.querySelector(`[data-mid="${mid}"]`);
    if (!el) return false;
    el.scrollIntoView({ block: 'center', behavior: smooth ? 'smooth' : 'auto' });
    // Restart the flash even if it was just highlighted (rapid re-clicks).
    el.classList.remove('wa-msg-highlight');
    void el.offsetWidth;
    el.classList.add('wa-msg-highlight');
    setTimeout(() => el.classList.remove('wa-msg-highlight'), 3000);
    return true;
  }

  useEffect(() => {
    const target = pendingScrollRef.current;
    if (target != null) {
      if (focusMessage(target)) {
        pendingScrollRef.current = null;
        if (onScrolledToMsg) onScrolledToMsg();
      }
      // Either handled, or the target isn't rendered yet — in both cases don't
      // fall through to the bottom-scroll (wait for the next messages update).
      return;
    }
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    setInput('');
    setMode('reply');
    setAiReadPrivate(false);
    setAiReplyInChat(true);
    setReplyingTo(null);
    setEmojiOpen(false);
  }, [phone]);

  // Client-side plugin lifecycle (plano 23 §3.4): emit `ui.conversation.opened`
  // when this chat view mounts/changes and `ui.conversation.closed` on teardown.
  // Minimal + stable payload; fire-and-forget (emit() isolates throwing handlers,
  // never blocks render). Empty phone (welcome screen) emits nothing.
  useEffect(() => {
    if (!phone) return;
    const payload = { conversationId: conversationId ?? null, phone, channelId: channelId || 'default' };
    emitClientEvent('ui.conversation.opened', payload);
    return () => emitClientEvent('ui.conversation.closed', payload);
  }, [phone, conversationId, channelId]);

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

  // Auto-focus message input when opening a chat
  useEffect(() => {
    if (phone && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [phone]);

  // Fetch group participants for @mention autocomplete.
  useEffect(() => {
    setMembers([]);
    setMentionMenu(null);
    if (!phone || sandbox || !(contact && contact.is_group)) return;
    let cancelled = false;
    getGroupMembers(phone)
      .then(res => { if (!cancelled && res && res.ok) setMembers(res.data.members || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [phone, contact && contact.is_group]);

  // A member joined/left the OPEN group. The server already applied the delta
  // (added member with its push name, or dropped a removed one) and ships the
  // authoritative roster in the event — use it directly so a removed member
  // disappears at once and a just-joined one shows its name. Fall back to a
  // forced refetch only if the event somehow carries no member list.
  useEffect(() => {
    if (!groupParticipantsChanged || sandbox) return;
    if (!phone || !(contact && contact.is_group)) return;
    if (groupParticipantsChanged.group_jid !== phone) return;
    if (Array.isArray(groupParticipantsChanged.members)) {
      setMembers(groupParticipantsChanged.members);
      return;
    }
    let cancelled = false;
    getGroupMembers(phone, true)
      .then(res => { if (!cancelled && res && res.ok) setMembers(res.data.members || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [groupParticipantsChanged]);

  // Render message text with WhatsApp formatting, highlighting @mentions of
  // known group members (and @todos). Names come from the participant list.
  function fmt(text) {
    const names = (contact && contact.is_group)
      ? members.map(m => m.name).filter(Boolean)
      : [];
    return formatWhatsApp(text, names);
  }

  // Label shown for a member in the @mention menu. A just-joined member has no
  // saved contact name and no captured pushName yet, so fall back to the phone
  // (or lid) digits — inserting "@<digits>" still resolves to a real mention.
  function mentionLabel(m) {
    return m.name || m.phone || m.lid || '';
  }

  // Build the @mention candidate list for a typed query (pure; used by render + keys).
  // The special "todos" entry maps to @todos (mention everyone).
  function getMentionCandidates(query) {
    const q = (query || '').toLowerCase();
    const list = [];
    if (!q || 'todos'.startsWith(q)) list.push({ special: true, name: 'todos' });
    for (const m of members) {
      const label = mentionLabel(m);
      if (label && label.toLowerCase().includes(q)) list.push(m);
    }
    return list.slice(0, 8);
  }

  // Detect an "@token" at the cursor and open/close the mention menu.
  function updateMentionMenu(el, val) {
    if (sandbox || !(contact && contact.is_group)) { setMentionMenu(null); return; }
    const pos = (el && el.selectionStart != null) ? el.selectionStart : val.length;
    const m = val.slice(0, pos).match(/(?:^|\s)@([\p{L}\p{N}_]*)$/u);
    if (m) setMentionMenu({ query: m[1], start: pos - m[1].length - 1, index: 0 });
    else setMentionMenu(null);
  }

  // Replace the typed "@token" with the chosen mention and close the menu.
  function applyMention(cand) {
    if (!cand || !mentionMenu) return;
    const el = inputRef.current;
    const pos = (el && el.selectionStart != null) ? el.selectionStart : input.length;
    const label = cand.special ? 'todos' : mentionLabel(cand);
    const before = input.slice(0, mentionMenu.start);
    const after = input.slice(pos);
    const insert = '@' + label + ' ';
    const newVal = before + insert + after;
    setInput(newVal);
    setMentionMenu(null);
    setTimeout(() => {
      if (el) {
        el.focus();
        const caret = (before + insert).length;
        el.setSelectionRange(caret, caret);
      }
    }, 0);
  }

  // ── Quick replies (plano 04): load the global list once, refresh on change ──
  useEffect(() => {
    let alive = true;
    function load() {
      getQuickReplies().then(res => { if (alive && res && res.ok) setQuickReplies(res.data || []); });
    }
    load();
    window.addEventListener('whatsbot:quick-replies-changed', load);
    return () => { alive = false; window.removeEventListener('whatsbot:quick-replies-changed', load); };
  }, []);

  // Candidates for a typed "/query" (matches short_code or content; sliced to 8).
  function getQuickReplyCandidates(query) {
    const q = (query || '').toLowerCase();
    return quickReplies.filter(qr =>
      qr.short_code.toLowerCase().includes(q) || (qr.content || '').toLowerCase().includes(q)
    ).slice(0, 8);
  }

  // Detect a "/token" at the cursor and open/close the quick-reply menu. Unlike
  // @mention this works in ANY conversation; opens only when there are matches
  // (so plain messages starting with "/" — e.g. URLs — aren't hijacked).
  function updateQuickReplyMenu(el, val) {
    if (sandbox) { setQuickReplyMenu(null); return; }
    const pos = (el && el.selectionStart != null) ? el.selectionStart : val.length;
    const m = val.slice(0, pos).match(/(?:^|\s)\/([\w-]*)$/);
    if (m && getQuickReplyCandidates(m[1]).length) {
      setQuickReplyMenu({ query: m[1], start: pos - m[1].length - 1, index: 0 });
    } else {
      setQuickReplyMenu(null);
    }
  }

  // Replace the typed "/token" with the chosen content (literal, NOT sent).
  function applyQuickReply(cand) {
    if (!cand || !quickReplyMenu) return;
    const el = inputRef.current;
    const pos = (el && el.selectionStart != null) ? el.selectionStart : input.length;
    const before = input.slice(0, quickReplyMenu.start);
    const after = input.slice(pos);
    const insert = cand.content;
    const newVal = before + insert + after;
    setInput(newVal);
    setQuickReplyMenu(null);
    setTimeout(() => {
      if (el) {
        el.focus();
        const caret = (before + insert).length;
        el.setSelectionRange(caret, caret);
      }
    }, 0);
  }

  // Send typing presence to contact (debounced)
  function handleInputChange(e) {
    const val = e.target.value;
    setInput(val);
    updateMentionMenu(e.target, val);
    updateQuickReplyMenu(e.target, val);
    if (!phone || sandbox) return;
    // Send "start" on first keystroke, then debounce "stop" after 3s of inactivity
    if (val.trim()) {
      if (!presenceTimerRef.current) {
        sendPresence(phone, 'start', conversationId).catch(() => {});
      }
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = setTimeout(() => {
        sendPresence(phone, 'stop', conversationId).catch(() => {});
        presenceTimerRef.current = null;
      }, 3000);
    } else {
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = null;
      sendPresence(phone, 'stop', conversationId).catch(() => {});
    }
  }

  // Clean up presence timer on unmount or phone change
  useEffect(() => {
    return () => {
      if (presenceTimerRef.current) {
        clearTimeout(presenceTimerRef.current);
        presenceTimerRef.current = null;
        if (phone) sendPresence(phone, 'stop', conversationId).catch(() => {});
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

  // Open the per-message context menu at the given screen coords.
  function openMsgMenu(e, message, isFromMe) {
    e.preventDefault();
    e.stopPropagation();
    const x = e.clientX || (e.currentTarget && e.currentTarget.getBoundingClientRect().left) || 0;
    const y = e.clientY || (e.currentTarget && e.currentTarget.getBoundingClientRect().bottom) || 0;
    setMsgMenu({ x, y, message, isFromMe });
  }

  // Copy the (display) text of a message to the clipboard, stripping the
  // "[Sender]: " group prefix the backend adds for LLM context.
  function copyMessageText(message) {
    let text = message.content || '';
    if (typeof text === 'string') {
      const match = text.match(/^\[([^\]]+)\]:\s*([\s\S]*)$/);
      if (match) text = match[2];
    }
    copyToClipboard(text);
  }

  // Permalink estilo Chatwoot: âncora na conversa + id interno da mensagem (a mesma
  // chave que o scroll-to-message usa via data-mid). Prefere a conversa da própria
  // mensagem; cai no prop da conversa aberta. null quando não há como ancorar (msg
  // sem _id ou pré-plano-11 sem conversation_id na visão mesclada) → item desabilitado.
  function messagePermalink(message) {
    if (!message || message._id == null) return null;
    const convId = message.conversation_id != null ? message.conversation_id : conversationId;
    if (convId == null) return null;
    return `${window.location.origin}/conversations/${convId}?message=${message._id}`;
  }

  function copyMessageLink(message) {
    const url = messagePermalink(message);
    if (url) copyToClipboard(url);
  }

  // Open the "Gerar melhoria" dialog for a flagged AI reply.
  function openImprove(message) {
    setImproveDialog({ message });
    setImproveText('');
    setImproveError('');
  }

  // Ask the backend for an improvement analysis. The result arrives as a
  // panel-only "system" message via the WS "new_message" event (no manual
  // insertion needed), so we just close the dialog on success.
  async function submitImprovement() {
    if (!improveDialog || improveLoading) return;
    setImproveLoading(true);
    setImproveError('');
    try {
      const res = await generateImprovement(phone, {
        message: {
          content: improveDialog.message.content,
          ts: improveDialog.message.ts,
          _id: improveDialog.message._id,
        },
        feedback: improveText.trim(),
      });
      if (res && res.ok) {
        setImproveDialog(null);
        setImproveText('');
      } else {
        setImproveError((res && res.error) || 'Falha ao gerar a análise.');
      }
    } catch {
      setImproveError('Erro de conexão.');
    }
    setImproveLoading(false);
  }

  // Locate a quoted message in the current thread by its GOWA msg_id.
  function findQuoted(msgId) {
    if (!msgId || !messages) return null;
    return messages.find(m => m.msg_id === msgId) || null;
  }

  // Build {senderLabel, senderColor, snippet} for a quoted message, mirroring
  // the bubble's own sender/side logic. Returns null when the message is gone.
  function quotedInfo(qmsg) {
    if (!qmsg) return null;
    const isGroupChat = contact && contact.is_group;
    const qIsUser = qmsg.role === 'user';
    let text = qmsg.content || '';
    let qSender = null;
    if (qIsUser && isGroupChat && typeof text === 'string') {
      const match = text.match(/^\[([^\]]+)\]:\s*([\s\S]*)$/);
      if (match) { qSender = match[1]; text = match[2]; }
    }
    if (qmsg.media_type === 'image') text = text || '📷 Foto';
    else if (qmsg.media_type === 'audio') text = '🎤 Áudio';
    else if (qmsg.media_type === 'video') text = text || '🎬 Vídeo';
    else if (qmsg.media_type === 'sticker') text = '🪧 Figurinha';
    else if (qmsg.media_type === 'document') text = '📄 Documento';
    else if (qmsg.media_type === 'location' || qmsg.media_type === 'live_location') text = '📍 Localização';
    const fromMe = sandbox ? qIsUser : !qIsUser;
    const dn = isGroupChat
      ? (contact.group_name || phone)
      : (info && info.name ? info.name.replace(/^~/, '') : phone);
    const senderLabel = sandbox
      ? (qIsUser ? 'Você' : 'IA')
      : (qIsUser ? (qSender || dn) : (qmsg.status === 'operator' ? 'Manual' : 'IA'));
    const senderColor = qIsUser ? '#1f7aec' : (qmsg.status === 'operator' ? '#b45309' : '#047857');
    return { senderLabel, senderColor, fromMe, snippet: (text || '').replace(/\s+/g, ' ').slice(0, 140) };
  }

  // Perform a message deletion. scope: 'me' | 'all'. Optimistically updates the
  // local view; the WS broadcast reconciles other clients.
  async function performDelete(message, scope) {
    const msgId = message.msg_id || null;
    const dbId = message._id || message.id || null;
    const localId = message._localId || null;
    setDeleteDialog(null);
    // Optimistic local update: flag the message as revoked but KEEP it in the list
    // (and its content) — deletes only remove it from WhatsApp, never from our panel.
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      const updated = prev.messages.map(m =>
        ((msgId && m.msg_id === msgId) || (dbId && (m._id === dbId || m.id === dbId))
          || (localId && m._localId === localId))
          ? { ...m, revoked: true, revoke_scope: scope }
          : m
      );
      return { ...prev, messages: updated };
    });
    try {
      if (msgId || dbId) await deleteMessage(phone, { msgId, dbId, scope, conversationId });
    } catch (_) { /* best-effort; WS will reconcile if it succeeded */ }
  }

  // React (or toggle off) on a message. Clicking the current emoji removes it.
  async function performReact(message, emoji) {
    const msgId = message.msg_id;
    if (!msgId) return;
    const current = myReaction(message);
    const next = current === emoji ? '' : emoji; // toggle off when same
    // Optimistic local update: one reaction per reactor (mirror backend).
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      const updated = prev.messages.map(m => {
        if (m.msg_id !== msgId) return m;
        const r = { ...(m.reactions || {}) };
        for (const em of Object.keys(r)) {
          r[em] = (r[em] || []).filter(x => x !== 'me');
          if (!r[em].length) delete r[em];
        }
        if (next) r[next] = [...(r[next] || []), 'me'];
        return { ...m, reactions: Object.keys(r).length ? r : undefined };
      });
      return { ...prev, messages: updated };
    });
    try {
      await reactToMessage(phone, msgId, next, conversationId);
    } catch (_) { /* best-effort; WS reconciles */ }
  }

  function handleKeyDown(e) {
    // When the @mention menu is open, arrows/enter/tab/esc drive it instead of
    // sending the message.
    if (mentionMenu) {
      const cands = getMentionCandidates(mentionMenu.query);
      if (cands.length) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setMentionMenu(mm => ({ ...mm, index: Math.min((mm.index || 0) + 1, cands.length - 1) }));
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setMentionMenu(mm => ({ ...mm, index: Math.max((mm.index || 0) - 1, 0) }));
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          applyMention(cands[Math.min(mentionMenu.index || 0, cands.length - 1)]);
          return;
        }
      }
      if (e.key === 'Escape') { e.preventDefault(); setMentionMenu(null); return; }
    }
    // Quick-reply menu (plano 04): arrows/enter/tab/esc drive it (mutually
    // exclusive with the mention menu — different trigger chars).
    if (quickReplyMenu) {
      const cands = getQuickReplyCandidates(quickReplyMenu.query);
      if (cands.length) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setQuickReplyMenu(mm => ({ ...mm, index: Math.min((mm.index || 0) + 1, cands.length - 1) }));
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setQuickReplyMenu(mm => ({ ...mm, index: Math.max((mm.index || 0) - 1, 0) }));
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          applyQuickReply(cands[Math.min(quickReplyMenu.index || 0, cands.length - 1)]);
          return;
        }
      }
      if (e.key === 'Escape') { e.preventDefault(); setQuickReplyMenu(null); return; }
    }
    // Enter sends; Shift+Enter inserts a line break (default behavior).
    // Ignore while IME composition is in progress.
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !e.repeat) {
      e.preventDefault();
      handleSend(e);
    }
  }

  // Auto-resize textarea up to ~6 lines, then scroll
  const INPUT_MAX_HEIGHT = 120;
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, INPUT_MAX_HEIGHT) + 'px';
  }, [input]);

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

  async function handleSend(e) {
    e.preventDefault();
    setMentionMenu(null);
    const text = input.trim();
    if (!text) return;

    // 24h window closed (WhatsApp Cloud): free text can't be sent — steer the
    // operator to an approved template instead of letting Meta reject it.
    if (sessionClosed && mode !== 'private') {
      setShowTemplatePicker(true);
      return;
    }

    // Stop typing presence
    clearTimeout(presenceTimerRef.current);
    presenceTimerRef.current = null;
    if (!sandbox) sendPresence(phone, 'stop', conversationId).catch(() => {});

    setInput('');
    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const msgTs = Date.now() / 1000;

    if (mode === 'private') {
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'private_note', content: text, ts: msgTs, status: null,
          _localId: localId, _status: 'sending',
        }],
      } : prev);
      try {
        const res = await sendPrivateMessage(phone, text, {
          aiRead: aiReadPrivate,
          aiReply: aiReadPrivate ? aiReplyInChat : true,
          conversationId,
        });
        updateMsgByLocalId(localId, () => ({
          _status: res.ok ? null : 'failed',
          ...(res.ok && res.data && res.data._id ? { _id: res.data._id } : {}),
        }));
      } catch (err) {
        console.error('Private send error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
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
            _localId: localId, _status: 'sending', reply_to_msg_id: replyTo || undefined }],
    } : prev);

    try {
      const res = await _api.sendText(phone, text, replyTo, conversationId, channelId);
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
          setShowTemplatePicker(true);
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

  function handleAttachClick() {
    // Always show the picker (image vs. arbitrary document).
    if (_api.sendDocument) {
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
      setShowTemplatePicker(true);
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
      sendPromise = _api.sendImage(phone, media.file, caption, conversationId, channelId);
    } else if (media.type === 'document') {
      const verb = sandbox ? 'recebido' : 'enviado';
      const docContent = caption
        ? `[Documento ${verb}: ${media.filename}]\n${caption}`
        : `[Documento ${verb}: ${media.filename}]`;
      optimistic = { ...base, content: docContent,
                     media_type: 'document', media_path: localUrl };
      sendPromise = _api.sendDocument(phone, media.file, caption, conversationId, channelId);
    } else {
      optimistic = { ...base, content: '[Áudio]', media_type: 'audio', media_path: localUrl };
      sendPromise = _api.sendAudio(phone, media.blob, media.filename, conversationId, channelId);
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
        setShowTemplatePicker(true);
      }
    } catch (err) {
      console.error('Send media error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
    }
    setSending(false);
  }

  async function handleMicClick() {
    if (recording) {
      // Stop recording
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop();
      }
      return;
    }

    // Start recording — uses opus-recorder to produce real OGG/Opus accepted by WhatsApp
    if (typeof window.Recorder !== 'function') {
      alert('Gravador de áudio indisponível: a biblioteca opus-recorder não foi carregada. Recarregue a página (Ctrl+F5) e tente novamente.');
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert('Seu navegador não permite acesso ao microfone neste contexto. Abra o WhatsBot via HTTPS (ou http://localhost) para gravar áudios.');
      return;
    }
    try {
      const recorder = new window.Recorder({
        encoderPath: '/static/vendor/opus-recorder/encoderWorker.min.js',
        encoderApplication: 2048, // VOIP
        encoderSampleRate: 48000,
        numberOfChannels: 1,
      });
      mediaRecorderRef.current = recorder;

      recorder.onstart = () => {
        setRecording(true);
        setRecordDuration(0);
        recordTimerRef.current = setInterval(() => setRecordDuration(d => d + 1), 1000);
      };

      recorder.ondataavailable = (blob) => {
        setRecording(false);
        clearInterval(recordTimerRef.current);
        setRecordDuration(0);

        if (!blob || blob.size === 0) return;

        const audioBlob = new Blob([blob], { type: 'audio/ogg' });
        const previewUrl = URL.createObjectURL(audioBlob);
        setPendingMedia({ type: 'audio', blob: audioBlob, filename: 'voice.ogg', previewUrl });
      };

      recorder.onstop = () => {
        setRecording(false);
        clearInterval(recordTimerRef.current);
        setRecordDuration(0);
      };

      await recorder.start();
    } catch (err) {
      console.error('Microphone access error:', err);
      setRecording(false);
      clearInterval(recordTimerRef.current);
      setRecordDuration(0);
      const msg = (err && err.name === 'NotAllowedError')
        ? 'Permissão para o microfone foi negada. Habilite o acesso nas configurações do navegador.'
        : `Não foi possível iniciar a gravação: ${err && err.message ? err.message : err}`;
      alert(msg);
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
  const canSend = contact ? (contact.can_send !== false) : true;
  const rawName = info && info.name;
  const isAutoName = !isGroup && rawName && rawName.startsWith('~');
  const displayName = isGroup ? (contact.group_name || phone) : (rawName ? rawName.replace(/^~/, '') : phone);
  const hasText = input.trim().length > 0;
  // Template support (Frente C): capability flag from the conversation payload — ou,
  // ao iniciar uma conversa NOVA pela caixa de entrada escolhida (plano 21), do
  // payload channel-scoped do getContact (ainda sem conversationId). O TemplatePicker
  // opera em "channel mode" (channelId + phone) quando não há conversa.
  // sessionClosed → a janela de texto livre de 24h expirou (ou nunca abriu, no caso de
  // um número novo no Cloud), então só um template pode sair.
  const templatesSupported = !sandbox && !!(contact && contact.templates_supported);
  const sessionClosed = templatesSupported && contact && contact.session_open === false;

  return html`
    <div class="flex flex-col h-full">
      <!-- Header -->
      <div class="h-[59px] flex items-center pl-4 pr-[56px] bg-wa-panel border-b border-wa-border shrink-0">
        <button onClick=${onBack} class="lg:hidden text-wa-icon hover:text-wa-text mr-2 shrink-0">
          <${BackArrowIcon} />
        </button>
        <div onClick=${onAvatarClick} class="w-[40px] h-[40px] rounded-full overflow-hidden shrink-0 mr-[13px] cursor-pointer">
          ${isGroup
            ? html`<${GroupAvatar} size=${40} avatarUrl=${avatarUrl(phone, contact && contact.avatar_v)} />`
            : html`<${DefaultAvatar} size=${40} avatarUrl=${avatarUrl(phone, contact && contact.avatar_v)} />`
          }
        </div>
        <div class="flex-1 min-w-0 cursor-pointer" onClick=${onAvatarClick}>
          <div class="text-wa-text text-[16px] leading-tight truncate flex items-center gap-[6px]">
            <span class=${'truncate' + (isAutoName ? ' underline decoration-1 underline-offset-2' : '')} title=${isAutoName ? 'Nome obtido do WhatsApp (ainda não renomeado)' : null}>${displayName}</span>${contact && contact.tags && contact.tags.length > 0 ? contact.tags.map(tagName => {
              const tagInfo = globalTags && globalTags[tagName];
              const color = tagInfo ? tagInfo.color : '#6b7280';
              return html`<span
                class="text-[9px] font-semibold rounded-full px-[5px] py-[0.5px] leading-[14px] shrink-0"
                style="background: ${color}20; color: ${color}; border: 1px solid ${color}40;"
              >${tagName}</span>`;
            }) : null}
          </div>
          ${aiResponding
            ? html`<div class="text-wa-teal text-[13px] leading-tight font-medium flex items-center gap-1.5">
                <span class="inline-block w-1.5 h-1.5 rounded-full bg-wa-teal animate-pulse"></span>
                <span>IA respondendo…</span>
              </div>`
            : contactTyping
            ? html`<div class="text-wa-teal text-[13px] leading-tight">${contactTyping === 'audio' ? 'gravando áudio...' : 'digitando...'}</div>`
            : isGroup ? html`<div class="text-wa-secondary text-[13px] leading-tight">Grupo</div>`
            : info && info.name ? html`<div class="text-wa-secondary text-[13px] leading-tight">${phone}</div>` : null
          }
        </div>

        <!-- Conversation actions (FF3): resolver / atribuir / transferir / IA. -->
        <${ConversationHeaderActions} phone=${phone} conversationId=${conversationId} sandbox=${sandbox} onOpenConversationInfo=${onOpenConversationInfo} onOpenContactInfo=${onAvatarClick} contactInfo=${info} />

        <!-- Informações da conversa (Onda 2): abre o painel lateral da conversa. -->
        ${!sandbox && onOpenConversationInfo ? html`
          <button
            type="button"
            onClick=${onOpenConversationInfo}
            class="shrink-0 ml-1 text-wa-icon hover:text-wa-text p-[6px] rounded-full hover:bg-wa-hover transition-colors"
            title="Informações da conversa"
          >
            <${InfoIcon} />
          </button>
        ` : null}
      </div>

      <!-- Plugin extension point: banner abaixo do header / acima das mensagens
           (faixa "atendimento atual" — SLA, aviso, etc.). Empty by default. -->
      <${Slot} name="chat.header.banner" ctx=${{ conv: { conversationId, phone, channelId }, conversationId, phone, channelId, contact }} />

      <!-- Chat area with doodle pattern -->
      <div ref=${chatRef} class="flex-1 min-h-0 overflow-y-auto overscroll-contain wa-scrollbar wa-chat-pattern py-2 px-[4%] lg:px-[7%]">
        ${!messages || messages.length === 0
          ? html`<div class="text-center text-wa-secondary py-8 text-[14px]">
              <span class="bg-wa-bg/80 rounded-lg px-3 py-1.5 text-[12.5px] shadow-sm">Nenhuma mensagem ainda</span>
            </div>`
          : messages.map((m, i) => {
              const isUser = m.role === 'user';
              const isTranscription = m.role === 'transcription';
              const isPrivateNote = m.role === 'private_note';
              const isSystemNotice = m.role === 'system_notice';
              const isToolCall = m.role === 'tool_call';
              const isConversationEvent = m.role === 'conversation_event';
              const isSystem = m.role === 'system';
              const isError = m.role === 'error';
              const isFirst = i === 0 || messages[i - 1].role !== m.role;

              const prevTs = i > 0 ? messages[i - 1].ts : null;
              const showDateSep = m.ts && (!prevTs || !isSameDay(prevTs, m.ts));
              const dateSeparator = showDateSep
                ? html`<div key=${`sep-${m.ts}-${i}`} class="flex justify-center my-[12px]">
                    <span class="bg-wa-bg/90 text-wa-secondary text-[12px] font-medium uppercase tracking-wide rounded-[7.5px] px-[12px] py-[5px] shadow-sm">
                      ${formatDateSeparator(m.ts)}
                    </span>
                  </div>`
                : null;

              if (isPrivateNote) {
                const failed = m._status === 'failed';
                const pending = m._status === 'sending';
                return [dateSeparator, html`
                  <div key=${m._localId || i} data-mid=${m._id} class="flex justify-center mt-[4px]">
                    <div
                      onContextMenu=${(e) => openMsgMenu(e, m, true)}
                      class="group max-w-[75%] rounded-[7.5px] px-[11px] pt-[6px] pb-[7px] text-[13px] leading-[18px] whitespace-pre-wrap relative shadow-sm"
                      style="background:#3b266b; color:#ede9fe; border:1px solid #7c3aed; ${failed ? 'opacity:0.7;' : ''}">
                      <button
                        onClick=${(e) => openMsgMenu(e, m, true)}
                        class="absolute top-[2px] right-[2px] opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity rounded-full p-[1px] hover:bg-black/20"
                        title="Opções da mensagem"
                      >
                        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" style="color:#c4b5fd;">
                          <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/>
                        </svg>
                      </button>
                      <span class="flex items-center gap-[5px] text-[10.5px] font-semibold mb-[3px] tracking-wide uppercase" style="color:#c4b5fd;">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
                        Mensagem privada
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
                      <span class="float-right ml-[8px] mt-[3px] text-[10.5px] leading-[14px] whitespace-nowrap" style="color:#a78bfa;">
                        ${pending ? '⏳ ' : (failed ? '⚠ ' : '')}${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `];
              }

              if (isTranscription) {
                return [dateSeparator, html`
                  <div key=${i} data-mid=${m._id} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #2d1b4e; color: #d4bfff; border: 1px solid #4a2d7a;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
                        Transcrição privada
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `];
              }

              if (isSystemNotice) {
                return [dateSeparator, html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #1b2e4e; color: #93c5fd; border: 1px solid #1e40af;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                        Mensagem do Sistema
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `];
              }

              if (isToolCall) {
                return [dateSeparator, html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #2d1b0e; color: #fbbf24; border: 1px solid #78350f;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/></svg>
                        Ferramenta IA
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `];
              }

              if (isConversationEvent) {
                // Lifecycle event (plano 12): centered subtle chip, like WhatsApp's
                // system lines. Content already carries the emoji + PT-BR text.
                // wa-* classes keep it legible in both light and dark themes.
                return [dateSeparator, html`
                  <div key=${i} data-mid=${m._id} class="flex justify-center my-[5px]">
                    <div class="max-w-[80%] rounded-[10px] px-[12px] py-[5px] bg-wa-bg/80 border border-wa-border text-wa-secondary text-[12px] leading-[16px] text-center whitespace-pre-wrap shadow-sm">
                      <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
                      <span class="ml-[6px] text-[10px] opacity-70 whitespace-nowrap">${formatBubbleTime(m.ts)}</span>
                    </div>
                  </div>
                `];
              }

              if (isSystem) {
                // Painel-only "Sistema" card (ex.: análise de melhoria da IA).
                // Cinza explícito — os tons gray-100/300 + text-gray-600/800 têm
                // override no html.dark (custom.css), então fica legível nos 2 temas.
                return [dateSeparator, html`
                  <div key=${i} data-mid=${m._id} class="flex justify-center mt-[4px]">
                    <div class="max-w-[80%] rounded-[10px] px-[12px] pt-[7px] pb-[8px] bg-gray-100 border border-gray-300 text-gray-800 text-[13px] leading-[19px] whitespace-pre-wrap relative shadow-sm">
                      <span class="flex items-center gap-[5px] text-[10.5px] font-semibold mb-[3px] tracking-wide uppercase text-gray-600">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                        Sistema
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
                      <span class="block text-right mt-[3px] text-[10.5px] leading-[14px] whitespace-nowrap text-gray-600 opacity-70">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `];
              }

              if (isError) {
                return [dateSeparator, html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[85%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #fef2f2; color: #dc2626; border: 1px solid #fecaca;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                        Erro no envio
                      </span>
                      <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `];
              }

              const isFailed = m._status === 'failed' || m.status === 'failed';
              const isSending = m._status === 'sending';
              const isOperator = !isUser && m.status === 'operator';

              // In groups, the backend prefixes user content with "[Sender Name]: text"
              // for LLM context. Strip the prefix here and use the sender name as label.
              let displayContent = m.content;
              let groupSender = null;
              if (isUser && isGroup && typeof m.content === 'string') {
                const match = m.content.match(/^\[([^\]]+)\]:\s*([\s\S]*)$/);
                if (match) {
                  groupSender = match[1];
                  displayContent = match[2];
                }
              }

              // Which side the bubble sits on. In sandbox you ARE the customer,
              // so your 'user' messages go right and the IA's replies go left —
              // the opposite of the contact chat (viewed by the operator).
              const isFromMe = sandbox ? isUser : !isUser;
              const senderLabel = sandbox
                ? (isUser ? 'Você' : 'IA')
                : (isUser ? (groupSender || displayName) : (isOperator ? 'Manual' : 'IA'));
              const senderColor = isUser ? '#1f7aec' : (isOperator ? '#b45309' : '#047857');

              return [dateSeparator, html`
                <div key=${m._localId || i} data-mid=${m._id} class="flex ${isFromMe ? 'justify-end' : 'justify-start'} ${isFirst ? 'mt-[12px]' : 'mt-[2px]'} ${(m.reactions && Object.keys(m.reactions).length) ? 'mb-[14px]' : ''}">
                  <div
                    onContextMenu=${(e) => openMsgMenu(e, m, isFromMe)}
                    class="wa-bubble group max-w-[65%] rounded-[7.5px] px-[9px] pt-[6px] pb-[8px] text-[14.2px] leading-[19px] whitespace-pre-wrap relative ${
                    !isFromMe
                      ? `bg-wa-incoming text-wa-text ${isFirst ? 'msg-tail-in rounded-tl-none' : ''}`
                      : `${isFailed ? 'text-wa-text' : 'bg-wa-outgoing text-wa-text'} ${isFirst ? 'msg-tail-out rounded-tr-none' : ''}`
                  }" style="${isFailed ? 'background: #fce8e8;' : ''}">
                    <button
                      onClick=${(e) => openMsgMenu(e, m, isFromMe)}
                      class="absolute top-[2px] right-[2px] opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity rounded-full p-[1px] hover:bg-black/10"
                      title="Opções da mensagem"
                    >
                      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" class="text-wa-secondary">
                        <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/>
                      </svg>
                    </button>
                    <span class="block text-[11px] font-semibold leading-[13px] mb-[2px] truncate" style="color: ${senderColor};">${senderLabel}</span>
                    ${(!m.revoked && m.reply_to_msg_id) ? (() => {
                      const qmsg = findQuoted(m.reply_to_msg_id);
                      const q = quotedInfo(qmsg);
                      const accent = q ? q.senderColor : '#8696a0';
                      const canJump = !!(qmsg && qmsg._id != null);
                      return html`
                        <div
                          onClick=${canJump ? ((e) => { e.stopPropagation(); focusMessage(qmsg._id, { smooth: true }); }) : null}
                          class="flex rounded-[4px] overflow-hidden mb-[4px] max-w-full ${canJump ? 'cursor-pointer hover:brightness-95' : ''}"
                          style="background: rgba(0,0,0,0.06);"
                          title=${canJump ? 'Ir para a mensagem' : ''}
                        >
                          <div class="w-[4px] shrink-0" style="background:${accent};"></div>
                          <div class="px-[8px] py-[3px] min-w-0">
                            <div class="text-[12px] font-semibold leading-[15px] truncate" style="color:${accent};">${q ? q.senderLabel : 'Mensagem'}</div>
                            <div class="text-[12.5px] leading-[16px] text-wa-secondary truncate">${q ? q.snippet : 'Mensagem original indisponível'}</div>
                          </div>
                        </div>
                      `;
                    })() : ''}
                    ${m.revoked ? '' : m.media_type === 'image' ? html`
                      <${MediaWithFallback} kind="image"
                        src=${m.media_path} isLocalBlob=${m._isLocalBlob} alt="Imagem"
                        className="rounded-[4px] max-w-full max-h-[300px] mb-1 cursor-pointer"
                        style="min-width:120px"
                        onClick=${() => window.open(m._isLocalBlob ? m.media_path : '/' + m.media_path, '_blank')} />
                      ${displayContent && displayContent !== '[Imagem enviada pelo contato]' && !displayContent.startsWith('[Descrição da imagem]')
                        ? html`<span dangerouslySetInnerHTML=${{ __html: fmt(displayContent)}}></span>`
                        : null}
                    ` : m.media_type === 'audio' ? html`
                      <${AudioPlayer} src=${m.media_path} isLocalBlob=${m._isLocalBlob} />
                      ${displayContent && displayContent !== '[Áudio recebido]' && displayContent !== '[Áudio]' && !displayContent.startsWith('[Transcrição do áudio]')
                        ? html`<span class="block text-[12px] text-wa-secondary italic" dangerouslySetInnerHTML=${{ __html: fmt(displayContent)}}></span>`
                        : null}
                    ` : m.media_type === 'video' ? html`
                      <${MediaWithFallback} kind="video"
                        src=${m.media_path} isLocalBlob=${m._isLocalBlob}
                        className="rounded-[4px] max-w-full max-h-[320px] mb-1"
                        style="min-width:180px" />
                      ${displayContent && !displayContent.startsWith('[Vídeo')
                        ? html`<span dangerouslySetInnerHTML=${{ __html: fmt(displayContent)}}></span>`
                        : null}
                    ` : m.media_type === 'sticker' ? html`
                      <${MediaWithFallback} kind="sticker"
                        src=${m.media_path} isLocalBlob=${m._isLocalBlob} alt="Sticker"
                        className="max-w-[160px] max-h-[160px] mb-1" />
                    ` : (m.media_type === 'location' || m.media_type === 'live_location') ? (() => {
                        // media_path here is "geo:lat,lng" (see _extract_media)
                        const m_path = m.media_path || '';
                        const coords = m_path.startsWith('geo:') ? m_path.slice(4) : '';
                        const mapsUrl = coords
                          ? `https://www.google.com/maps?q=${encodeURIComponent(coords)}`
                          : null;
                        return html`
                          <div class="flex flex-col gap-1">
                            <a
                              href=${mapsUrl || '#'}
                              target="_blank"
                              rel="noopener noreferrer"
                              class="text-wa-teal text-[13px] underline"
                            >📍 ${displayContent || coords || 'Localização'}</a>
                          </div>
                        `;
                      })() : m.media_type === 'document' ? (() => {
                        const docUrl = m._isLocalBlob ? m.media_path : '/' + m.media_path;
                        // content = "[Documento recebido: nome.ext]" + opcional "\nlegenda"
                        const dc = displayContent || '';
                        const mm = dc.match(/^\[Documento (?:recebido|enviado): ([^\]]+)\]\n?([\s\S]*)$/);
                        const docName = mm ? mm[1] : 'Documento';
                        const docCaption = (mm ? mm[2] : dc).trim();
                        return html`
                          <div class="flex flex-col gap-1">
                            <a
                              href=${docUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              class="flex items-center gap-1 text-wa-teal text-[13px] underline break-all"
                            >📄 ${docName}</a>
                            ${docCaption
                              ? html`<span dangerouslySetInnerHTML=${{ __html: fmt(docCaption)}}></span>`
                              : null}
                          </div>
                        `;
                      })() : html`<span dangerouslySetInnerHTML=${{ __html: fmt(displayContent)}}></span>`}
                    ${m.revoked ? html`
                      <span class="italic text-wa-secondary flex items-center gap-[5px] text-[12px] mt-[2px]">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8 0-1.85.63-3.55 1.69-4.9L16.9 18.31C15.55 19.37 13.85 20 12 20zm6.31-3.1L7.1 5.69C8.45 4.63 10.15 4 12 4c4.41 0 8 3.59 8 8 0 1.85-.63 3.55-1.69 4.9z"/></svg>
                        ${m.revoke_scope === 'me' ? 'apagado para mim no WhatsApp' : 'apagado para todos no WhatsApp'}
                      </span>
                    ` : ''}
                    <span class="float-right ml-[8px] mt-[4px] text-[11px] leading-[15px] whitespace-nowrap text-wa-secondary">
                      ${(!isUser && !sandbox) ? (() => {
                        if (isFailed) return html`<${FailedIcon} />${!m.media_type && m._localId ? html`<${RetryIcon} onClick=${() => handleRetry(m._localId, m.content)} />` : ''}`;
                        if (isSending) return html`<${ClockIcon} />`;
                        const st = m.status || m._status;
                        if (st === 'sent') return html`<${SingleCheckIcon} />`;
                        if (st === 'delivered') return html`<${DoubleCheckIcon} color="#92a58c" />`;
                        if (st === 'read') return html`<${DoubleCheckIcon} />`;
                        if (st === 'operator') return html`<${DoubleCheckIcon} color="#92a58c" />`;
                        return html`<${DoubleCheckIcon} />`;
                      })() : ''}${formatBubbleTime(m.ts)}
                    </span>
                    ${(m.reactions && Object.keys(m.reactions).length) ? (() => {
                      const entries = Object.entries(m.reactions).filter(([, rs]) => rs && rs.length);
                      const total = entries.reduce((n, [, rs]) => n + rs.length, 0);
                      const mine = myReaction(m);
                      return html`
                        <button
                          onClick=${(e) => openMsgMenu(e, m, isFromMe)}
                          class="absolute -bottom-[11px] ${isFromMe ? 'right-[6px]' : 'left-[6px]'} bg-wa-panel border border-wa-border rounded-full px-[5px] py-[1px] text-[12px] leading-[16px] shadow-sm flex items-center gap-[1px]"
                          title="${mine ? 'Sua reação: ' + mine : 'Reações'}"
                        >
                          ${entries.map(([em]) => html`<span key=${em}>${em}</span>`)}
                          ${total > 1 ? html`<span class="text-wa-secondary ml-[1px]">${total}</span>` : ''}
                        </button>
                      `;
                    })() : ''}
                  </div>
                </div>
              `];
            })
        }
      </div>

      <!-- Hidden file inputs for image / document upload -->
      <input
        ref=${fileInputRef}
        type="file"
        accept="image/*"
        class="hidden"
        onChange=${handleFileSelected}
      />
      <input
        ref=${docInputRef}
        type="file"
        class="hidden"
        onChange=${handleDocSelected}
      />

      <!-- Media confirmation overlay -->
      ${pendingMedia && canSend ? html`
        <div class="flex flex-col items-center bg-wa-panel border-t border-wa-border px-[16px] py-[12px] shrink-0 gap-[10px]">
          ${pendingMedia.type === 'image' ? html`
            <img src=${pendingMedia.previewUrl} class="max-h-[200px] max-w-full rounded-[8px] object-contain" />
          ` : pendingMedia.type === 'document' ? html`
            <div class="flex items-center gap-[8px] bg-wa-inputBg border border-wa-border rounded-[8px] px-[14px] py-[10px] max-w-full">
              <span class="text-[22px]">📄</span>
              <span class="text-[14px] text-wa-text break-all">${pendingMedia.filename}</span>
            </div>
          ` : html`
            <div class="w-full max-w-[320px]">
              <${AudioPlayer} src=${pendingMedia.previewUrl} isLocalBlob=${true} />
            </div>
          `}
          ${pendingMedia.type !== 'audio' ? html`
            <input
              type="text"
              class="wa-field w-full max-w-[420px] rounded-[8px] px-[12px] py-[8px] text-[14px] border border-wa-border"
              placeholder="Adicionar uma legenda (opcional)"
              value=${mediaCaption}
              onInput=${(e) => setMediaCaption(e.target.value)}
              onKeyDown=${(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); confirmPendingMedia(); } }}
            />
          ` : ''}
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
      ${!canSend ? html`
        <div class="flex items-center justify-center px-[10px] py-[14px] bg-wa-panel min-h-[62px] shrink-0 border-t border-wa-border">
          <span class="text-wa-secondary text-[14px] flex items-center gap-[6px]">
            <svg class="w-[16px] h-[16px]" viewBox="0 0 24 24" fill="currentColor">
              <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/>
            </svg>
            Você não pode enviar mensagens neste grupo
          </span>
        </div>
      ` : pendingMedia ? '' : recording ? html`
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
        ${!sandbox ? html`
        <div class="flex items-center gap-[10px] flex-wrap px-[14px] pt-[7px] pb-[3px] bg-wa-panel shrink-0">
          <div class="inline-flex items-center gap-[2px] p-[3px] rounded-full" style="background:#111b21;">
            <button
              type="button"
              onClick=${() => setMode('reply')}
              class="text-[12px] font-medium px-[14px] py-[4px] rounded-full transition-colors"
              style="background:${mode === 'reply' ? '#005c4b' : 'transparent'}; color:${mode === 'reply' ? '#ffffff' : '#aebac1'};"
            >Responder</button>
            <button
              type="button"
              onClick=${() => setMode('private')}
              class="text-[12px] font-medium px-[14px] py-[4px] rounded-full transition-colors flex items-center gap-[5px]"
              style="background:${mode === 'private' ? '#7c3aed' : 'transparent'}; color:${mode === 'private' ? '#ffffff' : '#aebac1'};"
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
              Mensagem Privada
            </button>
          </div>
          ${mode === 'private' ? html`
            <label class="inline-flex items-center gap-[6px] cursor-pointer select-none" title="Quando ligado, a IA processa a mensagem privada como instrução.">
              <input
                type="checkbox"
                class="sr-only peer"
                checked=${aiReadPrivate}
                onChange=${e => setAiReadPrivate(e.target.checked)}
              />
              <div class="relative w-[28px] h-[16px] bg-gray-500 rounded-full peer-checked:bg-violet-500 transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-[12px] after:w-[12px] after:transition-transform peer-checked:after:translate-x-[12px]"></div>
              <span class="text-[12px] text-wa-secondary">IA lê</span>
            </label>
            ${aiReadPrivate ? html`
              <label class="inline-flex items-center gap-[6px] cursor-pointer select-none" title="Quando ligado, a IA responde no chat do contato. Quando desligado, a resposta fica apenas como nota privada.">
                <input
                  type="checkbox"
                  class="sr-only peer"
                  checked=${aiReplyInChat}
                  onChange=${e => setAiReplyInChat(e.target.checked)}
                />
                <div class="relative w-[28px] h-[16px] bg-gray-500 rounded-full peer-checked:bg-violet-500 transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-[12px] after:w-[12px] after:transition-transform peer-checked:after:translate-x-[12px]"></div>
                <span class="text-[12px] text-wa-secondary">IA responde no chat</span>
              </label>
            ` : ''}
          ` : ''}
        </div>
        ` : ''}
        ${(replyingTo && mode !== 'private') ? (() => {
          const q = quotedInfo(replyingTo);
          const accent = q ? q.senderColor : '#8696a0';
          return html`
            <div class="px-[14px] pt-[6px] bg-wa-panel shrink-0">
              <div class="flex items-stretch rounded-[6px] overflow-hidden" style="background:#1f2c33;">
                <div class="w-[4px] shrink-0" style="background:${accent};"></div>
                <div class="px-[10px] py-[5px] min-w-0 flex-1">
                  <div class="text-[12.5px] font-semibold leading-[16px] truncate" style="color:${accent};">${q ? q.senderLabel : 'Mensagem'}</div>
                  <div class="text-[13px] leading-[17px] text-wa-secondary truncate">${q ? q.snippet : ''}</div>
                </div>
                <button
                  type="button"
                  onClick=${() => setReplyingTo(null)}
                  class="px-[12px] text-wa-secondary hover:text-wa-text shrink-0 text-[18px] leading-none"
                  title="Cancelar resposta"
                >✕</button>
              </div>
            </div>
          `;
        })() : ''}
        ${sessionClosed ? html`
          <div class="px-[14px] py-[6px] bg-wa-panel shrink-0">
            <div class="text-[12px] text-wa-secondary bg-wa-bg border border-wa-border rounded-[6px] px-3 py-1.5">
              Fora da janela de 24h: só é possível enviar um ${' '}
              <button type="button" onClick=${() => setShowTemplatePicker(true)} class="text-wa-teal underline font-medium">template aprovado</button>.
            </div>
          </div>
        ` : ''}
        <form onSubmit=${handleSend} class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
          <div ref=${emojiRef} class="relative shrink-0">
            <button
              type="button"
              class="p-[8px] transition-colors ${emojiOpen ? 'text-wa-teal' : ''}"
              tabindex="-1"
              onClick=${() => setEmojiOpen(o => !o)}
              title="Emojis"
            >
              <${EmojiIcon} />
            </button>
            ${emojiOpen ? html`
              <div class="absolute bottom-[48px] left-0 z-30">
                <${EmojiPicker} onPick=${insertEmoji} />
              </div>
            ` : ''}
          </div>
          ${mode === 'private' ? '' : html`
            <div ref=${attachMenuRef} class="relative shrink-0">
              <button type="button" class="p-[8px] ${sessionClosed ? 'opacity-40 cursor-not-allowed' : ''}" tabindex="-1"
                disabled=${sessionClosed} onClick=${handleAttachClick}>
                <${AttachIcon} />
              </button>
              ${attachMenuOpen ? html`
                <div class="absolute bottom-[44px] left-0 bg-wa-panel border border-wa-border rounded-[8px] shadow-lg py-[4px] min-w-[160px] z-20">
                  <button type="button" onClick=${pickImage}
                    class="w-full text-left px-[14px] py-[8px] text-[14px] text-wa-text hover:bg-wa-hover flex items-center gap-[8px]">
                    <span class="text-[16px]">🖼️</span> Imagem
                  </button>
                  <button type="button" onClick=${pickDocument}
                    class="w-full text-left px-[14px] py-[8px] text-[14px] text-wa-text hover:bg-wa-hover flex items-center gap-[8px]">
                    <span class="text-[16px]">📄</span> Documento
                  </button>
                </div>
              ` : ''}
            </div>
          `}
          ${templatesSupported && mode !== 'private' ? html`
            <button
              type="button"
              class="p-[8px] ${sessionClosed ? 'text-wa-teal' : ''}"
              tabindex="-1"
              onClick=${() => setShowTemplatePicker(true)}
              title="Enviar template"
            >
              <${TemplateIcon} />
            </button>
          ` : ''}
          <div class="flex-1 mx-[5px] relative">
            ${mentionMenu ? (() => {
              const cands = getMentionCandidates(mentionMenu.query);
              if (!cands.length) return '';
              const sel = Math.min(mentionMenu.index || 0, cands.length - 1);
              return html`
                <div class="absolute left-0 right-0 bottom-[calc(100%+6px)] max-h-[210px] overflow-y-auto bg-wa-panel border border-wa-border rounded-[8px] shadow-lg py-[4px] z-30 wa-scrollbar">
                  ${cands.map((c, i) => html`
                    <button
                      type="button"
                      key=${c.special ? '@todos' : c.phone}
                      onMouseDown=${(ev) => { ev.preventDefault(); applyMention(c); }}
                      class="w-full text-left px-[12px] py-[7px] text-[14px] flex items-center gap-[8px] ${i === sel ? 'bg-wa-hover' : ''} hover:bg-wa-hover"
                    >
                      <span class="w-[26px] h-[26px] rounded-full flex items-center justify-center text-[12px] shrink-0 ${c.special ? 'bg-wa-teal text-white' : 'bg-wa-border text-wa-text'}">
                        ${c.special ? '@' : (mentionLabel(c) || '?').slice(0, 1).toUpperCase()}
                      </span>
                      <span class="text-wa-text truncate">
                        ${c.special ? 'todos — todos os membros' : mentionLabel(c)}
                        ${(!c.special && c.is_admin) ? html`<span class="ml-[6px] text-[11px] text-wa-secondary">admin</span>` : ''}
                      </span>
                    </button>
                  `)}
                </div>
              `;
            })() : ''}
            ${quickReplyMenu ? (() => {
              const cands = getQuickReplyCandidates(quickReplyMenu.query);
              if (!cands.length) return '';
              const sel = Math.min(quickReplyMenu.index || 0, cands.length - 1);
              return html`
                <div class="absolute left-0 right-0 bottom-[calc(100%+6px)] max-h-[210px] overflow-y-auto bg-wa-panel border border-wa-border rounded-[8px] shadow-lg py-[4px] z-30 wa-scrollbar">
                  ${cands.map((c, i) => html`
                    <button
                      type="button"
                      key=${c.id}
                      onMouseDown=${(ev) => { ev.preventDefault(); applyQuickReply(c); }}
                      class="w-full text-left px-[12px] py-[7px] text-[14px] flex items-start gap-[8px] ${i === sel ? 'bg-wa-hover' : ''} hover:bg-wa-hover"
                    >
                      <span class="text-wa-teal font-mono shrink-0">/${c.short_code}</span>
                      <span class="text-wa-secondary truncate">${(c.content || '').replace(/\s+/g, ' ').slice(0, 60)}</span>
                    </button>
                  `)}
                </div>
              `;
            })() : ''}
            <textarea
              ref=${inputRef}
              rows="1"
              value=${input}
              onInput=${handleInputChange}
              onKeyDown=${handleKeyDown}
              onPaste=${handlePaste}
              placeholder=${mode === 'private' ? 'Mensagem privada' : 'Digite uma mensagem'}
              class="w-full block bg-wa-inputBg text-wa-text text-[15px] rounded-[8px] px-[12px] py-[9px] border border-wa-border outline-none placeholder-wa-secondary resize-none max-h-[120px] wa-scrollbar leading-[20px]"
            ></textarea>
          </div>
          ${hasText ? html`
            <button
              type="submit"
              class="p-[8px] shrink-0 transition-colors"
              style="color: ${mode === 'private' ? '#a78bfa' : '#00a884'};"
            >
              <${SendIcon} />
            </button>
          ` : mode === 'private' ? '' : html`
            <button type="button" class="p-[8px] shrink-0 text-wa-icon ${sessionClosed ? 'opacity-40 cursor-not-allowed' : ''}" tabindex="-1"
              disabled=${sessionClosed} onClick=${handleMicClick}>
              <${MicIcon} />
            </button>
          `}
        </form>
      `}
      ${showTemplatePicker ? html`
        <${TemplatePicker}
          conversationId=${conversationId}
          channelId=${channelId}
          phone=${phone}
          onClose=${() => setShowTemplatePicker(false)}
          onSent=${() => setShowTemplatePicker(false)}
        />
      ` : ''}
      ${msgMenu ? html`
        <${MessageContextMenu}
          x=${msgMenu.x}
          y=${msgMenu.y}
          reactionBar=${(!msgMenu.message.revoked && msgMenu.message.msg_id && !sandbox) ? {
            emojis: QUICK_REACTIONS,
            current: myReaction(msgMenu.message),
            onReact: (em) => performReact(msgMenu.message, em),
          } : null}
          items=${[
            ...((!msgMenu.message.revoked && mode !== 'private'
                 && msgMenu.message.role !== 'private_note') ? [
              { label: 'Responder', icon: ReplyIcon,
                onClick: () => { setMode('reply'); setReplyingTo(msgMenu.message);
                                 setTimeout(() => inputRef.current?.focus(), 0); } },
            ] : []),
            { label: 'Copiar', icon: CopyIcon, onClick: () => copyMessageText(msgMenu.message) },
            { label: 'Copiar link da mensagem', icon: LinkIcon,
              disabled: !messagePermalink(msgMenu.message),
              onClick: () => copyMessageLink(msgMenu.message) },
            ...(msgMenu.message.revoked ? [] : [
              { label: 'Apagar', icon: TrashIcon, danger: true,
                onClick: () => setDeleteDialog({ message: msgMenu.message, isFromMe: msgMenu.isFromMe }) },
            ]),
            ...((!msgMenu.message.revoked && !sandbox
                 && msgMenu.message.role === 'assistant'
                 && msgMenu.message.status !== 'operator') ? [
              { label: 'Gerar melhoria', icon: ImproveIcon,
                onClick: () => openImprove(msgMenu.message) },
            ] : []),
          ]}
          onClose=${() => setMsgMenu(null)}
        />
      ` : ''}
      ${deleteDialog ? html`
        <div
          class="fixed inset-0 z-[130] bg-black/40 flex items-center justify-center"
          onClick=${() => setDeleteDialog(null)}
        >
          <div
            class="bg-wa-panel rounded-lg shadow-xl w-[330px] max-w-[90vw] p-[22px]"
            onClick=${(e) => e.stopPropagation()}
          >
            <div class="text-[15px] text-wa-text mb-[20px]">Deseja apagar a mensagem?</div>
            <div class="flex flex-col items-end gap-[10px]">
              ${deleteDialog.isFromMe && deleteDialog.message.msg_id ? html`
                <button
                  onClick=${() => performDelete(deleteDialog.message, 'all')}
                  class="px-[20px] py-[8px] rounded-full border border-wa-teal text-wa-teal text-[14px] font-medium hover:bg-wa-teal/10 transition-colors"
                >Apagar para todos</button>
              ` : ''}
              <button
                onClick=${() => performDelete(deleteDialog.message, 'me')}
                class="px-[20px] py-[8px] rounded-full border border-wa-teal text-wa-teal text-[14px] font-medium hover:bg-wa-teal/10 transition-colors"
              >Apagar para mim</button>
              <button
                onClick=${() => setDeleteDialog(null)}
                class="px-[20px] py-[8px] rounded-full text-wa-teal text-[14px] font-medium hover:bg-wa-teal/10 transition-colors"
              >Cancelar</button>
            </div>
          </div>
        </div>
      ` : ''}
      ${improveDialog ? html`
        <div
          class="fixed inset-0 z-[130] bg-black/40 flex items-center justify-center p-4"
          onClick=${() => { if (!improveLoading) setImproveDialog(null); }}
        >
          <div
            class="bg-wa-panel rounded-lg shadow-xl w-[440px] max-w-[92vw] p-[22px] flex flex-col gap-3"
            onClick=${(e) => e.stopPropagation()}
          >
            <div class="flex items-center gap-2 text-[15px] font-semibold text-wa-text">
              <span class="text-wa-teal">${ImproveIcon}</span>
              Gerar melhoria
            </div>
            <p class="text-[13px] text-wa-secondary -mt-1">
              A IA vai analisar o prompt, as ferramentas e o histórico para sugerir
              ajustes. O resultado aparece como uma mensagem de Sistema no chat
              (visível só no painel).
            </p>
            <div>
              <span class="block text-[12px] font-medium text-wa-secondary mb-1">Resposta marcada como incorreta</span>
              <div class="max-h-[120px] overflow-y-auto rounded-md border border-wa-border bg-wa-bg px-3 py-2 text-[13px] text-wa-text whitespace-pre-wrap">
                ${(improveDialog.message.content || '').trim() || '(sem conteúdo)'}
              </div>
            </div>
            <div>
              <label class="block text-[12px] font-medium text-wa-secondary mb-1">O que saiu errado? (opcional)</label>
              <textarea
                value=${improveText}
                onInput=${(e) => setImproveText(e.target.value)}
                disabled=${improveLoading}
                rows="3"
                placeholder="Ex.: respondeu o valor errado, não usou a ferramenta de agenda, tom inadequado…"
                class="wa-field w-full px-3 py-2 rounded-md text-[13px] resize-none"
              ></textarea>
            </div>
            ${improveError ? html`<div class="text-[12px] text-red-500">${improveError}</div>` : ''}
            <div class="flex justify-end gap-2 mt-1">
              <button
                onClick=${() => setImproveDialog(null)}
                disabled=${improveLoading}
                class="px-4 py-2 rounded-lg text-[14px] font-medium text-wa-text bg-wa-bg hover:bg-wa-hover border border-wa-border transition-colors disabled:opacity-50"
              >Cancelar</button>
              <button
                onClick=${submitImprovement}
                disabled=${improveLoading}
                class="px-4 py-2 rounded-lg text-[14px] font-medium text-white bg-wa-teal hover:opacity-90 transition-colors disabled:opacity-50 flex items-center gap-2"
              >
                ${improveLoading ? html`
                  <span class="inline-block w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin"></span>
                  Analisando…
                ` : 'Gerar análise'}
              </button>
            </div>
          </div>
        </div>
      ` : ''}
    </div>
  `;
}
