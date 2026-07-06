// @ts-check
//
// Channels — pure constants + helpers (Plano 23 · D4). Extracted verbatim from
// ChannelsManager.js so the catalogue, credential rules, JID-type table, AI
// defaults, and the create-payload builder are testable in isolation (node --test)
// and shared by the channels/* component pieces. NO Preact/HTM here — pure data.

// Provider catalogue: label + accent tint (only classes covered by custom.css
// dark overrides — green/blue/gray/purple at -50/-700). `gowa` is the local
// WhatsApp bridge; `whatsapp_cloud` is Meta's Cloud API; `test` is a no-op.
export const PROVIDERS = {
  gowa: { label: 'GOWA', tint: 'bg-green-50 text-green-700' },
  whatsapp_cloud: { label: 'WhatsApp Cloud', tint: 'bg-blue-50 text-blue-700' },
  telegram: { label: 'Telegram', tint: 'bg-purple-50 text-purple-700' },
  test: { label: 'Teste', tint: 'bg-gray-100 text-wa-secondary' },
};

export function providerMeta(provider) {
  return PROVIDERS[provider] || { label: provider || '—', tint: 'bg-gray-100 text-wa-secondary' };
}

// Credential keys each provider MUST have to ever connect (anti zombie-channel).
// The backend is the source of truth (capabilities → GET /api/channels/providers);
// this mirror only gates the form before that fetch resolves. GOWA omitted = no
// required creds (it bootstraps via QR).
export const REQUIRED_CREDS_FALLBACK = {
  whatsapp_cloud: ['access_token', 'phone_number_id', 'verify_token'],
  telegram: ['bot_token'],
};

// Friendly PT-BR label for a credential key (used in the form + card warning).
export const CRED_LABELS = {
  access_token: 'Access Token',
  phone_number_id: 'Phone Number ID',
  verify_token: 'Verify Token',
  waba_id: 'WABA ID',
  bot_token: 'Bot Token',
};
export const credLabel = (k) => CRED_LABELS[k] || k;

// Which WhatsApp chat types (by JID suffix) a GOWA channel surfaces as
// conversations. The keys are stable identifiers persisted in the channel's
// config (config.allowed_jid_types) and mirror channels/jid.py on the backend.
// The user never sees the JID — only the friendly label.
export const JID_TYPES = [
  { key: 'person', label: 'Pessoa (contato)', hint: 'Conversas individuais com contatos.' },
  { key: 'person_lid', label: 'Pessoa (modo privacidade)', hint: 'Contatos que ocultam o número (modo privacidade).' },
  { key: 'group', label: 'Grupo / Comunidade', hint: 'Mensagens de grupos e comunidades.' },
  { key: 'newsletter', label: 'Canal', hint: 'Publicações de Canais do WhatsApp.' },
  { key: 'broadcast', label: 'Status / Transmissão', hint: 'Status e listas de transmissão.' },
  { key: 'bot', label: 'Bot (Meta AI etc.)', hint: 'Conversas com bots como o Meta AI.' },
];
export const DEFAULT_JID_TYPES = ['person', 'person_lid', 'group'];

// Parse a channel's `config` (the API returns it as a JSON string) into an
// object, tolerating already-parsed objects and malformed values.
export function parseChannelConfig(config) {
  if (!config) return {};
  if (typeof config === 'object') return config;
  try { return JSON.parse(config) || {}; } catch (e) { return {}; }
}

// Build the per-channel AI override object (config.ai) seeded from the current
// GLOBAL config (plano 21). A new channel "inherits" the values that used to be
// global; ``ai_enabled`` (the per-channel master switch) defaults on.
export function aiDefaultsFrom(cfg) {
  cfg = cfg || {};
  return {
    ai_enabled: true,
    default_ai_enabled: cfg.default_ai_enabled ?? true,
    group_reply_mode: cfg.group_reply_mode ?? 'mention_only',
    image_transcription_enabled: cfg.image_transcription_enabled ?? true,
    document_transcription_enabled: cfg.document_transcription_enabled ?? true,
    audio_transcription_mode: cfg.audio_transcription_mode ?? 'received',
    audio_transcription_target: cfg.audio_transcription_target ?? 'private',
    audio_transcription_chat_prefix: cfg.audio_transcription_chat_prefix ?? '',
    max_context_messages: cfg.max_context_messages ?? 10,
    message_batch_delay: cfg.message_batch_delay ?? 3,
    split_messages: cfg.split_messages ?? true,
    split_message_delay: cfg.split_message_delay ?? 2,
    transfer_alert_enabled: cfg.transfer_alert_enabled ?? true,
    transfer_alert_duration: cfg.transfer_alert_duration ?? 5,
    ai_sequential_delay: cfg.ai_sequential_delay ?? 2,
  };
}

// Audio transcription "mode" is a multi-select set of directions to transcribe.
// Stored in config.ai.audio_transcription_mode. Backward-compatible with the
// legacy single-value strings ("received"/"sent"/"both"/"off"); the multi-select
// persists a comma-joined list ("received,sent,private"). Mirrors
// server/transcription.py:parse_audio_modes so the UI and the gate agree.
export const AUDIO_MODE_TOKENS = ['received', 'sent', 'private'];

