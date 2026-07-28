// Run with: node --test web/static/js/services/messages.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  sameMessage, isDuplicateMessage, findDuplicateIndex, optimisticDupIndex,
  mediaPreviewLabel, DEDUP_WINDOW_S,
  dropSuperseded, mergeBufferedMessages,
} from './messages.js';

test('sameMessage: exact ts + role → dup', () => {
  assert.equal(sameMessage({ role: 'user', ts: 100, content: 'a' },
                           { role: 'user', ts: 100, content: 'DIFFERENT' }), true);
});

test('sameMessage: different role → never dup', () => {
  assert.equal(sameMessage({ role: 'user', ts: 100, content: 'a' },
                           { role: 'assistant', ts: 100, content: 'a' }), false);
});

test('sameMessage: same content within 30s window → dup', () => {
  assert.equal(sameMessage({ role: 'assistant', ts: 100, content: 'oi' },
                           { role: 'assistant', ts: 120, content: 'oi' }), true);
});

test('sameMessage: same content but >30s apart → not dup', () => {
  assert.equal(sameMessage({ role: 'assistant', ts: 100, content: 'oi' },
                           { role: 'assistant', ts: 100 + DEDUP_WINDOW_S, content: 'oi' }), false);
  assert.equal(sameMessage({ role: 'assistant', ts: 100, content: 'oi' },
                           { role: 'assistant', ts: 131, content: 'oi' }), false);
});

test('sameMessage: different content, different ts → not dup', () => {
  assert.equal(sameMessage({ role: 'user', ts: 100, content: 'a' },
                           { role: 'user', ts: 110, content: 'b' }), false);
});

test('sameMessage: null/undefined inputs → false', () => {
  assert.equal(sameMessage(null, { role: 'user', ts: 1 }), false);
  assert.equal(sameMessage({ role: 'user', ts: 1 }, undefined), false);
});

test('isDuplicateMessage: matches against a list', () => {
  const existing = [
    { role: 'user', ts: 50, content: 'hi' },
    { role: 'assistant', ts: 100, content: 'oi' },
  ];
  assert.equal(isDuplicateMessage({ role: 'assistant', ts: 115, content: 'oi' }, existing), true);
  assert.equal(isDuplicateMessage({ role: 'user', ts: 200, content: 'new' }, existing), false);
  assert.equal(isDuplicateMessage({ role: 'user', ts: 1 }, null), false);
});

test('findDuplicateIndex: returns index or -1', () => {
  const list = [
    { role: 'user', ts: 50, content: 'hi' },
    { role: 'assistant', ts: 100, content: 'oi' },
  ];
  assert.equal(findDuplicateIndex({ role: 'assistant', ts: 100, content: 'whatever' }, list), 1);
  assert.equal(findDuplicateIndex({ role: 'user', ts: 999, content: 'x' }, list), -1);
  assert.equal(findDuplicateIndex({ role: 'user', ts: 1 }, undefined), -1);
});

test('mediaPreviewLabel: plain text truncated to 80 chars', () => {
  const long = 'x'.repeat(120);
  assert.equal(mediaPreviewLabel({ content: long }).length, 80);
  assert.equal(mediaPreviewLabel({ content: 'short text' }), 'short text');
  assert.equal(mediaPreviewLabel({}), '');
});

test('mediaPreviewLabel: content-preferring media uses caption when present', () => {
  assert.equal(mediaPreviewLabel({ media_type: 'image', content: 'minha foto' }), 'minha foto');
  assert.equal(mediaPreviewLabel({ media_type: 'image' }), '📷 Imagem');
  assert.equal(mediaPreviewLabel({ media_type: 'document', content: 'nota.pdf' }), 'nota.pdf');
  assert.equal(mediaPreviewLabel({ media_type: 'location', content: 'Rua X' }), 'Rua X');
});

