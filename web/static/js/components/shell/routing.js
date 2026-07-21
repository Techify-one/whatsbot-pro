// @ts-check
//
// App-shell routing — PURE logic (Plano 23 · D4). No preact, no DOM, no network,
// no module state: the core-screen table + the path<->tab parsers, all taking
// explicit string args so they are node --test-able. screenRegistry.js re-exports
// these and adds the window.location-reading wrappers (which also fold in the
// entity deep-link from useDeepLink). Behavior is identical to the pre-decomposition
// app.js helpers.

// Core (built-in) routes. Plugin screens are merged in dynamically (App.js).
export const CORE_ROUTES = {
  '/': 'contacts',
  '/contacts': 'contatos',
  // A aba é exclusiva do plugin protocolos (override de rota). O path canônico é
  // /protocolos; /attendances segue como ALIAS silencioso p/ links antigos.
  '/protocolos': 'attendances',
  '/attendances': 'attendances',
  '/channels': 'channels',
  '/dashboard': 'dashboard',
  '/sandbox': 'sandbox',
  '/costs': 'costs',
  '/executions': 'executions',
  '/plugins': 'plugins',
  '/quick-replies': 'quick-replies',
  '/custom-attributes': 'custom-attributes',
  '/runtime': 'runtime',
  '/users': 'users',
  '/audit': 'audit',
  '/ai': 'ai',
  '/sounds': 'sounds',
};
export const CORE_TAB_PATHS = {
  contacts: '/',
  contatos: '/contacts',
  attendances: '/protocolos',
  channels: '/channels',
  dashboard: '/dashboard',
  sandbox: '/sandbox',
  costs: '/costs',
  executions: '/executions',
  plugins: '/plugins',
  'quick-replies': '/quick-replies',
  'custom-attributes': '/custom-attributes',
  runtime: '/runtime',
  users: '/users',
  audit: '/audit',
  ai: '/ai',
  sounds: '/sounds',
};

// Legacy Portuguese URLs → canonical English paths. The routes were standardized
// to English; these redirects keep old bookmarks/links working (applied with
// replaceState so they don't pollute history).
const LEGACY_PATH_REDIRECTS = {
  '/contatos': '/contacts',
  '/atendimentos': '/protocolos',
  '/painel': '/dashboard',
  '/auditoria': '/audit',
};

// Tab id used internally for plugin screens. We encode the plugin id and
// the screen path so the router can round-trip it.
export function pluginTabId(screen) { return `plugin:${screen.pluginId}:${screen.path}`; }

/** Legacy PT path → EN target, or null if not a legacy path. */
export function legacyRedirectTarget(pathname) {
  return LEGACY_PATH_REDIRECTS[pathname] || null;
}

/**
 * Resolve the active tab id from a pathname + the known plugin screens.
 * @param {string} path window.location.pathname
 * @param {Array} pluginScreens
 * @param {{tab:string}|null} ent already-resolved entity deep-link (entityFromPath)
 */
export function tabFromPathPure(path, pluginScreens, ent) {
  // Atendimento-cêntrico (plano 11 D1): /conversations/<id> abre o chat daquela
  // atendimento no hub de contatos. (/conversations sem id segue na lista full-page.)
  // /contacts/<id> (detalhe do contato) é resolvido via entityFromPath → 'contatos'.
  if (path.match(/^\/conversations\/\d+$/)) return 'contacts';
  if (path.match(/^\/executions\/\d+$/)) return 'executions';
  const screen = (pluginScreens || []).find(s => s.path === path);
  if (screen) return pluginTabId(screen);
  // Deep-link de entidade (/ai/agents/<key>, /channels/<id>, …) → tab dona.
  if (ent) return ent.tab;
  return CORE_ROUTES[path] || 'contacts';
}

/** Canonical SPA path for a tab id (core or plugin). */
export function pathForTab(tab, pluginScreens) {
  if (CORE_TAB_PATHS[tab]) return CORE_TAB_PATHS[tab];
  if (tab && tab.startsWith('plugin:')) {
    const screen = (pluginScreens || []).find(s => pluginTabId(s) === tab);
    if (screen) return screen.path;
  }
  return '/';
}

/** contact_id from /contacts/<id>, else null. */
export function contactIdFromPathname(pathname) {
  const m = pathname.match(/^\/contacts\/(\d+)$/);
  return m ? parseInt(m[1], 10) : null;
}

/** conversation_id from /conversations/<id>, else null (plano 11 D1). */
export function conversationIdFromPathname(pathname) {
  const m = pathname.match(/^\/conversations\/(\d+)$/);
  return m ? parseInt(m[1], 10) : null;
}

/**
 * Permalink message target (?message=<_id>): the scroll target int from the query
 * string, sanitized to an integer (feeds scrollToMsg). Returns null when absent
 * or non-numeric.
 */
export function scrollMsgFromSearchStr(search) {
  const raw = new URLSearchParams(search).get('message');
  if (raw == null) return null;
  const n = parseInt(raw, 10);
  return Number.isNaN(n) ? null : n;
}
