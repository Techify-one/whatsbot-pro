import { h } from 'preact';
import { useState, useEffect, useRef, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';
import { getContacts, getContact, getConversationMessages, listConversations, markAsRead, markAsUnread, toggleContactAI, getTags, deleteContact, archiveContact, pinContact, checkPhone, updateContactTags, createTag, getMe, getAssignableAgents, getUsers, getContactConversation, assignConversation, assignMeConversation, setConversationStatus } from '../../services/api.js';
import { ContactList } from './ContactList.js';
import { ContactDetail } from './ContactDetail.js';
import { ContactInfoPanel } from './ContactInfoPanel.js';
import { ContextMenu } from './ContextMenu.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';

// ── Sidebar resize (barra lateral arrastável) ───────────────────────
// Largura da lista de conversas: arrastável no desktop e persistida em
// localStorage (mesmo padrão do tema). No mobile a barra é `w-full` e estes
// valores não se aplicam.
const SIDEBAR_WIDTH_KEY = 'whatsbot_sidebar_width';
const SIDEBAR_MIN_WIDTH = 280;
const SIDEBAR_MAX_WIDTH = 640;
const SIDEBAR_DEFAULT_WIDTH = 400;
const SIDEBAR_DRAG_THRESHOLD = 4;  // px p/ distinguir arraste de clique (colapsar)

function clampSidebarWidth(px) {
  if (!Number.isFinite(px)) return SIDEBAR_DEFAULT_WIDTH;
  return Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, Math.round(px)));
}

function readStoredSidebarWidth() {
  try {
    const raw = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    return raw == null ? SIDEBAR_DEFAULT_WIDTH : clampSidebarWidth(parseInt(raw, 10));
  } catch { return SIDEBAR_DEFAULT_WIDTH; }
}

// ── Conversation tab/filter helpers (plano 10 FF2) ──────────────────
// All client-side over the enriched contact list (each row carries its active
// conversation's status/assignee/agente), so switching tabs is instant.
const isUnassigned = (c) => c.assignee_user_id == null && !c.active_agent_key;

function matchesStatus(c, statusFilter) {
  if (statusFilter === 'all') return true;
  return (c.conv_status || 'open') === statusFilter;   // 'open' | 'closed'
}
function matchesAssignment(c, tab, uid) {
  if (tab === 'mine') return uid != null && c.assignee_user_id === uid;
  if (tab === 'unassigned') return isUnassigned(c);
  return true;  // 'all'
}
function matchesTags(c, tagFilter) {
  if (!tagFilter || tagFilter.length === 0) return true;
  const ctags = c.tags || [];
  return tagFilter.some(t => ctags.includes(t));   // "é uma de" (OR), estilo Chatwoot
}
function sortContactsBy(list, sortBy) {
  const arr = [...list];
  const ts = (c) => c.last_message_ts || c.updated_at || 0;
  if (sortBy === 'oldest') {
    arr.sort((a, b) => ts(a) - ts(b));
  } else if (sortBy === 'unread') {
    arr.sort((a, b) => {
      const au = (a.unread_count || 0) + (a.unread_ai_count || 0);
      const bu = (b.unread_count || 0) + (b.unread_ai_count || 0);
      if (au !== bu) return bu - au;
      return ts(b) - ts(a);
    });
  } else {  // 'activity' — pinned first, then most recent (matches the backend)
    arr.sort((a, b) => {
      const ap = a.is_pinned ? 1 : 0, bp = b.is_pinned ? 1 : 0;
      if (ap !== bp) return bp - ap;
      return ts(b) - ts(a);
    });
  }
  return arr;
}

const html = htm.bind(h);

// ── Conversa-cêntrico (plano 11 D1) ──────────────────────────────
// Cada linha da sidebar é uma CONVERSA (uma por canal), não um contato. Um número
// presente em 2 canais vira 2 linhas distintas — em vez de fundir tudo numa só.
// Construímos as linhas cruzando os contatos (riqueza: tags/avatar/IA/nome) com as
// conversas (canal + preview e não-lidas POR CONVERSA). A identidade da linha é a
// `conversation_id`; contatos sem conversa ainda aparecem como linha única (phone).

function buildRows(contacts, conversations) {
  const byContact = new Map();
  for (const cv of conversations) {
    if (cv.contact_id == null) continue;
    if (!byContact.has(cv.contact_id)) byContact.set(cv.contact_id, []);
    byContact.get(cv.contact_id).push(cv);
  }
  const rows = [];
  for (const c of contacts) {
    const convs = byContact.get(c.id) || [];
    if (convs.length === 0) {
      // Contato sem conversa (ex: recém-iniciado pelo "Nova conversa") — linha única
      // que cai no caminho legado por telefone (channel 'default').
      rows.push({
        ...c, contact_id: c.id, conversation_id: null,
        channel_id: 'default', channel_provider: null, channel_name: null,
      });
    } else {
      for (const cv of convs) {
        rows.push({
          ...c,
          contact_id: c.id,
          conversation_id: cv.id,
          channel_id: cv.channel_id || 'default',
          channel_provider: cv.channel_provider || null,
          channel_name: cv.channel_name || null,
          conv_status: cv.status,
          // Preview + não-lidas vêm da CONVERSA (sobrescrevem os agregados do contato).
          last_message: (cv.last_message != null && cv.last_message !== '') ? cv.last_message : c.last_message,
          last_message_role: cv.last_message_role || c.last_message_role,
          last_message_ts: cv.last_message_ts || c.last_message_ts,
          last_message_status: cv.last_message_status || c.last_message_status,
          last_message_msg_id: cv.last_message_msg_id || c.last_message_msg_id,
          unread_count: cv.unread_count != null ? cv.unread_count : c.unread_count,
          has_unread_mention: cv.has_unread_mention != null ? cv.has_unread_mention : c.has_unread_mention,
        });
      }
    }
  }
  return rows;
}

// Shape a /api/conversations/{id}/messages payload into the same object the chat
// already consumes from getContact (full contact + messages), plus channel_id.
function shapeConvData(d) {
  return {
    ...(d.contact || {}),
    messages: d.messages || [],
    avatar_v: d.avatar_v,
    channel_id: d.channel_id || 'default',
    conversation: d.conversation || null,
  };
}

// ── Main Component ───────────────────────────────────────────────

