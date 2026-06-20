// Tela de configuração do plugin WhatsApp (GOWA) — config:true.
// Renderizada DENTRO do modal "Configurar" do card em /plugins. Mostra, por canal
// GOWA, o status de conexão e o QR para parear o número. O canal "default" vem
// semeado; números adicionais são criados na tela "Canais".
//
// Dark mode: classes semânticas wa-* e .wa-field (legível nos dois temas).
import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function authHeaders(extra = {}) {
  const token = localStorage.getItem('whatsbot_token') || '';
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

async function apiFetch(url, init = {}) {
  const headers = authHeaders(init.headers || {});
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    throw new Error('Não autenticado.');
  }
  return res;
}

const FIELD = 'wa-field w-full rounded px-3 py-2 text-sm border border-wa-border';
const LABEL = 'block text-sm font-medium text-wa-text mb-1';
const HINT = 'text-xs text-wa-secondary mt-1';

export default function GowaConfig() {
  const [channels, setChannels] = useState([]);
  const [channelId, setChannelId] = useState('');
  const [status, setStatus] = useState(null);
  const [qrUrl, setQrUrl] = useState(null);
  const [msg, setMsg] = useState(null);
  const qrUrlRef = useRef(null);

  async function loadChannels() {
    try {
      const r = await apiFetch('/api/channels');
      const data = await r.json();
      const list = ((data && data.data) || []).filter((c) => c.provider === 'gowa');
      setChannels(list);
      if (!channelId && list.length) {
        const def = list.find((c) => c.id === 'default') || list[0];
        setChannelId(def.id);
      }
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message || e) });
    }
  }

  async function loadStatus(cid) {
    if (!cid) { setStatus(null); return; }
    try {
      const r = await apiFetch(`/api/channels/${encodeURIComponent(cid)}/status`);
      const data = await r.json();
      setStatus((data && data.data) || null);
    } catch (e) {
      setStatus(null);
    }
  }

  async function loadQr(cid) {
    if (!cid) return;
    try {
      const r = await apiFetch(`/api/channels/${encodeURIComponent(cid)}/qr`);
      if (r.status === 200) {
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        if (qrUrlRef.current) URL.revokeObjectURL(qrUrlRef.current);
        qrUrlRef.current = url;
        setQrUrl(url);
      } else {
        if (qrUrlRef.current) { URL.revokeObjectURL(qrUrlRef.current); qrUrlRef.current = null; }
        setQrUrl(null);
      }
    } catch {
      setQrUrl(null);
    }
  }

  useEffect(() => { loadChannels(); }, []);

  // Poll status (4s) + QR (12s while not connected). Cleared on channel change/unmount.
  useEffect(() => {
    if (!channelId) return;
    loadStatus(channelId);
    loadQr(channelId);
    const st = setInterval(() => loadStatus(channelId), 4000);
    const qt = setInterval(() => {
      if (!(status && status.logged_in)) loadQr(channelId);
    }, 12000);
    return () => {
      clearInterval(st); clearInterval(qt);
      if (qrUrlRef.current) { URL.revokeObjectURL(qrUrlRef.current); qrUrlRef.current = null; }
    };
  }, [channelId]);

  const connected = status && status.connected;
  const loggedIn = status && status.logged_in;
  const needsQr = status && status.needs_qr;

  return html`
    <div class="p-5 max-w-2xl mx-auto text-wa-text">
      <h2 class="text-xl font-bold mb-1">WhatsApp (GOWA)</h2>
      <p class="text-sm text-wa-secondary mb-4">
        Conecte um número de WhatsApp escaneando o QR abaixo (WhatsApp →
        Aparelhos conectados → Conectar um aparelho). Esta caixa de entrada roda
        como subprocesso gerenciado por este plugin; desativá-lo derruba a conexão.
      </p>

      ${msg && html`
        <div class=${'mb-3 px-3 py-2 rounded text-sm ' +
          (msg.kind === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
          ${msg.text}
        </div>`}

      <div class="space-y-4">
        <div>
          <label class=${LABEL}>Canal WhatsApp (GOWA)</label>
          ${channels.length > 1
            ? html`<select class=${FIELD} value=${channelId}
                onChange=${(e) => setChannelId(e.target.value)}>
                ${channels.map((c) => html`<option value=${c.id}>${c.display_name || c.id} (${c.id})</option>`)}
              </select>`
            : html`<div class=${HINT}>${channels.length === 1
                ? html`Canal: <strong>${channels[0].display_name || channels[0].id}</strong>`
                : 'Nenhum canal GOWA — o canal "default" é criado automaticamente.'}</div>`}
        </div>

        <div class="px-3 py-2 rounded bg-wa-bg border border-wa-border text-sm">
          ${loggedIn
            ? html`<span class="text-wa-teal font-medium">● Conectado</span>${
                status && status.own_phone ? html` — <strong>${status.own_phone}</strong>` : ''}`
            : connected
              ? html`<span class="text-amber-600 font-medium">● Aguardando pareamento</span> — escaneie o QR.`
              : html`<span class="text-wa-secondary font-medium">● Desconectado</span> — iniciando o GOWA…`}
        </div>

        ${needsQr && html`
          <div class="flex flex-col items-center gap-2 p-4 rounded bg-wa-panel border border-wa-border">
            ${qrUrl
              ? html`<img src=${qrUrl} alt="QR Code" class="w-56 h-56 bg-white p-2 rounded" />`
              : html`<div class="w-56 h-56 flex items-center justify-center text-wa-secondary text-sm">Gerando QR…</div>`}
            <div class=${HINT}>O QR expira em ~20s e é renovado automaticamente.</div>
          </div>`}
      </div>
    </div>
  `;
}
