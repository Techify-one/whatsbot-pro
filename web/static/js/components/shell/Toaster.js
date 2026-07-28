// Host global de toasts. Montado UMA vez na raiz do app (App.js), ao lado do
// PluginModalHost. Assina o barramento services/notify.js e renderiza uma pilha
// de avisos que somem sozinhos (auto-dismiss + botão ×). Usa classes wa-*/tintas
// de acento (legível nos dois temas — mesmo padrão do banner de erro do painel).
//
// Reutilizável para qualquer mensagem transitória; hoje o produtor principal é o
// caminho de 403 "Permissão negada." (services/httpClient.js + api dos plugins).

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { subscribe } from '../../services/notify.js';

const html = htm.bind(h);

const KIND_CLASS = {
  error: 'bg-red-500/15 border-red-500 text-red-500 font-semibold',
  success: 'bg-wa-teal/10 border-wa-teal text-wa-teal',
  info: 'bg-wa-panel border-wa-border text-wa-text',
};
const KIND_ICON = { error: '⛔', success: '✓', info: 'ℹ️' };

export function Toaster() {
  const [toasts, setToasts] = useState([]);

  useEffect(() => subscribe((t) => {
    setToasts((list) => [...list, t]);
    if (t.timeoutMs > 0) {
      setTimeout(() => setToasts((list) => list.filter((x) => x.id !== t.id)), t.timeoutMs);
    }
  }), []);

  const dismiss = (id) => setToasts((list) => list.filter((x) => x.id !== id));
  if (!toasts.length) return null;

  return html`
    <div class="fixed z-[120] bottom-4 right-4 flex flex-col gap-2 w-[320px] max-w-[92vw] pointer-events-none">
      ${toasts.map((t) => html`
        <div key=${t.id} role="alert"
          class="pointer-events-auto flex items-start gap-2 px-3 py-2.5 rounded-lg shadow-lg border text-[13px] ${KIND_CLASS[t.kind] || KIND_CLASS.info}">
          <span class="shrink-0 leading-none pt-0.5">${KIND_ICON[t.kind] || KIND_ICON.info}</span>
          <span class="flex-1 leading-snug break-words">${t.message}</span>
          <button onClick=${() => dismiss(t.id)} aria-label="Fechar"
            class="shrink-0 leading-none opacity-70 hover:opacity-100 px-1 -mr-1">×</button>
        </div>`)}
    </div>`;
}

export default Toaster;
