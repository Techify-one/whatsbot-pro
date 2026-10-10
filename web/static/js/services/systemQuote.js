// Trecho citado, recolhível, dentro de um card de sistema (role=system).
//
// Um produtor (core OU plugin) delimita a parte longa do conteúdo com
// ``[[quote]]…[[/quote]]``. O restante do card (título, contexto, botão) fica sempre
// visível; só o trecho citado é recolhido, e apenas quando passa do limite de tamanho
// (citação que cabe nas linhas mostradas aparece inteira, sem controle nenhum; quem
// decide é a medição no componente, não o número de caracteres). Ex.: o plugin
// duvidas_aula cita o comentário do aluno.
//
// Puro (sem dependências) para ser testável com ``node --test``.

const QUOTE_RE = /\[\[quote\]\]([\s\S]*?)\[\[\/quote\]\]/g;

/** Quantas linhas da citação aparecem enquanto ela está recolhida. */
export const QUOTE_CLAMP_LINES = 4;

// Divide o conteúdo em segmentos, na ordem do texto: ``{ type: 'text', text }`` e
// ``{ type: 'quote', text }``. Segmento vazio é descartado; o texto ao redor perde
// só as quebras de linha das pontas (as do meio são preservadas).
export function parseQuote(content) {
  const src = content == null ? '' : String(content);
  const out = [];
  let last = 0;
  let m;
  QUOTE_RE.lastIndex = 0;
  while ((m = QUOTE_RE.exec(src)) !== null) {
    pushText(out, src.slice(last, m.index));
    const quoted = m[1].replace(/^\s+|\s+$/g, '');
    if (quoted) out.push({ type: 'quote', text: quoted });
    last = m.index + m[0].length;
  }
  pushText(out, src.slice(last));
  return out;
}

function pushText(out, raw) {
  const text = raw.replace(/^\s+|\s+$/g, '');
  if (text) out.push({ type: 'text', text });
}
