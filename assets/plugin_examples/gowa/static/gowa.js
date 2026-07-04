// Tela de configuração do plugin WhatsApp (GOWA) — config:true.
// Renderizada DENTRO do modal "Configurar" do card em /plugins. Mostra, por canal
// GOWA, o status de conexão e o QR para parear o número. O canal "default" vem
// semeado; números adicionais são criados na tela "Canais".
//
// Dark mode: classes semânticas wa-* e .wa-field (legível nos dois temas).
import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
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

// true se a URL aponta para a própria máquina (localhost/loopback) — um link
// assim NÃO abre em outro aparelho (ex: no celular que recebe o alerta).
function isLocalUrl(u) {
  if (!u) return false;
  let host = '';
  try { host = new URL(u).hostname.toLowerCase(); }
  catch { return /(^|\/\/)(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])/i.test(u); }
  host = host.replace(/^\[|\]$/g, ''); // tira colchetes de IPv6 (ex.: [::1])
  return host === 'localhost' || host.endsWith('.localhost')
    || host === '127.0.0.1' || host === '0.0.0.0' || host === '::1';
}

// Fuso horário do navegador (ex.: "America/Sao_Paulo", "America/Manaus") — a
// hora dos alertas é exibida neste fuso, sem o usuário precisar escolher.
function browserTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; }
  catch { return ''; }
}

