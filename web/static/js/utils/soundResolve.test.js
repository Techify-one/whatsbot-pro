// Run with: node --test web/static/js/utils/soundResolve.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveEvent, CODE_SEEDS, clamp01, pref } from './soundResolve.js';

const dev = (o = {}) => ({ masterOn: true, volumeMult: 1, ...o });

test('code-seed fallback: sem global nem user, cai no seed', () => {
  const r = resolveEvent('new_message', { device: dev() });
  assert.equal(r.play, true);
  assert.equal(r.soundId, 'ding');
  assert.equal(r.volume, 0.6);
});

test('mention seed = chime 0.8', () => {
  const r = resolveEvent('mention', { device: dev() });
  assert.equal(r.soundId, 'chime');
  assert.equal(r.volume, 0.8);
});

test('merge esparso: user sobrescreve só o volume; som cai no global', () => {
  const global = { events: { new_message: { enabled: true, sound: 'blip', volume: 0.5 } } };
  const user = { events: { new_message: { volume: 0.2 } } };
  const r = resolveEvent('new_message', { global, user, device: dev() });
  assert.equal(r.soundId, 'blip');   // do global
  assert.equal(r.volume, 0.2);       // do user
});

test('precedência: user > global > seed', () => {
  assert.equal(pref('u', 'g', 's'), 'u');
  assert.equal(pref(undefined, 'g', 's'), 'g');
  assert.equal(pref(undefined, undefined, 's'), 's');
  assert.equal(pref(false, 'g', 's'), false);  // false é valor, não "ausente"
});

test('multiplicador de volume per-device é aplicado', () => {
  const r = resolveEvent('new_message', { device: dev({ volumeMult: 0.5 }) });
  assert.equal(r.volume, 0.3);  // 0.6 * 0.5
});

test('device mute (masterOn=false) silencia tudo', () => {
  const r = resolveEvent('new_message', { device: dev({ masterOn: false }) });
  assert.equal(r.play, false);
});

test('master sincronizado OFF (user) silencia', () => {
  const r = resolveEvent('new_message', { user: { master_enabled: false }, device: dev() });
  assert.equal(r.play, false);
});

test('master global OFF silencia; user pode reativar', () => {
  const global = { master_enabled: false, events: {} };
  assert.equal(resolveEvent('new_message', { global, device: dev() }).play, false);
  const user = { master_enabled: true };
  assert.equal(resolveEvent('new_message', { global, user, device: dev() }).play, true);
});

test('evento enabled=false (user) silencia', () => {
  const user = { events: { new_message: { enabled: false } } };
  assert.equal(resolveEvent('new_message', { user, device: dev() }).play, false);
});

test('sound="none" silencia explicitamente', () => {
  const user = { events: { new_message: { sound: 'none' } } };
  assert.equal(resolveEvent('new_message', { user, device: dev() }).play, false);
});

test('transferência: enabledOverride do servidor tem precedência (silenciar)', () => {
  // Mesmo com seed enabled=true, o servidor pode silenciar.
  const r = resolveEvent('ia_to_human', { device: dev(), enabledOverride: false });
  assert.equal(r.play, false);
});

test('transferência: durationOverride do servidor vence o seed', () => {
  const r = resolveEvent('ia_to_human', { device: dev(), durationOverride: 12 });
  assert.equal(r.play, true);
  assert.equal(r.duration, 12);
  assert.equal(r.soundId, 'siren');
});

test('transferência: enabledOverride=true não ressuscita evento que o usuário desligou', () => {
  // O gate do servidor é um AND, não um override: o atendente pode silenciar
  // para si mesmo mesmo com o alerta ligado no padrão da equipe.
  const user = { events: { ia_to_human: { enabled: false } } };
  const r = resolveEvent('ia_to_human', { user, device: dev(), enabledOverride: true });
  assert.equal(r.play, false);
});

test('transferência: duração do usuário vence o durationOverride do servidor', () => {
  const user = { events: { assigned_to_me: { duration: 9 } } };
  const r = resolveEvent('assigned_to_me', { user, device: dev(), durationOverride: 12 });
  assert.equal(r.play, true);
  assert.equal(r.duration, 9);
});

test('transferência: duração do padrão da equipe vence o durationOverride legado', () => {
  const global = { events: { assigned_to_me: { duration: 8 } } };
  const r = resolveEvent('assigned_to_me', { global, device: dev(), durationOverride: 12 });
  assert.equal(r.duration, 8);
});

test('transferência: sem override, duração cai no seed (5s)', () => {
  const r = resolveEvent('assigned_to_me', { device: dev() });
  assert.equal(r.duration, 5);
});

test('volume clampado a 0..1', () => {
  const user = { events: { new_message: { volume: 5 } } };
  assert.equal(resolveEvent('new_message', { user, device: dev() }).volume, 1);
  assert.equal(clamp01(-3), 0);
  assert.equal(clamp01('x'), 0);
});

test('volume efetivo 0 (mult 0) não toca', () => {
  const r = resolveEvent('new_message', { device: dev({ volumeMult: 0 }) });
  assert.equal(r.play, false);
});

test('evento desconhecido → seed vazio → não toca', () => {
  const r = resolveEvent('inexistente', { device: dev() });
  assert.equal(r.play, false);
});

test('CODE_SEEDS cobre os 4 eventos reais', () => {
  assert.deepEqual(
    Object.keys(CODE_SEEDS).sort(),
    ['assigned_to_me', 'ia_to_human', 'mention', 'new_message'],
  );
});
