// Chat agêntico do plugin "melhorias" (plano 51 · 04 F4) — consumo dos eventos
// do executor re-emitidos no /ws do operador (decisão P2 do mestre: o gateway
// consome a SSE do executor server-side e faz broadcast de
// `plugin_melhorias_ai_event` {suggestion_id, conversation_id, event, data};
// aqui filtramos por conversation_id). A máquina de estados/cards é a mesma do
// caminho SSE-dedicado — só muda a fonte de bytes.
//
// `reduceAiEvent` é PURO (testável com node --test): recebe a lista de cards e
// um evento {event, data} e devolve a lista nova + o estado do chat.

import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { subscribe as subscribeWs } from '/static/js/services/wsBus.js';
import { reduceAiEvent, isAuthError, persistedToItems, authErrorInHistory,
         lastFailureFromHistory, footerStateFor, failureAction } from './chat_core.js';
import { renderMarkdown } from './markdown.js';

const html = htm.bind(h);

export { reduceAiEvent, isAuthError, persistedToItems, authErrorInHistory,
         lastFailureFromHistory, footerStateFor };

const WAIT_LABELS = {
  rate_limited: 'Limite de uso atingido',
  overloaded: 'Executor sobrecarregado',
};

// ── Hook: eventos da conversa via /ws ────────────────────────────────────────

export function useAiChatEvents(conversationId, onEvent) {
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;
  useEffect(() => {
    if (!conversationId) return undefined;
    // wsBus do core: conexão ÚNICA compartilhada com o painel, já autenticada
    // (?token= do localStorage — com RBAC ativo um WebSocket cru sem token é
    // fechado com 4401 e nenhum evento chega), com reconexão + heartbeat.
    return subscribeWs({
      plugin_melhorias_ai_event: (d) => {
        if (!d || d.conversation_id !== conversationId) return;
        cbRef.current && cbRef.current({ event: d.event, data: d.data || {} });
      },
    });
  }, [conversationId]);
}

// ── Cards ────────────────────────────────────────────────────────────────────

function AssistantCard({ item }) {
  const text = item.content || (item.streaming ? '…' : '');
  return html`
    <div class="flex justify-start">
      <div class="bg-wa-incoming text-wa-text rounded-[7.5px] px-3 py-2 max-w-[92%] text-[13px]">
        <div class="markdown-body" dangerouslySetInnerHTML=${{ __html: renderMarkdown(text) }}></div>
        ${item.streaming ? html`<span class="inline-block w-1.5 h-1.5 rounded-full bg-wa-teal animate-pulse ml-1"></span>` : ''}
      </div>
    </div>`;
}

function UserCard({ item }) {
  return html`
    <div class="flex justify-end">
      <div class="bg-wa-outgoing text-wa-text rounded-[7.5px] px-3 py-2 max-w-[85%] text-[13px] whitespace-pre-wrap">
        ${item.image ? html`<img src=${item.image} class="max-w-full rounded mb-1" />` : ''}
        ${item.content}
      </div>
    </div>`;
}

function ToolCard({ item }) {
  const [open, setOpen] = useState(false);
  const badge = item.status === 'running' ? '⏳' : (item.status === 'error' ? '✖' : '✔');
  return html`
    <div class="flex justify-start">
      <div class="bg-wa-bg border border-wa-border rounded-md px-3 py-2 max-w-[85%] text-[12px] text-wa-secondary cursor-pointer"
        onClick=${() => setOpen((o) => !o)}>
        <span class="font-medium text-wa-text">${badge} Ferramenta: ${item.name}</span>
        ${open ? html`
          <pre class="mt-1 text-[11px] whitespace-pre-wrap max-h-[160px] overflow-auto">${JSON.stringify(item.input || {}, null, 2)}</pre>
          ${item.output != null ? html`<pre class="mt-1 text-[11px] whitespace-pre-wrap max-h-[160px] overflow-auto border-t border-wa-border pt-1">${typeof item.output === 'string' ? item.output : JSON.stringify(item.output, null, 2)}</pre>` : ''}
          ${item.error ? html`<div class="text-red-500 mt-1">${item.error}</div>` : ''}
        ` : ''}
      </div>
    </div>`;
}

