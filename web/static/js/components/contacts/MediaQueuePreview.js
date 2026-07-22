import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import htm from 'htm';
import { SendIcon } from './icons.js';
import { AudioPlayer } from './AudioPlayer.js';

const html = htm.bind(h);

// Prévia da fila de mídia antes de enviar (plano 64 · F7).
//
// Substitui o overlay de confirmação de UM item: agora é uma grade de
// miniaturas com remoção individual, legenda compartilhada (P3) e contagem no
// botão. `Esc` cancela o lote inteiro.
//
// Áudio continua com o player inteiro e SEM legenda (`/send/audio` do GOWA é
// nota de voz e não aceita caption).

function extensionOf(name) {
  const i = (name || '').lastIndexOf('.');
  return i > 0 ? name.slice(i + 1).toUpperCase().slice(0, 5) : 'ARQ';
}

function Thumb({ item, onRemove, disabled }) {
  const body = item.kind === 'image' && item.previewUrl
    ? html`<img src=${item.previewUrl} alt=${item.filename}
             class="w-full h-full object-cover" />`
    : item.kind === 'video' && item.previewUrl
    ? html`
        <video src=${item.previewUrl} class="w-full h-full object-cover" muted preload="metadata"></video>
        <span class="absolute inset-0 flex items-center justify-center text-white text-[20px] drop-shadow">▶</span>
      `
    : html`
        <div class="w-full h-full flex flex-col items-center justify-center gap-[2px] px-[4px]">
          <span class="text-[20px] leading-none">📄</span>
          <span class="text-[9px] font-semibold text-wa-secondary">${extensionOf(item.filename)}</span>
        </div>
      `;

  return html`
    <div class="relative w-[84px] shrink-0" title=${item.filename}>
      <div class="relative w-[84px] h-[84px] rounded-[8px] overflow-hidden bg-wa-inputBg border border-wa-border">
        ${body}
      </div>
      <div class="mt-[3px] text-[10px] text-wa-secondary truncate leading-[13px]">${item.filename}</div>
      ${!disabled ? html`
        <button
          type="button"
          onClick=${() => onRemove(item.id)}
          title="Remover"
          class="absolute -top-[6px] -right-[6px] w-[20px] h-[20px] rounded-full bg-wa-panel border border-wa-border
                 text-wa-secondary hover:text-wa-text text-[12px] leading-none flex items-center justify-center shadow"
        >✕</button>
      ` : ''}
    </div>
  `;
}

export function MediaQueuePreview({
  queue, caption, setCaption, onRemove, onCancel, onConfirm,
  sending, progressLabel, mode,
  aiReadPrivate, setAiReadPrivate, aiReplyInChat, setAiReplyInChat,
}) {
  // Esc cancela o lote inteiro (e libera os objectURLs, via onCancel).
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !sending) { e.preventDefault(); onCancel(); }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel, sending]);

  if (!queue.length) return null;

  const isAudioOnly = queue.every(i => i.kind === 'audio');
  const total = queue.length;

  return html`
    <div class="flex flex-col items-center bg-wa-panel border-t border-wa-border px-[16px] py-[12px] shrink-0 gap-[10px]">
      ${total > 1 ? html`
        <div class="w-full max-w-[560px] text-[12px] text-wa-secondary">
          ${total} arquivos selecionados
        </div>
      ` : ''}

      ${isAudioOnly ? html`
        <div class="w-full max-w-[320px]">
          <${AudioPlayer} src=${queue[0].previewUrl} isLocalBlob=${true} />
        </div>
      ` : total === 1 && queue[0].kind === 'image' && queue[0].previewUrl ? html`
        <!-- Item único de imagem mantém a prévia grande de sempre. -->
        <img src=${queue[0].previewUrl}
             class="max-h-[200px] max-w-full rounded-[8px] object-contain" />
      ` : html`
        <div class="w-full max-w-[560px] flex gap-[10px] overflow-x-auto wa-scrollbar pb-[2px]">
          ${queue.map(item => html`
            <${Thumb} key=${item.id} item=${item} onRemove=${onRemove} disabled=${sending} />
          `)}
        </div>
      `}

      ${!isAudioOnly ? html`
        <input
          type="text"
          class="wa-field w-full max-w-[420px] rounded-[8px] px-[12px] py-[8px] text-[14px] border border-wa-border"
          placeholder=${total > 1 ? 'Adicionar uma legenda (vai no primeiro arquivo)' : 'Adicionar uma legenda (opcional)'}
          value=${caption}
          disabled=${sending}
          onInput=${(e) => setCaption(e.target.value)}
          onKeyDown=${(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onConfirm(); } }}
        />
      ` : ''}

      ${(isAudioOnly && mode === 'private') ? html`
        <div class="flex items-center gap-[16px] flex-wrap justify-center">
          <label class="inline-flex items-center gap-[6px] cursor-pointer select-none" title="Quando ligado, a IA processa o áudio como instrução.">
            <input type="checkbox" class="sr-only peer" checked=${aiReadPrivate}
              onChange=${e => setAiReadPrivate(e.target.checked)} />
            <div class="relative w-[28px] h-[16px] bg-gray-500 rounded-full peer-checked:bg-violet-500 transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-[12px] after:w-[12px] after:transition-transform peer-checked:after:translate-x-[12px]"></div>
            <span class="text-[13px] text-wa-secondary">IA lê</span>
          </label>
          ${aiReadPrivate ? html`
            <label class="inline-flex items-center gap-[6px] cursor-pointer select-none" title="Quando ligado, a IA responde no chat do contato. Quando desligado, a resposta fica apenas como nota privada.">
              <input type="checkbox" class="sr-only peer" checked=${aiReplyInChat}
                onChange=${e => setAiReplyInChat(e.target.checked)} />
              <div class="relative w-[28px] h-[16px] bg-gray-500 rounded-full peer-checked:bg-violet-500 transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-[12px] after:w-[12px] after:transition-transform peer-checked:after:translate-x-[12px]"></div>
              <span class="text-[13px] text-wa-secondary">IA responde no chat</span>
            </label>
          ` : ''}
        </div>
      ` : ''}

      <div class="flex items-center gap-[12px]">
        ${sending && progressLabel ? html`
          <span class="text-[12px] text-wa-secondary">${progressLabel}</span>
        ` : ''}
        <button
          type="button"
          onClick=${onCancel}
          disabled=${sending}
          class="px-[16px] py-[6px] rounded-[8px] text-[13px] bg-wa-hover text-wa-text border border-wa-border hover:bg-wa-inputBg transition-colors disabled:opacity-50"
        >Cancelar</button>
        <button
          type="button"
          onClick=${onConfirm}
          disabled=${sending}
          class="px-[16px] py-[6px] rounded-[8px] text-[13px] bg-wa-outgoing text-wa-text border border-wa-border hover:opacity-90 transition-colors disabled:opacity-50 flex items-center gap-[6px]"
        ><${SendIcon} /> ${total > 1 ? `Enviar (${total})` : 'Enviar'}</button>
      </div>
    </div>
  `;
}
