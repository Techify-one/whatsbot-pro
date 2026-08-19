// @ts-check
//
// Pure conversation-row patch logic shared between the conversation hub
// (Contacts.js) and the attendances flow (Plano 23 · R13).
//
// When a `conversation_*` WebSocket event arrives (assign / resolve / IA toggle
// / active-agent change / custom-attribute write), the sidebar must patch the
// affected row(s) in place. This was inline in Contacts.js; extracted here so
// the same mapping (event payload → row patch + targeting rule) can be reused
// by the attendances plugin instead of being re-derived.
//
// PURE: no DOM, no network, no module state. The caller owns React state and
// the refetch/membership side-effects; these helpers only compute.

/**
 * @typedef {Object} ConversationRow
 * @property {number|null} [conversation_id]
 * @property {number} [id] - contact id (legacy contact-only rows match by this).
 * @property {string} [conv_status]
 * @property {number|null} [assignee_user_id]
 * @property {string|null} [active_agent_key]
 * @property {boolean} [conv_ai_active]
 * @property {string[]} [conv_labels] - etiquetas da CONVERSA (não as tags do contato).
 */

/**
 * @typedef {Object} ConversationEvent
 * @property {number} [contact_id]
 * @property {number} [conversation_id]
 * @property {string} [status]
 * @property {number|null} [assignee_user_id]
 * @property {string|null} [active_agent_key]
 * @property {boolean} [ai_active]
 * @property {string[]} [labels] - snapshot de `conversation_labels_changed`.
 * @property {{ custom_attributes?: any }} [fields] - e.g. `{ custom_attributes: {...} }`.
 */

/**
 * Whether a conversation event targets a given row.
 *
 * Atendimento-cêntrico: a contact can own several rows (one per channel). Rows that
 * already have a `conversation_id` match by it (so assigning/resolving one
 * channel doesn't touch the others). A legacy contact-only row (no
 * conversation yet) still matches by `contact_id` and will adopt the new one.
 *
 * @param {ConversationRow} row
 * @param {ConversationEvent} ev
 * @returns {boolean}
 */
export function eventTargetsRow(row, ev) {
  if (!row || !ev) return false;
  const convId = ev.conversation_id;
  const cid = ev.contact_id;
  return (convId != null && row.conversation_id === convId)
    || (row.conversation_id == null && cid != null && row.id === cid);
}

/**
 * Build the partial patch for a row from a conversation event. Only keys present
 * in the event are written (so an assign event won't clobber status, etc.).
 * Returns an empty object when there is nothing to patch.
 *
 * @param {ConversationRow} row - the row being patched (for the adopt-id rule).
 * @param {ConversationEvent} ev
 * @returns {Partial<ConversationRow>}
 */
export function conversationPatch(row, ev) {
  /** @type {Record<string, any>} */
  const patch = {};
  if (!ev) return patch;
  if (ev.status !== undefined) patch.conv_status = ev.status;
  if (ev.assignee_user_id !== undefined) patch.assignee_user_id = ev.assignee_user_id;
  if (ev.active_agent_key !== undefined) patch.active_agent_key = ev.active_agent_key;
  if (ev.ai_active !== undefined) patch.conv_ai_active = ev.ai_active;
  // `conversation_labels_changed` carries the conversation's label snapshot as
  // `labels`; the sidebar row calls it `conv_labels` (o campo que os chips leem).
  if (ev.labels !== undefined) patch.conv_labels = ev.labels;
  // A legacy contact-only row adopts the conversation id from the event.
  if (ev.conversation_id != null && row && row.conversation_id == null) {
    patch.conversation_id = ev.conversation_id;
  }
  return patch;
}

/**
 * Apply a conversation event across a list of rows. The targeted row(s) are patched
 * (others returned unchanged by reference); quando NENHUMA linha muda, devolve o
 * MESMO array (plano 130 · F2) — o `rows.map` cru trocava a identidade da lista em
 * todo evento, re-renderizando a sidebar inteira e re-disparando o efeito da
 * contagem das abas. This is the single-source mapping used by the sidebar's
 * `setContacts(prev => …)`.
 *
 * @param {ConversationRow[]} rows
 * @param {ConversationEvent} ev
 * @returns {ConversationRow[]}
 */
export function applyConversationEvent(rows, ev) {
  if (!Array.isArray(rows)) return rows;
  let changed = false;
  const next = rows.map(row => {
    if (!eventTargetsRow(row, ev)) return row;
    const patch = conversationPatch(row, ev);
    // Só conta como mudança se algum VALOR difere: um evento que reafirma o estado
    // atual (o mesmo assignee, o mesmo status) não pode trocar a identidade da linha.
    // `conv_labels` é array, então compara por referência — o lado seguro do erro.
    if (!Object.keys(patch).some(k => row[k] !== patch[k])) return row;
    changed = true;
    return { ...row, ...patch };
  });
  return changed ? next : rows;
}

/**
 * Whether `ev` is a conversation-scope custom-attribute write (forwarded to the
 * open ConversationInfoPanel for a live refresh).
 *
 * @param {ConversationEvent} ev
 * @returns {boolean}
 */
export function isConversationAttributeWrite(ev) {
  return !!(ev && ev.conversation_id != null && ev.fields
    && ev.fields.custom_attributes !== undefined);
}
