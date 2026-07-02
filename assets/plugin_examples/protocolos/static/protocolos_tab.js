// Override da aba "Protocolos". Aba dedicada à entidade PROTOCOLO:
//  - Lista (colunas ordenáveis por clique) OU Kanban (agrupado por Status ou Atendente,
//    com drag-and-drop que ALTERA o estado: soltar numa coluna fecha/reabre ou atribui).
//  - Filtros: status, busca por cliente e PERÍODO de criação (intervalo + presets).
//  - Abrir um protocolo → detalhe; clicar numa de suas atendimentos → vai EXATAMENTE
//    àquele ponto da atendimento no chat (?message=<_id>).
// Desativar o plugin → a aba volta 100% ao Protocolos nativo.

import { h } from 'preact';
import { useState, useEffect, useCallback, useMemo, useRef } from 'preact/hooks';
import htm from 'htm';
import { AtendimentosTable } from '/plugins/protocolos/static/atendimentos_table.js';
import { ResolveForm, LabeledField } from '/plugins/protocolos/static/resolve_form.js';

const html = htm.bind(h);

const MODE_KEY = 'whatsbot_protocolos_mode';    // 'lista' | 'kanban'
const KGROUP_KEY = 'whatsbot_protocolos_kgroup'; // legado: 'status' | 'atendente' (migrado p/ VIEW_KEY)
const VIEW_KEY = 'whatsbot_protocolos_view';     // id (numérico) da aba ativa; sentinelas legadas '__status'/'__atendente' caem na 1ª aba
function lsGet(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }

// Deep-link da aba ativa do Kanban (Plano 24) — ?view=<id>. Padrão MANUAL do plugin
// (URLSearchParams + replaceState), como o ?detail= já faz; NÃO usa os hooks do core
// (evita divergência pelo boundary de plugin). Convivem: escrever ?view= preserva o
// ?detail= corrente e vice-versa.
// Copia texto p/ o clipboard funcionando também em HTTP/contexto não-seguro (o painel
// costuma rodar num IP de LAN sem TLS, onde navigator.clipboard não existe) — fallback
// via execCommand num textarea temporário. Chama onOk() no sucesso.
function copyText(text, onOk) {
  const fallback = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok && onOk) onOk();
    } catch (e) { /* clipboard indisponível */ }
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => onOk && onOk()).catch(fallback);
  } else { fallback(); }
}

function readUrlParam(key) { try { return new URLSearchParams(location.search).get(key); } catch (e) { return null; } }
function writeUrlParam(key, value) {
  try {
    const p = new URLSearchParams(location.search);
    if (value == null || value === '') p.delete(key); else p.set(key, String(value));
    const qs = p.toString();
    history.replaceState(null, '', `${location.pathname}${qs ? `?${qs}` : ''}`);
  } catch (e) { /* ignore */ }
}

// Fallback SÓ de render: garante que o board nunca fique em branco quando não há nenhuma
// visualização (ex.: todas excluídas). NÃO é uma aba da barra — as abas Status/Atendente
// agora são views REAIS (seed 010), editáveis/excluíveis como qualquer visualização criada
// pela interface.
const FALLBACK_VIEW = { id: '__fallback', group_by: 'status', filters: {} };

function fmtTs(ts) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }); }
  catch (e) { return '—'; }
}

function fmtCell(v, def) {
  if (def.type === 'checkbox') return v ? 'Sim' : 'Não';
  if (def.type === 'checkboxes') return (Array.isArray(v) && v.length) ? v.join(', ') : '—';
  return (v == null || v === '') ? '—' : String(v);
}

