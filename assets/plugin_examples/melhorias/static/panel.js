// Painel de aprovação de sugestões de melhoria — screen config:false, montada por
// PluginScreen (props {apiBase, can, currentUser}). Modelo: aba "Lista" de Protocolos.
//
// Como PluginScreen NÃO passa `api`, este componente fala com o backend por fetch
// direto (authHeaders do token no localStorage) e usa modais inline próprios.
//
// Colunas (todas filtráveis): Mensagem, Solicitante, Melhoria solicitada (nota do
// operador), Conversa (deep-link), Solicitado em, Aprovado em, Status, Aprovador.
// A ANÁLISE gerada pela IA aparece no detalhe (superfície "visível só pelo painel").

import { h } from 'preact';
import { useState, useEffect, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function authHeaders(extra = {}) {
  const token = localStorage.getItem('whatsbot_token') || '';
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

async function apiJson(url, init = {}) {
  const headers = authHeaders(init.headers || {});
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    throw new Error('Não autenticado.');
  }
  let body = {};
  try { body = await res.json(); } catch (_) { /* ignore */ }
  return { status: res.status, ok: res.ok && body && body.ok !== false, body };
}

function fmtTs(ts) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }); }
  catch (_) { return '—'; }
}

// Data (YYYY-MM-DD) de N dias atrás — usado no filtro padrão de "Solicitado em".
function ymdDaysAgo(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

function readUrlParam(key) {
  try { return new URLSearchParams(location.search).get(key); } catch (_) { return null; }
}
function writeUrlParam(key, val) {
  try {
    const p = new URLSearchParams(location.search);
    if (val == null || val === '') p.delete(key); else p.set(key, String(val));
    const qs = p.toString();
    history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : ''));
  } catch (_) { /* ignore */ }
}

const STATUS_LABEL = { pendente: 'Pendente', aprovada: 'Aprovada', recusada: 'Recusada' };
const STATUS_CLS = {
  pendente: 'text-amber-600', aprovada: 'text-wa-teal', recusada: 'text-red-500',
};

// Colunas: {key, label, nowrap, get(r) -> valor p/ filtro/sort, render(r) -> markup}.
function buildCols() {
  return [
    { key: 'message_content', label: 'Mensagem', filter: 'text',
      get: (r) => r.message_content || '',
      render: (r) => html`<span class="line-clamp-2 max-w-[280px] inline-block align-top">${(r.message_content || '').trim() || '—'}</span>` },
    { key: 'requester_name', label: 'Solicitante', filter: 'select',
      get: (r) => r.requester_name || '' },
    { key: 'feedback', label: 'Melhoria solicitada', filter: 'text',
      get: (r) => r.feedback || '',
      render: (r) => html`<span class="line-clamp-2 max-w-[240px] inline-block align-top">${(r.feedback || '').trim() || '—'}</span>` },
    { key: 'conversation', label: 'Conversa', filter: null,
      get: () => '',
      render: (r) => (r.conversation_url
        ? html`<a href=${r.conversation_url} onClick=${(e) => navConversation(e, r)}
            class="text-wa-teal hover:underline whitespace-nowrap">Abrir conversa ↗</a>`
        : html`<span class="text-wa-secondary">—</span>`) },
    { key: 'requested_at', label: 'Solicitado em', filter: 'date', nowrap: true,
      get: (r) => r.requested_at || 0, render: (r) => fmtTs(r.requested_at) },
    { key: 'approved_at', label: 'Aprovado em', filter: 'date', nowrap: true,
      get: (r) => (r.status === 'aprovada' ? (r.decided_at || 0) : 0),
      render: (r) => (r.status === 'aprovada' ? fmtTs(r.decided_at) : '—') },
    { key: 'status', label: 'Status', filter: 'select',
      get: (r) => r.status || '',
      render: (r) => html`<span class="font-medium ${STATUS_CLS[r.status] || ''}">${STATUS_LABEL[r.status] || r.status}</span>` },
    { key: 'approver_name', label: 'Aprovador', filter: 'select',
      get: (r) => (r.status === 'aprovada' ? (r.approver_name || '') : ''),
      render: (r) => (r.status === 'aprovada' ? (r.approver_name || '—') : '—') },
  ];
}

// Navega à conversa dentro da SPA (evita reload) no ?message âncora.
function navConversation(e, r) {
  if (!r.conversation_url) return;
  e.preventDefault();
  try {
    const u = new URL(r.conversation_url, location.origin);
    history.pushState(null, '', u.pathname + u.search);
    window.dispatchEvent(new PopStateEvent('popstate'));
  } catch (_) { location.href = r.conversation_url; }
}

