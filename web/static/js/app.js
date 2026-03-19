import { h, render } from 'preact';
import { useState, useCallback } from 'preact/hooks';
import htm from 'htm';
import { Header } from './components/Header.js';
import { Dashboard } from './components/Dashboard.js';
import { Sandbox } from './components/Sandbox.js';
import { StatusBar } from './components/StatusBar.js';
import { useWebSocket } from './hooks/useWebSocket.js';
import { useConfig } from './hooks/useConfig.js';

const html = htm.bind(h);

function App() {
  const [status, setStatus] = useState({ connected: false, msg_count: 0, auto_reply_running: false });
  const [qrAvailable, setQrAvailable] = useState(false);
  const [qrVersion, setQrVersion] = useState(0);
  const [notification, setNotification] = useState('Iniciando...');
  const [tab, setTab] = useState('dashboard');

  const { config, loading, saving, save } = useConfig();

  useWebSocket({
    onStatus: useCallback((data) => setStatus(data), []),
    onQrUpdate: useCallback((data) => {
      setQrAvailable(data.available);
      if (data.version) setQrVersion(data.version);
    }, []),
    onGowaStatus: useCallback((data) => setNotification(data.message), []),
    onConfigSaved: useCallback(() => setNotification('Configurações salvas!'), []),
  });

  async function handleSave(data) {
    const result = await save(data);
    setNotification(result.message);
  }

  function handleNotify(msg) {
    setNotification(msg);
  }

  if (loading) {
    return html`
      <div class="max-w-4xl mx-auto p-4">
        <${Header} />
        <div class="text-center text-gray-400 py-20 animate-pulse-slow">Carregando...</div>
      </div>
    `;
  }

  return html`
    <div class="max-w-4xl mx-auto p-4">
      <${Header} />

      <!-- Tabs -->
      <div class="flex gap-1 mb-4 bg-gray-800/50 rounded-lg p-1">
        <button
          onClick=${() => setTab('dashboard')}
          class="flex-1 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            tab === 'dashboard'
              ? 'bg-gray-700 text-white'
              : 'text-gray-400 hover:text-gray-200 hover:bg-gray-700/50'
          }"
        >
          \u2699 Painel
        </button>
        <button
          onClick=${() => setTab('sandbox')}
          class="flex-1 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            tab === 'sandbox'
              ? 'bg-whatsapp/20 text-whatsapp'
              : 'text-gray-400 hover:text-gray-200 hover:bg-gray-700/50'
          }"
        >
          \uD83E\uDDEA Sandbox / Debug
        </button>
      </div>

      <!-- Content -->
      ${tab === 'dashboard'
        ? html`<${Dashboard}
            status=${status}
            qrAvailable=${qrAvailable}
            qrVersion=${qrVersion}
            config=${config}
            saving=${saving}
            onSave=${handleSave}
            onNotify=${handleNotify}
          />`
        : html`<${Sandbox} />`
      }

      <${StatusBar}
        status=${status}
        notification=${notification}
      />
    </div>
  `;
}

render(html`<${App} />`, document.getElementById('app'));
