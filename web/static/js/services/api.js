/**
 * REST API client for WhatsBot backend.
 *
 * FACADE over `services/httpClient.js` (Plano 23 · R2): the JSON `request` and
 * multipart `uploadRequest` transports — incl. the single shared 401 branch —
 * live there now. This module keeps every public function and its signature, so
 * existing imports (`import { sendImage } from '.../api.js'`, `authHeaders`,
 * `handleUnauthorized`, …) keep resolving unchanged.
 */

import {
  request,
  uploadRequest,
  authHeaders as _authHeadersBase,
  handleUnauthorized,
} from './httpClient.js';

const BASE = '';

// Re-exported so the historical `import { handleUnauthorized } from '.../api.js'`
// keeps working (e.g. websocket.js, app.js).
export { handleUnauthorized };

/**
 * Auth headers for callers that build their own fetch (returns a fresh object
 * so the caller can safely mutate). Mirrors the legacy `api.authHeaders`.
 */
export function authHeaders(extra = {}) {
  return _authHeadersBase({ ...extra });
}

// Internal alias kept for the handful of functions below that build their own
// `fetch` (blob downloads / QR image) and therefore can't use `request`.
function _authHeaders(headers = {}) {
  return _authHeadersBase(headers);
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

export async function sandboxSendImage(phone, file, caption = '') {
  return uploadRequest('/api/sandbox/send-image', { phone, caption, image: file });
}

export async function sandboxSendAudio(phone, blob, filename = 'voice.ogg') {
  const named = blob instanceof File ? blob : new File([blob], filename, { type: blob.type || 'audio/ogg' });
  return uploadRequest('/api/sandbox/send-audio', { phone, audio: named });
}

export async function sandboxSendDocument(phone, file, caption = '') {
  return uploadRequest('/api/sandbox/send-document', { phone, caption, document: file });
}

export async function sandboxSendVideo(phone, file, caption = '') {
  return uploadRequest('/api/sandbox/send-video', { phone, caption, video: file });
}

// ── Contacts ──────────────────────────────────────────────────────

// `opts` (plano 50 F5/F7): { limit, offset, sort } ativam a paginação server-side — a
// resposta vira o envelope { items, total, has_more } (cap no servidor). `sort='name'`
// ordena alfabético (tela /contacts); default do servidor = recência (sidebar). SEM opts
// o shape legado (data = lista completa) é mantido, então callers antigos não mudam.
export async function getContacts(q = '', archived = false, opts = {}) {
  const params = [];
  if (archived) params.push('archived=true');
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  if (opts.limit != null) params.push(`limit=${encodeURIComponent(opts.limit)}`);
  if (opts.offset != null) params.push(`offset=${encodeURIComponent(opts.offset)}`);
  if (opts.sort) params.push(`sort=${encodeURIComponent(opts.sort)}`);
  // plano 69 F6: filtros avançados de contato (tag/contact_type/cattr:contact:*) como
  // params planos — o backend os aplica no WHERE da lista E do total (server-side).
  if (opts.filters) {
    for (const [k, v] of Object.entries(opts.filters)) {
      if (v === undefined || v === null || v === '') continue;
      const val = Array.isArray(v) ? v.join(',') : v;
      if (val === '') continue;
      params.push(`${encodeURIComponent(k)}=${encodeURIComponent(val)}`);
    }
  }
  const query = params.length ? `?${params.join('&')}` : '';
  // plano 62 F3: `opts.signal` (AbortSignal) cancela o request; abort rejeita
  // com AbortError — caller engole (não é erro de UI).
  return request('GET', `/api/contacts${query}`, undefined, { signal: opts.signal });
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
  return uploadRequest('/api/contacts/import', { file });
}

// Number of conversations with unread messages (for the browser-tab badge).
export async function getUnreadCount() {
  return request('GET', '/api/contacts/unread-count');
}

// `channelId` escopa o thread ao canal escolhido (multicanal): ao abrir uma
// atendimento Novo pela caixa de entrada selecionada, antes de existir um atendimento
// nesse canal, carrega só as mensagens daquele canal (vazio se ainda não houver) —
// nunca cai no atendimento de outro canal do mesmo número.
// `opts` (plano 50 F4): { limit, beforeId } paginam o histórico (keyset). Sem opts
// = página mais recente (retrocompatível). `beforeId` (o _id da 1ª msg da página
// atual) carrega as anteriores; a resposta traz `has_more`.
export async function getContact(phone, markRead = true, channelId = null, opts = {}) {
  const params = [];
  if (!markRead) params.push('mark_read=false');
  if (channelId) params.push(`channel_id=${encodeURIComponent(channelId)}`);
  params.push(...windowParams(opts));
  const qs = params.length ? `?${params.join('&')}` : '';
  return request('GET', `/api/contacts/${encodeURIComponent(phone)}${qs}`);
}

// Âncoras da janela da thread (plano 99). São MUTUAMENTE EXCLUSIVAS no servidor
// (400 se combinadas), então este helper existe para que os dois endpoints de
// thread montem a query exatamente do mesmo jeito:
//   beforeId → as anteriores (scroll-up, o caminho de sempre)
//   afterId  → as seguintes (scroll-down numa janela ancorada no passado)
//   aroundId → a janela CENTRADA nessa mensagem ("pular para cá")
//   atTs     → epoch no fuso do NAVEGADOR; o servidor resolve o 1º id com ts >=
//              e já devolve a janela em torno dele, numa ida só.
function windowParams(opts = {}) {
  const p = [];
  if (opts.limit != null) p.push(`limit=${encodeURIComponent(opts.limit)}`);
  if (opts.beforeId != null) p.push(`before_id=${encodeURIComponent(opts.beforeId)}`);
  if (opts.afterId != null) p.push(`after_id=${encodeURIComponent(opts.afterId)}`);
  if (opts.aroundId != null) p.push(`around_id=${encodeURIComponent(opts.aroundId)}`);
  if (opts.atTs != null) p.push(`at_ts=${encodeURIComponent(opts.atTs)}`);
  return p;
}

// Atendimento-cêntrico (plano 11 D1): carrega a thread de Um atendimento (um canal),
// sem fundir os canais do mesmo número. Retorna {conversation, contact, messages,
// has_more, channel_id, avatar_v}. markRead=false não zera o badge daquela atendimento.
// `opts` (plano 50 F4): { limit, beforeId } — keyset scroll-up (ver getContact).
export async function getConversationMessages(convId, markRead = true, opts = {}) {
  const params = [];
  if (!markRead) params.push('mark_read=false');
  params.push(...windowParams(opts));
  const qs = params.length ? `?${params.join('&')}` : '';
  return request('GET', `/api/atendimentos/${convId}/messages${qs}`);
}

// Busca de texto DENTRO de uma conversa (plano 99 F1) — o "pesquisar mensagens"
// do WhatsApp. Distinta da busca global da sidebar (que devolve UM hit por
// contato): aqui vem a lista de ocorrências desta thread, mais recente primeiro.
// Retorna {matches: [{id, ts, role, snippet}], total}. Termo com menos de 3
// caracteres devolve lista vazia com 200 — não é erro.
export async function searchInConversation(convId, q, opts = {}) {
  const params = [`q=${encodeURIComponent(q || '')}`];
  if (opts.limit != null) params.push(`limit=${encodeURIComponent(opts.limit)}`);
  if (opts.offset != null) params.push(`offset=${encodeURIComponent(opts.offset)}`);
  return request('GET', `/api/atendimentos/${convId}/messages/search?${params.join('&')}`,
                 undefined, { signal: opts.signal || null });
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

// conversationId (plano 11 D1) roteia o envio pelo CANAL daquela atendimento via
// OutboundRouter; ausente cai no 'default' (GOWA), preservando o legado.
// channelId roteia o envio quando o atendimento AINDA não existe (1ª mensagem de uma
// atendimento novo iniciada pelo picker de caixa de entrada). Quando há conversationId,
// o backend ignora channelId e usa o canal do atendimento.
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

// Edit the text of an already-sent outgoing message (operator/AI). Requires msgId
// (the provider message id); text-only messages, within the provider's edit window.
export async function editMessage(phone, { msgId = null, dbId = null, text = '', conversationId = null } = {}) {
  const body = { msg_id: msgId, db_id: dbId, text };
  if (conversationId != null) body.conversation_id = conversationId;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/messages/edit`, body);
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
  // plano 37 (C1): ao INICIAR uma conversa nova num canal não-default (sem
  // conversation_id ainda), o channelId é a única pista do canal — sem ele a nota
  // e a rodada de IA misfilam pro WhatsApp 'default'. Espelha sendPrivateAudio.
  if (opts.channelId != null) body.channel_id = opts.channelId;
  // Menções (@atendente / @time) — colaboração estilo Chatwoot.
  if (Array.isArray(opts.mentions) && opts.mentions.length) body.mentions = opts.mentions;
  if (opts.mentionInbox) body.mention_inbox = true;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/private-message`, body);
}

// Contagem de menções não-lidas do usuário logado (badge da aba "Menções").
export async function getMentionsUnreadCount() {
  return request('GET', '/api/mentions/unread-count');
}

export async function markAsRead(phone) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/read`);
}

export async function markAsUnread(phone) {
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/unread`);
}

