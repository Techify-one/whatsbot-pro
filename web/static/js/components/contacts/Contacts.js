import { h } from 'preact';
import { useState, useEffect, useRef, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';
import { getContacts, getContact, getConversationMessages, listConversations, markAsRead, markAsUnread, setConversationAi, deleteConversation, getTags, deleteContact, archiveContact, pinContact, checkPhone, updateContactTags, createTag, getMe, getAssignableAgents, getUsers, getContactConversation, getConversation, assignConversation, assignMeConversation, setConversationStatus, listConnectedChannels, getChannelSessionState, listSavedFilters, createSavedFilter, updateSavedFilter, deleteSavedFilter } from '../../services/api.js';
import { resolveConversation } from '../../utils/resolveConversation.js';
import { formatPhoneDisplay } from '../../utils/phone.js';
import { isDuplicateMessage, findDuplicateIndex, mediaPreviewLabel } from '../../services/messages.js';
import { applyConversationEvent, eventTargetsRow, isConversationAttributeWrite } from '../../services/conversationPatch.js';
import { ContactList, typingKey, rowKeyFor } from './ContactList.js';
import { ContactDetail } from './ContactDetail.js';
import { ContactInfoPanel } from './ContactInfoPanel.js';
import { ConversationInfoPanel } from './ConversationInfoPanel.js';
import { ContextMenu } from './ContextMenu.js';
import { ChannelPickerModal } from './ChannelPickerModal.js';
import { NewConversationModal } from './NewConversationModal.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';
import { emit as emitClientEvent } from '../../plugins/registry.js';

// ── Sidebar resize (barra lateral arrastável) ───────────────────────
// Largura da lista de conversas: arrastável no desktop e persistida em
// localStorage (mesmo padrão do tema). No mobile a barra é `w-full` e estes
// valores não se aplicam.
const SIDEBAR_WIDTH_KEY = 'whatsbot_sidebar_width';
// Persists which saved conversation-filter preset is applied, so it survives a
// page reload (per device). Stores the preset id; re-applied once the presets load.
const ACTIVE_FILTER_KEY = 'whatsbot_active_conv_filter';
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
// ── Filtros avançados (Chatwoot-style: Canais / Agente / Etiqueta / Última atividade) ──
// Cláusula: { dim, op, value }. Avaliadas client-side em AND sobre as linhas já
// carregadas. Cada linha carrega channel_id, assignee_user_id, active_agent_key,
// tags e last_message_ts — tudo o que essas dimensões precisam.
const DAY_SECONDS = 86400;

function clauseMatches(c, cl, now) {
  const { dim, op } = cl;
  const value = cl.value;
  if (value === '' || value == null) return true;   // cláusula incompleta → ignorada
  if (dim === 'channel') {
    const ch = c.channel_id || 'default';
    return op === 'ne' ? ch !== value : ch === value;
  }
  if (dim === 'status') {
    if (value === 'all') return true;                // "Todas" → não restringe
    const st = c.conv_status || 'open';              // 'open' | 'closed'
    return op === 'ne' ? st !== value : st === value;
  }
  if (dim === 'tag') {
    const has = (c.tags || []).includes(value);
    return op === 'ne' ? !has : has;
  }
  if (dim === 'agent') {
    let hit;
    if (value === 'none') hit = (c.assignee_user_id == null && !c.active_agent_key);
    else if (value.startsWith('user:')) hit = String(c.assignee_user_id) === value.slice(5);
    else if (value.startsWith('ai:')) hit = (c.active_agent_key || '') === value.slice(3);
    else hit = false;
    return op === 'ne' ? !hit : hit;
  }
  if (dim === 'activity') {
    const n = parseFloat(value);
    if (!Number.isFinite(n)) return true;
    const ts = c.last_message_ts || c.updated_at || 0;
    const ageDays = ts ? (now - ts) / DAY_SECONDS : Infinity;  // sem atividade → "muito antigo"
    if (op === 'gt') return ageDays > n;                       // há MAIS de N dias
    if (op === 'lt') return ageDays < n;                       // há MENOS de N dias
    if (op === 'days_before') return Math.floor(ageDays) === Math.floor(n);  // há exatamente N dias
    return true;
  }
  return true;
}

function matchesAdvFilters(c, advFilters, now) {
  if (!advFilters || advFilters.length === 0) return true;
  return advFilters.every(cl => clauseMatches(c, cl, now));
}

// Filtro simples (funil da esquerda) — etiquetas do contato, "é uma de" (OR).
function matchesTags(c, tagFilter) {
  if (!tagFilter || tagFilter.length === 0) return true;
  const ctags = c.tags || [];
  return tagFilter.some(t => ctags.includes(t));
}
// ── Saved-filter spec helpers ──────────────────────────────────────
// A filter preset is the full snapshot {statusFilter, sortBy, tagFilter,
// advFilters}. `normalizeSpec` drops the ephemeral clause ids and sorts
// tags/clauses so two equivalent filters compare equal regardless of the order
// they were built in. The assignment tab (Minhas / Não atribuídas / Todas) is
// NOT part of a filter — it's a standalone view selector, so it never counts
// toward "filtro ativo" nor is saved into a preset.
const DEFAULT_SPEC = { statusFilter: 'open', sortBy: 'activity', tagFilter: [], advFilters: [] };

function normalizeSpec(spec) {
  const s = spec || {};
  const tags = [...(s.tagFilter || [])].sort();
  const adv = (s.advFilters || [])
    .map(f => ({ dim: f.dim, op: f.op, value: String(f.value ?? '') }))
    .sort((a, b) => (a.dim + a.op + a.value).localeCompare(b.dim + b.op + b.value));
  return {
    statusFilter: s.statusFilter || 'open',
    sortBy: s.sortBy || 'activity',
    tagFilter: tags,
    advFilters: adv,
  };
}

function specsEqual(a, b) {
  return JSON.stringify(normalizeSpec(a)) === JSON.stringify(normalizeSpec(b));
}

function isDefaultSpec(spec) { return specsEqual(spec, DEFAULT_SPEC); }

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

// 55 85 97360559 → +55 (85) 97360-559 (espelha o helper da ContactList).
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
          // Per-conversation AI gate (plano 17) — drives the "IA OFF" badge and the
          // right-click toggle. WS patches keep this in sync as `conv_ai_active`.
          conv_ai_active: cv.ai_active,
          assignee_user_id: cv.assignee_user_id,
          active_agent_key: cv.active_agent_key,
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
    // Compositor hints (Frente C): template capability + 24h session window.
    templates_supported: !!d.templates_supported,
    session_open: d.session_open,
  };
}

