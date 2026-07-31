// @ts-check
//
// A grade de um mês para o "ir para data" do chat — plano 99 · F4.
//
// PURO (sem preact, sem DOM, sem rede) porque a parte que erra em calendário é
// sempre a aritmética, não o desenho: mês que começa no domingo, mês com 28 dias,
// virada de dezembro para janeiro, e — o clássico — a conversão dia → epoch.
//
// ⚠️ FUSO (plano 99 · F3·2): o dia é resolvido no fuso do NAVEGADOR
// (`new Date(ano, mês, dia)`), nunca em UTC. O servidor só compara epoch — ele
// jamais interpreta "dia". É o que mantém a coerência com `formatDateSeparator`
// (components/contacts/utils.js), que também resolve no fuso do navegador: sem
// isso, um operador em fuso diferente do servidor clicaria em "1 de janeiro" e
// aterrissaria no 31 de dezembro.

export const WEEKDAY_LABELS = ['D', 'S', 'T', 'Q', 'Q', 'S', 'S'];

const MONTH_NAMES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
  'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];

/**
 * Epoch em SEGUNDOS do início daquele dia, no fuso do navegador.
 * @param {number} year
 * @param {number} month - 0-11 (como no Date)
 * @param {number} day - 1-31
 * @returns {number}
 */
export function dayStartTs(year, month, day) {
  return Math.floor(new Date(year, month, day, 0, 0, 0, 0).getTime() / 1000);
}

/**
 * "julho de 2026"
 * @param {number} year
 * @param {number} month - 0-11
 */
export function monthLabel(year, month) {
  return `${MONTH_NAMES[month]} de ${year}`;
}

/**
 * Anda `delta` meses, normalizando a virada de ano nos dois sentidos.
 * @param {{year:number, month:number}} cursor
 * @param {number} delta
 * @returns {{year:number, month:number}}
 */
export function shiftMonth({ year, month }, delta) {
  const total = year * 12 + month + delta;
  return { year: Math.floor(total / 12), month: ((total % 12) + 12) % 12 };
}

/**
 * A grade do mês: 6 linhas de 7 células, começando no domingo.
 *
 * As células fora do mês vêm com `day: null` (em vez dos dias do mês vizinho):
 * clicar num dia de outro mês é sempre engano do dedo, e apagar a célula é mais
 * honesto do que aceitar o clique e saltar para longe do que se estava olhando.
 *
 * `disabled` marca o dia posterior a `maxTs` (o "agora"): não existe conversa no
 * futuro, e um dia clicável que nunca leva a lugar nenhum é uma promessa falsa.
 *
 * @param {number} year
 * @param {number} month - 0-11
 * @param {{maxTs?: number|null}} [opts]
 * @returns {Array<Array<{day:number|null, ts:number|null, disabled:boolean}>>}
 */
export function monthGrid(year, month, { maxTs = null } = {}) {
  const firstWeekday = new Date(year, month, 1).getDay();     // 0=domingo
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const day = i - firstWeekday + 1;
    if (day < 1 || day > daysInMonth) {
      cells.push({ day: null, ts: null, disabled: true });
      continue;
    }
    const ts = dayStartTs(year, month, day);
    cells.push({ day, ts, disabled: maxTs != null && ts > maxTs });
  }
  const weeks = [];
  for (let w = 0; w < 6; w++) weeks.push(cells.slice(w * 7, w * 7 + 7));
  return weeks;
}

/**
 * O cursor inicial do calendário: o mês da data de referência (a última mensagem
 * carregada, ou hoje). Abrir sempre em "hoje" faria o operador de uma conversa
 * antiga navegar meses para trás toda vez.
 *
 * @param {number|null} refTs - epoch em SEGUNDOS
 * @param {number} [nowTs] - injetável para teste
 * @returns {{year:number, month:number}}
 */
export function initialCursor(refTs, nowTs = Date.now() / 1000) {
  const d = new Date((refTs || nowTs) * 1000);
  return { year: d.getFullYear(), month: d.getMonth() };
}

/**
 * Já está no mês de `maxTs`? (o botão "próximo mês" para aqui)
 * @param {{year:number, month:number}} cursor
 * @param {number} maxTs - epoch em SEGUNDOS
 */
export function atLastMonth({ year, month }, maxTs) {
  const d = new Date(maxTs * 1000);
  return year === d.getFullYear() && month === d.getMonth();
}
