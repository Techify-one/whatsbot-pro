// node --test storages/plugins/protocolos/static/proto_fields.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { seedProtocolValues, mergeSeed, isMultiDef } from './proto_fields.js';

const ATENDENTE = { key: 'atendente', label: 'Atendente', type: 'atendente', required: true };
const OBS = { key: 'obs', label: 'Observações', type: 'textarea' };

// ── caracterização do bug (plano 88) ────────────────────────────────────────
test('sem defs não semeia nada — é a corrida que o gate/efeito fecham', () => {
  const vals = seedProtocolValues([], { id: 1, assignee_user_id: null }, { defaultAssignee: 7 });
  assert.deepEqual(vals, {});
});

// ── atendente ───────────────────────────────────────────────────────────────
test('protocolo sem atendente semeia o usuário logado', () => {
  const vals = seedProtocolValues([ATENDENTE], { assignee_user_id: null }, { defaultAssignee: 7 });
  // O valor é o id STRINGIFICADO — o AttendantSelect compara via String(value) e
  // devolve Number no onChange. Comportamento preservado da versão inline.
  assert.equal(vals.atendente, '7');
});

test('atendente já salvo NÃO é sobrescrito pelo usuário logado', () => {
  const vals = seedProtocolValues([ATENDENTE], { assignee_user_id: 3 }, { defaultAssignee: 7 });
  assert.equal(vals.atendente, '3');
});

test('readOnly não semeia o usuário logado', () => {
  const vals = seedProtocolValues([ATENDENTE], { assignee_user_id: null },
    { defaultAssignee: 7, readOnly: true });
  assert.equal(vals.atendente, '');
});

test('readOnly ainda mostra o atendente salvo', () => {
  const vals = seedProtocolValues([ATENDENTE], { assignee_user_id: 3 },
    { defaultAssignee: 7, readOnly: true });
  assert.equal(vals.atendente, '3');
});

test('sem usuário logado e sem assignee → vazio (nunca undefined)', () => {
  const vals = seedProtocolValues([ATENDENTE], {}, {});
  assert.equal(vals.atendente, '');
});

// ── tipos ───────────────────────────────────────────────────────────────────
test('checkbox vira booleano (inclui o legado em string)', () => {
  const def = [{ key: 'ok', type: 'checkbox' }];
  assert.equal(seedProtocolValues(def, { fields: { ok: 'true' } }).ok, true);
  assert.equal(seedProtocolValues(def, { fields: { ok: true } }).ok, true);
  assert.equal(seedProtocolValues(def, { fields: {} }).ok, false);
});

test('multi (checkboxes) vem de CSV ou array', () => {
  const def = [{ key: 'tipos', type: 'checkboxes' }];
  assert.deepEqual(seedProtocolValues(def, { fields: { tipos: 'a, b ,c' } }).tipos, ['a', 'b', 'c']);
  assert.deepEqual(seedProtocolValues(def, { fields: { tipos: ['a'] } }).tipos, ['a']);
  assert.deepEqual(seedProtocolValues(def, { fields: {} }).tipos, []);
});

test('select multiple também é multi; select simples é string', () => {
  assert.ok(isMultiDef({ type: 'select', multiple: true }));
  assert.ok(!isMultiDef({ type: 'select' }));
  const vals = seedProtocolValues(
    [{ key: 'm', type: 'select', multiple: true }, { key: 's', type: 'select' }],
    { fields: { m: 'x,y', s: 'x' } });
  assert.deepEqual(vals.m, ['x', 'y']);
  assert.equal(vals.s, 'x');
});

test('campos comuns viram string; nulo vira vazio', () => {
  const vals = seedProtocolValues([OBS, { key: 'n', type: 'number' }],
    { fields: { n: 12 } });
  assert.equal(vals.obs, '');
  assert.equal(vals.n, '12');
});

test('def sem key é ignorada (defesa contra config corrompida)', () => {
  assert.deepEqual(seedProtocolValues([null, {}, { key: 'a', type: 'text' }], {}), { a: '' });
});

// ── mergeSeed ───────────────────────────────────────────────────────────────
test('mergeSeed preenche só o que falta', () => {
  assert.deepEqual(mergeSeed({ resultado: 'X' }, { resultado: 'Y', atendente: '7' }),
    { resultado: 'X', atendente: '7' });
});

test('mergeSeed não ressuscita campo limpo de propósito', () => {
  // Operador escolheu "Não atribuído" (chave existe com '') → o seed não toca.
  assert.deepEqual(mergeSeed({ atendente: '' }, { atendente: '7' }), { atendente: '' });
  assert.deepEqual(mergeSeed({ tipos: [] }, { tipos: ['a'] }), { tipos: [] });
  assert.deepEqual(mergeSeed({ ok: false }, { ok: true }), { ok: false });
});

test('mergeSeed é idempotente e devolve a MESMA referência quando não há o que somar', () => {
  const cur = { a: '1' };
  assert.equal(mergeSeed(cur, { a: '2' }), cur);        // mesma referência → sem re-render
  const once = mergeSeed(cur, { a: '2', b: '' });
  assert.equal(mergeSeed(once, { a: '2', b: '' }), once);
});

test('mergeSeed tolera current nulo', () => {
  assert.deepEqual(mergeSeed(null, { a: '1' }), { a: '1' });
  assert.deepEqual(mergeSeed(undefined, {}), {});
});
