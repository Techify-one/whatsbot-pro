// Channels management screen (plano 02 Fase 2). Full-page CRUD.
// Lists messaging channels in cards (display_name, provider badge,
// connected/logged-in status, own_phone, last_error) with per-card actions
// (enable/disable, refresh status, delete). A modal creates a new channel:
// pick a provider, an id (snake_case), a display name, and the provider's
// credential fields. After creating a whatsapp_cloud channel the modal shows
// the webhook URL to paste into the Meta App configuration.

import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import {
  listChannels,
  createChannel,
  updateChannel,
  deleteChannel,
  getChannelStatus,
  getChannelQR,
  getChannelMembers,
  setChannelMembers,
  listChannelAssignableUsers,
} from '../services/api.js';
import { useDeepLink } from '../hooks/useDeepLink.js';

const html = htm.bind(h);

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

// Which WhatsApp chat types (by JID suffix) a GOWA channel surfaces as
// conversations. The keys are stable identifiers persisted in the channel's
// config (config.allowed_jid_types) and mirror channels/jid.py on the backend.
// The user never sees the JID — only the friendly label.
const JID_TYPES = [
  { key: 'person', label: 'Pessoa (contato)', hint: 'Conversas individuais com contatos.' },
  { key: 'person_lid', label: 'Pessoa (modo privacidade)', hint: 'Contatos que ocultam o número (modo privacidade).' },
  { key: 'group', label: 'Grupo / Comunidade', hint: 'Mensagens de grupos e comunidades.' },
  { key: 'newsletter', label: 'Canal', hint: 'Publicações de Canais do WhatsApp.' },
  { key: 'broadcast', label: 'Status / Transmissão', hint: 'Status e listas de transmissão.' },
  { key: 'bot', label: 'Bot (Meta AI etc.)', hint: 'Conversas com bots como o Meta AI.' },
];
const DEFAULT_JID_TYPES = ['person', 'person_lid', 'group'];

// Parse a channel's `config` (the API returns it as a JSON string) into an
// object, tolerating already-parsed objects and malformed values.
function parseChannelConfig(config) {
  if (!config) return {};
  if (typeof config === 'object') return config;
  try { return JSON.parse(config) || {}; } catch (e) { return {}; }
}

