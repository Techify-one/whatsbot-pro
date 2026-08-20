import { h } from 'preact';
import { useEffect, useRef, useState, useCallback } from 'preact/hooks';
import htm from 'htm';
import { sendMessage, sendImage, sendAudio, sendDocument, sendVideo } from '../../services/api.js';
import { BackArrowIcon, DefaultAvatar, GroupAvatar } from './icons.js';
import { isSameDay, formatDateSeparator, avatarUrl } from './utils.js';
import { formatWhatsApp, toWhatsAppMarkup } from '../../utils/formatWhatsApp.js';
import { MessageContextMenu, CopyIcon, TrashIcon, ReplyIcon, LinkIcon, EditIcon,
         MailIcon, PhoneIcon, OpenExternalIcon, copyToClipboard } from './MessageContextMenu.js';
import { entityFromElement, entityActions } from '../../services/messageEntities.js';
import { notify } from '../../services/notify.js';
import { ConversationHeaderActions } from './ConversationHeaderActions.js';
import { TemplatePickerHost } from './TemplatePickerHost.js';
import { Slot } from '../../plugins/Slot.js';
// Selo do canal — MESMO componente da linha da barra lateral.
import { ChannelChip } from './ChannelChip.js';
import { emit as emitClientEvent, applyFilter, getFilters } from '../../plugins/registry.js';
import { MessageBubble } from './MessageBubble.js';
import { MessageEditDialog } from './MessageEditDialog.js';
import { SystemMessageCard, isSystemCardRole } from './SystemMessageCard.js';
import { Composer } from './Composer.js';
import { useReverseInfiniteScroll, useScrollSentinel } from '../../hooks/useInfiniteScroll.js';
import { ConversationSearchBar } from './ConversationSearchBar.js';
import { planJump, isRendered } from '../../services/threadJump.js';
import { highlightHtml } from '../../services/searchHighlight.js';
import { useComposer } from './hooks/useComposer.js';
import { useAudioRecorder } from './hooks/useAudioRecorder.js';
import { useMediaUpload } from './hooks/useMediaUpload.js';
import { useDropZone } from './hooks/useDropZone.js';
import { DropOverlay } from './DropOverlay.js';
import { useTokenAutocomplete } from './hooks/useTokenAutocomplete.js';
import { useMessageActions, myReaction, selectionKey } from './hooks/useMessageActions.js';
import { useChatDayHeader } from './hooks/useChatDayHeader.js';
import { PILL_TRAVEL } from '../../services/chatDayHeader.js';
import { useContactSubtitle } from './hooks/useContactSubtitle.js';
import { stripGroupPrefix } from '../../services/composerTokens.js';
import { senderColor, quotedMediaText, cardStateKey, isCollapsibleCard } from '../../services/messageView.js';
import { hasPermission } from '../../utils/permissions.js';
import { transitionAfterOutput } from '../../services/outputTransition.js';
import { submitPlan, isAudioOnly } from '../../services/composerSubmit.js';

const html = htm.bind(h);

// Quick-reaction emojis shown in the message context menu bar (WhatsApp-style).
const QUICK_REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🙏'];

// Ícone do item "Selecionar mensagens" (plano 51 · 04 F1).
const SelectManyIcon = () => html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M3 5h2V3c-1.1 0-2 .9-2 2zm0 8h2v-2H3v2zm4 8h2v-2H7v2zM3 9h2V7H3v2zm10-6h-2v2h2V3zm6 0v2h2c0-1.1-.9-2-2-2zM5 21v-2H3c0 1.1.9 2 2 2zm-2-4h2v-2H3v2zM9 3H7v2h2V3zm2 18h2v-2h-2v2zm8-8h2v-2h-2v2zm0 8c1.1 0 2-.9 2-2h-2v2zm0-12h2V7h-2v2zm0 8h2v-2h-2v2zm-4 4h2v-2h-2v2zm0-16h2V3h-2v2zM7 17h10V7H7v10zm2-8h6v6H9V9z"/>
  </svg>`;

// ── Ações de ENTIDADE no menu da mensagem (plano 97 · F3) ────────
//
// O módulo puro `services/messageEntities.js` decide O QUE dá para fazer com o
// link / e-mail / telefone sob o cursor; aqui só resolvemos a chave do ícone e
// executamos (abrir / copiar). Sem entidade sob o cursor, nada disso aparece e o
// menu fica byte-idêntico ao de sempre.
const ENTITY_ICONS = { open: OpenExternalIcon, copy: CopyIcon, mail: MailIcon, phone: PhoneIcon };
const COPY_TOASTS = {
  'copy-url': 'Link copiado.',
  'copy-email': 'E-mail copiado.',
  'copy-phone': 'Número copiado.',
  'copy-jid': 'Número copiado.',
};

// `mailto:` / `tel:` são entregues ao handler do sistema pela navegação normal —
// abrir aba para eles deixaria uma janela em branco. Só http(s) vira aba nova.
function openEntityHref(href) {
  if (/^https?:/i.test(href)) window.open(href, '_blank', 'noopener,noreferrer');
  else window.location.href = href;
}

function entityMenuItems(entity) {
  return entityActions(entity).map((a) => ({
    label: a.label,
    icon: ENTITY_ICONS[a.icon] || LinkIcon,
    onClick: () => {
      if (a.href) { openEntityHref(a.href); return; }
      copyToClipboard(a.copy);
      notify(COPY_TOASTS[a.id] || 'Copiado.', { kind: 'success' });
    },
  }));
}

// A entidade sob o cursor. `e.target` pode ser um nó de texto (sem `closest`) ou
// um `<svg>` da setinha de hover — os dois degradam para "nenhuma entidade".
function entityUnderCursor(e) {
  try {
    const t = e && e.target;
    if (!t) return null;
    const el = typeof t.closest === 'function'
      ? t.closest('[data-entity]')
      : (t.parentElement ? t.parentElement.closest('[data-entity]') : null);
    return entityFromElement(el);
  } catch (_) { return null; }
}

// Texto selecionado DENTRO da bolha em que o menu abriu. Precisa ser lido no
// momento da abertura (o clique fora fecha o menu e desfaz a seleção).
const SELECTION_CAP = 5000;
function selectionInside(e) {
  try {
    const sel = window.getSelection ? window.getSelection() : null;
    if (!sel || sel.isCollapsed) return '';
    const container = e && e.currentTarget;
    if (container && container.contains && sel.anchorNode && !container.contains(sel.anchorNode)) return '';
    return String(sel.toString() || '').trim().slice(0, SELECTION_CAP);
  } catch (_) { return ''; }
}

// Três pontinhos pulsando — a assinatura visual de "digitando" (Chatwoot/WhatsApp).
// O atraso escalonado é inline porque o valor não existe como classe do Tailwind.
const TypingDots = () => html`
  <span class="flex items-center gap-[3px] shrink-0">
    ${[0, 150, 300].map(d => html`
      <span key=${d} class="inline-block w-[5px] h-[5px] rounded-full bg-wa-secondary animate-bounce"
            style=${`animation-delay:${d}ms`}></span>`)}
  </span>
