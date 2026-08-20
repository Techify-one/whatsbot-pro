// Run with: node --test web/static/js/services/composerTokens.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  detectMentionToken, detectQuickReplyToken, replaceToken,
  mentionLabel, mentionCandidates, quickReplyCandidates, stripGroupPrefix,
} from './composerTokens.js';

// ── detectMentionToken ──────────────────────────────────────────────
test('detectMentionToken: @ at start of input', () => {
  assert.deepEqual(detectMentionToken('@jo', 3), { query: 'jo', start: 0 });
});

test('detectMentionToken: @ after whitespace', () => {
  assert.deepEqual(detectMentionToken('oi @ma', 6), { query: 'ma', start: 3 });
});

test('detectMentionToken: bare @ (empty query) opens the menu', () => {
  assert.deepEqual(detectMentionToken('hello @', 7), { query: '', start: 6 });
});

test('detectMentionToken: unicode/accented names match', () => {
  assert.deepEqual(detectMentionToken('@josé', 5), { query: 'josé', start: 0 });
});

test('detectMentionToken: @ mid-word (no preceding space) → null', () => {
  assert.equal(detectMentionToken('email@x', 7), null);
});

test('detectMentionToken: space after token closes it → null', () => {
  assert.equal(detectMentionToken('@joao ', 6), null);
});

// ── detectQuickReplyToken ───────────────────────────────────────────
test('detectQuickReplyToken: / at start', () => {
  assert.deepEqual(detectQuickReplyToken('/ola', 4), { query: 'ola', start: 0 });
});

test('detectQuickReplyToken: bare / opens', () => {
  assert.deepEqual(detectQuickReplyToken('/', 1), { query: '', start: 0 });
});

test('detectQuickReplyToken: hyphen allowed in short_code', () => {
  assert.deepEqual(detectQuickReplyToken('/bom-dia', 8), { query: 'bom-dia', start: 0 });
});

test('detectQuickReplyToken: slash inside URL (no leading space) → null', () => {
  assert.equal(detectQuickReplyToken('http://x', 8), null);
});

// ── replaceToken ────────────────────────────────────────────────────
test('replaceToken: swaps token region and reports caret', () => {
  // value "oi @ma", token at start=3..caret=6, insert "@Maria "
  const r = replaceToken('oi @ma', 3, 6, '@Maria ');
  assert.equal(r.value, 'oi @Maria ');
  assert.equal(r.caret, '@Maria '.length + 3);
});

test('replaceToken: preserves text after the caret', () => {
  const r = replaceToken('a @b end', 2, 4, '@Bob ');
  assert.equal(r.value, 'a @Bob  end');
  assert.equal(r.caret, 'a @Bob '.length);
});

// ── replaceToken: âncora obsoleta (plano 132 · F5) ───────────────────
//
// `start` é congelado quando o menu ABRE; `caret` é lido VIVO do DOM na hora de
// aplicar. Entre um e outro o operador pode ter clicado noutro ponto do texto, e
// aí os dois índices deixam de descrever um token. Sem guarda, o splice
// `slice(0,start) + insert + slice(caret)` duplica ou apaga um trecho inteiro —
// silenciosamente, porque nada valida que `start <= caret`.
//
// A guarda devolve o valor INTACTO: perder a menção é irritante, comer 300
// caracteres do texto do operador é o bug relatado.

test('replaceToken: caret ANTES do start (operador clicou atrás) não duplica', () => {
  // Menu abriu com o "@" no índice 20; o operador clicou no índice 5 e escolheu.
  // Sem guarda: slice(0,20) + insert + slice(5) repetia os caracteres 5..20.
  const v = 'zero um dois tres @qu';
  const r = replaceToken(v, 18, 5, '@Quatro ');
  assert.equal(r.value, v, 'valor tem de sair intacto');
  assert.equal(r.caret, 5, 'caret fica onde o operador o deixou');
});

test('replaceToken: caret ALÉM do token não engole o texto do meio', () => {
  // Menu abriu no "@" (índice 3); o operador clicou lá no fim e escolheu.
  // Sem guarda: tudo entre o "@" e o clique desaparecia.
  const v = 'oi @ma, tudo bem? preciso confirmar o pedido';
  const r = replaceToken(v, 3, v.length, '@Maria ');
  assert.equal(r.value, v);
  assert.equal(r.caret, v.length);
});

test('replaceToken: índices fora dos limites devolvem o valor intacto', () => {
  const v = 'oi @ma';
  assert.equal(replaceToken(v, -1, 6, '@Maria ').value, v);
  assert.equal(replaceToken(v, 3, 99, '@Maria ').value, v);
  assert.equal(replaceToken(v, 3, 6, '@Maria ').value, 'oi @Maria ');  // controle
});

