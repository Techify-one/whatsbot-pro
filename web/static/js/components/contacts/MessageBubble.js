import { h } from 'preact';
import htm from 'htm';
import { formatBubbleTime } from './utils.js';
import { SingleCheckIcon, DoubleCheckIcon, ClockIcon, FailedIcon, RetryIcon } from './icons.js';
import { MediaContent } from './MediaContent.js';
import { stripGroupPrefix } from '../../services/composerTokens.js';
import { senderColor } from '../../services/messageView.js';

const html = htm.bind(h);

// ── A single chat bubble (user / assistant) ──────────────────────────
//
// Extracted verbatim from the bubble branch in ContactDetail.js: sender label,
// reply-quote (with jump-to-message), media body (delegated to MediaContent),
// revoked notice, status ticks (sandbox hides them), and the reactions chip.
//
// Props are the container-level helpers it needs (kept as props so the bubble
// stays presentational): `fmt` (WhatsApp formatting), `displayName`, `sandbox`,
// `isGroup`, `findQuoted`/`quotedInfo` (reply lookup), `focusMessage` (scroll to
// the quoted message), `openMsgMenu`, `myReaction`, `handleRetry`.
export function MessageBubble({
  message: m, index: i, isFirst,
  isGroup, sandbox, displayName, fmt,
  findQuoted, quotedInfo, focusMessage, openMsgMenu, myReaction, handleRetry,
  showAgentName = true,
  // plano 99 F0e·4: o container sabe pedir ao servidor a janela ANCORADA numa
  // mensagem, então a citação cujo alvo caiu fora da página carregada deixou de
  // ser um beco sem saída e volta a ser clicável.
  canJumpOutsideWindow = false,
  // plano 51 (04 F1): batch selection mode. Presentational only — the container
  // owns the Set; here we just render the checkbox/realce and route the click.
  selectionMode = false, selected = false, onToggleSelect = null,
}) {
  const isUser = m.role === 'user';
  const isFailed = m._status === 'failed' || m.status === 'failed';
  const isSending = m._status === 'sending';
  const isOperator = !isUser && m.status === 'operator';

  // In groups, the backend prefixes user content with "[Sender Name]: text"
  // for LLM context. Strip the prefix here and use the sender name as label.
  let displayContent = m.content;
  let groupSender = null;
  if (isUser && isGroup && typeof m.content === 'string') {
    const { sender, text } = stripGroupPrefix(m.content);
    if (sender != null) { groupSender = sender; displayContent = text; }
  }

  // Which side the bubble sits on. In sandbox you ARE the customer,
  // so your 'user' messages go right and the IA's replies go left —
  // the opposite of the contact chat (viewed by the operator).
  const isFromMe = sandbox ? isUser : !isUser;
  // Rótulo da IA: "IA - <nome do agente>" quando o toggle está ligado e a mensagem
  // carrega o agente que a produziu; senão apenas "IA".
  const aiLabel = (showAgentName && m.agent_name) ? `IA - ${m.agent_name}` : 'IA';
  const senderLabel = sandbox
    ? (isUser ? 'Você' : aiLabel)
    : (isUser ? (groupSender || displayName) : (isOperator ? (m.sent_by_name || 'Manual') : aiLabel));
  const sColor = senderColor(isUser, isOperator);

  return html`
    <div key=${m._localId || i} data-mid=${m._id}
      onClick=${(selectionMode && onToggleSelect) ? (() => onToggleSelect(m)) : null}
      class="flex ${isFromMe ? 'justify-end' : 'justify-start'} ${isFirst ? 'mt-[12px]' : 'mt-[2px]'} ${(m.reactions && Object.keys(m.reactions).length) ? 'mb-[14px]' : ''}${selectionMode ? ` relative pl-[34px] cursor-pointer rounded-[8px] ${selected ? 'bg-wa-teal/10' : 'hover:bg-wa-hover/60'}` : ''}">
      ${selectionMode ? html`
        <span class="absolute left-[6px] top-1/2 -translate-y-1/2 w-[20px] h-[20px] rounded-full border-2 flex items-center justify-center shrink-0 ${selected ? 'bg-wa-teal border-wa-teal' : 'border-wa-secondary bg-wa-panel'}">
          ${selected ? html`<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="white" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg>` : ''}
        </span>
      ` : ''}
      <div
        onContextMenu=${selectionMode ? null : ((e) => openMsgMenu(e, m, isFromMe))}
        class="wa-bubble group max-w-[65%] rounded-[7.5px] px-[9px] pt-[6px] pb-[8px] text-[14.2px] leading-[19px] whitespace-pre-wrap relative ${
        !isFromMe
          ? `bg-wa-incoming text-wa-text ${isFirst ? 'msg-tail-in rounded-tl-none' : ''}`
          : `${isFailed ? 'text-wa-text' : 'bg-wa-outgoing text-wa-text'} ${isFirst ? 'msg-tail-out rounded-tr-none' : ''}`
      }" style="${isFailed ? 'background: #fce8e8;' : ''}">
        ${selectionMode ? '' : html`<button
          onClick=${(e) => openMsgMenu(e, m, isFromMe)}
          class="absolute top-[2px] right-[2px] opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity rounded-full p-[1px] hover:bg-black/10"
          title="Opções da mensagem"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" class="text-wa-secondary">
            <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/>
          </svg>
        </button>`}
        <span class="block text-[11px] font-semibold leading-[13px] mb-[2px] truncate" style="color: ${sColor};">${senderLabel}</span>
        ${(!m.revoked && m.reply_to_msg_id) ? (() => {
          const qmsg = findQuoted(m.reply_to_msg_id, m);
          const q = quotedInfo(qmsg);
          const accent = q ? q.senderColor : '#8696a0';
          // Plano 75 F10: a citação hidratada pelo servidor mostra o CONTEÚDO mesmo
          // com o alvo fora da página carregada. Sem citação nenhuma (alvo apagado /
          // nunca recebido) segue o texto de indisponível.
          // Plano 99 F0e·4: o alvo `_hydrated` (fora da janela) VOLTA a ser clicável
          // quando o container sabe pedir a janela ancorada nele — antes o clique
          // ficava desligado porque não dava para rolar até uma linha ausente do DOM.
          const canJump = !!(qmsg && qmsg._id != null
                             && (!qmsg._hydrated || canJumpOutsideWindow));
          return html`
            <div
              onClick=${canJump ? ((e) => { e.stopPropagation(); focusMessage(qmsg._id, { smooth: true }); }) : null}
              class="flex rounded-[4px] overflow-hidden mb-[4px] max-w-full ${canJump ? 'cursor-pointer hover:brightness-95' : ''}"
              style="background: rgba(0,0,0,0.06);"
              title=${canJump ? 'Ir para a mensagem' : ''}
            >
              <div class="w-[4px] shrink-0" style="background:${accent};"></div>
              <div class="px-[8px] py-[3px] min-w-0">
                <div class="text-[12px] font-semibold leading-[15px] truncate" style="color:${accent};">${q ? q.senderLabel : 'Mensagem'}</div>
                <div class="text-[12.5px] leading-[16px] text-wa-secondary truncate">${q ? q.snippet : 'Mensagem original indisponível'}</div>
              </div>
            </div>
          `;
        })() : ''}
        ${m.revoked ? '' : html`<${MediaContent} message=${m} displayContent=${displayContent} fmt=${fmt} selectionMode=${selectionMode} />`}
        ${m.revoked ? html`
          <span class="italic text-wa-secondary flex items-center gap-[5px] text-[12px] mt-[2px]">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8 0-1.85.63-3.55 1.69-4.9L16.9 18.31C15.55 19.37 13.85 20 12 20zm6.31-3.1L7.1 5.69C8.45 4.63 10.15 4 12 4c4.41 0 8 3.59 8 8 0 1.85-.63 3.55-1.69 4.9z"/></svg>
            ${m.revoke_scope === 'me' ? 'apagado para mim no WhatsApp' : 'apagado para todos no WhatsApp'}
          </span>
        ` : ''}
        <span class="float-right ml-[8px] mt-[4px] text-[11px] leading-[15px] whitespace-nowrap text-wa-secondary">
          ${(!m.revoked && m.edited_ts) ? html`<span class="italic mr-[3px]">editada</span>` : ''}
          ${(!isUser && !sandbox) ? (() => {
            if (isFailed) return html`<${FailedIcon} />${!m.media_type && m._localId ? html`<${RetryIcon} onClick=${() => handleRetry(m._localId, m.content)} />` : ''}`;
            if (isSending) return html`<${ClockIcon} />`;
            const st = m.status || m._status;
            if (st === 'sent') return html`<${SingleCheckIcon} />`;
            if (st === 'delivered') return html`<${DoubleCheckIcon} color="#92a58c" />`;
            if (st === 'read') return html`<${DoubleCheckIcon} />`;
            if (st === 'operator') return html`<${DoubleCheckIcon} color="#92a58c" />`;
            return html`<${DoubleCheckIcon} />`;
          })() : ''}${formatBubbleTime(m.ts)}
        </span>
        ${(m.reactions && Object.keys(m.reactions).length) ? (() => {
          const entries = Object.entries(m.reactions).filter(([, rs]) => rs && rs.length);
          const total = entries.reduce((n, [, rs]) => n + rs.length, 0);
          const mine = myReaction(m);
          return html`
            <button
              onClick=${(e) => openMsgMenu(e, m, isFromMe)}
              class="absolute -bottom-[11px] ${isFromMe ? 'right-[6px]' : 'left-[6px]'} bg-wa-panel border border-wa-border rounded-full px-[5px] py-[1px] text-[12px] leading-[16px] shadow-sm flex items-center gap-[1px]"
              title="${mine ? 'Sua reação: ' + mine : 'Reações'}"
            >
              ${entries.map(([em]) => html`<span key=${em}>${em}</span>`)}
              ${total > 1 ? html`<span class="text-wa-secondary ml-[1px]">${total}</span>` : ''}
            </button>
          `;
        })() : ''}
      </div>
    </div>
  `;
}
