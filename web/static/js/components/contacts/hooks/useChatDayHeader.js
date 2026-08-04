import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { pickCurrentDay } from '../../../services/chatDayHeader.js';

// Plano 98 — medição da pílula de data flutuante do chat.
//
// O hook só MEDE: a decisão ("qual dia está no topo", "quanto empurrar") mora no
// módulo puro `services/chatDayHeader.js`, testado por `node --test`.
//
// Custo: os `[data-day]` são os SEPARADORES de data, ou seja O(nº de dias
// carregados) — dezenas —, não O(nº de mensagens) — milhares. A medição é
// coalescida por `requestAnimationFrame` (nunca mais de uma por quadro) e só
// chama `setState` quando o resultado muda de fato.
//
// Nada é cacheado entre quadros: `getBoundingClientRect()` é coordenada de
// VIEWPORT, então o salto de `scrollTop` do prepend ("carregar anteriores") e a
// mídia que carrega depois e muda alturas não estragam a leitura.

/**
 * @param {Object} opts
 * @param {{current: any}} opts.scrollRef container de rolagem do chat
 * @param {any[]} opts.items lista renderizada (re-mede quando muda)
 * @returns {{label: string|null, offsetY: number}}
 */
export function useChatDayHeader({ scrollRef, items }) {
  const [day, setDay] = useState({ label: null, offsetY: 0 });
  const dayRef = useRef(day);
  const frameRef = useRef(0);

  const measureRef = useRef(() => {});
  measureRef.current = () => {
    const el = scrollRef.current;
    if (!el) return;
    const topEdge = el.getBoundingClientRect().top;
    const seps = [];
    el.querySelectorAll('[data-day]').forEach((node) => {
      const r = node.getBoundingClientRect();
      seps.push({ label: node.getAttribute('data-day'), top: r.top, bottom: r.bottom });
    });
    const next = pickCurrentDay(seps, topEdge);
    const cur = dayRef.current;
    if (cur.label === next.label && Math.abs(cur.offsetY - next.offsetY) < 0.5) return;
    dayRef.current = next;
    setDay(next);
  };

  const scheduleRef = useRef(() => {});
  scheduleRef.current = () => {
    if (frameRef.current) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = 0;
      measureRef.current();
    });
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const onScroll = () => scheduleRef.current();
    const onContentReflow = () => scheduleRef.current();
    el.addEventListener('scroll', onScroll, { passive: true });
    // `load` não borbulha, mas a captura no scrollport vê imagens/áudios/vídeos.
    // Cards expansíveis também podem terminar uma transição sem mudar a lista.
    el.addEventListener('load', onContentReflow, true);
    el.addEventListener('loadedmetadata', onContentReflow, true);
    el.addEventListener('transitionend', onContentReflow, true);
    // O painel lateral abre/fecha e reflui a largura → as alturas mudam sem
    // rolagem nenhuma.
    let ro = null;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => scheduleRef.current());
      ro.observe(el);
      // A altura do scrollport costuma ficar fixa; quem muda ao carregar mídia ou
      // expandir card são seus filhos. Observá-los fecha esse ponto cego.
      for (const child of el.children) ro.observe(child);
    }
    let mo = null;
    if (typeof MutationObserver !== 'undefined') {
      mo = new MutationObserver((records) => {
        if (ro) {
          for (const record of records) {
            // Só filhos DIRETOS determinam a altura do conteúdo; observar cada
            // neto criado por uma bolha faria a lista de targets crescer sem ganho.
            if (record.target === el) for (const node of record.addedNodes || []) {
              if (node && node.nodeType === 1) ro.observe(node);
            }
          }
        }
        scheduleRef.current();
      });
      mo.observe(el, {
        childList: true, subtree: true, attributes: true,
        attributeFilter: ['class', 'style', 'hidden', 'open'],
      });
    }
    return () => {
      el.removeEventListener('scroll', onScroll);
      el.removeEventListener('load', onContentReflow, true);
      el.removeEventListener('loadedmetadata', onContentReflow, true);
      el.removeEventListener('transitionend', onContentReflow, true);
      if (ro) ro.disconnect();
      if (mo) mo.disconnect();
      if (frameRef.current) { cancelAnimationFrame(frameRef.current); frameRef.current = 0; }
    };
  }, [scrollRef]);

  // Mensagem nova, prepend e TROCA DE CONVERSA: medir ANTES do paint, para a
  // pílula da conversa anterior não sobrar num quadro.
  useLayoutEffect(() => {
    if (frameRef.current) { cancelAnimationFrame(frameRef.current); frameRef.current = 0; }
    measureRef.current();
  }, [items]);

  return day;
}
