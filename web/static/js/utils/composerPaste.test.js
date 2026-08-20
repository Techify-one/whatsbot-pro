// Normalização Unicode da colagem no compositor (node --test).
//   node --test web/static/js/utils/composerPaste.test.js
//
// Plano 132 · F7. Ver o cabeçalho de composerPaste.js para o porquê.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { toComposerNfc, needsNfcNormalization } from './composerPaste.js';

// "manutenção" nas duas formas. Iguais na tela, diferentes na contagem.
const NFC = 'manutenção';                       // ç, ã pré-compostos
const NFD = 'manutenção';                     // c+cedilha, a+til

test('as duas formas se desenham igual mas têm comprimentos diferentes', () => {
  assert.equal(NFC.normalize('NFC'), NFD.normalize('NFC'));  // mesmo texto
  assert.equal(NFC.length, 10);
  assert.equal(NFD.length, 12);   // é isso que faz o Backspace comer só o acento
});

test('texto decomposto (NFD) é normalizado', () => {
  assert.equal(toComposerNfc(NFD), NFC);
  assert.equal(toComposerNfc(NFD).length, 10);
});

test('texto já em NFC volta pela MESMA referência (call site deixa nativo)', () => {
  // O `===` do call site é o que decide interceptar ou não a colagem. Devolver
  // uma cópia igual faria toda colagem passar pelo caminho interceptado.
  const s = 'já está em NFC, com acentuação normal';
  assert.equal(toComposerNfc(s), s);
  assert.ok(Object.is(toComposerNfc(s), s));
  assert.equal(needsNfcNormalization(s), false);
});

test('texto sem acento nenhum volta pela mesma referência', () => {
  const s = 'plain ascii text 12345';
  assert.ok(Object.is(toComposerNfc(s), s));
  assert.equal(needsNfcNormalization(s), false);
});

test('needsNfcNormalization identifica o decomposto', () => {
  assert.equal(needsNfcNormalization(NFD), true);
  assert.equal(needsNfcNormalization(NFC), false);
});

test('entradas não-string / vazias passam intactas', () => {
  assert.equal(toComposerNfc(''), '');
  assert.equal(toComposerNfc(null), null);
  assert.equal(toComposerNfc(undefined), undefined);
  assert.equal(needsNfcNormalization(''), false);
  assert.equal(needsNfcNormalization(null), false);
});

test('emoji e caracteres fora do BMP não são alterados', () => {
  const s = 'festa 🎉 com bandeira 🇧🇷 e família 👨‍👩‍👧';
  assert.ok(Object.is(toComposerNfc(s), s));
});

test('normalização preserva o texto visível — só muda a codificação', () => {
  const frase = 'A manuteņção do serviço';
  assert.equal(toComposerNfc(frase).normalize('NFD'), frase.normalize('NFD'));
});

test('quebras de linha e espaços sobrevivem à normalização', () => {
  const s = 'linha 1\nlinha 2 com ação\n\n';
  const out = toComposerNfc(s);
  assert.ok(out.endsWith('\n\n'));
  assert.equal(out.split('\n').length, s.split('\n').length);
});
