import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { checkPhone, listConnectedChannels, sendMessage } from '../../services/api.js';

const html = htm.bind(h);

// Rótulo/cor por provider — espelha o ChannelPickerModal (paleta dark-mode-safe).
const PROVIDER_META = {
  gowa:           { label: 'WhatsApp',  dot: 'bg-wa-teal' },
  whatsapp_cloud: { label: 'Cloud API', dot: 'bg-blue-500' },
  telegram:       { label: 'Telegram',  dot: 'bg-blue-500' },
  test:           { label: 'Teste',     dot: 'bg-wa-secondary' },
};

function normalizePhone(input) {
  const digits = (input || '').replace(/\D/g, '');
  if (digits.length < 10) return null;
  if (digits.startsWith('55')) return digits;
  return '55' + digits;
}

function looksLikePhone(input) {
  return (input || '').replace(/\D/g, '').length >= 10;
}

// 5564900000000 → +55 (64) 99232-7255 (espelha o helper da sidebar). Preserva o
// que o operador digitou (não normaliza o 9º dígito).
function formatPhoneDisplay(phone) {
  if (!phone || phone.length < 12) return phone || '';
  return `+${phone.slice(0, 2)} (${phone.slice(2, 4)}) ${phone.slice(4, 9)}-${phone.slice(9)}`;
}

function channelLabel(ch) {
  const meta = PROVIDER_META[ch.provider] || { label: ch.provider || 'Canal' };
  return ch.display_name || meta.label;
}

