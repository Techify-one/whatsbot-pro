import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { getQrUrl, reconnect, logout, refreshQr } from '../services/api.js';

const html = htm.bind(h);

export function QRCode({ connected, qrAvailable, qrVersion }) {
  const [imgSrc, setImgSrc] = useState(null);
  const [imgError, setImgError] = useState(false);

  // Update QR image only when version changes (backend cached, ~20s per QR)
  useEffect(() => {
    if (!connected && qrAvailable && qrVersion) {
      setImgSrc(getQrUrl());
      setImgError(false);
    } else if (connected) {
      setImgSrc(null);
    }
  }, [connected, qrAvailable, qrVersion]);

  return html`
    <div class="bg-white rounded-xl p-5 flex flex-col items-center min-w-[260px] border border-wa-border shadow-sm">
      <h2 class="text-sm font-semibold text-wa-text mb-3">WhatsApp QR Code</h2>

      <div class="w-[220px] h-[220px] flex items-center justify-center bg-wa-panel rounded-lg mb-3 overflow-hidden">
        ${connected ? html`
          <div class="text-center">
            <div class="text-4xl mb-2">\u2713</div>
            <span class="text-green-600 font-medium">Conectado!</span>
          </div>
        ` : qrAvailable && imgSrc && !imgError ? html`
          <img
            src=${imgSrc}
            alt="QR Code"
            class="qr-image w-full h-full object-contain"
            onError=${() => setImgError(true)}
          />
        ` : html`
          <div class="text-center text-wa-secondary">
            <div class="animate-pulse-slow text-lg mb-1">...</div>
            <span class="text-sm">Aguardando QR Code...</span>
          </div>
        `}
      </div>

      ${connected ? html`
        <span class="text-green-600 text-sm font-medium mb-3">Conectado ao WhatsApp</span>
      ` : qrAvailable ? html`
        <span class="text-yellow-600 text-sm mb-1">Escaneie o QR Code com seu celular</span>
        <button
          onClick=${() => refreshQr()}
          class="text-wa-teal hover:text-wa-tealDark text-xs underline mb-2 transition-colors"
        >
          Atualizar QR Code
        </button>
      ` : html`
        <span class="text-red-500 text-sm mb-3">Desconectado</span>
      `}

      <div class="flex gap-2 w-full">
        <button
          onClick=${() => reconnect()}
          class="flex-1 py-2 px-3 bg-wa-teal hover:bg-wa-tealDark text-white text-sm rounded-lg transition-colors"
        >
          Reconectar
        </button>
        <button
          onClick=${() => logout()}
          class="flex-1 py-2 px-3 bg-red-500 hover:bg-red-600 text-white text-sm rounded-lg transition-colors"
        >
          Desconectar
        </button>
      </div>
    </div>
  `;
}
