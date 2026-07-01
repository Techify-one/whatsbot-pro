import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { sendMessage, sendImage, sendAudio, sendDocument } from '../../services/api.js';
import { BackArrowIcon, DefaultAvatar, GroupAvatar, InfoIcon } from './icons.js';
import { isSameDay, formatDateSeparator, avatarUrl } from './utils.js';
import { formatWhatsApp } from '../../utils/formatWhatsApp.js';
import { MessageContextMenu, CopyIcon, TrashIcon, ReplyIcon, LinkIcon, ImproveIcon } from './MessageContextMenu.js';
import { ConversationHeaderActions } from './ConversationHeaderActions.js';
import { TemplatePicker } from './TemplatePicker.js';
import { Slot } from '../../plugins/Slot.js';
import { emit as emitClientEvent } from '../../plugins/registry.js';
import { MessageBubble } from './MessageBubble.js';
import { SystemMessageCard, isSystemCardRole } from './SystemMessageCard.js';
import { Composer } from './Composer.js';
import { useComposer } from './hooks/useComposer.js';
import { useAudioRecorder } from './hooks/useAudioRecorder.js';
import { useMediaUpload } from './hooks/useMediaUpload.js';
import { useTokenAutocomplete } from './hooks/useTokenAutocomplete.js';
import { useMessageActions, myReaction } from './hooks/useMessageActions.js';
import { stripGroupPrefix } from '../../services/composerTokens.js';
import { senderColor, quotedMediaText } from '../../services/messageView.js';
import { hasPermission } from '../../utils/permissions.js';

const html = htm.bind(h);

// Quick-reaction emojis shown in the message context menu bar (WhatsApp-style).
const QUICK_REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🙏'];

// ── Contact Detail (WhatsApp Web chat panel) ─────────────────────
//
// Thin container (Plano 23 · D3): composes the message-list render + the
// composer / token-autocomplete / media-upload / audio-recorder / message-action
// hooks, plus the presentational components (MessageBubble, SystemMessageCard,
// MediaContent, Composer). The chat header, scroll/pagination effects, the
// reply-quote lookup and the dialogs (delete / improve / template / context menu)
// stay here; everything composer-related lives in the hooks/components.

