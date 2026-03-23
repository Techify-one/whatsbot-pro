/**
 * REST API client for WhatsBot backend.
 */

const BASE = '';

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
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

export async function getStatus() {
  return request('GET', '/api/status');
}

export async function reconnect() {
  return request('POST', '/api/whatsapp/reconnect');
}

export async function logout() {
  return request('POST', '/api/whatsapp/logout');
}

export function getQrUrl() {
  return `${BASE}/api/qr?t=${Date.now()}`;
}

// ── Sandbox ────────────────────────────────────────────────────────

export async function sandboxSend(phone, message) {
  return request('POST', '/api/sandbox/send', { phone, message });
}

export async function sandboxClear(phone) {
  return request('POST', '/api/sandbox/clear', { phone: phone || '' });
}

// ── Contacts ──────────────────────────────────────────────────────

export async function getContacts(q = '') {
  const query = q ? `?q=${encodeURIComponent(q)}` : '';
  return request('GET', `/api/contacts${query}`);
}

export async function getContact(phone) {
  return request('GET', `/api/contacts/${encodeURIComponent(phone)}`);
}

export async function sendMessage(phone, message) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/send`, { message });
}

export async function markAsRead(phone) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/read`);
}

export async function updateContactInfo(phone, info) {
  return request('PUT', `/api/contacts/${encodeURIComponent(phone)}/info`, info);
}

export async function toggleContactAI(phone, enabled) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/toggle-ai`, { enabled });
}

export async function sendImage(phone, file, caption = '') {
  const form = new FormData();
  form.append('image', file);
  form.append('caption', caption);
  const res = await fetch(`${BASE}/api/contacts/${encodeURIComponent(phone)}/send-image`, {
    method: 'POST',
    body: form,
  });
  return res.json();
}

export async function sendAudio(phone, blob) {
  const form = new FormData();
  form.append('audio', blob, 'voice.ogg');
  const res = await fetch(`${BASE}/api/contacts/${encodeURIComponent(phone)}/send-audio`, {
    method: 'POST',
    body: form,
  });
  return res.json();
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
