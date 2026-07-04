// AI Engine — agents editor (plano 06). Lists, CREATES, edits, duplicates and
// deletes agents: display_name, prompt (texto inline, próprio de cada agente —
// não reutilizável), model (select de /api/models), model_config
// (temperature/top_p/max_tokens → objeto), tool_names (todas|null ou multiselect
// do registry completo — core + plugin + code-in-DB), enabled, description,
// is_router toggle e routing_targets (multiselect de agent_keys quando router).
// Criar um agente = mesmo PUT do editar (upsert no backend), só com uma agent_key
// nova. Cada save bumpa a versão; o botão Histórico lista versões com Reverter
// (incluindo o prompt). O agente `default` é o fallback do motor e não pode ser
// excluído.

import { h } from 'preact';
import { useEffect, useMemo, useState, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  listAgents,
  saveAgent,
  deleteAgent,
  getAgentHistory,
  rollbackAgent,
  listRegisteredTools,
} from '../../services/api.js';
import { ModelSelect } from '../ModelSelect.js';
import { MarkdownEditor } from '../MarkdownEditor.js';
import { useDeepLink } from '../../hooks/useDeepLink.js';
import { useUrlState } from '../../hooks/useUrlState.js';
import { readParams, writeParams, bool } from '../../services/urlState.js';
import PromptHistoryModal from './PromptHistoryModal.js';

const html = htm.bind(h);

// Deep-link (Plano 24) — flags de query ADITIVAS sobre o path /ai/agents/{key}
// (o path já é dono do agente aberto via useDeepLink). Só refletem o modal aberto.
const AGENT_URL_SCHEMA = [
  bool('history'),         // ?history=1 → modal de histórico do agente
  bool('prompt-history'),  // ?prompt-history=1 → modal de trilha de prompt
];

