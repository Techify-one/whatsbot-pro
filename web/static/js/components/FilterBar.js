// FilterBar (plano 08 — UI). A filter bar that sits above the conversation
// list. On mount it fetches the rich filter schema from the backend
// (GET /api/conversations/filter-schema) and renders only the controls that
// actually exist for this install.
//
// Supported dimensions (mapped from schema `kind`):
//   q       → free-text search input (.wa-field)
//   enum    → <select> built from `enum` (status, priority, …)
//   assignee→ <select> "Minhas" / "Não atribuídas" / "Todas" + one option per
//             user (when getUsers() is reachable) + present/none
//   labels  → multiselect chips from GET /api/tags (OR semantics: comma list);
//             falls back to a comma-separated text input when tags aren't
//             reachable
//   reltime → "atividade desde" <select> (24h / 7d / 30d / qualquer)
//   bool    → tri-state <select> (qualquer / sim / não)
//   int     → number input (.wa-field)
//   text    → text input (.wa-field)
//   cattr:<key> → one simple text input per filterable custom attribute
//
// Active filters render as removable chips. State is lifted to the parent via
// onChange(filtersObj) — a flat { key: value } map ready for
// filterConversations(...). The "Limpar" button resets everything.

import { h } from 'preact';
import { useEffect, useState, useMemo, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import { getConversationFilterSchema, getUsers, getMe, getTags } from '../services/api.js';

const html = htm.bind(h);

// "Atividade desde" presets (the `since` param accepts 24h / 7d / 30d).
const SINCE_OPTIONS = [
  { value: '', label: 'Qualquer época' },
  { value: '24h', label: 'Últimas 24h' },
  { value: '7d', label: 'Últimos 7 dias' },
  { value: '30d', label: 'Últimos 30 dias' },
];

// pt-BR labels for the well-known enum values; falls back to the raw value.
const ENUM_LABELS = {
  open: 'Abertas',
  closed: 'Fechadas',
  pending: 'Pendentes',
  snoozed: 'Adiadas',
  resolved: 'Resolvidas',
  low: 'Baixa',
  medium: 'Média',
  high: 'Alta',
  urgent: 'Urgente',
};

function enumLabel(v) {
  return ENUM_LABELS[v] || v;
}

// Resolve a human label for a chip given a dimension + raw value.
function chipLabel(dim, value, userNameById) {
  const base = dim.label || dim.key;
  if (dim.kind === 'assignee') {
    if (value === 'me') return `${base}: Minhas`;
    if (value === 'present') return `${base}: Atribuídas`;
    if (value === 'none') return `${base}: Não atribuídas`;
    const name = userNameById.get(value) || userNameById.get(Number(value)) || `#${value}`;
    return `${base}: ${name}`;
  }
  if (dim.kind === 'reltime') {
    const opt = SINCE_OPTIONS.find((o) => o.value === value);
    return `${base}: ${opt ? opt.label : value}`;
  }
  if (dim.kind === 'bool') {
    return `${base}: ${value === 'true' ? 'Sim' : 'Não'}`;
  }
  if (dim.kind === 'labels') {
    const parts = String(value).split(',').filter(Boolean);
    return `${base}: ${parts.join(', ')}`;
  }
  if (dim.kind === 'enum') {
    return `${base}: ${enumLabel(value)}`;
  }
  return `${base}: ${value}`;
}

// ── Labels (etiquetas) multiselect chips ────────────────────────────

function LabelsControl({ dim, value, tagNames, onChange }) {
  const selected = useMemo(
    () => new Set(String(value || '').split(',').filter(Boolean)),
    [value],
  );

  const toggle = useCallback((name) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange(dim.key, Array.from(next).join(','));
  }, [selected, dim.key, onChange]);

  // No tags reachable → degrade to a plain comma-separated input.
  if (!tagNames || tagNames.length === 0) {
    return html`
      <div class="flex flex-col">
        <label class="text-[11px] text-wa-secondary mb-1">${dim.label || 'Etiquetas'}</label>
        <input
          class="wa-field px-3 py-2 rounded-md text-[14px]"
          placeholder="vip, lead"
          value=${value || ''}
          onInput=${(e) => onChange(dim.key, e.target.value)}
        />
      </div>
    `;
  }

  return html`
    <div class="flex flex-col">
      <label class="text-[11px] text-wa-secondary mb-1">${dim.label || 'Etiquetas'}</label>
      <div class="flex items-center gap-1.5 flex-wrap max-w-[420px]">
        ${tagNames.map((name) => {
          const on = selected.has(name);
          return html`
            <button
              key=${name}
              type="button"
              onClick=${() => toggle(name)}
              class=${`px-2.5 py-1 rounded-full text-[12px] border transition-colors ${
                on
                  ? 'bg-wa-teal/15 text-wa-teal border-wa-teal/40'
                  : 'border-wa-border text-wa-secondary hover:bg-wa-hover'
              }`}
            >
              ${name}
            </button>
          `;
        })}
      </div>
    </div>
  `;
}

