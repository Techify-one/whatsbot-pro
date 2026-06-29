// App-shell screen registry + routing wrappers (Plano 23 · D4). The declarative
// core-screen table + the PURE path<->tab parsers live in routing.js (dependency-
// free, node --test-able). This module re-exports them and adds the thin
// window.location-reading wrappers app.js used to define inline — including the
// entity deep-link fold-in (entityFromPath) and the history.replaceState legacy
// redirect. Behavior is preserved EXACTLY.
import { entityFromPath } from '../../hooks/useDeepLink.js';
import {
  CORE_ROUTES, CORE_TAB_PATHS, LEGACY_PATH_REDIRECTS, pluginTabId,
  legacyRedirectTarget, tabFromPathPure, pathForTab,
  contactIdFromPathname, conversationIdFromPathname, scrollMsgFromSearchStr,
} from './routing.js';

// Re-export the pure surface so existing imports keep resolving against this file.
export {
  CORE_ROUTES, CORE_TAB_PATHS, LEGACY_PATH_REDIRECTS, pluginTabId,
  legacyRedirectTarget, tabFromPathPure, pathForTab,
  contactIdFromPathname, conversationIdFromPathname, scrollMsgFromSearchStr,
};

// ── Thin window-reading wrappers (used by App.js; keep the old call sites) ──
export function tabFromPath(pluginScreens) {
  return tabFromPathPure(window.location.pathname, pluginScreens, entityFromPath());
}
export function contactIdFromPath() { return contactIdFromPathname(window.location.pathname); }
export function conversationIdFromPath() { return conversationIdFromPathname(window.location.pathname); }
export function scrollMsgFromSearch() { return scrollMsgFromSearchStr(window.location.search); }

// Rewrite a legacy PT path to its English equivalent in the address bar (no-op
// for any other path). Returns true when a redirect was applied.
export function redirectLegacyPath() {
  const to = legacyRedirectTarget(window.location.pathname);
  if (to) {
    history.replaceState(null, '', `${to}${window.location.search}`);
    return true;
  }
  return false;
}
