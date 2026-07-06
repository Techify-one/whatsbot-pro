// Shared permission checklist (RBAC). Used by the custom-user form and the role
// editor. Renders every permission from the catalog as a checkbox, organized in
// two tiers — "Sistema" (core) and "Plugins" — each split into groups
// (subheaders). The /api/roles catalog carries `tier` ("core"/"plugin") and
// `group` per item (see domain/permission_catalog.PERMISSION_GROUPS +
// rbac_repo.list_catalog). `template.*` is a core key but shows under Plugins.
// The permission `conversation.read_all` is flagged as currently inert (needs
// inbox membership, plano 01) so the operator knows toggling it has no effect.

import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// Permissions that exist in the catalog but are not enforced by any endpoint yet.
const INERT_PERMISSIONS = {
  'conversation.read_all': 'requer membership de inbox — sem efeito por enquanto',
};

// Ordem canônica dos subcabeçalhos core (espelha CORE_GROUP_ORDER no backend).
const CORE_GROUP_ORDER = [
  'Atendimentos e conversas',
  'Contatos e etiquetas',
  'Canais e inboxes',
  'IA e agente',
  'Configurações e sistema',
  'Usuários e auditoria',
  'Outros',
];

function tierOf(p) {
  return p.tier || (p.plugin_id ? 'plugin' : 'core');
}

function groupOf(p) {
  if (p.group) return p.group;
  if (p.plugin_id) return p.group_label || p.plugin_id;
  const i = p.key.indexOf('.');
  return i === -1 ? p.key : p.key.slice(0, i);
}

// Ordena grupos: core pela ordem canônica (extras alfabéticos ao fim); plugin
// sempre alfabético.
function sortGroups(tier, names) {
  return names.slice().sort((a, b) => {
    if (tier === 'core') {
      const ia = CORE_GROUP_ORDER.indexOf(a), ib = CORE_GROUP_ORDER.indexOf(b);
      const ra = ia === -1 ? CORE_GROUP_ORDER.length : ia;
      const rb = ib === -1 ? CORE_GROUP_ORDER.length : ib;
      if (ra !== rb) return ra - rb;
    }
    return a.localeCompare(b);
  });
}

function GroupBlock({ name, perms, sel, disabled, onToggle }) {
  return html`
    <div key=${name}>
      <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-1">${name}</div>
      <div class="flex flex-col gap-1">
        ${perms.map(p => html`
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
  `;
}

export default function PermissionPicker({ catalog, selected, onToggle, disabled }) {
  const sel = new Set(selected || []);

  // tier -> group -> [perms]
  const tiers = { core: {}, plugin: {} };
  for (const p of (catalog || [])) {
    const t = tierOf(p) === 'plugin' ? 'plugin' : 'core';
    const g = groupOf(p);
    (tiers[t][g] = tiers[t][g] || []).push(p);
  }

  const TIER_META = [
    { id: 'core', label: 'Sistema' },
    { id: 'plugin', label: 'Plugins' },
  ];

  return html`
    <div class="flex flex-col gap-5">
      ${TIER_META.map(({ id, label }) => {
        const groups = tiers[id];
        const names = sortGroups(id, Object.keys(groups));
        if (!names.length) return null;
        return html`
          <div key=${id} class="flex flex-col gap-3">
            <div class="text-[12px] font-semibold uppercase tracking-wider text-wa-text border-b border-wa-border pb-1">
              ${label}
            </div>
            ${names.map(name => html`
              <${GroupBlock} name=${name} perms=${groups[name]}
                sel=${sel} disabled=${disabled} onToggle=${onToggle} />
            `)}
          </div>
        `;
      })}
    </div>
  `;
}
