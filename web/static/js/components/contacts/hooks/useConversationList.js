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
import { rowKeyFor } from '../ContactList.js';

/**
 * @param {Object} opts
 * @param {(...args:any[])=>void} [opts.onUnreadChange] - app-shell unread refresh.
 */
// Tamanho de página da sidebar — vale para os DOIS modos (conversa-first do plano 50 F8
// e busca do plano 62 F6).
const SIDEBAR_PAGE = 50;

// plano 62 F6 — quantas páginas de contatos SEGUIDAS sem produzir linha podem ser
// puladas automaticamente antes de declarar o fim da lista. O cruzamento da busca é
// dirigido por CONTATOS, mas só emite linha para contato cujo atendimento está na janela
// de `listConversations` (200) ou que não tem atendimento nenhum — então uma página
// inteira pode não render linha alguma. Sem esse encadeamento o scroll TRAVA: o
// IntersectionObserver só re-dispara quando o DOM muda, e uma página que não anexa nada
// deixa a sentinela parada com `hasMore` ligado. Esgotado o crédito, encerramos a lista
// (fim limpo) em vez de congelar.
const SEARCH_EMPTY_PAGE_CHAIN = 3;

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
  // plano 62 F6: a query que produziu a lista ATUALMENTE carregada ('' = conversa-first).
  // É ela — não `searchRef`, que muda a cada tecla — que decide o modo do `loadMore`:
  // durante os 300ms de debounce `searchRef` já é a query nova enquanto `offsetRef`/as
  // linhas ainda são do modo anterior, e paginar por `searchRef` ali anexaria linhas de
  // busca sobre a lista conversa-first com o cursor errado.
  const loadedQueryRef = useRef('');

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
  // COM busca (plano 62 F6): buildRows cruzando getContacts(q) × conversas, agora com o
  // universo de contatos PAGINADO no servidor — preserva a semântica de busca e o scroll
  // infinito passa a valer nos dois modos. Contatos SEM atendimento não aparecem no modo
  // conversa-first (P3: tratados à parte / pela tela Contatos); aparecem na busca.
  const fetchContacts = useCallback((q = '') => {
    offsetRef.current = 0;
    // plano 62 F6 (fix da revisão): desliga a sentinela do scroll infinito ENQUANTO
    // o fetch novo está em voo. Sem isso, um flick de scroll na janela entre trocar
    // de modo (busca↔conversa-first) e a resposta chegar dispararia loadMore com o
    // token NOVO mas o modo/cursor VELHO — poluindo a lista com linhas do modo errado.
    // O has_more real volta do envelope no .then.
    setHasMore(false);
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
      // Modo busca — 1ª página. plano 62 F6: o universo de CONTATOS que casa `q` vem
      // PAGINADO do servidor (envelope { items, total, has_more }); antes vinha inteiro
      // num payload só (buscar 'a' baixava ~8,5k contatos ≈ 4 MB).
      // Plano 54: o arquivo é por CONVERSA, não por contato — a busca SEMPRE pede os
      // contatos não-arquivados (universo de riqueza: nome/avatar/tags + os contatos
      // sem atendimento). A view (caixa × arquivadas) é decidida pelo filtro de
      // ATENDIMENTOS; `buildRows` cruza os dois honrando `archivedView`.
      // COMBINAÇÃO plano 62 F6 (contatos paginados) + eafb629/Leandro (atendimentos por
      // `contact_ids`): 1º pede a PÁGINA de contatos que casa `q` (envelope
      // { items, has_more } — não baixa mais os ~8,5k num payload só); depois pede os
      // atendimentos EXATOS desses contatos (`contact_ids`), não a janela das 200 mais
      // recentes que fazia o `buildRows` descartar em silêncio o contato cujo atendimento
      // estivesse fora dela. Cada página busca os SEUS atendimentos, então o `loadMore`
      // (loadSearchPage) repete o mesmo padrão — não há janela compartilhada entre páginas.
      getContacts(q, false, { limit: SIDEBAR_PAGE, offset: 0, signal: ctrl.signal }).then((cRes) => {
        if (token !== fetchSeqRef.current) return;  // resposta obsoleta — descarta
        const env = (cRes && cRes.ok) ? (cRes.data || {}) : null;
        // `items` só existe no envelope paginado; sem ele (falha do request) o cruzamento
        // é pulado e a janela de atendimentos das 200 mais recentes vira a lista (fallback).
        const contactList = env ? (env.items || []) : null;
        // Busca OK que casa ZERO contatos → lista vazia direto, sem o 2º roundtrip (evita
        // buscar 200 atendimentos que `buildRows([], …)` descartaria — caso comum: typo).
        if (contactList && contactList.length === 0) {
          loadedQueryRef.current = q;
          setContacts([]); contactsRef.current = [];
          setHasMore(false);
          setLoading(false);
          return;
        }
        const ids = contactList ? contactList.map(c => c.id).filter(id => id != null) : [];
        const convsP = ids.length
          ? listConversations({ archived: archivedView, contact_ids: ids.join(','), limit: 500 },
                               { signal: ctrl.signal })
          : listConversations({ archived: archivedView, limit: 200 }, { signal: ctrl.signal });
        return convsP.then((vRes) => {
          if (token !== fetchSeqRef.current) return;
          loadedQueryRef.current = q;
          if (vRes && vRes.ok) {
            const convs = (vRes.data && vRes.data.conversations) || [];
            const rows = sortContacts(contactList
              ? buildRows(contactList, convs, { archivedView })
              : convs.map(convRowToSidebarRow));
            setContacts(rows);
            contactsRef.current = rows;
            // Só paginamos quando o universo de contatos paginou de verdade — no fallback
            // sem `contactList` não há cursor a avançar. O cursor conta CONTATOS lidos
            // (unidade da paginação do servidor), não linhas.
            setHasMore(!!(env && env.has_more) && !!contactList && contactList.length > 0);
            offsetRef.current = contactList ? contactList.length : 0;
          } else {
            setContacts([]); contactsRef.current = [];
            setHasMore(false);
          }
          setLoading(false);
        });
      }).catch((e) => {
        if (token !== fetchSeqRef.current || (e && e.name === 'AbortError')) return;
        setContacts([]); contactsRef.current = [];
        setHasMore(false);
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
        loadedQueryRef.current = '';
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

  // plano 62 F6 + eafb629 — próxima página do modo BUSCA: avança o cursor de CONTATOS,
  // pede os atendimentos EXATOS dos contatos da página nova (`contact_ids`, mesmo padrão
  // da 1ª página — nenhum some por ter o atendimento fora das 200 mais recentes) e anexa.
  // Páginas de contatos são disjuntas, então o append não duplica; a dedup por `rowKeyFor`
  // é cinto de segurança contra o re-ordenamento que o offset pagination sofre quando
  // chega mensagem nova durante a rolagem. `step` se auto-chama enquanto a página não
  // render NENHUMA linha (ver SEARCH_EMPTY_PAGE_CHAIN) — do contrário o scroll travaria.
  const loadSearchPage = useCallback((q, archivedView, token, done) => {
    const step = (chainLeft) => {
      const offset = offsetRef.current;
      const ctrl = new AbortController();
      loadMoreAbortRef.current = ctrl;
      getContacts(q, false, { limit: SIDEBAR_PAGE, offset, signal: ctrl.signal })
        .then((cRes) => {
          // Token ANTES de avançar o cursor: um reset (query nova/troca de aba) já zerou
          // o `offsetRef`, e avançá-lo aqui corromperia a paginação da query nova.
          if (token !== fetchSeqRef.current) { done(); return; }
          if (!cRes || !cRes.ok) { done(); return; }
          const env = cRes.data || {};
          const items = env.items || [];
          offsetRef.current = offset + items.length;
          // `items.length === 0` fecha a paginação mesmo com um `has_more` otimista —
          // sem isso o cursor ficaria parado pedindo a mesma página vazia pra sempre.
          const serverHasMore = !!env.has_more && items.length > 0;
          const finish = (fresh) => {
            if (token !== fetchSeqRef.current) { done(); return; }  // reset durante o 2º request
            if (fresh.length) {
              setContacts((prev) => {
                const seen = new Set(prev.map(rowKeyFor));
                const add = fresh.filter((r) => !seen.has(rowKeyFor(r)));
                const next = sortContacts([...prev, ...add]);
                contactsRef.current = next;
                return next;
              });
            }
            if (serverHasMore && fresh.length === 0 && chainLeft > 1) { step(chainLeft - 1); return; }
            setHasMore(serverHasMore && fresh.length > 0);
            done();
          };
          if (!items.length) { finish([]); return; }
          const ids = items.map((c) => c.id).filter((id) => id != null);
          listConversations({ archived: archivedView, contact_ids: ids.join(','), limit: 500 },
                            { signal: ctrl.signal })
            .then((vRes) => {
              const convs = (vRes && vRes.ok && vRes.data && vRes.data.conversations) || [];
              finish(buildRows(items, convs, { archivedView }));
            })
            .catch(() => { done(); });  // AbortError/erro de rede — sem estado a corrigir
        })
        .catch(() => { done(); });
    };
    step(SEARCH_EMPTY_PAGE_CHAIN);
  }, []);

  // Carrega a PRÓXIMA página (scroll infinito) e ANEXA. Vale nos DOIS modos (plano 62
  // F6: a busca também pagina). Guardado contra chamadas concorrentes.
  const loadMore = useCallback(() => {
    if (loadingMoreRef.current || !hasMore) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    // plano 62 F3: captura o token atual — se um reset (busca/arquivo) chegar com esta
    // página em voo, o append obsoleto é descartado (e o fetchContacts novo a aborta).
    const token = fetchSeqRef.current;
    const done = () => { loadingMoreRef.current = false; setLoadingMore(false); };
    const q = loadedQueryRef.current;   // modo da lista CARREGADA (não o texto em digitação)
    const archivedView = showArchivedRef.current;
    if (q) { loadSearchPage(q, archivedView, token, done); return; }
    const ctrl = new AbortController();
    loadMoreAbortRef.current = ctrl;
    listConversations({ archived: archivedView,
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
      .finally(done);
  }, [hasMore, loadSearchPage]);

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