// Strip common markdown markup so the card preview shows clean, readable text
// instead of raw syntax (**bold**, # heading, [link](url), `code`, lists, …).
function stripMarkdown(body) {
  return (body || '')
    .replace(/```[\s\S]*?```/g, ' ')          // fenced code blocks
    .replace(/`([^`]+)`/g, '$1')              // inline code
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')    // images
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')  // links → link text
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')       // headings
    .replace(/^\s{0,3}>\s?/gm, '')            // blockquotes
    .replace(/^\s{0,3}([-*+]|\d+\.)\s+/gm, '') // list markers
    .replace(/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/gm, ' ') // horizontal rules
    .replace(/(\*\*|__)(.*?)\1/g, '$2')       // bold
    .replace(/(\*|_)(.*?)\1/g, '$2')          // italic
    .replace(/~~(.*?)~~/g, '$1')              // strikethrough
    .replace(/\s+/g, ' ')                      // collapse whitespace
    .trim();
}

// Extract {placeholder} tokens from the prompt body for a small preview chip list
// (resolved from the Variáveis tab at render time).
function extractPlaceholders(body) {
  const out = [];
  const seen = new Set();
  const re = /\{([a-zA-Z0-9_]+)\}/g;
  let m;
  while ((m = re.exec(body || '')) !== null) {
    if (!seen.has(m[1])) { seen.add(m[1]); out.push(m[1]); }
  }
  return out;
}

// agent_key é IDENTIDADE — não pode ser renomeada depois (quebra executions.agent_key
// e o histórico de usage). Mesmo padrão de prompts/tools.
const KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;

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

// ── Agent form (create + edit) ──────────────────────────────────────
// `isNew` toggles the agent_key field. For "create" the parent passes a blank
// template ({}); for "duplicate" it passes an existing agent's config with the
// key stripped, so every field comes pre-filled but the user picks a new key.
function AgentForm({ isNew, agent, existingKeys, tools, onSave, onCancel, busy, onOpenPromptHistory }) {
  const mc = agent.model_config || {};
  const [key, setKey] = useState('');
  const [displayName, setDisplayName] = useState(agent.display_name || '');
  const [prompt, setPrompt] = useState(agent.prompt || '');
  // Commit message for the prompt trail — ephemeral, not rehydrated from the agent.
  const [changeNote, setChangeNote] = useState('');
  const placeholders = useMemo(() => extractPlaceholders(prompt), [prompt]);
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
  // Save flow: split button ("Salvar" = nova versão / caret = sobrescrever) gated
  // by a confirmation modal. pendingMode ∈ {null, 'new', 'amend'}; menuOpen = caret.
  const [pendingMode, setPendingMode] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);

  // Quando o pai troca o agente em edição por outra revisão (ex.: restaurar uma
  // versão do prompt, que volta com a versão bumpada), re-sincroniza os campos do
  // formulário — senão os useState ficam presos no valor da 1ª montagem e o
  // textarea continua mostrando o prompt antigo até um reload da página.
  const agentRev = `${agent.agent_key || ''}:v${agent.version || 1}`;
  const lastRevRef = useRef(agentRev);
  useEffect(() => {
    if (lastRevRef.current === agentRev) return; // mesma revisão → não pisa edição em andamento
    lastRevRef.current = agentRev;
    const mc2 = agent.model_config || {};
    setDisplayName(agent.display_name || '');
    setPrompt(agent.prompt || '');
    setModel(mc2.model || '');
    setTemperature(numField(mc2.temperature));
    setTopP(numField(mc2.top_p));
    setMaxTokens(numField(mc2.max_tokens));
    setEnabled(agent.enabled !== false);
    setDescription(agent.description || '');
    setIsRouter(!!agent.is_router);
    setAllTools(agent.tool_names == null);
    setToolNames(Array.isArray(agent.tool_names) ? [...agent.tool_names] : []);
    setRoutingTargets(Array.isArray(agent.routing_targets) ? [...agent.routing_targets] : []);
    setChangeNote('');
  }, [agentRev]);

  const trimmedKey = key.trim();
  const keyFormatErr = isNew && trimmedKey && !KEY_RE.test(trimmedKey)
    ? 'Use minúsculas, números e _ (começando por letra).' : '';
  const keyCollision = isNew && trimmedKey && (existingKeys || []).includes(trimmedKey)
    ? 'Já existe um agente com essa chave.' : '';
  const keyErr = keyFormatErr || keyCollision;
  // The default agent is the canonical "global" (plano 20) — its model must never
  // be empty (it is the fallback every other agent inherits). Other agents may
  // leave it blank to fall back to the app default.
  const isDefault = !isNew && agent.agent_key === 'default';

  function toggleTool(name) {
    setToolNames(prev => prev.includes(name) ? prev.filter(t => t !== name) : [...prev, name]);
  }
  function toggleTarget(k) {
    setRoutingTargets(prev => prev.includes(k) ? prev.filter(t => t !== k) : [...prev, k]);
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

  const canSave = !busy && displayName.trim()
    && (isNew ? (trimmedKey && !keyErr) : true)
    && (!isDefault || !!model.trim());

  // Step 1 — a save button opens the confirmation modal for that mode.
  function requestSave(mode) {
    if (!canSave) return;
    setMenuOpen(false);
    setPendingMode(mode);
  }

  // Step 2 — confirm: actually persist with the chosen version_mode.
  function confirmSave() {
    if (!canSave || !pendingMode) return;
    const finalKey = isNew ? trimmedKey : agent.agent_key;
    onSave(finalKey, {
      display_name: displayName.trim(),
      prompt: prompt,
      change_note: changeNote.trim() || null,
      version_mode: pendingMode,
      model_config: buildModelConfig(),
      tool_names: allTools ? null : toolNames,
      enabled,
      description: description.trim(),
      is_router: isRouter,
      routing_targets: isRouter ? routingTargets : null,
    });
    setChangeNote('');
    setPendingMode(null);
  }

  // Routing targets: other agents only (can't route to itself).
  const selfKey = isNew ? trimmedKey : agent.agent_key;
  const otherAgents = (window.__aiAgentsCache || []).filter(a => a.agent_key !== selfKey);
  // Único roteador (plano 29 Eixo B): salvar este como roteador rebaixa o atual.
  const currentRouter = otherAgents.find(a => a.is_router);

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${isNew
          ? 'Novo agente'
          : html`Editar agente <code class="text-[12px] text-wa-secondary">${agent.agent_key}</code>
              <span class="text-[12px] text-wa-secondary font-normal"> · v${agent.version || 1}</span>`}
      </div>
      <div class="flex flex-col gap-3">
        ${isNew ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Chave (agent_key) — não pode mudar depois</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono"
              type="text" placeholder="ex: financeiro" value=${key}
              onInput=${(e) => setKey(e.target.value)} />
            ${keyErr ? html`<div class="text-[12px] text-red-500 mt-1">${keyErr}</div>` : null}
          </div>
        ` : null}

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" value=${displayName} onInput=${(e) => setDisplayName(e.target.value)} />
        </div>

        <div>
          <div class="flex items-center justify-between mb-1 gap-2">
            <label class="block text-[12px] text-wa-secondary">Prompt do agente</label>
            <button type="button"
              class="px-2 py-1 rounded-md text-[11px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50 shrink-0"
              disabled=${isNew}
              title=${isNew ? 'Salve o agente para começar a versionar o prompt.' : 'Ver versões do prompt'}
              onClick=${() => onOpenPromptHistory && onOpenPromptHistory()}>Versões do prompt</button>
          </div>
          <${MarkdownEditor}
            value=${prompt}
            onChange=${setPrompt}
            placeholder="Escreva a personalidade e as instruções deste agente. Use {variavel} para inserir valores das Variáveis." />
          <div class="text-[11px] text-wa-secondary mt-1">
            Edite o texto já formatado (negrito, listas, títulos) usando a barra de ferramentas — não precisa escrever markdown na mão. Cada agente tem seu próprio prompt (não é compartilhado com outros agentes). Se ficar vazio, o agente usa o prompt padrão do app.
          </div>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[13px] mt-2"
            type="text" placeholder="Descreva a mudança do prompt (opcional)"
            value=${changeNote} onInput=${(e) => setChangeNote(e.target.value)} />
          ${placeholders.length > 0 ? html`
            <div class="mt-2">
              <div class="text-[11px] text-wa-secondary mb-1">Placeholders detectados (resolvidos pela aba <span class="font-medium">Variáveis</span>)</div>
              <div class="flex flex-wrap gap-1">
                ${placeholders.map(p => html`
                  <span key=${p} class="px-2 py-0.5 rounded-full text-[11px] bg-wa-teal/10 text-wa-teal font-mono">{${p}}</span>
                `)}
              </div>
            </div>
          ` : null}
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Modelo${isDefault ? ' (obrigatório)' : ''}</label>
          <${ModelSelect}
            value=${model}
            onChange=${setModel}
            placeholder=${isDefault ? 'Selecione o modelo' : '— padrão do app —'}
            allowEmpty=${!isDefault}
            emptyLabel="— padrão do app —"
          />
          ${isDefault ? html`<div class="text-[11px] text-wa-secondary mt-1">O agente padrão é o modelo global do app — não pode ficar vazio.</div>` : null}
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
        <div class="text-[11px] text-wa-secondary -mt-1">
          max_tokens: modelos de raciocínio gastam esse orçamento para "pensar" antes de responder —
          valores baixos podem zerar a resposta. Piso aplicado: 256 (1024 quando reasoning_effort está definido).
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
                ? html`<div class="text-[12px] text-wa-secondary">Nenhuma tool registrada.</div>`
                : (tools || []).map(t => html`
                  <label key=${t.name} class="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked=${toolNames.includes(t.name)} onChange=${() => toggleTool(t.name)} />
                    <span class="text-[14px] text-wa-text font-mono">${t.name}</span>
                    ${t.current_label ? html`<span class="text-[12px] text-wa-secondary truncate">${t.current_label}</span>` : null}
                    ${t.plugin_id ? html`<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-wa-teal/10 text-wa-teal shrink-0">${t.plugin_id}</span>` : null}
                  </label>
                `)}
            </div>
            <div class="text-[11px] text-wa-secondary mt-1">
              Lista todas as tools registradas (core, plugin e code-in-DB). Deixe "Todas" marcado para herdar o registry inteiro.
            </div>
          ` : null}
          ${!isRouter && !allTools && toolNames.includes('transfer_to_human') ? html`
            <div class="text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 mt-2">
              Convenção hub-and-spoke: deixe <code>transfer_to_human</code> apenas no <b>roteador</b>.
              Agentes especializados devolvem com <code>transferir_agente</code> (destino: o roteador) e ele decide escalar.
            </div>
          ` : null}
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Descrição</label>
          <textarea class="wa-field w-full px-3 py-2 rounded-md text-[14px] resize-y" rows="2"
            value=${description} onInput=${(e) => setDescription(e.target.value)}></textarea>
          <div class="text-[11px] text-wa-secondary mt-1">
            Usada pelo roteador para decidir o destino da transferência — escreva curto e objetivo
            (ex.: "Vendas e planos", "Suporte técnico de instalação").
          </div>
        </div>

        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked=${enabled} onChange=${(e) => setEnabled(e.target.checked)} />
          <span class="text-[14px] text-wa-text">Agente ativo</span>
        </label>

        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked=${isRouter} onChange=${(e) => setIsRouter(e.target.checked)} />
          <span class="text-[14px] text-wa-text">É roteador (handoff/routing)</span>
        </label>

        ${isRouter && currentRouter ? html`
          <div class="text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
            Só pode existir <b>um</b> roteador. Ao salvar, o roteador atual
            (<b>${currentRouter.display_name || currentRouter.agent_key}</b>) deixará de ser roteador.
          </div>
        ` : null}

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

        <div class="flex gap-2 justify-end items-center">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          ${isNew ? html`
            <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
              onClick=${() => requestSave('amend')} disabled=${!canSave}>${busy ? 'Salvando…' : 'Salvar'}</button>
          ` : html`
            <div class="relative flex">
              <button class="px-4 py-2 rounded-l-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
                onClick=${() => requestSave('amend')} disabled=${!canSave}>${busy ? 'Salvando…' : 'Salvar'}</button>
              <button class="px-2 py-2 rounded-r-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50 border-l border-white/25"
                title="Mais opções de salvamento" onClick=${() => setMenuOpen(o => !o)} disabled=${!canSave}>▾</button>
              ${menuOpen ? html`
                <div class="fixed inset-0 z-30" onClick=${() => setMenuOpen(false)}></div>
                <div class="absolute right-0 top-full mt-1 z-40 w-72 bg-wa-bg border border-wa-border rounded-md shadow-lg overflow-hidden">
                  <button class="w-full text-left px-3 py-2 text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                    onClick=${() => requestSave('new')}>
                    Salvar criando uma nova versão
                    <div class="text-[11px] text-wa-secondary">Registra uma nova versão no histórico do prompt</div>
                  </button>
                </div>
              ` : null}
            </div>
          `}
        </div>
      </div>

      ${pendingMode ? html`
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick=${() => !busy && setPendingMode(null)}>
          <div class="bg-wa-bg border border-wa-border rounded-lg w-full max-w-md flex flex-col"
            onClick=${(e) => e.stopPropagation()}>
            <div class="px-5 py-3 border-b border-wa-border text-[15px] font-medium text-wa-text">
              ${isNew
                ? 'Criar agente'
                : (pendingMode === 'amend' ? 'Sobrescrever a versão atual' : 'Salvar e criar nova versão')}
            </div>
            <div class="px-5 py-4 text-[13px] text-wa-text">
              ${isNew
                ? html`Isso cria o agente e registra a <span class="font-medium">primeira versão</span> do prompt no histórico.`
                : (pendingMode === 'amend'
                  ? html`Isso salva o agente e <span class="font-medium">sobrescreve a última versão</span> do prompt no histórico, sem criar uma nova. O texto anterior dessa versão será <span class="font-medium">substituído e não poderá ser recuperado</span>.`
                  : html`Isso salva o agente e registra uma <span class="font-medium">nova versão</span> do prompt no histórico, preservando as versões anteriores.`)}
            </div>
            <div class="px-5 py-3 border-t border-wa-border flex justify-end gap-2">
              <button class="px-3 py-2 rounded-md text-[13px] text-wa-text bg-wa-panel border border-wa-border hover:bg-wa-hover transition-colors disabled:opacity-50"
                disabled=${busy} onClick=${() => setPendingMode(null)}>Cancelar</button>
              <button class=${`px-3 py-2 rounded-md text-[13px] text-white hover:opacity-90 transition-opacity disabled:opacity-50 ${pendingMode === 'amend' ? 'bg-amber-600' : 'bg-wa-teal'}`}
                disabled=${busy} onClick=${confirmSave}>
                ${busy
                  ? 'Salvando…'
                  : (isNew ? 'Criar agente' : (pendingMode === 'amend' ? 'Sobrescrever' : 'Criar versão'))}
              </button>
            </div>
          </div>
        </div>
      ` : null}
    </div>
  `;
}

export default function AgentsManager({ initialEntity }) {
  const [agents, setAgents] = useState([]);
  const [tools, setTools] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  // creating: null = closed; true = blank new agent; object = duplicate seed.
  const [creating, setCreating] = useState(null);
  const [busy, setBusy] = useState(false);
  // History modal state
  const [historyFor, setHistoryFor] = useState(null);
  const [historyRows, setHistoryRows] = useState([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  // Dedicated prompt-trail modal state (separate from the whole-agent history)
  const [promptHistoryFor, setPromptHistoryFor] = useState(null);

  const formRef = useRef(null);
  useEffect(() => {
    if (editing || creating) {
      formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [editing, creating]);

  async function load() {
    setLoading(true);
    setError('');
    const [aRes, tRes] = await Promise.all([
      listAgents(), listRegisteredTools(),
    ]);
    if (aRes && aRes.ok) {
      const list = aRes.data || [];
      setAgents(list);
      window.__aiAgentsCache = list;  // used by the form for routing targets
    } else {
      setError((aRes && aRes.error) || 'Falha ao carregar agentes.');
    }
    if (tRes && tRes.ok) setTools(tRes.data || []);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  // Deep-link /ai/agents/<agent_key>: reabre o agente da URL e reflete o aberto.
  const pushUrl = useDeepLink({
    tab: 'ai',
    resolve: initialEntity && initialEntity.sub === 'agents'
      ? { sub: 'agents', id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel || sel.id == null) { setEditing(null); return; }
      const a = agents.find(x => x.agent_key === sel.id);
      if (a) { setEditing(a); setCreating(null); }
    },
  });
  // Reflete na URL todo abrir/fechar do editor num só ponto (cobre cancelar,
  // salvar, duplicar e excluir). Pula o 1º render p/ não atropelar o deep-link
  // de entrada antes de a lista carregar.
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return; }
    pushUrl(editing ? { sub: 'agents', id: editing.agent_key } : { sub: 'agents' });
  }, [editing]);

  // Reflete os modais de histórico na query (?history / ?prompt-history), aditivo
  // ao path do agente. Ao hidratar/voltar, reabre o modal contra o agente já
  // resolvido pelo path (editing) — reusa openHistory (carrega historyRows) e o
  // mesmo setPromptHistoryFor do botão "Versões do prompt".
  useUrlState({
    read: () => readParams(window.location.search, AGENT_URL_SCHEMA),
    apply: (s) => {
      if (s.history) { if (editing && !historyFor) openHistory(editing); }
      else if (historyFor) setHistoryFor(null);
      if (s['prompt-history']) { if (editing && !promptHistoryFor) setPromptHistoryFor(editing); }
      else if (promptHistoryFor) setPromptHistoryFor(null);
    },
    serialize: () => writeParams({
      history: !!historyFor,
      'prompt-history': !!promptHistoryFor,
    }, AGENT_URL_SCHEMA),
    deps: [historyFor, promptHistoryFor, editing],
  });
  // Corrida de montagem: o hydrate do useUrlState roda no mount, mas o agente do
  // path (editing) só resolve depois da lista carregar. Quando editing aparecer,
  // reabre o modal pedido pela URL uma única vez (não repete em cada troca).
  const flagsHydratedRef = useRef(false);
  useEffect(() => {
    if (flagsHydratedRef.current || !editing) return;
    flagsHydratedRef.current = true;
    const s = readParams(window.location.search, AGENT_URL_SCHEMA);
    if (s.history && !historyFor) openHistory(editing);
    if (s['prompt-history'] && !promptHistoryFor) setPromptHistoryFor(editing);
  }, [editing]);

  async function handleSave(key, data) {
    setBusy(true); setError('');
    const res = await saveAgent(key, data);
    setBusy(false);
    if (res && res.ok) { setEditing(null); setCreating(null); load(); }
    else setError((res && res.error) || 'Falha ao salvar o agente.');
  }

  function startDuplicate(a) {
    // Strip identity / versioning; keep the config so every field is pre-filled.
    const { agent_key, version, updated_at, ...rest } = a;
    setCreating({ ...rest, display_name: `${a.display_name || a.agent_key} (cópia)` });
    setEditing(null); setError('');
  }

  async function handleDelete(a) {
    if (a.agent_key === 'default') return;
    if (!confirm(`Excluir o agente "${a.display_name || a.agent_key}"? Conversas vinculadas voltam ao agente padrão. Esta ação não pode ser desfeita.`)) return;
    setError('');
    const res = await deleteAgent(a.agent_key);
    if (res && res.ok) {
      if (editing && editing.agent_key === a.agent_key) setEditing(null);
      load();
    } else {
      setError((res && res.error) || 'Falha ao excluir o agente.');
    }
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

  const formOpen = !!editing || !!creating;

  return html`
    <div>
      <div class="flex items-center justify-between mb-4 gap-2">
        <p class="text-[13px] text-wa-secondary">
          Agentes definem o prompt, o modelo e as tools que a IA usa. As mudanças valem
          na próxima mensagem (sem reiniciar). Um agente ativo já aparece para atribuir nas conversas.
        </p>
        ${!formOpen ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setEditing(null); setError(''); }}>+ Novo agente</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      <div ref=${formRef}>
        ${creating ? html`
          <${AgentForm} isNew=${true} agent=${creating === true ? {} : creating}
            existingKeys=${agents.map(a => a.agent_key)}
            tools=${tools}
            onSave=${handleSave} onCancel=${() => setCreating(null)} busy=${busy} />
        ` : null}

        ${editing ? html`
          <${AgentForm} isNew=${false} agent=${editing} existingKeys=${[]}
            tools=${tools}
            onSave=${handleSave} onCancel=${() => setEditing(null)} busy=${busy}
            onOpenPromptHistory=${() => setPromptHistoryFor(editing)} />
        ` : null}
      </div>

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && agents.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhum agente cadastrado. Clique em <span class="font-medium">+ Novo agente</span>.
        </div>
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
                ${a.agent_key === 'default' ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">padrão</span>` : null}
              </div>
              <div class="text-[12px] text-wa-secondary mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                <span>modelo: <span class="font-mono">${(a.model_config && a.model_config.model) || 'padrão'}</span></span>
                <span>tools: ${a.tool_names == null ? 'todas' : `${a.tool_names.length} selecionadas`}</span>
                <span>v${a.version || 1}</span>
              </div>
              ${a.prompt ? (() => { const p = stripMarkdown(a.prompt); return p ? html`<div class="text-[12px] text-wa-secondary mt-1 break-words line-clamp-2">${p.slice(0, 160)}${p.length > 160 ? '…' : ''}</div>` : null; })() : null}
              ${a.description ? html`<div class="text-[12px] text-wa-secondary mt-1 break-words">${a.description}</div>` : null}
            </div>
            <div class="flex gap-1 shrink-0 flex-wrap justify-end">
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => { setEditing(a); setCreating(null); setError(''); }}>Editar</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => startDuplicate(a)}>Duplicar</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => openHistory(a)}>Histórico</button>
              ${a.agent_key !== 'default' ? html`
                <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors"
                  onClick=${() => handleDelete(a)}>Excluir</button>
              ` : null}
            </div>
          </div>
        `)}
      </div>

      ${historyFor ? html`
        <${HistoryModal}
          title=${`Histórico do agente — ${historyFor.display_name || historyFor.agent_key}`}
          versions=${historyRows}
          current=${historyFor.version}
          busy=${historyBusy}
          onRollback=${handleRollback}
          onClose=${() => setHistoryFor(null)} />
      ` : null}

      ${promptHistoryFor ? html`
        <${PromptHistoryModal}
          agentKey=${promptHistoryFor.agent_key}
          agentLabel=${promptHistoryFor.display_name || promptHistoryFor.agent_key}
          currentVersion=${null}
          onClose=${() => setPromptHistoryFor(null)}
          onRestored=${(restored) => {
            setPromptHistoryFor(null);
            // Reflete o prompt restaurado no formulário aberto: troca o agente em
            // edição pela linha restaurada (com a versão bumpada), o que remonta o
            // AgentForm (via key) e reinicia o textarea com o prompt restaurado.
            if (restored && restored.agent_key) setEditing(restored);
            load();
          }} />
      ` : null}
    </div>
  `;
}
