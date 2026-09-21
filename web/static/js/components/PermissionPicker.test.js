import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CORE_GROUP_ORDER } from './permissionGroupOrder.js';

const source = readFileSync(new URL('./PermissionPicker.js', import.meta.url), 'utf8');

test('grupo Times aparece logo após Atendimentos e conversas', () => {
  const conversationsIndex = CORE_GROUP_ORDER.indexOf('Atendimentos e conversas');

  assert.notEqual(conversationsIndex, -1);
  assert.equal(CORE_GROUP_ORDER[conversationsIndex + 1], 'Times');
});

test('não apresenta conversation.read_all como inerte após o contrato de Times', () => {
  assert.doesNotMatch(source, /INERT_PERMISSIONS/);
  assert.doesNotMatch(source, /sem efeito por enquanto/);
});
