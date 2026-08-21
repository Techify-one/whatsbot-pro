// Tela de CHAVES DE API (plano "Sistema de API com chave por usuário", fase 7).
//
// Emitir, listar e revogar. O segredo aparece UMA ÚNICA VEZ — na resposta da
// criação — e não há endpoint que o leia de volta (o banco só guarda o hash),
// então a tela insiste no "copie agora" em vez de fingir que dá para recuperar.
//
// Os dois guardrails de emissão do backend (§4) chegam como 409 com um `reason`:
// `admin_owner_requires_confirm` e `never_expires_requires_confirm`. A tela os
// transforma numa confirmação explícita e reenvia com `confirm: true` — o
// operador precisa VER o que está aceitando; o backend nunca decide por ele.
//
// Cores: só classes semânticas `wa-*` + `.wa-field`, legíveis nos dois temas.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { getApiKeys, createApiKey, revokeApiKey, getApiKeyOwners } from '../services/api.js';

const html = htm.bind(h);

const TrashIcon = html`
  <svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
    <path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5 0a1 1 0 10-2 0v6a1 1 0 102 0V8z" clip-rule="evenodd" />
  </svg>`;

/** Dica (ⓘ) que abre ao passar o mouse ou pelo teclado.

Preferida a um parágrafo fixo abaixo do campo: a explicação é lida UMA vez e
depois só empurra o formulário para baixo. `title` fica como reserva — cobre
leitor de tela e o caso de a bolha ser cortada por algum contêiner. */
function InfoHint({ text }) {
  return html`
    <span class="group relative inline-flex align-middle ml-1" tabindex="0"
          role="note" aria-label=${text} title=${text}>
      <svg viewBox="0 0 20 20" fill="currentColor"
           class="w-[13px] h-[13px] text-wa-secondary hover:text-wa-teal cursor-help">
        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9 9a1 1 0 012 0v4a1 1 0 11-2 0V9zm1-4a1 1 0 100 2 1 1 0 000-2z" clip-rule="evenodd" />
      </svg>
      <span class="pointer-events-none absolute left-0 top-[18px] z-20 w-[260px] rounded
                   border border-wa-border bg-wa-panel px-2.5 py-1.5 text-[12px] leading-snug
                   text-wa-text shadow-lg opacity-0 invisible
                   group-hover:opacity-100 group-hover:visible
                   group-focus:opacity-100 group-focus:visible transition-opacity">
        ${text}
      </span>
    </span>`;
}

const TTL_OPTIONS = [
  ['30', '30 dias'],
  ['90', '90 dias'],
  ['365', '1 ano (padrão)'],
  ['never', 'Sem validade'],
];

function fmtDate(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts * 1000).toLocaleString('pt-BR');
  } catch (_) {
    return '—';
  }
}

/** Segredo revelado uma única vez, com botão de copiar. */
function SecretBanner({ secret, onDismiss }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (_) { /* clipboard bloqueado — o valor continua selecionável */ }
  };
  return html`
    <div class="mb-4 rounded-lg border border-wa-teal bg-wa-teal/10 p-4">
      <div class="text-[14px] font-semibold text-wa-text mb-1">Copie a chave agora</div>
      <div class="text-[13px] text-wa-secondary mb-3">
        Este é o único momento em que o segredo aparece. O servidor guarda só um
        hash — se você perder a chave, terá de emitir outra.
      </div>
      <div class="flex items-center gap-2">
        <input class="wa-field flex-1 rounded px-2 py-1.5 font-mono text-[13px]"
               readonly value=${secret}
               onClick=${(e) => e.target.select()} />
        <button class="rounded bg-wa-teal px-3 py-1.5 text-[13px] text-white"
                onClick=${copy}>${copied ? 'Copiado!' : 'Copiar'}</button>
        <button class="rounded border border-wa-border px-3 py-1.5 text-[13px] text-wa-text"
                onClick=${onDismiss}>Fechar</button>
      </div>
    </div>`;
}

