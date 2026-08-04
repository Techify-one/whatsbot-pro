// Testes de CARACTERIZAÇÃO do renderizador de texto da bolha (node --test).
//   node --test web/static/js/utils/formatWhatsApp.test.js
//
// Plano 97 · F0. `formatWhatsApp` renderiza TODA bolha, legenda de mídia e card
// de sistema do painel e não tinha um único teste. Estes casos congelam o
// comportamento atual, regra por regra, ANTES de a F2 delegar a linkificação ao
// módulo `services/messageEntities.js`. Um caso que quebre depois é regressão —
// só a F2 reescreve, de propósito, os dois casos que o plano MUDA (a âncora de
// URL ganha `data-entity`/`data-value`, e e-mail com usuário numérico deixa de
// casar a regra de JID).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { formatWhatsApp } from './formatWhatsApp.js';

// ── Regra 1: escape (invariante de segurança — D5) ───────────────

test('escapa <, &, " e \' antes de qualquer formatação', () => {
  assert.equal(
    formatWhatsApp('<b>x</b> & "aspas" \'simples\''),
    '&lt;b&gt;x&lt;/b&gt; &amp; &quot;aspas&quot; &#39;simples&#39;',
  );
});

test('texto vazio/nulo vira string vazia', () => {
  assert.equal(formatWhatsApp(''), '');
  assert.equal(formatWhatsApp(null), '');
  assert.equal(formatWhatsApp(undefined), '');
});

test('HTML injetado no texto nunca vira tag', () => {
  const out = formatWhatsApp('<img src=x onerror=alert(1)>');
  assert.ok(!out.includes('<img'));
  assert.ok(out.startsWith('&lt;img'));
});

// ── Regras 2-5: markup do WhatsApp ───────────────────────────────

test('code block (```) vem antes do inline (`)', () => {
  const out = formatWhatsApp('```const a = 1;```');
  assert.ok(out.startsWith('<pre '));
  assert.ok(out.includes('const a = 1;'));
  assert.ok(!out.includes('<code'));
});

test('code inline vira <code>', () => {
  const out = formatWhatsApp('use `npm test` aqui');
  assert.ok(out.includes('<code '));
  assert.ok(out.includes('npm test'));
});

test('negrito, itálico e tachado', () => {
  assert.equal(formatWhatsApp('*forte*'), '<b>forte</b>');
  assert.equal(formatWhatsApp('isso é _importante_ ok'), 'isso é <i>importante</i> ok');
  assert.equal(formatWhatsApp('~riscado~'), '<s>riscado</s>');
});

test('itálico usa \\b — underscore no meio de palavra não vira <i>', () => {
  assert.equal(formatWhatsApp('nome_do_arquivo'), 'nome_do_arquivo');
});

// ── Regra 6: URL ─────────────────────────────────────────────────

test('URL vira âncora com target/rel e o estilo azul da bolha', () => {
  const out = formatWhatsApp('veja https://exemplo.com/x agora');
  assert.ok(out.includes('<a href="https://exemplo.com/x"'));
  assert.ok(out.includes('target="_blank"'));
  assert.ok(out.includes('rel="noopener noreferrer"'));
  assert.ok(out.includes('color:#53bdeb'));
  assert.ok(out.includes('text-decoration:underline'));
  assert.ok(out.includes('word-break:break-all'));
  assert.ok(out.startsWith('veja <a '));
  assert.ok(out.endsWith('</a> agora'));
});

test('URL com & na query mantém a entidade &amp; no texto E no href', () => {
  const out = formatWhatsApp('https://x.com/?a=1&b=2');
  assert.ok(out.includes('href="https://x.com/?a=1&amp;b=2"'));
  assert.ok(out.includes('>https://x.com/?a=1&amp;b=2</a>'));
});

test('http:// também vira âncora; texto sem esquema não vira', () => {
  assert.ok(formatWhatsApp('http://a.b').includes('<a href="http://a.b"'));
  assert.ok(!formatWhatsApp('exemplo.com').includes('<a '));
});

