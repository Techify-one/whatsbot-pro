import test from 'node:test';
import assert from 'node:assert';
import { windowState, formatRemaining, URGENT_MS } from './sessionWindow.js';

// Plano 159 · F2 — o contador da janela do cliente. Os casos abaixo são os que
// decidem se a faixa aparece, o que ela diz e quando ela vira alerta.

const HOUR = 3600 * 1000;
const MIN = 60 * 1000;
const DAY = 24 * HOUR;

/** `now` fixo; o inbound entra em SEGUNDOS, como vem do backend. */
const NOW = 1_757_000_000_000;
const segundosAtras = (ms) => (NOW - ms) / 1000;

// ─── applies: quando a faixa nem existe ────────────────────────────────────

test('canal SEM janela (GOWA/Telegram: 0h) não exibe nada', () => {
  const s = windowState({ lastInboundTs: segundosAtras(HOUR), windowHours: 0, now: NOW });
  assert.equal(s.applies, false);
});

test('core anterior (campo ausente) não exibe nada', () => {
  // O campo trafega CRU justamente para `undefined` chegar até aqui em vez de
  // virar 0 no meio do caminho e ser confundido com "canal sem janela".
  assert.equal(windowState({ lastInboundTs: segundosAtras(HOUR), now: NOW }).applies, false);
  assert.equal(windowState({ lastInboundTs: segundosAtras(HOUR),
                             windowHours: null, now: NOW }).applies, false);
});

test('conversa sem inbound nenhum: a janela nunca abriu', () => {
  assert.equal(windowState({ lastInboundTs: null, windowHours: 24, now: NOW }).applies, false);
  assert.equal(windowState({ lastInboundTs: 0, windowHours: 24, now: NOW }).applies, false);
  assert.equal(windowState({ windowHours: 24, now: NOW }).applies, false);
});

// ─── open / remaining ──────────────────────────────────────────────────────

test('janela de 24h no meio: conta o que falta', () => {
  const s = windowState({ lastInboundTs: segundosAtras(6 * HOUR), windowHours: 24, now: NOW });
  assert.equal(s.applies, true);
  assert.equal(s.open, true);
  assert.equal(s.remainingMs, 18 * HOUR);
  assert.equal(s.label, '18h');
  assert.equal(s.urgent, false);
});

test('janela de 168h (Meta com HUMAN_AGENT): 7 dias, não 24h', () => {
  // O tamanho vem do servidor; um "24" escrito no cliente erraria por 6 dias.
  const s = windowState({ lastInboundTs: segundosAtras(2 * DAY), windowHours: 24 * 7, now: NOW });
  assert.equal(s.open, true);
  assert.equal(s.remainingMs, 5 * DAY);
  assert.equal(s.label, '5 dias');
});

test('expirada: applies segue true, mas open é false', () => {
  // A distinção importa: a faixa some e quem ocupa o lugar é o aviso "Fora da
  // janela" que o compositor já tinha — os dois nunca aparecem juntos.
  const s = windowState({ lastInboundTs: segundosAtras(25 * HOUR), windowHours: 24, now: NOW });
  assert.equal(s.applies, true);
  assert.equal(s.open, false);
  assert.equal(s.label, '');
});

test('o instante exato do fim já conta como fechada', () => {
  const s = windowState({ lastInboundTs: segundosAtras(24 * HOUR), windowHours: 24, now: NOW });
  assert.equal(s.open, false);
});

test('inbound recém-chegado: janela inteira pela frente', () => {
  const s = windowState({ lastInboundTs: NOW / 1000, windowHours: 24, now: NOW });
  assert.equal(s.remainingMs, 24 * HOUR);
});

// ─── urgent ────────────────────────────────────────────────────────────────

test('faltando 59min: urgente', () => {
  const s = windowState({ lastInboundTs: segundosAtras(23 * HOUR + MIN), windowHours: 24, now: NOW });
  assert.equal(s.urgent, true);
  assert.equal(s.label, '59min');
});

test('exatamente 1h restante ainda é urgente; 1h1min não é', () => {
  const noLimite = windowState({ lastInboundTs: segundosAtras(23 * HOUR), windowHours: 24, now: NOW });
  assert.equal(noLimite.remainingMs, URGENT_MS);
  assert.equal(noLimite.urgent, true);
  const acima = windowState({ lastInboundTs: segundosAtras(23 * HOUR - MIN), windowHours: 24, now: NOW });
  assert.equal(acima.urgent, false);
});

// ─── formatRemaining ───────────────────────────────────────────────────────

test('formata em no máximo duas unidades', () => {
  assert.equal(formatRemaining(5 * HOUR + 12 * MIN), '5h 12min');
  assert.equal(formatRemaining(48 * MIN), '48min');
  assert.equal(formatRemaining(2 * DAY + 3 * HOUR), '2 dias 3h');
  assert.equal(formatRemaining(DAY), '1 dia');            // singular
  assert.equal(formatRemaining(3 * HOUR), '3h');          // sem "0min"
  assert.equal(formatRemaining(2 * DAY), '2 dias');       // sem "0h"
});

test('arredonda SEMPRE para baixo', () => {
  // Anunciar mais tempo do que resta é o único erro que custa uma janela.
  assert.equal(formatRemaining(5 * HOUR + 59 * MIN + 59 * 1000), '5h 59min');
  assert.equal(formatRemaining(2 * DAY - 1000), '1 dia 23h');
});

test('menos de um minuto vira "1min", não "0min"', () => {
  assert.equal(formatRemaining(30 * 1000), '1min');
  assert.equal(formatRemaining(1), '1min');
});

test('zero e negativo não têm rótulo', () => {
  assert.equal(formatRemaining(0), '');
  assert.equal(formatRemaining(-5), '');
});
