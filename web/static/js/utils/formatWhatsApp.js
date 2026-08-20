/**
 * Convert WhatsApp formatting markers to HTML.
 * Escapes HTML first to prevent XSS, then applies formatting.
 */

import { linkifyToTokens } from '../services/messageEntities.js';

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function formatWhatsApp(text, mentionNames = []) {
  if (!text) return '';
  let s = escapeHtml(text);

  // Code block (``` must come before inline `)
  s = s.replace(/```([\s\S]+?)```/g,
    '<pre style="background:#1e1e1e;color:#d4d4d4;padding:6px 8px;border-radius:4px;overflow-x:auto;font-family:monospace;font-size:12px;margin:4px 0;white-space:pre-wrap">$1</pre>');

  // Inline code
  s = s.replace(/`([^`\n]+?)`/g,
    '<code style="background:#1e1e1e;color:#d4d4d4;padding:1px 4px;border-radius:3px;font-family:monospace;font-size:13px">$1</code>');

  // Bold
  s = s.replace(/\*([^\*\n]+?)\*/g, '<b>$1</b>');

  // Italic (word boundaries to avoid matching underscores in URLs)
  s = s.replace(/\b_((?!_)[^\n]+?)_\b/g, '<i>$1</i>');

  // Strikethrough
  s = s.replace(/~([^~\n]+?)~/g, '<s>$1</s>');

  // Entidades (URL, e-mail, telefone, JID) — plano 97 · F2. As duas regras que
  // moravam aqui (URL → âncora; `\d+@\w+` → span de JID) viraram uma chamada ao
  // módulo puro `services/messageEntities.js`, que também detecta e-mail e
  // telefone e carimba `data-entity`/`data-value` para o menu de contexto ler.
  // Roda DEPOIS do escape (invariante de segurança) e na MESMA posição de antes:
  // depois do tachado, antes das menções.
  //
  // A linkificação devolve TOKENS opacos, não HTML: assim a regra de menção
  // abaixo não consegue casar dentro de um `href` (um grupo com um membro
  // chamado "Empresa" corromperia `mailto:contato@empresa.com`). O `restore()`
  // reidrata as âncoras no fim, quando nenhuma outra regra roda mais.
  const linkified = linkifyToTokens(s);
  s = linkified.text;

  // @mentions: known group member names + the mention-all keywords (@todos, …).
  // Names are escaped the same way the text was, then regex-escaped, so they
  // match the already-escaped string. Longest names first so a short name does
  // not shadow a longer one. The mention-all keywords are ALWAYS highlighted —
  // independent of whether any member names resolved — so @todos stands out the
  // same way a user mention does, even in groups with no named members.
  const ALL_KEYWORDS = ['todos', 'todes', 'todxs', 'all', 'everyone', 'geral'];
  const names = (mentionNames || [])
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .map(n => escapeRegex(escapeHtml(n)));
  const alts = [...names, ...ALL_KEYWORDS];
  const mentionRe = new RegExp('@(' + alts.join('|') + ')', 'gi');
  s = s.replace(mentionRe, '<span style="color:#53bdeb;font-weight:600">@$1</span>');

  // Reidrata as âncoras/spans de entidade — última coisa do pipeline.
  return linkified.restore(s);
}

/**
 * Highlight WhatsApp markup for the composer overlay (WYSIWYG-in-place).
 *
 * Unlike formatWhatsApp (used to render already-sent messages), this KEEPS the
 * markers (*, _, ~, `) in place — only dimmed — so the visible text stays
 * character-for-character identical to what the operator typed in the textarea
 * underneath. Dropping the markers would shorten the mirror text and drift the
 * caret. Markers are re-emitted as HTML entities (&#42; etc.) so that a later
 * pass never re-matches a marker this pass just produced.
 */
export function highlightComposerMarkup(text) {
  if (!text) return '';
  let s = escapeHtml(text);
  const dim = (inner) => `<span style="opacity:.4">${inner}</span>`;

  // ⚠️ REGRA DURA deste realce (plano 132 · F2/D3): nada aqui pode mudar a
  // MÉTRICA da fonte — largura de avanço dos glifos ou família tipográfica.
  //
  // O espelho e a <textarea> precisam quebrar a linha exatamente no mesmo ponto;
  // é só isso que faz o caret cair onde o operador clica. Até aqui o negrito era
  // <b> e o código era `font-family:monospace`, e ambos ocupam largura diferente
  // do texto normal. Medido em Chromium e Firefox: com DOIS negritos na mensagem,
  // 52 de 52 pontos de clique caíam errados (pior: 27 caracteres); com TRÊS, o
  // campo inteiro perdia uma linha (198px contra 218px). Nenhum ajuste de padding
  // conserta isso, porque o erro é proporcional ao texto marcado.
  //
  // O realce continua existindo — mudou a TÉCNICA. As três propriedades abaixo
  // são puramente de pintura e não entram no cálculo de layout:
  //   • negrito  → -webkit-text-stroke engorda o traço SEM mexer no avanço
  //                (suportado em Chrome, Firefox e Safari; `currentColor` segue
  //                o tema sozinho);
  //   • código   → tarja de fundo, mantendo a MESMA família tipográfica;
  //   • itálico e tachado → medidos como neutros (0 divergência), ficam como estão.
  //
  // Cor via token `--wa-text` (regra do CLAUDE.md): 12% do próprio texto vira uma
  // tarja discreta no claro e no escuro, sem hex cru em nenhum dos dois temas.
  const CODE_STYLE = 'background:rgb(var(--wa-text) / .12);border-radius:3px';
  const BOLD_STYLE = '-webkit-text-stroke:.4px currentColor';

  // Code block (``` must come before inline `)
  s = s.replace(/```([\s\S]+?)```/g,
    (m, inner) => dim('&#96;&#96;&#96;') + `<span style="${CODE_STYLE}">` + inner + '</span>' + dim('&#96;&#96;&#96;'));

  // Inline code
  s = s.replace(/`([^`\n]+?)`/g,
    (m, inner) => dim('&#96;') + `<span style="${CODE_STYLE}">` + inner + '</span>' + dim('&#96;'));

  // Bold — authored with DOUBLE asterisks (**texto**). Collapsed to WhatsApp's
  // single-asterisk wire format by toWhatsAppMarkup() on send.
  s = s.replace(/\*\*([^\n]+?)\*\*/g,
    (m, inner) => dim('&#42;&#42;') + `<span style="${BOLD_STYLE}">` + inner + '</span>' + dim('&#42;&#42;'));

  // Italic (word boundaries to avoid matching underscores in URLs)
  s = s.replace(/\b_((?!_)[^\n]+?)_\b/g,
    (m, inner) => dim('&#95;') + '<i>' + inner + '</i>' + dim('&#95;'));

  // Strikethrough
  s = s.replace(/~([^~\n]+?)~/g,
    (m, inner) => dim('&#126;') + '<s>' + inner + '</s>' + dim('&#126;'));

  // A LINHA FINAL que o espelho não gera sozinho (plano 132 · F1).
  //
  // Uma <textarea> reserva uma última linha vazia para o caret quando o valor
  // termina em quebra; um bloco `white-space: pre-wrap` NÃO gera essa linha (a
  // quebra no fim do bloco é descartada pela especificação). O espelho ficava
  // então exatamente 20px — uma linha — mais curto que o textarea, e como
  // `syncMirror` copia `textarea.scrollTop` para um elemento cuja rolagem máxima
  // é menor, o valor era TRUNCADO: o campo inteiro passava a exibir conteúdo uma
  // linha adiantado. O operador clicava no texto que via e o caret caía ~65
  // caracteres à frente, no meio da mensagem — o chamado da investigação 131.
  //
  // Um <br> só, mesmo com várias quebras no fim: o espelho fica sempre UMA linha
  // curto, nunca duas (a penúltima quebra já produz linha própria; só a última é
  // descartada). E <br> em vez de "\n" literal porque é ELEMENTO, não texto:
  // `textContent` não muda e a paridade de contagem — o invariante de que o
  // espelho tem o mesmo comprimento do valor da textarea — sobrevive intacta.
  if (text.endsWith('\n')) s += '<br>';

  return s;
}

/**
 * Convert the composer's authoring markup to WhatsApp's wire format before
 * sending. Bold is AUTHORED as **texto** (two asterisks), but WhatsApp's wire
 * format for bold is *texto* (one asterisk) — collapse the pairs so the
 * recipient sees clean bold. Italic (_), strikethrough (~) and code (`) already
 * match WhatsApp's wire format and pass through untouched. Mirrors the bold
 * regex in highlightComposerMarkup so the preview and the sent text agree.
 */
export function toWhatsAppMarkup(text) {
  if (!text) return text;
  return text.replace(/\*\*([^\n]+?)\*\*/g, '*$1*');
}
