import test from 'node:test';
import assert from 'node:assert/strict';
import { scrollbarGutter } from './composerMirror.js';

test('sem barra de rolagem o espelho não é encolhido', () => {
  // offset = 400 (borda 1px de cada lado), client = 398 → só as bordas.
  assert.equal(scrollbarGutter(400, 398, 2), 0);
});

test('com barra de rolagem devolve a largura dela', () => {
  // wa-scrollbar tem 6px e ocupa espaço de layout no webkit.
  assert.equal(scrollbarGutter(400, 392, 2), 6);
});

test('nunca devolve negativo (medida ainda não estabilizada)', () => {
  assert.equal(scrollbarGutter(0, 0, 2), 0);
});
