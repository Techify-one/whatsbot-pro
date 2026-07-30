// Semeadura dos valores do formulário de campos do PROTOCOLO — módulo PURO (sem
// preact/htm/DOM/rede), testável com `node --test` (precedente: melhorias/static/
// markdown.test.js).
//
// Por que existe: a lógica vivia inline num `useState(() => …)` do DetailModal, onde
// rodava UMA única vez, no mount. Quando o modal montava ANTES de o GET
// `/field-defs?scope=protocolo` responder (deep-link `?detail=<id>`, F5, "Resolver e ir
// ao protocolo"), `protoDefs` era `[]` → o estado nascia `{}` e NUNCA era re-semeado:
// campos salvos apareciam vazios e o obrigatório "Atendente" travava o "Finalizar
// protocolo". Extraída aqui para poder ser reaplicada quando as defs chegam (mergeSeed).
//
// Este arquivo não importa nada — é o piso da cadeia de imports do plugin.

// Campo cujo VALOR é uma LISTA: "Caixa de seleção" (checkboxes) sempre, ou "Lista de
// seleção" (select) com `multiple` ligado. Espelha logic._is_multi no backend.
// Mora aqui (e não em resolve_form.js, que re-exporta) para o módulo puro não precisar
// arrastar preact/htm — a regra continua tendo UMA definição só.
export const isMultiDef = (d) => (d && (d.type === 'checkboxes'
  || (d.type === 'select' && d.multiple)));

// Valores iniciais do form a partir das definições + do protocolo carregado.
// Regras (idênticas às que rodavam inline no DetailModal):
//  - `atendente`: mantém o assignee JÁ salvo; só sugere o usuário logado
//    (`defaultAssignee`) quando não há assignee gravado E o form é editável;
//  - `checkbox`: booleano (aceita o legado `'true'` em string);
//  - multi (checkboxes / select multiple): array (CSV salvo vira lista);
//  - demais: string ('' quando nulo).
// `protoDefs` vazio ⇒ `{}` — é a caracterização do bug: sem defs não há o que semear.
export function seedProtocolValues(protoDefs, protocolo, opts = {}) {
  const { defaultAssignee = null, readOnly = false } = opts;
  const at = protocolo || {};
  const fields = at.fields || {};
  const hasSavedAssignee = at.assignee_user_id != null && String(at.assignee_user_id).trim() !== '';
  const init = {};
  for (const d of (protoDefs || [])) {
    if (!d || !d.key) continue;
    const cur = d.type === 'atendente'
      ? (hasSavedAssignee ? at.assignee_user_id : (!readOnly ? defaultAssignee : at.assignee_user_id))
      : fields[d.key];
    if (d.type === 'checkbox') init[d.key] = (cur === true || cur === 'true');
    else if (isMultiDef(d)) init[d.key] = Array.isArray(cur)
      ? cur : (cur ? String(cur).split(',').map((s) => s.trim()).filter(Boolean) : []);
    else init[d.key] = (cur == null ? '' : String(cur));
  }
  return init;
}

// Valores iniciais do popup "Resolver atendimento" (ResolveForm). Irmão do
// seedProtocolValues acima, com UMA diferença de política: aqui o rótulo `atendente`
// cai SEMPRE em `defaultAssignee` (o usuário conectado, quem clicou em Resolver) quando
// `initialValues` não traz um valor — e o call site do beforeResolve não traz nenhum de
// propósito, porque cada atendimento começa do zero. `initialValues` continua sendo um
// seam (usado pelos testes e por call sites que queiram semear algo explicitamente).
//
// Demais regras idênticas: checkbox → bool (aceita o legado 'true'), multi → array (CSV
// vira lista), resto → string ('' quando nulo).
export function seedResolveValues(defs, opts = {}) {
  const { initialValues = {}, defaultAssignee = null } = opts;
  const init0 = initialValues || {};
  const init = {};
  for (const d of (defs || [])) {
    if (!d || !d.key) continue;
    const cur = init0[d.key];
    if (d.type === 'checkbox') init[d.key] = (cur === true || cur === 'true');
    else if (d.type === 'atendente') {
      const seeded = (cur == null || String(cur).trim() === '') ? defaultAssignee : cur;
      init[d.key] = (seeded == null ? '' : seeded);
    } else if (isMultiDef(d)) init[d.key] = Array.isArray(cur)
      ? cur : (cur ? String(cur).split(',').map((s) => s.trim()).filter(Boolean) : []);
    else init[d.key] = (cur == null ? '' : String(cur));
  }
  return init;
}

// Completa `current` com as chaves do `seed` que ele ainda NÃO tem. Nunca sobrescreve:
//  - o que o operador digitou fica;
//  - campo limpo de propósito (a chave existe com '' / [] / false) NÃO ressuscita.
// Devolve a MESMA referência quando não há nada a acrescentar (o setState do Preact
// então nem re-renderiza).
export function mergeSeed(current, seed) {
  const cur = current || {};
  const add = [];
  for (const k of Object.keys(seed || {})) if (!(k in cur)) add.push(k);
  if (!add.length) return cur;
  const out = { ...cur };
  for (const k of add) out[k] = seed[k];
  return out;
}
