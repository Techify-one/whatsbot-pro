import { h } from 'preact';
import htm from 'htm';
import { QRCode } from './QRCode.js';
import { ConfigPanel } from './ConfigPanel.js';

const html = htm.bind(h);

export function Dashboard({ status, qrAvailable, qrVersion, config, saving, onSave, onNotify }) {
  return html`
    <div class="flex gap-4 flex-col lg:flex-row">
      <!-- Left: QR Code -->
      <${QRCode}
        connected=${status?.connected || false}
        qrAvailable=${qrAvailable}
        qrVersion=${qrVersion}
        botPhone=${status?.bot_phone || ''}
        botName=${status?.bot_name || ''}
      />

      <!-- Right: Config Panel -->
      <${ConfigPanel}
        config=${config}
        saving=${saving}
        onSave=${onSave}
        onNotify=${onNotify}
      />
    </div>
  `;
}
