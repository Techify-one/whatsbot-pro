// Barra de busca DENTRO da conversa — plano 99 · F2.
//
// Ela SUBSTITUI o header do chat enquanto está aberta, em vez de espremer mais um
// ícone ali (o header é `h-[59px]` com `pr-[56px]` e já carrega nome, selo de
// canal, etiquetas, ações de conversa e o botão de informações). É também o que o
// WhatsApp faz — e mantém intactos os pontos de extensão de plugin do chat, que
// ficam abaixo do header.
//
// ⚠️ O `pr-[56px]` do container NÃO é enfeite: o botão flutuante da engrenagem
// fica por cima do canto superior direito do painel, e o header normal reserva
// exatamente essa faixa. Sem ela, o último controle da barra fica ESCONDIDO
// atrás da engrenagem — foi o que aconteceu com o ícone de calendário, que por
// isso passou a morar junto da lupa (à esquerda), como no WhatsApp.
//
// A barra NÃO sabe rolar nem carregar nada: ela só descobre ONDE estão as
// ocorrências e pede o salto ao dono dos dados (`onJump`), que usa a mesma infra
// de janela ancorada dos outros três caminhos de salto (F0e).

import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { searchInConversation } from '../../services/api.js';
import { DatePickerPopover } from './DatePickerPopover.js';

const html = htm.bind(h);

// ⚠️ NUNCA use crase dentro do template html abaixo — nem em comentário HTML.
// A crase FECHA o template literal, o componente vira lixo em silêncio e o
// header do chat some sem erro no console. Pior: "node --check" NÃO pega,
// porque um par de crases deixa o arquivo sintaticamente válido.
//
// A classe .wa-field fica NO INPUT (padrão da casa): é ela que garante fundo e
// cor de texto legíveis nos dois temas, além do placeholder e do anel de foco
// no escuro. Num wrapper, o input cairia no branco padrão do navegador.

// Mesmo tempo do debounce da busca da sidebar (useConversationList.js) — dois
// campos de busca no mesmo painel com cadências diferentes ficam estranhos.
const DEBOUNCE_MS = 300;
// Piso do backend (`TRIGRAM_MIN_LEN`): abaixo disso o índice trigram não é
// aplicável e o servidor devolve vazio. Espelhado aqui só para o rodapé poder
// explicar o silêncio em vez de parecer "não achou nada".
const MIN_LEN = 3;
const PAGE = 50;

const SearchIcon = () => html`
  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
    <path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>`;
const CloseIcon = () => html`
  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>`;
const UpIcon = () => html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg>`;
const DownIcon = () => html`
  <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M7.41 8.59L12 13.17l4.58-4.58L18 10l-6 6-6-6z"/></svg>`;
const CalendarIcon = () => html`
  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
    <path d="M19 3h-1V1h-2v2H8V1H6v2H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zm0 16H5V9h14v10zM5 7V5h14v2H5z"/></svg>`;

/**
 * @param {Object} props
 * @param {number|null} props.conversationId
 * @param {string} props.term
 * @param {(t:string)=>void} props.onTermChange - o pai destaca o termo nas bolhas
 * @param {(msgId:number)=>void} props.onJump
 * @param {(ts:number)=>void} props.onPickDate
 * @param {()=>void} props.onBackToBottom
 * @param {()=>void} props.onClose
 * @param {number|null} [props.refTs] - epoch (s) que abre o calendário no mês certo
 */
