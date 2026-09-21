import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CORE_GROUP_ORDER } from './permissionGroupOrder.js';

test('grupo Times aparece logo após Atendimentos e conversas', () => {
  const conversationsIndex = CORE_GROUP_ORDER.indexOf('Atendimentos e conversas');

  assert.notEqual(conversationsIndex, -1);
  assert.equal(CORE_GROUP_ORDER[conversationsIndex + 1], 'Times');
});
