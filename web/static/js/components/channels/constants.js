// @ts-check
//
// Channels — pure constants + helpers. PURE data/logic (no Preact), testable in
// isolation (node --test) and shared by the channels/* pieces.
//
// Plano 33 — the core no longer knows any provider by name. The catalogue, the
// credential/config fields, the JID types and the create/edit payload shaping all
// come from the provider DESCRIPTOR (GET /api/channels/providers). These builders
// take the descriptor + the collected field values and assemble the payload
// generically — there is NO `if provider === 'gowa'/'telegram'/'whatsapp_cloud'`
// anywhere in the frontend.

// Semantic accent tint per descriptor `color` token. Only classes covered by the
// custom.css dark overrides (…-50/-700 tints) so badges stay legible in dark mode.
// A provider picks a token in its `provider_descriptor()`; the core never maps a
// provider NAME to a colour.
export const COLOR_TINTS = {
  green: 'bg-green-50 text-green-700',
  blue: 'bg-blue-50 text-blue-700',
  purple: 'bg-purple-50 text-purple-700',
  teal: 'bg-wa-teal/10 text-wa-teal',
  amber: 'bg-amber-50 text-amber-700',
  orange: 'bg-orange-50 text-orange-700',
  red: 'bg-red-50 text-red-700',
  pink: 'bg-pink-50 text-pink-700',
  gray: 'bg-gray-100 text-wa-secondary',
};
export const NEUTRAL_TINT = COLOR_TINTS.gray;

export function tintForColor(color) {
  return COLOR_TINTS[color] || NEUTRAL_TINT;
}

// Badge meta (label + tint) for a provider, resolved from the fetched descriptor
// map (provider id -> descriptor). Falls back to the raw provider name + neutral
// tint when the descriptor isn't available (e.g. an archived channel whose
// provider plugin is currently uninstalled). Never hardcodes a provider.
export function providerMeta(provider, descriptorsById) {
  const d = descriptorsById && descriptorsById[provider];
  if (d) return { label: d.label || provider || '—', tint: tintForColor(d.color) };
  return { label: provider || '—', tint: NEUTRAL_TINT };
}

// Friendly label for a credential key, read from the descriptor's field list
// (falls back to the raw key). Used by the card's "missing credentials" warning.
export function credLabel(key, descriptor) {
  const f = descriptor && (descriptor.credential_fields || []).find((x) => x.key === key);
  return (f && f.label) || key;
}

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

// Build the <script> embed snippet for a widget channel (plano 46). PURE.
// Shared by the post-create notice AND the channel edit form's "copy again" block,
// so the snippet is byte-identical wherever it is shown. The core doesn't know the
// provider — it renders this only when the descriptor's post_create.kind is
// 'embed_snippet'. `\/script` keeps the literal from closing this module's script.
export function buildEmbedSnippet(baseUrl, widgetToken) {
  const b = (baseUrl || '').replace(/\/+$/, '');
  return `<script>(function(d,t){var g=d.createElement(t);` +
    `g.src="${b}/plugins/website/static/sdk.js";g.async=true;d.body.appendChild(g);` +
    `g.onload=function(){window.WhatsBotChat.run({widgetToken:'${widgetToken}',baseUrl:'${b}'})}` +
    `})(document,'script')<\/script>`;
}

// Random URL-safe token, used for the "sugerir" verify-token button and for
// generated config fields (e.g. GOWA device id).
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

// Required creds a given channel is MISSING (returns the keys not present in its
// stored credentials). Capability-driven from the providers fetch (`requiredCreds`
// = the flat {provider: [key,...]} map). Used by ChannelCard's zombie warning.
export function missingCredsFor(channel, requiredCreds) {
  const cred = (channel && channel.credentials) || {};
  const req = (requiredCreds && requiredCreds[channel && channel.provider]) || [];
  return req.filter((k) => !cred[k]);
}

// Initial config-field values for a NEW channel of `descriptor`: multiselect →
// its `default` list; generated → `prefix` + a random token; bool → its
// `default` coerced to boolean; anything else → its `default` (or empty string).
// PURE.
export function initialConfigValues(descriptor) {
  const out = {};
  for (const f of (descriptor && descriptor.config_fields) || []) {
    if (f.type === 'multiselect') out[f.key] = Array.isArray(f.default) ? f.default.slice() : [];
    else if (f.type === 'generated') out[f.key] = `${f.prefix || ''}${randomToken(10)}`;
    else if (f.type === 'bool') out[f.key] = f.default != null ? !!f.default : false;
    else out[f.key] = f.default != null ? f.default : '';
  }
  return out;
}

/**
 * Build the create-channel POST payload GENERICALLY from the descriptor + the
 * collected field values. PURE. No provider branching: `credValues` maps each
 * descriptor credential_field key → its string value; `configValues` maps each
 * config_field key → its value (multiselect array / generated string / text).
 * The per-channel sequential-reply default comes from the descriptor
 * (`ai_sequential_default`), not from a hardcoded provider check.
 * @param {Object} f {provider, displayName, ai, descriptor, credValues, configValues}
 */
export function buildCreatePayload(f) {
  const descriptor = f.descriptor || {};
  const payload = {
    provider: f.provider,
    display_name: (f.displayName || '').trim(),
  };
  const seqDefault = !!descriptor.ai_sequential_default;
  const config = {
    ...(f.configValues || {}),
    ai: { ...f.ai, ai_sequential_enabled: f.ai.ai_sequential_enabled ?? seqDefault },
  };
  payload.config = config;
  const credentials = {};
  for (const field of descriptor.credential_fields || []) {
    const raw = f.credValues && f.credValues[field.key];
    const v = raw == null ? '' : String(raw).trim();
    if (v) credentials[field.key] = v;
  }
  if (Object.keys(credentials).length) payload.credentials = credentials;
  return payload;
}

/**
 * Build the edit-channel PUT payload GENERICALLY. PURE. PUT replaces config
 * wholesale, so existing config keys are preserved (spread first) — including
 * immutable `generated` fields like gowa_device_id — then the editable
 * `configValues` (e.g. jid types) and the per-channel AI settings overlay them.
 * Credentials: only a non-empty, non-masked value is sent ("keep current" when
 * blank or the •••• placeholder).
 * @param {Object} f {displayName, ai, descriptor, channelConfig, credValues, configValues}
 */
export function buildEditPayload(f) {
  const descriptor = f.descriptor || {};
  const payload = { display_name: (f.displayName || '').trim() };
  const cfg = parseChannelConfig(f.channelConfig);
  payload.config = { ...cfg, ...(f.configValues || {}), ai: f.ai };
  const credentials = {};
  for (const field of descriptor.credential_fields || []) {
    const raw = f.credValues && f.credValues[field.key];
    const v = raw == null ? '' : String(raw).trim();
    if (v && !v.startsWith('••••')) credentials[field.key] = v;
  }
  if (Object.keys(credentials).length) payload.credentials = credentials;
  return payload;
}
