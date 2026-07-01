// User management screen (RBAC — plano 03). Full-page.
// Lists users (name, email, roles as chips, active/inactive, last login) and
// allows creating, editing (name, active, roles), resetting passwords and
// deleting. The backend refuses to remove/deactivate the last active admin
// (409) — we surface its message as-is.

import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import htm from 'htm';
import PermissionPicker from './PermissionPicker.js';
import RolesManager from './RolesManager.js';
import { useDeepLink, entityPath, basePath } from '../hooks/useDeepLink.js';
import {
  getUsers,
  getRoles,
  createUser,
  updateUser,
  resetUserPassword,
  deleteUser,
} from '../services/api.js';

const html = htm.bind(h);

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString('pt-BR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch (e) {
    return iso;
  }
}

function RoleChips({ roleKeys, roleDefs }) {
  const labelFor = (k) => {
    const def = (roleDefs || []).find(r => r.key === k);
    return def ? (def.label || def.name || k) : k;
  };
  if (!roleKeys || roleKeys.length === 0) {
    return html`<span class="text-[12px] text-wa-secondary">sem grupo</span>`;
  }
  return html`
    <div class="flex flex-wrap gap-1">
      ${roleKeys.map(k => html`
        <span key=${k} class="px-2 py-0.5 rounded-full text-[11px] bg-wa-teal/10 text-wa-teal">
          ${labelFor(k)}
        </span>
      `)}
    </div>
  `;
}

// ── Create / edit form ─────────────────────────────────────────────
function UserForm({ editing, roleDefs, permCatalog, inboxes = [], onSubmit, onCancel, busy }) {
  const [email, setEmail] = useState(editing ? editing.email : '');
  const [name, setName] = useState(editing ? (editing.name || '') : '');
  const [password, setPassword] = useState('');
  const [isActive, setIsActive] = useState(editing ? !!editing.is_active : true);
  const [roles, setRoles] = useState(editing ? [...(editing.roles || [])] : []);
  // Custom mode: the user gets an explicit permission set, replacing roles.
  const [custom, setCustom] = useState(editing ? !!editing.custom_permissions : false);
  const [perms, setPerms] = useState(editing ? [...(editing.permissions || [])] : []);
  // Caixas de entrada (inbox_members) das quais este usuário é membro.
  const [inboxIds, setInboxIds] = useState(editing ? [...(editing.inbox_ids || [])] : []);

  function toggleRole(key) {
    setRoles(prev => prev.includes(key) ? prev.filter(r => r !== key) : [...prev, key]);
  }
  function togglePerm(key) {
    setPerms(prev => prev.includes(key) ? prev.filter(p => p !== key) : [...prev, key]);
  }
  function toggleInbox(id) {
    setInboxIds(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]);
  }

  const emailErr = !editing && email && !EMAIL_RE.test(email.trim())
    ? 'Email inválido.' : '';
  const passwordErr = !editing && password && password.length < 8
    ? 'A senha deve ter ao menos 8 caracteres.' : '';
  const rolesErr = (!custom && roles.length === 0) ? 'Selecione ao menos um grupo de permissão.' : '';
  const permsErr = (custom && perms.length === 0) ? 'Selecione ao menos uma permissão.' : '';

  const canSave = !busy
    && (editing || (email.trim() && !emailErr))
    && (editing || (password && !passwordErr))
    && (custom ? perms.length > 0 : roles.length > 0);

  function submit() {
    if (!canSave) return;
    const assignment = custom
      ? { custom_permissions: true, permissions: perms }
      : { custom_permissions: false, roles };
    if (editing) {
      onSubmit({ name: name.trim(), is_active: isActive, ...assignment, inbox_ids: inboxIds });
    } else {
      onSubmit({ email: email.trim().toLowerCase(), name: name.trim(), password, ...assignment, inbox_ids: inboxIds });
    }
  }

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">
        ${editing ? `Editar usuário "${editing.email}"` : 'Novo usuário'}
      </div>
      <div class="flex flex-col gap-3">
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Email</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] ${editing ? 'opacity-60' : ''}"
            type="email" placeholder="usuario@empresa.com" value=${email}
            disabled=${!!editing}
            onInput=${(e) => setEmail(e.target.value)} />
          ${emailErr ? html`<div class="text-[12px] text-red-500 mt-1">${emailErr}</div>` : null}
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" placeholder="Nome do atendente" value=${name}
            onInput=${(e) => setName(e.target.value)} />
        </div>
        ${!editing ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Senha</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="password" placeholder="Mínimo 8 caracteres" value=${password}
              onInput=${(e) => setPassword(e.target.value)} />
            ${passwordErr ? html`<div class="text-[12px] text-red-500 mt-1">${passwordErr}</div>` : null}
          </div>
        ` : null}
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked=${custom} onChange=${(e) => setCustom(e.target.checked)} />
          <span class="text-[14px] text-wa-text">Permissões personalizadas</span>
          <span class="text-[12px] text-wa-secondary">(ignora grupos de permissão — conjunto sob medida)</span>
        </label>

        ${!custom ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Grupos de permissão</label>
            <div class="flex flex-col gap-1.5">
              ${(roleDefs || []).map(r => html`
                <label key=${r.key} class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked=${roles.includes(r.key)}
                    onChange=${() => toggleRole(r.key)} />
                  <span class="text-[14px] text-wa-text">${r.label || r.name || r.key}</span>
                  <span class="text-[12px] text-wa-secondary font-mono">${r.key}</span>
                </label>
              `)}
            </div>
            ${rolesErr ? html`<div class="text-[12px] text-red-500 mt-1">${rolesErr}</div>` : null}
          </div>
        ` : html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Permissões deste usuário</label>
            <${PermissionPicker} catalog=${permCatalog} selected=${perms} onToggle=${togglePerm} />
            ${permsErr ? html`<div class="text-[12px] text-red-500 mt-1">${permsErr}</div>` : null}
          </div>
        `}
        ${inboxes.length ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Caixas de entrada</label>
            <p class="text-[12px] text-wa-secondary mb-1.5">
              As caixas que este usuário vê e atende — restringe leitura e envio às caixas marcadas.
              Qualquer papel com a permissão "Ler atendimentos de qualquer inbox" (ex.: Administrador)
              ignora esta seleção e acessa todas.
            </p>
            <div class="flex flex-col gap-1.5">
              ${inboxes.map(ib => html`
                <label key=${ib.id} class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked=${inboxIds.includes(ib.id)}
                    onChange=${() => toggleInbox(ib.id)} />
                  <span class="text-[14px] text-wa-text">${ib.name || ('Caixa #' + ib.id)}</span>
                </label>
              `)}
            </div>
          </div>
        ` : null}
        ${editing ? html`
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked=${isActive} onChange=${(e) => setIsActive(e.target.checked)} />
            <span class="text-[14px] text-wa-text">Ativo</span>
          </label>
        ` : null}
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

