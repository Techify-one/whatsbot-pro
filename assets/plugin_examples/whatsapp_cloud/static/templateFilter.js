// Regras de exibição da lista de templates (plano 92 · E1/E2/E3).
//
// Módulo PURO — sem preact, sem rede, sem DOM. É o que torna a ordem de
// aplicação testável (`templateFilter.test.js`), porque ela não é óbvia:
//
//   1. arquivado sai      (a menos que a aba seja "arquivadas")
//   2. aba                (todas | favoritas | arquivadas)
//   3. busca              (nome, categoria E CONTEÚDO)
//   4. favoritos primeiro (preservando, dentro de cada grupo, a ordem da Meta)
//
// A ordem importa: se a busca viesse antes do corte de arquivados, um template
// arquivado reapareceria só por casar o termo — o oposto do que "arquivar"
// promete. E favoritar não pode reordenar a lista inteira: dentro de cada grupo
// a ordem da Meta é preservada, senão o atendente perde a referência visual.

/** minúsculas + sem acento — buscar "cartao" tem de achar "cartão". */
export function normalize(text) {
  return String(text == null ? '' : text)
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '');
}

/**
 * Todo texto pesquisável de um template, em ordem de relevância.
 * O corpo é o que o atendente lembra ("aquele que fala em boleto"), então o
 * conteúdo pesa tanto quanto o nome.
 * @returns {{campo: string, texto: string}[]}
 */
export function searchableParts(tpl) {
  const parts = [];
  if (!tpl) return parts;
  if (tpl.name) parts.push({ campo: 'nome', texto: String(tpl.name) });
  if (tpl.category) parts.push({ campo: 'categoria', texto: String(tpl.category) });
  for (const c of (tpl.components || [])) {
    const tipo = String((c && c.type) || '').toLowerCase();
    if (c && c.text) {
      const campo = tipo === 'header' ? 'cabeçalho' : tipo === 'footer' ? 'rodapé' : 'corpo';
      parts.push({ campo, texto: String(c.text) });
    }
    for (const b of ((c && c.buttons) || [])) {
      if (b && b.text) parts.push({ campo: 'botão', texto: String(b.text) });
      if (b && b.url) parts.push({ campo: 'botão', texto: String(b.url) });
    }
  }
  return parts;
}

/**
 * O template casa a busca? Devolve também ONDE casou e um trecho, porque um
 * casamento invisível (só no corpo) parece falso positivo na lista.
 * @returns {{match: boolean, campo: string|null, trecho: string|null}}
 */
export function matchesQuery(tpl, query) {
  const q = normalize(query).trim();
  if (!q) return { match: true, campo: null, trecho: null };
  for (const part of searchableParts(tpl)) {
    const hay = normalize(part.texto);
    const at = hay.indexOf(q);
    if (at < 0) continue;
    // Casamento por nome/categoria dispensa trecho: já está visível na linha.
    if (part.campo === 'nome' || part.campo === 'categoria') {
      return { match: true, campo: part.campo, trecho: null };
    }
    const RAIO = 28;
    const ini = Math.max(0, at - RAIO);
    const fim = Math.min(part.texto.length, at + q.length + RAIO);
    const trecho = (ini > 0 ? '…' : '') + part.texto.slice(ini, fim).replace(/\s+/g, ' ').trim()
      + (fim < part.texto.length ? '…' : '');
    return { match: true, campo: part.campo, trecho };
  }
  return { match: false, campo: null, trecho: null };
}

export const ABAS = ['todas', 'favoritas', 'arquivadas'];

/**
 * Aplica as 4 etapas e devolve as linhas prontas para render.
 *
 * @param {Array} templates - a lista como veio da Meta (ordem preservada).
 * @param {{query?: string, tab?: string, favorites?: Set<string>|Array<string>,
 *          archived?: Set<string>|Array<string>}} view
 * @returns {Array} cada item = o template + {_favorito, _arquivado, _campo, _trecho}
 */
export function applyView(templates, view = {}) {
  const { query = '', tab = 'todas' } = view;
  const fav = view.favorites instanceof Set ? view.favorites : new Set(view.favorites || []);
  const arq = view.archived instanceof Set ? view.archived : new Set(view.archived || []);
  const naAba = tab === 'arquivadas' ? 'arquivadas' : (tab === 'favoritas' ? 'favoritas' : 'todas');

  const out = [];
  for (const t of (templates || [])) {
    const nome = (t && t.name) || '';
    const arquivado = arq.has(nome);
    const favorito = fav.has(nome);

    // 1 + 2: aba decide quem entra. "todas" e "favoritas" NUNCA mostram arquivado.
    if (naAba === 'arquivadas') {
      if (!arquivado) continue;
    } else {
      if (arquivado) continue;
      if (naAba === 'favoritas' && !favorito) continue;
    }

    // 3: busca
    const m = matchesQuery(t, query);
    if (!m.match) continue;

    out.push({ ...t, _favorito: favorito, _arquivado: arquivado, _campo: m.campo, _trecho: m.trecho });
  }

  // 4: favoritos primeiro, estável (na aba "arquivadas" a estrela não reordena —
  // lá o critério é "o que foi arquivado", e reordenar confundiria).
  if (naAba === 'arquivadas') return out;
  const favs = out.filter(t => t._favorito);
  const resto = out.filter(t => !t._favorito);
  return favs.concat(resto);
}

/** Quantos itens cada aba mostraria com a busca atual — alimenta os contadores. */
export function contarPorAba(templates, view = {}) {
  const base = { query: view.query || '', favorites: view.favorites, archived: view.archived };
  return {
    todas: applyView(templates, { ...base, tab: 'todas' }).length,
    favoritas: applyView(templates, { ...base, tab: 'favoritas' }).length,
    arquivadas: applyView(templates, { ...base, tab: 'arquivadas' }).length,
  };
}
