// Client-side plugin extension registry (camada de extensão de frontend).
//
// Mirrors the backend events/filters bus (plugins/events.py) on the client. A
// SINGLE instance is shared across the core app and every plugin's `extends.js`
// module: ES module URLs are cached, so everyone that imports this file gets the
// same maps. Plugins normally don't import this directly — they receive the
// curated `api` object (see plugins/api.js) and call api.addFilter/addSlot/etc.
//
// Primitives (same naming/semantics as the backend, WordPress-style):
//   - filters: applyFilter(name, value, ctx) — priority-ordered chain; `null`
//     aborts the whole chain (mirrors `apply_filter` returning None).
//   - slots:   getSlot(name) — additive list of components rendered at a named UI
//     point (like Discourse plugin outlets / Grafana extension points).
//   - routes:  getRouteOverride(tabId) — a plugin claims a core route (EXCLUSIVE;
//     first registrant wins, conflicts are logged — never silent).
//   - events:  emit/on — fire-and-forget (parity; optional).
//
// Observability: subscribe()/getVersion() bump on every mutation so the app
// re-renders when plugin modules (which load async) register late.
//
// ─────────────────────────────────────────────────────────────────────────────
// STABLE FRONTEND EXTENSION CONTRACTS (Plano 23 · D1) — versioned by
// FRONTEND_API_VERSION in plugins/api.js.
//
//   SLOTS (getSlot/addSlot) — additive list of components; empty ⇒ renders
//   nothing (a no-plugin install is byte-identical). Render via <Slot>.
//     • sidebar.row.badges  — ctx {row}                          (ContactList row)
//     • chat.header.banner  — ctx {conv, conversationId, phone,  (ContactDetail,
//                                   channelId, contact}           below the header)
//     • conversation.header.actions, conversation.info.panel,
//       attendances.toolbar, gear.menu.items                     (pre-existing)
//     • ai.settings.sections — ctx {}                            (AI settings tab,
//                                   at the bottom of "Configurações")
//
//   FILTERS (applyFilter/addFilter) — priority-ordered; null aborts the chain.
//     • filter.message.contextMenu.items — value: the per-message context-menu
//       item array (each {label, icon, onClick, disabled?, danger?}); ctx
//       {message, isFromMe, phone, conversationId, sandbox}. Applied by
//       ContactDetail WHEN THE MENU OPENS (async), so a plugin can append/remove
//       items. Return the (modified) array; returning `null` aborts (the caller
//       falls back to the base items so the menu still opens).
//
//   ROUTE OVERRIDE (overrideRoute/getRouteOverride) — EXCLUSIVE: first registrant
//   wins, later claims are LOGGED + ignored (never silent). Decision Q5: keep
//   override (replace) semantics — do NOT compose. D2/D3 must preserve the
//   'attendances' claim. Used by the `atendimentos` plugin.
//
//   CLIENT EVENTS (emit/on) — fire-and-forget; a throwing handler is isolated.
//     • ui.conversation.selected — {conversationId, phone, channelId}  (selectContact)
//     • ui.conversation.opened   — {conversationId, phone, channelId}  (ContactDetail mount)
//     • ui.conversation.closed   — {conversationId, phone, channelId}  (ContactDetail unmount)
//
//   FILTERS (applyFilter/addFilter) — priority-ordered; null aborts the chain.
//   ModalHost.openModal (plugins/ModalHost.js) — async modal seam (await operator
//   answer); <PluginModalHost> mounted once at app root. Both are stable contracts.
// ─────────────────────────────────────────────────────────────────────────────

const _filters = new Map();    // name  -> [{priority, pluginId, fn}]   (sorted asc)
const _slots = new Map();      // name  -> [{priority, pluginId, component}] (sorted asc)
const _routes = new Map();     // tabId -> {pluginId, component, opts}
const _events = new Map();     // name  -> [{pluginId, fn}]
const _subscribers = new Set();
let _version = 0;

function bump() {
  _version++;
  for (const fn of _subscribers) { try { fn(_version); } catch (_) { /* ignore */ } }
}

