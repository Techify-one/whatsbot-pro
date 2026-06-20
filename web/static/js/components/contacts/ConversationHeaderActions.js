// Conversation actions in the chat header (plano 10 FF3).
//
// Self-contained: resolves the OPEN conversation for the current phone, shows its
// status + assignee, and offers the per-conversation actions — Resolver/Reabrir,
// Atribuir a mim and Transferir (when the user may list users). Every
// action is permission-gated (P48: hide, don't disable). Live-updates via the WS
// conversation_* events for this conversation. Renders nothing in sandbox or when
// the contact has no open conversation.

import { h } from 'preact';
import { useState, useEffect, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  getContactConversation, getMe, getUsers,
  setConversationStatus, assignConversation, assignMeConversation,
} from '../../services/api.js';
import { hasPermission } from '../../utils/permissions.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';

const html = htm.bind(h);

function patchFromEvent(data) {
  const p = {};
  if (data.status !== undefined) p.status = data.status;
  if (data.assignee_user_id !== undefined) p.assignee_user_id = data.assignee_user_id;
  if (data.is_archived !== undefined) p.is_archived = data.is_archived;
  if (data.ai_active !== undefined) p.ai_active = data.ai_active;
  return p;
}

export function ConversationHeaderActions({ phone, sandbox = false }) {
  const [conv, setConv] = useState(null);
  const [user, setUser] = useState(null);
  const [users, setUsers] = useState([]);   // for "Transferir" — degrades on 403
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  // Identity (permission gating + assign-me).
  useEffect(() => {
    let alive = true;
    getMe()
      .then(r => { if (alive && r && r.ok && r.data && r.data.user) setUser(r.data.user); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // Assignable users for "Transferir" (requires users.manage; a 403 just hides it).
  useEffect(() => {
    let alive = true;
    getUsers()
      .then(r => { if (alive && r && r.ok && r.data && Array.isArray(r.data.users)) setUsers(r.data.users); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // Resolve the open conversation for this phone.
  const load = useCallback(() => {
    if (!phone || sandbox) { setConv(null); return; }
    getContactConversation(phone)
      .then(r => { if (r && r.ok) setConv((r.data && r.data.conversation) || null); })
      .catch(() => {});
  }, [phone, sandbox]);
  useEffect(() => { load(); }, [load]);

  // Live updates for THIS conversation (and pick up a freshly-created one).
  const convIdRef = useRef(null);
  useEffect(() => { convIdRef.current = conv ? conv.id : null; }, [conv]);
  const onConversationChanged = useCallback((name, data) => {
    const id = data && data.conversation_id;
    if (id == null) return;
    if (convIdRef.current != null && id === convIdRef.current) {
      setConv(prev => (prev ? { ...prev, ...patchFromEvent(data) } : prev));
    } else if (name === 'conversation_created') {
      load();
    }
  }, [load]);
  useWebSocket({ onConversationChanged });

  // Close the transfer menu on outside click.
  useEffect(() => {
    function onDoc(e) { if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false); }
    if (menuOpen) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [menuOpen]);

  if (!conv) return null;

  const isOpen = conv.status === 'open';
  const assignedToMe = user && conv.assignee_user_id != null && conv.assignee_user_id === user.id;
  const can = (p) => hasPermission(user, p);
  const userLabel = (id) => {
    const u = users.find(x => x.id === id);
    return u ? (u.name || u.email || `#${id}`) : `#${id}`;
  };

  async function run(fn) {
    if (busy) return;
    setBusy(true);
    try {
      const r = await fn();
      if (r && r.ok && r.data && r.data.conversation) setConv(r.data.conversation);
    } finally {
      setBusy(false);
    }
  }

  const btn = 'px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50 whitespace-nowrap';
  const canTransfer = can('conversation.assign') && users.length > 0;

  return html`
    <div class="flex items-center gap-1.5 shrink-0">
      <!-- Status / Resolver -->
      ${can('conversation.resolve') ? html`
        <button
          disabled=${busy}
          onClick=${() => run(() => setConversationStatus(conv.id, isOpen ? 'closed' : 'open'))}
          class=${btn}
          title=${isOpen ? 'Encerrar conversa' : 'Reabrir conversa'}
        >
          ${isOpen ? 'Resolver' : 'Reabrir'}
        </button>
      ` : html`
        <span class="px-2 py-0.5 rounded-full text-[11px] font-medium ${isOpen ? 'bg-wa-teal/15 text-wa-teal' : 'bg-wa-hover text-wa-secondary'}">
          ${isOpen ? 'Aberta' : 'Fechada'}
        </span>
      `}

      <!-- Atribuir a mim / responsável -->
      ${can('conversation.assign') ? html`
        ${assignedToMe
          ? html`
            <button disabled=${busy} onClick=${() => run(() => assignConversation(conv.id, null))} class=${btn} title="Remover atribuição">
              Atribuída a você
            </button>`
          : html`
            <button
              disabled=${busy}
              onClick=${() => run(() => assignMeConversation(conv.id))}
              class="px-2.5 py-1 rounded-md text-[12px] bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 transition-colors disabled:opacity-50 whitespace-nowrap"
              title="Assumir esta conversa"
            >
              Atribuir a mim
            </button>`}
      ` : (conv.assignee_user_id != null ? html`
        <span class="text-[12px] text-wa-secondary whitespace-nowrap">${userLabel(conv.assignee_user_id)}</span>
      ` : null)}

      <!-- Transferir (somente quem pode listar usuários) -->
      ${canTransfer ? html`
        <div class="relative" ref=${menuRef}>
          <button disabled=${busy} onClick=${() => setMenuOpen(o => !o)} class=${btn} title="Transferir para outro responsável">
            Transferir ▾
          </button>
          ${menuOpen ? html`
            <div class="absolute right-0 mt-1 bg-wa-bg rounded-lg shadow-lg border border-wa-border py-1 min-w-[180px] max-h-[260px] overflow-y-auto z-50">
              <button
                onClick=${() => { setMenuOpen(false); run(() => assignConversation(conv.id, null)); }}
                class="w-full text-left px-3 py-1.5 text-[13px] text-wa-secondary hover:bg-wa-hover"
              >Não atribuída</button>
              ${users.map(u => html`
                <button
                  key=${u.id}
                  onClick=${() => { setMenuOpen(false); run(() => assignConversation(conv.id, u.id)); }}
                  class="w-full text-left px-3 py-1.5 text-[13px] text-wa-text hover:bg-wa-hover ${conv.assignee_user_id === u.id ? 'text-wa-teal font-medium' : ''}"
                >${u.name || u.email || `#${u.id}`}</button>
              `)}
            </div>
          ` : null}
        </div>
      ` : null}
    </div>
  `;
}

export default ConversationHeaderActions;
