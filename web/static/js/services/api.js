/**
 * REST API client for WhatsBot backend.
 */

const BASE = '';

function _getToken() {
  return localStorage.getItem('whatsbot_token') || '';
}

function _authHeaders(headers = {}) {
  const token = _getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

export function authHeaders(extra = {}) {
  return _authHeaders({ ...extra });
}

export function handleUnauthorized() {
  localStorage.removeItem('whatsbot_token');
  window.dispatchEvent(new Event('whatsbot:unauthorized'));
}

async function request(method, path, body) {
  const opts = {
    method,
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  return res.json();
}

export async function getConfig() {
  return request('GET', '/api/config');
}

export async function saveConfig(config) {
  return request('PUT', '/api/config', config);
}

export async function testApiKey(apiKey) {
  return request('POST', '/api/config/test-key', { api_key: apiKey });
}

// Quick replies (plano 04) — global single list, plain text.
export async function getQuickReplies() {
  return request('GET', '/api/quick-replies');
}

export async function createQuickReply(data) {
  return request('POST', '/api/quick-replies', data);
}

export async function updateQuickReply(id, data) {
  return request('PUT', `/api/quick-replies/${id}`, data);
}

export async function deleteQuickReply(id) {
  return request('DELETE', `/api/quick-replies/${id}`);
}

// ── Custom attributes (plano 05) ──────────────────────────────────
export async function getCustomAttributes(appliesTo = 'contact') {
  return request('GET', `/api/custom-attributes?applies_to=${encodeURIComponent(appliesTo)}`);
}

export async function createCustomAttribute(def) {
  return request('POST', '/api/custom-attributes', def);
}

export async function updateCustomAttribute(id, def) {
  return request('PUT', `/api/custom-attributes/${id}`, def);
}

export async function deleteCustomAttribute(id) {
  return request('DELETE', `/api/custom-attributes/${id}`);
}

// ── Runtime observability (plano 09 Fase 5) ───────────────────────
export async function getRuntimeTasks() {
  return request('GET', '/api/runtime/tasks');
}

export async function getRuntimeSubprocesses() {
  return request('GET', '/api/runtime/subprocesses');
}

export async function getStatus() {
  return request('GET', '/api/status');
}

export async function reconnect() {
  return request('POST', '/api/whatsapp/reconnect');
}

export async function logout() {
  return request('POST', '/api/whatsapp/logout');
}

export async function fetchQrBlob() {
  const res = await fetch(`${BASE}/api/qr?t=${Date.now()}`, {
    headers: _authHeaders(),
  });
  if (!res.ok) return null;
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function refreshQr() {
  return request('POST', '/api/qr/refresh');
}

// ── Setup wizard ───────────────────────────────────────────────────

export async function setupRequestKey() {
  return request('POST', '/api/setup/request-key');
}

export async function setupKeyStatus() {
  return request('GET', '/api/setup/key-status');
}

// ── Sandbox ────────────────────────────────────────────────────────

export async function sandboxSend(phone, message) {
  return request('POST', '/api/sandbox/send', { phone, message });
}

export async function sandboxClear(phone) {
  return request('POST', '/api/sandbox/clear', { phone: phone || '' });
}

async function _sandboxUpload(path, fields) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value instanceof Blob) form.append(key, value, value.name || 'file');
    else form.append(key, value ?? '');
  }
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form,
  });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  return res.json();
}

export async function sandboxSendImage(phone, file, caption = '') {
  return _sandboxUpload('/api/sandbox/send-image', { phone, caption, image: file });
}

export async function sandboxSendAudio(phone, blob, filename = 'voice.ogg') {
  const named = blob instanceof File ? blob : new File([blob], filename, { type: blob.type || 'audio/ogg' });
  return _sandboxUpload('/api/sandbox/send-audio', { phone, audio: named });
}

export async function sandboxSendDocument(phone, file, caption = '') {
  return _sandboxUpload('/api/sandbox/send-document', { phone, caption, document: file });
}

// ── Contacts ──────────────────────────────────────────────────────

export async function getContacts(q = '', archived = false) {
  const params = [];
  if (archived) params.push('archived=true');
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  const query = params.length ? `?${params.join('&')}` : '';
  return request('GET', `/api/contacts${query}`);
}

// Number of conversations with unread messages (for the browser-tab badge).
export async function getUnreadCount() {
  return request('GET', '/api/contacts/unread-count');
}

