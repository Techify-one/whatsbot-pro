import { h } from 'preact';
import { useEffect, useRef, useState, useCallback } from 'preact/hooks';
import htm from 'htm';
import { sendMessage, sendImage, sendAudio, sendDocument, sendVideo } from '../../services/api.js';
import { BackArrowIcon, DefaultAvatar, GroupAvatar, InfoIcon } from './icons.js';
import { isSameDay, formatDateSeparator, avatarUrl } from './utils.js';
import { formatWhatsApp } from '../../utils/formatWhatsApp.js';
import { MessageContextMenu, CopyIcon, TrashIcon, ReplyIcon, LinkIcon, EditIcon } from './MessageContextMenu.js';
import { ConversationHeaderActions } from './ConversationHeaderActions.js';
import { TemplatePicker } from './TemplatePicker.js';
import { Slot } from '../../plugins/Slot.js';
// Selo do canal — MESMO componente da linha da barra lateral.
import { ChannelChip } from './ChannelChip.js';
import { emit as emitClientEvent, applyFilter, getFilters } from '../../plugins/registry.js';
import { MessageBubble } from './MessageBubble.js';
import { MessageEditDialog } from './MessageEditDialog.js';
import { SystemMessageCard, isSystemCardRole } from './SystemMessageCard.js';
import { Composer } from './Composer.js';
import { useReverseInfiniteScroll } from '../../hooks/useInfiniteScroll.js';
import { useComposer } from './hooks/useComposer.js';
import { useAudioRecorder } from './hooks/useAudioRecorder.js';
import { useMediaUpload } from './hooks/useMediaUpload.js';
import { useDropZone } from './hooks/useDropZone.js';
import { DropOverlay } from './DropOverlay.js';
import { useTokenAutocomplete } from './hooks/useTokenAutocomplete.js';
import { useMessageActions, myReaction, selectionKey } from './hooks/useMessageActions.js';
import { useContactSubtitle } from './hooks/useContactSubtitle.js';
import { stripGroupPrefix } from '../../services/composerTokens.js';
import { senderColor, quotedMediaText, cardStateKey, isCollapsibleRole } from '../../services/messageView.js';
import { hasPermission } from '../../utils/permissions.js';

const html = htm.bind(h);

// Quick-reaction emojis shown in the message context menu bar (WhatsApp-style).
const QUICK_REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🙏'];

