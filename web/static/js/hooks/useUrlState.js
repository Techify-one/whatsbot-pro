// @ts-check
//
// useUrlState (Plano 24) — sync a screen's view-state (filters / search / sub-tab)
// with the URL query-string, the query-string sibling of useDeepLink (which owns
// the pathname/entity). The pure parse/serialize lives in services/urlState.js;
// this hook is the thin window.location + history wrapper.
//
//   read()        → parse location.search into a state object (readParams(...)).
//   apply(state)  → push that state into the component (the setters).
//   serialize()   → the query string for the component's CURRENT state (writeParams).
//   deps          → the state values; when they change, reflect them into the URL.
//   mode          → 'replace' (default — filters/search: no history spam) | 'push'.
//
// Semantics: hydrate from the URL on mount and on popstate (back/forward); write
// the URL when the state diverges. The write effect SKIPS its first run so it
// can't clobber an incoming ?query before the mount-hydrate setState has flushed,
// and only writes when the serialized query actually differs from the address bar
// (so it never loops and never pollutes history with identical entries).
import { useEffect, useRef } from 'preact/hooks';

export function useUrlState({ read, apply, serialize, deps = [], mode = 'replace' }) {
  const applyRef = useRef(apply); applyRef.current = apply;
  const readRef = useRef(read); readRef.current = read;
  const serializeRef = useRef(serialize); serializeRef.current = serialize;
  const firstWrite = useRef(true);

  // Hydrate from the URL on mount + browser back/forward. pushState/replaceState
  // don't fire popstate, so our own writes never re-trigger this (no loop).
  useEffect(() => {
    function hydrate() { applyRef.current(readRef.current()); }
    hydrate();
    window.addEventListener('popstate', hydrate);
    return () => window.removeEventListener('popstate', hydrate);
  }, []);

  // Reflect the current state into the URL when deps change. Skip the very first
  // run: on mount the state is still at defaults (the hydrate setState hasn't
  // flushed yet), so writing here would strip an incoming ?query.
  useEffect(() => {
    if (firstWrite.current) { firstWrite.current = false; return; }
    const qs = serializeRef.current();
    const next = `${window.location.pathname}${qs ? `?${qs}` : ''}`;
    const cur = `${window.location.pathname}${window.location.search}`;
    if (next === cur) return;
    history[mode === 'push' ? 'pushState' : 'replaceState'](null, '', next);
  }, deps);
}
