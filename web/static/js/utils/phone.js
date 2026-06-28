// @ts-check
//
// Canonical phone-number display formatter for the WhatsBot frontend.
//
// Consolidates ~8 copies of a phone formatter that lived inline across
// components (Plano 23 · R1). There were TWO divergent families:
//
//   Family A — `formatPhoneDisplay` (Contacts.js, ContactsListScreen.js,
//     NewConversationModal.js, ContactList.js):
//       if (length < 12) return raw; else
//       `+CC (AA) XXXXX-XXX`  (naive 5-digit slice, assumes a 9-digit subscriber)
//     Quirk: returned the RAW phone (no leading `+`) for short numbers, and
//     mis-split 12-char BR landlines/8-digit cells (dash landed one digit off).
//
//   Family B — `formatPhone` (QRCode.js, StatusBar.js):
//       BR-aware: 13 chars starting "55" → `+55 (AA) XXXXX-XXXX` (9-digit),
//                 12 chars starting "55" → `+55 (AA) XXXX-XXXX`  (8-digit),
//                 else → `+${phone}`.
//     ChannelPickerModal.js had a THIRD, trivial variant (`+` prefix only).
//
// RESOLUTION: Family B is the most correct/complete — it special-cases both BR
// cell formats (9 and 8 digit) AND non-BR numbers, instead of blindly slicing.
// The canonical `formatPhoneDisplay` below is Family B's logic, generalised so
// the Family A call sites (which fed ≥12-char numbers and expected the
// `+CC (AA) ...` shape) keep producing a sensible grouped string for any
// 12/13-char number, BR or not. `formatPhone` is kept as an alias for the
// Family B import name so existing `import { formatPhone }` paths still resolve
// through the QRCode/StatusBar re-export shims.

/**
 * Format a bare phone string (digits only, no `+`) for display.
 *
 * @param {string | null | undefined} phone - E.164-ish digits, e.g. "5511999998888".
 * @returns {string} A grouped, human-readable phone (e.g. "+55 (11) 99999-8888"),
 *   or "" when `phone` is empty/nullish.
 */
export function formatPhoneDisplay(phone) {
  if (!phone) return '';
  const p = String(phone);

  // Brazilian mobile: 55 + 2-digit DDD + 9-digit subscriber → +55 (AA) XXXXX-XXXX
  if (p.length === 13 && p.startsWith('55')) {
    return `+${p.slice(0, 2)} (${p.slice(2, 4)}) ${p.slice(4, 9)}-${p.slice(9)}`;
  }
  // Brazilian landline / legacy cell: 55 + 2-digit DDD + 8-digit subscriber
  //   → +55 (AA) XXXX-XXXX
  if (p.length === 12 && p.startsWith('55')) {
    return `+${p.slice(0, 2)} (${p.slice(2, 4)}) ${p.slice(4, 8)}-${p.slice(8)}`;
  }
  // Generic ≥12-digit international number: country (2) + area (2) + rest, split
  // 5-then-remainder. Mirrors the legacy Family-A `formatPhoneDisplay` shape so
  // those call sites keep their grouped look for non-BR numbers.
  if (p.length >= 12) {
    return `+${p.slice(0, 2)} (${p.slice(2, 4)}) ${p.slice(4, 9)}-${p.slice(9)}`;
  }
  // Short / unknown shape: just prefix with `+` (Family B behaviour).
  return `+${p}`;
}

/**
 * Alias for {@link formatPhoneDisplay} — preserves the `formatPhone` import name
 * used by QRCode.js / StatusBar.js (Family B).
 *
 * @param {string | null | undefined} phone
 * @returns {string}
 */
export const formatPhone = formatPhoneDisplay;