test('mediaPreviewLabel: label-only media ignores content', () => {
  assert.equal(mediaPreviewLabel({ media_type: 'audio', content: 'ignored' }), '🎤 Áudio');
  assert.equal(mediaPreviewLabel({ media_type: 'sticker', content: 'ignored' }), '🎨 Sticker');
  assert.equal(mediaPreviewLabel({ media_type: 'live_location', content: 'ignored' }), '📍 Localização ao vivo');
  assert.equal(mediaPreviewLabel({ media_type: 'product', content: 'ignored' }), '🏷️ Produto');
});

test('mediaPreviewLabel: contacts both singular and plural', () => {
  assert.equal(mediaPreviewLabel({ media_type: 'contact' }), '👤 Contato');
  assert.equal(mediaPreviewLabel({ media_type: 'contacts' }), '👤 Contato');
});

test('mediaPreviewLabel: unknown media_type falls back to text', () => {
  assert.equal(mediaPreviewLabel({ media_type: 'mystery', content: 'fallback' }), 'fallback');
});

// ── optimisticDupIndex (plano 33 F4) ──────────────────────────────────────

test('optimisticDupIndex: two distinct inbound "ok" (each with a msg_id) → APPEND (-1)', () => {
  // The already-settled "ok" carries a stable msg_id (A). A second, genuinely
  // new inbound "ok" (msg_id B) within 30s must NOT be swallowed into it.
  const list = [{ role: 'user', ts: 100, content: 'ok', msg_id: 'A' }];
  const incoming = { role: 'user', ts: 105, content: 'ok', msg_id: 'B' };
  assert.equal(optimisticDupIndex(incoming, list), -1);
  // Plano 53: identity is authoritative in EVERY predicate now — the content-only
  // fallback no longer applies when both sides carry a msg_id, so even the
  // plain findDuplicateIndex keeps the two real "ok" rows apart.
  assert.equal(findDuplicateIndex(incoming, list), -1);
});

test('optimisticDupIndex: operator echo collapses into its optimistic bubble (no msg_id)', () => {
  // The optimistic operator bubble has no msg_id yet; its server echo (msg_id C)
  // must still fold in — this preserves the echo-collapse the operator relies on.
  const list = [{ role: 'assistant', ts: 200, content: '[Áudio]' }];  // optimistic: no msg_id
  const echo = { role: 'assistant', ts: 201, content: '[Áudio]', msg_id: 'C' };
  assert.equal(optimisticDupIndex(echo, list), 0);
});

test('optimisticDupIndex: does not match a settled message that has a msg_id', () => {
  const list = [{ role: 'assistant', ts: 300, content: 'oi', msg_id: 'D' }];
  const incoming = { role: 'assistant', ts: 300, content: 'oi', msg_id: 'E' };
  assert.equal(optimisticDupIndex(incoming, list), -1);
});

test('optimisticDupIndex: non-array list → -1', () => {
  assert.equal(optimisticDupIndex({ role: 'user', ts: 1, content: 'x' }, undefined), -1);
  assert.equal(optimisticDupIndex({ role: 'user', ts: 1, content: 'x' }, null), -1);
});

// ── identidade estável `_id`/`msg_id` (plano 53) ──────────────────────────

test('sameMessage: same _id → dup even hours apart with different content', () => {
  // The clock-skew bug: optimistic bubble (client clock) vs WS copy (server ts)
  // of the SAME private note row must collapse regardless of the 30s window.
  assert.equal(sameMessage(
    { role: 'private_note', ts: 100, content: 'teste', _id: 1425 },
    { role: 'private_note', ts: 100 + 4 * 3600, content: 'teste (editado)', _id: 1425 },
  ), true);
});

test('sameMessage: different _id → never dup, even identical content 1s apart', () => {
  assert.equal(sameMessage(
    { role: 'private_note', ts: 100, content: 'teste', _id: 1 },
    { role: 'private_note', ts: 101, content: 'teste', _id: 2 },
  ), false);
});

