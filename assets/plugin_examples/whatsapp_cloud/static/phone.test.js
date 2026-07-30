// Trava a cópia de `formatPhoneDisplay` que vive neste plugin (plano 92 · C1)
// contra o canônico do core (`web/static/js/utils/phone.js`). Rodar com:
//   node --test assets/plugin_examples/whatsapp_cloud/static/phone.test.js
//
// Os dois primeiros casos são os âncoras: o de 12 dígitos é exatamente onde a
// família A divergia (traço um dígito fora), e é o erro que uma cópia
// desatualizada reintroduziria em silêncio.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatPhoneDisplay } from './phone.js';

test('celular BR (13 dígitos) → +55 (AA) XXXXX-XXXX', () => {
  assert.equal(formatPhoneDisplay('5511999998888'), '+55 (11) 99999-8888');
});

test('fixo/celular legado BR (12 dígitos) → +55 (AA) XXXX-XXXX', () => {
  assert.equal(formatPhoneDisplay('558597360559'), '+55 (85) 9736-0559');
});

test('internacional ≥12 dígitos não-BR → país + área + 5-e-resto', () => {
  assert.equal(formatPhoneDisplay('351912345678'), '+35 (19) 12345-678');
});

test('curto/desconhecido → só prefixa +', () => {
  assert.equal(formatPhoneDisplay('5511999'), '+5511999');
});

test('vazio/nulo → string vazia', () => {
  assert.equal(formatPhoneDisplay(''), '');
  assert.equal(formatPhoneDisplay(null), '');
  assert.equal(formatPhoneDisplay(undefined), '');
});

test('aceita número (não só string)', () => {
  assert.equal(formatPhoneDisplay(5511999998888), '+55 (11) 99999-8888');
});
