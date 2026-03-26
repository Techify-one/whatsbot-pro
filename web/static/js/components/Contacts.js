import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getContacts, getContact, sendMessage, retrySend, sendImage, sendAudio, markAsRead, updateContactInfo, toggleContactAI, sendPresence } from '../services/api.js';

const html = htm.bind(h);

// ── Time formatting ──────────────────────────────────────────────

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const diffDays = Math.floor((now - d) / 86400000);
  if (diffDays === 0) return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  if (diffDays === 1) return 'Ontem';
  if (diffDays < 7) return d.toLocaleDateString('pt-BR', { weekday: 'short' });
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
}

function formatBubbleTime(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

// ── SVG Icons (WhatsApp Web exact style) ─────────────────────────

function SearchIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="20" height="20" class="shrink-0">
      <path fill="#54656f" d="M15.009 13.805h-.636l-.22-.219a5.184 5.184 0 001.257-3.386 5.207 5.207 0 10-5.207 5.208 5.183 5.183 0 003.385-1.258l.22.22v.635l4.004 3.999 1.194-1.195-3.997-4.004zm-4.806 0a3.6 3.6 0 110-7.202 3.6 3.6 0 010 7.202z"/>
    </svg>
  `;
}

function SendIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" class="shrink-0">
      <path d="M1.101 21.757L23.8 12.028 1.101 2.3l-.01 7.51 16.29 2.218-16.29 2.218.01 7.51z"/>
    </svg>
  `;
}

function BackArrowIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="#54656f">
      <path d="M12 4l1.4 1.4L7.8 11H20v2H7.8l5.6 5.6L12 20l-8-8 8-8z"/>
    </svg>
  `;
}

function DefaultAvatar({ size = 49 }) {
  return html`
    <svg viewBox="0 0 212 212" width="${size}" height="${size}">
      <path fill="#DFE5E7" d="M106.251.5C164.653.5 212 47.846 212 106.25S164.653 212 106.25 212C47.846 212 .5 164.654.5 106.25S47.846.5 106.251.5z"/>
      <path fill="#FFF" d="M173.561 171.615a62.767 62.767 0 00-16.06-22.06 62.91 62.91 0 00-22.794-14.132 17.694 17.694 0 001.883-1.467c7.87-7.168 12.762-17.434 12.762-28.812s-4.893-21.644-12.762-28.812c-7.869-7.168-18.753-11.597-30.84-11.597s-22.971 4.43-30.84 11.597c-7.87 7.168-12.762 17.434-12.762 28.812s4.892 21.644 12.762 28.812a17.71 17.71 0 001.883 1.467 62.91 62.91 0 00-22.794 14.131 62.769 62.769 0 00-16.06 22.06A105.752 105.752 0 01.5 106.25C.5 47.846 47.846.5 106.251.5S212 47.846 212 106.25a105.754 105.754 0 01-38.439 65.365z"/>
    </svg>
  `;
}

function EmojiIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="26" height="26" fill="#54656f" class="shrink-0">
      <path d="M9.153 11.603c.795 0 1.439-.879 1.439-1.962s-.644-1.962-1.439-1.962-1.439.879-1.439 1.962.644 1.962 1.439 1.962zm5.694 0c.795 0 1.439-.879 1.439-1.962s-.644-1.962-1.439-1.962-1.439.879-1.439 1.962.644 1.962 1.439 1.962zM12 2C6.486 2 2 6.486 2 12s4.486 10 10 10 10-4.486 10-10S17.514 2 12 2zm0 18c-4.411 0-8-3.589-8-8s3.589-8 8-8 8 3.589 8 8-3.589 8-8 8zm-.002-3.299a5.078 5.078 0 01-4.759-3.294h1.628a3.498 3.498 0 003.13 1.935 3.498 3.498 0 003.131-1.935h1.628a5.078 5.078 0 01-4.758 3.294z"/>
    </svg>
  `;
}

function AttachIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="#54656f" class="shrink-0">
      <path d="M1.816 15.556v.002c0 1.502.584 2.912 1.646 3.972s2.472 1.647 3.974 1.647a5.58 5.58 0 003.972-1.645l9.547-9.548c.769-.768 1.147-1.767 1.058-2.817-.079-.968-.548-1.927-1.319-2.698-1.594-1.592-4.068-1.711-5.517-.262l-7.916 7.915c-.881.881-.792 2.25.214 3.261.501.501 1.134.787 1.735.787.464 0 .882-.182 1.213-.509l5.511-5.512a.75.75 0 10-1.063-1.06l-5.509 5.509c-.093.093-.186.104-.241.104-.181 0-.477-.177-.717-.42-.488-.487-.574-1.049-.214-1.41l7.916-7.915c.899-.898 2.632-.832 3.857.393.579.578.897 1.248.947 1.888.052.654-.219 1.303-.762 1.846l-9.547 9.548a4.08 4.08 0 01-2.913 1.205 4.08 4.08 0 01-2.913-1.205 4.08 4.08 0 01-1.205-2.911 4.08 4.08 0 011.205-2.913l8.097-8.098a.75.75 0 10-1.063-1.06L3.463 11.59A5.58 5.58 0 001.816 15.556z"/>
    </svg>
  `;
}

function MicIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="#54656f" class="shrink-0">
      <path d="M11.999 14.942c2.001 0 3.531-1.53 3.531-3.531V4.35c0-2.001-1.53-3.531-3.531-3.531S8.469 2.35 8.469 4.35v7.061c0 2.001 1.53 3.531 3.53 3.531zm6.238-3.53c0 3.531-2.942 6.002-6.237 6.002s-6.237-2.471-6.237-6.002H4.761c0 3.885 3.009 7.06 6.737 7.533v3.236h1.004v-3.236c3.728-.472 6.737-3.648 6.737-7.533h-1.002z"/>
    </svg>
  `;
}

