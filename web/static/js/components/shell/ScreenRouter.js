// App-shell ScreenRouter (Plano 23 · D4), extracted verbatim from app.js's
// <main> render block. Resolves what to render for the active tab, in the SAME
// precedence order as before:
//   1. a plugin ROUTE OVERRIDE (overrideRoute, EXCLUSIVE — Q5 preserved)
//   2. an active PLUGIN SCREEN (config:false, mounted via PluginScreen)
//   3. a core screen (the big tab === '…' chain), default 'contacts' → Contacts,
//      final fallback → Sandbox.
// Behavior, PageHeader back targets, and every prop passed to each screen are
// unchanged. Layout wrappers are full-width (w-full p-4) so every core screen
// fills the viewport with no side gutters (was max-w-5xl mx-auto, centered/1024px).
import { h } from 'preact';
import htm from 'htm';
import { Dashboard } from '../Dashboard.js';
import { Sandbox } from '../Sandbox.js';
import { Contacts } from '../Contacts.js';
import ContactsListScreen from '../ContactsListScreen.js';
import ChannelsManager from '../ChannelsManager.js';
import { CostsDashboard } from '../CostsDashboard.js';
import { Executions } from '../Executions.js';
import { PluginsManager } from '../PluginsManager.js';
import { PluginScreen } from '../PluginScreen.js';
import QuickReplies from '../QuickReplies.js';
import CustomAttributesManager from '../CustomAttributesManager.js';
import RuntimePanel from '../RuntimePanel.js';
import UsersManager from '../UsersManager.js';
import AuditLog from '../AuditLog.js';
import AgentEngine from '../ai/AgentEngine.js';
import { PageHeader } from './GearMenu.js';
import { hasPermission } from '../../utils/permissions.js';

const html = htm.bind(h);

function PermissionDenied({ label = 'esta tela' }) {
  return html`
    <div class="w-full p-4">
      <div class="rounded-lg border border-wa-border bg-wa-bg p-4 text-[14px] text-wa-secondary">
        Você não tem permissão para acessar ${label}.
      </div>
    </div>`;
}

export function ScreenRouter({
  tab, setTab, activeRouteOverride, extensionsLoaded, activePluginScreen, currentUser,
  // Dashboard / config
  config, saving, handleSave, handleNotify, openWizard,
  // entity deep-link resolver: entFor(tab) → entity|null
  entFor,
  // Contacts (the chat hub) — full WS state bundle, unchanged
  contactsProps,
  // PluginsManager — re-fetch manifest callback (preserved)
  onPluginsChanged,
}) {
  if (activeRouteOverride) {
    // A plugin claimed (overrode) a CORE route — render its component instead of
    // the native screen.
    return html`<div class="mx-auto p-4 max-w-none">
        <${activeRouteOverride.component}
          apiBase=${`/api/plugins/${activeRouteOverride.pluginId}`}
          tab=${tab}
          setTab=${setTab}
        />
      </div>`;
  }
  if (activePluginScreen) {
    return html`<div class="w-full p-4">
        <${PageHeader} title=${activePluginScreen.title} onBack=${() => setTab('contacts')} />
        <${PluginScreen} screen=${activePluginScreen} currentUser=${currentUser} />
      </div>`;
  }
  if (tab === 'quick-replies') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Respostas Rápidas" onBack=${() => setTab('contacts')} />
        <${QuickReplies} initialEntity=${entFor('quick-replies')} />
      </div>`;
  }
  if (tab === 'custom-attributes') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Atributos Personalizados" onBack=${() => setTab('contacts')} />
        <${CustomAttributesManager} initialEntity=${entFor('custom-attributes')} />
      </div>`;
  }
  if (tab === 'runtime') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Runtime" onBack=${() => setTab('contacts')} />
        <${RuntimePanel} />
      </div>`;
  }
  if (tab === 'users') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Usuários" onBack=${() => setTab('contacts')} />
        <${UsersManager} initialEntity=${entFor('users')} />
      </div>`;
  }
  if (tab === 'audit') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Auditoria" onBack=${() => setTab('contacts')} />
        <${AuditLog} currentUser=${currentUser} />
      </div>`;
  }
  if (tab === 'ai') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Configurações de IA" onBack=${() => setTab('contacts')} />
        <${AgentEngine} initialEntity=${entFor('ai')} currentUser=${currentUser} />
      </div>`;
  }
  if (tab === 'plugins') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Plugins" onBack=${() => setTab('contacts')} />
        <${PluginsManager} initialEntity=${entFor('plugins')} onPluginsChanged=${onPluginsChanged} />
      </div>`;
  }
  if (tab === 'dashboard') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Configurações Gerais" onBack=${() => setTab('contacts')} />
        <${Dashboard}
          config=${config}
          saving=${saving}
          onSave=${handleSave}
          onNotify=${handleNotify}
          onReopenSetup=${openWizard}
          currentUser=${currentUser}
        />
      </div>`;
  }
  if (tab === 'contacts') {
    if (!hasPermission(currentUser, 'conversation.read')) {
      return html`<${PermissionDenied} label="as conversas" />`;
    }
    return html`<${Contacts} ...${contactsProps} />`;
  }
  if (tab === 'costs') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Custos de IA" onBack=${() => setTab('contacts')} />
        <${CostsDashboard} />
      </div>`;
  }
  if (tab === 'executions') {
    return html`<div class="w-full p-4 h-full">
        <${PageHeader} title="Execuções" onBack=${() => {
          if (window.location.pathname.match(/^\/executions\/\d+$/)) {
            history.pushState(null, '', '/executions');
            window.dispatchEvent(new PopStateEvent('popstate'));
          } else {
            setTab('contacts');
          }
        }} />
        <${Executions} />
      </div>`;
  }
  if (tab === 'contatos') {
    if (!hasPermission(currentUser, 'contact.read')) {
      return html`<${PermissionDenied} label="a tela de contatos" />`;
    }
    return html`<div class="w-full p-4">
        <${PageHeader} title="Contatos" onBack=${() => setTab('contacts')} />
        <${ContactsListScreen} initialEntity=${entFor('contatos')} currentUser=${currentUser} />
      </div>`;
  }
  if (tab === 'attendances') {
    // The native attendance screen was removed: this route only exists while a
    // plugin claims it through overrideRoute().  Wait for extension discovery so
    // a hard reload never flashes Sandbox before the claim is registered; when
    // no plugin claims it, return to the conversations hub.
    if (!extensionsLoaded) return null;
    queueMicrotask(() => setTab('contacts'));
    return null;
  }
  if (tab === 'channels') {
    return html`<div class="w-full p-4">
        <${PageHeader} title="Canais" onBack=${() => setTab('contacts')} />
        <${ChannelsManager} initialEntity=${entFor('channels')} />
      </div>`;
  }
  return html`<${Sandbox} newMessage=${contactsProps.newMessage} />`;
}
