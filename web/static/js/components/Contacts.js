import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getContacts, getContact } from '../services/api.js';

const html = htm.bind(h);

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

function formatFullTime(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
  });
}

// ── Contact List ──────────────────────────────────────────────────

function ContactList({ contacts, loading, search, onSearchChange, selected, onSelect }) {
  return html`
    <div class="flex flex-col h-full">
      <div class="p-3 border-b border-gray-700">
        <input
          type="text"
          placeholder="Buscar contato..."
          value=${search}
          onInput=${(e) => onSearchChange(e.target.value)}
          class="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded text-sm text-gray-200 placeholder-gray-500 focus:border-whatsapp focus:outline-none"
        />
      </div>
      <div class="flex-1 overflow-y-auto">
        ${loading && contacts.length === 0
          ? html`<div class="text-center text-gray-500 py-8 animate-pulse-slow">Carregando...</div>`
          : contacts.length === 0
            ? html`<div class="text-center text-gray-500 py-8">Nenhum contato encontrado</div>`
            : contacts.map(c => html`
                <div
                  key=${c.phone}
                  onClick=${() => onSelect(c.phone)}
                  class="flex items-center gap-3 px-4 py-3 cursor-pointer border-b border-gray-800 transition-colors ${
                    selected === c.phone
                      ? 'bg-gray-700 border-l-2 border-l-whatsapp'
                      : 'hover:bg-gray-800/50 border-l-2 border-l-transparent'
                  }"
                >
                  <div class="w-10 h-10 rounded-full bg-gray-600 flex items-center justify-center text-gray-300 text-sm font-bold shrink-0">
                    ${(c.name || c.phone).charAt(0).toUpperCase()}
                  </div>
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center justify-between gap-2">
                      <span class="font-medium text-sm text-gray-200 truncate">${c.name || c.phone}</span>
                      <span class="text-xs text-gray-500 shrink-0">${formatTime(c.last_message_ts)}</span>
                    </div>
                    <div class="flex items-center justify-between gap-2 mt-0.5">
                      <span class="text-xs text-gray-400 truncate">${c.last_message || 'Sem mensagens'}</span>
                      <span class="bg-gray-600 text-gray-300 text-xs px-1.5 py-0.5 rounded-full shrink-0">${c.msg_count}</span>
                    </div>
                  </div>
                </div>
              `)
        }
      </div>
    </div>
  `;
}

// ── Contact Detail ────────────────────────────────────────────────

function ContactDetail({ phone, onBack }) {
  const [contact, setContact] = useState(null);
  const [loading, setLoading] = useState(false);
  const chatRef = useRef(null);

  useEffect(() => {
    if (!phone) return;
    setLoading(true);
    getContact(phone).then(res => {
      if (res.ok) setContact(res.data);
      setLoading(false);
    });
  }, [phone]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [contact]);

  if (!phone) {
    return html`
      <div class="flex items-center justify-center h-full text-gray-500">
        <div class="text-center">
          <div class="text-4xl mb-2">💬</div>
          <div>Selecione um contato para ver a conversa</div>
        </div>
      </div>
    `;
  }

  if (loading) {
    return html`<div class="flex items-center justify-center h-full text-gray-500 animate-pulse-slow">Carregando...</div>`;
  }

  if (!contact) return null;

  const info = contact.info || {};
  const msgs = contact.messages || [];
  const hasInfo = info.name || info.email || info.profession || info.company || (info.observations && info.observations.length > 0);

  return html`
    <div class="flex flex-col h-full">
      <!-- Header -->
      <div class="p-4 border-b border-gray-700 bg-gray-800/50">
        <div class="flex items-center gap-3">
          <button onClick=${onBack} class="lg:hidden text-gray-400 hover:text-gray-200 mr-1">← </button>
          <div class="w-10 h-10 rounded-full bg-gray-600 flex items-center justify-center text-gray-300 text-sm font-bold shrink-0">
            ${(info.name || phone).charAt(0).toUpperCase()}
          </div>
          <div class="flex-1 min-w-0">
            <div class="font-medium text-gray-200">${info.name || phone}</div>
            ${info.name ? html`<div class="text-xs text-gray-400">${phone}</div>` : null}
          </div>
        </div>
        ${hasInfo ? html`
          <div class="mt-3 flex flex-wrap gap-2 text-xs">
            ${info.email ? html`<span class="bg-gray-700 px-2 py-1 rounded text-gray-300">✉ ${info.email}</span>` : null}
            ${info.profession ? html`<span class="bg-gray-700 px-2 py-1 rounded text-gray-300">💼 ${info.profession}</span>` : null}
            ${info.company ? html`<span class="bg-gray-700 px-2 py-1 rounded text-gray-300">🏢 ${info.company}</span>` : null}
            ${info.observations && info.observations.length > 0
              ? info.observations.map(obs => html`<span class="bg-gray-700 px-2 py-1 rounded text-gray-300">📌 ${obs}</span>`)
              : null}
          </div>
        ` : null}
      </div>

      <!-- Messages -->
      <div ref=${chatRef} class="flex-1 overflow-y-auto p-4 space-y-3 bg-gray-950">
        ${msgs.length === 0
          ? html`<div class="text-center text-gray-500 py-8">Nenhuma mensagem</div>`
          : msgs.map((m, i) => html`
              <div key=${i} class="flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}">
                <div class="max-w-[80%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap ${
                  m.role === 'user'
                    ? 'bg-whatsapp/20 text-green-100 rounded-br-none'
                    : 'bg-gray-800 text-gray-200 rounded-bl-none'
                }">
                  ${m.content}
                  <div class="text-xs mt-1 ${m.role === 'user' ? 'text-green-300/50' : 'text-gray-500'}">${formatFullTime(m.ts)}</div>
                </div>
              </div>
            `)
        }
      </div>
    </div>
  `;
}

// ── Main Component ────────────────────────────────────────────────

export function Contacts() {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);

  const fetchContacts = useCallback((q = '') => {
    setLoading(true);
    getContacts(q).then(res => {
      if (res.ok) setContacts(res.data);
      setLoading(false);
    });
  }, []);

  // Initial load
  useEffect(() => { fetchContacts(); }, []);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => fetchContacts(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  return html`
    <div class="flex flex-col lg:flex-row h-full">
      <!-- List -->
      <div class="w-full lg:w-80 shrink-0 border-b lg:border-b-0 lg:border-r border-gray-700 bg-gray-900 ${selected ? 'hidden lg:flex lg:flex-col' : 'flex flex-col'}">
        <${ContactList}
          contacts=${contacts}
          loading=${loading}
          search=${search}
          onSearchChange=${setSearch}
          selected=${selected}
          onSelect=${setSelected}
        />
      </div>
      <!-- Detail -->
      <div class="flex-1 min-w-0 ${!selected ? 'hidden lg:flex' : 'flex'}">
        <div class="w-full flex flex-col">
          <${ContactDetail}
            phone=${selected}
            onBack=${() => setSelected(null)}
          />
        </div>
      </div>
    </div>
  `;
}
