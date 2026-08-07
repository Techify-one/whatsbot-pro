// Run with: node --test web/static/js/services/threadData.test.js
//
// Plano 85 · C1 — a regra "como uma resposta do servidor vira a thread aberta",
// antes copiada em três call sites do hook de seleção, agora existe uma vez e é
// testável sem preact/DOM/rede.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { hydrateFailed, applyThreadResponse, prependOlder,
         appendNewer, isAnchored, pageCursorId,
         countNewWhileAnchored } from './threadData.js';

const msg = (over = {}) => ({ _id: 1, role: 'user', content: 'oi', ts: 1000, ...over });

test('hydrateFailed marca só as failed, e preserva as demais por referência', () => {
  const ok = msg({ _id: 1 });
  const bad = msg({ _id: 2, role: 'assistant', status: 'failed', ts: 1234 });
  const out = hydrateFailed([ok, bad]);
  assert.equal(out[0], ok, 'mensagem íntegra foi copiada à toa');
  assert.equal(out[1]._localId, 'loaded_1234');
  assert.equal(out[1]._status, 'failed');
  assert.equal(bad._localId, undefined, 'mutou a entrada');
});

test('applyThreadResponse carimba a thread de origem (A4) sem mutar a resposta', () => {
  const data = { messages: [msg()], info: { name: 'Renê' } };
  const out = applyThreadResponse(data, [], 'conv:15013');
  assert.equal(out._threadKey, 'conv:15013');
  assert.equal(out.info.name, 'Renê');
  assert.equal(data._threadKey, undefined, 'mutou a resposta do servidor');
});

test('applyThreadResponse mescla o buffer de WS sem duplicar (dedup R12)', () => {
  const jaNaResposta = msg({ _id: 7, content: 'chegou durante o fetch', ts: 2000 });
  // Mesma mensagem que já veio na resposta + uma genuinamente nova.
  const buffer = [
    { role: 'user', content: 'chegou durante o fetch', ts: 2000 },
    { role: 'user', content: 'essa é nova', ts: 2100 },
  ];
  const out = applyThreadResponse({ messages: [jaNaResposta] }, buffer, 'conv:1');
  assert.equal(out.messages.length, 2, 'a mensagem repetida virou bolha duplicada');
  assert.equal(out.messages[1].content, 'essa é nova');
});

test('applyThreadResponse hidrata as failed vindas do servidor', () => {
  const out = applyThreadResponse(
    { messages: [msg({ _id: 3, role: 'assistant', status: 'failed', ts: 99 })] }, [], 'phone:551199');
  assert.equal(out.messages[0]._localId, 'loaded_99');
});

test('applyThreadResponse tolera resposta sem messages', () => {
  const out = applyThreadResponse({ info: {} }, [], 'conv:9');
  assert.deepEqual(out.messages, []);
  assert.equal(out._threadKey, 'conv:9');
});

test('prependOlder põe a página anterior ANTES e descarta o que já está carregado', () => {
  const prev = { messages: [msg({ _id: 10, ts: 5000 })], has_more: true, _threadKey: 'conv:1' };
  const older = [msg({ _id: 8, ts: 3000 }), msg({ _id: 9, ts: 4000 }), msg({ _id: 10, ts: 5000 })];
  const out = prependOlder(prev, older, false);
  assert.deepEqual(out.messages.map(m => m._id), [8, 9, 10]);
  assert.equal(out.has_more, false);
  assert.equal(out._threadKey, 'conv:1', 'perdeu o carimbo da thread ao paginar');
  assert.equal(prev.messages.length, 1, 'mutou a thread anterior');
});

test('prependOlder mantém mensagem sem _id (otimista) e hidrata failed', () => {
  const prev = { messages: [msg({ _id: 10 })] };
  const out = prependOlder(prev,
    [{ role: 'assistant', content: 'x', ts: 42, status: 'failed' }], true);
  assert.equal(out.messages.length, 2);
  assert.equal(out.messages[0]._localId, 'loaded_42');
  assert.equal(out.has_more, true);
});

