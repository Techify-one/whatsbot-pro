import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getModels } from '../services/api.js';

const html = htm.bind(h);

// Shared cache across all ModelSelect instances
let _modelsCache = null;
let _modelsFetching = false;
let _modelsFetchCallbacks = [];

async function fetchModelsOnce() {
  if (_modelsCache) return _modelsCache;
  if (_modelsFetching) {
    return new Promise(resolve => _modelsFetchCallbacks.push(resolve));
  }
  _modelsFetching = true;
  try {
    const res = await getModels();
    if (res.ok) {
      _modelsCache = res.data;
    } else {
      _modelsCache = [];
    }
  } catch {
    _modelsCache = [];
  }
  _modelsFetching = false;
  _modelsFetchCallbacks.forEach(cb => cb(_modelsCache));
  _modelsFetchCallbacks = [];
  return _modelsCache;
}

/**
 * Searchable model selector dropdown.
 * @param {object} props
 * @param {string} props.value - Current model ID
 * @param {function} props.onChange - Called with new model ID
 * @param {string} [props.filterModality] - Filter by input modality ("audio", "image", or null for all)
 * @param {string} [props.placeholder] - Placeholder text
 */
export function ModelSelect({ value, onChange, filterModality, placeholder }) {
  const [models, setModels] = useState([]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlightIdx, setHighlightIdx] = useState(0);
  const wrapperRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);

  // Load models on first open
  const loadModels = useCallback(async () => {
    const data = await fetchModelsOnce();
    let filtered = data;
    if (filterModality) {
      filtered = data.filter(m =>
        m.input_modalities && m.input_modalities.includes(filterModality)
      );
    }
    setModels(filtered);
  }, [filterModality]);

  useEffect(() => { loadModels(); }, [loadModels]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const ql = query.toLowerCase();
  const filtered = query
    ? models.filter(m => m.id.toLowerCase().includes(ql) || m.name.toLowerCase().includes(ql))
    : models;

  // Reset highlight when filtered list changes
  useEffect(() => { setHighlightIdx(0); }, [query]);

  function handleSelect(id) {
    onChange(id);
    setQuery('');
    setOpen(false);
  }

  function handleKeyDown(e) {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') {
        setOpen(true);
        e.preventDefault();
      }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightIdx(prev => Math.min(prev + 1, filtered.length - 1));
      scrollToHighlight(Math.min(highlightIdx + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightIdx(prev => Math.max(prev - 1, 0));
      scrollToHighlight(Math.max(highlightIdx - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (filtered[highlightIdx]) handleSelect(filtered[highlightIdx].id);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  }

  function scrollToHighlight(idx) {
    if (listRef.current) {
      const items = listRef.current.children;
      if (items[idx]) items[idx].scrollIntoView({ block: 'nearest' });
    }
  }

  // Find display name for current value
  const currentModel = models.find(m => m.id === value);
  const displayValue = open ? query : (value || '');

  return html`
    <div ref=${wrapperRef} class="relative w-full">
      <input
        ref=${inputRef}
        type="text"
        value=${displayValue}
        placeholder=${placeholder || 'Selecione um modelo...'}
        onFocus=${() => { setOpen(true); setQuery(''); }}
        onInput=${(e) => { setQuery(e.target.value); setOpen(true); }}
        onKeyDown=${handleKeyDown}
        class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
      />
      ${value && !open ? html`
        <div class="absolute right-8 top-1/2 -translate-y-1/2 text-[10px] text-wa-secondary truncate max-w-[50%] pointer-events-none">
          ${currentModel ? currentModel.name : ''}
        </div>
      ` : null}
      ${open && html`
        <div
          ref=${listRef}
          class="absolute z-50 left-0 right-0 mt-1 bg-white border border-wa-border rounded-lg shadow-lg max-h-[240px] overflow-y-auto wa-scrollbar"
        >
          ${filtered.length === 0
            ? html`<div class="px-3 py-2 text-sm text-wa-secondary">Nenhum modelo encontrado</div>`
            : filtered.slice(0, 100).map((m, i) => html`
                <div
                  key=${m.id}
                  onClick=${() => handleSelect(m.id)}
                  class="px-3 py-[6px] cursor-pointer text-sm hover:bg-wa-hover ${
                    i === highlightIdx ? 'bg-wa-hover' : ''
                  } ${m.id === value ? 'text-wa-teal font-medium' : 'text-wa-text'}"
                >
                  <div class="truncate text-[13px]">${m.name}</div>
                  <div class="truncate text-[11px] text-wa-secondary">${m.id}</div>
                </div>
              `)
          }
        </div>
      `}
    </div>
  `;
}
