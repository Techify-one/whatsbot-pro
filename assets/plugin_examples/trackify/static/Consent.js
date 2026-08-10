// Aba "Descadastro por botão" da tela Configurar do plugin.
//
// O contato toca um botão de um template disparado pelo módulo Campanhas, o
// clique traz um CÓDIGO no payload, e é aqui que o código ganha função. Hoje há
// uma função: descadastro de marketing — o contato é marcado no campo de
// descadastro do Trackify, que é o campo que o Campanhas lê para montar a lista
// de disparo.
//
// A tela é DIRIGIDA PELO SERVIDOR: `status.actions` traz uma linha por ação
// registrada em `actions.py`, com seus rótulos, seus códigos e os descritores
// dos campos que ela expõe. Ação nova = uma dataclass em Python e nenhuma linha
// de JS. É por isso que "Campo de descadastro" e "Valor ao descadastrar" moram
// DENTRO da linha da ação e não num cartão global: eles são o alvo de UMA ação,
// e uma segunda ação teria os códigos numa linha e o campo-alvo noutro cartão.
//
// O que continua exigindo tela (e não um form declarativo):
//
//   1. Um valor de descadastro mal escolhido (`0`, `nao`, vazio) produz uma
//      configuração que PARECE funcionar e não bloqueia ninguém.
//   2. O veredito ao vivo do campo no CDP (existe? está ativo? é identificador?)
//      e o conflito com a aba "Campos do contato" só existem no servidor.
//   3. Um código reivindicado por DUAS ações não vale para nenhuma — sem o aviso
//      o sintoma é "não acontece nada".
//
// ⚠️ `onSaveSettings` é o `patchSettings` do config.js, que reenvia o objeto de
// settings COMPLETO. Nunca chamar `req('PUT', '/settings', parcial)` daqui.
import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

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

// ── 1. Interruptores globais ─────────────────────────────────────────────

function Geral({ status, settings, onSaveSettings, busy }) {
  if (!settings) return html`<${Card} title="Descadastro por botão">
    <p class="text-xs text-wa-secondary">Carregando…</p></${Card}>`;

  const canais = (status && status.channels) || [];
  const conflitos = (status && status.code_conflicts) || [];

  return html`
    <${Card} title="Descadastro por botão"
      hint=${'Um clique de botão num canal de WhatsApp oficial vira uma ação no '
             + 'cadastro do contato no Trackify.'}>

      <${Toggle} checked=${settings.consent_enabled} disabled=${busy}
        label="Registrar ações de clique em botão"
        hint="Vale para todos os canais de WhatsApp oficial. GOWA, Telegram e teste nunca participam."
        onChange=${(v) => onSaveSettings({ consent_enabled: v })} />

      <${Toggle} checked=${settings.consent_dry_run} disabled=${busy}
        label="Modo seco (não grava no Trackify)"
        hint="Deixe ligado até confirmar que os códigos combinados com o módulo Campanhas estão chegando."
        onChange=${(v) => onSaveSettings({ consent_dry_run: v })} />

      <${Toggle} checked=${settings.cdp_autocadastro_enabled !== false} disabled=${busy}
        label="Cadastrar automaticamente os contatos no Trackify"
        hint=${'Sem isso, o descadastro de quem ainda não tem ficha no CDP é '
               + 'registrado e descartado. Grupos e contatos sem telefone de '
               + 'verdade nunca são criados; contato no Trackify não tem exclusão '
               + 'definitiva.'}
        onChange=${(v) => onSaveSettings({ cdp_autocadastro_enabled: v })} />

      ${settings.cdp_autocadastro_enabled !== false && html`
        <${Toggle} checked=${!!settings.cdp_autocadastro_dry_run} disabled=${busy}
          label="Cadastro automático em modo seco (não cria no Trackify)"
          hint="Registra o que seria criado, sem criar."
          onChange=${(v) => onSaveSettings({ cdp_autocadastro_dry_run: v })} />`}

      ${status && canais.length === 0 && html`
        <${Alert} kind="warn">
          Nenhum canal de WhatsApp oficial cadastrado. Sem ele não há clique de
          botão de template para capturar.
        </${Alert}>`}

      ${status && status.blocked_reason && html`
        <${Alert} kind="error">
          A sincronização está parada: ${status.blocked_reason}
        </${Alert}>`}

      ${status && status.field_error && html`
        <${Alert} kind="warn">${status.field_error}</${Alert}>`}

      ${conflitos.length > 0 && html`
        <${Alert} kind="error">
          <p class="mb-1">
            <strong>Códigos reivindicados por mais de uma ação.</strong> Enquanto
            estiverem duplicados eles não valem para <em>nenhuma</em> delas —
            deixe cada código em uma ação só.
          </p>
          <ul class="list-disc pl-4 space-y-0.5">
            ${conflitos.map((c) => html`
              <li><code>${c.code}</code> — ${(c.actions || []).join(', ')}</li>`)}
          </ul>
        </${Alert}>`}
    </${Card}>`;
}

