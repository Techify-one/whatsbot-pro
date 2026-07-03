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
  getContactConversation, getConversation, getMe, getUsers, getCustomAttributes,
  setConversationStatus, assignConversation, assignMeConversation, setConversationAi,
} from '../../services/api.js';
import { hasPermission } from '../../utils/permissions.js';
import { CopyLinkButton } from '../../utils/copyDeepLink.js';
import { missingRequiredAttributes } from '../../utils/requiredAttributes.js';
import { RequiredAttributesModal } from './RequiredAttributesModal.js';
import { resolveConversation } from '../../utils/resolveConversation.js';
import { Slot } from '../../plugins/Slot.js';
import { getFilters } from '../../plugins/registry.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';

const html = htm.bind(h);

function patchFromEvent(data) {
  const p = {};
  if (data.status !== undefined) p.status = data.status;
  if (data.assignee_user_id !== undefined) p.assignee_user_id = data.assignee_user_id;
  if (data.is_archived !== undefined) p.is_archived = data.is_archived;
  if (data.ai_active !== undefined) p.ai_active = data.ai_active;
  // Saving conversation attributes (PUT /info) broadcasts them under `fields`;
  // keep them fresh so the Resolver guard sees the latest filled values.
  if (data.fields && data.fields.custom_attributes !== undefined) p.custom_attributes = data.fields.custom_attributes;
  return p;
}

export function ConversationHeaderActions({ phone, conversationId = null, sandbox = false, onOpenConversationInfo = null, onOpenContactInfo = null, contactInfo = null }) {
  const [conv, setConv] = useState(null);
  const [user, setUser] = useState(null);
  const [users, setUsers] = useState([]);   // for "Transferir" — degrades on 403
  const [busy, setBusy] = useState(false);
  const [convDefs, setConvDefs] = useState([]);   // conversation-scoped attribute defs
  const [contactDefs, setContactDefs] = useState([]);   // contact-scoped attribute defs
  const [missingAttrs, setMissingAttrs] = useState(null);   // { list, target } blocking resolve

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

  // Attribute definitions (conversation + contact scope) — to gate "Resolver" on
  // required ("Obrigatório preencher") attributes. Reload when the admin edits them.
  useEffect(() => {
    let alive = true;
    function load() {
      getCustomAttributes('conversation')
        .then(r => { if (alive && r && r.ok) setConvDefs(r.data || []); })
        .catch(() => {});
      getCustomAttributes('contact')
        .then(r => { if (alive && r && r.ok) setContactDefs(r.data || []); })
        .catch(() => {});
    }
    load();
    window.addEventListener('whatsbot:custom-attributes-changed', load);
    return () => { alive = false; window.removeEventListener('whatsbot:custom-attributes-changed', load); };
  }, []);

  // Resolve the open conversation. Atendimento-cêntrico (plano 11 D1): quando o chat
  // sabe o atendimento aberto (um canal), carrega ELA por id — getContactConversation
  // resolveria só uma dos atendimentos do número e mostraria ações do canal errado.
  const load = useCallback(() => {
    if (sandbox) { setConv(null); return; }
    if (conversationId != null) {
      getConversation(conversationId)
        .then(r => { if (r && r.ok) setConv((r.data && r.data.conversation) || null); })
        .catch(() => {});
      return;
    }
    if (!phone) { setConv(null); return; }
    getContactConversation(phone)
      .then(r => { if (r && r.ok) setConv((r.data && r.data.conversation) || null); })
      .catch(() => {});
  }, [phone, conversationId, sandbox]);
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

  // Resolver guard: an open conversation can only be closed once every required
  // ("Obrigatório preencher") attribute has a value — both conversation- and
  // contact-scoped. Conversation attributes take priority; once they're filled,
  // a second attempt surfaces any pending contact attributes. Missing ones open a
  // modal targeting the right panel; reopening (closed → open) is never blocked.
  function onStatusClick() {
    if (busy) return;
    if (!isOpen) { run(() => setConversationStatus(conv.id, 'open')); return; }
    // When a plugin owns the resolve flow (filter.conversation.beforeResolve), it collects
    // and saves the conversation attributes in its own pre-filled popup — skip the native
    // conversation-scope gate so the two popups don't stack. Contact-scope stays gated here
    // (the plugin popup doesn't handle contact attributes).
    if (!getFilters('filter.conversation.beforeResolve').length) {
      const convMissing = missingRequiredAttributes(convDefs, conv.custom_attributes);
      if (convMissing.length) { setMissingAttrs({ list: convMissing, target: 'conversation' }); return; }
    }
    const contactMissing = missingRequiredAttributes(contactDefs, contactInfo && contactInfo.custom_attributes);
    if (contactMissing.length) { setMissingAttrs({ list: contactMissing, target: 'contact' }); return; }
    // Plugin filter (filter.conversation.beforeResolve) runs after the native guard;
    // a plugin can show a popup / fill fields, or abort the close by returning null.
    run(() => resolveConversation(conv, 'closed'));
  }

  // Modal "OK": close it and open the panel that holds the pending attributes so
  // the operator can fill them right away.
  function onMissingConfirm() {
    const target = missingAttrs && missingAttrs.target;
    setMissingAttrs(null);
    if (target === 'contact') { if (onOpenContactInfo) onOpenContactInfo(); }
    else if (onOpenConversationInfo) onOpenConversationInfo();
  }

  const btn = 'px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50 whitespace-nowrap';

  return html`
    <div class="flex items-center gap-1.5 shrink-0">
      <!-- Copiar link do atendimento (permalink /conversations/<id>) — plano 24 -->
      ${!sandbox && conversationId != null ? html`
        <${CopyLinkButton} path=${`/conversations/${conversationId}`} title="Copiar link da conversa" />
      ` : null}
      <!-- Status / Resolver / Reabrir (reabrir volta p/ "Não atribuídas", sem responsável) -->
      ${can('conversation.resolve') ? html`
        <button
          disabled=${busy}
          onClick=${onStatusClick}
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

      <!-- Atribuir a mim / responsável (somente em atendimentos abertos) -->
      ${isOpen && can('conversation.assign') ? html`
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

      <!-- Extension point: plugins can inject extra conversation actions here. -->
      <${Slot} name="conversation.header.actions" ctx=${{ conv, user }} />

      ${missingAttrs ? html`
        <${RequiredAttributesModal} missing=${missingAttrs.list} onConfirm=${onMissingConfirm} />
      ` : null}
    </div>
  `;
}

