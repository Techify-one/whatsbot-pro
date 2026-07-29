// @ts-check
//
// Conversation-selection hook (Plano 23 · D2) — extracted verbatim from
// Contacts.js. Owns which thread is open (phone + conversation_id + channel_id,
// plus their refs for stale-closure-free WS routing), the loaded `contactData`
// (full contact + messages), the open side-drawer (`openPanel`), the
// search-hit scroll target, and the deep-link resolution.
//
// It emits the D1 client event `ui.conversation.selected` on every select /
// deselect (stable, minimal payload; fire-and-forget — emit() isolates throwing
// subscribers). The detail loader reuses the R12 dedup (`isDuplicateMessage`)
// for the pre-fetch / during-fetch buffer merge.
//
// Cross-hook wiring: `pageVisibleRef` (visibility) and `newConvChannelRef`
// (channel picker) are owned by the container and passed in; `setContacts` /
// `contactsRef` / `loading` come from the list hook; `isOpenRow` is derived here
// from the selection refs and consumed by the list/WS optimistic patches.
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import { getContact, getConversationMessages } from '../../../services/api.js';
import { applyThreadResponse, prependOlder } from '../../../services/threadData.js';
import { shapeConvData } from '../../../services/conversationRows.js';
import { resolveDeepLink } from '../../../services/deepLinkResolve.js';
import { emit as emitClientEvent } from '../../../plugins/registry.js';

/**
 * Chave canônica da thread aberta (plano 85 A2/A4) — a MESMA fórmula do `rowKeyFor`
 * da sidebar, para que o carimbo gravado em `contactData` possa ser comparado com a
 * seleção corrente no render do container. Preferir o atendimento ao telefone é o que
 * mantém a chave ESTÁVEL num deep-link `/conversations/:id` (que abre com `selected`
 * nulo e adota o telefone da resposta depois) — senão a chave mudaria no meio e o
 * painel piscaria "Carregando..." sem necessidade.
 *
 * @param {string|null} phone
 * @param {number|null} convId
 * @returns {string|null} `conv:<id>` | `phone:<n>` | null (nada selecionado)
 */
export function threadKeyOf(phone, convId) {
  if (convId != null) return `conv:${convId}`;
  return phone ? `phone:${phone}` : null;
}

/**
 * @param {Object} opts
 * @param {Record<string, any>[]} opts.contacts
 * @param {boolean} opts.loading
 * @param {(fn:any)=>void} opts.setContacts
 * @param {{ current: Record<string, any>[] }} opts.contactsRef
 * @param {{ current: boolean }} opts.pageVisibleRef
 * @param {{ current: string|null }} opts.newConvChannelRef
 * @param {number|null} [opts.initialContactId]
 * @param {number|null} [opts.initialConversationId]
 * @param {number|null} [opts.initialScrollMsgId]
 */
