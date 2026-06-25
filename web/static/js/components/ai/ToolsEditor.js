// AI Engine — code-in-DB tools editor (plano 06, recurso avançado/ADM).
// Edita o código Python da tool no banco (description, code, dependencies,
// enabled). Badges de install_status/install_error. Excluir + histórico/rollback.
// Salvar/excluir agenda um restart do worker (o instalador re-materializa o
// código). Tools nascem DESABILITADAS por segurança — ligue manualmente.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  listTools,
  getTool,
  saveTool,
  deleteTool,
  getToolHistory,
  rollbackTool,
} from '../../services/api.js';

const html = htm.bind(h);

const NAME_RE = /^[a-z][a-z0-9_]{0,63}$/;

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

export function StatusBadge({ status }) {
  const map = {
    ok: ['bg-green-500/10', 'text-green-600', 'instalada'],
    pending: ['bg-amber-500/10', 'text-amber-600', 'pendente'],
    installing: ['bg-blue-500/10', 'text-blue-600', 'instalando'],
    failed: ['bg-red-500/10', 'text-red-600', 'falhou'],
  };
  const [bg, fg, label] = map[status] || ['bg-wa-hover', 'text-wa-secondary', status || '—'];
  return html`<span class="px-2 py-0.5 rounded-full text-[11px] ${bg} ${fg}">${label}</span>`;
}

