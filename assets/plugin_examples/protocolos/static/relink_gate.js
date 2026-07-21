// Overlay bloqueante de continuidade de protocolo (dentro do chat).
//
// Renderizado pelo slot `chat.header.banner` (que fica no painel `relative` do chat), com
// `absolute inset-0` → cobre SÓ a área do chat desta conversa (a lista de conversas segue
// clicável, então dá pra trocar de conversa). Aparece quando o contato fechou um protocolo
// há pouco (dentro da janela) e o auto_link ADIOU a abertura do novo → o atendente PRECISA
// decidir antes de usar o chat. Não é fechável por clique-fora/esc: só pelas 3 opções (ou
// trocando de conversa, que desmonta o componente e adia a decisão para o próximo acesso).
//
// Opções:
//   (a) Faz parte do anterior → POST /protocolos/{prev}/relink { conversation_id }
//   (b) É um novo protocolo    → POST /conversas/{conv}/open-new-protocolo   (abre agora)
//   (c) Fechar conversa e protocolo → setConversationStatus(closed) + /relink-reviewed

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { getConversation } from '/static/js/services/api.js';
import { humanizeAgo } from '/plugins/protocolos/static/relink_modal.js';

const html = htm.bind(h);

export function RelinkGate({ conversationId, api }) {
  // status: loading | none | pending | busy | done
  const [st, setSt] = useState({ status: 'loading', data: null, err: '' });

  useEffect(() => {
    let alive = true;
    if (!conversationId) { setSt({ status: 'none', data: null, err: '' }); return; }
    setSt({ status: 'loading', data: null, err: '' });
    (async () => {
      try {
        const c = await getConversation(conversationId);
        const conv = (c && c.ok && c.data) ? c.data.conversation : null;
        const contactId = conv ? conv.contact_id : null;
        if (contactId == null) { if (alive) setSt({ status: 'none', data: null, err: '' }); return; }
        const s = await api.http.get(`/contacts/${contactId}/relink-suggestion`);
        const data = (s && s.ok && s.data) || null;
        if (!alive) return;
        if (data && data.suggest && data.previous) setSt({ status: 'pending', data, err: '' });
        else setSt({ status: 'none', data: null, err: '' });
      } catch (_) { if (alive) setSt({ status: 'none', data: null, err: '' }); }
    })();
    return () => { alive = false; };
  }, [conversationId]);

  if (st.status !== 'pending' && st.status !== 'busy') return null;

  const data = st.data;
  const prevId = data.previous.id;
  const busy = st.status === 'busy';

  const run = async (fn) => {
    if (busy) return;
    setSt((s) => ({ ...s, status: 'busy', err: '' }));
    try {
      const r = await fn();
      if (r && r.ok === false) { setSt((s) => ({ ...s, status: 'pending', err: r.error || 'Falha na operação.' })); return; }
      setSt({ status: 'done', data: null, err: '' });   // libera o chat
    } catch (_) {
      setSt((s) => ({ ...s, status: 'pending', err: 'Falha na operação.' }));
    }
  };

  const doRelink = () => run(() => api.http.post(`/protocolos/${prevId}/relink`,
    { conversation_id: conversationId }));
  const doNew = () => run(() => api.http.post(`/conversas/${conversationId}/open-new-protocolo`));
  const doCloseAll = () => run(async () => {
    const stt = await api.services.setConversationStatus(conversationId, 'closed');
    if (stt && stt.ok === false) return stt;
    return api.http.post(`/protocolos/${prevId}/relink-reviewed`);
  });

  const ago = data.seconds_since_close == null ? '' : humanizeAgo(data.seconds_since_close);
  const atendente = (data.previous.assignee_name || '').trim();
  const count = Number(data.previous.atendimentos_count || 0);

  // absolute inset-0 → ancorado no painel `relative` do chat (cobre header+mensagens+input,
  // não a lista). z alto para ficar acima de tudo do chat.
  return html`
    <div class="absolute inset-0 z-[55] bg-black/50 backdrop-blur-[1px] flex items-center justify-center p-4">
      <div class="bg-wa-bg rounded-2xl shadow-2xl max-w-sm w-full p-6 max-h-[85vh] overflow-auto border border-wa-border">
        <h2 class="text-base font-semibold text-wa-text mb-1">
          Este atendimento faz parte do protocolo anterior?
        </h2>
        <p class="text-sm text-wa-secondary mb-4">
          Este contato teve um protocolo finalizado ${ago ? html`<b>${ago}</b>` : 'recentemente'}.
          Escolha uma opção para continuar — o chat fica bloqueado até você decidir.
        </p>

        <div class="mb-4 px-3 py-2.5 rounded-lg border border-wa-border bg-wa-panel text-[13px] text-wa-text">
          <div class="font-medium">Protocolo anterior #${prevId}</div>
          <div class="text-[12px] text-wa-secondary mt-0.5">
            ${atendente ? html`Atendente: ${atendente}` : 'Sem atendente registrado'}
            ${count ? html` · ${count} atendimento${count === 1 ? '' : 's'}` : null}
          </div>
        </div>

        ${st.err ? html`
          <div class="mb-4 px-3 py-2.5 rounded-md bg-red-500/15 border border-red-500 text-red-500 text-[14px] font-semibold">
            ${st.err}
          </div>` : null}

        <div class="space-y-2">
          <button onClick=${doRelink} disabled=${busy}
            class="w-full py-2.5 px-4 bg-wa-teal text-white rounded-lg disabled:opacity-50 text-[14px] font-medium">
            ${busy ? 'Processando…' : 'Faz parte do anterior'}</button>

          <button onClick=${doNew} disabled=${busy}
            class="w-full py-2.5 px-4 bg-wa-panel hover:bg-wa-hover text-wa-text rounded-lg disabled:opacity-50 text-[14px] font-medium">
            É um novo protocolo</button>

          <button onClick=${doCloseAll} disabled=${busy}
            class="w-full py-2.5 px-4 rounded-lg border border-wa-border text-wa-secondary hover:bg-wa-hover disabled:opacity-50 text-[13px]">
            Fechar conversa e protocolo juntos</button>
        </div>
      </div>
    </div>`;
}

export default RelinkGate;
