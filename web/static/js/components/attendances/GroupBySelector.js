// Seletor de agrupamento do kanban. Alterna entre atendente / etapa / etiqueta /
// status. Quando modo=etapa e há >1 atributo lista, mostra um 2º select para
// escolher qual atributo personalizado vira o eixo das colunas.
import { h } from 'preact';
import htm from 'htm';
import { GROUP_MODES } from './grouping.js';

const html = htm.bind(h);

export function GroupBySelector({ mode, onMode, stageAttrs, stageAttrKey, onStageAttr }) {
  // 'stage' só faz sentido se existir ao menos um atributo de conversa tipo lista.
  const modes = GROUP_MODES.filter(m => m.id !== 'stage' || (stageAttrs && stageAttrs.length > 0));

  return html`
    <div class="flex items-center gap-2 flex-wrap">
      <label class="text-[12px] text-wa-secondary">Agrupar por</label>
      <select class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${mode}
        onChange=${(e) => onMode(e.target.value)}>
        ${modes.map(m => html`<option key=${m.id} value=${m.id}>${m.label}</option>`)}
      </select>

      ${mode === 'stage' && stageAttrs && stageAttrs.length > 1 ? html`
        <select class="wa-field px-2 py-1.5 rounded-md text-[13px]" value=${stageAttrKey || ''}
          onChange=${(e) => onStageAttr(e.target.value)}>
          ${stageAttrs.map(a => html`<option key=${a.attribute_key} value=${a.attribute_key}>${a.display_name || a.attribute_key}</option>`)}
        </select>
      ` : null}
    </div>
  `;
}

export default GroupBySelector;
