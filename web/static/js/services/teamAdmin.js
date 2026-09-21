import { accessModeOf } from './teamCapabilities.js';

export const ACCESS_MODES = [
  { value: 'open', label: 'Aberto', description: 'Visível para quem pode acessar a inbox.' },
  { value: 'list_hidden', label: 'Oculto da lista', description: 'Fora do time não vê nas listas, mas ainda pode abrir por link direto.' },
  { value: 'private', label: 'Privado', description: 'Protege listas, link direto, mensagens, busca, eventos e mídia.' },
];

export const ROUTING_MODES = [
  { value: 'manual', label: 'Manual', description: 'Vincula o time sem escolher um responsável.' },
  { value: 'round_robin', label: 'Round-robin', description: 'Alterna entre membros ativos que também pertencem à inbox.' },
  { value: 'fixed_user', label: 'Atendente fixo', description: 'Encaminha sempre para o atendente selecionado quando elegível.' },
  { value: 'fixed_ai', label: 'Agente de IA', description: 'Encaminha para o agente de IA selecionado quando habilitado.' },
];

const ACCESS_VALUES = new Set(ACCESS_MODES.map(option => option.value));
const ROUTING_VALUES = new Set(ROUTING_MODES.map(option => option.value));
const isActive = (record) => record && (record.is_active === true || record.is_active === 1);
const sameId = (left, right) => left != null && right != null && String(left) === String(right);

export function initialTeamForm(team = null) {
  const accessMode = team ? accessModeOf(team) : 'open';
  const routingMode = team && ROUTING_VALUES.has(team.routing_mode)
    ? team.routing_mode
    : 'manual';
  return {
    name: (team && team.name) || '',
    description: (team && team.description) || '',
    memberIds: team && Array.isArray(team.member_user_ids)
      ? [...team.member_user_ids]
      : [],
    accessMode: ACCESS_VALUES.has(accessMode) ? accessMode : 'open',
    visibleToAssignee: accessMode === 'open' ? false : !!(team && team.visible_to_assignee),
    routingMode,
    defaultUserId: routingMode === 'fixed_user' && team && team.default_user_id != null
      ? String(team.default_user_id)
      : '',
    defaultAgentKey: routingMode === 'fixed_ai' && team && team.default_agent_key
      ? String(team.default_agent_key)
      : '',
    aiAssignable: !!(team && team.ai_assignable),
    confirmAccessExpansion: false,
  };
}

export function validateTeamForm(form, { users = [], aiAgents = [], originalTeam = null } = {}) {
  const errors = {};
  if (!(form.name || '').trim()) errors.name = 'Informe um nome para o time.';
  if (!ACCESS_VALUES.has(form.accessMode)) errors.accessMode = 'Selecione um modo de acesso válido.';
  if (!ROUTING_VALUES.has(form.routingMode)) errors.routingMode = 'Selecione uma distribuição válida.';

  const selectedUsers = (form.memberIds || []).map(id =>
    users.find(user => sameId(user.id, id))).filter(Boolean);
  if (selectedUsers.some(user => !isActive(user))) {
    errors.members = 'Remova os membros inativos antes de salvar.';
  }
  if (form.accessMode === 'private' && !(form.memberIds || []).length) {
    errors.members = 'Selecione ao menos um membro para um time privado.';
  }
  if (form.routingMode === 'fixed_user') {
    const target = users.find(user => sameId(user.id, form.defaultUserId));
    if (!form.defaultUserId) errors.defaultUserId = 'Selecione o atendente fixo.';
    else if (!target || !isActive(target)) errors.defaultUserId = 'Selecione um atendente ativo.';
    else if (!(form.memberIds || []).some(id => sameId(id, target.id))) {
      errors.defaultUserId = 'O atendente fixo também precisa ser membro do time.';
    }
  }
  if (form.routingMode === 'fixed_ai') {
    const target = aiAgents.find(agent => agent.agent_key === form.defaultAgentKey);
    if (!form.defaultAgentKey) errors.defaultAgentKey = 'Selecione o agente de IA.';
    else if (!target || !(target.enabled === true || target.enabled === 1)) {
      errors.defaultAgentKey = 'Selecione um agente de IA habilitado.';
    }
  }
  if (originalTeam && accessModeOf(originalTeam) === 'private'
      && form.accessMode !== 'private' && !form.confirmAccessExpansion) {
    errors.confirmAccessExpansion = 'Confirme que este ajuste ampliará o acesso às conversas.';
  }
  return errors;
}

export function teamFormPayload(form, { originalTeam = null } = {}) {
  const payload = {
    name: (form.name || '').trim(),
    description: (form.description || '').trim(),
    member_user_ids: [...(form.memberIds || [])],
    access_mode: form.accessMode,
    visible_to_assignee: form.accessMode === 'open' ? false : !!form.visibleToAssignee,
    routing_mode: form.routingMode,
    default_user_id: form.routingMode === 'fixed_user' && form.defaultUserId !== ''
      ? Number(form.defaultUserId)
      : null,
    default_agent_key: form.routingMode === 'fixed_ai' && form.defaultAgentKey
      ? form.defaultAgentKey
      : null,
    ai_assignable: !!form.aiAssignable,
  };
  if (originalTeam && accessModeOf(originalTeam) === 'private'
      && form.accessMode !== 'private') {
    payload.confirm_access_expansion = !!form.confirmAccessExpansion;
  }
  return payload;
}

export function filterTeamMembers(users, query) {
  const normalized = (query || '').trim().toLocaleLowerCase('pt-BR');
  if (!normalized) return [...(users || [])];
  return (users || []).filter(user => (
    `${user.name || ''} ${user.email || ''}`.toLocaleLowerCase('pt-BR').includes(normalized)
  ));
}

export function accessLabel(team) {
  return ACCESS_MODES.find(option => option.value === accessModeOf(team))?.label || 'Aberto';
}

export function routingLabel(team) {
  return ROUTING_MODES.find(option => option.value === team?.routing_mode)?.label || 'Manual';
}

export function routingTargetLabel(team, users, aiAgents) {
  if (team?.routing_mode === 'fixed_user') {
    const user = (users || []).find(row => sameId(row.id, team.default_user_id));
    return user ? (user.name || user.email) : 'Destino indisponível';
  }
  if (team?.routing_mode === 'fixed_ai') {
    const agent = (aiAgents || []).find(row => row.agent_key === team.default_agent_key);
    return agent ? (agent.display_name || agent.agent_key) : 'Destino indisponível';
  }
  return null;
}
