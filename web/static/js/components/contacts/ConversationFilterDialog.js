// Chatwoot-style "Filtrar conversas" builder (plano 10 FF6+). Opened from the funnel
// icon in the inbox toolbar. Each row is a clause: [dimensão] [operador] [valor] [🗑].
// Dimensions: Status (Aberta/Fechada/Todas), Canais, Agente (atendente humano +
// IA), Etiqueta, Última atividade.
//
// Operators:
//   - Status / Canais / Agente / Etiqueta → "Igual a" (eq) / "Diferente" (ne)
//   - Última atividade → "É maior que" (gt) / "É menor que" (lt) / "É X dias antes"
//     (days_before), todos sobre número de DIAS desde a última atividade.
//
// The dialog only BUILDS the clause list ({dim, op, value}); evaluation is done
// client-side in Contacts.js over the already-fetched rows (instant, AND entre as
// cláusulas). "Aplicar filtros" commits o rascunho; "Limpar filtros" zera tudo.

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const DIMENSIONS = [
  { key: 'status',   label: 'Status',           ops: ['eq', 'ne'],                valueType: 'status' },
  { key: 'channel',  label: 'Canais',           ops: ['eq', 'ne'],                valueType: 'channel' },
  { key: 'agent',    label: 'Agente',           ops: ['eq', 'ne'],                valueType: 'agent' },
  { key: 'tag',      label: 'Etiqueta',         ops: ['eq', 'ne'],                valueType: 'tag' },
  { key: 'activity', label: 'Última atividade', ops: ['gt', 'lt', 'days_before'], valueType: 'days' },
];
const DIM_BY_KEY = Object.fromEntries(DIMENSIONS.map(d => [d.key, d]));
const OP_LABELS = {
  eq: 'Igual a', ne: 'Diferente',
  gt: 'É maior que', lt: 'É menor que', days_before: 'É X dias antes',
};

let _seq = 0;
const newClause = (dim = 'channel') => ({
  id: `f${++_seq}`, dim, op: DIM_BY_KEY[dim].ops[0], value: '',
});

function TrashIcon() {
  return html`<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>`;
}

const FIELD = 'wa-field px-2 py-1.5 rounded-md text-[13px] border border-wa-border';

function ValueInput({ clause, channels, agentsUsers, agentsAi, tagNames, onChange }) {
  const t = DIM_BY_KEY[clause.dim].valueType;
  const cls = `${FIELD} flex-1 min-w-0`;
  if (t === 'status') {
    return html`<select class=${cls} value=${clause.value} onChange=${(e) => onChange(e.target.value)}>
      <option value="">+ Selecione uma opção...</option>
      <option value="open">Aberta</option>
      <option value="closed">Fechada</option>
      <option value="all">Todas</option>
    </select>`;
  }
  if (t === 'channel') {
    return html`<select class=${cls} value=${clause.value} onChange=${(e) => onChange(e.target.value)}>
      <option value="">+ Selecione uma opção...</option>
      ${channels.map(c => html`<option key=${c.id} value=${c.id}>${c.label}</option>`)}
    </select>`;
  }
  if (t === 'agent') {
    return html`<select class=${cls} value=${clause.value} onChange=${(e) => onChange(e.target.value)}>
      <option value="">+ Selecione uma opção...</option>
      <option value="none">— Não atribuído —</option>
      ${agentsUsers.length ? html`<optgroup label="Atendentes">
        ${agentsUsers.map(u => html`<option key=${'u' + u.id} value=${'user:' + u.id}>${u.name}</option>`)}
      </optgroup>` : null}
      ${agentsAi.length ? html`<optgroup label="Agentes de IA">
        ${agentsAi.map(a => html`<option key=${'a' + a.agent_key} value=${'ai:' + a.agent_key}>${a.display_name}</option>`)}
      </optgroup>` : null}
    </select>`;
  }
  if (t === 'tag') {
    return html`<select class=${cls} value=${clause.value} onChange=${(e) => onChange(e.target.value)}>
      <option value="">+ Selecione uma opção...</option>
      ${tagNames.map(n => html`<option key=${n} value=${n}>${n}</option>`)}
    </select>`;
  }
  // days — número de dias desde a última atividade
  return html`<div class="flex items-center gap-1.5 flex-1 min-w-0">
    <input type="number" min="0" step="1" value=${clause.value} placeholder="Inserir valor"
      onInput=${(e) => onChange(e.target.value)}
      class="${FIELD} flex-1 min-w-0" />
    <span class="text-[12px] text-wa-secondary shrink-0">dias</span>
  </div>`;
}

