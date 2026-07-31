import { h } from 'preact';
import { useState, useRef, useCallback, useEffect, useMemo } from 'preact/hooks';
import htm from 'htm';
import { useUrlState } from '../../hooks/useUrlState.js';
import { readParams, writeParams } from '../../services/urlState.js';
import {
  hasStoredUser, defaultAssignmentTab, buildHubUrlSchema, hubUrlHasParams,
  shouldYieldAssignmentTab,
} from '../../services/hubDefaults.js';
import { ContactList, typingKey, rowKeyFor, operatorTypingFor } from './ContactList.js';
import { ContactDetail } from './ContactDetail.js';
import { ContactInfoPanel } from './ContactInfoPanel.js';
import { ConversationInfoPanel } from './ConversationInfoPanel.js';
import { ContextMenu } from './ContextMenu.js';
import { ChannelPickerModal } from './ChannelPickerModal.js';
import { NewConversationModal } from './NewConversationModal.js';
import { useSidebarResize } from './hooks/useSidebarResize.js';
import { useConversationList } from './hooks/useConversationList.js';
import { useConversationSelection, threadKeyOf } from './hooks/useConversationSelection.js';
import { useConversationFilters } from './hooks/useConversationFilters.js';
import { useConversationActions } from './hooks/useConversationActions.js';
import { useBulkSelection } from './hooks/useBulkSelection.js';
import { useChannelPicker } from './hooks/useChannelPicker.js';
import { useConversationWsEvents } from './hooks/useConversationWsEvents.js';
import { hasPermission } from '../../utils/permissions.js';

const html = htm.bind(h);

// Deep-link do estado da lista (Plano 24) — filtros/busca/ordenação/painel na
// query-string legível. `adv` guarda só as cláusulas do filtro avançado (JSON),
// omitido quando vazio. Serialize omite tudo que está no default → URL limpa.
// O schema virou fábrica em services/hubDefaults.js (plano 88 · F1/F2): o default
// de `assignment` depende de haver usuário logado.

