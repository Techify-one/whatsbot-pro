import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import {
  getContact, sandboxClear, getLogs, clearLogs,
  sandboxSend, sandboxSendImage, sandboxSendAudio, sandboxSendDocument,
} from '../services/api.js';
import { ContactDetail } from './contacts/ContactDetail.js';
import { isSameMessage } from './contacts/utils.js';

const html = htm.bind(h);

const LEVEL_COLORS = {
  DEBUG: 'text-wa-secondary',
  INFO: 'text-blue-600',
  WARNING: 'text-yellow-600',
  ERROR: 'text-red-500',
  CRITICAL: 'text-red-600 font-bold',
};

function LogPanel() {
  const [logs, setLogs] = useState([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState('');
  const logRef = useRef(null);
  const intervalRef = useRef(null);

  const fetchLogs = useCallback(async () => {
    const res = await getLogs(300);
    if (res.ok) setLogs(res.data);
  }, []);

  useEffect(() => {
    fetchLogs();
    intervalRef.current = setInterval(fetchLogs, 2000);
    return () => clearInterval(intervalRef.current);
  }, [fetchLogs]);

  useEffect(() => {
    if (autoScroll && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const filtered = filter
    ? logs.filter(l => l.message.toLowerCase().includes(filter.toLowerCase()) || l.level.includes(filter.toUpperCase()))
    : logs;

  async function handleClear() {
    await clearLogs();
    setLogs([]);
  }

  return html`
    <div class="flex flex-col h-full">
      <div class="flex items-center gap-2 mb-2">
        <h3 class="text-sm font-semibold text-wa-text uppercase tracking-wide">Logs</h3>
        <input
          type="text"
          placeholder="Filtrar logs..."
          value=${filter}
          onInput=${(e) => setFilter(e.target.value)}
          class="flex-1 bg-wa-panel border border-wa-border rounded px-2 py-1 text-xs text-wa-text focus:border-wa-teal focus:outline-none"
        />
        <label class="flex items-center gap-1 text-xs text-wa-secondary cursor-pointer select-none">
          <input
            type="checkbox"
            checked=${autoScroll}
            onChange=${(e) => setAutoScroll(e.target.checked)}
            class="rounded border-wa-border accent-wa-teal"
          />
          Auto-scroll
        </label>
        <button
          onClick=${handleClear}
          class="text-xs text-wa-secondary hover:text-red-500 transition-colors px-2 py-1"
        >Limpar</button>
      </div>
      <div
        ref=${logRef}
        class="flex-1 bg-wa-panel rounded border border-wa-border overflow-y-auto font-mono text-xs p-2 min-h-0"
        style="height: 60vh;"
      >
        ${filtered.length === 0
          ? html`<div class="text-wa-secondary text-center py-8">Nenhum log ainda...</div>`
          : filtered.map((log, i) => html`
            <div key=${i} class="flex gap-2 py-0.5 hover:bg-wa-hover leading-tight">
              <span class="text-wa-secondary shrink-0">${log.ts}</span>
              <span class="shrink-0 w-16 ${LEVEL_COLORS[log.level] || 'text-wa-secondary'}">${log.level}</span>
              <span class="text-wa-secondary shrink-0">${log.name}</span>
              <span class="text-wa-text break-all">${log.message}</span>
            </div>
          `)
        }
      </div>
    </div>
  `;
}

// ── Sandbox ────────────────────────────────────────────────────────
// A full WhatsApp-style chat for testing the bot. Reuses `ContactDetail`
// (sandbox mode): you play the customer, the AI replies — all local, nothing
// is sent over WhatsApp. The conversation is persisted to the same contact as
// the real chat, so it shows up there too.

export function Sandbox({ newMessage }) {
  const [phone, setPhone] = useState('5511999999999');
  const [activePhone, setActivePhone] = useState('5511999999999');
  const [contactData, setContactData] = useState({ messages: [], info: {} });
  const [botTyping, setBotTyping] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const activePhoneRef = useRef(activePhone);

  useEffect(() => { activePhoneRef.current = activePhone; }, [activePhone]);

  // Debounce the phone field — commit it as the active chat after a pause.
  useEffect(() => {
    const t = setTimeout(() => setActivePhone(phone.trim()), 500);
    return () => clearTimeout(t);
  }, [phone]);

  // Load conversation history for the active phone.
  useEffect(() => {
    if (!activePhone) { setContactData({ messages: [], info: {} }); return; }
    let cancelled = false;
    getContact(activePhone, false).then(res => {
      if (cancelled) return;
      setContactData(res.ok
        ? { ...res.data, messages: (res.data.messages || []).filter(m => m.role !== 'tool_call') }
        : { messages: [], info: {} });
    });
    return () => { cancelled = true; };
  }, [activePhone]);

  // Live updates: append messages broadcast for the active phone.
  useEffect(() => {
    if (!newMessage) return;
    const { phone: msgPhone, message } = newMessage;
    if (!message || msgPhone !== activePhoneRef.current) return;
    // Tool-execution cards are an operator-panel artifact — keep the sandbox
    // chat clean (they still appear in the official contact chat).
    if (message.role === 'tool_call') return;
    setContactData(prev => {
      const msgs = prev.messages || [];
      const idx = msgs.findIndex(m => isSameMessage(m, message));
      if (idx !== -1) {
        // Reconcile server-side fields onto the optimistic/loaded bubble.
        if (message.msg_id || message.status) {
          const updated = [...msgs];
          updated[idx] = {
            ...updated[idx],
            ...(message.msg_id ? { msg_id: message.msg_id } : {}),
            ...(message.status && !updated[idx]._status ? { status: message.status } : {}),
          };
          return { ...prev, messages: updated };
        }
        return prev;
      }
      return { ...prev, messages: [...msgs, message] };
    });
  }, [newMessage]);

  // Sandbox send API injected into ContactDetail. Wraps each call to show the
  // "digitando..." indicator while the AI is processing.
  async function withTyping(fn) {
    setBotTyping(true);
    try { return await fn(); }
    finally { setBotTyping(false); }
  }
  const sandboxApi = {
    sendText: (p, text) => withTyping(() => sandboxSend(p, text)),
    sendImage: (p, file, caption) => withTyping(() => sandboxSendImage(p, file, caption)),
    sendAudio: (p, blob, filename) => withTyping(() => sandboxSendAudio(p, blob, filename)),
    sendDocument: (p, file, caption) => withTyping(() => sandboxSendDocument(p, file, caption)),
  };

  async function handleClear() {
    await sandboxClear(activePhone);
    setContactData({ messages: [], info: {} });
  }

  return html`
    <div class="flex flex-col h-full">
      <!-- Sandbox toolbar -->
      <div class="flex items-center gap-[10px] px-[14px] py-[8px] bg-wa-panel border-b border-wa-border shrink-0">
        <span class="text-[13px] font-semibold text-wa-text uppercase tracking-wide shrink-0">Sandbox</span>
        <label class="text-[12px] text-wa-secondary shrink-0">Telefone:</label>
        <input
          type="text"
          value=${phone}
          onInput=${(e) => setPhone(e.target.value)}
          placeholder="5511999999999"
          class="bg-wa-inputBg border border-wa-border rounded px-[8px] py-[4px] text-[13px] text-wa-text w-[160px] focus:border-wa-teal focus:outline-none"
        />
        <div class="flex-1"></div>
        <button
          onClick=${() => setShowLogs(true)}
          class="text-[12px] text-wa-secondary hover:text-wa-text border border-wa-border rounded px-[10px] py-[4px] transition-colors"
        >Logs</button>
        <button
          onClick=${handleClear}
          class="text-[12px] text-wa-secondary hover:text-red-500 border border-wa-border rounded px-[10px] py-[4px] transition-colors"
        >Limpar conversa</button>
      </div>

      <!-- Chat (reuses the contact chat in sandbox mode) -->
      <div class="flex-1 min-h-0">
        <${ContactDetail}
          phone=${activePhone}
          sandbox=${true}
          api=${sandboxApi}
          messages=${contactData.messages || []}
          info=${contactData.info || {}}
          contact=${contactData}
          setContactData=${setContactData}
          contactTyping=${botTyping}
          globalTags=${{}}
          onBack=${() => {}}
          onAvatarClick=${() => {}}
        />
      </div>

      <!-- Logs overlay -->
      ${showLogs ? html`
        <div
          class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick=${() => setShowLogs(false)}
        >
          <div
            class="bg-wa-bg rounded-lg shadow-xl border border-wa-border w-[90vw] max-w-[900px] p-4"
            onClick=${(e) => e.stopPropagation()}
          >
            <${LogPanel} />
            <div class="flex justify-end mt-2">
              <button
                onClick=${() => setShowLogs(false)}
                class="text-[13px] text-wa-secondary hover:text-wa-text px-3 py-1"
              >Fechar</button>
            </div>
          </div>
        </div>
      ` : ''}
    </div>
  `;
}
