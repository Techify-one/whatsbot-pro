// Run with: node --test web/static/js/components/channels/constants.test.js
//
// Characterization tests (plano 33) for the DESCRIPTOR-DRIVEN channel helpers.
// The create/edit payload builders no longer branch on provider name: they take
// the provider descriptor + the collected field values and assemble the payload
// generically. These lock that shaping (credentials from credential_fields,
// config from config_fields, the sequential-reply default from the descriptor,
// "keep current" blank/masked creds on edit) so a regression trips here.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  providerMeta, credLabel, tintForColor, parseChannelConfig, aiDefaultsFrom,
  missingCredsFor, initialConfigValues, buildCreatePayload, buildEditPayload,
  buildEmbedSnippet,
} from './constants.js';

test('buildEmbedSnippet: trims trailing slash and embeds token + base', () => {
  const s = buildEmbedSnippet('https://x.example/', 'wgt_abc123');
  assert.ok(s.includes("widgetToken:'wgt_abc123'"));
  assert.ok(s.includes("baseUrl:'https://x.example'"));
  assert.ok(s.includes('https://x.example/plugins/website/static/sdk.js'));
  assert.ok(s.startsWith('<script>') && s.trimEnd().endsWith('</script>'));
});

// ── Descriptor fixtures (mirror the real gowa/telegram/whatsapp_cloud) ──────
const GOWA = {
  provider: 'gowa', label: 'GOWA', color: 'green',
  credential_fields: [],
  config_fields: [
    { key: 'gowa_device_id', type: 'generated', prefix: 'gowa_' },
    { key: 'allowed_jid_types', type: 'multiselect',
      options: [{ value: 'person' }, { value: 'group' }, { value: 'newsletter' }],
      default: ['person', 'group'] },
    { key: 'disconnect_alert_enabled', type: 'bool', default: true },
  ],
  capabilities: { needs_qr: true, templates: false },
  ai_sequential_default: true, post_create: null,
};
const TELEGRAM = {
  provider: 'telegram', label: 'Telegram', color: 'purple',
  credential_fields: [{ key: 'bot_token', label: 'Bot Token', type: 'secret', required: true }],
  config_fields: [],
  capabilities: { needs_qr: false, templates: false },
  ai_sequential_default: false,
  post_create: { kind: 'autoconfigure', endpoint: '/api/plugins/telegram/autoconfigure' },
};
const CLOUD = {
  provider: 'whatsapp_cloud', label: 'WhatsApp Cloud', color: 'blue',
  credential_fields: [
    { key: 'access_token', label: 'Access Token', type: 'secret', required: true },
    { key: 'phone_number_id', label: 'Phone Number ID', type: 'text', required: true },
    { key: 'waba_id', label: 'WABA ID', type: 'text', required: false },
    { key: 'verify_token', label: 'Verify Token', type: 'token_suggest', required: true },
  ],
  config_fields: [],
  capabilities: { needs_qr: false, templates: true },
  ai_sequential_default: false,
  post_create: { kind: 'webhook_url', path: '/api/webhook/whatsapp_cloud/{channel_id}' },
};
const BY_ID = { gowa: GOWA, telegram: TELEGRAM, whatsapp_cloud: CLOUD };

// ── providerMeta / tintForColor ─────────────────────────────────────────────
test('providerMeta: resolves label + tint from the descriptor map', () => {
  assert.equal(providerMeta('gowa', BY_ID).label, 'GOWA');
  assert.equal(providerMeta('telegram', BY_ID).tint, tintForColor('purple'));
  assert.equal(providerMeta('whatsapp_cloud', BY_ID).label, 'WhatsApp Cloud');
});
test('providerMeta: unknown/absent descriptor falls back to raw name + neutral tint', () => {
  const m = providerMeta('weird', BY_ID);
  assert.equal(m.label, 'weird');
  assert.match(m.tint, /text-wa-secondary/);
  assert.equal(providerMeta(null, BY_ID).label, '—');
  assert.equal(providerMeta('gowa', null).label, 'gowa'); // no map → raw name
});

