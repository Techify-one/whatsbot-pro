// Webhooks de SAÍDA (fase 8 do plano de API).
//
// Cadastrar para onde os eventos da instalação são empurrados, escolher quais,
// testar na hora e ver as últimas entregas. O segredo do HMAC aparece uma única
// vez (na criação ou ao rotacionar) — o servidor não o devolve depois.
//
// ⚠️ Não confundir com o webhook de ENTRADA (o provedor nos chamando); esta tela
// é o contrário.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  getWebhooks, createWebhook, updateWebhook, testWebhook,
  rotateWebhookSecret, deleteWebhook, getWebhookDeliveries,
} from '../services/api.js';

const html = htm.bind(h);

function fmtDate(ts) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleString('pt-BR'); } catch (_) { return '—'; }
}

const STATUS_LABEL = {
  pending: 'Na fila',
  failed: 'Vai tentar de novo',
  delivered: 'Entregue',
  dead: 'Desistiu',
};

function SecretBanner({ secret, onDismiss }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (_) { /* clipboard bloqueado */ }
  };
  return html`
    <div class="mb-4 rounded-lg border border-wa-teal bg-wa-teal/10 p-4">
      <div class="text-[14px] font-semibold text-wa-text mb-1">Guarde o segredo agora</div>
      <div class="text-[13px] text-wa-secondary mb-3">
        Use-o para conferir o cabeçalho <code class="font-mono">X-Whatsbot-Signature-256</code>
        (<code class="font-mono">sha256=HMAC_SHA256(segredo, corpo)</code>, sobre os
        bytes crus recebidos — re-serializar o JSON quebra a comparação). Ele não
        aparece de novo; se perder, rotacione.
      </div>
      <div class="flex items-center gap-2">
        <input class="wa-field flex-1 rounded px-2 py-1.5 font-mono text-[13px]"
               readonly value=${secret} onClick=${e => e.target.select()} />
        <button class="rounded bg-wa-teal px-3 py-1.5 text-[13px] text-white"
                onClick=${copy}>${copied ? 'Copiado!' : 'Copiar'}</button>
        <button class="rounded border border-wa-border px-3 py-1.5 text-[13px] text-wa-text"
                onClick=${onDismiss}>Fechar</button>
      </div>
    </div>`;
}

function EventPicker({ all, selected, onToggle, wildcard, onWildcard }) {
  return html`
    <div class="rounded border border-wa-border bg-wa-bg p-3">
      <label class="flex items-center gap-2 text-[13px] text-wa-text mb-2">
        <input type="checkbox" checked=${wildcard} onChange=${e => onWildcard(e.target.checked)} />
        <span>Todos os eventos exportáveis (<code class="font-mono">*</code>)</span>
      </label>
      <div class="text-[12px] text-wa-secondary mb-2">
        O curinga cobre a lista curada abaixo — nunca "qualquer coisa do barramento".
        Um evento de plugin precisa ser nomeado (ex.: <code class="font-mono">protocolos.*</code>)
        no campo livre.
      </div>
      <div class=${`grid grid-cols-2 gap-1 max-h-56 overflow-y-auto ${wildcard ? 'opacity-40 pointer-events-none' : ''}`}>
        ${all.map(ev => html`
          <label class="flex items-center gap-2 text-[12px] text-wa-text">
            <input type="checkbox" checked=${selected.includes(ev)}
                   onChange=${() => onToggle(ev)} />
            <span class="font-mono">${ev}</span>
          </label>`)}
      </div>
    </div>`;
}

