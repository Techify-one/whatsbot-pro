// Quick replies management screen (plano 04) — lista global única, texto puro.
// CRUD com validação ao vivo do short_code (P45). Dispara o evento global
// `whatsbot:quick-replies-changed` ao salvar/excluir para o composer recarregar.

import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  getQuickReplies,
  createQuickReply,
  updateQuickReply,
  deleteQuickReply,
} from '../services/api.js';
import { useDeepLink } from '../hooks/useDeepLink.js';

const html = htm.bind(h);

const SHORT_CODE_RE = /^[a-z0-9][a-z0-9_-]*$/;

function shortCodeError(code) {
  const c = (code || '').trim();
  if (!c) return 'O atalho é obrigatório.';
  if (c.startsWith('/')) return 'Não comece com "/" — ele é só o gatilho.';
  if (!SHORT_CODE_RE.test(c)) return 'Só minúsculas, números, "-" e "_" (sem espaços ou acentos).';
  return '';
}

function notifyChanged() {
  try { window.dispatchEvent(new Event('whatsbot:quick-replies-changed')); } catch (e) {}
}

function QuickReplyForm({ editing, onSubmit, onCancel, busy }) {
  const [shortCode, setShortCode] = useState(editing ? editing.short_code : '');
  const [content, setContent] = useState(editing ? editing.content : '');

  const scErr = shortCode ? shortCodeError(shortCode) : '';
  const canSave = !scErr && shortCode.trim() && content.trim() && !busy;

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${editing ? 'Editar resposta rápida' : 'Nova resposta rápida'}
      </div>
      <div class="flex flex-col gap-3">
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Atalho</label>
          <div class="flex items-center gap-1">
            <span class="text-wa-secondary text-[15px]">/</span>
            <input
              class="wa-field flex-1 px-3 py-2 rounded-md text-[14px]"
              type="text"
              placeholder="oi-anna"
              value=${shortCode}
              onInput=${(e) => setShortCode(e.target.value.toLowerCase())}
            />
          </div>
          ${scErr ? html`<div class="text-[12px] text-red-500 mt-1">${scErr}</div>` : null}
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Conteúdo</label>
          <textarea
            class="wa-field w-full px-3 py-2 rounded-md text-[14px] min-h-[90px] resize-y"
            placeholder="Olá! Sou a Anna, como posso ajudar?"
            value=${content}
            onInput=${(e) => setContent(e.target.value)}
          ></textarea>
        </div>
        <div class="flex gap-2 justify-end">
          <button
            class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel}
            disabled=${busy}
          >Cancelar</button>
          <button
            class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${() => canSave && onSubmit({ short_code: shortCode.trim(), content: content.trim() })}
            disabled=${!canSave}
          >${busy ? 'Salvando…' : 'Salvar'}</button>
        </div>
      </div>
    </div>
  `;
}

export default function QuickReplies({ initialEntity }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);   // row being edited
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    const res = await getQuickReplies();
    if (res && res.ok) setItems(res.data || []);
    else setError((res && res.error) || 'Falha ao carregar.');
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  // Deep-link /quick-replies/<short_code>: reabre a resposta rápida da URL.
  const pushUrl = useDeepLink({
    tab: 'quick-replies',
    resolve: initialEntity ? { id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel || sel.id == null) { setEditing(null); return; }
      const r = items.find(x => x.short_code === sel.id);
      if (r) { setEditing(r); setCreating(false); }
    },
  });
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return; }
    pushUrl(editing ? { id: editing.short_code } : null);
  }, [editing]);

  async function handleCreate(data) {
    setBusy(true); setError('');
    const res = await createQuickReply(data);
    setBusy(false);
    if (res && res.ok) { setCreating(false); notifyChanged(); load(); }
    else setError((res && res.error) || 'Falha ao criar.');
  }

  async function handleUpdate(data) {
    setBusy(true); setError('');
    const res = await updateQuickReply(editing.id, data);
    setBusy(false);
    if (res && res.ok) { setEditing(null); notifyChanged(); load(); }
    else setError((res && res.error) || 'Falha ao salvar.');
  }

  async function handleDelete(row) {
    if (!confirm(`Excluir a resposta rápida /${row.short_code}?`)) return;
    const res = await deleteQuickReply(row.id);
    if (res && res.ok) { notifyChanged(); load(); }
    else setError((res && res.error) || 'Falha ao excluir.');
  }

  return html`
    <div>
      <div class="flex items-center justify-between mb-4">
        <p class="text-[13px] text-wa-secondary">
          Lista global de respostas rápidas. No chat, digite <span class="font-mono">/atalho</span> para expandir.
        </p>
        ${!creating && !editing ? html`
          <button
            class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}
          >+ Nova</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${QuickReplyForm} onSubmit=${handleCreate} onCancel=${() => setCreating(false)} busy=${busy} />` : null}
      ${editing ? html`<${QuickReplyForm} editing=${editing} onSubmit=${handleUpdate} onCancel=${() => setEditing(null)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && items.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhuma resposta rápida ainda. Clique em <span class="font-medium">+ Nova</span> para criar.
        </div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${items.map(row => html`
          <div key=${row.id} class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3">
            <div class="flex-1 min-w-0">
              <div class="text-[14px] font-mono text-wa-teal">/${row.short_code}</div>
              <div class="text-[13px] text-wa-text whitespace-pre-wrap break-words mt-0.5">${row.content}</div>
            </div>
            <div class="flex gap-1 shrink-0">
              <button
                class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => { setEditing(row); setCreating(false); setError(''); }}
              >Editar</button>
              <button
                class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors"
                onClick=${() => handleDelete(row)}
              >Excluir</button>
            </div>
          </div>
        `)}
      </div>
    </div>
  `;
}
