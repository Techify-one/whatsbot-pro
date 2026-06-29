// Channels — AgentPicker (Plano 23 · D4), extracted verbatim from
// ChannelsManager.js. Selectable list of panel users (agents) that will
// see/receive a channel's inbox. Selected agents render as removable chips; an
// "add" dropdown lists the remaining users.
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

export function AgentPicker({ users, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const byId = {};
  for (const u of users) byId[u.id] = u;
  const selectedUsers = selected.map(id => byId[id]).filter(Boolean);
  const available = users.filter(u => !selected.includes(u.id));

  function add(id) {
    onChange([...selected, id]);
    if (available.length <= 1) setOpen(false);
  }
  function remove(id) {
    onChange(selected.filter(x => x !== id));
  }

  return html`
    <div class="relative">
      <div class="wa-field w-full min-h-[42px] px-2 py-1.5 rounded-md flex flex-wrap gap-1.5 items-center cursor-pointer"
        onClick=${() => { if (available.length > 0) setOpen(o => !o); }}
        title="Clique para escolher agentes">
        ${selectedUsers.length === 0
          ? html`<span class="text-[13px] text-wa-secondary px-1">Nenhum agente selecionado</span>`
          : selectedUsers.map(u => html`
            <span key=${u.id}
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[13px] bg-wa-hover text-wa-text border border-wa-border">
              ${u.name}${u.is_admin ? html`<span class="text-[11px] text-wa-secondary">admin</span>` : null}
              <button type="button" class="text-wa-secondary hover:text-red-500"
                onClick=${(e) => { e.stopPropagation(); remove(u.id); }} title="Remover">×</button>
            </span>
          `)}
        <span class="ml-auto text-[13px] text-wa-secondary px-1 shrink-0">${open ? '▲' : '▼'}</span>
      </div>
      ${open && available.length > 0 ? html`
        <div class="absolute z-10 mt-1 w-full max-h-56 overflow-auto bg-wa-panel border border-wa-border rounded-md shadow-lg">
          ${available.map(u => html`
            <button key=${u.id} type="button"
              class="w-full text-left px-3 py-2 text-[13px] text-wa-text hover:bg-wa-hover flex items-center gap-2"
              onClick=${() => add(u.id)}>
              <span class="truncate">${u.name}</span>
              <span class="text-[12px] text-wa-secondary truncate">${u.email}</span>
              ${u.is_admin ? html`<span class="ml-auto text-[11px] text-wa-secondary">admin</span>` : null}
            </button>
          `)}
        </div>
      ` : null}
    </div>
  `;
}
