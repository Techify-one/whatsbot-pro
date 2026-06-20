// Channels management screen (plano 02 Fase 2). Full-page CRUD.
// Lists messaging channels in cards (display_name, provider badge,
// connected/logged-in status, own_phone, last_error) with per-card actions
// (enable/disable, refresh status, delete). A modal creates a new channel:
// pick a provider, an id (snake_case), a display name, and the provider's
// credential fields. After creating a whatsapp_cloud channel the modal shows
// the webhook URL to paste into the Meta App configuration.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  listChannels,
  createChannel,
  updateChannel,
  deleteChannel,
  getChannelStatus,
} from '../services/api.js';

const html = htm.bind(h);

const ID_RE = /^[a-z][a-z0-9_]*$/;

// Provider catalogue: label + accent tint (only classes covered by custom.css
// dark overrides — green/blue/gray/purple at -50/-700). `gowa` is the local
// WhatsApp bridge; `whatsapp_cloud` is Meta's Cloud API; `test` is a no-op.
const PROVIDERS = {
  gowa: { label: 'GOWA', tint: 'bg-green-50 text-green-700' },
  whatsapp_cloud: { label: 'WhatsApp Cloud', tint: 'bg-blue-50 text-blue-700' },
  telegram: { label: 'Telegram', tint: 'bg-purple-50 text-purple-700' },
  test: { label: 'Teste', tint: 'bg-gray-100 text-wa-secondary' },
};

function providerMeta(provider) {
  return PROVIDERS[provider] || { label: provider || '—', tint: 'bg-gray-100 text-wa-secondary' };
}

// Random URL-safe token, used for the "sugerir" verify-token button.
function randomToken(len = 32) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  try {
    const arr = new Uint32Array(len);
    crypto.getRandomValues(arr);
    for (let i = 0; i < len; i++) out += chars[arr[i] % chars.length];
  } catch (e) {
    for (let i = 0; i < len; i++) out += chars[Math.floor(Math.random() * chars.length)];
  }
  return out;
}

function Dot({ on }) {
  return html`<span class="inline-block w-2 h-2 rounded-full ${on ? 'bg-green-500' : 'bg-gray-400'}"></span>`;
}

