// Channels — QRConnect (Plano 23 · D4), extracted verbatim from
// ChannelsManager.js. Polls the channel status + QR image until the GOWA device
// logs in. Shown right after creating a GOWA channel and reopenable from a card.
import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { getChannelStatus, getChannelQR } from '../../services/api.js';
import { Dot } from './notices.js';

const html = htm.bind(h);

export function QRConnect({ channelId, displayName, onClose }) {
  const [qrUrl, setQrUrl] = useState('');
  const [loggedIn, setLoggedIn] = useState(false);
  const [ownPhone, setOwnPhone] = useState('');
  const [waiting, setWaiting] = useState(true);
  const urlRef = useRef('');
  const aliveRef = useRef(true);
  const doneRef = useRef(false);

  function setQr(url) {
    if (urlRef.current) { try { URL.revokeObjectURL(urlRef.current); } catch (e) {} }
    urlRef.current = url || '';
    setQrUrl(url || '');
  }

  useEffect(() => {
    aliveRef.current = true;
    doneRef.current = false;
    let statusTimer = null;
    let qrTimer = null;

    function stop() {
      if (statusTimer) clearInterval(statusTimer);
      if (qrTimer) clearInterval(qrTimer);
      statusTimer = qrTimer = null;
    }

    // Fetch the QR. CRITICAL: each /app/login call mints a brand-new QR and
    // restarts GOWA's pairing session, so we do this sparingly (once now, then
    // ~every 20s before the 30s expiry) — NOT on every status poll, otherwise
    // the session the user is scanning gets torn down before the handshake
    // completes and the device never logs in.
    async function fetchQR() {
      if (!aliveRef.current || doneRef.current) return;
      const url = await getChannelQR(channelId);
      if (!aliveRef.current || doneRef.current) { if (url) URL.revokeObjectURL(url); return; }
      if (url) { setQr(url); setWaiting(false); }
    }

    // Poll only the status frequently — cheap, and does NOT disturb pairing.
    async function pollStatus() {
      if (!aliveRef.current || doneRef.current) return;
      const st = await getChannelStatus(channelId);
      if (!aliveRef.current || doneRef.current) return;
      const data = (st && st.ok && st.data) || {};
      if (data.logged_in) {
        doneRef.current = true;
        stop();
        setLoggedIn(true);
        setOwnPhone(data.own_phone || '');
        setWaiting(false);
        setQr('');
      }
    }

    fetchQR();
    pollStatus();
    statusTimer = setInterval(pollStatus, 2500);
    qrTimer = setInterval(fetchQR, 20000);

    return () => {
      aliveRef.current = false;
      stop();
      setQr('');
    };
  }, [channelId]);

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="flex items-center justify-between mb-3">
        <div class="text-[14px] font-medium text-wa-text">
          Conectar WhatsApp — ${displayName || channelId}
        </div>
        <button class="px-3 py-1.5 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
          onClick=${onClose}>Fechar</button>
      </div>

      ${loggedIn ? html`
        <div class="flex flex-col items-center gap-2 py-4">
          <div class="text-[40px]">✅</div>
          <div class="text-[15px] font-medium text-green-600">Conectado!</div>
          ${ownPhone ? html`<div class="text-[13px] text-wa-secondary">📱 ${ownPhone}</div>` : null}
          <button class="mt-2 px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity"
            onClick=${onClose}>Concluir</button>
        </div>
      ` : html`
        <div class="flex flex-col items-center gap-3 py-2">
          <p class="text-[13px] text-wa-secondary text-center max-w-sm">
            Abra o WhatsApp no celular → <span class="font-medium">Aparelhos conectados</span> →
            <span class="font-medium">Conectar um aparelho</span> e aponte para o código abaixo.
          </p>
          ${qrUrl ? html`
            <img src=${qrUrl} alt="QR Code"
              class="w-56 h-56 rounded-md bg-white p-2 border border-wa-border" />
          ` : html`
            <div class="w-56 h-56 rounded-md border border-wa-border flex items-center justify-center text-[13px] text-wa-secondary">
              ${waiting ? 'Gerando QR Code…' : 'Aguardando GOWA…'}
            </div>
          `}
          <div class="flex items-center gap-1.5 text-[12px] text-wa-secondary">
            <${Dot} on=${false} /> Aguardando leitura…
          </div>
        </div>
      `}
    </div>
  `;
}