// Modal de "Nova conversa" (estilo Chatwoot): digita o número (mostra o nome do
// contato ao lado se já existir), escolhe a caixa de entrada conectada e escreve a
// 1ª mensagem. Ao enviar, a conversa nova já aparece na sidebar (onSent dispara o
// refresh + abre a thread). Roteia pelo `channel_id` escolhido (a conversa ainda
// não existe, então o backend a cria nesse canal).
export function NewConversationModal({ contacts = [], onClose, onSent }) {
  const [phoneInput, setPhoneInput] = useState('');
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState(null);  // {phone, registered, name} | null
  const [checkError, setCheckError] = useState(null);
  const [channels, setChannels] = useState([]);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelId, setChannelId] = useState('');
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState(null);
  const checkSeq = useRef(0);

  // Carrega as caixas de entrada conectadas (já filtradas pelo backend).
  useEffect(() => {
    let alive = true;
    listConnectedChannels().then((res) => {
      if (!alive) return;
      const list = (res && res.ok && Array.isArray(res.data)) ? res.data : [];
      setChannels(list);
      if (list.length > 0) setChannelId(list[0].id);
      setChannelsLoading(false);
    }).catch(() => { if (alive) setChannelsLoading(false); });
    return () => { alive = false; };
  }, []);

  // Verifica o número (debounce) sempre que o input muda e parece um telefone.
  useEffect(() => {
    setCheckResult(null);
    setCheckError(null);
    if (!looksLikePhone(phoneInput)) { setChecking(false); return; }
    const normalized = normalizePhone(phoneInput);
    if (!normalized) { setChecking(false); return; }
    const seq = ++checkSeq.current;
    setChecking(true);
    const t = setTimeout(async () => {
      try {
        const res = await checkPhone(normalized, false);  // só valida, não cria o contato
        if (seq !== checkSeq.current) return;  // resposta obsoleta
        if (!res.ok) { setCheckError(res.error || 'Erro ao verificar número.'); setChecking(false); return; }
        if (!res.data.registered) { setCheckError('Este número não possui WhatsApp.'); setChecking(false); return; }
        // `phone` é o canônico do WhatsApp (usado pra enviar/evitar duplicatas BR,
        // pode soltar o 9º dígito); `displayPhone` é o que o operador digitou
        // (preserva o 9º dígito e o DDD com/sem zero) — só pro rótulo.
        setCheckResult({ phone: res.data.phone || normalized, displayPhone: normalized, registered: true, name: res.data.name || '' });
        setChecking(false);
      } catch (e) {
        if (seq !== checkSeq.current) return;
        setCheckError('Erro ao verificar número. Tente novamente.');
        setChecking(false);
      }
    }, 500);
    return () => clearTimeout(t);
  }, [phoneInput]);

  // Nome a exibir ao lado do número: contato salvo (sem o ~ de "veio do WhatsApp")
  // tem prioridade; senão o pushName retornado pelo check.
  const resolvedName = (() => {
    if (!checkResult) return '';
    const saved = contacts.find(c => c.phone === checkResult.phone);
    if (saved && saved.name) return saved.name.replace(/^~/, '');
    return checkResult.name || '';
  })();

  const canSend = !!checkResult && !!channelId && message.trim().length > 0 && !sending;

  async function handleSend() {
    if (!canSend) return;
    setSending(true);
    setSendError(null);
    const phone = checkResult.phone;
    try {
      // conversation_id null + channel_id → o backend cria a conversa nesse canal.
      const res = await sendMessage(phone, message.trim(), null, null, channelId);
      if (!res.ok) { setSendError(res.error || 'Falha ao enviar mensagem.'); setSending(false); return; }
      onSent && onSent(phone, channelId);
    } catch (e) {
      setSendError('Falha ao enviar mensagem. Tente novamente.');
      setSending(false);
    }
  }

  function onTextKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return html`
    <div
      class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-lg w-full flex flex-col max-h-[90vh] overflow-hidden">
        <!-- Header -->
        <div class="flex items-center justify-between px-5 py-4 border-b border-wa-border shrink-0">
          <h2 class="text-base font-semibold text-wa-text">Nova conversa</h2>
          <button
            onClick=${onClose}
            class="text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
            title="Fechar"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        <!-- Body -->
        <div class="px-5 py-4 flex flex-col gap-4 overflow-y-auto wa-scrollbar">
          <!-- Para: número -->
          <div class="flex flex-col gap-1.5">
            <label class="text-[13px] font-medium text-wa-secondary">Para</label>
            <input
              type="tel"
              autofocus
              value=${phoneInput}
              onInput=${(e) => setPhoneInput(e.target.value)}
              placeholder="DDD + número (ex: 64 90000-0000)"
              class="wa-field w-full rounded-lg px-3 py-2 text-[14px] outline-none border border-wa-border focus:border-wa-teal"
            />
            <div class="min-h-[18px] text-[12px]">
              ${checking ? html`<span class="text-wa-secondary animate-pulse-slow">Verificando se o número possui WhatsApp...</span>`
                : checkError ? html`<span class="text-red-400">${checkError}</span>`
                : checkResult ? html`
                    <span class="flex items-center gap-1.5 text-wa-secondary">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="#00a884"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
                      <span class="text-wa-text font-medium">${formatPhoneDisplay(checkResult.displayPhone || checkResult.phone)}</span>
                      ${resolvedName ? html`<span class="text-wa-secondary">· ${resolvedName}</span>` : ''}
                    </span>`
                : ''}
            </div>
          </div>

          <!-- Via: caixa de entrada -->
          <div class="flex flex-col gap-1.5">
            <label class="text-[13px] font-medium text-wa-secondary">Via</label>
            ${channelsLoading ? html`
              <div class="text-[13px] text-wa-secondary">Carregando caixas de entrada...</div>
            ` : channels.length === 0 ? html`
              <div class="text-[13px] text-wa-secondary bg-wa-panel rounded-lg py-2.5 px-3">
                Nenhuma caixa de entrada conectada no momento.
              </div>
            ` : html`
              <select
                value=${channelId}
                onChange=${(e) => setChannelId(e.target.value)}
                class="wa-field w-full rounded-lg px-3 py-2 text-[14px] outline-none border border-wa-border focus:border-wa-teal cursor-pointer"
              >
                ${channels.map((ch) => {
                  const meta = PROVIDER_META[ch.provider] || { label: ch.provider || 'Canal' };
                  const phoneSuffix = ch.own_phone ? ` (+${String(ch.own_phone).replace(/^\+/, '')})` : '';
                  return html`<option key=${ch.id} value=${ch.id}>${channelLabel(ch)} · ${meta.label}${phoneSuffix}</option>`;
                })}
              </select>
            `}
          </div>

          <!-- Mensagem -->
          <div class="flex flex-col gap-1.5">
            <label class="text-[13px] font-medium text-wa-secondary">Mensagem</label>
            <textarea
              value=${message}
              onInput=${(e) => setMessage(e.target.value)}
              onKeyDown=${onTextKeyDown}
              placeholder="Escreva sua mensagem aqui..."
              rows="4"
              class="wa-field w-full rounded-lg px-3 py-2 text-[14px] outline-none border border-wa-border focus:border-wa-teal resize-none"
            ></textarea>
          </div>

          ${sendError ? html`<div class="text-[13px] text-red-400">${sendError}</div>` : ''}
        </div>

        <!-- Footer -->
        <div class="flex items-center justify-end gap-2 px-5 py-4 border-t border-wa-border shrink-0">
          <button
            onClick=${onClose}
            class="px-4 py-2 text-[14px] rounded-lg text-wa-text bg-wa-panel hover:bg-wa-hover transition-colors"
          >Descartar</button>
          <button
            disabled=${!canSend}
            onClick=${handleSend}
            class="px-5 py-2 text-[14px] rounded-lg text-white transition-colors ${canSend ? 'bg-wa-teal hover:bg-wa-teal/90 cursor-pointer' : 'bg-wa-teal/40 cursor-not-allowed'}"
          >${sending ? 'Enviando...' : 'Enviar'}</button>
        </div>
      </div>
    </div>
  `;
}