// Ícone do item "Selecionar mensagens" (plano 51 · 04 F1).
const SelectManyIcon = () => html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M3 5h2V3c-1.1 0-2 .9-2 2zm0 8h2v-2H3v2zm4 8h2v-2H7v2zM3 9h2V7H3v2zm10-6h-2v2h2V3zm6 0v2h2c0-1.1-.9-2-2-2zM5 21v-2H3c0 1.1.9 2 2 2zm-2-4h2v-2H3v2zM9 3H7v2h2V3zm2 18h2v-2h-2v2zm8-8h2v-2h-2v2zm0 8c1.1 0 2-.9 2-2h-2v2zm0-12h2V7h-2v2zm0 8h2v-2h-2v2zm-4 4h2v-2h-2v2zm0-16h2V3h-2v2zM7 17h10V7H7v10zm2-8h6v6H9V9z"/>
  </svg>`;

// ── Contact Detail (WhatsApp Web chat panel) ─────────────────────
//
// Thin container (Plano 23 · D3): composes the message-list render + the
// composer / token-autocomplete / media-upload / audio-recorder / message-action
// hooks, plus the presentational components (MessageBubble, SystemMessageCard,
// MediaContent, Composer). The chat header, scroll/pagination effects, the
// reply-quote lookup and the dialogs (delete / improve / template / context menu)
// stay here; everything composer-related lives in the hooks/components.

export function ContactDetail({ phone, conversationId = null, channelId = null, onBack, messages, info, contact,
  channelProvider = null, channelName = null, showChannel = false, onAvatarClick, onOpenConversationInfo = null, currentUser = null, contactTyping, aiResponding = false, setContactData, globalTags, groupParticipantsChanged = null, sandbox = false, api = null, scrollToMsg = null, onScrolledToMsg = null, showAgentName = true, loadOlder = null, loadingOlder = false, hasMore = false, droppedFiles = null, onDroppedFilesConsumed = null }) {
  // P48 hides (sandbox is always allowed — no RBAC identity there).
  const canReadContact = sandbox || hasPermission(currentUser, 'contact.read');
  const canReadConv = sandbox || hasPermission(currentUser, 'conversation.read');
  const canReply = sandbox || hasPermission(currentUser, 'conversation.reply');
  // Effective send API. Sandbox injects local (no-GOWA) endpoints; the contact
  // chat uses the real ones.
  const _api = {
    sendText: sendMessage, sendImage, sendAudio, sendDocument, sendVideo,
    ...(api || {}),
  };

  const chatRef = useRef(null);
  // Scroll-up (plano 50 F4): sentinela no topo + âncora de scroll, centralizados no hook
  // reutilizável `useReverseInfiniteScroll`. Ele liga o IntersectionObserver ao topo,
  // captura a âncora e restaura a posição visual após o prepend (a viewport não salta),
  // e só dispara quando há o que rolar (evita o auto-load em cascata que empurrava as
  // mensagens novas pra fora da viewport — o bug "as anteriores não aparecem").
  // `justPrependedRef` sinaliza que a atualização de `messages` veio de "carregar
  // anteriores" → o efeito de scroll NÃO rola pro fim nessa atualização.
  const { sentinelRef: topSentinelRef, justPrependedRef } = useReverseInfiniteScroll({
    scrollRef: chatRef, items: messages, hasMore, loadOlder, loadingOlder,
  });

  // Header subtitle (line under the name): raw phone by default, but a plugin may
  // rewrite it via `filter.contact.headerSubtitle` — e.g. the website widget maps
  // the opaque session token (`wsess_…`) to a short visitor code (WEB-XXXXXX).
  const headerSubtitle = useContactSubtitle(phone, { channelId, contact, info });
  // Template picker modal (Cloud API 24h window). Owned by the container.
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const openTemplatePicker = () => setShowTemplatePicker(true);

  const isGroup = contact && contact.is_group;
  const canSend = contact ? (contact.can_send !== false) : true;
  const rawName = info && info.name;
  const isAutoName = !isGroup && rawName && rawName.startsWith('~');
  const displayName = isGroup ? (contact.group_name || phone) : (rawName ? rawName.replace(/^~/, '') : phone);
  // Template support (Frente C): capability flag from the conversation payload — ou,
  // ao iniciar um atendimento Novo pela caixa de entrada escolhida (plano 21), do
  // payload channel-scoped do getContact (ainda sem conversationId). O TemplatePicker
  // opera em "channel mode" (channelId + phone) quando não há atendimento.
  // sessionClosed → a janela de texto livre de 24h expirou (ou nunca abriu, no caso de
  // um número novo no Cloud), então só um template pode sair.
  const templatesSupported = !sandbox && !!(contact && contact.templates_supported);
  const sessionClosed = templatesSupported && contact && contact.session_open === false;
  // Message context-menu capability gates (plano — editar/apagar mensagem):
  //  • revokeSupported: default TRUE — só esconde "Apagar" quando o canal declara
  //    explicitamente que NÃO revoga (WhatsApp Cloud). GOWA e a visão legada
  //    all-channels (sem flag) continuam mostrando Apagar.
  //  • editSupported: só mostra "Editar" quando o canal edita e não é sandbox.
  const revokeSupported = sandbox || !(contact && contact.revoke_supported === false);
  const editSupported = !sandbox && !!(contact && contact.edit_supported);
  // Limites de mídia declarados pelo canal (tamanho/formato). O compositor usa
  // pra bloquear o anexo incompatível com um popup ANTES de tentar enviar. Canal
  // sem limites (GOWA/Telegram) manda `{}` e nada é bloqueado.
  const mediaLimits = (!sandbox && contact && contact.media_limits) || null;

  // ── Hooks ──────────────────────────────────────────────────────
  // Message actions own `updateMsgByLocalId` (shared by composer + media).
  const actions = useMessageActions({ phone, conversationId, setContactData });
  const { updateMsgByLocalId } = actions;

  // The composer needs the autocomplete's `updateMenus`/`closeMentionMenu`, and
  // the autocomplete needs the composer's `input`/`setInput`/`inputRef`. Break
  // the order cycle with a ref the composer's event handlers read at call time.
  const autocompleteRef = useRef(null);

  const composer = useComposer({
    api: _api, phone, conversationId, channelId, sandbox, sessionClosed, currentUser,
    setContactData, updateMsgByLocalId,
    updateMenus: (el, val) => autocompleteRef.current && autocompleteRef.current.updateMenus(el, val),
    closeMentionMenu: () => autocompleteRef.current && autocompleteRef.current.setMentionMenu(null),
    collectMentions: (text) => autocompleteRef.current
      ? autocompleteRef.current.collectMentions(text) : { mentions: [], mention_inbox: false },
    resetMentions: () => autocompleteRef.current && autocompleteRef.current.resetMentions(),
    openTemplatePicker,
  });

  const autocomplete = useTokenAutocomplete({
    phone, sandbox, contact, groupParticipantsChanged, mode: composer.mode,
    input: composer.input, setInput: composer.setInput, inputRef: composer.inputRef,
  });
  autocompleteRef.current = autocomplete;

  const media = useMediaUpload({
    api: _api, phone, conversationId, channelId, sandbox, sessionClosed, currentUser,
    mode: composer.mode, aiReadPrivate: composer.aiReadPrivate,
    aiReplyInChat: composer.aiReadPrivate ? composer.aiReplyInChat : true,
    setContactData, updateMsgByLocalId, openTemplatePicker, mediaLimits,
  });

  const audio = useAudioRecorder({
    onRecorded: (item) => media.setPendingAudio(item),
  });

  // Arrastar arquivos para dentro da conversa (plano 64 · F6). A zona é a raiz
  // do painel — o limite exato da conversa. Desligada quando o operador não
  // pode responder, quando a janela de 24h está fechada (só template resolve)
  // ou enquanto um lote está em voo.
  const drop = useDropZone({
    disabled: !canReply || media.sending || (sessionClosed && composer.mode !== 'private'),
    onFiles: (files, sendMode) => media.requestFilesDrop(files, sendMode),
  });

  // Arquivos soltos numa linha da sidebar (plano 64 · F11): o `Contacts` já
  // trocou para esta conversa e nos entrega o lote. Só consome quando o telefone
  // bate — evita despejar na conversa errada se o painel ainda não trocou.
  const consumedDropRef = useRef(0);
  useEffect(() => {
    if (!droppedFiles || droppedFiles.token === consumedDropRef.current) return;
    if (droppedFiles.phone !== phone) return;
    consumedDropRef.current = droppedFiles.token;
    if (canReply) media.requestFilesDrop(droppedFiles.files, 'media');
    if (onDroppedFilesConsumed) onDroppedFilesConsumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [droppedFiles, phone, canReply]);

  // ── Seleção em lote (plano 51 · 04 F1) ─────────────────────────
  // As mensagens completas resolvidas do Set de chaves + os itens de ação vindos
  // do seam `filter.selection.batchActions` (async → resolvidos num efeito e
  // guardados em estado; a barra só existe em selectionMode). Trocar de conversa
  // sai do modo de seleção.
  const [batchItems, setBatchItems] = useState([]);
  const selectedMessages = actions.selectionMode
    ? (messages || []).filter(m => actions.selection.has(selectionKey(m)))
    : [];
  useEffect(() => { actions.clearSelection(); }, [phone, conversationId]);
  useEffect(() => {
    if (!actions.selectionMode) { setBatchItems([]); return; }
    let alive = true;
    (async () => {
      let items = [];
      try {
        const out = await applyFilter('filter.selection.batchActions', [], {
          messages: selectedMessages, phone, conversationId, sandbox,
          clearSelection: actions.clearSelection,
        });
        if (Array.isArray(out)) items = out;
      } catch (_) { /* barra fica sem ações de plugin */ }
      if (alive) setBatchItems(items);
    })();
    return () => { alive = false; };
  }, [actions.selectionMode, actions.selection, messages]);

  // ── Scroll / search-hit jump ───────────────────────────────────
  // Remember a message to focus (e.g. opened from a search hit) until it renders,
  // so the messages-driven scroll below jumps to it instead of to the bottom.
  const pendingScrollRef = useRef(null);
  // Plano 63 F5: a deep-linked collapsible card must be RE-focused after it
  // expands — on expand the chip's DOM node is replaced by the expanded card, so
  // the highlight added to the chip wouldn't survive. This ref hands the target
  // off to the [expandedCards] effect below (which re-focuses once the open card
  // is committed). null on a manual toggle ⇒ that effect no-ops (no scroll, G2).
  const focusAfterExpandRef = useRef(null);
  useEffect(() => {
    pendingScrollRef.current = scrollToMsg != null ? String(scrollToMsg) : null;
  }, [scrollToMsg, phone]);

  // Scroll a message into view and flash it briefly. Returns false if the message
  // isn't rendered (e.g. outside the loaded window). Used by the search-hit jump
  // and by clicking a reply quote.
  function focusMessage(mid, { smooth = false } = {}) {
    if (mid == null || !chatRef.current) return false;
    const el = chatRef.current.querySelector(`[data-mid="${mid}"]`);
    if (!el) return false;
    el.scrollIntoView({ block: 'center', behavior: smooth ? 'smooth' : 'auto' });
    // Restart the flash even if it was just highlighted (rapid re-clicks).
    el.classList.remove('wa-msg-highlight');
    void el.offsetWidth;
    el.classList.add('wa-msg-highlight');
    setTimeout(() => el.classList.remove('wa-msg-highlight'), 3000);
    return true;
  }

  // ── Collapsible cards (plano 63) ───────────────────────────────
  // transcription/tool_call render minimized by default as a 1-line chip. The
  // expansion state lives HERE (not inside the card) so a history prepend can't
  // glue it to the wrong message — list keys are indices (G1). It's keyed by
  // message identity via `cardStateKey`. No persistence (D2): reset when the
  // conversation changes. The DEFAULT ("collapsed") is DERIVED in the render
  // below ("absent from the Set ⇒ collapsed") — never applied via an effect, so
  // opening a conversation never flashes expanded content or jumps scroll (G5).
  const [expandedCards, setExpandedCards] = useState(() => new Set());
  const toggleCard = useCallback((key) => {
    // A NEW Set each toggle — mutating the existing one wouldn't re-render
    // (Preact compares state by reference).
    setExpandedCards((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);
  // Reset on conversation change (D2). Declared BEFORE the messages-driven scroll
  // effect below, so on a conversation switch it clears first and a search-hit
  // re-expand (F5) in that effect wins.
  useEffect(() => { setExpandedCards(new Set()); }, [conversationId, phone, channelId]);

  useEffect(() => {
    // Prepend de "carregar anteriores" (F4): a posição visual já foi RESTAURADA pelo
    // useReverseInfiniteScroll (useLayoutEffect, antes do paint). Aqui só não rolamos
    // pro fim nessa atualização (senão a viewport saltaria).
    if (justPrependedRef.current) {
      justPrependedRef.current = false;
      return;
    }
    const target = pendingScrollRef.current;
    if (target != null) {
      // Plano 63 (F5): a search hit / deep-link may land on a collapsed
      // transcription or tool_call — expand it so the operator sees the content,
      // not a bare chip. `target` is a message `_id`, and cardStateKey prefixes
      // `_id` with `id:`. Only expand a genuinely collapsible target (never
      // pollute the Set for a normal bubble). The chip carries `data-mid` too, so
      // focusMessage scrolls to it now; the [expandedCards] effect below then
      // re-focuses once the OPEN card is committed (the chip node is replaced on
      // expand, so this immediate highlight wouldn't survive).
      const wantKey = `id:${target}`;
      const tMsg = (messages || []).find((mm) => mm._id != null && String(mm._id) === target);
      if (tMsg && isCollapsibleRole(tMsg.role) && !expandedCards.has(wantKey)) {
        focusAfterExpandRef.current = target;
        setExpandedCards((prev) => new Set(prev).add(wantKey));
      }
      if (focusMessage(target)) {
        pendingScrollRef.current = null;
        if (onScrolledToMsg) onScrolledToMsg();
      }
      // Either handled, or the target isn't rendered yet — in both cases don't
      // fall through to the bottom-scroll (wait for the next messages update).
      return;
    }
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  // Plano 63 F5: re-focus a deep-linked card once it has EXPANDED. On expand the
  // collapsed chip (a component) is replaced by the expanded card (a plain div),
  // so the highlight added to the chip is dropped — this lands it on the open
  // card after the commit. Guarded by the ref: a manual expand/collapse leaves it
  // null, so this never scrolls the viewport on its own (G2).
  useEffect(() => {
    const t = focusAfterExpandRef.current;
    if (t == null) return;
    if (focusMessage(t)) focusAfterExpandRef.current = null;
  }, [expandedCards]);

  // Client-side plugin lifecycle (plano 23 §3.4): emit `ui.conversation.opened`
  // when this chat view mounts/changes and `ui.conversation.closed` on teardown.
  // Minimal + stable payload; fire-and-forget (emit() isolates throwing handlers,
  // never blocks render). Empty phone (welcome screen) emits nothing.
  useEffect(() => {
    if (!phone) return;
    const payload = { conversationId: conversationId ?? null, phone, channelId: channelId || 'default' };
    emitClientEvent('ui.conversation.opened', payload);
    return () => emitClientEvent('ui.conversation.closed', payload);
  }, [phone, conversationId, channelId]);

  // ── Render helpers (container-level; depend on messages/contact/info) ──

  // Render message text with WhatsApp formatting, highlighting @mentions of
  // known group members (and @todos). Names come from the participant list.
  function fmt(text) {
    const names = (contact && contact.is_group)
      ? autocomplete.members.map(m => m.name).filter(Boolean)
      : [];
    return formatWhatsApp(text, names);
  }

  // Locate a quoted message by its provider msg_id (plano 75 F10).
  // Prefers the `quoted` block hydrated by the server (which also covers targets
  // OUTSIDE the loaded keyset page) and falls back to the in-memory window for
  // pages served by an older backend. Matching is exact — msg_id shapes are never
  // normalized (`WAID:` prefixed ids from the Chatwoot import must keep matching).
  // `_hydrated` marks a quote whose target is NOT in the DOM: the bubble shows its
  // content but cannot scroll to it.
  function findQuoted(msgId, msg) {
    if (!msgId) return null;
    const local = messages ? (messages.find(m => m.msg_id === msgId) || null) : null;
    const hydrated = (msg && msg.quoted && msg.quoted.msg_id === msgId) ? msg.quoted : null;
    if (!hydrated) return local;
    return { ...hydrated, _id: local ? local._id : hydrated._id, _hydrated: !local };
  }

  // Build {senderLabel, senderColor, snippet} for a quoted message, mirroring
  // the bubble's own sender/side logic. Returns null when the message is gone.
  function quotedInfo(qmsg) {
    if (!qmsg) return null;
    const isGroupChat = contact && contact.is_group;
    const qIsUser = qmsg.role === 'user';
    let text = qmsg.content || '';
    let qSender = null;
    if (qIsUser && isGroupChat && typeof text === 'string') {
      const { sender, text: stripped } = stripGroupPrefix(text);
      if (sender != null) { qSender = sender; text = stripped; }
    }
    text = quotedMediaText(qmsg, text);
    const fromMe = sandbox ? qIsUser : !qIsUser;
    const dn = isGroupChat
      ? (contact.group_name || phone)
      : (info && info.name ? info.name.replace(/^~/, '') : phone);
    const senderLabel = sandbox
      ? (qIsUser ? 'Você' : 'IA')
      : (qIsUser ? (qSender || dn) : (qmsg.status === 'operator' ? (qmsg.sent_by_name || 'Manual') : 'IA'));
    const sColor = senderColor(qIsUser, qmsg.status === 'operator');
    return { senderLabel, senderColor: sColor, fromMe, snippet: (text || '').replace(/\s+/g, ' ').slice(0, 140) };
  }

  // Keydown on the textarea: let the autocomplete menus consume arrows/enter/
  // tab/esc first; otherwise Enter sends (Shift+Enter = newline; ignore IME).
  function handleKeyDown(e) {
    if (autocomplete.handleMenuKeyDown(e)) return;
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !e.repeat) {
      e.preventDefault();
      composer.handleSend(e);
    }
  }

  // Empty state — no contact selected
  if (!phone) {
    return html`
      <div class="wa-empty-bg flex flex-col items-center justify-center h-full">
        <div class="mb-8">
          <svg width="250" viewBox="0 0 303 172" class="opacity-20">
            <path fill="#8696a0" d="M229.565 160.229c32.874-12.676 53.009-32.508 53.009-54.669 0-39.356-56.792-71.26-126.87-71.26C85.627 34.3 28.835 66.204 28.835 105.56c0 20.655 17.776 39.174 45.883 51.974a8.372 8.372 0 014.773 5.573l.988 4.89a4.186 4.186 0 006.107 3.312l6.212-3.106a8.372 8.372 0 016.456-.37c12.157 3.96 25.676 6.13 39.95 6.13 7.096 0 14.038-.519 20.772-1.517a8.372 8.372 0 016.164 1.136l7.155 4.479a4.186 4.186 0 006.355-3.438l.247-5.287a8.372 8.372 0 013.636-6.223 8.372 8.372 0 017.258-1.314l17.4 4.64a4.186 4.186 0 005.096-2.013l3.47-6.587a8.372 8.372 0 017.09-4.41z"/>
          </svg>
        </div>
        <h2 class="text-wa-text text-[32px] font-light mb-2">WhatsBot-Pro</h2>
        <p class="text-wa-secondary text-[14px] text-center max-w-[450px] leading-[20px]">
          Envie e receba mensagens. Selecione um contato para começar.
        </p>
        <div class="mt-10 flex items-center gap-2 text-wa-secondary text-[12px]">
          <svg viewBox="0 0 10 12" width="10" height="12"><path fill="#8696a0" d="M5.063 0C2.272 0 .006 2.274.006 5.078v1.715L0 6.792v.7l.006.007v.206C.006 9.708 2.272 12 5.063 12h.037C7.89 12 10.1 9.708 10.1 6.905v-.2l.007-.008v-.7l-.007-.001V5.078C10.1 2.274 7.89 0 5.1 0h-.037zm0 1.2h.037c2.146 0 3.837 1.71 3.837 3.878v1.138l-.87.862v.827c0 2.168-1.69 3.895-3.837 3.895h-.037c-2.147 0-3.857-1.727-3.857-3.895v-.827l-.87-.862V5.078c0-2.168 1.71-3.878 3.857-3.878z"/></svg>
          Criptografia de ponta a ponta
        </div>
      </div>
    `;
  }

  // Base context-menu items for a message (Responder/Copiar/Copiar link/Apagar).
  // Plugins append their own items via the `filter.message.contextMenu.items`
  // seam (e.g. the "melhorias" plugin adds "Gerar melhoria"). Kept as a builder so
  // the filter runs on the CURRENT message each time the menu opens.
  function buildBaseItems(message, isFromMe) {
    return [
      ...((canReply && !message.revoked && composer.mode !== 'private'
           && message.role !== 'private_note') ? [
        { label: 'Responder', icon: ReplyIcon,
          onClick: () => { composer.setMode('reply'); composer.setReplyingTo(message);
                           setTimeout(() => composer.inputRef.current?.focus(), 0); } },
      ] : []),
      // Editar: só mensagens de SAÍDA (isFromMe), de texto, já enviadas (msg_id),
      // em canais que suportam edição. Não em revogadas / notas privadas / mídia.
      ...((canReply && editSupported && isFromMe && !message.revoked && message.msg_id
           && !message.media_type && message.role !== 'private_note') ? [
        { label: 'Editar', icon: EditIcon,
          onClick: () => actions.setEditDialog({ message }) },
      ] : []),
      { label: 'Copiar', icon: CopyIcon, onClick: () => actions.copyMessageText(message) },
      { label: 'Copiar link da mensagem', icon: LinkIcon,
        disabled: !actions.messagePermalink(message),
        onClick: () => actions.copyMessageLink(message) },
      // plano 51 (04 F1): entra no modo de seleção em lote — só existe quando
      // algum plugin registrou uma ação de lote (sem plugin, menu byte-idêntico).
      ...(getFilters('filter.selection.batchActions').length ? [
        { label: 'Selecionar mensagens', icon: SelectManyIcon,
          onClick: () => actions.enterSelection(message) },
      ] : []),
      ...((canReply && !message.revoked && revokeSupported) ? [
        { label: 'Apagar', icon: TrashIcon, danger: true,
          onClick: () => actions.setDeleteDialog({ message, isFromMe }) },
      ] : []),
    ];
  }

  // Open the per-message context menu. Async: builds the base items, lets plugins
  // extend the array via the client filter, then stores the resolved items on the
  // menu state so the (sync) render just reads them. Returning `null` from a
  // filter aborts — we fall back to the base items so the menu still opens.
  async function openMsgMenu(e, message, isFromMe) {
    e.preventDefault();
    e.stopPropagation();
    const x = e.clientX || (e.currentTarget && e.currentTarget.getBoundingClientRect().left) || 0;
    const y = e.clientY || (e.currentTarget && e.currentTarget.getBoundingClientRect().bottom) || 0;
    const base = buildBaseItems(message, isFromMe);
    let items = base;
    try {
      const out = await applyFilter('filter.message.contextMenu.items', base,
        { message, isFromMe, phone, conversationId, sandbox });
      if (Array.isArray(out)) items = out;
    } catch (_) { /* keep base items */ }
    actions.setMsgMenu({ x, y, message, isFromMe, items });
  }

  return html`
    <div
      class="flex flex-col h-full relative"
      ref=${drop.rootRef}
      onDragEnter=${drop.dropHandlers.onDragEnter}
      onDragOver=${drop.dropHandlers.onDragOver}
      onDragLeave=${drop.dropHandlers.onDragLeave}
      onDrop=${drop.dropHandlers.onDrop}
    >
      <!-- Header -->
      <div class="h-[59px] flex items-center pl-4 pr-[56px] bg-wa-panel border-b border-wa-border shrink-0">
        <button onClick=${onBack} class="lg:hidden text-wa-icon hover:text-wa-text mr-2 shrink-0">
          <${BackArrowIcon} />
        </button>
        <div onClick=${canReadContact ? onAvatarClick : null} class="w-[40px] h-[40px] rounded-full overflow-hidden shrink-0 mr-[13px] ${canReadContact ? 'cursor-pointer' : ''}">
          ${isGroup
            ? html`<${GroupAvatar} size=${40} avatarUrl=${avatarUrl(phone, contact && contact.avatar_v)} />`
            : html`<${DefaultAvatar} size=${40} avatarUrl=${avatarUrl(phone, contact && contact.avatar_v)} />`
          }
        </div>
        <div class="flex-1 min-w-0 ${canReadContact ? 'cursor-pointer' : ''}" onClick=${canReadContact ? onAvatarClick : null} title=${'Conversa com ' + displayName}>
          <div class="text-wa-text text-[16px] leading-tight truncate flex items-center gap-[6px]">
            <span class=${'truncate' + (isAutoName ? ' underline decoration-1 underline-offset-2' : '')} title=${isAutoName ? 'Nome obtido do WhatsApp (ainda não renomeado)' : null}>${displayName}</span>${
            // Canal do atendimento, logo após o nome — o MESMO selo da linha da barra
            // lateral, gateado pelo mesmo `showChannel` (só com 2+ canais instalados),
            // para as duas telas nunca discordarem sobre mostrar ou não.
            showChannel ? html`<${ChannelChip} provider=${channelProvider} name=${channelName} margin=${false} />` : null
            }${contact && contact.tags && contact.tags.length > 0 ? contact.tags.map(tagName => {
              const tagInfo = globalTags && globalTags[tagName];
              const color = tagInfo ? tagInfo.color : '#6b7280';
              return html`<span
                class="text-[9px] font-semibold rounded-full px-[5px] py-[0.5px] leading-[14px] shrink-0"
                style="background: ${color}20; color: ${color}; border: 1px solid ${color}40;"
              >${tagName}</span>`;
            }) : null}
          </div>
          ${aiResponding
            ? html`<div class="text-wa-teal text-[13px] leading-tight font-medium flex items-center gap-1.5">
                <span class="inline-block w-1.5 h-1.5 rounded-full bg-wa-teal animate-pulse"></span>
                <span>IA respondendo…</span>
              </div>`
            : contactTyping
            ? html`<div class="text-wa-teal text-[13px] leading-tight">${contactTyping === 'audio' ? 'gravando áudio...' : 'digitando...'}</div>`
            : isGroup ? html`<div class="text-wa-secondary text-[13px] leading-tight">Grupo</div>`
            : info && info.name ? html`<div class="text-wa-secondary text-[13px] leading-tight">${headerSubtitle}</div>` : null
          }
        </div>

        <!-- Conversation actions (FF3): resolver / atribuir / transferir / IA. -->
        <${ConversationHeaderActions} phone=${phone} conversationId=${conversationId} sandbox=${sandbox} onOpenConversationInfo=${onOpenConversationInfo} onOpenContactInfo=${onAvatarClick} contactInfo=${info} />

        <!-- Informações do atendimento (Onda 2): abre o painel lateral do atendimento. -->
        ${!sandbox && onOpenConversationInfo && canReadConv ? html`
          <button
            type="button"
            onClick=${onOpenConversationInfo}
            class="shrink-0 ml-1 text-wa-icon hover:text-wa-text p-[6px] rounded-full hover:bg-wa-hover transition-colors"
            title="Informações da conversa"
          >
            <${InfoIcon} />
          </button>
        ` : null}
      </div>

      <!-- Plugin extension point: banner abaixo do header / acima das mensagens
           (faixa "atendimento atual" — SLA, aviso, etc.). Empty by default. -->
      <${Slot} name="chat.header.banner" ctx=${{ conv: { conversationId, phone, channelId }, conversationId, phone, channelId, contact }} />

      <!-- Chat area with doodle pattern -->
      <div ref=${chatRef} class="flex-1 min-h-0 overflow-y-auto overscroll-contain wa-scrollbar wa-chat-pattern py-2 px-[4%] lg:px-[7%]">
        <!-- Sentinela do topo (F4): dispara "carregar anteriores" AUTOMÁTICO ao entrar na
             viewport (rolar até o topo). Sem botão manual — só o indicador de carregando. -->
        ${hasMore ? html`
          <div ref=${topSentinelRef} class="flex justify-center py-2 min-h-[8px]">
            ${loadingOlder
              ? html`<span class="bg-wa-bg/80 text-wa-secondary rounded-lg px-3 py-1.5 text-[12px] shadow-sm">Carregando anteriores…</span>`
              : null}
          </div>` : null}
        ${!messages || messages.length === 0
          ? html`<div class="text-center text-wa-secondary py-8 text-[14px]">
              <span class="bg-wa-bg/80 rounded-lg px-3 py-1.5 text-[12.5px] shadow-sm">Nenhuma mensagem ainda</span>
            </div>`
          : messages.map((m, i) => {
              const isFirst = i === 0 || messages[i - 1].role !== m.role;
              const prevTs = i > 0 ? messages[i - 1].ts : null;
              const showDateSep = m.ts && (!prevTs || !isSameDay(prevTs, m.ts));
              const dateSeparator = showDateSep
                ? html`<div key=${`sep-${m.ts}-${i}`} class="flex justify-center my-[12px]">
                    <span class="bg-wa-bg/90 text-wa-secondary text-[12px] font-medium uppercase tracking-wide rounded-[7.5px] px-[12px] py-[5px] shadow-sm">
                      ${formatDateSeparator(m.ts)}
                    </span>
                  </div>`
                : null;

              if (isSystemCardRole(m.role)) {
                // List key MUST live on the array-member vnode (Preact reconciles
                // by the key on the direct child of the mapped array, not on the
                // element the component returns).
                // Plano 63 F2: key by message identity (cardStateKey), not index —
                // the history prepends, and an index key would drift after "load
                // older". Keep the optimistic `_localId` precedence for a
                // not-yet-saved private_note.
                const stateKey = cardStateKey(m, i);
                const cardKey = m._localId || stateKey;
                // Plano 63 F4: transcription/tool_call are collapsed unless the
                // user expanded THIS card. Derived in render (no effect) so there's
                // no flash/jump on open (G5); the card is controlled (G1).
                const collapsed = isCollapsibleRole(m.role) && !expandedCards.has(stateKey);
                return [dateSeparator, html`<${SystemMessageCard}
                  key=${cardKey} message=${m} index=${i} fmt=${fmt} openMsgMenu=${openMsgMenu}
                  showAgentName=${showAgentName}
                  collapsed=${collapsed} onToggleCollapse=${() => toggleCard(stateKey)} />`];
              }

              return [dateSeparator, html`<${MessageBubble}
                key=${m._localId || i} message=${m} index=${i} isFirst=${isFirst}
                isGroup=${isGroup} sandbox=${sandbox} displayName=${displayName} fmt=${fmt}
                findQuoted=${findQuoted} quotedInfo=${quotedInfo} focusMessage=${focusMessage}
                openMsgMenu=${openMsgMenu} myReaction=${myReaction} handleRetry=${canReply ? composer.handleRetry : null}
                showAgentName=${showAgentName}
                selectionMode=${actions.selectionMode}
                selected=${actions.selectionMode && actions.selection.has(selectionKey(m))}
                onToggleSelect=${actions.toggleSelect} />`];
            })
        }
      </div>

      <!-- Barra de ação em lote (plano 51 · 04 F1): só existe em selectionMode. -->
      ${actions.selectionMode ? html`
        <div class="flex items-center gap-2 px-[4%] lg:px-[7%] py-2 bg-wa-panel border-t border-wa-border shrink-0">
          <button
            onClick=${actions.clearSelection}
            class="text-wa-icon hover:text-wa-text p-[6px] rounded-full hover:bg-wa-hover transition-colors"
            title="Cancelar seleção"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
          </button>
          <span class="text-wa-text text-[14px] font-medium flex-1">
            ${selectedMessages.length} ${selectedMessages.length === 1 ? 'selecionada' : 'selecionadas'}
          </span>
          ${batchItems.map((item) => html`
            <button
              onClick=${item.onClick}
              disabled=${!!item.disabled}
              class="flex items-center gap-1.5 text-[13px] font-medium rounded-full px-3 py-1.5 transition-colors ${item.disabled ? 'text-wa-secondary bg-wa-hover cursor-not-allowed' : 'text-white bg-wa-teal hover:brightness-95'}"
            >
              ${item.icon ? html`<${item.icon} />` : ''}${item.label}
            </button>
          `)}
        </div>
      ` : ''}

      <!-- Composer: input bar (wires composer/autocomplete/media/audio hooks).
           P48: hidden entirely without conversation.reply — read-only banner. -->
      ${canReply ? html`
      <${Composer}
        sandbox=${sandbox} canSend=${canSend} templatesSupported=${templatesSupported} sessionClosed=${sessionClosed}
        composer=${composer} autocomplete=${autocomplete} media=${media} audio=${audio}
        quotedInfo=${quotedInfo} openTemplatePicker=${openTemplatePicker} handleKeyDown=${handleKeyDown} currentUser=${currentUser} />
      ` : html`
      <div class="px-[4%] lg:px-[7%] py-3 bg-wa-panel border-t border-wa-border shrink-0 text-center text-wa-secondary text-[13px]">
        Somente leitura — você não tem permissão para responder nesta conversa.
      </div>
      `}

      ${showTemplatePicker ? html`
        <${TemplatePicker}
          conversationId=${conversationId}
          channelId=${channelId}
          phone=${phone}
          onClose=${() => setShowTemplatePicker(false)}
          onSent=${() => setShowTemplatePicker(false)}
        />
      ` : ''}
      ${actions.msgMenu ? html`
        <${MessageContextMenu}
          x=${actions.msgMenu.x}
          y=${actions.msgMenu.y}
          reactionBar=${(canReply && !actions.msgMenu.message.revoked && actions.msgMenu.message.msg_id && !sandbox) ? {
            emojis: QUICK_REACTIONS,
            current: myReaction(actions.msgMenu.message),
            onReact: (em) => actions.performReact(actions.msgMenu.message, em),
          } : null}
          items=${actions.msgMenu.items || []}
          onClose=${() => actions.setMsgMenu(null)}
        />
      ` : ''}
      ${actions.deleteDialog ? html`
        <div
          class="fixed inset-0 z-[130] bg-black/40 flex items-center justify-center"
          onClick=${() => actions.setDeleteDialog(null)}
        >
          <div
            class="bg-wa-panel rounded-lg shadow-xl w-[330px] max-w-[90vw] p-[22px]"
            onClick=${(e) => e.stopPropagation()}
          >
            <div class="text-[15px] text-wa-text mb-[20px]">Deseja apagar a mensagem?</div>
            <div class="flex flex-col items-end gap-[10px]">
              ${actions.deleteDialog.isFromMe && actions.deleteDialog.message.msg_id ? html`
                <button
                  onClick=${() => actions.performDelete(actions.deleteDialog.message, 'all')}
                  class="px-[20px] py-[8px] rounded-full border border-wa-teal text-wa-teal text-[14px] font-medium hover:bg-wa-teal/10 transition-colors"
                >Apagar para todos</button>
              ` : ''}
              <button
                onClick=${() => actions.performDelete(actions.deleteDialog.message, 'me')}
                class="px-[20px] py-[8px] rounded-full border border-wa-teal text-wa-teal text-[14px] font-medium hover:bg-wa-teal/10 transition-colors"
              >Apagar para mim</button>
              <button
                onClick=${() => actions.setDeleteDialog(null)}
                class="px-[20px] py-[8px] rounded-full text-wa-teal text-[14px] font-medium hover:bg-wa-teal/10 transition-colors"
              >Cancelar</button>
            </div>
          </div>
        </div>
      ` : ''}
      ${actions.editDialog ? html`
        <${MessageEditDialog}
          message=${actions.editDialog.message}
          onSave=${(msg, text) => actions.performEdit(msg, text)}
          onCancel=${() => actions.setEditDialog(null)}
        />
      ` : ''}
      ${drop.dragging ? html`<${DropOverlay} zone=${drop.zone} />` : ''}
    </div>
  `;
}
