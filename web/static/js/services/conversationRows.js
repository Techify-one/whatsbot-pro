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
  if (tab === 'mentions') return !!c.has_user_mention;  // fui mencionado numa nota privada
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
  if (dim === 'contact_type') return (c.contact_type || 'outros') === value; // tipo do CONTATO (canal de origem)
  if (dim === 'tag') return (c.tags || []).includes(value);           // etiqueta do CONTATO
  if (dim === 'conv_label') return (c.conv_labels || []).includes(value); // etiqueta do ATENDIMENTO
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
  if (dim === 'channel' || dim === 'contact_type' || dim === 'tag' || dim === 'conv_label' || dim === 'agent') {
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
  if (dim === 'ai') {
    // IA ligada/desligada por conversa (mesmo sinal do badge "IA OFF"). value ∈ 'on'|'off'.
    const isOn = c.conv_ai_active !== 0 && c.conv_ai_active !== false;
    const hit = value === 'on' ? isOn : !isOn;
    return op === 'ne' ? !hit : hit;
  }
  if (dim === 'starter') {
    // Quem iniciou a conversa (plano 28: coluna `origin`). value ∈ 'customer'|'operator'.
    // 'inbound' = cliente mandou a 1ª mensagem; outbound/manual/imported/NULL = atendente.
    const byCustomer = c.origin === 'inbound';
    const hit = value === 'customer' ? byCustomer : !byCustomer;
    return op === 'ne' ? !hit : hit;
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

// ── Ordenação em duas dimensões (leitura × recência) ────────────────────────────
// A sidebar combina DOIS filtros independentes de ordenação, mas persiste um único
// token `sortBy` (compat com filtros salvos + deep-link de URL — que sempre foram um
// só campo). `splitSort` decodifica o token nas duas dimensões e `combineSort` volta:
//   • leitura  (`read`): 'none' (não ordena por leitura) | 'unread' (não lidas
//     primeiro / decrescente) | 'read' (lidas primeiro / crescente)
//   • recência (`time`): 'recent' (recentes primeiro) | 'oldest' (antigos primeiro)
// Combinadas, a leitura é a chave PRIMÁRIA e a recência é o desempate. Os 4 tokens
// legados ('activity'/'oldest'/'unread'/'read') continuam válidos; os combinados
// 'unread_oldest'/'read_oldest' cobrem as combinações novas.
const _SORT_SPLIT = {
  activity: { read: 'none', time: 'recent' },
  oldest: { read: 'none', time: 'oldest' },
  unread: { read: 'unread', time: 'recent' },
  read: { read: 'read', time: 'recent' },
  unread_oldest: { read: 'unread', time: 'oldest' },
  read_oldest: { read: 'read', time: 'oldest' },
};

/**
 * Decodifica o token `sortBy` nas duas dimensões {read, time}. Token desconhecido
 * cai no default ('activity' → não ordena por leitura, recentes primeiro).
 * @param {string} sortBy
 * @returns {{ read: 'none'|'unread'|'read', time: 'recent'|'oldest' }}
 */
export function splitSort(sortBy) {
  return _SORT_SPLIT[sortBy] || _SORT_SPLIT.activity;
}

/**
 * Recompõe o token `sortBy` a partir das duas dimensões.
 * @param {'none'|'unread'|'read'} read
 * @param {'recent'|'oldest'} time
 * @returns {string}
 */
export function combineSort(read, time) {
  const oldest = time === 'oldest';
  if (read === 'unread') return oldest ? 'unread_oldest' : 'unread';
  if (read === 'read') return oldest ? 'read_oldest' : 'read';
  return oldest ? 'oldest' : 'activity';   // read === 'none'
}

/**
 * Sort a row list by the chosen key (leitura × recência combinadas). A leitura é a
 * chave primária (unread = mais não lidas no topo; read = lidas no topo) e a
 * recência é o desempate (recent = mais novas primeiro; oldest = mais antigas).
 * Fixadas (`is_pinned`) só vão ao topo no default puro ('activity') — qualquer
 * reordenação explícita por leitura/antiguidade ignora o pin (senão o pin
 * "vazaria" pra uma ordem onde não faz sentido). Returns a NEW array.
 * @param {Record<string, any>[]} list
 * @param {string} sortBy - 'activity'|'oldest'|'unread'|'read'|'unread_oldest'|'read_oldest'
 * @returns {Record<string, any>[]}
 */
export function sortContactsBy(list, sortBy) {
  const arr = [...list];
  const ts = (c) => c.last_message_ts || c.updated_at || 0;
  const unread = (c) => (c.unread_count || 0) + (c.unread_ai_count || 0);
  const { read, time } = splitSort(sortBy);
  const timeCmp = (a, b) => (time === 'oldest' ? ts(a) - ts(b) : ts(b) - ts(a));
  const pristine = read === 'none' && time === 'recent';   // == 'activity'
  arr.sort((a, b) => {
    if (pristine) {   // pinned first, then most recent (matches the backend default)
      const ap = a.is_pinned ? 1 : 0, bp = b.is_pinned ? 1 : 0;
      if (ap !== bp) return bp - ap;
    }
    if (read !== 'none') {   // primary key: read status
      const au = unread(a), bu = unread(b);
      if (au !== bu) return read === 'unread' ? bu - au : au - bu;
    }
    return timeCmp(a, b);   // tiebreak / sole key: recency
  });
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

/**
 * Count the distinct channels (by `channel_provider`) present across a row list.
 * Drives the sidebar's per-row channel badge gate: with a single channel the badge
 * is noise, so it stays hidden until ≥2 distinct providers exist.
 *
 * PURE and view-independent by design — the caller latches the MAX count ever seen
 * so a narrowing search (which can collapse the visible set to one provider) never
 * makes the badge disappear list-wide (plano 56). Rows without a provider (legacy
 * phone-only rows) are ignored.
 * @param {Array<{ channel_provider?: string|null }>} rows
 * @returns {number}
 */
export function distinctChannelCount(rows) {
  const seen = new Set();
  for (const c of (rows || [])) if (c.channel_provider) seen.add(c.channel_provider);
  return seen.size;
}

// ── Atendimento-cêntrico (plano 11 D1) ──────────────────────────────
// Cada linha da sidebar é um ATENDIMENTO (uma por canal), não um contato. Um número
// presente em 2 canais vira 2 linhas distintas — em vez de fundir tudo numa só.
// Construímos as linhas cruzando os contatos (riqueza: tags/avatar/IA/nome) com as
// atendimentos (canal + preview e não-lidas POR ATENDIMENTO). A identidade da linha é a
// `conversation_id`; contatos sem atendimento ainda aparecem como linha única (phone).

/**
 * Cross the contact list with the conversation list into sidebar rows: one row
 * per conversation (per channel), plus a single legacy phone row for contacts
 * with no conversation yet. Per-conversation preview/unread override the
 * contact-level aggregates.
 *
 * Plano 54 — arquivo por CONVERSA. `conversations` já vem filtrado pela view atual
 * (aberta OU arquivada, via `listConversations({archived})`); cada linha carrega o
 * `is_archived` do ATENDIMENTO (não mais do contato). Dois ajustes fecham os buracos
 * do modelo contact-driven quando o arquivo migra pra conversa:
 *   • `opts.archivedView` — na aba Arquivadas NÃO se emite linha-fantasma "Novo
 *     atendimento" (ela só faz sentido na caixa de entrada).
 *   • `c.conversation_id` (id do atendimento MAIS RECENTE do contato, QUALQUER estado —
 *     `list_contacts` o expõe assim; é o MAX(id) sem filtro de arquivo) — um contato
 *     cujo ÚNICO atendimento está arquivado tem `conversation_id != null` e some da view
 *     aberta sem virar linha-fantasma. A linha legada só nasce pra contato SEM nenhum
 *     atendimento (`conversation_id == null`).
 * @param {Record<string, any>[]} contacts
 * @param {Record<string, any>[]} conversations
 * @param {{ archivedView?: boolean }} [opts]
 * @returns {Record<string, any>[]}
 */
export function buildRows(contacts, conversations, opts = {}) {
  const archivedView = !!(opts && opts.archivedView);
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
      // Sem atendimento NESTA view. Na aba Arquivadas, nada a mostrar. Na caixa de
      // entrada, só vira linha legada "Novo atendimento" se o contato não tem NENHUM
      // atendimento (conversation_id == null); ter um atendimento arquivado
      // (conversation_id != null) apenas o esconde da caixa — sem linha-fantasma.
      // `c.conversation_id` aqui = o atendimento mais recente do CONTATO vindo de
      // `list_contacts` (só é lido neste ramo, antes de a linha sobrescrevê-lo).
      if (archivedView) continue;
      if (c.conversation_id != null) continue;
      rows.push({
        ...c, contact_id: c.id, conversation_id: null,
        channel_id: 'default', channel_provider: null, channel_name: null,
        conv_custom_attributes: {}, conv_labels: [], is_archived: 0, is_pinned: 0,
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
          // Arquivo E fixação por CONVERSA (plano 54): a linha herda is_archived/is_pinned
          // do ATENDIMENTO, não do contato. Alimentam o menu (Arquivar/Fixar), o gate de
          // view (arquivadas) e a ordenação (fixadas ao topo).
          is_archived: cv.is_archived ? 1 : 0,
          is_pinned: cv.is_pinned ? 1 : 0,
          // Per-conversation AI gate (plano 17) — drives the "IA OFF" badge and the
          // right-click toggle. WS patches keep this in sync as `conv_ai_active`.
          conv_ai_active: cv.ai_active,
          assignee_user_id: cv.assignee_user_id,
          active_agent_key: cv.active_agent_key,
          // plano 28: provenance drives the sidebar visibility gate (an 'inbound'
          // conversation shows at t=0 even before its first message is persisted).
          origin: cv.origin,
          // Conversation-scoped custom attributes (plano 05) — kept under a distinct
          // key so they don't collide with the contact's `custom_attributes` (spread
          // from `...c` above). Both feed the cattr: filter dimensions.
          conv_custom_attributes: cv.custom_attributes || {},
          // Etiquetas do ATENDIMENTO (registro próprio, separado das tags do contato em
          // `...c.tags`) — alimentam a dimensão de filtro `conv_label`.
          conv_labels: cv.labels || [],
          // Preview + não-lidas vêm do ATENDIMENTO (sobrescrevem os agregados do contato).
          last_message: (cv.last_message != null && cv.last_message !== '') ? cv.last_message : c.last_message,
          last_message_role: cv.last_message_role || c.last_message_role,
          last_message_ts: cv.last_message_ts || c.last_message_ts,
          last_message_status: cv.last_message_status || c.last_message_status,
          last_message_msg_id: cv.last_message_msg_id || c.last_message_msg_id,
          unread_count: cv.unread_count != null ? cv.unread_count : c.unread_count,
          has_unread_mention: cv.has_unread_mention != null ? cv.has_unread_mention : c.has_unread_mention,
          // Menção INTERNA (nota privada) direcionada ao usuário logado — badge "@"
          // + aba Menções. Vem por-conversa do backend (has_user_mention).
          has_user_mention: !!cv.has_user_mention,
        });
      }
    }
  }
  return rows;
}

