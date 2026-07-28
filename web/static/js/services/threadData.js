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
  return { ...prev, messages: [...fresh, ...existing], has_more: !!hasMore };
}
