// Run with: node --test web/static/js/services/conversationRows.test.js
//
// Characterization tests (Plano 23 · D2) for the pure row-building + filter
// matching extracted from Contacts.js. These lock the behavior BEFORE/while the
// component is decomposed, so a stale-closure or refactor regression in the
// derived sidebar list trips here instead of silently in the browser.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildRows, shapeConvData,
  clauseMatches, matchesAdvFilters, matchesTags, matchesStatus, matchesAssignment,
  isUnassigned, shouldNotifyNewMessage,
  isVisibleInSidebar, sortContactsBy, sortContacts, splitSort, combineSort,
  normalizeSpec, specsEqual, isDefaultSpec, DEFAULT_SPEC, DAY_SECONDS,
  convRowToSidebarRow, upsertConversationRow, distinctChannelCount,
  rowMatchesView, specNeedsServer, aiEffectivelyOn, patchRows,
} from './conversationRows.js';

// ── buildRows ──────────────────────────────────────────────────────
test('buildRows: contact with no conversation → single legacy phone row', () => {
  const rows = buildRows([{ id: 1, phone: '5511', name: 'Ana', last_message: 'oi' }], []);
  assert.equal(rows.length, 1);
  const r = rows[0];
  assert.equal(r.contact_id, 1);
  assert.equal(r.conversation_id, null);
  assert.equal(r.channel_id, 'default');
  assert.equal(r.channel_provider, null);
  assert.equal(r.channel_name, null);
  assert.equal(r.name, 'Ana');               // contact richness preserved
  assert.equal(r.last_message, 'oi');        // contact-level fallback kept
});

test('buildRows: one row per conversation (two channels of same number)', () => {
  const contacts = [{ id: 1, phone: '5511', name: 'Ana', last_message: 'c-agg', last_message_ts: 5 }];
  const conversations = [
    { id: 10, contact_id: 1, channel_id: 'wa', channel_provider: 'gowa', channel_name: 'WhatsApp',
      status: 'open', ai_active: 1, assignee_user_id: 7, active_agent_key: null,
      last_message: 'msg-wa', last_message_role: 'user', last_message_ts: 100,
      last_message_status: 'read', last_message_msg_id: 'M1', unread_count: 2, has_unread_mention: false },
    { id: 11, contact_id: 1, channel_id: 'tg', channel_provider: 'telegram', channel_name: 'Telegram',
      status: 'closed', ai_active: 0, assignee_user_id: null, active_agent_key: 'bot',
      last_message: 'msg-tg', last_message_role: 'assistant', last_message_ts: 200,
      last_message_status: 'sent', last_message_msg_id: 'M2', unread_count: 0, has_unread_mention: true },
  ];
  const rows = buildRows(contacts, conversations);
  assert.equal(rows.length, 2);
  const wa = rows.find(r => r.conversation_id === 10);
  const tg = rows.find(r => r.conversation_id === 11);
  // Per-conversation fields override the contact aggregates.
  assert.equal(wa.channel_id, 'wa');
  assert.equal(wa.channel_provider, 'gowa');
  assert.equal(wa.conv_status, 'open');
  assert.equal(wa.conv_ai_active, 1);
  assert.equal(wa.assignee_user_id, 7);
  assert.equal(wa.last_message, 'msg-wa');
  assert.equal(wa.last_message_ts, 100);
  assert.equal(wa.unread_count, 2);
  assert.equal(tg.conv_status, 'closed');
  assert.equal(tg.active_agent_key, 'bot');
  assert.equal(tg.has_unread_mention, true);
  // Both rows still carry the shared contact richness.
  assert.equal(wa.name, 'Ana');
  assert.equal(tg.name, 'Ana');
});

test('buildRows: empty conversation last_message falls back to contact aggregate', () => {
  const contacts = [{ id: 1, phone: '5511', last_message: 'contact-preview' }];
  const conversations = [{ id: 10, contact_id: 1, channel_id: 'wa', last_message: '' }];
  const rows = buildRows(contacts, conversations);
  assert.equal(rows[0].last_message, 'contact-preview');
});

test('buildRows: unread_count 0 from conversation is honored (not contact fallback)', () => {
  const contacts = [{ id: 1, phone: '5511', unread_count: 9 }];
  const conversations = [{ id: 10, contact_id: 1, channel_id: 'wa', unread_count: 0 }];
  assert.equal(buildRows(contacts, conversations)[0].unread_count, 0);
});

test('buildRows: conversation with null contact_id is skipped (no orphan rows)', () => {
  const rows = buildRows([{ id: 1, phone: '5511' }], [{ id: 10, contact_id: null }]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].conversation_id, null);  // contact still gets its legacy row
});

// ── plano 54: arquivo por CONVERSA ─────────────────────────────────
test('buildRows: row carries the CONVERSATION is_archived (not the contact)', () => {
  const contacts = [{ id: 1, phone: '5511', is_archived: 0 }];
  const conversations = [{ id: 10, contact_id: 1, channel_id: 'wa', is_archived: 1 }];
  assert.equal(buildRows(contacts, conversations)[0].is_archived, 1);
});

test('buildRows: contact whose only conversation is archived → NO ghost legacy row in inbox view', () => {
  // Inbox view: the archived conversation is NOT in `conversations`, but the contact
  // carries conversation_id (list_contacts = its most-recent conversation, any state) →
  // it must NOT resurface as a "Novo atendimento" legacy row.
  const contacts = [{ id: 1, phone: '5511', conversation_id: 10 }];
  const rows = buildRows(contacts, [], { archivedView: false });
  assert.equal(rows.length, 0);
});

test('buildRows: archived view shows archived conversation rows, no legacy rows', () => {
  const contacts = [
    { id: 1, phone: '5511', conversation_id: 10 },   // has an archived conversation
    { id: 2, phone: '5522', conversation_id: null }, // no conversation at all
  ];
  const conversations = [{ id: 10, contact_id: 1, channel_id: 'wa', is_archived: 1 }];
  const rows = buildRows(contacts, conversations, { archivedView: true });
  assert.equal(rows.length, 1);                  // only the archived conversation row
  assert.equal(rows[0].conversation_id, 10);
  assert.equal(rows[0].is_archived, 1);
});

test('buildRows: contact with no conversation (conversation_id null) still gets a legacy row in inbox view', () => {
  const rows = buildRows([{ id: 1, phone: '5511', conversation_id: null }], [], { archivedView: false });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].conversation_id, null);
  assert.equal(rows[0].is_archived, 0);
});

// ── distinctChannelCount (plano 56: gate do badge de canal) ────────
test('distinctChannelCount: empty / null → 0', () => {
  assert.equal(distinctChannelCount([]), 0);
  assert.equal(distinctChannelCount(null), 0);
});

test('distinctChannelCount: same provider repeated → 1', () => {
  assert.equal(distinctChannelCount([
    { channel_provider: 'gowa' }, { channel_provider: 'gowa' },
  ]), 1);
});

