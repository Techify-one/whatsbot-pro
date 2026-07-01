// Channels management screen (plano 02 Fase 2). Full-page CRUD.
// Lists messaging channels in cards (display_name, provider badge,
// connected/logged-in status, own_phone, last_error) with per-card actions
// (enable/disable, refresh status, delete). A modal creates a new channel:
// pick a provider, an id (snake_case), a display name, and the provider's
// credential fields. After creating a whatsapp_cloud channel the modal shows
// the webhook URL to paste into the Meta App configuration.
//
// Plano 23 · D4 — decomposed: this file is now the thin container (data fetching
// + CRUD orchestration + layout). The presentational pieces live in
// components/channels/* (ChannelForm, ChannelEditForm, ChannelCard, QRConnect,
// JidTypePicker, AiSettingsFields, AgentPicker, notices) and the pure helpers in
// components/channels/constants.js. The provider list {gowa, whatsapp_cloud,
// telegram, test} and every route call (telegramAutoconfigure/status, …) are
// preserved exactly.

import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import {
  listChannels,
  listArchivedChannels,
  restoreChannel,
  createChannel,
  updateChannel,
  deleteChannel,
  getChannelStatus,
  channelReconnect,
  channelLogout,
  setChannelMembers,
  listChannelProviders,
  telegramAutoconfigure,
  getConfig,
} from '../services/api.js';
import { useDeepLink } from '../hooks/useDeepLink.js';
import { useUrlState } from '../hooks/useUrlState.js';
import { readParams, writeParams, enumStr, bool } from '../services/urlState.js';
import { CopyLinkButton } from '../utils/copyDeepLink.js';
import { PROVIDERS, aiDefaultsFrom } from './channels/constants.js';
import { ChannelForm } from './channels/ChannelForm.js';
import { ChannelEditForm } from './channels/ChannelEditForm.js';
import { ChannelCard } from './channels/ChannelCard.js';
import { QRConnect } from './channels/QRConnect.js';
import { WebhookNotice, TelegramWebhookNotice, PurgeChannelModal } from './channels/notices.js';

const html = htm.bind(h);

// Deep-link do estado da tela (Plano 24) — flags de query sobre o path
// /channels/{id} (ou /channels/new). `provider` pré-seleciona o form de criação;
// connect/webhook/telegram (mutuamente exclusivas) reabrem o modal do canal do
// path. `archived` abre a seção de arquivados. Serialize omite defaults → URL limpa.
const PROVIDER_KEYS = new Set(['gowa', 'whatsapp_cloud', 'telegram', 'test']);
const CHANNELS_URL_SCHEMA = [
  enumStr('provider', ''),   // pré-seleção do form de criação (/channels/new)
  bool('connect'),           // reabre o QR/conexão do canal do path
  bool('webhook'),           // reabre o aviso de webhook (whatsapp_cloud)
  bool('telegram'),          // reabre o aviso do Telegram
  bool('archived'),          // seção de canais arquivados
];

