import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// RequiredAttributesModal (P05) — bloqueia a resolução do atendimento quando há
// atributos personalizados marcados como "Obrigatório preencher" ainda vazios.
// Lista quais atributos faltam; ao clicar em "OK", o chamador abre o local de
// preenchimento (painel "Informações do atendimento") via onConfirm.
export function RequiredAttributesModal({ missing = [], onConfirm }) {
  return html`
    <div
      class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onConfirm(); }}
    >
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6">
        <h2 class="text-base font-semibold text-wa-text mb-1">
          Preencha antes de resolver
        </h2>
        <p class="text-sm text-wa-secondary mb-3">
          ${missing.length === 1
            ? 'Este atributo é obrigatório e precisa ser preenchido antes de resolver a conversa:'
            : 'Estes atributos são obrigatórios e precisam ser preenchidos antes de resolver a conversa:'}
        </p>
        <ul class="mb-5 space-y-1.5">
          ${missing.map(def => html`
            <li key=${def.attribute_key} class="flex items-start gap-2 text-[14px] text-wa-text">
              <span class="text-red-500 mt-0.5">•</span>
              <span>${def.display_name || def.attribute_key}</span>
            </li>
          `)}
        </ul>
        <div class="flex justify-end">
          <button
            onClick=${onConfirm}
            class="px-5 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity"
          >
            OK
          </button>
        </div>
      </div>
    </div>
  `;
}

