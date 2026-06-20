// Permission helpers for UI gating (plano 10 FF1 / plano 03 RBAC).
//
// Rule P48: hide, don't disable. A menu item / action is rendered only when the
// current user holds the permission. Defaults are permissive on purpose:
//  - no user at all (open / legacy single-password install) → allow everything;
//  - user whose permissions[] hasn't loaded yet → allow (avoid flicker-hiding).
// We only HIDE when we positively know the logged-in user lacks the permission.
// `admin` short-circuits to '*' on the backend, so '*' grants everything.

export function hasPermission(user, key) {
  if (!user) return true;                 // open / legacy — no RBAC identity
  const perms = user.permissions;
  if (!Array.isArray(perms)) return true; // not loaded yet
  if (perms.includes('*')) return true;   // admin short-circuit
  if (!key) return true;
  return perms.includes(key);
}

export function hasAnyPermission(user, keys) {
  return (keys || []).some(k => hasPermission(user, k));
}
