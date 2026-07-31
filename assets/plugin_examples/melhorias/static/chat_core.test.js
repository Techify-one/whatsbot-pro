// node --test para o núcleo puro do chat agêntico (plano 51 · 04 F4).
// Rodar: node --test assets/plugin_examples/melhorias/static/chat_core.test.js

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reduceAiEvent, isAuthError, persistedToItems, authErrorInHistory,
         lastFailureFromHistory, footerStateFor } from './chat_core.js';

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

// ── Plano 61 · falhas TIPADAS do executor ────────────────────────────────────

test('lastFailureFromHistory lê o kind GRAVADO, sem adivinhar pelo texto', () => {
  assert.deepEqual(lastFailureFromHistory([
    { id: 1, role: 'user', content: 'analisa' },
    { id: 2, role: 'system', content: 'sua sessão acabou', failure_kind: 'auth_required' },
  ]), { kind: 'auth_required', message: 'sua sessão acabou' });
  assert.equal(lastFailureFromHistory([
    { id: 1, role: 'system', content: 'sem crédito', failure_kind: 'quota_exceeded' },
  ]).kind, 'quota_exceeded');
  assert.equal(lastFailureFromHistory([]), null);
});

test('linha transitória que CITA 401 não é sessão expirada (o falso positivo)', () => {
  // A regressão que este desenho introduziria se a hidratação seguisse olhando
  // o texto: o executor manda um limite de uso cujo corpo cita um 401 de
  // upstream, e o painel oferecia "Renovar sessão" para algo que renovar não
  // conserta — queimando de quebra o guard `authNotifiedRef` da conversa.
  const f = lastFailureFromHistory([
    { id: 1, role: 'user', content: 'analisa' },
    { id: 2, role: 'system', failure_kind: 'rate_limited',
      content: 'Rate limit: upstream respondeu 401 authentication_error' },
  ]);
  assert.equal(f.kind, 'rate_limited');
  assert.equal(authErrorInHistory([
    { id: 2, role: 'system', failure_kind: 'rate_limited',
      content: 'Rate limit: upstream respondeu 401' },
  ]), false);
  assert.equal(footerStateFor({ convStatus: 'ACTIVE', historyFailure: f }).canRenew, false);
});

test('linha LEGADA (sem failure_kind) cai na heurística de texto', () => {
  // Linhas gravadas em produção antes do kind tipado existir.
  assert.equal(lastFailureFromHistory([
    { id: 1, role: 'system', content: 'API Error: 401 · Please run /login' },
  ]).kind, 'auth_required');
  assert.equal(lastFailureFromHistory([
    { id: 1, role: 'system', content: 'aviso qualquer sem erro' },
  ]), null);
});

test('auth seguido de transitório: quem decide é o STATUS da conversa', () => {
  // O histórico sozinho diz "rate_limited" (é a última linha), mas a conversa
  // continua AUTH_EXPIRED no banco. Este é o caso que o `convStatus` cobre —
  // parece redundante com a checagem de histórico e NÃO é.
  const messages = [
    { id: 1, role: 'system', content: '401', failure_kind: 'auth_required' },
    { id: 2, role: 'system', content: 'limite', failure_kind: 'rate_limited' },
  ];
  assert.equal(lastFailureFromHistory(messages).kind, 'rate_limited');
  const f = footerStateFor({ convStatus: 'AUTH_EXPIRED',
                             historyFailure: lastFailureFromHistory(messages) });
  assert.equal(f.mode, 'auth_expired');
  assert.equal(f.canRenew, true);
});

test('footerStateFor: só auth_required ganha botão de renovar', () => {
  const live = (kind, extra = {}) => footerStateFor({
    convStatus: 'ACTIVE', liveFailure: { kind, ...extra } });
  assert.deepEqual(live('auth_required'),
    { mode: 'auth_expired', kind: 'auth_required', canRenew: true });
  assert.deepEqual(live('rate_limited', { retryAfter: 30 }),
    { mode: 'wait', kind: 'rate_limited', canRenew: false, retryAfter: 30 });
  assert.equal(live('overloaded').mode, 'wait');
  assert.equal(live('quota_exceeded').mode, 'quota');
  assert.equal(live('quota_exceeded').canRenew, false);
  assert.equal(live('unknown').mode, 'generic');
  assert.equal(live('coisa_nova_do_executor').mode, 'generic');  // kind novo degrada
  // Sem falha nenhuma: conversa viva × encerrada.
  assert.equal(footerStateFor({ convStatus: 'ACTIVE' }).mode, 'live');
  assert.equal(footerStateFor({ convStatus: 'COMPLETED' }).mode, 'ended');
});

