// Hook compartilhado pelas abas "Notificações" e "Sons" de Configurações Gerais.
//
// Encapsula as 3 camadas de preferência (plano 63) e o modo admin:
//   efetivo[evento][campo] = override_do_usuário ?? padrão_global ?? code_seed
//
// - Camada POR-USUÁRIO e GLOBAL vêm de `GET /api/me/sound-prefs`; o override do
//   usuário é salvo campo a campo (auto-save, sem botão) em `PUT`.
// - O PADRÃO DA EQUIPE (config global `sound_settings`) é editado no modo admin.
//   O rascunho parte SEMPRE de uma cópia do global atual, então cada aba pode
//   salvar só os campos que ela edita sem apagar os da outra (o PUT /api/config
//   substitui a chave inteira).
//
// As duas abas dividem os mesmos dados de propósito: ativação mora na aba
// Notificações, som/volume/duração na aba Sons, mas ambos são o mesmo registro.
import { useState, useEffect, useCallback } from 'preact/hooks';
import { authHeaders } from '../../services/api.js';
import * as soundEngine from '../../utils/soundEngine.js';

const SEED = soundEngine.CODE_SEEDS;

export function useSoundPrefs({ onSaveConfig } = {}) {
  const [loading, setLoading] = useState(true);
  const [catalog, setCatalog] = useState(null);
  const [globalDefault, setGlobalDefault] = useState(null);
  const [userPrefs, setUserPrefs] = useState({});        // override esparso do usuário
  const [savingUser, setSavingUser] = useState(false);

  // Modo admin (editar o padrão da equipe) — a tela decide se mostra (settings.*).
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
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  // ── Resolução efetiva (usuário → global → seed) ──────────────────────────────
  const eff = useCallback((key, field) => {
    const u = userPrefs?.events?.[key]?.[field];
    if (u !== undefined) return u;
    const g = globalDefault?.events?.[key]?.[field];
    if (g !== undefined) return g;
    return SEED[key]?.[field];
  }, [userPrefs, globalDefault]);

  const isCustom = useCallback((key) => {
    const e = userPrefs?.events?.[key];
    return !!(e && Object.keys(e).length);
  }, [userPrefs]);

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
    soundEngine.reloadPrefs();   // mantém o motor em sincronia p/ o próximo disparo
    setSavingUser(false);
  }

  function setUserField(key, field, value) {
    const next = { ...(userPrefs || {}), events: { ...(userPrefs?.events || {}) } };
    next.events[key] = { ...(next.events[key] || {}), [field]: value };
    setUserPrefs(next);
    saveUser(next);
  }

  /** Atualiza só o estado local (slider arrastando) — sem ir ao servidor. */
  function setUserFieldLocal(key, field, value) {
    setUserPrefs(p => ({ ...(p || {}), events: { ...(p?.events || {}),
      [key]: { ...(p?.events?.[key] || {}), [field]: value } } }));
  }

  /** Remove o override do usuário para o evento (volta ao padrão da equipe). */
  function restoreDefault(key) {
    const events = { ...(userPrefs?.events || {}) };
    delete events[key];
    const next = { ...(userPrefs || {}), events };
    setUserPrefs(next);
    saveUser(next);
  }

  // ── Modo admin: padrão da equipe (config global sound_settings) ──────────────
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
  function setAdminMaster(on) {
    setAdminDraft(prev => ({ ...(prev || {}), master_enabled: on }));
  }
  async function saveAdmin() {
    if (!onSaveConfig) return;
    setSavingAdmin(true);
    const result = await onSaveConfig({ sound_settings: adminDraft });
    setSavingAdmin(false);
    if (result !== false) {
      setAdminSaved(true);
      setTimeout(() => setAdminSaved(false), 3000);
      await load();        // recarrega o global normalizado pelo backend
      setAdminMode(false);
    }
  }

  /** Valor exibido no grid: no modo admin lê o rascunho; no modo usuário, o efetivo. */
  function readVal(mode, key, field) {
    if (mode !== 'admin') return eff(key, field);
    return adminDraft?.events?.[key]?.[field]
      ?? globalDefault?.events?.[key]?.[field]
      ?? SEED[key]?.[field];
  }
  function writeVal(mode, key, field, value) {
    if (mode === 'admin') setAdminField(key, field, value);
    else setUserField(key, field, value);
  }

  /** Eventos do catálogo agrupados por `group` (ordem do backend). */
  function groups() {
    const out = {};
    for (const ev of (catalog?.events || [])) (out[ev.group] = out[ev.group] || []).push(ev);
    return out;
  }

  return {
    loading, catalog, globalDefault, userPrefs, savingUser,
    eff, isCustom, groups, readVal, writeVal,
    setUserField, setUserFieldLocal, restoreDefault, reload: load,
    adminMode, adminDraft, savingAdmin, adminSaved,
    enterAdmin, exitAdmin: () => setAdminMode(false), setAdminField, setAdminMaster, saveAdmin,
    SEED,
  };
}
