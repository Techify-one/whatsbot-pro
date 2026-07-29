// Audit trail screen (plano 07 Fase 2). Full-page.
// Lists the append-only audit log with filters (resource type, action, actor
// type, resource id, date range), a paginated table, expandable rows showing
// the before/after JSON diff, and CSV/JSON export. The backend masks sensitive
// values and gates everything behind the `audit.read` permission (403 if the
// current user lacks it — surfaced here as an error message).

import { h, Fragment } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { listAudit, getAuditActions, downloadAuditExport, getConfig, saveConfig } from '../services/api.js';
import { auditDiffView } from '../services/auditDiff.js';
import { ExportConfirmModal } from './audit/ExportConfirmModal.js';
import { SearchableSelect } from './SearchableSelect.js';
import { useUrlState } from '../hooks/useUrlState.js';
import { readParams, writeParams, str, int } from '../services/urlState.js';
import { CopyLinkButton } from '../utils/copyDeepLink.js';
import { hasPermission } from '../utils/permissions.js';

const html = htm.bind(h);

const PAGE_SIZE = 50;

// Teto de linhas por exportação — espelha `_EXPORT_CAP` em
// server/routes/audit.py. Só alimenta o aviso ao operador; quem corta é o
// backend (que ainda devolve o header `X-Audit-Truncated` com o total real).
const EXPORT_CAP = 10000;

// Deep-link do estado da tela (Plano 24) — filtros aplicados + paginação + linha
// expandida na query-string legível. Datas viajam como 'YYYY-MM-DD' (o que os
// inputs usam); o read reconverte p/ epoch. Serialize omite defaults → URL limpa.
const AUDIT_URL_SCHEMA = [
  str('resource_type', ''),
  str('action', ''),
  str('actor_type', ''),
  str('resource_id', ''),
  str('from', ''),                  // 'YYYY-MM-DD'
  str('to', ''),                    // 'YYYY-MM-DD'
  int('offset', 0),
  int('expanded', null),            // id da linha expandida
];

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

