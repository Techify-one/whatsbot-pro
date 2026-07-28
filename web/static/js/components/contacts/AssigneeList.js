// Shared assignee list body (plano 10) — the searchable "human + AI" picker list
// used by both the conversation info-panel dropdown (AssigneePicker) and the
// conversation right-click context menu flyout (ContextMenu). Presentational only:
// it renders the search input + "Não atribuída" + optional "Atribuir a mim" +
// "Agentes" (humans) + "Inteligência Artificial" (AI subagents) sections and reports
// a pick via onPick — the caller owns the data fetch and the assign call.
//
//  onPick(payload): payload is the assignAgent() body —
//    { kind: 'none' } | { kind: 'user', userId } | { kind: 'ai', agentKey }.

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

export function BotIcon() {
  return html`<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M12 2a2 2 0 012 2v1h3a2 2 0 012 2v2h1a2 2 0 010 4h-1v2a2 2 0 01-2 2h-3v1a2 2 0 01-4 0v-1H7a2 2 0 01-2-2v-2H4a2 2 0 010-4h1V7a2 2 0 012-2h3V4a2 2 0 012-2zm-3 7a1 1 0 00-1 1v4a1 1 0 002 0v-4a1 1 0 00-1-1zm6 0a1 1 0 00-1 1v4a1 1 0 002 0v-4a1 1 0 00-1-1z"/></svg>`;
}
export function PersonIcon() {
  return html`<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>`;
}

export function AssigneeList({
  users = [],
  aiAgents = [],
  me = null,
  assigneeUserId = null,
  activeAgentKey = null,
  onPick,
  showAssignToMe = false,
  showUnassign = null,
  busy = false,
  autoFocus = true,
  searchPlaceholder = 'Pesquisar agentes',
}) {
  const [search, setSearch] = useState('');

  const q = search.trim().toLowerCase();
  const filteredUsers = users.filter(u => !q || (u.name || '').toLowerCase().includes(q) || (u.email || '').toLowerCase().includes(q));
  const filteredAi = aiAgents.filter(a => !q || (a.display_name || '').toLowerCase().includes(q));
  const assignedToMe = me && assigneeUserId != null && assigneeUserId === me.id;
  // Default: offer "Desatribuir" when this conversation has an assignee (human or AI).
  // Callers with a mixed selection (bulk menu) pass an explicit bool to force it on.
  const canUnassign = showUnassign != null ? showUnassign : (assigneeUserId != null || !!activeAgentKey);

  const rowCls = (active) =>
    `w-full text-left px-3 py-1.5 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 ${active ? 'text-wa-teal font-medium' : 'text-wa-text'}`;

  const pick = (payload) => { if (!busy && onPick) onPick(payload); };

  return html`
    <div>
      <div class="p-2 border-b border-wa-border sticky top-0 bg-wa-panel z-10">
        <input
          type="text"
          value=${search}
          onInput=${(e) => setSearch(e.target.value)}
          placeholder=${searchPlaceholder}
          autofocus=${autoFocus}
          class="wa-field w-full text-[13px] rounded-md px-2 py-1.5 border border-wa-border outline-none"
        />
      </div>
      ${canUnassign ? html`
        <button onClick=${() => pick({ kind: 'none' })} class="w-full text-left px-3 py-1.5 text-[13px] text-red-400 hover:bg-wa-hover transition-colors flex items-center gap-2">
          <span class="w-[15px] shrink-0"></span> Desatribuir
        </button>
      ` : null}
      ${(showAssignToMe && me && !assignedToMe) ? html`
        <button onClick=${() => pick({ kind: 'user', userId: me.id })} class="w-full text-left px-3 py-1.5 text-[13px] text-wa-teal hover:bg-wa-hover transition-colors flex items-center gap-2">
          <span class="w-[15px] shrink-0"></span> Atribuir a mim
        </button>
      ` : null}
      ${filteredUsers.length > 0 ? html`
        <div class="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-wa-secondary">Agentes</div>
        ${filteredUsers.map(u => html`
          <button key=${'u' + u.id} onClick=${() => pick({ kind: 'user', userId: u.id })} class=${rowCls(assigneeUserId === u.id)}>
            <span class="text-wa-secondary"><${PersonIcon} /></span>
            <span class="truncate">${u.name}${u.is_admin ? html` <span class="text-[10px] text-wa-secondary">(admin)</span>` : ''}</span>
          </button>
        `)}
      ` : null}
      ${filteredAi.length > 0 ? html`
        <div class="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-wa-secondary">Inteligência Artificial</div>
        ${filteredAi.map(a => html`
          <button key=${'a' + a.agent_key} onClick=${() => pick({ kind: 'ai', agentKey: a.agent_key })} class=${rowCls(activeAgentKey === a.agent_key)}>
            <span class="text-wa-secondary"><${BotIcon} /></span>
            <span class="truncate">${a.display_name}</span>
          </button>
        `)}
      ` : null}
      ${(filteredUsers.length === 0 && filteredAi.length === 0) ? html`
        <div class="px-3 py-2 text-[13px] text-wa-secondary">Nenhum agente encontrado</div>
      ` : null}
    </div>
  `;
}
