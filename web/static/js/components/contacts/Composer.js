import { h } from 'preact';
import { useRef, useEffect } from 'preact/hooks';
import htm from 'htm';
import { SendIcon, EmojiIcon, AttachIcon, MicIcon, StopIcon, TemplateIcon } from './icons.js';
import { MediaTray } from './MediaTray.js';
import { EmojiPicker } from './EmojiPicker.js';
import { MediaRejectedModal } from './MediaRejectedModal.js';
import { hasPermission } from '../../utils/permissions.js';
import { templatePickerAvailable } from './TemplatePickerHost.js';
import { highlightComposerMarkup } from '../../utils/formatWhatsApp.js';
import { syncMirror } from '../../utils/composerMirror.js';

const html = htm.bind(h);

function formatRecordTime(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

// ── Composer: the whole input bar below the message list ─────────────
//
// Extracted verbatim from ContactDetail.js. Wires together the composer hook
// (text input + send + reply + emoji), the token-autocomplete hook (@mention /
// "/quick-reply" menus), the media-upload hook (attach menu + confirmation
// overlay + hidden inputs) and the audio-recorder hook (recording bar). Keeps
// the exact mode toggle / reply preview / 24h-window banner / send-vs-mic logic.
//
// Receives the hook return objects so each piece of state stays where it lives;
// this component is presentational + event wiring only.
//
// ⚠️ Plano 124 — a barra de entrada NÃO some quando há anexo pendente. Ela sumia
// (a fila renderizava no lugar dela), e era essa troca que fazia o texto já
// digitado desaparecer da tela e matava o `onPaste` — que vive na `<textarea>` e
// portanto ia junto, impedindo colar um SEGUNDO arquivo. Hoje a bandeja
// (`MediaTray`) é uma faixa acima do `<form>` e o texto do compositor é a
// legenda do lote. O único estado que ainda substitui a barra é a GRAVAÇÃO de
// áudio, que é modal de verdade.
export function Composer({
  sandbox, canSend, templatesSupported, sessionClosed,
  composer, autocomplete, media, audio, quotedInfo, openTemplatePicker, handleKeyDown,
  currentUser = null,
}) {
  // P48: the /atalho quick-reply picker only shows for users who can manage them.
  const canQuickReply = sandbox || hasPermission(currentUser, 'quickreply.manage');
  const {
    input, mode, setMode, aiReadPrivate, setAiReadPrivate, aiReplyInChat, setAiReplyInChat,
    replyingTo, setReplyingTo, emojiOpen, setEmojiOpen, inputRef, emojiRef,
    insertEmoji, handleInputChange,
  } = composer;
  const {
    mentionMenu, quickReplyMenu, getMentionCandidates, getQuickReplyCandidates,
    mentionLabel, applyMention, applyQuickReply,
  } = autocomplete;
  const {
    attachMenuOpen, attachMenuRef, pendingQueue,
    fileInputRef, docInputRef, videoInputRef, sending, sendProgressLabel, removePendingItem,
    handleAttachClick, pickImage, pickDocument, pickVideo,
    handleFileSelected, handleDocSelected, handleVideoSelected,
    handlePaste, cancelPendingMedia,
    rejection, dismissRejection,
  } = media;
  const hasPending = pendingQueue.length > 0;
  // Áudio é o único anexo que não aceita legenda (`/send-audio` é nota de voz),
  // então o placeholder promete o que o envio de fato faz: mandar o texto como
  // mensagem separada, antes do clipe.
  const audioOnlyPending = hasPending && pendingQueue.every(i => i.kind === 'audio');
  const { recording, recordDuration, handleMicClick } = audio;

  // Highlight overlay (WYSIWYG-in-place): a mirror <div> renders the typed text
  // with real bold/italic/strike/mono styling behind a transparent-text
  // textarea. Keep the mirror scrolled AND as wide as the textarea's content
  // box so long messages (past the 6-line cap, quando a barra de rolagem
  // aparece e rouba 6px) quebrem as linhas no mesmo ponto — senão o cursor
  // desencontra do fim do texto visível.
  const mirrorRef = useRef(null);
  useEffect(() => {
    const sync = () => syncMirror(inputRef.current, mirrorRef.current);
    sync();
    // O auto-resize do textarea vive num effect do componente PAI (useComposer),
    // que roda depois deste — remede na frame seguinte, já com a altura final
    // (é o instante em que a barra de rolagem nasce).
    const raf = requestAnimationFrame(sync);
    return () => cancelAnimationFrame(raf);
  }, [input]);

  const hasText = input.trim().length > 0;

  return html`
    <!-- Hidden file inputs for image / document upload -->
    <input
      ref=${fileInputRef}
      type="file"
      accept="image/*"
      multiple
      class="hidden"
      onChange=${handleFileSelected}
    />
    <input
      ref=${docInputRef}
      type="file"
      multiple
      class="hidden"
      onChange=${handleDocSelected}
    />
    <input
      ref=${videoInputRef}
      type="file"
      accept="video/*"
      class="hidden"
      onChange=${handleVideoSelected}
    />

    <!-- Anexo recusado pelas regras do canal (tamanho/formato) -->
    <${MediaRejectedModal} rejection=${rejection} onClose=${dismissRejection} />

    <!-- Bandeja de anexos (plano 64 · F7; virou faixa no plano 124) -->
    ${hasPending && canSend ? html`
      <${MediaTray}
        queue=${pendingQueue}
        onRemove=${removePendingItem}
        onCancel=${cancelPendingMedia}
        sending=${sending}
        escClears=${!hasText}
        hasText=${hasText}
      />
    ` : ''}

    <!-- Progresso do lote em voo (plano 64 · F5). A fila some ao confirmar (as
         bolhas óticas já apareceram no fio), então o "N de M" vive aqui. -->
    ${sending && sendProgressLabel ? html`
      <div class="flex items-center justify-center gap-[8px] bg-wa-panel border-t border-wa-border px-[16px] py-[7px] shrink-0">
        <span class="w-[8px] h-[8px] rounded-full bg-wa-teal animate-pulse"></span>
        <span class="text-[12px] text-wa-secondary">${sendProgressLabel}</span>
      </div>
    ` : ''}

    <!-- Input area -->
    ${!canSend ? html`
      <div class="flex items-center justify-center px-[10px] py-[14px] bg-wa-panel min-h-[62px] shrink-0 border-t border-wa-border">
        <span class="text-wa-secondary text-[14px] flex items-center gap-[6px]">
          <svg class="w-[16px] h-[16px]" viewBox="0 0 24 24" fill="currentColor">
            <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/>
          </svg>
          Você não pode enviar mensagens neste grupo
        </span>
      </div>
    ` : recording ? html`
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
      ${!sandbox ? html`
      <div class="flex items-center gap-[10px] flex-wrap px-[14px] pt-[7px] pb-[3px] bg-wa-panel shrink-0">
        <div class="inline-flex items-center gap-[2px] p-[3px] rounded-full" style="background:#111b21;">
          <button
            type="button"
            onClick=${() => setMode('reply')}
            class="text-[12px] font-medium px-[14px] py-[4px] rounded-full transition-colors"
            style="background:${mode === 'reply' ? '#005c4b' : 'transparent'}; color:${mode === 'reply' ? '#ffffff' : '#aebac1'};"
          >Responder</button>
          <button
            type="button"
            onClick=${() => setMode('private')}
            class="text-[12px] font-medium px-[14px] py-[4px] rounded-full transition-colors flex items-center gap-[5px]"
            style="background:${mode === 'private' ? '#7c3aed' : 'transparent'}; color:${mode === 'private' ? '#ffffff' : '#aebac1'};"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
            Mensagem Privada
          </button>
        </div>
        ${mode === 'private' ? html`
          <label class="inline-flex items-center gap-[6px] cursor-pointer select-none" title="Quando ligado, a IA processa a mensagem privada como instrução.">
            <input
              type="checkbox"
              class="sr-only peer"
              checked=${aiReadPrivate}
              onChange=${e => setAiReadPrivate(e.target.checked)}
            />
            <div class="relative w-[28px] h-[16px] bg-gray-500 rounded-full peer-checked:bg-violet-500 transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-[12px] after:w-[12px] after:transition-transform peer-checked:after:translate-x-[12px]"></div>
            <span class="text-[12px] text-wa-secondary">IA lê</span>
          </label>
          ${aiReadPrivate ? html`
            <label class="inline-flex items-center gap-[6px] cursor-pointer select-none" title="Quando ligado, a IA responde no chat do contato. Quando desligado, a resposta fica apenas como nota privada.">
              <input
                type="checkbox"
                class="sr-only peer"
                checked=${aiReplyInChat}
                onChange=${e => setAiReplyInChat(e.target.checked)}
              />
              <div class="relative w-[28px] h-[16px] bg-gray-500 rounded-full peer-checked:bg-violet-500 transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-[12px] after:w-[12px] after:transition-transform peer-checked:after:translate-x-[12px]"></div>
              <span class="text-[12px] text-wa-secondary">IA responde no chat</span>
            </label>
          ` : ''}
        ` : ''}
      </div>
      ` : ''}
      ${(replyingTo && mode !== 'private') ? (() => {
        const q = quotedInfo(replyingTo);
        const accent = q ? q.senderColor : '#8696a0';
        return html`
          <div class="px-[14px] pt-[6px] bg-wa-panel shrink-0">
            <div class="flex items-stretch rounded-[6px] overflow-hidden" style="background:rgb(var(--wa-replyPreviewBg));">
              <div class="w-[4px] shrink-0" style="background:${accent};"></div>
              <div class="px-[10px] py-[5px] min-w-0 flex-1">
                <div class="text-[12.5px] font-semibold leading-[16px] truncate" style="color:${accent};">${q ? q.senderLabel : 'Mensagem'}</div>
                <div class="text-[13px] leading-[17px] text-wa-secondary truncate">${q ? q.snippet : ''}</div>
              </div>
              <button
                type="button"
                onClick=${() => setReplyingTo(null)}
                class="px-[12px] text-wa-secondary hover:text-wa-text shrink-0 text-[18px] leading-none"
                title="Cancelar resposta"
              >✕</button>
            </div>
          </div>
        `;
      })() : ''}
      ${sessionClosed ? html`
        <div class="px-[14px] py-[6px] bg-wa-panel shrink-0">
          <div class="text-[12px] text-wa-secondary bg-wa-bg border border-wa-border rounded-[6px] px-3 py-1.5">
            Fora da janela de 24h: só é possível enviar um ${' '}
            ${templatePickerAvailable() ? html`
              <button type="button" onClick=${openTemplatePicker} class="text-wa-teal underline font-medium">template aprovado</button>.
            ` : html`<span class="font-medium">template aprovado</span>.`}
          </div>
        </div>
      ` : ''}
      <form onSubmit=${composer.handleSend} class="flex items-center px-[10px] py-[5px] bg-wa-panel min-h-[62px] shrink-0">
        <div ref=${emojiRef} class="relative shrink-0">
          <button
            type="button"
            class="p-[8px] transition-colors ${emojiOpen ? 'text-wa-teal' : ''}"
            tabindex="-1"
            onClick=${() => setEmojiOpen(o => !o)}
            title="Emojis"
          >
            <${EmojiIcon} />
          </button>
          ${emojiOpen ? html`
            <div class="absolute bottom-[48px] left-0 z-30">
              <${EmojiPicker} onPick=${insertEmoji} />
            </div>
          ` : ''}
        </div>
        ${html`
          <div ref=${attachMenuRef} class="relative shrink-0">
            <button type="button" class="p-[8px] ${(sessionClosed && mode !== 'private') ? 'opacity-40 cursor-not-allowed' : ''}" tabindex="-1"
              disabled=${sessionClosed && mode !== 'private'} onClick=${handleAttachClick}
              title=${mode === 'private' ? 'Anexar à nota privada' : 'Anexar'}>
              <${AttachIcon} />
            </button>
            ${attachMenuOpen ? html`
              <div class="absolute bottom-[44px] left-0 bg-wa-panel border border-wa-border rounded-[8px] shadow-lg py-[4px] min-w-[160px] z-20">
                <button type="button" onClick=${pickImage}
                  class="w-full text-left px-[14px] py-[8px] text-[14px] text-wa-text hover:bg-wa-hover flex items-center gap-[8px]">
                  <span class="text-[16px]">🖼️</span> Imagem
                </button>
                <button type="button" onClick=${pickVideo}
                  class="w-full text-left px-[14px] py-[8px] text-[14px] text-wa-text hover:bg-wa-hover flex items-center gap-[8px]">
                  <span class="text-[16px]">🎬</span> Vídeo
                </button>
                <button type="button" onClick=${pickDocument}
                  class="w-full text-left px-[14px] py-[8px] text-[14px] text-wa-text hover:bg-wa-hover flex items-center gap-[8px]">
                  <span class="text-[16px]">📄</span> Documento
                </button>
              </div>
            ` : ''}
          </div>
        `}
        ${templatesSupported && templatePickerAvailable() && mode !== 'private' ? html`
          <button
            type="button"
            class="p-[8px] ${sessionClosed ? 'text-wa-teal' : ''}"
            tabindex="-1"
            onClick=${openTemplatePicker}
            title="Enviar template"
          >
            <${TemplateIcon} />
          </button>
        ` : ''}
        <div class="flex-1 mx-[5px] relative">
          ${mentionMenu ? (() => {
            const cands = getMentionCandidates(mentionMenu.query);
            if (!cands.length) return '';
            const sel = Math.min(mentionMenu.index || 0, cands.length - 1);
            return html`
              <div class="absolute left-0 right-0 bottom-[calc(100%+6px)] max-h-[210px] overflow-y-auto bg-wa-panel border border-wa-border rounded-[8px] shadow-lg py-[4px] z-30 wa-scrollbar">
                ${cands.map((c, i) => {
                  const isSpecial = c.special || c.team;
                  const dispName = c.name || mentionLabel(c);
                  const key = c.team ? '@time' : c.special ? '@todos'
                    : c.internal ? ('u' + c.user_id) : c.phone;
                  const avatar = isSpecial ? (c.team ? '👥' : '@') : ((dispName || '?').slice(0, 1).toUpperCase());
                  const fullLabel = c.team ? 'Time — todos da caixa'
                    : c.special ? 'todos — todos os membros' : dispName;
                  return html`
                  <button
                    type="button"
                    key=${key}
                    onMouseDown=${(ev) => { ev.preventDefault(); applyMention(c); }}
                    class="w-full text-left px-[12px] py-[7px] text-[14px] flex items-center gap-[8px] ${i === sel ? 'bg-wa-hover' : ''} hover:bg-wa-hover"
                  >
                    <span class="w-[26px] h-[26px] rounded-full flex items-center justify-center text-[12px] shrink-0 ${isSpecial ? 'bg-wa-teal text-white' : 'bg-wa-border text-wa-text'}">
                      ${avatar}
                    </span>
                    <span class="text-wa-text truncate">
                      ${fullLabel}
                      ${(!isSpecial && c.is_admin) ? html`<span class="ml-[6px] text-[11px] text-wa-secondary">admin</span>` : ''}
                    </span>
                  </button>
                `;
                })}
              </div>
            `;
          })() : ''}
          ${quickReplyMenu && canQuickReply ? (() => {
            const cands = getQuickReplyCandidates(quickReplyMenu.query);
            if (!cands.length) return '';
            const sel = Math.min(quickReplyMenu.index || 0, cands.length - 1);
            return html`
              <div class="absolute left-0 right-0 bottom-[calc(100%+6px)] max-h-[210px] overflow-y-auto bg-wa-panel border border-wa-border rounded-[8px] shadow-lg py-[4px] z-30 wa-scrollbar">
                ${cands.map((c, i) => html`
                  <button
                    type="button"
                    key=${c.id}
                    onMouseDown=${(ev) => { ev.preventDefault(); applyQuickReply(c); }}
                    class="w-full text-left px-[12px] py-[7px] text-[14px] flex items-start gap-[8px] ${i === sel ? 'bg-wa-hover' : ''} hover:bg-wa-hover"
                  >
                    <span class="text-wa-teal font-mono shrink-0">/${c.short_code}</span>
                    <span class="text-wa-secondary truncate">${(c.content || '').replace(/\s+/g, ' ').slice(0, 60)}</span>
                  </button>
                `)}
              </div>
            `;
          })() : ''}
          <div
            ref=${mirrorRef}
            aria-hidden="true"
            class="pointer-events-none absolute inset-0 z-0 overflow-hidden box-border rounded-[8px] px-[12px] py-[9px] border border-transparent text-[15px] leading-[20px] whitespace-pre-wrap break-words bg-wa-inputBg text-wa-text"
            dangerouslySetInnerHTML=${{ __html: highlightComposerMarkup(input) }}
          ></div>
          <textarea
            ref=${inputRef}
            rows="1"
            value=${input}
            onInput=${handleInputChange}
            onKeyDown=${handleKeyDown}
            onPaste=${handlePaste}
            onScroll=${(e) => syncMirror(e.target, mirrorRef.current)}
            placeholder=${hasPending
              ? (audioOnlyPending ? 'Mensagem (enviada antes do áudio)' : 'Adicionar uma legenda (opcional)')
              : mode === 'private' ? 'Mensagem privada' : 'Digite uma mensagem'}
            style="caret-color: rgb(var(--wa-text));"
            class="relative z-[1] box-border w-full block bg-transparent text-transparent text-[15px] rounded-[8px] px-[12px] py-[9px] border border-wa-border outline-none placeholder-wa-secondary resize-none max-h-[120px] wa-scrollbar leading-[20px]"
          ></textarea>
        </div>
        ${(hasText || hasPending) ? html`
          <!-- Sem "disabled" durante o lote: mandar uma mensagem de texto
               enquanto os anexos sobem sempre foi possível e continua sendo.
               Quem impede um segundo disparo da FILA é o submitPlan. -->
          <button
            type="submit"
            title=${hasPending ? (hasText ? 'Enviar anexo com a legenda' : 'Enviar anexo') : 'Enviar'}
            class="p-[8px] shrink-0 transition-colors"
            style="color: ${mode === 'private' ? '#a78bfa' : '#00a884'};"
          >
            <${SendIcon} />
          </button>
        ` : html`
          <button type="button"
            title=${mode === 'private' ? 'Gravar áudio privado (só no painel)' : 'Gravar áudio'}
            class="p-[8px] shrink-0 ${mode === 'private' ? 'text-violet-400' : 'text-wa-icon'} ${(sessionClosed && mode !== 'private') ? 'opacity-40 cursor-not-allowed' : ''}" tabindex="-1"
            disabled=${sessionClosed && mode !== 'private'} onClick=${handleMicClick}>
            <${MicIcon} />
          </button>
        `}
      </form>
    `}
  `;
}
