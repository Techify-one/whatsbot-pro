// Extensão de frontend do plugin "melhorias". O app importa este arquivo UMA vez
// no boot e chama register(api). Tudo é aditivo contra o registry compartilhado —
// desativar o plugin remove tudo no próximo boot; o core fica idêntico.
//
// Primitivas usadas:
//   1. filter — filter.message.contextMenu.items → acrescenta "Gerar melhoria" no
//               menu de botão-direito, só em respostas da IA e com permissão `request`.
//   2. slot   — ai.settings.sections → seção "Sugestão de melhoria" na aba de IA
//               (config de modelo/prompt + lista de análises geradas).
// (O painel de aprovação é uma screen config:false — aparece sozinho no menu da
//  engrenagem via a lista de screens do plugin, filtrada por `requires: view`.)

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { MelhoriaAiSection } from '/plugins/melhorias/static/ai_section.js';

const html = htm.bind(h);

function currentUser() {
  try { return JSON.parse(localStorage.getItem('whatsbot_user') || 'null'); }
  catch (_) { return null; }
}

// Ícone lâmpada (mesmo do antigo ImproveIcon do core) — self-contained.
const ImproveIcon = html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7z"/>
  </svg>`;

export default function register(api) {
  // default-allow em instalação legada/sem RBAC (mesmo padrão do plugin protocolos).
  const can = (key) => (api.services && api.services.hasPermission
    ? api.services.hasPermission(currentUser(), `plugin.melhorias.${key}`) : true);

  // ── 1) Item "Gerar melhoria" no menu de contexto da mensagem ──────────────
  // O seam do core aplica este filtro AO ABRIR o menu, passando o array base de
  // itens + ctx {message, isFromMe, phone, conversationId, sandbox}. Retorna o
  // array (nunca null — null abortaria o menu).
  api.addFilter('filter.message.contextMenu.items', async (ctx, items) => {
    const m = (ctx && ctx.message) || {};
    const isAiReply = m.role === 'assistant' && m.status !== 'operator'
      && !m.revoked && !ctx.sandbox;
    if (!isAiReply || !can('request')) return items;
    return [...items, {
      label: 'Gerar melhoria', icon: ImproveIcon,
      onClick: () => api.ui.openModal((close) => html`<${ImproveDialog}
        api=${api} ctx=${ctx} onClose=${close} />`),
    }];
  });

  // ── 2) Ação em LOTE na multi-seleção de mensagens (plano 51 · 04 F2) ──────
  // O seam novo do core (`filter.selection.batchActions`) roda quando a seleção
  // muda; devolvemos SEMPRE um array (null abortaria a barra). O item só existe
  // quando TODAS as mensagens selecionadas são respostas da IA elegíveis.
  api.addFilter('filter.selection.batchActions', async (ctx, items) => {
    const messages = (ctx && ctx.messages) || [];
    if (ctx.sandbox || !can('request') || messages.length === 0) return items;
    const eligible = messages.every((m) => m
      && m.role === 'assistant' && m.status !== 'operator' && !m.revoked);
    if (!eligible) return items;
    return [...items, {
      label: `Gerar melhoria (${messages.length})`, icon: () => ImproveIcon,
      onClick: () => api.ui.openModal((close) => html`<${ImproveDialog}
        api=${api} ctx=${ctx} messages=${messages}
        onClose=${(ok) => { close(ok); if (ok && ctx.clearSelection) ctx.clearSelection(); }} />`),
    }];
  });

  // ── 3) Seção na aba "Configurações de IA" ─────────────────────────────────
  // Recebe `api` (registrada aqui) → tem api.http/api.ui, diferente do painel
  // (screen montada por PluginScreen, que não passa `api`).
  api.addSlot('ai.settings.sections', () => (can('view')
    ? html`<${MelhoriaAiSection} api=${api} can=${can} />`
    : null));
}

// Diálogo de solicitação: mostra a(s) resposta(s) marcada(s) + textarea opcional
// ("o que saiu errado?") e faz POST /suggestions. Multi-seleção (plano 51) manda
// `messages:[...]`; o caminho single continua `message:{...}` (compat). O aviso
// de sistema com o link chega pela conversa via WS new_message — aqui só fechamos.
function ImproveDialog({ api, ctx, messages = null, onClose }) {
  const list = (messages && messages.length ? messages : [((ctx && ctx.message) || {})])
    .filter((m) => m && (m.content || '').trim());
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function submit() {
    if (loading) return;
    setLoading(true); setError('');
    try {
      const payload = {
        feedback: (text || '').trim(),
        conversation_id: ctx.conversationId,
        phone: ctx.phone,
      };
      if (list.length > 1) {
        payload.messages = list.map((m) => ({
          content: m.content, ts: m.ts, _id: m._id,
          media_type: m.media_type || null, media_path: m.media_path || null,
        }));
      } else {
        const m = list[0] || {};
        payload.message = { content: m.content, ts: m.ts, _id: m._id };
      }
      const res = await api.http.post('/suggestions', payload);
      if (res && res.ok) { onClose(true); }
      else { setError((res && res.error) || 'Falha ao enviar a solicitação.'); }
    } catch (_) { setError('Erro de conexão.'); }
    setLoading(false);
  }

  return html`
    <div class="fixed inset-0 z-[130] bg-black/40 flex items-center justify-center p-4"
      onClick=${() => { if (!loading) onClose(null); }}>
      <div class="bg-wa-panel rounded-lg shadow-xl w-[460px] max-w-[92vw] p-[22px]"
        onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center gap-2 text-[15px] font-semibold text-wa-text mb-[14px]">
          <span class="text-wa-teal">${ImproveIcon}</span>
          Gerar melhoria${list.length > 1 ? ` (${list.length} mensagens)` : ''}
        </div>
        <div class="text-[12px] text-wa-secondary mb-[6px]">
          ${list.length > 1 ? 'Respostas marcadas (na ordem da seleção):' : 'Resposta marcada:'}
        </div>
        <div class="mb-[14px] max-h-[180px] overflow-auto flex flex-col gap-[6px]">
          ${list.map((m, i) => html`
            <div key=${m._id || i}
              class="text-[13px] text-wa-text bg-wa-bg border border-wa-border rounded p-[10px] whitespace-pre-wrap">
              ${(m.content || '').trim() || '(sem conteúdo)'}
            </div>`)}
        </div>
        <label class="block text-[12px] text-wa-secondary mb-[4px]">O que saiu errado? (opcional)</label>
        <textarea class="wa-field w-full rounded p-[8px] text-[13px] mb-[6px]" rows="3"
          placeholder="Descreva o problema para ajudar a análise…"
          value=${text} onInput=${(e) => setText(e.target.value)}></textarea>
        ${error ? html`<div class="text-[12px] text-red-500 mb-[8px]">${error}</div>` : ''}
        <div class="flex justify-end gap-[10px] mt-[8px]">
          <button onClick=${() => { if (!loading) onClose(null); }}
            class="px-[16px] py-[8px] rounded-full border border-wa-border text-wa-text text-[14px] hover:bg-wa-hover transition-colors">Cancelar</button>
          <button onClick=${submit} disabled=${loading}
            class="px-[16px] py-[8px] rounded-full bg-wa-teal text-white text-[14px] font-medium hover:opacity-90 transition-opacity disabled:opacity-50">
            ${loading ? 'Enviando…' : 'Enviar ao painel'}</button>
        </div>
      </div>
    </div>`;
}
