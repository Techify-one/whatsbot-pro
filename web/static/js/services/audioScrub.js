// @ts-check
//
// Aritmética de posição do player de áudio (plano 138 · F1).
//
// Por que existe: o `AudioPlayer` nasceu com o cálculo do seek inline, numa
// linha só e sem clamp nenhum — `a.currentTime = (x / rect.width) * duration`.
// Essa linha nunca teve teste, e o bug que originou este módulo (arrastar a
// barra para retroceder não fazia nada) sobreviveu meses justamente porque não
// havia onde uma regressão de posição aparecer verde/vermelha.
//
// Tudo aqui é PURO — sem preact, sem DOM, sem `document` — no mesmo espírito de
// `composerSubmit.js` / `mediaLimits.js` / `chatCalendar.js`. O componente
// entrega números (um `clientX`, um `DOMRect`, uma duração) e recebe números de
// volta; quem toca no elemento `<audio>` é ele.
//
// ⚠️ REGRA DE OURO deste módulo: **entrada inválida nunca vira `NaN` para fora.**
// Um `NaN` atribuído a `HTMLMediaElement.currentTime` levanta `TypeError` em
// alguns navegadores e é engolido em silêncio em outros — os dois desfechos são
// piores que "não fazer nada". Duração desconhecida (`0`, `NaN`, `Infinity` —
// os três acontecem de verdade: antes do `loadedmetadata`, em stream sem
// cabeçalho, em blob local recém-criado) devolve `0` e `isSeekable() === false`,
// e é o `false` que o componente usa para desabilitar o controle VISIVELMENTE
// em vez de falhar mudo.

/** @param {number} v @param {number} lo @param {number} hi */
function clamp(v, lo, hi) {
  if (!Number.isFinite(v)) return lo;
  return v < lo ? lo : (v > hi ? hi : v);
}

/**
 * A duração é utilizável para buscar?
 *
 * `Number.isFinite` (e não o `isFinite` global) de propósito: o global faz
 * coerção, então `isFinite("30")` é `true` e uma duração em string passaria.
 *
 * @param {*} duration
 * @returns {boolean}
 */
export function isSeekable(duration) {
  return typeof duration === 'number' && Number.isFinite(duration) && duration > 0;
}

/**
 * Onde o ponteiro caiu, em fração 0..1 da largura da barra.
 *
 * ⚠️ O clamp NÃO é decorativo: a bolinha do playhead tem 12px e transborda 6px
 * para a esquerda da barra: um `pointerdown` sobre essa saliência produz `x`
 * NEGATIVO, e o código antigo transformava isso num `currentTime` negativo.
 * Largura zero (elemento ainda não medido, bolha fora da tela) devolve 0 em vez
 * de dividir por zero.
 *
 * @param {number} clientX
 * @param {{left:number, width:number} | null | undefined} rect
 * @returns {number} 0..1
 */
export function ratioFromPointer(clientX, rect) {
  if (!rect) return 0;
  const width = rect.width;
  if (!Number.isFinite(width) || width <= 0) return 0;
  if (!Number.isFinite(clientX) || !Number.isFinite(rect.left)) return 0;
  return clamp((clientX - rect.left) / width, 0, 1);
}

/**
 * Fração da barra → segundos, clampado dentro do arquivo.
 *
 * @param {number} ratio 0..1
 * @param {*} duration
 * @returns {number} segundos, sempre finito
 */
export function timeFromRatio(ratio, duration) {
  if (!isSeekable(duration)) return 0;
  return clamp(clamp(ratio, 0, 1) * duration, 0, duration);
}

/**
 * Largura do preenchimento da barra, em porcentagem.
 *
 * ⚠️ D5 — enquanto o dedo está arrastando (`scrubRatio` não-nulo), o arraste
 * VENCE o `currentTime`. Sem isso o `timeupdate` do áudio, que continua
 * disparando ~4x por segundo porque o áudio segue tocando, redesenha a barra
 * embaixo do cursor: ela FOGE do dedo. Era metade da sensação de "não voltou".
 *
 * @param {{currentTime?:number, duration?:*, scrubRatio?:number|null}} s
 * @returns {number} 0..100
 */
export function progressPercent({ currentTime = 0, duration = 0, scrubRatio = null } = {}) {
  if (scrubRatio != null && Number.isFinite(scrubRatio)) {
    return clamp(scrubRatio, 0, 1) * 100;
  }
  if (!isSeekable(duration)) return 0;
  return clamp(currentTime / duration, 0, 1) * 100;
}

/**
 * Posição exibida: durante o arraste é a do ARRASTE, não a do `timeupdate`
 * (mesma razão de `progressPercent` — o rótulo de tempo não pode contar uma
 * história diferente da barra logo acima dele).
 *
 * @param {{currentTime?:number, duration?:*, scrubRatio?:number|null}} s
 * @returns {number} segundos, sempre finito
 */
export function displayTime({ currentTime = 0, duration = 0, scrubRatio = null } = {}) {
  if (scrubRatio != null && Number.isFinite(scrubRatio)) {
    return timeFromRatio(scrubRatio, duration);
  }
  return clamp(currentTime, 0, isSeekable(duration) ? duration : 0);
}

/**
 * Passo de teclado (setas / Home / End), clampado nas duas pontas.
 *
 * @param {number} currentTime
 * @param {number} deltaSeconds negativo retrocede
 * @param {*} duration
 * @returns {number} segundos, sempre finito
 */
export function nudge(currentTime, deltaSeconds, duration) {
  if (!isSeekable(duration)) return 0;
  const base = Number.isFinite(currentTime) ? currentTime : 0;
  const delta = Number.isFinite(deltaSeconds) ? deltaSeconds : 0;
  return clamp(base + delta, 0, duration);
}

/**
 * `mm:ss` — vive aqui (e não no componente) porque o rótulo agora mostra
 * POSIÇÃO e DURAÇÃO lado a lado, e as duas passam pelo mesmo formato.
 *
 * @param {*} seconds
 * @returns {string}
 */
export function formatClock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
