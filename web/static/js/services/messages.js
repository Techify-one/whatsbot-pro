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

// `messageView` também é puro (sem DOM/rede) — importá-lo mantém este módulo
// testável com `node --test` e evita uma 2ª cópia das regras de legenda.
import { mediaCaptionOf } from './messageView.js';

/**
 * @typedef {Object} ChatMessage
 * @property {string} [role] - 'user' | 'assistant' | panel-only roles…
 * @property {string|null} [content]
 * @property {number} [ts] - unix seconds (may be fractional).
 * @property {string} [msg_id] - GOWA message id, or synthetic "pn:<uuid>" for
 *   notified private notes (stable server identity).
 * @property {number} [_id] - DB row id (`messages.id`) — universal identity,
 *   present on detail reads, note broadcasts and POST responses.
 * @property {string} [media_type]
 */

/** Window (seconds) within which a same-role, same-content message is a dup. */
export const DEDUP_WINDOW_S = 30;

/**
 * Whether `a` and `b` are the "same" message for optimistic-send / WS dedup.
 *
 * Stable identity decides FIRST (plano 53): when both sides carry a `msg_id`
 * (GOWA id, or the synthetic `pn:<uuid>` of a notified private note) that field
 * is authoritative — equal means same message, different means two real rows.
 * Same for the DB row id `_id`. Identity is immune to clock skew: the optimistic
 * bubble stamps `ts` from the CLIENT clock while the server's broadcast copy
 * carries the row's ts, so a skewed client (>30s) breaks the content window —
 * the exact bug that duplicated private notes.
 *
 * Only when neither side settles identity do we fall back to the legacy
 * heuristic: same role AND either (a) the exact same timestamp, or (b)
 * identical content within {@link DEDUP_WINDOW_S} seconds.
 *
 * @param {ChatMessage} a
 * @param {ChatMessage} b
 * @returns {boolean}
 */
export function sameMessage(a, b) {
  if (!a || !b) return false;
  if (a.role !== b.role) return false;
  // Identity short-circuit — decides both ways (match AND mismatch).
  if (a.msg_id && b.msg_id) return a.msg_id === b.msg_id;
  if (a._id != null && b._id != null) return a._id === b._id;
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
 * Index of an OPTIMISTIC bubble in `list` that `message` collapses into, or -1
 * (plano 33 F4). Like {@link findDuplicateIndex} but only matches entries WITHOUT
 * a stable `msg_id` — i.e. an optimistic copy (the operator's own send, whose
 * bubble has no msg_id until its echo carries one). Two DISTINCT inbound messages
 * of identical content in separate batches (e.g. "ok"/"ok") each already carry
 * their OWN msg_id, so the `!m.msg_id` guard stops the second from being swallowed
 * into the first — they are two real rows and must render as two bubbles. The
 * content-only {@link findDuplicateIndex} wrongly merged them.
 *
 * @param {ChatMessage} message
 * @param {ChatMessage[]} list
 * @returns {number}
 */
export function optimisticDupIndex(message, list) {
  if (!Array.isArray(list)) return -1;
  return list.findIndex(m => !m.msg_id && sameMessage(m, message));
}

/**
 * Drop, from `messages`, the optimistic bubbles that an authoritative combined
 * row supersedes (plano 57). A batch joins N inbound messages into ONE row under
 * the LAST message's `msg_id`; the earlier per-message `msg_id`s arrive in
 * `supersedes`, so their individual t=0 bubbles are collapsed into the combined
 * row (which then reconciles in place by its own `msg_id`). Returns the SAME
 * array reference when nothing is removed (cheap no-op for the common case).
 *
 * @param {ChatMessage[]} messages
 * @param {string[]|undefined} supersedes
 * @returns {ChatMessage[]}
 */
export function dropSuperseded(messages, supersedes) {
  if (!Array.isArray(messages) || !Array.isArray(supersedes) || !supersedes.length) {
    return Array.isArray(messages) ? messages : [];
  }
  const sup = new Set(supersedes.filter(Boolean));
  if (!sup.size) return messages;
  const filtered = messages.filter(m => !(m && m.msg_id && sup.has(m.msg_id)));
  return filtered.length === messages.length ? messages : filtered;
}

/**
 * Merge WS-buffered messages into the freshly-fetched history (plano 57). Used by
 * the detail loader and the background thread reload, both of which drain the
 * pre-fetch/during-fetch buffers into the REST result. Beyond the legacy dedup it
 * honors `supersedes`: any buffered authoritative combined row collapses the
 * individual optimistic bubbles (in `existing` AND in the buffer) whose `msg_id`
 * it superseded — so a conversation opened mid-batch never shows an orphan bubble
 * alongside the DB's combined row. Order preserved: history first, then the
 * surviving buffered messages.
 *
 * @param {ChatMessage[]} existing - messages from the REST fetch.
 * @param {ChatMessage[]} pending - buffered WS messages (pre + during fetch).
 * @returns {ChatMessage[]}
 */
export function mergeBufferedMessages(existing, pending) {
  const base0 = Array.isArray(existing) ? existing : [];
  const buf = Array.isArray(pending) ? pending : [];
  if (!buf.length) return base0;
  const superseded = new Set();
  for (const m of buf) {
    if (m && Array.isArray(m.supersedes)) {
      for (const s of m.supersedes) if (s) superseded.add(s);
    }
  }
  const base = superseded.size
    ? base0.filter(m => !(m && m.msg_id && superseded.has(m.msg_id)))
    : base0;
  const add = [];
  for (const m of buf) {
    if (m && m.msg_id && superseded.has(m.msg_id)) continue;  // a collapsed bubble
    // msg_id collision WITHIN the buffer (e.g. the t=0 optimistic copy AND its
    // post-save authoritative copy — or a per-message bubble AND the combined row —
    // both share the last msg_id): keep the AUTHORITATIVE/richest copy instead of
    // first-seen. Arrival order puts the optimistic (stale, single-fragment) copy
    // first; without this the combined content would be dropped (live != reload).
    if (m && m.msg_id) {
      const j = add.findIndex(x => x.msg_id === m.msg_id);
      if (j !== -1) {
        if (m.authoritative || (m._id != null && add[j]._id == null)) add[j] = m;
        continue;
      }
    }
    if (!isDuplicateMessage(m, base) && !isDuplicateMessage(m, add)) add.push(m);
  }
  return add.length ? [...base, ...add] : base;
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

// Tipos cujo `content` a IA reescreve (server/transcription.py). Para eles o
// texto exibível NUNCA sai do content cru — sai de `mediaCaptionOf` (plano 87),
// senão a preview mostra "[Descrição da imagem]: …" em vez do que o cliente
// escreveu. Espelha `_MEDIA_PREVIEW_LABELS` do backend (db/repositories/_mapping.py).
const PREVIEW_AI_REWRITTEN = new Set(['image', 'audio', 'document']);

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
    if (PREVIEW_AI_REWRITTEN.has(mt)) {
      return (PREVIEW_PREFERS_CONTENT.has(mt) ? mediaCaptionOf(message) : '') || label;
    }
    if (PREVIEW_PREFERS_CONTENT.has(mt) && message.content) return message.content;
    return label;
  }
  return (message.content || '').substring(0, 80);
}
