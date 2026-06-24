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
export async function getCustomAttributes(appliesTo = null) {
  const qs = appliesTo ? `?applies_to=${encodeURIComponent(appliesTo)}` : '';
  return request('GET', `/api/custom-attributes${qs}`);
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

// Exporta todos os contatos como CSV (download). Retorna o Blob ou null.
export async function exportContacts() {
  const res = await fetch(`${BASE}/api/contacts/export`, {
    method: 'GET',
    headers: _authHeaders(),
  });
  if (res.status === 401) { handleUnauthorized(); return null; }
  if (!res.ok) return null;
  return res.blob();
}

// Importa contatos de um CSV. `file` é um File do input. Retorna {ok, data, error}.
export async function importContacts(file) {
  const form = new FormData();
  form.append('file', file);
  // Sem 'Content-Type': o browser define o boundary do multipart sozinho.
  const res = await fetch(`${BASE}/api/contacts/import`, {
    method: 'POST',
    headers: _authHeaders(),
    body: form,
  });
  if (res.status === 401) { handleUnauthorized(); return { ok: false, error: 'Não autenticado.' }; }
  return res.json();
}

// Number of conversations with unread messages (for the browser-tab badge).
export async function getUnreadCount() {
  return request('GET', '/api/contacts/unread-count');
}

// `channelId` escopa o thread ao canal escolhido (multicanal): ao abrir uma
// conversa NOVA pela caixa de entrada selecionada, antes de existir uma conversa
// nesse canal, carrega só as mensagens daquele canal (vazio se ainda não houver) —
// nunca cai na conversa de outro canal do mesmo número.
export async function getContact(phone, markRead = true, channelId = null) {
  const params = [];
  if (!markRead) params.push('mark_read=false');
  if (channelId) params.push(`channel_id=${encodeURIComponent(channelId)}`);
  const qs = params.length ? `?${params.join('&')}` : '';
  return request('GET', `/api/contacts/${encodeURIComponent(phone)}${qs}`);
}

// Conversa-cêntrico (plano 11 D1): carrega a thread de UMA conversa (um canal),
// sem fundir os canais do mesmo número. Retorna {conversation, contact, messages,
// channel_id, avatar_v}. markRead=false não zera o badge daquela conversa.
export async function getConversationMessages(convId, markRead = true) {
  const qs = markRead ? '' : '?mark_read=false';
  return request('GET', `/api/conversations/${convId}/messages${qs}`);
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

// conversationId (plano 11 D1) roteia o envio pelo CANAL daquela conversa via
// OutboundRouter; ausente cai no 'default' (GOWA), preservando o legado.
// channelId roteia o envio quando a conversa AINDA não existe (1ª mensagem de uma
// conversa nova iniciada pelo picker de caixa de entrada). Quando há conversationId,
// o backend ignora channelId e usa o canal da conversa.
export async function sendMessage(phone, message, replyTo = null, conversationId = null, channelId = null) {
  const body = { message };
  if (replyTo) body.reply_to = replyTo;
  if (conversationId != null) body.conversation_id = conversationId;
  if (channelId != null) body.channel_id = channelId;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/send`, body);
}

export async function retrySend(phone, message, conversationId = null, channelId = null) {
  const body = { message };
  if (conversationId != null) body.conversation_id = conversationId;
  if (channelId != null) body.channel_id = channelId;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/retry-send`, body);
}

// Delete a message. scope='me' (local) or scope='all' (revoke for everyone).
// Pass msgId (GOWA id) and/or dbId (DB row id, for local messages without a msg_id).
export async function deleteMessage(phone, { msgId = null, dbId = null, scope = 'me', conversationId = null } = {}) {
  const body = { msg_id: msgId, db_id: dbId, scope };
  if (conversationId != null) body.conversation_id = conversationId;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/messages/delete`, body);
}

// React to a message with an emoji. Empty emoji removes the operator's reaction.
export async function reactToMessage(phone, msgId, emoji, conversationId = null) {
  const body = { msg_id: msgId, emoji };
  if (conversationId != null) body.conversation_id = conversationId;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/messages/react`, body);
}