// ── Main Component ───────────────────────────────────────────────

export function Contacts({ newMessage, chatPresence, aiTyping, contactInfoUpdated, tagsChanged, contactTagsUpdated, contactAiToggled, messagesRead, messageStatus, messageAction, messageReaction, avatarUpdated, groupParticipantsChanged, conversationCreated, initialContactId, initialConversationId, initialScrollMsgId = null, wsConnected, config, onConfigSave, onUnreadChange }) {
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
  // Which side drawer is open: 'contact' (foto/nome) | 'conversation' (botão ℹ️) |
  // null. Single state so opening one closes the other (no overlapping drawers).
  const [openPanel, setOpenPanel] = useState(null);
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
  const [aiRespondingState, setAiRespondingState] = useState({});  // { phone: true } — IA processando
  const [showArchived, setShowArchived] = useState(false);
  const [globalTags, setGlobalTags] = useState({});
  // Conversation tabs/filters (plano 10 FF2) — applied client-side over `contacts`.
  const [statusFilter, setStatusFilter] = useState('open');   // open|closed|all (default Abertas)
  const [assignmentTab, setAssignmentTab] = useState('all');  // all|mine|unassigned
  const [sortBy, setSortBy] = useState('activity');           // activity|oldest|unread
  const [tagFilter, setTagFilter] = useState([]);             // funil simples (esquerda) — etiquetas
  const [advFilters, setAdvFilters] = useState([]);           // [{dim, op, value}] — filtro avançado (direita)
  // Saved filter presets (Chatwoot-style): named snapshots persisted per user.
  // `activeFilterId` = the preset currently applied (null = ad-hoc/none); the
  // toolbar shows its name in the corner and flags it "modificado" once the live
  // filters diverge from the saved spec.
  const [savedFilters, setSavedFilters] = useState([]);
  const [activeFilterId, setActiveFilterId] = useState(null);
  const [agentsUsers, setAgentsUsers] = useState([]);         // assignable human agents
  const [agentsAi, setAgentsAi] = useState([]);               // assignable AI agents
  const [checkingPhone, setCheckingPhone] = useState(false);
  const [checkPhoneError, setCheckPhoneError] = useState(null);
  // Popup de escolha de caixa de entrada ao iniciar uma conversa nova (multicanal).
  const [channelPicker, setChannelPicker] = useState(null);  // {phone, phoneDisplay, channels} | null
  // Modal "Iniciar conversa" (compor número + canal + 1ª mensagem) — menu da engrenagem.
  const [showNewConversation, setShowNewConversation] = useState(false);
  const [selectionMode, setSelectionMode] = useState(false);
  // Selection keyed per CONVERSATION row (rowKeyFor), so two conversations of the
  // same number (different channels) are selected independently.
  const [selectedKeys, setSelectedKeys] = useState([]);
  const pendingWsMessages = useRef({});
  const selectedRef = useRef(null);
  const selectedConvIdRef = useRef(null);
  const selectedChannelIdRef = useRef('default');
  // Canal escolhido no picker para uma conversa NOVA ainda sem conversa naquele
  // canal — consumido (e zerado) pelo loader de detalhe para escopar o getContact
  // ao canal certo (multicanal), em vez de fundir/abrir a conversa de outro canal.
  const newConvChannelRef = useRef(null);
  const typingTimers = useRef({});
  const aiTypingTimers = useRef({});
  const contactsRef = useRef([]);
  const displayedRef = useRef([]);   // currently-visible (filtered) rows — for "selecionar todas"
  const lastResolvedId = useRef(null);
  const lastResolvedConvId = useRef(null);
  const searchRef = useRef('');                // current search term (for ref-based refetch)
  const fetchContactsRef = useRef(null);       // stable handle to fetchContacts
  const convListRefetchTimer = useRef(null);   // debounce for membership-change refetch
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

  // Canais presentes nas conversas carregadas → opções do filtro "Canais". Derivado
  // das próprias linhas (mostra exatamente os canais em uso, sem fetch extra).
  const channelOptions = useMemo(() => {
    const map = new Map();
    for (const c of contacts) {
      const id = c.channel_id || 'default';
      if (!map.has(id)) {
        const label = c.channel_name
          || c.channel_provider
          || (id === 'default' ? 'Padrão' : id);
        map.set(id, label);
      }
    }
    return Array.from(map, ([id, label]) => ({ id, label }));
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
  }, []);

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
    // Funnel through resolveConversation so the beforeResolve filter (plugins) runs
    // here too. Pass an object so the filter gets the conversation id for context.
    const res = await resolveConversation({ id: convId }, status);
    if (res && res.ok && res.data && res.data.conversation) {
      patchCtxConv({ status: res.data.conversation.status });
    } else {
      setCtxConv(prev => ({ ...prev, error: (res && res.error) || 'Falha ao atualizar status.' }));
    }
  }, [patchCtxConv]);

  // ── Selection mode (bulk actions) ───────────────────────────────
  const enterSelection = useCallback(() => { setSelectionMode(true); setSelectedKeys([]); }, []);
  const exitSelection = useCallback(() => { setSelectionMode(false); setSelectedKeys([]); }, []);
  const toggleSelect = useCallback((key) => {
    setSelectedKeys(prev => prev.includes(key)
      ? prev.filter(k => k !== key)
      : [...prev, key]);
  }, []);
  const selectAllContacts = useCallback(() => {
    setSelectedKeys([...new Set(displayedRef.current.map(rowKeyFor))]);
  }, []);
  const clearSelection = useCallback(() => { setSelectedKeys([]); setSelectionMode(false); }, []);

  // Resolve the selected row keys back to their conversation rows. Each key maps to
  // a single row (a specific conversation/channel), so the two channels of the same
  // number are handled independently.
  const _selectedRows = useCallback(() => {
    const keys = new Set(selectedKeys);
    return contactsRef.current.filter(c => keys.has(rowKeyFor(c)));
  }, [selectedKeys]);

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
  }, [_selectedRows]);

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
  }, [_selectedRows, exitSelection]);

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
  }, [_selectedRows, sortContacts]);

  const handleBulkMarkRead = useCallback(async () => {
    const phones = [...new Set(_selectedRows().map(c => c.phone))];
    if (!phones.length) return;
    await Promise.all(phones.map(p => markAsRead(p).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false } : c
    ));
  }, [_selectedRows]);

  const handleBulkMarkUnread = useCallback(async () => {
    const phones = [...new Set(_selectedRows().map(c => c.phone))];
    if (!phones.length) return;
    await Promise.all(phones.map(p => markAsUnread(p).catch(() => null)));
    setContacts(prev => prev.map(c =>
      phones.includes(c.phone) ? { ...c, unread_count: Math.max(c.unread_count || 0, 1) } : c
    ));
  }, [_selectedRows]);

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
      // Client-side plugin event (plano 23 §3.4) — conversation deselected.
      // Fire-and-forget; emit() isolates throwing subscribers, never blocks the UI.
      emitClientEvent('ui.conversation.selected', { conversationId: null, phone: null, channelId: null });
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
    // Client-side plugin event (plano 23 §3.4): a conversation/row was selected.
    // Minimal + stable payload; fire-and-forget (emit() swallows subscriber errors).
    emitClientEvent('ui.conversation.selected', {
      conversationId: row.conversation_id ?? null,
      phone: row.phone ?? null,
      channelId: row.channel_id || 'default',
    });
    if (row.conversation_id != null) {
      history.pushState(null, '', `/conversations/${row.conversation_id}`);
    } else {
      // Sem conversa ainda: a seleção fica no estado; /contacts/{id} agora é a tela
      // de detalhe do contato (não o hub), então a URL volta pra raiz do hub.
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

  // Stable handles so the []-dep WS callback can refetch with the current search.
  useEffect(() => { searchRef.current = search; }, [search]);
  useEffect(() => { fetchContactsRef.current = fetchContacts; }, [fetchContacts]);

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

      // Multicanal: deixar o operador escolher por qual caixa de entrada CONECTADA
      // iniciar a conversa. O backend já filtra desconectadas/desabilitadas.
      const chRes = await listConnectedChannels();
      const channels = (chRes && chRes.ok && Array.isArray(chRes.data)) ? chRes.data : [];
      setCheckingPhone(false);
      setCheckPhoneError(null);

      if (channels.length === 0) {
        setCheckPhoneError('Nenhuma caixa de entrada conectada para iniciar a conversa.');
        return;
      }

      setChannelPicker({
        phone: canonicalPhone,
        // Exibir o número como o operador digitou (preserva o 9º dígito e o
        // DDD com/sem zero à esquerda). O `canonicalPhone` normalizado pelo
        // backend pode soltar o 9º dígito em celulares BR, o que quebra o
        // slice do formatPhoneDisplay (ex.: +55 (64) 91111-001). O roteamento
        // continua usando `canonicalPhone`; só o rótulo usa o que foi digitado.
        phoneDisplay: formatPhoneDisplay(normalizedPhone),
        channels,
      });
    } catch (e) {
      setCheckPhoneError('Erro ao verificar número. Tente novamente.');
      setCheckingPhone(false);
    }
  }, [checkingPhone]);

  // Caixa de entrada escolhida no popup — abre a thread DAQUELE canal. Resolve a
  // conversa existente do contato NAQUELE canal (multicanal): se já houver, abre-a
  // escopada (não cai na conversa de outro canal do mesmo número); se não houver,
  // abre um thread vazio do canal escolhido (a 1ª mensagem é roteada por ele).
  const openInChannel = useCallback(async (phone, channelId) => {
    setSearch('');
    let convId = null;
    try {
      const ss = await getChannelSessionState(channelId, phone);
      if (ss && ss.ok && ss.data && ss.data.conversation_id) convId = ss.data.conversation_id;
    } catch (e) { /* best-effort: cai no thread vazio do canal */ }
    // Sem conversa ainda nesse canal: marca o canal para o loader escopar o
    // getContact (senão ele funde os canais e mostra a conversa errada).
    newConvChannelRef.current = convId == null ? channelId : null;
    selectContact({
      phone,
      conversation_id: convId,
      channel_id: channelId,
      contact_id: null,
      id: null,
    });
    fetchContacts();
  }, [selectContact, fetchContacts]);

  const handlePickChannel = useCallback((channel) => {
    const picker = channelPicker;
    if (!picker) return;
    setChannelPicker(null);
    openInChannel(picker.phone, channel.id);
  }, [channelPicker, openInChannel]);

  // 1ª mensagem enviada pelo modal "Iniciar conversa" — fecha o modal, recarrega a
  // sidebar (a conversa nova já aparece) e abre a thread vinculada ao canal usado.
  const handleNewConversationSent = useCallback((phone, channelId) => {
    setShowNewConversation(false);
    openInChannel(phone, channelId);
  }, [openInChannel]);

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
  // resolve / IA). The conversation_* events carry contact_id (plano 10) and
  // conversation_id (plano 11). Conversa-cêntrico: um contato pode ter várias
  // linhas (uma por canal); casar só por contact_id resolveria/atribuiria TODAS
  // as conversas do número. Linhas COM conversa casam por conversation_id;
  // linhas sem conversa (legado) ainda casam por contact_id e adotam a nova.
  // P19: latest conversation-scope custom-attribute write, forwarded to the open
  // ConversationInfoPanel for live refresh (mirrors contact_info_updated → panel).
  const [convAttrPatch, setConvAttrPatch] = useState(null);
  const onConversationChanged = useCallback((name, data) => {
    const cid = data && data.contact_id;
    const convId = data && data.conversation_id;
    if (cid == null && convId == null) return;
    // Conversation deleted (plano 16): drop the row by conversation_id and clear
    // the open thread if it was the one deleted.
    if (name === 'conversation_deleted') {
      if (convId == null) return;
      setContacts(prev => prev.filter(c => c.conversation_id !== convId));
      if (selectedConvIdRef.current === convId) {
        setSelected(null);
        setSelectedConvId(null);
        setContactData(null);
        history.pushState(null, '', '/');
      }
      return;
    }
    // P19: a conversation-scope custom-attribute write arrives as conversation_updated
    // with fields.custom_attributes — forward it to the open ConversationInfoPanel so
    // it refreshes live (the sidebar patch below doesn't render attribute values).
    if (isConversationAttributeWrite(data)) {
      setConvAttrPatch({ conversation_id: convId, custom_attributes: data.fields.custom_attributes, ts: Date.now() });
    }
    setContacts(prev => applyConversationEvent(prev, data));
    // Membership safety net: a status change can move a conversation INTO the active
    // (client-side) status filter. The patch above only touches rows already loaded —
    // if the (re)opened conversation isn't in the list yet (e.g. it was closed and
    // fell outside the initial fetch window), refetch so it materialises live instead
    // of waiting for an F5. Mirrors the new_message + kanban fallbacks; debounced so a
    // burst of events refetches once.
    if (data.status !== undefined) {
      const present = contactsRef.current.some(c => eventTargetsRow(c, data));
      if (!present) {
        if (convListRefetchTimer.current) clearTimeout(convListRefetchTimer.current);
        convListRefetchTimer.current = setTimeout(() => {
          if (fetchContactsRef.current) fetchContactsRef.current(searchRef.current);
        }, 400);
      }
    }
  }, []);
  useWebSocket({ onConversationChanged });

  // Só entram na sidebar conversas com mensagem real trocada (last_message_ts > 0,
  // que já exclui eventos painel-only). Um contato recém-criado sem mensagem — ex:
  // recriado pela importação de chats do GOWA, ou aberto pelo modal sem enviar nada
  // — não deve poluir a lista. A conversa atualmente ABERTA fica visível mesmo sem
  // mensagem, pra não sumir enquanto o operador digita a 1ª mensagem.
  const activeContacts = useMemo(() => {
    const selKey = selectedConvId != null ? `conv:${selectedConvId}` : (selected ? `phone:${selected}` : null);
    return contacts.filter(c => {
      if (c.last_message_ts && c.last_message_ts > 0) return true;
      const key = c.conversation_id != null ? `conv:${c.conversation_id}` : `phone:${c.phone}`;
      return key === selKey;
    });
  }, [contacts, selected, selectedConvId]);

  // Derived list: status + tag filter feed the tab counts; the active assignment
  // tab + sort produce what's actually rendered.
  const statusTagFiltered = useMemo(() => {
    const now = Date.now() / 1000;
    // A status clause in the advanced filter overrides the status chip, so the two
    // never AND into an empty list (e.g. chip "Abertas" + cláusula "Fechada").
    const hasStatusClause = (advFilters || []).some(
      cl => cl.dim === 'status' && cl.value !== '' && cl.value != null);
    return activeContacts.filter(c =>
      (hasStatusClause || matchesStatus(c, statusFilter))
      && matchesTags(c, tagFilter)
      && matchesAdvFilters(c, advFilters, now));
  }, [activeContacts, statusFilter, tagFilter, advFilters]);
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

  // ── Saved filter presets ─────────────────────────────────────────
  // Load the user's presets once on mount (best-effort; degrade silently). If a
  // preset was active before a reload (persisted in localStorage), re-apply it so
  // the inbox comes back filtered — not reset.
  useEffect(() => {
    listSavedFilters().then(res => {
      if (!res || !res.ok || !Array.isArray(res.data)) return;
      setSavedFilters(res.data);
      let storedId = null;
      try { storedId = parseInt(localStorage.getItem(ACTIVE_FILTER_KEY) || '', 10); } catch {}
      if (storedId != null && !Number.isNaN(storedId)) {
        const preset = res.data.find(f => f.id === storedId);
        if (preset) applySavedFilter(preset);
        else { try { localStorage.removeItem(ACTIVE_FILTER_KEY); } catch {} }
      }
    }).catch(() => {});
  }, []);

  // Live filter snapshot — what a "Salvar filtro" would persist right now.
  // assignmentTab is intentionally excluded: switching tabs is a view change,
  // not a filter.
  const currentSpec = useMemo(() => ({
    statusFilter, sortBy, tagFilter, advFilters,
  }), [statusFilter, sortBy, tagFilter, advFilters]);

  // The applied preset (if any) + whether the live filters still match it. Once
  // the operator tweaks anything, `modified` flips true so the chip can offer to
  // update/save the preset.
  const activeFilter = useMemo(() => {
    if (activeFilterId == null) return null;
    const preset = savedFilters.find(f => f.id === activeFilterId);
    if (!preset) return null;
    return { ...preset, modified: !specsEqual(currentSpec, preset.spec) };
  }, [activeFilterId, savedFilters, currentSpec]);

  // Drop the active-preset binding whenever the live filters are reset to the
  // defaults (e.g. "Limpar filtros") — nothing is "in use" then.
  useEffect(() => {
    if (activeFilterId != null && isDefaultSpec(currentSpec)) {
      setActiveFilterId(null);
      try { localStorage.removeItem(ACTIVE_FILTER_KEY); } catch {}
    }
  }, [activeFilterId, currentSpec]);

  // Apply a preset: hydrate every filter dimension from its spec and mark it active.
  const applySavedFilter = useCallback((preset) => {
    const s = normalizeSpec(preset.spec);
    setStatusFilter(s.statusFilter);
    setSortBy(s.sortBy);
    setTagFilter(s.tagFilter);
    // Re-seed clause ids so the advanced dialog can edit rows without collisions.
    setAdvFilters(s.advFilters.map((f, i) => ({ ...f, id: `s${preset.id}_${i}` })));
    setActiveFilterId(preset.id);
    try { localStorage.setItem(ACTIVE_FILTER_KEY, String(preset.id)); } catch {}
  }, []);

  // Persist the current filters under a new name.
  const saveCurrentFilter = useCallback(async (name) => {
    const res = await createSavedFilter(name, normalizeSpec(currentSpec));
    if (res && res.ok && res.data) {
      setSavedFilters(prev => [...prev, res.data]);
      setActiveFilterId(res.data.id);
      try { localStorage.setItem(ACTIVE_FILTER_KEY, String(res.data.id)); } catch {}
      return { ok: true };
    }
    return { ok: false, error: (res && res.error) || 'Falha ao salvar o filtro.' };
  }, [currentSpec]);

  // Overwrite an existing preset's spec with the current filters (the "modificado"
  // chip → "Atualizar" action).
  const overwriteSavedFilter = useCallback(async (id) => {
    const res = await updateSavedFilter(id, { spec: normalizeSpec(currentSpec) });
    if (res && res.ok && res.data) {
      setSavedFilters(prev => prev.map(f => (f.id === id ? res.data : f)));
      setActiveFilterId(id);
      try { localStorage.setItem(ACTIVE_FILTER_KEY, String(id)); } catch {}
      return { ok: true };
    }
    return { ok: false, error: (res && res.error) || 'Falha ao atualizar o filtro.' };
  }, [currentSpec]);

  const renameSavedFilter = useCallback(async (id, name) => {
    const res = await updateSavedFilter(id, { name });
    if (res && res.ok && res.data) {
      setSavedFilters(prev => prev.map(f => (f.id === id ? res.data : f)));
      return { ok: true };
    }
    return { ok: false, error: (res && res.error) || 'Falha ao renomear o filtro.' };
  }, []);

  const removeSavedFilter = useCallback(async (id) => {
    const res = await deleteSavedFilter(id);
    if (res && res.ok) {
      setSavedFilters(prev => prev.filter(f => f.id !== id));
      if (activeFilterId === id) {
        setActiveFilterId(null);
        try { localStorage.removeItem(ACTIVE_FILTER_KEY); } catch {}
      }
      return { ok: true };
    }
    return { ok: false, error: (res && res.error) || 'Falha ao excluir o filtro.' };
  }, [activeFilterId]);

  // Clear all live filters back to the defaults and unbind any active preset.
  const clearAllFilters = useCallback(() => {
    setStatusFilter('open');
    setSortBy('activity');
    setTagFilter([]);
    setAdvFilters([]);
    setActiveFilterId(null);
    try { localStorage.removeItem(ACTIVE_FILTER_KEY); } catch {}
  }, []);

  // True when any filter dimension differs from the defaults — drives the "Salvar
  // filtro" / "Limpar" affordances in the toolbar.
  const anyFilterActive = useMemo(() => !isDefaultSpec(currentSpec), [currentSpec]);

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
    const convId = ctxMenu.conversationId;
    let alive = true;
    setCtxConv({ loading: true, conv: null });
    // Conversa-cêntrico: a linha clicada conhece sua conversa — carrega ELA por id.
    // getContactConversation(phone) resolveria só uma das conversas do número e o
    // menu agiria no canal errado (resolver/reabrir afetaria a conversa errada).
    const fetch = convId != null
      ? getConversation(convId)
      : getContactConversation(phone, { includeClosed: true });
    fetch.then((res) => {
      if (!alive) return;
      setCtxConv({ loading: false, conv: (res && res.ok && res.data) ? res.data.conversation : null });
    }).catch(() => { if (alive) setCtxConv({ loading: false, conv: null }); });
    return () => { alive = false; };
  }, [ctxMenu]);

  // Reload when archive filter changes (and drop any active selection)
  useEffect(() => { fetchContacts(search); setSelectionMode(false); setSelectedKeys([]); }, [showArchived]);

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
      // Permalink (?message=<_id>): foca a mensagem assim que a conversa renderiza.
      // Lido no momento da resolução e consumido uma vez (onScrolledToMsg limpa);
      // a conversa carrega TODAS as mensagens, então o alvo está sempre no DOM.
      if (initialScrollMsgId != null) setScrollToMsg(initialScrollMsgId);
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
      setOpenPanel('contact');
    } else {
      setOpenPanel(null);
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
    // Conversa nova sem conversation_id ainda: escopa o getContact ao canal
    // escolhido no picker (lido-e-zerado aqui), para não fundir os canais nem
    // abrir a conversa de outro canal do mesmo número (multicanal).
    const newConvChannel = newConvChannelRef.current;
    newConvChannelRef.current = null;
    const loader = convId != null
      ? getConversationMessages(convId, isPageVisible).then(res =>
          res.ok ? { ok: true, data: shapeConvData(res.data) } : res)
      : getContact(selected, isPageVisible, newConvChannel);
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
          const newMsgs = pending.filter(m => !isDuplicateMessage(m, existing));
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

  // Handle chat presence events (typing/recording indicators). Conversa-cêntrico:
  // a presença pertence a UMA conversa (o canal GOWA que reportou). Casamos por
  // conversation_id quando o evento o traz (inequívoco), senão por canal::telefone.
  // Assim só a conversa do canal que emitiu mostra "digitando" — mesmo fechada.
  useEffect(() => {
    if (!chatPresence) return;
    const { phone, state, media } = chatPresence;
    if (!phone) return;
    const key = typingKey({
      conversationId: chatPresence.conversation_id,
      channelId: chatPresence.channel_id,
      phone,
    });

    if (state === 'composing') {
      setTypingState(prev => ({ ...prev, [key]: media === 'audio' ? 'audio' : 'text' }));
      // WhatsApp emits a single `composing` event (not heartbeated). Auto-clear after
      // 25s as a defensive fallback in case `paused` never arrives (e.g. dropped connection).
      clearTimeout(typingTimers.current[key]);
      typingTimers.current[key] = setTimeout(() => {
        setTypingState(prev => { const n = { ...prev }; delete n[key]; return n; });
      }, 25000);
    } else {
      // paused or unknown → clear
      clearTimeout(typingTimers.current[key]);
      setTypingState(prev => { const n = { ...prev }; delete n[key]; return n; });
    }
  }, [chatPresence]);

  // Handle "IA respondendo" events — the AI is processing a reply for this chat,
  // so the operator sees a hint and avoids replying over it.
  useEffect(() => {
    if (!aiTyping) return;
    const { phone, active } = aiTyping;
    if (!phone) return;

    if (active) {
      setAiRespondingState(prev => ({ ...prev, [phone]: true }));
      // Defensive auto-clear in case the `active:false` event is missed (e.g. a
      // dropped connection mid-processing), so the hint never sticks forever.
      clearTimeout(aiTypingTimers.current[phone]);
      aiTypingTimers.current[phone] = setTimeout(() => {
        setAiRespondingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
      }, 60000);
    } else {
      clearTimeout(aiTypingTimers.current[phone]);
      setAiRespondingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
    }
  }, [aiTyping]);

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
          if (!isDuplicateMessage(message, buf)) {
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
        const dupIdx = prev.messages ? findDuplicateIndex(message, prev.messages) : -1;
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
      // An inbound (customer) message reopens the 24h free-text window — refresh
      // the compositor hint live so the operator isn't stuck on "fora da janela"
      // (WhatsApp Cloud) until a manual reload.
      if (message.role === 'user') {
        setContactData(prev => (prev && prev.session_open !== true)
          ? { ...prev, session_open: true } : prev);
      }
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
        const lastPreview = mediaPreviewLabel(message);
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
          advFilters=${advFilters}
          onAdvFiltersChange=${setAdvFilters}
          savedFilters=${savedFilters}
          activeFilter=${activeFilter}
          anyFilterActive=${anyFilterActive}
          onApplySavedFilter=${applySavedFilter}
          onSaveCurrentFilter=${saveCurrentFilter}
          onOverwriteSavedFilter=${overwriteSavedFilter}
          onRenameSavedFilter=${renameSavedFilter}
          onRemoveSavedFilter=${removeSavedFilter}
          onClearFilters=${clearAllFilters}
          channels=${channelOptions}
          agentsUsers=${agentsUsers}
          agentsAi=${agentsAi}
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
          onNewConversation=${() => setShowNewConversation(true)}
          checkingPhone=${checkingPhone}
          checkPhoneError=${checkPhoneError}
          wsConnected=${wsConnected}
          autoReply=${autoReply}
          onToggleAutoReply=${handleToggleAutoReply}
          selectionMode=${selectionMode}
          selectedKeys=${selectedKeys}
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
                channelId=${selectedChannelId}
                onBack=${() => selectContact(null)}
                messages=${messages}
                setContactData=${setContactData}
                info=${info}
                contact=${contactData}
                onAvatarClick=${() => selected && setOpenPanel('contact')}
                onOpenConversationInfo=${() => selected && setOpenPanel('conversation')}
                contactTyping=${selected && typingState[typingKey({ conversationId: selectedConvId, channelId: selectedChannelId, phone: selected })] || null}
                aiResponding=${selected && !!aiRespondingState[selected]}
                globalTags=${globalTags}
                groupParticipantsChanged=${groupParticipantsChanged}
                scrollToMsg=${scrollToMsg}
                onScrolledToMsg=${() => setScrollToMsg(null)}
              />`
          }
          ${openPanel === 'contact' && selected ? html`
            <${ContactInfoPanel}
              phone=${selected}
              info=${info}
              contactTags=${contactData && contactData.tags || []}
              globalTags=${globalTags}
              onGlobalTagsChange=${setGlobalTags}
              isGroup=${contactData && contactData.is_group}
              groupName=${contactData && contactData.group_name}
              avatarV=${contactData && contactData.avatar_v}
              onClose=${() => setOpenPanel(null)}
              onDeleteContact=${() => { handleDelete(selected); setOpenPanel(null); }}
              onSave=${(updatedInfo, updatedTags) => {
                setContactData(prev => prev ? { ...prev, info: updatedInfo, tags: updatedTags } : prev);
                setContacts(prev => prev.map(c =>
                  c.phone === selected ? { ...c, name: updatedInfo.name || c.name, tags: updatedTags } : c
                ));
                setOpenPanel(null);
              }}
            />
          ` : null}
          ${openPanel === 'conversation' && selected ? html`
            <${ConversationInfoPanel}
              phone=${selected}
              conversationId=${selectedConvId}
              onClose=${() => setOpenPanel(null)}
              onOpenContactInfo=${() => selected && setOpenPanel('contact')}
              contactInfo=${info}
              convAttrPatch=${convAttrPatch}
            />
          ` : null}
        </div>
      </div>
      ${channelPicker ? html`
        <${ChannelPickerModal}
          phoneDisplay=${channelPicker.phoneDisplay}
          channels=${channelPicker.channels}
          onPick=${handlePickChannel}
          onClose=${() => setChannelPicker(null)}
        />
      ` : null}
      ${showNewConversation ? html`
        <${NewConversationModal}
          contacts=${contacts}
          onClose=${() => setShowNewConversation(false)}
          onSent=${handleNewConversationSent}
        />
      ` : null}
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
              setOpenPanel('contact');
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
          onDeleteConversation=${handleDeleteConversation}
          onCreateTag=${handleCreateTag}
          onClose=${() => setCtxMenu(null)}
        />
      ` : null}
    </div>
  `;
}
