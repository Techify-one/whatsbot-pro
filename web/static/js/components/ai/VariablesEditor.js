// AI Engine — variables editor (plano 06). CRUD simples de variáveis globais
// (name/value) referenciadas pelos prompts via {name}. Sem histórico/versão.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  listVariables,
  saveVariable,
  deleteVariable,
} from '../../services/api.js';

const html = htm.bind(h);

const NAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{0,63}$/;

function VariableForm({ editing, onSave, onCancel, busy }) {
  const [name, setName] = useState(editing ? editing.name : '');
  const [value, setValue] = useState(editing ? (editing.value || '') : '');

  const isNew = !editing;
  const nameErr = isNew && name && !NAME_RE.test(name.trim())
    ? 'Use letras, números e _ (começando por letra).' : '';
  const canSave = !busy && (editing || (name.trim() && !nameErr));

  function submit() {
    if (!canSave) return;
    onSave(editing ? editing.name : name.trim(), value);
  }

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${editing ? html`Editar variável <code class="text-[12px] text-wa-secondary">${editing.name}</code>` : 'Nova variável'}
      </div>
      <div class="flex flex-col gap-3">
        ${isNew ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono"
              type="text" placeholder="ex: nome_empresa" value=${name}
              onInput=${(e) => setName(e.target.value)} />
            ${nameErr ? html`<div class="text-[12px] text-red-500 mt-1">${nameErr}</div>` : null}
            <div class="text-[11px] text-wa-secondary mt-1">Use no prompt como <code class="font-mono">{${name.trim() || 'nome'}}</code>.</div>
          </div>
        ` : null}
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Valor</label>
          <textarea class="wa-field w-full px-3 py-2 rounded-md text-[14px] resize-y" rows="3"
            value=${value} onInput=${(e) => setValue(e.target.value)}></textarea>
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

export default function VariablesEditor() {
  const [vars, setVars] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    const res = await listVariables();
    if (res && res.ok) setVars(res.data || []);
    else setError((res && res.error) || 'Falha ao carregar variáveis.');
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleSave(name, value) {
    setBusy(true); setError('');
    const res = await saveVariable(name, value);
    setBusy(false);
    if (res && res.ok) { setEditing(null); setCreating(false); load(); }
    else setError((res && res.error) || 'Falha ao salvar a variável.');
  }

  async function handleDelete(row) {
    if (!confirm(`Excluir a variável "${row.name}"? Esta ação não pode ser desfeita.`)) return;
    const res = await deleteVariable(row.name);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao excluir a variável.');
  }

  return html`
    <div>
      <div class="flex items-center justify-between mb-4 gap-2">
        <p class="text-[13px] text-wa-secondary">
          Valores globais usados pelos prompts via {nome}. As mudanças valem na próxima mensagem.
        </p>
        ${!creating && !editing ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}>+ Nova variável</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${VariableForm} onSave=${handleSave} onCancel=${() => setCreating(false)} busy=${busy} />` : null}
      ${editing ? html`<${VariableForm} editing=${editing} onSave=${handleSave} onCancel=${() => setEditing(null)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && vars.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhuma variável cadastrada. Clique em <span class="font-medium">+ Nova variável</span>.
        </div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${vars.map(v => html`
          <div key=${v.name} class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3 flex-wrap">
            <div class="flex-1 min-w-0">
              <code class="text-[14px] text-wa-text font-medium">{${v.name}}</code>
              <div class="text-[13px] text-wa-secondary mt-1 break-words whitespace-pre-wrap">${v.value || '—'}</div>
            </div>
            <div class="flex gap-1 shrink-0 flex-wrap justify-end">
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => { setEditing(v); setCreating(false); setError(''); }}>Editar</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors"
                onClick=${() => handleDelete(v)}>Excluir</button>
            </div>
          </div>
        `)}
      </div>
    </div>
  `;
}
