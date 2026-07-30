// Diff dos snapshots `before_json`/`after_json` da trilha de auditoria.
//
// A trilha GRAVA o snapshot inteiro do recurso (append-only, nada aqui muda o
// que está no banco) — mas ler um JSON de 30 linhas para achar o único campo
// que mudou é inviável na tela. Este módulo recorta os dois lados para as
// chaves que diferem — mais os campos de identificação do recurso, que dizem
// de QUEM é a mudança —, PRESERVANDO o aninhamento (o caminho até o campo é
// parte da informação: `config.ai.ai_enabled` ≠ `ai_enabled`).
//
// Módulo PURO (sem preact, sem DOM) — testado com `node --test`.

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

// Igualdade estrutural sobre valores JSON (o único universo possível aqui).
export function deepEqual(a, b) {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    return ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual(a[k], b[k]));
  }
  return false;
}

// Recorta `a` e `b` para os pares chave-valor que diferem. Chave presente em um
// só lado (adicionada/removida) fica no lado onde existe. Objetos aninhados são
// recursivos; arrays são tratados como valor atômico (mudou → vai inteiro).
export function diffObjects(a, b) {
  const before = {};
  const after = {};
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])];
  for (const k of keys) {
    const inA = Object.prototype.hasOwnProperty.call(a, k);
    const inB = Object.prototype.hasOwnProperty.call(b, k);
    if (inA && inB) {
      if (deepEqual(a[k], b[k])) continue;
      if (isPlainObject(a[k]) && isPlainObject(b[k])) {
        const [sub1, sub2] = diffObjects(a[k], b[k]);
        before[k] = sub1;
        after[k] = sub2;
      } else {
        before[k] = a[k];
        after[k] = b[k];
      }
    } else if (inA) {
      before[k] = a[k];
    } else {
      after[k] = b[k];
    }
  }
  return [before, after];
}

// Caminhos pontilhados das folhas que mudaram (ex.: 'config.ai.ai_enabled').
// Usado só para o resumo textual acima do diff.
export function changedPaths(before, after, prefix = '') {
  const out = [];
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  for (const k of keys) {
    const path = prefix ? `${prefix}.${k}` : k;
    const va = before[k];
    const vb = after[k];
    if (isPlainObject(va) && isPlainObject(vb)) {
      out.push(...changedPaths(va, vb, path));
    } else {
      out.push(path);
    }
  }
  return out;
}

// Chaves de IDENTIFICAÇÃO do recurso, mantidas no recorte mesmo quando não
// mudaram: sem elas o diff diz "ai_enabled virou true" sem dizer de QUEM. São
// nomes genéricos de identidade (não nomes de provider/recurso — o core não
// conhece nenhum), aplicados só no TOPO do objeto e só a valores escalares.
export const CONTEXT_KEYS = [
  'id', 'key', 'slug', 'name', 'display_name', 'title', 'label',
  'provider', 'phone', 'email', 'channel_id', 'plugin_id',
];

function isScalar(v) {
  return v === null || ['string', 'number', 'boolean'].includes(typeof v);
}

// Remonta um lado do diff acrescentando os campos de identificação, na ORDEM
// original do snapshot (identidade primeiro é o que o snapshot já traz).
export function withContext(source, diff, contextKeys = CONTEXT_KEYS) {
  const out = {};
  const inDiff = Object.prototype.hasOwnProperty.bind(diff);
  for (const k of Object.keys(source)) {
    if (inDiff(k)) out[k] = diff[k];
    else if (contextKeys.includes(k) && isScalar(source[k])) out[k] = source[k];
  }
  // Chave que só existe no diff (foi removida deste lado / é do outro lado).
  for (const k of Object.keys(diff)) {
    if (!Object.prototype.hasOwnProperty.call(out, k)) out[k] = diff[k];
  }
  return out;
}

// Parse defensivo: o valor gravado pode ser null, string vazia ou já inválido.
function parse(raw) {
  if (raw == null || raw === '') return { present: false, ok: false, value: null };
  try {
    return { present: true, ok: true, value: JSON.parse(raw) };
  } catch (_) {
    return { present: true, ok: false, value: null };
  }
}

export function prettyJson(raw) {
  if (raw == null || raw === '') return null;
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch (_) {
    return String(raw);
  }
}

/**
 * Monta o que a tela deve mostrar para um par before/after.
 *
 * Modos:
 *  - `diff`  — os dois lados são objetos JSON: mostra as chaves alteradas mais
 *              os campos de identificação do recurso (`CONTEXT_KEYS`).
 *  - `empty` — os dois lados são objetos e nada mudou.
 *  - `full`  — não dá para comparar (um lado ausente/inválido, ou raiz que não
 *              é objeto: create/delete, escalares, listas). Mostra tudo.
 *
 * `beforeFull`/`afterFull` vêm sempre preenchidos — a tela oferece "ver JSON
 * completo" e nada do registro original fica inacessível.
 */
export function auditDiffView(beforeRaw, afterRaw) {
  const beforeFull = prettyJson(beforeRaw);
  const afterFull = prettyJson(afterRaw);
  const b = parse(beforeRaw);
  const a = parse(afterRaw);

  const comparable = b.ok && a.ok && isPlainObject(b.value) && isPlainObject(a.value);
  if (!comparable) {
    return { mode: 'full', before: beforeFull, after: afterFull, beforeFull, afterFull, paths: [] };
  }

  const [bef, aft] = diffObjects(b.value, a.value);
  const paths = changedPaths(bef, aft);
  if (paths.length === 0) {
    return { mode: 'empty', before: null, after: null, beforeFull, afterFull, paths };
  }
  return {
    mode: 'diff',
    before: JSON.stringify(withContext(b.value, bef), null, 2),
    after: JSON.stringify(withContext(a.value, aft), null, 2),
    beforeFull,
    afterFull,
    paths,
  };
}
