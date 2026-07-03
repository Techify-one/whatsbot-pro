import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// Seletor de lista PADRÃO do app (single ou múltiplo). Gatilho + dropdown com:
//   - campo de busca SEMPRE visível (mesmo com 1 opção)
//   - opções com ✓ (seleção única) ou checkbox (múltipla)
//   - rodapé "Limpar seleção" (esvazia tudo)
// Inline-expand (NÃO flutua) → nunca é cortado por um modal com overflow. Dark-mode-safe
// (classes wa-*/.wa-field). Aceita `options` como strings (['a','b']) ou objetos
// ([{ value, label, group? }]). No modo múltiplo o valor é uma LISTA de values.
//
// @param {Array}    options    - ['a', ...] OU [{ value, label, group? }]
// @param {any}      value      - value único (single) ou lista de values (multiple)
// @param {boolean}  [multiple] - marca várias (valor vira lista)
// @param {boolean}  [grouped]  - agrupa por `option.group` (cabeçalhos)
// @param {function} onChange   - (novoValor) => void  ('' / [] ao limpar)
// @param {string}   [placeholder]
// @param {string}   [searchPlaceholder]
// @param {boolean}  [float] - dropdown FLUTUA sobre o conteúdo de baixo (absolute) em vez de
//                             empurrar (inline). Use fora de containers com overflow (barra de
//                             filtros, diálogos). Dentro de modais com overflow, deixe false.
function normOptions(options) {
  return (options || []).map((o) => (o && typeof o === 'object')
    ? { value: o.value, label: o.label != null ? o.label : String(o.value), group: o.group || '' }
    : { value: o, label: String(o), group: '' });
}

export function OptionListSelect({
  options, value, multiple = false, grouped = false, onChange,
  placeholder = 'Selecione…', searchPlaceholder = 'Pesquisar opções…', float = false,
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const ref = useRef(null);
  const opts = normOptions(options);

  // Fecha ao clicar fora; limpa a busca ao fechar.
  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    if (open) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);
  useEffect(() => { if (!open) setQ(''); }, [open]);

  const arr = multiple ? (Array.isArray(value) ? value : (value ? [value] : [])) : [];
  const has = multiple ? arr.length > 0 : (value != null && value !== '');
  const labelOf = (v) => { const o = opts.find((x) => x.value === v); return o ? o.label : String(v); };
  // Rótulo da barra = opções escolhidas separadas por vírgula, NA ORDEM das opções.
  const summary = has
    ? (multiple ? opts.filter((o) => arr.includes(o.value)).map((o) => o.label).join(', ') : labelOf(value))
    : placeholder;

  const term = q.trim().toLowerCase();
  const visible = term ? opts.filter((o) => String(o.label).toLowerCase().includes(term)) : opts;

  const pick = (v) => {
    if (multiple) {
      const set = new Set(arr);
      if (set.has(v)) set.delete(v); else set.add(v);
      onChange(opts.filter((o) => set.has(o.value)).map((o) => o.value));  // preserva ordem das opções
    } else { onChange(v); setOpen(false); setQ(''); }
  };
  const clear = () => onChange(multiple ? [] : '');

  // Agrupa o subconjunto visível preservando a ordem de aparição dos grupos.
  const groups = [];
  const byGroup = new Map();
  for (const o of visible) {
    const g = grouped ? (o.group || '') : '';
    if (!byGroup.has(g)) { byGroup.set(g, []); groups.push(g); }
    byGroup.get(g).push(o);
  }

  const rowFor = (o) => {
    const sel = multiple ? arr.includes(o.value) : value === o.value;
    return html`<button type="button" key=${o.value} onClick=${() => pick(o.value)}
      class="w-full text-left px-3 py-1.5 text-[14px] flex items-center gap-2 hover:bg-wa-hover ${sel ? 'text-wa-teal' : 'text-wa-text'}">
      ${multiple ? html`<input type="checkbox" checked=${sel} tabindex="-1" readOnly class="shrink-0" />` : null}
      <span class="truncate">${o.label}</span>
      ${(!multiple && sel) ? html`<span class="ml-auto text-wa-teal shrink-0">✓</span>` : null}
    </button>`;
  };

  return html`<div ref=${ref} class="relative w-full">
    <button type="button" onClick=${() => setOpen((v) => !v)}
      class="wa-field w-full px-3 py-2 rounded-md text-[14px] text-left flex items-center justify-between gap-2">
      <span class="truncate ${has ? '' : 'opacity-60'}">${summary}</span>
      <span class="opacity-60 shrink-0">${open ? '▴' : '▾'}</span>
    </button>
    ${open ? html`<div class="${float ? 'absolute z-[80] left-0 right-0' : ''} mt-1 rounded-md border border-wa-border bg-wa-panel shadow-lg overflow-hidden">
      <div class="p-2 border-b border-wa-border">
        <input type="text" class="wa-field w-full px-2 py-1.5 rounded-md text-[13px]"
          placeholder=${searchPlaceholder} value=${q} onInput=${(e) => setQ(e.target.value)} />
      </div>
      <div class="max-h-[200px] overflow-auto py-1">
        ${visible.length === 0 ? html`<div class="px-3 py-2 text-[13px] text-wa-secondary">Nenhuma opção encontrada.</div>`
          : groups.map((g) => html`<div key=${'g' + g}>
              ${(grouped && g) ? html`<div class="px-3 pt-1.5 pb-0.5 text-[11px] font-semibold uppercase tracking-wide text-wa-secondary">${g}</div>` : null}
              ${byGroup.get(g).map(rowFor)}
            </div>`)}
      </div>
      <div class="flex items-center justify-between gap-2 px-2 py-1.5 border-t border-wa-border">
        <button type="button" onClick=${clear} disabled=${!has}
          class="text-[12px] ${has ? 'text-wa-secondary hover:text-red-500' : 'text-wa-secondary opacity-40 cursor-not-allowed'}">Limpar seleção</button>
        ${multiple ? html`<button type="button" onClick=${() => setOpen(false)} class="text-[12px] text-wa-teal">Concluir</button>` : html`<span></span>`}
      </div>
    </div>` : null}
  </div>`;
}

export default OptionListSelect;
