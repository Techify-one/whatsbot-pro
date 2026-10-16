// Run with: node --test web/static/js/components/channels/showIf.test.js
//
// `show_if` / `form_order` (plano 176): o provider DECLARA campo condicional, o core só
// AVALIA. Estes testes travam a regra PURA do cliente; o espelho do servidor é
// tests/core/test_channel_show_if.py (mesma tabela de casos).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  fieldVisible, visibleFields, orderedFields, missingRequired, validateCredentials,
  buildCreatePayload, buildEditPayload, missingCredsFor, configWithDefaults,
} from './constants.js';

const IMAP_ON = { key: 'imap_enabled', equals: true };
const DESC = {
  provider: 'mail',
  config_fields: [
    { key: 'imap_enabled', type: 'bool', default: true },
    { key: 'smtp_enabled', type: 'bool', default: true },
  ],
  credential_fields: [
    { key: 'address', required: true },
    { key: 'imap_host', required: true, show_if: IMAP_ON },
    { key: 'imap_password', type: 'secret', required: true, show_if: IMAP_ON },
    { key: 'imap_port', show_if: IMAP_ON, pattern: '\\d{1,5}', pattern_error: 'porta inválida' },
    { key: 'smtp_host', required: true, show_if: { key: 'smtp_enabled', equals: true } },
  ],
};

// ── fieldVisible ────────────────────────────────────────────────────────────
test('fieldVisible: sem show_if o campo é sempre visível', () => {
  assert.equal(fieldVisible({ key: 'a' }, {}, {}), true);
  assert.equal(fieldVisible(undefined, {}, {}), true);
  assert.equal(fieldVisible(null, {}, {}), true);
});

test('fieldVisible: igualdade TIPADA — true não é "true"', () => {
  const f = { key: 'x', show_if: IMAP_ON };
  assert.equal(fieldVisible(f, { imap_enabled: true }, {}), true);
  assert.equal(fieldVisible(f, { imap_enabled: false }, {}), false);
  assert.equal(fieldVisible(f, { imap_enabled: 'true' }, {}), false);
  assert.equal(fieldVisible(f, { imap_enabled: 1 }, {}), false);
  assert.equal(fieldVisible(f, {}, {}), false, 'chave ausente = não igual');
});

test('fieldVisible: sem equals, aparece quando o outro for exatamente true', () => {
  const f = { key: 'x', show_if: { key: 'flag' } };
  assert.equal(fieldVisible(f, { flag: true }, {}), true);
  assert.equal(fieldVisible(f, { flag: false }, {}), false);
  assert.equal(fieldVisible(f, { flag: 'sim' }, {}), false);
});

test('fieldVisible: equals não-booleano (select por valor)', () => {
  const f = { key: 'x', show_if: { key: 'modo', equals: 'avancado' } };
  assert.equal(fieldVisible(f, { modo: 'avancado' }, {}), true);
  assert.equal(fieldVisible(f, { modo: 'simples' }, {}), false);
});

test('fieldVisible: lê a config primeiro e cai para as credenciais', () => {
  const f = { key: 'x', show_if: { key: 'k', equals: 'v' } };
  assert.equal(fieldVisible(f, {}, { k: 'v' }), true);
  assert.equal(fieldVisible(f, { k: 'outro' }, { k: 'v' }), false, 'config vence');
});

test('fieldVisible: show_if malformado é fail-open (campo visível)', () => {
  for (const bad of [{}, { equals: true }, { key: '' }, 'texto', 5, [], null]) {
    assert.equal(fieldVisible({ key: 'x', show_if: bad }, {}, {}), true, JSON.stringify(bad));
  }
});

test('fieldVisible: ciclo A↔B não trava (um nível por avaliação)', () => {
  const a = { key: 'a', show_if: { key: 'b', equals: true } };
  assert.equal(fieldVisible(a, { b: true }, {}), true);
  assert.equal(fieldVisible(a, { b: false }, {}), false);
});

// ── visibleFields / orderedFields ───────────────────────────────────────────
test('visibleFields filtra e preserva a ordem', () => {
  const keys = (cfg) => visibleFields(DESC.credential_fields, cfg, {}).map((f) => f.key);
  assert.deepEqual(keys({ imap_enabled: true, smtp_enabled: true }),
    ['address', 'imap_host', 'imap_password', 'imap_port', 'smtp_host']);
  assert.deepEqual(keys({ imap_enabled: false, smtp_enabled: true }), ['address', 'smtp_host']);
  assert.deepEqual(keys({ imap_enabled: false, smtp_enabled: false }), ['address']);
});

test('orderedFields: sem form_order é a ordem histórica (config, depois credenciais)', () => {
  const out = orderedFields(DESC).map((i) => `${i.kind}:${i.field.key}`);
  assert.deepEqual(out, ['config:imap_enabled', 'config:smtp_enabled', 'credential:address',
    'credential:imap_host', 'credential:imap_password', 'credential:imap_port',
    'credential:smtp_host']);
});

