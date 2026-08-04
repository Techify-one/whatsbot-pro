// Aba "Campos do contato": conecta atributo personalizado do WhatsBot com campo
// personalizado do contato no Trackify, nos dois sentidos.
//
// Três coisas que a tela precisa dizer em palavras, porque nenhuma delas é
// adivinhável e todas já custaram confusão em campo:
//
//   1. só contato JÁ VINCULADO sincroniza — esta feature nunca cria cadastro no
//      Trackify (quem faz isso é o espelho de eventos, com o toggle dele);
//   2. a direção ← funciona SEM conta de serviço (usa a leitura read-only), então
//      a feature inteira não está bloqueada por uma senha que o operador pode não
//      ter em mãos;
//   3. escrever num campo IDENTIFICADOR re-chaveia a identidade do contato no
//      CDP, e um valor já pertencente a outro cadastro falha com 409.
//
// Dark mode: só classes `wa-*` e `.wa-field` (regra do repo). O seletor é um
// `<select>` nativo com `<optgroup>`, e não o OptionListSelect do core, porque
// esta tela roda dentro de um modal com overflow — onde o dropdown flutuante do
// componente do core fica cortado.
import { h } from 'preact';
import { useState, useEffect, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);
const MASK = '***';

const DIR_GLYPH = { to_trackify: '→', to_whatsbot: '←', both: '↔' };
const DIR_LABEL = {
  to_trackify: 'WhatsBot → Trackify',
  to_whatsbot: 'Trackify → WhatsBot',
  both: 'Nos dois sentidos',
};
const POLICY_LABEL = {
  whatsbot_wins: 'WhatsBot vence',
  trackify_wins: 'Trackify vence',
  hold: 'Segurar e avisar',
};

// A direção é guardada como ENUM, nunca como a seta: o glifo é só render. Um
// copiar-e-colar do JSON por um caminho não-UTF8 corromperia a semântica.
const ROW_EMPTY = {
  wb_scope: 'attribute', wb_key: '', tk_slug: '',
  direction: 'to_trackify', conflict_policy: 'whatsbot_wins',
  ack_identifier: false, enabled: true,
};

// Espelho de field_codec.compat — só para a dica visual. A barreira que de fato
// protege é o regex do campo aplicado valor a valor, no servidor.
const COMPAT = {
  text: ['text', 'string', 'textarea', 'email', 'phone', 'url', 'select'],
  number: ['number', 'integer', 'decimal', 'currency'],
  date: ['date', 'datetime'],
  list: ['select', 'text', 'string'],
  checkbox: ['boolean', 'checkbox', 'text', 'string'],
  link: ['url', 'text', 'string'],
};
const LOSSY = {
  number: ['text', 'string'], list: ['text', 'string'],
  checkbox: ['text', 'string'], date: ['text', 'string'],
};
const LOSSY_NOTE = {
  list: 'As opções da lista viram texto livre no Trackify. Um valor digitado lá pode voltar fora das opções e ser recusado aqui.',
  checkbox: 'Vira o texto "true"/"false" no Trackify.',
  number: 'Vira texto no Trackify: comparação e soma numérica deixam de funcionar lá.',
  date: 'Vira texto no Trackify: ordenação por data deixa de funcionar lá.',
};

function compat(wbType, tkType) {
  const wb = (wbType || 'text').toLowerCase();
  const tk = (tkType || '').trim().toLowerCase();
  if (!tk) return 'unknown';               // join não resolveu: não avisar nada
  if ((LOSSY[wb] || []).includes(tk)) return 'lossy';
  if ((COMPAT[wb] || []).includes(tk)) return 'ok';
  return 'bad';
}

function Badge({ tone, children }) {
  const tones = {
    warn: 'bg-amber-500/15 text-amber-500',
    bad: 'bg-red-500/15 text-red-500',
    info: 'bg-wa-teal/15 text-wa-teal',
  };
  return html`<span class=${`px-1.5 py-0.5 rounded text-[10px] font-medium ${tones[tone] || tones.info}`}>${children}</span>`;
}

