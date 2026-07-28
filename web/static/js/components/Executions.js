import { h } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  getExecutions, getExecution, getConfig, saveConfig,
  getExecutionStats, getExecutionModels,
} from '../services/api.js';
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
  // Nexus: busca por mensagem gerada / recebida, ID da mensagem, só-IA e agente.
  str('si', ''),           // search_input  (Msg do Cliente)
  str('so', ''),           // search_output (Msg da IA)
  str('mid', ''),          // msg_id (ID da Mensagem)
  int('ai', 0),            // only_ai (0|1)
  str('agent', ''),        // agent_key
  str('ch', ''),           // channel_id
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
  running: { bg: 'bg-amber-100', text: 'text-amber-800', dot: 'bg-amber-500', label: 'Em execução' },
  completed: { bg: 'bg-green-100', text: 'text-green-800', dot: 'bg-green-500', label: 'Concluída' },
  failed: { bg: 'bg-red-100', text: 'text-red-800', dot: 'bg-red-500', label: 'Falhou' },
};

// Pills coloridos por agente (estilo Nexus). Fallback cinza para nomes fora da
// paleta. A cor do pill segue o PRIMEIRO segmento de agent_key (antes de "->").
const AGENT_COLORS = [
  { bg: 'bg-blue-100', text: 'text-blue-800' },
  { bg: 'bg-purple-100', text: 'text-purple-800' },
  { bg: 'bg-teal-100', text: 'text-teal-800' },
  { bg: 'bg-orange-100', text: 'text-orange-800' },
  { bg: 'bg-pink-100', text: 'text-pink-800' },
  { bg: 'bg-indigo-100', text: 'text-indigo-800' },
];
const AGENT_GRAY = { bg: 'bg-wa-panel', text: 'text-wa-secondary' };

// Hash estável nome→cor (determinístico entre renders/execuções).
function agentColor(key) {
  if (!key) return AGENT_GRAY;
  const base = String(key).split('->')[0].trim();
  let hash = 0;
  for (let i = 0; i < base.length; i++) hash = (hash * 31 + base.charCodeAt(i)) >>> 0;
  return AGENT_COLORS[hash % AGENT_COLORS.length];
}

function AgentBadge({ agentKey, title }) {
  if (!agentKey) return null;
  const c = agentColor(agentKey);
  return html`<span
    class="inline-block px-1.5 py-0.5 text-xs font-medium rounded ${c.bg} ${c.text}"
    title=${title || agentKey}
  >${agentKey}</span>`;
}

// Campo rotulado (label em caixa alta acima do controle) — layout estilo Nexus.
function FilterField({ label, children, className }) {
  return html`
    <label class="flex flex-col gap-1 ${className || ''}">
      <span class="text-[11px] uppercase tracking-wide text-wa-secondary font-medium">${label}</span>
      ${children}
    </label>
  `;
}

