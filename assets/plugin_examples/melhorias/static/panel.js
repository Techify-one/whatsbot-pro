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
import { subscribe as subscribeWs } from '/static/js/services/wsBus.js';
import { AgenticChat } from '/plugins/melhorias/static/chat.js';
import { ReloginModal } from '/plugins/melhorias/static/relogin.js';
import { renderMarkdown } from '/plugins/melhorias/static/markdown.js';

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

// 'concluida' (plano 58) = encerramento MANUAL do chat pelo operador; verde-claro
// para distinguir do teal de 'aprovada' (auto-COMPLETED do executor).
const STATUS_LABEL = { pendente: 'Pendente', em_chat: 'Em chat com a IA', aprovada: 'Aprovada', recusada: 'Recusada', concluida: 'Concluída' };
const STATUS_CLS = {
  pendente: 'text-amber-600', em_chat: 'text-blue-600', aprovada: 'text-wa-teal', recusada: 'text-red-500', concluida: 'text-green-600',
};
// Opções fixas do filtro multi-seleção de Status (o conjunto é conhecido — não
// depende dos valores presentes nas linhas carregadas).
const STATUS_OPTIONS = [
  { value: 'pendente', label: 'Pendente' },
  { value: 'em_chat', label: 'Em chat com a IA' },
  { value: 'aprovada', label: 'Aprovada' },
  { value: 'recusada', label: 'Recusada' },
  { value: 'concluida', label: 'Concluída' },
];

