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
import { reduceAiEvent, isAuthError, persistedToItems } from './chat_core.js';

const html = htm.bind(h);

export { reduceAiEvent, isAuthError, persistedToItems };

// ── Hook: eventos da conversa via /ws ────────────────────────────────────────

export function useAiChatEvents(conversationId, onEvent) {
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;
  useEffect(() => {
    if (!conversationId) return undefined;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    try {
      ws = new WebSocket(`${proto}//${location.host}/ws`);
      ws.onmessage = (m) => {
        try {
          const msg = JSON.parse(m.data);
          if (msg.event !== 'plugin_melhorias_ai_event') return;
          const d = msg.data || {};
          if (d.conversation_id !== conversationId) return;
          cbRef.current && cbRef.current({ event: d.event, data: d.data || {} });
        } catch (_) { /* ignore */ }
      };
    } catch (_) { /* ignore */ }
    return () => { try { ws && ws.close(); } catch (_) { /* ignore */ } };
  }, [conversationId]);
}

// ── Cards ────────────────────────────────────────────────────────────────────

function AssistantCard({ item }) {
  return html`
    <div class="flex justify-start">
      <div class="bg-wa-incoming text-wa-text rounded-[7.5px] px-3 py-2 max-w-[85%] text-[13px] whitespace-pre-wrap">
        ${item.content || (item.streaming ? '…' : '')}
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
                              onAuthError = null, onConversationEnd = null }) {
  const cid = conversation && conversation.id;
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('idle');
  const [input, setInput] = useState('');
  const [pendingImage, setPendingImage] = useState(null); // {dataUrl, mediaType, b64}
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const scrollRef = useRef(null);

  // Hidrata do DB ao abrir.
  useEffect(() => {
    if (!cid) return;
    (async () => {
      try {
        const r = await apiJson(`${apiBase}/conversations/${cid}`);
        if (r.ok) {
          setItems(persistedToItems(r.body.data.messages, r.body.data.approvals));
          const st = (r.body.data.conversation || {}).status;
          setStatus(st === 'ACTIVE' ? 'idle' : 'idle');
        }
      } catch (_) { /* ignore */ }
    })();
  }, [cid]);

  // Eventos ao vivo via /ws.
  useAiChatEvents(cid, (ev) => {
    setItems((prev) => {
      const out = reduceAiEvent(prev, ev, status);
      setStatus(out.status);
      // Auth-error pode vir como texto de message_end OU como evento error.
      if (ev.event === 'message_end') {
        const msg = out.items.find((c) => c.kind === 'text' && c.id === ev.data.messageId);
        if (msg && isAuthError(msg.content) && onAuthError) onAuthError();
      }
      if (ev.event === 'error' && isAuthError(ev.data && ev.data.message) && onAuthError) onAuthError();
      if (ev.event === 'done' && onConversationEnd) onConversationEnd();
      return out.items;
    });
  });

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items]);

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
      if (!r.ok) setError((r.body && r.body.error) || 'Falha ao retomar.');
    } catch (e) { setError(String(e.message || e)); }
    setBusy(false);
  }

  const convStatus = (conversation || {}).status || 'ACTIVE';

  return html`
    <div class="flex flex-col border border-wa-border rounded-lg overflow-hidden" style="height: 420px;">
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
        ${status === 'streaming' && !items.some((c) => c.streaming) ? html`
          <div class="flex justify-start">
            <div class="text-[12px] text-wa-secondary flex items-center gap-1.5 px-2">
              <span class="inline-block w-1.5 h-1.5 rounded-full bg-wa-teal animate-pulse"></span>
              IA pensando…
            </div>
          </div>` : ''}
      </div>
      ${error ? html`<div class="text-[12px] text-red-500 px-3 py-1 bg-wa-panel border-t border-wa-border">${error}</div>` : ''}
      ${convStatus === 'ACTIVE' ? html`
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
