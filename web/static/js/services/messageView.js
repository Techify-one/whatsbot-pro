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
 *   collapsible?: boolean,
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
  // Collapsible (plano 63): diagnostic noise, minimized to a 1-line chip by default.
  transcription: {
    label: 'Transcrição privada', icon: 'lock', layout: 'inline', collapsible: true,
    showTime: true, useWaClasses: false,
    style: 'background: #2d1b4e; color: #d4bfff; border: 1px solid #4a2d7a;',
  },
  // AI tool-call trace — amber (kept; matches the "Ferramenta IA" intent).
  // Collapsible (plano 63): the trace is diagnostic; the first line is the summary.
  tool_call: {
    label: 'Ferramenta IA', icon: 'tool', layout: 'inline', collapsible: true,
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
 * Whether a role renders collapsed-by-default as a 1-line chip (plano 63).
 * Only `transcription` and `tool_call` — diagnostic noise the operator wants
 * minimized. NEVER use a broad `isSystemCardRole` check for this: `private_note`
 * (human-authored), `system`, `system_notice`, `error` and `conversation_event`
 * stay fully expanded (D1).
 *
 * @param {string} role
 * @returns {boolean}
 */
export function isCollapsibleRole(role) {
  const v = SYSTEM_CARD_VARIANTS[role];
  return !!(v && v.collapsible);
}

/**
 * A short, plain-text preview shown inside a collapsed card chip (plano 63).
 *
 * ⚠️ Receives the RAW `content`, never the output of the parent's `fmt()`:
 * truncating already-formatted HTML would cut a tag mid-way and (via
 * dangerouslySetInnerHTML) inject broken markup. The result is rendered by
 * normal htm interpolation, which escapes — the WhatsApp formatting is
 * intentionally dropped in the preview.
 *
 * - `tool_call` → the first non-empty line (already "🔧 <tool_name>", the
 *   perfect summary), truncated only if it still exceeds `maxLen`.
 * - anything else → whitespace collapsed to single spaces, trimmed, and if
 *   longer than `maxLen` cut at the last word boundary before it (hard cut as
 *   fallback) + '…'.
 *
 * @param {string} role
 * @param {string|null|undefined} content
 * @param {{maxLen?: number}} [opts]
 * @returns {string}
 */
export function collapsedPreview(role, content, { maxLen = 70 } = {}) {
  if (content == null) return '';
  const str = String(content);
  let base;
  if (role === 'tool_call') {
    // The first non-empty line is the "🔧 <tool_name>" summary — use it whole.
    base = (str.split('\n').find((line) => line.trim() !== '') || '').trim();
  } else {
    base = str.replace(/\s+/g, ' ').trim();
  }
  if (!base) return '';
  if (base.length <= maxLen) return base;
  const slice = base.slice(0, maxLen);
  const lastSpace = slice.lastIndexOf(' ');
  const cut = lastSpace > 0 ? slice.slice(0, lastSpace) : slice;
  return cut + '…';
}

/**
 * A stable identity key for a chat message, used to key collapse state in the
 * container (plano 63). Precedence: `_id` → `msg_id` → `role:ts` → index.
 *
 * The message list is keyed by index and history is prepended, so a per-card
 * `useState` would glue to the wrong message after "load older" (G1). The
 * container keys its expansion Set by this instead. The final `ix:` fallback is
 * degraded (an index can drift after a prepend) and only reached for a message
 * with no `_id`/`msg_id`/`ts` — e.g. a `tool_call` broadcast whose save failed
 * so no `_id` was attached (G6).
 *
 * @param {{_id?: any, msg_id?: string|null, role?: string, ts?: any}} m
 * @param {number} index
 * @returns {string}
 */
export function cardStateKey(m, index) {
  if (m) {
    if (m._id != null) return `id:${m._id}`;
    if (m.msg_id != null && m.msg_id !== '') return `mid:${m.msg_id}`;
    if (m.ts != null) return `rt:${m.role}:${m.ts}`;
  }
  return `ix:${index}`;
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

// plano 87 — prefixos que o backend antepõe ao `content` de uma mídia quando a
// IA transcreve/descreve/extrai (server/transcription.py `_MEDIA_PREFIX`). O
// texto que vem DEPOIS deles pertence à IA, não ao cliente, e nunca pode ser
// desenhado como se fosse a legenda. Só é consultado no caminho LEGADO (linha
// anterior à coluna `media_caption`) — mensagem nova já traz a legenda pronta.
const AI_CONTENT_PREFIXES = [
  '[Descrição da imagem]',
  '[Transcrição do áudio]',
  '[Conteúdo do documento]',
];

// Placeholders que o backend grava quando a mídia não tem legenda nenhuma. Não
// são texto do cliente — o balão já desenha a própria mídia.
const MEDIA_PLACEHOLDERS = new Set([
  '[Imagem enviada pelo contato]', '[Áudio recebido]', '[Áudio]', '[Vídeo]',
]);

/**
 * A legenda que o CLIENTE escreveu junto da mídia — ou string vazia.
 *
 * Fonte única do "o que o humano digitou" para qualquer superfície que desenhe
 * uma mensagem de mídia (balão, preview da sidebar, citação). Precedência:
 *
 * 1. `media_caption` (plano 87) — verbatim, gravado no INSERT. Caminho normal.
 * 2. Linha LEGADA (sem a coluna): cai no `content` com dois cortes, ambos
 *    ancorados em MARCADOR EXATO — nunca em posição:
 *    - o bloco da IA vem ANTES (imagem/áudio): o content inteiro começa com o
 *      prefixo ⇒ devolve vazio. Não se tenta fatiar por `\n` para resgatar a
 *      legenda do fim: a descrição é markdown MULTILINHA, então o corte
 *      acertaria por acidente e, ao errar, exporia texto da IA como se fosse do
 *      cliente. Quem resolve esse legado é o backfill (Fase D), com gabarito.
 *    - o bloco da IA vem DEPOIS (documento): corta em `\n<prefixo>`, que é
 *      inequívoco — tudo antes é do cliente (podendo ser multilinha), tudo
 *      depois é da IA.
 *    Placeholders de mídia sem legenda também viram vazio.
 *
 * @param {{media_caption?:string|null, content?:string|null}} message
 * @param {string} [displayContent] - content já tratado pelo chamador (ex.: com
 *   o prefixo de remetente de grupo removido). Default: `message.content`.
 * @returns {string}
 */
export function mediaCaptionOf(message, displayContent) {
  if (!message) return '';
  const own = message.media_caption;
  if (typeof own === 'string' && own.trim()) return own;
  let body = (displayContent !== undefined && displayContent !== null)
    ? displayContent
    : (message.content || '');
  if (typeof body !== 'string' || !body) return '';
  if (AI_CONTENT_PREFIXES.some((p) => body.startsWith(p))) return '';
  for (const p of AI_CONTENT_PREFIXES) {
    const at = body.indexOf('\n' + p);
    if (at !== -1) body = body.slice(0, at);
  }
  body = body.trim();
  if (!body || MEDIA_PLACEHOLDERS.has(body)) return '';
  return body;
}

/**
 * The short text shown for a quoted message inside a reply, per media type.
 * Falls back to the message's own caption/content when present.
 *
 * plano 87: a legenda vem de `mediaCaptionOf`, então o snippet de uma imagem
 * transcrita mostra o que o cliente escreveu (ou o rótulo), nunca a descrição
 * gerada pela IA — que antes vazava aqui via `content` cru.
 *
 * @param {{media_type?:string, content?:string|null, media_caption?:string|null}} qmsg
 * @param {string} text - already-stripped text (group prefix removed).
 * @returns {string}
 */
export function quotedMediaText(qmsg, text) {
  const mt = qmsg && qmsg.media_type;
  if (mt === 'image') return mediaCaptionOf(qmsg, text) || '📷 Foto';
  if (mt === 'audio') return '🎤 Áudio';
  if (mt === 'video') return mediaCaptionOf(qmsg, text) || '🎬 Vídeo';
  if (mt === 'sticker') return '🪧 Figurinha';
  // Documento sempre foi o rótulo fixo; a legenda só entra quando ela EXISTE de
  // fato (coluna do plano 87). Não cair no `content` legado aqui de propósito:
  // no GOWA ele é o rótulo composto "[Documento recebido: x.pdf]\n<legenda>",
  // que ficaria pior que "📄 Documento".
  if (mt === 'document') return (qmsg.media_caption || '').trim() || '📄 Documento';
  if (mt === 'location' || mt === 'live_location') return '📍 Localização';
  return text;
}
