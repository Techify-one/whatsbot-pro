// Visão LISTA dos atendimentos — espelha o ConversationRow de Conversations.js,
// usando os helpers compartilhados (ui.js) e os mesmos handlers de ação do container.
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { relativeTime, GroupIcon, StatusBadge, ChannelBadge, LabelChip, nameOf } from './ui.js';
import { hasPermission } from '../../utils/permissions.js';

const html = htm.bind(h);

function ListRow({ convo, assigneeName, currentUserId, currentUser, showChannel, labels, onOpenChat, onAction }) {
  const [busy, setBusy] = useState(false);
  const assignedToMe = currentUserId != null && convo.assignee_user_id === currentUserId;
  const isOpen = convo.status === 'open';
  // P48: gate each inline action by the permission its backend call enforces.
  const canResolve = hasPermission(currentUser, 'conversation.resolve');
  const canAssign = hasPermission(currentUser, 'conversation.assign');
  const canArchive = hasPermission(currentUser, 'contact.write');

  async function run(fn) {
    if (busy) return;
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  }

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-3 flex flex-col gap-2">
      <div class="flex items-start gap-3 flex-wrap">
        <button onClick=${() => onOpenChat(convo)} class="flex-1 min-w-0 text-left group" title="Abrir atendimento">
          <div class="flex items-center gap-1.5 min-w-0">
            ${convo.contact_is_group ? html`<${GroupIcon} />` : null}
            <span class="text-[15px] font-medium text-wa-text truncate group-hover:text-wa-teal transition-colors">${nameOf(convo)}</span>
            ${convo.display_id != null ? html`<span class="text-[11px] text-wa-secondary shrink-0">#${convo.display_id}</span>` : null}
          </div>
          <div class="flex items-center gap-2 mt-0.5 text-[12px] text-wa-secondary flex-wrap">
            ${convo.contact_phone ? html`<span class="truncate">${convo.contact_phone}</span>` : null}
            <span aria-hidden="true">·</span>
            <span>${relativeTime(convo.last_activity_at || convo.opened_at)}</span>
          </div>
        </button>
        <div class="flex items-center gap-2 shrink-0">
          ${showChannel ? html`<${ChannelBadge} provider=${convo.channel_provider} name=${convo.channel_name} />` : null}
          <${StatusBadge} status=${convo.status} />
        </div>
      </div>

      ${labels && labels.length ? html`
        <div class="flex flex-wrap gap-1">${labels.map(l => html`<${LabelChip} key=${l.name} name=${l.name} color=${l.color} />`)}</div>
      ` : null}

      <div class="flex items-center justify-between gap-2 flex-wrap">
        <div class="text-[12px] ${assigneeName ? 'text-wa-text' : 'text-wa-secondary'}">${assigneeName || 'Não atribuído'}</div>
        <div class="flex items-center gap-1.5 flex-wrap">
          ${canResolve ? html`
          <button disabled=${busy} onClick=${() => run(() => onAction(convo, 'status', isOpen ? 'closed' : 'open'))}
            class="px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50">
            ${isOpen ? 'Fechar' : 'Reabrir'}
          </button>
          ` : null}
          ${canAssign && currentUserId != null ? (assignedToMe
            ? html`<button disabled=${busy} onClick=${() => run(() => onAction(convo, 'assign', null))}
                class="px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50">Remover atribuição</button>`
            : html`<button disabled=${busy} onClick=${() => run(() => onAction(convo, 'assign', currentUserId))}
                class="px-2.5 py-1 rounded-md text-[12px] bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 transition-colors disabled:opacity-50">Atribuir a mim</button>`) : null}
          ${canAssign && currentUserId == null && convo.assignee_user_id != null ? html`
            <button disabled=${busy} onClick=${() => run(() => onAction(convo, 'assign', null))}
              class="px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50">Remover atribuição</button>` : null}
          ${canArchive ? html`
          <button disabled=${busy} onClick=${() => run(() => onAction(convo, 'archive', !convo.is_archived))}
            class="px-2.5 py-1 rounded-md text-[12px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50">
            ${convo.is_archived ? 'Desarquivar' : 'Arquivar'}
          </button>
          ` : null}
        </div>
      </div>
    </div>
  `;
}

export function AttendanceList({ conversations, assigneeNameOf, currentUserId, currentUser = null, showChannel, labelsOf, onOpenChat, onAction }) {
  if (!conversations.length) {
    return html`<div class="text-center text-wa-secondary py-12 text-[14px]">Nenhum atendimento encontrado.</div>`;
  }
  return html`
    <div class="flex flex-col gap-2">
      ${conversations.map(c => html`
        <${ListRow} key=${c.id} convo=${c}
          assigneeName=${assigneeNameOf(c)} currentUserId=${currentUserId} currentUser=${currentUser}
          showChannel=${showChannel} labels=${labelsOf ? labelsOf(c) : []}
          onOpenChat=${onOpenChat} onAction=${onAction} />
      `)}
    </div>
  `;
}