export default function MelhoriasPanel({ apiBase = '/api/plugins/melhorias', can = () => true } = {}) {
  const canApprove = can('approve');
  const cols = useMemo(() => buildCols(), []);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filters, setFilters] = useState({});       // colKey -> texto|valor
  // Padrão ao montar/F5: "Solicitado em" começa nos últimos 7 dias (de = hoje-7).
  const [dateFilters, setDateFilters] = useState(() => ({ requested_at: { from: ymdDaysAgo(7) } })); // colKey -> {from, to}
  const [sort, setSort] = useState({ key: 'requested_at', dir: -1 });
  const [detail, setDetail] = useState(null);        // suggestion dict | null
  const [confirm, setConfirm] = useState(null);      // {row, action} | null

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiJson(`${apiBase}/suggestions?limit=500`);
      if (r.ok) { setRows(Array.isArray(r.body.data) ? r.body.data : []); setError(''); }
      else { setError((r.body && r.body.error) || 'Falha ao carregar.'); }
    } catch (e) { setError(String(e.message || e)); }
    setLoading(false);
  }, [apiBase]);

  useEffect(() => { load(); }, [load]);

  // Deep-link ?detail=<id>: abre o detalhe no mount e em popstate.
  const openDetail = useCallback(async (id) => {
    try {
      const r = await apiJson(`${apiBase}/suggestions/${id}`);
      if (r.ok && r.body.data) { setDetail(r.body.data); writeUrlParam('detail', id); }
    } catch (_) { /* ignore */ }
  }, [apiBase]);

  useEffect(() => {
    const id = readUrlParam('detail');
    if (id) openDetail(id);
    const onPop = () => { const d = readUrlParam('detail'); if (d) openDetail(d); else setDetail(null); };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [openDetail]);

  // Live: recarrega no broadcast do plugin.
  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    try {
      ws = new WebSocket(`${proto}//${location.host}/ws`);
      ws.onmessage = (m) => { try { if (JSON.parse(m.data).event === 'plugin_melhorias_changed') load(); } catch (_) { /* ignore */ } };
    } catch (_) { /* ignore */ }
    return () => { try { ws && ws.close(); } catch (_) { /* ignore */ } };
  }, [load]);

  // Valores distintos p/ os filtros de seleção.
  function distinct(col) {
    const s = new Set();
    for (const r of rows) { const v = col.get(r); if (v !== '' && v != null) s.add(String(v)); }
    return Array.from(s).sort();
  }

  function setTextFilter(key, v) { setFilters((f) => ({ ...f, [key]: v })); }
  function setDateF(key, part, v) {
    setDateFilters((f) => ({ ...f, [key]: { ...(f[key] || {}), [part]: v } }));
  }

  const visible = useMemo(() => {
    let out = rows.filter((r) => cols.every((c) => {
      if (c.filter === 'text' || c.filter === 'select') {
        const fv = filters[c.key];
        if (!fv) return true;
        const cell = String(c.get(r) ?? '').toLowerCase();
        return c.filter === 'select' ? cell === String(fv).toLowerCase()
          : cell.includes(String(fv).toLowerCase());
      }
      if (c.filter === 'date') {
        const df = dateFilters[c.key] || {};
        const val = Number(c.get(r) || 0);
        if (df.from) { const f = new Date(df.from).getTime() / 1000; if (!val || val < f) return false; }
        if (df.to) { const t = new Date(df.to).getTime() / 1000 + 86400; if (!val || val > t) return false; }
        return true;
      }
      return true;
    }));
    const col = cols.find((c) => c.key === sort.key);
    if (col) {
      out = [...out].sort((a, b) => {
        const va = col.get(a), vb = col.get(b);
        if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sort.dir;
        return String(va).localeCompare(String(vb)) * sort.dir;
      });
    }
    return out;
  }, [rows, cols, filters, dateFilters, sort]);

  function toggleSort(key) {
    setSort((s) => (s.key === key ? { key, dir: -s.dir } : { key, dir: 1 }));
  }

  async function doDecide(row, action) {
    setConfirm(null);
    try {
      const r = await apiJson(`${apiBase}/suggestions/${row.id}/${action}`, { method: 'POST' });
      if (!r.ok) setError((r.body && r.body.error) || 'Falha na ação.');
      await load();
      if (detail && detail.id === row.id) openDetail(row.id);
    } catch (e) { setError(String(e.message || e)); }
  }

  return html`
    <div class="w-full px-5 py-4">
      <div class="flex items-center justify-between mb-4">
        <h1 class="text-[20px] font-semibold text-wa-text">Sugestões de melhoria</h1>
        <button onClick=${load} class="text-[13px] px-3 py-1.5 rounded-md border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">Recarregar</button>
      </div>
      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : ''}

      ${loading ? html`<div class="text-wa-secondary text-[13px]">Carregando…</div>` : html`
        <div class="overflow-x-auto border border-wa-border rounded-lg">
          <table class="w-full text-[12px]">
            <thead>
              <tr class="text-wa-secondary text-left bg-wa-bg">
                ${cols.map((c) => html`<th key=${c.key}
                  class="py-2 px-2 whitespace-nowrap cursor-pointer select-none hover:text-wa-text"
                  onClick=${() => toggleSort(c.key)}>
                  ${c.label} ${sort.key === c.key ? (sort.dir === 1 ? '▲' : '▼') : ''}
                </th>`)}
                ${canApprove ? html`<th class="py-2 px-2 whitespace-nowrap">Ações</th>` : ''}
              </tr>
              <tr class="bg-wa-bg border-t border-wa-border">
                ${cols.map((c) => html`<th key=${c.key} class="px-2 pt-3 pb-3 align-top">
                  ${c.filter === 'text' ? html`<input class="wa-field w-full rounded px-1.5 py-1 text-[11px]"
                      placeholder="filtrar…" value=${filters[c.key] || ''}
                      onInput=${(e) => setTextFilter(c.key, e.target.value)} />`
                  : c.filter === 'select' ? html`<select class="wa-field w-full rounded px-1 py-1 text-[11px]"
                      value=${filters[c.key] || ''} onChange=${(e) => setTextFilter(c.key, e.target.value)}>
                      <option value="">todos</option>
                      ${distinct(c).map((v) => html`<option key=${v} value=${v}>${STATUS_LABEL[v] || v}</option>`)}
                    </select>`
                  : c.filter === 'date' ? html`<div class="flex flex-row items-center gap-1 min-w-[230px]">
                      <input type="date" title="De" class="wa-field rounded px-1.5 py-1 text-[11px] flex-1 min-w-0"
                        value=${(dateFilters[c.key] || {}).from || ''} onInput=${(e) => setDateF(c.key, 'from', e.target.value)} />
                      <span class="text-[11px] text-wa-secondary">–</span>
                      <input type="date" title="Até" class="wa-field rounded px-1.5 py-1 text-[11px] flex-1 min-w-0"
                        value=${(dateFilters[c.key] || {}).to || ''} onInput=${(e) => setDateF(c.key, 'to', e.target.value)} />
                    </div>` : ''}
                </th>`)}
                ${canApprove ? html`<th></th>` : ''}
              </tr>
            </thead>
            <tbody>
              ${visible.length === 0
                ? html`<tr><td colspan=${cols.length + (canApprove ? 1 : 0)}
                    class="text-center text-wa-secondary py-6 text-[13px]">Nenhuma sugestão.</td></tr>`
                : visible.map((r) => html`<tr key=${r.id}
                    class="border-t border-wa-border text-wa-text align-top hover:bg-wa-hover">
                    ${cols.map((c) => html`<td key=${c.key}
                      class="py-2 px-2 ${c.nowrap ? 'whitespace-nowrap' : ''} ${c.key === 'conversation' ? '' : 'cursor-pointer'}"
                      onClick=${c.key === 'conversation' ? undefined : () => openDetail(r.id)}>
                      ${c.render ? c.render(r) : (c.get(r) || '—')}
                    </td>`)}
                    ${canApprove ? html`<td class="py-2 px-2 whitespace-nowrap">
                      ${r.status === 'pendente' ? html`<div class="flex gap-1.5">
                        <button onClick=${() => setConfirm({ row: r, action: 'approve' })}
                          class="px-2 py-1 rounded border border-wa-teal text-wa-teal text-[11px] hover:bg-wa-teal/10">Aprovar</button>
                        <button onClick=${() => setConfirm({ row: r, action: 'reject' })}
                          class="px-2 py-1 rounded border border-red-400 text-red-500 text-[11px] hover:bg-red-500/10">Recusar</button>
                      </div>` : html`<span class="text-wa-secondary text-[11px]">—</span>`}
                    </td>` : ''}
                  </tr>`)}
            </tbody>
          </table>
        </div>`}

      ${detail ? html`<${DetailModal} detail=${detail} canApprove=${canApprove}
        onClose=${() => { setDetail(null); writeUrlParam('detail', null); }}
        onDecide=${(action) => setConfirm({ row: detail, action })} />` : ''}

      ${confirm ? html`<${ConfirmDialog}
        message=${confirm.action === 'approve'
          ? 'Aprovar esta sugestão? A análise da IA será gerada agora.'
          : 'Recusar esta sugestão?'}
        onOk=${() => doDecide(confirm.row, confirm.action)}
        onCancel=${() => setConfirm(null)} />` : ''}
    </div>`;
}

