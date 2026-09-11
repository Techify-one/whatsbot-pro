import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import htm from 'htm';
import { AudioPlayer } from './AudioPlayer.js';
import { captionTargetIndex } from '../../services/composerSubmit.js';

const html = htm.bind(h);

// Bandeja de anexos pendentes (plano 124 — era `MediaQueuePreview`, do plano 64).
//
// A diferença não é cosmética: antes isto era uma TELA DE CONFIRMAÇÃO que
// SUBSTITUÍA a barra de entrada inteira, com campo de legenda e botão Enviar
// próprios. O efeito colateral era o bug relatado — o texto já digitado sumia da
// tela e, sem a `<textarea>` montada, o segundo Ctrl+V não tinha onde cair.
//
// Agora é uma FAIXA que convive com o compositor (modelo Telegram/WhatsApp Web):
//   • sem campo de legenda — a legenda é o texto do compositor;
//   • sem botão Enviar — quem envia é o botão do compositor;
//   • sem toggles de IA — eles já existem na barra, que agora fica visível.
//
// Sobra o que é de fato desta faixa: ver o que está na fila, tirar um item e
// limpar tudo.
//
// ⚠️ Plano 124 · F10 — TAMANHO. A primeira versão da bandeja renderizava sempre
// a tira de miniaturas, e com isso perdeu os dois ramos que a tela de
// confirmação do plano 64 tinha: um anexo único de imagem virava um selo de
// 60px com `object-cover`, que RECORTA. Num print de tela — o caso mais comum —
// o operador via um pedaço do canto e não conseguia conferir o que ia mandar.
// O item único de imagem/vídeo volta a ser uma prévia grande com
// `object-contain`; o lote volta às miniaturas de 84px do plano 64.
//
// A diferença em relação a produção é que a prévia agora SOMA com a barra de
// entrada (ela não a substitui mais) e ainda empilha com o aviso de 24h, a
// citação e a faixa de progresso. Daí o teto por viewport em `PREVIEW_MAX_H`:
// em tela alta fica idêntico ao plano 64, num laptop a conversa não é espremida.
// Inline de propósito — o Tailwind aqui é o runtime vendorizado, e valor
// arbitrário com função aninhada (`max-h-[min(200px,28vh)]`) é justamente o
// tipo de coisa que falha em silêncio.
const PREVIEW_MAX_H = 'max-height: min(200px, 28vh)';

function extensionOf(name) {
  const i = (name || '').lastIndexOf('.');
  return i > 0 ? name.slice(i + 1).toUpperCase().slice(0, 5) : 'ARQ';
}

/** Botão de remover — o mesmo nos dois modos de render (miniatura e prévia). */
function RemoveButton({ id, onRemove, extraClass = '' }) {
  return html`
    <button
      type="button"
      onClick=${() => onRemove(id)}
      title="Remover"
      class="absolute -top-[5px] -right-[5px] w-[18px] h-[18px] rounded-full bg-wa-panel border border-wa-border
             text-wa-secondary hover:text-wa-text text-[11px] leading-none flex items-center justify-center shadow ${extraClass}"
    >✕</button>
  `;
}

/**
 * Prévia GRANDE de um anexo único de imagem/vídeo (F10) — o operador precisa
 * conferir o arquivo inteiro, não uma amostra recortada dele.
 */
function SinglePreview({ item, onRemove, disabled }) {
  const media = item.kind === 'video'
    ? html`
        <video src=${item.previewUrl} class="block max-w-full rounded-[8px] object-contain"
               style=${PREVIEW_MAX_H} muted preload="metadata" playsinline></video>
        <span class="pointer-events-none absolute inset-0 flex items-center justify-center
                     text-white text-[34px] drop-shadow">▶</span>
      `
    : html`<img src=${item.previewUrl} alt=${item.filename}
             class="block max-w-full rounded-[8px] object-contain" style=${PREVIEW_MAX_H} />`;

  return html`
    <div class="flex justify-center">
      <div class="relative inline-block max-w-full" title=${item.filename}>
        ${media}
        ${!disabled ? html`<${RemoveButton} id=${item.id} onRemove=${onRemove} />` : ''}
      </div>
    </div>
  `;
}

