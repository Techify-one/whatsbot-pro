import { h } from 'preact';
import htm from 'htm';
import { SearchIcon, DefaultAvatar, GroupAvatar, DoubleCheckIcon, ArchiveIcon } from './icons.js';
import { formatTime } from './utils.js';

const html = htm.bind(h);

// ── Contact List (WhatsApp Web sidebar) ──────────────────────────

export function ContactList({ contacts, loading, search, onSearchChange, selected, onSelect, onContextMenu, typingState, showArchived, onToggleArchived, globalTags }) {
  return html`
    <div class="flex flex-col h-full bg-wa-bg">
      <!-- Green header bar -->
      <div class="h-[59px] flex items-center justify-between px-4 ${showArchived ? 'bg-[#2a3942]' : 'bg-wa-teal'} shrink-0 transition-colors">
        <div class="flex items-center gap-3">
          <button
            onClick=${onToggleArchived}
            class="w-[40px] h-[40px] rounded-full flex items-center justify-center hover:bg-white/10 transition-colors ${showArchived ? 'bg-white/15' : ''}"
            title=${showArchived ? 'Voltar às conversas' : 'Ver arquivados'}
          >
            <span class="text-white"><${ArchiveIcon} /></span>
          </button>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-white text-[15px] font-medium opacity-90">${showArchived ? 'Arquivados' : 'WhatsBot'}</span>
        </div>
      </div>

      <!-- Search bar -->
      <div class="py-[6px] px-[12px] bg-wa-bg border-b border-wa-border">
        <div class="flex items-center bg-wa-panel rounded-lg h-[35px] px-[8px] gap-[20px]">
          <${SearchIcon} />
          <input
            type="text"
            placeholder="Pesquisar ou começar uma nova conversa"
            value=${search}
            onInput=${(e) => onSearchChange(e.target.value)}
            class="bg-transparent border-none outline-none text-wa-text text-[14px] w-full placeholder-wa-secondary"
          />
        </div>
      </div>

      <!-- Contact rows -->
      <div class="flex-1 overflow-y-auto wa-scrollbar bg-wa-bg">
        ${loading && contacts.length === 0
          ? html`<div class="text-center text-wa-secondary py-8 animate-pulse-slow text-[14px]">Carregando...</div>`
          : contacts.length === 0
            ? html`<div class="text-center text-wa-secondary py-8 text-[14px]">Nenhum contato encontrado</div>`
            : contacts.map(c => html`
                <div
                  key=${c.phone}
                  onClick=${() => onSelect(c.phone)}
                  onContextMenu=${(e) => { e.preventDefault(); onContextMenu && onContextMenu({ x: e.clientX, y: e.clientY, phone: c.phone, aiEnabled: c.ai_enabled !== false }); }}
                  class="wa-contact-row flex items-center pl-[13px] pr-[15px] cursor-pointer ${
                    selected === c.phone ? 'bg-wa-selected' : 'hover:bg-wa-hover'
                  }"
                >
                  <!-- Avatar -->
                  <div class="w-[49px] h-[49px] rounded-full overflow-hidden shrink-0 mr-[13px]">
                    ${c.is_group
                      ? html`<${GroupAvatar} size=${49} />`
                      : html`<${DefaultAvatar} size=${49} />`
                    }
                  </div>

                  <!-- Text content with bottom border -->
                  <div class="flex-1 min-w-0 border-b border-wa-border py-[13px]">
                    <div class="flex justify-between items-baseline">
                      <span class="text-wa-text text-[17px] truncate leading-[21px]">
                        ${c.is_group ? (c.group_name || c.name || c.phone) : ((c.name || '').replace(/^~/, '') || c.phone)}
                        ${!c.is_group && c.name && c.name.startsWith('~')
                          ? html`<span class="ml-[6px] text-[10px] font-semibold text-blue-400 bg-blue-500/15 rounded px-[5px] py-[1px] align-middle" title="Nome obtido do WhatsApp">WA</span>`
                          : null
                        }
                        ${c.ai_enabled === false
                          ? html`<span class="ml-[6px] text-[10px] font-semibold text-red-400 bg-red-500/15 rounded px-[5px] py-[1px] align-middle">IA OFF</span>`
                          : html`<span class="ml-[6px] text-[10px] font-semibold text-green-400 bg-green-500/15 rounded px-[5px] py-[1px] align-middle">IA</span>`
                        }
                      </span>
                      <span class="text-wa-secondary text-[12px] ml-[6px] shrink-0 leading-[14px]">${formatTime(c.last_message_ts)}</span>
                    </div>
                    ${(c.tags && c.tags.length > 0) ? html`
                      <div class="flex items-center gap-[3px] mt-[2px] flex-wrap">
                        ${c.tags.slice(0, 3).map(tagName => {
                          const tagInfo = globalTags && globalTags[tagName];
                          const color = tagInfo ? tagInfo.color : '#6b7280';
                          return html`<span
                            class="text-[9px] font-semibold rounded px-[4px] py-[0.5px] max-w-[70px] truncate leading-[14px]"
                            style="background: ${color}20; color: ${color}; border: 1px solid ${color}40;"
                            title=${tagName}
                          >${tagName}</span>`;
                        })}
                        ${c.tags.length > 3 ? html`<span class="text-[9px] text-wa-secondary">+${c.tags.length - 3}</span>` : null}
                      </div>
                    ` : null}
                    <div class="flex justify-between items-center mt-[3px]">
                      ${typingState && typingState[c.phone]
                        ? html`<span class="text-[14px] truncate leading-[20px] text-wa-teal font-medium">
                            ${typingState[c.phone] === 'audio' ? 'gravando áudio...' : 'digitando...'}
                          </span>`
                        : html`<span class="text-wa-secondary text-[14px] truncate leading-[20px]">
                            ${c.last_message_role === 'assistant' ? html`<${DoubleCheckIcon} />` : ''}${c.last_message ? c.last_message.substring(0, 80) : ''}
                          </span>`
                      }
                      ${(c.unread_ai_count > 0 || c.unread_count > 0) ? html`
                        <div class="flex items-center gap-[4px] ml-auto pl-[6px] shrink-0">
                          ${c.unread_ai_count > 0 ? html`
                            <span class="bg-blue-500 text-white text-[11px] font-bold min-w-[20px] h-[20px] rounded-full flex items-center justify-center px-[3px]">
                              ${c.unread_ai_count}
                            </span>
                          ` : null}
                          ${c.unread_count > 0 ? html`
                            <span class="bg-wa-badge text-white text-[11px] font-bold min-w-[20px] h-[20px] rounded-full flex items-center justify-center px-[3px]">
                              ${c.unread_count}
                            </span>
                          ` : null}
                        </div>
                      ` : null}
                    </div>
                  </div>
                </div>
              `)
        }
      </div>
    </div>
  `;
}
