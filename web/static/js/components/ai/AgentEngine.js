// AI Engine — container screen for the "/ai" tab (plano 06). Holds the sub-tabs
// (Agentes, Prompts, Variáveis, Tools) and renders the matching editor below.
// The engine (ai_engine_enabled) is always ON — there's no on/off toggle here.

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import AgentsManager from './AgentsManager.js';
import PromptsEditor from './PromptsEditor.js';
import VariablesEditor from './VariablesEditor.js';
import ToolsUnified from './ToolsUnified.js';
import GeneralSettings from './GeneralSettings.js';
import { entityPath } from '../../hooks/useDeepLink.js';

const html = htm.bind(h);

const TABS = [
  { id: 'agents', label: 'Agentes' },
  { id: 'prompts', label: 'Prompts' },
  { id: 'variables', label: 'Variáveis' },
  { id: 'tools', label: 'Tools' },
  { id: 'general', label: 'Configurações' },
];

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
      ${tab === 'tools' ? html`<${ToolsUnified} initialEntity=${initialEntity} />` : null}
      ${tab === 'general' ? html`<${GeneralSettings} />` : null}
    </div>
  `;
}
