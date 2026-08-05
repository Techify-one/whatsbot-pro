// Movimentação de nós na árvore de regras — PURO (sem preact, sem DOM).
// Testável com `node --test assets/plugin_examples/retornos/static/tree.test.js`.
//
// Endereçamento por CAMINHO: um array de índices da raiz até o nó.
//   []        → a lista raiz
//   [2]       → o 3º item da raiz
//   [2, 0]    → o 1º item DENTRO do grupo que é o 3º item da raiz
// Um destino de soltura é o par (caminhoDoPai, índice de INSERÇÃO nessa lista),
// medido ANTES de remover a origem — quem corrige o deslocamento é `ajustarDestino`.
//
// Invariante do motor (`rules.js`): o 1º item de cada lista NÃO tem `conector`
// (é o acumulador inicial) e os demais têm 'E'/'OU'. `normalizar` restaura isso
// depois de cada movimento — o conector ACOMPANHA o item que se move.

/** Teto de aninhamento de grupos (o mesmo do botão "+ Grupo" no construtor). */
export const MAX_PROFUNDIDADE = 5;

export const ehGrupo = (no) => !!no && String(no.tipo || '').toLowerCase() === 'grupo';

/** Altura do nó: 0 para condição; grupo = 1 + a maior altura dos filhos. */
export function alturaDe(no) {
  if (!ehGrupo(no)) return 0;
  const filhos = (Array.isArray(no.regras) ? no.regras : []).map(alturaDe);
  return 1 + (filhos.length ? Math.max(...filhos) : 0);
}

/** A lista de regras num caminho de PAI; `null` quando o caminho não existe. */
export function listaEm(regras, pai) {
  let lista = Array.isArray(regras) ? regras : [];
  for (const i of (pai || [])) {
    const no = lista[i];
    if (!ehGrupo(no)) return null;
    lista = Array.isArray(no.regras) ? no.regras : [];
  }
  return lista;
}

/** O nó num caminho completo; `null` quando não existe. */
export function itemEm(regras, caminho) {
  if (!Array.isArray(caminho) || caminho.length === 0) return null;
  const pai = listaEm(regras, caminho.slice(0, -1));
  if (!pai) return null;
  return pai[caminho[caminho.length - 1]] || null;
}

/** `a` é prefixo de `b` (ou igual) — ou seja, `b` está DENTRO de `a`. */
export function ehPrefixo(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length > b.length) return false;
  return a.every((v, i) => b[i] === v);
}

// ── Edição imutável ────────────────────────────────────────────────────────

export function removerEm(regras, caminho) {
  const lista = Array.isArray(regras) ? regras : [];
  if (!Array.isArray(caminho) || caminho.length === 0) return lista;
  const [i, ...resto] = caminho;
  if (resto.length === 0) return lista.filter((_, idx) => idx !== i);
  return lista.map((no, idx) => (idx === i
    ? { ...no, regras: removerEm(no.regras || [], resto) }
    : no));
}

export function inserirEm(regras, pai, indice, item) {
  const lista = Array.isArray(regras) ? regras : [];
  if (!Array.isArray(pai) || pai.length === 0) {
    const out = lista.slice();
    out.splice(Math.max(0, Math.min(indice, out.length)), 0, item);
    return out;
  }
  const [i, ...resto] = pai;
  return lista.map((no, idx) => (idx === i
    ? { ...no, regras: inserirEm(no.regras || [], resto, indice, item) }
    : no));
}

/** Restaura a invariante do conector em TODA a árvore (idempotente). */
export function normalizar(regras) {
  return (Array.isArray(regras) ? regras : []).map((no, i) => {
    if (!no || typeof no !== 'object') return no;
    const out = { ...no };
    if (i === 0) delete out.conector;
    else if (!out.conector) out.conector = 'E';
    if (ehGrupo(out)) out.regras = normalizar(out.regras || []);
    return out;
  });
}

// ── Movimento ──────────────────────────────────────────────────────────────

// O destino foi medido com a origem ainda no lugar: se a remoção acontece ANTES
// dele na mesma lista, tudo dali para a frente anda uma casa para trás — inclusive
// um índice do próprio CAMINHO do pai (soltar dentro de um grupo que vem depois).
function ajustarDestino(origem, destinoPai, destinoIndice) {
  const k = origem.length - 1;
  const mesmaRamificacao = destinoPai.length >= k
    && origem.slice(0, k).every((v, i) => destinoPai[i] === v);
  if (!mesmaRamificacao) return { pai: destinoPai.slice(), indice: destinoIndice };
  const pai = destinoPai.slice();
  if (pai.length > k) {
    if (pai[k] > origem[k]) pai[k] -= 1;
    return { pai, indice: destinoIndice };
  }
  return { pai, indice: destinoIndice > origem[k] ? destinoIndice - 1 : destinoIndice };
}

/**
 * O movimento é aceitável? Recusa: caminho inexistente, soltar um grupo dentro
 * de si mesmo, estourar o teto de aninhamento e a soltura que não muda nada
 * (as duas bordas do próprio item).
 */
export function podeMover(regras, origem, destinoPai, destinoIndice,
                          maxProfundidade = MAX_PROFUNDIDADE) {
  if (!Array.isArray(origem) || origem.length === 0) return false;
  if (!Array.isArray(destinoPai) || !Number.isInteger(destinoIndice)) return false;
  const item = itemEm(regras, origem);
  if (!item) return false;
  if (ehPrefixo(origem, destinoPai)) return false; // dentro de si mesmo
  const destino = listaEm(regras, destinoPai);
  if (destino === null) return false;
  if (destinoIndice < 0 || destinoIndice > destino.length) return false;
  if (destinoPai.length + alturaDe(item) > maxProfundidade) return false;

  const k = origem.length - 1;
  const mesmaLista = destinoPai.length === k
    && origem.slice(0, k).every((v, i) => destinoPai[i] === v);
  if (mesmaLista && (destinoIndice === origem[k] || destinoIndice === origem[k] + 1)) {
    return false; // já está exatamente aí
  }
  return true;
}

/**
 * Move o nó de `origem` para (destinoPai, destinoIndice) e devolve a NOVA lista
 * raiz normalizada. `null` quando o movimento não é aceitável.
 */
export function mover(regras, origem, destinoPai, destinoIndice,
                      maxProfundidade = MAX_PROFUNDIDADE) {
  if (!podeMover(regras, origem, destinoPai, destinoIndice, maxProfundidade)) return null;
  const item = itemEm(regras, origem);
  const semItem = removerEm(regras, origem);
  const { pai, indice } = ajustarDestino(origem, destinoPai, destinoIndice);
  return normalizar(inserirEm(semItem, pai, indice, item));
}

export default { mover, podeMover, normalizar, alturaDe, itemEm, listaEm, ehGrupo, ehPrefixo };