// ── credLabel ────────────────────────────────────────────────────────────
test('credLabel: friendly label from the descriptor, falls back to the raw key', () => {
  assert.equal(credLabel('access_token', CLOUD), 'Access Token');
  assert.equal(credLabel('bot_token', TELEGRAM), 'Bot Token');
  assert.equal(credLabel('something_new', CLOUD), 'something_new');
  assert.equal(credLabel('x', null), 'x');
});

// ── parseChannelConfig ─────────────────────────────────────────────────────
test('parseChannelConfig: handles object, JSON string, null, and malformed', () => {
  assert.deepEqual(parseChannelConfig({ a: 1 }), { a: 1 });
  assert.deepEqual(parseChannelConfig('{"a":2}'), { a: 2 });
  assert.deepEqual(parseChannelConfig(null), {});
  assert.deepEqual(parseChannelConfig(''), {});
  assert.deepEqual(parseChannelConfig('{not json'), {});
});

// ── aiDefaultsFrom ─────────────────────────────────────────────────────────
test('aiDefaultsFrom: empty config → canonical defaults (ai_enabled on)', () => {
  const d = aiDefaultsFrom({});
  assert.equal(d.ai_enabled, true);
  assert.equal(d.group_reply_mode, 'mention_only');
  assert.equal(d.max_context_messages, 10);
  assert.equal(d.split_messages, true);
});
test('aiDefaultsFrom: inherits provided global values', () => {
  const d = aiDefaultsFrom({ group_reply_mode: 'always', max_context_messages: 25, split_messages: false });
  assert.equal(d.group_reply_mode, 'always');
  assert.equal(d.max_context_messages, 25);
  assert.equal(d.split_messages, false);
});

// ── missingCredsFor ─────────────────────────────────────────────────────────
test('missingCredsFor: reports required creds not present on the channel', () => {
  const req = { whatsapp_cloud: ['access_token', 'phone_number_id', 'verify_token'] };
  const ch = { provider: 'whatsapp_cloud', credentials: { access_token: 'x' } };
  assert.deepEqual(missingCredsFor(ch, req), ['phone_number_id', 'verify_token']);
  const full = { provider: 'whatsapp_cloud', credentials: { access_token: 'a', phone_number_id: 'b', verify_token: 'c' } };
  assert.deepEqual(missingCredsFor(full, req), []);
  assert.deepEqual(missingCredsFor({ provider: 'gowa', credentials: {} }, req), []);
});

// ── initialConfigValues ─────────────────────────────────────────────────────
test('initialConfigValues: multiselect→default, generated→prefix+token, bool→default, text→default', () => {
  const v = initialConfigValues(GOWA);
  assert.deepEqual(v.allowed_jid_types, ['person', 'group']);
  assert.match(v.gowa_device_id, /^gowa_/);
  assert.equal(v.gowa_device_id.length, 'gowa_'.length + 10);
  assert.equal(v.disconnect_alert_enabled, true);            // bool default coerced
  assert.deepEqual(initialConfigValues(TELEGRAM), {}); // no config fields
  assert.deepEqual(initialConfigValues(null), {});
});

// ── buildCreatePayload (generic) ────────────────────────────────────────────
const aiBase = aiDefaultsFrom({});

test('buildCreatePayload: GOWA → config fields, no credentials, sequential default ON', () => {
  const p = buildCreatePayload({
    provider: 'gowa', displayName: '  Atendimento  ', ai: { ...aiBase },
    descriptor: GOWA,
    configValues: { gowa_device_id: 'gowa_abc', allowed_jid_types: ['person', 'group'] },
    credValues: {},
  });
  assert.equal(p.provider, 'gowa');
  assert.equal(p.display_name, 'Atendimento');              // trimmed
  assert.deepEqual(p.config.allowed_jid_types, ['person', 'group']);
  assert.equal(p.config.gowa_device_id, 'gowa_abc');
  assert.equal(p.config.ai.ai_sequential_enabled, true);    // descriptor default ON
  assert.equal(p.credentials, undefined);
});

