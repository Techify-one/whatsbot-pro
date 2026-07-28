// Tela de configuração do plugin Telegram (config:true).
// Renderizada DENTRO do modal "Configurar" do card em /plugins.
//
// SOMENTE INFORMATIVA: ajuda o operador a criar o bot no @BotFather e a validar o
// token de um canal (getMe). O modo de recebimento (webhook se houver domínio
// HTTPS público, senão long-poll) é decidido AUTOMATICAMENTE ao criar a inbox na
// tela "Canais" — não há configuração manual de webhook/long-poll aqui.
//
// Dark mode: classes semânticas wa-* e .wa-field (legível nos dois temas).
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
    setBusy(true);
    try {
      const r = await apiFetch(`${apiBase}/status?channel_id=${encodeURIComponent(cid)}`);
      const data = await r.json();
      setStatus((data && data.data) || null);
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message || e) });
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { loadChannels(); }, [apiBase]);
  // NÃO valida automaticamente: a checagem do token só roda ao clicar em "Validar
  // token". Ao trocar de canal, limpamos o status anterior (sem nova chamada).
  function selectChannel(cid) {
    setChannelId(cid);
    setStatus(null);
    setMsg(null);
  }

  const me = status && status.me;
  const webhook = status && status.webhook;
  const isWebhook = !!(status && (status.mode === 'webhook' || (webhook && webhook.url)));
  const webhookError = webhook && webhook.last_error_message;

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
        <li>O recebimento é definido <strong>automaticamente</strong> ao criar a inbox: <strong>webhook</strong> se houver domínio público (HTTPS), senão <strong>long-poll</strong>. Não há configuração manual aqui.</li>
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
                onChange=${(e) => selectChannel(e.target.value)}>
                ${channels.map((c) => html`<option value=${c.id}>${c.display_name} (${c.id})</option>`)}
              </select>`
            : html`<div class=${HINT}>Nenhum canal telegram ainda — crie um na tela <strong>Canais</strong>.</div>`}
        </div>

        ${channelId && html`
          <div class="px-3 py-2 rounded bg-wa-bg border border-wa-border text-xs text-wa-secondary">
            ${!status
              ? html`<span>Clique em <strong>Validar token</strong> para checar a conexão deste canal.</span>`
              : status.configured === false
                ? html`<span>Sem <code>bot_token</code> cadastrado para este canal.</span>`
                : me
                  ? html`<span>Bot conectado: <strong>@${me.username}</strong> (${me.first_name}) ·
                      Recebimento: <strong>${isWebhook ? 'webhook' : 'long-poll'}</strong></span>`
                  : html`<span>Token inválido ou indisponível${status.me_error ? html` — ${status.me_error}` : ''}.</span>`}
          </div>`}

        ${channelId && isWebhook && webhookError && html`
          <div class="px-3 py-2 rounded bg-red-100 text-red-700 text-sm">
            O Telegram reportou um erro de entrega no webhook: <code>${webhookError}</code>.
            Você pode ver e copiar a URL do webhook ao <strong>editar este canal</strong> na tela Canais.
          </div>`}

        ${channelId && html`
          <div>
            <button class=${BTN} disabled=${busy} onClick=${() => loadStatus(channelId)}>
              ${busy ? 'Validando…' : 'Validar token'}
            </button>
          </div>`}
      </div>
    </div>
  `;
}
