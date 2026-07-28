/* WhatsBot website widget — iframe chat app (plano 46 · 05.1/05.2/05.3).
 *
 * Runs INSIDE the WhatsBot-origin iframe (injected by sdk.js on the host page).
 * Same-origin as WhatsBot, so it uses relative URLs and inline styles freely.
 * Flow: GET config (mint/resume session + offline history) → open the per-visitor
 * WebSocket → POST messages → render assistant/operator replies pushed over the WS.
 * Reconnect with backoff+jitter; dedup by msg_id so a reconnect re-sync never
 * doubles a message. Talks to the host page (unread badge / open-close) via
 * postMessage with an explicit target origin.
 */
(function () {
  'use strict';
  var CFG = window.__WB_WIDGET__ || {};
  var API = '/api/plugins/website/public';
  var LS_KEY = 'wb_session_' + (CFG.widgetToken || 'default');

  var state = {
    // visible=false: the iframe mounts hidden (sdk.js display:none) until the
    // visitor clicks the launcher (→ wb:open). So messages arriving before the
    // first open correctly light the unread badge. Historical/re-sync messages
    // pass {historical:true} to addMsg so they never inflate the count.
    session: null, seen: {}, unread: 0, visible: false,
    ws: null, reconnect: 1000, typingTimer: null, sendingTyping: false,
  };

  // ── styles ────────────────────────────────────────────────────────────
  function injectStyles(color) {
    var css = ''
      + '*{box-sizing:border-box}'
      + 'html,body{margin:0;height:100%;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}'
      + '#wb{display:flex;flex-direction:column;height:100vh;background:#fff;border-radius:16px;overflow:hidden}'
      + '.wb-head{background:' + color + ';color:#fff;padding:16px 18px;display:flex;align-items:center;gap:10px}'
      + '.wb-head h1{margin:0;font-size:16px;font-weight:600;line-height:1.2}'
      + '.wb-head p{margin:2px 0 0;font-size:12px;opacity:.9}'
      + '.wb-x{margin-left:auto;cursor:pointer;background:transparent;border:0;color:#fff;font-size:22px;line-height:1;opacity:.85}'
      + '.wb-x:hover{opacity:1}'
      + '.wb-body{flex:1;overflow-y:auto;padding:14px;background:#f5f6f6;display:flex;flex-direction:column;gap:8px}'
      + '.wb-msg{max-width:80%;padding:8px 12px;border-radius:14px;font-size:14px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}'
      + '.wb-msg.user{align-self:flex-end;background:' + color + ';color:#fff;border-bottom-right-radius:4px}'
      + '.wb-msg.assistant{align-self:flex-start;background:#fff;color:#111;border:1px solid #e5e7eb;border-bottom-left-radius:4px}'
      + '.wb-msg img{max-width:100%;border-radius:8px;display:block}'
      + '.wb-msg audio{width:240px;max-width:100%;display:block}'
      + '.wb-msg video{max-width:100%;border-radius:8px;display:block}'
      + '.wb-msg .wb-cap{margin-top:6px}'
      + '.wb-typing{align-self:flex-start;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:10px 12px;display:none}'
      + '.wb-typing span{display:inline-block;width:6px;height:6px;margin:0 2px;border-radius:50%;background:#9ca3af;animation:wbb 1.2s infinite}'
      + '.wb-typing span:nth-child(2){animation-delay:.2s}.wb-typing span:nth-child(3){animation-delay:.4s}'
      + '@keyframes wbb{0%,60%,100%{opacity:.3}30%{opacity:1}}'
      + '.wb-foot{display:flex;gap:8px;padding:10px;border-top:1px solid #e5e7eb;background:#fff}'
      + '.wb-foot textarea{flex:1;resize:none;border:1px solid #e5e7eb;border-radius:20px;padding:9px 14px;font-size:14px;font-family:inherit;max-height:96px;outline:none}'
      + '.wb-foot textarea:focus{border-color:' + color + '}'
      + '.wb-send{border:0;background:' + color + ';color:#fff;border-radius:50%;width:40px;height:40px;cursor:pointer;flex-shrink:0;font-size:18px}'
      + '.wb-send:disabled{opacity:.5;cursor:default}'
      + '.wb-err{padding:14px;color:#b91c1c;font-size:14px;text-align:center}';
    var s = document.createElement('style');
    s.textContent = css;
    document.head.appendChild(s);
  }

  // ── DOM ───────────────────────────────────────────────────────────────
  var els = {};
  function build() {
    var root = document.getElementById('wb-root');
    root.innerHTML = ''
      + '<div id="wb">'
      + '  <div class="wb-head"><div><h1></h1><p></p></div><button class="wb-x" aria-label="Fechar">&times;</button></div>'
      + '  <div class="wb-body"><div class="wb-typing"><span></span><span></span><span></span></div></div>'
      + '  <div class="wb-foot">'
      + '    <textarea rows="1" placeholder="Escreva sua mensagem…"></textarea>'
      + '    <button class="wb-send" aria-label="Enviar">&#10148;</button>'
      + '  </div>'
      + '</div>';
    els.head = root.querySelector('.wb-head h1');
    els.tag = root.querySelector('.wb-head p');
    els.body = root.querySelector('.wb-body');
    els.typing = root.querySelector('.wb-typing');
    els.input = root.querySelector('textarea');
    els.send = root.querySelector('.wb-send');
    els.x = root.querySelector('.wb-x');
    els.head.textContent = CFG.title || 'Fale conosco';
    els.tag.textContent = CFG.tagline || '';
    els.x.onclick = function () { postParent({ type: 'wb:request-close' }); };
    els.send.onclick = sendCurrent;
    els.input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendCurrent(); }
    });
    els.input.addEventListener('input', function () {
      els.input.style.height = 'auto';
      els.input.style.height = Math.min(els.input.scrollHeight, 96) + 'px';
      notifyTyping();
    });
  }

  function scrollDown() { els.body.scrollTop = els.body.scrollHeight; }

  function addMsg(role, content, opts) {
    opts = opts || {};
    if (opts.msg_id) { if (state.seen[opts.msg_id]) return; state.seen[opts.msg_id] = 1; }
    var el = document.createElement('div');
    el.className = 'wb-msg ' + (role === 'user' ? 'user' : 'assistant');
    if (opts.media_url) {
      var mt = opts.media_type || '';
      var caption = content;
      if (mt === 'image') {
        var im = document.createElement('img'); im.src = opts.media_url; el.appendChild(im);
      } else if (mt === 'audio') {
        var au = document.createElement('audio'); au.controls = true; au.preload = 'metadata'; au.src = opts.media_url; el.appendChild(au);
        if (caption === '[Áudio]') caption = '';  // placeholder body, not a real caption
      } else if (mt === 'video') {
        var vd = document.createElement('video'); vd.controls = true; vd.preload = 'metadata'; vd.src = opts.media_url; el.appendChild(vd);
      } else {
        var a = document.createElement('a'); a.href = opts.media_url; a.target = '_blank'; a.textContent = content || 'Anexo'; el.appendChild(a);
        caption = '';  // link already carries the label
      }
      if (caption) { var cap = document.createElement('div'); cap.className = 'wb-cap'; cap.textContent = caption; el.appendChild(cap); }
    } else {
      el.textContent = content;
    }
    els.body.insertBefore(el, els.typing);
    scrollDown();
    if (role !== 'user' && !state.visible && !opts.historical) {
      state.unread++; postParent({ type: 'wb:unread', count: state.unread });
    }
    return el;
  }

  function setTyping(on) { els.typing.style.display = on ? 'block' : 'none'; if (on) scrollDown(); }

  // ── host bridge ───────────────────────────────────────────────────────
  function postParent(msg) { try { parent.postMessage(msg, '*'); } catch (e) {} }
  window.addEventListener('message', function (ev) {
    var d = ev.data || {};
    if (d.type === 'wb:open') { state.visible = true; state.unread = 0; postParent({ type: 'wb:unread', count: 0 }); els.input && els.input.focus(); }
    else if (d.type === 'wb:close') { state.visible = false; }
  });

  // ── API ───────────────────────────────────────────────────────────────
  function apiGet(path) { return fetch(API + path, { method: 'GET' }).then(function (r) { return r.json(); }); }
  function apiPost(path, body) {
    return fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-Token': state.session || '' },
      body: JSON.stringify(body || {}),
    }).then(function (r) { return r.json(); });
  }

  function bootstrap() {
    var stored = '';
    try { stored = localStorage.getItem(LS_KEY) || ''; } catch (e) {}
    var qs = '?widget_token=' + encodeURIComponent(CFG.widgetToken || '') + (stored ? '&session=' + encodeURIComponent(stored) : '');
    apiGet('/config' + qs).then(function (res) {
      if (!res || !res.ok) { showError((res && res.error) || 'Não foi possível carregar o chat.'); return; }
      state.session = res.data.session_token;
      try { localStorage.setItem(LS_KEY, state.session); } catch (e) {}
      (res.data.messages || []).forEach(function (m) {
        addMsg(m.role, m.content, { msg_id: m.msg_id, media_url: m.media_url, media_type: m.media_type, historical: true });
      });
      connectWs();
    }).catch(function () { showError('Falha de conexão.'); });
  }

  function showError(msg) {
    var d = document.createElement('div'); d.className = 'wb-err'; d.textContent = msg;
    els.body.insertBefore(d, els.typing);
  }

  function sendCurrent() {
    var text = (els.input.value || '').trim();
    if (!text || !state.session) return;
    els.input.value = ''; els.input.style.height = 'auto';
    addMsg('user', text, {});
    apiPost('/messages', { text: text }).then(function (res) {
      if (res && res.ok && res.data && res.data.msg_id) state.seen[res.data.msg_id] = 1;
      else if (res && res.error === 'rate_limited') showError('Muitas mensagens. Aguarde um instante.');
    }).catch(function () {});
  }

  var typingDebounce = null;
  function notifyTyping() {
    if (!state.session) return;
    if (typingDebounce) return;
    apiPost('/typing', { state: 'composing' }).catch(function () {});
    typingDebounce = setTimeout(function () { typingDebounce = null; }, 2500);
  }

  // ── WebSocket ─────────────────────────────────────────────────────────
  function connectWs() {
    if (!state.session) return;
    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    var url = proto + '://' + location.host + API + '/ws?session=' + encodeURIComponent(state.session);
    var ws;
    try { ws = new WebSocket(url); } catch (e) { scheduleReconnect(); return; }
    state.ws = ws;
    ws.onopen = function () { state.reconnect = 1000; };
    ws.onmessage = function (ev) {
      var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (!d) return;
      if (d.type === 'typing') { setTyping(d.state === 'composing'); }
      else if (d.type === 'message') {
        setTyping(false);
        addMsg(d.role || 'assistant', d.content || '', { msg_id: d.msg_id, media_url: d.media_url, media_type: d.media_type });
      }
    };
    ws.onclose = function () { state.ws = null; scheduleReconnect(); };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
  }

  function scheduleReconnect() {
    var delay = Math.min(state.reconnect, 15000) + Math.random() * 500;
    state.reconnect = Math.min(state.reconnect * 2, 15000);
    setTimeout(function () {
      // Re-sync history on reconnect so anything sent while offline appears (dedup by msg_id).
      var stored = state.session || '';
      apiGet('/config?widget_token=' + encodeURIComponent(CFG.widgetToken || '') + '&session=' + encodeURIComponent(stored))
        .then(function (res) {
          if (res && res.ok) (res.data.messages || []).forEach(function (m) {
            addMsg(m.role, m.content, { msg_id: m.msg_id, media_url: m.media_url, media_type: m.media_type, historical: true });
          });
        }).catch(function () {});
      connectWs();
    }, delay);
  }

  // ── boot ──────────────────────────────────────────────────────────────
  function main() {
    injectStyles(CFG.color || '#0ea5a4');
    build();
    if (CFG.error) { showError(CFG.error); return; }
    bootstrap();
    postParent({ type: 'wb:ready' });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', main);
  else main();
})();