// ── Create-channel modal ────────────────────────────────────────────
function ChannelForm({ onCreated, onCancel, busy, error }) {
  const [provider, setProvider] = useState('gowa');
  const [id, setId] = useState('');
  const [displayName, setDisplayName] = useState('');
  // Provider-specific credential/config fields.
  const [gowaDeviceId, setGowaDeviceId] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [verifyToken, setVerifyToken] = useState('');
  const [appSecret, setAppSecret] = useState('');

  const idErr = id && !ID_RE.test(id)
    ? 'Use apenas letras minúsculas, números e _ (começando por letra).'
    : '';

  const canSave = !busy && id.trim() && !idErr && displayName.trim();

  function buildPayload() {
    const payload = {
      id: id.trim(),
      provider,
      display_name: displayName.trim(),
    };
    if (provider === 'gowa') {
      if (gowaDeviceId.trim()) payload.config = { gowa_device_id: gowaDeviceId.trim() };
    } else if (provider === 'whatsapp_cloud') {
      const credentials = {};
      if (accessToken.trim()) credentials.access_token = accessToken.trim();
      if (phoneNumberId.trim()) credentials.phone_number_id = phoneNumberId.trim();
      if (verifyToken.trim()) credentials.verify_token = verifyToken.trim();
      if (appSecret.trim()) credentials.app_secret = appSecret.trim();
      if (Object.keys(credentials).length) payload.credentials = credentials;
    }
    return payload;
  }

  function submit() {
    if (!canSave) return;
    onCreated(buildPayload());
  }

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">Novo canal</div>
      <div class="flex flex-col gap-3">
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Provider</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            value=${provider} onChange=${(e) => setProvider(e.target.value)} disabled=${busy}>
            ${Object.entries(PROVIDERS).map(([key, meta]) => html`
              <option key=${key} value=${key}>${meta.label}</option>
            `)}
          </select>
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">ID</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" placeholder="ex: whatsapp_principal" value=${id}
            onInput=${(e) => setId(e.target.value)} />
          ${idErr ? html`<div class="text-[12px] text-red-500 mt-1">${idErr}</div>` : null}
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" placeholder="ex: Atendimento WhatsApp" value=${displayName}
            onInput=${(e) => setDisplayName(e.target.value)} />
        </div>

        ${provider === 'gowa' ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">GOWA Device ID <span class="text-wa-secondary">(opcional)</span></label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" placeholder="device id do GOWA" value=${gowaDeviceId}
              onInput=${(e) => setGowaDeviceId(e.target.value)} />
          </div>
        ` : null}

        ${provider === 'whatsapp_cloud' ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Access Token</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="password" placeholder="EAAB..." value=${accessToken}
              onInput=${(e) => setAccessToken(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Phone Number ID</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" placeholder="ID do número (Meta)" value=${phoneNumberId}
              onInput=${(e) => setPhoneNumberId(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Verify Token</label>
            <div class="flex gap-2">
              <input class="wa-field flex-1 px-3 py-2 rounded-md text-[14px]"
                type="text" placeholder="token de verificação do webhook" value=${verifyToken}
                onInput=${(e) => setVerifyToken(e.target.value)} />
              <button type="button"
                class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
                onClick=${() => setVerifyToken(randomToken())}>Sugerir</button>
            </div>
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">App Secret <span class="text-wa-secondary">(opcional)</span></label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="password" placeholder="App Secret do app Meta" value=${appSecret}
              onInput=${(e) => setAppSecret(e.target.value)} />
          </div>
        ` : null}

        ${error ? html`<div class="text-[13px] text-red-500">${error}</div>` : null}

        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${submit} disabled=${!canSave}>${busy ? 'Criando…' : 'Criar canal'}</button>
        </div>
      </div>
    </div>
  `;
}

// ── Webhook-URL notice (shown after creating a whatsapp_cloud channel) ──
function WebhookNotice({ channelId, onDismiss }) {
  const url = `${window.location.origin}/api/webhook/whatsapp_cloud/${channelId}`;
  const [copied, setCopied] = useState(false);
  function copy() {
    try {
      navigator.clipboard.writeText(url).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    } catch (e) { /* clipboard may be unavailable */ }
  }
  return html`
    <div class="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-blue-700 mb-1">Canal criado</div>
      <p class="text-[13px] text-wa-text mb-2">
        Cole esta URL como <span class="font-medium">Callback URL</span> na configuração de webhook do seu app na Meta:
      </p>
      <div class="flex gap-2 items-center flex-wrap">
        <code class="flex-1 min-w-0 break-all px-3 py-2 rounded-md text-[13px] bg-wa-bg border border-wa-border text-wa-text">${url}</code>
        <button class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
          onClick=${copy}>${copied ? 'Copiado!' : 'Copiar'}</button>
      </div>
      <div class="flex justify-end mt-3">
        <button class="px-3 py-1.5 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
          onClick=${onDismiss}>Fechar</button>
      </div>
    </div>
  `;
}

// ── Single channel card ─────────────────────────────────────────────
function ChannelCard({ channel, onToggle, onDelete, onRefresh, busyId }) {
  const meta = providerMeta(channel.provider);
  const cred = channel.credentials || {};
  const credEntries = Object.entries(cred);
  const busy = busyId === channel.id;

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3 flex-wrap">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-[14px] text-wa-text font-medium truncate">${channel.display_name || channel.id}</span>
          <span class="px-2 py-0.5 rounded-full text-[11px] ${meta.tint}">${meta.label}</span>
          ${channel.enabled
            ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-50 text-green-700">Ativo</span>`
            : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Inativo</span>`}
        </div>
        <div class="text-[12px] text-wa-secondary mt-0.5 font-mono break-words">${channel.id}</div>

        <div class="flex items-center gap-4 mt-2 flex-wrap">
          <span class="flex items-center gap-1.5 text-[12px] text-wa-secondary">
            <${Dot} on=${channel.connected} /> ${channel.connected ? 'Conectado' : 'Desconectado'}
          </span>
          <span class="flex items-center gap-1.5 text-[12px] text-wa-secondary">
            <${Dot} on=${channel.logged_in} /> ${channel.logged_in ? 'Autenticado' : 'Não autenticado'}
          </span>
          ${channel.own_phone ? html`
            <span class="text-[12px] text-wa-secondary">📱 ${channel.own_phone}</span>
          ` : null}
        </div>

        ${credEntries.length ? html`
          <div class="text-[12px] text-wa-secondary mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
            ${credEntries.map(([k, v]) => html`
              <span key=${k}><span class="text-wa-text">${k}:</span> <span class="font-mono">${v}</span></span>
            `)}
          </div>
        ` : null}

        ${channel.last_error ? html`
          <div class="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-md px-2 py-1 mt-2 break-words">
            ${channel.last_error}
          </div>
        ` : null}
      </div>

      <div class="flex gap-1 shrink-0 flex-wrap justify-end">
        <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
          onClick=${() => onRefresh(channel)} disabled=${busy}>
          ${busy ? '…' : 'Atualizar'}</button>
        <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
          onClick=${() => onToggle(channel)} disabled=${busy}>
          ${channel.enabled ? 'Desativar' : 'Ativar'}</button>
        <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors disabled:opacity-50"
          onClick=${() => onDelete(channel)} disabled=${busy}>Excluir</button>
      </div>
    </div>
  `;
}

