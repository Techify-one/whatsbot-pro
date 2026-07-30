// @ts-check
//
// Detecção de ENTIDADES no texto de uma mensagem — URL, e-mail, telefone e JID
// do WhatsApp (plano 97 · F1).
//
// Módulo PURO: sem Preact, sem DOM (fora do leitor de `dataset`), sem clipboard,
// sem rede. Toda a inteligência de "o que é isso no texto e o que dá para fazer
// com isso" mora aqui, testada por `node --test messageEntities.test.js`, no
// padrão de `mediaLimits.js` / `drafts.js` / `conversationRows.js`.
//
// ── INVARIANTE DE SEGURANÇA (plano 97 · D5) ──────────────────────────────────
// `linkifyEntities` recebe HTML **já escapado** por `formatWhatsApp` e NUNCA
// escapa nem desescapa nada. Consequências:
//   • o texto que vira `href`/`data-value` já teve `<`, `>`, `&`, `"` e `'`
//     convertidos em entidades, então não há como fechar o atributo nem injetar
//     tag — o próprio navegador desfaz as entidades ao ler `el.href`/`dataset`;
//   • um `href` só nasce de (a) um match de `https?://…`, (b) `mailto:` sobre um
//     e-mail casado, ou (c) `tel:`/`wa.me` sobre uma string de DÍGITOS. Não há
//     caminho por onde texto arbitrário vire esquema de URL, logo `javascript:`
//     é inalcançável por construção.
//
// ── ÂNCORA NÃO PODE SER REPROCESSADA (plano 97 · F1 item 4) ──────────────────
// `formatWhatsApp` ainda roda a regra de `@menção` DEPOIS da linkificação. Sem
// proteção, um grupo com um membro chamado "Empresa" faria a menção casar dentro
// de `href="mailto:contato@empresa.com"` e corromper o HTML.
// DECISÃO: **token/placeholder**. `linkifyToTokens()` troca cada âncora por um
// sentinela opaco (`U+E000<n>U+E001` — sem `@`, sem `<`, sem `*`/`_`/`~`, fora
// de qualquer alfabeto que o usuário digite) e devolve `restore()` para reidratar
// no FIM do pipeline. É genérico: qualquer regra futura fica igualmente cega ao
// markup gerado aqui. `linkifyEntities()` é o atalho tokeniza+restaura para quem
// não tem regras depois (e para os testes).
//
// Detecção só acontece FORA de tags (`<…>`): o texto que chega já contém
// `<pre style="…">`, `<b>`, `<code style="…">` das regras anteriores, e nenhum
// atributo pode virar entidade.

import { formatPhoneDisplay } from '../utils/phone.js';

/**
 * @typedef {'url'|'email'|'phone'|'jid'} EntityKind
 * @typedef {{kind: EntityKind, value: string, display: string}} Entity
 * @typedef {{id: string, label: string, icon: string, href?: string, copy?: string}} EntityAction
 */

// Sufixos de JID do WhatsApp (os tipos de channels/jid.py). Lista FECHADA de
// propósito: era `[\w.]+`, o que fazia `5511999@gmail.com` cair aqui como
// "JID" e virar um <span> morto em vez de um e-mail clicável (§2.4).
const JID_SUFFIXES = ['s\\.whatsapp\\.net', 'lid', 'g\\.us', 'c\\.us', 'broadcast', 'newsletter'];

// ⚠️ ORDEM (a alternância decide o empate quando dois padrões começam na MESMA
// posição). O plano previa e-mail antes de JID; com o sufixo de JID já fechado,
// JID vem ANTES — senão `5511999999999@s.whatsapp.net` (que também casa a forma
// de e-mail) viraria `mailto:`, uma regressão. Com a lista fechada os dois
// objetivos convivem: JID de verdade continua JID, `…@gmail.com` vira e-mail.
const ENTITY_RE = new RegExp(
  [
    // 1) URL — o mesmo padrão de sempre (para no `<` para não comer tag).
    '(?<url>https?:\\/\\/[^\\s<]+)',
    // 2) JID do WhatsApp — dígitos + sufixo conhecido, sem colar em palavra.
    `(?<jid>\\d{7,15}@(?:${JID_SUFFIXES.join('|')})(?![\\w.-]))`,
    // 3) E-mail.
    '(?<email>[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+)',
    // 4) Telefone — CONSERVADOR (D4): exige `+` internacional ou máscara BR
    //    completa. Linkificar dígito solto viraria desastre neste produto
    //    (`PROT-12345678`, valores, datas, CPF, id de pedido).
    '(?<phone>\\+\\d[\\d\\s().-]{7,17}\\d|\\(\\d{2}\\)\\s?\\d{4,5}-\\d{4})',
  ].join('|'),
  'g',
);

