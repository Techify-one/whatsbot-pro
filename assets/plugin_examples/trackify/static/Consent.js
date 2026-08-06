// Aba "Descadastro por botão" da tela Configurar do plugin.
//
// O contato toca um botão de um template de marketing disparado pelo módulo
// Campanhas, e o resultado vira um valor no campo de descadastro do cadastro
// dele no Trackify. É esse campo que o Campanhas lê para montar a lista de
// disparo — ou seja, ele é o ÚNICO elo entre os dois sistemas.
//
// Três coisas que esta tela existe para resolver, e que um form declarativo não
// resolveria:
//
//   1. O operador NÃO SABE qual string a Meta devolve no clique. O template é
//      criado do lado do Campanhas e, sem payload explícito, a Meta ecoa o
//      próprio texto do botão. Por isso há duas fontes de preenchimento —
//      importar dos templates e "botões vistos recentemente" — e nenhuma delas
//      pede que ele adivinhe.
//   2. Um valor de descadastro mal escolhido (`0`, `nao`, vazio) produz uma
//      configuração que PARECE funcionar e não bloqueia ninguém. O aviso
//      vermelho traduz o contrato do Campanhas para a tela.
//   3. Canais e regras são LISTAS, e lista não pode morar em settings: o
//      `PUT /settings` do core é destrutivo. Elas vêm/vão por rota própria.
//
// ⚠️ `onSaveSettings` é o `patchSettings` do config.js, que reenvia o objeto de
// settings COMPLETO. Nunca chamar `req('PUT', '/settings', parcial)` daqui.
import { h } from 'preact';
import { useState, useEffect, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

const MEANINGS = [
  ['optout', 'Não quer mais receber'],
  ['optin', 'Quer continuar recebendo'],
  ['ignore', 'Sem efeito'],
];

const MATCH_FIELDS = [
  ['any', 'Payload ou texto'],
  ['payload', 'Só o payload'],
  ['text', 'Só o texto'],
];

const QUEUE_LABEL = {
  pending: 'Na fila', sending: 'Enviando', sent: 'Concluído',
  blocked: 'Bloqueado', failed: 'Falhou',
};

const INPUT = 'wa-field rounded border border-wa-border px-2 py-1 text-sm';

function fmtDate(epoch) {
  if (!epoch) return '—';
  try { return new Date(Number(epoch) * 1000).toLocaleString('pt-BR'); }
  catch (_) { return '—'; }
}

function Card({ title, hint, children }) {
  return html`
    <section class="rounded border border-wa-border bg-wa-panel p-3 space-y-3">
      <div>
        <h3 class="text-sm font-medium text-wa-text">${title}</h3>
        ${hint && html`<p class="text-xs text-wa-secondary mt-0.5">${hint}</p>`}
      </div>
      ${children}
    </section>`;
}

function Toggle({ checked, onChange, label, hint, disabled }) {
  return html`
    <label class="flex items-start gap-2 text-sm text-wa-text">
      <input type="checkbox" class="mt-0.5" checked=${!!checked} disabled=${!!disabled}
        onChange=${(e) => onChange(e.target.checked)} />
      <span>
        <span class="font-medium">${label}</span>
        ${hint && html`<span class="block text-xs text-wa-secondary">${hint}</span>`}
      </span>
    </label>`;
}

function Alert({ kind, children }) {
  const cls = kind === 'error'
    ? 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300'
    : kind === 'warn'
      ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'
      : 'border-wa-border bg-wa-hover text-wa-secondary';
  return html`<div class=${`rounded border px-2.5 py-2 text-xs ${cls}`}>${children}</div>`;
}

// ── 1. Interruptores, campo de destino e valores ─────────────────────────

function Geral({ status, settings, onSaveSettings, busy }) {
  const [draft, setDraft] = useState(null);

  useEffect(() => {
    if (!settings) return;
    setDraft({
      consent_field_slug: settings.consent_field_slug || '',
      consent_optout_value: settings.consent_optout_value ?? '',
      consent_optin_value: settings.consent_optin_value ?? '',
      consent_rate_per_min: settings.consent_rate_per_min ?? 10,
    });
  }, [settings]);

  if (!settings || !draft) return html`<${Card} title="Descadastro por botão">
    <p class="text-xs text-wa-secondary">Carregando…</p></${Card}>`;

  const field = status && status.field;
  const fieldError = status && status.field_error;
  const valueErrors = (status && status.value_errors) || [];

  return html`
    <${Card} title="Descadastro por botão"
      hint=${'Grava no campo de descadastro do contato no Trackify quando ele toca '
             + 'um botão de template num canal de WhatsApp oficial. Nunca cria contato lá.'}>

      <${Toggle} checked=${settings.consent_enabled} busy=${busy}
        label="Registrar descadastro por clique em botão"
        hint="Desligado, os cliques continuam sendo anotados em 'botões vistos' — só nada é gravado."
        onChange=${(v) => onSaveSettings({ consent_enabled: v })} />

      <${Toggle} checked=${settings.consent_dry_run}
        label="Modo seco (não grava no Trackify)"
        hint="Deixe ligado até conferir, na lista de botões vistos, que o texto que chega da Meta é o esperado."
        onChange=${(v) => onSaveSettings({ consent_dry_run: v })} />

      ${status && status.blocked_reason && html`
        <${Alert} kind="error">
          A sincronização está parada: ${status.blocked_reason}
        </${Alert}>`}

      ${status && status.field_map_conflict && html`
        <${Alert} kind="error">
          O campo <code>${draft.consent_field_slug}</code> também está mapeado na aba
          “Campos do contato”. A leitura periódica do Trackify pode
          <strong>desfazer</strong> um descadastro — remova o mapeamento de lá.
        </${Alert}>`}

      <div class="grid gap-3 sm:grid-cols-2">
        <label class="block text-sm">
          <span class="block text-wa-text mb-1">Campo de descadastro no Trackify</span>
          <input class=${`${INPUT} w-full`} value=${draft.consent_field_slug}
            onInput=${(e) => setDraft({ ...draft, consent_field_slug: e.target.value })} />
          <span class="block text-xs text-wa-secondary mt-1">
            Tem de ser o MESMO slug configurado no módulo Campanhas
            (<code>trackify_campo_optout</code>). Slugs diferentes não quebram nada
            e fazem o disparo continuar indo para quem pediu para sair.
          </span>
        </label>

        <label class="block text-sm">
          <span class="block text-wa-text mb-1">Gravações por minuto</span>
          <input type="number" min="1" max="25" class=${`${INPUT} w-full`}
            value=${draft.consent_rate_per_min}
            onInput=${(e) => setDraft({ ...draft, consent_rate_per_min: e.target.value })} />
          <span class="block text-xs text-wa-secondary mt-1">
            Divide com a sincronização de campos o balde de 30/min por IP do Trackify.
          </span>
        </label>

        <label class="block text-sm">
          <span class="block text-wa-text mb-1">Valor ao descadastrar</span>
          <input class=${`${INPUT} w-full`} value=${draft.consent_optout_value}
            onInput=${(e) => setDraft({ ...draft, consent_optout_value: e.target.value })} />
        </label>

        <label class="block text-sm">
          <span class="block text-wa-text mb-1">Valor ao voltar a receber</span>
          <input class=${`${INPUT} w-full`} value=${draft.consent_optin_value}
            placeholder="(vazio apaga o campo)"
            onInput=${(e) => setDraft({ ...draft, consent_optin_value: e.target.value })} />
        </label>
      </div>

      ${valueErrors.length > 0 && html`
        <${Alert} kind="error">
          <ul class="list-disc pl-4 space-y-0.5">
            ${valueErrors.map((m) => html`<li>${m}</li>`)}
          </ul>
        </${Alert}>`}

      ${fieldError && html`<${Alert} kind="warn">${fieldError}</${Alert}>`}

      ${field && !fieldError && html`
        <${Alert}>
          Campo <strong>${field.name || field.slug}</strong> encontrado no Trackify
          — tipo ${field.field_type || '?'}${field.is_identifier ? ', identificador' : ''}.
          ${field.regex_pattern && html`<span> Máscara exigida: <code>${field.regex_pattern}</code>.</span>`}
        </${Alert}>`}

      <button class="rounded bg-wa-teal px-3 py-1.5 text-sm text-white disabled:opacity-50"
        disabled=${busy}
        onClick=${() => onSaveSettings({
          consent_field_slug: String(draft.consent_field_slug || '').trim(),
          consent_optout_value: draft.consent_optout_value,
          consent_optin_value: draft.consent_optin_value,
          consent_rate_per_min: Math.min(25, Math.max(1,
            Math.round(Number(draft.consent_rate_per_min) || 10))),
        })}>Salvar</button>
    </${Card}>`;
}

// ── 2. Canais permitidos ─────────────────────────────────────────────────

function Canais({ status, onSave, busy }) {
  const canais = (status && status.channels) || [];
  const [sel, setSel] = useState(null);

  useEffect(() => {
    if (status) setSel(new Set(status.allowed_channel_ids || []));
  }, [status]);

  if (!sel) return null;

  const toggle = (id) => {
    const next = new Set(sel);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSel(next);
  };

  return html`
    <${Card} title="Canais"
      hint="Só canais de WhatsApp oficial aparecem aqui. GOWA, Telegram e teste nunca participam.">

      ${canais.length === 0 && html`
        <${Alert} kind="warn">
          Nenhum canal de WhatsApp oficial cadastrado. Sem ele não há clique de
          botão de template para capturar.
        </${Alert}>`}

      ${sel.size === 0 && canais.length > 0 && html`
        <${Alert} kind="warn">
          Nenhum canal marcado: <strong>nada será gravado</strong>. Os cliques
          continuam sendo anotados em “botões vistos”, então você não perde o
          material para configurar.
        </${Alert}>`}

      <div class="space-y-1">
        ${canais.map((c) => html`
          <label class="flex items-center gap-2 text-sm text-wa-text">
            <input type="checkbox" checked=${sel.has(c.channel_id)}
              onChange=${() => toggle(c.channel_id)} />
            <span>${c.display_name}</span>
            ${c.own_phone && html`<span class="text-xs text-wa-secondary">${c.own_phone}</span>`}
            ${!c.enabled && html`<span class="text-xs text-amber-600">(desativado)</span>`}
          </label>`)}
      </div>

      ${canais.length > 0 && html`
        <button class="rounded bg-wa-teal px-3 py-1.5 text-sm text-white disabled:opacity-50"
          disabled=${busy} onClick=${() => onSave([...sel])}>Salvar canais</button>`}
    </${Card}>`;
}

// ── 3. Regras de botão ───────────────────────────────────────────────────

function novaRegra(extra) {
  return {
    channel_id: '', match_field: 'any', match_value: '', meaning: 'optout',
    template_name: '', template_category: '', enabled: true, ...(extra || {}),
  };
}

function Botoes({ status, req, onSaved, busy }) {
  const canais = (status && status.channels) || [];
  const [rows, setRows] = useState(null);
  const [errors, setErrors] = useState({});
  const [msg, setMsg] = useState('');
  const [seen, setSeen] = useState([]);
  const [tplCanal, setTplCanal] = useState('');
  const [tpl, setTpl] = useState(null);
  const [carregandoTpl, setCarregandoTpl] = useState(false);

  useEffect(() => { if (status) setRows((status.rules || []).map((r) => ({ ...r }))); },
    [status]);

  const loadSeen = useCallback(async () => {
    const r = await req('GET', '/consent/seen?limit=50');
    if (r && r.ok) setSeen((r.data && r.data.rows) || []);
  }, [req]);

  useEffect(() => { loadSeen(); }, [loadSeen]);

  const jaConfigurados = useMemo(() => new Set(
    (rows || []).map((r) => `${r.channel_id || ''} ${(r.match_value || '').trim().toLowerCase()}`)),
    [rows]);

  const carregarTemplates = useCallback(async () => {
    if (!tplCanal) return;
    setCarregandoTpl(true);
    try {
      const r = await req('GET', `/consent/templates?channel_id=${encodeURIComponent(tplCanal)}`);
      setTpl(r && r.ok ? r.data : { supported: false, templates: [], message: (r && r.error) || 'falhou' });
    } finally { setCarregandoTpl(false); }
  }, [req, tplCanal]);

  const salvar = useCallback(async () => {
    setMsg('');
    const r = await req('PUT', '/consent/rules', { rows });
    if (r && r.ok) {
      setErrors({});
      setMsg('Regras salvas.');
      await onSaved();
      await loadSeen();
    } else {
      setErrors((r && r.data && r.data.row_errors) || {});
      setMsg((r && r.error) || 'Não foi possível salvar.');
    }
  }, [req, rows, onSaved, loadSeen]);

  if (!rows) return null;

  const setRow = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return html`
    <${Card} title="Botões"
      hint=${'O que cada botão significa. O casamento é pelo texto/payload — o clique '
             + 'que a Meta manda não carrega nome de template nem índice de botão.'}>

      ${rows.length === 0 && html`
        <${Alert} kind="warn">
          Nenhuma regra configurada: nenhum clique produz descadastro. Use
          “Importar dos templates” ou a lista de botões vistos abaixo.
        </${Alert}>`}

      ${rows.length > 0 && html`
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-xs text-wa-secondary">
              <tr>
                <th class="text-left py-1">Canal</th>
                <th class="text-left py-1">Casar por</th>
                <th class="text-left py-1">Texto do botão</th>
                <th class="text-left py-1">Significa</th>
                <th class="text-left py-1">Ativo</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              ${rows.map((r, i) => html`
                <tr class="border-t border-wa-border align-top">
                  <td class="py-1 pr-2">
                    <select class=${INPUT} value=${r.channel_id || ''}
                      onChange=${(e) => setRow(i, { channel_id: e.target.value })}>
                      <option value="">Todos os canais</option>
                      ${canais.map((c) => html`
                        <option value=${c.channel_id}>${c.display_name}</option>`)}
                    </select>
                  </td>
                  <td class="py-1 pr-2">
                    <select class=${INPUT} value=${r.match_field || 'any'}
                      onChange=${(e) => setRow(i, { match_field: e.target.value })}>
                      ${MATCH_FIELDS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
                    </select>
                  </td>
                  <td class="py-1 pr-2">
                    <input class=${`${INPUT} w-full min-w-[12rem]`} value=${r.match_value || ''}
                      onInput=${(e) => setRow(i, { match_value: e.target.value })} />
                    ${r.template_name && html`
                      <span class="block text-xs text-wa-secondary mt-0.5">
                        de ${r.template_name}${r.template_category ? ` · ${r.template_category}` : ''}
                      </span>`}
                    ${Number(r.hits) > 0 && html`
                      <span class="block text-xs text-wa-secondary">
                        ${r.hits} clique(s) · último em ${fmtDate(r.last_hit_at)}
                      </span>`}
                    ${(errors[String(i)] || []).map((m) => html`
                      <span class="block text-xs text-red-500 mt-0.5">${m}</span>`)}
                  </td>
                  <td class="py-1 pr-2">
                    <select class=${INPUT} value=${r.meaning || 'optout'}
                      onChange=${(e) => setRow(i, { meaning: e.target.value })}>
                      ${MEANINGS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
                    </select>
                  </td>
                  <td class="py-1 pr-2">
                    <input type="checkbox" checked=${r.enabled !== 0 && r.enabled !== false}
                      onChange=${(e) => setRow(i, { enabled: e.target.checked })} />
                  </td>
                  <td class="py-1 text-right">
                    <button class="text-xs text-red-500"
                      onClick=${() => setRows(rows.filter((_, j) => j !== i))}>Remover</button>
                  </td>
                </tr>`)}
            </tbody>
          </table>
        </div>`}

      ${errors._ && html`<${Alert} kind="error">${errors._.join(' ')}</${Alert}>`}

      <div class="flex flex-wrap items-center gap-2">
        <button class="rounded border border-wa-border px-3 py-1.5 text-sm text-wa-text"
          onClick=${() => setRows([...rows, novaRegra()])}>Adicionar linha</button>
        <button class="rounded bg-wa-teal px-3 py-1.5 text-sm text-white disabled:opacity-50"
          disabled=${busy} onClick=${salvar}>Salvar botões</button>
        ${msg && html`<span class="text-xs text-wa-secondary">${msg}</span>`}
      </div>

      <!-- Importar dos templates da Meta -->
      <div class="rounded border border-wa-border p-2.5 space-y-2">
        <p class="text-xs text-wa-secondary">
          <strong class="text-wa-text">Importar dos templates.</strong> Lista os
          templates <strong>MARKETING</strong> da conta Meta do canal que têm botão
          de resposta rápida. Templates de utilidade e autenticação não aparecem.
        </p>
        <div class="flex flex-wrap items-center gap-2">
          <select class=${INPUT} value=${tplCanal}
            onChange=${(e) => { setTplCanal(e.target.value); setTpl(null); }}>
            <option value="">Escolha o canal…</option>
            ${canais.map((c) => html`<option value=${c.channel_id}>${c.display_name}</option>`)}
          </select>
          <button class="rounded border border-wa-border px-3 py-1.5 text-sm text-wa-text disabled:opacity-50"
            disabled=${!tplCanal || carregandoTpl} onClick=${carregarTemplates}>
            ${carregandoTpl ? 'Buscando…' : 'Buscar templates'}
          </button>
        </div>
        ${tpl && !tpl.supported && html`
          <${Alert} kind="warn">${tpl.message || 'Este canal não sabe listar templates.'}</${Alert}>`}
        ${tpl && tpl.supported && tpl.templates.length === 0 && html`
          <${Alert}>${tpl.message
            || 'Nenhum template de marketing com botão de resposta rápida nesta conta.'}</${Alert}>`}
        ${tpl && tpl.supported && tpl.templates.map((t) => html`
          <div class="border-t border-wa-border pt-2">
            <p class="text-sm text-wa-text">${t.name}
              <span class="text-xs text-wa-secondary">${t.language} · ${t.status}</span></p>
            <div class="flex flex-wrap gap-1.5 mt-1">
              ${t.buttons.map((b) => {
                const chave = `${tplCanal} ${b.text.trim().toLowerCase()}`;
                const ja = jaConfigurados.has(chave);
                return html`
                  <button class=${`rounded border px-2 py-1 text-xs ${ja
                      ? 'border-wa-border text-wa-secondary'
                      : 'border-wa-teal text-wa-text'}`}
                    disabled=${ja}
                    onClick=${() => setRows([...rows, novaRegra({
                      channel_id: tplCanal,
                      match_value: b.payload || b.text,
                      template_name: t.name,
                      template_category: t.category,
                    })])}>
                    ${b.text}${ja ? ' ✓' : ' +'}
                  </button>`;
              })}
            </div>
          </div>`)}
      </div>

      <!-- Botões vistos e ainda sem regra -->
      <div class="rounded border border-wa-border p-2.5 space-y-2">
        <div class="flex items-center justify-between">
          <p class="text-xs text-wa-secondary">
            <strong class="text-wa-text">Botões vistos recentemente.</strong> Cliques
            que chegaram e não casaram nenhuma regra. É a forma segura de descobrir
            a string exata que a Meta devolve.
          </p>
          <button class="text-xs text-wa-secondary" onClick=${loadSeen}>Atualizar</button>
        </div>
        ${seen.length === 0 && html`
          <p class="text-xs text-wa-secondary">Nada ainda. Dispare um template com botão
            para o seu número de teste e clique nele.</p>`}
        ${seen.length > 0 && html`
          <table class="w-full text-xs">
            <thead class="text-wa-secondary">
              <tr>
                <th class="text-left py-1">Texto / payload</th>
                <th class="text-left py-1">Forma</th>
                <th class="text-left py-1">Cliques</th>
                <th class="text-left py-1">Último</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              ${seen.map((s) => html`
                <tr class="border-t border-wa-border">
                  <td class="py-1 text-wa-text">${s.raw_text || s.raw_payload || s.match_norm}</td>
                  <td class="py-1 text-wa-secondary">${s.shape}</td>
                  <td class="py-1 text-wa-secondary">${s.hits}</td>
                  <td class="py-1 text-wa-secondary">${fmtDate(s.last_seen_at)}</td>
                  <td class="py-1 text-right">
                    <button class="text-wa-teal"
                      onClick=${() => setRows([...rows, novaRegra({
                        channel_id: s.channel_id || '',
                        match_value: s.raw_payload || s.raw_text,
                      })])}>Usar como regra</button>
                  </td>
                </tr>`)}
            </tbody>
          </table>`}
      </div>
    </${Card}>`;
}

// ── 4. Fila ──────────────────────────────────────────────────────────────

function Fila({ req, busy }) {
  const [data, setData] = useState(null);

  const load = useCallback(async () => {
    const r = await req('GET', '/consent/queue?limit=50');
    if (r && r.ok) setData(r.data);
  }, [req]);

  useEffect(() => { load(); }, [load]);

  const reprocessar = useCallback(async () => {
    await req('POST', '/consent/queue/retry');
    await load();
  }, [req, load]);

  const stats = (data && data.stats) || {};
  const rows = (data && data.rows) || [];
  const travados = rows.filter((r) => r.status === 'failed' || r.status === 'blocked');

  return html`
    <${Card} title="Fila de gravação"
      hint="O clique é capturado na hora, mas a gravação no Trackify é feita por uma tarefa de fundo.">
      <div class="flex flex-wrap items-center gap-3 text-xs text-wa-secondary">
        ${Object.keys(stats).length === 0 && html`<span>Fila vazia.</span>`}
        ${Object.entries(stats).map(([k, v]) => html`
          <span>${QUEUE_LABEL[k] || k}: <strong class="text-wa-text">${v}</strong></span>`)}
        <button class="ml-auto text-wa-secondary" onClick=${load}>Atualizar</button>
        ${travados.length > 0 && html`
          <button class="rounded border border-wa-border px-2 py-1 text-wa-text disabled:opacity-50"
            disabled=${busy} onClick=${reprocessar}>Reprocessar ${travados.length}</button>`}
      </div>
      ${travados.length > 0 && html`
        <table class="w-full text-xs">
          <thead class="text-wa-secondary">
            <tr>
              <th class="text-left py-1">Contato</th>
              <th class="text-left py-1">Motivo</th>
              <th class="text-left py-1">Erro</th>
              <th class="text-left py-1">Quando</th>
            </tr>
          </thead>
          <tbody>
            ${travados.map((r) => html`
              <tr class="border-t border-wa-border">
                <td class="py-1 text-wa-text">#${r.contact_id}</td>
                <td class="py-1 text-wa-secondary">${r.reason}</td>
                <td class="py-1 text-wa-secondary">${r.last_error}</td>
                <td class="py-1 text-wa-secondary">${fmtDate(r.updated_at)}</td>
              </tr>`)}
          </tbody>
        </table>`}
    </${Card}>`;
}

// ── Composição ───────────────────────────────────────────────────────────

export default function Consent({ req, settings, onSaveSettings, busy }) {
  const [status, setStatus] = useState(null);

  const load = useCallback(async () => {
    const r = await req('GET', '/consent/status');
    if (r && r.ok) setStatus(r.data);
  }, [req]);

  useEffect(() => { load(); }, [load]);

  const salvarSettings = useCallback(async (patch) => {
    const r = await onSaveSettings(patch);
    await load();
    return r;
  }, [onSaveSettings, load]);

  const salvarCanais = useCallback(async (ids) => {
    await req('PUT', '/consent/channels', { channel_ids: ids });
    await load();
  }, [req, load]);

  return html`
    <div class="space-y-4">
      <${Geral} status=${status} settings=${settings}
        onSaveSettings=${salvarSettings} busy=${busy} />
      <${Canais} status=${status} onSave=${salvarCanais} busy=${busy} />
      <${Botoes} status=${status} req=${req} onSaved=${load} busy=${busy} />
      <${Fila} req=${req} busy=${busy} />
    </div>`;
}