export default function ApiKeysManager({ currentUser }) {
  const [keys, setKeys] = useState([]);
  // Donos vêm da rota de chaves, já recortados pelo servidor: quem não tem
  // users.manage recebe só a si mesmo. A tela não decide o recorte — ela o desenha.
  const [users, setUsers] = useState([]);
  const [canChooseOthers, setCanChooseOthers] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [secret, setSecret] = useState('');

  const [label, setLabel] = useState('');
  const [userId, setUserId] = useState('');
  const [ttl, setTtl] = useState('365');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    const [k, u] = await Promise.all([getApiKeys(), getApiKeyOwners()]);
    if (k && k.ok) setKeys(k.data || []);
    if (u && u.ok) {
      const owners = (u.data && u.data.owners) || [];
      setUsers(owners);
      setCanChooseOthers(!!(u.data && u.data.can_choose_others));
      // Um único dono possível ⇒ já vem escolhido: não há decisão a tomar, e um
      // "Selecione…" obrigatório seria só um passo a mais para chegar ao mesmo lugar.
      if (owners.length === 1) setUserId(String(owners[0].id));
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const submit = async (confirm = false) => {
    setError('');
    if (!label.trim()) { setError('Dê um rótulo à chave (ex.: "CRM — produção").'); return; }
    if (!userId) { setError('Escolha o usuário dono da chave.'); return; }
    setBusy(true);
    const body = {
      label: label.trim(),
      user_id: Number(userId),
      expires_in_days: ttl === 'never' ? 'never' : Number(ttl),
    };
    if (confirm) body.confirm = true;
    const res = await createApiKey(body);
    setBusy(false);
    if (res && res.ok) {
      setSecret(res.data.key || '');
      setLabel('');
      load();
      return;
    }
    const reason = (res && res.data && res.data.reason) || '';
    const msg = (res && res.error) || 'Falha ao emitir a chave.';
    // Guardrails §4.1/§4.4: o backend recusa e pede confirmação EXPLÍCITA. A tela
    // mostra o motivo inteiro antes de reenviar — o risco tem de ser visível.
    if (reason.endsWith('requires_confirm')) {
      if (window.confirm(`${msg}\n\nContinuar mesmo assim?`)) await submit(true);
      return;
    }
    setError(msg);
  };

  const revoke = async (row) => {
    if (!window.confirm(
      `Revogar a chave "${row.label}"?\n\nQualquer integração que a use passa a ` +
      `receber 401 imediatamente. A ação não pode ser desfeita.`)) return;
    await revokeApiKey(row.id);
    load();
  };

  // Sem filtro de `is_active` aqui: /api/api-keys/owners já devolve só ativos e
  // NÃO manda esse campo — filtrar por ele no cliente esvaziaria o seletor.

  return html`
    <div class="space-y-4">
      <div class="rounded-lg border border-wa-border bg-wa-panel p-4">
        <div class="text-[14px] font-semibold text-wa-text mb-1">Emitir uma chave</div>
        <div class="text-[13px] text-wa-secondary mb-3">
          A chave age como o <b>usuário dono</b>: ela herda exatamente as permissões
          e as caixas de entrada dele. Para limitar uma integração, crie um usuário
          dedicado, marque "permissões personalizadas", conceda só o necessário e
          coloque-o apenas nas caixas que ela deve enxergar.
        </div>
        ${secret && html`<${SecretBanner} secret=${secret} onDismiss=${() => setSecret('')} />`}
        ${error && html`
          <div class="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-[13px] text-red-700">
            ${error}
          </div>`}
        <div class="flex flex-wrap items-end gap-3">
          <label class="flex-1 min-w-[200px]">
            <span class="block text-[12px] text-wa-secondary mb-1">Rótulo</span>
            <input class="wa-field w-full rounded px-2 py-1.5 text-[14px]"
                   placeholder="CRM — produção" value=${label}
                   onInput=${e => setLabel(e.target.value)} />
          </label>
          <label class="flex-1 min-w-[200px]">
            <span class="block text-[12px] text-wa-secondary mb-1">
              Usuário dono
              ${!canChooseOthers && html`<${InfoHint}
                text="Você só pode emitir chave para você mesmo — emitir no nome de outra pessoa exige a permissão de gerenciar usuários." />`}
            </span>
            <select class="wa-field w-full rounded px-2 py-1.5 text-[14px] disabled:opacity-70"
                    disabled=${!canChooseOthers}
                    value=${userId} onChange=${e => setUserId(e.target.value)}>
              ${canChooseOthers && html`<option value="">Selecione…</option>`}
              ${users.map(u => html`
                <option value=${u.id}>
                  ${u.name || u.email}${u.is_admin ? ' (administrador)' : ''}
                </option>`)}
            </select>
          </label>
          <label class="min-w-[150px]">
            <span class="block text-[12px] text-wa-secondary mb-1">Validade</span>
            <select class="wa-field w-full rounded px-2 py-1.5 text-[14px]"
                    value=${ttl} onChange=${e => setTtl(e.target.value)}>
              ${TTL_OPTIONS.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
            </select>
          </label>
          <button class="rounded bg-wa-teal px-4 py-1.5 text-[14px] text-white disabled:opacity-50"
                  disabled=${busy} onClick=${() => submit(false)}>
            ${busy ? 'Emitindo…' : 'Emitir chave'}
          </button>
        </div>
      </div>

      <div class="rounded-lg border border-wa-border bg-wa-panel overflow-hidden">
        <table class="w-full text-[13px]">
          <thead class="bg-wa-bg text-wa-secondary">
            <tr>
              <th class="px-3 py-2 text-left font-medium">Rótulo</th>
              <th class="px-3 py-2 text-left font-medium">Chave</th>
              <th class="px-3 py-2 text-left font-medium">Dono</th>
              <th class="px-3 py-2 text-left font-medium">Último uso</th>
              <th class="px-3 py-2 text-left font-medium">Expira</th>
              <th class="px-3 py-2 text-left font-medium">Estado</th>
              <th class="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            ${loading && html`<tr><td colspan="7" class="px-3 py-4 text-wa-secondary">Carregando…</td></tr>`}
            ${!loading && keys.length === 0 && html`
              <tr><td colspan="7" class="px-3 py-4 text-wa-secondary">
                Nenhuma chave emitida.
              </td></tr>`}
            ${keys.map(k => html`
              <tr class="border-t border-wa-border">
                <td class="px-3 py-2 text-wa-text">${k.label}</td>
                <td class="px-3 py-2 font-mono text-wa-secondary">${k.masked}</td>
                <td class="px-3 py-2 text-wa-text">${k.user_name || k.user_email || `#${k.user_id}`}</td>
                <td class="px-3 py-2 text-wa-secondary">${fmtDate(k.last_used_at)}</td>
                <td class="px-3 py-2 text-wa-secondary">${k.expires_at ? fmtDate(k.expires_at) : 'Sem validade'}</td>
                <td class="px-3 py-2">
                  ${k.revoked_at
                    ? html`<span class="text-wa-secondary">Revogada</span>`
                    : (k.active
                        ? html`<span class="text-wa-teal">Ativa</span>`
                        : html`<span class="text-wa-secondary">Expirada</span>`)}
                </td>
                <td class="px-3 py-2 text-right">
                  ${!k.revoked_at && html`
                    <button class="text-wa-secondary hover:text-red-600" title="Revogar"
                            onClick=${() => revoke(k)}>${TrashIcon}</button>`}
                </td>
              </tr>`)}
          </tbody>
        </table>
      </div>

      <div class="text-[12px] text-wa-secondary">
        Autentique enviando o cabeçalho <code class="font-mono">X-Api-Key: wsk_live_…</code>.
        A chave vale em toda a API (<code class="font-mono">/api/v1</code> e as rotas de
        plugin), sempre com as permissões do usuário dono. O esquema OpenAPI das rotas
        estáveis está em <code class="font-mono">GET /api/v1/openapi.json</code>.
      </div>
    </div>`;
}
