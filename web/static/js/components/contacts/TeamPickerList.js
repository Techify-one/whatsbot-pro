// Searchable team picker (plano 153) — sibling of AssigneeList.js, used by the
// conversation right-click context menu flyout ("Atribuir time"). NOT a variant
// of AssigneeList: that component's onPick contract is "substitua o dono único"
// (assignee/agente SÃO mutuamente exclusivos); time é ORTOGONAL a isso (D1) — não
// substitui nada, então ganha o próprio contrato em vez de herdar a semântica de
// exclusão que não se aplica.
//
//  onPick(teamId): teamId is a team id, or null ("Nenhum time").

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import {
  assignableTeamOptions,
  currentTeamReference,
  teamUnavailableReason,
} from '../../services/teamCapabilities.js';

const html = htm.bind(h);

function TeamIcon() {
  return html`<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M16.5 13c-1.2 0-3.07.34-4.5 1-1.43-.67-3.3-1-4.5-1C5.33 13 1 14.08 1 16.25V19h22v-2.75c0-2.17-4.33-3.25-6.5-3.25zm-4 5.5h-10v-1.25c0-.54 2.56-1.75 4.5-1.75s4.5 1.21 4.5 1.75v1.25zm7.5 0h-6v-1.25c0-.68-.35-1.24-.87-1.7.71-.24 1.47-.4 2.37-.4 1.94 0 4.5 1.21 4.5 1.75v1.6zM7.5 12c1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3 1.34 3 3 3zm0-4.5c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5S6 9.83 6 9s.67-1.5 1.5-1.5zm9 4.5c1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3 1.34 3 3 3zm0-4.5c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5S15 9.83 15 9s.67-1.5 1.5-1.5z"/></svg>`;
}

export function TeamPickerList({
  teams = [],
  currentTeamId = null,
  currentTeamName = '',
  onPick,
  busy = false,
  autoFocus = true,
  searchPlaceholder = 'Pesquisar times',
}) {
  const [search, setSearch] = useState('');

  const q = search.trim().toLowerCase();
  const currentTeam = currentTeamReference(teams, {
    id: currentTeamId,
    name: currentTeamName,
  });
  const destinations = assignableTeamOptions(teams);
  const options = currentTeam && !destinations.some(t => String(t.id) === String(currentTeam.id))
    ? [...destinations, currentTeam]
    : destinations;
  const filtered = options.filter(t => !q || (t.name || '').toLowerCase().includes(q));
  const canClear = !!(currentTeam && currentTeam.assignable === true);

  const rowCls = (active) =>
    `w-full text-left px-3 py-1.5 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 ${active ? 'text-wa-teal font-medium' : 'text-wa-text'}`;

  const pick = (teamId) => {
    const target = teamId == null
      ? currentTeam
      : teams.find(t => String(t.id) === String(teamId));
    if (!busy && target && target.assignable === true && onPick) onPick(teamId);
  };

  const renderOption = (team) => {
    const reason = teamUnavailableReason(team, 'assignable');
    const reasonId = reason ? `team-option-${team.id}-reason` : null;
    return html`
      <button key=${'t' + team.id} onClick=${() => pick(team.id)}
        disabled=${busy || team.assignable !== true}
        aria-describedby=${reasonId}
        class="${rowCls(String(currentTeamId) === String(team.id))} disabled:opacity-60 disabled:cursor-not-allowed">
        <span class="text-wa-secondary"><${TeamIcon} /></span>
        <span class="min-w-0">
          <span class="block truncate">${team.name}</span>
          ${reason ? html`
            <span id=${reasonId} class="block text-[11px] leading-tight text-wa-secondary">
              ${reason}
            </span>
          ` : null}
        </span>
      </button>
    `;
  };

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
      ${canClear ? html`
        <button onClick=${() => pick(null)} class="w-full text-left px-3 py-1.5 text-[13px] text-red-400 hover:bg-wa-hover transition-colors flex items-center gap-2">
          <span class="w-[15px] shrink-0"></span> Nenhum time
        </button>
      ` : null}
      ${filtered.length > 0 ? filtered.map(renderOption) : html`
        <div class="px-3 py-2 text-[13px] text-wa-secondary">
          ${q ? 'Nenhum time encontrado' : 'Nenhum time disponível para atribuição'}
        </div>
      `}
    </div>
  `;
}
