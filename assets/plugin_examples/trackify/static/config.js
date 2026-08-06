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
// ⚠️ CORREÇÃO: o comentário anterior dizia que "as demais opções seguem no
// formulário declarativo, na aba Configurações abaixo". ESSA ABA NÃO EXISTE.
// O core escolhe UM dos dois (PluginsManager.js): screen `config:true` OU
// `PluginSettingsForm` — nunca os dois. Enquanto esta tela não tinha os campos,
// as settings de `settings.py` ficavam sem interface nenhuma. Campo novo lá
// precisa de campo novo AQUI — não existe form automático de reserva.
//
// ⚠️ O `PUT /api/plugins/trackify/settings` é DESTRUTIVO por construção: o core
// faz `Settings(**body)` e grava `model_dump()` inteiro, então todo campo ausente
// volta ao DEFAULT. Um PUT parcial apagaria a URL de ingestão e derrubaria o espelho.
// Por isso todo save daqui reenvia o objeto COMPLETO (`{...values, ...alterados}`).
import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { authHeaders } from '/static/js/services/api.js';
import FieldSync, { ApiKeyCard } from '/plugins/trackify/static/FieldSync.js';
import Consent from '/plugins/trackify/static/Consent.js';

const html = htm.bind(h);

const API = '/api/plugins/trackify';
const MASK = '***';

// Limites declarados em settings.py (Field ge/le). Espelhados aqui para o campo
// já nascer válido — o PUT devolve 400 no valor fora da faixa.
const NUM_LIMITS = {
  cache_ttl_seconds: [0, 3600],
  timeline_page_size: [5, 100],
  rate_per_min: [1, 60],
  max_age_days: [1, 90],
  field_sync_poll_seconds: [15, 3600],
  field_sync_rate_per_min: [1, 25],
  field_sync_reconcile_minutes: [15, 1440],
};

function clampNum(key, raw) {
  const [lo, hi] = NUM_LIMITS[key];
  const n = Number(raw);
  if (!Number.isFinite(n)) return lo;
  return Math.min(Math.max(Math.round(n), lo), hi);
}

// Slug do canal, derivado da URL de ingestão — o texto de ajuda da chave era fixo
// em "whatsbot" e mentia em qualquer instalação com outro slug.
function channelSlug(url) {
  const parts = String(url || '').split('?')[0].replace(/\/+$/, '').split('/');
  const last = parts[parts.length - 1];
  return last && last !== 'ingestion' ? last : '';
}

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
          <${Dot} ok=${configured} /> API key ${configured ? 'configurada' : 'NÃO configurada'}
          ${!configured ? html`<span class="text-wa-secondary">— cole a chave abaixo</span>` : null}
        </li>
        <li class="flex items-center gap-2 text-wa-text">
          <${Dot} ok=${reachable} /> Trackify ${reachable ? 'respondendo' : 'sem resposta'}
          ${!reachable && message ? html`<span class="text-wa-secondary">— ${message}</span>` : null}
        </li>
        <li class="flex items-center gap-2 text-wa-text">
          <${Dot} ok=${schema_ok} /> Permissões da chave
          ${schema_ok ? ' suficientes' : ' insuficientes'}
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

