// Master-detail modal for an agent's dedicated PROMPT version trail (git-like).
// Left: version list (v{n}, "atual" badge, date, +N −M chip, commit note) with
// per-version rename/delete actions. Right: two "de v_X com v_Y" selectors
// (compare any two versions; default = previous → selected) + <PromptDiff/> +
// Restaurar. Separate from the whole-agent Histórico.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  getAgentPromptHistory,
  getAgentPromptDiff,
  restoreAgentPrompt,
  renameAgentPromptVersion,
  deleteAgentPromptVersion,
} from '../../services/api.js';
import { PromptDiff } from './PromptDiff.js';

const html = htm.bind(h);

function fmtDate(epoch) {
  if (epoch == null) return '—';
  try {
    const d = new Date(typeof epoch === 'number' ? epoch * 1000 : Date.parse(epoch));
    if (isNaN(d.getTime())) return String(epoch);
    return d.toLocaleString('pt-BR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch (e) {
    return String(epoch);
  }
}

function DeltaChip({ added, removed }) {
  return html`
    <span class="inline-flex items-center gap-1 text-[10px] font-mono">
      <span class="px-1 rounded-sm bg-green-500/30 text-green-700">+${added || 0}</span>
      <span class="px-1 rounded-sm bg-red-500/30 text-red-700">−${removed || 0}</span>
    </span>
  `;
}

export default function PromptHistoryModal({ agentKey, agentLabel, currentVersion, onClose, onRestored, canVersion = true, canDelete = true }) {
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [fromV, setFromV] = useState(null);
  const [toV, setToV] = useState(null);
  const [diff, setDiff] = useState(null);
  const [diffBusy, setDiffBusy] = useState(false);
  const [restoreBusy, setRestoreBusy] = useState(false);

  // Rename / delete sub-modals.
  const [renameTarget, setRenameTarget] = useState(null);  // version row or null
  const [renameValue, setRenameValue] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);  // version row or null
  const [deleteBusy, setDeleteBusy] = useState(false);

  async function loadHistory({ keepSelection } = {}) {
    setLoading(true); setError('');
    const res = await getAgentPromptHistory(agentKey);
    if (res && res.ok) {
      const rows = res.data || [];
      setVersions(rows);
      const stillThere = keepSelection && rows.some((r) => r.version === toV);
      if (!stillThere && rows.length) {
        const newest = rows[0].version;                 // history is newest-first
        const prev = rows.length > 1 ? rows[1].version : newest;
        setToV(newest);
        setFromV(prev);
      } else if (!rows.length) {
        setToV(null);
        setFromV(null);
      }
    } else {
      setError((res && res.error) || 'Falha ao carregar o histórico do prompt.');
    }
    setLoading(false);
  }

  // Load the trail once.
  useEffect(() => {
    let alive = true;
    (async () => {
      const res = await getAgentPromptHistory(agentKey);
      if (!alive) return;
      setLoading(true); setError('');
      if (res && res.ok) {
        const rows = res.data || [];
        setVersions(rows);
        if (rows.length) {
          const newest = rows[0].version;                 // history is newest-first
          const prev = rows.length > 1 ? rows[1].version : newest;
          setToV(newest);
          setFromV(prev);
        }
      } else {
        setError((res && res.error) || 'Falha ao carregar o histórico do prompt.');
      }
      setLoading(false);
    })();
    return () => { alive = false; };
  }, [agentKey]);

  // Recompute the diff whenever the selection changes.
  useEffect(() => {
    if (fromV == null || toV == null) { setDiff(null); return; }
    let alive = true;
    (async () => {
      setDiffBusy(true);
      const res = await getAgentPromptDiff(agentKey, fromV, toV);
      if (!alive) return;
      if (res && res.ok) setDiff(res.data);
      else setDiff(null);
      setDiffBusy(false);
    })();
    return () => { alive = false; };
  }, [agentKey, fromV, toV]);

  function selectVersion(v) {
    // Selecting a version diffs (its predecessor → it). Single version → identity.
    const idx = versions.findIndex((r) => r.version === v);
    const prev = idx >= 0 && idx + 1 < versions.length ? versions[idx + 1].version : v;
    setToV(v);
    setFromV(prev);
  }

  async function handleRestore(v) {
    setRestoreBusy(true); setError('');
    const res = await restoreAgentPrompt(agentKey, v);
    setRestoreBusy(false);
    if (res && res.ok) {
      if (onRestored) onRestored(res.data);
      onClose();
    } else {
      setError((res && res.error) || 'Falha ao restaurar a versão.');
    }
  }

  function openRename(r) {
    setRenameValue(r.note || '');
    setRenameTarget(r);
  }

  async function handleRename() {
    if (!renameTarget) return;
    setRenameBusy(true); setError('');
    const res = await renameAgentPromptVersion(agentKey, renameTarget.version, renameValue);
    setRenameBusy(false);
    if (res && res.ok) {
      setRenameTarget(null);
      await loadHistory({ keepSelection: true });
    } else {
      setError((res && res.error) || 'Falha ao renomear a versão.');
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleteBusy(true); setError('');
    const res = await deleteAgentPromptVersion(agentKey, deleteTarget.version);
    setDeleteBusy(false);
    if (res && res.ok) {
      setDeleteTarget(null);
      await loadHistory({ keepSelection: true });
    } else {
      setError((res && res.error) || 'Falha ao excluir a versão.');
    }
  }

  const versionOptions = versions.map((r) => r.version);
  const isFirstSelected = toV != null && fromV === toV;
  // The trail's current version is its newest (history is newest-first) unless the
  // caller pins one explicitly.
  const effectiveCurrent = currentVersion != null
    ? currentVersion
    : (versions.length ? versions[0].version : null);

  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onClose}>
      <div class="bg-wa-bg border border-wa-border rounded-lg w-full max-w-6xl max-h-[92vh] flex flex-col"
        onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center justify-between px-5 py-3 border-b border-wa-border">
          <div class="text-[15px] font-medium text-wa-text">Versões do prompt — ${agentLabel || agentKey}</div>
          <button class="text-wa-secondary hover:text-wa-text text-xl leading-none" onClick=${onClose}>×</button>
        </div>

        ${error ? html`<div class="px-5 pt-3 text-[13px] text-red-500">${error}</div>` : null}

        <div class="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
          <!-- Left: version list -->
          <div class="md:w-64 shrink-0 border-b md:border-b-0 md:border-r border-wa-border overflow-y-auto p-3">
            ${loading
              ? html`<div class="text-[13px] text-wa-secondary">Carregando…</div>`
              : (versions.length === 0
                ? html`<div class="text-[13px] text-wa-secondary">Nenhuma versão registrada.</div>`
                : html`<div class="flex flex-col gap-1.5">
                    ${versions.map((r) => html`
                      <div key=${r.version}
                        onClick=${() => selectVersion(r.version)}
                        class=${`group text-left bg-wa-panel border border-wa-border rounded-md px-2.5 py-2 hover:bg-wa-hover transition-colors cursor-pointer ${r.version === toV ? 'ring-1 ring-wa-teal' : ''}`}>
                        <div class="flex items-center gap-2 flex-wrap">
                          <span class="text-[13px] text-wa-text font-medium">v${r.version}</span>
                          ${r.version === effectiveCurrent ? html`<span class="px-1.5 py-0.5 rounded-full text-[10px] bg-wa-teal/10 text-wa-teal">atual</span>` : null}
                          <${DeltaChip} added=${r.added_lines} removed=${r.removed_lines} />
                          <span class="ml-auto flex items-center gap-1">
                            ${canVersion ? html`
                              <button type="button" title="Renomear versão"
                                class="text-wa-secondary hover:text-wa-teal p-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                                onClick=${(e) => { e.stopPropagation(); openRename(r); }}>✎</button>
                            ` : null}
                            ${canDelete ? html`
                              <button type="button" title="Excluir versão"
                                class="text-wa-secondary hover:text-red-500 p-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                                onClick=${(e) => { e.stopPropagation(); setDeleteTarget(r); }}>🗑</button>
                            ` : null}
                          </span>
                        </div>
                        <div class="text-[11px] text-wa-secondary mt-0.5">${fmtDate(r.created_at)}</div>
                        ${r.note ? html`<div class="text-[11px] text-wa-secondary italic mt-0.5 break-words">${r.note}</div>` : null}
                      </div>
                    `)}
                  </div>`)}
          </div>

          <!-- Right: comparison -->
          <div class="flex-1 min-w-0 overflow-y-auto p-4 flex flex-col gap-3">
            ${(!loading && versions.length > 0) ? html`
              <div class="flex items-center gap-2 flex-wrap text-[12px] text-wa-secondary">
                <span>Comparar de</span>
                <select class="wa-field px-2 py-1 rounded-md text-[12px]"
                  value=${fromV ?? ''} onChange=${(e) => setFromV(parseInt(e.target.value, 10))}>
                  ${versionOptions.map((v) => html`<option key=${v} value=${v}>v${v}</option>`)}
                </select>
                <span>com</span>
                <select class="wa-field px-2 py-1 rounded-md text-[12px]"
                  value=${toV ?? ''} onChange=${(e) => setToV(parseInt(e.target.value, 10))}>
                  ${versionOptions.map((v) => html`<option key=${v} value=${v}>v${v}</option>`)}
                </select>
              </div>
            ` : null}

            ${loading
              ? html`<div class="text-[13px] text-wa-secondary">Carregando…</div>`
              : (versions.length === 0
                ? null
                : (isFirstSelected
                  ? html`<div class="text-[13px] text-wa-secondary py-2">Primeira versão — sem comparação.</div>`
                  : (diffBusy
                    ? html`<div class="text-[13px] text-wa-secondary">Calculando diff…</div>`
                    : html`<${PromptDiff} lines=${diff && diff.lines} unifiedDiff=${diff && diff.unified_diff} />`)))}

            ${(!loading && toV != null && canVersion) ? html`
              <div class="flex justify-end pt-1">
                <button class="px-3 py-2 rounded-md text-[13px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
                  disabled=${restoreBusy}
                  onClick=${() => handleRestore(toV)}>
                  ${restoreBusy ? 'Restaurando…' : `Restaurar v${toV}`}
                </button>
              </div>
            ` : null}
          </div>
        </div>
      </div>

      ${renameTarget ? html`
        <div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
          onClick=${() => !renameBusy && setRenameTarget(null)}>
          <div class="bg-wa-bg border border-wa-border rounded-lg w-full max-w-md flex flex-col"
            onClick=${(e) => e.stopPropagation()}>
            <div class="px-5 py-3 border-b border-wa-border text-[15px] font-medium text-wa-text">
              Renomear versão v${renameTarget.version}
            </div>
            <div class="px-5 py-4 flex flex-col gap-2">
              <label class="text-[12px] text-wa-secondary">Nome da versão</label>
              <input type="text" class="wa-field px-3 py-2 rounded-md text-[13px]"
                value=${renameValue} autofocus
                placeholder="Ex.: ajuste de tom"
                onInput=${(e) => setRenameValue(e.target.value)}
                onKeyDown=${(e) => { if (e.key === 'Enter') handleRename(); }} />
            </div>
            <div class="px-5 py-3 border-t border-wa-border flex justify-end gap-2">
              <button class="px-3 py-2 rounded-md text-[13px] text-wa-text bg-wa-panel border border-wa-border hover:bg-wa-hover transition-colors disabled:opacity-50"
                disabled=${renameBusy} onClick=${() => setRenameTarget(null)}>Cancelar</button>
              <button class="px-3 py-2 rounded-md text-[13px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
                disabled=${renameBusy} onClick=${handleRename}>
                ${renameBusy ? 'Salvando…' : 'Salvar'}
              </button>
            </div>
          </div>
        </div>
      ` : null}

      ${deleteTarget ? html`
        <div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
          onClick=${() => !deleteBusy && setDeleteTarget(null)}>
          <div class="bg-wa-bg border border-wa-border rounded-lg w-full max-w-md flex flex-col"
            onClick=${(e) => e.stopPropagation()}>
            <div class="px-5 py-3 border-b border-wa-border text-[15px] font-medium text-wa-text">
              Excluir versão v${deleteTarget.version}
            </div>
            <div class="px-5 py-4 text-[13px] text-wa-text">
              Tem certeza que deseja excluir a versão <span class="font-medium">v${deleteTarget.version}</span> do histórico?
              Esta ação não pode ser desfeita. O prompt atual do agente não é afetado.
            </div>
            <div class="px-5 py-3 border-t border-wa-border flex justify-end gap-2">
              <button class="px-3 py-2 rounded-md text-[13px] text-wa-text bg-wa-panel border border-wa-border hover:bg-wa-hover transition-colors disabled:opacity-50"
                disabled=${deleteBusy} onClick=${() => setDeleteTarget(null)}>Cancelar</button>
              <button class="px-3 py-2 rounded-md text-[13px] text-white bg-red-600 hover:opacity-90 transition-opacity disabled:opacity-50"
                disabled=${deleteBusy} onClick=${handleDelete}>
                ${deleteBusy ? 'Excluindo…' : 'Excluir'}
              </button>
            </div>
          </div>
        </div>
      ` : null}
    </div>
  `;
}
