// Run with: node --test web/static/js/services/spaLink.test.js
//
// Plano 106 · F1 — o predicado que decide se um clique é do app ou do navegador.
// Ele fica num listener GLOBAL no `document` (F2), então um falso positivo aqui
// sequestra link de mensagem, de mídia ou o de saldo da Techify; um falso
// negativo faz o painel recarregar inteiro a cada navegação. Daí a bateria.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isModifiedClick, shouldOpenInNewTab, isInternalHref, spaLinkTarget } from './spaLink.js';

const ORIGIN = 'https://painel.example.com';
const click = (over = {}) => ({
  button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...over,
});
const anchor = (over = {}) => ({ href: `${ORIGIN}/costs`, target: '', download: false, dataset: {}, ...over });

// ---------------------------------------------------------------- isModifiedClick

test('isModifiedClick: clique simples é do app', () => {
  assert.equal(isModifiedClick(click()), false);
});

test('isModifiedClick: Ctrl, ⌘, Shift, Alt e botão não-esquerdo são do navegador', () => {
  assert.equal(isModifiedClick(click({ ctrlKey: true })), true);
  assert.equal(isModifiedClick(click({ metaKey: true })), true);
  assert.equal(isModifiedClick(click({ shiftKey: true })), true);
  assert.equal(isModifiedClick(click({ altKey: true })), true);
  assert.equal(isModifiedClick(click({ button: 1 })), true);
  assert.equal(isModifiedClick(click({ button: 2 })), true);
});

test('isModifiedClick: evento sem `button` (teclado/sintético) conta como esquerdo', () => {
  assert.equal(isModifiedClick({ ctrlKey: false }), false);
  assert.equal(isModifiedClick({ ctrlKey: true }), true);
});

test('isModifiedClick: evento ausente não quebra', () => {
  assert.equal(isModifiedClick(null), false);
  assert.equal(isModifiedClick(undefined), false);
});

// ------------------------------------------------------------- shouldOpenInNewTab

test('shouldOpenInNewTab: Ctrl e ⌘ + clique esquerdo pedem guia', () => {
  assert.equal(shouldOpenInNewTab(click({ ctrlKey: true })), true);
  assert.equal(shouldOpenInNewTab(click({ metaKey: true })), true);
});

test('shouldOpenInNewTab: clique do meio pede guia (click e auxclick)', () => {
  assert.equal(shouldOpenInNewTab(click({ button: 1 })), true);
});

test('shouldOpenInNewTab: clique simples e botão direito NÃO', () => {
  assert.equal(shouldOpenInNewTab(click()), false);
  assert.equal(shouldOpenInNewTab(click({ button: 2 })), false);
  // botão direito COM Ctrl continua sendo menu de contexto, não guia
  assert.equal(shouldOpenInNewTab(click({ button: 2, ctrlKey: true })), false);
});

test('shouldOpenInNewTab: Shift (nova janela) e Alt (baixar) ficam de fora — é mais largo em isModifiedClick', () => {
  assert.equal(shouldOpenInNewTab(click({ shiftKey: true })), false);
  assert.equal(shouldOpenInNewTab(click({ altKey: true })), false);
  // …mas o interceptor da F2 sai cedo neles pelo predicado largo:
  assert.equal(isModifiedClick(click({ shiftKey: true })), true);
});

// -------------------------------------------------------------- isInternalHref

test('isInternalHref: path do próprio app', () => {
  assert.equal(isInternalHref('/costs', ORIGIN), true);
  assert.equal(isInternalHref('/conversations/42?message=7', ORIGIN), true);
  assert.equal(isInternalHref(`${ORIGIN}/protocolos?detail=9`, ORIGIN), true);
});

test('isInternalHref: outro host é externo', () => {
  assert.equal(isInternalHref('https://llm.techify.one/credits', ORIGIN), false);
  assert.equal(isInternalHref('http://painel.example.com.evil.test/costs', ORIGIN), false);
});

test('isInternalHref: esquema não-navegacional nunca é interno', () => {
  assert.equal(isInternalHref('mailto:alguem@example.com', ORIGIN), false);
  assert.equal(isInternalHref('tel:+5511999990000', ORIGIN), false);
  assert.equal(isInternalHref('javascript:void(0)', ORIGIN), false);
  assert.equal(isInternalHref('data:text/html,<b>x</b>', ORIGIN), false);
  assert.equal(isInternalHref('blob:https://painel.example.com/abc', ORIGIN), false);
});

