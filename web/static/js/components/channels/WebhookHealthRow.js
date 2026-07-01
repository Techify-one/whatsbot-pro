// Channels — WebhookHealthRow (Plano 26). Linha de saúde do webhook que só
// aparece em canais ``whatsapp_cloud`` (D1). Self-fetch (não toca o caminho de
// status em massa — P1): no mount, pergunta à Meta qual webhook está configurado
// e compara com a URL que ESTA instância espera
// (``${origin}/api/webhook/whatsapp_cloud/${channel.id}`` — mesma fórmula de
// notices.js). O botão "Configurar webhook" repointa o override (com confirmação,
// D5) e re-checa. Cores com fallback dark via custom.css (padrão ChannelCard).
import { h } from 'preact';
import { useEffect, useState, useCallback } from 'preact/hooks';
import htm from 'htm';
import { cloudWebhookStatus, cloudSetWebhook } from '../../services/api.js';

const html = htm.bind(h);

const MATCH_META = {
  ok:           { tint: 'bg-green-50 border-green-200 text-green-700', label: 'Webhook: apontando pra cá ✓' },
  wrong_domain: { tint: 'bg-amber-50 border-amber-200 text-amber-700', label: 'Webhook aponta pra outro domínio' },
  wrong_path:   { tint: 'bg-amber-50 border-amber-200 text-amber-700', label: 'Webhook aponta pra outro canal (path divergente)' },
  unset:        { tint: 'bg-amber-50 border-amber-200 text-amber-700', label: 'Override de webhook não configurado (pode estar usando o webhook do App)' },
  unknown:      { tint: 'bg-wa-hover border-wa-border text-wa-secondary', label: 'Webhook: não verificável' },
};

export function WebhookHealthRow({ channel }) {
  // Hard gate: only WhatsApp Cloud channels (D1). Hooks below are unconditional
  // (provider never changes for a mounted card), so the early return is safe.
  if (channel.provider !== 'whatsapp_cloud') return null;

  const expectedUrl = `${window.location.origin}/api/webhook/whatsapp_cloud/${channel.id}`;
  const [state, setState] = useState({ loading: true });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');

  const check = useCallback(async () => {
    setState(s => ({ ...s, loading: true }));
    const res = await cloudWebhookStatus(channel.id, expectedUrl);
    if (res && res.ok && res.data) setState({ loading: false, ...res.data });
    else setState({ loading: false, match: 'unknown', configured_url: null,
      reason: (res && res.error) || 'falha ao consultar' });
  }, [channel.id, expectedUrl]);

  useEffect(() => { check(); }, [check]);

  async function handleSet() {
    if (!confirm('Apontar o webhook deste número para ESTA instância? Isso substitui o webhook atual na Meta.')) return;
    setBusy(true); setActionError('');
    const res = await cloudSetWebhook(channel.id, expectedUrl);
    setBusy(false);
    if (res && res.ok) check();
    else setActionError((res && res.error) || 'Falha ao configurar o webhook.');
  }

  if (state.loading) {
    return html`<div class="text-[12px] text-wa-secondary mt-2">Verificando webhook…</div>`;
  }

  const meta = MATCH_META[state.match] || MATCH_META.unknown;
  const canSet = !!state.can_set && state.match !== 'ok';
  // Show the "fill WABA ID/Verify Token" hint when there's a problem but the set
  // is blocked for lack of credentials (P2).
  const blockedNeedsCreds = !state.can_set && state.match !== 'ok' && state.match !== 'unknown';

  return html`
    <div class=${`text-[12px] ${meta.tint} border rounded-md px-2 py-1.5 mt-2 break-words`}>
      <div class="flex items-center justify-between gap-2 flex-wrap">
        <span class="font-medium">${meta.label}</span>
        ${canSet ? html`
          <button class="px-2 py-0.5 rounded-md text-[12px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50 shrink-0"
            onClick=${handleSet} disabled=${busy}>${busy ? '…' : 'Configurar webhook'}</button>
        ` : null}
      </div>
      ${state.configured_url && state.match !== 'ok' ? html`
        <div class="mt-1 font-mono break-all opacity-80">${state.configured_url}</div>
      ` : null}
      ${state.match === 'unknown' && state.reason ? html`
        <div class="mt-1 opacity-80">${state.reason}</div>
      ` : null}
      ${blockedNeedsCreds ? html`
        <div class="mt-1 opacity-80">Preencha o WABA ID e o Verify Token em Editar para apontar o webhook automaticamente.</div>
      ` : null}
      ${actionError ? html`<div class="mt-1 text-red-600">${actionError}</div>` : null}
    </div>
  `;
}
