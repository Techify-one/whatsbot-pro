import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { checkPhone, listConnectedChannels, sendMessage, getChannelSessionState } from '../../services/api.js';
import { formatPhoneDisplay } from '../../utils/phone.js';
import { TemplatePicker } from './TemplatePicker.js';
import { useQuickReplies } from '../../hooks/useQuickReplies.js';

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


function channelLabel(ch) {
  const meta = PROVIDER_META[ch.provider] || { label: ch.provider || 'Canal' };
  return ch.display_name || meta.label;
}

// Modal de "Novo atendimento" (estilo Chatwoot): digita o número (mostra o nome do
// contato ao lado se já existir), escolhe a caixa de entrada conectada e escreve a
// 1ª mensagem. Ao enviar, o atendimento novo já aparece na sidebar (onSent dispara o
// refresh + abre a thread). Roteia pelo `channel_id` escolhido (o atendimento ainda
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
  // Janela de 24h / templates (plano 21): estado da sessão pro número+canal escolhido.
  const [sessionState, setSessionState] = useState(null);  // {templates_supported, session_open, has_conversation, conversation_id} | null
  const [sessionLoading, setSessionLoading] = useState(false);
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const sessionSeq = useRef(0);
  // Respostas rápidas (autocomplete do "/") no campo de mensagem normal.
  const { quickReplies, getCandidates } = useQuickReplies();
  const [quickReplyMenu, setQuickReplyMenu] = useState(null);  // {query, start, index} | null
  const inputRef = useRef(null);

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

  // Verifica o número (debounce) sempre que o input OU o canal muda e parece um
  // telefone. O canal importa: só o GOWA consulta o WhatsApp; Cloud API/Telegram
  // assumem válido (não dá pra verificar antes de enviar).
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
        const res = await checkPhone(normalized, false, channelId);  // só valida, não cria o contato
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
  }, [phoneInput, channelId]);

  // Nome a exibir ao lado do número: contato salvo (sem o ~ de "veio do WhatsApp")
  // tem prioridade; senão o pushName retornado pelo check.
  const resolvedName = (() => {
    if (!checkResult) return '';
    const saved = contacts.find(c => c.phone === checkResult.phone);
    if (saved && saved.name) return saved.name.replace(/^~/, '');
    return checkResult.name || '';
  })();

  // Resolve a janela de 24h sempre que (número verificado + canal) mudam — sem modal:
  // a tela mostra o estado inline. A janela de 24h é contada a partir da ÚLTIMA
  // MENSAGEM DO CLIENTE (inbound role='user') daquela atendimento, no backend.
  useEffect(() => {
    setSessionState(null);
    if (!checkResult || !channelId) { setSessionLoading(false); return; }
    const phone = checkResult.phone;
    const seq = ++sessionSeq.current;
    let alive = true;
    setSessionLoading(true);
    getChannelSessionState(channelId, phone).then((res) => {
      if (!alive || seq !== sessionSeq.current) return;
      setSessionState((res && res.ok) ? res.data : null);
      setSessionLoading(false);
    }).catch(() => { if (alive && seq === sessionSeq.current) setSessionLoading(false); });
    return () => { alive = false; };
  }, [checkResult, channelId]);

  const templatesChannel = !!(sessionState && sessionState.templates_supported);
  const windowOpen = !!(sessionState && sessionState.session_open);
  // Texto livre só é permitido dentro da janela de 24h (ou em canais sempre-abertos
  // como o GOWA, onde windowOpen é sempre true). O template é SEMPRE opcional num
  // canal com suporte — mesmo dentro das 24h.
  const freeTextAllowed = !templatesChannel || windowOpen;
  // Fora da janela num canal com templates → texto livre indisponível, só template.
  const windowClosed = templatesChannel && !windowOpen;

  const canSendNormal = !!checkResult && !!channelId && message.trim().length > 0
    && !sending && freeTextAllowed && !sessionLoading;
  const canPickTemplate = !!checkResult && !!channelId && templatesChannel && !sessionLoading;

  async function handleSendNormal() {
    if (!canSendNormal) return;
    setSending(true);
    setSendError(null);
    const phone = checkResult.phone;
    // Janela aberta num atendimento existente → manda o conversation_id pro backend honrar
    // a janela (sem ele, um envio "novo atendimento" cairia no bloqueio de 24h).
    const convId = (sessionState && sessionState.conversation_id) || null;
    try {
      // conversation_id null + channel_id → o backend cria o atendimento nesse canal.
      const res = await sendMessage(phone, message.trim(), null, convId, channelId);
      if (!res.ok) { setSendError(res.error || 'Falha ao enviar mensagem.'); setSending(false); return; }
      onSent && onSent(phone, channelId);
    } catch (e) {
      setSendError('Falha ao enviar mensagem. Tente novamente.');
      setSending(false);
    }
  }

  // ── Respostas rápidas ("/") ─────────────────────────────────────────────
  function updateQuickReplyMenu(el, val) {
    const pos = (el && el.selectionStart != null) ? el.selectionStart : val.length;
    const m = val.slice(0, pos).match(/(?:^|\s)\/([\w-]*)$/);
    // Abre quando há candidatos OU quando o token é só "/" (query vazia) — nesse
    // caso, mesmo sem respostas cadastradas, mostra o estado-vazio com orientação
    // (antes não abria nada e parecia bug). "/palavra" sem match continua fechado
    // pra não sequestrar mensagens que começam com "/".
    if (m && (getCandidates(m[1]).length || m[1] === '')) {
      setQuickReplyMenu({ query: m[1], start: pos - m[1].length - 1, index: 0 });
    } else {
      setQuickReplyMenu(null);
    }
  }

  function applyQuickReply(cand) {
    if (!cand || !quickReplyMenu) return;
    const el = inputRef.current;
    const pos = (el && el.selectionStart != null) ? el.selectionStart : message.length;
    const before = message.slice(0, quickReplyMenu.start);
    const after = message.slice(pos);
    const insert = cand.content;
    setMessage(before + insert + after);
    setQuickReplyMenu(null);
    setTimeout(() => {
      if (el) {
        el.focus();
        const caret = (before + insert).length;
        el.setSelectionRange(caret, caret);
      }
    }, 0);
  }

  function onMessageInput(e) {
    setMessage(e.target.value);
    updateQuickReplyMenu(e.target, e.target.value);
  }

  function onTextKeyDown(e) {
    if (quickReplyMenu) {
      const cands = getCandidates(quickReplyMenu.query);
      if (cands.length) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setQuickReplyMenu(mm => ({ ...mm, index: Math.min((mm.index || 0) + 1, cands.length - 1) }));
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setQuickReplyMenu(mm => ({ ...mm, index: Math.max((mm.index || 0) - 1, 0) }));
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          applyQuickReply(cands[Math.min(quickReplyMenu.index || 0, cands.length - 1)]);
          return;
        }
      }
      if (e.key === 'Escape') { e.preventDefault(); setQuickReplyMenu(null); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendNormal();
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

          <!-- Status da janela de 24h (inline, sem modal) -->
          ${checkResult && templatesChannel && windowOpen ? html`
            <div class="flex items-start gap-1.5 rounded-lg border border-green-200 bg-green-100 text-green-700 px-3 py-2 text-[12px]">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="shrink-0 mt-0.5"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
              <span>A janela de 24h está aberta. Você pode enviar uma mensagem normal ou, se preferir, um template.</span>
            </div>
          ` : ''}
          ${checkResult && windowClosed ? html`
            <div class="flex items-start gap-1.5 rounded-lg border border-amber-200 bg-amber-100 text-amber-700 px-3 py-2.5 text-[13px]">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="shrink-0 mt-0.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              <span>${sessionState && sessionState.has_conversation
                ? 'Passaram-se mais de 24 horas desde a última mensagem do cliente. Só é possível enviar um template aprovado.'
                : 'Ainda não há conversa com este número neste canal. O primeiro contato precisa ser um template aprovado.'}</span>
            </div>
          ` : ''}

          <!-- Mensagem normal (com respostas rápidas "/") -->
          <div class="flex flex-col gap-1.5">
            <label class="text-[13px] font-medium text-wa-secondary">Mensagem</label>
            <div class="relative">
              <textarea
                ref=${inputRef}
                value=${message}
                onInput=${onMessageInput}
                onKeyDown=${onTextKeyDown}
                disabled=${!freeTextAllowed && !sessionLoading}
                placeholder=${freeTextAllowed ? 'Escreva sua mensagem aqui...  (use / para respostas rápidas)' : 'Texto livre indisponível fora da janela de 24h — envie um template.'}
                rows="4"
                class="wa-field w-full rounded-lg px-3 py-2 text-[14px] outline-none border border-wa-border focus:border-wa-teal resize-none ${(!freeTextAllowed && !sessionLoading) ? 'opacity-60 cursor-not-allowed' : ''}"
              ></textarea>
              ${quickReplyMenu ? (() => {
                const cands = getCandidates(quickReplyMenu.query);
                // Estado-vazio: token "/" sem nenhuma resposta rápida cadastrada.
                if (!cands.length) {
                  if (quickReplies.length === 0) {
                    return html`
                      <div class="absolute left-0 right-0 bottom-[calc(100%+6px)] bg-wa-panel border border-wa-border rounded-[8px] shadow-lg px-[12px] py-[10px] z-30 text-[13px] text-wa-secondary">
                        Nenhuma resposta rápida cadastrada. Crie no menu <span class="text-wa-text font-medium">⚙ ▸ Respostas Rápidas</span>.
                      </div>
                    `;
                  }
                  return '';
                }
                const sel = Math.min(quickReplyMenu.index || 0, cands.length - 1);
                return html`
                  <div class="absolute left-0 right-0 bottom-[calc(100%+6px)] max-h-[210px] overflow-y-auto bg-wa-panel border border-wa-border rounded-[8px] shadow-lg py-[4px] z-30 wa-scrollbar">
                    ${cands.map((c, i) => html`
                      <button type="button" key=${c.id} onMouseDown=${(ev) => { ev.preventDefault(); applyQuickReply(c); }}
                        class="w-full text-left px-[12px] py-[7px] text-[14px] flex items-start gap-[8px] ${i === sel ? 'bg-wa-hover' : ''} hover:bg-wa-hover">
                        <span class="text-wa-teal font-mono shrink-0">/${c.short_code}</span>
                        <span class="text-wa-secondary truncate">${(c.content || '').replace(/\s+/g, ' ').slice(0, 60)}</span>
                      </button>
                    `)}
                  </div>
                `;
              })() : ''}
            </div>
            ${sessionLoading && checkResult ? html`
              <span class="text-[12px] text-wa-secondary animate-pulse-slow">Verificando janela de conversa...</span>
            ` : ''}
          </div>

          ${sendError ? html`<div class="text-[13px] text-red-400">${sendError}</div>` : ''}
        </div>

        <!-- Footer: 1 botão (canal normal) ou 2 botões (canal com template) -->
        <div class="flex items-center justify-end gap-2 px-5 py-4 border-t border-wa-border shrink-0">
          <button
            onClick=${onClose}
            class="px-4 py-2 text-[14px] rounded-lg text-wa-text bg-wa-panel hover:bg-wa-hover transition-colors"
          >Descartar</button>
          ${templatesChannel ? html`
            <button
              disabled=${!canPickTemplate}
              onClick=${() => setShowTemplatePicker(true)}
              class="px-4 py-2 text-[14px] rounded-lg border border-wa-teal transition-colors ${canPickTemplate ? 'text-wa-teal hover:bg-wa-teal/10 cursor-pointer' : 'text-wa-teal/40 border-wa-teal/40 cursor-not-allowed'}"
            >Enviar como template</button>
          ` : ''}
          <button
            disabled=${!canSendNormal}
            onClick=${handleSendNormal}
            class="px-5 py-2 text-[14px] rounded-lg text-white transition-colors ${canSendNormal ? 'bg-wa-teal hover:bg-wa-teal/90 cursor-pointer' : 'bg-wa-teal/40 cursor-not-allowed'}"
          >${sending ? 'Enviando...' : (templatesChannel ? 'Enviar mensagem normal' : 'Enviar')}</button>
        </div>
      </div>

      ${showTemplatePicker && checkResult && channelId ? html`
        <${TemplatePicker}
          channelId=${channelId}
          phone=${checkResult.phone}
          onClose=${() => setShowTemplatePicker(false)}
          onSent=${() => { setShowTemplatePicker(false); onSent && onSent(checkResult.phone, channelId); }}
        />
      ` : ''}
    </div>
  `;
}