function DeliveriesPanel({ endpointId }) {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    let alive = true;
    getWebhookDeliveries(endpointId).then(r => {
      if (alive && r && r.ok) setRows((r.data && r.data.items) || []);
    });
    return () => { alive = false; };
  }, [endpointId]);
  if (!rows.length) {
    return html`<div class="px-3 py-2 text-[12px] text-wa-secondary">Nenhuma entrega ainda.</div>`;
  }
  return html`
    <div class="max-h-64 overflow-y-auto">
      <table class="w-full text-[12px]">
        <tbody>
          ${rows.map(d => html`
            <tr class="border-t border-wa-border">
              <td class="px-3 py-1 font-mono text-wa-secondary">${d.event}</td>
              <td class="px-3 py-1 text-wa-text">${STATUS_LABEL[d.status] || d.status}</td>
              <td class="px-3 py-1 text-wa-secondary">${d.response_status || '—'}</td>
              <td class="px-3 py-1 text-wa-secondary">${d.attempts}×</td>
              <td class="px-3 py-1 text-wa-secondary">${fmtDate(d.updated_at)}</td>
              <td class="px-3 py-1 text-wa-secondary truncate max-w-[240px]" title=${d.last_error || ''}>
                ${d.last_error || ''}
              </td>
            </tr>`)}
        </tbody>
      </table>
    </div>`;
}