// ── Main Component (conversation hub container) ──────────────────────
//
// Plano 23 · D2: this used to be a 1800-line god-component. Decomposed into the
// `services/conversationRows.js` pure module (row build + filter matching) and
// the `hooks/use*` cohesive hooks below. The container only wires the hooks
// together (in dependency order so every closure captures stable references) and
// renders — it adds NO new behavior.
//
// ROUTE-OVERRIDE BOUNDARY (Q5, preserved): the `atendimentos` plugin claims the
// 'attendances' route via registry.overrideRoute, which is EXCLUSIVE/REPLACE
// semantics (NOT compose) — it swaps the WHOLE rendered component for that tab.
// This decomposition is purely INTERNAL to <Contacts/>: its export name + props
// are unchanged and the hooks/services are not coupled to the router, so the
// attendances claim keeps replacing the chat exactly as before. The D1 slots
// (`sidebar.row.badges` in ContactList, `chat.header.banner` in ContactDetail)
// and the `ui.conversation.selected` emit (in useConversationSelection) are the
// additive seams that keep the attendances flow composing on top.
export function Contacts({ newMessage, chatPresence, aiTyping, contactInfoUpdated, tagsChanged, contactTagsUpdated, contactAiToggled, messagesRead, messageStatus, messageAction, messageReaction, avatarUpdated, groupParticipantsChanged, conversationCreated, initialContactId, initialConversationId, initialScrollMsgId = null, wsConnected, config, onConfigSave, onUnreadChange }) {
  // Refs shared between hooks (owned here so a single instance is threaded into
  // both the selection loader and the WS handlers — same identity, no drift).
  const pageVisibleRef = useRef(!document.hidden);
  // Canal escolhido no picker para um atendimento Novo ainda sem atendimento naquele
  // canal — consumido (e zerado) pelo loader de detalhe para escopar o getContact
  // ao canal certo (multicanal), em vez de fundir/abrir o atendimento de outro canal.
  const newConvChannelRef = useRef(null);
  // plano 72: espelho fresco da VIEW atual (serverMode + status/aba/tags/avançado +
  // usuário), escrito em render pelo hook de filtros e lido pelos handlers de WS
  // (useCallback([]) que só leem refs) para gatear insert/drop das linhas em serverMode.
  const viewSpecRef = useRef(null);

  // ── Sidebar geometry (self-contained) ──────────────────────────────
  const { sidebarHidden, sidebarWidth, isResizing, isDesktop, startResize } = useSidebarResize();

  // ── Conversation list (rows, search, archived, refs, fetch) ────────
  const list = useConversationList({ onUnreadChange });
  const {
    contacts, setContacts, loading,
    loadingMore, hasMore, loadMore,
    search, setSearch, handleSearchChange,
    showArchived, setShowArchived,
    fetchContacts, sortContacts,
    contactsRef, displayedRef, searchRef, fetchContactsRef, showArchivedRef,
    serverFilterRef,
    showChannel, channelOptions,
  } = list;

  // plano 72 F5 — após um patch OTIMISTA de membership (toggle IA / bulk IA / bulk
  // assign) a linha pode ter saído da view server-filtrada. Em serverMode a lista está
  // em paridade com a WHERE do servidor (a contagem já é server-side), então um refetch
  // server-filtrado reconcilia a membership. Fora de serverMode é no-op — o
  // `displayedContacts` client-side já reavalia a aba sobre as linhas patchadas.
  // `serverFilterRef` já foi sincronizado em render, então `fetchContacts` lê os params
  // certos. Chamado DEPOIS dos awaits do POST, logo o backend já commitou a mudança.
  const reconcileAfterMembershipChange = useCallback(() => {
    if (viewSpecRef.current && viewSpecRef.current.serverMode) {
      fetchContacts(searchRef.current);
    }
  }, [fetchContacts, searchRef]);

  // plano 72 F8 — ao LER uma menção (abrir a conversa) na aba Menções em serverMode, a
  // conversa deixa a view server-filtrada (has_mention→false), mas o clear otimista do
  // badge só zera o flag sem remover a linha (lista N vs badge N-1). Ler a menção NÃO
  // emite WS, então nada auto-cura → um refetch server-filtrado reconcilia a lista com a
  // contagem. Escopado à aba Menções (specNeedsServer): no-op em qualquer outra view, p/
  // não refazer a lista a cada conversa aberta. Passado ao selection-hook (site primário
  // de leitura, que não tem acesso ao scheduleListRefetch do ws-hook).
  const reconcileMentionsOnRead = useCallback(() => {
    const vs = viewSpecRef.current;
    if (vs && vs.serverMode && vs.assignmentTab === 'mentions') fetchContacts(searchRef.current);
  }, [fetchContacts, searchRef]);

  // ── Selection / open thread / detail load ──────────────────────────
  const selection = useConversationSelection({
    contacts, loading, setContacts, contactsRef,
    pageVisibleRef, newConvChannelRef,
    initialContactId, initialConversationId, initialScrollMsgId,
    // plano 72 F8: refetch a aba Menções (serverMode) ao ler a menção da conversa aberta.
    reconcileMentionsOnRead,
  });
  const {
    selected, setSelected,
    selectedConvId, setSelectedConvId,
    selectedChannelId,
    scrollToMsg, setScrollToMsg,
    contactData, setContactData,
    loadingDetail,
    detailError, retryDetail,      // plano 85 A3
    loadingOlder, loadOlder,
    // plano 99 — janela ancorada (rolar para os dois lados) + os caminhos de salto
    loadingNewer, loadNewer, jumping,
    jumpToMessage, jumpToDate, backToBottom,
    openPanel, setOpenPanel,
    selectedRef, selectedConvIdRef, selectedChannelIdRef,
    anchoredWindowRef,
    openInfoAfterSelect,
    pendingWsMessages,
    isOpenRow, selectContact, reloadOpenThread,
  } = selection;

  // ── Contact/conversation actions + identity + tags + context menu ──
  const actions = useConversationActions({
    setContacts, sortContacts, setContactData,
    setSelected, setSelectedConvId, selectedRef, selectedConvIdRef,
    // plano 72 F5: refetch server-filtrado após toggle IA otimista (reconcilia membership).
    reconcileAfterMembershipChange,
  });
  const {
    globalTags, setGlobalTags,
    currentUserId, currentUser, users, agentsUsers, agentsAi,
    ctxMenu, setCtxMenu, ctxConv,
    handleToggleAI, handleMarkUnread, handleMarkRead,
    handleArchive, handleDelete, handleDeleteConversation, handlePin,
    handleAssignConversation, handleAssignAgent, handleResolveConversation,
    handleCreateTag, applyTagResults, resolveAssignee,
  } = actions;

  // ── Bulk selection mode ─────────────────────────────────────────────
  const bulk = useBulkSelection({
    contactsRef, displayedRef, showArchivedRef,
    setContacts, sortContacts, setContactData,
    setSelected, setSelectedConvId, selectedRef, selectedConvIdRef, applyTagResults,
    // plano 72 F5: refetch server-filtrado após bulk IA / bulk assign otimistas.
    reconcileAfterMembershipChange,
  });
  const {
    selectionMode, setSelectionMode, selectedKeys, setSelectedKeys,
    enterSelection, exitSelection, toggleSelect, selectAllContacts, clearSelection, deselectAll,
    handleBulkAI, handleBulkArchive, handleBulkTag, handleBulkRemoveAllTags,
    handleBulkPin, handleBulkMarkRead, handleBulkMarkUnread, handleBulkAssign,
  } = bulk;

  // Archive toggle (deselect + drop selection mode), split from the list hook's
  // own [showArchived] reload so no hook reaches into another's setters. The list
  // hook's [showArchived] effect (created earlier) refetches first; these run next.
  const handleToggleArchived = useCallback(() => {
    setShowArchived(prev => !prev);
    setSelected(null);
    setSelectedConvId(null);
  }, [setShowArchived, setSelected, setSelectedConvId]);
  useEffect(() => { setSelectionMode(false); setSelectedKeys([]); }, [showArchived]);

  // Esc fecha, em camadas, o que estiver aberto no hub: primeiro o painel lateral
  // (contato / atendimento), depois a própria conversa — volta ao estado "nenhuma
  // conversa selecionada" (URL "/", só a sidebar + placeholder). Guardas:
  // `defaultPrevented` respeita quem já consumiu a tecla (menus de autocomplete do
  // compositor, prévia de mídia), e um overlay `.fixed.inset-0` no DOM significa
  // modal aberto — ele tem o próprio Esc e tem precedência.
  useEffect(() => {
    function onKey(e) {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (document.querySelector('.fixed.inset-0')) return;
      if (openPanel) { setOpenPanel(null); return; }
      if (selectedRef.current == null && selectedConvIdRef.current == null) return;
      selectContact(null);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [openPanel, setOpenPanel, selectContact, selectedRef, selectedConvIdRef]);

  // ── Filters + saved presets + derived sidebar list ──────────────────
  // plano 88 · F2 — a aba que o hub abre quando a URL não diz nada. Trocar de tela
  // DESMONTA o hub e voltar é um pushState('/') sem query, então "no mount" é
  // exatamente "toda vez que o operador volta de outra tela": o default precisa ser
  // "Minhas" (degradando para "Todas" sem usuário logado — ver hubDefaults.js).
  // Deps VAZIAS de propósito: a identidade é resolvida UMA vez por mount; um schema
  // que mudasse no meio da vida do componente reescreveria a URL sem ninguém ter
  // tocado em nada. Um só cálculo, compartilhado com o seed do hook de filtros (F3).
  const defaultTab = useMemo(() => defaultAssignmentTab(hasStoredUser()), []);
  const hubSchema = useMemo(() => buildHubUrlSchema(defaultTab), [defaultTab]);
  // Precedência (Plano 24 · D3): se a URL trouxer filtros, ela vence o preset
  // salvo no localStorage — o hook não auto-aplica o preset armazenado nesse caso.
  const hasUrlFilters = useMemo(() => hubUrlHasParams(window.location.search, hubSchema), [hubSchema]);
  const filters = useConversationFilters({
    contacts, selected, selectedConvId, currentUserId, displayedRef,
    search,
    // plano 88 · F3 — o MESMO valor do schema, para o 1º frame já nascer na aba certa
    // (sem piscar "Todas" e sem disparar um fetch de lista que seria refeito).
    defaultAssignmentTab: defaultTab,
    searching: !!search,
    showArchived,
    skipStoredPreset: hasUrlFilters,
    // plano 69 F2: a sidebar conversa-first é servida por /api/atendimentos/filter
    // (mesma query da contagem) quando o spec é server-expressável. O hook de filtros
    // escreve os params em `serverFilterRef` e refaz a lista ao mudar um filtro.
    serverFilterRef,
    fetchContacts,
    // plano 72 F2: o hook escreve a view atual aqui (em render) para os handlers de WS
    // reproduzirem a decisão do servidor no insert/drop das linhas em serverMode.
    viewSpecRef,
  });
  const {
    statusFilter, setStatusFilter,
    assignmentTab, setAssignmentTab,
    sortBy, setSortBy,
    tagFilter, setTagFilter,
    advFilters, setAdvFilters,
    savedFilters, activeFilter, anyFilterActive,
    applySavedFilter, saveCurrentFilter, overwriteSavedFilter,
    renameSavedFilter, removeSavedFilter, clearAllFilters,
    tabCounts, displayedContacts,
  } = filters;

  // Deep-link do estado da lista → URL (Plano 24). Hidrata da URL no mount e no
  // back/forward; reflete filtros/busca/ordenação/painel na query (replaceState,
  // sem poluir histórico). Serialize omite defaults → link limpo quando nada mexeu.
  useUrlState({
    read: () => readParams(window.location.search, hubSchema),
    apply: (s) => {
      setStatusFilter(s.status);
      setAssignmentTab(s.assignment);
      setSortBy(s.sort);
      setSearch(s.search);
      setShowArchived(s.archived);
      setTagFilter(s.tags);
      setOpenPanel(s.panel || null);
      // Re-seed clause ids p/ o diálogo avançado editar sem colisão.
      setAdvFilters(Array.isArray(s.adv) ? s.adv.map((f, i) => ({ ...f, id: `u${i}` })) : []);
    },
    serialize: () => writeParams({
      status: statusFilter,
      assignment: assignmentTab,
      sort: sortBy,
      search,
      archived: showArchived,
      tags: tagFilter,
      panel: openPanel || '',
      // Descarta o id efêmero das cláusulas p/ a URL ficar estável.
      adv: (advFilters || []).map(({ id, ...rest }) => rest),
    }, hubSchema),
    deps: [statusFilter, assignmentTab, sortBy, search, showArchived, tagFilter, openPanel, advFilters],
  });

  // ── New-conversation / channel picker ───────────────────────────────
  const picker = useChannelPicker({ selectContact, fetchContacts, setSearch, newConvChannelRef });

  // Arrastar arquivos para uma linha da sidebar (plano 64 · F11). Abre aquela
  // conversa e entrega os arquivos ao painel, que monta a prévia — nada é
  // enviado sem confirmação (P7). O handoff é um estado com `token` para o
  // painel consumir uma vez só (soltar os MESMOS arquivos duas vezes seguidas
  // ainda dispara, porque o token muda).
  const [droppedFiles, setDroppedFiles] = useState(null);
  const dropTokenRef = useRef(0);
  const handleRowDropFiles = useCallback((row, files) => {
    selectContact(row);
    dropTokenRef.current += 1;
    setDroppedFiles({ token: dropTokenRef.current, phone: row.phone, files });
  }, [selectContact]);
  const {
    checkingPhone, checkPhoneError, setCheckPhoneError,
    channelPicker, setChannelPicker,
    showNewConversation, setShowNewConversation,
    handleStartConversation, handlePickChannel, handleNewConversationSent,
  } = picker;

  // ── Real-time WS events (sidebar + open thread patches) ─────────────
  const { typingState, aiRespondingState, operatorTypingState, convAttrPatch } = useConversationWsEvents({
    newMessage, chatPresence, aiTyping, contactInfoUpdated, tagsChanged,
    contactTagsUpdated, contactAiToggled, messagesRead, messageStatus,
    messageAction, messageReaction, avatarUpdated, conversationCreated,
    setContacts, contactsRef, fetchContacts, fetchContactsRef, searchRef, search, sortContacts,
    showArchivedRef,
    setContactData, setSelected, setSelectedConvId,
    selectedRef, selectedConvIdRef, selectedChannelIdRef,
    pendingWsMessages, isOpenRow, selected, contactData,
    anchoredWindowRef,
    setGlobalTags,
    pageVisibleRef,
    reloadOpenThread,
    currentUserId,
    // plano 72 F3/F4: view atual (serverMode + filtros) p/ gatear insert/drop das linhas.
    viewSpecRef,
  });

  // Search box: clear the phone-check error as the operator types (preserves the
  // original handleSearchChange side-effect now that the two states live apart).
  const onSearchChange = useCallback((val) => {
    handleSearchChange(val);
    setCheckPhoneError(null);
  }, [handleSearchChange, setCheckPhoneError]);

  const selectedKey = threadKeyOf(selected, selectedConvId);
  // plano 85 A4 — `contactData` carrega o carimbo (`_threadKey`) da thread de onde veio.
  // Enquanto ele não casar com a seleção corrente os dados são da conversa ANTERIOR:
  // nada deles pode ir para a tela (nem as bolhas, nem o nome/telefone do drawer). É a
  // guarda que fecha o frame entre o clique e o efeito de carga — o efeito roda depois
  // do paint, então sem isto sempre haveria um quadro exibindo a conversa errada.
  const detailStale = selectedKey != null
    && (!contactData || contactData._threadKey !== selectedKey);
  const messages = (contactData && !detailStale) ? contactData.messages || [] : [];
  const info = (contactData && !detailStale) ? contactData.info || {} : {};
  // Linha da conversa aberta: é ela que carrega `channel_provider`/`channel_name`
  // (só a query da LISTA traz o canal — o detalhe do contato não). O cabeçalho do
  // chat mostra o mesmo selo da sidebar a partir daqui, sem requisição nova.
  const selectedRow = selectedKey
    ? (displayedContacts || []).find(c => rowKeyFor(c) === selectedKey) || null
    : null;
  // Rede de segurança: um filtro/busca ativo pode esconder a linha da conversa ABERTA
  // da lista. Aí o canal vem do catálogo de canais (casado por `channelId`), para o
  // selo do cabeçalho não piscar quando o operador mexe nos filtros.
  const selectedChannelOpt = selectedChannelId
    ? (channelOptions || []).find(o => o.id === selectedChannelId) || null
    : null;
  const headerChannelProvider = (selectedRow && selectedRow.channel_provider)
    || (selectedChannelOpt && selectedChannelOpt.provider) || null;
  const headerChannelName = (selectedRow && selectedRow.channel_name)
    || (selectedChannelOpt && selectedChannelOpt.label) || null;

  // plano 88 · F4 — A ABA CEDE: a conversa ABERTA que a aba de atribuição não consegue
  // mostrar derruba a aba para "Todas". UMA regra, dois consumidores (o plano 89 · P1
  // pedia justamente que não virassem duas mitigações parecidas em arquivos diferentes):
  //   • a conversa que o operador acaba de INICIAR — ela nasce sem responsável (o único
  //     carimbo automático é o `default_assignee_user_id` do INBOUND, por canal), então
  //     em "Minhas" o chat abre mas a sidebar fica sem a linha: o servidor não a devolve
  //     e o insert de WS é gateado de propósito (conversationRows.js `rowMatchesView`);
  //   • o deep-link de uma conversa que é de outro atendente (o 89 fez o link SEMPRE
  //     abrir; aqui a sidebar volta a mostrar o contexto dela).
  // Trocar a aba não descarta nada do operador — ela é uma VIEW, e por isso já é
  // excluída do spec de preset salvo (useConversationFilters.js "assignmentTab is
  // intentionally excluded"). A decisão em si é pura e testada (hubDefaults.js).
  //
  // UMA decisão por thread aberta, tomada quando o detalhe ASSENTA (`_threadKey` já
  // casa, sem carregamento em voo): reavaliar a cada mudança de `contactData` faria a
  // aba ceder no instante em que o operador atribui a si mesmo uma conversa da fila
  // "Não atribuídas" — isso é o fluxo normal daquela aba, não um contexto escondido.
  const yieldDecidedRef = useRef(null);
  useEffect(() => {
    if (selectedKey == null) { yieldDecidedRef.current = null; return; }
    if (yieldDecidedRef.current === selectedKey) return;
    if (loadingDetail || detailStale) return;   // a evidência ainda não assentou
    // Identidade em voo (`getMe()` é assíncrono): não CARIMBA a decisão, senão a
    // primeira conversa aberta logo após o boot ficaria decidida com base em "não sei
    // quem sou" e nunca seria reavaliada.
    if (assignmentTab === 'mine' && currentUserId == null) return;
    yieldDecidedRef.current = selectedKey;
    const openRow = selectedConvId != null
      ? contacts.find(c => c.conversation_id === selectedConvId)
      : contacts.find(c => c.conversation_id == null && c.phone === selected);
    if (shouldYieldAssignmentTab({
      assignmentTab, currentUserId, hasOpenThread: true,
      conversation: (contactData && contactData.conversation) || null,
      row: openRow || null,
    })) {
      setAssignmentTab('all');
    }
  }, [selectedKey, loadingDetail, detailStale, contactData, contacts,
      assignmentTab, currentUserId, selected, selectedConvId, setAssignmentTab]);
  const canReadContact = hasPermission(currentUser, 'contact.read');

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
        class="shrink-0 border-r border-wa-border overflow-hidden ${isResizing ? '' : 'transition-all duration-300'} ${sidebarHidden ? 'lg:border-r-0' : ''} ${selectedKey ? 'hidden lg:flex lg:flex-col' : 'flex flex-col w-full'}"
        style=${isDesktop ? `width:${sidebarHidden ? 0 : sidebarWidth}px` : ''}
      >
        <${ContactList}
          contacts=${displayedContacts}
          loading=${loading}
          loadingMore=${loadingMore}
          hasMore=${hasMore}
          loadMore=${loadMore}
          search=${search}
          onSearchChange=${onSearchChange}
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
          onSelect=${selectContact}
          onDropFiles=${handleRowDropFiles}
          onContextMenu=${setCtxMenu}
          typingState=${typingState}
          aiRespondingState=${aiRespondingState}
          operatorTypingState=${operatorTypingState}
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
          onDeselectAll=${deselectAll}
          onBulkAI=${handleBulkAI}
          onBulkArchive=${handleBulkArchive}
          onBulkTag=${handleBulkTag}
          onBulkRemoveAllTags=${handleBulkRemoveAllTags}
          onBulkPin=${handleBulkPin}
          onBulkMarkRead=${handleBulkMarkRead}
          onBulkMarkUnread=${handleBulkMarkUnread}
          onBulkAssign=${handleBulkAssign}
          currentUserId=${currentUserId}
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
      <!-- plano 89 F4 — a visibilidade segue a CHAVE da thread (conversa-primeiro), não o
           telefone: um deep-link resolvido por id abre com o telefone nulo, e a condição
           antiga escondia o chat (e o card de erro) abaixo do breakpoint lg. -->
      <div class="flex-1 min-w-0 min-h-0 ${!selectedKey ? 'hidden lg:flex' : 'flex'} relative">
        <div class="w-full h-full flex flex-col">
          ${detailError && selectedKey
            ? html`<div class="flex flex-col items-center justify-center gap-3 h-full bg-wa-panel px-6 text-center">
                <div class="text-[15px] text-wa-text">Não foi possível abrir esta conversa</div>
                <div class="text-[13px] text-wa-secondary max-w-md">${detailError}</div>
                <div class="mt-1 flex items-center gap-2">
                  <button
                    class="px-4 py-2 rounded-lg bg-wa-teal text-white text-[13px] hover:opacity-90 transition-opacity"
                    onClick=${retryDetail}
                  >Tentar de novo</button>
                  <!-- Saída no mobile: com a sidebar oculta (selectedKey setado), este card
                       era um beco sem saída — o "voltar" do ContactDetail não renderiza aqui. -->
                  <button
                    class="lg:hidden px-4 py-2 rounded-lg border border-wa-border text-wa-text text-[13px] hover:bg-wa-hover transition-colors"
                    onClick=${() => selectContact(null)}
                  >Voltar</button>
                </div>
              </div>`
          : (loadingDetail || detailStale)
            ? html`<div class="flex items-center justify-center h-full bg-wa-panel text-wa-secondary animate-pulse-slow text-[14px]">Carregando...</div>`
            : html`<${ContactDetail}
                phone=${selected}
                conversationId=${selectedConvId}
                channelId=${selectedChannelId}
                onBack=${() => selectContact(null)}
                messages=${messages}
                setContactData=${setContactData}
                info=${info}
                contact=${detailStale ? null : contactData}
                channelProvider=${headerChannelProvider}
                channelName=${headerChannelName}
                showChannel=${showChannel}
                onAvatarClick=${canReadContact ? () => selected && setOpenPanel('contact') : null}
                onOpenConversationInfo=${() => selected && setOpenPanel('conversation')}
                currentUser=${currentUser}
                contactTyping=${selected && typingState[typingKey({ conversationId: selectedConvId, channelId: selectedChannelId, phone: selected })] || null}
                aiResponding=${selected && !!aiRespondingState[typingKey({ channelId: selectedChannelId, phone: selected })]}
                operatorTyping=${selected ? operatorTypingFor(operatorTypingState, { conversation_id: selectedConvId, channel_id: selectedChannelId, phone: selected }) : null}
                globalTags=${globalTags}
                groupParticipantsChanged=${groupParticipantsChanged}
                scrollToMsg=${scrollToMsg}
                onScrolledToMsg=${() => setScrollToMsg(null)}
                showAgentName=${config ? (config.show_agent_name !== false) : true}
                loadOlder=${loadOlder}
                loadingOlder=${loadingOlder}
                hasMore=${contactData && contactData.has_more}
                loadNewer=${loadNewer}
                loadingNewer=${loadingNewer}
                hasMoreNewer=${!!(contactData && !detailStale && contactData.has_more_newer)}
                jumping=${jumping}
                onJumpToMessage=${jumpToMessage}
                onJumpToDate=${jumpToDate}
                onBackToBottom=${backToBottom}
                newWhileAnchored=${(contactData && contactData._newWhileAnchored) || 0}
                droppedFiles=${droppedFiles}
                onDroppedFilesConsumed=${() => setDroppedFiles(null)}
              />`
          }
          ${openPanel === 'contact' && selected && canReadContact ? html`
            <${ContactInfoPanel}
              phone=${selected}
              currentUser=${currentUser}
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
              onOpenContactInfo=${canReadContact ? () => selected && setOpenPanel('contact') : null}
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
          conversationId=${ctxMenu.conversationId}
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
          agentsUsers=${agentsUsers}
          agentsAi=${agentsAi}
          currentUserId=${currentUserId}
          currentUser=${currentUser}
          onAssignConversation=${handleAssignConversation}
          onAssignAgent=${handleAssignAgent}
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
