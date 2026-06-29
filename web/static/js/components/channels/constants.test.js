// Run with: node --test web/static/js/components/channels/constants.test.js
//
// Characterization tests (Plano 23 · D4) for the pure channels helpers extracted
// from ChannelsManager.js. These lock the create/edit payload shaping (provider
// branching, the GOWA-vs-others sequential default, "keep current" blank creds)
// and the catalogue/credential helpers, so a decomposition regression trips here
// instead of silently in the browser.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PROVIDERS, providerMeta, credLabel, parseChannelConfig, aiDefaultsFrom,
  requiredCredsFor, missingCredsFor, buildCreatePayload, buildEditPayload,
  REQUIRED_CREDS_FALLBACK,
} from './constants.js';

// ── providerMeta ─────────────────────────────────────────────────────────
test('providerMeta: known providers map to their catalogue entry', () => {
  assert.equal(providerMeta('gowa'), PROVIDERS.gowa);
  assert.equal(providerMeta('whatsapp_cloud').label, 'WhatsApp Cloud');
  assert.equal(providerMeta('telegram').label, 'Telegram');
  assert.equal(providerMeta('test').label, 'Teste');
});
test('providerMeta: unknown provider falls back to the raw name + neutral tint', () => {
  const m = providerMeta('weird');
  assert.equal(m.label, 'weird');
  assert.match(m.tint, /text-wa-secondary/);
  assert.equal(providerMeta(null).label, '—');
});

// ── credLabel ────────────────────────────────────────────────────────────
test('credLabel: friendly PT label, falls back to the raw key', () => {
  assert.equal(credLabel('access_token'), 'Access Token');
  assert.equal(credLabel('bot_token'), 'Bot Token');
  assert.equal(credLabel('something_new'), 'something_new');
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
  assert.equal(d.transfer_alert_duration, 5);
});
test('aiDefaultsFrom: inherits provided global values', () => {
  const d = aiDefaultsFrom({ group_reply_mode: 'always', max_context_messages: 25, split_messages: false });
  assert.equal(d.group_reply_mode, 'always');
  assert.equal(d.max_context_messages, 25);
  assert.equal(d.split_messages, false);
});

// ── requiredCredsFor / missingCredsFor ─────────────────────────────────────
test('requiredCredsFor: prefers backend list, falls back to local table', () => {
  assert.deepEqual(requiredCredsFor('telegram', { telegram: ['x'] }), ['x']);
  assert.deepEqual(requiredCredsFor('telegram', {}), REQUIRED_CREDS_FALLBACK.telegram);
  assert.deepEqual(requiredCredsFor('gowa', {}), []);
});
test('missingCredsFor: reports required creds not present on the channel', () => {
  const ch = { provider: 'whatsapp_cloud', credentials: { access_token: 'x' } };
  assert.deepEqual(missingCredsFor(ch, {}), ['phone_number_id', 'verify_token']);
  const full = { provider: 'whatsapp_cloud', credentials: { access_token: 'a', phone_number_id: 'b', verify_token: 'c' } };
  assert.deepEqual(missingCredsFor(full, {}), []);
  assert.deepEqual(missingCredsFor({ provider: 'gowa', credentials: {} }, {}), []);
});

// ── buildCreatePayload ─────────────────────────────────────────────────────
const aiBase = aiDefaultsFrom({});

test('buildCreatePayload: GOWA sets jid types + device id, sequential default ON', () => {
  const p = buildCreatePayload({
    provider: 'gowa', displayName: '  Atendimento  ', ai: { ...aiBase },
    gowaDeviceId: 'gowa_abc', jidTypes: ['person', 'group'],
  });
  assert.equal(p.provider, 'gowa');
  assert.equal(p.display_name, 'Atendimento');           // trimmed
  assert.deepEqual(p.config.allowed_jid_types, ['person', 'group']);
  assert.equal(p.config.gowa_device_id, 'gowa_abc');
  assert.equal(p.config.ai.ai_sequential_enabled, true); // GOWA default ON
  assert.equal(p.credentials, undefined);
});

test('buildCreatePayload: telegram sequential default OFF, only non-empty bot_token sent', () => {
  const p = buildCreatePayload({ provider: 'telegram', displayName: 'TG', ai: { ...aiBase }, botToken: ' 123:ABC ' });
  assert.equal(p.config.ai.ai_sequential_enabled, false);  // non-GOWA default OFF
  assert.deepEqual(p.credentials, { bot_token: '123:ABC' });
  assert.equal(p.config.allowed_jid_types, undefined);
});

test('buildCreatePayload: telegram with blank token omits credentials', () => {
  const p = buildCreatePayload({ provider: 'telegram', displayName: 'TG', ai: { ...aiBase }, botToken: '   ' });
  assert.equal(p.credentials, undefined);
});

test('buildCreatePayload: whatsapp_cloud only includes the non-empty creds', () => {
  const p = buildCreatePayload({
    provider: 'whatsapp_cloud', displayName: 'Cloud', ai: { ...aiBase },
    accessToken: 'tok', phoneNumberId: '', wabaId: 'W1', verifyToken: ' v ',
  });
  assert.deepEqual(p.credentials, { access_token: 'tok', waba_id: 'W1', verify_token: 'v' });
  assert.equal(p.config.ai.ai_sequential_enabled, false);
});

test('buildCreatePayload: explicit ai_sequential_enabled wins over the provider default', () => {
  const p = buildCreatePayload({
    provider: 'telegram', displayName: 'TG', ai: { ...aiBase, ai_sequential_enabled: true }, botToken: 't',
  });
  assert.equal(p.config.ai.ai_sequential_enabled, true);
});

// ── buildEditPayload ───────────────────────────────────────────────────────
test('buildEditPayload: preserves existing config keys, updates ai, GOWA jid types', () => {
  const p = buildEditPayload({
    displayName: 'New name', ai: { ...aiBase, group_reply_mode: 'always' },
    jidTypes: ['person'], isGowa: true, isCloud: false,
    channelConfig: '{"gowa_device_id":"dev1","allowed_jid_types":["person","group"]}',
  });
  assert.equal(p.display_name, 'New name');
  assert.equal(p.config.gowa_device_id, 'dev1');                 // preserved
  assert.deepEqual(p.config.allowed_jid_types, ['person']);      // updated by jidTypes
  assert.equal(p.config.ai.group_reply_mode, 'always');
  assert.equal(p.credentials, undefined);
});

test('buildEditPayload: whatsapp_cloud sends only non-blank creds ("keep current")', () => {
  const p = buildEditPayload({
    displayName: 'C', ai: { ...aiBase }, jidTypes: [], isGowa: false, isCloud: true,
    channelConfig: {}, accessToken: '', phoneNumberId: 'PNID', wabaId: '', verifyToken: '',
  });
  assert.deepEqual(p.credentials, { phone_number_id: 'PNID' });
  assert.equal(p.config.allowed_jid_types, undefined);  // not GOWA → not set
});

test('buildEditPayload: whatsapp_cloud with all-blank creds omits credentials', () => {
  const p = buildEditPayload({
    displayName: 'C', ai: { ...aiBase }, jidTypes: [], isGowa: false, isCloud: true,
    channelConfig: {}, accessToken: '', phoneNumberId: '', wabaId: '', verifyToken: '',
  });
  assert.equal(p.credentials, undefined);
});
