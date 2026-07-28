// Re-render hook for the provider catalog (plano 76 · H1). Any component that
// shows a provider's label/color/contact-type reads the catalog synchronously
// (fallback until the fetch lands) and subscribes here so it re-renders when the
// descriptors arrive async — same molde as the plugin registry's <Slot>.
//
// Kept out of providerCatalog.js on purpose: that module is PURE (no preact) so
// it stays importable in `node --test`. This hook is the preact-facing seam.
import { useState, useEffect } from 'preact/hooks';
import { subscribe } from '../services/providerCatalog.js';

export function useProviderCatalog() {
  const [, force] = useState(0);
  useEffect(() => subscribe(() => force((n) => n + 1)), []);
}
