// Calendário do "ir para data" — plano 99 · F4.
//
// Componente PRÓPRIO, mês a mês, e não um `<input type="date">`: o controle
// nativo até segue o tema (o `color-scheme` está setado em `:root`/`html.dark`),
// mas o layout é do sistema operacional e não dá para apagar os dias que não
// levam a lugar nenhum — que é justamente a informação útil aqui.
//
// Toda a aritmética (grade, virada de mês, dia → epoch) vive no módulo PURO
// `services/chatCalendar.js`, testado com `node --test`. Aqui só há desenho e
// eventos. ⚠️ O dia é convertido em epoch no fuso do NAVEGADOR — ver a nota de
// fuso naquele módulo e a F3·2 do plano.

import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { monthGrid, monthLabel, shiftMonth, initialCursor, atLastMonth,
         WEEKDAY_LABELS } from '../../services/chatCalendar.js';

const html = htm.bind(h);

const ChevronLeft = () => html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>`;
const ChevronRight = () => html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/></svg>`;

/**
 * @param {Object} props
 * @param {number|null} props.refTs - epoch (s) de referência: abre neste mês
 * @param {(ts:number)=>void} props.onPick - dia escolhido, epoch (s) do início do dia
 * @param {()=>void} props.onBackToBottom - carrega diretamente a ponta recente
 * @param {()=>void} props.onClose
 */
export function DatePickerPopover({ refTs = null, onPick, onBackToBottom, onClose }) {
  const nowTs = Math.floor(Date.now() / 1000);
  const [cursor, setCursor] = useState(() => initialCursor(refTs, nowTs));
  const boxRef = useRef(null);

  // Fechar com Esc ou clique fora — o popover não pode ficar preso na tela.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    const onDown = (e) => {
      if (e.target && e.target.closest && e.target.closest('[data-date-picker-toggle]')) return;
      if (boxRef.current && !boxRef.current.contains(e.target)) onClose();
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onDown, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onDown, true);
    };
  }, [onClose]);

  const weeks = monthGrid(cursor.year, cursor.month, { maxTs: nowTs });
  const isLast = atLastMonth(cursor, nowTs);

  return html`
    <div ref=${boxRef}
         class="absolute left-0 top-[calc(100%+6px)] z-30 w-[280px] bg-wa-panel border border-wa-border
                rounded-lg shadow-lg p-3 select-none">
      <div class="flex items-center justify-between mb-2">
        <button type="button" title="Mês anterior"
          onClick=${() => setCursor(c => shiftMonth(c, -1))}
          class="text-wa-icon hover:text-wa-text p-1 rounded-full hover:bg-wa-hover transition-colors">
          <${ChevronLeft} />
        </button>
        <span class="text-wa-text text-[13px] font-medium capitalize">
          ${monthLabel(cursor.year, cursor.month)}
        </span>
        <button type="button" title="Próximo mês" disabled=${isLast}
          onClick=${() => setCursor(c => shiftMonth(c, 1))}
          class=${'p-1 rounded-full transition-colors ' + (isLast
            ? 'text-wa-secondary opacity-40 cursor-not-allowed'
            : 'text-wa-icon hover:text-wa-text hover:bg-wa-hover')}>
          <${ChevronRight} />
        </button>
      </div>
      <div class="grid grid-cols-7 gap-[2px] mb-1">
        ${WEEKDAY_LABELS.map((d, i) => html`
          <div key=${i} class="text-center text-wa-secondary text-[11px] font-medium py-1">${d}</div>`)}
      </div>
      ${weeks.map((week, wi) => html`
        <div key=${wi} class="grid grid-cols-7 gap-[2px]">
          ${week.map((cell, ci) => cell.day == null
            ? html`<div key=${ci} class="h-[30px]"></div>`
            : html`<button
                key=${ci} type="button" disabled=${cell.disabled}
                onClick=${() => { onPick(cell.ts); onClose(); }}
                class=${'h-[30px] rounded-full text-[12.5px] transition-colors ' + (cell.disabled
                  ? 'text-wa-secondary opacity-30 cursor-not-allowed'
                  : 'text-wa-text hover:bg-wa-teal hover:text-white')}
              >${cell.day}</button>`)}
        </div>`)}
      <button type="button"
        onClick=${() => {
          if (onBackToBottom) onBackToBottom();
          else onPick(nowTs);  // compatibilidade com chamador antigo
          onClose();
        }}
        class="w-full mt-2 text-[12px] text-wa-teal hover:underline py-1">
        Ir para o fim da conversa
      </button>
    </div>`;
}