test('isInternalHref: âncora de mesma página não é navegação', () => {
  assert.equal(isInternalHref('#', ORIGIN), false);
  assert.equal(isInternalHref('#topo', ORIGIN), false);
});

test('isInternalHref: href ausente, vazio ou não-string', () => {
  assert.equal(isInternalHref(null, ORIGIN), false);
  assert.equal(isInternalHref(undefined, ORIGIN), false);
  assert.equal(isInternalHref('', ORIGIN), false);
  assert.equal(isInternalHref('   ', ORIGIN), false);
  assert.equal(isInternalHref(42, ORIGIN), false);
});

// -------------------------------------------------------------- spaLinkTarget

test('spaLinkTarget: link interno devolve o path completo (com query e hash)', () => {
  assert.deepEqual(spaLinkTarget(anchor(), ORIGIN), { path: '/costs' });
  assert.deepEqual(
    spaLinkTarget(anchor({ href: `${ORIGIN}/conversations/42?message=7#m7` }), ORIGIN),
    { path: '/conversations/42?message=7#m7' },
  );
  // href relativo (atributo cru, antes de o DOM resolver)
  assert.deepEqual(spaLinkTarget(anchor({ href: '/protocolos?detail=9' }), ORIGIN), { path: '/protocolos?detail=9' });
});

test('spaLinkTarget: target="_blank" é do navegador; "_self" e vazio são do app', () => {
  assert.equal(spaLinkTarget(anchor({ target: '_blank' }), ORIGIN), null);
  assert.equal(spaLinkTarget(anchor({ target: 'outroFrame' }), ORIGIN), null);
  assert.deepEqual(spaLinkTarget(anchor({ target: '_self' }), ORIGIN), { path: '/costs' });
  assert.deepEqual(spaLinkTarget(anchor({ target: '' }), ORIGIN), { path: '/costs' });
});

test('spaLinkTarget: download nunca é navegação de SPA', () => {
  assert.equal(spaLinkTarget(anchor({ download: true }), ORIGIN), null);
  // o contrato é BOOLEANO (`a.hasAttribute('download')`), não `a.download`,
  // que devolve '' tanto para ausente quanto para `<a download>`
  assert.deepEqual(spaLinkTarget(anchor({ download: false }), ORIGIN), { path: '/costs' });
});

test('spaLinkTarget: data-no-spa desliga o interceptor', () => {
  assert.equal(spaLinkTarget(anchor({ dataset: { noSpa: '' } }), ORIGIN), null);
  assert.equal(spaLinkTarget(anchor({ dataset: { noSpa: 'true' } }), ORIGIN), null);
});

test('spaLinkTarget: link externo, mailto e âncora sem href', () => {
  assert.equal(spaLinkTarget(anchor({ href: 'https://exemplo.test/x' }), ORIGIN), null);
  assert.equal(spaLinkTarget(anchor({ href: 'mailto:a@b.test' }), ORIGIN), null);
  assert.equal(spaLinkTarget(anchor({ href: undefined }), ORIGIN), null);
  assert.equal(spaLinkTarget(null, ORIGIN), null);
});

test('spaLinkTarget: os casos reais do painel que NÃO podem ser capturados', () => {
  // link dentro de mensagem (messageEntities.js) — externo + _blank
  assert.equal(spaLinkTarget({ href: 'https://exemplo.test/promo', target: '_blank', dataset: {} }, ORIGIN), null);
  // "Saldo e Recarregar" (GearMenu) — outro host
  assert.equal(spaLinkTarget({ href: 'https://llm.techify.one/account', target: '_blank', dataset: {} }, ORIGIN), null);
  // mídia baixável servida pelo próprio host — mesmo origin, mas é download
  assert.equal(spaLinkTarget({ href: `${ORIGIN}/statics/outbox/nota.pdf`, download: true, dataset: {} }, ORIGIN), null);
  // …e a mídia aberta em nova guia (sem download) segue fora pelo target
  assert.equal(spaLinkTarget({ href: `${ORIGIN}/statics/outbox/foto.jpg`, target: '_blank', dataset: {} }, ORIGIN), null);
});