// Campo do protocolo "preenchido"? checkbox sempre conta como preenchido (consistente
// com o backend _missing_required e com o isFilled do popup de resolver). Usado pelo gate
// de obrigatórios do detalhe e pelo guard do drag para "Fechado".
function isFilledProto(def, v) {
  if (def.type === 'checkbox') return true;
  if (def.type === 'checkboxes') return Array.isArray(v) ? v.length > 0 : !!v;
  return String(v == null ? '' : v).trim() !== '';
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

// Abre EXATAMENTE a atendimento (thread) no hub de chat — mesmo primitivo do kanban
// nativo (history.pushState + popstate). Com `messageId`, usa o permalink de mensagem
// (?message=<_id>) p/ rolar ATÉ aquele ponto da atendimento (estilo busca).
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

// ── Agrupamento do Kanban (uniforme: columns / columnIdOf / onDrop / confirmText) ──
// Espelha o padrão de web/static/js/components/attendances/grouping.js. `onDrop` ausente
// = modo SÓ-LEITURA (data): cards não arrastáveis. `confirmText` descreve EXATAMENTE a
// ação para o popup de confirmação (ver applyDrop).
function _startOfDayEpoch(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return Math.floor(x.getTime() / 1000); }
function _ymdLabel(s) { try { return new Date(`${s}T00:00:00`).toLocaleDateString('pt-BR'); } catch (e) { return s; } }
function _monthLabel(ym) {
  try { const [y, m] = ym.split('-'); return new Date(+y, +m - 1, 1).toLocaleDateString('pt-BR', { month: 'short', year: 'numeric' }); }
  catch (e) { return ym; }
}

function buildGrouping(view, { users, coreAttrDefs, rows, apiPost }) {
  const gb = (view && view.group_by) || 'status';
  const cliente = (r) => r.contact_name || r.contact_phone || 'cliente';

  if (gb === 'atendente') {
    const nameOf = (col) => ((users.find((u) => `u:${u.id}` === col) || {}).name) || 'este atendente';
    return {
      columns: [{ id: '__none__', label: 'Não atribuído' },
                ...users.map((u) => ({ id: `u:${u.id}`, label: u.name || `Usuário #${u.id}` }))],
      columnIdOf: (r) => (r.assignee_user_id != null ? `u:${r.assignee_user_id}` : '__none__'),
      confirmText: (r, col) => (col === '__none__'
        ? `Remover a atribuição do protocolo de ${cliente(r)}?`
        : `Atribuir o protocolo de ${cliente(r)} a ${nameOf(col)}?`),
      onDrop: (r, col) => apiPost(`/protocolos/${r.id}/assign`, {
        assignee_user_id: col === '__none__' ? null : +col.slice(2),
        assignee_name: col === '__none__' ? '' : nameOf(col),
      }),
    };
  }

  if (gb === 'data') {
    const mode = (view && view.group_date_mode) || 'faixas';
    if (mode === 'faixas') {
      const now = new Date();
      const sToday = _startOfDayEpoch(now);
      const sYest = sToday - 86400;
      const sWeek = sToday - 6 * 86400;
      const sMonth = _startOfDayEpoch(new Date(now.getFullYear(), now.getMonth(), 1));
      return {
        readOnly: true,
        columns: [{ id: 'today', label: 'Hoje' }, { id: 'yesterday', label: 'Ontem' },
                  { id: 'week', label: 'Últimos 7 dias' }, { id: 'month', label: 'Este mês' },
                  { id: 'older', label: 'Mais antigos' }],
        columnIdOf: (r) => {
          const t = r.opened_at || 0;
          if (t >= sToday) return 'today';
          if (t >= sYest) return 'yesterday';
          if (t >= sWeek) return 'week';
          if (t >= sMonth) return 'month';
          return 'older';
        },
      };
    }
    const dayOf = (r) => (r.opened_at ? ymd(new Date(r.opened_at * 1000)) : null);
    const monOf = (r) => { if (!r.opened_at) return null; const d = new Date(r.opened_at * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };
    const keyOf = mode === 'mes' ? monOf : dayOf;
    const labelOf = mode === 'mes' ? _monthLabel : _ymdLabel;
    const prefix = mode === 'mes' ? 'm:' : 'd:';
    const distinct = Array.from(new Set((rows || []).map(keyOf).filter(Boolean))).sort().reverse();
    const cols = distinct.map((k) => ({ id: prefix + k, label: labelOf(k) }));
    if ((rows || []).some((r) => !r.opened_at)) cols.push({ id: '__nodate__', label: 'Sem data' });
    return {
      readOnly: true,
      columns: cols.length ? cols : [{ id: '__nodate__', label: 'Sem data' }],
      columnIdOf: (r) => { const k = keyOf(r); return k ? prefix + k : '__nodate__'; },
    };
  }

  if (gb === 'attr') {
    const key = view && view.group_attr_key;
    const def = (coreAttrDefs || []).find((d) => d.key === key);
    if (!def) {
      return {
        readOnly: true, unavailable: true,
        columns: [{ id: '__none__', label: 'Atributo indisponível' }],
        columnIdOf: () => '__none__',
      };
    }
    const opts = Array.isArray(def.options) ? def.options : [];
    return {
      columns: [{ id: '__none__', label: 'Sem valor' },
                ...opts.map((o) => ({ id: `o:${o}`, label: o }))],
      columnIdOf: (r) => { const v = (r.atendimento_attrs || {})[key]; return (v != null && v !== '') ? `o:${v}` : '__none__'; },
      confirmText: (r, col) => (col === '__none__'
        ? `Limpar "${def.label}" da última atendimento de ${cliente(r)}?`
        : `Definir "${def.label}" = "${col.slice(2)}" na última atendimento de ${cliente(r)}?`),
      onDrop: (r, col) => apiPost(`/protocolos/${r.id}/set-attr`, {
        key, value: col === '__none__' ? null : col.slice(2),
      }),
    };
  }

  // status (built-in, default)
  return {
    columns: [{ id: 'aberto', label: 'Aberto' }, { id: 'fechado', label: 'Fechado' }],
    columnIdOf: (r) => (r.status === 'fechado' ? 'fechado' : 'aberto'),
    confirmText: (r, col) => (col === 'fechado'
      ? `Finalizar o protocolo de ${cliente(r)}?`
      : `Reabrir o protocolo de ${cliente(r)}?`),
    onDrop: (r, col) => (col === 'fechado'
      ? apiPost(`/protocolos/${r.id}/close`) : apiPost(`/protocolos/${r.id}/reopen`)),
  };
}

export function ProtocolosTab({ api, setTab }) {
  return html`
    <div>
      <div class="flex items-center gap-2 mb-3">
        ${setTab ? html`<button onClick=${() => setTab('contacts')}
          class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover inline-flex items-center gap-1.5 whitespace-nowrap">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>
          Voltar
        </button>` : null}
        <h1 class="text-lg font-semibold text-wa-text">Protocolos</h1>
      </div>
      <${ProtocolosList} api=${api} />
    </div>`;
}

function ProtocolosList({ api }) {
  const apiBase = api.apiBase;
  const { authHeaders } = api.services;

  const [cols, setCols] = useState([]);          // TODAS as defs do protocolo (OBS fixo + extras, com `required`)
  const [atendDefs, setAtendDefs] = useState([]);  // defs de "Resolver atendimento" (sub-tabela do detalhe)
  const [atendResolveDefs, setAtendResolveDefs] = useState([]); // atendimento: obs+extras editáveis (popup)
  const [coreAttrDefs, setCoreAttrDefs] = useState([]);   // atributo personalizado do core (escopo atendimento, não-sistema)
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  // Padrão ao entrar/F5: intervalo começa HOJE e fica aberto p/ frente (sem teto).
  // Não é persistido — todo (re)carregamento da página volta a "hoje para frente".
  const [dateFrom, setDateFrom] = useState(() => ymd(new Date()));
  const [dateTo, setDateTo] = useState('');
  const [datePreset, setDatePreset] = useState(null);
  const [mode, setMode] = useState(() => lsGet(MODE_KEY, 'lista'));   // 'lista' | 'kanban'
  // Visualizações (abas de "Agrupar por"): built-ins + carregadas do backend.
  const [views, setViews] = useState([]);
  const [activeViewId, setActiveViewId] = useState(() => {
    const u = readUrlParam('view');                   // precedência URL > localStorage
    if (u) return u;
    const v = lsGet(VIEW_KEY, '');
    if (v) return v;
    const kg = lsGet(KGROUP_KEY, '');                 // migração suave da chave antiga
    return kg === 'atendente' ? '__atendente' : '__status';
  });
  const [currentUser, setCurrentUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem('whatsbot_user') || 'null'); } catch (e) { return null; }
  });
  const [assigneeFilter, setAssigneeFilter] = useState(null);  // filtro por atendente (da aba)
  const [attrFilters, setAttrFilters] = useState({});          // filtros por atributo de atendimento (da aba)
  const [sortBy, setSortBy] = useState(null);
  const [sortDir, setSortDir] = useState('asc');
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);            // grupos de permissão (p/ "Quem pode ver")
  const [actionMsg, setActionMsg] = useState(null);  // {text, error}
  const [detail, setDetail] = useState(null);        // {protocolo, atendimentos}
  const [detailWarning, setDetailWarning] = useState(''); // aviso no detalhe (vindo do drag p/ "Fechado")
  const appliedViewRef = useRef(null);               // última aba cujos filtros já foram aplicados
  const setM = (m) => { setMode(m); lsSet(MODE_KEY, m); };
  // Troca a aba ativa: estado + localStorage (por-device) + reflete ?view=<id> na URL
  // (replaceState, preservando ?detail=). id vazio limpa o param (ex.: view excluída).
  const setActiveView = (id) => {
    setActiveViewId(String(id)); lsSet(VIEW_KEY, String(id)); writeUrlParam('view', id ? String(id) : '');
  };

  const tabs = useMemo(() => views.map((v) => ({ ...v, label: v.name })), [views]);
  const activeView = useMemo(
    () => tabs.find((t) => String(t.id) === String(activeViewId)) || tabs[0] || FALLBACK_VIEW,
    [tabs, activeViewId]);
  const canTeam = api.services.hasPermission
    ? api.services.hasPermission(currentUser, 'plugin.protocolos.manage_team_views') : true;
  const canEditView = (v) => canTeam
    || (v.scope === 'personal' && (!currentUser || v.owner_user_id === currentUser.id));
  // Filtros disponíveis na aba ativa (available_filters da view; null/undefined = todos).
  // Gate da barra de filtros ao vivo. Chaves: status|atendente|q|periodo|attr:<key>.
  const availArr = (activeView && Array.isArray(activeView.available_filters)) ? activeView.available_filters : null;
  const availFilter = (key) => !availArr || availArr.includes(key);

  const dragRef = useRef(null);     // protocolo sendo arrastado
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
      if (assigneeFilter != null) params.set('assignee_user_id', String(assigneeFilter));
      if (attrFilters && Object.keys(attrFilters).length) params.set('attr_filters', JSON.stringify(attrFilters));
      const [dd, cd, ca, ll] = await Promise.all([
        getJson(`${apiBase}/field-defs?scope=protocolo`),
        getJson(`${apiBase}/field-defs?scope=atendimento`),
        getJson('/api/custom-attributes?applies_to=conversation'),  // atributos do CORE
        getJson(`${apiBase}/protocolos?${params.toString()}`),
      ]);
      // TODAS as defs do protocolo (OBS fixo + extras, com `required`) — alimentam o
      // form editável do detalhe e o gate de obrigatórios (drag p/ "Fechado"). Sem filtrar.
      setCols((dd && dd.ok && dd.data && dd.data.defs) || []);
      const atendAll = (cd && cd.ok && cd.data && cd.data.defs) || [];
      setAtendDefs(atendAll.filter((d) => !d.fixed));
      setAtendResolveDefs(atendAll.filter((d) => !d.readonly)); // p/ o popup "Resolver atendimento"
      // Atributo personalizado = def do core (escopo atendimento) que NÃO é espelho do plugin (is_system=0).
      setCoreAttrDefs(((ca && ca.ok && ca.data) || [])
        .filter((d) => !d.is_system)
        .map((d) => ({ key: d.attribute_key, label: d.display_name || d.attribute_key, type: d.type,
                       options: Array.isArray(d.options) ? d.options : [] })));
      setRows((ll && ll.ok && ll.data) || []);
    } finally { setLoading(false); }
  }, [apiBase, status, q, dateFrom, dateTo, assigneeFilter, attrFilters, getJson]);

  useEffect(() => { load(); }, [load]);

  // Visualizações (abas) + usuário atual (p/ permissão de equipe).
  const loadViews = useCallback(async () => {
    try { const r = await getJson(`${apiBase}/kanban-views`); if (r && r.ok) setViews(r.data || []); }
    catch (e) { /* ignore */ }
  }, [apiBase, getJson]);
  useEffect(() => { loadViews(); }, [loadViews]);
  // Grupos de permissão (roles) p/ o seletor "Quem pode ver" do editor.
  useEffect(() => {
    let alive = true;
    getJson(`${apiBase}/roles`).then((r) => { if (alive && r && r.ok && r.data) setRoles(r.data.roles || []); }).catch(() => {});
    return () => { alive = false; };
  }, [apiBase, getJson]);
  useEffect(() => {
    if (!api.services.getMe) return;
    api.services.getMe().then((r) => { if (r && r.ok && r.data && r.data.user) setCurrentUser(r.data.user); }).catch(() => {});
  }, [api]);

  // Aplica os filtros pré-determinados da aba UMA vez por troca de aba (defaults de
  // entrada, não travas). appliedViewRef evita reaplicar em reloads (WS) e só dispara
  // quando a view alvo já está resolvida (built-in sempre; custom após carregar).
  useEffect(() => {
    const v = activeView;
    // Só aplica quando a view ALVO está resolvida — usa v.id === activeViewId p/ NÃO marcar
    // como aplicada enquanto activeView ainda é o fallback (senão a aba custom nunca recebia
    // seus filtros no reload → ficava inconsistente com a troca de aba). Aplica 1× por aba.
    const resolved = v && String(v.id) === String(activeViewId);
    if (!resolved || appliedViewRef.current === String(activeViewId)) return;
    appliedViewRef.current = String(activeViewId);
    // Origem dos filtros: a preferência do usuário (pessoal x equipe) por aba. Default =
    // filtros da EQUIPE (a coluna filters compartilhada). O fallback de render não tem pref/filters → {}.
    const usePersonal = v.pref && v.pref.use_personal;
    const f = (usePersonal ? (v.pref.personal_filters || {}) : (v.filters || {})) || {};
    // Só aplica pré-determinados de filtros DISPONÍVEIS nesta aba (available_filters da view;
    // null/undefined = todos). Um filtro indisponível nunca constrange silenciosamente.
    const availArr = Array.isArray(v.available_filters) ? v.available_filters : null;
    const av = (key) => !availArr || availArr.includes(key);
    setStatus(av('status') && f.status != null ? f.status : '');
    setQ(av('q') && f.q != null ? f.q : '');
    setAssigneeFilter(av('atendente') && f.assignee_user_id != null ? f.assignee_user_id : null);
    setAttrFilters(f.attrs && typeof f.attrs === 'object'
      ? Object.fromEntries(Object.entries(f.attrs).filter(([k]) => av(`attr:${k}`))) : {});
    // Data: preset relativo vira intervalo REAL (from/to); 'tudo' limpa; from/to literais
    // são respeitados; sem date (ou Período indisponível) → default (hoje→frente).
    const df = (av('periodo') && f.date && typeof f.date === 'object') ? f.date : null;
    const presetDays = df && df.preset && df.preset !== 'tudo'
      ? (DATE_PRESETS.find(([lbl]) => lbl === df.preset) || [])[1] : undefined;
    if (df && presetDays != null) {
      const t = new Date(); const fr = new Date(); fr.setDate(fr.getDate() - presetDays);
      setDatePreset(df.preset); setDateFrom(ymd(fr)); setDateTo(ymd(t));
    } else if (df && df.preset === 'tudo') {
      setDatePreset('tudo'); setDateFrom(''); setDateTo('');
    } else if (df && (df.from || df.to)) {
      setDatePreset(null); setDateFrom(df.from || ''); setDateTo(df.to || '');
    } else {
      setDatePreset(null); setDateFrom(ymd(new Date())); setDateTo('');  // default: hoje→frente
    }
  }, [activeViewId, views, activeView]);

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
      ws.onmessage = (m) => { try { if (JSON.parse(m.data).event === 'plugin_protocolos_changed') { load(); loadViews(); } } catch (_) { /* ignore */ } };
    } catch (_) { /* ignore */ }
    return () => { try { ws && ws.close(); } catch (_) { /* ignore */ } };
  }, [load, loadViews]);

  // ── Filtro de período ───────────────────────────────────────────────────────
  const onManualDate = (f, t) => { setDateFrom(f); setDateTo(t); setDatePreset(null); };
  const onPresetDate = (days, label) => {
    const t = new Date(); const f = new Date(); f.setDate(f.getDate() - days);
    setDateFrom(ymd(f)); setDateTo(ymd(t)); setDatePreset(label);
  };
  const onClearDate = () => { setDateFrom(''); setDateTo(''); setDatePreset('tudo'); };

  // Algum filtro (visível ou pré-determinado da aba) ativo? Usado p/ mostrar "Limpar
  // filtros" — assim uma aba com filtro pré-determinado que zera o resultado nunca é um
  // mistério: o usuário vê os controles e limpa para ver todos.
  const hasViewFilters = !!(status || (q && q.trim()) || assigneeFilter != null
    || Object.keys(attrFilters).length || dateTo || (datePreset && datePreset !== null));
  const clearFilters = () => {
    setStatus(''); setQ(''); setAssigneeFilter(null); setAttrFilters({});
    setDatePreset(null); setDateFrom(ymd(new Date())); setDateTo('');
  };

  // Id do protocolo atualmente aberto no detalhe (espelha ?detail= na URL). Ref p/ o
  // sync bidirecional não depender do estado assíncrono e evitar reabrir/refetch em loop.
  const detailIdRef = useRef(null);

  const openDetail = useCallback(async (atid, warn = false) => {
    const r = await getJson(`${apiBase}/protocolos/${atid}`);
    if (r && r.ok) {
      setDetailWarning(warn ? 'Preencha os campos obrigatórios para finalizar este protocolo.' : '');
      setDetail(r.data);
      // Deep-link (Plano 24): o protocolo aberto vira ?detail=<id> na URL (compartilhável).
      detailIdRef.current = String(atid);
      writeUrlParam('detail', String(atid));
    }
  }, [apiBase, getJson]);

  // Fecha o detalhe e limpa o ?detail= (preservando o ?view=).
  const closeDetail = useCallback(() => {
    setDetail(null); setDetailWarning('');
    detailIdRef.current = null;
    writeUrlParam('detail', '');
  }, []);

  // Deep-link do protocolo aberto (Plano 24): /attendances?detail=<id> abre e MANTÉM o
  // param na URL (compartilhável — antes era one-shot). Lido no mount E em popstate (é
  // assim que o "Resolver e ir ao protocolo" chega, via pushState no extends.js). Sync
  // bidirecional: param novo → abre; param ausente com detalhe aberto (voltar) → fecha.
  // No mesmo passo ressincroniza a aba ativa com ?view= (o param vence o localStorage).
  useEffect(() => {
    const readDeep = () => {
      const v = readUrlParam('view');            // ?view= tem precedência (setState idempotente no Preact)
      if (v) { setActiveViewId(String(v)); lsSet(VIEW_KEY, String(v)); }
      const id = readUrlParam('detail');
      if (id) {
        const n = parseInt(id, 10);
        if (detailIdRef.current !== String(id) && !Number.isNaN(n)) openDetail(n);
      } else if (detailIdRef.current != null) {
        // URL sem ?detail= (ex.: voltar do navegador) → fecha o detalhe aberto.
        setDetail(null); setDetailWarning(''); detailIdRef.current = null;
      }
    };
    readDeep();
    window.addEventListener('popstate', readDeep);
    return () => window.removeEventListener('popstate', readDeep);
  }, [openDetail]);

  // ── Kanban: agrupamento (dinâmico pela aba ativa) + drag-to-set-state ─────────
  const grouping = useMemo(
    () => buildGrouping(activeView, { users, coreAttrDefs, rows, apiPost }),
    [activeView, users, coreAttrDefs, rows, apiPost]);

  // Abre o editor de visualização (criar quando view=null). NÃO troca de aba sozinho —
  // o usuário só sai da aba atual pelo "✕" dela ou clicando noutra aba (req. do usuário).
  // Uma aba nova aparece na barra; editar a aba ATIVA re-aplica seus filtros (reset do ref).
  const openViewEditor = useCallback(async (view) => {
    const saved = await api.ui.openModal((close) => html`<${ViewEditorModal}
      view=${view} coreAttrDefs=${coreAttrDefs} users=${users} roles=${roles} canTeam=${canTeam}
      currentUser=${currentUser} api=${api}
      onSaved=${(v) => close(v)} onCancel=${() => close(null)} />`);
    if (saved) {
      if (view && String(view.id) === String(activeViewId)) appliedViewRef.current = null;
      await loadViews();
    }
  }, [api, coreAttrDefs, users, roles, canTeam, currentUser, loadViews, activeViewId]);

  const removeView = useCallback(async (view) => {
    if (!view) return;
    const ok = await api.ui.openModal((close) => html`<${ConfirmDialog} danger=${true} okLabel="Excluir"
      message=${`Excluir a visualização "${view.label || view.name}"?`}
      onOk=${() => close(true)} onCancel=${() => close(false)} />`);
    if (!ok) return;
    try {
      const r = await fetch(`${apiBase}/kanban-views/${view.id}`, { method: 'DELETE', headers: authHeaders() });
      const j = await r.json().catch(() => ({}));
      if (j && j.ok) { if (String(activeViewId) === String(view.id)) setActiveView(''); await loadViews(); }
      else setActionMsg({ text: (j && j.error) || 'Falha ao excluir.', error: true });
    } catch (_) { setActionMsg({ text: 'Falha ao excluir a visualização.', error: true }); }
  }, [api, apiBase, authHeaders, activeViewId, loadViews]);

  // Resolve a atendimento do ciclo aberto do protocolo (popup) e finaliza. Usado quando
  // o backend recusa o fechamento por haver atendimento aberta. Retorna true se finalizou.
  async function forceResolveAndClose(atid) {
    const r = await getJson(`${apiBase}/protocolos/${atid}`);
    const atendimentos = (r && r.ok && r.data && r.data.atendimentos) || [];
    const openCycle = atendimentos.find((c) => !c.ended_at && c.conversation_id);
    if (!openCycle) return false; // nada aberto → mantém o erro original
    let fields = {};
    if (atendResolveDefs.length) {
      const picked = await api.ui.openModal((close) => html`
        <${ResolveForm} defs=${atendResolveDefs} atend=${{ id: openCycle.conversation_id }}
          onOk=${(v) => close(v)} onCancel=${() => close(null)} />`);
      if (!picked) return false; // cancelou → não finaliza
      fields = picked.fields || {}; // onOk devolve { fields, custom_attributes, goTo }
    }
    const res = await apiPost(`/atendimentos/${openCycle.conversation_id}/resolve`, { fields });
    if (!res || res.ok === false) {
      setActionMsg({ text: (res && res.error) || 'Falha ao resolver o atendimento.', error: true });
      return false;
    }
    // Fecha a ATENDIMENTO no core (status=closed). O resolve do plugin só encerra o CICLO
    // (ended_at) — sem este passo a atendimento segue ABERTA na aba de atendimento (o header
    // fica em "Resolver" em vez de "Reabrir"). É o MESMO 2º passo que a aba de atendimento
    // faz após o beforeResolve (resolveConversation → setConversationStatus); aqui o popup
    // do plugin já rodou, então chamamos o status direto. Mantém as duas abas em sincronia.
    const st = await api.services.setConversationStatus(openCycle.conversation_id, 'closed');
    if (st && st.ok === false) {
      setActionMsg({ text: st.error || 'Falha ao fechar o atendimento.', error: true });
      return false;
    }
    const closed = await apiPost(`/protocolos/${atid}/close`);
    if (closed && closed.ok === false) {
      setActionMsg({ text: closed.error || 'Falha ao finalizar.', error: true });
      return false;
    }
    return true;
  }

  // Finaliza o protocolo a partir do detalhe (Req 3/4). Se o backend recusar por haver
  // atendimento aberta, abre o popup "Resolver atendimento" (forceResolveAndClose) e finaliza
  // depois. Sucesso → fecha o modal + recarrega. Outro erro → devolve {ok:false,error}
  // p/ o DetailModal exibir inline.
  async function finalizeProtocolo(atid) {
    const res = await apiPost(`/protocolos/${atid}/close`);
    if (res && res.ok === false) {
      if (/atendimento abert[ao]/i.test(res.error || '')) {
        const done = await forceResolveAndClose(atid);
        if (done) { closeDetail(); await load(); return { ok: true }; }
        return { ok: false, error: 'Resolva o atendimento aberto para finalizar o protocolo.' };
      }
      return { ok: false, error: res.error || 'Falha ao finalizar.' };
    }
    closeDetail(); await load();
    return { ok: true };
  }

  // Obrigatórios do PROTOCOLO faltando (mesma regra do backend _missing_required):
  // OBS (coluna) + extras das defs `required`; checkbox sempre conta como preenchido.
  function missingRequiredProto(row) {
    const eff = { obs: (row && row.obs) || '', ...((row && row.fields) || {}) };
    return cols.some((d) => d.required && !isFilledProto(d, eff[d.key]));
  }

  async function applyDrop(row, colId) {
    if (!row || !grouping.onDrop) return;            // modo só-leitura (data) não muta
    if (grouping.columnIdOf(row) === colId) return;  // já está na coluna
    setActionMsg(null);
    // Confirmação OBRIGATÓRIA: descreve EXATAMENTE o que será feito antes de QUALQUER
    // alteração. Só prossegue ao confirmar.
    const msg = grouping.confirmText ? grouping.confirmText(row, colId) : 'Confirmar esta alteração?';
    const ok = await api.ui.openModal((close) => html`<${ConfirmDialog} message=${msg}
      onOk=${() => close(true)} onCancel=${() => close(false)} />`);
    if (!ok) return;
    // Req 5: soltar em "Fechado" sem os obrigatórios do protocolo → abre o detalhe
    //        daquele protocolo com um aviso e NÃO fecha.
    if (colId === 'fechado' && missingRequiredProto(row)) {
      openDetail(row.id, true);
      return;
    }
    try {
      const res = await grouping.onDrop(row, colId);
      if (res && res.ok === false) {
        // Fechar barrado por atendimento aberta → força o popup "Resolver atendimento".
        if (colId === 'fechado' && /atendimento abert[ao]/i.test(res.error || '')) {
          await forceResolveAndClose(row.id);
        } else {
          setActionMsg({ text: res.error || 'Falha ao mover.', error: true });
        }
      }
    } catch (_) { setActionMsg({ text: 'Falha ao mover o protocolo.', error: true }); }
    await load();
  }

  // ── Lista: colunas data-driven (ordenáveis) ──────────────────────────────────
  // Só os dados PRÓPRIOS do protocolo. Os rótulos do plugin e os atributos
  // personalizados NÃO aparecem aqui — eles vivem no DETALHE de cada protocolo
  // (modal), junto da atendimento a que pertencem.
  const allCols = useMemo(() => [
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
  ], []);

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

  // Barra de abas "Agrupar por" — renderizada SEMPRE (kanban e lista, e mesmo quando vazio),
  // FORA do kanbanView, p/ a aba selecionada nunca sumir. Sai-se de uma aba só pelo "✕" dela.
  const tabBar = html`
    <div class="flex flex-wrap items-center gap-2 mb-3">
      <span class="text-[12px] text-wa-secondary">Agrupar por</span>
      <div class="flex flex-wrap items-center gap-1.5">
        ${tabs.map((t) => html`
          <div key=${t.id} class="inline-flex items-center rounded-lg border overflow-hidden ${String(activeViewId) === String(t.id) ? 'border-wa-teal' : 'border-wa-border'}">
            <button onClick=${() => setActiveView(t.id)}
              class="px-3 py-1 text-[12px] ${String(activeViewId) === String(t.id) ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}"
              title=${t.scope === 'team' ? 'Visualização de equipe' : 'Visualização pessoal'}>
              ${t.label}${t.scope === 'team' ? html`<span class="ml-1 opacity-70">·equipe</span>` : null}
            </button>
            ${canEditView(t) ? html`
            <button title="Editar" onClick=${() => openViewEditor(t)}
              class="px-1.5 py-1 text-[12px] bg-wa-panel text-wa-secondary hover:bg-wa-hover hover:text-wa-text border-l border-wa-border">✎</button>` : null}
            ${canEditView(t) ? html`
              <button title="Excluir" onClick=${() => removeView(t)}
                class="px-1.5 py-1 text-[12px] bg-wa-panel text-wa-secondary hover:bg-wa-hover hover:text-red-500 border-l border-wa-border">✕</button>` : null}
          </div>`)}
        <button title="Nova visualização" onClick=${() => openViewEditor(null)}
          class="px-2.5 py-1 text-[12px] rounded-lg border border-dashed border-wa-border text-wa-text hover:bg-wa-hover">+ Nova</button>
      </div>
      ${mode === 'kanban' && grouping.onDrop ? html`<span class="text-[11px] text-wa-secondary">Arraste um card para outra coluna (pede confirmação).</span>` : null}
      ${mode === 'kanban' && grouping.unavailable ? html`<span class="text-[11px] text-amber-600">Atributo da visualização indisponível (foi removido).</span>` : null}
    </div>`;

  const kanbanView = html`
    <div>
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
                  : cards.map((r) => html`<${KanbanCard} key=${r.id} row=${r}
                      draggedRef=${draggedRef} dragRef=${dragRef} canDrag=${!!grouping.onDrop}
                      onClearDrop=${() => setDropCol(null)} onOpen=${() => openDetail(r.id)} />`)}
              </div>
            </div>`;
        })}
      </div>
    </div>`;

  return html`
    <div>
      <!-- Configuração da aba ativa (filtros disponíveis + pré-determinados), acima da
           barra. Painel recolhível; só aparece quando há uma view real selecionada. -->
      ${activeView && activeView.id != null && activeView.id !== FALLBACK_VIEW.id ? html`
        <${ViewFilterConfig} key=${activeView.id}
          view=${activeView} coreAttrDefs=${coreAttrDefs} users=${users}
          canEdit=${canEditView(activeView)} apiBase=${apiBase} authHeaders=${authHeaders}
          onSaved=${() => { appliedViewRef.current = null; loadViews(); }} />` : null}
      <!-- Filtros principais + alternância Kanban/Lista -->
      <div class="flex flex-wrap items-end gap-3 mb-3 p-3 rounded-lg bg-wa-panel border border-wa-border">
        ${availFilter('status') ? html`
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Status</label>
          <select class="wa-field px-3 py-2 rounded-md text-[13px]" value=${status} onChange=${(e) => setStatus(e.target.value)}>
            <option value="">Todos</option>
            <option value="aberto">Aberto</option>
            <option value="fechado">Fechado</option>
          </select>
        </div>` : null}
        ${availFilter('atendente') ? html`
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Atendente</label>
          <select class="wa-field px-3 py-2 rounded-md text-[13px]"
            value=${assigneeFilter == null ? '' : String(assigneeFilter)}
            onChange=${(e) => setAssigneeFilter(e.target.value === '' ? null : +e.target.value)}>
            <option value="">Todos</option>
            ${users.map((u) => html`<option key=${u.id} value=${String(u.id)}>${u.name || `Usuário #${u.id}`}</option>`)}
          </select>
        </div>` : null}
        ${coreAttrDefs.filter((d) => d.type === 'list' && availFilter(`attr:${d.key}`)).map((d) => html`
          <div key=${d.key}>
            <label class="block text-[12px] text-wa-secondary mb-1 truncate max-w-[180px]" title=${d.label}>${d.label}</label>
            <select class="wa-field px-3 py-2 rounded-md text-[13px]" value=${attrFilters[d.key] || ''}
              onChange=${(e) => setAttrFilters((s) => { const n = { ...s }; if (e.target.value) n[d.key] = e.target.value; else delete n[d.key]; return n; })}>
              <option value="">Todos</option>
              ${(d.options || []).map((o) => html`<option key=${o} value=${o}>${o}</option>`)}
            </select>
          </div>`)}
        ${availFilter('q') ? html`
        <div class="flex-1 min-w-[180px]">
          <label class="block text-[12px] text-wa-secondary mb-1">Buscar cliente</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[13px]" type="text" value=${q}
            placeholder="nome ou telefone" onInput=${(e) => setQ(e.target.value)} />
        </div>` : null}
        ${availFilter('periodo') ? html`
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Período (criação)</label>
          <div class="flex flex-wrap items-center gap-1.5">
            <input type="date" class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${dateFrom}
              onInput=${(e) => onManualDate(e.target.value, dateTo)} />
            <span class="text-wa-secondary">→</span>
            <input type="date" class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${dateTo}
              onInput=${(e) => onManualDate(dateFrom, e.target.value)} />
            ${DATE_PRESETS.map(([lbl, d]) => html`<button key=${lbl} onClick=${() => onPresetDate(d, lbl)}
              class="px-2.5 py-1 rounded-md text-[12px] border ${datePreset === lbl ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
            <button onClick=${onClearDate}
              class="px-2.5 py-1 rounded-md text-[12px] border ${datePreset === 'tudo' ? 'bg-wa-teal text-white border-wa-teal' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">Tudo</button>
          </div>
        </div>` : null}
        <button onClick=${load} class="px-3 py-2 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover">Atualizar</button>
        ${hasViewFilters ? html`<button onClick=${clearFilters}
          class="px-3 py-2 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover">Limpar filtros</button>` : null}
        <div class="inline-flex rounded-lg border border-wa-border overflow-hidden">
          ${[['kanban', 'Kanban'], ['lista', 'Lista']].map(([k, lbl]) => html`
            <button key=${k} onClick=${() => setM(k)}
              class="px-3 py-2 text-[13px] ${mode === k ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
        </div>
      </div>

      <!-- Abas "Agrupar por" — só no Kanban (agrupamento não se aplica à Lista); aparece
           mesmo quando vazio, p/ não perder a aba selecionada. -->
      ${mode === 'kanban' ? tabBar : null}

      ${loading ? html`<div class="text-[13px] text-wa-secondary p-4">Carregando…</div>`
        : rows.length === 0 ? html`<div class="text-[13px] text-wa-secondary p-4">Nenhum protocolo.${hasViewFilters ? html` Esta aba tem filtros pré-determinados — <button onClick=${clearFilters} class="text-wa-teal hover:underline">limpar filtros</button> para ver todos.` : null}</div>`
        : mode === 'kanban' ? kanbanView : listaView}

      ${detail ? html`<${DetailModal} data=${detail} fieldDefs=${atendDefs} attrDefs=${coreAttrDefs}
        protoDefs=${cols} warning=${detailWarning} api=${api}
        onClose=${closeDetail}
        onChanged=${load} onFinalize=${finalizeProtocolo} />` : null}
    </div>`;
}

// Card do kanban: draggable (altera estado ao soltar) + clique abre o detalhe. A CAPA
// mostra só os dados próprios do protocolo (cliente, datas, atendente, status) — os
// rótulos do plugin e os atributos personalizados ficam no DETALHE (modal), junto da
// atendimento a que pertencem.
function KanbanCard({ row, draggedRef, dragRef, onClearDrop, onOpen, canDrag = true }) {
  return html`
    <div draggable=${canDrag}
      onDragStart=${(e) => {
        if (!canDrag) { e.preventDefault(); return; }
        draggedRef.current = true; dragRef.current = row;
        try { e.dataTransfer.setData('text/plain', String(row.id)); e.dataTransfer.effectAllowed = 'move'; } catch (_) { /* ignore */ }
      }}
      onDragEnd=${() => { dragRef.current = null; if (onClearDrop) onClearDrop(); setTimeout(() => { draggedRef.current = false; }, 0); }}
      onClick=${() => { if (draggedRef.current) { draggedRef.current = false; return; } onOpen(); }}
      class="p-3 rounded-lg border border-wa-border bg-wa-panel hover:border-wa-teal/50 ${canDrag ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'} transition-colors"
      title=${canDrag ? 'Arraste para mover · clique para abrir' : 'Clique para abrir'}>
      <div class="font-medium text-wa-text text-[13px] truncate">${row.contact_name || row.contact_phone || '—'}</div>
      ${row.contact_phone ? html`<div class="text-[12px] text-wa-secondary truncate">${row.contact_phone}</div>` : null}
      <div class="text-[12px] text-wa-secondary mt-1">Início: ${fmtTs(row.opened_at)}</div>
      ${row.closed_at ? html`<div class="text-[12px] text-wa-secondary">Fim: ${fmtTs(row.closed_at)}</div>` : null}
      <div class="flex items-center justify-between gap-2 mt-1">
        <span class="text-[12px] ${row.assignee_name ? 'text-wa-text' : 'text-wa-secondary'} truncate">${row.assignee_name || 'Não atribuído'}</span>
        <span class="px-1.5 py-0.5 rounded-full text-[10px] ${row.status === 'aberto' ? 'bg-wa-teal/15 text-wa-teal' : 'bg-wa-hover text-wa-secondary'}">${row.status === 'aberto' ? 'Aberto' : 'Fechado'}</span>
      </div>
    </div>`;
}

// Detalhe do protocolo. Topo = form EDITÁVEL dos campos do protocolo (OBS fixo +
// extras, normais e obrigatórios), pré-preenchido; "Salvar" persiste parcial (fechar
// depois) e "Finalizar protocolo" só habilita com os obrigatórios preenchidos. Se o
// protocolo já está FECHADO, os valores aparecem read-only (sem ações). A tabela de
// atendimentos (histórico, read-only) fica abaixo.
function DetailModal({ data, fieldDefs = [], attrDefs = [], protoDefs = [], warning = '',
                      api, onClose, onChanged, onFinalize }) {
  const at = data.protocolo || {};
  const atendimentos = data.atendimentos || [];
  const fechado = at.status === 'fechado';

  // Estado local dos campos do protocolo, pré-preenchido (OBS na coluna obs; extras em
  // at.fields). Editável enquanto aberto; serve de fonte também p/ a visão read-only.
  const [vals, setVals] = useState(() => {
    const init = {};
    for (const d of protoDefs) {
      const cur = d.key === 'obs' ? at.obs : (at.fields || {})[d.key];
      if (d.type === 'checkbox') init[d.key] = (cur === true || cur === 'true');
      else if (d.type === 'checkboxes') init[d.key] = Array.isArray(cur)
        ? cur : (cur ? String(cur).split(',').map((s) => s.trim()).filter(Boolean) : []);
      else init[d.key] = (cur == null ? '' : String(cur));
    }
    return init;
  });
  const [saving, setSaving] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [msg, setMsg] = useState(null);          // {text, error}
  const [linkCopied, setLinkCopied] = useState(false);

  // Copia o link compartilhável deste protocolo (/attendances?detail=<id>) — plano 24.
  const copyLink = () => copyText(`${location.origin}/attendances?detail=${at.id}`,
    () => { setLinkCopied(true); setTimeout(() => setLinkCopied(false), 2000); });

  const missing = protoDefs.filter((d) => d.required && !isFilledProto(d, vals[d.key]));

  // PUT parcial dos campos do protocolo (obrigatório só é exigido ao FECHAR).
  const putFields = () => fetch(`${api.apiBase}/protocolos/${at.id}/fields`, {
    method: 'PUT',
    headers: { ...api.services.authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields: vals }),
  }).then((r) => r.json());

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const j = await putFields();
      if (j && j.ok) { setMsg({ text: 'Campos salvos.', error: false }); if (onChanged) onChanged(); }
      else setMsg({ text: (j && j.error) || 'Falha ao salvar.', error: true });
    } catch (_) { setMsg({ text: 'Falha ao salvar.', error: true }); }
    finally { setSaving(false); }
  };

  const finalize = async () => {
    setFinalizing(true); setMsg(null);
    try { await putFields(); }                   // persiste os campos antes de fechar
    catch (_) { /* o close revalida os obrigatórios e devolve erro se faltar */ }
    let res;
    try { res = await onFinalize(at.id); }
    catch (_) { res = { ok: false, error: 'Falha ao finalizar.' }; }
    // Sucesso → o pai fecha o modal (setDetail(null)); só tratamos o erro aqui.
    if (res && res.ok === false) { setMsg({ text: res.error || 'Falha ao finalizar.', error: true }); setFinalizing(false); }
  };

  // Clicar numa atendimento (ciclo) → resolve a âncora (1ª mensagem do ciclo) no backend
  // e abre o chat rolando ATÉ aquele ponto exato (permalink ?message=<_id>). Se a
  // âncora falhar, abre a atendimento mesmo assim (sem o scroll fino).
  const openAtend = async (c) => {
    if (!c) return;
    let atendId = c.conversation_id;
    let msgId = null;
    try {
      const r = await fetch(`${api.apiBase}/atendimentos/${c.id}/anchor`, { headers: api.services.authHeaders() });
      const d = await r.json();
      if (d && d.ok && d.data) {
        if (d.data.conversation_id != null) atendId = d.data.conversation_id;
        if (d.data.message_id != null) msgId = d.data.message_id;
      }
    } catch (_) { /* fallback: abre a atendimento sem âncora de mensagem */ }
    onClose();
    openConversation(atendId, msgId);
  };

  // Visão read-only (protocolo fechado): só os campos com valor (checkbox sempre exibe).
  const readOnlyInfo = protoDefs.filter((d) => d.type === 'checkbox' || isFilledProto(d, vals[d.key]));

  return html`
    <div class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4">
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-2xl w-full p-6 max-h-[85vh] overflow-auto">
        <div class="flex items-center justify-between mb-3 gap-2">
          <h2 class="text-base font-semibold text-wa-text truncate">${at.contact_name || at.contact_phone || 'Protocolo'}</h2>
          <div class="flex items-center gap-1 shrink-0">
            <button onClick=${copyLink} title="Copiar link deste protocolo"
              class="px-2 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">
              ${linkCopied ? '✓ Link copiado' : 'Copiar link'}
            </button>
            <button onClick=${onClose} class="text-wa-secondary hover:text-wa-text text-xl leading-none px-1">×</button>
          </div>
        </div>
        <div class="text-[12px] text-wa-secondary mb-3">
          Início: ${fmtTs(at.opened_at)}${at.closed_at ? ` · Fim: ${fmtTs(at.closed_at)}` : ''} · ${fechado ? 'Fechado' : 'Aberto'}
        </div>

        ${warning ? html`
          <div class="mb-3 px-3 py-2.5 rounded-md bg-amber-500/15 border border-amber-500 text-amber-700 text-[13px] font-semibold">
            ${warning}
          </div>` : null}

        ${fechado ? html`
          <div class="mb-4 p-3 rounded-lg bg-wa-panel border border-wa-border">
            <div class="text-wa-iconActive text-[13px] font-semibold mb-1.5">Dados do protocolo</div>
            ${readOnlyInfo.length ? html`
              <div class="flex flex-wrap gap-x-5 gap-y-1 text-[12px]">
                ${readOnlyInfo.map((d) => html`<div key=${d.key}>
                  <span class="text-wa-secondary">${d.label}:</span> <span class="text-wa-text">${fmtCell(vals[d.key], d)}</span>
                </div>`)}
              </div>` : html`<div class="text-[12px] text-wa-secondary">Sem dados preenchidos.</div>`}
          </div>`
        : html`
          <div class="mb-4 p-3 rounded-lg bg-wa-panel border border-wa-border">
            <div class="text-wa-iconActive text-[13px] font-semibold mb-2">Dados do protocolo</div>
            <div class="space-y-3">
              ${protoDefs.map((d) => html`<${LabeledField} key=${d.key} def=${d}
                value=${vals[d.key]}
                onChange=${(v) => setVals((s) => ({ ...s, [d.key]: v }))} />`)}
            </div>
            ${missing.length ? html`
              <div class="mt-3 text-[12px] text-wa-secondary">
                Preencha os campos obrigatórios para finalizar: ${missing.map((d) => d.label || d.key).join(', ')}.
              </div>` : null}
            ${msg ? html`
              <div class="mt-3 px-3 py-2 rounded-md text-[13px] ${msg.error ? 'bg-red-500/15 border border-red-500 text-red-500 font-semibold' : 'bg-wa-teal/10 text-wa-teal'}">${msg.text}</div>` : null}
            <div class="flex gap-2 mt-4">
              <button onClick=${save} disabled=${saving || finalizing}
                class="px-4 py-2 rounded-lg bg-wa-panel border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-50 text-[14px]">
                ${saving ? 'Salvando…' : 'Salvar'}</button>
              <button onClick=${finalize} disabled=${missing.length > 0 || finalizing || saving}
                class="flex-1 px-4 py-2 rounded-lg bg-wa-teal text-white hover:opacity-90 disabled:opacity-50 text-[14px] font-medium">
                ${finalizing ? 'Finalizando…' : 'Finalizar protocolo'}</button>
            </div>
          </div>`}

        <div class="text-wa-iconActive text-[13px] font-semibold mb-2">Atendimentos</div>
        <div class="text-[12px] text-wa-secondary mb-2">Clique num atendimento para abri-lo no chat. As colunas estão agrupadas em <span class="text-wa-teal font-medium">Informações do atendimento</span> e <span class="text-amber-600 font-medium">Atributos personalizados</span>.</div>
        <${AtendimentosTable} atendimentos=${atendimentos} fieldDefs=${fieldDefs} attrDefs=${attrDefs}
          storageKey="whatsbot_proto_atend_cols_modal" onRowClick=${openAtend} />
      </div>
    </div>`;
}

// Diálogo de confirmação genérico (usado pelo drag que altera valor e pelo excluir
// visualização). Descreve a ação e só resolve true ao confirmar.
function ConfirmDialog({ message, onOk, onCancel, okLabel = 'Confirmar', danger = false }) {
  return html`
    <div class="fixed inset-0 bg-black/50 z-[80] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-md w-full p-6">
        <div class="text-[14px] text-wa-text mb-4">${message}</div>
        <div class="flex justify-end gap-2">
          <button onClick=${onCancel}
            class="px-4 py-2 rounded-lg bg-wa-panel border border-wa-border text-wa-text hover:bg-wa-hover text-[14px]">Cancelar</button>
          <button onClick=${onOk}
            class="px-4 py-2 rounded-lg ${danger ? 'bg-red-500' : 'bg-wa-teal'} text-white hover:opacity-90 text-[14px] font-medium">${okLabel}</button>
        </div>
      </div>
    </div>`;
}

// Painel recolhível (acima da barra de filtros, no Kanban e na Lista) que configura a aba
// ATIVA: quais filtros ficam DISPONÍVEIS na barra ao vivo (metadado da view; só o editor
// muda) e os PRÉ-DETERMINADOS de entrada (origem Pessoal por-usuário x Equipe compartilhada,
// esta só o editor). Extraído do ViewEditorModal — que hoje cuida só de Nome/Quem pode
// ver/Agrupar por. Montar com key=${view.id} no pai reinicializa o estado ao trocar de aba.
// Salva available_filters+filters (PUT /kanban-views, só editor) e a preferência pessoal
// (PUT /kanban-views/{id}/my-pref, sempre).
function ViewFilterConfig({ view, coreAttrDefs, users, canEdit, apiBase, authHeaders, onSaved }) {
  const listAttrs = (coreAttrDefs || []).filter((d) => d.type === 'list');
  const initTeam = (view && view.filters) || {};
  const initPersonal = (view && view.pref && view.pref.personal_filters) || {};
  const initUsePersonal = !!(view && view.pref && view.pref.use_personal);
  const initActive = initUsePersonal ? initPersonal : initTeam;  // origem mostrada ao abrir

  const [open, setOpen] = useState(false);
  const [usePersonal, setUsePersonal] = useState(initUsePersonal);  // toggle origem dos filtros
  const [fStatusOn, setFStatusOn] = useState(initActive.status != null);
  const [fStatus, setFStatus] = useState(initActive.status || '');
  const [fQOn, setFQOn] = useState(initActive.q != null);
  const [fQ, setFQ] = useState(initActive.q || '');
  const [fAssignOn, setFAssignOn] = useState(initActive.assignee_user_id != null);
  const [fAssign, setFAssign] = useState(initActive.assignee_user_id != null ? String(initActive.assignee_user_id) : '');
  const [fDateOn, setFDateOn] = useState(!!initActive.date);
  const [fDatePreset, setFDatePreset] = useState((initActive.date && initActive.date.preset) || '7 dias');
  const [fAttrs, setFAttrs] = useState((initActive.attrs && typeof initActive.attrs === 'object') ? { ...initActive.attrs } : {});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const [okMsg, setOkMsg] = useState('');
  // Buffer das DUAS origens; os campos visíveis editam só a origem ativa. Ao alternar,
  // os campos são salvos no buffer de saída e o buffer de entrada é carregado nos campos.
  const bufRef = useRef({ team: { ...initTeam }, personal: { ...initPersonal } });

  // Filtros de EQUIPE são read-only para quem não pode editar a visualização.
  const fieldsRO = !usePersonal && !canEdit;

  // Filtros DISPONÍVEIS nesta aba (metadado da VIEW; só o editor muda). null na view = TODOS.
  // Chaves: status | atendente | q | periodo | attr:<key>.
  const ALL_FILTER_KEYS = ['status', 'atendente', 'q', 'periodo', ...listAttrs.map((d) => `attr:${d.key}`)];
  const initAvail = (view && Array.isArray(view.available_filters)) ? view.available_filters : null;
  const [availSet, setAvailSet] = useState(() => new Set(initAvail == null ? ALL_FILTER_KEYS : initAvail));
  const isAvail = (key) => availSet.has(key);
  const toggleAvail = (key) => { setOkMsg(''); setAvailSet((s) => {
    const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n;
  }); };

  const setAttr = (key, val) => { setOkMsg(''); setFAttrs((s) => {
    const next = { ...s };
    if (val === '' || val == null) delete next[key]; else next[key] = val;
    return next;
  }); };

  const buildFilters = () => {
    const f = {};
    if (isAvail('status') && fStatusOn) f.status = fStatus;
    if (isAvail('q') && fQOn && fQ.trim()) f.q = fQ.trim();
    if (isAvail('atendente') && fAssignOn && fAssign) f.assignee_user_id = +fAssign;
    if (isAvail('periodo') && fDateOn) f.date = { preset: fDatePreset, from: '', to: '' };
    const attrs = {};
    for (const [k, v] of Object.entries(fAttrs)) if (isAvail(`attr:${k}`) && v != null && v !== '') attrs[k] = v;
    if (Object.keys(attrs).length) f.attrs = attrs;
    return f;
  };

  const loadFiltersIntoFields = (f) => {
    f = f || {};
    setFStatusOn(f.status != null); setFStatus(f.status || '');
    setFQOn(f.q != null); setFQ(f.q || '');
    setFAssignOn(f.assignee_user_id != null);
    setFAssign(f.assignee_user_id != null ? String(f.assignee_user_id) : '');
    setFDateOn(!!f.date); setFDatePreset((f.date && f.date.preset) || '7 dias');
    setFAttrs((f.attrs && typeof f.attrs === 'object') ? { ...f.attrs } : {});
  };

  const switchSource = (toPersonal) => {
    if (toPersonal === usePersonal) return;
    setOkMsg('');
    bufRef.current[usePersonal ? 'personal' : 'team'] = buildFilters();  // guarda edições atuais
    setUsePersonal(toPersonal);
    loadFiltersIntoFields(bufRef.current[toPersonal ? 'personal' : 'team']);
  };

  const save = async () => {
    setErr(''); setOkMsg(''); setSaving(true);
    // Captura as edições atuais na origem ativa antes de ler os dois buffers.
    bufRef.current[usePersonal ? 'personal' : 'team'] = buildFilters();
    const teamFilters = bufRef.current.team;
    const personalFilters = bufRef.current.personal;
    try {
      // Metadados de filtro da EQUIPE (available_filters + filters): só quem pode editar.
      // O body NÃO leva name/scope/group/ACL → o backend preserva (logic.update_kanban_view).
      if (canEdit) {
        // available_filters: todos habilitados → null (todos, à prova de futuro); senão a lista.
        const enabled = ALL_FILTER_KEYS.filter((k) => availSet.has(k));
        const available_filters = enabled.length === ALL_FILTER_KEYS.length ? null : enabled;
        const r = await fetch(`${apiBase}/kanban-views/${view.id}`, {
          method: 'PUT',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ available_filters, filters: teamFilters }),
        });
        const j = await r.json().catch(() => ({}));
        if (!(j && j.ok)) { setErr((j && j.error) || 'Falha ao salvar.'); setSaving(false); return; }
      }
      // Preferência do PRÓPRIO usuário (origem + filtros pessoais), sempre.
      const pr = await fetch(`${apiBase}/kanban-views/${view.id}/my-pref`, {
        method: 'PUT',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_personal: usePersonal, personal_filters: personalFilters }),
      });
      const pj = await pr.json().catch(() => ({}));
      if (!(pj && pj.ok)) { setErr((pj && pj.error) || 'Falha ao salvar preferência.'); setSaving(false); return; }
      setSaving(false); setOkMsg('Configuração salva.');
      onSaved && onSaved();
    } catch (_) { setErr('Falha ao salvar.'); setSaving(false); }
  };

  const fieldCls = 'wa-field px-3 py-2 rounded-md text-[13px]';
  return html`
    <div class="mb-3 rounded-lg bg-wa-panel border border-wa-border">
      <button type="button" onClick=${() => setOpen((o) => !o)}
        class="w-full flex items-center gap-2 px-3 py-2 text-[13px] text-wa-text hover:bg-wa-hover rounded-lg">
        <span class="text-wa-secondary w-3">${open ? '▾' : '▸'}</span>
        <span>⚙ Configurar filtros da aba</span>
      </button>
      ${open ? html`
      <div class="px-3 pb-3 border-t border-wa-border">
        ${canEdit ? html`
        <div class="mb-4 mt-3">
          <span class="block text-[12px] text-wa-secondary font-medium mb-1">Filtros disponíveis nesta aba</span>
          <div class="text-[11px] text-wa-secondary mb-2">Só os filtros marcados aparecem na barra de filtros e podem ser pré-determinados.</div>
          <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-wa-text">
            ${[['status', 'Status'], ['atendente', 'Atendente'], ['q', 'Buscar'], ['periodo', 'Período'],
               ...listAttrs.map((d) => [`attr:${d.key}`, d.label])].map(([key, lbl]) => html`
              <label key=${key} class="inline-flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" checked=${isAvail(key)} onChange=${() => toggleAvail(key)} /> ${lbl}
              </label>`)}
          </div>
        </div>` : null}

        <div class="mb-2 ${canEdit ? '' : 'mt-3'}">
          <span class="block text-[12px] text-wa-secondary font-medium mb-1">Filtros pré-determinados desta aba</span>
          <div class="flex items-center gap-4 text-[13px] text-wa-text">
            <label class="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="radio" name=${`filtersrc-${view.id}`} checked=${usePersonal} onChange=${() => switchSource(true)} /> Pessoal
            </label>
            <label class="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="radio" name=${`filtersrc-${view.id}`} checked=${!usePersonal} onChange=${() => switchSource(false)} /> Equipe
            </label>
          </div>
          <div class="text-[11px] text-wa-secondary mt-1">
            ${usePersonal
              ? 'Ao entrar nesta aba, você verá os SEUS filtros pessoais.'
              : (canEdit
                  ? 'Ao entrar nesta aba, você (e a equipe) verá os filtros definidos abaixo.'
                  : 'Ao entrar nesta aba, você verá os filtros definidos pela equipe (somente leitura).')}
          </div>
        </div>
        <div class="space-y-2 mb-4">
          ${isAvail('status') ? html`
          <div class="flex items-center gap-2">
            <label class="inline-flex items-center gap-1.5 text-[13px] text-wa-text w-28">
              <input type="checkbox" checked=${fStatusOn} disabled=${fieldsRO} onChange=${(e) => { setOkMsg(''); setFStatusOn(e.target.checked); }} /> Status
            </label>
            <select class="${fieldCls} flex-1" disabled=${fieldsRO || !fStatusOn} value=${fStatus} onChange=${(e) => { setOkMsg(''); setFStatus(e.target.value); }}>
              <option value="">Todos</option>
              <option value="aberto">Aberto</option>
              <option value="fechado">Fechado</option>
            </select>
          </div>` : null}
          ${isAvail('q') ? html`
          <div class="flex items-center gap-2">
            <label class="inline-flex items-center gap-1.5 text-[13px] text-wa-text w-28">
              <input type="checkbox" checked=${fQOn} disabled=${fieldsRO} onChange=${(e) => { setOkMsg(''); setFQOn(e.target.checked); }} /> Buscar
            </label>
            <input class="${fieldCls} flex-1" type="text" disabled=${fieldsRO || !fQOn} value=${fQ}
              placeholder="nome ou telefone" onInput=${(e) => { setOkMsg(''); setFQ(e.target.value); }} />
          </div>` : null}
          ${isAvail('atendente') ? html`
          <div class="flex items-center gap-2">
            <label class="inline-flex items-center gap-1.5 text-[13px] text-wa-text w-28">
              <input type="checkbox" checked=${fAssignOn} disabled=${fieldsRO} onChange=${(e) => { setOkMsg(''); setFAssignOn(e.target.checked); }} /> Atendente
            </label>
            <select class="${fieldCls} flex-1" disabled=${fieldsRO || !fAssignOn} value=${fAssign} onChange=${(e) => { setOkMsg(''); setFAssign(e.target.value); }}>
              <option value="">Qualquer</option>
              ${(users || []).map((u) => html`<option key=${u.id} value=${String(u.id)}>${u.name || `Usuário #${u.id}`}</option>`)}
            </select>
          </div>` : null}
          ${isAvail('periodo') ? html`
          <div class="flex items-center gap-2">
            <label class="inline-flex items-center gap-1.5 text-[13px] text-wa-text w-28">
              <input type="checkbox" checked=${fDateOn} disabled=${fieldsRO} onChange=${(e) => { setOkMsg(''); setFDateOn(e.target.checked); }} /> Período
            </label>
            <select class="${fieldCls} flex-1" disabled=${fieldsRO || !fDateOn} value=${fDatePreset} onChange=${(e) => { setOkMsg(''); setFDatePreset(e.target.value); }}>
              ${DATE_PRESETS.map(([lbl]) => html`<option key=${lbl} value=${lbl}>Últimos: ${lbl}</option>`)}
              <option value="tudo">Tudo</option>
            </select>
          </div>` : null}
          ${listAttrs.filter((d) => isAvail(`attr:${d.key}`)).length ? html`
            <div class="pt-1">
              <div class="text-[12px] text-wa-secondary mb-1">Atributos (lista)</div>
              ${listAttrs.filter((d) => isAvail(`attr:${d.key}`)).map((d) => html`
                <div key=${d.key} class="flex items-center gap-2 mb-1">
                  <span class="text-[13px] text-wa-text w-28 truncate" title=${d.label}>${d.label}</span>
                  <select class="${fieldCls} flex-1" disabled=${fieldsRO} value=${fAttrs[d.key] || ''} onChange=${(e) => setAttr(d.key, e.target.value)}>
                    <option value="">— sem filtro —</option>
                    ${(d.options || []).map((o) => html`<option key=${o} value=${o}>${o}</option>`)}
                  </select>
                </div>`)}
            </div>` : null}
        </div>

        ${err ? html`<div class="mb-3 px-3 py-2 rounded-md text-[13px] bg-red-500/15 border border-red-500 text-red-500 font-semibold">${err}</div>` : null}
        <div class="flex items-center justify-end gap-3">
          ${okMsg ? html`<span class="text-[12px] text-wa-teal">${okMsg}</span>` : null}
          <button onClick=${save} disabled=${saving}
            class="px-4 py-2 rounded-lg bg-wa-teal text-white hover:opacity-90 disabled:opacity-50 text-[13px] font-medium">${saving ? 'Salvando…' : 'Salvar configuração da aba'}</button>
        </div>
      </div>` : null}
    </div>`;
}

// Editor de visualização (criar/editar uma aba de "Agrupar por"). Cuida só dos METADADOS:
// Nome, Quem pode ver (ACL de grupos/usuários) e Agrupar por (nativos + atributos lista).
// Os filtros DISPONÍVEIS e PRÉ-DETERMINADOS saíram para o painel "Configurar filtros da aba"
// (ViewFilterConfig, acima da barra). Salva via POST/PUT /kanban-views SEM tocar em
// filters/available_filters (o backend preserva no update e usa defaults no create).
function ViewEditorModal({ view, coreAttrDefs, users, roles, canTeam, currentUser, api, onSaved, onCancel }) {
  const editing = !!(view && view.id != null);
  // Pode editar os METADADOS + filtros de equipe? Criar (view=null) sempre pode (vira pessoal).
  const canEditMeta = !view ? true
    : (canTeam || (view.scope === 'personal'
        && (!currentUser || view.owner_user_id === currentUser.id)));
  const listAttrs = (coreAttrDefs || []).filter((d) => d.type === 'list');
  const initGroup = () => {
    if (!view) return 'status';
    return view.group_by === 'attr' ? `attr:${view.group_attr_key || ''}` : view.group_by;
  };
  // ACL "Quem pode ver": grupos (roles) + usuários (3 estados: padrão/incluir/excluir).
  const initRoleKeys = (view && Array.isArray(view.visibility_roles)) ? view.visibility_roles : [];
  const initInc = (view && Array.isArray(view.visibility_users_include)) ? view.visibility_users_include : [];
  const initExc = (view && Array.isArray(view.visibility_users_exclude)) ? view.visibility_users_exclude : [];
  // Equipe LEGADO (view team sem ACL): preservar "todos veem" ao salvar sem selecionar nada.
  const wasLegacyTeamAll = editing && view.scope === 'team' && !initRoleKeys.length && !initInc.length;

  const [name, setName] = useState((view && view.name) || '');
  const [visTab, setVisTab] = useState('grupos');                  // 'grupos' | 'usuarios'
  const [visRoles, setVisRoles] = useState(() => new Set(initRoleKeys.map(String)));
  const [userStates, setUserStates] = useState(() => {
    const m = {};
    initInc.forEach((id) => { m[String(id)] = 'include'; });
    initExc.forEach((id) => { m[String(id)] = 'exclude'; });
    return m;
  });
  const [groupSel, setGroupSel] = useState(initGroup());
  const [dateMode, setDateMode] = useState((view && view.group_date_mode) || 'faixas');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  // ACL "Quem pode ver": grupos + usuários (3 estados). scope é DERIVADO (compartilha → team).
  const toggleRole = (key) => setVisRoles((s) => {
    const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n;
  });
  const setUserState = (uid, st) => setUserStates((s) => {
    const n = { ...s }; if (st === 'padrao') delete n[String(uid)]; else n[String(uid)] = st; return n;
  });
  const userState = (uid) => userStates[String(uid)] || 'padrao';
  const aclBody = () => {
    const roleArr = [...visRoles];
    const includeIds = Object.keys(userStates).filter((k) => userStates[k] === 'include').map(Number);
    const excludeIds = Object.keys(userStates).filter((k) => userStates[k] === 'exclude').map(Number);
    const shared = roleArr.length > 0 || includeIds.length > 0;
    const scope = shared ? 'team' : (wasLegacyTeamAll ? 'team' : 'personal');
    return { scope, visibility_roles: roleArr,
             visibility_users_include: includeIds, visibility_users_exclude: excludeIds };
  };

  const save = async () => {
    setErr('');
    const gb = groupSel.startsWith('attr:') ? 'attr' : groupSel;
    const gak = groupSel.startsWith('attr:') ? groupSel.slice(5) : null;
    const acl = aclBody();  // { scope derivado, visibility_roles, users_include, users_exclude }
    if (!name.trim()) { setErr('Informe um nome para a visualização.'); return; }
    if (gb === 'attr' && !gak) { setErr('Selecione um atributo (lista) para agrupar.'); return; }
    if (acl.scope === 'team' && !canTeam) { setErr('Sem permissão para compartilhar (visualização de equipe).'); return; }
    setSaving(true);
    try {
      // Só METADADOS. O body NÃO leva filters/available_filters → o backend preserva no
      // update e usa defaults ({}/null=todos) no create. Esses vivem no painel "Configurar
      // filtros da aba" (ViewFilterConfig), acima da barra de filtros.
      const body = {
        name: name.trim(), scope: acl.scope, group_by: gb, group_attr_key: gak,
        visibility_roles: acl.visibility_roles,
        visibility_users_include: acl.visibility_users_include,
        visibility_users_exclude: acl.visibility_users_exclude,
        group_date_mode: gb === 'data' ? dateMode : null,
      };
      const url = editing ? `${api.apiBase}/kanban-views/${view.id}` : `${api.apiBase}/kanban-views`;
      const r = await fetch(url, {
        method: editing ? 'PUT' : 'POST',
        headers: { ...api.services.authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await r.json().catch(() => ({}));
      if (!(j && j.ok)) { setErr((j && j.error) || 'Falha ao salvar.'); setSaving(false); return; }
      const vid = (j.data && j.data.id != null) ? j.data.id : (editing ? view.id : null);
      onSaved({ id: vid });
    } catch (_) { setErr('Falha ao salvar.'); setSaving(false); }
  };

  const fieldCls = 'wa-field px-3 py-2 rounded-md text-[13px]';
  const title = editing ? 'Editar visualização' : 'Nova visualização';
  return html`
    <div class="fixed inset-0 bg-black/50 z-[75] flex items-center justify-center p-4">
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-lg w-full p-6 max-h-[85vh] overflow-auto">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-base font-semibold text-wa-text">${title}</h2>
          <button onClick=${onCancel} class="text-wa-secondary hover:text-wa-text text-xl leading-none">×</button>
        </div>

        ${canEditMeta ? html`
        <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
        <input class="${fieldCls} w-full mb-4" type="text" value=${name}
          placeholder="Ex.: Por etapa de venda" onInput=${(e) => setName(e.target.value)} />

        <div class="mb-4">
          <span class="block text-[12px] text-wa-secondary mb-1">Quem pode ver</span>
          <div class="inline-flex rounded-lg border border-wa-border overflow-hidden mb-2">
            ${[['grupos', 'Grupos'], ['usuarios', 'Usuários']].map(([k, lbl]) => html`
              <button key=${k} type="button" onClick=${() => setVisTab(k)}
                class="px-3 py-1 text-[12px] ${visTab === k ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
          </div>
          ${!canTeam ? html`<div class="text-[11px] text-wa-secondary mb-1">Sem permissão para compartilhar — a visualização fica só sua (pessoal).</div>` : null}
          ${visTab === 'grupos' ? html`
            <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-wa-text">
              ${(roles || []).length ? (roles || []).map((r) => html`
                <label key=${r.key} class="inline-flex items-center gap-1.5 ${canTeam ? 'cursor-pointer' : 'opacity-50 cursor-not-allowed'}">
                  <input type="checkbox" disabled=${!canTeam} checked=${visRoles.has(r.key)} onChange=${() => canTeam && toggleRole(r.key)} /> ${r.name || r.key}
                </label>`) : html`<span class="text-[11px] text-wa-secondary">Nenhum grupo disponível.</span>`}
            </div>` : html`
            <div class="max-h-40 overflow-auto rounded-md border border-wa-border p-2 space-y-1">
              ${(users || []).length ? (users || []).map((u) => html`
                <div key=${u.id} class="flex items-center justify-between gap-2 text-[13px]">
                  <span class="text-wa-text truncate" title=${u.name || `Usuário #${u.id}`}>${u.name || `Usuário #${u.id}`}</span>
                  <div class="inline-flex rounded-md border border-wa-border overflow-hidden shrink-0">
                    ${[['padrao', 'Padrão'], ['include', 'Incluir'], ['exclude', 'Excluir']].map(([st, lbl]) => html`
                      <button key=${st} type="button" disabled=${!canTeam} onClick=${() => canTeam && setUserState(u.id, st)}
                        class="px-2 py-0.5 text-[11px] ${canTeam ? '' : 'opacity-50 cursor-not-allowed'} ${userState(u.id) === st ? (st === 'exclude' ? 'bg-red-500 text-white' : (st === 'include' ? 'bg-wa-teal text-white' : 'bg-wa-hover text-wa-text')) : 'bg-wa-panel text-wa-secondary hover:bg-wa-hover'}">${lbl}</button>`)}
                  </div>
                </div>`) : html`<span class="text-[11px] text-wa-secondary">Nenhum usuário.</span>`}
            </div>`}
          <div class="text-[11px] text-wa-secondary mt-1">
            ${aclBody().scope === 'team'
              ? 'Compartilhada: veem os grupos e usuários incluídos (menos os excluídos). Você e admins veem sempre.'
              : 'Sem grupos/usuários selecionados: só você vê esta visualização (pessoal).'}
          </div>
        </div>

        <label class="block text-[12px] text-wa-secondary mb-1">Agrupar por</label>
        <select class="${fieldCls} w-full mb-2" value=${groupSel} onChange=${(e) => setGroupSel(e.target.value)}>
          <optgroup label="Nativos">
            <option value="status">Status</option>
            <option value="atendente">Atendente</option>
            <option value="data">Data</option>
          </optgroup>
          ${listAttrs.length ? html`<optgroup label="Atributos (lista)">
            ${listAttrs.map((d) => html`<option key=${d.key} value=${`attr:${d.key}`}>${d.label}</option>`)}
          </optgroup>` : null}
        </select>
        ${groupSel === 'data' ? html`
          <select class="${fieldCls} w-full mb-4" value=${dateMode} onChange=${(e) => setDateMode(e.target.value)}>
            <option value="faixas">Faixas relativas (Hoje, Ontem, …)</option>
            <option value="dia">Por dia</option>
            <option value="mes">Por mês</option>
          </select>` : html`<div class="mb-4"></div>`}
        ` : html`<div class="text-[13px] text-wa-secondary mb-4">Sem permissão para editar esta visualização. Ajuste os filtros da aba no painel "Configurar filtros da aba", acima da barra de filtros.</div>`}

        ${err ? html`<div class="mb-3 px-3 py-2 rounded-md text-[13px] bg-red-500/15 border border-red-500 text-red-500 font-semibold">${err}</div>` : null}
        <div class="flex justify-end gap-2">
          <button onClick=${onCancel} class="px-4 py-2 rounded-lg bg-wa-panel border border-wa-border text-wa-text hover:bg-wa-hover text-[14px]">Cancelar</button>
          ${canEditMeta ? html`<button onClick=${save} disabled=${saving}
            class="px-4 py-2 rounded-lg bg-wa-teal text-white hover:opacity-90 disabled:opacity-50 text-[14px] font-medium">${saving ? 'Salvando…' : 'Salvar'}</button>` : null}
        </div>
      </div>
    </div>`;
}

export default ProtocolosTab;
