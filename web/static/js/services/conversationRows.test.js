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
  isUnassigned, sortContactsBy, sortContacts,
  normalizeSpec, specsEqual, isDefaultSpec, DEFAULT_SPEC, DAY_SECONDS,
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

test('clauseMatches: status eq/ne and "all"', () => {
  assert.equal(clauseMatches({ conv_status: 'open' }, { dim: 'status', op: 'eq', value: 'all' }, NOW), true);
  assert.equal(clauseMatches({}, { dim: 'status', op: 'eq', value: 'open' }, NOW), true); // default 'open'
  assert.equal(clauseMatches({ conv_status: 'closed' }, { dim: 'status', op: 'ne', value: 'open' }, NOW), true);
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

test('sortContactsBy: oldest ascending; unread by total desc', () => {
  const list = [{ id: 1, last_message_ts: 10 }, { id: 2, last_message_ts: 50 }];
  assert.deepEqual(sortContactsBy(list, 'oldest').map(c => c.id), [1, 2]);
  const ul = [
    { id: 1, unread_count: 1, unread_ai_count: 0, last_message_ts: 100 },
    { id: 2, unread_count: 3, unread_ai_count: 1, last_message_ts: 10 },
  ];
  assert.deepEqual(sortContactsBy(ul, 'unread').map(c => c.id), [2, 1]);
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
