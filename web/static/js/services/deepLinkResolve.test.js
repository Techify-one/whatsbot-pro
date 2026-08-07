// Run with: node --test web/static/js/services/deepLinkResolve.test.js
//
// Plano 89 · F1/F2 — a decisão do deep-link, antes enterrada num efeito do
// `useConversationSelection` sem nenhuma cobertura. É o que impede a regressão
// do guard `contacts.length === 0` (que quebrava todo link de conversa aberto
// com a sidebar vazia) de voltar.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveDeepLink } from './deepLinkResolve.js';

const row = (over = {}) => ({
  phone: '5511999990000', conversation_id: 10, channel_id: 'ch_a',
  contact_id: 7, id: 7, ...over,
});

test('loading ⇒ wait, em qualquer tamanho de lista', () => {
  assert.equal(resolveDeepLink({
    initialConversationId: 10, contacts: [], loading: true,
  }).action, 'wait');
  assert.equal(resolveDeepLink({
    initialConversationId: 10, contacts: [row()], loading: true,
  }).action, 'wait');
});

test('lista com linhas, nenhuma casando ⇒ open_by_id', () => {
  const out = resolveDeepLink({
    initialConversationId: 1851,
    contacts: [row({ conversation_id: 1 }), row({ conversation_id: 2 }),
               row({ conversation_id: 3 }), row({ conversation_id: 4 }),
               row({ conversation_id: 5 })],
    loading: false,
  });
  assert.equal(out.action, 'open_by_id');
  assert.equal(out.conversationId, 1851);
});

test('lista com a linha ⇒ select com telefone e canal DELA', () => {
  const alvo = row({ conversation_id: 1851, phone: '5511988887777', channel_id: 'ch_z' });
  const out = resolveDeepLink({
    initialConversationId: 1851,
    contacts: [row({ conversation_id: 3 }), alvo],
    loading: false,
  });
  assert.equal(out.action, 'select');
  assert.equal(out.via, 'conversation');
  assert.equal(out.row, alvo);
  assert.equal(out.row.phone, '5511988887777');
  assert.equal(out.row.channel_id, 'ch_z');
});

test('já resolvido ⇒ noop (não reabre em loop quando a lista muda depois)', () => {
  const out = resolveDeepLink({
    initialConversationId: 1851,
    contacts: [row({ conversation_id: 1851 })],
    loading: false,
    lastResolvedConvId: 1851,
  });
  assert.equal(out.action, 'noop');
});

test('ramo CONTATO com lista vazia ⇒ wait, nunca open_by_id (assimetria deliberada)', () => {
  const out = resolveDeepLink({ initialContactId: 7, contacts: [], loading: false });
  assert.equal(out.action, 'wait');
});

test('ambos os ids nulos, com algo resolvido antes ⇒ deselect', () => {
  assert.equal(resolveDeepLink({ lastResolvedConvId: 1851 }).action, 'deselect');
  assert.equal(resolveDeepLink({ lastResolvedId: 7 }).action, 'deselect');
  assert.equal(resolveDeepLink({}).action, 'noop', 'nada resolvido antes ⇒ nada a desmarcar');
});

test('lista VAZIA do servidor (não em voo) ⇒ open_by_id — o bug do plano 89', () => {
  // O caso que quebrava: aba "Minhas" sem atribuições, chip "Resolvidas" numa view
  // sem resolvidas, ou falha de rede na lista. Antes devolvia `wait` para sempre —
  // nenhuma requisição, URL intacta, painel no placeholder, ZERO feedback.
  const out = resolveDeepLink({
    initialConversationId: 1851, contacts: [], loading: false,
  });
  assert.equal(out.action, 'open_by_id');
  assert.equal(out.conversationId, 1851);
});
