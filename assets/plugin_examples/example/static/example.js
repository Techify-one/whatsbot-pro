// Example plugin screen — Preact + HTM, no build step.
// Imports use the same names as the core importmap.
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

export default function ExampleScreen({ apiBase = '/api/plugins/example' } = {}) {
  const [pings, setPings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${apiBase}/pings`);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'unknown error');
      setPings(data.data || []);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function clear() {
    if (!confirm('Apagar todos os pings registrados?')) return;
    await fetch(`${apiBase}/pings`, { method: 'DELETE' });
    load();
  }

  useEffect(() => { load(); }, []);

  return html`
    <div class="p-6 max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold mb-2">Plugin de Exemplo</h1>
      <p class="text-sm text-gray-500 mb-4">
        Acompanhamento de chamadas à tool <code>example_ping</code>.
      </p>
      <div class="flex gap-2 mb-4">
        <button class="px-3 py-1 bg-blue-600 text-white rounded" onClick=${load}>
          Recarregar
        </button>
        <button class="px-3 py-1 bg-red-600 text-white rounded" onClick=${clear}>
          Limpar
        </button>
      </div>
      ${error && html`<div class="text-red-600 mb-3">Erro: ${error}</div>`}
      ${loading
        ? html`<div>Carregando…</div>`
        : pings.length === 0
          ? html`<div class="text-gray-500">
              Nenhum ping ainda. Peça à IA: "ping de teste do exemplo".
            </div>`
          : html`<table class="w-full border-collapse">
              <thead>
                <tr class="border-b text-left">
                  <th class="py-2">#</th>
                  <th>Telefone</th>
                  <th>Nota</th>
                  <th>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                ${pings.map(p => html`
                  <tr class="border-b" key=${p.id}>
                    <td class="py-2">${p.id}</td>
                    <td>${p.phone}</td>
                    <td>${p.note || '—'}</td>
                    <td>${new Date(p.ts * 1000).toLocaleString()}</td>
                  </tr>
                `)}
              </tbody>
            </table>`
      }
    </div>
  `;
}
