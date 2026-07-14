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
import { useState, useEffect, useCallback, useMemo, useRef, useLayoutEffect } from 'preact/hooks';
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
// Opções fixas do filtro multi-seleção de Status (o conjunto é conhecido — não
// depende dos valores presentes nas linhas carregadas).
const STATUS_OPTIONS = [
  { value: 'pendente', label: 'Pendente' },
  { value: 'aprovada', label: 'Aprovada' },
  { value: 'recusada', label: 'Recusada' },
];

// Colunas: {key, label, nowrap, get(r) -> valor p/ filtro/sort, render(r) -> markup}.
function buildCols() {
  return [
    { key: 'message_content', label: 'Mensagem', filter: 'text',
      get: (r) => r.message_content || '',
      render: (r) => html`<span class="line-clamp-2 max-w-[280px] inline-block align-top">${(r.message_content || '').trim() || '—'}</span>` },
    { key: 'contact', label: 'Contato', filter: 'text',
      get: (r) => `${r.contact_name || ''} ${r.contact_phone || ''}`.trim(),
      render: (r) => html`<div class="max-w-[170px]">
        <div class="truncate">${(r.contact_name || '').trim() || '—'}</div>
        ${r.contact_phone ? html`<div class="text-[11px] text-wa-secondary truncate">${r.contact_phone}</div>` : ''}
      </div>` },
    { key: 'requester_name', label: 'Solicitante', filter: 'multiselect',
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
    { key: 'status', label: 'Status', filter: 'multiselect', options: STATUS_OPTIONS,
      get: (r) => r.status || '',
      render: (r) => html`<span class="font-medium ${STATUS_CLS[r.status] || ''}">${STATUS_LABEL[r.status] || r.status}</span>` },
    { key: 'approver_name', label: 'Aprovador', filter: 'multiselect',
      get: (r) => (r.status === 'aprovada' ? (r.approver_name || '') : ''),
      render: (r) => (r.status === 'aprovada' ? (r.approver_name || '—') : '—') },
    { key: 'model', label: 'Modelo', filter: 'multiselect', nowrap: true,
      get: (r) => r.model || '',
      render: (r) => html`<span class="text-[11px] text-wa-secondary">${r.model || '—'}</span>` },
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
  const [filters, setFilters] = useState({});       // colKey -> texto (text) | array (multiselect)
  const [pageSize, setPageSize] = useState(10);      // itens por página (10/20/50/100)
  const [page, setPage] = useState(0);               // página atual (base 0)
  // Padrão ao montar/F5: "Solicitado em" começa nos últimos 7 dias (de = hoje-7).
  const [dateFilters, setDateFilters] = useState(() => ({ requested_at: { from: ymdDaysAgo(7) } })); // colKey -> {from, to}
  const [sort, setSort] = useState({ key: 'requested_at', dir: -1 });
  const [detail, setDetail] = useState(null);        // suggestion dict | null
  const [confirm, setConfirm] = useState(null);      // {row, action} | null
  const [generating, setGenerating] = useState(false); // aprovando: IA gerando a análise (backend gera a análise dentro do próprio request de approve)

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiJson(`${apiBase}/suggestions?limit=100`);
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

  // Opções {value,label} de uma coluna multi-seleção: fixas quando a coluna as
  // declara (Status), senão derivadas dos valores presentes nas linhas.
  function optionsFor(col) {
    if (col.options) return col.options;
    return distinct(col).map((v) => ({ value: v, label: STATUS_LABEL[v] || v }));
  }

  function setTextFilter(key, v) { setFilters((f) => ({ ...f, [key]: v })); }
  function setMultiFilter(key, arr) { setFilters((f) => ({ ...f, [key]: arr })); }
  function clearFilters() { setFilters({}); setDateFilters({}); }
  function setDateF(key, part, v) {
    setDateFilters((f) => ({ ...f, [key]: { ...(f[key] || {}), [part]: v } }));
  }
  // Presets: "Hoje" = só o dia atual; "Nd" = dos últimos N dias até agora.
  function setDatePreset(key, days) {
    setDateFilters((f) => ({ ...f, [key]: { from: ymdDaysAgo(days), to: days === 0 ? ymdDaysAgo(0) : '' } }));
  }
  function clearDate(key) {
    setDateFilters((f) => { const n = { ...f }; delete n[key]; return n; });
  }

  const visible = useMemo(() => {
    let out = rows.filter((r) => cols.every((c) => {
      if (c.filter === 'text') {
        const fv = filters[c.key];
        if (!fv) return true;
        return String(c.get(r) ?? '').toLowerCase().includes(String(fv).toLowerCase());
      }
      if (c.filter === 'multiselect') {
        const arr = filters[c.key];
        if (!Array.isArray(arr) || arr.length === 0) return true;
        const cell = String(c.get(r) ?? '').toLowerCase();
        return arr.some((v) => String(v).toLowerCase() === cell);
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

  const hasActiveFilters = Object.values(filters).some((v) => (Array.isArray(v) ? v.length > 0 : (v != null && v !== '')))
    || Object.values(dateFilters).some((d) => d && (d.from || d.to));

  // Volta para a 1ª página quando a filtragem ou o tamanho da página muda
  // (evita ficar preso numa página que deixou de existir).
  useEffect(() => { setPage(0); }, [filters, dateFilters, pageSize]);

  // Paginação client-side sobre os resultados já filtrados.
  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const paged = visible.slice(safePage * pageSize, safePage * pageSize + pageSize);

  function toggleSort(key) {
    setSort((s) => (s.key === key ? { key, dir: -s.dir } : { key, dir: 1 }));
  }

  async function doDecide(row, action) {
    setConfirm(null);
    // Aprovar gera a análise da IA de forma síncrona no backend (dentro do próprio
    // POST /approve). Mostra o overlay de carregamento enquanto o request está no ar
    // e o fecha assim que ele responde — ou seja, quando a IA termina.
    const isApprove = action === 'approve';
    if (isApprove) setGenerating(true);
    try {
      const r = await apiJson(`${apiBase}/suggestions/${row.id}/${action}`, { method: 'POST' });
      if (isApprove) setGenerating(false); // IA concluiu (a resposta já traz a análise)
      if (!r.ok) setError((r.body && r.body.error) || 'Falha na ação.');
      await load();
      if (detail && detail.id === row.id) openDetail(row.id);
    } catch (e) { setError(String(e.message || e)); }
    finally { if (isApprove) setGenerating(false); } // rede de segurança: fecha também em erro
  }

  return html`
    <div class="w-full px-5 py-4">
      <div class="flex items-center justify-between gap-2 mb-3">
        <h2 class="text-[16px] font-semibold text-wa-text">Filtros</h2>
        <div class="flex items-center gap-2">
          ${hasActiveFilters ? html`<button onClick=${clearFilters}
            class="text-[13px] px-3 py-1.5 rounded-md border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">Limpar filtros</button>` : ''}
          <button onClick=${load} class="text-[13px] px-3 py-1.5 rounded-md border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">Recarregar</button>
        </div>
      </div>
      ${rows.length >= 100 ? html`<div class="text-[11px] text-wa-secondary mb-3">Mostrando as 100 sugestões mais recentes — refine pelos filtros (o restante ainda não é carregado).</div>` : ''}
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
                  : c.filter === 'multiselect' ? html`<${MultiSelectFilter}
                      options=${optionsFor(c)} selected=${filters[c.key] || []}
                      onChange=${(arr) => setMultiFilter(c.key, arr)} />`
                  : c.filter === 'date' ? html`<div class="min-w-[230px]">
                      <div class="flex flex-row items-center gap-1">
                        <input type="date" title="De" class="wa-field rounded px-1.5 py-1 text-[11px] flex-1 min-w-0"
                          value=${(dateFilters[c.key] || {}).from || ''} onInput=${(e) => setDateF(c.key, 'from', e.target.value)} />
                        <span class="text-[11px] text-wa-secondary">–</span>
                        <input type="date" title="Até" class="wa-field rounded px-1.5 py-1 text-[11px] flex-1 min-w-0"
                          value=${(dateFilters[c.key] || {}).to || ''} onInput=${(e) => setDateF(c.key, 'to', e.target.value)} />
                      </div>
                      <div class="flex flex-wrap gap-1 mt-1">
                        ${[['Hoje', 0], ['7d', 7], ['30d', 30]].map(([lbl, d]) => html`<button key=${lbl} type="button"
                          onClick=${() => setDatePreset(c.key, d)}
                          class="text-[10px] px-1.5 py-0.5 rounded border border-wa-border text-wa-secondary hover:bg-wa-hover">${lbl}</button>`)}
                        <button type="button" onClick=${() => clearDate(c.key)}
                          class="text-[10px] px-1.5 py-0.5 rounded border border-wa-border text-wa-secondary hover:bg-wa-hover">Tudo</button>
                      </div>
                    </div>` : ''}
                </th>`)}
                ${canApprove ? html`<th></th>` : ''}
              </tr>
            </thead>
            <tbody>
              ${visible.length === 0
                ? html`<tr><td colspan=${cols.length + (canApprove ? 1 : 0)}
                    class="text-center text-wa-secondary py-6 text-[13px]">Nenhuma sugestão.</td></tr>`
                : paged.map((r) => html`<tr key=${r.id}
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
        </div>
        <div class="flex flex-wrap items-center justify-between gap-3 mt-3 text-[12px] text-wa-secondary">
          <div class="flex items-center gap-2">
            <span>Itens por página:</span>
            <select class="wa-field rounded px-2 py-1 text-[12px]" value=${pageSize}
              onChange=${(e) => setPageSize(Number(e.target.value))}>
              ${[10, 20, 50, 100].map((n) => html`<option key=${n} value=${n}>${n}</option>`)}
            </select>
          </div>
          <div class="flex items-center gap-2">
            <span>${visible.length === 0 ? '0' : `${safePage * pageSize + 1}–${Math.min((safePage + 1) * pageSize, visible.length)}`} de ${visible.length}</span>
            <button type="button" disabled=${safePage <= 0} onClick=${() => setPage(Math.max(0, safePage - 1))}
              class="px-2 py-1 rounded border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-40 disabled:cursor-not-allowed">‹ Anterior</button>
            <span>Página ${safePage + 1} de ${pageCount}</span>
            <button type="button" disabled=${safePage >= pageCount - 1} onClick=${() => setPage(Math.min(pageCount - 1, safePage + 1))}
              class="px-2 py-1 rounded border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-40 disabled:cursor-not-allowed">Próxima ›</button>
          </div>
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

      ${generating ? html`<${GeneratingModal} />` : ''}
    </div>`;
}

// Overlay bloqueante mostrado enquanto a IA gera a análise de uma sugestão aprovada.
// Sem fechar manual: some sozinho quando a geração termina (ver doDecide).
function GeneratingModal() {
  return html`
    <div class="fixed inset-0 z-[150] bg-black/40 flex items-center justify-center p-4">
      <div class="bg-wa-panel rounded-lg shadow-xl px-7 py-6 max-w-sm flex items-center gap-4">
        <div class="w-7 h-7 border-2 border-wa-teal border-t-transparent rounded-full animate-spin shrink-0"></div>
        <div>
          <div class="text-[14px] font-medium text-wa-text">Gerando análise da IA…</div>
          <div class="text-[12px] text-wa-secondary mt-1">Isso pode levar alguns instantes. A janela fecha sozinha ao concluir.</div>
        </div>
      </div>
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

// Filtro multi-seleção (checkboxes) das colunas Solicitante/Status/Aprovador. O
// painel abre com position:fixed calculado a partir do botão para NÃO ser cortado
// pelo overflow-x-auto da tabela (mesma ideia do flyout do menu de contexto).
function MultiSelectFilter({ options = [], selected = [], onChange }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const btnRef = useRef(null);
  const panelRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (panelRef.current && panelRef.current.contains(e.target)) return;
      if (btnRef.current && btnRef.current.contains(e.target)) return;
      setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !btnRef.current) return;
    const r = btnRef.current.getBoundingClientRect();
    const margin = 6, maxH = 264;
    const width = Math.max(190, r.width);
    let left = Math.min(r.left, window.innerWidth - width - margin);
    if (left < margin) left = margin;
    let top = r.bottom + 4;
    if (top + maxH > window.innerHeight) top = Math.max(margin, window.innerHeight - maxH - margin);
    setPos({ left, top, width });
  }, [open]);

  const toggle = (v) => {
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  };
  const label = selected.length === 0
    ? 'todos'
    : (selected.length === 1
        ? ((options.find((o) => o.value === selected[0]) || {}).label || selected[0])
        : `${selected.length} selecionados`);

  return html`
    <button ref=${btnRef} type="button" onClick=${() => setOpen((o) => !o)}
      class="wa-field w-full rounded px-1.5 py-1 text-[11px] text-left flex items-center justify-between gap-1 ${selected.length ? 'text-wa-text font-medium' : 'text-wa-secondary'}">
      <span class="truncate">${label}</span>
      <span class="shrink-0 text-wa-secondary">▾</span>
    </button>
    ${open && pos ? html`
      <div ref=${panelRef}
        class="fixed z-[130] bg-wa-panel border border-wa-border rounded-md shadow-lg py-1 max-h-[260px] overflow-auto wa-scrollbar"
        style="left:${pos.left}px;top:${pos.top}px;width:${pos.width}px">
        ${options.length === 0
          ? html`<div class="px-2 py-1 text-[11px] text-wa-secondary">(sem opções)</div>`
          : options.map((o) => html`
              <label key=${o.value}
                class="flex items-center gap-2 px-2 py-1 text-[12px] text-wa-text hover:bg-wa-hover cursor-pointer">
                <input type="checkbox" checked=${selected.includes(o.value)}
                  onChange=${() => toggle(o.value)} />
                <span class="truncate">${o.label}</span>
              </label>`)}
        ${selected.length ? html`<button type="button" onClick=${() => onChange([])}
          class="w-full text-left px-2 py-1 mt-1 text-[11px] text-wa-teal hover:bg-wa-hover border-t border-wa-border">Limpar seleção</button>` : ''}
      </div>` : ''}
  `;
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