export async function getContact(phone, markRead = true) {
  const qs = markRead ? '' : '?mark_read=false';
  return request('GET', `/api/contacts/${encodeURIComponent(phone)}${qs}`);
}

export async function deleteContact(phone) {
  return request('DELETE', `/api/contacts/${encodeURIComponent(phone)}`);
}

export async function archiveContact(phone, archived) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/archive`, { archived });
}

export async function pinContact(phone, pinned) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/pin`, { pinned });
}

export async function sendMessage(phone, message, replyTo = null) {
  const body = { message };
  if (replyTo) body.reply_to = replyTo;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/send`, body);
}

export async function retrySend(phone, message) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/retry-send`, { message });
}

// Delete a message. scope='me' (local) or scope='all' (revoke for everyone).
// Pass msgId (GOWA id) and/or dbId (DB row id, for local messages without a msg_id).
export async function deleteMessage(phone, { msgId = null, dbId = null, scope = 'me' } = {}) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/messages/delete`, {
    msg_id: msgId, db_id: dbId, scope,
  });
}

// React to a message with an emoji. Empty emoji removes the operator's reaction.
export async function reactToMessage(phone, msgId, emoji) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/messages/react`, {
    msg_id: msgId, emoji,
  });
}

export async function sendPrivateMessage(phone, text, opts = {}) {
  const body = { text };
  if (opts.aiRead !== undefined) body.ai_read = !!opts.aiRead;
  if (opts.aiReply !== undefined) body.ai_reply = !!opts.aiReply;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/private-message`, body);
}

export async function markAsRead(phone) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/read`);
}

export async function markAsUnread(phone) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/unread`);
}

export async function markAllUnread() {
  return request('POST', `/api/contacts/mark-all-unread`);
}

export async function markAllRead() {
  return request('POST', `/api/contacts/mark-all-read`);
}

export async function updateContactInfo(phone, info) {
  return request('PUT', `/api/contacts/${encodeURIComponent(phone)}/info`, info);
}

export async function toggleContactAI(phone, enabled) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/toggle-ai`, { enabled });
}

export async function getGroupMembers(groupJid, force = false) {
  const qs = force ? '?force=true' : '';
  return request('GET', `/api/contacts/${encodeURIComponent(groupJid)}/members${qs}`);
}

export async function sendImage(phone, file, caption = '') {
  const form = new FormData();
  form.append('image', file);
  form.append('caption', caption);
  const res = await fetch(`${BASE}/api/contacts/${encodeURIComponent(phone)}/send-image`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form,
  });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  return res.json();
}

export async function sendAudio(phone, blob, filename = 'voice.ogg') {
  const form = new FormData();
  form.append('audio', blob, filename);
  const res = await fetch(`${BASE}/api/contacts/${encodeURIComponent(phone)}/send-audio`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form,
  });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  return res.json();
}

export async function sendDocument(phone, file, caption = '') {
  const form = new FormData();
  form.append('document', file);
  form.append('caption', caption);
  const res = await fetch(`${BASE}/api/contacts/${encodeURIComponent(phone)}/send-document`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form,
  });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  return res.json();
}

export async function sendPresence(phone, action = 'start') {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/presence`, { action });
}

export async function checkPhone(phone) {
  return request('POST', '/api/contacts/check-phone', { phone });
}

// ── Tags ─────────────────────────────────────────────────────────────

export async function getTags() {
  return request('GET', '/api/tags');
}

export async function createTag(name, color) {
  return request('POST', '/api/tags', { name, color });
}

export async function updateTag(name, data) {
  return request('PUT', `/api/tags/${encodeURIComponent(name)}`, data);
}

export async function deleteTag(name) {
  return request('DELETE', `/api/tags/${encodeURIComponent(name)}`);
}

export async function updateContactTags(phone, tags) {
  return request('PUT', `/api/contacts/${encodeURIComponent(phone)}/tags`, { tags });
}

// ── Conversations (plano 01 Fase 2) ───────────────────────────────
// Conversation lifecycle: list with filters, fetch one, change status
// (open/closed), assign to a user, and archive/unarchive.

export async function listConversations(params = {}) {
  const clean = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    clean[k] = v;
  }
  const qs = new URLSearchParams(clean).toString();
  return request('GET', `/api/conversations${qs ? '?' + qs : ''}`);
}

export async function getConversation(id) {
  return request('GET', `/api/conversations/${id}`);
}

// Filter engine (plano 08). The schema describes which dimensions exist so the
// UI can render only the controls that apply to this install.
export async function getConversationFilterSchema() {
  return request('GET', '/api/conversations/filter-schema');
}

// Filter conversations via the flat-param GET endpoint. Builds the querystring
// from `params`, dropping empty values and URL-encoding keys (cattr:<key>) and
// values correctly. AND between distinct params; a comma-separated value (e.g.
// labels=vip,lead) is OR within that dimension.
export async function filterConversations(params = {}) {
  const parts = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) {
      if (v.length === 0) continue;
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v.join(','))}`);
    } else {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    }
  }
  const qs = parts.join('&');
  return request('GET', `/api/conversations/filter${qs ? '?' + qs : ''}`);
}

