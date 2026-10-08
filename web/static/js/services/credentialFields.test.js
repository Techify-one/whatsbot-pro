import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_FIELDS,
  MAX_VALUE_BYTES,
  addField,
  applyReveal,
  credentialPayload,
  eyeAction,
  fieldStatus,
  hideField,
  initialCredentialForm,
  removeField,
  updateField,
  validateCredentialForm,
} from './credentialFields.js';

const stored = {
  name: 'plataforma',
  description: 'API da plataforma',
  fields: [
    { key: 'url', secret: false, has_value: true, value: 'https://api.exemplo.com' },
    { key: 'chave', secret: true, has_value: true },
  ],
};

test('campo secreto nunca é preenchido pelo formulário', () => {
  const form = initialCredentialForm(stored);
  const chave = form.fields.find((f) => f.key === 'chave');
  assert.equal(chave.value, '');
  assert.equal(chave.hasValue, true);
  const url = form.fields.find((f) => f.key === 'url');
  assert.equal(url.value, 'https://api.exemplo.com');
});

test('editar só a descrição não envia valor de nenhum campo (manter)', () => {
  const form = { ...initialCredentialForm(stored), description: 'nova' };
  const body = credentialPayload(form);
  assert.equal(body.description, 'nova');
  for (const f of body.fields) assert.equal('value' in f, false, f.key);
});

test('trocar a URL envia só a URL; a chave segue sem value', () => {
  let form = initialCredentialForm(stored);
  const url = form.fields.find((f) => f.key === 'url');
  form = updateField(form, url.uid, { value: 'https://outra.exemplo.com' });
  const byKey = Object.fromEntries(credentialPayload(form).fields.map((f) => [f.key, f]));
  assert.equal(byKey.url.value, 'https://outra.exemplo.com');
  assert.equal('value' in byKey.chave, false);
});

test('digitar a chave nova envia o value dela', () => {
  let form = initialCredentialForm(stored);
  const chave = form.fields.find((f) => f.key === 'chave');
  form = updateField(form, chave.uid, { value: 'rbk_novo' });
  const out = credentialPayload(form).fields.find((f) => f.key === 'chave');
  assert.equal(out.value, 'rbk_novo');
  assert.equal(out.secret, true);
});

test('campo removido some do corpo (ausente = remover)', () => {
  let form = initialCredentialForm(stored);
  form = removeField(form, form.fields.find((f) => f.key === 'url').uid);
  assert.deepEqual(credentialPayload(form).fields.map((f) => f.key), ['chave']);
});

test('campo novo nasce secreto e exige valor', () => {
  let form = addField(initialCredentialForm(null));
  assert.equal(form.fields[0].secret, true);
  form = updateField(form, form.fields[0].uid, { key: 'token' });
  const res = validateCredentialForm({ ...form, name: 'nova' }, { isNew: true });
  assert.equal(res.ok, false);
  assert.equal(res.errors.fieldErrors[form.fields[0].uid], 'Informe o valor.');
  form = updateField(form, form.fields[0].uid, { value: '12345' });
  assert.equal(validateCredentialForm({ ...form, name: 'nova' }, { isNew: true }).ok, true);
});

test('valor de 5 dígitos é aceito (sem tamanho mínimo)', () => {
  let form = addField(initialCredentialForm(null));
  form = updateField(form, form.fields[0].uid, { key: 'pin', value: '48213' });
  assert.equal(validateCredentialForm({ ...form, name: 'pin' }, { isNew: true }).ok, true);
  assert.equal(credentialPayload(form).fields[0].value, '48213');
});

test('segredo que vira público exige reinformar o valor', () => {
  let form = initialCredentialForm(stored);
  const chave = form.fields.find((f) => f.key === 'chave');
  form = updateField(form, chave.uid, { secret: false });
  const res = validateCredentialForm(form);
  assert.equal(res.errors.fieldErrors[chave.uid], 'Informe o valor.');
  form = updateField(form, chave.uid, { value: 'agora-publico' });
  assert.equal(validateCredentialForm(form).ok, true);
});

