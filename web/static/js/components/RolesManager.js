// Role editor screen (RBAC). Sub-view of the Users area ("Papéis" tab).
// Lists every role with its permissions and lets an admin:
//   • edit the permissions of gestor/atendente (system) and custom roles,
//   • restore a system role to its seeded defaults,
//   • create new custom roles and delete unused ones.
// The `admin` role is locked (it is a short-circuit that always has every
// permission). The backend enforces the same rules and refuses to delete a role
// still assigned to a user (409) — we surface its message as-is.

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import PermissionPicker from './PermissionPicker.js';
import {
  getRoles, createRole, updateRole, deleteRole, resetRole,
} from '../services/api.js';

const html = htm.bind(h);

const KEY_RE = /^[a-z][a-z0-9_]{0,31}$/;
const RESERVED = ['admin', 'gestor', 'atendente'];

// ── New-role form ───────────────────────────────────────────────────
function NewRoleForm({ catalog, onSubmit, onCancel, busy }) {
  const [key, setKey] = useState('');
  const [name, setName] = useState('');
  const [sel, setSel] = useState([]);

  const keyErr = key && !KEY_RE.test(key)
    ? 'Use a-z, 0-9 e _ (começando por letra).'
    : (RESERVED.includes(key) ? 'Essa chave é reservada.' : '');
  const canSave = !busy && key && !keyErr && name.trim();

  const toggle = (k) => setSel(p => p.includes(k) ? p.filter(x => x !== k) : [...p, k]);

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">Novo papel</div>
      <div class="flex flex-col gap-3">
        <div class="flex gap-3 flex-wrap">
          <div class="flex-1 min-w-[160px]">
            <label class="block text-[12px] text-wa-secondary mb-1">Chave (identificador)</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] font-mono"
              type="text" placeholder="ex: supervisor" value=${key}
              onInput=${(e) => setKey(e.target.value.trim().toLowerCase())} />
            ${keyErr ? html`<div class="text-[12px] text-red-500 mt-1">${keyErr}</div>` : null}
          </div>
          <div class="flex-1 min-w-[160px]">
            <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" placeholder="ex: Supervisor" value=${name}
              onInput=${(e) => setName(e.target.value)} />
          </div>
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Permissões</label>
          <${PermissionPicker} catalog=${catalog} selected=${sel} onToggle=${toggle} />
        </div>
        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${() => canSave && onSubmit({ key, name: name.trim(), permission_keys: sel })}
            disabled=${!canSave}>${busy ? 'Salvando…' : 'Criar papel'}</button>
        </div>
      </div>
    </div>
  `;
}

// ── Single role card (with inline editor) ───────────────────────────
function RoleCard({ role, catalog, onSave, onReset, onDelete, busy }) {
  const isAdmin = role.key === 'admin';
  const isSystem = !!role.is_system;
  const [editing, setEditing] = useState(false);
  const [sel, setSel] = useState([...(role.permission_keys || [])]);
  const [name, setName] = useState(role.name || '');

  // Reset local state whenever we (re)open the editor or the role changes.
  function open() {
    setSel([...(role.permission_keys || [])]);
    setName(role.name || '');
    setEditing(true);
  }
  const toggle = (k) => setSel(p => p.includes(k) ? p.filter(x => x !== k) : [...p, k]);

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-3">
      <div class="flex items-start gap-3 flex-wrap">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-[14px] text-wa-text font-medium">${role.label || role.name || role.key}</span>
            <span class="text-[11px] text-wa-secondary font-mono">${role.key}</span>
            ${isSystem
              ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">sistema</span>`
              : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-teal/10 text-wa-teal">custom</span>`}
          </div>
          <div class="text-[12px] text-wa-secondary mt-0.5">
            ${isAdmin ? 'Todas as permissões (papel fixo)' : `${(role.permission_keys || []).length} permissão(ões)`}
          </div>
        </div>
        ${!isAdmin && !editing ? html`
          <div class="flex gap-1 shrink-0 flex-wrap justify-end">
            <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
              onClick=${open}>Editar permissões</button>
            ${isSystem ? html`
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => onReset(role)}>Restaurar padrão</button>
            ` : html`
              <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors"
                onClick=${() => onDelete(role)}>Excluir</button>
            `}
          </div>
        ` : null}
      </div>

      ${editing ? html`
        <div class="mt-3 border-t border-wa-border pt-3 flex flex-col gap-3">
          ${!isSystem ? html`
            <div>
              <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
              <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
                type="text" value=${name} onInput=${(e) => setName(e.target.value)} />
            </div>
          ` : null}
          <${PermissionPicker} catalog=${catalog} selected=${sel} onToggle=${toggle} />
          <div class="flex gap-2 justify-end">
            <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
              onClick=${() => setEditing(false)} disabled=${busy}>Cancelar</button>
            <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
              onClick=${async () => {
                const ok = await onSave(role, {
                  permission_keys: sel,
                  ...(isSystem ? {} : { name: name.trim() || role.name }),
                });
                if (ok) setEditing(false);
              }}
              disabled=${busy}>${busy ? 'Salvando…' : 'Salvar'}</button>
          </div>
        </div>
      ` : null}
    </div>
  `;
}

export default function RolesManager() {
  const [roles, setRoles] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true); setError('');
    const res = await getRoles();
    if (res && res.ok) {
      setRoles((res.data && res.data.roles) || []);
      setCatalog((res.data && res.data.permissions) || []);
    } else {
      setError((res && res.error) || 'Falha ao carregar papéis.');
    }
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleCreate(data) {
    setBusy(true); setError('');
    const res = await createRole(data);
    setBusy(false);
    if (res && res.ok) { setCreating(false); load(); }
    else setError((res && res.error) || 'Falha ao criar papel.');
  }

  async function handleSave(role, data) {
    setBusy(true); setError('');
    const res = await updateRole(role.id, data);
    setBusy(false);
    if (res && res.ok) { load(); return true; }
    setError((res && res.error) || 'Falha ao salvar papel.');
    return false;
  }

  async function handleReset(role) {
    if (!confirm(`Restaurar as permissões padrão do papel "${role.label || role.key}"?`)) return;
    setBusy(true); setError('');
    const res = await resetRole(role.id);
    setBusy(false);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao restaurar padrão.');
  }

  async function handleDelete(role) {
    if (!confirm(`Excluir o papel "${role.label || role.key}"? Esta ação não pode ser desfeita.`)) return;
    setBusy(true); setError('');
    const res = await deleteRole(role.id);
    setBusy(false);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao excluir o papel.');
  }

  return html`
    <div>
      <div class="flex items-center justify-between mb-4">
        <p class="text-[13px] text-wa-secondary">
          Papéis e as permissões que cada um concede. O papel <span class="font-mono">admin</span> tem
          todas as permissões e é fixo.
        </p>
        ${!creating ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}>+ Novo papel</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${NewRoleForm} catalog=${catalog} onSubmit=${handleCreate} onCancel=${() => setCreating(false)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      <div class="flex flex-col gap-2">
        ${roles.map(role => html`
          <${RoleCard} key=${role.id} role=${role} catalog=${catalog}
            onSave=${handleSave} onReset=${handleReset} onDelete=${handleDelete} busy=${busy} />
        `)}
      </div>
    </div>
  `;
}