// ── 2. Uma linha por AÇÃO, vinda do servidor ─────────────────────────────

function AcaoCard({ acao, settings, onSaveSettings, onRetryClick, busy }) {
  const [draft, setDraft] = useState(null);
  // Qual linha está sendo reentregue. Por contato, não um booleano global: a
  // tabela costuma ter várias linhas e travar todas por causa de uma esconderia
  // quais já foram tentadas.
  const [tentando, setTentando] = useState(null);

  const onRetry = useCallback(async (contactId, actionId) => {
    setTentando(contactId);
    try { await onRetryClick(contactId, actionId); }
    finally { setTentando(null); }
  }, [onRetryClick]);

  // Semente vinda das settings, não do status: o status DESCREVE os campos, o
  // valor é sempre do objeto de settings (fonte única).
  useEffect(() => {
    if (!settings) return;
    const d = { [acao.codes_setting]: (acao.codes || []).join(', ') };
    for (const f of acao.settings_fields || []) d[f.key] = settings[f.key] ?? '';
    setDraft(d);
  }, [settings, acao]);

  if (!draft) return null;

  const erros = acao.errors || [];
  // `codes_active` já vem filtrado pelo servidor: a normalização é do Python e
  // refazê-la aqui divergiria nos casos que o operador não vê.
  const codigos = acao.codes_active || [];
  const conflitos = acao.code_conflicts || [];

  return html`
    <${Card} title=${acao.label} hint=${acao.description}>

      <label class="block text-sm">
        <span class="block text-wa-text mb-1">${acao.codes_label}</span>
        <textarea class=${`${INPUT} w-full`} rows="2"
          placeholder=${acao.codes_placeholder}
          value=${draft[acao.codes_setting]}
          onInput=${(e) => setDraft({ ...draft, [acao.codes_setting]: e.target.value })} />
        <span class="block text-xs text-wa-secondary mt-1">${acao.codes_help}</span>
      </label>

      ${codigos.length > 0 && html`
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="text-xs text-wa-secondary">Reconhecidos:</span>
          ${codigos.map((c) => html`
            <code class="rounded bg-wa-hover px-1.5 py-0.5 text-xs text-wa-text">${c}</code>`)}
        </div>`}

      ${codigos.length === 0 && conflitos.length === 0 && html`
        <${Alert} kind="warn">
          Sem código, nenhum clique ativa esta ação.
        </${Alert}>`}

      ${conflitos.length > 0 && html`
        <${Alert} kind="error">
          ${conflitos.length === 1 ? 'O código' : 'Os códigos'}
          ${' '}${conflitos.map((c) => html`<code class="mx-0.5">${c}</code>`)}
          ${' '}também ${conflitos.length === 1 ? 'pertence' : 'pertencem'} a outra
          ação — enquanto estiverem duplicados não valem para nenhuma delas.
        </${Alert}>`}

      ${(acao.settings_fields || []).length > 0 && html`
        <div class="grid gap-3 sm:grid-cols-2">
          ${acao.settings_fields.map((f) => html`
            <label class="block text-sm" key=${f.key}>
              <span class="block text-wa-text mb-1">${f.label}</span>
              <input class=${`${INPUT} w-full`} placeholder=${f.placeholder || ''}
                value=${draft[f.key] ?? ''}
                onInput=${(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
              ${f.help && html`
                <span class="block text-xs text-wa-secondary mt-1">${f.help}</span>`}
            </label>`)}
        </div>`}

      ${(acao.config_errors || []).length > 0 && html`
        <${Alert} kind="error">
          <ul class="list-disc pl-4 space-y-0.5">
            ${acao.config_errors.map((m) => html`<li>${m}</li>`)}
          </ul>
        </${Alert}>`}

      ${acao.field_map_conflict && html`
        <${Alert} kind="error">
          Um dos campos desta ação também está mapeado na aba “Campos do
          contato”. A leitura periódica do Trackify pode <strong>desfazer</strong>
          o que ela grava — remova o mapeamento de lá.
        </${Alert}>`}

      ${acao.field_error && html`<${Alert} kind="warn">${acao.field_error}</${Alert}>`}

      ${acao.field && !acao.field_error && html`
        <${Alert}>
          Campo <strong>${acao.field.name || acao.field.slug}</strong> encontrado no
          Trackify — tipo ${acao.field.field_type || '?'}${acao.field.is_identifier ? ', identificador' : ''}.
          ${acao.field.regex_pattern && html`<span> Máscara exigida: <code>${acao.field.regex_pattern}</code>.</span>`}
        </${Alert}>`}

      <button class="rounded bg-wa-teal px-3 py-1.5 text-sm text-white disabled:opacity-50"
        disabled=${busy}
        onClick=${() => {
          // Patch montado a partir dos DESCRITORES: nenhuma chave de setting é
          // literal aqui, e o config.js reenvia o objeto completo por cima.
          const patch = { [acao.codes_setting]: String(draft[acao.codes_setting] || '').trim() };
          for (const f of acao.settings_fields || []) {
            patch[f.key] = String(draft[f.key] ?? '').trim();
          }
          onSaveSettings(patch);
        }}>Salvar</button>

      ${erros.length > 0 && html`
        <div class="rounded border border-wa-border p-2.5 space-y-2">
          <p class="text-xs text-wa-secondary">
            <strong class="text-wa-text">Cliques que não chegaram ao Trackify.</strong>
            A gravação é feita na hora do clique e não é reprocessada sozinha.
            Corrija o que o motivo aponta e use <em>Tentar de novo</em>.
          </p>
          <table class="w-full text-xs">
            <thead class="text-wa-secondary">
              <tr>
                <th class="text-left py-1">Contato</th>
                <th class="text-left py-1">Motivo</th>
                <th class="text-left py-1">Última tentativa</th>
                <th class="text-right py-1"></th>
              </tr>
            </thead>
            <tbody>
              ${erros.map((r) => html`
                <tr class="border-t border-wa-border">
                  <td class="py-1 text-wa-text">
                    ${r.contact_name || `#${r.contact_id}`}
                    ${r.contact_phone && html`
                      <span class="block text-wa-secondary">${r.contact_phone}</span>`}
                  </td>
                  <td class="py-1 text-wa-secondary">${r.last_error}</td>
                  <td class="py-1 text-wa-secondary">${fmtDate(r.last_attempt_at)}</td>
                  <td class="py-1 text-right">
                    <button type="button"
                      class="rounded border border-wa-border px-2 py-0.5 text-wa-text
                             hover:bg-wa-hover disabled:opacity-50"
                      disabled=${busy || tentando === r.contact_id}
                      onClick=${() => onRetry(r.contact_id, acao.id)}>
                      ${tentando === r.contact_id ? 'Tentando…' : 'Tentar de novo'}
                    </button>
                  </td>
                </tr>`)}
            </tbody>
          </table>
        </div>`}
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

  // Recarrega o status depois de reentregar: é o status que traz a lista de
  // erros, e uma linha que sumiu dali é a confirmação de que a gravação passou.
  const reentregar = useCallback(async (contactId, actionId) => {
    await req('POST', '/consent/retry', { contact_id: contactId, action: actionId });
    await load();
  }, [req, load]);

  const acoes = (status && status.actions) || [];

  return html`
    <div class="space-y-4">
      <${Geral} status=${status} settings=${settings}
        onSaveSettings=${salvarSettings} busy=${busy} />
      ${acoes.map((acao) => html`
        <${AcaoCard} key=${acao.id} acao=${acao} settings=${settings}
          onSaveSettings=${salvarSettings} onRetryClick=${reentregar} busy=${busy} />`)}
    </div>`;
}
