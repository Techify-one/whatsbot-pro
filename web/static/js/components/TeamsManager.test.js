import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const teamsSource = readFileSync(new URL('./TeamsManager.js', import.meta.url), 'utf8');
const usersSource = readFileSync(new URL('./UsersManager.js', import.meta.url), 'utf8');
const routerSource = readFileSync(new URL('./shell/ScreenRouter.js', import.meta.url), 'utf8');
const menuSource = readFileSync(new URL('./shell/GearMenu.js', import.meta.url), 'utf8');
const deepLinkSource = readFileSync(new URL('../hooks/useDeepLink.js', import.meta.url), 'utf8');

test('create e edit compartilham TeamForm e não buscam catálogo por users.manage', () => {
  assert.match(teamsSource, /function TeamForm\(/);
  assert.doesNotMatch(teamsSource, /NewTeamForm|function EditTeamForm/);
  assert.doesNotMatch(teamsSource, /getUsers|listAgents|getAssignableAgents/);
  assert.match(teamsSource, /response\.data\?\.users/);
  assert.match(teamsSource, /response\.data\?\.ai_agents/);
});

test('formulário cobre acesso, routing, IA, membros e confirmação de expansão', () => {
  for (const marker of [
    'ACCESS_MODES', 'ROUTING_MODES', 'visibleToAssignee', 'defaultUserId',
    'defaultAgentKey', 'aiAssignable', 'confirmAccessExpansion',
    'Buscar por nome ou e-mail', 'Destino indisponível',
  ]) assert.match(teamsSource, new RegExp(marker));
  assert.match(teamsSource, /form\.accessMode !== 'open'/);
});

test('lifecycle só oferece hard delete com zero vínculos explicitamente informado', () => {
  assert.match(teamsSource, /Number\.isInteger\(team\.conversation_count\)/);
  assert.match(teamsSource, /team\.conversation_count === 0/);
  assert.match(teamsSource, /hard: action\.kind === 'hard'/);
  assert.match(teamsSource, /role="alertdialog"/);
  assert.doesNotMatch(teamsSource, /\bconfirm\(/);
});

test('rota /teams é separada, gated e mantém redirect legado após o gate', () => {
  assert.match(routerSource, /tab === 'teams'/);
  assert.match(routerSource, /hasPermission\(currentUser, 'team\.manage'\)/);
  assert.match(menuSource, /gated=\$\{can\('team\.manage'\)\}/);
  assert.match(deepLinkSource, /tab: 'teams', base: '\/teams'/);
  assert.match(deepLinkSource, /segs\[0\] === 'users' && segs\[1\] === 'teams'/);
  assert.doesNotMatch(usersSource, /import TeamsManager/);
  assert.doesNotMatch(usersSource, /id: 'teams', label: 'Times'/);
});

test('busca e item aberto são refletidos na URL sem poluir histórico', () => {
  assert.match(teamsSource, /params\.set\('q', query\.trim\(\)\)/);
  assert.match(teamsSource, /history\.replaceState/);
  assert.match(teamsSource, /navigate\(\{ id: team\.id \}\)/);
});