test('público que vira secreto não exige novo valor', () => {
  let form = initialCredentialForm(stored);
  const url = form.fields.find((f) => f.key === 'url');
  form = updateField(form, url.uid, { secret: true, value: '' });
  assert.equal(validateCredentialForm(form).ok, true);
  const out = credentialPayload(form).fields.find((f) => f.key === 'url');
  assert.equal(out.secret, true);
  assert.equal('value' in out, false);
});

test('nome de credencial e de campo são validados', () => {
  let form = addField(initialCredentialForm(null));
  form = updateField(form, form.fields[0].uid, { key: 'Chave Errada', value: 'x' });
  const res = validateCredentialForm({ ...form, name: '1ruim' }, { isNew: true });
  assert.ok(res.errors.name);
  assert.ok(res.errors.fieldErrors[form.fields[0].uid]);
});

test('o nome da credencial só é validado na criação', () => {
  const form = initialCredentialForm(stored);
  assert.equal(validateCredentialForm({ ...form, name: 'QUALQUER' }, { isNew: false }).ok, true);
});

test('campo repetido, vazio e acima do teto são recusados', () => {
  let form = initialCredentialForm(null);
  form = addField(addField(form));
  form = updateField(form, form.fields[0].uid, { key: 'a', value: 'x' });
  form = updateField(form, form.fields[1].uid, { key: 'a', value: 'y' });
  assert.equal(validateCredentialForm({ ...form, name: 'dup' }, { isNew: true }).errors.fieldErrors[form.fields[1].uid], 'Nome repetido.');

  assert.equal(validateCredentialForm({ name: 'v', description: '', fields: [] }, { isNew: true }).errors.fields, 'Adicione ao menos um campo.');

  form = updateField(form, form.fields[1].uid, { key: 'b', value: 'é'.repeat(MAX_VALUE_BYTES) });
  assert.match(validateCredentialForm({ ...form, name: 'big' }, { isNew: true }).errors.fieldErrors[form.fields[1].uid], /bytes/);
});

test('mais campos que o teto é recusado', () => {
  let form = initialCredentialForm(null);
  for (let i = 0; i <= MAX_FIELDS; i += 1) {
    form = addField(form);
    form = updateField(form, form.fields[i].uid, { key: `c${i}`, value: 'x' });
  }
  assert.equal(validateCredentialForm({ ...form, name: 'muitos' }, { isNew: true }).errors.fields, `No máximo ${MAX_FIELDS} campos.`);
});

test('o corpo nunca carrega o valor de quem não foi tocado', () => {
  const form = initialCredentialForm(stored);
  assert.doesNotMatch(JSON.stringify(credentialPayload(form)), /api\.exemplo\.com/);
});

test('indicador de estado do campo', () => {
  let form = initialCredentialForm(stored);
  const chave = form.fields.find((f) => f.key === 'chave');
  assert.equal(fieldStatus(chave), 'definido');
  form = updateField(form, chave.uid, { value: 'x' });
  assert.equal(fieldStatus(form.fields.find((f) => f.key === 'chave')), 'será alterado');
  assert.equal(fieldStatus(addField(initialCredentialForm(null)).fields[0]), 'novo');
});

// ── o olho ──────────────────────────────────────────────────────────────────

const chaveOf = (form) => form.fields.find((f) => f.key === 'chave');

test('o olho de um segredo salvo busca no servidor; o de um digitado só alterna', () => {
  let form = initialCredentialForm(stored);
  assert.equal(eyeAction(chaveOf(form)), 'fetch');
  form = updateField(form, chaveOf(form).uid, { value: 'digitado' });
  assert.equal(eyeAction(chaveOf(form)), 'toggle');
  // campo novo (ainda não existe no servidor) nunca busca
  assert.equal(eyeAction(addField(initialCredentialForm(null)).fields[0]), 'toggle');
});

