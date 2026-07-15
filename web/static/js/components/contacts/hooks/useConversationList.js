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
import { getContacts, listConversations } from '../../../services/api.js';
import { buildRows, convRowToSidebarRow, sortContacts } from '../../../services/conversationRows.js';

/**
 * @param {Object} opts
 * @param {(...args:any[])=>void} [opts.onUnreadChange] - app-shell unread refresh.
 */
// Tamanho de página da sidebar conversa-first (plano 50 F8).
// ⚠️ TESTE TEMPORÁRIO (plano 50 QA): baixado de 50→3 p/ disparar o scroll infinito com
// poucas conversas. REVERTER para 50 depois dos testes.
const SIDEBAR_PAGE = 3;

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

  // Keep refs in sync — avoids stale closures
  useEffect(() => { contactsRef.current = contacts; }, [contacts]);
  useEffect(() => { searchRef.current = search; }, [search]);
  useEffect(() => { showArchivedRef.current = showArchived; }, [showArchived]);

  // Notify the app shell whenever the conversation list changes so it can refresh
  // the browser-tab unread badge — covers reads that fire no WS event (e.g. the
  // operator opening a chat on this same client).
  useEffect(() => { if (onUnreadChange) onUnreadChange(); }, [contacts]);

  // Show the per-row channel badge only when ≥2 distinct channels exist (with a
  // single channel it would be noise) — mirrors the Conversations screen (FQ1).
  const showChannel = useMemo(() => {
    const seen = new Set();
    for (const c of contacts) if (c.channel_provider) seen.add(c.channel_provider);
    return seen.size > 1;
  }, [contacts]);

  // Canais presentes nos atendimentos carregadas → opções do filtro "Canais". Derivado
  // das próprias linhas (mostra exatamente os canais em uso, sem fetch extra).
  const channelOptions = useMemo(() => {
    const map = new Map();
    for (const c of contacts) {
      const id = c.channel_id || 'default';
      if (!map.has(id)) {
        const label = c.channel_name
          || c.channel_provider
          || (id === 'default' ? 'Padrão' : id);
        map.set(id, label);
      }
    }
    return Array.from(map, ([id, label]) => ({ id, label }));
  }, [contacts]);

  // plano 50 F8 — SEM busca: conversa-first paginado (dirige por /api/atendimentos, que
  // já pagina; DOM cresce por página via scroll infinito, sem baixar TODOS os contatos).
  // COM busca: caminho legado (buildRows cruzando getContacts(q) × conversas) — preserva
  // a semântica de busca. Contatos SEM atendimento não aparecem no modo conversa-first
  // (P3: tratados à parte / pela tela Contatos); aparecem no modo busca via buildRows.
  const fetchContacts = useCallback((q = '') => {
    offsetRef.current = 0;
    setLoading(true);
    if (q) {
      // Modo busca (legado): lista completa filtrada × conversas.
      Promise.all([
        getContacts(q, showArchivedRef.current),
        listConversations({ archived: showArchivedRef.current, limit: 200 }),
      ]).then(([cRes, vRes]) => {
        if (vRes && vRes.ok) {
          const convs = (vRes.data && vRes.data.conversations) || [];
          const rows = sortContacts(cRes && cRes.ok
            ? buildRows(cRes.data || [], convs)
            : convs.map(convRowToSidebarRow));
          setContacts(rows);
          contactsRef.current = rows;
        } else {
          setContacts([]); contactsRef.current = [];
        }
        setHasMore(false);
        setLoading(false);
      });
      return;
    }
    // Modo conversa-first (1ª página).
    listConversations({ archived: showArchivedRef.current, limit: SIDEBAR_PAGE, offset: 0 })
      .then((vRes) => {
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
      });
  }, []);

  // Carrega a PRÓXIMA página (scroll infinito) e ANEXA (dedup por conversation_id).
  // Só no modo conversa-first (sem busca). Guardado contra chamadas concorrentes.
  const loadMore = useCallback(() => {
    if (searchRef.current || loadingMoreRef.current || !hasMore) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    listConversations({ archived: showArchivedRef.current,
                        limit: SIDEBAR_PAGE, offset: offsetRef.current })
      .then((vRes) => {
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
      .finally(() => { loadingMoreRef.current = false; setLoadingMore(false); });
  }, [hasMore]);

  // Stable handle so the []-dep WS callback can refetch with the current search.
  useEffect(() => { fetchContactsRef.current = fetchContacts; }, [fetchContacts]);

  const handleSearchChange = useCallback((val) => {
    setSearch(val);
  }, []);

  // Initial load
  useEffect(() => { fetchContacts(); }, []);

  // Debounced search
  useEffect(() => {
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