function Toggle({ checked, onChange, label, hint, disabled }) {
  return html`
    <label class=${`flex items-start gap-2.5 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}>
      <input type="checkbox" class="mt-0.5 accent-current text-wa-teal"
        checked=${!!checked} disabled=${disabled}
        onChange=${(e) => onChange(e.target.checked)} />
      <span>
        <span class="text-[13px] text-wa-text">${label}</span>
        ${hint ? html`<span class="block text-[11px] text-wa-secondary">${hint}</span>` : null}
      </span>
    </label>`;
}

function Field({ label, hint, children }) {
  return html`
    <div>
      <label class="block text-xs font-medium text-wa-text mb-1">${label}</label>
      ${children}
      ${hint ? html`<p class="text-[11px] text-wa-secondary mt-1">${hint}</p>` : null}
    </div>`;
}

/** Espelho de eventos (escrita). É a seção que estava faltando: sem ela não havia
 *  jeito de ligar/desligar o espelho nem o modo seco pela interface. */
function MirrorSettings({ values, onSave, busy }) {
  const [v, setV] = useState(null);
  const [msg, setMsg] = useState('');
  useEffect(() => { setV(values ? { ...values } : null); }, [values]);
  if (!v) return null;

  const set = (k) => (val) => { setV({ ...v, [k]: val }); setMsg(''); };
  const live = v.mirror_enabled && !v.mirror_dry_run;

  async function save(e) {
    e.preventDefault();
    setMsg('');
    // Objeto COMPLETO: o PUT do core reseta todo campo ausente (ver topo).
    const r = await onSave({
      ...values,
      mirror_enabled: !!v.mirror_enabled,
      mirror_dry_run: !!v.mirror_dry_run,
      ingestion_url: String(v.ingestion_url || '').trim(),
      mirror_contact_types: String(v.mirror_contact_types || 'whatsapp').trim(),
      rate_per_min: clampNum('rate_per_min', v.rate_per_min),
      max_age_days: clampNum('max_age_days', v.max_age_days),
    });
    setMsg(r && r.ok ? 'Salvo.' : `Não foi possível salvar${r && r.error ? `: ${r.error}` : '.'}`);
  }

  return html`
    <section class="border border-wa-border rounded-xl p-4 bg-wa-panel">
      <h3 class="text-sm font-semibold text-wa-text mb-3">Espelho de eventos (envio ao Trackify)</h3>
      <form onSubmit=${save} class="space-y-3">
        <${Toggle} checked=${v.mirror_enabled} onChange=${set('mirror_enabled')}
          label="Espelhar acontecimentos no Trackify"
          hint="Conversas, protocolos e contatos viram eventos no CDP. Exige o canal e os mapeamentos criados lá." />
        <${Toggle} checked=${v.mirror_dry_run} onChange=${set('mirror_dry_run')}
          disabled=${!v.mirror_enabled}
          label="Modo seco (não envia de verdade)"
          hint="Enfileira e monta o envelope, mas não posta. Confira a fila antes de tocar no CDP." />

        ${live ? html`
          <p class="text-[12px] rounded-md px-3 py-2 bg-amber-500/15 text-amber-600 dark:text-amber-400">
            Envio real ligado. Vale para <strong>toda</strong> conversa elegível — não dá para
            limitar a um contato. Número que ainda não existe no Trackify vira cadastro novo lá,
            e contato no CDP só sai por exclusão suave.
          </p>` : null}

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="sm:col-span-2">
            <${Field} label="URL de ingestão"
              hint="Ex.: https://SEU-NEXUS/trackify/api/v1/ingestion/<canal>">
              <input type="text" class="wa-field px-2.5 py-1.5 text-sm rounded-md w-full"
                placeholder="https://…/api/v1/ingestion/whatsbot"
                value=${v.ingestion_url || ''} onInput=${(e) => set('ingestion_url')(e.target.value)} />
            <//>
          </div>
          <${Field} label="Envios por minuto" hint="Teto do Trackify: 60/min por IP. Nunca rode no teto.">
            <input type="number" min="1" max="60" class="wa-field px-2.5 py-1.5 text-sm rounded-md w-full"
              value=${v.rate_per_min} onInput=${(e) => set('rate_per_min')(e.target.value)} />
          <//>
          <${Field} label="Idade máxima na fila (dias)" hint="Evento não entregue nesse prazo é descartado com contador.">
            <input type="number" min="1" max="90" class="wa-field px-2.5 py-1.5 text-sm rounded-md w-full"
              value=${v.max_age_days} onInput=${(e) => set('max_age_days')(e.target.value)} />
          <//>
          <div class="sm:col-span-2">
            <${Field} label="Tipos de contato a espelhar"
              hint="Separados por vírgula. Grupos nunca são espelhados.">
              <input type="text" class="wa-field px-2.5 py-1.5 text-sm rounded-md w-full"
                value=${v.mirror_contact_types || ''}
                onInput=${(e) => set('mirror_contact_types')(e.target.value)} />
            <//>
          </div>
        </div>

        <div class="flex items-center gap-2">
          <button type="submit" disabled=${busy}
            class="px-3 py-1.5 rounded-md text-[12px] bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 disabled:opacity-50 transition-colors">
            Salvar espelho
          </button>
          ${msg ? html`<span class="text-[12px] text-wa-secondary">${msg}</span>` : null}
        </div>
      </form>
    </section>`;
}

/** Leitura do CDP. O DSN é segredo: o GET do core devolve em claro, então ele fica
 *  no estado (para o reenvio completo) mas nunca é RENDERIZADO — só substituível. */
function ReadSettings({ values, onSave, busy }) {
  const [v, setV] = useState(null);
  const [msg, setMsg] = useState('');
  useEffect(() => { setV(values ? { ...values } : null); }, [values]);
  if (!v) return null;

  const set = (k) => (val) => { setV({ ...v, [k]: val }); setMsg(''); };

  async function save(e) {
    e.preventDefault();
    setMsg('');
    const r = await onSave({
      ...values,
      nexus_base_url: String(v.nexus_base_url || '').trim(),
      cache_ttl_seconds: clampNum('cache_ttl_seconds', v.cache_ttl_seconds),
      timeline_page_size: clampNum('timeline_page_size', v.timeline_page_size),
      product_identity_fields: String(v.product_identity_fields || '').trim(),
    });
    setMsg(r && r.ok ? 'Salvo.' : `Não foi possível salvar${r && r.error ? `: ${r.error}` : '.'}`);
  }

  return html`
    <section class="border border-wa-border rounded-xl p-4 bg-wa-panel">
      <h3 class="text-sm font-semibold text-wa-text mb-3">Leitura do Trackify (jornada do contato)</h3>
      <p class="text-xs text-wa-secondary mb-3">
        A credencial é a <strong>API key</strong>, configurada na aba
        “Campos do contato”. Não há mais conexão direta ao banco do Nexus: tudo
        passa pelas rotas do Trackify.
      </p>
      <form onSubmit=${save} class="space-y-3">
        <${Field} label="URL do Trackify (botão “Abrir no Trackify”)"
          hint="Base pública do módulo, sem barra no fim. Vazio esconde o botão.">
          <input type="text" class="wa-field px-2.5 py-1.5 text-sm rounded-md w-full"
            placeholder="https://SEU-NEXUS/trackify"
            value=${v.nexus_base_url || ''} onInput=${(e) => set('nexus_base_url')(e.target.value)} />
        <//>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <${Field} label="Cache da jornada (s)" hint="0 desliga.">
            <input type="number" min="0" max="3600" class="wa-field px-2.5 py-1.5 text-sm rounded-md w-full"
              value=${v.cache_ttl_seconds} onInput=${(e) => set('cache_ttl_seconds')(e.target.value)} />
          <//>
          <${Field} label="Eventos por página">
            <input type="number" min="5" max="100" class="wa-field px-2.5 py-1.5 text-sm rounded-md w-full"
              value=${v.timeline_page_size} onInput=${(e) => set('timeline_page_size')(e.target.value)} />
          <//>
        </div>

        <${Field} label="Campos que identificam o produto (em ordem)"
          hint=${'Separados por vírgula; o primeiro preenchido nomeia a linha na aba Produtos. '
            + 'Cada gateway nomeia de um jeito — o ticto preenche product_name/offer_name e o '
            + 'pagarme só product_id/offer_id. Canal novo com um campo diferente entra aqui, sem '
            + 'atualizar o plugin: quando uma cobrança não pode ser listada, a própria aba diz '
            + 'quais campos aqueles eventos trazem. Vazio volta ao padrão.'}>
          <input type="text" class="wa-field px-2.5 py-1.5 text-sm rounded-md w-full"
            placeholder="product_name,offer_name,product_id,offer_id"
            value=${v.product_identity_fields || ''}
            onInput=${(e) => set('product_identity_fields')(e.target.value)} />
        <//>

        <div class="flex items-center gap-2">
          <button type="submit" disabled=${busy}
            class="px-3 py-1.5 rounded-md text-[12px] bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 disabled:opacity-50 transition-colors">
            Salvar leitura
          </button>
          ${msg ? html`<span class="text-[12px] text-wa-secondary">${msg}</span>` : null}
        </div>
      </form>
    </section>`;
}

function KeyField({ state, slug, onSave, busy }) {
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
        ${slug ? html`<code>${slug}</code>` : 'de ingestão'} no Trackify. Enviada no
        cabeçalho <code>X-Trackify-Key</code>. Deixe o <code>***</code> intacto para
        não alterá-la.
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

// Abas, com o Health SEMPRE visível acima delas: ele é a linha de status do
// plugin e já reporta as áreas, então esconder atrás de aba tiraria o único
// lugar onde se vê tudo de uma vez. "Campos do contato" vem depois das duas
// primeiras porque depende delas (precisa da credencial para listar os campos
// do CDP) e é a única que pode mexer na identidade dos contatos lá.
// "Descadastro por botão" fica por último porque escreve num campo só, e
// depende da mesma credencial.
const TABS = [
  ['conexao', 'Conexão'],
  ['espelho', 'Espelho de eventos'],
  ['campos', 'Campos do contato'],
  ['consent', 'Descadastro por botão'],
];

function Tabs({ value, onChange }) {
  return html`
    <div class="flex gap-1 border-b border-wa-border">
      ${TABS.map(([id, label]) => html`
        <button class=${`px-3 py-2 text-sm -mb-px border-b-2 ${
            value === id ? 'border-wa-teal text-wa-text' : 'border-transparent text-wa-secondary'}`}
          onClick=${() => onChange(id)}>${label}</button>`)}
    </div>`;
}

export default function TrackifyConfig() {
  const [health, setHealth] = useState(null);
  const [keyState, setKeyState] = useState(null);
  const [queue, setQueue] = useState(null);
  const [settings, setSettings] = useState(null);
  const [cred, setCred] = useState(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState('conexao');

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

  // Rota do CORE (`/api/plugins/<id>/settings`), não do router do plugin — cai no
  // mesmo prefixo mas o plugin não declara `/settings`, então resolve no core.
  const loadSettings = useCallback(async () => {
    const r = await req('GET', '/settings');
    if (r && r.ok && r.data) setSettings(r.data.values || null);
  }, []);

  // A API key é a credencial da CONEXÃO (vale para ler, escrever e ingerir),
  // então mora na aba Conexão — não na de mapeamento de campos, onde ficou por
  // engano quando substituiu a conta de serviço.
  const loadCred = useCallback(async () => {
    const r = await req('GET', '/api-key');
    if (r && r.ok) setCred(r.data || null);
  }, []);

  const saveCred = useCallback(async (body) => {
    setBusy(true);
    try {
      const r = await req('PUT', '/api-key', body);
      if (r && r.ok) { await loadCred(); await loadHealth(); }
      return r;
    } finally { setBusy(false); }
  }, [loadCred, loadHealth]);

  const testCred = useCallback(async (body) => {
    setBusy(true);
    try {
      const r = await req('POST', '/api-key/test', body);
      await loadHealth();
      return r;
    } finally { setBusy(false); }
  }, [loadHealth]);

  useEffect(() => {
    loadHealth(); loadKey(); loadQueue(); loadSettings(); loadCred();
  }, [loadHealth, loadKey, loadQueue, loadSettings, loadCred]);

  const saveSettings = useCallback(async (full) => {
    setBusy(true);
    try {
      const r = await req('PUT', '/settings', full);
      if (r && r.ok) {
        await loadSettings();
        await loadHealth();   // o farol "Espelho de eventos" mora no cartão de cima
      }
      return r;
    } finally { setBusy(false); }
  }, [loadSettings, loadHealth]);

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

  // Salva UM punhado de settings reenviando o objeto COMPLETO — ver o aviso
  // sobre o PUT destrutivo no topo do arquivo.
  const patchSettings = useCallback(async (patch) => {
    if (!settings) return null;
    return saveSettings({ ...settings, ...patch });
  }, [settings, saveSettings]);

  return html`
    <div class="space-y-4">
      <${Health} data=${health} onRefresh=${loadHealth} busy=${busy} />
      <${Tabs} value=${tab} onChange=${setTab} />
      ${tab === 'conexao' && html`
        <${ApiKeyCard} state=${cred} onSave=${saveCred} onTest=${testCred} busy=${busy} />
        <${ReadSettings} values=${settings} onSave=${saveSettings} busy=${busy} />`}
      ${tab === 'espelho' && html`
        <${MirrorSettings} values=${settings} onSave=${saveSettings} busy=${busy} />
        <${KeyField} state=${keyState} slug=${channelSlug(settings && settings.ingestion_url)}
          onSave=${saveKey} busy=${busy} />
        <${Queue} data=${queue} onRefresh=${loadQueue} onRequeue=${requeue} busy=${busy} />`}
      ${tab === 'campos' && html`
        <${FieldSync} req=${req} settings=${settings} onSaveSettings=${patchSettings}
          busy=${busy} />`}
      ${tab === 'consent' && html`
        <${Consent} req=${req} settings=${settings} onSaveSettings=${patchSettings}
          busy=${busy} />`}
    </div>`;
}
