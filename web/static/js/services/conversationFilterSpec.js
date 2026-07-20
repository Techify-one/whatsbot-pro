// @ts-check
//
// Pure translator from the conversation-hub filter state to the flat params used
// by /api/atendimentos/count. Keep this conservative: when a client-side clause
// has no exact server equivalent, isServerExpressible returns false and the UI
// falls back to the loaded-row counts.

import { DAY_SECONDS } from './conversationRows.js';

const EMPTY = Object.freeze({});

function asList(value) {
  if (Array.isArray(value)) return value.map(v => String(v)).filter(Boolean);
  if (value === '' || value == null) return [];
  return [String(value)];
}

function putList(params, key, values) {
  const list = asList(values);
  if (!list.length) return;
  const prev = asList(params[key]);
  params[key] = [...new Set([...prev, ...list])];
}

function mapOp(op) {
  if (op === 'ne' || op === 'not_equal_to') return 'not_equal_to';
  if (op === 'contains') return 'contains';
  if (op === 'not_contains' || op === 'does_not_contain') return 'does_not_contain';
  if (op === 'gt' || op === 'greater_than') return 'greater_than';
  if (op === 'lt' || op === 'less_than') return 'less_than';
  return 'equal_to';
}

function addScalarClause(params, key, op, value) {
  const list = asList(value);
  if (!list.length) return true;
  const serverOp = mapOp(op);
  if (serverOp === 'equal_to') {
    putList(params, key, list);
    return true;
  }
  if (list.length > 1) return false;
  params[`${key}__op`] = serverOp;
  params[key] = list[0];
  return true;
}

function addCattrClause(params, dim, op, value) {
  const m = String(dim || '').match(/^cattr:(contact|conversation):([a-z][a-z0-9_]{0,63})$/);
  if (!m) return false;
  const key = m[1] === 'conversation' ? `cattr:${m[2]}` : `cattr:contact:${m[2]}`;
  return addScalarClause(params, key, op, value);
}

function addActivityClause(params, op, value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return true;
  if (op === 'days_before') {
    const olderThan = Math.max(0, Math.floor(n));
    params.activity__op = 'between';
    params.activity = `${olderThan},${olderThan + 1}`;
    return true;
  }
  if (op !== 'gt' && op !== 'lt' && op !== 'greater_than' && op !== 'less_than') {
    return false;
  }
  params.activity__op = mapOp(op);
  params.activity = String(n);
  return true;
}

function addClause(params, cl) {
  const { dim, op = 'eq', value } = cl || EMPTY;
  if (!dim || value === '' || value == null) return true;
  if (Array.isArray(value) && value.length === 0) return true;
  if (String(dim).startsWith('cattr:')) return addCattrClause(params, dim, op, value);
  if (dim === 'tag') {
    if (mapOp(op) !== 'equal_to') return false;
    putList(params, 'labels', value);
    return true;
  }
  if (dim === 'conv_label') {
    if (mapOp(op) !== 'equal_to') return false;
    putList(params, 'conv_labels', value);
    return true;
  }
  if (dim === 'status') {
    if (value === 'all') return true;
    return addScalarClause(params, 'status', op, value);
  }
  if (dim === 'channel' || dim === 'contact_type' || dim === 'agent'
      || dim === 'ai' || dim === 'starter') {
    return addScalarClause(params, dim, op, value);
  }
  if (dim === 'activity') return addActivityClause(params, op, value);
  return false;
}

function clauseParamKey(cl) {
  const dim = cl && cl.dim;
  const value = cl && cl.value;
  if (!dim || value === '' || value == null) return null;
  if (Array.isArray(value) && value.length === 0) return null;
  if (dim === 'tag') return 'labels';
  if (dim === 'conv_label') return 'conv_labels';
  if (dim === 'status') return value === 'all' ? null : 'status';
  if (dim === 'channel' || dim === 'contact_type' || dim === 'agent'
      || dim === 'ai' || dim === 'starter' || dim === 'activity') return dim;
  const m = String(dim).match(/^cattr:(contact|conversation):([a-z][a-z0-9_]{0,63})$/);
  if (m) return m[1] === 'conversation' ? `cattr:${m[2]}` : `cattr:contact:${m[2]}`;
  return '__unsupported__';
}

/**
 * @param {{ search?: string, searching?: boolean, statusFilter?: string, tagFilter?: string[], advFilters?: any[], archived?: boolean }} spec
 * @returns {Record<string, any>}
 */
export function buildCountParams(spec = EMPTY) {
  const params = {};
  const search = String(spec.search || '').trim();
  if (search) params.q = search;
  params.archived = spec.archived ? 'true' : 'false';
  const adv = Array.isArray(spec.advFilters) ? spec.advFilters : [];
  const hasStatusClause = adv.some(cl => cl && cl.dim === 'status' && cl.value !== '' && cl.value != null);
  if (!spec.searching && !hasStatusClause && spec.statusFilter && spec.statusFilter !== 'all') {
    params.status = spec.statusFilter;
  }
  putList(params, 'labels', spec.tagFilter || []);
  for (const cl of adv) addClause(params, cl);
  return params;
}

/**
 * @param {{ advFilters?: any[] }} spec
 * @returns {boolean}
 */
export function isServerExpressible(spec = EMPTY) {
  const params = {};
  const seen = new Set();
  if ((spec.tagFilter || []).length) seen.add('labels');
  for (const cl of (spec.advFilters || [])) {
    const key = clauseParamKey(cl);
    if (key === '__unsupported__') return false;
    if (key && seen.has(key)) return false;
    if (key) seen.add(key);
    if (!addClause(params, cl)) return false;
  }
  return true;
}

export function activityDaysToSeconds(days) {
  const n = Number(days);
  return Number.isFinite(n) ? n * DAY_SECONDS : null;
}
