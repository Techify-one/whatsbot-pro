// "Notificações e sons" (plano 63 F3) — tela CORE onde cada atendente escolhe
// SOM, VOLUME e DURAÇÃO por evento, ouve um PREVIEW e liga/desliga o som. A
// preferência é POR-USUÁRIO (segue o atendente entre dispositivos, via
// /api/me/sound-prefs) com um multiplicador de volume POR-DISPOSITIVO
// (localStorage). O admin (settings.manage) pode editar o PADRÃO DA EQUIPE
// (config global sound_settings).
//
// Regras de tema/dark-mode (CLAUDE.md): classes wa-* e .wa-field; nada de cor crua.
import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { authHeaders } from '../services/api.js';
import { getNotifPref, setNotifPref } from '../utils/notifications.js';
import * as soundEngine from '../utils/soundEngine.js';
import { hasPermission } from '../utils/permissions.js';

const html = htm.bind(h);

const SEED = soundEngine.CODE_SEEDS;

// Sons oferecidos para um evento pela classe: 'alert' → sons de alerta; qualquer
// outra (notification/one-shot) → sons 'once'. 'any' (silêncio) sempre disponível.
function soundsForClass(sounds, cls) {
  const want = cls === 'alert' ? 'alert' : 'once';
  return (sounds || []).filter(s => s.cls === want || s.cls === 'any');
}

function VolumeSlider({ value, onInput, onChange, label }) {
  return html`
    <div class="flex items-center gap-2 min-w-[160px]">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" class="text-wa-secondary shrink-0"><path d="M3 9v6h4l5 5V4L7 9H3z"/></svg>
      <input type="range" min="0" max="1" step="0.05" value=${value}
        aria-label=${label || 'Volume'}
        onInput=${(e) => onInput && onInput(parseFloat(e.target.value))}
        onChange=${(e) => onChange && onChange(parseFloat(e.target.value))}
        class="flex-1 accent-wa-teal" />
      <span class="text-[11px] text-wa-secondary w-8 text-right">${Math.round((value || 0) * 100)}%</span>
    </div>`;
}

