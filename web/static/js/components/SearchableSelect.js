import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// Generic single-select dropdown with a built-in search box, keyboard
// navigation and dark-mode-safe styling. Generalizes ModelSelect — it is
// "dumb": the caller materializes `options`; this component never fetches.
//
// Reuse it for any <select> that has too many / dynamic options to be usable
// as a native control (whose option popup is also drawn by the OS and ignores
// the dark theme). Short enum selects stay native (lighter, no overhead).
//
// @param {object}   props
// @param {string}   props.value            - current value (string)
// @param {function} props.onChange         - (newValue) => void  ('' when cleared)
// @param {Array}    props.options          - [{ value, label, sublabel? }]
// @param {string}   [props.placeholder]    - input text when nothing selected
// @param {boolean}  [props.allowEmpty]     - prepend a "clear" row to the list
// @param {string}   [props.emptyLabel]     - label for the clear row (default "—")
// @param {string}   [props.searchPlaceholder] - placeholder while searching
// @param {boolean}  [props.disabled]
// @param {string}   [props.inputClass]     - override the closed-control classes
export function SearchableSelect({
  value,
  onChange,
  options,
  placeholder,
  allowEmpty,
  emptyLabel,
  searchPlaceholder,
  disabled,
  inputClass,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlightIdx, setHighlightIdx] = useState(0);
  const wrapperRef = useRef(null);
  const listRef = useRef(null);

  const opts = options || [];

  // Close dropdown on outside click.
  useEffect(() => {
    function handleClick(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false);
        setQuery('');
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const ql = query.trim().toLowerCase();
  const matches = ql
    ? opts.filter((o) =>
        String(o.label).toLowerCase().includes(ql)
        || String(o.value).toLowerCase().includes(ql)
        || (o.sublabel && String(o.sublabel).toLowerCase().includes(ql)))
    : opts;

  // The "clear" row only shows when allowEmpty and no active query — so typing
  // still filters real options. It's part of the navigable (keyboard) list.
  const showEmptyRow = !!allowEmpty && !ql;
  const navItems = showEmptyRow
    ? [{ value: '', label: emptyLabel || '—', __empty: true }, ...matches]
    : matches;

  // Reset highlight when the filtered list changes.
  useEffect(() => { setHighlightIdx(0); }, [query]);

  function handleSelect(v) {
    onChange(v);
    setQuery('');
    setOpen(false);
  }

  function scrollToHighlight(idx) {
    if (listRef.current) {
      const children = listRef.current.children;
      if (children[idx]) children[idx].scrollIntoView({ block: 'nearest' });
    }
  }

  function handleKeyDown(e) {
    if (disabled) return;
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') {
        setOpen(true);
        e.preventDefault();
      }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = Math.min(highlightIdx + 1, navItems.length - 1);
      setHighlightIdx(next);
      scrollToHighlight(next);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const next = Math.max(highlightIdx - 1, 0);
      setHighlightIdx(next);
      scrollToHighlight(next);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (navItems[highlightIdx]) handleSelect(navItems[highlightIdx].value);
    } else if (e.key === 'Escape') {
      setOpen(false);
      setQuery('');
    }
  }

  const current = opts.find((o) => o.value === value);
  // Closed: show the current label (or the raw value if it isn't in options).
  // Open: show whatever the user is typing.
  const displayValue = open ? query : (current ? current.label : (value || ''));

  const closedClass = inputClass
    || 'wa-field w-full px-3 py-2 rounded-md text-[14px] border border-wa-border outline-none focus:border-wa-teal disabled:opacity-50';

  return html`
    <div ref=${wrapperRef} class="relative w-full">
      <input
        type="text"
        value=${displayValue}
        placeholder=${open ? (searchPlaceholder || placeholder || 'Buscar...') : (placeholder || 'Selecione...')}
        disabled=${disabled}
        onFocus=${() => { if (!disabled) { setOpen(true); setQuery(''); } }}
        onInput=${(e) => { setQuery(e.target.value); setOpen(true); }}
        onKeyDown=${handleKeyDown}
        class=${closedClass}
        title=${current ? current.label : (value || '')}
      />
      ${open && !disabled && html`
        <div
          ref=${listRef}
          class="absolute z-50 left-0 right-0 mt-1 bg-wa-bg border border-wa-border rounded-lg shadow-lg max-h-[240px] overflow-y-auto wa-scrollbar"
        >
          ${navItems.length === 0
            ? html`<div class="px-3 py-2 text-[13px] text-wa-secondary">Nenhuma opção encontrada</div>`
            : navItems.slice(0, 200).map((o, i) => html`
                <div
                  key=${o.__empty ? '__empty__' : o.value}
                  onClick=${() => handleSelect(o.value)}
                  class="px-3 py-[6px] cursor-pointer text-[14px] hover:bg-wa-hover ${
                    i === highlightIdx ? 'bg-wa-hover' : ''
                  } ${o.__empty
                    ? 'text-wa-secondary italic'
                    : (o.value === value ? 'text-wa-teal font-medium' : 'text-wa-text')}"
                >
                  <div class="truncate">${o.label}</div>
                  ${o.sublabel ? html`<div class="truncate text-[11px] text-wa-secondary">${o.sublabel}</div>` : null}
                </div>
              `)
          }
        </div>
      `}
    </div>
  `;
}

