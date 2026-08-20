// Testes de CARACTERIZAÇÃO do espelho de highlight do compositor (node --test).
//   node --test web/static/js/utils/composerHighlight.test.js
//
// Plano 132 · F0. `highlightComposerMarkup` pinta o <div> que o operador LÊ
// atrás de uma <textarea> de texto transparente, nos DOIS compositores do
// painel (Composer.js e NewConversationModal.js), e até aqui não tinha um único
// teste — foi essa lacuna que deixou passar a família inteira de bugs de caret
// da investigação 131.
//
// O invariante que estes casos travam é a PARIDADE DE CONTAGEM: o texto visível
// do espelho tem de ter exatamente o mesmo comprimento do valor da textarea. É
// dele que depende o caret cair onde o operador clica — encurtar o espelho em um
// caractere desloca todo o resto da linha. Por isso os marcadores são mantidos
// (só esmaecidos) em vez de removidos, e por isso a linha final da F1 entra como
// `<br>` (elemento, invisível ao textContent) e nunca como `\n` literal.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { highlightComposerMarkup } from './formatWhatsApp.js';

// ── Helper: o `textContent` que o navegador extrairia do HTML gerado ──
//
// Sem DOM (o repo não tem jsdom), então reproduzimos a regra: tag não é texto,
// entidade é UM caractere. Cobre só as entidades que a função de fato emite —
// as 5 do escape e as 5 dos marcadores re-emitidos.
const ENTITIES = {
  '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'",
  '&#96;': '`', '&#42;': '*', '&#95;': '_', '&#126;': '~',
};

