import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { EMOJI_CATEGORIES } from './emojiData.js';

const html = htm.bind(h);

// ── Emoji Picker ─────────────────────────────────────────────────
// Categorized, scrollable emoji grid. No external dependency. Calls
// onPick(emoji) when an emoji is selected.

export function EmojiPicker({ onPick }) {
  const [cat, setCat] = useState(EMOJI_CATEGORIES[0].key);
  const current = EMOJI_CATEGORIES.find((c) => c.key === cat) || EMOJI_CATEGORIES[0];

  return html`
    <div class="bg-wa-panel rounded-lg shadow-lg border border-wa-border w-[296px] overflow-hidden">
      <div class="flex border-b border-wa-border overflow-x-auto wa-scrollbar">
        ${EMOJI_CATEGORIES.map((c) => html`
          <button
            type="button"
            key=${c.key}
            title=${c.label}
            onClick=${() => setCat(c.key)}
            class="shrink-0 px-[7px] py-[6px] text-[18px] leading-none transition-colors ${
              cat === c.key ? 'border-b-2 border-wa-teal' : 'opacity-55 hover:opacity-100'
            }"
          >${c.icon}</button>
        `)}
      </div>
      <div class="px-[6px] py-[6px] max-h-[240px] overflow-y-auto wa-scrollbar grid grid-cols-8 gap-[1px]">
        ${current.emojis.map((em, i) => html`
          <button
            type="button"
            key=${em + i}
            onClick=${() => onPick(em)}
            class="text-[20px] leading-none w-[33px] h-[33px] rounded hover:bg-wa-hover flex items-center justify-center"
          >${em}</button>
        `)}
      </div>
    </div>
  `;
}
