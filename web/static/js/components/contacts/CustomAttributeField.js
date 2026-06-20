import { h } from 'preact';
import htm from 'htm';
import { SearchableSelect } from '../SearchableSelect.js';

const html = htm.bind(h);

// Above this many options, the native <select> popup becomes unwieldy (and is
// drawn by the OS, ignoring the dark theme) — switch to the searchable dropdown.
const SEARCHABLE_THRESHOLD = 8;

// CustomAttributeField (plano 05) — renders one custom attribute control by type.
// Reused by the contact panel now and by the conversation sidebar (Fase 5).
// Dark-mode-safe: .wa-field on inputs/selects (CLAUDE.md rule).
export function CustomAttributeField({ def, value, onChange }) {
  const type = (def.type || 'text').toLowerCase();
  const label = def.display_name || def.attribute_key;
  const labelEl = html`
    <label class="text-wa-secondary text-[12px] font-medium block mb-1">
      ${label}${def.required ? html`<span class="text-red-500"> *</span>` : null}
    </label>
  `;

  if (type === 'checkbox') {
    return html`
      <div>
        <label class="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked=${value === true || value === 'true'}
            onChange=${(e) => onChange(e.target.checked)}
          />
          <span class="text-wa-text text-[14px]">${label}</span>
        </label>
        ${def.description ? html`<div class="text-wa-secondary text-[11px] mt-0.5">${def.description}</div>` : null}
      </div>
    `;
  }

  let control;
  if (type === 'list') {
    const opts = def.options || [];
    if (opts.length > SEARCHABLE_THRESHOLD) {
      // Long user-defined list → searchable dropdown (also dark-mode-safe).
      control = html`
        <${SearchableSelect}
          value=${value ?? ''}
          onChange=${(v) => onChange(v || null)}
          options=${opts.map(o => ({ value: o, label: o }))}
          placeholder="— selecione —"
          searchPlaceholder="Buscar..."
          allowEmpty=${true}
          emptyLabel="— selecione —"
        />
      `;
    } else {
      // Short list → native select stays (lighter, no floating dropdown).
      control = html`
        <select
          class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none"
          value=${value ?? ''}
          onChange=${(e) => onChange(e.target.value || null)}
        >
          <option value="">— selecione —</option>
          ${opts.map(opt => html`<option key=${opt} value=${opt}>${opt}</option>`)}
        </select>
      `;
    }
  } else {
    const inputType = type === 'number' ? 'number' : type === 'date' ? 'date' : type === 'link' ? 'url' : 'text';
    control = html`
      <input
        type=${inputType}
        class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none"
        value=${value ?? ''}
        placeholder=${def.regex_cue || ''}
        onInput=${(e) => onChange(e.target.value)}
      />
    `;
  }

  return html`
    <div>
      ${labelEl}
      ${control}
      ${def.description ? html`<div class="text-wa-secondary text-[11px] mt-0.5">${def.description}</div>` : null}
    </div>
  `;
}
