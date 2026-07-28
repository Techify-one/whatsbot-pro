// Run with: node --test web/static/js/services/httpClient.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { handleErrorResponse } from './httpClient.js';
import { subscribe } from './notify.js';

// Fake fetch Response: status + JSON body. Mensagens distintas por teste para
// não colidir com o dedupe de 4s do barramento notify.
function fakeRes(status, body) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

test('handleErrorResponse: 403 sem silent → dispara toast de permissão', async () => {
  const toasts = [];
  const unsub = subscribe((t) => toasts.push(t));
  try {
    const out = await handleErrorResponse(fakeRes(403, { ok: false, error: 'Permissão negada. A' }));
    assert.deepEqual(out, { ok: false, error: 'Permissão negada. A', status: 403 });
    assert.equal(toasts.length, 1);              // toast emitido
    assert.equal(toasts[0].kind, 'error');
    assert.equal(toasts[0].message, 'Permissão negada. A');
  } finally { unsub(); }
});

test('handleErrorResponse: 403 com silent → NÃO dispara toast, envelope idêntico', async () => {
  const toasts = [];
  const unsub = subscribe((t) => toasts.push(t));
  try {
    const out = await handleErrorResponse(
      fakeRes(403, { ok: false, error: 'Permissão negada. B' }), { silent: true });
    assert.deepEqual(out, { ok: false, error: 'Permissão negada. B', status: 403 });
    assert.equal(toasts.length, 0);              // nenhum toast
  } finally { unsub(); }
});

test('handleErrorResponse: erro não-403 nunca toasta (mesmo sem silent)', async () => {
  const toasts = [];
  const unsub = subscribe((t) => toasts.push(t));
  try {
    const out = await handleErrorResponse(fakeRes(500, { ok: false, error: 'Boom' }));
    assert.deepEqual(out, { ok: false, error: 'Boom', status: 500 });
    assert.equal(toasts.length, 0);
  } finally { unsub(); }
});

test('handleErrorResponse: normaliza {detail} de plugin + fallback "Erro <status>"', async () => {
  const a = await handleErrorResponse(fakeRes(403, { detail: 'Sem acesso' }), { silent: true });
  assert.equal(a.error, 'Sem acesso');
  const b = await handleErrorResponse(
    { status: 404, ok: false, json: async () => { throw new Error('no body'); } }, { silent: true });
  assert.equal(b.error, 'Erro 404');
});
