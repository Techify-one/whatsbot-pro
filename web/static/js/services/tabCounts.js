// @ts-check
//
// Decisão pura do contador das abas do hub (plano 130): QUAL número mostrar e
// QUANDO buscá-lo. Vivia inline em `useConversationFilters`, onde não era testável
// — e por isso o badge oscilava entre o total do servidor (ex.: 252) e o número de
// linhas carregadas na página (50) a cada evento de WebSocket.
//
// PURO: sem Preact, sem fetch, sem relógio próprio (o `now` entra por parâmetro —
// é o que torna o teto de frequência testável). O hook é dono dos refs, do timer e
// da requisição; aqui só se decide.
//
// Precedentes do repo: threadJump.js, hubDefaults.js, composerSubmit.js.

import { buildCountParams } from './conversationFilterSpec.js';

/** @typedef {{all:number, mine:number, unassigned:number, mentions:number}} TabCounts */

const COUNT_KEYS = ['all', 'mine', 'unassigned', 'mentions'];

/** @type {TabCounts} */
export const EMPTY_COUNTS = Object.freeze({ all: 0, mine: 0, unassigned: 0, mentions: 0 });

export const DEFAULT_DEBOUNCE_MS = 300;
// Teto de frequência do refetch disparado por "a lista mudou" (plano 130 · D3/P1).
// O total muda devagar (conversa abre/resolve), mas o gatilho é altíssimo: o
// `conversation_upsert` sai a cada mensagem visível da INSTÂNCIA inteira (o /ws não
// tem escopo por canal — plano 90). Mudança de FILTRO não passa por aqui: ela é
// imediata (o usuário está esperando).
export const DEFAULT_MIN_INTERVAL_MS = 4000;

/**
 * Se `c` já é um objeto de contagem completo e numérico, devolve a MESMA referência
 * (não criar objeto novo a cada render: `tabCounts` desce como prop). Caso contrário
 * normaliza o que der e completa o resto com 0.
 * @param {any} c
 * @returns {TabCounts}
 */
function coerceCounts(c) {
  if (!c || typeof c !== 'object') return EMPTY_COUNTS;
  let clean = true;
  for (const k of COUNT_KEYS) {
    if (typeof c[k] !== 'number' || !Number.isFinite(c[k])) { clean = false; break; }
  }
  if (clean) return /** @type {TabCounts} */ (c);
  /** @type {any} */
  const out = {};
  for (const k of COUNT_KEYS) {
    const n = Number(c[k]);
    out[k] = Number.isFinite(n) ? n : 0;
  }
  return out;
}

/**
 * Chave estável do spec de CONTAGEM: muda quando (e só quando) o filtro muda.
 *
 * Derivada de `buildCountParams` — a MESMA função que monta a query — com chaves e
 * valores de lista ORDENADOS: dois specs equivalentes (etiquetas na outra ordem,
 * array recriado com o mesmo conteúdo) têm de produzir a mesma chave, senão o reset
 * dispararia à toa e o total voltaria a piscar.
 *
 * @param {Record<string, any>} [spec]
 * @returns {string}
 */
export function countSpecKey(spec) {
  const params = buildCountParams(spec || {});
  return Object.keys(params).sort().map((k) => {
    const v = params[k];
    const val = Array.isArray(v) ? [...v].map(String).sort().join('|') : String(v);
    return `${k}=${val}`;
  }).join('&');
}

/**
 * Qual contagem exibir.
 *
 * Em `serverMode` a lista NÃO é re-filtrada no cliente, então `clientCounts` é
 * literalmente o tamanho da página carregada — nunca um total. Ele só serve ao
 * primeiro paint (antes do 1º total chegar) e ao caminho client-side, onde a lista
 * é de fato filtrada no cliente e o número está certo (plano 130 · D2).
 *
 * @param {{serverCounts?: any, clientCounts?: any, serverMode?: boolean}} [args]
 * @returns {TabCounts}
 */
export function resolveTabCounts({ serverCounts, clientCounts, serverMode = true } = {}) {
  if (serverMode && serverCounts) return coerceCounts(serverCounts);
  if (clientCounts) return coerceCounts(clientCounts);
  return EMPTY_COUNTS;
}

/**
 * O que fazer neste tick.
 *
 * ⚠️ O prazo é ancorado em `pendingSince` (o PRIMEIRO gatilho da rajada), não em
 * `now`. É isso que conserta o segundo bug do plano 130: com o prazo ancorado no
 * "agora" de cada evento, uma rajada contínua reiniciava o `setTimeout` sem parar e
 * a contagem NUNCA chegava a ser buscada. Ancorada no início, o chamador pode
 * recriar o timer à vontade que o prazo não anda.
 *
 * - `specKey == null`  → `idle` (fora de serverMode; o cliente é autoritativo).
 * - spec mudou         → `reset_and_fetch` (o total antigo é de outro filtro).
 *                        Ignora o teto de propósito: o usuário está esperando.
 * - só a lista mudou   → `fetch` (prazo vencido) ou `wait` (o que falta dele).
 *
 * @param {{specKey?: string|null, lastSpecKey?: string|null, lastFetchAt?: number|null,
 *          pendingSince?: number|null, now?: number, debounceMs?: number,
 *          minIntervalMs?: number}} args
 * @returns {{action: 'reset_and_fetch'|'fetch'|'wait'|'idle', delayMs: number}}
 */
export function planCountFetch({
  specKey = null, lastSpecKey = null, lastFetchAt = null, pendingSince = null,
  now = 0, debounceMs = DEFAULT_DEBOUNCE_MS, minIntervalMs = DEFAULT_MIN_INTERVAL_MS,
} = {}) {
  if (specKey == null) return { action: 'idle', delayMs: 0 };
  if (specKey !== lastSpecKey) return { action: 'reset_and_fetch', delayMs: debounceMs };
  const since = pendingSince == null ? now : pendingSince;
  const floor = lastFetchAt == null ? -Infinity : lastFetchAt + minIntervalMs;
  const deadline = Math.max(since + debounceMs, floor);
  if (deadline <= now) return { action: 'fetch', delayMs: 0 };
  return { action: 'wait', delayMs: deadline - now };
}
