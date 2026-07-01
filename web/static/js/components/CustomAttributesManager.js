// Custom attributes admin screen (plano 05) — full-page (FQ6).
// Define contact AND conversation custom attributes: key, type, options…
// attribute_key + applies_to are identity: settable on create, immutable after.
// Chatwoot-style: abas (Atendimentos | Contato) + tabela.
// Dispatches `whatsbot:custom-attributes-changed` so open contact/conversation panels reload.

import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  getCustomAttributes,
  createCustomAttribute,
  updateCustomAttribute,
  deleteCustomAttribute,
} from '../services/api.js';
import { useDeepLink, entityPath } from '../hooks/useDeepLink.js';

const html = htm.bind(h);

const TYPES = [
  ['text', 'Texto'],
  ['number', 'Número'],
  ['date', 'Data'],
  ['list', 'Lista (opções)'],
  ['checkbox', 'Sim/Não'],
  ['link', 'Link'],
];

const KEY_RE = /^[a-z][a-z0-9_]*$/;

// The two scopes a custom attribute can apply to (P54). applies_to is identity:
// settable on create, immutable on update (same as attribute_key/type).
const SCOPES = [
  ['contact', 'Contato'],
  ['conversation', 'Atendimento'],
];

// Tab order mirrors the Chatwoot layout (Atendimentos first, then Contato).
const SCOPE_TABS = [
  ['conversation', 'Atendimentos'],
  ['contact', 'Contato'],
];

const PencilIcon = html`
  <svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
    <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
  </svg>`;

const TrashIcon = html`
  <svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
    <path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5 0a1 1 0 10-2 0v6a1 1 0 102 0V8z" clip-rule="evenodd" />
  </svg>`;

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

// Modal de confirmação in-app (substitui o confirm() nativo do navegador).
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

function AttributeForm({ editing, defaultScope, onSubmit, onCancel, busy }) {
  const [displayName, setDisplayName] = useState(editing ? editing.display_name : '');
  const [key, setKey] = useState(editing ? editing.attribute_key : '');
  const [keyTouched, setKeyTouched] = useState(!!editing);
  const [type, setType] = useState(editing ? editing.type : 'text');
  const [appliesTo, setAppliesTo] = useState(editing ? editing.applies_to : (defaultScope || 'contact'));
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
          <label class="block text-[12px] text-wa-secondary mb-1">Aplica-se a (identidade — não muda depois)</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px] ${editing ? 'opacity-60' : ''}"
            value=${appliesTo} disabled=${!!editing}
            onChange=${(e) => setAppliesTo(e.target.value)}>
            ${SCOPES.map(([v, lbl]) => html`<option key=${v} value=${v}>${lbl}</option>`)}
          </select>
          <div class="text-[12px] text-wa-secondary mt-1">
            ${appliesTo === 'conversation'
              ? 'Aparece no painel "Informações do atendimento" — um valor por atendimento.'
              : 'Aparece no painel "Informações do contato" — um valor por contato.'}
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
          <span class="text-[14px] text-wa-text">Obrigatório preencher</span>
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