export function useConversationSelection({
  contacts, loading, setContacts, contactsRef,
  pageVisibleRef, newConvChannelRef,
  initialContactId, initialConversationId, initialScrollMsgId = null,
  // plano 72 F8 — reconcilia a aba Menções (serverMode) ao ler a menção da conversa
  // aberta (a leitura tira a linha da view, mas o clear otimista só zera o flag).
  // No-op fora da aba Menções. Default no-op p/ callers antigos.
  reconcileMentionsOnRead = () => {},
}) {
  const [selected, setSelected] = useState(null);          // open thread's phone (contact-level ops)
  const [selectedConvId, setSelectedConvId] = useState(null);   // open thread's conversation id
  const [selectedChannelId, setSelectedChannelId] = useState('default');  // open thread's channel
  const [scrollToMsg, setScrollToMsg] = useState(null);  // DB id of a message to focus on open (search hit)
  const [contactData, setContactData] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  // plano 85 A3 — falha de carregamento do detalhe. Antes o `if (res.ok)` simplesmente
  // não fazia nada num 404/500/403 e a promise rejeitada não era tratada: o painel
  // ficava mudo, exibindo a conversa anterior, sem erro e sem saída.
  const [detailError, setDetailError] = useState(null);
  // Incrementado pelo botão "Tentar de novo" E por um clique na linha JÁ selecionada —
  // as deps do efeito são `[selected, selectedConvId]`, então sem isto reclicar a mesma
  // conversa depois de uma falha era um no-op (nada re-tentava).
  const [retryNonce, setRetryNonce] = useState(0);
  const [loadingOlder, setLoadingOlder] = useState(false);  // plano 50 F4: scroll-up
  // Which side drawer is open: 'contact' (foto/nome) | 'conversation' (botão ℹ️) |
  // null. Single state so opening one closes the other (no overlapping drawers).
  const [openPanel, setOpenPanel] = useState(null);

  // plano 85 A2 — QUAL thread está carregada em `contactData` (era `hasLoadedDetail`,
  // um booleano one-shot da MONTAGEM: ele ligava o "Carregando..." só na primeira
  // conversa da sessão, então a partir da segunda o operador olhava a thread anterior
  // durante toda a carga sem nenhuma indicação visual). Guarda a chave de `threadKeyOf`
  // e o carregamento é ligado sempre que a thread pedida ≠ a thread carregada.
  const loadedThreadKeyRef = useRef(null);
  // plano 85 A1 — token de sequência do carregamento do DETALHE, espelhando o
  // `fetchSeqRef` que a LISTA ganhou no plano 62 F3. Só a carga mais recente pode
  // gravar `contactData`: uma resposta que chega depois de a seleção ter mudado
  // (clique rápido A→B, rede lenta, resposta fora de ordem) é DESCARTADA, em vez
  // de sobrescrever a thread aberta com a conversa anterior. Sem AbortController
  // de propósito (P1): o GET do atendimento marca a conversa como lida e agenda
  // recibos — abortar perderia esse efeito colateral na conversa recém-aberta.
  const detailSeqRef = useRef(0);
  const openInfoAfterSelect = useRef(false);
  const pendingWsMessages = useRef({});
  const selectedRef = useRef(null);
  const selectedConvIdRef = useRef(null);
  const selectedChannelIdRef = useRef('default');
  const lastResolvedId = useRef(null);
  const lastResolvedConvId = useRef(null);
  const contactDataRef = useRef(null);         // plano 50 F4: cursor sem stale-closure
  const loadingOlderRef = useRef(false);        // guarda contra loadOlder concorrente

  // Keep refs in sync — avoids stale closures
  useEffect(() => { contactDataRef.current = contactData; }, [contactData]);
  useEffect(() => { selectedRef.current = selected; }, [selected]);
  useEffect(() => { selectedConvIdRef.current = selectedConvId; }, [selectedConvId]);
  useEffect(() => { selectedChannelIdRef.current = selectedChannelId; }, [selectedChannelId]);

  // True when `row` is the currently-open thread (by conversation when available,
  // else by phone for legacy contact-only rows). Reads refs → safe in WS closures.
  const isOpenRow = useCallback((row) => {
    if (selectedConvIdRef.current != null) return row.conversation_id === selectedConvIdRef.current;
    return row.conversation_id == null && row.phone === selectedRef.current;
  }, []);

  // Push URL when selecting/deselecting a conversation row. Accepts a sidebar row
  // (conversation-centric) OR a bare phone string (legacy callers — start
  // conversation / context-menu edit), resolving the latter to its newest row.
  const retryDetail = useCallback(() => setRetryNonce(n => n + 1), []);

  const selectContact = useCallback((rowOrPhone, msgId = null) => {
    setScrollToMsg(msgId != null ? msgId : null);
    if (rowOrPhone == null) {
      setSelected(null);
      setSelectedConvId(null);
      setSelectedChannelId('default');
      history.pushState(null, '', '/');
      // Client-side plugin event (plano 23 §3.4) — conversation deselected.
      // Fire-and-forget; emit() isolates throwing subscribers, never blocks the UI.
      emitClientEvent('ui.conversation.selected', { conversationId: null, phone: null, channelId: null });
      return;
    }
    let row = rowOrPhone;
    if (typeof rowOrPhone === 'string') {
      row = contactsRef.current.find(c => c.phone === rowOrPhone)
        || { phone: rowOrPhone, conversation_id: null, channel_id: 'default', contact_id: null, id: null };
    }
    // plano 85 A3 — clique na linha JÁ selecionada re-tenta: as deps do efeito de carga
    // não mudam, então sem o nonce um painel travado numa falha não tinha como se
    // recuperar pela própria sidebar (só F5 ou abrir outra conversa).
    if (threadKeyOf(row.phone, row.conversation_id ?? null)
        === threadKeyOf(selectedRef.current, selectedConvIdRef.current)) {
      setRetryNonce(n => n + 1);
    }
    setSelected(row.phone);
    setSelectedConvId(row.conversation_id ?? null);
    setSelectedChannelId(row.channel_id || 'default');
    // Client-side plugin event (plano 23 §3.4): a conversation/row was selected.
    // Minimal + stable payload; fire-and-forget (emit() swallows subscriber errors).
    emitClientEvent('ui.conversation.selected', {
      conversationId: row.conversation_id ?? null,
      phone: row.phone ?? null,
      channelId: row.channel_id || 'default',
    });
    if (row.conversation_id != null) {
      history.pushState(null, '', `/conversations/${row.conversation_id}`);
    } else {
      // Sem atendimento ainda: a seleção fica no estado; /contacts/{id} agora é a tela
      // de detalhe do contato (não o hub), então a URL volta pra raiz do hub.
      history.pushState(null, '', '/');
    }
  }, [contactsRef]);

  // Resolve initialConversationId (/conversations/:id) or initialContactId
  // (/contacts/:id, legacy) → a sidebar row. A DECISÃO vive em services/deepLinkResolve.js
  // (puro, testado — plano 89 F1/F2); aqui só se APLICA a ação e se grava o carimbo.
  // `wait` não carimba nada, então o efeito fica ARMADO: qualquer coisa que mude as
  // deps (a lista chegar, uma busca, um upsert de WS) re-tenta sozinha.
  useEffect(() => {
    const decision = resolveDeepLink({
      initialConversationId, initialContactId, contacts, loading,
      lastResolvedConvId: lastResolvedConvId.current,
      lastResolvedId: lastResolvedId.current,
    });
    switch (decision.action) {
      case 'noop':
      case 'wait':
        return;
      case 'deselect':
        setSelected(null);
        setSelectedConvId(null);
        lastResolvedId.current = null;
        lastResolvedConvId.current = null;
        return;
      case 'select':
      case 'open_by_id': {
        if (decision.action === 'select') {
          const row = decision.row;
          setSelected(row.phone);
          setSelectedConvId(row.conversation_id ?? null);
          setSelectedChannelId(row.channel_id || 'default');
        } else {
          // Atendimento fora da sidebar (além da 1ª página, arquivado, ou fora da
          // view/filtro corrente — inclusive a lista inteiramente vazia, plano 89 F2):
          // abre direto por id; o loader deriva telefone/canal da resposta do endpoint
          // e a F3 abaixo adota da linha se ela aparecer antes disso.
          setSelected(null);
          setSelectedConvId(decision.conversationId);
        }
        if (decision.via === 'contact') {
          lastResolvedId.current = decision.contactId;
          lastResolvedConvId.current = null;
          return;
        }
        // Permalink (?message=<_id>): foca a mensagem assim que o atendimento renderiza.
        // Lido no momento da resolução e consumido uma vez (onScrolledToMsg limpa);
        // o atendimento carrega TODAS as mensagens, então o alvo está sempre no DOM.
        if (initialScrollMsgId != null) setScrollToMsg(initialScrollMsgId);
        lastResolvedConvId.current = initialConversationId;
        lastResolvedId.current = null;
        return;
      }
    }
  }, [initialContactId, initialConversationId, contacts, loading]);

  // Plano 89 F3 — ADOÇÃO TARDIA de telefone e canal. Com a F2, "abrir por id" deixou
  // de ser caso raro: entre a resolução e a resposta do endpoint, `selected` é nulo e
  // `selectedChannelId` fica em 'default' — compositor, presença e as chaves de
  // digitação (`typingKey`) operariam no canal errado. Se a linha aparecer na lista
  // nesse meio-tempo (paginação, busca, upsert de WS), adota-se dela na hora.
  // Sem corrida com o loader: os dois escrevem o MESMO valor, quem chegar primeiro
  // vence e o outro é no-op. E a chave da thread é conversa-primeiro (`conv:<id>`),
  // então adotar o telefone depois NÃO troca a chave nem pisca "Carregando..." (85 A2).
  useEffect(() => {
    if (selected || selectedConvId == null) return;
    const row = contacts.find(c => c.conversation_id === selectedConvId);
    if (!row || !row.phone) return;
    setSelected(row.phone);
    if (row.channel_id) setSelectedChannelId(row.channel_id);
  }, [contacts, selected, selectedConvId]);

  // Load chat detail when the open thread changes. Atendimento-cêntrico: prefer the
  // per-conversation endpoint (scoped to one channel); fall back to the legacy
  // per-contact endpoint for rows without a conversation.
  useEffect(() => {
    if (!selected && selectedConvId == null) {
      setContactData(null);
      loadedThreadKeyRef.current = null;
      setLoadingDetail(false);
      return;
    }
    if (openInfoAfterSelect.current) {
      openInfoAfterSelect.current = false;
      setOpenPanel('contact');
    } else {
      setOpenPanel(null);
    }
    // plano 85 A2 — carregamento por CONVERSA: liga sempre que a thread pedida não é a
    // que já está em `contactData`. Um refetch da MESMA thread (ex.: o segundo passe do
    // deep-link, que só adota o telefone da resposta) roda em silêncio, sem piscar.
    const threadKey = threadKeyOf(selected, selectedConvId);
    if (loadedThreadKeyRef.current !== threadKey) setLoadingDetail(true);
    setDetailError(null);   // plano 85 A3 — cada tentativa começa limpa
    // Preserve any messages already buffered for this thread (arrived before selection)
    // but reset the accumulator for new messages arriving during fetch
    const bufKey = selected || (selectedConvId != null ? `conv:${selectedConvId}` : '');
    const preFetchBuffer = pendingWsMessages.current[bufKey] || [];
    pendingWsMessages.current[bufKey] = [];
    // Clear unread badges immediately on the OPEN row (only if page is visible)
    const isPageVisible = pageVisibleRef.current;
    if (isPageVisible) {
      setContacts(prev => prev.map(c =>
        isOpenRow(c) ? { ...c, unread_count: 0, unread_ai_count: 0, has_unread_mention: false, has_user_mention: false } : c
      ));
      // plano 72 F8: se estamos na aba Menções (serverMode), ler a menção da conversa
      // aberta a remove da view server-filtrada — refetch reconcilia lista×badge (no-op
      // em qualquer outra view).
      reconcileMentionsOnRead();
    }
    const convId = selectedConvId;
    // Atendimento novo sem conversation_id ainda: escopa o getContact ao canal
    // escolhido no picker (lido-e-zerado aqui), para não fundir os canais nem
    // abrir o atendimento de outro canal do mesmo número (multicanal).
    const newConvChannel = newConvChannelRef.current;
    newConvChannelRef.current = null;
    const token = ++detailSeqRef.current;   // plano 85 A1
    const loader = convId != null
      ? getConversationMessages(convId, isPageVisible).then(res =>
          res.ok ? { ok: true, data: shapeConvData(res.data) } : res)
      : getContact(selected, isPageVisible, newConvChannel);
    loader.then(res => {
      if (token !== detailSeqRef.current) return;  // resposta obsoleta — descarta
      if (res.ok) {
        const data = res.data;
        if (data.channel_id) setSelectedChannelId(data.channel_id);
        // Opened by conversation id alone (row not in the sidebar) — adopt the
        // phone from the response so contact-level handlers key correctly.
        if (!selectedRef.current && data.phone) setSelected(data.phone);
        // Buffer de WS: pré-fetch (chegou antes do clique) + durante-fetch. O merge, a
        // hidratação dos `failed` e o carimbo da thread (plano 85 A4) vivem em
        // services/threadData.js — a MESMA regra dos outros dois call sites.
        const duringFetch = pendingWsMessages.current[bufKey] || [];
        const pending = [...preFetchBuffer, ...duringFetch];
        pendingWsMessages.current[bufKey] = [];
        setContactData(applyThreadResponse(data, pending, threadKey));
        loadedThreadKeyRef.current = threadKey;
      } else {
        // plano 85 A3 — 404/500/403 deixavam o painel mudo com a conversa anterior.
        // `handleErrorResponse` já normaliza a mensagem das duas formas de corpo.
        setDetailError(res.error || 'Não foi possível carregar esta conversa.');
      }
      setLoadingDetail(false);
    }).catch((e) => {
      // plano 85 A2/A3 — `httpClient.request` não engole rejeição (rede caiu, 502 num
      // redeploy): sem este catch a promise ficava não-tratada e `setLoadingDetail(false)`
      // nunca rodava — a tela travava em "Carregando..." para sempre.
      if (token !== detailSeqRef.current) return;
      if (e && e.name === 'AbortError') { setLoadingDetail(false); return; }
      setDetailError('Não foi possível carregar esta conversa. Verifique sua conexão.');
      setLoadingDetail(false);
    });
  }, [selected, selectedConvId, retryNonce]);

  // Plano 33 F2 — re-fetch the OPEN thread after a WS RECONNECT. The bus has no
  // replay, so a `new_message` that arrived during a connection gap (sleep / NAT
  // blip / half-open socket) is lost from the open thread — only the sidebar is
  // separately refetched. This ref-based, BACKGROUND reload recovers it. Reuses
  // the SAME per-conversation/per-contact loader and the R12 dedup, so it is
  // idempotent with the optimistic append (nothing is duplicated) and a failed
  // fetch leaves the current thread intact. Unlike the selection-time effect it
  // deliberately does NOT flip the loading spinner, reset the open panel, or clear
  // unread badges — a reconnect must not flash or reshuffle the open thread. Reads
  // refs (not deps) so it is stable across renders and safe in a WS closure.
  const reloadOpenThread = useCallback(() => {
    const sel = selectedRef.current;
    const convId = selectedConvIdRef.current;
    if (!sel && convId == null) return;
    const bufKey = sel || (convId != null ? `conv:${convId}` : '');
    const preFetchBuffer = pendingWsMessages.current[bufKey] || [];
    pendingWsMessages.current[bufKey] = [];
    const isPageVisible = pageVisibleRef.current;
    // plano 85 A1 — mesmo token da carga de seleção: este reload substitui a thread
    // inteira, então ele invalida uma carga anterior em voo (ambas são da MESMA
    // thread) e é invalidado por qualquer troca de conversa que aconteça no meio.
    const token = ++detailSeqRef.current;
    const loader = convId != null
      ? getConversationMessages(convId, isPageVisible).then(res =>
          res.ok ? { ok: true, data: shapeConvData(res.data) } : res)
      : getContact(sel, isPageVisible, null);
    loader.then(res => {
      if (token !== detailSeqRef.current) return;  // resposta obsoleta — descarta
      if (!res.ok) return;
      const data = res.data;
      if (data.channel_id) setSelectedChannelId(data.channel_id);
      const duringFetch = pendingWsMessages.current[bufKey] || [];
      const pending = [...preFetchBuffer, ...duringFetch];
      pendingWsMessages.current[bufKey] = [];
      setContactData(applyThreadResponse(data, pending, threadKeyOf(sel, convId)));
    });
  }, []);

  // Plano 50 F4 — carregar mensagens ANTERIORES (scroll-up / keyset). Busca a página
  // anterior (before_id = menor _id já carregado) SEM re-marcar como lida e a PREPENDA
  // ao histórico (dedup por _id, como o merge de WS). Guardado por ref contra chamadas
  // concorrentes. O caller (ContactDetail) ancora o scroll para a viewport não saltar.
  const loadOlder = useCallback(() => {
    const data = contactDataRef.current;
    if (!data || !data.has_more || loadingOlderRef.current) return;
    const msgs = data.messages || [];
    const ids = msgs.map(m => m._id).filter(v => v != null);
    if (ids.length === 0) return;
    const beforeId = Math.min(...ids);
    const sel = selectedRef.current;
    const convId = selectedConvIdRef.current;
    if (!sel && convId == null) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    // plano 85 A1 — CAPTURA o token (não incrementa): paginar para trás não invalida
    // uma carga de seleção em voo, mas qualquer troca de conversa invalida esta
    // página. Sem isso, a página anterior da conversa A era prependada na thread de
    // B se o operador trocasse de conversa durante a rolagem (`loadingOlderRef` só
    // protege contra `loadOlder` concorrente consigo mesmo).
    const token = detailSeqRef.current;
    const loader = convId != null
      ? getConversationMessages(convId, false, { beforeId }).then(res =>
          res.ok ? { ok: true, data: shapeConvData(res.data) } : res)
      : getContact(sel, false, null, { beforeId });
    loader.then(res => {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
      if (token !== detailSeqRef.current) return;  // trocou de conversa — descarta
      if (!res.ok) return;
      const older = res.data.messages || [];
      const newHasMore = !!res.data.has_more;
      setContactData(prev => prependOlder(prev, older, newHasMore));
    });
  }, []);

  return {
    selected, setSelected,
    selectedConvId, setSelectedConvId,
    selectedChannelId, setSelectedChannelId,
    scrollToMsg, setScrollToMsg,
    contactData, setContactData,
    loadingDetail,
    detailError, retryDetail,      // plano 85 A3
    loadingOlder, loadOlder,
    openPanel, setOpenPanel,
    selectedRef, selectedConvIdRef, selectedChannelIdRef,
    openInfoAfterSelect, pendingWsMessages,
    isOpenRow, selectContact, reloadOpenThread,
  };
}
