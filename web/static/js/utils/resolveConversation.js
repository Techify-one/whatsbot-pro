// Single funnel for resolving/reopening a conversation. EVERY "Resolver"/"Fechar"
// call site routes through here so the `filter.conversation.beforeResolve` client
// filter fires consistently (header do chat, painel de info, lista, kanban) — a
// plugin can intercept (show a popup, fill fields) or abort by returning null.
// Reopening (status 'open') is never gated.
//
// Returns the api response from setConversationStatus, OR {ok:false, aborted:true}
// when a plugin cancelled — callers already guard on `r.ok` so an abort is a no-op.

import { setConversationStatus } from '../services/api.js';
import { applyFilter } from '../plugins/registry.js';

export async function resolveConversation(conv, status, ctx = {}) {
  if (status === 'closed') {
    const r = await applyFilter('filter.conversation.beforeResolve', conv, { ...ctx, status });
    if (r === null) return { ok: false, aborted: true };
  }
  // Accept either a conversation object (preferred — filter gets full context) or a raw id.
  const id = (conv && typeof conv === 'object' && conv.id != null) ? conv.id : conv;
  return setConversationStatus(id, status);
}

export default resolveConversation;
