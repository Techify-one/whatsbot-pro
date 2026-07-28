// Popup "Este atendimento faz parte do protocolo anterior?".
//
// Aparece ao RESOLVER um atendimento cujo contato finalizou um protocolo há pouco (janela
// configurável). Não aparece mais ao abrir a conversa — abrir uma conversa fechada não
// pergunta nem reabre nada. Duas saídas:
//   'previous' → funde no protocolo anterior: nenhum atendimento novo é criado (só a data
//                final do último é estendida), o atendimento é resolvido e o protocolo
//                continua FINALIZADO — tudo na hora, sem o formulário de resolução
//   'new'      → abre de fato o protocolo novo + o atendimento dele e NÃO resolve nada:
//                a conversa continua aberta para o caso novo ser tocado
// Fechar por clique-fora/Cancelar aborta o resolver (nada é alterado).
// Molde visual do resolve_form.js; cores wa-*/.wa-field (modo escuro).

import { h } from 'preact';
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

// Props:
//   previous          = { id, closed_at, assignee_name, atendimentos_count }
//   secondsSinceClose = número (segundos desde o closed_at) | null
//   onPick(choice)    = 'previous' | 'new' | 'cancel'
export function RelinkModal({ previous = {}, secondsSinceClose = null, onPick }) {
  const ago = secondsSinceClose == null ? '' : humanizeAgo(secondsSinceClose);
  const atendente = (previous.assignee_name || '').trim();
  const count = Number(previous.atendimentos_count || 0);

  return html`
    <div class="fixed inset-0 bg-black/50 z-[70] flex items-center justify-center p-4"
      onClick=${(e) => { if (e.target === e.currentTarget) onPick('cancel'); }}>
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6 max-h-[85vh] overflow-auto">
        <h2 class="text-base font-semibold text-wa-text mb-1">
          Este atendimento faz parte do protocolo anterior?
        </h2>
        <p class="text-sm text-wa-secondary mb-4">
          Este contato teve um protocolo finalizado ${ago ? html`<b>${ago}</b>` : 'recentemente'}.
          Vincular junta este atendimento ao protocolo anterior — sem criar um atendimento
          novo — e finaliza o protocolo agora, mantendo os campos preenchidos antes.
          Sendo um caso novo, abre um protocolo novo e o atendimento segue em aberto.
        </p>

        <div class="mb-4 px-3 py-2.5 rounded-lg border border-wa-border bg-wa-panel text-[13px] text-wa-text">
          <div class="font-medium">Protocolo anterior #${previous.id}</div>
          <div class="text-[12px] text-wa-secondary mt-0.5">
            ${atendente ? html`Atendente: ${atendente}` : 'Sem atendente registrado'}
            ${count ? html` · ${count} atendimento${count === 1 ? '' : 's'}` : null}
          </div>
        </div>

        <div class="space-y-2">
          <button onClick=${() => onPick('previous')}
            class="w-full py-2.5 px-4 bg-wa-teal text-white rounded-lg text-[14px] font-medium">
            Faz parte do anterior</button>

          <button onClick=${() => onPick('new')}
            class="w-full py-2.5 px-4 bg-wa-panel hover:bg-wa-hover text-wa-text rounded-lg text-[14px] font-medium">
            É um novo protocolo</button>

          <button onClick=${() => onPick('cancel')}
            class="w-full py-2.5 px-4 rounded-lg border border-wa-border text-wa-secondary hover:bg-wa-hover text-[13px]">
            Cancelar</button>
        </div>
      </div>
    </div>`;
}

export default RelinkModal;