test('replaceToken: região substituída tem de começar pelo gatilho', () => {
  // O texto mudou embaixo da âncora (rascunho re-hidratado, colagem, undo) e o
  // que está em [start, caret) não é mais um token — não é seguro trocar.
  const r = replaceToken('oi Xma', 3, 6, '@Maria ');
  assert.equal(r.value, 'oi Xma');
});

test('replaceToken: aceita "/" além de "@" como gatilho (resposta rápida)', () => {
  const r = replaceToken('oi /ola', 3, 7, 'Olá, tudo bem?');
  assert.equal(r.value, 'oi Olá, tudo bem?');
});

test('replaceToken: seleção vazia no ponto do gatilho ainda insere', () => {
  // Token de query vazia: "@" acabou de ser digitado, start aponta para ele.
  const r = replaceToken('oi @', 3, 4, '@Ana ');
  assert.equal(r.value, 'oi @Ana ');
});

// ── mentionLabel ────────────────────────────────────────────────────
test('mentionLabel: name preferred, falls back to phone then lid', () => {
  assert.equal(mentionLabel({ name: 'Ana', phone: '55', lid: 'x' }), 'Ana');
  assert.equal(mentionLabel({ phone: '5511', lid: 'x' }), '5511');
  assert.equal(mentionLabel({ lid: 'abc' }), 'abc');
  assert.equal(mentionLabel({}), '');
  assert.equal(mentionLabel(null), '');
});

// ── mentionCandidates ───────────────────────────────────────────────
const MEMBERS = [
  { name: 'Ana', phone: '1', is_admin: true },
  { name: 'Bruno', phone: '2' },
  { name: 'Carla', phone: '3' },
];

test('mentionCandidates: empty query → todos + all members', () => {
  const c = mentionCandidates('', MEMBERS);
  assert.equal(c[0].special, true);
  assert.equal(c.length, 4);
});

test('mentionCandidates: query filters by label (case-insensitive, substring)', () => {
  const c = mentionCandidates('ar', MEMBERS); // matches "Carla"
  assert.equal(c.some(m => m.special), false);
  assert.deepEqual(c.map(m => m.name), ['Carla']);
});

test('mentionCandidates: "todos" prefix keeps special entry', () => {
  const c = mentionCandidates('to', MEMBERS);
  assert.equal(c[0].special, true);
});

test('mentionCandidates: capped at 8', () => {
  const many = Array.from({ length: 20 }, (_, i) => ({ name: 'X' + i }));
  assert.equal(mentionCandidates('x', many).length, 8);
});

test('mentionCandidates: null members → just todos for empty query', () => {
  assert.deepEqual(mentionCandidates('', null), [{ special: true, name: 'todos' }]);
});

// ── quickReplyCandidates ────────────────────────────────────────────
const QRS = [
  { id: 1, short_code: 'ola', content: 'Olá, tudo bem?' },
  { id: 2, short_code: 'bye', content: 'Até logo' },
];

test('quickReplyCandidates: matches short_code', () => {
  assert.deepEqual(quickReplyCandidates('ola', QRS).map(q => q.id), [1]);
});

test('quickReplyCandidates: matches content too', () => {
  assert.deepEqual(quickReplyCandidates('logo', QRS).map(q => q.id), [2]);
});

test('quickReplyCandidates: empty query returns all (capped 8)', () => {
  assert.equal(quickReplyCandidates('', QRS).length, 2);
});

test('quickReplyCandidates: null list → []', () => {
  assert.deepEqual(quickReplyCandidates('x', null), []);
});

// ── stripGroupPrefix ────────────────────────────────────────────────
test('stripGroupPrefix: splits "[Sender]: text"', () => {
  assert.deepEqual(stripGroupPrefix('[Ana]: oi pessoal'),
    { sender: 'Ana', text: 'oi pessoal' });
});

test('stripGroupPrefix: multiline body kept', () => {
  assert.deepEqual(stripGroupPrefix('[Bob]: linha1\nlinha2'),
    { sender: 'Bob', text: 'linha1\nlinha2' });
});

test('stripGroupPrefix: no prefix → sender null, text unchanged', () => {
  assert.deepEqual(stripGroupPrefix('plain text'),
    { sender: null, text: 'plain text' });
});

test('stripGroupPrefix: empty/null content → empty text', () => {
  assert.deepEqual(stripGroupPrefix(''), { sender: null, text: '' });
  assert.deepEqual(stripGroupPrefix(null), { sender: null, text: '' });
});