test('prependOlder sem thread carregada devolve prev intacto', () => {
  assert.equal(prependOlder(null, [msg()], true), null);
});

// ── Plano 99 · F0d — a janela deixou de terminar sempre na última mensagem ──

test('appendNewer põe a página seguinte DEPOIS e descarta o já carregado', () => {
  const prev = { messages: [msg({ _id: 10, ts: 5000 })], has_more: true,
                 has_more_older: true, _threadKey: 'conv:1' };
  const newer = [msg({ _id: 10, ts: 5000 }), msg({ _id: 11, ts: 6000 }),
                 msg({ _id: 12, ts: 7000 })];
  const out = appendNewer(prev, newer, true);
  assert.deepEqual(out.messages.map(m => m._id), [10, 11, 12]);
  assert.equal(out.has_more_newer, true);
  assert.equal(out._threadKey, 'conv:1', 'perdeu o carimbo da thread ao paginar');
  assert.equal(prev.messages.length, 1, 'mutou a thread anterior');
});

test('appendNewer NÃO mexe no lado antigo da janela', () => {
  // O servidor devolve has_more_older=true por construção numa leitura after_id
  // (ele não mede esse lado). Adotar esse valor apagaria o que o cliente já sabe.
  const prev = { messages: [msg({ _id: 10 })], has_more: false, has_more_older: false };
  const out = appendNewer(prev, [msg({ _id: 11 })], false);
  assert.equal(out.has_more, false, 'o começo do histórico foi reaberto sem motivo');
  assert.equal(out.has_more_older, false);
  assert.equal(out.has_more_newer, false);
});

test('appendNewer sem thread carregada devolve prev intacto', () => {
  assert.equal(appendNewer(null, [msg()], true), null);
});

test('appendNewer não perde mensagem que chegou durante a leitura do fim', () => {
  const startedWith = { messages: [msg({ _id: 10 })], has_more_newer: true,
                        _newWhileAnchored: 2 };
  // Uma terceira mensagem chegou por WS depois que o GET começou.
  const current = { ...startedWith, _newWhileAnchored: 3 };
  const out = appendNewer(current, [msg({ _id: 11 })], false,
                          startedWith._newWhileAnchored);
  assert.equal(out._newWhileAnchored, 1);
  assert.equal(out.has_more_newer, true,
    'a janela foi fechada apesar de haver mensagem posterior à resposta');

  const final = appendNewer(out, [], false, out._newWhileAnchored);
  assert.equal(final._newWhileAnchored, undefined);
  assert.equal(final.has_more_newer, false);
});

test('pageCursorId usa a borda cronológica, não o menor/maior id', () => {
  const rows = [msg({ _id: 90, ts: 10 }), msg({ _id: 7, ts: 20 }),
                { role: 'assistant', content: 'otimista', ts: 25 },
                msg({ _id: 30, ts: 30 })];
  assert.equal(pageCursorId(rows, 'older'), 90);
  assert.equal(pageCursorId(rows, 'newer'), 30);
});

test('prependOlder mantém has_more e has_more_older em sincronia', () => {
  const out = prependOlder({ messages: [] }, [msg({ _id: 1 })], true);
  assert.equal(out.has_more, true);
  assert.equal(out.has_more_older, true, 'os dois nomes do mesmo fato divergiram');
});

test('isAnchored: só há janela ancorada quando existe mensagem mais NOVA fora dela', () => {
  assert.equal(isAnchored({ has_more_newer: true }), true);
  assert.equal(isAnchored({ has_more_newer: false, has_more: true }), false,
    'ter mensagens mais ANTIGAS não é estar ancorado');
  assert.equal(isAnchored(null), false);
});

test('evento durante o primeiro GET ancorado conta sem misturar a mensagem', () => {
  const messages = [msg({ _id: 50 })];
  const prev = { messages, has_more_newer: false };
  const once = countNewWhileAnchored(prev);
  const twice = countNewWhileAnchored(once);
  assert.equal(once.messages, messages);
  assert.equal(once.has_more_newer, true);
  assert.equal(twice._newWhileAnchored, 2);
  assert.equal(countNewWhileAnchored(null), null);
});
