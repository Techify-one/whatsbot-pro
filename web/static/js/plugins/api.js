// Builds the `api` object handed to each plugin's `extends.js` default export
// (`register(api)`), called once at boot. This is the PUBLIC frontend extension
// surface — keep it stable and bump FRONTEND_API_VERSION on breaking changes.
//
// Plugins register against the shared registry through this object (so every
// registration is namespaced to the plugin → diagnostics show ownership) and
// reuse curated core utilities via `api.services` instead of importing internal
// paths. Preact/htm are available via the importmap (web/index.html).

import * as registry from './registry.js';
import { openModal } from './ModalHost.js';
import * as coreApi from '../services/api.js';
import { useWebSocket } from '../hooks/useWebSocket.js';
import { hasPermission, hasAnyPermission } from '../utils/permissions.js';

// Version of THIS extension surface (slots/filters/route-override contract).
// Plugins declare `frontend_api_version` in plugin.yaml; the loader checks it.
export const FRONTEND_API_VERSION = '1.0';

/** Minimal compat guard: '*' or matching MAJOR is accepted. */
export function isFrontendApiCompatible(range) {
  if (!range || range === '*') return true;
  const wantMajor = parseInt(String(FRONTEND_API_VERSION), 10);
  const m = String(range).match(/\d+/);
  if (!m) return true;
  return parseInt(m[0], 10) === wantMajor;
}

export function buildPluginApi(pluginId) {
  return {
    pluginId,
    apiBase: `/api/plugins/${pluginId}`,
    frontendApiVersion: FRONTEND_API_VERSION,

    // ── registry (auto-namespaced to this plugin) ──
    addFilter: (name, fn, priority = 100) => registry.addFilter(name, fn, priority, pluginId),
    addSlot: (name, component, priority = 100) => registry.addSlot(name, component, priority, pluginId),
    overrideRoute: (tabId, component, opts = {}) => registry.overrideRoute(tabId, component, opts, pluginId),
    on: (name, fn) => registry.on(name, fn, pluginId),
    emit: (name, data) => registry.emit(name, data),
    applyFilter: registry.applyFilter,

    // ── modal host ──
    ui: { openModal },

    // ── curated core utilities (so plugins don't depend on internal paths) ──
    services: {
      ...coreApi,            // setConversationStatus, filterConversations, updateConversationInfo, authHeaders, …
      useWebSocket,
      hasPermission,
      hasAnyPermission,
    },
  };
}