export default function WebhooksManager() {
  const [items, setItems] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [secret, setSecret] = useState('');
  const [openId, setOpenId] = useState(null);

  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [selected, setSelected] = useState([]);
  const [wildcard, setWildcard] = useState(true);
  const [extra, setExtra] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    const r = await getWebhooks();
    if (r && r.ok) {
      setItems((r.data && r.data.items) || []);
      setCatalog((r.data && r.data.exportable_events) || []);
    }
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const submit = async () => {
    setError('');
    const extras = extra.split(',').map(s => s.trim()).filter(Boolean);
    const events = wildcard ? ['*', ...extras] : [...selected, ...extras];
    if (!events.length) { setError('Escolha ao menos um evento.'); return; }
    setBusy(true);
    const res = await createWebhook({ url: url.trim(), description: description.trim(), events });
    setBusy(false);
    if (res && res.ok) {
      setSecret(res.data.secret || '');
      setUrl(''); setDescription(''); setExtra('');
      load();
      return;
    }
    setError((res && res.error) || 'Falha ao cadastrar o webhook.');
  };

  const toggleEnabled = async (row) => {
    await updateWebhook(row.id, { enabled: !row.enabled });
    load();
  };

  const doTest = async (row) => {
    const res = await testWebhook(row.id);
    window.alert(res && res.ok
      ? `O destino respondeu ${res.data.status}. Tudo certo.`
      : `Falhou: ${(res && res.error) || 'sem resposta'}`);
    load();
  };

  const rotate = async (row) => {
    if (!window.confirm(
      'Gerar um segredo novo?\n\nO antigo deixa de assinar imediatamente — ' +
      'atualize o destino antes que a próxima entrega saia.')) return;
    const res = await rotateWebhookSecret(row.id);
    if (res && res.ok) setSecret(res.data.secret || '');
    load();
  };

  const remove = async (row) => {
    if (!window.confirm(`Remover o webhook para ${row.url}?`)) return;
    await deleteWebhook(row.id);
    load();
  };

  return html`
    <div class="space-y-4">
      <div class="rounded-lg border border-wa-border bg-wa-panel p-4">
        <div class="text-[14px] font-semibold text-wa-text mb-1">Cadastrar um webhook</div>
        <div class="text-[13px] text-wa-secondary mb-3">
          O WhatsBot faz <code class="font-mono">POST</code> no seu endpoint a cada
          evento assinado, com o corpo assinado por HMAC. Falha é re-tentada com
          espera crescente (30s → 6h); depois disso a entrega fica registrada como
          "desistiu", nunca some calada.
        </div>
        ${secret && html`<${SecretBanner} secret=${secret} onDismiss=${() => setSecret('')} />`}
        ${error && html`
          <div class="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-[13px] text-red-700">
            ${error}
          </div>`}
        <div class="space-y-3">
          <div class="flex flex-wrap gap-3">
            <label class="flex-1 min-w-[260px]">
              <span class="block text-[12px] text-wa-secondary mb-1">URL de destino</span>
              <input class="wa-field w-full rounded px-2 py-1.5 text-[14px]"
                     placeholder="https://meu-crm.exemplo/webhooks/whatsbot"
                     value=${url} onInput=${e => setUrl(e.target.value)} />
            </label>
            <label class="flex-1 min-w-[200px]">
              <span class="block text-[12px] text-wa-secondary mb-1">Descrição (opcional)</span>
              <input class="wa-field w-full rounded px-2 py-1.5 text-[14px]"
                     value=${description} onInput=${e => setDescription(e.target.value)} />
            </label>
          </div>
          <${EventPicker} all=${catalog} selected=${selected} wildcard=${wildcard}
            onWildcard=${setWildcard}
            onToggle=${ev => setSelected(s => s.includes(ev) ? s.filter(x => x !== ev) : [...s, ev])} />
          <label class="block">
            <span class="block text-[12px] text-wa-secondary mb-1">
              Eventos de plugin (separados por vírgula, curinga aceito)
            </span>
            <input class="wa-field w-full rounded px-2 py-1.5 text-[14px] font-mono"
                   placeholder="protocolos.*, retornos.agendado"
                   value=${extra} onInput=${e => setExtra(e.target.value)} />
          </label>
          <button class="rounded bg-wa-teal px-4 py-1.5 text-[14px] text-white disabled:opacity-50"
                  disabled=${busy} onClick=${submit}>
            ${busy ? 'Cadastrando…' : 'Cadastrar webhook'}
          </button>
        </div>
      </div>

      <div class="space-y-3">
        ${loading && html`<div class="text-[13px] text-wa-secondary">Carregando…</div>`}
        ${!loading && items.length === 0 && html`
          <div class="rounded-lg border border-wa-border bg-wa-panel p-4 text-[13px] text-wa-secondary">
            Nenhum webhook cadastrado.
          </div>`}
        ${items.map(w => html`
          <div class="rounded-lg border border-wa-border bg-wa-panel">
            <div class="flex flex-wrap items-center gap-3 p-3">
              <div class="flex-1 min-w-[240px]">
                <div class="font-mono text-[13px] text-wa-text break-all">${w.url}</div>
                <div class="text-[12px] text-wa-secondary">
                  ${w.description || 'Sem descrição'} · ${(w.events || []).join(', ')}
                </div>
                <div class="text-[12px] text-wa-secondary">
                  Última entrega: ${fmtDate(w.last_delivery_at)}
                  ${w.last_status ? ` (HTTP ${w.last_status})` : ''}
                  ${w.failure_streak > 0 ? ` · ${w.failure_streak} falhas seguidas` : ''}
                </div>
                ${w.disabled_reason && html`
                  <div class="text-[12px] text-red-600">${w.disabled_reason}</div>`}
              </div>
              <div class="flex items-center gap-2">
                <span class=${w.enabled ? 'text-[12px] text-wa-teal' : 'text-[12px] text-wa-secondary'}>
                  ${w.enabled ? 'Ativo' : 'Desligado'}
                </span>
                <button class="rounded border border-wa-border px-2 py-1 text-[12px] text-wa-text"
                        onClick=${() => toggleEnabled(w)}>${w.enabled ? 'Desligar' : 'Ligar'}</button>
                <button class="rounded border border-wa-border px-2 py-1 text-[12px] text-wa-text"
                        onClick=${() => doTest(w)}>Testar</button>
                <button class="rounded border border-wa-border px-2 py-1 text-[12px] text-wa-text"
                        onClick=${() => rotate(w)}>Novo segredo</button>
                <button class="rounded border border-wa-border px-2 py-1 text-[12px] text-wa-text"
                        onClick=${() => setOpenId(openId === w.id ? null : w.id)}>
                  ${openId === w.id ? 'Ocultar entregas' : 'Entregas'}
                </button>
                <button class="rounded border border-wa-border px-2 py-1 text-[12px] text-red-600"
                        onClick=${() => remove(w)}>Remover</button>
              </div>
            </div>
            ${openId === w.id && html`
              <div class="border-t border-wa-border"><${DeliveriesPanel} endpointId=${w.id} /></div>`}
          </div>`)}
      </div>
    </div>`;
}
