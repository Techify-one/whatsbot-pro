// @ts-check
//
// Pure conversation-row + filter logic for the conversation hub (Plano 23 · D2).
//
// Extracted verbatim from the `Contacts.js` god-component so the row-building
// (cross of contacts × conversations into one row per channel) and the
// client-side filter/search clause matching can be unit-tested with `node --test`
// and reused by the attendances flow — instead of living inline in a 1800-line
// component. The behavior is IDENTICAL to the inline originals.
//
// PURE: no preact import, no DOM, no network, no module state.

// ── Conversation tab/filter helpers (plano 10 FF2) ──────────────────
// All client-side over the enriched contact list (each row carries its active
// conversation's status/assignee/agente), so switching tabs is instant.

/**
 * A conversation row is "unassigned" when it has neither a human assignee nor an
 * active AI agent.
 * @param {{ assignee_user_id?: number|null, active_agent_key?: string|null }} c
 * @returns {boolean}
 */
export const isUnassigned = (c) => c.assignee_user_id == null && !c.active_agent_key;

/**
 * Whether a row matches the status chip ('open' | 'closed' | 'all').
 * @param {{ conv_status?: string }} c
 * @param {string} statusFilter
 * @returns {boolean}
 */
export function matchesStatus(c, statusFilter) {
  if (statusFilter === 'all') return true;
  return (c.conv_status || 'open') === statusFilter;   // 'open' | 'closed'
}

/**
 * Whether a row matches the assignment tab ('all' | 'mine' | 'unassigned').
 * @param {{ assignee_user_id?: number|null, active_agent_key?: string|null }} c
 * @param {string} tab
 * @param {number|null} uid - the current user's id (for 'mine').
 * @returns {boolean}
 */
export function matchesAssignment(c, tab, uid) {
  if (tab === 'mine') return uid != null && c.assignee_user_id === uid;
  if (tab === 'unassigned') return isUnassigned(c);
  return true;  // 'all'
}

// ── Filtros avançados (Chatwoot-style: Canais / Agente / Etiqueta / Última atividade) ──
// Cláusula: { dim, op, value }. Avaliadas client-side em AND sobre as linhas já
// carregadas. Cada linha carrega channel_id, assignee_user_id, active_agent_key,
// tags e last_message_ts — tudo o que essas dimensões precisam.
export const DAY_SECONDS = 86400;

/**
 * Whether a single (scalar) channel/tag/agent value matches a row. The clause
 * value may be a list (multi-select) — `clauseMatches` ORs this over the list.
 * @param {Record<string, any>} c - the conversation row.
 * @param {string} dim - 'channel' | 'tag' | 'agent'.
 * @param {string} value - one scalar selection.
 * @returns {boolean}
 */
function matchOne(c, dim, value) {
  if (dim === 'channel') return (c.channel_id || 'default') === value;
  if (dim === 'tag') return (c.tags || []).includes(value);           // etiqueta do CONTATO
  if (dim === 'conv_label') return (c.conv_labels || []).includes(value); // etiqueta da CONVERSA
  // agent
  if (value === 'none') return c.assignee_user_id == null && !c.active_agent_key;
  if (value.startsWith('user:')) return String(c.assignee_user_id) === value.slice(5);
  if (value.startsWith('ai:')) return (c.active_agent_key || '') === value.slice(3);
  return false;
}

/**
 * Evaluate a custom-attribute clause (dim `cattr:<scope>:<key>`) against a row.
 * Reads the attribute value from the contact bag (`custom_attributes`) or the
 * conversation bag (`conv_custom_attributes`) per scope, then compares per op.
 * @param {Record<string, any>} c - the conversation row.
 * @param {string} scope - 'contact' | 'conversation'.
 * @param {string} key - the attribute_key.
 * @param {string} op - eq|ne|contains|not_contains|gt|lt.
 * @param {any} value - the clause value (list for multi-select, else scalar).
 * @returns {boolean}
 */
