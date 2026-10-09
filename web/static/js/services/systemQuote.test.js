// Run with: node --test web/static/js/services/systemQuote.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseQuote, QUOTE_CLAMP_LINES } from './systemQuote.js';

test('parseQuote: separa título, citação e o texto depois dela', () => {
  const segs = parseQuote('🎓 Dúvida · Maria\nCurso › Aula\n[[quote]]\nNão entendi o NAT.\n[[/quote]]\nfim');
  assert.deepEqual(segs, [
    { type: 'text', text: '🎓 Dúvida · Maria\nCurso › Aula' },
    { type: 'quote', text: 'Não entendi o NAT.' },
    { type: 'text', text: 'fim' },
  ]);
});

test('parseQuote: sem token → um único segmento de texto, intacto', () => {
  assert.deepEqual(parseQuote('Só uma mensagem de sistema.'), [
    { type: 'text', text: 'Só uma mensagem de sistema.' }]);
});

test('parseQuote: preserva as quebras de linha DENTRO da citação', () => {
  const [q] = parseQuote('[[quote]]linha 1\n\nlinha 3[[/quote]]');
  assert.equal(q.text, 'linha 1\n\nlinha 3');
});

test('parseQuote: citação vazia some; token sem fechamento vira texto', () => {
  assert.deepEqual(parseQuote('a [[quote]]  [[/quote]] b'), [
    { type: 'text', text: 'a' }, { type: 'text', text: 'b' }]);
  assert.deepEqual(parseQuote('a [[quote]] sem fim'), [{ type: 'text', text: 'a [[quote]] sem fim' }]);
});

test('parseQuote: conteúdo vazio/undefined é tolerado', () => {
  assert.deepEqual(parseQuote(''), []);
  assert.deepEqual(parseQuote(undefined), []);
  assert.deepEqual(parseQuote(null), []);
});

test('QUOTE_CLAMP_LINES: é um número de linhas positivo', () => {
  assert.ok(Number.isInteger(QUOTE_CLAMP_LINES) && QUOTE_CLAMP_LINES > 0);
});
