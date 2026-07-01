// @ts-check
//
// Bulk-selection hook (Plano 23 · D2) — extracted verbatim from Contacts.js.
// Owns selection mode + the per-row selected keys (keyed per CONVERSATION via
// rowKeyFor, so two channels of the same number select independently) and every
// bulk action: IA on/off, archive, tag toggle / remove-all, pin, mark read/unread.
//
// Each action dedupes by the correct identity (per conversation_id for IA, per
// phone for the contact-level archive/pin/tags/read) exactly as before.
// Cross-hook wiring: list + selection refs/setters + the actions hook's
// `applyTagResults` are passed in.
import { useState, useCallback } from 'preact/hooks';
import {
  setConversationAi, archiveContact, pinContact,
  markAsRead, markAsUnread, updateContactTags, assignAgent,
} from '../../../services/api.js';
import { rowKeyFor } from '../ContactList.js';

/**
 * @param {Object} opts
 * @param {{ current: Record<string, any>[] }} opts.contactsRef
 * @param {{ current: Record<string, any>[] }} opts.displayedRef
 * @param {{ current: boolean }} opts.showArchivedRef
 * @param {(fn:any)=>void} opts.setContacts
 * @param {(list:any[])=>any[]} opts.sortContacts
 * @param {(fn:any)=>void} opts.setContactData
 * @param {(v:any)=>void} opts.setSelected
 * @param {(v:any)=>void} opts.setSelectedConvId
 * @param {{ current: string|null }} opts.selectedRef
 * @param {(results:any[])=>void} opts.applyTagResults
 */
