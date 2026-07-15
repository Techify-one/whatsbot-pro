// Renders a plugin-contributed screen by dynamically importing its module.
// The module must default-export a Preact component that accepts an
// ``apiBase`` prop (the plugin's REST namespace).

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { hasPermission } from '../utils/permissions.js';

const html = htm.bind(h);

const _moduleCache = new Map();

export function PluginScreen({ screen, currentUser }) {
  const [Component, setComponent] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!screen || !screen.component) return;
    setError(null);
    setComponent(null);
    const cached = _moduleCache.get(screen.component);
    if (cached) {
      setComponent(() => cached);
      return;
    }
    import(screen.component)
      .then(mod => {
        const C = mod && (mod.default || mod.Component);
        if (typeof C !== 'function') {
          throw new Error('Plugin module must export a default Preact component');
        }
        _moduleCache.set(screen.component, C);
        setComponent(() => C);
      })
      .catch(e => setError(String(e && e.message || e)));
  }, [screen && screen.component]);

  if (error) {
    return html`
      <div class="max-w-3xl mx-auto p-6">
        <div class="bg-red-50 border border-red-200 text-red-800 rounded p-4">
          <strong>Falha ao carregar plugin:</strong> ${error}
        </div>
      </div>
    `;
  }

  if (!Component) {
    return html`
      <div class="max-w-3xl mx-auto p-6 text-wa-secondary">
        Carregando plugin…
      </div>
    `;
  }

  const apiBase = `/api/plugins/${screen.pluginId}`;
  // RBAC helper handed to the plugin component: can('delete') →
  // hasPermission(user, 'plugin.<id>.delete'). Default-allow for open (no-identity)
  // installs (no user) so screens keep working (plano "RBAC para Plugins").
  const can = (key) => hasPermission(currentUser, `plugin.${screen.pluginId}.${key}`);
  return html`<${Component} apiBase=${apiBase} screen=${screen} can=${can} currentUser=${currentUser} />`;
}

