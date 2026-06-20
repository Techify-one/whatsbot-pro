// Conversations screen (plano 01 Fase 2 — UI). Full-page.
//
// Lists conversations with the contact name (group icon when applicable),
// phone, status badge (Aberta/Fechada), relative last-activity time and the
// assignee (user name or "Não atribuída"). Filters by status, archived and —
// when /api/users is reachable — by assignee. Each row offers actions: toggle
// open/closed, assign to me / unassign, and archive/unarchive.
//
// Clicking a conversation opens the contact's chat in the main panel: it pushes
// `/contacts/{contact_id}` and dispatches popstate, which is exactly how the
// Contacts screen navigates internally (app.js maps that route to the contacts
// tab and resolves the id → phone → opens the chat).

import { h } from 'preact';
import { useEffect, useState, useCallback, useMemo, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  listConversations,
  filterConversations,
  setConversationStatus,
  assignConversation,
  archiveConversation,
  getMe,
  getUsers,
} from '../services/api.js';
import { FilterBar } from './FilterBar.js';
import { useWebSocket } from '../hooks/useWebSocket.js';

const html = htm.bind(h);

// ── Helpers ─────────────────────────────────────────────────────────

// Format an epoch (seconds, float) into a relative "há ..." label.
function relativeTime(epochSeconds) {
  if (!epochSeconds) return '—';
  const then = epochSeconds * 1000;
  const diff = Date.now() - then;
  if (diff < 0) return 'agora';
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return 'há instantes';
  const min = Math.floor(sec / 60);
  if (min < 60) return `há ${min} min`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `há ${hr} h`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `há ${day} d`;
  try {
    return new Date(then).toLocaleDateString('pt-BR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
    });
  } catch (e) {
    return '—';
  }
}

function GroupIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" class="text-wa-secondary shrink-0" aria-label="Grupo">
      <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/>
    </svg>
  `;
}

function StatusBadge({ status }) {
  const open = status === 'open';
  const cls = open
    ? 'bg-wa-teal/15 text-wa-teal'
    : 'bg-wa-hover text-wa-secondary';
  return html`
    <span class="px-2 py-0.5 rounded-full text-[11px] font-medium ${cls}">
      ${open ? 'Aberta' : 'Fechada'}
    </span>
  `;
}

// ── Single conversation row ─────────────────────────────────────────

function ConversationRow({ convo, userNameById, currentUserId, canListUsers, onOpenChat, onAction }) {
  const [busy, setBusy] = useState(false);

  const assigneeName = convo.assignee_user_id != null
    ? (userNameById.get(convo.assignee_user_id) || `Usuário #${convo.assignee_user_id}`)
    : null;
  const assignedToMe = currentUserId != null && convo.assignee_user_id === currentUserId;
  const isOpen = convo.status === 'open';

  async function run(fn) {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }

  const name = convo.contact_name || convo.contact_phone || `Conversa #${convo.display_id ?? convo.id}`;

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-3 flex flex-col gap-2">
      <div class="flex items-start gap-3 flex-wrap">
        <button
          onClick=${() => onOpenChat(convo)}
          class="flex-1 min-w-0 text-left group"
          title="Abrir conversa"
        >
          <div class="flex items-center gap-1.5 min-w-0">
            ${convo.contact_is_group ? html`<${GroupIcon} />` : null}
            <span class="text-[15px] font-medium text-wa-text truncate group-hover:text-wa-teal transition-colors">
              ${name}
            </span>
            ${convo.display_id != null
              ? html`<span class="text-[11px] text-wa-secondary shrink-0">#${convo.display_id}</span>`
              : null}
          </div>
          <div class="flex items-center gap-2 mt-0.5 text-[12px] text-wa-secondary flex-wrap">
            ${convo.contact_phone ? html`<span class="truncate">${convo.contact_phone}</span>` : null}
            <span aria-hidden="true">·</span>
            <span>${relativeTime(convo.last_activity_at || convo.opened_at)}</span>
          </div>
        </button>

        <div class="flex items-center gap-2 shrink-0">
          <${StatusBadge} status=${convo.status} />
        </div>
      </div>

      <div class="flex items-center justify-between gap-2 flex-wrap">
        <div class="text-[12px] text-wa-secondary flex items-center gap-1">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" class="shrink-0">
            <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
          </svg>
          <span class="${assigneeName ? 'text-wa-text' : ''}">
            ${assigneeName || 'Não atribuída'}
          </span>
        </div>

        <div class="flex items-center gap-1.5 flex-wrap">
          <button
            disabled=${busy}
            onClick=${() => run(() => onAction(convo, 'status', isOpen ? 'closed' : 'open'))}
            class="px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
          >
            ${isOpen ? 'Fechar' : 'Reabrir'}
          </button>

          ${currentUserId != null ? html`
            ${assignedToMe
              ? html`
                <button
                  disabled=${busy}
                  onClick=${() => run(() => onAction(convo, 'assign', null))}
                  class="px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
                >
                  Remover atribuição
                </button>`
              : html`
                <button
                  disabled=${busy}
                  onClick=${() => run(() => onAction(convo, 'assign', currentUserId))}
                  class="px-2.5 py-1 rounded-md text-[12px] bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 transition-colors disabled:opacity-50"
                >
                  Atribuir a mim
                </button>`}
          ` : null}

          ${(currentUserId == null && convo.assignee_user_id != null) ? html`
            <button
              disabled=${busy}
              onClick=${() => run(() => onAction(convo, 'assign', null))}
              class="px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
            >
              Remover atribuição
            </button>
          ` : null}

          <button
            disabled=${busy}
            onClick=${() => run(() => onAction(convo, 'archive', !convo.is_archived))}
            class="px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
          >
            ${convo.is_archived ? 'Desarquivar' : 'Arquivar'}
          </button>
        </div>
      </div>
    </div>
  `;
}

// ── Main screen ─────────────────────────────────────────────────────

export function Conversations() {
  const [conversations, setConversations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Rich filters from the FilterBar (flat { key: value } map ready for
  // filterConversations). Empty object → fall back to the plain list endpoint.
  const [richFilters, setRichFilters] = useState({});

  // Identity / users (best-effort — degrade if forbidden)
  const [currentUserId, setCurrentUserId] = useState(null);
  const [users, setUsers] = useState([]);
  const [canListUsers, setCanListUsers] = useState(false);

  const userNameById = useMemo(() => {
    const m = new Map();
    for (const u of users) m.set(u.id, u.name || u.email || `Usuário #${u.id}`);
    return m;
  }, [users]);

  // Who am I (for "Atribuir a mim"). Legacy single-password sessions have no
  // user → currentUserId stays null and the assign buttons hide.
  useEffect(() => {
    let alive = true;
    getMe()
      .then((res) => {
        if (!alive) return;
        if (res && res.ok && res.data && res.data.user) {
          setCurrentUserId(res.data.user.id);
        }
      })
      .catch(() => { /* ignore */ });
    return () => { alive = false; };
  }, []);

  // List of users for the assignee filter + name resolution. Requires
  // users.manage; a 403 just degrades the UI (no filter, names show as #id).
  useEffect(() => {
    let alive = true;
    getUsers()
      .then((res) => {
        if (!alive) return;
        if (res && res.ok && res.data && Array.isArray(res.data.users)) {
          setUsers(res.data.users);
          setCanListUsers(true);
        }
      })
      .catch(() => { /* ignore */ });
    return () => { alive = false; };
  }, []);

  const hasRichFilters = useMemo(
    () => Object.keys(richFilters).length > 0,
    [richFilters],
  );

  const fetchConversations = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      let res;
      if (Object.keys(richFilters).length > 0) {
        // Rich filter active → use the filter engine endpoint.
        res = await filterConversations({ limit: 200, ...richFilters });
      } else {
        // No filters → keep the original simple list behaviour (open, not archived).
        res = await listConversations({ limit: 200, status: 'open', archived: 'false' });
      }
      if (res && res.ok) {
        setConversations((res.data && res.data.conversations) || []);
      } else {
        setError((res && res.error) || 'Falha ao carregar conversas.');
        setConversations([]);
      }
    } catch (e) {
      setError('Falha ao carregar conversas.');
      setConversations([]);
    } finally {
      setLoading(false);
    }
  }, [richFilters]);

  useEffect(() => { fetchConversations(); }, [fetchConversations]);

  // ── Tempo real (FF2) ────────────────────────────────────────────────
  // Patch the touched row immediately for snappiness, then refetch (debounced)
  // so membership/ordering reconcile against the active filter — the strategy
  // the plano prescribes for the MVP.
  const fetchRef = useRef(fetchConversations);
  useEffect(() => { fetchRef.current = fetchConversations; }, [fetchConversations]);
  const debounceRef = useRef(null);

  const onConversationChanged = useCallback((name, data) => {
    const id = data && data.conversation_id;
    if (id != null) {
      setConversations(prev => prev.map(c => {
        if (c.id !== id) return c;
        const patch = {};
        if (data.status !== undefined) patch.status = data.status;
        if (data.assignee_user_id !== undefined) patch.assignee_user_id = data.assignee_user_id;
        if (data.is_archived !== undefined) patch.is_archived = data.is_archived;
        if (data.ai_active !== undefined) patch.ai_active = data.ai_active;
        return { ...c, ...patch };
      }));
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => { fetchRef.current(); }, 700);
  }, []);

  useWebSocket({ onConversationChanged });

  // Open the contact's chat in the main panel. Mirrors how the Contacts screen
  // navigates: push /contacts/{id} and notify the router via popstate.
  const openChat = useCallback((convo) => {
    if (convo.contact_id == null) return;
    history.pushState(null, '', `/contacts/${convo.contact_id}`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, []);

  // Apply an action, then refetch so the list reflects the current filters
  // (e.g. closing a conversation removes it from the "Abertas" view).
  const handleAction = useCallback(async (convo, kind, value) => {
    try {
      let res;
      if (kind === 'status') res = await setConversationStatus(convo.id, value);
      else if (kind === 'assign') res = await assignConversation(convo.id, value);
      else if (kind === 'archive') res = await archiveConversation(convo.id, value);
      if (res && res.ok === false) {
        setError(res.error || 'Falha na ação.');
        return;
      }
    } catch (e) {
      setError('Falha na ação.');
      return;
    }
    await fetchConversations();
  }, [fetchConversations]);

  return html`
    <div>
      <!-- Rich filter bar (schema-driven). Lifts its state up via onChange. -->
      <${FilterBar} onChange=${setRichFilters} />

      <div class="flex items-center justify-end mb-3">
        <button
          onClick=${fetchConversations}
          class="px-3 py-2 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors"
        >
          Atualizar
        </button>
      </div>

      ${error ? html`
        <div class="mb-4 px-3 py-2 rounded-md bg-red-50 text-red-600 text-[13px] border border-red-200">
          ${error}
        </div>
      ` : null}

      ${loading
        ? html`<div class="text-center text-wa-secondary py-12 animate-pulse-slow">Carregando conversas...</div>`
        : conversations.length === 0
          ? html`
            <div class="text-center text-wa-secondary py-12">
              <div class="text-[15px] mb-1">Nenhuma conversa encontrada</div>
              <div class="text-[13px]">
                ${hasRichFilters
                  ? 'Ajuste os filtros acima para ver outras conversas.'
                  : 'Nenhuma conversa aberta no momento.'}
              </div>
            </div>`
          : html`
            <div class="flex flex-col gap-2">
              ${conversations.map(c => html`
                <${ConversationRow}
                  key=${c.id}
                  convo=${c}
                  userNameById=${userNameById}
                  currentUserId=${currentUserId}
                  canListUsers=${canListUsers}
                  onOpenChat=${openChat}
                  onAction=${handleAction}
                />
              `)}
            </div>`}
    </div>
  `;
}

export default Conversations;
