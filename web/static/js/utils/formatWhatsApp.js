/**
 * Convert WhatsApp formatting markers to HTML.
 * Escapes HTML first to prevent XSS, then applies formatting.
 */

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function formatWhatsApp(text, mentionNames = []) {
  if (!text) return '';
  let s = escapeHtml(text);

  // Code block (``` must come before inline `)
  s = s.replace(/```([\s\S]+?)```/g,
    '<pre style="background:#1e1e1e;color:#d4d4d4;padding:6px 8px;border-radius:4px;overflow-x:auto;font-family:monospace;font-size:12px;margin:4px 0;white-space:pre-wrap">$1</pre>');

  // Inline code
  s = s.replace(/`([^`\n]+?)`/g,
    '<code style="background:#1e1e1e;color:#d4d4d4;padding:1px 4px;border-radius:3px;font-family:monospace;font-size:13px">$1</code>');

  // Bold
  s = s.replace(/\*([^\*\n]+?)\*/g, '<b>$1</b>');

  // Italic (word boundaries to avoid matching underscores in URLs)
  s = s.replace(/\b_((?!_)[^\n]+?)_\b/g, '<i>$1</i>');

  // Strikethrough
  s = s.replace(/~([^~\n]+?)~/g, '<s>$1</s>');

  // Links (URLs) — after escaping, so &amp; in query strings is fine
  s = s.replace(/(https?:\/\/[^\s<]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer" style="color:#53bdeb;text-decoration:underline;word-break:break-all">$1</a>');

  // Phone numbers with @ (e.g. 5511999999999@s.whatsapp.net) — styled as link but not clickable
  s = s.replace(/(\d{7,15})@([\w.]+)/g,
    '<span style="color:#53bdeb;text-decoration:underline;cursor:default">$1@$2</span>');

  // @mentions: known group member names + the mention-all keywords (@todos, …).
  // Names are escaped the same way the text was, then regex-escaped, so they
  // match the already-escaped string. Longest names first so a short name does
  // not shadow a longer one. The mention-all keywords are ALWAYS highlighted —
  // independent of whether any member names resolved — so @todos stands out the
  // same way a user mention does, even in groups with no named members.
  const ALL_KEYWORDS = ['todos', 'todes', 'todxs', 'all', 'everyone', 'geral'];
  const names = (mentionNames || [])
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .map(n => escapeRegex(escapeHtml(n)));
  const alts = [...names, ...ALL_KEYWORDS];
  const mentionRe = new RegExp('@(' + alts.join('|') + ')', 'gi');
  s = s.replace(mentionRe, '<span style="color:#53bdeb;font-weight:600">@$1</span>');

  return s;
}

/**
 * Highlight WhatsApp markup for the composer overlay (WYSIWYG-in-place).
 *
 * Unlike formatWhatsApp (used to render already-sent messages), this KEEPS the
 * markers (*, _, ~, `) in place — only dimmed — so the visible text stays
 * character-for-character identical to what the operator typed in the textarea
 * underneath. Dropping the markers would shorten the mirror text and drift the
 * caret. Markers are re-emitted as HTML entities (&#42; etc.) so that a later
 * pass never re-matches a marker this pass just produced.
 */
export function highlightComposerMarkup(text) {
  if (!text) return '';
  let s = escapeHtml(text);
  const dim = (inner) => `<span style="opacity:.4">${inner}</span>`;

  // Code block (``` must come before inline `)
  s = s.replace(/```([\s\S]+?)```/g,
    (m, inner) => dim('&#96;&#96;&#96;') + '<code style="font-family:monospace">' + inner + '</code>' + dim('&#96;&#96;&#96;'));

  // Inline code
  s = s.replace(/`([^`\n]+?)`/g,
    (m, inner) => dim('&#96;') + '<code style="font-family:monospace">' + inner + '</code>' + dim('&#96;'));

  // Bold — authored with DOUBLE asterisks (**texto**). Collapsed to WhatsApp's
  // single-asterisk wire format by toWhatsAppMarkup() on send.
  s = s.replace(/\*\*([^\n]+?)\*\*/g,
    (m, inner) => dim('&#42;&#42;') + '<b>' + inner + '</b>' + dim('&#42;&#42;'));

  // Italic (word boundaries to avoid matching underscores in URLs)
  s = s.replace(/\b_((?!_)[^\n]+?)_\b/g,
    (m, inner) => dim('&#95;') + '<i>' + inner + '</i>' + dim('&#95;'));

  // Strikethrough
  s = s.replace(/~([^~\n]+?)~/g,
    (m, inner) => dim('&#126;') + '<s>' + inner + '</s>' + dim('&#126;'));

  return s;
}

/**
 * Convert the composer's authoring markup to WhatsApp's wire format before
 * sending. Bold is AUTHORED as **texto** (two asterisks), but WhatsApp's wire
 * format for bold is *texto* (one asterisk) — collapse the pairs so the
 * recipient sees clean bold. Italic (_), strikethrough (~) and code (`) already
 * match WhatsApp's wire format and pass through untouched. Mirrors the bold
 * regex in highlightComposerMarkup so the preview and the sent text agree.
 */
export function toWhatsAppMarkup(text) {
  if (!text) return text;
  return text.replace(/\*\*([^\n]+?)\*\*/g, '*$1*');
}
