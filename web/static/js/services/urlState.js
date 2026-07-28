// @ts-check
//
// URL query-string state (Plano 24) — PURE logic, no preact/DOM/network. Given a
// declarative `schema` (one descriptor per query param), reads a search string
// into a plain state object and serializes a state object back into a search
// string that OMITS anything equal to its default (so a "clean" screen has a
// clean URL). hooks/useUrlState.js is the thin window.location wrapper on top.
//
// Percent-encoding is delegated to URLSearchParams: `enc`/`dec` operate on the
// DECODED string level (URLSearchParams.get already decodes, .toString encodes),
// so codecs never double-encode. List/JSON codecs add their own inner encode so
// values with commas / reserved chars round-trip through the ',' join.

/**
 * A field descriptor:
 *   key        — the query-param name AND the state key.
 *   default    — value when the param is absent (and the value that gets omitted).
 *   dec(raw)   — decoded-string → value (optional; identity when absent).
 *   enc(value) — value → decoded-string (optional; String() when absent).
 *   isDefault(value) — omit predicate (optional; `value === default` when absent).
 * @typedef {{ key: string, default: any, dec?: (raw:string)=>any, enc?: (v:any)=>string, isDefault?: (v:any)=>boolean }} Field
 */

/**
 * Parse a search string (`location.search`, with or without leading '?') into a
 * `{ [key]: value }` object using `schema`. Absent params fall back to default.
 * @param {string} search
 * @param {Field[]} schema
 */
export function readParams(search, schema) {
  const p = new URLSearchParams(search || '');
  const out = {};
  for (const f of schema) {
    const raw = p.get(f.key);
    out[f.key] = raw == null ? f.default : (f.dec ? f.dec(raw) : raw);
  }
  return out;
}

/**
 * Serialize a state object into a query string (NO leading '?'), omitting any
 * field equal to its default. Field order follows `schema` for stable URLs.
 * @param {Record<string, any>} state
 * @param {Field[]} schema
 */
export function writeParams(state, schema) {
  const p = new URLSearchParams();
  for (const f of schema) {
    const v = state ? state[f.key] : undefined;
    const isDef = f.isDefault ? f.isDefault(v) : v === f.default;
    if (isDef || v == null) continue;
    p.set(f.key, f.enc ? f.enc(v) : String(v));
  }
  return p.toString();
}

// ── Codec factories (keep call-site schemas terse) ───────────────────────────

/** A plain string param; omitted when equal to `def` (default ''). */
export const str = (key, def = '') => ({ key, default: def, isDefault: (v) => v == null || v === def });

/** An enum string param with a required default (same shape as str, semantic). */
export const enumStr = (key, def) => str(key, def);

/** A boolean flag; encoded as '1', truthy for '1'/'true', omitted when false. */
export const bool = (key) => ({
  key, default: false,
  dec: (v) => v === '1' || v === 'true',
  enc: () => '1',
  isDefault: (v) => !v,
});

/** An integer param; `def` (default null) is omitted; NaN decodes to `def`. */
export const int = (key, def = null) => ({
  key, default: def,
  dec: (v) => { const n = parseInt(v, 10); return Number.isNaN(n) ? def : n; },
  enc: (v) => String(v),
  isDefault: (v) => v == null || v === def,
});

/**
 * A comma-joined list of strings, each inner-encoded so commas/reserved chars in
 * an element survive the split. Empty list is omitted.
 */
export const list = (key) => ({
  key, default: [],
  dec: (s) => (s ? s.split(',').map(decodeURIComponent) : []),
  enc: (a) => a.map(encodeURIComponent).join(','),
  isDefault: (v) => !v || v.length === 0,
});

/**
 * A JSON-encoded object param (e.g. the advanced-filter spec). Invalid JSON on
 * read decodes to null. `isDefault(v)` decides when to omit (e.g. isDefaultSpec).
 * @param {string} key
 * @param {{ isDefault?: (v:any)=>boolean }} [opts]
 */
export const json = (key, { isDefault } = {}) => ({
  key, default: null,
  dec: (s) => { try { return JSON.parse(s); } catch { return null; } },
  enc: (v) => JSON.stringify(v),
  isDefault: (v) => v == null || (isDefault ? isDefault(v) : false),
});