function Thumb({ item, onRemove, disabled, badge = '' }) {
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
          <span class="text-[22px] leading-none">📄</span>
          <span class="text-[9px] font-semibold text-wa-secondary">${extensionOf(item.filename)}</span>
        </div>
      `;

  return html`
    <div class="relative w-[84px] shrink-0" title=${item.filename}>
      <div class="relative w-[84px] h-[84px] rounded-[8px] overflow-hidden bg-wa-inputBg border border-wa-border">
        ${body}
        ${badge ? html`
          <span class="absolute bottom-0 inset-x-0 bg-black/55 text-white text-[9px] leading-[13px] text-center">
            ${badge}
          </span>
        ` : ''}
      </div>
      <div class="mt-[2px] text-[9px] text-wa-secondary truncate leading-[12px]">${item.filename}</div>
      ${!disabled ? html`<${RemoveButton} id=${item.id} onRemove=${onRemove} />` : ''}
    </div>
  `;
}

/**
 * Rótulo do cabeçalho (F9) — puro, fora do render para a regra caber num lugar
 * só (este módulo importa preact, então não há como cobri-lo com `node --test`;
 * a rede aqui é o roteiro manual).
 *
 * Com anexo na bandeja o placeholder do compositor ("Adicionar uma legenda")
 * some assim que o operador digita a primeira letra, que é exatamente quando a
 * dúvida "isso vai junto ou separado?" aparece. O rótulo da bandeja passa a
 * responder isso o tempo todo — e a anunciar a exceção do áudio ANTES do envio,
 * não depois.
 *
 * @param {{total:number, hasText:boolean, isAudioOnly:boolean}} s
 */
export function trayLabel({ total, hasText, isAudioOnly }) {
  if (isAudioOnly) {
    return hasText ? 'O texto vai como mensagem separada, antes do áudio' : 'Áudio gravado';
  }
  if (hasText) {
    return total > 1 ? 'O texto abaixo vai como legenda do último arquivo' : 'O texto abaixo vai como legenda';
  }
  return total > 1 ? `${total} arquivos anexados` : '1 arquivo anexado';
}

/**
 * @param {Object} props
 * @param {Array<any>} props.queue
 * @param {(id:string)=>void} props.onRemove
 * @param {()=>void} props.onCancel
 * @param {boolean} props.sending
 * @param {boolean} props.escClears - `Esc` pode limpar o lote agora?
 * @param {boolean} [props.hasText] - há texto no compositor? (vira legenda)
 */
export function MediaTray({ queue, onRemove, onCancel, sending, escClears = true, hasText = false }) {
  // `Esc` limpa a bandeja — mas SÓ quando o compositor está vazio (o chamador
  // decide via `escClears`). Com a barra de entrada viva, `Esc` virou um reflexo
  // de "fechar o que está aberto": apagar 5 anexos no meio de uma frase, sem
  // confirmação e sem desfazer, seria destruição acidental.
  useEffect(() => {
    if (!escClears || sending) return;
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel, sending, escClears]);

  if (!queue.length) return null;

  const isAudioOnly = queue.every(i => i.kind === 'audio');
  const total = queue.length;
  // Item único de imagem/vídeo ganha a prévia GRANDE (F10). Documento sozinho
  // não: não há o que conferir visualmente num ícone de PDF ampliado.
  const single = (total === 1 && queue[0].previewUrl
    && (queue[0].kind === 'image' || queue[0].kind === 'video')) ? queue[0] : null;
  // Mesma regra pura do envio: o selo "legenda" tem de cair no MESMO item que
  // vai de fato levá-la, senão a bandeja promete uma coisa e o lote faz outra.
  const captionIndex = hasText ? captionTargetIndex(queue) : -1;

  return html`
    <div class="bg-wa-panel border-t border-wa-border px-[14px] py-[8px] shrink-0">
      <div class="flex items-center justify-between gap-[10px] mb-[6px]">
        <span class="text-[11px] text-wa-secondary">
          ${trayLabel({ total, hasText, isAudioOnly })}
        </span>
        ${!sending ? html`
          <button
            type="button"
            onClick=${onCancel}
            class="text-[11px] text-wa-secondary hover:text-wa-text underline shrink-0"
          >Limpar</button>
        ` : ''}
      </div>

      ${isAudioOnly ? html`
        <div class="w-full max-w-[320px]">
          <${AudioPlayer} src=${queue[0].previewUrl} isLocalBlob=${true} />
        </div>
      ` : single ? html`
        <${SinglePreview} item=${single} onRemove=${onRemove} disabled=${sending} />
      ` : html`
        <div class="flex gap-[8px] overflow-x-auto wa-scrollbar pb-[2px]">
          ${queue.map((item, i) => html`
            <${Thumb} key=${item.id} item=${item} onRemove=${onRemove} disabled=${sending}
              badge=${i === captionIndex ? 'legenda' : ''} />
          `)}
        </div>
      `}
    </div>
  `;
}
