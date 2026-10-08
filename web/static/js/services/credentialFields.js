// Cofre de credenciais das tools de IA (plano 177) — regras PURAS do formulário.
//
// A LISTA nunca devolve o valor de um campo secreto: traz só `{key, secret,
// has_value}` (e `value` apenas quando `secret === false`). O valor só vem quando
// o operador clica no "olho" (rota de revelar: um campo por vez, auditada). Por
// isso o PUT tem uma semântica de três vias, e este módulo é quem a monta:
//   - campo com `value`      → grava (cria ou troca)
//   - campo SEM `value`      → mantém o atual (editar a URL não obriga a redigitar a chave)
//   - campo ausente da lista → remove
// A mesma regra mora em server/routes/ai_credentials.py; mantenha as duas iguais.

export const CRED_NAME_RE = /^[a-z][a-z0-9_]{0,63}$/;
export const FIELD_NAME_RE = /^[a-z][a-z0-9_]{0,31}$/;
export const MAX_FIELDS = 16;
export const MAX_VALUE_BYTES = 4096;

const byteLength = (text) => new TextEncoder().encode(String(text ?? '')).length;

let _seq = 0;
const nextUid = () => `f${++_seq}`;

// Um campo do formulário. `existing` distingue "já está no cofre" de "acabei de
// adicionar" — só o primeiro pode ficar sem valor (= manter).
function formField({ key = '', secret = true, value = '', hasValue = false, existing = false } = {}) {
  return {
    uid: nextUid(),
    key,
    secret,
    value,          // o que o operador digitou (secreto: sempre começa vazio)
    original: value, // valor em claro que o servidor mostrou (só campo não secreto)
    wasSecret: secret,
    hasValue,
    existing,
    visible: false,  // o olho: mostra o texto em vez de bolinhas
    revealed: null,  // valor trazido pelo olho (null = nunca revelado nesta abertura)
  };
}

export function initialCredentialForm(cred = null) {
  const fields = cred && Array.isArray(cred.fields)
    ? cred.fields.map((f) => formField({
      key: f.key,
      secret: f.secret !== false,
      value: f.secret === false ? String(f.value ?? '') : '',
      hasValue: !!f.has_value,
      existing: true,
    }))
    : [];
  return {
    name: (cred && cred.name) || '',
    description: (cred && cred.description) || '',
    fields,
  };
}

// Campo novo nasce SECRETO: é o padrão seguro (P1) — quem quer ver o valor
// (uma URL, por exemplo) desmarca de propósito.
export function addField(form) {
  return { ...form, fields: [...form.fields, formField({ secret: true })] };
}

export function removeField(form, uid) {
  return { ...form, fields: form.fields.filter((f) => f.uid !== uid) };
}

export function updateField(form, uid, patch) {
  return {
    ...form,
    fields: form.fields.map((f) => (f.uid === uid ? { ...f, ...patch } : f)),
  };
}

// Um campo secreto que passa a ser público exporia o valor guardado na próxima
// listagem — então o operador tem de reinformá-lo (e ver o que está publicando).
function needsValue(field) {
  if (!field.existing) return true;
  if (field.wasSecret && !field.secret) return true;
  return false;
}

// Há valor a enviar para este campo?
//   - secreto: o que foi digitado (vazio = manter);
//   - público: o digitado, se mudou em relação ao que o servidor mostrou.
function valueToSend(field) {
  if (field.value === '') return null;
  // Segredo que vira público: o servidor EXIGE o valor de novo, mesmo que seja o
  // mesmo que o olho acabou de mostrar.
  if (field.existing && field.wasSecret && !field.secret) return field.value;
  // Inalterado desde que o servidor o mostrou (campo público) ou desde que o olho o
  // revelou ⇒ manter, não reenviar.
  if (field.existing && !field.wasSecret && field.value === field.original) return null;
  if (field.existing && field.revealed !== null && field.value === field.revealed) return null;
  return field.value;
}

export function validateCredentialForm(form, { isNew = false } = {}) {
  const errors = {};
  const name = (form.name || '').trim();
  if (isNew && !CRED_NAME_RE.test(name)) {
    errors.name = 'Use minúsculas, números e _ (começando por letra, até 64).';
  }
  if (form.fields.length === 0) errors.fields = 'Adicione ao menos um campo.';
  if (form.fields.length > MAX_FIELDS) errors.fields = `No máximo ${MAX_FIELDS} campos.`;

  const seen = new Set();
  const fieldErrors = {};
  for (const f of form.fields) {
    const key = (f.key || '').trim();
    let err = '';
    if (!FIELD_NAME_RE.test(key)) err = 'Use minúsculas, números e _ (até 32).';
    else if (seen.has(key)) err = 'Nome repetido.';
    else if (needsValue(f) && f.value === '') err = 'Informe o valor.';
    else if (byteLength(f.value) > MAX_VALUE_BYTES) err = `Valor acima de ${MAX_VALUE_BYTES} bytes.`;
    seen.add(key);
    if (err) fieldErrors[f.uid] = err;
  }
  if (Object.keys(fieldErrors).length) errors.fieldErrors = fieldErrors;
  return { ok: Object.keys(errors).length === 0, errors };
}

// Corpo do PUT /api/ai/credentials/{name}.
export function credentialPayload(form) {
  return {
    description: (form.description || '').trim(),
    fields: form.fields.map((f) => {
      const out = { key: f.key.trim(), secret: !!f.secret };
      const value = valueToSend(f);
      if (value !== null) out.value = value;
      return out;
    }),
  };
}

// ── O olho ──────────────────────────────────────────────────────────────────
// 'fetch'  → o valor está no servidor: busca (e audita) antes de mostrar;
// 'toggle' → o valor já está na tela (digitado ou já revelado): só alterna.
export function eyeAction(field) {
  const unfetched = field.existing && field.wasSecret && field.hasValue
    && field.value === '' && field.revealed === null;
  return unfetched ? 'fetch' : 'toggle';
}

// Valor que o servidor devolveu ao clicar no olho: vira o texto do campo e passa a
// contar como "inalterado" (revealed), então salvar sem mexer não o reenvia.
export function applyReveal(form, uid, value) {
  return updateField(form, uid, { value, revealed: value, visible: true });
}

// Oculta de novo. Se o texto ainda é o que o olho trouxe, ele SAI da tela (volta ao
// "manter o atual"); se o operador digitou outra coisa, a edição dele fica, mascarada.
export function hideField(form, uid) {
  return {
    ...form,
    fields: form.fields.map((f) => {
      if (f.uid !== uid) return f;
      const untouched = f.revealed !== null && f.value === f.revealed;
      return { ...f, visible: false, value: untouched ? '' : f.value, revealed: untouched ? null : f.revealed };
    }),
  };
}

// Rótulo do indicador de um campo já guardado ("definida · alterada por X em Y"
// fica por conta da tela, que conhece o nome e a data).
export function fieldStatus(field) {
  if (!field.existing) return 'novo';
  if (field.revealed !== null && field.value === field.revealed) return 'definido';
  if (field.value !== '' && (field.secret || field.value !== field.original)) return 'será alterado';
  return field.hasValue ? 'definido' : 'vazio';
}