// Estilo inline das entidades — IDÊNTICO ao que a bolha já usava, para a mudança
// não ter efeito visual nenhum.
const LINK_STYLE = 'color:#53bdeb;text-decoration:underline;word-break:break-all';
const JID_STYLE = 'color:#53bdeb;text-decoration:underline;cursor:default';

// Sentinela do token: dois code points da Área de Uso Privado do Unicode. Não
// existem em texto digitado, não são `\w` (a regra de menção não os atravessa)
// e não significam nada para nenhuma regra do pipeline.
const TOKEN_OPEN = '\uE000';
const TOKEN_CLOSE = '\uE001';

/**
 * Dígitos E.164-ish (sem `+`) de um telefone detectado.
 *
 * A máscara BR (`(11) 99999-8888`) é, por construção, um número brasileiro sem
 * código de país — 10 ou 11 dígitos. Prefixamos `55` para que `tel:` e `wa.me`
 * apontem para o número certo; com `+` explícito, respeitamos o que veio.
 *
 * @param {string} value
 * @returns {string}
 */
function phoneDigits(value) {
  const digits = String(value || '').replace(/\D/g, '');
  if (String(value || '').trim().startsWith('+')) return digits;
  if (digits.length === 10 || digits.length === 11) return `55${digits}`;
  return digits;
}

/**
 * Monta o `href` de uma entidade. Único ponto onde um esquema de URL nasce.
 *
 * @param {EntityKind} kind
 * @param {string} value - já escapado quando vem do render; cru quando vem do DOM.
 * @returns {string|null}
 */
function hrefFor(kind, value) {
  if (kind === 'url') return value;
  if (kind === 'email') return `mailto:${value}`;
  if (kind === 'phone') return `tel:+${phoneDigits(value)}`;
  return null;                                   // jid: identificador interno
}

/**
 * Rótulo humano de uma entidade (usado em `display`).
 * @param {EntityKind} kind
 * @param {string} value
 * @returns {string}
 */
function displayFor(kind, value) {
  if (kind === 'phone') return formatPhoneDisplay(phoneDigits(value));
  return value;
}

/**
 * Substitui as entidades de um HTML JÁ ESCAPADO por tokens opacos e devolve o
 * reidratador. Ver o cabeçalho do módulo para o porquê do token.
 *
 * @param {string} escapedHtml
 * @returns {{text: string, restore: (s: string) => string}}
 */
export function linkifyToTokens(escapedHtml) {
  const s = String(escapedHtml == null ? '' : escapedHtml);
  /** @type {string[]} */
  const chunks = [];

  const tokenize = (segment) => segment.replace(ENTITY_RE, (match, ...rest) => {
    const groups = /** @type {Record<string, string|undefined>} */ (rest[rest.length - 1]);
    const kind = /** @type {EntityKind} */ (
      groups.url ? 'url' : groups.jid ? 'jid' : groups.email ? 'email' : 'phone'
    );
    const value = match;
    const html = kind === 'jid'
      ? `<span data-entity="jid" data-value="${value}" style="${JID_STYLE}">${value}</span>`
      : `<a href="${hrefFor(kind, value)}" target="_blank" rel="noopener noreferrer"`
        + ` data-entity="${kind}" data-value="${value}" style="${LINK_STYLE}">${value}</a>`;
    chunks.push(html);
    return `${TOKEN_OPEN}${chunks.length - 1}${TOKEN_CLOSE}`;
  });

  // Só o texto FORA de tags é candidato — `<[^>]*>` casa apenas as tags que as
  // regras anteriores geraram (o texto do usuário já teve `<` virado `&lt;`).
  const text = s.split(/(<[^>]*>)/).map((part, i) => (i % 2 ? part : tokenize(part))).join('');

  const restore = (str) => String(str == null ? '' : str).replace(
    new RegExp(`${TOKEN_OPEN}(\\d+)${TOKEN_CLOSE}`, 'g'),
    (m, idx) => (chunks[Number(idx)] !== undefined ? chunks[Number(idx)] : m),
  );

  return { text, restore };
}

