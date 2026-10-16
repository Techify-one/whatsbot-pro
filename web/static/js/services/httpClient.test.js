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

// Plano 177 — campo repetido no multipart ("Agrupar": vários arquivos sob o mesmo nome).
import { uploadRequest } from './httpClient.js';

async function captureForm(fields) {
  // `authHeaders()` lê o token do navegador; o Node não tem `localStorage`.
  const realFetch = globalThis.fetch;
  const hadStorage = 'localStorage' in globalThis;
  const realStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  let body = null;
  globalThis.fetch = async (_url, opts) => {
    body = opts.body;
    return { status: 200, ok: true, json: async () => ({ ok: true }) };
  };
  try {
    await uploadRequest('/x', fields);
  } finally {
    globalThis.fetch = realFetch;
    if (hadStorage) globalThis.localStorage = realStorage; else delete globalThis.localStorage;
  }
  return body;
}

test('uploadRequest: array de arquivos vira o MESMO campo repetido, com nome', async () => {
  const a = new File(['1'], 'a.png', { type: 'image/png' });
  const b = new File(['2'], 'b.pdf', { type: 'application/pdf' });
  const form = await captureForm({ files: [a, b], caption: 'oi' });
  const files = form.getAll('files');
  assert.equal(files.length, 2);
  assert.deepEqual(files.map(f => f.name), ['a.png', 'b.pdf']);
  assert.equal(form.get('caption'), 'oi');
});

test('uploadRequest: valor único continua igual (regressão)', async () => {
  const a = new File(['1'], 'a.png', { type: 'image/png' });
  const form = await captureForm({ image: a, caption: '', channel_id: null });
  assert.equal(form.getAll('image').length, 1);
  assert.equal(form.get('image').name, 'a.png');
  assert.equal(form.get('channel_id'), '', 'null vira string vazia, como antes');
});

test('uploadRequest: array vazio não cria campo nenhum', async () => {
  const form = await captureForm({ files: [] });
  assert.equal(form.getAll('files').length, 0);
});
