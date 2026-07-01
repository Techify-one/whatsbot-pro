import { h } from 'preact';
import { useRef, useCallback, useEffect } from 'preact/hooks';
import htm from 'htm';
import { ContactList, typingKey } from './ContactList.js';
import { ContactDetail } from './ContactDetail.js';
import { ContactInfoPanel } from './ContactInfoPanel.js';
import { ConversationInfoPanel } from './ConversationInfoPanel.js';
import { ContextMenu } from './ContextMenu.js';
import { ChannelPickerModal } from './ChannelPickerModal.js';
import { NewConversationModal } from './NewConversationModal.js';
import { useSidebarResize } from './hooks/useSidebarResize.js';
import { useConversationList } from './hooks/useConversationList.js';
import { useConversationSelection } from './hooks/useConversationSelection.js';
import { useConversationFilters } from './hooks/useConversationFilters.js';
import { useConversationActions } from './hooks/useConversationActions.js';
import { useBulkSelection } from './hooks/useBulkSelection.js';
import { useChannelPicker } from './hooks/useChannelPicker.js';
import { useConversationWsEvents } from './hooks/useConversationWsEvents.js';

const html = htm.bind(h);

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
  // Canal escolhido no picker para uma conversa NOVA ainda sem conversa naquele
  // canal — consumido (e zerado) pelo loader de detalhe para escopar o getContact
  // ao canal certo (multicanal), em vez de fundir/abrir a conversa de outro canal.
  const newConvChannelRef = useRef(null);

  // ── Sidebar geometry (self-contained) ──────────────────────────────
  const { sidebarHidden, sidebarWidth, isResizing, isDesktop, startResize } = useSidebarResize();

  // ── Conversation list (rows, search, archived, refs, fetch) ────────
  const list = useConversationList({ onUnreadChange });
  const {
    contacts, setContacts, loading,
    search, setSearch, handleSearchChange,
    showArchived, setShowArchived,
    fetchContacts, sortContacts,
    contactsRef, displayedRef, searchRef, fetchContactsRef, showArchivedRef,
    showChannel, channelOptions,
  } = list;

  // ── Selection / open thread / detail load ──────────────────────────
  const selection = useConversationSelection({
    contacts, loading, setContacts, contactsRef,
    pageVisibleRef, newConvChannelRef,
    initialContactId, initialConversationId, initialScrollMsgId,
  });
  const {
    selected, setSelected,
    selectedConvId, setSelectedConvId,
    selectedChannelId,
    scrollToMsg, setScrollToMsg,
    contactData, setContactData,
    loadingDetail,
    openPanel, setOpenPanel,
    selectedRef, selectedConvIdRef, selectedChannelIdRef,
    openInfoAfterSelect,
    pendingWsMessages,
    isOpenRow, selectContact,
  } = selection;

  // ── Contact/conversation actions + identity + tags + context menu ──
  const actions = useConversationActions({
    setContacts, sortContacts, setContactData,
    setSelected, setSelectedConvId, selectedRef, selectedConvIdRef,
  });
  const {
    globalTags, setGlobalTags,
    currentUserId, currentUser, users, agentsUsers, agentsAi,
    ctxMenu, setCtxMenu, ctxConv,
    handleToggleAI, handleMarkUnread, handleMarkRead,
    handleArchive, handleDelete, handleDeleteConversation, handlePin,
    handleAssignConversation, handleResolveConversation,
    handleCreateTag, applyTagResults, resolveAssignee,
  } = actions;

  // ── Bulk selection mode ─────────────────────────────────────────────
  const bulk = useBulkSelection({
    contactsRef, displayedRef, showArchivedRef,
    setContacts, sortContacts, setContactData,
    setSelected, setSelectedConvId, selectedRef, applyTagResults,
  });
  const {
    selectionMode, setSelectionMode, selectedKeys, setSelectedKeys,
    enterSelection, exitSelection, toggleSelect, selectAllContacts, clearSelection,
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

  // ── Filters + saved presets + derived sidebar list ──────────────────
  const filters = useConversationFilters({
    contacts, selected, selectedConvId, currentUserId, displayedRef,
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

  // ── New-conversation / channel picker ───────────────────────────────
  const picker = useChannelPicker({ selectContact, fetchContacts, setSearch, newConvChannelRef });
  const {
    checkingPhone, checkPhoneError, setCheckPhoneError,
    channelPicker, setChannelPicker,
    showNewConversation, setShowNewConversation,
    handleStartConversation, handlePickChannel, handleNewConversationSent,
  } = picker;

  // ── Real-time WS events (sidebar + open thread patches) ─────────────
  const { typingState, aiRespondingState, convAttrPatch } = useConversationWsEvents({
    newMessage, chatPresence, aiTyping, contactInfoUpdated, tagsChanged,
    contactTagsUpdated, contactAiToggled, messagesRead, messageStatus,
    messageAction, messageReaction, avatarUpdated, conversationCreated,
    setContacts, contactsRef, fetchContacts, fetchContactsRef, searchRef, search, sortContacts,
    setContactData, setSelected, setSelectedConvId,
    selectedRef, selectedConvIdRef, selectedChannelIdRef,
    pendingWsMessages, isOpenRow, selected, contactData,
    setGlobalTags,
    pageVisibleRef,
  });

  // Search box: clear the phone-check error as the operator types (preserves the
  // original handleSearchChange side-effect now that the two states live apart).
  const onSearchChange = useCallback((val) => {
    handleSearchChange(val);
    setCheckPhoneError(null);
  }, [handleSearchChange, setCheckPhoneError]);

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
          showChannel=${showChannel}
          onSelect=${selectContact}
          onContextMenu=${setCtxMenu}
          typingState=${typingState}
          aiRespondingState=${aiRespondingState}
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
          onBulkAssign=${handleBulkAssign}
          users=${users}
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
                currentUser=${currentUser}
                contactTyping=${selected && typingState[typingKey({ conversationId: selectedConvId, channelId: selectedChannelId, phone: selected })] || null}
                aiResponding=${selected && !!aiRespondingState[typingKey({ channelId: selectedChannelId, phone: selected })]}
                globalTags=${globalTags}
                groupParticipantsChanged=${groupParticipantsChanged}
                scrollToMsg=${scrollToMsg}
                onScrolledToMsg=${() => setScrollToMsg(null)}
              />`
          }
          ${openPanel === 'contact' && selected ? html`
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
          currentUser=${currentUser}
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
