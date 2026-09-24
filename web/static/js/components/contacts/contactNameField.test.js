// Run with: node --test web/static/js/components/contacts/contactNameField.test.js
//
// Locks the "Nome" field of the contact panel: a group comes pre-filled with its
// WhatsApp name and locked (even for an admin), a person stays editable.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { contactNameField, infoPayloadFor } from './contactNameField.js';

test('group → pre-filled with group_name and locked, even with contact.write', () => {
  const f = contactNameField({ isGroup: true, groupName: 'Teste', infoName: '', canWrite: true });
  assert.equal(f.value, 'Teste');
  assert.equal(f.locked, true);
  assert.ok(f.hint);
});

test('group ignores a stale contacts.name and always mirrors group_name', () => {
  const f = contactNameField({ isGroup: true, groupName: 'Equipe', infoName: 'antigo', canWrite: true });
  assert.equal(f.value, 'Equipe');
});

test('group whose name has not been synced yet → empty, still locked', () => {
  const f = contactNameField({ isGroup: true, groupName: '', infoName: '', canWrite: true });
  assert.equal(f.value, '');
  assert.equal(f.locked, true);
  assert.match(f.placeholder, /grupo/i);
});

test('person → editable with contact.write, "~" pushName marker stripped', () => {
  const f = contactNameField({ isGroup: false, groupName: '', infoName: '~Bia', canWrite: true });
  assert.equal(f.value, 'Bia');
  assert.equal(f.locked, false);
  assert.equal(f.hint, null);
});

test('person without contact.write → locked', () => {
  const f = contactNameField({ isGroup: false, groupName: '', infoName: 'Bia', canWrite: false });
  assert.equal(f.locked, true);
});

test('save payload drops name for a group, keeps the rest', () => {
  const form = { name: 'Teste', observations: ['a'] };
  assert.deepEqual(infoPayloadFor(true, form), { observations: ['a'] });
});

test('save payload is untouched for a person', () => {
  const form = { name: 'Bia', observations: [] };
  assert.equal(infoPayloadFor(false, form), form);
});
