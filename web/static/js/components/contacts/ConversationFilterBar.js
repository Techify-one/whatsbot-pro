// Conversation filter bar (plano 10 FF2/FF6) — sits right below the search box in
// the main inbox. Chatwoot-style: a status chip (Abertas/Resolvidas/Todas), a
// funnel popover (filter by status + etiqueta), a sort popover (Ordenar por), and
// the assignment tabs (Minhas / Não atribuídas / Todas) with live counts.
//
// All filtering is client-side over the already-fetched (enriched) contact list,
// so switching tabs is instant. State is owned by Contacts.js and passed in.

import { h } from 'preact';
import { useState, useRef, useEffect } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const STATUS_LABELS = { open: 'Abertas', closed: 'Resolvidas', all: 'Todas' };
const STATUS_OPTIONS = [
  { value: 'open', label: 'Abertas' },
  { value: 'closed', label: 'Resolvidas' },
  { value: 'all', label: 'Todas' },
];
const SORT_OPTIONS = [
  { value: 'activity', label: 'Última atividade' },
  { value: 'oldest', label: 'Mais antigas' },
  { value: 'unread', label: 'Não lidas primeiro' },
];

function FunnelIcon() {
  return html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z"/></svg>`;
}
function SortIcon() {
  return html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M3 18h6v-2H3v2zM3 6v2h18V6H3zm0 7h12v-2H3v2z"/></svg>`;
}
function ChevronDown() {
  return html`<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>`;
}

// Small popover anchored to its trigger button; closes on outside click / Escape.
function Popover({ open, onClose, children, align = 'left' }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) onClose(); }
    function onKey(e) { if (e.key === 'Escape') onClose(); }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open, onClose]);
  if (!open) return null;
  return html`
    <div ref=${ref} class="absolute z-[70] mt-1 ${align === 'right' ? 'right-0' : 'left-0'} bg-wa-panel rounded-lg shadow-lg border border-wa-border p-3 min-w-[260px]">
      ${children}
    </div>
  `;
}

