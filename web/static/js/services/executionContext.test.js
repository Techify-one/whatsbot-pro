import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PREVIEW_LINES, PREVIEW_MAX_CHARS, charsOf, approxTokens, contextState,
  previewOf, formatInt, sizeLabel, legacyNotice,
} from './executionContext.js';

const MARKER = '… (truncado)';

test('charsOf usa `chars` quando existe', () => {
  assert.equal(charsOf({ content: 'abc', chars: 31563 }), 31563);
  assert.equal(charsOf({ content: '', chars: 0 }), 0);
});

test('charsOf cai no tamanho guardado em linha legada (sem `chars`)', () => {
  assert.equal(charsOf({ content: 'abcde' }), 5);
  assert.equal(charsOf({ content: 'a'.repeat(2000) + MARKER, truncated: true }), 2000);
  assert.equal(charsOf(null), 0);
  assert.equal(charsOf({}), 0);
});

test('charsOf ignora `chars` inválido', () => {
  assert.equal(charsOf({ content: 'abcd', chars: -1 }), 4);
  assert.equal(charsOf({ content: 'abcd', chars: 'x' }), 4);
});

test('approxTokens arredonda para cima e tolera lixo', () => {
  assert.equal(approxTokens(0), 0);
  assert.equal(approxTokens(1), 1);
  assert.equal(approxTokens(4), 1);
  assert.equal(approxTokens(5), 2);
  assert.equal(approxTokens(31563), 7891);
  assert.equal(approxTokens(undefined), 0);
  assert.equal(approxTokens(-3), 0);
});

test('contextState: curto, completo e legado', () => {
  assert.equal(contextState({ content: 'oi', truncated: false }), 'curto');
  assert.equal(contextState({ content: '' }), 'curto');
  assert.equal(contextState({ content: 'x'.repeat(PREVIEW_MAX_CHARS) }), 'curto');
  assert.equal(contextState({ content: 'x'.repeat(PREVIEW_MAX_CHARS + 1) }), 'completo');
  assert.equal(contextState({ content: 'x'.repeat(3000), truncated: false }), 'completo');
});

test('contextState: truncado é legado, mesmo curto, e nunca vira completo', () => {
  assert.equal(contextState({ content: 'x'.repeat(2000) + MARKER, truncated: true }), 'legado');
  assert.equal(contextState({ content: 'curto', truncated: true }), 'legado');
});

test('contextState tolera mensagem ausente', () => {
  assert.equal(contextState(null), 'curto');
  assert.equal(contextState(undefined), 'curto');
});

test('previewOf corta por linhas', () => {
  const text = Array.from({ length: 20 }, (_, i) => `linha ${i + 1}`).join('\n');
  const p = previewOf(text);
  assert.equal(p.text.split('\n').length, PREVIEW_LINES);
  assert.equal(p.text.split('\n')[0], 'linha 1');
  assert.equal(p.cut, true);
});

test('previewOf corta por caracteres numa linha única gigante', () => {
  const p = previewOf('a'.repeat(5000));
  assert.equal(p.text.length, PREVIEW_MAX_CHARS);
  assert.equal(p.cut, true);
});

test('previewOf não marca corte quando cabe inteiro', () => {
  const p = previewOf('um\ndois\ntres');
  assert.equal(p.text, 'um\ndois\ntres');
  assert.equal(p.cut, false);
  assert.deepEqual(previewOf(null), { text: '', cut: false });
});

test('formatInt usa ponto como separador de milhar', () => {
  assert.equal(formatInt(0), '0');
  assert.equal(formatInt(999), '999');
  assert.equal(formatInt(1000), '1.000');
  assert.equal(formatInt(31563), '31.563');
  assert.equal(formatInt(1234567), '1.234.567');
  assert.equal(formatInt('x'), '0');
});

test('sizeLabel junta caracteres e tokens aproximados', () => {
  assert.equal(sizeLabel({ content: 'x', chars: 31563 }), '31.563 caracteres · ≈7.891 tokens');
});

test('legacyNotice mostra "X de Y" quando o original é conhecido', () => {
  const m = { content: 'a'.repeat(2000) + MARKER, truncated: true, chars: 31563 };
  assert.equal(legacyNotice(m), 'cortado na gravação — mostrando 2.000 de 31.563');
});

test('legacyNotice sem `chars` (execução anterior) usa o aviso genérico', () => {
  const m = { content: 'a'.repeat(2000) + MARKER, truncated: true };
  assert.equal(legacyNotice(m), 'cortado na gravação, execução anterior à captura completa');
});
