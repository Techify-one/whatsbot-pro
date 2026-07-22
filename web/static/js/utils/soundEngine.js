/**
 * soundEngine — motor unificado de sons de notificação (plano 63 F2).
 *
 * Um único módulo toca sons SINTETIZADOS (Web Audio) para os 4 eventos do
 * WhatsBot, resolvendo 3 camadas de preferência (fail-open):
 *
 *   efetivo[evento][campo] = override_do_usuário ?? padrão_global_admin ?? code_seed
 *   volume_real            = volume_efetivo × device_volume_mult   (multiplicador local)
 *
 * - Camada POR-USUÁRIO e GLOBAL vêm de `GET /api/me/sound-prefs` (cacheadas em
 *   `_user`/`_global`; recarregar com `reloadPrefs()`).
 * - Camada POR-DISPOSITIVO: master = `whatsbot_notif_sound` (o mesmo interruptor
 *   per-device reexposto na F0 / na tela "Notificações e sons"); multiplicador de
 *   volume = `whatsbot_sound_prefs_v1.device_volume_mult`. Lidas do localStorage a
 *   CADA disparo (sem cache) — refletem mudança na hora, entre abas.
 *
 * Corrige o vazamento de AudioContext do `alertSound.js` (1 contexto por disparo):
 * usa um SINGLETON lazy + `resume()`. Volume unificado cobre TAMBÉM a sirene (antes
 * gain fixo 0.3). `resolveEvent` é uma função PURA (testável via node --test).
 *
 * Os IDs de som casam com `server/sound_catalog.py` (fonte dos metadados da tela).
 */

import { authHeaders } from '../services/api.js';
import { resolveEvent, clamp01 as _clamp01, CODE_SEEDS } from './soundResolve.js';

// Re-exporta a resolução pura (núcleo em `soundResolve.js`, sem deps de browser).
export { resolveEvent, CODE_SEEDS };

// Classe do evento (§6e): notification = one-shot; alert = sustained (repete por
// `duration` segundos). Estático — casa com o `duration_applies` do catálogo.
const EVENT_CLASS = {
  new_message: 'notification',
  mention: 'notification',
  ia_to_human: 'alert',
  assigned_to_me: 'alert',
};

const DEVICE_KEY = 'whatsbot_sound_prefs_v1';
const DEVICE_MASTER_KEY = 'whatsbot_notif_sound';  // per-device master (F0/legacy)
const THROTTLE_MS = 300;

// ── Estado de runtime (prefs cacheadas) ────────────────────────────────────────
let _global = null;
let _user = null;
let _catalog = null;
const _lastPlay = {};

export function getCatalog() { return _catalog; }
export function getGlobalDefault() { return _global; }
export function getUserPrefs() { return _user; }

/** (Re)carrega override do usuário + padrão global + catálogo do servidor. */
export async function reloadPrefs() {
  try {
    const res = await fetch('/api/me/sound-prefs', { headers: authHeaders() });
    const j = await res.json();
    if (j && j.ok && j.data) {
      _user = j.data.prefs || {};
      _global = j.data.global_default || null;
      _catalog = j.data.catalog || null;
    }
  } catch (_) { /* fail-open: segue com code-seeds */ }
  return { user: _user, global: _global, catalog: _catalog };
}

// ── Camada por-dispositivo (localStorage, lida a cada disparo) ──────────────────
function _deviceMasterOn() {
  try {
    const v = localStorage.getItem(DEVICE_MASTER_KEY);
    return v === null ? true : v === '1';  // default ON (plano 63 — master padrão ligado)
  } catch (_) { return true; }
}

export function getDeviceVolumeMult() {
  try {
    const raw = JSON.parse(localStorage.getItem(DEVICE_KEY) || '{}');
    if (typeof raw.device_volume_mult === 'number' && isFinite(raw.device_volume_mult)) {
      return _clamp01(raw.device_volume_mult);
    }
  } catch (_) { /* ignore */ }
  return 1;
}

