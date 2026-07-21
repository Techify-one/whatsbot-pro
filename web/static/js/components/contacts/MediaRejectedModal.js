import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// Popup de anexo incompatível: o arquivo escolhido não atende às regras do canal
// (tamanho/formato declarados pelo provider — no WhatsApp oficial, os limites da
// Meta). Aparece NO LUGAR do envio, então nunca vira uma bolha com erro.
export function MediaRejectedModal({ rejection, onClose }) {
  if (!rejection) return null;
  const isSize = rejection.reason === 'too_big';
  return html`
    <div
      class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6 relative" role="alertdialog">
        <button
          onClick=${onClose}
          class="absolute top-3 right-3 text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
          title="Fechar"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>

        <div class="flex items-start gap-3 mb-3 pr-6">
          <span class="text-[22px] leading-none shrink-0">${isSize ? '📦' : '🚫'}</span>
          <h2 class="text-base font-semibold text-wa-text">
            ${isSize ? 'Arquivo grande demais' : 'Formato não compatível'}
          </h2>
        </div>

        <p class="text-sm text-wa-text mb-2">${rejection.message}</p>
        ${rejection.detail ? html`
          <p class="text-[13px] text-wa-secondary bg-wa-panel border border-wa-border rounded-lg py-2 px-3">
            ${rejection.detail}
          </p>
        ` : ''}

        <div class="flex justify-end mt-5">
          <button
            onClick=${onClose}
            class="bg-wa-teal text-white text-sm font-medium rounded-lg py-2 px-4 hover:opacity-90 transition-opacity cursor-pointer"
          >
            Entendi
          </button>
        </div>
      </div>
    </div>
  `;
}
