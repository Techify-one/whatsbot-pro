// Tela "API e Webhooks" — as duas metades do plano de API num lugar só.
//
// `pull` (o integrador chama a gente, autenticando com uma chave) e `push` (a
// gente chama o integrador quando algo acontece) são o mesmo assunto para quem
// está integrando, e separá-las em dois itens de menu faria o operador procurar
// em dois lugares pela mesma coisa. Cada aba é gateada pela sua permissão.

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import ApiKeysManager from './ApiKeysManager.js';
import WebhooksManager from './WebhooksManager.js';
import { hasPermission } from '../utils/permissions.js';

const html = htm.bind(h);

export default function IntegrationsScreen({ currentUser }) {
  const canKeys = hasPermission(currentUser, 'apikey.manage');
  const canHooks = hasPermission(currentUser, 'webhook.manage');
  const [tab, setTab] = useState(canKeys ? 'keys' : 'webhooks');

  const TabButton = ({ id, children }) => html`
    <button
      class=${`px-3 py-1.5 text-[14px] rounded-t border-b-2 ${
        tab === id
          ? 'border-wa-teal text-wa-text font-medium'
          : 'border-transparent text-wa-secondary'}`}
      onClick=${() => setTab(id)}>${children}</button>`;

  if (!canKeys && !canHooks) {
    return html`
      <div class="rounded-lg border border-wa-border bg-wa-bg p-4 text-[14px] text-wa-secondary">
        Você não tem permissão para acessar as integrações.
      </div>`;
  }

  return html`
    <div class="space-y-4">
      <div class="flex gap-1 border-b border-wa-border">
        ${canKeys && html`<${TabButton} id="keys">Chaves de API</${TabButton}>`}
        ${canHooks && html`<${TabButton} id="webhooks">Webhooks de saída</${TabButton}>`}
      </div>
      ${tab === 'keys' && canKeys && html`<${ApiKeysManager} currentUser=${currentUser} />`}
      ${tab === 'webhooks' && canHooks && html`<${WebhooksManager} />`}
    </div>`;
}
