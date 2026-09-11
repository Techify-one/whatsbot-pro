// @ts-check
//
// Pure token-parsing helpers for the chat composer (Plano 23 · D3).
//
// Extracted verbatim from the inline @mention / "/quick-reply" caret-detection
// logic in ContactDetail.js so it can be unit-tested with `node --test`. These
// functions are the heart of the autocomplete: given the text BEFORE the caret,
// they detect whether the operator is mid-token and, if so, return the typed
// query and the index of the trigger char ('@' or '/').
//
// PURE: no DOM, no network, no module state. Caret/selection reads stay in the
// hook; only the string matching lives here.

/**
 * @typedef {Object} TokenMatch
 * @property {string} query - text typed after the trigger char (may be empty).
 * @property {number} start - index of the trigger char in the full string.
 */

// '@' followed by letters/numbers/underscore, anchored at the caret and preceded
// by start-of-string or whitespace. Unicode-aware (matches accented names).
const MENTION_RE = /(?:^|\s)@([\p{L}\p{N}_]*)$/u;

// '/' followed by word chars or '-', same anchoring. Used for quick replies.
const QUICK_REPLY_RE = /(?:^|\s)\/([\w-]*)$/;

/**
 * Detect an "@token" immediately before the caret.
 *
 * @param {string} textBeforeCaret - the input value sliced to the caret offset.
 * @param {number} caret - caret offset (== textBeforeCaret.length at call sites).
 * @returns {TokenMatch|null} the query + start index, or null when not in a token.
 */
export function detectMentionToken(textBeforeCaret, caret) {
  const m = (textBeforeCaret || '').match(MENTION_RE);
  if (!m) return null;
  return { query: m[1], start: caret - m[1].length - 1 };
}

/**
 * Detect a "/token" immediately before the caret.
 *
 * @param {string} textBeforeCaret
 * @param {number} caret
 * @returns {TokenMatch|null}
 */
export function detectQuickReplyToken(textBeforeCaret, caret) {
  const m = (textBeforeCaret || '').match(QUICK_REPLY_RE);
  if (!m) return null;
  return { query: m[1], start: caret - m[1].length - 1 };
}

// Um token válido é o gatilho ('@' ou '/') seguido SÓ de caracteres de token —
// superset das duas regexes de detecção acima (`[\p{L}\p{N}_]*` da menção e
// `[\w-]*` da resposta rápida). Espaço, pontuação ou quebra de linha na região
// significam que ela deixou de ser um token e não é seguro substituí-la.
const TOKEN_REGION_RE = /^[@/][\p{L}\p{N}_-]*$/u;

/**
 * Replace the typed token (from `start` to `caret`) with `insert`, returning the
 * new value and the caret position after the insertion. Used by both @mention
 * (insert = "@Name ") and quick-reply (insert = the snippet) application.
 *
 * ⚠️ Os dois índices vêm de FONTES DIFERENTES e podem descrever coisas
 * incompatíveis (plano 132 · F5): `start` é congelado quando o menu ABRE e
 * `caret` é lido VIVO do DOM na hora de aplicar. Entre um e outro o operador
 * pode ter clicado noutro ponto, colado texto ou tido o rascunho re-hidratado —
 * e aí o splice ingênuo `slice(0,start) + insert + slice(caret)` DUPLICA (caret
 * antes de start) ou APAGA (caret muito além) um trecho inteiro, calado. Por
 * isso a substituição só acontece quando `[start, caret)` ainda é de fato um
 * token; fora disso o valor sai INTACTO e o caret fica onde o operador o
 * deixou. Perder a menção é irritante — comer o texto dele é o bug relatado.
 *
 * @param {string} value - full current input value.
 * @param {number} start - index of the trigger char.
 * @param {number} caret - caret offset (end of the token).
 * @param {string} insert - replacement text.
 * @returns {{ value: string, caret: number }}
 */
export function replaceToken(value, start, caret, insert) {
  const v = value || '';
  const abort = { value: v, caret };
  if (!Number.isInteger(start) || !Number.isInteger(caret)) return abort;
  if (start < 0 || caret < 0 || start > caret) return abort;
  if (start > v.length || caret > v.length) return abort;
  if (!TOKEN_REGION_RE.test(v.slice(start, caret))) return abort;
  const before = v.slice(0, start);
  const after = v.slice(caret);
  return { value: before + insert + after, caret: (before + insert).length };
}

/**
 * Label shown for a group member in the @mention menu. A just-joined member has
 * no saved contact name and no captured pushName yet, so fall back to the phone
 * (or lid) digits — inserting "@<digits>" still resolves to a real mention.
 *
 * @param {{name?:string, phone?:string, lid?:string}} m
 * @returns {string}
 */
export function mentionLabel(m) {
  return (m && (m.name || m.phone || m.lid)) || '';
}

/**
 * Build the @mention candidate list for a typed query (used by render + keys).
 * The special "todos" entry maps to @todos (mention everyone) and is shown when
 * the query is empty or a prefix of "todos". At most 8 candidates.
 *
 * @param {string} query
 * @param {Array<{name?:string, phone?:string, lid?:string, is_admin?:boolean}>} members
 * @returns {Array<any>}
 */
export function mentionCandidates(query, members) {
  const q = (query || '').toLowerCase();
  const list = [];
  if (!q || 'todos'.startsWith(q)) list.push({ special: true, name: 'todos' });
  for (const m of members || []) {
    const label = mentionLabel(m);
    if (label && label.toLowerCase().includes(q)) list.push(m);
  }
  return list.slice(0, 8);
}

/**
 * Candidates for a typed "/query": match short_code or content; sliced to 8.
 *
 * @param {string} query
 * @param {Array<{short_code:string, content?:string}>} quickReplies
 * @returns {Array<any>}
 */
export function quickReplyCandidates(query, quickReplies) {
  const q = (query || '').toLowerCase();
  return (quickReplies || []).filter(qr =>
    qr.short_code.toLowerCase().includes(q) || (qr.content || '').toLowerCase().includes(q)
  ).slice(0, 8);
}

/**
 * Strip the "[Sender]: " group prefix the backend prepends to user content for
 * LLM context. Returns the parsed sender (or null) and the text without prefix.
 *
 * @param {string} content
 * @returns {{ sender: string|null, text: string }}
 */
export function stripGroupPrefix(content) {
  const text = typeof content === 'string' ? content : (content || '');
  if (typeof text !== 'string') return { sender: null, text: '' };
  const match = text.match(/^\[([^\]]+)\]:\s*([\s\S]*)$/);
  if (match) return { sender: match[1], text: match[2] };
  return { sender: null, text };
}
