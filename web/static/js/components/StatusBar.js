import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

function formatPhone(phone) {
  if (!phone) return '';
  // Format as +55 (11) 99999-9999 for BR numbers
  if (phone.length === 13 && phone.startsWith('55')) {
    return `+${phone.slice(0, 2)} (${phone.slice(2, 4)}) ${phone.slice(4, 9)}-${phone.slice(9)}`;
  }
  if (phone.length === 12 && phone.startsWith('55')) {
    return `+${phone.slice(0, 2)} (${phone.slice(2, 4)}) ${phone.slice(4, 8)}-${phone.slice(8)}`;
  }
  return `+${phone}`;
}

export function StatusBar({ status, notification }) {
  const connected = status?.connected || false;
  const msgCount = status?.msg_count || 0;
  const botPhone = status?.bot_phone || '';
  const botName = status?.bot_name || '';

  const phoneLabel = botPhone
    ? (botName ? `${botName} · ${formatPhone(botPhone)}` : formatPhone(botPhone))
    : '';

  return html`
    <div class="px-4 py-1.5 bg-gray-800 border-t border-gray-700 flex items-center justify-between text-sm">
      <div class="flex items-center gap-4">
        <div class="flex items-center gap-2">
          <div class="w-2.5 h-2.5 rounded-full status-dot ${connected ? 'bg-green-400' : 'bg-red-400'}"></div>
          <span class="${connected ? 'text-green-400' : 'text-red-400'}">
            ${connected ? 'Conectado' : 'Desconectado'}
          </span>
        </div>
        ${connected && phoneLabel ? html`
          <span class="text-gray-400">|</span>
          <span class="text-gray-300 truncate max-w-xs" title="${phoneLabel}">${phoneLabel}</span>
        ` : ''}
        <span class="text-gray-400">|</span>
        <span class="text-gray-300">Msgs processadas: <strong>${msgCount}</strong></span>
      </div>
      <span class="text-gray-500 text-xs truncate ml-4 max-w-xs">${notification || ''}</span>
    </div>
  `;
}