export default function SoundSettings({ config, onSaveConfig, currentUser }) {
  const canManage = hasPermission(currentUser, 'settings.manage');

  const [loading, setLoading] = useState(true);
  const [catalog, setCatalog] = useState(null);
  const [globalDefault, setGlobalDefault] = useState(null);
  const [userPrefs, setUserPrefs] = useState({});        // override esparso do usuário
  const [savingUser, setSavingUser] = useState(false);

  // Camada por-dispositivo (localStorage): master ligado + multiplicador de volume.
  const [deviceMaster, setDeviceMaster] = useState(true);
  const [deviceVol, setDeviceVol] = useState(1);

  // Modo admin (editar o padrão da equipe) — só com settings.manage.
  const [adminMode, setAdminMode] = useState(false);
  const [adminDraft, setAdminDraft] = useState(null);
  const [savingAdmin, setSavingAdmin] = useState(false);
  const [adminSaved, setAdminSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/me/sound-prefs', { headers: authHeaders() });
      const j = await res.json();
      if (j && j.ok && j.data) {
        setUserPrefs(j.data.prefs || {});
        setGlobalDefault(j.data.global_default || null);
        setCatalog(j.data.catalog || null);
      }
    } catch (_) { /* fail-open */ }
    setDeviceMaster(getNotifPref('sound'));
    setDeviceVol(soundEngine.getDeviceVolumeMult());
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // ── Resolução efetiva (usuário → global → seed) ──────────────────────────────
  function eff(key, field) {
    const u = userPrefs?.events?.[key]?.[field];
    if (u !== undefined) return u;
    const g = globalDefault?.events?.[key]?.[field];
    if (g !== undefined) return g;
    return SEED[key]?.[field];
  }
  function isCustom(key) {
    const e = userPrefs?.events?.[key];
    return !!(e && Object.keys(e).length);
  }

  // ── Persistência do override do usuário ──────────────────────────────────────
  async function saveUser(nextPrefs) {
    setSavingUser(true);
    try {
      const res = await fetch('/api/me/sound-prefs', {
        method: 'PUT',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ prefs: nextPrefs }),
      });
      const j = await res.json();
      if (j && j.ok && j.data) setUserPrefs(j.data.prefs || {});
    } catch (_) { /* ignore */ }
    // Mantém o motor em sincronia para o próximo disparo real.
    soundEngine.reloadPrefs();
    setSavingUser(false);
  }

  function setUserField(key, field, value) {
    const next = { ...(userPrefs || {}), events: { ...(userPrefs?.events || {}) } };
    next.events[key] = { ...(next.events[key] || {}), [field]: value };
    setUserPrefs(next);
    saveUser(next);
  }

  function restoreDefault(key) {
    const events = { ...(userPrefs?.events || {}) };
    delete events[key];
    const next = { ...(userPrefs || {}), events };
    setUserPrefs(next);
    saveUser(next);
  }

  // ── Camada por-dispositivo ───────────────────────────────────────────────────
  function toggleDeviceMaster(on) {
    setDeviceMaster(on);
    setNotifPref('sound', on);   // whatsbot_notif_sound (dispara whatsbot:notif-prefs)
  }
  function commitDeviceVol(v) {
    setDeviceVol(v);
    soundEngine.setDeviceVolumeMult(v);
  }

  // ── Preview (gesto de usuário → destrava a autoplay policy) ──────────────────
  function preview(soundId, volume, cls, duration) {
    soundEngine.playDescriptor(soundId, {
      volume,
      loop: cls === 'alert',
      duration: cls === 'alert' ? Math.min(duration || 3, 4) : 0,
    });
  }

  // ── Modo admin: editar o padrão da equipe (sound_settings global) ────────────
  function enterAdmin() {
    setAdminDraft(JSON.parse(JSON.stringify(globalDefault || { master_enabled: true, events: {} })));
    setAdminMode(true);
    setAdminSaved(false);
  }
  function setAdminField(key, field, value) {
    setAdminDraft(prev => {
      const next = { ...(prev || {}), events: { ...(prev?.events || {}) } };
      next.events[key] = { ...(next.events[key] || {}), [field]: value };
      return next;
    });
  }
  async function saveAdmin() {
    setSavingAdmin(true);
    const result = await onSaveConfig({ sound_settings: adminDraft });
    setSavingAdmin(false);
    if (result !== false) {
      setAdminSaved(true);
      setTimeout(() => setAdminSaved(false), 3000);
      await load();  // recarrega o global normalizado pelo backend
      setAdminMode(false);
    }
  }

  if (loading || !catalog) {
    return html`<div class="bg-wa-bg rounded-xl p-5 animate-pulse-slow text-wa-secondary border border-wa-border">Carregando…</div>`;
  }

  const groups = {};
  for (const ev of catalog.events) {
    (groups[ev.group] = groups[ev.group] || []).push(ev);
  }

  // Grid de eventos. `mode` = 'user' | 'admin'.
  function EventGrid({ mode }) {
    const readVal = (key, field) => mode === 'admin'
      ? (adminDraft?.events?.[key]?.[field] ?? globalDefault?.events?.[key]?.[field] ?? SEED[key]?.[field])
      : eff(key, field);
    const writeVal = (key, field, v) => mode === 'admin' ? setAdminField(key, field, v) : setUserField(key, field, v);

    return html`
      ${Object.entries(groups).map(([groupName, evs]) => html`
        <div class="mb-4">
          <div class="text-[11px] font-semibold text-wa-secondary uppercase tracking-wider mb-2">${groupName}</div>
          <div class="flex flex-col gap-2">
            ${evs.map(ev => {
              const key = ev.key;
              const enabled = readVal(key, 'enabled') !== false;
              const soundId = readVal(key, 'sound') || 'none';
              const volume = (typeof readVal(key, 'volume') === 'number') ? readVal(key, 'volume') : 0.6;
              const duration = readVal(key, 'duration') ?? 5;
              const opts = soundsForClass(catalog.sounds, ev.cls);
              const custom = mode === 'user' && isCustom(key);
              const isAlert = ev.cls === 'alert';
              // Transferências: enabled/duração são do ADMIN (keys legadas) — no modo
              // usuário mostramos como informativo; só editamos som/volume.
              const enabledEditable = !ev.server_gated;
              return html`
                <div key=${key} class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
                  <div class="flex items-center justify-between gap-3 flex-wrap">
                    <label class="flex items-center gap-2 text-sm font-semibold text-wa-text ${enabledEditable ? 'cursor-pointer' : ''}">
                      ${enabledEditable ? html`
                        <input type="checkbox" checked=${enabled}
                          onChange=${(e) => writeVal(key, 'enabled', e.target.checked)}
                          class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
                      ` : null}
                      ${ev.label}
                    </label>
                    ${custom
                      ? html`<span class="text-[10px] px-2 py-0.5 rounded-full bg-wa-teal/15 text-wa-teal font-medium">personalizado</span>`
                      : html`<span class="text-[10px] px-2 py-0.5 rounded-full bg-wa-hover text-wa-secondary">padrão da equipe</span>`}
                  </div>
                  <div class="flex items-center gap-3 flex-wrap">
                    <select value=${soundId} onChange=${(e) => writeVal(key, 'sound', e.target.value)}
                      class="wa-field px-2 py-1.5 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none">
                      ${opts.map(s => html`<option value=${s.id}>${s.label}</option>`)}
                    </select>
                    <button type="button"
                      onClick=${() => preview(soundId, volume, ev.cls, duration)}
                      class="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-sm border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                      Ouvir
                    </button>
                    <${VolumeSlider} value=${volume} label=${`Volume de ${ev.label}`}
                      onInput=${(v) => mode === 'admin' ? setAdminField(key, 'volume', v) : setUserPrefs(p => ({ ...(p || {}), events: { ...(p?.events || {}), [key]: { ...(p?.events?.[key] || {}), volume: v } } }))}
                      onChange=${(v) => writeVal(key, 'volume', v)} />
                    ${custom ? html`
                      <button type="button" onClick=${() => restoreDefault(key)}
                        class="text-[12px] text-wa-secondary hover:text-wa-text underline decoration-dotted">Restaurar padrão</button>
                    ` : null}
                  </div>
                  ${isAlert ? html`
                    <div class="text-[11px] text-wa-secondary">
                      ${ev.server_gated
                        ? html`Ativação e duração (${duration}s) são definidas pelo administrador. Você escolhe apenas o som e o volume.`
                        : html`Alerta contínuo — repete por ${duration}s.`}
                    </div>
                  ` : html`<div class="text-[11px] text-wa-secondary">Toca uma vez.</div>`}
                </div>`;
            })}
          </div>
        </div>
      `)}
    `;
  }

  return html`
    <div class="flex flex-col gap-4 max-w-3xl">
      <!-- Master + volume do dispositivo -->
      <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm flex flex-col gap-4">
        <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
          <input type="checkbox" checked=${deviceMaster}
            onChange=${(e) => toggleDeviceMaster(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
          Tocar sons
        </label>
        <span class="text-xs text-wa-secondary -mt-2">Liga/desliga todos os sons de notificação NESTE dispositivo (navegador). As escolhas por evento abaixo seguem você em qualquer dispositivo.</span>
        <div class="flex items-center gap-3">
          <span class="text-sm font-semibold text-wa-text min-w-[160px]">Volume neste dispositivo</span>
          <${VolumeSlider} value=${deviceVol} label="Volume neste dispositivo"
            onInput=${(v) => setDeviceVol(v)} onChange=${(v) => commitDeviceVol(v)} />
        </div>
        <span class="text-xs text-wa-secondary -mt-2">Ajuste local (ex.: PC do escritório × notebook silencioso). Multiplica o volume de cada evento sem mudar sua preferência sincronizada.</span>
      </div>

      <!-- Preferências por evento (por-usuário) -->
      <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider">Sons por evento</h3>
          ${savingUser ? html`<span class="text-[11px] text-wa-secondary">salvando…</span>` : null}
        </div>
        ${EventGrid({ mode: 'user' })}
      </div>

      <!-- Modo admin: padrão da equipe -->
      ${canManage ? html`
        <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider">Padrão da equipe</h3>
              <span class="text-xs text-wa-secondary">Define o padrão para quem não personalizou. Ativação e duração das transferências ficam nas Configurações Gerais / Canais.</span>
            </div>
            ${!adminMode
              ? html`<button type="button" onClick=${enterAdmin}
                  class="px-3 py-1.5 rounded-lg text-sm bg-wa-teal text-white hover:opacity-90 transition-opacity">Editar padrão da equipe</button>`
              : html`
                <div class="flex items-center gap-2">
                  ${adminSaved ? html`<span class="text-[12px] text-wa-teal font-medium">✓ Salvo!</span>` : null}
                  <button type="button" onClick=${() => setAdminMode(false)}
                    class="px-3 py-1.5 rounded-lg text-sm border border-wa-border text-wa-text hover:bg-wa-hover">Cancelar</button>
                  <button type="button" onClick=${saveAdmin} disabled=${savingAdmin}
                    class="px-3 py-1.5 rounded-lg text-sm bg-wa-teal text-white hover:opacity-90 disabled:opacity-50">
                    ${savingAdmin ? 'Salvando…' : 'Salvar padrão'}</button>
                </div>`}
          </div>
          ${adminMode ? html`
            <div class="mt-4 pt-4 border-t border-wa-border">
              <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer mb-3">
                <input type="checkbox" checked=${adminDraft?.master_enabled !== false}
                  onChange=${(e) => setAdminDraft(prev => ({ ...(prev || {}), master_enabled: e.target.checked }))}
                  class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
                Sons ligados por padrão para a equipe
              </label>
              ${EventGrid({ mode: 'admin' })}
            </div>
          ` : null}
        </div>
      ` : null}
    </div>`;
}
