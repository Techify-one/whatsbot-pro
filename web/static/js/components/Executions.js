import { h } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import { getExecutions, getExecution, getConfig, saveConfig } from '../services/api.js';
import { useUrlState } from '../hooks/useUrlState.js';
import { readParams, writeParams, str, enumStr, int } from '../services/urlState.js';
import { CopyLinkButton } from '../utils/copyDeepLink.js';

const html = htm.bind(h);

// Deep-link do estado da tela (Plano 24). Duas camadas independentes que
// COMPÕEM sobre a mesma URL: o PATH (/executions ou /executions/{id}) já é
// versionado pelo pushState/popstate próprio do componente (não mexer nele);
// aqui só adicionamos a QUERY (useUrlState preserva o pathname).
//
// Lista (/executions): filtros + página. `page` vai 1-based na URL (legível);
// o estado interno é 0-based, então convertemos na hidratação/serialização.
// plano 36 F5: além de phone/status, filtramos por conversa (`conv`) e período
// (`from`/`to`, datas YYYY-MM-DD).
const LIST_URL_SCHEMA = [
  str('phone', ''),
  enumStr('status', ''),   // ''|completed|failed|running
  str('conv', ''),         // conversation_id (string; '' = sem filtro)
  str('from', ''),         // date_from YYYY-MM-DD
  str('to', ''),           // date_to YYYY-MM-DD
  int('page', 1),          // 1-based na URL; default 1 é omitido
];

