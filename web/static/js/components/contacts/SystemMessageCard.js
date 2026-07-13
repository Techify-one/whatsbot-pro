import { h } from 'preact';
import htm from 'htm';
import { formatBubbleTime } from './utils.js';
import { isSystemCardRole } from '../../services/messageView.js';
import { parseCta } from '../../services/systemCta.js';
import { AudioPlayer } from './AudioPlayer.js';
import { MediaContent } from './MediaContent.js';

const html = htm.bind(h);

// ── Panel-only system cards (centered, never sent to WhatsApp) ───────
//
// Data-driven by `m.role` (private_note / transcription / system_notice /
// tool_call / conversation_event / system / error). Extracted verbatim from the
// inline branches in ContactDetail.js.
//
// COLOR FIX (Plano 23 · D3): the three cards that used raw hex / crude Tailwind
// colors are now built from semantic `wa-*` classes so they stay legible in dark
// mode (light-mode appearance kept equivalent):
//   • system_notice  was #1b2e4e / #93c5fd / #1e40af  → bg-wa-bg + border-wa-border + text-wa-secondary
//   • system         was bg-gray-100 / border-gray-300 / text-gray-800 / text-gray-600 → wa-*
//   • error          was #fef2f2 / #dc2626 / #fecaca   → bg-wa-bg + border-wa-border + text-red-500
// The purple/amber accents (private_note / transcription / tool_call) are intent
// colors and stay inline — unchanged.
//
// `fmt` is the parent's WhatsApp-formatting fn (knows group member names).
// `openMsgMenu(e, message, isFromMe)` opens the private-note context menu.
export function SystemMessageCard({ message: m, index: i, fmt, openMsgMenu, showAgentName = true }) {
  const role = m.role;

  if (role === 'private_note') {
    const failed = m._status === 'failed';
    const pending = m._status === 'sending';
    return html`
      <div key=${m._localId || i} data-mid=${m._id} class="flex justify-center mt-[4px]">
        <div
          onContextMenu=${(e) => openMsgMenu(e, m, true)}
          class="group max-w-[75%] rounded-[7.5px] px-[11px] pt-[6px] pb-[7px] text-[13px] leading-[18px] whitespace-pre-wrap relative shadow-sm"
          style="background:#3b266b; color:#ede9fe; border:1px solid #7c3aed; ${failed ? 'opacity:0.7;' : ''}">
          <button
            onClick=${(e) => openMsgMenu(e, m, true)}
            class="absolute top-[2px] right-[2px] opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity rounded-full p-[1px] hover:bg-black/20"
            title="Opções da mensagem"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" style="color:#c4b5fd;">
              <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/>
            </svg>
          </button>
          <span class="flex items-center gap-[5px] text-[10.5px] font-semibold mb-[3px] tracking-wide uppercase" style="color:#c4b5fd;">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
            Mensagem privada${m.sent_by_name ? html`<span class="normal-case font-normal opacity-90"> · por ${m.sent_by_name}</span>` : ''}
          </span>
          ${(m.media_type === 'audio' && m.media_path) ? html`
            <div class="min-w-[220px] max-w-[280px] my-[2px]">
              <${AudioPlayer} src=${m.media_path} isLocalBlob=${m._isLocalBlob} />
            </div>
          ` : (m.media_type === 'image' || m.media_type === 'document' || m.media_type === 'video') ? html`
            <${MediaContent} message=${m} displayContent=${m.content} fmt=${fmt} />
          ` : html`<span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>`}
          <span class="float-right ml-[8px] mt-[3px] text-[10.5px] leading-[14px] whitespace-nowrap" style="color:#a78bfa;">
            ${pending ? '⏳ ' : (failed ? '⚠ ' : '')}${formatBubbleTime(m.ts)}
          </span>
        </div>
      </div>
    `;
  }

  if (role === 'transcription') {
    return html`
      <div key=${i} data-mid=${m._id} class="flex justify-center mt-[4px]">
        <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
             style="background: #2d1b4e; color: #d4bfff; border: 1px solid #4a2d7a;">
          <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1s3.1 1.39 3.1 3.1v2z"/></svg>
            Transcrição privada
          </span>
          <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
          <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
            ${formatBubbleTime(m.ts)}
          </span>
        </div>
      </div>
    `;
  }

  if (role === 'system_notice') {
    // COLOR FIX: was inline #1b2e4e / #93c5fd / #1e40af → semantic wa-*.
    return html`
      <div key=${i} class="flex justify-center mt-[4px]">
        <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative bg-wa-bg border border-wa-border text-wa-secondary">
          <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            Mensagem do Sistema
          </span>
          <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
          <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
            ${formatBubbleTime(m.ts)}
          </span>
        </div>
      </div>
    `;
  }

  if (role === 'tool_call') {
    return html`
      <div key=${i} class="flex justify-center mt-[4px]">
        <div class="max-w-[75%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative"
             style="background: #2d1b0e; color: #fbbf24; border: 1px solid #78350f;">
          <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"/></svg>
            ${(showAgentName && m.agent_name) ? `Ferramenta IA - ${m.agent_name}` : 'Ferramenta IA'}
          </span>
          <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
          <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
            ${formatBubbleTime(m.ts)}
          </span>
        </div>
      </div>
    `;
  }

  if (role === 'conversation_event') {
    // Lifecycle event (plano 12): centered subtle chip, like WhatsApp's
    // system lines. Content already carries the emoji + PT-BR text.
    // wa-* classes keep it legible in both light and dark themes.
    return html`
      <div key=${i} data-mid=${m._id} class="flex justify-center my-[5px]">
        <div class="max-w-[80%] rounded-[10px] px-[12px] py-[5px] bg-wa-bg/80 border border-wa-border text-wa-secondary text-[12px] leading-[16px] text-center whitespace-pre-wrap shadow-sm">
          <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
          <span class="ml-[6px] text-[10px] opacity-70 whitespace-nowrap">${formatBubbleTime(m.ts)}</span>
        </div>
      </div>
    `;
  }

  if (role === 'system') {
    // Painel-only "Sistema" card (ex.: análise de melhoria da IA).
    // COLOR FIX: was bg-gray-100 / border-gray-300 / text-gray-800 / text-gray-600
    // (relied on html.dark overrides) → durable semantic wa-*.
    //
    // Botão de ação opcional: o produtor da mensagem (core OU plugin) pode anexar
    // um call-to-action codificado no conteúdo como ``[[cta:RÓTULO|URL]]``. O card
    // remove o token do texto e renderiza um botão no lugar de um link inline
    // (ex.: o plugin melhorias → "Ir para sugestão"). Genérico: o rótulo vem do
    // produtor; só http(s)/caminho-interno é aceito como destino (guarda de XSS).
    const cta = parseCta(m.content || '');
    return html`
      <div key=${i} data-mid=${m._id} class="flex justify-center mt-[4px]">
        <div class="max-w-[80%] rounded-[10px] px-[12px] pt-[7px] pb-[8px] bg-wa-bg border border-wa-border text-wa-text text-[13px] leading-[19px] whitespace-pre-wrap relative shadow-sm">
          <span class="flex items-center gap-[5px] text-[10.5px] font-semibold mb-[3px] tracking-wide uppercase text-wa-secondary">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            Sistema
          </span>
          <span dangerouslySetInnerHTML=${{ __html: fmt(cta.text)}}></span>
          ${cta.action ? html`
            <div class="mt-[7px]">
              <a href=${cta.action.url} target="_blank" rel="noopener noreferrer"
                 class="inline-flex items-center gap-[6px] px-[12px] py-[6px] rounded-lg bg-wa-teal text-white text-[12.5px] font-semibold no-underline hover:opacity-90 transition-opacity">
                ${cta.action.label}
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
              </a>
            </div>
          ` : null}
          <span class="block text-right mt-[3px] text-[10.5px] leading-[14px] whitespace-nowrap text-wa-secondary opacity-70">
            ${formatBubbleTime(m.ts)}
          </span>
        </div>
      </div>
    `;
  }

  if (role === 'error') {
    // COLOR FIX: was inline #fef2f2 / #dc2626 / #fecaca → semantic wa-* + red text.
    return html`
      <div key=${i} class="flex justify-center mt-[4px]">
        <div class="max-w-[85%] rounded-[7.5px] px-[10px] pt-[5px] pb-[6px] text-[12.5px] leading-[17px] whitespace-pre-wrap relative bg-wa-bg border border-wa-border text-red-500">
          <span class="flex items-center gap-1 text-[10px] font-semibold mb-[2px] opacity-80">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            Erro no envio
          </span>
          <span dangerouslySetInnerHTML=${{ __html: fmt(m.content)}}></span>
          <span class="float-right ml-[8px] mt-[2px] text-[10px] leading-[14px] whitespace-nowrap opacity-60">
            ${formatBubbleTime(m.ts)}
          </span>
        </div>
      </div>
    `;
  }

  return null;
}

export { isSystemCardRole };
