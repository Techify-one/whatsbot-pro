// Tela de configuração do plugin Widget de site (config:true).
// Renderizada DENTRO do modal "Configurar" do card em /plugins.
// O canal em si é criado/gerenciado na tela "Canais" do core (provider
// "Site (Widget)"). Aqui o operador copia o snippet de instalação de cada
// widget e revela o segredo HMAC (opcional). Dark mode: classes wa-* + .wa-field.
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

function embedSnippet(baseUrl, token) {
  const b = (baseUrl || '').replace(/\/+$/, '');
  return `<script>(function(d,t){var g=d.createElement(t);` +
    `g.src="${b}/plugins/website/static/sdk.js";g.async=true;d.body.appendChild(g);` +
    `g.onload=function(){window.WhatsBotChat.run({widgetToken:'${token}',baseUrl:'${b}'})}` +
    `})(document,'script')<\/script>`;
}

function CopyBtn({ text }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    const done = () => { setCopied(true); setTimeout(() => setCopied(false), 1800); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallback(text, done));
    } else fallback(text, done);
  }
  return html`<button class="px-3 py-1.5 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
    onClick=${copy}>${copied ? 'Copiado!' : 'Copiar'}</button>`;
}

function fallback(text, done) {
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.focus(); ta.select();
    if (document.execCommand('copy')) done();
    document.body.removeChild(ta);
  } catch (e) { /* ignore */ }
}

function ChannelCard({ ch, baseUrl, apiBase }) {
  const [hmac, setHmac] = useState('');
  const snippet = embedSnippet(baseUrl, ch.widget_token);
  async function reveal() {
    try {
      const r = await apiFetch(`${apiBase}/reveal-hmac?channel_id=${encodeURIComponent(ch.id)}`);
      const d = await r.json();
      if (d.ok) setHmac(d.data.hmac_token || '(não configurado)');
    } catch (e) { setHmac('(erro)'); }
  }
  return html`
    <div class="border border-wa-border rounded-lg p-4 mb-3 bg-wa-panel">
      <div class="flex items-center justify-between mb-2">
        <div class="text-[14px] font-medium text-wa-text">${ch.display_name}</div>
        <span class="text-[11px] px-2 py-0.5 rounded ${ch.enabled ? 'bg-wa-teal/10 text-wa-teal' : 'bg-gray-100 text-wa-secondary'}">
          ${ch.enabled ? 'ativo' : 'desativado'}
        </span>
      </div>

      <label class="block text-[12px] text-wa-secondary mb-1">Token do widget (público)</label>
      <code class="block break-all px-3 py-2 rounded-md text-[12px] bg-wa-bg border border-wa-border text-wa-text mb-3">${ch.widget_token || '—'}</code>

      <label class="block text-[12px] text-wa-secondary mb-1">Domínios autorizados</label>
      <div class="text-[13px] text-wa-text mb-3">${ch.allowed_domains ? ch.allowed_domains : html`<span class="text-wa-secondary">qualquer site (edite o canal na tela Canais para restringir)</span>`}</div>

      <label class="block text-[12px] text-wa-secondary mb-1">Snippet de instalação</label>
      <p class="text-[12px] text-wa-secondary mb-2">Cole antes de <code>&lt;/body&gt;</code> no site onde o widget deve aparecer.</p>
      <div class="flex gap-2 items-start">
        <code class="flex-1 min-w-0 break-all px-3 py-2 rounded-md text-[12px] bg-wa-bg border border-wa-border text-wa-text">${snippet}</code>
        <${CopyBtn} text=${snippet} />
      </div>

      <div class="mt-3 flex items-center gap-2 flex-wrap">
        <span class="text-[12px] text-wa-secondary">HMAC (setUser):</span>
        ${ch.hmac_set
          ? (hmac
              ? html`<code class="break-all px-2 py-1 rounded text-[12px] bg-wa-bg border border-wa-border text-wa-text">${hmac}</code>`
              : html`<button class="px-3 py-1.5 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors" onClick=${reveal}>Revelar segredo</button>`)
          : html`<span class="text-[12px] text-wa-secondary">não configurado (opcional — defina o "HMAC token" ao editar o canal)</span>`}
      </div>
    </div>`;
}

export default function WebsiteConfig({ apiBase = '/api/plugins/website' } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await apiFetch(`${apiBase}/channels`);
        const d = await r.json();
        if (d.ok) setData(d.data);
        else setError(d.error || 'Falha ao carregar');
      } catch (e) { setError(String(e.message || e)); }
    })();
  }, [apiBase]);

  const baseUrl = (data && data.public_base_url) || window.location.origin;

  return html`
    <div class="text-wa-text">
      <p class="text-[13px] text-wa-secondary mb-4">
        O widget de site é um canal. Crie um em <span class="font-medium text-wa-text">Canais → Novo canal → Site (Widget)</span>,
        defina os domínios autorizados e a IA/agentes. Depois copie o snippet abaixo para o seu site.
      </p>
      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}
      ${!data ? html`<div class="text-[13px] text-wa-secondary">Carregando…</div>` : null}
      ${data && data.channels && data.channels.length === 0
        ? html`<div class="border border-wa-border rounded-lg p-4 text-[13px] text-wa-secondary">
            Nenhum widget criado ainda. Vá em <span class="font-medium text-wa-text">Canais</span> e crie um canal do tipo "Site (Widget)".
          </div>`
        : null}
      ${data && (data.channels || []).map((ch) => html`<${ChannelCard} key=${ch.id} ch=${ch} baseUrl=${baseUrl} apiBase=${apiBase} />`)}
    </div>`;
}
