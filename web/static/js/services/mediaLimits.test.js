// Testes puros da pré-validação de anexo (node --test).
//   node --test web/static/js/services/mediaLimits.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { checkMediaFile, fmtSize, limitsSummary, VIDEO_INPUT_CEILING_BYTES } from './mediaLimits.js';

const CLOUD = {
  image: { max_bytes: 5 * 1024 * 1024, extensions: ['.jpg', '.jpeg', '.png'] },
  document: { max_bytes: 100 * 1024 * 1024, extensions: ['.pdf', '.txt'] },
  video: { max_bytes: 16 * 1024 * 1024, extensions: ['.mp4', '.3gp'], transcode: false },
};

test('canal sem limites nunca bloqueia (só o teto de entrada de vídeo vale)', () => {
  assert.equal(checkMediaFile({ name: 'a.mkv', size: 500 * 1024 * 1024 }, 'document', null), null);
  assert.equal(checkMediaFile({ name: 'a.exe', size: 10 ** 9 }, 'document', {}), null);
  assert.equal(checkMediaFile({ name: 'a.mkv', size: 50 * 1024 * 1024 }, 'video', null), null);
});

test('imagem fora do formato -> bad_format', () => {
  const bad = checkMediaFile({ name: 'foto.gif', size: 10 }, 'image', CLOUD);
  assert.equal(bad.reason, 'bad_format');
  assert.match(bad.message, /JPG\/JPEG\/PNG/);
});

test('imagem acima do cap -> too_big citando o limite', () => {
  const bad = checkMediaFile({ name: 'foto.png', size: 6 * 1024 * 1024 }, 'image', CLOUD);
  assert.equal(bad.reason, 'too_big');
  assert.match(bad.message, /5 MB/);
});

test('arquivo conforme passa (extensão case-insensitive)', () => {
  assert.equal(checkMediaFile({ name: 'FOTO.PNG', size: 1024 }, 'image', CLOUD), null);
  assert.equal(checkMediaFile({ name: 'doc.pdf', size: 1024 * 1024 }, 'document', CLOUD), null);
});

test('tipo sem declaração no canal não é bloqueado', () => {
  assert.equal(checkMediaFile({ name: 'voz.ogg', size: 10 ** 9 }, 'audio', CLOUD), null);
});

test('vídeo com transcode disponível não bloqueia (servidor recomprime)', () => {
  const limits = { video: { ...CLOUD.video, transcode: true } };
  assert.equal(checkMediaFile({ name: 'clip.mkv', size: 50 * 1024 * 1024 }, 'video', limits), null);
});

test('vídeo acima do teto de entrada bloqueia mesmo com transcode', () => {
  const limits = { video: { ...CLOUD.video, transcode: true } };
  const bad = checkMediaFile(
    { name: 'clip.mp4', size: VIDEO_INPUT_CEILING_BYTES + 1 }, 'video', limits);
  assert.equal(bad.reason, 'too_big');
});

test('áudio com transcode disponível não bloqueia (servidor recodifica)', () => {
  const entry = { max_bytes: 16 * 1024 * 1024, extensions: ['.mp3', '.ogg'] };
  // Sem transcode o formato estranho é barrado no compositor…
  assert.equal(
    checkMediaFile({ name: 'voz.wav', size: 1024 }, 'audio', { audio: entry }).reason,
    'bad_format');
  // …com transcode ligado o servidor conserta, então o painel deixa passar.
  assert.equal(
    checkMediaFile({ name: 'voz.wav', size: 1024 }, 'audio',
      { audio: { ...entry, transcode: true } }),
    null);
});

test('isAttachmentOfKind reconhece áudio por mime, por extensão do canal e por fallback', async () => {
  const { isAttachmentOfKind, isAudioAttachment } = await import('./mediaLimits.js');
  const limits = { audio: { max_bytes: 16 * 1024 * 1024, extensions: ['.ogg', '.mp3'] } };
  // mime explícito
  assert.equal(isAttachmentOfKind({ name: 'x', type: 'audio/ogg' }, 'audio', null), true);
  // extensão declarada pelo canal, sem mime (browser entregou type vazio)
  assert.equal(isAttachmentOfKind({ name: 'voz.ogg', type: '' }, 'audio', limits), true);
  // fallback de container comum, canal sem declaração
  assert.equal(isAudioAttachment({ name: 'voz.wav', type: '' }, null), true);
  // não-áudio continua sendo documento
  assert.equal(isAudioAttachment({ name: 'doc.pdf', type: 'application/pdf' }, limits), false);
});

test('fmtSize / limitsSummary', () => {
  assert.equal(fmtSize(512 * 1024), '512 KB');
  assert.equal(fmtSize(16 * 1024 * 1024), '16 MB');
  assert.match(limitsSummary('image', CLOUD.image), /JPG, JPEG, PNG.*até 5 MB/);
  assert.equal(limitsSummary('image', null), '');
});

test('isVideoAttachment reconhece vídeo por mime, por extensão do canal e por fallback', async () => {
  const { isVideoAttachment } = await import('./mediaLimits.js');
  assert.equal(isVideoAttachment({ name: 'clip', type: 'video/mp4' }, CLOUD), true);
  assert.equal(isVideoAttachment({ name: 'clip.MP4', type: '' }, CLOUD), true);
  assert.equal(isVideoAttachment({ name: 'clip.mkv', type: '' }, null), true);
  assert.equal(isVideoAttachment({ name: 'doc.pdf', type: 'application/pdf' }, CLOUD), false);
  assert.equal(isVideoAttachment(null, CLOUD), false);
});