/** Subscribe to registry mutations; returns an unsubscribe fn. */
export function subscribe(fn) { _subscribers.add(fn); return () => _subscribers.delete(fn); }
export function getVersion() { return _version; }

// ── Filters ────────────────────────────────────────────────────────────────
export function addFilter(name, fn, priority = 100, pluginId = 'core') {
  if (typeof fn !== 'function') return;
  const list = _filters.get(name) || [];
  list.push({ priority, pluginId, fn });
  list.sort((a, b) => a.priority - b.priority);
  _filters.set(name, list);
  bump();
}
export function getFilters(name) { return _filters.get(name) || []; }

/**
 * Chain every filter registered for `name`. Each receives (ctx, value) and
 * returns the modified value, `null` to abort (short-circuits, returns null),
 * or `undefined` to leave the value unchanged. A throwing filter is logged and
 * the value passes through (a broken plugin never traps the action).
 */
export async function applyFilter(name, value, ctx = {}) {
  const list = _filters.get(name);
  if (!list || !list.length) return value;
  let current = value;
  for (const { priority, pluginId, fn } of [...list]) {
    try {
      const result = await fn({ ...ctx, pluginId, filterName: name, priority }, current);
      if (result === null) return null;              // abort
      if (result !== undefined) current = result;    // replace (undefined = no change)
    } catch (e) {
      console.warn(`[plugins] filter "${name}" (${pluginId}) raised — value passed through`, e);
    }
  }
  return current;
}

// ── Slots ──────────────────────────────────────────────────────────────────
export function addSlot(name, component, priority = 100, pluginId = 'core') {
  if (typeof component !== 'function') return;
  const list = _slots.get(name) || [];
  list.push({ priority, pluginId, component });
  list.sort((a, b) => a.priority - b.priority);
  _slots.set(name, list);
  bump();
}
export function getSlot(name) { return _slots.get(name) || []; }

// ── Route overrides (exclusive) ──────────────────────────────────────────────
/**
 * Claim a core route (`tabId`) and REPLACE its component. EXCLUSIVE contract
 * (Plano 23 · D1, decision Q5 = keep override/replace, do NOT change to compose):
 * the first registrant wins; any later claim for the same `tabId` is logged and
 * ignored (anti-conflict policy — never fails silently). The `atendimentos`
 * plugin claims 'attendances'; D2/D3 must preserve this claim.
 */
export function overrideRoute(tabId, component, opts = {}, pluginId = 'core') {
  if (typeof component !== 'function') return;
  const existing = _routes.get(tabId);
  if (existing) {
    // Exclusive ownership: first registrant wins. Never fail silently (anti-conflict policy).
    console.warn(
      `[plugins] route "${tabId}" already overridden by "${existing.pluginId}"; ignoring "${pluginId}"`,
    );
    return;
  }
  _routes.set(tabId, { pluginId, component, opts });
  bump();
}
export function getRouteOverride(tabId) { return _routes.get(tabId) || null; }

// ── Events (fire-and-forget) ─────────────────────────────────────────────────
export function on(name, fn, pluginId = 'core') {
  if (typeof fn !== 'function') return;
  const list = _events.get(name) || [];
  list.push({ pluginId, fn });
  _events.set(name, list);
}
export function emit(name, data) {
  for (const { pluginId, fn } of (_events.get(name) || [])) {
    try { fn(data); } catch (e) { console.warn(`[plugins] event "${name}" (${pluginId}) raised`, e); }
  }
}

// ── Lifecycle / diagnostics ──────────────────────────────────────────────────
/** Clear all registrations. Called before re-registering from a fresh manifest. */
export function reset() {
  _filters.clear();
  _slots.clear();
  _routes.clear();
  _events.clear();
  bump();
}

/** "Who is plugged in where" snapshot (Query Monitor equivalent). */
export function inventory() {
  const byPlugin = (m) => Object.fromEntries(
    [...m.entries()].map(([k, v]) => [k, v.map((x) => x.pluginId)]),
  );
  return {
    filters: byPlugin(_filters),
    slots: byPlugin(_slots),
    routes: Object.fromEntries(
      [..._routes.entries()].map(([k, v]) => [k, v.pluginId]),
    ),
  };
}
