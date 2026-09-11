// @ts-check
//
// Bulk-selection hook (Plano 23 · D2) — extracted verbatim from Contacts.js.
// Owns selection mode + the per-row selected keys (keyed per CONVERSATION via
// rowKeyFor, so two channels of the same number select independently) and every
// bulk action: IA on/off, archive, etiqueta toggle / remove-all, pin, mark read/unread.
//
// Each action dedupes by the correct identity (per conversation_id for IA,
// etiquetas e arquivo; per phone só no fallback legado de leitura).
// Cross-hook wiring: list + selection refs/setters + the actions hook's
// `applyConvLabelResults` are passed in.
import { useState, useCallback } from 'preact/hooks';
import {
  setConversationAi, archiveConversation, pinConversation,
  markAsRead, markAsUnread, markConversationRead, markConversationUnread,
  updateConversationLabels, assignAgent,
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
 * @param {(results:any[])=>void} opts.applyConvLabelResults
 */
export function useBulkSelection({
  contactsRef, displayedRef, showArchivedRef,
  setContacts, sortContacts, setContactData,
  setSelected, setSelectedConvId, selectedRef, selectedConvIdRef, applyConvLabelResults,
  // plano 72 F5 — reconcilia a lista server-filtrada após um bulk otimista de
  // membership (no-op fora de serverMode). Default no-op p/ callers antigos.
  reconcileAfterMembershipChange = () => {},
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
  // Desmarca todas SEM sair do modo de seleção (usado pelo toggle "Selecionar todas": apertar
  // de novo com tudo marcado desmarca, mas a barra de seleção continua aberta).
  const deselectAll = useCallback(() => { setSelectedKeys([]); }, []);

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
    // plano 72 F5: em serverMode as linhas patchadas podem ter saído da view — refetch
    // server-filtrado reconcilia a membership com o servidor.
    reconcileAfterMembershipChange();
  }, [_selectedRows, setContacts, reconcileAfterMembershipChange]);

  // Assign an attendant across all selected conversations at once. Takes the same
  // payload as the per-conversation picker (AssigneeList → assignAgent), via the
  // unified /assign-agent endpoint (plano 10):
  //   * kind='user' → sets the human assignee, clears any AI agent and turns the IA
  //     OFF (a person took over) — same transition as the per-conversation picker;
  //   * kind='ai'   → activates the AI subagent (clears the human assignee, IA ON);
  //   * kind='none' → unassigns whatever is set, HUMAN or AI (active_agent_key),
  //     even when it was assigned before the selection.
  // Conversation-level — one call per conversation_id; legacy rows without a
  // conversation are skipped.
  const handleBulkAssign = useCallback(async (payload) => {
    const kind = payload && payload.kind ? payload.kind : 'none';
    const convIds = _selectedRows()
      .filter(c => c.conversation_id != null)
      .map(c => c.conversation_id);
    if (!convIds.length) return;
    const idSet = new Set(convIds);
    const body = kind === 'user'
      ? { kind: 'user', userId: payload.userId }
      : kind === 'ai'
        ? { kind: 'ai', agentKey: payload.agentKey }
        : { kind: 'none' };
    await Promise.all(convIds.map(id => assignAgent(id, body).catch(() => null)));
    setContacts(prev => prev.map(c => {
      if (!idSet.has(c.conversation_id)) return c;
      if (kind === 'user') {
        // Human took over: mirror the backend — clear the AI agent + flip IA OFF.
        return { ...c, assignee_user_id: payload.userId, active_agent_key: null, conv_ai_active: 0 };
      }
      if (kind === 'ai') {
        // AI subagent took over: clear the human assignee + flip IA ON.
        return { ...c, assignee_user_id: null, active_agent_key: payload.agentKey, conv_ai_active: 1 };
      }
      return { ...c, assignee_user_id: null, active_agent_key: null };
    }));
    // plano 72 F5: em serverMode as linhas reatribuídas podem ter saído da view (ex.:
    // atribuir a OUTRO atendente na aba Minhas) — refetch server-filtrado reconcilia.
    reconcileAfterMembershipChange();
  }, [_selectedRows, setContacts, reconcileAfterMembershipChange]);

  const handleBulkArchive = useCallback(async () => {
    // Arquivo por CONVERSA (plano 54): uma chamada por conversation_id selecionado.
    // Linhas legadas sem atendimento (conversation_id == null) são puladas.
    const convIds = _selectedRows()
      .filter(c => c.conversation_id != null)
      .map(c => c.conversation_id);
    if (!convIds.length) return;
    const archived = !showArchivedRef.current; // archive when viewing inbox, unarchive when viewing archived
    await Promise.all(convIds.map(id => archiveConversation(id, archived).catch(() => null)));
    const idSet = new Set(convIds);
    setContacts(prev => prev.filter(c => !idSet.has(c.conversation_id)));
    if (selectedConvIdRef && idSet.has(selectedConvIdRef.current)) {
      setSelected(null);
      setSelectedConvId(null);
      setContactData(null);
      history.pushState(null, '', '/');
    }
    exitSelection();
  }, [_selectedRows, exitSelection, showArchivedRef, setContacts, selectedConvIdRef, setSelected, setSelectedConvId, setContactData]);

  // Etiquetas são por CONVERSA — cada linha selecionada é um atendimento e vale por
  // si. NÃO deduplicar por phone: selecionar os dois canais do mesmo número etiqueta
  // os dois atendimentos, independentemente. Linha legada sem atendimento é pulada
  // (não há onde pendurar a etiqueta).
  const _selectedLabelTargets = useCallback(
    () => _selectedRows().filter(c => c.conversation_id != null),
    [_selectedRows]);

  // Toggle a label across all selected: if every selected conversation already has
  // it, remove it from all; otherwise add it to all (keeping those that had it).
  // Repeated clicks cycle add → remove → add …
  const handleBulkTag = useCallback(async (labelName) => {
    const targets = _selectedLabelTargets();
    if (!targets.length) return;
    const allHave = targets.every(c => (c.conv_labels || []).includes(labelName));
    const results = await Promise.all(targets.map(async (c) => {
      const labels = Array.isArray(c.conv_labels) ? c.conv_labels : [];
      const next = allHave
        ? labels.filter(t => t !== labelName)
        : (labels.includes(labelName) ? labels : [...labels, labelName]);
      if (next.length === labels.length) return { conversationId: c.conversation_id, labels };
      const res = await updateConversationLabels(c.conversation_id, next).catch(() => null);
      return {
        conversationId: c.conversation_id,
        labels: (res && res.ok) ? res.data.labels : labels,
      };
    }));
    applyConvLabelResults(results);
  }, [_selectedLabelTargets, applyConvLabelResults]);

  // Remove all labels from all selected conversations.
  const handleBulkRemoveAllTags = useCallback(async () => {
    const targets = _selectedLabelTargets();
    if (!targets.length) return;
    const results = await Promise.all(targets.map(async (c) => {
      const labels = Array.isArray(c.conv_labels) ? c.conv_labels : [];
      if (!labels.length) return { conversationId: c.conversation_id, labels };
      const res = await updateConversationLabels(c.conversation_id, []).catch(() => null);
      return {
        conversationId: c.conversation_id,
        labels: (res && res.ok) ? res.data.labels : [],
      };
    }));
    applyConvLabelResults(results);
  }, [_selectedLabelTargets, applyConvLabelResults]);

  // Pin/unpin all selected at once (pinned ones sort to the top). Plano 54: por
  // CONVERSA (uma chamada por conversation_id); linhas legadas sem atendimento pulam.
  const handleBulkPin = useCallback(async (pinned) => {
    const convIds = _selectedRows()
      .filter(c => c.conversation_id != null)
      .map(c => c.conversation_id);
    if (!convIds.length) return;
    await Promise.all(convIds.map(id => pinConversation(id, pinned).catch(() => null)));
    const idSet = new Set(convIds);
    setContacts(prev => sortContacts(prev.map(c =>
      idSet.has(c.conversation_id) ? { ...c, is_pinned: pinned ? 1 : 0 } : c
    )));
  }, [_selectedRows, sortContacts, setContacts]);

  // Plano 49: por CONVERSA (itera as LINHAS selecionadas, não phones deduplicados),
  // com fallback por phone só nas linhas legadas sem conversation_id. Selecionar 2
  // canais do mesmo número marca só as conversas escolhidas.
  const handleBulkMarkRead = useCallback(async () => {
    const rows = _selectedRows();
    if (!rows.length) return;
    const convIds = new Set(rows.filter(c => c.conversation_id != null).map(c => c.conversation_id));
    const phones = new Set(rows.filter(c => c.conversation_id == null).map(c => c.phone));
    await Promise.all([
      ...[...convIds].map(id => markConversationRead(id).catch(() => null)),
      ...[...phones].map(p => markAsRead(p).catch(() => null)),
    ]);
    setContacts(prev => prev.map(c => {
      if (c.conversation_id != null && convIds.has(c.conversation_id)) {
        return { ...c, unread_count: 0, has_unread_mention: false };
      }
      if (c.conversation_id == null && phones.has(c.phone)) {
        return { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false };
      }
      return c;
    }));
  }, [_selectedRows, setContacts]);

  const handleBulkMarkUnread = useCallback(async () => {
    const rows = _selectedRows();
    if (!rows.length) return;
    const convIds = new Set(rows.filter(c => c.conversation_id != null).map(c => c.conversation_id));
    const phones = new Set(rows.filter(c => c.conversation_id == null).map(c => c.phone));
    await Promise.all([
      ...[...convIds].map(id => markConversationUnread(id).catch(() => null)),
      ...[...phones].map(p => markAsUnread(p).catch(() => null)),
    ]);
    setContacts(prev => prev.map(c => {
      const hit = (c.conversation_id != null && convIds.has(c.conversation_id))
        || (c.conversation_id == null && phones.has(c.phone));
      return hit ? { ...c, unread_count: Math.max(c.unread_count || 0, 1) } : c;
    }));
  }, [_selectedRows, setContacts]);

  return {
    selectionMode, setSelectionMode, selectedKeys, setSelectedKeys,
    enterSelection, exitSelection, toggleSelect, selectAllContacts, clearSelection, deselectAll,
    handleBulkAI, handleBulkArchive, handleBulkTag, handleBulkRemoveAllTags,
    handleBulkPin, handleBulkMarkRead, handleBulkMarkUnread, handleBulkAssign,
  };
}
