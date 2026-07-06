// Renderizador dinâmico de campos configuráveis + o popup de resolução.
// Os tipos (text/textarea/select/radio/checkbox) vêm das definições da config do
// plugin. Reutilizado pelo popup (beforeResolve) e pelo painel "Protocolo atual".
// Preact/htm via importmap; cores wa-*/.wa-field (modo escuro).

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
// Seletor de lista PADRÃO do app (busca sempre visível + "Limpar seleção"), compartilhado
// com os atributos do core e a barra de filtros — um único componente, um só padrão.
import { OptionListSelect } from '/static/js/components/OptionListSelect.js';
// Lista de atendentes atribuíveis (mesma de "Atribuir atendente") p/ o tipo "atendente".
import { getAssignableAgents } from '/static/js/services/api.js';

const html = htm.bind(h);

// Campo cujo VALOR é uma LISTA: "Caixa de seleção" (checkboxes) sempre, ou "Lista de
// seleção" (select) com `multiple` ligado. Espelha logic._is_multi no backend.
export const isMultiDef = (d) => (d && (d.type === 'checkboxes'
  || (d.type === 'select' && d.multiple)));

// Cache module-level dos atendentes atribuíveis (lista pequena, reusada em vários campos).
let _assignableUsers = null;

// Seletor de ATENDENTE nativo: os mesmos usuários de "Atribuir atendente" + "Não atribuído".
// value = uid (número) ou vazio quando não atribuído; onChange devolve number|null.
export function AttendantSelect({ value, onChange }) {
  const [users, setUsers] = useState(_assignableUsers || []);
  useEffect(() => {
    if (_assignableUsers) return undefined;
    let alive = true;
    getAssignableAgents().then((r) => {
      if (alive && r && r.ok && r.data) { _assignableUsers = r.data.users || []; setUsers(_assignableUsers); }
    }).catch(() => {});
    return () => { alive = false; };
  }, []);
  const cur = value == null ? '' : String(value);
  return html`<select class="wa-field w-full px-3 py-2 rounded-md text-[14px]" value=${cur}
    onChange=${(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
    <option value="">Não atribuído</option>
    ${users.map((u) => html`<option key=${u.id} value=${u.id}>${u.name || u.email || ('#' + u.id)}</option>`)}
  </select>`;
}

// Um campo, renderizado conforme `def.type`. value/onChange controlados pelo pai.
export function FieldInput({ def, value, onChange }) {
  const t = def.type || 'text';
  if (t === 'atendente') {
    return html`<${AttendantSelect} value=${value} onChange=${onChange} />`;
  }
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
  if (def.type === 'atendente') return v != null && String(v).trim() !== '';
  if (isMultiDef(def)) return Array.isArray(v) ? v.length > 0 : !!v;
  return String(v == null ? '' : v).trim() !== '';
}

// Popup do beforeResolve. Mostra só os rótulos do protocolo (`defs` — escopo atendimento:
// OBS + extras), pré-preenchidos com os valores atuais da atendimento (`initialValues` =
// conversations.custom_attributes, o que foi salvo na aba "Informações do atendimento").
// Os atributos personalizados de CONVERSA do core não fazem mais parte do plugin.
// Os botões de finalizar ("Resolver" e "Resolver e ir ao protocolo") só habilitam
// quando todos os obrigatórios estão preenchidos. onOk devolve { fields, goTo } —
// goTo=true só no botão "ir ao protocolo".
export function ResolveForm({ defs = [], initialValues = {}, defaultAssignee = null, onOk, onCancel }) {
  const init0 = initialValues || {};
  const [vals, setVals] = useState(() => {
    const init = {};
    for (const d of defs) {
      const cur = init0[d.key];
      if (d.type === 'checkbox') init[d.key] = (cur === true || cur === 'true');
      else if (d.type === 'atendente') {
        // Valor PADRÃO = usuário conectado quando não há atendente já definido. Não impede
        // trocar de atendente nem escolher "Não atribuído" depois — só semeia o campo.
        const seeded = (cur == null || String(cur).trim() === '') ? defaultAssignee : cur;
        init[d.key] = (seeded == null ? '' : seeded);
      }
      else if (isMultiDef(d)) init[d.key] = Array.isArray(cur)
        ? cur : (cur ? String(cur).split(',').map((s) => s.trim()).filter(Boolean) : []);
      else init[d.key] = (cur == null ? '' : String(cur));
    }
    return init;
  });

  const missing = defs.filter((d) => d.required && !isFilled(d, vals[d.key])).map((d) => d.label || d.key);
  const allRequired = missing.length === 0;

  return html`
    <div class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6 max-h-[85vh] overflow-auto">
        <h2 class="text-base font-semibold text-wa-text mb-1">Resolver atendimento</h2>
        <p class="text-sm text-wa-secondary mb-4">Preencha antes de resolver:</p>

        ${defs.length ? html`
          <div>
            <div class="space-y-3">
              ${defs.map((d) => html`<${LabeledField} key=${d.key} def=${d}
                value=${vals[d.key]}
                onChange=${(v) => setVals((s) => ({ ...s, [d.key]: v }))} />`)}
            </div>
          </div>` : null}

        ${!allRequired ? html`
          <div class="mt-4 px-3 py-2.5 rounded-md bg-red-500/15 border border-red-500 text-red-500 text-[14px] font-semibold">
            Preencha os campos obrigatórios: ${missing.join(', ')}.
          </div>` : null}
        <div class="mt-5 space-y-2">
          <div class="flex gap-2">
            <button onClick=${() => onOk({ fields: vals, goTo: false })} disabled=${!allRequired}
              class="flex-1 py-2.5 px-4 bg-wa-teal text-white rounded-lg disabled:opacity-50">Resolver</button>
            <button onClick=${onCancel}
              class="flex-1 py-2.5 px-4 bg-wa-panel hover:bg-wa-hover text-wa-text rounded-lg">Cancelar</button>
          </div>
          <button onClick=${() => onOk({ fields: vals, goTo: true })} disabled=${!allRequired}
            class="w-full py-2.5 px-4 rounded-lg border border-wa-teal text-wa-teal hover:bg-wa-teal/10 disabled:opacity-50 text-[14px] font-medium">
            Resolver e ir ao protocolo</button>
        </div>
      </div>
    </div>`;
}

export default ResolveForm;
