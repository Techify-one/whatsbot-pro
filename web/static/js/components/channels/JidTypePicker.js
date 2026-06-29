// Channels — JidTypePicker (Plano 23 · D4), extracted verbatim from
// ChannelsManager.js. Checkbox group letting the user pick which WhatsApp chat
// types fall into a GOWA channel (config.allowed_jid_types). The user never sees
// the raw JID — only the friendly label.
import { h } from 'preact';
import htm from 'htm';
import { JID_TYPES } from './constants.js';

const html = htm.bind(h);

export function JidTypePicker({ selected, onChange, disabled }) {
  function toggle(key) {
    const set = new Set(selected);
    if (set.has(key)) set.delete(key); else set.add(key);
    // Preserve canonical order.
    onChange(JID_TYPES.map(t => t.key).filter(k => set.has(k)));
  }
  return html`
    <div class="flex flex-col gap-2">
      ${JID_TYPES.map(t => html`
        <label key=${t.key} class="flex items-start gap-2 cursor-pointer ${disabled ? 'opacity-60 cursor-not-allowed' : ''}">
          <input type="checkbox" class="mt-0.5" checked=${selected.includes(t.key)}
            disabled=${disabled} onChange=${() => toggle(t.key)} />
          <span class="flex flex-col">
            <span class="text-[13px] text-wa-text">${t.label}</span>
            <span class="text-[11px] text-wa-secondary">${t.hint}</span>
          </span>
        </label>
      `)}
    </div>
  `;
}
