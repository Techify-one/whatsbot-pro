// Tela "Configurar" do plugin (screen config:true — renderizada dentro do modal
// Configurar do card em /plugins, no lugar do formulário declarativo).
//
// Existe por duas coisas que o form genérico não sabe fazer:
//   1. a CHAVE de ingestão precisa ser mascarada. O `GET /api/plugins/{id}/settings`
//      devolve valores em claro, então a chave mora fora dele, em rota própria com
//      sentinela "***" (mesmo padrão do plugin melhorias);
//   2. o diagnóstico — conexão, schema e a FILA de envio — é operação, não config:
//      é aqui que se descobre por que um evento não chegou no CDP.
//
// As demais opções (DSN, URL, ritmo, modo seco) seguem no formulário declarativo,
// na aba "Configurações" abaixo.
import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { authHeaders } from '/static/js/services/api.js';

const html = htm.bind(h);

const API = '/api/plugins/trackify';
const MASK = '***';

async function req(method, path, body) {
  const init = { method, headers: authHeaders() };
  if (body !== undefined) {
    init.headers = { ...init.headers, 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const r = await fetch(`${API}${path}`, init);
  try { return await r.json(); } catch (_) { return { ok: false, error: `HTTP ${r.status}` }; }
}

const STATUS_LABEL = {
  pending: 'Na fila', sending: 'Enviando', sent: 'Enviado',
  blocked: 'Bloqueado', failed: 'Falhou', dropped: 'Descartado',
};

function Dot({ ok, warn }) {
  const cls = ok ? 'bg-wa-teal' : warn ? 'bg-amber-500' : 'bg-red-500';
  return html`<span class=${`inline-block w-2 h-2 rounded-full ${cls}`} />`;
}

function Health({ data, onRefresh, busy }) {
  if (!data) return null;
  const { configured, reachable, schema_ok, schema_missing, message,
          base_url_set, mirror_enabled } = data;
  return html`
    <section class="border border-wa-border rounded-xl p-4 bg-wa-panel">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-sm font-semibold text-wa-text">Conexão com o Trackify</h3>
        <button type="button" onClick=${onRefresh} disabled=${busy}
          class="text-[12px] px-2.5 py-1 rounded-md bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-50 transition-colors">
          ${busy ? 'Testando…' : 'Testar agora'}
        </button>
      </div>
      <ul class="space-y-1.5 text-[13px]">
        <li class="flex items-center gap-2 text-wa-text">
          <${Dot} ok=${configured} /> DSN do Nexus ${configured ? 'configurado' : 'NÃO configurado'}
        </li>
        <li class="flex items-center gap-2 text-wa-text">
          <${Dot} ok=${reachable} /> Banco ${reachable ? 'alcançável' : 'inalcançável'}
          ${!reachable && message ? html`<span class="text-wa-secondary">— ${message}</span>` : null}
        </li>
        <li class="flex items-center gap-2 text-wa-text">
          <${Dot} ok=${schema_ok} /> Estrutura das tabelas
          ${schema_ok ? ' compatível' : ' incompatível'}
          ${schema_missing && schema_missing.length
            ? html`<span class="text-wa-secondary">— falta: ${schema_missing.join(', ')}</span>` : null}
        </li>
        <li class="flex items-center gap-2 text-wa-text">
          <${Dot} ok=${base_url_set} warn=${true} />
          Link "Abrir no Trackify" ${base_url_set ? 'ativo' : 'sem URL (botão escondido)'}
        </li>
        <li class="flex items-center gap-2 text-wa-text">
          <${Dot} ok=${mirror_enabled} warn=${true} />
          Espelho de eventos ${mirror_enabled ? 'LIGADO' : 'desligado'}
        </li>
      </ul>
    </section>`;
}

function KeyField({ state, onSave, busy }) {
  const [value, setValue] = useState('');
  const [msg, setMsg] = useState('');
  useEffect(() => { setValue(state && state.set ? MASK : ''); }, [state && state.set]);

  async function save(e) {
    e.preventDefault();
    setMsg('');
    const r = await onSave(value);
    setMsg(r && r.ok
      ? (r.data && r.data.unchanged ? 'Chave mantida.' : 'Chave salva.')
      : 'Não foi possível salvar.');
  }

  return html`
    <section class="border border-wa-border rounded-xl p-4 bg-wa-panel">
      <h3 class="text-sm font-semibold text-wa-text mb-1">Chave de ingestão</h3>
      <p class="text-[12px] text-wa-secondary mb-3">
        A mesma chave que está em <strong>config.auth.apiKey</strong> do canal
        <code>whatsbot</code> no Trackify. Enviada no cabeçalho
        <code>X-Trackify-Key</code>. Deixe o <code>***</code> intacto para não alterá-la.
      </p>
      <form onSubmit=${save} class="flex flex-wrap items-center gap-2">
        <input type="text" class="wa-field px-2.5 py-1.5 text-sm rounded-md flex-1 min-w-[240px]"
          placeholder="cole a chave aqui" value=${value}
          onInput=${(e) => setValue(e.target.value)} />
        <button type="submit" disabled=${busy}
          class="px-3 py-1.5 rounded-md text-[12px] bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 disabled:opacity-50 transition-colors">
          Salvar chave
        </button>
        ${msg ? html`<span class="text-[12px] text-wa-secondary">${msg}</span>` : null}
      </form>
    </section>`;
}

function Queue({ data, onRefresh, onRequeue, busy }) {
  if (!data) return null;
  const st = data.stats || {};
  const rows = data.rows || [];
  const counters = ['pending', 'sending', 'sent', 'blocked', 'failed', 'dropped']
    .filter((k) => st[k]);

  return html`
    <section class="border border-wa-border rounded-xl p-4 bg-wa-panel">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-sm font-semibold text-wa-text">Fila de envio</h3>
        <button type="button" onClick=${onRefresh} disabled=${busy}
          class="text-[12px] px-2.5 py-1 rounded-md bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-50 transition-colors">
          Atualizar
        </button>
      </div>

      ${counters.length === 0
        ? html`<p class="text-[13px] text-wa-secondary">A fila está vazia — nenhum evento enfileirado ainda.</p>`
        : html`
          <div class="flex flex-wrap gap-4 mb-3">
            ${counters.map((k) => html`
              <div key=${k}>
                <div class="text-[11px] text-wa-secondary">${STATUS_LABEL[k] || k}</div>
                <div class="text-sm font-semibold text-wa-text">${st[k]}</div>
              </div>`)}
            ${st.backlog_age_s ? html`
              <div>
                <div class="text-[11px] text-wa-secondary">Atraso da fila</div>
                <div class="text-sm font-semibold text-wa-text">${Math.round(st.backlog_age_s / 60)} min</div>
              </div>` : null}
          </div>`}

      ${rows.length === 0
        ? html`<p class="text-[12px] text-wa-secondary">Nada preso: sem eventos bloqueados ou falhos.</p>`
        : html`
          <div class="overflow-x-auto">
            <table class="w-full text-[12px]">
              <thead>
                <tr class="text-left text-wa-secondary border-b border-wa-border">
                  <th class="py-1.5 pr-3">Tipo</th>
                  <th class="py-1.5 pr-3">Situação</th>
                  <th class="py-1.5 pr-3">Tentativas</th>
                  <th class="py-1.5 pr-3">Erro</th>
                  <th class="py-1.5"></th>
                </tr>
              </thead>
              <tbody>
                ${rows.map((r) => html`
                  <tr key=${r.id} class="border-b border-wa-border/50">
                    <td class="py-1.5 pr-3 text-wa-text whitespace-nowrap">${r.kind}</td>
                    <td class="py-1.5 pr-3 text-wa-text">
                      ${STATUS_LABEL[r.status] || r.status}
                      ${r.last_http_status ? html` <span class="text-wa-secondary">(HTTP ${r.last_http_status})</span>` : null}
                    </td>
                    <td class="py-1.5 pr-3 text-wa-text">${r.attempts}</td>
                    <td class="py-1.5 pr-3 text-wa-secondary break-all">${r.last_error || '—'}</td>
                    <td class="py-1.5 text-right">
                      <button type="button" onClick=${() => onRequeue(r.id)} disabled=${busy}
                        class="px-2 py-1 rounded-md text-[11px] bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 disabled:opacity-50 transition-colors whitespace-nowrap">
                        Reprocessar
                      </button>
                    </td>
                  </tr>`)}
              </tbody>
            </table>
          </div>`}
    </section>`;
}

export default function TrackifyConfig() {
  const [health, setHealth] = useState(null);
  const [keyState, setKeyState] = useState(null);
  const [queue, setQueue] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadHealth = useCallback(async () => {
    setBusy(true);
    try {
      const r = await req('GET', '/health');
      if (r && r.ok) setHealth(r.data);
    } finally { setBusy(false); }
  }, []);

  const loadKey = useCallback(async () => {
    const r = await req('GET', '/ingestion-key');
    if (r && r.ok) setKeyState(r.data);
  }, []);

  const loadQueue = useCallback(async () => {
    setBusy(true);
    try {
      const r = await req('GET', '/outbox');
      if (r && r.ok) setQueue(r.data);
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { loadHealth(); loadKey(); loadQueue(); }, [loadHealth, loadKey, loadQueue]);

  const saveKey = useCallback(async (value) => {
    setBusy(true);
    try {
      const r = await req('PUT', '/ingestion-key', { api_key: value });
      await loadKey();
      return r;
    } finally { setBusy(false); }
  }, [loadKey]);

  const requeue = useCallback(async (id) => {
    setBusy(true);
    try {
      await req('POST', `/outbox/${id}/requeue`);
      await loadQueue();
    } finally { setBusy(false); }
  }, [loadQueue]);

  return html`
    <div class="space-y-4">
      <${Health} data=${health} onRefresh=${loadHealth} busy=${busy} />
      <${KeyField} state=${keyState} onSave=${saveKey} busy=${busy} />
      <${Queue} data=${queue} onRefresh=${loadQueue} onRequeue=${requeue} busy=${busy} />
    </div>`;
}
