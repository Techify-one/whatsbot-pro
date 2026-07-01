// Run with: node --test web/static/js/services/urlState.test.js
//
// Characterization tests (Plano 24) for the pure URL query-string codec. Lock
// the omit-default behavior, name encoding round-trips, and the list/json codecs
// so a regression trips here instead of silently mangling a shared link.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  readParams, writeParams, str, enumStr, bool, int, list, json,
} from './urlState.js';

// A representative "hub" schema mixing every codec kind.
const isDefaultSpec = (s) => !s || (Array.isArray(s.advFilters) && s.advFilters.length === 0);
const SCHEMA = [
  enumStr('status', 'open'),
  enumStr('assignment', 'all'),
  str('search', ''),
  bool('archived'),
  int('page', 1),
  list('tags'),
  json('adv', { isDefault: isDefaultSpec }),
];

test('readParams: absent params fall back to defaults', () => {
  const s = readParams('', SCHEMA);
  assert.equal(s.status, 'open');
  assert.equal(s.assignment, 'all');
  assert.equal(s.search, '');
  assert.equal(s.archived, false);
  assert.equal(s.page, 1);
  assert.deepEqual(s.tags, []);
  assert.equal(s.adv, null);
});

test('writeParams: values equal to defaults are omitted (clean URL)', () => {
  const qs = writeParams({
    status: 'open', assignment: 'all', search: '', archived: false,
    page: 1, tags: [], adv: null,
  }, SCHEMA);
  assert.equal(qs, '');
});

test('writeParams: only non-default values appear, in schema order', () => {
  const qs = writeParams({
    status: 'closed', assignment: 'mine', search: 'pagamento',
    archived: true, page: 3, tags: [], adv: null,
  }, SCHEMA);
  assert.equal(qs, 'status=closed&assignment=mine&search=pagamento&archived=1&page=3');
});

test('round-trip: writeParams → readParams preserves values', () => {
  const state = {
    status: 'closed', assignment: 'unassigned', search: 'olá mundo',
    archived: true, page: 7, tags: ['a b', 'c,d'], adv: { advFilters: [{ dim: 'status', op: 'eq', value: 'x' }] },
  };
  const qs = writeParams(state, SCHEMA);
  const back = readParams(qs, SCHEMA);
  assert.deepEqual(back, state);
});

test('bool: "1"/"true" decode truthy, everything else false', () => {
  assert.equal(readParams('archived=1', SCHEMA).archived, true);
  assert.equal(readParams('archived=true', SCHEMA).archived, true);
  assert.equal(readParams('archived=0', SCHEMA).archived, false);
  assert.equal(readParams('archived=nope', SCHEMA).archived, false);
});

test('int: non-numeric decodes to the field default', () => {
  assert.equal(readParams('page=abc', SCHEMA).page, 1);
  assert.equal(readParams('page=42', SCHEMA).page, 42);
});

test('list: elements with commas and spaces survive the split (inner-encoded)', () => {
  const qs = writeParams({ tags: ['urgente', 'a,b', 'c d'] }, [list('tags')]);
  const back = readParams(qs, [list('tags')]);
  assert.deepEqual(back.tags, ['urgente', 'a,b', 'c d']);
});

test('list: empty list is omitted', () => {
  assert.equal(writeParams({ tags: [] }, [list('tags')]), '');
});

test('json: invalid JSON on read decodes to null', () => {
  // "adv=%7Bbroken" — an un-parseable fragment must not throw.
  const back = readParams('adv=%7Bbroken', SCHEMA);
  assert.equal(back.adv, null);
});

test('json: default spec is omitted, non-default spec is serialized', () => {
  assert.equal(writeParams({ adv: { advFilters: [] } }, SCHEMA), '');
  const qs = writeParams({ adv: { advFilters: [{ dim: 'tag', op: 'in', value: ['vip'] }] } }, SCHEMA);
  assert.match(qs, /^adv=/);
  assert.deepEqual(readParams(qs, SCHEMA).adv, { advFilters: [{ dim: 'tag', op: 'in', value: ['vip'] }] });
});

test('str: a non-empty default is omitted only when it matches', () => {
  const schema = [str('mode', 'assignee')];
  assert.equal(writeParams({ mode: 'assignee' }, schema), '');
  assert.equal(writeParams({ mode: 'status' }, schema), 'mode=status');
  assert.equal(readParams('', schema).mode, 'assignee');
});
