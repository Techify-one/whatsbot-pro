import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { updateContactInfo, updateContactTags, createTag } from '../../services/api.js';
import { CloseIcon, DefaultAvatar, GroupAvatar, TrashIcon, PlusIcon } from './icons.js';

const html = htm.bind(h);

const TAG_COLORS = [
  '#ef4444', '#f97316', '#f59e0b', '#84cc16', '#10b981',
  '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899', '#6b7280',
];

// ── Contact Info Panel (WhatsApp Web style slide-in) ─────────────

export function ContactInfoPanel({ phone, info, contactTags, globalTags, onGlobalTagsChange, isGroup, groupName, onClose, onSave }) {
  const [form, setForm] = useState({ name: '', email: '', profession: '', company: '', address: '', observations: [] });
  const [tags, setTags] = useState([]);
  const [saving, setSaving] = useState(false);
  const [newObs, setNewObs] = useState('');

  // Tag editor state
  const [tagSearch, setTagSearch] = useState('');
  const [showTagDropdown, setShowTagDropdown] = useState(false);
  const [creatingTag, setCreatingTag] = useState(false);
  const [newTagName, setNewTagName] = useState('');
  const [newTagColor, setNewTagColor] = useState(TAG_COLORS[0]);
  const tagDropdownRef = useRef(null);

  // Sync form when info/phone changes
  useEffect(() => {
    if (info) {
      setForm({
        name: (info.name || '').replace(/^~/, ''),
        email: info.email || '',
        profession: info.profession || '',
        company: info.company || '',
        address: info.address || '',
        observations: [...(info.observations || [])],
      });
    }
    setTags([...(contactTags || [])]);
  }, [phone, info, contactTags]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e) {
      if (tagDropdownRef.current && !tagDropdownRef.current.contains(e.target)) {
        setShowTagDropdown(false);
      }
    }
    if (showTagDropdown) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showTagDropdown]);

  function setField(key, value) {
    setForm(prev => ({ ...prev, [key]: value }));
  }

  function addObservation() {
    const text = newObs.trim();
    if (!text) return;
    setForm(prev => ({ ...prev, observations: [...prev.observations, text] }));
    setNewObs('');
  }

  function removeObservation(idx) {
    setForm(prev => ({ ...prev, observations: prev.observations.filter((_, i) => i !== idx) }));
  }

  // Tag management
  function addTagToContact(tagName) {
    if (!tags.includes(tagName)) {
      setTags(prev => [...prev, tagName]);
    }
    setTagSearch('');
    setShowTagDropdown(false);
  }

  function removeTagFromContact(tagName) {
    setTags(prev => prev.filter(t => t !== tagName));
  }

  async function handleCreateTag() {
    const name = newTagName.trim();
    if (!name) return;
    const res = await createTag(name, newTagColor);
    if (res.ok) {
      // Update global tags in parent
      onGlobalTagsChange(prev => ({ ...prev, [name]: { color: newTagColor } }));
      addTagToContact(name);
      setCreatingTag(false);
      setNewTagName('');
      setNewTagColor(TAG_COLORS[0]);
    }
  }

  // Filter available tags (not already assigned)
  const availableTags = Object.entries(globalTags || {})
    .filter(([name]) => !tags.includes(name))
    .filter(([name]) => !tagSearch || name.toLowerCase().includes(tagSearch.toLowerCase()));

  const searchHasExactMatch = Object.keys(globalTags || {}).some(
    name => name.toLowerCase() === tagSearch.trim().toLowerCase()
  );

  async function handleSave() {
    setSaving(true);
    try {
      const [infoRes, tagsRes] = await Promise.all([
        updateContactInfo(phone, form),
        updateContactTags(phone, tags),
      ]);
      if (infoRes.ok) {
        onSave(infoRes.data, tagsRes.ok ? tagsRes.data.tags : tags);
      }
    } catch (err) {
      console.error('Failed to save contact info:', err);
    }
    setSaving(false);
  }

  const fields = [
    { key: 'name', label: 'Nome', placeholder: 'Nome do contato' },
    { key: 'email', label: 'Email', placeholder: 'email@exemplo.com' },
    { key: 'profession', label: 'Profissão', placeholder: 'Ex: Desenvolvedor' },
    { key: 'company', label: 'Empresa', placeholder: 'Nome da empresa' },
    { key: 'address', label: 'Endereço', placeholder: 'Rua, número, bairro' },
  ];

  return html`
    <div class="absolute inset-0 z-50 flex justify-end" onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="w-full lg:w-[400px] h-full bg-wa-panel flex flex-col shadow-xl animate-slide-in-right">
        <!-- Header -->
        <div class="h-[59px] flex items-center px-4 bg-wa-teal shrink-0 gap-4">
          <button onClick=${onClose} class="text-white hover:opacity-80 shrink-0">
            <${CloseIcon} />
          </button>
          <span class="text-white text-[16px] font-medium">Dados do contato</span>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto wa-scrollbar">
          <!-- Avatar -->
          <div class="flex flex-col items-center py-7 bg-wa-panel">
            <div class="w-[200px] h-[200px] rounded-full overflow-hidden mb-3">
              ${isGroup
                ? html`<${GroupAvatar} size=${200} />`
                : html`<${DefaultAvatar} size=${200} />`
              }
            </div>
            <div class="text-wa-text text-[22px] font-light">${isGroup ? (groupName || phone) : (form.name || phone)}</div>
            ${isGroup
              ? html`<div class="text-wa-secondary text-[14px] mt-0.5">Grupo</div>`
              : form.name ? html`<div class="text-wa-secondary text-[14px] mt-0.5">${phone}</div>` : null}
          </div>

          <!-- Fields -->
          <div class="bg-wa-bg px-6 py-4 space-y-4">
            ${fields.map(f => html`
              <div key=${f.key}>
                <label class="text-wa-iconActive text-[13px] font-medium block mb-1">${f.label}</label>
                <div class="flex items-center gap-2">
                  <input
                    type="text"
                    value=${form[f.key]}
                    onInput=${(e) => setField(f.key, e.target.value)}
                    placeholder=${f.placeholder}
                    class="flex-1 bg-wa-panel text-wa-text text-[15px] rounded-[8px] px-3 py-2 border border-wa-border outline-none placeholder-wa-secondary focus:border-wa-iconActive transition-colors"
                  />
                </div>
              </div>
            `)}

            <!-- Tags -->
            <div>
              <label class="text-wa-iconActive text-[13px] font-medium block mb-1">Tags</label>

              <!-- Current tags as removable chips -->
              ${tags.length > 0 ? html`
                <div class="flex flex-wrap gap-[5px] mb-2">
                  ${tags.map(tagName => {
                    const tagInfo = (globalTags || {})[tagName];
                    const color = tagInfo ? tagInfo.color : '#6b7280';
                    return html`
                      <span
                        key=${tagName}
                        class="inline-flex items-center gap-[3px] text-[12px] font-medium rounded-full px-[8px] py-[2px] leading-[18px]"
                        style="background: ${color}20; color: ${color}; border: 1px solid ${color}40;"
                      >
                        ${tagName}
                        <button
                          type="button"
                          onClick=${() => removeTagFromContact(tagName)}
                          class="ml-[1px] hover:opacity-70 leading-none text-[14px]"
                          title="Remover tag"
                        >\u00d7</button>
                      </span>
                    `;
                  })}
                </div>
              ` : null}

              <!-- Tag search / add dropdown -->
              <div class="relative" ref=${tagDropdownRef}>
                <input
                  type="text"
                  value=${tagSearch}
                  onInput=${(e) => { setTagSearch(e.target.value); setShowTagDropdown(true); setCreatingTag(false); }}
                  onFocus=${() => { setShowTagDropdown(true); setCreatingTag(false); }}
                  placeholder="Buscar ou criar tag..."
                  class="w-full bg-wa-panel text-wa-text text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none placeholder-wa-secondary focus:border-wa-iconActive transition-colors"
                />

                ${showTagDropdown ? html`
                  <div class="absolute left-0 right-0 top-full mt-1 bg-wa-panel border border-wa-border rounded-[8px] shadow-lg z-10 max-h-[200px] overflow-y-auto wa-scrollbar">
                    ${!creatingTag ? html`
                      ${availableTags.map(([name, tagData]) => html`
                        <button
                          key=${name}
                          type="button"
                          onClick=${() => addTagToContact(name)}
                          class="w-full text-left px-3 py-2 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2"
                        >
                          <span
                            class="w-[10px] h-[10px] rounded-full shrink-0"
                            style="background: ${tagData.color};"
                          ></span>
                          <span class="text-wa-text">${name}</span>
                        </button>
                      `)}
                      ${availableTags.length === 0 && !tagSearch.trim() ? html`
                        <div class="px-3 py-2 text-[13px] text-wa-secondary">Nenhuma tag disponível</div>
                      ` : null}
                      ${tagSearch.trim() && !searchHasExactMatch ? html`
                        <button
                          type="button"
                          onClick=${() => { setCreatingTag(true); setNewTagName(tagSearch.trim()); }}
                          class="w-full text-left px-3 py-2 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 border-t border-wa-border"
                        >
                          <${PlusIcon} />
                          <span class="text-wa-iconActive font-medium">Criar "${tagSearch.trim()}"</span>
                        </button>
                      ` : null}
                      ${!tagSearch.trim() ? html`
                        <button
                          type="button"
                          onClick=${() => setCreatingTag(true)}
                          class="w-full text-left px-3 py-2 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 border-t border-wa-border"
                        >
                          <${PlusIcon} />
                          <span class="text-wa-iconActive font-medium">Criar nova tag</span>
                        </button>
                      ` : null}
                    ` : html`
                      <!-- Create new tag form -->
                      <div class="p-3 space-y-3">
                        <input
                          type="text"
                          value=${newTagName}
                          onInput=${(e) => setNewTagName(e.target.value)}
                          onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); handleCreateTag(); } }}
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
                                onClick=${() => setNewTagColor(c)}
                                class="w-[22px] h-[22px] rounded-full border-2 transition-transform ${newTagColor === c ? 'scale-110' : 'hover:scale-105'}"
                                style="background: ${c}; border-color: ${newTagColor === c ? '#fff' : c}; box-shadow: ${newTagColor === c ? '0 0 0 2px ' + c : 'none'};"
                              />
                            `)}
                          </div>
                        </div>
                        <div class="flex gap-2">
                          <button
                            type="button"
                            onClick=${() => { setCreatingTag(false); setNewTagName(''); }}
                            class="flex-1 text-[12px] text-wa-secondary py-1.5 rounded-[6px] hover:bg-wa-hover transition-colors"
                          >Cancelar</button>
                          <button
                            type="button"
                            onClick=${handleCreateTag}
                            disabled=${!newTagName.trim()}
                            class="flex-1 text-[12px] text-white py-1.5 rounded-[6px] bg-wa-iconActive hover:opacity-90 transition-opacity disabled:opacity-50"
                          >Criar</button>
                        </div>
                      </div>
                    `}
                  </div>
                ` : null}
              </div>
            </div>

            <!-- Observations -->
            <div>
              <label class="text-wa-iconActive text-[13px] font-medium block mb-1">Observações</label>
              <div class="space-y-2">
                ${form.observations.map((obs, i) => html`
                  <div key=${i} class="flex items-start gap-2 bg-wa-panel rounded-[8px] px-3 py-2 border border-wa-border group">
                    <span class="flex-1 text-wa-text text-[14px] break-words">${obs}</span>
                    <button
                      type="button"
                      onClick=${() => removeObservation(i)}
                      class="shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 hover:opacity-100 transition-opacity"
                      title="Remover"
                    >
                      <${TrashIcon} />
                    </button>
                  </div>
                `)}
                <!-- Add new observation -->
                <div class="flex items-center gap-2">
                  <input
                    type="text"
                    value=${newObs}
                    onInput=${(e) => setNewObs(e.target.value)}
                    onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); addObservation(); } }}
                    placeholder="Adicionar observação..."
                    class="flex-1 bg-wa-panel text-wa-text text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none placeholder-wa-secondary focus:border-wa-iconActive transition-colors"
                  />
                  <button
                    type="button"
                    onClick=${addObservation}
                    class="shrink-0 p-1 hover:bg-wa-hover rounded-full transition-colors"
                    title="Adicionar"
                  >
                    <${PlusIcon} />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Save button -->
        <div class="px-6 py-4 bg-wa-panel border-t border-wa-border shrink-0">
          <button
            onClick=${handleSave}
            disabled=${saving}
            class="w-full bg-wa-iconActive text-white text-[15px] font-medium py-2.5 rounded-[8px] hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            ${saving ? 'Salvando...' : 'Salvar'}
          </button>
        </div>
      </div>
    </div>
  `;
}
