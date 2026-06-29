import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { AudioPlayer } from './AudioPlayer.js';

const html = htm.bind(h);

// Renders an <img>/<video> for a message's media and, if the file fails to load
// (e.g. the server lost the file under statics/ — wiped on a deploy without a
// persistent volume), swaps to a neutral "indisponível" placeholder instead of
// the broken-image icon. Local blobs (optimistic, just-sent) never fall back.
export function MediaWithFallback({ kind, src, isLocalBlob, alt, className, style, onClick }) {
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
export function MediaContent({ message, displayContent, fmt }) {
  const m = message;
  if (m.media_type === 'image') {
    return html`
      <${MediaWithFallback} kind="image"
        src=${m.media_path} isLocalBlob=${m._isLocalBlob} alt="Imagem"
        className="rounded-[4px] max-w-full max-h-[300px] mb-1 cursor-pointer"
        style="min-width:120px"
        onClick=${() => window.open(m._isLocalBlob ? m.media_path : '/' + m.media_path, '_blank')} />
      ${displayContent && displayContent !== '[Imagem enviada pelo contato]' && !displayContent.startsWith('[Descrição da imagem]')
        ? html`<span dangerouslySetInnerHTML=${{ __html: fmt(displayContent)}}></span>`
        : null}
    `;
  }
  if (m.media_type === 'audio') {
    return html`
      <${AudioPlayer} src=${m.media_path} isLocalBlob=${m._isLocalBlob} />
      ${displayContent && displayContent !== '[Áudio recebido]' && displayContent !== '[Áudio]' && !displayContent.startsWith('[Transcrição do áudio]')
        ? html`<span class="block text-[12px] text-wa-secondary italic" dangerouslySetInnerHTML=${{ __html: fmt(displayContent)}}></span>`
        : null}
    `;
  }
  if (m.media_type === 'video') {
    return html`
      <${MediaWithFallback} kind="video"
        src=${m.media_path} isLocalBlob=${m._isLocalBlob}
        className="rounded-[4px] max-w-full max-h-[320px] mb-1"
        style="min-width:180px" />
      ${displayContent && !displayContent.startsWith('[Vídeo')
        ? html`<span dangerouslySetInnerHTML=${{ __html: fmt(displayContent)}}></span>`
        : null}
    `;
  }
  if (m.media_type === 'sticker') {
    return html`
      <${MediaWithFallback} kind="sticker"
        src=${m.media_path} isLocalBlob=${m._isLocalBlob} alt="Sticker"
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
    const docUrl = m._isLocalBlob ? m.media_path : '/' + m.media_path;
    // content = "[Documento recebido: nome.ext]" + opcional "\nlegenda"
    const dc = displayContent || '';
    const mm = dc.match(/^\[Documento (?:recebido|enviado): ([^\]]+)\]\n?([\s\S]*)$/);
    const docName = mm ? mm[1] : 'Documento';
    const docCaption = (mm ? mm[2] : dc).trim();
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
