// @ts-check
//
// "Este clique é para o app ou para o navegador?" — plano 106 · F1.
//
// O painel é uma SPA que navega por `history.pushState` dentro de `onClick` de
// `<div>`/`<tr>`/`<button>`. Elemento que não é `<a href>` não tem semântica de
// link: o navegador não tem o que abrir em outra guia, então Ctrl+clique e clique
// do meio simplesmente não fazem nada. O único ponto do core que acertava era o
// `MenuItem` da engrenagem, com a condição escrita inline numa linha só
// (`GearMenu.js:20`) — copiável, mas não reusável e sem teste.
//
// Aqui essa regra existe UMA vez, decide sem tocar em DOM nem em estado, e é
// testável. O interceptor do shell (F2) e as superfícies que não podem virar
// anchor (F5) consomem daqui.
//
// PURO: sem preact, sem DOM, sem rede, sem estado de módulo. `URL` é global
// padrão (browser e Node), não é API de DOM.

/**
 * @typedef {Object} ClickLike
 * @property {number} [button] - 0 esquerdo, 1 meio, 2 direito
 * @property {boolean} [ctrlKey]
 * @property {boolean} [metaKey]
 * @property {boolean} [shiftKey]
 * @property {boolean} [altKey]
 */

/**
 * @typedef {Object} AnchorLike
 * @property {string} [href] - o href RESOLVIDO (`a.href`) ou o atributo cru
 * @property {string} [target] - `''` quando ausente (semântica da propriedade)
 * @property {boolean} [download] - passe `a.hasAttribute('download')`, NÃO `a.download`
 * @property {Record<string, any>} [dataset] - `data-no-spa` desliga o interceptor
 */

/**
 * O clique deve ser entregue ao NAVEGADOR em vez de tratado pelo app?
 *
 * É a MESMA condição que o `MenuItem` da engrenagem já usava, extraída: qualquer
 * botão que não o esquerdo, ou qualquer modificador. Deliberadamente mais larga
 * que `shouldOpenInNewTab` — Shift (nova janela) e Alt (baixar) também são gestos
 * do navegador, e cancelá-los seria roubar do usuário um comportamento nativo.
 *
 * @param {ClickLike|null|undefined} e
 * @returns {boolean}
 */
export function isModifiedClick(e) {
  if (!e) return false;
  if (e.button != null && e.button !== 0) return true;
  return !!(e.ctrlKey || e.metaKey || e.shiftKey || e.altKey);
}

/**
 * O gesto pede especificamente NOVA GUIA?
 *
 * Só Ctrl/⌘ + clique esquerdo e o clique do meio. Shift (nova JANELA) e Alt
 * (baixar) ficam de fora de propósito: as superfícies do Grupo C abrem a guia
 * na mão com `window.open`, e emular "nova janela"/"baixar" ali seria adivinhar.
 * Nelas, esses dois gestos caem na ação normal — que é o comportamento de hoje.
 *
 * @param {ClickLike|null|undefined} e
 * @returns {boolean}
 */
export function shouldOpenInNewTab(e) {
  if (!e) return false;
  if (e.button === 1) return true;            // clique do meio (click ou auxclick)
  if (e.button != null && e.button !== 0) return false;  // direito e afins: nunca
  return !!(e.ctrlKey || e.metaKey);
}

/**
 * O href aponta para dentro do próprio painel?
 *
 * Recusa `mailto:`/`tel:`/`javascript:`/`blob:`/`data:` (protocolo), outro host
 * (origin) e a âncora de mesma página (`#`). É o predicado que impede o
 * interceptor global de sequestrar link de mensagem, de mídia ou o de saldo da
 * Techify.
 *
 * @param {string|null|undefined} href
 * @param {string} origin - `window.location.origin`, ou a URL completa da página
 *   (qualquer URL absoluta serve de base; a completa também resolve href relativo)
 * @returns {boolean}
 */
export function isInternalHref(href, origin) {
  if (typeof href !== 'string') return false;
  const raw = href.trim();
  if (!raw) return false;
  // Âncora de mesma página: `new URL('#x', origin)` resolveria para o próprio
  // endereço e passaria no teste de origin — mas não é navegação.
  if (raw.startsWith('#')) return false;
  let base;
  try { base = new URL(origin); } catch { return false; }
  let u;
  try { u = new URL(raw, base); } catch { return false; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
  return u.origin === base.origin;
}

/**
 * O que o interceptor da F2 (e as superfícies da F5) devem fazer com esta âncora.
 *
 * @param {AnchorLike|null|undefined} anchorLike
 * @param {string} origin - `window.location.origin`
 * @returns {{path: string}|null} `null` ⇒ deixe o navegador cuidar
 */
export function spaLinkTarget(anchorLike, origin) {
  if (!anchorLike) return null;
  const { href, target, download } = anchorLike;
  const dataset = anchorLike.dataset || {};
  // Opt-out declarativo: `data-no-spa` força recarga real (F2 item 2).
  if (dataset.noSpa != null) return null;
  // `target` vazio/ausente é o normal; `_self` é explicitamente "mesmo frame".
  if (target && target !== '_self') return null;
  if (download) return null;
  if (!isInternalHref(href, origin)) return null;
  const u = new URL(String(href).trim(), origin);
  return { path: u.pathname + u.search + u.hash };
}
