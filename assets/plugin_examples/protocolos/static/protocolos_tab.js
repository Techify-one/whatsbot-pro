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
import { ResolveForm, LabeledField, isMultiDef } from '/plugins/protocolos/static/resolve_form.js';
import { OptionListSelect } from '/static/js/components/OptionListSelect.js';

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
  if (isMultiDef(def)) return (Array.isArray(v) && v.length) ? v.join(', ') : '—';
  return (v == null || v === '') ? '—' : String(v);
}

// Campo do protocolo "preenchido"? checkbox sempre conta como preenchido (consistente
// com o backend _missing_required e com o isFilled do popup de resolver). Usado pelo gate
// de obrigatórios do detalhe e pelo guard do drag para "Fechado".
function isFilledProto(def, v) {
  if (def.type === 'checkbox') return true;
  if (isMultiDef(def)) return Array.isArray(v) ? v.length > 0 : !!v;
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

// Tipos de campo de protocolo com OPÇÕES fixas (viram colunas de Kanban / filtro dropdown).
const OPTION_TYPES = new Set(['select', 'radio', 'checkboxes']);
// Um campo é MULTI-valor (várias colunas) quando select/checkboxes com `multiple`. (checkboxes
// SEM multiple guarda lista de 1 item — conta como valor único p/ agrupar.)
const isMultiField = (d) => (d.type === 'select' || d.type === 'checkboxes') && !!d.multiple;
// Campos de opção de um escopo, com {scope,key,label,type,options,multiple}. singleOnly → só
// valor único (agrupar/arrastar); senão todos (filtros).
function optionFields(defs, scope, singleOnly) {
  return (defs || [])
    .filter((d) => OPTION_TYPES.has(d.type) && Array.isArray(d.options) && d.options.length
                   && (!singleOnly || !isMultiField(d)))
    .map((d) => ({ scope, key: d.key, label: d.label || d.key, type: d.type,
                   options: d.options, multiple: !!d.multiple }));
}

function buildGrouping(view, { users, groupFields, rows, apiPost }) {
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

  if (gb === 'pfield') {
    const scope = view && view.group_field_scope;
    const key = view && view.group_attr_key;
    const def = (groupFields || []).find((d) => d.scope === scope && d.key === key);
    if (!def) {
      return {
        readOnly: true, unavailable: true,
        columns: [{ id: '__none__', label: 'Campo indisponível' }],
        columnIdOf: () => '__none__',
      };
    }
    const opts = Array.isArray(def.options) ? def.options : [];
    // Valor do campo na row: escopo protocolo → `fields`; atendimento → `atendimento_fields`.
    // checkboxes de valor único guarda lista de 1 item → usa o 1º.
    const valOf = (r) => {
      const src = (scope === 'protocolo' ? r.fields : r.atendimento_fields) || {};
      let v = src[key];
      if (Array.isArray(v)) v = v.length ? v[0] : '';
      return v;
    };
    return {
      columns: [{ id: '__none__', label: 'Sem valor' },
                ...opts.map((o) => ({ id: `o:${o}`, label: o }))],
      columnIdOf: (r) => { const v = valOf(r); return (v != null && v !== '') ? `o:${v}` : '__none__'; },
      confirmText: (r, col) => (col === '__none__'
        ? `Limpar "${def.label}" do protocolo de ${cliente(r)}?`
        : `Definir "${def.label}" = "${col.slice(2)}" no protocolo de ${cliente(r)}?`),
      onDrop: (r, col) => apiPost(`/protocolos/${r.id}/set-field`, {
        scope, key, value: col === '__none__' ? null : col.slice(2),
      }),
    };
  }

  if (gb === 'attr') {  // legado: agrupamento por atributo de conversa (descontinuado)
    return {
      readOnly: true, unavailable: true,
      columns: [{ id: '__none__', label: 'Agrupamento indisponível' }],
      columnIdOf: () => '__none__',
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

// Reordena as colunas geradas por `buildGrouping` segundo uma lista salva de ids. As colunas
// cujo id está em `order` vêm primeiro (na ordem de `order`); as demais (dinâmicas novas,
// ausentes da ordem salva) seguem na ordem natural, no fim. order vazio/nulo → intacto.
function applyColumnOrder(columns, order) {
  if (!Array.isArray(order) || !order.length) return columns;
  const byId = new Map(columns.map((c) => [c.id, c]));
  const seen = new Set();
  const out = [];
  for (const id of order) { const c = byId.get(id); if (c && !seen.has(id)) { out.push(c); seen.add(id); } }
  for (const c of columns) if (!seen.has(c.id)) out.push(c);
  return out;
}

export function ProtocolosTab({ api, setTab }) {
  // Alternância Kanban/Lista vive AQUI (topo, ao lado do título) — o modo é passado ao
  // ProtocolosList por prop. Persistido por-device em localStorage (MODE_KEY).
  const [mode, setMode] = useState(() => lsGet(MODE_KEY, 'lista'));  // 'lista' | 'kanban'
  const setM = (m) => { setMode(m); lsSet(MODE_KEY, m); };
  return html`
    <div>
      <div class="flex items-center gap-2 mb-3">
        ${setTab ? html`<button onClick=${() => setTab('contacts')}
          class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover inline-flex items-center gap-1.5 whitespace-nowrap">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>
          Voltar
        </button>` : null}
        <h1 class="text-lg font-semibold text-wa-text">Protocolos</h1>
        <div class="inline-flex rounded-lg border border-wa-border overflow-hidden">
          ${[['kanban', 'Kanban'], ['lista', 'Lista']].map(([k, lbl]) => html`
            <button key=${k} onClick=${() => setM(k)}
              class="px-3 py-1.5 text-[13px] ${mode === k ? 'bg-wa-teal text-white' : 'bg-wa-panel text-wa-text hover:bg-wa-hover'}">${lbl}</button>`)}
        </div>
      </div>
      <${ProtocolosList} api=${api} mode=${mode} />
    </div>`;
}

function ProtocolosList({ api, mode }) {
  const apiBase = api.apiBase;
  const { authHeaders } = api.services;

  const [cols, setCols] = useState([]);          // TODAS as defs do protocolo (OBS fixo + extras, com `required`)
  const [atendDefs, setAtendDefs] = useState([]);  // defs de "Resolver atendimento" (sub-tabela do detalhe)
  const [atendResolveDefs, setAtendResolveDefs] = useState([]); // atendimento: obs+extras editáveis (popup)
  const [coreAttrDefs, setCoreAttrDefs] = useState([]);   // atributo de CONVERSA do core (só p/ o detalhe)
  const [contactAttrDefs, setContactAttrDefs] = useState([]);  // atributo de CONTATO do core (filtros, tipo list, não-sistema)
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  // Padrão ao entrar/F5: intervalo começa HOJE e fica aberto p/ frente (sem teto).
  // Não é persistido — todo (re)carregamento da página volta a "hoje para frente".
  const [dateFrom, setDateFrom] = useState(() => ymd(new Date()));
  const [dateTo, setDateTo] = useState('');
  const [datePreset, setDatePreset] = useState(null);
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
  const [savingPref, setSavingPref] = useState(false);  // salvando filtros pessoais/equipe
  const [prefMsg, setPrefMsg] = useState('');           // feedback do salvar/alternar origem
  const [detail, setDetail] = useState(null);        // {protocolo, atendimentos}
  const [detailWarning, setDetailWarning] = useState(''); // aviso no detalhe (vindo do drag p/ "Fechado")
  const appliedViewRef = useRef(null);               // última aba cujos filtros já foram aplicados
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

  // Campos de "Configurações — Protocolos" (escopos protocolo + atendimento) que alimentam o
  // Kanban: `groupFields` = opção de valor único (agrupar/arrastar); `filterFields` = todos os
  // de opção (filtrar, inclusive multi). `cols` = defs do protocolo; `atendDefs` = de atendimento.
  const groupFields = useMemo(() => [
    ...optionFields(cols, 'protocolo', true),
    ...optionFields(atendDefs, 'atendimento', true),
  ], [cols, atendDefs]);
  const filterFields = useMemo(() => [
    ...optionFields(cols, 'protocolo', false),
    ...optionFields(atendDefs, 'atendimento', false),
  ], [cols, atendDefs]);
  // Pode CRIAR novas visualizações (agrupamentos)? Gate do "+ Nova". create_views OU
  // manage_team_views (quem gerencia views de equipe também cria). Default-allow sem RBAC.
  const canCreateViews = (api.services.hasPermission
    ? api.services.hasPermission(currentUser, 'plugin.protocolos.create_views') : true) || canTeam;
  // Filtros disponíveis na aba ativa (available_filters da view; null/undefined = todos).
  // Gate da barra de filtros ao vivo. Chaves: status|atendente|q|periodo|attr:<key>.
  const availArr = (activeView && Array.isArray(activeView.available_filters)) ? activeView.available_filters : null;
  const availFilter = (key) => !availArr || availArr.includes(key);

  const dragRef = useRef(null);     // protocolo sendo arrastado
  const draggedRef = useRef(false); // distingue clique de arrasto
  const [dropCol, setDropCol] = useState(null);
  // Reorder de COLUNAS (arrastar o título): ref separado do card p/ não colidir. A ordem só
  // é aplicada localmente (colOrderDraft) — persiste no "Salvar visualização pessoal/equipe".
  const colDragRef = useRef(null);              // id da coluna sendo arrastada
  const [colOrderDraft, setColOrderDraft] = useState(null);  // null = usar a ordem salva
  const [colDropTarget, setColDropTarget] = useState(null);  // realce da coluna alvo

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
      const [dd, cd, ca, co, ll] = await Promise.all([
        getJson(`${apiBase}/field-defs?scope=protocolo`),
        getJson(`${apiBase}/field-defs?scope=atendimento`),
        getJson('/api/custom-attributes?applies_to=conversation'),  // atributos de CONVERSA (só p/ o detalhe)
        getJson('/api/custom-attributes?applies_to=contact'),       // atributos de CONTATO (filtros)
        getJson(`${apiBase}/protocolos?${params.toString()}`),
      ]);
      // TODAS as defs do protocolo (OBS fixo + extras, com `required`) — alimentam o
      // form editável do detalhe e o gate de obrigatórios (drag p/ "Fechado"). Sem filtrar.
      setCols((dd && dd.ok && dd.data && dd.data.defs) || []);
      const atendAll = (cd && cd.ok && cd.data && cd.data.defs) || [];
      setAtendDefs(atendAll.filter((d) => !d.fixed));
      setAtendResolveDefs(atendAll.filter((d) => !d.readonly)); // p/ o popup "Resolver atendimento"
      // Atributo de CONVERSA (is_system=0) — mantido SÓ p/ exibição no detalhe (não filtra/agrupa).
      setCoreAttrDefs(((ca && ca.ok && ca.data) || [])
        .filter((d) => !d.is_system)
        .map((d) => ({ key: d.attribute_key, label: d.display_name || d.attribute_key, type: d.type,
                       options: Array.isArray(d.options) ? d.options : [] })));
      // Atributo de CONTATO tipo list (is_system=0) — alimenta os FILTROS do Kanban.
      setContactAttrDefs(((co && co.ok && co.data) || [])
        .filter((d) => !d.is_system && d.type === 'list')
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

  // Aplica um dict de filtros pré-determinados na BARRA ao vivo. Só aplica os tipos
  // DISPONÍVEIS na aba (availFilter); um tipo indisponível nunca constrange silenciosamente.
  // Data: preset relativo vira intervalo REAL; 'tudo' limpa; from/to literais respeitados;
  // sem date → default (hoje→frente). Reusado pela troca de aba e pelo toggle Pessoal/Equipe.
  const applyFilterDict = (f) => {
    f = f || {};
    setStatus(availFilter('status') && f.status != null ? f.status : '');
    setQ(availFilter('q') && f.q != null ? f.q : '');
    setAssigneeFilter(availFilter('atendente') && f.assignee_user_id != null ? f.assignee_user_id : null);
    setAttrFilters(f.attrs && typeof f.attrs === 'object'
      ? Object.fromEntries(Object.entries(f.attrs).filter(([k]) => availFilter(k))) : {});
    const df = (availFilter('periodo') && f.date && typeof f.date === 'object') ? f.date : null;
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
  };

  // Aplica os filtros pré-determinados da aba UMA vez por troca de aba (defaults de
  // entrada, não travas). appliedViewRef evita reaplicar em reloads (WS) e só dispara
  // quando a view alvo já está resolvida (built-in sempre; custom após carregar).
  useEffect(() => {
    const v = activeView;
    const resolved = v && String(v.id) === String(activeViewId);
    if (!resolved || appliedViewRef.current === String(activeViewId)) return;
    appliedViewRef.current = String(activeViewId);
    // Origem dos filtros: a preferência do usuário (pessoal x equipe) por aba. Default =
    // filtros da EQUIPE (coluna filters compartilhada). Fallback de render não tem pref/filters.
    const usePersonal = v.pref && v.pref.use_personal;
    applyFilterDict((usePersonal ? (v.pref.personal_filters || {}) : (v.filters || {})) || {});
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

  // Monta um dict de filtros a partir da BARRA ao vivo (o que o usuário vê agora), só com os
  // tipos DISPONÍVEIS na aba. Data: preset relativo/'tudo' preservados; intervalo manual vira
  // from/to literais; o default "hoje→frente" (sem ajuste) é omitido (= sem filtro de data).
  const buildCurrentFilters = () => {
    const f = {};
    if (availFilter('status') && status) f.status = status;
    if (availFilter('q') && q.trim()) f.q = q.trim();
    if (availFilter('atendente') && assigneeFilter != null) f.assignee_user_id = assigneeFilter;
    if (availFilter('periodo')) {
      if (datePreset && datePreset !== 'tudo') f.date = { preset: datePreset, from: '', to: '' };
      else if (datePreset === 'tudo') f.date = { preset: 'tudo', from: '', to: '' };
      else if ((dateFrom && dateFrom !== ymd(new Date())) || dateTo) f.date = { preset: '', from: dateFrom || '', to: dateTo || '' };
    }
    const attrs = {};
    for (const [k, v] of Object.entries(attrFilters || {})) if (availFilter(k) && v != null && v !== '') attrs[k] = v;
    if (Object.keys(attrs).length) f.attrs = attrs;
    return f;
  };

  // Toggle Pessoal/Equipe (parte de cima dos filtros): aplica na barra o conjunto escolhido e
  // persiste a preferência do usuário (my-pref) sem mexer nos VALORES salvos. appliedViewRef é
  // marcado p/ o effect de entrada não re-aplicar por cima.
  const switchSource = async (toPersonal) => {
    const v = activeView;
    if (!v || v.id == null || v.id === FALLBACK_VIEW.id) return;
    setPrefMsg('');
    setColOrderDraft(null);  // a ordem exibida passa a refletir a origem escolhida
    appliedViewRef.current = String(activeViewId);
    applyFilterDict(toPersonal ? ((v.pref && v.pref.personal_filters) || {}) : (v.filters || {}));
    setViews((vs) => vs.map((x) => String(x.id) === String(v.id)
      ? { ...x, pref: { ...(x.pref || {}), use_personal: toPersonal } } : x));
    try {
      await fetch(`${apiBase}/kanban-views/${v.id}/my-pref`, {
        method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_personal: toPersonal }),
      });
    } catch (_) { /* silencioso: a barra já reflete a escolha */ }
  };

  // Salva os filtros ATUAIS da barra como padrão PESSOAL desta aba (my-pref) e marca a origem
  // como pessoal. Não re-aplica (a barra já mostra o que foi salvo).
  const savePersonalFilters = async () => {
    const v = activeView;
    if (!v || v.id == null || v.id === FALLBACK_VIEW.id) return;
    setSavingPref(true); setPrefMsg('');
    const f = buildCurrentFilters();
    const co = currentOrderIds();  // ordem das colunas atual (visível) → parte da visualização
    try {
      const r = await fetch(`${apiBase}/kanban-views/${v.id}/my-pref`, {
        method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_personal: true, personal_filters: f, personal_column_order: co }),
      });
      const j = await r.json().catch(() => ({}));
      setSavingPref(false);
      if (!(j && j.ok)) { setPrefMsg('Falha ao salvar visualização pessoal.'); return; }
      setPrefMsg('Visualização pessoal salva.');
      appliedViewRef.current = String(activeViewId);
      setColOrderDraft(null);
      setViews((vs) => vs.map((x) => String(x.id) === String(v.id)
        ? { ...x, pref: { use_personal: true, personal_filters: f, personal_column_order: co } } : x));
    } catch (_) { setSavingPref(false); setPrefMsg('Falha ao salvar visualização pessoal.'); }
  };

  // Salva os filtros ATUAIS da barra como padrão da EQUIPE (kanban-views.filters). Só para
  // quem pode editar a visualização; available_filters é omitido → o backend preserva.
  const saveTeamFilters = async () => {
    const v = activeView;
    if (!v || v.id == null || v.id === FALLBACK_VIEW.id) return;
    setSavingPref(true); setPrefMsg('');
    const f = buildCurrentFilters();
    const co = currentOrderIds();  // ordem das colunas atual (visível) → parte da visualização
    try {
      const r = await fetch(`${apiBase}/kanban-views/${v.id}`, {
        method: 'PUT', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: f, column_order: co }),
      });
      const j = await r.json().catch(() => ({}));
      setSavingPref(false);
      if (!(j && j.ok)) { setPrefMsg((j && j.error) || 'Falha ao salvar visualização da equipe.'); return; }
      setPrefMsg('Visualização da equipe salva.');
      appliedViewRef.current = String(activeViewId);  // barra já reflete; não re-aplicar
      setColOrderDraft(null);
      setViews((vs) => vs.map((x) => String(x.id) === String(v.id) ? { ...x, filters: f, column_order: co } : x));
    } catch (_) { setSavingPref(false); setPrefMsg('Falha ao salvar visualização da equipe.'); }
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
    () => buildGrouping(activeView, { users, groupFields, rows, apiPost }),
    [activeView, users, groupFields, rows, apiPost]);

  // Ordem das colunas salva desta aba: PESSOAL (pref) se a origem for pessoal, senão da EQUIPE
  // (view.column_order). Ambas podem ser null → ordem padrão de buildGrouping.
  const storedColumnOrder = (v) => {
    if (!v) return null;
    return (v.pref && v.pref.use_personal)
      ? (v.pref.personal_column_order || null) : (v.column_order || null);
  };
  // Colunas efetivamente exibidas: rascunho local (se o usuário arrastou) tem precedência
  // sobre a ordem salva. Reaplica sobre as colunas ATUAIS (dinâmicas continuam funcionando).
  const orderedColumns = useMemo(
    () => applyColumnOrder(grouping.columns, colOrderDraft || storedColumnOrder(activeView)),
    [grouping, colOrderDraft, activeView]);
  // Troca de aba OU de origem (Pessoal/Equipe) → descarta o rascunho, refletindo o que está salvo.
  useEffect(() => { setColOrderDraft(null); },
    [activeViewId, !!(activeView && activeView.pref && activeView.pref.use_personal)]);
  // Move a coluna `fromId` para a posição da `toId` (só local; persiste no Salvar).
  const reorderColumns = (fromId, toId) => {
    if (fromId === toId) return;
    const ids = orderedColumns.map((c) => c.id);
    const from = ids.indexOf(fromId); const to = ids.indexOf(toId);
    if (from < 0 || to < 0) return;
    ids.splice(from, 1); ids.splice(to, 0, fromId);
    setColOrderDraft(ids);
  };
  const currentOrderIds = () => orderedColumns.map((c) => c.id);  // ordem visível p/ salvar

  // Abre o editor de visualização (criar quando view=null). NÃO troca de aba sozinho —
  // o usuário só sai da aba atual pelo "✕" dela ou clicando noutra aba (req. do usuário).
  // Uma aba nova aparece na barra; editar a aba ATIVA re-aplica seus filtros (reset do ref).
  const openViewEditor = useCallback(async (view) => {
    const saved = await api.ui.openModal((close) => html`<${ViewEditorModal}
      view=${view} groupFields=${groupFields} users=${users} roles=${roles} canTeam=${canTeam}
      currentUser=${currentUser} api=${api}
      onSaved=${(v) => close(v)} onCancel=${() => close(null)} />`);
    if (saved) {
      if (view && String(view.id) === String(activeViewId)) appliedViewRef.current = null;
      await loadViews();
    }
  }, [api, groupFields, users, roles, canTeam, currentUser, loadViews, activeViewId]);

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
        ${canCreateViews ? html`<button title="Novo agrupamento" onClick=${() => openViewEditor(null)}
          class="px-2.5 py-1 text-[12px] rounded-lg border border-dashed border-wa-border text-wa-text hover:bg-wa-hover">+ Nova</button>` : null}
      </div>
      ${mode === 'kanban' && grouping.onDrop ? html`<span class="text-[11px] text-wa-secondary">Arraste um card para outra coluna (pede confirmação).</span>` : null}
      ${mode === 'kanban' ? html`<span class="text-[11px] text-wa-secondary">Arraste o título de uma coluna para reordenar (salve para manter).</span>` : null}
      ${mode === 'kanban' && grouping.unavailable ? html`<span class="text-[11px] text-amber-600">Atributo da visualização indisponível (foi removido).</span>` : null}
    </div>`;

  const kanbanView = html`
    <div>
      ${actionMsg ? html`<div class="mb-2 px-3 py-2 rounded-md text-[13px] ${actionMsg.error ? 'bg-red-500/15 border border-red-500 text-red-500 font-semibold' : 'text-wa-secondary'}">${actionMsg.text}</div>` : null}
      <div class="flex gap-3 overflow-x-auto pb-2">
        ${orderedColumns.map((col) => {
          const cards = rows.filter((r) => grouping.columnIdOf(r) === col.id);
          const isTarget = dropCol === col.id;
          const isColTarget = colDropTarget === col.id;
          return html`
            <div key=${col.id} class="flex flex-col w-72 shrink-0">
              <div draggable=${true}
                onDragStart=${(e) => { colDragRef.current = col.id; try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(col.id)); } catch (_) { /* ignore */ } }}
                onDragEnd=${() => { colDragRef.current = null; setColDropTarget(null); }}
                onDragOver=${(e) => { if (!colDragRef.current) return; e.preventDefault(); if (colDropTarget !== col.id) setColDropTarget(col.id); }}
                onDragLeave=${() => setColDropTarget((d) => (d === col.id ? null : d))}
                onDrop=${(e) => { if (!colDragRef.current) return; e.preventDefault(); reorderColumns(colDragRef.current, col.id); colDragRef.current = null; setColDropTarget(null); }}
                title="Arraste para reordenar as colunas"
                class="flex items-center justify-between mb-2 px-1 py-0.5 rounded cursor-move select-none ${isColTarget ? 'ring-2 ring-wa-teal bg-wa-hover' : ''}">
                <span class="text-[13px] font-semibold text-wa-text truncate">${col.label}</span>
                <span class="text-[12px] text-wa-secondary">${cards.length}</span>
              </div>
              <div
                onDragOver=${(e) => { if (colDragRef.current) return; e.preventDefault(); if (dropCol !== col.id) setDropCol(col.id); }}
                onDragLeave=${() => setDropCol((d) => (d === col.id ? null : d))}
                onDrop=${(e) => { if (colDragRef.current) return; e.preventDefault(); const row = dragRef.current; setDropCol(null); applyDrop(row, col.id); }}
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
      <!-- Config do AGRUPAMENTO (quais TIPOS de filtro existem nesta aba) — painel recolhível,
           só para quem pode editar a visualização. -->
      ${activeView && activeView.id != null && activeView.id !== FALLBACK_VIEW.id && canEditView(activeView) ? html`
        <${ViewGroupingConfig} key=${activeView.id}
          view=${activeView} filterFields=${filterFields} contactAttrDefs=${contactAttrDefs}
          apiBase=${apiBase} authHeaders=${authHeaders}
          onSaved=${() => { appliedViewRef.current = null; loadViews(); }} />` : null}
      <!-- Origem (Pessoal/Equipe) + salvar a VISUALIZAÇÃO atual (filtros da barra + ordem das
           colunas) como padrão da aba. "Salvar visualização equipe" só para quem pode editar. -->
      ${activeView && activeView.id != null && activeView.id !== FALLBACK_VIEW.id ? html`
        <div class="flex flex-wrap items-center gap-3 mb-3">
          <span class="text-[12px] text-wa-secondary">Visualização do agrupamento:</span>
          <div class="inline-flex items-center gap-3 text-[13px] text-wa-text">
            <label class="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="radio" name=${`src-${activeView.id}`} checked=${!!(activeView.pref && activeView.pref.use_personal)} onChange=${() => switchSource(true)} /> Pessoal
            </label>
            <label class="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="radio" name=${`src-${activeView.id}`} checked=${!(activeView.pref && activeView.pref.use_personal)} onChange=${() => switchSource(false)} /> Equipe
            </label>
          </div>
          <button onClick=${savePersonalFilters} disabled=${savingPref}
            class="px-3 py-1.5 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-50">Salvar visualização pessoal</button>
          ${canEditView(activeView) ? html`<button onClick=${saveTeamFilters} disabled=${savingPref}
            class="px-3 py-1.5 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-50">Salvar visualização equipe</button>` : null}
          ${prefMsg ? html`<span class="text-[12px] ${prefMsg.includes('Falha') ? 'text-red-500' : 'text-wa-teal'}">${prefMsg}</span>` : null}
        </div>` : null}
      <!-- Filtros principais (Kanban/Lista alternam no topo, ao lado do título) -->
      <div class="flex flex-wrap items-end gap-3 mb-3 p-3 rounded-lg bg-wa-panel border border-wa-border">
        ${availFilter('status') ? html`
        <div class="w-[150px]">
          <label class="block text-[12px] text-wa-secondary mb-1">Status</label>
          <${OptionListSelect} options=${[{ value: 'aberto', label: 'Aberto' }, { value: 'fechado', label: 'Fechado' }]}
            value=${status} onChange=${(v) => setStatus(v)} placeholder="Todos" float=${true} />
        </div>` : null}
        ${availFilter('atendente') ? html`
        <div class="w-[170px]">
          <label class="block text-[12px] text-wa-secondary mb-1">Atendente</label>
          <${OptionListSelect}
            options=${users.map((u) => ({ value: String(u.id), label: u.name || `Usuário #${u.id}` }))}
            value=${assigneeFilter == null ? '' : String(assigneeFilter)}
            onChange=${(v) => setAssigneeFilter(v === '' ? null : +v)} placeholder="Todos" float=${true} />
        </div>` : null}
        ${[...filterFields.map((d) => ({ fk: `pf:${d.scope}:${d.key}`, label: d.label, options: d.options })),
           ...contactAttrDefs.map((d) => ({ fk: `cattr:${d.key}`, label: d.label, options: d.options }))]
          .filter((d) => availFilter(d.fk)).map((d) => html`
          <div key=${d.fk} class="w-[180px]">
            <label class="block text-[12px] text-wa-secondary mb-1 truncate" title=${d.label}>${d.label}</label>
            <${OptionListSelect} options=${d.options || []} value=${attrFilters[d.fk] || ''}
              onChange=${(v) => setAttrFilters((s) => { const n = { ...s }; if (v) n[d.fk] = v; else delete n[d.fk]; return n; })}
              placeholder="Todos" float=${true} />
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
      else if (isMultiDef(d)) init[d.key] = Array.isArray(cur)
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

// Painel recolhível (acima da barra, no Kanban e na Lista) com a configuração GERAL do
// AGRUPAMENTO: quais TIPOS de filtro existem nesta aba (available_filters da view). Só é
// renderizado para quem pode editar a visualização. Os VALORES pré-determinados NÃO vivem
// mais aqui — são salvos a partir dos filtros ATUAIS da barra pelos botões "Salvar filtros
// pessoal/equipe" acima da barra. Montar com key=${view.id} reinicializa o estado por aba.
function ViewGroupingConfig({ view, filterFields, contactAttrDefs, apiBase, authHeaders, onSaved }) {
  // Filtros por CAMPO DE PROTOCOLO (pf:<scope>:<key>) + ATRIBUTO DE CONTATO (cattr:<key>).
  const attrFilterKeys = [
    ...(filterFields || []).map((d) => [`pf:${d.scope}:${d.key}`, d.label]),
    ...(contactAttrDefs || []).map((d) => [`cattr:${d.key}`, d.label]),
  ];
  // Tipos de filtro que podem existir na aba. null na view = TODOS (à prova de futuro).
  const ALL_FILTER_KEYS = ['status', 'atendente', 'q', 'periodo', ...attrFilterKeys.map(([k]) => k)];
  const initAvail = (view && Array.isArray(view.available_filters)) ? view.available_filters : null;
  const [open, setOpen] = useState(false);
  const [availSet, setAvailSet] = useState(() => new Set(initAvail == null ? ALL_FILTER_KEYS : initAvail));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const [okMsg, setOkMsg] = useState('');
  const isAvail = (key) => availSet.has(key);
  const toggleAvail = (key) => { setOkMsg(''); setAvailSet((s) => {
    const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n;
  }); };

  const save = async () => {
    setErr(''); setOkMsg(''); setSaving(true);
    try {
      // available_filters: todos habilitados → null (todos, à prova de futuro); senão a lista.
      // O body NÃO leva filters → o backend preserva os VALORES pré-determinados da equipe.
      const enabled = ALL_FILTER_KEYS.filter((k) => availSet.has(k));
      const available_filters = enabled.length === ALL_FILTER_KEYS.length ? null : enabled;
      const r = await fetch(`${apiBase}/kanban-views/${view.id}`, {
        method: 'PUT',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ available_filters }),
      });
      const j = await r.json().catch(() => ({}));
      if (!(j && j.ok)) { setErr((j && j.error) || 'Falha ao salvar.'); setSaving(false); return; }
      setSaving(false); setOkMsg('Configuração salva.');
      onSaved && onSaved();
    } catch (_) { setErr('Falha ao salvar.'); setSaving(false); }
  };

  return html`
    <div class="mb-3 rounded-lg bg-wa-panel border border-wa-border">
      <button type="button" onClick=${() => setOpen((o) => !o)}
        class="w-full flex items-center gap-2 px-3 py-2 text-[13px] text-wa-text hover:bg-wa-hover rounded-lg">
        <span class="text-wa-secondary w-3">${open ? '▾' : '▸'}</span>
        <span>⚙ Configurar filtros da aba</span>
      </button>
      ${open ? html`
      <div class="px-3 pb-3 pt-3 border-t border-wa-border">
        <span class="block text-[12px] text-wa-secondary font-medium mb-1">Filtros disponíveis nesta aba</span>
        <div class="text-[11px] text-wa-secondary mb-2">Só os tipos de filtro marcados aparecem na barra de filtros deste agrupamento.</div>
        <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-wa-text">
          ${[['status', 'Status'], ['atendente', 'Atendente'], ['q', 'Buscar'], ['periodo', 'Período'],
             ...attrFilterKeys].map(([key, lbl]) => html`
            <label key=${key} class="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked=${isAvail(key)} onChange=${() => toggleAvail(key)} /> ${lbl}
            </label>`)}
        </div>
        ${err ? html`<div class="mt-3 px-3 py-2 rounded-md text-[13px] bg-red-500/15 border border-red-500 text-red-500 font-semibold">${err}</div>` : null}
        <div class="flex items-center justify-end gap-3 mt-3">
          ${okMsg ? html`<span class="text-[12px] text-wa-teal">${okMsg}</span>` : null}
          <button onClick=${save} disabled=${saving}
            class="px-4 py-2 rounded-lg bg-wa-teal text-white hover:opacity-90 disabled:opacity-50 text-[13px] font-medium">${saving ? 'Salvando…' : 'Salvar configuração da aba'}</button>
        </div>
      </div>` : null}
    </div>`;
}

// Editor de visualização (criar/editar uma aba de "Agrupar por"). Cuida só dos METADADOS:
// Nome, Quem pode ver (ACL de grupos/usuários) e Agrupar por (nativos + atributos lista).
// Os TIPOS de filtro da aba saíram para o painel "Configurar filtros da aba"
// (ViewGroupingConfig, acima da barra), e os VALORES pré-determinados para os botões
// "Salvar visualização pessoal/equipe". Salva via POST/PUT /kanban-views SEM tocar em
// filters/available_filters (o backend preserva no update e usa defaults no create).
function ViewEditorModal({ view, groupFields, users, roles, canTeam, currentUser, api, onSaved, onCancel }) {
  const editing = !!(view && view.id != null);
  // Pode editar os METADADOS + filtros de equipe? Criar (view=null) sempre pode (vira pessoal).
  const canEditMeta = !view ? true
    : (canTeam || (view.scope === 'personal'
        && (!currentUser || view.owner_user_id === currentUser.id)));
  const protoGroupFields = (groupFields || []).filter((d) => d.scope === 'protocolo');
  const atendGroupFields = (groupFields || []).filter((d) => d.scope === 'atendimento');
  const initGroup = () => {
    if (!view) return 'status';
    if (view.group_by === 'pfield') return `pfield:${view.group_field_scope || ''}:${view.group_attr_key || ''}`;
    if (view.group_by === 'attr') return 'status';  // legado (atributo de conversa) → cai em Status
    return view.group_by;
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
    const isPf = groupSel.startsWith('pfield:');
    const pfParts = isPf ? groupSel.split(':') : [];   // ['pfield', scope, key]
    const gb = isPf ? 'pfield' : groupSel;
    const gfs = isPf ? (pfParts[1] || null) : null;
    const gak = isPf ? (pfParts[2] || null) : null;
    const acl = aclBody();  // { scope derivado, visibility_roles, users_include, users_exclude }
    if (!name.trim()) { setErr('Informe um nome para a visualização.'); return; }
    if (gb === 'pfield' && (!gfs || !gak)) { setErr('Selecione um campo de opção para agrupar.'); return; }
    if (acl.scope === 'team' && !canTeam) { setErr('Sem permissão para compartilhar (visualização de equipe).'); return; }
    setSaving(true);
    try {
      // Só METADADOS. O body NÃO leva filters/available_filters → o backend preserva no
      // update e usa defaults ({}/null=todos) no create. Os TIPOS de filtro vivem no painel
      // "Configurar filtros da aba" (ViewGroupingConfig) e os VALORES nos botões "Salvar
      // filtros pessoal/equipe", acima da barra de filtros.
      const body = {
        name: name.trim(), scope: acl.scope, group_by: gb,
        group_attr_key: gak, group_field_scope: gfs,
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
  const title = editing ? 'Editar visualização' : 'Novo agrupamento';
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
          ${protoGroupFields.length ? html`<optgroup label="Campos do protocolo">
            ${protoGroupFields.map((d) => html`<option key=${`protocolo:${d.key}`} value=${`pfield:protocolo:${d.key}`}>${d.label}</option>`)}
          </optgroup>` : null}
          ${atendGroupFields.length ? html`<optgroup label="Campos de resolver atendimento">
            ${atendGroupFields.map((d) => html`<option key=${`atendimento:${d.key}`} value=${`pfield:atendimento:${d.key}`}>${d.label}</option>`)}
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
