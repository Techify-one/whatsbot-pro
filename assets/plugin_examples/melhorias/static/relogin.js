// Modal de relogin OAuth do executor Claude (plano 51 · 04 F5) — porta do
// ai-chat-relogin-modal do nexus. Fases: starting → awaiting-code →
// completing → success (+ error). Cancelar/fechar aborta a sessão do CLI.

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

export function ReloginModal({ apiJson, apiBase, onClose, onSuccess }) {
  const [phase, setPhase] = useState('starting');
  const [session, setSession] = useState(null); // {sessionId, url}
  const [code, setCode] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const r = await apiJson(`${apiBase}/admin/relogin/start`, { method: 'POST' });
        const data = (r.body && r.body.data) || {};
        if (r.ok && (data.sessionId || data.session_id)) {
          setSession({ sessionId: data.sessionId || data.session_id, url: data.url });
          setPhase('awaiting-code');
        } else {
          setError((r.body && r.body.error) || 'Falha ao iniciar o relogin.');
          setPhase('error');
        }
      } catch (e) { setError(String(e.message || e)); setPhase('error'); }
    })();
  }, []);

  async function complete() {
    if (!session || !code.trim()) return;
    setPhase('completing'); setError('');
    try {
      const r = await apiJson(`${apiBase}/admin/relogin/complete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId: session.sessionId, code: code.trim() }),
      });
      if (r.ok) { setPhase('success'); onSuccess && onSuccess(); }
      else { setError((r.body && r.body.error) || 'Código recusado.'); setPhase('awaiting-code'); }
    } catch (e) { setError(String(e.message || e)); setPhase('awaiting-code'); }
  }

  async function abort() {
    if (session && phase !== 'success') {
      try {
        await apiJson(`${apiBase}/admin/relogin/abort`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId: session.sessionId }),
        });
      } catch (_) { /* best-effort */ }
    }
    onClose();
  }

  return html`
    <div class="fixed inset-0 z-[160] bg-black/40 flex items-center justify-center p-4" onClick=${abort}>
      <div class="bg-wa-panel rounded-lg shadow-xl w-[460px] max-w-[92vw] p-[22px]"
        onClick=${(e) => e.stopPropagation()}>
        <div class="text-[15px] font-semibold text-wa-text mb-3">
          Renovar sessão do Claude (executor de IA)
        </div>
        ${phase === 'starting' ? html`
          <div class="flex items-center gap-3 text-[13px] text-wa-secondary py-4">
            <div class="w-5 h-5 border-2 border-wa-teal border-t-transparent rounded-full animate-spin"></div>
            Iniciando sessão de login no servidor…
          </div>` : ''}
        ${phase === 'awaiting-code' || phase === 'completing' ? html`
          <div class="text-[13px] text-wa-text mb-2">
            1. Abra a URL abaixo, entre com a conta Claude.ai e autorize.
          </div>
          <div class="bg-wa-bg border border-wa-border rounded p-2 mb-3 text-[12px] break-all">
            <a href=${session && session.url} target="_blank" rel="noopener"
              class="text-wa-teal hover:underline">${session && session.url}</a>
          </div>
          <div class="text-[13px] text-wa-text mb-2">2. Cole aqui o código exibido na página:</div>
          <input class="wa-field w-full rounded px-3 py-2 text-[13px] mb-2" placeholder="Código"
            value=${code} onInput=${(e) => setCode(e.target.value)}
            onKeyDown=${(e) => { if (e.key === 'Enter') complete(); }} />
          ${error ? html`<div class="text-[12px] text-red-500 mb-2">${error}</div>` : ''}
          <div class="flex justify-end gap-2 mt-2">
            <button onClick=${abort}
              class="px-4 py-2 rounded-full border border-wa-border text-wa-text text-[13px] hover:bg-wa-hover">Cancelar</button>
            <button onClick=${complete} disabled=${phase === 'completing' || !code.trim()}
              class="px-4 py-2 rounded-full bg-wa-teal text-white text-[13px] font-medium hover:opacity-90 disabled:opacity-50">
              ${phase === 'completing' ? 'Confirmando…' : 'Confirmar código'}</button>
          </div>` : ''}
        ${phase === 'success' ? html`
          <div class="text-[13px] text-wa-teal font-medium py-3">
            ✓ Sessão renovada. Abra uma nova conversa para usar o token fresco.
          </div>
          <div class="flex justify-end">
            <button onClick=${onClose}
              class="px-4 py-2 rounded-full bg-wa-teal text-white text-[13px] font-medium hover:opacity-90">Fechar</button>
          </div>` : ''}
        ${phase === 'error' ? html`
          <div class="text-[12px] text-red-500 mb-3">${error}</div>
          <div class="flex justify-end">
            <button onClick=${onClose}
              class="px-4 py-2 rounded-full border border-wa-border text-wa-text text-[13px] hover:bg-wa-hover">Fechar</button>
          </div>` : ''}
      </div>
    </div>`;
}
