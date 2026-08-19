// Run with: node --test web/static/js/services/tabCounts.test.js
//
// Plano 130 · F0 — trava a decisão do contador das abas do hub. O bug que originou
// este módulo: o badge alternava entre o total do servidor (252) e o número de
// linhas carregadas na página (50) a cada evento de WebSocket, porque as duas
// grandezas dividiam a mesma variável e o total era zerado antes de cada refetch.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  EMPTY_COUNTS, DEFAULT_DEBOUNCE_MS, DEFAULT_MIN_INTERVAL_MS,
  countSpecKey, resolveTabCounts, planCountFetch,
} from './tabCounts.js';

const D = DEFAULT_DEBOUNCE_MS;
const MIN = DEFAULT_MIN_INTERVAL_MS;

// ── countSpecKey ─────────────────────────────────────────────────────────────

test('countSpecKey: mesmo filtro ⇒ mesma chave, mesmo com arrays recriados', () => {
  const a = countSpecKey({ statusFilter: 'open', tagFilter: ['vip'], advFilters: [] });
  const b = countSpecKey({ statusFilter: 'open', tagFilter: ['vip'], advFilters: [] });
  assert.equal(a, b);
});

test('countSpecKey: ordem das etiquetas não muda a chave', () => {
  const a = countSpecKey({ statusFilter: 'open', tagFilter: ['vip', 'novo'] });
  const b = countSpecKey({ statusFilter: 'open', tagFilter: ['novo', 'vip'] });
  assert.equal(a, b);
});

test('countSpecKey: trocar o chip de status muda a chave', () => {
  const open = countSpecKey({ statusFilter: 'open' });
  const all = countSpecKey({ statusFilter: 'all' });
  assert.notEqual(open, all);
});

test('countSpecKey: arquivadas é uma dimensão da chave', () => {
  assert.notEqual(
    countSpecKey({ statusFilter: 'open', archived: false }),
    countSpecKey({ statusFilter: 'open', archived: true }),
  );
});

test('countSpecKey: cláusula avançada entra na chave', () => {
  const base = countSpecKey({ statusFilter: 'open' });
  const withClause = countSpecKey({
    statusFilter: 'open', advFilters: [{ dim: 'channel', op: 'eq', value: '7' }],
  });
  assert.notEqual(base, withClause);
});

test('countSpecKey: a ABA de atribuição NÃO entra na chave', () => {
  // buildCountParams devolve as 4 contagens do mesmo spec base — trocar de aba não
  // pode invalidar o total nem disparar refetch.
  assert.equal(
    countSpecKey({ statusFilter: 'open', assignmentTab: 'mine' }),
    countSpecKey({ statusFilter: 'open', assignmentTab: 'unassigned' }),
  );
});

test('countSpecKey: spec vazio não explode', () => {
  assert.equal(typeof countSpecKey(), 'string');
  assert.equal(typeof countSpecKey({}), 'string');
});

// ── resolveTabCounts ─────────────────────────────────────────────────────────

const SERVER = { all: 252, mine: 1, unassigned: 2, mentions: 0 };
const CLIENT = { all: 50, mine: 0, unassigned: 0, mentions: 0 };

test('resolveTabCounts: em serverMode o total do servidor vence o da página', () => {
  const out = resolveTabCounts({ serverCounts: SERVER, clientCounts: CLIENT, serverMode: true });
  assert.equal(out.all, 252);
  assert.equal(out, SERVER, 'devolve a MESMA referência (não recria objeto por render)');
});

test('resolveTabCounts: 1º paint (sem total ainda) cai no cliente — D2', () => {
  const out = resolveTabCounts({ serverCounts: null, clientCounts: CLIENT, serverMode: true });
  assert.equal(out, CLIENT);
});

test('resolveTabCounts: fora de serverMode o cliente é autoritativo', () => {
  // Mesmo com um total em memória (ex.: serverMode acabou de desligar), o número
  // exibido é o da lista client-filtrada — senão o badge não bateria com a lista.
  const out = resolveTabCounts({ serverCounts: SERVER, clientCounts: CLIENT, serverMode: false });
  assert.equal(out, CLIENT);
});

test('resolveTabCounts: sem nada devolve zeros', () => {
  assert.deepEqual(resolveTabCounts({}), EMPTY_COUNTS);
  assert.deepEqual(resolveTabCounts(), EMPTY_COUNTS);
});

test('resolveTabCounts: contagem parcial completa com 0 em vez de NaN', () => {
  const out = resolveTabCounts({ serverCounts: { all: 7 }, serverMode: true });
  assert.deepEqual(out, { all: 7, mine: 0, unassigned: 0, mentions: 0 });
});