export function ConversationSearchBar({ conversationId, term, onTermChange, onJump,
                                        onPickDate, onBackToBottom, onClose,
                                        refTs = null }) {
  const [matches, setMatches] = useState([]);
  const [total, setTotal] = useState(0);
  const [index, setIndex] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [showCalendar, setShowCalendar] = useState(false);
  const inputRef = useRef(null);
  // Token de sequência: uma resposta que chega depois de o termo ter mudado é
  // DESCARTADA. Sem isso, digitar rápido deixa o contador mostrando o resultado
  // de um termo que já não está no campo (mesmo padrão do `detailSeqRef`).
  const seqRef = useRef(0);
  const mountedRef = useRef(true);
  const queryAbortRef = useRef(null);
  const pageAbortRef = useRef(null);
  const pageBusyRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    if (inputRef.current) inputRef.current.focus();
    return () => {
      mountedRef.current = false;
      seqRef.current += 1;
      if (queryAbortRef.current) queryAbortRef.current.abort();
      if (pageAbortRef.current) pageAbortRef.current.abort();
      queryAbortRef.current = null;
      pageAbortRef.current = null;
      pageBusyRef.current = false;
    };
  }, []);

  // Busca com debounce. Termo curto não vai ao servidor — o piso é conhecido.
  useEffect(() => {
    const q = (term || '').trim();
    const token = ++seqRef.current;
    if (queryAbortRef.current) queryAbortRef.current.abort();
    if (pageAbortRef.current) pageAbortRef.current.abort();
    queryAbortRef.current = null;
    pageAbortRef.current = null;
    pageBusyRef.current = false;
    // O resultado anterior não representa mais o campo atual: desabilita setas
    // imediatamente, inclusive durante debounce/erro de rede.
    setMatches([]); setTotal(0); setIndex(-1);
    if (conversationId == null || q.length < MIN_LEN) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const controller = new AbortController();
    queryAbortRef.current = controller;
    const t = setTimeout(() => {
      searchInConversation(conversationId, q,
                           { limit: PAGE, offset: 0, signal: controller.signal })
        .then(res => {
          if (!mountedRef.current || token !== seqRef.current) return;
          setLoading(false);
          if (!res.ok) { setMatches([]); setTotal(0); setIndex(-1); return; }
          const list = res.data.matches || [];
          setMatches(list);
          setTotal(res.data.total || 0);
          setIndex(list.length ? 0 : -1);
          if (list.length) onJump(list[0].id);
        })
        .catch(() => {
          if (!mountedRef.current || token !== seqRef.current) return;
          setLoading(false); setMatches([]); setTotal(0); setIndex(-1);
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(t);
      controller.abort();
      if (queryAbortRef.current === controller) queryAbortRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [term, conversationId]);

  // Anda `delta` ocorrências. As ocorrências vêm da mais RECENTE para a mais
  // antiga (a ordem do WhatsApp), então "⌄" anda para o passado. Ao chegar no fim
  // da página carregada, busca a seguinte por offset em vez de travar no 50º.
  const step = useCallback((delta) => {
    if (!matches.length || pageBusyRef.current) return;
    const next = index + delta;
    if (next < 0) return;
    if (next < matches.length) {
      setIndex(next);
      onJump(matches[next].id);
      return;
    }
    if (matches.length >= total) return;   // acabou de verdade
    const token = seqRef.current;
    const controller = new AbortController();
    if (pageAbortRef.current) pageAbortRef.current.abort();
    pageAbortRef.current = controller;
    pageBusyRef.current = true;
    setLoading(true);
    searchInConversation(conversationId, (term || '').trim(),
                         { limit: PAGE, offset: matches.length,
                           signal: controller.signal })
      .then(res => {
        if (!mountedRef.current || token !== seqRef.current) return;
        if (!res.ok) return;
        const more = res.data.matches || [];
        if (!more.length) return;
        setMatches(prev => [...prev, ...more]);
        setIndex(next);
        onJump(more[0].id);
      })
      .catch(() => {})
      .finally(() => {
        if (pageAbortRef.current !== controller) return;
        pageAbortRef.current = null;
        pageBusyRef.current = false;
        if (mountedRef.current && token === seqRef.current) setLoading(false);
      });
  }, [matches, index, total, conversationId, term, onJump]);

  function onKeyDown(e) {
    if (e.key === 'Escape') { e.preventDefault(); onClose(); return; }
    if (e.key === 'Enter') { e.preventDefault(); step(e.shiftKey ? -1 : 1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); step(1); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); step(-1); }
  }

  const short = (term || '').trim().length > 0 && (term || '').trim().length < MIN_LEN;
  const counter = short
    ? `mínimo de ${MIN_LEN} letras`
    : loading ? '…'
    : total > 0 ? `${index + 1} de ${total}`
    : (term || '').trim() ? 'nenhuma' : '';

  return html`
    <div class="h-[59px] flex items-center gap-2 pl-3 pr-[56px] bg-wa-panel border-b border-wa-border shrink-0 relative">
      <button type="button" onClick=${onClose} title="Fechar busca"
        class="shrink-0 text-wa-icon hover:text-wa-text p-[6px] rounded-full hover:bg-wa-hover transition-colors">
        <${CloseIcon} />
      </button>
      <span class="shrink-0 text-wa-secondary"><${SearchIcon} /></span>
      <div class="shrink-0 relative">
        <button type="button" data-date-picker-toggle="true"
          onClick=${() => setShowCalendar(v => !v)} title="Ir para uma data"
          class="text-wa-icon hover:text-wa-text p-[6px] rounded-full hover:bg-wa-hover transition-colors">
          <${CalendarIcon} />
        </button>
        ${showCalendar ? html`
          <${DatePickerPopover} refTs=${refTs}
            onPick=${(ts) => onPickDate(ts)}
            onBackToBottom=${onBackToBottom}
            onClose=${() => setShowCalendar(false)} />` : null}
      </div>
      <input
        ref=${inputRef}
        type="text"
        value=${term}
        onInput=${(e) => onTermChange(e.target.value)}
        onKeyDown=${onKeyDown}
        placeholder="Pesquisar nesta conversa"
        class="wa-field flex-1 min-w-0 rounded-lg px-3 h-[36px] text-[14px] outline-none
               border border-wa-border focus:border-wa-teal"
      />
      ${counter ? html`
        <span class="shrink-0 text-wa-secondary text-[12px] whitespace-nowrap">${counter}</span>` : null}
      <button type="button" onClick=${() => step(1)} title="Ocorrência anterior (↑)"
        disabled=${!matches.length || index + 1 >= total}
        class=${'shrink-0 p-[6px] rounded-full transition-colors ' + (!matches.length || index + 1 >= total
          ? 'text-wa-secondary opacity-40 cursor-not-allowed'
          : 'text-wa-icon hover:text-wa-text hover:bg-wa-hover')}>
        <${UpIcon} />
      </button>
      <button type="button" onClick=${() => step(-1)} title="Próxima ocorrência (↓)"
        disabled=${index <= 0}
        class=${'shrink-0 p-[6px] rounded-full transition-colors ' + (index <= 0
          ? 'text-wa-secondary opacity-40 cursor-not-allowed'
          : 'text-wa-icon hover:text-wa-text hover:bg-wa-hover')}>
        <${DownIcon} />
      </button>
    </div>`;
}