test('sameMessage: same msg_id → dup; msg_id wins over _id', () => {
  assert.equal(sameMessage(
    { role: 'private_note', ts: 100, content: 'a', msg_id: 'pn:x' },
    { role: 'private_note', ts: 999, content: 'b', msg_id: 'pn:x' },
  ), true);
  // msg_id is checked first — a conflicting _id does not override it.
  assert.equal(sameMessage(
    { role: 'user', ts: 100, content: 'a', msg_id: 'M', _id: 1 },
    { role: 'user', ts: 100, content: 'a', msg_id: 'M', _id: 2 },
  ), true);
});

test('sameMessage: identity never crosses roles', () => {
  assert.equal(sameMessage(
    { role: 'user', ts: 100, content: 'a', _id: 7 },
    { role: 'assistant', ts: 100, content: 'a', _id: 7 },
  ), false);
});

test('sameMessage: one side without ids → legacy heuristic still applies', () => {
  // Optimistic bubble (no ids) vs server copy (both ids): content within 30s.
  assert.equal(sameMessage(
    { role: 'private_note', ts: 100, content: 'oi' },
    { role: 'private_note', ts: 110, content: 'oi', _id: 9, msg_id: 'pn:z' },
  ), true);
  // ...and outside the window it still fails (the reason F2/F3 exist).
  assert.equal(sameMessage(
    { role: 'private_note', ts: 100, content: 'oi' },
    { role: 'private_note', ts: 400, content: 'oi', _id: 9, msg_id: 'pn:z' },
  ), false);
});

test('optimisticDupIndex: WS copy folds into an _id-only bubble (post-POST)', () => {
  // Bubble already reconciled by the POST response (_id set, no msg_id yet):
  // the WS copy of the same row (same _id) must merge, not append.
  const list = [
    { role: 'private_note', ts: 100, content: 'teste', _id: 1425, _localId: 'local_1' },
  ];
  const wsCopy = { role: 'private_note', ts: 400, content: 'teste', _id: 1425 };
  assert.equal(optimisticDupIndex(wsCopy, list), 0);
});

test('isDuplicateMessage: buffered WS copy vs DB-loaded row matches by _id', () => {
  const loaded = [{ role: 'private_note', ts: 400, content: 'teste', _id: 1425, msg_id: 'pn:a' }];
  const buffered = { role: 'private_note', ts: 400.5, content: 'teste', _id: 1425, msg_id: 'pn:a' };
  assert.equal(isDuplicateMessage(buffered, loaded), true);
  // Two DISTINCT notes with identical content seconds apart stay apart.
  const other = { role: 'private_note', ts: 401, content: 'teste', _id: 1426, msg_id: 'pn:b' };
  assert.equal(isDuplicateMessage(other, loaded), false);
});

// ── plano 57: dropSuperseded ─────────────────────────────────────────────
test('dropSuperseded: removes bubbles whose msg_id is superseded', () => {
  const msgs = [
    { role: 'user', ts: 1, content: 'Oii', msg_id: 'A' },
    { role: 'user', ts: 2, content: 'Oiiiiiiiiii', msg_id: 'B' },
  ];
  const out = dropSuperseded(msgs, ['A']);
  assert.equal(out.length, 1);
  assert.equal(out[0].msg_id, 'B');
});

test('dropSuperseded: no supersedes → SAME array reference (no-op)', () => {
  const msgs = [{ role: 'user', ts: 1, content: 'x', msg_id: 'A' }];
  assert.equal(dropSuperseded(msgs, undefined), msgs);
  assert.equal(dropSuperseded(msgs, []), msgs);
  // supersedes present but nothing matches → still same ref
  assert.equal(dropSuperseded(msgs, ['Z']), msgs);
});

test('dropSuperseded: non-array messages → []', () => {
  assert.deepEqual(dropSuperseded(null, ['A']), []);
});