// ── Main bar ────────────────────────────────────────────────────────

export function FilterBar({ onChange }) {
  const [dimensions, setDimensions] = useState([]);
  const [filters, setFilters] = useState({}); // { key: value }
  const [users, setUsers] = useState([]);
  const [currentUserId, setCurrentUserId] = useState(null);
  const [tagNames, setTagNames] = useState([]);

  // Keep the latest onChange without re-running the effect that calls it.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Fetch the schema once. Anything we don't recognise is simply skipped.
  useEffect(() => {
    let alive = true;
    getConversationFilterSchema()
      .then((res) => {
        if (!alive) return;
        if (res && res.ok && res.data && Array.isArray(res.data.dimensions)) {
          setDimensions(res.data.dimensions);
        }
      })
      .catch(() => { /* ignore — bar just stays minimal */ });
    return () => { alive = false; };
  }, []);

  // Users + identity (best-effort) for the assignee dimension.
  useEffect(() => {
    let alive = true;
    getMe()
      .then((res) => {
        if (!alive) return;
        if (res && res.ok && res.data && res.data.user) setCurrentUserId(res.data.user.id);
      })
      .catch(() => {});
    getUsers()
      .then((res) => {
        if (!alive) return;
        if (res && res.ok && res.data && Array.isArray(res.data.users)) setUsers(res.data.users);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // Tags for the labels dimension (best-effort). getTags returns an object
  // { name: { color } } — keys are the tag names.
  useEffect(() => {
    let alive = true;
    getTags()
      .then((res) => {
        if (!alive) return;
        if (res && res.ok && res.data && typeof res.data === 'object') {
          setTagNames(Object.keys(res.data));
        }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const userNameById = useMemo(() => {
    const m = new Map();
    for (const u of users) m.set(u.id, u.name || u.email || `Usuário #${u.id}`);
    return m;
  }, [users]);

  // Emit only the non-empty filters to the parent whenever they change.
  const activeFilters = useMemo(() => {
    const out = {};
    for (const [k, v] of Object.entries(filters)) {
      if (v === undefined || v === null || v === '') continue;
      out[k] = v;
    }
    return out;
  }, [filters]);

  useEffect(() => {
    if (onChangeRef.current) onChangeRef.current(activeFilters);
  }, [activeFilters]);

  const setFilter = useCallback((key, value) => {
    setFilters((prev) => {
      const next = { ...prev };
      if (value === undefined || value === null || value === '') delete next[key];
      else next[key] = value;
      return next;
    });
  }, []);

  const clearAll = useCallback(() => setFilters({}), []);
  const removeFilter = useCallback((key) => setFilter(key, ''), [setFilter]);

  const dimByKey = useMemo(() => {
    const m = new Map();
    for (const d of dimensions) m.set(d.key, d);
    return m;
  }, [dimensions]);

  // Render one control per known dimension kind.
  function renderControl(dim) {
    const value = filters[dim.key] ?? '';
    const isCattr = dim.kind === 'cattr' || dim.key.startsWith('cattr:');

    if (dim.kind === 'q') {
      return html`
        <div class="flex flex-col flex-1 min-w-[180px]" key=${dim.key}>
          <label class="text-[11px] text-wa-secondary mb-1">${dim.label || 'Buscar'}</label>
          <input
            class="wa-field px-3 py-2 rounded-md text-[14px]"
            placeholder="Buscar conversas..."
            value=${value}
            onInput=${(e) => setFilter(dim.key, e.target.value)}
          />
        </div>
      `;
    }

    if (dim.kind === 'enum') {
      const opts = Array.isArray(dim.enum) ? dim.enum : [];
      return html`
        <div class="flex flex-col" key=${dim.key}>
          <label class="text-[11px] text-wa-secondary mb-1">${dim.label || dim.key}</label>
          <select
            class="wa-field px-3 py-2 rounded-md text-[14px]"
            value=${value}
            onChange=${(e) => setFilter(dim.key, e.target.value)}
          >
            <option value="">Todas</option>
            ${opts.map((o) => html`<option key=${o} value=${o}>${enumLabel(o)}</option>`)}
          </select>
        </div>
      `;
    }

    if (dim.kind === 'assignee') {
      return html`
        <div class="flex flex-col" key=${dim.key}>
          <label class="text-[11px] text-wa-secondary mb-1">${dim.label || 'Responsável'}</label>
          <select
            class="wa-field px-3 py-2 rounded-md text-[14px]"
            value=${value}
            onChange=${(e) => setFilter(dim.key, e.target.value)}
          >
            <option value="">Todas</option>
            ${currentUserId != null ? html`<option value="me">Minhas</option>` : null}
            <option value="present">Atribuídas</option>
            <option value="none">Não atribuídas</option>
            ${users.map((u) => html`
              <option key=${u.id} value=${String(u.id)}>${u.name || u.email}</option>
            `)}
          </select>
        </div>
      `;
    }

    if (dim.kind === 'reltime') {
      return html`
        <div class="flex flex-col" key=${dim.key}>
          <label class="text-[11px] text-wa-secondary mb-1">${dim.label || 'Atividade desde'}</label>
          <select
            class="wa-field px-3 py-2 rounded-md text-[14px]"
            value=${value}
            onChange=${(e) => setFilter(dim.key, e.target.value)}
          >
            ${SINCE_OPTIONS.map((o) => html`<option key=${o.value} value=${o.value}>${o.label}</option>`)}
          </select>
        </div>
      `;
    }

    if (dim.kind === 'bool') {
      return html`
        <div class="flex flex-col" key=${dim.key}>
          <label class="text-[11px] text-wa-secondary mb-1">${dim.label || dim.key}</label>
          <select
            class="wa-field px-3 py-2 rounded-md text-[14px]"
            value=${value}
            onChange=${(e) => setFilter(dim.key, e.target.value)}
          >
            <option value="">Qualquer</option>
            <option value="true">Sim</option>
            <option value="false">Não</option>
          </select>
        </div>
      `;
    }

    if (dim.kind === 'labels') {
      return html`
        <${LabelsControl}
          key=${dim.key}
          dim=${dim}
          value=${value}
          tagNames=${tagNames}
          onChange=${setFilter}
        />
      `;
    }

    if (dim.kind === 'int') {
      return html`
        <div class="flex flex-col" key=${dim.key}>
          <label class="text-[11px] text-wa-secondary mb-1">${dim.label || dim.key}</label>
          <input
            type="number"
            class="wa-field px-3 py-2 rounded-md text-[14px] w-[120px]"
            value=${value}
            onInput=${(e) => setFilter(dim.key, e.target.value)}
          />
        </div>
      `;
    }

    // text + cattr:<key> + any unknown simple kind → text input.
    if (dim.kind === 'text' || dim.kind === 'cattr' || isCattr) {
      return html`
        <div class="flex flex-col" key=${dim.key}>
          <label class="text-[11px] text-wa-secondary mb-1">${dim.label || dim.key}</label>
          <input
            class="wa-field px-3 py-2 rounded-md text-[14px] min-w-[140px]"
            value=${value}
            onInput=${(e) => setFilter(dim.key, e.target.value)}
          />
        </div>
      `;
    }

    return null;
  }

  const activeEntries = Object.entries(activeFilters);

  return html`
    <div class="mb-4">
      <div class="flex items-end gap-3 flex-wrap">
        ${dimensions.map((dim) => renderControl(dim))}

        ${activeEntries.length > 0 ? html`
          <button
            type="button"
            onClick=${clearAll}
            class="ml-auto px-3 py-2 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors"
          >
            Limpar
          </button>
        ` : null}
      </div>

      ${activeEntries.length > 0 ? html`
        <div class="flex items-center gap-1.5 flex-wrap mt-3">
          ${activeEntries.map(([key, value]) => {
            const dim = dimByKey.get(key) || { key, label: key, kind: 'text' };
            return html`
              <span
                key=${key}
                class="inline-flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-full text-[12px] bg-wa-teal/15 text-wa-teal"
              >
                <span class="truncate max-w-[220px]">${chipLabel(dim, value, userNameById)}</span>
                <button
                  type="button"
                  onClick=${() => removeFilter(key)}
                  class="w-4 h-4 inline-flex items-center justify-center rounded-full hover:bg-wa-teal/25 transition-colors"
                  aria-label="Remover filtro"
                  title="Remover filtro"
                >
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
                    <path d="M18.3 5.71a1 1 0 0 0-1.41 0L12 10.59 7.11 5.7A1 1 0 0 0 5.7 7.11L10.59 12 5.7 16.89a1 1 0 1 0 1.41 1.41L12 13.41l4.89 4.89a1 1 0 0 0 1.41-1.41L13.41 12l4.89-4.89a1 1 0 0 0 0-1.4z"/>
                  </svg>
                </button>
              </span>
            `;
          })}
        </div>
      ` : null}
    </div>
  `;
}

export default FilterBar;