export function ConversationFilterBar({
  statusFilter, onStatusChange,
  assignmentTab, onAssignmentChange,
  counts,
  sortBy, onSortChange,
  tagFilter, onTagFilterChange,
  globalTags,
  hasIdentity,
}) {
  const [statusOpen, setStatusOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);

  const tagNames = Object.keys(globalTags || {});
  const activeTagCount = (tagFilter || []).length;
  const filterActive = statusFilter !== 'open' || activeTagCount > 0;

  function toggleTag(name) {
    const cur = tagFilter || [];
    onTagFilterChange(cur.includes(name) ? cur.filter(t => t !== name) : [...cur, name]);
  }

  const tabBtn = (key, label, count) => {
    const active = assignmentTab === key;
    return html`
      <button
        onClick=${() => onAssignmentChange(key)}
        class="px-2.5 py-2 text-[13px] whitespace-nowrap border-b-2 -mb-px transition-colors flex items-center gap-1.5 ${active
          ? 'border-wa-teal text-wa-teal font-medium'
          : 'border-transparent text-wa-secondary hover:text-wa-text'}"
      >
        ${label}
        <span class="text-[11px] font-semibold px-1.5 py-0.5 rounded-full ${active ? 'bg-wa-teal/15 text-wa-teal' : 'bg-wa-hover text-wa-secondary'}">${count}</span>
      </button>
    `;
  };

  return html`
    <div class="bg-wa-bg border-b border-wa-border">
      <!-- Toolbar: status chip + filter/sort -->
      <div class="flex items-center justify-between px-[12px] pt-[8px] gap-2">
        <div class="relative">
          <button
            onClick=${() => { setStatusOpen(o => !o); setFilterOpen(false); setSortOpen(false); }}
            class="flex items-center gap-1.5 text-[13px] font-medium text-wa-text hover:bg-wa-hover rounded-md px-2 py-1 transition-colors"
            title="Filtrar por status"
          >
            <span class="text-wa-secondary font-normal">Conversas</span>
            <span class="px-2 py-0.5 rounded-full text-[12px] bg-wa-teal/15 text-wa-teal">${STATUS_LABELS[statusFilter] || 'Abertas'}</span>
            <${ChevronDown} />
          </button>
          <${Popover} open=${statusOpen} onClose=${() => setStatusOpen(false)}>
            <div class="text-[12px] text-wa-secondary mb-2 font-medium">Status</div>
            ${STATUS_OPTIONS.map(o => html`
              <button
                key=${o.value}
                onClick=${() => { onStatusChange(o.value); setStatusOpen(false); }}
                class="w-full text-left px-2 py-1.5 rounded-md text-[13px] hover:bg-wa-hover transition-colors ${statusFilter === o.value ? 'text-wa-teal font-medium' : 'text-wa-text'}"
              >${o.label}</button>
            `)}
          </${Popover}>
        </div>

        <div class="flex items-center gap-1">
          <!-- Filter (funnel) -->
          <div class="relative">
            <button
              onClick=${() => { setFilterOpen(o => !o); setStatusOpen(false); setSortOpen(false); }}
              class="w-[32px] h-[32px] rounded-md flex items-center justify-center transition-colors ${filterActive ? 'bg-wa-teal/15 text-wa-teal' : 'text-wa-secondary hover:bg-wa-hover'}"
              title="Filtrar conversas"
            ><${FunnelIcon} /></button>
            <${Popover} open=${filterOpen} onClose=${() => setFilterOpen(false)} align="right">
              <div class="text-[13px] font-medium text-wa-text mb-3">Filtrar conversas</div>
              <label class="block text-[12px] text-wa-secondary mb-1">Status</label>
              <select
                value=${statusFilter}
                onChange=${(e) => onStatusChange(e.target.value)}
                class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] border border-wa-border mb-3"
              >
                ${STATUS_OPTIONS.map(o => html`<option value=${o.value}>${o.label}</option>`)}
              </select>
              ${tagNames.length > 0 ? html`
                <label class="block text-[12px] text-wa-secondary mb-1">Etiquetas</label>
                <div class="flex flex-wrap gap-1.5 mb-3 max-h-[120px] overflow-y-auto wa-scrollbar">
                  ${tagNames.map(name => {
                    const color = (globalTags[name] && globalTags[name].color) || '#6b7280';
                    const on = (tagFilter || []).includes(name);
                    return html`<button
                      key=${name}
                      onClick=${() => toggleTag(name)}
                      class="text-[11px] font-semibold rounded px-2 py-0.5 transition-all"
                      style="background:${color}${on ? '40' : '20'}; color:${color}; border:1px solid ${color}${on ? 'aa' : '40'};"
                    >${on ? '✓ ' : ''}${name}</button>`;
                  })}
                </div>
              ` : null}
              <div class="flex items-center justify-end gap-2">
                <button
                  onClick=${() => { onStatusChange('open'); onTagFilterChange([]); }}
                  class="px-2.5 py-1 rounded-md text-[12px] text-wa-secondary hover:bg-wa-hover transition-colors"
                >Limpar filtros</button>
                <button
                  onClick=${() => setFilterOpen(false)}
                  class="px-3 py-1 rounded-md text-[12px] bg-wa-teal text-white hover:bg-wa-tealDark transition-colors"
                >Aplicar</button>
              </div>
            </${Popover}>
          </div>

          <!-- Sort -->
          <div class="relative">
            <button
              onClick=${() => { setSortOpen(o => !o); setStatusOpen(false); setFilterOpen(false); }}
              class="w-[32px] h-[32px] rounded-md flex items-center justify-center text-wa-secondary hover:bg-wa-hover transition-colors"
              title="Ordenar"
            ><${SortIcon} /></button>
            <${Popover} open=${sortOpen} onClose=${() => setSortOpen(false)} align="right">
              <label class="block text-[12px] text-wa-secondary mb-1">Status</label>
              <select
                value=${statusFilter}
                onChange=${(e) => onStatusChange(e.target.value)}
                class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] border border-wa-border mb-3"
              >
                ${STATUS_OPTIONS.map(o => html`<option value=${o.value}>${o.label}</option>`)}
              </select>
              <label class="block text-[12px] text-wa-secondary mb-1">Ordenar por</label>
              <select
                value=${sortBy}
                onChange=${(e) => onSortChange(e.target.value)}
                class="wa-field w-full px-2 py-1.5 rounded-md text-[13px] border border-wa-border"
              >
                ${SORT_OPTIONS.map(o => html`<option value=${o.value}>${o.label}</option>`)}
              </select>
            </${Popover}>
          </div>
        </div>
      </div>

      <!-- Assignment tabs -->
      <div class="flex items-center gap-1 px-[12px] mt-1 border-b border-wa-border overflow-x-auto wa-scrollbar">
        ${hasIdentity ? tabBtn('mine', 'Minhas', counts.mine) : null}
        ${tabBtn('unassigned', 'Não atribuídas', counts.unassigned)}
        ${tabBtn('all', 'Todas', counts.all)}
      </div>
    </div>
  `;
}

export default ConversationFilterBar;