export function ContactDetail({ phone, conversationId = null, channelId = null, onBack, messages, info, contact, onAvatarClick, onOpenConversationInfo = null, currentUser = null, contactTyping, aiResponding = false, setContactData, globalTags, groupParticipantsChanged = null, sandbox = false, api = null, scrollToMsg = null, onScrolledToMsg = null }) {
  // P48 hides (sandbox is always allowed — no RBAC identity there).
  const canReadContact = sandbox || hasPermission(currentUser, 'contact.read');
  const canReadConv = sandbox || hasPermission(currentUser, 'conversation.read');
  const canReply = sandbox || hasPermission(currentUser, 'conversation.reply');
  // Effective send API. Sandbox injects local (no-GOWA) endpoints; the contact
  // chat uses the real ones.
  const _api = {
    sendText: sendMessage, sendImage, sendAudio, sendDocument,
    ...(api || {}),
  };

  const chatRef = useRef(null);
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

  // ── Hooks ──────────────────────────────────────────────────────
  // Message actions own `updateMsgByLocalId` (shared by composer + media).
  const actions = useMessageActions({ phone, conversationId, setContactData });
  const { updateMsgByLocalId } = actions;

  // The composer needs the autocomplete's `updateMenus`/`closeMentionMenu`, and
  // the autocomplete needs the composer's `input`/`setInput`/`inputRef`. Break
  // the order cycle with a ref the composer's event handlers read at call time.
  const autocompleteRef = useRef(null);

  const composer = useComposer({
    api: _api, phone, conversationId, channelId, sandbox, sessionClosed,
    setContactData, updateMsgByLocalId,
    updateMenus: (el, val) => autocompleteRef.current && autocompleteRef.current.updateMenus(el, val),
    closeMentionMenu: () => autocompleteRef.current && autocompleteRef.current.setMentionMenu(null),
    openTemplatePicker,
  });

  const autocomplete = useTokenAutocomplete({
    phone, sandbox, contact, groupParticipantsChanged,
    input: composer.input, setInput: composer.setInput, inputRef: composer.inputRef,
  });
  autocompleteRef.current = autocomplete;

  const media = useMediaUpload({
    api: _api, phone, conversationId, channelId, sandbox, sessionClosed,
    mode: composer.mode, aiReadPrivate: composer.aiReadPrivate,
    aiReplyInChat: composer.aiReadPrivate ? composer.aiReplyInChat : true,
    setContactData, updateMsgByLocalId, openTemplatePicker,
  });

  const audio = useAudioRecorder({
    onRecorded: (item) => media.setPendingAudio(item),
  });

  // ── Scroll / search-hit jump ───────────────────────────────────
  // Remember a message to focus (e.g. opened from a search hit) until it renders,
  // so the messages-driven scroll below jumps to it instead of to the bottom.
  const pendingScrollRef = useRef(null);
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

  useEffect(() => {
    const target = pendingScrollRef.current;
    if (target != null) {
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

  // Locate a quoted message in the current thread by its GOWA msg_id.
  function findQuoted(msgId) {
    if (!msgId || !messages) return null;
    return messages.find(m => m.msg_id === msgId) || null;
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
      : (qIsUser ? (qSender || dn) : (qmsg.status === 'operator' ? 'Manual' : 'IA'));
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
        <h2 class="text-wa-text text-[32px] font-light mb-2">WhatsBot</h2>
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

  return html`
    <div class="flex flex-col h-full">
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
            <span class=${'truncate' + (isAutoName ? ' underline decoration-1 underline-offset-2' : '')} title=${isAutoName ? 'Nome obtido do WhatsApp (ainda não renomeado)' : null}>${displayName}</span>${contact && contact.tags && contact.tags.length > 0 ? contact.tags.map(tagName => {
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
            : info && info.name ? html`<div class="text-wa-secondary text-[13px] leading-tight">${phone}</div>` : null
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
            title="Informações do atendimento"
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
                // element the component returns). Match the baseline exactly:
                // private_note keyed by _localId||i, all other cards by i.
                const cardKey = m.role === 'private_note' ? (m._localId || i) : i;
                return [dateSeparator, html`<${SystemMessageCard}
                  key=${cardKey} message=${m} index=${i} fmt=${fmt} openMsgMenu=${actions.openMsgMenu} />`];
              }

              return [dateSeparator, html`<${MessageBubble}
                key=${m._localId || i} message=${m} index=${i} isFirst=${isFirst}
                isGroup=${isGroup} sandbox=${sandbox} displayName=${displayName} fmt=${fmt}
                findQuoted=${findQuoted} quotedInfo=${quotedInfo} focusMessage=${focusMessage}
                openMsgMenu=${actions.openMsgMenu} myReaction=${myReaction} handleRetry=${canReply ? composer.handleRetry : null} />`];
            })
        }
      </div>

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
          items=${[
            ...((canReply && !actions.msgMenu.message.revoked && composer.mode !== 'private'
                 && actions.msgMenu.message.role !== 'private_note') ? [
              { label: 'Responder', icon: ReplyIcon,
                onClick: () => { composer.setMode('reply'); composer.setReplyingTo(actions.msgMenu.message);
                                 setTimeout(() => composer.inputRef.current?.focus(), 0); } },
            ] : []),
            { label: 'Copiar', icon: CopyIcon, onClick: () => actions.copyMessageText(actions.msgMenu.message) },
            { label: 'Copiar link da mensagem', icon: LinkIcon,
              disabled: !actions.messagePermalink(actions.msgMenu.message),
              onClick: () => actions.copyMessageLink(actions.msgMenu.message) },
            ...((canReply && !actions.msgMenu.message.revoked) ? [
              { label: 'Apagar', icon: TrashIcon, danger: true,
                onClick: () => actions.setDeleteDialog({ message: actions.msgMenu.message, isFromMe: actions.msgMenu.isFromMe }) },
            ] : []),
            ...((!actions.msgMenu.message.revoked && !sandbox
                 && actions.msgMenu.message.role === 'assistant'
                 && actions.msgMenu.message.status !== 'operator') ? [
              { label: 'Gerar melhoria', icon: ImproveIcon,
                onClick: () => actions.openImprove(actions.msgMenu.message) },
            ] : []),
          ]}
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
      ${actions.improveDialog ? html`
        <div
          class="fixed inset-0 z-[130] bg-black/40 flex items-center justify-center p-4"
          onClick=${() => { if (!actions.improveLoading) actions.setImproveDialog(null); }}
        >
          <div
            class="bg-wa-panel rounded-lg shadow-xl w-[440px] max-w-[92vw] p-[22px] flex flex-col gap-3"
            onClick=${(e) => e.stopPropagation()}
          >
            <div class="flex items-center gap-2 text-[15px] font-semibold text-wa-text">
              <span class="text-wa-teal">${ImproveIcon}</span>
              Gerar melhoria
            </div>
            <p class="text-[13px] text-wa-secondary -mt-1">
              A IA vai analisar o prompt, as ferramentas e o histórico para sugerir
              ajustes. O resultado aparece como uma mensagem de Sistema no chat
              (visível só no painel).
            </p>
            <div>
              <span class="block text-[12px] font-medium text-wa-secondary mb-1">Resposta marcada como incorreta</span>
              <div class="max-h-[120px] overflow-y-auto rounded-md border border-wa-border bg-wa-bg px-3 py-2 text-[13px] text-wa-text whitespace-pre-wrap">
                ${(actions.improveDialog.message.content || '').trim() || '(sem conteúdo)'}
              </div>
            </div>
            <div>
              <label class="block text-[12px] font-medium text-wa-secondary mb-1">O que saiu errado? (opcional)</label>
              <textarea
                value=${actions.improveText}
                onInput=${(e) => actions.setImproveText(e.target.value)}
                disabled=${actions.improveLoading}
                rows="3"
                placeholder="Ex.: respondeu o valor errado, não usou a ferramenta de agenda, tom inadequado…"
                class="wa-field w-full px-3 py-2 rounded-md text-[13px] resize-none"
              ></textarea>
            </div>
            ${actions.improveError ? html`<div class="text-[12px] text-red-500">${actions.improveError}</div>` : ''}
            <div class="flex justify-end gap-2 mt-1">
              <button
                onClick=${() => actions.setImproveDialog(null)}
                disabled=${actions.improveLoading}
                class="px-4 py-2 rounded-lg text-[14px] font-medium text-wa-text bg-wa-bg hover:bg-wa-hover border border-wa-border transition-colors disabled:opacity-50"
              >Cancelar</button>
              <button
                onClick=${actions.submitImprovement}
                disabled=${actions.improveLoading}
                class="px-4 py-2 rounded-lg text-[14px] font-medium text-white bg-wa-teal hover:opacity-90 transition-colors disabled:opacity-50 flex items-center gap-2"
              >
                ${actions.improveLoading ? html`
                  <span class="inline-block w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin"></span>
                  Analisando…
                ` : 'Gerar análise'}
              </button>
            </div>
          </div>
        </div>
      ` : ''}
    </div>
  `;
}
