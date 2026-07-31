// Run with: node --test web/static/js/services/chatCalendar.test.js
//
// Plano 99 · F4 — a aritmética do calendário do "ir para data".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { monthGrid, dayStartTs, monthLabel, shiftMonth,
         initialCursor, atLastMonth } from './chatCalendar.js';

test('dayStartTs resolve no fuso do NAVEGADOR, nunca em UTC', () => {
  // O contrato com o servidor (plano 99 F3·2) é: o cliente manda epoch, o
  // servidor só compara. Se isto virasse UTC, um operador a oeste de Greenwich
  // clicaria em "1 de janeiro" e aterrissaria no 31 de dezembro.
  const ts = dayStartTs(2026, 0, 1);
  const d = new Date(ts * 1000);
  assert.equal(d.getFullYear(), 2026);
  assert.equal(d.getMonth(), 0);
  assert.equal(d.getDate(), 1);
  assert.equal(d.getHours(), 0);
});

test('monthGrid: 6 semanas de 7 dias, sempre', () => {
  const g = monthGrid(2026, 6);   // julho/2026
  assert.equal(g.length, 6);
  for (const w of g) assert.equal(w.length, 7);
});

test('monthGrid alinha o 1º dia no dia da semana certo', () => {
  // 1/fev/2026 é um domingo → primeira célula da 1ª semana.
  const g = monthGrid(2026, 1);
  assert.equal(g[0][0].day, 1);
  // 1/jul/2026 é uma quarta → índice 3.
  const j = monthGrid(2026, 6);
  assert.equal(j[0][3].day, 1);
  assert.equal(j[0][2].day, null, 'célula fora do mês deveria estar apagada');
});

test('monthGrid cobre o mês inteiro e só ele', () => {
  const dias = monthGrid(2024, 1).flat().filter(c => c.day != null).map(c => c.day);
  assert.deepEqual(dias, Array.from({ length: 29 }, (_, i) => i + 1),
    'fevereiro de 2024 é bissexto (29 dias)');
  const abril = monthGrid(2026, 3).flat().filter(c => c.day != null);
  assert.equal(abril.length, 30);
});

test('monthGrid desabilita o dia posterior a maxTs (não existe conversa no futuro)', () => {
  const max = dayStartTs(2026, 6, 15);
  const cells = monthGrid(2026, 6, { maxTs: max }).flat().filter(c => c.day != null);
  assert.equal(cells.find(c => c.day === 15).disabled, false);
  assert.equal(cells.find(c => c.day === 16).disabled, true);
  assert.equal(cells.find(c => c.day === 1).disabled, false);
});

test('shiftMonth vira o ano nos dois sentidos', () => {
  assert.deepEqual(shiftMonth({ year: 2026, month: 11 }, 1), { year: 2027, month: 0 });
  assert.deepEqual(shiftMonth({ year: 2026, month: 0 }, -1), { year: 2025, month: 11 });
  assert.deepEqual(shiftMonth({ year: 2026, month: 5 }, -13), { year: 2025, month: 4 });
});

test('monthLabel em português', () => {
  assert.equal(monthLabel(2026, 2), 'março de 2026');
});

test('initialCursor abre no mês da conversa, não em hoje', () => {
  // Numa conversa antiga, abrir em "hoje" obrigaria a navegar meses para trás.
  const antigo = dayStartTs(2025, 2, 9);
  assert.deepEqual(initialCursor(antigo, dayStartTs(2026, 6, 31)),
                   { year: 2025, month: 2 });
  // Sem referência, cai em hoje.
  assert.deepEqual(initialCursor(null, dayStartTs(2026, 6, 31)),
                   { year: 2026, month: 6 });
});

test('atLastMonth trava o botão "próximo mês" no mês corrente', () => {
  const agora = dayStartTs(2026, 6, 31);
  assert.equal(atLastMonth({ year: 2026, month: 6 }, agora), true);
  assert.equal(atLastMonth({ year: 2026, month: 5 }, agora), false);
});