const STEP_COLORS = {
  webhook_received: { bg: 'bg-wa-panel', text: 'text-wa-text', border: 'border-wa-border' },
  batch_accumulated: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-300' },
  media_processed: { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-300' },
  llm_request: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-300' },
  llm_context: { bg: 'bg-yellow-50', text: 'text-yellow-700', border: 'border-yellow-300' },
  llm_response: { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-300' },
  tool_executed: { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-300' },
  channel_send: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-300' },
  response_sent: { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-300' },
  routing_halted: { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-300' },
  error: { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-300' },
};

const STATUS_BADGES = {
  running: { bg: 'bg-yellow-100', text: 'text-yellow-800', label: 'Em execução' },
  completed: { bg: 'bg-green-100', text: 'text-green-800', label: 'Concluída' },
  failed: { bg: 'bg-red-100', text: 'text-red-800', label: 'Falhou' },
};

function formatTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDuration(ms) {
  if (ms == null) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}min`;
}

function formatRelativeTs(ts, baseTsMs) {
  const diffMs = Math.round((ts * 1000) - baseTsMs);
  if (diffMs <= 0) return '+0ms';
  if (diffMs < 1000) return `+${diffMs}ms`;
  return `+${(diffMs / 1000).toFixed(2)}s`;
}

function StepBadge({ type }) {
  const colors = STEP_COLORS[type] || STEP_COLORS.error;
  return html`<span class="inline-block px-2 py-0.5 text-xs font-medium rounded ${colors.bg} ${colors.text}">${type}</span>`;
}

function StatusBadge({ status }) {
  const badge = STATUS_BADGES[status] || STATUS_BADGES.failed;
  return html`<span class="inline-block px-2 py-0.5 text-xs font-medium rounded ${badge.bg} ${badge.text}">${badge.label}</span>`;
}

// ── Step-body building blocks (plano 36 F5) ──────────────────────────

// Um <pre> temático que aceita string OU objeto (serializa como JSON).
function Pre({ value }) {
  if (value == null) return null;
  const s = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return html`<pre class="text-xs bg-wa-panel border border-wa-border rounded p-2 overflow-x-auto whitespace-pre-wrap break-words text-wa-text">${s}</pre>`;
}

// Seção colapsável cuja abertura inicial segue o expandSignal (expandir/recolher
// tudo). stopPropagation p/ não disparar o "focar passo" da linha da timeline.
function Collapsible({ title, expandSignal, children }) {
  const [open, setOpen] = useState(expandSignal ? expandSignal.value : true);
  useEffect(() => { if (expandSignal) setOpen(expandSignal.value); }, [expandSignal]);
  return html`
    <div class="mt-1">
      <button
        onClick=${(e) => { e.stopPropagation(); setOpen(o => !o); }}
        class="text-xs text-wa-secondary hover:text-wa-teal flex items-center gap-1 select-none"
      >
        <span class="inline-block w-3">${open ? '▾' : '▸'}</span><span>${title}</span>
      </button>
      ${open ? html`<div class="mt-1">${children}</div>` : null}
    </div>
  `;
}

function JsonBlock({ data, expandSignal }) {
  const [expanded, setExpanded] = useState(expandSignal ? expandSignal.value : true);
  useEffect(() => {
    if (expandSignal) setExpanded(expandSignal.value);
  }, [expandSignal]);
  if (!data || (typeof data === 'object' && Object.keys(data).length === 0)) return null;
  if (!expanded) return null;
  return html`<${Pre} value=${data} />`;
}

// tool_executed → nome da tool sempre visível; args e result colapsáveis (F2).
function ToolStepCard({ data, expandSignal }) {
  if (!data) return null;
  const args = data.args;
  const hasArgs = args != null && (typeof args !== 'object' || Object.keys(args).length > 0);
  const hasResult = data.result != null && data.result !== '';
  return html`
    <div class="mt-1 space-y-1">
      <div class="text-xs">
        <span class="text-wa-secondary">tool:</span>
        <span class="font-mono font-medium text-wa-text">${data.tool || '—'}</span>
      </div>
      ${hasArgs ? html`
        <${Collapsible} title="Argumentos" expandSignal=${expandSignal}>
          <${Pre} value=${args} />
        <//>
      ` : null}
      ${hasResult ? html`
        <${Collapsible} title="Resultado" expandSignal=${expandSignal}>
          <${Pre} value=${data.result} />
        <//>
      ` : html`<div class="text-xs text-wa-secondary italic">sem retorno</div>`}
    </div>
  `;
}

// llm_request / llm_response → modelo + tokens + contexto, inline e compacto.
function LlmStepCard({ data, type }) {
  if (!data) return null;
  const tokens = (data.prompt_tokens || 0) + (data.completion_tokens || 0);
  return html`
    <div class="mt-1 text-xs text-wa-secondary flex flex-wrap gap-x-3 gap-y-0.5">
      ${data.model ? html`<span>modelo: <span class="font-mono text-wa-text">${data.model}</span></span>` : null}
      ${data.context_messages != null ? html`<span>contexto: ${data.context_messages} msgs</span>` : null}
      ${type === 'llm_response' && tokens ? html`<span>tokens: ${tokens} <span class="opacity-70">(${data.prompt_tokens || 0}+${data.completion_tokens || 0})</span></span>` : null}
      ${data.tools && data.tools.length ? html`<span>tools: <span class="text-wa-text">${data.tools.join(', ')}</span></span>` : null}
      ${data.has_tool_calls ? html`<span>↳ tool calls</span>` : null}
      ${data.type ? html`<span class="opacity-70">[${data.type}]</span>` : null}
    </div>
  `;
}

// llm_context → system prompt + histórico enviado à IA (F3), lista expansível.
function ContextStepCard({ data, expandSignal }) {
  if (!data || !Array.isArray(data.messages)) {
    return html`<${JsonBlock} data=${data} expandSignal=${expandSignal} />`;
  }
  const msgs = data.messages;
  return html`
    <${Collapsible} title=${`Contexto enviado à IA (${msgs.length} mensagem(ns))`} expandSignal=${expandSignal}>
      <div class="mt-1 space-y-1">
        ${data.model ? html`<div class="text-xs text-wa-secondary">modelo: <span class="font-mono text-wa-text">${data.model}</span></div>` : null}
        ${msgs.map((m, i) => html`
          <div key=${i} class="border border-wa-border rounded p-2 bg-wa-panel">
            <div class="text-xs font-medium text-wa-secondary mb-0.5">
              ${m.role}${m.truncated ? html`<span class="opacity-70"> · truncado</span>` : null}
            </div>
            <pre class="text-xs text-wa-text whitespace-pre-wrap break-words">${m.content}</pre>
          </div>
        `)}
      </div>
    <//>
  `;
}

// Registro por step_type (sem if/elif gigante): cai no JsonBlock cru por padrão.
const STEP_BODY = {
  tool_executed: (d, ex) => html`<${ToolStepCard} data=${d} expandSignal=${ex} />`,
  llm_request: (d) => html`<${LlmStepCard} data=${d} type="llm_request" />`,
  llm_response: (d) => html`<${LlmStepCard} data=${d} type="llm_response" />`,
  llm_context: (d, ex) => html`<${ContextStepCard} data=${d} expandSignal=${ex} />`,
};

function StepBody({ step, expandSignal }) {
  const render = STEP_BODY[step.step_type]
    || ((d, ex) => html`<${JsonBlock} data=${d} expandSignal=${ex} />`);
  return render(step.data, expandSignal);
}

// ── Detail helpers ───────────────────────────────────────────────────

function deriveModel(steps) {
  for (const s of steps) {
    if ((s.step_type === 'llm_request' || s.step_type === 'llm_response') && s.data && s.data.model) {
      return s.data.model;
    }
  }
  return null;
}

function deriveTokens(execution) {
  if (execution.total_tokens) return execution.total_tokens;
  let sum = 0;
  for (const s of (execution.steps || [])) {
    if (s.step_type === 'llm_response' && s.data) {
      sum += (s.data.prompt_tokens || 0) + (s.data.completion_tokens || 0);
    }
  }
  return sum;
}

// routing_steps é gravado como JSON string na coluna (o get_by_id não o parseia).
function parseRouting(rs) {
  if (!rs) return [];
  let arr = rs;
  if (typeof rs === 'string') {
    try { arr = JSON.parse(rs); } catch { return []; }
  }
  return Array.isArray(arr) ? arr : [];
}

// ── Detail View ──────────────────────────────────────────────────

function ExecutionDetail({ execution, onBack, focusStep, onFocusStep, onOpenConversation }) {
  const baseTsMs = execution.started_at * 1000;
  const steps = execution.steps || [];
  const [expandSignal, setExpandSignal] = useState({ value: true, ver: 0 });
  const toggleAll = () => setExpandSignal(s => ({ value: !s.value, ver: s.ver + 1 }));

  const model = deriveModel(steps);
  const tokens = deriveTokens(execution);
  const routing = parseRouting(execution.routing_steps);

  // Passo focado via ?step=<id>: rola até ele ao abrir/mudar o alvo. Guarda os
  // nós por id (chave estável = step.id) p/ o scrollIntoView.
  const stepRefs = useRef({});
  useEffect(() => {
    if (focusStep == null) return;
    const node = stepRefs.current[String(focusStep)];
    if (node) node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [focusStep, execution.id]);

  // Link deste detalhe (inclui ?step quando um passo está focado).
  const detailPath = `/executions/${execution.id}${focusStep != null ? `?step=${encodeURIComponent(focusStep)}` : ''}`;

  return html`
    <div class="flex flex-col h-full">
      <!-- Header -->
      <div class="flex items-center gap-3 px-4 py-3 border-b border-wa-border bg-wa-bg">
        <button
          onClick=${onBack}
          class="p-1.5 rounded hover:bg-wa-hover transition-colors"
          title="Voltar"
        >
          <svg viewBox="0 0 24 24" width="20" height="20" fill="#54656f"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
        </button>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="text-sm font-bold text-wa-text">#${execution.id}</span>
            <${StatusBadge} status=${execution.status} />
            <span class="text-xs text-wa-secondary">${execution.trigger_type}</span>
          </div>
          <div class="text-xs text-wa-secondary">
            ${execution.phone} · ${formatTime(execution.started_at)}
            ${execution.duration_ms != null ? ` · ${formatDuration(execution.duration_ms)}` : ''}
          </div>
          <!-- Meta enriquecida (plano 36 F5): conversa/canal/agente/modelo/tokens -->
          <div class="text-xs text-wa-secondary flex flex-wrap gap-x-3 gap-y-0.5 mt-0.5">
            ${execution.conversation_id != null ? html`
              <button
                onClick=${() => onOpenConversation && onOpenConversation(execution.conversation_id)}
                class="text-wa-teal hover:underline underline-offset-2"
                title="Filtrar execuções desta conversa"
              >Conversa #${execution.conversation_id}</button>
            ` : null}
            ${execution.channel_label ? html`<span>Canal: <span class="text-wa-text">${execution.channel_label}</span></span>` : null}
            ${execution.agent_key ? html`<span>Agente: <span class="text-wa-text">${execution.agent_key}</span></span>` : null}
            ${model ? html`<span>Modelo: <span class="font-mono text-wa-text">${model}</span></span>` : null}
            ${tokens ? html`<span>${tokens} tokens</span>` : null}
          </div>
        </div>
        ${steps.length > 0 ? html`
          <button
            onClick=${toggleAll}
            class="px-2 py-1 text-xs text-wa-secondary hover:text-wa-teal hover:bg-wa-hover rounded transition-colors shrink-0"
            title=${expandSignal.value ? 'Recolher todos os eventos' : 'Expandir todos os eventos'}
          >${expandSignal.value ? 'Recolher tudo' : 'Expandir tudo'}</button>
        ` : null}
        <${CopyLinkButton} path=${detailPath} variant="icon" title="Copiar link desta execução" />
      </div>

      <!-- Timeline -->
      <div class="flex-1 overflow-y-auto p-4">
        ${execution.error ? html`
          <div class="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg">
            <span class="text-sm font-medium text-red-700">Erro: </span>
            <span class="text-sm text-red-600">${execution.error}</span>
          </div>
        ` : null}

        ${routing.length ? html`
          <div class="mb-4 p-2 bg-wa-panel border border-wa-border rounded-lg text-xs">
            <div class="font-medium text-wa-secondary mb-1">Rota de agentes</div>
            <div class="flex flex-wrap items-center gap-1">
              ${routing.map((r, i) => html`
                <span key=${i} class="flex items-center gap-1">
                  ${i === 0 && r.from ? html`<span class="px-1.5 py-0.5 rounded bg-wa-bg border border-wa-border text-wa-text">${r.from}</span>` : null}
                  <span class="text-wa-secondary">→</span>
                  <span class="px-1.5 py-0.5 rounded bg-wa-bg border border-wa-border text-wa-text" title=${r.reason || ''}>${r.to}</span>
                </span>
              `)}
            </div>
          </div>
        ` : null}

        <div class="relative pl-6">
          <!-- Vertical line -->
          <div class="absolute left-[9px] top-2 bottom-2 w-px bg-wa-border"></div>

          ${steps.map((step, i) => {
            const colors = STEP_COLORS[step.step_type] || STEP_COLORS.error;
            const isError = step.status === 'error';
            // id estável do passo (fallback pro índice se o backend não expõe id).
            const stepId = step.id != null ? step.id : i;
            const isFocused = focusStep != null && String(focusStep) === String(stepId);
            return html`
              <div
                key=${stepId}
                ref=${(el) => { if (el) stepRefs.current[String(stepId)] = el; }}
                onClick=${() => onFocusStep && onFocusStep(stepId)}
                class="relative mb-4 last:mb-0 rounded cursor-pointer ${isFocused ? 'ring-1 ring-wa-teal bg-wa-hover' : ''}"
                title="Focar este passo (link direto)"
              >
                <!-- Dot -->
                <div class="absolute -left-6 top-1 w-[18px] h-[18px] rounded-full border-2 ${isError ? 'bg-red-100 border-red-400' : `bg-wa-bg ${colors.border}`}"></div>
                <!-- Content -->
                <div class="ml-2">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="text-xs font-mono text-wa-secondary">${formatRelativeTs(step.ts, baseTsMs)}</span>
                    <${StepBadge} type=${step.step_type} />
                    ${step.agent_key ? html`<span class="text-xs text-wa-secondary">${step.agent_key}</span>` : null}
                    ${isError ? html`<span class="text-xs text-red-600 font-medium">ERRO</span>` : null}
                  </div>
                  <${StepBody} step=${step} expandSignal=${expandSignal} />
                </div>
              </div>
            `;
          })}

          ${steps.length === 0 ? html`
            <div class="text-sm text-wa-secondary italic">Nenhum passo registrado.</div>
          ` : null}
        </div>
      </div>
    </div>
  `;
}

// ── List View ────────────────────────────────────────────────────

function executionIdFromUrl() {
  const m = window.location.pathname.match(/^\/executions\/(\d+)$/);
  return m ? parseInt(m[1], 10) : null;
}

// Passo focado (?step=<id>) da query — string p/ casar com step.id numérico ou
// índice-fallback. null quando ausente.
function stepFromUrl() {
  const v = new URLSearchParams(window.location.search).get('step');
  return v == null || v === '' ? null : v;
}

export function Executions() {
  const [executions, setExecutions] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [filterPhone, setFilterPhone] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterConversation, setFilterConversation] = useState('');
  const [filterFrom, setFilterFrom] = useState('');
  const [filterTo, setFilterTo] = useState('');
  const [selected, setSelected] = useState(null);
  const [selectedData, setSelectedData] = useState(null);
  const [focusStep, setFocusStep] = useState(stepFromUrl());  // ?step do detalhe
  // plano 36 F3/F5: kill-switch da captura do contexto enviado à IA (default OFF).
  const [captureContext, setCaptureContext] = useState(false);
  const PAGE_SIZE = 30;

  // Lê o estado atual do kill-switch no mount.
  useEffect(() => {
    let alive = true;
    getConfig().then(res => {
      if (alive && res.ok) setCaptureContext(!!res.data.execution_capture_context);
    });
    return () => { alive = false; };
  }, []);

  const toggleCapture = useCallback(async () => {
    const next = !captureContext;
    setCaptureContext(next);  // otimista
    const res = await saveConfig({ execution_capture_context: next });
    if (!res.ok) setCaptureContext(!next);  // reverte em falha
  }, [captureContext]);

  const fetchList = useCallback(async () => {
    setLoading(true);
    const params = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (filterPhone) params.phone = filterPhone;
    if (filterStatus) params.status = filterStatus;
    if (filterConversation) params.conversation_id = filterConversation;
    if (filterFrom) params.date_from = filterFrom;
    if (filterTo) params.date_to = filterTo;
    const res = await getExecutions(params);
    if (res.ok) {
      setExecutions(res.data.items || []);
      setTotal(res.data.total || 0);
    }
    setLoading(false);
  }, [page, filterPhone, filterStatus, filterConversation, filterFrom, filterTo]);

  useEffect(() => { fetchList(); }, [fetchList]);

  // Deep-link da LISTA → query (Plano 24). Só a query: o pathname (/executions
  // vs /executions/{id}) segue governado pelo pushState/popstate próprio abaixo.
  // Hidrata filtros/página no mount+back/forward; reflete no replaceState quando
  // mudam. `page` é 1-based na URL, 0-based no estado.
  useUrlState({
    read: () => readParams(window.location.search, LIST_URL_SCHEMA),
    apply: (s) => {
      setFilterPhone(s.phone);
      setFilterStatus(s.status);
      setFilterConversation(s.conv || '');
      setFilterFrom(s.from || '');
      setFilterTo(s.to || '');
      setPage(Math.max(0, (s.page || 1) - 1));
    },
    serialize: () => writeParams({
      phone: filterPhone,
      status: filterStatus,
      conv: filterConversation,
      from: filterFrom,
      to: filterTo,
      page: page + 1,
    }, LIST_URL_SCHEMA),
    deps: [filterPhone, filterStatus, filterConversation, filterFrom, filterTo, page],
  });

  // Auto-refresh every 5s
  useEffect(() => {
    const id = setInterval(fetchList, 5000);
    return () => clearInterval(id);
  }, [fetchList]);

  const handleSelect = useCallback(async (id, opts = {}) => {
    const res = await getExecution(id);
    if (res.ok) {
      setSelectedData(res.data);
      setSelected(id);
      if (opts.skipPush) {
        // Aberto pela URL (mount/popstate): honra o ?step que já está no endereço.
        setFocusStep(stepFromUrl());
      } else {
        // Clique numa linha: detalhe limpo, sem passo focado.
        setFocusStep(null);
        const target = `/executions/${id}`;
        if (window.location.pathname !== target) {
          history.pushState(null, '', target);
        }
      }
    }
  }, []);

  const listQuery = useCallback((overrides = {}) => writeParams({
    phone: filterPhone,
    status: filterStatus,
    conv: filterConversation,
    from: filterFrom,
    to: filterTo,
    page: page + 1,
    ...overrides,
  }, LIST_URL_SCHEMA), [filterPhone, filterStatus, filterConversation, filterFrom, filterTo, page]);

  const handleBack = useCallback(() => {
    setSelected(null);
    setSelectedData(null);
    setFocusStep(null);
    // Volta pra lista preservando os filtros ativos na query.
    const qs = listQuery();
    const target = `/executions${qs ? `?${qs}` : ''}`;
    if (`${window.location.pathname}${window.location.search}` !== target) {
      history.pushState(null, '', target);
    }
    fetchList();
  }, [fetchList, listQuery]);

  // Abrir a lista filtrada por uma conversa (clique no "#conv" do detalhe, F5/D1).
  const openConversationFilter = useCallback((convId) => {
    const conv = String(convId);
    setFilterConversation(conv);
    setPage(0);
    setSelected(null);
    setSelectedData(null);
    setFocusStep(null);
    const qs = writeParams({
      phone: filterPhone, status: filterStatus, conv, from: filterFrom, to: filterTo, page: 1,
    }, LIST_URL_SCHEMA);
    const target = `/executions${qs ? `?${qs}` : ''}`;
    history.pushState(null, '', target);
    // O refetch dispara pelo useEffect([fetchList]) quando filterConversation muda.
  }, [filterPhone, filterStatus, filterFrom, filterTo]);

  // Open from URL on mount, and sync with browser back/forward via popstate.
  const selectedRef = useRef(selected);
  useEffect(() => { selectedRef.current = selected; }, [selected]);

  useEffect(() => {
    const initial = executionIdFromUrl();
    if (initial != null) handleSelect(initial, { skipPush: true });

    function onPop() {
      const urlId = executionIdFromUrl();
      if (urlId == null) {
        setSelected(null);
        setSelectedData(null);
        setFocusStep(null);
      } else if (urlId !== selectedRef.current) {
        handleSelect(urlId, { skipPush: true });
      }
      // ?step vive na query do path do detalhe — ressincroniza no back/forward.
      setFocusStep(stepFromUrl());
    }
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [handleSelect]);

  // Reflete o passo focado como ?step na URL do detalhe (replace, sem histórico).
  // Só age em detalhe (pathname /executions/{id}); preserva o path e mexe só na
  // query — não briga com o useUrlState da lista (deps/telas distintas).
  useEffect(() => {
    if (executionIdFromUrl() == null) return;  // fora do detalhe: nada a refletir
    const qs = focusStep != null ? `?step=${encodeURIComponent(focusStep)}` : '';
    const next = `${window.location.pathname}${qs}`;
    const cur = `${window.location.pathname}${window.location.search}`;
    if (next !== cur) history.replaceState(null, '', next);
  }, [focusStep, selected]);

  // Detail view
  if (selected && selectedData) {
    return html`
      <div class="h-full bg-wa-bg rounded-xl border border-wa-border shadow-sm overflow-hidden">
        <${ExecutionDetail}
          execution=${selectedData}
          onBack=${handleBack}
          focusStep=${focusStep}
          onFocusStep=${setFocusStep}
          onOpenConversation=${openConversationFilter}
        />
      </div>
    `;
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const inputCls = 'bg-wa-panel text-wa-text px-3 py-1.5 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none';

  // List view
  return html`
    <div class="bg-wa-bg rounded-xl border border-wa-border shadow-sm flex flex-col h-full">
      <!-- Header -->
      <div class="px-4 py-3 border-b border-wa-border">
        <div class="flex items-center justify-between mb-2 gap-2 flex-wrap">
          <h2 class="text-base font-bold text-wa-text">Execuções</h2>
          <label
            class="flex items-center gap-2 text-xs text-wa-secondary cursor-pointer select-none"
            title="Salva o system prompt + histórico enviado à IA em cada execução (para depurar). Aumenta o tamanho do banco — desligue quando não precisar."
          >
            <input
              type="checkbox"
              checked=${captureContext}
              onChange=${toggleCapture}
              class="accent-wa-teal"
            />
            Capturar contexto enviado à IA
          </label>
        </div>
        <div class="flex gap-2 flex-wrap items-center">
          <input
            type="text"
            placeholder="Filtrar por telefone..."
            value=${filterPhone}
            onInput=${(e) => { setFilterPhone(e.target.value); setPage(0); }}
            class="${inputCls} w-40"
          />
          <input
            type="number"
            placeholder="ID conversa"
            value=${filterConversation}
            onInput=${(e) => { setFilterConversation(e.target.value); setPage(0); }}
            class="${inputCls} w-32"
          />
          <select
            value=${filterStatus}
            onChange=${(e) => { setFilterStatus(e.target.value); setPage(0); }}
            class=${inputCls}
          >
            <option value="">Todos os status</option>
            <option value="completed">Concluída</option>
            <option value="failed">Falhou</option>
            <option value="running">Em execução</option>
          </select>
          <label class="text-xs text-wa-secondary flex items-center gap-1">
            De
            <input
              type="date"
              value=${filterFrom}
              onInput=${(e) => { setFilterFrom(e.target.value); setPage(0); }}
              class="${inputCls} py-1"
            />
          </label>
          <label class="text-xs text-wa-secondary flex items-center gap-1">
            Até
            <input
              type="date"
              value=${filterTo}
              onInput=${(e) => { setFilterTo(e.target.value); setPage(0); }}
              class="${inputCls} py-1"
            />
          </label>
          ${(filterPhone || filterStatus || filterConversation || filterFrom || filterTo) ? html`
            <button
              onClick=${() => { setFilterPhone(''); setFilterStatus(''); setFilterConversation(''); setFilterFrom(''); setFilterTo(''); setPage(0); }}
              class="text-xs text-wa-secondary hover:text-wa-teal px-2 py-1 rounded border border-wa-border hover:bg-wa-hover transition-colors"
            >Limpar</button>
          ` : null}
          <span class="text-xs text-wa-secondary self-center ml-auto">${total} execução(ões)</span>
        </div>
      </div>

      <!-- Table -->
      <div class="flex-1 overflow-auto">
        ${loading && executions.length === 0 ? html`
          <div class="p-8 text-center text-wa-secondary text-sm">Carregando...</div>
        ` : executions.length === 0 ? html`
          <div class="p-8 text-center text-wa-secondary text-sm">Nenhuma execução encontrada.</div>
        ` : html`
          <table class="w-full text-sm">
            <thead class="bg-wa-panel sticky top-0">
              <tr>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">#</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Conversa</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Canal</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Telefone</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Agente</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Tipo</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Status</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Início</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Duração</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Tokens</th>
                <th class="text-left px-4 py-2 font-medium text-wa-secondary text-xs">Steps</th>
              </tr>
            </thead>
            <tbody>
              ${executions.map(ex => html`
                <tr
                  key=${ex.id}
                  onClick=${() => handleSelect(ex.id)}
                  class="border-t border-wa-border hover:bg-wa-hover cursor-pointer transition-colors"
                >
                  <td class="px-4 py-2.5 font-mono font-bold text-wa-text">${ex.id}</td>
                  <td class="px-4 py-2.5 text-wa-text font-mono text-xs">${ex.conversation_id != null ? `#${ex.conversation_id}` : '—'}</td>
                  <td class="px-4 py-2.5 text-wa-secondary text-xs">${ex.channel_label || '—'}</td>
                  <td class="px-4 py-2.5 text-wa-text">${ex.phone}</td>
                  <td class="px-4 py-2.5 text-wa-secondary text-xs">${ex.agent_key || '—'}</td>
                  <td class="px-4 py-2.5">
                    <span class="text-xs px-1.5 py-0.5 rounded bg-wa-panel text-wa-secondary">${ex.trigger_type}</span>
                  </td>
                  <td class="px-4 py-2.5"><${StatusBadge} status=${ex.status} /></td>
                  <td class="px-4 py-2.5 text-wa-secondary text-xs">${formatTime(ex.started_at)}</td>
                  <td class="px-4 py-2.5 text-wa-secondary text-xs">${formatDuration(ex.duration_ms)}</td>
                  <td class="px-4 py-2.5 text-wa-secondary text-xs">${ex.total_tokens || 0}</td>
                  <td class="px-4 py-2.5 text-wa-secondary text-xs">${ex.step_count || 0}</td>
                </tr>
              `)}
            </tbody>
          </table>
        `}
      </div>

      <!-- Pagination -->
      ${totalPages > 1 ? html`
        <div class="flex items-center justify-between px-4 py-2 border-t border-wa-border text-xs text-wa-secondary">
          <button
            onClick=${() => setPage(Math.max(0, page - 1))}
            disabled=${page === 0}
            class="px-3 py-1 rounded border border-wa-border hover:bg-wa-hover disabled:opacity-30 transition-colors"
          >Anterior</button>
          <span>Página ${page + 1} de ${totalPages}</span>
          <button
            onClick=${() => setPage(Math.min(totalPages - 1, page + 1))}
            disabled=${page >= totalPages - 1}
            class="px-3 py-1 rounded border border-wa-border hover:bg-wa-hover disabled:opacity-30 transition-colors"
          >Próxima</button>
        </div>
      ` : null}
    </div>
  `;
}