export function HistoryModal({ title, versions, current, busy, onRollback, onClose }) {
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

export function ToolForm({ editing, onSave, onCancel, busy }) {
  const [name, setName] = useState(editing ? editing.name : '');
  const [description, setDescription] = useState(editing ? (editing.description || '') : '');
  const [code, setCode] = useState(editing ? (editing.code || '') : '');
  // dependencies: list -> one per line in the textarea.
  const [depsText, setDepsText] = useState(
    editing && Array.isArray(editing.dependencies) ? editing.dependencies.join('\n') : ''
  );
  const [enabled, setEnabled] = useState(editing ? !!editing.enabled : false);

  const isNew = !editing;
  const nameErr = isNew && name && !NAME_RE.test(name.trim())
    ? 'Use minúsculas, números e _ (começando por letra).' : '';
  const canSave = !busy && (editing || (name.trim() && !nameErr));

  function submit() {
    if (!canSave) return;
    const dependencies = depsText.split('\n').map(s => s.trim()).filter(Boolean);
    onSave(editing ? editing.name : name.trim(), {
      description: description.trim(),
      code,
      dependencies,
      enabled,
    });
  }

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${editing
          ? html`Editar tool <code class="text-[12px] text-wa-secondary">${editing.name}</code> · v${editing.version || 1}`
          : 'Nova tool (code-in-DB)'}
      </div>
      <div class="flex flex-col gap-3">
        ${isNew ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Nome (identidade da tool)</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono"
              type="text" placeholder="ex: consulta_estoque" value=${name}
              onInput=${(e) => setName(e.target.value)} />
            ${nameErr ? html`<div class="text-[12px] text-red-500 mt-1">${nameErr}</div>` : null}
          </div>
        ` : null}
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Descrição (enviada ao LLM)</label>
          <textarea class="wa-field w-full px-3 py-2 rounded-md text-[14px] resize-y" rows="2"
            value=${description} onInput=${(e) => setDescription(e.target.value)}></textarea>
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Código Python</label>
          <textarea class="wa-field w-full px-3 py-2 rounded-md text-[13px] font-mono resize-y" rows="14"
            spellcheck="false"
            placeholder="def execute(ctx, args):\n    ..."
            value=${code} onInput=${(e) => setCode(e.target.value)}></textarea>
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Dependências (pip, uma por linha)</label>
          <textarea class="wa-field w-full px-3 py-2 rounded-md text-[13px] font-mono resize-y" rows="2"
            spellcheck="false"
            placeholder="httpx>=0.27,<0.28"
            value=${depsText} onInput=${(e) => setDepsText(e.target.value)}></textarea>
        </div>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked=${enabled} onChange=${(e) => setEnabled(e.target.checked)} />
          <span class="text-[14px] text-wa-text">Habilitada (executa código do banco)</span>
        </label>
        <div class="text-[11px] text-amber-600">
          Salvar agenda um restart do worker para o instalador re-materializar o código.
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

export default function ToolsEditor() {
  const [tools, setTools] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);   // full tool object (with code)
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [historyFor, setHistoryFor] = useState(null);
  const [historyRows, setHistoryRows] = useState([]);
  const [historyBusy, setHistoryBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    const res = await listTools();
    if (res && res.ok) setTools(res.data || []);
    else setError((res && res.error) || 'Falha ao carregar tools.');
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  // The list endpoint already returns the full row (incl. code), but fetch the
  // single tool to be safe in case the list ever trims large fields.
  async function openEdit(row) {
    setError('');
    const res = await getTool(row.name);
    setEditing(res && res.ok ? res.data : row);
    setCreating(false);
  }

  async function handleSave(name, data) {
    setBusy(true); setError('');
    const res = await saveTool(name, data);
    setBusy(false);
    if (res && res.ok) { setEditing(null); setCreating(false); load(); }
    else setError((res && res.error) || 'Falha ao salvar a tool.');
  }

  async function handleDelete(row) {
    if (!confirm(`Excluir a tool "${row.name}"? Isso agenda um restart do worker.`)) return;
    const res = await deleteTool(row.name);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao excluir a tool.');
  }

  async function openHistory(tool) {
    setHistoryFor(tool);
    setHistoryRows([]);
    const res = await getToolHistory(tool.name);
    if (res && res.ok) setHistoryRows(res.data || []);
  }

  async function handleRollback(version) {
    if (!historyFor) return;
    setHistoryBusy(true);
    const res = await rollbackTool(historyFor.name, version);
    setHistoryBusy(false);
    if (res && res.ok) { setHistoryFor(null); load(); }
    else setError((res && res.error) || 'Falha ao reverter a versão.');
  }

  return html`
    <div>
      <div class="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 mb-4">
        <div class="text-[13px] text-amber-700 dark:text-amber-400 font-medium">Recurso avançado (ADM)</div>
        <div class="text-[12px] text-wa-secondary mt-0.5">
          Tools code-in-DB executam código Python arbitrário do banco. Só rodam com o
          kill-switch <code class="font-mono">ai_tools_code_enabled</code> ligado e nascem
          DESABILITADAS. Toda mudança agenda um restart do worker.
        </div>
      </div>

      <div class="flex items-center justify-between mb-4 gap-2">
        <p class="text-[13px] text-wa-secondary">Editor de tools com código no banco.</p>
        ${!creating && !editing ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setEditing(null); setError(''); }}>+ Nova tool</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${ToolForm} onSave=${handleSave} onCancel=${() => setCreating(false)} busy=${busy} />` : null}
      ${editing ? html`<${ToolForm} editing=${editing} onSave=${handleSave} onCancel=${() => setEditing(null)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && tools.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhuma tool code-in-DB cadastrada. Clique em <span class="font-medium">+ Nova tool</span>.
        </div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${tools.map(t => html`
          <div key=${t.name} class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3 flex-wrap">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <code class="text-[14px] text-wa-text font-medium">${t.name}</code>
                <span class="text-[11px] text-wa-secondary">v${t.version || 1}</span>
                <${StatusBadge} status=${t.install_status} />
                ${t.enabled
                  ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-500/10 text-green-600">Habilitada</span>`
                  : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Desabilitada</span>`}
              </div>
              ${t.description ? html`<div class="text-[12px] text-wa-secondary mt-1 break-words">${t.description}</div>` : null}
              ${(t.dependencies && t.dependencies.length) ? html`
                <div class="text-[11px] text-wa-secondary mt-1 font-mono">deps: ${t.dependencies.join(', ')}</div>
              ` : null}
              ${t.install_error ? html`
                <div class="text-[12px] text-red-500 mt-1 break-words whitespace-pre-wrap">${t.install_error}</div>
              ` : null}
            </div>
            <div class="flex gap-1 shrink-0 flex-wrap justify-end">
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => openEdit(t)}>Editar</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => openHistory(t)}>Histórico</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors"
                onClick=${() => handleDelete(t)}>Excluir</button>
            </div>
          </div>
        `)}
      </div>

      ${historyFor ? html`
        <${HistoryModal}
          title=${`Histórico — ${historyFor.name}`}
          versions=${historyRows}
          current=${historyFor.version}
          busy=${historyBusy}
          onRollback=${handleRollback}
          onClose=${() => setHistoryFor(null)} />
      ` : null}
    </div>
  `;
}
