import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { ConnectionStatus, QRCodeModal } from './QRCode.js';
import { ConfigPanel } from './ConfigPanel.js';

const html = htm.bind(h);

export function Dashboard({ status, qrAvailable, qrVersion, config, saving, onSave, onNotify }) {
  const connected = status?.connected || false;
  const [showQR, setShowQR] = useState(!connected);
  const userDismissedQR = useRef(false);
  const prevConnected = useRef(connected);

  // Auto-open QR modal when disconnected (unless user dismissed it)
  useEffect(() => {
    // Reset dismiss flag when transitioning from connected to disconnected
    if (prevConnected.current && !connected) {
      userDismissedQR.current = false;
      setShowQR(true);
    }
    // Auto-close when connected
    if (connected) {
      setShowQR(false);
    }
    prevConnected.current = connected;
  }, [connected]);

  // Auto-open on first load if not connected
  useEffect(() => {
    if (!connected && !userDismissedQR.current) {
      setShowQR(true);
    }
  }, []);

  function handleCloseQR() {
    userDismissedQR.current = true;
    setShowQR(false);
  }

  return html`
    <div class="flex flex-col gap-4">
      <${ConnectionStatus}
        connected=${connected}
        botPhone=${status?.bot_phone || ''}
        botName=${status?.bot_name || ''}
        onOpenQR=${() => setShowQR(true)}
      />

      ${showQR ? html`
        <${QRCodeModal}
          connected=${connected}
          qrAvailable=${qrAvailable}
          qrVersion=${qrVersion}
          botPhone=${status?.bot_phone || ''}
          botName=${status?.bot_name || ''}
          onClose=${handleCloseQR}
        />
      ` : null}

      <${ConfigPanel}
        config=${config}
        saving=${saving}
        onSave=${onSave}
        onNotify=${onNotify}
      />
    </div>
  `;
}
