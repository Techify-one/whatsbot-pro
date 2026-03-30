import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { updateContactTags } from '../../services/api.js';

const html = htm.bind(h);

// ── Context Menu ─────────────────────────────────────────────────

export function ContextMenu({ x, y, phone, aiEnabled, contactTags, globalTags, isArchived, onToggleAI, onEditContact, onTagsUpdate, onArchive, onDelete, onClose }) {
  const ref = useRef(null);
  const [showTags, setShowTags] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [onClose]);

  const left = Math.min(x, window.innerWidth - 200);
  const top = Math.min(y, window.innerHeight - 50);

  async function toggleTag(tagName) {
    const current = contactTags || [];
    const newTags = current.includes(tagName)
      ? current.filter(t => t !== tagName)
      : [...current, tagName];
    const res = await updateContactTags(phone, newTags);
    if (res.ok) {
      onTagsUpdate(phone, res.data.tags);
    }
  }

  const tagEntries = Object.entries(globalTags || {});

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

      <!-- Tags toggle -->
      <button
        onClick=${() => setShowTags(prev => !prev)}
        class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.42l9 9c.36.36.86.58 1.41.58.55 0 1.05-.22 1.41-.59l7-7c.37-.36.59-.86.59-1.41 0-.55-.23-1.06-.59-1.42zM5.5 7C4.67 7 4 6.33 4 5.5S4.67 4 5.5 4 7 4.67 7 5.5 6.33 7 5.5 7z"/>
        </svg>
        Tags
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="ml-auto transition-transform ${showTags ? 'rotate-180' : ''}">
          <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/>
        </svg>
      </button>

      ${showTags ? html`
        <div class="border-t border-wa-border">
          ${tagEntries.length === 0 ? html`
            <div class="px-4 py-[8px] text-[13px] text-wa-secondary">Nenhuma tag criada</div>
          ` : tagEntries.map(([name, tagData]) => {
            const isActive = (contactTags || []).includes(name);
            return html`
              <button
                key=${name}
                onClick=${() => toggleTag(name)}
                class="w-full text-left px-4 py-[8px] text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-3"
              >
                <span
                  class="w-[16px] h-[16px] rounded border-2 flex items-center justify-center shrink-0"
                  style="border-color: ${tagData.color}; background: ${isActive ? tagData.color : 'transparent'};"
                >
                  ${isActive ? html`
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="white">
                      <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                    </svg>
                  ` : null}
                </span>
                <span
                  class="font-medium"
                  style="color: ${tagData.color};"
                >${name}</span>
              </button>
            `;
          })}
        </div>
      ` : null}

      <!-- Archive / Delete separator -->
      <div class="border-t border-wa-border">
        <button
          onClick=${() => { onArchive(phone, !isArchived); onClose(); }}
          class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M20.54 5.23l-1.39-1.68C18.88 3.21 18.47 3 18 3H6c-.47 0-.88.21-1.16.55L3.46 5.23C3.17 5.57 3 6.02 3 6.5V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6.5c0-.48-.17-.93-.46-1.27zM12 17.5L6.5 12H10v-2h4v2h3.5L12 17.5zM5.12 5l.81-1h12l.94 1H5.12z"/>
          </svg>
          ${isArchived ? 'Desarquivar' : 'Arquivar'}
        </button>
        <button
          onClick=${() => {
            if (!confirmDelete) {
              setConfirmDelete(true);
              return;
            }
            onDelete(phone);
            onClose();
          }}
          class="w-full text-left px-4 py-[10px] text-[14.5px] ${confirmDelete ? 'text-red-400 bg-red-500/10' : 'text-red-400'} hover:bg-wa-hover transition-colors flex items-center gap-3"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
          </svg>
          ${confirmDelete ? 'Confirmar exclusão?' : 'Apagar Contato'}
        </button>
      </div>
    </div>
  `;
}
