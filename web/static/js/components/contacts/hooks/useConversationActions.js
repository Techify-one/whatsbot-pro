// @ts-check
//
// Conversation/contact actions hook (Plano 23 · D2) — extracted verbatim from
// Contacts.js. Owns the operator identity + assignable agents, the global tag
// registry, the single-row actions (toggle IA, mark read/unread, archive,
// delete contact, delete conversation, pin) with their optimistic sidebar
// patches, and the right-click context-menu conversation lookup + assign/resolve.
//
// Every optimistic patch and its targeting rule (by phone for contact-level ops,
// by conversation_id for conversation-level ops) is preserved exactly.
// Cross-hook wiring: list (`setContacts`/`sortContacts`) + selection refs/setters
// are passed in so an action that closes the open thread clears it.
import { useState, useEffect, useCallback } from 'preact/hooks';
import {
  markAsRead, markAsUnread, markConversationRead, markConversationUnread,
  setConversationAi, deleteConversation, deleteContact,
  archiveContact, pinContact, createTag, updateContactTags,
  getMe, getAssignableAgents, getUsers, getTags,
  getContactConversation, getConversation, assignConversation, assignAgent,
} from '../../../services/api.js';
import { resolveConversation } from '../../../utils/resolveConversation.js';

/**
 * @param {Object} opts
 * @param {(fn:any)=>void} opts.setContacts
 * @param {(list:any[])=>any[]} opts.sortContacts
 * @param {(fn:any)=>void} opts.setContactData
 * @param {(v:any)=>void} opts.setSelected
 * @param {(v:any)=>void} opts.setSelectedConvId
 * @param {{ current: string|null }} opts.selectedRef
 * @param {{ current: number|null }} opts.selectedConvIdRef
 */
