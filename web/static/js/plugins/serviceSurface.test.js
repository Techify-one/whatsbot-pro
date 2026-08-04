import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  LEGACY_V1_SERVICE_NAMES,
  buildVersionedServiceSurface,
} from './serviceSurface.js';

test('services 2.x contains only the current curated surface', () => {
  const current = { getStatus: () => 'ok' };
  const surface = buildVersionedServiceSurface(current, '2.0');

  assert.notEqual(surface, current);
  assert.equal(surface.getStatus(), 'ok');
  for (const name of LEGACY_V1_SERVICE_NAMES) {
    assert.equal(name in surface, false, name);
  }
});

test('services 1.x preserves both removed helpers through generic HTTP', async () => {
  const calls = [];
  const http = {
    get: async (path) => { calls.push(['GET', path]); return { ok: true }; },
    post: async (path, body) => {
      calls.push(['POST', path, body]);
      return { ok: true };
    },
  };
  const surface = buildVersionedServiceSurface({}, '1.0', http);

  await surface.getGowaAlertSettings();
  await surface.getConversationLabelsBatch([11, 22]);
  assert.deepEqual(calls, [
    ['GET', '/api/plugins/gowa/alert-settings'],
    ['POST', '/api/atendimentos/labels-batch', { ids: [11, 22] }],
  ]);
});

test('services 1.x cannot be built without its compatibility transport', () => {
  assert.throws(
    () => buildVersionedServiceSurface({}, '1.0'),
    /legacyHttp is required/,
  );
});