export function Contacts({ newMessage, chatPresence, contactInfoUpdated, tagsChanged, contactTagsUpdated, contactAiToggled, messagesRead, messageStatus, messageAction, messageReaction, avatarUpdated, groupParticipantsChanged, conversationCreated, initialContactId, initialConversationId, wsConnected, config, onConfigSave, onUnreadChange }) {
  const [contacts, setContacts] = useState([]);  // sidebar rows (one per conversation)
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);          // open thread's phone (contact-level ops)
  const [selectedConvId, setSelectedConvId] = useState(null);   // open thread's conversation id
  const [selectedChannelId, setSelectedChannelId] = useState('default');  // open thread's channel
  const [scrollToMsg, setScrollToMsg] = useState(null);  // DB id of a message to focus on open (search hit)
  const [contactData, setContactData] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const hasLoadedDetail = useRef(false);
  const [showInfoPanel, setShowInfoPanel] = useState(false);
  const openInfoAfterSelect = useRef(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  // Largura arrastável da barra (desktop). `isDesktop` decide se aplicamos o style
  // inline — no mobile a barra é w-full e não deve receber largura fixa.
  const [sidebarWidth, setSidebarWidth] = useState(readStoredSidebarWidth);
  const [isResizing, setIsResizing] = useState(false);
  const [isDesktop, setIsDesktop] = useState(() => {
    try { return window.matchMedia('(min-width:1024px)').matches; } catch { return true; }
  });
  const resizeRef = useRef(null);  // { startX, startWidth, moved } durante o arraste
  const [ctxMenu, setCtxMenu] = useState(null);
  // Conversation-level data for the open context menu (assignee/resolve). Resolved
  // lazily on right-click since the sidebar rows are contact-level only.
  const [ctxConv, setCtxConv] = useState({ loading: false, conv: null });
  // Identity + users for the "assign attendant" submenu (degrade gracefully on 403).
  const [currentUserId, setCurrentUserId] = useState(null);
  const [users, setUsers] = useState([]);
  const [typingState, setTypingState] = useState({});  // { phone: 'text'|'audio'|null }
  const [showArchived, setShowArchived] = useState(false);
  const [globalTags, setGlobalTags] = useState({});
  // Conversation tabs/filters (plano 10 FF2) — applied client-side over `contacts`.
  const [statusFilter, setStatusFilter] = useState('open');   // open|closed|all (default Abertas)
  const [assignmentTab, setAssignmentTab] = useState('all');  // all|mine|unassigned
  const [sortBy, setSortBy] = useState('activity');           // activity|oldest|unread
  const [tagFilter, setTagFilter] = useState([]);             // array of tag names
  const [agentsUsers, setAgentsUsers] = useState([]);         // assignable human agents
  const [agentsAi, setAgentsAi] = useState([]);               // assignable AI agents
  const [checkingPhone, setCheckingPhone] = useState(false);
  const [checkPhoneError, setCheckPhoneError] = useState(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedPhones, setSelectedPhones] = useState([]);
  const pendingWsMessages = useRef({});
  const selectedRef = useRef(null);
  const selectedConvIdRef = useRef(null);
  const selectedChannelIdRef = useRef('default');
  const typingTimers = useRef({});
  const contactsRef = useRef([]);
  const displayedRef = useRef([]);   // currently-visible (filtered) rows — for "selecionar todas"
  const lastResolvedId = useRef(null);
  const lastResolvedConvId = useRef(null);
  const pageVisibleRef = useRef(!document.hidden);

  // Keep refs in sync — avoids stale closures
  useEffect(() => { selectedRef.current = selected; }, [selected]);
  useEffect(() => { selectedConvIdRef.current = selectedConvId; }, [selectedConvId]);
  useEffect(() => { selectedChannelIdRef.current = selectedChannelId; }, [selectedChannelId]);
  useEffect(() => { contactsRef.current = contacts; }, [contacts]);

  // Show the per-row channel badge only when ≥2 distinct channels exist (with a
  // single channel it would be noise) — mirrors the Conversations screen (FQ1).
  const showChannel = useMemo(() => {
    const seen = new Set();
    for (const c of contacts) if (c.channel_provider) seen.add(c.channel_provider);
    return seen.size > 1;
  }, [contacts]);

  // True when `row` is the currently-open thread (by conversation when available,
  // else by phone for legacy contact-only rows). Reads refs → safe in WS closures.
  const isOpenRow = useCallback((row) => {
    if (selectedConvIdRef.current != null) return row.conversation_id === selectedConvIdRef.current;
    return row.conversation_id == null && row.phone === selectedRef.current;
  }, []);

  // Notify the app shell whenever the conversation list changes so it can refresh
  // the browser-tab unread badge — covers reads that fire no WS event (e.g. the
  // operator opening a chat on this same client).
  useEffect(() => { if (onUnreadChange) onUnreadChange(); }, [contacts]);

  // Track page visibility — mark selected contact as read when tab becomes visible
  useEffect(() => {
    const handler = () => {
      const visible = !document.hidden;
      pageVisibleRef.current = visible;
      if (visible && selectedRef.current) {
        markAsRead(selectedRef.current);
        setContacts(prev => prev.map(c =>
          isOpenRow(c) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
        ));
      }
    };
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, []);

  const handleToggleAI = useCallback(async (phone, enabled) => {
    const res = await toggleContactAI(phone, enabled);
    if (res.ok) {
      setContacts(prev => prev.map(c =>
        c.phone === phone ? { ...c, ai_enabled: res.data.ai_enabled } : c
      ));
      if (contactData && contactData.phone === phone) {
        setContactData(prev => prev ? { ...prev, ai_enabled: res.data.ai_enabled } : prev);
      }
    }
  }, [contactData]);

  const handleMarkUnread = useCallback(async (phone) => {
    const res = await markAsUnread(phone);
    if (res.ok) {
      setContacts(prev => prev.map(c =>
        c.phone === phone
          ? { ...c, unread_count: Math.max(c.unread_count || 0, 1) }
          : c
      ));
    }
  }, []);

  const handleMarkRead = useCallback(async (phone) => {
    const res = await markAsRead(phone);
    if (res.ok) {
      setContacts(prev => prev.map(c =>
        c.phone === phone ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
      ));
    }
  }, []);

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
  }, []);

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
  }, []);

  // Re-sort like the backend: pinned first, then by last message time desc.
  const sortContacts = useCallback((list) => {
    return [...list].sort((a, b) => {
      const ap = a.is_pinned ? 1 : 0;
      const bp = b.is_pinned ? 1 : 0;
      if (ap !== bp) return bp - ap;
      return (b.last_message_ts || b.updated_at || 0) - (a.last_message_ts || a.updated_at || 0);
    });
  }, []);

  const handlePin = useCallback(async (phone, pinned) => {
    const res = await pinContact(phone, pinned);
    if (res.ok) {
      setContacts(prev => sortContacts(prev.map(c =>
        c.phone === phone ? { ...c, is_pinned: res.data.pinned } : c
      )));
    }
  }, [sortContacts]);

  // ── Sidebar resize (barra lateral arrastável) ──────────────────────
  // Acompanha o breakpoint lg: só aplicamos largura fixa no desktop.
  useEffect(() => {
    let mql;
    try { mql = window.matchMedia('(min-width:1024px)'); } catch { return; }
    const onChange = () => setIsDesktop(mql.matches);
    onChange();
    // addEventListener('change') é o moderno; addListener cobre navegadores antigos.
    if (mql.addEventListener) mql.addEventListener('change', onChange);
    else if (mql.addListener) mql.addListener(onChange);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener('change', onChange);
      else if (mql.removeListener) mql.removeListener(onChange);
    };
  }, []);

  const endResize = useCallback(() => {
    const st = resizeRef.current;
    resizeRef.current = null;
    setIsResizing(false);
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', endResize);
    if (st && st.moved) {
      // Persiste só quando houve arraste de fato (clique puro = colapsar/expandir).
      setSidebarWidth(w => {
        try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(w)); } catch {}
        return w;
      });
    } else if (st && !st.moved) {
      // Clique sem arraste no handle: colapsa/expande a barra.
      setSidebarHidden(h => !h);
    }
  }, []);

  const onResizeMove = useCallback((e) => {
    const st = resizeRef.current;
    if (!st || st.hidden) return;  // barra colapsada: handle só expande no clique
    const dx = e.clientX - st.startX;
    if (!st.moved && Math.abs(dx) < SIDEBAR_DRAG_THRESHOLD) return;
    st.moved = true;
    setSidebarWidth(clampSidebarWidth(st.startWidth + dx));
  }, []);

  const startResize = useCallback((e) => {
    // Só no desktop; com a barra colapsada o handle só expande (via clique).
    if (!isDesktop) return;
    e.preventDefault();
    resizeRef.current = { startX: e.clientX, startWidth: sidebarWidth, moved: false,
                          hidden: sidebarHidden };
    setIsResizing(true);
    document.addEventListener('mousemove', onResizeMove);
    document.addEventListener('mouseup', endResize);
  }, [isDesktop, sidebarWidth, sidebarHidden, onResizeMove, endResize]);

  // Limpeza defensiva: se o componente desmontar no meio de um arraste.
  useEffect(() => () => {
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', endResize);
  }, [onResizeMove, endResize]);

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

  const handleResolveConversation = useCallback(async (convId, status) => {
    const res = await setConversationStatus(convId, status);
    if (res && res.ok && res.data && res.data.conversation) {
      patchCtxConv({ status: res.data.conversation.status });
    } else {
      setCtxConv(prev => ({ ...prev, error: (res && res.error) || 'Falha ao atualizar status.' }));
    }
  }, [patchCtxConv]);

  // ── Selection mode (bulk actions) ───────────────────────────────
  const enterSelection = useCallback(() => { setSelectionMode(true); setSelectedPhones([]); }, []);
  const exitSelection = useCallback(() => { setSelectionMode(false); setSelectedPhones([]); }, []);
  const toggleSelect = useCallback((phone) => {
    setSelectedPhones(prev => prev.includes(phone)
      ? prev.filter(p => p !== phone)
      : [...prev, phone]);
  }, []);
  const selectAllContacts = useCallback(() => {
    setSelectedPhones([...new Set(displayedRef.current.map(c => c.phone))]);
  }, []);
  const clearSelection = useCallback(() => { setSelectedPhones([]); setSelectionMode(false); }, []);

  const handleBulkAI = useCallback(async (enabled) => {
    const phones = [...selectedPhones];
    if (!phones.length) return;
    await Promise.all(phones.map(p => toggleContactAI(p, enabled).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, ai_enabled: enabled } : c
    ));
    if (phones.includes(selectedRef.current)) {
      setContactData(prev => prev ? { ...prev, ai_enabled: enabled } : prev);
    }
  }, [selectedPhones]);

  const handleBulkArchive = useCallback(async () => {
    const phones = [...selectedPhones];
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
  }, [selectedPhones, exitSelection]);

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
  }, []);

  const _selectedTargets = useCallback(() => {
    const current = contactsRef.current;
    // One target per selected phone (rows may repeat a phone across channels).
    return [...selectedPhones].map(p => current.find(c => c.phone === p)).filter(Boolean);
  }, [selectedPhones]);

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
    const phones = [...selectedPhones];
    if (!phones.length) return;
    await Promise.all(phones.map(p => pinContact(p, pinned).catch(() => null)));
    setContacts(prev => sortContacts(prev.map(c =>
      phones.includes(c.phone) ? { ...c, is_pinned: pinned } : c
    )));
  }, [selectedPhones, sortContacts]);

  const handleBulkMarkRead = useCallback(async () => {
    const phones = [...selectedPhones];
    if (!phones.length) return;
    await Promise.all(phones.map(p => markAsRead(p).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
    ));
  }, [selectedPhones]);

  const handleBulkMarkUnread = useCallback(async () => {
    const phones = [...selectedPhones];
    if (!phones.length) return;
    await Promise.all(phones.map(p => markAsUnread(p).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, unread_count: Math.max(c.unread_count || 0, 1) } : c
    ));
  }, [selectedPhones]);

  // Push URL when selecting/deselecting a conversation row. Accepts a sidebar row
  // (conversation-centric) OR a bare phone string (legacy callers — start
  // conversation / context-menu edit), resolving the latter to its newest row.
  const selectContact = useCallback((rowOrPhone, msgId = null) => {
    setScrollToMsg(msgId != null ? msgId : null);
    if (rowOrPhone == null) {
      setSelected(null);
      setSelectedConvId(null);
      setSelectedChannelId('default');
      history.pushState(null, '', '/');
      return;
    }
    let row = rowOrPhone;
    if (typeof rowOrPhone === 'string') {
      row = contactsRef.current.find(c => c.phone === rowOrPhone)
        || { phone: rowOrPhone, conversation_id: null, channel_id: 'default', contact_id: null, id: null };
    }
    setSelected(row.phone);
    setSelectedConvId(row.conversation_id ?? null);
    setSelectedChannelId(row.channel_id || 'default');
    if (row.conversation_id != null) {
      history.pushState(null, '', `/conversations/${row.conversation_id}`);
    } else if (row.contact_id != null || row.id != null) {
      history.pushState(null, '', `/contacts/${row.contact_id ?? row.id}`);
    } else {
      history.pushState(null, '', '/');
    }
  }, []);

  const handleSearchChange = useCallback((val) => {
    setSearch(val);
    setCheckPhoneError(null);
  }, []);

  const showArchivedRef = useRef(false);
  useEffect(() => { showArchivedRef.current = showArchived; }, [showArchived]);

  const fetchContacts = useCallback((q = '') => {
    setLoading(true);
    Promise.all([
      getContacts(q, showArchivedRef.current),
      listConversations({ archived: showArchivedRef.current, limit: 200 }),
    ]).then(([cRes, vRes]) => {
      if (cRes.ok) {
        const convs = (vRes && vRes.ok && vRes.data && vRes.data.conversations) || [];
        const rows = sortContacts(buildRows(cRes.data, convs));
        setContacts(rows);
        contactsRef.current = rows;
      }
      setLoading(false);
    });
  }, [sortContacts]);

  const handleStartConversation = useCallback(async (normalizedPhone) => {
    if (!normalizedPhone || checkingPhone) return;

    setCheckingPhone(true);
    setCheckPhoneError(null);

    try {
      const res = await checkPhone(normalizedPhone);
      if (!res.ok) {
        setCheckPhoneError(res.error || 'Erro ao verificar número.');
        setCheckingPhone(false);
        return;
      }

      if (!res.data.registered) {
        setCheckPhoneError('Este número não possui WhatsApp.');
        setCheckingPhone(false);
        return;
      }

      // Number is valid — use canonical phone from API (avoids BR duplicates)
      const canonicalPhone = res.data.phone || normalizedPhone;
      setCheckingPhone(false);
      setCheckPhoneError(null);
      setSearch('');
      selectContact(canonicalPhone);
      fetchContacts();
    } catch (e) {
      setCheckPhoneError('Erro ao verificar número. Tente novamente.');
      setCheckingPhone(false);
    }
  }, [checkingPhone, selectContact, fetchContacts]);

  const handleToggleArchived = useCallback(() => {
    setShowArchived(prev => !prev);
    setSelected(null);
    setSelectedConvId(null);
  }, []);

  // Initial load
  useEffect(() => { fetchContacts(); }, []);

  // Load global tags
  useEffect(() => {
    getTags().then(res => { if (res.ok) setGlobalTags(res.data); });
  }, []);

  // Identity + assignable agents (plano 10) drive "Minhas" and the assignee label
  // on each row; the users list feeds the context-menu "assign attendant" submenu.
  // All best-effort; degrade silently if forbidden.
  useEffect(() => {
    getMe().then(res => {
      if (res && res.ok && res.data && res.data.user) setCurrentUserId(res.data.user.id);
    }).catch(() => {});
    getAssignableAgents().then(res => {
      if (res && res.ok && res.data) {
        setAgentsUsers(Array.isArray(res.data.users) ? res.data.users : []);
        setAgentsAi(Array.isArray(res.data.ai_agents) ? res.data.ai_agents : []);
      }
    }).catch(() => {});
    getUsers().then((res) => {
      if (res && res.ok && res.data && Array.isArray(res.data.users)) setUsers(res.data.users);
    }).catch(() => {});
  }, []);

  // Real-time: patch a contact row when its conversation changes (assign /
  // resolve / IA). The conversation_* events carry contact_id (plano 10).
  const onConversationChanged = useCallback((name, data) => {
    const cid = data && data.contact_id;
    if (cid == null) return;
    setContacts(prev => prev.map(c => {
      if (c.id !== cid) return c;
      const patch = {};
      if (data.status !== undefined) patch.conv_status = data.status;
      if (data.assignee_user_id !== undefined) patch.assignee_user_id = data.assignee_user_id;
      if (data.active_agent_key !== undefined) patch.active_agent_key = data.active_agent_key;
      if (data.ai_active !== undefined) patch.conv_ai_active = data.ai_active;
      if (data.conversation_id != null && c.conversation_id == null) patch.conversation_id = data.conversation_id;
      return { ...c, ...patch };
    }));
  }, []);
  useWebSocket({ onConversationChanged });

  // Derived list: status + tag filter feed the tab counts; the active assignment
  // tab + sort produce what's actually rendered.
  const statusTagFiltered = useMemo(
    () => contacts.filter(c => matchesStatus(c, statusFilter) && matchesTags(c, tagFilter)),
    [contacts, statusFilter, tagFilter],
  );
  const tabCounts = useMemo(() => ({
    all: statusTagFiltered.length,
    mine: currentUserId == null ? 0 : statusTagFiltered.filter(c => c.assignee_user_id === currentUserId).length,
    unassigned: statusTagFiltered.filter(isUnassigned).length,
  }), [statusTagFiltered, currentUserId]);
  const displayedContacts = useMemo(
    () => sortContactsBy(statusTagFiltered.filter(c => matchesAssignment(c, assignmentTab, currentUserId)), sortBy),
    [statusTagFiltered, assignmentTab, currentUserId, sortBy],
  );
  useEffect(() => { displayedRef.current = displayedContacts; }, [displayedContacts]);

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

  // Resolve the contact's conversation whenever the context menu opens, so the
  // menu can show the current assignee and the resolve/reopen state. include_closed
  // so a resolved thread still resolves (lets us show "Reabrir conversa").
  useEffect(() => {
    if (!ctxMenu || !ctxMenu.phone) { setCtxConv({ loading: false, conv: null }); return; }
    const phone = ctxMenu.phone;
    let alive = true;
    setCtxConv({ loading: true, conv: null });
    getContactConversation(phone, { includeClosed: true }).then((res) => {
      if (!alive) return;
      setCtxConv({ loading: false, conv: (res && res.ok && res.data) ? res.data.conversation : null });
    }).catch(() => { if (alive) setCtxConv({ loading: false, conv: null }); });
    return () => { alive = false; };
  }, [ctxMenu]);

  // Reload when archive filter changes (and drop any active selection)
  useEffect(() => { fetchContacts(search); setSelectionMode(false); setSelectedPhones([]); }, [showArchived]);

  // Resolve initialConversationId (/conversations/:id) or initialContactId
  // (/contacts/:id, legacy) → a sidebar row, once the list is loaded. Mirrors the
  // contact-centric resolution but adds the conversation dimension.
  useEffect(() => {
    if (initialConversationId != null) {
      if (initialConversationId === lastResolvedConvId.current) return;
      if (contacts.length === 0 || loading) return;
      const row = contacts.find(c => c.conversation_id === initialConversationId);
      if (row) {
        setSelected(row.phone);
        setSelectedConvId(row.conversation_id);
        setSelectedChannelId(row.channel_id || 'default');
      } else {
        // Conversa fora da sidebar (ex: além do limite ou arquivada) — abre direto
        // por id; o load deriva o telefone/canal da resposta do endpoint.
        setSelected(null);
        setSelectedConvId(initialConversationId);
      }
      lastResolvedConvId.current = initialConversationId;
      lastResolvedId.current = null;
      return;
    }
    if (initialContactId == null) {
      // popstate back to "/" — deselect without pushing URL again
      if (lastResolvedId.current != null || lastResolvedConvId.current != null) {
        setSelected(null);
        setSelectedConvId(null);
        lastResolvedId.current = null;
        lastResolvedConvId.current = null;
      }
      return;
    }
    if (initialContactId === lastResolvedId.current) return;
    if (contacts.length === 0 || loading) return;
    // /contacts/:id opens that contact's newest conversation row (rows are sorted).
    const row = contacts.find(c => c.contact_id === initialContactId)
      || contacts.find(c => c.id === initialContactId);
    if (row) {
      setSelected(row.phone);
      setSelectedConvId(row.conversation_id ?? null);
      setSelectedChannelId(row.channel_id || 'default');
      lastResolvedId.current = initialContactId;
      lastResolvedConvId.current = null;
    }
  }, [initialContactId, initialConversationId, contacts, loading]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => fetchContacts(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Load chat detail when the open thread changes. Conversa-cêntrico: prefer the
  // per-conversation endpoint (scoped to one channel); fall back to the legacy
  // per-contact endpoint for rows without a conversation.
  useEffect(() => {
    if (!selected && selectedConvId == null) { setContactData(null); return; }
    if (openInfoAfterSelect.current) {
      openInfoAfterSelect.current = false;
      setShowInfoPanel(true);
    } else {
      setShowInfoPanel(false);
    }
    if (!hasLoadedDetail.current) setLoadingDetail(true);
    // Preserve any messages already buffered for this thread (arrived before selection)
    // but reset the accumulator for new messages arriving during fetch
    const bufKey = selected || (selectedConvId != null ? `conv:${selectedConvId}` : '');
    const preFetchBuffer = pendingWsMessages.current[bufKey] || [];
    pendingWsMessages.current[bufKey] = [];
    // Clear unread badges immediately on the OPEN row (only if page is visible)
    const isPageVisible = pageVisibleRef.current;
    if (isPageVisible) {
      setContacts(prev => prev.map(c =>
        isOpenRow(c) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
      ));
    }
    const convId = selectedConvId;
    const loader = convId != null
      ? getConversationMessages(convId, isPageVisible).then(res =>
          res.ok ? { ok: true, data: shapeConvData(res.data) } : res)
      : getContact(selected, isPageVisible);
    loader.then(res => {
      if (res.ok) {
        const data = res.data;
        if (data.channel_id) setSelectedChannelId(data.channel_id);
        // Opened by conversation id alone (row not in the sidebar) — adopt the
        // phone from the response so contact-level handlers key correctly.
        if (!selectedRef.current && data.phone) setSelected(data.phone);
        // Merge buffered messages: pre-fetch (arrived before click) + during-fetch (arrived during loading)
        const duringFetch = pendingWsMessages.current[bufKey] || [];
        const pending = [...preFetchBuffer, ...duringFetch];
        if (pending.length > 0) {
          const existing = data.messages || [];
          const newMsgs = pending.filter(m =>
            !existing.some(e =>
              (e.ts === m.ts && e.role === m.role) ||
              (e.role === m.role && e.content === m.content && Math.abs(e.ts - m.ts) < 30)
            )
          );
          if (newMsgs.length > 0) {
            data.messages = [...(data.messages || []), ...newMsgs];
          }
        }
        // Hydrate failed messages with _localId so retry button works after reload
        data.messages = (data.messages || []).map(m => {
          if (m.status === 'failed') {
            return { ...m, _localId: `loaded_${m.ts}`, _status: 'failed' };
          }
          return m;
        });
        pendingWsMessages.current[bufKey] = [];
        setContactData(data);
      }
      hasLoadedDetail.current = true;
      setLoadingDetail(false);
    });
  }, [selected, selectedConvId]);

  // Handle chat presence events (typing/recording indicators)
  useEffect(() => {
    if (!chatPresence) return;
    const { phone, state, media } = chatPresence;
    if (!phone) return;

    if (state === 'composing') {
      setTypingState(prev => ({ ...prev, [phone]: media === 'audio' ? 'audio' : 'text' }));
      // WhatsApp emits a single `composing` event (not heartbeated). Auto-clear after
      // 25s as a defensive fallback in case `paused` never arrives (e.g. dropped connection).
      clearTimeout(typingTimers.current[phone]);
      typingTimers.current[phone] = setTimeout(() => {
        setTypingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
      }, 25000);
    } else {
      // paused or unknown → clear
      clearTimeout(typingTimers.current[phone]);
      setTypingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
    }
  }, [chatPresence]);

  // Handle real-time contact info updates (e.g. from save_contact_info tool)
  useEffect(() => {
    if (!contactInfoUpdated) return;
    const { phone, info: updatedInfo } = contactInfoUpdated;
    console.log('[WS] contact_info_updated', phone, updatedInfo);
    if (!phone || !updatedInfo) return;

    // Update sidebar name (all rows of this phone share the contact name)
    setContacts(prev => prev.map(c =>
      c.phone === phone ? { ...c, name: updatedInfo.name || c.name } : c
    ));

    // Update detail view if this contact is selected
    if (phone === selectedRef.current) {
      setContactData(prev => prev ? { ...prev, info: { ...updatedInfo } } : prev);
    }
  }, [contactInfoUpdated]);

  // Handle global tags registry changes (create/update/delete)
  useEffect(() => {
    if (!tagsChanged) return;
    setGlobalTags(tagsChanged);
  }, [tagsChanged]);

  // Handle real-time AI toggle (e.g. from transfer_to_human tool)
  useEffect(() => {
    if (!contactAiToggled) return;
    const { phone, ai_enabled } = contactAiToggled;
    if (!phone) return;
    setContacts(prev => prev.map(c =>
      c.phone === phone ? { ...c, ai_enabled } : c
    ));
    if (phone === selectedRef.current) {
      setContactData(prev => prev ? { ...prev, ai_enabled } : prev);
    }
  }, [contactAiToggled]);

  // Handle contact-level tag changes
  useEffect(() => {
    if (!contactTagsUpdated) return;
    const { phone, tags } = contactTagsUpdated;
    if (!phone) return;
    setContacts(prev => prev.map(c =>
      c.phone === phone ? { ...c, tags } : c
    ));
    if (phone === selectedRef.current) {
      setContactData(prev => prev ? { ...prev, tags } : prev);
    }
  }, [contactTagsUpdated]);

  // Handle messages read (WhatsApp mobile ack or AI auto-read)
  useEffect(() => {
    if (!messagesRead) return;
    const { phone, only_user } = messagesRead;
    if (!phone) return;
    setContacts(prev => prev.map(c =>
      c.phone === phone
        ? { ...c, unread_count: 0, ...(only_user ? {} : { unread_ai_count: 0, has_unread_mention: false }) }
        : c
    ));
  }, [messagesRead]);

  // Handle delivery/read status updates for outgoing messages
  useEffect(() => {
    if (!messageStatus) return;
    const { msg_ids, status } = messageStatus;
    if (!msg_ids || !status) return;
    // Always try to update messages by msg_id in the current detail view
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      let changed = false;
      const updated = prev.messages.map(m => {
        if (m.msg_id && msg_ids.includes(m.msg_id) && m.status !== status) {
          changed = true;
          return { ...m, status };
        }
        return m;
      });
      return changed ? { ...prev, messages: updated } : prev;
    });
    // Update sidebar last message status (forward-only: sent → delivered → read)
    const { phone } = messageStatus;
    if (phone) {
      const STATUS_ORDER = { sent: 1, delivered: 2, read: 3 };
      setContacts(prev => prev.map(c => {
        if (c.phone === phone && c.last_message_role === 'assistant'
            && (STATUS_ORDER[status] || 0) > (STATUS_ORDER[c.last_message_status] || 0)) {
          return { ...c, last_message_status: status };
        }
        return c;
      }));
    }
  }, [messageStatus]);

  // Handle message deletions/revocations (from this panel, the phone, or the contact)
  useEffect(() => {
    if (!messageAction) return;
    const { action, phone, msg_id, db_id } = messageAction;
    if (phone && phone !== selectedRef.current) return;
    // Both revoke and "delete for me" keep the message in the list (and its content);
    // we only flag it as revoked so it renders with a scope-specific 'deleted'
    // indicator. action 'deleted' => "para mim"; 'revoked' => "para todos".
    const scope = action === 'deleted' ? 'me' : 'all';
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      let changed = false;
      const updated = prev.messages.map(m => {
        if (((msg_id && m.msg_id === msg_id) || (db_id && (m._id === db_id || m.id === db_id))) && !m.revoked) {
          changed = true;
          return { ...m, revoked: true, revoke_scope: scope };
        }
        return m;
      });
      return changed ? { ...prev, messages: updated } : prev;
    });
  }, [messageAction]);

  // Handle live avatar updates (background sweep / opening a conversation
  // detected a changed photo) — bump avatar_v so the <img> re-fetches.
  useEffect(() => {
    if (!avatarUpdated) return;
    const { phone, v } = avatarUpdated;
    if (!phone || !v) return;
    setContacts(prev => prev.map(c => c.phone === phone ? { ...c, avatar_v: v } : c));
    setContactData(prev => (prev && prev.phone === phone) ? { ...prev, avatar_v: v } : prev);
  }, [avatarUpdated]);

  // Handle reaction updates (from this panel, the phone, or the contact)
  useEffect(() => {
    if (!messageReaction) return;
    const { phone, msg_id, reactions } = messageReaction;
    if (phone && phone !== selectedRef.current) return;
    setContactData(prev => {
      if (!prev || !prev.messages) return prev;
      let changed = false;
      const updated = prev.messages.map(m => {
        if (msg_id && m.msg_id === msg_id) {
          changed = true;
          return { ...m, reactions: (reactions && Object.keys(reactions).length) ? reactions : undefined };
        }
        return m;
      });
      return changed ? { ...prev, messages: updated } : prev;
    });
  }, [messageReaction]);

  // Sync last assistant message status from chat detail → sidebar
  // Covers both WS updates and fresh data from API fetch
  useEffect(() => {
    if (!contactData || !contactData.messages || !selected) return;
    const msgs = contactData.messages;
    // Find the last visible (non-transcription/system) assistant message
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m.role === 'assistant' && m.status) {
        setContacts(prev => prev.map(c => {
          if (isOpenRow(c) && c.last_message_role === 'assistant' && m.status !== c.last_message_status) {
            return { ...c, last_message_status: m.status };
          }
          return c;
        }));
        break;
      }
    }
  }, [contactData, selected]);

  // A brand-new per-channel conversation just materialised on the backend
  // (conversation_created) — refetch so its row appears in the sidebar even before
  // any reply arrives (plano 11 D1). Events are rare, so no debounce is needed.
  useEffect(() => {
    if (!conversationCreated) return;
    fetchContacts(search);
  }, [conversationCreated]);

  // Handle real-time messages from WebSocket. Conversa-cêntrico routing: a message
  // belongs to the OPEN thread by conversation_id when present (operator/save
  // payloads), else by (phone, channel_id) — (phone, channel) uniquely identifies
  // a conversation, so the two channels of one number never cross-contaminate.
  useEffect(() => {
    if (!newMessage) return;
    const { phone, message } = newMessage;
    const msgConvId = message.conversation_id;
    const msgChannel = newMessage.channel_id || message.channel_id || 'default';

    let belongsToOpen;
    if (selectedConvIdRef.current != null) {
      belongsToOpen = (msgConvId != null)
        ? (msgConvId === selectedConvIdRef.current)
        : (phone === selectedRef.current && msgChannel === selectedChannelIdRef.current);
    } else {
      // Legacy contact-only open thread (no conversation) — route by phone.
      belongsToOpen = (phone === selectedRef.current);
    }

    if (belongsToOpen) {
      setContactData(prev => {
        if (!prev) {
          // Detail still loading — buffer under the open thread's phone key
          const buf = pendingWsMessages.current[phone] || [];
          if (!buf.some(m =>
            (m.ts === message.ts && m.role === message.role) ||
            (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
          )) {
            pendingWsMessages.current[phone] = [...buf, message];
          }
          return prev;
        }
        // Reconcile by GOWA msg_id first: a plugin may have rewritten the text
        // (e.g. appended a signature), so an optimistic/prior copy with the same
        // msg_id won't match by content — adopt the server's text in place
        // instead of appending a duplicate.
        if (message.msg_id && prev.messages) {
          const byId = prev.messages.findIndex(m => m.msg_id === message.msg_id);
          if (byId !== -1) {
            const updated = [...prev.messages];
            updated[byId] = {
              ...updated[byId],
              content: message.content != null ? message.content : updated[byId].content,
              status: message.status || updated[byId].status,
              _status: null,
            };
            return { ...prev, messages: updated };
          }
        }
        // Deduplicate by ts + role, or by content + role (within 30s window)
        const dupIdx = prev.messages ? prev.messages.findIndex(m =>
          (m.ts === message.ts && m.role === message.role) ||
          (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
        ) : -1;
        if (dupIdx !== -1) {
          // Merge ids/status from server into existing (optimistic) message
          if (message.msg_id || message.status || message._id) {
            const updated = [...prev.messages];
            updated[dupIdx] = { ...updated[dupIdx],
              ...(message.msg_id ? { msg_id: message.msg_id } : {}),
              ...(message._id && !updated[dupIdx]._id ? { _id: message._id } : {}),
              ...(message.status && !updated[dupIdx]._status ? { status: message.status } : {}),
            };
            return { ...prev, messages: updated };
          }
          return prev;
        }
        return {
          ...prev,
          messages: [...(prev.messages || []), message],
          updated_at: message.ts,
        };
      });
      if (message.role === 'user' && pageVisibleRef.current) markAsRead(phone);
    }

    // Skip contact list preview update for transcription, system_notice, tool_call, conversation_event, and error messages
    if (message.role === 'transcription' || message.role === 'system_notice' || message.role === 'tool_call' || message.role === 'conversation_event' || message.role === 'error') return;

    setContacts(prev => {
      // Target the row for this exact conversation/channel (not all rows of the phone).
      const idx = prev.findIndex(c =>
        (msgConvId != null && c.conversation_id === msgConvId) ||
        (msgConvId == null && c.phone === phone && (c.channel_id || 'default') === msgChannel)
      );
      if (idx >= 0) {
        const updated = [...prev];
        const isUserMsg = message.role === 'user';
        const isViewing = isOpenRow(updated[idx]) && pageVisibleRef.current;
        let lastPreview = (message.content || '').substring(0, 80);
        if (message.media_type === 'image') lastPreview = message.content || '📷 Imagem';
        else if (message.media_type === 'audio') lastPreview = '🎤 Áudio';
        else if (message.media_type === 'video') lastPreview = message.content || '🎥 Vídeo';
        else if (message.media_type === 'sticker') lastPreview = '🎨 Sticker';
        else if (message.media_type === 'document') lastPreview = message.content || '📄 Documento';
        else if (message.media_type === 'location') lastPreview = message.content || '📍 Localização';
        else if (message.media_type === 'live_location') lastPreview = '📍 Localização ao vivo';
        else if (message.media_type === 'poll') lastPreview = message.content || '📊 Enquete';
        else if (message.media_type === 'interactive') lastPreview = message.content || '↩️ Resposta';
        else if (message.media_type === 'order') lastPreview = message.content || '🛒 Pedido';
        else if (message.media_type === 'product') lastPreview = '🏷️ Produto';
        else if (message.media_type === 'contact' || message.media_type === 'contacts') lastPreview = message.content || '👤 Contato';
        updated[idx] = {
          ...updated[idx],
          last_message: lastPreview,
          last_message_role: message.role,
          last_message_ts: message.ts,
          last_message_status: message.status || '',
          last_message_msg_id: message.msg_id || '',
          msg_count: (updated[idx].msg_count || 0) + 1,
          unread_count: isUserMsg && !isViewing
            ? (updated[idx].unread_count || 0) + 1
            : updated[idx].unread_count || 0,
          unread_ai_count: message.role === 'assistant' && !isViewing
            ? (updated[idx].unread_ai_count || 0) + 1
            : updated[idx].unread_ai_count || 0,
          has_unread_mention: (message.mentioned && !isViewing)
            ? true
            : (updated[idx].has_unread_mention || false),
          updated_at: message.ts,
        };
        return sortContacts(updated);
      }
      // No matching row — likely a brand-new conversation/channel; refetch to
      // materialise it (a `conversation_created` event also nudges this).
      fetchContacts(search);
      return prev;
    });
  }, [newMessage]);

  const messages = contactData ? contactData.messages || [] : [];
  const info = contactData ? contactData.info || {} : {};
  const selectedKey = selectedConvId != null ? `conv:${selectedConvId}` : (selected ? `phone:${selected}` : null);

  const autoReply = config ? config.auto_reply : false;
  const handleToggleAutoReply = useCallback(async (newValue) => {
    if (onConfigSave) {
      await onConfigSave({ auto_reply: newValue });
    }
  }, [onConfigSave]);

  return html`
    <div class="flex flex-col lg:flex-row h-full">
      <!-- Sidebar (largura arrastável no desktop; w-full no mobile) -->
      <div
        class="shrink-0 border-r border-wa-border overflow-hidden ${isResizing ? '' : 'transition-all duration-300'} ${sidebarHidden ? 'lg:border-r-0' : ''} ${selected ? 'hidden lg:flex lg:flex-col' : 'flex flex-col w-full'}"
        style=${isDesktop ? `width:${sidebarHidden ? 0 : sidebarWidth}px` : ''}
      >
        <${ContactList}
          contacts=${displayedContacts}
          loading=${loading}
          search=${search}
          onSearchChange=${handleSearchChange}
          statusFilter=${statusFilter}
          onStatusChange=${setStatusFilter}
          assignmentTab=${assignmentTab}
          onAssignmentChange=${setAssignmentTab}
          tabCounts=${tabCounts}
          sortBy=${sortBy}
          onSortChange=${setSortBy}
          tagFilter=${tagFilter}
          onTagFilterChange=${setTagFilter}
          resolveAssignee=${resolveAssignee}
          hasIdentity=${currentUserId != null}
          selected=${selectedKey}
          showChannel=${showChannel}
          onSelect=${selectContact}
          onContextMenu=${setCtxMenu}
          typingState=${typingState}
          showArchived=${showArchived}
          onToggleArchived=${handleToggleArchived}
          globalTags=${globalTags}
          onStartConversation=${handleStartConversation}
          checkingPhone=${checkingPhone}
          checkPhoneError=${checkPhoneError}
          wsConnected=${wsConnected}
          autoReply=${autoReply}
          onToggleAutoReply=${handleToggleAutoReply}
          selectionMode=${selectionMode}
          selectedPhones=${selectedPhones}
          onEnterSelection=${enterSelection}
          onExitSelection=${exitSelection}
          onToggleSelect=${toggleSelect}
          onSelectAll=${selectAllContacts}
          onCreateTag=${handleCreateTag}
          onClearSelection=${clearSelection}
          onBulkAI=${handleBulkAI}
          onBulkArchive=${handleBulkArchive}
          onBulkTag=${handleBulkTag}
          onBulkRemoveAllTags=${handleBulkRemoveAllTags}
          onBulkPin=${handleBulkPin}
          onBulkMarkRead=${handleBulkMarkRead}
          onBulkMarkUnread=${handleBulkMarkUnread}
        />
      </div>
      <!-- Divisória redimensionável (desktop): arraste p/ redimensionar, clique p/ esconder -->
      <div
        class="hidden lg:flex items-center justify-center w-[14px] shrink-0 bg-wa-panel border-r border-wa-border select-none transition-colors ${isResizing ? 'bg-wa-teal/40' : 'hover:bg-wa-hover'} ${sidebarHidden ? 'cursor-pointer' : 'cursor-col-resize'}"
        onMouseDown=${startResize}
        title=${sidebarHidden ? 'Mostrar contatos' : 'Arraste para redimensionar • clique para esconder'}
        role="separator"
        aria-orientation="vertical"
      >
        <span class="text-wa-secondary text-[11px] pointer-events-none">${sidebarHidden ? '›' : '‹'}</span>
      </div>
      <!-- Chat panel -->
      <div class="flex-1 min-w-0 min-h-0 ${!selected ? 'hidden lg:flex' : 'flex'} relative">
        <div class="w-full h-full flex flex-col">
          ${loadingDetail
            ? html`<div class="flex items-center justify-center h-full bg-wa-panel text-wa-secondary animate-pulse-slow text-[14px]">Carregando...</div>`
            : html`<${ContactDetail}
                phone=${selected}
                conversationId=${selectedConvId}
                onBack=${() => selectContact(null)}
                messages=${messages}
                setContactData=${setContactData}
                info=${info}
                contact=${contactData}
                onAvatarClick=${() => selected && setShowInfoPanel(true)}
                contactTyping=${selected && typingState[selected] || null}
                globalTags=${globalTags}
                groupParticipantsChanged=${groupParticipantsChanged}
                scrollToMsg=${scrollToMsg}
                onScrolledToMsg=${() => setScrollToMsg(null)}
              />`
          }
          ${showInfoPanel && selected ? html`
            <${ContactInfoPanel}
              phone=${selected}
              conversationId=${selectedConvId}
              info=${info}
              contactTags=${contactData && contactData.tags || []}
              globalTags=${globalTags}
              onGlobalTagsChange=${setGlobalTags}
              isGroup=${contactData && contactData.is_group}
              groupName=${contactData && contactData.group_name}
              avatarV=${contactData && contactData.avatar_v}
              onClose=${() => setShowInfoPanel(false)}
              onSave=${(updatedInfo, updatedTags) => {
                setContactData(prev => prev ? { ...prev, info: updatedInfo, tags: updatedTags } : prev);
                setContacts(prev => prev.map(c =>
                  c.phone === selected ? { ...c, name: updatedInfo.name || c.name, tags: updatedTags } : c
                ));
                setShowInfoPanel(false);
              }}
            />
          ` : null}
        </div>
      </div>
      ${ctxMenu ? html`
        <${ContextMenu}
          x=${ctxMenu.x}
          y=${ctxMenu.y}
          phone=${ctxMenu.phone}
          aiEnabled=${ctxMenu.aiEnabled}
          contactTags=${ctxMenu.tags}
          globalTags=${globalTags}
          isArchived=${ctxMenu.isArchived}
          isUnread=${ctxMenu.isUnread}
          isPinned=${ctxMenu.isPinned}
          conv=${ctxConv.conv}
          convLoading=${ctxConv.loading}
          convError=${ctxConv.error}
          users=${users}
          currentUserId=${currentUserId}
          onAssignConversation=${handleAssignConversation}
          onResolveConversation=${handleResolveConversation}
          onToggleAI=${handleToggleAI}
          onEditContact=${(phone) => {
            if (selectedRef.current === phone) {
              // Already open — the [selected] effect won't refire, so open directly.
              setShowInfoPanel(true);
            } else {
              openInfoAfterSelect.current = true;
              selectContact(phone);
            }
          }}
          onMarkUnread=${handleMarkUnread}
          onMarkRead=${handleMarkRead}
          onTagsUpdate=${(phone, newTags) => {
            setContacts(prev => prev.map(c => c.phone === phone ? { ...c, tags: newTags } : c));
            setCtxMenu(prev => prev && prev.phone === phone ? { ...prev, tags: newTags } : prev);
            if (phone === selectedRef.current) {
              setContactData(prev => prev ? { ...prev, tags: newTags } : prev);
            }
          }}
          onArchive=${handleArchive}
          onPin=${handlePin}
          onDelete=${handleDelete}
          onCreateTag=${handleCreateTag}
          onClose=${() => setCtxMenu(null)}
        />
      ` : null}
    </div>
  `;
}
