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
// Seletor com busca do core: a lista de fusos tem centenas de itens.
import { SearchableSelect } from '/static/js/components/SearchableSelect.js';

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

// Fuso horário do navegador (ex.: "America/Sao_Paulo") — a hora dos alertas é
// exibida neste fuso, sem o operador precisar escolher.
function browserTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; }
  catch { return ''; }
}

// Campos que precisam estar assinados no App Dashboard da Meta para os avisos
// chegarem. Sem isto, só o polling de qualidade funciona (por isso ele existe).
const META_FIELDS = [
  ['message_template_status_update', 'template pausado, reprovado ou aprovado'],
  ['message_template_quality_update', 'qualidade do template caiu'],
  ['template_category_update', 'template recategorizado (muda o custo)'],
  ['phone_number_quality_update', 'qualidade e limite do número'],
  ['business_capability_update', 'limite de conversas por dia'],
  ['account_update', 'conta restrita, banida ou em revisão'],
  ['account_review_update', 'resultado da revisão da conta'],
];

// Seção "Alertas da conta Meta (Telegram)" — avisa num grupo do Telegram quando
// um template cai, a qualidade do número muda, o limite muda ou os envios começam
// a falhar. Usa um bot próprio: não tem relação com a caixa Telegram do sistema.
function AccountAlerts({ apiBase }) {
  const browserTz = browserTimezone();
  const [cfg, setCfg] = useState(null);
  const [token, setToken] = useState(''); // valor novo (vazio = mantém o salvo)
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [note, setNote] = useState(null);

  async function load() {
    try {
      const q = browserTz ? `?tz=${encodeURIComponent(browserTz)}` : '';
      const r = await apiFetch(`${apiBase}/alert-settings${q}`);
      const data = await r.json();
      const d = (data && data.data) || null;
      if (d && !d.timezone) d.timezone = d.timezone_auto || browserTz || 'America/Sao_Paulo';
      setCfg(d);
    } catch (e) {
      setNote({ kind: 'err', text: String(e.message || e) });
    }
  }
  useEffect(() => { load(); }, []);

  function upd(patch) { setCfg((c) => ({ ...(c || {}), ...patch })); }

  function toggleGroup(key, value) {
    upd({ groups: (cfg.groups || []).map((g) => (g.key === key ? { ...g, enabled: value } : g)) });
  }

  async function save() {
    setSaving(true); setNote(null);
    try {
      const groups = {};
      for (const g of cfg.groups || []) groups[g.key] = !!g.enabled;
      const body = {
        enabled: !!cfg.enabled,
        chat_id: cfg.chat_id || '',
        interval_min: Number(cfg.interval_min) || 15,
        quality_poll_min: Number(cfg.quality_poll_min) || 10,
        timezone: cfg.timezone || 'America/Sao_Paulo',
        groups,
      };
      if (token.trim()) body.bot_token = token.trim();
      const r = await apiFetch(`${apiBase}/alert-settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Falha ao salvar.');
      setToken('');
      await load();
      setNote({ kind: 'ok', text: 'Configuração salva.' });
    } catch (e) {
      setNote({ kind: 'err', text: String(e.message || e) });
    } finally { setSaving(false); }
  }

  async function test() {
    setTesting(true); setNote(null);
    try {
      const body = { chat_id: cfg.chat_id || '' };
      if (token.trim()) body.bot_token = token.trim();
      const r = await apiFetch(`${apiBase}/alert-test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Falha no teste.');
      setNote({ kind: 'ok', text: 'Mensagem de teste enviada ao Telegram.' });
    } catch (e) {
      setNote({ kind: 'err', text: String(e.message || e) });
    } finally { setTesting(false); }
  }

  if (!cfg) return html`<div class=${HINT}>Carregando alertas…</div>`;

  return html`
    <div class="mt-8 pt-6 border-t border-wa-border">
      <h3 class="text-base font-semibold text-wa-text mb-1">Alertas da conta Meta (Telegram)</h3>
      <p class="text-sm text-wa-secondary mb-3">
        Avisa num grupo do Telegram quando a Meta mexe na sua conta — template
        pausado ou reprovado, qualidade do número caindo, limite de mensagens
        alterado, conta restrita — e quando os envios começam a falhar. Usa um bot
        próprio do Telegram: não tem relação com a caixa de entrada do Telegram.
      </p>

      ${note && html`
        <div class=${'mb-3 px-3 py-2 rounded text-sm ' +
          (note.kind === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
          ${note.text}
        </div>`}

      <div class="space-y-3">
        <label class="flex items-center gap-2 text-sm text-wa-text">
          <input type="checkbox" checked=${!!cfg.enabled}
            onChange=${(e) => upd({ enabled: e.target.checked })} />
          <span>Enviar alertas ao Telegram</span>
        </label>

        <div>
          <label class=${LABEL}>Token do bot do Telegram</label>
          <input class=${FIELD} type="password" autocomplete="off"
            placeholder=${cfg.bot_token_set
              ? `Token salvo (…${cfg.bot_token_hint || ''}) — deixe em branco para manter`
              : '123456:ABC-DEF...'}
            value=${token} onInput=${(e) => setToken(e.target.value)} />
          <div class=${HINT}>Crie um bot com o @BotFather e cole o token aqui.</div>
        </div>

        <div>
          <label class=${LABEL}>Chat ID de destino (grupo)</label>
          <input class=${FIELD} type="text" placeholder="Ex: -1001234567890"
            value=${cfg.chat_id || ''} onInput=${(e) => upd({ chat_id: e.target.value })} />
          <div class=${HINT}>
            ID do grupo que receberá os alertas (adicione o @userinfobot ao grupo
            para descobrir). Se o grupo virar supergrupo, o ID é atualizado sozinho.
          </div>
        </div>

        <div>
          <label class=${LABEL}>O que alertar</label>
          <div class="space-y-1 mt-1">
            ${(cfg.groups || []).map((g) => html`
              <label class="flex items-start gap-2 text-sm text-wa-text">
                <input type="checkbox" class="mt-1" checked=${!!g.enabled}
                  onChange=${(e) => toggleGroup(g.key, e.target.checked)} />
                <span>${g.label}</span>
              </label>`)}
          </div>
        </div>

        <div class="flex gap-3">
          <div>
            <label class=${LABEL}>Agrupar repetições por (minutos)</label>
            <input class=${FIELD + ' max-w-[8rem]'} type="number" min="1" step="1"
              value=${cfg.interval_min ?? 15}
              onInput=${(e) => upd({ interval_min: e.target.value })} />
            <div class=${HINT}>Repetição do mesmo aviso só soma no contador da mensagem já enviada.</div>
          </div>
          <div>
            <label class=${LABEL}>Verificar a qualidade a cada (minutos)</label>
            <input class=${FIELD + ' max-w-[8rem]'} type="number" min="5" step="1"
              value=${cfg.quality_poll_min ?? 10}
              onInput=${(e) => upd({ quality_poll_min: e.target.value })} />
            <div class=${HINT}>Funciona mesmo sem assinar nada na Meta. Mínimo 5 min.</div>
          </div>
        </div>

        <div>
          <label class=${LABEL}>Fuso horário (hora exibida nos alertas)</label>
          <${SearchableSelect} value=${cfg.timezone || 'America/Sao_Paulo'}
            onChange=${(v) => upd({ timezone: v })}
            options=${(cfg.timezones || []).map((t) => ({ value: t.value, label: t.label }))}
            inputClass=${FIELD + ' w-full'} searchPlaceholder="Pesquisar fuso…" />
        </div>

        <div class="flex gap-2 pt-1">
          <button class="px-4 py-2 rounded bg-wa-teal text-white text-sm font-medium disabled:opacity-50"
            disabled=${saving} onClick=${save}>${saving ? 'Salvando…' : 'Salvar'}</button>
          <button class="px-4 py-2 rounded border border-wa-border text-wa-text text-sm disabled:opacity-50"
            disabled=${testing} onClick=${test}>${testing ? 'Enviando…' : 'Enviar teste'}</button>
        </div>
      </div>

      <div class="mt-5 px-3 py-3 rounded bg-wa-bg border border-wa-border text-xs text-wa-secondary">
        <div class="font-semibold text-wa-text mb-1">Para a Meta enviar esses avisos</div>
        <div class="mb-2 text-wa-text">
          Primeiro, edite o canal WhatsApp Cloud e preencha <strong>WABA ID</strong>
          e <strong>App Secret (Meta)</strong>. O App Secret autentica a assinatura
          do webhook; sem ele, avisos de template/conta são descartados por segurança.
          Mensagens continuam chegando durante a migração, e o polling de qualidade
          e as falhas de envio abaixo continuam funcionando.
        </div>
        <div class="mb-2">
          Em <strong>developers.facebook.com</strong> → seu App → WhatsApp →
          Configuração → Webhooks → <strong>Gerenciar</strong>, assine estes
          campos além de <code>messages</code>:
        </div>
        <ul class="list-disc ml-5 space-y-0.5">
          ${META_FIELDS.map(([f, d]) => html`<li><code>${f}</code> — ${d}</li>`)}
        </ul>
        <div class="mt-2">
          Sem assinar, só a verificação de qualidade acima funciona (ela consulta a
          Meta sozinha) e as falhas de envio, que não dependem de assinatura.
        </div>
      </div>
    </div>
  `;
}

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
          <label class=${LABEL}>App Secret <span class="text-wa-secondary">(obrigatório)</span></label>
          <input
            type="password"
            class=${FIELD}
            placeholder="para validar a assinatura X-Hub"
            value=${form.app_secret || ''}
            onInput=${(e) => update('app_secret', e.target.value)}
          />
          <div class=${HINT}>
            Obrigatório em canais novos para validar a assinatura do webhook.
            Se este canal é legado, preencha agora: sem o segredo as mensagens
            continuam chegando por compatibilidade, mas alertas de conta ficam bloqueados.
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

      <${AccountAlerts} apiBase=${apiBase} />

      <div class="mt-5 px-3 py-2 rounded bg-wa-bg border border-wa-border text-xs text-wa-secondary">
        Dica: anote estes valores e cadastre as credenciais
        (<code>phone_number_id</code>, <code>waba_id</code>,
        <code>access_token</code>, <code>verify_token</code>,
        <code>app_secret</code>) no canal pela tela <strong>Canais</strong>. Os
        campos acima ficam só neste navegador como rascunho.
      </div>
    </div>
  `;
}
