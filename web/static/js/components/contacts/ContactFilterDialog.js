// "Filtrar contatos" — construtor de filtros da tela Contatos (full-page), espelhando
// o "Filtrar atendimentos" da sidebar, mas restrito ao que faz sentido para um CONTATO:
//   - Etiqueta (tag) — multi-select; eq = "é uma de" / ne = "não é nenhuma".
//   - Atributos personalizados do CONTATO (plano 05) — dimensões dinâmicas que
//     aparecem conforme cadastradas em Configurações → Atributos personalizados.
//
// O diálogo apenas CONSTRÓI a lista de cláusulas ({dim, op, value}); a avaliação é
// client-side via matchesAdvFilters/clauseMatches (services/conversationRows.js)
// sobre os contatos já carregados — cada contato carrega `tags` e `custom_attributes`,
// então `tag` e `cattr:contact:<key>` casam direto, sem backend novo. As dimensões de
// atributo são codificadas como `cattr:contact:<attribute_key>`.

import { h } from 'preact';
import { useState, useRef, useEffect, useMemo } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// Apenas Etiqueta é dimensão core. Email/Profissão/Empresa/Endereço NÃO são
// hardcoded — vêm dinamicamente como atributos personalizados do contato (são
// seeds padrão), via `contactAttrDefs`.
const CORE_DIMENSIONS = [
  { key: 'tag', label: 'Etiqueta do contato', ops: ['eq', 'ne'], valueType: 'tag' },
];
const CORE_BY_KEY = Object.fromEntries(CORE_DIMENSIONS.map(d => [d.key, d]));

const OP_LABELS = {
  eq: 'Igual a', ne: 'Diferente',
  gt: 'É maior que', lt: 'É menor que',
  contains: 'Contém', not_contains: 'Não contém',
};

// Mapa tipo-de-atributo → {valueType de input, operadores oferecidos}.
const ATTR_TYPE_MAP = {
  list:     { valueType: 'attr_list',   ops: ['eq', 'ne'] },
  checkbox: { valueType: 'attr_bool',   ops: ['eq'] },
  number:   { valueType: 'attr_number', ops: ['eq', 'ne', 'gt', 'lt'] },
  date:     { valueType: 'attr_date',   ops: ['eq', 'ne', 'gt', 'lt'] },
  text:     { valueType: 'attr_text',   ops: ['contains', 'not_contains', 'eq', 'ne'] },
  link:     { valueType: 'attr_text',   ops: ['contains', 'not_contains', 'eq', 'ne'] },
};

const MULTI_TYPES = new Set(['tag', 'attr_list']);
const isMultiType = (valueType) => MULTI_TYPES.has(valueType);
const emptyValueFor = (dimDesc) => (dimDesc && isMultiType(dimDesc.valueType) ? [] : '');
const isEmptyValue = (v) => v == null || v === '' || (Array.isArray(v) && v.length === 0);
const asList = (v) => (Array.isArray(v) ? v : (v == null || v === '' ? [] : [v]));

// Constrói a dimensão de filtro de um atributo personalizado de contato.
function attrDim(def) {
  const t = (def.type || 'text').toLowerCase();
  const m = ATTR_TYPE_MAP[t] || ATTR_TYPE_MAP.text;
  return {
    key: `cattr:contact:${def.attribute_key}`,
    label: def.display_name || def.attribute_key,
    ops: m.ops,
    valueType: m.valueType,
    attrDef: def,
    group: 'contact',
  };
}

let _seq = 0;
const newClause = (dim = 'tag') => {
  const d = CORE_BY_KEY[dim] || CORE_BY_KEY.tag;
  return { id: `cf${++_seq}`, dim: d.key, op: d.ops[0], value: emptyValueFor(d) };
};

function TrashIcon() {
  return html`<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>`;
}
function ChevronIcon({ open = false }) {
  return html`<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"
    class="shrink-0 text-wa-secondary transition-transform ${open ? 'rotate-180' : ''}"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>`;
}

const FIELD = 'wa-field px-2 py-1.5 rounded-md text-[13px] border border-wa-border';

