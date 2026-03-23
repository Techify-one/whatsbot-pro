import { h, render } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import { Dashboard } from './components/Dashboard.js';
import { Sandbox } from './components/Sandbox.js';
import { Contacts } from './components/Contacts.js';
import { useWebSocket } from './hooks/useWebSocket.js';
import { useConfig } from './hooks/useConfig.js';

const html = htm.bind(h);

function GearMenu({ tab, onTabChange }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  return html`
    <div ref=${menuRef} class="fixed top-3 right-3 z-50">
      <button
        onClick=${() => setOpen(!open)}
        class="w-[36px] h-[36px] flex items-center justify-center rounded-full bg-white shadow-md border border-wa-border hover:bg-wa-hover transition-colors"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="#54656f">
          <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.488.488 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>
        </svg>
      </button>
      ${open ? html`
        <div class="absolute right-0 mt-1 bg-white rounded-lg shadow-lg border border-wa-border py-1 min-w-[160px]">
          <button
            onClick=${() => { onTabChange('dashboard'); setOpen(false); }}
            class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-2 ${tab === 'dashboard' ? 'text-wa-teal font-medium' : 'text-wa-text'}"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.488.488 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
            Painel
          </button>
          <button
            onClick=${() => { onTabChange('sandbox'); setOpen(false); }}
            class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-2 ${tab === 'sandbox' ? 'text-wa-teal font-medium' : 'text-wa-text'}"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M7 5h10v2h2V3c0-.55-.45-1-1-1H6c-.55 0-1 .45-1 1v4h2V5zm8.41 11.59L20 12l-4.59-4.59L14 8.83 17.17 12 14 15.17l1.41 1.42zM10 15.17L6.83 12 10 8.83 8.59 7.41 4 12l4.59 4.59L10 15.17zM17 19H7v-2H5v4c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-4h-2v2z"/></svg>
            Sandbox
          </button>
        </div>
      ` : null}
    </div>
  `;
}

function App() {
  const [status, setStatus] = useState({ connected: false, msg_count: 0, auto_reply_running: false });
  const [qrAvailable, setQrAvailable] = useState(false);
  const [qrVersion, setQrVersion] = useState(0);
  const [notification, setNotification] = useState('Iniciando...');
  const [tab, setTab] = useState('contacts');
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
    <div class="h-screen flex flex-col relative">
      <${GearMenu} tab=${tab} onTabChange=${setTab} />

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
    </div>
  `;
}

render(html`<${App} />`, document.getElementById('app'));
