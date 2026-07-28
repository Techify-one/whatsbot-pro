import { h } from 'preact';
import { useMemo } from 'preact/hooks';
import htm from 'htm';
import { SearchableSelect } from './SearchableSelect.js';

const html = htm.bind(h);

// Campo de VALOR de um atributo/rótulo.
//
// A lista de escolha aparece SOMENTE quando o campo é de opção — "Lista de seleção",
// "Caixa de seleção", radio, atributo do tipo lista — e traz exatamente as opções
// CONFIGURADAS naquele campo. Um campo de texto/número/data não tem lista: sugerir
// os valores já digitados por aí só devolvia lixo (".", ",K", "12"), então ele é um
// input comum. Mesmo com lista, o operador pode digitar um valor fora dela
// (allowCustom) — a lista é atalho, não restrição.
//
// @param {Array}    [props.options]- opções declaradas na definição do campo
// @param {string}   [props.type]   - tipo da definição (text|number|date|list|select|…)
// @param {any}      props.value
// @param {function} props.onChange - (novoValor: string) => void
// @param {boolean}  [props.disabled]
// @param {string}   [props.placeholder]
// @param {string}   [props.inputClass] - classes do controle fechado
const NATIVE_TYPES = { date: 'date', number: 'number' };

export function AttributeValueInput({
  options, type, value, onChange, disabled, placeholder, inputClass,
}) {
  const t = String(type || 'text').toLowerCase();

  // Aceita ['a','b'] ou [{value,label}]; descarta vazios e repetidos.
  const opts = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const o of (options || [])) {
      const v = String(o == null ? '' : (o && typeof o === 'object' ? o.value : o)).trim();
      if (!v || seen.has(v)) continue;
      seen.add(v);
      out.push({ value: v, label: v });
    }
    return out;
  }, [options]);

  const cls = inputClass || 'wa-field w-full px-2 py-1.5 rounded-md text-[13px]';

  if (!opts.length) {
    return html`<input class=${cls} type=${NATIVE_TYPES[t] || 'text'} disabled=${disabled}
      value=${value == null ? '' : value} placeholder=${placeholder || ''}
      onInput=${(e) => onChange(e.target.value)} />`;
  }

  return html`<${SearchableSelect}
    value=${value == null ? '' : String(value)}
    onChange=${onChange}
    options=${opts}
    allowCustom=${true}
    disabled=${disabled}
    inputClass=${cls}
    placeholder=${placeholder || 'Escolha ou digite um valor'}
    searchPlaceholder=${placeholder || 'Escolha ou digite um valor'} />`;
}

export default AttributeValueInput;
