// node --test para o núcleo puro do chat agêntico (plano 51 · 04 F4).
// Rodar: node --test assets/plugin_examples/melhorias/static/chat_core.test.js

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reduceAiEvent, isAuthError, persistedToItems, authErrorInHistory } from './chat_core.js';

test('message start/chunk/end monta uma bolha assistant em streaming', () => {
  let s = reduceAiEvent([], { event: 'message_start', data: { messageId: 'm1' } });
  s = reduceAiEvent(s.items, { event: 'message_chunk', data: { messageId: 'm1', delta: 'olá ' } }, s.status);
  s = reduceAiEvent(s.items, { event: 'message_chunk', data: { messageId: 'm1', delta: 'mundo' } }, s.status);
  assert.equal(s.status, 'streaming');
  assert.equal(s.items.length, 1);
  assert.equal(s.items[0].content, 'olá mundo');
  assert.equal(s.items[0].streaming, true);
  s = reduceAiEvent(s.items, { event: 'message_end', data: { messageId: 'm1' } }, s.status);
  assert.equal(s.items[0].streaming, false);
});

test('chunk sem start cria a bolha (reconexão não perde tokens)', () => {
  const s = reduceAiEvent([], { event: 'message_chunk', data: { messageId: 'mX', delta: 'oi' } });
  assert.equal(s.items[0].content, 'oi');
});

test('tool_call start/end e dedupe por toolCallId', () => {
  let s = reduceAiEvent([], { event: 'tool_call_start', data: { toolCallId: 't1', name: 'get_agent', input: { key: 'default' } } });
  s = reduceAiEvent(s.items, { event: 'tool_call_start', data: { toolCallId: 't1', name: 'get_agent' } }, s.status);
  assert.equal(s.items.length, 1); // dedupe
  s = reduceAiEvent(s.items, { event: 'tool_call_end', data: { toolCallId: 't1', output: '{"ok":true}' } }, s.status);
  assert.equal(s.items[0].status, 'done');
  s = reduceAiEvent(s.items, { event: 'tool_call_end', data: { toolCallId: 't1', error: 'boom' } }, s.status);
  assert.equal(s.items[0].status, 'error');
});

test('approval_needed trava em awaiting-approval; done volta a idle', () => {
  let s = reduceAiEvent([], { event: 'approval_needed', data: { approvalId: 'a1', toolName: 'patch_agent_prompt', summary: 'Editar prompt' } });
  assert.equal(s.status, 'awaiting-approval');
  assert.equal(s.items[0].decided, null);
  // approval_registered (write-through snake_case) com MESMO id não duplica.
  s = reduceAiEvent(s.items, { event: 'approval_registered', data: { id: 'a1', tool_name: 'patch_agent_prompt' } }, s.status);
  assert.equal(s.items.filter((c) => c.kind === 'approval').length, 1);
  s = reduceAiEvent(s.items, { event: 'done', data: {} }, s.status);
  assert.equal(s.status, 'idle');
});

test('error vira card e status error', () => {
  const s = reduceAiEvent([], { event: 'error', data: { message: 'quebrou' } });
  assert.equal(s.status, 'error');
  assert.equal(s.items[0].kind, 'error');
  assert.equal(s.items[0].message, 'quebrou');
});

test('isAuthError detecta as variantes de sessão expirada', () => {
  assert.equal(isAuthError('HTTP 401 Unauthorized'), true);
  assert.equal(isAuthError('authentication_error: invalid'), true);
  assert.equal(isAuthError('Please run /login to continue'), true);
  assert.equal(isAuthError('Invalid API key · fix your key'), true);
  assert.equal(isAuthError('resposta normal do modelo'), false);
});

test('persistedToItems hidrata mensagens + approvals do DB', () => {
  const items = persistedToItems(
    [{ id: 1, role: 'user', content: 'analisa isso' },
     { id: 2, role: 'assistant', content: 'analisando…' },
     { id: 3, role: 'tool', tool_name: 'get_agent', tool_input: { key: 'x' }, tool_result: '{}' },
     { id: 4, role: 'assistant', content: '' }],
    [{ id: 'a1', tool_name: 'set_variable', tool_input: {}, summary: 's', approved: null },
     { id: 'a2', tool_name: 'set_variable', tool_input: {}, summary: 's', approved: 1 }]);
  assert.equal(items.filter((c) => c.kind === 'text').length, 2);
  assert.equal(items.filter((c) => c.kind === 'tool').length, 1);
  const approvals = items.filter((c) => c.kind === 'approval');
  assert.equal(approvals[0].decided, null);
  assert.equal(approvals[1].decided, true);
});

// ── Plano 60 · sessão expirada (camada 1) ────────────────────────────────────

test('system vira cartão de erro (o 401 é gravado assim pelo gateway)', () => {
  const items = persistedToItems([
    { id: 1, role: 'user', content: 'analisa' },
    { id: 2, role: 'system', content: 'API Error: 401 authentication_error' },
    { id: 3, role: 'system', content: '   ' },   // vazio não vira cartão
  ]);
  const errors = items.filter((c) => c.kind === 'error');
  assert.equal(errors.length, 1);
  assert.equal(errors[0].message, 'API Error: 401 authentication_error');
  assert.equal(errors[0].id, 'db-2');
  // A mensagem do humano continua sendo bolha, não cartão.
  assert.equal(items.filter((c) => c.kind === 'text').length, 1);
});

test('authErrorInHistory olha a ÚLTIMA mensagem com conteúdo', () => {
  assert.equal(authErrorInHistory([
    { id: 1, role: 'user', content: 'analisa' },
    { id: 2, role: 'system', content: 'API Error: 401 · Please run /login' },
  ]), true);
  // Linhas sem conteúdo (tool call) não decidem — a busca continua para trás.
  assert.equal(authErrorInHistory([
    { id: 1, role: 'system', content: 'API Error: 401' },
    { id: 2, role: 'tool', tool_name: 'get_agent', content: '' },
  ]), true);
  // Erro ANTIGO já superado por uma resposta boa ⇒ não oferece renovar.
  assert.equal(authErrorInHistory([
    { id: 1, role: 'system', content: 'API Error: 401' },
    { id: 2, role: 'assistant', content: 'analisando de novo…' },
  ]), false);
  assert.equal(authErrorInHistory([]), false);
});
