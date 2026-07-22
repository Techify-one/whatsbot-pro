// @ts-check
//
// Zona de arrastar-e-soltar da conversa (plano 64 · F6).
//
// Expõe os 4 handlers que vão na RAIZ do painel de conversa e o estado que o
// overlay lê. O modo de envio é decidido pelo GESTO (D1): a metade de cima é
// "Foto ou vídeo" (inline, comprimido pelo provider), a de baixo é "Arquivo"
// (documento, original) — igual ao Telegram.
//
// `dragenter`/`dragleave` disparam também ao cruzar filhos do elemento, o que
// faria o overlay piscar. Por isso a entrada/saída é contada num ref (o padrão
// de "drag depth"), e só quando a contagem zera o overlay some.
import { useState, useRef } from 'preact/hooks';

/** O arraste carrega arquivo? (arrastar texto/seleção dentro do app não conta.) */
export function dragHasFiles(e) {
  const types = e && e.dataTransfer && e.dataTransfer.types;
  return !!types && Array.from(types).includes('Files');
}

/**
 * @param {{onFiles:(files:FileList, sendMode:'media'|'file')=>void, disabled?:boolean}} opts
 */
export function useDropZone({ onFiles, disabled = false }) {
  const [dragging, setDragging] = useState(false);
  const [zone, setZone] = useState(/** @type {'media'|'file'} */('media'));
  const depth = useRef(0);
  const rootRef = useRef(null);

  function reset() {
    depth.current = 0;
    setDragging(false);
  }

  function onDragEnter(e) {
    if (disabled || !dragHasFiles(e)) return;
    e.preventDefault();
    depth.current += 1;
    setDragging(true);
  }

  function onDragOver(e) {
    if (disabled || !dragHasFiles(e)) return;
    e.preventDefault();
    // Sem isto o cursor mostra "mover" e alguns navegadores recusam o drop.
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    if (!dragging) setDragging(true);
    const el = rootRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // `setZone` com o mesmo valor não re-renderiza (preact faz bail-out), então
    // mover o cursor dentro da mesma metade é de graça.
    setZone(e.clientY < r.top + r.height / 2 ? 'media' : 'file');
  }

  function onDragLeave(e) {
    if (disabled) return;
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setDragging(false);
  }

  function onDrop(e) {
    if (disabled || !dragHasFiles(e)) return;
    // `stopPropagation` para a guarda global (F0) não ver este drop como
    // "soltou fora do alvo" — ela só existe para cancelar o default do browser.
    e.preventDefault();
    e.stopPropagation();
    reset();
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) onFiles(files, zone);
  }

  return {
    rootRef, dragging, zone, reset,
    dropHandlers: { onDragEnter, onDragOver, onDragLeave, onDrop },
  };
}
