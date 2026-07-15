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
import { notify } from '../../../services/notify.js';
import { showBrowserNotification, playNotificationSound, getNotifPref } from '../../../utils/notifications.js';
import { playTransferAlert } from '../../../utils/alertSound.js';
import { optimisticDupIndex } from '../../../services/messages.js';
import { applyConversationEvent, eventTargetsRow, isConversationAttributeWrite } from '../../../services/conversationPatch.js';
import { upsertConversationRow, convRowToSidebarRow } from '../../../services/conversationRows.js';
import { typingKey } from '../ContactList.js';
import { useWebSocket } from '../../../hooks/useWebSocket.js';

// Papéis painel-only (cards que nunca vão ao WhatsApp — ver CLAUDE.md). Só
// eles ganham o fallback (phone, channel) quando o conversation_id do payload
// diverge da thread aberta (plano 30 F1b).
const PANEL_CARD_ROLES = new Set([
  'tool_call', 'system_notice', 'transcription', 'private_note', 'error',
  'conversation_event', 'system',
]);

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
    // selection: background re-fetch of the OPEN thread (plano 33 F2)
    reloadOpenThread,
    // usuário logado — filtra `mention_created` (colaboração estilo Chatwoot)
    currentUserId,
  } = opts;

  const [typingState, setTypingState] = useState({});  // { 'channel::phone'|'conv:id': 'text'|'audio' }
  const [aiRespondingState, setAiRespondingState] = useState({});  // { 'channel::phone': true } — IA processando
  const [convAttrPatch, setConvAttrPatch] = useState(null);

  const typingTimers = useRef({});
  const aiTypingTimers = useRef({});
  const convListRefetchTimer = useRef(null);   // debounce for membership-change refetch
  const listRefetchTimer = useRef(null);       // debounce/coalesce for new-conversation refetch
  const openReadSyncTimer = useRef(null);      // debounce: sync backend read of the OPEN conversa (nota privada)
  const wsConnectedOnceRef = useRef(false);    // skip the first WS connect (initial fetch covers it)
  const reloadOpenThreadRef = useRef(null);    // plano 33 F2 — freshest thread reloader for the []-dep onWsConnect
  reloadOpenThreadRef.current = reloadOpenThread;
  const currentUserIdRef = useRef(null);       // freshest logged-in user id for the []-dep mention handler
  currentUserIdRef.current = currentUserId;

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
  //
  // Plano 33 F2 (LINCHPIN): the list refetch above ONLY heals the sidebar. The
  // open THREAD also lost any `new_message` that arrived during the gap (the
  // append is live-only, no replay), so it stayed stale until re-selection/F5.
  // Reload the open thread too — best-effort, background, dedup-idempotent.
  const onWsConnect = useCallback(() => {
    if (!wsConnectedOnceRef.current) { wsConnectedOnceRef.current = true; return; }
    scheduleListRefetch();
    if (reloadOpenThreadRef.current) reloadOpenThreadRef.current();
  }, [scheduleListRefetch]);

  // Track page visibility — mark selected contact as read when tab becomes visible
  useEffect(() => {
    const handler = () => {
      const visible = !document.hidden;
      pageVisibleRef.current = visible;
      if (visible && selectedRef.current) {
        markAsRead(selectedRef.current);
        setContacts(prev => prev.map(c =>
          isOpenRow(c) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false, has_user_mention: false } : c
        ));
      }
    };
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, []);

  // Real-time: patch a contact row when its conversation changes (assign /
  // resolve / IA). The conversation_* events carry contact_id (plano 10) and
  // conversation_id (plano 11). Atendimento-cêntrico: um contato pode ter várias
  // linhas (uma por canal); casar só por contact_id resolveria/atribuiria TODAS
  // os atendimentos do número. Linhas COM atendimento casam por conversation_id;
  // linhas sem atendimento (legado) ainda casam por contact_id e adotam a nova.
  // P19: latest conversation-scope custom-attribute write, forwarded to the open
  // ConversationInfoPanel for live refresh (mirrors contact_info_updated → panel).
  const onConversationChanged = useCallback((name, data) => {
    // plano 28: the AUTHORITATIVE list-row event. Carries the whole enriched row;
    // we upsert it by conversation_id (insert brand-new, or scoped-merge the
    // message/preview/unread of an existing row) — no refetch, no stale-read race.
    // Its `id` is the conversation id, so the generic cid/convId guard below (which
    // reads data.conversation_id) does not apply; handle it first and return.
    if (name === 'conversation_upsert') {
      if (data == null || data.id == null) return;
      const row = convRowToSidebarRow(data);
      // Se a conversa está ABERTA e a aba visível, o operador já está vendo tudo →
      // considerar lida: zera o badge LOCALMENTE (sem flash verde de nota privada
      // própria). NÃO chama markAsRead por-evento: durante um envio em rajada isso
      // apagava as linhas de não-lida no meio da rajada e cada `conversation_upsert`
      // emitia unread_count=1 → o badge de uma conversa NÃO aberta ficava travado em
      // "1" em vez da contagem real. Em vez disso, agenda UM markAsRead (debounce) que
      // só dispara se eu ainda estiver na conversa — mantém a contagem correta para
      // conversas não abertas e zera a aberta sem corromper nada.
      if (pageVisibleRef.current && isOpenRow(row)
          && ((row.unread_count || 0) > 0 || (row.unread_ai_count || 0) > 0)) {
        row.unread_count = 0;
        row.unread_ai_count = 0;
        const _phone = row.phone;
        const _convId = row.conversation_id;
        if (_phone) {
          if (openReadSyncTimer.current) clearTimeout(openReadSyncTimer.current);
          openReadSyncTimer.current = setTimeout(() => {
            // Só sincroniza se AINDA estou vendo esta mesma conversa e a aba visível.
            if (pageVisibleRef.current
                && (selectedConvIdRef.current === _convId
                    || (_convId == null && selectedRef.current === _phone))) {
              markAsRead(_phone);
            }
          }, 500);
        }
      }
      setContacts(prev => upsertConversationRow(prev, row));
      return;
    }
    // Menção INTERNA numa nota privada (colaboração estilo Chatwoot). Só reage se EU
    // fui mencionado: acende o badge "@" na linha + toast + (opcional) som/desktop.
    // A conversa atualmente ABERTA já vai ler a menção ao carregar — não incomoda.
    if (name === 'mention_created') {
      const uid = currentUserIdRef.current;
      const targets = (data && data.mentioned_user_ids) || [];
      if (uid == null || !targets.includes(uid)) return;
      const convId = data.conversation_id;
      if (convId != null && selectedConvIdRef.current === convId) return;  // já estou nela
      setContacts(prev => prev.map(c =>
        c.conversation_id === convId ? { ...c, has_user_mention: true } : c));
      const who = data.actor_name ? `${data.actor_name} mencionou você` : 'Você foi mencionado';
      const body = data.preview ? `${who}: ${data.preview}` : who;
      notify(body, { kind: 'info' });
      if (getNotifPref('browser')) showBrowserNotification('WhatsBot — menção', body);
      if (getNotifPref('sound')) playNotificationSound();
      return;
    }
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
  // Alerta sonoro de transferência ENTRE atendentes — mesma sirene do IA→humano,
  // mas direcionada: o backend só emite `agent_transfer_alert` numa reatribuição
  // PARA OUTRO humano (nunca em "assumir para mim"), e aqui filtramos por usuário
  // logado, então só o atendente que recebeu a conversa ouve o som. `enabled`/
  // `duration` vêm resolvidos do backend (config global) no próprio payload.
  const onAgentTransferAlert = useCallback((data) => {
    const uid = currentUserIdRef.current;
    if (uid == null || !data || data.assignee_user_id !== uid) return;
    if (data.enabled === false) return;
    playTransferAlert(data.duration || 5);
  }, []);
  useWebSocket({ onConversationChanged, onAgentTransferAlert, onWsConnect });

  // Handle chat presence events (typing/recording indicators). Atendimento-cêntrico:
  // a presença pertence a Um atendimento (o canal GOWA que reportou). Casamos por
  // conversation_id quando o evento o traz (inequívoco), senão por canal::telefone.
  // Assim só o atendimento do canal que emitiu mostra "digitando" — mesmo fechada.
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

  // plano 28: `conversation_created` no longer drives a list refetch. The brand-new
  // conversation's row is INSERTED by the authoritative `conversation_upsert` emitted
  // at t=0 by `ensure_conversation_live` (fully formed: name/channel/status, gated
  // visible by origin='inbound'), so no stale-read refetch is needed. The event is
  // still consumed elsewhere (kanban/plugins); it is simply a no-op for this list.

  // Handle real-time messages from WebSocket. Atendimento-cêntrico routing: a message
  // belongs to the OPEN thread by conversation_id when present (operator/save
  // payloads), else by (phone, channel_id) — (phone, channel) uniquely identifies
  // a conversation, so the two channels of one number never cross-contaminate.
  useEffect(() => {
    if (!newMessage) return;
    const { phone, message } = newMessage;
    const msgConvId = message.conversation_id;
    const msgChannel = newMessage.channel_id || message.channel_id || 'default';

    // Canal EXPLÍCITO do payload (sem o default 'default' de msgChannel) — o
    // fallback painel-only abaixo exige canal declarado pra não casar um
    // payload sem channel_id (ex.: system notice de outro canal) com a thread
    // aberta do canal 'default' por acidente.
    const explicitChannel = newMessage.channel_id || message.channel_id || null;
    let belongsToOpen;
    if (selectedConvIdRef.current != null) {
      if (msgConvId != null && msgConvId === selectedConvIdRef.current) {
        belongsToOpen = true;
      } else if (msgConvId == null) {
        // Sem conversation_id no payload — comportamento clássico (phone, channel).
        belongsToOpen = (phone === selectedRef.current
          && msgChannel === selectedChannelIdRef.current);
      } else {
        // conversation_id DIVERGENTE da thread aberta. Rede de segurança
        // (defesa em profundidade do plano 30 F1b) SÓ para cards painel-only
        // com canal explícito: a classe de bug era o card tool_call sair com
        // id mal resolvido. Mensagem REAL com id divergente pertence a OUTRA
        // conversa — aceitar vazaria conteúdo entre threads/canais e
        // dispararia markAsRead da conversa errada.
        belongsToOpen = PANEL_CARD_ROLES.has(message.role)
          && explicitChannel != null
          && phone === selectedRef.current
          && explicitChannel === selectedChannelIdRef.current;
      }
    } else {
      // Legacy contact-only open thread (no conversation) — route by phone.
      belongsToOpen = (phone === selectedRef.current);
    }

    if (belongsToOpen) {
      setContactData(prev => {
        if (!prev) {
          // Detail still loading — buffer under the open thread's phone key
          const buf = pendingWsMessages.current[phone] || [];
          if (optimisticDupIndex(message, buf) === -1) {
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
        // Collapse only an OPTIMISTIC bubble (no msg_id) into its server echo —
        // NOT two distinct inbound rows of identical content (plano 33 F4).
        const dupIdx = optimisticDupIndex(message, prev.messages);
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

    // plano 28: `new_message` is APPEND-ONLY for the open thread now. The sidebar
    // list (new-row insert AND existing-row preview/unread) is driven exclusively by
    // the authoritative `conversation_upsert` event (emitted post-commit from
    // `add_message`, carrying the real preview/unread from the DB). This removes the
    // notification-then-stale-refetch race (a brand-new conversation used to be
    // fetched with last_message_ts=0 and hidden) AND the double-count that keeping an
    // in-place unread increment alongside the DB-truth upsert would cause.
  }, [newMessage]);

  return { typingState, aiRespondingState, convAttrPatch };
}
