// Run with: node --test web/static/js/services/apiSearch.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { searchInConversation } from './api.js';

test('searchInConversation encaminha AbortSignal e paginação ao transporte', async () => {
  const oldFetch = globalThis.fetch;
  const oldStorage = globalThis.localStorage;
  const controller = new AbortController();
  let seen = null;
  globalThis.localStorage = { getItem: () => '', removeItem: () => {} };
  globalThis.fetch = async (url, opts) => {
    seen = { url, opts };
    return {
      status: 200, ok: true,
      json: async () => ({ ok: true, data: { matches: [], total: 0 } }),
    };
  };
  try {
    const res = await searchInConversation(77, 'olá mundo', {
      limit: 25, offset: 50, signal: controller.signal,
    });
    assert.equal(res.ok, true);
    assert.equal(seen.opts.signal, controller.signal);
    assert.match(seen.url, /\/api\/atendimentos\/77\/messages\/search\?/);
    assert.match(seen.url, /q=ol%C3%A1%20mundo/);
    assert.match(seen.url, /limit=25/);
    assert.match(seen.url, /offset=50/);
  } finally {
    globalThis.fetch = oldFetch;
    globalThis.localStorage = oldStorage;
  }
});
