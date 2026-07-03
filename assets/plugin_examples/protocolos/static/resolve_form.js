// Renderizador dinâmico de campos configuráveis + o popup de resolução.
// Os tipos (text/textarea/select/radio/checkbox) vêm das definições da config do
// plugin. Reutilizado pelo popup (beforeResolve) e pelo painel "Protocolo atual".
// Preact/htm via importmap; cores wa-*/.wa-field (modo escuro).

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
// Componente core que renderiza UM atributo personalizado por tipo (text/number/
// date/list/checkbox/link), dark-mode-safe. Reusado aqui p/ os atributos do core
// não-espelho (is_system=0) — mesma aparência da aba "Informações do atendimento".
import { CustomAttributeField } from '/static/js/components/contacts/CustomAttributeField.js';
// Seletor de lista PADRÃO do app (busca sempre visível + "Limpar seleção"), compartilhado
// com os atributos do core e a barra de filtros — um único componente, um só padrão.
import { OptionListSelect } from '/static/js/components/OptionListSelect.js';

const html = htm.bind(h);

// Campo cujo VALOR é uma LISTA: "Caixa de seleção" (checkboxes) sempre, ou "Lista de
// seleção" (select) com `multiple` ligado. Espelha logic._is_multi no backend.
export const isMultiDef = (d) => (d && (d.type === 'checkboxes'
  || (d.type === 'select' && d.multiple)));

