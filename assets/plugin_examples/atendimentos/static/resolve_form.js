// Renderizador dinâmico de campos configuráveis + o popup de resolução.
// Os tipos (text/textarea/select/radio/checkbox) vêm das definições da config do
// plugin. Reutilizado pelo popup (beforeResolve) e pelo painel "Atendimento atual".
// Preact/htm via importmap; cores wa-*/.wa-field (modo escuro).

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
// Componente core que renderiza UM atributo personalizado por tipo (text/number/
// date/list/checkbox/link), dark-mode-safe. Reusado aqui p/ os atributos do core
// não-espelho (is_system=0) — mesma aparência da aba "Informações da conversa".
import { CustomAttributeField } from '/static/js/components/contacts/CustomAttributeField.js';

const html = htm.bind(h);

// Um campo, renderizado conforme `def.type`. value/onChange controlados pelo pai.
export function FieldInput({ def, value, onChange }) {
  const t = def.type || 'text';
  if (t === 'textarea') {
    return html`<textarea class="wa-field w-full px-3 py-2 rounded-md text-[14px] min-h-[80px]"
      value=${value || ''} onInput=${(e) => onChange(e.target.value)} />`;
  }
  if (t === 'select') {
    return html`<select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
      value=${value || ''} onChange=${(e) => onChange(e.target.value)}>
      <option value="">Selecione…</option>
      ${(def.options || []).map((o) => html`<option key=${o} value=${o}>${o}</option>`)}
    </select>`;
  }
  if (t === 'radio') {
    return html`<div class="flex flex-wrap gap-4 pt-1">
      ${(def.options || []).map((o) => html`
        <label key=${o} class="flex items-center gap-1.5 text-[14px] text-wa-text cursor-pointer">
          <input type="radio" name=${`f_${def.key}`} value=${o}
            checked=${value === o} onChange=${() => onChange(o)} />
          ${o}
        </label>`)}
    </div>`;
  }
  if (t === 'checkbox') {
    return html`<label class="flex items-center gap-2 text-[14px] text-wa-text cursor-pointer pt-1">
      <input type="checkbox" checked=${!!value} onChange=${(e) => onChange(e.target.checked)} />
      ${def.label}
    </label>`;
  }
  // text (default)
  return html`<input type="text" class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
    value=${value || ''} onInput=${(e) => onChange(e.target.value)} />`;
}

// Bloco "label + campo" (checkbox já tem o label embutido).
export function LabeledField({ def, value, onChange }) {
  if (def.type === 'checkbox') {
    return html`<div><${FieldInput} def=${def} value=${value} onChange=${onChange} /></div>`;
  }
  return html`<div>
    <label class="block text-[13px] font-medium text-wa-text mb-1">
      ${def.label}${def.required ? html`<span class="text-red-500"> *</span>` : null}
    </label>
    <${FieldInput} def=${def} value=${value} onChange=${onChange} />
  </div>`;
}

function isFilled(def, v) {
  if (def.type === 'checkbox') return true; // bool é sempre "preenchido"
  return String(v == null ? '' : v).trim() !== '';
}

// Popup do beforeResolve. Mostra DOIS conjuntos, ambos pré-preenchidos com os valores
// atuais da conversa (`initialValues` = conversations.custom_attributes, o que foi salvo
// na aba "Informações da conversa"):
//   1. `defs`     — rótulos do atendimento (escopo conversa: OBS + extras), via FieldInput.
//   2. `attrDefs` — atributos personalizados do core NÃO-espelho (is_system=0), via o
//                   CustomAttributeField do core. (Os espelhados do plugin chegam como
//                   is_system=1 e são filtrados fora no extends.js p/ não duplicar.)
// O botão "Resolver" só habilita quando todos os obrigatórios (dos dois conjuntos) estão
// preenchidos. onOk devolve { fields, custom_attributes } p/ o chamador gravar cada um.
export function ResolveForm({ defs = [], attrDefs = [], initialValues = {}, onOk, onCancel }) {
  const init0 = initialValues || {};
  const [vals, setVals] = useState(() => {
    const init = {};
    for (const d of defs) {
      const cur = init0[d.key];
      init[d.key] = d.type === 'checkbox'
        ? (cur === true || cur === 'true')
        : (cur == null ? '' : String(cur));
    }
    return init;
  });
  const [attrVals, setAttrVals] = useState(() => {
    const init = {};
    for (const a of attrDefs) {
      const cur = init0[a.attribute_key];
      if (cur !== undefined && cur !== null && cur !== '') init[a.attribute_key] = cur;
    }
    return init;
  });

  const missing = [
    ...defs.filter((d) => d.required && !isFilled(d, vals[d.key])).map((d) => d.label || d.key),
    ...attrDefs.filter((a) => a.required && !isFilled(a, attrVals[a.attribute_key]))
      .map((a) => a.display_name || a.attribute_key),
  ];
  const allRequired = missing.length === 0;

  return html`
    <div class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6 max-h-[85vh] overflow-auto">
        <h2 class="text-base font-semibold text-wa-text mb-1">Resolver conversa</h2>
        <p class="text-sm text-wa-secondary mb-4">Preencha antes de resolver:</p>

        ${defs.length ? html`
          <div class="space-y-3">
            ${defs.map((d) => html`<${LabeledField} key=${d.key} def=${d}
              value=${vals[d.key]}
              onChange=${(v) => setVals((s) => ({ ...s, [d.key]: v }))} />`)}
          </div>` : null}

        ${attrDefs.length ? html`
          <div class="space-y-4 ${defs.length ? 'mt-4 pt-4 border-t border-wa-border' : ''}">
            ${attrDefs.map((a) => html`<${CustomAttributeField} key=${a.id} def=${a}
              value=${attrVals[a.attribute_key]}
              onChange=${(v) => setAttrVals((s) => {
                const next = { ...s };
                if (v === null || v === undefined || v === '') delete next[a.attribute_key];
                else next[a.attribute_key] = v;
                return next;
              })} />`)}
          </div>` : null}

        ${!allRequired ? html`
          <div class="mt-4 px-3 py-2.5 rounded-md bg-red-500/15 border border-red-500 text-red-500 text-[14px] font-semibold">
            Preencha os campos obrigatórios: ${missing.join(', ')}.
          </div>` : null}
        <div class="flex gap-2 mt-5">
          <button onClick=${() => onOk({ fields: vals, custom_attributes: attrVals })} disabled=${!allRequired}
            class="flex-1 py-2.5 px-4 bg-wa-teal text-white rounded-lg disabled:opacity-50">Resolver</button>
          <button onClick=${onCancel}
            class="flex-1 py-2.5 px-4 bg-wa-panel hover:bg-wa-hover text-wa-text rounded-lg">Cancelar</button>
        </div>
      </div>
    </div>`;
}

export default ResolveForm;
