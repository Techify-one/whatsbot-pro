// AI Engine — container screen for the "/ai" tab (plano 06). Holds the sub-tabs
// (Agentes, Prompts, Variáveis, Tools) and renders the matching editor below.
// Plano 22: there is a single AI engine (config-in-DB) — no on/off toggle. Only
// the "Reiniciar worker" action remains (needed for code-in-DB tools).

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import AgentsManager from './AgentsManager.js';
import PromptsEditor from './PromptsEditor.js';
import VariablesEditor from './VariablesEditor.js';
import ToolsEditor from './ToolsEditor.js';
import GeneralSettings from './GeneralSettings.js';
import { ToolsManager } from '../ToolsManager.js';
import { restartAi } from '../../services/api.js';
import { entityPath } from '../../hooks/useDeepLink.js';

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

function ToolsSection({ initialEntity }) {
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
      ${view === 'registered' ? html`<${ToolsManager} initialEntity=${initialEntity} />` : html`<${ToolsEditor} />`}
    </div>
  `;
}

export default function AgentEngine({ initialEntity }) {
  // Sub-aba ativa: inicia da URL (/ai/<sub>) e segue back/forward.
  const [tab, setTab] = useState(() => initialEntity?.sub || 'agents');
  const [restarting, setRestarting] = useState(false);
  const [restartMsg, setRestartMsg] = useState('');

  // Deep-link de sub-aba: back/forward (ou reload) leva à sub-aba da URL.
  useEffect(() => {
    if (initialEntity?.sub) setTab(initialEntity.sub);
  }, [initialEntity]);

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
      <div class="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div class="min-w-0">
          <div class="text-[13px] text-wa-text font-medium">Engine de IA</div>
          <div class="text-[12px] text-wa-secondary mt-0.5">
            Configure agente, prompt, variáveis e tools sem deploy. Edições de
            agente/prompt/variável valem na próxima mensagem; tools (código no banco)
            exigem reiniciar o worker.
          </div>
        </div>
        <div class="flex gap-2 shrink-0 flex-wrap">
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
            onClick=${() => {
              setTab(t.id);
              const p = entityPath('ai', { sub: t.id });
              if (window.location.pathname !== p) {
                // popstate (como o setTab do app.js) mantém o App.initialEntity em
                // sync com a URL — senão voltar à sub-aba reabriria a entidade antiga.
                history.pushState(null, '', p);
                window.dispatchEvent(new PopStateEvent('popstate'));
              }
            }}>${t.label}</button>
        `)}
      </div>

      ${tab === 'agents' ? html`<${AgentsManager} initialEntity=${initialEntity} />` : null}
      ${tab === 'prompts' ? html`<${PromptsEditor} initialEntity=${initialEntity} />` : null}
      ${tab === 'variables' ? html`<${VariablesEditor} initialEntity=${initialEntity} />` : null}
      ${tab === 'tools' ? html`<${ToolsSection} initialEntity=${initialEntity} />` : null}
      ${tab === 'general' ? html`<${GeneralSettings} />` : null}
    </div>
  `;
}