function ApprovalCard({ item, onDecide, busy }) {
  const [reason, setReason] = useState('');
  const [showReason, setShowReason] = useState(false);
  const decided = item.decided;
  return html`
    <div class="flex justify-start w-full">
      <div class="border rounded-md px-3 py-2 w-full max-w-[92%] text-[12.5px]
        ${decided == null ? 'border-amber-400 bg-amber-50' : 'border-wa-border bg-wa-bg'}">
        <div class="font-medium text-wa-text mb-1">
          🔐 Aprovação necessária: <span class="font-mono">${item.toolName}</span>
        </div>
        ${item.summary ? html`<div class="text-wa-text mb-1">${item.summary}</div>` : ''}
        <details class="text-wa-secondary mb-2">
          <summary class="cursor-pointer text-[11px]">Ver parâmetros</summary>
          <pre class="text-[11px] whitespace-pre-wrap max-h-[180px] overflow-auto">${JSON.stringify(item.toolInput || {}, null, 2)}</pre>
        </details>
        ${decided == null ? html`
          ${showReason ? html`
            <input class="wa-field w-full rounded px-2 py-1 text-[12px] mb-2"
              placeholder="Motivo da recusa (opcional)"
              value=${reason} onInput=${(e) => setReason(e.target.value)} />` : ''}
          <div class="flex gap-2">
            <button disabled=${busy} onClick=${() => onDecide(item.id, true, '')}
              class="px-3 py-1 rounded-full bg-wa-teal text-white text-[12px] font-medium hover:opacity-90 disabled:opacity-50">✓ Aprovar</button>
            ${showReason
              ? html`<button disabled=${busy} onClick=${() => onDecide(item.id, false, reason)}
                  class="px-3 py-1 rounded-full border border-red-400 text-red-500 text-[12px] hover:bg-red-500/10 disabled:opacity-50">Confirmar recusa</button>`
              : html`<button disabled=${busy} onClick=${() => setShowReason(true)}
                  class="px-3 py-1 rounded-full border border-red-400 text-red-500 text-[12px] hover:bg-red-500/10 disabled:opacity-50">✕ Recusar</button>`}
          </div>` : html`
          <div class="text-[12px] ${item.decided ? 'text-wa-teal' : 'text-red-500'} font-medium">
            ${item.decided ? '✓ Aprovada' : '✕ Recusada'}
          </div>`}
      </div>
    </div>`;
}

function ErrorCard({ item }) {
  return html`
    <div class="flex justify-center">
      <div class="border border-red-400 bg-red-50 text-red-600 rounded-md px-3 py-2 text-[12px] max-w-[90%]">
        ${item.message}
      </div>
    </div>`;
}

// ── Componente principal ─────────────────────────────────────────────────────

