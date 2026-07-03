// Conversation filter bar (plano 10 FF2/FF6) — sits right below the search box in
// the main inbox. Layout:
//   - status chip (Abertas/Resolvidas/Todas)
//   - saved-filters bookmark → popover: apply / rename / delete named presets
//   - save (disk) icon → "Salvar filtro" dialog (only when a filter is active)
//   - clear (X) icon → reset all filters (only when a filter is active)
//   - RIGHT icon (tune) → modal "Filtrar atendimentos" estilo Chatwoot: construtor de
//     filtros (Canais / Agente / Etiqueta / Última atividade) + Ordenar por
//   - assignment tabs (Minhas / Não atribuídas / Todas) com contagem ao vivo
//   - active-preset chip (canto): mostra qual filtro salvo está em uso
//
// All filtering is client-side over the already-fetched (enriched) contact list,
// so switching tabs is instant. State is owned by Contacts.js and passed in. The
// advanced builder is a centered modal (not an anchored popover) porque a sidebar
// tem overflow-hidden e cortaria um popover largo. Saved presets are persisted
// per user (Chatwoot-style) via /api/me/conversation-filters.

import { h } from 'preact';
import { useState, useRef, useEffect } from 'preact/hooks';
import htm from 'htm';
import { ConversationFilterDialog } from './ConversationFilterDialog.js';
import { getCustomAttributes, getConversationLabels } from '../../services/api.js';

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

// ── Removable per-dimension filter chips (plano 10 FF6) ─────────────────────────
// Each active filter dimension renders as its own chip with an individual ✕, so the
// operator can drop one filter without reopening a dropdown/modal. Labels reuse the
// same friendly names the advanced dialog shows (status/channel/agent/tag/activity).
const DIM_LABELS = { status: 'Status', channel: 'Canal', agent: 'Agente', tag: 'Etiqueta do contato', conv_label: 'Etiqueta da conversa', activity: 'Atividade' };

function _channelLabel(channels, value) {
  const ch = (channels || []).find(c => String(c.id) === String(value));
  return ch ? ch.label : value;
}
function _agentLabel(agentsUsers, agentsAi, value) {
  if (value === 'none') return 'Não atribuído';
  if (typeof value === 'string' && value.startsWith('user:')) {
    const u = (agentsUsers || []).find(x => String(x.id) === value.slice(5));
    return u ? (u.name || u.email || value) : value;
  }
  if (typeof value === 'string' && value.startsWith('ai:')) {
    const a = (agentsAi || []).find(x => x.agent_key === value.slice(3));
    return a ? (a.display_name || value) : value;
  }
  return value;
}
// Rótulo amigável do chip para uma dimensão de atributo personalizado
// (cattr:<scope>:<key>): "Contato · Plano" / "Atendimento · Plano" pra desambiguar
// quando o mesmo key existe nos dois escopos. Usa as defs carregadas para o nome.
function _cattrLabel(cl, attrDefs) {
  const m = String(cl.dim).match(/^cattr:(contact|conversation):(.+)$/);
  if (!m) return null;
  const scope = m[1], key = m[2];
  const def = (attrDefs || []).find(d => d.attribute_key === key && d.applies_to === scope);
  const name = def ? (def.display_name || key) : key;
  const prefix = scope === 'contact' ? 'Contato · ' : 'Conversa · ';
  return prefix + name;
}
function advClauseLabel(cl, channels, agentsUsers, agentsAi, attrDefs) {
  const cattrLabel = _cattrLabel(cl, attrDefs);
  const dimLabel = cattrLabel || DIM_LABELS[cl.dim] || cl.dim;
  if (cl.dim === 'activity') {
    const days = `${cl.value} dia${String(cl.value) === '1' ? '' : 's'}`;
    if (cl.op === 'gt') return `Atividade: há mais de ${days}`;
    if (cl.op === 'lt') return `Atividade: há menos de ${days}`;
    return `Atividade: há ${days}`;
  }
  // channel/agent/tag e atributos `list` são multi-select (lista). Rotula cada valor.
  const labelOne = (v) => {
    if (cl.dim === 'status') return STATUS_LABELS[v] || v;
    if (cl.dim === 'channel') return _channelLabel(channels, v);
    if (cl.dim === 'agent') return _agentLabel(agentsUsers, agentsAi, v);
    return v;   // tag / atributo personalizado
  };
  const list = Array.isArray(cl.value) ? cl.value : [cl.value];
  const val = list.map(labelOne).join(', ');
  const OP_SEP = { ne: ' ≠ ', contains: ' contém ', not_contains: ' não contém ', gt: ' > ', lt: ' < ' };
  const sep = OP_SEP[cl.op] || ': ';
  return `${dimLabel}${sep}${val}`;
}

