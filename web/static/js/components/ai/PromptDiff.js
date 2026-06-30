// Stateless GitHub-style diff renderer. Renders straight from the backend's
// structured `lines[]` (no string parsing): each line is ctx/add/del, and changed
// lines carry word-level `segments` so altered words get a stronger tint.
//
// Dark-mode (regra do CLAUDE.md): translucent tints (não dependem do fallback do
// custom.css) + texto neutro `text-wa-text`. Linha inteira ganha fundo claro;
// a palavra alterada ganha fundo mais forte.

import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

const LINE_BG = { add: 'bg-green-500/10', del: 'bg-red-500/10', ctx: '' };
const GLYPH = { add: '+', del: '−', ctx: ' ' };
const GLYPH_COLOR = { add: 'text-green-600', del: 'text-red-600', ctx: 'text-wa-secondary' };
const WORD_BG = { add: 'bg-green-500/30 rounded-sm', del: 'bg-red-500/30 rounded-sm' };

// Fallback when only a raw `unified_diff` string is available (no `lines`): split
// by the first char of each line, sem realce de palavra.
function parseUnified(unified) {
  const out = [];
  (unified || '').split('\n').forEach((ln) => {
    if (ln.startsWith('+++') || ln.startsWith('---') || ln.startsWith('@@')) return;
    if (ln.startsWith('+')) out.push({ type: 'add', text: ln.slice(1) });
    else if (ln.startsWith('-')) out.push({ type: 'del', text: ln.slice(1) });
    else out.push({ type: 'ctx', text: ln.startsWith(' ') ? ln.slice(1) : ln });
  });
  return out;
}

function DiffLine({ line }) {
  const type = line.type || 'ctx';
  const content = Array.isArray(line.segments)
    ? line.segments.map((s, i) => html`
        <span key=${i} class=${s.t === 'eq' ? '' : (WORD_BG[s.t] || '')}>${s.s}</span>`)
    : (line.text != null ? line.text : '');
  return html`
    <div class=${`flex ${LINE_BG[type] || ''}`}>
      <span class=${`w-4 select-none shrink-0 text-center ${GLYPH_COLOR[type] || ''}`}>${GLYPH[type] || ' '}</span>
      <span class="whitespace-pre-wrap break-words text-wa-text flex-1">${content}</span>
    </div>
  `;
}

export function PromptDiff({ lines, unifiedDiff }) {
  const rows = (Array.isArray(lines) && lines.length)
    ? lines
    : parseUnified(unifiedDiff);
  if (!rows.length) {
    return html`<div class="text-[12px] text-wa-secondary py-3">Sem diferenças.</div>`;
  }
  return html`
    <div class="font-mono text-[12px] bg-wa-bg border border-wa-border rounded-md overflow-x-auto p-2 leading-5">
      ${rows.map((line, i) => html`<${DiffLine} key=${i} line=${line} />`)}
    </div>
  `;
}

export default PromptDiff;