function Empty({ title, children }) {
  return html`
    <div class="border border-wa-border rounded p-4 text-sm text-wa-secondary">
      <div class="text-wa-text font-medium mb-1">${title}</div>
      ${children}
    </div>`;
}

// ── API key ──────────────────────────────────────────────────────────────

export function ApiKeyCard({ state, onSave, onTest, busy }) {
  const [key, setKey] = useState('');
  const [veredito, setVeredito] = useState(null);

  useEffect(() => {
    if (state) setKey(state.key_masked || '');
  }, [state]);

  if (!state) return html`<section class="wa-panel border border-wa-border rounded p-4 text-sm text-wa-secondary">Carregando…</section>`;

  return html`
    <section class="wa-panel border border-wa-border rounded p-4 space-y-3">
      <div class="flex items-center justify-between">
        <h3 class="font-medium text-wa-text">API key do Trackify</h3>
        <${Badge} tone=${state.set ? 'info' : 'warn'}>${state.set ? 'configurada' : 'não configurada'}<//>
      </div>
      <p class="text-xs text-wa-secondary">
        Gere a chave no Trackify em <strong>Configurações → API Keys</strong>, com as
        permissões <code>read</code>, <code>contacts:write</code> e <code>ingest</code>.
        Ela é exibida uma única vez, na criação — o Trackify guarda só o hash.
        <br />É a <strong>única</strong> credencial do plugin: vale para ler a jornada,
        escrever campos (${DIR_GLYPH.to_trackify} e ${DIR_GLYPH.both}) e enviar eventos.
      </p>
      ${state.blocked_reason && html`
        <div class="text-xs text-red-500 border border-red-500/40 rounded p-2">${state.blocked_reason}</div>`}
      ${!state.blocked_reason && state.last_auth_error && html`
        <div class="text-xs text-amber-500 border border-amber-500/40 rounded p-2">
          Última autenticação falhou: ${state.last_auth_error}
        </div>`}
      <label class="block text-xs text-wa-secondary">Chave
        <input type="password" class="wa-field w-full mt-1 px-2 py-1 rounded text-sm"
          value=${key} onInput=${(e) => setKey(e.target.value)}
          placeholder="tk_………" />
      </label>
      ${state.api_base && html`
        <div class="text-[11px] text-wa-secondary">API deduzida: <code>${state.api_base}</code></div>`}
      <div class="flex items-center gap-2 flex-wrap">
        <button class="px-3 py-1 rounded bg-wa-teal text-white text-sm disabled:opacity-50"
          disabled=${busy} onClick=${async () => {
            setVeredito(null);
            await onSave({ key });
          }}>Salvar</button>
        <button class="px-3 py-1 rounded border border-wa-border text-sm disabled:opacity-50"
          disabled=${busy} onClick=${async () => {
            // Testa o que está DIGITADO, não só o que está salvo — é a diferença
            // entre conferir antes e descobrir pela fila de erro depois.
            const r = await onTest({ key });
            setVeredito((r && r.data) || { ok: false, message: 'Falha ao testar.' });
          }}>Testar acesso</button>
        ${veredito && html`
          <span class=${`text-xs ${veredito.ok && !(veredito.missing_scopes || []).length ? 'text-wa-teal' : 'text-red-500'}`}>
            ${veredito.message}${veredito.ok && veredito.name ? ` (${veredito.name})` : ''}
          </span>`}
      </div>
      ${veredito && veredito.ok && (veredito.scopes || []).length ? html`
        <div class="text-[11px] text-wa-secondary">
          Permissões da chave: ${veredito.scopes.join(', ')}
        </div>` : null}
    </section>`;
}

// ── Uma linha do mapeamento ──────────────────────────────────────────────

