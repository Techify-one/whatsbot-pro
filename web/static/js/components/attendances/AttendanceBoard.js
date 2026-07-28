// Kanban de atendimentos. Monta as colunas a partir do objeto `grouping`, agrupa
// os atendimentos por coluna e gerencia o realce da coluna-alvo no drag-and-drop.
import { h } from 'preact';
import { useState, useMemo } from 'preact/hooks';
import htm from 'htm';
import { BoardColumn } from './BoardColumn.js';

const html = htm.bind(h);

export function AttendanceBoard({
  conversations, grouping, canDrag, pendingIds,
  assigneeNameOf, labelsOf, showChannel, onOpenChat, onCardDrop,
}) {
  const [dropCol, setDropCol] = useState(null);
  const [draggingId, setDraggingId] = useState(null);

  const columns = useMemo(() => grouping.columns(), [grouping]);

  // Agrupa os atendimentos por coluna; card cujo id de coluna não existe cai na 1ª.
  const cardsByCol = useMemo(() => {
    const ids = new Set(columns.map(c => c.id));
    const map = {};
    for (const c of columns) map[c.id] = [];
    for (const conv of conversations) {
      let colId = grouping.columnIdOf(conv);
      if (!ids.has(colId)) colId = columns.length ? columns[0].id : null;
      if (colId != null && map[colId]) map[colId].push(conv);
    }
    return map;
  }, [conversations, columns, grouping]);

  function onDragOver(e, colId) {
    if (!canDrag) return;
    e.preventDefault();
    try { e.dataTransfer.dropEffect = 'move'; } catch (err) { /* ignore */ }
    if (dropCol !== colId) setDropCol(colId);
  }
  function onDragLeave(e) {
    // Só limpa se realmente saiu da coluna (não ao passar por um filho).
    if (e.currentTarget && e.relatedTarget && e.currentTarget.contains(e.relatedTarget)) return;
    setDropCol(null);
  }
  function onDrop(e, colId) {
    e.preventDefault();
    setDropCol(null);
    if (!canDrag) return;
    let id = null;
    try { id = parseInt(e.dataTransfer.getData('text/plain'), 10); } catch (err) { /* ignore */ }
    if (id != null && !Number.isNaN(id)) onCardDrop(id, colId);
  }

  const cardProps = (c) => ({
    canDrag,
    assigneeName: assigneeNameOf(c),
    labelsOf,
    showChannel,
    onOpenChat,
    pending: pendingIds && pendingIds.has(c.id),
    onDragStart: (conv) => setDraggingId(conv.id),
    onDragEnd: () => setDraggingId(null),
  });

  return html`
    <div class="flex gap-3 overflow-x-auto pb-2" style="min-height: 60vh;">
      ${columns.map(col => html`
        <${BoardColumn}
          key=${col.id}
          column=${col}
          cards=${cardsByCol[col.id] || []}
          canDrop=${canDrag}
          isDropTarget=${dropCol === col.id}
          cardProps=${cardProps}
          onDragOver=${onDragOver}
          onDragLeave=${onDragLeave}
          onDrop=${onDrop}
        />
      `)}
    </div>
  `;
}