export default function ChannelsManager() {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState('');
  const [busyId, setBusyId] = useState('');
  // {id} of a just-created whatsapp_cloud channel — shows the webhook notice.
  const [webhookFor, setWebhookFor] = useState(null);

  async function load() {
    setLoading(true);
    setError('');
    const res = await listChannels();
    if (res && res.ok) setChannels((res.data && res.data.channels) || res.data || []);
    else setError((res && res.error) || 'Falha ao carregar canais.');
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleCreate(payload) {
    setCreateBusy(true); setCreateError('');
    const res = await createChannel(payload);
    setCreateBusy(false);
    if (res && res.ok) {
      setCreating(false);
      if (payload.provider === 'whatsapp_cloud') setWebhookFor(payload.id);
      load();
    } else {
      setCreateError((res && res.error) || 'Falha ao criar o canal.');
    }
  }

  async function handleToggle(channel) {
    setBusyId(channel.id); setError('');
    const res = await updateChannel(channel.id, { enabled: !channel.enabled });
    setBusyId('');
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao atualizar o canal.');
  }

  async function handleDelete(channel) {
    if (!confirm(`Excluir o canal "${channel.display_name || channel.id}"? Esta ação não pode ser desfeita.`)) return;
    setBusyId(channel.id); setError('');
    const res = await deleteChannel(channel.id);
    setBusyId('');
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao excluir o canal.');
  }

  // Refresh just this channel's live status and merge it into the card.
  async function handleRefresh(channel) {
    setBusyId(channel.id); setError('');
    const res = await getChannelStatus(channel.id);
    setBusyId('');
    if (res && res.ok && res.data) {
      const s = res.data;
      setChannels(prev => prev.map(c => c.id === channel.id
        ? { ...c, connected: !!s.connected, logged_in: !!s.logged_in, last_error: s.error || null }
        : c));
    } else {
      setError((res && res.error) || 'Falha ao consultar o status do canal.');
    }
  }

  return html`
    <div>
      <div class="flex items-center justify-between mb-4 gap-3">
        <p class="text-[13px] text-wa-secondary">
          Canais de mensagens conectados ao WhatsBot. Cada canal usa um provider
          (GOWA, WhatsApp Cloud, Telegram ou Teste) com suas próprias credenciais.
        </p>
        ${!creating ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setCreateError(''); setError(''); }}>+ Adicionar canal</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${webhookFor ? html`<${WebhookNotice} channelId=${webhookFor} onDismiss=${() => setWebhookFor(null)} />` : null}

      ${creating ? html`<${ChannelForm}
        onCreated=${handleCreate}
        onCancel=${() => setCreating(false)}
        busy=${createBusy}
        error=${createError} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && channels.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhum canal configurado ainda. Clique em <span class="font-medium">+ Adicionar canal</span> para começar.
        </div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${channels.map(channel => html`
          <${ChannelCard}
            key=${channel.id}
            channel=${channel}
            onToggle=${handleToggle}
            onDelete=${handleDelete}
            onRefresh=${handleRefresh}
            busyId=${busyId} />
        `)}
      </div>
    </div>
  `;
}