/**
 * Shape a /api/atendimentos/{id}/messages payload into the same object the chat
 * already consumes from getContact (full contact + messages), plus channel_id.
 * @param {Record<string, any>} d
 * @returns {Record<string, any>}
 */
export function shapeConvData(d) {
  return {
    ...(d.contact || {}),
    messages: d.messages || [],
    // plano 50 F4: keyset — ainda há mensagens mais antigas p/ carregar (scroll-up).
    has_more: !!d.has_more,
    avatar_v: d.avatar_v,
    channel_id: d.channel_id || 'default',
    conversation: d.conversation || null,
    // Compositor hints (Frente C): template capability + 24h session window.
    templates_supported: !!d.templates_supported,
    session_open: d.session_open,
    // Message context-menu capability hints: hide "Apagar" where the channel can't
    // revoke (Cloud), show "Editar" only where it can edit. Preserved as-is (a real
    // `false` from the backend must survive so the gate can distinguish it from
    // "unknown/legacy" → shows).
    revoke_supported: d.revoke_supported,
    edit_supported: d.edit_supported,
  };
}

// ── Event-Carried State Transfer: conversation_upsert (plano 28) ─────
// The backend pushes the whole enriched conversation row (same shape as a
// /api/atendimentos item) after a commit; the sidebar applies it as an idempotent
// upsert by `conversation_id` — no notification-then-refetch race. `buildRows`
// (REST fetch) and this mapper produce the SAME row shape; a unit test pins that.

