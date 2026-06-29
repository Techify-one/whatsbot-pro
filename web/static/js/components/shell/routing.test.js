// Run with: node --test web/static/js/components/shell/routing.test.js
//
// Characterization tests (Plano 23 · D4) for the pure app-shell routing logic
// extracted from app.js. These lock the path<->tab resolution, the special
// /conversations/<id> and /executions/<id> cases, plugin-screen round-tripping,
// the entity deep-link precedence, the default screen ('contacts'), the legacy
// PT→EN redirect map, and the contact/conversation/message id parsers — so a
// router regression trips here instead of silently in the browser.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CORE_ROUTES, CORE_TAB_PATHS, pluginTabId, legacyRedirectTarget,
  tabFromPathPure, pathForTab,
  contactIdFromPathname, conversationIdFromPathname, scrollMsgFromSearchStr,
} from './routing.js';

const SCREENS = [
  { pluginId: 'lembretes', path: '/lembretes', title: 'Lembretes' },
  { pluginId: 'orders', path: '/orders', title: 'Pedidos' },
];

// ── pluginTabId ────────────────────────────────────────────────────────────
test('pluginTabId encodes pluginId + path', () => {
  assert.equal(pluginTabId(SCREENS[0]), 'plugin:lembretes:/lembretes');
});

// ── tabFromPathPure ─────────────────────────────────────────────────────────
test('tabFromPathPure: core paths map to their tab', () => {
  assert.equal(tabFromPathPure('/', [], null), 'contacts');
  assert.equal(tabFromPathPure('/contacts', [], null), 'contatos');
  assert.equal(tabFromPathPure('/channels', [], null), 'channels');
  assert.equal(tabFromPathPure('/ai', [], null), 'ai');
});
test('tabFromPathPure: /conversations/<id> opens the contacts hub', () => {
  assert.equal(tabFromPathPure('/conversations/42', [], null), 'contacts');
});
test('tabFromPathPure: /executions/<id> stays on executions', () => {
  assert.equal(tabFromPathPure('/executions/7', [], null), 'executions');
});
test('tabFromPathPure: a plugin screen path round-trips to its plugin tab', () => {
  assert.equal(tabFromPathPure('/lembretes', SCREENS, null), 'plugin:lembretes:/lembretes');
});
test('tabFromPathPure: a plugin-screen match precedes the entity deep-link', () => {
  // A registered plugin screen path wins even when an entity is present.
  assert.equal(tabFromPathPure('/lembretes', SCREENS, { tab: 'ai' }), 'plugin:lembretes:/lembretes');
});
test('tabFromPathPure: the entity deep-link precedes CORE_ROUTES (matches the original order)', () => {
  // Order in app.js: /conversations|/executions → plugin screen → entityFromPath →
  // CORE_ROUTES. So an entity (e.g. /channels/<id> resolving to {tab:'channels'})
  // is consulted BEFORE the bare-path CORE_ROUTES lookup.
  assert.equal(tabFromPathPure('/ai/agents/x', SCREENS, { tab: 'ai' }), 'ai');
  assert.equal(tabFromPathPure('/channels', SCREENS, { tab: 'channels' }), 'channels');
});
test('tabFromPathPure: unknown path with no entity → default contacts', () => {
  assert.equal(tabFromPathPure('/totally-unknown', SCREENS, null), 'contacts');
});

// ── pathForTab (round-trip with tabFromPathPure) ────────────────────────────
test('pathForTab: every core tab maps back to its canonical path', () => {
  for (const [path, tab] of Object.entries(CORE_ROUTES)) {
    assert.equal(pathForTab(tab, []), CORE_TAB_PATHS[tab], `tab ${tab}`);
  }
});
test('pathForTab: plugin tab resolves to the screen path; unknown → /', () => {
  assert.equal(pathForTab('plugin:orders:/orders', SCREENS), '/orders');
  assert.equal(pathForTab('plugin:ghost:/ghost', SCREENS), '/');
  assert.equal(pathForTab('nope', []), '/');
});

// ── legacyRedirectTarget ─────────────────────────────────────────────────────
test('legacyRedirectTarget: PT → EN map, null otherwise', () => {
  assert.equal(legacyRedirectTarget('/contatos'), '/contacts');
  assert.equal(legacyRedirectTarget('/atendimentos'), '/attendances');
  assert.equal(legacyRedirectTarget('/painel'), '/dashboard');
  assert.equal(legacyRedirectTarget('/auditoria'), '/audit');
  assert.equal(legacyRedirectTarget('/contacts'), null);
  assert.equal(legacyRedirectTarget('/'), null);
});

// ── id parsers ───────────────────────────────────────────────────────────────
test('contactIdFromPathname', () => {
  assert.equal(contactIdFromPathname('/contacts/15'), 15);
  assert.equal(contactIdFromPathname('/contacts'), null);
  assert.equal(contactIdFromPathname('/conversations/15'), null);
});
test('conversationIdFromPathname', () => {
  assert.equal(conversationIdFromPathname('/conversations/99'), 99);
  assert.equal(conversationIdFromPathname('/conversations'), null);
  assert.equal(conversationIdFromPathname('/contacts/99'), null);
});
test('scrollMsgFromSearchStr: ?message int, sanitized', () => {
  assert.equal(scrollMsgFromSearchStr('?message=123'), 123);
  assert.equal(scrollMsgFromSearchStr('?message=abc'), null);
  assert.equal(scrollMsgFromSearchStr('?foo=1'), null);
  assert.equal(scrollMsgFromSearchStr(''), null);
});
