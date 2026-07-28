// Run with: node --test web/static/js/services/conversationFilterSpec.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildCountParams, isServerExpressible, buildListParams, isListServerExpressible,
  buildContactFilterParams, isContactFilterServerExpressible,
} from './conversationFilterSpec.js';

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

// ── plano 69 F1 — assignment tab → list params ─────────────────────────────

test('buildListParams: tab "all" equals the base count params', () => {
  const spec = { statusFilter: 'open', assignmentTab: 'all', advFilters: [] };
  assert.deepEqual(buildListParams(spec), buildCountParams(spec));
});

test('buildListParams: tab "mine" appends assignee=me', () => {
  const spec = { statusFilter: 'open', assignmentTab: 'mine', advFilters: [] };
  assert.deepEqual(buildListParams(spec), { archived: 'false', status: 'open', assignee: 'me' });
  assert.equal(isListServerExpressible(spec), true);
});

test('buildListParams: tab "unassigned" appends agent=none with equal_to override', () => {
  const spec = { statusFilter: 'open', assignmentTab: 'unassigned', advFilters: [] };
  assert.deepEqual(buildListParams(spec), {
    archived: 'false', status: 'open', agent: 'none', agent__op: 'equal_to',
  });
  assert.equal(isListServerExpressible(spec), true);
});

test('buildListParams: tab "mentions" appends has_mention=true', () => {
  const spec = { statusFilter: 'open', assignmentTab: 'mentions', advFilters: [] };
  assert.deepEqual(buildListParams(spec), {
    archived: 'false', status: 'open', has_mention: 'true',
  });
  assert.equal(isListServerExpressible(spec), true);
});

test('unassigned tab + an advanced Agente clause collides → not list-expressible', () => {
  const spec = {
    statusFilter: 'open', assignmentTab: 'unassigned',
    advFilters: [{ dim: 'agent', op: 'eq', value: 'user:7' }],
  };
  // Base spec is fine, but the tab's `agent` param would clobber the adv `agent` one.
  assert.equal(isServerExpressible(spec), true);
  assert.equal(isListServerExpressible(spec), false);
});

test('mine/mentions tabs never collide with an advanced Agente clause', () => {
  const adv = [{ dim: 'agent', op: 'eq', value: 'user:7' }];
  assert.equal(isListServerExpressible({ assignmentTab: 'mine', advFilters: adv }), true);
  assert.equal(isListServerExpressible({ assignmentTab: 'mentions', advFilters: adv }), true);
});

test('searching disables the assignment clause (all tabs shown while searching)', () => {
  const spec = { search: 'x', searching: true, assignmentTab: 'mine', advFilters: [] };
  assert.deepEqual(buildListParams(spec), buildCountParams(spec));
});

// ── plano 69 F6 — filtro de CONTATOS → params do /api/contacts ─────────────

test('buildContactFilterParams: tag/contact_type/cattr map to flat params', () => {
  const adv = [
    { dim: 'tag', op: 'eq', value: ['vip', 'lead'] },
    { dim: 'contact_type', op: 'ne', value: 'telegram' },
    { dim: 'cattr:contact:origem', op: 'contains', value: 'importado' },
  ];
  assert.equal(isContactFilterServerExpressible(adv), true);
  assert.deepEqual(buildContactFilterParams(adv), {
    labels: ['vip', 'lead'],
    contact_type: 'telegram',
    contact_type__op: 'not_equal_to',
    'cattr:contact:origem__op': 'contains',
    'cattr:contact:origem': 'importado',
  });
});

test('contact tag "≠" (ne) is not server-expressible → client fallback', () => {
  // The conversation `tag`→`labels` mapping only supports membership (eq); ne stays client.
  assert.equal(isContactFilterServerExpressible(
    [{ dim: 'tag', op: 'ne', value: ['vip'] }]), false);
});

test('duplicate contact dims (two tags) fall back to the client', () => {
  assert.equal(isContactFilterServerExpressible([
    { dim: 'tag', op: 'eq', value: ['vip'] },
    { dim: 'tag', op: 'eq', value: ['lead'] },
  ]), false);
});

test('empty contact filter is trivially expressible', () => {
  assert.equal(isContactFilterServerExpressible([]), true);
  assert.deepEqual(buildContactFilterParams([]), {});
});
