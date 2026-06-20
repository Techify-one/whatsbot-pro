// Custom attributes admin screen (plano 05) — full-page (FQ6).
// Define contact (and later conversation) custom attributes: key, type, options…
// attribute_key is identity: auto-derived from the name and immutable after create.
// Dispatches `whatsbot:custom-attributes-changed` so open contact panels reload.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  getCustomAttributes,
  createCustomAttribute,
  updateCustomAttribute,
  deleteCustomAttribute,
} from '../services/api.js';

const html = htm.bind(h);

const TYPES = [
  ['text', 'Texto'],
  ['number', 'Número'],
  ['date', 'Data'],
  ['list', 'Lista (opções)'],
  ['checkbox', 'Sim/Não'],
  ['link', 'Link'],
];

// Scope: who the attribute belongs to. Immutable after creation (backend enforces
// UNIQUE(attribute_key, applies_to)). contact → contacts.custom_attributes;
// conversation → conversations.custom_attributes.
const SCOPES = [
  ['contact', 'Contato'],
  ['conversation', 'Conversa'],
];

const KEY_RE = /^[a-z][a-z0-9_]*$/;

function slugify(name) {
  return (name || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')   // strip accents
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/^([0-9])/, '_$1');                          // key must start with a letter
}

function notifyChanged() {
  try { window.dispatchEvent(new Event('whatsbot:custom-attributes-changed')); } catch (e) {}
}