function useCloseOnOutside(open, setOpen, ref) {
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);
}

// Dropdown custom de multi-seleção (checkboxes) — evita o `<select multiple>` nativo.
function MultiSelect({ options, selected, onChange, placeholder = '+ Selecione uma opção...' }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useCloseOnOutside(open, setOpen, ref);

  const sel = new Set(selected || []);
  const toggle = (v) => {
    const next = new Set(sel);
    if (next.has(v)) next.delete(v); else next.add(v);
    onChange(options.filter(o => next.has(o.value)).map(o => o.value));
  };

  const chosen = options.filter(o => sel.has(o.value));
  const summary = chosen.length === 0
    ? placeholder
    : (chosen.length <= 2 ? chosen.map(o => o.label).join(', ') : `${chosen.length} selecionados`);

  return html`<div ref=${ref} class="relative flex-1 min-w-0">
    <button type="button" onClick=${() => setOpen(o => !o)}
      class="${FIELD} w-full flex items-center justify-between gap-1.5 text-left">
      <span class="truncate ${chosen.length === 0 ? 'text-wa-secondary' : ''}">${summary}</span>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="shrink-0 text-wa-secondary"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
    </button>
    ${open ? html`<div class="absolute z-[80] mt-1 left-0 right-0 max-h-[220px] overflow-y-auto bg-wa-panel rounded-md shadow-lg border border-wa-border py-1">
      ${options.length === 0 ? html`<div class="px-2.5 py-1.5 text-[13px] text-wa-secondary">Nenhuma opção</div>` : null}
      ${options.map(o => html`<label key=${o.value}
        class="flex items-center gap-2 px-2.5 py-1.5 text-[13px] text-wa-text hover:bg-wa-hover cursor-pointer">
        <input type="checkbox" checked=${sel.has(o.value)} onChange=${() => toggle(o.value)} class="shrink-0" />
        <span class="truncate">${o.label}</span>
      </label>`)}
    </div>` : null}
  </div>`;
}

// Seletor de DIMENSÃO: dimensões core no topo + accordion recolhível "Atributos do
// contato" (recolhido por padrão) para manter a lista limpa quando há muitos atributos.
function DimensionPicker({ dimensions, value, onChange }) {
  const [open, setOpen] = useState(false);
  const [openContact, setOpenContact] = useState(false);
  const ref = useRef(null);
  useCloseOnOutside(open, setOpen, ref);

  const core = dimensions.filter(d => !d.group);
  const contactDims = dimensions.filter(d => d.group === 'contact');
  const current = dimensions.find(d => d.key === value);
  const pick = (key) => { onChange(key); setOpen(false); };

  const row = (d) => html`<button type="button" key=${d.key} onClick=${() => pick(d.key)}
    class="w-full text-left px-2.5 py-1.5 text-[13px] hover:bg-wa-hover transition-colors ${d.key === value ? 'text-wa-teal font-medium' : 'text-wa-text'}">
    ${d.label}
  </button>`;

  return html`<div ref=${ref} class="relative shrink-0 w-[150px]">
    <button type="button" onClick=${() => setOpen(o => !o)}
      class="${FIELD} w-full flex items-center justify-between gap-1.5 text-left">
      <span class="truncate">${current ? current.label : 'Selecione...'}</span>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="shrink-0 text-wa-secondary"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
    </button>
    ${open ? html`<div class="absolute z-[80] mt-1 left-0 w-[240px] max-h-[320px] overflow-y-auto bg-wa-panel rounded-md shadow-lg border border-wa-border py-1">
      ${core.map(row)}
      ${contactDims.length === 0 ? null : html`<div class="border-t border-wa-border mt-1 pt-1">
        <button type="button" onClick=${() => setOpenContact(o => !o)}
          class="w-full flex items-center justify-between gap-1.5 px-2.5 py-1.5 text-[12px] font-semibold uppercase tracking-wide text-wa-secondary hover:bg-wa-hover transition-colors">
          <span>Atributos do contato (${contactDims.length})</span>
          <${ChevronIcon} open=${openContact} />
        </button>
        ${openContact ? html`<div>${contactDims.map(row)}</div>` : null}
      </div>`}
    </div>` : null}
  </div>`;
}

