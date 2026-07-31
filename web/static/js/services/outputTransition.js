// Plano 99 — serializa uma saída confirmada com a troca da janela ancorada
// para a ponta recente. PURO: sem Preact/DOM, para cobrir a corrida em teste.

/**
 * @param {boolean} sent - o POST/ACK terminou com sucesso
 * @param {boolean} anchored - a janela ainda termina antes da ponta recente
 * @param {Function|null} backToBottom - carrega a janela mais recente
 * @param {{current: Promise<any>|null}} inFlightRef - coalescedor compartilhado
 * @returns {Promise<boolean>} preserva o resultado do envio
 */
export async function transitionAfterOutput(sent, anchored, backToBottom, inFlightRef) {
  if (!sent || !anchored || typeof backToBottom !== 'function') return !!sent;
  if (!inFlightRef.current) {
    const task = Promise.resolve().then(() => backToBottom()).finally(() => {
      if (inFlightRef.current === task) inFlightRef.current = null;
    });
    inFlightRef.current = task;
  }
  try { await inFlightRef.current; } catch (_) { /* o envio já confirmou */ }
  return true;
}
