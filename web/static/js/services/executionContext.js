// Visão de uma mensagem do step `llm_context` (tela de Execuções, plano 182).
//
// O backend grava o SYSTEM prompt inteiro (com o que os plugins injetaram) e
// trunca só o histórico (user/assistant). Execuções gravadas antes do plano 182
// têm o system cortado em 2.000 caracteres — sem como recuperar o resto.
//
// Este módulo decide, para cada mensagem, COMO a tela a mostra:
//   - `curto`   : cabe na prévia, aparece inteiro e sem controle nenhum;
//   - `completo`: longo e gravado por inteiro → botão Expandir/Minimizar;
//   - `legado`  : cortado na gravação → só o aviso, sem botão (nada a mostrar).
//
// Puro (sem DOM, sem dependências) para ser testável com `node --test`.

/** Linhas da prévia enquanto o prompt está minimizado (D11 do plano 182). */
export const PREVIEW_LINES = 6;

/** Acima disto (caracteres) o texto ganha o botão; abaixo, aparece inteiro. */
export const PREVIEW_MAX_CHARS = 600;

/** Estimativa grosseira usada no rótulo "≈N tokens" (nunca é o valor cobrado). */
export const CHARS_PER_TOKEN = 4;

const MARKER = '… (truncado)';

function textOf(msg) {
  const c = msg && msg.content;
  return c == null ? '' : String(c);
}

// Tamanho ORIGINAL da mensagem. Linha legada não tem `chars`: cai no tamanho do
// que está guardado (já cortado, sem o sufixo do corte).
export function charsOf(msg) {
  if (msg && Number.isFinite(msg.chars) && msg.chars >= 0) return msg.chars;
  const t = textOf(msg);
  return msg && msg.truncated && t.endsWith(MARKER) ? t.length - MARKER.length : t.length;
}

export function approxTokens(chars) {
  const n = Number(chars);
  return Number.isFinite(n) && n > 0 ? Math.ceil(n / CHARS_PER_TOKEN) : 0;
}

// `curto` | `completo` | `legado`. Legado = qualquer corte gravado; o plano 182
// só deixa de cortar o `system`, então um `truncated` aqui é sempre um corte real.
export function contextState(msg) {
  if (msg && msg.truncated) return 'legado';
  return textOf(msg).length > PREVIEW_MAX_CHARS ? 'completo' : 'curto';
}

// Prévia: primeiras `PREVIEW_LINES` linhas, limitadas a `PREVIEW_MAX_CHARS`.
// `cut` diz se algo ficou de fora (a tela só mostra reticências quando sim).
export function previewOf(content, lines = PREVIEW_LINES, maxChars = PREVIEW_MAX_CHARS) {
  const text = content == null ? '' : String(content);
  const head = text.split('\n').slice(0, lines).join('\n');
  const clipped = head.length > maxChars ? head.slice(0, maxChars) : head;
  return { text: clipped, cut: clipped.length < text.length };
}

// Separador de milhar pt-BR sem depender de Intl (ambiente de teste enxuto).
export function formatInt(n) {
  const v = Math.max(0, Math.trunc(Number(n) || 0));
  return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}

// Rótulo do botão: "31.563 caracteres · ≈7.891 tokens".
export function sizeLabel(msg) {
  const chars = charsOf(msg);
  const tokens = approxTokens(chars);
  return `${formatInt(chars)} caracteres · ≈${formatInt(tokens)} tokens`;
}

// Aviso do legado: "mostrando 2.000 de 31.563" quando o original é conhecido,
// senão só que foi cortado na gravação.
export function legacyNotice(msg) {
  const shown = textOf(msg).replace(MARKER, '').length;
  const original = msg && Number.isFinite(msg.chars) ? msg.chars : null;
  if (original != null && original > shown) {
    return `cortado na gravação — mostrando ${formatInt(shown)} de ${formatInt(original)}`;
  }
  return 'cortado na gravação, execução anterior à captura completa';
}
