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
import { authHeaders, handleUnauthorized, handleErrorResponse } from '../services/httpClient.js';
import { notifyPermissionDenied } from '../services/notify.js';
import { useWebSocket } from '../hooks/useWebSocket.js';
import { hasPermission, hasAnyPermission } from '../utils/permissions.js';
import { satisfiesVersionRange, selectSupportedVersion } from './versionCompat.js';
import {
  LEGACY_V1_SERVICE_NAMES,
  buildVersionedServiceSurface,
} from './serviceSurface.js';

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
// `protocolos` plugin, which imports: authHeaders, hasPermission,
// getAssignableAgents — all present here.
//
// DENY-LIST rationale: anything touching users / roles / permissions / admin /
// migrate / auth bootstrap / API-key / global-config mutation / AI-engine
// lifecycle is core-operator surface and must NOT be reachable from a plugin
// screen, even if it currently leaks through the `...coreApi` spread.

// 2.0 removes `getGowaAlertSettings` and the dead attendance batch helper from
// the current surface. Manifests negotiate this independently from the registry
// API through `plugin_services_version`; legacy manifests default to 1.x and get
// a narrow compatibility adapter, while incompatible declared ranges are skipped.
export const PLUGIN_SERVICES_VERSION = '2.0';
export const SUPPORTED_PLUGIN_SERVICES_VERSIONS = Object.freeze(['1.0', '2.0']);

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
    .filter((name) => typeof coreApi[name] === 'function'
      && !_DENY_SET.has(name)
      && !LEGACY_V1_SERVICE_NAMES.includes(name))
    .sort()
);

/** Names available to a manifest that still targets the compatibility surface 1.x. */
export const PLUGIN_SERVICES_V1 = Object.freeze(
  [...PLUGIN_SERVICES, ...LEGACY_V1_SERVICE_NAMES].sort()
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
export function buildAllowedServices(extras = {}, servicesVersion = PLUGIN_SERVICES_VERSION) {
  /** @type {Record<string, any>} */
  const current = {};
  for (const name of PLUGIN_SERVICES) current[name] = coreApi[name];
  const legacyHttp = String(servicesVersion).split('.')[0] === '1'
    ? buildPluginHttp('/api')
    : null;
  return {
    ...buildVersionedServiceSurface(current, servicesVersion, legacyHttp),
    ...extras,
  };
}

/**
 * Build the plugin HTTP transport (`api.http`). Same status-aware normalisation as
 * the core `httpClient` (checks HTTP status, unifies the `{error}`/`{detail}` bodies,
 * fires the "Permissão negada." toast on 403 via `handleErrorResponse`, and clears the
 * session on 401) — so a plugin never silently treats a 403 as success. Path handling:
 * a path starting with `http(s)://` or `/api/` is used AS-IS (already absolute); any
 * other path is resolved relative to the plugin's `apiBase` (`/api/plugins/<id>`).
 *
 * @param {string} apiBase - `/api/plugins/<id>`.
 * @returns {{get:Function, post:Function, put:Function, del:Function, request:Function}}
 */
export function buildPluginHttp(apiBase) {
  const resolve = (path) => {
    const p = String(path == null ? '' : path);
    if (/^https?:\/\//.test(p) || p.startsWith('/api/')) return p;
    return `${apiBase}${p.startsWith('/') ? p : `/${p}`}`;
  };
  async function req(method, path, body) {
    /** @type {RequestInit} */
    const init = { method, headers: authHeaders() };
    if (body !== undefined && body !== null) {
      init.headers = { ...init.headers, 'Content-Type': 'application/json' };
      init.body = JSON.stringify(body);
    }
    const res = await fetch(resolve(path), init);
    if (res.status === 401) {
      handleUnauthorized();
      return { ok: false, error: 'Não autenticado.', status: 401 };
    }
    if (!res.ok) return handleErrorResponse(res);
    return res.json();
  }
  return {
    get: (path) => req('GET', path),
    post: (path, body) => req('POST', path, body),
    put: (path, body) => req('PUT', path, body),
    del: (path, body) => req('DELETE', path, body),
    request: req,
  };
}

/** Registry/slot API compatibility gate. */
export function isFrontendApiCompatible(range) {
  return satisfiesVersionRange(FRONTEND_API_VERSION, range);
}

/** Return the newest service surface supported by both host and manifest. */
export function selectPluginServicesVersion(range) {
  return selectSupportedVersion(range, SUPPORTED_PLUGIN_SERVICES_VERSIONS, {
    defaultRange: '1.0',
  });
}

export function isPluginServicesCompatible(range) {
  return selectPluginServicesVersion(range) !== null;
}

export function buildPluginApi(pluginId, pluginServicesRange = '1.0') {
  const negotiatedServicesVersion = (
    selectPluginServicesVersion(pluginServicesRange) || PLUGIN_SERVICES_VERSION
  );
  return {
    pluginId,
    apiBase: `/api/plugins/${pluginId}`,
    frontendApiVersion: FRONTEND_API_VERSION,
    // The concrete surface this object exposes, plus the newest host surface
    // for diagnostics. Plugins should feature-detect optional individual names.
    pluginServicesVersion: negotiatedServicesVersion,
    pluginServicesHostVersion: PLUGIN_SERVICES_VERSION,

    // ── registry (auto-namespaced to this plugin) ──
    addFilter: (name, fn, priority = 100) => registry.addFilter(name, fn, priority, pluginId),
    addSlot: (name, component, priority = 100) => registry.addSlot(name, component, priority, pluginId),
    overrideRoute: (tabId, component, opts = {}) => registry.overrideRoute(tabId, component, opts, pluginId),
    overrideComponent: (name, component) => registry.overrideComponent(name, component, pluginId),
    on: (name, fn) => registry.on(name, fn, pluginId),
    emit: (name, data) => registry.emit(name, data),
    applyFilter: registry.applyFilter,

    // ── status-aware HTTP transport (checks status, unifies error body, toasts
    //    "Permissão negada." on 403). Plugins SHOULD use this instead of raw fetch. ──
    http: buildPluginHttp(`/api/plugins/${pluginId}`),

    // ── modal host + notificações transitórias (toast) ──
    ui: { openModal, notifyPermissionDenied },

    // ── curated core utilities (so plugins don't depend on internal paths) ──
    // D1 ENFORCEMENT: the plugin-facing surface is the FROZEN PLUGIN_SERVICES
    // allowlist (D0) + the non-`api.js` extras below. We project ONLY allowlisted
    // members instead of spreading `...coreApi` (which leaked ~120 fns incl.
    // createUser/deleteRole). Behavior-preserving: the allowlist was grandfathered
    // from the full NON-sensitive coreApi surface, so every name a known plugin
    // already imports (protocolos → authHeaders, getAssignableAgents) is present;
    // only deny-listed operator surface (RBAC/auth/config/AI-engine/channel admin)
    // is now withheld. To re-expose a name, add it to PLUGIN_SERVICES (MINOR bump),
    // never re-introduce the spread.
    services: buildAllowedServices({
      useWebSocket,
      hasPermission,
      hasAnyPermission,
    }, negotiatedServicesVersion),
  };
}
