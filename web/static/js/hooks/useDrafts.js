// Re-render hook dos rascunhos do compositor. Quem MOSTRA rascunho (a sidebar)
// lê o texto de forma síncrona com `getDraft(key)` e assina aqui para re-render
// quando outra tela (o compositor, ou outra aba do navegador) mexer no mapa.
//
// `ignoreKey` = a conversa ABERTA. Enquanto o operador digita nela, a linha dela
// não mostra rascunho nem muda de lugar na lista (regra do painel), então essas
// mudanças não devem custar um re-render da sidebar a cada 400ms de digitação.
// Ao SAIR da conversa a seleção muda e a lista recalcula por conta própria.
//
// Fora de drafts.js de propósito: aquele módulo é PURO (sem preact) para rodar
// em `node --test`; este é o seam preact — mesmo molde do useProviderCatalog.
import { useState, useEffect, useRef } from 'preact/hooks';
import { subscribe, getDraftsVersion } from '../services/drafts.js';

/**
 * @param {string|null} [ignoreKey] - chave cujas mudanças não forçam re-render.
 * @returns {number} versão atual do mapa (serve de dependência de useMemo).
 */
export function useDrafts(ignoreKey = null) {
  const [version, setVersion] = useState(getDraftsVersion);
  const ignoreRef = useRef(ignoreKey);
  ignoreRef.current = ignoreKey;
  useEffect(() => subscribe((v, changed) => {
    // `changed === null` = o mapa inteiro virou (troca de usuário, outra aba).
    if (changed && changed.every((k) => k === ignoreRef.current)) return;
    setVersion(v);
  }), []);
  return version;
}
