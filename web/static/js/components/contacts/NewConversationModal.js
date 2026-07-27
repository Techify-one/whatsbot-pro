import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { checkPhone, listConnectedChannels, sendMessage, getChannelSessionState, getContacts } from '../../services/api.js';
import { formatPhoneDisplay } from '../../utils/phone.js';
import { highlightComposerMarkup, toWhatsAppMarkup } from '../../utils/formatWhatsApp.js';
import { syncMirror } from '../../utils/composerMirror.js';
import { TemplatePicker } from './TemplatePicker.js';
import { useQuickReplies } from '../../hooks/useQuickReplies.js';
import { avatarUrl } from './utils.js';
import { DefaultAvatar } from './icons.js';
import { channelPickerMeta } from '../../services/providerCatalog.js';
import { useProviderCatalog } from '../../hooks/useProviderCatalog.js';

const html = htm.bind(h);

// Casefold + strip accents — espelha o `_fold` do backend pra busca/realce baterem.
function foldStr(s) {
  return (s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

// Quebra `text` em segmentos {s, hit} ao redor das ocorrências de `query`
// (case/acento-insensível), pra destacar o trecho casado no autocomplete.
function highlightParts(text, query) {
  const t = text || '';
  const q = foldStr(query);
  if (!q) return [{ s: t, hit: false }];
  const f = foldStr(t);
  if (f.length !== t.length) return [{ s: t, hit: false }];  // fold mudou o tamanho → sem realce
  const parts = [];
  let i = 0;
  while (i <= t.length) {
    const idx = f.indexOf(q, i);
    if (idx === -1) { if (i < t.length) parts.push({ s: t.slice(i), hit: false }); break; }
    if (idx > i) parts.push({ s: t.slice(i, idx), hit: false });
    parts.push({ s: t.slice(idx, idx + q.length), hit: true });
    i = idx + q.length;
  }
  return parts;
}

// Rótulo/cor por provider — do CATÁLOGO ÚNICO (plano 76), espelha o ChannelPickerModal.
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
  return ch.display_name || channelPickerMeta(ch.provider).label;
}

// Modal de "Novo atendimento" (estilo Chatwoot): digita o número (mostra o nome do
// contato ao lado se já existir), escolhe a caixa de entrada conectada e escreve a
// 1ª mensagem. Ao enviar, o atendimento novo já aparece na sidebar (onSent dispara o
// refresh + abre a thread). Roteia pelo `channel_id` escolhido (o atendimento ainda
// não existe, então o backend a cria nesse canal).
export function NewConversationModal({ contacts = [], onClose, onSent }) {
  useProviderCatalog();  // re-render quando o catálogo de providers carregar
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
  // Overlay de highlight (WYSIWYG no campo): espelho atrás do textarea que mostra
  // o texto com negrito/itálico/tachado/mono reais. Sincroniza a rolagem E a
  // largura de conteúdo (a barra de rolagem encolhe só o textarea — sem isso as
  // quebras de linha divergem e o cursor descola do fim do texto).
  const mirrorRef = useRef(null);
  useEffect(() => {
    syncMirror(inputRef.current, mirrorRef.current);
  }, [message]);
  // Autocomplete do campo "Para": busca contatos por NOME ou número (server-side,
  // cobre todos os contatos independente do filtro da sidebar). Escolher um item
  // preenche o número e dispara a verificação de WhatsApp já existente.
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [sugIndex, setSugIndex] = useState(0);
  const sugSeq = useRef(0);
  const paraRef = useRef(null);

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

  // Canal escolhido + seu tipo de contato (plano tipos-de-contato). Canais "de
  // telefone" (WhatsApp) normalizam BR (+55) e verificam registro no WhatsApp; os
  // demais usam um identificador OPACO — Telegram = chat_id, website = session id —
  // que NÃO pode ser normalizado nem verificado (o "+55" quebra o destino e a
  // verificação não faz sentido). Vai pro envio exatamente como digitado/escolhido.
  const selectedChannel = channels.find((ch) => String(ch.id) === String(channelId)) || null;
  const channelType = selectedChannel && selectedChannel.contact_type;
  const isPhoneChannel = !channelType || channelType === 'whatsapp';

  // Verifica o número (debounce) sempre que o input OU o canal muda e parece um
  // telefone. O canal importa: só o GOWA consulta o WhatsApp; Cloud API/Telegram
  // assumem válido (não dá pra verificar antes de enviar).
  useEffect(() => {
    setCheckResult(null);
    setCheckError(null);
    // Canal de identificador opaco (Telegram/website/…): sem normalização BR e sem
    // verificação — o texto digitado (ou o id do contato escolhido no autocomplete)
    // JÁ é o destinatário. `opaque` sinaliza pro render mostrar o id cru (sem "+55").
    if (!isPhoneChannel) {
      const id = phoneInput.trim();
      setChecking(false);
      if (id) setCheckResult({ phone: id, displayPhone: id, registered: true, name: '', opaque: true });
      return;
    }
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
  }, [phoneInput, channelId, isPhoneChannel]);

  // Busca de contatos (debounce) enquanto o operador digita no campo "Para".
  // Bate no mesmo endpoint da lista (`/api/contacts?q=`), que casa nome, número e
  // nome de grupo no backend. Só busca com o dropdown aberto (evita requisição
  // depois de escolher um contato, quando o campo já virou o número).
  useEffect(() => {
    const q = phoneInput.trim();
    if (!showSuggestions || q.length < 2) { setSuggestions([]); return; }
    // Tipo de contato do canal escolhido (plano tipos-de-contato): o dropdown "Para"
    // sugere só contatos daquele tipo (canal Telegram → só contatos telegram). Se o
    // canal não expõe o tipo (payload antigo), não filtra (mostra todos).
    const wantType = channelType;
    const seq = ++sugSeq.current;
    const t = setTimeout(async () => {
      try {
        // plano 62 F3: teto no caller — com `limit` o backend pagina e devolve o
        // envelope { items, total, has_more } em vez da lista completa.
        const res = await getContacts(q, false, { limit: 20 });
        if (seq !== sugSeq.current) return;  // resposta obsoleta
        const list = (res && res.ok && res.data && Array.isArray(res.data.items))
          ? res.data.items : [];
        // Só pessoas (grupos não são um "novo atendimento" por número) e com número,
        // e do mesmo tipo de contato que o canal escolhido.
        const people = list
          .filter((c) => c && !c.is_group && c.phone)
          .filter((c) => !wantType || (c.contact_type || 'outros') === wantType)
          .slice(0, 8);
        setSuggestions(people);
        setSugIndex(0);
      } catch (e) {
        if (seq === sugSeq.current) setSuggestions([]);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [phoneInput, showSuggestions, channelId, channels]);

  function pickContact(c) {
    if (!c || !c.phone) return;
    setShowSuggestions(false);
    setSuggestions([]);
    setPhoneInput(c.phone);  // dispara a verificação de WhatsApp já existente
    setTimeout(() => { if (paraRef.current) paraRef.current.focus(); }, 0);
  }

  function onParaInput(e) {
    setPhoneInput(e.target.value);
    setShowSuggestions(true);
  }

  function onParaKeyDown(e) {
    if (!showSuggestions || suggestions.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSugIndex((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSugIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      pickContact(suggestions[Math.min(sugIndex, suggestions.length - 1)]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setShowSuggestions(false);
    }
  }

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
      // Converte o **negrito** do campo para *negrito* (formato nativo do WhatsApp).
      const res = await sendMessage(phone, toWhatsAppMarkup(message.trim()), null, convId, channelId);
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
                  const meta = channelPickerMeta(ch.provider);
                  const phoneSuffix = ch.own_phone ? ` (+${String(ch.own_phone).replace(/^\+/, '')})` : '';
                  return html`<option key=${ch.id} value=${ch.id}>${channelLabel(ch)} · ${meta.label}${phoneSuffix}</option>`;
                })}
              </select>
            `}
          </div>

          <!-- Para: nome do contato ou número -->
          <div class="flex flex-col gap-1.5">
            <label class="text-[13px] font-medium text-wa-secondary">Para</label>
            <div class="relative">
              <input
                ref=${paraRef}
                type="text"
                autofocus
                value=${phoneInput}
                onInput=${onParaInput}
                onKeyDown=${onParaKeyDown}
                onFocus=${() => { if (phoneInput.trim().length >= 2) setShowSuggestions(true); }}
                onBlur=${() => setTimeout(() => setShowSuggestions(false), 150)}
                placeholder=${isPhoneChannel ? 'Nome ou número (ex: João ou 64 90000-0000)' : 'Nome ou ID do chat (ex: 8192089640)'}
                autocomplete="off"
                class="wa-field w-full rounded-lg px-3 py-2 text-[14px] outline-none border border-wa-border focus:border-wa-teal"
              />
              ${showSuggestions && suggestions.length > 0 ? html`
                <div class="absolute left-0 right-0 top-[calc(100%+4px)] max-h-[240px] overflow-y-auto bg-wa-panel border border-wa-border rounded-lg shadow-lg py-1 z-30 wa-scrollbar">
                  ${suggestions.map((c, i) => {
                    const nm = (c.name || '').replace(/^~/, '');
                    // Canal opaco (Telegram/website): o "phone" é um id (chat_id), não
                    // formata como telefone BR (senão vira um "+55 (…)" enganoso).
                    const phoneStr = isPhoneChannel ? formatPhoneDisplay(c.phone) : c.phone;
                    const primary = nm || phoneStr;
                    const secondary = nm ? phoneStr : '';
                    const sel = i === Math.min(sugIndex, suggestions.length - 1);
                    return html`
                      <button
                        type="button"
                        key=${c.phone}
                        onMouseDown=${(ev) => { ev.preventDefault(); pickContact(c); }}
                        class="w-full flex items-center gap-2.5 px-3 py-2 text-left ${sel ? 'bg-wa-hover' : ''} hover:bg-wa-hover"
                      >
                        <div class="w-8 h-8 rounded-full overflow-hidden shrink-0">
                          <${DefaultAvatar} size=${32} avatarUrl=${avatarUrl(c.phone, c.avatar_v)} />
                        </div>
                        <div class="min-w-0 flex-1">
                          <div class="text-[14px] text-wa-text truncate">
                            ${highlightParts(primary, phoneInput).map((p) => p.hit
                              ? html`<mark class="bg-wa-teal/30 text-wa-text rounded px-0.5">${p.s}</mark>`
                              : p.s)}
                          </div>
                          ${secondary ? html`<div class="text-[12px] text-wa-secondary truncate">${secondary}</div>` : ''}
                        </div>
                      </button>
                    `;
                  })}
                </div>
              ` : ''}
            </div>
            <div class="min-h-[18px] text-[12px]">
              ${checking ? html`<span class="text-wa-secondary animate-pulse-slow">Verificando se o número possui WhatsApp...</span>`
                : checkError ? html`<span class="text-red-400">${checkError}</span>`
                : checkResult ? html`
                    <span class="flex items-center gap-1.5 text-wa-secondary">
                      ${checkResult.opaque
                        ? html`<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`
                        : html`<svg viewBox="0 0 24 24" width="14" height="14" fill="#00a884"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>`}
                      <span class="text-wa-text font-medium">${checkResult.opaque
                        ? checkResult.displayPhone
                        : formatPhoneDisplay(checkResult.displayPhone || checkResult.phone)}</span>
                      ${resolvedName ? html`<span class="text-wa-secondary">· ${resolvedName}</span>` : ''}
                    </span>`
                : ''}
            </div>
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
              <div
                ref=${mirrorRef}
                aria-hidden="true"
                class="wa-field pointer-events-none absolute inset-0 z-0 overflow-hidden box-border rounded-lg px-3 py-2 text-[14px] whitespace-pre-wrap break-words border border-transparent ${(!freeTextAllowed && !sessionLoading) ? 'opacity-60' : ''}"
                dangerouslySetInnerHTML=${{ __html: highlightComposerMarkup(message) }}
              ></div>
              <textarea
                ref=${inputRef}
                value=${message}
                onInput=${onMessageInput}
                onKeyDown=${onTextKeyDown}
                onScroll=${(e) => syncMirror(e.target, mirrorRef.current)}
                disabled=${!freeTextAllowed && !sessionLoading}
                placeholder=${freeTextAllowed ? 'Escreva sua mensagem aqui...  (use / para respostas rápidas)' : 'Texto livre indisponível fora da janela de 24h — envie um template.'}
                rows="4"
                style="color: transparent; background: transparent; caret-color: #000;"
                class="wa-field relative z-[1] box-border w-full rounded-lg px-3 py-2 text-[14px] outline-none border border-wa-border focus:border-wa-teal resize-none ${(!freeTextAllowed && !sessionLoading) ? 'opacity-60 cursor-not-allowed' : ''}"
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
