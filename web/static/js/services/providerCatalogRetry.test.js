// Run with: node --test web/static/js/services/providerCatalogRetry.test.js
//
// Plano 85 · B1 — o catálogo NÃO pode disparar uma requisição por re-render.
// `providerLabel`/`providerTint` são lidos no corpo de render do ChannelChip, que
// aparece em TODA linha da sidebar; antes da B1 uma falha persistente deixava
// `_descriptors` nulo e `_loading` de volta em `false`, então a leitura seguinte
// rearmava o fetch — a re-tentativa nunca era exercida por teste (o arquivo
// providerCatalog.test.js roda justamente com o fetch falhando e só valida o
// fallback).
//
// Arquivo SEPARADO de propósito: o estado do catálogo é de módulo, e o node roda
// cada arquivo de teste no seu processo — aqui ele nasce limpo, com os stubs de
// `localStorage`/`fetch` instalados ANTES do import.
import { test } from 'node:test';
import assert from 'node:assert/strict';

let calls = 0;
globalThis.localStorage = /** @type {any} */ ({ getItem: () => '', setItem: () => {}, removeItem: () => {} });
globalThis.fetch = async () => { calls++; throw new Error('network down'); };

const { providerLabel, providerColor, refresh, MAX_ATTEMPTS } =
  await import('./providerCatalog.js');

test('N leituras (N re-renders) com o fetch falhando ⇒ UMA requisição', () => {
  for (let i = 0; i < 50; i++) providerLabel('gowa');
  for (let i = 0; i < 50; i++) providerColor('telegram');
  assert.equal(calls, 1, 'cada leitura estava disparando uma requisição nova');
});

test('o fallback continua respondendo enquanto o catálogo não carrega', () => {
  assert.equal(providerLabel('gowa'), 'GOWA');
  assert.equal(providerLabel('telegram'), 'telegram');   // desconhecido → próprio id
  assert.equal(providerColor('telegram'), 'gray');
});

test('refresh() é a re-tentativa DELIBERADA — e é a única que dispara rede', () => {
  const before = calls;
  refresh();
  assert.equal(calls, before + 1);
  for (let i = 0; i < 20; i++) providerLabel('gowa');
  assert.equal(calls, before + 1, 'ler depois do refresh voltou a disparar por render');
});

test('o teto de tentativas é finito', () => {
  assert.ok(Number.isInteger(MAX_ATTEMPTS) && MAX_ATTEMPTS > 0 && MAX_ATTEMPTS < 10);
});
