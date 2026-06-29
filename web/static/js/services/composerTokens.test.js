// Run with: node --test web/static/js/services/composerTokens.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  detectMentionToken, detectQuickReplyToken, replaceToken,
  mentionLabel, mentionCandidates, quickReplyCandidates, stripGroupPrefix,
} from './composerTokens.js';

// ── detectMentionToken ──────────────────────────────────────────────
test('detectMentionToken: @ at start of input', () => {
  assert.deepEqual(detectMentionToken('@jo', 3), { query: 'jo', start: 0 });
});

test('detectMentionToken: @ after whitespace', () => {
  assert.deepEqual(detectMentionToken('oi @ma', 6), { query: 'ma', start: 3 });
});

test('detectMentionToken: bare @ (empty query) opens the menu', () => {
  assert.deepEqual(detectMentionToken('hello @', 7), { query: '', start: 6 });
});

test('detectMentionToken: unicode/accented names match', () => {
  assert.deepEqual(detectMentionToken('@josé', 5), { query: 'josé', start: 0 });
});

test('detectMentionToken: @ mid-word (no preceding space) → null', () => {
  assert.equal(detectMentionToken('email@x', 7), null);
});

test('detectMentionToken: space after token closes it → null', () => {
  assert.equal(detectMentionToken('@joao ', 6), null);
});

// ── detectQuickReplyToken ───────────────────────────────────────────
test('detectQuickReplyToken: / at start', () => {
  assert.deepEqual(detectQuickReplyToken('/ola', 4), { query: 'ola', start: 0 });
});

test('detectQuickReplyToken: bare / opens', () => {
  assert.deepEqual(detectQuickReplyToken('/', 1), { query: '', start: 0 });
});

test('detectQuickReplyToken: hyphen allowed in short_code', () => {
  assert.deepEqual(detectQuickReplyToken('/bom-dia', 8), { query: 'bom-dia', start: 0 });
});

test('detectQuickReplyToken: slash inside URL (no leading space) → null', () => {
  assert.equal(detectQuickReplyToken('http://x', 8), null);
});

// ── replaceToken ────────────────────────────────────────────────────
test('replaceToken: swaps token region and reports caret', () => {
  // value "oi @ma", token at start=3..caret=6, insert "@Maria "
  const r = replaceToken('oi @ma', 3, 6, '@Maria ');
  assert.equal(r.value, 'oi @Maria ');
  assert.equal(r.caret, '@Maria '.length + 3);
});

test('replaceToken: preserves text after the caret', () => {
  const r = replaceToken('a @b end', 2, 4, '@Bob ');
  assert.equal(r.value, 'a @Bob  end');
  assert.equal(r.caret, 'a @Bob '.length);
});

// ── mentionLabel ────────────────────────────────────────────────────
test('mentionLabel: name preferred, falls back to phone then lid', () => {
  assert.equal(mentionLabel({ name: 'Ana', phone: '55', lid: 'x' }), 'Ana');
  assert.equal(mentionLabel({ phone: '5511', lid: 'x' }), '5511');
  assert.equal(mentionLabel({ lid: 'abc' }), 'abc');
  assert.equal(mentionLabel({}), '');
  assert.equal(mentionLabel(null), '');
});

// ── mentionCandidates ───────────────────────────────────────────────
const MEMBERS = [
  { name: 'Ana', phone: '1', is_admin: true },
  { name: 'Bruno', phone: '2' },
  { name: 'Carla', phone: '3' },
];

test('mentionCandidates: empty query → todos + all members', () => {
  const c = mentionCandidates('', MEMBERS);
  assert.equal(c[0].special, true);
  assert.equal(c.length, 4);
});

test('mentionCandidates: query filters by label (case-insensitive, substring)', () => {
  const c = mentionCandidates('ar', MEMBERS); // matches "Carla"
  assert.equal(c.some(m => m.special), false);
  assert.deepEqual(c.map(m => m.name), ['Carla']);
});

test('mentionCandidates: "todos" prefix keeps special entry', () => {
  const c = mentionCandidates('to', MEMBERS);
  assert.equal(c[0].special, true);
});

test('mentionCandidates: capped at 8', () => {
  const many = Array.from({ length: 20 }, (_, i) => ({ name: 'X' + i }));
  assert.equal(mentionCandidates('x', many).length, 8);
});

test('mentionCandidates: null members → just todos for empty query', () => {
  assert.deepEqual(mentionCandidates('', null), [{ special: true, name: 'todos' }]);
});

// ── quickReplyCandidates ────────────────────────────────────────────
const QRS = [
  { id: 1, short_code: 'ola', content: 'Olá, tudo bem?' },
  { id: 2, short_code: 'bye', content: 'Até logo' },
];

test('quickReplyCandidates: matches short_code', () => {
  assert.deepEqual(quickReplyCandidates('ola', QRS).map(q => q.id), [1]);
});

test('quickReplyCandidates: matches content too', () => {
  assert.deepEqual(quickReplyCandidates('logo', QRS).map(q => q.id), [2]);
});

test('quickReplyCandidates: empty query returns all (capped 8)', () => {
  assert.equal(quickReplyCandidates('', QRS).length, 2);
});

test('quickReplyCandidates: null list → []', () => {
  assert.deepEqual(quickReplyCandidates('x', null), []);
});

// ── stripGroupPrefix ────────────────────────────────────────────────
test('stripGroupPrefix: splits "[Sender]: text"', () => {
  assert.deepEqual(stripGroupPrefix('[Ana]: oi pessoal'),
    { sender: 'Ana', text: 'oi pessoal' });
});

test('stripGroupPrefix: multiline body kept', () => {
  assert.deepEqual(stripGroupPrefix('[Bob]: linha1\nlinha2'),
    { sender: 'Bob', text: 'linha1\nlinha2' });
});

test('stripGroupPrefix: no prefix → sender null, text unchanged', () => {
  assert.deepEqual(stripGroupPrefix('plain text'),
    { sender: null, text: 'plain text' });
});

test('stripGroupPrefix: empty/null content → empty text', () => {
  assert.deepEqual(stripGroupPrefix(''), { sender: null, text: '' });
  assert.deepEqual(stripGroupPrefix(null), { sender: null, text: '' });
});
