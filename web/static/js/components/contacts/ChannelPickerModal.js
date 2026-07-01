import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// Rótulo/cor por provider — alinhado ao ChannelChip da sidebar, dentro da paleta
// dark-mode-safe (wa-*/blue) conforme a regra de tema do CLAUDE.md.
const PROVIDER_META = {
  gowa:           { label: 'WhatsApp',  dot: 'bg-wa-teal' },
  whatsapp_cloud: { label: 'Cloud API', dot: 'bg-blue-500' },
  telegram:       { label: 'Telegram',  dot: 'bg-blue-500' },
  test:           { label: 'Teste',     dot: 'bg-wa-secondary' },
};

// Distinct from utils/phone.js `formatPhoneDisplay` ON PURPOSE: a channel's
// `own_phone` may already carry a leading `+` and is not always a BR-grouped
// number, so we only normalise the `+` prefix here (no DDD/grouping). Kept local.
function formatPhone(p) {
  if (!p) return '';
  return p.startsWith('+') ? p : `+${p}`;
}

// Popup de escolha da caixa de entrada por onde iniciar um atendimento novo.
// `channels` já vem filtrado pelo backend (somente conectadas + logadas).
// Clicar numa caixa chama onPick(channel); a 1ª mensagem é roteada por ela.
export function ChannelPickerModal({ phoneDisplay, channels = [], onPick, onClose }) {
  return html`
    <div
      class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6 relative">
        <button
          onClick=${onClose}
          class="absolute top-3 right-3 text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
          title="Fechar"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>

        <h2 class="text-base font-semibold text-wa-text mb-1">Escolha a caixa de entrada</h2>
        <p class="text-sm text-wa-secondary mb-4">
          Iniciar atendimento com <span class="text-wa-text font-medium">${phoneDisplay}</span> por:
        </p>

        ${channels.length === 0 ? html`
          <div class="text-sm text-wa-secondary bg-wa-panel rounded-lg py-3 px-4 text-center">
            Nenhuma caixa de entrada conectada no momento.
          </div>
        ` : html`
          <div class="flex flex-col gap-2">
            ${channels.map((ch) => {
              const meta = PROVIDER_META[ch.provider] || { label: ch.provider || 'Canal', dot: 'bg-wa-secondary' };
              const name = ch.display_name || meta.label;
              return html`
                <button
                  key=${ch.id}
                  onClick=${() => onPick(ch)}
                  class="w-full flex items-center gap-3 py-3 px-4 bg-wa-panel hover:bg-wa-hover rounded-lg transition-colors text-left cursor-pointer border border-wa-border"
                >
                  <span class="w-2.5 h-2.5 rounded-full shrink-0 ${meta.dot}"></span>
                  <span class="flex-1 min-w-0">
                    <span class="block text-[14px] text-wa-text truncate">${name}</span>
                    <span class="block text-[12px] text-wa-secondary truncate">
                      ${meta.label}${ch.own_phone ? ` · ${formatPhone(ch.own_phone)}` : ''}
                    </span>
                  </span>
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" class="text-wa-secondary shrink-0"><polyline points="9 18 15 12 9 6"/></svg>
                </button>
              `;
            })}
          </div>
        `}
      </div>
    </div>
  `;
}