// Filtro padrão embutido (plano 58): usado no 1º render e quando não há padrão
// salvo no servidor. "Sempre as pendentes", sem restrição de data.
const BUILTIN_DEFAULT = {
  filters: { status: ['pendente'] }, dateFilters: {},
  sort: { key: 'requested_at', dir: -1 }, pageSize: 10,
};

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
      // O atendimento marcado pode ter sido APAGADO depois do pedido (o histórico
      // do plugin sobrevive à limpeza do core). Nesse caso o backend devolve o
      // link do CONTATO — que o core abre no atendimento atual dele — e o rótulo
      // diz isso, em vez de um "Abrir conversa" que caía na tela principal vazia.
      render: (r) => {
        if (!r.conversation_url) {
          return html`<span class="text-wa-secondary whitespace-nowrap" title="O atendimento marcado foi excluído.">conversa excluída</span>`;
        }
        const fallback = r.conversation_link_kind === 'contact';
        return html`<a href=${r.conversation_url} onClick=${(e) => navConversation(e, r)}
          title=${fallback ? 'O atendimento marcado foi excluído — abre o atendimento atual deste contato.' : ''}
          class="text-wa-teal hover:underline whitespace-nowrap">
          ${fallback ? 'Abrir contato ↗' : 'Abrir conversa ↗'}</a>`;
      } },
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
  // plano 51: backend agêntico? (external ⇒ aprovar abre o CHAT, não gera inline).
  // Desde a 1.3.0 o agêntico é o motor padrão/único da UI — 'external' é o fallback.
  const [backend, setBackend] = useState('external');
  const [filters, setFilters] = useState(() => ({ ...BUILTIN_DEFAULT.filters }));   // colKey -> texto | array
  const [pageSize, setPageSize] = useState(BUILTIN_DEFAULT.pageSize);               // itens por página
  const [page, setPage] = useState(0);               // página atual (base 0)
  const [dateFilters, setDateFilters] = useState(() => ({ ...BUILTIN_DEFAULT.dateFilters })); // colKey -> {from, to}
  const [sort, setSort] = useState(() => ({ ...BUILTIN_DEFAULT.sort }));
  const [savedDefault, setSavedDefault] = useState(null);   // plano 58: filtro padrão salvo no servidor
  const [savingDefault, setSavingDefault] = useState(false);
  const [detail, setDetail] = useState(null);        // suggestion dict | null
  const [confirm, setConfirm] = useState(null);      // {row, action} | null
  const [generating, setGenerating] = useState(false); // aprovando: IA gerando a análise inline (backend direto legado)
  // plano 60: estado GLOBAL da sessão do Claude no executor ({status:'ok'|'expired'}).
  const [aiSession, setAiSession] = useState({ status: 'ok' });
  const [panelRelogin, setPanelRelogin] = useState(false);
  const sessionExpired = (aiSession || {}).status === 'expired';

  // Aplica um conjunto de filtro (do servidor ou o embutido) ao estado da UI.
  const applyDefaultFilter = useCallback((def) => {
    const d = def || BUILTIN_DEFAULT;
    setFilters(d.filters && typeof d.filters === 'object' ? { ...d.filters } : {});
    setDateFilters(d.dateFilters && typeof d.dateFilters === 'object' ? { ...d.dateFilters } : {});
    setSort(d.sort && d.sort.key ? { key: d.sort.key, dir: d.sort.dir === 1 ? 1 : -1 } : { ...BUILTIN_DEFAULT.sort });
    setPageSize(d.pageSize ? (Number(d.pageSize) || BUILTIN_DEFAULT.pageSize) : BUILTIN_DEFAULT.pageSize);
  }, []);

  // Backend agêntico + filtro padrão salvo no servidor (plano 58). Sem padrão
  // salvo, fica o embutido (pendentes) já semeado nos states acima.
  useEffect(() => {
    (async () => {
      try {
        const r = await apiJson(`${apiBase}/config`);
        if (r.ok) {
          const data = r.body.data || {};
          setBackend(data.generator_backend || 'external');
          if (data.ai_session && typeof data.ai_session === 'object') setAiSession(data.ai_session);
          const def = (data.default_filter && typeof data.default_filter === 'object') ? data.default_filter : null;
          setSavedDefault(def);
          if (def) applyDefaultFilter(def);
        }
      } catch (_) { /* mantém external + default embutido */ }
    })();
  }, [apiBase, applyDefaultFilter]);

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

  // Live: recarrega no broadcast do plugin. Via wsBus do core (conexão única
  // AUTENTICADA — WebSocket cru sem ?token= é fechado com 4401 sob RBAC e a
  // lista só atualizava no F5).
  useEffect(() => subscribeWs({
    plugin_melhorias_changed: () => load(),
    // plano 60 · 1.6: o servidor avisa TODOS os painéis quando a sessão morre
    // (ou volta) — mesmo mecanismo já usado para `plugin_melhorias_changed`.
    plugin_melhorias_ai_session: (d) => setAiSession(d && typeof d === 'object' ? d : { status: 'ok' }),
  }), [load]);

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
  // plano 58: filtro padrão compartilhado (servidor). "Padrão" reaplica o salvo
  // (ou o embutido); "Salvar filtro padrão" persiste o conjunto atual p/ todos.
  function restoreDefault() { applyDefaultFilter(savedDefault); }
  async function saveDefaultFilter() {
    if (savingDefault) return;
    setSavingDefault(true); setError('');
    try {
      const filter = { filters, dateFilters, sort, pageSize };
      const r = await apiJson(`${apiBase}/default-filter`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filter }) });
      if (r.ok) setSavedDefault(filter);
      else setError((r.body && r.body.error) || 'Falha ao salvar o filtro padrão.');
    } catch (e) { setError(String(e.message || e)); }
    setSavingDefault(false);
  }
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

  // plano 58: encerrar manualmente a melhoria — fecha a conversa mais recente
  // (COMPLETED) e marca a sugestão como 'concluida'.
  async function concludeSuggestion(row) {
    setConfirm(null); setError('');
    try {
      const rc = await apiJson(`${apiBase}/suggestions/${row.id}/conversations`);
      const list = (rc.ok && Array.isArray(rc.body.data)) ? rc.body.data : [];
      const cid = (list[0] || {}).id;
      if (!cid) { setError('Não há conversa para encerrar.'); return; }
      const r = await apiJson(`${apiBase}/conversations/${cid}/complete`, { method: 'POST' });
      if (!r.ok) { setError((r.body && r.body.error) || 'Falha ao encerrar a melhoria.'); return; }
      // Encerrada: fecha o modal (evita chat com estado defasado) e recarrega.
      if (detail && detail.id === row.id) { setDetail(null); writeUrlParam('detail', null); }
      await load();
    } catch (e) { setError(String(e.message || e)); }
  }

  return html`
    <div class="w-full px-5 py-4">
      ${sessionExpired ? html`
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-400 bg-amber-50 px-3 py-2">
          <span class="text-[13px] text-amber-700">
            <strong>Sessão do Claude expirada no executor.</strong>
            As conversas em andamento estão paradas e novas análises não podem começar até renovar.
          </span>
          ${canApprove ? html`<button onClick=${() => setPanelRelogin(true)}
            class="shrink-0 px-3 py-1.5 rounded-full bg-wa-teal text-white text-[12px] font-medium hover:opacity-90">
            Renovar sessão</button>` : ''}
        </div>` : ''}
      <div class="flex items-center justify-between gap-2 mb-3">
        <h2 class="text-[16px] font-semibold text-wa-text">Filtros</h2>
        <div class="flex items-center gap-2">
          ${hasActiveFilters ? html`<button onClick=${clearFilters}
            class="text-[13px] px-3 py-1.5 rounded-md border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">Limpar filtros</button>` : ''}
          <button onClick=${restoreDefault}
            class="text-[13px] px-3 py-1.5 rounded-md border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">Padrão</button>
          ${canApprove ? html`<button onClick=${saveDefaultFilter} disabled=${savingDefault}
            class="text-[13px] px-3 py-1.5 rounded-md border border-wa-teal text-wa-teal hover:bg-wa-teal/10 transition-colors disabled:opacity-50">
            ${savingDefault ? 'Salvando…' : 'Salvar filtro padrão'}</button>` : ''}
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
                        ${backend === 'external' ? html`
                          <button onClick=${() => openDetail(r.id)}
                            class="px-2 py-1 rounded border border-wa-teal text-wa-teal text-[11px] hover:bg-wa-teal/10">Abrir chat</button>
                        ` : html`
                          <button onClick=${() => setConfirm({ row: r, action: 'approve' })}
                            class="px-2 py-1 rounded border border-wa-teal text-wa-teal text-[11px] hover:bg-wa-teal/10">Aprovar</button>
                        `}
                        <button onClick=${() => setConfirm({ row: r, action: 'reject' })}
                          class="px-2 py-1 rounded border border-red-400 text-red-500 text-[11px] hover:bg-red-500/10">Recusar</button>
                      </div>` : r.status === 'em_chat' ? html`<div class="flex gap-1.5">
                        <button onClick=${() => openDetail(r.id)}
                          class="px-2 py-1 rounded border border-blue-400 text-blue-600 text-[11px] hover:bg-blue-500/10">Continuar chat</button>
                        <button onClick=${() => setConfirm({ row: r, action: 'conclude' })}
                          class="px-2 py-1 rounded border border-wa-teal text-wa-teal text-[11px] hover:bg-wa-teal/10">Encerrar</button>
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
        backend=${backend} apiBase=${apiBase} sessionExpired=${sessionExpired}
        onRefresh=${() => { load(); openDetail(detail.id); }}
        onClose=${() => { setDetail(null); writeUrlParam('detail', null); }}
        onDecide=${(action) => setConfirm({ row: detail, action })} />` : ''}

      ${confirm ? html`<${ConfirmDialog}
        message=${confirm.action === 'approve'
          ? 'Aprovar esta sugestão? A análise da IA será gerada agora.'
          : confirm.action === 'conclude'
          ? 'Encerrar esta melhoria? A conversa com a IA será fechada e a sugestão marcada como concluída.'
          : 'Recusar esta sugestão?'}
        onOk=${() => (confirm.action === 'conclude'
          ? concludeSuggestion(confirm.row)
          : doDecide(confirm.row, confirm.action))}
        onCancel=${() => setConfirm(null)} />` : ''}

      ${generating ? html`<${GeneratingModal} />` : ''}

      ${panelRelogin ? html`<${ReloginModal} apiJson=${apiJson} apiBase=${apiBase}
        onClose=${() => setPanelRelogin(false)}
        onSuccess=${() => setAiSession({ status: 'ok' })} />` : ''}
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

