// Plugin modal host. Lets a (possibly async) plugin filter open a modal and
// AWAIT the operator's answer — the mechanism behind "popup ao resolver".
//
//   const vals = await api.ui.openModal((close) =>
//     html`<${MyForm} onOk=${(v) => close(v)} onCancel=${() => close(null)} />`);
//
// `<PluginModalHost/>` must be mounted ONCE at the app root (see app.js) BEFORE
// any filter runs, otherwise openModal would hang. The plugin renders its own
// overlay/markup (use wa-*/.wa-field for dark-mode) — same pattern as the core
// LowBalanceModal / RequiredAttributesModal.

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

let _seq = 0;
const _modals = [];          // [{id, render, close}]
const _subs = new Set();

function notify() { for (const fn of _subs) { try { fn(); } catch (_) { /* ignore */ } } }

/**
 * Open a modal and resolve with whatever the plugin passes to `close(result)`.
 * @param {(close: (result:any)=>void) => any} renderFn  returns a vnode.
 * @param {{timeoutMs?: number}} [opts]  auto-resolve to null after timeoutMs (0 = never).
 * @returns {Promise<any>}
 */
export function openModal(renderFn, { timeoutMs = 0 } = {}) {
  return new Promise((resolve) => {
    const id = ++_seq;
    let settled = false;
    const close = (result) => {
      if (settled) return;
      settled = true;
      const i = _modals.findIndex((m) => m.id === id);
      if (i >= 0) _modals.splice(i, 1);
      notify();
      resolve(result);
    };
    _modals.push({ id, render: renderFn, close });
    notify();
    if (timeoutMs > 0) setTimeout(() => close(null), timeoutMs);
  });
}

export function PluginModalHost() {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    _subs.add(fn);
    return () => { _subs.delete(fn); };
  }, []);

  if (!_modals.length) return null;
  return html`
    <div>
      ${_modals.map((m) => html`<div key=${m.id}>${m.render(m.close)}</div>`)}
    </div>
  `;
}

