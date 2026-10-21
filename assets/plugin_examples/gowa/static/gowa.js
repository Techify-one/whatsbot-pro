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
              ? html`<strong>${cfg.panel_url_effective}</strong>/channels/<em>canal</em>?connect=1`
              : '— (abra o painel pelo seu domínio para registrar a URL)'}
          </div>
          <div class=${HINT}>Endereço do WhatsBot salvo no sistema (detectado no seu 1º acesso pelo domínio). O link "Reconectar agora" do alerta abre a tela de Canais, já no QR da caixa que caiu.</div>
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

// ── Versão e atualização do binário do GOWA ──────────────────────────────────

const PHASE_LABEL = {
  downloading: 'Baixando a nova versão',
  verifying: 'Conferindo a integridade do arquivo',
  installing: 'Instalando',
  restarting: 'Reiniciando o GOWA',
  health_check: 'Aguardando o GOWA responder',
  rollback: 'Revertendo para a versão anterior',
};

function fmtWhen(ts) {
  if (!ts) return '';
  try { return new Date(ts * 1000).toLocaleString('pt-BR'); } catch { return ''; }
}

function GowaVersion({ apiBase }) {
  const [info, setInfo] = useState(null);   // status local (+ release do GitHub após "Verificar")
  const [denied, setDenied] = useState(false);
  const [checking, setChecking] = useState(false);
  const [starting, setStarting] = useState(false);
  const [note, setNote] = useState(null);

  async function request(path, init) {
    const r = await apiFetch(`${apiBase}${path}`, init);
    if (r.status === 403) { setDenied(true); return null; }
    return r.json();
  }

  async function loadStatus() {
    try {
      const res = await request('/binary/status');
      if (res && res.ok) setInfo((prev) => ({ ...(prev || {}), ...res.data }));
    } catch (e) { setNote({ kind: 'err', text: String(e.message || e) }); }
  }

  async function check(force) {
    setChecking(true); setNote(null);
    try {
      const res = await request(`/binary/check${force ? '?force=true' : ''}`);
      if (res && res.ok) setInfo(res.data);
      else if (res) setNote({ kind: 'err', text: res.error || 'Falha ao verificar.' });
    } catch (e) { setNote({ kind: 'err', text: String(e.message || e) }); }
    finally { setChecking(false); }
  }

  useEffect(() => { loadStatus(); check(false); }, []);

  // Enquanto uma troca roda (aqui ou em outra aba) acompanha o andamento. O servidor
  // segue de pé durante a troca: só o GOWA reinicia, então o polling não cai.
  const busy = !!(info && info.busy);
  useEffect(() => {
    if (!busy) return undefined;
    const t = setInterval(loadStatus, 1500);
    return () => clearInterval(t);
  }, [busy]);

  // Quando a troca termina, mostra o desfecho e atualiza a comparação com o GitHub.
  const lastResult = info && info.state && info.state.last_result;
  const lastKey = lastResult ? `${lastResult.finished_at}` : '';
  useEffect(() => {
    if (!lastResult || busy) return;
    // Só avisa do que terminou AGORA. O servidor guarda o último resultado em memória;
    // sem este corte, abrir a tela horas depois mostraria "atualizado…" como novidade
    // (a linha "Última operação" abaixo já cobre o histórico, com data).
    if (Date.now() / 1000 - (lastResult.finished_at || 0) < 600) {
      setNote({ kind: lastResult.ok ? 'ok' : 'err', text: lastResult.message || (lastResult.ok ? 'Concluído.' : 'Falhou.') });
    }
    check(false);
  }, [lastKey, busy]);

  async function install(version, confirmUnsupported) {
    setStarting(true); setNote(null);
    try {
      const res = await request('/binary/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version, confirm_unsupported: !!confirmUnsupported }),
      });
      if (!res) return;
      if (res.requires_confirmation) {
        if (window.confirm(`${res.message}\n\nInstalar mesmo assim?`)) {
          await install(version, true);
        }
        return;
      }
      if (!res.ok) { setNote({ kind: 'err', text: res.error || res.message || 'Não foi possível iniciar.' }); return; }
      await loadStatus();
    } catch (e) { setNote({ kind: 'err', text: String(e.message || e) }); }
    finally { setStarting(false); }
  }

  function confirmInstall() {
    const from = info.installed_version || '?';
    const to = info.latest_version;
    const major = info.major_jump
      ? '\n\nÉ uma mudança de versão MAIOR: o banco de sessão do WhatsApp pode ser migrado e uma cópia de segurança é feita antes. '
      : '\n\n';
    const ok = window.confirm(
      `Atualizar o GOWA de ${from} para ${to}?${major}` +
      'Todos os números ficam desconectados por alguns segundos durante a troca ' +
      '(os alertas de desconexão são pausados nesse período). Se algo falhar, a versão anterior volta sozinha.');
    if (ok) install(to, false);
  }

  async function rollback() {
    if (!window.confirm(`Reverter o GOWA para a versão ${info.backup_version || 'anterior'}? Os números ficam desconectados por alguns segundos.`)) return;
    setStarting(true); setNote(null);
    try {
      const res = await request('/binary/rollback', { method: 'POST' });
      if (res && !res.ok) setNote({ kind: 'err', text: res.error || 'Não foi possível reverter.' });
      await loadStatus();
    } catch (e) { setNote({ kind: 'err', text: String(e.message || e) }); }
    finally { setStarting(false); }
  }

  if (denied) {
    return html`<div class=${HINT}>Seu usuário não tem permissão para gerenciar a versão do GOWA (exige "Ativar/desativar/configurar plugins").</div>`;
  }
  if (!info) return html`<div class=${HINT}>Carregando versão do GOWA…</div>`;
  if (info.managed === false) {
    return html`
      <div>
        <h3 class="text-base font-semibold text-wa-text mb-1">Versão do GOWA</h3>
        <div class=${HINT}>O GOWA não está sob o controle deste plugin agora (plugin recém-ativado ou binário ausente). Reinicie o servidor para gerenciar a versão aqui.</div>
      </div>`;
  }

  const st = info.state || {};
  const running = busy || starting;
  const phase = PHASE_LABEL[st.phase] || 'Trabalhando';
  const pct = Number(st.progress) || 0;
  const canUpdate = !running && info.update_available && info.platform_supported;

  return html`
    <div>
      <h3 class="text-base font-semibold text-wa-text mb-1">Versão do GOWA</h3>
      <p class="text-sm text-wa-secondary mb-3">
        O GOWA é o programa que conecta o WhatsApp. Aqui você vê a versão em uso e atualiza
        para a última publicada, sem rebuild nem acesso ao servidor.
      </p>

      ${note && html`
        <div class=${'mb-3 px-3 py-2 rounded text-sm ' +
          (note.kind === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
          ${note.text}
        </div>`}

      <div class="rounded border border-wa-border bg-wa-panel p-3 space-y-2 text-sm">
        <div class="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <div>Em uso: <strong>${info.installed_version || '—'}</strong>
            ${info.supported ? '' : html`<span class="text-amber-600 text-xs ml-1">(fora da faixa homologada ${info.supported_range})</span>`}
          </div>
          <div>Última publicada: <strong>${info.latest_version || '—'}</strong>
            ${info.published_at ? html`<span class="text-wa-secondary text-xs ml-1">(${new Date(info.published_at).toLocaleDateString('pt-BR')})</span>` : ''}
          </div>
        </div>
        <div class=${HINT}>
          Origem: ${info.source === 'managed' ? 'instalada por este painel' : 'a que veio com o sistema'}
          · Plataforma: ${info.platform}
        </div>

        ${info.error ? html`<div class="text-xs text-amber-600">${info.error}</div>` : null}
        ${info.rate_limited ? html`<div class="text-xs text-amber-600">O GitHub limitou as consultas; mostrando o último resultado guardado.</div>` : null}
        ${!info.platform_supported ? html`<div class="text-xs text-amber-600">O GOWA não publica binário para esta plataforma — atualização automática indisponível.</div>` : null}

        ${running ? html`
          <div>
            <div class="text-xs text-wa-secondary mb-1">${phase}${st.version ? ` (${st.version})` : ''}${pct && pct < 100 ? ` — ${pct}%` : ''}…</div>
            <div class="h-2 rounded bg-wa-hover overflow-hidden">
              <div class="h-2 bg-wa-teal" style=${{ width: `${Math.max(pct, 5)}%`, transition: 'width .3s' }}></div>
            </div>
          </div>` : null}

        ${!running && info.update_available ? html`
          <div class="text-sm">
            Há uma versão nova: <strong>${info.latest_version}</strong>.
            ${info.major_jump ? html`<span class="text-amber-600"> É uma mudança de versão maior — a sessão do WhatsApp é copiada antes, por segurança.</span>` : null}
            ${info.latest_supported ? '' : html`<span class="text-amber-600"> Fora da faixa homologada (${info.supported_range}): pedirá confirmação.</span>`}
            ${info.release_url ? html` <a class="underline text-wa-teal" href=${info.release_url} target="_blank" rel="noopener noreferrer">Ver novidades</a>` : null}
          </div>` : null}
        ${!running && !info.update_available && info.latest_version && !info.error ? html`
          <div class="text-sm text-wa-secondary">Você já está na versão mais recente.</div>` : null}

        <div class="flex flex-wrap gap-2 pt-1">
          <button class="px-4 py-2 rounded bg-wa-teal text-white text-sm font-medium disabled:opacity-50"
            disabled=${!canUpdate} onClick=${confirmInstall}>
            ${running ? 'Atualizando…' : (info.update_available ? `Atualizar para ${info.latest_version}` : 'Atualizar')}
          </button>
          <button class="px-4 py-2 rounded border border-wa-border text-wa-text text-sm disabled:opacity-50"
            disabled=${running || checking} onClick=${() => check(true)}>
            ${checking ? 'Verificando…' : 'Verificar atualizações'}
          </button>
          ${info.can_rollback ? html`
            <button class="px-4 py-2 rounded border border-wa-border text-wa-text text-sm disabled:opacity-50"
              disabled=${running} onClick=${rollback}>
              Reverter para ${info.backup_version || 'a anterior'}
            </button>` : null}
        </div>
        ${lastResult && lastResult.finished_at ? html`
          <div class=${HINT}>Última operação: ${fmtWhen(lastResult.finished_at)} — ${lastResult.ok ? 'sucesso' : 'falhou'}${lastResult.rolled_back ? ' (revertida automaticamente)' : ''}.</div>` : null}
      </div>
    </div>
  `;
}

export default function GowaConfig({ apiBase = '/api/plugins/gowa' } = {}) {
  // Duas seções: a versão do binário do GOWA e o alerta de desconexão (bot/destino/
  // intervalo/fuso). Pareamento, status de conexão e o liga/desliga do alerta ficam
  // na tela Canais.
  return html`
    <div class="p-5 max-w-2xl mx-auto text-wa-text space-y-8">
      <${GowaVersion} apiBase=${apiBase} />
      <${DisconnectAlerts} apiBase=${apiBase} />
    </div>
  `;
}