function DetailModal({ detail, canApprove, backend = 'external', apiBase = '/api/plugins/melhorias',
                       sessionExpired = false, onClose, onDecide, onRefresh = () => {} }) {
  const d = detail;
  const agentic = backend === 'external';
  // Mensagens marcadas: multi-seleção (d.messages) ou a âncora legada.
  const targets = (d.messages && d.messages.length)
    ? d.messages
    : [{ content: d.message_content, ts: d.message_ts, _id: d.message_db_id }];

  // Chat agêntico (plano 51 · 04 F3/F4): estado do gate (a) + conversa ativa.
  const [observation, setObservation] = useState('');
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');
  const [conversation, setConversation] = useState(null);
  const [relogin, setRelogin] = useState(false);
  // plano 60 · 1.2: gatilho de re-hidratação do chat SEM desmontá-lo.
  const [reloadKey, setReloadKey] = useState(0);
  const [chatNotice, setChatNotice] = useState('');

  // Sugestão em_chat: carrega a conversa mais recente ao abrir.
  useEffect(() => {
    if (!agentic || d.status !== 'em_chat') return;
    (async () => {
      try {
        const r = await apiJson(`${apiBase}/suggestions/${d.id}/conversations`);
        const list = (r.ok && Array.isArray(r.body.data)) ? r.body.data : [];
        if (list.length) setConversation(list[0]);
      } catch (_) { /* ignore */ }
    })();
  }, [agentic, d.id, d.status]);

  async function startChat() {
    if (starting) return;
    setStarting(true); setStartError('');
    try {
      const r = await apiJson(`${apiBase}/suggestions/${d.id}/conversations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ observation: (observation || '').trim() }),
      });
      if (r.ok && r.body.data && r.body.data.conversation) {
        setConversation(r.body.data.conversation);
        onRefresh();
      } else {
        setStartError((r.body && r.body.error) || 'Falha ao iniciar a conversa.');
      }
    } catch (e) { setStartError(String(e.message || e)); }
    setStarting(false);
  }

  // Renovar a sessão RECUPERA a conversa em vez de matá-la (plano 60 · 1.1):
  // `resume` é a primitiva que reconstrói o runner com a credencial nova. Nada
  // de auto-reenvio da mensagem que falhou — o executor pode tê-la processado
  // parcialmente; o operador reenvia, avisado pelo aviso abaixo.
  async function onReloginSuccess() {
    setChatNotice(''); setStartError('');
    const cid = conversation && conversation.id;
    if (!cid) { onRefresh(); return; }
    try {
      const r = await apiJson(`${apiBase}/conversations/${cid}/resume`, { method: 'POST' });
      if (r.ok && r.body.data) {
        setConversation(r.body.data);
        setChatNotice('Sessão renovada — pode reenviar sua mensagem.');
      } else {
        setStartError((r.body && r.body.error) || 'Sessão renovada, mas a conversa não retomou.');
      }
    } catch (e) { setStartError(String(e.message || e)); }
    setReloadKey((k) => k + 1);   // re-hidrata mantendo o chat na tela
    onRefresh();
  }

  const showGateA = agentic && canApprove && d.status === 'pendente' && !conversation;
  const showChat = agentic && conversation;

  return html`
    <div class="fixed inset-0 z-[120] bg-black/40 flex items-center justify-center p-4" onClick=${onClose}>
      <div class="bg-wa-panel rounded-lg shadow-xl w-[min(1200px,96vw)] h-[92vh] flex flex-col overflow-hidden p-0"
        onClick=${(e) => e.stopPropagation()}>
        <div class="px-[22px] py-3 border-b border-wa-border flex items-center justify-between shrink-0">
          <h2 class="text-[16px] font-semibold text-wa-text">Sugestão #${d.id}</h2>
          <button onClick=${onClose} class="text-wa-secondary hover:text-wa-text text-[18px]">✕</button>
        </div>

        <div class="flex-1 min-h-0 flex flex-col lg:flex-row overflow-y-auto lg:overflow-hidden">
          <div class="${showChat ? 'lg:w-[380px] lg:shrink-0 lg:border-r border-wa-border lg:overflow-y-auto' : 'flex-1 lg:overflow-y-auto'} p-[22px]">
            <${Field} label="Status" value=${STATUS_LABEL[d.status] || d.status} />
            <div class="mb-3">
              <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">
                ${targets.length > 1 ? `Mensagens da IA marcadas (${targets.length})` : 'Mensagem da IA'}
              </div>
              <div class="flex flex-col gap-1 max-h-[160px] overflow-auto">
                ${targets.map((t, i) => html`<div key=${t._id || i}
                  class="text-[13px] text-wa-text whitespace-pre-wrap bg-wa-bg border border-wa-border rounded p-2">${(t.content || '').trim() || '—'}</div>`)}
              </div>
            </div>
            <${Field} label="Solicitante" value=${d.requester_name || '—'} />
            <${Field} label="Melhoria solicitada (nota do operador)" value=${d.feedback || '—'} pre />

            ${showGateA ? html`
              <div class="border border-wa-teal/40 bg-wa-teal/5 rounded-lg p-3 mb-3">
                <div class="text-[13px] font-medium text-wa-text mb-2">
                  Aprovar para a IA começar a analisar (você ainda aprova cada mudança que ela propuser)
                </div>
                <textarea class="wa-field w-full rounded p-2 text-[13px] mb-2" rows="2"
                  placeholder="Observação extra para a IA (opcional)…"
                  value=${observation} onInput=${(e) => setObservation(e.target.value)}></textarea>
                ${startError ? html`<div class="text-[12px] text-red-500 mb-2">${startError}</div>` : ''}
                ${sessionExpired ? html`
                  <div class="text-[12px] text-amber-700 mb-2">
                    Sessão do Claude expirada — renove a sessão antes de iniciar (o chat nasceria morto).
                  </div>` : ''}
                <div class="flex justify-end gap-2">
                  ${sessionExpired ? html`<button onClick=${() => setRelogin(true)}
                    class="px-4 py-2 rounded-full border border-wa-teal text-wa-teal text-[13px] font-medium hover:bg-wa-teal/10">
                    Renovar sessão</button>` : ''}
                  <button onClick=${startChat} disabled=${starting || sessionExpired}
                    class="px-4 py-2 rounded-full bg-wa-teal text-white text-[13px] font-medium hover:opacity-90 disabled:opacity-50">
                    ${starting ? 'Iniciando…' : 'Aprovar p/ iniciar'}</button>
                </div>
              </div>` : ''}

            ${!agentic || d.status !== 'pendente' ? ((d.status === 'aprovada' || d.status === 'concluida')
              ? html`<${Field} label="Análise gerada pela IA" value=${d.analysis || '—'} pre html />`
              : html`<${Field} label="Análise gerada pela IA"
                  value=${d.status === 'em_chat' ? '(em construção no chat)' : '(gerada apenas na aprovação)'} pre />`) : ''}
            <${Field} label="Solicitado em" value=${fmtTs(d.requested_at)} />
            <${Field} label="Aprovado em" value=${d.status === 'aprovada' ? fmtTs(d.decided_at) : '—'} />
            <${Field} label="Aprovador" value=${d.status === 'aprovada' ? (d.approver_name || '—') : '—'} />
            ${d.conversation_url ? html`<div class="mt-2">
              <a href=${d.conversation_url} onClick=${(e) => navConversation(e, d)}
                class="text-wa-teal hover:underline text-[13px]">
                ${d.conversation_link_kind === 'contact'
                  ? 'Abrir atendimento atual deste contato ↗'
                  : 'Abrir conversa nesta mensagem ↗'}</a>
              ${d.conversation_link_kind === 'contact' ? html`
                <div class="text-[11px] text-wa-secondary mt-1">
                  O atendimento marcado foi excluído — este link leva ao atendimento atual do contato.
                </div>` : ''}
            </div>` : html`<div class="mt-2 text-[13px] text-wa-secondary">
              Conversa excluída — não há mais para onde navegar.
            </div>`}
            ${canApprove && d.status === 'pendente' ? html`<div class="flex justify-end gap-2 mt-5">
              <button onClick=${() => onDecide('reject')}
                class="px-4 py-2 rounded-full border border-red-400 text-red-500 text-[13px] hover:bg-red-500/10">Recusar</button>
              ${!agentic ? html`<button onClick=${() => onDecide('approve')}
                class="px-4 py-2 rounded-full bg-wa-teal text-white text-[13px] font-medium hover:opacity-90">Aprovar</button>` : ''}
            </div>` : ''}
          </div>

          ${showChat ? html`
            <div class="flex-1 min-h-0 flex flex-col p-[22px]">
              <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1 shrink-0">Chat com a IA de melhoria</div>
              <${AgenticChat} apiJson=${apiJson} apiBase=${apiBase}
                suggestion=${d} conversation=${conversation}
                reloadKey=${reloadKey} notice=${chatNotice}
                onNoticeClear=${() => setChatNotice('')}
                onAuthError=${() => setRelogin(true)}
                onRenewSession=${() => setRelogin(true)}
                onConversationEnd=${onRefresh} />
              ${/* AUTH_EXPIRED fica DE FORA de propósito (plano 60 · 1.5): reiniciar
                    a análise abriria um chat novo, que é justamente a armadilha —
                    o caminho certo é renovar a sessão e retomar esta conversa. */
                (conversation.status === 'ERRORED' || conversation.status === 'CANCELLED') ? html`
                <div class="flex items-center justify-between gap-2 mt-2 shrink-0">
                  <span class="text-[12px] text-wa-secondary">
                    A conversa anterior não foi concluída — dá para reiniciar a análise do zero (novo contexto completo).
                  </span>
                  <button onClick=${startChat} disabled=${starting}
                    class="px-3 py-1.5 rounded-full bg-wa-teal text-white text-[12px] font-medium hover:opacity-90 disabled:opacity-50 shrink-0">
                    ${starting ? 'Iniciando…' : 'Reiniciar análise'}</button>
                </div>` : ''}
              ${startError ? html`<div class="text-[12px] text-red-500 mt-1 shrink-0">${startError}</div>` : ''}
              ${conversation.status === 'ACTIVE' && canApprove ? html`
                <div class="flex justify-end mt-2 shrink-0">
                  <button onClick=${() => onDecide('conclude')}
                    class="px-3 py-1.5 rounded-full border border-wa-teal text-wa-teal text-[12px] font-medium hover:bg-wa-teal/10">
                    Encerrar melhoria</button>
                </div>` : ''}
            </div>` : ''}
        </div>

        ${relogin ? html`<${ReloginModal} apiJson=${apiJson} apiBase=${apiBase}
          onClose=${() => setRelogin(false)}
          onSuccess=${onReloginSuccess} />` : ''}
      </div>
    </div>`;
}

function Field({ label, value, pre, html: asHtml }) {
  const boxCls = pre ? 'bg-wa-bg border border-wa-border rounded p-2 max-h-[220px] overflow-auto' : '';
  return html`<div class="mb-3">
    <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">${label}</div>
    ${asHtml
      ? html`<div class="markdown-body text-[13px] text-wa-text ${boxCls}"
          dangerouslySetInnerHTML=${{ __html: renderMarkdown(String(value || '')) }}></div>`
      : html`<div class="text-[13px] text-wa-text ${pre ? `whitespace-pre-wrap ${boxCls}` : ''}">${value}</div>`}
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