// Um campo, renderizado conforme `def.type`. value/onChange controlados pelo pai.
export function FieldInput({ def, value, onChange }) {
  const t = def.type || 'text';
  if (t === 'textarea') {
    return html`<textarea class="wa-field w-full px-3 py-2 rounded-md text-[14px] min-h-[80px]"
      value=${value || ''} placeholder=${def.regex_cue || ''} onInput=${(e) => onChange(e.target.value)} />`;
  }
  if (t === 'select') {
    // "Lista de seleção" (padrão do app); `def.multiple` habilita marcar várias (valor = lista).
    return html`<${OptionListSelect} options=${def.options || []} value=${value}
      multiple=${!!def.multiple} onChange=${onChange} />`;
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
  if (t === 'checkboxes') {
    // Caixa de seleção configurável: value é sempre uma LISTA. `def.multiple` decide se
    // marca várias (toggle) ou só uma (marcar troca a anterior — comporta como rádio).
    const arr = Array.isArray(value) ? value : (value ? [value] : []);
    const toggle = (o) => {
      if (def.multiple) onChange(arr.includes(o) ? arr.filter((x) => x !== o) : [...arr, o]);
      else onChange(arr.includes(o) ? [] : [o]);
    };
    return html`<div class="flex flex-wrap gap-x-4 gap-y-1.5 pt-1">
      ${(def.options || []).map((o) => html`
        <label key=${o} class="flex items-center gap-1.5 text-[14px] text-wa-text cursor-pointer">
          <input type="checkbox" checked=${arr.includes(o)} onChange=${() => toggle(o)} />
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
  if (t === 'date' || t === 'number') {
    return html`<input type=${t} class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
      value=${value || ''} placeholder=${def.regex_cue || ''} onInput=${(e) => onChange(e.target.value)} />`;
  }
  // text (default)
  return html`<input type="text" class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
    value=${value || ''} placeholder=${def.regex_cue || ''} onInput=${(e) => onChange(e.target.value)} />`;
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
  if (isMultiDef(def)) return Array.isArray(v) ? v.length > 0 : !!v;
  return String(v == null ? '' : v).trim() !== '';
}

// Popup do beforeResolve. Mostra DOIS conjuntos, ambos pré-preenchidos com os valores
// atuais da atendimento (`initialValues` = conversations.custom_attributes, o que foi salvo
// na aba "Informações do atendimento"):
//   1. `defs`     — rótulos do protocolo (escopo atendimento: OBS + extras), via FieldInput.
//   2. `attrDefs` — atributos personalizados do core NÃO-espelho (is_system=0), via o
//                   CustomAttributeField do core. (Os espelhados do plugin chegam como
//                   is_system=1 e são filtrados fora no extends.js p/ não duplicar.)
// Os botões de finalizar ("Resolver" e "Resolver e ir ao protocolo") só habilitam
// quando todos os obrigatórios (dos dois conjuntos) estão preenchidos. onOk devolve
// { fields, custom_attributes, goTo } — goTo=true só no botão "ir ao protocolo".
export function ResolveForm({ defs = [], attrDefs = [], initialValues = {}, onOk, onCancel }) {
  const init0 = initialValues || {};
  const [vals, setVals] = useState(() => {
    const init = {};
    for (const d of defs) {
      const cur = init0[d.key];
      if (d.type === 'checkbox') init[d.key] = (cur === true || cur === 'true');
      else if (isMultiDef(d)) init[d.key] = Array.isArray(cur)
        ? cur : (cur ? String(cur).split(',').map((s) => s.trim()).filter(Boolean) : []);
      else init[d.key] = (cur == null ? '' : String(cur));
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
  // Com os DOIS conjuntos, rotula cada zona p/ deixar claro a origem: os campos de cima
  // são informações da atendimento; abaixo da barra, atributos personalizados.
  const showHeaders = defs.length > 0 && attrDefs.length > 0;

  return html`
    <div class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6 max-h-[85vh] overflow-auto">
        <h2 class="text-base font-semibold text-wa-text mb-1">Resolver atendimento</h2>
        <p class="text-sm text-wa-secondary mb-4">Preencha antes de resolver:</p>

        ${defs.length ? html`
          <div>
            ${showHeaders ? html`<div class="text-[10px] uppercase tracking-wide font-semibold text-wa-teal mb-2">Informações do atendimento</div>` : null}
            <div class="space-y-3">
              ${defs.map((d) => html`<${LabeledField} key=${d.key} def=${d}
                value=${vals[d.key]}
                onChange=${(v) => setVals((s) => ({ ...s, [d.key]: v }))} />`)}
            </div>
          </div>` : null}

        ${attrDefs.length ? html`
          <div class="${defs.length ? 'mt-4 pt-4 border-t border-wa-border' : ''}">
            ${showHeaders ? html`<div class="text-[10px] uppercase tracking-wide font-semibold text-amber-600 mb-2">Atributos personalizados</div>` : null}
            <div class="space-y-4">
              ${attrDefs.map((a) => html`<${CustomAttributeField} key=${a.id} def=${a}
                value=${attrVals[a.attribute_key]}
                onChange=${(v) => setAttrVals((s) => {
                  const next = { ...s };
                  if (v === null || v === undefined || v === '') delete next[a.attribute_key];
                  else next[a.attribute_key] = v;
                  return next;
                })} />`)}
            </div>
          </div>` : null}

        ${!allRequired ? html`
          <div class="mt-4 px-3 py-2.5 rounded-md bg-red-500/15 border border-red-500 text-red-500 text-[14px] font-semibold">
            Preencha os campos obrigatórios: ${missing.join(', ')}.
          </div>` : null}
        <div class="mt-5 space-y-2">
          <div class="flex gap-2">
            <button onClick=${() => onOk({ fields: vals, custom_attributes: attrVals, goTo: false })} disabled=${!allRequired}
              class="flex-1 py-2.5 px-4 bg-wa-teal text-white rounded-lg disabled:opacity-50">Resolver</button>
            <button onClick=${onCancel}
              class="flex-1 py-2.5 px-4 bg-wa-panel hover:bg-wa-hover text-wa-text rounded-lg">Cancelar</button>
          </div>
          <button onClick=${() => onOk({ fields: vals, custom_attributes: attrVals, goTo: true })} disabled=${!allRequired}
            class="w-full py-2.5 px-4 rounded-lg border border-wa-teal text-wa-teal hover:bg-wa-teal/10 disabled:opacity-50 text-[14px] font-medium">
            Resolver e ir ao protocolo</button>
        </div>
      </div>
    </div>`;
}

export default ResolveForm;
