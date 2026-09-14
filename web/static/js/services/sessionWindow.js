// Quanto falta para a janela de texto livre do cliente fechar (plano 159).
//
// Módulo PURO: sem `Date.now()` interno, sem DOM, sem rede — `now` é parâmetro. É
// o que torna o comportamento testável (`node --test sessionWindow.test.js`); o
// relógio que faz a faixa contar ao vivo mora no componente, não aqui.
//
// ⚠️ SÃO TRÊS JANELAS no produto e esta trata de UMA só: a do ATENDENTE
// (`session_window_hours` EFETIVO, já com a extensão `human_window_hours` aplicada
// pelo servidor). A da IA (`ai_window_hours`) fecha antes nos canais Meta e tem
// tratamento próprio nos toggles do compositor — derivar uma da outra reintroduz
// bug conhecido. Ver docs/CANAIS_META.md.
//
// ⚠️ O TAMANHO DA JANELA VEM DO SERVIDOR, nunca de um "24" escrito aqui: um canal
// Meta com a tag HUMAN_AGENT ligada dá 7 dias ao atendente. `windowHours` ausente
// (core anterior) e `0` (canal sem janela: GOWA, Telegram) significam a mesma
// coisa para a tela — não exibir nada —, e é por isso que o campo trafega cru.

const HOUR_MS = 3600 * 1000;
const MIN_MS = 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

/** Abaixo disto a faixa fica âmbar (o atendente precisa agir agora). */
export const URGENT_MS = HOUR_MS;

/**
 * Estado da janela do atendente num instante.
 *
 * @param {object} p
 * @param {number|null|undefined} p.lastInboundTs  carimbo do último inbound, em SEGUNDOS
 *   (epoch do backend — `messages.ts`), não milissegundos.
 * @param {number|null|undefined} p.windowHours  tamanho efetivo da janela, em horas.
 * @param {number} p.now  agora, em milissegundos (`Date.now()` do chamador).
 * @returns {{applies: boolean, open: boolean, remainingMs: number, label: string, urgent: boolean}}
 *   `applies:false` ⇒ não há o que exibir (canal sem janela, conversa sem inbound,
 *   core anterior). `open:false` com `applies:true` ⇒ a janela existia e expirou:
 *   a faixa cede lugar ao aviso "Fora da janela" que o compositor já tinha.
 */
export function windowState({ lastInboundTs, windowHours, now }) {
  const none = { applies: false, open: false, remainingMs: 0, label: '', urgent: false };
  const hours = Number(windowHours);
  if (!Number.isFinite(hours) || hours <= 0) return none;
  const ts = Number(lastInboundTs);
  if (!Number.isFinite(ts) || ts <= 0) return none;

  const deadline = ts * 1000 + hours * HOUR_MS;
  const remainingMs = deadline - now;
  if (remainingMs <= 0) {
    return { applies: true, open: false, remainingMs: 0, label: '', urgent: false };
  }
  return {
    applies: true,
    open: true,
    remainingMs,
    label: formatRemaining(remainingMs),
    urgent: remainingMs <= URGENT_MS,
  };
}

/**
 * "2 dias 3h" · "5h 12min" · "48min" · "1min".
 *
 * Duas unidades no máximo: quem lê a faixa quer a ordem de grandeza, não a
 * precisão. Sempre ARREDONDA PARA BAIXO — anunciar mais tempo do que resta é o
 * único erro que custa uma janela perdida.
 */
export function formatRemaining(ms) {
  if (!(ms > 0)) return '';
  if (ms >= DAY_MS) {
    const dias = Math.floor(ms / DAY_MS);
    const horas = Math.floor((ms % DAY_MS) / HOUR_MS);
    const d = `${dias} ${dias === 1 ? 'dia' : 'dias'}`;
    return horas ? `${d} ${horas}h` : d;
  }
  if (ms >= HOUR_MS) {
    const horas = Math.floor(ms / HOUR_MS);
    const mins = Math.floor((ms % HOUR_MS) / MIN_MS);
    return mins ? `${horas}h ${mins}min` : `${horas}h`;
  }
  const mins = Math.floor(ms / MIN_MS);
  return `${Math.max(1, mins)}min`;   // "0min" restante ainda é janela aberta
}
