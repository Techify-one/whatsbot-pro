import { useEffect, useState } from 'preact/hooks';
import { applyFilter } from '../../../plugins/registry.js';

// The line under the contact name (chat header + contact info panel). For most
// channels this is the raw phone; a plugin may rewrite it via the generic
// `filter.contact.headerSubtitle` seam — e.g. the website widget shows a short,
// stable visitor code (WEB-XXXXXX) instead of the opaque session token
// (`wsess_…`) that doubles as the contact's phone. Display only: the phone stays
// the routing identity underneath.
//
// The client filter bus is async, so we resolve the override into state on
// phone/channel change and fall back to the raw phone until (and unless) a
// plugin overrides it. `setOverride(null)` on change avoids flashing a stale
// code from the previously-open contact.
export function useContactSubtitle(phone, ctx = {}) {
  const [override, setOverride] = useState(null);
  const channelId = ctx.channelId || null;
  useEffect(() => {
    let alive = true;
    setOverride(null);
    applyFilter('filter.contact.headerSubtitle', phone, { ...ctx, phone })
      .then((v) => {
        if (alive && typeof v === 'string' && v && v !== phone) setOverride(v);
      })
      .catch(() => { /* fail-open: keep the raw phone */ });
    return () => { alive = false; };
    // ctx is a fresh literal each render; phone+channelId are the real inputs.
  }, [phone, channelId]);
  return override || phone;
}
