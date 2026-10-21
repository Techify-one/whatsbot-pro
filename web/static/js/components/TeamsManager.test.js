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

test('Times é sub-aba de Usuários, com permissão própria e /teams como alias legado', () => {
  // A sub-aba mora em UsersManager e a tela não tem mais rota/menu próprios.
  assert.match(usersSource, /import TeamsManager from '\.\/TeamsManager\.js'/);
  assert.match(usersSource, /view === 'teams' \? html`<\$\{TeamsManager\}/);
  assert.match(usersSource, /visibleUsersSubtabs\(currentUser\)/);
  assert.doesNotMatch(routerSource, /import TeamsManager/);
  assert.doesNotMatch(routerSource, /tab === 'teams'/);
  assert.doesNotMatch(menuSource, /tab === 'teams'/);
  assert.match(menuSource, /can\('users\.manage'\) \|\| can\('team\.manage'\)/);
  // Deep-link: /users/teams[/id] é o canônico; /teams[/id] só normaliza.
  assert.match(deepLinkSource, /if \(r\[0\] === 'teams'\) return \{ sub: 'teams'/);
  assert.match(deepLinkSource, /segs\[0\] === 'teams' && segs\.length <= 2/);
  assert.doesNotMatch(deepLinkSource, /tab: 'teams'/);
  assert.match(teamsSource, /history\.replaceState\(null, '', `\/users\$\{path\}/);
  assert.match(teamsSource, /tab: 'users'/);
});

test('busca e item aberto são refletidos na URL sem poluir histórico', () => {
  assert.match(teamsSource, /params\.set\('q', query\.trim\(\)\)/);
  assert.match(teamsSource, /history\.replaceState/);
  assert.match(teamsSource, /navigate\(\{ id: team\.id \}\)/);
});

test('layout segue Usuários/Grupos: coluna única, cabeçalho com botão à direita, card p-3', () => {
  // O formulário já foi duas colunas (xl:grid-cols-2) com cada bloco em sua caixa;
  // Usuários e Grupos de permissão são uma coluna de campos de largura total.
  assert.doesNotMatch(teamsSource, /grid-cols-2/);
  assert.match(teamsSource, /<form onSubmit=\$\{submit\}/);
  assert.doesNotMatch(teamsSource, /sticky bottom-0/);
  // Cabeçalho = descrição à esquerda + "+ Novo time" à direita (como RolesManager).
  assert.match(teamsSource, /flex items-center justify-between mb-4/);
  assert.match(teamsSource, /Times organizam quem enxerga/);
  // Card de lista com o mesmo esqueleto do RoleCard; edição abaixo de um border-t.
  assert.match(teamsSource, /<article class="bg-wa-panel border border-wa-border rounded-lg p-3">/);
  assert.match(teamsSource, /mt-3 border-t border-wa-border pt-3/);
  // Carregando = texto simples, não skeleton.
  assert.doesNotMatch(teamsSource, /animate-pulse-slow/);
});

test('interruptor da atribuição automática fica colado ao rótulo, não na ponta da linha', () => {
  // Com `justify-between` o switch ia para a borda direita, longe do texto, e não
  // se percebia que existia. Também precisa ficar na linha do TÍTULO (estável): a
  // descrição muda de tamanho ao ligar e arrastaria o botão sob o cursor.
  assert.match(teamsSource,
    /<div class="flex items-center gap-3">\s*<span id="team-auto-assign-label"[^>]*>Atribuir conversas automaticamente<\/span>\s*<button type="button" role="switch"/);
  assert.doesNotMatch(teamsSource, /flex items-center justify-between gap-3">\s*<span class="min-w-0">\s*<span id="team-auto-assign-label"/);
});