function DetailModal({ detail, canApprove, onClose, onDecide }) {
  const d = detail;
  return html`
    <div class="fixed inset-0 z-[120] bg-black/40 flex items-center justify-center p-4" onClick=${onClose}>
      <div class="bg-wa-panel rounded-lg shadow-xl w-[640px] max-w-[94vw] max-h-[88vh] overflow-auto p-[22px]"
        onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-[16px] font-semibold text-wa-text">Sugestão #${d.id}</h2>
          <button onClick=${onClose} class="text-wa-secondary hover:text-wa-text text-[18px]">✕</button>
        </div>
        <${Field} label="Status" value=${STATUS_LABEL[d.status] || d.status} />
        <${Field} label="Mensagem da IA" value=${d.message_content || '—'} pre />
        <${Field} label="Solicitante" value=${d.requester_name || '—'} />
        <${Field} label="Melhoria solicitada (nota do operador)" value=${d.feedback || '—'} pre />
        <${Field} label="Análise gerada pela IA"
          value=${d.status === 'aprovada' ? (d.analysis || '—') : '(gerada apenas na aprovação)'} pre />
        <${Field} label="Modelo" value=${d.model || '—'} />
        <${Field} label="Solicitado em" value=${fmtTs(d.requested_at)} />
        <${Field} label="Aprovado em" value=${d.status === 'aprovada' ? fmtTs(d.decided_at) : '—'} />
        <${Field} label="Aprovador" value=${d.status === 'aprovada' ? (d.approver_name || '—') : '—'} />
        ${d.conversation_url ? html`<div class="mt-2">
          <a href=${d.conversation_url} onClick=${(e) => navConversation(e, d)}
            class="text-wa-teal hover:underline text-[13px]">Abrir conversa nesta mensagem ↗</a>
        </div>` : ''}
        ${canApprove && d.status === 'pendente' ? html`<div class="flex justify-end gap-2 mt-5">
          <button onClick=${() => onDecide('reject')}
            class="px-4 py-2 rounded-full border border-red-400 text-red-500 text-[13px] hover:bg-red-500/10">Recusar</button>
          <button onClick=${() => onDecide('approve')}
            class="px-4 py-2 rounded-full bg-wa-teal text-white text-[13px] font-medium hover:opacity-90">Aprovar</button>
        </div>` : ''}
      </div>
    </div>`;
}

function Field({ label, value, pre }) {
  return html`<div class="mb-3">
    <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">${label}</div>
    <div class="text-[13px] text-wa-text ${pre ? 'whitespace-pre-wrap bg-wa-bg border border-wa-border rounded p-2 max-h-[220px] overflow-auto' : ''}">${value}</div>
  </div>`;
}

function ConfirmDialog({ message, onOk, onCancel }) {
  return html`
    <div class="fixed inset-0 z-[140] bg-black/40 flex items-center justify-center p-4" onClick=${onCancel}>
      <div class="bg-wa-panel rounded-lg shadow-xl w-[360px] max-w-[92vw] p-[22px]" onClick=${(e) => e.stopPropagation()}>
        <div class="text-[15px] text-wa-text mb-[20px]">${message}</div>
        <div class="flex justify-end gap-[10px]">
          <button onClick=${onCancel} class="px-[16px] py-[8px] rounded-full border border-wa-border text-wa-text text-[14px] hover:bg-wa-hover transition-colors">Cancelar</button>
          <button onClick=${onOk} class="px-[16px] py-[8px] rounded-full bg-wa-teal text-white text-[14px] font-medium hover:opacity-90 transition-opacity">Confirmar</button>
        </div>
      </div>
    </div>`;
}
