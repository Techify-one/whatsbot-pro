// Run with: node --test web/static/js/services/wsBus.test.js
//
// Characterization tests (Plano 23 · D4) for the singleton WebSocket bus. They
// lock the N->1 consolidation guarantee: ONE underlying connection, every event
// fanned out to EVERY current subscriber, onConnect/onDisconnect parity, late
// subscriber gets an immediate connect signal, last-unsubscribe closes the socket,
// and a 3s reconnect is scheduled on an unintended close.
import { test } from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal browser-global stubs (jsdom-free) ───────────────────────────────
const sockets = [];
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.onopen = this.onmessage = this.onclose = this.onerror = null;
    sockets.push(this);
  }
  // Test helpers
  _open() { this.readyState = 1; if (this.onopen) this.onopen(); }
  _message(event, data) { if (this.onmessage) this.onmessage({ data: JSON.stringify({ event, data }) }); }
  _serverClose() { this.readyState = 3; if (this.onclose) this.onclose(); }
  close() { this.readyState = 3; if (this.onclose) this.onclose(); }
}

globalThis.WebSocket = FakeWebSocket;
globalThis.location = { protocol: 'http:', host: 'localhost:8080' };
globalThis.localStorage = { getItem: () => '', setItem() {}, removeItem() {} };

// Capture reconnect timers WITHOUT clobbering node:test's own setTimeout (doing
// that globally hangs the runner). We swap globalThis.setTimeout only across the
// synchronous span of `act()`, then restore it.
let scheduled = [];
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
function act(fn) {
  globalThis.setTimeout = (cb, ms) => { const id = { cb, ms }; scheduled.push(id); return id; };
  globalThis.clearTimeout = (id) => { scheduled = scheduled.filter((x) => x !== id); };
  try { return fn(); }
  finally { globalThis.setTimeout = realSetTimeout; globalThis.clearTimeout = realClearTimeout; }
}

const { subscribe, _subscriberCount } = await import('./wsBus.js');

// The newest socket the module opened (it only ever owns one at a time).
const cur = () => sockets[sockets.length - 1];

// Each test fully unsubscribes its consumers, so the module returns to idle
// (subscriber count 0 → socket torn down). reset() only clears the per-test
// capture buffers; the module's own state is already clean.
function reset() {
  assert.equal(_subscriberCount(), 0, 'previous test left subscribers attached');
  sockets.length = 0;
  scheduled = [];
}

test('first subscriber opens exactly one socket; second reuses it', () => {
  reset();
  const a = act(() => subscribe({ status: () => {} }));
  assert.equal(sockets.length, 1, 'one socket after first subscribe');
  const b = act(() => subscribe({ new_message: () => {} }));
  assert.equal(sockets.length, 1, 'second subscriber reuses the same socket');
  assert.equal(_subscriberCount(), 2);
  act(() => { a(); b(); });
});

test('an event is fanned out to EVERY subscriber that registered it', () => {
  reset();
  const hits = [];
  const a = act(() => subscribe({ new_message: (d) => hits.push(['a', d.x]) }));
  const b = act(() => subscribe({ new_message: (d) => hits.push(['b', d.x]), status: () => hits.push(['b-status']) }));
  const c = act(() => subscribe({ status: () => hits.push(['c-status']) }));
  cur()._open();
  cur()._message('new_message', { x: 1 });
  assert.deepEqual(hits, [['a', 1], ['b', 1]], 'both new_message subscribers fired; status one skipped');
  hits.length = 0;
  cur()._message('status', {});
  assert.deepEqual(hits, [['b-status'], ['c-status']]);
  act(() => { a(); b(); c(); });
});

test('onConnect fires for all subscribers on open; late subscriber gets immediate connect', () => {
  reset();
  const log = [];
  const a = act(() => subscribe({ onConnect: () => log.push('a') }));
  cur()._open();
  assert.deepEqual(log, ['a'], 'open fires onConnect for present subscribers');
  const b = act(() => subscribe({ onConnect: () => log.push('b') }));
  assert.deepEqual(log, ['a', 'b'], 'late subscriber gets immediate onConnect (socket already open)');
  act(() => { a(); b(); });
});

test('onDisconnect fires for all subscribers and a reconnect is scheduled after an unintended close', () => {
  reset();
  const log = [];
  const a = act(() => subscribe({ onDisconnect: () => log.push('disc') }));
  cur()._open();
  act(() => cur()._serverClose());
  assert.deepEqual(log, ['disc']);
  assert.equal(scheduled.length, 1, 'a reconnect timer was scheduled');
  assert.equal(scheduled[0].ms, 3000, 'reconnect after 3s');
  act(() => a());
});

test('last unsubscribe closes the socket and no reconnect is scheduled', () => {
  reset();
  const a = act(() => subscribe({ status: () => {} }));
  const b = act(() => subscribe({ status: () => {} }));
  cur()._open();
  act(() => a());
  assert.equal(_subscriberCount(), 1);
  assert.equal(cur().readyState, 1, 'socket still open while a consumer remains');
  act(() => b());
  assert.equal(_subscriberCount(), 0);
  assert.equal(cur().readyState, 3, 'socket closed on last unsubscribe');
  // The intentional teardown must NOT schedule a reconnect.
  assert.equal(scheduled.length, 0, 'no reconnect after intentional last-unsubscribe close');
});

test('a throwing handler is isolated — other subscribers still receive the event', () => {
  reset();
  const seen = [];
  const a = act(() => subscribe({ new_message: () => { throw new Error('boom'); } }));
  const b = act(() => subscribe({ new_message: (d) => seen.push(d.x) }));
  cur()._open();
  cur()._message('new_message', { x: 42 });
  assert.deepEqual(seen, [42]);
  act(() => { a(); b(); });
});
