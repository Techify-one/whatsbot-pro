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

// ──────────────────────────────────────────────────────────────────────────
// PLUGIN_SERVICES — frozen plugin-facing core API allowlist (Plano 23 · D0)
// ──────────────────────────────────────────────────────────────────────────
//
// CONTRACT (versioned by PLUGIN_SERVICES_VERSION below):
//   `api.services` exposes ONLY the core `api.js` functions named here, plus the
//   non-`api.js` extras wired in `buildPluginApi` (useWebSocket, hasPermission,
//   hasAnyPermission). This is the stable surface a plugin screen may call.
//
//   • ADDITIVE / MINOR: adding a NON-sensitive function to PLUGIN_SERVICES is a
//     MINOR bump (back-compatible — never remove a name a plugin already uses).
//   • REMOVING a name, or moving one into PLUGIN_SERVICES_DENY, is a MAJOR bump.
//   • A function in PLUGIN_SERVICES_DENY must NEVER appear in PLUGIN_SERVICES.
//
// D0 DEFINED + DOCUMENTED the allowlist; D1 ENFORCES it — `buildPluginApi` now
// builds `api.services` via `buildAllowedServices()` (curated pick) instead of the
// `...coreApi` spread, so deny-listed members are unreachable from a plugin screen.
// The list below is the current FULL non-sensitive `coreApi` surface
// (grandfathering everything except the deny-list), validated against the
// `atendimentos` plugin, which imports: authHeaders, hasPermission,
// getAssignableAgents — all present here.
//
// DENY-LIST rationale: anything touching users / roles / permissions / admin /
// migrate / auth bootstrap / API-key / global-config mutation / AI-engine
// lifecycle is core-operator surface and must NOT be reachable from a plugin
// screen, even if it currently leaks through the `...coreApi` spread.

export const PLUGIN_SERVICES_VERSION = '1.0';

/** Sensitive core functions a plugin screen must NEVER reach. Frozen. */
export const PLUGIN_SERVICES_DENY = Object.freeze([
  // Users / roles / permissions (RBAC administration)
  'getUsers', 'createUser', 'updateUser', 'deleteUser', 'resetUserPassword',
  'getRoles', 'createRole', 'updateRole', 'deleteRole', 'resetRole',
  // Auth / session bootstrap
  'login', 'logoutSession', 'bootstrapAdmin', 'checkAuth',
  // API key / global config mutation
  'testApiKey', 'saveConfig', 'getConfig',
  // WhatsApp connection lifecycle / provisioning
  'reconnect', 'logout', 'setupRequestKey', 'setupKeyStatus',
  // AI engine config-in-DB (agents/prompts/tools/variables) + restart
  'listAgents', 'getAgent', 'saveAgent', 'saveAgentPrompt', 'getAgentHistory', 'rollbackAgent', 'deleteAgent',
  'listPrompts', 'getPrompt', 'savePrompt', 'getPromptHistory', 'rollbackPrompt',
  'listVariables', 'saveVariable', 'deleteVariable',
  'listTools', 'getTool', 'saveTool', 'deleteTool', 'getToolHistory', 'rollbackTool',
  'listRegisteredTools', 'restartAi',
  // Channel administration (create/delete/membership/credentials)
  'createChannel', 'updateChannel', 'deleteChannel', 'restoreChannel',
  'setChannelMembers', 'getChannelMembers',
  'telegramAutoconfigure', 'telegramChannelStatus',
  // Audit log + raw log access
  'listAudit', 'getAuditActions', 'downloadAuditExport', 'getLogs', 'clearLogs',
  // Runtime/subprocess introspection
  'getRuntimeTasks', 'getRuntimeSubprocesses',
]);

const _DENY_SET = new Set(PLUGIN_SERVICES_DENY);

/**
 * The frozen allowlist: every `coreApi` export NOT in the deny-list. Computed
 * once from the live module so a newly-added non-sensitive function is
 * grandfathered automatically — while any name placed in PLUGIN_SERVICES_DENY
 * is excluded even if present on `coreApi`. (D0 documents this; D1 will use it
 * to build the curated `api.services` instead of `...coreApi`.)
 *
 * @type {ReadonlyArray<string>}
 */
export const PLUGIN_SERVICES = Object.freeze(
  Object.keys(coreApi)
    .filter((name) => typeof coreApi[name] === 'function' && !_DENY_SET.has(name))
    .sort()
);

/**
 * Build the curated `services` object (D1 enforcement): only the allowlisted core
 * functions, plus the non-`api.js` extras. `buildPluginApi` calls this to populate
 * `api.services`, replacing the old `...coreApi` spread — deny-listed members are
 * never reachable.
 *
 * @param {Record<string, any>} extras - non-api.js additions (useWebSocket, …).
 * @returns {Record<string, any>}
 */
export function buildAllowedServices(extras = {}) {
  /** @type {Record<string, any>} */
  const out = {};
  for (const name of PLUGIN_SERVICES) out[name] = coreApi[name];
  return { ...out, ...extras };
}

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
    // D1 ENFORCEMENT: the plugin-facing surface is the FROZEN PLUGIN_SERVICES
    // allowlist (D0) + the non-`api.js` extras below. We project ONLY allowlisted
    // members instead of spreading `...coreApi` (which leaked ~120 fns incl.
    // createUser/deleteRole). Behavior-preserving: the allowlist was grandfathered
    // from the full NON-sensitive coreApi surface, so every name a known plugin
    // already imports (atendimentos → authHeaders, getAssignableAgents) is present;
    // only deny-listed operator surface (RBAC/auth/config/AI-engine/channel admin)
    // is now withheld. To re-expose a name, add it to PLUGIN_SERVICES (MINOR bump),
    // never re-introduce the spread.
    services: buildAllowedServices({
      useWebSocket,
      hasPermission,
      hasAnyPermission,
    }),
  };
}
