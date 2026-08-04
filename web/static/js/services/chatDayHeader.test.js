// Testes puros da decisão "qual dia está no topo do chat" (node --test).
//   node --test web/static/js/services/chatDayHeader.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { pickCurrentDay, PILL_TRAVEL, PUSH_START, PUSH_END } from './chatDayHeader.js';

const EDGE = 100;        // borda superior do container de rolagem
const SEP_H = 26;        // altura real do separador inline

/** Separador cujo TOPO está a `delta` px abaixo da borda. */
const at = (label, delta) => ({ label, top: EDGE + delta, bottom: EDGE + delta + SEP_H });
/** Um separador bem acima da borda (já saiu de cena há muito). */
const gone = (label) => at(label, -600);

test('sem separadores => nada a renderizar', () => {
  assert.deepEqual(pickCurrentDay([], EDGE), { label: null, offsetY: 0 });
  assert.deepEqual(pickCurrentDay(null, EDGE), { label: null, offsetY: 0 });
  assert.deepEqual(pickCurrentDay(undefined, EDGE), { label: null, offsetY: 0 });
});

test('um separador que já saiu pelo topo => é o dia corrente, pílula parada', () => {
  assert.deepEqual(pickCurrentDay([gone('ONTEM')], EDGE), { label: 'ONTEM', offsetY: 0 });
});

test('vários separadores => o último que saiu inteiro pelo topo ganha', () => {
  const seps = [gone('1 de janeiro'), at('ONTEM', -300), at('HOJE', 600)];
  assert.deepEqual(pickCurrentDay(seps, EDGE), { label: 'ONTEM', offsetY: 0 });
});

test('viewport no topo do histórico => rótulo do primeiro separador', () => {
  // Longe da borda (histórico longo abaixo da sentinela): pílula visível.
  const seps = [at('1 de janeiro', 300), at('HOJE', 900)];
  assert.deepEqual(pickCurrentDay(seps, EDGE), { label: '1 de janeiro', offsetY: 0 });
});

test('conversa curta de um dia só => sem pílula em cena (o inline responde)', () => {
  // py-2 do container (8px) + my-[12px] do separador ≈ 20px da borda.
  const out = pickCurrentDay([at('HOJE', 20)], EDGE);
  assert.equal(out.label, 'HOJE');
  assert.equal(out.offsetY, -PILL_TRAVEL);   // fora de cena => opacidade 0
});

test('empurrão: parada, deslizando, e fora de cena', () => {
  const base = [gone('ONTEM')];
  const pick = (delta) => pickCurrentDay([...base, at('HOJE', delta)], EDGE);

  assert.deepEqual(pick(PUSH_START + 1), { label: 'ONTEM', offsetY: 0 });
  assert.deepEqual(pick(PUSH_START), { label: 'ONTEM', offsetY: 0 });

  const mid = pick((PUSH_START + PUSH_END) / 2);
  assert.equal(mid.label, 'ONTEM');
  assert.equal(mid.offsetY, -PILL_TRAVEL / 2);          // metade do caminho

  assert.deepEqual(pick(PUSH_END), { label: 'ONTEM', offsetY: -PILL_TRAVEL });
  assert.deepEqual(pick(0), { label: 'ONTEM', offsetY: -PILL_TRAVEL });
});

test('o rótulo só troca quando o separador inline sai INTEIRO pelo topo', () => {
  const base = [gone('ONTEM')];
  // Ainda visível (bottom abaixo da borda): pílula fora de cena, rótulo antigo —
  // em nenhum quadro se lê o mesmo dia duas vezes.
  const still = pickCurrentDay([...base, at('HOJE', -SEP_H + 1)], EDGE);
  assert.deepEqual(still, { label: 'ONTEM', offsetY: -PILL_TRAVEL });
  // Saiu inteiro: a pílula reaparece já com o dia novo.
  const after = pickCurrentDay([...base, at('HOJE', -SEP_H)], EDGE);
  assert.deepEqual(after, { label: 'HOJE', offsetY: 0 });
});

test('dois dias colados (um dia com poucas mensagens) => sem tremer entre eles', () => {
  // 'HOJE' acabou de sair pelo topo, mas 'AMANHÃ' já está na zona de empurrão:
  // a pílula continua fora de cena em vez de piscar aparecendo.
  const seps = [gone('ONTEM'), at('HOJE', -SEP_H), at('AMANHÃ', PUSH_END - 1)];
  assert.deepEqual(pickCurrentDay(seps, EDGE), { label: 'HOJE', offsetY: -PILL_TRAVEL });
});

test('lista fora de ordem é ordenada pela posição medida', () => {
  const seps = [at('HOJE', 600), gone('1 de janeiro'), at('ONTEM', -300)];
  assert.deepEqual(pickCurrentDay(seps, EDGE), { label: 'ONTEM', offsetY: 0 });
});

test('entradas malformadas são ignoradas, não lançam', () => {
  const seps = [null, { label: '', top: -10 }, { label: 'ONTEM', top: 'x' }, { top: -5 }, gone('HOJE')];
  assert.deepEqual(pickCurrentDay(seps, EDGE), { label: 'HOJE', offsetY: 0 });
  assert.deepEqual(pickCurrentDay([{ label: 'a', top: NaN }], EDGE), { label: null, offsetY: 0 });
});

test('separador sem bottom medido cai no top', () => {
  assert.deepEqual(pickCurrentDay([{ label: 'ONTEM', top: EDGE - 1 }], EDGE), { label: 'ONTEM', offsetY: 0 });
});

test('topEdge inválido degrada para o primeiro rótulo, sem empurrão', () => {
  assert.deepEqual(pickCurrentDay([gone('ONTEM'), at('HOJE', 10)], undefined), { label: 'ONTEM', offsetY: 0 });
});

test('geometria pode ser sobrescrita e nunca produz offset positivo', () => {
  const seps = [gone('ONTEM'), at('HOJE', 30)];
  const out = pickCurrentDay(seps, EDGE, { travel: 10, pushStart: 40, pushEnd: 20 });
  assert.equal(out.offsetY, -5);
  const weird = pickCurrentDay(seps, EDGE, { travel: -5, pushStart: 0, pushEnd: 100 });
  assert.ok(weird.offsetY <= 0);
});
