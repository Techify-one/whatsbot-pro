// Trava as regras de exibição da lista de templates (plano 92 · E1/E2/E3).
//   node --test assets/plugin_examples/whatsapp_cloud/static/templateFilter.test.js

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalize, matchesQuery, applyView, contarPorAba } from './templateFilter.js';

const tpl = (name, opts = {}) => ({
  name,
  category: opts.category || 'MARKETING',
  status: 'APPROVED',
  language: { code: 'pt_BR' },
  components: opts.components || [],
});
const corpo = (texto) => ({ type: 'body', text: texto });

const BOLETO = tpl('cobranca_1', { components: [corpo('Olá {{1}}, seu boleto do cartão venceu ontem.')] });
const BOAS = tpl('boas_vindas', { components: [corpo('Seja bem-vindo à Redes!')] });
const PROMO = tpl('promo_natal', {
  category: 'UTILITY',
  components: [
    { type: 'header', text: 'Promoção de fim de ano' },
    corpo('Aproveite nossas ofertas'),
    { type: 'buttons', buttons: [{ type: 'URL', text: 'Ver catálogo', url: 'https://ex.com/catalogo' }] },
  ],
});
const TODOS = [BOLETO, BOAS, PROMO];

// ── normalize ──────────────────────────────────────────────────────────────
test('normalize tira acento e caixa', () => {
  assert.equal(normalize('Cartão ÁGUA'), 'cartao agua');
  assert.equal(normalize(null), '');
});

// ── busca por conteúdo (E3) ────────────────────────────────────────────────
test('acha pelo CORPO, não só pelo nome', () => {
  const r = matchesQuery(BOLETO, 'boleto');
  assert.equal(r.match, true);
  assert.equal(r.campo, 'corpo');
  assert.ok(r.trecho.includes('boleto'));
});

test('busca sem acento acha texto com acento', () => {
  assert.equal(matchesQuery(BOLETO, 'cartao').match, true);
  assert.equal(matchesQuery(BOLETO, 'CARTÃO').match, true);
});

test('acha pelo cabeçalho e pelo botão', () => {
  assert.equal(matchesQuery(PROMO, 'fim de ano').campo, 'cabeçalho');
  assert.equal(matchesQuery(PROMO, 'catálogo').campo, 'botão');
  assert.equal(matchesQuery(PROMO, 'ex.com').campo, 'botão');
});

test('casamento por nome/categoria não gera trecho (já está visível)', () => {
  const r = matchesQuery(BOLETO, 'cobranca');
  assert.equal(r.campo, 'nome');
  assert.equal(r.trecho, null);
  assert.equal(matchesQuery(BOLETO, 'marketing').campo, 'categoria');
});

test('busca vazia casa tudo', () => {
  assert.equal(matchesQuery(BOLETO, '').match, true);
  assert.equal(matchesQuery(BOLETO, '   ').match, true);
});

test('não casa o que não existe', () => {
  assert.equal(matchesQuery(BOAS, 'boleto').match, false);
});

// ── arquivar (E2) ──────────────────────────────────────────────────────────
test('arquivado some da aba "todas"', () => {
  const r = applyView(TODOS, { archived: ['cobranca_1'] });
  assert.deepEqual(r.map(t => t.name), ['boas_vindas', 'promo_natal']);
});

test('arquivado NÃO volta pela busca — a regra que define "arquivar"', () => {
  const r = applyView(TODOS, { query: 'boleto', archived: ['cobranca_1'] });
  assert.deepEqual(r.map(t => t.name), []);
});

test('a aba "arquivadas" mostra só os arquivados, e é como se desarquiva', () => {
  const r = applyView(TODOS, { tab: 'arquivadas', archived: ['cobranca_1'] });
  assert.deepEqual(r.map(t => t.name), ['cobranca_1']);
  assert.equal(r[0]._arquivado, true);
});

test('arquivado também não aparece em "favoritas", mesmo sendo favorito', () => {
  const r = applyView(TODOS, { tab: 'favoritas', favorites: ['cobranca_1'], archived: ['cobranca_1'] });
  assert.deepEqual(r.map(t => t.name), []);
});

// ── favoritos (E1) ─────────────────────────────────────────────────────────
test('favorito sobe para o topo, resto preserva a ordem da Meta', () => {
  const r = applyView(TODOS, { favorites: ['promo_natal'] });
  assert.deepEqual(r.map(t => t.name), ['promo_natal', 'cobranca_1', 'boas_vindas']);
  assert.equal(r[0]._favorito, true);
});

test('dois favoritos preservam entre si a ordem da Meta (estável)', () => {
  const r = applyView(TODOS, { favorites: ['promo_natal', 'cobranca_1'] });
  assert.deepEqual(r.map(t => t.name), ['cobranca_1', 'promo_natal', 'boas_vindas']);
});

test('aba "favoritas" mostra só os favoritos', () => {
  const r = applyView(TODOS, { tab: 'favoritas', favorites: ['boas_vindas'] });
  assert.deepEqual(r.map(t => t.name), ['boas_vindas']);
});

test('na aba "arquivadas" a estrela NÃO reordena', () => {
  const r = applyView(TODOS, {
    tab: 'arquivadas',
    archived: ['cobranca_1', 'promo_natal'],
    favorites: ['promo_natal'],
  });
  assert.deepEqual(r.map(t => t.name), ['cobranca_1', 'promo_natal']);
});

// ── combinações + contadores ───────────────────────────────────────────────
test('busca e favorito compõem: filtra e depois ordena', () => {
  const r = applyView(TODOS, { query: 'e', favorites: ['promo_natal'] });
  assert.equal(r[0].name, 'promo_natal');
});

test('contadores por aba respeitam a busca corrente', () => {
  const c = contarPorAba(TODOS, { query: 'boleto', favorites: ['cobranca_1'], archived: [] });
  assert.deepEqual(c, { todas: 1, favoritas: 1, arquivadas: 0 });
});

test('aceita Set ou Array, e lista vazia/nula não estoura', () => {
  assert.equal(applyView(TODOS, { favorites: new Set(['boas_vindas']) })[0].name, 'boas_vindas');
  assert.deepEqual(applyView(null, {}), []);
  assert.deepEqual(applyView([], {}), []);
});

test('aba desconhecida cai em "todas" (degradação segura)', () => {
  const r = applyView(TODOS, { tab: 'inexistente' });
  assert.equal(r.length, 3);
});
