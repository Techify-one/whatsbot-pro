// Run with: node --test web/static/js/services/systemCta.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseCta } from './systemCta.js';

test('parseCta: token válido vira ação e sai do texto', () => {
  const { text, action } = parseCta(
    '🔧 Sugestão enviada.\n[[cta:Ir para sugestão|https://x.techify.run/melhorias?detail=8]]');
  assert.equal(text, '🔧 Sugestão enviada.');
  assert.deepEqual(action, {
    label: 'Ir para sugestão',
    url: 'https://x.techify.run/melhorias?detail=8',
  });
});

test('parseCta: sem token → texto intacto, sem ação', () => {
  const { text, action } = parseCta('Só uma mensagem de sistema.');
  assert.equal(text, 'Só uma mensagem de sistema.');
  assert.equal(action, null);
});

test('parseCta: caminho interno é aceito', () => {
  const { action } = parseCta('abc [[cta:Abrir|/melhorias?detail=3]]');
  assert.deepEqual(action, { label: 'Abrir', url: '/melhorias?detail=3' });
});

test('parseCta: destino inseguro é rejeitado (guarda XSS)', () => {
  const { text, action } = parseCta('x [[cta:Clique|javascript:alert(1)]]');
  assert.equal(action, null);      // não vira botão
  assert.equal(text, 'x');         // mas o token ainda é removido do texto
});

test('parseCta: rótulo vazio não vira ação', () => {
  const { action } = parseCta('[[cta: |https://x.com]]');
  assert.equal(action, null);
});

test('parseCta: conteúdo vazio/undefined é tolerado', () => {
  assert.deepEqual(parseCta(''), { text: '', action: null });
  assert.deepEqual(parseCta(undefined), { text: '', action: null });
});