function attrMatches(c, scope, key, op, value) {
  const bag = scope === 'contact' ? (c.custom_attributes || {}) : (c.conv_custom_attributes || {});
  const raw = bag[key];
  if (op === 'eq' || op === 'ne') {
    // list value (multi-select) → "é uma de" / "não é nenhuma"; escalar → igualdade.
    const list = Array.isArray(value) ? value : [value];
    const hit = list.some(v => String(raw) === String(v));
    return op === 'ne' ? !hit : hit;
  }
  if (op === 'contains' || op === 'not_contains') {
    const has = String(raw ?? '').toLowerCase().includes(String(value).toLowerCase());
    return op === 'not_contains' ? !has : has;
  }
  if (op === 'gt' || op === 'lt') {
    // número ou data: Number() é estrito (NaN p/ "2026-06-29"), então datas caem
    // no Date.parse — diferente de parseFloat, que parsearia o "2026" inicial.
    let a = Number(raw), b = Number(value);
    if (!Number.isFinite(a) || !Number.isFinite(b)) {
      a = Date.parse(raw); b = Date.parse(value);
    }
    if (!Number.isFinite(a) || !Number.isFinite(b)) return true;  // incomparável → não restringe
    return op === 'gt' ? a > b : a < b;
  }
  return true;
}

/**
 * Evaluate a single advanced-filter clause against a row.
 * @param {Record<string, any>} c - the conversation row.
 * @param {{ dim: string, op: string, value: any }} cl - the clause.
 * @param {number} now - current time in unix SECONDS (for the activity dimension).
 * @returns {boolean}
 */
export function clauseMatches(c, cl, now) {
  const { dim, op } = cl;
  const value = cl.value;
  if (value === '' || value == null) return true;   // cláusula incompleta → ignorada
  if (Array.isArray(value) && value.length === 0) return true;   // multi-select vazio → ignorada
  // Atributo personalizado dinâmico: cattr:<scope>:<key>.
  const cattr = typeof dim === 'string' && dim.match(/^cattr:(contact|conversation):(.+)$/);
  if (cattr) return attrMatches(c, cattr[1], cattr[2], op, value);
  // channel / tag / conv_label / agent — valor pode ser lista (multi-select). eq = "é
  // uma de" (OR); ne = "não é nenhuma de". Escalar legado é tratado como lista de 1.
  if (dim === 'channel' || dim === 'tag' || dim === 'conv_label' || dim === 'agent') {
    const list = Array.isArray(value) ? value : [value];
    if (list.length === 0) return true;             // cláusula incompleta → ignorada
    const hit = list.some(v => matchOne(c, dim, v));
    return op === 'ne' ? !hit : hit;
  }
  if (dim === 'status') {
    if (value === 'all') return true;                // "Todas" → não restringe
    const st = c.conv_status || 'open';              // 'open' | 'closed'
    return op === 'ne' ? st !== value : st === value;
  }
  if (dim === 'activity') {
    const n = parseFloat(value);
    if (!Number.isFinite(n)) return true;
    const ts = c.last_message_ts || c.updated_at || 0;
    const ageDays = ts ? (now - ts) / DAY_SECONDS : Infinity;  // sem atividade → "muito antigo"
    if (op === 'gt') return ageDays > n;                       // há MAIS de N dias
    if (op === 'lt') return ageDays < n;                       // há MENOS de N dias
    if (op === 'days_before') return Math.floor(ageDays) === Math.floor(n);  // há exatamente N dias
    return true;
  }
  return true;
}

/**
 * Whether a row matches every advanced-filter clause (AND).
 * @param {Record<string, any>} c
 * @param {Array<{ dim: string, op: string, value: any }>} advFilters
 * @param {number} now - unix SECONDS.
 * @returns {boolean}
 */
export function matchesAdvFilters(c, advFilters, now) {
  if (!advFilters || advFilters.length === 0) return true;
  return advFilters.every(cl => clauseMatches(c, cl, now));
}

/**
 * Filtro simples (funil da esquerda) — etiquetas do contato, "é uma de" (OR).
 * @param {{ tags?: string[] }} c
 * @param {string[]} tagFilter
 * @returns {boolean}
 */
export function matchesTags(c, tagFilter) {
  if (!tagFilter || tagFilter.length === 0) return true;
  const ctags = c.tags || [];
  return tagFilter.some(t => ctags.includes(t));
}

