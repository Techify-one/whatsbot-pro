import test from 'node:test';
import assert from 'node:assert';
import {
  isSeekable, ratioFromPointer, timeFromRatio,
  progressPercent, displayTime, nudge, formatClock,
} from './audioScrub.js';

// Plano 138 · F1 — a aritmética que o `AudioPlayer` fazia inline, sem clamp e
// sem teste nenhum. Os casos abaixo são os que mordiam de verdade em produção.

const rect = { left: 100, width: 200 };

// ─── isSeekable ────────────────────────────────────────────────────────────

test('isSeekable: só duração numérica, finita e positiva', () => {
  assert.equal(isSeekable(171.86), true);
  assert.equal(isSeekable(0.5), true);
});

test('isSeekable: os três estados inválidos que acontecem de verdade', () => {
  assert.equal(isSeekable(0), false, 'antes do loadedmetadata');
  assert.equal(isSeekable(NaN), false, 'arquivo sem cabeçalho');
  assert.equal(isSeekable(Infinity), false, 'stream/blob sem duração conhecida');
});

test('isSeekable: negativa e não-número não passam', () => {
  assert.equal(isSeekable(-1), false);
  assert.equal(isSeekable(null), false);
  assert.equal(isSeekable(undefined), false);
  // `isFinite` global faria coerção e deixaria uma string passar.
  assert.equal(isSeekable('30'), false);
});

// ─── ratioFromPointer ──────────────────────────────────────────────────────

test('ratioFromPointer: início, meio e fim da barra', () => {
  assert.equal(ratioFromPointer(100, rect), 0);
  assert.equal(ratioFromPointer(200, rect), 0.5);
  assert.equal(ratioFromPointer(300, rect), 1);
});

test('ratioFromPointer: ponteiro À ESQUERDA da barra vira 0, não negativo', () => {
  // O caso real: a bolinha do playhead tem 12px e transborda 6px para fora da
  // barra; um pointerdown nessa saliência dava `x` negativo e o código antigo
  // atribuía um currentTime negativo.
  assert.equal(ratioFromPointer(94, rect), 0);
  assert.equal(ratioFromPointer(-500, rect), 0);
});

test('ratioFromPointer: ponteiro à direita satura em 1 (captura de ponteiro)', () => {
  // Com setPointerCapture o pointermove continua chegando mesmo com o dedo fora
  // do elemento — é exatamente o que conserta o arraste, e é o que produz
  // coordenada além da largura.
  assert.equal(ratioFromPointer(9999, rect), 1);
});

test('ratioFromPointer: largura zero não vira NaN nem divisão por zero', () => {
  assert.equal(ratioFromPointer(150, { left: 100, width: 0 }), 0);
  assert.equal(ratioFromPointer(150, { left: 100, width: NaN }), 0);
  assert.equal(ratioFromPointer(150, null), 0);
  assert.equal(ratioFromPointer(NaN, rect), 0);
});

// ─── timeFromRatio ─────────────────────────────────────────────────────────

test('timeFromRatio: fração vira segundos', () => {
  assert.equal(timeFromRatio(0, 200), 0);
  assert.equal(timeFromRatio(0.25, 200), 50);
  assert.equal(timeFromRatio(1, 200), 200);
});

test('timeFromRatio: duração inutilizável devolve 0 (nunca NaN)', () => {
  for (const d of [0, NaN, Infinity, -5, null, undefined]) {
    const t = timeFromRatio(0.5, d);
    assert.equal(t, 0, `duração ${String(d)}`);
    assert.ok(Number.isFinite(t));
  }
});

test('timeFromRatio: fração fora de 0..1 é clampada nas duas pontas', () => {
  assert.equal(timeFromRatio(-3, 200), 0);
  assert.equal(timeFromRatio(7, 200), 200);
  assert.equal(timeFromRatio(NaN, 200), 0);
});

// ─── progressPercent ───────────────────────────────────────────────────────

test('progressPercent: sem arraste, segue o currentTime', () => {
  assert.equal(progressPercent({ currentTime: 50, duration: 200 }), 25);
  assert.equal(progressPercent({ currentTime: 0, duration: 200 }), 0);
});

test('progressPercent: DURANTE o arraste o arraste VENCE o currentTime (D5)', () => {
  // O áudio segue tocando durante o gesto: se o timeupdate mandasse na barra,
  // ela fugiria do dedo. É metade da sensação de "não voltou".
  const p = progressPercent({ currentTime: 180, duration: 200, scrubRatio: 0.1 });
  assert.equal(p, 10);
});

test('progressPercent: scrubRatio 0 vence (não pode cair no ramo do currentTime)', () => {
  // Armadilha clássica de `||`: retroceder para o começo é ratio 0, que é
  // falsy. Com um teste de verdade só `null` desliga o arraste.
  assert.equal(progressPercent({ currentTime: 180, duration: 200, scrubRatio: 0 }), 0);
});

test('progressPercent: duração inutilizável dá 0, sem NaN no `width: %`', () => {
  for (const d of [0, NaN, Infinity]) {
    assert.equal(progressPercent({ currentTime: 10, duration: d }), 0);
  }
  assert.equal(progressPercent(), 0);
});

test('progressPercent: currentTime além da duração não estoura 100%', () => {
  assert.equal(progressPercent({ currentTime: 500, duration: 200 }), 100);
});

// ─── displayTime ───────────────────────────────────────────────────────────

test('displayTime: durante o arraste mostra a posição do ARRASTE', () => {
  assert.equal(displayTime({ currentTime: 180, duration: 200, scrubRatio: 0.5 }), 100);
  assert.equal(displayTime({ currentTime: 180, duration: 200, scrubRatio: 0 }), 0);
});

test('displayTime: sem arraste mostra o currentTime, clampado', () => {
  assert.equal(displayTime({ currentTime: 42, duration: 200 }), 42);
  assert.equal(displayTime({ currentTime: 999, duration: 200 }), 200);
  assert.equal(displayTime({ currentTime: NaN, duration: 200 }), 0);
  assert.equal(displayTime({ currentTime: 42, duration: 0 }), 0);
});

// ─── nudge (teclado) ───────────────────────────────────────────────────────

test('nudge: retrocede e avança', () => {
  assert.equal(nudge(100, -5, 200), 95);
  assert.equal(nudge(100, 5, 200), 105);
});

test('nudge: clampado nas duas pontas', () => {
  assert.equal(nudge(2, -5, 200), 0);
  assert.equal(nudge(198, 5, 200), 200);
});

test('nudge: estado inválido nunca vira NaN', () => {
  assert.equal(nudge(NaN, -5, 200), 0);
  assert.equal(nudge(100, NaN, 200), 100);
  assert.equal(nudge(100, -5, 0), 0);
  assert.equal(nudge(100, -5, Infinity), 0);
});

// ─── formatClock ───────────────────────────────────────────────────────────

test('formatClock: mm:ss com zero à esquerda', () => {
  assert.equal(formatClock(0), '0:00');
  assert.equal(formatClock(9), '0:09');
  assert.equal(formatClock(65), '1:05');
  assert.equal(formatClock(171.86), '2:51');
  assert.equal(formatClock(600), '10:00');
});

test('formatClock: entrada inválida não vira "NaN:NaN"', () => {
  for (const v of [NaN, Infinity, -1, null, undefined, '30']) {
    assert.equal(formatClock(v), '0:00', String(v));
  }
});
