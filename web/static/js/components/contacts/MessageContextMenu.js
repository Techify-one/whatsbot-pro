import { h } from 'preact';
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { EmojiPicker } from './EmojiPicker.js';

const html = htm.bind(h);

// ── Context Menu (messages + input) ──────────────────────────────
// Generic per-element action menu, opened by right-click or by the hover
// arrow inside a bubble. Mirrors the visual language of ContextMenu.js.
// `items`: [{ label, icon (html), onClick, disabled?, danger? }]
//          | { separator: true }   ← divisória (plano 97 · F4), sem label nem
//            onClick: separa o bloco CONTEXTUAL (link/e-mail/telefone sob o
//            cursor) do bloco da mensagem. A `key` é o ÍNDICE justamente porque
//            um separador não tem label.

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
        ${items.map((item, i) => (item && item.separator) ? html`
          <div key=${'sep' + i} class="my-[4px] border-t border-wa-border"></div>
        ` : html`
          <button
            key=${'item' + i + ':' + item.label}
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

export const EditIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34a.9959.9959 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
  </svg>
`;

// Ícones das AÇÕES DE ENTIDADE (plano 97 · F4) — link/e-mail/telefone sob o
// cursor. Mesmo formato dos de cima (24×24, 18px, `fill="currentColor"`), então
// herdam a cor do item e o modo escuro sai de graça, sem cor crua.

export const OpenExternalIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/>
  </svg>
`;

export const MailIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/>
  </svg>
`;

export const PhoneIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/>
  </svg>
`;

// (ImproveIcon movido para o plugin "melhorias" — o core não conhece mais o recurso.)

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