function AttributeForm({ editing, onSubmit, onCancel, busy }) {
  const [displayName, setDisplayName] = useState(editing ? editing.display_name : '');
  const [key, setKey] = useState(editing ? editing.attribute_key : '');
  const [keyTouched, setKeyTouched] = useState(!!editing);
  const [type, setType] = useState(editing ? editing.type : 'text');
  const [appliesTo, setAppliesTo] = useState(editing ? editing.applies_to : 'contact');
  const [options, setOptions] = useState(editing && editing.options ? editing.options.join('\n') : '');
  const [required, setRequired] = useState(editing ? !!editing.required : false);
  const [description, setDescription] = useState(editing ? (editing.description || '') : '');
  const [regexPattern, setRegexPattern] = useState(editing ? (editing.regex_pattern || '') : '');
  const [regexCue, setRegexCue] = useState(editing ? (editing.regex_cue || '') : '');

  // Auto-derive key from name until the user edits the key manually (create only).
  function onNameInput(v) {
    setDisplayName(v);
    if (!editing && !keyTouched) setKey(slugify(v));
  }

  const keyErr = !editing && key && !KEY_RE.test(key)
    ? 'Chave inválida: minúsculas, números e _, começando com letra.' : '';
  const optionList = options.split('\n').map(o => o.trim()).filter(Boolean);
  const optionsErr = type === 'list' && optionList.length === 0
    ? 'Liste ao menos uma opção (uma por linha).' : '';
  const canSave = !busy && displayName.trim() && (editing || (key && !keyErr)) && !optionsErr;

  function submit() {
    if (!canSave) return;
    const payload = editing
      ? {
          display_name: displayName.trim(), required: required ? 1 : 0,
          description: description.trim(),
          regex_pattern: regexPattern.trim() || null, regex_cue: regexCue.trim() || null,
          ...(type === 'list' ? { options: optionList } : {}),
        }
      : {
          attribute_key: key, display_name: displayName.trim(), type,
          applies_to: appliesTo, required: required ? 1 : 0, description: description.trim(),
          regex_pattern: regexPattern.trim() || null, regex_cue: regexCue.trim() || null,
          ...(type === 'list' ? { options: optionList } : {}),
        };
    onSubmit(payload);
  }

  const showRegex = type === 'text' || type === 'link';

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${editing ? `Editar atributo "${editing.attribute_key}"` : 'Novo atributo'}
      </div>
      <div class="flex flex-col gap-3">
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
            placeholder="Ex: Plano contratado" value=${displayName}
            onInput=${(e) => onNameInput(e.target.value)} />
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Chave (identidade — não muda depois)</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono ${editing ? 'opacity-60' : ''}"
            type="text" placeholder="plano_contratado" value=${key}
            disabled=${!!editing}
            onInput=${(e) => { setKey(e.target.value.toLowerCase()); setKeyTouched(true); }} />
          ${keyErr ? html`<div class="text-[12px] text-red-500 mt-1">${keyErr}</div>` : null}
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Tipo</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px] ${editing ? 'opacity-60' : ''}"
            value=${type} disabled=${!!editing}
            onChange=${(e) => setType(e.target.value)}>
            ${TYPES.map(([v, lbl]) => html`<option key=${v} value=${v}>${lbl}</option>`)}
          </select>
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Escopo (não muda depois)</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px] ${editing ? 'opacity-60' : ''}"
            value=${appliesTo} disabled=${!!editing}
            onChange=${(e) => setAppliesTo(e.target.value)}>
            ${SCOPES.map(([v, lbl]) => html`<option key=${v} value=${v}>${lbl}</option>`)}
          </select>
          <div class="text-[11px] text-wa-secondary mt-1">
            ${appliesTo === 'conversation'
              ? 'Por conversa — preenchido manualmente em "Dados desta conversa".'
              : 'Por contato — preenchido pelo operador e pela IA.'}
          </div>
        </div>
        ${type === 'list' ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Opções (uma por linha)</label>
            <textarea class="wa-field w-full px-3 py-2 rounded-md text-[14px] min-h-[80px] resize-y"
              placeholder=${'free\npremium\nenterprise'} value=${options}
              onInput=${(e) => setOptions(e.target.value)}></textarea>
            ${optionsErr ? html`<div class="text-[12px] text-red-500 mt-1">${optionsErr}</div>` : null}
          </div>
        ` : null}
        ${showRegex ? html`
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Regex (opcional)</label>
              <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono" type="text"
                placeholder="^\\d{11}$" value=${regexPattern} onInput=${(e) => setRegexPattern(e.target.value)} />
            </div>
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Dica do formato</label>
              <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
                placeholder="11 dígitos" value=${regexCue} onInput=${(e) => setRegexCue(e.target.value)} />
            </div>
          </div>
        ` : null}
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Descrição (opcional)</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
            placeholder="Texto de ajuda exibido no painel" value=${description}
            onInput=${(e) => setDescription(e.target.value)} />
        </div>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked=${required} onChange=${(e) => setRequired(e.target.checked)} />
          <span class="text-[14px] text-wa-text">Obrigatório</span>
        </label>
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

export default function CustomAttributesManager() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    const res = await getCustomAttributes();   // todos os escopos (contato + conversa)
    if (res && res.ok) setItems(res.data || []);
    else setError((res && res.error) || 'Falha ao carregar.');
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleCreate(data) {
    setBusy(true); setError('');
    const res = await createCustomAttribute(data);
    setBusy(false);
    if (res && res.ok) { setCreating(false); notifyChanged(); load(); }
    else setError((res && res.error) || 'Falha ao criar.');
  }

  async function handleUpdate(data) {
    setBusy(true); setError('');
    const res = await updateCustomAttribute(editing.id, data);
    setBusy(false);
    if (res && res.ok) { setEditing(null); notifyChanged(); load(); }
    else setError((res && res.error) || 'Falha ao salvar.');
  }

  async function handleDelete(row) {
    if (!confirm(`Excluir o atributo "${row.attribute_key}"? Os valores já preenchidos são preservados.`)) return;
    const res = await deleteCustomAttribute(row.id);
    if (res && res.ok) { notifyChanged(); load(); }
    else setError((res && res.error) || 'Falha ao excluir.');
  }

  const typeLabel = (t) => (TYPES.find(([v]) => v === t) || [t, t])[1];
  const scopeLabel = (s) => (SCOPES.find(([v]) => v === s) || [s, s])[1];

  return html`
    <div>
      <div class="flex items-center justify-between mb-4">
        <p class="text-[13px] text-wa-secondary">
          Campos personalizados por <span class="font-medium">contato</span> ou por <span class="font-medium">conversa</span>. Aparecem no painel de dados; os de contato a IA também pode preencher.
        </p>
        ${!creating && !editing ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}>+ Novo</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${AttributeForm} onSubmit=${handleCreate} onCancel=${() => setCreating(false)} busy=${busy} />` : null}
      ${editing ? html`<${AttributeForm} editing=${editing} onSubmit=${handleUpdate} onCancel=${() => setEditing(null)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && items.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhum atributo personalizado ainda. Clique em <span class="font-medium">+ Novo</span> para criar.
        </div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${items.map(row => html`
          <div key=${row.id} class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3">
            <div class="flex-1 min-w-0">
              <div class="text-[14px] text-wa-text font-medium flex items-center gap-2 flex-wrap">
                <span>${row.display_name}</span>
                ${row.required ? html`<span class="text-red-500">*</span>` : null}
                <span class="text-[11px] font-normal px-1.5 py-0.5 rounded bg-wa-hover text-wa-secondary">
                  ${scopeLabel(row.applies_to)}
                </span>
              </div>
              <div class="text-[12px] text-wa-secondary mt-0.5">
                <span class="font-mono">${row.attribute_key}</span> · ${typeLabel(row.type)}
                ${row.type === 'list' && row.options ? html` · ${row.options.join(', ')}` : null}
              </div>
            </div>
            <div class="flex gap-1 shrink-0">
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => { setEditing(row); setCreating(false); setError(''); }}>Editar</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors"
                onClick=${() => handleDelete(row)}>Excluir</button>
            </div>
          </div>
        `)}
      </div>
    </div>
  `;
}
