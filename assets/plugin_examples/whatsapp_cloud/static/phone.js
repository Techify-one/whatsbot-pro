// @ts-check
//
// Cópia autossuficiente do formatador de telefone do core (plano 92 · C1).
//
// POR QUE UMA CÓPIA, e não um import: `formatPhoneDisplay` mora em
// `web/static/js/utils/phone.js`, que NÃO é `services/api.js` — logo não chega
// pela allowlist `api.services`. As alternativas eram importar por URL absoluta
// (`/static/js/utils/phone.js`, precedente vivo no plugin `protocolos`) ou
// copiar. Copiamos, pelo mesmo princípio já adotado na família Meta (duas
// cópias de `meta_graph.py`, CLAUDE.md · plano 76·F9): o zip do plugin é
// autossuficiente e não quebra se o core mover um caminho interno.
//
// O PREÇO da cópia é divergir do canônico em silêncio — e o histórico do arquivo
// original mostra que isso já aconteceu (duas famílias divergentes consolidadas
// no plano 23·R1, sendo que a família A errava justamente o número BR de 12
// dígitos). Por isso o `phone.test.js` ao lado ancora os dois formatos.
//
// Sincronize com `web/static/js/utils/phone.js` quando aquele mudar.

/**
 * Formata um telefone (só dígitos, sem `+`) para exibição.
 *
 * @param {string | null | undefined} phone - dígitos E.164-ish, ex. "5511999998888".
 * @returns {string} telefone agrupado (ex. "+55 (11) 99999-8888"), ou "" quando vazio.
 */
export function formatPhoneDisplay(phone) {
  if (!phone) return '';
  const p = String(phone);

  // Celular BR: 55 + DDD (2) + assinante (9) → +55 (AA) XXXXX-XXXX
  if (p.length === 13 && p.startsWith('55')) {
    return `+${p.slice(0, 2)} (${p.slice(2, 4)}) ${p.slice(4, 9)}-${p.slice(9)}`;
  }
  // Fixo BR / celular legado: 55 + DDD (2) + assinante (8) → +55 (AA) XXXX-XXXX
  if (p.length === 12 && p.startsWith('55')) {
    return `+${p.slice(0, 2)} (${p.slice(2, 4)}) ${p.slice(4, 8)}-${p.slice(8)}`;
  }
  // Internacional genérico ≥12 dígitos: país (2) + área (2) + resto, 5-e-resto.
  if (p.length >= 12) {
    return `+${p.slice(0, 2)} (${p.slice(2, 4)}) ${p.slice(4, 9)}-${p.slice(9)}`;
  }
  // Formato curto/desconhecido: só prefixa com `+`.
  return `+${p}`;
}
