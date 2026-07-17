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
import { deleteMessage, editMessage, reactToMessage } from '../../../services/api.js';
import { copyToClipboard } from '../MessageContextMenu.js';
import { deepLinkUrl } from '../../../utils/copyDeepLink.js';
import { notify } from '../../../services/notify.js';

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
  // Per-message context menu: { x, y, message, isFromMe, items } | null.
  // `items` is resolved by the caller (ContactDetail) when the menu opens — base
  // items + the `filter.message.contextMenu.items` plugin chain.
  const [msgMenu, setMsgMenu] = useState(null);
  // Delete confirmation dialog: { message, isFromMe } | null
  const [deleteDialog, setDeleteDialog] = useState(null);
  // Edit dialog: { message } | null
  const [editDialog, setEditDialog] = useState(null);

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

  // (The context menu is opened by ContactDetail, which resolves the item list
  // through the `filter.message.contextMenu.items` plugin seam and calls
  // `setMsgMenu`. This hook only owns the menu STATE + the item operations.)

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
    return deepLinkUrl(`/conversations/${convId}?message=${message._id}`);
  }

  function copyMessageLink(message) {
    const url = messagePermalink(message);
    if (url) copyToClipboard(url);
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

  // Edit an outgoing message's text. Optimistically swaps the content in place
  // (and marks it edited); on failure reverts to the previous content and toasts.
  // The WS `message_edited` broadcast reconciles other clients.
  async function performEdit(message, newText) {
    const text = (newText || '').trim();
    setEditDialog(null);
    if (!text || text === message.content) return;
    const msgId = message.msg_id || null;
    const dbId = message._id || message.id || null;
    const localId = message._localId || null;
    const prevContent = message.content;
    const matches = (m) => (msgId && m.msg_id === msgId)
      || (dbId && (m._id === dbId || m.id === dbId))
      || (localId && m._localId === localId);
    const nowTs = Date.now() / 1000;
    // Optimistic content swap.
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      const updated = prev.messages.map(m =>
        matches(m) ? { ...m, content: text, edited_ts: nowTs } : m);
      return { ...prev, messages: updated };
    });
    try {
      const res = await editMessage(phone, { msgId, dbId, text, conversationId });
      if (!res || res.ok === false) {
        // Revert and inform the operator (e.g. edit window expired at the provider).
        setContactData(prev => {
          if (!prev || !prev.messages) return prev;
          const reverted = prev.messages.map(m =>
            matches(m) ? { ...m, content: prevContent, edited_ts: message.edited_ts } : m);
          return { ...prev, messages: reverted };
        });
        notify((res && res.error) || 'Não foi possível editar a mensagem.', { kind: 'error' });
      }
    } catch (_) {
      setContactData(prev => {
        if (!prev || !prev.messages) return prev;
        const reverted = prev.messages.map(m =>
          matches(m) ? { ...m, content: prevContent, edited_ts: message.edited_ts } : m);
        return { ...prev, messages: reverted };
      });
      notify('Não foi possível editar a mensagem.', { kind: 'error' });
    }
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
    editDialog, setEditDialog,
    updateMsgByLocalId, copyMessageText, messagePermalink, copyMessageLink,
    performDelete, performEdit, performReact,
  };
}