test('distinctChannelCount: two distinct providers → 2', () => {
  assert.equal(distinctChannelCount([
    { channel_provider: 'gowa' }, { channel_provider: 'telegram' }, { channel_provider: 'gowa' },
  ]), 2);
});

test('distinctChannelCount: rows without provider (legacy phone rows) are ignored', () => {
  assert.equal(distinctChannelCount([
    { channel_provider: null }, { channel_provider: '' }, { channel_provider: 'gowa' }, {},
  ]), 1);
});

// ── shapeConvData ──────────────────────────────────────────────────
test('shapeConvData: spreads contact + messages + channel + hints', () => {
  const d = shapeConvData({
    contact: { phone: '5511', name: 'Ana' },
    messages: [{ role: 'user' }],
    avatar_v: 42, channel_id: 'wa', conversation: { id: 9 },
    templates_supported: 1, session_open: false,
  });
  assert.equal(d.phone, '5511');
  assert.equal(d.name, 'Ana');
  assert.equal(d.messages.length, 1);
  assert.equal(d.avatar_v, 42);
  assert.equal(d.channel_id, 'wa');
  assert.deepEqual(d.conversation, { id: 9 });
  assert.equal(d.templates_supported, true);   // coerced to bool
  assert.equal(d.session_open, false);
});

test('shapeConvData: defaults for a sparse payload', () => {
  const d = shapeConvData({});
  assert.deepEqual(d.messages, []);
  assert.equal(d.channel_id, 'default');
  assert.equal(d.conversation, null);
  assert.equal(d.templates_supported, false);
  assert.equal(d.has_more, false);   // plano 50 F4: default sem mais páginas
});

test('shapeConvData: passa has_more do payload (keyset scroll-up)', () => {
  assert.equal(shapeConvData({ has_more: true }).has_more, true);
  assert.equal(shapeConvData({ has_more: false }).has_more, false);
});

// A janela da IA (capability `ai_window_hours`) fecha ANTES da do operador nos
// canais Meta: com a tag HUMAN_AGENT ligada `session_open` continua `true` por 7
// dias enquanto a IA já está calada desde as 24h. Esquecer a chave nesta
// whitelist fazia o compositor ler `undefined` e seguir oferecendo os toggles
// "IA lê"/"IA responde no chat" no 2º–7º dia, com a instrução do atendente
// descartada em silêncio pelo filtro do plugin.
test('shapeConvData: preserva ai_window_open (divergente de session_open)', () => {
  const d = shapeConvData({ session_open: true, ai_window_open: false });
  assert.equal(d.session_open, true);
  assert.equal(d.ai_window_open, false);
});

test('shapeConvData: ai_window_open ausente fica undefined (não vira false)', () => {
  // Canal sem restrição (GOWA/Telegram/Cloud) ou core antigo: só um `false`
  // explícito bloqueia — coagir para bool aqui esconderia os toggles em todo
  // canal que nunca teve janela de IA.
  assert.equal(shapeConvData({}).ai_window_open, undefined);
  assert.equal(shapeConvData({ ai_window_open: true }).ai_window_open, true);
});

// ── clauseMatches ──────────────────────────────────────────────────
const NOW = 1_000_000;  // unix seconds

test('clauseMatches: incomplete clause (empty/null value) is ignored → true', () => {
  assert.equal(clauseMatches({}, { dim: 'channel', op: 'eq', value: '' }, NOW), true);
  assert.equal(clauseMatches({}, { dim: 'tag', op: 'eq', value: null }, NOW), true);
});

test('clauseMatches: channel eq/ne (default fallback)', () => {
  assert.equal(clauseMatches({ channel_id: 'wa' }, { dim: 'channel', op: 'eq', value: 'wa' }, NOW), true);
  assert.equal(clauseMatches({ channel_id: 'wa' }, { dim: 'channel', op: 'ne', value: 'wa' }, NOW), false);
  assert.equal(clauseMatches({}, { dim: 'channel', op: 'eq', value: 'default' }, NOW), true);
});

// Plano 59: as opções do filtro "Canais" vêm do banco (GET /api/channels/for-filter,
// itens {id, provider, display_name}), não das linhas carregadas. O valor da opção
// é o `id` textual do canal — que TEM que casar com o `channel_id` da conversa. Este
// teste fixa esse contrato: uma opção construída a partir de uma linha de canal do
// endpoint casa a conversa daquele canal, mesmo que a conversa não esteja "carregada".
test('clauseMatches: opção de canal do banco casa por `id` (contrato plano 59)', () => {
  const channelRow = { id: 'tg-2', provider: 'telegram', display_name: 'Suporte TG' };
  const optionValue = channelRow.id;  // é o que o frontend usa como value da opção
  assert.equal(clauseMatches({ channel_id: 'tg-2' }, { dim: 'channel', op: 'eq', value: optionValue }, NOW), true);
  assert.equal(clauseMatches({ channel_id: 'wa' }, { dim: 'channel', op: 'eq', value: optionValue }, NOW), false);
  // canal 'default' (linha sem channel_id) casa a opção cujo id é 'default'
  assert.equal(clauseMatches({}, { dim: 'channel', op: 'eq', value: 'default' }, NOW), true);
});

test('clauseMatches: status eq/ne and "all"', () => {
  assert.equal(clauseMatches({ conv_status: 'open' }, { dim: 'status', op: 'eq', value: 'all' }, NOW), true);
  assert.equal(clauseMatches({}, { dim: 'status', op: 'eq', value: 'open' }, NOW), true); // default 'open'
  assert.equal(clauseMatches({ conv_status: 'closed' }, { dim: 'status', op: 'ne', value: 'open' }, NOW), true);
});

test('clauseMatches: starter (quem iniciou a conversa via origin)', () => {
  // Cliente = origin 'inbound'; atendente = qualquer outra origem (outbound/manual/imported/NULL).
  assert.equal(clauseMatches({ origin: 'inbound' }, { dim: 'starter', op: 'eq', value: 'customer' }, NOW), true);
  assert.equal(clauseMatches({ origin: 'inbound' }, { dim: 'starter', op: 'eq', value: 'operator' }, NOW), false);
  assert.equal(clauseMatches({ origin: 'outbound' }, { dim: 'starter', op: 'eq', value: 'operator' }, NOW), true);
  assert.equal(clauseMatches({ origin: 'manual' }, { dim: 'starter', op: 'eq', value: 'operator' }, NOW), true);
  assert.equal(clauseMatches({}, { dim: 'starter', op: 'eq', value: 'operator' }, NOW), true); // origin ausente → atendente
  // ne inverte o casamento
  assert.equal(clauseMatches({ origin: 'inbound' }, { dim: 'starter', op: 'ne', value: 'customer' }, NOW), false);
  assert.equal(clauseMatches({ origin: 'outbound' }, { dim: 'starter', op: 'ne', value: 'customer' }, NOW), true);
});

