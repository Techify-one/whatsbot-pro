import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  getConversationLabels, getConversationLabelsFor,
  createConversationLabel, updateConversationLabels,
} from '../../services/api.js';
import { PlusIcon } from './icons.js';
import { hasPermission } from '../../utils/permissions.js';

const html = htm.bind(h);

// Conversation labels editor (Onda 3) — chips + search + inline create, mirroring
// the contact-tag editor but for conversation-scoped labels. Changes apply
// immediately (snapshot PUT). Visually distinct from contact tags (🏷️ prefix).
// Dark-mode-safe: wa-* classes only.

const LABEL_COLORS = [
  '#ef4444', '#f97316', '#f59e0b', '#84cc16', '#10b981',
  '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899', '#6b7280',
];

export function ConversationLabelEditor({ conversationId, currentUser = null }) {
  // P48: creating/editing the GLOBAL label registry needs conversation_label.manage.
  // Associating existing labels to the conversation is gated at the call site
  // (conversation.reply); here we only hide the "create new label" affordances.
  const canManageLabels = hasPermission(currentUser, 'conversation_label.manage');
  const [globalLabels, setGlobalLabels] = useState([]);   // [{id,name,color}]
  const [selected, setSelected] = useState([]);            // [name]
  const [search, setSearch] = useState('');
  const [showDropdown, setShowDropdown] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newColor, setNewColor] = useState(LABEL_COLORS[0]);
  const [busy, setBusy] = useState(false);
  const dropdownRef = useRef(null);

  const colorOf = (name) => {
    const l = globalLabels.find(x => x.name === name);
    return l ? l.color : '#6b7280';
  };

  // Load the global registry once.
  useEffect(() => {
    let alive = true;
    getConversationLabels().then(res => {
      if (alive && res && res.ok) setGlobalLabels(res.data || []);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Load this conversation's current labels.
  useEffect(() => {
    if (conversationId == null) { setSelected([]); return; }
    let alive = true;
    getConversationLabelsFor(conversationId).then(res => {
      if (alive && res && res.ok && res.data) {
        setSelected((res.data.labels || []).map(l => l.name));
      }
    }).catch(() => {});
    return () => { alive = false; };
  }, [conversationId]);

  // Close dropdown on outside click.
  useEffect(() => {
    function onDoc(e) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setShowDropdown(false);
    }
    if (showDropdown) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [showDropdown]);

  async function persist(names) {
    if (conversationId == null || busy) return;
    setBusy(true);
    const prev = selected;
    setSelected(names);   // optimistic
    try {
      const res = await updateConversationLabels(conversationId, names);
      if (res && res.ok && res.data) setSelected(res.data.labels || names);
      else setSelected(prev);
    } catch (e) {
      setSelected(prev);
    }
    setBusy(false);
  }

  function addLabel(name) {
    setSearch('');
    setShowDropdown(false);
    if (!selected.includes(name)) persist([...selected, name]);
  }

  function removeLabel(name) {
    persist(selected.filter(n => n !== name));
  }

  async function handleCreate() {
    const name = newName.trim();
    if (!name || busy) return;
    setBusy(true);
    const res = await createConversationLabel(name, newColor);
    setBusy(false);
    if (res && res.ok && res.data) {
      setGlobalLabels(prev => [...prev.filter(l => l.name !== res.data.name), res.data]);
      setCreating(false);
      setNewName('');
      setNewColor(LABEL_COLORS[0]);
      addLabel(res.data.name);
    }
  }

  const available = globalLabels
    .filter(l => !selected.includes(l.name))
    .filter(l => !search || l.name.toLowerCase().includes(search.toLowerCase()));
  const searchHasExact = globalLabels.some(l => l.name.toLowerCase() === search.trim().toLowerCase());

  return html`
    <div>
      <label class="text-wa-iconActive text-[13px] font-semibold block mb-1.5">Etiquetas da conversa</label>

      ${selected.length > 0 ? html`
        <div class="flex flex-wrap gap-[5px] mb-2">
          ${selected.map(name => {
            const color = colorOf(name);
            return html`
              <span key=${name}
                class="inline-flex items-center gap-[3px] text-[12px] font-medium rounded-full px-[8px] py-[2px] leading-[18px]"
                style="background: ${color}20; color: ${color}; border: 1px solid ${color}40;">
                🏷️ ${name}
                <button type="button" onClick=${() => removeLabel(name)}
                  class="ml-[1px] hover:opacity-70 leading-none text-[14px]" title="Remover etiqueta">×</button>
              </span>
            `;
          })}
        </div>
      ` : null}

      <div class="relative" ref=${dropdownRef}>
        <input
          type="text"
          value=${search}
          onInput=${(e) => { setSearch(e.target.value); setShowDropdown(true); setCreating(false); }}
          onFocus=${() => { setShowDropdown(true); setCreating(false); }}
          placeholder="Buscar ou criar etiqueta..."
          class="w-full bg-wa-panel text-wa-text text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none placeholder-wa-secondary focus:border-wa-iconActive transition-colors"
        />

        ${showDropdown ? html`
          <div class="absolute left-0 right-0 top-full mt-1 bg-wa-panel border border-wa-border rounded-[8px] shadow-lg z-10 max-h-[220px] overflow-y-auto wa-scrollbar">
            ${!creating ? html`
              ${available.map(l => html`
                <button key=${l.id} type="button" onClick=${() => addLabel(l.name)}
                  class="w-full text-left px-3 py-2 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2">
                  <span class="w-[10px] h-[10px] rounded-full shrink-0" style="background: ${l.color};"></span>
                  <span class="text-wa-text">${l.name}</span>
                </button>
              `)}
              ${available.length === 0 && !search.trim() ? html`
                <div class="px-3 py-2 text-[13px] text-wa-secondary">Nenhuma etiqueta disponível</div>
              ` : null}
              ${canManageLabels && search.trim() && !searchHasExact ? html`
                <button type="button"
                  onClick=${() => { setCreating(true); setNewName(search.trim()); }}
                  class="w-full text-left px-3 py-2 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 border-t border-wa-border">
                  <${PlusIcon} />
                  <span class="text-wa-iconActive font-medium">Criar "${search.trim()}"</span>
                </button>
              ` : null}
              ${canManageLabels && !search.trim() ? html`
                <button type="button" onClick=${() => setCreating(true)}
                  class="w-full text-left px-3 py-2 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 border-t border-wa-border">
                  <${PlusIcon} />
                  <span class="text-wa-iconActive font-medium">Criar nova etiqueta</span>
                </button>
              ` : null}
            ` : html`
              <div class="p-3 space-y-3">
                <input type="text" value=${newName}
                  onInput=${(e) => setNewName(e.target.value)}
                  onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); handleCreate(); } }}
                  placeholder="Nome da etiqueta"
                  class="w-full bg-wa-bg text-wa-text text-[13px] rounded-[6px] px-2.5 py-1.5 border border-wa-border outline-none placeholder-wa-secondary focus:border-wa-iconActive"
                  autoFocus />
                <div>
                  <div class="text-wa-secondary text-[11px] mb-1.5">Cor</div>
                  <div class="flex flex-wrap gap-[6px]">
                    ${LABEL_COLORS.map(c => html`
                      <button key=${c} type="button" onClick=${() => setNewColor(c)}
                        class="w-[22px] h-[22px] rounded-full border-2 transition-transform ${newColor === c ? 'scale-110' : 'hover:scale-105'}"
                        style="background: ${c}; border-color: ${newColor === c ? '#fff' : c}; box-shadow: ${newColor === c ? '0 0 0 2px ' + c : 'none'};" />
                    `)}
                  </div>
                </div>
                <div class="flex gap-2">
                  <button type="button" onClick=${() => { setCreating(false); setNewName(''); }}
                    class="flex-1 text-[12px] text-wa-secondary py-1.5 rounded-[6px] hover:bg-wa-hover transition-colors">Cancelar</button>
                  <button type="button" onClick=${handleCreate} disabled=${!newName.trim() || busy}
                    class="flex-1 text-[12px] text-white py-1.5 rounded-[6px] bg-wa-iconActive hover:opacity-90 transition-opacity disabled:opacity-50">Criar</button>
                </div>
              </div>
            `}
          </div>
        ` : null}
      </div>
    </div>
  `;
}

