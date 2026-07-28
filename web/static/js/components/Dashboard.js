// "Configurações Gerais" — host das ABAS da tela de configuração do core.
//
//   Geral         → ConfigPanel (avisos de sistema no chat) + "Refazer
//                   configuração".            perm: settings.general
//   Avançado      → ConfigPanel (domínio público, execuções, auditoria).
//                                             perm: settings.advanced
//   Notificações  → NotificationSettings (quais eventos avisam + notas privadas).
//                                             perm: settings.notifications
//   Sons          → SoundSettings (som/volume/duração + biblioteca importada).
//                                             SEM permissão — som é PESSOAL.
//
// `settings.manage` continua liberando tudo (é o super-conjunto). Quem não tem
// nenhuma das três só enxerga a aba Sons. Aba inicial: `?tab=<aba>`, o
// `?section=` legado do ConfigPanel ou o path legado `/sounds`.
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { ConfigPanel } from './ConfigPanel.js';
import SoundSettings from './SoundSettings.js';
import NotificationSettings from './NotificationSettings.js';
import { hasPermission } from '../utils/permissions.js';

const html = htm.bind(h);

// Aba → (rótulo, permissão exigida). `null` = aberta a qualquer atendente.
const TAB_DEFS = [
  ['geral', 'Geral', 'settings.general'],
  ['avancado', 'Avançado', 'settings.advanced'],
  ['notificacoes', 'Notificações', 'settings.notifications'],
  ['sons', 'Sons', null],
];

/** Abas visíveis para o usuário (settings.manage é super-conjunto). */
function allowedTabs(currentUser) {
  const canAll = hasPermission(currentUser, 'settings.manage');
  return TAB_DEFS.filter(([, , perm]) => !perm || canAll || hasPermission(currentUser, perm));
}

/** Aba pedida pela URL: `?tab=<aba>`, o `?section=` legado ou o path `/sounds`. */
function initialTab(tabs) {
  const ids = tabs.map(([id]) => id);
  try {
    const params = new URLSearchParams(window.location.search);
    const asked = params.get('tab');
    if (ids.includes(asked)) return asked;
    if (params.get('section') === 'avancado' && ids.includes('avancado')) return 'avancado';
    if (window.location.pathname === '/sounds') return 'sons';
  } catch (_) { /* fail-open */ }
  return ids[0];
}

function TabButton({ active, onClick, children }) {
  return html`
    <button
      type="button"
      onClick=${onClick}
      class=${`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
        active ? 'bg-wa-teal text-white' : 'text-wa-text hover:bg-wa-hover'}`}
    >${children}</button>
  `;
}

export function Dashboard({ config, saving, onSave, onNotify, onReopenSetup, currentUser }) {
  const tabs = allowedTabs(currentUser);
  const [tab, setTab] = useState(() => initialTab(tabs));
  // Uma permissão revogada entre renders não pode deixar a aba órfã visível.
  const active = tabs.some(([id]) => id === tab) ? tab : (tabs[0] || [])[0];

  const configPanel = (sections) => html`
    <${ConfigPanel}
      config=${config}
      saving=${saving}
      onSave=${onSave}
      onNotify=${onNotify}
      currentUser=${currentUser}
      sections=${sections}
    />`;

  return html`
    <div class="flex flex-col gap-4">
      ${tabs.length > 1 ? html`
        <div class="flex items-center gap-1 p-1 bg-wa-bg rounded-xl border border-wa-border w-fit flex-wrap">
          ${tabs.map(([id, label]) => html`
            <${TabButton} key=${id} active=${active === id} onClick=${() => setTab(id)}>${label}<//>
          `)}
        </div>
      ` : null}

      ${active === 'sons' ? html`
        <${SoundSettings} config=${config} onSaveConfig=${onSave} currentUser=${currentUser} />
      ` : active === 'notificacoes' ? html`
        <${NotificationSettings} config=${config} onSaveConfig=${onSave} currentUser=${currentUser} />
      ` : active === 'avancado' ? configPanel(['avancado']) : html`
        ${configPanel(['avisos'])}

        ${onReopenSetup ? html`
          <div class="flex justify-end">
            <button
              onClick=${onReopenSetup}
              class="px-4 py-2.5 text-[14px] rounded-lg border border-wa-border hover:bg-wa-hover transition-colors flex items-center gap-2 text-wa-text bg-wa-bg"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 3h-4.18C14.4 1.84 13.3 1 12 1c-1.3 0-2.4.84-2.82 2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 0c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1zm-2 14l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"/></svg>
              Refazer configuração
            </button>
          </div>
        ` : null}
      `}
    </div>
  `;
}
