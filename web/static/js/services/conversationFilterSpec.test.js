// Run with: node --test web/static/js/services/conversationFilterSpec.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildCountParams, isServerExpressible } from './conversationFilterSpec.js';

test('default open inbox builds an archived-scoped status count', () => {
  const spec = { statusFilter: 'open', tagFilter: [], advFilters: [], archived: false };
  assert.equal(isServerExpressible(spec), true);
  assert.deepEqual(buildCountParams(spec), { archived: 'false', status: 'open' });
});

test('search disables the status chip and sends q', () => {
  const spec = { search: 'alice', searching: true, statusFilter: 'open', advFilters: [] };
  assert.deepEqual(buildCountParams(spec), { q: 'alice', archived: 'false' });
});

test('known advanced dimensions map to server params', () => {
  const spec = {
    statusFilter: 'all',
    archived: true,
    tagFilter: ['vip'],
    advFilters: [
      { dim: 'channel', op: 'eq', value: ['telegram'] },
      { dim: 'agent', op: 'eq', value: 'user:7' },
      { dim: 'cattr:conversation:plano', op: 'contains', value: 'gold' },
      { dim: 'cattr:contact:origem', op: 'ne', value: 'importado' },
    ],
  };
  assert.equal(isServerExpressible(spec), true);
  assert.deepEqual(buildCountParams(spec), {
    archived: 'true',
    labels: ['vip'],
    channel: ['telegram'],
    agent: ['user:7'],
    'cattr:plano__op': 'contains',
    'cattr:plano': 'gold',
    'cattr:contact:origem__op': 'not_equal_to',
    'cattr:contact:origem': 'importado',
  });
});

test('duplicate dimensions that would turn AND into OR fall back to client counts', () => {
  assert.equal(isServerExpressible({
    tagFilter: ['vip'],
    advFilters: [{ dim: 'tag', op: 'eq', value: ['lead'] }],
  }), false);
  assert.equal(isServerExpressible({
    advFilters: [
      { dim: 'channel', op: 'eq', value: 'wa' },
      { dim: 'channel', op: 'eq', value: 'telegram' },
    ],
  }), false);
});

test('unsupported clauses fall back to client counts', () => {
  assert.equal(isServerExpressible({
    advFilters: [{ dim: 'unknown', op: 'eq', value: 'x' }],
  }), false);
});
