// Coluna do kanban: header (título + contagem) e dropzone com os cards.
// Cap visual de cards (ver mais client-side) para não estourar o DOM.
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { AttendanceCard } from './AttendanceCard.js';

const html = htm.bind(h);

const PAGE = 50;

export function BoardColumn({ column, cards, canDrop, isDropTarget, cardProps, onDragOver, onDragLeave, onDrop }) {
  const [shown, setShown] = useState(PAGE);
  const visible = cards.slice(0, shown);

  return html`
    <div class="flex flex-col w-72 shrink-0 max-h-full">
      <div class="flex items-center justify-between px-1 mb-2">
        <div class="flex items-center gap-1.5 min-w-0">
          ${column.color ? html`<span class="w-2.5 h-2.5 rounded-full shrink-0" style=${`background:${column.color}`}></span>` : null}
          <span class="text-[13px] font-medium text-wa-text truncate">${column.label}</span>
          ${column.isAi ? html`<span class="text-[10px] px-1 rounded bg-wa-teal/15 text-wa-teal">IA</span>` : null}
        </div>
        <span class="text-[11px] text-wa-secondary shrink-0">${cards.length}</span>
      </div>

      <div
        onDragOver=${(e) => onDragOver(e, column.id)}
        onDragLeave=${onDragLeave}
        onDrop=${(e) => onDrop(e, column.id)}
        class="flex-1 min-h-[120px] overflow-y-auto rounded-lg p-2 flex flex-col gap-2 border border-dashed transition-colors ${isDropTarget
          ? 'ring-2 ring-wa-teal bg-wa-hover border-wa-teal'
          : 'bg-wa-bg border-wa-border'}"
      >
        ${visible.map(c => html`<${AttendanceCard} key=${c.id} convo=${c} ...${cardProps(c)} />`)}
        ${cards.length === 0 ? html`<div class="text-[12px] text-wa-secondary text-center py-4">Vazio</div>` : null}
        ${cards.length > shown ? html`
          <button class="text-[12px] text-wa-teal hover:underline py-1"
            onClick=${() => setShown(s => s + PAGE)}>Ver mais (${cards.length - shown})</button>
        ` : null}
      </div>
    </div>
  `;
}

