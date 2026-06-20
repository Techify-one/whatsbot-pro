// AI Engine — agents editor (plano 06). Lists and edits agents:
// display_name, prompt_key (select dos prompts), model (select de /api/models),
// model_config (temperature/top_p/max_tokens → objeto), tool_names (todas|null
// ou multiselect das tools de código), enabled, description, is_router toggle
// e routing_targets (multiselect de agent_keys quando router).
// Cada save bumpa a versão; o botão Histórico lista versões com Reverter.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  listAgents,
  saveAgent,
  getAgentHistory,
  rollbackAgent,
  listPrompts,
  listTools,
  getModels,
} from '../../services/api.js';

const html = htm.bind(h);

function fmtDate(epochOrIso) {
  if (epochOrIso == null) return '—';
  try {
    // History created_at is an epoch float (seconds); be tolerant of ISO too.
    const ms = typeof epochOrIso === 'number' ? epochOrIso * 1000 : Date.parse(epochOrIso);
    const d = new Date(ms);
    if (isNaN(d.getTime())) return String(epochOrIso);
    return d.toLocaleString('pt-BR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch (e) {
    return String(epochOrIso);
  }
}

// Parse a numeric model_config field, returning '' for empty / undefined so
// the input can be cleared (and the key dropped from the object on save).
function numField(v) {
  if (v === '' || v === null || v === undefined) return '';
  return String(v);
}

// ── History modal (shared shape with prompts/tools) ─────────────────
function HistoryModal({ title, versions, current, busy, onRollback, onClose }) {
  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onClose}>
      <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-md max-h-[80vh] overflow-y-auto"
        onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center justify-between mb-3">
          <div class="text-[15px] font-medium text-wa-text">${title}</div>
          <button class="text-wa-secondary hover:text-wa-text text-xl leading-none" onClick=${onClose}>×</button>
        </div>
        ${(!versions || versions.length === 0)
          ? html`<div class="text-[13px] text-wa-secondary py-4">Nenhuma versão registrada.</div>`
          : html`
            <div class="flex flex-col gap-2">
              ${versions.map(v => html`
                <div key=${v.version} class="flex items-center justify-between gap-2 bg-wa-panel border border-wa-border rounded-md px-3 py-2">
                  <div class="min-w-0">
                    <span class="text-[13px] text-wa-text font-medium">v${v.version}</span>
                    ${v.version === current ? html`<span class="ml-2 px-1.5 py-0.5 rounded-full text-[10px] bg-wa-teal/10 text-wa-teal">atual</span>` : null}
                    <div class="text-[11px] text-wa-secondary">${fmtDate(v.created_at)}</div>
                  </div>
                  <button class="px-2 py-1 rounded-md text-[12px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50 shrink-0"
                    disabled=${busy || v.version === current}
                    onClick=${() => onRollback(v.version)}>Reverter</button>
                </div>
              `)}
            </div>
          `}
      </div>
    </div>
  `;
}

// ── Agent form ──────────────────────────────────────────────────────
// Normalize free text into a valid agent_key slug: lowercase, non-alnum → "_".
function slugifyKey(s) {
  return (s || '').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+/, '').slice(0, 32);
}

function AgentForm({ agent, agents, prompts, tools, models, onSave, onCancel, busy }) {
  // No agent_key => creation mode (the key is typed; otherwise it's fixed).
  const isNew = !agent.agent_key;
  const [agentKey, setAgentKey] = useState(agent.agent_key || '');
  const existingKeys = (agents || []).map(a => a.agent_key);
  const keyTrimmed = agentKey.trim();
  const keyValid = /^[a-z][a-z0-9_]{0,31}$/.test(keyTrimmed) && !existingKeys.includes(keyTrimmed);
  const mc = agent.model_config || {};
  const [displayName, setDisplayName] = useState(agent.display_name || '');
  const [promptKey, setPromptKey] = useState(agent.prompt_key || '');
  const [model, setModel] = useState(mc.model || '');
  const [temperature, setTemperature] = useState(numField(mc.temperature));
  const [topP, setTopP] = useState(numField(mc.top_p));
  const [maxTokens, setMaxTokens] = useState(numField(mc.max_tokens));
  const [enabled, setEnabled] = useState(agent.enabled !== false);
  const [description, setDescription] = useState(agent.description || '');
  const [isRouter, setIsRouter] = useState(!!agent.is_router);
  // tool_names: null => "todas as tools". Otherwise a list of names.
  const [allTools, setAllTools] = useState(agent.tool_names == null);
  const [toolNames, setToolNames] = useState(
    Array.isArray(agent.tool_names) ? [...agent.tool_names] : []
  );
  const [routingTargets, setRoutingTargets] = useState(
    Array.isArray(agent.routing_targets) ? [...agent.routing_targets] : []
  );

  function toggleTool(name) {
    setToolNames(prev => prev.includes(name) ? prev.filter(t => t !== name) : [...prev, name]);
  }
  function toggleTarget(key) {
    setRoutingTargets(prev => prev.includes(key) ? prev.filter(t => t !== key) : [...prev, key]);
  }

  function buildModelConfig() {
    const out = {};
    if (model) out.model = model;
    if (temperature !== '') out.temperature = parseFloat(temperature);
    if (topP !== '') out.top_p = parseFloat(topP);
    if (maxTokens !== '') out.max_tokens = parseInt(maxTokens, 10);
    // Preserve any extra keys the UI doesn't surface (e.g. provider-specific).
    for (const [k, v] of Object.entries(mc)) {
      if (!['model', 'temperature', 'top_p', 'max_tokens'].includes(k)) out[k] = v;
    }
    return out;
  }

  function submit() {
    onSave(isNew ? keyTrimmed : agent.agent_key, {
      display_name: displayName.trim(),
      prompt_key: promptKey,
      model_config: buildModelConfig(),
      tool_names: allTools ? null : toolNames,
      enabled,
      description: description.trim(),
      is_router: isRouter,
      routing_targets: isRouter ? routingTargets : null,
    });
  }

  // Routing targets: other agents only (can't route to itself).
  const otherAgents = (window.__aiAgentsCache || []).filter(a => a.agent_key !== agent.agent_key);

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${isNew
          ? html`Novo agente`
          : html`Editar agente <code class="text-[12px] text-wa-secondary">${agent.agent_key}</code>
              <span class="text-[12px] text-wa-secondary font-normal"> · v${agent.version || 1}</span>`}
      </div>
      <div class="flex flex-col gap-3">
        ${isNew ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Identificador (agent_key)</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono"
              type="text" value=${agentKey} placeholder="ex: vendas, suporte_n2"
              onInput=${(e) => setAgentKey(slugifyKey(e.target.value))} />
            <div class="text-[11px] mt-1 ${keyTrimmed && !keyValid ? 'text-red-500' : 'text-wa-secondary'}">
              ${keyTrimmed && existingKeys.includes(keyTrimmed)
                ? 'Já existe um agente com esse identificador.'
                : keyTrimmed && !keyValid
                  ? 'Use letras minúsculas, números e _ (começando por letra).'
                  : 'Identidade fixa do agente — não muda depois de criado.'}
            </div>
          </div>
        ` : null}
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" value=${displayName} onInput=${(e) => setDisplayName(e.target.value)} />
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Prompt</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            value=${promptKey} onChange=${(e) => setPromptKey(e.target.value)}>
            <option value="">— selecione —</option>
            ${(prompts || []).map(p => html`<option key=${p.prompt_key} value=${p.prompt_key}>${p.prompt_key}</option>`)}
          </select>
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Modelo</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            value=${model} onChange=${(e) => setModel(e.target.value)}>
            <option value="">— padrão do app —</option>
            ${(models || []).map(m => html`<option key=${m.id} value=${m.id}>${m.name || m.id}</option>`)}
            ${(model && !(models || []).some(m => m.id === model))
              ? html`<option value=${model}>${model} (atual)</option>` : null}
          </select>
        </div>

        <div class="grid grid-cols-3 gap-2">
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Temperature</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="number" step="0.1" min="0" max="2" placeholder="—"
              value=${temperature} onInput=${(e) => setTemperature(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">top_p</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="number" step="0.05" min="0" max="1" placeholder="—"
              value=${topP} onInput=${(e) => setTopP(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">max_tokens</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="number" step="1" min="1" placeholder="—"
              value=${maxTokens} onInput=${(e) => setMaxTokens(e.target.value)} />
          </div>
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Tools disponíveis</label>
          <label class="flex items-center gap-2 cursor-pointer mb-2">
            <input type="checkbox" checked=${allTools} onChange=${(e) => setAllTools(e.target.checked)} />
            <span class="text-[14px] text-wa-text">Todas as tools registradas</span>
          </label>
          ${!allTools ? html`
            <div class="flex flex-col gap-1 max-h-40 overflow-y-auto border border-wa-border rounded-md p-2">
              ${(tools || []).length === 0
                ? html`<div class="text-[12px] text-wa-secondary">Nenhuma tool code-in-DB cadastrada.</div>`
                : (tools || []).map(t => html`
                  <label key=${t.name} class="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked=${toolNames.includes(t.name)} onChange=${() => toggleTool(t.name)} />
                    <span class="text-[14px] text-wa-text font-mono">${t.name}</span>
                  </label>
                `)}
            </div>
            <div class="text-[11px] text-wa-secondary mt-1">
              Lista os nomes exatos das tools. Deixe "Todas" marcado para herdar todo o registry.
            </div>
          ` : null}
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Descrição</label>
          <textarea class="wa-field w-full px-3 py-2 rounded-md text-[14px] resize-y" rows="2"
            value=${description} onInput=${(e) => setDescription(e.target.value)}></textarea>
        </div>

        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked=${enabled} onChange=${(e) => setEnabled(e.target.checked)} />
          <span class="text-[14px] text-wa-text">Agente ativo</span>
        </label>

        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked=${isRouter} onChange=${(e) => setIsRouter(e.target.checked)} />
          <span class="text-[14px] text-wa-text">É roteador (handoff/routing)</span>
        </label>

        ${isRouter ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Destinos de roteamento</label>
            ${otherAgents.length === 0
              ? html`<div class="text-[12px] text-wa-secondary">Nenhum outro agente para rotear.</div>`
              : html`
                <div class="flex flex-col gap-1 max-h-40 overflow-y-auto border border-wa-border rounded-md p-2">
                  ${otherAgents.map(a => html`
                    <label key=${a.agent_key} class="flex items-center gap-2 cursor-pointer">
                      <input type="checkbox" checked=${routingTargets.includes(a.agent_key)} onChange=${() => toggleTarget(a.agent_key)} />
                      <span class="text-[14px] text-wa-text">${a.display_name || a.agent_key}</span>
                      <span class="text-[12px] text-wa-secondary font-mono">${a.agent_key}</span>
                    </label>
                  `)}
                </div>
              `}
          </div>
        ` : null}

        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${submit} disabled=${busy || !displayName.trim() || (isNew && !keyValid)}>${busy ? 'Salvando…' : (isNew ? 'Criar' : 'Salvar')}</button>
        </div>
      </div>
    </div>
  `;
}

export default function AgentsManager() {
  const [agents, setAgents] = useState([]);
  const [prompts, setPrompts] = useState([]);
  const [tools, setTools] = useState([]);
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  // History modal state
  const [historyFor, setHistoryFor] = useState(null);
  const [historyRows, setHistoryRows] = useState([]);
  const [historyBusy, setHistoryBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    const [aRes, pRes, tRes, mRes] = await Promise.all([
      listAgents(), listPrompts(), listTools(), getModels(),
    ]);
    if (aRes && aRes.ok) {
      const list = aRes.data || [];
      setAgents(list);
      window.__aiAgentsCache = list;  // used by the form for routing targets
    } else {
      setError((aRes && aRes.error) || 'Falha ao carregar agentes.');
    }
    if (pRes && pRes.ok) setPrompts(pRes.data || []);
    if (tRes && tRes.ok) setTools(tRes.data || []);
    if (mRes && mRes.ok) setModels(mRes.data || []);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleSave(key, data) {
    setBusy(true); setError('');
    const res = await saveAgent(key, data);
    setBusy(false);
    if (res && res.ok) { setEditing(null); load(); }
    else setError((res && res.error) || 'Falha ao salvar o agente.');
  }

  async function openHistory(agent) {
    setHistoryFor(agent);
    setHistoryRows([]);
    const res = await getAgentHistory(agent.agent_key);
    if (res && res.ok) setHistoryRows(res.data || []);
  }

  async function handleRollback(version) {
    if (!historyFor) return;
    setHistoryBusy(true);
    const res = await rollbackAgent(historyFor.agent_key, version);
    setHistoryBusy(false);
    if (res && res.ok) { setHistoryFor(null); load(); }
    else setError((res && res.error) || 'Falha ao reverter a versão.');
  }

  return html`
    <div>
      <div class="flex items-start justify-between gap-3 mb-4 flex-wrap">
        <p class="text-[13px] text-wa-secondary flex-1 min-w-0">
          Agentes definem o prompt, o modelo e as tools que a IA usa. As mudanças valem
          na próxima mensagem (sem reiniciar).
        </p>
        ${!editing ? html`
          <button class="px-3 py-2 rounded-md text-[13px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setEditing({}); setError(''); }}>+ Novo agente</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${editing ? html`
        <${AgentForm} agent=${editing} agents=${agents} prompts=${prompts} tools=${tools} models=${models}
          onSave=${handleSave} onCancel=${() => setEditing(null)} busy=${busy} />
      ` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && agents.length === 0 ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">Nenhum agente cadastrado.</div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${agents.map(a => html`
          <div key=${a.agent_key} class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3 flex-wrap">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-[14px] text-wa-text font-medium truncate">${a.display_name || a.agent_key}</span>
                <code class="text-[11px] text-wa-secondary">${a.agent_key}</code>
                ${a.enabled
                  ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-500/10 text-green-600">Ativo</span>`
                  : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Inativo</span>`}
                ${a.is_router ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-teal/10 text-wa-teal">router</span>` : null}
              </div>
              <div class="text-[12px] text-wa-secondary mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                <span>prompt: <span class="font-mono">${a.prompt_key || '—'}</span></span>
                <span>modelo: <span class="font-mono">${(a.model_config && a.model_config.model) || 'padrão'}</span></span>
                <span>tools: ${a.tool_names == null ? 'todas' : `${a.tool_names.length} selecionadas`}</span>
                <span>v${a.version || 1}</span>
              </div>
              ${a.description ? html`<div class="text-[12px] text-wa-secondary mt-1 break-words">${a.description}</div>` : null}
            </div>
            <div class="flex gap-1 shrink-0 flex-wrap justify-end">
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => { setEditing(a); setError(''); }}>Editar</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => openHistory(a)}>Histórico</button>
            </div>
          </div>
        `)}
      </div>

      ${historyFor ? html`
        <${HistoryModal}
          title=${`Histórico — ${historyFor.display_name || historyFor.agent_key}`}
          versions=${historyRows}
          current=${historyFor.version}
          busy=${historyBusy}
          onRollback=${handleRollback}
          onClose=${() => setHistoryFor(null)} />
      ` : null}
    </div>
  `;
}
