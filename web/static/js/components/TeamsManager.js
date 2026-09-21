// Administração de Times (plano 166/U2). Superfície própria /teams, gated por
// team.manage. Create e edit usam o mesmo formulário e enviam uma única intenção
// completa; autorização e invariantes continuam pertencendo ao backend.

import { h } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { getTeams, createTeam, updateTeam, deleteTeam } from '../services/api.js';
import { useDeepLink } from '../hooks/useDeepLink.js';
import {
  ACCESS_MODES,
  ROUTING_MODES,
  accessLabel,
  filterTeamMembers,
  initialTeamForm,
  routingLabel,
  routingTargetLabel,
  teamFormPayload,
  validateTeamForm,
} from '../services/teamAdmin.js';

const html = htm.bind(h);
const sameId = (left, right) => left != null && right != null && String(left) === String(right);
const activeFlag = (record, field = 'is_active') => (
  record && (record[field] === true || record[field] === 1)
);

function ConfirmDialog({ title, description, reversible = false, confirmLabel, danger = false,
  busy = false, error = '', onConfirm, onClose }) {
  const cancelRef = useRef(null);
  useEffect(() => {
    cancelRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [busy, onClose]);

  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown=${(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="team-confirm-title"
        aria-describedby="team-confirm-description"
        class="bg-wa-panel border border-wa-border rounded-lg p-5 w-full max-w-md shadow-xl">
        <div id="team-confirm-title" class="text-[16px] font-semibold text-wa-text">${title}</div>
        <div id="team-confirm-description" class="text-[13px] text-wa-secondary mt-2 leading-relaxed">${description}</div>
        <div class="mt-3 rounded-md border border-wa-border bg-wa-bg px-3 py-2 text-[12px] ${reversible ? 'text-wa-secondary' : 'text-red-500'}">
          ${reversible
            ? 'Dá para desfazer: o time poderá ser reativado nesta tela.'
            : 'Exclusão definitiva: não é possível desfazer.'}
        </div>
        ${error ? html`<div role="alert" aria-live="assertive" class="mt-3 text-[12px] text-red-500">${error}</div>` : null}
        <div class="flex gap-2 justify-end mt-5">
          <button ref=${cancelRef} type="button"
            class="px-3 py-2 rounded-md border border-wa-border bg-wa-bg text-[14px] text-wa-text hover:bg-wa-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-wa-teal"
            onClick=${onClose} disabled=${busy}>Cancelar</button>
          <button type="button"
            class="px-4 py-2 rounded-md text-[14px] text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-wa-teal ${danger ? 'bg-red-600 hover:opacity-90' : 'bg-wa-teal hover:opacity-90'}"
            onClick=${onConfirm} disabled=${busy}>${busy ? 'Aguarde…' : confirmLabel}</button>
        </div>
      </div>
    </div>
  `;
}

function FieldError({ id, children }) {
  return children ? html`<div id=${id} class="text-[12px] text-red-500 mt-1">${children}</div>` : null;
}

function TeamForm({ team = null, users = [], aiAgents = [], busy = false, serverError = '',
  onSubmit, onCancel }) {
  const freshState = useMemo(() => initialTeamForm(team), [team && team.id]);
  const [form, setForm] = useState(freshState);
  const [memberQuery, setMemberQuery] = useState('');
  const [errors, setErrors] = useState({});
  const [discarding, setDiscarding] = useState(false);
  const nameRef = useRef(null);

  useEffect(() => {
    setForm(freshState);
    setMemberQuery('');
    setErrors({});
    requestAnimationFrame(() => nameRef.current?.focus());
  }, [freshState]);

  const set = (field, value) => setForm(previous => ({ ...previous, [field]: value }));
  const filteredUsers = filterTeamMembers(users, memberQuery);
  const selectedCount = form.memberIds.length;
  const memberUsers = users.filter(user => form.memberIds.some(id => sameId(id, user.id)));
  const defaultUserListed = memberUsers.some(user => sameId(user.id, form.defaultUserId));
  const defaultAgentListed = aiAgents.some(agent => agent.agent_key === form.defaultAgentKey);
  const initialPayload = teamFormPayload(freshState, { originalTeam: team });
  const dirty = JSON.stringify(teamFormPayload(form, { originalTeam: team }))
    !== JSON.stringify(initialPayload);

  const toggleMember = (user) => {
    const selected = form.memberIds.some(id => sameId(id, user.id));
    if (!selected && !activeFlag(user)) return;
    set('memberIds', selected
      ? form.memberIds.filter(id => !sameId(id, user.id))
      : [...form.memberIds, user.id]);
  };

  const submit = async (event) => {
    event.preventDefault();
    const nextErrors = validateTeamForm(form, { users, aiAgents, originalTeam: team });
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    await onSubmit(teamFormPayload(form, { originalTeam: team }));
  };

  const requestCancel = () => {
    if (dirty) setDiscarding(true);
    else onCancel();
  };

  return html`
    <form onSubmit=${submit} class="bg-wa-panel border border-wa-border rounded-lg overflow-hidden">
      <div class="px-4 py-3 border-b border-wa-border flex items-center justify-between gap-3">
        <div>
          <div class="text-[15px] font-semibold text-wa-text">${team ? `Editar ${team.name}` : 'Novo time'}</div>
          <div class="text-[12px] text-wa-secondary">Configuração salva em uma única operação.</div>
        </div>
        ${team && !activeFlag(team) ? html`<span class="px-2 py-1 rounded-full bg-wa-hover text-wa-secondary text-[11px]">Inativo</span>` : null}
      </div>

      <div class="p-4 grid grid-cols-1 xl:grid-cols-2 gap-5">
        <section class="space-y-3" aria-labelledby="team-basic-title">
          <div id="team-basic-title" class="text-[13px] font-semibold text-wa-text">Dados e membros</div>
          <div>
            <label for="team-name" class="block text-[12px] text-wa-secondary mb-1">Nome</label>
            <input ref=${nameRef} id="team-name" class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" value=${form.name} aria-invalid=${!!errors.name}
              aria-describedby=${errors.name ? 'team-name-error' : null}
              onInput=${(event) => set('name', event.target.value)}
              onBlur=${() => {
                if (!form.name.trim()) setErrors(previous => ({ ...previous, name: 'Informe um nome para o time.' }));
              }} />
            <${FieldError} id="team-name-error">${errors.name}</${FieldError}>
          </div>
          <div>
            <label for="team-description" class="block text-[12px] text-wa-secondary mb-1">Descrição</label>
            <textarea id="team-description" rows="2" class="wa-field w-full px-3 py-2 rounded-md text-[14px] resize-y"
              value=${form.description} onInput=${(event) => set('description', event.target.value)} />
          </div>
          <div>
            <div class="flex items-center justify-between gap-2 mb-1">
              <label for="team-member-search" class="text-[12px] text-wa-secondary">Membros</label>
              <span class="text-[11px] tabular-nums text-wa-secondary">${selectedCount} selecionado(s)</span>
            </div>
            <input id="team-member-search" type="search" value=${memberQuery} placeholder="Buscar por nome ou e-mail"
              class="wa-field w-full px-3 py-2 rounded-md text-[13px] mb-2"
              onInput=${(event) => setMemberQuery(event.target.value)} />
            <div class="border border-wa-border rounded-md bg-wa-bg max-h-[230px] overflow-y-auto wa-scrollbar">
              ${filteredUsers.length ? filteredUsers.map(user => {
                const selected = form.memberIds.some(id => sameId(id, user.id));
                const inactive = !activeFlag(user);
                return html`
                  <label key=${user.id} class="flex items-start gap-2 px-3 py-2 border-b border-wa-border last:border-b-0 ${inactive && !selected ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer hover:bg-wa-hover'}">
                    <input type="checkbox" class="mt-0.5" checked=${selected}
                      disabled=${inactive && !selected} onChange=${() => toggleMember(user)} />
                    <span class="min-w-0 flex-1">
                      <span class="block text-[13px] text-wa-text truncate">${user.name || user.email}</span>
                      <span class="block text-[11px] text-wa-secondary truncate">${user.email || ''}${inactive ? ' · inativo' : ''}</span>
                    </span>
                  </label>`;
              }) : html`
                <div class="px-3 py-4 text-[13px] text-wa-secondary text-center">
                  ${memberQuery ? 'Nenhum usuário encontrado.' : 'Nenhum usuário disponível.'}
                </div>`}
            </div>
            <${FieldError} id="team-members-error">${errors.members}</${FieldError}>
          </div>
        </section>

        <div class="space-y-5">
          <section aria-labelledby="team-access-title">
            <div id="team-access-title" class="text-[13px] font-semibold text-wa-text mb-2">Acesso</div>
            <div class="space-y-2" role="radiogroup" aria-describedby="team-access-help">
              ${ACCESS_MODES.map(option => html`
                <label key=${option.value} class="flex items-start gap-2 rounded-md border border-wa-border bg-wa-bg px-3 py-2 cursor-pointer hover:bg-wa-hover">
                  <input type="radio" name="team-access" value=${option.value}
                    checked=${form.accessMode === option.value}
                    onChange=${() => setForm(previous => ({
                      ...previous,
                      accessMode: option.value,
                      visibleToAssignee: option.value === 'open' ? false : previous.visibleToAssignee,
                    }))} />
                  <span>
                    <span class="block text-[13px] font-medium text-wa-text">${option.label}</span>
                    <span class="block text-[11px] leading-relaxed text-wa-secondary">${option.description}</span>
                  </span>
                </label>`)}
            </div>
            <div id="team-access-help" class="sr-only">O modo privado também protege acesso direto e mídia.</div>
            <${FieldError} id="team-access-error">${errors.accessMode}</${FieldError}>

            ${form.accessMode !== 'open' ? html`
              <label class="flex items-start gap-2 mt-3 rounded-md border border-wa-border bg-wa-bg px-3 py-2 cursor-pointer">
                <input type="checkbox" class="mt-0.5" checked=${form.visibleToAssignee}
                  onChange=${(event) => set('visibleToAssignee', event.target.checked)} />
                <span>
                  <span class="block text-[13px] text-wa-text">Responsável pode visualizar</span>
                  <span class="block text-[11px] leading-relaxed text-wa-secondary">Permite que o responsável veja sua conversa mesmo sem pertencer ao time.</span>
                </span>
              </label>` : null}

            ${team && freshState.accessMode === 'private' && form.accessMode !== 'private' ? html`
              <div class="mt-3 rounded-md border border-wa-border bg-wa-bg px-3 py-2">
                <div class="text-[12px] text-red-500 font-medium">Este ajuste amplia o acesso às conversas.</div>
                <label class="flex items-start gap-2 mt-2 cursor-pointer">
                  <input type="checkbox" class="mt-0.5" checked=${form.confirmAccessExpansion}
                    onChange=${(event) => set('confirmAccessExpansion', event.target.checked)} />
                  <span class="text-[12px] text-wa-text">Confirmo que pessoas fora do time poderão ter mais acesso.</span>
                </label>
                <${FieldError} id="team-expansion-error">${errors.confirmAccessExpansion}</${FieldError}>
              </div>` : null}
          </section>

          <section aria-labelledby="team-routing-title">
            <div id="team-routing-title" class="text-[13px] font-semibold text-wa-text mb-2">Distribuição</div>
            <label for="team-routing" class="block text-[12px] text-wa-secondary mb-1">Estratégia</label>
            <select id="team-routing" class="wa-field w-full px-3 py-2 rounded-md text-[14px]" value=${form.routingMode}
              onChange=${(event) => setForm(previous => ({
                ...previous,
                routingMode: event.target.value,
                defaultUserId: event.target.value === 'fixed_user' ? previous.defaultUserId : '',
                defaultAgentKey: event.target.value === 'fixed_ai' ? previous.defaultAgentKey : '',
              }))}>
              ${ROUTING_MODES.map(option => html`<option value=${option.value}>${option.label}</option>`)}
            </select>
            <div class="text-[11px] text-wa-secondary mt-1">${ROUTING_MODES.find(option => option.value === form.routingMode)?.description}</div>
            <${FieldError} id="team-routing-error">${errors.routingMode}</${FieldError}>

            ${form.routingMode === 'fixed_user' ? html`
              <div class="mt-3">
                <label for="team-default-user" class="block text-[12px] text-wa-secondary mb-1">Atendente fixo</label>
                <select id="team-default-user" class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
                  value=${form.defaultUserId} aria-invalid=${!!errors.defaultUserId}
                  onChange=${(event) => set('defaultUserId', event.target.value)}>
                  <option value="">Selecione…</option>
                  ${form.defaultUserId && !defaultUserListed ? html`
                    <option value=${form.defaultUserId} disabled>Destino indisponível</option>
                  ` : null}
                  ${memberUsers.map(user => html`
                    <option value=${String(user.id)} disabled=${!activeFlag(user)}>
                      ${user.name || user.email}${activeFlag(user) ? '' : ' (inativo)'}
                    </option>`)}
                </select>
                <${FieldError} id="team-default-user-error">${errors.defaultUserId}</${FieldError}>
              </div>` : null}

            ${form.routingMode === 'fixed_ai' ? html`
              <div class="mt-3">
                <label for="team-default-ai" class="block text-[12px] text-wa-secondary mb-1">Agente de IA</label>
                <select id="team-default-ai" class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
                  value=${form.defaultAgentKey} aria-invalid=${!!errors.defaultAgentKey}
                  onChange=${(event) => set('defaultAgentKey', event.target.value)}>
                  <option value="">Selecione…</option>
                  ${form.defaultAgentKey && !defaultAgentListed ? html`
                    <option value=${form.defaultAgentKey} disabled>Destino indisponível</option>
                  ` : null}
                  ${aiAgents.map(agent => html`
                    <option value=${agent.agent_key} disabled=${!activeFlag(agent, 'enabled')}>
                      ${agent.display_name || agent.agent_key}${activeFlag(agent, 'enabled') ? '' : ' (desabilitado)'}
                    </option>`)}
                </select>
                <${FieldError} id="team-default-ai-error">${errors.defaultAgentKey}</${FieldError}>
              </div>` : null}

            <label class="flex items-start gap-2 mt-3 rounded-md border border-wa-border bg-wa-bg px-3 py-2 cursor-pointer">
              <input type="checkbox" class="mt-0.5" checked=${form.aiAssignable}
                onChange=${(event) => set('aiAssignable', event.target.checked)} />
              <span>
                <span class="block text-[13px] text-wa-text">IA pode encaminhar para este time</span>
                <span class="block text-[11px] leading-relaxed text-wa-secondary">Controla se ferramentas de IA podem escolher este time como destino.</span>
              </span>
            </label>
          </section>
        </div>
      </div>

      ${serverError ? html`
        <div role="alert" aria-live="assertive" class="mx-4 mb-3 rounded-md border border-wa-border bg-wa-bg px-3 py-2 text-[13px] text-red-500">${serverError}</div>
      ` : null}

      <div class="sticky bottom-0 bg-wa-panel border-t border-wa-border px-4 py-3 flex justify-end gap-2">
        <button type="button" class="px-3 py-2 rounded-md border border-wa-border bg-wa-bg text-[14px] text-wa-text hover:bg-wa-hover"
          onClick=${requestCancel} disabled=${busy}>Cancelar</button>
        <button type="submit" class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 disabled:opacity-50"
          disabled=${busy}>${busy ? 'Salvando…' : team ? 'Salvar alterações' : 'Criar time'}</button>
      </div>

      ${discarding ? html`
        <${ConfirmDialog} title="Descartar alterações?" description="As mudanças feitas neste formulário não foram salvas."
          confirmLabel="Descartar" danger=${true}
          onConfirm=${() => { setDiscarding(false); onCancel(); }} onClose=${() => setDiscarding(false)} />
      ` : null}
    </form>
  `;
}

function TeamCard({ team, users, aiAgents, editing, busy, error, onOpen, onClose,
  onSave, onDeactivate, onReactivate, onHardDelete }) {
  const target = routingTargetLabel(team, users, aiAgents);
  const countKnown = Number.isInteger(team.conversation_count);
  const canHardDelete = !activeFlag(team) && countKnown && team.conversation_count === 0;
  return html`
    <article class="bg-wa-panel border border-wa-border rounded-lg">
      <div class="px-3 py-2.5 flex items-start gap-3">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-1.5 flex-wrap">
            <span class="text-[14px] text-wa-text font-medium truncate">${team.name}</span>
            <span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">${accessLabel(team)}</span>
            <span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">${routingLabel(team)}</span>
            ${!activeFlag(team) ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Inativo</span>` : null}
            ${team.ai_assignable ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Disponível para IA</span>` : null}
          </div>
          <div class="text-[12px] text-wa-secondary mt-1 truncate">
            ${(team.member_user_ids || []).length} membro(s)${target ? ` · ${target}` : ''}${countKnown ? ` · ${team.conversation_count} conversa(s)` : ''}
          </div>
          ${team.description ? html`<div class="text-[12px] text-wa-secondary mt-0.5 truncate">${team.description}</div>` : null}
        </div>
        ${!editing ? html`
          <div class="flex gap-1 shrink-0 flex-wrap justify-end">
            <a href=${`/teams/${team.id}`} onClick=${(event) => {
              if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
              event.preventDefault(); onOpen();
            }} class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover no-underline">Editar</a>
            ${activeFlag(team) ? html`
              <button type="button" class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover" onClick=${onDeactivate}>Desativar</button>
            ` : html`
              <button type="button" class="px-2 py-1 rounded-md text-[13px] text-wa-teal hover:bg-wa-hover" onClick=${onReactivate} disabled=${busy}>Reativar</button>
              ${canHardDelete ? html`
                <button type="button" class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover" onClick=${onHardDelete}>Excluir definitivamente</button>
              ` : null}
            `}
          </div>` : null}
      </div>
      ${editing ? html`
        <div class="border-t border-wa-border p-3">
          <${TeamForm} team=${team} users=${users} aiAgents=${aiAgents} busy=${busy}
            serverError=${error} onSubmit=${(data) => onSave(team, data)} onCancel=${onClose} />
        </div>` : null}
    </article>
  `;
}

export default function TeamsManager({ initialEntity = null }) {
  const [teams, setTeams] = useState([]);
  const [users, setUsers] = useState([]);
  const [aiAgents, setAiAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get('q') || '');

  async function load({ keepRows = false } = {}) {
    if (!keepRows) setLoading(true);
    setError('');
    try {
      const response = await getTeams({ includeInactive: true });
      if (response && response.ok) {
        setTeams(Array.isArray(response.data?.teams) ? response.data.teams : []);
        setUsers(Array.isArray(response.data?.users) ? response.data.users : []);
        setAiAgents(Array.isArray(response.data?.ai_agents) ? response.data.ai_agents : []);
      } else setError(response?.error || 'Não foi possível carregar os times.');
    } catch (_) {
      setError('Não foi possível carregar os times. Verifique a conexão e tente novamente.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (!window.location.pathname.startsWith('/users/teams')) return;
    const suffix = window.location.pathname.slice('/users/teams'.length);
    history.replaceState(null, '', `/teams${suffix}${window.location.search}`);
  }, []);

  const pushUrl = useDeepLink({
    tab: 'teams', resolve: initialEntity, ready: !loading,
    open: (selection) => {
      if (!selection || selection.id == null) { setCreating(false); setEditingId(null); return; }
      if (selection.id === 'new') { setCreating(true); setEditingId(null); return; }
      const team = teams.find(row => sameId(row.id, selection.id));
      if (team) { setEditingId(team.id); setCreating(false); }
    },
  });

  const navigate = (selection) => {
    pushUrl(selection);
    const params = new URLSearchParams(window.location.search);
    if (query.trim()) params.set('q', query.trim());
    else params.delete('q');
    const search = params.toString();
    history.replaceState(null, '', `${window.location.pathname}${search ? `?${search}` : ''}`);
  };

  useEffect(() => {
    const syncQuery = () => setQuery(new URLSearchParams(window.location.search).get('q') || '');
    window.addEventListener('popstate', syncQuery);
    return () => window.removeEventListener('popstate', syncQuery);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (query.trim()) params.set('q', query.trim());
    else params.delete('q');
    const next = `${window.location.pathname}${params.toString() ? `?${params}` : ''}`;
    if (next !== `${window.location.pathname}${window.location.search}`) history.replaceState(null, '', next);
  }, [query]);

  const filteredTeams = teams.filter(team => {
    const needle = query.trim().toLocaleLowerCase('pt-BR');
    return !needle || `${team.name || ''} ${team.description || ''}`.toLocaleLowerCase('pt-BR').includes(needle);
  });

  async function create(data) {
    setBusy(true); setError('');
    const response = await createTeam(data).catch(() => ({ ok: false, error: 'Falha de conexão ao criar o time.' }));
    setBusy(false);
    if (!response?.ok) { setError(response?.error || 'Não foi possível criar o time.'); return false; }
    setCreating(false); navigate(null); await load({ keepRows: true }); return true;
  }

  async function save(team, data) {
    setBusy(true); setError('');
    const response = await updateTeam(team.id, data).catch(() => ({ ok: false, error: 'Falha de conexão ao salvar o time.' }));
    setBusy(false);
    if (!response?.ok) { setError(response?.error || 'Não foi possível salvar o time.'); return false; }
    setEditingId(null); navigate(null); await load({ keepRows: true }); return true;
  }

  async function reactivate(team) {
    setBusy(true); setError('');
    const response = await updateTeam(team.id, { is_active: true }).catch(() => ({ ok: false, error: 'Falha de conexão ao reativar o time.' }));
    setBusy(false);
    if (response?.ok) await load({ keepRows: true });
    else setError(response?.error || 'Não foi possível reativar o time.');
  }

  async function confirmLifecycle() {
    const action = confirming;
    if (!action) return;
    setBusy(true); setError('');
    const response = await deleteTeam(action.team.id, { hard: action.kind === 'hard' })
      .catch(() => ({ ok: false, error: 'Falha de conexão ao alterar o time.' }));
    setBusy(false);
    if (response?.ok) { setConfirming(null); await load({ keepRows: true }); }
    else setError(response?.error || 'Não foi possível alterar o time.');
  }

  return html`
    <div class="space-y-3">
      <div class="flex items-center gap-2 flex-wrap">
        <input type="search" value=${query} placeholder="Buscar times" aria-label="Buscar times"
          class="wa-field min-w-[220px] flex-1 px-3 py-2 rounded-md text-[14px]"
          onInput=${(event) => setQuery(event.target.value)} />
        <span class="text-[12px] tabular-nums text-wa-secondary">${filteredTeams.length} de ${teams.length}</span>
        ${!creating && editingId == null ? html`
          <a href="/teams/new" class="px-3 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 no-underline"
            onClick=${(event) => {
              if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
              event.preventDefault(); setCreating(true); setEditingId(null); setError(''); navigate({ id: 'new' });
            }}>+ Novo time</a>` : null}
      </div>

      ${error && !creating && editingId == null ? html`
        <div role="alert" aria-live="assertive" class="rounded-md border border-wa-border bg-wa-panel px-3 py-2 flex items-center justify-between gap-3">
          <span class="text-[13px] text-red-500">${error}</span>
          <button type="button" class="text-[13px] text-wa-teal" onClick=${() => load({ keepRows: teams.length > 0 })}>Tentar novamente</button>
        </div>` : null}

      ${creating ? html`
        <${TeamForm} users=${users} aiAgents=${aiAgents} busy=${busy} serverError=${error}
          onSubmit=${create} onCancel=${() => { setCreating(false); setError(''); navigate(null); }} />` : null}

      ${loading && teams.length === 0 ? html`
        <div aria-label="Carregando times" class="space-y-2">
          ${[1, 2, 3].map(index => html`<div key=${index} class="h-[72px] rounded-lg bg-wa-panel border border-wa-border animate-pulse-slow"></div>`)}
        </div>` : null}

      ${!loading && teams.length === 0 && !creating && !error ? html`
        <div class="rounded-lg border border-wa-border bg-wa-panel px-4 py-8 text-center">
          <div class="text-[14px] text-wa-text">Nenhum time cadastrado.</div>
          <div class="text-[12px] text-wa-secondary mt-1">Crie o primeiro time para organizar acesso e distribuição.</div>
        </div>` : null}

      ${!loading && teams.length > 0 && filteredTeams.length === 0 ? html`
        <div class="rounded-lg border border-wa-border bg-wa-panel px-4 py-6 text-center text-[13px] text-wa-secondary">
          Nenhum time corresponde a “${query}”.
          <button type="button" class="ml-1 text-wa-teal" onClick=${() => setQuery('')}>Limpar busca</button>
        </div>` : null}

      <div class="space-y-2 ${loading && teams.length ? 'opacity-60' : ''}" aria-busy=${loading}>
        ${filteredTeams.map(team => html`
          <${TeamCard} key=${team.id} team=${team} users=${users} aiAgents=${aiAgents}
            editing=${sameId(editingId, team.id)} busy=${busy} error=${sameId(editingId, team.id) ? error : ''}
            onOpen=${() => { setEditingId(team.id); setCreating(false); setError(''); navigate({ id: team.id }); }}
            onClose=${() => { setEditingId(null); setError(''); navigate(null); }} onSave=${save}
            onDeactivate=${() => setConfirming({ kind: 'deactivate', team })}
            onReactivate=${() => reactivate(team)}
            onHardDelete=${() => setConfirming({ kind: 'hard', team })} />`)}
      </div>

      ${confirming ? html`
        <${ConfirmDialog}
          title=${confirming.kind === 'hard'
            ? `Excluir definitivamente o time “${confirming.team.name}”?`
            : `Desativar o time “${confirming.team.name}”?`}
          description=${confirming.kind === 'hard'
            ? 'A API informou que não há conversas vinculadas. O cadastro e seus membros serão apagados.'
            : 'O time deixará de aparecer como destino, mas conversas históricas continuarão vinculadas e legíveis.'}
          reversible=${confirming.kind !== 'hard'} danger=${true} busy=${busy}
          error=${error}
          confirmLabel=${confirming.kind === 'hard' ? 'Excluir definitivamente' : 'Desativar'}
          onConfirm=${confirmLifecycle} onClose=${() => { if (!busy) setConfirming(null); }} />` : null}
    </div>
  `;
}
