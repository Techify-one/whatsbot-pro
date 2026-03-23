import { h, render } from 'preact';
import { useState, useCallback } from 'preact/hooks';
import htm from 'htm';
import { Header } from './components/Header.js';
import { Dashboard } from './components/Dashboard.js';
import { Sandbox } from './components/Sandbox.js';
import { Contacts } from './components/Contacts.js';
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
  const [newMessage, setNewMessage] = useState(null);

  const { config, loading, saving, save } = useConfig();

  useWebSocket({
    onStatus: useCallback((data) => setStatus(data), []),
    onQrUpdate: useCallback((data) => {
      setQrAvailable(data.available);
      if (data.version) setQrVersion(data.version);
    }, []),
    onGowaStatus: useCallback((data) => setNotification(data.message), []),
    onConfigSaved: useCallback(() => setNotification('Configurações salvas!'), []),
    onNewMessage: useCallback((data) => setNewMessage(data), []),
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
      <div class="h-screen flex items-center justify-center">
        <div class="text-center text-gray-400 animate-pulse-slow">Carregando...</div>
      </div>
    `;
  }

  return html`
    <div class="h-screen flex flex-col">
      <${Header} tab=${tab} onTabChange=${setTab} />

      <main class="flex-1 overflow-auto">
        ${tab === 'dashboard'
          ? html`<div class="max-w-5xl mx-auto p-4"><${Dashboard}
              status=${status}
              qrAvailable=${qrAvailable}
              qrVersion=${qrVersion}
              config=${config}
              saving=${saving}
              onSave=${handleSave}
              onNotify=${handleNotify}
            /></div>`
          : tab === 'contacts'
            ? html`<${Contacts} newMessage=${newMessage} />`
            : html`<div class="max-w-5xl mx-auto p-4"><${Sandbox} /></div>`
        }
      </main>

      <${StatusBar}
        status=${status}
        notification=${notification}
      />
    </div>
  `;
}

render(html`<${App} />`, document.getElementById('app'));