test('bool config_field (GOWA disconnect_alert_enabled) rides through create + edit payloads', () => {
  // create: the bool value flows via configValues, no provider-specific branch
  const created = buildCreatePayload({
    provider: 'gowa', displayName: 'X', ai: { ...aiBase }, descriptor: GOWA,
    configValues: { gowa_device_id: 'gowa_x', allowed_jid_types: ['person'],
      disconnect_alert_enabled: false },
    credValues: {},
  });
  assert.equal(created.config.disconnect_alert_enabled, false);
  // edit: configValues override the parsed existing config
  const edited = buildEditPayload({
    displayName: 'X', descriptor: GOWA,
    channelConfig: { allowed_jid_types: ['person'], disconnect_alert_enabled: true },
    ai: { ...aiBase },
    configValues: { allowed_jid_types: ['person'], disconnect_alert_enabled: false },
    credValues: {},
  });
  assert.equal(edited.config.disconnect_alert_enabled, false);
});

test('buildCreatePayload: telegram → only non-empty bot_token, sequential default OFF', () => {
  const p = buildCreatePayload({
    provider: 'telegram', displayName: 'TG', ai: { ...aiBase }, descriptor: TELEGRAM,
    configValues: {}, credValues: { bot_token: ' 123:ABC ' },
  });
  assert.equal(p.config.ai.ai_sequential_enabled, false);
  assert.deepEqual(p.credentials, { bot_token: '123:ABC' });
  assert.equal(p.config.allowed_jid_types, undefined);
});

test('buildCreatePayload: telegram blank token omits credentials', () => {
  const p = buildCreatePayload({
    provider: 'telegram', displayName: 'TG', ai: { ...aiBase }, descriptor: TELEGRAM,
    configValues: {}, credValues: { bot_token: '   ' },
  });
  assert.equal(p.credentials, undefined);
});

test('buildCreatePayload: whatsapp_cloud includes only the non-empty creds', () => {
  const p = buildCreatePayload({
    provider: 'whatsapp_cloud', displayName: 'Cloud', ai: { ...aiBase }, descriptor: CLOUD,
    configValues: {},
    credValues: { access_token: 'tok', phone_number_id: '', waba_id: 'W1', verify_token: ' v ' },
  });
  assert.deepEqual(p.credentials, { access_token: 'tok', waba_id: 'W1', verify_token: 'v' });
  assert.equal(p.config.ai.ai_sequential_enabled, false);
});

test('buildCreatePayload: explicit ai_sequential_enabled wins over the descriptor default', () => {
  const p = buildCreatePayload({
    provider: 'telegram', displayName: 'TG', descriptor: TELEGRAM,
    ai: { ...aiBase, ai_sequential_enabled: true }, configValues: {}, credValues: { bot_token: 't' },
  });
  assert.equal(p.config.ai.ai_sequential_enabled, true);
});

// ── buildEditPayload (generic) ───────────────────────────────────────────────
test('buildEditPayload: preserves existing config keys (generated), updates multiselect + ai', () => {
  const p = buildEditPayload({
    displayName: 'New name', ai: { ...aiBase, group_reply_mode: 'always' },
    descriptor: GOWA,
    channelConfig: '{"gowa_device_id":"dev1","allowed_jid_types":["person","group"]}',
    configValues: { allowed_jid_types: ['person'] }, credValues: {},
  });
  assert.equal(p.display_name, 'New name');
  assert.equal(p.config.gowa_device_id, 'dev1');                // preserved from stored config
  assert.deepEqual(p.config.allowed_jid_types, ['person']);     // updated
  assert.equal(p.config.ai.group_reply_mode, 'always');
  assert.equal(p.credentials, undefined);
});

test('buildEditPayload: whatsapp_cloud sends only non-blank, non-masked creds ("keep current")', () => {
  const p = buildEditPayload({
    displayName: 'C', ai: { ...aiBase }, descriptor: CLOUD, channelConfig: {},
    configValues: {},
    credValues: { access_token: '••••abcd', phone_number_id: 'PNID', waba_id: '', verify_token: '' },
  });
  assert.deepEqual(p.credentials, { phone_number_id: 'PNID' });  // masked + blank skipped
});

test('buildEditPayload: all-blank/masked creds omit credentials', () => {
  const p = buildEditPayload({
    displayName: 'C', ai: { ...aiBase }, descriptor: CLOUD, channelConfig: {},
    configValues: {}, credValues: { access_token: '••••abcd', phone_number_id: '', verify_token: '' },
  });
  assert.equal(p.credentials, undefined);
});
