// Adapter de compatibilidade do catálogo de Times (plano 166/U1).
//
// A UI consome somente os booleanos normalizados abaixo. Ela não tenta deduzir
// autorização a partir de member_user_ids, access_mode ou do papel do usuário.
// O fallback legado é deliberadamente isolado aqui e pode ser removido quando a
// janela de compatibilidade do payload anterior terminar.

export const UNKNOWN_TEAM_LABEL = 'Time indisponível';

/**
 * @typedef {Object} TeamCatalogCapabilities
 * @property {boolean} can_manage_teams
 * @property {boolean} can_assign_own_team
 * @property {boolean} can_assign_any_team
 * @property {boolean} can_route_team
 * @property {boolean} provided Se o servidor enviou o bloco de capabilities.
 */

/**
 * @typedef {Object} TeamCatalogItem
 * @property {string|number} id
 * @property {string} name
 * @property {boolean} is_active
 * @property {'open'|'list_hidden'|'private'} access_mode
 * @property {boolean} readable
 * @property {boolean} assignable
 * @property {boolean} routable
 * @property {string|null} unavailable_reason
 */

const TEAM_CAPABILITY_KEYS = ['readable', 'assignable', 'routable'];
const CATALOG_CAPABILITY_KEYS = [
  'can_manage_teams',
  'can_assign_own_team',
  'can_assign_any_team',
  'can_route_team',
];

export const EMPTY_TEAM_CAPABILITIES = Object.freeze({
  can_manage_teams: false,
  can_assign_own_team: false,
  can_assign_any_team: false,
  can_route_team: false,
  provided: false,
});

const hasOwn = (value, key) => (
  value != null && Object.prototype.hasOwnProperty.call(value, key)
);

const sameId = (left, right) => (
  left != null && right != null && String(left) === String(right)
);

const isTrue = (value) => value === true || value === 1;

export function accessModeOf(team) {
  if (team && ['open', 'list_hidden', 'private'].includes(team.access_mode)) {
    return team.access_mode;
  }
  if (team && team.enforce_team_access) return 'private';
  if (team && team.restrict_visibility) return 'list_hidden';
  return 'open';
}

function normalizeCatalogCapabilities(raw) {
  const source = raw && typeof raw === 'object' ? raw : null;
  if (!source) return EMPTY_TEAM_CAPABILITIES;
  const normalized = {};
  for (const key of CATALOG_CAPABILITY_KEYS) {
    normalized[key] = source ? isTrue(source[key]) : false;
  }
  return {
    ...normalized,
    provided: true,
  };
}

function normalizeTeam(team, { legacy = false } = {}) {
  const source = team && typeof team === 'object' ? team : {};
  return {
    ...source,
    name: typeof source.name === 'string' && source.name.trim()
      ? source.name.trim()
      : UNKNOWN_TEAM_LABEL,
    is_active: hasOwn(source, 'is_active') ? isTrue(source.is_active) : true,
    access_mode: accessModeOf(source),
    // Antes da parte 1, o catálogo significava implicitamente "visível e
    // atribuível". Esse default só é aplicado quando o catálogo inteiro é
    // reconhecido como legado; payloads parciais novos falham fechados.
    readable: hasOwn(source, 'readable') ? isTrue(source.readable) : legacy,
    assignable: hasOwn(source, 'assignable') ? isTrue(source.assignable) : legacy,
    // Routing ainda não tem contrato R1/R3. Nunca promovemos assignable para
    // routable: até o servidor declarar a capability, a opção não é roteável.
    routable: hasOwn(source, 'routable') ? isTrue(source.routable) : false,
    unavailable_reason: typeof source.unavailable_reason === 'string'
      && source.unavailable_reason.trim()
      ? source.unavailable_reason.trim()
      : null,
  };
}

/**
 * Normaliza o `data` de assignable-agents, aceitando o shape atual
 * `{users, ai_agents, teams, capabilities?}` e o futuro
 * `{catalog:{...}, capabilities}`.
 */