export function useConversationActions({
  setContacts, sortContacts, setContactData,
  setSelected, setSelectedConvId, selectedRef, selectedConvIdRef,
}) {
  const [globalTags, setGlobalTags] = useState({});
  // Identity + users for the "assign attendant" submenu (degrade gracefully on 403).
  const [currentUserId, setCurrentUserId] = useState(null);
  const [currentUser, setCurrentUser] = useState(null);   // full user (permissions[]) for P48 hides
  const [users, setUsers] = useState([]);
  const [agentsUsers, setAgentsUsers] = useState([]);         // assignable human agents
  const [agentsAi, setAgentsAi] = useState([]);               // assignable AI agents
  const [ctxMenu, setCtxMenu] = useState(null);
  // Conversation-level data for the open context menu (assignee/resolve). Resolved
  // lazily on right-click since the sidebar rows are contact-level only.
  const [ctxConv, setCtxConv] = useState({ loading: false, conv: null });

  // Toggle the AI for a single CONVERSATION (plano 17). Turning it OFF also
  // unassigns the conversation (handled server-side) so it drops into the
  // "Não atribuídas" fila; turning it ON re-binds the default agent. Optimistic
  // patch by conversation_id (a phone can have N conversations).
  const handleToggleAI = useCallback(async (convId, enabled) => {
    if (convId == null) return;
    const res = await setConversationAi(convId, enabled);
    if (res.ok) {
      setContacts(prev => prev.map(c =>
        c.conversation_id === convId
          ? {
              ...c,
              conv_ai_active: enabled ? 1 : 0,
              // On OFF the row must fall into "Não atribuídas" immediately; the
              // WS conversation_assigned event then fills the real values on ON.
              ...(enabled ? {} : { active_agent_key: null, assignee_user_id: null }),
            }
          : c
      ));
    }
  }, [setContacts]);

  // Plano 49: por CONVERSA quando a linha tem conversation_id (número em 2 canais =
  // 2 linhas, badges independentes); fallback por phone só na linha legada sem
  // atendimento (convId == null). O patch otimista mira a mesma dimensão da chamada.
  const handleMarkUnread = useCallback(async (phone, convId = null) => {
    const res = convId != null ? await markConversationUnread(convId) : await markAsUnread(phone);
    if (res.ok) {
      setContacts(prev => prev.map(c => {
        const hit = convId != null ? c.conversation_id === convId : c.phone === phone;
        return hit ? { ...c, unread_count: Math.max(c.unread_count || 0, 1) } : c;
      }));
    }
  }, [setContacts]);

  const handleMarkRead = useCallback(async (phone, convId = null) => {
    const res = convId != null ? await markConversationRead(convId) : await markAsRead(phone);
    if (res.ok) {
      setContacts(prev => prev.map(c => {
        const hit = convId != null ? c.conversation_id === convId : c.phone === phone;
        // unread_ai_count (badge "IA respondeu") é contato-nível (plano 28): no caminho
        // por-conversa não o zeramos — coerente com abrir a conversa. Só o fallback
        // por-phone (contato-nível) o limpa.
        return hit
          ? (convId != null
              ? { ...c, unread_count: 0, has_unread_mention: false }
              : { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false })
          : c;
      }));
    }
  }, [setContacts]);

  const handleArchive = useCallback(async (phone, archived) => {
    const res = await archiveContact(phone, archived);
    if (res.ok) {
      setContacts(prev => prev.filter(c => c.phone !== phone));
      if (selectedRef.current === phone) {
        setSelected(null);
        setSelectedConvId(null);
        setContactData(null);
        history.pushState(null, '', '/');
      }
    }
  }, [setContacts, selectedRef, setSelected, setSelectedConvId, setContactData]);

  const handleDelete = useCallback(async (phone) => {
    const res = await deleteContact(phone);
    if (res.ok) {
      setContacts(prev => prev.filter(c => c.phone !== phone));
      if (selectedRef.current === phone) {
        setSelected(null);
        setSelectedConvId(null);
        setContactData(null);
        history.pushState(null, '', '/');
      }
    }
  }, [setContacts, selectedRef, setSelected, setSelectedConvId, setContactData]);

  // Delete a single CONVERSATION/thread (plano 16, ação A). Filters the sidebar by
  // conversation_id (NEVER phone — a phone has N rows) and clears the open thread
  // only if it is the one being deleted.
  const handleDeleteConversation = useCallback(async (convId) => {
    if (convId == null) return;
    const res = await deleteConversation(convId);
    if (res.ok) {
      setContacts(prev => prev.filter(c => c.conversation_id !== convId));
      if (selectedConvIdRef.current === convId) {
        setSelected(null);
        setSelectedConvId(null);
        setContactData(null);
        history.pushState(null, '', '/');
      }
    }
  }, [setContacts, selectedConvIdRef, setSelected, setSelectedConvId, setContactData]);

  const handlePin = useCallback(async (phone, pinned) => {
    const res = await pinContact(phone, pinned);
    if (res.ok) {
      setContacts(prev => sortContacts(prev.map(c =>
        c.phone === phone ? { ...c, is_pinned: res.data.pinned } : c
      )));
    }
  }, [setContacts, sortContacts]);

  // ── Conversation actions from the context menu (assign attendant / resolve) ──
  // They act on the conversation resolved for the right-clicked contact (ctxConv)
  // and patch it in place so the menu reflects the new assignee/status without
  // reopening. Errors (e.g. 403 for a role without conversation.assign) surface on
  // the menu via ctxConv.error.
  const patchCtxConv = useCallback((patch) => {
    setCtxConv(prev => prev.conv
      ? { ...prev, conv: { ...prev.conv, ...patch }, error: null }
      : prev);
  }, []);

  const handleAssignConversation = useCallback(async (convId, userId) => {
    const res = await assignConversation(convId, userId);
    if (res && res.ok && res.data && res.data.conversation) {
      patchCtxConv({ assignee_user_id: res.data.conversation.assignee_user_id });
    } else {
      setCtxConv(prev => ({ ...prev, error: (res && res.error) || 'Falha ao atribuir conversa.' }));
    }
  }, [patchCtxConv]);

  // Unified assign (plano 10) for the context menu — routes to a HUMAN or an AI
  // subagent via the same endpoint AssigneePicker uses. `payload` is the assignAgent
  // body: {kind:'none'} | {kind:'user',userId} | {kind:'ai',agentKey}. Patches all
  // three fields the menu reads (human clears AI + IA off; AI clears human + IA on).
  const handleAssignAgent = useCallback(async (convId, payload) => {
    const res = await assignAgent(convId, payload);
    if (res && res.ok && res.data && res.data.conversation) {
      const c = res.data.conversation;
      patchCtxConv({
        assignee_user_id: c.assignee_user_id,
        active_agent_key: c.active_agent_key,
        ai_active: c.ai_active,
      });
    } else {
      setCtxConv(prev => ({ ...prev, error: (res && res.error) || 'Falha ao atribuir conversa.' }));
    }
  }, [patchCtxConv]);

  const handleResolveConversation = useCallback(async (convId, status) => {
    // Funnel through resolveConversation so the beforeResolve filter (plugins) runs
    // here too. Pass an object so the filter gets the conversation id for context.
    const res = await resolveConversation({ id: convId }, status);
    if (res && res.ok && res.data && res.data.conversation) {
      patchCtxConv({ status: res.data.conversation.status });
    } else {
      setCtxConv(prev => ({ ...prev, error: (res && res.error) || 'Falha ao atualizar status.' }));
    }
  }, [patchCtxConv]);

  // Create a new global tag and add it to the sidebar's tag map. Returns true on
  // success so the caller (context menu / bulk menu) can then apply it.
  const handleCreateTag = useCallback(async (name, color) => {
    const res = await createTag(name, color);
    if (res.ok) {
      setGlobalTags(prev => ({ ...prev, [name]: { color } }));
      return true;
    }
    return false;
  }, []);

  // Apply a list of {phone, tags} results to the sidebar + open chat.
  const applyTagResults = useCallback((results) => {
    const map = Object.fromEntries(results.map(r => [r.phone, r.tags]));
    setContacts(prev => prev.map(c => map[c.phone] ? { ...c, tags: map[c.phone] } : c));
    if (map[selectedRef.current]) {
      setContactData(prev => prev ? { ...prev, tags: map[selectedRef.current] } : prev);
    }
  }, [setContacts, setContactData, selectedRef]);

  // Resolve the assignee badge for a row (human name, or AI agent name).
  const resolveAssignee = useCallback((c) => {
    if (c.assignee_user_id != null) {
      const u = agentsUsers.find(x => x.id === c.assignee_user_id);
      return { label: u ? u.name : `#${c.assignee_user_id}`, isAi: false,
               isMe: currentUserId != null && c.assignee_user_id === currentUserId };
    }
    if (c.active_agent_key) {
      const a = agentsAi.find(x => x.agent_key === c.active_agent_key);
      return { label: a ? a.display_name : c.active_agent_key, isAi: true, isMe: false };
    }
    return null;
  }, [agentsUsers, agentsAi, currentUserId]);

  // Load global tags
  useEffect(() => {
    getTags().then(res => { if (res.ok) setGlobalTags(res.data); });
  }, []);

  // Identity + assignable agents (plano 10) drive "Minhas" and the assignee label
  // on each row; the users list feeds the context-menu "assign attendant" submenu.
  // All best-effort; degrade silently if forbidden.
  useEffect(() => {
    getMe().then(res => {
      if (res && res.ok && res.data && res.data.user) {
        setCurrentUserId(res.data.user.id);
        setCurrentUser(res.data.user);
      }
    }).catch(() => {});
    getAssignableAgents().then(res => {
      if (res && res.ok && res.data) {
        setAgentsUsers(Array.isArray(res.data.users) ? res.data.users : []);
        setAgentsAi(Array.isArray(res.data.ai_agents) ? res.data.ai_agents : []);
      }
    }).catch(() => {});
    // silent: read best-effort — sem `users.manage` o backend responde 403 e a
    // lista fica vazia (degradação silenciosa), sem toast "Permissão negada.".
    getUsers({ silent: true }).then((res) => {
      if (res && res.ok && res.data && Array.isArray(res.data.users)) setUsers(res.data.users);
    }).catch(() => {});
  }, []);

  // Resolve the contact's conversation whenever the context menu opens, so the
  // menu can show the current assignee and the resolve/reopen state. include_closed
  // so a resolved thread still resolves (lets us show "Reabrir atendimento").
  useEffect(() => {
    if (!ctxMenu || !ctxMenu.phone) { setCtxConv({ loading: false, conv: null }); return; }
    const phone = ctxMenu.phone;
    const convId = ctxMenu.conversationId;
    let alive = true;
    setCtxConv({ loading: true, conv: null });
    // Atendimento-cêntrico: a linha clicada conhece seu atendimento — carrega ELA por id.
    // getContactConversation(phone) resolveria só uma dos atendimentos do número e o
    // menu agiria no canal errado (resolver/reabrir afetaria o atendimento errada).
    const fetch = convId != null
      ? getConversation(convId)
      : getContactConversation(phone, { includeClosed: true });
    fetch.then((res) => {
      if (!alive) return;
      setCtxConv({ loading: false, conv: (res && res.ok && res.data) ? res.data.conversation : null });
    }).catch(() => { if (alive) setCtxConv({ loading: false, conv: null }); });
    return () => { alive = false; };
  }, [ctxMenu]);

  return {
    globalTags, setGlobalTags,
    currentUserId, currentUser, users, agentsUsers, agentsAi,
    ctxMenu, setCtxMenu, ctxConv, setCtxConv,
    handleToggleAI, handleMarkUnread, handleMarkRead,
    handleArchive, handleDelete, handleDeleteConversation, handlePin,
    handleAssignConversation, handleAssignAgent, handleResolveConversation,
    handleCreateTag, applyTagResults, resolveAssignee,
  };
}
