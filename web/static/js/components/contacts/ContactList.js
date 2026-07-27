import { h } from 'preact';
import { useState, useEffect, useLayoutEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { SearchIcon, DefaultAvatar, GroupAvatar, SingleCheckIcon, DoubleCheckIcon, ClockIcon, ArchiveIcon } from './icons.js';
import { formatTime, avatarUrl } from './utils.js';
import { useScrollSentinel } from '../../hooks/useInfiniteScroll.js';
import { formatPhoneDisplay } from '../../utils/phone.js';
import { TagPicker } from './TagPicker.js';
import { AssigneeList } from './AssigneeList.js';
import { clampFlyoutOffset } from './menuLayout.js';
import { dragHasFiles } from './hooks/useDropZone.js';
import { ConversationFilterBar } from './ConversationFilterBar.js';
// Selo do canal — compartilhado com o cabeçalho do chat (mesma aparência nos dois).
import { ChannelChip } from './ChannelChip.js';
import { Slot } from '../../plugins/Slot.js';
// Rascunho do compositor (services/drafts.js): a linha mostra "Rascunho: …" no
// lugar da última mensagem enquanto houver texto não enviado naquela conversa.
import { getDraft } from '../../services/drafts.js';
import { useDrafts } from '../../hooks/useDrafts.js';

const html = htm.bind(h);

// Approximate flyout width (px) — used to decide which side the bulk submenu opens on.
const FLYOUT_WIDTH = 264;

// Tiny assignee chip shown on each row (plano 10): person for a human agent, bot
// for an AI agent. Highlighted in teal when the conversation is assigned to me.
function AssigneeChip({ assignee }) {
  if (!assignee) return null;
  const cls = assignee.isMe ? 'text-wa-teal' : 'text-wa-secondary';
  const icon = assignee.isAi
    ? html`<svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M12 2a2 2 0 012 2v1h3a2 2 0 012 2v2h1a2 2 0 010 4h-1v2a2 2 0 01-2 2h-3v1a2 2 0 01-4 0v-1H7a2 2 0 01-2-2v-2H4a2 2 0 010-4h1V7a2 2 0 012-2h3V4a2 2 0 012-2zm-3 7a1 1 0 00-1 1v4a1 1 0 002 0v-4a1 1 0 00-1-1zm6 0a1 1 0 00-1 1v4a1 1 0 002 0v-4a1 1 0 00-1-1z"/></svg>`
    : html`<svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>`;
  return html`
    <span class="flex items-center gap-[3px] text-[10px] ${cls} max-w-[110px]" title=${'Atribuída a ' + assignee.label}>
      ${icon}<span class="truncate">${assignee.label}</span>
    </span>
  `;
}

// Kebab (3-dots) menu icon, shared by the header menus. Defined as components
// (functions returning a vnode) so they can be used as <${KebabIcon} />.
const KebabIcon = () => html`
  <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
    <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/>
  </svg>
`;
const PinIcon = () => html`
  <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
    <path d="M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z"/>
  </svg>
`;
// Cadeado — prefixo do preview quando a última mensagem é uma nota privada
// (role 'private_note'), notificada na sidebar via `notify_private_messages`. Mesmo
// path do card "Mensagem privada" em SystemMessageCard.js.
const LockIcon = () => html`
  <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" class="inline-block shrink-0 align-[-1px] mr-[3px]">
    <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/>
  </svg>
`;

// Atendimento-cêntrico (plano 11 D1): cada linha é um ATENDIMENTO. A identidade é a
// conversation_id (linhas sem atendimento caem no phone) — usada como key do Preact e
// para casar a seleção. Mantém os dois canais do mesmo número como linhas distintas.
export function rowKeyFor(c) {
  return c.conversation_id != null ? `conv:${c.conversation_id}` : `phone:${c.phone}`;
}

// Chave do estado de "digitando". Atendimento-cêntrico: a presença pertence a UMA
// atendimento específica (o canal GOWA que reportou) — casamos por conversation_id,
// que é inequívoco. Linhas/eventos sem atendimento (legado/sandbox) caem no par
// canal::telefone. As duas pontas (broadcast e linha da sidebar) usam ESTA função.
export function typingKey({ conversationId = null, channelId = null, phone = null } = {}) {
  if (conversationId != null) return `conv:${conversationId}`;
  return `${channelId || 'default'}::${phone}`;
}

function normalizePhone(input) {
  const digits = input.replace(/\D/g, '');
  if (digits.length < 10) return null;
  if (digits.startsWith('55')) return digits;
  return '55' + digits;
}

function looksLikePhone(input) {
  return input.replace(/\D/g, '').length >= 10;
}

// Casefold + strip accents, mirroring the backend `_fold` so highlighting matches
// the same way the search does.
function foldStr(s) {
  return (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

// Split `text` into {s, hit} segments around occurrences of `query`
// (accent/case-insensitive). Falls back to one plain segment when folding changes
// the length (then folded indices can't be mapped back to the original text).
function highlightParts(text, query) {
  const t = text || '';
  const q = foldStr(query);
  if (!q) return [{ s: t, hit: false }];
  const f = foldStr(t);
  if (f.length !== t.length) return [{ s: t, hit: false }];
  const parts = [];
  let i = 0;
  while (i <= t.length) {
    const idx = f.indexOf(q, i);
    if (idx === -1) { if (i < t.length) parts.push({ s: t.slice(i), hit: false }); break; }
    if (idx > i) parts.push({ s: t.slice(i, idx), hit: false });
    parts.push({ s: t.slice(idx, idx + q.length), hit: true });
    i = idx + q.length;
  }
  return parts;
}

// Chevron da faixa de etiquetas (mesmo path do ChevronDown da barra de filtros).
const TagChevron = () => html`
  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
`;

// Faixa de etiquetas da linha da conversa. Regras (pedido do operador):
//   - o nome da etiqueta aparece SEMPRE inteiro (nada de truncar com "…");
//   - colapsada, mostra só o que coube na PRIMEIRA linha;
//   - havendo excesso, uma seta expande a linha inteira (todas as etiquetas) e
//     recolhe no clique seguinte.
// O corte é feito por `max-height` = altura de uma etiqueta + `overflow-hidden`: o
// flex-wrap já posiciona os chips, então o navegador esconde as linhas seguintes sem
// nunca cortar um chip no meio. A altura da etiqueta é MEDIDA (não hardcoded) para
// acompanhar zoom/fonte do usuário, e re-medida quando a sidebar muda de largura.
function RowTags({ tags, globalTags, expanded, onToggle }) {
  const wrapRef = useRef(null);
  const [lineH, setLineH] = useState(0);
  const [overflowing, setOverflowing] = useState(false);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      const first = el.firstElementChild;
      if (!first) { setLineH(0); setOverflowing(false); return; }
      const h = first.offsetHeight;
      setLineH(h);
      setOverflowing(el.scrollHeight > h + 1);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [tags.join('|'), expanded]);

  return html`
    <div class="flex items-start gap-[3px] mt-[2px]">
      <div
        ref=${wrapRef}
        class="flex-1 min-w-0 flex flex-wrap gap-[3px] overflow-hidden"
        style=${(!expanded && lineH) ? `max-height:${lineH}px` : null}
      >
        ${tags.map(tagName => {
          const tagInfo = globalTags && globalTags[tagName];
          const color = tagInfo ? tagInfo.color : '#6b7280';
          return html`<span
            key=${tagName}
            class="text-[9px] font-semibold rounded px-[4px] py-[0.5px] leading-[14px] whitespace-nowrap"
            style="background: ${color}20; color: ${color}; border: 1px solid ${color}40;"
            title=${tagName}
          >${tagName}</span>`;
        })}
      </div>
      ${(overflowing || expanded) ? html`
        <button
          onClick=${(e) => { e.stopPropagation(); e.preventDefault(); onToggle(); }}
          title=${expanded ? 'Mostrar menos etiquetas' : 'Mostrar todas as etiquetas'}
          class="shrink-0 w-[16px] h-[16px] flex items-center justify-center rounded text-wa-secondary hover:text-wa-text hover:bg-wa-hover transition-colors"
        ><span class="flex transition-transform ${expanded ? 'rotate-180' : ''}"><${TagChevron} /></span></button>
      ` : null}
    </div>
  `;
}

// ── Contact List (WhatsApp Web sidebar) ──────────────────────────

export function ContactList({ contacts, loading, search, onSearchChange, selected, onSelect, onContextMenu, onDropFiles, typingState, aiRespondingState, showArchived, onToggleArchived, globalTags, onStartConversation, onNewConversation, checkingPhone, checkPhoneError, wsConnected, autoReply, onToggleAutoReply,
  selectionMode, selectedKeys, onEnterSelection, onExitSelection, onToggleSelect, onSelectAll, onClearSelection, onBulkAI, onBulkArchive, onBulkTag, onBulkRemoveAllTags, onBulkPin, onBulkMarkRead, onBulkMarkUnread, onBulkAssign, onCreateTag,
  currentUserId,
  statusFilter, onStatusChange, assignmentTab, onAssignmentChange, tabCounts, sortBy, onSortChange, tagFilter, onTagFilterChange, advFilters, onAdvFiltersChange, channels, agentsUsers, agentsAi, resolveAssignee, hasIdentity,
  savedFilters, activeFilter, anyFilterActive, onApplySavedFilter, onSaveCurrentFilter, onOverwriteSavedFilter, onRenameSavedFilter, onRemoveSavedFilter, onClearFilters,
  loadMore = null, loadingMore = false, hasMore = false }) {
  const headerBg = wsConnected === false ? 'bg-[#6b2c2c]' : showArchived ? 'bg-[#2a3942]' : 'bg-wa-teal';
  // Rascunhos (services/drafts.js): re-renderiza quando o compositor — ou outra
  // aba do navegador — mexe no mapa, e resolve o texto de cada linha aqui. A
  // chave do rascunho É o rowKeyFor (a conversa é a dona do texto); truncado no
  // mesmo tamanho do preview da última mensagem.
  //
  // A conversa ABERTA fica de fora: com o chat na tela o operador já vê o que
  // escreveu no compositor, e trocar o preview a cada tecla era ruído. O
  // "Rascunho:" aparece quando ele SAI da conversa deixando texto para trás.
  useDrafts(selected);
  const rowDrafts = {};
  for (const c of (contacts || [])) {
    const key = rowKeyFor(c);
    if (key === selected) continue;
    const text = getDraft(key);
    if (text) rowDrafts[key] = text.substring(0, 80);
  }
  // plano 50 F8 — scroll infinito: sentinela no fim da lista dispara loadMore quando
  // há próxima página. plano 62 F6: os DOIS modos paginam (conversa-first e busca), então
  // o gatilho é só `hasMore`. Usa o mesmo primitivo reutilizável `useScrollSentinel` das
  // demais listas.
  const bottomSentinelRef = useRef(null);
  const listScrollRef = useRef(null);
  useScrollSentinel(
    bottomSentinelRef,
    () => { if (!loadingMore) loadMore && loadMore(); },
    !!(hasMore && loadMore),
    listScrollRef,
    '0px 0px 200px 0px',
  );
  const selCount = (selectedKeys || []).length;
  // Selection is keyed per CONVERSATION row (rowKeyFor), not by phone — so the two
  // channels of the same number are selectable independently.
  const selectedSet = new Set(selectedKeys || []);
  // For the bulk-tag toggle indicator: does every selected conversation have this tag?
  const selectedContacts = (contacts || []).filter(c => selectedSet.has(rowKeyFor(c)));

  // Arrastar arquivos direto para uma conversa da lista (plano 64 · F11). O
  // drop NÃO envia às cegas (P7): abre a conversa com a prévia já montada, e o
  // operador confirma. `dragOverKey` realça a linha sob o cursor.
  const [dragOverKey, setDragOverKey] = useState(null);
  const dropEnabled = !selectionMode && typeof onDropFiles === 'function';

  // Linhas com a faixa de etiquetas expandida (todas as etiquetas, várias linhas).
  // Chaveado por rowKeyFor — o mesmo identificador de seleção/drag. O estado mora AQUI
  // (e não dentro do RowTags) para sobreviver aos re-renders frequentes da lista
  // (WebSocket de nova mensagem, refresh de contagens, etc.).
  const [expandedTagRows, setExpandedTagRows] = useState(() => new Set());
  const toggleTagRow = (key) => setExpandedTagRows(prev => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const allSelectedHaveTag = (name) =>
    selectedContacts.length > 0 && selectedContacts.every(c => (c.tags || []).includes(name));
  // Pin toggle: when every selected is already pinned, the action unpins all.
  const allSelectedPinned = selectedContacts.length > 0 && selectedContacts.every(c => c.is_pinned);
  // Bulk assign (attendant): mirror the single-conversation menu — the flyout reuses
  // AssigneeList (search + humans + AI). Hide when there's no identity and no
  // listable agents (open install before the first admin).
  const bulkAgentsUsers = Array.isArray(agentsUsers) ? agentsUsers : [];
  const bulkAgentsAi = Array.isArray(agentsAi) ? agentsAi : [];
  const showBulkAssign = currentUserId != null || bulkAgentsUsers.length > 0 || bulkAgentsAi.length > 0;
  // AssigneeList checkmarks a single assignee/agent. For the multi-selection, resolve
  // the COMMON value: the shared assignee (or AI agent) when every selected row agrees,
  // else null (mixed → no checkmark).
  const _uids = new Set(selectedContacts.map(c => c.assignee_user_id ?? null));
  const commonAssigneeId = _uids.size === 1 ? [..._uids][0] : null;
  const _keys = new Set(selectedContacts.map(c => c.active_agent_key ?? null));
  const commonActiveKey = _keys.size === 1 ? [..._keys][0] : null;
  // "Desatribuir" clears whoever is assigned — human OR AI agent — so it must appear
  // whenever any selected conversation has either set (incl. those assigned before
  // entering selection mode), even when the selection is mixed.
  const anySelectedAssigned = selectedContacts.some(c => c.assignee_user_id != null || c.active_agent_key != null);

  // Header dropdown state (one menu visible at a time given selectionMode).
  const [headerMenuOpen, setHeaderMenuOpen] = useState(false);
  const [bulkMenuOpen, setBulkMenuOpen] = useState(false);
  const menuRef = useRef(null);

  // Bulk submenus (Adicionar tags / Atribuir atendente) render as a flyout BESIDE the
  // dropdown (`openSub` = 'tags' | 'assign' | null), mirroring the right-click ContextMenu
  // instead of expanding inline. The flyout is `position: fixed` (like ContextMenu) so it
  // escapes the sidebar's `overflow-hidden` and paints ABOVE the conversation pane instead
  // of being clipped by it. Opens on hover; a short close delay lets the pointer travel
  // from the trigger into the flyout without it flickering shut.
  const [openSub, setOpenSub] = useState(null);
  const [flyoutSide, setFlyoutSide] = useState('right');
  const flyoutRef = useRef(null);
  const tagsRowRef = useRef(null);
  const assignRowRef = useRef(null);
  const [flyoutTop, setFlyoutTop] = useState(0);
  const [flyoutLeft, setFlyoutLeft] = useState(0);
  const [flyoutReady, setFlyoutReady] = useState(false);
  // While the Tags flyout's inline "create tag" form is open, pin the flyout so it
  // doesn't close on mouse-leave (the form pops up away from the pointer).
  const [flyoutPinned, setFlyoutPinned] = useState(false);
  const flyoutPinnedRef = useRef(false);
  const closeTimer = useRef(null);
  const openSubmenu = (name) => { if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; } if (name !== openSub) setFlyoutReady(false); setOpenSub(name); };
  const scheduleClose = () => { if (flyoutPinnedRef.current) return; if (closeTimer.current) clearTimeout(closeTimer.current); closeTimer.current = setTimeout(() => { if (!flyoutPinnedRef.current) setOpenSub(null); }, 180); };

  // Chevron pointing toward where the flyout opens.
  const SubArrow = () => html`<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="ml-auto shrink-0">
    ${flyoutSide === 'left'
      ? html`<path d="M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6z"/>`
      : html`<path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6z"/>`}
  </svg>`;
  // `fixed` (viewport-anchored) + high z so it is neither clipped by the sidebar's
  // overflow nor painted under the conversation pane. Hidden until measured (no flash).
  const flyoutCls = `fixed z-[110] w-64 bg-wa-panel border border-wa-border rounded-lg shadow-lg ${flyoutReady ? '' : 'invisible'}`;

  function closeMenus() {
    setHeaderMenuOpen(false);
    setBulkMenuOpen(false);
    setOpenSub(null);
    setFlyoutPinned(false);
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
  }

  useEffect(() => {
    if (!headerMenuOpen && !bulkMenuOpen) return;
    function onDoc(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) closeMenus();
    }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [headerMenuOpen, bulkMenuOpen]);

  // Leaving selection mode collapses any open bulk menu.
  useEffect(() => { if (!selectionMode) closeMenus(); }, [selectionMode]);

  // Drop the pin whenever the open submenu is not the Tags flyout (only its create-tag
  // form needs it); mirror it into the ref and cancel a pending close the instant we pin.
  useEffect(() => { if (openSub !== 'tags') setFlyoutPinned(false); }, [openSub]);
  useEffect(() => {
    flyoutPinnedRef.current = flyoutPinned;
    if (flyoutPinned && closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; }
  }, [flyoutPinned]);
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);

  // Position the open flyout in viewport (fixed) coords, computed from its trigger row's
  // rect. Opens to the right of the row by default; flips to the left when it would
  // overflow the right viewport edge. Vertically clamped so it stays fully on screen
  // (clampFlyoutOffset); its max-height caps it below the viewport, so it always fits.
  useLayoutEffect(() => {
    if (!openSub) { setFlyoutReady(false); return; }
    const el = flyoutRef.current;
    const rowEl = openSub === 'tags' ? tagsRowRef.current : assignRowRef.current;
    if (!el || !rowEl) return;
    const rect = rowEl.getBoundingClientRect();
    const flyH = el.getBoundingClientRect().height;
    const side = rect.right + FLYOUT_WIDTH + 8 > window.innerWidth ? 'left' : 'right';
    setFlyoutSide(side);
    setFlyoutLeft(side === 'left' ? Math.max(8, rect.left - FLYOUT_WIDTH) : rect.right);
    setFlyoutTop(rect.top + clampFlyoutOffset(rect.top, flyH, window.innerHeight));
    setFlyoutReady(true);
  }, [openSub, globalTags, bulkAgentsUsers.length, bulkAgentsAi.length]);

  return html`
    <div class="flex flex-col h-full bg-wa-bg">
      ${selectionMode ? html`
      <!-- Selection header -->
      <div class="h-[59px] flex items-center justify-between px-4 bg-[#2a3942] shrink-0">
        <div class="flex items-center gap-3 min-w-0">
          <button
            onClick=${onExitSelection}
            class="w-[40px] h-[40px] rounded-full flex items-center justify-center hover:bg-white/10 text-white shrink-0"
            title="Sair da seleção"
          >
            <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M18.3 5.71L12 12.01l-6.3-6.3-1.42 1.42 6.3 6.29-6.3 6.3 1.42 1.41 6.3-6.29 6.29 6.29 1.41-1.41-6.29-6.3 6.3-6.29z"/></svg>
          </button>
          <span class="text-white text-[16px] font-medium truncate">Selecionadas: ${selCount}</span>
        </div>
        <div ref=${menuRef} class="relative shrink-0">
          <button
            onClick=${() => { setBulkMenuOpen(o => !o); setOpenSub(null); }}
            class="w-[40px] h-[40px] rounded-full flex items-center justify-center hover:bg-white/10 text-white"
            title="Ações em massa"
          ><${KebabIcon} /></button>
          ${bulkMenuOpen ? html`
            <div class="absolute right-0 top-[46px] z-[60] bg-wa-panel rounded-lg shadow-lg border border-wa-border py-[4px] min-w-[238px]">
              <button
                disabled=${selCount === 0}
                onClick=${() => { if (confirm(`Ativar a IA para ${selCount} conversa(s) selecionada(s)?`)) { onBulkAI && onBulkAI(true); } closeMenus(); }}
                class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : 'text-wa-text'}"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="#00a884"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
                Ativar IA
              </button>
              <button
                disabled=${selCount === 0}
                onClick=${() => { if (confirm(`Desativar a IA para ${selCount} conversa(s) selecionada(s)?`)) { onBulkAI && onBulkAI(false); } closeMenus(); }}
                class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : 'text-wa-text'}"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="#ef4444"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 13.59L15.59 17 12 13.41 8.41 17 7 15.59 10.59 12 7 8.41 8.41 7 12 10.59 15.59 7 17 8.41 13.41 12 17 15.59z"/></svg>
                Desativar IA
              </button>
              <button
                disabled=${selCount === 0}
                onClick=${() => { onBulkPin && onBulkPin(!allSelectedPinned); closeMenus(); }}
                class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : 'text-wa-text'}"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z"/></svg>
                ${allSelectedPinned ? 'Desafixar conversas' : 'Fixar conversas'}
              </button>
              ${selCount <= 1 ? html`
              <button
                disabled=${selCount === 0}
                onClick=${() => { onBulkMarkRead && onBulkMarkRead(); closeMenus(); }}
                class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : 'text-wa-text'}"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
                Marcar como lidas
              </button>
              ` : ''}
              <button
                disabled=${selCount === 0}
                onClick=${() => { onBulkMarkUnread && onBulkMarkUnread(); closeMenus(); }}
                class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : 'text-wa-text'}"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="#00a884"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
                Marcar como não lidas
              </button>
              <div ref=${tagsRowRef} class="relative" onMouseEnter=${() => { if (selCount > 0) openSubmenu('tags'); }} onMouseLeave=${scheduleClose}>
                <button
                  disabled=${selCount === 0}
                  onClick=${() => { if (selCount > 0) openSubmenu('tags'); }}
                  class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : (openSub === 'tags' ? 'bg-wa-hover text-wa-text' : 'text-wa-text')}"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.42l9 9c.36.36.86.58 1.41.58.55 0 1.05-.22 1.41-.59l7-7c.37-.36.59-.86.59-1.41 0-.55-.23-1.06-.59-1.42zM5.5 7C4.67 7 4 6.33 4 5.5S4.67 4 5.5 4 7 4.67 7 5.5 6.33 7 5.5 7z"/></svg>
                  Adicionar tags
                  <${SubArrow} />
                </button>
                ${(openSub === 'tags' && selCount > 0) ? html`
                  <div ref=${flyoutRef} class=${flyoutCls} style="left:${flyoutLeft}px;top:${flyoutTop}px">
                    <div class="max-h-[70vh] overflow-y-auto wa-scrollbar">
                      <${TagPicker}
                        globalTags=${globalTags}
                        isActive=${allSelectedHaveTag}
                        onToggle=${(name) => onBulkTag && onBulkTag(name)}
                        onCreateTag=${onCreateTag}
                        onClearAll=${() => { if (confirm(`Remover TODAS as tags de ${selCount} conversa(s) selecionada(s)?`)) onBulkRemoveAllTags && onBulkRemoveAllTags(); }}
                        onCreatingChange=${setFlyoutPinned}
                      />
                    </div>
                  </div>
                ` : ''}
              </div>
              ${showBulkAssign ? html`
              <div ref=${assignRowRef} class="relative" onMouseEnter=${() => { if (selCount > 0) openSubmenu('assign'); }} onMouseLeave=${scheduleClose}>
                <button
                  disabled=${selCount === 0}
                  onClick=${() => { if (selCount > 0) openSubmenu('assign'); }}
                  class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : (openSub === 'assign' ? 'bg-wa-hover text-wa-text' : 'text-wa-text')}"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>
                  Atribuir atendente
                  <${SubArrow} />
                </button>
                ${(openSub === 'assign' && selCount > 0) ? html`
                  <div ref=${flyoutRef} class=${flyoutCls} style="left:${flyoutLeft}px;top:${flyoutTop}px">
                    <div class="max-h-[70vh] overflow-y-auto wa-scrollbar">
                      <${AssigneeList}
                        users=${bulkAgentsUsers}
                        aiAgents=${bulkAgentsAi}
                        me=${currentUserId != null ? { id: currentUserId } : null}
                        assigneeUserId=${commonAssigneeId}
                        activeAgentKey=${commonActiveKey}
                        showUnassign=${anySelectedAssigned}
                        onPick=${(payload) => onBulkAssign && onBulkAssign(payload)}
                        showAssignToMe=${currentUserId != null}
                        searchPlaceholder="Buscar atendentes"
                      />
                    </div>
                  </div>
                ` : ''}
              </div>
              ` : ''}
              <button
                disabled=${selCount === 0}
                onClick=${() => { onBulkArchive && onBulkArchive(); closeMenus(); }}
                class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : 'text-wa-text'}"
              >
                <span class="text-wa-text"><${ArchiveIcon} /></span>
                ${showArchived ? 'Desarquivar conversas' : 'Arquivar conversas'}
              </button>
              <div class="border-t border-wa-border">
                <button
                  onClick=${() => { onSelectAll && onSelectAll(); }}
                  class="w-full text-left px-4 py-[10px] text-[14px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-9 14l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
                  Selecionar todas
                </button>
                <button
                  disabled=${selCount === 0}
                  onClick=${() => { onClearSelection && onClearSelection(); closeMenus(); }}
                  class="w-full text-left px-4 py-[10px] text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-3 ${selCount === 0 ? 'opacity-40 cursor-not-allowed text-wa-secondary' : 'text-wa-text'}"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zM7 13v-2h10v2H7z"/></svg>
                  Limpar conversas selecionadas
                </button>
              </div>
            </div>
          ` : ''}
        </div>
      </div>
      ` : html`
      <!-- Green header bar -->
      <div class="h-[59px] flex items-center justify-between px-4 ${headerBg} shrink-0 transition-colors">
        <div class="flex items-center gap-3">
          <button
            onClick=${onToggleArchived}
            class="w-[40px] h-[40px] rounded-full flex items-center justify-center hover:bg-white/10 transition-colors ${showArchived ? 'bg-white/15' : ''}"
            title=${showArchived ? 'Voltar às conversas' : 'Ver arquivados'}
          >
            <span class="text-white"><${ArchiveIcon} /></span>
          </button>
        </div>
        <div class="flex items-center gap-2">
          ${wsConnected === false ? html`
            <span class="text-white/80 text-[13px] animate-pulse">Sem conexão</span>
            <span class="inline-block w-2 h-2 rounded-full bg-red-400 animate-pulse" title="Offline"></span>
          ` : html`
            <span class="text-white text-[15px] font-medium opacity-90">${showArchived ? 'Arquivados' : 'WhatsBot-Pro'}</span>
            <span class="inline-block w-2 h-2 rounded-full bg-green-400" title="Online"></span>
          `}
          <div ref=${menuRef} class="relative">
            <button
              onClick=${() => setHeaderMenuOpen(o => !o)}
              class="w-[34px] h-[34px] rounded-full flex items-center justify-center text-white hover:bg-white/10 transition-colors"
              title="Mais opções"
            ><${KebabIcon} /></button>
            ${headerMenuOpen ? html`
              <div class="absolute right-0 top-[42px] z-[60] bg-wa-panel rounded-lg shadow-lg border border-wa-border py-[4px] min-w-[210px]">
                <button
                  onClick=${() => { closeMenus(); onEnterSelection && onEnterSelection(); }}
                  class="w-full text-left px-4 py-[10px] text-[14px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-9 14l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
                  Selecionar conversas
                </button>
                <button
                  onClick=${() => { closeMenus(); onNewConversation && onNewConversation(); }}
                  class="w-full text-left px-4 py-[10px] text-[14px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-7 9h-2v2H9v-2H7V9h2V7h2v2h2v2z"/></svg>
                  Iniciar conversa
                </button>
              </div>
            ` : ''}
          </div>
        </div>
      </div>
      `}

      <!-- Search bar -->
      <div class="py-[6px] px-[12px] bg-wa-bg border-b border-wa-border">
        <div class="flex items-center bg-wa-panel rounded-lg h-[35px] px-[8px] gap-[20px]">
          <${SearchIcon} />
          <input
            type="text"
            placeholder="Pesquisar ou começar uma nova conversa"
            value=${search}
            onInput=${(e) => onSearchChange(e.target.value)}
            class="bg-transparent border-none outline-none text-wa-text text-[14px] w-full placeholder-wa-secondary"
          />
        </div>
      </div>

      <!-- Status/assignment tabs + filters (plano 10 FF2) -->
      ${!selectionMode && onStatusChange ? html`
        <${ConversationFilterBar}
          statusFilter=${statusFilter}
          onStatusChange=${onStatusChange}
          assignmentTab=${assignmentTab}
          onAssignmentChange=${onAssignmentChange}
          counts=${tabCounts || { all: 0, mine: 0, unassigned: 0 }}
          sortBy=${sortBy}
          onSortChange=${onSortChange}
          tagFilter=${tagFilter}
          onTagFilterChange=${onTagFilterChange}
          advFilters=${advFilters}
          onAdvFiltersChange=${onAdvFiltersChange}
          savedFilters=${savedFilters}
          activeFilter=${activeFilter}
          anyFilterActive=${anyFilterActive}
          onApplySavedFilter=${onApplySavedFilter}
          onSaveCurrentFilter=${onSaveCurrentFilter}
          onOverwriteSavedFilter=${onOverwriteSavedFilter}
          onRenameSavedFilter=${onRenameSavedFilter}
          onRemoveSavedFilter=${onRemoveSavedFilter}
          onClearFilters=${onClearFilters}
          channels=${channels}
          agentsUsers=${agentsUsers}
          agentsAi=${agentsAi}
          globalTags=${globalTags}
          hasIdentity=${hasIdentity}
        />
      ` : null}

      <!-- Contact rows -->
      <div ref=${listScrollRef} class="flex-1 overflow-y-auto wa-scrollbar bg-wa-bg">
        ${loading && contacts.length === 0
          ? html`<div class="text-center text-wa-secondary py-8 animate-pulse-slow text-[14px]">Carregando...</div>`
          : contacts.length === 0
            ? html`<div class="text-center py-8 px-4">
                <div class="text-wa-secondary text-[14px]">Nenhum contato encontrado</div>
                ${search && looksLikePhone(search) ? html`
                  <div class="mt-4">
                    ${checkingPhone
                      ? html`<div class="text-wa-secondary text-[13px] animate-pulse-slow">
                          Verificando se o número possui WhatsApp...
                        </div>`
                      : checkPhoneError
                        ? html`<div class="text-red-400 text-[13px] mb-2">${checkPhoneError}</div>
                               <button
                                 onClick=${() => onStartConversation(normalizePhone(search))}
                                 class="text-wa-teal text-[13px] hover:underline cursor-pointer"
                               >Tentar novamente</button>`
                        : html`<button
                            onClick=${() => onStartConversation(normalizePhone(search))}
                            class="mt-2 px-4 py-[6px] bg-wa-teal/10 text-wa-teal text-[13px] rounded-lg hover:bg-wa-teal/20 transition-colors cursor-pointer border border-wa-teal/30"
                          >
                            Iniciar conversa com ${formatPhoneDisplay(normalizePhone(search))}
                          </button>`
                    }
                  </div>
                ` : null}
              </div>`
            : contacts.map(c => html`
                <div
                  key=${rowKeyFor(c)}
                  onClick=${() => selectionMode ? onToggleSelect(rowKeyFor(c)) : onSelect(c, c.match_msg_id)}
                  onDragEnter=${dropEnabled ? (e) => { if (dragHasFiles(e)) { e.preventDefault(); setDragOverKey(rowKeyFor(c)); } } : null}
                  onDragOver=${dropEnabled ? (e) => {
                    if (!dragHasFiles(e)) return;
                    e.preventDefault();
                    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
                    setDragOverKey(rowKeyFor(c));
                  } : null}
                  onDragLeave=${dropEnabled ? () => setDragOverKey(k => (k === rowKeyFor(c) ? null : k)) : null}
                  onDrop=${dropEnabled ? (e) => {
                    if (!dragHasFiles(e)) return;
                    e.preventDefault();
                    e.stopPropagation();
                    setDragOverKey(null);
                    const files = e.dataTransfer && e.dataTransfer.files;
                    if (files && files.length) onDropFiles(c, files);
                  } : null}
                  onContextMenu=${(e) => { if (selectionMode) return; e.preventDefault(); onContextMenu && onContextMenu({ x: e.clientX, y: e.clientY, phone: c.phone, conversationId: c.conversation_id ?? null, aiEnabled: c.ai_enabled !== false, tags: c.tags || [], isArchived: !!c.is_archived, isUnread: (c.unread_count > 0 || c.unread_ai_count > 0), isPinned: !!c.is_pinned }); }}
                  class="wa-contact-row flex items-center pl-[13px] pr-[15px] cursor-pointer ${
                    dragOverKey === rowKeyFor(c) ? 'bg-wa-teal/25 outline outline-2 outline-wa-teal -outline-offset-2'
                      : (selectionMode && selectedSet.has(rowKeyFor(c))) ? 'bg-wa-selected'
                      : (!selectionMode && selected === rowKeyFor(c)) ? 'bg-wa-selected' : 'hover:bg-wa-hover'
                  }"
                >
                  ${selectionMode ? html`
                    <div class="shrink-0 mr-[10px] flex items-center justify-center">
                      <span class="w-[22px] h-[22px] rounded-full border-2 flex items-center justify-center transition-colors ${selectedSet.has(rowKeyFor(c)) ? 'bg-wa-teal border-wa-teal' : 'border-wa-secondary'}">
                        ${selectedSet.has(rowKeyFor(c)) ? html`
                          <svg viewBox="0 0 24 24" width="14" height="14" fill="white"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
                        ` : ''}
                      </span>
                    </div>
                  ` : ''}
                  <!-- Avatar -->
                  <div class="w-[49px] h-[49px] rounded-full overflow-hidden shrink-0 mr-[13px]">
                    ${c.is_group
                      ? html`<${GroupAvatar} size=${49} avatarUrl=${avatarUrl(c.phone, c.avatar_v)} />`
                      : html`<${DefaultAvatar} size=${49} avatarUrl=${avatarUrl(c.phone, c.avatar_v)} />`
                    }
                  </div>

                  <!-- Text content with bottom border -->
                  <div class="flex-1 min-w-0 border-b border-wa-border py-[13px]">
                    <!-- Linha 1: canal (esq.) + atendente (dir.), na MESMA altura, para não
                         sobrar vão vazio de um dos lados. O canal fica ACIMA do nome (estilo
                         Chatwoot) e é sempre visível — não passa pelo gate showChannel, que
                         segue valendo só no cabeçalho do chat. Cada chip devolve null quando
                         não há dado (linha sem canal / sem atendente). -->
                    <div class="flex items-center justify-between gap-[6px] min-w-0 mb-[1px]">
                      <${ChannelChip} provider=${c.channel_provider} name=${c.channel_name} margin=${false} />
                      ${resolveAssignee ? html`<span class="ml-auto shrink-0"><${AssigneeChip} assignee=${resolveAssignee(c)} /></span>` : null}
                    </div>
                    <div class="flex justify-between items-baseline">
                      <span class="text-wa-text text-[17px] truncate leading-[21px]">
                        ${c.is_group
                          ? (c.group_name || c.name || c.phone)
                          : html`<span class=${c.name && c.name.startsWith('~') ? 'underline decoration-1 underline-offset-2' : ''} title=${c.name && c.name.startsWith('~') ? 'Nome obtido do WhatsApp (ainda não renomeado)' : null}>${(c.name || '').replace(/^~/, '') || c.phone}</span>`
                        }
                        ${c.archived_by_app
                          ? html`<span class="ml-[6px] text-[10px] font-semibold text-amber-400 bg-amber-500/15 rounded px-[5px] py-[1px] align-middle" title="Arquivado pela aplicação">APP</span>`
                          : null
                        }
                        ${c.conversation_id == null
                          // O badge descreve o gate de IA do ATENDIMENTO. Linha sem atendimento
                          // (contato que só aparece na busca) não tem estado de IA a mostrar —
                          // sem este guard cairia no ramo verde, porque `conv_ai_active` vem
                          // NULL do banco e `_shape_contact_row` defaulta para true.
                          ? null
                          : (!autoReply || c.conv_ai_active === 0 || c.conv_ai_active === false)
                            ? html`<span class="ml-[6px] text-[10px] font-semibold text-red-400 bg-red-500/15 rounded px-[5px] py-[1px] align-middle" title=${!autoReply ? 'IA desligada pelo interruptor global' : null}>IA OFF</span>`
                            : html`<span class="ml-[6px] text-[10px] font-semibold text-green-400 bg-green-500/15 rounded px-[5px] py-[1px] align-middle">IA</span>`
                        }
                      </span>
                      <!-- Linha 2 (dir.): só fixado + hora. O atendente subiu para a linha do
                           canal, então esta coluna deixou de ser uma pilha. -->
                      <span class="flex items-center gap-[4px] ml-[6px] shrink-0">
                        ${c.is_pinned ? html`<span class="text-wa-secondary" title="Conversa fixada"><${PinIcon} /></span>` : ''}
                        <span class="text-wa-secondary text-[12px] leading-[14px]">${formatTime(c.last_message_ts)}</span>
                      </span>
                    </div>
                    ${(c.tags && c.tags.length > 0) ? html`<${RowTags}
                      tags=${c.tags}
                      globalTags=${globalTags}
                      expanded=${expandedTagRows.has(rowKeyFor(c))}
                      onToggle=${() => toggleTagRow(rowKeyFor(c))}
                    />` : null}
                    <div class="flex justify-between items-center mt-[3px]">
                      ${aiRespondingState && aiRespondingState[typingKey({ channelId: c.channel_id, phone: c.phone })]
                        ? html`<span class="text-[14px] truncate leading-[20px] text-wa-teal font-medium flex items-center gap-1.5">
                            <span class="inline-block w-1.5 h-1.5 rounded-full bg-wa-teal animate-pulse shrink-0"></span>
                            <span class="truncate">IA respondendo…</span>
                          </span>`
                        : typingState && typingState[typingKey({ conversationId: c.conversation_id, channelId: c.channel_id, phone: c.phone })]
                        ? html`<span class="text-[14px] truncate leading-[20px] text-wa-teal font-medium">
                            ${typingState[typingKey({ conversationId: c.conversation_id, channelId: c.channel_id, phone: c.phone })] === 'audio' ? 'gravando áudio...' : 'digitando...'}
                          </span>`
                        : c.match_snippet
                          ? html`<span class="text-wa-secondary text-[14px] truncate leading-[20px]">
                              ${highlightParts(c.match_snippet, search).map(p =>
                                p.hit ? html`<span class="font-semibold text-wa-text">${p.s}</span>` : p.s
                              )}
                            </span>`
                          : rowDrafts[rowKeyFor(c)]
                          ? html`<span class="text-wa-secondary text-[14px] truncate leading-[20px]">
                              <span class="text-wa-draft font-medium">Rascunho:</span>${' ' + rowDrafts[rowKeyFor(c)]}
                            </span>`
                          : html`<span class="text-wa-secondary text-[14px] truncate leading-[20px]">
                            ${c.last_message_role === 'private_note' ? html`<${LockIcon} />` : ''}${c.last_message_role === 'assistant' ? (() => {
                              const st = c.last_message_status;
                              if (st === 'sent') return html`<${SingleCheckIcon} />`;
                              if (st === 'delivered' || st === 'operator') return html`<${DoubleCheckIcon} color="#92a58c" />`;
                              if (st === 'read') return html`<${DoubleCheckIcon} />`;
                              return html`<${DoubleCheckIcon} color="#92a58c" />`;
                            })() : ''}${c.last_message ? c.last_message.substring(0, 80) : ''}
                          </span>`
                      }
                      ${(c.unread_ai_count > 0 || c.unread_count > 0 || c.has_unread_mention || c.has_user_mention) ? html`
                        <div class="flex items-center gap-[4px] ml-auto pl-[6px] shrink-0">
                          ${c.has_user_mention ? html`
                            <span class="text-violet-400 font-bold text-[17px] leading-none" title="Você foi mencionado numa nota privada">@</span>
                          ` : null}
                          ${c.has_unread_mention ? html`
                            <span class="text-wa-badge font-bold text-[17px] leading-none" title="Você foi mencionado">@</span>
                          ` : null}
                          ${c.unread_ai_count > 0 ? html`
                            <span class="bg-blue-500 text-white text-[11px] font-bold min-w-[20px] h-[20px] rounded-full flex items-center justify-center px-[3px]">
                              ${c.unread_ai_count}
                            </span>
                          ` : null}
                          ${c.unread_count > 0 ? html`
                            <span class="bg-wa-badge text-white text-[11px] font-bold min-w-[20px] h-[20px] rounded-full flex items-center justify-center px-[3px]">
                              ${c.unread_count}
                            </span>
                          ` : null}
                        </div>
                      ` : null}
                      <!-- Plugin extension point: per-row badges (SLA/prioridade/…). Empty by default. -->
                      <${Slot} name="sidebar.row.badges" ctx=${{ row: c }} />
                    </div>
                  </div>
                </div>
              `)
        }
        <!-- plano 69 F4: "mostrando X de Y" — só quando o TOTAL da aba (server-side)
             supera o carregado. Verdadeiro agora que a lista é a filtrada (F2/F3);
             some quando iguais. Legível no modo escuro (text-wa-secondary). -->
        ${(() => {
          const total = tabCounts ? Number(tabCounts[assignmentTab] ?? tabCounts.all ?? 0) : 0;
          const loaded = contacts.length;
          if (!total || total <= loaded) return null;
          return html`<div class="text-center text-wa-secondary pt-3 pb-1 text-[11px]">
            Mostrando ${loaded} de ${total}
          </div>`;
        })()}
        <!-- Sentinela do scroll infinito (plano 50 F8): dispara loadMore ao aproximar
             do fim quando há mais páginas. plano 62 F6: vale também no modo BUSCA (que
             agora pagina) — quem decide é só o hasMore. -->
        ${hasMore ? html`
          <div ref=${bottomSentinelRef} class="text-center text-wa-secondary py-4 text-[12px]">
            ${loadingMore ? 'Carregando mais…' : ''}
          </div>` : null}
      </div>
    </div>
  `;
}
