import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { AudioPlayer } from './AudioPlayer.js';
import { authHeaders, handleUnauthorized } from '../../services/httpClient.js';
// plano 87: fonte única de "o que o cliente escreveu junto da mídia". Antes cada
// ramo abaixo adivinhava isso com um `startsWith('[…]')` próprio — e errava nos
// dois sentidos (escondia a legenda da imagem, mostrava a extração do documento).
import { mediaCaptionOf } from '../../services/messageView.js';

const html = htm.bind(h);

function useAuthorizedMedia(message) {
  const [state, setState] = useState({ url: null, failed: false });
  const local = Boolean(message && message._isLocalBlob);
  // Core/legacy payloads expose the DB primary key as `_id`; the v1 DTO uses
  // `id`. Both identify the same authorized media endpoint.
  const messageId = message && (message.id ?? message._id);
  const raw = message && message.media_path;

  useEffect(() => {
    if (local) {
      setState({ url: raw, failed: false });
      return undefined;
    }
    if (!messageId || !raw) {
      setState({ url: null, failed: Boolean(raw) });
      return undefined;
    }
    const controller = new AbortController();
    let objectUrl = null;
    let disposed = false;
    setState({ url: null, failed: false });
    fetch(`/api/messages/${messageId}/media`, {
      headers: authHeaders(), signal: controller.signal,
    }).then(async (response) => {
      if (response.status === 401) handleUnauthorized();
      if (!response.ok) throw new Error(`media ${response.status}`);
      objectUrl = URL.createObjectURL(await response.blob());
      if (disposed) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
        return;
      }
      setState({ url: objectUrl, failed: false });
    }).catch((error) => {
      if (error && error.name !== 'AbortError') setState({ url: null, failed: true });
    });
    return () => {
      disposed = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [local, messageId, raw]);

  return state;
}

// Renders an <img>/<video> for a message's media and, if the file fails to load
// (e.g. the server lost the file under statics/ — wiped on a deploy without a
// persistent volume), swaps to a neutral "indisponível" placeholder instead of
// the broken-image icon. Local blobs (optimistic, just-sent) never fall back.
function MediaWithFallback({ kind, src, isLocalBlob, alt, className, style, onClick }) {
  const [failed, setFailed] = useState(false);
  const url = isLocalBlob ? src : '/' + src;
  if (failed && !isLocalBlob) {
    const label = kind === 'video' ? 'Vídeo indisponível'
      : kind === 'sticker' ? 'Figurinha indisponível' : 'Imagem indisponível';
    return html`
      <div class="flex items-center gap-2 rounded-[4px] mb-1 px-3 py-4 bg-wa-hover text-wa-secondary text-[13px]"
           style="min-width:140px" title="O arquivo de mídia não está mais disponível no servidor.">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
          <circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>
        </svg>
        <span>${label}</span>
      </div>`;
  }
  if (kind === 'video') {
    return html`<video controls preload="metadata" src=${url} class=${className}
      style=${style} onError=${() => setFailed(true)}></video>`;
  }
  return html`<img src=${url} alt=${alt} class=${className} style=${style}
    onClick=${onClick} loading="lazy" onError=${() => setFailed(true)} />`;
}

// Render a message's media body (image / audio / video / sticker / location /
// document). `fmt` is the WhatsApp-formatting function from the parent (it knows
// the group member names for @mention highlighting). Returns null/empty when the
// message carries no recognized media (the bubble then renders plain text).
//
// Behavior-preserving extraction of the inline media branches in the bubble.
// `selectionMode` chega só para o player de áudio (plano 138 · P3): em modo
// seleção a linha inteira é o alvo do clique ([MessageBubble.js:62]), e um
// scrubber vivo criaria uma faixa de 20px onde clicar não marca a mensagem.
export function MediaContent({ message, displayContent, fmt, selectionMode = false }) {
  const m = message;
  const protectedKind = ['image', 'audio', 'video', 'sticker', 'document'].includes(m.media_type);
  const media = useAuthorizedMedia(protectedKind ? m : null);
  const mediaSrc = m._isLocalBlob ? m.media_path : media.url;
  if (protectedKind && !m._isLocalBlob && media.failed) {
    return html`
      <div class="flex items-center gap-2 rounded-[4px] mb-1 px-3 py-4 bg-wa-hover text-wa-secondary text-[13px]"
           title="O arquivo de mídia não está disponível ou você não tem mais acesso.">
        <span>Mídia indisponível</span>
      </div>`;
  }
  // A legenda do cliente (coluna `media_caption`; linha legada cai no content
  // com os guards conservadores de `mediaCaptionOf`).
  const caption = mediaCaptionOf(m, displayContent);
  if (m.media_type === 'image') {
    return html`
      <${MediaWithFallback} kind="image"
        src=${mediaSrc} isLocalBlob=${true} alt="Imagem"
        className="rounded-[4px] max-w-full max-h-[300px] mb-1 cursor-pointer"
        style="min-width:120px"
        onClick=${() => mediaSrc && window.open(mediaSrc, '_blank')} />
      ${caption
        ? html`<span dangerouslySetInnerHTML=${{ __html: fmt(caption)}}></span>`
        : null}
    `;
  }
  if (m.media_type === 'audio') {
    return html`
      <${AudioPlayer} src=${mediaSrc || ''} isLocalBlob=${true} disabled=${selectionMode} />
      ${caption
        ? html`<span class="block text-[12px] text-wa-secondary italic" dangerouslySetInnerHTML=${{ __html: fmt(caption)}}></span>`
        : null}
    `;
  }
  if (m.media_type === 'video') {
    return html`
      <${MediaWithFallback} kind="video"
        src=${mediaSrc} isLocalBlob=${true}
        className="rounded-[4px] max-w-full max-h-[320px] mb-1"
        style="min-width:180px" />
      ${caption && !caption.startsWith('[Vídeo')
        ? html`<span dangerouslySetInnerHTML=${{ __html: fmt(caption)}}></span>`
        : null}
    `;
  }
  if (m.media_type === 'sticker') {
    return html`
      <${MediaWithFallback} kind="sticker"
        src=${mediaSrc} isLocalBlob=${true} alt="Sticker"
        className="max-w-[160px] max-h-[160px] mb-1" />
    `;
  }
  if (m.media_type === 'location' || m.media_type === 'live_location') {
    // media_path here is "geo:lat,lng" (see _extract_media)
    const m_path = m.media_path || '';
    const coords = m_path.startsWith('geo:') ? m_path.slice(4) : '';
    const mapsUrl = coords
      ? `https://www.google.com/maps?q=${encodeURIComponent(coords)}`
      : null;
    return html`
      <div class="flex flex-col gap-1">
        <a
          href=${mapsUrl || '#'}
          target="_blank"
          rel="noopener noreferrer"
          class="text-wa-teal text-[13px] underline"
        >📍 ${displayContent || coords || 'Localização'}</a>
      </div>
    `;
  }
  if (m.media_type === 'document') {
    const docUrl = mediaSrc || '#';
    // O NOME do arquivo continua saindo do rótulo "[Documento recebido: x.pdf]"
    // que GOWA/sandbox compõem no content; provider que não o componha (Cloud)
    // cai em "Documento", como antes.
    const dc = displayContent || '';
    const mm = dc.match(/^\[Documento (?:recebido|enviado): ([^\]]+)\]\n?([\s\S]*)$/);
    const docName = mm ? mm[1] : 'Documento';
    // A LEGENDA nunca mais sai do content CRU (plano 87) — era daqui que vazava
    // a extração da IA ("[Conteúdo do documento]: <dump do PDF>" desenhado como
    // se fosse texto do cliente, expondo comprovante bancário e afins).
    // Precedência: coluna do plano 87 → `mediaCaptionOf` sobre o que sobra.
    // ⚠️ O fallback roda MESMO quando o rótulo não casa (`mm` null). Só GOWA,
    // sandbox e o envio do operador compõem "[Documento …]"; Cloud/Telegram/Meta
    // gravam a legenda pura. Devolver '' nesse ramo apagaria a legenda de toda
    // linha legada desses canais — o próprio bug do plano 87, ao contrário.
    // `mediaCaptionOf` já é seguro aqui: devolve '' se o texto COMEÇA com o
    // prefixo da IA e corta em "\n<prefixo>" caso contrário.
    const docCaption = (m.media_caption || '').trim()
      || mediaCaptionOf({ content: mm ? mm[2] : dc });
    return html`
      <div class="flex flex-col gap-1">
        <a
          href=${docUrl}
          target="_blank"
          rel="noopener noreferrer"
          class="flex items-center gap-1 text-wa-teal text-[13px] underline break-all"
        >📄 ${docName}</a>
        ${docCaption
          ? html`<span dangerouslySetInnerHTML=${{ __html: fmt(docCaption)}}></span>`
          : null}
      </div>
    `;
  }
  return html`<span dangerouslySetInnerHTML=${{ __html: fmt(displayContent)}}></span>`;
}
