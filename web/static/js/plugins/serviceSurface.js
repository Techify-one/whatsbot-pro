// Pure policy for the versioned `api.services` surface.

export const LEGACY_V1_SERVICE_NAMES = Object.freeze([
  'getConversationLabelsBatch',
  'getGowaAlertSettings',
]);

/**
 * Add the narrow 1.x compatibility adapters to an already-curated current
 * surface. The caller owns the HTTP transport so this module stays browser- and
 * Node-compatible.
 *
 * @param {Record<string, any>} currentServices
 * @param {string} servicesVersion - concrete negotiated host version (1.0/2.0)
 * @param {{get:Function, post:Function}|null} legacyHttp
 */
export function buildVersionedServiceSurface(
  currentServices,
  servicesVersion,
  legacyHttp = null,
) {
  const out = { ...(currentServices || {}) };
  if (String(servicesVersion || '').split('.')[0] !== '1') return out;
  if (!legacyHttp) {
    throw new TypeError('legacyHttp is required for plugin services 1.x');
  }
  out.getGowaAlertSettings = () => (
    legacyHttp.get('/api/plugins/gowa/alert-settings')
  );
  out.getConversationLabelsBatch = (ids) => (
    legacyHttp.post('/api/atendimentos/labels-batch', { ids })
  );
  return out;
}
