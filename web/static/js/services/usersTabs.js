// Sub-abas da tela Usuários (Usuários | Grupos de permissão | Times) e a regra de
// quem enxerga cada uma. PURO (sem preact/DOM) para ser testável com `node --test`.
//
// Cada sub-aba tem a SUA permissão: Usuários e Grupos pedem `users.manage`; Times
// pede `team.manage`. Quem só gerencia times abre a tela e vê só a aba Times, sem
// precisar de `users.manage` (a API de times já não exige — `server/routes/teams.py`).
import { hasPermission } from '../utils/permissions.js';

export const USERS_SUBTABS = [
  { id: 'users', label: 'Usuários', permission: 'users.manage' },
  { id: 'roles', label: 'Grupos de permissão', permission: 'users.manage' },
  { id: 'teams', label: 'Times', permission: 'team.manage' },
];

// Sub-abas que o usuário atual pode ver, na ordem de exibição.
export function visibleUsersSubtabs(user) {
  return USERS_SUBTABS.filter(tab => hasPermission(user, tab.permission));
}

// A tela de Usuários só abre para quem enxerga ao menos uma sub-aba.
export function canOpenUsersScreen(user) {
  return visibleUsersSubtabs(user).length > 0;
}

// Sub-aba pedida pela URL (`{ sub?, id? }` de entityFromPath), sem olhar permissão.
export function requestedUsersView(entity) {
  if (entity && entity.sub === 'roles') return 'roles';
  if (entity && entity.sub === 'teams') return 'teams';
  return 'users';
}

// Sub-aba efetivamente mostrada: a pedida se o usuário puder vê-la; senão a
// primeira que ele pode (ex.: link /users para quem só tem team.manage → Times).
export function resolveUsersView(entity, user) {
  const visible = visibleUsersSubtabs(user);
  const wanted = requestedUsersView(entity);
  if (visible.some(tab => tab.id === wanted)) return wanted;
  return visible.length ? visible[0].id : wanted;
}
