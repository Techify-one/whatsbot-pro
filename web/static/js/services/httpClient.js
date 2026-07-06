// @ts-check
//
// Shared HTTP transport for the WhatsBot frontend (Plano 23 · R2).
//
// Consolidates the duplicated "fetch + handle a single 401" logic that lived
// inline in `api.js`: a JSON `request(...)` path and a multipart
// `uploadRequest(...)` path (the latter was copy-pasted across `_sandboxUpload`,
// `sendImage`, `sendAudio`, `sendDocument`, `importContacts`). Both share ONE
// 401-handling branch (clear token + dispatch `whatsbot:unauthorized`).
//
// `api.js` keeps every public function but now delegates here, so existing
// `import { sendImage } from '.../api.js'` etc. keep resolving unchanged.

import { notifyPermissionDenied } from './notify.js';

const BASE = '';

/**
 * @returns {string} The bearer token from localStorage (empty when absent).
 */
function getToken() {
  return localStorage.getItem('whatsbot_token') || '';
}

/**
 * Merge the Authorization header (when a token exists) into `headers`.
 * Mutates and returns the passed object (matches the legacy `_authHeaders`).
 *
 * @param {Record<string, string>} [headers]
 * @returns {Record<string, string>}
 */
export function authHeaders(headers = {}) {
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

/**
 * Drop the stored token and notify the app that auth was lost. Idempotent.
 * @returns {void}
 */
export function handleUnauthorized() {
  localStorage.removeItem('whatsbot_token');
  window.dispatchEvent(new Event('whatsbot:unauthorized'));
}

/** Standard 401 envelope returned to callers after `handleUnauthorized()`. */
const UNAUTHORIZED_RESULT = { ok: false, error: 'Não autenticado.' };

/**
 * Read a non-2xx response and normalise it to a single `{ ok:false, error, status }`
 * shape, regardless of the two 403 body shapes the backend produces (core `_err`
 * → `{ok:false,error}` vs plugin `HTTPException` → `{detail}`). On HTTP 403 it also
 * fires the global "permission denied" toast. This is the single place that turns a
 * silent 403 (which `res.json()` used to return verbatim, tricking `res.ok===false`
 * guards into a false success) into a visible, consistently-shaped error.
 *
 * Exported so the plugin transport (`api.http` in plugins/api.js) reuses the exact
 * same normalisation + 403-toast behaviour as the core path.
 *
 * @param {Response} res - a fetch Response whose `res.ok` is false and status !== 401.
 * @returns {Promise<{ok:false, error:string, status:number}>}
 */
export async function handleErrorResponse(res) {
  let body = null;
  try { body = await res.json(); } catch (_) { /* corpo vazio / não-JSON */ }
  const error = (body && (body.error || body.detail)) || `Erro ${res.status}`;
  if (res.status === 403) notifyPermissionDenied(error);
  return { ok: false, error, status: res.status };
}

/**
 * JSON request. Serialises `body` as JSON, attaches auth, and on HTTP 401
 * clears the session and resolves to `{ ok: false, error }` (single retry-free
 * 401 path — callers don't see a thrown error for auth loss).
 *
 * @param {string} method - HTTP verb.
 * @param {string} path - Path beginning with `/` (relative to the app origin).
 * @param {unknown} [body] - JSON-serialisable body; omitted for GET/DELETE.
 * @returns {Promise<any>} The parsed JSON envelope, or the 401 fallback.
 */
export async function request(method, path, body) {
  /** @type {RequestInit} */
  const opts = {
    method,
    headers: authHeaders({ 'Content-Type': 'application/json' }),
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401) {
    handleUnauthorized();
    return { ...UNAUTHORIZED_RESULT };
  }
  if (!res.ok) return handleErrorResponse(res);
  return res.json();
}

/**
 * Multipart/form-data upload. Builds a `FormData` from `fields` (Blob/File
 * values are appended with a filename; everything else is coerced to string),
 * attaches auth (NO Content-Type — the browser sets the multipart boundary),
 * and shares the SAME single-401 branch as {@link request}.
 *
 * @param {string} path - Path beginning with `/`.
 * @param {Record<string, unknown>} fields - Form fields; Blob/File values are
 *   appended as file parts.
 * @returns {Promise<any>} The parsed JSON envelope, or the 401 fallback.
 */
export async function uploadRequest(path, fields) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value instanceof Blob) {
      // File carries its own .name; a bare Blob falls back to 'file'.
      form.append(key, value, /** @type {any} */ (value).name || 'file');
    } else {
      form.append(key, value == null ? '' : String(value));
    }
  }
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  if (res.status === 401) {
    handleUnauthorized();
    return { ...UNAUTHORIZED_RESULT };
  }
  if (!res.ok) return handleErrorResponse(res);
  return res.json();
}
