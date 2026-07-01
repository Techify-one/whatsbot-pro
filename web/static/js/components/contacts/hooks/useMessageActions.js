// @ts-check
//
// Message-actions hook (Plano 23 · D3) — extracted verbatim from ContactDetail.js.
// Owns the per-message context menu, the delete-confirm dialog, the "Gerar
// melhoria" dialog, and the operations they trigger: react (toggle), delete
// (optimistic revoke, keep in panel), copy text / copy permalink, request an AI
// improvement analysis. Also exposes `updateMsgByLocalId` (the generic local
// message patcher shared by the composer + media-upload hooks).
//
// Behavior-preserving: same optimistic update shapes, same best-effort error
// handling (WS reconciles), same permalink format, same group-prefix stripping.
import { useState } from 'preact/hooks';
import { deleteMessage, reactToMessage, generateImprovement } from '../../../services/api.js';
import { copyToClipboard } from '../MessageContextMenu.js';

// The operator's own current reaction on a message (stored under reactor "me").
export function myReaction(message) {
  const r = message && message.reactions;
  if (!r) return null;
  for (const [emoji, reactors] of Object.entries(r)) {
    if (Array.isArray(reactors) && reactors.includes('me')) return emoji;
  }
  return null;
}

/**
 * @param {Object} opts
 * @param {string} opts.phone
 * @param {any} opts.conversationId
 * @param {(updater:(prev:any)=>any)=>void} opts.setContactData
 */
export function useMessageActions({ phone, conversationId, setContactData }) {
  // Per-message context menu: { x, y, message, isFromMe } | null
  const [msgMenu, setMsgMenu] = useState(null);
  // Delete confirmation dialog: { message, isFromMe } | null
  const [deleteDialog, setDeleteDialog] = useState(null);
  // Improvement-analysis dialog for a flagged AI reply: { message } | null
  const [improveDialog, setImproveDialog] = useState(null);
  const [improveText, setImproveText] = useState('');
  const [improveLoading, setImproveLoading] = useState(false);
  const [improveError, setImproveError] = useState('');

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

  // Permalink estilo Chatwoot: âncora no atendimento + id interno da mensagem (a mesma
  // chave que o scroll-to-message usa via data-mid). Prefere o atendimento da própria
  // mensagem; cai no prop do atendimento aberto. null quando não há como ancorar (msg
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

  return {
    msgMenu, setMsgMenu, deleteDialog, setDeleteDialog,
    improveDialog, setImproveDialog, improveText, setImproveText,
    improveLoading, improveError,
    updateMsgByLocalId, openMsgMenu, copyMessageText, messagePermalink, copyMessageLink,
    openImprove, submitImprovement, performDelete, performReact,
  };
}
