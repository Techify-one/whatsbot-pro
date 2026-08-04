// @ts-check
//
// Destacar o termo buscado DENTRO da bolha — plano 99 · F2·4.
//
// O flash amarelo (`wa-msg-highlight`) diz em QUAL mensagem se aterrissou; isto
// diz ONDE, dentro dela, o termo aparece — que é o que resolve numa mensagem
// longa. A sidebar já faz o equivalente no trecho do resultado (`highlightParts`
// em ContactList.js), mas ali o alvo é texto puro; aqui o alvo já passou por
// `formatWhatsApp` e é **HTML**.
//
// Daí o cuidado central deste módulo: o destaque só pode entrar nos SEGMENTOS DE
// TEXTO, nunca dentro de uma tag. Um `replace` ingênuo sobre a string inteira
// casaria "code" dentro de `<code style=…>` e produziria markup quebrado — e como
// o resultado é injetado como HTML, markup quebrado aqui não é só feio.
//
// PURO: sem preact, sem DOM, sem rede, sem estado de módulo.

/** Casefold + tira acentos — espelha o `fold` do backend (db/search/contact_search.py). */
function foldStr(s) {
  return (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

const MARK_OPEN = '<mark class="wa-search-hit">';
const MARK_CLOSE = '</mark>';

/**
 * Envolve em `<mark>` cada ocorrência de `term` nos trechos de TEXTO de `html`.
 *
 * Devolve `html` intacto (mesma referência) quando não há o que destacar, para
 * o caminho normal do chat continuar byte-idêntico — o destaque só existe com o
 * modo busca aberto.
 *
 * Cai fora, sem destacar, quando dobrar o texto muda o comprimento (ligadura,
 * "ß" → "ss"): aí os índices do texto dobrado não mapeiam de volta no original
 * e o recorte sairia deslocado. Mesma salvaguarda do `highlightParts` da sidebar.
 *
 * @param {string} html - saída de `formatWhatsApp` (já escapada)
 * @param {string} term - o que o operador digitou
 * @returns {string}
 */
export function highlightHtml(html, term) {
  const q = foldStr(term);
  if (!q || !html) return html;
  // Alterna entre texto e tag: os índices ÍMPARES do split são as tags.
  const parts = String(html).split(/(<[^>]*>)/);
  let touched = false;
  for (let i = 0; i < parts.length; i += 2) {
    const out = highlightText(parts[i], q);
    if (out !== parts[i]) { parts[i] = out; touched = true; }
  }
  return touched ? parts.join('') : html;
}

/**
 * O destaque num trecho de texto puro já escapado.
 * @param {string} text
 * @param {string} foldedQ - termo JÁ dobrado
 * @returns {string}
 */
function highlightText(text, foldedQ) {
  if (!text) return text;
  const folded = foldStr(text);
  if (folded.length !== text.length) return text;
  let i = 0, out = '', found = false;
  for (;;) {
    const idx = folded.indexOf(foldedQ, i);
    if (idx === -1) { out += text.slice(i); break; }
    found = true;
    out += text.slice(i, idx) + MARK_OPEN + text.slice(idx, idx + foldedQ.length) + MARK_CLOSE;
    i = idx + foldedQ.length;
  }
  return found ? out : text;
}
