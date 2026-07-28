// Tela de configuração do plugin Instagram (config:true).
// Renderizada DENTRO do modal "Configurar" do card em /plugins.
//
// Instagram via login do Facebook: uma Página do Facebook conectada à conta
// profissional do Instagram (graph.facebook.com, Page Access Token). O canal em
// si é criado na tela "Canais" do core (o formulário vem do descriptor do
// provider). Aqui o operador: copia a URL de callback do webhook, confere se a
// Página está assinada nos campos certos e assina com 1 clique.
//
// ⚠️ Pré-requisito da Meta (senão send/subscribe falham em silêncio): a conta
// profissional do Instagram precisa estar CONECTADA à Página do Facebook
// (Página → Configurações → Contas vinculadas → Instagram) e o acesso a
// mensagens do Instagram precisa estar permitido para a Página.
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

export default function InstagramConfig({ apiBase = '/api/plugins/instagram' } = {}) {
  const [info, setInfo] = useState(null);
  const [channels, setChannels] = useState([]);
  const [selected, setSelected] = useState('');
  const [status, setStatus] = useState(null);
  const [diag, setDiag] = useState(null);
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
      // /autoconfigure = registra o Callback URL no app E assina a conta (o mesmo
      // que roda na criação do canal); /subscribe sozinho só faria a 2ª metade.
      const r = await apiFetch(`${apiBase}/autoconfigure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: selected }),
      });
      const d = await r.json();
      if (!d.ok) setError(d.error || 'Falha ao configurar o webhook.');
      else if (d.data && d.data.mode === 'manual') setError(d.data.message || d.data.reason || '');
      await loadStatus(selected);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function diagnose() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const r = await apiFetch(`${apiBase}/diagnose?channel_id=${encodeURIComponent(selected)}`);
      const d = await r.json();
      setDiag(d.ok ? d.data : null);
      if (!d.ok) setError(d.error || 'Falha ao diagnosticar.');
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
        O canal do Instagram é criado na tela <strong class="text-wa-text">Canais</strong>
        (provider "Instagram") conectando a <strong class="text-wa-text">Página do Facebook</strong>
        vinculada à conta do Instagram. O webhook é registrado na Meta automaticamente
        ao criar o canal; volte aqui só se precisar reconferir ou reassinar.
      </div>

      <div class="rounded border border-amber-300 bg-amber-50 text-amber-800 px-3 py-2 text-sm">
        <strong>Pré-requisito:</strong> a conta profissional do Instagram precisa
        estar <strong>conectada à Página do Facebook</strong> (Página →
        Configurações → Contas vinculadas → Instagram) e a Página deve permitir o
        acesso a mensagens do Instagram. Sem isso, o envio e a assinatura falham
        em silêncio.
      </div>

      ${error && html`
        <div class="rounded border border-red-400 bg-red-50 text-red-700 px-3 py-2 text-sm">${error}</div>`}

      ${!channels.length && html`
        <div class="rounded border border-wa-border bg-wa-hover px-3 py-2 text-sm">
          Nenhum canal Instagram criado ainda.
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
            Registrado automaticamente na Meta ao criar o canal. Se precisar colar à
            mão: App Dashboard → Webhooks → produto Instagram, junto com o Verify Token do canal.
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
        <div class="flex gap-2 flex-wrap">
          <button class=${BTN} onClick=${subscribe} disabled=${busy}>
            ${busy ? 'Registrando...' : 'Registrar webhook na Meta'}
          </button>
          <button class="px-3 py-2 rounded text-sm border border-wa-border"
                  onClick=${diagnose} disabled=${busy}>
            ${busy ? '...' : 'Diagnosticar recebimento'}
          </button>
          <button class="px-3 py-2 rounded text-sm border border-wa-border"
                  onClick=${() => loadStatus(selected)} disabled=${busy}>Atualizar</button>
        </div>`}

      ${diag && html`
        <div class="rounded border border-wa-border bg-wa-panel px-3 py-3 text-sm space-y-2">
          <div class="font-medium text-wa-text">${diag.verdict}</div>
          <ul class="space-y-1 text-wa-secondary">
            <li>${diag.token_ok ? '✅' : '❌'} Page token válido${diag.username ? ` (${diag.username})` : ''}${diag.token_error ? ` — ${diag.token_error}` : ''}</li>
            <li>${diag.callback_match === 'ok' ? '✅' : '❌'} Callback apontando pra cá (${diag.callback_match})</li>
            <li>${diag.account_subscribed ? '✅' : '❌'} Página assinada em 'messages'${(diag.subscribed_fields || []).length ? ` (${diag.subscribed_fields.join(', ')})` : ''}</li>
            <li>${diag.recent_inbound > 0 ? '✅' : '⬜'} Webhooks recebidos: ${diag.recent_inbound}</li>
          </ul>
          ${diag.configured_url && diag.callback_match !== 'ok' && html`
            <div class="text-wa-secondary break-all">Callback atual no app: <span class="font-mono">${diag.configured_url}</span></div>`}
        </div>`}

      ${info && html`
        <div class=${HINT}>
          Instagram via login do Facebook (graph.facebook.com) · Graph API
          ${info.graph_api_version} · janela de 24h para texto livre (fora dela, só
          um atendente humano com a tag HUMAN_AGENT) · Page Access Token não expira
          por tempo (sem renovação automática).
        </div>`}
    </div>
  `;
}