export default function ChannelsManager({ initialEntity }) {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  // Provider pré-selecionado no form de criação (deep-link /channels/new?provider=).
  const [initialProvider, setInitialProvider] = useState('');
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState('');
  const [busyId, setBusyId] = useState('');
  // Canais arquivados (soft-delete) + visibilidade da seção de restauração.
  const [archived, setArchived] = useState([]);
  const [showArchived, setShowArchived] = useState(false);
  // {id} of a just-created whatsapp_cloud channel — shows the webhook notice.
  const [webhookFor, setWebhookFor] = useState(null);
  const [telegramNotice, setTelegramNotice] = useState(null);
  // {id, display_name} of the GOWA channel whose QR-connect panel is open.
  const [connectFor, setConnectFor] = useState(null);
  // The channel object being edited (display info + inbox agents), or null.
  const [editingChannel, setEditingChannel] = useState(null);
  // The channel pending hard-delete confirmation, or null.
  const [purgeTarget, setPurgeTarget] = useState(null);
  // Per-channel AI defaults, derived from the global config (plano 21): a new
  // channel inherits the values that used to be global.
  const [aiDefaults, setAiDefaults] = useState(() => aiDefaultsFrom({}));
  // Providers offered in the "Novo canal" picker — only those whose plugin is
  // enabled (null while loading → ChannelForm shows the full catalogue).
  const [providers, setProviders] = useState(null);
  // Required credentials per provider (capability-driven, from the providers
  // fetch) — gates the create form and flags zombie channels on the cards.
  const [requiredCreds, setRequiredCreds] = useState({});
  const channelsRef = useRef([]);
  channelsRef.current = channels;

  // Deep-link /channels/<id>: a URL reflete o canal aberto no editor. Também
  // resolve /channels/new (form de criação, com ?provider=) e as flags de modal
  // (?connect|?webhook|?telegram=1) sobre um canal existente — lidas do search
  // aqui porque só neste ponto (ready + lista carregada) o canal do path resolve.
  const pushUrl = useDeepLink({
    tab: 'channels',
    resolve: initialEntity ? { id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      // Fecha o que estava aberto por deep-link ao sair da entidade.
      if (!sel) { setEditingChannel(null); setCreating(false); return; }
      // /channels/new → form de criação (não é um canal real; trate antes do find).
      if (sel.id === 'new') {
        const q = readParams(window.location.search, CHANNELS_URL_SCHEMA);
        if (PROVIDER_KEYS.has(q.provider)) setInitialProvider(q.provider);
        setEditingChannel(null);
        setCreateError(''); setError('');
        setCreating(true);
        return;
      }
      const c = channels.find(ch => ch.id === sel.id);
      if (!c) return;
      setEditingChannel(c);
      // Flags de modal sobre o canal do path (mutuamente exclusivas: só a 1ª vale).
      const q = readParams(window.location.search, CHANNELS_URL_SCHEMA);
      if (q.connect) setConnectFor({ id: c.id, display_name: c.display_name || c.id });
      else if (q.webhook) setWebhookFor(c.id);
      else if (q.telegram) setTelegramNotice({ channel_id: c.id, deep_link: true });
    },
  });

  // Espelha o estado de tela na query (Plano 24). Hidrata só `archived` (as flags
  // de modal dependem da lista + do canal do path, resolvidas no open do
  // useDeepLink acima); serialize reflete tudo. replaceState → sem poluir histórico.
  useUrlState({
    read: () => readParams(window.location.search, CHANNELS_URL_SCHEMA),
    apply: (s) => { setShowArchived(s.archived); },
    serialize: () => writeParams({
      // provider só faz sentido no path /channels/new (form de criação aberto).
      provider: creating ? initialProvider : '',
      connect: !!connectFor,
      webhook: !!webhookFor,
      telegram: !!telegramNotice,
      archived: showArchived,
    }, CHANNELS_URL_SCHEMA),
    deps: [creating, initialProvider, connectFor, webhookFor, telegramNotice, showArchived],
  });

  useEffect(() => {
    let alive = true;
    (async () => {
      const res = await getConfig();
      if (alive && res && res.ok) setAiDefaults(aiDefaultsFrom(res.data));
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      const res = await listChannelProviders();
      if (alive && res && res.ok) {
        setProviders(res.data.providers || []);
        setRequiredCreds(res.data.required_credentials || {});
      }
    })();
    return () => { alive = false; };
  }, []);

  async function load() {
    setLoading(true);
    setError('');
    const res = await listChannels();
    if (res && res.ok) setChannels((res.data && res.data.channels) || res.data || []);
    else setError((res && res.error) || 'Falha ao carregar canais.');
    const arc = await listArchivedChannels();
    if (arc && arc.ok) setArchived((arc.data && arc.data.channels) || arc.data || []);
    setLoading(false);
    refreshStatuses();
  }

  async function handleRestore(channel) {
    setBusyId(channel.id); setError('');
    const res = await restoreChannel(channel.id);
    setBusyId('');
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao restaurar o canal.');
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
      setInitialProvider('');
      // Sai de /channels/new e ancora as flags de modal pós-criação no canal novo
      // (/channels/{newId}?connect|webhook|telegram=1 via useUrlState).
      pushUrl(newId ? { id: newId } : null);
      if (payload.provider === 'whatsapp_cloud') setWebhookFor(newId);
      // GOWA: open the QR-connect panel immediately so the user can scan it.
      if (payload.provider === 'gowa') {
        setConnectFor({ id: newId, display_name: created.display_name || payload.display_name });
      }
      // Telegram: auto-detect a public domain and register the webhook (or fall
      // back to long-poll), then show the resulting webhook URL so the operator
      // can copy it / confirm — no need to open the plugin config.
      if (payload.provider === 'telegram' && newId) {
        const auto = await telegramAutoconfigure(newId);
        if (auto && auto.ok && auto.data) {
          setTelegramNotice(auto.data);
        } else {
          // Autoconfigure failed (plugin off?): still show the webhook URL with a
          // long-poll fallback so the inbox isn't left in limbo.
          setTelegramNotice({
            mode: 'poll', registered: false,
            reason: (auto && auto.error) || 'plugin Telegram indisponível',
            webhook_url: `${window.location.origin}/api/webhook/telegram/${newId}`,
          });
        }
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

  // Abre o form de criação e reflete /channels/new na URL (o ?provider= é
  // acrescentado pelo useUrlState quando initialProvider muda). pushState = entra
  // no histórico (voltar fecha o form). Fechar volta para /channels.
  function openCreate() {
    setCreateError(''); setError('');
    setCreating(true);
    pushUrl({ id: 'new' });
  }
  function closeCreate() {
    setCreating(false);
    setInitialProvider('');
    pushUrl(null);
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
    if (!confirm(`Arquivar o canal "${channel.display_name || channel.id}"? Ele sai da lista, mas o histórico de atendimentos é preservado e pode ser restaurado depois.`)) return;
    setBusyId(channel.id); setError('');
    const res = await deleteChannel(channel.id);
    setBusyId('');
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao arquivar o canal.');
  }

  // Hard-delete (purge): abre o modal de confirmação.
  function handlePurge(channel) {
    setError('');
    setPurgeTarget(channel);
  }

  async function confirmPurge() {
    const channel = purgeTarget;
    if (!channel) return;
    setPurgeTarget(null);
    setBusyId(channel.id); setError('');
    const res = await deleteChannel(channel.id, { purge: true });
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

  // Reconnect a paired-but-offline GOWA device (no QR); refresh dots after.
  async function handleReconnect(channel) {
    setBusyId(channel.id); setError('');
    const res = await channelReconnect(channel.id);
    setBusyId('');
    if (res && res.ok) refreshStatuses();
    else setError((res && res.error) || 'Falha ao reconectar o canal.');
  }

  // Log the device out of WhatsApp — destructive, so confirm first.
  async function handleLogout(channel) {
    if (!confirm('Desconectar este número do WhatsApp? Vai precisar ler o QR de novo pra reconectar.')) return;
    setBusyId(channel.id); setError('');
    const res = await channelLogout(channel.id);
    setBusyId('');
    if (res && res.ok) refreshStatuses();
    else setError((res && res.error) || 'Falha ao desconectar o canal.');
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
            onClick=${openCreate}>+ Adicionar canal</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${webhookFor ? html`<${WebhookNotice} channelId=${webhookFor} onDismiss=${() => setWebhookFor(null)} />` : null}
      ${telegramNotice ? html`<${TelegramWebhookNotice} result=${telegramNotice} onDismiss=${() => setTelegramNotice(null)} />` : null}

      ${connectFor ? html`<${QRConnect}
        channelId=${connectFor.id}
        displayName=${connectFor.display_name}
        onClose=${() => { setConnectFor(null); refreshStatuses(); }} />` : null}

      ${creating ? html`<${ChannelForm}
        onCreated=${handleCreate}
        onCancel=${closeCreate}
        onProviderChange=${setInitialProvider}
        initialProvider=${initialProvider}
        busy=${createBusy}
        error=${createError}
        aiDefaults=${aiDefaults}
        availableProviders=${providers}
        requiredCreds=${requiredCreds} />` : null}

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
            onPurge=${handlePurge}
            onRefresh=${handleRefresh}
            onConnect=${handleConnect}
            onReconnect=${handleReconnect}
            onLogout=${handleLogout}
            onEdit=${handleEdit}
            busyId=${busyId}
            requiredCreds=${requiredCreds} />
        `)}
      </div>

      ${archived.length ? html`
        <div class="mt-6 border-t border-wa-border pt-3">
          <button class="text-[13px] text-wa-secondary hover:text-wa-text transition-colors"
            onClick=${() => setShowArchived(v => !v)}>
            ${showArchived ? '▾' : '▸'} Canais arquivados (${archived.length})
          </button>
          ${showArchived ? html`
            <div class="flex flex-col gap-2 mt-2">
              ${archived.map(channel => html`
                <div key=${channel.id}
                  class="flex items-center justify-between gap-3 px-3 py-2 rounded-md bg-wa-panel border border-wa-border">
                  <div class="min-w-0">
                    <div class="text-[14px] text-wa-text truncate">${channel.display_name || channel.id}</div>
                    <div class="text-[12px] text-wa-secondary truncate">
                      ${(PROVIDERS[channel.provider] || {}).label || channel.provider} · arquivado
                    </div>
                  </div>
                  <button class="px-2 py-1 rounded-md text-[13px] text-wa-teal hover:bg-wa-hover transition-colors disabled:opacity-50 shrink-0"
                    onClick=${() => handleRestore(channel)} disabled=${busyId === channel.id}>
                    ${busyId === channel.id ? '…' : 'Restaurar'}</button>
                </div>
              `)}
            </div>
          ` : null}
        </div>
      ` : null}

      ${editingChannel ? html`<${ChannelEditForm}
        channel=${editingChannel}
        aiDefaults=${aiDefaults}
        onCancel=${() => { setEditingChannel(null); pushUrl(null); }}
        onSaved=${() => { setEditingChannel(null); pushUrl(null); load(); }} />` : null}

      ${purgeTarget ? html`<${PurgeChannelModal}
        channel=${purgeTarget}
        onCancel=${() => setPurgeTarget(null)}
        onConfirm=${confirmPurge} />` : null}
    </div>
  `;
}