test('só falha DURÁVEL sobrevive ao F5; transitória some', () => {
  const after = (kind, convStatus = 'AUTH_EXPIRED') =>
    footerStateFor({ convStatus, historyFailure: { kind } }).mode;
  // Sem crédito não passa sozinho — e não mexe no status, então dura em ACTIVE.
  assert.equal(after('quota_exceeded', 'ACTIVE'), 'quota');
  assert.equal(after('auth_required'), 'auth_expired');
  assert.equal(after('rate_limited', 'ACTIVE'), 'live');  // 429 já passou
  assert.equal(after('overloaded', 'ACTIVE'), 'live');
});

test('sessão RENOVADA solta o rodapé: auth do histórico não vale em ACTIVE', () => {
  // O bug que isto fecha: renovar a sessão devolvia a conversa a ACTIVE, mas a
  // linha de falha continuava sendo a última do histórico e o rodapé ficava
  // preso em "Renovar sessão". Como ele ocupa o lugar do COMPOSITOR, o operador
  // não conseguia reenviar a mensagem que o aviso "sessão renovada — pode
  // reenviar" acabara de pedir. Só saía encerrando a melhoria.
  const historyFailure = { kind: 'auth_required', message: 'API Error: 401' };
  // Antes do relogin: a conversa está AUTH_EXPIRED ⇒ oferece renovar.
  assert.equal(footerStateFor({ convStatus: 'AUTH_EXPIRED', historyFailure }).mode,
               'auth_expired');
  // Depois do relogin (o `resume` devolveu a conversa a ACTIVE): compositor.
  const f = footerStateFor({ convStatus: 'ACTIVE', historyFailure });
  assert.equal(f.mode, 'live');
  assert.equal(f.canRenew, false);
  // Uma falha AO VIVO depois da renovação ainda manda — a porta é só do histórico.
  assert.equal(footerStateFor({ convStatus: 'ACTIVE', historyFailure,
                                liveFailure: { kind: 'auth_required' } }).mode,
               'auth_expired');
  // E `quota_exceeded` não é afetado: segue durável mesmo em ACTIVE.
  assert.equal(footerStateFor({ convStatus: 'ACTIVE',
                                historyFailure: { kind: 'quota_exceeded' } }).mode,
               'quota');
});

test('executor_failure vira UM cartão e tira o chat de "streaming"', () => {
  // Sem sair de `streaming`, o spinner "IA pensando…" giraria para sempre: o
  // fallback de quiescência do chat.js só arma com uma resposta assentada.
  const s = reduceAiEvent([], { event: 'executor_failure',
    data: { kind: 'rate_limited', message: 'limite', retry_after: 30 } }, 'streaming');
  assert.equal(s.items.length, 1);
  assert.equal(s.items[0].kind, 'error');
  assert.equal(s.items[0].failureKind, 'rate_limited');
  assert.equal(s.items[0].retryAfter, 30);
  assert.equal(s.status, 'idle');
  // Sessão expirada é a exceção: o chat de fato parou.
  assert.equal(reduceAiEvent([], { event: 'executor_failure',
    data: { kind: 'auth_required' } }, 'streaming').status, 'error');
});

test('evento desconhecido é inerte (painel antigo em cache não quebra)', () => {
  const before = [{ kind: 'text', id: 'm1', role: 'assistant', content: 'oi' }];
  const s = reduceAiEvent(before, { event: 'evento_do_futuro', data: {} }, 'idle');
  assert.deepEqual(s.items, before);
  assert.equal(s.status, 'idle');
});

test('persistedToItems carrega o failure_kind no cartão de erro', () => {
  const items = persistedToItems([
    { id: 7, role: 'system', content: 'sem crédito', failure_kind: 'quota_exceeded' },
    { id: 8, role: 'system', content: '401 legado' },
  ], []);
  assert.equal(items.length, 2);
  assert.equal(items[0].failureKind, 'quota_exceeded');
  assert.equal(items[1].failureKind, null);
});

test('análise da IA que MENCIONA 401 não é falha (o bug do /ajuda)', () => {
  // O texto do `/ajuda` do executor falso lista `/limite401`, e uma análise real
  // pode citar um erro de API. Nenhum dos dois pode abrir o modal de relogin.
  assert.equal(lastFailureFromHistory([
    { id: 1, role: 'user', content: 'analisa' },
    { id: 2, role: 'assistant', content: 'O endpoint devolveu 401 — oriente o agente.' },
  ]), null);
  assert.equal(authErrorInHistory([
    { id: 1, role: 'assistant', content: 'erro 401 authentication_error no fornecedor' },
  ]), false);
  // Mas a linha SYSTEM legada (que existe porque o gateway classificou uma
  // falha) continua valendo pela heurística.
  assert.equal(lastFailureFromHistory([
    { id: 1, role: 'system', content: 'API Error: 401 · Please run /login' },
  ]).kind, 'auth_required');
});
