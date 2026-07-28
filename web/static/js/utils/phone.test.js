// Run with: node --test web/static/js/utils/phone.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatPhoneDisplay, formatPhone, samePhone } from './phone.js';

test('empty / nullish → empty string', () => {
  assert.equal(formatPhoneDisplay(''), '');
  assert.equal(formatPhoneDisplay(null), '');
  assert.equal(formatPhoneDisplay(undefined), '');
});

test('BR mobile (13 digits, 9-digit subscriber) → +55 (AA) XXXXX-XXXX', () => {
  // 55 11 999998888 → +55 (11) 99999-8888
  assert.equal(formatPhoneDisplay('5511999998888'), '+55 (11) 99999-8888');
  // 55 64 911110001 → +55 (64) 91111-0001 (mirrors the ContactList.js comment case)
  assert.equal(formatPhoneDisplay('5564911110001'), '+55 (64) 91111-0001');
});

test('BR landline / 8-digit cell (12 digits) → +55 (AA) XXXX-XXXX', () => {
  // 55 85 97360559 → +55 (85) 9736-0559  (Family B split, the CORRECT one)
  assert.equal(formatPhoneDisplay('558597360559'), '+55 (85) 9736-0559');
  // 55 11 33334444 → +55 (11) 3333-4444
  assert.equal(formatPhoneDisplay('551133334444'), '+55 (11) 3333-4444');
});

test('non-BR ≥12-digit international number → grouped with + prefix', () => {
  // 13-digit non-BR (e.g. US-ish): falls into the generic ≥12 branch.
  assert.equal(formatPhoneDisplay('1415551234567'), '+14 (15) 55123-4567');
  // 12-digit non-BR
  assert.equal(formatPhoneDisplay('441234567890'), '+44 (12) 34567-890');
});

test('short / unknown shape → just + prefix (Family B behaviour)', () => {
  assert.equal(formatPhoneDisplay('5511999'), '+5511999');
  assert.equal(formatPhoneDisplay('12345'), '+12345');
});

test('accepts numeric input (coerced to string)', () => {
  assert.equal(formatPhoneDisplay(5511999998888), '+55 (11) 99999-8888');
});

test('formatPhone is an alias for formatPhoneDisplay', () => {
  assert.equal(formatPhone, formatPhoneDisplay);
  assert.equal(formatPhone('5511999998888'), '+55 (11) 99999-8888');
});

// ── plano 57: samePhone (digit-normalized comparison) ─────────────────────
test('samePhone: exact string equal → true', () => {
  assert.equal(samePhone('5564911110001', '5564911110001'), true);
});

test('samePhone: digit-equal but different format → true (broadens only)', () => {
  assert.equal(samePhone('+55 64 91111-0001', '5564911110001'), true);
  assert.equal(samePhone('5564911110001', '5564911110001@s.whatsapp.net'), true);
});

test('samePhone: distinct numbers → false (never crosses)', () => {
  assert.equal(samePhone('5564911110001', '5511999998888'), false);
});

test('samePhone: nullish / empty-after-strip → false', () => {
  assert.equal(samePhone(null, '5564911110001'), false);
  assert.equal(samePhone('5564911110001', undefined), false);
  assert.equal(samePhone('abc', 'def'), false);   // both strip to '' → false
  assert.equal(samePhone(null, null), true);       // identical ref short-circuit
});