// Plano 49 — não-lida/lida POR CONVERSA (atendimento-cêntrico). Escopa a UMA conversa
// (canal) do número, ao contrário dos wrappers por-phone acima que acendem/limpam
// todas as conversas do contato. Usados pela sidebar quando a linha tem conversation_id;
// as versões por-phone ficam como fallback para linhas legadas sem atendimento.
export async function markConversationUnread(convId) {
  return request('POST', `/api/atendimentos/${convId}/unread`);
}

export async function markConversationRead(convId) {
  return request('POST', `/api/atendimentos/${convId}/read`);
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

export async function toggleContactAI(phone, enabled, opts = {}) {
  // plano 37 (B3/P2): num contato multicanal, informar a conversa/canal ancora o
  // card + o flip de ai_active NAQUELE canal — desligar a IA numa conversa não
  // reflete na outra. Sem os campos, o backend cai no comportamento legado.
  const body = { enabled };
  if (opts.conversationId != null) body.conversation_id = opts.conversationId;
  if (opts.channelId != null) body.channel_id = opts.channelId;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/toggle-ai`, body);
}

export async function getGroupMembers(groupJid, force = false) {
  const qs = force ? '?force=true' : '';
  return request('GET', `/api/contacts/${encodeURIComponent(groupJid)}/members${qs}`);
}

// Only append conversation_id/channel_id when present (the backend distinguishes
// absent from empty), so build the fields conditionally before uploadRequest.
function _scopeFields(conversationId, channelId) {
  const f = {};
  if (conversationId != null) f.conversation_id = String(conversationId);
  if (channelId != null) f.channel_id = String(channelId);
  return f;
}

export async function sendImage(phone, file, caption = '', conversationId = null, channelId = null) {
  return uploadRequest(`/api/contacts/${encodeURIComponent(phone)}/send-image`,
    { image: file, caption, ..._scopeFields(conversationId, channelId) });
}

export async function sendAudio(phone, blob, filename = 'voice.ogg', conversationId = null, channelId = null) {
  // Preserve the filename: uploadRequest names Blob parts by `.name` (default
  // 'file'), so wrap a bare Blob in a File carrying `filename`.
  const named = blob instanceof File ? blob : new File([blob], filename, { type: blob.type || 'audio/ogg' });
  return uploadRequest(`/api/contacts/${encodeURIComponent(phone)}/send-audio`,
    { audio: named, ..._scopeFields(conversationId, channelId) });
}

// Private audio note: stays in the panel (never sent to the contact). `aiRead`
// mirrors the "IA lê" toggle (transcribe + let the AI process it); `aiReply`
// (only meaningful when aiRead) picks chat reply vs. private note.
export async function sendPrivateAudio(phone, blob, filename = 'voice.ogg', opts = {}) {
  const named = blob instanceof File ? blob : new File([blob], filename, { type: blob.type || 'audio/ogg' });
  const fields = { audio: named };
  fields.ai_read = opts.aiRead ? 'true' : 'false';
  fields.ai_reply = (opts.aiReply === false) ? 'false' : 'true';
  if (opts.conversationId != null) fields.conversation_id = String(opts.conversationId);
  if (opts.channelId != null) fields.channel_id = String(opts.channelId);
  return uploadRequest(`/api/contacts/${encodeURIComponent(phone)}/private-audio`, fields);
}

export async function sendDocument(phone, file, caption = '', conversationId = null, channelId = null) {
  return uploadRequest(`/api/contacts/${encodeURIComponent(phone)}/send-document`,
    { document: file, caption, ..._scopeFields(conversationId, channelId) });
}

export async function sendVideo(phone, file, caption = '', conversationId = null, channelId = null) {
  return uploadRequest(`/api/contacts/${encodeURIComponent(phone)}/send-video`,
    { video: file, caption, ..._scopeFields(conversationId, channelId) });
}

// Anexos como NOTA PRIVADA (só no painel). Espelham sendPrivateAudio e aceitam
// as mesmas menções (@atendente / @time) via campos multipart.
function _mentionFields(opts = {}) {
  const f = {};
  if (Array.isArray(opts.mentions) && opts.mentions.length) f.mentions = JSON.stringify(opts.mentions);
  if (opts.mentionInbox) f.mention_inbox = 'true';
  return f;
}

export async function sendPrivateImage(phone, file, caption = '', opts = {}) {
  return uploadRequest(`/api/contacts/${encodeURIComponent(phone)}/private-image`,
    { image: file, caption, ..._scopeFields(opts.conversationId, opts.channelId), ..._mentionFields(opts) });
}

export async function sendPrivateDocument(phone, file, caption = '', opts = {}) {
  return uploadRequest(`/api/contacts/${encodeURIComponent(phone)}/private-document`,
    { document: file, caption, ..._scopeFields(opts.conversationId, opts.channelId), ..._mentionFields(opts) });
}

export async function sendPresence(phone, action = 'start', conversationId = null,
                                   channelId = null) {
  const body = { action };
  if (conversationId != null) body.conversation_id = conversationId;
  // plano 37 (C2): "digitando…" no canal que o operador compõe — sem channelId a
  // conversa nova de canal não-default emitiria presence no WhatsApp 'default'.
  if (channelId != null) body.channel_id = channelId;
  return request('POST', `/api/contacts/${encodeURIComponent(phone)}/presence`, body);
}

// create=false apenas valida o número sem materializar o contato (usado pela
// verificação ao vivo do modal "Novo atendimento" — o contato só nasce no envio).
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

// `reqOpts` (plano 62 F3): { signal } opcional para cancelamento via AbortController
// (um abort rejeita com AbortError — caller engole). Callers antigos não mudam.
export async function listConversations(params = {}, reqOpts = {}) {
  const clean = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    clean[k] = v;
  }
  const qs = new URLSearchParams(clean).toString();
  return request('GET', `/api/atendimentos${qs ? '?' + qs : ''}`, undefined, reqOpts);
}

export async function getConversation(id) {
  return request('GET', `/api/atendimentos/${id}`);
}

// ── Saved conversation filters (presets nomeados, por usuário) ──────
// Each operator can name and persist one or more inbox filter presets. `spec`
// is the full filter snapshot ({statusFilter, assignmentTab, sortBy, tagFilter,
// advFilters}). Scoped server-side to the logged-in user (shared in legacy mode).

export async function listSavedFilters() {
  return request('GET', '/api/me/conversation-filters');
}

export async function createSavedFilter(name, spec) {
  return request('POST', '/api/me/conversation-filters', { name, spec });
}

export async function updateSavedFilter(id, patch) {
  return request('PUT', `/api/me/conversation-filters/${id}`, patch);
}

export async function deleteSavedFilter(id) {
  return request('DELETE', `/api/me/conversation-filters/${id}`);
}

// Filter conversations via the flat-param GET endpoint. Builds the querystring
// from `params`, dropping empty values and URL-encoding keys (cattr:<key>) and
// values correctly. AND between distinct params; a comma-separated value (e.g.
// labels=vip,lead) is OR within that dimension.
export async function filterConversations(params = {}, reqOpts = {}) {
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
  // `reqOpts` (plano 69 F2): { signal } opcional — a sidebar conversa-first agora
  // pode ser servida por este endpoint e precisa cancelar o fetch anterior.
  return request('GET', `/api/atendimentos/filter${qs ? '?' + qs : ''}`, undefined, reqOpts);
}

export async function countConversations(params = {}) {
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
  return request('GET', `/api/atendimentos/count${qs ? '?' + qs : ''}`);
}

export async function setConversationStatus(id, status) {
  return request('POST', `/api/atendimentos/${id}/status`, { status });
}

export async function assignConversation(id, assigneeUserId) {
  return request('POST', `/api/atendimentos/${id}/assign`, {
    assignee_user_id: assigneeUserId == null ? null : assigneeUserId,
  });
}

export async function archiveConversation(id, archived) {
  return request('POST', `/api/atendimentos/${id}/archive`, { archived });
}

// Fixar/desafixar uma conversa no topo da sidebar (plano 54 — por atendimento).
export async function pinConversation(id, pinned) {
  return request('POST', `/api/atendimentos/${id}/pin`, { pinned });
}

// Hard-delete a single conversation/thread (plano 16). Keeps the contact and its
// other conversations; only this thread + its messages are removed.
export async function deleteConversation(id) {
  return request('DELETE', `/api/atendimentos/${id}`);
}

// Assume the conversation for the current user (plano 10 Onda 0). No body — the
// server resolves "me" from the authenticated session.
export async function assignMeConversation(id) {
  return request('POST', `/api/atendimentos/${id}/assign-me`, {});
}

// Toggle the conversation-level AI (ai_active) — distinct from the contact-level
// toggle. Drives the chat-header IA switch (FF3).
export async function setConversationAi(id, active) {
  return request('POST', `/api/atendimentos/${id}/ai`, { active });
}

// Resolve the conversation for a contact by phone (feeds the chat header and the
// sidebar right-click menu). With { includeClosed: true } the server returns the
// latest conversation regardless of status, so a resolved thread still resolves
// (lets the menu show its assignee and a "reopen" action).
export async function getContactConversation(phone, { includeClosed = false } = {}) {
  const qs = includeClosed ? '?include_closed=true' : '';
  return request('GET', `/api/contacts/${encodeURIComponent(phone)}/atendimento${qs}`);
}

// Agents that can take a conversation (plano 10): {users:[...], ai_agents:[...]}.
// Gated by conversation.read so attendants (not only admins) can transfer.
export async function getAssignableAgents() {
  return request('GET', '/api/atendimentos/assignable-agents');
}

// Unified assignment (plano 10): route a conversation to a human or an AI agent.
// kind: 'user' (needs userId), 'ai' (needs agentKey) or 'none' (unassign).
export async function assignAgent(id, { kind, userId = null, agentKey = null } = {}) {
  const body = { kind };
  if (kind === 'user') body.user_id = userId;
  if (kind === 'ai') body.agent_key = agentKey;
  return request('POST', `/api/atendimentos/${id}/assign-agent`, body);
}

// Update a conversation's custom_attributes (FF5). Server validates keys against
// the conversation attribute definitions.
export async function updateConversationInfo(id, body) {
  return request('PUT', `/api/atendimentos/${id}/info`, body);
}

// ── Conversation labels (Onda 3) ──────────────────────────────────
// Etiquetas próprias do atendimento (registro global + atribuição por atendimento),
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
  return request('GET', `/api/atendimentos/${convId}/labels`);
}

// Replace a conversation's labels (snapshot of names).
export async function updateConversationLabels(convId, labels) {
  return request('PUT', `/api/atendimentos/${convId}/labels`, { labels });
}

// ── Templates (Cloud API, Frente C) ───────────────────────────────
// Channel-aware: returns {supported, templates}. Only channels with the
// `templates` capability (WhatsApp Cloud) return supported=true.
export async function getConversationTemplates(convId) {
  return request('GET', `/api/atendimentos/${convId}/templates`);
}

// body: {template_name, language?, components?, preview_text?}
export async function sendConversationTemplate(convId, payload) {
  return request('POST', `/api/atendimentos/${convId}/send-template`, payload);
}

// Create a template (WhatsApp Cloud) — gated by template.create.
// body: {name, category?, language?, body_text, header_text?, footer_text?,
//        body_examples?, header_examples?, header_format?, header_handle?,
//        buttons?}  (plano 73 — cabeçalho de mídia + botões)
export async function createConversationTemplate(convId, payload) {
  return request('POST', `/api/atendimentos/${convId}/templates`, payload);
}

// Sobe um arquivo de exemplo do cabeçalho de mídia → {handle} (plano 73).
// O handle volta como `header_handle` na criação do template.
export async function uploadConversationTemplateExample(convId, file) {
  return uploadRequest(`/api/atendimentos/${convId}/templates/upload-example`, { file });
}

// Delete a template (all languages) by name — gated by template.delete.
export async function deleteConversationTemplate(convId, name) {
  return request('DELETE', `/api/atendimentos/${convId}/templates/${encodeURIComponent(name)}`);
}

// ── Templates / janela 24h ao iniciar atendimento (plano 21) ─────────────
// Versões CHANNEL-scoped (sem atendimento ainda): a "Novo atendimento" precisa saber a
// janela e listar/enviar templates antes que o atendimento exista.

// Estado da janela de 24h para iniciar um atendimento em `channelId` com `phone`.
// Retorna {templates_supported, session_open, has_conversation, conversation_id, last_inbound_ts}.
export async function getChannelSessionState(channelId, phone) {
  return request('GET', `/api/channels/${encodeURIComponent(channelId)}/session-state?phone=${encodeURIComponent(phone)}`);
}

// Lista os templates de um canal (mesmo shape de getConversationTemplates).
export async function getChannelTemplates(channelId) {
  return request('GET', `/api/channels/${encodeURIComponent(channelId)}/templates`);
}

// Envia um template aprovado para `phone` via `channelId` (cria o atendimento).
// body: {phone, template_name, language?, components?, preview_text?}
export async function sendChannelTemplate(channelId, payload) {
  return request('POST', `/api/channels/${encodeURIComponent(channelId)}/send-template`, payload);
}

// Cria um template no canal — gated por template.create. Mesmo payload da versão
// conv-scoped (inclui header_format/header_handle/buttons — plano 73).
export async function createChannelTemplate(channelId, payload) {
  return request('POST', `/api/channels/${encodeURIComponent(channelId)}/templates`, payload);
}

// Sobe um arquivo de exemplo do cabeçalho de mídia no canal → {handle}.
export async function uploadChannelTemplateExample(channelId, file) {
  return uploadRequest(`/api/channels/${encodeURIComponent(channelId)}/templates/upload-example`, { file });
}

// Apaga um template (todas as línguas) no canal — gated por template.delete.
export async function deleteChannelTemplate(channelId, name) {
  return request('DELETE', `/api/channels/${encodeURIComponent(channelId)}/templates/${encodeURIComponent(name)}`);
}

// ── Channels (plano 02 Fase 2) ────────────────────────────────────
// Messaging channels — each provider ships as a plugin (plano 33).
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
// para iniciar um atendimento novo. Mais leve e com menos privilégio que listChannels
// (gated por conversation.reply, sem credenciais). Itens: {id, provider, display_name, own_phone}.
export async function listConnectedChannels() {
  return request('GET', '/api/channels/connected');
}

// TODOS os canais (id/provider/display_name) para as opções do filtro "Canais"
// do hub de atendimentos (plano 59). Baixo privilégio (conversation.reply, sem
// credenciais) e mais amplo que /connected (inclui desabilitados + arquivados) —
// as opções do filtro não podem depender das conversas carregadas na sidebar.
export async function listChannelsForFilter() {
  return request('GET', '/api/channels/for-filter');
}

export async function getChannel(id) {
  return request('GET', `/api/channels/${encodeURIComponent(id)}`);
}

// body: {id, provider, display_name, config?, credentials?:{key:value}}
export async function createChannel(payload) {
  return request('POST', '/api/channels', payload);
}
// Generic post-create action (plano 33): a provider descriptor may declare a
// `post_create.autoconfigure` endpoint the core POSTs after creating the channel
// (e.g. Telegram detects a public domain → webhook, else long-poll). The core
// doesn't know the provider — it just POSTs {channel_id} to the declared endpoint.
export async function providerPostCreateAction(endpoint, channelId) {
  return request('POST', endpoint, { channel_id: channelId });
}
// (plano 76 · V11) telegramAutoconfigure/telegramChannelStatus saíram do core: o
// autoconfigure é coberto pelo genérico providerPostCreateAction; o status do
// canal é consumido só pela screen do plugin telegram, que já usa seu próprio
// apiFetch namespaceado (/api/plugins/telegram/status).

// (plano 76 · V7) As funções cloudWebhookStatus/cloudSetWebhook/cloudDeleteWebhook
// saíram do core: o WebhookHealthRow virou componente do plugin whatsapp_cloud e
// fala com os próprios endpoints via o `http` de buildPluginHttp. O core não
// chama mais endpoint de plugin daqui.

// body: {display_name?, enabled?, config?, credentials?:{key:value}}
export async function updateChannel(id, payload) {
  return request('PUT', `/api/channels/${encodeURIComponent(id)}`, payload);
}

// Soft-delete (arquivar) por padrão; `{ purge: true }` faz hard-delete:
// apaga o canal e a inbox de vez (não é restaurável).
export async function deleteChannel(id, { purge = false } = {}) {
  const qs = purge ? '?purge=true' : '';
  return request('DELETE', `/api/channels/${encodeURIComponent(id)}${qs}`);
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

// plano 50 F13 — status de VÁRIOS canais numa request: {status_by_id:{id:status}}.
// Substitui o fan-out de 1 GET por canal na tela Canais.
export async function getChannelStatusBatch(ids) {
  return request('POST', '/api/channels/status-batch', { ids });
}

// Reconnect a GOWA channel's device socket (plano 27) — acts on the right
// device. Distinct from the legacy singleton reconnect()/logout() above.
export async function channelReconnect(id) {
  return request('POST', `/api/channels/${encodeURIComponent(id)}/reconnect`);
}

// Log a GOWA channel's device out of WhatsApp (plano 27) — clears the session
// so the next connect asks for a fresh QR.
export async function channelLogout(id) {
  return request('POST', `/api/channels/${encodeURIComponent(id)}/logout`);
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
  // Drop empty/null params so a blank filter (e.g. conversation_id="") is omitted
  // instead of sent as ""=422 against the typed endpoint (plano 36 F4).
  const clean = {};
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') clean[k] = v;
  }
  const qs = new URLSearchParams(clean).toString();
  return request('GET', `/api/executions${qs ? '?' + qs : ''}`);
}

export async function getExecution(id) {
  return request('GET', `/api/executions/${id}`);
}

// Nexus-style stat cards / cost panel / filter pills.
export async function getExecutionStats(params = {}) {
  const clean = {};
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') clean[k] = v;
  }
  const qs = new URLSearchParams(clean).toString();
  return request('GET', `/api/executions/stats${qs ? '?' + qs : ''}`);
}

export async function getExecutionCost(params = {}) {
  const clean = {};
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') clean[k] = v;
  }
  const qs = new URLSearchParams(clean).toString();
  return request('GET', `/api/executions/cost${qs ? '?' + qs : ''}`);
}

export async function getExecutionModels() {
  return request('GET', '/api/executions/models');
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

// body: {display_name, prompt(string), model_config(obj), tool_names(list|null),
//        enabled(bool), description, is_router(bool), routing_targets(list|null)}
// `prompt` is the agent's inline system prompt (free text, per-agent — not reusable).
export async function saveAgent(key, data) {
  return request('PUT', `/api/ai/agents/${encodeURIComponent(key)}`, data);
}

// Patch only an agent's inline prompt, preserving its other fields. Used by the
// onboarding wizard (which knows the prompt but not the full agent payload).
export async function saveAgentPrompt(key, prompt) {
  return request('PUT', `/api/ai/agents/${encodeURIComponent(key)}/prompt`, { prompt });
}

export async function getAgentHistory(key) {
  return request('GET', `/api/ai/agents/${encodeURIComponent(key)}/history`);
}

export async function rollbackAgent(key, version) {
  return request('POST', `/api/ai/agents/${encodeURIComponent(key)}/rollback/${version}`);
}

// ── Dedicated prompt version trail (git-like). Separate from the whole-agent
// history above. Diff is computed server-side from full snapshots.
export async function getAgentPromptHistory(key) {
  return request('GET', `/api/ai/agents/${encodeURIComponent(key)}/prompt/history`);
}

export async function getAgentPromptVersion(key, version) {
  return request('GET', `/api/ai/agents/${encodeURIComponent(key)}/prompt/history/${version}`);
}

export async function getAgentPromptDiff(key, fromV, toV) {
  const qs = new URLSearchParams({ from: fromV, to: toV }).toString();
  return request('GET', `/api/ai/agents/${encodeURIComponent(key)}/prompt/diff?${qs}`);
}

export async function restoreAgentPrompt(key, version) {
  return request('POST', `/api/ai/agents/${encodeURIComponent(key)}/prompt/restore/${version}`);
}

export async function renameAgentPromptVersion(key, version, note) {
  return request('PATCH', `/api/ai/agents/${encodeURIComponent(key)}/prompt/history/${version}`, { note });
}

export async function deleteAgentPromptVersion(key, version) {
  return request('DELETE', `/api/ai/agents/${encodeURIComponent(key)}/prompt/history/${version}`);
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

// RBAC user login ({email, password}) — the legacy single-password was retired
// (plano 48). Both fields are required server-side.
export async function login(password, email = '') {
  return request('POST', '/api/auth/login', { email: email.trim(), password });
}

// Create the first admin user (only works while no users exist).
export async function bootstrapAdmin(data) {
  return request('POST', '/api/auth/bootstrap', data);
}

// Current identity for the bearer token (RBAC user session).
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

// `opts.silent` suprime o toast "Permissão negada." quando o chamador é um read
// best-effort de fundo (ex.: tela de Conversas popular a lista de "Transferir"
// para quem não tem `users.manage`). A tela de Usuários chama sem `silent`.
// ── Chaves de API (plano "Sistema de API com chave por usuário") ───────────
// O SEGREDO só existe na resposta de createApiKey — não há endpoint que o leia
// de volta (o banco guarda apenas o hash Argon2).

export async function getApiKeys(opts) {
  return request('GET', '/api/api-keys', undefined, opts);
}

export async function createApiKey(data) {
  return request('POST', '/api/api-keys', data, { silent: true });
}

export async function revokeApiKey(id) {
  return request('DELETE', `/api/api-keys/${id}`);
}

// ── Webhooks de SAÍDA (fase 8) ────────────────────────────────────────────
// ⚠️ Não confundir com o webhook de ENTRADA (`/api/webhook/...`), que é o
// provedor nos chamando. Aqui é o contrário: nós chamando o integrador.

export async function getWebhooks(opts) {
  return request('GET', '/api/webhooks', undefined, opts);
}

export async function createWebhook(data) {
  return request('POST', '/api/webhooks', data, { silent: true });
}

export async function updateWebhook(id, data) {
  return request('PUT', `/api/webhooks/${id}`, data, { silent: true });
}

export async function testWebhook(id) {
  return request('POST', `/api/webhooks/${id}/test`, undefined, { silent: true });
}

export async function rotateWebhookSecret(id) {
  return request('POST', `/api/webhooks/${id}/rotate-secret`);
}

export async function deleteWebhook(id) {
  return request('DELETE', `/api/webhooks/${id}`);
}

export async function getWebhookDeliveries(id, opts) {
  return request('GET', `/api/webhooks/${id}/deliveries`, undefined, opts);
}

export async function getUsers(opts) {
  return request('GET', '/api/users', undefined, opts);
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

// Self-service (plano 47): the logged-in RBAC user changes their OWN password.
// Requires the current password; a wrong one returns 400 (not 401) so it doesn't
// trigger the global logout branch.
export async function changeMyPassword(currentPassword, newPassword) {
  return request('POST', '/api/me/password', {
    current_password: currentPassword,
    new_password: newPassword,
  });
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
