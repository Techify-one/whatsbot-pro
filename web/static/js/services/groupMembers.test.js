// Run with: node --test web/static/js/services/groupMembers.test.js
//
// Locks the "Ver membros" list of a group: who is linkable, where the click goes,
// how the roster is ordered and searched.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memberEntries, membersCountLabel } from './groupMembers.js';

const roster = [
  { phone: '5511999990003', lid: '111', name: 'Zélia', is_admin: false },
  { phone: '5511999990001', lid: '222', name: 'Ana', is_admin: true },
  { phone: '5511999990002', lid: '333', name: '', is_admin: false },
  { phone: '', lid: '444', name: 'Só LID', is_admin: false },
  { phone: '', lid: '555', name: '', is_admin: false },
];

test('member with a real phone links to "Novo contato" pre-filled with phone and name', () => {
  const ana = memberEntries(roster).find((e) => e.name === 'Ana');
  assert.equal(ana.href, '/contacts?createPhone=5511999990001&createName=Ana');
  assert.equal(ana.isAdmin, true);
});

test('nameless member links by phone only (no createName) and is labelled by the phone', () => {
  const e = memberEntries(roster).find((x) => x.phone === '5511999990002');
  assert.equal(e.href, '/contacts?createPhone=5511999990002');
  assert.match(e.label, /^\+55 /);
  assert.equal(e.sub, '');
});

test('lid-only member is listed but NOT linkable — a LID is not a phone', () => {
  const e = memberEntries(roster).find((x) => x.name === 'Só LID');
  assert.equal(e.href, null);
  assert.equal(e.phone, '');
  assert.equal(e.sub, 'Número indisponível');
});

test('lid-only member with no name at all gets a placeholder label', () => {
  const e = memberEntries(roster).find((x) => x.key === '555');
  assert.equal(e.label, 'Participante sem número');
  assert.equal(e.href, null);
});

test('a phone outside 10-15 digits is rejected (never linkable)', () => {
  const [e] = memberEntries([{ phone: '12345', lid: '', name: 'Curto' }]);
  assert.equal(e.href, null);
});

test('"~" pushName marker is stripped from label and from the pre-filled name', () => {
  const [e] = memberEntries([{ phone: '5511999990009', name: '~Bia' }]);
  assert.equal(e.label, 'Bia');
  assert.equal(e.href, '/contacts?createPhone=5511999990009&createName=Bia');
});

test('order: named (A→Z, accent-insensitive), then phone-only, then anonymous', () => {
  const labels = memberEntries(roster).map((e) => e.key);
  assert.deepEqual(labels, ['5511999990001', '444', '5511999990003', '5511999990002', '555']);
});

test('search matches the name ignoring case and accents', () => {
  assert.deepEqual(memberEntries(roster, 'zelia').map((e) => e.name), ['Zélia']);
  assert.deepEqual(memberEntries(roster, 'ANA').map((e) => e.name), ['Ana']);
});

test('search matches phone digits, even when typed formatted', () => {
  assert.deepEqual(memberEntries(roster, '(11) 99999-0002').map((e) => e.phone), ['5511999990002']);
  assert.deepEqual(memberEntries(roster, '99990002').map((e) => e.phone), ['5511999990002']);
});

test('search without a match → empty list', () => {
  assert.deepEqual(memberEntries(roster, 'xyz'), []);
});

test('non-array / null entries are tolerated', () => {
  assert.deepEqual(memberEntries(null), []);
  assert.deepEqual(memberEntries([null, undefined]), []);
});

test('count label is singular for 1', () => {
  assert.equal(membersCountLabel(1), '1 participante');
  assert.equal(membersCountLabel(0), '0 participantes');
  assert.equal(membersCountLabel(12), '12 participantes');
});
