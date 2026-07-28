// Run with: node --test web/static/js/services/uploadLimits.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_UPLOAD_BYTES, MAX_FILES_PER_DROP, formatBytes, applyUploadLimits, limitsMessage,
} from './uploadLimits.js';

const f = (name, size) => ({ name, size });
const MB = 1024 * 1024;

test('tetos espelham o backend (50 MB / 10 arquivos)', () => {
  assert.equal(MAX_UPLOAD_BYTES, 50 * MB);
  assert.equal(MAX_FILES_PER_DROP, 10);
});

test('formatBytes: MB com vírgula decimal (pt-BR)', () => {
  assert.equal(formatBytes(12.34 * MB), '12,3 MB');
});

test('formatBytes: abaixo de 1 MB mostra KB', () => {
  assert.equal(formatBytes(2048), '2 KB');
});

test('tudo dentro do teto passa intacto', () => {
  const r = applyUploadLimits([f('a', 1), f('b', 2)]);
  assert.equal(r.accepted.length, 2);
  assert.equal(r.tooLarge.length, 0);
  assert.equal(r.droppedForCount, 0);
});

test('arquivo grande demais é separado, os outros seguem', () => {
  const r = applyUploadLimits([f('ok', 10), f('gigante', 60 * MB), f('ok2', 10)]);
  assert.deepEqual(r.accepted.map(x => x.name), ['ok', 'ok2']);
  assert.deepEqual(r.tooLarge.map(x => x.name), ['gigante']);
});

test('exatamente no teto ainda passa', () => {
  const r = applyUploadLimits([f('limite', MAX_UPLOAD_BYTES)]);
  assert.equal(r.accepted.length, 1);
  assert.equal(r.tooLarge.length, 0);
});

test('acima do teto por 1 byte é recusado', () => {
  const r = applyUploadLimits([f('limite', MAX_UPLOAD_BYTES + 1)]);
  assert.equal(r.accepted.length, 0);
  assert.equal(r.tooLarge.length, 1);
});

test('quantidade acima do teto trunca e reporta o corte', () => {
  const files = Array.from({ length: 15 }, (_, i) => f(`f${i}`, 1));
  const r = applyUploadLimits(files);
  assert.equal(r.accepted.length, 10);
  assert.equal(r.droppedForCount, 5);
});

test('o corte por quantidade conta só o que passou no tamanho', () => {
  const files = [f('gigante', 60 * MB), ...Array.from({ length: 10 }, (_, i) => f(`f${i}`, 1))];
  const r = applyUploadLimits(files);
  assert.equal(r.accepted.length, 10);
  assert.equal(r.droppedForCount, 0);
  assert.equal(r.tooLarge.length, 1);
});

test('lista vazia/nula não quebra', () => {
  assert.equal(applyUploadLimits(null).accepted.length, 0);
  assert.equal(applyUploadLimits([]).accepted.length, 0);
});

test('arquivo sem size é tratado como 0 (passa)', () => {
  assert.equal(applyUploadLimits([{ name: 'x' }]).accepted.length, 1);
});

// ── limitsMessage ───────────────────────────────────────────────────
test('nada recusado → sem mensagem', () => {
  assert.equal(limitsMessage(applyUploadLimits([f('a', 1)])), null);
});

test('um arquivo grande → mensagem cita o nome e o tamanho', () => {
  const msg = limitsMessage(applyUploadLimits([f('video.mov', 60 * MB)]));
  assert.match(msg, /video\.mov/);
  assert.match(msg, /60,0 MB/);
  assert.match(msg, /50,0 MB/);
});

test('vários grandes → mensagem agregada', () => {
  const msg = limitsMessage(applyUploadLimits([f('a', 60 * MB), f('b', 60 * MB)]));
  assert.match(msg, /2 arquivos excedem/);
});

test('corte por quantidade entra na mensagem', () => {
  const files = Array.from({ length: 12 }, (_, i) => f(`f${i}`, 1));
  const msg = limitsMessage(applyUploadLimits(files));
  assert.match(msg, /2 foram ignorados/);
});
