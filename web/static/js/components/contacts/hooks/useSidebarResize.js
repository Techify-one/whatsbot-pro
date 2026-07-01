// @ts-check
//
// Sidebar resize hook (Plano 23 · D2) — extracted verbatim from Contacts.js.
//
// Owns the draggable conversation-list width: persisted per-device in
// localStorage, applied only on desktop, with a click-vs-drag threshold so a
// pure click on the handle collapses/expands instead of resizing. Self-contained
// (no cross-hook dependencies); returns the geometry state + the mousedown
// handler the divider binds.
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';

// ── Sidebar resize (barra lateral arrastável) ───────────────────────
// Largura da lista de atendimentos: arrastável no desktop e persistida em
// localStorage (mesmo padrão do tema). No mobile a barra é `w-full` e estes
// valores não se aplicam.
const SIDEBAR_WIDTH_KEY = 'whatsbot_sidebar_width';
const SIDEBAR_MIN_WIDTH = 280;
const SIDEBAR_MAX_WIDTH = 640;
const SIDEBAR_DEFAULT_WIDTH = 400;
const SIDEBAR_DRAG_THRESHOLD = 4;  // px p/ distinguir arraste de clique (colapsar)

function clampSidebarWidth(px) {
  if (!Number.isFinite(px)) return SIDEBAR_DEFAULT_WIDTH;
  return Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, Math.round(px)));
}

function readStoredSidebarWidth() {
  try {
    const raw = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    return raw == null ? SIDEBAR_DEFAULT_WIDTH : clampSidebarWidth(parseInt(raw, 10));
  } catch { return SIDEBAR_DEFAULT_WIDTH; }
}

/**
 * @returns {{
 *   sidebarHidden: boolean,
 *   sidebarWidth: number,
 *   isResizing: boolean,
 *   isDesktop: boolean,
 *   startResize: (e: MouseEvent) => void,
 * }}
 */
export function useSidebarResize() {
  const [sidebarHidden, setSidebarHidden] = useState(false);
  // Largura arrastável da barra (desktop). `isDesktop` decide se aplicamos o style
  // inline — no mobile a barra é w-full e não deve receber largura fixa.
  const [sidebarWidth, setSidebarWidth] = useState(readStoredSidebarWidth);
  const [isResizing, setIsResizing] = useState(false);
  const [isDesktop, setIsDesktop] = useState(() => {
    try { return window.matchMedia('(min-width:1024px)').matches; } catch { return true; }
  });
  const resizeRef = useRef(null);  // { startX, startWidth, moved } durante o arraste

  // Acompanha o breakpoint lg: só aplicamos largura fixa no desktop.
  useEffect(() => {
    let mql;
    try { mql = window.matchMedia('(min-width:1024px)'); } catch { return; }
    const onChange = () => setIsDesktop(mql.matches);
    onChange();
    // addEventListener('change') é o moderno; addListener cobre navegadores antigos.
    if (mql.addEventListener) mql.addEventListener('change', onChange);
    else if (mql.addListener) mql.addListener(onChange);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener('change', onChange);
      else if (mql.removeListener) mql.removeListener(onChange);
    };
  }, []);

  const endResize = useCallback(() => {
    const st = resizeRef.current;
    resizeRef.current = null;
    setIsResizing(false);
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', endResize);
    if (st && st.moved) {
      // Persiste só quando houve arraste de fato (clique puro = colapsar/expandir).
      setSidebarWidth(w => {
        try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(w)); } catch {}
        return w;
      });
    } else if (st && !st.moved) {
      // Clique sem arraste no handle: colapsa/expande a barra.
      setSidebarHidden(h => !h);
    }
  }, []);

  const onResizeMove = useCallback((e) => {
    const st = resizeRef.current;
    if (!st || st.hidden) return;  // barra colapsada: handle só expande no clique
    const dx = e.clientX - st.startX;
    if (!st.moved && Math.abs(dx) < SIDEBAR_DRAG_THRESHOLD) return;
    st.moved = true;
    setSidebarWidth(clampSidebarWidth(st.startWidth + dx));
  }, []);

  const startResize = useCallback((e) => {
    // Só no desktop; com a barra colapsada o handle só expande (via clique).
    if (!isDesktop) return;
    e.preventDefault();
    resizeRef.current = { startX: e.clientX, startWidth: sidebarWidth, moved: false,
                          hidden: sidebarHidden };
    setIsResizing(true);
    document.addEventListener('mousemove', onResizeMove);
    document.addEventListener('mouseup', endResize);
  }, [isDesktop, sidebarWidth, sidebarHidden, onResizeMove, endResize]);

  // Limpeza defensiva: se o componente desmontar no meio de um arraste.
  useEffect(() => () => {
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', endResize);
  }, [onResizeMove, endResize]);

  return { sidebarHidden, sidebarWidth, isResizing, isDesktop, startResize };
}
