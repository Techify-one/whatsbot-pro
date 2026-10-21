// @ts-check
//
// Religar a IA automaticamente (plano 178) — a parte PURA do formulário do card
// "Automações" em Configurações de IA.
//
// Por que existe: o card tem uma bifurcação (regra por TEMPO parado vs. por AGENDA) e
// cada ramo mostra campos diferentes e manda chaves diferentes. Decidir isso inline no
// componente deixa a bifurcação sem teste — e o erro que ela custa é silencioso (a
// tela diz "08:00" e o servidor recebe lixo, que o PUT descarta sem avisar). Aqui tudo
// é PURO — sem preact, sem DOM — no mesmo espírito de `audioScrub.js` /
// `composerSubmit.js`: o componente entrega o estado e recebe o corpo do PUT.
//
// Dias da semana seguem o servidor: 0 = segunda … 6 = domingo.
//
// ⚠️ O fuso NÃO é decisão do servidor. "08:00" é lido no fuso de QUEM SALVOU (o
// navegador), porque um container costuma estar em UTC e "08:00" viraria 05:00 em
// Brasília. `browserTimeZone()` é o único lugar que pergunta isso ao navegador.

export const UNITS = [
  { id: 'minutes', label: 'Minutos sem mensagens' },
  { id: 'hours', label: 'Horas sem mensagens' },
  { id: 'days', label: 'Dias sem mensagens' },
  { id: 'schedule', label: 'Dias da semana e horário' },
];

export const OWNERS = [
  { id: 'both', label: 'Cliente ou atendente (qualquer um)' },
  { id: 'customer', label: 'Cliente' },
  { id: 'attendant', label: 'Usuário/atendente' },
];

export const WEEKDAYS = [
  { id: 0, label: 'Seg', full: 'segunda-feira' },
  { id: 1, label: 'Ter', full: 'terça-feira' },
  { id: 2, label: 'Qua', full: 'quarta-feira' },
  { id: 3, label: 'Qui', full: 'quinta-feira' },
  { id: 4, label: 'Sex', full: 'sexta-feira' },
  { id: 5, label: 'Sáb', full: 'sábado' },
  { id: 6, label: 'Dom', full: 'domingo' },
];

/** Unidade no rótulo do campo de tempo. */
export const VALUE_HINT = { minutes: 'minutos', hours: 'horas', days: 'dias' };

export const MAX_VALUE = 100000;

/** O mesmo que o servidor usa quando a chave não existe (config/settings.py). */
export const DEFAULTS = Object.freeze({
  enabled: false,
  unit: 'hours',
  value: 24,
  weekdays: [0, 1, 2, 3, 4],
  time: '08:00',
  lastSender: 'both',
  includeGroupsArchived: false,
});

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** @param {unknown} v @param {{id: string}[]} list @param {string} fallback */
function oneOf(v, list, fallback) {
  return list.some((o) => o.id === v) ? /** @type {string} */ (v) : fallback;
}

/**
 * Estado do formulário a partir do `GET /api/config`. O servidor já normaliza, mas a
 * tela não confia nisso: resposta incompleta (core mais antigo, sem as chaves) cai no
 * default por campo em vez de renderizar `undefined`.
 * @param {Record<string, any> | null | undefined} cfg
 */
export function fromConfig(cfg) {
  const c = cfg || {};
  const weekdays = Array.isArray(c.ai_reactivation_weekdays)
    ? c.ai_reactivation_weekdays.filter((d) => Number.isInteger(d) && d >= 0 && d <= 6)
    : DEFAULTS.weekdays;
  return {
    enabled: c.ai_reactivation_enabled === true,
    unit: oneOf(c.ai_reactivation_unit, UNITS, DEFAULTS.unit),
    value: c.ai_reactivation_value ?? DEFAULTS.value,
    weekdays: [...new Set(weekdays)].sort((a, b) => a - b),
    time: typeof c.ai_reactivation_time === 'string' && HHMM.test(c.ai_reactivation_time)
      ? c.ai_reactivation_time : DEFAULTS.time,
    lastSender: oneOf(c.ai_reactivation_last_sender, OWNERS, DEFAULTS.lastSender),
    includeGroupsArchived: c.ai_reactivation_include_groups_archived === true,
  };
}

/** Inteiro ≥ 1 limitado a MAX_VALUE; vazio/lixo cai no default (nunca NaN ao servidor).
 * @param {unknown} raw */
export function clampValue(raw) {
  const n = parseInt(String(raw), 10);
  if (!Number.isFinite(n)) return DEFAULTS.value;
  return Math.min(Math.max(n, 1), MAX_VALUE);
}

/** Liga/desliga um dia, mantendo a lista ordenada e sem repetição.
 * @param {number[]} days @param {number} id */
export function toggleWeekday(days, id) {
  const set = new Set(days);
  if (set.has(id)) set.delete(id); else set.add(id);
  return [...set].sort((a, b) => a - b);
}

/** O navegador é quem diz o fuso (ver nota no topo). Vazio = o servidor usa o dele.
 * @returns {string} */
export function browserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
}

/**
 * O que a regra ESCOLHIDA exige do formulário, para a tela mostrar só o que vale e
 * avisar antes de salvar uma agenda que nunca dispara.
 * @param {ReturnType<typeof fromConfig>} form
 */
export function describeRule(form) {
  const scheduled = form.unit === 'schedule';
  return {
    scheduled,
    showWeekdays: scheduled,
    showTime: scheduled,
    showValue: !scheduled,
    // Agenda sem nenhum dia marcado é uma regra que nunca dispara: avisa em vez de
    // deixar o operador achar que está ativa.
    emptySchedule: scheduled && form.weekdays.length === 0,
  };
}

/**
 * Corpo do PUT. Manda SEMPRE as 8 chaves (o PUT da tela é parcial por conjunto de
 * chaves que ela possui) — assim desmarcar "grupos e arquivadas" também grava.
 * @param {ReturnType<typeof fromConfig>} form
 * @param {string} [timeZone] fuso do navegador (injetável para teste)
 */
export function toPayload(form, timeZone = browserTimeZone()) {
  return {
    ai_reactivation_enabled: form.enabled === true,
    ai_reactivation_unit: oneOf(form.unit, UNITS, DEFAULTS.unit),
    ai_reactivation_value: clampValue(form.value),
    ai_reactivation_weekdays: [...form.weekdays].sort((a, b) => a - b),
    ai_reactivation_time: HHMM.test(form.time) ? form.time : DEFAULTS.time,
    ai_reactivation_last_sender: oneOf(form.lastSender, OWNERS, DEFAULTS.lastSender),
    ai_reactivation_include_groups_archived: form.includeGroupsArchived === true,
    ai_reactivation_timezone: timeZone,
  };
}
