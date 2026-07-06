// Run with: node --test web/static/js/services/messages.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  sameMessage, isDuplicateMessage, findDuplicateIndex, optimisticDupIndex,
  mediaPreviewLabel, DEDUP_WINDOW_S,
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
  // Regression guard: the old content-only predicate WOULD have merged them.
  assert.equal(findDuplicateIndex(incoming, list), 0);
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
