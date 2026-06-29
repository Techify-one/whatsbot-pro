// Override da aba "Atendimentos". Aba dedicada à entidade ATENDIMENTO:
//  - Lista (colunas ordenáveis por clique) OU Kanban (agrupado por Status ou Atendente,
//    com drag-and-drop que ALTERA o estado: soltar numa coluna fecha/reabre ou atribui).
//  - Filtros: status, busca por cliente e PERÍODO de criação (intervalo + presets).
//  - Abrir um atendimento → detalhe; clicar numa de suas conversas → vai EXATAMENTE
//    àquele ponto da conversa no chat (?message=<_id>).
// Desativar o plugin → a aba volta 100% ao Atendimentos nativo.

import { h } from 'preact';
import { useState, useEffect, useCallback, useMemo, useRef } from 'preact/hooks';
import htm from 'htm';
import { ConversasTable } from '/plugins/atendimentos/static/conversas_table.js';
import { ResolveForm } from '/plugins/atendimentos/static/resolve_form.js';

const html = htm.bind(h);

const MODE_KEY = 'whatsbot_atendimentos_mode';    // 'lista' | 'kanban'
const KGROUP_KEY = 'whatsbot_atendimentos_kgroup'; // 'status' | 'atendente'
const CARD_FIELDS_KEY = 'whatsbot_atend_kanban_card_fields'; // atributos ocultos no card (por-usuário)
function lsGet(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
function lsGetJSON(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } }
function lsSetJSON(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } }

function fmtTs(ts) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }); }
  catch (e) { return '—'; }
}

function fmtCell(v, def) {
  if (def.type === 'checkbox') return v ? 'Sim' : 'Não';
  return (v == null || v === '') ? '—' : String(v);
}

