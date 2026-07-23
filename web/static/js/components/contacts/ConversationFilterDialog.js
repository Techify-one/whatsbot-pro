// Chatwoot-style "Filtrar atendimentos" builder (plano 10 FF6+). Opened from the funnel
// icon in the inbox toolbar. Each row is a clause: [dimensão] [operador] [valor] [🗑].
// Dimensions: Status (Aberta/Fechada/Todas), Canais, Agente (atendente humano +
// IA), Etiqueta, Última atividade — MAIS os atributos personalizados (dinâmicos),
// de contato e de atendimento, que aparecem conforme cadastrados (plano 05).
//
// Operators:
//   - Status / Canais / Agente / Etiqueta → "Igual a" (eq) / "Diferente" (ne)
//   - Última atividade → "É maior que" (gt) / "É menor que" (lt) / "É X dias antes"
//     (days_before), todos sobre número de DIAS desde a última atividade.
//   - Atributo personalizado → varia pelo tipo (list/checkbox/number/date/text).
//
// Canais / Agente / Etiqueta e atributos do tipo `list` são MULTI-SELECT (o valor
// vira uma lista; eq = "é uma de" / ne = "não é nenhuma"). As demais dimensões são
// escalares. The dialog only BUILDS the clause list ({dim, op, value}); evaluation
// is done client-side in conversationRows.clauseMatches over the already-fetched
// rows (instant, AND entre as cláusulas). Dimensões de atributo são codificadas
// como `cattr:<scope>:<attribute_key>` (scope ∈ contact|conversation).

import { h } from 'preact';
import { useState, useRef, useEffect, useMemo } from 'preact/hooks';
import htm from 'htm';
import { OptionListSelect } from '../OptionListSelect.js';
import { contactTypeOrder, contactTypeMeta } from '../../services/contactTypes.js';
import { useProviderCatalog } from '../../hooks/useProviderCatalog.js';
import { splitSort, combineSort } from '../../services/conversationRows.js';

const html = htm.bind(h);

const CORE_DIMENSIONS = [
  { key: 'status',       label: 'Status',           ops: ['eq', 'ne'],                valueType: 'status' },
  { key: 'channel',      label: 'Canais',           ops: ['eq', 'ne'],                valueType: 'channel' },
  { key: 'contact_type', label: 'Tipo de contato',  ops: ['eq', 'ne'],                valueType: 'contact_type' },
  { key: 'agent',      label: 'Agente',              ops: ['eq', 'ne'],                valueType: 'agent' },
  { key: 'tag',        label: 'Etiqueta do contato', ops: ['eq', 'ne'],                valueType: 'tag' },
  { key: 'conv_label', label: 'Etiqueta da conversa', ops: ['eq', 'ne'],               valueType: 'conv_label' },
  { key: 'ai',         label: 'IA',                  ops: ['eq', 'ne'],                valueType: 'ai_state' },
  { key: 'starter',    label: 'Início de conversa',  ops: ['eq', 'ne'],                valueType: 'starter' },
  { key: 'activity',   label: 'Última atividade',    ops: ['gt', 'lt', 'days_before'], valueType: 'days' },
];
const CORE_BY_KEY = Object.fromEntries(CORE_DIMENSIONS.map(d => [d.key, d]));

