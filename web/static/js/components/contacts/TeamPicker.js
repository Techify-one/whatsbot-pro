// Team picker (plano 153) for the conversation info panel — the "Time atribuído"
// counterpart to AssigneePicker.js. Team is INDEPENDENT of the human/AI assignee
// (D1): assigning/clearing it never touches assignee_user_id/active_agent_key, so
// this stays its own self-contained block instead of folding into AssigneePicker
// (whose onPick contract already means "replace the sole owner"). Reuses
// TeamPickerList — the same dropdown body as the context menu's "Atribuir time".

import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getAssignableAgents, assignTeam } from '../../services/api.js';
import { TeamPickerList } from './TeamPickerList.js';

const html = htm.bind(h);

function ChevronDown() {
  return html`<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>`;
}

function TeamIcon() {
  return html`<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M16.5 13c-1.2 0-3.07.34-4.5 1-1.43-.67-3.3-1-4.5-1C5.33 13 1 14.08 1 16.25V19h22v-2.75c0-2.17-4.33-3.25-6.5-3.25zm-4 5.5h-10v-1.25c0-.54 2.56-1.75 4.5-1.75s4.5 1.21 4.5 1.75v1.25zm7.5 0h-6v-1.25c0-.68-.35-1.24-.87-1.7.71-.24 1.47-.4 2.37-.4 1.94 0 4.5 1.21 4.5 1.75v1.6zM7.5 12c1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3 1.34 3 3 3zm0-4.5c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5S6 9.83 6 9s.67-1.5 1.5-1.5zm9 4.5c1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3 1.34 3 3 3zm0-4.5c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5S15 9.83 15 9s.67-1.5 1.5-1.5z"/></svg>`;
}

export function TeamPicker({ conv, onChange }) {
  const [teams, setTeams] = useState([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);

  // Same source as the context menu's team list (`assignable-agents`), not
  // `/api/teams` — that CRUD endpoint is gated by `team.manage` and would 403
  // for an operator who can merely assign, not administer, teams.
  useEffect(() => {
    let alive = true;
    getAssignableAgents().then(r => {
      if (!alive || !r || !r.ok || !r.data) return;
      setTeams(Array.isArray(r.data.teams) ? r.data.teams : []);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    if (open) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const pick = useCallback(async (teamId) => {
    if (!conv || busy) return;
    setBusy(true);
    try {
      const r = await assignTeam(conv.id, teamId);
      if (r && r.ok && r.data && r.data.conversation) onChange && onChange(r.data.conversation);
    } finally {
      setBusy(false);
      setOpen(false);
    }
  }, [conv, busy, onChange]);

  if (!conv) {
    return html`
      <div class="text-[13px] text-wa-secondary">Sem conversa ativa para atribuir.</div>
    `;
  }

  const currentTeam = conv.team_id != null ? teams.find(t => t.id === conv.team_id) : null;
  const currentLabel = conv.team_id == null
    ? 'Nenhum'
    : (currentTeam ? currentTeam.name : (conv.team_name || `#${conv.team_id}`));

  return html`
    <div>
      <div class="flex items-center justify-between mb-1.5">
        <span class="text-wa-iconActive text-[13px] font-medium">Time atribuído</span>
      </div>

      <div class="relative" ref=${ref}>
        <button
          disabled=${busy}
          onClick=${() => setOpen(o => !o)}
          class="w-full flex items-center justify-between gap-2 bg-wa-panel text-wa-text text-[14px] rounded-[8px] px-3 py-2 border border-wa-border hover:border-wa-iconActive transition-colors disabled:opacity-50"
        >
          <span class="flex items-center gap-2 min-w-0">
            <span class="text-wa-secondary shrink-0"><${TeamIcon} /></span>
            <span class="truncate ${currentLabel === 'Nenhum' ? 'text-wa-secondary' : ''}">${currentLabel}</span>
          </span>
          <${ChevronDown} />
        </button>

        ${open ? html`
          <div class="absolute left-0 right-0 top-full mt-1 bg-wa-panel border border-wa-border rounded-[8px] shadow-lg z-20 max-h-[300px] overflow-y-auto wa-scrollbar">
            <${TeamPickerList}
              teams=${teams}
              currentTeamId=${conv.team_id}
              onPick=${pick}
              busy=${busy}
            />
          </div>
        ` : null}
      </div>
    </div>
  `;
}