test('resolveTabCounts: valor não numérico vira 0 (nunca NaN na tela)', () => {
  const out = resolveTabCounts({
    serverCounts: { all: '12', mine: null, unassigned: undefined, mentions: NaN },
    serverMode: true,
  });
  assert.deepEqual(out, { all: 12, mine: 0, unassigned: 0, mentions: 0 });
});

// ── planCountFetch ───────────────────────────────────────────────────────────

test('planCountFetch: fora de serverMode ⇒ idle', () => {
  assert.deepEqual(planCountFetch({ specKey: null, now: 1000 }), { action: 'idle', delayMs: 0 });
});

test('planCountFetch: spec mudou ⇒ reset_and_fetch com o debounce', () => {
  const p = planCountFetch({ specKey: 'status=open', lastSpecKey: 'status=all', now: 1000 });
  assert.deepEqual(p, { action: 'reset_and_fetch', delayMs: D });
});

test('planCountFetch: 1º run (nunca houve spec) ⇒ reset_and_fetch', () => {
  const p = planCountFetch({ specKey: 'status=open', lastSpecKey: null, now: 0 });
  assert.equal(p.action, 'reset_and_fetch');
});

test('planCountFetch: mudança de filtro FURA o teto de frequência', () => {
  // O usuário acabou de mexer no filtro: ele está olhando o número. O teto só vale
  // para o gatilho "a lista mudou".
  const p = planCountFetch({
    specKey: 'status=all', lastSpecKey: 'status=open',
    lastFetchAt: 1000, now: 1001,
  });
  assert.deepEqual(p, { action: 'reset_and_fetch', delayMs: D });
});

test('planCountFetch: lista mudou e o teto já passou ⇒ fetch imediato', () => {
  const p = planCountFetch({
    specKey: 'k', lastSpecKey: 'k', lastFetchAt: 0, pendingSince: 0, now: MIN + 1,
  });
  assert.deepEqual(p, { action: 'fetch', delayMs: 0 });
});

test('planCountFetch: lista mudou dentro do teto ⇒ wait pelo que falta', () => {
  const p = planCountFetch({
    specKey: 'k', lastSpecKey: 'k', lastFetchAt: 10_000, pendingSince: 10_500, now: 11_000,
  });
  assert.equal(p.action, 'wait');
  assert.equal(p.delayMs, 10_000 + MIN - 11_000);
});

test('planCountFetch: o prazo é ancorado no INÍCIO da rajada, não em "agora"', () => {
  // O bug: com o prazo ancorado no evento atual, uma rajada contínua reiniciava o
  // timer para sempre e a contagem nunca era buscada. Aqui, 5 eventos seguidos
  // fazem o prazo ANDAR PARA PERTO, nunca para longe.
  const base = { specKey: 'k', lastSpecKey: 'k', lastFetchAt: null, pendingSince: 1000 };
  let last = Infinity;
  for (const now of [1000, 1050, 1100, 1200, 1290]) {
    const p = planCountFetch({ ...base, now });
    assert.equal(p.action, 'wait');
    assert.ok(p.delayMs < last, `prazo tem de encurtar (now=${now})`);
    last = p.delayMs;
  }
  // e vence exatamente um debounce depois do primeiro gatilho
  assert.deepEqual(planCountFetch({ ...base, now: 1000 + D }), { action: 'fetch', delayMs: 0 });
});

test('planCountFetch: sem rajada pendente, o debounce conta a partir de agora', () => {
  const p = planCountFetch({
    specKey: 'k', lastSpecKey: 'k', lastFetchAt: null, pendingSince: null, now: 5000,
  });
  assert.deepEqual(p, { action: 'wait', delayMs: D });
});

test('planCountFetch: nunca buscado antes ⇒ só o debounce segura', () => {
  const p = planCountFetch({
    specKey: 'k', lastSpecKey: 'k', lastFetchAt: null, pendingSince: 0, now: D,
  });
  assert.deepEqual(p, { action: 'fetch', delayMs: 0 });
});

test('planCountFetch: o teto vence o debounce quando é maior', () => {
  const p = planCountFetch({
    specKey: 'k', lastSpecKey: 'k', lastFetchAt: 0, pendingSince: 0, now: 0,
  });
  assert.equal(p.action, 'wait');
  assert.equal(p.delayMs, MIN, 'não pode cair no debounce e furar o teto');
});

test('planCountFetch: minIntervalMs=0 desliga o teto', () => {
  const p = planCountFetch({
    specKey: 'k', lastSpecKey: 'k', lastFetchAt: 999, pendingSince: 0, now: 1000,
    debounceMs: 0, minIntervalMs: 0,
  });
  assert.deepEqual(p, { action: 'fetch', delayMs: 0 });
});