export function AgenticChat({ apiJson, apiBase, suggestion, conversation,
                              onAuthError = null, onConversationEnd = null,
                              onRenewSession = null, reloadKey = 0, notice = '',
                              onNoticeClear = null }) {
  const cid = conversation && conversation.id;
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('idle');
  const [input, setInput] = useState('');
  const [pendingImage, setPendingImage] = useState(null); // {dataUrl, mediaType, b64}
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Estado REAL da conversa: a prop pode estar defasada (ex.: runner morreu e o
  // status virou ERRORED no banco) — a hidratação corrige, e o rodapé decide
  // entre input e "Continuar conversa" por AQUI.
  const [conv, setConv] = useState(conversation || null);
  // Última falha TIPADA vista no histórico (durável: sessão expirada, cota) e a
  // que chegou ao vivo pelo /ws (efêmera: limite, sobrecarga). Separadas porque
  // têm tempos de vida diferentes — a segunda vence sozinha.
  const [historyFailure, setHistoryFailure] = useState(null);
  const [liveFailure, setLiveFailure] = useState(null); // {kind, message, until}
  const [, forceTick] = useState(0);
  const scrollRef = useRef(null);
  // Oferta de renovar disparada pela HIDRATAÇÃO: uma vez por conversa. Sem isto,
  // re-hidratar depois de retomar reabriria o modal em loop — o 401 continua
  // sendo a última linha do histórico (ele não é apagado). Um 401 NOVO chega
  // pelo evento `auth_expired` ao vivo, que não passa por aqui.
  const authNotifiedRef = useRef('');

  useEffect(() => {
    setConv(conversation || null);
    setHistoryFailure(null);
    setLiveFailure(null);
  }, [cid]);

  // Contagem regressiva do aviso transitório: só força o re-render: o tempo que
  // falta é DERIVADO de `until`, então não há estado para dessincronizar.
  useEffect(() => {
    if (!liveFailure || !liveFailure.until) return undefined;
    const t = setInterval(() => forceTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [liveFailure]);

  // Hidrata do DB ao abrir — e a cada `reloadKey` (plano 60 · 1.2): sem um
  // gatilho próprio, nenhuma recuperação (renovar sessão → retomar) seria
  // possível sem desmontar o chat.
  useEffect(() => {
    if (!cid) return;
    (async () => {
      try {
        const r = await apiJson(`${apiBase}/conversations/${cid}`);
        if (r.ok) {
          const messages = r.body.data.messages || [];
          setItems(persistedToItems(messages, r.body.data.approvals));
          const fresh = r.body.data.conversation;
          if (fresh) setConv(fresh);
          // Detecta na HIDRATAÇÃO, não só ao vivo: quem abre a conversa depois
          // da falha também recebe a oferta de renovar (plano 60 · 1.3).
          const failure = lastFailureFromHistory(messages);
          setHistoryFailure(failure);
          // Só SESSÃO EXPIRADA abre o modal — e só ela queima o guard. Um limite
          // de uso que por acaso cite um 401 abriria o relogin à toa E gastaria
          // o guard desta conversa para sempre, deixando uma expiração real
          // futura sem aviso nenhum (o guard não é reposto).
          const dead = (fresh && fresh.status === 'AUTH_EXPIRED')
            || (failure && failure.kind === 'auth_required');
          if (dead && onAuthError && authNotifiedRef.current !== cid) {
            authNotifiedRef.current = cid;
            onAuthError();
          }
        }
      } catch (_) { /* ignore */ }
    })();
  }, [cid, reloadKey]);

  // Eventos ao vivo via /ws.
  useAiChatEvents(cid, (ev) => {
    // O gateway classificou uma sessão expirada (write-through ou frame de erro)
    // — a conversa já está AUTH_EXPIRED no banco; o chat reflete na hora.
    if (ev.event === 'auth_expired') {
      setConv((c) => ({ ...(c || {}), status: 'AUTH_EXPIRED' }));
      setStatus('error');
      setHistoryFailure({ kind: 'auth_required', message: (ev.data || {}).message || '' });
      if (onAuthError) onAuthError();
      return;
    }
    // Falha TIPADA não-fatal (plano 61): a conversa segue viva e o compositor
    // liberado — só entra um aviso, com contagem quando o executor mandou uma.
    if (ev.event === 'executor_failure') {
      const d = ev.data || {};
      const kind = d.kind || 'unknown';
      const secs = Number(d.retry_after);
      setLiveFailure({ kind, message: d.message || '',
                       until: Number.isFinite(secs) && secs > 0 ? Date.now() + secs * 1000 : null });
      if (failureAction(kind) === 'billing') {
        setHistoryFailure({ kind, message: d.message || '' });  // cota não passa sozinha
      }
    }
    setItems((prev) => {
      const out = reduceAiEvent(prev, ev, status);
      setStatus(out.status);
      // A IA voltou a falar ⇒ o aviso transitório cumpriu seu papel.
      if (ev.event === 'message_start') setLiveFailure(null);
      // NENHUM palpite por texto no caminho ao vivo (plano 61). Havia dois, e os
      // dois eram dispensáveis:
      //
      // - `message_end`: era REDUNDANTE. Um 401 entregue como resposta da IA
      //   (executor antigo) é classificado no SERVIDOR pelo write-through, que
      //   emite `auth_expired` — o modal abre pelo canal certo. Enquanto isso o
      //   palpite abria o relogin para QUALQUER mensagem que citasse "401":
      //   bastava a IA analisar um erro de API, ou o `/ajuda` do executor falso
      //   listar o comando `/limite401`.
      // - `error` cru: era CÓDIGO MORTO. O `error` só chega aqui quando o
      //   `derive_kind` do gateway devolveu `None`, o que por definição
      //   significa que a heurística de auth deu falso.
      //
      // O texto ainda decide em UM lugar só: `lastFailureFromHistory`, para
      // linhas LEGADAS do banco (gravadas antes do kind existir).
      if (ev.event === 'done' && onConversationEnd) onConversationEnd();
      return out.items;
    });
  });

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items]);

  // Quiescence fallback: the `done` event that clears the "IA pensando…"
  // indicator can be lost. When we are `streaming` but nothing is actually in
  // flight — no text streaming, no tool running, no undecided approval — arm a
  // timer to drop back to idle. Every ws event mutates items/status and re-runs
  // this effect, so the cleanup re-arms the timer; a live stream/tool/approval
  // keeps a marker present and prevents the timer from firing.
  useEffect(() => {
    if (status !== 'streaming') return undefined;
    const inFlight = items.some((c) => c.streaming)
      || items.some((c) => c.kind === 'tool' && c.status === 'running')
      || items.some((c) => c.kind === 'approval' && c.decided == null);
    if (inFlight) return undefined;
    // Only arm once the assistant actually produced and SETTLED a reply this
    // turn (last item is a finished assistant message). send()/decide() flip
    // status to 'streaming' optimistically before any event arrives; without
    // this gate the timer would hide "IA pensando…" during the initial wait
    // before the first token — the executor (a Claude Code agent) often takes
    // more than 4s to start streaming.
    const last = items[items.length - 1];
    const settledReply = last && last.kind === 'text'
      && last.role === 'assistant' && !last.streaming;
    if (!settledReply) return undefined;
    const t = setTimeout(() => setStatus('idle'), 4000);
    return () => clearTimeout(t);
  }, [status, items]);

  const send = useCallback(async () => {
    const text = (input || '').trim();
    if ((!text && !pendingImage) || busy || !cid) return;
    setBusy(true); setError('');
    try {
      const payload = pendingImage
        ? { parts: [
            ...(text ? [{ type: 'text', text }] : []),
            { type: 'image', source: { type: 'base64',
              media_type: pendingImage.mediaType, data: pendingImage.b64 } },
          ] }
        : { text };
      const r = await apiJson(`${apiBase}/conversations/${cid}/messages`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload) });
      if (r.ok) {
        setItems((prev) => [...prev, { kind: 'text', id: `local-${Date.now()}`,
          role: 'user', content: text, streaming: false,
          image: pendingImage ? pendingImage.dataUrl : null }]);
        setInput(''); setPendingImage(null); setStatus('streaming');
        // O operador decidiu tentar de novo: o aviso de espera sai da frente.
        setLiveFailure(null);
        // "Sessão renovada — pode reenviar sua mensagem" cumpriu seu papel.
        if (onNoticeClear) onNoticeClear();
      } else {
        setError((r.body && r.body.error) || 'Falha ao enviar.');
      }
    } catch (e) { setError(String(e.message || e)); }
    setBusy(false);
  }, [input, pendingImage, busy, cid]);

  async function decide(approvalId, approved, reason) {
    setBusy(true); setError('');
    try {
      const r = await apiJson(`${apiBase}/conversations/${cid}/approve`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approval_id: approvalId, approved, reason }) });
      if (r.ok) {
        setItems((prev) => prev.map((c) => (c.kind === 'approval' && c.id === approvalId
          ? { ...c, decided: approved } : c)));
        setStatus('streaming');
      } else {
        setError((r.body && r.body.error) || 'Falha na decisão.');
      }
    } catch (e) { setError(String(e.message || e)); }
    setBusy(false);
  }

  function pickImage(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file || !file.type.startsWith('image/')) return;
    if (file.size > 5 * 1024 * 1024) { setError('Imagem acima de 5MB.'); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || '');
      const b64 = dataUrl.split(',')[1] || '';
      setPendingImage({ dataUrl, mediaType: file.type, b64 });
    };
    reader.readAsDataURL(file);
  }

  async function resume() {
    setBusy(true); setError('');
    try {
      const r = await apiJson(`${apiBase}/conversations/${cid}/resume`, { method: 'POST' });
      if (r.ok) setConv((c) => ({ ...(c || {}), status: 'ACTIVE' }));
      else setError((r.body && r.body.error) || 'Falha ao retomar.');
    } catch (e) { setError(String(e.message || e)); }
    setBusy(false);
  }

  const convStatus = (conv || {}).status || 'ACTIVE';
  // Quanto falta da espera — DERIVADO de `until`, nunca de um contador paralelo.
  const waitLeft = liveFailure && liveFailure.until
    ? Math.max(0, Math.ceil((liveFailure.until - Date.now()) / 1000)) : null;
  // Espera vencida ⇒ o aviso some sozinho, sem effect nem race.
  const activeFailure = (waitLeft !== null && waitLeft <= 0) ? null : liveFailure;
  const footer = footerStateFor({ convStatus, historyFailure,
                                  liveFailure: activeFailure });

  return html`
    <div class="flex flex-col border border-wa-border rounded-lg overflow-hidden flex-1 min-h-[360px]">
      <div ref=${scrollRef} class="flex-1 overflow-y-auto wa-scrollbar bg-wa-bg p-3 flex flex-col gap-2">
        ${items.length === 0 ? html`
          <div class="text-center text-wa-secondary text-[12px] py-6">
            A IA está analisando o atendimento… as mensagens aparecem aqui.
          </div>` : ''}
        ${items.map((item) => {
          if (item.kind === 'text') {
            return item.role === 'user'
              ? html`<${UserCard} key=${item.id} item=${item} />`
              : html`<${AssistantCard} key=${item.id} item=${item} />`;
          }
          if (item.kind === 'tool') return html`<${ToolCard} key=${item.id} item=${item} />`;
          if (item.kind === 'approval') {
            return html`<${ApprovalCard} key=${item.id} item=${item}
              onDecide=${decide} busy=${busy} />`;
          }
          return html`<${ErrorCard} key=${item.id} item=${item} />`;
        })}
        ${convStatus === 'ACTIVE' && status === 'streaming' && !items.some((c) => c.streaming) ? html`
          <div class="flex justify-start">
            <div class="text-[12px] text-wa-secondary flex items-center gap-1.5 px-2">
              <span class="inline-block w-1.5 h-1.5 rounded-full bg-wa-teal animate-pulse"></span>
              IA pensando…
            </div>
          </div>` : ''}
      </div>
      ${error ? html`<div class="text-[12px] text-red-500 px-3 py-1 bg-wa-panel border-t border-wa-border">${error}</div>` : ''}
      ${notice ? html`<div class="text-[12px] text-wa-teal px-3 py-1 bg-wa-panel border-t border-wa-border">${notice}</div>` : ''}
      ${footer.mode === 'wait' ? html`
        <div class="text-[12px] text-amber-700 px-3 py-1 bg-amber-50 border-t border-amber-400">
          ${`${WAIT_LABELS[footer.kind] || 'Executor indisponível'} — ${
            waitLeft ? `tentando de novo em ${waitLeft}s…` : 'tentando de novo…'}`}
        </div>` : ''}
      ${footer.mode === 'quota' ? html`
        <div class="text-[12px] text-amber-700 px-3 py-1 bg-amber-50 border-t border-amber-400">
          Sem crédito no executor — a IA não vai responder até recarregar. Avise o responsável.
        </div>` : ''}
      ${footer.mode === 'generic' ? html`
        <div class="text-[12px] text-wa-secondary px-3 py-1 bg-wa-panel border-t border-wa-border">
          ${footer.message || 'Falha no executor.'}
        </div>` : ''}
      ${footer.mode === 'auth_expired' ? html`
        <div class="bg-wa-panel border-t border-wa-border p-2 flex items-center justify-between gap-2">
          <span class="text-[12px] text-wa-secondary">
            Sessão do Claude expirada — a resposta acima é o erro do executor, não uma análise.
          </span>
          <button onClick=${() => onRenewSession && onRenewSession()} disabled=${!onRenewSession}
            class="px-3 py-1.5 rounded-full bg-wa-teal text-white text-[12px] font-medium hover:opacity-90 disabled:opacity-50 shrink-0">
            Renovar sessão</button>
        </div>`
      : convStatus === 'ACTIVE' ? html`
        <div class="bg-wa-panel border-t border-wa-border p-2">
          ${pendingImage ? html`
            <div class="flex items-center gap-2 mb-2">
              <img src=${pendingImage.dataUrl} class="h-[42px] rounded border border-wa-border" />
              <button onClick=${() => setPendingImage(null)}
                class="text-[11px] text-red-500 hover:underline">remover imagem</button>
            </div>` : ''}
          <div class="flex items-end gap-2">
            <label class="cursor-pointer text-wa-icon hover:text-wa-text p-1.5 rounded-full hover:bg-wa-hover" title="Anexar imagem">
              <input type="file" accept="image/*" class="hidden" onChange=${pickImage} />
              <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
            </label>
            <textarea class="wa-field flex-1 rounded-lg px-3 py-2 text-[13px] resize-none" rows="1"
              placeholder="Mensagem para a IA… (Enter envia)"
              value=${input}
              onInput=${(e) => setInput(e.target.value)}
              onKeyDown=${(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}></textarea>
            <button onClick=${send} disabled=${busy || (!input.trim() && !pendingImage)}
              class="px-4 py-2 rounded-full bg-wa-teal text-white text-[13px] font-medium hover:opacity-90 disabled:opacity-50">Enviar</button>
          </div>
        </div>` : html`
        <div class="bg-wa-panel border-t border-wa-border p-2 flex items-center justify-between">
          <span class="text-[12px] text-wa-secondary">
            Conversa ${convStatus === 'COMPLETED' ? 'concluída' : (convStatus === 'CANCELLED' ? 'cancelada' : 'com erro')}.
          </span>
          <button onClick=${resume} disabled=${busy}
            class="px-3 py-1.5 rounded-full border border-wa-teal text-wa-teal text-[12px] hover:bg-wa-teal/10 disabled:opacity-50">Continuar conversa</button>
        </div>`}
    </div>`;
}
