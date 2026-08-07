// Run with: node --test web/static/js/services/threadJump.test.js
//
// Plano 99 · F0e — caracterização do "salto que falha em silêncio", agora como
// uma regra nomeada. Cada teste abaixo corresponde a um sintoma observado em
// produção antes do plano.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { planJump, isRendered } from './threadJump.js';

const msg = (id) => ({ _id: id, role: 'user', content: 'oi', ts: 1000 });

test('alvo na janela: foca (o caminho feliz de sempre)', () => {
  assert.deepEqual(
    planJump({ target: 7, rendered: true, requested: false }),
    { action: 'focus' });
});

test('alvo FORA da janela: pede a janela ancorada — não espera cascata', () => {
  // Era aqui que o bug morava: `focusMessage` devolvia false e ninguém pedia
  // nada. Se o alvo estivesse na última página possível, o salto nunca acontecia.
  assert.deepEqual(
    planJump({ target: 7, rendered: false, requested: false }),
    { action: 'fetch' });
});

test('janela ancorada em voo: espera — não pede duas vezes nem desiste', () => {
  assert.deepEqual(
    planJump({ target: 7, rendered: false, requested: true, inFlight: true }),
    { action: 'none' });
});

test('já pedimos e o alvo continua ausente: desiste AVISANDO, não em laço', () => {
  // Mensagem apagada, id de outra conversa, permalink velho. O que não se pode
  // fazer é pedir de novo eternamente (nem ficar mudo, que era o comportamento).
  assert.deepEqual(
    planJump({ target: 7, rendered: false, requested: true }),
    { action: 'give_up' });
});

test('sem alvo pendente não há nada a decidir', () => {
  assert.deepEqual(planJump({ target: null, rendered: false, requested: false }),
                   { action: 'none' });
});

test('isRendered compara como string (o alvo vem de três origens diferentes)', () => {
  const msgs = [msg(41), msg(42)];
  assert.equal(isRendered(msgs, 42), true);
  assert.equal(isRendered(msgs, '42'), true, 'permalink entrega string');
  assert.equal(isRendered(msgs, 99), false);
  assert.equal(isRendered(msgs, null), false);
  assert.equal(isRendered(null, 42), false);
});

test('isRendered ignora bolha otimista (sem _id) em vez de casar por engano', () => {
  assert.equal(isRendered([{ role: 'user', content: 'x' }], 'undefined'), false);
});
