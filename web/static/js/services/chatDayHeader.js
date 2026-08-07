// @ts-check
//
// Plano 98 — "qual dia está no topo do chat?".
//
// Decisão PURA (sem Preact, sem DOM) por trás da pílula de data flutuante do
// chat: recebe os separadores de data JÁ MEDIDOS (coordenadas de VIEWPORT) e
// devolve o rótulo a exibir + o quanto a pílula deve deslizar para fora quando o
// separador inline do próximo dia chega ao topo (o "empurrão" do WhatsApp Web,
// que evita ler o mesmo dia duas vezes).
//
// Por que coordenadas de viewport (`getBoundingClientRect`) e não `offsetTop`:
// o chat faz prepend ao "carregar anteriores" e a posição de rolagem é
// restaurada somando o delta de altura — todo `offsetTop` muda, mas a posição
// VISUAL não. Medir em viewport a cada quadro é imune a isso (e à mídia que
// carrega depois e muda a altura da lista).
//
// ── Geometria (px, relativos à borda superior do container de rolagem) ────────
//
// A pílula ocupa a faixa [8, 34]: `top-[8px]` + ~26px de altura (12px de texto +
// `py-[5px]`) — a mesma altura do separador inline, que é o mesmo desenho.
//
//   delta = topo do PRÓXIMO separador − borda superior
//
//   delta >= PUSH_START ......... pílula parada, opaca
//   PUSH_END < delta < PUSH_START  desliza para cima proporcionalmente
//   delta <= PUSH_END ........... fora de cena (o inline ocupa o lugar dela)
//
// PUSH_END é 24 e não 0 de propósito: quando o separador inline chega à faixa da
// pílula os dois desenham o MESMO dia, e é aí que a pílula tem de já ter saído.
// É também o que resolve a conversa curta de um dia só, que não rola: o
// separador inline nasce a ~20px da borda e a pílula simplesmente não aparece —
// a resposta ("que dia é isto?") já está na tela, inline.

/** Deslocamento vertical (px) que tira a pílula inteira do campo de visão. */
export const PILL_TRAVEL = 44;
/** Distância da borda em que o empurrão COMEÇA. */
export const PUSH_START = 56;
/** Distância da borda em que o empurrão está COMPLETO (pílula invisível). */
export const PUSH_END = 24;

const DEFAULT_GEOM = { travel: PILL_TRAVEL, pushStart: PUSH_START, pushEnd: PUSH_END };

function toNumber(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Normaliza a lista medida: descarta entradas sem rótulo ou sem `top` numérico e
 * ordena por `top` (a ordem do documento já é essa, mas ordenar torna a função
 * imune a uma medição fora de ordem). `bottom` ausente cai no `top`.
 *
 * @param {Array<{label?:string, top?:number, bottom?:number}>|null|undefined} seps
 */
function normalize(seps) {
  if (!Array.isArray(seps)) return [];
  const out = [];
  for (const s of seps) {
    if (!s) continue;
    const top = toNumber(s.top);
    const label = s.label == null ? '' : String(s.label);
    if (top === null || !label) continue;
    const bottom = toNumber(s.bottom);
    out.push({ label, top, bottom: bottom === null ? top : bottom });
  }
  out.sort((a, b) => a.top - b.top);
  return out;
}

/**
 * Qual dia está no topo, e quanto a pílula deve deslizar para fora.
 *
 * @param {Array<{label?:string, top?:number, bottom?:number}>|null|undefined} seps
 *   separadores medidos em coordenadas de VIEWPORT, na ordem do documento
 *   (mais antigo → mais recente).
 * @param {number} topEdge coordenada Y da borda superior do container de rolagem.
 * @param {{travel?:number, pushStart?:number, pushEnd?:number}} [geom]
 *   override da geometria (default: as constantes deste módulo).
 * @returns {{label: string|null, offsetY: number}}
 *   `label === null` ⇒ nada a renderizar. `offsetY < 0` ⇒ a pílula está saindo;
 *   `offsetY === -travel` ⇒ está fora de cena.
 */
export function pickCurrentDay(seps, topEdge, geom) {
  const list = normalize(seps);
  if (!list.length) return { label: null, offsetY: 0 };

  const edge = toNumber(topEdge);
  if (edge === null) return { label: list[0].label, offsetY: 0 };

  const g = { ...DEFAULT_GEOM, ...(geom || {}) };
  const travel = Math.max(0, toNumber(g.travel) ?? PILL_TRAVEL);
  const pushEnd = toNumber(g.pushEnd) ?? PUSH_END;
  const pushStart = Math.max(pushEnd, toNumber(g.pushStart) ?? PUSH_START);

  // O dia corrente é o do último separador que saiu INTEIRO pelo topo (`bottom`,
  // não `top`): enquanto o separador inline ainda estiver visível, ele é que
  // responde pelo próprio dia — trocar o rótulo da pílula antes disso mostraria
  // o mesmo dia em dois lugares. Se nenhum saiu ainda (viewport no topo do
  // histórico carregado), o dia é o do primeiro — o da mensagem mais antiga.
  let idx = -1;
  for (let i = 0; i < list.length; i++) {
    if (list[i].bottom <= edge) idx = i;
    else break;
  }

  const label = list[idx === -1 ? 0 : idx].label;

  // Quem empurra é o PRÓXIMO separador (o do dia seguinte). No caso "nenhum saiu
  // ainda", quem empurra é o próprio primeiro: a pílula mostra o mesmo rótulo
  // dele, então sair de cena é justamente o que evita a duplicata.
  const pusher = list[idx === -1 ? 0 : idx + 1];
  if (!pusher) return { label, offsetY: 0 };

  const delta = pusher.top - edge;
  if (delta >= pushStart) return { label, offsetY: 0 };
  if (delta <= pushEnd) return { label, offsetY: -travel };
  const progress = (pushStart - delta) / (pushStart - pushEnd);   // 0 → 1
  return { label, offsetY: -travel * progress };
}