const OP_LABELS = {
  eq: 'Igual a', ne: 'Diferente',
  gt: 'É maior que', lt: 'É menor que', days_before: 'É X dias antes',
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

// Valores cujo input é multi-select (lista). As demais são escalares.
const MULTI_TYPES = new Set(['channel', 'contact_type', 'agent', 'tag', 'conv_label', 'attr_list']);
const isMultiType = (valueType) => MULTI_TYPES.has(valueType);
const emptyValueFor = (dimDesc) => (dimDesc && isMultiType(dimDesc.valueType) ? [] : '');
const isEmptyValue = (v) => v == null || v === '' || (Array.isArray(v) && v.length === 0);
// Presets antigos podem trazer um escalar onde agora esperamos lista — normaliza ao ler.
const asList = (v) => (Array.isArray(v) ? v : (v == null || v === '' ? [] : [v]));

// Constrói a dimensão de filtro de um atributo personalizado.
function attrDim(def, scope) {
  const t = (def.type || 'text').toLowerCase();
  const m = ATTR_TYPE_MAP[t] || ATTR_TYPE_MAP.text;
  return {
    key: `cattr:${scope}:${def.attribute_key}`,
    label: def.display_name || def.attribute_key,
    ops: m.ops,
    valueType: m.valueType,
    attrDef: def,
    group: scope,   // 'contact' | 'conversation'
  };
}

let _seq = 0;
const newClause = (dim = 'channel') => {
  const d = CORE_BY_KEY[dim] || CORE_BY_KEY.channel;
  return { id: `f${++_seq}`, dim: d.key, op: d.ops[0], value: emptyValueFor(d) };
};

function TrashIcon() {
  return html`<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>`;
}
function ChevronIcon({ open = false }) {
  return html`<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"
    class="shrink-0 text-wa-secondary transition-transform ${open ? 'rotate-180' : ''}"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>`;
}

const FIELD = 'wa-field px-2 py-1.5 rounded-md text-[13px] border border-wa-border';

// Fecha o dropdown em clique-fora / Escape. Compartilhado por MultiSelect e DimensionPicker.
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

// Multi-seleção da barra de filtros → delega ao seletor PADRÃO do app (OptionListSelect):
// busca sempre visível, agrupamento por `group` e rodapé "Limpar seleção".
// options: [{ value, label, group? }]; `selected` = lista de values.
function MultiSelect({ options, selected, onChange, placeholder = '+ Selecione uma opção...' }) {
  return html`<div class="flex-1 min-w-0">
    <${OptionListSelect} options=${options} value=${selected} multiple=${true} grouped=${true}
      onChange=${onChange} placeholder=${placeholder} float=${true} />
  </div>`;
}

// Seletor de DIMENSÃO (single-select custom). Lista as dimensões core no topo e, em
// seguida, dois accordions recolhíveis — "Atributos do contato" e "Atributos da
// atendimento" — recolhidos por padrão, para esconder os atributos e manter a lista limpa.
function DimensionPicker({ dimensions, value, onChange }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [openContact, setOpenContact] = useState(false);
  const [openConv, setOpenConv] = useState(false);
  const ref = useRef(null);
  useCloseOnOutside(open, setOpen, ref);
  useEffect(() => { if (!open) setQ(''); }, [open]);

  // Com busca ativa os accordions abrem sozinhos: esconder um resultado atrás de um
  // accordion recolhido faria a busca parecer não ter achado nada.
  const term = q.trim().toLowerCase();
  const visible = term
    ? dimensions.filter(d => String(d.label).toLowerCase().includes(term))
    : dimensions;
  const core = visible.filter(d => !d.group);
  const contactDims = visible.filter(d => d.group === 'contact');
  const convDims = visible.filter(d => d.group === 'conversation');
  const current = dimensions.find(d => d.key === value);
  const pick = (key) => { onChange(key); setOpen(false); };

  const row = (d) => html`<button type="button" key=${d.key} onClick=${() => pick(d.key)}
    class="w-full text-left px-2.5 py-1.5 text-[13px] hover:bg-wa-hover transition-colors ${d.key === value ? 'text-wa-teal font-medium' : 'text-wa-text'}">
    ${d.label}
  </button>`;

  const accordion = (label, dims, isOpen, setIsOpen) => (dims.length === 0 ? null : html`<div class="border-t border-wa-border mt-1 pt-1">
    <button type="button" onClick=${() => setIsOpen(o => !o)}
      class="w-full flex items-center justify-between gap-1.5 px-2.5 py-1.5 text-[12px] font-semibold uppercase tracking-wide text-wa-secondary hover:bg-wa-hover transition-colors">
      <span>${label} (${dims.length})</span>
      <${ChevronIcon} open=${isOpen} />
    </button>
    ${isOpen ? html`<div>${dims.map(row)}</div>` : null}
  </div>`);

  return html`<div ref=${ref} class="relative shrink-0 w-[150px]">
    <button type="button" onClick=${() => setOpen(o => !o)}
      class="${FIELD} w-full flex items-center justify-between gap-1.5 text-left">
      <span class="truncate">${current ? current.label : 'Selecione...'}</span>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="shrink-0 text-wa-secondary"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
    </button>
    ${open ? html`<div class="absolute z-[80] mt-1 left-0 w-[240px] bg-wa-panel rounded-md shadow-lg border border-wa-border">
      <div class="p-2 border-b border-wa-border">
        <input type="text" class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]"
          placeholder="Pesquisar dimensão…" value=${q} autofocus
          onInput=${(e) => setQ(e.target.value)} />
      </div>
      <div class="max-h-[280px] overflow-y-auto py-1">
        ${(core.length + contactDims.length + convDims.length) === 0
          ? html`<div class="px-2.5 py-2 text-[13px] text-wa-secondary">Nenhuma opção encontrada.</div>`
          : html`<div>
              ${core.map(row)}
              ${accordion('Atributos do contato', contactDims, term ? true : openContact, setOpenContact)}
              ${accordion('Atributos da conversa', convDims, term ? true : openConv, setOpenConv)}
            </div>`}
      </div>
    </div>` : null}
  </div>`;
}