function DoubleCheckIcon() {
  return html`
    <svg viewBox="0 0 16 11" width="16" height="11" class="inline-block mr-[3px] align-middle shrink-0">
      <path d="M11.071.653a.457.457 0 00-.304-.102.493.493 0 00-.381.178l-6.19 7.636-2.011-2.095a.463.463 0 00-.336-.153.48.48 0 00-.347.143.45.45 0 00-.14.337c0 .122.052.24.143.343l2.304 2.394c.096.099.218.153.35.153.132 0 .255-.058.348-.161L11.1 1.308a.452.452 0 00.109-.296.452.452 0 00-.138-.36z" fill="#53bdeb"/>
      <path d="M14.925.653a.457.457 0 00-.304-.102.493.493 0 00-.381.178l-6.19 7.636-1.006-1.048-.352.388.988 1.027c.096.099.218.153.35.153.132 0 .255-.058.348-.161l7.572-8.327a.452.452 0 00.109-.296.452.452 0 00-.134-.448z" fill="#53bdeb"/>
    </svg>
  `;
}

function ClockIcon() {
  return html`
    <svg viewBox="0 0 16 15" width="14" height="14" class="inline-block mr-[3px] align-middle shrink-0">
      <path d="M9.75 7.713H8.244V5.359a.5.5 0 00-.5-.5H7.65a.5.5 0 00-.5.5v2.947a.5.5 0 00.5.5h2.1a.5.5 0 00.5-.5v-.094a.5.5 0 00-.5-.5zm-1.2-5.783A5.545 5.545 0 003 7.475a5.545 5.545 0 005.55 5.546 5.545 5.545 0 005.55-5.546A5.545 5.545 0 008.55 1.93z" fill="#92a58c"/>
    </svg>
  `;
}

function FailedIcon() {
  return html`
    <svg viewBox="0 0 16 16" width="14" height="14" class="inline-block mr-[3px] align-middle shrink-0">
      <path d="M8 1.5a6.5 6.5 0 110 13 6.5 6.5 0 010-13zM7.25 5v4.5h1.5V5h-1.5zm0 6v1.5h1.5V11h-1.5z" fill="#e53e3e"/>
    </svg>
  `;
}

function RetryIcon({ onClick }) {
  return html`
    <button onClick=${onClick} class="ml-[6px] inline-flex items-center align-middle opacity-70 hover:opacity-100 transition-opacity" title="Reenviar mensagem" style="background:none;border:none;cursor:pointer;padding:2px;">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="#e53e3e">
        <path d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>
      </svg>
    </button>
  `;
}

// ── Context Menu ─────────────────────────────────────────────────

function ContextMenu({ x, y, phone, aiEnabled, onToggleAI, onEditContact, onClose }) {
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

// ── Contact List (WhatsApp Web sidebar) ──────────────────────────

function ContactList({ contacts, loading, search, onSearchChange, selected, onSelect, onContextMenu, typingState }) {
  return html`
    <div class="flex flex-col h-full bg-wa-bg">
      <!-- Green header bar -->
      <div class="h-[59px] flex items-center justify-between px-4 bg-wa-teal shrink-0">
        <div class="flex items-center gap-3">
          <div class="w-[40px] h-[40px] rounded-full overflow-hidden">
            <${DefaultAvatar} size=${40} />
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-white text-[15px] font-medium opacity-90">WhatsBot</span>
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
                    <${DefaultAvatar} size=${49} />
                  </div>

                  <!-- Text content with bottom border -->
                  <div class="flex-1 min-w-0 border-b border-wa-border py-[13px]">
                    <div class="flex justify-between items-baseline">
                      <span class="text-wa-text text-[17px] truncate leading-[21px]">
                        ${c.name || c.phone}
                        ${c.ai_enabled === false
                          ? html`<span class="ml-[6px] text-[10px] font-semibold text-red-400 bg-red-500/15 rounded px-[5px] py-[1px] align-middle">IA OFF</span>`
                          : html`<span class="ml-[6px] text-[10px] font-semibold text-green-400 bg-green-500/15 rounded px-[5px] py-[1px] align-middle">IA</span>`
                        }
                      </span>
                      <span class="text-wa-secondary text-[12px] ml-[6px] shrink-0 leading-[14px]">${formatTime(c.last_message_ts)}</span>
                    </div>
                    <div class="flex justify-between items-center mt-[3px]">
                      ${typingState && typingState[c.phone]
                        ? html`<span class="text-[14px] truncate leading-[20px] text-wa-teal font-medium">
                            ${typingState[c.phone] === 'audio' ? 'gravando áudio...' : 'digitando...'}
                          </span>`
                        : html`<span class="text-wa-secondary text-[14px] truncate leading-[20px]">
                            ${c.last_message_role === 'assistant' ? html`<${DoubleCheckIcon} />` : ''}${c.last_message ? c.last_message.substring(0, 80) : ''}
                          </span>`
                      }
                      ${c.unread_count > 0 ? html`
                        <span class="bg-wa-badge text-white text-[11px] font-bold min-w-[20px] h-[20px] rounded-full flex items-center justify-center px-[3px] ml-[6px] shrink-0">
                          ${c.unread_count}
                        </span>
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

// ── SVG Icons for Info Panel ──────────────────────────────────────

function CloseIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
      <path d="M19.6471 4.34705L4.34705 19.647M4.34705 4.34705L19.6471 19.647" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/>
    </svg>
  `;
}

function PencilIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="18" height="18" fill="#8696a0">
      <path d="M3.95 16.7v3.4h3.4l9.8-9.8-3.4-3.4-9.8 9.8zm15.8-9.1c.4-.4.4-.9 0-1.3l-2.1-2.1c-.4-.4-.9-.4-1.3 0l-1.6 1.6 3.4 3.4 1.6-1.6z"/>
    </svg>
  `;
}

function TrashIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="16" height="16" fill="#8696a0">
      <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
    </svg>
  `;
}

function PlusIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="18" height="18" fill="#00a884">
      <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
    </svg>
  `;
}

