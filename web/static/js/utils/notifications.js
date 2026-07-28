/**
 * Client-side notification preferences + helpers.
 *
 * These are per-device settings (the browser-notification permission itself is
 * per-browser), so they live in localStorage rather than the server config.
 * Changing one dispatches a `whatsbot:notif-prefs` window event so listeners
 * (e.g. the tab-title badge in app.js) can re-apply immediately.
 */

import { playEvent } from './soundEngine.js';

const KEYS = {
  tab: 'whatsbot_notif_tab',         // browser-tab unread badge "(N) WhatsBot"
  browser: 'whatsbot_notif_browser', // desktop/browser notifications
  sound: 'whatsbot_notif_sound',     // play a sound on new message
};

// Tab badge defaults ON (matches prior behavior); browser default OFF (needs an
// explicit permission grant). Sound default ON (plano 63 — o interruptor voltou a
// ser alcançável e o padrão da equipe é "tocar"; antes era OFF sem UI que o ligasse).
const DEFAULTS = { tab: true, browser: false, sound: true };

export function getNotifPref(key) {
  const v = localStorage.getItem(KEYS[key]);
  if (v === null) return DEFAULTS[key];
  return v === '1';
}

export function setNotifPref(key, value) {
  localStorage.setItem(KEYS[key], value ? '1' : '0');
  try { window.dispatchEvent(new Event('whatsbot:notif-prefs')); } catch (_) { /* ignore */ }
}

export function browserNotifSupported() {
  return typeof Notification !== 'undefined';
}

export function browserNotifPermission() {
  return browserNotifSupported() ? Notification.permission : 'unsupported';
}

export async function requestBrowserPermission() {
  if (!browserNotifSupported()) return 'unsupported';
  try {
    return await Notification.requestPermission();
  } catch (_) {
    return 'denied';
  }
}

export function showBrowserNotification(title, body) {
  if (!browserNotifSupported() || Notification.permission !== 'granted') return;
  try {
    const n = new Notification(title, { body, tag: 'whatsbot-message', renotify: true });
    n.onclick = () => { try { window.focus(); } catch (_) {} n.close(); };
  } catch (_) { /* ignore */ }
}

// plano 63 F2 — shim fino sobre o motor unificado (`soundEngine`). Mantido para
// não quebrar chamadores legados/plugins que importam `playNotificationSound`; o
// motor resolve as 3 camadas (usuário/global/dispositivo) e cobre o evento
// "mensagem nova". A tela dedicada dispara `soundEngine.playEvent(...)` direto.
export function playNotificationSound() {
  playEvent('new_message');
}