function ValueInput({ clause, dimDesc, channels, agentsUsers, agentsAi, tagNames, convLabelNames, onChange }) {
  const t = dimDesc.valueType;
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
    const options = channels.map(c => ({ value: c.id, label: c.label }));
    return html`<${MultiSelect} options=${options} selected=${asList(clause.value)} onChange=${onChange} />`;
  }
  if (t === 'contact_type') {
    // Tipos conhecidos + os descobertos do catálogo de providers (plano 76). Rótulos via catálogo.
    const options = contactTypeOrder().map(v => ({ value: v, label: contactTypeMeta(v).label }));
    return html`<${MultiSelect} options=${options} selected=${asList(clause.value)} onChange=${onChange} />`;
  }
  if (t === 'agent') {
    const options = [
      { value: 'none', label: '— Não atribuído —' },
      ...agentsUsers.map(u => ({ value: 'user:' + u.id, label: u.name, group: 'Atendentes' })),
      ...agentsAi.map(a => ({ value: 'ai:' + a.agent_key, label: a.display_name, group: 'Agentes de IA' })),
    ];
    return html`<${MultiSelect} options=${options} selected=${asList(clause.value)} onChange=${onChange} />`;
  }
  if (t === 'tag') {
    const options = tagNames.map(n => ({ value: n, label: n }));
    return html`<${MultiSelect} options=${options} selected=${asList(clause.value)} onChange=${onChange} />`;
  }
  if (t === 'conv_label') {
    const options = (convLabelNames || []).map(n => ({ value: n, label: n }));
    return html`<${MultiSelect} options=${options} selected=${asList(clause.value)} onChange=${onChange} />`;
  }
  if (t === 'ai_state') {
    return html`<select class=${cls} value=${clause.value} onChange=${(e) => onChange(e.target.value)}>
      <option value="">+ Selecione uma opção...</option>
      <option value="on">Ligada</option>
      <option value="off">Desligada</option>
    </select>`;
  }
  if (t === 'starter') {
    // Quem iniciou a conversa (plano 28: coluna `origin`). Cliente = conversa
    // 'inbound' (o cliente mandou a 1ª mensagem); Atendente = qualquer outra origem.
    return html`<select class=${cls} value=${clause.value} onChange=${(e) => onChange(e.target.value)}>
      <option value="">+ Selecione uma opção...</option>
      <option value="customer">Cliente</option>
      <option value="operator">Atendente</option>
    </select>`;
  }
  // ── Atributos personalizados ──
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
  if (t === 'attr_text') {
    return html`<input type="text" value=${clause.value} placeholder="Inserir valor"
      onInput=${(e) => onChange(e.target.value)} class=${cls} />`;
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
  convLabelNames = [], contactAttrDefs = [], convAttrDefs = [],
  sortBy, onSortChange, readSortOptions = [], timeSortOptions = [], onApply, onClose }) {
  useProviderCatalog();  // re-render quando o catálogo de providers carregar (opções de tipo)
  // Dimensões = core + atributos personalizados (contato e atendimento), dinâmicas.
  const dimensions = useMemo(() => [
    ...CORE_DIMENSIONS,
    ...(contactAttrDefs || []).map(d => attrDim(d, 'contact')),
    ...(convAttrDefs || []).map(d => attrDim(d, 'conversation')),
  ], [contactAttrDefs, convAttrDefs]);
  const dimByKey = useMemo(() => Object.fromEntries(dimensions.map(d => [d.key, d])), [dimensions]);

  // Local draft so editing rows doesn't re-filter the list until "Aplicar". Mounts
  // fresh each time the popover opens, so seed straight from the applied filters.
  const [draft, setDraft] = useState(() =>
    (filters && filters.length) ? filters.map(f => ({ ...f })) : [newClause()]);

  const patch = (id, changes) => setDraft(d => d.map(c => (c.id === id ? { ...c, ...changes } : c)));
  const changeDim = (id, dimKey) => {
    const d = dimByKey[dimKey] || CORE_BY_KEY.channel;
    patch(id, { dim: d.key, op: d.ops[0], value: emptyValueFor(d) });
  };
  const removeRow = (id) => setDraft(d => (d.length > 1 ? d.filter(c => c.id !== id) : [newClause()]));
  const addRow = () => setDraft(d => [...d, newClause()]);

  const apply = () => {
    onApply(draft.filter(c => !isEmptyValue(c.value)));
    onClose();
  };
  const clear = () => { onApply([]); onClose(); };

  return html`
    <div>
      <div class="text-[15px] font-semibold text-wa-text mb-3">Filtrar conversas</div>
      ${onSortChange ? html`
        <div class="flex gap-3 mb-4">
          <div class="flex-1 min-w-0">
            <label class="block text-[12px] text-wa-secondary mb-1">Ordenar por leitura</label>
            <select value=${splitSort(sortBy).read}
              onChange=${(e) => onSortChange(combineSort(e.target.value, splitSort(sortBy).time))}
              class="${FIELD} w-full">
              ${readSortOptions.map(o => html`<option key=${o.value} value=${o.value}>${o.label}</option>`)}
            </select>
          </div>
          <div class="flex-1 min-w-0">
            <label class="block text-[12px] text-wa-secondary mb-1">Ordem</label>
            <select value=${splitSort(sortBy).time}
              onChange=${(e) => onSortChange(combineSort(splitSort(sortBy).read, e.target.value))}
              class="${FIELD} w-full">
              ${timeSortOptions.map(o => html`<option key=${o.value} value=${o.value}>${o.label}</option>`)}
            </select>
          </div>
        </div>
        <div class="text-[12px] text-wa-secondary mb-1">Filtros</div>
      ` : null}
      <div class="flex flex-col gap-2 mb-3">
        ${draft.map((c) => {
          const dim = dimByKey[c.dim] || CORE_BY_KEY.channel;
          return html`<div key=${c.id} class="flex items-center gap-1.5">
            <${DimensionPicker} dimensions=${dimensions} value=${c.dim} onChange=${(k) => changeDim(c.id, k)} />
            <select class="${FIELD} shrink-0 w-[120px]" value=${c.op} onChange=${(e) => patch(c.id, { op: e.target.value })}>
              ${dim.ops.map(op => html`<option key=${op} value=${op}>${OP_LABELS[op]}</option>`)}
            </select>
            <${ValueInput} clause=${c} dimDesc=${dim} channels=${channels} agentsUsers=${agentsUsers}
              agentsAi=${agentsAi} tagNames=${tagNames} convLabelNames=${convLabelNames}
              onChange=${(v) => patch(c.id, { value: v })} />
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