// ── Saved-filter spec helpers ──────────────────────────────────────
// A filter preset is the full snapshot {statusFilter, sortBy, tagFilter,
// advFilters}. `normalizeSpec` drops the ephemeral clause ids and sorts
// tags/clauses so two equivalent filters compare equal regardless of the order
// they were built in. The assignment tab (Minhas / Não atribuídas / Todas) is
// NOT part of a filter — it's a standalone view selector, so it never counts
// toward "filtro ativo" nor is saved into a preset.
export const DEFAULT_SPEC = { statusFilter: 'open', sortBy: 'activity', tagFilter: [], advFilters: [] };

/**
 * Canonicalize a filter spec (drop clause ids, sort tags/clauses) so two
 * equivalent filters serialize identically.
 * @param {Record<string, any>} spec
 * @returns {{ statusFilter: string, sortBy: string, tagFilter: string[], advFilters: Array<{dim:string,op:string,value:string}> }}
 */
export function normalizeSpec(spec) {
  const s = spec || {};
  const tags = [...(s.tagFilter || [])].sort();
  // value pode ser lista (multi-select) ou escalar (status/days, presets legados).
  // Listas são ordenadas para que dois filtros equivalentes serializem igual.
  const normVal = (v) => (Array.isArray(v) ? [...v].map(String).sort() : String(v ?? ''));
  const adv = (s.advFilters || [])
    .map(f => ({ dim: f.dim, op: f.op, value: normVal(f.value) }))
    .sort((a, b) => (a.dim + a.op + JSON.stringify(a.value))
      .localeCompare(b.dim + b.op + JSON.stringify(b.value)));
  return {
    statusFilter: s.statusFilter || 'open',
    sortBy: s.sortBy || 'activity',
    tagFilter: tags,
    advFilters: adv,
  };
}

/**
 * Whether two filter specs are equivalent after normalization.
 * @param {Record<string, any>} a
 * @param {Record<string, any>} b
 * @returns {boolean}
 */
export function specsEqual(a, b) {
  return JSON.stringify(normalizeSpec(a)) === JSON.stringify(normalizeSpec(b));
}

/**
 * Whether a spec equals the defaults (no filter active).
 * @param {Record<string, any>} spec
 * @returns {boolean}
 */
export function isDefaultSpec(spec) { return specsEqual(spec, DEFAULT_SPEC); }

/**
 * Sort a row list by the chosen key. 'activity' pins-first then most-recent
 * (matches the backend); 'oldest' ascending; 'unread' by total unread desc.
 * Returns a NEW array (never mutates the input).
 * @param {Record<string, any>[]} list
 * @param {string} sortBy - 'activity' | 'oldest' | 'unread'
 * @returns {Record<string, any>[]}
 */
export function sortContactsBy(list, sortBy) {
  const arr = [...list];
  const ts = (c) => c.last_message_ts || c.updated_at || 0;
  if (sortBy === 'oldest') {
    arr.sort((a, b) => ts(a) - ts(b));
  } else if (sortBy === 'unread') {
    arr.sort((a, b) => {
      const au = (a.unread_count || 0) + (a.unread_ai_count || 0);
      const bu = (b.unread_count || 0) + (b.unread_ai_count || 0);
      if (au !== bu) return bu - au;
      return ts(b) - ts(a);
    });
  } else {  // 'activity' — pinned first, then most recent (matches the backend)
    arr.sort((a, b) => {
      const ap = a.is_pinned ? 1 : 0, bp = b.is_pinned ? 1 : 0;
      if (ap !== bp) return bp - ap;
      return ts(b) - ts(a);
    });
  }
  return arr;
}

/**
 * Re-sort like the backend default: pinned first, then by last message time desc.
 * (This is `sortContactsBy(list, 'activity')`, kept as a named export because the
 * sidebar's optimistic patches call it directly.)
 * @param {Record<string, any>[]} list
 * @returns {Record<string, any>[]}
 */
export function sortContacts(list) {
  return [...list].sort((a, b) => {
    const ap = a.is_pinned ? 1 : 0;
    const bp = b.is_pinned ? 1 : 0;
    if (ap !== bp) return bp - ap;
    return (b.last_message_ts || b.updated_at || 0) - (a.last_message_ts || a.updated_at || 0);
  });
}