test('orderedFields: form_order intercala, ignora chave desconhecida e não perde campo', () => {
  const d = { ...DESC, form_order: ['address', 'imap_enabled', 'imap_host', 'nao_existe',
    'imap_enabled'] };
  const out = orderedFields(d).map((i) => i.field.key);
  assert.deepEqual(out.slice(0, 3), ['address', 'imap_enabled', 'imap_host']);
  assert.equal(new Set(out).size, out.length, 'sem duplicata');
  assert.deepEqual([...out].sort(), [...DESC.config_fields, ...DESC.credential_fields]
    .map((f) => f.key).sort(), 'o que o provider esqueceu de listar entra no fim');
});

test('orderedFields: descriptor vazio ou sem campos', () => {
  assert.deepEqual(orderedFields({}), []);
  assert.deepEqual(orderedFields(null), []);
  assert.deepEqual(orderedFields({ form_order: ['x'] }), []);
});

// ── obrigatoriedade e formato ───────────────────────────────────────────────
test('missingRequired: campo required escondido não trava o Criar', () => {
  const cred = { address: 'a@x.com', smtp_host: 'smtp.x.com' };
  assert.deepEqual(missingRequired(DESC, cred, { imap_enabled: true, smtp_enabled: true }),
    ['imap_host', 'imap_password']);
  assert.deepEqual(missingRequired(DESC, cred, { imap_enabled: false, smtp_enabled: true }), []);
  assert.deepEqual(missingRequired(DESC, {}, { imap_enabled: false, smtp_enabled: false }),
    ['address']);
});

test('missingRequired: espaço em branco conta como vazio', () => {
  assert.deepEqual(missingRequired(DESC, { address: '   ' }, { imap_enabled: false,
    smtp_enabled: false }), ['address']);
});

test('validateCredentials: campo escondido não é validado', () => {
  const cred = { imap_port: 'abc' };
  assert.deepEqual(validateCredentials(DESC, cred, { imap_enabled: true }),
    { imap_port: 'porta inválida' });
  assert.deepEqual(validateCredentials(DESC, cred, { imap_enabled: false }), {});
});

// ── payload: valor de campo escondido NÃO viaja ─────────────────────────────
const AI = { ai_sequential_enabled: false };

test('buildCreatePayload: senha digitada e depois escondida não vai ao servidor', () => {
  const cred = { address: 'a@x.com', imap_host: 'imap.x.com', imap_password: 'SEGREDO',
    smtp_host: 'smtp.x.com' };
  const on = buildCreatePayload({ provider: 'mail', displayName: 'n', ai: AI, descriptor: DESC,
    credValues: cred, configValues: { imap_enabled: true, smtp_enabled: true } });
  assert.equal(on.credentials.imap_password, 'SEGREDO');
  const off = buildCreatePayload({ provider: 'mail', displayName: 'n', ai: AI, descriptor: DESC,
    credValues: cred, configValues: { imap_enabled: false, smtp_enabled: true } });
  assert.deepEqual(off.credentials, { address: 'a@x.com', smtp_host: 'smtp.x.com' });
  assert.equal(JSON.stringify(off).includes('SEGREDO'), false);
  assert.equal(off.config.imap_enabled, false, 'o interruptor em si vai no config');
});

test('buildEditPayload: o mesmo vale na edição', () => {
  const cred = { imap_host: 'imap.x.com', imap_password: 'NOVA', smtp_host: 'smtp.x.com' };
  const p = buildEditPayload({ displayName: 'n', ai: AI, descriptor: DESC, channelConfig: '{}',
    credValues: cred, configValues: { imap_enabled: false, smtp_enabled: true } });
  assert.deepEqual(p.credentials, { smtp_host: 'smtp.x.com' });
});

// ── zumbi no card ───────────────────────────────────────────────────────────
test('missingCredsFor: lado desligado não vira zumbi', () => {
  const req = { mail: ['imap_host', 'imap_password', 'smtp_host'] };
  const ch = (config, credentials) => ({ provider: 'mail', config: JSON.stringify(config),
    credentials });
  assert.deepEqual(missingCredsFor(ch({ imap_enabled: false, smtp_enabled: true },
    { smtp_host: 'x' }), req, DESC), []);
  assert.deepEqual(missingCredsFor(ch({ imap_enabled: true, smtp_enabled: true },
    { smtp_host: 'x' }), req, DESC), ['imap_host', 'imap_password']);
});

test('missingCredsFor: canal antigo sem a chave no config usa o default (ligado)', () => {
  const req = { mail: ['imap_host'] };
  assert.deepEqual(missingCredsFor({ provider: 'mail', config: '{}', credentials: {} },
    req, DESC), ['imap_host']);
});

test('missingCredsFor: sem descriptor é o comportamento de sempre', () => {
  const req = { gowa: ['a'] };
  assert.deepEqual(missingCredsFor({ provider: 'gowa', credentials: {} }, req), ['a']);
});

test('configWithDefaults completa só o que falta e não sobrescreve', () => {
  assert.deepEqual(configWithDefaults(DESC, {}), { imap_enabled: true, smtp_enabled: true });
  assert.deepEqual(configWithDefaults(DESC, { imap_enabled: false }),
    { imap_enabled: false, smtp_enabled: true });
  assert.deepEqual(configWithDefaults(null, { a: 1 }), { a: 1 });
});