// ── Contact Info Panel (WhatsApp Web style slide-in) ─────────────

function ContactInfoPanel({ phone, info, onClose, onSave }) {
  const [form, setForm] = useState({ name: '', email: '', profession: '', company: '', address: '', observations: [] });
  const [saving, setSaving] = useState(false);
  const [newObs, setNewObs] = useState('');

  // Sync form when info/phone changes
  useEffect(() => {
    if (info) {
      setForm({
        name: info.name || '',
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
              <${DefaultAvatar} size=${200} />
            </div>
            <div class="text-wa-text text-[22px] font-light">${form.name || phone}</div>
            ${form.name ? html`<div class="text-wa-secondary text-[14px] mt-0.5">${phone}</div>` : null}
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

// ── Contact Detail (WhatsApp Web chat panel) ─────────────────────

function StopIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="#ef4444" class="shrink-0">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  `;
}

function ContactDetail({ phone, onBack, messages, info, onAvatarClick, contactTyping, setContactData }) {
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordDuration, setRecordDuration] = useState(0);
  // pendingMedia: { type: 'image'|'audio', file, blob, filename, previewUrl }
  const [pendingMedia, setPendingMedia] = useState(null);
  const chatRef = useRef(null);
  const fileInputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const recordTimerRef = useRef(null);
  const presenceTimerRef = useRef(null);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => { setInput(''); }, [phone]);

  // Send typing presence to contact (debounced)
  function handleInputChange(e) {
    const val = e.target.value;
    setInput(val);
    if (!phone) return;
    // Send "start" on first keystroke, then debounce "stop" after 3s of inactivity
    if (val.trim()) {
      if (!presenceTimerRef.current) {
        sendPresence(phone, 'start').catch(() => {});
      }
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = setTimeout(() => {
        sendPresence(phone, 'stop').catch(() => {});
        presenceTimerRef.current = null;
      }, 3000);
    } else {
      clearTimeout(presenceTimerRef.current);
      presenceTimerRef.current = null;
      sendPresence(phone, 'stop').catch(() => {});
    }
  }

  // Clean up presence timer on unmount or phone change
  useEffect(() => {
    return () => {
      if (presenceTimerRef.current) {
        clearTimeout(presenceTimerRef.current);
        presenceTimerRef.current = null;
        if (phone) sendPresence(phone, 'stop').catch(() => {});
      }
    };
  }, [phone]);

  // Helper to find and update a message by its local ID
  function updateMsgByLocalId(localId, updater) {
    setContactData(prev => {
      if (!prev) return prev;
      const msgs = (prev.messages || []).map(m =>
        m._localId === localId ? { ...m, ...updater(m) } : m
      );
      return { ...prev, messages: msgs };
    });
  }

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    // Stop typing presence
    clearTimeout(presenceTimerRef.current);
    presenceTimerRef.current = null;
    sendPresence(phone, 'stop').catch(() => {});

    setSending(true);
    setInput('');

    // Add message optimistically
    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const msgTs = Date.now() / 1000;
    setContactData(prev => prev ? {
      ...prev,
      messages: [...(prev.messages || []), {
        role: 'assistant', content: text, ts: msgTs,
        _localId: localId, _status: 'sending',
      }],
    } : prev);

    try {
      const res = await sendMessage(phone, text);
      if (res.ok) {
        updateMsgByLocalId(localId, () => ({ _status: null }));
      } else {
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    } catch (err) {
      console.error('Send error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
    }
    setSending(false);
  }

  async function handleRetry(localId, text) {
    updateMsgByLocalId(localId, () => ({ _status: 'sending', status: null }));
    try {
      const res = await retrySend(phone, text);
      if (res.ok) {
        updateMsgByLocalId(localId, () => ({ _status: null, status: null }));
      } else {
        updateMsgByLocalId(localId, () => ({ _status: 'failed', status: 'failed' }));
      }
    } catch (err) {
      console.error('Retry error:', err);
      updateMsgByLocalId(localId, () => ({ _status: 'failed', status: 'failed' }));
    }
  }

  function handleAttachClick() {
    if (fileInputRef.current) fileInputRef.current.click();
  }

  function requestImageSend(file) {
    if (!file || sending || pendingMedia) return;
    const previewUrl = URL.createObjectURL(file);
    setPendingMedia({ type: 'image', file, previewUrl });
  }

  function handleFileSelected(e) {
    const file = e.target.files[0];
    if (file) requestImageSend(file);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) requestImageSend(file);
        return;
      }
    }
  }

  function cancelPendingMedia() {
    if (pendingMedia?.previewUrl) URL.revokeObjectURL(pendingMedia.previewUrl);
    setPendingMedia(null);
  }

  async function confirmPendingMedia() {
    if (!pendingMedia || sending) return;
    const media = pendingMedia;
    setPendingMedia(null);
    setSending(true);

    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const localUrl = media.previewUrl || URL.createObjectURL(media.blob || media.file);

    if (media.type === 'image') {
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'assistant', content: '', ts: Date.now() / 1000,
          media_type: 'image', media_path: localUrl, _localId: localId, _status: 'sending', _isLocalBlob: true,
        }],
      } : prev);
      try {
        const res = await sendImage(phone, media.file);
        updateMsgByLocalId(localId, () => ({ _status: res.ok ? null : 'failed' }));
      } catch (err) {
        console.error('Send image error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    } else {
      // audio
      setContactData(prev => prev ? {
        ...prev,
        messages: [...(prev.messages || []), {
          role: 'assistant', content: '[Áudio]', ts: Date.now() / 1000,
          media_type: 'audio', media_path: localUrl, _localId: localId, _status: 'sending', _isLocalBlob: true,
        }],
      } : prev);
      try {
        const res = await sendAudio(phone, media.blob, media.filename);
        updateMsgByLocalId(localId, () => ({ _status: res.ok ? null : 'failed' }));
      } catch (err) {
        console.error('Send audio error:', err);
        updateMsgByLocalId(localId, () => ({ _status: 'failed' }));
      }
    }
    setSending(false);
  }

  // Convert a WebM/Opus blob to WAV so GOWA accepts it (audio/wav is allowed)
  async function convertToWav(webmBlob) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    try {
      const arrayBuf = await webmBlob.arrayBuffer();
      const audioBuf = await ctx.decodeAudioData(arrayBuf);
      const numCh = audioBuf.numberOfChannels;
      const rate = audioBuf.sampleRate;
      const length = audioBuf.length;
      const bytesPerSample = 2; // 16-bit
      const blockAlign = numCh * bytesPerSample;
      const dataLen = length * blockAlign;
      const buf = new ArrayBuffer(44 + dataLen);
      const v = new DataView(buf);
      // RIFF header
      const w = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
      w(0, 'RIFF'); v.setUint32(4, 36 + dataLen, true);
      w(8, 'WAVE'); w(12, 'fmt ');
      v.setUint32(16, 16, true); v.setUint16(20, 1, true); // PCM
      v.setUint16(22, numCh, true); v.setUint32(24, rate, true);
      v.setUint32(28, rate * blockAlign, true); v.setUint16(32, blockAlign, true);
      v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, dataLen, true);
      // Interleaved PCM samples
      const channels = [];
      for (let c = 0; c < numCh; c++) channels.push(audioBuf.getChannelData(c));
      let off = 44;
      for (let i = 0; i < length; i++) {
        for (let c = 0; c < numCh; c++) {
          const s = Math.max(-1, Math.min(1, channels[c][i]));
          v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
          off += 2;
        }
      }
      return new Blob([buf], { type: 'audio/wav' });
    } finally {
      ctx.close();
    }
  }

  async function handleMicClick() {
    if (recording) {
      // Stop recording
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      return;
    }

    // Start recording — prefer OGG/Opus (accepted by GOWA), fallback to WebM then convert to WAV
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      const useOgg = MediaRecorder.isTypeSupported('audio/ogg;codecs=opus');
      const mimeType = useOgg ? 'audio/ogg;codecs=opus' : 'audio/webm;codecs=opus';
      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        setRecording(false);
        clearInterval(recordTimerRef.current);
        setRecordDuration(0);

        if (chunks.length === 0) return;

        // Prepare audio blob in a format GOWA accepts
        let audioBlob, audioFilename;
        if (useOgg) {
          audioBlob = new Blob(chunks, { type: 'audio/ogg' });
          audioFilename = 'voice.ogg';
        } else {
          const webmBlob = new Blob(chunks, { type: 'audio/webm' });
          audioBlob = await convertToWav(webmBlob);
          audioFilename = 'voice.wav';
        }

        const previewUrl = URL.createObjectURL(audioBlob);
        setPendingMedia({ type: 'audio', blob: audioBlob, filename: audioFilename, previewUrl });
      };

      recorder.start();
      setRecording(true);
      setRecordDuration(0);
      recordTimerRef.current = setInterval(() => setRecordDuration(d => d + 1), 1000);
    } catch (err) {
      console.error('Microphone access error:', err);
    }
  }

  function formatRecordTime(secs) {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  // Empty state — no contact selected
  if (!phone) {
    return html`
      <div class="wa-empty-bg flex flex-col items-center justify-center h-full">
        <div class="mb-8">
          <svg width="250" viewBox="0 0 303 172" class="opacity-20">
            <path fill="#8696a0" d="M229.565 160.229c32.874-12.676 53.009-32.508 53.009-54.669 0-39.356-56.792-71.26-126.87-71.26C85.627 34.3 28.835 66.204 28.835 105.56c0 20.655 17.776 39.174 45.883 51.974a8.372 8.372 0 014.773 5.573l.988 4.89a4.186 4.186 0 006.107 3.312l6.212-3.106a8.372 8.372 0 016.456-.37c12.157 3.96 25.676 6.13 39.95 6.13 7.096 0 14.038-.519 20.772-1.517a8.372 8.372 0 016.164 1.136l7.155 4.479a4.186 4.186 0 006.355-3.438l.247-5.287a8.372 8.372 0 013.636-6.223 8.372 8.372 0 017.258-1.314l17.4 4.64a4.186 4.186 0 005.096-2.013l3.47-6.587a8.372 8.372 0 017.09-4.41z"/>
          </svg>
        </div>
        <h2 class="text-wa-text text-[32px] font-light mb-2">WhatsBot</h2>
        <p class="text-wa-secondary text-[14px] text-center max-w-[450px] leading-[20px]">
          Envie e receba mensagens. Selecione um contato para começar.
        </p>
        <div class="mt-10 flex items-center gap-2 text-wa-secondary text-[12px]">
          <svg viewBox="0 0 10 12" width="10" height="12"><path fill="#8696a0" d="M5.063 0C2.272 0 .006 2.274.006 5.078v1.715L0 6.792v.7l.006.007v.206C.006 9.708 2.272 12 5.063 12h.037C7.89 12 10.1 9.708 10.1 6.905v-.2l.007-.008v-.7l-.007-.001V5.078C10.1 2.274 7.89 0 5.1 0h-.037zm0 1.2h.037c2.146 0 3.837 1.71 3.837 3.878v1.138l-.87.862v.827c0 2.168-1.69 3.895-3.837 3.895h-.037c-2.147 0-3.857-1.727-3.857-3.895v-.827l-.87-.862V5.078c0-2.168 1.71-3.878 3.857-3.878z"/></svg>
          Criptografia de ponta a ponta
        </div>
      </div>
    `;
  }

  const displayName = (info && info.name) || phone;
  const hasText = input.trim().length > 0;

  return html`
    <div class="flex flex-col h-full">
      <!-- Header -->
      <div class="h-[59px] flex items-center px-4 bg-wa-panel border-b border-wa-border shrink-0">
        <button onClick=${onBack} class="lg:hidden text-wa-icon hover:text-wa-text mr-2 shrink-0">
          <${BackArrowIcon} />
        </button>
        <div onClick=${onAvatarClick} class="w-[40px] h-[40px] rounded-full overflow-hidden shrink-0 mr-[13px] cursor-pointer">
          <${DefaultAvatar} size=${40} />
        </div>
        <div class="flex-1 min-w-0 cursor-pointer" onClick=${onAvatarClick}>
          <div class="text-wa-text text-[16px] leading-tight truncate">${displayName}</div>
          ${contactTyping
            ? html`<div class="text-wa-teal text-[13px] leading-tight">${contactTyping === 'audio' ? 'gravando áudio...' : 'digitando...'}</div>`
            : info && info.name ? html`<div class="text-wa-secondary text-[13px] leading-tight">${phone}</div>` : null
          }
        </div>
      </div>

      <!-- Chat area with doodle pattern -->
      <div ref=${chatRef} class="flex-1 overflow-y-auto wa-scrollbar wa-chat-pattern py-2 px-[4%] lg:px-[7%]">
        ${!messages || messages.length === 0
          ? html`<div class="text-center text-wa-secondary py-8 text-[14px]">
              <span class="bg-white/80 rounded-lg px-3 py-1.5 text-[12.5px] shadow-sm">Nenhuma mensagem ainda</span>
            </div>`
          : messages.map((m, i) => {
              const isUser = m.role === 'user';
              const isTranscription = m.role === 'transcription';
              const isToolCall = m.role === 'tool_call';
              const isError = m.role === 'error';
              const isFirst = i === 0 || messages[i - 1].role !== m.role;

              if (isTranscription) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #2d1b4e; color: #d4bfff; border: 1px solid #4a2d7a;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
                        Transcrição privada
                      </span>
                      <span>${m.content}</span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isToolCall) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #2d1b0e; color: #fbbf24; border: 1px solid #78350f;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/></svg>
                        Ferramenta IA
                      </span>
                      <span>${m.content}</span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              if (isError) {
                return html`
                  <div key=${i} class="flex justify-center mt-[4px]">
                    <div class="max-w-[85%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
                         style="background: #fef2f2; color: #dc2626; border: 1px solid #fecaca;">
                      <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
                        Erro no envio
                      </span>
                      <span>${m.content}</span>
                      <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
                        ${formatBubbleTime(m.ts)}
                      </span>
                    </div>
                  </div>
                `;
              }

              const isFailed = m._status === 'failed' || m.status === 'failed';
              const isSending = m._status === 'sending';

              return html`
                <div key=${m._localId || i} class="flex ${isUser ? 'justify-start' : 'justify-end'} ${isFirst ? 'mt-[12px]' : 'mt-[2px]'}">
                  <div class="wa-bubble max-w-[65%] rounded-[7.5px] px-[9px] pt-[6px] pb-[8px] text-[14.2px] leading-[19px] whitespace-pre-wrap relative ${
                    isUser
                      ? `bg-wa-incoming text-wa-text ${isFirst ? 'msg-tail-in rounded-tl-none' : ''}`
                      : `${isFailed ? 'text-wa-text' : 'bg-wa-outgoing text-wa-text'} ${isFirst ? 'msg-tail-out rounded-tr-none' : ''}`
                  }" style="${isFailed ? 'background: #fce8e8;' : ''}">
                    ${m.media_type === 'image' ? html`
                      <img
                        src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}"
                        alt="Imagem"
                        class="rounded-[4px] max-w-full max-h-[300px] mb-1 cursor-pointer"
                        style="min-width:120px"
                        onClick=${() => window.open(m._isLocalBlob ? m.media_path : '/' + m.media_path, '_blank')}
                        loading="lazy"
                      />
                      ${m.content && m.content !== '[Imagem enviada pelo contato]'
                        ? html`<span>${m.content}</span>`
                        : null}
                    ` : m.media_type === 'audio' ? html`
                      <audio controls preload="none" class="max-w-full mb-1" style="min-width:240px">
                        <source src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}" type="audio/wav" />
                        <source src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}" type="audio/ogg" />
                        <source src="${m._isLocalBlob ? m.media_path : '/' + m.media_path}" type="audio/mpeg" />
                      </audio>
                      ${m.content && m.content !== '[Áudio recebido]' && m.content !== '[Áudio]'
                        ? html`<span class="block text-[12px] text-wa-secondary italic">${m.content}</span>`
                        : null}
                    ` : html`<span>${m.content}</span>`}
                    <span class="float-right ml-[8px] mt-[4px] text-[11px] leading-[15px] whitespace-nowrap text-wa-secondary">
                      ${!isUser ? (
                        isFailed ? html`<${FailedIcon} />${!m.media_type && m._localId ? html`<${RetryIcon} onClick=${() => handleRetry(m._localId, m.content)} />` : ''}` :
                        isSending ? html`<${ClockIcon} />` :
                        html`<${DoubleCheckIcon} />`
                      ) : ''}${formatBubbleTime(m.ts)}
                    </span>
                  </div>
                </div>
              `;
            })
        }
      </div>

      <!-- Hidden file input for image upload -->
      <input
        ref=${fileInputRef}
        type="file"
        accept="image/*"
        class="hidden"
        onChange=${handleFileSelected}
      />

      <!-- Media confirmation overlay -->
      ${pendingMedia ? html`
        <div class="flex flex-col items-center bg-wa-panel border-t border-wa-border px-[16px] py-[12px] shrink-0 gap-[10px]">
          ${pendingMedia.type === 'image' ? html`
            <img src=${pendingMedia.previewUrl} class="max-h-[200px] max-w-full rounded-[8px] object-contain" />
          ` : html`
            <audio controls preload="auto" class="w-full max-w-[320px]">
              <source src=${pendingMedia.previewUrl} type="audio/wav" />
              <source src=${pendingMedia.previewUrl} type="audio/ogg" />
            </audio>
          `}
          <div class="flex gap-[12px]">
            <button
              type="button"
              onClick=${cancelPendingMedia}
              class="px-[16px] py-[6px] rounded-[8px] text-[13px] bg-wa-hover text-wa-text border border-wa-border hover:bg-wa-inputBg transition-colors"
            >Cancelar</button>
            <button
              type="button"
              onClick=${confirmPendingMedia}
              disabled=${sending}
              class="px-[16px] py-[6px] rounded-[8px] text-[13px] bg-wa-outgoing text-wa-text border border-wa-border hover:opacity-90 transition-colors disabled:opacity-50 flex items-center gap-[6px]"
            ><${SendIcon} /> Enviar</button>
          </div>
        </div>
      ` : ''}

      <!-- Input area -->
      ${pendingMedia ? '' : recording ? html`
        <div class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
          <div class="flex-1 flex items-center gap-3 mx-[5px]">
            <span class="w-[10px] h-[10px] rounded-full bg-red-500 animate-pulse shrink-0"></span>
            <span class="text-red-500 text-[15px] font-medium">${formatRecordTime(recordDuration)}</span>
            <span class="text-wa-secondary text-[14px]">Gravando...</span>
          </div>
          <button
            type="button"
            onClick=${handleMicClick}
            class="p-[8px] shrink-0"
          >
            <${StopIcon} />
          </button>
        </div>
      ` : html`
        <form onSubmit=${handleSend} class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
          <button type="button" class="p-[8px] shrink-0" tabindex="-1">
            <${EmojiIcon} />
          </button>
          <button type="button" class="p-[8px] shrink-0" tabindex="-1" onClick=${handleAttachClick}>
            <${AttachIcon} />
          </button>
          <div class="flex-1 mx-[5px]">
            <input
              type="text"
              value=${input}
              onInput=${handleInputChange}
              onPaste=${handlePaste}
              placeholder="Digite uma mensagem"
              disabled=${sending}
              class="w-full bg-wa-inputBg text-wa-text text-[15px] rounded-[8px] px-[12px] py-[9px] border border-wa-border outline-none placeholder-wa-secondary disabled:opacity-50"
            />
          </div>
          ${hasText ? html`
            <button
              type="submit"
              disabled=${sending}
              class="p-[8px] shrink-0 text-wa-iconActive transition-colors disabled:opacity-50"
            >
              <${SendIcon} />
            </button>
          ` : html`
            <button type="button" class="p-[8px] shrink-0 text-wa-icon" tabindex="-1" onClick=${handleMicClick}>
              <${MicIcon} />
            </button>
          `}
        </form>
      `}
    </div>
  `;
}

// ── Main Component ───────────────────────────────────────────────

export function Contacts({ newMessage, chatPresence, contactInfoUpdated, initialContactId }) {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [contactData, setContactData] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const hasLoadedDetail = useRef(false);
  const [showInfoPanel, setShowInfoPanel] = useState(false);
  const openInfoAfterSelect = useRef(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [ctxMenu, setCtxMenu] = useState(null);
  const [typingState, setTypingState] = useState({});  // { phone: 'text'|'audio'|null }
  const pendingWsMessages = useRef({});
  const selectedRef = useRef(null);
  const typingTimers = useRef({});
  const contactsRef = useRef([]);
  const lastResolvedId = useRef(null);

  // Keep refs in sync — avoids stale closures
  useEffect(() => { selectedRef.current = selected; }, [selected]);
  useEffect(() => { contactsRef.current = contacts; }, [contacts]);

  const handleToggleAI = useCallback(async (phone, enabled) => {
    const res = await toggleContactAI(phone, enabled);
    if (res.ok) {
      setContacts(prev => prev.map(c =>
        c.phone === phone ? { ...c, ai_enabled: res.data.ai_enabled } : c
      ));
      if (contactData && contactData.phone === phone) {
        setContactData(prev => prev ? { ...prev, ai_enabled: res.data.ai_enabled } : prev);
      }
    }
  }, [contactData]);

  // Push URL when selecting/deselecting a contact
  const selectContact = useCallback((phone) => {
    setSelected(phone);
    if (phone) {
      const c = contactsRef.current.find(c => c.phone === phone);
      if (c && c.id != null) {
        history.pushState(null, '', `/contacts/${c.id}`);
      }
    } else {
      history.pushState(null, '', '/');
    }
  }, []);

  const fetchContacts = useCallback((q = '') => {
    setLoading(true);
    getContacts(q).then(res => {
      if (res.ok) {
        setContacts(res.data);
        contactsRef.current = res.data;
      }
      setLoading(false);
    });
  }, []);

  // Initial load
  useEffect(() => { fetchContacts(); }, []);

  // Resolve initialContactId → phone when contacts are loaded
  useEffect(() => {
    if (initialContactId == null) {
      // popstate back to "/" — deselect without pushing URL again
      if (lastResolvedId.current != null) {
        setSelected(null);
        lastResolvedId.current = null;
      }
      return;
    }
    // Already resolved this exact ID — skip (prevents re-selecting on contacts list refresh)
    if (initialContactId === lastResolvedId.current) return;
    if (contacts.length === 0 || loading) return;
    const c = contacts.find(c => c.id === initialContactId);
    if (c) {
      setSelected(c.phone);
      lastResolvedId.current = initialContactId;
    }
  }, [initialContactId, contacts, loading]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => fetchContacts(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Load contact detail when selected changes
  useEffect(() => {
    if (!selected) { setContactData(null); return; }
    if (openInfoAfterSelect.current) {
      openInfoAfterSelect.current = false;
      setShowInfoPanel(true);
    } else {
      setShowInfoPanel(false);
    }
    if (!hasLoadedDetail.current) setLoadingDetail(true);
    // Preserve any messages already buffered for this contact (arrived before selection)
    // but reset the accumulator for new messages arriving during fetch
    const preFetchBuffer = pendingWsMessages.current[selected] || [];
    pendingWsMessages.current[selected] = [];
    // Clear unread badge immediately in local state
    setContacts(prev => prev.map(c =>
      c.phone === selected ? { ...c, unread_count: 0 } : c
    ));
    getContact(selected).then(res => {
      if (res.ok) {
        const data = res.data;
        // Merge buffered messages: pre-fetch (arrived before click) + during-fetch (arrived during loading)
        const duringFetch = pendingWsMessages.current[selected] || [];
        const pending = [...preFetchBuffer, ...duringFetch];
        if (pending.length > 0) {
          const existing = data.messages || [];
          const newMsgs = pending.filter(m =>
            !existing.some(e =>
              (e.ts === m.ts && e.role === m.role) ||
              (e.role === m.role && e.content === m.content && Math.abs(e.ts - m.ts) < 30)
            )
          );
          if (newMsgs.length > 0) {
            data.messages = [...(data.messages || []), ...newMsgs];
          }
        }
        // Hydrate failed messages with _localId so retry button works after reload
        data.messages = (data.messages || []).map(m => {
          if (m.status === 'failed') {
            return { ...m, _localId: `loaded_${m.ts}`, _status: 'failed' };
          }
          return m;
        });
        pendingWsMessages.current[selected] = [];
        setContactData(data);
      }
      hasLoadedDetail.current = true;
      setLoadingDetail(false);
    });
  }, [selected]);

  // Handle chat presence events (typing/recording indicators)
  useEffect(() => {
    if (!chatPresence) return;
    const { phone, state, media } = chatPresence;
    if (!phone) return;

    if (state === 'composing') {
      setTypingState(prev => ({ ...prev, [phone]: media === 'audio' ? 'audio' : 'text' }));
      // Auto-clear after 5s if no "paused" arrives
      clearTimeout(typingTimers.current[phone]);
      typingTimers.current[phone] = setTimeout(() => {
        setTypingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
      }, 5000);
    } else {
      // paused or unknown → clear
      clearTimeout(typingTimers.current[phone]);
      setTypingState(prev => { const n = { ...prev }; delete n[phone]; return n; });
    }
  }, [chatPresence]);

  // Handle real-time contact info updates (e.g. from save_contact_info tool)
  useEffect(() => {
    if (!contactInfoUpdated) return;
    const { phone, info: updatedInfo } = contactInfoUpdated;
    console.log('[WS] contact_info_updated', phone, updatedInfo);
    if (!phone || !updatedInfo) return;

    // Update sidebar name
    setContacts(prev => prev.map(c =>
      c.phone === phone ? { ...c, name: updatedInfo.name || c.name } : c
    ));

    // Update detail view if this contact is selected
    if (phone === selectedRef.current) {
      setContactData(prev => prev ? { ...prev, info: { ...updatedInfo } } : prev);
    }
  }, [contactInfoUpdated]);

  // Handle real-time messages from WebSocket
  useEffect(() => {
    if (!newMessage) return;
    const { phone, message } = newMessage;

    // Update detail view if this contact is selected
    // Use selectedRef to avoid stale closure
    if (phone === selectedRef.current) {
      // Use functional updater — prev is always the latest contactData
      setContactData(prev => {
        if (!prev) {
          // Contact data still loading — buffer in per-phone map
          const buf = pendingWsMessages.current[phone] || [];
          if (!buf.some(m =>
            (m.ts === message.ts && m.role === message.role) ||
            (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
          )) {
            pendingWsMessages.current[phone] = [...buf, message];
          }
          return prev;
        }
        // Deduplicate by ts + role, or by content + role (within 30s window)
        if (prev.messages && prev.messages.some(m =>
          (m.ts === message.ts && m.role === message.role) ||
          (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
        )) {
          return prev;
        }
        return {
          ...prev,
          messages: [...(prev.messages || []), message],
          updated_at: message.ts,
        };
      });
      if (message.role === 'user') markAsRead(phone);
    } else {
      // Contact NOT selected — buffer for when it's opened
      const buf = pendingWsMessages.current[phone] || [];
      if (!buf.some(m =>
        (m.ts === message.ts && m.role === message.role) ||
        (m.role === message.role && m.content === message.content && Math.abs(m.ts - message.ts) < 30)
      )) {
        pendingWsMessages.current[phone] = [...buf, message];
      }
    }

    // Skip contact list preview update for transcription, tool_call, and error messages
    if (message.role === 'transcription' || message.role === 'tool_call' || message.role === 'error') return;

    setContacts(prev => {
      const idx = prev.findIndex(c => c.phone === phone);
      if (idx >= 0) {
        const updated = [...prev];
        const isUserMsg = message.role === 'user';
        const isViewing = phone === selectedRef.current;
        let lastPreview = (message.content || '').substring(0, 80);
        if (message.media_type === 'image') lastPreview = message.content || '📷 Imagem';
        if (message.media_type === 'audio') lastPreview = '🎤 Áudio';
        updated[idx] = {
          ...updated[idx],
          last_message: lastPreview,
          last_message_role: message.role,
          last_message_ts: message.ts,
          msg_count: updated[idx].msg_count + 1,
          unread_count: isUserMsg && !isViewing
            ? (updated[idx].unread_count || 0) + 1
            : updated[idx].unread_count || 0,
          updated_at: message.ts,
        };
        updated.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
        return updated;
      }
      fetchContacts(search);
      return prev;
    });
  }, [newMessage]);

  const messages = contactData ? contactData.messages || [] : [];
  const info = contactData ? contactData.info || {} : {};

  return html`
    <div class="flex flex-col lg:flex-row h-full">
      <!-- Sidebar -->
      <div class="shrink-0 border-r border-wa-border transition-all duration-300 overflow-hidden ${sidebarHidden ? 'lg:w-0 lg:border-r-0' : 'lg:w-[400px]'} ${selected ? 'hidden lg:flex lg:flex-col' : 'flex flex-col w-full'}">
        <${ContactList}
          contacts=${contacts}
          loading=${loading}
          search=${search}
          onSearchChange=${setSearch}
          selected=${selected}
          onSelect=${selectContact}
          onContextMenu=${setCtxMenu}
          typingState=${typingState}
        />
      </div>
      <!-- Toggle sidebar button (desktop only) -->
      <button
        class="hidden lg:flex items-center justify-center w-[14px] shrink-0 bg-wa-panel hover:bg-wa-hover border-r border-wa-border cursor-pointer transition-colors"
        onClick=${() => setSidebarHidden(h => !h)}
        title=${sidebarHidden ? 'Mostrar contatos' : 'Esconder contatos'}
      >
        <span class="text-wa-secondary text-[11px] select-none">${sidebarHidden ? '›' : '‹'}</span>
      </button>
      <!-- Chat panel -->
      <div class="flex-1 min-w-0 ${!selected ? 'hidden lg:flex' : 'flex'} relative">
        <div class="w-full flex flex-col">
          ${loadingDetail
            ? html`<div class="flex items-center justify-center h-full bg-wa-panel text-wa-secondary animate-pulse-slow text-[14px]">Carregando...</div>`
            : html`<${ContactDetail}
                phone=${selected}
                onBack=${() => selectContact(null)}
                messages=${messages}
                setContactData=${setContactData}
                info=${info}
                onAvatarClick=${() => selected && setShowInfoPanel(true)}
                contactTyping=${selected && typingState[selected] || null}
              />`
          }
          ${showInfoPanel && selected ? html`
            <${ContactInfoPanel}
              phone=${selected}
              info=${info}
              onClose=${() => setShowInfoPanel(false)}
              onSave=${(updatedInfo) => {
                setContactData(prev => prev ? { ...prev, info: updatedInfo } : prev);
                setContacts(prev => prev.map(c =>
                  c.phone === selected ? { ...c, name: updatedInfo.name || c.name } : c
                ));
                setShowInfoPanel(false);
              }}
            />
          ` : null}
        </div>
      </div>
      ${ctxMenu ? html`
        <${ContextMenu}
          x=${ctxMenu.x}
          y=${ctxMenu.y}
          phone=${ctxMenu.phone}
          aiEnabled=${ctxMenu.aiEnabled}
          onToggleAI=${handleToggleAI}
          onEditContact=${(phone) => { openInfoAfterSelect.current = true; selectContact(phone); }}
          onClose=${() => setCtxMenu(null)}
        />
      ` : null}
    </div>
  `;
}