/**
 * Map a `conversation_upsert` payload (an enriched conversation row) into the
 * sidebar row shape `buildRows` produces. Field-by-field on purpose: the enriched
 * row's `id` is the CONVERSATION id (→ `conversation_id`) while the sidebar row's
 * `id` is the CONTACT id — conflating them would corrupt row identity.
 * @param {Record<string, any>} p - the enriched conversation row (WS payload).
 * @returns {Record<string, any>}
 */
export function convRowToSidebarRow(p) {
  return {
    id: p.contact_id,                 // row identity for contact-level ops
    contact_id: p.contact_id,
    conversation_id: p.id,            // enriched row `id` = conversation id
    name: p.contact_name,
    phone: p.contact_phone,
    is_group: p.contact_is_group,
    // Tipo do contato (plano tipos-de-contato) — vem no row enriquecido, então a
    // linha empurrada por WS já é filtrável por tipo (não fica como 'outros' até o
    // reconcile). Fallback 'outros' se um payload antigo não trouxer.
    contact_type: p.contact_type || 'outros',
    channel_id: p.channel_id || 'default',
    channel_provider: p.channel_provider || null,
    channel_name: p.channel_name || null,
    conv_status: p.status,
    conv_ai_active: p.ai_active,
    assignee_user_id: p.assignee_user_id,
    active_agent_key: p.active_agent_key,
    conv_custom_attributes: p.custom_attributes || {},
    conv_labels: p.labels || [],
    last_message: p.last_message,
    last_message_role: p.last_message_role,
    last_message_ts: p.last_message_ts,
    last_message_status: p.last_message_status,
    last_message_msg_id: p.last_message_msg_id,
    unread_count: p.unread_count,
    unread_ai_count: p.unread_ai_count,
    has_unread_mention: p.has_unread_mention,
    is_pinned: p.is_pinned,
    is_archived: p.is_archived,
    origin: p.origin,
    // Sort key: a t=0 row has last_message_ts=0, so fall back to last_activity_at
    // (touched = now) via updated_at → the brand-new conversation sorts to the top.
    updated_at: p.last_activity_at,
    // Contato (plano 50 F8): o row enriquecido agora carrega tags + atributos + avatar
    // do contato, então a linha nasce COMPLETA (filtros por `tag`/`cattr:contact:*` +
    // foto funcionam sem um fetch de contatos à parte). Fallbacks p/ payloads antigos.
    tags: p.contact_tags || [],
    custom_attributes: p.contact_custom_attributes || {},
    avatar_v: p.avatar_v,
  };
}