// Checkbox group letting the user pick which chat types fall into a GOWA channel.
function JidTypePicker({ selected, onChange, disabled }) {
  function toggle(key) {
    const set = new Set(selected);
    if (set.has(key)) set.delete(key); else set.add(key);
    // Preserve canonical order.
    onChange(JID_TYPES.map(t => t.key).filter(k => set.has(k)));
  }
  return html`
    <div class="flex flex-col gap-2">
      ${JID_TYPES.map(t => html`
        <label key=${t.key} class="flex items-start gap-2 cursor-pointer ${disabled ? 'opacity-60 cursor-not-allowed' : ''}">
          <input type="checkbox" class="mt-0.5" checked=${selected.includes(t.key)}
            disabled=${disabled} onChange=${() => toggle(t.key)} />
          <span class="flex flex-col">
            <span class="text-[13px] text-wa-text">${t.label}</span>
            <span class="text-[11px] text-wa-secondary">${t.hint}</span>
          </span>
        </label>
      `)}
    </div>
  `;
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
  const [displayName, setDisplayName] = useState('');
  // Provider-specific credential/config fields.
  // The GOWA device id is auto-generated and read-only: each channel maps to its
  // own GOWA device (one WhatsApp number) on the shared GOWA process.
  const [gowaDeviceId, setGowaDeviceId] = useState(() => `gowa_${randomToken(10)}`);
  // Which chat types this GOWA channel surfaces (config.allowed_jid_types).
  const [jidTypes, setJidTypes] = useState(DEFAULT_JID_TYPES);
  const [accessToken, setAccessToken] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [wabaId, setWabaId] = useState('');
  const [verifyToken, setVerifyToken] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [botToken, setBotToken] = useState('');
  // Agents to assign to the new channel's inbox (all providers). Loaded once.
  const [users, setUsers] = useState([]);
  const [agentIds, setAgentIds] = useState([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      const res = await listChannelAssignableUsers();
      if (alive && res && res.ok) setUsers(res.data.users || []);
    })();
    return () => { alive = false; };
  }, []);

  // O ID do canal é gerado automaticamente pelo backend (o usuário só escolhe o
  // nome de exibição). GOWA reusa o device id; demais providers, "<provider>_<hex>".
  const canSave = !busy && displayName.trim();

  function buildPayload() {
    const payload = {
      provider,
      display_name: displayName.trim(),
    };
    if (provider === 'gowa') {
      payload.config = { allowed_jid_types: jidTypes };
      if (gowaDeviceId.trim()) payload.config.gowa_device_id = gowaDeviceId.trim();
    } else if (provider === 'whatsapp_cloud') {
      const credentials = {};
      if (accessToken.trim()) credentials.access_token = accessToken.trim();
      if (phoneNumberId.trim()) credentials.phone_number_id = phoneNumberId.trim();
      if (wabaId.trim()) credentials.waba_id = wabaId.trim();
      if (verifyToken.trim()) credentials.verify_token = verifyToken.trim();
      if (appSecret.trim()) credentials.app_secret = appSecret.trim();
      if (Object.keys(credentials).length) payload.credentials = credentials;
    } else if (provider === 'telegram') {
      if (botToken.trim()) payload.credentials = { bot_token: botToken.trim() };
    }
    return payload;
  }

  function submit() {
    if (!canSave) return;
    onCreated(buildPayload(), agentIds);
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
          <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" placeholder="ex: Atendimento WhatsApp" value=${displayName}
            onInput=${(e) => setDisplayName(e.target.value)} />
        </div>

        ${provider === 'gowa' ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">GOWA Device ID <span class="text-wa-secondary">(gerado automaticamente)</span></label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] opacity-60 cursor-not-allowed"
              type="text" value=${gowaDeviceId} readonly disabled />
            <div class="text-[12px] text-wa-secondary mt-1">
              Identifica este número dentro do GOWA. Após criar, leia o QR Code para conectar o WhatsApp.
            </div>
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">O que deve aparecer no painel</label>
            <p class="text-[12px] text-wa-secondary mb-2">
              Escolha quais tipos de conversa deste número viram atendimento. Os tipos
              desmarcados são ignorados (não aparecem no painel).
            </p>
            <${JidTypePicker} selected=${jidTypes} onChange=${setJidTypes} disabled=${busy} />
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
            <label class="block text-[12px] text-wa-secondary mb-1">WABA ID <span class="text-wa-secondary">(para templates)</span></label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" placeholder="WhatsApp Business Account ID" value=${wabaId}
              onInput=${(e) => setWabaId(e.target.value)} />
            <div class="text-[12px] text-wa-secondary mt-1">
              Necessário para listar e criar templates (HSM). Em WhatsApp Manager → Configurações da conta. Diferente do Phone Number ID.
            </div>
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

        ${provider === 'telegram' ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Bot Token</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="password" placeholder="123456:ABC-DEF... (do @BotFather)" value=${botToken}
              onInput=${(e) => setBotToken(e.target.value)} />
            <div class="text-[12px] text-wa-secondary mt-1">
              Crie um bot com o <span class="font-medium">@BotFather</span> (<code>/newbot</code>) e cole o token.
              Recebe por long-poll (sem host público) — basta criar e mandar mensagem ao bot.
            </div>
          </div>
        ` : null}

        <div class="border-t border-wa-border pt-3">
          <label class="block text-[12px] text-wa-secondary mb-1">Agentes desta caixa de entrada</label>
          <p class="text-[12px] text-wa-secondary mb-2">
            Os agentes selecionados veem as conversas deste canal e recebem as mensagens que caírem aqui.
            Administradores veem todos os canais.
          </p>
          <${AgentPicker} users=${users} selected=${agentIds} onChange=${setAgentIds} />
        </div>

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
  function flagCopied() {
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  function fallbackCopy() {
    // navigator.clipboard is unavailable over plain HTTP (non-secure context),
    // which is the common case here (panel served on a LAN IP). Fall back to
    // the legacy execCommand approach via a temporary textarea.
    try {
      const ta = document.createElement('textarea');
      ta.value = url;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) flagCopied();
    } catch (e) { /* clipboard truly unavailable */ }
  }
  function copy() {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(flagCopied).catch(fallbackCopy);
    } else {
      fallbackCopy();
    }
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

// ── QR connect panel (GOWA) ─────────────────────────────────────────
// Polls the channel status + QR image until the device logs in. Shown right
// after creating a GOWA channel, and reopenable from a card's "Conectar".
function QRConnect({ channelId, displayName, onClose }) {
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

// ── Single channel card ─────────────────────────────────────────────
function ChannelCard({ channel, onToggle, onDelete, onRefresh, onConnect, onEdit, busyId }) {
  const meta = providerMeta(channel.provider);
  const cred = channel.credentials || {};
  const credEntries = Object.entries(cred);
  const busy = busyId === channel.id;
  const canConnect = channel.provider === 'gowa' && !channel.logged_in;

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
        ${canConnect ? html`
          <button class="px-2 py-1 rounded-md text-[13px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${() => onConnect(channel)} disabled=${busy}>Conectar</button>
        ` : null}
        <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
          onClick=${() => onEdit(channel)} disabled=${busy}>Editar</button>
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

// ── Agent multi-select (chips) ──────────────────────────────────────
// Selectable list of panel users (agents) that will see/receive this channel's
// inbox. Selected agents render as removable chips; an "add" dropdown lists the
// remaining users. Mirrors the look of the inbox "Agentes" picker.
function AgentPicker({ users, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const byId = {};
  for (const u of users) byId[u.id] = u;
  const selectedUsers = selected.map(id => byId[id]).filter(Boolean);
  const available = users.filter(u => !selected.includes(u.id));

  function add(id) {
    onChange([...selected, id]);
    if (available.length <= 1) setOpen(false);
  }
  function remove(id) {
    onChange(selected.filter(x => x !== id));
  }

  return html`
    <div class="relative">
      <div class="wa-field w-full min-h-[42px] px-2 py-1.5 rounded-md flex flex-wrap gap-1.5 items-center cursor-pointer"
        onClick=${() => { if (available.length > 0) setOpen(o => !o); }}
        title="Clique para escolher agentes">
        ${selectedUsers.length === 0
          ? html`<span class="text-[13px] text-wa-secondary px-1">Nenhum agente selecionado</span>`
          : selectedUsers.map(u => html`
            <span key=${u.id}
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[13px] bg-wa-hover text-wa-text border border-wa-border">
              ${u.name}${u.is_admin ? html`<span class="text-[11px] text-wa-secondary">admin</span>` : null}
              <button type="button" class="text-wa-secondary hover:text-red-500"
                onClick=${(e) => { e.stopPropagation(); remove(u.id); }} title="Remover">×</button>
            </span>
          `)}
        <span class="ml-auto text-[13px] text-wa-secondary px-1 shrink-0">${open ? '▲' : '▼'}</span>
      </div>
      ${open && available.length > 0 ? html`
        <div class="absolute z-10 mt-1 w-full max-h-56 overflow-auto bg-wa-panel border border-wa-border rounded-md shadow-lg">
          ${available.map(u => html`
            <button key=${u.id} type="button"
              class="w-full text-left px-3 py-2 text-[13px] text-wa-text hover:bg-wa-hover flex items-center gap-2"
              onClick=${() => add(u.id)}>
              <span class="truncate">${u.name}</span>
              <span class="text-[12px] text-wa-secondary truncate">${u.email}</span>
              ${u.is_admin ? html`<span class="ml-auto text-[11px] text-wa-secondary">admin</span>` : null}
            </button>
          `)}
        </div>
      ` : null}
    </div>
  `;
}

// ── Edit-channel modal ──────────────────────────────────────────────
// Edits the channel's display info + the agents that see its inbox. Does NOT
// reconnect or change the QR — login/session are untouched here.
function ChannelEditForm({ channel, onSaved, onCancel }) {
  const isCloud = channel.provider === 'whatsapp_cloud';
  const isGowa = channel.provider === 'gowa';
  const [displayName, setDisplayName] = useState(channel.display_name || channel.id);
  // GOWA: which chat types this channel surfaces. Seed from the stored config
  // (falling back to the default set when unset).
  const [jidTypes, setJidTypes] = useState(() => {
    const cfg = parseChannelConfig(channel.config);
    return Array.isArray(cfg.allowed_jid_types) && cfg.allowed_jid_types.length
      ? cfg.allowed_jid_types
      : DEFAULT_JID_TYPES;
  });
  // whatsapp_cloud secrets: blank means "keep current"; only non-empty is sent.
  const [accessToken, setAccessToken] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [verifyToken, setVerifyToken] = useState('');
  const [appSecret, setAppSecret] = useState('');
  // WABA ID is NOT a secret (returned in clear), so it is pre-filled and editable.
  const [wabaId, setWabaId] = useState((channel.credentials && channel.credentials.waba_id) || '');

  const [users, setUsers] = useState([]);
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    (async () => {
      const res = await getChannelMembers(channel.id);
      if (!alive) return;
      if (res && res.ok) {
        setUsers(res.data.users || []);
        setSelected(res.data.member_ids || []);
      } else {
        setError((res && res.error) || 'Falha ao carregar agentes.');
      }
      setLoading(false);
    })();
    return () => { alive = false; };
  }, [channel.id]);

  async function save() {
    if (busy || !displayName.trim()) return;
    setBusy(true); setError('');
    const payload = { display_name: displayName.trim() };
    if (isGowa) {
      // PUT replaces config wholesale — preserve gowa_device_id (and any other
      // existing keys) while updating the allowed chat types.
      const cfg = parseChannelConfig(channel.config);
      payload.config = { ...cfg, allowed_jid_types: jidTypes };
    }
    if (isCloud) {
      const credentials = {};
      if (accessToken.trim()) credentials.access_token = accessToken.trim();
      if (phoneNumberId.trim()) credentials.phone_number_id = phoneNumberId.trim();
      if (wabaId.trim()) credentials.waba_id = wabaId.trim();
      if (verifyToken.trim()) credentials.verify_token = verifyToken.trim();
      if (appSecret.trim()) credentials.app_secret = appSecret.trim();
      if (Object.keys(credentials).length) payload.credentials = credentials;
    }
    const r1 = await updateChannel(channel.id, payload);
    if (!r1 || !r1.ok) {
      setBusy(false);
      setError((r1 && r1.error) || 'Falha ao salvar o canal.');
      return;
    }
    const r2 = await setChannelMembers(channel.id, selected);
    setBusy(false);
    if (!r2 || !r2.ok) {
      setError((r2 && r2.error) || 'Canal salvo, mas falha ao salvar os agentes.');
      return;
    }
    onSaved();
  }

  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onCancel}>
      <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-md max-h-[90vh] overflow-auto"
        onClick=${(e) => e.stopPropagation()}>
        <div class="text-[15px] font-medium text-wa-text mb-1">Editar canal</div>
        <div class="text-[12px] text-wa-secondary mb-4 font-mono break-words">${channel.id}</div>

        <div class="flex flex-col gap-3">
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" value=${displayName} onInput=${(e) => setDisplayName(e.target.value)} />
          </div>

          ${isGowa ? html`
            <div class="border-t border-wa-border pt-3">
              <label class="block text-[12px] text-wa-secondary mb-1">O que deve aparecer no painel</label>
              <p class="text-[12px] text-wa-secondary mb-2">
                Escolha quais tipos de conversa deste número viram atendimento. Os tipos
                desmarcados são ignorados (não aparecem no painel).
              </p>
              <${JidTypePicker} selected=${jidTypes} onChange=${setJidTypes} disabled=${busy} />
            </div>
          ` : null}

          ${isCloud ? html`
            <div class="border-t border-wa-border pt-3">
              <label class="block text-[12px] text-wa-secondary mb-1">WABA ID <span class="text-wa-secondary">(para templates)</span></label>
              <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
                placeholder="WhatsApp Business Account ID" value=${wabaId}
                onInput=${(e) => setWabaId(e.target.value)} />
              <div class="text-[12px] text-wa-secondary mt-1 mb-3">
                Necessário para listar e criar templates (HSM). Diferente do Phone Number ID.
              </div>
              <div class="text-[12px] text-wa-secondary mb-2">Demais credenciais — deixe em branco para manter a atual.</div>
              <div class="flex flex-col gap-2">
                <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="password"
                  placeholder="Access Token (manter)" value=${accessToken}
                  onInput=${(e) => setAccessToken(e.target.value)} />
                <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
                  placeholder="Phone Number ID (manter)" value=${phoneNumberId}
                  onInput=${(e) => setPhoneNumberId(e.target.value)} />
                <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
                  placeholder="Verify Token (manter)" value=${verifyToken}
                  onInput=${(e) => setVerifyToken(e.target.value)} />
                <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="password"
                  placeholder="App Secret (manter)" value=${appSecret}
                  onInput=${(e) => setAppSecret(e.target.value)} />
              </div>
            </div>
          ` : null}

          <div class="border-t border-wa-border pt-3">
            <label class="block text-[12px] text-wa-secondary mb-1">Agentes desta caixa de entrada</label>
            <p class="text-[12px] text-wa-secondary mb-2">
              Os agentes selecionados veem as conversas deste canal e recebem as mensagens que caírem aqui.
              Administradores veem todos os canais.
            </p>
            ${loading
              ? html`<div class="text-[13px] text-wa-secondary">Carregando agentes…</div>`
              : html`<${AgentPicker} users=${users} selected=${selected} onChange=${setSelected} />`}
          </div>

          ${error ? html`<div class="text-[13px] text-red-500">${error}</div>` : null}

          <div class="flex gap-2 justify-end mt-1">
            <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
              onClick=${onCancel} disabled=${busy}>Cancelar</button>
            <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
              onClick=${save} disabled=${busy || loading || !displayName.trim()}>
              ${busy ? 'Salvando…' : 'Salvar'}</button>
          </div>
        </div>
      </div>
    </div>
  `;
}

export default function ChannelsManager({ initialEntity }) {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState('');
  const [busyId, setBusyId] = useState('');
  // {id} of a just-created whatsapp_cloud channel — shows the webhook notice.
  const [webhookFor, setWebhookFor] = useState(null);
  // {id, display_name} of the GOWA channel whose QR-connect panel is open.
  const [connectFor, setConnectFor] = useState(null);
  // The channel object being edited (display info + inbox agents), or null.
  const [editingChannel, setEditingChannel] = useState(null);
  const channelsRef = useRef([]);
  channelsRef.current = channels;

  // Deep-link /channels/<id>: a URL reflete o canal aberto no editor.
  const pushUrl = useDeepLink({
    tab: 'channels',
    resolve: initialEntity ? { id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel) { setEditingChannel(null); return; }
      const c = channels.find(ch => ch.id === sel.id);
      if (c) setEditingChannel(c);
    },
  });

  async function load() {
    setLoading(true);
    setError('');
    const res = await listChannels();
    if (res && res.ok) setChannels((res.data && res.data.channels) || res.data || []);
    else setError((res && res.error) || 'Falha ao carregar canais.');
    setLoading(false);
    refreshStatuses();
  }

  // Pull live connected/logged-in status for every channel and merge into the
  // cards (so they reflect the real GOWA session, not just the stored flags).
  async function refreshStatuses() {
    const list = channelsRef.current;
    if (!list || !list.length) return;
    const results = await Promise.all(
      list.map(c => getChannelStatus(c.id).then(r => [c.id, r]).catch(() => [c.id, null]))
    );
    const byId = {};
    for (const [id, r] of results) {
      if (r && r.ok && r.data) byId[id] = r.data;
    }
    setChannels(prev => prev.map(c => byId[c.id]
      ? { ...c, connected: !!byId[c.id].connected, logged_in: !!byId[c.id].logged_in,
          own_phone: byId[c.id].own_phone || c.own_phone, last_error: byId[c.id].error || null }
      : c));
  }

  useEffect(() => { load(); }, []);

  // Keep card statuses live while the screen is open.
  useEffect(() => {
    const t = setInterval(refreshStatuses, 8000);
    return () => clearInterval(t);
  }, []);

  async function handleCreate(payload, agentIds) {
    setCreateBusy(true); setCreateError('');
    const res = await createChannel(payload);
    if (res && res.ok) {
      // O id é gerado pelo backend — usar o canal retornado, não o payload.
      const created = res.data || {};
      const newId = created.id;
      // Assign the picked agents to the new channel's inbox (best-effort: a
      // failure here never blocks creation, which already succeeded).
      if (agentIds && agentIds.length && newId) {
        await setChannelMembers(newId, agentIds);
      }
      setCreateBusy(false);
      setCreating(false);
      if (payload.provider === 'whatsapp_cloud') setWebhookFor(newId);
      // GOWA: open the QR-connect panel immediately so the user can scan it.
      if (payload.provider === 'gowa') {
        setConnectFor({ id: newId, display_name: created.display_name || payload.display_name });
      }
      load();
    } else {
      setCreateBusy(false);
      setCreateError((res && res.error) || 'Falha ao criar o canal.');
    }
  }

  function handleConnect(channel) {
    setConnectFor({ id: channel.id, display_name: channel.display_name || channel.id });
  }

  function handleEdit(channel) {
    setEditingChannel(channel);
    pushUrl({ id: channel.id });
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

      ${connectFor ? html`<${QRConnect}
        channelId=${connectFor.id}
        displayName=${connectFor.display_name}
        onClose=${() => { setConnectFor(null); refreshStatuses(); }} />` : null}

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
            onConnect=${handleConnect}
            onEdit=${handleEdit}
            busyId=${busyId} />
        `)}
      </div>

      ${editingChannel ? html`<${ChannelEditForm}
        channel=${editingChannel}
        onCancel=${() => { setEditingChannel(null); pushUrl(null); }}
        onSaved=${() => { setEditingChannel(null); pushUrl(null); load(); }} />` : null}
    </div>
  `;
}