export function ConversationFilterDialog({ filters, channels, agentsUsers, agentsAi, tagNames,
  sortBy, onSortChange, sortOptions, onApply, onClose }) {
  // Local draft so editing rows doesn't re-filter the list until "Aplicar". Mounts
  // fresh each time the popover opens, so seed straight from the applied filters.
  const [draft, setDraft] = useState(() =>
    (filters && filters.length) ? filters.map(f => ({ ...f })) : [newClause()]);

  const patch = (id, changes) => setDraft(d => d.map(c => (c.id === id ? { ...c, ...changes } : c)));
  const changeDim = (id, dim) => patch(id, { dim, op: DIM_BY_KEY[dim].ops[0], value: '' });
  const removeRow = (id) => setDraft(d => (d.length > 1 ? d.filter(c => c.id !== id) : [newClause()]));
  const addRow = () => setDraft(d => [...d, newClause()]);

  const apply = () => {
    onApply(draft.filter(c => c.value !== '' && c.value != null));
    onClose();
  };
  const clear = () => { onApply([]); onClose(); };

  return html`
    <div>
      <div class="text-[15px] font-semibold text-wa-text mb-3">Filtrar conversas</div>
      ${onSortChange ? html`
        <label class="block text-[12px] text-wa-secondary mb-1">Ordenar por</label>
        <select value=${sortBy} onChange=${(e) => onSortChange(e.target.value)}
          class="${FIELD} w-full mb-4">
          ${(sortOptions || []).map(o => html`<option key=${o.value} value=${o.value}>${o.label}</option>`)}
        </select>
        <div class="text-[12px] text-wa-secondary mb-1">Filtros</div>
      ` : null}
      <div class="flex flex-col gap-2 mb-3">
        ${draft.map((c) => {
          const dim = DIM_BY_KEY[c.dim];
          return html`<div key=${c.id} class="flex items-center gap-1.5">
            <select class="${FIELD} shrink-0 w-[130px]" value=${c.dim} onChange=${(e) => changeDim(c.id, e.target.value)}>
              ${DIMENSIONS.map(d => html`<option key=${d.key} value=${d.key}>${d.label}</option>`)}
            </select>
            <select class="${FIELD} shrink-0 w-[120px]" value=${c.op} onChange=${(e) => patch(c.id, { op: e.target.value })}>
              ${dim.ops.map(op => html`<option key=${op} value=${op}>${OP_LABELS[op]}</option>`)}
            </select>
            <${ValueInput} clause=${c} channels=${channels} agentsUsers=${agentsUsers}
              agentsAi=${agentsAi} tagNames=${tagNames} onChange=${(v) => patch(c.id, { value: v })} />
            <button onClick=${() => removeRow(c.id)} title="Remover filtro"
              class="shrink-0 w-[30px] h-[30px] flex items-center justify-center rounded-md text-wa-secondary hover:bg-wa-hover hover:text-red-400 transition-colors">
              <${TrashIcon} />
            </button>
          </div>`;
        })}
      </div>
      <button onClick=${addRow} class="text-[13px] text-wa-teal hover:underline mb-3">+ Adicionar filtro</button>
      <div class="flex items-center justify-end gap-2 pt-2 border-t border-wa-border">
        <button onClick=${clear}
          class="px-2.5 py-1 rounded-md text-[12px] text-wa-secondary hover:bg-wa-hover transition-colors">Limpar filtros</button>
        <button onClick=${apply}
          class="px-3 py-1 rounded-md text-[12px] bg-wa-teal text-white hover:bg-wa-tealDark transition-colors">Aplicar filtros</button>
      </div>
    </div>
  `;
}

export default ConversationFilterDialog;
