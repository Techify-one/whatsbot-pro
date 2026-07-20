// @ts-check
//
// Conversation-list hook (Plano 23 · D2) — extracted verbatim from Contacts.js.
//
// Owns the sidebar rows (`contacts`, one per conversation/channel), the
// search + archived view state, and the loader that crosses contacts ×
// conversations into rows (via the pure `buildRows`/`sortContacts`). Exposes the
// stable refs (`contactsRef`/`searchRef`/`fetchContactsRef`/`showArchivedRef`)
// that the []-dep WS callbacks read to avoid stale closures, plus the derived
// channel options + the "show per-row channel badge" flag.
//
// Cross-hook wiring: this hook owns only `contacts`/`search`/`showArchived` +
// fetch. Clearing the open thread / selection mode on an archive toggle is
// orchestrated by the container (it owns those setters), so this hook exposes
// `setShowArchived` and reloads on the change without reaching into other hooks.
import { useState, useEffect, useRef, useCallback, useMemo } from 'preact/hooks';
import { getContacts, listConversations, listChannelsForFilter } from '../../../services/api.js';
import { buildRows, convRowToSidebarRow, sortContacts, distinctChannelCount } from '../../../services/conversationRows.js';

/**
 * @param {Object} opts
 * @param {(...args:any[])=>void} [opts.onUnreadChange] - app-shell unread refresh.
 */
// Tamanho de página da sidebar conversa-first (plano 50 F8).
const SIDEBAR_PAGE = 50;

// plano 62 F3: TTL do cache do universo de contatos da BUSCA. Refetches disparados
// por evento WS com a MESMA query (membership/reconnect) reusam o resultado pesado
// de getContacts(q) e só repetem o listConversations (barato).
const SEARCH_CACHE_TTL_MS = 30000;

