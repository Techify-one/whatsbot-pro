// Run with: node --test web/static/js/services/providerCatalog.test.js
//
// Plano 76 · F1 — o catálogo único de providers. Sem servidor, o fetch de
// `listChannelProviders()` falha (é engolido em ensureLoaded) e os getters caem
// no FALLBACK estático mínimo. Estes testes travam o contrato do fallback +
// degradê: um provider desconhecido nunca some, cai no próprio identificador em
// cinza (D3). A cor sai do mesmo tintForColor da tela Canais.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  providerLabel, providerColor, providerTint, contactTypeFor, descriptorFor,
} from './providerCatalog.js';
import { tintForColor } from '../components/channels/constants.js';

test('fallback: gowa/test resolvem rótulo e tipo mesmo sem fetch', () => {
  assert.equal(providerLabel('gowa'), 'GOWA');
  assert.equal(contactTypeFor('gowa'), 'whatsapp');
  assert.equal(providerLabel('test'), 'Teste');
  assert.equal(contactTypeFor('test'), 'outros');
});

test('degradê: provider desconhecido → próprio id + cinza + tipo outros', () => {
  assert.equal(providerLabel('facebook_messenger'), 'facebook_messenger');
  assert.equal(providerColor('facebook_messenger'), 'gray');
  assert.equal(providerTint('facebook_messenger'), tintForColor('gray'));
  assert.equal(contactTypeFor('facebook_messenger'), 'outros');
  assert.equal(descriptorFor('facebook_messenger'), null);
});

test('tint resolve pelo mesmo tintForColor da tela Canais', () => {
  assert.equal(providerTint('gowa'), tintForColor('green'));
});
