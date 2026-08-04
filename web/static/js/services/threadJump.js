// @ts-check
//
// O que fazer quando o chat precisa PULAR para uma mensagem — plano 99 · F0e.
//
// Antes deste plano a regra era meia regra, escrita direto no efeito de scroll do
// `ContactDetail`: procurava o `[data-mid]` no DOM e, se não achasse, devolvia
// `false` e ninguém fazia mais nada. O alvo fora da janela de 50 mensagens
// carregadas simplesmente não era alcançado — a conversa abria no lugar errado, o
// sentinela do topo cascateava `loadOlder` de 50 em 50 e, na atualização em que o
// alvo finalmente chegava, a flag `justPrepended` comia a tentativa de foco. Se o
// alvo estivesse na última página possível, o salto NUNCA acontecia e nada avisava
// o operador. Isso valia para os três caminhos que já existiam (resultado da busca
// global, clique numa citação antiga, deep-link `?message=<id>`).
//
// Aqui a regra existe uma vez, tem nome e é testável. As três decisões possíveis:
//
//   focus    — o alvo está renderizado: rola e pisca.
//   fetch    — o alvo não está na janela: pedir ao servidor a janela ANCORADA nele
//              (`around_id`), em vez de esperar uma cascata que pode nunca chegar.
//   give_up  — já pedimos essa janela e o alvo continua ausente (mensagem apagada,
//              id de outra conversa, permalink velho). Avisar o operador e limpar
//              o pedido — o que NÃO se pode fazer é tentar de novo em laço.
//
// PURO: sem preact, sem DOM, sem rede, sem estado de módulo.

/**
 * @typedef {{action: 'none'|'focus'|'fetch'|'give_up'}} JumpPlan
 */

/**
 * @param {Object} opts
 * @param {string|number|null} opts.target - `_id` da mensagem a focar (null = nada pendente)
 * @param {boolean} opts.rendered - o alvo está na janela carregada?
 * @param {boolean} opts.requested - já pedimos a janela ancorada NESTE alvo?
 * @param {boolean} [opts.inFlight] - o pedido da janela ancorada está em voo
 * @returns {JumpPlan}
 */
export function planJump({ target, rendered, requested, inFlight = false }) {
  if (target == null) return { action: 'none' };
  if (rendered) return { action: 'focus' };
  // Enquanto a janela pedida não chegou, não pedir de novo nem desistir: a
  // próxima atualização de `messages` reavalia.
  if (inFlight) return { action: 'none' };
  if (requested) return { action: 'give_up' };
  return { action: 'fetch' };
}

/**
 * A mensagem-alvo está na janela carregada?
 *
 * Compara como STRING de propósito: o alvo chega de três origens diferentes
 * (atributo do DOM, query string do permalink, campo numérico da API) e um
 * `42 === '42'` falso é justamente o tipo de bug que some sem barulho.
 *
 * @param {any[]} messages
 * @param {string|number|null} target
 * @returns {boolean}
 */
export function isRendered(messages, target) {
  if (target == null) return false;
  const t = String(target);
  return (messages || []).some(m => m && m._id != null && String(m._id) === t);
}