test('clauseMatches: tag eq/ne', () => {
  assert.equal(clauseMatches({ tags: ['vip'] }, { dim: 'tag', op: 'eq', value: 'vip' }, NOW), true);
  assert.equal(clauseMatches({ tags: ['vip'] }, { dim: 'tag', op: 'ne', value: 'vip' }, NOW), false);
  assert.equal(clauseMatches({ tags: [] }, { dim: 'tag', op: 'eq', value: 'vip' }, NOW), false);
});

test('clauseMatches: agent none / user: / ai:', () => {
  assert.equal(clauseMatches({ assignee_user_id: null, active_agent_key: null },
    { dim: 'agent', op: 'eq', value: 'none' }, NOW), true);
  assert.equal(clauseMatches({ assignee_user_id: 5 },
    { dim: 'agent', op: 'eq', value: 'user:5' }, NOW), true);
  assert.equal(clauseMatches({ assignee_user_id: 5 },
    { dim: 'agent', op: 'ne', value: 'user:5' }, NOW), false);
  assert.equal(clauseMatches({ active_agent_key: 'bot' },
    { dim: 'agent', op: 'eq', value: 'ai:bot' }, NOW), true);
  assert.equal(clauseMatches({}, { dim: 'agent', op: 'eq', value: 'weird' }, NOW), false);
});

test('clauseMatches: activity gt/lt/days_before', () => {
  const fiveDaysAgo = NOW - 5 * DAY_SECONDS;
  assert.equal(clauseMatches({ last_message_ts: fiveDaysAgo }, { dim: 'activity', op: 'gt', value: '3' }, NOW), true);
  assert.equal(clauseMatches({ last_message_ts: fiveDaysAgo }, { dim: 'activity', op: 'lt', value: '3' }, NOW), false);
  assert.equal(clauseMatches({ last_message_ts: fiveDaysAgo }, { dim: 'activity', op: 'days_before', value: '5' }, NOW), true);
  // no activity → treated as Infinity-old
  assert.equal(clauseMatches({}, { dim: 'activity', op: 'gt', value: '999' }, NOW), true);
  // non-numeric value → ignored (true)
  assert.equal(clauseMatches({ last_message_ts: NOW }, { dim: 'activity', op: 'gt', value: 'abc' }, NOW), true);
});

test('clauseMatches: unknown dimension → true (no restriction)', () => {
  assert.equal(clauseMatches({}, { dim: 'mystery', op: 'eq', value: 'x' }, NOW), true);
});

test('clauseMatches: multi-select channel eq = "é uma de" (OR)', () => {
  const c = { channel_id: 'tg' };
  assert.equal(clauseMatches(c, { dim: 'channel', op: 'eq', value: ['wa', 'tg'] }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'channel', op: 'eq', value: ['wa', 'n1'] }, NOW), false);
});

test('clauseMatches: multi-select ne = "não é nenhuma de"', () => {
  const c = { channel_id: 'tg' };
  assert.equal(clauseMatches(c, { dim: 'channel', op: 'ne', value: ['wa', 'n1'] }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'channel', op: 'ne', value: ['wa', 'tg'] }, NOW), false);
});

test('clauseMatches: multi-select agent OR across user/ai/none', () => {
  assert.equal(clauseMatches({ assignee_user_id: 5 },
    { dim: 'agent', op: 'eq', value: ['user:5', 'user:9'] }, NOW), true);
  assert.equal(clauseMatches({ active_agent_key: 'bot' },
    { dim: 'agent', op: 'eq', value: ['user:5', 'ai:bot'] }, NOW), true);
  assert.equal(clauseMatches({ assignee_user_id: 1 },
    { dim: 'agent', op: 'eq', value: ['user:5', 'ai:bot'] }, NOW), false);
});

test('clauseMatches: empty array value → ignored (true)', () => {
  assert.equal(clauseMatches({ channel_id: 'wa' }, { dim: 'channel', op: 'eq', value: [] }, NOW), true);
});

// ── custom attributes (cattr:<scope>:<key>) ────────────────────────
test('clauseMatches: cattr conversation list eq/ne (multi OR)', () => {
  const c = { conv_custom_attributes: { plano: 'gold' } };
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:plano', op: 'eq', value: ['gold', 'silver'] }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:plano', op: 'eq', value: ['silver'] }, NOW), false);
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:plano', op: 'ne', value: ['silver'] }, NOW), true);
});

test('clauseMatches: cattr contact reads contact bag (not conversation)', () => {
  const c = { custom_attributes: { cpf: '123' }, conv_custom_attributes: { cpf: '999' } };
  assert.equal(clauseMatches(c, { dim: 'cattr:contact:cpf', op: 'eq', value: '123' }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'cattr:contact:cpf', op: 'eq', value: '999' }, NOW), false);
});

test('clauseMatches: cattr text contains / not_contains (case-insensitive)', () => {
  const c = { conv_custom_attributes: { notes: 'Cliente URGENTE' } };
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:notes', op: 'contains', value: 'urgente' }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:notes', op: 'not_contains', value: 'spam' }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:notes', op: 'not_contains', value: 'urgente' }, NOW), false);
});

test('clauseMatches: cattr number gt/lt', () => {
  const c = { conv_custom_attributes: { score: 7 } };
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:score', op: 'gt', value: '5' }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:score', op: 'lt', value: '5' }, NOW), false);
});

test('clauseMatches: cattr date gt/lt (ISO strings)', () => {
  const c = { conv_custom_attributes: { contrato: '2026-06-29' } };
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:contrato', op: 'gt', value: '2026-01-01' }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:contrato', op: 'lt', value: '2026-01-01' }, NOW), false);
});

test('clauseMatches: cattr checkbox eq via string compare', () => {
  const c = { conv_custom_attributes: { vip: true } };
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:vip', op: 'eq', value: 'true' }, NOW), true);
  assert.equal(clauseMatches(c, { dim: 'cattr:conversation:vip', op: 'eq', value: 'false' }, NOW), false);
});

// ── matchesAdvFilters / matchesTags / matchesStatus / matchesAssignment ──
test('matchesAdvFilters: empty list → true; AND across clauses', () => {
  assert.equal(matchesAdvFilters({}, [], NOW), true);
  assert.equal(matchesAdvFilters({}, null, NOW), true);
  const c = { channel_id: 'wa', tags: ['vip'] };
  assert.equal(matchesAdvFilters(c, [
    { dim: 'channel', op: 'eq', value: 'wa' },
    { dim: 'tag', op: 'eq', value: 'vip' },
  ], NOW), true);
  assert.equal(matchesAdvFilters(c, [
    { dim: 'channel', op: 'eq', value: 'wa' },
    { dim: 'tag', op: 'eq', value: 'gold' },
  ], NOW), false);
});

test('matchesTags: empty filter → true; OR semantics', () => {
  assert.equal(matchesTags({ tags: ['a'] }, []), true);
  assert.equal(matchesTags({ tags: ['a'] }, ['a', 'b']), true);
  assert.equal(matchesTags({ tags: ['c'] }, ['a', 'b']), false);
  assert.equal(matchesTags({}, ['a']), false);
});

