// AI Engine — unified "Tools" tab. One list for EVERY tool: registered ones
// (core + plugin + installed code-in-DB, from /api/tools) merged by name with the
// code-in-DB definitions (from /api/ai/tools). All rows share the same UI:
//   - toggle on/off (Ativa)
//   - "Editar": code-in-DB rows open the Python editor; core/plugin rows open the
//     description/label override modal
//   - "Histórico": code-in-DB rows only (version rollback)
//   - NO delete (excluir) — kept off this screen by design
// The old "Registradas" / "Code-in-DB" sub-tabs were removed in favour of this.

import { h } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import {
  authHeaders,
  handleUnauthorized,
  listRegisteredTools,
  listTools,
  getTool,
  saveTool,
  deleteTool,
  getToolHistory,
  rollbackTool,
} from '../../services/api.js';
import { useDeepLink } from '../../hooks/useDeepLink.js';
import { useUrlState } from '../../hooks/useUrlState.js';
import { readParams, writeParams, bool, str } from '../../services/urlState.js';
import { EditModal } from '../ToolsManager.js';
import { ToolForm, HistoryModal, StatusBadge } from './ToolsEditor.js';

const html = htm.bind(h);

// Deep-link (Plano 24): ?q= reflete a busca (replace); ?history=1 é aditivo ao
// path /ai/tools/{name} (o path é dono do editor aberto; a flag abre o histórico).
const TOOLS_URL_SCHEMA = [
  str('q', ''),      // ?q=<termo> → filtro de busca
  bool('history'),   // ?history=1 → modal de histórico da tool code-in-DB
];