// ── Regra 7: JID do WhatsApp ─────────────────────────────────────

test('JID vira span azul não-clicável (não é âncora)', () => {
  const out = formatWhatsApp('de 5511999999999@s.whatsapp.net');
  assert.ok(out.includes('5511999999999@s.whatsapp.net'));
  assert.ok(out.includes('cursor:default') || out.includes('data-entity="jid"'));
  assert.ok(!out.includes('<a '));
});

// ── Regra 8: menções ─────────────────────────────────────────────

test('@menção de membro conhecido vira span destacado', () => {
  const out = formatWhatsApp('bom dia @Maria', ['Maria']);
  assert.ok(out.includes('<span style="color:#53bdeb;font-weight:600">@Maria</span>'));
});

test('@todos é destacado mesmo sem membros resolvidos', () => {
  const out = formatWhatsApp('@todos atenção');
  assert.ok(out.includes('>@todos</span>'));
});

test('nome de membro com caractere de regex é escapado (não explode)', () => {
  const out = formatWhatsApp('oi @A. (B)', ['A. (B)']);
  assert.ok(out.includes('>@A. (B)</span>'));
  // e não casa um nome parecido só porque `.` seria coringa
  assert.ok(!formatWhatsApp('oi @AX (B)', ['A. (B)']).includes('>@AX (B)</span>'));
});

test('nome mais longo vence o mais curto (ordenação por tamanho)', () => {
  const out = formatWhatsApp('oi @Ana Paula', ['Ana', 'Ana Paula']);
  assert.ok(out.includes('>@Ana Paula</span>'));
});

// ── F2: o que o plano MUDA de propósito ──────────────────────────

test('a âncora de URL ganha data-entity/data-value (o menu lê daí)', () => {
  const out = formatWhatsApp('https://exemplo.com/x');
  assert.ok(out.includes('data-entity="url"'));
  assert.ok(out.includes('data-value="https://exemplo.com/x"'));
});

test('e-mail com usuário numérico deixa de ser JID e vira mailto: (§2.4)', () => {
  const out = formatWhatsApp('5511999@gmail.com');
  assert.ok(out.includes('href="mailto:5511999@gmail.com"'));
  assert.ok(!out.includes('cursor:default'));
});

test('e-mail comum vira link mailto:', () => {
  const out = formatWhatsApp('escreva para contato@empresa.com.br');
  assert.ok(out.includes('href="mailto:contato@empresa.com.br"'));
  assert.ok(out.includes('data-entity="email"'));
});

test('telefone com + e máscara BR viram tel:', () => {
  assert.ok(formatWhatsApp('ligue +55 11 99999-8888').includes('href="tel:+5511999998888"'));
  assert.ok(formatWhatsApp('ligue (11) 99999-8888').includes('href="tel:+5511999998888"'));
});

test('nota do plugin protocolos NÃO ganha link (D4)', () => {
  const nota = '🔖 Protocolo aberto · PROT-12345678';
  assert.equal(formatWhatsApp(nota), nota);
});

test('menção + link + e-mail na mesma mensagem: nenhuma regra come a outra', () => {
  const out = formatWhatsApp(
    '@Empresa veja https://empresa.com/a e escreva para contato@empresa.com',
    ['Empresa'],
  );
  // A menção destaca só o texto — não entra no href do e-mail nem no do link.
  assert.ok(out.includes('<span style="color:#53bdeb;font-weight:600">@Empresa</span> veja'));
  assert.ok(out.includes('href="https://empresa.com/a"'));
  assert.ok(out.includes('href="mailto:contato@empresa.com"'));
  assert.ok(!out.includes('mailto:contato<span'));
  assert.equal((out.match(/<a /g) || []).length, 2);
});

test('URL dentro de negrito/itálico continua virando âncora', () => {
  const out = formatWhatsApp('*https://a.b/c*');
  assert.ok(out.startsWith('<b>'));
  assert.ok(out.includes('href="https://a.b/c"'));
});