function TuneIcon() {
  return html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"/></svg>`;
}
function ChevronDown() {
  return html`<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>`;
}
// Disk / "save" icon (matches the Chatwoot save-filter affordance).
function SaveIcon() {
  return html`<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor">
    <path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm2 16H5V5h11.17L19 7.83V19zm-7-7c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3zM6 6h9v4H6V6z"/></svg>`;
}
function BookmarkIcon() {
  return html`<svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor">
    <path d="M17 3H7c-1.1 0-2 .9-2 2v16l7-3 7 3V5c0-1.1-.9-2-2-2z"/></svg>`;
}
function CloseIcon() {
  return html`<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>`;
}
function TrashIcon() {
  return html`<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>`;
}
function PencilIcon() {
  return html`<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>`;
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

// Centered "Salvar filtro" dialog — name the current filter (create) or rename an
// existing preset. Mirrors the Chatwoot "Você quer salvar este filtro?" card.
function SaveFilterDialog({ title, initialName, confirmLabel, onConfirm, onClose }) {
  const [name, setName] = useState(initialName || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);
  useEffect(() => { if (inputRef.current) inputRef.current.focus(); }, []);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) { setError('Dê um nome ao filtro.'); return; }
    setSaving(true); setError(null);
    const res = await onConfirm(trimmed);
    setSaving(false);
    if (res && res.ok) onClose();
    else setError((res && res.error) || 'Não foi possível salvar.');
  };

  return html`
    <div class="fixed inset-0 z-[90] flex items-start justify-center bg-black/40 px-4 pt-[14vh]"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="bg-wa-panel rounded-xl shadow-2xl border border-wa-border w-[420px] max-w-[95vw] p-5">
        <div class="text-[15px] font-semibold text-wa-text mb-3">${title}</div>
        <input
          ref=${inputRef}
          value=${name}
          maxlength="60"
          onInput=${(e) => setName(e.target.value)}
          onKeyDown=${(e) => { if (e.key === 'Enter') submit(); }}
          placeholder="Nomeie seu filtro para referenciá-lo posteriormente."
          class="wa-field w-full px-3 py-2 rounded-md text-[13px] border border-wa-border"
        />
        ${error ? html`<div class="text-[12px] text-red-500 mt-2">${error}</div>` : null}
        <div class="flex items-center justify-end gap-2 mt-4">
          <button onClick=${onClose} disabled=${saving}
            class="px-3 py-1.5 rounded-md text-[13px] text-wa-secondary hover:bg-wa-hover transition-colors">Cancelar</button>
          <button onClick=${submit} disabled=${saving}
            class="px-4 py-1.5 rounded-md text-[13px] bg-wa-teal text-white hover:bg-wa-tealDark transition-colors disabled:opacity-60">
            ${saving ? 'Salvando…' : (confirmLabel || 'Salvar filtro')}</button>
        </div>
      </div>
    </div>
  `;
}

