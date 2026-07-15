// @ts-check
//
// Conversation-selection hook (Plano 23 · D2) — extracted verbatim from
// Contacts.js. Owns which thread is open (phone + conversation_id + channel_id,
// plus their refs for stale-closure-free WS routing), the loaded `contactData`
// (full contact + messages), the open side-drawer (`openPanel`), the
// search-hit scroll target, and the deep-link resolution.
//
// It emits the D1 client event `ui.conversation.selected` on every select /
// deselect (stable, minimal payload; fire-and-forget — emit() isolates throwing
// subscribers). The detail loader reuses the R12 dedup (`isDuplicateMessage`)
// for the pre-fetch / during-fetch buffer merge.
//
// Cross-hook wiring: `pageVisibleRef` (visibility) and `newConvChannelRef`
// (channel picker) are owned by the container and passed in; `setContacts` /
// `contactsRef` / `loading` come from the list hook; `isOpenRow` is derived here
// from the selection refs and consumed by the list/WS optimistic patches.
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import { getContact, getConversationMessages } from '../../../services/api.js';
import { isDuplicateMessage } from '../../../services/messages.js';
import { shapeConvData } from '../../../services/conversationRows.js';
import { emit as emitClientEvent } from '../../../plugins/registry.js';

/**
 * @param {Object} opts
 * @param {Record<string, any>[]} opts.contacts
 * @param {boolean} opts.loading
 * @param {(fn:any)=>void} opts.setContacts
 * @param {{ current: Record<string, any>[] }} opts.contactsRef
 * @param {{ current: boolean }} opts.pageVisibleRef
 * @param {{ current: string|null }} opts.newConvChannelRef
 * @param {number|null} [opts.initialContactId]
 * @param {number|null} [opts.initialConversationId]
 * @param {number|null} [opts.initialScrollMsgId]
 */