function ValueInput({ clause, dimDesc, tagNames, onChange }) {
  const t = dimDesc.valueType;
  const cls = `${FIELD} flex-1 min-w-0`;
  if (t === 'tag') {
    const options = tagNames.map(n => ({ value: n, label: n }));
    return html`<${MultiSelect} options=${options} selected=${asList(clause.value)} onChange=${onChange} />`;
  }
  if (t === 'attr_list') {
    const options = ((dimDesc.attrDef && dimDesc.attrDef.options) || []).map(o => ({ value: o, label: o }));
    return html`<${MultiSelect} options=${options} selected=${asList(clause.value)} onChange=${onChange} />`;
  }
  if (t === 'attr_bool') {
    return html`<select class=${cls} value=${clause.value} onChange=${(e) => onChange(e.target.value)}>
      <option value="">+ Selecione uma opção...</option>
      <option value="true">Sim</option>
      <option value="false">Não</option>
    </select>`;
  }
  if (t === 'attr_number') {
    return html`<input type="number" step="any" value=${clause.value} placeholder="Inserir valor"
      onInput=${(e) => onChange(e.target.value)} class=${cls} />`;
  }
  if (t === 'attr_date') {
    return html`<input type="date" value=${clause.value}
      onInput=${(e) => onChange(e.target.value)} class=${cls} />`;
  }
  // attr_text
  return html`<input type="text" value=${clause.value} placeholder="Inserir valor"
    onInput=${(e) => onChange(e.target.value)} class=${cls} />`;
}

export function ContactFilterDialog({ filters, tagNames = [], contactAttrDefs = [], onApply, onClose }) {
  const dimensions = useMemo(() => [
    ...CORE_DIMENSIONS,
    ...(contactAttrDefs || []).map(d => attrDim(d)),
  ], [contactAttrDefs]);
  const dimByKey = useMemo(() => Object.fromEntries(dimensions.map(d => [d.key, d])), [dimensions]);

  // Draft local: editar as linhas não re-filtra até "Aplicar".
  const [draft, setDraft] = useState(() =>
    (filters && filters.length) ? filters.map(f => ({ ...f })) : [newClause()]);

  const patch = (id, changes) => setDraft(d => d.map(c => (c.id === id ? { ...c, ...changes } : c)));
  const changeDim = (id, dimKey) => {
    const d = dimByKey[dimKey] || CORE_BY_KEY.tag;
    patch(id, { dim: d.key, op: d.ops[0], value: emptyValueFor(d) });
  };
  const removeRow = (id) => setDraft(d => (d.length > 1 ? d.filter(c => c.id !== id) : [newClause()]));
  const addRow = () => setDraft(d => [...d, newClause()]);

  const apply = () => { onApply(draft.filter(c => !isEmptyValue(c.value))); onClose(); };
  const clear = () => { onApply([]); onClose(); };

  return html`
    <div>
      <div class="text-[15px] font-semibold text-wa-text mb-3">Filtrar contatos</div>
      <div class="text-[12px] text-wa-secondary mb-1">Filtros</div>
      <div class="flex flex-col gap-2 mb-3">
        ${draft.map((c) => {
          const dim = dimByKey[c.dim] || CORE_BY_KEY.tag;
          return html`<div key=${c.id} class="flex items-center gap-1.5">
            <${DimensionPicker} dimensions=${dimensions} value=${c.dim} onChange=${(k) => changeDim(c.id, k)} />
            <select class="${FIELD} shrink-0 w-[120px]" value=${c.op} onChange=${(e) => patch(c.id, { op: e.target.value })}>
              ${dim.ops.map(op => html`<option key=${op} value=${op}>${OP_LABELS[op]}</option>`)}
            </select>
            <${ValueInput} clause=${c} dimDesc=${dim} tagNames=${tagNames} onChange=${(v) => patch(c.id, { value: v })} />
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
