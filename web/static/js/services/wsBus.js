// @ts-check
//
// Singleton WebSocket bus (Plano 23 · D4) — ONE shared connection for every
// consumer, replacing the N independent sockets that `createWebSocket` opened
// (one per `useWebSocket` mount: app.js, ContactList's useConversationWsEvents,
// ConversationHeaderActions, Attendances, + plugins via plugins/api.js).
//
// WHY: opening a socket per component multiplied the server's connection count
// (and reconnect storms on a flaky link) while every socket received the SAME
// fan-out broadcast from the backend. This module owns a single connection and
// pub/subs every event to every current subscriber, byte-identically.
//
// CONTRACT (must match the old per-socket behavior exactly):
//   • subscribe(handlers) — `handlers` is the SAME shape `createWebSocket` took:
//       { onConnect?, onDisconnect?, <eventName>: (data) => void, ... }
//     Returns an unsubscribe fn. The first subscriber opens the socket; the last
//     unsubscribe closes it (and cancels any pending reconnect) — so a no-consumer
//     app keeps zero open sockets, like before.
//   • On a WS message, EVERY subscriber that registered a handler for that event
//     name is invoked with msg.data (a throwing handler is isolated so one bad
//     consumer can't starve the others — this is stricter than the old behavior,
//     where a throw bubbled to window.onerror; events still reach everyone).
//   • onConnect / onDisconnect fire for every current subscriber on (re)connect /
//     close. A subscriber that mounts while the socket is ALREADY open gets an
//     immediate onConnect (a fresh per-component socket used to fire onopen once
//     it connected; we preserve that "I'm connected" signal on late subscribe).
//   • Auto-reconnect: 3s timer on close, same token-in-querystring URL, same
//     wss/ws scheme selection — copied verbatim from services/websocket.js.

const _subscribers = new Set();   // each: the handlers object passed to subscribe()
let _ws = null;
let _reconnectTimer = null;
let _closing = false;             // true only while we intentionally tear the socket down (last unsubscribe)
let _connected = false;           // mirrors the socket's open state for late-subscriber onConnect

function _wsUrl() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = localStorage.getItem('whatsbot_token') || '';
  const qs = token ? `?token=${encodeURIComponent(token)}` : '';
  return `${protocol}//${location.host}/ws${qs}`;
}

function _connect() {
  if (_closing) return;
  // Capture THIS socket in local scope so its onclose decides reconnect from its
  // own identity, not the shared `_closing` flag — mirroring the baseline's
  // per-instance `closed` closure. A rapid teardown+resubscribe replaces `_ws`
  // with a fresh socket; the OLD socket's deferred onclose then sees it is no
  // longer the current `_ws` (or that it was tagged `_intentional`) and stays
  // quiet instead of firing a spurious onDisconnect/reconnect against the new one.
  const sock = new WebSocket(_wsUrl());
  _ws = sock;

  sock.onopen = () => {
    if (sock !== _ws) return;   // superseded before it ever opened — ignore.
    _connected = true;
    for (const h of [..._subscribers]) {
      if (h.onConnect) { try { h.onConnect(); } catch (e) { console.error('WS onConnect error:', e); } }
    }
  };

  sock.onmessage = (event) => {
    if (sock !== _ws) return;   // stale socket — ignore.
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      console.error('WS parse error:', e);
      return;
    }
    // Fan the event out to every subscriber that registered a handler for it.
    for (const h of [..._subscribers]) {
      const handler = h[msg.event];
      if (handler) {
        try { handler(msg.data); } catch (e) { console.error(`WS handler "${msg.event}" error:`, e); }
      }
    }
  };

  sock.onclose = () => {
    // A close from a socket that has been superseded (teardown nulled/replaced
    // `_ws`) or that we intentionally closed must NOT notify subscribers or
    // reconnect — exactly like the baseline's per-instance `closed` guard.
    if (sock._intentional || sock !== _ws) return;
    _connected = false;
    for (const h of [..._subscribers]) {
      if (h.onDisconnect) { try { h.onDisconnect(); } catch (e) { console.error('WS onDisconnect error:', e); } }
    }
    if (!_closing) {
      _reconnectTimer = setTimeout(_connect, 3000);
    }
  };

  sock.onerror = () => {
    if (sock === _ws) sock.close();
  };
}

function _teardown() {
  _closing = true;
  if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
  if (_ws) { _ws._intentional = true; try { _ws.close(); } catch (e) {} }
  _ws = null;
  _connected = false;
}

/**
 * Subscribe to the shared bus. `handlers` mirrors the object `createWebSocket`
 * accepted: { onConnect?, onDisconnect?, [eventName]: fn }. Returns an
 * unsubscribe fn (idempotent).
 * @param {Record<string, any>} handlers
 * @returns {() => void}
 */
export function subscribe(handlers) {
  const h = handlers || {};
  _subscribers.add(h);

  if (!_ws && !_reconnectTimer) {
    // First live subscriber → open the socket.
    _closing = false;
    _connect();
  } else if (_connected && h.onConnect) {
    // Socket already open → give the late subscriber its "connected" signal now,
    // matching how a fresh per-component socket fired onopen on connect.
    try { h.onConnect(); } catch (e) { console.error('WS onConnect error:', e); }
  }

  let done = false;
  return () => {
    if (done) return;
    done = true;
    _subscribers.delete(h);
    if (_subscribers.size === 0) _teardown();
  };
}

// Test/diagnostic seam: how many consumers are currently attached.
export function _subscriberCount() { return _subscribers.size; }