// ── plano 57: mergeBufferedMessages ──────────────────────────────────────
test('mergeBufferedMessages: authoritative combined row + supersedes collapses bubbles', () => {
  // History already has the individual t=0 bubbles (buffered before fetch); the
  // fetched DB row is the combined one; the buffered authoritative carries supersedes.
  const existing = [
    { role: 'user', ts: 1, content: 'Oii', msg_id: 'A' },
  ];
  const pending = [
    { role: 'user', ts: 2, content: 'Oii\nOiiiiiiiiii', msg_id: 'B', _id: 99,
      supersedes: ['A'], authoritative: true },
  ];
  const out = mergeBufferedMessages(existing, pending);
  // 'A' bubble collapsed; combined 'B' row present exactly once.
  assert.equal(out.length, 1);
  assert.equal(out[0].msg_id, 'B');
  assert.equal(out[0].content, 'Oii\nOiiiiiiiiii');
});

test('mergeBufferedMessages: combined DB row already present → authoritative dedups (no dup)', () => {
  const existing = [
    { role: 'user', ts: 2, content: 'Oii\nOiiiiiiiiii', msg_id: 'B', _id: 99 },
  ];
  const pending = [
    { role: 'user', ts: 1, content: 'Oii', msg_id: 'A' },  // orphan optimistic bubble
    { role: 'user', ts: 2, content: 'Oii\nOiiiiiiiiii', msg_id: 'B', _id: 99,
      supersedes: ['A'], authoritative: true },
  ];
  const out = mergeBufferedMessages(existing, pending);
  assert.equal(out.length, 1);
  assert.equal(out[0].msg_id, 'B');
});

test('mergeBufferedMessages: no pending → same existing ref', () => {
  const existing = [{ role: 'user', ts: 1, content: 'x', msg_id: 'A' }];
  assert.equal(mergeBufferedMessages(existing, []), existing);
});

test('mergeBufferedMessages: plain new inbound not in history → appended once', () => {
  const existing = [{ role: 'user', ts: 1, content: 'a', msg_id: 'A' }];
  const pending = [{ role: 'user', ts: 2, content: 'b', msg_id: 'B', _id: 5, authoritative: true }];
  const out = mergeBufferedMessages(existing, pending);
  assert.equal(out.length, 2);
  assert.equal(out[1].msg_id, 'B');
});

test('mergeBufferedMessages: t=0 optimistic (msg_id, no _id) then authoritative (_id) → one bubble', () => {
  // Loader ran before save; history empty. Buffer has the t=0 copy AND the
  // authoritative copy of the SAME message (same msg_id) → collapse to one.
  const out = mergeBufferedMessages([], [
    { role: 'user', ts: 1.0, content: 'Oii', msg_id: 'A' },
    { role: 'user', ts: 1.2, content: 'Oii', msg_id: 'A', _id: 42, authoritative: true },
  ]);
  assert.equal(out.length, 1);
  assert.equal(out[0].msg_id, 'A');
});

test('mergeBufferedMessages: batch during load — combined NOT yet in history keeps combined content (não perde fragmento)', () => {
  // Finding #2 (plano 57 review): fetch SELECT snapshot predates the save, so the
  // combined row is NOT in `existing`; the buffer holds optA, optB and the
  // authoritative combined (msg_id=B). Must render the COMBINED "a\nb", not "b".
  const existing = [];
  const pending = [
    { role: 'user', ts: 1.0, content: 'a', msg_id: 'A' },
    { role: 'user', ts: 1.1, content: 'b', msg_id: 'B' },
    { role: 'user', ts: 2.0, content: 'a\nb', msg_id: 'B', _id: 77,
      supersedes: ['A'], authoritative: true },
  ];
  const out = mergeBufferedMessages(existing, pending);
  assert.equal(out.length, 1);
  assert.equal(out[0].msg_id, 'B');
  assert.equal(out[0].content, 'a\nb');   // combined content wins, not the "b" fragment
  assert.equal(out[0]._id, 77);
});