// Multi-seleção de canais: dropdown com checkboxes + busca (suporta muitos canais).
// `selected` é uma lista de channel_id; `onChange` recebe a nova lista.
function ChannelMultiSelect({ channels, selected, onChange, inputCls }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef(null);

  // Fecha ao clicar fora.
  useEffect(() => {
    if (!open) return;
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const selSet = new Set(selected);
  const toggle = (id) => {
    const next = new Set(selSet);
    if (next.has(id)) next.delete(id); else next.add(id);
    onChange([...next]);
  };

  const q = query.trim().toLowerCase();
  const filtered = q
    ? channels.filter(c => (c.channel_label || '').toLowerCase().includes(q)
        || (c.channel_id || '').toLowerCase().includes(q))
    : channels;

  const labelText = selected.length === 0
    ? 'Todos os canais'
    : selected.length === 1
      ? (channels.find(c => c.channel_id === selected[0])?.channel_label || selected[0])
      : `${selected.length} canais selecionados`;

  return html`
    <div class="relative" ref=${ref}>
      <button
        type="button"
        onClick=${() => setOpen(o => !o)}
        class="${inputCls} w-full flex items-center justify-between gap-2 text-left ${selected.length ? 'text-wa-text' : 'text-wa-secondary'}"
      >
        <span class="truncate">${labelText}</span>
        <span class="flex items-center gap-1.5 shrink-0">
          ${selected.length ? html`<span class="text-xs px-1.5 py-0.5 rounded-full bg-wa-teal text-white">${selected.length}</span>` : null}
          <span class="text-wa-secondary text-xs">▾</span>
        </span>
      </button>
      ${open ? html`
        <div class="absolute z-20 mt-1 w-full min-w-[16rem] bg-wa-panel border border-wa-border rounded-lg shadow-lg overflow-hidden">
          ${channels.length > 6 ? html`
            <div class="p-2 border-b border-wa-border">
              <input
                type="text" placeholder="Buscar canal..."
                value=${query}
                onInput=${(e) => setQuery(e.target.value)}
                class="${inputCls} w-full py-1"
                autofocus
              />
            </div>
          ` : null}
          ${selected.length ? html`
            <button
              type="button"
              onClick=${() => onChange([])}
              class="w-full text-left px-3 py-1.5 text-xs text-wa-secondary hover:text-wa-teal hover:bg-wa-hover border-b border-wa-border"
            >Limpar seleção (${selected.length})</button>
          ` : null}
          <div class="max-h-60 overflow-y-auto py-1">
            ${filtered.length === 0 ? html`
              <div class="px-3 py-2 text-xs text-wa-secondary italic">Nenhum canal encontrado.</div>
            ` : filtered.map(c => html`
              <label
                key=${c.channel_id}
                class="flex items-center gap-2 px-3 py-1.5 hover:bg-wa-hover cursor-pointer text-sm text-wa-text"
              >
                <input
                  type="checkbox"
                  checked=${selSet.has(c.channel_id)}
                  onChange=${() => toggle(c.channel_id)}
                  class="accent-wa-teal"
                />
                <span class="truncate" title=${c.channel_id}>${c.channel_label}</span>
              </label>
            `)}
          </div>
        </div>
      ` : null}
    </div>
  `;
}

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
  return html`<span class="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded ${badge.bg} ${badge.text}">
    <span class="w-1.5 h-1.5 rounded-full ${badge.dot}"></span>${badge.label}
  </span>`;
}

// ── Stat cards (B1) + Cost panel (B2) ────────────────────────────────

function StatCard({ label, value, icon, accent }) {
  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-3 flex flex-col gap-1">
      <div class="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-wa-secondary font-medium">
        ${icon ? html`<span aria-hidden="true">${icon}</span>` : null}
        <span>${label}</span>
      </div>
      <div class="text-2xl font-bold ${accent || 'text-wa-text'}">${value}</div>
    </div>
  `;
}

function StatCards({ stats }) {
  const s = stats || {};
  return html`
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      <${StatCard} label="Total 24h" icon="📊" value=${(s.total_count ?? 0).toLocaleString('pt-BR')} />
      <${StatCard} label="Sucesso" icon="✓" accent="text-green-600" value=${(s.success_count ?? 0).toLocaleString('pt-BR')} />
      <${StatCard} label="Erros" icon="✕" accent="text-red-600" value=${(s.error_count ?? 0).toLocaleString('pt-BR')} />
      <${StatCard} label="Tokens 24h" icon="⚡" value=${(s.total_tokens ?? 0).toLocaleString('pt-BR')} />
    </div>
  `;
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
// B5: badge do sub-agente que chamou a tool (qual hop do roteamento a invocou).
function ToolStepCard({ data, expandSignal, agentKey }) {
  if (!data) return null;
  const args = data.args;
  const hasArgs = args != null && (typeof args !== 'object' || Object.keys(args).length > 0);
  const hasResult = data.result != null && data.result !== '';
  return html`
    <div class="mt-1 space-y-1">
      <div class="text-xs flex items-center gap-1.5 flex-wrap">
        <span class="text-wa-secondary">tool:</span>
        <span class="font-mono font-medium text-wa-text">${data.tool || '—'}</span>
        ${agentKey ? html`<${AgentBadge} agentKey=${agentKey} title=${`Chamada pelo agente ${agentKey}`} />` : null}
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
// Cada renderer recebe (step, expandSignal) — o step carrega data + agent_key.
const STEP_BODY = {
  tool_executed: (s, ex) => html`<${ToolStepCard} data=${s.data} expandSignal=${ex} agentKey=${s.agent_key} />`,
  llm_request: (s) => html`<${LlmStepCard} data=${s.data} type="llm_request" />`,
  llm_response: (s) => html`<${LlmStepCard} data=${s.data} type="llm_response" />`,
  llm_context: (s, ex) => html`<${ContextStepCard} data=${s.data} expandSignal=${ex} />`,
};

function StepBody({ step, expandSignal }) {
  const render = STEP_BODY[step.step_type]
    || ((s, ex) => html`<${JsonBlock} data=${s.data} expandSignal=${ex} />`);
  return render(step, expandSignal);
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
            ${execution.agent_key ? html`<span class="flex items-center gap-1">Agente: <${AgentBadge} agentKey=${execution.agent_key} /></span>` : null}
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
  const [filterStatus, setFilterStatus] = useState([]);  // status selecionados (multi)
  const [filterConversation, setFilterConversation] = useState('');
  const [filterFrom, setFilterFrom] = useState('');
  const [filterTo, setFilterTo] = useState('');
  // Nexus: busca por mensagem gerada/recebida, ID da mensagem, só-IA e agente.
  const [filterSearchInput, setFilterSearchInput] = useState('');
  const [filterSearchOutput, setFilterSearchOutput] = useState('');
  const [filterMsgId, setFilterMsgId] = useState('');
  const [filterOnlyAi, setFilterOnlyAi] = useState(false);
  const [filterAgent, setFilterAgent] = useState([]);  // agent_keys selecionados (multi)
  const [filterChannels, setFilterChannels] = useState([]);  // channel_ids selecionados
  // Cards de estatística + pills por agente.
  const [stats, setStats] = useState(null);
  const [agentKeys, setAgentKeys] = useState([]);
  const [channels, setChannels] = useState([]);  // [{channel_id, channel_label}]
  const [filtersOpen, setFiltersOpen] = useState(false);  // barra de filtros colapsável
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
    if (filterStatus.length) params.status = filterStatus.join(',');
    if (filterConversation) params.conversation_id = filterConversation;
    if (filterFrom) params.date_from = filterFrom;
    if (filterTo) params.date_to = filterTo;
    if (filterSearchInput) params.search_input = filterSearchInput;
    if (filterSearchOutput) params.search_output = filterSearchOutput;
    if (filterMsgId) params.msg_id = filterMsgId;
    if (filterOnlyAi) params.only_ai = 1;
    if (filterAgent.length) params.agent_key = filterAgent.join(',');
    if (filterChannels.length) params.channel_id = filterChannels.join(',');
    const res = await getExecutions(params);
    if (res.ok) {
      setExecutions(res.data.items || []);
      setTotal(res.data.total || 0);
    }
    setLoading(false);
  }, [page, filterPhone, filterStatus, filterConversation, filterFrom, filterTo,
      filterSearchInput, filterSearchOutput, filterMsgId, filterOnlyAi, filterAgent,
      filterChannels]);

  useEffect(() => { fetchList(); }, [fetchList]);

  // Cards de estatística (janela padrão 24h no backend).
  const fetchStats = useCallback(async () => {
    const res = await getExecutionStats();
    if (res.ok) setStats(res.data);
  }, []);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  // Pills por agente (a partir dos agent_key conhecidos).
  useEffect(() => {
    let alive = true;
    getExecutionModels().then(res => {
      if (alive && res.ok) {
        setAgentKeys(res.data.agent_keys || []);
        setChannels(res.data.channels || []);
      }
    });
    return () => { alive = false; };
  }, []);

  // Deep-link da LISTA → query (Plano 24). Só a query: o pathname (/executions
  // vs /executions/{id}) segue governado pelo pushState/popstate próprio abaixo.
  // Hidrata filtros/página no mount+back/forward; reflete no replaceState quando
  // mudam. `page` é 1-based na URL, 0-based no estado.
  useUrlState({
    read: () => readParams(window.location.search, LIST_URL_SCHEMA),
    apply: (s) => {
      setFilterPhone(s.phone);
      setFilterStatus(s.status ? s.status.split(',').filter(Boolean) : []);
      setFilterConversation(s.conv || '');
      setFilterFrom(s.from || '');
      setFilterTo(s.to || '');
      setFilterSearchInput(s.si || '');
      setFilterSearchOutput(s.so || '');
      setFilterMsgId(s.mid || '');
      setFilterOnlyAi(!!s.ai);
      setFilterAgent(s.agent ? s.agent.split(',').filter(Boolean) : []);
      setFilterChannels(s.ch ? s.ch.split(',').filter(Boolean) : []);
      setPage(Math.max(0, (s.page || 1) - 1));
    },
    serialize: () => writeParams({
      phone: filterPhone,
      status: filterStatus.join(','),
      conv: filterConversation,
      from: filterFrom,
      to: filterTo,
      si: filterSearchInput,
      so: filterSearchOutput,
      mid: filterMsgId,
      ai: filterOnlyAi ? 1 : 0,
      agent: filterAgent.join(','),
      ch: filterChannels.join(','),
      page: page + 1,
    }, LIST_URL_SCHEMA),
    deps: [filterPhone, filterStatus, filterConversation, filterFrom, filterTo,
           filterSearchInput, filterSearchOutput, filterMsgId, filterOnlyAi, filterAgent,
           filterChannels, page],
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
    status: filterStatus.join(','),
    conv: filterConversation,
    from: filterFrom,
    to: filterTo,
    si: filterSearchInput,
    so: filterSearchOutput,
    mid: filterMsgId,
    ai: filterOnlyAi ? 1 : 0,
    agent: filterAgent.join(','),
    ch: filterChannels.join(','),
    page: page + 1,
    ...overrides,
  }, LIST_URL_SCHEMA), [filterPhone, filterStatus, filterConversation, filterFrom, filterTo,
    filterSearchInput, filterSearchOutput, filterMsgId, filterOnlyAi, filterAgent,
    filterChannels, page]);

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
      phone: filterPhone, status: filterStatus.join(','), conv, from: filterFrom, to: filterTo,
      si: filterSearchInput, so: filterSearchOutput, mid: filterMsgId,
      ai: filterOnlyAi ? 1 : 0, agent: filterAgent.join(','), ch: filterChannels.join(','), page: 1,
    }, LIST_URL_SCHEMA);
    const target = `/executions${qs ? `?${qs}` : ''}`;
    history.pushState(null, '', target);
    // O refetch dispara pelo useEffect([fetchList]) quando filterConversation muda.
  }, [filterPhone, filterStatus, filterFrom, filterTo,
      filterSearchInput, filterSearchOutput, filterMsgId, filterOnlyAi, filterAgent,
      filterChannels]);

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
  const activeFilterCount = [
    filterPhone, filterConversation, filterFrom, filterTo,
    filterSearchInput, filterSearchOutput, filterMsgId, filterOnlyAi,
    filterStatus.length > 0, filterAgent.length > 0, filterChannels.length > 0,
  ].filter(Boolean).length;

  // List view
  return html`
    <div class="bg-wa-bg rounded-xl border border-wa-border shadow-sm flex flex-col">
      <!-- Header -->
      <div class="px-4 py-3 border-b border-wa-border space-y-3">
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <div class="flex items-center gap-2">
            <span class="w-9 h-9 rounded-lg bg-wa-teal/10 text-wa-teal flex items-center justify-center text-lg" aria-hidden="true">📈</span>
            <div>
              <h2 class="text-base font-bold text-wa-text leading-tight">Execuções</h2>
              <p class="text-xs text-wa-secondary leading-tight">Histórico de webhooks e turnos de IA</p>
            </div>
          </div>
          <div class="flex items-center gap-3 flex-wrap">
            <label
              class="flex items-center gap-2 text-xs text-wa-secondary cursor-pointer select-none"
              title="Salva o system prompt + histórico enviado à IA em cada execução (para depurar). Aumenta o tamanho do banco — desligue quando não precisar."
            >
              <input type="checkbox" checked=${captureContext} onChange=${toggleCapture} class="accent-wa-teal" />
              Capturar contexto enviado à IA
            </label>
            <button
              onClick=${() => { fetchList(); fetchStats(); }}
              class="text-xs text-wa-secondary hover:text-wa-teal px-2 py-1 rounded border border-wa-border hover:bg-wa-hover transition-colors flex items-center gap-1"
              title="Atualizar agora"
            >↻ Atualizar</button>
          </div>
        </div>

        <!-- Cards de estatística (B1) -->
        <${StatCards} stats=${stats} />

        <!-- Filtros (colapsável, estilo Nexus) -->
        <div class="border border-wa-border rounded-lg overflow-hidden">
          <button
            onClick=${() => setFiltersOpen(o => !o)}
            class="w-full flex items-center justify-between px-3 py-2 bg-wa-panel hover:bg-wa-hover transition-colors select-none"
          >
            <span class="flex items-center gap-2 text-sm font-medium text-wa-text">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" class="text-wa-secondary"><path d="M3 4h18v2l-7 8v6l-4-2v-4L3 6V4z"/></svg>
              Filtros
              ${activeFilterCount ? html`<span class="text-xs px-1.5 py-0.5 rounded-full bg-wa-teal text-white">${activeFilterCount}</span>` : null}
            </span>
            <span class="flex items-center gap-2">
              <span class="text-xs text-wa-secondary">${total} execução(ões)</span>
              <span class="text-wa-secondary text-sm">${filtersOpen ? '▾' : '▸'}</span>
            </span>
          </button>
          ${filtersOpen ? html`
          <div class="p-4 space-y-4 border-t border-wa-border">
        <!-- STATUS (pills) -->
        <div>
          <div class="text-[11px] uppercase tracking-wide text-wa-secondary font-medium mb-1.5">Status</div>
          <div class="flex gap-1.5 flex-wrap items-center">
            ${[['', 'Todos'], ['completed', 'Sucesso'], ['failed', 'Erro'], ['running', 'Em execução']].map(([val, lbl]) => {
              // "Todos" (val vazio) limpa a seleção; os demais alternam (multi).
              const active = val === '' ? filterStatus.length === 0 : filterStatus.includes(val);
              return html`
              <button
                key=${val}
                onClick=${() => {
                  if (val === '') setFilterStatus([]);
                  else setFilterStatus(active
                    ? filterStatus.filter((s) => s !== val)
                    : [...filterStatus, val]);
                  setPage(0);
                }}
                class="text-xs px-2.5 py-1 rounded-full border transition-colors ${active
                  ? 'bg-wa-teal text-white border-wa-teal'
                  : 'bg-wa-hover text-wa-secondary border-wa-border hover:bg-wa-border'}"
              >${lbl}</button>
            `;
            })}
            <span class="w-px h-4 bg-wa-border mx-1"></span>
            <button
              onClick=${() => { setFilterOnlyAi(v => !v); setPage(0); }}
              class="text-xs px-2.5 py-1 rounded-full border transition-colors ${filterOnlyAi
                ? 'bg-wa-teal text-white border-wa-teal'
                : 'bg-wa-hover text-wa-secondary border-wa-border hover:bg-wa-border'}"
              title="Só execuções que realmente invocaram o modelo"
            >⚡ Só IA</button>
          </div>
        </div>

        <!-- AGENTE (pills) -->
        ${agentKeys.length ? html`
          <div>
            <div class="text-[11px] uppercase tracking-wide text-wa-secondary font-medium mb-1.5">Agente</div>
            <div class="flex gap-1.5 flex-wrap items-center">
              ${agentKeys.map((ak) => {
                const active = filterAgent.includes(ak);
                return html`
                  <button
                    key=${ak}
                    onClick=${() => {
                      setFilterAgent(active
                        ? filterAgent.filter((k) => k !== ak)
                        : [...filterAgent, ak]);
                      setPage(0);
                    }}
                    class="text-xs px-2.5 py-1 rounded-full border transition-colors ${active
                      ? 'bg-wa-teal text-white border-wa-teal'
                      : 'bg-wa-hover text-wa-secondary border-wa-border hover:bg-wa-border'}"
                  >${ak}</button>
                `;
              })}
            </div>
          </div>
        ` : null}

        <!-- Campos de texto / data / canais (grid rotulado) -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <${FilterField} label="Canais">
            <${ChannelMultiSelect}
              channels=${channels}
              selected=${filterChannels}
              onChange=${(v) => { setFilterChannels(v); setPage(0); }}
              inputCls=${inputCls}
            />
          <//>
          <${FilterField} label="Telefone">
            <input
              type="text" placeholder="Ex: 5564..."
              value=${filterPhone}
              onInput=${(e) => { setFilterPhone(e.target.value); setPage(0); }}
              class="${inputCls} w-full"
            />
          <//>
          <${FilterField} label="ID Conversa">
            <input
              type="number" placeholder="Ex: 12345"
              value=${filterConversation}
              onInput=${(e) => { setFilterConversation(e.target.value); setPage(0); }}
              class="${inputCls} w-full"
            />
          <//>
          <${FilterField} label="ID da Mensagem">
            <input
              type="text" placeholder="Ex: 3EB0..."
              value=${filterMsgId}
              onInput=${(e) => { setFilterMsgId(e.target.value); setPage(0); }}
              class="${inputCls} w-full"
            />
          <//>
          <${FilterField} label="Data de">
            <input type="date" value=${filterFrom}
              onInput=${(e) => { setFilterFrom(e.target.value); setPage(0); }}
              class="${inputCls} w-full" />
          <//>
          <${FilterField} label="Data até">
            <input type="date" value=${filterTo}
              onInput=${(e) => { setFilterTo(e.target.value); setPage(0); }}
              class="${inputCls} w-full" />
          <//>
          <${FilterField} label="Msg do Cliente">
            <input
              type="text" placeholder="Buscar no input..."
              value=${filterSearchInput}
              onInput=${(e) => { setFilterSearchInput(e.target.value); setPage(0); }}
              class="${inputCls} w-full"
            />
          <//>
          <${FilterField} label="Msg da IA">
            <input
              type="text" placeholder="Buscar no output..."
              value=${filterSearchOutput}
              onInput=${(e) => { setFilterSearchOutput(e.target.value); setPage(0); }}
              class="${inputCls} w-full"
            />
          <//>
        </div>

        <!-- Ações -->
        ${activeFilterCount ? html`
          <div class="flex justify-end">
            <button
              onClick=${() => {
                setFilterPhone(''); setFilterStatus([]); setFilterConversation('');
                setFilterFrom(''); setFilterTo(''); setFilterSearchInput('');
                setFilterSearchOutput(''); setFilterMsgId(''); setFilterOnlyAi(false);
                setFilterAgent([]); setFilterChannels([]); setPage(0);
              }}
              class="text-xs text-wa-secondary hover:text-wa-teal px-3 py-1.5 rounded border border-wa-border hover:bg-wa-hover transition-colors"
            >Limpar filtros</button>
          </div>
        ` : null}
          </div>
          ` : null}
        </div>
      </div>

      <!-- Table (sem scroll interno; a página rola e a paginação limita o tamanho) -->
      <div>
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
                  <td class="px-4 py-2.5 text-xs">${ex.agent_key ? html`<${AgentBadge} agentKey=${ex.agent_key} />` : html`<span class="text-wa-secondary">—</span>`}</td>
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
