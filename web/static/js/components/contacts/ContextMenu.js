import { h } from 'preact';
import { useEffect, useRef } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// ── Context Menu ─────────────────────────────────────────────────

export function ContextMenu({ x, y, phone, aiEnabled, onToggleAI, onEditContact, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [onClose]);

  const left = Math.min(x, window.innerWidth - 200);
  const top = Math.min(y, window.innerHeight - 50);

  return html`
    <div
      ref=${ref}
      class="fixed z-[100] bg-wa-panel rounded-lg shadow-lg border border-wa-border py-[4px] min-w-[180px]"
      style="left:${left}px;top:${top}px"
    >
      <button
        onClick=${() => { onToggleAI(phone, !aiEnabled); onClose(); }}
        class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill=${aiEnabled ? '#ef4444' : '#00a884'}>
          ${aiEnabled
            ? html`<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 13.59L15.59 17 12 13.41 8.41 17 7 15.59 10.59 12 7 8.41 8.41 7 12 10.59 15.59 7 17 8.41 13.41 12 17 15.59z"/>`
            : html`<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>`
          }
        </svg>
        ${aiEnabled ? 'Desativar IA' : 'Ativar IA'}
      </button>
      <button
        onClick=${() => { onEditContact(phone); onClose(); }}
        class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1.003 1.003 0 000-1.42l-2.34-2.33a1.003 1.003 0 00-1.42 0l-1.83 1.83 3.75 3.75 1.84-1.83z"/>
        </svg>
        Editar Contato
      </button>
    </div>
  `;
}
