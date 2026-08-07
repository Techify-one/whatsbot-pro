// AI Engine — container screen for the "/ai" tab (plano 06). Holds the sub-tabs
// (Agentes, Variáveis, Tools, Configurações) and renders the matching editor below.
// O prompt deixou de ser uma aba/recurso reutilizável: cada agente tem seu próprio
// prompt inline, editado no formulário do agente (aba Agentes).
// Plano 22: there is a single AI engine (config-in-DB) — no on/off toggle. Only
// the "Reiniciar worker" action remains (needed for code-in-DB tools).

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import AgentsManager from './AgentsManager.js';
import VariablesEditor from './VariablesEditor.js';
import ToolsUnified from './ToolsUnified.js';
import GeneralSettings from './GeneralSettings.js';
import { restartAi } from '../../services/api.js';
import { entityPath } from '../../hooks/useDeepLink.js';
import { hasPermission, hasAnyPermission } from '../../utils/permissions.js';

// Permissões granulares de IA — cada sub-aba exige sua chave (substituíram o
// antigo agent.manage). "Agentes" cobre config OU qualquer permissão de prompt
// (editar/versionar/apagar); "Configurações" é a config global (settings).
const TAB_PERMS = {
  agents: ['agent.config.manage', 'agent.create', 'agent.duplicate',
    'agent.prompts.edit', 'agent.prompts.version', 'agent.prompts.delete'],
  variables: ['agent.variables.manage'],
  tools: ['agent.tools.manage'],
  general: ['settings.manage'],
};

const html = htm.bind(h);

const TABS = [
  { id: 'agents', label: 'Agentes' },
  { id: 'variables', label: 'Variáveis' },
  { id: 'tools', label: 'Tools' },
  { id: 'general', label: 'Configurações' },
];

const VALID_TABS = new Set(TABS.map(t => t.id));

export default function AgentEngine({ initialEntity, currentUser }) {
  const can = (k) => hasPermission(currentUser, k);
  const allowedTabs = TABS.filter(t => hasAnyPermission(currentUser, TAB_PERMS[t.id]));
  const allowedIds = new Set(allowedTabs.map(t => t.id));
  const firstAllowed = allowedTabs[0]?.id || 'agents';
  const subAllowed = (sub) =>
    VALID_TABS.has(sub) && hasAnyPermission(currentUser, TAB_PERMS[sub]);

  // Sub-aba ativa: inicia da URL (/ai/<sub>) e segue back/forward. Sub-abas
  // removidas (bookmarks antigos) ou sem permissão caem na 1ª aba permitida.
  const [tab, setTab] = useState(() =>
    subAllowed(initialEntity?.sub) ? initialEntity.sub : firstAllowed);
  const [restarting, setRestarting] = useState(false);
  const [restartMsg, setRestartMsg] = useState('');

  // Deep-link de sub-aba: back/forward (ou reload) leva à sub-aba da URL.
  useEffect(() => {
    if (subAllowed(initialEntity?.sub)) setTab(initialEntity.sub);
  }, [initialEntity]);

  // Se a aba ativa deixar de ser permitida (currentUser carregou depois), cai
  // na primeira permitida.
  useEffect(() => {
    if (!allowedIds.has(tab)) setTab(firstAllowed);
  }, [currentUser, tab]);

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
          <div class="text-[13px] text-wa-text font-medium">Configurações de IA</div>
          <div class="text-[12px] text-wa-secondary mt-0.5">
            Configure agente, prompt, variáveis e tools sem deploy. Edições de
            agente/prompt/variável valem na próxima mensagem; tools (código no banco)
            exigem reiniciar o worker.
          </div>
        </div>
        <div class="flex gap-2 shrink-0 flex-wrap">
          ${can('agent.tools.manage') ? html`
            <button class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors disabled:opacity-50"
              onClick=${handleRestart} disabled=${restarting}>
              ${restarting ? 'Reiniciando…' : 'Reiniciar worker'}
            </button>
          ` : null}
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
        ${allowedTabs.map(t => html`
          <!-- Plano 106 · F4 (B6): sub-aba é navegação → <a href> (Ctrl/⌘, clique do
               meio e botão direito de graça). O onClick abaixo fica inalterado. -->
          <a key=${t.id}
            href=${entityPath('ai', { sub: t.id })}
            class="px-4 py-2 text-[14px] -mb-px border-b-2 transition-colors whitespace-nowrap no-underline ${tab === t.id
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
            }}>${t.label}</a>
        `)}
      </div>

      ${tab === 'agents' && allowedIds.has('agents') ? html`<${AgentsManager} initialEntity=${initialEntity} currentUser=${currentUser} />` : null}
      ${tab === 'variables' && allowedIds.has('variables') ? html`<${VariablesEditor} initialEntity=${initialEntity} />` : null}
      ${tab === 'tools' && allowedIds.has('tools') ? html`<${ToolsUnified} initialEntity=${initialEntity} />` : null}
      ${tab === 'general' && allowedIds.has('general') ? html`<${GeneralSettings} />` : null}
    </div>
  `;
}
