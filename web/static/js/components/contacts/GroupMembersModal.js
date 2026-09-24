import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { getGroupMembers } from '../../services/api.js';
import { isModifiedClick } from '../../services/spaLink.js';
import { memberEntries, membersCountLabel } from '../../services/groupMembers.js';
import { DefaultAvatar, CloseIcon, ChevronRightIcon } from './icons.js';

const html = htm.bind(h);

// Navega pelo mesmo par que o resto do app usa (pushState + popstate). Feito aqui e
// não deixado ao interceptor de links do shell (App.js): o painel de contato também
// abre dentro do overlay da tela de Contatos, cujo wrapper faz stopPropagation — o
// clique nunca chegaria ao `document` e o navegador recarregaria a página inteira.
function goTo(href) {
  history.pushState(null, '', href);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

// Popup "Membros do grupo" (botão "Ver membros" do ContactInfoPanel). Lista o roster
// do grupo; clicar num membro leva à tela de Contatos com o modal "Novo contato"
// pré-preenchido — que já bloqueia a criação e oferece "Ver detalhes" se o número é
// um contato salvo. Membro sem telefone real (só-LID) aparece, mas sem link.
//
//   groupJid  — JID do grupo (`…@g.us`)
//   groupName — nome do grupo (subtítulo)
//   channelId — canal da conversa: o roster sai do GOWAClient DAQUELE canal
//               (null = cliente padrão do app, como sempre)
export function GroupMembersModal({ groupJid, groupName = '', channelId = null, onClose }) {
  const [state, setState] = useState({ status: 'loading', members: [], error: null });
  const [query, setQuery] = useState('');
  const seq = useRef(0);

  function load(force) {
    const mine = ++seq.current;
    setState((prev) => ({ ...prev, status: 'loading', error: null }));
    const fail = (error) => {
      if (mine === seq.current) setState({ status: 'error', members: [], error });
    };
    getGroupMembers(groupJid, force, channelId)
      .then((res) => {
        if (mine !== seq.current) return;  // resposta obsoleta (outro grupo/atualizar)
        if (res && res.ok) {
          setState({ status: 'ready', members: (res.data && res.data.members) || [], error: null });
        } else {
          fail((res && res.error) || 'Falha ao carregar os membros.');
        }
      })
      .catch(() => fail('Falha ao carregar os membros.'));
  }

  useEffect(() => {
    load(false);
    return () => { seq.current += 1; };
  }, [groupJid, channelId]);

  useEffect(() => {
    // preventDefault = "tecla consumida": o Esc do hub (Contacts.js) só fecha o painel
    // lateral se ninguém a consumiu. Sem isso o re-render (microtask) já removeu este
    // overlay quando o handler do hub roda, e o Esc fecharia o popup E o painel juntos.
    function onKey(e) { if (e.key === 'Escape') { e.preventDefault(); onClose(); } }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  function handleLink(e, href) {
    // Ctrl/⌘/Shift/clique do meio: o navegador abre em outra guia/janela.
    if (isModifiedClick(e)) return;
    e.preventDefault();
    onClose();
    goTo(href);
  }

  const total = state.members.length;
  const entries = memberEntries(state.members, query);
  const loading = state.status === 'loading';
  const subtitle = [groupName, state.status === 'ready' ? membersCountLabel(total) : null]
    .filter(Boolean).join(' · ');

  return html`
    <div class="fixed inset-0 z-[130] bg-black/50 flex items-center justify-center p-4" onClick=${onClose}>
      <div
        class="bg-wa-panel rounded-xl shadow-xl border border-wa-border w-full max-w-md max-h-[80vh] flex flex-col"
        role="dialog" aria-modal="true" aria-label="Membros do grupo"
        onClick=${(e) => e.stopPropagation()}
      >
        <div class="flex items-center gap-3 p-4 border-b border-wa-border shrink-0">
          <div class="min-w-0 flex-1">
            <div class="text-[16px] font-semibold text-wa-text">Membros do grupo</div>
            <div class="text-[12px] text-wa-secondary truncate">
              ${subtitle}
            </div>
          </div>
          <button
            type="button"
            onClick=${() => load(true)}
            disabled=${loading}
            class="shrink-0 text-[13px] text-wa-teal font-medium px-2 py-1 rounded hover:bg-wa-hover transition-colors disabled:opacity-50"
            title="Buscar a lista de membros de novo"
          >Atualizar</button>
          <button
            type="button"
            onClick=${onClose}
            class="shrink-0 w-[34px] h-[34px] rounded-full flex items-center justify-center text-wa-secondary hover:bg-wa-hover transition-colors"
            title="Fechar"
          ><${CloseIcon} /></button>
        </div>

        ${total > 0 ? html`
          <div class="p-3 border-b border-wa-border shrink-0">
            <input
              type="text"
              value=${query}
              onInput=${(e) => setQuery(e.target.value)}
              placeholder="Buscar por nome ou número..."
              autoFocus
              class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none focus:border-wa-iconActive transition-colors"
            />
          </div>
        ` : null}

        <div class="flex-1 min-h-0 overflow-y-auto wa-scrollbar">
          ${loading && total === 0 ? html`
            <div class="p-6 text-center text-[14px] text-wa-secondary animate-pulse-slow">Carregando membros...</div>
          ` : state.status === 'error' ? html`
            <div class="p-6 text-center text-[14px] text-red-500">${state.error}</div>
          ` : total === 0 ? html`
            <div class="p-6 text-center text-[14px] text-wa-secondary">
              Não foi possível obter os membros deste grupo. Confira se o canal está conectado e clique em Atualizar.
            </div>
          ` : entries.length === 0 ? html`
            <div class="p-6 text-center text-[14px] text-wa-secondary">Nenhum membro encontrado.</div>
          ` : html`
            <div class="px-4 pt-3 pb-1 text-[12px] text-wa-secondary">Clique em um membro para criar o contato.</div>
            ${entries.map((e) => {
              const body = html`
                <div class="w-10 h-10 rounded-full overflow-hidden shrink-0"><${DefaultAvatar} size=${40} /></div>
                <div class="min-w-0 flex-1">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="text-[15px] text-wa-text truncate">${e.label}</span>
                    ${e.isAdmin ? html`<span class="shrink-0 text-[10px] font-semibold rounded px-[6px] py-[1px] leading-[15px] text-wa-teal bg-wa-teal/10 border border-wa-teal/30">Admin</span>` : null}
                  </div>
                  ${e.sub ? html`<div class="text-[12px] text-wa-secondary truncate">${e.sub}</div>` : null}
                </div>
              `;
              return e.href ? html`
                <a
                  key=${e.key}
                  href=${e.href}
                  onClick=${(ev) => handleLink(ev, e.href)}
                  title=${e.name ? `Criar ou ver o contato de ${e.name}` : 'Criar ou ver este contato'}
                  class="flex items-center gap-3 px-4 py-2.5 no-underline hover:bg-wa-hover transition-colors cursor-pointer"
                >
                  ${body}
                  <span class="shrink-0 text-wa-secondary"><${ChevronRightIcon} /></span>
                </a>
              ` : html`
                <div
                  key=${e.key}
                  class="flex items-center gap-3 px-4 py-2.5 opacity-60"
                  title="Sem número disponível — não dá para criar o contato"
                >${body}</div>
              `;
            })}
          `}
        </div>
      </div>
    </div>
  `;
}
