// Conversation filter bar (plano 10 FF2/FF6) — sits right below the search box in
// the main inbox. Layout:
//   - status chip (Abertas/Resolvidas/Todas)
//   - LEFT funnel icon  → popover simples: filtro por status + etiqueta (legado)
//   - RIGHT icon (tune) → modal "Filtrar conversas" estilo Chatwoot: construtor de
//     filtros (Canais / Agente / Etiqueta / Última atividade) + Ordenar por
//   - assignment tabs (Minhas / Não atribuídas / Todas) com contagem ao vivo
//
// All filtering is client-side over the already-fetched (enriched) contact list,
// so switching tabs is instant. State is owned by Contacts.js and passed in. The
// advanced builder is a centered modal (not an anchored popover) porque a sidebar
// tem overflow-hidden e cortaria um popover largo.

import { h } from 'preact';
import { useState, useRef, useEffect } from 'preact/hooks';
import htm from 'htm';
import { ConversationFilterDialog } from './ConversationFilterDialog.js';

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
function TuneIcon() {
  return html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"/></svg>`;
}
function ChevronDown() {
  return html`<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>`;
}

// Small popover anchored to its trigger button; closes on outside click / Escape.
function Popover({ open, onClose, children, align = 'left', width = 'min-w-[260px]' }) {
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
    <div ref=${ref} class="absolute z-[70] mt-1 ${align === 'right' ? 'right-0' : 'left-0'} bg-wa-panel rounded-lg shadow-lg border border-wa-border p-3 ${width}">
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
  advFilters, onAdvFiltersChange,
  channels, agentsUsers, agentsAi,
  globalTags,
  hasIdentity,
}) {
  const [statusOpen, setStatusOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);   // funil simples (esquerda)
  const [advOpen, setAdvOpen] = useState(false);         // modal avançado (direita)

  const tagNames = Object.keys(globalTags || {});
  const activeTagCount = (tagFilter || []).length;
  const simpleActive = statusFilter !== 'open' || activeTagCount > 0;
  const advCount = (advFilters || []).length;
  const advActive = advCount > 0;

  function toggleTag(name) {
    const cur = tagFilter || [];
    onTagFilterChange(cur.includes(name) ? cur.filter(t => t !== name) : [...cur, name]);
  }

  // Escape fecha o modal avançado.
  useEffect(() => {
    if (!advOpen) return;
    function onKey(e) { if (e.key === 'Escape') setAdvOpen(false); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [advOpen]);

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
      <!-- Toolbar: status chip + filtros -->
      <div class="flex items-center justify-between px-[12px] pt-[8px] gap-2">
        <div class="relative">
          <button
            onClick=${() => { setStatusOpen(o => !o); setFilterOpen(false); }}
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
          <!-- Filtro simples (funil) — legado: status + etiquetas -->
          <div class="relative">
            <button
              onClick=${() => { setFilterOpen(o => !o); setStatusOpen(false); }}
              class="w-[32px] h-[32px] rounded-md flex items-center justify-center transition-colors ${simpleActive ? 'bg-wa-teal/15 text-wa-teal' : 'text-wa-secondary hover:bg-wa-hover'}"
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

          <!-- Filtro avançado (modal) — Canais / Agente / Etiqueta / Última atividade + Ordenar por -->
          <div class="relative">
            <button
              onClick=${() => { setAdvOpen(true); setStatusOpen(false); setFilterOpen(false); }}
              class="w-[32px] h-[32px] rounded-md flex items-center justify-center transition-colors ${advActive ? 'bg-wa-teal/15 text-wa-teal' : 'text-wa-secondary hover:bg-wa-hover'}"
              title="Filtros avançados e ordenação"
            ><${TuneIcon} />${advActive ? html`<span class="absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] px-1 rounded-full bg-wa-teal text-white text-[10px] font-semibold flex items-center justify-center">${advCount}</span>` : null}</button>
          </div>
        </div>
      </div>

      <!-- Assignment tabs -->
      <div class="flex items-center gap-1 px-[12px] mt-1 border-b border-wa-border overflow-x-auto wa-scrollbar">
        ${hasIdentity ? tabBtn('mine', 'Minhas', counts.mine) : null}
        ${tabBtn('unassigned', 'Não atribuídas', counts.unassigned)}
        ${tabBtn('all', 'Todas', counts.all)}
      </div>

      <!-- Modal de filtros avançados -->
      ${advOpen ? html`
        <div class="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 px-4 pt-[12vh]"
          onClick=${(e) => { if (e.target === e.currentTarget) setAdvOpen(false); }}>
          <div class="bg-wa-panel rounded-xl shadow-2xl border border-wa-border w-[600px] max-w-[95vw] p-4">
            <${ConversationFilterDialog}
              filters=${advFilters}
              channels=${channels || []}
              agentsUsers=${agentsUsers || []}
              agentsAi=${agentsAi || []}
              tagNames=${tagNames}
              sortBy=${sortBy}
              onSortChange=${onSortChange}
              sortOptions=${SORT_OPTIONS}
              onApply=${onAdvFiltersChange}
              onClose=${() => setAdvOpen(false)}
            />
          </div>
        </div>
      ` : null}
    </div>
  `;
}

export default ConversationFilterBar;
