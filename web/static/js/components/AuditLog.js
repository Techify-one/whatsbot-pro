// Audit trail screen (plano 07 Fase 2). Full-page.
// Lists the append-only audit log with filters (resource type, action, actor
// type, resource id, date range), a paginated table, expandable rows showing
// the before/after JSON diff, and CSV/JSON export. The backend masks sensitive
// values and gates everything behind the `audit.read` permission (403 if the
// current user lacks it — surfaced here as an error message).

import { h, Fragment } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { listAudit, getAuditActions, downloadAuditExport } from '../services/api.js';

const html = htm.bind(h);

const PAGE_SIZE = 50;

// Colored badge per actor type. Uses accent tints (with dark-mode fallback in
// custom.css); surfaces/text use the semantic wa-* classes.
const ACTOR_BADGES = {
  user: { bg: 'bg-blue-500/10', text: 'text-blue-600', label: 'Usuário' },
  system: { bg: 'bg-wa-hover', text: 'text-wa-secondary', label: 'Sistema' },
  ai: { bg: 'bg-purple-500/10', text: 'text-purple-600', label: 'IA' },
};

// created_at is epoch seconds (float). Format to BR date/time.
function formatTime(ts) {
  if (ts == null) return '—';
  const d = new Date(ts * 1000);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

// Defensive JSON parse + pretty-print. The stored value may be null, an empty
// string, or already-invalid — never throw, just fall back to the raw string.
function prettyJson(raw) {
  if (raw == null || raw === '') return null;
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch (_) {
    return String(raw);
  }
}

// Convert a `<input type="date">` value (YYYY-MM-DD) to epoch seconds.
// `edge='start'` → 00:00:00 local; `edge='end'` → 23:59:59 local.
function dateToEpoch(value, edge) {
  if (!value) return null;
  const [y, m, d] = value.split('-').map((n) => parseInt(n, 10));
  if (!y || !m || !d) return null;
  const dt = edge === 'end'
    ? new Date(y, m - 1, d, 23, 59, 59, 999)
    : new Date(y, m - 1, d, 0, 0, 0, 0);
  return Math.floor(dt.getTime() / 1000);
}

function ActorBadge({ type }) {
  const b = ACTOR_BADGES[type] || ACTOR_BADGES.system;
  return html`<span class="px-2 py-0.5 rounded-full text-[11px] ${b.bg} ${b.text}">${b.label}</span>`;
}

function DiffBlock({ title, raw }) {
  const json = prettyJson(raw);
  if (json == null) return null;
  return html`
    <div class="flex-1 min-w-0">
      <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">${title}</div>
      <pre class="text-xs bg-wa-panel border border-wa-border rounded p-2 overflow-x-auto text-wa-text whitespace-pre-wrap break-words">${json}</pre>
    </div>
  `;
}

function Row({ row, expanded, onToggle }) {
  const hasDiff = (row.before_json != null && row.before_json !== '')
    || (row.after_json != null && row.after_json !== '');
  return html`
    <${Fragment}>
      <tr
        key=${row.id}
        onClick=${onToggle}
        class="border-t border-wa-border hover:bg-wa-hover cursor-pointer transition-colors"
      >
        <td class="px-3 py-2.5 text-wa-secondary text-xs whitespace-nowrap">${formatTime(row.created_at)}</td>
        <td class="px-3 py-2.5">
          <div class="flex items-center gap-2">
            <span class="text-wa-text text-sm truncate max-w-[160px]">${row.actor_label || '—'}</span>
            <${ActorBadge} type=${row.actor_type} />
          </div>
        </td>
        <td class="px-3 py-2.5 text-wa-text text-sm">
          <span class="font-mono text-xs px-1.5 py-0.5 rounded bg-wa-panel text-wa-secondary">${row.action}</span>
        </td>
        <td class="px-3 py-2.5 text-wa-secondary text-xs">
          ${row.resource_type || '—'}${row.resource_id ? html`<span class="text-wa-text">:${row.resource_id}</span>` : null}
        </td>
        <td class="px-3 py-2.5 text-wa-secondary text-xs whitespace-nowrap">${row.ip_address || '—'}</td>
      </tr>
      ${expanded ? html`
        <tr class="border-t border-wa-border bg-wa-panel/40">
          <td colspan="5" class="px-3 py-3">
            ${hasDiff ? html`
              <div class="flex flex-col md:flex-row gap-3">
                <${DiffBlock} title="Antes" raw=${row.before_json} />
                <${DiffBlock} title="Depois" raw=${row.after_json} />
              </div>
            ` : html`
              <div class="text-xs text-wa-secondary italic">Sem dados de alteração.</div>
            `}
            ${row.request_id ? html`
              <div class="text-[11px] text-wa-secondary mt-2 font-mono break-all">request_id: ${row.request_id}</div>
            ` : null}
          </td>
        </tr>
      ` : null}
    <//>
  `;
}

export default function AuditLog() {
  // Filter inputs (draft) — applied on "Filtrar".
  const [resourceType, setResourceType] = useState('');
  const [action, setAction] = useState('');
  const [actorType, setActorType] = useState('');
  const [resourceId, setResourceId] = useState('');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');

  // Applied filters (what's actually sent to the backend).
  const [applied, setApplied] = useState({});

  const [actions, setActions] = useState([]);
  const [resourceTypes, setResourceTypes] = useState([]);

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedId, setExpandedId] = useState(null);
  const [exporting, setExporting] = useState(false);

  // Load distinct actions / resource types for the filter selects.
  useEffect(() => {
    getAuditActions().then((res) => {
      if (res && res.ok && res.data) {
        setActions(res.data.actions || []);
        setResourceTypes(res.data.resource_types || []);
      }
    });
  }, []);

  // Build the param object from the applied filters + current offset.
  const buildParams = useCallback(() => {
    const p = { limit: PAGE_SIZE, offset };
    if (applied.resource_type) p.resource_type = applied.resource_type;
    if (applied.action) p.action = applied.action;
    if (applied.actor_type) p.actor_type = applied.actor_type;
    if (applied.resource_id) p.resource_id = applied.resource_id;
    if (applied.from != null) p.from = applied.from;
    if (applied.to != null) p.to = applied.to;
    return p;
  }, [applied, offset]);

  const fetchList = useCallback(async () => {
    setLoading(true);
    setError('');
    const res = await listAudit(buildParams());
    if (res && res.ok && res.data) {
      setItems(res.data.items || []);
      setTotal(res.data.total || 0);
    } else {
      setItems([]);
      setTotal(0);
      setError((res && res.error) || 'Falha ao carregar a trilha de auditoria.');
    }
    setLoading(false);
  }, [buildParams]);

  useEffect(() => { fetchList(); }, [fetchList]);

  function applyFilters() {
    setApplied({
      resource_type: resourceType,
      action,
      actor_type: actorType,
      resource_id: resourceId.trim(),
      from: dateToEpoch(fromDate, 'start'),
      to: dateToEpoch(toDate, 'end'),
    });
    setOffset(0);
    setExpandedId(null);
  }

  function clearFilters() {
    setResourceType('');
    setAction('');
    setActorType('');
    setResourceId('');
    setFromDate('');
    setToDate('');
    setApplied({});
    setOffset(0);
    setExpandedId(null);
  }

  // Export uses the *applied* filters (same as the visible list).
  async function handleExport(format) {
    setExporting(true);
    setError('');
    const params = {};
    if (applied.resource_type) params.resource_type = applied.resource_type;
    if (applied.action) params.action = applied.action;
    if (applied.actor_type) params.actor_type = applied.actor_type;
    if (applied.resource_id) params.resource_id = applied.resource_id;
    if (applied.from != null) params.from = applied.from;
    if (applied.to != null) params.to = applied.to;
    const res = await downloadAuditExport(params, format);
    if (!res || !res.ok) setError((res && res.error) || 'Falha ao exportar.');
    setExporting(false);
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return html`
    <div class="flex flex-col gap-4">
      <p class="text-[13px] text-wa-secondary">
        Trilha de auditoria (somente leitura). Registra ações de usuários, do sistema e da IA.
      </p>

      <!-- Filters -->
      <div class="bg-wa-bg border border-wa-border rounded-lg p-4">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Recurso</label>
            <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              value=${resourceType} onChange=${(e) => setResourceType(e.target.value)}>
              <option value="">Todos os recursos</option>
              ${resourceTypes.map((rt) => html`<option key=${rt} value=${rt}>${rt}</option>`)}
            </select>
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Ação</label>
            <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              value=${action} onChange=${(e) => setAction(e.target.value)}>
              <option value="">Todas as ações</option>
              ${actions.map((a) => html`<option key=${a} value=${a}>${a}</option>`)}
            </select>
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Tipo de ator</label>
            <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              value=${actorType} onChange=${(e) => setActorType(e.target.value)}>
              <option value="">Todos os atores</option>
              <option value="user">Usuário</option>
              <option value="system">Sistema</option>
              <option value="ai">IA</option>
            </select>
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">ID do recurso</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" placeholder="ex.: 42" value=${resourceId}
              onInput=${(e) => setResourceId(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">De</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="date" value=${fromDate} onInput=${(e) => setFromDate(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Até</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="date" value=${toDate} onInput=${(e) => setToDate(e.target.value)} />
          </div>
        </div>
        <div class="flex flex-wrap gap-2 justify-end mt-3">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${clearFilters}>Limpar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity"
            onClick=${applyFilters}>Filtrar</button>
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors disabled:opacity-50"
            onClick=${() => handleExport('csv')} disabled=${exporting}>Exportar CSV</button>
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors disabled:opacity-50"
            onClick=${() => handleExport('json')} disabled=${exporting}>Exportar JSON</button>
        </div>
      </div>

      ${error ? html`<div class="text-[13px] text-red-500">${error}</div>` : null}

      <!-- Table -->
      <div class="bg-wa-bg border border-wa-border rounded-lg overflow-hidden">
        ${loading && items.length === 0 ? html`
          <div class="p-8 text-center text-wa-secondary text-sm">Carregando…</div>
        ` : items.length === 0 ? html`
          <div class="p-8 text-center text-wa-secondary text-sm">Nenhum registro.</div>
        ` : html`
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead class="bg-wa-panel">
                <tr>
                  <th class="text-left px-3 py-2 font-medium text-wa-secondary text-xs">Data/hora</th>
                  <th class="text-left px-3 py-2 font-medium text-wa-secondary text-xs">Ator</th>
                  <th class="text-left px-3 py-2 font-medium text-wa-secondary text-xs">Ação</th>
                  <th class="text-left px-3 py-2 font-medium text-wa-secondary text-xs">Recurso</th>
                  <th class="text-left px-3 py-2 font-medium text-wa-secondary text-xs">IP</th>
                </tr>
              </thead>
              <tbody>
                ${items.map((row) => html`
                  <${Row}
                    key=${row.id}
                    row=${row}
                    expanded=${expandedId === row.id}
                    onToggle=${() => setExpandedId((id) => (id === row.id ? null : row.id))}
                  />
                `)}
              </tbody>
            </table>
          </div>
        `}

        <!-- Pagination -->
        ${total > 0 ? html`
          <div class="flex items-center justify-between px-3 py-2 border-t border-wa-border text-xs text-wa-secondary">
            <button
              onClick=${() => { setOffset(Math.max(0, offset - PAGE_SIZE)); setExpandedId(null); }}
              disabled=${offset === 0}
              class="px-3 py-1 rounded border border-wa-border hover:bg-wa-hover disabled:opacity-30 transition-colors"
            >Anterior</button>
            <span>Página ${page} de ${totalPages} · ${total} registro(s)</span>
            <button
              onClick=${() => { setOffset(offset + PAGE_SIZE); setExpandedId(null); }}
              disabled=${offset + PAGE_SIZE >= total}
              class="px-3 py-1 rounded border border-wa-border hover:bg-wa-hover disabled:opacity-30 transition-colors"
            >Próxima</button>
          </div>
        ` : null}
      </div>
    </div>
  `;
}