test('matchesStatus / isUnassigned / matchesAssignment', () => {
  assert.equal(matchesStatus({}, 'all'), true);
  assert.equal(matchesStatus({}, 'open'), true);
  assert.equal(matchesStatus({ conv_status: 'closed' }, 'open'), false);
  assert.equal(isUnassigned({ assignee_user_id: null, active_agent_key: null }), true);
  assert.equal(isUnassigned({ assignee_user_id: 3 }), false);
  assert.equal(isUnassigned({ active_agent_key: 'k' }), false);
  assert.equal(matchesAssignment({ assignee_user_id: 3 }, 'mine', 3), true);
  assert.equal(matchesAssignment({ assignee_user_id: 3 }, 'mine', 4), false);
  assert.equal(matchesAssignment({}, 'mine', null), false);
  assert.equal(matchesAssignment({ assignee_user_id: null, active_agent_key: null }, 'unassigned', 1), true);
  assert.equal(matchesAssignment({}, 'all', null), true);
});

// Som + pop-up de mensagem nova: só as MINHAS ou as sem dono nenhum.
test('shouldNotifyNewMessage: escopo por atribuição', () => {
  const GABRIEL = 7, ANNA = 9;
  // A conversa é minha → notifica.
  assert.equal(shouldNotifyNewMessage(
    { assignee_user_id: GABRIEL, active_agent_key: null }, GABRIEL), true);
  // De outro atendente → não notifica (é o caso que motivou a mudança).
  assert.equal(shouldNotifyNewMessage(
    { assignee_user_id: ANNA, active_agent_key: null }, GABRIEL), false);
  // Sem humano e sem IA (fila "Não atribuídas") → notifica todo mundo.
  assert.equal(shouldNotifyNewMessage(
    { assignee_user_id: null, active_agent_key: null }, GABRIEL), true);
  // Sem humano mas com a IA atendendo → não notifica ninguém.
  assert.equal(shouldNotifyNewMessage(
    { assignee_user_id: null, active_agent_key: 'vendas' }, GABRIEL), false);
  assert.equal(shouldNotifyNewMessage(
    { assignee_user_id: null, active_agent_key: 'vendas' }, null), true);  // sem login: fail-open
  // FAIL-OPEN: payload sem a informação de atribuição notifica como antes.
  assert.equal(shouldNotifyNewMessage({ role: 'user' }, GABRIEL), true);
  assert.equal(shouldNotifyNewMessage(undefined, GABRIEL), true);
  // FAIL-OPEN: instalação sem login (uid nulo) notifica mesmo com dono.
  assert.equal(shouldNotifyNewMessage(
    { assignee_user_id: ANNA, active_agent_key: null }, null), true);
});

// ── sorting ────────────────────────────────────────────────────────
test('sortContactsBy: activity pins first then recent', () => {
  const list = [
    { id: 1, last_message_ts: 10 },
    { id: 2, last_message_ts: 50 },
    { id: 3, last_message_ts: 5, is_pinned: true },
  ];
  const out = sortContactsBy(list, 'activity');
  assert.deepEqual(out.map(c => c.id), [3, 2, 1]);
  // input not mutated
  assert.deepEqual(list.map(c => c.id), [1, 2, 3]);
});

test('sortContactsBy: oldest ascending; unread by total desc; read by total asc', () => {
  const list = [{ id: 1, last_message_ts: 10 }, { id: 2, last_message_ts: 50 }];
  assert.deepEqual(sortContactsBy(list, 'oldest').map(c => c.id), [1, 2]);
  const ul = [
    { id: 1, unread_count: 1, unread_ai_count: 0, last_message_ts: 100 },
    { id: 2, unread_count: 3, unread_ai_count: 1, last_message_ts: 10 },
  ];
  // 'unread' (decrescente): mais não lidas primeiro → 2, 1
  assert.deepEqual(sortContactsBy(ul, 'unread').map(c => c.id), [2, 1]);
  // 'read' (crescente): lidas/menos não lidas primeiro → 1, 2
  assert.deepEqual(sortContactsBy(ul, 'read').map(c => c.id), [1, 2]);
  // desempate por atividade quando o total de não lidas é igual
  const tie = [
    { id: 1, unread_count: 0, unread_ai_count: 0, last_message_ts: 10 },
    { id: 2, unread_count: 0, unread_ai_count: 0, last_message_ts: 50 },
  ];
  assert.deepEqual(sortContactsBy(tie, 'read').map(c => c.id), [2, 1]);
});

test('splitSort / combineSort: round-trip das duas dimensões', () => {
  const cases = [
    ['activity', 'none', 'recent'],
    ['oldest', 'none', 'oldest'],
    ['unread', 'unread', 'recent'],
    ['read', 'read', 'recent'],
    ['unread_oldest', 'unread', 'oldest'],
    ['read_oldest', 'read', 'oldest'],
  ];
  for (const [token, read, time] of cases) {
    assert.deepEqual(splitSort(token), { read, time });
    assert.equal(combineSort(read, time), token);
  }
  // token desconhecido → default
  assert.deepEqual(splitSort('lixo'), { read: 'none', time: 'recent' });
});

test('sortContactsBy: leitura é primária, recência é o desempate (combinações)', () => {
  const rows = [
    { id: 1, unread_count: 2, unread_ai_count: 0, last_message_ts: 10 },  // não lida, antiga
    { id: 2, unread_count: 2, unread_ai_count: 0, last_message_ts: 90 },  // não lida, nova
    { id: 3, unread_count: 0, unread_ai_count: 0, last_message_ts: 50 },  // lida, meio
  ];
  // não lidas primeiro + recentes primeiro → 2 (nova, não lida), 1 (antiga, não lida), 3 (lida)
  assert.deepEqual(sortContactsBy(rows, 'unread').map(c => c.id), [2, 1, 3]);
  // não lidas primeiro + antigos primeiro → 1 (antiga, não lida), 2, 3
  assert.deepEqual(sortContactsBy(rows, 'unread_oldest').map(c => c.id), [1, 2, 3]);
  // lidas primeiro + recentes primeiro → 3 (lida), 2, 1
  assert.deepEqual(sortContactsBy(rows, 'read').map(c => c.id), [3, 2, 1]);
  // lidas primeiro + antigos primeiro → 3 (lida), 1, 2
  assert.deepEqual(sortContactsBy(rows, 'read_oldest').map(c => c.id), [3, 1, 2]);
});

test('sortContacts === activity ordering (pinned first, recent desc)', () => {
  const list = [
    { id: 1, last_message_ts: 10 },
    { id: 2, last_message_ts: 50 },
    { id: 3, last_message_ts: 5, is_pinned: true },
  ];
  assert.deepEqual(sortContacts(list).map(c => c.id), [3, 2, 1]);
});

