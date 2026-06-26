// Tela de configuração do plugin WhatsApp Cloud API (config:true).
// Renderizada DENTRO do modal "Configurar" do card em /plugins.
// É um form de AJUDA/documentação do provider: o canal em si é criado e
// gerenciado na tela "Canais" do core. Aqui o operador anota os dados da Meta
// e copia a URL de webhook para colar no painel da Meta.
//
// Dark mode: usa classes semânticas wa-* e .wa-field (legível nos dois temas).
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

const FIELD = 'wa-field w-full rounded px-3 py-2 text-sm border border-wa-border';
const LABEL = 'block text-sm font-medium text-wa-text mb-1';
const HINT = 'text-xs text-wa-secondary mt-1';

export default function WhatsAppCloudConfig({ apiBase = '/api/plugins/whatsapp_cloud' } = {}) {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  // Form é apenas de anotação/ajuda — segredos reais ficam no registry de
  // canais do core. Persistimos um rascunho local por device.
  const [form, setForm] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('whatsbot_wacloud_draft') || '{}');
    } catch {
      return {};
    }
  });
  const [channelId, setChannelId] = useState(() =>
    localStorage.getItem('whatsbot_wacloud_channel_id') || ''
  );
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await apiFetch(`${apiBase}/info`);
        const data = await r.json();
        if (data.ok) setInfo(data.data);
      } catch (e) {
        setError(String(e.message || e));
      }
    })();
  }, [apiBase]);

  function update(key, value) {
    const next = { ...form, [key]: value };
    setForm(next);
    try {
      localStorage.setItem('whatsbot_wacloud_draft', JSON.stringify(next));
    } catch {}
  }

  function updateChannelId(value) {
    setChannelId(value);
    try {
      localStorage.setItem('whatsbot_wacloud_channel_id', value);
    } catch {}
  }

  const webhookUrl = `${location.origin}/api/webhook/whatsapp_cloud/${channelId || '{channel_id}'}`;

  async function copyWebhook() {
    try {
      await navigator.clipboard.writeText(webhookUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  }

  return html`
    <div class="p-5 max-w-2xl mx-auto text-wa-text">
      <h2 class="text-xl font-bold mb-1">WhatsApp Cloud API</h2>
      <p class="text-sm text-wa-secondary mb-4">
        Provider oficial da Meta (Graph API), sem QR. O canal é criado e
        gerenciado na tela <strong>Canais</strong> do WhatsBot — este formulário
        é apenas ajuda para reunir os dados da Meta e a URL de webhook.
      </p>

      ${error && html`
        <div class="mb-3 px-3 py-2 rounded bg-red-100 text-red-700 text-sm">
          ${error}
        </div>`}

      ${info && html`
        <div class="mb-4 px-3 py-2 rounded bg-wa-bg border border-wa-border text-xs text-wa-secondary">
          Graph API: <strong>${info.graph_api_version}</strong> ·
          Templates (HSM): ${info.capabilities?.templates ? 'sim' : 'não'} ·
          Grupos: ${info.capabilities?.groups ? 'sim' : 'não'}
        </div>`}

      <div class="space-y-4">
        <div>
          <label class=${LABEL}>Channel ID (da tela Canais)</label>
          <input
            class=${FIELD}
            placeholder="ex.: wacloud-1"
            value=${channelId}
            onInput=${(e) => updateChannelId(e.target.value)}
          />
          <div class=${HINT}>
            O ID do canal criado em <strong>Canais</strong>. Usado para montar a
            URL do webhook abaixo.
          </div>
        </div>

        <div>
          <label class=${LABEL}>Phone Number ID</label>
          <input
            class=${FIELD}
            placeholder="ex.: 1234567890"
            value=${form.phone_number_id || ''}
            onInput=${(e) => update('phone_number_id', e.target.value)}
          />
          <div class=${HINT}>WhatsApp Manager → API Setup.</div>
        </div>

        <div>
          <label class=${LABEL}>WABA ID</label>
          <input
            class=${FIELD}
            placeholder="WhatsApp Business Account ID"
            value=${form.waba_id || ''}
            onInput=${(e) => update('waba_id', e.target.value)}
          />
          <div class=${HINT}>
            Habilita templates (HSM). Cadastre-o de fato nas credenciais do canal
            pela tela <strong>Canais</strong> — aqui é só rascunho.
          </div>
        </div>

        <div>
          <label class=${LABEL}>Access Token</label>
          <input
            type="password"
            class=${FIELD}
            placeholder="EAAG... (token permanente)"
            value=${form.access_token || ''}
            onInput=${(e) => update('access_token', e.target.value)}
          />
          <div class=${HINT}>
            Token permanente do System User. Guarde nas credenciais do canal — não
            é salvo em texto claro.
          </div>
        </div>

        <div>
          <label class=${LABEL}>Verify Token</label>
          <input
            class=${FIELD}
            placeholder="defina um segredo qualquer"
            value=${form.verify_token || ''}
            onInput=${(e) => update('verify_token', e.target.value)}
          />
          <div class=${HINT}>
            Você escolhe esta string e cola igual na Meta (Webhook → Verify token).
          </div>
        </div>

        <div>
          <label class=${LABEL}>URL de webhook (cole na Meta)</label>
          <div class="flex gap-2">
            <input class=${FIELD + ' font-mono'} readonly value=${webhookUrl} />
            <button
              class="shrink-0 px-3 py-2 rounded text-sm bg-wa-teal text-white hover:bg-wa-tealDark"
              onClick=${copyWebhook}
            >${copied ? 'Copiado!' : 'Copiar'}</button>
          </div>
          <div class=${HINT}>
            Meta → Configuração do app → WhatsApp → Configuração → Webhook
            (Callback URL). Assine o campo <strong>messages</strong>.
          </div>
        </div>
      </div>

      <div class="mt-5 px-3 py-2 rounded bg-wa-bg border border-wa-border text-xs text-wa-secondary">
        Dica: anote estes valores e cadastre as credenciais
        (<code>phone_number_id</code>, <code>waba_id</code>,
        <code>access_token</code>, <code>verify_token</code>) no canal pela tela
        <strong>Canais</strong>. Os campos acima ficam só neste navegador como
        rascunho.
      </div>
    </div>
  `;
}
