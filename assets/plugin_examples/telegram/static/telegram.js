// Tela de configuração do plugin Telegram (config:true).
// Renderizada DENTRO do modal "Configurar" do card em /plugins.
// Ajuda o operador a: criar o bot no @BotFather, validar o token de um canal
// (getMe) e — no modo webhook — registrar a URL do webhook na Bot API.
//
// O canal em si (com o bot_token) é criado na tela "Canais" do core. Dark mode:
// classes semânticas wa-* e .wa-field (legível nos dois temas).
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
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

// Copy `text` to the clipboard with a non-secure-context fallback. The async
// Clipboard API (navigator.clipboard) is UNDEFINED over plain HTTP (e.g. the
// panel on a LAN IP like http://10.8.200.104), so fall back to a temporary
// textarea + execCommand('copy') — the same pattern the core uses.
function copyText(text, onOk) {
  const fallback = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok && onOk) onOk();
    } catch (e) { /* clipboard truly unavailable */ }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => onOk && onOk()).catch(fallback);
  } else {
    fallback();
  }
}

const FIELD = 'wa-field w-full rounded px-3 py-2 text-sm border border-wa-border';
const LABEL = 'block text-sm font-medium text-wa-text mb-1';
const HINT = 'text-xs text-wa-secondary mt-1';
const BTN = 'shrink-0 px-3 py-2 rounded text-sm bg-wa-teal text-white hover:bg-wa-tealDark disabled:opacity-50';

export default function TelegramConfig({ apiBase = '/api/plugins/telegram' } = {}) {
  const [channels, setChannels] = useState([]);
  const [channelId, setChannelId] = useState('');
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [copied, setCopied] = useState(false);

  async function loadChannels() {
    try {
      const r = await apiFetch(`${apiBase}/channels`);
      const data = await r.json();
      const list = (data && data.data) || [];
      setChannels(list);
      if (!channelId && list.length) setChannelId(list[0].id);
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message || e) });
    }
  }

  async function loadStatus(cid) {
    if (!cid) { setStatus(null); return; }
    try {
      const r = await apiFetch(`${apiBase}/status?channel_id=${encodeURIComponent(cid)}`);
      const data = await r.json();
      setStatus((data && data.data) || null);
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message || e) });
    }
  }

  useEffect(() => { loadChannels(); }, [apiBase]);
  useEffect(() => { loadStatus(channelId); }, [channelId]);

  const webhookUrl = `${location.origin}/api/webhook/telegram/${channelId || '{channel_id}'}`;

  function copyWebhook() {
    copyText(webhookUrl, () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  async function action(path, body) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch(`${apiBase}/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (data.ok) {
        setMsg({ kind: 'ok', text: 'Pronto!' });
        await loadStatus(channelId);
      } else {
        setMsg({ kind: 'err', text: data.error || 'Falhou.' });
      }
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message || e) });
    } finally {
      setBusy(false);
    }
  }

  const me = status && status.me;
  const webhook = status && status.webhook;

  return html`
    <div class="p-5 max-w-2xl mx-auto text-wa-text">
      <h2 class="text-xl font-bold mb-1">Telegram</h2>
      <p class="text-sm text-wa-secondary mb-4">
        Caixa de entrada via Bot API. Crie um bot no
        <strong>@BotFather</strong> (comando <code>/newbot</code>), copie o
        <strong>token</strong> e cadastre um canal com provider
        <strong>telegram</strong> e a credencial <code>bot_token</code> na tela
        <strong>Canais</strong>. Depois é só mandar mensagem ao bot.
      </p>

      <ol class="text-sm text-wa-secondary mb-4 list-decimal pl-5 space-y-1">
        <li>No Telegram, fale com o <strong>@BotFather</strong> e use <code>/newbot</code>.</li>
        <li>Copie o token (algo como <code>123456:ABC-DEF...</code>).</li>
        <li>Em <strong>Canais</strong>, crie um canal provider <strong>telegram</strong> com a credencial <code>bot_token</code>.</li>
        <li>Modo <strong>long-poll</strong> (padrão) funciona sem host público. Para <strong>webhook</strong>, registre a URL abaixo.</li>
      </ol>

      ${msg && html`
        <div class=${'mb-3 px-3 py-2 rounded text-sm ' +
          (msg.kind === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
          ${msg.text}
        </div>`}

      <div class="space-y-4">
        <div>
          <label class=${LABEL}>Canal Telegram</label>
          ${channels.length
            ? html`<select class=${FIELD} value=${channelId}
                onChange=${(e) => setChannelId(e.target.value)}>
                ${channels.map((c) => html`<option value=${c.id}>${c.display_name} (${c.id})</option>`)}
              </select>`
            : html`<div class=${HINT}>Nenhum canal telegram ainda — crie um na tela <strong>Canais</strong>.</div>`}
        </div>

        ${channelId && html`
          <div class="px-3 py-2 rounded bg-wa-bg border border-wa-border text-xs text-wa-secondary">
            ${status && status.configured === false
              ? html`<span>Sem <code>bot_token</code> cadastrado para este canal.</span>`
              : me
                ? html`<span>Bot conectado: <strong>@${me.username}</strong> (${me.first_name}) ·
                    Webhook: <strong>${webhook && webhook.url ? webhook.url : 'não registrado (long-poll)'}</strong></span>`
                : html`<span>Token inválido ou indisponível${status && status.me_error ? html` — ${status.me_error}` : ''}.</span>`}
          </div>`}

        <div>
          <label class=${LABEL}>URL de webhook (modo webhook)</label>
          <div class="flex gap-2">
            <input class=${FIELD + ' font-mono'} readonly value=${webhookUrl} />
            <button class=${BTN} onClick=${copyWebhook}>${copied ? 'Copiado!' : 'Copiar'}</button>
          </div>
          <div class=${HINT}>
            Exige host público (HTTPS). No desktop/EXE prefira o modo long-poll
            (padrão) — não precisa de webhook.
          </div>
        </div>

        ${channelId && html`
          <div class="flex flex-wrap gap-2">
            <button class=${BTN} disabled=${busy} onClick=${() => loadStatus(channelId)}>Validar token</button>
            <button class=${BTN} disabled=${busy}
              onClick=${() => action('set-webhook', { channel_id: channelId, url: webhookUrl })}>
              Registrar webhook
            </button>
            <button class="shrink-0 px-3 py-2 rounded text-sm border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-50"
              disabled=${busy}
              onClick=${() => action('delete-webhook', { channel_id: channelId })}>
              Remover webhook (usar long-poll)
            </button>
          </div>`}
      </div>
    </div>
  `;
}