export default function CustomAttributesManager({ initialEntity }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null); // row pendente de exclusão
  // Aba de escopo (Chatwoot-style) espelha /custom-attributes/<scope>[/<key>].
  const [tab, setTab] = useState(() => initialEntity?.sub || 'conversation');

  async function load() {
    setLoading(true);
    // Both scopes (P54): contact + conversation, filtered per active tab below.
    const [cRes, vRes] = await Promise.all([
      getCustomAttributes('contact'),
      getCustomAttributes('conversation'),
    ]);
    if ((cRes && cRes.ok) || (vRes && vRes.ok)) {
      // Esta tela administra APENAS os atributos criados aqui (is_system=0). Os
      // is_system=1 são rótulos registrados por plugins (o Atendimentos espelha seus
      // campos de "Resolver atendimento" como atributos de atendimento) — eles vivem e são
      // editados na config do próprio plugin, não nesta lista. O backend já bloqueia
      // editar/excluir is_system, então aqui só os escondemos da gestão.
      const onlyUserCreated = (rows) => (rows || []).filter(a => !a.is_system);
      setItems([
        ...onlyUserCreated(cRes && cRes.ok && cRes.data),
        ...onlyUserCreated(vRes && vRes.ok && vRes.data),
      ]);
      setError('');
    } else {
      setError((cRes && cRes.error) || (vRes && vRes.error) || 'Falha ao carregar.');
    }
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  // Aba de escopo segue a URL (deep-link / back-forward).
  useEffect(() => {
    if (initialEntity?.sub) setTab(initialEntity.sub);
  }, [initialEntity]);

  // Deep-link /custom-attributes/<scope>/<key>: reabre o atributo (escopo + chave).
  const pushUrl = useDeepLink({
    tab: 'custom-attributes',
    resolve: initialEntity ? { sub: initialEntity.sub, id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel || sel.id == null) { setEditing(null); return; }
      const a = items.find(r => (r.applies_to || 'contact') === sel.sub && r.attribute_key === sel.id);
      if (a) { setEditing(a); setCreating(false); }
    },
  });
  // Reflete o atributo aberto na URL (escopo do próprio atributo + chave); ao
  // fechar, volta ao escopo da aba atual. Pula o 1º render p/ não atropelar o
  // deep-link de entrada.
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return; }
    pushUrl(editing
      ? { sub: editing.applies_to || 'contact', id: editing.attribute_key }
      : { sub: tab });
  }, [editing]);

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

  async function handleDelete() {
    if (!confirmDelete) return;
    setBusy(true);
    const res = await deleteCustomAttribute(confirmDelete.id);
    setBusy(false);
    if (res && res.ok) { setConfirmDelete(null); notifyChanged(); load(); }
    else { setConfirmDelete(null); setError((res && res.error) || 'Falha ao excluir.'); }
  }

  const typeLabel = (t) => (TYPES.find(([v]) => v === t) || [t, t])[1];
  const tabLabel = (SCOPE_TABS.find(([v]) => v === tab) || ['', ''])[1];
  const tabRows = items.filter(r => (r.applies_to || 'contact') === tab);

  return html`
    <div>
      <div class="flex items-center justify-between mb-4">
        <p class="text-[13px] text-wa-secondary">
          Campos personalizados de contato e de atendimento. Aparecem nos painéis de informações e a IA pode preenchê-los.
        </p>
        ${!creating && !editing ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}>+ Novo</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      <div class="flex items-center gap-6 border-b border-wa-border mb-4">
        ${SCOPE_TABS.map(([scope, title]) => html`
          <button key=${scope}
            class="relative pb-2 text-[14px] transition-colors ${tab === scope ? 'text-wa-teal font-medium' : 'text-wa-secondary hover:text-wa-text'}"
            onClick=${() => {
              setTab(scope); setEditing(null); setError('');
              const p = entityPath('custom-attributes', { sub: scope });
              if (window.location.pathname !== p) {
                history.pushState(null, '', p);
                window.dispatchEvent(new PopStateEvent('popstate'));
              }
            }}>
            ${title}
            ${tab === scope ? html`<span class="absolute left-0 right-0 -bottom-px h-0.5 bg-wa-teal rounded-full"></span>` : null}
          </button>
        `)}
      </div>

      ${creating ? html`<${AttributeForm} defaultScope=${tab} onSubmit=${handleCreate} onCancel=${() => setCreating(false)} busy=${busy} />` : null}
      ${editing ? html`<${AttributeForm} editing=${editing} onSubmit=${handleUpdate} onCancel=${() => setEditing(null)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && tabRows.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhum atributo de ${tabLabel.toLowerCase()} ainda. Clique em <span class="font-medium">+ Novo</span> para criar.
        </div>
      ` : null}

      ${!loading && tabRows.length > 0 ? html`
        <div class="overflow-x-auto border border-wa-border rounded-lg">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-wa-border text-[12px] text-wa-secondary uppercase tracking-wide">
                <th class="px-4 py-3 font-medium">Nome</th>
                <th class="px-4 py-3 font-medium">Descrição</th>
                <th class="px-4 py-3 font-medium">Tipo</th>
                <th class="px-4 py-3 font-medium">Chave</th>
                <th class="px-4 py-3 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              ${tabRows.map(row => html`
                <tr key=${row.id} class="border-b border-wa-border last:border-0 hover:bg-wa-hover transition-colors">
                  <td class="px-4 py-3 text-[14px] text-wa-text">
                    ${row.display_name}${row.required ? html`<span class="text-red-500"> *</span>` : null}
                  </td>
                  <td class="px-4 py-3 text-[14px] text-wa-secondary">${row.description || '—'}</td>
                  <td class="px-4 py-3 text-[14px] text-wa-text">
                    ${typeLabel(row.type)}${row.type === 'list' && row.options ? html` · ${row.options.join(', ')}` : null}
                  </td>
                  <td class="px-4 py-3 text-[13px] text-wa-secondary font-mono">${row.attribute_key}</td>
                  <td class="px-4 py-3">
                    <div class="flex items-center gap-1 justify-end">
                      <button title="Editar" aria-label="Editar"
                        class="p-1.5 rounded-md text-wa-secondary hover:text-wa-text hover:bg-wa-hover transition-colors"
                        onClick=${() => { setEditing(row); setCreating(false); setError(''); }}>${PencilIcon}</button>
                      <button title="Excluir" aria-label="Excluir"
                        class="p-1.5 rounded-md text-red-500 hover:bg-red-500/10 transition-colors"
                        onClick=${() => setConfirmDelete(row)}>${TrashIcon}</button>
                    </div>
                  </td>
                </tr>
              `)}
            </tbody>
          </table>
        </div>
      ` : null}

      ${confirmDelete ? html`
        <${ConfirmModal}
          title="Excluir atributo"
          message=${html`Excluir o atributo "${confirmDelete.display_name}"? Os valores já preenchidos são preservados.`}
          confirmLabel="Excluir"
          danger
          busy=${busy}
          onConfirm=${handleDelete}
          onClose=${() => setConfirmDelete(null)} />
      ` : null}
    </div>
  `;
}