export function parseAudioModes(raw) {
  if (raw == null) return new Set(['received']);
  const s = String(raw).trim().toLowerCase();
  if (s === 'both') return new Set(['received', 'sent']);
  if (s === '' || s === 'off' || s === 'none') return new Set();
  return new Set(s.split(',').map((t) => t.trim()).filter((t) => AUDIO_MODE_TOKENS.includes(t)));
}

export function serializeAudioModes(set) {
  const ordered = AUDIO_MODE_TOKENS.filter((t) => set.has(t));
  return ordered.length ? ordered.join(',') : 'off';
}

// Random URL-safe token, used for the "sugerir" verify-token button.
export function randomToken(len = 32) {
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

// The required-credential set for a provider: capability-driven (backend
// `requiredCreds`) with the local fallback when the providers fetch hasn't
// resolved. Pure — gates the create form and flags zombie channels on cards.
export function requiredCredsFor(provider, requiredCreds) {
  return (requiredCreds && requiredCreds[provider]) || REQUIRED_CREDS_FALLBACK[provider] || [];
}

// Required creds a given channel is MISSING (returns the keys not present in its
// stored credentials). Used by ChannelCard's zombie-channel warning.
export function missingCredsFor(channel, requiredCreds) {
  const cred = (channel && channel.credentials) || {};
  return requiredCredsFor(channel && channel.provider, requiredCreds).filter((k) => !cred[k]);
}

/**
 * Build the create-channel POST payload from the ChannelForm field values.
 * PURE: same logic the inline `buildPayload` used, lifted out so the provider
 * branching (GOWA jid types/device id, whatsapp_cloud credentials, telegram bot
 * token) and the sequential-reply default (ON for GOWA, OFF otherwise when the
 * user never touched it) are locked by tests.
 * @param {Object} f field values
 */
export function buildCreatePayload(f) {
  const provider = f.provider;
  const payload = {
    provider,
    display_name: (f.displayName || '').trim(),
  };
  // Per-channel AI settings live under config.ai for every provider (plano 21).
  // Persist the sequential-reply toggle explicitly: when the user never touched
  // it, default ON for GOWA (anti-block) and OFF for the other providers, so a
  // new non-GOWA channel doesn't inherit the backend's legacy "always on" fallback.
  const config = {
    ai: { ...f.ai, ai_sequential_enabled: f.ai.ai_sequential_enabled ?? (provider === 'gowa') },
  };
  if (provider === 'gowa') {
    config.allowed_jid_types = f.jidTypes;
    if ((f.gowaDeviceId || '').trim()) config.gowa_device_id = f.gowaDeviceId.trim();
    // Per-channel on/off for the Telegram disconnect alert (bot/chat/timezone stay
    // global in the plugin). Default ON when the field is omitted.
    config.disconnect_alert_enabled = f.gowaAlertEnabled ?? true;
  } else if (provider === 'whatsapp_cloud') {
    const credentials = {};
    if ((f.accessToken || '').trim()) credentials.access_token = f.accessToken.trim();
    if ((f.phoneNumberId || '').trim()) credentials.phone_number_id = f.phoneNumberId.trim();
    if ((f.wabaId || '').trim()) credentials.waba_id = f.wabaId.trim();
    if ((f.verifyToken || '').trim()) credentials.verify_token = f.verifyToken.trim();
    if (Object.keys(credentials).length) payload.credentials = credentials;
  } else if (provider === 'telegram') {
    if ((f.botToken || '').trim()) payload.credentials = { bot_token: f.botToken.trim() };
  }
  payload.config = config;
  return payload;
}

/**
 * Build the edit-channel PUT payload. PURE. PUT replaces config wholesale, so we
 * preserve existing config keys (gowa_device_id, allowed_jid_types) while updating
 * the per-channel AI settings; whatsapp_cloud sends only the non-empty credentials
 * ("keep current" when blank).
 * @param {Object} f field values + the original channel
 */
export function buildEditPayload(f) {
  const payload = { display_name: (f.displayName || '').trim() };
  const cfg = parseChannelConfig(f.channelConfig);
  const newConfig = { ...cfg, ai: f.ai };
  if (f.isGowa) {
    newConfig.allowed_jid_types = f.jidTypes;
    // Per-channel on/off for the GOWA disconnect alert (Telegram). The bot/chat/
    // interval/timezone stay global in the plugin's config screen.
    newConfig.disconnect_alert_enabled = !!f.gowaAlertEnabled;
  }
  payload.config = newConfig;
  if (f.isCloud) {
    const credentials = {};
    if ((f.accessToken || '').trim()) credentials.access_token = f.accessToken.trim();
    if ((f.phoneNumberId || '').trim()) credentials.phone_number_id = f.phoneNumberId.trim();
    if ((f.wabaId || '').trim()) credentials.waba_id = f.wabaId.trim();
    if ((f.verifyToken || '').trim()) credentials.verify_token = f.verifyToken.trim();
    if (Object.keys(credentials).length) payload.credentials = credentials;
  }
  return payload;
}
