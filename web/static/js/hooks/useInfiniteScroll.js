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
import { useState, useRef, useCallback, useEffect } from 'preact/hooks';

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
 * @param {{current:any}} sentinelRef
 * @param {()=>void} onHit
 * @param {boolean} active
 * @param {{current:any}} [rootRef]
 */
export function useScrollSentinel(sentinelRef, onHit, active, rootRef) {
  const cbRef = useRef(onHit);
  useEffect(() => { cbRef.current = onHit; });
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !active) return;
    const root = rootRef && rootRef.current ? rootRef.current : null;
    const obs = new IntersectionObserver((entries) => {
      if (entries[0] && entries[0].isIntersecting) cbRef.current();
    }, { root, rootMargin: '0px 0px 300px 0px' });
    obs.observe(el);
    return () => obs.disconnect();
  }, [active, sentinelRef, rootRef]);
}
