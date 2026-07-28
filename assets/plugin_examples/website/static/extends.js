// Frontend extension for the "website" channel plugin. The app imports this file
// ONCE at boot and calls register(api). Everything is additive against the shared
// client registry — disabling the plugin removes it on the next boot and the core
// renders identically.
//
// Its only job: shorten the contact subtitle for widget visitors. A website
// contact is keyed by an opaque session token (`wsess_…`, minted in sessions.py)
// which doubles as the contact's `phone`, so the chat header / info panel would
// otherwise show that long token under the friendly "Site - Contato N" name.
// We map it to a short, stable code (WEB-XXXXXX) via the generic core seam
// `filter.contact.headerSubtitle`. Display ONLY — the token stays the routing
// identity underneath (it authenticates the visitor's WebSocket/API session).

const PREFIX = 'wsess_';

// Deterministic short code from a session token: the first 6 alphanumeric chars
// of the token body, uppercased, behind a `WEB-` prefix. Stable across a
// visitor's messages (same token ⇒ same code). Returns null for anything that
// isn't a website session token, so the subtitle falls back to the raw phone.
export function shortCode(phone) {
  if (typeof phone !== 'string' || !phone.startsWith(PREFIX)) return null;
  const body = phone.slice(PREFIX.length).replace(/[^A-Za-z0-9]/g, '');
  if (!body) return null;
  return 'WEB-' + body.slice(0, 6).toUpperCase();
}

export default function register(api) {
  api.addFilter('filter.contact.headerSubtitle', (ctx, value) => {
    const code = shortCode((ctx && ctx.phone) || value);
    return code || value; // non-website contacts pass through unchanged
  });
}
