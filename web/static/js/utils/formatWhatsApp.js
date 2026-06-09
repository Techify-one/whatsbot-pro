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
