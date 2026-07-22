// Run with: node --test web/static/js/services/mediaQueue.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classifyFile, filenameFor, buildQueueItems, audioQueueItem, progressLabel,
} from './mediaQueue.js';

const f = (name, type, size = 10) => ({ name, type, size });

// ── classifyFile ────────────────────────────────────────────────────
test('zona "foto/vídeo": imagem vai inline', () => {
  assert.equal(classifyFile(f('a.png', 'image/png'), 'media'), 'image');
});

test('zona "foto/vídeo": vídeo vai inline', () => {
  assert.equal(classifyFile(f('a.mp4', 'video/mp4'), 'media'), 'video');
});

test('zona "arquivo": imagem vai como documento (original, sem compressão)', () => {
  assert.equal(classifyFile(f('a.png', 'image/png'), 'file'), 'document');
});

test('zona "arquivo": vídeo vai como documento', () => {
  assert.equal(classifyFile(f('a.mp4', 'video/mp4'), 'file'), 'document');
});

test('áudio cai em documento nas DUAS zonas (/send-audio é nota de voz)', () => {
  assert.equal(classifyFile(f('a.mp3', 'audio/mpeg'), 'media'), 'document');
  assert.equal(classifyFile(f('a.mp3', 'audio/mpeg'), 'file'), 'document');
});

test('pdf/zip/desconhecido caem em documento nas duas zonas', () => {
  for (const mode of ['media', 'file']) {
    assert.equal(classifyFile(f('a.pdf', 'application/pdf'), mode), 'document');
    assert.equal(classifyFile(f('a.zip', 'application/zip'), mode), 'document');
    assert.equal(classifyFile(f('a.qualquer', ''), mode), 'document');
  }
});

test('arquivo sem type nunca quebra a classificação', () => {
  assert.equal(classifyFile({}, 'media'), 'document');
  assert.equal(classifyFile(null, 'media'), 'document');
});

// ── filenameFor ─────────────────────────────────────────────────────
test('filenameFor: usa o nome real quando existe', () => {
  assert.equal(filenameFor(f('relatorio.pdf', 'application/pdf'), 'document'), 'relatorio.pdf');
});

test('filenameFor: sem nome cai num default por kind (nunca undefined)', () => {
  assert.equal(filenameFor({}, 'image'), 'imagem.jpg');
  assert.equal(filenameFor({}, 'video'), 'video.mp4');
  assert.equal(filenameFor({}, 'document'), 'arquivo');
});

// ── buildQueueItems ─────────────────────────────────────────────────
test('buildQueueItems: enfileira N arquivos preservando a ordem', () => {
  const items = buildQueueItems(
    [f('1.png', 'image/png'), f('2.pdf', 'application/pdf')], 'media');
  assert.equal(items.length, 2);
  assert.deepEqual(items.map(i => i.kind), ['image', 'document']);
  assert.deepEqual(items.map(i => i.filename), ['1.png', '2.pdf']);
});

test('buildQueueItems: todo item carrega filename, sendMode e status inicial', () => {
  const [item] = buildQueueItems([f('x.bin', '')], 'file');
  assert.equal(item.sendMode, 'file');
  assert.equal(item.filename, 'x.bin');
  assert.equal(item._status, 'queued');
  assert.equal(item.caption, '');
  assert.equal(item.error, null);
});

test('buildQueueItems: ids são únicos', () => {
  const items = buildQueueItems(
    [f('a.png', 'image/png'), f('b.png', 'image/png'), f('c.png', 'image/png')], 'media');
  assert.equal(new Set(items.map(i => i.id)).size, 3);
});

test('buildQueueItems: só imagem/vídeo ganham previewUrl', () => {
  const items = buildQueueItems(
    [f('a.png', 'image/png'), f('b.mp4', 'video/mp4'), f('c.pdf', 'application/pdf')],
    'media', { makePreviewUrl: () => 'blob:fake' });
  assert.deepEqual(items.map(i => i.previewUrl), ['blob:fake', 'blob:fake', null]);
});

test('buildQueueItems: imagem na zona "arquivo" não gera previewUrl (é documento)', () => {
  const items = buildQueueItems([f('a.png', 'image/png')], 'file',
    { makePreviewUrl: () => 'blob:fake' });
  assert.equal(items[0].previewUrl, null);
});

test('buildQueueItems: lista vazia/nula devolve []', () => {
  assert.deepEqual(buildQueueItems([], 'media'), []);
  assert.deepEqual(buildQueueItems(null, 'media'), []);
});

test('buildQueueItems: entradas falsy são descartadas', () => {
  assert.equal(buildQueueItems([null, f('a.png', 'image/png'), undefined], 'media').length, 1);
});

// ── audioQueueItem ──────────────────────────────────────────────────
test('audioQueueItem: clipe gravado vira item kind=audio', () => {
  const item = audioQueueItem({ blob: {}, filename: 'voice.ogg', previewUrl: 'blob:a' });
  assert.equal(item.kind, 'audio');
  assert.equal(item.filename, 'voice.ogg');
  assert.equal(item._status, 'queued');
});

// ── progressLabel ───────────────────────────────────────────────────
test('progressLabel: item único não mostra contagem', () => {
  assert.equal(progressLabel(0, 1), 'Enviando…');
});

test('progressLabel: fila mostra "N de M"', () => {
  assert.equal(progressLabel(0, 5), 'Enviando 1 de 5…');
  assert.equal(progressLabel(2, 5), 'Enviando 3 de 5…');
});

test('progressLabel: não passa do total no último item', () => {
  assert.equal(progressLabel(5, 5), 'Enviando 5 de 5…');
});

test('progressLabel: total zero devolve string vazia', () => {
  assert.equal(progressLabel(0, 0), '');
});
