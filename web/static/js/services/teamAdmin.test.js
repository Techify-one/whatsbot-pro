import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  filterTeamMembers,
  initialTeamForm,
  teamFormPayload,
  validateTeamForm,
} from './teamAdmin.js';

const users = [
  { id: 1, name: 'Ana', email: 'ana@example.com', is_active: true },
  { id: 2, name: 'Bruno', email: 'bruno@example.com', is_active: false },
];
const aiAgents = [
  { agent_key: 'sales', display_name: 'Vendas IA', enabled: true },
  { agent_key: 'old', display_name: 'Antigo', enabled: false },
];

test('create e edit partem do mesmo shape completo', () => {
  assert.deepEqual(Object.keys(initialTeamForm()).sort(), Object.keys(initialTeamForm({
    name: 'Suporte', access_mode: 'private', member_user_ids: [1],
    routing_mode: 'fixed_user', default_user_id: 1,
  })).sort());
});

test('payload limpa campos condicionais e preserva visible_to_assignee no oculto', () => {
  const form = initialTeamForm();
  Object.assign(form, {
    name: ' Comercial ', accessMode: 'list_hidden', visibleToAssignee: true,
    routingMode: 'manual', defaultUserId: '1', defaultAgentKey: 'sales',
  });
  const payload = teamFormPayload(form);

  assert.equal(payload.name, 'Comercial');
  assert.equal(payload.visible_to_assignee, true);
  assert.equal(payload.default_user_id, null);
  assert.equal(payload.default_agent_key, null);
});

test('time privado exige membro e ampliação de acesso exige confirmação', () => {
  const form = initialTeamForm({ access_mode: 'private', member_user_ids: [1] });
  form.accessMode = 'open';
  form.memberIds = [];
  let errors = validateTeamForm(form, { users, aiAgents, originalTeam: { access_mode: 'private' } });
  assert.equal(errors.confirmAccessExpansion, 'Confirme que este ajuste ampliará o acesso às conversas.');

  form.accessMode = 'private';
  errors = validateTeamForm(form, { users, aiAgents });
  assert.equal(errors.members, 'Selecione ao menos um membro para um time privado.');
});

test('atendente fixo precisa estar ativo e selecionado como membro', () => {
  const form = initialTeamForm();
  Object.assign(form, { name: 'Suporte', routingMode: 'fixed_user', defaultUserId: '1' });
  let errors = validateTeamForm(form, { users, aiAgents });
  assert.match(errors.defaultUserId, /membro/);

  form.memberIds = [1];
  errors = validateTeamForm(form, { users, aiAgents });
  assert.equal(errors.defaultUserId, undefined);

  form.defaultUserId = '2';
  form.memberIds = [2];
  errors = validateTeamForm(form, { users, aiAgents });
  assert.match(errors.defaultUserId, /ativo/);
  assert.match(errors.members, /inativos/);
});

test('agente fixo precisa existir e estar habilitado', () => {
  const form = initialTeamForm();
  Object.assign(form, { name: 'Bot', routingMode: 'fixed_ai', defaultAgentKey: 'old' });
  assert.match(validateTeamForm(form, { users, aiAgents }).defaultAgentKey, /habilitado/);
  form.defaultAgentKey = 'sales';
  assert.equal(validateTeamForm(form, { users, aiAgents }).defaultAgentKey, undefined);
});

test('busca de membros considera nome e email, sem alterar a lista', () => {
  assert.deepEqual(filterTeamMembers(users, 'EXAMPLE.COM').map(user => user.id), [1, 2]);
  assert.deepEqual(filterTeamMembers(users, 'ana').map(user => user.id), [1]);
  assert.equal(users.length, 2);
});
