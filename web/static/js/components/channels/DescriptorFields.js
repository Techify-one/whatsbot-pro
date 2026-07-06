// Channels — generic descriptor-driven field renderers (plano 33). The core form
// renders ANY provider from its descriptor's `credential_fields` / `config_fields`
// with NO provider-specific code. Field types: text, secret, token_suggest,
// multiselect, generated. Rich providers may instead ship a `form_component`
// (loaded elsewhere via import()); these renderers are the always-available
// generic path that covers the built-in providers.
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { randomToken } from './constants.js';

const html = htm.bind(h);

// Optional rich form: a provider may declare `form_component` (a JS module path
// like /plugins/<id>/static/<x>.js) instead of / on top of the generic fields.
// It is loaded via native dynamic import() — the same seam plugin SCREENS use —
// and rendered with the shared field state so it can drive credentials/config.
// The built-in providers use the generic fields and set form_component=null; this
// is the extension point for genuinely custom UI (plano 33 D2 / G4).
export function FormComponentLoader({ path, ...props }) {
  const [Comp, setComp] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let alive = true;
    setComp(null); setErr('');
    import(path)
      .then((m) => { if (alive) setComp(() => (m && m.default) || null); })
      .catch((e) => { if (alive) setErr(String((e && e.message) || e)); });
    return () => { alive = false; };
  }, [path]);
  if (err) {
    return html`<div class="text-[12px] text-red-500">Falha ao carregar o formulário do provider: ${err}</div>`;
  }
  if (!Comp) {
    return html`<div class="text-[12px] text-wa-secondary">Carregando formulário…</div>`;
  }
  return html`<${Comp} ...${props} />`;
}

// Generic checkbox group for a `multiselect` field (options = [{value,label,hint?}]).
export function MultiSelect({ options, selected, onChange, disabled }) {
  const sel = new Set(selected || []);
  const order = (options || []).map((o) => o.value);
  function toggle(value) {
    const s = new Set(sel);
    if (s.has(value)) s.delete(value); else s.add(value);
    onChange(order.filter((v) => s.has(v))); // preserve canonical option order
  }
  return html`
    <div class="flex flex-col gap-2">
      ${(options || []).map((o) => html`
        <label key=${o.value}
          class="flex items-start gap-2 cursor-pointer ${disabled ? 'opacity-60 cursor-not-allowed' : ''}">
          <input type="checkbox" class="mt-0.5" checked=${sel.has(o.value)}
            disabled=${disabled} onChange=${() => toggle(o.value)} />
          <span class="flex flex-col">
            <span class="text-[13px] text-wa-text">${o.label}</span>
            ${o.hint ? html`<span class="text-[11px] text-wa-secondary">${o.hint}</span>` : null}
          </span>
        </label>
      `)}
    </div>`;
}

// A field label with an optional required marker.
function FieldLabel({ field, required }) {
  return html`<label class="block text-[12px] text-wa-secondary mb-1">
    ${field.label || field.key}${required ? html`<span class="text-red-500"> *</span>` : null}
    ${field.type === 'generated' ? html`<span class="text-wa-secondary"> (gerado automaticamente)</span>` : null}
  </label>`;
}

// Render a single credential field. In edit mode a blank value means "keep
// current", so nothing is marked required and secrets show a "(manter)" hint.
function CredentialField({ field, value, onChange, editMode, busy }) {
  const type = field.type || 'text';
  const required = !!field.required && !editMode;
  const placeholder = editMode
    ? `${field.label || field.key} (manter)`
    : (field.placeholder || '');
  const set = (v) => onChange(field.key, v);
  let input;
  if (type === 'token_suggest') {
    input = html`
      <div class="flex gap-2">
        <input class="wa-field flex-1 px-3 py-2 rounded-md text-[14px]" type="text"
          placeholder=${placeholder} value=${value || ''} disabled=${busy}
          onInput=${(e) => set(e.target.value)} />
        <button type="button"
          class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
          onClick=${() => set(randomToken())}>Sugerir</button>
      </div>`;
  } else {
    const inputType = type === 'secret' ? 'password' : 'text';
    input = html`<input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
      type=${inputType} placeholder=${placeholder} value=${value || ''} disabled=${busy}
      onInput=${(e) => set(e.target.value)} />`;
  }
  return html`
    <div>
      <${FieldLabel} field=${field} required=${required} />
      ${input}
      ${field.help ? html`<div class="text-[12px] text-wa-secondary mt-1">${field.help}</div>` : null}
    </div>`;
}

// Render a single config field (text / generated / multiselect).
function ConfigField({ field, value, onChange, busy }) {
  const type = field.type || 'text';
  const set = (v) => onChange(field.key, v);
  if (type === 'multiselect') {
    return html`
      <div>
        <${FieldLabel} field=${field} required=${false} />
        ${field.help ? html`<p class="text-[12px] text-wa-secondary mb-2">${field.help}</p>` : null}
        <${MultiSelect} options=${field.options || []} selected=${value || []}
          onChange=${set} disabled=${busy} />
      </div>`;
  }
  if (type === 'generated') {
    return html`
      <div>
        <${FieldLabel} field=${field} required=${false} />
        <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] opacity-60 cursor-not-allowed"
          type="text" value=${value || ''} readonly disabled />
        ${field.help ? html`<div class="text-[12px] text-wa-secondary mt-1">${field.help}</div>` : null}
      </div>`;
  }
  return html`
    <div>
      <${FieldLabel} field=${field} required=${false} />
      <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
        placeholder=${field.placeholder || ''} value=${value || ''} disabled=${busy}
        onInput=${(e) => set(e.target.value)} />
      ${field.help ? html`<div class="text-[12px] text-wa-secondary mt-1">${field.help}</div>` : null}
    </div>`;
}

// Render all credential fields of a descriptor. values = {key: string}.
export function CredentialFields({ fields, values, onChange, editMode, busy }) {
  const list = fields || [];
  if (!list.length) return null;
  return html`${list.map((field) => html`<${CredentialField} key=${field.key}
    field=${field} value=${(values || {})[field.key]} onChange=${onChange}
    editMode=${editMode} busy=${busy} />`)}`;
}

// Render all config fields of a descriptor. In edit mode, `generated` fields are
// immutable (assigned at create) and skipped. values = {key: value}.
export function ConfigFields({ fields, values, onChange, editMode, busy }) {
  const list = (fields || []).filter((f) => !(editMode && f.type === 'generated'));
  if (!list.length) return null;
  return html`${list.map((field) => html`<${ConfigField} key=${field.key}
    field=${field} value=${(values || {})[field.key]} onChange=${onChange}
    busy=${busy} />`)}`;
}
