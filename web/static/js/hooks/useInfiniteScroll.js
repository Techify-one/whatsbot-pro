// @ts-check
//
// Scroll infinito com LOTE FIXO (plano 50) — acumula páginas do servidor conforme o
// usuário rola; NUNCA carrega tudo de uma vez e sempre pede a MESMA quantidade
// (`pageSize`). Some com os botões Anterior/Próxima: rolar até o fim carrega o próximo
// lote automaticamente.
//
// Contrato:
// - `fetchPage(offset)` → Promise<{ items, hasMore }>: uma página do servidor. O caller
//   monta a query com `limit=pageSize&offset=<offset>` e devolve os itens + se há mais.
// - `resetKey`: quando muda (ex.: busca/filtro/período), recomeça da 1ª página.
// - `keyOf(item)`: chave de dedup ao anexar (evita duplicar na virada de página).
//
// Devolve a lista ACUMULADA + `loadMore` (chame no sentinela de fim de lista) + flags.
import { useState, useRef, useCallback, useEffect, useLayoutEffect } from 'preact/hooks';

/**
 * @param {Object} opts
 * @param {(offset:number)=>Promise<{items:any[], hasMore:boolean}>} opts.fetchPage
 * @param {number} opts.pageSize
 * @param {any} opts.resetKey
 * @param {(item:any)=>any} [opts.keyOf]
 */
export function useInfiniteScroll({ fetchPage, pageSize, resetKey, keyOf = (x) => x.id }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const offsetRef = useRef(0);
  const busyRef = useRef(false);      // guarda contra loadMore concorrente
  const fetchRef = useRef(fetchPage); // sempre a versão atual (sem re-disparar efeito)
  useEffect(() => { fetchRef.current = fetchPage; });

  // (Re)carrega a 1ª página sempre que `resetKey` muda (busca/filtro/período).
  useEffect(() => {
    let alive = true;
    offsetRef.current = 0;
    busyRef.current = false;
    setLoading(true);
    Promise.resolve(fetchRef.current(0)).then((r) => {
      if (!alive) return;
      const list = (r && r.items) || [];
      setItems(list);
      setHasMore(!!(r && r.hasMore));
      offsetRef.current = list.length;
    }).catch(() => { if (alive) { setItems([]); setHasMore(false); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line
  }, [resetKey, pageSize]);

  // Próximo lote (chamado pelo sentinela). Anexa com dedup; nunca substitui.
  const loadMore = useCallback(() => {
    if (busyRef.current || !hasMore) return;
    busyRef.current = true;
    setLoadingMore(true);
    Promise.resolve(fetchRef.current(offsetRef.current)).then((r) => {
      const list = (r && r.items) || [];
      setItems((prev) => {
        const seen = new Set(prev.map(keyOf));
        const fresh = list.filter((x) => !seen.has(keyOf(x)));
        return [...prev, ...fresh];
      });
      setHasMore(!!(r && r.hasMore));
      offsetRef.current += list.length;
    }).catch(() => {})
      .finally(() => { busyRef.current = false; setLoadingMore(false); });
  }, [hasMore, keyOf]);

  return { items, setItems, loading, loadingMore, hasMore, loadMore };
}

/**
 * Efeito utilitário: liga um IntersectionObserver a um sentinela de fim de lista.
 * `onHit` dispara quando o sentinela entra na viewport (rolagem do mouse inclusive).
 * `root` = container de scroll (ou null p/ a viewport da página). `active` liga/desliga.
 * `rootMargin` pré-carrega antes do sentinela aparecer — default expande o BAIXO
 * (listas que anexam pra baixo). Para um sentinela de TOPO (scroll-up), passe algo
 * como '120px 0px 0px 0px'.
 * @param {{current:any}} sentinelRef
 * @param {()=>void} onHit
 * @param {boolean} active
 * @param {{current:any}} [rootRef]
 * @param {string} [rootMargin]
 */
export function useScrollSentinel(sentinelRef, onHit, active, rootRef, rootMargin = '0px 0px 300px 0px') {
  const cbRef = useRef(onHit);
  useEffect(() => { cbRef.current = onHit; });
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !active) return;
    const root = rootRef && rootRef.current ? rootRef.current : null;
    const obs = new IntersectionObserver((entries) => {
      if (entries[0] && entries[0].isIntersecting) cbRef.current();
    }, { root, rootMargin });
    obs.observe(el);
    return () => obs.disconnect();
  }, [active, sentinelRef, rootRef, rootMargin]);
}

/**
 * Scroll infinito REVERSO (keyset / prepend) — para o histórico do chat, que rola pra
 * CIMA e carrega mensagens ANTERIORES via cursor (`before_id`). Enquanto o hook acima
 * anexa páginas por offset PARA BAIXO, este:
 *  - liga um sentinela no TOPO do container de scroll (via `useScrollSentinel`);
 *  - ao ser atingido, captura a âncora (scrollHeight/scrollTop) e chama `loadOlder`;
 *  - após o prepend, RESTAURA a posição visual num `useLayoutEffect` (antes do paint,
 *    sem flicker) somando o delta de altura — a viewport não salta;
 *  - só dispara quando há o que rolar (`scrollHeight > clientHeight`), evitando o
 *    auto-load em cascata quando a lista não transborda (que empurraria as mensagens
 *    novas pra fora da viewport — o bug "as anteriores não aparecem").
 *
 * A busca/prepend em si continua no dono dos dados (`loadOlder`); o hook cuida só do
 * gatilho por rolagem + ancoragem. Devolve `sentinelRef` (ponha no elemento do topo) e
 * `justPrependedRef` (o consumidor checa p/ NÃO rolar pro fim na atualização do prepend).
 *
 * @param {Object} opts
 * @param {{current:any}} opts.scrollRef  container de scroll
 * @param {any[]} opts.items              lista renderizada (dispara a restauração ao mudar)
 * @param {boolean} opts.hasMore
 * @param {(()=>void)|null} opts.loadOlder
 * @param {boolean} opts.loadingOlder
 */
export function useReverseInfiniteScroll({ scrollRef, items, hasMore, loadOlder, loadingOlder }) {
  const sentinelRef = useRef(null);
  const anchorRef = useRef(null);          // {height, top} pré-prepend, ou null
  const justPrependedRef = useRef(false);

  const trigger = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !hasMore || loadingOlder || !loadOlder) return;
    if (el.scrollHeight - el.clientHeight <= 4) return;  // nada a rolar → não dispara
    anchorRef.current = { height: el.scrollHeight, top: el.scrollTop };
    loadOlder();
  }, [scrollRef, hasMore, loadingOlder, loadOlder]);

  useScrollSentinel(sentinelRef, trigger, !!(hasMore && loadOlder), scrollRef, '120px 0px 0px 0px');

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && anchorRef.current) {
      const delta = el.scrollHeight - anchorRef.current.height;
      el.scrollTop = anchorRef.current.top + delta;
      anchorRef.current = null;
      justPrependedRef.current = true;
    }
    // eslint-disable-next-line
  }, [items]);

  return { sentinelRef, justPrependedRef };
}
