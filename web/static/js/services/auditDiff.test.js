// node --test web/static/js/services/auditDiff.test.js
import test from 'node:test';
import assert from 'node:assert/strict';

import { auditDiffView, diffObjects, changedPaths, deepEqual, withContext } from './auditDiff.js';

const CHANNEL_BEFORE = {
  id: 'whatsapp_cloud_3a45b0c0',
  provider: 'whatsapp_cloud',
  display_name: 'Whatsapp Oficial',
  enabled: true,
  archived: false,
  config: { ai: { ai_enabled: false, default_ai_enabled: true, max_context_messages: 10 } },
  credential_keys: ['access_token', 'app_id'],
};
const CHANNEL_AFTER = JSON.parse(JSON.stringify(CHANNEL_BEFORE));
CHANNEL_AFTER.config.ai.ai_enabled = true;

test('recorta o snapshot de canal para a única chave alterada + identificação', () => {
  const v = auditDiffView(JSON.stringify(CHANNEL_BEFORE), JSON.stringify(CHANNEL_AFTER));
  assert.equal(v.mode, 'diff');
  const ident = {
    id: 'whatsapp_cloud_3a45b0c0',
    provider: 'whatsapp_cloud',
    display_name: 'Whatsapp Oficial',
  };
  assert.deepEqual(JSON.parse(v.before), { ...ident, config: { ai: { ai_enabled: false } } });
  assert.deepEqual(JSON.parse(v.after), { ...ident, config: { ai: { ai_enabled: true } } });
  // O resumo/contagem conta SÓ o que mudou — identificação é contexto.
  assert.deepEqual(v.paths, ['config.ai.ai_enabled']);
});

test('ordem das chaves segue o snapshot (identificação primeiro)', () => {
  const v = auditDiffView(JSON.stringify(CHANNEL_BEFORE), JSON.stringify(CHANNEL_AFTER));
  assert.deepEqual(Object.keys(JSON.parse(v.after)), ['id', 'provider', 'display_name', 'config']);
});

test('campo de identificação que MUDOU aparece com o valor de cada lado', () => {
  const before = { id: 'ch1', display_name: 'Antigo', enabled: true };
  const after = { id: 'ch1', display_name: 'Novo', enabled: true };
  const v = auditDiffView(JSON.stringify(before), JSON.stringify(after));
  assert.deepEqual(JSON.parse(v.before), { id: 'ch1', display_name: 'Antigo' });
  assert.deepEqual(JSON.parse(v.after), { id: 'ch1', display_name: 'Novo' });
  assert.deepEqual(v.paths, ['display_name']);
});

test('withContext ignora identificação não-escalar e chave fora do topo', () => {
  const source = { id: { nested: 1 }, name: 'x', config: { name: 'interno', v: 1 } };
  const out = withContext(source, { config: { v: 2 } });
  assert.deepEqual(out, { name: 'x', config: { v: 2 } });
});

test('o JSON completo continua disponível nos dois lados', () => {
  const v = auditDiffView(JSON.stringify(CHANNEL_BEFORE), JSON.stringify(CHANNEL_AFTER));
  assert.deepEqual(JSON.parse(v.beforeFull), CHANNEL_BEFORE);
  assert.deepEqual(JSON.parse(v.afterFull), CHANNEL_AFTER);
});

test('chave adicionada/removida fica no lado onde existe', () => {
  const [b, a] = diffObjects({ x: 1, sumiu: 'v' }, { x: 1, nova: 'w' });
  assert.deepEqual(b, { sumiu: 'v' });
  assert.deepEqual(a, { nova: 'w' });
});

test('array é atômico — muda inteiro, não por índice', () => {
  const [b, a] = diffObjects({ tags: ['a', 'b'] }, { tags: ['a', 'c'] });
  assert.deepEqual(b, { tags: ['a', 'b'] });
  assert.deepEqual(a, { tags: ['a', 'c'] });
  assert.deepEqual(changedPaths(b, a), ['tags']);
});

test('array idêntico não entra no diff', () => {
  const [b, a] = diffObjects({ tags: ['a', 'b'], n: 1 }, { tags: ['a', 'b'], n: 2 });
  assert.deepEqual(b, { n: 1 });
  assert.deepEqual(a, { n: 2 });
});

test('nada mudou => modo empty (não inventa diff)', () => {
  const raw = JSON.stringify(CHANNEL_BEFORE);
  const v = auditDiffView(raw, raw);
  assert.equal(v.mode, 'empty');
  assert.equal(v.before, null);
  assert.equal(v.after, null);
  assert.notEqual(v.beforeFull, null);
});

test('create/delete (um lado só) mostra o JSON inteiro', () => {
  const created = auditDiffView(null, JSON.stringify({ name: 'novo' }));
  assert.equal(created.mode, 'full');
  assert.deepEqual(JSON.parse(created.after), { name: 'novo' });
  assert.equal(created.before, null);

  const deleted = auditDiffView(JSON.stringify({ name: 'velho' }), '');
  assert.equal(deleted.mode, 'full');
  assert.deepEqual(JSON.parse(deleted.before), { name: 'velho' });
});

test('JSON inválido ou raiz que não é objeto cai no modo full sem quebrar', () => {
  const invalid = auditDiffView('{quebrado', '{"a":1}');
  assert.equal(invalid.mode, 'full');
  assert.equal(invalid.before, '{quebrado');

  const scalars = auditDiffView('1', '2');
  assert.equal(scalars.mode, 'full');

  const arrays = auditDiffView('[1,2]', '[1,3]');
  assert.equal(arrays.mode, 'full');
});

test('valor mascarado pelo backend não vira falso positivo', () => {
  // O repo grava "***" nos dois lados quando o segredo não mudou.
  const v = auditDiffView(
    JSON.stringify({ access_token: '***', name: 'a' }),
    JSON.stringify({ access_token: '***', name: 'b' }),
  );
  assert.deepEqual(v.paths, ['name']);
});

test('objeto vira escalar (e vice-versa) troca o valor inteiro', () => {
  const [b, a] = diffObjects({ cfg: { x: 1 } }, { cfg: null });
  assert.deepEqual(b, { cfg: { x: 1 } });
  assert.deepEqual(a, { cfg: null });
});

test('deepEqual cobre aninhamento e chaves faltando', () => {
  assert.ok(deepEqual({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] }));
  assert.ok(!deepEqual({ a: 1 }, { a: 1, b: 2 }));
  assert.ok(!deepEqual({ a: 1 }, { b: 1 }));
});