function MappingRow({ row, index, vocab, fields, tkReachable, credSet, pullEnabled,
                      errors, onChange, onRemove, canEdit }) {
  const leftValue = row.wb_key ? `${row.wb_scope}::${row.wb_key}` : '';
  const wbDef = useMemo(() => {
    const all = [...(vocab.columns || []).map((c) => ({ ...c, scope: 'column' })),
                 ...(vocab.attributes || []).map((a) => ({ ...a, scope: 'attribute' }))];
    return all.find((a) => a.scope === row.wb_scope && a.key === row.wb_key) || null;
  }, [vocab, row.wb_scope, row.wb_key]);
  const tkField = (fields || []).find((f) => f.slug === row.tk_slug) || null;

  const writesTk = row.direction === 'to_trackify' || row.direction === 'both';
  const writesWb = row.direction === 'to_whatsbot' || row.direction === 'both';
  const isIdentifier = !!(tkField && tkField.is_identifier);
  const verdict = wbDef ? compat(wbDef.type, tkField && tkField.field_type) : 'unknown';
  const rowErrors = errors || [];

  const identificadores = (fields || []).filter((f) => f.is_identifier);
  const comuns = (fields || []).filter((f) => !f.is_identifier);

  return html`
    <div class=${`border rounded p-3 space-y-2 ${rowErrors.length ? 'border-red-500' : 'border-wa-border'}`}>
      <div class="flex flex-wrap items-end gap-2">
        <label class="text-xs text-wa-secondary flex-1 min-w-[180px]">Campo no WhatsBot
          <select class="wa-field w-full mt-1 px-2 py-1 rounded text-sm" value=${leftValue}
            disabled=${!canEdit}
            onChange=${(e) => {
              const [scope, key] = String(e.target.value).split('::');
              // Trocar a esquerda ZERA a direita: o veredito de tipo, o aviso de
              // identificador e a regra de conflito são propriedades do PAR.
              onChange(index, { wb_scope: scope || 'attribute', wb_key: key || '',
                                tk_slug: '', ack_identifier: false });
            }}>
            <option value="">— escolha —</option>
            <optgroup label="Contato">
              ${(vocab.columns || []).map((c) => html`
                <option value=${`column::${c.key}`}>${c.label}</option>`)}
            </optgroup>
            <optgroup label="Atributos personalizados">
              ${(vocab.attributes || []).filter((a) => !a.is_system).map((a) => html`
                <option value=${`attribute::${a.key}`}>${a.label}</option>`)}
            </optgroup>
            <optgroup label="Atributos de sistema (de outros plugins)">
              ${(vocab.attributes || []).filter((a) => a.is_system).map((a) => html`
                <option value=${`attribute::${a.key}`}>${a.label}</option>`)}
            </optgroup>
          </select>
        </label>

        <label class="text-xs text-wa-secondary w-[190px]">Sentido
          <select class="wa-field w-full mt-1 px-2 py-1 rounded text-sm" value=${row.direction}
            disabled=${!canEdit}
            onChange=${(e) => onChange(index, { direction: e.target.value })}>
            ${Object.keys(DIR_LABEL).map((d) => html`
              <!-- Sem chave nenhum sentido funciona: a leitura também é HTTP
                   autenticado desde que o DSN direto ao banco do CDP acabou. -->
              <option value=${d} disabled=${!credSet
                                            || (d !== 'to_trackify' && !pullEnabled)}>
                ${DIR_GLYPH[d]} ${DIR_LABEL[d]}
              </option>`)}
          </select>
        </label>

        <label class="text-xs text-wa-secondary flex-1 min-w-[180px]">Campo no Trackify
          ${tkReachable ? html`
            <select class="wa-field w-full mt-1 px-2 py-1 rounded text-sm" value=${row.tk_slug}
              disabled=${!canEdit}
              onChange=${(e) => onChange(index, { tk_slug: e.target.value, ack_identifier: false })}>
              <option value="">— escolha —</option>
              ${identificadores.length > 0 && html`
                <optgroup label="Identificadores">
                  ${identificadores.map((f) => html`<option value=${f.slug}>${f.name}</option>`)}
                </optgroup>`}
              <optgroup label="Campos">
                ${comuns.map((f) => html`<option value=${f.slug}>${f.name}</option>`)}
              </optgroup>
            </select>` : html`
            <div class="mt-1 px-2 py-1 rounded border border-wa-border text-sm text-wa-secondary">
              ${row.tk_slug || '—'} <span class="text-[10px]">(não foi possível conferir)</span>
            </div>`}
        </label>

        ${canEdit && html`
          <button class="px-2 py-1 text-xs text-red-500 border border-red-500/40 rounded"
            onClick=${() => onRemove(index)}>Remover</button>`}
      </div>

      <div class="flex flex-wrap items-center gap-2">
        ${isIdentifier && html`<${Badge} tone="warn">Identificador<//>`}
        ${verdict === 'lossy' && html`<${Badge} tone="warn">conversão com perda<//>`}
        ${verdict === 'bad' && html`<${Badge} tone="bad">tipos incompatíveis<//>`}
        ${row.unverified && html`<${Badge} tone="warn">não verificado<//>`}
        ${row.direction === 'both' && html`
          <label class="text-xs text-wa-secondary ml-auto">Em caso de conflito
            <select class="wa-field ml-1 px-2 py-1 rounded text-xs" value=${row.conflict_policy}
              disabled=${!canEdit}
              onChange=${(e) => onChange(index, { conflict_policy: e.target.value })}>
              ${Object.keys(POLICY_LABEL).map((p) => html`
                <option value=${p}>${POLICY_LABEL[p]}</option>`)}
            </select>
          </label>`}
      </div>

      ${verdict === 'lossy' && wbDef && html`
        <p class="text-[11px] text-amber-500">${LOSSY_NOTE[wbDef.type] || ''}</p>`}

      ${isIdentifier && writesTk && html`
        <div class="border border-red-500/40 rounded p-2 space-y-1">
          <p class="text-[11px] text-red-500">
            <strong>Campo identificador.</strong> Escrever em <code>${row.tk_slug}</code>
            re-chaveia a identidade do contato no Trackify. Se o valor já pertencer a
            outro cadastro, a gravação falha com 409 e nada muda. Contatos podem ser
            fundidos ou separados sem aviso.
          </p>
          <label class="flex items-center gap-2 text-[11px] text-wa-text">
            <input type="checkbox" checked=${!!row.ack_identifier} disabled=${!canEdit}
              onChange=${(e) => onChange(index, { ack_identifier: e.target.checked })} />
            Entendi e quero escrever neste campo
          </label>
        </div>`}

      ${writesWb && wbDef && wbDef.is_system && html`
        <p class="text-[11px] text-red-500">
          Este atributo é mantido por outro plugin — deixar o Trackify escrever nele
          começa uma disputa de escrita. Use apenas ${DIR_GLYPH.to_trackify}.
        </p>`}

      ${rowErrors.map((e) => html`<p class="text-[11px] text-red-500">${e}</p>`)}
    </div>`;
}

// ── Editor ───────────────────────────────────────────────────────────────

function MappingEditor({ rows, vocab, tk, credSet, pullEnabled, errors,
                         onChange, onAdd, onRemove, onSave, busy, canEdit }) {
  if (!vocab || !tk) {
    return html`<section class="wa-panel border border-wa-border rounded p-4 text-sm text-wa-secondary">Carregando…</section>`;
  }
  if (!tk.configured) {
    return html`
      <section class="wa-panel border border-wa-border rounded p-4">
        <${Empty} title="Sem conexão de leitura com o Trackify">
          Sem o DSN do Nexus não dá para listar os campos do Trackify.
          Configure na aba <strong>Conexão</strong> e volte aqui.
        <//>
      </section>`;
  }
  if (tk.reachable && (tk.fields || []).length === 0) {
    return html`
      <section class="wa-panel border border-wa-border rounded p-4">
        <${Empty} title="Nenhum campo personalizado ativo no Trackify">
          Crie os campos no Trackify primeiro — este plugin não cria campos lá.
        <//>
      </section>`;
  }

  return html`
    <section class="wa-panel border border-wa-border rounded p-4 space-y-3">
      <div class="flex items-center justify-between">
        <h3 class="font-medium text-wa-text">Campos conectados</h3>
        <span class="text-xs text-wa-secondary">${(rows || []).length} mapeamento(s)</span>
      </div>
      <p class="text-xs text-wa-secondary">
        Cada campo do WhatsBot liga a um campo do Trackify, e cada um só pode
        aparecer uma vez. Só contatos <strong>já vinculados</strong> a um cadastro
        no Trackify sincronizam — esta função nunca cria contato lá.
      </p>
      ${!tk.reachable && html`
        <div class="text-xs text-amber-500 border border-amber-500/40 rounded p-2">
          Não foi possível ler os campos do Trackify agora. Os mapeamentos salvos
          continuam editáveis e removíveis — que é justamente o que se precisa
          fazer quando algo está errado.
        </div>`}
      ${errors._ && errors._.map((e) => html`<p class="text-xs text-red-500">${e}</p>`)}

      <div class="space-y-2">
        ${(rows || []).map((row, i) => html`
          <${MappingRow} key=${i} row=${row} index=${i} vocab=${vocab}
            fields=${tk.fields} tkReachable=${tk.reachable} credSet=${credSet}
            pullEnabled=${pullEnabled} errors=${errors[String(i)]}
            onChange=${onChange} onRemove=${onRemove} canEdit=${canEdit} />`)}
        ${(rows || []).length === 0 && html`
          <p class="text-sm text-wa-secondary">Nenhum campo conectado ainda.</p>`}
      </div>

      ${canEdit && html`
        <div class="flex items-center gap-2">
          <button class="px-3 py-1 rounded border border-wa-border text-sm"
            onClick=${onAdd}>+ Conectar um campo</button>
          <button class="px-3 py-1 rounded bg-wa-teal text-white text-sm disabled:opacity-50"
            disabled=${busy} onClick=${onSave}>Salvar mapeamentos</button>
        </div>`}
    </section>`;
}

// ── Status ───────────────────────────────────────────────────────────────

function fmtAgo(ts) {
  if (!ts) return 'nunca';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `há ${s}s`;
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  return `há ${Math.floor(s / 3600)} h`;
}

function SyncStatus({ data, onRefresh, busy }) {
  if (!data) return null;
  const m = data.mappings || [];
  const st = data.state || {};
  return html`
    <section class="wa-panel border border-wa-border rounded p-4 space-y-3">
      <div class="flex items-center justify-between">
        <h3 class="font-medium text-wa-text">Situação</h3>
        <button class="px-2 py-1 text-xs rounded border border-wa-border disabled:opacity-50"
          disabled=${busy} onClick=${onRefresh}>Atualizar</button>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
        <div><div class="text-wa-secondary">Sincronização</div>
          <div class="text-wa-text">${data.enabled ? (data.dry_run ? 'ligada (modo seco)' : 'ligada') : 'desligada'}</div></div>
        <div><div class="text-wa-secondary">Leitura do Trackify</div>
          <div class="text-wa-text">${data.pull_enabled ? 'ligada' : 'desligada'}</div></div>
        <div><div class="text-wa-secondary">Última leitura</div>
          <div class="text-wa-text">${fmtAgo(data.cursor_ts)}</div></div>
        <div><div class="text-wa-secondary">Pares acompanhados</div>
          <div class="text-wa-text">${st.total || 0}</div></div>
      </div>

      ${(data.enabled || data.pull_enabled) && !data.logged_in && html`
        <div class="text-xs text-red-500 border border-red-500/40 rounded p-2">
          <strong>A conta de serviço nunca autenticou.</strong> Sem isso nada é
          gravado no Trackify, e a leitura de volta nem chega a rodar — ela se
          recusa a ligar sem saber quem somos, senão reimportaria as próprias
          escritas como se fossem edições de uma pessoa.
          ${data.last_auth_error ? html`<div class="mt-1">${data.last_auth_error}</div>` : null}
        </div>`}

      ${m.length > 0 && html`
        <div class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead><tr class="text-wa-secondary text-left">
              <th class="py-1">Campo</th><th>Enviados</th><th>Recebidos</th>
              <th>Recusados</th><th>Conflitos</th><th>Último erro</th>
            </tr></thead>
            <tbody>
              ${m.map((r) => html`
                <tr class="border-t border-wa-border">
                  <td class="py-1 text-wa-text">${r.wb_key} ${DIR_GLYPH[r.direction]} ${r.tk_slug}</td>
                  <td>${r.pushed_ok}</td><td>${r.pulled_ok}</td>
                  <td>${r.pulled_rejected}</td><td>${r.conflicts}</td>
                  <td class="text-wa-secondary max-w-[220px] truncate" title=${r.last_error || ''}>
                    ${r.last_error || '—'}</td>
                </tr>`)}
            </tbody>
          </table>
        </div>`}

      ${(data.conflicts || []).length > 0 && html`
        <div>
          <div class="text-xs text-amber-500 mb-1">
            ${data.conflicts.length} conflito(s): os dois lados mudaram desde a última
            sincronização e a regra do mapeamento mandou segurar.
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-xs">
              <tbody>
                ${data.conflicts.map((c) => html`
                  <tr class="border-t border-wa-border">
                    <td class="py-1 text-wa-text">contato #${c.contact_id}</td>
                    <td class="text-wa-secondary">${c.conflict_reason}</td>
                  </tr>`)}
              </tbody>
            </table>
          </div>
        </div>`}
    </section>`;
}

// ── Simulação num contato ────────────────────────────────────────────────

function Simular({ req, busy }) {
  const [id, setId] = useState('');
  const [out, setOut] = useState(null);

  return html`
    <section class="wa-panel border border-wa-border rounded p-4 space-y-2">
      <h3 class="font-medium text-wa-text">Conferir num contato</h3>
      <p class="text-xs text-wa-secondary">
        Mostra o que seria escrito no Trackify para um contato específico, sem
        gravar nada. Use antes de desligar o modo seco.
      </p>
      <div class="flex items-center gap-2">
        <input class="wa-field px-2 py-1 rounded text-sm w-40" placeholder="ID do contato"
          value=${id} onInput=${(e) => setId(e.target.value)} />
        <button class="px-3 py-1 rounded border border-wa-border text-sm disabled:opacity-50"
          disabled=${busy || !id} onClick=${async () => {
            const r = await req('POST', '/field-sync/run', { contact_id: Number(id) });
            setOut((r && r.data) || { skip: (r && r.error) || 'Falha.' });
          }}>Simular</button>
      </div>
      ${out && html`
        <div class="text-xs space-y-1">
          ${out.skip
            ? html`<p class="text-amber-500">${out.skip}</p>`
            : html`
              <p class="text-wa-secondary">Cadastro no Trackify:
                <code>${out.trackify_contact_id}</code></p>
              ${Object.keys(out.would_write || {}).length === 0
                ? html`<p class="text-wa-teal">Nada a escrever: os dois lados já estão iguais.</p>`
                : html`<ul class="list-disc ml-4 text-wa-text">
                    ${Object.entries(out.would_write).map(([k, v]) => html`
                      <li><code>${k}</code> ← ${v === '' ? html`<em>apagar</em>` : v}</li>`)}
                  </ul>`}
              ${(out.decisions || []).map((d) => html`
                <p class="text-wa-secondary">${d.wb_key} ${DIR_GLYPH[d.direction]} ${d.tk_slug}:
                  <strong>${d.action}</strong>${d.reason ? ` — ${d.reason}` : ''}</p>`)}`}
        </div>`}
    </section>`;
}

// ── Raiz da aba ──────────────────────────────────────────────────────────

export default function FieldSync({ req, settings, onSaveSettings, busy, canEdit = true }) {
  const [vocab, setVocab] = useState(null);
  const [tk, setTk] = useState(null);
  const [rows, setRows] = useState(null);
  const [cred, setCred] = useState(null);
  const [status, setStatus] = useState(null);
  const [errors, setErrors] = useState({});
  const [flash, setFlash] = useState('');

  const loadAll = useCallback(async () => {
    const [v, f, m, c, s] = await Promise.all([
      req('GET', '/contact-attributes'), req('GET', '/trackify-fields'),
      req('GET', '/mappings'), req('GET', '/api-key'),
      req('GET', '/field-sync/status'),
    ]);
    if (v && v.ok) setVocab(v.data);
    if (f && f.ok) setTk(f.data);
    if (m && m.ok) setRows(m.data.rows || []);
    if (c && c.ok) setCred(c.data);
    if (s && s.ok) setStatus(s.data);
  }, [req]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // Helpers imutáveis (mesma forma do editor de regras do plugin protocolos).
  const change = useCallback((i, patch) => {
    setRows((rs) => (rs || []).map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }, []);
  const add = useCallback(() => setRows((rs) => [...(rs || []), { ...ROW_EMPTY }]), []);
  const remove = useCallback((i) => {
    setRows((rs) => (rs || []).filter((_, j) => j !== i));
  }, []);

  const save = useCallback(async () => {
    setErrors({}); setFlash('');
    const r = await req('PUT', '/mappings', { rows });
    if (r && r.ok) {
      setRows(r.data.rows || []);
      setFlash('Mapeamentos salvos.');
      const s = await req('GET', '/field-sync/status');
      if (s && s.ok) setStatus(s.data);
    } else {
      setErrors((r && r.data && r.data.row_errors) || { _: [(r && r.error) || 'Falha ao salvar.'] });
    }
  }, [req, rows]);

  const saveCred = useCallback(async (body) => {
    const r = await req('PUT', '/api-key', body);
    if (r && r.ok) { await loadAll(); setFlash('API key salva.'); }
    return r;
  }, [req, loadAll]);

  const testCred = useCallback((body) => req('POST', '/api-key/test', body), [req]);

  const s = settings || {};
  const credSet = !!(cred && cred.set);
  const pullEnabled = !!s.field_sync_pull_enabled;

  return html`
    <div class="space-y-4">
      <section class="wa-panel border border-wa-border rounded p-4 space-y-3">
        <h3 class="font-medium text-wa-text">Sincronização</h3>
        <label class="flex items-center gap-2 text-sm text-wa-text">
          <input type="checkbox" checked=${!!s.field_sync_enabled} disabled=${!canEdit || busy}
            onChange=${(e) => onSaveSettings({ field_sync_enabled: e.target.checked })} />
          Sincronizar campos do contato
        </label>
        <label class="flex items-center gap-2 text-sm text-wa-text">
          <input type="checkbox" checked=${!!s.field_sync_dry_run}
            disabled=${!canEdit || busy || !s.field_sync_enabled}
            onChange=${(e) => onSaveSettings({ field_sync_dry_run: e.target.checked })} />
          Modo seco (calcula e registra, mas não grava no Trackify)
        </label>
        ${s.field_sync_enabled && !s.field_sync_dry_run && html`
          <div class="text-xs text-amber-500 border border-amber-500/40 rounded p-2">
            Gravação real ligada. Toda escrita nossa também dispara as automações do
            Trackify — confira primeiro com o modo seco.
          </div>`}
        <label class="flex items-center gap-2 text-sm text-wa-text">
          <input type="checkbox" checked=${!!s.field_sync_pull_enabled} disabled=${!canEdit || busy}
            onChange=${(e) => onSaveSettings({ field_sync_pull_enabled: e.target.checked })} />
          Trazer alterações feitas no Trackify (necessário para ${DIR_GLYPH.to_whatsbot} e ${DIR_GLYPH.both})
        </label>
        <p class="text-[11px] text-wa-secondary">
          O Trackify não avisa quando alguém edita um contato, então a volta é por
          consulta periódica — o intervalo fica na aba Conexão.
        </p>
      </section>


      <${MappingEditor} rows=${rows} vocab=${vocab} tk=${tk} credSet=${credSet}
        pullEnabled=${pullEnabled} errors=${errors} onChange=${change} onAdd=${add}
        onRemove=${remove} onSave=${save} busy=${busy} canEdit=${canEdit} />

      ${flash && html`<p class="text-xs text-wa-teal">${flash}</p>`}

      <${Simular} req=${req} busy=${busy} />

      <${SyncStatus} data=${status} busy=${busy}
        onRefresh=${async () => {
          const r = await req('GET', '/field-sync/status');
          if (r && r.ok) setStatus(r.data);
        }} />
    </div>`;
}