test('depois de revelado o olho só alterna (não busca de novo)', () => {
  let form = initialCredentialForm(stored);
  form = applyReveal(form, chaveOf(form).uid, 'rbk_do_servidor');
  assert.equal(eyeAction(chaveOf(form)), 'toggle');
  assert.equal(chaveOf(form).value, 'rbk_do_servidor');
  assert.equal(chaveOf(form).visible, true);
});

test('salvar sem mexer num valor revelado NÃO o reenvia (continua "manter")', () => {
  let form = initialCredentialForm(stored);
  form = applyReveal(form, chaveOf(form).uid, 'rbk_do_servidor');
  const out = credentialPayload(form).fields.find((f) => f.key === 'chave');
  assert.equal('value' in out, false);
  assert.doesNotMatch(JSON.stringify(credentialPayload(form)), /rbk_do_servidor/);
  assert.equal(validateCredentialForm(form).ok, true);
});

test('editar um valor revelado envia o valor novo', () => {
  let form = initialCredentialForm(stored);
  form = applyReveal(form, chaveOf(form).uid, 'rbk_do_servidor');
  form = updateField(form, chaveOf(form).uid, { value: 'rbk_trocada' });
  assert.equal(credentialPayload(form).fields.find((f) => f.key === 'chave').value, 'rbk_trocada');
  assert.equal(fieldStatus(chaveOf(form)), 'será alterado');
});

test('ocultar um valor intocado tira o texto da tela e volta ao "manter"', () => {
  let form = initialCredentialForm(stored);
  form = applyReveal(form, chaveOf(form).uid, 'rbk_do_servidor');
  form = hideField(form, chaveOf(form).uid);
  const f = chaveOf(form);
  assert.equal(f.value, '');
  assert.equal(f.visible, false);
  assert.equal(f.revealed, null);
  assert.equal(eyeAction(f), 'fetch');
  assert.doesNotMatch(JSON.stringify(form.fields), /rbk_do_servidor/);
  assert.equal('value' in credentialPayload(form).fields.find((x) => x.key === 'chave'), false);
});

test('ocultar NÃO apaga o que o operador digitou por cima', () => {
  let form = initialCredentialForm(stored);
  form = applyReveal(form, chaveOf(form).uid, 'rbk_do_servidor');
  form = updateField(form, chaveOf(form).uid, { value: 'rbk_trocada' });
  form = hideField(form, chaveOf(form).uid);
  assert.equal(chaveOf(form).value, 'rbk_trocada');
  assert.equal(chaveOf(form).visible, false);
  assert.equal(credentialPayload(form).fields.find((f) => f.key === 'chave').value, 'rbk_trocada');
});

test('segredo revelado que vira público ainda exige o valor (o servidor recusa sem ele)', () => {
  let form = initialCredentialForm(stored);
  form = applyReveal(form, chaveOf(form).uid, 'rbk_do_servidor');
  form = updateField(form, chaveOf(form).uid, { secret: false });
  // o valor está na tela, então a validação passa e o corpo o INCLUI
  assert.equal(validateCredentialForm(form).ok, true);
  const out = credentialPayload(form).fields.find((f) => f.key === 'chave');
  assert.equal(out.secret, false);
  assert.equal(out.value, 'rbk_do_servidor');
});

test('o olho não toca em campo público já visível', () => {
  const form = initialCredentialForm(stored);
  const url = form.fields.find((f) => f.key === 'url');
  assert.equal(eyeAction(url), 'toggle');
  assert.equal(url.value, 'https://api.exemplo.com');
});

test('ocultar um campo que nunca foi revelado não muda nada', () => {
  const form = initialCredentialForm(stored);
  const out = hideField(form, chaveOf(form).uid);
  assert.deepEqual(out.fields.map((f) => f.value), form.fields.map((f) => f.value));
});
