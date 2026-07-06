// Barramento global e leve de notificações transitórias (toasts).
//
// Desacoplado do Preact de propósito: qualquer camada (services/httpClient, a
// api dos plugins, um componente) pode EMITIR um aviso sem importar componente
// nenhum. O único assinante que RENDERIZA é o <Toaster/> (montado uma vez na
// raiz do app — ver components/shell/Toaster.js).
//
// Deduplica mensagens idênticas dentro de uma janela curta: uma rajada do mesmo
// erro (ex.: vários 403 "Permissão negada." no carregamento de uma tela) vira UM
// único toast, não N. Hoje o principal produtor é o caminho de 403 em
// services/httpClient.js e na api dos plugins.

let _seq = 0;
const _subs = new Set();
const _recent = new Map();          // message -> epoch(ms) do último emit
const DEDUPE_MS = 4000;
const PRUNE_MS = 60000;

function _now() {
  try { return Date.now(); } catch (_) { return 0; }
}

function _prune(now) {
  for (const [msg, ts] of _recent) if (now - ts > PRUNE_MS) _recent.delete(msg);
}

/**
 * Assina o barramento. `fn` recebe cada toast `{id, message, kind, timeoutMs}`.
 * @param {(toast: {id:number, message:string, kind:string, timeoutMs:number}) => void} fn
 * @returns {() => void} unsubscribe
 */
export function subscribe(fn) {
  _subs.add(fn);
  return () => { _subs.delete(fn); };
}

/**
 * Emite um aviso. Deduplica mensagens iguais dentro de `dedupeMs`.
 * @param {string} message
 * @param {{kind?: 'error'|'info'|'success', timeoutMs?: number, dedupeMs?: number}} [opts]
 * @returns {void}
 */
export function notify(message, { kind = 'info', timeoutMs = 5000, dedupeMs = DEDUPE_MS } = {}) {
  const msg = String(message == null ? '' : message).trim();
  if (!msg) return;
  const now = _now();
  _prune(now);
  const last = _recent.get(msg);
  if (last != null && (now - last) < dedupeMs) return;   // engole a rajada
  _recent.set(msg, now);
  const toast = { id: ++_seq, message: msg, kind, timeoutMs };
  for (const fn of _subs) { try { fn(toast); } catch (_) { /* ignore */ } }
}

/**
 * Atalho para "Permissão negada." — sempre `kind:'error'`. Usado pelo transporte
 * (core e plugin) ao receber um HTTP 403 do back-end.
 * @param {string} [message]
 * @returns {void}
 */
export function notifyPermissionDenied(message) {
  notify(message || 'Você não tem permissão para realizar esta ação.', { kind: 'error' });
}