/**
 * Linkifica um HTML já escapado, de uma vez (tokeniza + reidrata).
 *
 * @param {string} escapedHtml - saída de `escapeHtml`, NUNCA texto cru.
 * @returns {string}
 */
export function linkifyEntities(escapedHtml) {
  const { text, restore } = linkifyToTokens(escapedHtml);
  return restore(text);
}

/**
 * Detecta a PRIMEIRA entidade de uma string curta e crua (fallback de seleção).
 *
 * @param {string|null|undefined} text
 * @returns {Entity|null}
 */
export function detectEntity(text) {
  const s = String(text == null ? '' : text);
  if (!s) return null;
  ENTITY_RE.lastIndex = 0;
  const m = ENTITY_RE.exec(s);
  ENTITY_RE.lastIndex = 0;
  if (!m || !m.groups) return null;
  const g = m.groups;
  const kind = /** @type {EntityKind} */ (
    g.url ? 'url' : g.jid ? 'jid' : g.email ? 'email' : 'phone'
  );
  return { kind, value: m[0], display: displayFor(kind, m[0]) };
}

/**
 * Lê a entidade de um elemento do DOM (o `closest('[data-entity]')` do alvo do
 * clique). Tolerante a `null`, a nós de texto e a `<svg>` (que não têm
 * `dataset`).
 *
 * @param {any} el
 * @returns {Entity|null}
 */
export function entityFromElement(el) {
  if (!el || !el.dataset) return null;
  const kind = /** @type {EntityKind} */ (el.dataset.entity || '');
  const value = el.dataset.value || (typeof el.textContent === 'string' ? el.textContent : '');
  if (!kind || !value) return null;
  if (kind !== 'url' && kind !== 'email' && kind !== 'phone' && kind !== 'jid') return null;
  return { kind, value, display: displayFor(kind, value) };
}

/**
 * Tabela de AÇÕES por tipo de entidade — o que o menu de contexto oferece.
 * Pura: não abre janela, não copia, não toca no DOM. Quem executa é a F3.
 *
 * `href` ⇒ item de navegação (`window.open`); `copy` ⇒ item de cópia.
 * `icon` é uma CHAVE ('open' | 'copy' | 'mail' | 'phone'), resolvida para o
 * ícone real por quem renderiza — o módulo continua sem depender de Preact.
 *
 * @param {Entity|null} entity
 * @returns {EntityAction[]}
 */
export function entityActions(entity) {
  if (!entity || !entity.kind || !entity.value) return [];
  const { kind, value } = entity;

  if (kind === 'url') {
    return [
      { id: 'open-url', label: 'Abrir link', icon: 'open', href: value },
      // "endereço do link" é o vocabulário do próprio navegador e não colide com
      // o "Copiar link da mensagem" (permalink interno) que já existe no menu.
      { id: 'copy-url', label: 'Copiar endereço do link', icon: 'copy', copy: value },
    ];
  }
  if (kind === 'email') {
    return [
      { id: 'open-email', label: 'Enviar e-mail', icon: 'mail', href: `mailto:${value}` },
      { id: 'copy-email', label: 'Copiar e-mail', icon: 'copy', copy: value },
    ];
  }
  if (kind === 'phone') {
    const digits = phoneDigits(value);
    return [
      { id: 'copy-phone', label: 'Copiar número', icon: 'copy', copy: value },
      { id: 'call-phone', label: 'Ligar', icon: 'phone', href: `tel:+${digits}` },
      { id: 'wa-phone', label: 'Conversar no WhatsApp', icon: 'open', href: `https://wa.me/${digits}` },
    ];
  }
  // JID é identificador interno: sem navegação, só a cópia dos dígitos.
  return [
    { id: 'copy-jid', label: 'Copiar número', icon: 'copy', copy: value.split('@')[0] },
  ];
}
