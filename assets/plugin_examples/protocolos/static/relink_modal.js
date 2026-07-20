// Popup "Este atendimento faz parte do protocolo anterior?" (plano 49).
// Aparece ao ABRIR uma conversa cujo contato fechou um protocolo há pouco (janela
// configurável). Três saídas: (a) vincular ao anterior, (b) manter como novo,
// (c) fechar conversa + protocolo juntos. Molde visual do resolve_form.js;
// cores wa-*/.wa-field (modo escuro). Preact/htm via importmap.

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// "há X" humano a partir de segundos desde o fechamento. Conservador e curto.
export function humanizeAgo(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return 'há instantes';
  const min = Math.round(s / 60);
  if (min < 60) return `há ${min} min`;
  const hrs = Math.round(min / 60);
  if (hrs < 24) return `há ${hrs} h`;
  const days = Math.round(hrs / 24);
  return `há ${days} d`;
}

// Popup de vínculo. Props:
//   previous            = { id, closed_at, assignee_name, atendimentos_count }
//   secondsSinceClose   = número (segundos desde o closed_at) | null
//   doRelink()          = async () => ({ ok, error? })   — botão (a)
//   doCloseAll()        = async () => ({ ok, error? })   — botão (c)
//   onDone(outcome)     = fecha o modal. outcome ∈ 'relinked' | 'new' | 'closed_all' | 'cancel'
export function RelinkModal({ previous = {}, secondsSinceClose = null,
                              doRelink, doCloseAll, onDone }) {
  const [busy, setBusy] = useState('');   // '' | 'relink' | 'close'
  const [err, setErr] = useState('');

  const run = async (which, fn, outcome) => {
    if (busy) return;
    setErr(''); setBusy(which);
    try {
      const r = await fn();
      if (r && r.ok === false) { setErr(r.error || 'Falha na operação.'); setBusy(''); return; }
      onDone(outcome);
    } catch (_) {
      setErr('Falha na operação.'); setBusy('');
    }
  };

  const ago = secondsSinceClose == null ? '' : humanizeAgo(secondsSinceClose);
  const atendente = (previous.assignee_name || '').trim();
  const count = Number(previous.atendimentos_count || 0);

  return html`
    <div class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget && !busy) onDone('cancel'); }}>
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6 max-h-[85vh] overflow-auto">
        <h2 class="text-base font-semibold text-wa-text mb-1">
          Este atendimento faz parte do protocolo anterior?
        </h2>
        <p class="text-sm text-wa-secondary mb-4">
          Este contato teve um protocolo finalizado ${ago ? html`<b>${ago}</b>` : 'recentemente'}.
          Vincular reabre o protocolo anterior e junta este atendimento a ele.
        </p>

        <div class="mb-4 px-3 py-2.5 rounded-lg border border-wa-border bg-wa-panel text-[13px] text-wa-text">
          <div class="font-medium">Protocolo anterior #${previous.id}</div>
          <div class="text-[12px] text-wa-secondary mt-0.5">
            ${atendente ? html`Atendente: ${atendente}` : 'Sem atendente registrado'}
            ${count ? html` · ${count} atendimento${count === 1 ? '' : 's'}` : null}
          </div>
        </div>

        ${err ? html`
          <div class="mb-4 px-3 py-2.5 rounded-md bg-red-500/15 border border-red-500 text-red-500 text-[14px] font-semibold">
            ${err}
          </div>` : null}

        <div class="space-y-2">
          <button onClick=${() => run('relink', doRelink, 'relinked')} disabled=${!!busy}
            class="w-full py-2.5 px-4 bg-wa-teal text-white rounded-lg disabled:opacity-50 text-[14px] font-medium">
            ${busy === 'relink' ? 'Vinculando…' : 'Faz parte do anterior'}</button>

          <button onClick=${() => { if (!busy) onDone('new'); }} disabled=${!!busy}
            class="w-full py-2.5 px-4 bg-wa-panel hover:bg-wa-hover text-wa-text rounded-lg disabled:opacity-50 text-[14px] font-medium">
            É um novo protocolo</button>

          <button onClick=${() => run('close', doCloseAll, 'closed_all')} disabled=${!!busy}
            class="w-full py-2.5 px-4 rounded-lg border border-wa-border text-wa-secondary hover:bg-wa-hover disabled:opacity-50 text-[13px]">
            ${busy === 'close' ? 'Fechando…' : 'Fechar conversa e protocolo juntos'}</button>
        </div>
      </div>
    </div>`;
}

export default RelinkModal;