// ── spec helpers ───────────────────────────────────────────────────
test('normalizeSpec: drops clause ids, sorts tags + clauses', () => {
  const s = normalizeSpec({
    statusFilter: 'closed', sortBy: 'unread',
    tagFilter: ['b', 'a'],
    advFilters: [
      { id: 'x', dim: 'tag', op: 'eq', value: 'z' },
      { id: 'y', dim: 'channel', op: 'eq', value: 'wa' },
    ],
  });
  assert.deepEqual(s.tagFilter, ['a', 'b']);
  assert.equal(s.advFilters[0].dim, 'channel'); // sorted by dim+op+value
  assert.equal(s.advFilters[0].id, undefined);  // id dropped
  assert.equal(s.advFilters[1].value, 'z');
});

test('normalizeSpec: preserves + sorts array (multi-select) values', () => {
  const s = normalizeSpec({
    advFilters: [{ id: 'x', dim: 'channel', op: 'eq', value: ['tg', 'wa', 'n1'] }],
  });
  assert.deepEqual(s.advFilters[0].value, ['n1', 'tg', 'wa']);
});

test('specsEqual: equivalent multi-select filters compare equal regardless of order', () => {
  const a = { advFilters: [{ dim: 'channel', op: 'eq', value: ['wa', 'tg'] }] };
  const b = { advFilters: [{ dim: 'channel', op: 'eq', value: ['tg', 'wa'] }] };
  assert.equal(specsEqual(a, b), true);
});

test('specsEqual / isDefaultSpec', () => {
  assert.equal(specsEqual(
    { statusFilter: 'open', sortBy: 'activity', tagFilter: ['a', 'b'], advFilters: [] },
    { statusFilter: 'open', sortBy: 'activity', tagFilter: ['b', 'a'], advFilters: [] },
  ), true);
  assert.equal(specsEqual({ statusFilter: 'open' }, { statusFilter: 'closed' }), false);
  assert.equal(isDefaultSpec(DEFAULT_SPEC), true);
  assert.equal(isDefaultSpec({}), true);  // empty normalizes to defaults
  assert.equal(isDefaultSpec({ statusFilter: 'closed' }), false);
});

// ── convRowToSidebarRow + upsertConversationRow (plano 28 · ECST) ─────

// A `conversation_upsert` payload = an enriched conversation row (like a
// /api/atendimentos item). `id` is the CONVERSATION id, contact fields are prefixed.
const ENRICHED = {
  id: 42, contact_id: 7, contact_name: 'Bia', contact_phone: '5511', contact_is_group: 0,
  inbox_id: 3, channel_id: 'wa1', channel_provider: 'gowa', channel_name: 'WA',
  origin: 'inbound', status: 'open', ai_active: 1, assignee_user_id: null, active_agent_key: 'default',
  is_archived: 0, is_pinned: 0, has_unread_mention: false,
  last_message: 'oi', last_message_role: 'user', last_message_ts: 1000,
  last_message_status: '', last_message_msg_id: 'm1', unread_count: 2,
  labels: ['VIP'], custom_attributes: { plano: 'ouro' }, last_activity_at: 1000,
};

test('convRowToSidebarRow: enriched row → sidebar row shape (identity not conflated)', () => {
  const r = convRowToSidebarRow(ENRICHED);
  assert.equal(r.id, 7);                 // row identity = CONTACT id
  assert.equal(r.contact_id, 7);
  assert.equal(r.conversation_id, 42);   // enriched `id` = conversation id
  assert.equal(r.name, 'Bia');
  assert.equal(r.phone, '5511');
  assert.equal(r.conv_status, 'open');
  assert.equal(r.conv_ai_active, 1);
  assert.equal(r.active_agent_key, 'default');
  assert.deepEqual(r.conv_labels, ['VIP']);
  assert.deepEqual(r.conv_custom_attributes, { plano: 'ouro' });
  assert.equal(r.origin, 'inbound');
  assert.equal(r.channel_id, 'wa1');
  assert.equal(r.last_message, 'oi');
  assert.equal(r.last_message_ts, 1000);
  assert.equal(r.unread_count, 2);
  assert.equal(r.updated_at, 1000);      // sort key falls back to last_activity_at
});

test('convRowToSidebarRow shape ≡ buildRows shape for the key sidebar fields', () => {
  // buildRows produces a row from (contact, conversation); convRowToSidebarRow must
  // produce the same-shaped row from the enriched WS payload.
  const contact = { id: 7, phone: '5511', name: 'Bia' };
  const conv = {
    id: 42, contact_id: 7, channel_id: 'wa1', channel_provider: 'gowa', channel_name: 'WA',
    status: 'open', ai_active: 1, assignee_user_id: null, active_agent_key: 'default',
    custom_attributes: { plano: 'ouro' }, labels: ['VIP'], origin: 'inbound',
    last_message: 'oi', last_message_role: 'user', last_message_ts: 1000,
    last_message_status: '', last_message_msg_id: 'm1', unread_count: 2, has_unread_mention: false,
  };
  const fromBuild = buildRows([contact], [conv])[0];
  const fromWs = convRowToSidebarRow(ENRICHED);
  for (const k of ['contact_id', 'conversation_id', 'name', 'phone', 'channel_id',
    'conv_status', 'conv_ai_active', 'active_agent_key', 'conv_labels', 'origin',
    'last_message', 'last_message_ts', 'unread_count']) {
    assert.deepEqual(fromWs[k], fromBuild[k], `field ${k} must match buildRows`);
  }
});

test('upsertConversationRow: absent → INSERT (sorted)', () => {
  const prev = [{ conversation_id: 1, phone: 'a', last_message_ts: 500 }];
  const next = upsertConversationRow(prev, convRowToSidebarRow(ENRICHED));
  assert.equal(next.length, 2);
  const inserted = next.find(r => r.conversation_id === 42);
  assert.ok(inserted, 'new conversation row inserted');
  assert.equal(next[0].conversation_id, 42);  // ts 1000 > 500 → sorts first
});

test('upsertConversationRow: INSERT replaces a legacy phone-only row of same contact', () => {
  const prev = [{ conversation_id: null, phone: '5511', name: 'Bia', id: 7 }];
  const next = upsertConversationRow(prev, convRowToSidebarRow(ENRICHED));
  assert.equal(next.length, 1, 'legacy phone row replaced, not duplicated');
  assert.equal(next[0].conversation_id, 42);
});

test('upsertConversationRow: present → scoped MERGE (message fields only, not status/assignee)', () => {
  const prev = [{
    conversation_id: 42, phone: '5511', name: 'Bia',
    conv_status: 'closed', assignee_user_id: 99, conv_ai_active: 0,   // owned by dedicated events
    last_message: 'antigo', last_message_ts: 500, unread_count: 0,
  }];
  const next = upsertConversationRow(prev, convRowToSidebarRow(ENRICHED));  // incoming ts 1000
  const row = next[0];
  assert.equal(row.last_message, 'oi');        // message field merged (newer)
  assert.equal(row.last_message_ts, 1000);
  assert.equal(row.unread_count, 2);
  assert.equal(row.conv_status, 'closed');     // NOT clobbered by the upsert
  assert.equal(row.assignee_user_id, 99);      // NOT clobbered → no assign/resolve revert
  assert.equal(row.conv_ai_active, 0);         // NOT clobbered
});

