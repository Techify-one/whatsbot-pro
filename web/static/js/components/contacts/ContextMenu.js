import { h } from 'preact';
import { useState, useEffect, useLayoutEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { updateContactTags } from '../../services/api.js';
import { hasPermission } from '../../utils/permissions.js';
import { TagPicker } from './TagPicker.js';

const html = htm.bind(h);

// ── Context Menu ─────────────────────────────────────────────────

export function ContextMenu({ x, y, phone, aiEnabled, contactTags, globalTags, isArchived, isUnread, isPinned, conv, convLoading, convError, users, currentUserId, currentUser = null, onAssignConversation, onResolveConversation, onToggleAI, onEditContact, onMarkUnread, onMarkRead, onTagsUpdate, onArchive, onPin, onDeleteConversation, onCreateTag, onClose }) {
  // P48 (hide, don't disable): each affordance is gated by the permission that
  // its backend call actually enforces. `can` is permissive with no user
  // identity (open/legacy install) — see hasPermission.
  const can = (p) => hasPermission(currentUser, p);
  const ref = useRef(null);
  const [showTags, setShowTags] = useState(false);
  const [showAssign, setShowAssign] = useState(false);
  const [confirmDeleteConv, setConfirmDeleteConv] = useState(false);
  // Start at the raw click point; `useLayoutEffect` below measures the rendered
  // menu and clamps it inside the viewport (the menu can be taller than the
  // bottom gap, and grows when the Tags/Assign submenus expand).
  const [pos, setPos] = useState({ left: x, top: y });

  // Conversation-level menu state (assign attendant / resolve). The sidebar rows
  // are contact-level, so `conv` is resolved lazily by the parent on right-click.
  const userList = Array.isArray(users) ? users : [];
  const assigneeId = conv ? conv.assignee_user_id : null;
  const isOpen = conv ? conv.status === 'open' : null;
  const canAct = !!(conv && conv.id != null) && !convLoading;
  // Hide the whole assign section in legacy single-password mode (no identity and
  // no listable users) — assignment is meaningless there.
  const showAssignSection = (currentUserId != null || userList.length > 0) && can('conversation.assign');
  const userName = (u) => u.name || u.email || `Usuário #${u.id}`;
  const assignee = assigneeId != null ? userList.find(u => u.id === assigneeId) : null;
  const assigneeLabel = assigneeId != null ? (assignee ? userName(assignee) : `#${assigneeId}`) : null;

  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [onClose]);

  // Reset the inline delete confirmation when the menu targets a different row.
  useEffect(() => { setConfirmDeleteConv(false); }, [phone, conv && conv.id]);

  // Keep the menu inside the viewport. Measure the real rendered size and clamp
  // both axes; re-run whenever the content height changes (submenus toggling).
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const margin = 8;
    const rect = el.getBoundingClientRect();
    let left = x;
    let top = y;
    if (left + rect.width + margin > window.innerWidth) {
      left = Math.max(margin, window.innerWidth - rect.width - margin);
    }
    if (left < margin) left = margin;
    if (top + rect.height + margin > window.innerHeight) {
      top = Math.max(margin, window.innerHeight - rect.height - margin);
    }
    if (top < margin) top = margin;
    setPos({ left, top });
  }, [x, y, showTags, showAssign, confirmDeleteConv, conv && conv.id]);

  const { left, top } = pos;

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

  return html`
    <div
      ref=${ref}
      class="fixed z-[100] bg-wa-panel rounded-lg shadow-lg border border-wa-border py-[4px] min-w-[200px] max-h-[85vh] overflow-y-auto wa-scrollbar"
      style="left:${left}px;top:${top}px"
    >
      ${canAct && can('conversation.reply') ? html`
      <button
        onClick=${() => { onToggleAI(conv.id, !conv.ai_active); onClose(); }}
        class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill=${conv.ai_active ? '#ef4444' : '#00a884'}>
          ${conv.ai_active
            ? html`<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 13.59L15.59 17 12 13.41 8.41 17 7 15.59 10.59 12 7 8.41 8.41 7 12 10.59 15.59 7 17 8.41 13.41 12 17 15.59z"/>`
            : html`<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>`
          }
        </svg>
        ${conv.ai_active ? 'Desativar IA' : 'Ativar IA'}
      </button>
      ` : null}
      ${can('contact.write') ? html`
      <button
        onClick=${() => { onEditContact(phone); onClose(); }}
        class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1.003 1.003 0 000-1.42l-2.34-2.33a1.003 1.003 0 00-1.42 0l-1.83 1.83 3.75 3.75 1.84-1.83z"/>
        </svg>
        Editar Contato
      </button>
      ` : null}
      ${can('conversation.reply') ? (isUnread ? html`
        <button
          onClick=${() => { onMarkRead(phone); onClose(); }}
          class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/>
          </svg>
          Marcar como lido
        </button>
      ` : html`
        <button
          onClick=${() => { onMarkUnread(phone); onClose(); }}
          class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="#00a884">
            <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/>
          </svg>
          Marcar como não lida
        </button>
      `) : null}

      <!-- Pin / Unpin -->
      ${can('contact.write') ? html`
      <button
        onClick=${() => { onPin && onPin(phone, !isPinned); onClose(); }}
        class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z"/>
        </svg>
        ${isPinned ? 'Desafixar conversa' : 'Fixar conversa'}
      </button>
      ` : null}

      <!-- Tags toggle -->
      ${can('contact.write') ? html`
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
        <${TagPicker}
          globalTags=${globalTags}
          isActive=${(name) => (contactTags || []).includes(name)}
          onToggle=${toggleTag}
          onCreateTag=${onCreateTag}
        />
      ` : null}
      ` : null}

      <!-- Conversation: assign attendant (submenu) -->
      ${showAssignSection ? html`
        <div class="border-t border-wa-border">
          <button
            onClick=${() => setShowAssign(prev => !prev)}
            class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
              <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/>
            </svg>
            <span class="shrink-0">Atribuir atendente</span>
            ${assigneeLabel ? html`<span class="text-[11px] text-wa-secondary truncate" title=${assigneeLabel}>${assigneeLabel}</span>` : null}
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="ml-auto shrink-0 transition-transform ${showAssign ? 'rotate-180' : ''}">
              <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/>
            </svg>
          </button>
          ${showAssign ? html`
            <div class="border-t border-wa-border">
              ${convLoading ? html`
                <div class="px-4 py-[8px] text-[13px] text-wa-secondary animate-pulse-slow">Carregando conversa...</div>
              ` : !conv ? html`
                <div class="px-4 py-[8px] text-[13px] text-wa-secondary">Nenhuma conversa para este contato</div>
              ` : html`
                ${(currentUserId != null && assigneeId !== currentUserId) ? html`
                  <button
                    onClick=${() => onAssignConversation && onAssignConversation(conv.id, currentUserId)}
                    class="w-full text-left px-4 py-[8px] text-[13px] text-wa-teal hover:bg-wa-hover transition-colors flex items-center gap-3"
                  >
                    <span class="w-[16px] h-[16px] shrink-0"></span>
                    Atribuir a mim
                  </button>
                ` : null}
                <div class="max-h-[200px] overflow-y-auto wa-scrollbar">
                  ${userList.length === 0 ? html`
                    <div class="px-4 py-[8px] text-[13px] text-wa-secondary">Sem usuários para listar</div>
                  ` : userList.map(u => {
                    const active = u.id === assigneeId;
                    return html`
                      <button
                        key=${u.id}
                        onClick=${() => onAssignConversation && onAssignConversation(conv.id, u.id)}
                        class="w-full text-left px-4 py-[8px] text-[13px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
                        title=${active ? 'Atribuída a este atendente' : 'Atribuir a este atendente'}
                      >
                        <span class="w-[16px] h-[16px] shrink-0 flex items-center justify-center text-wa-teal">
                          ${active ? html`<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>` : ''}
                        </span>
                        <span class="truncate ${active ? 'font-medium' : ''}">${userName(u)}</span>
                      </button>
                    `;
                  })}
                </div>
                ${assigneeId != null ? html`
                  <button
                    onClick=${() => onAssignConversation && onAssignConversation(conv.id, null)}
                    class="w-full text-left px-4 py-[8px] text-[13px] text-red-400 hover:bg-wa-hover transition-colors flex items-center gap-3 border-t border-wa-border"
                  >
                    <span class="w-[16px] h-[16px] shrink-0"></span>
                    Remover atribuição
                  </button>
                ` : null}
              `}
            </div>
          ` : null}
        </div>
      ` : null}

      <!-- Conversation: resolve / reopen -->
      ${can('conversation.resolve') ? html`
      <button
        disabled=${!canAct}
        onClick=${() => { if (canAct && onResolveConversation) onResolveConversation(conv.id, isOpen ? 'closed' : 'open'); }}
        class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3 disabled:opacity-50 border-t border-wa-border"
      >
        ${isOpen === false ? html`
          <svg viewBox="0 0 24 24" width="18" height="18" fill="#00a884"><path d="M12 5V1L7 6l5 5V7c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6H4c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8z"/></svg>
          Reabrir conversa
        ` : html`
          <svg viewBox="0 0 24 24" width="18" height="18" fill="#00a884"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
          ${convLoading ? 'Carregando...' : 'Marcar como resolvida'}
        `}
      </button>
      ` : null}

      ${convError ? html`
        <div class="px-4 py-[6px] text-[12px] text-red-400">${convError}</div>
      ` : null}

      <!-- Archive / Delete separator -->
      <div class="border-t border-wa-border">
        ${can('contact.write') ? html`
        <button
          onClick=${() => { onArchive(phone, !isArchived); onClose(); }}
          class="w-full text-left px-4 py-[10px] text-[14.5px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-3"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M20.54 5.23l-1.39-1.68C18.88 3.21 18.47 3 18 3H6c-.47 0-.88.21-1.16.55L3.46 5.23C3.17 5.57 3 6.02 3 6.5V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6.5c0-.48-.17-.93-.46-1.27zM12 17.5L6.5 12H10v-2h4v2h3.5L12 17.5zM5.12 5l.81-1h12l.94 1H5.12z"/>
          </svg>
          ${isArchived ? 'Desarquivar' : 'Arquivar'}
        </button>
        ` : null}
        ${canAct && can('conversation.delete') ? html`
        <button
          onClick=${() => {
            if (!confirmDeleteConv) {
              setConfirmDeleteConv(true);
              return;
            }
            onDeleteConversation(conv.id);
            onClose();
          }}
          class="w-full text-left px-4 py-[10px] text-[14.5px] ${confirmDeleteConv ? 'text-red-400 bg-red-500/10' : 'text-red-400'} hover:bg-wa-hover transition-colors flex items-center gap-3"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
          </svg>
          ${confirmDeleteConv ? 'Confirmar exclusão da conversa?' : 'Apagar conversa'}
        </button>
        ` : null}
      </div>
    </div>
  `;
}
