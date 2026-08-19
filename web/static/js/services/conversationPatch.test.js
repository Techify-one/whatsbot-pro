// Run with: node --test web/static/js/services/conversationPatch.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  eventTargetsRow, conversationPatch, applyConversationEvent, isConversationAttributeWrite,
} from './conversationPatch.js';

test('eventTargetsRow: row with conv id matches by conversation_id', () => {
  const row = { conversation_id: 7, id: 1 };
  assert.equal(eventTargetsRow(row, { conversation_id: 7 }), true);
  assert.equal(eventTargetsRow(row, { conversation_id: 8 }), false);
  // contact_id alone must NOT match a row that already has a conversation
  assert.equal(eventTargetsRow(row, { contact_id: 1 }), false);
});

test('eventTargetsRow: legacy contact-only row matches by contact_id', () => {
  const row = { conversation_id: null, id: 1 };
  assert.equal(eventTargetsRow(row, { contact_id: 1 }), true);
  assert.equal(eventTargetsRow(row, { contact_id: 2 }), false);
  assert.equal(eventTargetsRow(row, { conversation_id: 9, contact_id: 1 }), true);
});

test('eventTargetsRow: null guards', () => {
  assert.equal(eventTargetsRow(null, { conversation_id: 1 }), false);
  assert.equal(eventTargetsRow({ conversation_id: 1 }, null), false);
});

test('conversationPatch: only present keys are written', () => {
  const row = { conversation_id: 7 };
  assert.deepEqual(conversationPatch(row, { status: 'fechado' }), { conv_status: 'fechado' });
  assert.deepEqual(conversationPatch(row, { assignee_user_id: 5 }), { assignee_user_id: 5 });
  assert.deepEqual(conversationPatch(row, { active_agent_key: 'k' }), { active_agent_key: 'k' });
  assert.deepEqual(conversationPatch(row, { ai_active: false }), { conv_ai_active: false });
  // assignee_user_id can be explicitly null (unassign) and must be written
  assert.deepEqual(conversationPatch(row, { assignee_user_id: null }), { assignee_user_id: null });
});

test('conversationPatch: empty event → empty patch', () => {
  assert.deepEqual(conversationPatch({ conversation_id: 7 }, {}), {});
});

test('conversationPatch: legacy row adopts conversation_id', () => {
  assert.deepEqual(
    conversationPatch({ conversation_id: null, id: 1 }, { conversation_id: 9, status: 'aberto' }),
    { conv_status: 'aberto', conversation_id: 9 },
  );
  // row that already has a conversation_id does NOT re-adopt
  assert.deepEqual(
    conversationPatch({ conversation_id: 7 }, { conversation_id: 7, status: 'aberto' }),
    { conv_status: 'aberto' },
  );
});

test('applyConversationEvent: patches only the targeted row', () => {
  const rows = [
    { conversation_id: 1, id: 10, conv_status: 'aberto' },
    { conversation_id: 2, id: 10, conv_status: 'aberto' },
    { conversation_id: 3, id: 11, conv_status: 'aberto' },
  ];
  const out = applyConversationEvent(rows, { conversation_id: 2, status: 'fechado' });
  assert.equal(out[0].conv_status, 'aberto');
  assert.equal(out[1].conv_status, 'fechado');
  assert.equal(out[2].conv_status, 'aberto');
  // untouched rows keep the same reference
  assert.equal(out[0], rows[0]);
  assert.notEqual(out[1], rows[1]);
});

test('applyConversationEvent: targeting one channel does not touch siblings', () => {
  const rows = [
    { conversation_id: 1, id: 10, assignee_user_id: null },
    { conversation_id: 2, id: 10, assignee_user_id: null }, // same contact, other channel
  ];
  const out = applyConversationEvent(rows, { conversation_id: 1, assignee_user_id: 5 });
  assert.equal(out[0].assignee_user_id, 5);
  assert.equal(out[1].assignee_user_id, null);
});

test('applyConversationEvent: empty patch returns same row reference', () => {
  const rows = [{ conversation_id: 1, id: 10 }];
  const out = applyConversationEvent(rows, { conversation_id: 1 }); // no patchable keys
  assert.equal(out[0], rows[0]);
});

test('applyConversationEvent: non-array → returned as-is', () => {
  assert.equal(applyConversationEvent(null, { conversation_id: 1 }), null);
});

test('isConversationAttributeWrite', () => {
  assert.equal(isConversationAttributeWrite(
    { conversation_id: 1, fields: { custom_attributes: { a: 1 } } }), true);
  assert.equal(isConversationAttributeWrite(
    { conversation_id: 1, fields: {} }), false);
  assert.equal(isConversationAttributeWrite({ conversation_id: 1 }), false);
  assert.equal(isConversationAttributeWrite({ fields: { custom_attributes: {} } }), false);
  assert.equal(isConversationAttributeWrite(null), false);
});

// ── plano 130 · F2 — identidade do array preservada no no-op ─────────────────

test('applyConversationEvent: evento que não atinge ninguém devolve a MESMA lista', () => {
  const rows = [{ conversation_id: 1, conv_status: 'open' }, { conversation_id: 2 }];
  assert.equal(applyConversationEvent(rows, { conversation_id: 99, status: 'resolved' }), rows);
});

test('applyConversationEvent: evento que reafirma o estado atual é no-op de identidade', () => {
  const rows = [{ conversation_id: 1, conv_status: 'open', assignee_user_id: 7 }];
  const out = applyConversationEvent(rows, { conversation_id: 1, status: 'open', assignee_user_id: 7 });
  assert.equal(out, rows, 'nada mudou de valor ⇒ nada pode mudar de identidade');
});

test('applyConversationEvent: mudança real ⇒ array novo, linhas alheias intactas', () => {
  const rows = [{ conversation_id: 1, conv_status: 'open' }, { conversation_id: 2, conv_status: 'open' }];
  const out = applyConversationEvent(rows, { conversation_id: 2, status: 'resolved' });
  assert.notEqual(out, rows);
  assert.equal(out[0], rows[0]);
  assert.equal(out[1].conv_status, 'resolved');
  assert.equal(rows[1].conv_status, 'open', 'entrada não é mutada');
});
