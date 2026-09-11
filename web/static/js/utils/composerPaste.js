// @ts-check
//
// Normalização Unicode do texto COLADO no compositor (plano 132 · F7).
//
// Um "ç" pode ser gravado de duas formas: NFC, um único ponto de código U+00E7,
// ou NFD, a letra "c" seguida da marca combinante U+0327. As duas se DESENHAM
// igual — e é isso que torna o defeito difícil de acreditar quando alguém o
// relata. O que muda é a contagem: em NFD, "manutenção" tem 11 caracteres em vez
// de 10, e o Backspace do navegador remove UMA unidade de código por vez. Com o
// cursor no fim, o primeiro Backspace tira só o til e a palavra vira
// "manutençao" — o caractere apagado está visualmente no MEIO, exatamente como o
// operador descreveu.
//
// Texto colado de PDF, de páginas geradas no macOS ou de sistemas legados vem em
// NFD com frequência; o que se digita no teclado vem em NFC. Por isso a
// normalização é feita SÓ na colagem:
//
//   ⚠️ Normalizar a cada tecla (em `handleInputChange`) seria um erro — dentro de
//   um componente controlado isso interrompe a sessão de composição da tecla
//   morta do ABNT2 (o `~` de "ã", o `` ` `` de "à") e joga o caret para o fim do
//   campo a cada acento digitado. É a decisão D4 do plano.
//
// PURO: sem DOM, sem estado de módulo. O call site decide o que fazer com o
// resultado — e usa a IDENTIDADE da string para saber se há algo a fazer.

/**
 * Devolve o texto em NFC. Quando ele JÁ está em NFC, devolve **a mesma
 * referência** — assim o call site distingue "nada a fazer" (e deixa a colagem
 * seguir pelo caminho nativo do navegador, que é o que funciona hoje) de
 * "precisa interceptar" com um `===`, sem comparar strings longas de novo.
 *
 * @param {string} text
 * @returns {string} o próprio `text` quando já normalizado; a forma NFC caso contrário.
 */
export function toComposerNfc(text) {
  if (typeof text !== 'string' || text === '') return text;
  let nfc;
  try {
    nfc = text.normalize('NFC');
  } catch {
    return text;              // ambiente sem String.prototype.normalize: não piora nada
  }
  return nfc === text ? text : nfc;
}

/**
 * `true` quando o texto está decomposto e a colagem precisa ser interceptada.
 *
 * @param {string} text
 * @returns {boolean}
 */
export function needsNfcNormalization(text) {
  return toComposerNfc(text) !== text;
}