export function useBulkSelection({
  contactsRef, displayedRef, showArchivedRef,
  setContacts, sortContacts, setContactData,
  setSelected, setSelectedConvId, selectedRef, applyTagResults,
}) {
  const [selectionMode, setSelectionMode] = useState(false);
  // Selection keyed per CONVERSATION row (rowKeyFor), so two conversations of the
  // same number (different channels) are selected independently.
  const [selectedKeys, setSelectedKeys] = useState([]);

  const enterSelection = useCallback(() => { setSelectionMode(true); setSelectedKeys([]); }, []);
  const exitSelection = useCallback(() => { setSelectionMode(false); setSelectedKeys([]); }, []);
  const toggleSelect = useCallback((key) => {
    setSelectedKeys(prev => prev.includes(key)
      ? prev.filter(k => k !== key)
      : [...prev, key]);
  }, []);
  const selectAllContacts = useCallback(() => {
    setSelectedKeys([...new Set(displayedRef.current.map(rowKeyFor))]);
  }, [displayedRef]);
  const clearSelection = useCallback(() => { setSelectedKeys([]); setSelectionMode(false); }, []);

  // Resolve the selected row keys back to their conversation rows. Each key maps to
  // a single row (a specific conversation/channel), so the two channels of the same
  // number are handled independently.
  const _selectedRows = useCallback(() => {
    const keys = new Set(selectedKeys);
    return contactsRef.current.filter(c => keys.has(rowKeyFor(c)));
  }, [selectedKeys, contactsRef]);

  const handleBulkAI = useCallback(async (enabled) => {
    // Operate per CONVERSATION (plano 17): each selected row is one conversation.
    // Legacy rows without a conversation are skipped.
    const convIds = _selectedRows()
      .filter(c => c.conversation_id != null)
      .map(c => c.conversation_id);
    if (!convIds.length) return;
    const idSet = new Set(convIds);
    await Promise.all(convIds.map(id => setConversationAi(id, enabled).catch(() => null)));
    setContacts(prev => prev.map(c =>
      idSet.has(c.conversation_id)
        ? {
            ...c,
            conv_ai_active: enabled ? 1 : 0,
            ...(enabled ? {} : { active_agent_key: null, assignee_user_id: null }),
          }
        : c
    ));
  }, [_selectedRows, setContacts]);

  // Assign a HUMAN attendant (userId) — or clear whoever is currently assigned
  // (userId == null) — across all selected conversations at once. Uses the unified
  // /assign-agent endpoint (plano 10) so that:
  //   * kind='user' → sets the assignee, clears any AI agent and turns the IA OFF
  //     (a person took over) — same transition as the per-conversation picker;
  //   * kind='none' → unassigns whatever is set, HUMAN or AI (active_agent_key),
  //     even when it was assigned before the selection.
  // The bulk list only offers humans; removal clears both by design.
  // Conversation-level — one call per conversation_id; legacy rows without a
  // conversation are skipped.
  const handleBulkAssign = useCallback(async (userId) => {
    const convIds = _selectedRows()
      .filter(c => c.conversation_id != null)
      .map(c => c.conversation_id);
    if (!convIds.length) return;
    const idSet = new Set(convIds);
    const body = userId == null ? { kind: 'none' } : { kind: 'user', userId };
    await Promise.all(convIds.map(id => assignAgent(id, body).catch(() => null)));
    setContacts(prev => prev.map(c => {
      if (!idSet.has(c.conversation_id)) return c;
      return userId == null
        ? { ...c, assignee_user_id: null, active_agent_key: null }
        // Human took over: mirror the backend — clear the AI agent + flip IA OFF.
        : { ...c, assignee_user_id: userId, active_agent_key: null, conv_ai_active: 0 };
    }));
  }, [_selectedRows, setContacts]);

  const handleBulkArchive = useCallback(async () => {
    // Archive is chat-level (per phone) — dedupe across channels.
    const phones = [...new Set(_selectedRows().map(c => c.phone))];
    if (!phones.length) return;
    const archived = !showArchivedRef.current; // archive when viewing inbox, unarchive when viewing archived
    await Promise.all(phones.map(p => archiveContact(p, archived).catch(() => null)));
    setContacts(prev => prev.filter(c => !phones.includes(c.phone)));
    if (phones.includes(selectedRef.current)) {
      setSelected(null);
      setSelectedConvId(null);
      setContactData(null);
      history.pushState(null, '', '/');
    }
    exitSelection();
  }, [_selectedRows, exitSelection, showArchivedRef, setContacts, selectedRef, setSelected, setSelectedConvId, setContactData]);

  const _selectedTargets = useCallback(() => {
    // Tags are contact-level (per phone) — dedupe the selected rows by phone so each
    // number is updated once even when both of its channels are selected.
    const byPhone = new Map();
    for (const c of _selectedRows()) if (!byPhone.has(c.phone)) byPhone.set(c.phone, c);
    return [...byPhone.values()];
  }, [_selectedRows]);

  // Toggle a tag across all selected: if every selected conversation already has
  // it, remove it from all; otherwise add it to all (keeping those that had it).
  // Repeated clicks cycle add → remove → add …
  const handleBulkTag = useCallback(async (tagName) => {
    const targets = _selectedTargets();
    if (!targets.length) return;
    const allHave = targets.every(c => (c.tags || []).includes(tagName));
    const results = await Promise.all(targets.map(async (c) => {
      const tags = Array.isArray(c.tags) ? c.tags : [];
      const next = allHave
        ? tags.filter(t => t !== tagName)
        : (tags.includes(tagName) ? tags : [...tags, tagName]);
      if (next.length === tags.length) return { phone: c.phone, tags };
      const res = await updateContactTags(c.phone, next).catch(() => null);
      return { phone: c.phone, tags: (res && res.ok) ? res.data.tags : tags };
    }));
    applyTagResults(results);
  }, [_selectedTargets, applyTagResults]);

  // Remove all tags from all selected conversations.
  const handleBulkRemoveAllTags = useCallback(async () => {
    const targets = _selectedTargets();
    if (!targets.length) return;
    const results = await Promise.all(targets.map(async (c) => {
      const tags = Array.isArray(c.tags) ? c.tags : [];
      if (!tags.length) return { phone: c.phone, tags };
      const res = await updateContactTags(c.phone, []).catch(() => null);
      return { phone: c.phone, tags: (res && res.ok) ? res.data.tags : [] };
    }));
    applyTagResults(results);
  }, [_selectedTargets, applyTagResults]);

  // Pin/unpin all selected at once (pinned ones sort to the top).
  const handleBulkPin = useCallback(async (pinned) => {
    // Pin is contact-level (per phone) — dedupe across channels.
    const phones = [...new Set(_selectedRows().map(c => c.phone))];
    if (!phones.length) return;
    await Promise.all(phones.map(p => pinContact(p, pinned).catch(() => null)));
    setContacts(prev => sortContacts(prev.map(c =>
      phones.includes(c.phone) ? { ...c, is_pinned: pinned } : c
    )));
  }, [_selectedRows, sortContacts, setContacts]);

  const handleBulkMarkRead = useCallback(async () => {
    const phones = [...new Set(_selectedRows().map(c => c.phone))];
    if (!phones.length) return;
    await Promise.all(phones.map(p => markAsRead(p).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
    ));
  }, [_selectedRows, setContacts]);

  const handleBulkMarkUnread = useCallback(async () => {
    const phones = [...new Set(_selectedRows().map(c => c.phone))];
    if (!phones.length) return;
    await Promise.all(phones.map(p => markAsUnread(p).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, unread_count: Math.max(c.unread_count || 0, 1) } : c
    ));
  }, [_selectedRows, setContacts]);

  return {
    selectionMode, setSelectionMode, selectedKeys, setSelectedKeys,
    enterSelection, exitSelection, toggleSelect, selectAllContacts, clearSelection,
    handleBulkAI, handleBulkArchive, handleBulkTag, handleBulkRemoveAllTags,
    handleBulkPin, handleBulkMarkRead, handleBulkMarkUnread, handleBulkAssign,
  };
}
