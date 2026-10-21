// node --test web/static/js/services/aiReactivation.test.js
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULTS, MAX_VALUE, OWNERS, UNITS, WEEKDAYS, browserTimeZone, clampValue,
  describeRule, fromConfig, toPayload, toggleWeekday,
} from './aiReactivation.js';

// ── fromConfig ──────────────────────────────────────────────────────────────

test('fromConfig: resposta vazia cai nos defaults', () => {
  const f = fromConfig({});
  assert.equal(f.enabled, false);
  assert.equal(f.unit, 'hours');
  assert.equal(f.value, 24);
  assert.deepEqual(f.weekdays, [0, 1, 2, 3, 4]);
  assert.equal(f.time, '08:00');
  assert.equal(f.lastSender, 'both');
  assert.equal(f.includeGroupsArchived, false);
});

test('fromConfig: null/undefined não quebram (core antigo sem as chaves)', () => {
  assert.deepEqual(fromConfig(null), fromConfig({}));
  assert.deepEqual(fromConfig(undefined), fromConfig({}));
});

test('fromConfig: lê uma regra salva', () => {
  const f = fromConfig({
    ai_reactivation_enabled: true, ai_reactivation_unit: 'schedule',
    ai_reactivation_value: 3, ai_reactivation_weekdays: [5, 1, 1],
    ai_reactivation_time: '09:30', ai_reactivation_last_sender: 'customer',
    ai_reactivation_include_groups_archived: true,
  });
  assert.equal(f.enabled, true);
  assert.equal(f.unit, 'schedule');
  assert.equal(f.value, 3);
  assert.deepEqual(f.weekdays, [1, 5]);
  assert.equal(f.time, '09:30');
  assert.equal(f.lastSender, 'customer');
  assert.equal(f.includeGroupsArchived, true);
});

test('fromConfig: lixo cai no default POR CAMPO, sem derrubar o resto', () => {
  const f = fromConfig({
    ai_reactivation_enabled: 'sim', ai_reactivation_unit: 'semanas',
    ai_reactivation_time: '25:00', ai_reactivation_last_sender: 'ia',
    ai_reactivation_weekdays: [9, -1, 'x', 3], ai_reactivation_value: 7,
  });
  assert.equal(f.enabled, false);
  assert.equal(f.unit, 'hours');
  assert.equal(f.time, '08:00');
  assert.equal(f.lastSender, 'both');
  assert.deepEqual(f.weekdays, [3]);
  assert.equal(f.value, 7);
});

// ── clampValue ──────────────────────────────────────────────────────────────

test('clampValue: limita em vez de rejeitar', () => {
  assert.equal(clampValue(24), 24);
  assert.equal(clampValue('7'), 7);
  assert.equal(clampValue(0), 1);
  assert.equal(clampValue(-5), 1);
  assert.equal(clampValue(10 ** 9), MAX_VALUE);
});

test('clampValue: vazio/lixo cai no default, nunca NaN', () => {
  for (const junk of ['', 'abc', null, undefined, NaN]) {
    assert.equal(clampValue(junk), DEFAULTS.value);
  }
});

// ── toggleWeekday ───────────────────────────────────────────────────────────

test('toggleWeekday: liga e desliga mantendo ordem e sem repetição', () => {
  assert.deepEqual(toggleWeekday([0, 2], 1), [0, 1, 2]);
  assert.deepEqual(toggleWeekday([0, 1, 2], 1), [0, 2]);
  assert.deepEqual(toggleWeekday([], 6), [6]);
  assert.deepEqual(toggleWeekday([6], 6), []);
});

test('toggleWeekday: não muta o array de entrada', () => {
  const input = [0, 2];
  toggleWeekday(input, 1);
  assert.deepEqual(input, [0, 2]);
});

// ── describeRule: a bifurcação tempo vs agenda ──────────────────────────────

