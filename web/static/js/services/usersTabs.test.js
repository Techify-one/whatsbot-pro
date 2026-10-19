import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  canOpenUsersScreen,
  requestedUsersView,
  resolveUsersView,
  visibleUsersSubtabs,
} from './usersTabs.js';

const ids = (user) => visibleUsersSubtabs(user).map(tab => tab.id);
const admin = { permissions: ['*'] };
const onlyUsers = { permissions: ['users.manage'] };
const onlyTeams = { permissions: ['team.manage'] };
const neither = { permissions: ['conversation.read'] };

test('cada sub-aba aparece só para quem tem a SUA permissão', () => {
  assert.deepEqual(ids(admin), ['users', 'roles', 'teams']);
  assert.deepEqual(ids(onlyUsers), ['users', 'roles']);
  assert.deepEqual(ids(onlyTeams), ['teams']);
  assert.deepEqual(ids(neither), []);
});

test('instalação sem usuário ou permissões ainda não carregadas vê tudo (hide, don\'t disable)', () => {
  assert.deepEqual(ids(null), ['users', 'roles', 'teams']);
  assert.deepEqual(ids({}), ['users', 'roles', 'teams']);
});

test('a tela abre com users.manage OU team.manage, nunca sem nenhum dos dois', () => {
  assert.equal(canOpenUsersScreen(onlyUsers), true);
  assert.equal(canOpenUsersScreen(onlyTeams), true);
  assert.equal(canOpenUsersScreen(neither), false);
});

test('a URL escolhe a sub-aba; sub desconhecida cai em Usuários', () => {
  assert.equal(requestedUsersView({ sub: 'teams' }), 'teams');
  assert.equal(requestedUsersView({ sub: 'roles' }), 'roles');
  assert.equal(requestedUsersView({ id: 7 }), 'users');
  assert.equal(requestedUsersView(null), 'users');
});

test('sub-aba pedida sem permissão cai na primeira visível, nunca mostra a proibida', () => {
  assert.equal(resolveUsersView({ sub: 'teams' }, onlyUsers), 'users');
  assert.equal(resolveUsersView({ sub: 'roles' }, onlyTeams), 'teams');
  assert.equal(resolveUsersView(null, onlyTeams), 'teams');
  assert.equal(resolveUsersView({ sub: 'teams' }, admin), 'teams');
});
