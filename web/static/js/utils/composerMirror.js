// ── Geometria do espelho de highlight do compositor ──────────────────
//
// O compositor (e o campo "Mensagem" da conversa nova) renderiza o texto num
// <div> espelho ATRÁS de um <textarea> de texto transparente. Para o cursor
// cair exatamente no fim do texto visível, os dois precisam quebrar as linhas
// no MESMO ponto — ou seja, ter a mesma largura de conteúdo.
//
// Quando a mensagem passa da altura máxima, o textarea ganha barra de rolagem
// e ela ocupa espaço de layout (`::-webkit-scrollbar { width: 6px }`), então o
// conteúdo do textarea fica mais estreito que o do espelho (que tem
// `overflow-hidden` e nunca ganha barra). Resultado: o espelho cabe mais
// caracteres por linha, o texto visível "termina antes" e o cursor aparece
// adiantado, depois de um espaço vazio.
//
// A correção é reservar a mesma sobra (o "gutter" da barra) no espelho, para
// as duas caixas de conteúdo terem a MESMA largura. Medido em vez de fixado em
// 6px porque fora do webkit a barra tem outra largura.

/** Sobra ocupada pela barra de rolagem vertical, em px. Puro (testável). */
export function scrollbarGutter(offsetWidth, clientWidth, borderWidth) {
  const gutter = offsetWidth - clientWidth - borderWidth;
  return gutter > 0 ? gutter : 0;
}

/**
 * Mantém o espelho alinhado ao textarea: mesma rolagem e mesma largura de
 * conteúdo. Chamar a cada mudança de texto e no `scroll` do textarea.
 */
export function syncMirror(textarea, mirror) {
  if (!textarea || !mirror) return;
  const cs = getComputedStyle(textarea);
  const borderWidth =
    (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0);
  // Medido SEMPRE no textarea (nunca no espelho já ajustado) — senão o valor
  // aplicado zeraria a diferença na medição seguinte e ficaria oscilando.
  const gutter = scrollbarGutter(textarea.offsetWidth, textarea.clientWidth, borderWidth);
  // Reserva a mesma sobra no espelho engordando o padding direito dele. O
  // padding-base sai do TEXTAREA (que nunca é mexido aqui), então repetir a
  // chamada é idempotente.
  const padRight = `${(parseFloat(cs.paddingRight) || 0) + gutter}px`;
  if (mirror.style.paddingRight !== padRight) mirror.style.paddingRight = padRight;
  mirror.scrollTop = textarea.scrollTop;
}
