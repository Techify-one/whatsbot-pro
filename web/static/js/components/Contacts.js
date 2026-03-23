import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getContacts, getContact, sendMessage, markAsRead } from '../services/api.js';

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

// ── Contact List (WhatsApp Web sidebar) ──────────────────────────

function ContactList({ contacts, loading, search, onSearchChange, selected, onSelect }) {
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
                      <span class="text-wa-text text-[17px] truncate leading-[21px]">${c.name || c.phone}</span>
                      <span class="text-wa-secondary text-[12px] ml-[6px] shrink-0 leading-[14px]">${formatTime(c.last_message_ts)}</span>
                    </div>
                    <div class="flex justify-between items-center mt-[3px]">
                      <span class="text-wa-secondary text-[14px] truncate leading-[20px]">
                        ${c.last_message_role === 'assistant' ? html`<${DoubleCheckIcon} />` : ''}${c.last_message ? c.last_message.substring(0, 80) : ''}
                      </span>
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

// ── Contact Detail (WhatsApp Web chat panel) ─────────────────────

function ContactDetail({ phone, onBack, messages, info }) {
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const chatRef = useRef(null);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages]);

  useEffect(() => { setInput(''); }, [phone]);

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    setSending(true);
    setInput('');
    try {
      const res = await sendMessage(phone, text);
      if (!res.ok) console.error('Send failed:', res.error);
    } catch (err) {
      console.error('Send error:', err);
    }
    setSending(false);
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
        <div class="w-[40px] h-[40px] rounded-full overflow-hidden shrink-0 mr-[13px] cursor-pointer">
          <${DefaultAvatar} size=${40} />
        </div>
        <div class="flex-1 min-w-0">
          <div class="text-wa-text text-[16px] leading-tight truncate">${displayName}</div>
          ${info && info.name ? html`<div class="text-wa-secondary text-[13px] leading-tight">${phone}</div>` : null}
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
              const isFirst = i === 0 || messages[i - 1].role !== m.role;
              return html`
                <div key=${i} class="flex ${isUser ? 'justify-start' : 'justify-end'} ${isFirst ? 'mt-[12px]' : 'mt-[2px]'}">
                  <div class="wa-bubble max-w-[65%] rounded-[7.5px] px-[9px] pt-[6px] pb-[8px] text-[14.2px] leading-[19px] whitespace-pre-wrap relative ${
                    isUser
                      ? `bg-wa-incoming text-wa-text ${isFirst ? 'msg-tail-in rounded-tl-none' : ''}`
                      : `bg-wa-outgoing text-wa-text ${isFirst ? 'msg-tail-out rounded-tr-none' : ''}`
                  }">
                    <span>${m.content}</span>
                    <span class="float-right ml-[8px] mt-[4px] text-[11px] leading-[15px] whitespace-nowrap text-wa-secondary">
                      ${!isUser ? html`<${DoubleCheckIcon} />` : ''}${formatBubbleTime(m.ts)}
                    </span>
                  </div>
                </div>
              `;
            })
        }
      </div>

      <!-- Input area -->
      <form onSubmit=${handleSend} class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
        <button type="button" class="p-[8px] shrink-0" tabindex="-1">
          <${EmojiIcon} />
        </button>
        <button type="button" class="p-[8px] shrink-0" tabindex="-1">
          <${AttachIcon} />
        </button>
        <div class="flex-1 mx-[5px]">
          <input
            type="text"
            value=${input}
            onInput=${(e) => setInput(e.target.value)}
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
          <button type="button" class="p-[8px] shrink-0 text-wa-icon" tabindex="-1">
            <${MicIcon} />
          </button>
        `}
      </form>
    </div>
  `;
}

// ── Main Component ───────────────────────────────────────────────

export function Contacts({ newMessage }) {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [contactData, setContactData] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

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

  // Load contact detail when selected changes
  useEffect(() => {
    if (!selected) { setContactData(null); return; }
    setLoadingDetail(true);
    // Clear unread badge immediately in local state
    setContacts(prev => prev.map(c =>
      c.phone === selected ? { ...c, unread_count: 0 } : c
    ));
    getContact(selected).then(res => {
      if (res.ok) setContactData(res.data);
      setLoadingDetail(false);
    });
  }, [selected]);

  // Handle real-time messages from WebSocket
  useEffect(() => {
    if (!newMessage) return;
    const { phone, message } = newMessage;

    // Update detail view if this contact is selected
    if (phone === selected && contactData) {
      setContactData(prev => ({
        ...prev,
        messages: [...(prev.messages || []), message],
        updated_at: message.ts,
      }));
      // Persist read state since user is viewing this contact
      if (message.role === 'user') markAsRead(phone);
    }

    // Update contact list preview
    setContacts(prev => {
      const idx = prev.findIndex(c => c.phone === phone);
      if (idx >= 0) {
        const updated = [...prev];
        const isUserMsg = message.role === 'user';
        const isViewing = phone === selected;
        updated[idx] = {
          ...updated[idx],
          last_message: message.content.substring(0, 80),
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
      <div class="w-full lg:w-[400px] shrink-0 border-r border-wa-border ${selected ? 'hidden lg:flex lg:flex-col' : 'flex flex-col'}">
        <${ContactList}
          contacts=${contacts}
          loading=${loading}
          search=${search}
          onSearchChange=${setSearch}
          selected=${selected}
          onSelect=${setSelected}
        />
      </div>
      <!-- Chat panel -->
      <div class="flex-1 min-w-0 ${!selected ? 'hidden lg:flex' : 'flex'}">
        <div class="w-full flex flex-col">
          ${loadingDetail
            ? html`<div class="flex items-center justify-center h-full bg-wa-panel text-wa-secondary animate-pulse-slow text-[14px]">Carregando...</div>`
            : html`<${ContactDetail}
                phone=${selected}
                onBack=${() => setSelected(null)}
                messages=${messages}
                info=${info}
              />`
          }
        </div>
      </div>
    </div>
  `;
}