`;

// ── Contact Detail (WhatsApp Web chat panel) ─────────────────────
//
// Thin container (Plano 23 · D3): composes the message-list render + the
// composer / token-autocomplete / media-upload / audio-recorder / message-action
// hooks, plus the presentational components (MessageBubble, SystemMessageCard,
// MediaContent, Composer). The chat header, scroll/pagination effects, the
// reply-quote lookup and the dialogs (delete / improve / template / context menu)
// stay here; everything composer-related lives in the hooks/components.

export function ContactDetail({ phone, conversationId = null, channelId = null, onBack, messages, info, contact,
  channelProvider = null, channelName = null, showChannel = false, onAvatarClick, onOpenConversationInfo = null, currentUser = null, contactTyping, aiResponding = false, operatorTyping = null, setContactData, groupParticipantsChanged = null, sandbox = false, api = null, scrollToMsg = null, onScrolledToMsg = null, showAgentName = true, loadOlder = null, loadingOlder = false, hasMore = false, droppedFiles = null, onDroppedFilesConsumed = null,
  // Plano 99 — janela ancorada + os caminhos de salto. Todos opcionais: sem eles
  // (sandbox, chamador antigo) o painel se comporta exatamente como antes.
  loadNewer = null, loadingNewer = false, hasMoreNewer = false, jumping = false,
  onJumpToMessage = null, onJumpToDate = null, onBackToBottom = null,
  newWhileAnchored = 0,
  // A engrenagem do app é `fixed` no canto superior direito e passaria POR CIMA do
  // fim do cabeçalho; por isso o header reserva 56px à direita. Quando ela está na
  // barra da sidebar (hub de conversas com a sidebar à vista), o canto é livre e o
  // menu (⋮) da conversa ocupa a ponta. Default `true` = comportamento antigo, para
  // o chamador que não sabe disso (sandbox).
  gearFloating = true }) {
  // P48 hides (sandbox is always allowed — no RBAC identity there).
  const canReadContact = sandbox || hasPermission(currentUser, 'contact.read');
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

  // Sentinela de BAIXO (plano 99 F0d·3) — "carregar seguintes" numa janela
  // ancorada no passado. É um `useScrollSentinel` SEPARADO de propósito: o hook
  // de cima carrega uma âncora de scroll e a restaura num `useLayoutEffect`
  // (código delicado, mexer nele quebraria o scroll-up); aqui não é preciso nada
  // disso, porque anexar conteúdo ABAIXO da viewport não desloca o que já está
  // em tela. Só existe enquanto `has_more_newer` — na conversa normal, que
  // termina na última mensagem, nem é montado.
  const bottomSentinelRef = useRef(null);
  const canLoadNewer = !!(hasMoreNewer && loadNewer);
  useScrollSentinel(
    bottomSentinelRef,
    () => { if (canLoadNewer && !loadingNewer) loadNewer(); },
    canLoadNewer, chatRef, '0px 0px 120px 0px');

  // ── Modo busca (plano 99 F2) ───────────────────────────────────────
  // Aberto pela lupa do header, ele SUBSTITUI a barra do header (o header já
  // está cheio). O termo mora aqui porque duas peças o consomem: a barra (que
  // procura) e o render das bolhas (que destaca).
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  // Sem atendimento não há o que pesquisar — a busca é POR CONVERSA (P1), e o
  // sandbox não tem linha na sidebar nem endpoint de busca.
  const canSearchThread = !sandbox && conversationId != null && !!onJumpToMessage;
  const closeSearch = useCallback(() => { setSearchOpen(false); setSearchTerm(''); }, []);
  // "Ir para data" (plano 99 F4·3). O servidor aterrissa na primeira mensagem com
  // `ts >=` o dia pedido — então um dia SEM conversa leva ao próximo dia com
  // conteúdo, que é o comportamento do WhatsApp. O que não pode acontecer é isso
  // ser silencioso: sem aviso, escolher um domingo vazio e cair na terça parece
  // que o calendário ignorou o clique.
  const handlePickDate = useCallback((ts) => {
    if (!onJumpToDate) return;
    Promise.resolve(onJumpToDate(ts)).then((r) => {
      if (!r) return;
      if (!r.ok) {
        notify('Não foi possível ir para essa data.', { kind: 'error' });
        return;
      }
      if (r.anchorId == null) {
        notify('Nenhuma mensagem nessa data ou depois dela.', { kind: 'info' });
        return;
      }
      if (r.anchorTs != null && !isSameDay(ts, r.anchorTs)) {
        notify(`Sem mensagens nesse dia — abrimos em ${formatDateSeparator(r.anchorTs)}.`,
               { kind: 'info' });
      }
    });
  }, [onJumpToDate]);
  // Trocar de conversa fecha a busca — o termo pertencia à thread anterior.
  useEffect(() => { setSearchOpen(false); setSearchTerm(''); }, [conversationId, phone]);

  // Pílula de data fixa (plano 98): qual dia está no topo da área de mensagens.
  // O separador inline já existe, mas rola para fora da viewport — numa conversa
  // de centenas de mensagens o operador perdia a referência temporal. A medição
  // roda sobre os separadores (O(nº de dias)) e a decisão é do módulo puro.
  const chatDay = useChatDayHeader({ scrollRef: chatRef, items: messages });

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
  // sessionClosed → a janela de texto livre expirou (ou nunca abriu, no caso de
  // um número novo no Cloud). No Cloud ainda resta o template; num canal SEM
  // template (Instagram/Messenger) não sobra nada e o compositor fica bloqueado
  // até o cliente escrever de novo.
  //
  // ⚠️ NÃO reintroduza o `templatesSupported &&` que existia aqui: ele amarrava o
  // bloqueio a "o canal tem template", então Instagram e Messenger — os únicos em
  // que a janela fecha SEM saída — nunca bloqueavam nada. O operador digitava,
  // mandava, e só a Meta recusava, virando bolha "falhou" sem explicação.
  const templatesSupported = !sandbox && !!(contact && contact.templates_supported);
  const sessionClosed = !sandbox && !!contact && contact.session_open === false;
  // aiWindowClosed → a janela da IA deste canal (capability `ai_window_hours`)
  // fechou: o plugin do canal cala o turno da IA e qualquer instrução escrita na
  // nota privada seria descartada em silêncio. É uma janela DIFERENTE de
  // `sessionClosed`: no Messenger/Instagram com a tag HUMAN_AGENT ligada o
  // compositor do atendente segue aberto por 7 dias enquanto esta já fechou às
  // 24h — foi exatamente esse intervalo que fazia o operador instruir a IA e
  // nada acontecer. Ausente no payload (canal sem restrição, core antigo) ⇒
  // aberta, e o compositor fica como sempre foi.
  const aiWindowClosed = !sandbox && !!contact && contact.ai_window_open === false;
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
    api: _api, phone, conversationId, channelId, sandbox, sessionClosed,
    aiWindowClosed, currentUser,
    setContactData, updateMsgByLocalId,
    updateMenus: (el, val) => autocompleteRef.current && autocompleteRef.current.updateMenus(el, val),
    closeMentionMenu: () => autocompleteRef.current && autocompleteRef.current.setMentionMenu(null),
    closeMenus: () => {
      const a = autocompleteRef.current;
      if (!a) return;
      a.setMentionMenu(null);
      a.setQuickReplyMenu(null);
    },
    menusOpen: () => {
      const a = autocompleteRef.current;
      return !!(a && (a.mentionMenu || a.quickReplyMenu));
    },
    collectMentions: (text) => autocompleteRef.current
      ? autocompleteRef.current.collectMentions(text) : { mentions: [], mention_inbox: false },
    resetMentions: () => autocompleteRef.current && autocompleteRef.current.resetMentions(),
    openTemplatePicker,
  });

  // Plano 99 — toda saída confirmada com a janela no passado volta ao fim.
  // O ACK do POST vem ANTES do GET da ponta recente; dispará-los em paralelo
  // deixava o GET vencer a gravação e a mensagem recém-enviada sumir até o WS/F5.
  // Uma promise compartilhada coalesce confirmações simultâneas (lote/template/
  // texto) em UMA transição autoritativa.
  const hasMoreNewerRef = useRef(hasMoreNewer);
  hasMoreNewerRef.current = hasMoreNewer;
  const outputTransitionRef = useRef(null);
  const finishSuccessfulOutput = useCallback((sent) => transitionAfterOutput(
    sent, hasMoreNewerRef.current, onBackToBottom, outputTransitionRef,
  ), [onBackToBottom]);

  const composerSend = composer.handleSend;
  const composerRetry = composer.handleRetry;
  const handleRetryGuarded = useCallback(async (localId, text) =>
    finishSuccessfulOutput(await composerRetry(localId, text)),
  [composerRetry, finishSuccessfulOutput]);

  // Plano 124 · F8 — depois de colar/arrastar/escolher um arquivo, o foco volta
  // ao compositor para o `Enter` enviar sem clique intermediário. Dos 5 caminhos
  // que enchem a bandeja, só o Ctrl+V com o cursor JÁ no campo deixava o foco em
  // lugar útil; os outros quatro (colar com foco fora, arrastar na conversa,
  // soltar numa linha da sidebar, menu de anexo) largavam o foco no `body`.
  //
  // Duas guardas: (1) nunca sequestrar foco de OUTRO campo de texto — arrastar um
  // arquivo enquanto se digita na busca da conversa não pode mover o cursor do
  // operador (mesma regra do listener de colar, logo abaixo); (2) `preventScroll`
  // + `setTimeout(0)` porque focar a textarea arrasta o scrollport e a bandeja
  // ainda vai mudar de altura — pior com a janela ancorada do plano 99.
  const composerInputRef = composer.inputRef;
  const focusComposer = useCallback(() => {
    setTimeout(() => {
      const el = document.activeElement;
      const tag = el && el.tagName;
      // ⚠️ `type="file"` fica de FORA da guarda: os seletores de arquivo do menu
      // de anexo são `<input type="file">` escondidos, e vários navegadores
      // devolvem o foco a eles quando o diálogo fecha. Tratá-los como "campo de
      // texto do operador" desligaria o foco justamente no caminho que a F8
      // existe para consertar.
      const typing = (tag === 'TEXTAREA'
        || (tag === 'INPUT' && el.type !== 'file')
        || (el && el.isContentEditable));
      if (typing && el !== composerInputRef.current) return;
      composerInputRef.current?.focus({ preventScroll: true });
    }, 0);
  }, [composerInputRef]);

  const media = useMediaUpload({
    api: _api, phone, conversationId, channelId, sandbox, sessionClosed, currentUser,
    // O `&& !aiWindowClosed` espelha o gate do useComposer: o estado do toggle
    // sobrevive à troca de conversa, então sem ele um áudio privado gravado numa
    // conversa com a janela da IA fechada ainda pediria um turno que o filtro do
    // plugin descarta calado.
    mode: composer.mode, aiReadPrivate: composer.aiReadPrivate && !aiWindowClosed,
    aiReplyInChat: composer.aiReadPrivate ? composer.aiReplyInChat : true,
    setContactData, updateMsgByLocalId, openTemplatePicker, mediaLimits,
    onSent: () => finishSuccessfulOutput(true),
    onQueued: focusComposer,
    // Menções internas escritas na legenda de uma NOTA PRIVADA (@atendente /
    // @time). Só a nota privada as aceita — ver `useMediaUpload`.
    collectMentions: (text) => autocompleteRef.current
      ? autocompleteRef.current.collectMentions(text) : { mentions: [], mention_inbox: false },
    resetMentions: () => autocompleteRef.current && autocompleteRef.current.resetMentions(),
  });

  // Depois do `media` de propósito: o menu de @menção precisa saber se há anexo
  // na bandeja (o texto vira legenda, e legenda para o CLIENTE não leva menção).
  // Os dois hooks continuam desacoplados — quem os liga é o `autocompleteRef`,
  // lido só na hora da chamada.
  const autocomplete = useTokenAutocomplete({
    phone, sandbox, contact, groupParticipantsChanged, mode: composer.mode,
    input: composer.input, setInput: composer.setInput, inputRef: composer.inputRef,
    mentionsUnsupported: media.pendingQueue.length > 0 && composer.mode !== 'private',
  });
  autocompleteRef.current = autocomplete;

  // ── O gesto de ENVIAR (plano 124) ────────────────────────────────
  // Um só ponto de decisão para o botão e para a tecla Enter: a regra pura mora
  // em `services/composerSubmit.js`, aqui fica só a execução. Com anexo na
  // bandeja, o texto do compositor é a LEGENDA do lote — por isso ele é
  // consumido (estado + rascunho) ANTES de disparar o envio, do mesmo jeito que
  // `handleSend` faz com uma mensagem de texto.
  const handleSendGuarded = useCallback(async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    const plan = submitPlan({
      text: composer.input,
      queueLength: media.pendingQueue.length,
      queueIsAudioOnly: isAudioOnly(media.pendingQueue),
      mode: composer.mode,
      sessionClosed,
      sending: media.sending,
    });

    if (plan.action === 'noop') return false;

    // Fora da janela de 24h nada é consumido: o operador ainda vai precisar do
    // texto e dos anexos depois de escolher (ou desistir do) template.
    if (plan.action === 'template') { openTemplatePicker(); return false; }

    if (plan.action === 'text') return finishSuccessfulOutput(await composerSend(e));

    // A partir daqui há anexo na bandeja. `text_then_media` é o caso do áudio,
    // que não aceita legenda: o texto vai como mensagem PRÓPRIA, antes do clipe.
    if (plan.action === 'text_then_media') {
      const sentText = await composerSend(e);
      if (!sentText) return finishSuccessfulOutput(false);  // texto falhou: o áudio fica na bandeja
      const okAudio = await media.confirmQueue('');
      focusComposer();
      return okAudio;
    }

    composer.consumeInput();
    composer.stopPresence();
    // Mesma colapsagem de marcação do envio de texto: o compositor mostra
    // **negrito** com realce, então a legenda tem de chegar no formato de fio do
    // WhatsApp (*negrito*) — senão o mesmo texto sairia diferente só por ter
    // anexo junto.
    // `confirmQueue` já chama `onSent` → `finishSuccessfulOutput(true)`.
    const okMedia = await media.confirmQueue(toWhatsAppMarkup(plan.caption));
    // F8 item 4: quem manda uma imagem também continua digitando em seguida —
    // só o caminho de TEXTO devolvia o foco (`useComposer`).
    focusComposer();
    return okMedia;
  }, [composer, media, sessionClosed, composerSend, finishSuccessfulOutput,
      openTemplatePicker, focusComposer]);

  const composerUi = { ...composer, handleSend: handleSendGuarded,
                       handleRetry: handleRetryGuarded };

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

  // Colar com o foco FORA do compositor (plano 124 · F6). A `<textarea>` já
  // escuta `onPaste`; isto cobre o resto da conversa — Ctrl+V depois de rolar o
  // histórico, sem ter clicado no campo, exatamente como o arrastar já funciona.
  // Mesmas condições de desligamento do `useDropZone`, mais uma: nunca sequestrar
  // um colar destinado a OUTRO campo de texto (busca na conversa, editar
  // mensagem, filtros, tela de plugin) — lá o colar é do usuário, não nosso.
  const pasteDisabled = !canReply || media.sending
    || (sessionClosed && composer.mode !== 'private');
  useEffect(() => {
    if (pasteDisabled) return;
    function onDocPaste(e) {
      const el = e.target;
      const tag = el && el.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (el && el.isContentEditable)) return;
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      const files = [];
      for (const item of items) {
        if (item.kind !== 'file') continue;
        const file = item.getAsFile();
        if (file) files.push(file);
      }
      if (!files.length) return;
      e.preventDefault();
      media.requestFilesDrop(files, 'media');
    }
    document.addEventListener('paste', onDocPaste);
    return () => document.removeEventListener('paste', onDocPaste);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pasteDisabled, phone, media.requestFilesDrop]);

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
  // Plano 99 F0e — para QUAL alvo já pedimos a janela ancorada. É o que impede o
  // laço: se a janela voltou e o alvo continua ausente (mensagem apagada, id de
  // outra conversa), pedir de novo daria o mesmo resultado para sempre.
  const requestedJumpRef = useRef(null);
  useEffect(() => {
    pendingScrollRef.current = scrollToMsg != null ? String(scrollToMsg) : null;
    if (scrollToMsg == null) requestedJumpRef.current = null;
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

  // Ir para uma mensagem — o ponto de entrada de QUEM CLICA (hoje, a citação de
  // uma resposta). Foca se estiver na janela; se não, pede a janela ancorada nela
  // (plano 99 F0e·4) em vez de desistir. É o mesmo mecanismo do salto vindo da
  // busca global, do deep-link e da busca dentro da conversa — quatro caminhos,
  // um comportamento.
  const goToMessage = useCallback((mid, opts = {}) => {
    if (focusMessage(mid, opts)) return true;
    if (!onJumpToMessage) return false;
    pendingScrollRef.current = String(mid);
    requestedJumpRef.current = String(mid);
    onJumpToMessage(mid);
    return true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onJumpToMessage]);

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
    // useReverseInfiniteScroll (useLayoutEffect, antes do paint) — não rolamos pro
    // fim nessa atualização (senão a viewport saltaria).
    //
    // ⚠️ plano 99 F0e·2: antes esta flag dava `return` e engolia TAMBÉM a tentativa
    // de foco. Era o segundo tempo do salto silencioso: a atualização em que o alvo
    // finalmente chegava era justamente uma atualização de prepend, então o foco
    // nunca era tentado. A flag agora suprime só o auto-scroll para o fim, que é a
    // única coisa que ela tem a ver.
    const wasPrepend = justPrependedRef.current;
    if (wasPrepend) justPrependedRef.current = false;
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
      if (tMsg && isCollapsibleCard(tMsg.role, tMsg.content) && !expandedCards.has(wantKey)) {
        focusAfterExpandRef.current = target;
        setExpandedCards((prev) => new Set(prev).add(wantKey));
      }
      // Plano 99 F0e·1 — o alvo fora da janela deixou de ser beco sem saída. A
      // decisão ("focar / pedir a janela ancorada / desistir avisando") vive no
      // módulo puro `services/threadJump.js`; aqui só se executa.
      const plan = planJump({
        target,
        rendered: isRendered(messages, target),
        requested: requestedJumpRef.current === target,
        inFlight: jumping,
      });
      if (plan.action === 'focus' && focusMessage(target)) {
        pendingScrollRef.current = null;
        requestedJumpRef.current = null;
        if (onScrolledToMsg) onScrolledToMsg();
      } else if (plan.action === 'fetch' && onJumpToMessage) {
        // Pede a janela CENTRADA no alvo em vez de esperar a cascata de
        // "carregar anteriores" — que, se o alvo estivesse na última página,
        // nunca chegava (era o bug de produção da §2.4 do plano).
        requestedJumpRef.current = target;
        onJumpToMessage(target);
      } else if (plan.action === 'give_up') {
        // Mensagem apagada, id de outra conversa, permalink velho. Avisar é o
        // mínimo — o comportamento antigo era ficar mudo para sempre.
        pendingScrollRef.current = null;
        requestedJumpRef.current = null;
        if (onScrolledToMsg) onScrolledToMsg();
        notify('Não foi possível localizar essa mensagem nesta conversa.', { kind: 'error' });
      }
      // Tratado, pedido ou aguardando — em nenhum caso caímos no scroll pro fim.
      return;
    }
    // plano 99 F0d·6: com a janela ANCORADA no passado, rolar pro fim jogaria o
    // operador para o fim de uma janela que não é o fim da conversa — e desfaria
    // o salto que ele acabou de pedir.
    if (wasPrepend || hasMoreNewer) return;
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
    // `jumping` entra nas deps para que a virada "em voo → chegou" reavalie o
    // plano por si só. Sem isso, uma janela ancorada que voltasse SEM mudar a
    // lista de mensagens deixaria o pedido pendurado para sempre.
  }, [messages, jumping]);

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
    let out = formatWhatsApp(text, names);
    // Plano 99 F2·4 — o flash amarelo diz em QUAL mensagem se aterrissou; isto
    // diz ONDE dentro dela, que é o que resolve numa mensagem longa. Só entra
    // com o modo busca aberto: sem termo, `highlightHtml` devolve a MESMA
    // string e o render do chat fica byte-idêntico ao de sempre.
    if (searchOpen && searchTerm) out = highlightHtml(out, searchTerm);
    // Em modo de seleção em lote o clique tem UM significado só: marcar a
    // mensagem. Sem isto, clicar numa âncora marcaria a mensagem E abriria uma
    // aba (a navegação é o default do <a>, que o onClick do container não
    // cancela). Já valia para URL antes do plano 97; com e-mail e telefone
    // linkificados, ficaria fácil de esbarrar. `pointer-events:none` entra no
    // COMEÇO do style para não depender da ordem dos atributos.
    return actions.selectionMode
      ? out.replace(/(<a\b[^>]*\bstyle=")/g, '$1pointer-events:none;')
      : out;
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
      handleSendGuarded(e);
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
  function buildBaseItems(message, isFromMe, ctx = {}) {
    // Bloco CONTEXTUAL (plano 97): só existe quando o botão direito caiu em cima
    // de uma entidade ou havia texto selecionado. Vem ANTES dos itens da
    // mensagem, separado por uma divisória.
    const entityItems = entityMenuItems(ctx.entity || null);
    const selectionText = ctx.selectionText || '';
    return [
      ...(entityItems.length ? [...entityItems, { separator: true }] : []),
      ...(selectionText ? [
        { label: 'Copiar seleção', icon: CopyIcon,
          onClick: () => { copyToClipboard(selectionText); notify('Seleção copiada.', { kind: 'success' }); } },
        { separator: true },
      ] : []),
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
    // Contexto do CLIQUE (plano 97 · F3) — lido AGORA, síncrono: depois do
    // `await` abaixo o evento nativo já perdeu o `currentTarget`, e o clique que
    // fecha o menu desfaria a seleção. Pela setinha de hover, o alvo é o botão
    // (fora de qualquer entidade) → menu idêntico ao de sempre.
    const entity = entityUnderCursor(e);
    const selectionText = selectionInside(e);
    const base = buildBaseItems(message, isFromMe, { entity, selectionText });
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
      <!-- Header. Em modo busca (plano 99 F2·1) ele dá lugar à barra de busca em
           vez de espremer mais um controle: o header tem largura fixa e já
           carrega nome, selo de canal, etiquetas e duas fileiras de ações. Os
           pontos de extensão de plugin ficam ABAIXO daqui e não são afetados. -->
      ${searchOpen ? html`
        <${ConversationSearchBar}
          tall=${showChannel}
          conversationId=${conversationId}
          term=${searchTerm}
          onTermChange=${setSearchTerm}
          onJump=${(id) => onJumpToMessage && onJumpToMessage(id)}
          onPickDate=${handlePickDate}
          onBackToBottom=${onBackToBottom}
          onClose=${closeSearch}
          refTs=${messages && messages.length ? messages[messages.length - 1].ts : null}
        />` : html`
      <!-- Altura: o selo do canal ocupa uma linha PRÓPRIA acima do nome (3 linhas),
           então a barra precisa de um pouco mais de altura que as outras do painel.
           Sem o selo (instalação com um único canal) ela continua nos 59px de
           sempre — e a barra de busca, que a SUBSTITUI, recebe o mesmo veredito
           pela prop "tall" para abrir/fechar a busca não deslocar a conversa.
           (Sem crase neste comentário: ele mora DENTRO do template html.) -->
      <div class="${showChannel ? 'h-[68px]' : 'h-[59px]'} flex items-center pl-4 ${gearFloating ? 'pr-[56px]' : 'pr-2'} bg-wa-panel border-b border-wa-border shrink-0">
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
          <!-- Canal do atendimento, ACIMA do nome — o MESMO selo da linha da barra
               lateral, gateado pelo mesmo showChannel (só com 2+ canais
               instalados), para as duas telas nunca discordarem sobre mostrar ou
               não. Fica numa linha própria (e não ao lado do nome, como era até
               aqui) porque o nome disputava espaço horizontal com ele e com as
               etiquetas, e era o nome que truncava. -->
          ${showChannel ? html`
            <div class="flex items-center leading-none mb-[2px]">
              <${ChannelChip} provider=${channelProvider} name=${channelName} margin=${false} />
            </div>` : null}
          <!-- Só o nome: as etiquetas da conversa NÃO são mostradas aqui (elas
               continuam na linha da barra lateral, no painel do contato e no menu
               de contexto). Com muitas tags o nome era espremido e a barra virava
               uma fileira de chips. -->
          <div class="text-wa-text text-[16px] leading-tight truncate flex items-center gap-[6px]">
            <span class=${'truncate' + (isAutoName ? ' underline decoration-1 underline-offset-2' : '')} title=${isAutoName ? 'Nome obtido do WhatsApp (ainda não renomeado)' : null}>${displayName}</span>
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

        <!-- Pesquisar nesta conversa (plano 99 F2). Escondido sem atendimento
             (a busca é POR conversa) e no sandbox. -->
        ${canSearchThread ? html`
          <button
            type="button"
            onClick=${() => setSearchOpen(true)}
            class="shrink-0 ml-1 text-wa-icon hover:text-wa-text p-[6px] rounded-full hover:bg-wa-hover transition-colors"
            title="Pesquisar nesta conversa"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
            </svg>
          </button>
        ` : null}

        <!-- Ações do atendimento (FF3): "Resolver" na barra + o menu (⋮) do canto,
             que reúne as informações do atendimento e o que os plugins injetam. -->
        <${ConversationHeaderActions} phone=${phone} conversationId=${conversationId} sandbox=${sandbox} onOpenConversationInfo=${onOpenConversationInfo} onOpenContactInfo=${onAvatarClick} contactInfo=${info} />
      </div>`}

      <!-- Plugin extension point: banner abaixo do header / acima das mensagens
           (faixa "atendimento atual" — SLA, aviso, etc.). Empty by default. -->
      <${Slot} name="chat.header.banner" ctx=${{ conv: { conversationId, phone, channelId }, conversationId, phone, channelId, contact }} />

      <!-- Pílula de data FIXA (plano 98) — o dia do trecho que está no topo, sempre
           visível, sem precisar rolar atrás do separador inline.
           Mesmo padrão do balão "Fulano está digitando" logo abaixo: container de
           altura ZERO + filho absoluto → flutua sobre as mensagens sem empurrar a
           rolagem. Fica DEPOIS do slot chat.header.banner e antes do container de
           rolagem, então uma faixa injetada por plugin a empurra junto em vez de
           cobri-la. A caixa de recorte (h-48px + overflow-hidden) apara a pílula
           enquanto ela desliza para fora no "empurrão" do separador do dia seguinte —
           sem ela, a pílula invadiria o header.
           Cala enquanto o "Carregando anteriores…" está em tela: os dois ocupam o
           MESMO ponto (centro, topo) e a pílula cobriria o indicador por completo —
           e ali, no topo do histórico, o dia é o que menos importa. -->
      ${chatDay.label && !loadingOlder ? html`
        <div class="relative h-0 z-10 pointer-events-none">
          <div class="absolute top-0 left-0 right-0 h-[48px] overflow-hidden">
            <div
              class="absolute top-[8px] left-1/2 bg-wa-bg text-wa-secondary text-[12px] font-medium uppercase
                     tracking-wide rounded-[7.5px] px-[12px] py-[5px] shadow-md whitespace-nowrap
                     transition-opacity duration-150"
              style=${`transform: translate(-50%, ${chatDay.offsetY.toFixed(1)}px); opacity: ${
                Math.max(0, 1 + chatDay.offsetY / PILL_TRAVEL).toFixed(3)};`}
            >${chatDay.label}</div>
          </div>
        </div>` : null}

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
              // `data-day` (plano 98 F2a): torna o separador localizável pela medição da
              // pílula flutuante de data. Só um atributo — layout inalterado.
              const dateSeparator = showDateSep
                ? html`<div key=${`sep-${m.ts}-${i}`} data-day=${formatDateSeparator(m.ts)} class="flex justify-center my-[12px]">
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
                // Plano 63 F4: transcription/tool_call (e nota privada LONGA, ex.: a
                // instrução que um plugin de automação registra) are collapsed unless
                // the user expanded THIS card. Derived in render (no effect) so there's
                // no flash/jump on open (G5); the card is controlled (G1).
                const collapsed = isCollapsibleCard(m.role, m.content) && !expandedCards.has(stateKey);
                return [dateSeparator, html`<${SystemMessageCard}
                  key=${cardKey} message=${m} index=${i} fmt=${fmt} openMsgMenu=${openMsgMenu}
                  showAgentName=${showAgentName}
                  collapsed=${collapsed} onToggleCollapse=${() => toggleCard(stateKey)} />`];
              }

              return [dateSeparator, html`<${MessageBubble}
                key=${m._localId || i} message=${m} index=${i} isFirst=${isFirst}
                isGroup=${isGroup} sandbox=${sandbox} displayName=${displayName} fmt=${fmt}
                findQuoted=${findQuoted} quotedInfo=${quotedInfo} focusMessage=${goToMessage}
                canJumpOutsideWindow=${!!onJumpToMessage}
                openMsgMenu=${openMsgMenu} myReaction=${myReaction} handleRetry=${canReply ? composerUi.handleRetry : null}
                showAgentName=${showAgentName}
                selectionMode=${actions.selectionMode}
                selected=${actions.selectionMode && actions.selection.has(selectionKey(m))}
                onToggleSelect=${actions.toggleSelect} />`];
            })
        }
        <!-- Sentinela de BAIXO (plano 99 F0d): só existe quando a janela está
             ancorada no passado — dispara "carregar seguintes" ao rolar até aqui.
             Na conversa normal (que termina na última mensagem) nem é montado, e
             a área de mensagens fica byte-idêntica à de antes. -->
        ${hasMoreNewer ? html`
          <div ref=${bottomSentinelRef} class="flex justify-center py-2 min-h-[8px]">
            ${loadingNewer
              ? html`<span class="bg-wa-bg/80 text-wa-secondary rounded-lg px-3 py-1.5 text-[12px] shadow-sm">Carregando seguintes…</span>`
              : null}
          </div>` : null}
      </div>

      <!-- "Voltar ao fim" (plano 99 F0d·5) — só com a janela ANCORADA. Mesmo
           padrão visual do chip de digitação logo abaixo: container de altura
           zero + filho absoluto, para flutuar sobre o fim da conversa sem
           empurrar a rolagem nem o compositor.
           O contador (P5) importa: enquanto o operador lê o passado, a mensagem
           nova NÃO é anexada (criaria um buraco no histórico), então sem este
           número ele não teria como saber que a conversa andou. -->
      ${hasMoreNewer && onBackToBottom ? html`
        <div class="relative h-0 z-20">
          <div class="absolute bottom-[8px] right-[16px]">
            <button
              type="button"
              onClick=${onBackToBottom}
              title="Voltar para o fim da conversa"
              class="flex items-center gap-1.5 bg-wa-panel border border-wa-border text-wa-text
                     rounded-full shadow-md pl-3 pr-2 py-[6px] text-[13px] hover:bg-wa-hover transition-colors"
            >
              <span>Voltar ao fim</span>
              ${newWhileAnchored > 0 ? html`
                <span class="bg-wa-teal text-white text-[11px] font-semibold rounded-full px-[6px] py-[1px] leading-[15px]">
                  ${newWhileAnchored} ${newWhileAnchored === 1 ? 'nova' : 'novas'}
                </span>` : null}
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M7.41 8.59L12 13.17l4.58-4.58L18 10l-6 6-6-6z"/></svg>
            </button>
          </div>
        </div>` : null}

      <!-- Balão flutuante "Fulano está digitando" (multi-operador, estilo Chatwoot).
           Só aparece com OUTRO atendente digitando nesta conversa — o estado já vem
           sem o próprio usuário logado. Container de altura zero + posição absoluta:
           flutua sobre o fim da conversa sem empurrar a rolagem nem o compositor. -->
      ${operatorTyping ? html`
        <div class="relative h-0 z-10 pointer-events-none">
          <div class="absolute bottom-[8px] left-1/2 -translate-x-1/2 max-w-[70%] flex items-center gap-2
                      bg-wa-panel border border-wa-border rounded-full shadow-md px-3 py-[6px]">
            <span class="text-[13px] text-wa-secondary truncate">
              <span class="text-wa-text font-medium">${operatorTyping.name}</span>${' está digitando'}
            </span>
            <${TypingDots} />
          </div>
        </div>` : null}

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
        aiWindowClosed=${aiWindowClosed}
        composer=${composerUi} autocomplete=${autocomplete} media=${media} audio=${audio}
        quotedInfo=${quotedInfo} openTemplatePicker=${openTemplatePicker} handleKeyDown=${handleKeyDown} currentUser=${currentUser} />
      ` : html`
      <div class="px-[4%] lg:px-[7%] py-3 bg-wa-panel border-t border-wa-border shrink-0 text-center text-wa-secondary text-[13px]">
        Somente leitura — você não tem permissão para responder nesta conversa.
      </div>
      `}

      ${showTemplatePicker ? html`
        <${TemplatePickerHost}
          conversationId=${conversationId}
          channelId=${channelId}
          phone=${phone}
          onClose=${() => setShowTemplatePicker(false)}
          onSent=${() => {
            setShowTemplatePicker(false);
            return finishSuccessfulOutput(true);
          }}
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