// yyyy-mm-dd local (para os <input type=date>) e conversão p/ epoch (segundos).
function ymd(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
function dayStartEpoch(s) { if (!s) return null; const d = new Date(`${s}T00:00:00`); return Number.isNaN(d) ? null : Math.floor(d.getTime() / 1000); }
function dayEndEpoch(s) { if (!s) return null; const d = new Date(`${s}T23:59:59`); return Number.isNaN(d) ? null : Math.floor(d.getTime() / 1000); }

// Abre EXATAMENTE a conversa (thread) no hub de chat — mesmo primitivo do kanban
// nativo (history.pushState + popstate). Com `messageId`, usa o permalink de mensagem
// (?message=<_id>) p/ rolar ATÉ aquele ponto da conversa (estilo busca).
function openConversation(conversationId, messageId = null) {
  if (conversationId == null) return;
  const qs = messageId != null ? `?message=${messageId}` : '';
  history.pushState(null, '', `/conversations/${conversationId}${qs}`);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

// ── Filtro de período (intervalo + presets, estilo da referência) ─────────────
const DATE_PRESETS = [['Hoje', 0], ['7 dias', 7], ['30 dias', 30], ['90 dias', 90], ['1 ano', 365]];

function DateFilter({ from, to, active, onManual, onPreset, onClear }) {
  return html`
    <div class="flex flex-wrap items-center gap-2 mb-3 p-3 rounded-lg bg-wa-panel border border-wa-border">
      <span class="text-[12px] text-wa-secondary font-medium">Período (criação)</span>
      <button onClick=${onClear} class="text-[12px] text-wa-teal hover:underline">limpar</button>
      <div class="flex items-center gap-1.5">
        <input type="date" class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${from}
          onInput=${(e) => onManual(e.target.value, to)} />
        <span class="text-wa-secondary">→</span>
        <input type="date" class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${to}
          onInput=${(e) => onManual(from, e.target.value)} />
      </div>
      <div class="flex flex-wrap gap-1.5">
        ${DATE_PRESETS.map(([lbl, d]) => html`<button key=${lbl} onClick=${() => onPreset(d, lbl)}
          class="px-2.5 py-1 rounded-md text-[12px] border ${active === lbl ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
        <button onClick=${onClear}
          class="px-2.5 py-1 rounded-md text-[12px] border ${active === 'tudo' ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">Tudo</button>
      </div>
    </div>`;
}

export function AtendimentosTab({ api, setTab }) {
  return html`
    <div>
      <div class="flex items-center gap-2 mb-3">
        ${setTab ? html`<button onClick=${() => setTab('contacts')}
          class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover inline-flex items-center gap-1.5 whitespace-nowrap">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>
          Voltar
        </button>` : null}
        <h1 class="text-lg font-semibold text-wa-text">Atendimentos</h1>
      </div>
      <${AtendimentosList} api=${api} />
    </div>`;
}

function AtendimentosList({ api }) {
  const apiBase = api.apiBase;
  const { authHeaders } = api.services;

  const [cols, setCols] = useState([]);          // defs do atendimento (rótulo do plugin — escopo atendimento)
  const [convDefs, setConvDefs] = useState([]);  // defs de "Resolver conversa" (sub-tabela do detalhe)
  const [convResolveDefs, setConvResolveDefs] = useState([]); // conversa: obs+extras editáveis (popup)
  const [convBadgeCols, setConvBadgeCols] = useState([]); // rótulo do plugin — escopo conversa (obs+extras)
  const [coreAttrDefs, setCoreAttrDefs] = useState([]);   // atributo personalizado do core (escopo conversa, não-sistema)
  const [rows, setRows] = useState([]);
  // Quais atributos personalizados aparecem no card do kanban (mapa key→true = oculto).
  const [cardHidden, setCardHidden] = useState(() => lsGetJSON(CARD_FIELDS_KEY) || {});
  const [cardFieldsOpen, setCardFieldsOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  // Padrão ao entrar/F5: intervalo começa HOJE e fica aberto p/ frente (sem teto).
  // Não é persistido — todo (re)carregamento da página volta a "hoje para frente".
  const [dateFrom, setDateFrom] = useState(() => ymd(new Date()));
  const [dateTo, setDateTo] = useState('');
  const [datePreset, setDatePreset] = useState(null);
  const [mode, setMode] = useState(() => lsGet(MODE_KEY, 'lista'));   // 'lista' | 'kanban'
  const [kgroup, setKgroup] = useState(() => lsGet(KGROUP_KEY, 'status')); // 'status' | 'atendente'
  const [sortBy, setSortBy] = useState(null);
  const [sortDir, setSortDir] = useState('asc');
  const [users, setUsers] = useState([]);
  const [actionMsg, setActionMsg] = useState(null);  // {text, error}
  const [detail, setDetail] = useState(null);        // {atendimento, conversas}
  const setM = (m) => { setMode(m); lsSet(MODE_KEY, m); };
  const setKG = (g) => { setKgroup(g); lsSet(KGROUP_KEY, g); };

  const dragRef = useRef(null);     // atendimento sendo arrastado
  const draggedRef = useRef(false); // distingue clique de arrasto
  const [dropCol, setDropCol] = useState(null);

  const getJson = useCallback(async (url, opts) => {
    const r = await fetch(url, { headers: authHeaders(), ...(opts || {}) });
    return r.json();
  }, [authHeaders]);

  const apiPost = useCallback(async (path, body) => {
    const r = await fetch(`${apiBase}${path}`, {
      method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    return r.json();
  }, [apiBase, authHeaders]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      if (q.trim()) params.set('q', q.trim());
      const of = dayStartEpoch(dateFrom);
      const ot = dayEndEpoch(dateTo);
      if (of != null) params.set('opened_from', String(of));
      if (ot != null) params.set('opened_to', String(ot));
      const [dd, cd, ca, ll] = await Promise.all([
        getJson(`${apiBase}/field-defs?scope=atendimento`),
        getJson(`${apiBase}/field-defs?scope=conversa`),
        getJson('/api/custom-attributes?applies_to=conversation'),  // atributos do CORE
        getJson(`${apiBase}/atendimentos?${params.toString()}`),
      ]);
      setCols(((dd && dd.ok && dd.data && dd.data.defs) || []).filter((d) => !d.fixed));
      const convAll = (cd && cd.ok && cd.data && cd.data.defs) || [];
      setConvDefs(convAll.filter((d) => !d.fixed));
      setConvResolveDefs(convAll.filter((d) => !d.readonly)); // p/ o popup "Resolver conversa"
      setConvBadgeCols(convAll.filter((d) => !d.readonly));   // rótulos de conversa (obs+extras) p/ badges/colunas
      // Atributo personalizado = def do core (escopo conversa) que NÃO é espelho do plugin (is_system=0).
      setCoreAttrDefs(((ca && ca.ok && ca.data) || [])
        .filter((d) => !d.is_system)
        .map((d) => ({ key: d.attribute_key, label: d.display_name || d.attribute_key, type: d.type })));
      setRows((ll && ll.ok && ll.data) || []);
    } finally { setLoading(false); }
  }, [apiBase, status, q, dateFrom, dateTo, getJson]);

  useEffect(() => { load(); }, [load]);

  // Atendentes (p/ as colunas do kanban "por atendente"). Reusa o serviço do core.
  useEffect(() => {
    let alive = true;
    const p = (api.services.getAssignableAgents
      ? api.services.getAssignableAgents() : Promise.resolve(null));
    p.then((r) => { if (alive && r && r.ok && r.data) setUsers(r.data.users || []); }).catch(() => {});
    return () => { alive = false; };
  }, [api]);

  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    try {
      ws = new WebSocket(`${proto}//${location.host}/ws`);
      ws.onmessage = (m) => { try { if (JSON.parse(m.data).event === 'plugin_atendimentos_changed') load(); } catch (_) { /* ignore */ } };
    } catch (_) { /* ignore */ }
    return () => { try { ws && ws.close(); } catch (_) { /* ignore */ } };
  }, [load]);

  // ── Filtro de período ───────────────────────────────────────────────────────
  const onManualDate = (f, t) => { setDateFrom(f); setDateTo(t); setDatePreset(null); };
  const onPresetDate = (days, label) => {
    const t = new Date(); const f = new Date(); f.setDate(f.getDate() - days);
    setDateFrom(ymd(f)); setDateTo(ymd(t)); setDatePreset(label);
  };
  const onClearDate = () => { setDateFrom(''); setDateTo(''); setDatePreset('tudo'); };

  async function openDetail(atid) {
    const r = await getJson(`${apiBase}/atendimentos/${atid}`);
    if (r && r.ok) setDetail(r.data);
  }

  // ── Kanban: agrupamento + drag-to-set-state ──────────────────────────────────
  const grouping = useMemo(() => {
    if (kgroup === 'atendente') {
      return {
        columns: [{ id: '__none__', label: 'Não atribuído' },
                  ...users.map((u) => ({ id: `u:${u.id}`, label: u.name || `Usuário #${u.id}` }))],
        columnIdOf: (r) => (r.assignee_user_id != null ? `u:${r.assignee_user_id}` : '__none__'),
        onDrop: (r, col) => apiPost(`/atendimentos/${r.id}/assign`, {
          assignee_user_id: col === '__none__' ? null : +col.slice(2),
          assignee_name: col === '__none__' ? '' : ((users.find((u) => `u:${u.id}` === col) || {}).name || ''),
        }),
      };
    }
    return {
      columns: [{ id: 'aberto', label: 'Aberto' }, { id: 'fechado', label: 'Fechado' }],
      columnIdOf: (r) => (r.status === 'fechado' ? 'fechado' : 'aberto'),
      onDrop: (r, col) => (col === 'fechado'
        ? apiPost(`/atendimentos/${r.id}/close`) : apiPost(`/atendimentos/${r.id}/reopen`)),
    };
  }, [kgroup, users, apiPost]);

  // Resolve a conversa do ciclo aberto do atendimento (popup) e finaliza. Usado quando
  // o backend recusa o fechamento por haver conversa aberta. Retorna true se finalizou.
  async function forceResolveAndClose(atid) {
    const r = await getJson(`${apiBase}/atendimentos/${atid}`);
    const conversas = (r && r.ok && r.data && r.data.conversas) || [];
    const openCycle = conversas.find((c) => !c.ended_at && c.conversation_id);
    if (!openCycle) return false; // nada aberto → mantém o erro original
    let values = {};
    if (convResolveDefs.length) {
      const picked = await api.ui.openModal((close) => html`
        <${ResolveForm} defs=${convResolveDefs} conv=${{ id: openCycle.conversation_id }}
          onOk=${(v) => close(v)} onCancel=${() => close(null)} />`);
      if (!picked) return false; // cancelou → não finaliza
      values = picked;
    }
    const res = await apiPost(`/conversas/${openCycle.conversation_id}/resolve`, { fields: values });
    if (!res || res.ok === false) {
      setActionMsg({ text: (res && res.error) || 'Falha ao resolver a conversa.', error: true });
      return false;
    }
    const closed = await apiPost(`/atendimentos/${atid}/close`);
    if (closed && closed.ok === false) {
      setActionMsg({ text: closed.error || 'Falha ao finalizar.', error: true });
      return false;
    }
    return true;
  }

  async function applyDrop(row, colId) {
    if (!row) return;
    if (grouping.columnIdOf(row) === colId) return; // já está na coluna
    setActionMsg(null);
    try {
      const res = await grouping.onDrop(row, colId);
      if (res && res.ok === false) {
        // Fechar barrado por conversa aberta → força o popup "Resolver conversa".
        if (colId === 'fechado' && /conversa aberta/i.test(res.error || '')) {
          await forceResolveAndClose(row.id);
        } else {
          setActionMsg({ text: res.error || 'Falha ao mover.', error: true });
        }
      }
    } catch (_) { setActionMsg({ text: 'Falha ao mover o atendimento.', error: true }); }
    await load();
  }

  // ── Lista: colunas data-driven (ordenáveis) ──────────────────────────────────
  const allCols = useMemo(() => {
    const base = [
      { key: 'cliente', label: 'Cliente', nowrap: true,
        get: (r) => (r.contact_name || r.contact_phone || '').toLowerCase(),
        render: (r) => r.contact_name || r.contact_phone || '—' },
      { key: 'opened_at', label: 'Data criação', nowrap: true,
        get: (r) => r.opened_at || 0, render: (r) => fmtTs(r.opened_at) },
      { key: 'closed_at', label: 'Data fechamento', nowrap: true,
        get: (r) => r.closed_at || 0, render: (r) => fmtTs(r.closed_at) },
      { key: 'atendente', label: 'Atendente', nowrap: true,
        get: (r) => (r.assignee_name || '').toLowerCase(), render: (r) => r.assignee_name || '—' },
      { key: 'status', label: 'Status', nowrap: true,
        get: (r) => r.status || '', render: (r) => (r.status === 'aberto' ? 'ABERTO' : 'FECHADO') },
      { key: 'obs', label: 'Observações',
        get: (r) => (r.obs || '').toLowerCase(), render: (r) => r.obs || '—' },
    ];
    // Grupos com origem clara no cabeçalho: "Atend." (rótulo do plugin · atendimento),
    // "Conversa" (rótulo do plugin · Resolver conversa), "Atributo" (atributo do core).
    const grp = (prefix, tag, defs, read) => (defs || []).map((d) => ({
      key: `${prefix}:${d.key}`, label: `${tag} · ${d.label}`,
      get: (r) => {
        const v = (read(r) || {})[d.key];
        if (d.type === 'checkbox') return v ? 1 : 0;
        return (v == null ? '' : String(v)).toLowerCase();
      },
      render: (r) => fmtCell((read(r) || {})[d.key], d),
    }));
    return [
      ...base,
      ...grp('f', 'Atend.', cols, (r) => r.fields),
      ...grp('cv', 'Conversa', convBadgeCols, (r) => r.conversa_fields),
      ...grp('ca', 'Atributo', coreAttrDefs, (r) => r.conversa_attrs),
    ];
  }, [cols, convBadgeCols, coreAttrDefs]);

  const sorted = useMemo(() => {
    if (!sortBy) return rows;
    const col = allCols.find((c) => c.key === sortBy);
    if (!col) return rows;
    const dir = sortDir === 'desc' ? -1 : 1;
    return [...rows].sort((a, b) => {
      const va = col.get(a); const vb = col.get(b);
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
      return String(va).localeCompare(String(vb), 'pt-BR') * dir;
    });
  }, [rows, sortBy, sortDir, allCols]);

  function toggleSort(key) {
    if (sortBy === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortBy(key); setSortDir('asc'); }
  }

  // Grupos de campos do card/lista, com ORIGEM clara (cor + etiqueta):
  //  - Atend.   = rótulo do plugin (escopo atendimento)        → row.fields
  //  - Conversa = rótulo do plugin (escopo "Resolver conversa") → row.conversa_fields (última conversa)
  //  - Atributo = atributo personalizado do CORE (escopo conversa, não-sistema) → row.conversa_attrs
  const BADGE_GROUPS = useMemo(() => ([
    { id: 'f', tag: 'Atend.', cls: 'bg-wa-teal/15 text-wa-teal', defs: cols, read: (r) => r.fields || {} },
    { id: 'cv', tag: 'Conversa', cls: 'bg-blue-100 text-blue-700', defs: convBadgeCols, read: (r) => r.conversa_fields || {} },
    { id: 'ca', tag: 'Atributo', cls: 'bg-amber-100 text-amber-700', defs: coreAttrDefs, read: (r) => r.conversa_attrs || {} },
  ]), [cols, convBadgeCols, coreAttrDefs]);

  // Visibilidade por-usuário (localStorage), com chave por grupo+campo (não colide entre grupos).
  const cardVisible = (gid, key) => !cardHidden[`${gid}:${key}`];
  function toggleCardField(gid, key) {
    const nk = `${gid}:${key}`;
    setCardHidden((prev) => {
      const n = { ...prev };
      if (n[nk]) delete n[nk]; else n[nk] = true;
      lsSetJSON(CARD_FIELDS_KEY, n);
      return n;
    });
  }

  const listaView = html`
    <div class="overflow-x-auto rounded-lg border border-wa-border">
      <table class="w-full text-[13px]">
        <thead><tr class="bg-wa-panel text-wa-text text-left">
          ${allCols.map((c) => html`<th key=${c.key} onClick=${() => toggleSort(c.key)}
            class="px-3 py-2 font-semibold whitespace-nowrap cursor-pointer select-none hover:bg-wa-hover">
            ${c.label}${sortBy === c.key ? html`<span class="ml-1 text-wa-teal">${sortDir === 'asc' ? '▲' : '▼'}</span>` : null}
          </th>`)}
        </tr></thead>
        <tbody>
          ${sorted.map((r) => html`<tr key=${r.id} onClick=${() => openDetail(r.id)}
            class="border-t border-wa-border text-wa-text hover:bg-wa-hover cursor-pointer">
            ${allCols.map((c) => html`<td key=${c.key} class="px-3 py-2 ${c.nowrap ? 'whitespace-nowrap' : ''}">${c.render(r)}</td>`)}
          </tr>`)}
        </tbody>
      </table>
    </div>`;

  const kanbanView = html`
    <div>
      <div class="flex items-center gap-2 mb-2">
        <span class="text-[12px] text-wa-secondary">Agrupar por</span>
        <div class="inline-flex rounded-lg border border-wa-border overflow-hidden">
          ${[['status', 'Status'], ['atendente', 'Atendente']].map(([k, lbl]) => html`
            <button key=${k} onClick=${() => setKG(k)}
              class="px-3 py-1 text-[12px] ${kgroup === k ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
        </div>
        ${BADGE_GROUPS.some((g) => g.defs.length) ? html`
          <div class="relative">
            <button onClick=${() => setCardFieldsOpen((o) => !o)}
              class="text-[12px] text-wa-secondary hover:text-wa-text px-2 py-1 rounded border border-wa-border hover:bg-wa-hover">
              Campos do card ▾
            </button>
            ${cardFieldsOpen ? html`
              <div class="absolute left-0 top-full z-20 mt-1 w-60 max-h-72 overflow-auto bg-wa-bg border border-wa-border rounded-lg shadow-xl p-2"
                onMouseLeave=${() => setCardFieldsOpen(false)}>
                ${BADGE_GROUPS.filter((g) => g.defs.length).map((g) => html`
                  <div key=${g.id}>
                    <div class="text-[10px] uppercase tracking-wide px-1 pt-1.5 pb-0.5 font-semibold ${g.cls} rounded">${g.tag}</div>
                    ${g.defs.map((d) => html`
                      <label key=${d.key}
                        class="flex items-center gap-2 px-1 py-1 text-[13px] text-wa-text cursor-pointer hover:bg-wa-hover rounded">
                        <input type="checkbox" checked=${cardVisible(g.id, d.key)} onChange=${() => toggleCardField(g.id, d.key)} />
                        <span class="truncate">${d.label}</span>
                      </label>`)}
                  </div>`)}
              </div>` : null}
          </div>` : null}
        <span class="text-[11px] text-wa-secondary">Arraste um card para outra coluna para alterar o ${kgroup === 'atendente' ? 'atendente' : 'status'}.</span>
      </div>
      ${actionMsg ? html`<div class="mb-2 px-3 py-2 rounded-md text-[13px] ${actionMsg.error ? 'bg-red-500/15 border border-red-500 text-red-500 font-semibold' : 'text-wa-secondary'}">${actionMsg.text}</div>` : null}
      <div class="flex gap-3 overflow-x-auto pb-2">
        ${grouping.columns.map((col) => {
          const cards = rows.filter((r) => grouping.columnIdOf(r) === col.id);
          const isTarget = dropCol === col.id;
          return html`
            <div key=${col.id} class="flex flex-col w-72 shrink-0">
              <div class="flex items-center justify-between mb-2 px-1">
                <span class="text-[13px] font-semibold text-wa-text truncate">${col.label}</span>
                <span class="text-[12px] text-wa-secondary">${cards.length}</span>
              </div>
              <div
                onDragOver=${(e) => { e.preventDefault(); if (dropCol !== col.id) setDropCol(col.id); }}
                onDragLeave=${() => setDropCol((d) => (d === col.id ? null : d))}
                onDrop=${(e) => { e.preventDefault(); const row = dragRef.current; setDropCol(null); applyDrop(row, col.id); }}
                class="flex-1 min-h-[120px] rounded-lg p-2 flex flex-col gap-2 border border-dashed transition-colors ${isTarget ? 'ring-2 ring-wa-teal bg-wa-hover border-wa-teal' : 'bg-wa-bg border-wa-border'}">
                ${cards.length === 0 ? html`<div class="text-[12px] text-wa-secondary text-center py-4">Vazio</div>`
                  : cards.map((r) => html`<${KanbanCard} key=${r.id} row=${r} groups=${BADGE_GROUPS} cardVisible=${cardVisible}
                      draggedRef=${draggedRef} dragRef=${dragRef}
                      onClearDrop=${() => setDropCol(null)} onOpen=${() => openDetail(r.id)} />`)}
              </div>
            </div>`;
        })}
      </div>
    </div>`;

  return html`
    <div>
      <!-- Filtros principais + alternância Kanban/Lista -->
      <div class="flex flex-wrap items-end gap-3 mb-3 p-3 rounded-lg bg-wa-panel border border-wa-border">
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Status</label>
          <select class="wa-field px-3 py-2 rounded-md text-[13px]" value=${status} onChange=${(e) => setStatus(e.target.value)}>
            <option value="">Todos</option>
            <option value="aberto">Aberto</option>
            <option value="fechado">Fechado</option>
          </select>
        </div>
        <div class="flex-1 min-w-[180px]">
          <label class="block text-[12px] text-wa-secondary mb-1">Buscar cliente</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[13px]" type="text" value=${q}
            placeholder="nome ou telefone" onInput=${(e) => setQ(e.target.value)} />
        </div>
        <button onClick=${load} class="px-3 py-2 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover">Atualizar</button>
        <div class="inline-flex rounded-lg border border-wa-border overflow-hidden">
          ${[['kanban', 'Kanban'], ['lista', 'Lista']].map(([k, lbl]) => html`
            <button key=${k} onClick=${() => setM(k)}
              class="px-3 py-2 text-[13px] ${mode === k ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
        </div>
      </div>

      <!-- Filtro de período (intervalo + presets) -->
      <${DateFilter} from=${dateFrom} to=${dateTo} active=${datePreset}
        onManual=${onManualDate} onPreset=${onPresetDate} onClear=${onClearDate} />

      ${loading ? html`<div class="text-[13px] text-wa-secondary p-4">Carregando…</div>`
        : rows.length === 0 ? html`<div class="text-[13px] text-wa-secondary p-4">Nenhum atendimento.</div>`
        : mode === 'kanban' ? kanbanView : listaView}

      ${detail ? html`<${DetailModal} data=${detail} fieldDefs=${convDefs} api=${api} onClose=${() => setDetail(null)} />` : null}
    </div>`;
}

// Card do kanban: draggable (altera estado ao soltar) + clique abre o detalhe. Os campos
// viram badges com COR + ETIQUETA por origem: "Atend." e "Conversa" = rótulos do plugin;
// "Atributo" = atributo personalizado do core. Só os preenchidos (e visíveis) aparecem.
function KanbanCard({ row, groups = [], cardVisible, draggedRef, dragRef, onClearDrop, onOpen }) {
  const badges = [];
  for (const g of groups) {
    const vals = g.read(row) || {};
    for (const d of g.defs) {
      if (cardVisible && !cardVisible(g.id, d.key)) continue;
      const v = vals[d.key];
      if (v === null || v === undefined || v === '') continue;
      badges.push({ k: `${g.id}:${d.key}`, tag: g.tag, cls: g.cls, label: d.label, val: fmtCell(v, d) });
    }
  }
  return html`
    <div draggable=${true}
      onDragStart=${(e) => {
        draggedRef.current = true; dragRef.current = row;
        try { e.dataTransfer.setData('text/plain', String(row.id)); e.dataTransfer.effectAllowed = 'move'; } catch (_) { /* ignore */ }
      }}
      onDragEnd=${() => { dragRef.current = null; if (onClearDrop) onClearDrop(); setTimeout(() => { draggedRef.current = false; }, 0); }}
      onClick=${() => { if (draggedRef.current) { draggedRef.current = false; return; } onOpen(); }}
      class="p-3 rounded-lg border border-wa-border bg-wa-panel hover:border-wa-teal/50 cursor-grab active:cursor-grabbing transition-colors"
      title="Arraste para mover · clique para abrir">
      <div class="font-medium text-wa-text text-[13px] truncate">${row.contact_name || row.contact_phone || '—'}</div>
      ${row.contact_phone ? html`<div class="text-[12px] text-wa-secondary truncate">${row.contact_phone}</div>` : null}
      <div class="text-[12px] text-wa-secondary mt-1">Início: ${fmtTs(row.opened_at)}</div>
      ${row.closed_at ? html`<div class="text-[12px] text-wa-secondary">Fim: ${fmtTs(row.closed_at)}</div>` : null}
      <div class="flex items-center justify-between gap-2 mt-1">
        <span class="text-[12px] ${row.assignee_name ? 'text-wa-text' : 'text-wa-secondary'} truncate">${row.assignee_name || 'Não atribuído'}</span>
        <span class="px-1.5 py-0.5 rounded-full text-[10px] ${row.status === 'aberto' ? 'bg-wa-teal/15 text-wa-teal' : 'bg-wa-hover text-wa-secondary'}">${row.status === 'aberto' ? 'Aberto' : 'Fechado'}</span>
      </div>
      ${badges.length ? html`
        <div class="flex flex-wrap gap-1 mt-2">
          ${badges.map((b) => html`
            <span key=${b.k} title=${`${b.tag} · ${b.label}: ${b.val}`}
              class="inline-flex items-center gap-1 max-w-full px-1.5 py-0.5 rounded text-[11px] ${b.cls}">
              <span class="font-semibold opacity-80">${b.tag}</span>
              <span class="truncate">${b.label}: ${b.val}</span>
            </span>`)}
        </div>` : null}
    </div>`;
}

function DetailModal({ data, fieldDefs = [], api, onClose }) {
  const at = data.atendimento || {};
  const conversas = data.conversas || [];
  // Clicar numa conversa (ciclo) → resolve a âncora (1ª mensagem do ciclo) no backend
  // e abre o chat rolando ATÉ aquele ponto exato (permalink ?message=<_id>). Se a
  // âncora falhar, abre a conversa mesmo assim (sem o scroll fino).
  const openConv = async (c) => {
    if (!c) return;
    let convId = c.conversation_id;
    let msgId = null;
    try {
      const r = await fetch(`${api.apiBase}/conversas/${c.id}/anchor`, { headers: api.services.authHeaders() });
      const d = await r.json();
      if (d && d.ok && d.data) {
        if (d.data.conversation_id != null) convId = d.data.conversation_id;
        if (d.data.message_id != null) msgId = d.data.message_id;
      }
    } catch (_) { /* fallback: abre a conversa sem âncora de mensagem */ }
    onClose();
    openConversation(convId, msgId);
  };
  return html`
    <div class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-2xl w-full p-6 max-h-[85vh] overflow-auto">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-base font-semibold text-wa-text">${at.contact_name || at.contact_phone || 'Atendimento'}</h2>
          <button onClick=${onClose} class="text-wa-secondary hover:text-wa-text text-xl leading-none">×</button>
        </div>
        <div class="text-[12px] text-wa-secondary mb-3">
          Início: ${fmtTs(at.opened_at)}${at.closed_at ? ` · Fim: ${fmtTs(at.closed_at)}` : ''} · ${at.status === 'aberto' ? 'Aberto' : 'Fechado'}
        </div>
        <div class="text-wa-iconActive text-[13px] font-semibold mb-2">Conversas</div>
        <div class="text-[12px] text-wa-secondary mb-2">Clique numa conversa para abri-la no chat.</div>
        <${ConversasTable} conversas=${conversas} fieldDefs=${fieldDefs}
          storageKey="whatsbot_atend_conv_cols_modal" onRowClick=${openConv} />
      </div>
    </div>`;
}

export default AtendimentosTab;
