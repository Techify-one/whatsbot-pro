// Team editor screen (plano 153). Sub-view of the Users area ("Times" tab).
// Lists every team with its description and member count, and lets an admin:
//   • create/edit a team (nome + descrição + membros — um usuário pode estar em
//     vários times ao mesmo tempo, requisito #3),
//   • delete a team (a FK em atendimentos.team_id é ON DELETE SET NULL — apagar
//     um time só limpa a etiqueta das conversas, nunca quebra uma, D4).
// Gated by the dedicated `team.manage` permission.

import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import htm from 'htm';
import { getTeams, createTeam, updateTeam, deleteTeam, getUsers } from '../services/api.js';
import { useDeepLink } from '../hooks/useDeepLink.js';

const html = htm.bind(h);

// ── Modal de confirmação in-app (mesmo padrão de RolesManager) ──
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

// ── New-team form ───────────────────────────────────────────────────
function NewTeamForm({ users, onSubmit, onCancel, busy }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [memberIds, setMemberIds] = useState([]);

  const toggleMember = (id) => setMemberIds(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);
  const canSave = !busy && name.trim();

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">Novo time</div>
      <div class="flex flex-col gap-3">
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" placeholder="ex: Suporte" value=${name}
            onInput=${(e) => setName(e.target.value)} />
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Descrição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" placeholder="ex: Atendimento de dúvidas técnicas" value=${description}
            onInput=${(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Membros</label>
          <div class="flex flex-col gap-1.5 max-h-[220px] overflow-y-auto wa-scrollbar">
            ${(users || []).map(u => html`
              <label key=${u.id} class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked=${memberIds.includes(u.id)}
                  onChange=${() => toggleMember(u.id)} />
                <span class="text-[14px] text-wa-text">${u.name || u.email}</span>
              </label>
            `)}
          </div>
        </div>
        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${() => canSave && onSubmit({ name: name.trim(), description: description.trim(), member_user_ids: memberIds })}
            disabled=${!canSave}>${busy ? 'Salvando…' : 'Criar time'}</button>
        </div>
      </div>
    </div>
  `;
}

// ── Single team card (with inline editor) ───────────────────────────
function TeamCard({ team, users, onSave, onDelete, busy, editing, onOpen, onClose }) {
  const [name, setName] = useState(team.name || '');
  const [description, setDescription] = useState(team.description || '');
  const [memberIds, setMemberIds] = useState([...(team.member_user_ids || [])]);
  const [restrictVisibility, setRestrictVisibility] = useState(!!team.restrict_visibility);
  const [visibleToAssignee, setVisibleToAssignee] = useState(!!team.visible_to_assignee);

  useEffect(() => {
    if (editing) {
      setName(team.name || '');
      setDescription(team.description || '');
      setMemberIds([...(team.member_user_ids || [])]);
      setRestrictVisibility(!!team.restrict_visibility);
      setVisibleToAssignee(!!team.visible_to_assignee);
    }
  }, [editing]);

  const toggleMember = (id) => setMemberIds(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);
  const memberNames = (team.member_user_ids || [])
    .map(id => (users.find(u => u.id === id) || {}).name || (users.find(u => u.id === id) || {}).email)
    .filter(Boolean);

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-3">
      <div class="flex items-start gap-3 flex-wrap">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-[14px] text-wa-text font-medium">${team.name}</span>
            <span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">
              ${(team.member_user_ids || []).length} membro(s)
            </span>
            ${team.restrict_visibility ? html`
              <span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">🔒 Restrito</span>
            ` : null}
          </div>
          ${team.description ? html`<div class="text-[12px] text-wa-secondary mt-0.5">${team.description}</div>` : null}
          ${memberNames.length ? html`
            <div class="text-[12px] text-wa-secondary mt-1">${memberNames.join(', ')}</div>
          ` : null}
        </div>
        ${!editing ? html`
          <div class="flex gap-1 shrink-0 flex-wrap justify-end">
            <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
              onClick=${onOpen}>Editar</button>
            <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors"
              onClick=${() => onDelete(team)}>Excluir</button>
          </div>
        ` : null}
      </div>

      ${editing ? html`
        <div class="mt-3 border-t border-wa-border pt-3 flex flex-col gap-3">
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Nome</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" value=${name} onInput=${(e) => setName(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Descrição</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" value=${description} onInput=${(e) => setDescription(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Membros</label>
            <div class="flex flex-col gap-1.5 max-h-[220px] overflow-y-auto wa-scrollbar">
              ${(users || []).map(u => html`
                <label key=${u.id} class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked=${memberIds.includes(u.id)}
                    onChange=${() => toggleMember(u.id)} />
                  <span class="text-[14px] text-wa-text">${u.name || u.email}</span>
                </label>
              `)}
            </div>
          </div>
          <label class="flex items-start gap-2 cursor-pointer">
            <input type="checkbox" class="mt-0.5" checked=${restrictVisibility}
              onChange=${(e) => setRestrictVisibility(e.target.checked)} />
            <span class="text-[13px] text-wa-text">
              Restringir às conversas deste time
              <span class="block text-[12px] text-wa-secondary">
                Quando ligado, atendentes da mesma caixa que não são deste time deixam de ver
                essas conversas na lista (mas ainda podem abri-las por link direto).
              </span>
            </span>
          </label>
          ${restrictVisibility ? html`
            <label class="flex items-start gap-2 cursor-pointer ml-6">
              <input type="checkbox" class="mt-0.5" checked=${visibleToAssignee}
                onChange=${(e) => setVisibleToAssignee(e.target.checked)} />
              <span class="text-[13px] text-wa-text">
                Exceção: quem estiver atribuído à conversa continua vendo
                <span class="block text-[12px] text-wa-secondary">
                  Se um atendente de fora do time for o responsável por uma conversa deste
                  time, ele continua vendo essa conversa específica na lista — os demais de
                  fora do time continuam sem ver.
                </span>
              </span>
            </label>
          ` : null}
          <div class="flex gap-2 justify-end">
            <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
              onClick=${onClose} disabled=${busy}>Cancelar</button>
            <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
              onClick=${async () => {
                const ok = await onSave(team, {
                  name: name.trim() || team.name,
                  description: description.trim(),
                  member_user_ids: memberIds,
                  restrict_visibility: restrictVisibility,
                  visible_to_assignee: visibleToAssignee,
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

export default function TeamsManager({ initialEntity }) {
  const [teams, setTeams] = useState([]);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [deleting, setDeleting] = useState(null);

  async function load() {
    setLoading(true); setError('');
    const [tRes, uRes] = await Promise.all([getTeams(), getUsers()]);
    if (tRes && tRes.ok) setTeams((tRes.data && tRes.data.teams) || []);
    else setError((tRes && tRes.error) || 'Falha ao carregar times.');
    if (uRes && uRes.ok) setUsers((uRes.data && uRes.data.users) || []);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  // Deep-link /users/teams/<team_id> — mesmo padrão de /users/roles/<key>.
  const pushUrl = useDeepLink({
    tab: 'users',
    resolve: initialEntity && initialEntity.sub === 'teams'
      ? { sub: 'teams', id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel || sel.id == null) { setEditingId(null); return; }
      const t = teams.find(x => String(x.id) === String(sel.id));
      if (t) setEditingId(t.id);
    },
  });
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) { didMountRef.current = true; return; }
    pushUrl(editingId != null ? { sub: 'teams', id: editingId } : { sub: 'teams' });
  }, [editingId]);

  async function handleCreate(data) {
    setBusy(true); setError('');
    const res = await createTeam(data);
    setBusy(false);
    if (res && res.ok) { setCreating(false); load(); }
    else setError((res && res.error) || 'Falha ao criar time.');
  }

  async function handleSave(team, data) {
    setBusy(true); setError('');
    const res = await updateTeam(team.id, data);
    setBusy(false);
    if (res && res.ok) { load(); return true; }
    setError((res && res.error) || 'Falha ao salvar time.');
    return false;
  }

  async function handleDelete(team) {
    setBusy(true); setError('');
    const res = await deleteTeam(team.id);
    setBusy(false);
    if (res && res.ok) { setDeleting(null); load(); }
    else setError((res && res.error) || 'Falha ao excluir o time.');
  }

  return html`
    <div>
      <div class="flex items-center justify-between mb-4">
        <p class="text-[13px] text-wa-secondary">
          Times agrupam atendentes para filtrar e transferir conversas. Um usuário pode
          pertencer a vários times ao mesmo tempo.
        </p>
        ${!creating ? html`
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity shrink-0"
            onClick=${() => { setCreating(true); setError(''); }}>+ Novo time</button>
        ` : null}
      </div>

      ${error ? html`<div class="text-[13px] text-red-500 mb-3">${error}</div>` : null}

      ${creating ? html`<${NewTeamForm}
        users=${users} onSubmit=${handleCreate} onCancel=${() => setCreating(false)} busy=${busy} />` : null}

      ${loading ? html`<div class="text-[14px] text-wa-secondary">Carregando…</div>` : null}

      ${!loading && teams.length === 0 && !creating ? html`
        <div class="text-[14px] text-wa-secondary text-center py-8">
          Nenhum time cadastrado ainda. Clique em <span class="font-medium">+ Novo time</span> para criar.
        </div>
      ` : null}

      <div class="flex flex-col gap-2">
        ${teams.map(team => html`
          <${TeamCard} key=${team.id} team=${team} users=${users}
            onSave=${handleSave} onDelete=${(t) => { setDeleting(t); setError(''); }} busy=${busy}
            editing=${editingId === team.id}
            onOpen=${() => setEditingId(team.id)} onClose=${() => setEditingId(null)} />
        `)}
      </div>

      ${deleting ? html`<${ConfirmModal}
        title="Excluir time"
        message=${html`Excluir o time "${deleting.name}"? As conversas atribuídas a ele só perdem a etiqueta — nada é apagado. Esta ação não pode ser desfeita.`}
        confirmLabel="Excluir" danger=${true} busy=${busy}
        onConfirm=${() => handleDelete(deleting)}
        onClose=${() => { if (!busy) setDeleting(null); }} />` : null}
    </div>
  `;
}