// ── Conversa-cêntrico (plano 11 D1) ──────────────────────────────
// Cada linha da sidebar é uma CONVERSA (uma por canal), não um contato. Um número
// presente em 2 canais vira 2 linhas distintas — em vez de fundir tudo numa só.
// Construímos as linhas cruzando os contatos (riqueza: tags/avatar/IA/nome) com as
// conversas (canal + preview e não-lidas POR CONVERSA). A identidade da linha é a
// `conversation_id`; contatos sem conversa ainda aparecem como linha única (phone).

/**
 * Cross the contact list with the conversation list into sidebar rows: one row
 * per conversation (per channel), plus a single legacy phone row for contacts
 * with no conversation yet. Per-conversation preview/unread override the
 * contact-level aggregates.
 * @param {Record<string, any>[]} contacts
 * @param {Record<string, any>[]} conversations
 * @returns {Record<string, any>[]}
 */
export function buildRows(contacts, conversations) {
  const byContact = new Map();
  for (const cv of conversations) {
    if (cv.contact_id == null) continue;
    if (!byContact.has(cv.contact_id)) byContact.set(cv.contact_id, []);
    byContact.get(cv.contact_id).push(cv);
  }
  const rows = [];
  for (const c of contacts) {
    const convs = byContact.get(c.id) || [];
    if (convs.length === 0) {
      // Contato sem conversa (ex: recém-iniciado pelo "Nova conversa") — linha única
      // que cai no caminho legado por telefone (channel 'default').
      rows.push({
        ...c, contact_id: c.id, conversation_id: null,
        channel_id: 'default', channel_provider: null, channel_name: null,
        conv_custom_attributes: {}, conv_labels: [],
      });
    } else {
      for (const cv of convs) {
        rows.push({
          ...c,
          contact_id: c.id,
          conversation_id: cv.id,
          channel_id: cv.channel_id || 'default',
          channel_provider: cv.channel_provider || null,
          channel_name: cv.channel_name || null,
          conv_status: cv.status,
          // Per-conversation AI gate (plano 17) — drives the "IA OFF" badge and the
          // right-click toggle. WS patches keep this in sync as `conv_ai_active`.
          conv_ai_active: cv.ai_active,
          assignee_user_id: cv.assignee_user_id,
          active_agent_key: cv.active_agent_key,
          // Conversation-scoped custom attributes (plano 05) — kept under a distinct
          // key so they don't collide with the contact's `custom_attributes` (spread
          // from `...c` above). Both feed the cattr: filter dimensions.
          conv_custom_attributes: cv.custom_attributes || {},
          // Etiquetas da CONVERSA (registro próprio, separado das tags do contato em
          // `...c.tags`) — alimentam a dimensão de filtro `conv_label`.
          conv_labels: cv.labels || [],
          // Preview + não-lidas vêm da CONVERSA (sobrescrevem os agregados do contato).
          last_message: (cv.last_message != null && cv.last_message !== '') ? cv.last_message : c.last_message,
          last_message_role: cv.last_message_role || c.last_message_role,
          last_message_ts: cv.last_message_ts || c.last_message_ts,
          last_message_status: cv.last_message_status || c.last_message_status,
          last_message_msg_id: cv.last_message_msg_id || c.last_message_msg_id,
          unread_count: cv.unread_count != null ? cv.unread_count : c.unread_count,
          has_unread_mention: cv.has_unread_mention != null ? cv.has_unread_mention : c.has_unread_mention,
        });
      }
    }
  }
  return rows;
}

/**
 * Shape a /api/conversations/{id}/messages payload into the same object the chat
 * already consumes from getContact (full contact + messages), plus channel_id.
 * @param {Record<string, any>} d
 * @returns {Record<string, any>}
 */
export function shapeConvData(d) {
  return {
    ...(d.contact || {}),
    messages: d.messages || [],
    avatar_v: d.avatar_v,
    channel_id: d.channel_id || 'default',
    conversation: d.conversation || null,
    // Compositor hints (Frente C): template capability + 24h session window.
    templates_supported: !!d.templates_supported,
    session_open: d.session_open,
  };
}
