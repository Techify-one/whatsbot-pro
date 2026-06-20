// AI Engine — container screen for the "/ai" tab (plano 06). Holds the sub-tabs
// (Agentes, Prompts, Variáveis, Tools) and renders the matching editor below.
// Top note: there's no status endpoint for the ai_engine_enabled flag, so we
// just inform that changes take effect when the engine is enabled.

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import AgentsManager from './AgentsManager.js';
import PromptsEditor from './PromptsEditor.js';
import VariablesEditor from './VariablesEditor.js';
import ToolsEditor from './ToolsEditor.js';
import GeneralSettings from './GeneralSettings.js';
import { ToolsManager } from '../ToolsManager.js';
import { restartAi, getConfig, saveConfig } from '../../services/api.js';

const html = htm.bind(h);

const TABS = [
  { id: 'agents', label: 'Agentes' },
  { id: 'prompts', label: 'Prompts' },
  { id: 'variables', label: 'Variáveis' },
  { id: 'tools', label: 'Tools' },
  { id: 'general', label: 'Configurações' },
];

// Sub-views of the "Tools" tab. Consolidated here after the standalone
// "Gerenciar Tools" gear-menu entry was removed. "Registradas" governs every
// tool registered in the handler (core + plugin + installed code-in-DB):
// toggle on/off and override the description/label sent to the LLM — applies
// immediately, no restart. "Code-in-DB" is the advanced editor for tools whose
// Python code lives in the database.
const TOOLS_SUBTABS = [
  { id: 'registered', label: 'Registradas' },
  { id: 'code', label: 'Code-in-DB' },
];

function ToolsSection() {
  const [view, setView] = useState('registered');
  return html`
    <div>
      <div class="flex gap-2 mb-4">
        ${TOOLS_SUBTABS.map(s => html`
          <button key=${s.id}
            class="px-3 py-1.5 text-[13px] rounded-md border transition-colors ${view === s.id
              ? 'bg-wa-teal text-white border-wa-teal'
              : 'bg-wa-panel text-wa-secondary border-wa-border hover:text-wa-text'}"
            onClick=${() => setView(s.id)}>${s.label}</button>
        `)}
      </div>
      ${view === 'registered' ? html`<${ToolsManager} />` : html`<${ToolsEditor} />`}
    </div>
  `;
}

export default function AgentEngine() {
  const [tab, setTab] = useState('agents');
  const [restarting, setRestarting] = useState(false);
  const [restartMsg, setRestartMsg] = useState('');
  // ai_engine_enabled flag: null = unknown/loading.
  const [engineOn, setEngineOn] = useState(null);
  const [engineBusy, setEngineBusy] = useState(false);

  useEffect(() => {
    getConfig().then((res) => {
      if (res && res.ok) setEngineOn(!!res.data.ai_engine_enabled);
    });
  }, []);

  async function toggleEngine() {
    const next = !engineOn;
    if (!confirm(next
      ? 'Ativar o motor de IA (config-in-DB)? A IA passa a usar os agentes/prompts configurados aqui.'
      : 'Desativar o motor de IA? A IA volta ao comportamento padrão (prompt único das Configurações).')) return;
    setEngineBusy(true);
    const res = await saveConfig({ ai_engine_enabled: next });
    setEngineBusy(false);
    if (res && res.ok) setEngineOn(next);
    else alert((res && res.error) || 'Falha ao alterar o motor de IA.');
  }

  async function handleRestart() {
    if (!confirm('Reiniciar o worker agora? As mudanças em tools (código no banco) só valem após o restart.')) return;
    setRestarting(true);
    setRestartMsg('');
    const res = await restartAi();
    if (!res || !res.ok) {
      setRestartMsg((res && res.error) || 'Falha ao reiniciar.');
      setRestarting(false);
      return;
    }
    // The worker exits ~1.5s after this returns. Wait for it to go down and
    // come back, then reload so the browser fetches fresh assets (avoids the
    // stale-module SyntaxError that shows up mid-restart).
    setRestartMsg('Servidor reiniciando… a página recarrega sozinha quando o worker voltar.');
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    await sleep(2500);
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      try {
        const r = await fetch('/health', { cache: 'no-store' });
        if (r.ok) { location.reload(); return; }
      } catch (e) { /* server still down — keep polling */ }
      await sleep(1000);
    }
    setRestartMsg('O servidor está demorando para voltar. Recarregue a página manualmente.');
    setRestarting(false);
  }

  return html`
    <div>
      <div class="bg-wa-teal/10 border border-wa-teal/30 rounded-lg p-3 mb-4 flex items-start justify-between gap-3 flex-wrap">
        <div class="min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-[13px] text-wa-text font-medium">Motor de IA (config-in-DB)</span>
            ${engineOn === null
              ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">…</span>`
              : engineOn
                ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-500/10 text-green-600">Ativo</span>`
                : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Desligado</span>`}
          </div>
          <div class="text-[12px] text-wa-secondary mt-0.5">
            Configure agente, prompt, variáveis e tools sem deploy. As mudanças passam a
            valer quando o motor de IA estiver ativado (flag <code class="font-mono">ai_engine_enabled</code>).
            Edições de agente/prompt/variável valem na próxima mensagem; tools (código no banco)
            exigem reiniciar o worker.
          </div>
        </div>
        <div class="flex gap-2 shrink-0 flex-wrap">
          <button class="px-3 py-2 rounded-md text-[13px] border transition-colors disabled:opacity-50 ${engineOn
              ? 'text-wa-text border-wa-border hover:bg-wa-hover'
              : 'text-white bg-wa-teal border-wa-teal hover:opacity-90'}"
            onClick=${toggleEngine} disabled=${engineBusy || engineOn === null}>
            ${engineOn ? 'Desativar motor' : 'Ativar motor'}
          </button>
          <button class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors disabled:opacity-50"
            onClick=${handleRestart} disabled=${restarting}>
            ${restarting ? 'Reiniciando…' : 'Reiniciar worker'}
          </button>
        </div>
      </div>

      ${restarting ? html`
        <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div class="bg-wa-bg border border-wa-border rounded-lg p-6 text-center max-w-sm">
            <div class="text-[15px] font-medium text-wa-text mb-1">Servidor reiniciando…</div>
            <div class="text-[13px] text-wa-secondary">${restartMsg || 'Aguarde, a página recarrega sozinha quando o worker voltar.'}</div>
          </div>
        </div>
      ` : null}

      ${restartMsg && !restarting ? html`<div class="text-[13px] text-wa-secondary mb-3">${restartMsg}</div>` : null}

      <div class="flex gap-1 border-b border-wa-border mb-4 overflow-x-auto">
        ${TABS.map(t => html`
          <button key=${t.id}
            class="px-4 py-2 text-[14px] -mb-px border-b-2 transition-colors whitespace-nowrap ${tab === t.id
              ? 'border-wa-teal text-wa-teal font-medium'
              : 'border-transparent text-wa-secondary hover:text-wa-text'}"
            onClick=${() => setTab(t.id)}>${t.label}</button>
        `)}
      </div>

      ${tab === 'agents' ? html`<${AgentsManager} />` : null}
      ${tab === 'prompts' ? html`<${PromptsEditor} />` : null}
      ${tab === 'variables' ? html`<${VariablesEditor} />` : null}
      ${tab === 'tools' ? html`<${ToolsSection} />` : null}
      ${tab === 'general' ? html`<${GeneralSettings} />` : null}
    </div>
  `;
}