// ── Reset-password modal ────────────────────────────────────────────
function PasswordModal({ user, onSubmit, onCancel, busy }) {
  const [password, setPassword] = useState('');
  const err = password && password.length < 8 ? 'A senha deve ter ao menos 8 caracteres.' : '';
  const canSave = !busy && password && !err;
  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onCancel}>
      <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-sm" onClick=${(e) => e.stopPropagation()}>
        <div class="text-[15px] font-medium text-wa-text mb-1">Resetar senha</div>
        <div class="text-[13px] text-wa-secondary mb-3 break-words">${user.email}</div>
        <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
          type="password" placeholder="Nova senha (mín. 8 caracteres)" value=${password}
          autofocus onInput=${(e) => setPassword(e.target.value)} />
        ${err ? html`<div class="text-[12px] text-red-500 mt-1">${err}</div>` : null}
        <div class="flex gap-2 justify-end mt-4">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${() => canSave && onSubmit(password)} disabled=${!canSave}>
            ${busy ? 'Salvando…' : 'Salvar'}</button>
        </div>
      </div>
    </div>
  `;
}

const SUBTABS = [
  { id: 'users', label: 'Usuários' },
  { id: 'roles', label: 'Grupos de permissão' },
];

export default function UsersManager({ initialEntity }) {
  // Sub-view (Usuários | Papéis) espelha a URL: /users[/{id}] vs /users/roles[/{key}].
  const [view, setView] = useState(() => (initialEntity && initialEntity.sub === 'roles' ? 'roles' : 'users'));
  const [users, setUsers] = useState([]);
  const [roleDefs, setRoleDefs] = useState([]);
  const [permCatalog, setPermCatalog] = useState([]);
  const [inboxes, setInboxes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [pwUser, setPwUser] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    const [uRes, rRes] = await Promise.all([getUsers(), getRoles()]);
    if (rRes && rRes.ok) {
      setRoleDefs((rRes.data && rRes.data.roles) || []);
      setPermCatalog((rRes.data && rRes.data.permissions) || []);
      setInboxes((rRes.data && rRes.data.inboxes) || []);
    }
    if (uRes && uRes.ok) setUsers((uRes.data && uRes.data.users) || []);
    else setError((uRes && uRes.error) || 'Falha ao carregar usuários.');
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  // Sub-view segue a URL (deep-link / back-forward).
  useEffect(() => {
    setView(initialEntity && initialEntity.sub === 'roles' ? 'roles' : 'users');
  }, [initialEntity]);

  // Deep-link /users/<id> (sub-view Usuários). RolesManager cuida de /users/roles/<key>.
  const pushUrl = useDeepLink({
    tab: 'users',
    resolve: initialEntity && !initialEntity.sub ? { id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel || sel.id == null) { setEditing(null); return; }
      const u = users.find(x => String(x.id) === String(sel.id));
      if (u) { setEditing(u); setCreating(false); }
    },
  });
  // Reflete o usuário aberto na URL — só na view Usuários (a view Papéis tem URL
  // própria, empurrada no clique da sub-aba; aqui evitamos sobrescrevê-la).
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return; }
    if (view === 'users') pushUrl(editing ? { id: editing.id } : null);
  }, [editing]);

  async function handleCreate(data) {
    setBusy(true); setError('');
    const res = await createUser(data);
    setBusy(false);
    if (res && res.ok) { setCreating(false); load(); }
    else setError((res && res.error) || 'Falha ao criar usuário.');
  }

  async function handleUpdate(data) {
    setBusy(true); setError('');
    const res = await updateUser(editing.id, data);
    setBusy(false);
    if (res && res.ok) { setEditing(null); load(); }
    else setError((res && res.error) || 'Falha ao salvar.');
  }

  async function handleResetPassword(password) {
    setBusy(true); setError('');
    const res = await resetUserPassword(pwUser.id, password);
    setBusy(false);
    if (res && res.ok) { setPwUser(null); }
    else setError((res && res.error) || 'Falha ao resetar a senha.');
  }

  async function handleDelete(row) {
    if (!confirm(`Excluir o usuário "${row.email}"? Esta ação não pode ser desfeita.`)) return;
    const res = await deleteUser(row.id);
    if (res && res.ok) load();
    else setError((res && res.error) || 'Falha ao excluir o usuário.');
  }

  return html`
    <div>
      <div class="flex gap-1 border-b border-wa-border mb-4 overflow-x-auto">
        ${SUBTABS.map(t => html`
          <button key=${t.id}
            class="px-4 py-2 text-[14px] -mb-px border-b-2 transition-colors whitespace-nowrap ${view === t.id
              ? 'border-wa-teal text-wa-teal font-medium'
              : 'border-transparent text-wa-secondary hover:text-wa-text'}"
            onClick=${() => {
              setView(t.id); setEditing(null); setCreating(false); setError('');
              const p = t.id === 'roles' ? entityPath('users', { sub: 'roles' }) : basePath('users');
              if (window.location.pathname !== p) {
                history.pushState(null, '', p);
                window.dispatchEvent(new PopStateEvent('popstate'));
              }
            }}>${t.label}</button>
        `)}
      </div>

      ${view === 'roles' ? html`<${RolesManager} initialEntity=${initialEntity} />` : html`
      <div>
      <div class="flex items-center justify-between mb-4">
        <p class="text-[13px] text-wa-secondary">
          Usuários do painel e seus grupos de permissão. Cada grupo concede um conjunto de permissões.
        </p>
        ${!creating && !editing ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}>+ Novo usuário</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${UserForm} roleDefs=${roleDefs} permCatalog=${permCatalog} inboxes=${inboxes} onSubmit=${handleCreate} onCancel=${() => setCreating(false)} busy=${busy} />` : null}
      ${editing ? html`<${UserForm} editing=${editing} roleDefs=${roleDefs} permCatalog=${permCatalog} inboxes=${inboxes} onSubmit=${handleUpdate} onCancel=${() => setEditing(null)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && users.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhum usuário cadastrado ainda. Clique em <span class="font-medium">+ Novo usuário</span> para criar.
        </div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${users.map(row => html`
          <div key=${row.id} class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3 flex-wrap">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-[14px] text-wa-text font-medium truncate">${row.name || row.email}</span>
                ${row.is_admin ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-teal/10 text-wa-teal">admin</span>` : null}
                ${row.custom_permissions ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-amber-500/10 text-amber-600">custom</span>` : null}
                ${row.is_active
                  ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-500/10 text-green-600">Ativo</span>`
                  : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Inativo</span>`}
              </div>
              <div class="text-[12px] text-wa-secondary mt-0.5 break-words">${row.email}</div>
              <div class="mt-1.5">
                ${row.custom_permissions
                  ? html`<span class="text-[12px] text-wa-secondary">${(row.permissions || []).length} permissão(ões) personalizada(s)</span>`
                  : html`<${RoleChips} roleKeys=${row.roles} roleDefs=${roleDefs} />`}
              </div>
              <div class="text-[11px] text-wa-secondary mt-1">
                Último acesso: ${fmtDate(row.last_login_at)}
              </div>
            </div>
            <div class="flex gap-1 shrink-0 flex-wrap justify-end">
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => { setEditing(row); setCreating(false); setError(''); }}>Editar</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
                onClick=${() => { setPwUser(row); setError(''); }}>Senha</button>
              <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors"
                onClick=${() => handleDelete(row)}>Excluir</button>
            </div>
          </div>
        `)}
      </div>

      ${pwUser ? html`<${PasswordModal} user=${pwUser} onSubmit=${handleResetPassword} onCancel=${() => setPwUser(null)} busy=${busy} />` : null}
      </div>
      `}
    </div>
  `;
}