export function useConversationSelection({
  contacts, loading, setContacts, contactsRef,
  pageVisibleRef, newConvChannelRef,
  initialContactId, initialConversationId, initialScrollMsgId = null,
}) {
  const [selected, setSelected] = useState(null);          // open thread's phone (contact-level ops)
  const [selectedConvId, setSelectedConvId] = useState(null);   // open thread's conversation id
  const [selectedChannelId, setSelectedChannelId] = useState('default');  // open thread's channel
  const [scrollToMsg, setScrollToMsg] = useState(null);  // DB id of a message to focus on open (search hit)
  const [contactData, setContactData] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);  // plano 50 F4: scroll-up
  // Which side drawer is open: 'contact' (foto/nome) | 'conversation' (botão ℹ️) |
  // null. Single state so opening one closes the other (no overlapping drawers).
  const [openPanel, setOpenPanel] = useState(null);

  const hasLoadedDetail = useRef(false);
  const openInfoAfterSelect = useRef(false);
  const pendingWsMessages = useRef({});
  const selectedRef = useRef(null);
  const selectedConvIdRef = useRef(null);
  const selectedChannelIdRef = useRef('default');
  const lastResolvedId = useRef(null);
  const lastResolvedConvId = useRef(null);
  const contactDataRef = useRef(null);         // plano 50 F4: cursor sem stale-closure
  const loadingOlderRef = useRef(false);        // guarda contra loadOlder concorrente

  // Keep refs in sync — avoids stale closures
  useEffect(() => { contactDataRef.current = contactData; }, [contactData]);
  useEffect(() => { selectedRef.current = selected; }, [selected]);
  useEffect(() => { selectedConvIdRef.current = selectedConvId; }, [selectedConvId]);
  useEffect(() => { selectedChannelIdRef.current = selectedChannelId; }, [selectedChannelId]);

  // True when `row` is the currently-open thread (by conversation when available,
  // else by phone for legacy contact-only rows). Reads refs → safe in WS closures.
  const isOpenRow = useCallback((row) => {
    if (selectedConvIdRef.current != null) return row.conversation_id === selectedConvIdRef.current;
    return row.conversation_id == null && row.phone === selectedRef.current;
  }, []);

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
      // Sem atendimento ainda: a seleção fica no estado; /contacts/{id} agora é a tela
      // de detalhe do contato (não o hub), então a URL volta pra raiz do hub.
      history.pushState(null, '', '/');
    }
  }, [contactsRef]);

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
        // Atendimento fora da sidebar (ex: além do limite ou arquivada) — abre direto
        // por id; o load deriva o telefone/canal da resposta do endpoint.
        setSelected(null);
        setSelectedConvId(initialConversationId);
      }
      // Permalink (?message=<_id>): foca a mensagem assim que o atendimento renderiza.
      // Lido no momento da resolução e consumido uma vez (onScrolledToMsg limpa);
      // o atendimento carrega TODAS as mensagens, então o alvo está sempre no DOM.
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

  // Load chat detail when the open thread changes. Atendimento-cêntrico: prefer the
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
        isOpenRow(c) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false, has_user_mention: false } : c
      ));
    }
    const convId = selectedConvId;
    // Atendimento novo sem conversation_id ainda: escopa o getContact ao canal
    // escolhido no picker (lido-e-zerado aqui), para não fundir os canais nem
    // abrir o atendimento de outro canal do mesmo número (multicanal).
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

  // Plano 33 F2 — re-fetch the OPEN thread after a WS RECONNECT. The bus has no
  // replay, so a `new_message` that arrived during a connection gap (sleep / NAT
  // blip / half-open socket) is lost from the open thread — only the sidebar is
  // separately refetched. This ref-based, BACKGROUND reload recovers it. Reuses
  // the SAME per-conversation/per-contact loader and the R12 dedup, so it is
  // idempotent with the optimistic append (nothing is duplicated) and a failed
  // fetch leaves the current thread intact. Unlike the selection-time effect it
  // deliberately does NOT flip the loading spinner, reset the open panel, or clear
  // unread badges — a reconnect must not flash or reshuffle the open thread. Reads
  // refs (not deps) so it is stable across renders and safe in a WS closure.
  const reloadOpenThread = useCallback(() => {
    const sel = selectedRef.current;
    const convId = selectedConvIdRef.current;
    if (!sel && convId == null) return;
    const bufKey = sel || (convId != null ? `conv:${convId}` : '');
    const preFetchBuffer = pendingWsMessages.current[bufKey] || [];
    pendingWsMessages.current[bufKey] = [];
    const isPageVisible = pageVisibleRef.current;
    const loader = convId != null
      ? getConversationMessages(convId, isPageVisible).then(res =>
          res.ok ? { ok: true, data: shapeConvData(res.data) } : res)
      : getContact(sel, isPageVisible, null);
    loader.then(res => {
      if (!res.ok) return;
      const data = res.data;
      if (data.channel_id) setSelectedChannelId(data.channel_id);
      const duringFetch = pendingWsMessages.current[bufKey] || [];
      const pending = [...preFetchBuffer, ...duringFetch];
      if (pending.length > 0) {
        const existing = data.messages || [];
        const newMsgs = pending.filter(m => !isDuplicateMessage(m, existing));
        if (newMsgs.length > 0) data.messages = [...(data.messages || []), ...newMsgs];
      }
      data.messages = (data.messages || []).map(m =>
        m.status === 'failed' ? { ...m, _localId: `loaded_${m.ts}`, _status: 'failed' } : m);
      pendingWsMessages.current[bufKey] = [];
      setContactData(data);
    });
  }, []);

  // Plano 50 F4 — carregar mensagens ANTERIORES (scroll-up / keyset). Busca a página
  // anterior (before_id = menor _id já carregado) SEM re-marcar como lida e a PREPENDA
  // ao histórico (dedup por _id, como o merge de WS). Guardado por ref contra chamadas
  // concorrentes. O caller (ContactDetail) ancora o scroll para a viewport não saltar.
  const loadOlder = useCallback(() => {
    const data = contactDataRef.current;
    if (!data || !data.has_more || loadingOlderRef.current) return;
    const msgs = data.messages || [];
    const ids = msgs.map(m => m._id).filter(v => v != null);
    if (ids.length === 0) return;
    const beforeId = Math.min(...ids);
    const sel = selectedRef.current;
    const convId = selectedConvIdRef.current;
    if (!sel && convId == null) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    const loader = convId != null
      ? getConversationMessages(convId, false, { beforeId }).then(res =>
          res.ok ? { ok: true, data: shapeConvData(res.data) } : res)
      : getContact(sel, false, null, { beforeId });
    loader.then(res => {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
      if (!res.ok) return;
      let older = res.data.messages || [];
      const newHasMore = !!res.data.has_more;
      older = older.map(m =>
        m.status === 'failed' ? { ...m, _localId: `loaded_${m.ts}`, _status: 'failed' } : m);
      setContactData(prev => {
        if (!prev) return prev;
        const existing = prev.messages || [];
        const existingIds = new Set(existing.map(m => m._id).filter(v => v != null));
        const fresh = older.filter(m => m._id == null || !existingIds.has(m._id));
        return { ...prev, messages: [...fresh, ...existing], has_more: newHasMore };
      });
    });
  }, []);

  return {
    selected, setSelected,
    selectedConvId, setSelectedConvId,
    selectedChannelId, setSelectedChannelId,
    scrollToMsg, setScrollToMsg,
    contactData, setContactData,
    loadingDetail,
    loadingOlder, loadOlder,
    openPanel, setOpenPanel,
    selectedRef, selectedConvIdRef, selectedChannelIdRef,
    openInfoAfterSelect, pendingWsMessages,
    isOpenRow, selectContact, reloadOpenThread,
  };
}
