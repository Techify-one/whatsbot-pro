// Card de atendimento (kanban). Draggable quando o usuário tem a permissão do
// modo atual. Clicar (sem arrastar) abre o chat da conversa.
import { h } from 'preact';
import { useRef } from 'preact/hooks';
import htm from 'htm';
import { relativeTime, GroupIcon, StatusBadge, ChannelBadge, LabelChip, nameOf } from './ui.js';

const html = htm.bind(h);

export function AttendanceCard({ convo, canDrag, assigneeName, showChannel, labelsOf, onOpenChat, onDragStart, onDragEnd, pending }) {
  // Distingue clique de arrasto: dragstart roda antes do click; se arrastou, não abre.
  const draggedRef = useRef(false);

  const labels = (labelsOf && labelsOf(convo)) || [];

  return html`
    <div
      draggable=${canDrag}
      onDragStart=${(e) => {
        draggedRef.current = true;
        try {
          e.dataTransfer.setData('text/plain', String(convo.id));
          e.dataTransfer.effectAllowed = 'move';
        } catch (err) { /* ignore */ }
        onDragStart && onDragStart(convo);
      }}
      onDragEnd=${() => { onDragEnd && onDragEnd(); setTimeout(() => { draggedRef.current = false; }, 0); }}
      onClick=${() => { if (draggedRef.current) { draggedRef.current = false; return; } onOpenChat(convo); }}
      class="bg-wa-panel border border-wa-border rounded-lg p-2.5 flex flex-col gap-1.5 ${canDrag ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'} ${pending ? 'opacity-60' : ''} hover:border-wa-teal/50 transition-colors"
      title=${canDrag ? 'Arraste para mover · clique para abrir' : 'Clique para abrir'}
    >
      <div class="flex items-start gap-1.5 min-w-0">
        ${convo.contact_is_group ? html`<${GroupIcon} />` : null}
        <span class="text-[13px] font-medium text-wa-text truncate flex-1">${nameOf(convo)}</span>
        ${convo.display_id != null ? html`<span class="text-[10px] text-wa-secondary shrink-0">#${convo.display_id}</span>` : null}
      </div>

      <div class="flex items-center gap-1.5 text-[11px] text-wa-secondary flex-wrap">
        ${convo.contact_phone ? html`<span class="truncate">${convo.contact_phone}</span>` : null}
        <span aria-hidden="true">·</span>
        <span>${relativeTime(convo.last_activity_at || convo.opened_at)}</span>
      </div>

      ${labels.length ? html`
        <div class="flex flex-wrap gap-1">
          ${labels.map(l => html`<${LabelChip} key=${l.name} name=${l.name} color=${l.color} />`)}
        </div>
      ` : null}

      <div class="flex items-center justify-between gap-2 flex-wrap mt-0.5">
        <span class="text-[11px] ${assigneeName ? 'text-wa-text' : 'text-wa-secondary'} truncate">
          ${assigneeName || 'Não atribuído'}
        </span>
        <div class="flex items-center gap-1 shrink-0">
          ${showChannel ? html`<${ChannelBadge} provider=${convo.channel_provider} name=${convo.channel_name} />` : null}
          <${StatusBadge} status=${convo.status} />
        </div>
      </div>
    </div>
  `;
}

export default AttendanceCard;