export function normalizeAssignableCatalog(data) {
  const outer = data && typeof data === 'object' ? data : {};
  const catalog = outer.catalog && typeof outer.catalog === 'object'
    ? outer.catalog
    : outer;
  const rawTeams = Array.isArray(catalog.teams) ? catalog.teams : [];
  const hasExplicitTeamCapabilities = rawTeams.some(team =>
    TEAM_CAPABILITY_KEYS.some(key => hasOwn(team, key)));
  const legacy = !hasExplicitTeamCapabilities;

  return {
    ...catalog,
    users: Array.isArray(catalog.users) ? catalog.users : [],
    ai_agents: Array.isArray(catalog.ai_agents) ? catalog.ai_agents : [],
    teams: rawTeams.map(team => normalizeTeam(team, { legacy })),
    capabilities: normalizeCatalogCapabilities(
      outer.capabilities || catalog.capabilities),
    contract: legacy ? 'legacy' : 'capabilities-v1',
  };
}

/** Preserve failures byte-for-byte; only successful data is adapted. */
export function normalizeAssignableCatalogResponse(response) {
  if (!response || response.ok !== true) return response;
  return {
    ...response,
    data: normalizeAssignableCatalog(response.data),
  };
}

/** Normaliza times retornados pelo CRUD sem adicionar capabilities operacionais. */
export function normalizeAdminTeam(team) {
  const source = team && typeof team === 'object' ? team : {};
  return {
    ...source,
    name: typeof source.name === 'string' && source.name.trim()
      ? source.name.trim()
      : UNKNOWN_TEAM_LABEL,
    is_active: hasOwn(source, 'is_active') ? isTrue(source.is_active) : true,
    access_mode: accessModeOf(source),
  };
}

export function normalizeTeamAdminResponse(response) {
  if (!response || response.ok !== true || !response.data) return response;
  const data = { ...response.data };
  if (Array.isArray(data.teams)) data.teams = data.teams.map(normalizeAdminTeam);
  if (data.team) data.team = normalizeAdminTeam(data.team);
  return { ...response, data };
}

/**
 * Preserva uma referência legível do time atual quando o catálogo vier
 * recortado. O fallback nunca é destino de atribuição ou routing.
 */
export function currentTeamReference(teams, current = {}) {
  const list = Array.isArray(teams) ? teams : [];
  const currentId = current && current.id;
  if (currentId == null) return null;
  const found = list.find(team => sameId(team.id, currentId));
  if (found) return found;
  return {
    id: currentId,
    name: typeof current.name === 'string' && current.name.trim()
      ? current.name.trim()
      : UNKNOWN_TEAM_LABEL,
    is_active: hasOwn(current, 'is_active') ? isTrue(current.is_active) : true,
    access_mode: accessModeOf(current),
    readable: true,
    assignable: false,
    routable: false,
    unavailable_reason: 'Este time não está disponível como destino.',
    source: 'current-team-fallback',
  };
}

export function readableTeamOptions(teams, current = null) {
  const list = (Array.isArray(teams) ? teams : []).filter(team => team.readable === true);
  const currentRef = currentTeamReference(teams, current || {});
  if (currentRef && !list.some(team => sameId(team.id, currentRef.id))) {
    return [...list, currentRef];
  }
  return list;
}

export function assignableTeamOptions(teams) {
  return (Array.isArray(teams) ? teams : []).filter(team => team.assignable === true);
}

export function routableTeamOptions(teams) {
  return (Array.isArray(teams) ? teams : []).filter(team => team.routable === true);
}

export function teamUnavailableReason(team, capability = 'assignable') {
  if (!team || team[capability] === true) return null;
  if (team.unavailable_reason) return team.unavailable_reason;
  if (team.is_active === false) return 'Este time está inativo.';
  if (capability === 'routable') return 'Este time não está disponível para encaminhamento.';
  if (capability === 'readable') return 'Este time não está disponível para consulta.';
  return 'Este time não está disponível para atribuição.';
}
