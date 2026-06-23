// Role editor screen (RBAC). Sub-view of the Users area ("Grupos de permissão" tab).
// (Internamente continua "role/roles" — só o rótulo visível mudou.)
// Lists every role with its permissions and lets an admin:
//   • edit the permissions of gestor/atendente (system) and custom roles,
//   • restore a system role to its seeded defaults,
//   • create new custom roles and delete unused ones.
// The `admin` role is locked (it is a short-circuit that always has every
// permission). The backend enforces the same rules and refuses to delete a role
// still assigned to a user (409) — we surface its message as-is.

import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import htm from 'htm';
import PermissionPicker from './PermissionPicker.js';
import {
  getRoles, createRole, updateRole, deleteRole, resetRole,
} from '../services/api.js';
import { useDeepLink } from '../hooks/useDeepLink.js';

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
      <div class="text-[14px] font-medium text-wa-text mb-3">Novo grupo de permissão</div>
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
            disabled=${!canSave}>${busy ? 'Salvando…' : 'Criar grupo de permissão'}</button>
        </div>
      </div>
    </div>
  `;
}

// ── Single role card (with inline editor) ───────────────────────────
function RoleCard({ role, catalog, onSave, onReset, onDelete, busy, editing, onOpen, onClose }) {
  const isAdmin = role.key === 'admin';
  const isSystem = !!role.is_system;
  const [sel, setSel] = useState([...(role.permission_keys || [])]);
  const [name, setName] = useState(role.name || '');

  // `editing` é controlado pelo pai (espelha a URL /users/roles/<key>). Ao abrir,
  // reinicializa os campos locais a partir do papel.
  useEffect(() => {
    if (editing) { setSel([...(role.permission_keys || [])]); setName(role.name || ''); }
  }, [editing]);
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
            ${isAdmin ? 'Todas as permissões (grupo fixo)' : `${(role.permission_keys || []).length} permissão(ões)`}
          </div>
        </div>
        ${!isAdmin && !editing ? html`
          <div class="flex gap-1 shrink-0 flex-wrap justify-end">
            <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
              onClick=${onOpen}>Editar permissões</button>
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
              onClick=${onClose} disabled=${busy}>Cancelar</button>
            <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
              onClick=${async () => {
                const ok = await onSave(role, {
                  permission_keys: sel,
                  ...(isSystem ? {} : { name: name.trim() || role.name }),
                });
                if (ok) onClose();
              }}
              disabled=${busy}>${busy ? 'Salvando…' : 'Salvar'}</button>
          </div>
        </div>
      ` : null}
    </div>
  `;
}

export default function RolesManager({ initialEntity }) {
  const [roles, setRoles] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  // Papel aberto no editor (controla os cards) — espelha /users/roles/<key>.
  const [editingKey, setEditingKey] = useState(null);

  async function load() {
    setLoading(true); setError('');
    const res = await getRoles();
    if (res && res.ok) {
      setRoles((res.data && res.data.roles) || []);
      setCatalog((res.data && res.data.permissions) || []);
    } else {
      setError((res && res.error) || 'Falha ao carregar grupos de permissão.');
    }
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  // Deep-link /users/roles/<role_key>: reabre o editor do papel da URL (exceto o
  // papel fixo admin, que não tem editor).
  const pushUrl = useDeepLink({
    tab: 'users',
    resolve: initialEntity && initialEntity.sub === 'roles'
      ? { sub: 'roles', id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel || sel.id == null) { setEditingKey(null); return; }
      const r = roles.find(x => x.key === sel.id);
      if (r && r.key !== 'admin') setEditingKey(sel.id);
    },
  });
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return; }
    pushUrl(editingKey ? { sub: 'roles', id: editingKey } : { sub: 'roles' });
  }, [editingKey]);

  async function handleCreate(data) {
    setBusy(true); setError('');
    const res = await createRole(data);
    setBusy(false);
    if (res && res.ok) { setCreating(false); load(); }
    else setError((res && res.error) || 'Falha ao criar grupo de permissão.');
  }

  async function handleSave(role, data) {
    setBusy(true); setError('');
    const res = await updateRole(role.id, data);
    setBusy(false);
    if (res && res.ok) { load(); return true; }
    setError((res && res.error) || 'Falha ao salvar grupo de permissão.');
    return false;
  }

  async function handleReset(role) {
    if (!confirm(`Restaurar as permissões padrão do grupo "${role.label || role.key}"?`)) return;
    setBusy(true); setError('');
    const res = await resetRole(role.id);
    setBusy(false);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao restaurar padrão.');
  }

  async function handleDelete(role) {
    if (!confirm(`Excluir o grupo de permissão "${role.label || role.key}"? Esta ação não pode ser desfeita.`)) return;
    setBusy(true); setError('');
    const res = await deleteRole(role.id);
    setBusy(false);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao excluir o grupo de permissão.');
  }

  return html`
    <div>
      <div class="flex items-center justify-between mb-4">
        <p class="text-[13px] text-wa-secondary">
          Grupos de permissão e as permissões que cada um concede. O grupo <span class="font-mono">admin</span> tem
          todas as permissões e é fixo.
        </p>
        ${!creating ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}>+ Novo grupo de permissão</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${NewRoleForm} catalog=${catalog} onSubmit=${handleCreate} onCancel=${() => setCreating(false)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      <div class="flex flex-col gap-2">
        ${roles.map(role => html`
          <${RoleCard} key=${role.id} role=${role} catalog=${catalog}
            onSave=${handleSave} onReset=${handleReset} onDelete=${handleDelete} busy=${busy}
            editing=${editingKey === role.key && role.key !== 'admin'}
            onOpen=${() => setEditingKey(role.key)} onClose=${() => setEditingKey(null)} />
        `)}
      </div>
    </div>
  `;
}
