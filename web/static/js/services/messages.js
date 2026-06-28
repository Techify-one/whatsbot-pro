// @ts-check
//
// Pure message helpers for the conversation UI (Plano 23 · R12).
//
// Consolidates the optimistic-send / WS dedup predicate and the sidebar
// media-preview label that were duplicated inline in `Contacts.js` (the
// pre-fetch buffer merge, the loading-buffer merge, and the live `new_message`
// merge all hand-rolled the SAME `(ts+role) OR (content+role within 30s)`
// check). Extracted here as pure functions so they can be unit-tested with
// `node --test` and reused by the attendances flow.
//
// PURE: no DOM, no network, no module state.

/**
 * @typedef {Object} ChatMessage
 * @property {string} [role] - 'user' | 'assistant' | panel-only roles…
 * @property {string|null} [content]
 * @property {number} [ts] - unix seconds (may be fractional).
 * @property {string} [msg_id] - GOWA message id (stable server identity).
 * @property {string} [media_type]
 */

/** Window (seconds) within which a same-role, same-content message is a dup. */
export const DEDUP_WINDOW_S = 30;

/**
 * Whether `a` and `b` are the "same" message for optimistic-send / WS dedup.
 *
 * Two messages match when they share a role AND either (a) the exact same
 * timestamp, or (b) identical content within {@link DEDUP_WINDOW_S} seconds.
 * This mirrors the legacy inline predicate so an optimistic bubble and the
 * server's broadcast copy (which may differ slightly in ts) collapse into one.
 *
 * Note: a stable `msg_id` match is handled separately by the caller (reconcile
 * in place) BEFORE falling back to this heuristic.
 *
 * @param {ChatMessage} a
 * @param {ChatMessage} b
 * @returns {boolean}
 */
export function sameMessage(a, b) {
  if (!a || !b) return false;
  if (a.role !== b.role) return false;
  if (a.ts === b.ts) return true;
  return a.content === b.content
    && Math.abs((a.ts || 0) - (b.ts || 0)) < DEDUP_WINDOW_S;
}

/**
 * Whether `message` already exists in `existing` (by {@link sameMessage}).
 *
 * @param {ChatMessage} message
 * @param {ChatMessage[]} existing
 * @returns {boolean}
 */
export function isDuplicateMessage(message, existing) {
  if (!Array.isArray(existing)) return false;
  return existing.some(m => sameMessage(m, message));
}

/**
 * Index of the message in `list` that matches `message` ({@link sameMessage}),
 * or -1. Useful when the caller wants to merge ids/status into the dup.
 *
 * @param {ChatMessage} message
 * @param {ChatMessage[]} list
 * @returns {number}
 */
export function findDuplicateIndex(message, list) {
  if (!Array.isArray(list)) return -1;
  return list.findIndex(m => sameMessage(m, message));
}

/**
 * Default sidebar preview labels per media type (Portuguese, with emoji).
 * @type {Record<string, string>}
 */
const MEDIA_PREVIEW_LABELS = {
  image: '📷 Imagem',
  audio: '🎤 Áudio',
  video: '🎥 Vídeo',
  sticker: '🎨 Sticker',
  document: '📄 Documento',
  location: '📍 Localização',
  live_location: '📍 Localização ao vivo',
  poll: '📊 Enquete',
  interactive: '↩️ Resposta',
  order: '🛒 Pedido',
  product: '🏷️ Produto',
  contact: '👤 Contato',
  contacts: '👤 Contato',
};

// Media types whose own caption/content (when present) should be preferred over
// the generic emoji label in the sidebar preview. Voice notes, stickers, live
// location and product never carry useful text, so they always use the label.
const PREVIEW_PREFERS_CONTENT = new Set([
  'image', 'video', 'document', 'location', 'poll', 'interactive', 'order', 'contact', 'contacts',
]);

/**
 * One-line sidebar preview for a message: a media-type label (optionally
 * overridden by the message's own caption/content), or the first 80 chars of
 * plain text. Mirrors the inline `lastPreview` logic in Contacts.js.
 *
 * @param {ChatMessage} message
 * @returns {string}
 */
export function mediaPreviewLabel(message) {
  if (!message) return '';
  const mt = message.media_type;
  if (mt && MEDIA_PREVIEW_LABELS[mt]) {
    const label = MEDIA_PREVIEW_LABELS[mt];
    if (PREVIEW_PREFERS_CONTENT.has(mt) && message.content) return message.content;
    return label;
  }
  return (message.content || '').substring(0, 80);
}