export async function sendPrivateMessage(phone, text, opts = {}) {
  const body = { text };
  if (opts.aiRead !== undefined) body.ai_read = !!opts.aiRead;
  if (opts.aiReply !== undefined) body.ai_reply = !!opts.aiReply;
  if (opts.conversationId != null) body.conversation_id = opts.conversationId;
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

export async function sendImage(phone, file, caption = '', conversationId = null, channelId = null) {
  const form = new FormData();
  form.append('image', file);
  form.append('caption', caption);
  if (conversationId != null) form.append('conversation_id', String(conversationId));
  if (channelId != null) form.append('channel_id', String(channelId));
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

export async function sendAudio(phone, blob, filename = 'voice.ogg', conversationId = null, channelId = null) {
  const form = new FormData();
  form.append('audio', blob, filename);
  if (conversationId != null) form.append('conversation_id', String(conversationId));
  if (channelId != null) form.append('channel_id', String(channelId));
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

export async function sendDocument(phone, file, caption = '', conversationId = null, channelId = null) {
  const form = new FormData();
  form.append('document', file);
  form.append('caption', caption);
  if (conversationId != null) form.append('conversation_id', String(conversationId));
  if (channelId != null) form.append('channel_id', String(channelId));
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

export async function sendPresence(phone, action = 'start', conversationId = null) {
  const body = { action };
  if (conversationId != null) body.conversation_id = conversationId;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/presence`, body);
}

// create=false apenas valida o número sem materializar o contato (usado pela
// verificação ao vivo do modal "Nova conversa" — o contato só nasce no envio).
// channelId roteia a verificação pelo canal escolhido: só o GOWA consulta o
// WhatsApp; Cloud API/Telegram não verificam antes de enviar (assumem válido).
export async function checkPhone(phone, create = true, channelId = null) {
  const body = { phone, create };
  if (channelId) body.channel_id = channelId;
  return request('POST', '/api/contacts/check-phone', body);
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

// Hard-delete a single conversation/thread (plano 16). Keeps the contact and its
// other conversations; only this thread + its messages are removed.
export async function deleteConversation(id) {
  return request('DELETE', `/api/conversations/${id}`);
}

// Assume the conversation for the current user (plano 10 Onda 0). No body — the
// server resolves "me" from the authenticated session.
export async function assignMeConversation(id) {
  return request('POST', `/api/conversations/${id}/assign-me`, {});
}

// Toggle the conversation-level AI (ai_active) — distinct from the contact-level
// toggle. Drives the chat-header IA switch (FF3).
export async function setConversationAi(id, active) {
  return request('POST', `/api/conversations/${id}/ai`, { active });
}

// Resolve the conversation for a contact by phone (feeds the chat header and the
// sidebar right-click menu). With { includeClosed: true } the server returns the
// latest conversation regardless of status, so a resolved thread still resolves
// (lets the menu show its assignee and a "reopen" action).
export async function getContactConversation(phone, { includeClosed = false } = {}) {
  const qs = includeClosed ? '?include_closed=true' : '';
  return request('GET', `/api/contacts/${encodeURIComponent(phone)}/conversation${qs}`);
}

// Agents that can take a conversation (plano 10): {users:[...], ai_agents:[...]}.
// Gated by conversation.read so attendants (not only admins) can transfer.
export async function getAssignableAgents() {
  return request('GET', '/api/conversations/assignable-agents');
}

// Unified assignment (plano 10): route a conversation to a human or an AI agent.
// kind: 'user' (needs userId), 'ai' (needs agentKey) or 'none' (unassign).
export async function assignAgent(id, { kind, userId = null, agentKey = null } = {}) {
  const body = { kind };
  if (kind === 'user') body.user_id = userId;
  if (kind === 'ai') body.agent_key = agentKey;
  return request('POST', `/api/conversations/${id}/assign-agent`, body);
}

// Update a conversation's custom_attributes (FF5). Server validates keys against
// the conversation attribute definitions.
export async function updateConversationInfo(id, body) {
  return request('PUT', `/api/conversations/${id}/info`, body);
}

// ── Conversation labels (Onda 3) ──────────────────────────────────
// Etiquetas próprias da conversa (registro global + atribuição por conversa),
// separadas das tags de contato.

export async function getConversationLabels() {
  return request('GET', '/api/conversation-labels');
}

export async function createConversationLabel(name, color) {
  return request('POST', '/api/conversation-labels', { name, color });
}

export async function updateConversationLabel(id, data) {
  return request('PUT', `/api/conversation-labels/${id}`, data);
}

export async function deleteConversationLabel(id) {
  return request('DELETE', `/api/conversation-labels/${id}`);
}

// Labels currently attached to ONE conversation: {conversation_id, labels:[...]}.
export async function getConversationLabelsFor(convId) {
  return request('GET', `/api/conversations/${convId}/labels`);
}

// Replace a conversation's labels (snapshot of names).
export async function updateConversationLabels(convId, labels) {
  return request('PUT', `/api/conversations/${convId}/labels`, { labels });
}

// ── Templates (Cloud API, Frente C) ───────────────────────────────
// Channel-aware: returns {supported, templates}. Only channels with the
// `templates` capability (WhatsApp Cloud) return supported=true.
export async function getConversationTemplates(convId) {
  return request('GET', `/api/conversations/${convId}/templates`);
}

// body: {template_name, language?, components?, preview_text?}
export async function sendConversationTemplate(convId, payload) {
  return request('POST', `/api/conversations/${convId}/send-template`, payload);
}

// Create a template (WhatsApp Cloud) — gated by template.create.
// body: {name, category?, language?, body_text, header_text?, footer_text?,
//        body_examples?, header_examples?}
export async function createConversationTemplate(convId, payload) {
  return request('POST', `/api/conversations/${convId}/templates`, payload);
}

// Delete a template (all languages) by name — gated by template.delete.
export async function deleteConversationTemplate(convId, name) {
  return request('DELETE', `/api/conversations/${convId}/templates/${encodeURIComponent(name)}`);
}

// ── Templates / janela 24h ao iniciar conversa (plano 21) ─────────────
// Versões CHANNEL-scoped (sem conversa ainda): a "Nova conversa" precisa saber a
// janela e listar/enviar templates antes que a conversa exista.

// Estado da janela de 24h para iniciar uma conversa em `channelId` com `phone`.
// Retorna {templates_supported, session_open, has_conversation, conversation_id, last_inbound_ts}.
export async function getChannelSessionState(channelId, phone) {
  return request('GET', `/api/channels/${encodeURIComponent(channelId)}/session-state?phone=${encodeURIComponent(phone)}`);
}

// Lista os templates de um canal (mesmo shape de getConversationTemplates).
export async function getChannelTemplates(channelId) {
  return request('GET', `/api/channels/${encodeURIComponent(channelId)}/templates`);
}

// Envia um template aprovado para `phone` via `channelId` (cria a conversa).
// body: {phone, template_name, language?, components?, preview_text?}
export async function sendChannelTemplate(channelId, payload) {
  return request('POST', `/api/channels/${encodeURIComponent(channelId)}/send-template`, payload);
}

// Cria um template no canal — gated por template.create.
export async function createChannelTemplate(channelId, payload) {
  return request('POST', `/api/channels/${encodeURIComponent(channelId)}/templates`, payload);
}

// Apaga um template (todas as línguas) no canal — gated por template.delete.
export async function deleteChannelTemplate(channelId, name) {
  return request('DELETE', `/api/channels/${encodeURIComponent(channelId)}/templates/${encodeURIComponent(name)}`);
}

// ── Channels (plano 02 Fase 2) ────────────────────────────────────
// Messaging channels (providers: gowa, whatsapp_cloud, telegram, test).
// Credentials are ALWAYS returned masked by the backend; sending a new
// credential value replaces it. All responses are {ok, data, error}.

export async function listChannels() {
  return request('GET', '/api/channels');
}

// Canais arquivados (soft-delete) — para a seção de restauração.
export async function listArchivedChannels() {
  return request('GET', '/api/channels?archived=true');
}

// Desarquiva um canal arquivado (volta para a lista; fica desativado até reativar).
export async function restoreChannel(id) {
  return request('POST', `/api/channels/${encodeURIComponent(id)}/restore`);
}

// Canais conectados (connected + logged_in + enabled) que um operador pode usar
// para iniciar uma conversa nova. Mais leve e com menos privilégio que listChannels
// (gated por conversation.reply, sem credenciais). Itens: {id, provider, display_name, own_phone}.
export async function listConnectedChannels() {
  return request('GET', '/api/channels/connected');
}

export async function getChannel(id) {
  return request('GET', `/api/channels/${encodeURIComponent(id)}`);
}

// body: {id, provider, display_name, config?, credentials?:{key:value}}
export async function createChannel(payload) {
  return request('POST', '/api/channels', payload);
}

// body: {display_name?, enabled?, config?, credentials?:{key:value}}
export async function updateChannel(id, payload) {
  return request('PUT', `/api/channels/${encodeURIComponent(id)}`, payload);
}

export async function deleteChannel(id) {
  return request('DELETE', `/api/channels/${encodeURIComponent(id)}`);
}

// Usuários do painel atribuíveis como agentes de um canal (criação + edição).
// → {users:[{id,name,email,is_admin}]}
export async function listChannelAssignableUsers() {
  return request('GET', '/api/channels/assignable-users');
}

// Providers disponíveis para CRIAR um canal — só os cujos plugins estão ativos
// (GOWA é core e sempre presente). → {providers:["gowa", ...]}
export async function listChannelProviders() {
  return request('GET', '/api/channels/providers');
}

// Agentes (usuários do painel) que veem/recebem a inbox deste canal.
// → {inbox_id, member_ids:[...], users:[{id,name,email,is_admin}]}
export async function getChannelMembers(id) {
  return request('GET', `/api/channels/${encodeURIComponent(id)}/members`);
}

// body: {user_ids:[...]} → substitui o conjunto de membros da inbox do canal.
export async function setChannelMembers(id, userIds) {
  return request('PUT', `/api/channels/${encodeURIComponent(id)}/members`, { user_ids: userIds });
}

// {connected, logged_in, needs_qr, own_phone, error}
export async function getChannelStatus(id) {
  return request('GET', `/api/channels/${encodeURIComponent(id)}/status`);
}

// GOWA login QR for a channel's device. Returns an object-URL string for the
// PNG, or null when there's no QR (already logged in / not ready → 204). The
// caller must URL.revokeObjectURL() the returned url when done.
export async function getChannelQR(id) {
  try {
    const res = await fetch(`${BASE}/api/channels/${encodeURIComponent(id)}/qr`, {
      headers: _authHeaders(),
    });
    if (res.status === 401) { handleUnauthorized(); return null; }
    if (!res.ok || res.status === 204) return null;
    const blob = await res.blob();
    if (!blob || blob.size < 100) return null;
    return URL.createObjectURL(blob);
  } catch (e) {
    return null;
  }
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

// ── AI Engine (plano 06) — agents / prompts / variables / tools ────
// Config-in-DB + code-in-DB. All responses are {ok, data, error}; the data is
// the list/object directly (no extra wrapper key). Agent/prompt/variable edits
// apply on the next message; tool (code-in-DB) edits schedule a restart.

export async function listAgents() {
  return request('GET', '/api/ai/agents');
}

export async function getAgent(key) {
  return request('GET', `/api/ai/agents/${encodeURIComponent(key)}`);
}

// body: {display_name, prompt_key, model_config(obj), tool_names(list|null),
//        enabled(bool), description, is_router(bool), routing_targets(list|null)}
export async function saveAgent(key, data) {
  return request('PUT', `/api/ai/agents/${encodeURIComponent(key)}`, data);
}

export async function getAgentHistory(key) {
  return request('GET', `/api/ai/agents/${encodeURIComponent(key)}/history`);
}

export async function rollbackAgent(key, version) {
  return request('POST', `/api/ai/agents/${encodeURIComponent(key)}/rollback/${version}`);
}

export async function deleteAgent(key) {
  return request('DELETE', `/api/ai/agents/${encodeURIComponent(key)}`);
}

// Full registry of tools registered in the handler (core + plugin + installed
// code-in-DB), used by the agent editor's per-agent tool selection. The agent's
// `tool_names` filter applies over THIS set, not just code-in-DB tools, so the
// picker must read from here. Normalised to {ok, data: array} like the others.
export async function listRegisteredTools() {
  const res = await request('GET', '/api/tools');
  if (res && res.ok) return { ok: true, data: (res.data && res.data.tools) || [] };
  return res;
}

export async function listPrompts() {
  return request('GET', '/api/ai/prompts');
}

export async function getPrompt(key) {
  return request('GET', `/api/ai/prompts/${encodeURIComponent(key)}`);
}

// body: {body}
export async function savePrompt(key, body) {
  return request('PUT', `/api/ai/prompts/${encodeURIComponent(key)}`, { body });
}

export async function getPromptHistory(key) {
  return request('GET', `/api/ai/prompts/${encodeURIComponent(key)}/history`);
}

export async function rollbackPrompt(key, version) {
  return request('POST', `/api/ai/prompts/${encodeURIComponent(key)}/rollback/${version}`);
}

export async function listVariables() {
  return request('GET', '/api/ai/variables');
}

// body: {value} (category optional)
export async function saveVariable(name, value, category = '') {
  return request('PUT', `/api/ai/variables/${encodeURIComponent(name)}`, { value, category });
}

export async function deleteVariable(name) {
  return request('DELETE', `/api/ai/variables/${encodeURIComponent(name)}`);
}

export async function listTools() {
  return request('GET', '/api/ai/tools');
}

export async function getTool(name) {
  return request('GET', `/api/ai/tools/${encodeURIComponent(name)}`);
}

// body: {description, code, dependencies(list), enabled(bool)}
export async function saveTool(name, data) {
  return request('PUT', `/api/ai/tools/${encodeURIComponent(name)}`, data);
}

export async function deleteTool(name) {
  return request('DELETE', `/api/ai/tools/${encodeURIComponent(name)}`);
}

export async function getToolHistory(name) {
  return request('GET', `/api/ai/tools/${encodeURIComponent(name)}/history`);
}

export async function rollbackTool(name, version) {
  return request('POST', `/api/ai/tools/${encodeURIComponent(name)}/rollback/${version}`);
}

export async function restartAi() {
  return request('POST', '/api/ai/restart');
}

// ── Audit trail (plano 07 Fase 2) ─────────────────────────────────
// Append-only audit log, gated by the `audit.read` permission (the backend
// returns 403 if the user lacks it). Responses are {ok, data, error}.

// Drop empty/undefined values and build a querystring from `params`. The list
// endpoint's date filters (`from`/`to`) are epoch seconds; `actor` is a user id.
function _auditQuery(params = {}) {
  const parts = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
  }
  return parts.join('&');
}

export async function listAudit(params = {}) {
  const qs = _auditQuery(params);
  return request('GET', `/api/audit${qs ? '?' + qs : ''}`);
}

export async function getAuditActions() {
  return request('GET', '/api/audit/actions');
}

// Download the audit export (csv|json). The export endpoint returns the raw
// file (not {ok,data}); a plain link/window.open wouldn't carry the bearer
// token, so we fetch with auth headers and trigger a blob download. Returns
// {ok} or {ok:false, error}.
export async function downloadAuditExport(params = {}, format = 'csv') {
  const qs = _auditQuery({ ...params, format });
  const res = await fetch(`${BASE}/api/audit/export?${qs}`, {
    headers: _authHeaders(),
  });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    return { ok: false, error: 'Não autenticado.' };
  }
  if (!res.ok) {
    let msg = 'Falha ao exportar.';
    try { const j = await res.json(); if (j && j.error) msg = j.error; } catch (_) {}
    return { ok: false, error: msg };
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = format === 'json' ? 'audit_log.json' : 'audit_log.csv';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { ok: true };
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

// Role editor (RBAC) — create/edit/delete custom roles + edit system roles.
export async function createRole(data) {
  return request('POST', '/api/roles', data);
}

export async function updateRole(id, data) {
  return request('PUT', `/api/roles/${id}`, data);
}

export async function deleteRole(id) {
  return request('DELETE', `/api/roles/${id}`);
}

export async function resetRole(id) {
  return request('POST', `/api/roles/${id}/reset`);
}