test('upsertConversationRow: burst with tied last_message_ts converges on MAX unread (not the last-arriving stale count)', () => {
  // Envio rápido/concorrente de várias mensagens: os `conversation_upsert` saem com o
  // MESMO last_message_ts mas unread_count fora de ordem (3,4,4,6,5). O badge deve
  // convergir na contagem real (6), não travar no último a chegar (5).
  let rows = [{ conversation_id: 42, phone: '5511', last_message: 'a', last_message_ts: 1000, unread_count: 0 }];
  for (const uc of [3, 4, 4, 6, 5]) {
    rows = upsertConversationRow(rows, { conversation_id: 42, phone: '5511', last_message: String(uc), last_message_ts: 1000, unread_count: uc });
  }
  assert.equal(rows[0].unread_count, 6);          // MAX across the tied-ts burst
});

test('upsertConversationRow: a NEWER message (ts advances) still overwrites unread downward (read then 1 new)', () => {
  // Não é rajada: ts avança → sobrescreve normalmente (permite a queda pós-leitura).
  const prev = [{ conversation_id: 42, phone: '5511', last_message: 'x', last_message_ts: 1000, unread_count: 6 }];
  const next = upsertConversationRow(prev, { conversation_id: 42, phone: '5511', last_message: 'nova', last_message_ts: 2000, unread_count: 1 });
  assert.equal(next[0].unread_count, 1);          // ts avançou → overwrite (não MAX)
});

test('upsertConversationRow: guard — older snapshot never regresses the preview', () => {
  const prev = [{ conversation_id: 42, phone: '5511', last_message: 'novo', last_message_ts: 2000, unread_count: 5 }];
  const stale = convRowToSidebarRow({ ...ENRICHED, last_message: 'velho', last_message_ts: 1000, unread_count: 1 });
  const next = upsertConversationRow(prev, stale);
  assert.equal(next[0].last_message, 'novo');   // preview kept
  assert.equal(next[0].last_message_ts, 2000);
  assert.equal(next[0].unread_count, 5);        // unread not regressed
});

test('upsertConversationRow: t=0 seed (ts=0) inserts, then real preview (ts>0) merges forward', () => {
  const seed = convRowToSidebarRow({ ...ENRICHED, last_message: '', last_message_ts: 0, unread_count: 0, last_activity_at: 900 });
  let rows = upsertConversationRow([], seed);
  assert.equal(rows[0].last_message_ts, 0);     // brand-new row visible with no preview
  const real = convRowToSidebarRow({ ...ENRICHED, last_message: 'oi', last_message_ts: 1000, unread_count: 1 });
  rows = upsertConversationRow(rows, real);
  assert.equal(rows.length, 1, 'same conversation, no duplicate');
  assert.equal(rows[0].last_message, 'oi');     // existing (ts 0) accepts the first real preview
  assert.equal(rows[0].last_message_ts, 1000);
});

// ── isVisibleInSidebar (gate de visibilidade da lista SEM busca) ────
// O modo BUSCA desliga este gate em useConversationFilters (`searching`) — era o
// motivo de um contato recém-criado sumir da barra de busca embora aparecesse na
// tela Contatos.
test('isVisibleInSidebar: contato recém-criado (sem mensagem, sem origin) fica oculto', () => {
  assert.equal(isVisibleInSidebar({ phone: '5511', last_message_ts: 0 }, null), false);
  assert.equal(isVisibleInSidebar({ phone: '5511' }, null), false);
});

test('isVisibleInSidebar: mensagem visível OU origin inbound tornam a linha visível', () => {
  assert.equal(isVisibleInSidebar({ phone: '5511', last_message_ts: 1000 }, null), true);
  // conversa que o cliente iniciou aparece em t=0, antes de a 1ª msg ser persistida
  assert.equal(isVisibleInSidebar({ phone: '5511', last_message_ts: 0, origin: 'inbound' }, null), true);
  // origin de importação/manual NÃO conta
  assert.equal(isVisibleInSidebar({ phone: '5511', last_message_ts: 0, origin: 'imported' }, null), false);
});

test('isVisibleInSidebar: a conversa ABERTA fica visível mesmo sem mensagem', () => {
  const byConv = { phone: '5511', conversation_id: 42, last_message_ts: 0 };
  assert.equal(isVisibleInSidebar(byConv, 'conv:42'), true);
  assert.equal(isVisibleInSidebar(byConv, 'conv:99'), false);
  // linha-fantasma (sem atendimento) casa por telefone
  const byPhone = { phone: '5511', conversation_id: null, last_message_ts: 0 };
  assert.equal(isVisibleInSidebar(byPhone, 'phone:5511'), true);
  assert.equal(isVisibleInSidebar(byPhone, 'phone:5522'), false);
});

// ── convRowToSidebarRow: has_user_mention (plano 72 F1) ─────────────
test('convRowToSidebarRow: mapeia has_user_mention (false no broadcast global)', () => {
  // O payload de broadcast é ÚNICO p/ todos os clientes e não passa current_user_id →
  // has_user_mention vem sempre false; por isso a aba Menções é dimensão de servidor.
  assert.equal(convRowToSidebarRow(ENRICHED).has_user_mention, false);
  assert.equal(convRowToSidebarRow({ ...ENRICHED, has_user_mention: true }).has_user_mention, true);
});

// ── rowMatchesView (plano 72 F1: reprodução client-side da WHERE do servidor) ──
// Compõe os matchers puros EXATAMENTE como statusTagFiltered + displayedContacts
// faziam antes do serverMode curto-circuitar o filtro no cliente.
const VIEW = (over = {}) => ({
  statusFilter: 'open', assignmentTab: 'all', tagFilter: [], advFilters: [], currentUserId: 7,
  ...over,
});

test('rowMatchesView: aba de atribuição mine/unassigned/all', () => {
  const mine = { conv_status: 'open', assignee_user_id: 7, active_agent_key: null };
  const other = { conv_status: 'open', assignee_user_id: 9, active_agent_key: null };
  const none = { conv_status: 'open', assignee_user_id: null, active_agent_key: null };
  assert.equal(rowMatchesView(mine, VIEW({ assignmentTab: 'mine' }), NOW), true);
  assert.equal(rowMatchesView(other, VIEW({ assignmentTab: 'mine' }), NOW), false); // outro atendente → fora (o BUG)
  assert.equal(rowMatchesView(none, VIEW({ assignmentTab: 'mine' }), NOW), false);  // não atribuída → fora de Minhas
  assert.equal(rowMatchesView(none, VIEW({ assignmentTab: 'unassigned' }), NOW), true);
  assert.equal(rowMatchesView(mine, VIEW({ assignmentTab: 'unassigned' }), NOW), false);
  assert.equal(rowMatchesView(other, VIEW({ assignmentTab: 'all' }), NOW), true);   // Todas não corta atribuição
});

