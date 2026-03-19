import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

export function StatusBar({ status, notification }) {
  const connected = status?.connected || false;
  const msgCount = status?.msg_count || 0;

  return html`
    <div class="mt-4 px-4 py-2 bg-gray-800 rounded-lg flex items-center justify-between text-sm">
      <div class="flex items-center gap-4">
        <div class="flex items-center gap-2">
          <div class="w-2.5 h-2.5 rounded-full status-dot ${connected ? 'bg-green-400' : 'bg-red-400'}"></div>
          <span class="${connected ? 'text-green-400' : 'text-red-400'}">
            ${connected ? 'Conectado' : 'Desconectado'}
          </span>
        </div>
        <span class="text-gray-400">|</span>
        <span class="text-gray-300">Msgs processadas: <strong>${msgCount}</strong></span>
      </div>
      <span class="text-gray-500 text-xs truncate ml-4 max-w-xs">${notification || ''}</span>
    </div>
  `;
}