function textContentOf(htmlStr) {
  return htmlStr
    .replace(/<[^>]+>/g, '')                       // tags não são texto (inclui <br>)
    .replace(/&(?:amp|lt|gt|quot|#39|#96|#42|#95|#126);/g, m => ENTITIES[m]);
}

/** O que o operador VÊ tem de ter o mesmo comprimento do que ele DIGITOU. */
function assertParidade(entrada) {
  const visivel = textContentOf(highlightComposerMarkup(entrada));
  assert.equal(
    visivel.length,
    entrada.length,
    `paridade quebrada para ${JSON.stringify(entrada)}: ` +
    `espelho=${visivel.length} textarea=${entrada.length}`,
  );
  // Não basta o comprimento bater: o texto tem de ser o MESMO.
  assert.equal(visivel, entrada);
}

// ── Regra 1: escape (invariante de segurança) ────────────────────
//
// A saída vai para `dangerouslySetInnerHTML`, então o escape roda ANTES de
// qualquer marcação e é a única coisa entre o operador e um XSS armazenado.

test('escapa <, >, &, " e \' antes de qualquer formatação', () => {
  assert.equal(
    highlightComposerMarkup('<b>x</b> & "aspas" \'simples\''),
    '&lt;b&gt;x&lt;/b&gt; &amp; &quot;aspas&quot; &#39;simples&#39;',
  );
});

test('HTML injetado no texto nunca vira tag', () => {
  const out = highlightComposerMarkup('<img src=x onerror=alert(1)>');
  assert.ok(!out.includes('<img'));
  assert.ok(out.startsWith('&lt;img'));
});

test('escape preserva a paridade de contagem', () => {
  assertParidade('<b>x</b> & "aspas" \'simples\'');
});

test('texto vazio/nulo vira string vazia', () => {
  assert.equal(highlightComposerMarkup(''), '');
  assert.equal(highlightComposerMarkup(null), '');
  assert.equal(highlightComposerMarkup(undefined), '');
});

// ── Regra 2: um caso por marcador, sempre com o marcador PRESERVADO ──

const DIM_OPEN = '<span style="opacity:.4">';
// Espelham as constantes de highlightComposerMarkup. Mudaram? Foi de propósito?
const CODE_STYLE = 'background:rgb(var(--wa-text) / .12);border-radius:3px';
const BOLD_STYLE = '-webkit-text-stroke:.4px currentColor';

test('bloco de código (```) mantém as três crases dos dois lados', () => {
  assert.equal(
    highlightComposerMarkup('```bloco```'),
    `${DIM_OPEN}&#96;&#96;&#96;</span><span style="${CODE_STYLE}">bloco</span>` +
    `${DIM_OPEN}&#96;&#96;&#96;</span>`,
  );
});

test('código inline (`) mantém as crases', () => {
  assert.equal(
    highlightComposerMarkup('`mono`'),
    `${DIM_OPEN}&#96;</span><span style="${CODE_STYLE}">mono</span>${DIM_OPEN}&#96;</span>`,
  );
});

test('negrito exige DOIS asteriscos e mantém os quatro', () => {
  assert.equal(
    highlightComposerMarkup('**negrito**'),
    `${DIM_OPEN}&#42;&#42;</span><span style="${BOLD_STYLE}">negrito</span>${DIM_OPEN}&#42;&#42;</span>`,
  );
});

// ── Regra 2b: o realce NÃO pode alterar a métrica da fonte (D3) ───
//
// Este é o invariante que a F2 trava. Um <b> ou um `font-family:monospace` no
// espelho muda a largura de avanço das letras, as duas camadas quebram a linha
// em pontos diferentes e o caret desencontra do texto — medido: 52 de 52 cliques
// errados com dois negritos, e uma linha inteira a menos com três.

test('nenhuma tag de peso ou família tipográfica sai do realce', () => {
  const out = highlightComposerMarkup('**a** `b` ```c``` _d_ ~e~');
  for (const proibida of ['<b>', '<strong>', '<code', 'font-family', 'font-weight',
                          'font-size', 'letter-spacing', 'font-stretch']) {
    assert.ok(!out.includes(proibida), `realce não pode emitir ${proibida}: ${out}`);
  }
});

test('itálico e tachado continuam com as tags nativas (métrica neutra medida)', () => {
  assert.ok(highlightComposerMarkup('_x_').includes('<i>'));
  assert.ok(highlightComposerMarkup('~x~').includes('<s>'));
});

test('itálico (_) mantém os sublinhados', () => {
  assert.equal(
    highlightComposerMarkup('_italico_'),
    `${DIM_OPEN}&#95;</span><i>italico</i>${DIM_OPEN}&#95;</span>`,
  );
});

test('tachado (~) mantém os tis', () => {
  assert.equal(
    highlightComposerMarkup('~tachado~'),
    `${DIM_OPEN}&#126;</span><s>tachado</s>${DIM_OPEN}&#126;</span>`,
  );
});

test('marcador é re-emitido como entidade, nunca como caractere cru', () => {
  // Se saísse cru, a regra seguinte do pipeline re-casaria um marcador que esta
  // acabou de produzir e o espelho encurtaria.
  const out = highlightComposerMarkup('**a** `b` ~c~');
  assert.ok(!/<span style="opacity:\.4">[*`~_]/.test(out));
});

// ── Regra 3: marcador solto NÃO vira tag ─────────────────────────

test('asterisco simples (sintaxe do WhatsApp) não vira negrito no compositor', () => {
  // O compositor autora com **, e é `toWhatsAppMarkup` quem colapsa no envio.
  assert.equal(highlightComposerMarkup('só *um* asterisco'), 'só *um* asterisco');
});

test('marcador sem par passa intacto', () => {
  assert.equal(highlightComposerMarkup('* solto'), '* solto');
  assert.equal(highlightComposerMarkup('texto `sem par'), 'texto `sem par');
  assert.equal(highlightComposerMarkup('meio ~ do caminho'), 'meio ~ do caminho');
});

// ── Regra 4: PARIDADE DE CONTAGEM — o invariante do caret ─────────
//
// Qualquer mudança futura em `highlightComposerMarkup` tem de manter estes
// casos verdes. É a rede que a F1 precisa para acrescentar a linha final sem
// deslocar o caret.

const CORPUS = [
  'texto simples sem marcação nenhuma',
  '**negrito** no início',
  'no meio tem **negrito** e segue',
  '`mono` e ~tachado~ e _italico_ juntos',
  '```bloco de código```',
  'acentuação: ação, coração, não, você, três',
  'emoji 🎉 fora do BMP conta como dois',
  'a\nb\nc',
  'termina em quebra\n',
  'termina em duas quebras\n\n',
  '<script>alert(1)</script>',
  'e-mail contato@empresa.com e URL https://exemplo.com/a_b_c',
  '* solto e ` solto e ~ solto',
  '**dois** negritos **na** mesma linha',
  'linha longa '.repeat(40),
];

for (const entrada of CORPUS) {
  test(`paridade de contagem: ${JSON.stringify(entrada.slice(0, 42))}`, () => {
    assertParidade(entrada);
  });
}

// ── Regra 5: quebras de linha e a LINHA FINAL (plano 132 · F1) ───
//
// A <textarea> reserva uma linha vazia para o caret quando o valor termina em
// quebra; o `pre-wrap` do espelho não gera essa linha. Sem o <br> o espelho fica
// 20px mais curto, `syncMirror` trunca o `scrollTop` copiado e o campo mostra
// tudo uma linha adiantado — é a causa raiz do chamado.

test('quebras internas passam como \\n literal (o pre-wrap as renderiza)', () => {
  assert.equal(highlightComposerMarkup('linha1\nlinha2'), 'linha1\nlinha2');
});

test('texto terminado em quebra ganha a linha final como <br>', () => {
  assert.equal(highlightComposerMarkup('linha1\n'), 'linha1\n<br>');
});

test('texto SEM quebra final não ganha nada', () => {
  assert.equal(highlightComposerMarkup('linha1'), 'linha1');
  assert.equal(highlightComposerMarkup('linha1\nlinha2'), 'linha1\nlinha2');
});

test('várias quebras no fim ganham exatamente UM <br>', () => {
  // O espelho fica sempre UMA linha curto, nunca duas: só a última quebra do
  // bloco é descartada pelo pre-wrap; as anteriores já produzem linha própria.
  assert.equal(highlightComposerMarkup('a\n\n'), 'a\n\n<br>');
  assert.equal(highlightComposerMarkup('a\n\n\n'), 'a\n\n\n<br>');
  assert.equal((highlightComposerMarkup('a\n\n').match(/<br>/g) || []).length, 1);
});

test('a linha final entra DEPOIS da marcação, sem quebrá-la', () => {
  assert.equal(
    highlightComposerMarkup('**oi**\n'),
    `${DIM_OPEN}&#42;&#42;</span><span style="${BOLD_STYLE}">oi</span>` +
    `${DIM_OPEN}&#42;&#42;</span>\n<br>`,
  );
});

test('a linha final NÃO conta como texto — a paridade sobrevive', () => {
  // O invariante do caret: <br> é elemento, não texto. Se um dia virar "\n"
  // literal, o espelho fica um caractere mais longo que a textarea e TODO o
  // resto da linha desloca — o oposto do que a F1 conserta.
  assertParidade('termina em quebra\n');
  assertParidade('duas quebras\n\n');
  assertParidade('**negrito** e quebra\n');
  assert.ok(!textContentOf(highlightComposerMarkup('a\n')).endsWith('\n\n'));
});

test('marcação não atravessa quebra de linha', () => {
  // As regras de inline excluem \n na classe de caracteres.
  assert.equal(highlightComposerMarkup('`a\nb`'), '`a\nb`');
  assert.equal(highlightComposerMarkup('~a\nb~'), '~a\nb~');
});
