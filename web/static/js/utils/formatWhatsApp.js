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

export function formatWhatsApp(text) {
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

  return s;
}
