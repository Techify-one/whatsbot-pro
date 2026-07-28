/**
 * soundResolve — núcleo PURO da resolução 3-tier de som (plano 63 F2).
 *
 * Extraído do `soundEngine.js` para ser testável via `node --test` sem depender de
 * browser/localStorage/fetch (o motor importa daqui). Nenhum import — só lógica.
 *
 *   efetivo[evento][campo] = override_do_usuário ?? padrão_global ?? code_seed
 *   volume_real            = volume_efetivo × device_volume_mult
 */

// Code-seeds (piso: usados antes das prefs carregarem — nunca deixam mudo).
// Espelham `config.settings.SOUND_SETTINGS_SEED`.
export const CODE_SEEDS = {
  new_message:    { enabled: true, sound: 'ding',  volume: 0.6 },
  mention:        { enabled: true, sound: 'chime', volume: 0.8 },
  ia_to_human:    { enabled: true, sound: 'siren', volume: 0.6, duration: 5 },
  assigned_to_me: { enabled: true, sound: 'siren', volume: 0.6, duration: 5 },
};

export function clamp01(v) {
  const f = Number(v);
  if (!isFinite(f)) return 0;
  return Math.max(0, Math.min(1, f));
}

// Precedência: usuário → global → seed (undefined "cai" para a próxima camada).
export function pref(u, g, s) {
  if (u !== undefined) return u;
  if (g !== undefined) return g;
  return s;
}

/**
 * Resolve a decisão de tocar para um evento. PURA.
 *
 * @param {string} eventKey
 * @param {object} ctx  { global, user, device:{masterOn,volumeMult}, seeds,
 *                        enabledOverride, durationOverride }
 *   - `enabledOverride`: gate do SERVIDOR (transferências). `false` cala sem
 *     apelação; `true`/ausente deixa a preferência decidir (AND, não override).
 *   - `durationOverride`: FALLBACK de duração (keys legadas), abaixo de
 *     usuário/global e acima do code-seed.
 * @returns {{play:boolean, soundId?:string, volume?:number, duration?:number}}
 */
export function resolveEvent(eventKey, ctx = {}) {
  const seeds = ctx.seeds || CODE_SEEDS;
  const seed = seeds[eventKey] || {};
  const global = ctx.global || {};
  const user = ctx.user || {};
  const device = ctx.device || {};

  const gEv = (global.events && global.events[eventKey]) || {};
  const uEv = (user.events && user.events[eventKey]) || {};

  // Masters: sincronizado (usuário??global??true) E per-device (mudo local).
  const masterSynced = pref(user.master_enabled, global.master_enabled, true);
  const deviceMasterOn = device.masterOn !== false;
  if (!deviceMasterOn || masterSynced === false) return { play: false };

  // Enabled do evento: AND entre o gate do SERVIDOR (transferências — o admin
  // pode silenciar para todos) e a preferência user??global??seed (o atendente
  // pode silenciar só para si). Qualquer um dos dois em `false` cala.
  if (ctx.enabledOverride === false) return { play: false };
  if (pref(uEv.enabled, gEv.enabled, seed.enabled) === false) return { play: false };

  const soundId = pref(uEv.sound, gEv.sound, seed.sound) || 'none';
  if (soundId === 'none') return { play: false };

  let volume = pref(uEv.volume, gEv.volume, seed.volume);
  if (typeof volume !== 'number' || !isFinite(volume)) volume = 0.6;
  const mult = typeof device.volumeMult === 'number' ? device.volumeMult : 1;
  volume = clamp01(volume) * clamp01(mult);
  if (volume <= 0) return { play: false };

  // Duração: preferência do atendente primeiro (ele a edita na aba "Notificações
  // e sons"), depois o padrão da equipe; o valor mandado pelo servidor é só um
  // FALLBACK (keys legadas ``*_alert_duration``) antes do code-seed.
  const durationFallback = ctx.durationOverride !== undefined ? ctx.durationOverride : seed.duration;
  const duration = pref(uEv.duration, gEv.duration, durationFallback);

  return { play: true, soundId, volume, duration };
}
