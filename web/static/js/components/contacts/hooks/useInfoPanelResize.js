// @ts-check
//
// Info-panel resize hook — controla a largura de um painel ancorado à direita
// (ex.: "Informações da conversa"). Mesmo padrão do `useSidebarResize`, mas:
//   • a alça fica na BORDA ESQUERDA do painel (o painel desliza pela direita),
//     então arrastar p/ a esquerda ALARGA — o delta é subtraído (`startWidth - dx`);
//   • não há colapsar-no-clique: o painel já tem seu próprio botão de fechar, então
//     a alça serve só para redimensionar;
//   • o máximo é DINÂMICO: o painel pode crescer até quase cobrir toda a área de
//     conversa, deixando só uma fresta (`minVisible`). Medimos a largura real do
//     container (via `containerRef`) a cada arraste/resize, então nunca estoura a
//     área disponível em telas menores.
// A largura é persistida por-dispositivo em localStorage (igual ao tema) e só é
// aplicada no desktop — no mobile o painel é `w-full` e o style inline não entra.
import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'preact/hooks';

const DEFAULT_MIN_WIDTH = 320;     // abaixo disso os campos do form ficam apertados
const DEFAULT_MIN_VISIBLE = 48;    // fresta da conversa preservada à esquerda do painel
const FALLBACK_MAX_WIDTH = 720;    // teto até o container poder ser medido (1º paint)
const HARD_CAP = 4000;             // sanidade p/ valores absurdos vindos do storage
const DEFAULT_WIDTH = 400;         // = o `lg:w-[400px]` original

/**
 * @param {object} [opts]
 * @param {string} [opts.storageKey]  chave de localStorage (por-dispositivo)
 * @param {{ current: any }} [opts.containerRef]  ref do elemento que delimita a área
 *   disponível — o painel não passa de `largura − minVisible` dele
 * @param {number} [opts.min]
 * @param {number} [opts.minVisible]  fresta preservada à esquerda do painel
 * @param {number} [opts.max]  teto de fallback enquanto o container não foi medido
 * @param {number} [opts.defaultWidth]
 * @returns {{
 *   width: number,
 *   isResizing: boolean,
 *   isDesktop: boolean,
 *   startResize: (e: MouseEvent) => void,
 * }}
 */
export function useInfoPanelResize(opts = {}) {
  const storageKey = opts.storageKey || 'whatsbot_info_panel_width';
  const containerRef = opts.containerRef || null;
  const MIN = opts.min ?? DEFAULT_MIN_WIDTH;
  const MIN_VISIBLE = opts.minVisible ?? DEFAULT_MIN_VISIBLE;
  const FALLBACK_MAX = opts.max ?? FALLBACK_MAX_WIDTH;
  const DEFAULT = opts.defaultWidth ?? DEFAULT_WIDTH;

  // Máximo dinâmico: quase toda a largura da área de conversa (container − fresta).
  // Sem container medível ainda (1º render) → cai no teto de fallback.
  const maxWidth = useCallback(() => {
    let measured = 0;
    try {
      if (containerRef && containerRef.current) {
        measured = containerRef.current.getBoundingClientRect().width;
      }
    } catch {}
    if (measured > 0) return Math.max(MIN, Math.round(measured - MIN_VISIBLE));
    return FALLBACK_MAX;
  }, [containerRef, MIN, MIN_VISIBLE, FALLBACK_MAX]);

  // `desired` = largura escolhida pelo usuário (persistida, só limitada por sanidade).
  // O que aplicamos é `desired` limitado ao máximo dinâmico — assim encolher a janela
  // não apaga a preferência: ao alargar de volta, o painel volta ao tamanho desejado.
  const readStored = useCallback(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw == null) return DEFAULT;
      const n = parseInt(raw, 10);
      if (!Number.isFinite(n)) return DEFAULT;
      return Math.max(MIN, Math.min(HARD_CAP, Math.round(n)));
    } catch { return DEFAULT; }
  }, [storageKey, MIN, DEFAULT]);

  const [desired, setDesired] = useState(readStored);
  const [availMax, setAvailMax] = useState(FALLBACK_MAX);
  const [isResizing, setIsResizing] = useState(false);
  const [isDesktop, setIsDesktop] = useState(() => {
    try { return window.matchMedia('(min-width:1024px)').matches; } catch { return true; }
  });
  const resizeRef = useRef(null);  // { startX, startWidth } durante o arraste

  // Largura efetivamente aplicada: a desejada, nunca além do que cabe agora.
  const width = Math.max(MIN, Math.min(availMax, desired));

  // Acompanha o breakpoint lg: só aplicamos largura fixa no desktop.
  useEffect(() => {
    let mql;
    try { mql = window.matchMedia('(min-width:1024px)'); } catch { return; }
    const onChange = () => setIsDesktop(mql.matches);
    onChange();
    if (mql.addEventListener) mql.addEventListener('change', onChange);
    else if (mql.addListener) mql.addListener(onChange);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener('change', onChange);
      else if (mql.removeListener) mql.removeListener(onChange);
    };
  }, []);

  // Mede o container (após o commit, antes do paint p/ não piscar) e re-mede a cada
  // resize da janela, atualizando o máximo dinâmico aplicado.
  useLayoutEffect(() => {
    const remeasure = () => setAvailMax(maxWidth());
    remeasure();
    window.addEventListener('resize', remeasure);
    return () => window.removeEventListener('resize', remeasure);
  }, [maxWidth]);

  const endResize = useCallback(() => {
    resizeRef.current = null;
    setIsResizing(false);
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', endResize);
    try { document.body.style.userSelect = ''; } catch {}
    setDesired(w => {
      try { localStorage.setItem(storageKey, String(w)); } catch {}
      return w;
    });
  }, [storageKey]);

  const onResizeMove = useCallback((e) => {
    const st = resizeRef.current;
    if (!st) return;
    // Alça na borda esquerda de um painel ancorado à direita: arrastar p/ a esquerda
    // (dx < 0) alarga, então subtraímos o delta. Clamp contra o máximo medido AO VIVO
    // p/ nunca passar da área de conversa.
    const dx = e.clientX - st.startX;
    setDesired(Math.max(MIN, Math.min(maxWidth(), Math.round(st.startWidth - dx))));
  }, [MIN, maxWidth]);

  const startResize = useCallback((e) => {
    if (!isDesktop) return;   // mobile: painel é w-full, sem redimensionamento
    e.preventDefault();
    resizeRef.current = { startX: e.clientX, startWidth: width };
    setIsResizing(true);
    try { document.body.style.userSelect = 'none'; } catch {}
    document.addEventListener('mousemove', onResizeMove);
    document.addEventListener('mouseup', endResize);
  }, [isDesktop, width, onResizeMove, endResize]);

  // Limpeza defensiva: se o componente desmontar no meio de um arraste.
  useEffect(() => () => {
    document.removeEventListener('mousemove', onResizeMove);
    document.removeEventListener('mouseup', endResize);
    try { document.body.style.userSelect = ''; } catch {}
  }, [onResizeMove, endResize]);

  return { width, isResizing, isDesktop, startResize };
}
