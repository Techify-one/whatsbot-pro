// Presentation-only helpers for the team shown in conversation rows.
// Authorization stays server-side: this module only resolves an id against the
// catalog that Contacts already loaded for the current user.

export const UNKNOWN_TEAM_LABEL = 'Time indisponível';

export function buildTeamMap(teams) {
  const byId = new Map();
  for (const team of (Array.isArray(teams) ? teams : [])) {
    if (!team || team.id == null) continue;
    byId.set(String(team.id), team);
  }
  return byId;
}

export function teamPresentation(teamId, teamsById) {
  if (teamId == null || teamId === '') return null;

  const team = teamsById instanceof Map ? teamsById.get(String(teamId)) : null;
  const name = typeof team?.name === 'string' ? team.name.trim() : '';
  if (!team || !name) {
    return {
      label: UNKNOWN_TEAM_LABEL,
      title: 'O time atual não está disponível no catálogo.',
      unavailable: true,
    };
  }

  if (team.is_active === false) {
    return {
      label: `${name} (inativo)`,
      title: `Time inativo: ${name}`,
      unavailable: false,
    };
  }

  return {
    label: name,
    title: `Time: ${name}`,
    unavailable: false,
  };
}
