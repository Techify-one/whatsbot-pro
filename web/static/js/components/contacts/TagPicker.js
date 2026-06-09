import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { PlusIcon } from './icons.js';

const html = htm.bind(h);

export const TAG_COLORS = [
  '#ef4444', '#f97316', '#f59e0b', '#84cc16', '#10b981',
  '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899', '#6b7280',
];

// Reusable searchable tag list with inline "create tag". Shared by the contact
// context menu (right-click) and the bulk-selection menu.
//
//  - globalTags:  { name: { color } } — every existing tag.
//  - isActive(name): bool — drives the checkbox state (assigned to the contact /
//                    to all selected conversations).
//  - onToggle(name): apply/remove the tag.
//  - onCreateTag(name, color): create a NEW global tag; returns a truthy promise on
//                    success. The freshly created tag is then applied via onToggle.
export function TagPicker({ globalTags, isActive, onToggle, onCreateTag }) {
  const [search, setSearch] = useState('');
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newColor, setNewColor] = useState(TAG_COLORS[0]);

  const term = search.trim();
  const entries = Object.entries(globalTags || {})
    .filter(([name]) => !term || name.toLowerCase().includes(term.toLowerCase()));
  const hasExact = Object.keys(globalTags || {}).some(
    n => n.toLowerCase() === term.toLowerCase()
  );

  async function doCreate(name) {
    const n = (name || '').trim();
    if (!n) return;
    const ok = await onCreateTag(n, newColor);
    if (ok) {
      if (!isActive(n)) onToggle(n);
      setCreating(false);
      setNewName('');
      setNewColor(TAG_COLORS[0]);
      setSearch('');
    }
  }

  return html`
    <div class="border-t border-wa-border">
      ${!creating ? html`
        <div class="p-[6px]">
          <input
            type="text"
            value=${search}
            onInput=${(e) => setSearch(e.target.value)}
            placeholder="Buscar tag..."
            class="w-full bg-wa-bg text-wa-text text-[13px] rounded-[6px] px-2.5 py-1.5 border border-wa-border outline-none placeholder-wa-secondary focus:border-wa-iconActive"
          />
        </div>
        <div class="max-h-[200px] overflow-y-auto wa-scrollbar">
          ${entries.map(([name, tagData]) => {
            const active = isActive(name);
            return html`
              <button
                key=${name}
                type="button"
                onClick=${() => onToggle(name)}
                class="w-full text-left px-4 py-[8px] text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-3"
                title=${active ? 'Clique para remover' : 'Clique para adicionar'}
              >
                <span
                  class="w-[16px] h-[16px] rounded border-2 flex items-center justify-center shrink-0"
                  style="border-color:${tagData.color}; background:${active ? tagData.color : 'transparent'};"
                >
                  ${active ? html`<svg viewBox="0 0 24 24" width="12" height="12" fill="white"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>` : ''}
                </span>
                <span class="font-medium truncate" style="color:${tagData.color};">${name}</span>
              </button>
            `;
          })}
          ${entries.length === 0 && term ? html`
            <div class="px-4 py-[8px] text-[13px] text-wa-secondary">Nenhuma tag encontrada</div>
          ` : null}
          ${entries.length === 0 && !term ? html`
            <div class="px-4 py-[8px] text-[13px] text-wa-secondary">Nenhuma tag criada</div>
          ` : null}
        </div>
        ${(term && !hasExact) ? html`
          <button
            type="button"
            onClick=${() => { setCreating(true); setNewName(term); }}
            class="w-full text-left px-4 py-[8px] text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 border-t border-wa-border"
          >
            <${PlusIcon} />
            <span class="text-wa-iconActive font-medium truncate">Criar "${term}"</span>
          </button>
        ` : html`
          <button
            type="button"
            onClick=${() => { setCreating(true); setNewName(''); }}
            class="w-full text-left px-4 py-[8px] text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 border-t border-wa-border"
          >
            <${PlusIcon} />
            <span class="text-wa-iconActive font-medium">Criar nova tag</span>
          </button>
        `}
      ` : html`
        <div class="p-3 space-y-3">
          <input
            type="text"
            value=${newName}
            onInput=${(e) => setNewName(e.target.value)}
            onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); doCreate(newName); } }}
            placeholder="Nome da tag"
            class="w-full bg-wa-bg text-wa-text text-[13px] rounded-[6px] px-2.5 py-1.5 border border-wa-border outline-none placeholder-wa-secondary focus:border-wa-iconActive"
            autoFocus
          />
          <div>
            <div class="text-wa-secondary text-[11px] mb-1.5">Cor</div>
            <div class="flex flex-wrap gap-[6px]">
              ${TAG_COLORS.map(c => html`
                <button
                  key=${c}
                  type="button"
                  onClick=${() => setNewColor(c)}
                  class="w-[22px] h-[22px] rounded-full border-2 transition-transform ${newColor === c ? 'scale-110' : 'hover:scale-105'}"
                  style="background:${c}; border-color:${newColor === c ? '#fff' : c}; box-shadow:${newColor === c ? '0 0 0 2px ' + c : 'none'};"
                />
              `)}
            </div>
          </div>
          <div class="flex gap-2">
            <button
              type="button"
              onClick=${() => { setCreating(false); setNewName(''); }}
              class="flex-1 text-[12px] text-wa-secondary py-1.5 rounded-[6px] hover:bg-wa-hover transition-colors"
            >Cancelar</button>
            <button
              type="button"
              onClick=${() => doCreate(newName)}
              disabled=${!newName.trim()}
              class="flex-1 text-[12px] text-white py-1.5 rounded-[6px] bg-wa-iconActive hover:opacity-90 transition-opacity disabled:opacity-50"
            >Criar</button>
          </div>
        </div>
      `}
    </div>
  `;
}