test('rowMatchesView: chip de status (open/closed/all)', () => {
  const open = { conv_status: 'open', assignee_user_id: null, active_agent_key: null };
  const closed = { conv_status: 'closed', assignee_user_id: null, active_agent_key: null };
  assert.equal(rowMatchesView(open, VIEW({ statusFilter: 'open' }), NOW), true);
  assert.equal(rowMatchesView(closed, VIEW({ statusFilter: 'open' }), NOW), false);
  assert.equal(rowMatchesView(closed, VIEW({ statusFilter: 'closed' }), NOW), true);
  assert.equal(rowMatchesView(closed, VIEW({ statusFilter: 'all' }), NOW), true);
});

test('rowMatchesView: uma CLÁUSULA de status sobrepõe o chip (nunca AND vazio)', () => {
  // chip "Abertas" + cláusula avançada "Fechada": a cláusula vence (mesmo precedente
  // de statusTagFiltered), então uma conversa FECHADA casa e uma ABERTA é excluída.
  const spec = VIEW({ statusFilter: 'open', advFilters: [{ dim: 'status', op: 'eq', value: 'closed' }] });
  const closed = { conv_status: 'closed', assignee_user_id: null, active_agent_key: null };
  const open = { conv_status: 'open', assignee_user_id: null, active_agent_key: null };
  assert.equal(rowMatchesView(closed, spec, NOW), true);
  assert.equal(rowMatchesView(open, spec, NOW), false);
});

test('rowMatchesView: funil de tag (confiável após F0)', () => {
  const vip = { conv_status: 'open', assignee_user_id: null, active_agent_key: null, tags: ['vip'] };
  const plain = { conv_status: 'open', assignee_user_id: null, active_agent_key: null, tags: [] };
  assert.equal(rowMatchesView(vip, VIEW({ tagFilter: ['vip'] }), NOW), true);
  assert.equal(rowMatchesView(plain, VIEW({ tagFilter: ['vip'] }), NOW), false);
});

test('rowMatchesView: dims avançadas channel/agent/ai/starter/contact_type', () => {
  const base = { conv_status: 'open', assignee_user_id: null, active_agent_key: null };
  const adv = (cl) => VIEW({ advFilters: [cl] });
  assert.equal(rowMatchesView({ ...base, channel_id: 'wa' }, adv({ dim: 'channel', op: 'eq', value: 'wa' }), NOW), true);
  assert.equal(rowMatchesView({ ...base, channel_id: 'tg' }, adv({ dim: 'channel', op: 'eq', value: 'wa' }), NOW), false);
  assert.equal(rowMatchesView({ ...base, assignee_user_id: 5 }, adv({ dim: 'agent', op: 'eq', value: 'user:5' }), NOW), true);
  assert.equal(rowMatchesView({ ...base, active_agent_key: 'bot' }, adv({ dim: 'agent', op: 'eq', value: 'ai:bot' }), NOW), true);
  assert.equal(rowMatchesView({ ...base, conv_ai_active: 0 }, adv({ dim: 'ai', op: 'eq', value: 'off' }), NOW), true);
  assert.equal(rowMatchesView({ ...base, conv_ai_active: 1 }, adv({ dim: 'ai', op: 'eq', value: 'off' }), NOW), false);
  assert.equal(rowMatchesView({ ...base, origin: 'inbound' }, adv({ dim: 'starter', op: 'eq', value: 'customer' }), NOW), true);
  assert.equal(rowMatchesView({ ...base, contact_type: 'telegram' }, adv({ dim: 'contact_type', op: 'eq', value: 'telegram' }), NOW), true);
  assert.equal(rowMatchesView({ ...base, contact_type: 'whatsapp' }, adv({ dim: 'contact_type', op: 'eq', value: 'telegram' }), NOW), false);
});

test('rowMatchesView: AND composto — todas as dimensões precisam casar', () => {
  const row = { conv_status: 'open', assignee_user_id: 7, active_agent_key: null, tags: ['vip'], channel_id: 'wa' };
  const spec = VIEW({ assignmentTab: 'mine', tagFilter: ['vip'], advFilters: [{ dim: 'channel', op: 'eq', value: 'wa' }] });
  assert.equal(rowMatchesView(row, spec, NOW), true);
  assert.equal(rowMatchesView({ ...row, channel_id: 'tg' }, spec, NOW), false);  // canal errado → fora
  assert.equal(rowMatchesView({ ...row, assignee_user_id: 9 }, spec, NOW), false); // outro atendente → fora
});

// ── specNeedsServer (plano 72 F1: dimensões que só o servidor decide) ──
test('specNeedsServer: aba Menções → true', () => {
  assert.equal(specNeedsServer({ assignmentTab: 'mentions', advFilters: [] }), true);
});

test('specNeedsServer: cláusula cattr:* → true (3-valued / lexical)', () => {
  assert.equal(specNeedsServer({ assignmentTab: 'all',
    advFilters: [{ dim: 'cattr:contact:cpf', op: 'ne', value: '1' }] }), true);
  assert.equal(specNeedsServer({ assignmentTab: 'all',
    advFilters: [{ dim: 'cattr:conversation:plano', op: 'gt', value: '5' }] }), true);
});

test('specNeedsServer: cláusula activity → true (last_message_ts vs last_activity_at)', () => {
  assert.equal(specNeedsServer({ assignmentTab: 'all',
    advFilters: [{ dim: 'activity', op: 'gt', value: '3' }] }), true);
});

test('specNeedsServer: mine/all + open puro → false', () => {
  assert.equal(specNeedsServer({ assignmentTab: 'mine', advFilters: [] }), false);
  assert.equal(specNeedsServer({ assignmentTab: 'all', advFilters: [] }), false);
});

test('specNeedsServer: tag/channel/status/agent/conv_label confiáveis (após F0) → false', () => {
  assert.equal(specNeedsServer({ assignmentTab: 'all', advFilters: [
    { dim: 'tag', op: 'eq', value: 'vip' },
    { dim: 'channel', op: 'eq', value: 'wa' },
    { dim: 'status', op: 'eq', value: 'closed' },
    { dim: 'agent', op: 'eq', value: 'user:5' },
    { dim: 'conv_label', op: 'eq', value: 'x' },
  ] }), false);
});

test('specNeedsServer: spec nulo/indefinido/vazio → false (defensivo)', () => {
  assert.equal(specNeedsServer(null), false);
  assert.equal(specNeedsServer(undefined), false);
  assert.equal(specNeedsServer({}), false);
});

// ── aiEffectivelyOn (plano 96 · D4/I10) ────────────────────────────
//
// Espelho do gate do servidor. A matriz cobre os 6 estados que o selo podia
// exibir errado: o bug era 14 conversas com dono humano + ai_active=1 marcadas
// como "IA" verde estando mudas de fato.

test('aiEffectivelyOn: conversa saudável (IA on, sem dono) → true', () => {
  assert.equal(aiEffectivelyOn({ conv_ai_active: 1, assignee_user_id: null }), true);
});