// In-app confirmation modal (substitui o confirm() nativo do navegador). Estilo
// alinhado ao HistoryModal/EditModal, legível no modo escuro (classes wa-*).
function ConfirmModal({ title, message, confirmLabel = 'Confirmar', danger = false, busy = false, onConfirm, onClose }) {
  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onClose}>
      <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-sm"
        onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center justify-between mb-2">
          <div class="text-[15px] font-medium text-wa-text">${title}</div>
          <button class="text-wa-secondary hover:text-wa-text text-xl leading-none" onClick=${onClose}>×</button>
        </div>
        <div class="text-[13px] text-wa-secondary mb-4 break-words">${message}</div>
        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onClose} disabled=${busy}>Cancelar</button>
          <button
            class="px-4 py-2 rounded-md text-[14px] text-white transition-opacity disabled:opacity-50 ${danger ? 'bg-red-600 hover:opacity-90' : 'bg-wa-teal hover:opacity-90'}"
            onClick=${onConfirm} disabled=${busy}>${busy ? 'Aguarde…' : confirmLabel}</button>
        </div>
      </div>
    </div>
  `;
}

export default function ToolsUnified({ initialEntity }) {
  const [regTools, setRegTools] = useState([]);   // /api/tools rows
  const [codeTools, setCodeTools] = useState([]); // /api/ai/tools rows (full, incl. code)
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(null);            // tool name being toggled/saved
  const [query, setQuery] = useState('');

  const [editingOverride, setEditingOverride] = useState(null); // tool name → EditModal
  const [editingCode, setEditingCode] = useState(null);         // full code obj → ToolForm
  const [creating, setCreating] = useState(false);              // ToolForm create mode
  const [formBusy, setFormBusy] = useState(false);

  const [historyFor, setHistoryFor] = useState(null); // {name, version}
  const [historyRows, setHistoryRows] = useState([]);
  const [historyBusy, setHistoryBusy] = useState(false);

  const [confirmDelete, setConfirmDelete] = useState(null); // row pendente de exclusão

  const formRef = useRef(null);
  useEffect(() => {
    if (editingCode || creating) {
      formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [editingCode, creating]);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const [reg, code] = await Promise.all([listRegisteredTools(), listTools()]);
      setRegTools(reg && reg.ok ? (reg.data || []) : []);
      setCodeTools(code && code.ok ? (code.data || []) : []);
      if (reg && !reg.ok) throw new Error(reg.error || 'Falha ao carregar tools.');
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data);
        if (ev.event === 'tools_changed') load();
      } catch {}
    };
    return () => ws.close();
  }, []);

  // Merge both sources by name: registered order first, then code-only rows.
  const rows = useMemo(() => {
    const regByName = new Map(regTools.map((t) => [t.name, t]));
    const codeByName = new Map(codeTools.map((t) => [t.name, t]));
    const names = [];
    const seen = new Set();
    for (const t of regTools) { names.push(t.name); seen.add(t.name); }
    for (const t of codeTools) { if (!seen.has(t.name)) { names.push(t.name); seen.add(t.name); } }
    return names.map((name) => {
      const reg = regByName.get(name) || null;
      const code = codeByName.get(name) || null;
      return {
        name,
        reg,
        code,
        isCode: !!code,
        // 'builtin' = core tool (não excluível); 'code' = code-in-DB do usuário
        // (excluível); 'plugin' = tool de plugin (override-only).
        kind: code ? (code.kind || 'code') : (reg && reg.plugin_id ? 'plugin' : 'core'),
        registered: !!reg,
        plugin_id: reg ? reg.plugin_id : null,
        label: (reg && reg.current_label) || name,
        enabled: reg ? !!reg.enabled : !!(code && code.enabled),
        customized: reg ? !!(reg.has_override || reg.has_label_override) : false,
        version: code ? (code.version || 1) : null,
        install_status: code ? code.install_status : null,
        install_error: code ? code.install_error : null,
      };
    });
  }, [regTools, codeTools]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) => {
      const label = (r.label || '').toLowerCase();
      const name = r.name.toLowerCase();
      const plugin = (r.plugin_id || 'core').toLowerCase();
      return label.includes(q) || name.includes(q) || plugin.includes(q);
    });
  }, [rows, query]);

  // ---- Deep link /ai/tools/<name> → open the matching editor ---------------
  const pushUrl = useDeepLink({
    tab: 'ai',
    resolve: initialEntity && initialEntity.sub === 'tools'
      ? { sub: 'tools', id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel || sel.id == null) { setEditingOverride(null); setEditingCode(null); return; }
      const row = rows.find((r) => r.name === sel.id);
      if (!row) return;
      if (row.isCode) openCodeEdit(row);
      else if (row.reg) setEditingOverride(sel.id);
    },
  });
  const editName = editingCode ? editingCode.name : editingOverride;
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return; }
    pushUrl(editName ? { sub: 'tools', id: editName } : { sub: 'tools' });
  }, [editName]);

  // Deep-link da query (Plano 24): hidrata busca (?q) no mount/popstate e reflete
  // no URL (replace); ?history=1 é aditivo ao path — reabre o histórico contra a
  // tool nomeada no path (editName) via openHistory (carrega historyRows).
  useUrlState({
    read: () => readParams(window.location.search, TOOLS_URL_SCHEMA),
    apply: (s) => {
      setQuery(s.q);
      if (s.history) {
        const row = rows.find((r) => r.name === (editName || (historyFor && historyFor.name)));
        if (row && row.isCode && !historyFor) openHistory(row);
      } else if (historyFor) setHistoryFor(null);
    },
    serialize: () => writeParams({
      q: query,
      history: !!historyFor,
    }, TOOLS_URL_SCHEMA),
    deps: [query, historyFor],
  });
  // Corrida de montagem: o path (editName) só resolve depois da lista carregar.
  // Quando resolver, reabre o histórico pedido pela URL uma única vez.
  const histHydratedRef = useRef(false);
  useEffect(() => {
    if (histHydratedRef.current || !editName) return;
    histHydratedRef.current = true;
    const s = readParams(window.location.search, TOOLS_URL_SCHEMA);
    if (!s.history || historyFor) return;
    const row = rows.find((r) => r.name === editName);
    if (row && row.isCode) openHistory(row);
  }, [editName]);

  // ---- Toggle (Ativa) -------------------------------------------------------
  async function toggle(row) {
    setBusy(row.name);
    try {
      if (row.registered) {
        // Immediate runtime override — no restart.
        const r = await fetch(`/api/tools/${encodeURIComponent(row.name)}`, {
          method: 'PUT',
          headers: authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ enabled: !row.enabled }),
        });
        if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
        const data = await r.json();
        if (!data.ok) throw new Error(data.error || 'failed');
        setRegTools((prev) => prev.map((t) => (t.name === row.name ? { ...t, ...data.data } : t)));
      } else if (row.code) {
        // Code-in-DB not yet registered: flip ai_tools.enabled — schedules a restart.
        const c = row.code;
        const res = await saveTool(row.name, {
          description: c.description || '',
          code: c.code || '',
          dependencies: c.dependencies || [],
          enabled: !row.enabled,
        });
        if (!res || !res.ok) throw new Error((res && res.error) || 'failed');
        load();
      }
    } catch (e) {
      alert('Erro ao salvar: ' + (e.message || e));
    } finally {
      setBusy(null);
    }
  }

  // ---- Edit: override modal (core/plugin) ----------------------------------
  async function saveOverride(name, body) {
    setBusy(name);
    try {
      const r = await fetch(`/api/tools/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'failed');
      setRegTools((prev) => prev.map((t) => (t.name === name ? { ...t, ...data.data } : t)));
      if ('description' in body || 'display_label' in body) setEditingOverride(null);
    } catch (e) {
      alert('Erro ao salvar: ' + (e.message || e));
    } finally {
      setBusy(null);
    }
  }

  // ---- Edit / create: code-in-DB form --------------------------------------
  async function openCodeEdit(row) {
    setError('');
    const res = await getTool(row.name);
    setEditingCode(res && res.ok ? res.data : row.code);
    setCreating(false);
  }

  async function saveCode(name, data) {
    setFormBusy(true);
    setError('');
    const res = await saveTool(name, data);
    setFormBusy(false);
    if (res && res.ok) { setEditingCode(null); setCreating(false); load(); }
    else setError((res && res.error) || 'Falha ao salvar a tool.');
  }

  // ---- History (code-in-DB only) -------------------------------------------
  async function openHistory(row) {
    setHistoryFor({ name: row.name, version: row.version });
    setHistoryRows([]);
    const res = await getToolHistory(row.name);
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

  // ---- Delete (code-in-DB criada pelo usuário; core/plugin não são excluíveis)
  // Abre o modal de confirmação in-app (não usa o confirm() nativo do navegador).
  async function confirmDeleteNow() {
    const row = confirmDelete;
    if (!row) return;
    setBusy(row.name);
    const res = await deleteTool(row.name);
    setBusy(null);
    setConfirmDelete(null);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao excluir a tool.');
  }

  const editingOverrideTool = editingOverride ? regTools.find((t) => t.name === editingOverride) : null;
  const showForm = creating || editingCode;

  return html`
    <div>
      <div class="text-sm text-wa-secondary mb-3">
        Tools são as ações que a IA pode executar (salvar contato, transferir pra humano,
        plugins, código no banco…). Aqui você liga/desliga cada uma, ajusta a descrição que
        vai pro modelo e edita o código das tools code-in-DB.
      </div>

      <div class="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 mb-4">
        <div class="text-[13px] text-amber-700 dark:text-amber-400 font-medium">Tools com código no banco (ADM)</div>
        <div class="text-[12px] text-wa-secondary mt-0.5">
          Tools code-in-DB executam código Python arbitrário do banco. Só rodam com o
          kill-switch <code class="font-mono">ai_tools_code_enabled</code> ligado e nascem
          DESABILITADAS. Salvar/ligar agenda um restart do worker.
        </div>
      </div>

      <div class="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <input
          type="search"
          value=${query}
          onInput=${(e) => setQuery(e.target.value)}
          placeholder="Buscar por nome, slug ou plugin…"
          class="flex-1 md:flex-none md:w-80 wa-field text-[13px] border border-wa-border rounded px-3 py-2 focus:outline-none focus:border-wa-teal"
        />
        ${!showForm ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setEditingCode(null); setError(''); }}>+ Nova tool</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-red-600 mb-3 text-sm">${error}</div>` : null}

      <div ref=${formRef}>
        ${creating ? html`<${ToolForm} onSave=${saveCode} onCancel=${() => setCreating(false)} busy=${formBusy} />` : null}
        ${editingCode ? html`<${ToolForm} editing=${editingCode} onSave=${saveCode} onCancel=${() => setEditingCode(null)} busy=${formBusy} />` : null}
      </div>

      ${loading ? html`<div class="text-wa-secondary">Carregando…</div>` : null}

      ${!loading && rows.length === 0 ? html`<div class="text-wa-secondary">Nenhuma tool registrada.</div>` : null}

      ${!loading && rows.length > 0 ? html`
        <div class="flex flex-col gap-2">
          ${filtered.length === 0
            ? html`<div class="text-wa-secondary text-center py-6">Nenhuma tool encontrada.</div>`
            : filtered.map((r) => {
              const desc = (r.code && r.code.description) || (r.reg && r.reg.current_description) || '';
              const deps = r.code && r.code.dependencies;
              return html`
                <div key=${r.name} class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3 flex-wrap">
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                      <code class="text-[14px] text-wa-text font-medium">${r.label}</code>
                      ${r.isCode ? html`<span class="text-[11px] text-wa-secondary">v${r.version}</span>` : null}
                      ${r.isCode
                        ? html`<${StatusBadge} status=${r.install_status} />`
                        : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">${r.plugin_id || 'core'}</span>`}
                      ${r.enabled
                        ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-500/10 text-green-600">Habilitada</span>`
                        : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Desabilitada</span>`}
                      ${r.customized ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-blue-500/10 text-blue-700">customizada</span>` : null}
                    </div>
                    ${r.name !== r.label ? html`<div class="text-[12px] text-wa-secondary mt-0.5 font-mono">${r.name}</div>` : null}
                    ${desc ? html`<div class="text-[12px] text-wa-secondary mt-1 break-words">${desc}</div>` : null}
                    ${(deps && deps.length) ? html`<div class="text-[11px] text-wa-secondary mt-1 font-mono">deps: ${deps.join(', ')}</div>` : null}
                    ${r.install_error ? html`<div class="text-[12px] text-red-500 mt-1 break-words whitespace-pre-wrap">${r.install_error}</div>` : null}
                  </div>
                  <div class="flex items-center gap-2 shrink-0 flex-wrap justify-end">
                    <label class="inline-flex items-center cursor-pointer align-middle" title=${r.enabled ? 'Desativar' : 'Ativar'}>
                      <input
                        type="checkbox"
                        class="sr-only peer"
                        checked=${r.enabled}
                        disabled=${busy === r.name}
                        onChange=${() => toggle(r)}
                      />
                      <div class="relative w-9 h-5 bg-wa-border rounded-full peer peer-checked:bg-green-600 transition-colors after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-transform peer-checked:after:translate-x-4"></div>
                    </label>
                    <button
                      onClick=${() => (r.isCode ? openCodeEdit(r) : setEditingOverride(r.name))}
                      class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                    >Editar</button>
                    ${r.isCode ? html`
                      <button
                        onClick=${() => openHistory(r)}
                        class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                      >Histórico</button>
                    ` : null}
                    ${r.kind === 'code' ? html`
                      <button
                        onClick=${() => setConfirmDelete(r)}
                        disabled=${busy === r.name}
                        class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors disabled:opacity-50"
                      >Excluir</button>
                    ` : null}
                  </div>
                </div>
              `;
            })
          }
        </div>
      ` : null}

      ${editingOverrideTool ? html`
        <${EditModal}
          tool=${editingOverrideTool}
          onClose=${() => setEditingOverride(null)}
          onSave=${saveOverride}
          busy=${busy === editingOverrideTool.name}
        />
      ` : null}

      ${historyFor ? html`
        <${HistoryModal}
          title=${`Histórico — ${historyFor.name}`}
          versions=${historyRows}
          current=${historyFor.version}
          busy=${historyBusy}
          onRollback=${handleRollback}
          onClose=${() => setHistoryFor(null)} />
      ` : null}

      ${confirmDelete ? html`
        <${ConfirmModal}
          title="Excluir tool"
          message=${html`Excluir a tool <code class="text-wa-text font-medium">${confirmDelete.name}</code>? Isso agenda um restart do worker.`}
          confirmLabel="Excluir"
          danger=${true}
          busy=${busy === confirmDelete.name}
          onConfirm=${confirmDeleteNow}
          onClose=${() => setConfirmDelete(null)} />
      ` : null}
    </div>
  `;
}
