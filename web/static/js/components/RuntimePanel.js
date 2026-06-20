// Runtime observability panel (plano 09 Fase 5) — full-page admin view of the
// background task supervisor + managed subprocess service. Health is in-memory
// (P30): we poll the read-only endpoints. Dark-mode-safe (wa-* classes).

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { getRuntimeTasks, getRuntimeSubprocesses } from '../services/api.js';

const html = htm.bind(h);

const POLL_MS = 4000;

const STATE_COLORS = {
  running: 'text-green-600',
  completed: 'text-wa-secondary',
  stopped: 'text-wa-secondary',
  crashed: 'text-red-500',
  registered: 'text-wa-secondary',
};

function StateBadge({ state }) {
  const cls = STATE_COLORS[state] || 'text-wa-text';
  return html`<span class="text-[12px] font-medium ${cls}">${state}</span>`;
}

export default function RuntimePanel() {
  const [tasks, setTasks] = useState([]);
  const [procs, setProcs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function load() {
    const [t, p] = await Promise.all([getRuntimeTasks(), getRuntimeSubprocesses()]);
    if (t && t.ok) setTasks(t.data || []);
    if (p && p.ok) setProcs(p.data || []);
    if ((t && !t.ok) || (p && !p.ok)) setError('Falha ao carregar estado do runtime.');
    else setError('');
    setLoading(false);
  }

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, []);

  return html`
    <div>
      <p class="text-[13px] text-wa-secondary mb-4">
        Estado das tasks de fundo e subprocessos gerenciados. Atualiza a cada ${POLL_MS / 1000}s.
      </p>
      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}
      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      <div class="mb-6">
        <div class="text-[14px] font-medium text-wa-text mb-2">Tasks de fundo (${tasks.length})</div>
        <div class="bg-wa-panel border border-wa-border rounded-lg overflow-hidden">
          <table class="w-full text-[13px]">
            <thead>
              <tr class="text-wa-secondary text-left border-b border-wa-border">
                <th class="px-3 py-2 font-medium">Nome</th>
                <th class="px-3 py-2 font-medium">Dono</th>
                <th class="px-3 py-2 font-medium">Política</th>
                <th class="px-3 py-2 font-medium">Estado</th>
                <th class="px-3 py-2 font-medium">Restarts</th>
                <th class="px-3 py-2 font-medium">Último erro</th>
              </tr>
            </thead>
            <tbody>
              ${tasks.map(t => html`
                <tr key=${t.name} class="border-b border-wa-border last:border-0">
                  <td class="px-3 py-2 text-wa-text font-mono">${t.name}</td>
                  <td class="px-3 py-2 text-wa-secondary">${t.owner}</td>
                  <td class="px-3 py-2 text-wa-secondary">${t.policy}</td>
                  <td class="px-3 py-2"><${StateBadge} state=${t.state} /></td>
                  <td class="px-3 py-2 text-wa-text">${t.restarts}</td>
                  <td class="px-3 py-2 text-wa-secondary truncate max-w-[260px]">${t.last_error || '—'}</td>
                </tr>
              `)}
              ${tasks.length === 0 && !loading ? html`
                <tr><td colspan="6" class="px-3 py-4 text-center text-wa-secondary">Nenhuma task registrada.</td></tr>
              ` : null}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <div class="text-[14px] font-medium text-wa-text mb-2">Subprocessos gerenciados (${procs.length})</div>
        <div class="bg-wa-panel border border-wa-border rounded-lg overflow-hidden">
          <table class="w-full text-[13px]">
            <thead>
              <tr class="text-wa-secondary text-left border-b border-wa-border">
                <th class="px-3 py-2 font-medium">Nome</th>
                <th class="px-3 py-2 font-medium">Dono</th>
                <th class="px-3 py-2 font-medium">PID</th>
                <th class="px-3 py-2 font-medium">Rodando</th>
                <th class="px-3 py-2 font-medium">Restarts</th>
              </tr>
            </thead>
            <tbody>
              ${procs.map(p => html`
                <tr key=${p.name} class="border-b border-wa-border last:border-0">
                  <td class="px-3 py-2 text-wa-text font-mono">${p.name}</td>
                  <td class="px-3 py-2 text-wa-secondary">${p.owner}</td>
                  <td class="px-3 py-2 text-wa-text">${p.pid ?? '—'}</td>
                  <td class="px-3 py-2">
                    <span class="text-[12px] font-medium ${p.running ? 'text-green-600' : 'text-wa-secondary'}">
                      ${p.running ? 'sim' : 'não'}
                    </span>
                  </td>
                  <td class="px-3 py-2 text-wa-text">${p.restarts}</td>
                </tr>
              `)}
              ${procs.length === 0 && !loading ? html`
                <tr><td colspan="5" class="px-3 py-4 text-center text-wa-secondary">Nenhum subprocesso de plugin ativo. (O GOWA é gerenciado à parte.)</td></tr>
              ` : null}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;
}
