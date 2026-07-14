/* WhatsBot website widget — host-page loader (plano 46 · 05.1).
 *
 * Runs on the CUSTOMER's site (any domain). Zero dependencies. Injects a floating
 * launcher bubble + an <iframe> pointing at the WhatsBot widget page; the actual
 * chat UI lives in that iframe (same-origin as WhatsBot → isolated from the host
 * page's CSS/JS and able to call the widget API without CORS). The host↔iframe
 * bridge is postMessage, always with an explicit target origin and an origin check
 * on receive (never '*').
 *
 * Embed:
 *   <script>(function(d,t){var g=d.createElement(t);
 *     g.src="{baseUrl}/plugins/website/static/sdk.js";g.async=true;
 *     d.body.appendChild(g);g.onload=function(){
 *       window.WhatsBotChat.run({widgetToken:'<token>',baseUrl:'{baseUrl}'})}})(document,'script')
 *   </script>
 *
 * Styles are applied via the CSSOM (el.style.x), which is NOT gated by the host
 * site's style-src CSP — so the launcher renders even under a strict host CSP.
 */
(function () {
  'use strict';
  if (window.WhatsBotChat && window.WhatsBotChat._loaded) return;

  var state = { open: false, iframe: null, bubble: null, badge: null, baseOrigin: '' };

  function css(el, styles) { for (var k in styles) el.style[k] = styles[k]; }

  function makeBubble(color) {
    var b = document.createElement('div');
    b.setAttribute('aria-label', 'Abrir chat');
    b.setAttribute('role', 'button');
    css(b, {
      position: 'fixed', right: '20px', bottom: '20px', width: '60px', height: '60px',
      borderRadius: '50%', background: color, boxShadow: '0 6px 20px rgba(0,0,0,0.25)',
      cursor: 'pointer', zIndex: '2147483000', display: 'flex', alignItems: 'center',
      justifyContent: 'center', transition: 'transform .15s ease',
    });
    b.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" ' +
      'xmlns="http://www.w3.org/2000/svg"><path d="M12 3C7.03 3 3 6.58 3 11c0 2.1.92 ' +
      '4 2.43 5.42L4 21l4.9-1.3C10 20.2 11 20.4 12 20.4c4.97 0 9-3.58 9-8s-4.03-9-9-9z" ' +
      'fill="#fff"/></svg>';
    b.onmouseenter = function () { b.style.transform = 'scale(1.06)'; };
    b.onmouseleave = function () { b.style.transform = 'scale(1)'; };
    b.onclick = toggle;

    var badge = document.createElement('div');
    css(badge, {
      position: 'absolute', top: '-2px', right: '-2px', minWidth: '20px', height: '20px',
      padding: '0 5px', borderRadius: '10px', background: '#ef4444', color: '#fff',
      fontSize: '12px', fontWeight: '700', display: 'none', alignItems: 'center',
      justifyContent: 'center', fontFamily: 'system-ui,sans-serif', boxSizing: 'border-box',
    });
    b.appendChild(badge);
    state.badge = badge;
    return b;
  }

  function makeIframe(src) {
    var f = document.createElement('iframe');
    f.src = src;
    f.title = 'Chat';
    f.setAttribute('allow', 'microphone; clipboard-write');
    css(f, {
      position: 'fixed', right: '20px', bottom: '92px', width: '380px', height: '600px',
      maxWidth: 'calc(100vw - 40px)', maxHeight: 'calc(100vh - 120px)', border: '0',
      borderRadius: '16px', boxShadow: '0 12px 40px rgba(0,0,0,0.28)',
      zIndex: '2147483000', display: 'none', background: 'transparent',
    });
    return f;
  }

  function setBadge(n) {
    if (!state.badge) return;
    if (n > 0) { state.badge.textContent = n > 99 ? '99+' : String(n); state.badge.style.display = 'flex'; }
    else state.badge.style.display = 'none';
  }

  function open() {
    if (!state.iframe) return;
    state.open = true;
    state.iframe.style.display = 'block';
    setBadge(0);
    post({ type: 'wb:open' });
  }
  function close() {
    if (!state.iframe) return;
    state.open = false;
    state.iframe.style.display = 'none';
    post({ type: 'wb:close' });
  }
  function toggle() { state.open ? close() : open(); }

  function post(msg) {
    try {
      if (state.iframe && state.iframe.contentWindow) {
        state.iframe.contentWindow.postMessage(msg, state.baseOrigin || '*');
      }
    } catch (e) { /* ignore */ }
  }

  function onMessage(ev) {
    // Only trust messages from the WhatsBot iframe origin.
    if (state.baseOrigin && ev.origin !== state.baseOrigin) return;
    var d = ev.data || {};
    if (!d || typeof d !== 'object') return;
    if (d.type === 'wb:unread') setBadge(parseInt(d.count, 10) || 0);
    else if (d.type === 'wb:request-close') close();
    else if (d.type === 'wb:request-open') open();
  }

  function run(opts) {
    opts = opts || {};
    var token = opts.widgetToken || '';
    var base = (opts.baseUrl || '').replace(/\/+$/, '');
    if (!token || !base) { console.error('WhatsBotChat.run: widgetToken e baseUrl são obrigatórios'); return; }
    try { state.baseOrigin = new URL(base).origin; } catch (e) { state.baseOrigin = base; }
    var color = opts.color || '#0ea5a4';
    var src = base + '/api/plugins/website/public/widget?widget_token=' + encodeURIComponent(token);

    function mount() {
      if (state.bubble) return;
      state.bubble = makeBubble(color);
      state.iframe = makeIframe(src);
      document.body.appendChild(state.iframe);
      document.body.appendChild(state.bubble);
      window.addEventListener('message', onMessage);
    }
    if (document.body) mount();
    else window.addEventListener('DOMContentLoaded', mount);
  }

  window.WhatsBotChat = { _loaded: true, run: run, open: open, close: close, toggle: toggle };
})();
