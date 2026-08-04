// @ts-check
//
// Como uma resposta do servidor vira a thread aberta (`contactData`) — plano 85 · C1.
//
// Esta regra existia TRÊS vezes, copiada, dentro do hook de seleção: no efeito de
// carga da conversa, no `reloadOpenThread` (re-fetch pós-reconexão de WS) e no
// `loadOlder` (página anterior por keyset). As três faziam o mesmo merge do buffer de
// WS + a mesma hidratação das mensagens `failed`, e por isso as correções do plano 85
// tinham que ser aplicadas três vezes — foi exatamente assim que `loadOlder` ficou sem
// guarda de troca de conversa até a A1. Aqui a regra existe uma vez e é testável.
//
// PURO: sem preact, sem DOM, sem rede, sem estado de módulo.

import { mergeBufferedMessages } from './messages.js';

/**
 * Marca as mensagens que o servidor devolve como `failed` com o `_localId`/`_status`
 * que o botão "reenviar" usa — sem isso o retry não funciona depois de um reload.
 *
 * @param {any[]} messages
 * @returns {any[]} nova lista (as mensagens não-failed são preservadas por REFERÊNCIA)
 */
export function hydrateFailed(messages) {
  return (messages || []).map(m =>
    m && m.status === 'failed'
      ? { ...m, _localId: `loaded_${m.ts}`, _status: 'failed' }
      : m);
}

/**
 * Aplica a resposta do servidor à thread: mescla o buffer de mensagens que chegaram
 * por WS antes/durante o fetch (dedup R12 + `supersedes` do plano 57), hidrata os
 * `failed` e CARIMBA a thread de origem (plano 85 A4 — o container só renderiza
 * `contactData` cujo carimbo casa com a seleção corrente).
 *
 * Não muta `data`.
 *
 * @param {any} data - o corpo já normalizado da resposta (`shapeConvData`/`getContact`)
 * @param {any[]} pending - buffer de WS a mesclar (pré-fetch + durante-fetch)
 * @param {string|null} threadKey - `threadKeyOf(...)` da thread que originou o fetch
 * @returns {any}
 */
export function applyThreadResponse(data, pending, threadKey) {
  const out = { ...(data || {}) };
  let msgs = out.messages || [];
  if (pending && pending.length > 0) msgs = mergeBufferedMessages(msgs, pending);
  out.messages = hydrateFailed(msgs);
  out._threadKey = threadKey;
  return out;
}

/**
 * Append da página SEGUINTE (scroll-down numa janela ancorada — plano 99 · F0d).
 *
 * Espelho exato de `prependOlder`, do outro lado: hidrata os `failed`, descarta o
 * que já está carregado (dedup por `_id`) e ANEXA preservando a ordem cronológica.
 *
 * Só mexe em `has_more_newer` — `has_more`/`has_more_older` são deste lado da
 * janela e o servidor não os mede numa leitura `after_id` (ele devolve `true`
 * por construção). Sobrescrevê-los apagaria o que o cliente já sabe sobre o
 * começo do histórico.
 *
 * Não muta `prev`. Devolve `prev` intacto quando não há thread carregada.
 *
 * @param {any} prev - `contactData` atual
 * @param {any[]} newer - mensagens da página seguinte
 * @param {boolean} hasMoreNewer - ainda há página depois desta
 * @param {number|null} newCountAtStart - contador de WS capturado ao iniciar a página
 * @returns {any}
 */
export function appendNewer(prev, newer, hasMoreNewer, newCountAtStart = null) {
  if (!prev) return prev;
  const existing = prev.messages || [];
  const existingIds = new Set(existing.map(m => m._id).filter(v => v != null));
  const fresh = hydrateFailed(newer).filter(m => m._id == null || !existingIds.has(m._id));
  // Se chegarem mensagens por WS ENQUANTO a página está em voo, a resposta pode
  // declarar que chegou ao fim usando um snapshot anterior. Descontamos apenas as
  // mensagens que já existiam quando o GET começou; qualquer delta mantém a janela
  // ancorada para mais uma leitura (que deduplica se a linha já veio na resposta).
  let pending = Math.max(0, Number(prev._newWhileAnchored) || 0);
  if (!hasMoreNewer && newCountAtStart != null) {
    pending = Math.max(0, pending - Math.max(0, Number(newCountAtStart) || 0));
  }
  const out = {
    ...prev,
    messages: [...existing, ...fresh],
    has_more_newer: !!hasMoreNewer || pending > 0,
  };
  if (pending > 0) out._newWhileAnchored = pending;
  else delete out._newWhileAnchored;
  return out;
}

/**
 * Id persistido na borda CRONOLÓGICA da página.
 *
 * O cursor HTTP é um id, mas a ordem real é ``(ts, id)``. Usar ``Math.min/max``
 * escolhe a PK errada quando houve importação/backfill fora de ordem.
 *
 * @param {any[]} messages
 * @param {'older'|'newer'} side
 * @returns {number|null}
 */
export function pageCursorId(messages, side) {
  const list = messages || [];
  if (side === 'newer') {
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i] && list[i]._id != null) return list[i]._id;
    }
    return null;
  }
  for (let i = 0; i < list.length; i++) {
    if (list[i] && list[i]._id != null) return list[i]._id;
  }
  return null;
}

/**
 * A janela está ANCORADA no passado? (plano 99 · F0d)
 *
 * "Ancorada" = a janela carregada NÃO termina na última mensagem da conversa.
 * É a condição que muda três comportamentos do painel: o auto-scroll para o fim
 * é suprimido, um `new_message` deixa de ser anexado (criaria um buraco no
 * histórico) e o botão "voltar ao fim" aparece.
 *
 * Deriva de UM campo do servidor para não haver duas fontes de verdade.
 *
 * @param {any} data - `contactData`
 * @returns {boolean}
 */
export function isAnchored(data) {
  return !!(data && data.has_more_newer);
}

/**
 * Registra uma mensagem que chegou enquanto a visita ao passado já foi pedida,
 * inclusive antes de a resposta ancorada substituir a página recente anterior.
 * Nunca anexa a linha — só mantém a janela aberta e incrementa o contador.
 * @param {any} prev
 * @returns {any}
 */
export function countNewWhileAnchored(prev) {
  if (!prev) return prev;
  return { ...prev, has_more_newer: true,
           _newWhileAnchored: (prev._newWhileAnchored || 0) + 1 };
}

/**
 * Prepend da página ANTERIOR (scroll-up / keyset): hidrata os `failed` e descarta o
 * que já está carregado (dedup por `_id`), preservando a ordem cronológica.
 *
 * Não muta `prev`. Devolve `prev` intacto quando não há thread carregada.
 *
 * @param {any} prev - `contactData` atual
 * @param {any[]} older - mensagens da página anterior
 * @param {boolean} hasMore - ainda há página antes desta
 * @returns {any}
 */
export function prependOlder(prev, older, hasMore) {
  if (!prev) return prev;
  const existing = prev.messages || [];
  const existingIds = new Set(existing.map(m => m._id).filter(v => v != null));
  const fresh = hydrateFailed(older).filter(m => m._id == null || !existingIds.has(m._id));
  // `has_more_older` acompanha o alias `has_more` (plano 99): as duas leituras
  // existem lado a lado durante a transição e não podem divergir.
  return { ...prev, messages: [...fresh, ...existing],
           has_more: !!hasMore, has_more_older: !!hasMore };
}
