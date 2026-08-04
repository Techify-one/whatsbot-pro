// Run with: node --test web/static/js/services/searchHighlight.test.js
//
// Plano 99 · F2·4 — destacar o termo dentro da bolha sem estragar o HTML que
// `formatWhatsApp` produziu.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { highlightHtml } from './searchHighlight.js';

test('destaca o termo no texto', () => {
  assert.equal(highlightHtml('segue o boleto anexo', 'boleto'),
    'segue o <mark class="wa-search-hit">boleto</mark> anexo');
});

test('NUNCA destaca dentro de uma tag', () => {
  // O perigo real: `<code style=…>` contém "code". Um replace ingênuo produziria
  // markup quebrado — e o resultado é injetado como HTML.
  const input = '<code style="font-family:monospace">code</code>';
  assert.equal(highlightHtml(input, 'code'),
    '<code style="font-family:monospace"><mark class="wa-search-hit">code</mark></code>');
  assert.equal(highlightHtml('<a href="https://x/style">z</a>', 'style'),
    '<a href="https://x/style">z</a>', 'destacou dentro do atributo');
});

test('acento e caixa não importam, mas o texto original é preservado', () => {
  assert.equal(highlightHtml('Falei com o João', 'joao'),
    'Falei com o <mark class="wa-search-hit">João</mark>');
  assert.equal(highlightHtml('ORÇAMENTO aprovado', 'orçamento'),
    '<mark class="wa-search-hit">ORÇAMENTO</mark> aprovado');
});

test('destaca todas as ocorrências', () => {
  const out = highlightHtml('pix, pix e mais pix', 'pix');
  assert.equal(out.split('<mark').length - 1, 3);
});

test('sem termo devolve a MESMA string (caminho normal do chat intacto)', () => {
  const s = 'oi <b>tudo bem</b>';
  assert.equal(highlightHtml(s, ''), s);
  assert.equal(highlightHtml(s, null), s);
  assert.equal(highlightHtml(s, 'zzz'), s, 'sem casamento não deveria remontar a string');
});

test('não quebra com entrada vazia', () => {
  assert.equal(highlightHtml('', 'x'), '');
  assert.equal(highlightHtml(null, 'x'), null);
});

test('desiste (sem destacar) quando dobrar muda o comprimento', () => {
  // 'ﬁ' (ligadura) vira 'fi' no NFKD: os índices do texto dobrado deixam de
  // mapear no original e o recorte sairia deslocado. Mesma salvaguarda do
  // `highlightParts` da sidebar.
  const s = 'o arquivo ﬁnal';
  assert.equal(highlightHtml(s, 'nal'), s);
});