// Fields a MERGE (row already present) is allowed to overwrite — message/preview +
// unread + activity only. Status/assignee/AI/labels/pin/archive stay owned by their
// dedicated conversation_* events (scoped merge — plano 28 D4), so a stale upsert
// snapshot can never revert an assign/resolve/AI toggle.
const UPSERT_MSG_FIELDS = [
  'last_message', 'last_message_role', 'last_message_ts', 'last_message_status',
  'last_message_msg_id', 'unread_count', 'unread_ai_count', 'has_unread_mention',
];

/**
 * Apply a `conversation_upsert` row to the sidebar list (idempotent by
 * `conversation_id`). Absent → INSERT (replacing any legacy phone-only row of the
 * same contact, mirroring `buildRows`). Present → scoped MERGE of message/preview/
 * unread fields, guarded so an older snapshot never regresses the preview. Always a
 * NEW sorted array.
 * @param {Record<string, any>[]} prev - current sidebar rows.
 * @param {Record<string, any>} incoming - a `convRowToSidebarRow(...)` result.
 * @returns {Record<string, any>[]}
 */
export function upsertConversationRow(prev, incoming) {
  if (!Array.isArray(prev) || incoming == null || incoming.conversation_id == null) return prev;
  const idx = prev.findIndex(r => r.conversation_id != null
    && r.conversation_id === incoming.conversation_id);

  if (idx === -1) {
    // INSERT. If a legacy phone-only row (no conversation) exists for this contact,
    // REPLACE it — `buildRows` never emits both a legacy and a conversation row.
    const legacyIdx = prev.findIndex(r => r.conversation_id == null
      && r.phone === incoming.phone);
    const next = [...prev];
    if (legacyIdx !== -1) next[legacyIdx] = incoming;
    else next.push(incoming);
    return sortContacts(next);
  }

  const existing = prev[idx];
  const incomingTs = incoming.last_message_ts || 0;
  const existingTs = existing.last_message_ts || 0;
  // A newer (or first-ever) snapshot may write the message/preview/unread fields; an
  // older one (e.g. the t=0 seed with ts=0 arriving after the batch's real preview)
  // must not regress them.
  const patch = {};
  if (existingTs === 0 || incomingTs >= existingTs) {
    for (const k of UPSERT_MSG_FIELDS) {
      if (incoming[k] !== undefined) patch[k] = incoming[k];
    }
    // Rajada de mensagens (envio rápido / concorrente): vários `conversation_upsert`
    // saem com o MESMO `last_message_ts` (todos veem a última mensagem) mas com
    // `unread_count` fora de ordem (ex.: 3,4,4,5) porque cada emit lê o subquery num
    // instante diferente. Como o "último a chegar" venceria com um valor defasado, o
    // badge travava abaixo do real. Quando o ts NÃO avança (mesmo instante), a
    // não-lida só CRESCE até ser lida (a leitura zera por outro evento: messages_read
    // / abrir a conversa) — então pega o MAX para convergir na contagem verdadeira.
    if (incomingTs === existingTs) {
      if (incoming.unread_count !== undefined)
        patch.unread_count = Math.max(existing.unread_count || 0, incoming.unread_count || 0);
      if (incoming.unread_ai_count !== undefined)
        patch.unread_ai_count = Math.max(existing.unread_ai_count || 0, incoming.unread_ai_count || 0);
    }
  }
  // Activity/sort bump is always forward-only.
  patch.updated_at = Math.max(existing.updated_at || 0, incoming.updated_at || 0);
  const next = [...prev];
  next[idx] = { ...existing, ...patch };
  return sortContacts(next);
}
