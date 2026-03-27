import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { updateContactInfo } from '../../services/api.js';
import { CloseIcon, DefaultAvatar, GroupAvatar, TrashIcon, PlusIcon } from './icons.js';

const html = htm.bind(h);

// ── Contact Info Panel (WhatsApp Web style slide-in) ─────────────

export function ContactInfoPanel({ phone, info, isGroup, groupName, onClose, onSave }) {
  const [form, setForm] = useState({ name: '', email: '', profession: '', company: '', address: '', observations: [] });
  const [saving, setSaving] = useState(false);
  const [newObs, setNewObs] = useState('');

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
  }, [phone, info]);

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

  async function handleSave() {
    setSaving(true);
    try {
      const res = await updateContactInfo(phone, form);
      if (res.ok) {
        onSave(res.data);
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