test('describeRule: tempo mostra o valor e esconde dias/horário', () => {
  for (const unit of ['minutes', 'hours', 'days']) {
    const r = describeRule({ ...fromConfig({}), unit });
    assert.equal(r.scheduled, false);
    assert.equal(r.showValue, true);
    assert.equal(r.showWeekdays, false);
    assert.equal(r.showTime, false);
    assert.equal(r.emptySchedule, false);
  }
});

test('describeRule: agenda mostra dias/horário e esconde o valor', () => {
  const r = describeRule({ ...fromConfig({}), unit: 'schedule' });
  assert.equal(r.scheduled, true);
  assert.equal(r.showValue, false);
  assert.equal(r.showWeekdays, true);
  assert.equal(r.showTime, true);
});

test('describeRule: agenda sem nenhum dia é sinalizada (nunca dispara)', () => {
  const r = describeRule({ ...fromConfig({}), unit: 'schedule', weekdays: [] });
  assert.equal(r.emptySchedule, true);
  // …mas só na agenda: no modo tempo os dias nem existem.
  assert.equal(describeRule({ ...fromConfig({}), unit: 'hours', weekdays: [] }).emptySchedule, false);
});

// ── toPayload ───────────────────────────────────────────────────────────────

test('toPayload: manda as 8 chaves e o fuso do navegador', () => {
  const body = toPayload(fromConfig({}), 'America/Sao_Paulo');
  assert.deepEqual(Object.keys(body).sort(), [
    'ai_reactivation_enabled', 'ai_reactivation_include_groups_archived',
    'ai_reactivation_last_sender', 'ai_reactivation_time', 'ai_reactivation_timezone',
    'ai_reactivation_unit', 'ai_reactivation_value', 'ai_reactivation_weekdays',
  ]);
  assert.equal(body.ai_reactivation_timezone, 'America/Sao_Paulo');
});

test('toPayload: desmarcar "grupos e arquivadas" grava false (não omite a chave)', () => {
  const body = toPayload({ ...fromConfig({}), includeGroupsArchived: false }, 'UTC');
  assert.equal(body.ai_reactivation_include_groups_archived, false);
});

test('toPayload: ida e volta é estável (fromConfig ∘ toPayload)', () => {
  const form = fromConfig({
    ai_reactivation_enabled: true, ai_reactivation_unit: 'schedule',
    ai_reactivation_value: 5, ai_reactivation_weekdays: [6, 0],
    ai_reactivation_time: '21:05', ai_reactivation_last_sender: 'attendant',
    ai_reactivation_include_groups_archived: true,
  });
  assert.deepEqual(fromConfig(toPayload(form, 'UTC')), form);
});

test('toPayload: valor ruim vira inteiro válido, hora ruim vira o default', () => {
  const body = toPayload({ ...fromConfig({}), value: '', time: '99:99' }, 'UTC');
  assert.equal(body.ai_reactivation_value, DEFAULTS.value);
  assert.equal(body.ai_reactivation_time, DEFAULTS.time);
});

test('toPayload: valor numérico ruim não vira NaN (JSON.stringify(NaN) = null)', () => {
  const body = toPayload({ ...fromConfig({}), value: 'abc' }, 'UTC');
  assert.ok(Number.isInteger(body.ai_reactivation_value));
  assert.doesNotMatch(JSON.stringify(body), /null/);
});

test('toPayload: dias saem ordenados', () => {
  const body = toPayload({ ...fromConfig({}), weekdays: [4, 0, 2] }, 'UTC');
  assert.deepEqual(body.ai_reactivation_weekdays, [0, 2, 4]);
});

// ── tabelas e fuso ──────────────────────────────────────────────────────────

test('as tabelas casam com as listas do servidor', () => {
  assert.deepEqual(UNITS.map((u) => u.id), ['minutes', 'hours', 'days', 'schedule']);
  assert.deepEqual(OWNERS.map((o) => o.id), ['both', 'customer', 'attendant']);
  assert.deepEqual(WEEKDAYS.map((d) => d.id), [0, 1, 2, 3, 4, 5, 6]);
});

test('browserTimeZone: devolve uma string (vazia se o navegador não souber)', () => {
  assert.equal(typeof browserTimeZone(), 'string');
});
