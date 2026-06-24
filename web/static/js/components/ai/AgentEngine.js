// AI Engine — container screen for the "/ai" tab (plano 06). Holds the sub-tabs
// (Agentes, Prompts, Variáveis, Tools) and renders the matching editor below.
// The engine (ai_engine_enabled) is always ON — there's no on/off toggle here.

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import AgentsManager from './AgentsManager.js';
import PromptsEditor from './PromptsEditor.js';
import VariablesEditor from './VariablesEditor.js';
import ToolsEditor from './ToolsEditor.js';
import GeneralSettings from './GeneralSettings.js';
import { ToolsManager } from '../ToolsManager.js';
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

  // Deep-link de sub-aba: back/forward (ou reload) leva à sub-aba da URL.
  useEffect(() => {
    if (initialEntity?.sub) setTab(initialEntity.sub);
  }, [initialEntity]);

  return html`
    <div>
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