test('aiEffectivelyOn: ai_active 0/false → false', () => {
  assert.equal(aiEffectivelyOn({ conv_ai_active: 0 }), false);
  assert.equal(aiEffectivelyOn({ conv_ai_active: false }), false);
});

test('aiEffectivelyOn: dono humano cala mesmo com ai_active=1 (o bug do selo)', () => {
  assert.equal(aiEffectivelyOn({ conv_ai_active: 1, assignee_user_id: 7 }), false);
});

test('aiEffectivelyOn: D2 — agente vinculado NÃO devolve o verde ao dono humano', () => {
  assert.equal(
    aiEffectivelyOn({ conv_ai_active: 1, assignee_user_id: 7, active_agent_key: 'roteador' }),
    false);
});

test('aiEffectivelyOn: agente vinculado sem dono humano → true (D5)', () => {
  assert.equal(
    aiEffectivelyOn({ conv_ai_active: 1, assignee_user_id: null, active_agent_key: 'roteador' }),
    true);
});

test('aiEffectivelyOn: interruptor global desligado → false, seja qual for a linha', () => {
  assert.equal(aiEffectivelyOn({ conv_ai_active: 1 }, { autoReply: false }), false);
  assert.equal(aiEffectivelyOn({ conv_ai_active: 1, assignee_user_id: 7 }, { autoReply: false }), false);
});

test('aiEffectivelyOn: linha sem os campos (sandbox/payload antigo) → true, fail-open', () => {
  assert.equal(aiEffectivelyOn({}), true);
  assert.equal(aiEffectivelyOn(null), true);
  assert.equal(aiEffectivelyOn(undefined), true);
});

test('filtro ai: usa o MESMO veredito do selo (P4) — dono humano cai em "off"', () => {
  const row = { conv_ai_active: 1, assignee_user_id: 7 };
  assert.equal(clauseMatches(row, { dim: 'ai', op: 'eq', value: 'off' }), true);
  assert.equal(clauseMatches(row, { dim: 'ai', op: 'eq', value: 'on' }), false);
});

test('filtro ai: conversa sem dono continua em "on"', () => {
  const row = { conv_ai_active: 1, assignee_user_id: null };
  assert.equal(clauseMatches(row, { dim: 'ai', op: 'eq', value: 'on' }), true);
  assert.equal(clauseMatches(row, { dim: 'ai', op: 'ne', value: 'on' }), false);
});

// ── plano 130 · F2 — identidade do array preservada no no-op ─────────────────
// Todo evento de WS passa por `setContacts`, e o `conversation_upsert` sai a cada
// mensagem visível da INSTÂNCIA. Enquanto um patch no-op devolvia array novo, a
// identidade de `contacts` trocava ~1×/s: a sidebar re-renderizava inteira e o
// efeito da contagem das abas re-disparava, zerando o total (badge 252 ⇄ 50).

const ROWS = Object.freeze([
  { conversation_id: 1, phone: '5511', unread_count: 0, last_message_status: 'read', name: 'Ana' },
  { conversation_id: 2, phone: '5522', unread_count: 3, last_message_status: 'sent', name: 'Beto' },
]);

test('patchRows: patch que não muda valor devolve a MESMA referência', () => {
  const out = patchRows(ROWS, c => c.phone === '5511', { unread_count: 0 });
  assert.equal(out, ROWS);
});

test('patchRows: nenhuma linha casa ⇒ mesma referência', () => {
  assert.equal(patchRows(ROWS, c => c.phone === 'ninguem', { unread_count: 9 }), ROWS);
});

test('patchRows: mudança real ⇒ array novo, só a linha alvo trocada', () => {
  const out = patchRows(ROWS, c => c.phone === '5522', { unread_count: 0 });
  assert.notEqual(out, ROWS);
  assert.equal(out[0], ROWS[0], 'linha não-alvo preserva a própria identidade');
  assert.notEqual(out[1], ROWS[1]);
  assert.equal(out[1].unread_count, 0);
  assert.equal(out[1].name, 'Beto', 'campos fora do patch sobrevivem');
  assert.equal(ROWS[1].unread_count, 3, 'entrada não é mutada');
});

test('patchRows: patch por função vê a linha e pode virar no-op', () => {
  const keepName = patchRows(ROWS, c => c.phone === '5511', c => ({ name: '' || c.name }));
  assert.equal(keepName, ROWS);
  const newName = patchRows(ROWS, c => c.phone === '5511', c => ({ name: 'Ana Maria' || c.name }));
  assert.notEqual(newName, ROWS);
  assert.equal(newName[0].name, 'Ana Maria');
});

test('patchRows: patch nulo/vazio nunca troca a referência', () => {
  assert.equal(patchRows(ROWS, () => true, {}), ROWS);
  assert.equal(patchRows(ROWS, () => true, () => null), ROWS);
});

test('patchRows: entrada que não é array passa intacta', () => {
  assert.equal(patchRows(null, () => true, { a: 1 }), null);
  assert.equal(patchRows(undefined, () => true, { a: 1 }), undefined);
});

test('upsertConversationRow: re-emit idêntico devolve a MESMA lista', () => {
  const row = convRowToSidebarRow({
    id: 10, contact_id: 5, phone: '5533', name: 'Caio',
    last_message: 'oi', last_message_ts: 1000, last_activity_at: 1000, unread_count: 2,
  });
  const seeded = upsertConversationRow([], row);
  assert.equal(seeded.length, 1);
  // o MESMO snapshot chegando de novo (o caso comum numa rajada) não pode
  // re-ordenar nem trocar a identidade da lista
  assert.equal(upsertConversationRow(seeded, row), seeded);
  assert.equal(upsertConversationRow(upsertConversationRow(seeded, row), row), seeded);
});

test('upsertConversationRow: snapshot MAIS ANTIGO também é no-op de identidade', () => {
  const novo = convRowToSidebarRow({
    id: 10, contact_id: 5, phone: '5533',
    last_message: 'nova', last_message_ts: 2000, last_activity_at: 2000,
  });
  const velho = convRowToSidebarRow({
    id: 10, contact_id: 5, phone: '5533',
    last_message: 'velha', last_message_ts: 1000, last_activity_at: 1000,
  });
  const list = upsertConversationRow([], novo);
  const out = upsertConversationRow(list, velho);
  assert.equal(out, list, 'o guard anti-regressão não pode custar uma realocação');
  assert.equal(out[0].last_message, 'nova');
});

test('upsertConversationRow: mudança real continua devolvendo lista nova e ordenada', () => {
  const t0 = convRowToSidebarRow({
    id: 10, contact_id: 5, phone: '5533', last_message_ts: 1000, last_activity_at: 1000,
  });
  const t1 = convRowToSidebarRow({
    id: 10, contact_id: 5, phone: '5533', last_message: 'nova',
    last_message_ts: 2000, last_activity_at: 2000, unread_count: 4,
  });
  const list = upsertConversationRow([], t0);
  const out = upsertConversationRow(list, t1);
  assert.notEqual(out, list);
  assert.equal(out[0].last_message, 'nova');
  assert.equal(out[0].unread_count, 4);
});