export async function setConversationStatus(id, status) {
  return request('POST', `/api/conversations/${id}/status`, { status });
}

export async function assignConversation(id, assigneeUserId) {
  return request('POST', `/api/conversations/${id}/assign`, {
    assignee_user_id: assigneeUserId == null ? null : assigneeUserId,
  });
}

export async function archiveConversation(id, archived) {
  return request('POST', `/api/conversations/${id}/archive`, { archived });
}

// ── Models ──────────────────────────────────────────────────────────

export async function getModels() {
  return request('GET', '/api/models');
}

// ── Logs ───────────────────────────────────────────────────────────

export async function getLogs(limit = 200) {
  return request('GET', `/api/logs?limit=${limit}`);
}

export async function clearLogs() {
  return request('DELETE', '/api/logs');
}

// ── Executions ───────────────────────────────────────────────────

export async function getExecutions(params = {}) {
  const qs = new URLSearchParams(params).toString();
  return request('GET', `/api/executions${qs ? '?' + qs : ''}`);
}

export async function getExecution(id) {
  return request('GET', `/api/executions/${id}`);
}

// ── Usage / Costs ─────────────────────────────────────────────────

export async function getUsageSummary(params = {}) {
  const qs = new URLSearchParams(params).toString();
  return request('GET', `/api/usage/summary${qs ? '?' + qs : ''}`);
}

export async function getUsageByContact(params = {}) {
  const qs = new URLSearchParams(params).toString();
  return request('GET', `/api/usage/by-contact${qs ? '?' + qs : ''}`);
}

export async function getUsageContactDetail(phone, params = {}) {
  const qs = new URLSearchParams(params).toString();
  return request('GET', `/api/usage/contact/${encodeURIComponent(phone)}${qs ? '?' + qs : ''}`);
}

// ── Update ────────────────────────────────────────────────────────

export async function checkForUpdates() {
  return request('GET', '/api/update/check');
}

export async function performUpdate() {
  return request('POST', '/api/update');
}

// ── Auth ──────────────────────────────────────────────────────────

// Login. With an email, performs an RBAC user login ({email, password});
// without one, falls back to the legacy single-password login ({password}).
export async function login(password, email = '') {
  const body = email ? { email: email.trim(), password } : { password };
  return request('POST', '/api/auth/login', body);
}

// Create the first admin user (only works while no users exist).
export async function bootstrapAdmin(data) {
  return request('POST', '/api/auth/bootstrap', data);
}

// Current identity for the bearer token (RBAC user, or legacy single-password).
export async function getMe() {
  return request('GET', '/api/auth/me');
}

export async function logoutSession() {
  return request('POST', '/api/auth/logout');
}

export async function checkAuth() {
  // checkAuth needs to send token but not trigger unauthorized event on 401
  const opts = {
    method: 'GET',
    headers: _authHeaders({ 'Content-Type': 'application/json' }),
  };
  const res = await fetch(`${BASE}/api/auth/check`, opts);
  return res.json();
}

// ── Users & roles (RBAC — plano 03) ───────────────────────────────

export async function getUsers() {
  return request('GET', '/api/users');
}

export async function getRoles() {
  return request('GET', '/api/roles');
}

export async function createUser(data) {
  return request('POST', '/api/users', data);
}

export async function updateUser(id, data) {
  return request('PUT', `/api/users/${id}`, data);
}

export async function resetUserPassword(id, password) {
  return request('POST', `/api/users/${id}/password`, { password });
}

export async function deleteUser(id) {
  return request('DELETE', `/api/users/${id}`);
}
