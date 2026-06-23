// Tela "Atendimentos" (container). Toggle Lista|Kanban; o kanban agrupa de forma
// configurável (atendente / etapa / etiqueta / status). Reaproveita as funções de
// conversa do api.js e os eventos WS já existentes; o drag-and-drop é otimista
// com rollback. Abrir um card navega para o chat (/conversations/<id>).
import { h } from 'preact';
import { useEffect, useState, useCallback, useMemo, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  filterConversations, setConversationStatus, assignConversation, assignAgent,
  archiveConversation, updateConversationInfo, updateConversationLabels,
  getConversationLabels, getConversationLabelsFor, getAssignableAgents,
  getCustomAttributes, getMe,
} from '../../services/api.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';
import { hasPermission } from '../../utils/permissions.js';
import { buildGrouping } from './grouping.js';
import { GroupBySelector } from './GroupBySelector.js';
import { AttendanceBoard } from './AttendanceBoard.js';
import { AttendanceList } from './AttendanceList.js';

const html = htm.bind(h);

const VIEW_KEY = 'whatsbot_attendances_view';
const MODE_KEY = 'whatsbot_attendances_mode';
const STAGE_KEY = 'whatsbot_attendances_stage_attr';

function lsGet(k, def) { try { return localStorage.getItem(k) || def; } catch (e) { return def; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }

export function Attendances() {
  const [view, setView] = useState(() => lsGet(VIEW_KEY, 'board'));   // 'board' | 'list'
  const [mode, setMode] = useState(() => lsGet(MODE_KEY, 'assignee'));
  const [onlyOpen, setOnlyOpen] = useState(false);

  const [conversations, setConversations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [currentUser, setCurrentUser] = useState(null);
  const [agents, setAgents] = useState({ users: [], ai_agents: [] });
  const [labels, setLabels] = useState([]);          // [{id,name,color}]
  const [stageAttrs, setStageAttrs] = useState([]);  // atributos de conversa type=list
  const [stageAttrKey, setStageAttrKey] = useState(() => lsGet(STAGE_KEY, ''));

  // label_names por conversa (hidratado sob demanda no modo etiqueta).
  const [labelsByConv, setLabelsByConv] = useState({});

  // ── Lookups derivados ────────────────────────────────────────────
  const userNameById = useMemo(() => {
    const m = new Map();
    for (const u of agents.users) m.set(u.id, u.name || `Usuário #${u.id}`);
    return m;
  }, [agents]);
  const agentNameByKey = useMemo(() => {
    const m = new Map();
    for (const a of agents.ai_agents) m.set(a.agent_key, a.display_name || a.agent_key);
    return m;
  }, [agents]);
  const labelColorByName = useMemo(() => {
    const m = new Map();
    for (const l of labels) m.set(l.name, l.color);
    return m;
  }, [labels]);

  const currentUserId = currentUser && currentUser.id != null ? currentUser.id : null;

  const assigneeNameOf = useCallback((c) => {
    if (c.assignee_user_id != null) return userNameById.get(c.assignee_user_id) || `Usuário #${c.assignee_user_id}`;
    if (c.active_agent_key) return `${agentNameByKey.get(c.active_agent_key) || c.active_agent_key} (IA)`;
    return null;
  }, [userNameById, agentNameByKey]);

  const labelsOf = useCallback((c) => {
    const names = c.label_names || labelsByConv[c.id] || [];
    return names.map(n => ({ name: n, color: labelColorByName.get(n) }));
  }, [labelsByConv, labelColorByName]);

  const showChannel = useMemo(() => {
    const seen = new Set();
    for (const c of conversations) if (c.channel_provider) seen.add(c.channel_provider);
    return seen.size > 1;
  }, [conversations]);

  const stageAttrDef = useMemo(
    () => stageAttrs.find(a => a.attribute_key === stageAttrKey) || stageAttrs[0] || null,
    [stageAttrs, stageAttrKey],
  );

  // ── Carregamentos auxiliares (1×) ────────────────────────────────
  useEffect(() => {
    getMe().then(res => { if (res && res.ok && res.data && res.data.user) setCurrentUser(res.data.user); }).catch(() => {});
    getAssignableAgents().then(res => { if (res && res.ok && res.data) setAgents({ users: res.data.users || [], ai_agents: res.data.ai_agents || [] }); }).catch(() => {});
    getConversationLabels().then(res => { if (res && res.ok && Array.isArray(res.data)) setLabels(res.data); }).catch(() => {});
    getCustomAttributes('conversation').then(res => {
      const defs = (res && res.ok && Array.isArray(res.data)) ? res.data.filter(d => d.type === 'list') : [];
      setStageAttrs(defs);
      if (defs.length && !defs.find(d => d.attribute_key === stageAttrKey)) setStageAttrKey(defs[0].attribute_key);
    }).catch(() => {});
  }, []);

  // ── Fetch das conversas ──────────────────────────────────────────
  const fetchConversations = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const params = { archived: 'false', limit: 200 };
      if (onlyOpen) params.status = 'open';
      const res = await filterConversations(params);
      if (res && res.ok) setConversations((res.data && res.data.conversations) || []);
      else { setError((res && res.error) || 'Falha ao carregar atendimentos.'); setConversations([]); }
    } catch (e) {
      setError('Falha ao carregar atendimentos.'); setConversations([]);
    } finally { setLoading(false); }
  }, [onlyOpen]);

  useEffect(() => { fetchConversations(); }, [fetchConversations]);

  // ── Hidratar label_names quando o modo etiqueta está ativo ───────
  useEffect(() => {
    if (mode !== 'label' || !conversations.length) return;
    let alive = true;
    const missing = conversations.filter(c => c.label_names == null && labelsByConv[c.id] == null);
    if (!missing.length) return;
    Promise.all(missing.map(c => getConversationLabelsFor(c.id).then(r => [c.id, (r && r.ok && r.data && r.data.labels) ? r.data.labels.map(l => l.name) : []]).catch(() => [c.id, []])))
      .then(pairs => { if (!alive) return; setLabelsByConv(prev => { const next = { ...prev }; for (const [id, names] of pairs) next[id] = names; return next; }); });
    return () => { alive = false; };
  }, [mode, conversations, labelsByConv]);

  // Mescla label_names hidratados nas conversas (para grouping.columnIdOf e chips).
  const conversationsForBoard = useMemo(() => {
    if (mode !== 'label') return conversations;
    return conversations.map(c => c.label_names != null ? c : { ...c, label_names: labelsByConv[c.id] || [] });
  }, [conversations, mode, labelsByConv]);

  // ── Ações de API (injetadas no grouping) ─────────────────────────
  const actions = useMemo(() => ({
    assignAgent: (id, opts) => assignAgent(id, opts),
    setStatus: (id, status) => setConversationStatus(id, status),
    replaceLabels: (id, names) => updateConversationLabels(id, names),
    // Mescla o atributo no JSON existente (o endpoint substitui o objeto inteiro).
    setStage: (conv, key, value) => {
      const next = { ...(conv.custom_attributes || {}) };
      if (value == null) delete next[key]; else next[key] = value;
      return updateConversationInfo(conv.id, { custom_attributes: next });
    },
  }), []);

  const grouping = useMemo(
    () => buildGrouping(mode, { agents, labels, attributeDef: stageAttrDef, actions }),
    [mode, agents, labels, stageAttrDef, actions],
  );

  const canDrag = hasPermission(currentUser, grouping.requiredPerm);

  // ── Drag-and-drop: otimista + rollback ───────────────────────────
  const [pendingIds, setPendingIds] = useState(() => new Set());

  const applyOptimistic = useCallback((conv, colId) => {
    const patch = grouping.patchFor ? grouping.patchFor(colId, conv) : {};
    setConversations(prev => prev.map(c => {
      if (c.id !== conv.id) return c;
      const next = { ...c };
      if (patch.assignee_user_id !== undefined) next.assignee_user_id = patch.assignee_user_id;
      if (patch.active_agent_key !== undefined) next.active_agent_key = patch.active_agent_key;
      if (patch.ai_active !== undefined) next.ai_active = patch.ai_active;
      if (patch.status !== undefined) next.status = patch.status;
      if (patch.label_names !== undefined) next.label_names = patch.label_names;
      if (patch.custom_attributes_patch) {
        const ca = { ...(c.custom_attributes || {}) };
        for (const [k, v] of Object.entries(patch.custom_attributes_patch)) { if (v == null) delete ca[k]; else ca[k] = v; }
        next.custom_attributes = ca;
      }
      return next;
    }));
    if (patch.label_names !== undefined) setLabelsByConv(prev => ({ ...prev, [conv.id]: patch.label_names }));
  }, [grouping]);

  const handleCardDrop = useCallback(async (convId, colId) => {
    const conv = conversationsForBoard.find(c => c.id === convId);
    if (!conv) return;
    if (grouping.columnIdOf(conv) === colId) return; // já está na coluna
    const prevConv = conversations.find(c => c.id === convId);
    applyOptimistic(conv, colId);
    setPendingIds(prev => { const s = new Set(prev); s.add(convId); return s; });
    try {
      const res = await grouping.onDrop(conv, colId);
      if (res && res.ok === false) throw new Error(res.error || 'Falha ao mover.');
    } catch (e) {
      // rollback
      if (prevConv) setConversations(prev => prev.map(c => c.id === convId ? prevConv : c));
      setError((e && e.message) || 'Falha ao mover o atendimento.');
    } finally {
      setPendingIds(prev => { const s = new Set(prev); s.delete(convId); return s; });
    }
  }, [conversationsForBoard, conversations, grouping, applyOptimistic]);

  // ── Lista: ações inline (status/assign/archive) ──────────────────
  const handleAction = useCallback(async (convo, kind, value) => {
    try {
      let res;
      if (kind === 'status') res = await setConversationStatus(convo.id, value);
      else if (kind === 'assign') res = await assignConversation(convo.id, value);
      else if (kind === 'archive') res = await archiveConversation(convo.id, value);
      if (res && res.ok === false) { setError(res.error || 'Falha na ação.'); return; }
    } catch (e) { setError('Falha na ação.'); return; }
    await fetchConversations();
  }, [fetchConversations]);

  // ── Navegação: abrir o chat ──────────────────────────────────────
  const openChat = useCallback((convo) => {
    if (convo.id != null) history.pushState(null, '', `/conversations/${convo.id}`);
    else if (convo.contact_id != null) history.pushState(null, '', `/contacts/${convo.contact_id}`);
    else return;
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, []);

  // ── Tempo real (WS) ──────────────────────────────────────────────
  const fetchRef = useRef(fetchConversations);
  useEffect(() => { fetchRef.current = fetchConversations; }, [fetchConversations]);
  const debounceRef = useRef(null);

  const onConversationChanged = useCallback((name, data) => {
    const id = data && data.conversation_id;
    if (id != null) {
      if (name === 'conversation_labels_changed') {
        const names = Array.isArray(data.labels) ? data.labels : [];
        setConversations(prev => prev.map(c => c.id === id ? { ...c, label_names: names } : c));
        setLabelsByConv(prev => ({ ...prev, [id]: names }));
      } else {
        setConversations(prev => prev.map(c => {
          if (c.id !== id) return c;
          const patch = {};
          if (data.status !== undefined) patch.status = data.status;
          if (data.assignee_user_id !== undefined) patch.assignee_user_id = data.assignee_user_id;
          if (data.active_agent_key !== undefined) patch.active_agent_key = data.active_agent_key;
          if (data.is_archived !== undefined) patch.is_archived = data.is_archived;
          if (data.ai_active !== undefined) patch.ai_active = data.ai_active;
          if (data.fields && data.fields.custom_attributes) patch.custom_attributes = data.fields.custom_attributes;
          if (data.fields && data.fields.active_agent_key !== undefined) patch.active_agent_key = data.fields.active_agent_key;
          return { ...c, ...patch };
        }));
      }
    }
    // Reconcilia membership/ordenação contra o filtro ativo (debounced).
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => { fetchRef.current(); }, 800);
  }, []);

  useWebSocket({ onConversationChanged });

  // Se o modo persistido for "etapa" mas não houver atributo lista, cai p/ atendente.
  useEffect(() => {
    if (mode === 'stage' && stageAttrs.length === 0) setMode('assignee');
  }, [mode, stageAttrs]);

  // ── Persistência das prefs ───────────────────────────────────────
  useEffect(() => { lsSet(VIEW_KEY, view); }, [view]);
  useEffect(() => { lsSet(MODE_KEY, mode); }, [mode]);
  useEffect(() => { if (stageAttrKey) lsSet(STAGE_KEY, stageAttrKey); }, [stageAttrKey]);

  // ── Render ───────────────────────────────────────────────────────
  const TabBtn = (id, label) => html`
    <button onClick=${() => setView(id)}
      class="px-3 py-1.5 text-[13px] rounded-md border transition-colors ${view === id
        ? 'bg-wa-teal text-white border-wa-teal'
        : 'bg-wa-panel text-wa-secondary border-wa-border hover:text-wa-text'}">${label}</button>`;

  return html`
    <div>
      <div class="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <div class="flex items-center gap-2">
          ${TabBtn('board', 'Kanban')}
          ${TabBtn('list', 'Lista')}
        </div>
        <div class="flex items-center gap-3 flex-wrap">
          ${view === 'board' ? html`<${GroupBySelector}
            mode=${mode} onMode=${setMode}
            stageAttrs=${stageAttrs} stageAttrKey=${stageAttrDef && stageAttrDef.attribute_key}
            onStageAttr=${setStageAttrKey} />` : null}
          <label class="flex items-center gap-1.5 text-[12px] text-wa-secondary cursor-pointer">
            <input type="checkbox" checked=${onlyOpen} onChange=${(e) => setOnlyOpen(e.target.checked)} />
            Só abertos
          </label>
          <button onClick=${fetchConversations}
            class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">Atualizar</button>
        </div>
      </div>

      ${view === 'board' && !canDrag ? html`
        <div class="mb-3 text-[12px] text-wa-secondary">Você não tem permissão para mover atendimentos neste agrupamento — visualização apenas.</div>
      ` : null}

      ${error ? html`<div class="mb-3 px-3 py-2 rounded-md bg-red-50 text-red-600 text-[13px] border border-red-200">${error}</div>` : null}

      ${loading
        ? html`<div class="text-center text-wa-secondary py-12 animate-pulse-slow">Carregando atendimentos...</div>`
        : view === 'board'
          ? html`<${AttendanceBoard}
              conversations=${conversationsForBoard} grouping=${grouping} canDrag=${canDrag}
              pendingIds=${pendingIds} assigneeNameOf=${assigneeNameOf} labelsOf=${labelsOf}
              showChannel=${showChannel} onOpenChat=${openChat} onCardDrop=${handleCardDrop} />`
          : html`<${AttendanceList}
              conversations=${conversationsForBoard} assigneeNameOf=${assigneeNameOf}
              currentUserId=${currentUserId} showChannel=${showChannel} labelsOf=${labelsOf}
              onOpenChat=${openChat} onAction=${handleAction} />`}
    </div>
  `;
}

export default Attendances;
