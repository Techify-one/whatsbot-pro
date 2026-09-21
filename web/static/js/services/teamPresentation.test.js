// Run with: node --test web/static/js/services/teamPresentation.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  UNKNOWN_TEAM_LABEL,
  buildTeamMap,
  teamPresentation,
} from './teamPresentation.js';

test('buildTeamMap normalizes numeric and string ids without mutating the catalog', () => {
  const teams = [
    { id: 7, name: 'Comercial' },
    { id: '8', name: 'Suporte' },
    null,
    { id: null, name: 'Sem id' },
  ];

  const byId = buildTeamMap(teams);

  assert.equal(byId.get('7'), teams[0]);
  assert.equal(byId.get('8'), teams[1]);
  assert.equal(byId.size, 2);
  assert.equal(teams.length, 4);
});

test('teamPresentation omits conversations that have no team', () => {
  const byId = buildTeamMap([{ id: 1, name: 'Suporte' }]);

  assert.equal(teamPresentation(null, byId), null);
  assert.equal(teamPresentation(undefined, byId), null);
  assert.equal(teamPresentation('', byId), null);
});

test('teamPresentation resolves the team name across id types', () => {
  const byId = buildTeamMap([{ id: 9, name: '  Plantão Norte  ' }]);

  assert.deepEqual(teamPresentation('9', byId), {
    label: 'Plantão Norte',
    title: 'Time: Plantão Norte',
    unavailable: false,
  });
});

test('teamPresentation marks an inactive team in text, not only with color', () => {
  const byId = buildTeamMap([{ id: 4, name: 'Legado', is_active: false }]);

  assert.deepEqual(teamPresentation(4, byId), {
    label: 'Legado (inativo)',
    title: 'Time inativo: Legado',
    unavailable: false,
  });
});

test('teamPresentation uses a neutral fallback without exposing an unknown id', () => {
  const presentation = teamPresentation(987654, buildTeamMap([]));

  assert.equal(presentation.label, UNKNOWN_TEAM_LABEL);
  assert.equal(presentation.title, 'O time atual não está disponível no catálogo.');
  assert.equal(presentation.unavailable, true);
  assert.equal(JSON.stringify(presentation).includes('987654'), false);
});

test('teamPresentation treats a blank catalog name as unavailable', () => {
  const byId = buildTeamMap([{ id: 2, name: '   ' }]);

  assert.equal(teamPresentation(2, byId).label, UNKNOWN_TEAM_LABEL);
});
