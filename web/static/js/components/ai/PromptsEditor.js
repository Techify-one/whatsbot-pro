// AI Engine — prompts editor (plano 06). CRUD de prompt-templates: cada prompt
// tem um prompt_key (identidade) e um body com {placeholders} resolvidos a
// partir das variáveis. Editor com realce simples dos {placeholders} + histórico
// e rollback por versão. Salvar com um prompt_key novo cria o prompt.

import { h } from 'preact';
import { useEffect, useMemo, useState, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  listPrompts,
  savePrompt,
  getPromptHistory,
  rollbackPrompt,
} from '../../services/api.js';
import { useDeepLink } from '../../hooks/useDeepLink.js';

const html = htm.bind(h);

const KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;

function fmtDate(epoch) {
  if (epoch == null) return '—';
  try {
    const ms = typeof epoch === 'number' ? epoch * 1000 : Date.parse(epoch);
    const d = new Date(ms);
    if (isNaN(d.getTime())) return String(epoch);
    return d.toLocaleString('pt-BR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch (e) { return String(epoch); }
}

// Extract {placeholder} tokens from a body for a small preview chip list.
function extractPlaceholders(body) {
  const out = [];
  const seen = new Set();
  const re = /\{([a-zA-Z0-9_]+)\}/g;
  let m;
  while ((m = re.exec(body || '')) !== null) {
    if (!seen.has(m[1])) { seen.add(m[1]); out.push(m[1]); }
  }
  return out;
}

function HistoryModal({ title, versions, current, busy, onRollback, onClose }) {
  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onClose}>
      <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-md max-h-[80vh] overflow-y-auto"
        onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center justify-between mb-3">
          <div class="text-[15px] font-medium text-wa-text">${title}</div>
          <button class="text-wa-secondary hover:text-wa-text text-xl leading-none" onClick=${onClose}>×</button>
        </div>
        ${(!versions || versions.length === 0)
          ? html`<div class="text-[13px] text-wa-secondary py-4">Nenhuma versão registrada.</div>`
          : html`
            <div class="flex flex-col gap-2">
              ${versions.map(v => html`
                <div key=${v.version} class="flex items-center justify-between gap-2 bg-wa-panel border border-wa-border rounded-md px-3 py-2">
                  <div class="min-w-0">
                    <span class="text-[13px] text-wa-text font-medium">v${v.version}</span>
                    ${v.version === current ? html`<span class="ml-2 px-1.5 py-0.5 rounded-full text-[10px] bg-wa-teal/10 text-wa-teal">atual</span>` : null}
                    <div class="text-[11px] text-wa-secondary">${fmtDate(v.created_at)}</div>
                  </div>
                  <button class="px-2 py-1 rounded-md text-[12px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50 shrink-0"
                    disabled=${busy || v.version === current}
                    onClick=${() => onRollback(v.version)}>Reverter</button>
                </div>
              `)}
            </div>
          `}
      </div>
    </div>
  `;
}

function PromptForm({ editing, onSave, onCancel, busy }) {
  const [key, setKey] = useState(editing ? editing.prompt_key : '');
  const [body, setBody] = useState(editing ? (editing.body || '') : '');

  const isNew = !editing;
  const keyErr = isNew && key && !KEY_RE.test(key.trim())
    ? 'Use minúsculas, números e _ (começando por letra).' : '';
  const placeholders = useMemo(() => extractPlaceholders(body), [body]);
  const canSave = !busy && (editing || (key.trim() && !keyErr));

  function submit() {
    if (!canSave) return;
    onSave(editing ? editing.prompt_key : key.trim(), body);
  }

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${editing
          ? html`Editar prompt <code class="text-[12px] text-wa-secondary">${editing.prompt_key}</code> · v${editing.version || 1}`
          : 'Novo prompt'}
      </div>
      <div class="flex flex-col gap-3">
        ${isNew ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Chave (prompt_key)</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono"
              type="text" placeholder="ex: atendimento_padrao" value=${key}
              onInput=${(e) => setKey(e.target.value)} />
            ${keyErr ? html`<div class="text-[12px] text-red-500 mt-1">${keyErr}</div>` : null}
          </div>
        ` : null}
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Conteúdo do prompt</label>
          <textarea class="wa-field w-full px-3 py-2 rounded-md text-[13px] font-mono resize-y" rows="12"
            placeholder="Escreva a personalidade da IA. Use {variavel} para inserir valores."
            value=${body} onInput=${(e) => setBody(e.target.value)}></textarea>
        </div>
        <div>
          <div class="text-[12px] text-wa-secondary mb-1">Placeholders detectados</div>
          ${placeholders.length === 0
            ? html`<div class="text-[12px] text-wa-secondary">Nenhum {placeholder} no corpo.</div>`
            : html`<div class="flex flex-wrap gap-1">
                ${placeholders.map(p => html`
                  <span key=${p} class="px-2 py-0.5 rounded-full text-[11px] bg-wa-teal/10 text-wa-teal font-mono">{${p}}</span>
                `)}
              </div>`}
        </div>
        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${submit} disabled=${!canSave}>${busy ? 'Salvando…' : 'Salvar'}</button>
        </div>
      </div>
    </div>
  `;
}

export default function PromptsEditor({ initialEntity }) {
  const [prompts, setPrompts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [historyFor, setHistoryFor] = useState(null);
  const [historyRows, setHistoryRows] = useState([]);
  const [historyBusy, setHistoryBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    const res = await listPrompts();
    if (res && res.ok) setPrompts(res.data || []);
    else setError((res && res.error) || 'Falha ao carregar prompts.');
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  // Deep-link /ai/prompts/<prompt_key>: reabre o prompt da URL e reflete o aberto.
  const pushUrl = useDeepLink({
    tab: 'ai',
    resolve: initialEntity && initialEntity.sub === 'prompts'
      ? { sub: 'prompts', id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel || sel.id == null) { setEditing(null); return; }
      const p = prompts.find(x => x.prompt_key === sel.id);
      if (p) { setEditing(p); setCreating(false); }
    },
  });
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return; }
    pushUrl(editing ? { sub: 'prompts', id: editing.prompt_key } : { sub: 'prompts' });
  }, [editing]);

  async function handleSave(key, body) {
    setBusy(true); setError('');
    const res = await savePrompt(key, body);
    setBusy(false);
    if (res && res.ok) { setEditing(null); setCreating(false); load(); }
    else setError((res && res.error) || 'Falha ao salvar o prompt.');
  }

  async function openHistory(prompt) {
    setHistoryFor(prompt);
    setHistoryRows([]);
    const res = await getPromptHistory(prompt.prompt_key);
    if (res && res.ok) setHistoryRows(res.data || []);
  }

  async function handleRollback(version) {
    if (!historyFor) return;
    setHistoryBusy(true);
    const res = await rollbackPrompt(historyFor.prompt_key, version);
    setHistoryBusy(false);
    if (res && res.ok) { setHistoryFor(null); load(); }
    else setError((res && res.error) || 'Falha ao reverter a versão.');
  }

  return html`
    <div>
      <div class="flex items-center justify-between mb-4 gap-2">
        <p class="text-[13px] text-wa-secondary">
          Templates de prompt com {placeholders} resolvidos pelas variáveis. As mudanças
          valem na próxima mensagem.
        </p>
        ${!creating && !editing ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}>+ Novo prompt</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${PromptForm} onSave=${handleSave} onCancel=${() => setCreating(false)} busy=${busy} />` : null}
      ${editing ? html`<${PromptForm} editing=${editing} onSave=${handleSave} onCancel=${() => setEditing(null)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && prompts.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhum prompt cadastrado. Clique em <span class="font-medium">+ Novo prompt</span>.
        </div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${prompts.map(p => html`
          <div key=${p.prompt_key} class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3 flex-wrap">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <code class="text-[14px] text-wa-text font-medium">${p.prompt_key}</code>
                <span class="text-[11px] text-wa-secondary">v${p.version || 1}</span>
              </div>
              <div class="text-[12px] text-wa-secondary mt-1 break-words line-clamp-2 whitespace-pre-wrap">
                ${(p.body || '').slice(0, 160)}${(p.body || '').length > 160 ? '…' : ''}
              </div>
            </div>
            <div class="flex gap-1 shrink-0 flex-wrap justify-end">
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => { setEditing(p); setCreating(false); setError(''); }}>Editar</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => openHistory(p)}>Histórico</button>
            </div>
          </div>
        `)}
      </div>

      ${historyFor ? html`
        <${HistoryModal}
          title=${`Histórico — ${historyFor.prompt_key}`}
          versions=${historyRows}
          current=${historyFor.version}
          busy=${historyBusy}
          onRollback=${handleRollback}
          onClose=${() => setHistoryFor(null)} />
      ` : null}
    </div>
  `;
}
