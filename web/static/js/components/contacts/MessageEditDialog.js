import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// Modal para editar o texto de uma mensagem de saída (operador/IA). Espelha o
// pop-up de Apagar: overlay + card, fecha no clique-fora / Esc / Cancelar. Mantém
// seu próprio estado de texto (semeado com o conteúdo atual), então o container só
// precisa abrir/fechar via `message` + `onSave`/`onCancel`.
export function MessageEditDialog({ message, onSave, onCancel }) {
  const [text, setText] = useState(message ? (message.content || '') : '');
  const areaRef = useRef(null);

  // Re-seed quando abrir para outra mensagem e focar/selecionar o fim.
  useEffect(() => {
    setText(message ? (message.content || '') : '');
    setTimeout(() => {
      const el = areaRef.current;
      if (el) { el.focus(); const n = el.value.length; el.setSelectionRange(n, n); }
    }, 0);
  }, [message]);

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onCancel(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  if (!message) return null;

  const trimmed = text.trim();
  const canSave = !!trimmed && trimmed !== (message.content || '').trim();

  return html`
    <div
      class="fixed inset-0 z-[130] bg-black/40 flex items-center justify-center"
      onClick=${onCancel}
    >
      <div
        class="bg-wa-panel rounded-lg shadow-xl w-[420px] max-w-[92vw] p-[22px]"
        onClick=${(e) => e.stopPropagation()}
      >
        <div class="text-[15px] text-wa-text mb-[14px]">Editar mensagem</div>
        <textarea
          ref=${areaRef}
          class="wa-field w-full rounded-[8px] p-[10px] text-[14px] leading-[19px] resize-none min-h-[96px] max-h-[240px]"
          value=${text}
          onInput=${(e) => setText(e.target.value)}
          onKeyDown=${(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && canSave) {
              e.preventDefault(); onSave(message, text);
            }
          }}
        ></textarea>
        <div class="flex justify-end gap-[10px] mt-[18px]">
          <button
            onClick=${onCancel}
            class="px-[20px] py-[8px] rounded-full text-wa-teal text-[14px] font-medium hover:bg-wa-teal/10 transition-colors"
          >Cancelar</button>
          <button
            onClick=${() => onSave(message, text)}
            disabled=${!canSave}
            class="px-[20px] py-[8px] rounded-full border border-wa-teal text-wa-teal text-[14px] font-medium hover:bg-wa-teal/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >Salvar</button>
        </div>
      </div>
    </div>
  `;
}
