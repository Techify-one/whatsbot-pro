import { h } from 'preact';
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { EmojiPicker } from './EmojiPicker.js';

const html = htm.bind(h);

// ── Context Menu (messages + input) ──────────────────────────────
// Generic per-element action menu, opened by right-click or by the hover
// arrow inside a bubble. Mirrors the visual language of ContextMenu.js.
// `items`: [{ label, icon (html), onClick, disabled?, danger? }]

export function MessageContextMenu({ x, y, items, reactionBar, onClose }) {
  const ref = useRef(null);
  const [showPicker, setShowPicker] = useState(false);
  // Start at the requested coords; a layout pass clamps to the viewport below.
  const [pos, setPos] = useState({ left: x, top: y });

  useEffect(() => {
    function handleClick(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    function handleKey(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [onClose]);

  // Measure the actually-rendered menu (the reaction bar can be wider than the
  // item list, and the emoji picker is wider/taller still) and clamp it inside
  // the viewport. Runs before paint, so the corrected position never flickers.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const margin = 8;
    const { width, height } = el.getBoundingClientRect();
    let left = x;
    let top = y;
    if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
    if (top + height > window.innerHeight - margin) top = window.innerHeight - height - margin;
    left = Math.max(margin, left);
    top = Math.max(margin, top);
    setPos({ left, top });
  }, [x, y, showPicker, items.length, reactionBar]);

  return html`
    <div
      ref=${ref}
      class="fixed z-[120]"
      style="left:${pos.left}px;top:${pos.top}px"
    >
      ${showPicker ? html`
        <${EmojiPicker} onPick=${(em) => { reactionBar.onReact(em); onClose(); }} />
      ` : html`
        ${reactionBar ? html`
          <div class="bg-wa-panel rounded-full shadow-lg border border-wa-border px-[6px] py-[4px] mb-[6px] flex items-center gap-[2px] w-fit">
            ${reactionBar.emojis.map((em) => html`
              <button
                key=${em}
                onClick=${() => { reactionBar.onReact(em); onClose(); }}
                class="text-[22px] leading-none w-[36px] h-[36px] rounded-full flex items-center justify-center hover:bg-wa-hover transition-colors ${reactionBar.current === em ? 'bg-wa-hover' : ''}"
              >${em}</button>
            `)}
            ${(reactionBar.current && !reactionBar.emojis.includes(reactionBar.current)) ? html`
              <button
                onClick=${() => { reactionBar.onReact(reactionBar.current); onClose(); }}
                title="Remover reação"
                class="text-[22px] leading-none w-[36px] h-[36px] rounded-full flex items-center justify-center hover:bg-wa-hover transition-colors bg-wa-hover"
              >${reactionBar.current}</button>
            ` : html`
              <button
                onClick=${() => setShowPicker(true)}
                title="Mais emojis"
                class="w-[36px] h-[36px] rounded-full flex items-center justify-center hover:bg-wa-hover transition-colors text-wa-secondary"
              >
                <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
                  <path d="M11 13H5v-2h6V5h2v6h6v2h-6v6h-2z"/>
                </svg>
              </button>
            `}
          </div>
        ` : ''}
        <div class="bg-wa-panel rounded-lg shadow-lg border border-wa-border py-[4px] min-w-[180px]">
        ${items.map((item) => html`
          <button
            key=${item.label}
            disabled=${item.disabled}
            onClick=${() => { if (item.disabled) return; item.onClick(); onClose(); }}
            class="w-full text-left px-4 py-[10px] text-[14.5px] transition-colors flex items-center gap-3 ${
              item.disabled
                ? 'text-wa-secondary opacity-50 cursor-not-allowed'
                : (item.danger ? 'text-red-400 hover:bg-wa-hover' : 'text-wa-text hover:bg-wa-hover')
            }"
          >
            ${item.icon}
            ${item.label}
          </button>
        `)}
        </div>
      `}
    </div>
  `;
}

// Copy icon markup, shared by call sites.
export const CopyIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/>
  </svg>
`;

export const TrashIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
  </svg>
`;

export const ReplyIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z"/>
  </svg>
`;

export const LinkIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"/>
  </svg>
`;

export const ImproveIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7z"/>
  </svg>
`;

// ── Clipboard helpers (work in insecure contexts via execCommand) ──

export function copyToClipboard(text) {
  if (!text) return;
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    return;
  }
  fallbackCopy(text);
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.top = '-9999px';
  ta.style.left = '-9999px';
  ta.setAttribute('readonly', '');
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (_) { /* ignore */ }
  document.body.removeChild(ta);
}