// Seção "Alertas de desconexão via Telegram" — envia um alerta a um bot do
// Telegram quando o número cai. Independente do canal Telegram do sistema.
function DisconnectAlerts({ apiBase }) {
  const browserTz = browserTimezone();
  const [cfg, setCfg] = useState(null); // {enabled, bot_token_set, chat_id, panel_url, panel_url_auto, ...}
  const [token, setToken] = useState(''); // valor novo do token (vazio = não altera)
  const [showOverride, setShowOverride] = useState(false); // revela o campo de URL manual
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [note, setNote] = useState(null);

  async function load() {
    try {
      // Envia o fuso do navegador para o backend persistir (detecção automática).
      const q = browserTz ? `?tz=${encodeURIComponent(browserTz)}` : '';
      const r = await apiFetch(`${apiBase}/alert-settings${q}`);
      const data = await r.json();
      const d = (data && data.data) || null;
      setCfg(d);
      // Abre o campo de override se já houver um salvo OU se a URL detectada for
      // localhost (aí o link não abriria no celular — precisa de um endereço real).
      if (d && (d.panel_url || isLocalUrl(d.panel_url_auto))) setShowOverride(true);
    } catch (e) {
      setNote({ kind: 'err', text: String(e.message || e) });
    }
  }
  useEffect(() => { load(); }, []);

  function upd(patch) { setCfg((c) => ({ ...(c || {}), ...patch })); }

  async function save() {
    setSaving(true); setNote(null);
    try {
      const body = {
        enabled: !!cfg.enabled,
        chat_id: cfg.chat_id || '',
        panel_url: cfg.panel_url || '',
        interval_min: Number(cfg.interval_min) || 15,
        timezone: cfg.timezone || '', // vazio = automático (fuso do navegador)
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
    <div class="mt-6 pt-5 border-t border-wa-border">
      <h3 class="text-base font-semibold text-wa-text mb-1">Alertas de desconexão (Telegram)</h3>
      <p class="text-sm text-wa-secondary mb-3">
        Receba um alerta no Telegram quando este número cair. Enquanto ficar fora do ar,
        a mesma mensagem é atualizada a cada ${Number(cfg.interval_min) || 15} min. Usa um
        bot do Telegram próprio — não tem relação com a caixa de entrada do Telegram.
      </p>

      ${note && html`
        <div class=${'mb-3 px-3 py-2 rounded text-sm ' +
          (note.kind === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
          ${note.text}
        </div>`}

      <label class="flex items-center gap-2 mb-3 text-sm text-wa-text">
        <input type="checkbox" checked=${!!cfg.enabled}
          onChange=${(e) => upd({ enabled: e.target.checked })} />
        Ativar alertas de desconexão
      </label>

      <div class="space-y-3">
        <div>
          <label class=${LABEL}>Token do bot do Telegram</label>
          <input class=${FIELD} type="password" autocomplete="off"
            placeholder=${cfg.bot_token_set ? 'Token salvo — deixe em branco para manter' : '123456:ABC-DEF...'}
            value=${token} onInput=${(e) => setToken(e.target.value)} />
          <div class=${HINT}>Crie um bot com o @BotFather e cole o token aqui.</div>
        </div>

        <div>
          <label class=${LABEL}>Chat ID de destino</label>
          <input class=${FIELD} type="text" placeholder="Ex: 123456789 ou -1001234567890"
            value=${cfg.chat_id || ''} onInput=${(e) => upd({ chat_id: e.target.value })} />
          <div class=${HINT}>ID do usuário/grupo que receberá o alerta (fale com @userinfobot para descobrir).</div>
        </div>

        <div>
          <label class=${LABEL}>URL do painel (link de reconexão)</label>
          <div class="px-3 py-2 rounded bg-wa-bg border border-wa-border text-sm text-wa-text">
            Detectada automaticamente:${' '}
            <strong>${cfg.panel_url_auto || '—'}</strong>/gowa/config
          </div>
          <div class=${HINT}>Puxada do endereço que você usa para acessar o WhatsBot — não precisa preencher.</div>

          ${isLocalUrl(cfg.panel_url || cfg.panel_url_auto) && html`
            <div class="mt-2 px-3 py-2 rounded text-sm bg-amber-100 text-amber-700">
              ⚠️ O endereço detectado é <strong>localhost</strong>, que só abre nesta máquina —
              o link não vai funcionar no celular que receber o alerta. Informe abaixo o
              IP da rede (ex.: <strong>http://192.168.x.x:8080</strong>) ou um domínio.
            </div>`}

          <label class="flex items-center gap-2 mt-2 text-sm text-wa-text">
            <input type="checkbox" checked=${showOverride}
              onChange=${(e) => {
                const on = e.target.checked;
                setShowOverride(on);
                if (!on) upd({ panel_url: '' }); // desmarcar volta a usar a URL detectada
              }} />
            Estou acessando via localhost / quero forçar outra URL
          </label>

          ${showOverride && html`
            <div class="mt-2">
              <input class=${FIELD} type="text"
                placeholder="Ex: https://seu-dominio ou http://192.168.x.x:8080"
                value=${cfg.panel_url || ''} onInput=${(e) => upd({ panel_url: e.target.value })} />
              <div class=${HINT}>Este endereço substitui a URL detectada no link de reconexão.</div>
            </div>`}
        </div>

        <div>
          <label class=${LABEL}>Reavisar a cada (minutos)</label>
          <input class=${FIELD + ' max-w-[8rem]'} type="number" min="1" step="1"
            value=${cfg.interval_min ?? 15} onInput=${(e) => upd({ interval_min: e.target.value })} />
        </div>

        <div>
          <label class=${LABEL}>Fuso horário (hora exibida nos alertas)</label>
          <select class=${FIELD} value=${cfg.timezone || ''}
            onChange=${(e) => upd({ timezone: e.target.value })}>
            <option value="">Automático — do seu navegador (${cfg.timezone_auto || browserTz || 'America/Sao_Paulo'})</option>
            ${Object.entries(cfg.timezones || {}).map(
              ([tz, label]) => html`<option value=${tz}>${label}</option>`
            )}
          </select>
          <div class=${HINT}>No automático, usamos o fuso do computador que abriu esta tela — funciona em qualquer região. Escolha um da lista só se quiser fixar outro.</div>
        </div>

        <div class="flex gap-2 pt-1">
          <button class="px-4 py-2 rounded bg-wa-teal text-white text-sm font-medium disabled:opacity-50"
            disabled=${saving} onClick=${save}>${saving ? 'Salvando…' : 'Salvar'}</button>
          <button class="px-4 py-2 rounded border border-wa-border text-wa-text text-sm disabled:opacity-50"
            disabled=${testing} onClick=${test}>${testing ? 'Enviando…' : 'Enviar teste'}</button>
        </div>
      </div>
    </div>
  `;
}

export default function GowaConfig({ apiBase = '/api/plugins/gowa' } = {}) {
  const [channels, setChannels] = useState([]);
  const [channelId, setChannelId] = useState('');
  const [status, setStatus] = useState(null);
  const [qrUrl, setQrUrl] = useState(null);
  const [msg, setMsg] = useState(null);
  const qrUrlRef = useRef(null);

  async function loadChannels() {
    try {
      const r = await apiFetch('/api/channels');
      const data = await r.json();
      const list = ((data && data.data) || []).filter((c) => c.provider === 'gowa');
      setChannels(list);
      if (!channelId && list.length) {
        const def = list.find((c) => c.id === 'default') || list[0];
        setChannelId(def.id);
      }
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message || e) });
    }
  }

  async function loadStatus(cid) {
    if (!cid) { setStatus(null); return; }
    try {
      const r = await apiFetch(`/api/channels/${encodeURIComponent(cid)}/status`);
      const data = await r.json();
      setStatus((data && data.data) || null);
    } catch (e) {
      setStatus(null);
    }
  }

  async function loadQr(cid) {
    if (!cid) return;
    try {
      const r = await apiFetch(`/api/channels/${encodeURIComponent(cid)}/qr`);
      if (r.status === 200) {
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        if (qrUrlRef.current) URL.revokeObjectURL(qrUrlRef.current);
        qrUrlRef.current = url;
        setQrUrl(url);
      } else {
        if (qrUrlRef.current) { URL.revokeObjectURL(qrUrlRef.current); qrUrlRef.current = null; }
        setQrUrl(null);
      }
    } catch {
      setQrUrl(null);
    }
  }

  useEffect(() => { loadChannels(); }, []);

  // Poll status (4s) + QR (12s while not connected). Cleared on channel change/unmount.
  useEffect(() => {
    if (!channelId) return;
    loadStatus(channelId);
    loadQr(channelId);
    const st = setInterval(() => loadStatus(channelId), 4000);
    const qt = setInterval(() => {
      if (!(status && status.logged_in)) loadQr(channelId);
    }, 12000);
    return () => {
      clearInterval(st); clearInterval(qt);
      if (qrUrlRef.current) { URL.revokeObjectURL(qrUrlRef.current); qrUrlRef.current = null; }
    };
  }, [channelId]);

  const connected = status && status.connected;
  const loggedIn = status && status.logged_in;
  const needsQr = status && status.needs_qr;

  return html`
    <div class="p-5 max-w-2xl mx-auto text-wa-text">
      <h2 class="text-xl font-bold mb-1">WhatsApp (GOWA)</h2>
      <p class="text-sm text-wa-secondary mb-4">
        Conecte um número de WhatsApp escaneando o QR abaixo (WhatsApp →
        Aparelhos conectados → Conectar um aparelho). Esta caixa de entrada roda
        como subprocesso gerenciado por este plugin; desativá-lo derruba a conexão.
      </p>

      ${msg && html`
        <div class=${'mb-3 px-3 py-2 rounded text-sm ' +
          (msg.kind === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
          ${msg.text}
        </div>`}

      <div class="space-y-4">
        <div>
          <label class=${LABEL}>Canal WhatsApp (GOWA)</label>
          ${channels.length > 1
            ? html`<select class=${FIELD} value=${channelId}
                onChange=${(e) => setChannelId(e.target.value)}>
                ${channels.map((c) => html`<option value=${c.id}>${c.display_name || c.id} (${c.id})</option>`)}
              </select>`
            : html`<div class=${HINT}>${channels.length === 1
                ? html`Canal: <strong>${channels[0].display_name || channels[0].id}</strong>`
                : 'Nenhum canal GOWA — o canal "default" é criado automaticamente.'}</div>`}
        </div>

        <div class="px-3 py-2 rounded bg-wa-bg border border-wa-border text-sm">
          ${loggedIn
            ? html`<span class="text-wa-teal font-medium">● Conectado</span>${
                status && status.own_phone ? html` — <strong>${status.own_phone}</strong>` : ''}`
            : connected
              ? html`<span class="text-amber-600 font-medium">● Aguardando pareamento</span> — escaneie o QR.`
              : html`<span class="text-wa-secondary font-medium">● Desconectado</span> — iniciando o GOWA…`}
        </div>

        ${needsQr && html`
          <div class="flex flex-col items-center gap-2 p-4 rounded bg-wa-panel border border-wa-border">
            ${qrUrl
              ? html`<img src=${qrUrl} alt="QR Code" class="w-56 h-56 bg-white p-2 rounded" />`
              : html`<div class="w-56 h-56 flex items-center justify-center text-wa-secondary text-sm">Gerando QR…</div>`}
            <div class=${HINT}>O QR expira em ~20s e é renovado automaticamente.</div>
          </div>`}
      </div>

      <${DisconnectAlerts} apiBase=${apiBase} />
    </div>
  `;
}