export function setDeviceVolumeMult(v) {
  let raw = {};
  try { raw = JSON.parse(localStorage.getItem(DEVICE_KEY) || '{}'); } catch (_) { raw = {}; }
  raw.version = 1;
  raw.device_volume_mult = _clamp01(v);
  try {
    localStorage.setItem(DEVICE_KEY, JSON.stringify(raw));
    window.dispatchEvent(new Event('whatsbot:notif-prefs'));
  } catch (_) { /* ignore */ }
}

// ── Web Audio: contexto singleton + síntese ────────────────────────────────────
let _ctx = null;
function _audioCtx() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!_ctx) _ctx = new Ctx();
    if (_ctx.state === 'suspended') { try { _ctx.resume(); } catch (_) {} }
    return _ctx;
  } catch (_) { return null; }
}

/** Um "beep": osc + gain com ramp exponencial (piso 0.0001 p/ não estourar). */
function _beep(ac, { freq, type = 'sine', start, dur, peak }) {
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  const p = Math.max(0.0001, peak);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(p, start + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  osc.connect(gain);
  gain.connect(ac.destination);
  osc.start(start);
  osc.stop(start + dur + 0.02);
}

// Receitas: cada `unit(ac, t0, vol)` toca UMA vez a partir de t0 e devolve o
// comprimento do ciclo (segundos) — usado para repetir sons de ALERTA.
const SOUNDS = {
  ding: { cls: 'once', unit(ac, t, vol) {
    [[784, 0], [1047, 0.11]].forEach(([f, o]) => _beep(ac, { freq: f, type: 'sine', start: t + o, dur: 0.3, peak: 0.25 * vol }));
    return 0.45;
  } },
  chime: { cls: 'once', unit(ac, t, vol) {
    [[659, 0], [880, 0.09], [1175, 0.18]].forEach(([f, o]) => _beep(ac, { freq: f, type: 'sine', start: t + o, dur: 0.28, peak: 0.18 * vol }));
    return 0.5;
  } },
  blip: { cls: 'once', unit(ac, t, vol) {
    _beep(ac, { freq: 1000, type: 'sine', start: t, dur: 0.12, peak: 0.22 * vol });
    return 0.2;
  } },
  soft: { cls: 'once', unit(ac, t, vol) {
    [[440, 0], [554, 0.1]].forEach(([f, o]) => _beep(ac, { freq: f, type: 'sine', start: t + o, dur: 0.32, peak: 0.16 * vol }));
    return 0.45;
  } },
  pulse: { cls: 'alert', unit(ac, t, vol) {
    _beep(ac, { freq: 660, type: 'triangle', start: t, dur: 0.16, peak: 0.2 * vol });
    return 0.55;  // beep + gap
  } },
  siren: { cls: 'alert', _i: 0, unit(ac, t, vol) {
    const freqs = [880, 660];
    _beep(ac, { freq: freqs[(this._i++) % 2], type: 'square', start: t, dur: 0.3, peak: 0.3 * vol });
    return 0.5;  // 0.3 on + 0.2 off (igual à sirene original)
  } },
  none: { cls: 'any', unit() { return 0.5; } },
};

// ── Sons IMPORTADOS (biblioteca da equipe) ─────────────────────────────────────
/** URL do som importado `custom:<id>`, ou null se não está no catálogo carregado. */
function _customUrl(soundId) {
  const hit = ((_catalog && _catalog.sounds) || []).find(s => s.id === soundId);
  return (hit && hit.kind === 'file' && hit.url) ? hit.url : null;
}

/**
 * Toca um arquivo de áudio (som importado). One-shot quando `alertSec` é 0; num
 * alerta, repete até a duração escolhida (`loop` + parada agendada). Elementos
 * ficam em `_playing` para o preview poder cortar o som anterior.
 */
const _playing = new Set();
function _renderFile(url, volume, alertSec) {
  try {
    const el = new Audio(url);
    el.volume = Math.max(0, Math.min(1, volume));
    if (alertSec > 0) el.loop = true;
    el.addEventListener('ended', () => _playing.delete(el));
    const p = el.play();
    if (p && typeof p.catch === 'function') p.catch(() => _playing.delete(el));
    _playing.add(el);
    if (alertSec > 0) {
      setTimeout(() => { try { el.pause(); } catch (_) {} _playing.delete(el); }, alertSec * 1000);
    }
  } catch (_) { /* fail-open: som importado indisponível não quebra o disparo */ }
}

/** Interrompe os sons importados em andamento (usado pelo preview da tela). */
export function stopFileSounds() {
  for (const el of _playing) { try { el.pause(); } catch (_) {} }
  _playing.clear();
}

/** Toca `soundId` uma vez (notification) ou repetindo por `alertSec` s (alert). */
function _render(soundId, volume, alertSec) {
  // Som importado: arquivo, não receita sintetizada.
  const url = _customUrl(soundId);
  if (url) return _renderFile(url, volume, alertSec);
  const ac = _audioCtx();
  if (!ac) return;
  const def = SOUNDS[soundId];
  if (!def || soundId === 'none') return;
  if (alertSec && alertSec > 0) {
    let t = ac.currentTime;
    const end = t + alertSec;
    let guard = 0;
    while (t < end && guard < 200) {
      const cycle = def.unit(ac, t, volume) || 0.5;
      t += cycle;
      guard++;
    }
  } else {
    def.unit(ac, ac.currentTime, volume);
  }
}

// ── API pública de disparo ──────────────────────────────────────────────────────
/**
 * Toca o som de um EVENTO, resolvendo as 3 camadas. Throttle ~300ms por evento.
 * @param {string} eventKey  new_message | mention | ia_to_human | assigned_to_me
 * @param {object} [opts]    { enabledOverride, durationOverride } — usados pelas
 *   transferências (o servidor manda enabled/duration no payload).
 */
export function playEvent(eventKey, opts = {}) {
  try {
    const now = Date.now();
    if (_lastPlay[eventKey] && (now - _lastPlay[eventKey]) < THROTTLE_MS) return;
    const r = resolveEvent(eventKey, {
      global: _global,
      user: _user,
      device: { masterOn: _deviceMasterOn(), volumeMult: getDeviceVolumeMult() },
      seeds: CODE_SEEDS,
      enabledOverride: opts.enabledOverride,
      durationOverride: opts.durationOverride,
    });
    if (!r.play) return;
    _lastPlay[eventKey] = now;
    const alertSec = EVENT_CLASS[eventKey] === 'alert' ? (r.duration || 0) : 0;
    // Som importado que sumiu da biblioteca (excluído): cai no som de código do
    // evento em vez de ficar mudo — a preferência não precisa ser reescrita.
    let soundId = r.soundId;
    if (soundId.startsWith('custom:') && !_customUrl(soundId)) {
      soundId = (CODE_SEEDS[eventKey] || {}).sound || 'none';
      if (soundId === 'none') return;
    }
    _render(soundId, r.volume, alertSec);
  } catch (_) { /* nunca deixa o disparo quebrar o pipeline */ }
}

/**
 * Toca um som avulso (PREVIEW na tela, sem salvar). Gesto de usuário → destrava a
 * autoplay policy (`AudioContext.resume()`). Aplica o multiplicador do dispositivo
 * para refletir o volume real.
 * @param {string} soundId
 * @param {object} [opts] { volume (0..1), duration (s), loop }
 */
export function playDescriptor(soundId, opts = {}) {
  try {
    stopFileSounds();   // corta um preview anterior de som importado
    const vol = _clamp01(opts.volume == null ? 0.6 : opts.volume) * getDeviceVolumeMult();
    if (vol <= 0) return;
    // Som importado: arquivo. Num evento de alerta, repete até o cap do preview.
    const url = _customUrl(soundId);
    if (url) {
      return _renderFile(url, vol, opts.loop ? Math.min(opts.duration || 2, 6) : 0);
    }
    const def = SOUNDS[soundId];
    if (!def || soundId === 'none') return;
    if (opts.loop && def.cls === 'alert') {
      _render(soundId, vol, Math.min(opts.duration || 2, 6));  // cap do preview
    } else {
      _render(soundId, vol, 0);
    }
  } catch (_) { /* ignore */ }
}