// Centered confirmation dialog for deleting a saved filter — asks before removing.
function ConfirmDeleteDialog({ name, onConfirm, onClose }) {
  const [deleting, setDeleting] = useState(false);
  const submit = async () => {
    setDeleting(true);
    await onConfirm();
    setDeleting(false);
    onClose();
  };
  return html`
    <div class="fixed inset-0 z-[95] flex items-start justify-center bg-black/40 px-4 pt-[16vh]"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="bg-wa-panel rounded-xl shadow-2xl border border-wa-border w-[400px] max-w-[95vw] p-5">
        <div class="text-[15px] font-semibold text-wa-text mb-2">Excluir filtro</div>
        <div class="text-[13px] text-wa-secondary mb-4">
          Tem certeza que deseja excluir o filtro ${name ? html`"${name}"` : 'salvo'}? Esta ação não pode ser desfeita.
        </div>
        <div class="flex items-center justify-end gap-2">
          <button onClick=${onClose} disabled=${deleting}
            class="px-3 py-1.5 rounded-md text-[13px] text-wa-secondary hover:bg-wa-hover transition-colors">Cancelar</button>
          <button onClick=${submit} disabled=${deleting}
            class="px-4 py-1.5 rounded-md text-[13px] bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-60">
            ${deleting ? 'Excluindo…' : 'Excluir'}</button>
        </div>
      </div>
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
  // Saved presets
  savedFilters = [], activeFilter = null, anyFilterActive = false,
  onApplySavedFilter, onSaveCurrentFilter, onOverwriteSavedFilter,
  onRenameSavedFilter, onRemoveSavedFilter, onClearFilters,
}) {
  const [statusOpen, setStatusOpen] = useState(false);
  const [advOpen, setAdvOpen] = useState(false);         // modal avançado (direita)
  const [savedOpen, setSavedOpen] = useState(false);     // popover de filtros salvos
  const [saveDialog, setSaveDialog] = useState(null);    // { mode: 'create'|'rename', id?, name? } | null
  const [confirmDelete, setConfirmDelete] = useState(null); // { id, name } | null — confirmação de exclusão
  // Definições de atributos personalizados (plano 05) — viram dimensões de filtro
  // dinâmicas no construtor. Recarrega ao ouvir o evento global de mudança.
  const [contactAttrDefs, setContactAttrDefs] = useState([]);
  const [convAttrDefs, setConvAttrDefs] = useState([]);
  // Etiquetas do atendimento (registro próprio, separado das tags de contato) → opções
  // da dimensão "Etiqueta do atendimento". Recarrega ao abrir o modal avançado.
  const [convLabelNames, setConvLabelNames] = useState([]);
  useEffect(() => {
    let alive = true;
    const load = () => {
      getCustomAttributes('contact').then(r => { if (alive && r && r.ok && Array.isArray(r.data)) setContactAttrDefs(r.data); }).catch(() => {});
      getCustomAttributes('conversation').then(r => { if (alive && r && r.ok && Array.isArray(r.data)) setConvAttrDefs(r.data); }).catch(() => {});
      getConversationLabels().then(r => { if (alive && r && r.ok && Array.isArray(r.data)) setConvLabelNames(r.data.map(l => l.name)); }).catch(() => {});
    };
    load();
    window.addEventListener('whatsbot:custom-attributes-changed', load);
    return () => { alive = false; window.removeEventListener('whatsbot:custom-attributes-changed', load); };
  }, []);
  // Refresca as etiquetas do atendimento todo vez que o modal avançado abre (o registro
  // pode ter mudado em outra tela desde o load inicial).
  useEffect(() => {
    if (!advOpen) return;
    let alive = true;
    getConversationLabels().then(r => { if (alive && r && r.ok && Array.isArray(r.data)) setConvLabelNames(r.data.map(l => l.name)); }).catch(() => {});
    return () => { alive = false; };
  }, [advOpen]);
  const allAttrDefs = [...contactAttrDefs, ...convAttrDefs];

  const tagNames = Object.keys(globalTags || {});
  const advCount = (advFilters || []).length;
  const advActive = advCount > 0;
  const presets = savedFilters || [];

  // One removable chip per active filter dimension. Each ✕ drops only that filter,
  // reusing the setters already owned by Contacts.js (no clear-all).
  const filterChips = [];
  if (statusFilter && statusFilter !== 'open') {
    filterChips.push({
      key: 'status',
      label: `Status: ${STATUS_LABELS[statusFilter] || statusFilter}`,
      onRemove: () => onStatusChange('open'),
    });
  }
  (tagFilter || []).forEach(t => filterChips.push({
    key: `tag:${t}`,
    label: `Etiqueta: ${t}`,
    onRemove: () => onTagFilterChange((tagFilter || []).filter(x => x !== t)),
  }));
  (advFilters || []).forEach(cl => {
    // incomplete clause — not applied (escalar vazio OU multi-select sem seleção)
    if (cl.value === '' || cl.value == null || (Array.isArray(cl.value) && cl.value.length === 0)) return;
    filterChips.push({
      key: `adv:${cl.id}`,
      label: advClauseLabel(cl, channels, agentsUsers, agentsAi, allAttrDefs),
      onRemove: () => onAdvFiltersChange((advFilters || []).filter(c => c.id !== cl.id)),
    });
  });

  // Escape fecha os modais centralizados (avançado / filtros salvos).
  useEffect(() => {
    if (!advOpen && !savedOpen) return;
    function onKey(e) { if (e.key === 'Escape') { setAdvOpen(false); setSavedOpen(false); } }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [advOpen, savedOpen]);

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
            onClick=${() => { setStatusOpen(o => !o); }}
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
          <!-- Filtros salvos (bookmark) -->
          <div class="relative">
            <button
              onClick=${() => { setSavedOpen(o => !o); setStatusOpen(false); }}
              class="w-[32px] h-[32px] rounded-md flex items-center justify-center transition-colors ${activeFilter ? 'bg-wa-teal/15 text-wa-teal' : 'text-wa-secondary hover:bg-wa-hover'}"
              title="Filtros salvos"
            ><${BookmarkIcon} />${presets.length ? html`<span class="absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] px-1 rounded-full bg-wa-secondary text-white text-[10px] font-semibold flex items-center justify-center">${presets.length}</span>` : null}</button>
          </div>

          <!-- Salvar filtro atual (disk) — só quando há filtro ativo -->
          ${anyFilterActive ? html`
            <button
              onClick=${() => { setSaveDialog(activeFilter ? { mode: 'update', id: activeFilter.id, name: activeFilter.name } : { mode: 'create' }); setStatusOpen(false); }}
              class="w-[32px] h-[32px] rounded-md flex items-center justify-center text-wa-secondary hover:bg-wa-hover transition-colors"
              title=${activeFilter ? 'Atualizar/Salvar filtro' : 'Salvar este filtro'}
            ><${SaveIcon} /></button>
          ` : null}

          <!-- Limpar filtros (X) — só quando há filtro ativo -->
          ${anyFilterActive ? html`
            <button
              onClick=${() => onClearFilters && onClearFilters()}
              class="w-[32px] h-[32px] rounded-md flex items-center justify-center text-wa-secondary hover:bg-wa-hover hover:text-red-400 transition-colors"
              title="Limpar filtros"
            ><${CloseIcon} /></button>
          ` : null}

          <!-- Filtro avançado (modal) — Canais / Agente / Etiqueta / Última atividade + Ordenar por -->
          <div class="relative">
            <button
              onClick=${() => { setAdvOpen(true); setStatusOpen(false); }}
              class="w-[32px] h-[32px] rounded-md flex items-center justify-center transition-colors ${advActive ? 'bg-wa-teal/15 text-wa-teal' : 'text-wa-secondary hover:bg-wa-hover'}"
              title="Filtros avançados e ordenação"
            ><${TuneIcon} />${advActive ? html`<span class="absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] px-1 rounded-full bg-wa-teal text-white text-[10px] font-semibold flex items-center justify-center">${advCount}</span>` : null}</button>
          </div>
        </div>
      </div>

      <!-- Chips de filtros ativos: preset salvo (✕ remove o preset/limpa tudo) +
           um chip removível POR dimensão (✕ remove só aquela). -->
      ${(activeFilter || filterChips.length > 0) ? html`
        <div class="flex flex-wrap items-center gap-1.5 px-[12px] pt-1.5">
          ${activeFilter ? html`
            <span class="inline-flex items-center gap-1.5 max-w-full text-[12px] bg-wa-teal/15 text-wa-teal rounded-full pl-2 pr-1 py-0.5">
              <${BookmarkIcon} />
              <span class="truncate font-medium" title=${activeFilter.name}>${activeFilter.name}</span>
              ${activeFilter.modified ? html`<span class="text-[11px] text-wa-secondary">(modificado)</span>` : null}
              <button onClick=${() => onClearFilters && onClearFilters()} title="Remover filtro salvo (limpa tudo)"
                class="w-[18px] h-[18px] flex items-center justify-center rounded-full hover:bg-wa-teal/20 transition-colors">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
              </button>
            </span>
            ${activeFilter.modified ? html`
              <button onClick=${async () => { await onOverwriteSavedFilter(activeFilter.id); }}
                class="text-[12px] text-wa-teal hover:underline">Atualizar</button>
            ` : null}
          ` : null}
          ${filterChips.map(chip => html`
            <span key=${chip.key}
              class="inline-flex items-center gap-1 max-w-full text-[12px] bg-wa-hover text-wa-text rounded-full pl-2.5 pr-1 py-0.5 border border-wa-border">
              <span class="truncate max-w-[180px]" title=${chip.label}>${chip.label}</span>
              <button onClick=${chip.onRemove} title="Remover este filtro"
                class="shrink-0 w-[16px] h-[16px] flex items-center justify-center rounded-full text-wa-secondary hover:bg-wa-border hover:text-red-400 transition-colors">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
              </button>
            </span>
          `)}
        </div>
      ` : null}

      <!-- Assignment tabs -->
      <div class="flex items-center gap-1 px-[12px] mt-1 border-b border-wa-border overflow-x-auto wa-scrollbar">
        ${hasIdentity ? tabBtn('mine', 'Minhas', counts.mine) : null}
        ${tabBtn('unassigned', 'Não atribuídas', counts.unassigned)}
        ${tabBtn('all', 'Todas', counts.all)}
      </div>

      <!-- Modal "Filtros salvos" — centered (fora do overflow-hidden da sidebar) -->
      ${savedOpen ? html`
        <div class="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 px-4 pt-[14vh]"
          onClick=${(e) => { if (e.target === e.currentTarget) setSavedOpen(false); }}>
          <div class="bg-wa-panel rounded-xl shadow-2xl border border-wa-border w-[380px] max-w-[95vw] p-4">
            <div class="flex items-center justify-between mb-3">
              <div class="text-[15px] font-semibold text-wa-text">Filtros salvos</div>
              <button onClick=${() => setSavedOpen(false)} title="Fechar"
                class="w-[28px] h-[28px] flex items-center justify-center rounded-md text-wa-secondary hover:bg-wa-hover transition-colors">
                <${CloseIcon} /></button>
            </div>
            ${presets.length === 0 ? html`
              <div class="text-[13px] text-wa-secondary py-2">
                Nenhum filtro salvo ainda. Configure um filtro e clique no ícone de salvar.
              </div>
            ` : html`
              <div class="flex flex-col gap-0.5 max-h-[50vh] overflow-y-auto wa-scrollbar -mx-1">
                ${presets.map(p => {
                  const on = activeFilter && activeFilter.id === p.id;
                  return html`<div key=${p.id}
                    class="group flex items-center gap-1 rounded-md px-1 ${on ? 'bg-wa-teal/10' : 'hover:bg-wa-hover'}">
                    <button
                      onClick=${() => { onApplySavedFilter(p); setSavedOpen(false); }}
                      class="flex-1 text-left px-1.5 py-2 text-[13px] truncate ${on ? 'text-wa-teal font-medium' : 'text-wa-text'}"
                      title=${p.name}
                    >${on ? '✓ ' : ''}${p.name}</button>
                    <button onClick=${() => setSaveDialog({ mode: 'rename', id: p.id, name: p.name })}
                      title="Renomear"
                      class="shrink-0 w-[28px] h-[28px] flex items-center justify-center rounded text-wa-secondary hover:text-wa-text hover:bg-wa-hover transition-colors">
                      <${PencilIcon} /></button>
                    <button onClick=${() => setConfirmDelete({ id: p.id, name: p.name })}
                      title="Excluir filtro"
                      class="shrink-0 w-[28px] h-[28px] flex items-center justify-center rounded text-wa-secondary hover:text-red-400 hover:bg-wa-hover transition-colors">
                      <${TrashIcon} /></button>
                  </div>`;
                })}
              </div>
            `}
            ${anyFilterActive ? html`
              <button
                onClick=${() => { setSaveDialog({ mode: 'create' }); setSavedOpen(false); }}
                class="w-full mt-3 pt-3 border-t border-wa-border text-[13px] text-wa-teal hover:underline text-left px-1.5">
                + Salvar filtro atual
              </button>
            ` : null}
          </div>
        </div>
      ` : null}

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
              convLabelNames=${convLabelNames}
              contactAttrDefs=${contactAttrDefs}
              convAttrDefs=${convAttrDefs}
              sortBy=${sortBy}
              onSortChange=${onSortChange}
              sortOptions=${SORT_OPTIONS}
              onApply=${onAdvFiltersChange}
              onClose=${() => setAdvOpen(false)}
            />
          </div>
        </div>
      ` : null}

      <!-- Dialog "Salvar filtro" (criar / atualizar / renomear) -->
      ${saveDialog ? html`
        <${SaveFilterDialog}
          title=${saveDialog.mode === 'rename' ? 'Renomear filtro'
            : saveDialog.mode === 'update' ? 'Atualizar filtro' : 'Você quer salvar este filtro?'}
          initialName=${saveDialog.name || ''}
          confirmLabel=${saveDialog.mode === 'rename' ? 'Renomear'
            : saveDialog.mode === 'update' ? 'Atualizar' : 'Salvar filtro'}
          onConfirm=${async (name) => {
            if (saveDialog.mode === 'rename') return onRenameSavedFilter(saveDialog.id, name);
            if (saveDialog.mode === 'update') {
              // "Atualizar" sobre o preset ativo: renomeia se mudou + sobrescreve a spec.
              const r = await onOverwriteSavedFilter(saveDialog.id);
              if (r && r.ok && name && activeFilter && name !== activeFilter.name) {
                return onRenameSavedFilter(saveDialog.id, name);
              }
              return r;
            }
            return onSaveCurrentFilter(name);
          }}
          onClose=${() => setSaveDialog(null)}
        />
      ` : null}

      <!-- Confirmação de exclusão de filtro salvo -->
      ${confirmDelete ? html`
        <${ConfirmDeleteDialog}
          name=${confirmDelete.name}
          onConfirm=${async () => { await onRemoveSavedFilter(confirmDelete.id); }}
          onClose=${() => setConfirmDelete(null)}
        />
      ` : null}
    </div>
  `;
}

