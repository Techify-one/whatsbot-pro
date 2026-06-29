// Unified assignee picker (plano 10) — assign a conversation to a HUMAN agent or
// to an AI agent from a single list (the user chose "humanos + IA juntos").
//
// Assigning to a person routes the conversation to them and turns the IA OFF;
// assigning to an AI agent makes that agent take over (IA ON). Self-contained:
// fetches the assignable agents + the current identity, and reports the updated
// conversation back via onChange. Renders "Atribuir a mim" when logged in.

import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getAssignableAgents, getMe, assignAgent } from '../../services/api.js';

const html = htm.bind(h);

function ChevronDown() {
  return html`<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>`;
}
function BotIcon() {
  return html`<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M12 2a2 2 0 012 2v1h3a2 2 0 012 2v2h1a2 2 0 010 4h-1v2a2 2 0 01-2 2h-3v1a2 2 0 01-4 0v-1H7a2 2 0 01-2-2v-2H4a2 2 0 010-4h1V7a2 2 0 012-2h3V4a2 2 0 012-2zm-3 7a1 1 0 00-1 1v4a1 1 0 002 0v-4a1 1 0 00-1-1zm6 0a1 1 0 00-1 1v4a1 1 0 002 0v-4a1 1 0 00-1-1z"/></svg>`;
}
function PersonIcon() {
  return html`<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>`;
}

export function AssigneePicker({ conv, onChange }) {
  const [me, setMe] = useState(null);
  const [users, setUsers] = useState([]);
  const [aiAgents, setAiAgents] = useState([]);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    let alive = true;
    getMe().then(r => { if (alive && r && r.ok && r.data && r.data.user) setMe(r.data.user); }).catch(() => {});
    getAssignableAgents().then(r => {
      if (!alive || !r || !r.ok || !r.data) return;
      setUsers(Array.isArray(r.data.users) ? r.data.users : []);
      setAiAgents(Array.isArray(r.data.ai_agents) ? r.data.ai_agents : []);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    if (open) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const assign = useCallback(async (payload) => {
    if (!conv || busy) return;
    setBusy(true);
    try {
      const r = await assignAgent(conv.id, payload);
      if (r && r.ok && r.data && r.data.conversation) onChange && onChange(r.data.conversation);
    } finally {
      setBusy(false);
      setOpen(false);
      setSearch('');
    }
  }, [conv, busy, onChange]);

  if (!conv) {
    return html`
      <div class="text-[13px] text-wa-secondary">Sem conversa ativa para atribuir.</div>
    `;
  }

  // Resolve current selection label.
  let currentLabel = 'Nenhum';
  let currentIsAi = false;
  if (conv.assignee_user_id != null) {
    const u = users.find(x => x.id === conv.assignee_user_id);
    currentLabel = u ? u.name : `Usuário #${conv.assignee_user_id}`;
  } else if (conv.active_agent_key) {
    const a = aiAgents.find(x => x.agent_key === conv.active_agent_key);
    currentLabel = a ? a.display_name : conv.active_agent_key;
    currentIsAi = true;
  }

  const q = search.trim().toLowerCase();
  const filteredUsers = users.filter(u => !q || (u.name || '').toLowerCase().includes(q) || (u.email || '').toLowerCase().includes(q));
  const filteredAi = aiAgents.filter(a => !q || (a.display_name || '').toLowerCase().includes(q));
  const assignedToMe = me && conv.assignee_user_id != null && conv.assignee_user_id === me.id;

  const rowCls = (active) =>
    `w-full text-left px-3 py-1.5 text-[13px] hover:bg-wa-hover transition-colors flex items-center gap-2 ${active ? 'text-wa-teal font-medium' : 'text-wa-text'}`;

  return html`
    <div>
      <div class="flex items-center justify-between mb-1.5">
        <span class="text-wa-iconActive text-[13px] font-medium">Agente atribuído</span>
        ${(me && !assignedToMe) ? html`
          <button
            disabled=${busy}
            onClick=${() => assign({ kind: 'user', userId: me.id })}
            class="text-[12px] text-wa-teal hover:underline disabled:opacity-50"
          >→ Atribuir a mim</button>
        ` : null}
      </div>

      <div class="relative" ref=${ref}>
        <button
          disabled=${busy}
          onClick=${() => setOpen(o => !o)}
          class="w-full flex items-center justify-between gap-2 bg-wa-panel text-wa-text text-[14px] rounded-[8px] px-3 py-2 border border-wa-border hover:border-wa-iconActive transition-colors disabled:opacity-50"
        >
          <span class="flex items-center gap-2 min-w-0">
            <span class="text-wa-secondary shrink-0">${currentIsAi ? html`<${BotIcon} />` : html`<${PersonIcon} />`}</span>
            <span class="truncate ${currentLabel === 'Nenhum' ? 'text-wa-secondary' : ''}">${currentLabel}</span>
          </span>
          <${ChevronDown} />
        </button>

        ${open ? html`
          <div class="absolute left-0 right-0 top-full mt-1 bg-wa-panel border border-wa-border rounded-[8px] shadow-lg z-20 max-h-[300px] overflow-y-auto wa-scrollbar">
            <div class="p-2 border-b border-wa-border sticky top-0 bg-wa-panel">
              <input
                type="text"
                value=${search}
                onInput=${(e) => setSearch(e.target.value)}
                placeholder="Pesquisar agentes"
                autofocus
                class="wa-field w-full text-[13px] rounded-md px-2 py-1.5 border border-wa-border outline-none"
              />
            </div>
            <button onClick=${() => assign({ kind: 'none' })} class=${rowCls(conv.assignee_user_id == null && !conv.active_agent_key)}>
              <span class="w-[15px] shrink-0"></span> Não atribuída
            </button>
            ${filteredUsers.length > 0 ? html`
              <div class="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-wa-secondary">Agentes</div>
              ${filteredUsers.map(u => html`
                <button key=${'u' + u.id} onClick=${() => assign({ kind: 'user', userId: u.id })} class=${rowCls(conv.assignee_user_id === u.id)}>
                  <span class="text-wa-secondary"><${PersonIcon} /></span>
                  <span class="truncate">${u.name}${u.is_admin ? html` <span class="text-[10px] text-wa-secondary">(admin)</span>` : ''}</span>
                </button>
              `)}
            ` : null}
            ${filteredAi.length > 0 ? html`
              <div class="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-wa-secondary">Inteligência Artificial</div>
              ${filteredAi.map(a => html`
                <button key=${'a' + a.agent_key} onClick=${() => assign({ kind: 'ai', agentKey: a.agent_key })} class=${rowCls(conv.active_agent_key === a.agent_key)}>
                  <span class="text-wa-secondary"><${BotIcon} /></span>
                  <span class="truncate">${a.display_name}</span>
                </button>
              `)}
            ` : null}
            ${(filteredUsers.length === 0 && filteredAi.length === 0) ? html`
              <div class="px-3 py-2 text-[13px] text-wa-secondary">Nenhum agente encontrado</div>
            ` : null}
          </div>
        ` : null}
      </div>
    </div>
  `;
}

