import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { sandboxSend, sandboxClear, getLogs, clearLogs } from '../services/api.js';

const html = htm.bind(h);

const LEVEL_COLORS = {
  DEBUG: 'text-gray-500',
  INFO: 'text-blue-400',
  WARNING: 'text-yellow-400',
  ERROR: 'text-red-400',
  CRITICAL: 'text-red-500 font-bold',
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
        <h3 class="text-sm font-semibold text-gray-300 uppercase tracking-wide">Logs</h3>
        <input
          type="text"
          placeholder="Filtrar logs..."
          value=${filter}
          onInput=${(e) => setFilter(e.target.value)}
          class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 focus:border-whatsapp focus:outline-none"
        />
        <label class="flex items-center gap-1 text-xs text-gray-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked=${autoScroll}
            onChange=${(e) => setAutoScroll(e.target.checked)}
            class="rounded border-gray-600"
          />
          Auto-scroll
        </label>
        <button
          onClick=${handleClear}
          class="text-xs text-gray-500 hover:text-red-400 transition-colors px-2 py-1"
        >Limpar</button>
      </div>
      <div
        ref=${logRef}
        class="flex-1 bg-gray-950 rounded border border-gray-800 overflow-y-auto font-mono text-xs p-2 min-h-0"
        style="max-height: 300px;"
      >
        ${filtered.length === 0
          ? html`<div class="text-gray-600 text-center py-8">Nenhum log ainda...</div>`
          : filtered.map((log, i) => html`
            <div key=${i} class="flex gap-2 py-0.5 hover:bg-gray-900 leading-tight">
              <span class="text-gray-600 shrink-0">${log.ts}</span>
              <span class="shrink-0 w-16 ${LEVEL_COLORS[log.level] || 'text-gray-400'}">${log.level}</span>
              <span class="text-gray-500 shrink-0">${log.name}</span>
              <span class="text-gray-300 break-all">${log.message}</span>
            </div>
          `)
        }
      </div>
    </div>
  `;
}

function ChatPanel() {
  const [phone, setPhone] = useState('5511999999999');
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState([]);
  const [sending, setSending] = useState(false);
  const chatRef = useRef(null);

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [messages]);

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    setMessages(prev => [...prev, { role: 'user', content: text, ts: new Date() }]);
    setInput('');
    setSending(true);

    try {
      const res = await sandboxSend(phone, text);
      if (res.ok) {
        setMessages(prev => [...prev, { role: 'assistant', content: res.data.reply, ts: new Date() }]);
      } else {
        setMessages(prev => [...prev, { role: 'error', content: res.error || 'Erro desconhecido', ts: new Date() }]);
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'error', content: `Erro de rede: ${err.message}`, ts: new Date() }]);
    } finally {
      setSending(false);
    }
  }

  async function handleClear() {
    await sandboxClear(phone);
    setMessages([]);
  }

  return html`
    <div class="flex flex-col h-full">
      <!-- Header -->
      <div class="flex items-center gap-2 mb-2">
        <h3 class="text-sm font-semibold text-gray-300 uppercase tracking-wide shrink-0">Chat Sandbox</h3>
        <div class="flex items-center gap-1 flex-1">
          <label class="text-xs text-gray-500 shrink-0">Telefone:</label>
          <input
            type="text"
            value=${phone}
            onInput=${(e) => setPhone(e.target.value)}
            placeholder="5511999999999"
            class="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 w-36 focus:border-whatsapp focus:outline-none"
          />
        </div>
        <button
          onClick=${handleClear}
          class="text-xs text-gray-500 hover:text-red-400 transition-colors px-2 py-1"
        >Limpar conversa</button>
      </div>

      <!-- Messages -->
      <div
        ref=${chatRef}
        class="flex-1 bg-gray-950 rounded border border-gray-800 overflow-y-auto p-3 space-y-2 min-h-0"
        style="max-height: 400px;"
      >
        ${messages.length === 0
          ? html`<div class="text-gray-600 text-center py-12 text-sm">
              Envie uma mensagem para testar o bot.<br/>
              <span class="text-xs text-gray-700">Usa o mesmo pipeline do WhatsApp (AgentHandler.process_message)</span>
            </div>`
          : messages.map((msg, i) => html`
            <div key=${i} class="flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}">
              <div class="max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-whatsapp/20 text-green-100 rounded-br-none'
                  : msg.role === 'error'
                    ? 'bg-red-900/30 text-red-300 rounded-bl-none'
                    : 'bg-gray-800 text-gray-200 rounded-bl-none'
              }">
                <div class="whitespace-pre-wrap break-words">${msg.content}</div>
                <div class="text-[10px] mt-1 ${msg.role === 'user' ? 'text-green-400/50' : 'text-gray-600'} text-right">
                  ${msg.ts.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </div>
              </div>
            </div>
          `)
        }
        ${sending ? html`
          <div class="flex justify-start">
            <div class="bg-gray-800 rounded-lg px-3 py-2 text-sm text-gray-400 rounded-bl-none animate-pulse-slow">
              Processando...
            </div>
          </div>
        ` : null}
      </div>

      <!-- Input -->
      <form onSubmit=${handleSend} class="flex gap-2 mt-2">
        <input
          type="text"
          value=${input}
          onInput=${(e) => setInput(e.target.value)}
          placeholder="Digite uma mensagem..."
          disabled=${sending}
          class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:border-whatsapp focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled=${sending || !input.trim()}
          class="bg-whatsapp hover:bg-whatsapp/80 disabled:bg-gray-700 disabled:text-gray-500 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >Enviar</button>
      </form>
    </div>
  `;
}

export function Sandbox() {
  return html`
    <div class="space-y-4">
      <!-- Chat -->
      <div class="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
        <${ChatPanel} />
      </div>

      <!-- Logs -->
      <div class="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
        <${LogPanel} />
      </div>
    </div>
  `;
}