export function useConversationList({ onUnreadChange }) {
  const [contacts, setContacts] = useState([]);  // sidebar rows (one per conversation)
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);  // plano 50 F8: scroll infinito
  const [hasMore, setHasMore] = useState(false);          // há próxima página (servidor)
  const [search, setSearch] = useState('');
  const [showArchived, setShowArchived] = useState(false);

  const contactsRef = useRef([]);
  const displayedRef = useRef([]);   // currently-visible (filtered) rows — for "selecionar todas"
  const searchRef = useRef('');                // current search term (for ref-based refetch)
  const fetchContactsRef = useRef(null);       // stable handle to fetchContacts
  const showArchivedRef = useRef(false);
  const offsetRef = useRef(0);                  // plano 50 F8: cursor de paginação
  const loadingMoreRef = useRef(false);         // guarda contra loadMore concorrente
  const fetchSeqRef = useRef(0);                // plano 62 F3: token de sequência do fetch
  const fetchAbortRef = useRef(null);           // plano 62 F3: aborta o fetch anterior
  const loadMoreAbortRef = useRef(null);        // plano 62 F3: aborta o loadMore em voo
  const searchCacheRef = useRef(null);          // plano 62 F3: { q, ts, list } — último getContacts(q)

  // Keep refs in sync — avoids stale closures
  useEffect(() => { contactsRef.current = contacts; }, [contacts]);
  useEffect(() => { searchRef.current = search; }, [search]);
  useEffect(() => { showArchivedRef.current = showArchived; }, [showArchived]);

  // Notify the app shell whenever the conversation list changes so it can refresh
  // the browser-tab unread badge — covers reads that fire no WS event (e.g. the
  // operator opening a chat on this same client).
  useEffect(() => { if (onUnreadChange) onUnreadChange(); }, [contacts]);

  // Show the per-row channel badge only when the ACCOUNT uses ≥2 distinct channels
  // (with a single channel it would be noise) — mirrors the Conversations screen (FQ1).
  // Plano 56: latch the MAX diversity ever seen this session instead of reading the
  // CURRENT (post-search) rows. The initial load is unfiltered and captures the real
  // count, so a narrowing search — which often collapses the visible set to a single
  // provider — never makes the badge vanish list-wide. A single-channel account never
  // reaches >1, so the badge stays hidden as intended.
  const maxChannelsSeenRef = useRef(0);
  const showChannel = useMemo(() => {
    maxChannelsSeenRef.current = Math.max(maxChannelsSeenRef.current, distinctChannelCount(contacts));
    return maxChannelsSeenRef.current > 1;
  }, [contacts]);

  // Opções do filtro "Canais" — vêm do BANCO, não das linhas carregadas (plano 59).
  // Antes eram derivadas de `contacts`, que é capado em 200 conversas mais recentes
  // pelo backend, então canais fora dessa janela (ou só na outra view de arquivo)
  // sumiam do filtro. Agora um fetch único no mount lista TODOS os canais (incl.
  // desabilitados/arquivados). Fetch falho degrada silencioso (filtro vazio). O
  // casamento continua por `id` textual (= `conversations.channel_id`); o label
  // preserva a regra antiga (display_name → provider → 'Padrão'/id).
  const [channelOptions, setChannelOptions] = useState([]);
  useEffect(() => {
    let alive = true;
    listChannelsForFilter().then((res) => {
      if (!alive || !res || !res.ok) return;
      const rows = res.data || [];
      setChannelOptions(rows.map((c) => ({
        id: c.id,
        label: c.display_name
          || c.provider
          || (c.id === 'default' ? 'Padrão' : c.id),
      })));
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // plano 50 F8 — SEM busca: conversa-first paginado (dirige por /api/atendimentos, que
  // já pagina; DOM cresce por página via scroll infinito, sem baixar TODOS os contatos).
  // COM busca: caminho legado (buildRows cruzando getContacts(q) × conversas) — preserva
  // a semântica de busca. Contatos SEM atendimento não aparecem no modo conversa-first
  // (P3: tratados à parte / pela tela Contatos); aparecem no modo busca via buildRows.
  const fetchContacts = useCallback((q = '') => {
    offsetRef.current = 0;
    setLoading(true);
    const archivedView = showArchivedRef.current;
    // plano 62 F3 — token de sequência: só a chamada MAIS RECENTE pode gravar estado,
    // então uma resposta fora de ordem (busca lenta × limpar rápido) nunca sobrescreve
    // a lista nova (mesmo princípio do guard `alive` acima). O request anterior ainda
    // em voo também é abortado — libera o slot do browser; um AbortError é engolido
    // (não é erro de UI), o token já protege o estado de qualquer forma.
    const token = ++fetchSeqRef.current;
    if (fetchAbortRef.current) fetchAbortRef.current.abort();
    if (loadMoreAbortRef.current) loadMoreAbortRef.current.abort();
    const ctrl = new AbortController();
    fetchAbortRef.current = ctrl;
    if (q) {
      // Modo busca (legado): lista completa filtrada × conversas.
      // Plano 54: o arquivo é por CONVERSA, não por contato — a busca SEMPRE pede os
      // contatos não-arquivados (universo de riqueza: nome/avatar/tags + os contatos
      // sem atendimento). A view (caixa × arquivadas) é decidida pelo filtro de
      // ATENDIMENTOS; `buildRows` cruza os dois honrando `archivedView`.
      // plano 62 F3: refetch com a MESMA query (disparado por evento WS) reusa o
      // universo de contatos do cache curto e só repete o listConversations (barato).
      const cache = searchCacheRef.current;
      const cachedList = (cache && cache.q === q && (Date.now() - cache.ts) < SEARCH_CACHE_TTL_MS)
        ? cache.list : null;
      Promise.all([
        cachedList ? Promise.resolve(null) : getContacts(q, false, { signal: ctrl.signal }),
        listConversations({ archived: archivedView, limit: 200 }, { signal: ctrl.signal }),
      ]).then(([cRes, vRes]) => {
        if (token !== fetchSeqRef.current) return;  // resposta obsoleta — descarta
        const contactList = cachedList
          || (cRes && cRes.ok ? (cRes.data || []) : null);
        if (!cachedList && contactList) {
          searchCacheRef.current = { q, ts: Date.now(), list: contactList };
        }
        if (vRes && vRes.ok) {
          const convs = (vRes.data && vRes.data.conversations) || [];
          const rows = sortContacts(contactList
            ? buildRows(contactList, convs, { archivedView })
            : convs.map(convRowToSidebarRow));
          setContacts(rows);
          contactsRef.current = rows;
        } else {
          setContacts([]); contactsRef.current = [];
        }
        setHasMore(false);
        setLoading(false);
      }).catch((e) => {
        if (token !== fetchSeqRef.current || (e && e.name === 'AbortError')) return;
        setLoading(false);
      });
      return;
    }
    // Modo conversa-first (1ª página). Aqui as rows vêm direto dos atendimentos, então
    // a view (caixa × arquivadas) já é decidida no servidor pelo filtro `archived`.
    listConversations({ archived: archivedView, limit: SIDEBAR_PAGE, offset: 0 },
                      { signal: ctrl.signal })
      .then((vRes) => {
        if (token !== fetchSeqRef.current) return;  // resposta obsoleta — descarta
        if (vRes && vRes.ok) {
          const convs = (vRes.data && vRes.data.conversations) || [];
          const rows = sortContacts(convs.map(convRowToSidebarRow));
          setContacts(rows);
          contactsRef.current = rows;
          setHasMore(!!(vRes.data && vRes.data.has_more));
          offsetRef.current = convs.length;
        } else {
          setContacts([]); contactsRef.current = []; setHasMore(false);
        }
        setLoading(false);
      })
      .catch((e) => {
        if (token !== fetchSeqRef.current || (e && e.name === 'AbortError')) return;
        setLoading(false);
      });
  }, []);

  // Carrega a PRÓXIMA página (scroll infinito) e ANEXA (dedup por conversation_id).
  // Só no modo conversa-first (sem busca). Guardado contra chamadas concorrentes.
  const loadMore = useCallback(() => {
    if (searchRef.current || loadingMoreRef.current || !hasMore) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    // plano 62 F3: captura o token atual — se um reset (busca/arquivo) chegar com esta
    // página em voo, o append obsoleto é descartado (e o fetchContacts novo a aborta).
    const token = fetchSeqRef.current;
    const ctrl = new AbortController();
    loadMoreAbortRef.current = ctrl;
    listConversations({ archived: showArchivedRef.current,
                        limit: SIDEBAR_PAGE, offset: offsetRef.current },
                      { signal: ctrl.signal })
      .then((vRes) => {
        if (token !== fetchSeqRef.current) return;  // reset no meio — descarta
        if (vRes && vRes.ok) {
          const convs = (vRes.data && vRes.data.conversations) || [];
          const fresh = convs.map(convRowToSidebarRow);
          setContacts((prev) => {
            const seen = new Set(prev.map(r => r.conversation_id));
            const add = fresh.filter(r => r.conversation_id == null || !seen.has(r.conversation_id));
            const next = sortContacts([...prev, ...add]);
            contactsRef.current = next;
            return next;
          });
          setHasMore(!!(vRes.data && vRes.data.has_more));
          offsetRef.current += convs.length;
        }
      })
      .catch(() => {})  // plano 62 F3: AbortError/erro de rede — sem estado a corrigir
      .finally(() => { loadingMoreRef.current = false; setLoadingMore(false); });
  }, [hasMore]);

  // Stable handle so the []-dep WS callback can refetch with the current search.
  useEffect(() => { fetchContactsRef.current = fetchContacts; }, [fetchContacts]);

  const handleSearchChange = useCallback((val) => {
    setSearch(val);
  }, []);

  // Initial load
  useEffect(() => { fetchContacts(); }, []);

  // Debounced search. plano 62 F3: pula a execução do MOUNT (search ainda '') — o
  // initial load acima já cobre; sem o skip, o refetch duplicado de 300ms abortava
  // o fetch inicial e atrasava o 1º paint da sidebar em cargas lentas.
  const searchDebounceMountedRef = useRef(false);
  useEffect(() => {
    if (!searchDebounceMountedRef.current) { searchDebounceMountedRef.current = true; return; }
    const timer = setTimeout(() => fetchContacts(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // Reload when the archive filter changes. The container clears the open thread
  // + selection mode in its own [showArchived] effect (runs after this one).
  useEffect(() => { fetchContacts(search); }, [showArchived]);

  return {
    contacts, setContacts, loading,
    loadingMore, hasMore, loadMore,
    search, setSearch, handleSearchChange,
    showArchived, setShowArchived,
    fetchContacts, sortContacts,
    contactsRef, displayedRef, searchRef, fetchContactsRef, showArchivedRef,
    showChannel, channelOptions,
  };
}
