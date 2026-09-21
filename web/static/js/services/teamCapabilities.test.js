import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  UNKNOWN_TEAM_LABEL,
  accessModeOf,
  assignableTeamOptions,
  currentTeamReference,
  normalizeAssignableCatalog,
  normalizeAssignableCatalogResponse,
  normalizeTeamAdminResponse,
  readableTeamOptions,
  routableTeamOptions,
  teamUnavailableReason,
} from './teamCapabilities.js';

test('adapter mantém compatibilidade com catálogo legado sem inventar routing', () => {
  const data = normalizeAssignableCatalog({
    users: [{ id: 1 }],
    teams: [{ id: 7, name: 'Comercial' }],
  });

  assert.equal(data.contract, 'legacy');
  assert.deepEqual(data.ai_agents, []);
  assert.equal(data.teams[0].readable, true);
  assert.equal(data.teams[0].assignable, true);
  assert.equal(data.teams[0].routable, false);
  assert.equal(data.capabilities.provided, false);
});

test('payload novo parcial falha fechado por capability ausente', () => {
  const data = normalizeAssignableCatalog({
    teams: [
      { id: 1, name: 'Um', readable: true, assignable: true },
      { id: 2, name: 'Dois', readable: true },
      { id: 3, name: 'Três', routable: true, unavailable_reason: 'Fora da inbox.' },
    ],
    capabilities: { can_assign_own_team: true },
  });

  assert.equal(data.contract, 'capabilities-v1');
  assert.equal(data.teams[1].assignable, false);
  assert.equal(data.teams[0].routable, false);
  assert.equal(data.capabilities.can_assign_own_team, true);
  assert.equal(data.capabilities.can_route_team, false);
});

test('aceita envelope futuro com catalog aninhado', () => {
  const data = normalizeAssignableCatalog({
    catalog: { teams: [{ id: 4, name: 'Suporte', readable: true }] },
    capabilities: { can_manage_teams: true },
  });

  assert.equal(data.teams[0].name, 'Suporte');
  assert.equal(data.capabilities.can_manage_teams, true);
});

test('preserva erro e status sem mascarar motivo do servidor', () => {
  const error = { ok: false, error: 'Conflito de acesso.', status: 409 };
  assert.equal(normalizeAssignableCatalogResponse(error), error);
  assert.equal(normalizeTeamAdminResponse(error), error);
});

test('normaliza modos legado e novo no CRUD', () => {
  const response = normalizeTeamAdminResponse({
    ok: true,
    data: { teams: [
      { id: 1, name: 'Aberto' },
      { id: 2, name: 'Oculto', restrict_visibility: true },
      { id: 3, name: 'Privado', enforce_team_access: true },
    ] },
  });

  assert.deepEqual(response.data.teams.map(team => team.access_mode), [
    'open', 'list_hidden', 'private',
  ]);
  assert.equal(accessModeOf({ access_mode: 'private' }), 'private');
});

test('normaliza booleanos SQL 1/0 sem marcar time ativo como inativo', () => {
  const data = normalizeAssignableCatalog({ teams: [
    { id: 1, name: 'Ativo', is_active: 1, readable: 1, assignable: 1, routable: 0 },
    { id: 2, name: 'Inativo', is_active: 0, readable: 1, assignable: 0 },
  ] });
  const admin = normalizeTeamAdminResponse({
    ok: true,
    data: { team: { id: 3, name: 'Legado', is_active: 1 } },
  });

  assert.equal(data.teams[0].is_active, true);
  assert.equal(data.teams[0].assignable, true);
  assert.equal(data.teams[0].routable, false);
  assert.equal(data.teams[1].is_active, false);
  assert.equal(data.teams[1].assignable, false);
  assert.equal(admin.data.team.is_active, true);
});

test('mantém time atual fora do catálogo como referência somente leitura', () => {
  const teams = normalizeAssignableCatalog({
    teams: [{ id: 1, name: 'Comercial', readable: true, assignable: true }],
  }).teams;
  const current = currentTeamReference(teams, { id: '99', name: 'Legado' });
  const readable = readableTeamOptions(teams, { id: 99, name: 'Legado' });

  assert.equal(current.name, 'Legado');
  assert.equal(current.readable, true);
  assert.equal(current.assignable, false);
  assert.equal(current.routable, false);
  assert.equal(current.source, 'current-team-fallback');
  assert.deepEqual(readable.map(team => String(team.id)), ['1', '99']);
});

test('fallback desconhecido não revela id cru e opções respeitam capabilities', () => {
  const data = normalizeAssignableCatalog({ teams: [
    { id: 1, name: 'Leitura', readable: true, assignable: false },
    { id: 2, name: 'Destino', readable: true, assignable: true },
    { id: 3, name: 'Rota', readable: true, assignable: true, routable: true },
  ] });
  const fallback = currentTeamReference(data.teams, { id: 404 });

  assert.equal(fallback.name, UNKNOWN_TEAM_LABEL);
  assert.equal(fallback.name.includes('404'), false);
  assert.deepEqual(assignableTeamOptions(data.teams).map(team => team.id), [2, 3]);
  assert.deepEqual(routableTeamOptions(data.teams).map(team => team.id), [3]);
  assert.equal(teamUnavailableReason(data.teams[0]), 'Este time não está disponível para atribuição.');
  assert.equal(
    teamUnavailableReason({ assignable: false, unavailable_reason: 'Fora da inbox.' }),
    'Fora da inbox.',
  );
});