// Epoch seconds → 'YYYY-MM-DD' local (o formato do `<input type="date">`), p/ a
// URL. Inverso de `dateToEpoch` no nível do dia — o round-trip via a mesma data.
function epochToDate(ts) {
  if (ts == null) return '';
  const d = new Date(ts * 1000);
  if (isNaN(d.getTime())) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function ActorBadge({ type }) {
  const b = ACTOR_BADGES[type] || ACTOR_BADGES.system;
  return html`<span class="px-2 py-0.5 rounded-full text-[11px] ${b.bg} ${b.text}">${b.label}</span>`;
}

function DiffBlock({ title, json }) {
  if (json == null) return null;
  return html`
    <div class="flex-1 min-w-0">
      <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">${title}</div>
      <pre class="text-xs bg-wa-panel border border-wa-border rounded p-2 overflow-x-auto text-wa-text whitespace-pre-wrap break-words">${json}</pre>
    </div>
  `;
}

// Bloco expandido: por padrão mostra SÓ os campos alterados (o snapshot inteiro
// costuma ter dezenas de chaves idênticas dos dois lados). O registro completo
// continua a um clique — a trilha guarda o snapshot inteiro, nada se perde.
function DiffPanel({ row }) {
  const [showFull, setShowFull] = useState(false);
  const view = auditDiffView(row.before_json, row.after_json);
  const full = view.mode === 'full' || showFull;
  const before = full ? view.beforeFull : view.before;
  const after = full ? view.afterFull : view.after;
  const n = view.paths.length;
  // Uma edição pode mexer em dezenas de campos — a linha de resumo é um rótulo,
  // não a lista completa (o diff logo abaixo mostra todos).
  const shown = view.paths.slice(0, 6).join(', ') + (n > 6 ? `, +${n - 6}` : '');

  return html`
    <${Fragment}>
      ${view.mode !== 'full' ? html`
        <div class="flex items-center gap-2 mb-2 flex-wrap">
          <span class="text-[11px] text-wa-secondary">
            ${view.mode === 'empty'
              ? 'Nenhum campo foi alterado.'
              : `${n} ${n === 1 ? 'campo alterado' : 'campos alterados'}: ${shown}`}
          </span>
          <button
            type="button"
            onClick=${(e) => { e.stopPropagation(); setShowFull((v) => !v); }}
            class="text-[11px] text-wa-teal hover:underline"
          >${showFull ? 'Ver só as alterações' : 'Ver JSON completo'}</button>
        </div>
      ` : null}
      ${(before == null && after == null) ? html`
        <div class="text-xs text-wa-secondary italic">Sem campos alterados para exibir.</div>
      ` : html`
        <div class="flex flex-col md:flex-row gap-3">
          <${DiffBlock} title="Antes" json=${before} />
          <${DiffBlock} title="Depois" json=${after} />
        </div>
      `}
    <//>
  `;
}

function Row({ row, expanded, onToggle, linkPath }) {
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
        <td class="px-3 py-2.5 text-right whitespace-nowrap">
          <${CopyLinkButton} path=${linkPath} title="Copiar link para este registro" />
        </td>
      </tr>
      ${expanded ? html`
        <tr class="border-t border-wa-border bg-wa-panel/40">
          <td colspan="6" class="px-3 py-3">
            ${hasDiff ? html`
              <${DiffPanel} row=${row} />
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

export default function AuditLog({ currentUser } = {}) {
  // Master global (plano 07): liga/desliga a gravação da trilha. Só quem tem
  // `audit.manage` vê o interruptor (P48: hide, don't disable); quem só tem
  // `audit.read` enxerga a tela em modo leitura, sem o toggle.
  const canManage = hasPermission(currentUser, 'audit.manage');
  const [auditEnabled, setAuditEnabled] = useState(null);   // null = ainda carregando
  const [togglingAudit, setTogglingAudit] = useState(false);

  useEffect(() => {
    if (!canManage) return;
    getConfig().then((res) => {
      if (res && res.ok && res.data) setAuditEnabled(res.data.audit_enabled !== false);
    });
  }, [canManage]);

  async function toggleAudit() {
    if (togglingAudit || auditEnabled == null) return;
    const next = !auditEnabled;
    setTogglingAudit(true);
    setAuditEnabled(next);                                   // otimista
    const res = await saveConfig({ audit_enabled: next });
    if (!res || !res.ok) setAuditEnabled(!next);             // rollback em falha
    setTogglingAudit(false);
  }

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
  const [pendingExport, setPendingExport] = useState(null);   // 'csv' | 'json' | null

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

  // "Aplicar" a partir de valores de filtro explícitos (drafts atuais OU vindos da
  // URL). O botão Filtrar passa os drafts; o read do deep-link passa os da URL.
  const applyValues = useCallback((v) => {
    setApplied({
      resource_type: v.resourceType || '',
      action: v.action || '',
      actor_type: v.actorType || '',
      resource_id: (v.resourceId || '').trim(),
      from: dateToEpoch(v.fromDate, 'start'),
      to: dateToEpoch(v.toDate, 'end'),
    });
  }, []);

  function applyFilters() {
    applyValues({ resourceType, action, actorType, resourceId, fromDate, toDate });
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

  // Deep-link (Plano 24) — hidrata filtros/paginação/linha expandida da URL no
  // mount e no back/forward; reflete o estado APLICADO (não os drafts) + offset +
  // expanded na query (replaceState). Datas viajam como 'YYYY-MM-DD'; o read
  // reconverte p/ epoch via applyValues. Serialize omite defaults → link limpo.
  useUrlState({
    read: () => readParams(window.location.search, AUDIT_URL_SCHEMA),
    apply: (s) => {
      // Hidrata os inputs (drafts) e aplica (dispara o fetch pelo effect de applied).
      setResourceType(s.resource_type);
      setAction(s.action);
      setActorType(s.actor_type);
      setResourceId(s.resource_id);
      setFromDate(s.from);
      setToDate(s.to);
      applyValues({
        resourceType: s.resource_type, action: s.action, actorType: s.actor_type,
        resourceId: s.resource_id, fromDate: s.from, toDate: s.to,
      });
      setOffset(s.offset);
      setExpandedId(s.expanded);
    },
    serialize: () => writeParams({
      resource_type: applied.resource_type,
      action: applied.action,
      actor_type: applied.actor_type,
      resource_id: applied.resource_id,
      from: epochToDate(applied.from),
      to: epochToDate(applied.to),
      offset,
      expanded: expandedId,
    }, AUDIT_URL_SCHEMA),
    deps: [applied, offset, expandedId],
  });

  // Export uses the *applied* filters (same as the visible list). O clique só
  // ABRE o aviso do teto de linhas; quem dispara o download é o modal.
  async function runExport(format) {
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
    setPendingExport(null);
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Deep-link p/ uma linha: /audit?<filtros aplicados>&expanded=<id> (reusa o
  // mesmo schema → params legíveis + omissão de defaults; offset fica de fora).
  const rowLinkPath = (id) => {
    const qs = writeParams({
      resource_type: applied.resource_type,
      action: applied.action,
      actor_type: applied.actor_type,
      resource_id: applied.resource_id,
      from: epochToDate(applied.from),
      to: epochToDate(applied.to),
      expanded: id,
    }, AUDIT_URL_SCHEMA);
    return `/audit${qs ? `?${qs}` : ''}`;
  };

  return html`
    <div class="flex flex-col gap-4">
      <div class="flex items-start justify-between gap-4 flex-wrap">
        <p class="text-[13px] text-wa-secondary flex-1 min-w-[240px]">
          Trilha de auditoria (somente leitura). Registra ações de usuários, do sistema e da IA.
        </p>
        ${canManage && auditEnabled != null ? html`
          <div class="flex items-center gap-3 bg-wa-bg border border-wa-border rounded-lg px-3 py-2">
            <div class="flex flex-col">
              <span class="text-[13px] text-wa-text font-medium">Registrar auditoria</span>
              <span class="text-[11px] text-wa-secondary">
                ${auditEnabled ? 'Ativa — novos eventos são gravados.' : 'Desativada — nada novo é gravado.'}
              </span>
            </div>
            <button
              onClick=${toggleAudit}
              disabled=${togglingAudit}
              role="switch"
              aria-checked=${auditEnabled}
              title=${auditEnabled ? 'Desativar auditoria' : 'Ativar auditoria'}
              class="w-11 h-6 rounded-full transition-colors relative shrink-0 disabled:opacity-50 ${auditEnabled ? 'bg-wa-teal' : 'bg-wa-border'}"
            >
              <span class="absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-all ${auditEnabled ? 'left-[22px]' : 'left-0.5'}"></span>
            </button>
          </div>
        ` : null}
      </div>

      <!-- Filters -->
      <div class="bg-wa-bg border border-wa-border rounded-lg p-4">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Recurso</label>
            <${SearchableSelect}
              value=${resourceType}
              onChange=${setResourceType}
              options=${resourceTypes.map((rt) => ({ value: rt, label: rt }))}
              placeholder="Todos os recursos"
              searchPlaceholder="Buscar recurso..."
              allowEmpty=${true}
              emptyLabel="Todos os recursos"
            />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Ação</label>
            <${SearchableSelect}
              value=${action}
              onChange=${setAction}
              options=${actions.map((a) => ({ value: a, label: a }))}
              placeholder="Todas as ações"
              searchPlaceholder="Buscar ação..."
              allowEmpty=${true}
              emptyLabel="Todas as ações"
            />
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
            onClick=${() => setPendingExport('csv')} disabled=${exporting}>Exportar CSV</button>
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors disabled:opacity-50"
            onClick=${() => setPendingExport('json')} disabled=${exporting}>Exportar JSON</button>
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
                  <th class="px-3 py-2"><span class="sr-only">Link</span></th>
                </tr>
              </thead>
              <tbody>
                ${items.map((row) => html`
                  <${Row}
                    key=${row.id}
                    row=${row}
                    expanded=${expandedId === row.id}
                    onToggle=${() => setExpandedId((id) => (id === row.id ? null : row.id))}
                    linkPath=${rowLinkPath(row.id)}
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

      <${ExportConfirmModal}
        format=${pendingExport}
        total=${total}
        cap=${EXPORT_CAP}
        busy=${exporting}
        onConfirm=${() => runExport(pendingExport)}
        onCancel=${() => { if (!exporting) setPendingExport(null); }}
      />
    </div>
  `;
}
