// Tela de configuração do plugin Facebook Messenger (config:true).
// Renderizada DENTRO do modal "Configurar" do card em /plugins.
//
// O canal em si é criado na tela "Canais" do core (o formulário vem do
// descriptor do provider). Aqui o operador: copia a URL de callback do webhook,
// confere se a Página está assinada nos campos certos e assina com 1 clique.
//
// Dark mode: classes semânticas wa-* e .wa-field (legível nos dois temas).
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function authHeaders(extra = {}) {
  const token = localStorage.getItem('whatsbot_token') || '';
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

async function apiFetch(url, init = {}) {
  const headers = authHeaders(init.headers || {});
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    throw new Error('Não autenticado.');
  }
  return res;
}

const LABEL = 'block text-sm font-medium text-wa-text mb-1';
const HINT = 'text-xs text-wa-secondary mt-1';
const BTN = 'px-3 py-2 rounded text-sm bg-wa-teal text-white disabled:opacity-50';

export default function FacebookMessengerConfig({ apiBase = '/api/plugins/facebook_messenger' } = {}) {
  const [info, setInfo] = useState(null);
  const [channels, setChannels] = useState([]);
  const [selected, setSelected] = useState('');
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [ri, rc] = await Promise.all([
          apiFetch(`${apiBase}/info`),
          apiFetch(`${apiBase}/channels`),
        ]);
        const di = await ri.json();
        const dc = await rc.json();
        if (di.ok) setInfo(di.data);
        if (dc.ok) {
          setChannels(dc.data.channels || []);
          if ((dc.data.channels || []).length) setSelected(dc.data.channels[0].id);
        }
      } catch (e) {
        setError(String(e.message || e));
      }
    })();
  }, [apiBase]);

  const channel = channels.find((c) => c.id === selected) || null;

  async function loadStatus(channelId) {
    if (!channelId) return;
    setBusy(true);
    setError(null);
    try {
      const r = await apiFetch(`${apiBase}/webhook-status?channel_id=${encodeURIComponent(channelId)}`);
      const d = await r.json();
      setStatus(d.ok ? d.data : null);
      if (!d.ok) setError(d.error || 'Falha ao consultar o webhook.');
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { if (selected) loadStatus(selected); }, [selected]);

  async function subscribe() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      // /autoconfigure = registra o Callback URL no app E assina a Página (o
      // mesmo que roda na criação do canal); /subscribe sozinho só faria a 2ª.
      const r = await apiFetch(`${apiBase}/autoconfigure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: selected }),
      });
      const d = await r.json();
      if (!d.ok) setError(d.error || 'Falha ao assinar o webhook.');
      else if (d.data && d.data.mode === 'manual') setError(d.data.message || d.data.reason || '');
      await loadStatus(selected);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  function copyUrl() {
    if (!channel || !channel.webhook_url) return;
    navigator.clipboard?.writeText(channel.webhook_url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return html`
    <div class="space-y-4 text-wa-text">
      <div class="text-sm text-wa-secondary">
        O canal do Facebook é criado na tela <strong class="text-wa-text">Canais</strong>
        (provider "Facebook"). Depois de criar, volte aqui para assinar o webhook da Página.
      </div>

      ${error && html`
        <div class="rounded border border-red-400 bg-red-50 text-red-700 px-3 py-2 text-sm">${error}</div>`}

      ${!channels.length && html`
        <div class="rounded border border-wa-border bg-wa-hover px-3 py-2 text-sm">
          Nenhum canal Facebook criado ainda.
        </div>`}

      ${channels.length > 0 && html`
        <div>
          <label class=${LABEL}>Canal</label>
          <select class="wa-field w-full rounded px-3 py-2 text-sm border border-wa-border"
                  value=${selected} onChange=${(e) => setSelected(e.target.value)}>
            ${channels.map((c) => html`<option value=${c.id}>${c.name || c.id}</option>`)}
          </select>
        </div>`}

      ${channel && html`
        <div>
          <label class=${LABEL}>URL de callback do webhook</label>
          <div class="flex gap-2">
            <input class="wa-field flex-1 rounded px-3 py-2 text-sm border border-wa-border"
                   readonly value=${channel.webhook_url || '(configure o endereço público do painel)'} />
            <button class=${BTN} onClick=${copyUrl} disabled=${!channel.webhook_url}>
              ${copied ? 'Copiado!' : 'Copiar'}
            </button>
          </div>
          <div class=${HINT}>
            Cole no App Dashboard da Meta → Messenger → Webhooks, junto com o Verify Token do canal.
          </div>
        </div>`}

      ${status && html`
        <div class="rounded border border-wa-border bg-wa-panel px-3 py-2 text-sm space-y-1">
          <div>
            Assinatura da Página:
            <strong class=${status.subscribed ? 'text-green-600' : 'text-amber-600'}>
              ${status.subscribed ? 'ativa' : 'pendente'}
            </strong>
          </div>
          ${status.reason && html`<div class="text-wa-secondary">${status.reason}</div>`}
          <div class="text-wa-secondary">
            Campos assinados: ${(status.subscribed_fields || []).join(', ') || '—'}
          </div>
          <div class=${status.has_app_secret ? 'text-wa-secondary' : 'text-amber-600'}>
            ${status.has_app_secret
              ? 'App Secret configurado — assinatura dos webhooks validada.'
              : 'Sem App Secret: os webhooks NÃO são validados. Configure no canal.'}
          </div>
        </div>`}

      ${channel && html`
        <div class="flex gap-2">
          <button class=${BTN} onClick=${subscribe} disabled=${busy}>
            ${busy ? 'Registrando...' : 'Registrar webhook na Meta'}
          </button>
          <button class="px-3 py-2 rounded text-sm border border-wa-border"
                  onClick=${() => loadStatus(selected)} disabled=${busy}>Atualizar</button>
        </div>`}

      ${info && html`
        <div class=${HINT}>
          Graph API ${info.graph_api_version} · janela de 24h para texto livre
          (fora dela, só um atendente humano com a tag HUMAN_AGENT).
        </div>`}
    </div>
  `;
}
