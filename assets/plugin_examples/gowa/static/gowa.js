// Tela de configuração do plugin WhatsApp (GOWA) — config:true.
// Renderizada DENTRO do modal "Configurar" do card em /plugins. Contém APENAS a
// configuração do alerta de desconexão via Telegram (bot, destino, intervalo, fuso).
// O LIGA/DESLIGA do alerta é POR CANAL, feito na edição de cada caixa GOWA na tela
// "Canais". O pareamento (QR) e o status de conexão também vivem em "Canais".
//
// Dark mode: classes semânticas wa-* e .wa-field (legível nos dois temas).
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
  const [cfg, setCfg] = useState(null); // {enabled, bot_token_set, chat_id, panel_url_effective, ...}
  const [token, setToken] = useState(''); // valor novo do token (vazio = não altera)
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [note, setNote] = useState(null);

  async function load() {
    try {
      // Manda o fuso do navegador só como SUGESTÃO: desde o plano 148 o GET não
      // persiste nada — ele volta em `timezone_auto` e só vira config no Salvar.
      const q = browserTz ? `?tz=${encodeURIComponent(browserTz)}` : '';
      const r = await apiFetch(`${apiBase}/alert-settings${q}`);
      const data = await r.json();
      const d = (data && data.data) || null;
      // Sem "Automático": pré-seleciona um fuso concreto (o do navegador como
      // sugestão) quando ainda não há um salvo. O usuário pode trocar na lista.
      if (d && !d.timezone) d.timezone = d.timezone_auto || browserTz || 'America/Sao_Paulo';
      setCfg(d);
    } catch (e) {
      setNote({ kind: 'err', text: String(e.message || e) });
    }
  }
  useEffect(() => { load(); }, []);

  function upd(patch) { setCfg((c) => ({ ...(c || {}), ...patch })); }

  async function save() {
    setSaving(true); setNote(null);
    try {
      // O liga/desliga NÃO é gravado aqui — é por canal (edição da caixa GOWA).
      const body = {
        chat_id: cfg.chat_id || '',
        interval_min: Number(cfg.interval_min) || 15,
        timezone: cfg.timezone || 'America/Sao_Paulo', // sempre um fuso fixo
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
    <div>
      <h3 class="text-base font-semibold text-wa-text mb-1">Alertas de desconexão (Telegram)</h3>
      <p class="text-sm text-wa-secondary mb-3">
        Avisa no Telegram quando um número GOWA cair. O <strong>liga/desliga é por canal</strong>,
        feito na edição de cada caixa em <strong>Canais</strong>; aqui você define o bot, o destino
        e o fuso. Enquanto ficar fora do ar, o alerta é reenviado a cada ${Number(cfg.interval_min) || 15} min
        (a mensagem anterior é apagada). Usa um bot do Telegram próprio — não tem relação com a
        caixa de entrada do Telegram.
      </p>

      ${note && html`
        <div class=${'mb-3 px-3 py-2 rounded text-sm ' +
          (note.kind === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
          ${note.text}
        </div>`}

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
          <div class="px-3 py-2 rounded bg-wa-bg border border-wa-border text-sm text-wa-text break-all">
            ${cfg.panel_url_effective
              ? html`<strong>${cfg.panel_url_effective}</strong>/gowa/config`
              : '— (abra o painel pelo seu domínio para registrar a URL)'}
          </div>
          <div class=${HINT}>Endereço do WhatsBot salvo no sistema (detectado no seu 1º acesso pelo domínio). Usado no link do alerta.</div>
        </div>

        <div>
          <label class=${LABEL}>Reavisar a cada (minutos)</label>
          <input class=${FIELD + ' max-w-[8rem]'} type="number" min="1" step="1"
            value=${cfg.interval_min ?? 15} onInput=${(e) => upd({ interval_min: e.target.value })} />
        </div>

        <div>
          <label class=${LABEL}>Fuso horário (hora exibida nos alertas)</label>
          <${SearchableSelect} value=${cfg.timezone || 'America/Sao_Paulo'}
            onChange=${(v) => upd({ timezone: v })}
            options=${(cfg.timezones || []).map((t) => ({ value: t.value, label: t.label }))}
            inputClass=${FIELD + ' w-full'} searchPlaceholder="Pesquisar fuso…" />
          <div class=${HINT}>Fuso usado na hora exibida nos alertas. A lista traz todos os fusos do mundo.</div>
          ${cfg.timezone_effective && cfg.timezone !== cfg.timezone_effective ? html`
            <div class="text-xs text-amber-600 mt-1">
              Ainda não salvo — os alertas usam ${cfg.timezone_effective} até você clicar em Salvar.
            </div>` : null}
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
  // A tela do plugin cuida SÓ do alerta de desconexão (bot/destino/intervalo/fuso).
  // Pareamento, status de conexão e o liga/desliga do alerta ficam na tela Canais.
  return html`
    <div class="p-5 max-w-2xl mx-auto text-wa-text">
      <${DisconnectAlerts} apiBase=${apiBase} />
    </div>
  `;
}
