// Shared permission checklist (RBAC). Used by the custom-user form and the role
// editor. Renders every permission from the catalog as a checkbox, grouped by
// domain (the part before the dot), with the pt-BR description. The permission
// `conversation.read_all` is flagged as currently inert (needs inbox membership,
// plano 01) so the operator knows toggling it has no effect yet.

import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// Permissions that exist in the catalog but are not enforced by any endpoint yet.
const INERT_PERMISSIONS = {
  'conversation.read_all': 'requer membership de inbox — sem efeito por enquanto',
};

function groupOf(key) {
  const i = key.indexOf('.');
  return i === -1 ? key : key.slice(0, i);
}

export default function PermissionPicker({ catalog, selected, onToggle, disabled }) {
  const sel = new Set(selected || []);
  const groups = {};
  for (const p of (catalog || [])) {
    (groups[groupOf(p.key)] = groups[groupOf(p.key)] || []).push(p);
  }
  const groupKeys = Object.keys(groups).sort();
  return html`
    <div class="flex flex-col gap-3">
      ${groupKeys.map(g => html`
        <div key=${g}>
          <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">${g}</div>
          <div class="flex flex-col gap-1">
            ${groups[g].map(p => html`
              <label key=${p.key}
                class="flex items-start gap-2 ${disabled ? 'opacity-60' : 'cursor-pointer'}">
                <input type="checkbox" class="mt-0.5" checked=${sel.has(p.key)}
                  disabled=${disabled}
                  onChange=${() => !disabled && onToggle(p.key)} />
                <span class="flex-1 min-w-0">
                  <span class="text-[13px] text-wa-text">${p.description || p.key}</span>
                  <span class="text-[11px] text-wa-secondary font-mono ml-1">${p.key}</span>
                  ${INERT_PERMISSIONS[p.key] ? html`
                    <span class="block text-[11px] text-amber-600">${INERT_PERMISSIONS[p.key]}</span>
                  ` : null}
                </span>
              </label>
            `)}
          </div>
        </div>
      `)}
    </div>
  `;
}
