// @ts-check
//
// Conversation WebSocket-events hook (Plano 23 · D2) — extracted verbatim from
// Contacts.js. Centralizes every real-time effect that patches the sidebar +
// open thread: new_message routing/dedup, presence (typing/recording),
// "IA respondendo", contact info/tags/AI toggles, messages-read, delivery
// status, revoke/delete, avatar bumps, reactions, conversation lifecycle
// (assign/resolve/IA/delete/attribute writes via onConversationChanged), and the
// page-visibility read. It also owns the typing/aiResponding maps + their
// defensive auto-clear timers, `pageVisibleRef`-driven read gating, and the
// membership safety-net refetches (debounced).
//
// All routing rules are preserved exactly: a message belongs to the open thread
// by conversation_id when present, else by (phone, channel_id); optimistic copies
// reconcile by GOWA msg_id first, then by the R12 dedup heuristic.
//
// Cross-hook wiring: list + selection state/refs/setters and `setGlobalTags`
// (actions) are passed in; `pageVisibleRef` is owned by the container and shared
// with the selection hook's detail loader so reads gate on the same visibility.
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import { markAsRead } from '../../../services/api.js';
import { isDuplicateMessage, findDuplicateIndex, mediaPreviewLabel } from '../../../services/messages.js';
import { applyConversationEvent, eventTargetsRow, isConversationAttributeWrite } from '../../../services/conversationPatch.js';
import { typingKey } from '../ContactList.js';
import { useWebSocket } from '../../../hooks/useWebSocket.js';

/**
 * @param {Object} opts - WS event props + cross-hook state/refs/setters.
 */
export function useConversationWsEvents(opts) {
  const {
    // WS event props
    newMessage, chatPresence, aiTyping, contactInfoUpdated, tagsChanged,
    contactTagsUpdated, contactAiToggled, messagesRead, messageStatus,
    messageAction, messageReaction, avatarUpdated, conversationCreated,
    // list
    setContacts, contactsRef, fetchContacts, fetchContactsRef, searchRef, search, sortContacts,
    // selection
    setContactData, setSelected, setSelectedConvId,
    selectedRef, selectedConvIdRef, selectedChannelIdRef,
    pendingWsMessages, isOpenRow, selected, contactData,
    // actions
    setGlobalTags,
    // container-shared ref (page visibility gates read + unread bumps)
    pageVisibleRef,
  } = opts;

  const [typingState, setTypingState] = useState({});  // { 'channel::phone'|'conv:id': 'text'|'audio' }
  const [aiRespondingState, setAiRespondingState] = useState({});  // { 'channel::phone': true } — IA processando
  const [convAttrPatch, setConvAttrPatch] = useState(null);

  const typingTimers = useRef({});
  const aiTypingTimers = useRef({});
  const convListRefetchTimer = useRef(null);   // debounce for membership-change refetch
  const listRefetchTimer = useRef(null);       // debounce/coalesce for new-conversation refetch
  const wsConnectedOnceRef = useRef(false);    // skip the first WS connect (initial fetch covers it)

  // Coalesce every "a conversation not in the list just changed" trigger
  // (new_message for an unknown row, conversation_created, WS reconnect) into ONE
  // ref-based refetch shortly after — avoids the stale-closure/side-effect-in-reducer
  // fetch and the double-fetch race when two events fire together. Uses the stable
  // refs so a []-dep callback never reads a stale search/handle.
  const scheduleListRefetch = useCallback(() => {
    if (listRefetchTimer.current) clearTimeout(listRefetchTimer.current);
    listRefetchTimer.current = setTimeout(() => {
      if (fetchContactsRef.current) fetchContactsRef.current(searchRef.current);
    }, 250);
  }, []);

  // Resync after a dropped WS connection: events that arrived during the gap are
  // lost (the bus has no replay), so on RE-connect refetch the list to catch any
  // conversation that changed while we were offline. Skip the first connect — the
  // initial fetch already covers it.
  const onWsConnect = useCallback(() => {
    if (!wsConnectedOnceRef.current) { wsConnectedOnceRef.current = true; return; }
    scheduleListRefetch();
  }, [scheduleListRefetch]);

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

  // Real-time: patch a contact row when its conversation changes (assign /
  // resolve / IA). The conversation_* events carry contact_id (plano 10) and
  // conversation_id (plano 11). Conversa-cêntrico: um contato pode ter várias
  // linhas (uma por canal); casar só por contact_id resolveria/atribuiria TODAS
  // as conversas do número. Linhas COM conversa casam por conversation_id;
  // linhas sem conversa (legado) ainda casam por contact_id e adotam a nova.
  // P19: latest conversation-scope custom-attribute write, forwarded to the open
  // ConversationInfoPanel for live refresh (mirrors contact_info_updated → panel).
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
  useWebSocket({ onConversationChanged, onWsConnect });

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
    // Conversa-cêntrico: a IA responde numa conversa de UM canal. O evento traz
    // {phone, channel_id} (sem conversation_id), então casamos por canal::telefone —
    // a mesma chave que a sidebar usa por linha. Assim só a conversa do canal que
    // está processando acende "IA respondendo" (o header e a linha da sidebar).
    const key = typingKey({ channelId: aiTyping.channel_id, phone });

    if (active) {
      setAiRespondingState(prev => ({ ...prev, [key]: true }));
      // Defensive auto-clear in case the `active:false` event is missed (e.g. a
      // dropped connection mid-processing), so the hint never sticks forever.
      clearTimeout(aiTypingTimers.current[key]);
      aiTypingTimers.current[key] = setTimeout(() => {
        setAiRespondingState(prev => { const n = { ...prev }; delete n[key]; return n; });
      }, 60000);
    } else {
      clearTimeout(aiTypingTimers.current[key]);
      setAiRespondingState(prev => { const n = { ...prev }; delete n[key]; return n; });
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
    scheduleListRefetch();
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
      // No matching row — likely a brand-new (or deleted-then-recreated) conversation.
      // Coalesced ref-based refetch materialises it reliably (debounced so it merges
      // with the sibling `conversation_created` trigger into a single, post-commit
      // fetch — no stale-closure fetch inside the reducer, no double-fetch race).
      scheduleListRefetch();
      return prev;
    });
  }, [newMessage]);

  return { typingState, aiRespondingState, convAttrPatch };
}
