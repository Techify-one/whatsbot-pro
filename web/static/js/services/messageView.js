// @ts-check
//
// Pure presentation helpers for rendering chat messages (Plano 23 · D3).
//
// Two concerns extracted from ContactDetail.js so they can be unit-tested and
// drive the data-driven SystemMessageCard:
//   1) SYSTEM_CARD_VARIANTS — per panel-only role, the label/icon/styling for
//      the centered card. `private_note`/`transcription`/`tool_call` keep their
//      original purple/amber inline colors (theme-specific accents, fine as-is);
//      `system_notice`/`system`/`error` move to semantic `wa-*` classes so dark
//      mode is legible (light-mode appearance kept equivalent).
//   2) quotedSnippet — the {senderLabel, senderColor, snippet, fromMe} for a
//      reply quote, mirroring the bubble's own sender/side logic.
//
// PURE: no DOM, no network, no module state. Components read these and render.

/**
 * Roles that render as a painel-only centered card (never sent to WhatsApp).
 * @type {Record<string, {
 *   label: string,
 *   icon: 'lock'|'info'|'tool',
 *   layout: 'inline'|'chip'|'block',
 *   uppercaseLabel?: boolean,
 *   showTime?: boolean,
 *   useWaClasses?: boolean,
 *   wrapClass?: string,
 *   cardClass?: string,
 *   labelClass?: string,
 *   timeClass?: string,
 *   style?: string,
 *   labelStyle?: string,
 *   timeStyle?: string,
 * }>}
 */
export const SYSTEM_CARD_VARIANTS = {
  // Operator private note — purple accent (kept; theme-specific intent color).
  private_note: {
    label: 'Mensagem privada', icon: 'lock', layout: 'inline',
    uppercaseLabel: true, showTime: true, useWaClasses: false,
  },
  // Private audio/image transcription — muted purple (kept).
  transcription: {
    label: 'Transcrição privada', icon: 'lock', layout: 'inline',
    showTime: true, useWaClasses: false,
    style: 'background: #2d1b4e; color: #d4bfff; border: 1px solid #4a2d7a;',
  },
  // AI tool-call trace — amber (kept; matches the "Ferramenta IA" intent).
  tool_call: {
    label: 'Ferramenta IA', icon: 'tool', layout: 'inline',
    showTime: true, useWaClasses: false,
    style: 'background: #2d1b0e; color: #fbbf24; border: 1px solid #78350f;',
  },
  // System notice — was raw #1b2e4e/#93c5fd; now semantic wa-* (dark-mode safe).
  system_notice: {
    label: 'Mensagem do Sistema', icon: 'info', layout: 'inline',
    showTime: true, useWaClasses: true,
    cardClass: 'bg-wa-bg border border-wa-border text-wa-secondary',
  },
  // Lifecycle event (plano 12) — already wa-* based; rendered as a subtle chip.
  conversation_event: {
    label: '', icon: 'info', layout: 'chip', showTime: true, useWaClasses: true,
  },
  // "Sistema" block card (e.g. AI improvement analysis) — was bg-gray-100/etc;
  // those grays have html.dark overrides today, but wa-* is the durable choice.
  system: {
    label: 'Sistema', icon: 'info', layout: 'block', uppercaseLabel: true,
    showTime: true, useWaClasses: true,
    cardClass: 'bg-wa-bg border border-wa-border text-wa-text',
    labelClass: 'text-wa-secondary', timeClass: 'text-wa-secondary',
  },
  // Send error — was raw #fef2f2/#dc2626/#fecaca; now semantic wa-* + red text.
  error: {
    label: 'Erro no envio', icon: 'info', layout: 'inline',
    showTime: true, useWaClasses: true,
    cardClass: 'bg-wa-bg border border-wa-border text-red-500',
  },
};

/** Whether a role renders as a painel-only system card. */
export function isSystemCardRole(role) {
  return Object.prototype.hasOwnProperty.call(SYSTEM_CARD_VARIANTS, role);
}

/**
 * The accent color for a bubble's sender label / quote bar.
 * user → blue, operator (manual) → amber, AI → green. Mirrors the inline rule.
 *
 * @param {boolean} isUser
 * @param {boolean} isOperator
 * @returns {string}
 */
export function senderColor(isUser, isOperator) {
  // IA usa uma variável CSS (--wa-ai-label) que fica CLARA no modo escuro e escura
  // no claro — a cor inline não responderia ao tema sozinha. user→azul, operator→âmbar.
  return isUser ? '#1f7aec' : (isOperator ? '#b45309' : 'rgb(var(--wa-ai-label))');
}

/**
 * The short text shown for a quoted message inside a reply, per media type.
 * Falls back to the message's own caption/content when present.
 *
 * @param {{media_type?:string, content?:string|null}} qmsg
 * @param {string} text - already-stripped text (group prefix removed).
 * @returns {string}
 */
export function quotedMediaText(qmsg, text) {
  const mt = qmsg && qmsg.media_type;
  if (mt === 'image') return text || '📷 Foto';
  if (mt === 'audio') return '🎤 Áudio';
  if (mt === 'video') return text || '🎬 Vídeo';
  if (mt === 'sticker') return '🪧 Figurinha';
  if (mt === 'document') return '📄 Documento';
  if (mt === 'location' || mt === 'live_location') return '📍 Localização';
  return text;
}
